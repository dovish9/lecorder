import os
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch
from py_mini_racer import MiniRacer, JSTimeoutException
from web.backend.note_validation import code_issues
from web.backend.transcription import CancellationToken, TranscriptionCancelled


class MathRuntimeTests(unittest.TestCase):
    def test_without_browser_or_node(self):
        with patch.dict(os.environ, {'PATH': ''}):
            self.assertEqual(code_issues({'markdown': r'$$\int_0^1 x^2 dx = \frac{1}{3}$$'}), [])
            self.assertTrue(code_issues({'markdown': r'$\unknown{x}$'}))
            self.assertTrue(code_issues({'markdown': '$x'}))

    def test_cancel_is_propagated_not_reported_as_pass(self):
        token = CancellationToken()
        timer = threading.Timer(.02, token.cancel)
        timer.start()
        try:
            with self.assertRaises(TranscriptionCancelled):
                code_issues({'markdown': '$x$ ' * 100000}, token)
        finally:
            timer.join()
        self.assertFalse(token._processes)

    def test_failed_or_missing_runtime_is_not_a_pass(self):
        for error in [OSError('missing'), subprocess.TimeoutExpired('worker', 10), subprocess.CalledProcessError(1, 'worker')]:
            with patch.object(CancellationToken, 'run', side_effect=error):
                self.assertEqual(code_issues({'markdown': '$x$'}), ['수식 검증기 실행에 실패했습니다.'])

    def test_v8_interrupts_runaway_javascript(self):
        with MiniRacer() as engine:
            with self.assertRaises(JSTimeoutException):
                engine.eval('while (true) {}', timeout_sec=.01)
            self.assertEqual(engine.eval('1+1'), 2)

    def test_worker_timeout_reaps_process(self):
        token = CancellationToken()
        with self.assertRaises(subprocess.TimeoutExpired):
            token.run([sys.executable, '-c', 'import time; time.sleep(60)'], timeout=.03)
        self.assertFalse(token._processes)
