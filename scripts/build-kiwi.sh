#!/bin/bash
set -euo pipefail
source "$(dirname "$0")/build-common.sh"
SOURCE="$DEPS/kiwi"
REVISION=280302af5ff9e739bec1ce56d6df5f9436ac41d4
CMAKE="$(command -v cmake)"
if [[ ! -d "$SOURCE/.git" ]]; then
  git_plain clone https://github.com/bab2min/Kiwi.git "$SOURCE"
fi
if [[ -n "$(git_plain -C "$SOURCE" status --porcelain --untracked-files=no -- . ':!models/cong/base')" ]]; then
  echo 'Kiwi 소스에 변경 사항이 있습니다. 보존을 위해 빌드를 중단합니다.' >&2; exit 1
fi
if [[ "$(git_plain -C "$SOURCE" rev-parse HEAD)" != "$REVISION" ]]; then
  ensure_revision "$SOURCE" "$REVISION"
fi
git_plain -C "$SOURCE" submodule update --init --recursive
"$CMAKE" -S "$ROOT/scripts/kiwi" -B "$DEPS/kiwi/build" -DCMAKE_BUILD_TYPE=Release -DKIWI_SOURCE="$SOURCE"
"$CMAKE" --build "$DEPS/kiwi/build" --target kiwi-helper -j "$JOBS"
mkdir -p "$SOURCE/bin"
cp "$DEPS/kiwi/build/kiwi-helper" "$DEPS/kiwi/bin/kiwi-helper"
[[ -x "$SOURCE/bin/kiwi-helper" ]]
