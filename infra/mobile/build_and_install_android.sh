#!/usr/bin/env bash
set -euo pipefail

# Build + install Android APK over ADB.
# Defaults are for local/internal development.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_ROOT="${REPO_ROOT}/infra/mobile/android"
BUILD_TYPE="${BUILD_TYPE:-debug}"   # debug|release
PACKAGE_DEBUG="com.eiel.chat.debug"
PACKAGE_RELEASE="com.eiel.chat"
MOBILE_ENV_SCRIPT="${REPO_ROOT}/infra/bin/mobile_agent_env.sh"
VERSION_HELPER="${REPO_ROOT}/infra/mobile/next_android_version.py"

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
eval "$("${VERSION_HELPER}" --repo-root "${REPO_ROOT}" --channel "${BUILD_TYPE}" --base-version "0.1.0" --format shell)"

cd "${APP_ROOT}"

if ! command -v adb >/dev/null 2>&1; then
  echo "ERROR: adb not found in PATH. Install Android platform-tools."
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
      ADB_CMD="adb"
    fi
  elif [[ -x "${ADB_WIN}" ]]; then
    echo "[warn] Skipping Windows adb.exe; WSL interop probe failed."
  fi
fi

if [[ -x "./gradlew" ]]; then
  GRADLE_CMD="./gradlew"
elif command -v gradle >/dev/null 2>&1; then
  GRADLE_CMD="gradle"
else
  echo "ERROR: Neither ./gradlew nor system gradle is available."
  exit 2
fi

echo "[info] Using adb: ${ADB_CMD}"
if [[ -n "${ADB_SERVER_SOCKET:-}" ]]; then
  echo "[info] ADB server socket: ${ADB_SERVER_SOCKET}"
fi
echo "[info] Gradle user home: ${GRADLE_USER_HOME}"
_adb_print_devices "${ADB_CMD}"
if ! _adb_has_connected_device "${ADB_CMD}"; then
  echo "ERROR: no connected Android device found by ${ADB_CMD}."
  echo "[hint] If using wireless debugging, run: adb connect <ip:port>"
  echo "[hint] Then verify: adb devices"
  exit 4
fi

"${GRADLE_CMD}" clean "assemble${BUILD_TYPE^}" \
  -PEIEL_APP_VERSION_CODE="${EIEL_APP_VERSION_CODE}" \
  -PEIEL_APP_VERSION_NAME="${EIEL_APP_VERSION_NAME}"

APK_PATH="app/build/outputs/apk/${BUILD_TYPE}/app-${BUILD_TYPE}.apk"
if [[ ! -f "${APK_PATH}" ]]; then
  echo "ERROR: APK not found at ${APK_PATH}"
  exit 3
fi

echo "[info] APK: ${APK_PATH}"

if [[ "${BUILD_TYPE}" == "release" ]]; then
  PACKAGE_NAME="${PACKAGE_RELEASE}"
else
  PACKAGE_NAME="${PACKAGE_DEBUG}"
fi

"${ADB_CMD}" install -r "${APK_PATH}" || {
  echo "[warn] install -r failed; trying uninstall/reinstall for ${PACKAGE_NAME}"
  "${ADB_CMD}" uninstall "${PACKAGE_NAME}" || true
  "${ADB_CMD}" install "${APK_PATH}"
}

echo "[ok] Installed ${PACKAGE_NAME}"
echo "[info] Version: ${EIEL_APP_VERSION_NAME} (${EIEL_APP_VERSION_CODE})"
