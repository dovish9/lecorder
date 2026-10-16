from __future__ import annotations
from .ai_settings import using, defaults
from .execution_metrics import collect, current_trace
from .ai_providers import require_llm

import json
import queue
import shutil
import subprocess
from .services import start_thread
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import requests

from .config import DB_FILE
from .engine import Transcriber
from .files import OutputFiles
from .markdown import render_note
from .records import Recording
from .state import JobState
from .store import LectureStore
from .notes import NoteLibrary
from .review_jobs import ReviewQueue
from .transcription import CancellationToken, Options, TranscriptionCancelled


WORK_ROOT = DB_FILE.parent / "recordings"


from .recording_intake import RecordingIntake
from .recording_output import RecordingOutput

class RecordingPipeline(RecordingIntake, RecordingOutput):
    """Crash-tolerant recording intake and a single-consumer transcription queue."""

    def __init__(self, store: LectureStore, state: JobState, engine: Transcriber,
                 work_root: Path = WORK_ROOT, start_worker: bool = True) -> None:
        self.store = store
        self.state = state
        self.engine = engine
        self.work_root = work_root
        self.work_root.mkdir(parents=True, exist_ok=True)
        self._queue = queue.PriorityQueue()
        self._queued: set[str] = set()
        self._cancellations: dict[str, CancellationToken] = {}
        self._cancel_stages: dict[str, str] = {}
        self._lock = threading.RLock()
        self.notes = NoteLibrary(store, self.work_root.parent / "notes", False)
        self.reviews = ReviewQueue(store, engine, self._lock, False)
        self._threads = []
        self._stopping = threading.Event()
        if start_worker:
            self.start()

    def start(self):
        if self._threads or self._stopping.is_set():
            return
        from .publication import recover
        recover(self.store)
        self.cleanup_orphans()
        self.notes.start()
        self.reviews.start()
        self.store.recover_interrupted()
        self._worker = start_thread(self._run, "lecture-transcription")
        self._threads.append(self._worker)
        for recording in self.store.queued_recordings():
            self.enqueue(recording.id)

    def prepare_shutdown(self):
        self._stopping.set()
        self.notes.prepare_shutdown()
        self.reviews.prepare_shutdown()

    def close(self):
        self._stopping.set()
        if getattr(self, 'live', None):
            self.live.close()
        with self._lock:
            for token in list(self._cancellations.values()):
                token.cancel()
        self.notes.close()
        self.reviews.close()
        for thread in self._threads:
            if thread is not threading.current_thread():
                thread.join(timeout=10)

    @contextmanager
    def output_transaction(self):
        with self._lock:
            yield


    def enqueue(self, recording_id: str, priority=1) -> None:
        with self._lock:
            if self._stopping.is_set() or recording_id in self._queued:
                return
            try:
                require_llm((json.loads(self.store.get_recording(recording_id).ai_settings_json) or defaults())["llm"])
            except RuntimeError as error:
                self.store.update_recording(recording_id, status="failed", error=str(error))
                self.state.set("error", str(error), job_id=recording_id)
                return
            self._cancellations.setdefault(recording_id, CancellationToken())
            self._queued.add(recording_id)
            self._queue.put((priority, time.monotonic_ns(), recording_id))

    def cancel(self, recording_id: str) -> Recording:
        with self._lock:
            recording = self.store.get_recording(recording_id)
            if recording.status not in {"queued", "processing", "cancelling"}:
                raise ValueError("대기 중이거나 처리 중인 전사 작업만 중단할 수 있습니다.")
            original_status = recording.status
            current_state = self.state.get()
            stage = (
                str(current_state.get("phase") or original_status)
                if current_state.get("job_id") == recording_id else original_status
            )
            self._cancel_stages[recording_id] = stage
            token = self._cancellations.setdefault(recording_id, CancellationToken())
            token.cancel()
            if recording.status != "cancelling":
                recording = self.store.update_recording(
                    recording_id, status="cancelling", error="중단 요청을 처리하고 있습니다."
                )
            was_queued = original_status == "queued"
        self.state.set(
            "cancelling", f"{recording.title} 전사 작업 중단 중",
            job_id=recording.id, title=recording.title, course_id=recording.course_id,
        )
        if was_queued:
            return self._complete_cancellation(recording_id, None, "queued")
        return recording


    def _run(self) -> None:
        while not self._stopping.is_set():
            try:
                _, _, recording_id = self._queue.get(timeout=.25)
            except queue.Empty:
                continue
            try:
                try:
                    self.process(recording_id)
                except Exception as error:
                    # A single corrupt queue entry must never terminate the worker permanently.
                    self.state.set(
                        "error", f"대기열 작업을 불러오지 못했습니다: {error}",
                        job_id=recording_id,
                    )
            finally:
                with self._lock:
                    deferred = self._finish_queue_entry(recording_id)
                self._queue.task_done()
                if deferred:
                    time.sleep(1)

    def process_next(self) -> None:
        _, _, recording_id = self._queue.get_nowait()
        try:
            self.process(recording_id)
        finally:
            with self._lock:
                self._finish_queue_entry(recording_id)
            self._queue.task_done()

    def _finish_queue_entry(self, recording_id: str) -> bool:
        try:
            if not self._stopping.is_set() and self.store.get_recording(recording_id).status == "queued":
                self._queue.put((1, time.monotonic_ns(), recording_id))
                return True
        except KeyError:
            pass
        self._queued.discard(recording_id)
        self._cancellations.pop(recording_id, None)
        self._cancel_stages.pop(recording_id, None)
        return False

    def process(self, recording_id):
        try:
            row = self.store.get_recording(recording_id)
        except KeyError:
            return
        from .request_history import execution
        with using(json.loads(row.ai_settings_json) or defaults()), collect(), execution(recording_id, 'transcription') as finish:
            result = self._process(recording_id)
            if not self._stopping.is_set():
                latest = self.store.get_recording(recording_id)
                if latest.status != 'queued':
                    finish(latest.status)
            return result

    def _process(self, recording_id: str) -> None:
        with self._lock:
            try:
                recording = self.store.get_recording(recording_id)
            except KeyError:
                # A queued item may have been cancelled and its history deleted
                # before the single consumer reaches it.
                return
            if recording.status in {"cancelled", "cancelling"}:
                return
            if recording.status != "queued":
                raise ValueError("대기 중인 작업만 처리할 수 있습니다.")
            try:
                require_llm((json.loads(self.store.get_recording(recording_id).ai_settings_json) or defaults())["llm"])
            except RuntimeError as error:
                self.store.update_recording(recording_id, status="failed", error=str(error))
                return
            recording = self.store.update_recording(recording_id, llm_enabled=True, format_transcript=True)
            if recording.note_revision_id:
                # Check even legacy queued uploads that predate the dependency marker.
                # Do not acquire the speech inference slot while notes are unfinished.
                with self.notes.access():
                    revision = self.notes.revision(recording.note_revision_id)
                    waiting = json.loads(recording.note_snapshot_json).get("waiting_for_analysis")
                    if self._note_analysis_pending(revision):
                        if not waiting:
                            self._bind_note(recording, recording.note_revision_id)
                        return
                    if waiting:
                        if revision['status'] not in {'ready', 'partial'} or revision['study_status'] != 'completed':
                            self.store.update_recording(recording_id, status="failed",
                                error="선택한 강의노트 분석이 실패하거나 중단되어 전사를 시작하지 않았습니다. 노트를 다시 분석하거나 다른 노트를 선택하세요.")
                            return
                        recording = self._bind_note(recording, recording.note_revision_id)
            if self._stopping.is_set():
                return
            cancellation = self._cancellations.setdefault(recording_id, CancellationToken())
            recording = self.store.update_recording(
                recording_id, status="processing", started_at=self.store.now(), error=""
            )
        source = Path(recording.source_path)
        run_id = uuid.uuid4().hex
        with self.store.transaction() as db:
            db.execute("INSERT INTO transcription_runs(id,recording_id,started_at,status,settings) VALUES (?,?,?,?,?)",
                       (run_id, recording.id, self.store.now(), "processing", json.dumps({
                           "language": recording.language, "note_revision_id": recording.note_revision_id,
                           "note_snapshot": json.loads(recording.note_snapshot_json), "ai_settings": json.loads(recording.ai_settings_json),
                           "model": (json.loads(recording.ai_settings_json) or defaults())["speech"]["model"],
                           "llm_model": recording.llm_model, "format_transcript": recording.format_transcript,
                       }, ensure_ascii=False)))
        started = time.monotonic()
        hidden_audio: Path | None = None
        hidden_note: Path | None = None
        destination_audio: Path | None = None
        destination_note: Path | None = None
        note_backup: Path | None = None
        published_audio = False
        published_note = False

        def rollback_outputs() -> None:
            if published_note and destination_note:
                if note_backup and note_backup.exists():
                    note_backup.replace(destination_note)
                else:
                    destination_note.unlink(missing_ok=True)
            if published_audio and destination_audio:
                destination_audio.unlink(missing_ok=True)

        try:
            cancellation.check()
            if not source.is_file():
                raise FileNotFoundError("처리할 원본 녹음을 찾지 못했습니다.")
            self.state.set("processing", f"{recording.course_name} · {recording.title} 전사 중",
                           job_id=recording.id, title=recording.title, course_id=recording.course_id)
            options = Options(
                language=recording.language, course_name=recording.course_name,
                note_keywords=tuple(json.loads(recording.note_snapshot_json).get('keywords', [])),
                format_text=False, use_llm=False,
                llm_model=recording.llm_model,
                ai_settings=json.loads(recording.ai_settings_json) or defaults(),
            )
            def progress(stage: str, index: int, total: int) -> None:
                cancellation.check()
                self.state.set(
                    "processing" if stage in {"transcribing", "vad_fallback", "repetition_fallback"} else
                    "refining" if stage == "retry" else "polishing",
                    ("반복 감지 · 해당 음성 구간만 안전 설정으로 재전사 중"
                     if stage == "repetition_fallback" else
                     "VAD 음성 누락 확인 · 해당 구간의 원본 음성으로 재전사 중"
                     if stage == "vad_fallback" else
                     f"음성 구간 전사 중 ({index}/{total})" if stage == "transcribing" else
                     f"저신뢰 구간 재확인 중 ({index}/{total})" if stage == "retry" else
                    f"LLM 검수 중 ({index}/{total})"),
                    job_id=recording.id, title=recording.title, course_id=recording.course_id,
                )

            result = self.engine.transcribe(
                source, options, progress=progress, cancellation=cancellation,
            )
            cancellation.check()
            saving_started = time.monotonic()
            self.state.set(
                "saving", f"{recording.title} 결과 파일 정리 중",
                job_id=recording.id, title=recording.title, course_id=recording.course_id,
            )
            files = OutputFiles(Path(recording.output_dir))
            if recording.audio_path or recording.note_path:
                destination_audio = (Path(recording.audio_path) if recording.audio_path else
                                     Path(recording.note_path).with_suffix(recording.extension))
                destination_note = (Path(recording.note_path) if recording.note_path else
                                    destination_audio.with_suffix(".md"))
            else:
                destination_audio, destination_note = files.pair(recording.title, recording.extension)
            if not destination_audio.is_file():
                hidden_audio = files.folder / f".lecture-{recording.id}{recording.extension}"
            hidden_note = files.folder / f".lecture-{recording.id}.md"
            if hidden_audio:
                self._store_playable_audio(recording, source, hidden_audio, cancellation)
            segments = [segment.public() for segment in result.segments]
            breaks = set(result.paragraph_breaks)
            pipeline_details = {
                "live_events": self.live.log(recording.id) if getattr(self, 'live', None) else [],
                "pipeline_version": 2,
                "conversion_seconds": round(result.conversion_seconds, 3),
                "whisper_primary_seconds": round(result.whisper_primary_seconds, 3),
                "vad_fallback_seconds": round(result.vad_fallback_seconds, 3),
                "repetition_fallback_seconds": round(result.repetition_fallback_seconds, 3),
                "retry_processing_seconds": round(result.retry_processing_seconds, 3),
                "llm_seconds": round(result.qwen_seconds, 3),
                "saving_seconds": 0.0,
                "low_confidence_segments": result.low_confidence_segments,
                "retry_attempted_groups": result.retry_attempted_groups,
                "retry_accepted_groups": result.retry_accepted_groups,
                "lowest_avg_logprob": result.lowest_avg_logprob,
                "vad_fallback": result.vad_fallback,
                "repetition_detected": result.repetition_detected,
                "repetition_ratio": round(result.repetition_ratio, 4),
                "segment_count": len(result.segments),
                "llm_batches": result.llm_chunks,
                "llm_failed_batches": result.fallback_chunks,
                "llm_requests": result.qwen_requests,
                "llm_split_retries": result.qwen_split_retries,
                "llm_prompt_tokens": result.qwen_prompt_tokens,
                "llm_output_tokens": result.qwen_output_tokens,
                "suggestion_count": len(result.suggestions),
                "paragraph_break_count": len(result.paragraph_breaks),
                "settings": result.pipeline_settings,
            }
            recording = replace(
                recording, audio_path=str(destination_audio), note_path=str(destination_note),
                duration_seconds=result.duration_seconds or recording.duration_seconds,
                transcript_text=result.text, segments_json=json.dumps(segments, ensure_ascii=False),
                breaks_json=json.dumps(sorted(breaks)),
                quality_json=json.dumps(pipeline_details),
            )
            hidden_note.write_text(render_note(recording, segments, breaks), encoding="utf-8")
            if destination_note.exists():
                note_backup = destination_note.with_name(f".lecture-{recording.id}-previous.md")
                shutil.copy2(destination_note, note_backup)

            def publish() -> None:
                nonlocal published_audio, published_note
                cancellation.check()
                if hidden_audio:
                    hidden_audio.replace(destination_audio)
                    published_audio = True
                hidden_note.replace(destination_note)
                published_note = True

            pipeline_details["saving_seconds"] = round(time.monotonic() - saving_started, 3)
            elapsed = max(0, time.monotonic() - started - current_trace()["waiting_seconds"])+current_trace().get('reused_seconds',0)
            pipeline_details["total_seconds"] = round(elapsed, 3)
            pipeline_details.update(current_trace())
            pipeline_details["stages"] = [
                {"name": "오디오 변환", "seconds": pipeline_details["conversion_seconds"], "detail": "16 kHz · mono WAV"},
                {"name": "음성 전사", "seconds": pipeline_details["whisper_primary_seconds"], "detail": f"{len(result.segments)}개 발화"},
                {"name": "VAD 보호", "seconds": pipeline_details["vad_fallback_seconds"], "detail": "실행" if result.vad_fallback else "실행 안 함"},
                {"name": "반복 보호", "seconds": pipeline_details["repetition_fallback_seconds"], "detail": "실행" if result.repetition_detected else "실행 안 함"},
                {"name": "저신뢰 확인", "seconds": pipeline_details["retry_processing_seconds"], "detail": f"{result.low_confidence_segments}개 구간 · {result.retry_accepted_groups}개 교체"},
                {"name": "파일 저장", "seconds": pipeline_details["saving_seconds"], "detail": "오디오와 Markdown 저장"},
            ]
            if current_trace().get('reused_seconds'):
                pipeline_details['stages'].insert(-1,{'name':'복구된 완료 요청','seconds':current_trace()['reused_seconds'],
                    'detail':f"이전 실행에서 처리한 {current_trace()['reused_requests']}개 요청 · 재호출 없음"})
            review_warning = (
                f"LLM 검수 {result.fallback_chunks}/{result.llm_chunks}구간 실패 · "
                "Whisper 원문과 완료된 검수 결과만 저장됨"
                if result.fallback_chunks else ""
            )
            with self._lock:
                cancellation.check()
                recording = replace(
                    recording, status="completed", source_path="", processing_seconds=elapsed,
                    completed_at=self.store.now(), error=review_warning,
                    quality_json=json.dumps(pipeline_details),
                    review_status="queued" if recording.llm_enabled else "none", review_error="",
                )
                self.store.commit_result(recording, [item.public() for item in result.suggestions], publish,
                                         output_paths=(destination_note, destination_audio if hidden_audio else None))
            shutil.rmtree(self.folder(recording.id), ignore_errors=True)
            if recording.llm_enabled:
                self.reviews.enqueue(recording.id)
            self.state.set(
                "warning" if review_warning else "complete",
                review_warning or f"저장 완료 · 검토할 수정 제안 {len(result.suggestions)}개",
                job_id=recording.id, title=recording.title,
                course_id=recording.course_id,
            )
        except TranscriptionCancelled:
            rollback_outputs()
            if self._stopping.is_set() and recording_id not in self._cancel_stages:
                self.store.update_recording(recording_id, status='queued', error='앱 재시작 후 완료된 구간부터 복구합니다.')
            else:
                phase = self._cancel_stages.get(recording_id, "processing")
                self._complete_cancellation(recording_id, started, phase)
        except subprocess.CalledProcessError as error:
            rollback_outputs()
            detail = error.stderr.decode("utf-8", errors="replace")[-500:] if error.stderr else ""
            self._fail(recording_id, f"오디오 변환 실패: {detail or error}", started)
        except requests.RequestException as error:
            rollback_outputs()
            self._fail(recording_id, f"Whisper 연결 또는 처리 실패: {error}", started)
        except Exception as error:
            rollback_outputs()
            self._fail(recording_id, str(error), started)
        finally:
            try:
                latest = self.store.get_recording(recording_id)
                with self.store.transaction() as db:
                    db.execute("UPDATE transcription_runs SET status=?,completed_at=?,metrics=?,error=? WHERE id=?",
                               (latest.status, self.store.now(), latest.quality_json, latest.error, run_id))
            except Exception as error:
                # An audit write must not skip cleanup or invalidate saved output.
                print(f"전사 실행 기록 저장 실패: {error}")
            if hidden_audio:
                hidden_audio.unlink(missing_ok=True)
            if hidden_note:
                hidden_note.unlink(missing_ok=True)
            if note_backup:
                note_backup.unlink(missing_ok=True)
