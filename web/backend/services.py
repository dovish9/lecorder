"""Application-scoped infrastructure, also inherited by background workers."""
from contextlib import contextmanager
from contextvars import ContextVar, copy_context
from dataclasses import dataclass
from pathlib import Path
import threading

from .platforms import HostPlatform, host
from .processes import PythonWorker, Worker
from .ai_runtime import RuntimeRegistry
from .ai_settings import AISettings, Credentials
from .ocr import OCR, NativeOCR

_current = ContextVar("lecorder_services", default=None)


@dataclass
class Services:
    platform: HostPlatform
    worker: Worker
    runtimes: RuntimeRegistry
    ai: AISettings
    ocr: OCR
    workflow: object = None
    live: object = None

    @classmethod
    def create(cls, data_dir, *, platform=None, worker=None, ocr=None):
        platform = platform or host
        return cls(platform, worker or PythonWorker(),
                   RuntimeRegistry(log_dir=Path(data_dir), platform=platform),
                   AISettings(Path(data_dir) / "ai-settings.json", Credentials(platform)),
                   ocr or NativeOCR(platform))

    @contextmanager
    def scope(self):
        token = _current.set(self)
        try:
            yield self
        finally:
            _current.reset(token)

    def start(self):
        if self.workflow:
            with self.scope():
                self.workflow.start()

    def close(self):
        if self.live:
            self.live.close()
        if self.workflow:
            self.workflow.close()
        self.runtimes.close()


def current_services():
    return _current.get()


def start_thread(target, name):
    context = copy_context()
    thread = threading.Thread(target=context.run, args=(target,), name=name, daemon=True)
    thread.start()
    return thread
