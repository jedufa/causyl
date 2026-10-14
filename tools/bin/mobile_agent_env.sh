#!/usr/bin/env bash
#
# Android/mobile environment bootstrap.
# Safe for both:
# - sourced usage (interactive shells, wrappers)
# - executed usage
#
# Guarantees:
# - temporary strict mode while configuring
# - caller shell options/traps restored after completion
#

__mobile_agent_env_prev_set__="$(set +o | sed '/^set [+-]o history$/d')"
__mobile_agent_env_prev_history__="$(set +o | awk '/^set [+-]o history$/ {print; exit}')"
__mobile_agent_env_prev_trap_return__="$(trap -p RETURN || true)"
__mobile_agent_env_prev_trap_exit__="$(trap -p EXIT || true)"

__mobile_agent_env_restore__() {
  # Restore `history` last so these bookkeeping commands do not pollute the
  # caller's interactive history when this file is sourced from ~/.bashrc.
  set +o history 2>/dev/null || true
  eval "${__mobile_agent_env_prev_set__}"

  if [[ -n "${__mobile_agent_env_prev_trap_return__}" ]]; then
    eval "${__mobile_agent_env_prev_trap_return__}"
  else
    trap - RETURN 2>/dev/null || true
  fi

  if [[ -n "${__mobile_agent_env_prev_trap_exit__}" ]]; then
    eval "${__mobile_agent_env_prev_trap_exit__}"
  else
    trap - EXIT 2>/dev/null || true
  fi

  if [[ -n "${__mobile_agent_env_prev_history__}" ]]; then
    eval "${__mobile_agent_env_prev_history__}"
  fi

  unset __mobile_agent_env_prev_set__ __mobile_agent_env_prev_history__ __mobile_agent_env_prev_trap_return__ __mobile_agent_env_prev_trap_exit__
}

_mobile_agent_probe_tcp() {
  local host="$1"
  local port="$2"
  if ! command -v bash >/dev/null 2>&1; then
    return 1
  fi
  if command -v timeout >/dev/null 2>&1; then
    timeout 1 bash -lc "cat < /dev/null > /dev/tcp/${host}/${port}" >/dev/null 2>&1
  else
    bash -lc "cat < /dev/null > /dev/tcp/${host}/${port}" >/dev/null 2>&1
  fi
}

_mobile_agent_probe_adb_protocol() {
  local socket="$1"
  local adb_bin=""
  adb_bin="$(command -v adb 2>/dev/null || true)"
  if [[ -z "${adb_bin}" ]] || [[ ! -x "${adb_bin}" ]]; then
    return 1
  fi
  if command -v timeout >/dev/null 2>&1; then
    env ADB_SERVER_SOCKET="${socket}" timeout 4 "${adb_bin}" start-server >/dev/null 2>&1
  else
    env ADB_SERVER_SOCKET="${socket}" "${adb_bin}" start-server >/dev/null 2>&1
  fi
}

_mobile_agent_try_adb_socket() {
  local source_label="$1"
  local host="$2"
  local port="$3"
  local socket=""
  if [[ -z "${host}" ]] || [[ -z "${port}" ]]; then
    return 1
  fi
  socket="tcp:${host}:${port}"
  if _mobile_agent_probe_tcp "${host}" "${port}" && _mobile_agent_probe_adb_protocol "${socket}"; then
    export ADB_SERVER_SOCKET="${socket}"
    export EIEL_ADB_SERVER_SOCKET_SOURCE="${source_label}"
    export EIEL_ADB_SERVER_SOCKET_STATUS="ok"
    return 0
  fi
  return 1
}

_mobile_agent_validate_preconfigured_adb_socket() {
  local socket="${ADB_SERVER_SOCKET:-}"
  if [[ -z "${socket}" ]]; then
    return 1
  fi
  if _mobile_agent_probe_adb_protocol "${socket}"; then
    export EIEL_ADB_SERVER_SOCKET_SOURCE="${EIEL_ADB_SERVER_SOCKET_SOURCE:-preconfigured}"
    export EIEL_ADB_SERVER_SOCKET_STATUS="ok"
    return 0
  fi
  unset ADB_SERVER_SOCKET
  export EIEL_ADB_SERVER_SOCKET_SOURCE="invalid_preconfigured"
  export EIEL_ADB_SERVER_SOCKET_STATUS="protocol_unhealthy"
  return 1
}

_mobile_agent_configure_adb_socket() {
  if [[ -n "${ADB_SERVER_SOCKET:-}" ]]; then
    _mobile_agent_validate_preconfigured_adb_socket && return 0
  fi

  local port="${EIEL_ADB_SERVER_PORT:-5037}"
  local host=""
  if [[ -n "${EIEL_ADB_SERVER_HOST:-}" ]]; then
    _mobile_agent_try_adb_socket "env_host" "${EIEL_ADB_SERVER_HOST}" "${port}" && return 0
  fi

  if grep -qi microsoft /proc/version 2>/dev/null; then
    _mobile_agent_try_adb_socket "wsl_localhost" "127.0.0.1" "${port}" && return 0
    host="$(ip route show default 2>/dev/null | awk 'NR==1 {print $3}')"
    _mobile_agent_try_adb_socket "wsl_default_gateway" "${host}" "${port}" && return 0
  fi

  export EIEL_ADB_SERVER_SOCKET_STATUS="${EIEL_ADB_SERVER_SOCKET_STATUS:-not_configured}"
}

