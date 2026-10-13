#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
mkdir -p "$DEPS/vision/bin"
xcrun swiftc "$ROOT/scripts/native/vision.swift" -module-cache-path "$DEPS/vision/swift-cache" -O -framework Vision -framework AppKit -o "$DEPS/vision/bin/vision-ocr"
[[ -x "$DEPS/vision/bin/vision-ocr" ]]
echo 'Vision helper 빌드 완료'
