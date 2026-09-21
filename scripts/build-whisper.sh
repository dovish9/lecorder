#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/dependencies/whisper.cpp"
mkdir -p "$ROOT/dependencies"
REVISION="$(cat "$ROOT/scripts/whisper/revision")"
if [[ ! -d "$DEST/.git" ]]; then git clone https://github.com/ggml-org/whisper.cpp.git "$DEST"; fi
if [[ "$(git -C "$DEST" rev-parse HEAD)" != "$REVISION" ]]; then
  [[ -z "$(git -C "$DEST" status --porcelain)" ]] || { echo '기존 변경이 있는 Whisper 경로는 변경하지 않습니다.'; exit 1; }
  git -C "$DEST" checkout "$REVISION"
fi
if git -C "$DEST" apply --check "$ROOT/scripts/whisper/server.patch" 2>/dev/null; then
  git -C "$DEST" apply "$ROOT/scripts/whisper/server.patch"
elif ! git -C "$DEST" apply --reverse --check "$ROOT/scripts/whisper/server.patch" 2>/dev/null; then
  echo 'Whisper 패치가 현재 소스와 호환되지 않습니다.'; exit 1
fi
cmake -S "$DEST" -B "$DEST/build" -DGGML_METAL=ON
cmake --build "$DEST/build" --config Release -j 4
[[ -f "$DEST/models/ggml-large-v3.bin" ]] || bash "$DEST/models/download-ggml-model.sh" large-v3
[[ -f "$DEST/models/ggml-silero-v6.2.0.bin" ]] || bash "$DEST/models/download-vad-model.sh" silero-v6.2.0
echo "Whisper 준비 완료: $DEST"
