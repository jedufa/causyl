#!/usr/bin/env bash
set -euo pipefail

# Build an Android APK on the server and publish it to a stable artifact path
# for the mobile updater flow.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_ROOT="${REPO_ROOT}/infra/mobile/android"
MOBILE_ENV_SCRIPT="${REPO_ROOT}/infra/bin/mobile_agent_env.sh"
PUBLISH_ROOT="${REPO_ROOT}/runtime/artifacts/mobile_apk"
TMP_APK_ROOT="${REPO_ROOT}/tmp/apks"
VERSION_HELPER="${REPO_ROOT}/infra/mobile/next_android_version.py"

CHANNEL="${1:-debug}"
case "${CHANNEL}" in
  debug|release) ;;
  *)
    echo "ERROR: channel must be debug or release"
    exit 2
    ;;
esac

if [[ ! -f "${MOBILE_ENV_SCRIPT}" ]]; then
  echo "ERROR: missing mobile env helper: ${MOBILE_ENV_SCRIPT}"
  exit 2
fi

# shellcheck disable=SC1090
source "${MOBILE_ENV_SCRIPT}"

# The web-triggered APK builder should stay pinned to this repo's own Android
# toolchain when it is installed locally. That avoids cross-repo overrides such
# as `.tooling/android-env.local.sh` pointing at a different checkout.
REPO_JAVA_HOME="${REPO_ROOT}/.tooling/jdk-17"
REPO_ANDROID_SDK_ROOT="${REPO_ROOT}/.tooling/android-sdk"
if [[ -x "${REPO_JAVA_HOME}/bin/java" ]]; then
  export JAVA_HOME="${REPO_JAVA_HOME}"
  export JDK_HOME="${REPO_JAVA_HOME}"
  export PATH="${JAVA_HOME}/bin:${PATH}"
fi
if [[ -d "${REPO_ANDROID_SDK_ROOT}" ]]; then
  export ANDROID_SDK_ROOT="${REPO_ANDROID_SDK_ROOT}"
  export ANDROID_HOME="${REPO_ANDROID_SDK_ROOT}"
  export PATH="${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${PATH}"
fi

SERVER_BASE_URL="${EIEL_SERVER_BASE_URL:-http://192.0.2.1:8787}"
CHAT_STREAM_PATH="${EIEL_CHAT_STREAM_PATH:-/api/mobile/chat/stream}"
AUTH_TOKEN="${EIEL_AUTH_TOKEN:-}"

if [[ ! -x "${VERSION_HELPER}" ]]; then
  echo "ERROR: missing version helper: ${VERSION_HELPER}"
  exit 2
fi

eval "$("${VERSION_HELPER}" --repo-root "${REPO_ROOT}" --channel "${CHANNEL}" --base-version "0.1.0" --format shell)"

cd "${APP_ROOT}"

# APK builds repeat the same task shape frequently. Configuration cache can
# cut repeated web-triggered startup time without changing the APK contents.
GRADLE_APK_FLAGS=(--configuration-cache)

if [[ "${CHANNEL}" == "debug" ]]; then
  APK_PREFIX="causyl-debug"
  rm -f "${APP_ROOT}/app/build/outputs/apk/debug/app-debug.apk"
  build_started_epoch="$(date +%s)"
  ./gradlew "${GRADLE_APK_FLAGS[@]}" :app:assembleDebug \
    -PEIEL_APP_VERSION_CODE="${EIEL_APP_VERSION_CODE}" \
    -PEIEL_APP_VERSION_NAME="${EIEL_APP_VERSION_NAME}" \
    -PEIEL_SERVER_BASE_URL="${SERVER_BASE_URL}" \
    -PEIEL_CHAT_STREAM_PATH="${CHAT_STREAM_PATH}" \
    -PEIEL_AUTH_TOKEN="${AUTH_TOKEN}"
  APK_PATH="${APP_ROOT}/app/build/outputs/apk/debug/app-debug.apk"
