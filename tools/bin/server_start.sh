#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"

source "${script_dir}/mobile_agent_env.sh"

# The plane must not inherit an operator terminal slot as its default CODEX_HOME.
# Scheduled Codex work needs either an isolated worker home or the shared baseline
# home, but never the currently attached interactive slot.
unset CODEX_HOME || true
unset EIEL_TERMINAL_CODEX_SLOT_HOME || true

export EIEL_SCHEDULED_JOBS_AUTOSTART="${EIEL_SCHEDULED_JOBS_AUTOSTART:-1}"
export EIEL_WEBUI_SCAN_DEPTH="${EIEL_WEBUI_SCAN_DEPTH:-12}"

if [[ -z "${EIEL_CODEX_TURN_CMD:-}" ]]; then
  codex_bin="$(command -v codex || true)"
  if [[ -z "${codex_bin}" ]] && [[ -x "/home/user/.nvm/versions/node/v24.14.0/bin/codex" ]]; then
    codex_bin="/home/user/.nvm/versions/node/v24.14.0/bin/codex"
  fi
  if [[ -n "${codex_bin}" ]]; then
    printf -v quoted_repo_root '%q' "${repo_root}"
    if [[ "${EIEL_CODEX_UNRESTRICTED:-0}" == "1" ]]; then
      export EIEL_CODEX_TURN_CMD="${codex_bin} --dangerously-bypass-approvals-and-sandbox exec -C ${quoted_repo_root} --skip-git-repo-check -"
    else
      export EIEL_CODEX_TURN_CMD="${codex_bin} -a never -s workspace-write exec -C ${quoted_repo_root} --skip-git-repo-check -"
    fi
  fi
fi

runtime_dir="${repo_root}/runtime/state/webui"
log_dir="${runtime_dir}/launcher_logs"
pid_file="${runtime_dir}/webui_server.pid"
tunnel_pid_file="${runtime_dir}/cloudflared_tunnel.pid"
tunnel_control_file="${runtime_dir}/cloudflared_control.env"
mkdir -p "${log_dir}"

if [[ -f "${tunnel_control_file}" ]]; then
  # Local operator override for cloudflared autostart persistence.
  # shellcheck disable=SC1090
  source "${tunnel_control_file}"
fi

resume_paused_jobs() {
  if [[ "${resume_jobs}" != "1" ]]; then
    return 0
  fi
  local resume_json=""
  local resumed_count=""
  resume_json="$(
    cd "${repo_root}" &&
    python - <<'PY'
import json
from infra.controlplane import server_maintenance as maint
print(json.dumps(maint.resume_paused_jobs()))
PY
  )"
  resumed_count="$(
    python - "${resume_json}" <<'PY'
import json,sys
print(int(json.loads(sys.argv[1] or '{}').get('resumed_count') or 0))
PY
  )"
  if [[ "${resumed_count}" != "0" ]]; then
    echo "[server_start] resumed ${resumed_count} previously paused scheduled jobs"
  fi
}

cloudflared_autostart="${EIEL_CLOUDFLARED_AUTOSTART:-1}"
cloudflared_tunnel_name="${EIEL_CLOUDFLARED_TUNNEL_NAME:-lab-assistant}"
cloudflared_bin="${EIEL_CLOUDFLARED_BIN:-}"
cloudflared_pid=""
cloudflared_log_path=""

ensure_cloudflared_bin() {
  if [[ "${cloudflared_autostart}" != "1" ]]; then
    return 0
  fi
  if [[ -n "${cloudflared_bin}" ]] && [[ -x "${cloudflared_bin}" ]]; then
    return 0
  fi
  cloudflared_bin="$(command -v cloudflared || true)"
  if [[ -n "${cloudflared_bin}" ]] && [[ -x "${cloudflared_bin}" ]]; then
    return 0
  fi
  echo "[server_start] cloudflared autostart enabled but binary not found" >&2
  echo "[server_start] set EIEL_CLOUDFLARED_AUTOSTART=0 to disable tunnel startup" >&2
  exit 1
}

