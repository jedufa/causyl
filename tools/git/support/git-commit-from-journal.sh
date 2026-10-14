#!/usr/bin/env bash
#
# git-commit-from-journal.sh
#
# Purpose:
#   Internal support wrapper: compose a commit message from pending
#   commit-journal fragments, commit using the existing git-commit.sh workflow,
#   and archive the consumed fragments.
#
# Usage:
#   tools/git/support/git-commit-from-journal.sh <ID>
#   tools/git/support/git-commit-from-journal.sh --subject-override "..." <ID>
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"

# Serialize write-side git operations across shared worktrees.
source "${SCRIPT_DIR}/git-write-lock.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage:
  git-commit-from-journal.sh <ID>
  git-commit-from-journal.sh --subject-override "..." <ID>

Behavior:
  - Renders a commit message from runtime/state/commit_journal/pending/*.json
    and, by default, synthesizes a message from changed files when the
    pending journal is empty or incomplete
  - Optional subject override replaces the composed subject line while keeping
    the journal-composed body and consume/archive flow
  - Commits via tools/git/support/git-commit.sh using the rendered message
  - Archives exactly the consumed fragments after commit success

Environment:
  - GIT_COMMIT_FROM_JOURNAL_STRICT=1
      require pending journal fragments and disable changed-file fallback
USAGE
  exit 0
fi

SUBJECT_OVERRIDE=""
POSITIONAL=()
while [[ $# -gt 0 ]]; do
  case "$1" in
    --subject-override)
      [[ $# -ge 2 ]] || { echo "Usage: git-commit-from-journal.sh [--subject-override \"...\"] <ID>" >&2; exit 2; }
      SUBJECT_OVERRIDE="$2"
      shift 2
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        POSITIONAL+=("$1")
        shift
      done
      ;;
    *)
      POSITIONAL+=("$1")
      shift
      ;;
  esac
done

if [[ ${#POSITIONAL[@]} -ne 1 ]]; then
  echo "Usage: git-commit-from-journal.sh [--subject-override \"...\"] <ID>" >&2
  exit 2
fi

REEXEC_ARGS=()
if [[ -n "${SUBJECT_OVERRIDE}" ]]; then
  REEXEC_ARGS+=(--subject-override "${SUBJECT_OVERRIDE}")
fi
REEXEC_ARGS+=("${POSITIONAL[@]}")
reexec_with_git_write_lock "${SCRIPT_PATH}" "${REEXEC_ARGS[@]}"

ID="${POSITIONAL[0]}"
MSG_FILE="$(mktemp -t commit-journal-msg.XXXXXX)"
PLAN_FILE=""
NOOP_EXIT=0

verify_commit_journal_preflight() {
  local output
  if output="$(python -m tools.git.support.commit_journal status 2>&1)"; then
    return 0
  fi

  if [[ "${GIT_COMMIT_JOURNAL_AUTO_REPAIR:-1}" == "1" ]]; then
    echo "[WARN] commit journal preflight detected invalid fragments; attempting auto-repair via quarantine." >&2
    python -m tools.git.support.commit_journal doctor --quarantine-invalid >/dev/null 2>&1 || true
    if output="$(python -m tools.git.support.commit_journal status 2>&1)"; then
      echo "[OK] commit journal auto-repair succeeded." >&2
      return 0
    fi
  fi

  {
    echo "[FAIL] commit journal preflight failed."
    echo "${output}"
    echo "[HINT] Fix invalid fragments under runtime/state/commit_journal/pending/ and retry."
    echo "[HINT] Set GIT_COMMIT_JOURNAL_AUTO_REPAIR=1 to enable auto-quarantine self-heal."
  } >&2
  exit 1
}

_cleanup() {
  local rc=$?
  if [[ ${NOOP_EXIT} -eq 1 ]]; then
    rm -f "${MSG_FILE}"
    return
  fi
  if [[ $rc -ne 0 && -n "${PLAN_FILE}" && -f "${MSG_FILE}" ]]; then
    local preserved_msg="${PLAN_FILE%.json}.message.txt"
    cp "${MSG_FILE}" "${preserved_msg}" 2>/dev/null || true
    echo "[WARN] commit-from-journal failed. Compose plan preserved at ${PLAN_FILE}" >&2
    echo "[WARN] rendered message preserved at ${preserved_msg}" >&2
  fi
  rm -f "${MSG_FILE}"
}
trap _cleanup EXIT

cd "${REPO_ROOT}"
verify_commit_journal_preflight
RENDER_ARGS=(
  --message-out "${MSG_FILE}"
  --print-plan-path
)
if [[ -n "${SUBJECT_OVERRIDE}" ]]; then
  RENDER_ARGS+=(--subject-override "${SUBJECT_OVERRIDE}")
fi
if [[ "${GIT_COMMIT_FROM_JOURNAL_STRICT:-0}" == "1" ]]; then
  RENDER_ARGS+=(--journal-only)
fi
set +e
RENDER_OUTPUT="$(python -m tools.git.support.commit_journal render \
  "${RENDER_ARGS[@]}" \
  2>&1 >/dev/null)"
RENDER_RC=$?
set -e
if [[ ${RENDER_RC} -ne 0 ]]; then
  if [[ "${RENDER_OUTPUT}" == *"no pending commit journal entries"* || "${RENDER_OUTPUT}" == *"no relevant changed files to reconstruct"* ]]; then
    NOOP_EXIT=1
    echo "[INFO] nothing to commit from journal or relevant worktree changes" >&2
    exit 3
  fi
  echo "${RENDER_OUTPUT}" >&2
  exit ${RENDER_RC}
fi
PLAN_FILE="${RENDER_OUTPUT}"
set +e
COMMIT_OUTPUT="$(
  bash "${SCRIPT_DIR}/git-commit.sh" "${ID}" < "${MSG_FILE}" 2>&1
)"
COMMIT_RC=$?
set -e
if [[ ${COMMIT_RC} -ne 0 ]]; then
  if [[ "${COMMIT_OUTPUT}" == *"nothing to commit, working tree clean"* ]]; then
    NOOP_EXIT=1
    echo "[INFO] nothing to commit after journal compose; working tree already clean" >&2
    exit 3
  fi
  printf '%s\n' "${COMMIT_OUTPUT}" >&2
  exit ${COMMIT_RC}
fi
COMMIT_SHA="$(git rev-parse HEAD)"
if ! python -m tools.git.support.commit_journal consume --plan "${PLAN_FILE}" --commit-sha "${COMMIT_SHA}" >/dev/null; then
  echo "[WARN] commit succeeded, but commit journal consume failed. Plan preserved at ${PLAN_FILE}" >&2
fi
