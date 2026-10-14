#!/usr/bin/env python3
"""List or resolve scaffold-registered experiments."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments_shared import EXPERIMENT_DISCOVERY_REGISTRY_PATH
from experiments_shared import list_registered_experiments
from experiments_shared import resolve_registered_experiment


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List scaffold-registered experiments")
    parser.add_argument("--project-id", default="")
    parser.add_argument("--no-verify-paths", action="store_true")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    verify = not bool(args.no_verify_paths)
    if str(args.project_id or "").strip():
        result = resolve_registered_experiment(
            str(args.project_id).strip(),
            repo_root=REPO_ROOT,
            verify_paths=verify,
        )
        result.setdefault("registry_path", EXPERIMENT_DISCOVERY_REGISTRY_PATH)
        stream = sys.stdout if result.get("ok") else sys.stderr
        stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
        return 0 if result.get("ok") else 2
    rows = list_registered_experiments(repo_root=REPO_ROOT, verify_paths=verify)
    sys.stdout.write(
        json.dumps(
            {
                "ok": True,
                "registry_path": EXPERIMENT_DISCOVERY_REGISTRY_PATH,
                "experiments": rows,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
