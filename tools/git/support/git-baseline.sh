#!/usr/bin/env bash
# File: git-baseline.sh
# Purpose:
#   Create a baseline commit + annotated tag baseline-NNN.
#   Then (optionally) create a git worktree for that tag and ensure the shared venv is usable.
#
# Usage:
#   ./git-baseline.sh <message...>              # auto-increment NNN from existing baseline tags
#   ./git-baseline.sh <NNN> <message...>        # explicit override (backwards compatible)
#
# Tag format:
#   baseline-000 .. baseline-999 (3 digits)
#
# Behavior:
# - Refuses empty ceremonial commits (no changes and nothing staged)
# - Stages all changes
# - Commits with "NNN <message...>"
# - Creates annotated tag "baseline-NNN" with tag message "<message...>"
# - Pushes commit + tag
# - Creates a snapshot ZIP with a manifest
# - Creates a baseline worktree folder
# - Ensures shared venv exists (optional) and can install requirements into it
#
# Environment overrides:
#   CAUSYL_BASELINE_WORKTREES_DIR    Parent folder where baseline worktrees are created.
#                                     Default: parent directory of the current repo root.
#   CAUSYL_BASELINE_WORKTREE_PREFIX  Worktree folder prefix.
#                                     Default: causyl__baseline
#   CAUSYL_BASELINE_BOOTSTRAP_VENV   If set to '0', skip venv checks/creation/install.
#                                     Default: 1
#   CAUSYL_BASELINE_SHARED_VENV_PATH Shared venv path used by infra/bin/venv_activate.sh
#                                     Default: ~/venvs/causyl
#   CAUSYL_BASELINE_SHARED_VENV_INSTALL If '1', ensure requirements are installed into shared venv.
#                                     Default: 0 (but if venv is created, requirements are installed once)
#   CAUSYL_PIP_CACHE_DIR             Shared pip cache (speeds up installs).
#                                     Example: /mnt/d/WSL/pip_cache
#
# Notes:
#   - This script intentionally does NOT change engine/harness semantics.
#   - Baseline worktrees no longer create per-worktree .venv. Activation is via:
#       infra/bin/venv_activate.sh  (shared venv, interactive shell, WSL-friendly)
#
# Last modified: 2026-02-22 (Europe/Vienna)
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

usage() {
  cat >&2 <<'EOFU'
Usage:
  ./git-baseline.sh <message...>
  ./git-baseline.sh <NNN> <message...>

Environment overrides:
  CAUSYL_BASELINE_WORKTREES_DIR       Parent folder where baseline worktrees are created.
                                        Default: parent directory of the current repo root.
  CAUSYL_BASELINE_WORKTREE_PREFIX     Worktree folder prefix.
                                        Default: causyl__baseline
  CAUSYL_BASELINE_BOOTSTRAP_VENV      If set to '0', skip shared venv checks/creation/install.
                                        Default: 1
  CAUSYL_BASELINE_SHARED_VENV_PATH    Shared venv path used by infra/bin/venv_activate.sh.
                                        Default: ~/venvs/causyl
  CAUSYL_BASELINE_SHARED_VENV_INSTALL If '1', ensure requirements are installed into shared venv.
                                        Default: 0 (but if venv is created, requirements are installed once)
  CAUSYL_PIP_CACHE_DIR                Shared pip cache dir (optional).

Examples:
  ./git-baseline.sh Stable baseline before moving to Harness
  ./git-baseline.sh 064 Stable baseline before moving to Harness
EOFU
}

reexec_with_git_write_lock "${SCRIPT_PATH}" "$@"

repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || die "Not inside a git working tree."
}

ensure_nonempty_tree_at_head() {
  if [[ "${CAUSYL_ALLOW_EMPTY_TREE:-}" == "1" ]]; then
    return 0
  fi
  local n
  n="$(git ls-tree --name-only HEAD | wc -l | tr -d '[:space:]')"
  if [[ "${n}" == "0" ]]; then
    die "HEAD has an empty tree (0 tracked files). Refusing baseline tag/worktree. If intentional, set CAUSYL_ALLOW_EMPTY_TREE=1."
  fi
}

