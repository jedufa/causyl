#!/usr/bin/env bash
set -euo pipefail

# Pull on-device app logs written by AppLogger.
#
# Usage:
#   infra/mobile/pull_mobile_app_logs.sh [debug|release]
#
# Output directory:
#   logs/mobile/

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
OUT_DIR="${REPO_ROOT}/logs/mobile"
MODE="${1:-debug}"

if ! command -v adb >/dev/null 2>&1; then
  echo "ERROR: adb not found."
  exit 2
fi

if [[ "${MODE}" == "release" ]]; then
  PKG="com.eiel.chat"
else
  PKG="com.eiel.chat.debug"
fi

mkdir -p "${OUT_DIR}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_FILE="${OUT_DIR}/mobile_app_${MODE}_${TS}.log"

echo "[info] Pulling logs from package: ${PKG}"
if adb exec-out run-as "${PKG}" cat files/mobile_app.log > "${OUT_FILE}" 2>/dev/null; then
  echo "[ok] Wrote ${OUT_FILE}"
else
  echo "[warn] Could not read app file log via run-as (package may be non-debuggable or unavailable)."
  echo "[info] Falling back to logcat grep (last 300 lines for tag EIELChat)."
  adb logcat -d -s EIELChat:D | tail -n 300 > "${OUT_FILE}" || true
  echo "[ok] Wrote fallback logcat extract to ${OUT_FILE}"
fi
