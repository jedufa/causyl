from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import server_maintenance as maint


REPORT_SCHEMA_VERSION = "runtime_failure_report_v1"
READY_URL_DEFAULT = "http://127.0.0.1:8787/api/schedules/jobs"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _utc_iso(ts: Optional[dt.datetime] = None) -> str:
    return (ts or _utc_now()).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _runtime_health_dir(repo_root: Path) -> Path:
    path = (repo_root / "runtime" / "state" / "runtime_health").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _server_pid_path(repo_root: Path) -> Path:
    return (repo_root / "runtime" / "state" / "webui" / "webui_server.pid").resolve()


def _read_pid(path: Path) -> int:
    try:
        return int((path.read_text(encoding="utf-8").strip() or "0"))
    except Exception:
        return 0


def _is_pid_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _http_ready(url: str) -> Tuple[bool, str]:
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200, ""
    except urllib.error.URLError as exc:
        return False, str(exc.reason or exc)
    except socket.timeout:
        return False, "timeout"
    except Exception as exc:
        return False, str(exc)


def _job_rows(repo_root: Path) -> List[Dict[str, Any]]:
    return sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())


def _stale_or_orphan_jobs(rows: List[Dict[str, Any]]) -> Tuple[List[str], List[str]]:
    stale_claim_jobs: List[str] = []
    orphan_running_jobs: List[str] = []
    for row in rows:
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        job_id = str(row.get("job_id") or "")
        if bool(state.get("active_claim_stale")):
            stale_claim_jobs.append(job_id)
        if str(state.get("active_run_id") or "").strip() and not bool(state.get("active_claim_present")):
            orphan_running_jobs.append(job_id)
    return stale_claim_jobs, orphan_running_jobs


def _recommended_actions(*, server_alive: bool, http_ready: bool, stale_locks: int, stale_claim_jobs: List[str], orphan_running_jobs: List[str], blocked_jobs: int) -> List[str]:
    actions: List[str] = []
    if not server_alive:
        actions.append("start_server")
    elif not http_ready:
        actions.append("inspect_server_readiness")
    if stale_locks > 0 or stale_claim_jobs:
        actions.append("run_scheduler_watchdog_selfheal")
    if orphan_running_jobs:
        actions.append("clear_orphan_running_state")
    if blocked_jobs > 0:
        actions.append("inspect_scheduler_live_watch")
    if not actions:
        actions.append("no_action_needed")
    return actions


def _incident_classification(*, server_alive: bool, http_ready: bool, stale_locks: int, stale_claim_jobs: List[str], orphan_running_jobs: List[str], blocked_jobs: int) -> Dict[str, Any]:
    codes: List[str] = []
    severity = "healthy"
    if not server_alive:
        codes.append("server_not_running")
        severity = "critical"
    elif not http_ready:
        codes.append("server_not_ready")
        severity = "critical"
    if stale_locks > 0:
        codes.append("stale_locks")
        severity = "critical"
    if stale_claim_jobs:
        codes.append("stale_claims")
        severity = "critical"
    if orphan_running_jobs:
        codes.append("orphan_running_state")
        severity = "critical"
    if blocked_jobs > 0 and severity == "healthy":
        codes.append("blocked_jobs_present")
        severity = "degraded"
    if not codes:
        codes.append("healthy")
    return {"severity": severity, "codes": codes}


def build_report(*, repo_root: Optional[Path] = None, ready_url: str = READY_URL_DEFAULT, write_cache: bool = True) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    pid_path = _server_pid_path(root)
    pid = _read_pid(pid_path)
    server_alive = _is_pid_alive(pid)
    http_ready, http_error = _http_ready(ready_url)
    rows = _job_rows(root)
    locks = maint.active_job_locks(repo_root=root)
    stale_claim_jobs, orphan_running_jobs = _stale_or_orphan_jobs(rows)
    blocked_jobs = sum(1 for row in rows if str(((row.get("state") or {}).get("last_status") or "")).lower() == "blocked")
    failed_jobs = sum(1 for row in rows if str(((row.get("state") or {}).get("last_status") or "")).lower() in {"failed_verification", "error", "proof_gap"})
    active_jobs = sum(1 for row in rows if str(((row.get("state") or {}).get("active_run_id") or "")).strip())
    classification = _incident_classification(
        server_alive=server_alive,
        http_ready=http_ready,
        stale_locks=len(locks),
        stale_claim_jobs=stale_claim_jobs,
        orphan_running_jobs=orphan_running_jobs,
        blocked_jobs=blocked_jobs,
    )
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "timestamp_utc": _utc_iso(),
        "server_metrics": {
            "pid": pid,
            "pid_file": str(pid_path),
            "server_alive": server_alive,
            "ready_url": ready_url,
            "http_ready": http_ready,
            "http_error": http_error,
            "active_lock_count": len(locks),
            "active_job_count": active_jobs,
            "blocked_job_count": blocked_jobs,
            "failed_job_count": failed_jobs,
        },
        "failure_signature": {
            "stale_claim_jobs": stale_claim_jobs,
            "orphan_running_jobs": orphan_running_jobs,
            "active_lock_jobs": [str(row.get("job_id") or "") for row in locks],
        },
        "incident_classification": classification,
        "recommended_actions": _recommended_actions(
            server_alive=server_alive,
            http_ready=http_ready,
            stale_locks=len(locks),
            stale_claim_jobs=stale_claim_jobs,
            orphan_running_jobs=orphan_running_jobs,
            blocked_jobs=blocked_jobs,
        ),
        "proof_bundle": {
            "job_ids": [str(row.get("job_id") or "") for row in rows],
            "lock_paths": [str(row.get("path") or "") for row in locks],
        },
        "tool_version": "runtime_health_probe_v1",
    }
    if write_cache:
        out_dir = _runtime_health_dir(root)
        _write_json((out_dir / "latest_report.json").resolve(), report)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Probe runtime/server health and classify scheduler failures.")
    parser.add_argument("--server", default=READY_URL_DEFAULT, help="Ready URL to probe.")
    parser.add_argument("--no-cache", action="store_true", help="Do not write runtime/state/runtime_health/latest_report.json")
    args = parser.parse_args(argv)
    report = build_report(ready_url=str(args.server), write_cache=not bool(args.no_cache))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
