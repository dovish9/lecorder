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
WHISPER_HOME="$ROOT/dependencies/whisper.cpp"
WHISPER_BIN="$WHISPER_HOME/build/bin/whisper-server"
WHISPER_MODEL="${WHISPER_MODEL_PATH:-$WHISPER_HOME/models/ggml-large-v3.bin}"
WHISPER_VAD_MODEL="${WHISPER_VAD_MODEL_PATH:-$WHISPER_HOME/models/ggml-silero-v6.2.0.bin}"
APP_PORT="${LECORDER_PORT:-5055}"
WHISPER_PORT="${WHISPER_PORT:-8080}"
if [ -x "$SCRIPT_ROOT/.venv/bin/python" ]; then
    PYTHON_BIN="$SCRIPT_ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="$(command -v python3)"
else
    PYTHON_BIN=""
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
    echo "🛑 Lecorder 종료 중..."
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
    for PID in "$APP_PID"; do
        if active "$PID"; then kill -KILL "$PID" 2>/dev/null || true; fi
        [ -z "$PID" ] || wait "$PID" 2>/dev/null || true
    done
    echo "✅ 안전하게 종료했습니다."
}

on_signal() {
    exit 0
}

wait_for() {
    PORT="$1"
    LABEL="$2"
    for _ in $(seq 1 120); do
        /usr/bin/nc -z 127.0.0.1 "$PORT" >/dev/null 2>&1 && return 0
        sleep 0.25
    done
    fail "$LABEL 서버가 시작되지 않았습니다."
}

trap stop EXIT
trap on_signal INT TERM HUP

[ -f "$ROOT/app.py" ] || fail "app.py 없음: $ROOT/app.py"
[ -n "$PYTHON_BIN" ] && [ -x "$PYTHON_BIN" ] || fail "Python 3를 찾지 못했습니다. README의 Python 설치 단계를 실행하세요."
"$PYTHON_BIN" -c "import flask, requests" >/dev/null 2>&1 \
    || fail "Python 패키지가 없습니다. README에 따라 requirements.txt를 설치하세요."
/usr/bin/nc -z 127.0.0.1 "$APP_PORT" >/dev/null 2>&1 && fail "$APP_PORT 포트를 이미 사용 중입니다."

echo "================================================="
echo "🎙️ Lecorder"
echo "================================================="

if ! command -v ffmpeg >/dev/null 2>&1; then
    echo "⚠️ FFmpeg 없음: 설치 전에는 전사할 수 없습니다."
elif [ ! -x "$WHISPER_BIN" ]; then
    echo "⚠️ whisper-server 없음: scripts/build-whisper.sh를 실행하세요."
elif [ ! -f "$WHISPER_MODEL" ]; then
    echo "⚠️ large-v3 모델 없음: scripts/build-whisper.sh를 실행하세요."
elif [ ! -f "$WHISPER_VAD_MODEL" ]; then
    echo "⚠️ Silero VAD 모델 없음: $WHISPER_VAD_MODEL"
else
    echo "ℹ️ Whisper는 전사 시작 시 로드하고 유휴 상태에서 자동 해제합니다."
fi

echo "▶️ 대시보드와 대기열 시작"
LECORDER_PORT="$APP_PORT" LECORDER_MANAGED_RESTART=1 "$PYTHON_BIN" "$ROOT/app.py" &
APP_PID=$!
wait_for "$APP_PORT" "대시보드"

if /usr/bin/nc -z 127.0.0.1 11434 >/dev/null 2>&1; then
    echo "✅ 대시보드 + Ollama 준비됨 (Whisper 대기)"
else
    echo "⚠️ Ollama 꺼짐: Qwen3 교정 구간은 Whisper 원문으로 저장됩니다."
fi
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