cleanup_stale_tunnel_pid() {
  if [[ ! -f "${tunnel_pid_file}" ]]; then
    return 0
  fi
  local existing_tunnel_pid=""
  existing_tunnel_pid="$(cat "${tunnel_pid_file}" 2>/dev/null || true)"
  if [[ -n "${existing_tunnel_pid}" ]] && kill -0 "${existing_tunnel_pid}" 2>/dev/null; then
    if [[ "${replace_running}" == "1" ]]; then
      kill "${existing_tunnel_pid}" 2>/dev/null || true
      local deadline=$((SECONDS + 10))
      while kill -0 "${existing_tunnel_pid}" 2>/dev/null; do
        if (( SECONDS >= deadline )); then
          kill -9 "${existing_tunnel_pid}" 2>/dev/null || true
          break
        fi
        sleep 1
      done
    else
      echo "[server_start] cloudflared already running pid=${existing_tunnel_pid}"
      echo "[server_start] tunnel_pid_file=${tunnel_pid_file}"
      return 0
    fi
  fi
  rm -f "${tunnel_pid_file}"
}

start_cloudflared() {
  if [[ "${cloudflared_autostart}" != "1" ]]; then
    return 0
  fi
  ensure_cloudflared_bin
  cleanup_stale_tunnel_pid
  if [[ -f "${tunnel_pid_file}" ]]; then
    cloudflared_pid="$(cat "${tunnel_pid_file}" 2>/dev/null || true)"
    if [[ -n "${cloudflared_pid}" ]] && kill -0 "${cloudflared_pid}" 2>/dev/null; then
      return 0
    fi
    rm -f "${tunnel_pid_file}"
  fi

  local stamp=""
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  cloudflared_log_path="${log_dir}/cloudflared_tunnel__${stamp}.log"
  if command -v setsid >/dev/null 2>&1; then
    setsid "${cloudflared_bin}" tunnel run "${cloudflared_tunnel_name}" >> "${cloudflared_log_path}" 2>&1 < /dev/null &
  else
    nohup "${cloudflared_bin}" tunnel run "${cloudflared_tunnel_name}" >> "${cloudflared_log_path}" 2>&1 < /dev/null &
  fi
  cloudflared_pid="$!"
  echo "${cloudflared_pid}" > "${tunnel_pid_file}"
  echo "[server_start] cloudflared started pid=${cloudflared_pid}"
  echo "[server_start] cloudflared_tunnel=${cloudflared_tunnel_name}"
  echo "[server_start] cloudflared_log_path=${cloudflared_log_path}"
}

stop_cloudflared() {
  local pid="${cloudflared_pid:-}"
  if [[ -z "${pid}" ]] && [[ -f "${tunnel_pid_file}" ]]; then
    pid="$(cat "${tunnel_pid_file}" 2>/dev/null || true)"
  fi
  if [[ -z "${pid}" ]]; then
    rm -f "${tunnel_pid_file}"
    return 0
  fi
  if ! kill -0 "${pid}" 2>/dev/null; then
    rm -f "${tunnel_pid_file}"
    return 0
  fi
  kill "${pid}" 2>/dev/null || true
  local deadline=$((SECONDS + 10))
  while kill -0 "${pid}" 2>/dev/null; do
    if (( SECONDS >= deadline )); then
      kill -9 "${pid}" 2>/dev/null || true
      break
    fi
    sleep 1
  done
  rm -f "${tunnel_pid_file}"
}

foreground=0
replace_running=0
resume_jobs=1
wait_ready=1
ready_timeout_s=60
server_args=()
for arg in "$@"; do
  if [[ "${arg}" == "--foreground" ]]; then
    foreground=1
  elif [[ "${arg}" == "--replace-running" ]]; then
    replace_running=1
  elif [[ "${arg}" == "--no-resume-jobs" ]]; then
    resume_jobs=0
  elif [[ "${arg}" == "--no-wait-ready" ]]; then
    wait_ready=0
  elif [[ "${arg}" =~ ^--ready-timeout=([0-9]+)$ ]]; then
    ready_timeout_s="${BASH_REMATCH[1]}"
  else
    server_args+=("${arg}")
  fi
