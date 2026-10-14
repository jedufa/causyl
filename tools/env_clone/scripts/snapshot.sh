#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# snapshot.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Capture a snapshot of this machine’s environment in a way that can be
#   replayed on another Ubuntu machine.
#
# Outputs (written under ../manifests):
#   - APT package inventories (manual + full), with versions
#   - APT sources and keyring inventory (best-effort)
#   - Tool versions (git, tmux, python, node, etc if present)
#   - Optional ecosystem inventories (pipx, npm globals, cargo installs, rustup)
#   - WARNINGS.txt for secrets and drift hazards
#
# Safety:
#   - Does NOT copy secrets. It only warns if it sees likely secret locations.
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/snapshot.sh [--manifests-dir <path>]

Options:
  --manifests-dir <path>   Write manifests into an alternate directory.
  -h, --help              Show this help text.
EOF
}

MAN_DIR_OVERRIDE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --manifests-dir)
      [ "$#" -ge 2 ] || { echo "--manifests-dir requires a path" >&2; exit 2; }
      MAN_DIR_OVERRIDE="$2"
      shift 2
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

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
MAN_DIR="${MAN_DIR_OVERRIDE:-${ROOT_DIR}/manifests}"
APT_DIR="${MAN_DIR}/apt_sources"
KEYRING_EXPORT_DIR="${APT_DIR}/keyrings"

mkdir -p "${MAN_DIR}" "${APT_DIR}" "${KEYRING_EXPORT_DIR}"

warn_file="${MAN_DIR}/WARNINGS.txt"
: > "${warn_file}"

note() { printf "[snapshot] %s\n" "$*"; }
warn() { printf "[WARNING] %s\n" "$*" | tee -a "${warn_file}" >/dev/null; }

require_cmd() {
  # Purpose: Ensure a command exists, else warn.
  # Args: $1 command name
  if ! command -v "$1" >/dev/null 2>&1; then
    warn "Command not found: $1"
    return 1
  fi
  return 0
}

write_tool_version() {
  # Purpose: Add a tool version row to tool_versions.tsv
  # Args:
  #   $1 tool_name
  #   $2 version_command (string)
  local tool="$1"
  local cmd="$2"
  local out=""
  if command -v "${tool}" >/dev/null 2>&1; then
    # shellcheck disable=SC2086
    out="$(bash -lc "${cmd}" 2>/dev/null | head -n 1 | tr -d '\r')"
    printf "%s\t%s\n" "${tool}" "${out}" >> "${MAN_DIR}/tool_versions.tsv"
  fi
}

note "Writing system identity..."
{
  echo "DATE_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "HOSTNAME=$(hostname)"
  echo "KERNEL=$(uname -r)"
  if [ -f /etc/os-release ]; then
    echo "--- /etc/os-release ---"
    cat /etc/os-release
  fi
} > "${MAN_DIR}/system_identity.txt"

note "Capturing APT package inventory (manual + full)..."
# Manual packages are what you intentionally installed. Much cleaner for replay.
# We also store the full list for verification.
if require_cmd dpkg-query; then
  # Full package list with versions
  dpkg-query -W -f='${binary:Package}\t${Version}\n' \
    | sort > "${MAN_DIR}/apt_all_packages.tsv"

  # Manual packages + versions
  if require_cmd apt-mark; then
    apt-mark showmanual \
      | grep -vE '^(linux-image-|linux-headers-|linux-modules-|linux-firmware)' \
      | sort \
      | awk 'NF{print $0}' \
      | while read -r pkg; do
          ver="$(dpkg-query -W -f='${Version}' "${pkg}" 2>/dev/null || true)"
          if [ -n "${ver}" ]; then
            printf "%s\t%s\n" "${pkg}" "${ver}"
          fi
        done \
      | sort > "${MAN_DIR}/apt_manual_packages.tsv"
  else
    warn "apt-mark missing; cannot capture manual package set accurately."
  fi
else
  warn "dpkg-query missing; cannot capture package inventory."
