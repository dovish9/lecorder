"""Owned, cancellable child processes and the JSON worker protocol."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Protocol

from web.backend.platforms import host


class TranscriptionCancelled(RuntimeError):
    """The caller cancelled an operation before its result could be published."""


class CancellationToken:
    def __init__(self):
        self._event = threading.Event()
        self._lock = threading.RLock()
        self._processes = set()

    @property
    def cancelled(self):
        return self._event.is_set()

    def check(self):
        if self.cancelled:
            raise TranscriptionCancelled("사용자가 작업을 중단했습니다.")

    def cancel(self):
        self._event.set()
        with self._lock:
            for process in self._processes:
                self._terminate(process)

    @staticmethod
    def _terminate(process):
        try:
            if process.poll() is None:
                process.terminate()
        except ProcessLookupError:
            pass

    def run(self, args, *, input_data=None, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, timeout=None, env=None):
        from web.backend.execution_context import execution_resources
        services = execution_resources()
        platform = services.platform if services else host
        self.check()
        process = subprocess.Popen(
            args, stdin=subprocess.PIPE if input_data is not None else None,
            stdout=stdout, stderr=stderr, env=env, **platform.process_options(),
        )
        with self._lock:
            self._processes.add(process)
            if self.cancelled:
                self._terminate(process)
        started = time.monotonic()
        try:
            # communicate() preserves buffered output across timeout retries. A
            # bounded wait also handles a child that ignores SIGTERM on POSIX.
            while True:
                self.check()
                remaining = None if timeout is None else timeout - (time.monotonic() - started)
                if remaining is not None and remaining <= 0:
                    raise subprocess.TimeoutExpired(args, timeout)
                try:
                    output, errors = process.communicate(
                        input=input_data, timeout=min(.2, remaining) if remaining is not None else .2,
                    )
                    break
                except subprocess.TimeoutExpired:
                    input_data = None
            self.check()
            result = subprocess.CompletedProcess(args, process.returncode, output, errors)
            result.check_returncode()
            return result
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate()
            with self._lock:
                self._processes.discard(process)


class Worker(Protocol):
    def call(self, name: str, payload, *, cancellation=None, timeout=180): ...


class PythonWorker:
    """Worker names are internal constants; user paths never select Python code."""
    names = frozenset({"media", "document", "math", "http"})

    def call(self, name, payload, *, cancellation=None, timeout=180):
        if name not in self.names:
            raise ValueError("알 수 없는 작업 도구입니다.")
        token = cancellation or CancellationToken()
        result = token.run(
            [sys.executable, "-X", "utf8", str(Path(__file__).with_name(name + "_worker.py"))],
            input_data=json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8"),
            timeout=timeout,
        )
        return json.loads(result.stdout)


class WorkerRouter:
    def __init__(self):
        self.fallback = PythonWorker()

    def call(self, *args, **kwargs):
        from web.backend.execution_context import execution_resources
        services = execution_resources()
        return (services.worker if services else self.fallback).call(*args, **kwargs)


worker = WorkerRouter()
