# Shared Python selection. Source only; select_python plan never installs.
select_python() {
  local mode="${1:-plan}" version root candidate name target
  PYTHON=""; PYTHON_SOURCE=""
  target="${LECORDER_PYTHON_VERSION:-3.13}"
  [[ "$target" =~ ^3\.(1[1-9]|[2-9][0-9])$ ]] || { echo "LECORDER_PYTHON_VERSION: 3.11 이상의 minor 버전이 필요합니다." >&2; return 1; }
  if command -v pyenv >/dev/null 2>&1; then
    PYTHON_SOURCE=pyenv
    root="$(pyenv root)" || return 1
    version="$(pyenv versions --bare | awk -v target="$target" 'index($0,target ".")==1 && /^[0-9]+\.[0-9]+\.[0-9]+$/' | sort -t . -k3,3n | tail -1)"
    if [[ -z "$version" && "$mode" == install ]]; then
      pyenv install -s "$target" >&2 || return 1
      version="$(pyenv versions --bare | awk -v target="$target" 'index($0,target ".")==1 && /^[0-9]+\.[0-9]+\.[0-9]+$/' | sort -t . -k3,3n | tail -1)"
    fi
    if [[ -n "$version" ]]; then
      PYTHON="$root/versions/$version/bin/python3"
      [[ -x "$PYTHON" ]] || { echo "pyenv 실행 파일 누락: $PYTHON" >&2; return 1; }
    elif [[ "$mode" == install ]]; then
      echo "pyenv Python $target 설치를 확인하세요." >&2; return 1
    fi
    return 0
  fi
  for name in python3 python; do
    candidate="$(command -v "$name" || true)"
    if [[ -n "$candidate" ]] && "$candidate" -B -c 'import sys; assert sys.version_info.major == 3' >/dev/null 2>&1; then
      PYTHON="$candidate"; PYTHON_SOURCE=existing; return 0
    fi
  done
  PYTHON_SOURCE=homebrew
}

check_python_version() {
  "$PYTHON" -B -c 'import sys; assert sys.version_info >= (3,11)' || {
    echo '기존 Python이 너무 오래됐습니다. Python 3.11 이상이 필요하며, 권장 버전은 3.13입니다.' >&2; return 1;
  }
}
