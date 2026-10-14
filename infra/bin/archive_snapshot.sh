#!/usr/bin/env bash
# =====================================================================
# archive_complete_repo_snapshot.sh
#
# Last modified: 2026-04-02
# Purpose:
#   Create a repository snapshot ZIP that:
#   - Respects .gitignore (no ignored files)
#   - Excludes known junk directories (extra safety)
#   - Reflects the working tree (tracked + untracked-but-not-ignored)
#   - Names the ZIP with: timestamp + tag/baseline + commit_short + dirty
#   - Embeds SNAPSHOT_MANIFEST.txt inside the ZIP (includes filename too)
#   - Manifest includes tag annotation + HEAD commit subject/body
#
# Mode-driven packaging:
#   Default mode: share
#     Whole repo snapshot: tracked + untracked non-ignored files, filtered by
#     config/archive_profiles.toml.
#
#   --debug
#     Focused debugging snapshot.
#
#   --weekly
#     Broader repo archive that intentionally re-includes selected ignored
#     runtime/report/benchmark paths defined in the config.
#
# Console UX upgrades (2026-02):
#   - Colorized and structured output.
#   - Disable color via: EIEL_COLOR=0
#
# Usage:
#   ./archive_complete_repo_snapshot.sh
#   ./archive_complete_repo_snapshot.sh --debug
#   ./archive_complete_repo_snapshot.sh --weekly
#   ./archive_complete_repo_snapshot.sh --list
#
# Output:
#   archives/causyl_snapshot_<ts>__<tag|untagged>__<hash>[__dirty].zip
# =====================================================================

set -euo pipefail

# ---------------------------------------------------------------------
# Pretty console helpers (pure ANSI, no dependencies)
# ---------------------------------------------------------------------
EIEL_COLOR="${EIEL_COLOR:-1}"
if [[ "${EIEL_COLOR}" == "1" && -t 1 ]]; then
  _BOLD=$'\033[1m'
  _DIM=$'\033[2m'
  _RESET=$'\033[0m'
  _RED=$'\033[31m'
  _GREEN=$'\033[32m'
  _YELLOW=$'\033[33m'
  _BLUE=$'\033[34m'
  _CYAN=$'\033[36m'
else
  _BOLD=""; _DIM=""; _RESET=""; _RED=""; _GREEN=""; _YELLOW=""; _BLUE=""; _CYAN=""
fi

section() { printf "\n%s\n" "${_BOLD}${_CYAN}========== $* ==========${_RESET}"; }

# --- PATCH: these were missing, but preview_file_list() calls them ---
subsection() { printf "%s\n" "${_BOLD}${_CYAN}--- $* ---${_RESET}"; }
hr() { printf "%s\n" "${_DIM}----------------------------------------------------------------------${_RESET}"; }
# -------------------------------------------------------------------

info() { printf "%s\n" "${_BLUE}[INFO]${_RESET} $*"; }
ok() { printf "%s\n" "${_GREEN}[OK]${_RESET} $*"; }
warn() { printf "%s\n" "${_YELLOW}[WARN]${_RESET} $*"; }
fail() { printf "%s\n" "${_RED}[FAIL]${_RESET} $*"; }

# ---------------------------------------------------------------------
# Function: preview_file_list
# Description:
#   Print a short preview of the file list that will be zipped.
#   Avoids dumping hundreds/thousands of lines to the console.
# Args:
#   $1 = list file path
#   $2 = head lines (default 25)
#   $3 = tail lines (default 10)
# ---------------------------------------------------------------------
preview_file_list() {
  local list_path="$1"
  local head_n="${2:-25}"
  local tail_n="${3:-10}"

  [[ -f "${list_path}" ]] || return 0

  local total
  total="$(wc -l < "${list_path}" | tr -d '[:space:]' || echo 0)"

  subsection "Included paths preview"
  info "Total: ${total} files"
  info "Profile: ${PROFILE_NAME}"
  hr

  # Hard cap: never print more than this many lines (prevents console spam).
  local hard_cap=80

  # Clamp head/tail so head+tail never exceeds hard_cap-5
  local max_preview=$((hard_cap - 5))
  if [[ $((head_n + tail_n)) -gt "${max_preview}" ]]; then
    # Keep proportion roughly, but enforce safety
    head_n=$((max_preview * 2 / 3))
    tail_n=$((max_preview - head_n))
  fi

  if [[ "${total}" -le "${hard_cap}" ]]; then
    # Small list: ok to print fully
    cat "${list_path}"
    hr
    return 0
  fi

  sed -n "1,${head_n}p" "${list_path}" || true
  echo "  ... (${total} total; showing first ${head_n} and last ${tail_n})"
  tail -n "${tail_n}" "${list_path}" || true
  hr
}

