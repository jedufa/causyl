#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/mobile_agent_env.sh"

probe_timeout_s="${EIEL_MOBILE_ENV_PROBE_TIMEOUT_S:-1800}"

run_probe() {
  local label="$1"
  shift
  echo "[probe] ${label}"
  if command -v timeout >/dev/null 2>&1; then
    if ! timeout "${probe_timeout_s}s" "$@"; then
      echo "[probe] ${label}: failed or timed out"
    fi
  else
    if ! "$@"; then
      echo "[probe] ${label}: failed"
    fi
  fi
}

echo "EIEL_MOBILE_ENV_PROBE_TIMEOUT_S=${probe_timeout_s}"
echo "EIEL_REPO_ROOT=${EIEL_REPO_ROOT}"
echo "JAVA_HOME=${JAVA_HOME}"
echo "JDK_HOME=${JDK_HOME}"
echo "ANDROID_SDK_ROOT=${ANDROID_SDK_ROOT}"
echo "ANDROID_HOME=${ANDROID_HOME}"
echo "GRADLE_USER_HOME=${GRADLE_USER_HOME}"
echo "ANDROID_USER_HOME=${ANDROID_USER_HOME}"
echo "ANDROID_AVD_HOME=${ANDROID_AVD_HOME}"
echo "ANDROID_EMULATOR_HOME=${ANDROID_EMULATOR_HOME}"
echo "ADB_SERVER_SOCKET=${ADB_SERVER_SOCKET:-}"
echo "EIEL_ADB_SERVER_SOCKET_SOURCE=${EIEL_ADB_SERVER_SOCKET_SOURCE:-}"
echo "EIEL_ADB_SERVER_SOCKET_STATUS=${EIEL_ADB_SERVER_SOCKET_STATUS:-}"
echo "EIEL_AGENTS_BACKEND=${EIEL_AGENTS_BACKEND:-}"
echo "EIEL_CODEX_PROTOCOL=${EIEL_CODEX_PROTOCOL:-}"
echo "EIEL_CODEX_UNRESTRICTED=${EIEL_CODEX_UNRESTRICTED:-}"

echo "--- tool resolution ---"
for tool_name in java javac sdkmanager adb emulator; do
  if command -v "${tool_name}" >/dev/null 2>&1; then
    echo "${tool_name}: $(command -v "${tool_name}")"
  else
    echo "${tool_name}: missing"
  fi
done

echo "--- versions ---"
run_probe "java -version" java -version
run_probe "javac -version" javac -version
run_probe "sdkmanager --version" sdkmanager --version
run_probe "adb version" adb version
if command -v emulator >/dev/null 2>&1; then
  run_probe "emulator -version" bash -lc 'emulator -version | sed -n "1,3p"'
else
  echo "emulator: not installed"
fi

echo "--- gradle ---"
run_probe "gradlew -version" bash -lc '
  cd "${EIEL_REPO_ROOT}/infra/mobile/android"
  ./gradlew -version
'
