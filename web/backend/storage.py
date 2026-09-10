"""Read-only access to the user-selected output directory."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .transcription import SUPPORTED_MEDIA


def resolve_storage_file(folder: Path, name: str) -> Path:
    """Resolve one supported filename without allowing directory traversal."""
    if not name or name.startswith(".") or Path(name).name != name:
        raise ValueError("잘못된 파일 이름입니다.")
    root = folder.expanduser().resolve()
    candidate = (root / name).resolve()
    if candidate.parent != root:
        raise ValueError("저장 폴더 밖의 파일에는 접근할 수 없습니다.")
    if not candidate.is_file():
        raise FileNotFoundError("파일을 찾지 못했습니다.")
    if candidate.suffix.lower() != ".md" and candidate.suffix.lower() not in SUPPORTED_MEDIA:
        raise ValueError("지원하지 않는 파일 형식입니다.")
    return candidate


def _file_entry(path: Path) -> dict[str, Any] | None:
    # Lecorder uses dot-prefixed files for atomic conversion and note updates.
    # They can briefly exist while /api/storage is polled and must never become
    # user-facing library entries.
    if path.name.startswith(".") or not path.is_file():
        return None
    suffix = path.suffix.lower()
    if suffix != ".md" and suffix not in SUPPORTED_MEDIA:
        return None
    try:
        stat = path.stat()
    except OSError:
        return None
    return {
        "name": path.name,
        "stem": path.stem,
        "extension": suffix,
        "kind": "markdown" if suffix == ".md" else "audio",
        "size": stat.st_size,
        "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
    }


def _group_entries(files: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in files:
        grouped.setdefault(item["stem"], []).append(item)

    items: list[dict[str, Any]] = []
    paired_count = 0
    for stem, members in grouped.items():
        audio = [item for item in members if item["kind"] == "audio"]
        markdown = [item for item in members if item["kind"] == "markdown"]
        if audio and markdown:
            paired_count += 1
            items.append({
                "name": stem,
                "kind": "pair",
                "extensions": [item["extension"] for item in audio],
                "files": [item["name"] for item in members],
                "audio_files": [item["name"] for item in audio],
                "markdown_files": [item["name"] for item in markdown],
                "size": sum(item["size"] for item in members),
                "modified_at": max(item["modified_at"] for item in members),
            })
            continue
        items.extend({
            "name": item["stem"],
            "kind": item["kind"],
            "extensions": [item["extension"]],
            "files": [item["name"]],
            "audio_files": [item["name"]] if item["kind"] == "audio" else [],
            "markdown_files": [item["name"]] if item["kind"] == "markdown" else [],
            "size": item["size"],
            "modified_at": item["modified_at"],
        } for item in members)

    items.sort(key=lambda item: (item["modified_at"], item["name"]), reverse=True)
    return items, paired_count


def storage_catalog(folder: Path) -> dict[str, Any]:
    """Return a live catalog; no output-file metadata is cached in SQLite."""
    root = folder.expanduser()
    files = [entry for path in root.iterdir() if (entry := _file_entry(path))] if root.is_dir() else []
    files.sort(key=lambda item: (item["modified_at"], item["name"]), reverse=True)
    items, paired_count = _group_entries(files)
    return {
        "path": str(root),
        "exists": root.is_dir(),
        "files": files,
        "items": items,
        "audio_count": sum(item["kind"] == "audio" for item in files),
        "markdown_count": sum(item["kind"] == "markdown" for item in files),
        "paired_count": paired_count,
    }
