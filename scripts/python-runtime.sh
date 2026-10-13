# Shared Python selection. Source only; select_python plan never installs.
select_python() {
  local mode="${1:-plan}" version root candidate name
  PYTHON=""; PYTHON_SOURCE=""
  if command -v pyenv >/dev/null 2>&1; then
    PYTHON_SOURCE=pyenv
    root="$(pyenv root)" || return 1
    version="$(pyenv versions --bare | awk '/^3\.13\.[0-9]+$/' | sort -t . -k3,3n | tail -1)"
    if [[ -z "$version" && "$mode" == install ]]; then
      pyenv install -s 3.13 >&2 || return 1
      version="$(pyenv versions --bare | awk '/^3\.13\.[0-9]+$/' | sort -t . -k3,3n | tail -1)"
    fi
    if [[ -n "$version" ]]; then
      PYTHON="$root/versions/$version/bin/python3"
      [[ -x "$PYTHON" ]] || { echo "pyenv 실행 파일 누락: $PYTHON" >&2; return 1; }
    elif [[ "$mode" == install ]]; then
      echo 'pyenv Python 3.13 설치를 확인하세요.' >&2; return 1
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
