"""Durable request results and usage. Credentials and request bodies are never stored."""
from contextlib import contextmanager, nullcontext, closing
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import time
import uuid
import sqlite3
from pathlib import Path

from .execution_metrics import current_trace

_scope = ContextVar('request_history_scope', default=None)
_tracking = ContextVar('tracking_request', default=False)
VERSION = 1


def tracking_request():
    return _tracking.get()


def fingerprint(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode()).hexdigest()


class RequestHistory:
    def __init__(self, store):
        self.store = store
        with store.transaction() as db:
            exists = db.execute("SELECT 1 FROM sqlite_master WHERE name='request_checkpoints'").fetchone()
            if not exists and db.execute('SELECT 1 FROM recordings LIMIT 1').fetchone():
                path = store.path.with_name(store.path.stem+'-before-recovery.db')
                if not path.exists():
                    with closing(sqlite3.connect(path)) as target:
                        db.backup(target)
        with store.transaction() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS recovery_sessions (
                    id TEXT PRIMARY KEY, owner TEXT NOT NULL, kind TEXT NOT NULL,
                    status TEXT NOT NULL, created_at TEXT NOT NULL, message TEXT NOT NULL DEFAULT '',
                    acknowledged INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS request_checkpoints (
                    session_id TEXT NOT NULL REFERENCES recovery_sessions(id) ON DELETE CASCADE,
                    fingerprint TEXT NOT NULL, result TEXT NOT NULL,
                    PRIMARY KEY(session_id, fingerprint));
                CREATE TABLE IF NOT EXISTS ai_requests (
                    id TEXT PRIMARY KEY, session_id TEXT, owner TEXT NOT NULL,
                    kind TEXT NOT NULL, provider TEXT NOT NULL, model TEXT NOT NULL,
                    location TEXT NOT NULL, started_at TEXT NOT NULL, completed_at TEXT,
                    status TEXT NOT NULL, seconds REAL, audio_seconds REAL,
                    input_tokens INTEGER, output_tokens INTEGER);
            ''')
            if 'source_id' not in {r[1] for r in db.execute('PRAGMA table_info(ai_requests)')}:
                db.execute("ALTER TABLE ai_requests ADD COLUMN source_id TEXT NOT NULL DEFAULT ''")
            db.execute("UPDATE ai_requests SET source_id=id WHERE source_id=''")
            if 'seconds' not in {r[1] for r in db.execute('PRAGMA table_info(request_checkpoints)')}:
                db.execute('ALTER TABLE request_checkpoints ADD COLUMN seconds REAL NOT NULL DEFAULT 0')
        self.import_legacy()

    def import_legacy(self):
        """Only explicit provider-call records; aggregate old counters are ambiguous."""
        with self.store.transaction() as db:
            owners={r[0] for r in db.execute("SELECT DISTINCT owner FROM ai_requests WHERE id NOT LIKE 'legacy-%'")}
            for table in ('transcription_runs','review_runs'):
                for row in db.execute(f'SELECT * FROM {table}').fetchall():
                    if row['recording_id'] in owners: continue
                    try:
                        metrics=json.loads(row['metrics'] or '{}')
                        settings=json.loads(row['settings'] or '{}')
                        config=settings.get('ai_settings',{}).get('llm',{})
                        for index,call in enumerate(metrics.get('provider_calls',[])):
                            if not call.get('provider') or not call.get('model'): continue
                            provider=call['provider']
                            location='local' if config.get('managed') else 'custom' if provider=='llama_cpp' else 'external'
                            db.execute('INSERT OR IGNORE INTO ai_requests(id,owner,kind,provider,model,location,started_at,completed_at,status,seconds,input_tokens,output_tokens) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                                (f'legacy-{row["id"]}-{index}',row['recording_id'],'llm',provider,call['model'],location,row['started_at'],row['completed_at'],call.get('status','unknown'),call.get('seconds'),call.get('input_tokens'),call.get('output_tokens')))
                    except (ValueError,TypeError,KeyError):
                        continue
            db.execute("UPDATE ai_requests SET source_id=id WHERE source_id=''")

    @contextmanager
    def scope(self, owner, kind):
        with self.store.transaction() as db:
            row = db.execute("SELECT id FROM recovery_sessions WHERE owner=? AND kind=? AND status='active' ORDER BY created_at DESC LIMIT 1", (owner, kind)).fetchone()
            session = row['id'] if row else uuid.uuid4().hex
            if not row:
                db.execute('INSERT INTO recovery_sessions(id,owner,kind,status,created_at) VALUES (?,?,?,?,?)',
                           (session, owner, kind, 'active', self.store.now()))
        token = _scope.set((self, session, owner, kind))
        try:
            yield session
        finally:
            _scope.reset(token)

    def finish(self, session, status):
        with self.store.transaction() as db:
            db.execute('UPDATE recovery_sessions SET status=? WHERE id=?', (status, session))
            if status=='completed':
                count=db.execute('SELECT COUNT(*) FROM request_checkpoints WHERE session_id=?',(session,)).fetchone()[0]
                db.execute("UPDATE recovery_sessions SET message=? WHERE id=? AND message!=''",(f'복구 완료 · 저장된 요청 결과를 재사용하고 처리를 마쳤습니다. (완료 요청 {count}개)',session))
            if status == 'failed':
                row = db.execute('SELECT owner,message FROM recovery_sessions WHERE id=?',(session,)).fetchone()
                if row and row['message']:
                    recording=db.execute('SELECT error,review_error FROM recordings WHERE id=?',(row['owner'],)).fetchone()
                    reason=(recording['review_error'] or recording['error']) if recording else '노트 원본과 AI 설정을 확인하세요.'
                    db.execute('UPDATE recovery_sessions SET message=?,acknowledged=0 WHERE id=?',('복구 확인 필요: '+reason,session))
            if status in {'completed', 'cancelled'}:
                db.execute('DELETE FROM request_checkpoints WHERE session_id=?', (session,))

    def recover(self):
        with self.store.transaction() as db:
            db.execute("UPDATE ai_requests SET status='interrupted' WHERE status='running'")
            for recording in db.execute("SELECT id FROM recordings WHERE status IN ('recording','recoverable')").fetchall():
                if not db.execute("SELECT 1 FROM recovery_sessions WHERE owner=? AND kind='capture'",(recording['id'],)).fetchone():
                    db.execute('INSERT INTO recovery_sessions(id,owner,kind,status,created_at,message) VALUES (?,?,?,?,?,?)',
                        (uuid.uuid4().hex,recording['id'],'capture','waiting',self.store.now(),
                         '녹음 복구 대기 · 녹음했던 브라우저를 다시 열면 남은 조각을 전송합니다. 마이크가 꺼진 동안의 음성은 복구할 수 없습니다.'))
            rows = db.execute("SELECT id,owner,kind FROM recovery_sessions WHERE status='active'").fetchall()
            for row in rows:
                recording = db.execute('SELECT status,review_status FROM recordings WHERE id=?',(row['owner'],)).fetchone()
                if recording:
                    state = recording['review_status'] if row['kind']=='review' else recording['status']
                    if state in {'completed','cancelled','failed'}:
                        db.execute('UPDATE recovery_sessions SET status=?,message=? WHERE id=?',(state,'',row['id']))
                        if state!='failed':
                            db.execute('DELETE FROM request_checkpoints WHERE session_id=?',(row['id'],))
                        continue
                count = db.execute('SELECT COUNT(*) FROM request_checkpoints WHERE session_id=?', (row['id'],)).fetchone()[0]
                db.execute('UPDATE recovery_sessions SET message=?,acknowledged=0 WHERE id=?',
                           (f'앱 재시작으로 작업을 복구합니다. 완료된 AI 요청 {count}개를 재사용합니다.', row['id']))

    def capture_recovered(self, owner):
        message='저장된 녹음 조각을 복구해 최종 전사에 사용합니다. 마이크는 재개되지 않았으며, 수집하지 못한 음성은 실시간 초안 생략과 달리 복구할 수 없습니다.'
        with self.store.transaction() as db:
            row=db.execute("SELECT id FROM recovery_sessions WHERE owner=? AND kind='capture'",(owner,)).fetchone()
            if row:
                db.execute("UPDATE recovery_sessions SET status='completed',message=?,acknowledged=0 WHERE id=?",(message,row['id']))
            else:
                db.execute('INSERT INTO recovery_sessions(id,owner,kind,status,created_at,message) VALUES (?,?,?,?,?,?)',
                    (uuid.uuid4().hex,owner,'capture','completed',self.store.now(),message))

    def request(self, operation, config, kind, identity, token, audio_seconds=None, owner=''):
        from .ai_transport import ProviderError
        marker = _tracking.set(True)
        try:
            for attempt in range(2):
                try:
                    return self._request(operation, config, kind, identity, token, audio_seconds, owner)
                except ProviderError as error:
                    if attempt or not error.retryable:
                        raise
                    until = time.monotonic()+1
                    while time.monotonic()<until:
                        token.check()
                        time.sleep(.05)
        finally:
            _tracking.reset(marker)

    def _request(self, operation, config, kind, identity, token, audio_seconds=None, owner=''):
        scope = _scope.get()
        session, owner = (scope[1], scope[2]) if scope and scope[0] is self else (None, owner)
        assets={}
        if config.get('managed'):
            for name in ('model_path','projector_path','vad_path'):
                path=Path(config.get(name,''))
                if path.is_file():
                    info=path.stat();assets[name]=[info.st_size,info.st_mtime_ns]
        key = fingerprint([VERSION, config, assets, identity])
        if session:
            from .services import current_services
            control = getattr(current_services(), 'ai_control', None)
            with (control.condition if control else nullcontext()), self.store.transaction() as db:
                saved = db.execute('SELECT result,seconds FROM request_checkpoints WHERE session_id=? AND fingerprint=?', (session, key)).fetchone()
            if saved:
                token.check()
                current_trace().setdefault('reused_requests', 0)
                current_trace()['reused_requests'] += 1
                current_trace()['reused_seconds'] = current_trace().get('reused_seconds',0)+saved['seconds']
                result = json.loads(saved['result'])
                if kind == 'llm':
                    result.update(seconds=0, waiting_seconds=0, input_tokens=None, output_tokens=None, reused=True)
                return result
        request_id = uuid.uuid4().hex
        location = 'local' if config.get('managed') else 'custom' if config['provider'] in {'llama_cpp', 'whisper_cpp'} else 'external'
        started, before = time.monotonic(), current_trace()['waiting_seconds']
        with self.store.transaction() as db:
            db.execute('INSERT INTO ai_requests(id,session_id,owner,kind,provider,model,location,started_at,status,audio_seconds) VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (request_id, session, owner, kind, config['provider'], config['model'], location,
                        datetime.now(timezone.utc).isoformat(), 'running', audio_seconds))
            db.execute('UPDATE ai_requests SET source_id=id WHERE id=?',(request_id,))
        status, result = 'failed', None
        try:
            result = operation(token)
            token.check()
            status = 'completed'
            # Result and request completion commit together; restart never counts a cache hit as a call.
            return result
        finally:
            if token.cancelled:
                status = 'cancelled'
            seconds = max(0, time.monotonic()-started-(current_trace()['waiting_seconds']-before))
            from .services import current_services
            control = getattr(current_services(), 'ai_control', None)
            with (control.condition if control else nullcontext()), self.store.transaction() as db:
                if token.cancelled:
                    status = 'cancelled'
                db.execute('UPDATE ai_requests SET status=?,completed_at=?,seconds=?,input_tokens=?,output_tokens=? WHERE id=?',
                           (status, datetime.now(timezone.utc).isoformat(), seconds,
                            result.get('input_tokens') if result else None,
                            result.get('output_tokens') if result else None, request_id))
                if session and status == 'completed':
                    db.execute('INSERT OR REPLACE INTO request_checkpoints(session_id,fingerprint,result,seconds) VALUES (?,?,?,?)',
                               (session, key, json.dumps(result, ensure_ascii=False),seconds))


def request(operation, config, kind, identity, token, audio_seconds=None, owner=''):
    from .services import current_services
    services = current_services()
    history = getattr(services, 'history', None)
    if history:
        return history.request(operation, config, kind, identity, token, audio_seconds, owner)
    return operation(token)


@contextmanager
def execution(owner, kind):
    from .services import current_services
    history = getattr(current_services(), 'history', None)
    if history:
        with history.scope(owner, kind) as session:
            yield lambda status: history.finish(session, status)
    else:
        yield lambda status: None
