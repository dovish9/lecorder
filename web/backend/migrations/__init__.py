"""Immutable, transactional SQLite migrations; no pre-0.3.0 conversion."""
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import os
import sqlite3
import threading
import uuid

from web.backend.versions import SCHEMA_VERSION

class MigrationError(RuntimeError):
    pass

_handles = {}
_guard = threading.RLock()

class DatasetLock:
    def __init__(self, path):
        self.key = str(Path(path).resolve())
        self.closed = False
        with _guard:
            current = _handles.get(self.key)
            if current and current[2] == os.getpid():
                current[1] += 1
                return
            handle = open(self.key + '.lock', 'a+b')
            try:
                if os.name == 'nt':
                    import msvcrt
                    handle.seek(0); handle.write(b'0'); handle.flush(); handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as error:
                handle.close()
                raise MigrationError('이 데이터 환경은 다른 앱에서 사용 중입니다.') from error
            _handles[self.key] = [handle, 1, os.getpid()]

    def close(self):
        with _guard:
            if self.closed: return
            self.closed = True
            handle, count, pid = _handles[self.key]
            if count > 1:
                _handles[self.key][1] -= 1
            else:
                handle.close(); del _handles[self.key]


def migrations(folder=None):
    files = sorted(Path(folder or Path(__file__).parent).glob('[0-9][0-9][0-9]_*.sql'))
    result = [(int(p.name[:3]), p.name, hashlib.sha256(p.read_bytes()).hexdigest(), p.read_text()) for p in files]
    if [r[0] for r in result] != list(range(1, len(result)+1)):
        raise MigrationError('마이그레이션 번호가 연속되지 않습니다.')
    return result


def statements(sql):
    pending = ''
    for line in sql.splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending; pending = ''
    if pending.strip():
        raise MigrationError('완결되지 않은 마이그레이션 SQL입니다.')


def apply(connection, path, *, folder=None):
    scripts = migrations(folder)
    if not scripts or (folder is None and scripts[-1][0] != SCHEMA_VERSION):
        raise MigrationError('배포 스키마 버전이 마이그레이션과 다릅니다.')
    tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
    version = connection.execute('PRAGMA user_version').fetchone()[0]
    if tables and 'schema_migrations' not in tables:
        raise MigrationError('0.3.0 이전 데이터 환경입니다. 기존 자료를 보존하고 별도의 빈 데이터 경로를 지정하세요.')
    applied = list(connection.execute('SELECT version,name,checksum FROM schema_migrations ORDER BY version')) if tables else []
    if (tables and not applied) or version != len(applied) or version > len(scripts):
        raise MigrationError('지원하지 않거나 일치하지 않는 DB 버전입니다.')
    for row, script in zip(applied, scripts):
        if tuple(row) != script[:3]:
            raise MigrationError('마이그레이션 이력 또는 체크섬이 다릅니다.')
    if applied:
        identity = connection.execute("SELECT value FROM app_state WHERE key='dataset_id'").fetchone()
        import re
        if not identity or not re.fullmatch('[a-f0-9]{32}',identity[0]):
            raise MigrationError('데이터 환경 ID가 올바르지 않습니다.')
    if version == len(scripts): return
    if version:
        directory = Path(path).parent / 'schema-backups'
        directory.mkdir(exist_ok=True)
        backup = directory / f'{Path(path).stem}-v{version}-{uuid.uuid4().hex}.db'
        with closing(sqlite3.connect(backup)) as target:
            connection.backup(target)
    connection.execute('PRAGMA foreign_keys=ON')
    connection.execute('BEGIN IMMEDIATE')
    try:
        if not applied:
            connection.execute('CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, checksum TEXT NOT NULL, applied_at TEXT NOT NULL)')
        for number, name, checksum, sql in scripts[version:]:
            for statement in statements(sql): connection.execute(statement)
            connection.execute('INSERT INTO schema_migrations VALUES (?,?,?,?)', (number,name,checksum,datetime.now(timezone.utc).isoformat()))
            connection.execute(f'PRAGMA user_version={number}')
        if not applied:
            connection.execute("INSERT INTO app_state VALUES ('dataset_id',?)", (uuid.uuid4().hex,))
        if connection.execute('PRAGMA foreign_key_check').fetchone():
            raise MigrationError('마이그레이션 후 참조 무결성 검사에 실패했습니다.')
        if connection.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise MigrationError('마이그레이션 후 DB 검사에 실패했습니다.')
        connection.commit()
    except BaseException:
        connection.rollback()
        raise
