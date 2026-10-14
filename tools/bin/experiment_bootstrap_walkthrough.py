#!/usr/bin/env python3
"""Run a deterministic experiment bootstrap walkthrough."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments_shared.experiment_scaffold_seams import build_experiment_bootstrap_walkthrough


def _parse_args(argv: List[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run an experiment bootstrap walkthrough")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--display-name", default="")
    parser.add_argument("--owner", default="")
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--family-tag", action="append", default=[])
    parser.add_argument("--default-profile", default="starter")
    parser.add_argument("--analysis-recipe", default="summary_report")
    parser.add_argument("--required-context-bundle", default="lab_core_v1")
    parser.add_argument("--resource-class", default="cpu_light")
    parser.add_argument("--external-data-root", default="")
    parser.add_argument("--create", action="store_true", help="Create the scaffold before running the walkthrough")
    parser.add_argument("--dry-run", action="store_true", help="Plan the create step instead of writing files")
    parser.add_argument("--collision-policy", default="fail", choices=["fail", "overwrite_generated"])
    parser.add_argument("--no-manifest-compatibility", action="store_true")
    return parser.parse_args(argv)


def main(argv: List[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        result = build_experiment_bootstrap_walkthrough(
            str(args.project_id).strip(),
            display_name=str(args.display_name or "").strip(),
            owner=str(args.owner or "").strip(),
            tags=list(args.tag or []),
            family_tags=list(args.family_tag or []),
            default_profile=str(args.default_profile or "").strip(),
            analysis_recipe=str(args.analysis_recipe or "").strip(),
            required_context_bundle=str(args.required_context_bundle or "").strip(),
            resource_class=str(args.resource_class or "").strip(),
            external_data_root=str(args.external_data_root or "").strip(),
            include_manifest_compatibility=not bool(args.no_manifest_compatibility),
            collision_policy=str(args.collision_policy or "fail").strip(),
            create=bool(args.create),
            dry_run=bool(args.dry_run),
            repo_root=REPO_ROOT,
        )
    except Exception as exc:
        sys.stderr.write(json.dumps({"ok": False, "error": str(exc)}, indent=2, sort_keys=True) + "\n")
        return 2
    stream = sys.stdout if result.get("ok") else sys.stderr
    stream.write(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    raise SystemExit(main())