done

python_bin="${EIEL_PYTHON_BIN:-}"
if [[ -z "${python_bin}" ]]; then
  python_bin="$(command -v python || true)"
fi
if [[ -z "${python_bin}" ]]; then
  echo "[server_start] python not found on PATH" >&2
  exit 1
fi

python_has_yaml() {
  local bin="$1"
  [[ -n "${bin}" ]] || return 1
  [[ -x "${bin}" ]] || return 1
  "${bin}" - <<'PY' >/dev/null 2>&1
import yaml  # noqa: F401
PY
}

if ! python_has_yaml "${python_bin}"; then
  fallback_python="$(command -v python || true)"
  if [[ -n "${fallback_python}" ]] && python_has_yaml "${fallback_python}"; then
    echo "[server_start] overriding python runtime because ${python_bin} lacks PyYAML"
    python_bin="${fallback_python}"
  else
    echo "[server_start] selected python lacks PyYAML: ${python_bin}" >&2
    exit 1
  fi
fi

server_cmd=("${python_bin}" -m infra.webui.server)
ready_url="http://127.0.0.1:8787/api/health"
scheduled_health_url="http://127.0.0.1:8787/api/schedules/health"
has_arg() {
  local needle="$1"
  local item=""
  for item in "${server_args[@]}"; do
    if [[ "${item}" == "${needle}" ]]; then
      return 0
    fi
  done
  return 1
}
if ! has_arg "--runs_root"; then
  server_cmd+=(--runs_root ./runtime/live)
fi
if ! has_arg "--scan_depth"; then
  server_cmd+=(--scan_depth "${EIEL_WEBUI_SCAN_DEPTH}")
fi

if ! has_arg "--host"; then
  server_cmd+=(--host 127.0.0.1)
fi
if ! has_arg "--port"; then
  server_cmd+=(--port 8787)
fi
server_cmd+=("${server_args[@]}")

wait_for_ready() {
  local timeout_s="$1"
  local deadline=$((SECONDS + timeout_s))
  while true; do
    if ! kill -0 "${server_pid}" 2>/dev/null; then
      echo "[server_start] server process exited before readiness"
      if [[ -f "${log_path}" ]]; then
        echo "[server_start] recent log tail:"
        tail -n 40 "${log_path}" || true
      fi
      exit 1
    fi
    if "${python_bin}" - "${ready_url}" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

url = str(sys.argv[1] or "").strip()
with urllib.request.urlopen(url, timeout=3) as resp:
    raise SystemExit(0 if 200 <= int(resp.status) < 500 else 1)
PY
    then
      echo "[server_start] ready url=${ready_url}"
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "[server_start] readiness timeout after ${timeout_s}s url=${ready_url}"
      return 1
    fi
    sleep 1
  done
}

wait_for_scheduler_helpers() {
  local timeout_s="$1"
  local deadline=$((SECONDS + timeout_s))
  while true; do
    if ! kill -0 "${server_pid}" 2>/dev/null; then
      echo "[server_start] server process exited before scheduler helpers were ready"
      if [[ -f "${log_path}" ]]; then
        echo "[server_start] recent log tail:"
        tail -n 40 "${log_path}" || true
      fi
      exit 1
    fi
    if "${python_bin}" - "${scheduled_health_url}" <<'PY' >/dev/null 2>&1
import json
import sys
import urllib.request

url = str(sys.argv[1] or "").strip()
with urllib.request.urlopen(url, timeout=3) as resp:
    payload = json.load(resp)

if not isinstance(payload, dict):
    raise SystemExit(1)

for key in ("scheduler", "watchdog"):
    row = payload.get(key)
    if not isinstance(row, dict) or not bool(row.get("running")):
        raise SystemExit(1)

healer = payload.get("healer")
if isinstance(healer, dict) and bool(healer.get("running")):
    raise SystemExit(0)

healer_health = payload.get("healer_health")
if (
    isinstance(healer_health, dict)
    and str(healer_health.get("health") or "").strip().lower() == "disabled"
    and str(healer_health.get("reason") or "").strip() == "scheduler_owned_reconciliation"
):
    raise SystemExit(0)

raise SystemExit(0)
PY
    then
      echo "[server_start] scheduler helpers ready url=${scheduled_health_url}"
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "[server_start] scheduler helper readiness timeout after ${timeout_s}s url=${scheduled_health_url}"
      return 1
    fi
    sleep 1
  done
}

