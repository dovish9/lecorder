#!/bin/bash

set -u

SCRIPT_ROOT="$(cd "$(dirname "$0")" && pwd)"
if [ -f "$SCRIPT_ROOT/.env" ]; then
    set -a
    source "$SCRIPT_ROOT/.env"
    set +a
fi
ROOT="${LECORDER_DIR:-$SCRIPT_ROOT}"
if [[ "$ROOT" != /* ]]; then
    ROOT="$SCRIPT_ROOT/$ROOT"
fi
if [ ! -f "$ROOT/app.py" ]; then
    ROOT="$SCRIPT_ROOT"
fi
APP_PORT="${LECORDER_PORT:-5055}"
RUNTIME_FILE="$SCRIPT_ROOT/dependencies/python/runtime"
if [[ -f "$RUNTIME_FILE" ]]; then
    { IFS= read -r PYTHON_BIN; IFS= read -r PACKAGE_PATH || true; } < "$RUNTIME_FILE"
    if [[ -n "$PACKAGE_PATH" ]]; then
        export PYTHONPATH="$PACKAGE_PATH${PYTHONPATH:+:$PYTHONPATH}"
    fi
else
    source "$SCRIPT_ROOT/scripts/python-runtime.sh"
    select_python plan || exit 1
    PYTHON_BIN="$PYTHON"
fi
APP_PID=""
CLEANED=0

fail() { echo "❌ $1"; exit 1; }

active() {
    [ -n "$1" ] || return 1
    STATE="$(ps -o stat= -p "$1" 2>/dev/null || true)"
    [ -n "$STATE" ] && [[ "$STATE" != *Z* ]]
}

stop() {
    [ "$CLEANED" -eq 0 ] || return
    CLEANED=1
    trap - EXIT INT TERM HUP
    echo ""
    [ -n "$APP_PID" ] || return
    echo "🛑 Lecorder와 앱이 시작한 로컬 AI 서버를 종료합니다..."
    for PID in "$APP_PID"; do
        if active "$PID"; then kill -TERM "$PID" 2>/dev/null || true; fi
    done
    for _ in $(seq 1 50); do
        ANY_ACTIVE=0
        for PID in "$APP_PID"; do
            if active "$PID"; then ANY_ACTIVE=1; fi
        done
        [ "$ANY_ACTIVE" -eq 0 ] && break
        sleep 0.1
    done
    FORCED=0
    for PID in "$APP_PID"; do
        if active "$PID"; then
            FORCED=1
            kill -KILL "$PID" 2>/dev/null || true
        fi
        [ -z "$PID" ] || wait "$PID" 2>/dev/null || true
    done
    if [ "$FORCED" -eq 1 ]; then
        echo "⚠️ 종료 응답이 없어 앱을 강제 종료했습니다. 다음 시작 시 작업 상태를 확인하세요."
    else
        echo "✅ Lecorder를 종료했습니다. 사용자 지정 AI 서버는 종료하지 않습니다."
    fi
}

on_signal() {
    exit 0
}

wait_for() {
    PORT="$1"
    LABEL="$2"
    for _ in $(seq 1 120); do
        active "$APP_PID" || fail "$LABEL 프로세스가 종료되었습니다. 위의 오류를 확인하세요."
        /usr/bin/nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1 && return 0
        sleep 0.25
    done
    fail "$LABEL 서버가 시작되지 않았습니다."
}

trap stop EXIT
trap on_signal INT TERM HUP

[ -f "$ROOT/app.py" ] || fail "app.py 없음: $ROOT/app.py"
[ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ] || fail "Python 3를 찾지 못했습니다. README의 Python 설치 단계를 실행하세요."
"$PYTHON_BIN" -c "import flask, requests, keyring, jsonschema, tiktoken, av, pypdfium2, PIL, pillow_heif, py_mini_racer, pdfplumber" >/dev/null 2>&1 \
    || fail "Python 패키지가 없습니다. README에 따라 requirements.txt를 설치하세요."
/usr/bin/nc -z 127.0.0.1 "$APP_PORT" >/dev/null 2>&1 && fail "$APP_PORT 포트를 이미 사용 중입니다."

echo "================================================="
echo "🎙️ Lecorder"
echo "================================================="

"$PYTHON_BIN" "$ROOT/scripts/startup_info.py" || fail "AI 설정을 읽지 못했습니다. 설정 파일과 위의 오류를 확인하세요."

echo "▶️ 대시보드와 대기열 시작"
LECORDER_PORT="$APP_PORT" LECORDER_MANAGED_RESTART=1 "$PYTHON_BIN" "$ROOT/app.py" &
APP_PID=$!
wait_for "$APP_PORT" "대시보드"

echo "ℹ️ 제공자·모델 변경: 환경설정 → 음성 전사 / LLM"
echo "ℹ️ AI 설정 변경은 새 작업부터 적용됩니다. 진행 중인 작업은 기존 설정을 유지합니다."
echo "✅ http://127.0.0.1:$APP_PORT"
echo "💡 종료: Ctrl+C"

if [ "${LECORDER_NO_BROWSER:-0}" != "1" ]; then
    open "http://127.0.0.1:$APP_PORT" >/dev/null 2>&1 || true
fi
wait "$APP_PID"
APP_STATUS=$?
if [ "$APP_STATUS" -eq 75 ]; then
    echo "🔄 새 경로 설정으로 서버를 다시 시작합니다."
    stop
    exec "$SCRIPT_ROOT/start.command"
fi
exit "$APP_STATUS"
