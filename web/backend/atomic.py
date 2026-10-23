"""Close temporary files before replacement (required on Windows)."""
import os
import tempfile
import stat
from pathlib import Path


def clear_hidden_flag(path: Path) -> None:
    """Make a published macOS file visible after replacing a dot-prefixed temp file."""
    flags = getattr(path.stat(), "st_flags", 0)
    hidden = getattr(stat, "UF_HIDDEN", 0)
    change_flags = getattr(os, "chflags", None)
    if hidden and flags & hidden and change_flags:
        change_flags(path, flags & ~hidden)


def publish_file(source: Path, destination: Path) -> None:
    """Atomically publish a staged file and preserve public output visibility."""
    source.replace(destination)
    if not destination.name.startswith('.'):
        clear_hidden_flag(destination)


def write_text(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        publish_file(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
