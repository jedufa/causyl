#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
timeout_s=1800
poll_s=5
backend=""

for arg in "$@"; do
  if [[ "${arg}" =~ ^--timeout=([0-9]+)$ ]]; then
    timeout_s="${BASH_REMATCH[1]}"
  elif [[ "${arg}" =~ ^--poll=([0-9]+)$ ]]; then
    poll_s="${BASH_REMATCH[1]}"
  elif [[ "${arg}" =~ ^--backend=(v2|legacy)$ ]]; then
    backend="${BASH_REMATCH[1]}"
  else
    echo "[pause_scheduler_pipeline] unknown argument: ${arg}" >&2
    exit 2
  fi
done

echo "[pause_scheduler_pipeline] capturing currently-enabled jobs and disabling all scheduled jobs"
pause_json="$(
  cd "${repo_root}" &&
  EIEL_CLI_BACKEND="${backend}" python - <<'PY'
import json
import datetime as dt
from pathlib import Path
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend

sched_backend.require_legacy_for_mutation("pause scheduler pipeline", backend=__import__("os").environ.get("EIEL_CLI_BACKEND") or None)

root = sched._repo_root()
jobs_dir = sched.jobs_dir(root)
enabled_before = []
for spec_path in sorted(jobs_dir.glob("*.json")):
    spec = sched._read_json(spec_path)
    if not spec:
        continue
    job_id = str(spec.get("job_id") or spec_path.stem)
    if bool(spec.get("enabled", True)):
        enabled_before.append(job_id)
        sched.set_job_enabled(job_id, False, repo_root=root)

manifest_path = (root / "runtime" / "state" / "webui" / "scheduler_pipeline_pause_state.json").resolve()
manifest_path.parent.mkdir(parents=True, exist_ok=True)
manifest = {
    "schema_version": "scheduler_pipeline_pause_state_v1",
    "paused_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00","Z"),
    "enabled_job_ids_before_pause": enabled_before,
}
manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({"disabled_count": len(enabled_before), "manifest_path": str(manifest_path)}))
PY
)"
python - "${pause_json}" <<'PY'
import json,sys
obj=json.loads(sys.argv[1] or "{}")
print(f"[pause_scheduler_pipeline] disabled_count={int(obj.get('disabled_count') or 0)}")
print(f"[pause_scheduler_pipeline] manifest_path={str(obj.get('manifest_path') or '')}")
PY

deadline=$((SECONDS + timeout_s))
while true; do
  locks_json="$(
    cd "${repo_root}" &&
    python - <<'PY'
import json
from infra.controlplane import server_maintenance as maint
print(json.dumps(maint.active_job_locks()))
PY
  )"
  active_count="$(
    python - "${locks_json}" <<'PY'
import json,sys
rows=json.loads(sys.argv[1] or "[]")
print(len(rows) if isinstance(rows,list) else 0)
PY
  )"
  if [[ "${active_count}" == "0" ]]; then
    echo "[pause_scheduler_pipeline] active_runs=0 (drained)"
    break
  fi
  echo "[pause_scheduler_pipeline] waiting for active_runs=${active_count}"
  python - "${locks_json}" <<'PY'
import json,sys
for row in json.loads(sys.argv[1] or "[]"):
    print(f"[pause_scheduler_pipeline] active job={row.get('job_id','')} claimed={row.get('claimed_utc','')} heartbeat={row.get('heartbeat_utc','')}")
PY
  if (( timeout_s >= 0 && SECONDS >= deadline )); then
    echo "[pause_scheduler_pipeline] timeout while waiting for active runs to drain" >&2
    exit 1
  fi
  sleep "${poll_s}"
done

echo "[pause_scheduler_pipeline] done"
