#!/usr/bin/env python3
"""Show active and upcoming scheduled jobs in a pipeline-friendly view."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend


def _status_of(row: Dict[str, Any]) -> str:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    active = str(state.get("active_run_id") or "").strip()
    if active:
        return "running"
    return str(state.get("last_status") or "unknown").strip() or "unknown"


def _is_ok_status(status: str) -> bool:
    return status.lower() == "ok"


def _dep_summary(row: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> Dict[str, List[str]]:
    deps = [str(x).strip() for x in list(row.get("depends_on") or []) if str(x).strip()]
    ok: List[str] = []
    missing: List[str] = []
    not_ok: List[str] = []
    for dep_id in deps:
        dep = by_id.get(dep_id)
        if not dep:
            missing.append(dep_id)
            continue
        dep_status = _status_of(dep)
        if _is_ok_status(dep_status):
            ok.append(dep_id)
        else:
            not_ok.append(f"{dep_id}({dep_status})")
    return {"ok": ok, "missing": missing, "not_ok": not_ok}


def _key_due(row: Dict[str, Any]) -> str:
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    return str(due.get("due_utc") or "")


def _trim(s: str, n: int = 110) -> str:
    txt = " ".join(str(s or "").split())
    if len(txt) <= n:
        return txt
    return txt[: n - 3] + "..."


def _as_row(row: Dict[str, Any], by_id: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    deps = _dep_summary(row, by_id)
    return {
        "job_id": str(row.get("job_id") or ""),
        "job_kind": str(row.get("job_kind") or ""),
        "binding": f"{str(binding.get('type') or '')}:{str(binding.get('id') or '')}",
        "enabled": bool(row.get("enabled")),
        "status": _status_of(row),
        "active_run_id": str(state.get("active_run_id") or ""),
        "last_run_id": str(state.get("last_run_id") or ""),
        "due_eligible": bool(due.get("eligible")),
        "due_reason": str(due.get("reason") or ""),
        "due_utc": str(due.get("due_utc") or ""),
        "depends_on": [str(x).strip() for x in list(row.get("depends_on") or []) if str(x).strip()],
        "deps_ok": deps["ok"],
        "deps_not_ok": deps["not_ok"],
        "deps_missing": deps["missing"],
        "prompt_preview": str(row.get("prompt_preview") or row.get("prompt") or "").strip(),
    }


def _print_section(title: str, rows: List[Dict[str, Any]]) -> None:
    print(f"\n{title} ({len(rows)})")
    if not rows:
        print("  - none")
        return
    for r in rows:
        print(
            f"  - {r['job_id']} [{r['job_kind']}] status={r['status']} "
            f"due={int(r['due_eligible'])} reason={r['due_reason'] or '-'}"
        )
        print(f"    binding={r['binding']} due_utc={r['due_utc'] or '-'}")
        if r["deps_not_ok"] or r["deps_missing"]:
            parts: List[str] = []
            if r["deps_not_ok"]:
                parts.append("not_ok=" + ",".join(r["deps_not_ok"]))
            if r["deps_missing"]:
                parts.append("missing=" + ",".join(r["deps_missing"]))
            print(f"    deps_blockers: {'; '.join(parts)}")
        elif r["depends_on"]:
            print(f"    deps_ok: {','.join(r['deps_ok'])}")
        print(f"    prompt: {_trim(r['prompt_preview'])}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Show active + next scheduled jobs in pipeline order.")
    ap.add_argument("--limit", type=int, default=20, help="max rows per section (default: 20)")
    ap.add_argument("--binding-id", default="", help="filter by binding id (task_id or ledger uid)")
    ap.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    args = ap.parse_args()

    backend = sched_backend.selected_backend(args.backend or None)
    if backend == "v2":
        bundle = sched_backend.v2_surface_bundle()
        rows = [dict(row) for row in list(((bundle.get("surfaces") or {}).get("watch") or [])) if isinstance(row, dict)]
        if args.binding_id:
            needle = str(args.binding_id).strip()
            rows = [row for row in rows if str(row.get("child_uid") or "").strip() == needle]
        active = [row for row in rows if str(row.get("decision") or "") == "active"]
        runnable = [row for row in rows if str(row.get("decision") or "") == "admit"]
        blocked = [row for row in rows if str(row.get("decision") or "") == "blocked"]
        retired = [row for row in rows if str(row.get("decision") or "") == "retired"]
        if args.json:
            print(
                json.dumps(
                    {
                        "backend": "v2",
                        "surface_status": str(bundle.get("status") or ""),
                        "parent_uid": str(bundle.get("parent_uid") or ""),
                        "active": active[: args.limit],
                        "next_runnable": runnable[: args.limit],
                        "blocked_by_dependencies": blocked[: args.limit],
                        "retired": retired[: args.limit],
                        "total_jobs": len(rows),
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        print("Next Pipeline Jobs")
        print(f"backend=v2 surface_status={bundle.get('status') or ''} parent={bundle.get('parent_uid') or ''} total_jobs={len(rows)}")
        for title, section_rows in (
            ("Active", active[: args.limit]),
            ("Next Runnable", runnable[: args.limit]),
            ("Blocked By Dependencies", blocked[: args.limit]),
            ("Retired", retired[: args.limit]),
        ):
            print(f"\n{title} ({len(section_rows)})")
            if not section_rows:
                print("  - none")
                continue
            for row in section_rows:
                print(
                    f"  - {row.get('child_uid') or ''} [{row.get('node_kind') or ''}] "
                    f"decision={row.get('decision') or ''} gate={row.get('gate') or ''} reason={row.get('reason') or ''}"
                )
                print(f"    title={_trim(str(row.get('title') or ''))}")
        return 0

    rows = sched.list_jobs()
    by_id = {str(r.get("job_id") or ""): r for r in rows}

    shaped = [_as_row(r, by_id) for r in rows]
    if args.binding_id:
        needle = str(args.binding_id).strip()
        shaped = [r for r in shaped if r["binding"].endswith(f":{needle}")]

    active = [r for r in shaped if r["active_run_id"]]
    active.sort(key=lambda r: r["job_id"])

    runnable = [r for r in shaped if (not r["active_run_id"]) and r["enabled"] and r["due_eligible"]]
    runnable.sort(key=lambda r: (r["due_utc"], r["job_id"]))

    blocked = [
        r
        for r in shaped
        if (not r["active_run_id"])
        and r["enabled"]
        and (not r["due_eligible"])
        and (r["deps_not_ok"] or r["deps_missing"])
    ]
    blocked.sort(key=lambda r: (r["due_utc"], r["job_id"]))

    disabled = [r for r in shaped if not r["enabled"]]
    disabled.sort(key=lambda r: r["job_id"])

    if args.json:
        out = {
            "active": active[: args.limit],
            "next_runnable": runnable[: args.limit],
            "blocked_by_dependencies": blocked[: args.limit],
            "disabled": disabled[: args.limit],
            "total_jobs": len(shaped),
        }
        print(json.dumps(out, indent=2, ensure_ascii=False))
        return 0

    print("Next Pipeline Jobs")
    print(f"total_jobs={len(shaped)}")
    if args.binding_id:
        print(f"binding_filter={args.binding_id}")
    _print_section("Active", active[: args.limit])
    _print_section("Next Runnable", runnable[: args.limit])
    _print_section("Blocked By Dependencies", blocked[: args.limit])
    _print_section("Disabled", disabled[: args.limit])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
