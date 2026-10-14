from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import Any, Dict, List, Optional

from infra.pipeline.core import build_envelope, emit_pipeline_error, load_json, load_pipeline_config, save_json_atomic, utc_now_iso
from infra.pipeline.decision_trace import build_task_classifier_decision_trace
from infra.pipeline.schemas import validate_stage_input
from infra.pipeline.task_classification_rules import build_task_profile, task_profile_confidence
from infra.pipeline.v2_foundation import ensure_intake_assessment

STAGE_KEY = "script_2_task_classifier"


def classify(
    job_contract: Dict[str, Any],
    *,
    intake_assessment: Optional[Dict[str, Any]] = None,
    repo_root: str | Path = ".",
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    intake = ensure_intake_assessment(intake_assessment)
    task_profile = build_task_profile(
        job_contract,
        intake_assessment=intake,
        repo_root=repo_root,
        config=config,
    )
    task_profile["confidence"] = task_profile_confidence(task_profile)
    result = {
        "intake_assessment": intake,
        "task_profile": task_profile,
        "job_contract": job_contract,
    }
    result["decision_trace"] = build_task_classifier_decision_trace(result)
    return result


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MVP-1 deterministic task classifier")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        env = validate_stage_input(payload, ["job_contract"])
        result = classify(
            dict(env.result["job_contract"]),
            intake_assessment=dict(env.result.get("intake_assessment") or {}),
            repo_root=args.repo_root,
            config=config,
        )
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
