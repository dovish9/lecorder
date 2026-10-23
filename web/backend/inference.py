"""One inference slot; waiting speech work takes precedence between requests."""

from contextlib import contextmanager
import threading
import time
from web.backend.execution_metrics import add_wait


class InferenceCoordinator:
    def __init__(self):
        self._condition = threading.Condition()
        self._busy = False
        self._speech_waiters = 0
        self._reservation = None

    def reserve(self, owner):
        with self._condition:
            if self._reservation not in (None, owner):
                raise ValueError('다른 녹음에서 실시간 음성 변환을 사용 중입니다.')
            self._reservation = owner
            self._condition.notify_all()

    def release(self, owner):
        with self._condition:
            if self._reservation == owner:
                self._reservation = None
                self._condition.notify_all()

    @property
    def reserved(self):
        with self._condition:
            return self._reservation is not None

    @contextmanager
    def lease(self, speech=False, cancellation=None, *, owner=None, local=True):
        if not local:
            if cancellation:
                cancellation.check()
            yield
            return
        queued = time.monotonic()
        with self._condition:
            if speech:
                self._speech_waiters += 1
            try:
                while (self._busy or self._reservation not in (None, owner)
                       or (not speech and self._speech_waiters and not (owner and owner == self._reservation))):
                    if cancellation:
                        cancellation.check()
                    self._condition.wait(0.25)
                if cancellation:
                    cancellation.check()
                self._busy = True
            finally:
                add_wait(time.monotonic() - queued)
                if speech:
                    self._speech_waiters -= 1
        try:
            yield
        finally:
            with self._condition:
                self._busy = False
                self._condition.notify_all()


inference = InferenceCoordinator()
