#!/usr/bin/env bash
#
# git-commit.sh
#
# Purpose:
#   Internal support wrapper for git commit with a numeric ID prefix and
#   support for multi-line commit messages via stdin. Normalizes CRLF by
#   stripping carriage returns. Stages ALL changes automatically using git add
#   -A.
#
# Usage:
#   tools/git-commit.sh <ID> <one line message...>
#   tools/git-commit.sh <ID> < multi_line_message.txt
#   tools/git-commit.sh <ID> <<'EOF'
#   Subject line
#
#   Body...
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

list_modified_tracked_ignored_paths() {
  local tmpfile
  tmpfile="$(mktemp -t git-commit-ignored.XXXXXX)"
  local status path code tracked_path
  while IFS= read -r -d '' status; do
    [[ -n "${status}" ]] || continue
    [[ ${#status} -ge 4 ]] || continue
    code="${status:0:2}"
    path="${status:3}"
    tracked_path="${path}"
    if [[ "${code}" == *R* || "${code}" == *C* ]]; then
      IFS= read -r -d '' tracked_path || true
    fi
    [[ "${code}" == *"?"* ]] && continue
    printf '%s\0' "${tracked_path}" >> "${tmpfile}"
  done < <(git status --porcelain=v1 -z --untracked-files=all)

  if [[ ! -s "${tmpfile}" ]]; then
    rm -f "${tmpfile}"
    return 0
  fi

  local rc=0
  local ignored_match=""
  while IFS= read -r -d '' tracked_path; do
    [[ -n "${tracked_path}" ]] || continue
    if [[ "${tracked_path}" == runtime/state/commit_journal/* ]]; then
      continue
    fi
    ignored_match="$(git ls-files -ci --exclude-standard -- "${tracked_path}" || true)"
    if [[ -n "${ignored_match}" ]]; then
      printf '%s\n' "${tracked_path}"
      rc=1
      break
    fi
  done < "${tmpfile}"
  rm -f "${tmpfile}"
  return "${rc}"
}

list_verify_syntax_targets() {
  local status code path target_path
  while IFS= read -r -d '' status; do
    [[ -n "${status}" ]] || continue
    [[ ${#status} -ge 4 ]] || continue
    code="${status:0:2}"
    path="${status:3}"
    target_path="${path}"
    if [[ "${code}" == *R* || "${code}" == *C* ]]; then
      IFS= read -r -d '' target_path || true
    fi
    if [[ "${code}" == *D* ]]; then
      continue
    fi
    if [[ "${target_path}" != *.py && "${target_path}" != *.sh ]]; then
      continue
    fi
    if [[ -e "${target_path}" ]]; then
      printf '%s\0' "${target_path}"
    fi
  done < <(git status --porcelain=v1 -z --untracked-files=all)
}

renormalize_staged_paths() {
  local tmpfile
  tmpfile="$(mktemp -t git-commit-renorm.XXXXXX)"
  trap 'rm -f "$TMPFILE" "$tmpfile"' EXIT
  git diff --cached --name-only --diff-filter=ACMR -z > "$tmpfile"
  if [[ ! -s "$tmpfile" ]]; then
    rm -f "$tmpfile"
    trap 'rm -f "$TMPFILE"' EXIT
    return 0
  fi
  git add --renormalize --pathspec-from-file="$tmpfile" --pathspec-file-nul
  rm -f "$tmpfile"
  trap 'rm -f "$TMPFILE"' EXIT
}


_usage() {
  cat <<'USAGE'
Usage:
  git-commit.sh <ID> [message words...]
  git-commit.sh <ID> < message_from_stdin

Behavior:
  - Runs: git add -A (stages all changes, including deletions and new files)
  - If stdin is provided (piped/heredoc/file), the entire stdin becomes the commit message.
  - Otherwise, remaining CLI arguments become a one-line commit message.
  - Prefixes the first line with "<ID> ".
  - Strips carriage returns (CR, "\r") to avoid CRLF issues.
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

ID="$1"
shift

# Read commit message from explicit CLI args first; otherwise fall back to stdin.
MESSAGE=""
if [[ $# -ge 1 ]]; then
  MESSAGE="$*"
elif [[ ! -t 0 ]]; then
  MESSAGE="$(cat)"
else
  fail "No commit message provided. Provide message words or pipe stdin." >&2
  exit 2
fi

# Normalize CRLF: strip carriage returns.
MESSAGE="${MESSAGE//$'\r'/}"

if [[ -z "${MESSAGE}" ]]; then
  fail "Empty commit message." >&2
  exit 2
fi

# Prefix only the first line with ID.
FIRST_LINE="${MESSAGE%%$'\n'*}"
if [[ "${MESSAGE}" == *$'\n'* ]]; then
  REST="${MESSAGE#*$'\n'}"
  MESSAGE="${ID} ${FIRST_LINE}"$'\n'"${REST}"
else
  MESSAGE="${ID} ${MESSAGE}"
fi

# Ensure we run from repo root (so tools.verify_syntax is found reliably).
REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
if [[ -n "${REPO_ROOT}" ]]; then
  cd "${REPO_ROOT}"
fi

RECENT_REMOVAL_OVERRIDE="${PLANE_ALLOW_RECENT_REMOVAL_REUSE:-}"
if [[ -z "${RECENT_REMOVAL_OVERRIDE}" ]]; then
  PRECHECK_JSON_FILE="$(mktemp -t plane-recent-removal-autoreview.XXXXXX)"
  trap 'rm -f "$TMPFILE" "$PRECHECK_JSON_FILE"' EXIT
  set +e
  python tools/bin/plane_recent_removal_autoreview.py > "${PRECHECK_JSON_FILE}"
  PRECHECK_RC=$?
  set -e
  PRECHECK_OUTPUT="$(cat "${PRECHECK_JSON_FILE}" 2>/dev/null || true)"
  if [[ ${PRECHECK_RC} -ne 0 ]]; then
    printf '%s\n' "${PRECHECK_OUTPUT}" >&2
    die "Recent-removal Codex review did not approve this critical-path commit."
  fi
  PARSED_REASON="$(
    python - "${PRECHECK_JSON_FILE}" <<'PY'
import json
import sys
path = sys.argv[1]
try:
    raw = open(path, "r", encoding="utf-8").read().strip()
except OSError:
    raw = ""
try:
    data = json.loads(raw) if raw else {}
except Exception:
    data = {}
if str(data.get("status") or "").strip() == "approved":
    print(str(data.get("reason") or "").strip())
else:
    print("")
PY
  )"
  if [[ -n "${PARSED_REASON}" ]]; then
    RECENT_REMOVAL_OVERRIDE="${PARSED_REASON}"
    info "Codex approved recent-removal reuse: ${RECENT_REMOVAL_OVERRIDE}"
  fi
fi

if [[ -n "${RECENT_REMOVAL_OVERRIDE}" ]]; then
  TRAILER="ALLOW-RECENT-REMOVAL-REUSE: ${RECENT_REMOVAL_OVERRIDE}"
  if [[ "${MESSAGE}" != *"${TRAILER}"* ]]; then
    MESSAGE="${MESSAGE}"$'\n\n'"${TRAILER}"
  fi
fi

VERIFY_TARGETS=()
while IFS= read -r -d '' path; do
  [[ -n "${path}" ]] || continue
  VERIFY_TARGETS+=("${path}")
done < <(list_verify_syntax_targets)

if (( ${#VERIFY_TARGETS[@]} > 0 )); then
  step "VERIFY SYNTAX (and line endings)"
  VERIFY_ARGS=(--fix)
  for path in "${VERIFY_TARGETS[@]}"; do
    VERIFY_ARGS+=(--file "${path}")
  done
  python -m tools.scripts.verify_syntax "${VERIFY_ARGS[@]}"
else
  info "Skipping syntax verification: no changed .py or .sh files."
fi

TRACKED_IGNORED_MODIFIED="$(list_modified_tracked_ignored_paths || true)"
if [[ -n "${TRACKED_IGNORED_MODIFIED}" ]]; then
  fail "Refusing commit because modified tracked files now match .gitignore:"
  printf '%s\n' "${TRACKED_IGNORED_MODIFIED}" >&2
  die "Clean these tracked-ignored paths first (for example with git rm --cached if they should stop being tracked)."
fi

# Stage everything first (captures deletes/renames), then renormalize line endings.
git add -A
renormalize_staged_paths
step "COMMIT"
TMPFILE="$(mktemp -t git-commit-msg.XXXXXX)"
trap 'rm -f "$TMPFILE"' EXIT
printf '%s' "$MESSAGE" > "$TMPFILE"

git commit -F "$TMPFILE"