fi

note "Capturing APT sources and keyring inventory (best-effort)..."
# Copy sources lists (requires sudo to read reliably, but many are world-readable).
mkdir -p "${APT_DIR}/sources"
mkdir -p "${APT_DIR}/keyrings_listing"
mkdir -p "${KEYRING_EXPORT_DIR}/etc_apt_keyrings"
mkdir -p "${KEYRING_EXPORT_DIR}/etc_apt_trusted_gpg_d"
mkdir -p "${KEYRING_EXPORT_DIR}/usr_share_keyrings"

# Sources
if [ -f /etc/apt/sources.list ]; then
  cp -a /etc/apt/sources.list "${APT_DIR}/sources/" || true
fi
if [ -d /etc/apt/sources.list.d ]; then
  cp -a /etc/apt/sources.list.d "${APT_DIR}/sources/" || true
fi

# Keyrings inventory (we only inventory file names; copying keys is optional and risky).
{
  echo "== /etc/apt/keyrings =="
  ls -la /etc/apt/keyrings 2>/dev/null || true
  echo
  echo "== /etc/apt/trusted.gpg.d =="
  ls -la /etc/apt/trusted.gpg.d 2>/dev/null || true
  echo
  echo "== /usr/share/keyrings =="
  ls -la /usr/share/keyrings 2>/dev/null || true
} > "${APT_DIR}/keyrings_listing/keyrings_ls.txt"

note "Capturing APT keyring files for automatic bootstrap..."
if [ -d /etc/apt/keyrings ]; then
  cp -a /etc/apt/keyrings/. "${KEYRING_EXPORT_DIR}/etc_apt_keyrings/" 2>/dev/null || true
fi
if [ -d /etc/apt/trusted.gpg.d ]; then
  cp -a /etc/apt/trusted.gpg.d/. "${KEYRING_EXPORT_DIR}/etc_apt_trusted_gpg_d/" 2>/dev/null || true
fi
if [ -d /usr/share/keyrings ]; then
  cp -a /usr/share/keyrings/. "${KEYRING_EXPORT_DIR}/usr_share_keyrings/" 2>/dev/null || true
fi

# Snap / Flatpak inventories (optional)

note "Capturing APT policy and repo metadata (helps debug drift)..."
apt-cache policy > "${MAN_DIR}/apt_policy.txt" 2>/dev/null || true
apt-config dump > "${MAN_DIR}/apt_config_dump.txt" 2>/dev/null || true
dpkg --print-architecture > "${MAN_DIR}/dpkg_arch.txt" 2>/dev/null || true
dpkg --print-foreign-architectures > "${MAN_DIR}/dpkg_foreign_arch.txt" 2>/dev/null || true


# List PPAs (best-effort)
# Launchpad PPAs can appear as ppa.launchpad.net OR ppa.launchpadcontent.net in deb822 sources.
if command -v add-apt-repository >/dev/null 2>&1; then
  grep -RhsE 'ppa\.launchpad(net|content\.net)|launchpadcontent\.net|LP-PPA' /etc/apt/sources.list /etc/apt/sources.list.d 2>/dev/null \
    | sort -u > "${MAN_DIR}/ppa_grep.txt" || true
fi

note "Checking captured APT sources for embedded PGP keys (warn only)..."
# Deb822 .sources files may embed a public key block. This is legitimate, but it *is* trust material.
embedded_keys=0
if grep -Rhs "BEGIN PGP PUBLIC KEY BLOCK" "${APT_DIR}/sources" >/dev/null 2>&1; then
  embedded_keys=1
  warn "Detected embedded PGP public key block inside captured APT .sources file(s). Treat sources files as trust artifacts."
  grep -Rhn "BEGIN PGP PUBLIC KEY BLOCK" "${APT_DIR}/sources" >> "${MAN_DIR}/embedded_pgp_keys_locations.txt" || true
fi

