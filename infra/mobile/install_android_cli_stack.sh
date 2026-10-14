#!/usr/bin/env bash
set -euo pipefail

# Installs a local Android CLI toolchain (JDK + cmdline-tools + SDK packages)
# under the repository `.tooling/` directory, without requiring Android Studio.

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TOOLING_DIR="${REPO_ROOT}/.tooling"

JDK_VERSION="${JDK_VERSION:-17}"
JDK_ARCHIVE="${TOOLING_DIR}/jdk-${JDK_VERSION}.tar.gz"
JDK_DIR="${TOOLING_DIR}/jdk-${JDK_VERSION}"
JDK_URL="${JDK_URL:-https://api.adoptium.net/v3/binary/latest/${JDK_VERSION}/ga/linux/x64/jdk/hotspot/normal/eclipse}"

ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT:-${TOOLING_DIR}/android-sdk}"
ANDROID_CMDLINE_ZIP="${TOOLING_DIR}/commandlinetools-linux.zip"
ANDROID_CMDLINE_URL="${ANDROID_CMDLINE_URL:-https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip}"

ANDROID_PLATFORM="${ANDROID_PLATFORM:-android-35}"
ANDROID_BUILD_TOOLS="${ANDROID_BUILD_TOOLS:-35.0.0}"
ANDROID_PACKAGES=(
  "platform-tools"
  "platforms;${ANDROID_PLATFORM}"
  "build-tools;${ANDROID_BUILD_TOOLS}"
)

mkdir -p "${TOOLING_DIR}"

download_file() {
  local url="$1"
  local out="$2"
  if [[ -f "${out}" ]]; then
    return 0
  fi
  if command -v curl >/dev/null 2>&1; then
    curl -fL "${url}" -o "${out}"
  elif command -v wget >/dev/null 2>&1; then
    wget -O "${out}" "${url}"
  else
    echo "ERROR: curl/wget not found."
    exit 2
  fi
}

install_jdk() {
  if [[ -d "${JDK_DIR}" ]] && [[ -x "${JDK_DIR}/bin/java" ]]; then
    return 0
  fi
  echo "[info] Installing JDK ${JDK_VERSION} into ${JDK_DIR}"
  download_file "${JDK_URL}" "${JDK_ARCHIVE}"
  rm -rf "${JDK_DIR}"
  mkdir -p "${JDK_DIR}"
  tar -xzf "${JDK_ARCHIVE}" -C "${JDK_DIR}" --strip-components=1
}

install_cmdline_tools() {
  local latest_dir="${ANDROID_SDK_ROOT}/cmdline-tools/latest"
  if [[ -x "${latest_dir}/bin/sdkmanager" ]]; then
    return 0
  fi
  echo "[info] Installing Android command-line tools into ${ANDROID_SDK_ROOT}"
  download_file "${ANDROID_CMDLINE_URL}" "${ANDROID_CMDLINE_ZIP}"
  mkdir -p "${ANDROID_SDK_ROOT}/cmdline-tools"
  rm -rf "${latest_dir}"
  local tmp_extract
  tmp_extract="$(mktemp -d)"
  unzip -oq "${ANDROID_CMDLINE_ZIP}" -d "${tmp_extract}"
  mv "${tmp_extract}/cmdline-tools" "${latest_dir}"
  rm -rf "${tmp_extract}"
}

install_sdk_packages() {
  export JAVA_HOME="${JDK_DIR}"
  export ANDROID_SDK_ROOT
  export PATH="${JAVA_HOME}/bin:${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin:${ANDROID_SDK_ROOT}/platform-tools:${PATH}"

  # sdkmanager exits after consuming enough input; `yes` then gets SIGPIPE.
  set +o pipefail
  yes | sdkmanager --sdk_root="${ANDROID_SDK_ROOT}" --licenses >/dev/null
  set -o pipefail
  sdkmanager --sdk_root="${ANDROID_SDK_ROOT}" "${ANDROID_PACKAGES[@]}"
}

write_local_override_file() {
  local env_file="${TOOLING_DIR}/android-env.local.sh"
  cat > "${env_file}" <<EOF
#!/usr/bin/env bash
export JAVA_HOME="${JDK_DIR}"
export ANDROID_SDK_ROOT="${ANDROID_SDK_ROOT}"
export ANDROID_HOME="\${ANDROID_SDK_ROOT}"
EOF
  chmod +x "${env_file}"
  echo "[ok] Wrote local Android env override: ${env_file}"
}

install_jdk
install_cmdline_tools
install_sdk_packages
write_local_override_file

echo "[ok] Android CLI stack installed."
echo "[next] source infra/mobile/android_env.sh"
echo "[next] infra/mobile/bootstrap_android_gradle_wrapper.sh"
echo "[next] cd infra/mobile/android && ./gradlew :app:testDebugUnitTest"
