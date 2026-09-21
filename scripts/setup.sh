#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
command -v brew >/dev/null || { echo 'Homebrew를 먼저 설치하세요: https://brew.sh'; exit 1; }
brew bundle --file="$ROOT/Brewfile"
if [[ "${1:-}" == "--pptx" ]]; then brew install --cask libreoffice; fi
PYTHON="$(brew --prefix python@3.13)/bin/python3.13"
[[ -x .venv/bin/python ]] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
mkdir -p dependencies
xcrun swiftc scripts/native/vision.swift -module-cache-path dependencies/swift-cache -O -framework Vision -framework AppKit -o dependencies/vision-ocr
bash scripts/build-kiwi.sh
.venv/bin/python scripts/doctor.py