note "Capturing Snap/Flatpak inventories if present..."
if command -v snap >/dev/null 2>&1; then
  snap list > "${MAN_DIR}/snap_list.txt" || true
fi
if command -v flatpak >/dev/null 2>&1; then
  flatpak list --columns=application,ref,version,branch,origin > "${MAN_DIR}/flatpak_list.txt" || true
fi

note "Writing critical tools list..."
cat > "${MAN_DIR}/critical_tools.txt" <<'EOF'
# Commands required for env_clone bootstrap + verification.
sudo
bash
git
python3
pip3
rsync
curl
jq
tar
gzip
unzip
EOF

note "Capturing tool versions..."
: > "${MAN_DIR}/tool_versions.tsv"
printf "tool\tversion\n" >> "${MAN_DIR}/tool_versions.tsv"

write_tool_version "bash" "bash --version | head -n 1"
write_tool_version "git" "git --version"
write_tool_version "curl" "curl --version | head -n 1"
write_tool_version "wget" "wget --version | head -n 1"
write_tool_version "tmux" "tmux -V"
write_tool_version "vim" "vim --version | head -n 2 | tr '\n' ' '"
write_tool_version "nvim" "nvim --version | head -n 1"
write_tool_version "python3" "python3 --version"
write_tool_version "pip3" "pip3 --version"
write_tool_version "node" "node --version"
write_tool_version "npm" "npm --version"
write_tool_version "pnpm" "pnpm --version"
write_tool_version "docker" "docker --version"
write_tool_version "docker-compose" "docker-compose --version"
write_tool_version "podman" "podman --version"
write_tool_version "go" "go version"
write_tool_version "rustc" "rustc --version"
write_tool_version "cargo" "cargo --version"
write_tool_version "rustup" "rustup --version"
write_tool_version "java" "java -version 2>&1 | head -n 1"
write_tool_version "javac" "javac -version 2>&1 | head -n 1"
write_tool_version "dotnet" "dotnet --version"
write_tool_version "cuda" "nvcc --version | tail -n 1"

note "Capturing ecosystem inventories (pipx/npm/cargo/rustup) if present..."

# pipx
if command -v pipx >/dev/null 2>&1; then
  pipx list --json > "${MAN_DIR}/pipx_list.json" || true
fi

# npm globals
if command -v npm >/dev/null 2>&1; then
  npm -g list --depth=0 --json > "${MAN_DIR}/npm_globals.json" || true
fi

# cargo installs
if command -v cargo >/dev/null 2>&1; then
  cargo install --list > "${MAN_DIR}/cargo_installs.txt" || true
fi

# rustup toolchains
if command -v rustup >/dev/null 2>&1; then
  rustup toolchain list > "${MAN_DIR}/rustup_toolchains.txt" || true
  rustup component list --installed > "${MAN_DIR}/rustup_components.txt" || true
fi

# conda/mamba (inventory only)
if command -v conda >/dev/null 2>&1; then
  conda info > "${MAN_DIR}/conda_info.txt" || true
  conda env list > "${MAN_DIR}/conda_env_list.txt" || true
fi
if command -v mamba >/dev/null 2>&1; then
  mamba info > "${MAN_DIR}/mamba_info.txt" || true
fi

note "Checking for common secret locations (warn only)..."
# We do NOT copy these. We just warn so you remember to handle them.
[ -d "${HOME}/.ssh" ] && warn "Found ~/.ssh (keys are secrets). Do NOT copy blindly."
[ -d "${HOME}/.aws" ] && warn "Found ~/.aws (credentials are secrets). Do NOT copy blindly."
[ -d "${HOME}/.gnupg" ] && warn "Found ~/.gnupg (private keys). Do NOT copy blindly."
[ -f "${HOME}/.netrc" ] && warn "Found ~/.netrc (often contains credentials)."

note "Snapshot complete."
note "Manifests written to: ${MAN_DIR}"
if [ -s "${warn_file}" ]; then
  note "Warnings recorded in: ${warn_file}"
fi
