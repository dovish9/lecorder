import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing

from web.backend.migrations import apply, migrations, MigrationError
from web.backend.library.store import LectureStore
from web.backend.result_codec import dumps, loads, ResultFormatError
from web.backend.versions import APP_VERSION, SCHEMA_VERSION

ROOT = Path(__file__).resolve().parents[2]

class MigrationTests(unittest.TestCase):
    def setUp(self):
        temporary=tempfile.TemporaryDirectory();self.addCleanup(temporary.cleanup)
        self.root=Path(temporary.name);self.path=self.root/'data.db'
        self.folder=self.root/'migrations';self.folder.mkdir()
        shutil.copy(ROOT/'web/backend/migrations/001_initial.sql',self.folder)

    def connect(self):
        return closing(sqlite3.connect(self.path))

    def test_new_database_is_empty_and_identity_survives_restart(self):
        store=LectureStore(self.path)
        self.assertEqual(store.courses.list_courses(),[])
        self.assertEqual(store.list_recordings(),[])
        self.assertEqual(store.courses.active_course_id(),0)
        self.assertEqual(store.get().course_name,'선택 안 함')
        self.assertEqual(LectureStore(self.path).dataset_id,store.dataset_id)
        with self.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],SCHEMA_VERSION)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],SCHEMA_VERSION)
        self.assertEqual(APP_VERSION,'0.3.0')

    def test_manual_hint_removal_preserves_recording_and_creates_snapshot(self):
        with self.connect() as db:
            apply(db,self.path,folder=self.folder)
            db.execute("INSERT INTO recordings(id,course_name,title,source_kind,extension,status,language,llm_model,output_dir,created_at,prompt,corrections) VALUES ('keep','course','title','upload','.wav','completed','ko','model','output','now','old hint','old correction')")
            db.commit()
        store=LectureStore(self.path)
        self.addCleanup(store.close)
        self.assertEqual(store.get_recording('keep').title,'title')
        with self.connect() as db:
            columns={row[1] for row in db.execute('PRAGMA table_info(recordings)')}
            self.assertFalse(columns & {'prompt','corrections'})
        self.assertEqual(len(list((self.root/'schema-backups').glob('*.db'))),1)

    def test_legacy_database_is_not_modified(self):
        with self.connect() as db:
            db.execute('CREATE TABLE recordings (id TEXT)');db.execute("INSERT INTO recordings VALUES ('keep')");db.commit()
        original=self.path.read_bytes()
        with self.assertRaisesRegex(MigrationError,'이전 데이터'):LectureStore(self.path)
        self.assertEqual(self.path.read_bytes(),original)

    def test_upgrade_has_verified_snapshot_and_applies_once(self):
        with self.connect() as db:
            apply(db,self.path,folder=self.folder)
            identity=db.execute("SELECT value FROM app_state WHERE key='dataset_id'").fetchone()[0]
            (self.folder/'002_test.sql').write_text('CREATE TABLE test_marker (id INTEGER);\nINSERT INTO test_marker VALUES (42);\n')
            apply(db,self.path,folder=self.folder);apply(db,self.path,folder=self.folder)
            self.assertEqual(db.execute('SELECT * FROM test_marker').fetchall(),[(42,)])
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],2)
            self.assertEqual(db.execute("SELECT value FROM app_state WHERE key='dataset_id'").fetchone()[0],identity)
        snapshots=list((self.root/'schema-backups').glob('*.db'));self.assertEqual(len(snapshots),1)
        with closing(sqlite3.connect(snapshots[0])) as saved:
            self.assertEqual(saved.execute('PRAGMA integrity_check').fetchone()[0],'ok')
            self.assertEqual(saved.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertIsNone(saved.execute("SELECT name FROM sqlite_master WHERE name='test_marker'").fetchone())

    def test_failed_upgrade_rolls_back_ddl_data_and_history(self):
        with self.connect() as db:
            apply(db,self.path,folder=self.folder)
            (self.folder/'002_fail.sql').write_text("CREATE TABLE partial (id INTEGER);\nUPDATE app_state SET value='changed';\nINSERT INTO missing VALUES (1);\n")
            with self.assertRaises(sqlite3.Error):apply(db,self.path,folder=self.folder)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())
            self.assertNotEqual(db.execute("SELECT value FROM app_state WHERE key='dataset_id'").fetchone()[0],'changed')

    def test_changed_checksum_missing_history_and_future_version_are_rejected(self):
        with self.connect() as db:
            apply(db,self.path,folder=self.folder)
            file=self.folder/'001_initial.sql';original=file.read_text();file.write_text(original+'\n-- changed\n')
            with self.assertRaisesRegex(MigrationError,'체크섬'):apply(db,self.path,folder=self.folder)
            file.write_text(original)
            db.execute('PRAGMA user_version=99')
            with self.assertRaises(MigrationError):apply(db,self.path,folder=self.folder)
            db.execute('PRAGMA user_version=1');db.execute('DELETE FROM schema_migrations');db.commit()
            with self.assertRaises(MigrationError):apply(db,self.path,folder=self.folder)

    def test_other_process_cannot_open_live_dataset(self):
        store=LectureStore(self.path)
        program='from pathlib import Path;from web.backend.library.store import LectureStore;LectureStore(Path('+repr(str(self.path))+'))'
        result=subprocess.run([sys.executable,'-c',program],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertNotEqual(result.returncode,0);self.assertIn('다른 앱',result.stderr)
        store._finalize_lock()
        result=subprocess.run([sys.executable,'-c',program],cwd=ROOT,capture_output=True,text=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_process_death_leaves_previous_committed_database(self):
        with self.connect() as db:apply(db,self.path,folder=self.folder)
        program="import sqlite3,os;d=sqlite3.connect("+repr(str(self.path))+");d.execute('BEGIN IMMEDIATE');d.execute('CREATE TABLE unfinished (id INTEGER)');d.execute('PRAGMA user_version=2');os._exit(71)"
        result=subprocess.run([sys.executable,'-c',program],timeout=15)
        self.assertEqual(result.returncode,71)
        with self.connect() as db:
            apply(db,self.path,folder=self.folder)
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='unfinished'").fetchone())

    def test_known_version_documents_are_strict_and_roundtrip(self):
        for kind,value in [('segments',[{'start':1,'end':2,'text':'μ₀'}]),('note-page',{'analysis':{'markdown':'설명'}}),('approval',{'start':1,'end':2}),('checkpoint',{'result':{}})]:
            encoded=dumps(value,kind,'speech')
            self.assertEqual(loads(encoded,kind),value)
            document=json.loads(encoded);document['format_version']=99
            with self.assertRaises(ResultFormatError):loads(document,kind)
        with self.assertRaises(ResultFormatError):loads('{}','quality')
        with self.assertRaises(ResultFormatError):loads(dumps({},'quality','speech'),'note-page')

    def test_store_writes_envelopes_but_domain_records_keep_shapes(self):
        store=LectureStore(self.path)
        row=store.create_recording('id',store.get(),'title','upload','.wav')
        store.update_recording(row.id,segments_json=json.dumps([{'start':1,'end':2,'text':'hello'}]),quality_json='{"ok":true}')
        with self.connect() as db:
            segments,quality=db.execute('SELECT segments_json,quality_json FROM recordings').fetchone()
            self.assertEqual(json.loads(segments)['format'],'segments')
            self.assertEqual(loads(quality,'quality'),{'ok':True})
        self.assertEqual(json.loads(store.get_recording(row.id).quality_json),{'ok':True})
