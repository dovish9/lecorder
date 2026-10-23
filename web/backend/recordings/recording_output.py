from __future__ import annotations
from web.backend.execution_metrics import current_trace
from web.backend.atomic import publish_file

import json
from web.backend.media import request as media_request
import shutil
import subprocess
import time
from dataclasses import replace
from pathlib import Path


from web.backend.files import OutputFiles, clear_hidden_flag
from web.backend.markdown import render_note, transcript_text
from web.backend.library.records import Recording
from web.backend.speech.transcription import CancellationToken, TranscriptionCancelled


class RecordingOutput:
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
        try:
            media_request("transcode", cancellation, source=source,
                          destination=destination, playback=True)
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
            publish_file(hidden, destination)
            return str(destination), ""
        except Exception as error:
            return "", str(error)
        finally:
            if hidden:
                hidden.unlink(missing_ok=True)


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
                publish_file(hidden_audio, destination)
        except Exception as error:
            if hidden_audio:
                hidden_audio.unlink(missing_ok=True)
            self._fail(recording_id, f"작업은 중단됐지만 원본 음성을 보존하지 못했습니다: {error}",
                       started or time.monotonic())
            return self.store.get_recording(recording_id)

        elapsed = max(0, time.monotonic() - started - current_trace()["waiting_seconds"]) if started is not None else 0.0
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
            **current_trace(),
            "cancelled_stage": stage,
            "total_seconds": round(elapsed, 3),
            "attempts": previous_attempts,
        }
        keep_note = bool(recording.note_path and Path(recording.note_path).is_file())
        if not keep_note:
            self.store.approvals.replace_suggestions(recording_id, [])
        recording = self.store.update_recording(
            recording_id, status="cancelled", source_path="", audio_path=str(destination),
            note_path=recording.note_path if keep_note else "", processing_seconds=elapsed,
            transcript_text=recording.transcript_text if keep_note else "",
            segments_json=recording.segments_json if keep_note else "[]",
            breaks_json=recording.breaks_json if keep_note else "[]", quality_json=json.dumps(details),
            error="사용자가 음성 변환 작업을 중단했습니다.", completed_at=self.store.now(),
            review_status="none", review_error="",
        )
        shutil.rmtree(self.folder(recording_id), ignore_errors=True)
        self.state.set(
            "cancelled", ("중단 완료 · 기존 음성과 음성 변환 결과를 보존했습니다." if keep_note
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
        elapsed = max(0, time.monotonic() - started - current_trace()["waiting_seconds"])
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
            **current_trace(),
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
        suggestion = self.store.approvals.get_suggestion(suggestion_id)
        recording = self.store.get_recording(suggestion.recording_id)
        if recording.review_status in {"queued", "processing"}:
            raise ValueError("검수가 끝난 후 제안을 적용하세요.")
        if suggestion.status != "pending":
            raise ValueError("이미 검토한 제안입니다.")
        if action == "reject":
            self.store.approvals.decide_suggestion(suggestion_id, "rejected")
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
            raise ValueError("현재 변환 텍스트에서 원래 표현을 찾지 못했습니다.")
        context = dict(segments[index], index=index)
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
            recording = self.store.approvals.accept_suggestion(
                suggestion_id, updated, context, lambda: publish_file(temporary, note))
        finally:
            temporary.unlink(missing_ok=True)
        return recording, None
