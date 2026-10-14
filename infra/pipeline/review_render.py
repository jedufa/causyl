from __future__ import annotations

from pathlib import Path
from typing import Dict, Any

from .artifact_registry import ArtifactRegistry
from .core import PipelineError, artifact_metadata, load_json, save_json_atomic, save_text_atomic
from .job_state import JobStateStore
from .planning import render_planning_review
from .schemas import validate_context_bundle_result, validate_patch_plan_result, validate_planning_review_payload, validate_ranked_context_result


def render_review(*, job_dir: str | Path, run_id: str, config: Dict[str, Any]) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    state_store = JobStateStore(job_path)
    registry = ArtifactRegistry(job_path, schema_version=str(config.get("pipeline", {}).get("schema_version")), contract_version=str(config.get("pipeline", {}).get("contract_version")))
    state = state_store.load()
    if str(state.get("active_head_run_id")) != str(run_id) and str(run_id) not in state.get("runs", {}):
        raise PipelineError("unknown_run", "run_id not present in job_state", details={"run_id": run_id}, exit_code=3)
    patch_plan_ref = registry.lookup_latest(run_id=run_id, stage="script_7_patch_plan", role="stage_output")
    bundle_ref = registry.lookup_latest(run_id=run_id, stage="script_6_context_bundle", role="stage_output")
    ranked_ref = registry.lookup_latest(run_id=run_id, stage="script_5_context_ranker", role="stage_output")
    patch_plan_payload = load_json(job_path / patch_plan_ref["file_path"])
    bundle_payload = load_json(job_path / bundle_ref["file_path"])
    ranked_payload = load_json(job_path / ranked_ref["file_path"])
    validate_patch_plan_result(patch_plan_payload["result"])
    validate_context_bundle_result(bundle_payload["result"])
    validate_ranked_context_result(ranked_payload["result"])
    review_json, review_md = render_planning_review(str(state["job_id"]), str(run_id), patch_plan_payload["result"], bundle_payload["result"], ranked_payload["result"])
    validate_planning_review_payload(review_json)
    run_dir = job_path / "runs" / str(run_id)
    json_path = run_dir / "07_planning_review.json"
    md_path = run_dir / "07_planning_review.md"
    save_json_atomic(json_path, review_json)
    save_text_atomic(md_path, review_md)
    review_record = registry.register(stage="operator_review", run_id=run_id, branch_id=str(state["runs"][run_id]["branch_id"]), file_path=json_path, metadata=artifact_metadata(config, stage_key="operator_review"), role="planning_review")
    registry.register(stage="operator_review", run_id=run_id, branch_id=str(state["runs"][run_id]["branch_id"]), file_path=md_path, metadata=artifact_metadata(config, stage_key="operator_review"), role="planning_review_markdown")
    state_store.record_planning_review(run_id=run_id, artifact_id=review_record["artifact_id"])
    return {
        "planning_review_artifact_id": review_record["artifact_id"],
        "planning_review_json": str(json_path),
        "planning_review_markdown": str(md_path),
        "patch_plan_artifact_id": patch_plan_ref["artifact_id"],
    }