if [[ -f "${pid_file}" ]]; then
  existing_pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ "${replace_running}" != "1" ]] && [[ -n "${existing_pid}" ]] && kill -0 "${existing_pid}" 2>/dev/null; then
    echo "[server_start] already running pid=${existing_pid}"
    echo "[server_start] pid_file=${pid_file}"
    exit 0
  fi
  rm -f "${pid_file}"
fi

cd "${repo_root}"
if [[ "${foreground}" == "1" ]]; then
  stamp="$(date -u +%Y%m%dT%H%M%SZ)"
  log_path="${log_dir}/webui_server__${stamp}.log"
  ln -sfn "$(basename "${log_path}")" "${log_dir}/latest.log"
  if command -v tee >/dev/null 2>&1; then
    exec > >(tee -a "${log_path}") 2>&1
  fi
  echo "[server_start] mode=foreground"
  echo "[server_start] python=${python_bin}"
  echo "[server_start] log_path=${log_path}"
  echo "[server_start] EIEL_SCHEDULED_JOBS_AUTOSTART=${EIEL_SCHEDULED_JOBS_AUTOSTART}"
  echo "[server_start] EIEL_CODEX_MAX_WORKERS=${EIEL_CODEX_MAX_WORKERS:-runtime_policy}"
  echo "[server_start] EIEL_CODEX_MAX_WORKERS_PER_ROLE=${EIEL_CODEX_MAX_WORKERS_PER_ROLE:-runtime_policy}"
  echo "[server_start] EIEL_WEBUI_SCAN_DEPTH=${EIEL_WEBUI_SCAN_DEPTH}"
  echo "[server_start] EIEL_CLOUDFLARED_AUTOSTART=${cloudflared_autostart}"
  resume_paused_jobs
  start_cloudflared
  trap 'stop_cloudflared' EXIT
  "${server_cmd[@]}"
  exit_code=$?
  stop_cloudflared
  exit "${exit_code}"
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
log_path="${log_dir}/webui_server__${stamp}.log"
ln -sfn "$(basename "${log_path}")" "${log_dir}/latest.log"

echo "[server_start] mode=detached"
echo "[server_start] python=${python_bin}"
echo "[server_start] log_path=${log_path}"
echo "[server_start] EIEL_SCHEDULED_JOBS_AUTOSTART=${EIEL_SCHEDULED_JOBS_AUTOSTART}"
echo "[server_start] EIEL_CODEX_MAX_WORKERS=${EIEL_CODEX_MAX_WORKERS:-runtime_policy}"
echo "[server_start] EIEL_CODEX_MAX_WORKERS_PER_ROLE=${EIEL_CODEX_MAX_WORKERS_PER_ROLE:-runtime_policy}"
echo "[server_start] EIEL_WEBUI_SCAN_DEPTH=${EIEL_WEBUI_SCAN_DEPTH}"
echo "[server_start] EIEL_CLOUDFLARED_AUTOSTART=${cloudflared_autostart}"
start_cloudflared

if command -v setsid >/dev/null 2>&1; then
  setsid "${server_cmd[@]}" >> "${log_path}" 2>&1 < /dev/null &
else
  nohup "${server_cmd[@]}" >> "${log_path}" 2>&1 < /dev/null &
fi
server_pid="$!"
echo "${server_pid}" > "${pid_file}"
echo "[server_start] started pid=${server_pid}"
echo "[server_start] pid_file=${pid_file}"
resume_paused_jobs
if [[ "${wait_ready}" == "1" ]]; then
  if ! wait_for_ready "${ready_timeout_s}"; then
    stop_cloudflared
    exit 1
  fi
  if ! wait_for_scheduler_helpers "${ready_timeout_s}"; then
    stop_cloudflared
    exit 1
  fi
fi
