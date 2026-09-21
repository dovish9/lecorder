"""Application records shared by persistence, routes, and the pipeline."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any

from .config import DEFAULT_LLM_MODEL


@dataclass(frozen=True)
class Course:
    id: int
    name: str
    language: str = "ko"
    prompt: str = ""
    corrections: str = ""
    format_transcript: bool = True
    llm_enabled: bool = True
    updated_at: str = ""

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("prompt", None)
        data.pop("corrections", None)
        return data


@dataclass(frozen=True)
class ActiveSettings:
    course_id: int | None
    course_name: str
    language: str
    prompt: str
    corrections: str
    format_transcript: bool
    llm_enabled: bool
    output_dir: str
    llm_model: str = DEFAULT_LLM_MODEL

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        data.pop("output_dir")
        data.pop("prompt", None)
        data.pop("corrections", None)
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
    prompt: str
    corrections: str
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

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        private_fields = {
            "prompt",
            "corrections",
            "output_dir",
            "source_path",
            "transcript_text",
            "segments_json",
            "breaks_json",
            "quality_json",
            "note_snapshot_json",
        }
        for key in private_fields:
            data.pop(key)
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
