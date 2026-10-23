#!/bin/bash
# Read-only preview: no installation, build, network request, or server startup.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/load-env.sh"
DEPS="${LECORDER_DEPENDENCIES_DIR:-$ROOT/dependencies}"
[[ "$DEPS" = /* ]] || DEPS="$ROOT/$DEPS"
export HOMEBREW_NO_AUTO_UPDATE=1 HOMEBREW_NO_ANALYTICS=1
printf '설치 미리보기: 변경을 실행하지 않습니다.\n\n[1/5] 필수 도구\n'
for tool in xcode-select xcrun git brew; do
  if command -v "$tool" >/dev/null 2>&1; then
    echo "발견: $tool — $(command -v "$tool")"
  else
    echo "필수 도구 누락: $tool"
  fi
done
if ! xcode-select -p >/dev/null 2>&1 || ! xcrun --find clang >/dev/null 2>&1 || ! xcrun --find swiftc >/dev/null 2>&1; then
  echo '개발 도구 확인 실패: 실제 설치는 1단계에서 중단됩니다.'
fi
source "$ROOT/scripts/python-runtime.sh"
select_python plan
printf '\n[2/5] Homebrew 설치 계획\n'
while IFS= read -r -u 3 formula; do
  if [[ "$formula" == python && "$PYTHON_SOURCE" != homebrew ]]; then
    echo "Python 선택: $PYTHON_SOURCE ${PYTHON:-— ${LECORDER_PYTHON_VERSION:-3.13} 설치 예정}"
  elif command -v brew >/dev/null 2>&1 && brew list --versions "$formula" </dev/null 3<&- >/dev/null 2>&1; then
    echo "재사용: $formula"
  else
    echo "설치 예정: $formula (필요한 의존성이 함께 설치될 수 있습니다.)"
  fi
done 3< <(sed -n 's/^brew "\([^"]*\)".*/\1/p' "$ROOT/Brewfile")
printf '\n[3/5] Python 패키지\n'
if [[ -n "$PYTHON" ]]; then
  if check_python_version; then
    echo "Python 재사용: $PYTHON"
    if ! "$PYTHON" -B -c 'import pathlib, sys, sysconfig; sys.exit(1 if sys.prefix == sys.base_prefix and (pathlib.Path(sysconfig.get_path("stdlib")) / "EXTERNALLY-MANAGED").exists() else 0)'; then
      echo "패키지 설치 예정 위치: $DEPS/python/site-packages"
    fi
  else
    echo '호환되지 않는 Python: 실제 설치는 중단됩니다.'
  fi
else
  if [[ "$PYTHON_SOURCE" == pyenv ]]; then
    echo "Python 신규 설치 예정: pyenv ${LECORDER_PYTHON_VERSION:-3.13}"
  else
    echo "Python 신규 설치 예정: Homebrew 최신 안정 버전 (python)"
  fi
  if [[ "$PYTHON_SOURCE" == homebrew ]]; then echo "패키지 설치 예정 위치: $DEPS/python/site-packages"; fi
fi
echo "설치·검사 대상: $ROOT/requirements.txt (현재 충족 여부와 다운로드는 실행하지 않음)"
printf '\n[4/5] 네이티브 빌드\n'
for tool in whisper llama kiwi vision; do echo "실행 예정: scripts/build-$tool.sh"; done
printf '\n[5/5] 모델 및 최종 검사\n'
if [[ -n "$PYTHON" ]] && "$PYTHON" -B -c 'import sys; assert sys.version_info[:2] >= (3,11)' >/dev/null 2>&1; then
  "$PYTHON" -B scripts/check-install.py --plan
else
  echo 'Whisper·VAD·LLM·이미지 projector·Kiwi·토크나이저: Python 준비 후 파일별 검증 예정'
fi
echo '실행 파일 --help 검사·패키지 import·모델 체크섬 검사 예정'
echo '미리보기 완료 — 설치 성공이나 모델 유효성을 확인한 결과는 아닙니다.'
