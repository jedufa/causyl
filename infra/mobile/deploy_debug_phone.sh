#!/usr/bin/env bash
set -euo pipefail

# One-command Android debug deploy for physical phones.
# - Ensures local Android CLI env is loaded
# - Ensures Gradle wrapper exists
# - Builds and installs debug APK over adb
#
# Optional overrides:
#   EIEL_SERVER_BASE_URL (default: http://192.0.2.1:8787)
#   EIEL_CHAT_STREAM_PATH (default: /api/mobile/chat/stream)
#   EIEL_AUTH_TOKEN (default: empty)
#   EIEL_WIRELESS_HOST (optional; used when first arg is only a port)
#
# Optional positional arg:
#   $1 = wireless target as either:
#        - full <ip:port> (example: 192.0.2.1:37325)
#        - port only (example: 37325)
#          In port-only mode, host is taken from EIEL_WIRELESS_HOST or inferred
#          from current "adb devices" wireless entries.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MOBILE_ENV_SCRIPT="${REPO_ROOT}/infra/bin/mobile_agent_env.sh"
APP_ROOT="${REPO_ROOT}/infra/mobile/android"
CACHE_DIR="${REPO_ROOT}/.tooling"
WIRELESS_HOST_CACHE_FILE="${CACHE_DIR}/last_wireless_host.txt"
VERSION_HELPER="${REPO_ROOT}/infra/mobile/next_android_version.py"

SERVER_BASE_URL="${EIEL_SERVER_BASE_URL:-http://192.0.2.1:8787}"
CHAT_STREAM_PATH="${EIEL_CHAT_STREAM_PATH:-/api/mobile/chat/stream}"
AUTH_TOKEN="${EIEL_AUTH_TOKEN:-}"
WIRELESS_ARG="${1:-}"

