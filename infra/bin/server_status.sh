#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
runtime_dir="${repo_root}/runtime/state/webui"
pid_file="${runtime_dir}/webui_server.pid"
tunnel_pid_file="${runtime_dir}/cloudflared_tunnel.pid"
tunnel_control_file="${runtime_dir}/cloudflared_control.env"
assistant_control_file="${runtime_dir}/assistant_bridge_control.env"
web_control_file="${runtime_dir}/web_surface_control.env"
latest_log="${runtime_dir}/launcher_logs/latest.log"
python_bin="${EIEL_PYTHON_BIN:-}"
if [[ -z "${python_bin}" ]]; then
  python_bin="$(command -v python || true)"
fi
ready_url="${EIEL_SERVER_READY_URL:-http://127.0.0.1:8787/api/health}"

if [[ -f "${tunnel_control_file}" ]]; then
  # shellcheck disable=SC1090
  source "${tunnel_control_file}"
fi
cloudflared_autostart="${EIEL_CLOUDFLARED_AUTOSTART:-1}"
assistant_bridge_enabled=1
web_surface_enabled=1

if [[ -f "${assistant_control_file}" ]]; then
  # shellcheck disable=SC1090
  source "${assistant_control_file}"
  assistant_bridge_enabled="${EIEL_ASSISTANT_BRIDGE_ENABLED:-1}"
fi
if [[ -f "${web_control_file}" ]]; then
  # shellcheck disable=SC1090
  source "${web_control_file}"
  web_surface_enabled="${EIEL_WEB_SURFACE_ENABLED:-1}"
fi

http_ready=0
if [[ -n "${python_bin}" ]]; then
  if "${python_bin}" - "${ready_url}" <<'PY' >/dev/null 2>&1
import sys
import urllib.request

url = str(sys.argv[1] or "").strip()
with urllib.request.urlopen(url, timeout=3) as resp:
    raise SystemExit(0 if 200 <= int(resp.status) < 500 else 1)
PY
  then
    http_ready=1
  fi
fi

if [[ ! -f "${pid_file}" ]]; then
  if [[ "${http_ready}" == "1" ]]; then
    echo "[server_status] ready pid=unknown"
  else
    echo "[server_status] stopped"
  fi
  echo "[server_status] pid_file=${pid_file}"
  if [[ -n "${python_bin}" ]]; then
    echo "[server_status] ready_url=${ready_url}"
    echo "[server_status] http_ready=${http_ready}"
  fi
  if [[ -e "${latest_log}" ]]; then
    echo "[server_status] latest_log=${latest_log}"
  fi
  echo "[server_status] assistant_bridge_enabled=${assistant_bridge_enabled}"
  echo "[server_status] assistant_bridge_control_file=${assistant_control_file}"
  echo "[server_status] web_surface_enabled=${web_surface_enabled}"
  echo "[server_status] web_surface_control_file=${web_control_file}"
else
  pid="$(cat "${pid_file}" 2>/dev/null || true)"
  if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
    if [[ "${http_ready}" == "1" ]]; then
      echo "[server_status] ready pid=${pid}"
    else
      echo "[server_status] starting pid=${pid}"
    fi
  else
    if [[ "${http_ready}" == "1" ]]; then
      echo "[server_status] ready pid=${pid:-unknown}"
    else
      echo "[server_status] stale pid_file pid=${pid:-unknown}"
    fi
  fi
  echo "[server_status] pid_file=${pid_file}"
  if [[ -n "${python_bin}" ]]; then
    echo "[server_status] ready_url=${ready_url}"
    echo "[server_status] http_ready=${http_ready}"
  fi
  if [[ -e "${latest_log}" ]]; then
    echo "[server_status] latest_log=${latest_log}"
  fi
  echo "[server_status] assistant_bridge_enabled=${assistant_bridge_enabled}"
  echo "[server_status] assistant_bridge_control_file=${assistant_control_file}"
  echo "[server_status] web_surface_enabled=${web_surface_enabled}"
  echo "[server_status] web_surface_control_file=${web_control_file}"
fi

if [[ ! -f "${tunnel_pid_file}" ]]; then
  echo "[server_status] cloudflared=stopped"
  echo "[server_status] cloudflared_autostart=${cloudflared_autostart}"
  echo "[server_status] cloudflared_control_file=${tunnel_control_file}"
  echo "[server_status] cloudflared_pid_file=${tunnel_pid_file}"
  exit 0
fi

tunnel_pid="$(cat "${tunnel_pid_file}" 2>/dev/null || true)"
if [[ -n "${tunnel_pid}" ]] && kill -0 "${tunnel_pid}" 2>/dev/null; then
  echo "[server_status] cloudflared=running pid=${tunnel_pid}"
else
  echo "[server_status] cloudflared=stale pid=${tunnel_pid:-unknown}"
fi
echo "[server_status] cloudflared_autostart=${cloudflared_autostart}"
echo "[server_status] cloudflared_control_file=${tunnel_control_file}"
echo "[server_status] cloudflared_pid_file=${tunnel_pid_file}"
