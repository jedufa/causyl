#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

PYTHONPATH_VALUE="${PYTHONPATH:-}"
if [[ -n "$PYTHONPATH_VALUE" ]]; then
  export PYTHONPATH=".:$PYTHONPATH_VALUE"
else
  export PYTHONPATH="."
fi

if [[ -z "${EIEL_CODEX_TURN_CMD:-}" ]]; then
  echo "[rerun-campaign] EIEL_CODEX_TURN_CMD is not set; aborting." >&2
  exit 2
fi

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
OUT_DIR="governance/agents/runtime/scheduled_jobs/rerun_campaigns"
LOG_PATH="$OUT_DIR/rerun_one_iter_ok__${STAMP}.jsonl"
mkdir -p "$OUT_DIR"

MAX_WAIT_S="${MAX_WAIT_S:-1800}"
SLEEP_S=10
ELAPSED=0

echo "[rerun-campaign] waiting for scheduler jobs to become idle..."
while true; do
  RUNNING_COUNT="$(
    python - <<'PY'
import json
from pathlib import Path
state_dir = Path("governance/agents/runtime/scheduled_jobs/state")
count = 0
for p in state_dir.glob("*.json"):
    try:
        s = json.load(open(p))
    except Exception:
        continue
    if str(s.get("last_status") or "") == "running":
        count += 1
print(count)
PY
  )"
  if [[ "$RUNNING_COUNT" == "0" ]]; then
    break
  fi
  if (( ELAPSED >= MAX_WAIT_S )); then
    echo "[rerun-campaign] timeout waiting for idle state (${MAX_WAIT_S}s)." >&2
    exit 3
  fi
  sleep "$SLEEP_S"
  ELAPSED=$((ELAPSED + SLEEP_S))
done

mapfile -t JOBS < <(
  python - <<'PY'
import json
from pathlib import Path
base = Path("governance/agents/runtime/scheduled_jobs")
state = base / "state"
receipts = base / "receipts"
inbox = base / "inbox"

rows = []
for stp in sorted(state.glob("*.json")):
    try:
        st = json.load(open(stp))
    except Exception:
        continue
    jid = st.get("job_id") or stp.stem
    if st.get("last_status") != "ok":
        continue
    rid = st.get("last_run_id")
    if not rid:
        continue
    rp = receipts / jid / f"{rid}.json"
    if not rp.exists():
        continue
    try:
        r = json.load(open(rp))
    except Exception:
        continue
    if r.get("job_kind") != "build_verify_loop":
        continue
    loop = r.get("loop") if isinstance(r.get("loop"), dict) else {}
    it = int(loop.get("completed_iterations") or len(loop.get("iterations") or []) or 0)
    if it != 1:
        continue
    sp = inbox / f"{jid}.json"
    if not sp.exists():
        continue
    try:
        spec = json.load(open(sp))
    except Exception:
        continue
    if not bool(spec.get("enabled")):
        continue
    rows.append(jid)

for jid in rows:
    print(jid)
PY
)

if (( ${#JOBS[@]} == 0 )); then
  echo "[rerun-campaign] no eligible 1-iteration jobs found."
  exit 0
fi

echo "[rerun-campaign] selected ${#JOBS[@]} jobs"
echo "[rerun-campaign] log: $LOG_PATH"

for jid in "${JOBS[@]}"; do
  NOW="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "[rerun-campaign] running $jid at $NOW"
  RES="$(
    python infra/bin/scheduler_schedule_job.py run-now "$jid" 2>&1 || true
  )"
  python - <<'PY' "$LOG_PATH" "$jid" "$NOW" "$RES"
import json
import sys
from datetime import datetime, timezone

path, jid, started, raw = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
row = {
    "utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
    "started_utc": started,
    "job_id": jid,
    "raw": raw,
}
with open(path, "a", encoding="utf-8") as f:
    f.write(json.dumps(row, ensure_ascii=False) + "\n")
PY
done

echo "[rerun-campaign] complete"
