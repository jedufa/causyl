#!/usr/bin/env python3
"""Verify contract/scaffold/discovery seam rules for one experiment."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments_shared.experiment_scaffold_seams import verify_experiment_contract_scaffold_seams


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify experiment contract/scaffold seam rules")
    parser.add_argument("--project-id", required=True)
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = verify_experiment_contract_scaffold_seams(str(args.project_id).strip(), repo_root=REPO_ROOT)
    except Exception as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True) + "\n")
        return 2
    stream = sys.stdout if result.get("ok") else sys.stderr
    stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
