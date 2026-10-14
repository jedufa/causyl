#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "${script_dir}/../.." && pwd)"
keep_manifest=0
backend=""

for arg in "$@"; do
  if [[ "${arg}" == "--keep-manifest" ]]; then
    keep_manifest=1
  elif [[ "${arg}" =~ ^--backend=(v2|legacy)$ ]]; then
    backend="${BASH_REMATCH[1]}"
  else
    echo "[resume_scheduler_pipeline] unknown argument: ${arg}" >&2
    exit 2
  fi
done

resume_json="$(
  cd "${repo_root}" &&
  EIEL_CLI_BACKEND="${backend}" python - <<'PY'
import json
from pathlib import Path
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend

sched_backend.require_legacy_for_mutation("resume scheduler pipeline", backend=__import__("os").environ.get("EIEL_CLI_BACKEND") or None)

root = sched._repo_root()
manifest_path = (root / "runtime" / "state" / "webui" / "scheduler_pipeline_pause_state.json").resolve()
if not manifest_path.exists() or not manifest_path.is_file():
    print(json.dumps({"ok": False, "reason": "missing_manifest", "manifest_path": str(manifest_path)}))
    raise SystemExit(0)

try:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
except Exception:
    manifest = {}
job_ids = manifest.get("enabled_job_ids_before_pause")
if not isinstance(job_ids, list):
    job_ids = []

resumed = []
missing = []
for raw in job_ids:
    jid = str(raw or "").strip()
    if not jid:
        continue
    spec_path = sched._job_path(jid, repo_root=root)
    if not spec_path.exists():
        missing.append(jid)
        continue
    sched.set_job_enabled(jid, True, repo_root=root)
    resumed.append(jid)

print(json.dumps({
    "ok": True,
    "manifest_path": str(manifest_path),
    "resumed_count": len(resumed),
    "resumed_job_ids": resumed,
    "missing_job_ids": missing,
}))
PY
)"

ok_flag="$(python - "${resume_json}" <<'PY'
import json,sys
print("1" if bool(json.loads(sys.argv[1] or "{}").get("ok")) else "0")
PY
)"
if [[ "${ok_flag}" != "1" ]]; then
  python - "${resume_json}" <<'PY'
import json,sys
obj=json.loads(sys.argv[1] or "{}")
print(f"[resume_scheduler_pipeline] {obj.get('reason','unknown_error')}: {obj.get('manifest_path','')}")
PY
  exit 1
fi

python - "${resume_json}" <<'PY'
import json,sys
obj=json.loads(sys.argv[1] or "{}")
print(f"[resume_scheduler_pipeline] resumed_count={int(obj.get('resumed_count') or 0)}")
if obj.get("missing_job_ids"):
    print("[resume_scheduler_pipeline] missing job specs:")
    for jid in obj.get("missing_job_ids") or []:
        print(f"  - {jid}")
PY

if [[ "${keep_manifest}" != "1" ]]; then
  manifest_path="$(python - "${resume_json}" <<'PY'
import json,sys
print(str(json.loads(sys.argv[1] or "{}").get("manifest_path") or ""))
PY
)"
  if [[ -n "${manifest_path}" && -f "${manifest_path}" ]]; then
    rm -f "${manifest_path}"
    echo "[resume_scheduler_pipeline] removed manifest ${manifest_path}"
  fi
fi

echo "[resume_scheduler_pipeline] done"