preview_top_level_summary() {
  local list_path="$1"
  local label="$2"
  [[ -f "${list_path}" ]] || return 0

  local total
  total="$(wc -l < "${list_path}" | tr -d '[:space:]' || echo 0)"

  subsection "Top-level summary: ${label}"
  info "Total files: ${total}"
  python - "${list_path}" <<'PY'
from collections import Counter
from pathlib import Path
import sys

list_path = Path(sys.argv[1])
paths = [line.strip() for line in list_path.read_text(encoding="utf-8").splitlines() if line.strip()]
counter = Counter()
root_files = []
for path in paths:
    parts = Path(path).parts
    if len(parts) <= 1:
        root_files.append(path)
    elif parts:
        counter[parts[0]] += 1

for name, count in sorted(counter.items(), key=lambda item: (-item[1], item[0])):
    print(f"{name}/ ({count})")
if root_files:
    print("root files:")
    for path in sorted(root_files)[:20]:
        print(f"  {path}")
    remaining = len(root_files) - min(len(root_files), 20)
    if remaining > 0:
        print(f"  ... and {remaining} more")
PY
  hr
}

# Global temp paths for safe cleanup in EXIT trap (locals go out of scope).
TMP_LIST=""
TMP_DIR=""

PROFILE_NAME="share"
CONFIG_PATH="config/archive_profiles.toml"
LIST_ONLY=0


# ---------------------------------------------------------------------
# Function: cleanup
# Description:
#   Remove any temporary files/directories created by this script.
# ---------------------------------------------------------------------
cleanup() {
  if [[ -n "${TMP_LIST:-}" && -f "${TMP_LIST:-}" ]]; then
    rm -f "${TMP_LIST}"
  fi
  if [[ -n "${TMP_DIR:-}" && -d "${TMP_DIR:-}" ]]; then
    rm -rf "${TMP_DIR}"
  fi
}
trap cleanup EXIT

# ---------------------------------------------------------------------
# Function: die
# Description:
#   Print an error message to stderr and exit with code 1.
# ---------------------------------------------------------------------
die() {
  fail "$*"
  exit 1
}

# ---------------------------------------------------------------------
# Function: require_git_repo
# Description:
#   Ensure we are inside a git repository.
# ---------------------------------------------------------------------
require_git_repo() {
  git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "Not inside a Git repository."
}

# ---------------------------------------------------------------------
# Function: cd_to_repo_root
# Description:
#   Ensure the script runs from the repo root for stable relative paths.
# ---------------------------------------------------------------------
cd_to_repo_root() {
  local root
  root="$(git rev-parse --show-toplevel 2>/dev/null)" || die "Unable to locate repo root."
  cd "${root}"
}

# ---------------------------------------------------------------------
# Function: snapshot_local_timestamp
# Description:
#   Return Europe/Vienna local timestamp for archive identity.
# ---------------------------------------------------------------------
snapshot_local_timestamp() {
  TZ="Europe/Vienna" date +"%Y-%m-%d_%H%M%S"
}

# ---------------------------------------------------------------------
# Function: snapshot_utc_timestamp
# Description:
#   Return canonical UTC timestamp for archive identity metadata.
# ---------------------------------------------------------------------
snapshot_utc_timestamp() {
  date -u +"%Y-%m-%dT%H:%M:%SZ"
}

# ---------------------------------------------------------------------
# Function: snapshot_zip_path
# Description:
#   Build the output archive path from a timestamp + repo identity.
# ---------------------------------------------------------------------
snapshot_zip_path() {
  local ts_local="$1"
  local zip_path
  zip_path="archives/causyl_snapshot${ts_local}__${TAG_AT_HEAD}__${COMMIT_SHORT}${DIRTY_SUFFIX}.zip"
  if [[ "${PROFILE_NAME}" != "share" ]]; then
    zip_path="archives/causyl_snapshot${ts_local}__${TAG_AT_HEAD}__${COMMIT_SHORT}${DIRTY_SUFFIX}__$(resolve_profile_label "${PROFILE_NAME}").zip"
  fi
  printf "%s" "${zip_path}"
}

