import threading
import time
import unittest
from web.backend.ai_control import AIControl, RequestToken
from web.backend.processes import CancellationToken, TranscriptionCancelled
from web.backend.execution_metrics import collect


class AIControlTests(unittest.TestCase):
    def setUp(self):
        self.control = AIControl()
        self.addCleanup(self.control.close)

    def launch(self, fn):
        results, errors = [], []
        def work():
            try: results.append(fn())
            except Exception as error: errors.append(error)
        thread = threading.Thread(target=work); thread.start()
        self.addCleanup(thread.join, 2)
        return thread, results, errors

    def test_off_blocks_and_resume_runs_once(self):
        self.control.set_enabled(False)
        started = threading.Event()
        thread, result, errors = self.launch(lambda: self.control.run(lambda t: started.set() or 42))
        self.assertFalse(started.wait(.1))
        self.control.set_enabled(True); thread.join(2)
        self.assertEqual(result, [42]); self.assertEqual(errors, [])

    def test_cancelled_request_retried_not_completed_chunks(self):
        started = threading.Event(); calls = []; result = []
        def request(token):
            calls.append(token)
            if len(calls) == 1:
                started.set()
                while True: token.check(); time.sleep(.005)
            return 'second chunk'
        def work():
            result.append('first chunk')
            result.append(self.control.run(request))
        thread, _, errors = self.launch(work)
        self.assertTrue(started.wait(1)); self.control.set_enabled(False)
        deadline=time.monotonic()+1
        while self.control.public()['state']=='pausing' and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(self.control.public()['state'], 'paused')
        self.assertEqual(result, ['first chunk'])
        self.control.set_enabled(True);thread.join(2)
        self.assertEqual(result,['first chunk','second chunk']);self.assertEqual(errors,[])
        self.assertIsNot(calls[0],calls[1])

    def test_late_result_after_rapid_toggle_is_discarded(self):
        entered=threading.Event(); complete=threading.Event(); calls=[]
        def request(token):
            calls.append(1)
            if len(calls)==1: entered.set();complete.wait(2);return 'stale'
            return 'fresh'
        thread,result,errors=self.launch(lambda:self.control.run(request))
        self.assertTrue(entered.wait(1));self.control.set_enabled(False);self.control.set_enabled(True);complete.set();thread.join(2)
        self.assertEqual(result,['fresh']);self.assertEqual(errors,[])

    def test_user_cancel_and_shutdown_beat_pause(self):
        for shutdown in (False,True):
            control=AIControl();control.set_enabled(False);parent=CancellationToken()
            thread,result,errors=self.launch(lambda:control.run(lambda t:1,parent))
            if shutdown:control.close()
            else:parent.cancel()
            thread.join(2);control.close()
            self.assertFalse(thread.is_alive());self.assertFalse(result)
            self.assertIsInstance(errors[0],TranscriptionCancelled)

    def test_pause_time_is_excluded_and_failure_is_not_retried(self):
        self.control.set_enabled(False); timer=threading.Timer(.15,lambda:self.control.set_enabled(True));timer.start()
        with collect() as trace:
            self.assertEqual(self.control.run(lambda t:3),3)
            self.assertGreaterEqual(trace['waiting_seconds'],.12)
            self.assertEqual(trace['ai_control_events'][0]['status'],'resumed')
        timer.join()
        with self.assertRaisesRegex(ValueError,'invalid'):
            self.control.run(lambda t: (_ for _ in ()).throw(ValueError('invalid')))
        self.assertEqual(AIControl().public()['state'],'enabled')

    def test_user_cancel_is_visible_to_model_session_cleanup(self):
        parent=CancellationToken(); request=RequestToken(parent)
        self.assertFalse(request.cancelled)
        parent.cancel()
        self.assertTrue(request.cancelled)
        with self.assertRaises(TranscriptionCancelled): request.check()

    def test_idle_model_cleanup_remains_pausing_until_finished(self):
        self.control.set_enabled(False)
        started=threading.Event(); release=threading.Event()
        self.control.clean_up(lambda: (started.set(),release.wait(2)))
        self.assertTrue(started.wait(1))
        self.assertEqual(self.control.public()['state'],'pausing')
        release.set()
        deadline=time.monotonic()+2
        while self.control.public()['state']=='pausing' and time.monotonic()<deadline: time.sleep(.01)
        self.assertEqual(self.control.public()['state'],'paused')
