#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# bootstrap.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Rebuild an environment from manifests/ on a fresh Ubuntu machine.
#
# What it does:
#   - Installs base bootstrap tools (curl, git, etc.)
#   - Restores APT sources (best-effort) then installs packages
#   - Installs chezmoi (for dotfiles) and applies them if CHEZMOI_REPO is set
#   - Optionally restores pipx packages, cargo installs, npm globals
#
# Usage:
#   bash scripts/bootstrap.sh
#
# Environment variables:
#   CHEZMOI_REPO   Optional. Git repo URL/path for your chezmoi dotfiles repo.
#   INSTALL_ALL    If "1", install from apt_all_packages.tsv (not recommended).
#  PROFILE        Optional. One of: server_minimal, desktop_extra (installs profiles/<name>.txt)
#  OVERWRITE_APT_SOURCES If "1", overwrite /etc/apt sources with captured ones (default: 0)
# STRICT_KEYS     If "1", fail if captured repos likely need keys but keys are missing (default: 0)
#  ALLOW_EMBEDDED_KEYS If "1", allow restoring .sources files that contain embedded PGP key blocks (default: 0)
#  CONTINUE_ON_ERROR If "1", continue even if some steps fail (default: 1)
#
# Notes:
#   - This script uses sudo for system-level installation steps.
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/bootstrap.sh [--manifests-dir <path>] [--profile <name>] [--dry-run] [--auto-apt-trust]

Options:
  --manifests-dir <path>   Read manifests from an alternate directory.
  --profile <name>         Install an alternate package profile.
  --dry-run                Print planned actions without changing the host.
  --auto-apt-trust         Restore captured APT sources and keyrings automatically.
  -h, --help               Show this help text.
EOF
}

MAN_DIR_OVERRIDE=""
PROFILE_ARG=""
DRY_RUN=${DRY_RUN:-0}
AUTO_APT_TRUST=${AUTO_APT_TRUST:-0}
while [ "$#" -gt 0 ]; do
  case "$1" in
    --manifests-dir)
      [ "$#" -ge 2 ] || { echo "--manifests-dir requires a path" >&2; exit 2; }
      MAN_DIR_OVERRIDE="$2"
      shift 2
      ;;
    --profile)
      [ "$#" -ge 2 ] || { echo "--profile requires a value" >&2; exit 2; }
      PROFILE_ARG="$2"
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --auto-apt-trust)
      AUTO_APT_TRUST=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [ -n "${PROFILE_ARG}" ]; then
  PROFILE="${PROFILE_ARG}"
fi

CONTINUE_ON_ERROR=${CONTINUE_ON_ERROR:-1}

run_step() {
  # Purpose: Run a step and optionally continue on failure.
  # Args: $1 step_name, $2... command
  local name="$1"; shift
  note "STEP: ${name}"
  if "$@"; then
    note "OK: ${name}"
    return 0
  else
    note "FAIL: ${name}"
    if [ "${CONTINUE_ON_ERROR}" = "1" ]; then
      note "Continuing despite failure (CONTINUE_ON_ERROR=1)."
      return 0
    fi
    return 1
  fi
}


SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
MAN_DIR="${MAN_DIR_OVERRIDE:-${ROOT_DIR}/manifests}"
APT_DIR="${MAN_DIR}/apt_sources"
KEYRING_DIR="${APT_DIR}/keyrings"
mkdir -p "${MAN_DIR}"

note() { printf "[bootstrap] %s\n" "$*"; }

need_sudo() {
  # Purpose: Ensure sudo works early and fail fast.
  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: skipping sudo credential check."
    return 0
  fi
  if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo not found. Install sudo or run as root." >&2
    exit 1
  fi
  sudo -v
}

