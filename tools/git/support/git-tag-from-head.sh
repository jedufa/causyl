#!/usr/bin/env bash
#
# git-tag-from-head.sh
#
# Purpose:
#   Internal support wrapper: create an annotated tag using the current HEAD
#   commit message as the tag annotation, so tagging can follow the
#   journal-driven commit flow without writing another message manually.
#
# Usage:
#   tools/git/support/git-tag-from-head.sh <TAG>
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"

# Serialize write-side git operations across shared worktrees.
source "${SCRIPT_DIR}/git-write-lock.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage:
  git-tag-from-head.sh <TAG>

Behavior:
  - Reads the current HEAD commit message
  - Uses that message as the annotation for git-tag.sh
USAGE
  exit 0
fi

if [[ $# -ne 1 ]]; then
  echo "Usage: git-tag-from-head.sh <TAG>" >&2
  exit 2
fi

reexec_with_git_write_lock "${SCRIPT_PATH}" "$@"

TAG="$1"
git log -1 --format=%B HEAD | bash "${SCRIPT_DIR}/git-tag.sh" "${TAG}"
