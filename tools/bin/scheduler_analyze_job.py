#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from tools.bin.scheduler_tty_ui import prompt_menu
from tools.bin.scheduler_verify_job import verify_job


def _parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)


def _recent_activity_utc(row: Dict[str, Any]) -> str:
    state = dict(row.get("state") or {})
    due = dict(row.get("due") or {})
    for key in ("active_started_utc", "last_started_utc", "last_nonlaunch_utc", "last_finished_utc"):
        if state.get(key):
            return str(state[key])
    if due.get("due_utc"):
        return str(due["due_utc"])
    return str(row.get("created_utc") or "")


def recent_sort_key(row: Dict[str, Any]) -> tuple[int, float, str]:
    state = dict(row.get("state") or {})
    stamp = _recent_activity_utc(row)
    try:
        ts = _parse_utc(stamp).timestamp() if stamp else 0.0
    except Exception:
        ts = 0.0
    return (0 if state.get("active_run_id") else 1, -ts, row["job_id"])


def _latest_receipt(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    state = dict(row.get("state") or {})
    active_run_id = str(state.get("active_run_id") or "").strip()
    if active_run_id:
        try:
            return sched.load_receipt(row["job_id"], active_run_id, repo_root=REPO_ROOT)
        except Exception:
            pass
    receipts = sched.list_receipts(row["job_id"], repo_root=REPO_ROOT, limit=1)
    return receipts[0] if receipts else None


def iteration_brief(iteration: Dict[str, Any]) -> Dict[str, Any]:
    builder = dict(iteration.get("builder") or {})
    verifier = dict(iteration.get("verifier") or {})
    builder_meta = dict(builder.get("meta") or {})
    verifier_meta = dict(verifier.get("meta") or {})
    return {
        "iteration": int(iteration.get("iteration") or 0),
        "builder_runtime": f"{builder_meta.get('agent_tech') or '?'}:{builder_meta.get('selected_model') or builder_meta.get('model') or '?'}",
        "verifier_runtime": f"{verifier_meta.get('agent_tech') or '?'}:{verifier_meta.get('selected_model') or verifier_meta.get('model') or '?'}",
        "builder_preview": " ".join(str(builder.get("content") or "").split())[:180],
        "verifier_preview": " ".join(str(verifier.get("content") or "").split())[:180],
    }


def _choose_job(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    return prompt_menu(
        "Recent scheduled jobs",
        choices=rows,
        render=lambda row: (
            f"{row['display_title']} [{row['job_id']}] "
            f"{str((dict(row.get('state') or {}).get('last_status') or dict(row.get('due') or {}).get('reason') or '')).strip()}"
            f"{' (active)' if dict(row.get('state') or {}).get('active_run_id') else ''}"
        ),
        default_index=0,
        fallback_reader=input,
    )


def _choose_mode(default: str) -> str:
    modes = ["executive", "standard", "complete"]
    return prompt_menu(
        "Analysis mode",
        choices=modes,
        default_index=modes.index(default) if default in modes else 1,
        fallback_reader=input,
    )


def _print_header(row: Dict[str, Any], verification: Dict[str, Any]) -> None:
    state = dict(row.get("state") or {})
    due = dict(row.get("due") or {})
    print(f"Job: {row['display_title']}")
    print(f"- job_id: {row['job_id']}")
    print(f"- verdict: {verification['verdict']}")
    print(f"- job_kind: {row['job_kind']}")
    print(f"- due_reason: {due.get('reason') or ''}")
    print(f"- top_runtime: {verification['runtime']}")
    if verification.get('builder_runtime'):
        print(f"- builder_runtime: {verification['builder_runtime']}")
        print(f"- verifier_runtime: {verification['verifier_runtime']}")
    print(f"- active_run_id: {state.get('active_run_id') or ''}")
    print(f"- active_claim_present: {str(bool(state.get('active_claim_present'))).lower()}")
    print(f"- active_claim_reason: {state.get('active_claim_reason') or ''}")
    print(f"- last_status: {state.get('last_status') or ''}")


def _print_receipt(receipt: Optional[Dict[str, Any]], mode: str) -> None:
    if not receipt:
        print("- receipt: none")
        return
    print(f"- receipt_run_id: {receipt.get('run_id') or ''}")
    print(f"- receipt_status: {receipt.get('status') or ''}")
    print(f"- started_utc: {receipt.get('started_utc') or ''}")
    print(f"- finished_utc: {receipt.get('finished_utc') or ''}")
    if receipt.get("error"):
        print(f"- error: {' '.join(str(receipt.get('error')).split())[:200]}")
    loop = dict(receipt.get("loop") or {})
    iterations = list(loop.get("iterations") or [])
    if iterations:
        print(f"- iterations_completed: {loop.get('completed_iterations') or len(iterations)}/{loop.get('max_iterations') or '?'}")
        for brief in [iteration_brief(item) for item in iterations[-3:]]:
            print(f"  - iter {brief['iteration']}: builder={brief['builder_runtime']} verifier={brief['verifier_runtime']}")
            print(f"    builder: {brief['builder_preview']}")
            print(f"    verifier: {brief['verifier_preview']}")
    if mode == "complete":
        print("- receipt_json:")
        print(json.dumps(receipt, indent=2, ensure_ascii=False)[:12000])


def main(argv: List[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(description="Analyze recent scheduled job runs.")
        parser.add_argument("selection", nargs="?", help="Optional 1-based job selection from the recent list.")
        parser.add_argument("--mode", choices=["executive", "standard", "complete"], default=None)
        parser.add_argument("--limit", type=int, default=20)
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        if sched_backend.selected_backend(args.backend or None) == "v2":
            bundle = sched_backend.v2_surface_bundle()
            rows = list(((bundle.get("surfaces") or {}).get("watch") or []))
            print(f"Scheduler v2 surface: status={bundle.get('status') or ''} parent={bundle.get('parent_uid') or ''}")
            for row in rows[: max(1, int(args.limit))]:
                print(
                    f"- {row.get('child_uid') or '-'} decision={row.get('decision') or '-'} "
                    f"kind={row.get('node_kind') or '-'} gate={row.get('gate') or '-'} title={row.get('title') or ''}"
                )
            return 0

        rows = sorted(sched.list_jobs(repo_root=REPO_ROOT), key=recent_sort_key)[: max(1, args.limit)]
        if not rows:
            print("No scheduled jobs found.")
            return 1

        if args.selection:
            idx = int(args.selection) - 1
            if idx < 0 or idx >= len(rows):
                print("selection out of range", file=sys.stderr)
                return 2
            row = rows[idx]
        else:
            row = _choose_job(rows)

        mode = args.mode or _choose_mode("standard")
        verification = verify_job(row["job_id"], repo_root=REPO_ROOT)
        receipt = _latest_receipt(row)

        _print_header(row, verification)
        if mode != "executive":
            _print_receipt(receipt, mode)
        if verification.get("failures"):
            print("- failures:")
            for item in verification["failures"]:
                print(f"  - {item}")
        if verification.get("warnings"):
            print("- warnings:")
            for item in verification["warnings"]:
                print(f"  - {item}")
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
