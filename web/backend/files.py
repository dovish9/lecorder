from __future__ import annotations

import re
from pathlib import Path

from .transcription import SUPPORTED_MEDIA


def safe_name(value: str) -> str:
    value = re.sub(r"[\\/:*?\"<>|\x00-\x1f]", "-", value.strip())
    return re.sub(r"\s+", " ", value).strip(" .")[:160]


class OutputFiles:
    """Allocate non-destructive audio/Markdown names inside one output folder."""

    def __init__(self, folder: Path):
        self.folder = folder.expanduser().resolve(strict=True)
        if not self.folder.is_dir():
            raise ValueError("저장 위치가 폴더가 아닙니다.")

    def available_title(self, title: str, reserved: set[str] | None = None) -> str:
        occupied = {name.casefold() for name in (reserved or set())}
        occupied.update(
            path.stem.casefold() for path in self.folder.iterdir()
            if path.suffix.lower() in SUPPORTED_MEDIA or path.suffix.lower() == ".md"
        )
        candidate = title
        number = 2
        while candidate.casefold() in occupied:
            candidate = f"{title} ({number})"
            number += 1
        return candidate

    def pair(self, title: str, extension: str) -> tuple[Path, Path]:
        candidate = self.available_title(title)
        return self.folder / f"{candidate}{extension}", self.folder / f"{candidate}.md"

    def audio(self, title: str, extension: str) -> Path:
        candidate = self.available_title(title)
        return self.folder / f"{candidate}{extension}"

    def note(self, title: str) -> Path:
        candidate = self.folder / f"{title}.md"
        number = 2
        while candidate.exists():
            candidate = self.folder / f"{title} ({number}).md"
            number += 1
        return candidate