else
  APK_PREFIX="causyl-release"
  rm -f "${APP_ROOT}/app/build/outputs/apk/release/app-release.apk"
  build_started_epoch="$(date +%s)"
  ./gradlew "${GRADLE_APK_FLAGS[@]}" :app:assembleRelease \
    -PEIEL_APP_VERSION_CODE="${EIEL_APP_VERSION_CODE}" \
    -PEIEL_APP_VERSION_NAME="${EIEL_APP_VERSION_NAME}" \
    -PEIEL_SERVER_BASE_URL="${SERVER_BASE_URL}" \
    -PEIEL_CHAT_STREAM_PATH="${CHAT_STREAM_PATH}" \
    -PEIEL_AUTH_TOKEN="${AUTH_TOKEN}" \
    -PEIEL_RELEASE_STORE_FILE="${EIEL_RELEASE_STORE_FILE:-}" \
    -PEIEL_RELEASE_STORE_PASSWORD="${EIEL_RELEASE_STORE_PASSWORD:-}" \
    -PEIEL_RELEASE_KEY_ALIAS="${EIEL_RELEASE_KEY_ALIAS:-}" \
    -PEIEL_RELEASE_KEY_PASSWORD="${EIEL_RELEASE_KEY_PASSWORD:-}"
  APK_PATH="${APP_ROOT}/app/build/outputs/apk/release/app-release.apk"
fi
VERSION_SLUG="$(printf '%s' "${EIEL_APP_VERSION_NAME}" | tr -c '[:alnum:]._-' '-')"
APK_NAME="${APK_PREFIX}-${VERSION_SLUG}.apk"

if [[ ! -f "${APK_PATH}" ]]; then
  echo "ERROR: APK not found at ${APK_PATH}"
  exit 3
fi

apk_mtime_epoch="$(stat -c %Y "${APK_PATH}" 2>/dev/null || echo 0)"
if (( apk_mtime_epoch < build_started_epoch )); then
  echo "ERROR: build did not produce a fresh APK at ${APK_PATH}" >&2
  exit 4
fi

mkdir -p "${PUBLISH_ROOT}/${CHANNEL}"
cp "${APK_PATH}" "${PUBLISH_ROOT}/${CHANNEL}/${APK_NAME}"
cat > "${PUBLISH_ROOT}/${CHANNEL}/${APK_NAME}.meta.json" <<EOF
{"schema_version":"mobile_apk_release_meta_v1","channel":"${CHANNEL}","apk_name":"${APK_NAME}","version_code":${EIEL_APP_VERSION_CODE},"version_name":"${EIEL_APP_VERSION_NAME}","built_at":"${EIEL_APP_VERSION_ALLOCATED_UTC}","server_base_url":"${SERVER_BASE_URL}","chat_stream_path":"${CHAT_STREAM_PATH}"}
EOF
mkdir -p "${TMP_APK_ROOT}"
cp "${APK_PATH}" "${TMP_APK_ROOT}/${APK_PREFIX}.apk"
cat > "${TMP_APK_ROOT}/README.md" <<EOF
# Causyl APK build modes

Debug APK:
- Path: ./tmp/apks/causyl-debug.apk
- Install: adb install -r ./tmp/apks/causyl-debug.apk
- Server: ${SERVER_BASE_URL}
- Chat stream path: ${CHAT_STREAM_PATH}

Release APK:
- Path: ./tmp/apks/causyl-release.apk
- Install: adb install -r ./tmp/apks/causyl-release.apk
- Server: ${SERVER_BASE_URL}
- Chat stream path: ${CHAT_STREAM_PATH}
EOF

# F5M6: Write a restart-safe release receipt for artifact lineage
APK_PUBLISHED="${PUBLISH_ROOT}/${CHANNEL}/${APK_NAME}"
if command -v sha256sum >/dev/null 2>&1; then
  APK_SHA256="$(sha256sum "${APK_PUBLISHED}" | awk "{print \$1}")"
