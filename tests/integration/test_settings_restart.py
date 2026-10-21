import json
import os
from unittest.mock import patch
from tests.integration import test_insights_backup as fixtures
from web.backend.settings_restart import rebind_pending


from tests.support import RecorderCase


class SettingsRestartTests(RecorderCase):
    def setUp(self):
        self.fixture=fixtures.InsightsBackupTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        for name in ("services","store","root","client","app"):
            setattr(self,name,getattr(self.fixture,name))
    def test_saved_settings_wait_for_restart_and_only_pending_work_rebinds(self):
        ai=self.services.ai
        old=ai.snapshot()
        new={**old['llm'], 'model':'changed-model'}
        with patch.object(ai,'clean',side_effect=lambda area,config:config):
            ai.save('llm',new)
        self.assertEqual(ai.snapshot(),old)
        self.assertEqual(ai.saved_snapshot()['llm']['model'],'changed-model')
        self.assertTrue(ai.public()['restart_required'])
        self.store.update_recording('source',review_status='queued',ai_settings_json=json.dumps(old))
        rebind_pending(self.store,ai.saved_snapshot())
        row=self.store.get_recording('source')
        self.assertEqual(json.loads(row.ai_settings_json)['llm']['model'],'changed-model')
        self.assertEqual(row.transcript_text,'분수 $x/y$')
        self.assertEqual(json.loads(row.ai_settings_json)['speech'],old['speech'])

    def test_path_save_does_not_restart_or_change_runtime_paths(self):
        original=self.store.environment.get()
        target=self.root/'other-output';target.mkdir()
        with patch('web.backend.routes.schedule_managed_restart') as restart:
            response=self.client.put('/api/environment',json={'output_dir':str(target)})
        self.assertEqual(response.status_code,200)
        restart.assert_not_called()
        self.assertEqual(self.store.environment.get(),original)
        self.assertEqual(self.store.environment.saved().output_dir,str(target.resolve()))

    def test_restart_is_explicit_rejects_recording_and_blocks_new_writes(self):
        with patch.dict(os.environ,{'LECORDER_MANAGED_RESTART':'1'}), patch('web.backend.routes.schedule_managed_restart',return_value=True) as restart:
            self.store.update_recording('source',status='recording')
            self.assertEqual(self.client.post('/api/restart',json={}).status_code,409)
            restart.assert_not_called()
            self.store.update_recording('source',status='completed')
            before=self.client.get('/api/bootstrap').json['instance_id']
            response=self.client.post('/api/restart',json={})
            self.assertEqual(response.status_code,202)
            self.assertEqual(response.json['instance_id'],before)
            restart.assert_called_once()
            self.assertEqual(self.client.post('/api/restart',json={}).status_code,503)

    def test_unmanaged_restart_is_explicit_failure(self):
        with patch.dict(os.environ,{'LECORDER_MANAGED_RESTART':'0'}):
            self.assertEqual(self.client.post('/api/restart',json={}).status_code,409)
