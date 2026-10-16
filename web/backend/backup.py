"""Versioned, verified archive transfer. Import never overwrites existing files."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path, PurePosixPath
import shutil
import stat
import threading
import time
import uuid
import zipfile
import re

from .atomic import write_text
from .files import safe_name

TABLES = ('courses', 'lecture_notes', 'note_revisions', 'note_pages', 'note_details',
          'recordings', 'recording_chunks', 'suggestions', 'transcription_runs', 'review_runs',
          'recovery_sessions', 'request_checkpoints', 'ai_requests')


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def clean_settings(value):
    if isinstance(value, dict):
        return {k: clean_settings(v) for k,v in value.items()
                if k not in {'api_key', 'key', 'authorization', 'credential_ref'}}
    if isinstance(value, list):
        return [clean_settings(v) for v in value]
    return value


class Backups:
    def __init__(self, services, store):
        self.services, self.store = services, store
        self.folder = store.path.parent / 'transfers'
        self.folder.mkdir(exist_ok=True)
        self.lock = threading.RLock()
        self.condition = threading.Condition(self.lock)
        self.mutating_requests = 0
        self.maintenance = False
        self.jobs = {}
        self.threads = []
        self.closed = False
        self.recover_imports()

    def recover_imports(self):
        for path in self.folder.glob('restore-*.json'):
            journal=json.loads(path.read_text())
            with self.store.transaction() as db:
                committed=db.execute('SELECT 1 FROM app_state WHERE key=?',(journal['key'],)).fetchone()
            if not committed:
                for filename in journal['created']:
                    Path(filename).unlink(missing_ok=True)
            path.unlink()
            with self.store.transaction() as db:
                db.execute('DELETE FROM app_state WHERE key=?',(journal['key'],))

    def close(self):
        self.closed = True
        for job in list(self.jobs.values()):
            if job['state'] == 'running': job['cancelled'] = True
        for thread in self.threads:
            thread.join()

    def begin_request(self):
        with self.condition:
            if self.maintenance:
                return False
            self.mutating_requests += 1
            return True

    def end_request(self):
        with self.condition:
            self.mutating_requests -= 1
            self.condition.notify_all()

    @contextmanager
    def consistent(self, job):
        workflow = self.services.workflow
        control = self.services.ai_control
        with self.condition:
            if self.maintenance:
                raise ValueError('다른 백업·복원 작업이 진행 중입니다.')
            self.maintenance = True
        previous = control.public()['enabled']
        try:
            with self.condition:
                while self.mutating_requests:
                    self.check(job)
                    self.condition.wait(.1)
            with self.store.transaction() as db:
                if db.execute("SELECT 1 FROM recordings WHERE status='recording' LIMIT 1").fetchone():
                    raise ValueError('녹음과 업로드를 마친 뒤 다시 시도하세요.')
            control.set_enabled(False)
            deadline = time.monotonic()+60
            while control.public()['state'] == 'pausing':
                self.check(job)
                if time.monotonic() > deadline:
                    raise ValueError('AI 요청 정리를 기다리는 중입니다. 잠시 후 다시 시도하세요.')
                time.sleep(.1)
            with workflow.output_transaction(), workflow.notes.access(), self.store._lock:
                yield
        finally:
            if not control.closed:
                control.set_enabled(previous)
            with self.condition:
                self.maintenance = False
                self.condition.notify_all()

    @staticmethod
    def check(job):
        if job.get('cancelled'):
            raise ValueError('작업을 취소했습니다.')

    def start(self, operation, **kwargs):
        if self.closed:
            raise ValueError('앱이 종료 중입니다.')
        from .services import start_thread
        job = {'id': uuid.uuid4().hex, 'operation': operation, 'state': 'running', 'message': '', 'cancelled': False}
        self.jobs[job['id']] = job
        def work():
            try:
                getattr(self, operation)(job, **kwargs)
                job['state'] = 'ready' if operation == 'inspect' else 'completed'
            except Exception as error:
                job.update(state='cancelled' if job['cancelled'] else 'failed', message=str(error))
        self.threads.append(start_thread(work, 'backup-'+operation))
        return job

    def export(self, job):
        path = self.folder / (job['id']+'.zip')
        temporary = path.with_suffix('.partial')
        try:
            with self.consistent(job):
                with self.store.transaction() as db:
                    tables = {table: [dict(row) for row in db.execute(f'SELECT * FROM {table}')] for table in TABLES}
                files = {}
                def add(path, category, relative):
                    path = Path(path)
                    if path.is_symlink():
                        raise ValueError('자료 안의 심볼릭 링크는 백업할 수 없습니다.')
                    if not path.is_file():
                        raise ValueError(f'백업할 파일을 찾지 못했습니다: {path.name}')
                    files[str(path)] = {'category': category, 'relative': relative}
                for row in tables['recordings']:
                    for key in ('audio_path','note_path'):
                        if row[key]: add(row[key], 'output', Path(row[key]).name)
                    if row['source_path']:
                        source=Path(row['source_path'])
                        if source.is_file():
                            add(source, 'work', source.relative_to(self.services.workflow.work_root).as_posix())
                        elif row['status'] not in {'recording','recoverable'} or not any(self.services.workflow.folder(row['id']).glob('chunks/*.part')):
                            raise ValueError('원본 파일을 찾지 못했습니다: '+row['title'])
                for category, root in [('notes', self.services.workflow.notes.folder), ('work', self.services.workflow.work_root)]:
                    for source in root.rglob('*'):
                        if source.is_file():
                            # Decoder temporary wave files can be reproduced and aren't recovery source data.
                            if category == 'work' and (source.name in {'converted-audio.wav','live-window.wav'} or source.suffix in {'.upload','.assembling'} or 'whisper-chunks-' in str(source)):
                                continue
                            add(source, category, source.relative_to(root).as_posix())
                manifest = {'version': 1, 'created_at': self.store.now(), 'tables': tables, 'files': files}
                for rows in tables.values():
                    for row in rows:
                        for key in ('ai_settings_json','ai_settings','settings'):
                            if key in row:
                                row[key] = json.dumps(clean_settings(json.loads(row[key] or '{}')), ensure_ascii=False)
                stored = set()
                with zipfile.ZipFile(temporary, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as archive:
                    for index, (source, entry) in enumerate(files.items()):
                        self.check(job)
                        job['message'] = f'파일 {index+1}/{len(files)} 백업 중'
                        entry.update(sha256=digest(source), size=Path(source).stat().st_size)
                        if entry['sha256'] not in stored:
                            check=hashlib.sha256()
                            with Path(source).open('rb') as incoming,archive.open('files/'+entry['sha256'],'w',force_zip64=True) as outgoing:
                                while chunk:=incoming.read(1024*1024):
                                    self.check(job);check.update(chunk);outgoing.write(chunk)
                            if check.hexdigest()!=entry['sha256']:
                                raise ValueError('백업 도중 파일이 변경됐습니다. 작업이 끝난 뒤 다시 시도하세요.')
                            stored.add(entry['sha256'])
                    archive.writestr('manifest.json', json.dumps(manifest, ensure_ascii=False))
                self.check(job)
                temporary.replace(path)
                job.update(file=path.name, message='백업 완료')
        finally:
            temporary.unlink(missing_ok=True)

    def inspect(self, job, path):
        with zipfile.ZipFile(path) as archive:
            names = set()
            for info in archive.infolist():
                parts = PurePosixPath(info.filename)
                if info.filename in names or parts.is_absolute() or '..' in parts.parts or '\\' in info.filename or stat.S_ISLNK(info.external_attr >> 16):
                    raise ValueError('안전하지 않은 백업 파일 경로입니다.')
                names.add(info.filename)
            if archive.getinfo('manifest.json').file_size > 128*1024*1024:
                raise ValueError('백업 목록이 너무 큽니다.')
            manifest = json.loads(archive.read('manifest.json'))
            if manifest.get('version') != 1 or set(manifest.get('tables', {})) != set(TABLES):
                raise ValueError('지원하지 않는 백업 형식입니다.')
            with self.store.transaction() as db:
                for table, rows in manifest['tables'].items():
                    columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                    if table == 'note_revisions' and isinstance(rows,list):
                        for row in rows:
                            if isinstance(row,dict): row.setdefault('glossary_data','{}')
                    if not isinstance(rows,list) or any(not isinstance(r,dict) or set(r)!=columns for r in rows):
                        raise ValueError('백업 데이터 구조가 맞지 않습니다.')
            for row in manifest['tables']['recordings']:
                title = row['title']
                if not isinstance(title,str) or not title.strip() or any(c in title for c in '/\\\0') or title in {'.','..'}:
                    raise ValueError('녹음 이름에 안전하지 않은 경로 문자가 있습니다.')
                for key in ('source_path','audio_path','note_path'):
                    if row[key] and row[key] not in manifest['files']:
                        chunks=any(e['category']=='work' and e['relative'].startswith(row['id']+'/chunks/') for e in manifest['files'].values())
                        if key!='source_path' or row['status'] not in {'recording','recoverable'} or not chunks:
                            raise ValueError('녹음에 연결된 파일이 백업에 없습니다.')
            for row in manifest['tables']['lecture_notes']:
                for name in json.loads(row['originals']):
                    if Path(name).name != name or name in {'.','..'} or '\\' in name:
                        raise ValueError('노트 원본 파일명이 올바르지 않습니다.')
            required = sum(item['size'] for item in manifest['files'].values())
            if required > shutil.disk_usage(self.folder).free:
                raise ValueError('복원에 필요한 디스크 공간이 부족합니다.')
            output=Path(self.store.environment.get().output_dir)
            output_required=sum(item['size'] for item in manifest['files'].values() if item['category']=='output')
            if output_required>shutil.disk_usage(output).free:
                raise ValueError('결과 폴더에 필요한 디스크 공간이 부족합니다.')
            for entry in manifest['files'].values():
                self.check(job)
                relative = PurePosixPath(entry['relative'])
                if not relative.parts or entry['category'] not in {'output','notes','work'} or relative.is_absolute() or '..' in relative.parts or '\\' in entry['relative'] or ':' in entry['relative'] or '\0' in entry['relative']:
                    raise ValueError('안전하지 않은 복원 경로입니다.')
                if not re.fullmatch(r'[0-9a-f]{64}', entry['sha256']) or type(entry['size']) is not int or entry['size'] < 0:
                    raise ValueError('파일 검증 정보가 올바르지 않습니다.')
                info = archive.getinfo('files/'+entry['sha256'])
                if info.file_size != entry['size']:
                    raise ValueError('백업 파일 크기가 일치하지 않습니다.')
                with archive.open(info) as source:
                    if hashlib.file_digest(source, 'sha256').hexdigest() != entry['sha256']:
                        raise ValueError('백업 파일 체크섬이 일치하지 않습니다.')
            job.update(source=str(path), summary={table:len(rows) for table,rows in manifest['tables'].items()},
                       bytes=required, message='검사 완료 · 기존 이름과 겹치면 숫자를 붙여 별도 사본으로 추가합니다.')
            with self.store.transaction() as db:
                existing = {r[0].casefold() for r in db.execute('SELECT title FROM recordings')}
            job['collisions'] = [r['title'] for r in manifest['tables']['recordings'] if r['title'].casefold() in existing]

    def restore(self, job, inspected):
        self.check(inspected)
        if inspected['state'] != 'ready':
            raise ValueError('먼저 백업 검사를 완료하세요.')
        created = []
        committed = False
        marker='restore-'+uuid.uuid4().hex
        journal=self.folder/(marker+'.json')
        resume_recordings, resume_reviews, resume_notes = [], [], []
        try:
            with self.consistent(job), zipfile.ZipFile(inspected['source']) as archive, self.store.transaction() as db:
                manifest = json.loads(archive.read('manifest.json'))
                tables = manifest['tables']
                ids = {}
                for table in ('lecture_notes','note_revisions','recordings','transcription_runs','review_runs','recovery_sessions','ai_requests'):
                    ids.update({row['id']: uuid.uuid4().hex for row in tables[table]})
                courses = {}
                def unique(name, used):
                    name = safe_name(name) or '가져온 자료'
                    candidate, index = name, 2
                    while candidate.casefold() in used:
                        candidate = f'{name} ({index})'; index += 1
                    used.add(candidate.casefold())
                    return candidate
                course_names = {r[0].casefold() for r in db.execute('SELECT name FROM courses')}
                for row in tables['courses']:
                    old = row['id']; values = {k:v for k,v in row.items() if k != 'id'}
                    values['name'] = unique(values['name'], course_names)
                    cursor = db.execute(f'INSERT INTO courses({",".join(values)}) VALUES ({",".join("?" for _ in values)})', list(values.values()))
                    courses[old] = (cursor.lastrowid, values['name'])
                output = Path(self.store.environment.get().output_dir)
                output.mkdir(parents=True, exist_ok=True)
                used = {r[0].casefold() for r in db.execute('SELECT title FROM recordings')}
                used.update(p.stem.casefold() for p in output.iterdir())
                paths = {}
                note_names = {r[0].casefold() for r in db.execute('SELECT title FROM lecture_notes WHERE deleted=0')}
                for row in tables['lecture_notes']:
                    row['title'] = unique(row['title'], note_names)
                for row in tables['recordings']:
                    row['title'] = unique(row['title'], used)
                    for key in ('audio_path','note_path'):
                        if row[key]: paths[row[key]] = str(output / (row['title']+Path(row[key]).suffix))
                    row['output_dir'] = str(output)
                    if row['source_path'] and row['source_path'] not in manifest['files']:
                        paths[row['source_path']]=str(self.services.workflow.work_root/ids[row['id']]/(safe_name(Path(row['source_path']).name) or 'source.webm'))
                for old, entry in manifest['files'].items():
                    if old in paths: continue
                    pieces = [ids.get(p, p) for p in PurePosixPath(entry['relative']).parts]
                    root = self.services.workflow.notes.folder if entry['category']=='notes' else self.services.workflow.work_root
                    if entry['category'] == 'output':
                        raise ValueError('녹음과 연결되지 않은 결과 파일입니다.')
                    paths[old] = str(root.joinpath(*pieces))
                def remap(value):
                    if isinstance(value, str):
                        return paths.get(value, ids.get(value, value))
                    if isinstance(value, list): return [remap(v) for v in value]
                    if isinstance(value, dict): return {k:remap(v) for k,v in value.items()}
                    return value
                for old, entry in manifest['files'].items():
                    self.check(job)
                    destination = Path(paths[old])
                    # Check existing ancestors as well as ZIP entries: a local symlink
                    # must never redirect imported data outside our managed roots.
                    root = output if entry['category']=='output' else self.services.workflow.notes.folder if entry['category']=='notes' else self.services.workflow.work_root
                    if not destination.resolve().is_relative_to(root.resolve()) or any(p.is_symlink() for p in [destination,*destination.parents] if p.is_relative_to(root)):
                        raise ValueError('복원 대상 경로가 안전하지 않습니다.')
                    if destination.exists(): raise ValueError('복원 대상 파일이 이미 존재합니다.')
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    created.append(destination)
                    write_text(journal,json.dumps({'key':marker,'created':[str(p) for p in created]}))
                    with archive.open('files/'+entry['sha256']) as source, destination.open('xb') as target:
                        while chunk := source.read(1024*1024):
                            self.check(job)
                            target.write(chunk)
                    if digest(destination) != entry['sha256']: raise ValueError('복원 파일 검증 실패')
                for table in TABLES[1:]:
                    columns = {r[1] for r in db.execute(f'PRAGMA table_info({table})')}
                    for original in tables[table]:
                        self.check(job)
                        row = remap(original)
                        if table=='ai_requests': row['source_id']=original['source_id'] or original['id']
                        if set(row) - columns: raise ValueError('백업 데이터 버전이 맞지 않습니다.')
                        if 'course_id' in row:
                            course = courses.get(original['course_id'])
                            row['course_id'] = course[0] if course else None
                            if 'course_name' in row: row['course_name'] = course[1] if course else '선택 안 함'
                        for key, value in list(row.items()):
                            if isinstance(value,str) and (key.endswith('_json') or key in {'payload','settings','metrics','ai_settings','result'}):
                                row[key] = json.dumps(remap(json.loads(value)), ensure_ascii=False)
                        if table=='note_details':
                            detail=json.loads(row['payload'])
                            if 'ai_settings' in detail:
                                detail['ai_settings'],detail_reason=self.adapt_snapshot(detail['ai_settings'])
                            else:
                                revision=next(r for r in tables['note_revisions'] if ids[r['id']]==row['revision_id'])
                                detail['ai_settings'],detail_reason=self.adapt_snapshot(json.loads(revision['ai_settings'] or '{}'))
                            if detail.get('detail_status') in {'pending','analyzing'}:
                                detail['detail_status']='failed' if detail_reason else 'pending'
                                if detail_reason:detail['analysis_error']='복구 확인 필요: '+detail_reason
                                else:resume_notes.append((row['revision_id'],row['number']))
                            row['payload']=json.dumps(detail,ensure_ascii=False)
                        if table == 'suggestions': row.pop('id',None)
                        if table in {'recordings','note_revisions'}:
                            key = 'ai_settings_json' if table=='recordings' else 'ai_settings'
                            adapted, reason = self.adapt_snapshot(json.loads(row[key] or '{}'))
                            row[key] = json.dumps(adapted, ensure_ascii=False)
                        if table == 'recordings' and row['status'] in {'processing','queued'}:
                            reason = reason or ('' if Path(row['source_path']).is_file() else '원본 파일을 찾을 수 없습니다.')
                            row.update(status='failed' if reason else 'queued', error='복구 확인 필요: '+reason if reason else '백업에서 가져온 작업을 이어갑니다.')
                            if not reason: resume_recordings.append(row['id'])
                        if table == 'recordings' and row['review_status'] in {'processing','queued'}:
                            row.update(review_status='failed' if reason else 'queued', review_error='복구 확인 필요: '+reason if reason else '')
                            if not reason: resume_reviews.append(row['id'])
                        if table == 'note_revisions':
                            if row['status'] in {'extracting','queued'}:
                                row['status']='failed' if reason else 'queued'
                                if not reason: resume_notes.append((row['id'], 'extract'))
                            elif row['study_status'] in {'pending','analyzing'} and not reason:
                                resume_notes.append((row['id'], 'study'))
                            if row['study_status'] in {'pending','analyzing'}: row['study_status']='failed' if reason else 'pending'
                            if reason: row['error']='복구 확인 필요: '+reason
                        if table == 'ai_requests' and row['status']=='running': row['status']='interrupted'
                        db.execute(f'INSERT INTO {table}({",".join(row)}) VALUES ({",".join("?" for _ in row)})', list(row.values()))
                self.check(job)
                db.execute('INSERT INTO app_state(key,value) VALUES (?,?)',(marker,'committed'))
            committed = True
            journal.unlink(missing_ok=True)
            with self.store.transaction() as db:
                db.execute('DELETE FROM app_state WHERE key=?',(marker,))
            workflow = self.services.workflow
            if workflow._threads:
                for recording in resume_recordings: workflow.enqueue(recording)
                for recording in resume_reviews: workflow.reviews.enqueue(recording)
                for revision, kind in resume_notes:
                    (workflow.notes.extract_queue if kind=='extract' else workflow.notes.study_queue).put(revision if kind=='extract' else (revision,None if kind=='study' else kind))
            job['message'] = '가져오기 완료'
        except Exception:
            if not committed:
                for path in reversed(created): path.unlink(missing_ok=True)
                journal.unlink(missing_ok=True)
            raise

    def adapt_snapshot(self, snapshot):
        """Never execute a binary path supplied by an archive or silently switch models."""
        current = self.services.ai.snapshot()
        result, reasons = {}, []
        for area in ('speech','llm'):
            config = dict(snapshot.get(area) or current[area])
            if config.get('managed'):
                local = current[area]
                if not local.get('managed') or (config.get('provider'),config.get('model')) != (local['provider'],local['model']):
                    reasons.append(f'{area} 모델 연결을 확인하세요.')
                    # No archive-controlled executable can be used on a subsequent retry.
                    for key in ('binary','model_path','vad_path','projector_path'): config.pop(key,None)
                else:
                    for key in ('binary','model_path','vad_path','projector_path','base_url'):
                        if key in local: config[key]=local[key]
            try:
                config = self.services.ai.clean(area,config)
                if not config.get('managed') and config['provider'] not in {'whisper_cpp','llama_cpp'} and not self.services.ai.credentials.get(config):
                    reasons.append(f'{area} API 키를 등록하세요.')
            except (ValueError,RuntimeError):
                reasons.append(f'{area} 모델 파일과 서버 설정을 확인하세요.')
            result[area]=config
        return result, ' '.join(dict.fromkeys(reasons))
