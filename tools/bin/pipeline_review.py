#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.pipeline.core import emit_pipeline_error, load_pipeline_config
from infra.pipeline.review_render import render_review


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Render MVP-2 planning review artifacts")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--job-id", required=True)
    parser.add_argument("--run-id", default="")
    args = parser.parse_args(argv)
    try:
        config = load_pipeline_config(repo_root=args.repo_root)
        jobs_root = Path(config["_derived"]["jobs_root"])
        job_dir = jobs_root / args.job_id
        run_id = args.run_id or str(load_json(job_dir / "job_state.json")["active_head_run_id"])
        result = render_review(job_dir=job_dir, run_id=run_id, config=config)
    except Exception as exc:
        return emit_pipeline_error(exc)
    sys.stdout.write(result["planning_review_markdown"] + "\n")
    return 0


from infra.pipeline.core import load_json

if __name__ == "__main__":
    raise SystemExit(main())