install_base_tools() {
  # Purpose: Install minimal tools needed to run the rest of the bootstrap.
  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: would install base tools: ca-certificates curl git gnupg lsb-release software-properties-common jq unzip"
    return 0
  fi
  note "Installing base tools..."
  sudo apt-get update -y
  sudo apt-get install -y --no-install-recommends \
    ca-certificates \
    curl \
    git \
    gnupg \
    lsb-release \
    software-properties-common \
    jq \
    unzip
}

restore_apt_sources_best_effort() {
  # Purpose: Restore APT sources lists (does not magically re-add missing keys).
  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: would inspect and optionally restore captured APT sources."
    return 0
  fi
  note "Restoring APT sources (best-effort)..."
  if [ "${OVERWRITE_APT_SOURCES:-0}" != "1" ] && [ "${AUTO_APT_TRUST}" != "1" ]; then
    note "OVERWRITE_APT_SOURCES is not set. Skipping overwrite of /etc/apt sources."
    note "If you want to restore captured sources, run: OVERWRITE_APT_SOURCES=1 bash scripts/bootstrap.sh"
    return 0
  fi

  # Guard: block restoring .sources files with embedded PGP keys unless explicitly allowed.
  if [ "${ALLOW_EMBEDDED_KEYS:-0}" != "1" ] && [ "${AUTO_APT_TRUST}" != "1" ]; then
    if grep -Rhs "BEGIN PGP PUBLIC KEY BLOCK" "${APT_DIR}/sources" >/dev/null 2>&1; then
      note "Detected embedded PGP key block in captured sources."
      note "Refusing to overwrite APT sources unless ALLOW_EMBEDDED_KEYS=1 is set."
      note "This avoids silently copying trust material into new machines."
      return 0
    fi
  fi
  if [ -d "${APT_DIR}/sources" ]; then
    if [ -f "${APT_DIR}/sources/sources.list" ]; then
      sudo cp -a "${APT_DIR}/sources/sources.list" /etc/apt/sources.list
    fi
    if [ -d "${APT_DIR}/sources/sources.list.d" ]; then
      sudo mkdir -p /etc/apt/sources.list.d
      sudo cp -a "${APT_DIR}/sources/sources.list.d/." /etc/apt/sources.list.d/
    fi
  else
    note "No saved APT sources found. Skipping."
  fi
}

restore_apt_keyrings_best_effort() {
  # Purpose: Restore captured APT trust material for fully automatic bootstrap flows.
  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: would restore captured APT keyrings if present."
    return 0
  fi
  if [ "${AUTO_APT_TRUST}" != "1" ]; then
    note "AUTO_APT_TRUST is not set. Skipping keyring restore."
    return 0
  fi
  if [ ! -d "${KEYRING_DIR}" ]; then
    note "No captured keyring directory found at ${KEYRING_DIR}. Skipping keyring restore."
    return 0
  fi

  note "Restoring captured APT keyrings..."
  if [ -d "${KEYRING_DIR}/etc_apt_keyrings" ]; then
    sudo mkdir -p /etc/apt/keyrings
    sudo cp -a "${KEYRING_DIR}/etc_apt_keyrings/." /etc/apt/keyrings/
  fi
  if [ -d "${KEYRING_DIR}/etc_apt_trusted_gpg_d" ]; then
    sudo mkdir -p /etc/apt/trusted.gpg.d
    sudo cp -a "${KEYRING_DIR}/etc_apt_trusted_gpg_d/." /etc/apt/trusted.gpg.d/
  fi
  if [ -d "${KEYRING_DIR}/usr_share_keyrings" ]; then
    sudo mkdir -p /usr/share/keyrings
    sudo cp -a "${KEYRING_DIR}/usr_share_keyrings/." /usr/share/keyrings/
  fi
}

