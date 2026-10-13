#!/usr/bin/env python3
"""Read-only dependency checks; never starts or stops a model server."""
import importlib.util
import json
import shutil
import subprocess
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from web.backend.config import EnvironmentStore, WHISPER_URL
from web.backend.platforms import host
import requests


def checks():
    root = Path(__file__).resolve().parents[1]
    output = {
        name: shutil.which(name)
        for name in (
            "tesseract",
        )
    }
    output["capabilities"] = host.capabilities()
    output["vision"] = (
        str(root / "dependencies/vision/bin/vision-ocr")
        if (root / "dependencies/vision/bin/vision-ocr").is_file()
        else None
    )
    output["kiwi"] = {
        "helper": host.executable(root / "dependencies/kiwi/bin", "kiwi-helper").is_file(),
        "model": (root / "dependencies/kiwi/models/cong/base/cong.mdl").is_file(),
    }
    output["python"] = {
        name: bool(importlib.util.find_spec(name)) for name in ("flask", "requests", "keyring", "jsonschema", "tiktoken", "av", "pypdfium2", "PIL", "pillow_heif", "py_mini_racer", "pdfplumber")
    }
    if output["tesseract"]:
        result = subprocess.run(
            [output["tesseract"], "--list-langs"], capture_output=True, text=True, encoding="utf-8", timeout=10, **host.process_options()
        )
        output["ocr_languages"] = {
            language: language in result.stdout.splitlines()
            for language in ("kor", "eng")
        }
    from web.backend.ai_settings import settings
    from web.backend.ai_providers import provider_status
    output["ai"] = {area: provider_status(area) for area in ('speech', 'llm')}
    output["llama_revision"] = (root / 'scripts/llama/revision').read_text().strip()
    home = Path(EnvironmentStore().get().whisper_cpp_dir)
    output["whisper"] = {
        name: (host.executable(home / "build/bin", "whisper-server") if name == "server" else home / path).is_file()
        for name, path in {
            "server": "build/bin/whisper-server",
            "model": "models/ggml-large-v3.bin",
            "vad": "models/ggml-silero-v6.2.0.bin",
        }.items()
    }
    try:
        response = requests.post(
            WHISPER_URL.rsplit("/", 1)[0] + "/tokenize",
            json={"text": "lecture"},
            timeout=1,
        )
        output["tokenizer"] = (
            "ready"
            if response.ok and isinstance(response.json().get("tokens"), int)
            else "unsupported"
        )
    except (requests.RequestException, ValueError):
        output["tokenizer"] = "server idle; checked at transcription time"
    return output


if __name__ == "__main__":
    print(json.dumps(checks(), ensure_ascii=False, indent=2))
