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
from tools.bin.scheduler_tty_ui import prompt_menu


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
                return (0 if state.get("active_run_id") else 1, -_parse_utc(stamp).timestamp(), row["job_id"])
            except Exception:
                continue
    return (1, 0.0, row["job_id"])


def parse_selection(expr: str, max_index: int) -> List[int]:
    selected: List[int] = []
    seen = set()
    for part in [p.strip() for p in expr.split(",") if p.strip()]:
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


def main(argv: List[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(description="Start one or more scheduled jobs.")
        parser.add_argument("selection", nargs="?", help="Selection like 1,2,5-7. Default: interactive prompt.")
        parser.add_argument("--limit", type=int, default=50, help="How many recent jobs to show (default: 50).")
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        sched_backend.require_legacy_for_mutation("start scheduled job", backend=args.backend or None)

        rows = sorted(sched.list_jobs(repo_root=REPO_ROOT), key=_recent_sort_key)[: max(1, args.limit)]
        if not rows:
            print("No scheduled jobs found.")
            return 1

        print("Recent jobs")
        for idx, row in enumerate(rows, start=1):
            state = dict(row.get("state") or {})
            due = dict(row.get("due") or {})
            status = str(state.get("last_status") or "").strip() or str(due.get("reason") or "")
            active = " (active)" if state.get("active_run_id") else ""
            print(f"  {idx}. {row['display_title']} [{row['job_id']}] {status}{active}")

        expr = args.selection
        if not expr:
            selected = prompt_menu(
                "Select job to start",
                choices=list(enumerate(rows, start=1)),
                render=lambda item: f"{item[0]}. {item[1]['display_title']} [{item[1]['job_id']}]",
                default_index=0,
                fallback_reader=input,
            )
            expr = str(selected[0])

        try:
            indexes = parse_selection(expr, len(rows))
        except Exception as exc:
            print(f"Invalid selection: {exc}", file=sys.stderr)
            return 2

        exit_code = 0
        for index in indexes:
            row = rows[index - 1]
            print(f"[start] {row['job_id']}")
            launched = sched.request_run_now(row["job_id"], repo_root=REPO_ROOT, request_source="manual")
            ok = bool(launched.get("ok"))
            if not ok:
                exit_code = 1
            print(
                f"[queued] ok={str(ok).lower()} queued={str(bool(launched.get('queued'))).lower()} "
                f"reason={launched.get('reason') or ''} requested_utc={launched.get('requested_utc') or ''}"
            )
        return exit_code
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
