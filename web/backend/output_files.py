from __future__ import annotations

import re
import shutil
import uuid
from collections.abc import Callable
from pathlib import Path
from web.backend.atomic import publish_file, write_text

from web.backend.markdown import markdown_target
from web.backend.media import request as media_request
from web.backend.speech.transcription import SUPPORTED_MEDIA


def rename_output_files(
    targets: list[Path], title: str, audio_names: tuple[str, str] | None = None
) -> tuple[list[Path], Callable[[], None]]:
    """Rename one recording's output files atomically enough to support DB rollback."""
    destinations = [target.with_name(f"{title}{target.suffix}") for target in targets]
    for target, destination in zip(targets, destinations, strict=True):
        if destination != target and destination.exists():
            raise ValueError(f"‘{destination.name}’ 파일이 이미 있습니다.")

    if audio_names is None:
        audio_names = next(
            (
                (target.name, destination.name)
                for target, destination in zip(targets, destinations, strict=True)
                if target.suffix.lower() in SUPPORTED_MEDIA
            ),
            None,
        )
    markdown_sources = {
        target: target.read_text(encoding="utf-8")
        for target in targets
        if target.suffix.lower() == ".md"
    }

    moved: list[tuple[Path, Path]] = []

    def rollback() -> None:
        for target, destination in reversed(moved):
            if target != destination and destination.exists():
                publish_file(destination, target)
        for target, source in markdown_sources.items():
            if target.exists():
                write_text(target, source)

    try:
        for target, destination in zip(targets, destinations, strict=True):
            if target != destination:
                publish_file(target, destination)
                moved.append((target, destination))
        if audio_names:
            old_audio, new_audio = audio_names
            old_target = markdown_target(old_audio)
            new_target = markdown_target(new_audio)
            # Replace in one pass: a new filename may itself contain the old name.
            # Link destinations need URL escaping; frontmatter uses the raw name.
            pattern = re.compile(
                rf"(?P<link>(?<=\]\(){re.escape(old_target)})(?=[#)])"
                rf"|{re.escape(old_audio)}"
            )
            for target, destination in zip(targets, destinations, strict=True):
                if destination.suffix.lower() != ".md":
                    continue
                source = markdown_sources[target]
                write_text(destination, pattern.sub(
                    lambda match: new_target if match.group("link") else new_audio, source
                ))
    except (OSError, UnicodeError):
        rollback()
        raise
    return destinations, rollback


def prepare_webm_playback(target: Path) -> tuple[bool, float | None]:
    """Normalize browser WebM files that lack duration metadata or use tiny Opus frames."""
    if target.suffix.lower() != ".webm":
        return False, None
    info = media_request("probe", source=target)
    current_duration = info["duration"]
    normalize_frames = info["short_frames"]
    if current_duration > 0 and not normalize_frames:
        return False, current_duration

    temporary = target.with_name(f".{target.name}.playback-{uuid.uuid4().hex}.tmp")
    try:
        media_request("transcode", source=target, destination=temporary, playback=True)
        optimized_duration = media_request("probe", source=temporary)["duration"]
        if optimized_duration <= 0 or temporary.stat().st_size == 0:
            raise ValueError("오디오의 재생 정보를 복구하지 못했습니다.")
        shutil.copystat(target, temporary)
        publish_file(temporary, target)
        return True, optimized_duration
    finally:
        temporary.unlink(missing_ok=True)
