#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
base_url="http://localhost:8787"
json_stdout=0
for arg in "$@"; do
  if [[ "${arg}" == "--json" ]]; then
    json_stdout=1
  elif [[ "${arg}" == --* ]]; then
    echo "[scheduler_health_check] unknown argument: ${arg}" >&2
    exit 2
  else
    base_url="${arg}"
  fi
done
tmp_out="$(mktemp)"
trap 'rm -f "${tmp_out}"' EXIT

cd "${repo_root}"

server_state="$("${script_dir}/server_status.sh" || true)"
echo "${server_state}"

BASE_URL="${base_url}" python - <<'PY' > "${tmp_out}"
import json
import os
import urllib.request
import urllib.error
from infra.controlplane import server_maintenance as maint

BASE_URL = str(os.environ.get("BASE_URL") or "http://localhost:8787").strip()

def get_json(url: str, *, method: str = "GET", data: dict | None = None, timeout: int = 20):
    payload = None
    headers = {"Accept": "application/json"}
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))

out = {
    "ok": True,
    "checks": {},
    "warnings": [],
    "errors": [],
}

try:
    jobs = get_json(f"{BASE_URL}/api/schedules/jobs")
    scheduler = jobs.get("scheduler") if isinstance(jobs, dict) else {}
    scheduler_v2 = jobs.get("scheduler_v2") if isinstance(jobs, dict) and isinstance(jobs.get("scheduler_v2"), dict) else {}
    v2_ownership = scheduler_v2.get("ownership") if isinstance(scheduler_v2.get("ownership"), dict) else {}
    v2_projection = (
        ((scheduler_v2.get("shadow_payload") or {}).get("evaluation") or {}).get("projection")
        if isinstance(scheduler_v2.get("shadow_payload"), dict)
        else {}
    )
    v2_progress = v2_projection.get("family_progress") if isinstance(v2_projection, dict) and isinstance(v2_projection.get("family_progress"), dict) else {}
    rows = jobs.get("jobs") if isinstance(jobs, dict) else []
    rows = rows if isinstance(rows, list) else []
    active = []
    blocked_enabled = []
    error_enabled = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        jid = str(row.get("job_id") or "")
        if str(state.get("active_run_id") or "").strip():
            active.append(jid)
        if bool(row.get("enabled")) and str(state.get("last_status") or "").lower() == "blocked":
            blocked_enabled.append({"job_id": jid, "due_reason": str(due.get("reason") or "")})
        if bool(row.get("enabled")) and str(state.get("last_status") or "").lower() == "error":
            error_enabled.append(jid)
    out["checks"]["scheduler"] = {
        "backend": "v2" if scheduler_v2 else "legacy",
        "v2_status": str(scheduler_v2.get("status") or ""),
        "v2_mode": str(v2_ownership.get("mode") or ""),
        "v2_owner": str(v2_ownership.get("canonical_owner") or ""),
        "v2_parent_uid": str(scheduler_v2.get("parent_uid") or ""),
        "v2_projection_equivalent": bool(scheduler_v2.get("projection_equivalent")),
        "v2_family_status": str(v2_projection.get("family_status") or "") if isinstance(v2_projection, dict) else "",
        "v2_family_completed": int(v2_progress.get("completed_children") or 0),
        "v2_family_total": int(v2_progress.get("total_children") or 0),
        "running": bool((scheduler or {}).get("running")),
        "interval_s": int((scheduler or {}).get("interval_s") or 0),
        "last_tick_utc": str((scheduler or {}).get("last_tick_utc") or ""),
        "last_error": str((scheduler or {}).get("last_error") or ""),
        "jobs_total": len(rows),
        "active_jobs": active,
        "blocked_enabled_jobs": blocked_enabled,
        "error_enabled_jobs": error_enabled,
    }
    v2_ready = bool(scheduler_v2) and str(scheduler_v2.get("status") or "") in {"ready", "pending_decomposition"} and str(v2_ownership.get("mode") or "") == "v2_primary"
    if not v2_ready and not bool((scheduler or {}).get("running")):
        out["ok"] = False
        out["errors"].append("scheduler_not_running_or_v2_not_ready")
    if scheduler_v2 and not v2_ready:
        out["warnings"].append("scheduler_v2_not_ready_primary")
    if str((scheduler or {}).get("last_error") or "").strip():
        out["warnings"].append("scheduler_last_error_nonempty")
