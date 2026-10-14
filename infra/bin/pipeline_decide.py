#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import emit_pipeline_error, load_json, load_pipeline_config
from infra.pipeline.operator_decision import persist_operator_decision


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Persist MVP-2 operator decision")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-id", default="")
    parser.add_argument("--decision", required=True)
    parser.add_argument("--reason", default="")
    parser.add_argument("--revision", action="append", default=[])
    args = parser.parse_args(argv)
    try:
        config = load_pipeline_config(repo_root=args.repo_root)
        jobs_root = Path(config["_derived"]["jobs_root"])
        job_dir = jobs_root / args.job_id
        run_id = args.run_id or str(load_json(job_dir / "job_state.json")["active_head_run_id"])
        result = persist_operator_decision(job_dir=job_dir, run_id=run_id, config=config, decision=args.decision, decision_reason=args.reason, requested_revisions=args.revision)
    except Exception as exc:
        return emit_pipeline_error(exc)
    sys.stdout.write(result["decision"] + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
