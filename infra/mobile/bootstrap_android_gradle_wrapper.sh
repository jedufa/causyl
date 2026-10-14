#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
APP_ROOT="${REPO_ROOT}/infra/mobile/android"
GRADLE_VERSION="${GRADLE_VERSION:-8.9}"
BOOTSTRAP_DIR="${REPO_ROOT}/.tooling/gradle-bootstrap"
DIST_BASENAME="gradle-${GRADLE_VERSION}-bin"
DIST_ZIP="${DIST_BASENAME}.zip"
DIST_URL="https://services.gradle.org/distributions/${DIST_ZIP}"
LOCAL_GRADLE="${BOOTSTRAP_DIR}/gradle-${GRADLE_VERSION}/bin/gradle"

mkdir -p "${BOOTSTRAP_DIR}"

pick_gradle() {
  if command -v gradle >/dev/null 2>&1; then
    command -v gradle
    return 0
  fi
  if [[ -x "${LOCAL_GRADLE}" ]]; then
    echo "${LOCAL_GRADLE}"
    return 0
  fi
  return 1
}

download_gradle() {
  local zip_path="${BOOTSTRAP_DIR}/${DIST_ZIP}"
  if [[ ! -f "${zip_path}" ]]; then
    echo "[info] Downloading ${DIST_URL}"
    if command -v curl >/dev/null 2>&1; then
      curl -fL "${DIST_URL}" -o "${zip_path}"
    elif command -v wget >/dev/null 2>&1; then
      wget -O "${zip_path}" "${DIST_URL}"
    else
      echo "ERROR: neither curl nor wget found to download Gradle."
      return 1
    fi
  fi

  if ! command -v unzip >/dev/null 2>&1; then
    echo "ERROR: unzip not found. Install unzip to extract ${DIST_ZIP}."
    return 1
  fi

  if [[ ! -x "${LOCAL_GRADLE}" ]]; then
    echo "[info] Extracting ${zip_path} to ${BOOTSTRAP_DIR}"
    unzip -oq "${zip_path}" -d "${BOOTSTRAP_DIR}"
  fi

  [[ -x "${LOCAL_GRADLE}" ]]
}

GRADLE_BIN="$(pick_gradle || true)"
if [[ -z "${GRADLE_BIN}" ]]; then
  download_gradle || {
    echo "ERROR: unable to bootstrap Gradle ${GRADLE_VERSION}."
    exit 2
  }
  GRADLE_BIN="$(pick_gradle || true)"
fi

if [[ -z "${GRADLE_BIN}" ]]; then
  echo "ERROR: Gradle bootstrap failed."
  exit 2
fi

cd "${APP_ROOT}"
"${GRADLE_BIN}" wrapper --gradle-version "${GRADLE_VERSION}" --distribution-type bin
chmod +x gradlew

echo "[ok] Generated Gradle wrapper in ${APP_ROOT} using ${GRADLE_BIN}"

