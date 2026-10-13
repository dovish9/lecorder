from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import jsonify, request, send_file

from .config import text
from .files import safe_name
from .output_files import rename_output_files
from .storage import resolve_storage_file
from .transcription import SUPPORTED_MEDIA


def register(app, *, lectures, recording_payload, serialize_outputs, suggestions_payload, workflow):
    @app.delete("/api/recordings/<recording_id>/history")
    @serialize_outputs
    def delete_recording_history(recording_id: str):
        try:
            workflow.delete_history(recording_id)
            return jsonify(ok=True)
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.get("/api/recordings")
    def list_recordings():
        return jsonify(recordings=[recording_payload(item) for item in lectures.list_recordings(50)])


    @app.get("/api/recordings/<recording_id>")
    def get_recording(recording_id: str):
        try:
            recording = lectures.get_recording(recording_id)
            return jsonify(
                recording=recording_payload(recording),
                suggestions=suggestions_payload(recording),
            )
        except KeyError as error:
            return jsonify(error=error.args[0]), 404


    @app.patch("/api/recordings/<recording_id>")
    @serialize_outputs
    def update_recording(recording_id: str):
        payload = request.get_json(silent=True) or {}
        title = safe_name(text(payload.get("title"), 160))
        if not title:
            return jsonify(error="녹음 제목을 입력해 주세요."), 400
        try:
            recording = lectures.get_recording(recording_id)
            if recording.status not in {"completed", "failed", "cancelled"}:
                raise ValueError("완료·실패·중단된 작업의 제목만 수정할 수 있습니다.")
            if recording.status != "cancelled" and recording.review_status in {"queued", "processing"}:
                raise ValueError("검수가 끝난 후 파일을 수정하거나 삭제하세요.")
            root = Path(recording.output_dir).expanduser().resolve()
            targets: list[Path] = []
            target_fields: list[str] = []
            for field, value in (("audio_path", recording.audio_path), ("note_path", recording.note_path)):
                if not value:
                    continue
                expected = Path(value).expanduser().resolve()
                if expected.parent != root:
                    raise ValueError("저장 폴더 밖의 파일은 이름을 수정할 수 없습니다.")
                try:
                    target = resolve_storage_file(root, expected.name)
                except FileNotFoundError:
                    continue
                targets.append(target)
                target_fields.append(field)
            audio_names = None
            if recording.audio_path:
                old_audio = Path(recording.audio_path).name
                audio_names = (old_audio, f"{title}{Path(old_audio).suffix}")
            destinations, rollback = rename_output_files(targets, title, audio_names)
            changes: dict[str, Any] = {"title": title}
            changes.update(
                (field, str(destination))
                for field, destination in zip(target_fields, destinations, strict=True)
            )
            try:
                updated = lectures.update_recording(recording_id, **changes)
            except Exception:
                rollback()
                raise
            return jsonify(
                ok=True,
                recording=recording_payload(updated),
                renamed_files=[destination.name for destination in destinations],
            )
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except (UnicodeError, ValueError) as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일 이름을 수정하지 못했습니다: {error}"), 500


    @app.delete("/api/recordings/<recording_id>/with-files")
    @serialize_outputs
    def delete_recording_with_files(recording_id: str):
        try:
            recording = lectures.get_recording(recording_id)
            if recording.status not in {"completed", "failed", "cancelled"}:
                raise ValueError("완료·실패·중단된 작업만 파일과 함께 삭제할 수 있습니다.")
            if recording.status != "cancelled" and recording.review_status in {"queued", "processing"}:
                raise ValueError("검수가 끝난 후 파일을 수정하거나 삭제하세요.")
            root = Path(recording.output_dir).expanduser().resolve()
            targets: list[Path] = []
            for value in (recording.audio_path, recording.note_path):
                if not value:
                    continue
                expected = Path(value).expanduser().resolve()
                if expected.parent != root:
                    raise ValueError("저장 폴더 밖의 파일은 삭제할 수 없습니다.")
                try:
                    targets.append(resolve_storage_file(root, expected.name))
                except FileNotFoundError:
                    continue
            for target in targets:
                target.unlink()
            workflow.delete_history(recording_id)
            return jsonify(ok=True, deleted_files=[target.name for target in targets])
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일을 삭제하지 못했습니다: {error}"), 500


    @app.get("/api/recordings/<recording_id>/audio")
    def get_recording_audio(recording_id: str):
        try:
            recording = lectures.get_recording(recording_id)
            audio = Path(recording.audio_path)
            if not audio.is_file() or audio.suffix.lower() not in SUPPORTED_MEDIA:
                raise FileNotFoundError("녹음 파일을 찾지 못했습니다.")
            return send_file(audio, conditional=True)
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404


    @app.post("/api/recordings/<recording_id>/retranscribe")
    @serialize_outputs
    def retranscribe_recording(recording_id: str):
        try:
            payload = request.get_json(silent=True) or {}
            recording = workflow.retranscribe(recording_id, payload.get("lecture_note_id"), retain_note="lecture_note_id" not in payload, course_id=payload.get("course_id"), title=payload.get("title"))
            return jsonify(ok=True, recording=recording_payload(recording)), 202
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400


    @app.post("/api/recordings/<recording_id>/retry")
    def retry_recording(recording_id: str):
        try:
            recording = workflow.retry(recording_id)
            return jsonify(ok=True, recording=recording_payload(recording)), 202
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400


    @app.post("/api/recordings/<recording_id>/cancel")
    def cancel_recording(recording_id: str):
        try:
            recording = workflow.cancel(recording_id)
            return jsonify(ok=True, recording=recording_payload(recording)), (
                200 if recording.status == "cancelled" else 202
            )
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400


    @app.post("/api/suggestions/<int:suggestion_id>/decision")
    def decide_suggestion(suggestion_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            recording, course = workflow.decide_suggestion(suggestion_id, str(payload.get("action") or ""))
            return jsonify(
                ok=True,
                recording=recording_payload(recording),
                suggestions=suggestions_payload(recording),
                course=course.public() if course else None,
            )
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
