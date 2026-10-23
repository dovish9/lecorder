#!/usr/bin/env python3
"""Inspect a dataset read-only or explicitly initialize a separate empty environment."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
from contextlib import closing

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))


def inspect(path):
    if not path.is_file():raise ValueError('DB 파일이 없습니다.')
    with closing(sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        names={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        result={'database':str(path.resolve()),'schema':db.execute('PRAGMA user_version').fetchone()[0],
                'managed':'schema_migrations' in names,'integrity':db.execute('PRAGMA quick_check').fetchone()[0]}
        if result['managed']:
            result['migrations']=[dict(zip(('version','name','checksum','applied_at'),row)) for row in db.execute('SELECT * FROM schema_migrations ORDER BY version')]
            identity=db.execute("SELECT value FROM app_state WHERE key='dataset_id'").fetchone()
            result['dataset_id']=identity[0] if identity else None
        return result


def initialize(data, output):
    data=data.resolve();output=output.resolve()
    if data==output or data.is_relative_to(output) or output.is_relative_to(data):
        raise ValueError('데이터 경로와 출력 경로는 서로 분리하세요.')
    for path in (data,output):
        if path.exists() and (not path.is_dir() or any(path.iterdir())):
            raise ValueError(f'비어 있는 새 경로가 필요합니다: {path}')
    from web.backend.library.store import LectureStore
    from web.backend.config import EnvironmentStore
    from web.backend.ai_settings import defaults
    from web.backend.runtime_config import template
    from web.backend.atomic import write_text
    data.mkdir(parents=True,exist_ok=True);output.mkdir(parents=True,exist_ok=True)
    env_path=data/'environment.env'
    values={'LECORDER_DIR':str(ROOT),'LECORDER_ENV_FILE':str(env_path),
            'LECORDER_DATA_DIR':str(data),'LECORDER_OUTPUT_DIR':str(output),'LECORDER_AI_SETTINGS':str(data/'ai-settings.json')}
    # Nothing is written to the production .env or its settings/secure store.
    write_text(env_path,template(values));env_path.chmod(0o600)
    write_text(data/'ai-settings.json',json.dumps({'settings':defaults(environment=False)},ensure_ascii=False,indent=2))
    store=LectureStore(data/'lecorder.db',EnvironmentStore(env_path,ROOT))
    result={'dataset_id':store.dataset_id,'environment':str(env_path),'versions':__import__('web.backend.versions',fromlist=['public_versions']).public_versions(),
            'courses':len(store.courses.list_courses()),'recordings':len(store.list_recordings()),
            'launch':f'LECORDER_ENV_FILE={__import__("shlex").quote(str(env_path))} ./start.command'}
    store.close()
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__);commands=parser.add_subparsers(dest='command',required=True)
    status=commands.add_parser('status');status.add_argument('database',type=Path)
    init=commands.add_parser('init');init.add_argument('--data-dir',type=Path,required=True);init.add_argument('--output-dir',type=Path,required=True)
    upgrade=commands.add_parser('migrate');upgrade.add_argument('database',type=Path)
    args=parser.parse_args()
    try:
        if args.command=='status':result=inspect(args.database)
        elif args.command=='init':result=initialize(args.data_dir,args.output_dir)
        else:
            if not args.database.is_file():raise ValueError('DB 파일이 없습니다.')
            from web.backend.library.store import LectureStore
            store=LectureStore(args.database);store.close();result=inspect(args.database)
        print(json.dumps(result,ensure_ascii=False,indent=2))
    except (ValueError,RuntimeError,sqlite3.Error) as error:parser.exit(1,str(error)+'\n')

if __name__=='__main__':main()
