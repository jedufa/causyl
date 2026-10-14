#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui.ledger import ledger_storage

CALLER_PATTERNS = (
    re.compile(r'glob\(f?"[^"]*\{?[A-Za-z_][A-Za-z0-9_]*\}?__\*'),
    re.compile(r'glob\(f?"[^"]*PIS-\*__\*'),
    re.compile(r'governance"\s*/\s*"ledger"\s*/\s*"items"'),
)


def _items_root(repo_root: Path) -> Path:
    return ledger_storage.ledger_patch_instruction_sheets_root(repo_root)


def _registry_cmd(repo_root: Path) -> Dict[str, Any]:
    return ledger_storage.write_uid_registry(_items_root(repo_root))


def _plan_rows(repo_root: Path) -> List[Dict[str, Any]]:
    items_root = _items_root(repo_root)
    out: List[Dict[str, Any]] = []
    for item_dir in ledger_storage.list_item_dirs(items_root):
        meta = ledger_storage.read_json(item_dir / "meta.json") or {}
        uid = str(meta.get("uid") or item_dir.name.split("__", 1)[0]).strip()
        slug = str(meta.get("slug") or item_dir.name.split("__", 1)[-1]).strip()
        target = ledger_storage.planned_item_dir(
            items_root,
            uid=uid,
            slug=slug,
            project_id=str(meta.get("project_id") or ""),
            status=str(meta.get("status") or ""),
            created_utc=str(meta.get("created_utc") or ""),
            storage_layout="status_sharded",
        )
        current_rel = str(item_dir.resolve().relative_to(items_root)).replace("\\", "/")
        target_rel = str(target.resolve().relative_to(items_root)).replace("\\", "/")
        out.append(
            {
                "uid": uid,
                "status": str(meta.get("status") or "draft"),
                "project_id": ledger_storage.normalize_project_id(meta.get("project_id")),
                "storage_bucket": ledger_storage.classify_storage_bucket(meta),
                "current_rel": current_rel,
                "target_rel": target_rel,
                "needs_move": current_rel != target_rel,
            }
        )
    out.sort(key=lambda row: (str(row.get("storage_bucket") or ""), str(row.get("uid") or "")))
    return out


def _audit_callers(repo_root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in sorted((repo_root / "tools").rglob("*.py")):
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
        for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            for pattern in CALLER_PATTERNS:
                if pattern.search(raw):
                    out.append({"path": rel, "line": line_no, "text": raw.strip()})
                    break
    return out


def _apply_migration(repo_root: Path, *, uids: List[str], move_all: bool) -> List[Dict[str, Any]]:
    rows = _plan_rows(repo_root)
    selected = {str(uid).strip() for uid in uids if str(uid).strip()}
    if not move_all and not selected:
        raise SystemExit("apply-migration requires --all or one or more --uid values")
    items_root = _items_root(repo_root)
    moved: List[Dict[str, Any]] = []
    for row in rows:
        uid = str(row.get("uid") or "")
        if not bool(row.get("needs_move")):
            continue
        if not move_all and uid not in selected:
            continue
        current = (items_root / str(row.get("current_rel") or "")).resolve()
        target = (items_root / str(row.get("target_rel") or "")).resolve()
        if target.exists():
            raise SystemExit(f"target already exists for {uid}: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(current), str(target))
        moved.append({"uid": uid, "from": str(row.get("current_rel") or ""), "to": str(row.get("target_rel") or "")})
    ledger_storage.write_uid_registry(items_root)
    return moved


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Ledger storage registry, audit, and migration helper")
    ap.add_argument("--repo-root", default=str(REPO_ROOT), help="Repo root (default: current repo)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("build-registry", help="Generate governance/ledger/patch_instruction_sheets/uid_index.json")
    sub.add_parser("audit-callers", help="List Python callers still using flat ledger layout assumptions")

    plan = sub.add_parser("plan-migration", help="Show lifecycle-sharded target paths for ledger items")
    plan.add_argument("--json", action="store_true", help="Emit JSON instead of text")

    apply_p = sub.add_parser("apply-migration", help="Move ledger items into lifecycle-sharded paths")
    apply_p.add_argument("--uid", action="append", default=[], help="Specific UID to migrate (repeatable)")
    apply_p.add_argument("--all", action="store_true", help="Migrate all items that still need moving")
    apply_p.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    return ap


def main(argv: List[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(str(args.repo_root or REPO_ROOT)).resolve()

    if args.cmd == "build-registry":
        reg = _registry_cmd(repo_root)
        print(json.dumps({"items": len(reg.get("items") or {}), "duplicates": reg.get("duplicates") or {}}, indent=2, sort_keys=True))
        return 0

    if args.cmd == "audit-callers":
        rows = _audit_callers(repo_root)
        print(json.dumps({"matches": rows, "count": len(rows)}, indent=2, sort_keys=True))
        return 0

    if args.cmd == "plan-migration":
        rows = _plan_rows(repo_root)
        if bool(args.json):
            print(json.dumps({"rows": rows}, indent=2, sort_keys=True))
            return 0
        for row in rows:
            marker = "MOVE" if bool(row.get("needs_move")) else "KEEP"
            print(f"{marker} {row['uid']} {row['current_rel']} -> {row['target_rel']}")
        return 0

    if args.cmd == "apply-migration":
        moved = _apply_migration(repo_root, uids=list(args.uid or []), move_all=bool(args.all))
        if bool(args.json):
            print(json.dumps({"moved": moved}, indent=2, sort_keys=True))
            return 0
        for row in moved:
            print(f"MOVED {row['uid']} {row['from']} -> {row['to']}")
        return 0

    raise SystemExit(f"unsupported command: {args.cmd}")


if __name__ == "__main__":
    raise SystemExit(main())
