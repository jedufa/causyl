#!/usr/bin/env bash
#
# git-tag.sh
#
# Purpose:
#   Internal support wrapper for annotated git tags with robust multi-line
#   messages (via stdin) and CRLF normalization.
#
# Usage:
#   ./git-tag.sh v1.2.3 "Short message"
#   ./git-tag.sh v1.2.3 < tag_message.txt
#   ./git-tag.sh v1.2.3 <<'EOF'
#   Release v1.2.3
#
#   - Item 1
#   - Item 2
#   EOF
#
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"

# Serialize write-side git operations across shared worktrees.
source "${SCRIPT_DIR}/git-write-lock.sh"
# -----------------------------------------------------------------------------
# Pretty console output (pure ANSI). Disable via: EIEL_COLOR=0
# -----------------------------------------------------------------------------
EIEL_COLOR="${EIEL_COLOR:-1}"
if [[ -t 1 && "${EIEL_COLOR}" == "1" ]]; then
  _EIEL_RESET=$'\033[0m'
  _EIEL_BOLD=$'\033[1m'
  _EIEL_RED=$'\033[31m'
  _EIEL_GREEN=$'\033[32m'
  _EIEL_YELLOW=$'\033[33m'
  _EIEL_BLUE=$'\033[34m'
  _EIEL_CYAN=$'\033[36m'
  _EIEL_MAGENTA=$'\033[35m'
else
  _EIEL_RESET=''
  _EIEL_BOLD=''
  _EIEL_RED=''
  _EIEL_GREEN=''
  _EIEL_YELLOW=''
  _EIEL_BLUE=''
  _EIEL_CYAN=''
  _EIEL_MAGENTA=''
fi

section(){ echo; echo "${_EIEL_BOLD}${_EIEL_CYAN}========== $* ==========${_EIEL_RESET}"; }
step(){ section "$@"; }
info(){ echo "${_EIEL_BLUE}[INFO]${_EIEL_RESET} $*"; }
ok(){ echo "${_EIEL_GREEN}[OK]${_EIEL_RESET} $*"; }
warn(){ echo "${_EIEL_YELLOW}[WARN]${_EIEL_RESET} $*"; }
fail(){ echo "${_EIEL_RED}[FAIL]${_EIEL_RESET} $*"; }
die(){ fail "$*"; exit 1; }


_usage() {
  cat <<'USAGE'
Usage:
  git-tag.sh <TAG> [message words...]
  git-tag.sh <TAG> < message_from_stdin

Behavior:
  - If stdin is provided (piped/heredoc/file), the entire stdin becomes the tag message.
  - Otherwise, the remaining CLI arguments become a one-line tag message.
  - Any carriage returns (CR, "\r") are stripped to avoid CRLF issues.
USAGE
}

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  _usage
  exit 0
fi

if [[ $# -lt 1 ]]; then
  _usage
  exit 2
fi

reexec_with_git_write_lock "${SCRIPT_PATH}" "$@"

TAG="$1"
shift

MESSAGE=""
if [[ ! -t 0 ]]; then
  MESSAGE="$(cat)"
else
  if [[ $# -lt 1 ]]; then
    fail "No tag message provided. Provide message words or pipe stdin." >&2
    exit 2
  fi
  MESSAGE="$*"
fi

MESSAGE="${MESSAGE//$'\r'/}"

TMPFILE="$(mktemp -t git-tag-msg.XXXXXX)"
trap 'rm -f "$TMPFILE"' EXIT

printf '%s' "$MESSAGE" > "$TMPFILE"

git tag -a "$TAG" -F "$TMPFILE"
#git tag --sort=-creatordate
