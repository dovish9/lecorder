#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SOURCE="$ROOT/dependencies/kiwi"
REVISION=280302af5ff9e739bec1ce56d6df5f9436ac41d4
CMAKE="$(brew --prefix cmake)/bin/cmake"
command -v git-lfs >/dev/null || { echo 'brew install git-lfs 필요'; exit 1; }
if [[ ! -d "$SOURCE/.git" ]]; then
  GIT_LFS_SKIP_SMUDGE=1 git clone https://github.com/bab2min/Kiwi.git "$SOURCE"
fi
if [[ -n "$(git -C "$SOURCE" status --porcelain --untracked-files=no)" ]]; then
  echo 'Kiwi 소스에 변경 사항이 있습니다. 보존을 위해 빌드를 중단합니다.' >&2; exit 1
fi
GIT_LFS_SKIP_SMUDGE=1 git -C "$SOURCE" checkout --detach "$REVISION"
git -C "$SOURCE" submodule update --init --recursive
git -C "$SOURCE" lfs pull --include='models/cong/base/*'
"$CMAKE" -S "$ROOT/scripts/kiwi" -B "$ROOT/dependencies/kiwi/build" -DCMAKE_BUILD_TYPE=Release -DKIWI_SOURCE="$SOURCE"
"$CMAKE" --build "$ROOT/dependencies/kiwi/build" --target kiwi-helper -j 4
mkdir -p "$SOURCE/bin"
cp "$ROOT/dependencies/kiwi/build/kiwi-helper" "$ROOT/dependencies/kiwi/bin/kiwi-helper"
