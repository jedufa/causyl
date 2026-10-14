#!/bin/bash
#
# git-commit-tag.sh
#
# Purpose:
#   Actor-facing git entrypoint: auto-pick the next baseline-NNN id, commit
#   from the pending journal, and create a matching annotated tag from the
#   resulting HEAD commit message.
#
# Usage:
#   tools/git/git-commit-tag.sh
#   tools/git/git-commit-tag.sh <one line message...>
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
SCRIPT_PATH="${SCRIPT_DIR}/$(basename "${BASH_SOURCE[0]}")"

# Serialize write-side git operations across shared worktrees.
source "${SCRIPT_DIR}/support/git-write-lock.sh"

if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
  cat <<'USAGE'
Usage:
  git-commit-tag.sh
  git-commit-tag.sh --debug
  git-commit-tag.sh --weekly
  git-commit-tag.sh <one line message...>

Behavior:
  - Finds the highest existing baseline-NNN tag
  - Increments it to the next baseline id
  - Always runs support/git-commit-from-journal.sh with that baseline id
  - With message args, uses them as the commit subject override while still
    composing the body from the journal/worktree path and consuming any
    pending journal entries
  - Runs support/git-tag-from-head.sh with the same baseline id
  - Optional archive flags run infra/bin/archive_complete_repo_snapshot.sh
    after the commit + tag step so the snapshot captures the new baseline tag
USAGE
  exit 0
fi

reexec_with_git_write_lock "${SCRIPT_PATH}" "$@"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

active_git_processes() {
  ps -eo pid=,stat=,comm=,args= | awk '
    $3 == "git" && $2 !~ /Z/ { print }
  '
}

clear_stale_index_lock() {
  local lock_path="${REPO_ROOT}/.git/index.lock"
  [[ -e "${lock_path}" ]] || return 0

  local active_git
  active_git="$(active_git_processes)"
  if [[ -n "${active_git}" ]]; then
    {
      echo "ERROR: Git index lock exists at ${lock_path} and active git processes were detected:"
      printf '%s\n' "${active_git}"
    } >&2
    exit 1
  fi

  rm -f "${lock_path}"
  echo "[WARN] removed stale git index lock ${lock_path}"
}

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

next_baseline_id() {
  local max_id
  max_id="$(
    git tag -l 'baseline-[0-9][0-9][0-9]' \
      | sed -n 's/^baseline-\([0-9]\{3\}\)$/\1/p' \
      | sort -n \
      | tail -n 1
  )"

  if [[ -z "${max_id}" ]]; then
    echo "001"
    return 0
  fi

  local next=$((10#${max_id} + 1))
  if (( next > 999 )); then
    die "Next baseline id would be ${next}, exceeding 999."
  fi
  printf "%03d" "${next}"
}

cd "${REPO_ROOT}"
clear_stale_index_lock

CUSTOM_MESSAGE=""
ARCHIVE_PROFILE=""
ARCHIVE_CONFIG=""
FORWARD_ARGS=()

while [[ $# -gt 0 ]]; do
  case "$1" in
    --debug)
      [[ -z "${ARCHIVE_PROFILE}" ]] || die "Specify at most one archive profile flag."
      ARCHIVE_PROFILE="debug"
      shift
      ;;
    --weekly)
      [[ -z "${ARCHIVE_PROFILE}" ]] || die "Specify at most one archive profile flag."
      ARCHIVE_PROFILE="weekly"
      shift
      ;;
    --config)
      [[ $# -ge 2 ]] || die "--config requires a value."
      ARCHIVE_CONFIG="$2"
      shift 2
      ;;
    --)
      shift
      while [[ $# -gt 0 ]]; do
        FORWARD_ARGS+=("$1")
        shift
      done
      ;;
    *)
      FORWARD_ARGS+=("$1")
      shift
      ;;
  esac
done

if [[ ${#FORWARD_ARGS[@]} -gt 0 ]]; then
  CUSTOM_MESSAGE="${FORWARD_ARGS[*]}"
fi

verify_commit_journal_preflight

run_with_progress() {
  local label="$1"
  shift
  local interval_sec="${GIT_COMMIT_TAG_PROGRESS_SEC:-20}"

  echo "[INFO] starting ${label}"
  "$@" &
  local child_pid=$!
  local started_at
  started_at="$(date +%s)"

  while kill -0 "${child_pid}" 2>/dev/null; do
    sleep "${interval_sec}" &
    local sleep_pid=$!
    wait "${sleep_pid}" 2>/dev/null || true
    if ! kill -0 "${child_pid}" 2>/dev/null; then
      break
    fi
    local now elapsed
    now="$(date +%s)"
    elapsed=$((now - started_at))
    echo "[INFO] ${label} still running (${elapsed}s elapsed)"
  done

  wait "${child_pid}"
  local rc=$?
  if [[ ${rc} -ne 0 ]]; then
    echo "[FAIL] ${label} failed (rc=${rc})" >&2
    return "${rc}"
  fi
  local finished_at elapsed_total
  finished_at="$(date +%s)"
  elapsed_total=$((finished_at - started_at))
  echo "[OK] ${label} finished (${elapsed_total}s)"
}

ID="baseline-$(next_baseline_id)"
echo "[INFO] using ${ID}"

if [[ -n "${CUSTOM_MESSAGE}" ]]; then
  echo "[INFO] using subject override plus journal compose"
  run_with_progress "journal commit ${ID}" bash "${SCRIPT_DIR}/support/git-commit-from-journal.sh" --subject-override "${CUSTOM_MESSAGE}" "${ID}" || {
    rc=$?
    if [[ ${rc} -eq 3 ]]; then
      echo "[OK] nothing to commit; skipped commit and tag for ${ID}"
      exit 0
    fi
    exit "${rc}"
  }
else
  run_with_progress "journal commit ${ID}" bash "${SCRIPT_DIR}/support/git-commit-from-journal.sh" "${ID}" || {
    rc=$?
    if [[ ${rc} -eq 3 ]]; then
      echo "[OK] nothing to commit; skipped commit and tag for ${ID}"
      exit 0
    fi
    exit "${rc}"
  }
fi
run_with_progress "annotated tag ${ID}" bash "${SCRIPT_DIR}/support/git-tag-from-head.sh" "${ID}"

if [[ -n "${ARCHIVE_PROFILE}" ]]; then
  archive_args=()
  if [[ "${ARCHIVE_PROFILE}" == "debug" ]]; then
    archive_args+=(--debug)
  elif [[ "${ARCHIVE_PROFILE}" == "weekly" ]]; then
    archive_args+=(--weekly)
  fi
  if [[ -n "${ARCHIVE_CONFIG}" ]]; then
    archive_args+=(--config "${ARCHIVE_CONFIG}")
  fi
  run_with_progress "snapshot archive ${ID}" bash "${REPO_ROOT}/infra/bin/archive_complete_repo_snapshot.sh" "${archive_args[@]}"
fi

echo "[OK] committed and tagged ${ID}"
