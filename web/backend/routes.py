from __future__ import annotations

import json
import os
import subprocess
import threading
from pathlib import Path
from functools import wraps
from typing import Any

from flask import Flask, jsonify, request, send_file

from .config import ROOT, WHISPER_PORT, EnvironmentStore, flag, text
from .engine import Transcriber, ollama_ready, port_ready
from .files import safe_name
from .output_files import prepare_webm_playback, rename_output_files
from .pipeline import RecordingPipeline
from .note_routes import note_routes
from .records import Recording
from .state import JobState
from .whisper_runtime import whisper_runtime
from .storage import resolve_storage_file, storage_catalog
from .store import LectureStore
from .transcription import SUPPORTED_MEDIA


RESTART_EXIT_CODE = 75


def schedule_managed_restart(delay: float = 0.8) -> bool:
    """Ask start.command to restart the app and its managed Whisper process."""
    if not flag(os.getenv("LECORDER_MANAGED_RESTART")):
        return False
    def restart():
        whisper_runtime.close()
        os._exit(RESTART_EXIT_CODE)
    timer = threading.Timer(delay, restart)
    timer.daemon = True
    timer.start()
    return True


def create_app(
    store: LectureStore | None = None,
    jobs: JobState | None = None,
    transcriber: Transcriber | None = None,
    environment: EnvironmentStore | None = None,
    pipeline: RecordingPipeline | None = None,
) -> Flask:
    paths = environment or (store.environment if store else EnvironmentStore())
    lectures = store or LectureStore(environment=paths)
    state = jobs or JobState()
    engine = transcriber or Transcriber()
    workflow = pipeline or RecordingPipeline(lectures, state, engine)
    app = Flask(__name__, static_folder=str(ROOT / "web" / "static"))
    app.extensions["recording_pipeline"] = workflow
    app.register_blueprint(note_routes(workflow.notes, workflow.reviews))

    def serialize_outputs(handler):
        @wraps(handler)
        def run(*args, **kwargs):
            with workflow._lock:
                return handler(*args, **kwargs)
        return run

    @app.errorhandler(405)
    def api_method_not_allowed(error):
        if request.path.startswith("/api/"):
            return jsonify(error="현재 실행 중인 앱이 이 요청 방식을 지원하지 않습니다."), 405
        return error

    def recording_payload(recording: Recording, counts: dict[str, int] | None = None) -> dict[str, Any]:
        data = recording.public()
        data["lecture_note_id"] = workflow.notes._revision(recording.note_revision_id)["note_id"] if recording.note_revision_id else None
        data["suggestion_counts"] = (
            counts if counts is not None else lectures.suggestion_counts([recording.id])[recording.id]
        )
        return data

    def suggestions_payload(recording: Recording) -> list[dict[str, Any]]:
        try:
            segments = json.loads(recording.segments_json)
        except (json.JSONDecodeError, TypeError):
            segments = []
        output: list[dict[str, Any]] = []
        for suggestion in lectures.list_suggestions(recording.id):
            data = suggestion.public()
            try:
                index = int(suggestion.sentence_id.removeprefix("s")) - 1
                data["context"] = str(segments[index].get("text") or "")
                data["start"] = float(segments[index].get("start") or 0)
                data["end"] = float(segments[index].get("end") or data["start"])
            except (ValueError, TypeError, IndexError, AttributeError):
                data["context"] = ""
                data["start"] = 0
                data["end"] = 0
            data["audio_name"] = Path(recording.audio_path).name if recording.audio_path else ""
            output.append(data)
        return output

    def storage_file(name: str) -> Path:
        return resolve_storage_file(Path(paths.get().output_dir), name)

    @app.get("/")
    def dashboard():
        return send_file(ROOT / "web" / "static" / "dashboard.html")

    @app.get("/guide")
    def guide():
        return send_file(ROOT / "web" / "static" / "guide.html")

    def runtime_payload() -> dict[str, Any]:
        current = lectures.get()
        ollama, model = ollama_ready(current.llm_model)
        recordings = lectures.list_recordings(30)
        counts = lectures.suggestion_counts([item.id for item in recordings])
        return {
            "whisper_ready": port_ready(WHISPER_PORT),
            "whisper_state": whisper_runtime.state,
            "ollama_ready": ollama,
            "ollama_model_ready": model,
            "settings": current.public(),
            "job": state.get(),
            "recordings": [recording_payload(item, counts[item.id]) for item in recordings],
        }

    def bootstrap_payload() -> dict[str, Any]:
        payload = runtime_payload()
        payload.update({
            "courses": [course.public() for course in lectures.list_courses()],
            "active_course_id": payload["settings"]["course_id"],
            "environment": paths.get().public(),
        })
        return payload

    @app.get("/api/status")
    def status():
        return jsonify(runtime_payload())

    @app.get("/api/bootstrap")
    def bootstrap():
        return jsonify(bootstrap_payload())

    @app.get("/health")
    def health():
        ready = port_ready(WHISPER_PORT)
        return jsonify(proxy="ready", whisper="ready" if ready else whisper_runtime.state), 200

    @app.post("/api/courses")
    def create_course():
        payload = request.get_json(silent=True) or {}
        try:
            course = lectures.create_course(payload.get("name", ""))
            return jsonify(ok=True, course=course.public()), 201
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.patch("/api/courses/<int:course_id>")
    def update_course(course_id: int):
        payload = request.get_json(silent=True) or {}
        try:
            course = lectures.update_course(course_id, **payload)
            return jsonify(ok=True, course=course.public())
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.delete("/api/courses/<int:course_id>")
    def delete_course(course_id: int):
        try:
            active = lectures.delete_course(course_id)
            return jsonify(ok=True, active_course_id=active)
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.post("/api/courses/<int:course_id>/select")
    def select_course(course_id: int):
        try:
            course = lectures.select_course(course_id)
            return jsonify(ok=True, course=course.public())
        except KeyError as error:
            return jsonify(error=error.args[0]), 404

    @app.put("/api/environment")
    def update_environment():
        payload = request.get_json(silent=True) or {}
        try:
            current = paths.update(**payload)
            scheduled = schedule_managed_restart()
            return jsonify(
                ok=True,
                environment=current.public(),
                restart_required=True,
                restart_scheduled=scheduled,
            )
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.post("/api/upload")
    def upload():
        uploaded = request.files.get("file")
        if uploaded is None:
            return jsonify(error="업로드할 녹음파일을 선택해 주세요."), 400
        original = Path(uploaded.filename or "").name
        extension = Path(original).suffix.lower()
        if extension not in SUPPORTED_MEDIA:
            return jsonify(error="지원하지 않는 오디오 형식입니다."), 400
        current = lectures.get()
        title = safe_name(text(request.form.get("title"), 160))
        if not title:
            return jsonify(error="저장할 파일 이름을 입력해 주세요."), 400
        try:
            recording = workflow.create_upload(current, title, extension, uploaded.stream, request.form.get("lecture_note_id") or None)
            return jsonify(ok=True, recording=recording_payload(recording)), 202
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except Exception as error:
            return jsonify(error=str(error)), 500

    @app.post("/api/recordings")
    def begin_recording():
        payload = request.get_json(silent=True) or {}
        title = safe_name(text(payload.get("title"), 160))
        extension = text(payload.get("extension"), 12).lower()
        if not title:
            return jsonify(error="저장할 파일 이름을 입력해 주세요."), 400
        if extension not in SUPPORTED_MEDIA:
            return jsonify(error="지원하지 않는 녹음 형식입니다."), 400
        try:
            recording = workflow.begin_chunked(lectures.get(), title, extension, payload.get("lecture_note_id"))
            return jsonify(ok=True, recording=recording_payload(recording)), 201
        except Exception as error:
            return jsonify(error=str(error)), 500

    @app.patch("/api/recordings/<recording_id>/lecture-note")
    @serialize_outputs
    def select_recording_note(recording_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            if "lecture_note_id" not in payload:
                raise ValueError("강의노트를 선택하세요.")
            recording = workflow.select_recording_note(recording_id, payload["lecture_note_id"])
            return jsonify(ok=True, recording=recording_payload(recording))
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except (TypeError, ValueError) as error:
            return jsonify(error=str(error)), 400

    @app.put("/api/recordings/<recording_id>/chunks/<int:index>")
    def save_recording_chunk(recording_id: str, index: int):
        try:
            size = workflow.save_chunk(recording_id, index, request.stream)
            return jsonify(ok=True, index=index, size=size)
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.post("/api/recordings/<recording_id>/finalize")
    @serialize_outputs
    def finalize_recording(recording_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            recording = workflow.finalize_chunked(
                recording_id, float(payload.get("duration_seconds") or 0)
            )
            return jsonify(ok=True, recording=recording_payload(recording)), 202
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except (TypeError, ValueError) as error:
            return jsonify(error=str(error)), 400

    @app.delete("/api/recordings/<recording_id>")
    def discard_recording(recording_id: str):
        try:
            workflow.discard(recording_id)
            return jsonify(ok=True)
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400

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

    @app.get("/api/storage")
    def storage():
        return jsonify(storage_catalog(Path(paths.get().output_dir)))

    @app.get("/api/storage/file")
    def serve_storage_file():
        try:
            return send_file(storage_file(str(request.args.get("name") or "")), conditional=True)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400

    @app.post("/api/storage/audio/prepare")
    def prepare_storage_audio():
        payload = request.get_json(silent=True) or {}
        try:
            target = storage_file(str(payload.get("name") or ""))
            if target.suffix.lower() not in SUPPORTED_MEDIA:
                raise ValueError("오디오 파일을 선택해 주세요.")
            prepared, duration = prepare_webm_playback(target)
            response: dict[str, Any] = {"ok": True, "prepared": prepared}
            if duration is not None:
                response["duration"] = duration
            return jsonify(response)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except (OSError, subprocess.SubprocessError) as error:
            return jsonify(error=f"오디오 재생 정보를 준비하지 못했습니다: {error}"), 500

    @app.delete("/api/storage/file")
    def delete_storage_file():
        try:
            target = storage_file(str(request.args.get("name") or ""))
            name = target.name
            target.unlink()
            return jsonify(ok=True, name=name)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일을 삭제하지 못했습니다: {error}"), 500

    @app.delete("/api/storage/files")
    def delete_storage_files():
        payload = request.get_json(silent=True) or {}
        names = payload.get("names")
        if (
            not isinstance(names, list)
            or not names
            or len(names) > 10
            or not all(isinstance(name, str) for name in names)
        ):
            return jsonify(error="삭제할 저장 파일을 선택해 주세요."), 400
        try:
            targets = [storage_file(str(name)) for name in dict.fromkeys(names)]
            for target in targets:
                target.unlink()
            return jsonify(ok=True, deleted_files=[target.name for target in targets])
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일을 삭제하지 못했습니다: {error}"), 500

    @app.patch("/api/storage/files")
    def rename_storage_files():
        payload = request.get_json(silent=True) or {}
        names = payload.get("names")
        title = safe_name(text(payload.get("title"), 160))
        if (
            not isinstance(names, list)
            or not names
            or len(names) > 10
            or not all(isinstance(name, str) for name in names)
        ):
            return jsonify(error="수정할 저장 파일을 선택해 주세요."), 400
        if not title:
            return jsonify(error="파일 제목을 입력해 주세요."), 400
        try:
            targets = [storage_file(str(name)) for name in dict.fromkeys(names)]
            if len({target.stem for target in targets}) != 1:
                raise ValueError("같은 녹음에 속한 파일만 함께 수정할 수 있습니다.")
            destinations, _ = rename_output_files(targets, title)
            return jsonify(
                ok=True,
                name=title,
                files=[destination.name for destination in destinations],
            )
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except (UnicodeError, ValueError) as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"파일 이름을 수정하지 못했습니다: {error}"), 500

    @app.post("/api/storage/reveal")
    def reveal_storage_file():
        payload = request.get_json(silent=True) or {}
        try:
            target = storage_file(str(payload.get("name") or ""))
            subprocess.Popen(
                ["open", "-R", str(target)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            return jsonify(ok=True)
        except FileNotFoundError as error:
            return jsonify(error=str(error)), 404
        except ValueError as error:
            return jsonify(error=str(error)), 400
        except OSError as error:
            return jsonify(error=f"Finder를 열지 못했습니다: {error}"), 500

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
            if recording.review_status in {"queued", "processing"}:
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
            if recording.review_status in {"queued", "processing"}:
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
    def retranscribe_recording(recording_id: str):
        try:
            payload = request.get_json(silent=True) or {}
            recording = workflow.retranscribe(recording_id, payload.get("lecture_note_id"), retain_note="lecture_note_id" not in payload)
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

    return app
