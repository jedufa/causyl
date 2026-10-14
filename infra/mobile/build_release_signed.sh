#!/usr/bin/env bash
set -euo pipefail

# Build a signed release APK/AAB using env vars or -P Gradle properties.
#
# Required (env or Gradle -P):
#   EIEL_RELEASE_STORE_FILE
#   EIEL_RELEASE_STORE_PASSWORD
#   EIEL_RELEASE_KEY_ALIAS
#   EIEL_RELEASE_KEY_PASSWORD
#
# Optional:
#   EIEL_SERVER_BASE_URL
#   EIEL_CHAT_STREAM_PATH
#   EIEL_AUTH_TOKEN
#   EIEL_BUILD_BUNDLE=true   (builds .aab in addition to APK)

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_ROOT="${REPO_ROOT}/infra/mobile/android"
MOBILE_ENV_SCRIPT="${REPO_ROOT}/infra/bin/mobile_agent_env.sh"
VERSION_HELPER="${REPO_ROOT}/infra/mobile/next_android_version.py"

need_var() {
  local name="$1"
  if [[ -z "${!name:-}" ]]; then
    echo "ERROR: missing required env var: ${name}"
    exit 2
  fi
}

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
eval "$("${VERSION_HELPER}" --repo-root "${REPO_ROOT}" --channel "release" --base-version "0.1.0" --format shell)"

need_var "EIEL_RELEASE_STORE_FILE"
need_var "EIEL_RELEASE_STORE_PASSWORD"
need_var "EIEL_RELEASE_KEY_ALIAS"
need_var "EIEL_RELEASE_KEY_PASSWORD"

if [[ ! -f "${EIEL_RELEASE_STORE_FILE}" ]]; then
  echo "ERROR: keystore file not found: ${EIEL_RELEASE_STORE_FILE}"
  exit 2
fi

SERVER_BASE_URL="${EIEL_SERVER_BASE_URL:-http://192.0.2.1:8787}"
CHAT_STREAM_PATH="${EIEL_CHAT_STREAM_PATH:-/api/mobile/chat/stream}"
AUTH_TOKEN="${EIEL_AUTH_TOKEN:-}"

cd "${APP_ROOT}"
echo "[info] Gradle user home: ${GRADLE_USER_HOME}"

./gradlew :app:assembleRelease \
  -PEIEL_APP_VERSION_CODE="${EIEL_APP_VERSION_CODE}" \
  -PEIEL_APP_VERSION_NAME="${EIEL_APP_VERSION_NAME}" \
  -PEIEL_SERVER_BASE_URL="${SERVER_BASE_URL}" \
  -PEIEL_CHAT_STREAM_PATH="${CHAT_STREAM_PATH}" \
  -PEIEL_AUTH_TOKEN="${AUTH_TOKEN}" \
  -PEIEL_RELEASE_STORE_FILE="${EIEL_RELEASE_STORE_FILE}" \
  -PEIEL_RELEASE_STORE_PASSWORD="${EIEL_RELEASE_STORE_PASSWORD}" \
  -PEIEL_RELEASE_KEY_ALIAS="${EIEL_RELEASE_KEY_ALIAS}" \
  -PEIEL_RELEASE_KEY_PASSWORD="${EIEL_RELEASE_KEY_PASSWORD}"

if [[ "${EIEL_BUILD_BUNDLE:-false}" == "true" ]]; then
  ./gradlew :app:bundleRelease \
    -PEIEL_APP_VERSION_CODE="${EIEL_APP_VERSION_CODE}" \
    -PEIEL_APP_VERSION_NAME="${EIEL_APP_VERSION_NAME}" \
    -PEIEL_RELEASE_STORE_FILE="${EIEL_RELEASE_STORE_FILE}" \
    -PEIEL_RELEASE_STORE_PASSWORD="${EIEL_RELEASE_STORE_PASSWORD}" \
    -PEIEL_RELEASE_KEY_ALIAS="${EIEL_RELEASE_KEY_ALIAS}" \
    -PEIEL_RELEASE_KEY_PASSWORD="${EIEL_RELEASE_KEY_PASSWORD}"
fi

echo "[ok] Release build complete."
echo "[info] APK: ${APP_ROOT}/app/build/outputs/apk/release/app-release.apk"
echo "[info] Version: ${EIEL_APP_VERSION_NAME} (${EIEL_APP_VERSION_CODE})"
if [[ "${EIEL_BUILD_BUNDLE:-false}" == "true" ]]; then
  echo "[info] AAB: ${APP_ROOT}/app/build/outputs/bundle/release/app-release.aab"
fi
