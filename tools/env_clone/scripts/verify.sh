#!/usr/bin/env bash
# -----------------------------------------------------------------------------
# verify.sh
# -----------------------------------------------------------------------------
# Purpose:
#   Compare current machine state to the captured manifests.
#   This is how you catch drift instead of guessing.
#
# Outputs:
#   - Prints a report to stdout
#   - Writes manifests/verify_report.txt
# -----------------------------------------------------------------------------

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: bash scripts/verify.sh [--strict] [--manifests-dir <path>]

Options:
  --strict                 Fail on missing critical tools.
  --manifests-dir <path>   Read manifests from an alternate directory.
  -h, --help               Show this help text.
EOF
}

STRICT=0
MAN_DIR_OVERRIDE=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --strict)
      STRICT=1
      shift
      ;;
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

CONTINUE_ON_ERROR=${CONTINUE_ON_ERROR:-1}

run_check() {
  # Purpose: Run a check command; never stop the whole verify unless STRICT or CONTINUE_ON_ERROR=0.
  # Args: $1 check_name, $2... command
  local name="$1"; shift
  if "$@"; then
    return 0
  else
    echo "CHECK FAILED: ${name}" | tee -a "${REPORT}" >/dev/null
    if [ "${STRICT}" = "1" ] || [ "${CONTINUE_ON_ERROR}" = "0" ]; then
      exit 10
    fi
    return 0
  fi
}



SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
MAN_DIR="${MAN_DIR_OVERRIDE:-${ROOT_DIR}/manifests}"
REPORT="${MAN_DIR}/verify_report.txt"
mkdir -p "${MAN_DIR}"

note() { printf "[verify] %s\n" "$*"; }

: > "${REPORT}"

section() {
  printf "\n=== %s ===\n" "$1" | tee -a "${REPORT}"
}

tool_ver() {
  # Purpose: Run a version command and normalize to one line.
  # Args: $1 command string
  bash -lc "$1" 2>/dev/null | head -n 1 | tr -d '\r'
}

section "System identity"
{
  echo "DATE_UTC=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "HOSTNAME=$(hostname)"
  echo "KERNEL=$(uname -r)"
  [ -f /etc/os-release ] && grep -E '^(PRETTY_NAME|VERSION_ID|VERSION_CODENAME)=' /etc/os-release || true
} | tee -a "${REPORT}"

section "APT package drift (manual packages)"
if [ -f "${MAN_DIR}/apt_manual_packages.tsv" ] && command -v dpkg-query >/dev/null 2>&1; then
  # Build a temp list of current versions for the manual package set.
  tmp_current="$(mktemp)"
  while IFS=$'\t' read -r pkg _ver; do
    [ -z "${pkg}" ] && continue
    cur="$(dpkg-query -W -f='${Version}' "${pkg}" 2>/dev/null || true)"
    if [ -n "${cur}" ]; then
      printf "%s\t%s\n" "${pkg}" "${cur}" >> "${tmp_current}"
    else
      printf "%s\t%s\n" "${pkg}" "<NOT INSTALLED>" >> "${tmp_current}"
    fi
  done < <(tail -n +1 "${MAN_DIR}/apt_manual_packages.tsv")

  # Compare expected vs current
  # Expected file format: pkg \t version
  diff_out="$(diff -u "${MAN_DIR}/apt_manual_packages.tsv" <(sort "${tmp_current}") || true)"
  if [ -z "${diff_out}" ]; then
    echo "PASS: manual packages match snapshot versions (or close enough ordering)." | tee -a "${REPORT}"
  else
    echo "NOTE: manual package drift detected (diff below)." | tee -a "${REPORT}"
    echo "${diff_out}" | tee -a "${REPORT}"
  fi
  rm -f "${tmp_current}"
else
  echo "SKIP: apt_manual_packages.tsv missing or dpkg-query missing." | tee -a "${REPORT}"
fi