if [[ $# -gt 1 ]]; then
  echo "Usage: $0 [ip:port|port]"
  exit 2
fi

if [[ ! -f "${MOBILE_ENV_SCRIPT}" ]]; then
  echo "ERROR: missing mobile env helper: ${MOBILE_ENV_SCRIPT}"
  exit 2
fi
if [[ ! -x "${VERSION_HELPER}" ]]; then
  echo "ERROR: missing version helper: ${VERSION_HELPER}"
  exit 2
fi

# shellcheck disable=SC1090
source "${MOBILE_ENV_SCRIPT}"
eval "$("${VERSION_HELPER}" --repo-root "${REPO_ROOT}" --channel "debug" --base-version "0.1.0" --format shell)"

if [[ ! -x "${JAVA_HOME}/bin/java" ]] || [[ ! -x "${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager" ]] || [[ ! -x "${ANDROID_SDK_ROOT}/platform-tools/adb" ]]; then
  echo "[info] Android CLI stack missing or incomplete; installing local stack..."
  "${REPO_ROOT}/infra/mobile/install_android_cli_stack.sh"
  # shellcheck disable=SC1090
  source "${MOBILE_ENV_SCRIPT}"
fi

if [[ ! -x "${APP_ROOT}/gradlew" ]]; then
  echo "[info] Gradle wrapper missing; bootstrapping..."
  "${REPO_ROOT}/infra/mobile/bootstrap_android_gradle_wrapper.sh"
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "ERROR: adb not found after loading ${MOBILE_ENV_SCRIPT}."
  exit 2
fi

_adb_has_connected_device() {
  local cmd="$1"
  "${cmd}" devices 2>/dev/null | tr -d '\r' | awk 'NR>1 && $2=="device"{ok=1} END{exit(ok?0:1)}'
}

_adb_print_devices() {
  local cmd="$1"
  "${cmd}" devices | tr -d '\r'
}

_adb_cmd_is_usable() {
  local cmd="$1"
  timeout 4s "${cmd}" start-server >/dev/null 2>&1
}

# In WSL, choose the ADB instance that actually sees a connected device.
ADB_CMD="adb"
if [[ -n "${ADB_SERVER_SOCKET:-}" ]]; then
  ADB_CMD="adb"
elif grep -qi microsoft /proc/version 2>/dev/null; then
  ADB_WIN="/mnt/c/android-platform-tools/adb.exe"
  if [[ -x "${ADB_WIN}" ]] && _adb_cmd_is_usable "${ADB_WIN}"; then
    if _adb_has_connected_device "adb"; then
      ADB_CMD="adb"
    elif _adb_has_connected_device "${ADB_WIN}"; then
      ADB_CMD="${ADB_WIN}"
    else
      # No connected devices yet; default to local adb so wireless-paired devices
      # in WSL continue to work without forcing Windows adb.exe.
      ADB_CMD="adb"
    fi
  elif [[ -x "${ADB_WIN}" ]]; then
    echo "[warn] Skipping Windows adb.exe; WSL interop probe failed."
  fi
fi

echo "[info] Using adb: ${ADB_CMD}"
if [[ -n "${ADB_SERVER_SOCKET:-}" ]]; then
  echo "[info] ADB server socket: ${ADB_SERVER_SOCKET}"
fi

WIRELESS_TARGET=""
if [[ -n "${WIRELESS_ARG}" ]]; then
  if [[ "${WIRELESS_ARG}" == *:* ]]; then
    WIRELESS_TARGET="${WIRELESS_ARG}"
  elif [[ "${WIRELESS_ARG}" =~ ^[0-9]+$ ]]; then
    WIRELESS_HOST="${EIEL_WIRELESS_HOST:-}"
    if [[ -z "${WIRELESS_HOST}" ]] && [[ -f "${WIRELESS_HOST_CACHE_FILE}" ]]; then
      WIRELESS_HOST="$(tr -d '\r' < "${WIRELESS_HOST_CACHE_FILE}" | head -n1)"
    fi
    if [[ -z "${WIRELESS_HOST}" ]]; then
      WIRELESS_HOST="$("${ADB_CMD}" devices 2>/dev/null | tr -d '\r' | awk 'NR>1{print $1}' | sed -n 's/^\([0-9]\{1,3\}\(\.[0-9]\{1,3\}\)\{3\}\):[0-9]\+$/\1/p' | head -n1)"
    fi
    if [[ -z "${WIRELESS_HOST}" ]]; then
      echo "ERROR: port-only mode needs a host. Provide <ip:port> or set EIEL_WIRELESS_HOST."
      exit 4
    fi
    WIRELESS_TARGET="${WIRELESS_HOST}:${WIRELESS_ARG}"
  else
    echo "ERROR: invalid wireless target '${WIRELESS_ARG}'. Use <ip:port> or <port>."
    exit 4
  fi
fi

if [[ -n "${WIRELESS_TARGET}" ]]; then
  echo "[info] Connecting wireless device: ${WIRELESS_TARGET}"
  "${ADB_CMD}" connect "${WIRELESS_TARGET}" || true
  if [[ "${WIRELESS_TARGET}" =~ ^([0-9]{1,3}(\.[0-9]{1,3}){3}):[0-9]+$ ]]; then
    mkdir -p "${CACHE_DIR}"
    printf '%s\n' "${BASH_REMATCH[1]}" > "${WIRELESS_HOST_CACHE_FILE}"
  fi
fi

_adb_print_devices "${ADB_CMD}"
if ! _adb_has_connected_device "${ADB_CMD}"; then
  echo "ERROR: no connected Android device found by ${ADB_CMD}."
  echo "[hint] If using wireless debugging, run: adb connect <ip:port>"
  echo "[hint] Then verify: adb devices"
  exit 4
fi

cd "${APP_ROOT}"
echo "[info] Gradle user home: ${GRADLE_USER_HOME}"
echo "[info] Building debug APK (first run can take 30-120s while Gradle starts)..."
./gradlew :app:assembleDebug \
  -PEIEL_APP_VERSION_CODE="${EIEL_APP_VERSION_CODE}" \
  -PEIEL_APP_VERSION_NAME="${EIEL_APP_VERSION_NAME}" \
  -PEIEL_SERVER_BASE_URL="${SERVER_BASE_URL}" \
  -PEIEL_CHAT_STREAM_PATH="${CHAT_STREAM_PATH}" \
  -PEIEL_AUTH_TOKEN="${AUTH_TOKEN}"

APK_PATH="app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -f "${APK_PATH}" ]]; then
  echo "ERROR: APK not found at ${APK_PATH}"
  exit 3
fi

 "${ADB_CMD}" install -r "${APK_PATH}" || {
  echo "[warn] install -r failed; trying uninstall/reinstall for com.eiel.chat.debug"
  "${ADB_CMD}" uninstall "com.eiel.chat.debug" || true
  "${ADB_CMD}" install "${APK_PATH}"
}

echo "[ok] Deployed debug APK to connected device."
echo "[info] Server: ${SERVER_BASE_URL}${CHAT_STREAM_PATH}"
echo "[info] Version: ${EIEL_APP_VERSION_NAME} (${EIEL_APP_VERSION_CODE})"
