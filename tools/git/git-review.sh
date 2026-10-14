#!/usr/bin/env bash
#
# git-review.sh
#
# Purpose:
#   Run the standard commit-journal review sequence before a journal-based commit.
#
# Usage:
#   tools/git/git-review.sh [--view]
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
OPEN_VIEW=0

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage:
  git-review.sh [--view]

Behavior:
  - Runs commit_journal status
  - Runs commit_journal doctor
  - With --view, opens the pending journal view in the pager-backed journal viewer
USAGE
  exit 0
fi

while [[ $# -gt 0 ]]; do
  case "${1:-}" in
    --view)
      OPEN_VIEW=1
      shift
      ;;
    *)
      echo "Usage: git-review.sh [--view]" >&2
      exit 2
      ;;
  esac
done

cd "${REPO_ROOT}"

rc=0

python -m tools.git.support.commit_journal status || rc=$?
python -m tools.git.support.commit_journal doctor || rc=$?
python - <<'PY' || true
from tools.git.support import commit_journal

status = commit_journal.scan_pending_entries()
audit = commit_journal.audit_worktree_coverage()
if int(status.get("pending_count") or 0) == 0 and list(audit.get("changed_paths") or []):
    print("[INFO] No pending commit journal entries, but Git has modified files.")
    print("[INFO] Commit fallback can synthesize a message from changed file paths.")
    changed = list(audit.get("changed_paths") or [])
    preview = changed[:8]
    for path in preview:
        print(f"[INFO] changed: {path}")
    remaining = len(changed) - len(preview)
    if remaining > 0:
        print(f"[INFO] ... and {remaining} more changed path(s).")
PY

if [[ "${OPEN_VIEW}" == "1" ]]; then
  bash "${SCRIPT_DIR}/support/git-commit-journal-view.sh" || rc=$?
fi

exit "${rc}"
