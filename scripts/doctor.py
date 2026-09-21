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
import requests


def checks():
    root = Path(__file__).resolve().parents[1]
    output = {
        name: shutil.which(name)
        for name in (
            "ffmpeg",
            "pdftotext",
            "pdftoppm",
            "pdfinfo",
            "tesseract",
            "ollama",
            "soffice",
        )
    }
    if not output["soffice"]:
        office = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
        output["soffice"] = str(office) if office.is_file() else None
    output["vision"] = (
        str(root / "dependencies/vision-ocr")
        if (root / "dependencies/vision-ocr").is_file()
        else None
    )
    output["python"] = {
        name: bool(importlib.util.find_spec(name)) for name in ("flask", "requests")
    }
    if output["tesseract"]:
        result = subprocess.run(
            [output["tesseract"], "--list-langs"], capture_output=True, text=True
        )
        output["ocr_languages"] = {
            language: language in result.stdout.splitlines()
            for language in ("kor", "eng")
        }
    home = Path(EnvironmentStore().get().whisper_cpp_dir)
    output["whisper"] = {
        name: (home / path).is_file()
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
