from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from .records import Recording


def clock(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"


def _quoted(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _local_timestamp(value: str) -> str:
    try:
        return datetime.fromisoformat(value).astimezone().isoformat(timespec="seconds")
    except (TypeError, ValueError):
        return value


def markdown_target(filename: str) -> str:
    """Keep readable Unicode while escaping characters that break Markdown links."""
    safe = "-._~!$&'*,;=+@"
    output: list[str] = []
    for character in filename:
        if character.isalnum() or character in safe:
            output.append(character)
        else:
            output.extend(f"%{byte:02X}" for byte in character.encode("utf-8"))
    return "".join(output)


def transcript_text(segments: list[dict[str, Any]], breaks: set[int]) -> str:
    output: list[str] = []
    paragraph: list[str] = []
    for index, segment in enumerate(segments, 1):
        paragraph.append(str(segment.get("text") or "").strip())
        if index in breaks:
            output.append(" ".join(item for item in paragraph if item))
            paragraph = []
    if paragraph:
        output.append(" ".join(item for item in paragraph if item))
    return "\n\n".join(item for item in output if item)


def render_note(recording: Recording, segments: list[dict[str, Any]], breaks: set[int]) -> str:
    audio_name = Path(recording.audio_path).name
    audio_target = markdown_target(audio_name)
    review_model = recording.llm_model if recording.llm_enabled else ""
    lines = [
        "---",
        "type: lecture-transcript",
        f"recording_id: {_quoted(recording.id)}",
        f"course: {_quoted(recording.course_name)}",
        f"recorded_at: {_quoted(_local_timestamp(recording.created_at))}",
        f"duration: {_quoted(clock(recording.duration_seconds))}",
        f"duration_seconds: {round(recording.duration_seconds, 3)}",
        f"language: {_quoted(recording.language)}",
        'speech_model: "whisper.cpp/large-v3"',
        f"review_model: {_quoted(review_model)}",
        f"audio: {_quoted(audio_name)}",
        "---",
        "",
        f"[녹음 듣기]({audio_target})",
        "",
        "# 전사문",
        "",
    ]
    paragraph: list[str] = []
    paragraph_start = 0.0

    def write_paragraph() -> None:
        if not paragraph:
            return
        target = f"{audio_target}#t={int(paragraph_start)}"
        lines.append(f"[{clock(paragraph_start)}]({target}) {' '.join(paragraph)}")
        lines.append("")

    for index, segment in enumerate(segments, 1):
        text = str(segment.get("text") or "").strip()
        if text:
            if not paragraph:
                paragraph_start = max(0.0, float(segment.get("start") or 0))
            paragraph.append(text)
        if index in breaks:
            write_paragraph()
            paragraph = []
    write_paragraph()
    return "\n".join(lines).rstrip() + "\n"
