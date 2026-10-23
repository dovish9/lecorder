#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-env.sh"
DEPS="${LECORDER_DEPENDENCIES_DIR:-$ROOT/dependencies}"
[[ "$DEPS" = /* ]] || DEPS="$ROOT/$DEPS"
case "${1:-}" in
  --dry-run) [[ $# -eq 1 ]] || { echo '사용법: bash scripts/setup.sh [--dry-run]' >&2; exit 2; }; exec bash "$ROOT/scripts/setup-plan.sh" ;;
  '') ;;
  *) echo '사용법: bash scripts/setup.sh [--dry-run]' >&2; exit 2 ;;
esac
source "$ROOT/scripts/python-runtime.sh"
STAGE=1
trap 'echo "설치 실패: [$STAGE/5]. 위 오류를 해결한 뒤 bash scripts/setup.sh를 다시 실행하세요." >&2' ERR
step() { STAGE="$1"; printf '\n[%s/5] %s\n' "$1" "$2"; }
step 1 'Xcode Command Line Tools · Homebrew 확인'
[[ "$(uname -s)" == Darwin ]] || { echo '현재 설치 스크립트는 macOS용입니다.' >&2; exit 1; }
xcode-select -p >/dev/null 2>&1 && xcrun --find clang >/dev/null 2>&1 && xcrun --find swiftc >/dev/null 2>&1 || {
  echo 'Xcode Command Line Tools가 필요합니다. xcode-select --install 실행 후 다시 시작하세요.' >&2; exit 1;
}
git --version >/dev/null 2>&1 || { echo 'Git이 필요합니다. Xcode Command Line Tools 설치를 확인하세요.' >&2; exit 1; }
command -v brew >/dev/null || { echo 'Homebrew를 먼저 설치하세요: https://brew.sh' >&2; exit 1; }
select_python install
if [[ -n "$PYTHON" ]]; then check_python_version; fi
step 2 'Homebrew 개발 도구 확인 · 누락 항목 설치'
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_INSTALL_UPGRADE=1
# Keep the formula list separate from child stdin; brew may read stdin.
while IFS= read -r -u 3 formula; do
  if [[ "$formula" == python && -n "$PYTHON" ]]; then
    echo "기존 Python 사용: $PYTHON"
    continue
  fi
  if brew list --versions "$formula" </dev/null 3<&- >/dev/null 2>&1; then
    echo "기존 설치 사용: $formula"
  else
    brew install "$formula" </dev/null 3<&-
  fi
done 3< <(sed -n 's/^brew "\([^"]*\)".*/\1/p' "$ROOT/Brewfile")
if [[ -z "$PYTHON" ]]; then
  PYTHON="$(brew --prefix python)/libexec/bin/python3"
fi
for formula in cmake; do
  prefix="$(brew --prefix "$formula")"
  [[ -x "$prefix/bin/$formula" ]] || {
    echo "설치된 실행 파일을 찾을 수 없습니다: $prefix/bin/$formula" >&2; exit 1;
  }
  export PATH="$prefix/bin:$PATH"
done
hash -r
"$PYTHON" --version
"$(brew --prefix cmake)/bin/cmake" --version
step 3 'Python 선택 · 패키지 설치'
check_python_version
echo "패키지 설치에 사용할 Python: $PYTHON ($PYTHON_SOURCE)"
PACKAGE_PATH=""
if ! "$PYTHON" -B -c 'import pathlib, sys, sysconfig; sys.exit(1 if sys.prefix == sys.base_prefix and (pathlib.Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists() else 0)'; then
  PACKAGE_PATH="$DEPS/python/site-packages"
  echo "관리되는 Python: 패키지는 $PACKAGE_PATH 에 설치합니다."
  mkdir -p "$PACKAGE_PATH"
  "$PYTHON" -m pip install --upgrade --target "$PACKAGE_PATH" -r requirements.txt
  export PYTHONPATH="$PACKAGE_PATH${PYTHONPATH:+:$PYTHONPATH}"
else
  "$PYTHON" -m pip install -r requirements.txt
fi
"$PYTHON" -m pip check
"$PYTHON" "$ROOT/scripts/check-install.py" --packages-only
step 4 'Whisper → llama.cpp → Kiwi → Vision 빌드'
bash scripts/build-whisper.sh
bash scripts/build-llama.sh
bash scripts/build-kiwi.sh
bash scripts/build-vision.sh
step 5 '모델 · 데이터 다운로드 및 설치 확인'
"$PYTHON" scripts/download-models.py
"$PYTHON" scripts/check-install.py
mkdir -p "$DEPS/python"
printf '%s\n%s\n' "$PYTHON" "$PACKAGE_PATH" > "$DEPS/python/runtime.tmp"
mv "$DEPS/python/runtime.tmp" "$DEPS/python/runtime"
printf '\n설치 완료: 5단계 검증을 통과했습니다. 저장 위치를 설정한 뒤 ./start.command를 실행하세요.\n'
