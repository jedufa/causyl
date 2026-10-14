from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import List, Optional

from infra.pipeline.core import build_envelope, emit_pipeline_error, load_json, load_pipeline_config, save_json_atomic, utc_now_iso
from infra.pipeline.decision_trace import build_context_ranker_decision_trace
from infra.pipeline.planning import rank_context
from infra.pipeline.schemas import validate_retrieval_result, validate_stage_input

STAGE_KEY = "script_5_context_ranker"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MVP-2 deterministic context ranker")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        env = validate_stage_input(payload, ["job_contract", "task_profile", "search_plan", "retrieval"])
        validate_retrieval_result(env.result)
        result = rank_context(dict(env.result), config)
        if env.result.get("retry_contract") is not None:
            result["retry_contract"] = dict(env.result.get("retry_contract") or {})
        result["decision_trace"] = build_context_ranker_decision_trace(result)
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=str(env.job_id),
            run_id=str(env.run_id),
            input_payload=payload,
            result=result,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(args.output, envelope)
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
