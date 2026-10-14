#!/usr/bin/env python3
"""Show the next best PIS items to work on."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger.ledger_manager import ChangeLedger


EXCLUDED_STATUSES = {"verified", "done", "completed", "deprecated", "superseded", "deferred"}


def _sort_key(row: Dict[str, Any]) -> tuple:
    prio = row.get("execution_priority")
    prio_num = int(prio) if isinstance(prio, int) else 10**9
    return (
        prio_num,
        str(row.get("updated_utc") or ""),
        str(row.get("uid") or ""),
    )


def next_pises(*, repo_root: Path, limit: int = 10, project_id: str = "") -> List[Dict[str, Any]]:
    ledger = ChangeLedger(repo_root=repo_root)
    rows = ledger.list_items(limit=5000)
    out: List[Dict[str, Any]] = []
    for row in rows:
        if str(row.get("status") or "").strip().lower() in EXCLUDED_STATUSES:
            continue
        if project_id and str(row.get("project_id") or "") != project_id:
            continue
        out.append(row)
    out.sort(key=_sort_key)
    return out[: max(1, min(int(limit or 10), 200))]


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Show the next best PIS items to execute.")
    ap.add_argument("--limit", type=int, default=10, help="Number of PIS items to show (default: 10).")
    ap.add_argument("--project-id", default="", help="Optional project filter.")
    ap.add_argument("--json", action="store_true", help="Emit JSON.")
    args = ap.parse_args(argv)

    rows = next_pises(repo_root=REPO_ROOT, limit=int(args.limit), project_id=str(args.project_id or "").strip())
    if args.json:
        print(json.dumps({"rows": rows}, indent=2, sort_keys=True, ensure_ascii=False))
        return 0
    for idx, row in enumerate(rows, start=1):
        line = (
            f"{idx}. {row['uid']} | {row['title']} | status={row['status']} | "
            f"prio={row.get('execution_priority')}"
        )
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
