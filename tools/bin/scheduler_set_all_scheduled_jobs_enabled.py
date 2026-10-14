#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Sequence

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Bulk-enable or bulk-disable scheduled jobs.")
    parser.add_argument("--enabled", required=True, choices=("true", "false"))
    parser.add_argument("--prefix", default="", help="Optional job_id prefix filter.")
    parser.add_argument("--dry-run", action="store_true", help="Report changes without writing them.")
    parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = _parse_args(argv)
        sched_backend.require_legacy_for_mutation("bulk enable or disable scheduled jobs", backend=args.backend or None)
        enabled = args.enabled == "true"
        prefix = str(args.prefix or "").strip()
        changed: list[str] = []
        untouched: list[str] = []
        for path in sorted(sched.jobs_dir().glob("*.json")):
            spec = sched._read_json(path)
            if not spec:
                continue
            job_id = str(spec.get("job_id") or path.stem)
            if prefix and not job_id.startswith(prefix):
                untouched.append(job_id)
                continue
            current = bool(spec.get("enabled", True))
            if current == enabled:
                untouched.append(job_id)
                continue
            changed.append(job_id)
            if not args.dry_run:
                sched.set_job_enabled(job_id, enabled)
        payload = {
            "ok": True,
            "enabled": enabled,
            "prefix": prefix,
            "dry_run": bool(args.dry_run),
            "changed_count": len(changed),
            "changed_job_ids": changed,
            "untouched_count": len(untouched),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