elif command -v shasum >/dev/null 2>&1; then
  APK_SHA256="$(shasum -a 256 "${APK_PUBLISHED}" | awk "{print \$1}")"
else
  APK_SHA256=""
fi
RECEIPT_DIR="${REPO_ROOT}/runtime/state/mobile_apk/releases/${CHANNEL}/receipts"
mkdir -p "${RECEIPT_DIR}"
UTC_NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
JOB_ID="${EIEL_ADMIN_JOB_ID:-}"
RECEIPT_PATH="${RECEIPT_DIR}/rr_${UTC_NOW}__${CHANNEL}.json"
SOURCE_SNAPSHOT_IDENTITY="$(git -C "${REPO_ROOT}" rev-parse --verify HEAD 2>/dev/null || true)"
if [[ -z "${SOURCE_SNAPSHOT_IDENTITY}" ]]; then
  SOURCE_SNAPSHOT_IDENTITY="workspace_unversioned"
fi
VALIDATION_SCOPE="${EIEL_MOBILE_APK_VALIDATION_SCOPE:-artifact_build_only}"
CHANNEL_PUBLICATION_STATE="${EIEL_MOBILE_APK_CHANNEL_PUBLICATION_STATE:-published_local_artifact}"
VALIDATION_COMMANDS_JSON="${EIEL_MOBILE_APK_VALIDATION_COMMANDS_JSON:-[]}"
PROOF_GAP_RAW="$(printf '%s' "${EIEL_MOBILE_APK_PROOF_GAP:-true}" | tr '[:upper:]' '[:lower:]')"
if [[ "${PROOF_GAP_RAW}" == "false" || "${PROOF_GAP_RAW}" == "0" || "${PROOF_GAP_RAW}" == "no" ]]; then
  PROOF_GAP_JSON=false
else
  PROOF_GAP_JSON=true
fi
PROOF_GAP_REASON="${EIEL_MOBILE_APK_PROOF_GAP_REASON:-}"
if [[ "${PROOF_GAP_JSON}" == true && -z "${PROOF_GAP_REASON}" ]]; then
  PROOF_GAP_REASON="no_post_build_validation_recorded"
fi
cat > "${RECEIPT_PATH}" <<EOF
{"schema_version":"mobile_apk_release_receipt_v1","utc":"${UTC_NOW}","job_id":"${JOB_ID}","channel":"${CHANNEL}","channel_publication_state":"${CHANNEL_PUBLICATION_STATE}","apk_name":"${APK_NAME}","apk_sha256":"${APK_SHA256}","version_code":${EIEL_APP_VERSION_CODE},"version_name":"${EIEL_APP_VERSION_NAME}","built_at":"${EIEL_APP_VERSION_ALLOCATED_UTC}","source_path":"runtime/artifacts/mobile_apk/${CHANNEL}/${APK_NAME}","source_snapshot_identity":"${SOURCE_SNAPSHOT_IDENTITY}","validation_scope":"${VALIDATION_SCOPE}","validation_commands":${VALIDATION_COMMANDS_JSON},"proof_gap":${PROOF_GAP_JSON},"proof_gap_reason":"${PROOF_GAP_REASON}","artifact_lineage":{"published_apk_path":"runtime/artifacts/mobile_apk/${CHANNEL}/${APK_NAME}","meta_sidecar_path":"runtime/artifacts/mobile_apk/${CHANNEL}/${APK_NAME}.meta.json","receipt_relpath":"runtime/state/mobile_apk/releases/${CHANNEL}/receipts/$(basename "${RECEIPT_PATH}")"}}
EOF
echo "[ok] Built ${CHANNEL} APK"
echo "[info] Published: ${PUBLISH_ROOT}/${CHANNEL}/${APK_NAME}"
echo "[info] Stable copy: ${TMP_APK_ROOT}/${APK_PREFIX}.apk"
echo "[info] Version: ${EIEL_APP_VERSION_NAME} (${EIEL_APP_VERSION_CODE})"
echo "[info] Server: ${SERVER_BASE_URL}"
