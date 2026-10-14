#!/usr/bin/env bash
# shellcheck shell=bash

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo "source this file instead of executing it: source infra/bin/activate_mobile_agent_shell.sh" >&2
  exit 1
fi

_eiel_activate_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_eiel_activate_repo_root="$(cd "${_eiel_activate_script_dir}/../.." && pwd)"

if [[ -z "${EIEL_CODEX_REAL_BIN:-}" ]]; then
  _eiel_codex_real_bin="$(type -P codex || true)"
  if [[ -z "${_eiel_codex_real_bin}" ]]; then
    echo "codex binary not found on PATH before activation" >&2
    return 1
  fi
  export EIEL_CODEX_REAL_BIN="${_eiel_codex_real_bin}"
fi

if [[ -z "${EIEL_CODEX_SLOT_WRAPPER:-}" ]]; then
  export EIEL_CODEX_SLOT_WRAPPER="${_eiel_activate_repo_root}/infra/bin/persistent_codex_slot.py"
fi

_eiel_refresh_mobile_agent_env() {
  # Refresh tooling env on each invocation so cwd changes do not matter.
  source "${_eiel_activate_repo_root}/infra/bin/mobile_agent_env.sh"
}

_eiel_codex_default_flags() {
  if [[ "${EIEL_CODEX_UNRESTRICTED:-0}" != "1" ]]; then
    return 0
  fi
  local arg=""
  for arg in "$@"; do
    case "${arg}" in
      --dangerously-bypass-approvals-and-sandbox|--full-auto|--sandbox|-s|--ask-for-approval|-a)
        return 0
        ;;
    esac
  done
  printf '%s\n' "--dangerously-bypass-approvals-and-sandbox"
}

_eiel_codex_has_workdir_flag() {
  local arg=""
  for arg in "$@"; do
    case "${arg}" in
      -C|--cd|--cwd|--workdir|--working-directory)
        return 0
        ;;
      -C=*|--cd=*|--cwd=*|--workdir=*|--working-directory=*)
        return 0
        ;;
    esac
  done
  return 1
}

_eiel_codex_project_root_for() {
  local cwd="$1"
  local root=""
  root="$(git -C "${cwd}" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "${root}" ]]; then
    printf '%s\n' "${root}"
  else
    (cd "${cwd}" && pwd)
  fi
}

_eiel_codex_trust_project() {
  local project_root="$1"
  local config_path="${CODEX_CONFIG_FILE:-${EIEL_CODEX_SHARED_HOME:-${HOME}/.codex}/config.toml}"
  if [[ "${EIEL_CODEX_AUTO_TRUST_PROJECTS:-1}" != "1" ]]; then
    return 0
  fi
  [[ -n "${project_root}" ]] || return 0
  "${EIEL_PYTHON_BIN:-python3}" - "${config_path}" "${project_root}" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

config_path = Path(sys.argv[1]).expanduser()
project_root = str(Path(sys.argv[2]).expanduser().resolve())
toml_project_root = project_root.replace("\\", "\\\\").replace('"', '\\"')
header = f'[projects."{toml_project_root}"]'
config_path.parent.mkdir(parents=True, exist_ok=True)
text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
if header in text:
    raise SystemExit(0)
suffix = "" if not text or text.endswith("\n") else "\n"
with config_path.open("a", encoding="utf-8", newline="\n") as handle:
    handle.write(suffix)
    if text and not text.endswith("\n\n"):
        handle.write("\n")
    handle.write(f'{header}\ntrust_level = "trusted"\n')
PY
}

_eiel_codex_trace_launch() {
  if [[ "${EIEL_CODEX_TRACE_LAUNCH:-0}" != "1" ]]; then
    return 0
  fi
  local trace_path="${EIEL_CODEX_TRACE_FILE:-${HOME}/.codex/codex-launch-trace.log}"
  mkdir -p "$(dirname "${trace_path}")"
  {
    printf 'utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
    printf 'shell_pwd=%s\n' "${PWD}"
    printf 'launch_cwd=%s\n' "${1:-}"
    printf 'launch_project_root=%s\n' "${2:-}"
    printf 'slot_wrapper=%s\n' "${EIEL_CODEX_SLOT_WRAPPER:-}"
    printf 'real_bin=%s\n' "${EIEL_CODEX_REAL_BIN:-}"
    printf 'argv='
    printf '%q ' "$@"
    printf '\n---\n'
  } >> "${trace_path}"
}

codex() {
  local launch_cwd="${PWD}"
  local launch_project_root=""
  launch_project_root="$(_eiel_codex_project_root_for "${launch_cwd}")"
  _eiel_codex_trust_project "${launch_project_root}" || return 1
  _eiel_refresh_mobile_agent_env || return 1
  local default_flags=()
  local workdir_flags=()
  local flag=""
  while IFS= read -r flag; do
    [[ -n "${flag}" ]] && default_flags+=("${flag}")
  done < <(_eiel_codex_default_flags "$@")
  if ! _eiel_codex_has_workdir_flag "$@"; then
    workdir_flags=(-C "${launch_cwd}")
  fi
  _eiel_codex_trace_launch "${launch_cwd}" "${launch_project_root}" "${default_flags[@]}" "${workdir_flags[@]}" -c shell_environment_policy.inherit=all "$@"
  command "${EIEL_CODEX_SLOT_WRAPPER}" "${default_flags[@]}" "${workdir_flags[@]}" -c shell_environment_policy.inherit=all "$@"
}

export EIEL_REPO_ROOT="${_eiel_activate_repo_root}"
_eiel_refresh_mobile_agent_env || return 1
if [[ "${EIEL_ACTIVATE_QUIET:-0}" != "1" ]]; then
  echo "mobile agent shell active: codex now uses persistent slot-backed storage from ${EIEL_REPO_ROOT}" >&2
  if [[ "${EIEL_CODEX_UNRESTRICTED:-0}" == "1" ]]; then
    echo "mobile agent shell active: unrestricted Codex execution is enabled by default" >&2
  fi
fi