# ---------------------------------------------------------------------
# Function: git_identity_parts
# Description:
#   Compute tag_at_head (or 'untagged'), commit_short, dirty_flag.
# Globals set:
#   TAG_AT_HEAD, COMMIT_SHORT, DIRTY_SUFFIX
# ---------------------------------------------------------------------
git_identity_parts() {
  COMMIT_SHORT="$(git rev-parse --short=12 HEAD)"

  TAG_AT_HEAD="$(git tag --points-at HEAD | head -n 1 || true)"
  if [[ -z "${TAG_AT_HEAD}" ]]; then
    TAG_AT_HEAD="untagged"
  fi

  DIRTY_SUFFIX=""
  if ! git diff --quiet || ! git diff --cached --quiet; then
    DIRTY_SUFFIX="__dirty"
  fi
}

# ---------------------------------------------------------------------
# Function: parse_args
# Description:
#   Parse CLI arguments.
# ---------------------------------------------------------------------
parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --debug)
        PROFILE_NAME="debug"
        shift
        ;;
      --weekly)
        PROFILE_NAME="weekly_full"
        shift
        ;;
      --config)
        [[ $# -ge 2 ]] || die "--config requires a value"
        CONFIG_PATH="$2"
        shift 2
        ;;
      --list)
        LIST_ONLY=1
        shift
        ;;
      -h|--help)
        cat <<'EOF_HELP'
Usage:
  ./archive_complete_repo_snapshot.sh [--debug|--weekly] [--config <path>] [--list]

Options:
  --debug             Focused debug snapshot
  --weekly            Broader weekly snapshot with selected ignored paths
  --config <path>     Alternate TOML config file
  --list              Show top-level file/folder summaries; without mode flag,
                      prints summaries for share, debug, and weekly
  -h, --help          Show this help.
EOF_HELP
        exit 0
        ;;
      *)
        die "Unknown argument: $1"
        ;;
    esac
  done
}

resolve_profile_label() {
  case "${1:-share}" in
    share) echo "share" ;;
    debug) echo "debug" ;;
    weekly_full) echo "weekly" ;;
    *) echo "${1:-share}" ;;
  esac
}

