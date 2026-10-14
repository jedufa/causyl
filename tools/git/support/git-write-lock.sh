#!/usr/bin/env bash
#
# git-write-lock.sh
#
# Purpose:
#   Shared helper for serializing write-side git operations across the main
#   worktree and linked worktrees that share the same git common dir.
#

git_write_lock_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || return 1
}

git_write_lock_common_dir() {
  local common_dir
  if common_dir="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"; then
    printf '%s\n' "${common_dir}"
    return 0
  fi

  local repo_root raw_dir
  repo_root="$(git_write_lock_repo_root)" || return 1
  raw_dir="$(git rev-parse --git-common-dir 2>/dev/null)" || return 1
  python - "${repo_root}" "${raw_dir}" <<'PY'
from pathlib import Path
import sys

repo_root = Path(sys.argv[1])
raw_dir = Path(sys.argv[2])
common_dir = raw_dir if raw_dir.is_absolute() else (repo_root / raw_dir)
print(common_dir.resolve())
PY
}

git_write_lock_path() {
  local common_dir
  common_dir="$(git_write_lock_common_dir)" || return 1
  printf '%s/eiel-git-write.lock\n' "${common_dir}"
}

reexec_with_git_write_lock() {
  local script_path="$1"
  shift

  if [[ "${EIEL_GIT_WRITE_LOCK_HELD:-0}" == "1" ]]; then
    return 0
  fi

  local lock_path timeout_s conflict_rc
  lock_path="$(git_write_lock_path)" || return 0
  timeout_s="${EIEL_GIT_WRITE_LOCK_TIMEOUT_S:-900}"
  conflict_rc=73

  mkdir -p "$(dirname "${lock_path}")"
  echo "[INFO] waiting for repo git write lock ${lock_path}"

  if env EIEL_GIT_WRITE_LOCK_HELD=1 EIEL_GIT_WRITE_LOCK_PATH="${lock_path}" \
    flock -E "${conflict_rc}" -w "${timeout_s}" "${lock_path}" bash "${script_path}" "$@"; then
    exit 0
  fi

  local rc=$?
  if [[ ${rc} -eq ${conflict_rc} ]]; then
    echo "ERROR: timed out after ${timeout_s}s waiting for repo git write lock ${lock_path}" >&2
  fi
  exit "${rc}"
}
