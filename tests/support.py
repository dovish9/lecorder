from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

from web.backend.config import EnvironmentStore
from web.backend.platforms import host
PROJECT_ROOT = Path(__file__).resolve().parents[1]
from web.backend.speech.engine import Transcriber
from web.backend.speech.transcription import Options, Result, TranscriptSegment


class FakeTranscriber:
    def __init__(self) -> None:
        self.calls: list[Options] = []

    def transcribe(self, source, options, incoming=None, progress=None,
                   cancellation=None) -> Result:
        self.calls.append(options)
        return Result(
            "정리된 전사문", segments=(TranscriptSegment(1, 12.0, 18.0, "정리된 전사문"),),
            duration_seconds=18.0,
            conversion_seconds=.1, whisper_primary_seconds=.2, qwen_seconds=.3,
            pipeline_settings={"entropy_thold": "2.8", "no_context": "true"},
            qwen_requests=2, qwen_prompt_tokens=120, qwen_output_tokens=24,
        )


class RecorderCase(unittest.TestCase):
    def setUp(self):
        from web.backend.library.store import LectureStore
        original = LectureStore.__init__
        def fixture_store(store, *args, **kwargs):
            original(store,*args,**kwargs)
            self.addCleanup(store.close)
            if not store.courses.list_courses(): store.courses.create_course('새 강의')
        fixture = patch.object(LectureStore,'__init__',fixture_store)
        fixture.start(); self.addCleanup(fixture.stop)
        for module in ('recordings.pipeline', 'recordings.review_jobs', 'notes.notes', 'notes.note_study', 'notes.note_intake'):
            availability = patch(f"web.backend.{module}.require_llm", return_value=None)
            availability.start()
            self.addCleanup(availability.stop)


    @staticmethod
    def environment(root: Path) -> tuple[EnvironmentStore, Path]:
        project = root / "lecorder"
        whisper = project / "dependencies/whisper.cpp"
        output = root / "output"
        project.mkdir()
        whisper.mkdir(parents=True)
        output.mkdir()
        (project / "app.py").write_text("", encoding="utf-8")
        (project / "web/backend").mkdir(parents=True)
        (whisper / "build/bin").mkdir(parents=True)
        host.executable(whisper / "build/bin", "whisper-server").write_text("", encoding="utf-8")
        (whisper / "models").mkdir()
        (whisper / "models/ggml-large-v3.bin").write_text("", encoding="utf-8")
        (whisper / "models/ggml-silero-v6.2.0.bin").write_text("", encoding="utf-8")
        environment = EnvironmentStore(root / ".env", project)
        environment.update(project_dir=str(project), output_dir=str(output))
        return environment, output
