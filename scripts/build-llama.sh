#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
LINK="$DEPS/llama.cpp"
if [[ -e "$LINK" ]]; then
  DEST="$(cd "$LINK" && pwd -P)"
  if [[ -n "${LECORDER_LLAMA_DIR:-}" && "$DEST" != "$(cd "$LECORDER_LLAMA_DIR" && pwd -P)" ]]; then
    echo '다른 llama.cpp 설치가 있습니다. 기존 경로를 보존합니다.' >&2; exit 1
  fi
elif [[ -L "$LINK" ]]; then
  echo 'llama.cpp 링크 대상이 없습니다. 링크를 복구한 뒤 다시 실행하세요.' >&2; exit 1
else
  DEST="${LECORDER_LLAMA_DIR:-$HOME/Local Storage/llama.cpp}"
fi
REVISION="$(cat "$ROOT/scripts/llama/revision")"
if [[ ! -d "$DEST/.git" ]]; then git_plain clone https://github.com/ggml-org/llama.cpp.git "$DEST"; fi
if [[ -n "$(git_plain -C "$DEST" status --porcelain --untracked-files=no)" ]]; then
  echo 'llama.cpp에 로컬 수정이 있습니다. 원본을 보존하고 빌드를 중단합니다.' >&2; exit 1
fi
ensure_revision "$DEST" "$REVISION"
cmake -S "$DEST" -B "$DEST/build" -DCMAKE_BUILD_TYPE=Release -DGGML_METAL=ON \
  -DLLAMA_OPENSSL=OFF -DLLAMA_BUILD_BORINGSSL=OFF -DLLAMA_BUILD_LIBRESSL=OFF
cmake --build "$DEST/build" --config Release --target llama-server llama-bench -j "$JOBS"
[[ -x "$DEST/build/bin/llama-server" && -x "$DEST/build/bin/llama-bench" ]]
[[ -e "$LINK" ]] || ln -s "$DEST" "$LINK"
echo "llama.cpp 빌드 완료: $DEST"
