#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "${script_dir}/mobile_agent_env.sh"

if [[ -z "${EIEL_CODEX_REAL_BIN:-}" ]]; then
  echo "EIEL_CODEX_REAL_BIN is required before starting the mobile Codex slot." >&2
  exit 64
fi

default_flags=()
if [[ "${EIEL_CODEX_UNRESTRICTED:-0}" == "1" ]]; then
  explicit_mode=0
  for arg in "$@"; do
    case "${arg}" in
      --dangerously-bypass-approvals-and-sandbox|--full-auto|--sandbox|-s|--ask-for-approval|-a)
        explicit_mode=1
        break
        ;;
    esac
  done
  if [[ "${explicit_mode}" == "0" ]]; then
    default_flags+=(--dangerously-bypass-approvals-and-sandbox)
  fi
fi

_codex_has_workdir_flag() {
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

_codex_project_root_for() {
  local cwd="$1"
  local root=""
  root="$(git -C "${cwd}" rev-parse --show-toplevel 2>/dev/null || true)"
  if [[ -n "${root}" ]]; then
    printf '%s\n' "${root}"
  else
    (cd "${cwd}" && pwd)
  fi
}

_codex_trust_project() {
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

_codex_trace_launch() {
  if [[ "${EIEL_CODEX_TRACE_LAUNCH:-0}" != "1" ]]; then
    return 0
  fi
  local trace_path="${EIEL_CODEX_TRACE_FILE:-${HOME}/.codex/codex-launch-trace.log}"
  mkdir -p "$(dirname "${trace_path}")"
  {
    printf 'utc=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null || date)"
    printf 'shell_pwd=%s\n' "${PWD}"
    printf 'launch_project_root=%s\n' "${1:-}"
    printf 'slot_wrapper=%s\n' "${script_dir}/persistent_codex_slot.py"
    printf 'real_bin=%s\n' "${EIEL_CODEX_REAL_BIN:-}"
    printf 'argv='
    printf '%q ' "$@"
    printf '\n---\n'
  } >> "${trace_path}"
}

launch_project_root="$(_codex_project_root_for "${PWD}")"
_codex_trust_project "${launch_project_root}"

workdir_flags=()
if ! _codex_has_workdir_flag "$@"; then
  workdir_flags=(-C "${PWD}")
fi

_codex_trace_launch "${launch_project_root}" "${default_flags[@]}" "${workdir_flags[@]}" -c shell_environment_policy.inherit=all "$@"
exec "${script_dir}/persistent_codex_slot.py" "${default_flags[@]}" "${workdir_flags[@]}" -c shell_environment_policy.inherit=all "$@"