section "Critical tools presence"
crit_file="${MAN_DIR}/critical_tools.txt"
if [ -f "${crit_file}" ]; then
  missing=0
  while read -r tool; do
    [[ "${tool}" =~ ^\s*$ ]] && continue
    [[ "${tool}" =~ ^\s*# ]] && continue
    if ! command -v "${tool}" >/dev/null 2>&1; then
      echo "MISSING: ${tool}" | tee -a "${REPORT}"
      missing=1
    fi
  done < "${crit_file}"
  if [ "${missing}" = "0" ]; then
    echo "PASS: all critical tools present" | tee -a "${REPORT}"
  else
    echo "NOTE: missing critical tools detected" | tee -a "${REPORT}"
    if [ "${STRICT}" = "1" ]; then
      echo "STRICT mode: failing due to missing critical tools." | tee -a "${REPORT}"
      exit 2
    fi
  fi
else
  echo "SKIP: critical_tools.txt missing." | tee -a "${REPORT}"
fi

section "Tool version drift"
if [ -f "${MAN_DIR}/tool_versions.tsv" ]; then
  # Current tool versions (subset) and compare lines
  tmp_tools="$(mktemp)"
  tail -n +2 "${MAN_DIR}/tool_versions.tsv" | while IFS=$'\t' read -r tool expected; do
    [ -z "${tool}" ] && continue
    if command -v "${tool}" >/dev/null 2>&1; then
      # Use common version patterns where possible
      case "${tool}" in
        bash) cur="$(tool_ver 'bash --version | head -n 1')" ;;
        git) cur="$(tool_ver 'git --version')" ;;
        tmux) cur="$(tool_ver 'tmux -V')" ;;
        python3) cur="$(tool_ver 'python3 --version')" ;;
        pip3) cur="$(tool_ver 'pip3 --version')" ;;
        node) cur="$(tool_ver 'node --version')" ;;
        npm) cur="$(tool_ver 'npm --version')" ;;
        go) cur="$(tool_ver 'go version')" ;;
        rustc) cur="$(tool_ver 'rustc --version')" ;;
        cargo) cur="$(tool_ver 'cargo --version')" ;;
        rustup) cur="$(tool_ver 'rustup --version')" ;;
        dotnet) cur="$(tool_ver 'dotnet --version')" ;;
        java) cur="$(tool_ver 'java -version 2>&1 | head -n 1')" ;;
        javac) cur="$(tool_ver 'javac -version 2>&1 | head -n 1')" ;;
        *) cur="$(tool_ver "${tool} --version")" ;;
      esac
    else
      cur="<NOT INSTALLED>"
    fi
    printf "%s\t%s\t%s\n" "${tool}" "${expected}" "${cur}" >> "${tmp_tools}"
  done

  printf "tool\texpected\tcurrent\n" | tee -a "${REPORT}"
  sort "${tmp_tools}" | tee -a "${REPORT}"

  if [ "${STRICT}" = "1" ]; then
    if grep -q "<NOT INSTALLED>" "${REPORT}"; then
      echo "STRICT mode: failing due to missing tools." | tee -a "${REPORT}"
      rm -f "${tmp_tools}"
      exit 3
    fi
  fi

  rm -f "${tmp_tools}"
else
  echo "SKIP: tool_versions.tsv missing." | tee -a "${REPORT}"
fi

section "Secrets sanity check (warn only)"
[ -d "${HOME}/.ssh" ] && echo "WARN: ~/.ssh exists (do not copy private keys blindly)" | tee -a "${REPORT}"
[ -d "${HOME}/.aws" ] && echo "WARN: ~/.aws exists (credentials)" | tee -a "${REPORT}"
[ -d "${HOME}/.gnupg" ] && echo "WARN: ~/.gnupg exists (private keys)" | tee -a "${REPORT}"
[ -f "${HOME}/.netrc" ] && echo "WARN: ~/.netrc exists (often credentials)" | tee -a "${REPORT}"

note "Verification complete. Report: ${REPORT}"
