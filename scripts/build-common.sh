# Shared paths for build scripts. Source only; no downloads or global Git edits.
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT/scripts/load-env.sh"
DEPS="${LECORDER_DEPENDENCIES_DIR:-$ROOT/dependencies}"
[[ "$DEPS" = /* ]] || DEPS="$ROOT/$DEPS"
JOBS="${LECORDER_BUILD_JOBS:-4}"
[[ "$JOBS" =~ ^[0-9]+$ && "$JOBS" -ge 1 && "$JOBS" -le 128 ]] || { echo "LECORDER_BUILD_JOBS에는 1~128의 정수를 지정하세요." >&2; return 1; }
if [[ -n "${LECORDER_LLAMA_DIR:-}" && "$LECORDER_LLAMA_DIR" != /* ]]; then
    LECORDER_LLAMA_DIR="$ROOT/$LECORDER_LLAMA_DIR"
fi
mkdir -p "$DEPS"
export GIT_LFS_SKIP_SMUDGE=1
# Models are downloaded and verified in stage 5, even when Git LFS is installed.
git_plain() { git -c filter.lfs.process= -c filter.lfs.smudge=cat -c filter.lfs.clean=cat -c filter.lfs.required=false "$@"; }
ensure_revision() {
  if ! git_plain -C "$1" cat-file -e "$2^{commit}" 2>/dev/null; then git_plain -C "$1" fetch origin "$2"; fi
  if [[ "$(git_plain -C "$1" rev-parse HEAD)" != "$2" ]]; then git_plain -C "$1" checkout --detach "$2"; fi
}
