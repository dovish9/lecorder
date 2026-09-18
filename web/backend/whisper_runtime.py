"""Own the local Whisper process only while transcription needs it."""
from __future__ import annotations

import atexit
import os
import socket
import subprocess
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlparse

import requests

from .config import ROOT, WHISPER_URL, EnvironmentStore, resolve_path


class WhisperRuntime:
    def __init__(self, *, url: str = WHISPER_URL, idle_seconds: float | None = None):
        self.url = url
        self.external = bool(os.getenv("WHISPER_URL"))
        self.idle_seconds = max(0, float(os.getenv("WHISPER_IDLE_SECONDS", "60"))) if idle_seconds is None else idle_seconds
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
                    process.wait(timeout=5)
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

    def _start(self, cancellation) -> None:
        if self.external:
            return
        if self._process is not None and self._process.poll() is None:
            return
        endpoint = urlparse(self.url)
        port = endpoint.port or 8080
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=.3):
                raise RuntimeError(f"Whisper 포트 {port}를 다른 서버가 사용 중입니다. 기존 서버를 종료하거나 WHISPER_PORT를 변경하세요.")
        except OSError:
            pass
        home = Path(EnvironmentStore().get().whisper_cpp_dir)
        binary = home / "build/bin/whisper-server"
        model = resolve_path(os.getenv("WHISPER_MODEL_PATH", str(home / "models/ggml-large-v3.bin")))
        vad = resolve_path(os.getenv("WHISPER_VAD_MODEL_PATH", str(home / "models/ggml-silero-v6.2.0.bin")))
        for path in (binary, model, vad):
            if not path.is_file():
                raise RuntimeError(f"Whisper 파일을 찾지 못했습니다: {path}")
        log_path = ROOT / "web/data/whisper-server.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        libraries = [home / "build" / p for p in ("src", "ggml/src", "ggml/src/ggml-blas", "ggml/src/ggml-metal")]
        env["DYLD_LIBRARY_PATH"] = ":".join(map(str, libraries)) + (":" + env["DYLD_LIBRARY_PATH"] if env.get("DYLD_LIBRARY_PATH") else "")
        self._state = "loading"
        try:
            with log_path.open("ab") as log:
                self._process = subprocess.Popen(
                    [str(binary), "-m", str(model), "-vm", str(vad), "--host", "127.0.0.1", "--port", str(port)],
                    cwd=home, env=env, stdout=log, stderr=log,
                )
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if self._closed:
                    raise RuntimeError("Whisper 관리자가 종료되었습니다.")
                if cancellation:
                    cancellation.check()
                if self._process.poll() is not None:
                    raise RuntimeError(f"Whisper 모델 로드 실패. 로그: {log_path}")
                try:
                    response = requests.get(self.url.rsplit("/", 1)[0] + "/health", timeout=(.3, .5))
                    if response.ok and response.json().get("status") == "ok":
                        self._state = "ready"
                        return
                except (requests.RequestException, ValueError):
                    pass
                time.sleep(.1)
            raise RuntimeError("Whisper 모델 로드 제한 시간(180초)을 초과했습니다.")
        except BaseException:
            self._stop()
            raise

    @contextmanager
    def session(self, cancellation=None):
        with self._lock:
            if self._closed:
                raise RuntimeError("Whisper 관리자가 종료되었습니다.")
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
                # Disconnecting curl does not guarantee immediate inference abort.
                if cancellation and cancellation.cancelled and self._users == 0:
                    self._stop()
                if self._users == 0 and not self.external and not self._closed:
                    self._timer = threading.Timer(self.idle_seconds, self._expire)
                    self._timer.daemon = True
                    self._timer.start()


whisper_runtime = WhisperRuntime()
