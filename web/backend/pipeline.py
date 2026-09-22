from __future__ import annotations

import json
import queue
import re
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

import requests

from .config import DB_FILE
from .engine import Transcriber
from .files import OutputFiles, safe_name
from .output_files import rename_output_files
from .markdown import render_note, transcript_text
from .records import ActiveSettings, Recording
from .state import JobState
from .store import LectureStore
from .notes import NoteLibrary
from .review_jobs import ReviewQueue
from .transcription import CancellationToken, Options, TranscriptionCancelled


WORK_ROOT = DB_FILE.parent / "recordings"


class RecordingPipeline:
    """Crash-tolerant recording intake and a single-consumer transcription queue."""

    def __init__(self, store: LectureStore, state: JobState, engine: Transcriber,
                 work_root: Path = WORK_ROOT, start_worker: bool = True) -> None:
        self.store = store
        self.state = state
        self.engine = engine
        self.work_root = work_root
        self.work_root.mkdir(parents=True, exist_ok=True)
        self._queue: queue.Queue[str] = queue.Queue()
        self._queued: set[str] = set()
        self._cancellations: dict[str, CancellationToken] = {}
        self._cancel_stages: dict[str, str] = {}
        self._lock = threading.RLock()
        self.notes = NoteLibrary(store, self.work_root.parent / "notes", start_worker)
        self.reviews = ReviewQueue(store, engine, self._lock, start_worker)
        self.cleanup_orphans()
        if start_worker:
            self.store.recover_interrupted()
            self._worker = threading.Thread(target=self._run, name="lecture-transcription", daemon=True)
            self._worker.start()
            for recording in self.store.queued_recordings():
                self.enqueue(recording.id)

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex[:12]

    def folder(self, recording_id: str) -> Path:
        return self.work_root / recording_id

    def cleanup_orphans(self) -> None:
        """Remove only work directories that have no corresponding database job."""
        persisted = self.store.recording_ids()
        for item in self.work_root.iterdir():
            if item.is_dir() and item.name not in persisted:
                shutil.rmtree(item, ignore_errors=True)

    @staticmethod
    def _store_playable_audio(
        recording: Recording,
        source: Path,
        destination: Path,
        cancellation: CancellationToken,
    ) -> None:
        """Copy uploads as-is and normalize browser recordings for reliable playback."""
        if recording.source_kind != "browser" or source.suffix.lower() not in {".webm", ".mp4"}:
            shutil.copy2(source, destination)
            return
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            shutil.copy2(source, destination)
            return
        command = [
            ffmpeg, "-y", "-fflags", "+genpts", "-i", str(source),
            "-map", "0:a:0", "-vn", "-af", "aresample=async=1:first_pts=0",
        ]
        if source.suffix.lower() == ".webm":
            command.extend([
                "-c:a", "libopus", "-b:a", "128k", "-vbr", "on",
                "-compression_level", "10", "-frame_duration", "20",
                "-application", "audio", "-ar", "48000",
            ])
        else:
            command.extend([
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                "-movflags", "+faststart",
            ])
        command.append(str(destination))
        try:
            cancellation.run(command, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
            if destination.stat().st_size == 0:
                raise OSError("빈 오디오 파일이 생성됐습니다.")
        except TranscriptionCancelled:
            destination.unlink(missing_ok=True)
            raise
        except (OSError, subprocess.SubprocessError):
            destination.unlink(missing_ok=True)
            shutil.copy2(source, destination)

    def _preserve_failed_audio(self, recording: Recording) -> tuple[str, str]:
        """Copy the original into the user output folder without consuming retry source."""
        existing = Path(recording.audio_path) if recording.audio_path else None
        if existing and existing.is_file():
            return str(existing), ""
        source = Path(recording.source_path)
        if not source.is_file():
            return "", "작업용 원본 파일을 찾지 못했습니다."
        hidden: Path | None = None
        try:
            files = OutputFiles(Path(recording.output_dir))
            destination = files.audio(recording.title, recording.extension)
            hidden = files.folder / f".lecture-{recording.id}-failed{recording.extension}"
            self._store_playable_audio(recording, source, hidden, CancellationToken())
            hidden.replace(destination)
            return str(destination), ""
        except Exception as error:
            return "", str(error)
        finally:
            if hidden:
                hidden.unlink(missing_ok=True)

    def _create_named_recording(self, recording_id: str, settings: ActiveSettings,
                                title: str, source_kind: str, extension: str,
                                source: Path, status: str) -> Recording:
        # Reserve the title in the DB before another intake can choose it.
        with self._lock:
            files = OutputFiles(Path(settings.output_dir))
            title = files.available_title(title, self.store.recording_titles(files.folder))
            return self.store.create_recording(
                recording_id, settings, title, source_kind, extension, str(source), status
            )

    def _preserve_intake(self, recording):
        path, error = self._preserve_failed_audio(recording)
        if not path:
            self.store.update_recording(recording.id, status="failed", error=error)
            raise ValueError(f"원본 보존 실패: {error}")
        return self.store.update_recording(recording.id, audio_path=path)

    def create_upload(self, settings: ActiveSettings, title: str, extension: str,
                      stream: BinaryIO, lecture_note_id: str | None = None) -> Recording:
        revision = self.notes.bind(lecture_note_id, settings.course_id)
        recording_id = self.new_id()
        folder = self.folder(recording_id)
        folder.mkdir(parents=True, exist_ok=False)
        source = folder / f"source{extension}"
        try:
            with source.open("wb") as destination:
                shutil.copyfileobj(stream, destination, length=1024 * 1024)
            if source.stat().st_size == 0:
                raise ValueError("녹음 파일이 비어 있습니다.")
            recording = self._create_named_recording(
                recording_id, settings, title, "upload", extension, source, "queued"
            )
            recording = self._bind_note(recording, revision)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise
        recording = self._preserve_intake(recording)
        self.state.set("queued", f"{recording.title} 작업을 대기열에 추가했습니다.",
                       job_id=recording.id, title=recording.title, course_id=settings.course_id)
        self.enqueue(recording.id)
        return recording

    def begin_chunked(self, settings: ActiveSettings, title: str, extension: str, lecture_note_id: str | None = None) -> Recording:
        revision = self.notes.bind(lecture_note_id, settings.course_id)
        recording_id = self.new_id()
        folder = self.folder(recording_id)
        (folder / "chunks").mkdir(parents=True, exist_ok=False)
        source = folder / f"source{extension}"
        try:
            recording = self._create_named_recording(
                recording_id, settings, title, "browser", extension, source, "recording"
            )
            return self._bind_note(recording, revision)
        except Exception:
            shutil.rmtree(folder, ignore_errors=True)
            raise

    @staticmethod
    def _note_analysis_pending(revision):
        return revision['status'] in {'queued', 'extracting'} or revision['study_status'] in {'pending', 'analyzing'}

    def _bind_note(self, recording, revision):
        with self.notes.lock:
            waiting = revision and self._note_analysis_pending(self.notes._revision(revision))
            snapshot = ({"waiting_for_analysis": True, "keywords": [], "pages": []}
                        if waiting else self.notes.snapshot(revision))
            return self.store.update_recording(recording.id, note_revision_id=revision,
                note_snapshot_json=json.dumps(snapshot, ensure_ascii=False))

    def select_recording_course(self, recording_id: str, course_id: int):
        with self._lock:
            recording = self.store.get_recording(recording_id)
            if recording.status != "recording" or recording.source_kind != "browser":
                raise ValueError("녹음 중인 작업의 강의만 변경할 수 있습니다.")
            course = self.store.get_course(course_id)
            if recording.course_id == course.id:
                return recording
            title = recording.title
            automatic = re.fullmatch(re.escape(safe_name(recording.course_name)) + r"-(\d{1,2}월\d{1,2}일-[월화수목금토일])(?: \(\d+\))?", title)
            if automatic:
                base = safe_name(f"{course.name}-{automatic.group(1)}")
                reserved = self.store.recording_titles(Path(recording.output_dir)) - {recording.title}
                title = OutputFiles(Path(recording.output_dir)).available_title(base, reserved)
            with self.store._connect() as db:
                db.execute("""UPDATE recordings SET course_id=?,course_name=?,title=?,language=?,
                    format_transcript=?,llm_enabled=?,note_revision_id=NULL,note_snapshot_json='{}'
                    WHERE id=?""", (course.id, course.name, title, course.language,
                    int(course.format_transcript), int(course.llm_enabled), recording_id))
            return self.store.get_recording(recording_id)

    def select_recording_note(self, recording_id: str, lecture_note_id: str | None):
        with self._lock:
            recording = self.store.get_recording(recording_id)
            if recording.status not in {"recording", "recoverable"}:
                raise ValueError("녹음 중인 작업의 강의노트만 변경할 수 있습니다.")
            revision = self.notes.bind(lecture_note_id, recording.course_id)
            return self._bind_note(recording, revision)

    def save_chunk(self, recording_id: str, index: int, stream: BinaryIO) -> int:
        recording = self.store.get_recording(recording_id)
        if recording.status not in {"recording", "recoverable"}:
            raise ValueError("더 이상 녹음 조각을 받을 수 없는 작업입니다.")
        if index < 0 or index > 100000:
            raise ValueError("잘못된 녹음 조각 번호입니다.")
        chunks = self.folder(recording_id) / "chunks"
        chunks.mkdir(parents=True, exist_ok=True)
        destination = chunks / f"{index:08d}.part"
        temporary = chunks / f".{index:08d}.upload"
        size = 0
        try:
            with temporary.open("wb") as output:
                while True:
                    block = stream.read(1024 * 1024)
                    if not block:
                        break
                    size += len(block)
                    if size > 32 * 1024 * 1024:
                        raise ValueError("녹음 조각 하나가 32MB를 초과했습니다.")
                    output.write(block)
            if size == 0:
                raise ValueError("빈 녹음 조각입니다.")
            temporary.replace(destination)
            return size
        finally:
            temporary.unlink(missing_ok=True)

    def finalize_chunked(self, recording_id: str, duration_seconds: float = 0.0,
                         allow_partial: bool = False) -> Recording:
        recording = self.store.get_recording(recording_id)
        if recording.status not in {"recording", "recoverable"}:
            raise ValueError("완료할 수 없는 녹음 작업입니다.")
        chunks = sorted((self.folder(recording_id) / "chunks").glob("*.part"))
        if not chunks:
            raise ValueError("저장된 녹음 조각이 없습니다.")
        indices = [int(path.stem) for path in chunks]
        if indices != list(range(indices[-1] + 1)) and not allow_partial:
            raise ValueError("일부 녹음 조각이 누락되어 복구가 필요합니다.")
        if allow_partial:
            contiguous: list[Path] = []
            for expected, chunk in enumerate(chunks):
                if int(chunk.stem) != expected:
                    break
                contiguous.append(chunk)
            chunks = contiguous
            if not chunks:
                raise ValueError("처음부터 이어지는 녹음 조각을 찾지 못했습니다.")
        source = Path(recording.source_path)
        temporary = source.with_suffix(source.suffix + ".assembling")
        try:
            with temporary.open("wb") as output:
                for chunk in chunks:
                    with chunk.open("rb") as item:
                        shutil.copyfileobj(item, output, length=1024 * 1024)
            temporary.replace(source)
        finally:
            temporary.unlink(missing_ok=True)
        recording = self.store.update_recording(
            recording_id, status="queued", duration_seconds=max(0.0, float(duration_seconds)), error=""
        )
        recording = self._preserve_intake(recording)
        self.state.set("queued", f"{recording.title} 녹음을 안전하게 저장하고 대기열에 추가했습니다.",
                       job_id=recording.id, title=recording.title, course_id=recording.course_id)
        self.enqueue(recording_id)
        return recording

    def enqueue(self, recording_id: str) -> None:
        with self._lock:
            if recording_id in self._queued:
                return
            self._cancellations.setdefault(recording_id, CancellationToken())
            self._queued.add(recording_id)
            self._queue.put(recording_id)

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

    def discard(self, recording_id: str) -> None:
        self.store.discard_recording(recording_id)
        shutil.rmtree(self.folder(recording_id), ignore_errors=True)

    def delete_history(self, recording_id: str) -> None:
        with self._lock:
            recording = self.store.get_recording(recording_id)
            if recording.review_status in {"queued", "processing"}:
                raise ValueError("검수가 끝난 후 작업을 삭제하세요.")
            self.store.delete_recording_history(recording_id)
            shutil.rmtree(self.folder(recording_id), ignore_errors=True)

    def retranscribe(self, recording_id: str, lecture_note_id: str | None = None, *, retain_note=True, course_id=None, title=None) -> Recording:
        """Re-run the same job, preserving its output until replacement succeeds."""
        with self._lock:
            previous = self.store.get_recording(recording_id)
            if previous.status not in {"completed", "failed", "cancelled"}:
                raise ValueError("완료·실패·중단된 작업만 다시 전사할 수 있습니다.")
            if previous.review_status in {"queued", "processing"}:
                raise ValueError("검수가 끝난 후 다시 전사하세요.")
            target_course = previous.course_id if course_id is None else course_id
            if course_id is not None:
                if isinstance(course_id, bool) or not isinstance(course_id, int):
                    raise ValueError("강의를 선택하세요.")
                self.store.get_course(course_id)
            if retain_note and target_course == previous.course_id and previous.note_revision_id:
                lecture_note_id = self.notes._revision(previous.note_revision_id)['note_id']
            revision = self.notes.bind(lecture_note_id, target_course, allow_pending=True)
            source = next((Path(value) for value in (previous.source_path, previous.audio_path)
                           if value and Path(value).is_file()), None)
            if source is None:
                raise ValueError("다시 전사할 원본 음성 파일을 찾지 못했습니다.")
            folder = self.folder(recording_id)
            folder.mkdir(parents=True, exist_ok=True)
            working = folder / f"source{previous.extension}"
            try:
                if source.resolve() != working.resolve():
                    shutil.copy2(source, working)
            except OSError as error:
                raise ValueError("원본 음성 파일을 읽거나 작업용 사본을 만들 수 없습니다.") from error
            changes = {}
            rollback = lambda: None
            if title is not None:
                if not isinstance(title, str) or not title.strip():
                    raise ValueError("저장할 파일 이름을 입력해 주세요.")
                title = safe_name(title.strip()[:160])
                if title != previous.title:
                    reserved = self.store.recording_titles(Path(previous.output_dir))
                    if title in reserved:
                        raise ValueError("같은 이름의 작업이 이미 있습니다.")
                    fields, targets = [], []
                    root = Path(previous.output_dir).resolve()
                    for field in ("audio_path", "note_path"):
                        value = getattr(previous, field)
                        if value and Path(value).is_file():
                            path = Path(value).resolve()
                            if path.parent != root:
                                raise ValueError("저장 폴더 밖의 파일은 이름을 수정할 수 없습니다.")
                            fields.append(field)
                            targets.append(path)
                    destinations, rollback = rename_output_files(targets, title)
                    changes.update(zip(fields, map(str, destinations)))
                    changes["title"] = title
            try:
                recording = self.store.queue_retranscription(recording_id, working,
                    course_id=course_id, output_changes=changes)
            except Exception:
                rollback()
                raise
            recording = self._bind_note(recording, revision)
            self._cancellations[recording_id] = CancellationToken()
            self._cancel_stages.pop(recording_id, None)
            self.enqueue(recording_id)
            self.state.set("queued", f"{recording.title} 다시 전사 대기 중",
                           job_id=recording.id, title=recording.title, course_id=recording.course_id)
            return recording

    def retry(self, recording_id: str) -> Recording:
        recording = self.store.get_recording(recording_id)
        if recording.status in {"recording", "recoverable"} and recording.source_kind == "browser":
            return self.finalize_chunked(recording_id, recording.duration_seconds, allow_partial=True)
        if recording.status != "failed":
            raise ValueError("실패한 작업만 다시 처리할 수 있습니다.")
        return self.retranscribe(recording_id)

    def _run(self) -> None:
        while True:
            recording_id = self._queue.get()
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
        recording_id = self._queue.get_nowait()
        try:
            self.process(recording_id)
        finally:
            with self._lock:
                self._finish_queue_entry(recording_id)
            self._queue.task_done()

    def _finish_queue_entry(self, recording_id: str) -> bool:
        try:
            if self.store.get_recording(recording_id).status == "queued":
                self._queue.put(recording_id)
                return True
        except KeyError:
            pass
        self._queued.discard(recording_id)
        self._cancellations.pop(recording_id, None)
        self._cancel_stages.pop(recording_id, None)
        return False

    def process(self, recording_id: str) -> None:
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
            if recording.note_revision_id:
                # Check even legacy queued uploads that predate the dependency marker.
                # Do not acquire the speech inference slot while notes are unfinished.
                with self.notes.lock:
                    revision = self.notes._revision(recording.note_revision_id)
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
            cancellation = self._cancellations.setdefault(recording_id, CancellationToken())
            recording = self.store.update_recording(
                recording_id, status="processing", started_at=self.store.now(), error=""
            )
        source = Path(recording.source_path)
        run_id = uuid.uuid4().hex
        with self.store._connect() as db:
            db.execute("INSERT INTO transcription_runs(id,recording_id,started_at,status,settings) VALUES (?,?,?,?,?)",
                       (run_id, recording.id, self.store.now(), "processing", json.dumps({
                           "language": recording.language, "note_revision_id": recording.note_revision_id,
                           "note_snapshot": json.loads(recording.note_snapshot_json), "model": "whisper/large-v3",
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
                note_keywords=tuple(k['term'] for k in json.loads(recording.note_snapshot_json).get('keywords', []) if k.get('included', True)),
                format_text=False, use_llm=False,
                llm_model=recording.llm_model,
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
                    f"Qwen3 검수 중 ({index}/{total})"),
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
                "pipeline_version": 1,
                "conversion_seconds": round(result.conversion_seconds, 3),
                "whisper_primary_seconds": round(result.whisper_primary_seconds, 3),
                "vad_fallback_seconds": round(result.vad_fallback_seconds, 3),
                "repetition_fallback_seconds": round(result.repetition_fallback_seconds, 3),
                "retry_processing_seconds": round(result.retry_processing_seconds, 3),
                "qwen_seconds": round(result.qwen_seconds, 3),
                "saving_seconds": 0.0,
                "low_confidence_segments": result.low_confidence_segments,
                "retry_attempted_groups": result.retry_attempted_groups,
                "retry_accepted_groups": result.retry_accepted_groups,
                "lowest_avg_logprob": result.lowest_avg_logprob,
                "vad_fallback": result.vad_fallback,
                "repetition_detected": result.repetition_detected,
                "repetition_ratio": round(result.repetition_ratio, 4),
                "segment_count": len(result.segments),
                "qwen_batches": result.llm_chunks,
                "qwen_failed_batches": result.fallback_chunks,
                "qwen_requests": result.qwen_requests,
                "qwen_split_retries": result.qwen_split_retries,
                "qwen_prompt_tokens": result.qwen_prompt_tokens,
                "qwen_output_tokens": result.qwen_output_tokens,
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
            elapsed = time.monotonic() - started
            pipeline_details["total_seconds"] = round(elapsed, 3)
            review_warning = (
                f"Qwen3 검수 {result.fallback_chunks}/{result.llm_chunks}구간 실패 · "
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
                self.store.commit_result(recording, [item.public() for item in result.suggestions], publish)
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
                with self.store._connect() as db:
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

    def _complete_cancellation(self, recording_id: str, started: float | None,
                               stage: str) -> Recording:
        recording = self.store.get_recording(recording_id)
        source = Path(recording.source_path)
        hidden_audio: Path | None = None
        try:
            existing_audio = Path(recording.audio_path) if recording.audio_path else None
            if existing_audio and existing_audio.is_file():
                destination = existing_audio
            elif not source.is_file():
                raise FileNotFoundError("중단된 작업의 원본 음성을 찾지 못했습니다.")
            else:
                files = OutputFiles(Path(recording.output_dir))
                destination = files.audio(recording.title, recording.extension)
                hidden_audio = files.folder / f".lecture-{recording.id}-cancelled{recording.extension}"
                self._store_playable_audio(recording, source, hidden_audio, CancellationToken())
                hidden_audio.replace(destination)
        except Exception as error:
            if hidden_audio:
                hidden_audio.unlink(missing_ok=True)
            self._fail(recording_id, f"작업은 중단됐지만 원본 음성을 보존하지 못했습니다: {error}",
                       started or time.monotonic())
            return self.store.get_recording(recording_id)

        elapsed = time.monotonic() - started if started is not None else 0.0
        try:
            previous_details = json.loads(recording.quality_json)
        except (json.JSONDecodeError, TypeError):
            previous_details = {}
        if not isinstance(previous_details, dict):
            previous_details = {}
        previous_attempts = previous_details.get("attempts", [])
        if not isinstance(previous_attempts, list):
            previous_attempts = []
        details = {
            "pipeline_version": 1,
            "cancelled": True,
            "cancelled_stage": stage,
            "total_seconds": round(elapsed, 3),
            "attempts": previous_attempts,
        }
        keep_note = bool(recording.note_path and Path(recording.note_path).is_file())
        if not keep_note:
            self.store.replace_suggestions(recording_id, [])
        recording = self.store.update_recording(
            recording_id, status="cancelled", source_path="", audio_path=str(destination),
            note_path=recording.note_path if keep_note else "", processing_seconds=elapsed,
            transcript_text=recording.transcript_text if keep_note else "",
            segments_json=recording.segments_json if keep_note else "[]",
            breaks_json=recording.breaks_json if keep_note else "[]", quality_json=json.dumps(details),
            error="사용자가 전사 작업을 중단했습니다.", completed_at=self.store.now(),
        )
        shutil.rmtree(self.folder(recording_id), ignore_errors=True)
        self.state.set(
            "cancelled", ("중단 완료 · 기존 음성과 전사 결과를 보존했습니다." if keep_note
                          else f"중단 완료 · {destination.name}만 저장했습니다."),
            job_id=recording.id, title=recording.title, course_id=recording.course_id,
        )
        return recording

    def _fail(self, recording_id: str, message: str, started: float) -> None:
        previous = self.store.get_recording(recording_id)
        audio_path, audio_error = self._preserve_failed_audio(previous)
        final_message = message[:1000]
        if audio_error:
            final_message = f"{final_message} · 원음 저장 실패: {audio_error}"[:1000]
        try:
            details = json.loads(previous.quality_json)
        except (json.JSONDecodeError, TypeError):
            details = {}
        if not isinstance(details, dict):
            details = {}
        attempts = details.get("attempts", [])
        if not isinstance(attempts, list):
            attempts = []
        elapsed = time.monotonic() - started
        state = self.state.get()
        stage = (
            str(state.get("phase") or "processing")
            if state.get("job_id") == recording_id else "processing"
        )
        attempts.append({
            "status": "failed",
            "failed_at": self.store.now(),
            "stage": stage,
            "elapsed_seconds": round(elapsed, 3),
            "error": final_message,
        })
        details.update({
            "pipeline_version": 1,
            "failed": True,
            "failed_stage": stage,
            "total_seconds": round(elapsed, 3),
            "attempts": attempts[-10:],
            "audio_preserved": bool(audio_path),
            "audio_preservation_error": audio_error,
        })
        changes = {
            "status": "failed", "processing_seconds": elapsed,
            "error": final_message, "quality_json": json.dumps(details, ensure_ascii=False),
            "completed_at": self.store.now(),
        }
        if audio_path:
            changes["audio_path"] = audio_path
        recording = self.store.update_recording(recording_id, **changes)
        self.state.set("error", f"{recording.title} 처리 실패 · 최근 작업에서 재시도하세요.",
                       job_id=recording.id, title=recording.title, course_id=recording.course_id)

    def decide_suggestion(self, suggestion_id: int, action: str) -> tuple[Recording, object | None]:
        suggestion = self.store.get_suggestion(suggestion_id)
        recording = self.store.get_recording(suggestion.recording_id)
        if recording.review_status in {"queued", "processing"}:
            raise ValueError("검수가 끝난 후 제안을 적용하세요.")
        if suggestion.status != "pending":
            raise ValueError("이미 검토한 제안입니다.")
        if action == "reject":
            self.store.decide_suggestion(suggestion_id, "rejected")
            return recording, None
        if action != "accept":
            raise ValueError("지원하지 않는 검토 결과입니다.")
        if recording.status != "completed" or not recording.note_path:
            raise ValueError("완료된 작업의 제안만 적용할 수 있습니다.")
        segments = json.loads(recording.segments_json)
        try:
            index = int(suggestion.sentence_id.removeprefix("s")) - 1
            current = str(segments[index]["text"])
        except (ValueError, IndexError, KeyError, TypeError) as error:
            raise ValueError("수정 제안의 문장 위치를 찾지 못했습니다.") from error
        if suggestion.original not in current:
            raise ValueError("현재 전사문에서 원래 표현을 찾지 못했습니다.")
        segments[index]["text"] = current.replace(suggestion.original, suggestion.replacement, 1)
        breaks = {int(item) for item in json.loads(recording.breaks_json)}
        updated = replace(
            recording,
            segments_json=json.dumps(segments, ensure_ascii=False),
            transcript_text=transcript_text(segments, breaks),
        )
        note = Path(recording.note_path)
        if not note.is_file():
            raise ValueError("수정할 Markdown 파일을 찾지 못했습니다.")
        temporary = note.with_name(f".{note.name}.{recording.id}.review")
        try:
            temporary.write_text(render_note(updated, segments, breaks), encoding="utf-8")
            temporary.replace(note)
        finally:
            temporary.unlink(missing_ok=True)
        recording = self.store.update_recording(
            recording.id, segments_json=updated.segments_json,
            transcript_text=updated.transcript_text,
        )
        self.store.decide_suggestion(suggestion_id, "accepted")
        return recording, None
