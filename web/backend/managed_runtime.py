"""Common lifecycle for owned model servers, independent of provider and OS."""
from __future__ import annotations
from web.backend.runtime_config import get as runtime

import atexit
import subprocess
import threading
from contextlib import contextmanager


class ManagedRuntime:
    def __init__(self, *, url: str, idle_seconds: float = 60, external: bool = False):
        self.url = url
        self.external = external
        self.idle_seconds = max(0, idle_seconds)
        self._lock = threading.RLock()
        self._process: subprocess.Popen | None = None
        self._timer: threading.Timer | None = None
        self._users = 0
        self._closed = False
        self._state = "idle"
        atexit.register(self.close)

    @property
    def state(self) -> str:
        if self.external:
            return "external"
        if self._process is not None and self._process.poll() is not None:
            return "offline"
        return self._state

    def _stop(self) -> None:
        process = self._process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=runtime('LECORDER_MODEL_STOP_SECONDS'))
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            self._process = None
        self._state = "idle"

    def _expire(self) -> None:
        with self._lock:
            # A cancelled timer may already be waiting for the lock.
            if self._timer is not threading.current_thread():
                return
            self._timer = None
            if self._users == 0:
                self._stop()

    def close(self) -> None:
        self._closed = True
        with self._lock:
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._stop()

    def _start(self, cancellation):
        raise NotImplementedError

    @contextmanager
    def session(self, cancellation=None):
        with self._lock:
            if self._closed:
                raise RuntimeError("모델 관리자가 종료되었습니다.")
            if cancellation:
                cancellation.check()
            if self._timer:
                self._timer.cancel()
                self._timer = None
            self._start(cancellation)
            self._users += 1
        try:
            yield
        finally:
            with self._lock:
                self._users -= 1
                # Disconnecting the HTTP client does not guarantee immediate inference abort.
                if cancellation and cancellation.cancelled and self._users == 0:
                    self._stop()
                if self._users == 0 and not self.external and not self._closed:
                    self._timer = threading.Timer(self.idle_seconds, self._expire)
                    self._timer.daemon = True
                    self._timer.start()