install_apt_packages() {
  # Purpose: Install packages captured on the source machine.
  local list_file=""
  if [ "${INSTALL_ALL:-0}" = "1" ]; then
    list_file="${MAN_DIR}/apt_all_packages.tsv"
    note "INSTALL_ALL=1 set: using apt_all_packages.tsv (can be huge / risky)."
  else
    list_file="${MAN_DIR}/apt_manual_packages.tsv"
    note "Using manual package list: apt_manual_packages.tsv"
  fi

  if [ ! -f "${list_file}" ]; then
    note "Package list not found: ${list_file}. Skipping APT install."
    return 0
  fi

  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: would install packages listed in ${list_file}."
    return 0
  fi

  note "Updating APT..."
  sudo apt-get update -y

  note "Installing packages (names only; versions are verified later)..."
  # We install by package name only. Exact versions are not guaranteed (repos drift).
  cut -f1 "${list_file}" \
    | grep -vE '^\s*$' \
    | xargs -r sudo apt-get install -y
}

install_chezmoi() {
  # Purpose: Install chezmoi for dotfiles management.
  # Best practice: use official installer for latest stable.
  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: would install chezmoi if it is missing."
    return 0
  fi
  if command -v chezmoi >/dev/null 2>&1; then
    note "chezmoi already installed."
    return 0
  fi
  note "Installing chezmoi..."
  sh -c "$(curl -fsLS get.chezmoi.io)" -- -b "${HOME}/.local/bin"
  export PATH="${HOME}/.local/bin:${PATH}"
}

apply_dotfiles() {
  # Purpose: Apply dotfiles from a chezmoi repo if provided.
  if [ "${DRY_RUN}" = "1" ]; then
    if [ -n "${CHEZMOI_REPO:-}" ]; then
      note "DRY_RUN=1: would apply dotfiles from CHEZMOI_REPO=${CHEZMOI_REPO}."
    else
      note "DRY_RUN=1: CHEZMOI_REPO not set; dotfiles apply would be skipped."
    fi
    return 0
  fi
  if [ -z "${CHEZMOI_REPO:-}" ]; then
    note "CHEZMOI_REPO not set; skipping dotfiles apply."
    return 0
  fi
  note "Applying dotfiles from CHEZMOI_REPO=${CHEZMOI_REPO}"
  chezmoi init --apply "${CHEZMOI_REPO}"
}

restore_pipx() {
  # Purpose: Restore pipx-installed apps if pipx inventory exists.
  if [ "${DRY_RUN}" = "1" ]; then
    if [ -f "${MAN_DIR}/pipx_list.json" ]; then
      note "DRY_RUN=1: would restore pipx applications from ${MAN_DIR}/pipx_list.json."
    fi
    return 0
  fi
  if [ ! -f "${MAN_DIR}/pipx_list.json" ]; then
    return 0
  fi
  if ! command -v pipx >/dev/null 2>&1; then
    note "pipx not installed. Installing..."
    sudo apt-get install -y pipx
    pipx ensurepath || true
  fi
  note "Restoring pipx apps..."
  # Extract package names from pipx json (best-effort).
  jq -r '.venvs | keys[]' "${MAN_DIR}/pipx_list.json" \
    | xargs -r -n1 pipx install || true
}

restore_npm_globals() {
  # Purpose: Restore npm global packages if inventory exists.
  if [ "${DRY_RUN}" = "1" ]; then
    if [ -f "${MAN_DIR}/npm_globals.json" ]; then
      note "DRY_RUN=1: would restore npm global packages from ${MAN_DIR}/npm_globals.json."
    fi
    return 0
  fi
  if [ ! -f "${MAN_DIR}/npm_globals.json" ]; then
    return 0
  fi
  if ! command -v npm >/dev/null 2>&1; then
    note "npm not installed; skipping npm globals restore."
    return 0
  fi
  note "Restoring npm global packages..."
  jq -r '.dependencies | keys[]' "${MAN_DIR}/npm_globals.json" \
    | grep -vE '^(npm|corepack)$' \
    | xargs -r sudo npm -g install || true
}

