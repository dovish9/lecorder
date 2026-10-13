from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from functools import wraps
from typing import Any

from flask import Flask, jsonify, request, send_file, g

from .config import ROOT, EnvironmentStore, flag
from .engine import Transcriber
from .ai_providers import provider_status
from .ai_settings import using
from .ai_runtime import close_all
from .ai_routes import ai_routes
from .pipeline import RecordingPipeline
from .note_routes import note_routes
from .records import Recording
from .state import JobState
from .services import Services, current_services
from .storage import resolve_storage_file
from .store import LectureStore


from .course_http import register as register_course_http
from .intake_http import register as register_intake_http
from .storage_http import register as register_storage_http
from .recording_http import register as register_recording_http

RESTART_EXIT_CODE = 75


def schedule_managed_restart(delay: float = 0.8) -> bool:
    """Ask start.command to restart the app and its managed Whisper process."""
    if not flag(os.getenv("LECORDER_MANAGED_RESTART")):
        return False
    services = current_services()
    def restart():
        if services:
            services.close()
        close_all()
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
    *, services: Services | None = None, start_workers: bool = False,
) -> Flask:
    paths = environment or (store.environment if store else EnvironmentStore())
    lectures = store or LectureStore(environment=paths)
    state = jobs or JobState()
    engine = transcriber or Transcriber()
    services = services or Services.create(lectures.path.parent)
    lectures.ai = services.ai
    with services.scope():
        workflow = pipeline or RecordingPipeline(lectures, state, engine,
            work_root=lectures.path.parent / "recordings", start_worker=False)
    services.workflow = workflow
    app = Flask(__name__, static_folder=str(ROOT / "web" / "static"))
    app.extensions["recording_pipeline"] = workflow
    app.extensions["lecorder_services"] = services

    @app.before_request
    def enter_services():
        g.service_scope = services.scope()
        g.service_scope.__enter__()

    @app.teardown_request
    def leave_services(error):
        scope = g.pop("service_scope", None)
        if scope:
            scope.__exit__(None, None, None)

    @app.get("/api/capabilities")
    def capabilities():
        return jsonify(services.platform.capabilities())

    app.register_blueprint(note_routes(workflow.notes, workflow.reviews))
    app.register_blueprint(ai_routes(lectures.ai))

    def serialize_outputs(handler):
        @wraps(handler)
        def run(*args, **kwargs):
            with workflow.output_transaction():
                return handler(*args, **kwargs)
        return run

    @app.errorhandler(405)
    def api_method_not_allowed(error):
        if request.path.startswith("/api/"):
            return jsonify(error="현재 실행 중인 앱이 이 요청 방식을 지원하지 않습니다."), 405
        return error

    def recording_payload(recording: Recording, counts: dict[str, int] | None = None) -> dict[str, Any]:
        data = recording.public()
        data["total_processing_seconds"] = lectures.total_processing_seconds(recording)
        data["lecture_note_id"] = workflow.notes.revision(recording.note_revision_id)["note_id"] if recording.note_revision_id else None
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
        with using(lectures.ai.snapshot()):
            speech_status = provider_status("speech")
            llm_status = provider_status("llm")
        recordings = lectures.list_recordings(30)
        counts = lectures.suggestion_counts([item.id for item in recordings])
        return {
            "speech": speech_status,
            "llm": llm_status,
            "settings": current.public(),
            "job": state.get(),
            "note_jobs": workflow.notes.queue_items(),
            "notes": workflow.notes.completion_items(),
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
        with using(lectures.ai.snapshot()):
            return jsonify(proxy="ready", speech=provider_status("speech"), llm=provider_status("llm")), 200


    register_course_http(app, lectures=lectures, paths=paths, schedule_managed_restart=schedule_managed_restart)
    register_intake_http(app, lectures=lectures, recording_payload=recording_payload, serialize_outputs=serialize_outputs, workflow=workflow)
    register_storage_http(app, paths=paths, services=services, storage_file=storage_file)
    register_recording_http(app, lectures=lectures, recording_payload=recording_payload, serialize_outputs=serialize_outputs, suggestions_payload=suggestions_payload, workflow=workflow)

    if start_workers:
        services.start()
    return app
