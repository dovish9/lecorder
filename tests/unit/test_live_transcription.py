"""Live drafts cannot block original recording or leak resource reservations."""
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from web.backend.inference import InferenceCoordinator
from web.backend.live_transcription import LiveSession, RATE
from web.backend.processes import CancellationToken, TranscriptionCancelled


class LiveTests(unittest.TestCase):
    def session(self):
        root = tempfile.TemporaryDirectory(); self.addCleanup(root.cleanup)
        listener = Mock()
        listener.getsockname.return_value = ('127.0.0.1', 19082)
        socket_patch = patch('web.backend.live_transcription.socket.socket')
        socket_patch.start().return_value.__enter__.return_value = listener
        self.addCleanup(socket_patch.stop)
        service = SimpleNamespace(folder=lambda _: Path(root.name), workflow=Mock(), platform=Mock())
        return LiveSession(service, 'recording', {})

    def test_order_gaps_duplicates_and_bounded_memory(self):
        s = self.session(); packet = b'\0' * RATE * 2
        for n in range(65): s.push(n, n*RATE, packet)
        self.assertEqual(s.pending_samples, RATE*60)
        self.assertEqual(len(s.gaps), 5)
        s.push(64, 64*RATE, packet)
        self.assertEqual(len(s.pending), 60)
        s.push(67, 67*RATE, packet)
        self.assertTrue(any(g['reason'] == '음성 전송 누락' for g in s.gaps))
        with self.assertRaises(ValueError): s.push(68, 66*RATE, packet)
        s.stop()
        with self.assertRaises(ValueError): s.push(68, 68*RATE, packet)
        self.assertEqual(s.pending_samples, 0)

    def test_pause_accepts_last_inflight_packet_and_flushes_it(self):
        s = self.session(); s.control(True)
        s.flush = False
        s.push(0, 0, b'\0'*1000)
        self.assertTrue(s.flush)
        self.assertEqual(s.pending_samples, 500)

    def test_reservation_yields_at_boundary_and_remote_bypasses(self):
        c = InferenceCoordinator(); started = threading.Event(); finished = threading.Event()
        c.reserve('live')
        def ordinary():
            started.set()
            with c.lease(): finished.set()
        t = threading.Thread(target=ordinary); t.start(); self.addCleanup(t.join, 2)
        self.assertTrue(started.wait(1)); self.assertFalse(finished.wait(.03))
        with c.lease(local=False): self.assertFalse(finished.is_set())
        with c.lease(owner='live', speech=True): self.assertFalse(finished.is_set())
        c.release('other'); self.assertTrue(c.reserved)
        c.release('live'); self.assertTrue(finished.wait(1))

    def test_cancelled_waiter_does_not_release_owner(self):
        c = InferenceCoordinator(); c.reserve('live'); token = CancellationToken(); token.cancel()
        with self.assertRaises(TranscriptionCancelled):
            with c.lease(cancellation=token): pass
        self.assertTrue(c.reserved)
        c.release('live')
        with c.lease(): pass

    def test_active_request_finishes_then_live_precedes_next_chunk(self):
        c = InferenceCoordinator()
        active = threading.Event(); boundary = threading.Event()
        acquired = threading.Event(); release_live = threading.Event()
        order = []
        def normal():
            with c.lease(speech=True):
                active.set(); boundary.wait(2); order.append('first')
            with c.lease(speech=True): order.append('second')
        def live():
            with c.lease(speech=True, owner='live'):
                order.append('live'); acquired.set(); release_live.wait(2)
            c.release('live')
        ordinary = threading.Thread(target=normal); ordinary.start()
        self.assertTrue(active.wait(1)); c.reserve('live')
        owner = threading.Thread(target=live); owner.start()
        try:
            self.assertFalse(acquired.wait(.03))
            boundary.set(); self.assertTrue(acquired.wait(1))
            self.assertEqual(order, ['first', 'live'])
        finally:
            boundary.set(); release_live.set(); c.release('live')
            owner.join(2); ordinary.join(2)
        self.assertEqual(order, ['first', 'live', 'second'])

    def test_failure_closes_only_session_owned_runtime(self):
        s = self.session()
        runtime = Mock(); runtime.session.side_effect = RuntimeError('model missing')
        with patch('web.backend.live_transcription.ModelRuntime', return_value=runtime):
            s.run()
        self.assertEqual(s.state, 'failed')
        runtime.close.assert_called_once()
        self.assertTrue(s.stopped.is_set())

    def test_live_runs_while_foreground_slot_is_busy_and_does_not_reserve_it(self):
        from contextlib import nullcontext
        from web.backend.inference import inference
        s = self.session()
        s.config = {'base_url': 'http://127.0.0.1:8080'}
        runtime = Mock(); runtime.session.return_value = nullcontext()
        s.service.vad_factory = Mock(return_value=Mock())
        entered = threading.Event(); finish = threading.Event()
        s._consume = lambda: (entered.set(), finish.wait(2))
        with patch('web.backend.live_transcription.ModelRuntime', return_value=runtime) as factory:
            with inference.lease():
                worker = threading.Thread(target=s.run); worker.start()
                try:
                    self.assertTrue(entered.wait(1))
                    self.assertFalse(inference.reserved)
                    self.assertNotEqual(s.config['base_url'], 'http://127.0.0.1:8080')
                    self.assertNotIn('peers', factory.call_args.kwargs)
                finally:
                    finish.set(); worker.join(3)
        runtime.close.assert_called_once()
        self.assertTrue(s.stopped.is_set())

    def test_silence_is_not_sent_to_whisper(self):
        s = self.session(); s.helper = Mock(); s.helper.probabilities.return_value = [0]*32
        s._decode = Mock()
        s.push(0, 0, b'\0'*RATE*2)
        t = threading.Thread(target=lambda: self.consume(s)); t.start()
        deadline = time.monotonic()+1
        while not s.helper.probabilities.called and time.monotonic()<deadline: time.sleep(.01)
        s.stop(); t.join(1)
        self.assertFalse(t.is_alive()); s._decode.assert_not_called()

    @staticmethod
    def consume(s):
        try: s._consume()
        except TranscriptionCancelled: pass

    def test_long_speech_splits_with_one_second_overlap_and_pause_flush(self):
        s = self.session(); s.helper = Mock()
        s.helper.probabilities.side_effect = lambda pcm, _: [1]*((len(pcm)//2+511)//512)
        s._decode = Mock()
        for n in range(21): s.push(n,n*RATE,b'\0'*RATE*2)
        s.control(True)
        t = threading.Thread(target=lambda: self.consume(s)); t.start()
        deadline=time.monotonic()+1
        while s._decode.call_count<2 and time.monotonic()<deadline: time.sleep(.01)
        s.stop(); t.join(1)
        first, second = s._decode.call_args_list[:2]
        self.assertEqual((len(first.args[0])//2,first.args[1]),(20*RATE,0))
        self.assertEqual(second.args[1],19*RATE)
        self.assertTrue(second.args[3])

    def test_late_inference_response_cannot_publish_after_stop(self):
        from web.backend.transcription import Options
        s = self.session(); s.config={'base_url':'http://127.0.0.1:1'}
        s._options=Mock(return_value=Options(language='en'))
        def response(*args, **kwargs):
            s.stop()
            return {'text':'too late','segments':[]}
        with patch('web.backend.live_transcription.request_json', side_effect=response):
            with self.assertRaises(TranscriptionCancelled):
                s._decode(b'\0'*32000,0,time.monotonic(),False)
        self.assertEqual(s.segments,[])
        self.assertFalse((s.folder/'live-window.wav').exists())