__mobile_agent_env_main__() {
  set -euo pipefail

  local _mobile_agent_env_script_dir
  local _mobile_agent_repo_root
  local _mobile_agent_android_env_script
  local _mobile_agent_secrets_file
  local _mobile_agent_home_debug_keystore
  local _mobile_agent_repo_debug_keystore
  local _mobile_agent_python_bin_dir
  local _mkdir_targets=()

  _mobile_agent_env_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  _mobile_agent_repo_root="$(cd "${_mobile_agent_env_script_dir}/../.." && pwd)"
  _mobile_agent_android_env_script="${_mobile_agent_repo_root}/infra/mobile/android_env.sh"

  if [[ ! -f "${_mobile_agent_android_env_script}" ]]; then
    echo "missing Android env loader: ${_mobile_agent_android_env_script}" >&2
    return 1
  fi

  # shellcheck disable=SC1090
  source "${_mobile_agent_android_env_script}"

  # Load local secrets for all wrappers that source this env bootstrap.
  # This keeps API keys consistent across server start/stop and agent tooling.
  _mobile_agent_secrets_file="${EIEL_SECRETS_FILE:-${HOME}/.config/causyl/secrets.env}"
  if [[ -f "${_mobile_agent_secrets_file}" ]]; then
    # shellcheck disable=SC1090
    source "${_mobile_agent_secrets_file}"
  fi

  [[ -n "${GRADLE_USER_HOME:-}" ]] && _mkdir_targets+=("${GRADLE_USER_HOME}")
  [[ -n "${ANDROID_USER_HOME:-}" ]] && _mkdir_targets+=("${ANDROID_USER_HOME}")
  [[ -n "${ANDROID_AVD_HOME:-}" ]] && _mkdir_targets+=("${ANDROID_AVD_HOME}")
  if (( ${#_mkdir_targets[@]} > 0 )); then
    mkdir -p "${_mkdir_targets[@]}"
  fi

  _mobile_agent_configure_adb_socket

  # Make the mobile agent stack default to the unrestricted Codex launcher path.
  # This affects future server/Codex processes started through the repo wrappers;
  # it cannot relax sandboxing for an already-running session.
  export EIEL_CODEX_UNRESTRICTED="${EIEL_CODEX_UNRESTRICTED:-0}"
  export EIEL_AGENTS_BACKEND="${EIEL_AGENTS_BACKEND:-codex}"
  export EIEL_CODEX_PROTOCOL="${EIEL_CODEX_PROTOCOL:-oneshot}"

  # Canonical Python runtime for server/scheduler flows.
  # Prefer the repo-managed runtime if present so optional SDK deps (google-genai)
  # are resolved consistently for all wrapper scripts.
  if [[ -n "${EIEL_PYTHON_BIN:-}" ]] && [[ -x "${EIEL_PYTHON_BIN}" ]]; then
    :
  elif [[ -x "${_mobile_agent_repo_root}/.tooling/python-runtime/bin/python" ]]; then
    export EIEL_PYTHON_BIN="${_mobile_agent_repo_root}/.tooling/python-runtime/bin/python"
  elif command -v python >/dev/null 2>&1; then
    export EIEL_PYTHON_BIN="$(command -v python)"
  else
    export EIEL_PYTHON_BIN=""
  fi
  if [[ -n "${EIEL_PYTHON_BIN}" ]]; then
    _mobile_agent_python_bin_dir="$(cd "$(dirname "${EIEL_PYTHON_BIN}")" && pwd)"
    case ":${PATH:-}:" in
      *":${_mobile_agent_python_bin_dir}:"*) ;;
      *) export PATH="${_mobile_agent_python_bin_dir}${PATH:+:${PATH}}" ;;
    esac
  fi

  # Preserve debug-signing continuity for debug APK updates.
  _mobile_agent_home_debug_keystore="${HOME}/.android/debug.keystore"
  _mobile_agent_repo_debug_keystore="${ANDROID_USER_HOME}/debug.keystore"
  if [[ -f "${_mobile_agent_home_debug_keystore}" ]]; then
    if [[ ! -f "${_mobile_agent_repo_debug_keystore}" ]] || ! cmp -s "${_mobile_agent_home_debug_keystore}" "${_mobile_agent_repo_debug_keystore}"; then
      cp "${_mobile_agent_home_debug_keystore}" "${_mobile_agent_repo_debug_keystore}"
    fi
  fi

  export EIEL_REPO_ROOT="${_mobile_agent_repo_root}"
  return 0
}

__mobile_agent_env_rc=0
if [[ "${BASH_SOURCE[0]}" != "${0}" ]]; then
  __mobile_agent_env_main__ || __mobile_agent_env_rc=$?
  if declare -F __mobile_agent_env_restore__ >/dev/null 2>&1; then
    __mobile_agent_env_restore__
  fi
  # Do not unset `__mobile_agent_env_restore__` here: this file is sourced from
  # interactive shells, and leaving a harmless helper function defined is safer
  # than breaking any stray hooks that might call it after environment refresh.
  unset -f __mobile_agent_env_main__ _mobile_agent_probe_tcp _mobile_agent_probe_adb_protocol _mobile_agent_try_adb_socket _mobile_agent_validate_preconfigured_adb_socket _mobile_agent_configure_adb_socket
  return "${__mobile_agent_env_rc}"
fi

__mobile_agent_env_main__ || __mobile_agent_env_rc=$?
if declare -F __mobile_agent_env_restore__ >/dev/null 2>&1; then
  __mobile_agent_env_restore__
fi
unset -f __mobile_agent_env_main__ _mobile_agent_probe_tcp _mobile_agent_probe_adb_protocol _mobile_agent_try_adb_socket _mobile_agent_validate_preconfigured_adb_socket _mobile_agent_configure_adb_socket
exit "${__mobile_agent_env_rc}"
