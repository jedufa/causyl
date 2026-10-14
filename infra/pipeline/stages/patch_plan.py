from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from typing import List, Optional

from infra.pipeline.core import build_envelope, emit_pipeline_error, load_json, load_pipeline_config, save_json_atomic, utc_now_iso
from infra.pipeline.decision_trace import build_patch_plan_decision_trace
from infra.pipeline.planning import STAGE7_EXECUTION_CONTRACT_FILE, build_execution_contract, maybe_model_patch_plan
from infra.pipeline.v2_foundation import ensure_intake_assessment
from infra.pipeline.schemas import validate_context_bundle_result, validate_execution_contract_payload, validate_patch_plan_result, validate_stage_input

STAGE_KEY = "script_7_patch_plan"


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="MVP-2 patch planner")
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)
    try:
        started = utc_now_iso()
        config = load_pipeline_config(repo_root=args.repo_root, stage_key=STAGE_KEY)
        payload = load_json(args.input)
        env = validate_stage_input(payload, ["job_contract", "task_profile", "context_bundle"])
        validate_context_bundle_result(env.result)
        patch_plan, warnings = maybe_model_patch_plan(dict(env.result), config)
        result = {
            "intake_assessment": ensure_intake_assessment(dict(env.result.get("intake_assessment") or {})),
            "job_contract": dict(env.result.get("job_contract") or {}),
            "task_profile": dict(env.result.get("task_profile") or {}),
            "context_bundle": dict(env.result.get("context_bundle") or {}),
            "patch_plan": patch_plan,
        }
        if env.result.get("retry_contract") is not None:
            result["retry_contract"] = dict(env.result.get("retry_contract") or {})
        validate_patch_plan_result(result)
        execution_contract = build_execution_contract(
            {**dict(env.result), "job_id": str(env.job_id), "run_id": str(env.run_id)},
            patch_plan,
            config,
        )
        validate_execution_contract_payload(execution_contract)
        result["decision_trace"] = build_patch_plan_decision_trace(result, execution_contract=execution_contract)
        execution_contract_path = Path(args.output).resolve().parent / STAGE7_EXECUTION_CONTRACT_FILE
        save_json_atomic(execution_contract_path, execution_contract)
        envelope = build_envelope(
            stage=STAGE_KEY,
            job_id=str(env.job_id),
            run_id=str(env.run_id),
            input_payload=payload,
            result=result,
            warnings=warnings,
            tool_version=str(config.get("pipeline", {}).get("tool_version")),
            started_at_iso=started,
        )
        save_json_atomic(args.output, envelope)
        return 0
    except Exception as exc:
        return emit_pipeline_error(exc)


if __name__ == "__main__":
    raise SystemExit(main())
