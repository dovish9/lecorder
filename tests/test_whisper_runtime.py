import tempfile
import threading
import time
import unittest
import wave
from pathlib import Path
from unittest.mock import Mock, patch

from web.backend.engine import Transcriber
from web.backend.transcription import CancellationToken, Options, TranscriptSegment, TranscriptionCancelled, WhisperResult
from web.backend.whisper_runtime import WhisperRuntime


class RuntimeTests(unittest.TestCase):
    def runtime(self):
        runtime = WhisperRuntime(idle_seconds=.04)
        runtime.external = False
        self.addCleanup(runtime.close)
        return runtime

    def process(self, runtime):
        process = Mock()
        process.poll.return_value = None
        process.wait.side_effect = lambda **kw: setattr(process.poll, 'return_value', 0)
        runtime._process = process
        runtime._state = 'ready'
        return process

    def test_lazy_load_reuse_and_idle_unload(self):
        runtime = self.runtime()
        self.assertEqual(runtime.state, 'idle')
        self.assertIsNone(runtime._process)
        process = self.process(runtime)
        with runtime.session():
            time.sleep(.07)
            process.terminate.assert_not_called()
        with runtime.session():
            time.sleep(.07)
            process.terminate.assert_not_called()
        deadline = time.monotonic() + 1
        while runtime._process is not None and time.monotonic() < deadline:
            time.sleep(.01)
        process.terminate.assert_called_once()
        self.assertIsNone(runtime._process)
        self.assertEqual(runtime.state, 'idle')

    def test_cancel_stops_inflight_server(self):
        runtime = self.runtime()
        process = self.process(runtime)
        token = CancellationToken()
        with runtime.session(token):
            token.cancel()
        process.terminate.assert_called_once()
        self.assertIsNone(runtime._process)

    def test_close_stops_owned_process_and_rejects_new_work(self):
        runtime = self.runtime()
        process = self.process(runtime)
        runtime.close()
        process.terminate.assert_called_once()
        with self.assertRaises(RuntimeError):
            with runtime.session(): pass

    def test_external_server_is_not_started_or_stopped(self):
        runtime = self.runtime()
        runtime.external = True
        with patch('web.backend.whisper_runtime.subprocess.Popen') as popen:
            with runtime.session(): pass
            runtime.close()
            popen.assert_not_called()

    def test_cancelled_job_never_loads_model(self):
        runtime = self.runtime()
        token = CancellationToken()
        token.cancel()
        with patch.object(runtime, '_start') as start:
            with self.assertRaises(TranscriptionCancelled):
                with runtime.session(token): pass
            start.assert_not_called()

    def test_old_timer_cannot_unload_new_session(self):
        runtime = self.runtime()
        process = self.process(runtime)
        runtime._timer = Mock()
        runtime._expire()
        process.terminate.assert_not_called()

    def test_load_failure_resets_state_and_can_be_retried(self):
        runtime = self.runtime()
        process = Mock()
        process.poll.return_value = 1
        with patch('web.backend.whisper_runtime.socket.create_connection', side_effect=OSError), \
             patch('web.backend.whisper_runtime.Path.is_file', return_value=True), \
             patch('web.backend.whisper_runtime.subprocess.Popen', return_value=process):
            with self.assertRaisesRegex(RuntimeError, '로드 실패'):
                with runtime.session(): pass
        self.assertEqual(runtime.state, 'idle')
        self.assertIsNone(runtime._process)
        self.assertEqual(runtime._users, 0)

    def test_launch_error_does_not_leave_loading_state(self):
        runtime = self.runtime()
        with patch('web.backend.whisper_runtime.socket.create_connection', side_effect=OSError), \
             patch('web.backend.whisper_runtime.Path.is_file', return_value=True), \
             patch('web.backend.whisper_runtime.subprocess.Popen', side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                with runtime.session(): pass
        self.assertEqual(runtime.state, 'idle')

    def test_busy_port_is_rejected_without_adopting_other_process(self):
        runtime = self.runtime()
        with patch('web.backend.whisper_runtime.socket.create_connection') as connect, \
             patch('web.backend.whisper_runtime.subprocess.Popen') as popen:
            connect.return_value.__enter__ = Mock()
            connect.return_value.__exit__ = Mock(return_value=False)
            with self.assertRaisesRegex(RuntimeError, '다른 서버'):
                with runtime.session(): pass
            popen.assert_not_called()


class ChunkTests(unittest.TestCase):
    def test_chunks_keep_timeline_and_remove_overlap_duplicates(self):
        lengths = []
        def whisper(clip, *args):
            with wave.open(str(clip)) as audio:
                duration = audio.getnframes() / audio.getframerate()
            lengths.append(duration)
            # Segments at one-second intervals let us check seam ownership.
            segments = tuple(TranscriptSegment(i+1,i,i+1,f'word {i}') for i in range(int(duration)))
            return WhisperResult('text', duration, segments)
        with tempfile.TemporaryDirectory() as d:
            wav = Path(d)/'long.wav'
            with wave.open(str(wav),'wb') as audio:
                audio.setparams((1,2,16000,0,'NONE','not compressed'))
                audio.writeframes(b'\0\0'*16000*250)
            with patch.object(Transcriber,'_whisper',side_effect=whisper), patch.object(Transcriber, '_chunk_boundaries', return_value=[(0,True),(120,False),(240,False),(250,True)]):
                result=Transcriber._whisper_chunked(wav,Options(language='en'))
            self.assertEqual(list(Path(d).iterdir()),[wav])
        self.assertEqual(lengths,[122,124,12])
        self.assertEqual(result.duration_seconds,250)
        self.assertEqual([s.start for s in result.segments],list(range(250)))
        self.assertEqual([s.end for s in result.segments],list(range(1,251)))
        self.assertEqual([s.index for s in result.segments],list(range(1,251)))

    def test_silence_boundaries_prefer_pause_and_bound_chunk_size(self):
        completed = Mock(stderr=b'silence_start: 117.0\nsilence_end: 119.0\n')
        with patch('web.backend.engine.subprocess.run', return_value=completed):
            boundaries = Transcriber._chunk_boundaries(Path('sample.wav'), 400)
        self.assertEqual(boundaries, [(0.0, True), (118.0, True), (238.0, False), (358.0, False), (400, True)])

    def test_boundary_does_not_leave_a_one_second_tail(self):
        completed = Mock(stderr=b'silence_start: 131.5\nsilence_end: 132.5\n')
        with patch('web.backend.engine.subprocess.run', return_value=completed):
            boundaries = Transcriber._chunk_boundaries(Path('sample.wav'), 133)
        self.assertEqual(boundaries, [(0.0, True), (103, False), (133, True)])

    def test_short_collapse_is_detected_but_natural_restatement_is_allowed(self):
        phrase='We calculate the electric field from this equation.'
        self.assertTrue(Transcriber._repetition_profile([TranscriptSegment(1,0,20,' '.join([phrase]*5))])[0])
        self.assertTrue(Transcriber._repetition_profile([TranscriptSegment(i,i,i+1,phrase) for i in range(4)])[0])
        self.assertFalse(Transcriber._repetition_profile([TranscriptSegment(i,i,i+1,phrase) for i in range(2)])[0])

    def test_hints_are_bounded_and_empty_hint_is_explicit(self):
        response=Mock()
        response.json.return_value={'text':'test','duration':1,'segments':[{'start':0,'end':1,'text':'test'}]}
        with tempfile.TemporaryDirectory() as d:
            wav=Path(d)/'audio.wav';wav.write_bytes(b'wav')
            with patch('web.backend.engine.requests.post',return_value=response) as post:
                Transcriber._whisper(wav,Options(language='ko',recognition_hint='물리학 용어'),None)
                self.assertEqual(post.call_args.kwargs['data']['prompt'], '물리학 용어')
                Transcriber._whisper(wav,Options(language='en'),{'no_context':'false'})
                self.assertEqual(post.call_args.kwargs['data']['no_context'],'true')
                self.assertEqual(post.call_args.kwargs['data']['prompt'],'')


if __name__ == '__main__': unittest.main()
