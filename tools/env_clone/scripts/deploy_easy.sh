#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# deploy_easy.sh
# -----------------------------------------------------------------------------
# Purpose:
#   One-command repo deployment using the existing env_clone snapshot/bootstrap/
#   verify flow.
#
# Modes:
#   - --wet-run: copy the repo into /tmp (or --dest) and run bootstrap in
#     --dry-run mode so you can inspect the whole orchestration locally.
#     It also stages a home overlay containing ~/.bashrc and .config/causyl.
#   - SSH mode: copy the repo to a remote target over SSH, refresh manifests,
#     run bootstrap there, then verify the result. The SSH path defaults to
#     automatic APT trust restore so captured sources and keyrings are applied
#     without manual follow-up, and it syncs ~/.bashrc plus .config/causyl
#     into the target user's home directory.
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  bash tools/env_clone/scripts/deploy_easy.sh --wet-run [--dest /tmp/path] [--profile server_minimal]
  bash tools/env_clone/scripts/deploy_easy.sh --target user@host [--port 22] [--dest /srv/causyl] [--profile server_minimal]

Options:
  --wet-run               Demonstrate the deploy locally in /tmp without SSH.
  --target <user@host>    SSH target for the real deploy path.
  --port <n>              SSH port (default: 22).
  --dest <path>           Destination base directory.
  --profile <name>        Bootstrap profile (default: server_minimal).
  --skip-snapshot         Reuse existing tools/env_clone/manifests instead of capturing fresh ones.
  --skip-bootstrap        Copy files but do not run bootstrap on the target.
  --skip-verify           Copy files but do not run verification on the target.
  --bootstrap-dry-run     Force bootstrap --dry-run even for SSH mode.
  --conservative-apt      Do not auto-restore captured APT sources and keyrings.
  -h, --help              Show this help text.
USAGE
}

note() { printf '[deploy_easy] %s\n' "$*"; }
die() { printf '[deploy_easy][ERROR] %s\n' "$*" >&2; exit 1; }
require_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}
quote_remote() {
  printf '%q' "$1"
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_CLONE_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
REPO_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." && pwd)"
EXCLUDE_FILE="${ENV_CLONE_DIR}/rsync_excludes.txt"
LOCAL_BASHRC="${HOME}/.bashrc"
LOCAL_CAUSYL_CONFIG="$(cd -- "${REPO_ROOT}/.." && pwd)/.config/causyl"

MODE="ssh"
TARGET=""
PORT=22
DEST_BASE=""
PROFILE_NAME="${PROFILE:-server_minimal}"
SKIP_SNAPSHOT=0
SKIP_BOOTSTRAP=0
SKIP_VERIFY=0
BOOTSTRAP_DRY_RUN=0
AUTO_APT_TRUST=1

while [ "$#" -gt 0 ]; do
  case "$1" in
    --wet-run)
      MODE="wet-run"
      shift
      ;;
    --target)
      [ "$#" -ge 2 ] || die "--target requires a value"
      TARGET="$2"
      shift 2
      ;;
    --port)
      [ "$#" -ge 2 ] || die "--port requires a value"
      PORT="$2"
      shift 2
      ;;
    --dest)
      [ "$#" -ge 2 ] || die "--dest requires a value"
      DEST_BASE="$2"
      shift 2
      ;;
    --profile)
      [ "$#" -ge 2 ] || die "--profile requires a value"
      PROFILE_NAME="$2"
      shift 2
      ;;
    --skip-snapshot)
      SKIP_SNAPSHOT=1
      shift
      ;;
    --skip-bootstrap)
      SKIP_BOOTSTRAP=1
      shift
      ;;
    --skip-verify)
      SKIP_VERIFY=1
      shift
      ;;
    --bootstrap-dry-run)
      BOOTSTRAP_DRY_RUN=1
      shift
      ;;
    --conservative-apt)
      AUTO_APT_TRUST=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

require_cmd rsync
require_cmd bash

if [ "${MODE}" = "ssh" ]; then
  require_cmd ssh
  [ -n "${TARGET}" ] || die "--target is required unless --wet-run is set"
else
  BOOTSTRAP_DRY_RUN=1
fi

TS_UTC="$(date -u +%Y%m%dT%H%M%SZ)"
WORK_DIR="$(mktemp -d "/tmp/causyl_deploy_easy_${TS_UTC}_XXXXXX")"
STAGED_MANIFESTS="${WORK_DIR}/manifests"
REPORT_FILE="${WORK_DIR}/deploy_report.txt"
mkdir -p "${STAGED_MANIFESTS}"
: > "${REPORT_FILE}"

log_and_run() {
  note "$*"
  printf '[deploy_easy][cmd] %s\n' "$*" >> "${REPORT_FILE}"
  "$@"
}

log_text() {
  note "$*"
  printf '[deploy_easy] %s\n' "$*" >> "${REPORT_FILE}"
}

