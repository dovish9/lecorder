from __future__ import annotations

from pathlib import Path

from flask import jsonify, request

from .config import text
from .files import safe_name
from .transcription import SUPPORTED_MEDIA


def register(app, *, lectures, recording_payload, serialize_outputs, workflow):
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


    @app.patch("/api/recordings/<recording_id>/course")
    @serialize_outputs
    def select_recording_course(recording_id: str):
        payload = request.get_json(silent=True) or {}
        try:
            course_id = payload.get("course_id")
            if isinstance(course_id, bool) or not isinstance(course_id, int):
                raise ValueError("강의를 선택하세요.")
            recording = workflow.select_recording_course(recording_id, course_id)
            return jsonify(ok=True, recording=recording_payload(recording))
        except KeyError as error:
            return jsonify(error=error.args[0]), 404
        except (TypeError, ValueError) as error:
            return jsonify(error=str(error)), 400

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