except urllib.error.URLError:
    out["ok"] = False
    out["errors"].append("schedules_api_unreachable")
except Exception as exc:
    out["ok"] = False
    out["errors"].append(f"schedules_api_error:{exc}")

try:
    runtime = get_json(f"{BASE_URL}/api/admin/server/refresh_runtime", method="POST", data={})
    agents = runtime.get("agents") if isinstance(runtime.get("agents"), dict) else {}
    codex = agents.get("codex") if isinstance(agents.get("codex"), dict) else {}
    cmd = codex.get("default_cmd") if isinstance(codex.get("default_cmd"), list) else []
    out["checks"]["runtime"] = {
        "agents_backend": str(agents.get("backend") or ""),
        "codex_ok": bool(codex.get("ok")),
        "codex_changed": bool(codex.get("changed")),
        "codex_default_cmd_head": str(cmd[0]) if cmd else "",
    }
    if out["checks"]["runtime"]["agents_backend"] != "codex":
        out["warnings"].append("agents_backend_not_codex")
    if not bool(codex.get("ok")):
        out["ok"] = False
        out["errors"].append("codex_runtime_not_ok")
    if not cmd:
        out["ok"] = False
        out["errors"].append("codex_default_cmd_missing")
except urllib.error.URLError:
    out["ok"] = False
    out["errors"].append("refresh_runtime_api_unreachable")
except Exception as exc:
    out["ok"] = False
    out["errors"].append(f"refresh_runtime_api_error:{exc}")

try:
    locks = maint.active_job_locks()
    out["checks"]["locks"] = {
        "active_lock_count": len(locks),
        "active_locks": locks,
    }
except Exception as exc:
    out["warnings"].append(f"active_lock_check_failed:{exc}")

print(json.dumps(out, ensure_ascii=False, indent=2))
PY

if [[ "${json_stdout}" == "1" ]]; then
  cat "${tmp_out}"
else
  python - "${tmp_out}" <<'PY'
import json,sys
obj=json.loads(open(sys.argv[1],encoding='utf-8').read() or "{}")
checks=obj.get("checks") if isinstance(obj.get("checks"), dict) else {}
scheduler=checks.get("scheduler") if isinstance(checks.get("scheduler"), dict) else {}
runtime=checks.get("runtime") if isinstance(checks.get("runtime"), dict) else {}
locks=checks.get("locks") if isinstance(checks.get("locks"), dict) else {}
status="OK" if bool(obj.get("ok")) else "FAIL"
print(f"[scheduler_health_check] status={status}")
print(
    "[scheduler_health_check] "
    f"scheduler_running={str(bool(scheduler.get('running'))).lower()} "
    f"backend={str(scheduler.get('backend') or 'legacy')} "
    f"v2_status={str(scheduler.get('v2_status') or '-')} "
    f"v2_mode={str(scheduler.get('v2_mode') or '-')} "
    f"v2_parent={str(scheduler.get('v2_parent_uid') or '-')} "
    f"jobs_total={int(scheduler.get('jobs_total') or 0)} "
    f"active_jobs={len(list(scheduler.get('active_jobs') or []))} "
    f"blocked_enabled_jobs={len(list(scheduler.get('blocked_enabled_jobs') or []))} "
    f"error_enabled_jobs={len(list(scheduler.get('error_enabled_jobs') or []))}"
)
print(
    "[scheduler_health_check] "
    f"codex_runtime_ok={str(bool(runtime.get('codex_ok'))).lower()} "
    f"agents_backend={str(runtime.get('agents_backend') or '-')}"
)
print(
    "[scheduler_health_check] "
    f"active_lock_count={int(locks.get('active_lock_count') or 0)}"
)
warnings=list(obj.get("warnings") or [])
errors=list(obj.get("errors") or [])
if warnings:
    print("[scheduler_health_check] warnings=" + ", ".join(str(x) for x in warnings))
if errors:
    print("[scheduler_health_check] errors=" + ", ".join(str(x) for x in errors))
PY
fi
ok_flag="$(python - "${tmp_out}" <<'PY'
import json,sys
obj=json.loads(open(sys.argv[1],encoding='utf-8').read() or "{}")
print("1" if bool(obj.get("ok")) else "0")
PY
)"

if [[ "${ok_flag}" == "1" ]]; then
  exit 0
fi

exit 1
