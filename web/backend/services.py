"""Application-scoped infrastructure, also inherited by background workers."""
from dataclasses import dataclass, field
from pathlib import Path

from web.backend.platforms import HostPlatform, host
from web.backend.processes import PythonWorker, Worker
from web.backend.ai_runtime import RuntimeRegistry
from web.backend.ai_settings import AISettings, Credentials, SETTING_FILE
from web.backend.config import DATA_DIR
from web.backend.ocr import OCR, NativeOCR
from web.backend.ai_control import AIControl

from web.backend.execution_context import ExecutionResources


@dataclass
class Services:
    platform: HostPlatform
    worker: Worker
    runtimes: RuntimeRegistry
    ai: AISettings
    ocr: OCR
    ai_control: AIControl = field(default_factory=AIControl)
    workflow: object = None
    live: object = None
    history: object = None
    insights: object = None
    backups: object = None

    @classmethod
    def create(cls, data_dir, *, platform=None, worker=None, ocr=None):
        platform = platform or host
        return cls(platform, worker or PythonWorker(),
                   RuntimeRegistry(log_dir=Path(data_dir), platform=platform),
                   AISettings(SETTING_FILE if Path(data_dir).resolve()==DATA_DIR.resolve() else Path(data_dir) / "ai-settings.json", Credentials(platform)),
                   ocr or NativeOCR(platform))

    def scope(self):
        return ExecutionResources(self.platform, self.worker, self.runtimes,
                                  self.ai, self.ocr, self.ai_control, self.history).scope()

    def start(self):
        if self.history:
            self.history.recover()
        if self.insights:
            self.insights.start()
        if self.workflow:
            with self.scope():
                self.workflow.start()

    def close(self):
        if self.backups:
            self.backups.close()
        if self.insights:
            self.insights.close()
        if self.workflow:
            self.workflow.prepare_shutdown()
        self.ai_control.close()
        if self.live:
            self.live.close()
        if self.workflow:
            self.workflow.close()
        self.runtimes.close()
        if self.workflow:
            self.workflow.store.close()