is_3digit_id() {
  [[ "${1:-}" =~ ^[0-9]{3}$ ]]
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

# ---------------------------------------------------------------------
# Snapshot support
# ---------------------------------------------------------------------

TMP_LIST=""
TMP_DIR=""

cleanup() {
  [[ -n "${TMP_LIST:-}" && -f "${TMP_LIST:-}" ]] && rm -f "${TMP_LIST}"
  [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR:-}" ]] && rm -rf "${TMP_DIR}"
}
trap cleanup EXIT

build_file_list() {
  local out_list="$1"
  local EXCLUDE_DIR_REGEX='^(doc/|\.venv/|__pycache__/|runs/|archives/|Backups/|\.bootstrap/|\.worktrees?/)' 

  git ls-files --cached --others --exclude-standard \
    | grep -Ev "${EXCLUDE_DIR_REGEX}" \
    | LC_ALL=C sort \
    > "${out_list}"

  local count
  count="$(wc -l < "${out_list}" | tr -d '[:space:]')"
  [[ "${count}" -ge 5 ]] || die "Snapshot file list unexpectedly small (${count} files)."
}

write_manifest() {
  local tmp_dir="$1"
  local zip_basename="$2"
  local ts="$3"
  local file_count="$4"
  local tag="$5"

  local manifest_path="${tmp_dir}/SNAPSHOT_MANIFEST.txt"
  local commit_short
  commit_short="$(git rev-parse --short=12 HEAD)"

  local dirty="false"
  if ! git diff --quiet || ! git diff --cached --quiet; then
    dirty="true"
  fi

  {
    echo "Causyl snapshot manifest"
    echo "zip_filename: ${zip_basename}"
    echo "created_at: ${ts}"
    echo "repo_root: $(pwd)"
    echo "branch: $(git rev-parse --abbrev-ref HEAD)"
    echo "requested_baseline_tag: ${tag}"
    echo "tag_at_HEAD: ${tag}"
    echo "tag_message:"
    git for-each-ref "refs/tags/${tag}" --format="%(contents)" || echo "n/a"
    echo "describe: $(git describe --tags --always --dirty 2>/dev/null || echo 'n/a')"
    echo "commit: $(git rev-parse HEAD)"
    echo "commit_short: ${commit_short}"
    echo "head_commit_subject:"
    git log -1 --pretty=%s
    echo "head_commit_body:"
    git log -1 --pretty=%b || true
    echo "dirty: ${dirty}"
    echo "file_count: ${file_count}"
    echo ""
    echo "git_status_porcelain:"
    git status --porcelain=v1 || true
  } > "${manifest_path}"

  echo "${manifest_path}"
}

create_snapshot_zip() {
  local out_zip="$1"
  local manifest_path="$2"
  local list_path="$3"

  rm -f "${out_zip}"
  zip -X -q "${out_zip}" -j "${manifest_path}"
  zip -X -q -@ "${out_zip}" < "${list_path}"
}

make_snapshot() {
  local tag="$1"

  mkdir -p archives

  local ts
  ts="$(date +"%Y-%m-%d_%H%M%S")"

  local commit_short
  commit_short="$(git rev-parse --short=12 HEAD)"

  local dirty_suffix=""
  if ! git diff --quiet || ! git diff --cached --quiet; then
    dirty_suffix="__dirty"
  fi

  local zip_path
  zip_path="archives/causyl_snapshot_${ts}__${tag}__${commit_short}${dirty_suffix}.zip"

  TMP_LIST="$(mktemp)"
  TMP_DIR="$(mktemp -d)"

  build_file_list "${TMP_LIST}"

  local file_count
  file_count="$(wc -l < "${TMP_LIST}" | tr -d '[:space:]')"

  local manifest_path
  manifest_path="$(write_manifest "${TMP_DIR}" "$(basename "${zip_path}")" "${ts}" "${file_count}" "${tag}")"

  create_snapshot_zip "${zip_path}" "${manifest_path}" "${TMP_LIST}"

  step "Snapshot written"
  echo "${zip_path} (${file_count} files)"
}

# ---------------------------------------------------------------------
# Worktree + shared venv bootstrap (no per-worktree .venv)
# ---------------------------------------------------------------------

abspath() {
  python3 -c 'import os,sys; print(os.path.abspath(os.path.expanduser(sys.argv[1])))' "${1}"
}

ensure_shared_venv_ready() {
  local wt_dir="$1"

  local shared_venv
  shared_venv="$(abspath "${CAUSYL_BASELINE_SHARED_VENV_PATH:-$HOME/venvs/causyl}")"

  local python_path="${shared_venv}/bin/python"
  local created="0"

  if [[ ! -x "${python_path}" ]]; then
    step "Create shared venv (missing)"
    info "Creating: ${shared_venv}"
    mkdir -p "$(dirname "${shared_venv}")"
    python3 -m venv "${shared_venv}"
    created="1"
  fi

  if [[ -n "${CAUSYL_PIP_CACHE_DIR:-}" ]]; then
    export PIP_CACHE_DIR="${CAUSYL_PIP_CACHE_DIR}"
  fi

  local install_shared="${CAUSYL_BASELINE_SHARED_VENV_INSTALL:-0}"
  local req_file="${wt_dir}/engine/requirements.txt"

  if [[ ! -f "${req_file}" ]]; then
    warn "Requirements file not found at: ${req_file} (skipping dependency install)."
    return 0
  fi

  if [[ "${created}" == "1" || "${install_shared}" == "1" ]]; then
    step "Install requirements into shared venv"
    "${python_path}" -m pip install -U pip
    "${python_path}" -m pip install -r "${req_file}"
  else
    info "Shared venv present; skipping requirements install (set CAUSYL_BASELINE_SHARED_VENV_INSTALL=1 to install)."
  fi

  ok "Shared venv ready: ${python_path}"
}

create_baseline_worktree_and_bootstrap() {
  local tag="$1"
  local id="$2"

  local root
  root="$(repo_root)"

  local parent
  parent="${CAUSYL_BASELINE_WORKTREES_DIR:-$(dirname "${root}")}"
  parent="$(abspath "${parent}")"

  local prefix
  prefix="${CAUSYL_BASELINE_WORKTREE_PREFIX:-causyl__baseline}"

  local wt_dir
  wt_dir="${parent}/${prefix}${id}"

  step "Create baseline worktree"
  echo "Worktree dir: ${wt_dir}"

  if [[ -e "${wt_dir}" ]]; then
    die "Worktree path already exists: ${wt_dir}"
  fi

  git worktree add "${wt_dir}" "${tag}"

  step "Worktree status"
  (cd "${wt_dir}" && git status && git describe --tags --always)

  local bootstrap
  bootstrap="${CAUSYL_BASELINE_BOOTSTRAP_VENV:-1}"
  if [[ "${bootstrap}" == "0" ]]; then
    step "Skipping shared venv bootstrap (CAUSYL_BASELINE_BOOTSTRAP_VENV=0)"
    return 0
  fi

  # Sanity-check that the activation helper exists in the worktree.
  local act_script="${wt_dir}/infra/bin/venv_activate.sh"
  if [[ -x "${act_script}" ]]; then
    ok "Activation helper present: infra/bin/venv_activate.sh"
  else
    warn "Activation helper not found/executable at: ${act_script}"
    warn "Worktree is still created, but you'll need to activate your venv manually."
  fi

  ensure_shared_venv_ready "${wt_dir}"

  step "Baseline worktree ready"
  echo "  folder: ${wt_dir}"
  echo "  activate (WSL-friendly): ${wt_dir}/infra/bin/venv_activate.sh"
  echo "  shared venv: ${CAUSYL_BASELINE_SHARED_VENV_PATH:-$HOME/venvs/causyl}"
}

# -----------------------------
# Parse args
# -----------------------------

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

ID=""
MESSAGE=""

if is_3digit_id "${1}"; then
  if [[ $# -lt 2 ]]; then
    usage
    exit 1
  fi
  ID="$1"
  shift
  MESSAGE="$*"
else
  ID="$(next_baseline_id)"
  MESSAGE="$*"
fi

[[ -n "${MESSAGE}" ]] || die "Commit message is empty."

TAG="baseline-${ID}"

step "Git status"
git status

ensure_nonempty_tree_at_head

if git diff --quiet && git diff --cached --quiet; then
  die "Working tree clean and nothing staged. Refusing to create an empty baseline commit."
fi

step "Ensure tag does not already exist"
if git rev-parse "${TAG}" >/dev/null 2>&1; then
  die "Tag '${TAG}' already exists."
fi

git add --renormalize .
step "Add all changes"
git add -A

step "Commit"
git commit -m "${ID} ${MESSAGE}"

ensure_nonempty_tree_at_head

step "Create annotated tag"
git tag -a "${TAG}" -m "${MESSAGE}"

step "Push commit"
git push

step "Push tag"
git push origin "${TAG}"

echo "Baseline complete: commit '${ID} ${MESSAGE}' + tag '${TAG}'"

make_snapshot "${TAG}"

create_baseline_worktree_and_bootstrap "${TAG}" "${ID}"

step "Active worktrees"
git worktree list
