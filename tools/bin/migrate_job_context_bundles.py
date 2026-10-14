#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from infra.controlplane import context_bundles


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate scheduled jobs to context bundle + delta refs.")
    parser.add_argument("--prefix", default="", help="Optional job_id prefix filter.")
    parser.add_argument("--bundle", default="lab_core_v1", help="Bundle id to assign.")
    parser.add_argument("--apply", action="store_true", help="Write changes (default is dry-run).")
    return parser.parse_args()


def _normalize_refs(raw: object) -> List[str]:
    if not isinstance(raw, list):
        return []
    out: List[str] = []
    for item in raw:
        txt = str(item or "").strip()
        if txt and txt not in out:
            out.append(txt)
    return out


def _migrate_job(job: Dict[str, object], *, bundle_id: str) -> bool:
    changed = False
    refs = _normalize_refs(job.get("context_refs"))
    bundle_refs = context_bundles.bundle_refs(bundle_id)

    if str(job.get("required_context_bundle") or "") != bundle_id:
        job["required_context_bundle"] = bundle_id
        changed = True

    additional = [ref for ref in refs if ref not in bundle_refs]
    if _normalize_refs(job.get("additional_context_refs")) != additional:
        job["additional_context_refs"] = additional
        changed = True

    if _normalize_refs(job.get("forbidden_context_refs")) != []:
        job["forbidden_context_refs"] = []
        changed = True

    return changed


def main() -> int:
    args = _parse_args()
    bundle_id = str(args.bundle or "").strip()
    if bundle_id not in context_bundles.CONTEXT_BUNDLES:
        raise SystemExit(f"Unknown bundle: {bundle_id}. Allowed: {', '.join(context_bundles.available_bundle_ids())}")

    inbox = Path("governance/agents/runtime/scheduled_jobs/inbox")
    if not inbox.exists():
        raise SystemExit(f"Inbox not found: {inbox}")

    changed: List[str] = []
    scanned = 0
    for path in sorted(inbox.glob("*.json")):
        try:
            obj = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(obj, dict):
            continue
        scanned += 1
        job_id = str(obj.get("job_id") or path.stem)
        if args.prefix and not job_id.startswith(args.prefix):
            continue
        if _migrate_job(obj, bundle_id=bundle_id):
            changed.append(job_id)
            if args.apply:
                path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    payload = {
        "ok": True,
        "bundle": bundle_id,
        "prefix": args.prefix,
        "apply": bool(args.apply),
        "scanned": scanned,
        "changed_count": len(changed),
        "changed_jobs_sample": changed[:30],
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