# ---------------------------------------------------------------------
# Function: build_file_list
# Description:
#   Resolve the archive file list from the TOML profile config.
# Args:
#   $1 = output path for list file
# ---------------------------------------------------------------------
build_file_list() {
  local out_list="$1"
  [[ -f "${CONFIG_PATH}" ]] || die "Archive config not found: ${CONFIG_PATH}"

  python - "${CONFIG_PATH}" "${PROFILE_NAME}" "${out_list}" <<'PY'
from __future__ import annotations

import fnmatch
import subprocess
import sys
import tomllib
from pathlib import Path

config_path = Path(sys.argv[1]).resolve()
profile_name = str(sys.argv[2])
out_list = Path(sys.argv[3]).resolve()
repo_root = Path.cwd().resolve()

with config_path.open("rb") as fh:
    cfg = tomllib.load(fh)

defaults = dict(cfg.get("defaults") or {})
profiles = dict(cfg.get("profiles") or {})
if profile_name not in profiles:
    names = ", ".join(sorted(profiles))
    raise SystemExit(f"Unknown archive profile '{profile_name}'. Available profiles: {names}")

profile = dict(profiles.get(profile_name) or {})
include_mode = str(profile.get("include_mode") or "all_visible").strip()
respect_gitignore = bool(defaults.get("respect_gitignore", True))
always_exclude = [str(x) for x in list(defaults.get("always_exclude_globs") or [])]
profile_exclude = [str(x) for x in list(profile.get("exclude_globs") or [])]
include_globs = [str(x) for x in list(profile.get("include_globs") or [])]
include_ignored_globs = [str(x) for x in list(profile.get("include_ignored_globs") or [])]


def git_visible_files() -> set[str]:
    cmd = ["git", "ls-files", "--cached", "--others"]
    if respect_gitignore:
        cmd.append("--exclude-standard")
    cmd.append("-z")
    proc = subprocess.run(cmd, cwd=str(repo_root), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if proc.returncode != 0:
        raise SystemExit(proc.stderr.decode("utf-8", errors="replace").strip() or "git ls-files failed")
    out: set[str] = set()
    for raw in proc.stdout.split(b"\x00"):
        if not raw:
            continue
        rel = raw.decode("utf-8", errors="surrogateescape").replace("\\", "/").strip("/")
        if rel:
            out.add(rel)
    return out


def matches(path: str, pattern: str) -> bool:
    norm = str(path).replace("\\", "/").strip("/")
    pat = str(pattern).replace("\\", "/").strip("/")
    if not pat:
        return False
    if pat.endswith("/**"):
        prefix = pat[:-3].rstrip("/")
        if "*" in prefix or "?" in prefix or "[" in prefix:
            return fnmatch.fnmatch(norm, pat)
        return norm == prefix or norm.startswith(prefix + "/")
    return fnmatch.fnmatch(norm, pat)


def matches_any(path: str, patterns: list[str]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


def expand_pattern(pattern: str) -> set[str]:
    pat = str(pattern).replace("\\", "/").strip("/")
    if not pat:
        return set()
    out: set[str] = set()
    if pat.endswith("/**"):
        prefix = pat[:-3].rstrip("/")
        if "*" in prefix or "?" in prefix or "[" in prefix:
            for item in repo_root.glob(pat):
                if item.is_file():
                    out.add(item.relative_to(repo_root).as_posix())
                elif item.is_dir():
                    for child in item.rglob("*"):
                        if child.is_file():
                            out.add(child.relative_to(repo_root).as_posix())
            return out
        target = repo_root / prefix
        if target.is_file():
            out.add(target.relative_to(repo_root).as_posix())
            return out
        if target.is_dir():
            for item in target.rglob("*"):
                if item.is_file():
                    out.add(item.relative_to(repo_root).as_posix())
        return out
    target = repo_root / pat
    if target.is_file():
        out.add(target.relative_to(repo_root).as_posix())
        return out
    if target.is_dir():
        for item in target.rglob("*"):
            if item.is_file():
                out.add(item.relative_to(repo_root).as_posix())
        return out
    for item in repo_root.glob(pat):
        if item.is_file():
            out.add(item.relative_to(repo_root).as_posix())
        elif item.is_dir():
            for child in item.rglob("*"):
                if child.is_file():
                    out.add(child.relative_to(repo_root).as_posix())
    return out


selected = git_visible_files()
if include_mode == "curated":
    selected = {path for path in selected if matches_any(path, include_globs)}
elif include_mode != "all_visible":
    raise SystemExit(f"Unsupported include_mode for profile '{profile_name}': {include_mode}")

for pattern in include_globs:
    selected.update(expand_pattern(pattern))
for pattern in include_ignored_globs:
    selected.update(expand_pattern(pattern))

selected = {
    path for path in selected
    if not matches_any(path, always_exclude) and not matches_any(path, profile_exclude)
}

if not selected:
    raise SystemExit(f"Archive profile '{profile_name}' selected no files.")

out_list.parent.mkdir(parents=True, exist_ok=True)
out_list.write_text("".join(f"{path}\n" for path in sorted(selected)), encoding="utf-8")
PY

  local count
  count="$(wc -l < "${out_list}" | tr -d '[:space:]')"
  [[ "${count}" -ge 1 ]] || die "Snapshot file list is empty (${count} files)."
}

build_file_list_for_profile() {
  local profile_name="$1"
  local out_list="$2"
  local old_profile="${PROFILE_NAME}"
  PROFILE_NAME="${profile_name}"
  build_file_list "${out_list}"
  PROFILE_NAME="${old_profile}"
}

# ---------------------------------------------------------------------
# Function: tag_message_at_head
# Description:
#   Return the annotated tag message if HEAD is exactly tagged and the tag is annotated.
#   If no exact tag or no message, return "n/a".
# ---------------------------------------------------------------------
tag_message_at_head() {
  if git describe --tags --exact-match >/dev/null 2>&1; then
    # %(contents) is empty for lightweight tags, which is fine.
    local msg
    msg="$(git for-each-ref "refs/tags/${TAG_AT_HEAD}" --format="%(contents)" || true)"
    if [[ -n "${msg}" ]]; then
      printf "%s" "${msg}"
      return 0
    fi
  fi
  echo "n/a"
}

# ---------------------------------------------------------------------
# Function: write_manifest
# Description:
#   Write the manifest to a fixed filename inside a temp directory so
#   it appears in the ZIP as SNAPSHOT_MANIFEST.txt (not tmp.*).
# Args:
#   $1 = temp directory path
#   $2 = zip filename (basename)
#   $3 = local timestamp
#   $4 = utc timestamp
#   $5 = file_count
# Outputs:
#   Echoes the full manifest path to stdout.
# ---------------------------------------------------------------------
write_manifest() {
  local tmp_dir="$1"
  local zip_basename="$2"
  local ts_local="$3"
  local ts_utc="$4"
  local file_count="$5"

  local manifest_path="${tmp_dir}/SNAPSHOT_MANIFEST.txt"

  {
    echo "causyl snapshot manifest"
    echo "snapshot_identity_version: causyl_snapshot_identity_v1"
    echo "zip_filename: ${zip_basename}"
    echo "expected_zip_filename: ${zip_basename}"
    echo "created_at: ${ts_local}"
    echo "created_at_local: ${ts_local}"
    echo "created_at_utc: ${ts_utc}"
    echo "created_timezone: Europe/Vienna"
    echo "validation_summary_filename: SNAPSHOT_VALIDATION_SUMMARY.txt"
    echo "repo_root: $(pwd)"
    echo "branch: $(git rev-parse --abbrev-ref HEAD)"
    echo "tag_at_HEAD: ${TAG_AT_HEAD}"
    echo "tag_message:"
    tag_message_at_head
    echo "describe: $(git describe --tags --always --dirty 2>/dev/null || echo 'n/a')"
    echo "commit: $(git rev-parse HEAD)"
    echo "commit_short: ${COMMIT_SHORT}"
    echo "archive_profile: $(resolve_profile_label "${PROFILE_NAME}")"
    echo "archive_config: ${CONFIG_PATH}"
    echo "head_commit_subject:"
    git log -1 --pretty=%s
    echo "head_commit_body:"
    git log -1 --pretty=%b || true
    if [[ -n "${DIRTY_SUFFIX}" ]]; then
      echo "dirty: true"
    else
      echo "dirty: false"
    fi
    echo "file_count: ${file_count}"
    echo ""
    echo "git_status_porcelain:"
    git status --porcelain=v1 || true
  } > "${manifest_path}"

  echo "${manifest_path}"
}

# ---------------------------------------------------------------------
# Function: write_validation_summary
# Description:
#   Write a short validation summary that repeats the snapshot identity tuple.
# Args:
#   $1 = temp directory path
#   $2 = zip filename (basename)
#   $3 = local timestamp
#   $4 = utc timestamp
#   $5 = file_count
# Outputs:
#   Echoes the full validation summary path to stdout.
# ---------------------------------------------------------------------
write_validation_summary() {
  local tmp_dir="$1"
  local zip_basename="$2"
  local ts_local="$3"
  local ts_utc="$4"
  local file_count="$5"

  local summary_path="${tmp_dir}/SNAPSHOT_VALIDATION_SUMMARY.txt"

  {
    echo "causyl snapshot validation summary"
    echo "snapshot_identity_version: causyl_snapshot_identity_v1"
    echo "zip_filename: ${zip_basename}"
    echo "expected_zip_filename: ${zip_basename}"
    echo "created_at_local: ${ts_local}"
    echo "created_at_utc: ${ts_utc}"
    echo "created_timezone: Europe/Vienna"
    echo "archive_profile: $(resolve_profile_label "${PROFILE_NAME}")"
    echo "file_count: ${file_count}"
    echo "manifest_filename: SNAPSHOT_MANIFEST.txt"
    echo "validation_summary_filename: SNAPSHOT_VALIDATION_SUMMARY.txt"
    echo "identity_match: true"
  } > "${summary_path}"

  echo "${summary_path}"
}

# ---------------------------------------------------------------------
# Function: assert_snapshot_identity_consistent
# Description:
#   Fail closed when the manifest/validation bundle disagree on snapshot identity.
# Args:
#   $1 = zip filename (basename)
#   $2 = manifest path
#   $3 = validation summary path
# ---------------------------------------------------------------------
assert_snapshot_identity_consistent() {
  local zip_basename="$1"
  local manifest_path="$2"
  local summary_path="$3"

  grep -Fqx "zip_filename: ${zip_basename}" "${manifest_path}" || die "Manifest zip filename mismatch"
  grep -Fqx "expected_zip_filename: ${zip_basename}" "${manifest_path}" || die "Manifest expected zip filename mismatch"
  grep -Fqx "zip_filename: ${zip_basename}" "${summary_path}" || die "Validation summary zip filename mismatch"
  grep -Fqx "expected_zip_filename: ${zip_basename}" "${summary_path}" || die "Validation summary expected zip filename mismatch"
  grep -Fqx "identity_match: true" "${summary_path}" || die "Validation summary identity_match flag missing"
}

# ---------------------------------------------------------------------
# Function: create_snapshot_zip
# Description:
#   Create the ZIP with manifest + validation summary + file list.
# Args:
#   $1 = output zip path
#   $2 = path to manifest file (named SNAPSHOT_MANIFEST.txt)
#   $3 = path to validation summary (named SNAPSHOT_VALIDATION_SUMMARY.txt)
#   $4 = path to file list
# ---------------------------------------------------------------------
create_snapshot_zip() {
  local out_zip="$1"
  local manifest_path="$2"
  local validation_summary_path="$3"
  local list_path="$4"

  rm -f "${out_zip}"

  # -X strips extra attrs; adding manifest + summary first keeps them at root with stable names.
  zip -X -q "${out_zip}" -j "${manifest_path}" "${validation_summary_path}"
  zip -X -q -@ "${out_zip}" < "${list_path}"
}

main() {
  parse_args "$@"

  section "SNAPSHOT"

  require_git_repo
  cd_to_repo_root
  mkdir -p archives

  local ts_local ts_utc
  ts_local="$(snapshot_local_timestamp)"
  ts_utc="$(snapshot_utc_timestamp)"

  git_identity_parts

  info "Repo root: $(pwd)"
  info "HEAD:      $(git rev-parse --short=12 HEAD)"
  info "Tag:       ${TAG_AT_HEAD}"
  info "Mode:      $(resolve_profile_label "${PROFILE_NAME}")"
  info "Created:   ${ts_local} Europe/Vienna (${ts_utc} UTC)"
  if [[ -n "${DIRTY_SUFFIX}" ]]; then
    warn "Working tree is dirty"
  else
    ok "Working tree is clean"
  fi

  local zip_path
  zip_path="$(snapshot_zip_path "${ts_local}")"

  TMP_LIST="$(mktemp)"
  TMP_DIR="$(mktemp -d)"

  section "FILE LIST"
  if [[ "${LIST_ONLY}" == "1" ]]; then
    if [[ "${PROFILE_NAME}" == "share" ]]; then
      local share_list debug_list weekly_list
      share_list="$(mktemp)"
      debug_list="$(mktemp)"
      weekly_list="$(mktemp)"
      build_file_list_for_profile "share" "${share_list}"
      build_file_list_for_profile "debug" "${debug_list}"
      build_file_list_for_profile "weekly_full" "${weekly_list}"
      preview_top_level_summary "${share_list}" "share"
      preview_top_level_summary "${debug_list}" "debug"
      preview_top_level_summary "${weekly_list}" "weekly"
      rm -f "${share_list}" "${debug_list}" "${weekly_list}"
    else
      build_file_list "${TMP_LIST}"
      preview_top_level_summary "${TMP_LIST}" "$(resolve_profile_label "${PROFILE_NAME}")"
    fi
    ok "List-only mode complete"
    return 0
  fi

  build_file_list "${TMP_LIST}"

  local file_count
  file_count="$(wc -l < "${TMP_LIST}" | tr -d '[:space:]')"
  ok "Selected ${file_count} files"

  section "MANIFEST"
  local manifest_path validation_summary_path
  manifest_path="$(write_manifest "${TMP_DIR}" "$(basename "${zip_path}")" "${ts_local}" "${ts_utc}" "${file_count}")"
  validation_summary_path="$(write_validation_summary "${TMP_DIR}" "$(basename "${zip_path}")" "${ts_local}" "${ts_utc}" "${file_count}")"
  assert_snapshot_identity_consistent "$(basename "${zip_path}")" "${manifest_path}" "${validation_summary_path}"
  ok "Manifest prepared: ${manifest_path##*/}"
  ok "Validation summary prepared: ${validation_summary_path##*/}"

  section "ZIP"
  create_snapshot_zip "${zip_path}" "${manifest_path}" "${validation_summary_path}" "${TMP_LIST}"
  ok "Snapshot written: ${zip_path} (${file_count} files)"

  # Machine-readable output for tooling
  echo "SNAPSHOT_ZIP_PATH=${zip_path}"
}

main "$@"