REPO_NAME="$(basename "${REPO_ROOT}")"
if [ -z "${DEST_BASE}" ]; then
  if [ "${MODE}" = "wet-run" ]; then
    DEST_BASE="/tmp/causyl_wet_run_${TS_UTC}"
  else
    DEST_BASE="/srv/causyl"
  fi
fi
TARGET_REPO="${DEST_BASE%/}/${REPO_NAME}"

log_text "Mode: ${MODE}"
log_text "Source repo: ${REPO_ROOT}"
log_text "Working directory: ${WORK_DIR}"
log_text "Staged manifests: ${STAGED_MANIFESTS}"
log_text "Target repo: ${TARGET_REPO}"
log_text "Bootstrap profile: ${PROFILE_NAME}"
if [ -f "${LOCAL_BASHRC}" ]; then
  log_text "Will sync bashrc from: ${LOCAL_BASHRC}"
else
  log_text "Local bashrc not found at ${LOCAL_BASHRC}; home sync will skip it."
fi
if [ -d "${LOCAL_CAUSYL_CONFIG}" ]; then
  log_text "Will sync causyl config from: ${LOCAL_CAUSYL_CONFIG}"
else
  log_text "Local causyl config not found at ${LOCAL_CAUSYL_CONFIG}; home sync will skip it."
fi

if [ "${SKIP_SNAPSHOT}" = "1" ]; then
  log_text "Reusing existing env_clone manifests from the source repo."
  log_and_run rsync -a "${ENV_CLONE_DIR}/manifests/" "${STAGED_MANIFESTS}/"
else
  log_text "Capturing fresh source manifests into ${STAGED_MANIFESTS}."
  log_and_run bash "${SCRIPT_DIR}/snapshot.sh" --manifests-dir "${STAGED_MANIFESTS}"
fi

if [ ! -f "${STAGED_MANIFESTS}/critical_tools.txt" ] && [ -f "${ENV_CLONE_DIR}/manifests/critical_tools.txt" ]; then
  log_text "Backfilling critical_tools.txt into staged manifests."
  log_and_run rsync -a "${ENV_CLONE_DIR}/manifests/critical_tools.txt" "${STAGED_MANIFESTS}/critical_tools.txt"
fi

run_local_bootstrap() {
  local repo_path="$1"
  local manifests_dir="$2"
  if [ "${SKIP_BOOTSTRAP}" = "1" ]; then
    log_text "Skipping bootstrap by request."
    return 0
  fi
  if [ "${BOOTSTRAP_DRY_RUN}" = "1" ]; then
    log_text "Running bootstrap in DRY_RUN mode."
    if [ "${AUTO_APT_TRUST}" = "1" ]; then
      log_and_run env PROFILE="${PROFILE_NAME}" DRY_RUN=1 AUTO_APT_TRUST=1 bash "${repo_path}/tools/env_clone/scripts/bootstrap.sh" --manifests-dir "${manifests_dir}" --profile "${PROFILE_NAME}" --dry-run --auto-apt-trust
    else
      log_and_run env PROFILE="${PROFILE_NAME}" DRY_RUN=1 bash "${repo_path}/tools/env_clone/scripts/bootstrap.sh" --manifests-dir "${manifests_dir}" --profile "${PROFILE_NAME}" --dry-run
    fi
  else
    log_text "Running bootstrap on the target repo."
    if [ "${AUTO_APT_TRUST}" = "1" ]; then
      log_and_run env PROFILE="${PROFILE_NAME}" AUTO_APT_TRUST=1 bash "${repo_path}/tools/env_clone/scripts/bootstrap.sh" --manifests-dir "${manifests_dir}" --profile "${PROFILE_NAME}" --auto-apt-trust
    else
      log_and_run env PROFILE="${PROFILE_NAME}" bash "${repo_path}/tools/env_clone/scripts/bootstrap.sh" --manifests-dir "${manifests_dir}" --profile "${PROFILE_NAME}"
    fi
  fi
}

run_local_verify() {
  local repo_path="$1"
  local manifests_dir="$2"
  if [ "${SKIP_VERIFY}" = "1" ]; then
    log_text "Skipping verify by request."
    return 0
  fi
  log_text "Running verification on the target repo."
  log_and_run bash "${repo_path}/tools/env_clone/scripts/verify.sh" --manifests-dir "${manifests_dir}"
}

run_remote_command() {
  local remote_cmd="$1"
  ssh -tt -p "${PORT}" "${TARGET}" "${remote_cmd}"
}

sync_local_home_overlay() {
  local overlay_home="$1"
  mkdir -p "${overlay_home}/.config/causyl"
  if [ -f "${LOCAL_BASHRC}" ]; then
    log_text "Staging bashrc into wet-run home overlay."
    log_and_run rsync -a "${LOCAL_BASHRC}" "${overlay_home}/.bashrc"
  fi
  if [ -d "${LOCAL_CAUSYL_CONFIG}" ]; then
    log_text "Staging .config/causyl into wet-run home overlay."
    log_and_run rsync -a "${LOCAL_CAUSYL_CONFIG}/" "${overlay_home}/.config/causyl/"
  fi
}

