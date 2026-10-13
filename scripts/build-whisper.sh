#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
DEST="$DEPS/whisper.cpp"
REVISION="$(cat "$ROOT/scripts/whisper/revision")"
if [[ ! -d "$DEST/.git" ]]; then git_plain clone https://github.com/ggml-org/whisper.cpp.git "$DEST"; fi
if [[ "$(git -C "$DEST" rev-parse HEAD)" != "$REVISION" ]]; then
  [[ -z "$(git -C "$DEST" status --porcelain)" ]] || { echo '기존 변경이 있는 Whisper 경로는 변경하지 않습니다.'; exit 1; }
  ensure_revision "$DEST" "$REVISION"
fi
if ! git -C "$DEST" diff --quiet HEAD && ! cmp -s <(git -C "$DEST" diff --no-ext-diff HEAD) "$ROOT/scripts/whisper/server.patch"; then
  echo 'Whisper에 설치 패치 이외의 수정이 있습니다. 원본을 보존하고 중단합니다.' >&2; exit 1
fi
if git -C "$DEST" apply --check "$ROOT/scripts/whisper/server.patch" 2>/dev/null; then
  git -C "$DEST" apply "$ROOT/scripts/whisper/server.patch"
elif ! git -C "$DEST" apply --reverse --check "$ROOT/scripts/whisper/server.patch" 2>/dev/null; then
  echo 'Whisper 패치가 현재 소스와 호환되지 않습니다.'; exit 1
fi
cmake -S "$DEST" -B "$DEST/build" -DGGML_METAL=ON
cmake --build "$DEST/build" --config Release -j "$JOBS"
[[ -x "$DEST/build/bin/whisper-server" ]]
echo "Whisper 준비 완료: $DEST"
