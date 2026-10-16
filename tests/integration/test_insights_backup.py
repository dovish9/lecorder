import json
import tempfile
import zipfile
import io
from unittest.mock import patch
from pathlib import Path
from tests.support import RecorderCase
from web.backend.store import LectureStore
from web.backend.routes import create_app
from web.backend.processes import CancellationToken
from web.backend.execution_metrics import collect


class InsightsBackupTests(RecorderCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        env, self.output = self.environment(self.root)
        self.store = LectureStore(self.root/'test.db', env)
        self.app = create_app(store=self.store)
        self.services = self.app.extensions['lecorder_services']
        self.addCleanup(self.services.close)
        self.client = self.app.test_client()
        self.audio = self.output/'물리학.wav'; self.audio.write_bytes(b'original audio')
        self.note = self.output/'물리학.md'; self.note.write_text('분수 $x/y$')
        row = self.store.create_recording('source', self.store.get(), '물리학', 'upload', '.wav', '', 'completed')
        self.store.update_recording(row.id, audio_path=str(self.audio), note_path=str(self.note),
            transcript_text='분수 $x/y$', segments_json=json.dumps([{'text':'분수 $x/y$', 'start':12, 'end':15}]))

    def test_search_normalizes_and_updates_after_edit_and_delete(self):
        result = self.client.get('/api/search?q=분수').json
        self.assertEqual(result['results'][0]['seconds'], 12)
        self.assertEqual(self.client.get('/api/search?q=X%2FY').json['total'], 1)
        self.store.update_recording('source', transcript_text='바뀐 내용', segments_json='[]')
        self.assertEqual(self.client.get('/api/search?q=분수').json['total'], 0)
        self.assertEqual(self.client.get('/api/search?q=바뀐').json['total'], 1)
        self.assertEqual(self.client.get('/api/search?q=물리학&kind=note').json['total'], 0)

    def test_usage_preserves_unknown_and_reuse_is_not_double_counted(self):
        history = self.services.history
        config = {'provider':'llama_cpp','model':'test','managed':True}
        with collect(), history.scope('source','review'):
            for _ in range(2): history.request(lambda t:{'result':{}}, config,'llm','same',CancellationToken())
        group = self.client.get('/api/usage?days=0').json['groups'][0]
        self.assertEqual(group['requests'],1)
        self.assertIsNone(group['input_tokens'])
        self.assertEqual(group['unknown_usage'],1)

    def test_backup_roundtrip_merges_without_overwriting(self):
        backup = self.services.backups
        with collect(), self.services.history.scope('source','review'):
            self.services.history.request(lambda t:{'result':{}}, {'provider':'llama_cpp','model':'test','managed':True},'llm','same',CancellationToken())
        with self.services.scope():
            job={'id':'export','cancelled':False}; backup.export(job)
            inspected={'cancelled':False}; backup.inspect(inspected,backup.folder/job['file']); inspected['state']='ready'
            for _ in range(2): backup.restore({'cancelled':False},inspected)
        with self.store.transaction() as db:
            rows=db.execute('SELECT title,audio_path FROM recordings ORDER BY title').fetchall()
        self.assertEqual({r['title'] for r in rows},{'물리학','물리학 (2)','물리학 (3)'})
        self.assertTrue(all(Path(r['audio_path']).read_bytes()==b'original audio' for r in rows))
        self.assertEqual(self.audio.read_bytes(),b'original audio')
        self.assertEqual(self.client.get('/api/search?q=분수').json['total'],3)
        self.assertEqual(self.client.get('/api/usage?days=0').json['groups'][0]['requests'],1)

    def test_note_graph_and_selected_revision_survive_merge(self):
        notes=self.services.workflow.notes
        with patch('web.backend.note_intake.require_llm',return_value=None):
            note=notes.register(self.store.active_course_id(), [('slides.pdf',io.BytesIO(b'%PDF-1.7 fixture'))])
        revision=note['revision_id']
        notes._page(revision,{'number':1,'text':'Electric field','image':'page-1.png','error':'','analysis':{'summary':'설명'}})
        notes._update(revision,status='ready',study_status='completed',page_count=1,
            keywords=json.dumps([{'id':'g1','term':'μ₀','meaning':'귀무가설 모평균','spoken_forms':['뮤 제로'],'pages':[1],'included':True}]),
            glossary_data=json.dumps({'version':'glossary-v1','symbols':[{'id':'s1','term':'μ₀','pages':[1]}]}))
        self.assertEqual(self.client.get('/api/search?q=뮤%20제로').json['total'],1)
        self.store.update_recording('source',note_revision_id=revision)
        backup=self.services.backups
        inspected={'cancelled':False};backup.inspect(inspected,self.archive());inspected['state']='ready'
        with self.services.scope():backup.restore({'cancelled':False},inspected)
        with self.store.transaction() as db:
            copied=db.execute("SELECT * FROM recordings WHERE id!='source'").fetchone()
            linked=db.execute('SELECT * FROM note_revisions WHERE id=?',(copied['note_revision_id'],)).fetchone()
            self.assertNotEqual(linked['id'],revision)
            self.assertEqual(json.loads(linked['glossary_data'])['symbols'][0]['term'],'μ₀')
            self.assertEqual(json.loads(linked['keywords'])[0]['spoken_forms'],['뮤 제로'])
            self.assertEqual(db.execute('SELECT COUNT(*) FROM note_pages WHERE revision_id=?',(linked['id'],)).fetchone()[0],1)
            self.assertTrue((notes.folder/linked['note_id']).is_dir())

    def test_legacy_backup_without_glossary_column_still_restores(self):
        notes=self.services.workflow.notes
        with patch('web.backend.note_intake.require_llm',return_value=None):
            notes.register(self.store.active_course_id(), [('old.pdf',io.BytesIO(b'%PDF-1.7 fixture'))])
        original=self.archive()
        with zipfile.ZipFile(original) as archive:
            entries={name:archive.read(name) for name in archive.namelist()}
        manifest=json.loads(entries['manifest.json'])
        for row in manifest['tables']['note_revisions']: row.pop('glossary_data')
        entries['manifest.json']=json.dumps(manifest).encode()
        path=self.root/'legacy.zip'
        with zipfile.ZipFile(path,'w') as archive:
            for name,content in entries.items(): archive.writestr(name,content)
        inspected={'cancelled':False}
        self.services.backups.inspect(inspected,path);inspected['state']='ready'
        with self.services.scope(): self.services.backups.restore({'cancelled':False},inspected)
        with self.store.transaction() as db:
            self.assertTrue(all(row[0]=='{}' for row in db.execute('SELECT glossary_data FROM note_revisions')))

    def test_cancelled_inspection_cannot_restore_and_cross_origin_is_rejected(self):
        backup=self.services.backups
        backup.jobs['ready']={'id':'ready','state':'ready','operation':'inspect','cancelled':False}
        self.assertEqual(self.client.post('/api/backups/ready/cancel').status_code,200)
        self.assertEqual(self.client.post('/api/backups/ready/restore').status_code,409)
        self.assertEqual(self.client.post('/api/backups',headers={'Origin':'https://unrelated.example'}).status_code,403)

    def test_backup_excludes_credentials_and_usage_respects_local_midnight(self):
        self.store.update_recording('source',ai_settings_json=json.dumps({'llm':{'api_key':'SECRET-MARKER','credential_ref':'PRIVATE-REF'}}))
        with zipfile.ZipFile(self.archive()) as archive:
            manifest=archive.read('manifest.json')
        self.assertNotIn(b'SECRET-MARKER',manifest)
        self.assertNotIn(b'PRIVATE-REF',manifest)
        from datetime import datetime,timezone
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,16,1,tzinfo=timezone.utc).astimezone(tz)
        with self.store.transaction() as db:
            for index,started in enumerate(('2026-10-15T14:59:59+00:00','2026-10-15T15:00:00+00:00')):
                db.execute("INSERT INTO ai_requests(id,source_id,owner,kind,provider,model,location,started_at,status) VALUES (?,?,?,?,?,?,?,?,?)",(str(index),str(index),'source','speech','whisper_cpp','test','local',started,'interrupted'))
        with patch('web.backend.insights.datetime',Clock):
            group=self.services.insights.usage(1,540,'Asia/Seoul')['groups'][0]
        self.assertEqual(group['requests'],1)
        self.assertEqual(group['unknown_time'],1)

    def test_backup_during_recording_is_rejected_and_control_restored(self):
        self.store.update_recording('source',status='recording')
        with self.services.scope(), self.assertRaisesRegex(ValueError,'녹음'):
            self.services.backups.export({'id':'blocked','cancelled':False})
        self.assertFalse(self.services.backups.maintenance)
        self.assertTrue(self.services.ai_control.public()['enabled'])

    def archive(self):
        backup=self.services.backups
        job={'id':'fixture','cancelled':False}
        with self.services.scope(): backup.export(job)
        return backup.folder/job['file']

    def test_rejects_traversal_and_corrupted_content(self):
        original=self.archive()
        with zipfile.ZipFile(original) as archive:
            entries={name:archive.read(name) for name in archive.namelist()}
        for mode in ('path','hash','title'):
            with self.subTest(mode=mode):
                altered=dict(entries)
                if mode=='path': altered['../outside']=b'bad'
                elif mode=='hash': altered[next(n for n in altered if n.startswith('files/'))]=b'bad'
                else:
                    manifest=json.loads(altered['manifest.json'])
                    manifest['tables']['recordings'][0]['title']='../outside'
                    altered['manifest.json']=json.dumps(manifest).encode()
                path=self.root/(mode+'.zip')
                with zipfile.ZipFile(path,'w') as archive:
                    for name,content in altered.items():archive.writestr(name,content)
                with self.assertRaises(ValueError):self.services.backups.inspect({'cancelled':False},path)
        self.assertEqual(self.audio.read_bytes(),b'original audio')

    def test_import_failure_rolls_back_only_new_files(self):
        backup=self.services.backups
        inspected={'cancelled':False}
        backup.inspect(inspected,self.archive());inspected['state']='ready'
        existing=set(self.output.iterdir())
        with self.services.scope(),patch.object(backup,'adapt_snapshot',side_effect=ValueError('injected failure')):
            with self.assertRaisesRegex(ValueError,'injected'):backup.restore({'cancelled':False},inspected)
        self.assertEqual(set(self.output.iterdir()),existing)
        self.assertEqual(len(self.store.list_recordings()),1)
        self.assertTrue(self.services.ai_control.public()['enabled'])

    def test_cancel_and_space_checks(self):
        backup=self.services.backups
        with self.services.scope(),self.assertRaisesRegex(ValueError,'취소'):
            backup.export({'id':'cancelled','cancelled':True})
        from collections import namedtuple
        usage=namedtuple('Disk','total used free')(10,10,0)
        path=self.archive()
        with patch('web.backend.backup.shutil.disk_usage',return_value=usage),self.assertRaisesRegex(ValueError,'공간'):
            backup.inspect({'cancelled':False},path)

    def test_chunk_receipts_are_durable_and_conflicts_preserve_original(self):
        workflow=self.services.workflow
        row=workflow.begin_chunked(self.store.get(),'receipt','.webm')
        self.assertEqual(workflow.save_chunk(row.id,0,io.BytesIO(b'audio')),5)
        self.assertEqual(workflow.save_chunk(row.id,0,io.BytesIO(b'audio')),5)
        with self.assertRaisesRegex(ValueError,'다릅니다'):
            workflow.save_chunk(row.id,0,io.BytesIO(b'changed'))
        received=self.client.get(f'/api/recordings/{row.id}/chunks').json['chunks']
        self.assertEqual(len(received),1)
        self.assertEqual((workflow.folder(row.id)/'chunks/00000000.part').read_bytes(),b'audio')

    def test_publication_recovery_restores_before_commit_and_keeps_after_commit(self):
        from web.backend.publication import Publication,recover
        from dataclasses import replace
        row=self.store.get_recording('source')
        expected=replace(row,transcript_text='new')
        Publication(self.store,expected,[self.note])
        self.note.write_text('new file')
        recover(self.store)
        self.assertEqual(self.note.read_text(),'분수 $x/y$')
        Publication(self.store,expected,[self.note])
        self.note.write_text('new file')
        self.store.update_recording('source',transcript_text='new')
        recover(self.store)
        self.assertEqual(self.note.read_text(),'new file')
