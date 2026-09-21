from __future__ import annotations

import os
import re
import shlex
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = SCRIPT_ROOT / ".env"
LANGUAGES = {"ko", "en", "auto"}


def resolve_path(value: str, base: Path = SCRIPT_ROOT) -> Path:
    """Resolve configured relative paths from the project root."""
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = base / path
    return path.resolve()


def text(value: Any, limit: int) -> str:
    return str(value or "").strip()[:limit]


def flag(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _read_env(path: Path = ENV_FILE) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return values
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        try:
            parsed = shlex.split(raw_value.strip(), posix=True)
            values[key] = parsed[0] if parsed else ""
        except ValueError:
            values[key] = raw_value.strip().strip('"\'')
    return values


def _load_process_env() -> None:
    for key, value in _read_env().items():
        os.environ.setdefault(key, value)


_load_process_env()

ROOT = resolve_path(os.getenv("LECORDER_DIR", "."))
if not (ROOT / "app.py").is_file():
    ROOT = SCRIPT_ROOT
DB_FILE = ROOT / "web" / "data" / "lecorder.db"

APP_PORT = int(os.getenv("LECORDER_PORT", "5055"))
WHISPER_PORT = int(os.getenv("WHISPER_PORT", "8080"))
WHISPER_URL = os.getenv("WHISPER_URL", f"http://127.0.0.1:{WHISPER_PORT}/inference")
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://127.0.0.1:11434/api/chat")
NOTE_VISION_MODEL = os.getenv("NOTE_VISION_MODEL", "qwen3.5:9b")
DEFAULT_LLM_MODEL = os.getenv("OLLAMA_MODEL", "qwen3:8b-q4_K_M")


@dataclass(frozen=True)
class Environment:
    project_dir: str
    whisper_cpp_dir: str
    output_dir: str

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        project = Path(self.project_dir).expanduser()
        whisper = Path(self.whisper_cpp_dir).expanduser()
        output = Path(self.output_dir).expanduser()
        data["checks"] = {
            "project": (project / "app.py").is_file() and (project / "web/backend").is_dir(),
            "whisper": (whisper / "build/bin/whisper-server").is_file(),
            "model": (whisper / "models/ggml-large-v3.bin").is_file(),
            "vad_model": (whisper / "models/ggml-silero-v6.2.0.bin").is_file(),
            "output": output.is_dir(),
        }
        return data


class EnvironmentStore:
    """Persist dashboard-editable path variables in the project's .env file."""

    KEYS = {
        "project_dir": "LECORDER_DIR",
        "output_dir": "LECORDER_OUTPUT_DIR",
    }

    def __init__(self, path: Path = ENV_FILE, script_root: Path = SCRIPT_ROOT):
        self.path = path
        self.script_root = script_root
        self._lock = threading.RLock()

    def get(self) -> Environment:
        values = _read_env(self.path)
        project = resolve_path(values.get("LECORDER_DIR", os.getenv("LECORDER_DIR", ".")), self.script_root)
        return Environment(
            project_dir=str(project),
            whisper_cpp_dir=str(project / "dependencies" / "whisper.cpp"),
            output_dir=str(resolve_path(values.get(
                "LECORDER_OUTPUT_DIR",
                os.getenv("LECORDER_OUTPUT_DIR", "output"),
            ), self.script_root)),
        )

    def update(self, **changes: Any) -> Environment:
        with self._lock:
            current = self.get()
            clean: dict[str, str] = {}
            for field in self.KEYS:
                value = text(changes.get(field, getattr(current, field)), 2000)
                if not value:
                    raise ValueError("경로를 비워 둘 수 없습니다.")
                clean[field] = str(resolve_path(value, self.script_root))

            project = Path(clean["project_dir"])
            if not (project / "app.py").is_file() or not (project / "web/backend").is_dir():
                raise ValueError("프로젝트 경로에서 app.py와 web/backend 폴더를 찾지 못했습니다.")
            if not Path(clean["output_dir"]).is_dir():
                raise ValueError("파일 저장 경로가 존재하는 폴더가 아닙니다.")

            updated = replace(current, **clean, whisper_cpp_dir=str(project / "dependencies" / "whisper.cpp"))
            managed_values = {
                env_key: getattr(updated, field) for field, env_key in self.KEYS.items()
            }
            existing = _read_env(self.path)
            preserved = {
                key: value for key, value in existing.items()
                if key not in managed_values
                and key not in {"OLLAMA_MODEL", "WHISPER_CPP_DIR"}
                and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key)
            }
            lines = [
                "# Lecorder paths — dashboard managed",
                *(f"{env_key}={shlex.quote(value)}" for env_key, value in managed_values.items()),
                f"OLLAMA_MODEL={shlex.quote(existing.get('OLLAMA_MODEL', DEFAULT_LLM_MODEL))}",
                *(f"{key}={shlex.quote(value)}" for key, value in sorted(preserved.items())),
                "",
            ]
            temporary = self.path.with_suffix(".env.tmp")
            temporary.write_text("\n".join(lines), encoding="utf-8")
            temporary.replace(self.path)
            return updated
