"""One inference slot; waiting speech work takes precedence between requests."""

from contextlib import contextmanager
import threading


class InferenceCoordinator:
    def __init__(self):
        self._condition = threading.Condition()
        self._busy = False
        self._speech_waiters = 0

    @contextmanager
    def lease(self, speech=False, cancellation=None):
        with self._condition:
            if speech:
                self._speech_waiters += 1
            try:
                while self._busy or (not speech and self._speech_waiters):
                    if cancellation:
                        cancellation.check()
                    self._condition.wait(0.25)
                if cancellation:
                    cancellation.check()
                self._busy = True
            finally:
                if speech:
                    self._speech_waiters -= 1
        try:
            yield
        finally:
            with self._condition:
                self._busy = False
                self._condition.notify_all()


inference = InferenceCoordinator()
