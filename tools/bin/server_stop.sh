#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
runtime_dir="${repo_root}/runtime/state/webui"
pid_file="${runtime_dir}/webui_server.pid"
tunnel_pid_file="${runtime_dir}/cloudflared_tunnel.pid"
source "${script_dir}/mobile_agent_env.sh"
python_bin="${EIEL_PYTHON_BIN:-}"
if [[ -z "${python_bin}" ]]; then
  python_bin="$(command -v python || true)"
fi
if [[ -z "${python_bin}" ]]; then
  echo "[server_stop] python not found on PATH" >&2
  exit 1
fi

hard=0
force=0
yes_flag=0
timeout_s=20
drain_timeout_s=3600
for arg in "$@"; do
  if [[ "${arg}" == "--hard" ]]; then
    hard=1
  elif [[ "${arg}" == "--force" ]]; then
    force=1
  elif [[ "${arg}" == "--yes" ]]; then
    yes_flag=1
  elif [[ "${arg}" =~ ^--timeout=([0-9]+)$ ]]; then
    timeout_s="${BASH_REMATCH[1]}"
  elif [[ "${arg}" =~ ^--drain-timeout=([0-9]+)$ ]]; then
    drain_timeout_s="${BASH_REMATCH[1]}"
  else
    echo "[server_stop] unknown argument: ${arg}" >&2
    exit 2
  fi
done

pause_json="$(
  cd "${repo_root}" &&
  "${python_bin}" - <<'PY'
import json
from infra.controlplane import server_maintenance as maint
print(json.dumps(maint.pause_unfinished_jobs()))
PY
)"
paused_count="$(
  "${python_bin}" - "${pause_json}" <<'PY'
import json,sys
print(int(json.loads(sys.argv[1] or '{}').get('paused_count') or 0))
PY
)"
if [[ "${paused_count}" != "0" ]]; then
  echo "[server_stop] paused ${paused_count} unfinished scheduled jobs"
fi

while true; do
  locks_json="$(
    cd "${repo_root}" &&
    "${python_bin}" - <<'PY'
import json
from infra.controlplane import server_maintenance as maint
print(json.dumps(maint.active_job_locks()))
PY
  )"
  active_count="$(
    "${python_bin}" - "${locks_json}" <<'PY'
import json,sys
data=json.loads(sys.argv[1] or '[]')
print(len(data) if isinstance(data, list) else 0)
PY
  )"
  if [[ "${active_count}" == "0" ]]; then
    break
  fi
  echo "[server_stop] waiting for ${active_count} active scheduled job(s) to drain"
  "${python_bin}" - "${locks_json}" <<'PY'
import json,sys
for row in json.loads(sys.argv[1] or '[]'):
    print(f"[server_stop] active job={row.get('job_id','')} claimed={row.get('claimed_utc','')} heartbeat={row.get('heartbeat_utc','')}")
PY
  if [[ "${force}" == "1" ]]; then
    echo "[server_stop] WARNING: forcing stop with active scheduled jobs can interrupt in-flight work."
    if [[ "${yes_flag}" != "1" ]]; then
      if [[ ! -t 0 ]]; then
        echo "[server_stop] --force requires --yes when stdin is not interactive" >&2
        exit 1
      fi
      read -r -p "[server_stop] Type FORCE to continue killing the server anyway: " reply
      if [[ "${reply}" != "FORCE" ]]; then
        echo "[server_stop] force stop canceled"
        exit 1
      fi
    fi
    break
  fi
  if (( drain_timeout_s <= 0 )); then
    echo "[server_stop] active jobs are still running; re-run with --force to interrupt them" >&2
    exit 1
  fi
  sleep 5
  drain_timeout_s=$((drain_timeout_s - 5))
done

if [[ ! -f "${pid_file}" ]]; then
  echo "[server_stop] pid file not found: ${pid_file}"
else
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -z "${pid}" ]]; then
    echo "[server_stop] pid file empty: ${pid_file}"
    rm -f "${pid_file}"
  elif ! kill -0 "${pid}" 2>/dev/null; then
    echo "[server_stop] pid ${pid} is not running"
    rm -f "${pid_file}"
  else
    kill "${pid}"
    echo "[server_stop] sent SIGTERM to pid=${pid}"

    deadline=$((SECONDS + timeout_s))
    while kill -0 "${pid}" 2>/dev/null; do
      if (( SECONDS >= deadline )); then
        if [[ "${hard}" == "1" ]]; then
          kill -9 "${pid}" 2>/dev/null || true
          echo "[server_stop] sent SIGKILL to pid=${pid}"
          break
        fi
        echo "[server_stop] timeout waiting for pid=${pid} to stop"
        echo "[server_stop] re-run with --hard to force kill"
        exit 1
      fi
      sleep 1
    done

    rm -f "${pid_file}"
    echo "[server_stop] stopped"
  fi
fi

if [[ ! -f "${tunnel_pid_file}" ]]; then
  echo "[server_stop] cloudflared pid file not found: ${tunnel_pid_file}"
  exit 0
fi

tunnel_pid="$(cat "${tunnel_pid_file}" 2>/dev/null || true)"
if [[ -z "${tunnel_pid}" ]]; then
  echo "[server_stop] cloudflared pid file empty: ${tunnel_pid_file}"
  rm -f "${tunnel_pid_file}"
  exit 0
fi

if ! kill -0 "${tunnel_pid}" 2>/dev/null; then
  echo "[server_stop] cloudflared pid ${tunnel_pid} is not running"
  rm -f "${tunnel_pid_file}"
  exit 0
fi

kill "${tunnel_pid}"
echo "[server_stop] sent SIGTERM to cloudflared pid=${tunnel_pid}"

tunnel_deadline=$((SECONDS + 10))
while kill -0 "${tunnel_pid}" 2>/dev/null; do
  if (( SECONDS >= tunnel_deadline )); then
    kill -9 "${tunnel_pid}" 2>/dev/null || true
    echo "[server_stop] sent SIGKILL to cloudflared pid=${tunnel_pid}"
    break
  fi
  sleep 1
done

rm -f "${tunnel_pid_file}"
echo "[server_stop] cloudflared stopped"
