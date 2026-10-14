#!/usr/bin/env bash
#
# git-commit-journal-view.sh
#
# Purpose:
#   Render pending commit-journal entries in a pager-friendly terminal view.
#
# Usage:
#   tools/git/support/git-commit-journal-view.sh
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage:
  git-commit-journal-view.sh

Behavior:
  - Renders all pending commit journal entries as readable text
  - Opens the result in a terminal pager when available
USAGE
  exit 0
fi

if [[ $# -ne 0 ]]; then
  echo "Usage: git-commit-journal-view.sh" >&2
  exit 2
fi

cd "${REPO_ROOT}"

if [[ -t 0 && -t 1 ]] && command -v less >/dev/null 2>&1; then
  python -m tools.git.support.commit_journal view | less
else
  python -m tools.git.support.commit_journal view
fi
