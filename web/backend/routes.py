from __future__ import annotations

import json
import os
import threading
import uuid
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
            saved = services.ai.saved_snapshot()
            active = services.ai.snapshot()
            changed = [area for area in saved if saved[area] != active[area]]
            services.close()
            from .settings_restart import rebind_pending
            rebind_pending(services.workflow.store, saved, changed)
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
    services.ai.freeze()
    paths.freeze()
    instance_id = uuid.uuid4().hex
    restarting = threading.Event()
    restart_lock = threading.Lock()
    lectures.ai = services.ai
    with services.scope():
        workflow = pipeline or RecordingPipeline(lectures, state, engine,
            work_root=lectures.path.parent / "recordings", start_worker=False)
    services.workflow = workflow
    from .request_history import RequestHistory
    services.history = RequestHistory(lectures)
    from .insights import Insights
    services.insights = Insights(lectures)
    from .backup import Backups
    services.backups = Backups(services, lectures)
    from .live_transcription import LiveTranscription
    from .live_http import register as register_live_http
    from .ai_settings import live_configuration
    services.live = LiveTranscription(workflow, services.platform, ai_control=services.ai_control,
        config_provider=lambda: live_configuration(services.ai.snapshot()["speech"]))
    workflow.live = services.live
    app = Flask(__name__, static_folder=str(ROOT / "web" / "static"))
    register_live_http(app, services.live)
    app.extensions["recording_pipeline"] = workflow
    app.extensions["lecorder_services"] = services

    @app.before_request
    def enter_services():
        g.service_scope = services.scope()
        g.service_scope.__enter__()
        if restarting.is_set() and request.method not in {'GET','HEAD','OPTIONS'}:
            return jsonify(error='서버 재시작 중입니다. 잠시 후 다시 시도하세요.'), 503
        if request.method not in {'GET','HEAD','OPTIONS'}:
            origin=request.headers.get('Origin')
            if origin and origin.rstrip('/')!=request.host_url.rstrip('/'):
                return jsonify(error='다른 사이트에서 보낸 변경 요청은 허용하지 않습니다.'),403
        if request.method not in {'GET','HEAD','OPTIONS'} and not request.path.startswith('/api/backups'):
            if not services.backups.begin_request():
                return jsonify(error='백업·복원 중입니다. 완료 후 다시 시도하세요.'), 409
            g.backup_mutation = True

    @app.teardown_request
    def leave_services(error):
        if g.pop('backup_mutation', False):
            services.backups.end_request()
        scope = g.pop("service_scope", None)
        if scope:
            scope.__exit__(None, None, None)

    @app.get("/api/capabilities")
    def capabilities():
        return jsonify(services.platform.capabilities())

    @app.get('/api/search')
    def search():
        try:
            course = int(request.args['course']) if request.args.get('course') else None
            offset = max(0, int(request.args.get('offset', 0)))
            limit = min(50, max(1, int(request.args.get('limit', 30))))
        except ValueError:
            return jsonify(error='검색 조건이 올바르지 않습니다.'), 400
        kind = request.args.get('kind') or None
        if kind not in {None, 'recording', 'note'}:
            return jsonify(error='검색 종류가 올바르지 않습니다.'), 400
        return jsonify(services.insights.search(request.args.get('q', ''), course, kind, offset, limit))

    @app.get('/api/usage')
    def usage():
        try:
            days = int(request.args.get('days', 1))
            offset = int(request.args.get('timezone_offset', 0))
            if days not in {0, 1, 7, 30} or not -840 <= offset <= 840:
                raise ValueError()
        except ValueError:
            return jsonify(error='집계 기간이 올바르지 않습니다.'), 400
        return jsonify(services.insights.usage(days, offset, request.args.get('timezone','')))

    @app.get('/api/recovery')
    def recovery():
        with lectures.transaction() as db:
            rows = db.execute("""SELECT s.id,s.owner,s.kind,s.status,s.message,COALESCE(r.title,n.title,'작업') AS title
                FROM recovery_sessions s LEFT JOIN recordings r ON r.id=s.owner
                LEFT JOIN note_revisions v ON v.id=s.owner LEFT JOIN lecture_notes n ON n.id=v.note_id
                WHERE s.message!='' AND s.acknowledged=0 ORDER BY s.created_at DESC LIMIT 20""").fetchall()
        return jsonify(items=[dict(r) for r in rows])

    def transfer_payload(job):
        return {k:v for k,v in job.items() if k not in {'source','cancelled'}}

    @app.post('/api/backups')
    def create_backup():
        return jsonify(transfer_payload(services.backups.start('export'))), 202

    @app.post('/api/backups/inspect')
    def inspect_backup():
        import uuid
        upload = request.files.get('file')
        if upload is None:
            return jsonify(error='백업 파일을 선택하세요.'), 400
        path = services.backups.folder / (uuid.uuid4().hex+'.zip')
        upload.save(path)
        return jsonify(transfer_payload(services.backups.start('inspect', path=path))), 202

    @app.get('/api/backups/<job_id>')
    def backup_status(job_id):
        job = services.backups.jobs.get(job_id)
        return (jsonify(transfer_payload(job)), 200) if job else (jsonify(error='백업 작업을 찾지 못했습니다.'), 404)

    @app.post('/api/backups/<job_id>/restore')
    def restore_backup(job_id):
        job = services.backups.jobs.get(job_id)
        if not job or job['state'] != 'ready' or job['cancelled']:
            return jsonify(error='검사가 완료된 백업을 선택하세요.'), 409
        return jsonify(transfer_payload(services.backups.start('restore', inspected=job))), 202

    @app.post('/api/backups/<job_id>/cancel')
    def cancel_backup(job_id):
        job = services.backups.jobs.get(job_id)
        if not job:
            return jsonify(error='백업 작업을 찾지 못했습니다.'), 404
        job['cancelled'] = True
        if job['state'] == 'ready':
            job.update(state='cancelled', message='가져오기를 취소했습니다.')
        return jsonify(ok=True)

    @app.get('/api/backups/<job_id>/download')
    def download_backup(job_id):
        job = services.backups.jobs.get(job_id)
        if not job or job['operation'] != 'export' or job['state'] != 'completed':
            return jsonify(error='완료된 백업이 없습니다.'), 404
        return send_file(services.backups.folder / job['file'], as_attachment=True,
                         download_name='Lecorder-backup.zip')

    @app.post('/api/recovery/<session_id>/acknowledge')
    def acknowledge_recovery(session_id):
        with lectures.transaction() as db:
            db.execute('UPDATE recovery_sessions SET acknowledged=1 WHERE id=?', (session_id,))
        return jsonify(ok=True)

    app.register_blueprint(note_routes(workflow.notes, workflow.reviews, services.live))
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
        data['waiting_for_ai'] = not services.ai_control.public()['enabled'] and (recording.status in {'queued', 'processing'} or recording.review_status in {'queued', 'processing'})
        data['has_live_draft'] = services.live.has_draft(recording)
        data["total_processing_seconds"] = lectures.total_processing_seconds(recording)
        active_live = services.live.active
        if active_live and not active_live.stopped.is_set() and (recording.status in {"queued", "processing"} or recording.review_status in {'queued', 'processing'}):
            settings = json.loads(recording.ai_settings_json or '{}')
            area = 'llm' if recording.review_status in {'queued', 'processing'} else 'speech'
            data['waiting_for_live'] = settings.get(area, {}).get('managed', False)

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
        note_jobs = workflow.notes.queue_items()
        active_live = services.live.active
        if active_live and not active_live.stopped.is_set():
            for item in note_jobs:
                managed = item.pop('managed_llm', None)
                item['waiting_for_live'] = managed is None or bool(managed)
        for item in note_jobs:
            item['waiting_for_ai'] = not services.ai_control.public()['enabled'] and item.get('stage') != 'extracting'
        return {
            'ai_control': services.ai_control.public(),
            "live_recording_id": services.live.active.recording_id if services.live.active and not services.live.active.stopped.is_set() else None,
            "speech": speech_status,
            "llm": llm_status,
            "settings": current.public(),
            "job": state.get(),
            "note_jobs": note_jobs,
            "notes": workflow.notes.completion_items(),
            "recordings": [recording_payload(item, counts[item.id]) for item in recordings],
        }

    def bootstrap_payload() -> dict[str, Any]:
        payload = runtime_payload()
        payload.update({
            "courses": [course.public() for course in lectures.list_courses()],
            "active_course_id": payload["settings"]["course_id"],
            "environment": paths.saved().public(),
            "instance_id": instance_id,
            "restart_required": paths.saved() != paths.get() or services.ai.saved_snapshot() != services.ai.snapshot(),
        })
        return payload

    @app.get("/api/status")
    def status():
        return jsonify(runtime_payload())

    @app.post('/api/ai/control')
    def ai_control():
        value = request.get_json(silent=True)
        if not isinstance(value, dict) or type(value.get('enabled')) is not bool:
            return jsonify(error='enabled는 boolean이어야 합니다.'), 400
        try:
            services.ai_control.set_enabled(value['enabled'])
            if not value['enabled']:
                def unload():
                    for runtime in services.runtimes.runtimes():
                        runtime.release_idle()
                services.ai_control.clean_up(unload)
            return jsonify(ai_control=services.ai_control.public())
        except ValueError as error:
            return jsonify(error=str(error)), 409

    @app.post('/api/restart')
    def restart_server():
        with restart_lock:
            with lectures.transaction() as db:
                recording = db.execute("SELECT 1 FROM recordings WHERE status='recording' LIMIT 1").fetchone()
            if recording:
                return jsonify(error='녹음과 원음 업로드를 마친 뒤 재시작해 주세요. 다른 탭의 녹음도 확인하세요.'), 409
            if not flag(os.getenv('LECORDER_MANAGED_RESTART')):
                return jsonify(error='자동 재시작은 start.command로 실행했을 때 사용할 수 있습니다. 저장된 설정은 앱을 다시 실행하면 적용됩니다.'), 409
            restarting.set()
            if not schedule_managed_restart():
                restarting.clear()
                return jsonify(error='재시작을 예약하지 못했습니다.'), 409
            return jsonify(ok=True, instance_id=instance_id, restart_scheduled=True), 202

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
