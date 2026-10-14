#!/usr/bin/env python3
"""Delete one or more scheduled jobs by job id or numeric selection."""

from __future__ import annotations

import argparse
import json
import sys
from typing import List

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from tools.bin.scheduler_tty_ui import prompt_yes_no


class UserAbort(Exception):
    """Raised when the operator cancels the delete flow."""


def _read_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise UserAbort("input stream closed") from exc
    except KeyboardInterrupt as exc:
        raise UserAbort("canceled by user") from exc


def _require_tty() -> None:
    if not sys.stdin.isatty():
        raise UserAbort("interactive commands require a TTY")


def _render_row(index: int, row: dict) -> str:
    state = row.get("state") or {}
    return (
        f"{index}. {row.get('display_title') or row.get('job_id')} "
        f"[{row.get('job_id')}] "
        f"enabled={int(bool(row.get('enabled')))} "
        f"last={str(state.get('last_status') or '-')}"
    )


def _parse_selection_spec(raw: str, *, count: int) -> List[int]:
    text = str(raw or "").strip()
    if not text:
        raise ValueError("selection is required")
    selected: List[int] = []
    seen = set()
    for part in text.split(","):
        token = part.strip()
        if not token:
            continue
        if "-" in token:
            left, right = token.split("-", 1)
            if not left.strip().isdigit() or not right.strip().isdigit():
                raise ValueError(f"invalid range: {token}")
            start = int(left.strip())
            end = int(right.strip())
            if start > end:
                raise ValueError(f"descending range is not allowed: {token}")
            values = range(start, end + 1)
        else:
            if not token.isdigit():
                raise ValueError(f"invalid index: {token}")
            values = [int(token)]
        for value in values:
            if value < 1 or value > count:
                raise ValueError(f"selection {value} is out of range 1-{count}")
            if value in seen:
                continue
            seen.add(value)
            selected.append(value)
    if not selected:
        raise ValueError("selection is empty")
    return selected


def _resolve_job_ids(rows: List[dict], selection: str) -> List[str]:
    by_job_id = {str(row.get("job_id") or "").strip(): row for row in rows}
    token = str(selection or "").strip()
    if token in by_job_id:
        return [token]
    indexes = _parse_selection_spec(token, count=len(rows))
    return [str(rows[index - 1].get("job_id") or "").strip() for index in indexes]


def _prompt_selection(rows: List[dict]) -> str:
    _require_tty()
    print("Select scheduled jobs to delete:")
    for idx, row in enumerate(rows, start=1):
        print(_render_row(idx, row))
    print("Enter numbers like 1,2,4-8 or a job id.")
    return _read_input("Selection: ").strip()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Delete one or more scheduled jobs by job id or numeric selection.")
    p.add_argument("selection", nargs="?", help="job id or numeric selection like 1,2,4-8")
    p.add_argument("--yes", action="store_true", help="Skip confirmation prompt")
    p.add_argument("--purge-history", action="store_true", help="Also remove state, receipts, and stored prompt files")
    p.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
    return p


def main(argv: List[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        sched_backend.require_legacy_for_mutation("delete scheduled job", backend=args.backend or None)
        rows = sched.list_jobs()
        if not rows:
            print("No scheduled jobs found.", file=sys.stderr)
            return 1
        selection = str(args.selection or "").strip() or _prompt_selection(rows)
        job_ids = _resolve_job_ids(rows, selection)
        if not args.yes:
            suffix = " and purge their stored history" if args.purge_history else ""
            label = ", ".join(job_ids)
            confirmed = prompt_yes_no(
                f"Delete scheduled job(s) {label}{suffix}",
                default=False,
                fallback_reader=_read_input,
            )
            if not confirmed:
                raise UserAbort("delete canceled")
        results = [sched.delete_job(job_id, purge_history=bool(args.purge_history)) for job_id in job_ids]
        print(json.dumps({"ok": True, "deleted": results}, indent=2, ensure_ascii=False))
        return 0
    except UserAbort as exc:
        print(f"Canceled: {exc}", file=sys.stderr)
        return 130
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
