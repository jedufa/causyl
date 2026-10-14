#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
MOBILE_ENV_SCRIPT="${REPO_ROOT}/infra/bin/mobile_agent_env.sh"

if [[ ! -f "${MOBILE_ENV_SCRIPT}" ]]; then
  echo "ERROR: missing mobile env helper: ${MOBILE_ENV_SCRIPT}"
  exit 2
fi

# shellcheck disable=SC1090
source "${MOBILE_ENV_SCRIPT}"

SERVER_BASE_URL="${1:-${EIEL_SERVER_BASE_URL:-http://192.0.2.1:8787}}"
PACKAGE_NAME="${EIEL_ANDROID_PACKAGE:-com.eiel.chat.debug}"
HEALTH_URL="${SERVER_BASE_URL%/}/api/health"

require_cmd() {
  local name="$1"
  if ! command -v "${name}" >/dev/null 2>&1; then
    echo "ERROR: missing required command: ${name}"
    exit 2
  fi
}

require_cmd adb
require_cmd curl

scheme="${SERVER_BASE_URL%%://*}"
host_and_path="${SERVER_BASE_URL#*://}"
host_port="${host_and_path%%/*}"
server_host="${host_port%%:*}"
server_port="${host_port#*:}"
if [[ "${server_port}" == "${host_port}" ]]; then
  if [[ "${scheme}" == "https" ]]; then
    server_port="443"
  else
    server_port="80"
  fi
fi

echo "[info] repo: ${REPO_ROOT}"
echo "[info] server: ${SERVER_BASE_URL}"
echo "[info] health: ${HEALTH_URL}"
echo "[info] package: ${PACKAGE_NAME}"
echo "[info] adb socket: ${ADB_SERVER_SOCKET:-local_default}"

first_device_serial="$(
  adb devices | tr -d '\r' | awk 'NR>1 && $2=="device"{print $1; exit}'
)"
if [[ -z "${first_device_serial}" ]]; then
  echo "ERROR: no connected adb device found."
  exit 3
fi

echo "[ok] adb sees device: ${first_device_serial}"

boot_completed="$(
  adb -s "${first_device_serial}" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r[:space:]'
)"
if [[ "${boot_completed}" != "1" ]]; then
  echo "ERROR: device ${first_device_serial} is not fully booted (sys.boot_completed=${boot_completed:-<empty>})."
  exit 4
fi

echo "[ok] device boot completed"

if adb -s "${first_device_serial}" shell pm list packages "${PACKAGE_NAME}" 2>/dev/null | tr -d '\r' | grep -q "^package:${PACKAGE_NAME}$"; then
  echo "[ok] package installed: ${PACKAGE_NAME}"
else
  echo "[warn] package not installed: ${PACKAGE_NAME}"
fi

health_body="$(
  curl -fsS --max-time 5 "${HEALTH_URL}"
)"
if [[ "${health_body}" != *'"status"'* ]] || [[ "${health_body}" != *'"ok"'* ]]; then
  echo "ERROR: unexpected health response from ${HEALTH_URL}"
  printf '%s\n' "${health_body}"
  exit 5
fi

echo "[ok] server health reachable from WSL"

if adb -s "${first_device_serial}" shell "toybox nc -z -w 2 ${server_host} ${server_port}" >/dev/null 2>&1; then
  echo "[ok] device can open ${server_host}:${server_port}"
else
  echo "[warn] device socket probe to ${server_host}:${server_port} failed or toybox nc is unavailable"
fi

echo "[ok] mobile stack smoke test passed"