sync_remote_home() {
  run_remote_command "mkdir -p ~/.config/causyl"
  if [ -f "${LOCAL_BASHRC}" ]; then
    log_text "Syncing bashrc to remote home."
    log_and_run rsync -a -e "ssh -p ${PORT}" "${LOCAL_BASHRC}" "${TARGET}:~/.bashrc"
  fi
  if [ -d "${LOCAL_CAUSYL_CONFIG}" ]; then
    log_text "Syncing .config/causyl to remote home."
    log_and_run rsync -a -e "ssh -p ${PORT}" "${LOCAL_CAUSYL_CONFIG}/" "${TARGET}:~/.config/causyl/"
  fi
}

if [ "${MODE}" = "wet-run" ]; then
  mkdir -p "${TARGET_REPO}"
  log_text "Copying repository into the wet-run target."
  log_and_run rsync -a --human-readable --exclude-from "${EXCLUDE_FILE}" "${REPO_ROOT}/" "${TARGET_REPO}/"
  log_text "Refreshing env_clone manifests inside the wet-run copy."
  mkdir -p "${TARGET_REPO}/tools/env_clone/manifests"
  log_and_run rsync -a "${STAGED_MANIFESTS}/" "${TARGET_REPO}/tools/env_clone/manifests/"
  sync_local_home_overlay "${DEST_BASE%/}/home_overlay"
  run_local_bootstrap "${TARGET_REPO}" "${TARGET_REPO}/tools/env_clone/manifests"
  run_local_verify "${TARGET_REPO}" "${TARGET_REPO}/tools/env_clone/manifests"
  log_text "Wet run complete. Inspect: ${TARGET_REPO}"
else
  log_text "Preparing remote destination over SSH."
  run_remote_command "mkdir -p $(quote_remote "${TARGET_REPO}") $(quote_remote "${TARGET_REPO}/tools/env_clone/manifests")"
  log_text "Copying repository to the remote target."
  log_and_run rsync -a --human-readable --partial --exclude-from "${EXCLUDE_FILE}" -e "ssh -p ${PORT}" "${REPO_ROOT}/" "${TARGET}:${TARGET_REPO}/"
  log_text "Refreshing env_clone manifests on the remote target."
  log_and_run rsync -a -e "ssh -p ${PORT}" "${STAGED_MANIFESTS}/" "${TARGET}:${TARGET_REPO}/tools/env_clone/manifests/"
  sync_remote_home
  if [ "${SKIP_BOOTSTRAP}" != "1" ]; then
    if [ "${BOOTSTRAP_DRY_RUN}" = "1" ]; then
      log_text "Running remote bootstrap in DRY_RUN mode."
      if [ "${AUTO_APT_TRUST}" = "1" ]; then
        run_remote_command "PROFILE=$(quote_remote "${PROFILE_NAME}") DRY_RUN=1 AUTO_APT_TRUST=1 bash $(quote_remote "${TARGET_REPO}/tools/env_clone/scripts/bootstrap.sh") --manifests-dir $(quote_remote "${TARGET_REPO}/tools/env_clone/manifests") --profile $(quote_remote "${PROFILE_NAME}") --dry-run --auto-apt-trust"
      else
        run_remote_command "PROFILE=$(quote_remote "${PROFILE_NAME}") DRY_RUN=1 bash $(quote_remote "${TARGET_REPO}/tools/env_clone/scripts/bootstrap.sh") --manifests-dir $(quote_remote "${TARGET_REPO}/tools/env_clone/manifests") --profile $(quote_remote "${PROFILE_NAME}") --dry-run"
      fi
    else
      log_text "Running remote bootstrap."
      if [ "${AUTO_APT_TRUST}" = "1" ]; then
        run_remote_command "PROFILE=$(quote_remote "${PROFILE_NAME}") AUTO_APT_TRUST=1 bash $(quote_remote "${TARGET_REPO}/tools/env_clone/scripts/bootstrap.sh") --manifests-dir $(quote_remote "${TARGET_REPO}/tools/env_clone/manifests") --profile $(quote_remote "${PROFILE_NAME}") --auto-apt-trust"
      else
        run_remote_command "PROFILE=$(quote_remote "${PROFILE_NAME}") bash $(quote_remote "${TARGET_REPO}/tools/env_clone/scripts/bootstrap.sh") --manifests-dir $(quote_remote "${TARGET_REPO}/tools/env_clone/manifests") --profile $(quote_remote "${PROFILE_NAME}")"
      fi
    fi
  else
    log_text "Skipping remote bootstrap by request."
  fi
  if [ "${SKIP_VERIFY}" != "1" ]; then
    log_text "Running remote verification."
    run_remote_command "bash $(quote_remote "${TARGET_REPO}/tools/env_clone/scripts/verify.sh") --manifests-dir $(quote_remote "${TARGET_REPO}/tools/env_clone/manifests")"
  else
    log_text "Skipping remote verification by request."
  fi
  log_text "Remote deploy complete: ${TARGET}:${TARGET_REPO}"
fi

log_text "Local deploy report: ${REPORT_FILE}"