restore_cargo_installs() {
  # Purpose: Restore cargo-installed binaries if inventory exists.
  if [ "${DRY_RUN}" = "1" ]; then
    if [ -f "${MAN_DIR}/cargo_installs.txt" ]; then
      note "DRY_RUN=1: would restore cargo installs from ${MAN_DIR}/cargo_installs.txt."
    fi
    return 0
  fi
  if [ ! -f "${MAN_DIR}/cargo_installs.txt" ]; then
    return 0
  fi
  if ! command -v cargo >/dev/null 2>&1; then
    note "cargo not installed; skipping cargo restore."
    return 0
  fi
  note "Restoring cargo installs (best-effort)..."
  # Parse "crate vX.Y.Z:" lines and install crate by name.
  grep -E '^[a-zA-Z0-9_-]+ v[0-9]+' "${MAN_DIR}/cargo_installs.txt" \
    | awk '{print $1}' \
    | sort -u \
    | xargs -r -n1 cargo install || true
}


print_repo_key_checklist() {
  # Purpose: Print guidance based on captured sources/keyrings listing.
  note "Repo/key checklist..."
  if [ -f "${APT_DIR}/keyrings_listing/keyrings_ls.txt" ]; then
    note "Captured keyring inventory exists at manifests/apt_sources/keyrings_listing/keyrings_ls.txt"
    if [ "${AUTO_APT_TRUST}" = "1" ]; then
      note "AUTO_APT_TRUST=1: captured keyrings were restored before package install."
    else
      note "If apt update fails with NO_PUBKEY, re-add the repo key for the relevant source."
    fi
  fi

  # Simple detection: sources that reference signed-by= often need keyrings
  if [ -d "${APT_DIR}/sources" ]; then
    if grep -Rhs "signed-by=" "${APT_DIR}/sources" >/dev/null 2>&1; then
      note "Detected 'signed-by=' in captured sources. Ensure corresponding keyring files exist on target."
      if [ "${STRICT_KEYS:-0}" = "1" ]; then
        if [ ! -d /etc/apt/keyrings ]; then
          echo "STRICT_KEYS=1: /etc/apt/keyrings missing. Create it and add required keyrings." >&2
          exit 1
        fi
      fi
    fi
  fi
}


install_profile_packages() {
  # Purpose: Install an optional profile package set (server_minimal, desktop_extra).
  local profile="${PROFILE:-}"
  if [ -z "${profile}" ]; then
    return 0
  fi
  local profile_file="${ROOT_DIR}/profiles/${profile}.txt"
  if [ ! -f "${profile_file}" ]; then
    echo "Unknown PROFILE: ${profile}. Expected: ${profile_file}" >&2
    exit 1
  fi
  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: would install profile package set from ${profile_file}."
    return 0
  fi
  note "Installing profile package set: ${profile}"
  sudo apt-get update -y
  grep -vE '^\s*#|^\s*$' "${profile_file}" | xargs -r sudo apt-get install -y
}

main() {
  run_step "sudo_check" need_sudo
  run_step "install_base_tools" install_base_tools
  run_step "restore_apt_sources" restore_apt_sources_best_effort
  run_step "restore_apt_keyrings" restore_apt_keyrings_best_effort
  run_step "repo_key_checklist" print_repo_key_checklist
  run_step "install_profile_packages" install_profile_packages
  run_step "install_apt_packages" install_apt_packages

  run_step "install_chezmoi" install_chezmoi
  run_step "apply_dotfiles" apply_dotfiles

  run_step "restore_pipx" restore_pipx
  run_step "restore_npm_globals" restore_npm_globals
  run_step "restore_cargo_installs" restore_cargo_installs

  note "Bootstrap complete."
  if [ "${DRY_RUN}" = "1" ]; then
    note "DRY_RUN=1: no system changes were applied."
  fi
  note "Next: bash scripts/verify.sh --manifests-dir ${MAN_DIR}"
}

main "$@"
