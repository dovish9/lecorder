"""Application records shared by persistence, routes, and the pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from web.backend.config import DEFAULT_LLM_MODEL


@dataclass(frozen=True)
class Course:
    id: int
    name: str
    language: str = "ko"
    updated_at: str = ""

    def public(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "language": self.language,
                "updated_at": self.updated_at}


@dataclass(frozen=True)
class ActiveSettings:
    course_id: int | None
    course_name: str
    language: str
    format_transcript: bool
    llm_enabled: bool
    output_dir: str
    llm_model: str = DEFAULT_LLM_MODEL

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("output_dir")
        return data


@dataclass(frozen=True)
class Recording:
    id: str
    course_id: int | None
    course_name: str
    title: str
    source_kind: str
    extension: str
    status: str
    language: str
    format_transcript: bool
    llm_enabled: bool
    llm_model: str
    output_dir: str
    source_path: str = ""
    audio_path: str = ""
    note_path: str = ""
    duration_seconds: float = 0.0
    processing_seconds: float = 0.0
    transcript_text: str = ""
    segments_json: str = "[]"
    breaks_json: str = "[]"
    quality_json: str = "{}"
    error: str = ""
    created_at: str = ""
    started_at: str = ""
    completed_at: str = ""
    note_revision_id: str | None = None
    note_snapshot_json: str = "{}"
    review_status: str = "none"
    review_error: str = ""
    last_operation: str = "transcribe"
    ai_settings_json: str = "{}"

    def public(self) -> dict[str, Any]:
        fields = (
            'id', 'course_id', 'course_name', 'title', 'source_kind', 'extension',
            'status', 'language', 'format_transcript', 'llm_enabled', 'llm_model',
            'audio_path', 'note_path', 'duration_seconds', 'processing_seconds',
            'error', 'created_at', 'started_at', 'completed_at', 'note_revision_id',
            'review_status', 'review_error', 'last_operation',
        )
        data = {name: getattr(self, name) for name in fields}
        try:
            data["waiting_for_note"] = self.status == "queued" and bool(json.loads(self.note_snapshot_json).get("waiting_for_analysis"))
        except (ValueError, TypeError, AttributeError):
            data["waiting_for_note"] = False
        try:
            data["quality"] = json.loads(self.quality_json)
        except (json.JSONDecodeError, TypeError):
            data["quality"] = {}
        return data


@dataclass(frozen=True)
class StoredSuggestion:
    id: int
    recording_id: str
    sentence_id: str
    original: str
    replacement: str
    reason: str
    confidence: float
    status: str
    created_at: str
    decided_at: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)
