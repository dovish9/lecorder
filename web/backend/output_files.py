from __future__ import annotations

import shutil
import subprocess
import uuid
from collections.abc import Callable
from pathlib import Path

from .markdown import markdown_target
from .transcription import SUPPORTED_MEDIA


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

    def rollback() -> None:
        for target, destination in reversed(list(zip(targets, destinations, strict=True))):
            if target != destination and destination.exists():
                destination.rename(target)
        for target, source in markdown_sources.items():
            if target.exists():
                target.write_text(source, encoding="utf-8")

    try:
        for target, destination in zip(targets, destinations, strict=True):
            if target != destination:
                target.rename(destination)
        if audio_names:
            old_audio, new_audio = audio_names
            old_target = markdown_target(old_audio)
            new_target = markdown_target(new_audio)
            for target, destination in zip(targets, destinations, strict=True):
                if destination.suffix.lower() != ".md":
                    continue
                source = markdown_sources[target]
                destination.write_text(
                    source.replace(old_target, new_target).replace(old_audio, new_audio),
                    encoding="utf-8",
                )
    except (OSError, UnicodeError):
        rollback()
        raise
    return destinations, rollback


def _probe_duration(ffprobe: str, path: Path) -> float:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def _uses_short_audio_frames(ffprobe: str, path: Path) -> bool:
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-read_intervals",
            "%+1",
            "-select_streams",
            "a:0",
            "-show_entries",
            "packet=duration_time",
            "-of",
            "csv=p=0",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    frame_lengths: list[float] = []
    for line in result.stdout.splitlines():
        try:
            frame_lengths.append(float(line.rstrip(",")))
        except ValueError:
            continue
    return (
        len(frame_lengths) >= 4
        and sum(length < 0.01 for length in frame_lengths) > len(frame_lengths) / 2
    )


def prepare_webm_playback(target: Path) -> tuple[bool, float | None]:
    """Normalize browser WebM files that lack duration metadata or use tiny Opus frames."""
    if target.suffix.lower() != ".webm":
        return False, None
    ffprobe = shutil.which("ffprobe")
    ffmpeg = shutil.which("ffmpeg")
    if not ffprobe or not ffmpeg:
        return False, None

    current_duration = _probe_duration(ffprobe, target)
    normalize_frames = current_duration > 0 and _uses_short_audio_frames(ffprobe, target)
    if current_duration > 0 and not normalize_frames:
        return False, current_duration

    temporary = target.with_name(f".{target.name}.playback-{uuid.uuid4().hex}.tmp")
    try:
        subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-y",
                "-fflags",
                "+genpts",
                "-i",
                str(target),
                "-map",
                "0:a:0",
                "-vn",
                "-af",
                "aresample=async=1:first_pts=0",
                "-c:a",
                "libopus",
                "-b:a",
                "128k",
                "-vbr",
                "on",
                "-compression_level",
                "10",
                "-frame_duration",
                "20",
                "-application",
                "audio",
                "-ar",
                "48000",
                "-f",
                "webm",
                str(temporary),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        optimized_duration = _probe_duration(ffprobe, temporary)
        if optimized_duration <= 0 or temporary.stat().st_size == 0:
            raise ValueError("오디오의 재생 정보를 복구하지 못했습니다.")
        shutil.copystat(target, temporary)
        temporary.replace(target)
        return True, optimized_duration
    finally:
        temporary.unlink(missing_ok=True)
