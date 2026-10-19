import json
from unittest.mock import patch
import tempfile
from pathlib import Path
from tests.support import RecorderCase
from web.backend.store import LectureStore
from web.backend.routes import create_app


class ApprovalHistoryTests(RecorderCase):
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
        self.note = self.output/'물리학.md'; self.note.write_text('분수 $x/y$')
        self.store.create_recording('source', self.store.get(), '물리학', 'upload', '.wav', '', 'completed')
        self.store.update_recording('source', note_path=str(self.note),transcript_text='분수 $x/y$',
            segments_json=json.dumps([{'text':'분수 $x/y$', 'start':12, 'end':15}]))

    def archive(self):
        backup=self.services.backups
        job={'id':'fixture','cancelled':False}
        with self.services.scope():backup.export(job)
        return backup.folder/job['file']

    def seed(self):
        self.store.update_recording('source',review_status='completed')
        self.store.replace_suggestions('source',[dict(sentence_id='s00001',original='분수',replacement='비율',reason='문맥',confidence=.95)])
        return self.store.list_suggestions('source')[0]

    def test_accept_snapshot_survives_replacement_and_deletion(self):
        suggestion=self.seed()
        result=self.client.post(f'/api/suggestions/{suggestion.id}/decision',json={'action':'accept'})
        self.assertEqual(result.status_code,200,result.json)
        data=self.client.get('/api/suggestion-history').json
        self.assertEqual(data['total'],1)
        item=data['items'][0]
        self.assertEqual((item['start'],item['end']),(12,15))
        self.assertEqual(item['context_before'],'분수 $x/y$')
        self.assertEqual(item['context_after'],'비율 $x/y$')
        self.assertEqual(item['time_scope'],'utterance')
        self.assertNotIn('api_key',json.dumps(item))
        self.assertEqual(self.client.post(f'/api/suggestions/{suggestion.id}/decision',json={'action':'accept'}).status_code,400)
        self.store.replace_suggestions('source',[])
        self.store.delete_recording_history('source')
        self.assertEqual(self.store.approval_history()['items'],data['items'])

    def test_reject_and_failed_publication_create_no_history(self):
        suggestion=self.seed()
        self.client.post(f'/api/suggestions/{suggestion.id}/decision',json={'action':'reject'})
        self.assertEqual(self.store.approval_history()['total'],0)
        suggestion=self.seed()
        before=self.note.read_text()
        from pathlib import Path
        original=Path.replace
        def fail(source,target):
            if source.name.endswith('.review'):raise OSError('publication failed')
            return original(source,target)
        with patch.object(Path,'replace',fail),self.assertRaises(OSError):
            self.services.workflow.decide_suggestion(suggestion.id,'accept')
        self.assertEqual(self.store.get_suggestion(suggestion.id).status,'pending')
        self.assertEqual(self.store.get_recording('source').transcript_text,'분수 $x/y$')
        self.assertEqual(self.note.read_text(),before)
        self.assertEqual(self.store.approval_history()['total'],0)

    def test_history_roundtrip_and_pagination(self):
        suggestion=self.seed()
        self.services.workflow.decide_suggestion(suggestion.id,'accept')
        backup=self.services.backups
        inspected={'cancelled':False};backup.inspect(inspected,self.archive());inspected['state']='ready'
        with self.services.scope():backup.restore({'cancelled':False},inspected)
        data=self.client.get('/api/suggestion-history?limit=1&offset=1').json
        self.assertEqual(data['total'],2)
        self.assertEqual(len(data['items']),1)
        all_items=self.store.approval_history()['items']
        self.assertEqual(len({r['id'] for r in all_items}),2)
        self.assertEqual(len({r['recording_id'] for r in all_items}),2)
        self.assertEqual(self.client.get('/api/suggestion-history?limit=no').status_code,400)


    def test_model_snapshot_uses_proposing_run_and_never_keys(self):
        suggestion=self.seed()
        with self.store.transaction() as db:
            db.execute("INSERT INTO review_runs(id,recording_id,started_at,status,settings) VALUES (?,?,?,?,?)",
                ('review-before','source',suggestion.created_at,'completed',json.dumps({'ai_settings':{'llm':{'model':'review-model','provider':'llama_cpp','api_key':'private-test-key'}}})))
            db.execute("INSERT INTO review_runs(id,recording_id,started_at,status,settings) VALUES (?,?,?,?,?)",
                ('later-failed','source','2999-01-01','failed',json.dumps({'model':'wrong-model'})))
        self.services.workflow.decide_suggestion(suggestion.id,'accept')
        item=self.store.approval_history()['items'][0]
        self.assertEqual(item['model'],'review-model')
        self.assertEqual(item['run_id'],'review-before')
        self.assertNotIn('private-test-key',json.dumps(item))
        self.assertEqual(LectureStore(self.root/'test.db',self.store.environment).approval_history()['items'][0],item)

    def test_legacy_backup_without_approval_table(self):
        import zipfile
        source=self.archive()
        with zipfile.ZipFile(source) as archive:
            entries={name:archive.read(name) for name in archive.namelist()}
        manifest=json.loads(entries['manifest.json']);manifest['tables'].pop('suggestion_history')
        entries['manifest.json']=json.dumps(manifest).encode()
        old=self.root/'legacy.zip'
        with zipfile.ZipFile(old,'w') as archive:
            for name,data in entries.items():archive.writestr(name,data)
        backup=self.services.backups;inspected={'cancelled':False}
        backup.inspect(inspected,old);inspected['state']='ready'
        with self.services.scope():backup.restore({'cancelled':False},inspected)
        self.assertEqual(self.store.approval_history()['total'],0)
