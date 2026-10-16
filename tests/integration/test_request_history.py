import tempfile
from pathlib import Path
from tests.support import RecorderCase
from web.backend.store import LectureStore
from web.backend.request_history import RequestHistory
from web.backend.processes import CancellationToken, TranscriptionCancelled
from web.backend.execution_metrics import collect


class RequestHistoryTests(RecorderCase):
    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        env, _ = self.environment(root)
        self.store = LectureStore(root / 'test.db', env)
        self.history = RequestHistory(self.store)
        self.config = {'provider': 'llama_cpp', 'model': 'test', 'managed': True}

    def test_restart_reuses_completed_request_without_usage_duplication(self):
        calls = []
        def operation(token):
            calls.append(1)
            return {'result': {'text': '보존'}, 'input_tokens': 8, 'output_tokens': 3}
        with collect(), self.history.scope('job', 'review'):
            self.history.request(operation, self.config, 'llm', ['input'], CancellationToken())
        restarted = RequestHistory(self.store)
        with collect() as trace, restarted.scope('job', 'review'):
            result = restarted.request(operation, self.config, 'llm', ['input'], CancellationToken())
            self.assertEqual(trace['reused_requests'], 1)
            self.assertGreater(trace['reused_seconds'], 0)
            self.assertEqual(result['result']['text'], '보존')
        self.assertEqual(len(calls), 1)
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_requests').fetchone()[0], 1)

    def test_cancelled_request_and_changed_input_are_not_cached(self):
        def cancelled(token):
            token.cancel()
            token.check()
        with collect(), self.history.scope('job', 'review'):
            with self.assertRaises(TranscriptionCancelled):
                self.history.request(cancelled, self.config, 'llm', 'a', CancellationToken())
            self.history.request(lambda t: {'result': 1}, self.config, 'llm', 'a', CancellationToken())
            result = self.history.request(lambda t: {'result': 2}, self.config, 'llm', 'b', CancellationToken())
            self.assertEqual(result['result'], 2)
        with self.store.transaction() as db:
            self.assertEqual([r[0] for r in db.execute('SELECT status FROM ai_requests ORDER BY started_at')], ['cancelled', 'completed', 'completed'])

    def test_new_execution_after_completion_does_not_reuse_old_results(self):
        for _ in range(2):
            with collect(), self.history.scope('job', 'review') as session:
                self.history.request(lambda t: {'result': 1}, self.config, 'llm', 'a', CancellationToken())
                self.history.finish(session, 'completed')
        with self.store.transaction() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM ai_requests').fetchone()[0], 2)
            self.assertEqual(db.execute('SELECT COUNT(*) FROM request_checkpoints').fetchone()[0], 0)

    def test_connection_retry_counts_each_attempt_and_is_bounded(self):
        from web.backend.ai_transport import ProviderError
        from unittest.mock import patch
        calls=[]
        def failed(token):
            calls.append(1)
            raise ProviderError('timeout',retryable=True)
        with collect(),patch('web.backend.request_history.time.sleep'),self.assertRaises(ProviderError):
            self.history.request(failed,self.config,'llm','input',CancellationToken())
        self.assertEqual(len(calls),2)
        with self.store.transaction() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM ai_requests WHERE status='failed'").fetchone()[0],2)
