#!/usr/bin/env bash
#
# git-apply.sh
#
# Purpose:
#   Normalize a GPT-provided unified diff against the current repo root,
#   validate it, dry-run it, and apply it safely.
#
# Usage:
#   tools/git/git-apply.sh /path/to/patch.diff
#   tools/git/git-apply.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"
DEFAULT_DOWNLOADS_DIR="/mnt/c/home/user/Downloads"

source "${SCRIPT_DIR}/support/git-write-lock.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage:
  git-apply.sh <diff-file>
  git-apply.sh

Behavior:
  - When no diff file is provided, auto-selects the newest .patch/.diff file
    from /mnt/c/home/user/Downloads
  - Validates the diff file exists
  - Normalizes GPT-style wrong path prefixes against the current repo root
  - Dry-runs `git apply` first
  - Falls back to a 3-way dry-run when needed
  - Applies the patch and reports the files that changed
  - Deletes the patch file after a successful apply
USAGE
  exit 0
fi

reexec_with_git_write_lock "${SCRIPT_PATH}" "$@"

pick_latest_patch() {
  python - <<'PY'
from pathlib import Path

downloads = Path("/mnt/c/home/user/Downloads")
if not downloads.exists():
    raise SystemExit(1)
candidates = []
for pattern in ("*.patch", "*.diff", "*.PATCH", "*.DIFF"):
    for path in downloads.glob(pattern):
        if path.is_file():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            candidates.append((mtime, str(path)))
if not candidates:
    raise SystemExit(1)
candidates.sort()
print(candidates[-1][1])
PY
}

ARGS=("$@")
PATCH_PATH=""
for arg in "${ARGS[@]}"; do
  if [[ "${arg}" != --* ]]; then
    PATCH_PATH="${arg}"
    break
  fi
done

if [[ -z "${PATCH_PATH}" ]]; then
  if ! PATCH_PATH="$(pick_latest_patch)"; then
    echo "ERROR: No patch file argument provided and no .patch/.diff file found in ${DEFAULT_DOWNLOADS_DIR}." >&2
    exit 2
  fi
  echo "[INFO] auto-selected patch ${PATCH_PATH}"
  ARGS+=("${PATCH_PATH}")
fi

python -m tools.git.support.git_apply "${ARGS[@]}"

if [[ -f "${PATCH_PATH}" ]]; then
  rm -f "${PATCH_PATH}"
  echo "[OK] deleted applied patch ${PATCH_PATH}"
fi
