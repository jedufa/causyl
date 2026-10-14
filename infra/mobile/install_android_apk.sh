#!/usr/bin/env bash
set -euo pipefail

APK_PATH="${1:-}"
if [[ -z "${APK_PATH}" ]]; then
  echo "Usage: $0 <path-to-apk>"
  exit 2
fi

if ! command -v adb >/dev/null 2>&1; then
  echo "ERROR: adb not found in PATH."
  exit 2
fi

if [[ ! -f "${APK_PATH}" ]]; then
  echo "ERROR: APK not found: ${APK_PATH}"
  exit 2
fi

adb devices
adb install -r "${APK_PATH}"
echo "[ok] Installed ${APK_PATH}"
