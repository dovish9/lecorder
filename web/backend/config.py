from __future__ import annotations

import os
import re
import shlex
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any
from web.backend.atomic import write_text


SCRIPT_ROOT = Path(__file__).resolve().parents[2]
ENV_FILE = (SCRIPT_ROOT / os.getenv("LECORDER_ENV_FILE", ".env")).expanduser().resolve()
if os.getenv('LECORDER_ENV_FILE') and not ENV_FILE.is_file():
    raise ValueError(f'선택한 환경설정 파일이 없습니다: {ENV_FILE}')
_ENV_LOCK = threading.RLock()
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
            lexer = shlex.shlex(raw_value.strip(), posix=True)
            lexer.whitespace_split = True
            lexer.escape = ""  # paths are data, not shell commands (C:\Users\...)
            parsed = list(lexer)
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
from web.backend.runtime_config import get as runtime, validate as validate_runtime
validate_runtime()
DATA_DIR = resolve_path(runtime('LECORDER_DATA_DIR'), ROOT)
DEPENDENCIES_DIR = resolve_path(runtime('LECORDER_DEPENDENCIES_DIR'), ROOT)
DB_FILE = DATA_DIR / 'lecorder.db'
APP_PORT = runtime('LECORDER_PORT')
WHISPER_URL = runtime('LECORDER_SPEECH_BASE_URL').rstrip('/') + '/inference'
DEFAULT_LLM_MODEL = runtime('LECORDER_LLM_MODEL')


def write_env(path, values, *, remove=()):
    """Update just the requested keys without destroying category comments."""
    with _ENV_LOCK:
        path = Path(path)
        lines = path.read_text(encoding='utf-8').splitlines() if path.exists() else []
        lines = [line for line in lines if not any(re.match(r'^\s*'+re.escape(key)+r'\s*=',line) for key in remove)]
        remaining = dict(values)
        for index, line in enumerate(lines):
            match = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=', line)
            if match and match[1] in values:
                lines[index] = f'{match[1]}={shlex.quote(str(values[match[1]]))}'
                remaining.pop(match[1], None)
        if remaining:
            lines.extend(['', '# 화면에서 저장한 설정', *(f'{key}={shlex.quote(str(value))}' for key,value in remaining.items())])
        write_text(path, '\n'.join(lines) + '\n')



@dataclass(frozen=True)
class Environment:
    project_dir: str
    whisper_cpp_dir: str
    output_dir: str

    def public(self) -> dict[str, Any]:
        data = asdict(self)
        project = Path(self.project_dir).expanduser()
        output = Path(self.output_dir).expanduser()
        data["checks"] = {
            "project": (project / "app.py").is_file() and (project / "web/backend").is_dir(),
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
        self._active = None

    def freeze(self):
        self._active = self.saved()

    def get(self) -> Environment:
        return self._active or self.saved()

    def saved(self) -> Environment:
        values = _read_env(self.path)
        project = resolve_path(values.get("LECORDER_DIR", os.getenv("LECORDER_DIR", ".")), self.script_root)
        return Environment(
            project_dir=str(project),
            whisper_cpp_dir=str(resolve_path(values.get("LECORDER_DEPENDENCIES_DIR", os.getenv("LECORDER_DEPENDENCIES_DIR", "dependencies")), project) / "whisper.cpp"),
            output_dir=str(resolve_path(values.get(
                "LECORDER_OUTPUT_DIR",
                runtime("LECORDER_OUTPUT_DIR"),
            ), self.script_root)),
        )

    def update(self, **changes: Any) -> Environment:
        with self._lock:
            current = self.saved()
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

            dependency = _read_env(self.path).get("LECORDER_DEPENDENCIES_DIR", os.getenv("LECORDER_DEPENDENCIES_DIR", "dependencies"))
            updated = replace(current, **clean, whisper_cpp_dir=str(resolve_path(dependency, project) / "whisper.cpp"))
            managed_values = {
                env_key: getattr(updated, field) for field, env_key in self.KEYS.items()
            }
            write_env(self.path, managed_values)
            return updated
