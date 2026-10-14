#!/usr/bin/env python3
"""Interactive helper to enable or disable scheduled jobs.

Usage:
  scheduler_enable_disable_jobs.py [--all]

Options:
  --all    Show every job instead of only those due within the next 48 hours.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from heapq import heappop, heappush
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_repo_in_path() -> None:
    sys.path.insert(0, str(REPO_ROOT))


_ensure_repo_in_path()

from tools.bin.scheduler_tty_ui import prompt_menu
from infra.controlplane.sched import operator_backend as sched_backend
from infra.webui.ledger import ledger_storage


def _pis_title_from_binding(binding_id: str) -> str:
    if not binding_id:
        return ""
    item_dir = ledger_storage.item_dir_for_uid(REPO_ROOT / "governance" / "ledger" / "items", binding_id)
    if item_dir is None:
        return ""
    lines = (item_dir / "pis.md").read_text(encoding="utf-8", errors="replace").splitlines()
    in_title = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("## 1. Title"):
            in_title = True
            continue
        if stripped.startswith("## ") and in_title:
            break
        if in_title and stripped:
            return stripped
    return ""


def _parse_due_timestamp(row: Dict[str, Any]) -> Optional[datetime]:
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    raw = str(due.get("due_utc") or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _hypothetical_due_for_disabled(row: Dict[str, Any], *, now: datetime) -> Dict[str, Any]:
    from infra.controlplane import scheduled_jobs as sched

    if bool(row.get("enabled")):
        return dict(row.get("due") or {})
    job_id = str(row.get("job_id") or "")
    if not job_id:
        return {}
    spec = sched.load_job(job_id, repo_root=REPO_ROOT)
    spec["enabled"] = True
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    return sched.due_status(spec, state=state, now=now, repo_root=REPO_ROOT)


def _row_effective_due(row: Dict[str, Any], *, now: datetime) -> Dict[str, Any]:
    if bool(row.get("enabled")):
        return dict(row.get("due") or {})
    hypothetical = _hypothetical_due_for_disabled(row, now=now)
    out = dict(hypothetical)
    out["_hypothetical"] = True
    return out


def _reason_display(reason: str, rows_by_job_id: Dict[str, Dict[str, Any]]) -> str:
    txt = str(reason or "").strip()
    if not txt:
        return ""
    if txt.startswith("dependency_not_ok:"):
        dep_id = txt.split(":", 1)[1].strip()
        dep_row = rows_by_job_id.get(dep_id) or {}
        dep_title = str(dep_row.get("display_title") or dep_id).strip()
        return f"blocked by {dep_title}"
    return txt.replace("_", " ")


def _disabled_reason_display(effective_due: Dict[str, Any], rows_by_job_id: Dict[str, Dict[str, Any]]) -> str:
    reason = str(effective_due.get("reason") or "").strip().lower()
    if reason.startswith("dependency_not_ok:"):
        return f"disabled; would be {_reason_display(reason, rows_by_job_id)}"
    if reason in {"due", "due_failed_slot_reusable"}:
        return "disabled; would be due"
    if reason == "not_due_yet":
        due_utc = str(effective_due.get("due_utc") or "").strip()
        return f"disabled; due {due_utc}" if due_utc else "disabled; due soon"
    if reason == "active_run":
        return "disabled; blocked by active run"
    if reason:
        return f"disabled; would be {_reason_display(reason, rows_by_job_id)}"
    return "disabled"


def _display_sort_key(job_id: str, rows_by_job_id: Dict[str, Dict[str, Any]], effective_due: Dict[str, Dict[str, Any]]) -> Tuple[str, str, str]:
    row = rows_by_job_id.get(job_id) or {}
    due = effective_due.get(job_id) or {}
    return (
        str(due.get("due_utc") or ""),
        str(row.get("display_title") or ""),
        job_id,
    )


def _order_rows_by_dependencies(rows: List[Dict[str, Any]], *, now: datetime) -> List[Dict[str, Any]]:
    rows_by_job_id = {str(row.get("job_id") or ""): row for row in rows if row.get("job_id")}
    effective_due = {job_id: _row_effective_due(row, now=now) for job_id, row in rows_by_job_id.items()}
    indegree: Dict[str, int] = {job_id: 0 for job_id in rows_by_job_id}
    children: Dict[str, List[str]] = {job_id: [] for job_id in rows_by_job_id}
    for job_id, row in rows_by_job_id.items():
        for dep in list(row.get("depends_on") or []):
            dep_id = str(dep or "").strip()
            if dep_id in rows_by_job_id:
                indegree[job_id] += 1
                children.setdefault(dep_id, []).append(job_id)
    heap: List[Tuple[Tuple[str, str, str], str]] = []
    for job_id, degree in indegree.items():
        if degree == 0:
            heappush(heap, (_display_sort_key(job_id, rows_by_job_id, effective_due), job_id))
    ordered: List[Dict[str, Any]] = []
    seen: set[str] = set()
    while heap:
        _key, job_id = heappop(heap)
        if job_id in seen:
            continue
        seen.add(job_id)
        ordered.append(rows_by_job_id[job_id])
        for child_id in sorted(children.get(job_id) or [], key=lambda x: _display_sort_key(x, rows_by_job_id, effective_due)):
            indegree[child_id] -= 1
            if indegree[child_id] <= 0:
                heappush(heap, (_display_sort_key(child_id, rows_by_job_id, effective_due), child_id))
    for job_id in sorted((jid for jid in rows_by_job_id if jid not in seen), key=lambda x: _display_sort_key(x, rows_by_job_id, effective_due)):
        ordered.append(rows_by_job_id[job_id])
    return ordered


def _should_include_row(row: Dict[str, Any], *, include_all: bool, now: datetime, target: datetime) -> bool:
    effective_due = _row_effective_due(row, now=now)
    reason = str(effective_due.get("reason") or "").lower()
    if reason == "already_executed":
        return False
    if include_all:
        return True
    ts = _parse_due_timestamp({"due": effective_due})
    if ts and ts <= target:
        return True
    return reason in {"due", "due_failed_slot_reusable", "active_run"} or reason.startswith("dependency_not_ok:")


def _list_pending_jobs(include_all: bool) -> List[Dict[str, Any]]:
    from infra.controlplane import scheduled_jobs as sched

    now = datetime.now(timezone.utc)
    target = now + timedelta(days=2)
    rows = sched.list_jobs(repo_root=REPO_ROOT)
    pending = [row for row in rows if _should_include_row(row, include_all=include_all, now=now, target=target)]
    return _order_rows_by_dependencies(pending, now=now)


def _describe_row(row: Dict[str, Any], rows_by_job_id: Dict[str, Dict[str, Any]]) -> str:
    effective_due = _row_effective_due(row, now=datetime.now(timezone.utc))
    if bool(row.get("enabled")):
        return _reason_display(str(effective_due.get("reason") or ""), rows_by_job_id)
    return _disabled_reason_display(effective_due, rows_by_job_id)


def _choose_job_from_list(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        print("No jobs available for selection.", file=sys.stderr)
        return []

    rows_by_job_id = {str(row.get("job_id") or ""): row for row in rows}
    print("Scheduled jobs:")
    for idx, row in enumerate(rows, start=1):
        runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
        model = str(runtime.get("model") or "").strip() or "(unspecified)"
        binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
        binding_id = str(binding.get("id") or "").strip()
        tail = binding_id[-4:] if binding_id else row.get("job_id", "")[-4:]
        title = str(row.get("display_title") or row.get("job_id") or "").strip()
        reason = _describe_row(row, rows_by_job_id)
        status = "enabled" if bool(row.get("enabled")) else "disabled"
        reason_txt = f"({reason})" if reason else ""
        pis_title = _pis_title_from_binding(binding_id) or ""
        if pis_title:
            print(f"  {idx}. [{status}] [{model}] {tail} {pis_title} - {title} {reason_txt}")
        else:
            print(f"  {idx}. [{status}] [{model}] {tail} {title} {reason_txt}")
    selected = prompt_menu(
        "Select job",
        choices=rows,
        render=lambda row: f"[{'enabled' if bool(row.get('enabled')) else 'disabled'}] {row.get('display_title') or row.get('job_id')} [{row.get('job_id')}]",
        default_index=0,
        fallback_reader=input,
    )
    return [selected]


def _prompt_action() -> bool:
    selected = prompt_menu("Action", choices=["disable", "enable"], default_index=0, fallback_reader=input)
    return selected == "enable"


def _set_job_enabled(job_id: str, enabled: bool) -> None:
    from infra.controlplane import scheduled_jobs as sched

    sched.set_job_enabled(job_id, enabled, repo_root=REPO_ROOT)


def main() -> int:
    try:
        _ensure_repo_in_path()
        parser = argparse.ArgumentParser(description="Enable or disable scheduled jobs interactively.")
        parser.add_argument("--all", action="store_true", help="Show every job instead of only those due within 48 hours.")
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args()
        sched_backend.require_legacy_for_mutation("enable or disable scheduled jobs", backend=args.backend or None)

        rows = _list_pending_jobs(include_all=args.all)
        selected = _choose_job_from_list(rows)
        if not selected:
            print("No selection made; exiting.")
            return 1
        job_ids = [str(row.get("job_id") or "") for row in selected]
        job_ids = [jid for jid in job_ids if jid]
        if not job_ids:
            print("Selected entries missing job IDs; exiting.")
            return 1

        enable = _prompt_action()
        verb = "enable" if enable else "disable"
        for jid in job_ids:
            _set_job_enabled(jid, enable)
            print(f"{verb.capitalize()}d {jid}")
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
