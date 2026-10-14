#!/usr/bin/env python3
"""Postpone the current active once-job by N days and resume pipeline execution."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend


def _active_jobs(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        if str(state.get("active_run_id") or "").strip():
            out.append(row)
    return out


def _pick_target(rows: List[Dict[str, Any]], job_id: str) -> Dict[str, Any]:
    active = _active_jobs(rows)
    if job_id:
        for row in active:
            if str(row.get("job_id") or "").strip() == job_id:
                return row
        raise ValueError(f"job_id is not active: {job_id}")
    if len(active) == 0:
        raise ValueError("no active scheduled job found")
    if len(active) > 1:
        ids = ", ".join(str(r.get("job_id") or "") for r in active)
        raise ValueError(f"multiple active jobs found; pass --job-id explicitly: {ids}")
    return active[0]


def _utc_iso(when: dt.datetime) -> str:
    return when.astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _postpone_target(
    *,
    target: Dict[str, Any],
    days: int,
    resume_next: bool,
    dry_run: bool,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    job_id = str(target.get("job_id") or "").strip()
    spec = sched.load_job(job_id, repo_root=repo_root)
    state = sched.load_state(job_id, repo_root=repo_root)
    schedule = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    if str(schedule.get("mode") or "").strip().lower() != "once":
        raise ValueError(f"active job is not schedule.mode=once: {job_id}")

    active_run_id = str((state or {}).get("active_run_id") or "").strip()
    now = dt.datetime.now(dt.timezone.utc)
    new_run_at = _utc_iso(now + dt.timedelta(days=max(1, int(days))))
    old_run_at = str(schedule.get("run_at") or "")

    summary: Dict[str, Any] = {
        "job_id": job_id,
        "active_run_id": active_run_id,
        "old_run_at": old_run_at,
        "new_run_at": new_run_at,
        "days": int(days),
        "dry_run": bool(dry_run),
    }

    if dry_run:
        summary["changed"] = False
        summary["tick"] = {"ok": False, "skipped": True, "reason": "dry_run"}
        return summary

    spec["schedule"] = {**schedule, "run_at": new_run_at}
    sched.save_job(spec, repo_root=repo_root)

    recovered_utc = sched._utc_iso()
    new_state = {
        **state,
        "active_run_id": "",
        "active_started_utc": "",
        "last_status": "interrupted",
        "last_recovered_run_id": active_run_id,
        "last_recovered_reason": "manual_postpone_active_job",
        "last_recovered_utc": recovered_utc,
    }
    sched._write_state(job_id, new_state, repo_root=repo_root)
    if active_run_id:
        sched._release_job_claim(job_id, repo_root=repo_root, run_id=active_run_id)
        sched._append_event(
            repo_root=repo_root,
            job_id=job_id,
            run_id=active_run_id,
            event="active_run_recovered",
            trigger="manual",
            status="interrupted",
            details={
                "recovery_reason": "manual_postpone_active_job",
                "recovered_run_id": active_run_id,
                "new_run_at": new_run_at,
            },
        )

    tick_result: Dict[str, Any] = {"ok": True, "executed_count": 0, "executed": [], "skipped": []}
    if resume_next:
        tick_result = sched.scheduler_tick(repo_root=repo_root, limit=1)

    summary["changed"] = True
    summary["tick"] = tick_result
    return summary


def main() -> int:
    try:
        ap = argparse.ArgumentParser(
            description=(
                "Stop the active scheduled job logically, postpone its run_at, "
                "and run scheduler tick to continue with the next runnable job."
            )
        )
        ap.add_argument("--job-id", default="", help="active job id to postpone; required when multiple jobs are active")
        ap.add_argument("--days", type=int, default=7, help="days to postpone (default: 7)")
        ap.add_argument("--no-resume-next", action="store_true", help="do not run scheduler tick after postponing")
        ap.add_argument("--dry-run", action="store_true", help="report planned action without modifying state")
        ap.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = ap.parse_args()
        sched_backend.require_legacy_for_mutation("postpone active scheduled job", backend=args.backend or None)

        if int(args.days) < 1:
            raise SystemExit("--days must be >= 1")

        rows = sched.list_jobs()
        target = _pick_target(rows, str(args.job_id or "").strip())
        result = _postpone_target(
            target=target,
            days=int(args.days),
            resume_next=not bool(args.no_resume_next),
            dry_run=bool(args.dry_run),
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
