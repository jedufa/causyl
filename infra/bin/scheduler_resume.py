#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend


def _parse_utc(raw: str) -> datetime:
    return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)


def _recent_sort_key(row: Dict[str, Any]) -> tuple[float, str]:
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
                return (-_parse_utc(stamp).timestamp(), str(row.get("job_id") or ""))
            except Exception:
                continue
    return (0.0, str(row.get("job_id") or ""))


def parse_selection(expr: str, max_index: int) -> List[int]:
    text = str(expr or "").strip().lower()
    if text == "all":
        return list(range(1, max_index + 1))
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


def _manifest_rows(*, repo_root: Path) -> tuple[Path, List[Dict[str, Any]], List[str]]:
    manifest_path = (repo_root / "runtime" / "state" / "webui" / "scheduler_pipeline_pause_state.json").resolve()
    if not manifest_path.exists() or not manifest_path.is_file():
        raise FileNotFoundError(str(manifest_path))
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception:
        manifest = {}
    job_ids = manifest.get("enabled_job_ids_before_pause")
    if not isinstance(job_ids, list):
        job_ids = []

    jobs_by_id = {str(row.get("job_id") or ""): row for row in sched.list_jobs(repo_root=repo_root)}
    rows: List[Dict[str, Any]] = []
    missing: List[str] = []
    for raw in job_ids:
        jid = str(raw or "").strip()
        if not jid:
            continue
        row = jobs_by_id.get(jid)
        if row is None:
            missing.append(jid)
            continue
        rows.append(row)
    rows.sort(key=_recent_sort_key)
    return manifest_path, rows, missing


def main(argv: List[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(description="Resume paused scheduled jobs from the pause manifest.")
        parser.add_argument("selection", nargs="?", help="Selection like 1,2,5-7 or 'all'. Default: interactive prompt.")
        parser.add_argument("--all", action="store_true", help="Resume all jobs listed in the pause manifest.")
        parser.add_argument("--keep-manifest", action="store_true", help="Do not remove the pause manifest after resuming.")
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        sched_backend.require_legacy_for_mutation("resume paused scheduled jobs", backend=args.backend or None)

        manifest_path, rows, missing = _manifest_rows(repo_root=REPO_ROOT)
        if not rows and not missing:
            print(f"[scheduler_resume] no resumable jobs found in {manifest_path}")
            return 1

        print("Resumable jobs")
        for idx, row in enumerate(rows, start=1):
            state = dict(row.get("state") or {})
            due = dict(row.get("due") or {})
            status = str(state.get("last_status") or "").strip() or str(due.get("reason") or "").strip() or "unknown"
            print(f"  {idx}. {row['display_title']} [{row['job_id']}] {status}")
        if missing:
            print("Missing job specs")
            for jid in missing:
                print(f"  - {jid}")

        if not rows:
            return 0

        expr = "all" if bool(args.all) else str(args.selection or "").strip()
        if not expr:
            raw = input("Selection (default: 1, or 'all'): ").strip()
            expr = raw or "1"

        try:
            indexes = parse_selection(expr, len(rows))
        except Exception as exc:
            print(f"Invalid selection: {exc}", file=sys.stderr)
            return 2

        resumed: List[str] = []
        for index in indexes:
            row = rows[index - 1]
            jid = str(row.get("job_id") or "")
            sched.set_job_enabled(jid, True, repo_root=REPO_ROOT)
            resumed.append(jid)
            print(f"[scheduler_resume] resumed {jid}")

        print(f"[scheduler_resume] resumed_count={len(resumed)}")
        if missing:
            print(f"[scheduler_resume] missing_count={len(missing)}")

        if not bool(args.keep_manifest):
            manifest_path.unlink(missing_ok=True)
            print(f"[scheduler_resume] removed manifest {manifest_path}")
        print("[scheduler_resume] done")
        return 0
    except FileNotFoundError as exc:
        print(f"[scheduler_resume] missing_manifest: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
