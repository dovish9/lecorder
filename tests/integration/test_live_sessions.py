import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from tests.support import RecorderCase
from web.backend.routes import create_app
from web.backend.store import LectureStore
from web.backend.live_transcription import LiveSession


class LiveSessionContractTests(RecorderCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(); self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        env, _ = self.environment(root)
        self.store = LectureStore(root/'test.db', env)
        self.app = create_app(store=self.store)
        self.services = self.app.extensions['lecorder_services']
        self.addCleanup(self.services.close)
        self.workflow = self.services.workflow
        self.row = self.workflow.begin_chunked(self.store.get(), 'test', '.webm')
        self.client = self.app.test_client()

    def test_live_failure_does_not_block_original_chunks_and_generations_are_checked(self):
        with patch('web.backend.live_transcription.ModelRuntime', side_effect=RuntimeError('missing model')):
            response = self.client.post(f'/api/recordings/{self.row.id}/live', json={})
            self.assertEqual(response.status_code, 200)
            session = self.services.live.active
            self.assertTrue(session.stopped.wait(2))
        self.assertEqual(session.state, 'failed')
        self.workflow.save_chunk(self.row.id, 0, io.BytesIO(b'original audio'))
        self.assertEqual((self.workflow.folder(self.row.id)/'chunks/00000000.part').read_bytes(), b'original audio')
        self.assertEqual(self.store.get_recording(self.row.id).status, 'recording')
        prefix = f'/api/recordings/{self.row.id}/live'
        self.assertEqual(self.client.put(prefix+'/old/pcm?sequence=0&start=0',data=b'\0\0').status_code, 409)
        self.assertEqual(self.client.put(prefix+f'/{session.id}/pcm?sequence=0&start=0',data=b'\0'*64002).status_code, 413)
        self.assertEqual(self.client.get(prefix).json['live']['state'], 'failed')

    def test_draft_survives_final_failure_until_review_commits_or_recording_is_deleted(self):
        live = self.services.live
        session = LiveSession(live, self.row.id, {})
        session.state = 'stopped'; session.persist()
        row = self.store.update_recording(self.row.id, status='failed')
        self.assertTrue(live.has_draft(row))
        row = self.store.update_recording(self.row.id, status='completed', note_path='final.md', review_status='failed')
        self.assertTrue(live.has_draft(row))
        row = self.store.update_recording(self.row.id, review_status='completed')
        self.assertFalse(live.has_draft(row))
        self.assertFalse(live.folder(row.id).exists())

    def test_empty_or_incomplete_recovery_never_stops_active_live_session(self):
        live = Mock(); self.workflow.live = live
        for chunk in (None, 1):
            if chunk is not None:
                self.workflow.save_chunk(self.row.id, chunk, io.BytesIO(b'partial'))
            with self.assertRaises(ValueError):
                self.workflow.finalize_chunked(self.row.id)
            live.end.assert_not_called()
            self.assertEqual(self.store.get_recording(self.row.id).status, 'recording')

    def test_stop_reason_is_recorded_once(self):
        session = LiveSession(self.services.live, self.row.id, {})
        session.stop('pagehide'); session.stop('another')
        self.assertEqual([e['reason'] for e in session.events if e['event']=='stop_requested'], ['pagehide'])

    def test_original_finalization_stops_live_before_queueing(self):
        live = Mock(); self.workflow.live = live
        self.workflow.save_chunk(self.row.id, 0, io.BytesIO(b'original'))
        with patch.object(self.workflow, '_preserve_intake', side_effect=lambda row: row):
            self.workflow.finalize_chunked(self.row.id, 3)
        live.end.assert_called_once_with(self.row.id, reason="recording_finalized")
        self.assertEqual(Path(self.row.source_path).read_bytes(), b'original')
        priority, _, item = self.workflow._queue.get_nowait()
        self.assertEqual((priority,item), (0,self.row.id))

    def test_global_control_preserves_original_and_rejects_old_pcm_generation(self):
        control = self.services.ai_control
        self.assertEqual(self.client.post('/api/ai/control', json={'enabled': 'no'}).status_code, 400)
        self.assertIn(self.client.post('/api/ai/control', json={'enabled': False}).json['ai_control']['state'], ('paused', 'pausing'))
        self.assertFalse(self.client.get('/api/status').json['ai_control']['enabled'])
        self.workflow.save_chunk(self.row.id, 0, io.BytesIO(b'original while AI off'))
        session = LiveSession(self.services.live, self.row.id, {})
        session.push(0, 0, b'\0'*32000, 0)
        session.set_ai_enabled(False)
        session.push(1, 16000, b'\0'*32000, 0)
        self.assertEqual(session.pending_samples, 0)
        session.set_ai_enabled(True)
        session.push(2, 32000, b'\0'*32000, 0)
        self.assertEqual(session.pending_samples, 0)
        session.push(3, 48000, b'\0'*32000, session.input_generation)
        self.assertEqual(session.pending_samples, 16000)
        self.assertTrue(any(g['reason']=='AI 처리 일시정지' for g in session.gaps))
        self.assertEqual(self.client.post('/api/ai/control', json={'enabled': True}).status_code, 200)
        self.assertEqual((self.workflow.folder(self.row.id)/'chunks/00000000.part').read_bytes(), b'original while AI off')

    def test_live_settings_are_frozen_until_the_next_recording(self):
        settings=self.services.ai.snapshot()
        settings['speech'].update(live_model='selected-live',live_model_path='/test/live.bin',live_vad_path='/test/vad.bin')
        with patch.object(self.services.ai,'snapshot',return_value=settings), patch('web.backend.live_transcription.start_thread',return_value=Mock()):
            self.services.live.begin(self.row.id)
            session=self.services.live.active
            self.assertEqual(session.public()['model'],'selected-live')
            self.assertEqual(session.config['model_path'],'/test/live.bin')
            self.assertEqual(session.config['vad_path'],'/test/vad.bin')
            settings['speech']['live_model']='next-live'
            self.services.live.begin(self.row.id)
            self.assertEqual(session.config['model'],'selected-live')
            session.stop('client_stop')
            session.stopped.set()  # The worker is intentionally mocked in this snapshot test.
            row=self.workflow.begin_chunked(self.store.get(),'next','.webm')
            self.services.live.begin(row.id)
            self.assertEqual(self.services.live.active.config['model'],'next-live')
            self.services.live.active.stopped.set()

    def test_active_live_session_does_not_label_other_jobs_as_waiting(self):
        session = LiveSession(self.services.live, self.row.id, {})
        self.services.live.active = session
        session.stopped.set()  # No worker in this API-only fixture.
        waiting = self.workflow.begin_chunked(self.store.get(), 'ordinary', '.webm')
        self.store.update_recording(waiting.id, status='queued')
        with patch.object(session.stopped, 'is_set', return_value=False), patch.object(self.workflow.notes, 'queue_items', return_value=[{'stage':'overview','status':'processing','managed_llm':True}]):
            payload = self.client.get('/api/status').json
            self.assertNotIn('waiting_for_live', str(payload))
