#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from tools.bin.scheduler_tty_ui import prompt_yes_no


def _parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)


def _recent_sort_key(row: Dict[str, Any]) -> tuple[int, float, str]:
    state = dict(row.get("state") or {})
    due = dict(row.get("due") or {})
    stamps = [
        str(state.get("active_started_utc") or ""),
        str(state.get("last_started_utc") or ""),
        str(state.get("last_nonlaunch_utc") or ""),
        str(state.get("last_finished_utc") or ""),
        str(due.get("due_utc") or ""),
        str(row.get("created_utc") or ""),
    ]
    for stamp in stamps:
        if stamp:
            try:
                return (0 if state.get("active_run_id") else 1, -_parse_utc(stamp).timestamp(), str(row.get("job_id") or ""))
            except Exception:
                continue
    return (1, 0.0, str(row.get("job_id") or ""))


def parse_selection(expr: str, max_index: int) -> List[int]:
    selected: List[int] = []
    seen = set()
    for part in [p.strip() for p in str(expr or "").split(",") if p.strip()]:
        if "-" in part:
            left, right = part.split("-", 1)
            start = int(left)
            end = int(right)
            if start > end:
                start, end = end, start
            values = range(start, end + 1)
        else:
            values = [int(part)]
        for value in values:
            if value < 1 or value > max_index:
                raise ValueError(f"selection {value} out of range 1..{max_index}")
            if value not in seen:
                seen.add(value)
                selected.append(value)
    return selected


def _candidate_rows(*, limit: int) -> List[Dict[str, Any]]:
    rows = sorted(sched.list_jobs(repo_root=REPO_ROOT), key=_recent_sort_key)[: max(1, int(limit))]
    return [row for row in rows if str((row.get("state") or {}).get("active_run_id") or "").strip()]


def main(argv: List[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(description="Cancel one or more active scheduled jobs.")
        parser.add_argument("selection", nargs="?", help="Selection like 1,2,5-7. Default: interactive prompt.")
        parser.add_argument("--limit", type=int, default=50, help="How many recent jobs to inspect (default: 50).")
        parser.add_argument("--reason", default="operator_cancel", help="Cancellation reason to record.")
        parser.add_argument("--yes", action="store_true", help="Skip confirmation prompt.")
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        sched_backend.require_legacy_for_mutation("cancel scheduled job", backend=args.backend or None)

        rows = _candidate_rows(limit=args.limit)
        if not rows:
            print("No active scheduled jobs found.")
            return 1

        print("Active jobs")
        for idx, row in enumerate(rows, start=1):
            state = dict(row.get("state") or {})
            print(
                f"  {idx}. {row['display_title']} [{row['job_id']}] "
                f"active_run_id={state.get('active_run_id') or ''}"
            )

        expr = str(args.selection or "").strip()
        if not expr:
            expr = input("Selection [1]: ").strip() or "1"

        try:
            indexes = parse_selection(expr, len(rows))
        except Exception as exc:
            print(f"Invalid selection: {exc}", file=sys.stderr)
            return 2

        selected_rows = [rows[index - 1] for index in indexes]
        if not bool(args.yes):
            labels = ", ".join(str(row.get("job_id") or "") for row in selected_rows)
            if not prompt_yes_no(f"Cancel job(s): {labels}", default=False, fallback_reader=input):
                print("Canceled.", file=sys.stderr)
                return 130

        exit_code = 0
        for row in selected_rows:
            jid = str(row.get("job_id") or "")
            result = sched.cancel_job(jid, repo_root=REPO_ROOT, reason=str(args.reason or "operator_cancel"))
            ok = bool(result.get("ok"))
            if not ok:
                exit_code = 1
            print(f"[cancel] ok={str(ok).lower()} job_id={jid} status={result.get('status') or ''} receipt_path={result.get('receipt_path') or ''}")
        return exit_code
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
