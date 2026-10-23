# Source from setup/build scripts; no Python or application packages required.
LECORDER_SCRIPT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LECORDER_ENV_PATH="${LECORDER_ENV_FILE:-$LECORDER_SCRIPT_ROOT/.env}"
[[ "$LECORDER_ENV_PATH" = /* ]] || LECORDER_ENV_PATH="$LECORDER_SCRIPT_ROOT/$LECORDER_ENV_PATH"
if [[ -f "$LECORDER_ENV_PATH" ]]; then
    set -a
    source "$LECORDER_ENV_PATH"
    set +a
fi
