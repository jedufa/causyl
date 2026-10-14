from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from .artifact_registry import ArtifactRegistry
from .core import PipelineError, artifact_metadata, save_json_atomic, utc_now_iso
from .job_state import JobStateStore
from .planning import make_decision_artifact
from .schemas import validate_operator_decision_payload


def persist_operator_decision(
    *,
    job_dir: str | Path,
    run_id: str,
    config: Dict[str, Any],
    decision: str,
    decision_reason: str,
    requested_revisions: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    state_store = JobStateStore(job_path)
    registry = ArtifactRegistry(job_path, schema_version=str(config.get("pipeline", {}).get("schema_version")), contract_version=str(config.get("pipeline", {}).get("contract_version")))
    state = state_store.load()
    run = dict(state.get("runs", {}).get(run_id) or {})
    if not run:
        raise PipelineError("unknown_run", "run_id not present in job_state", details={"run_id": run_id}, exit_code=3)
    review_artifact_id = str(run.get("review", {}).get("planning_review_artifact_id") or "").strip()
    if not review_artifact_id:
        raise PipelineError("missing_planning_review", "planning review artifact must exist before operator decision", details={"run_id": run_id}, exit_code=3)
    patch_plan_ref = registry.lookup_latest(run_id=run_id, stage="script_7_patch_plan", role="stage_output")
    payload = make_decision_artifact(str(state["job_id"]), str(run_id), decision, decision_reason, patch_plan_ref["artifact_id"], review_artifact_id, requested_revisions=requested_revisions)
    payload["result"]["timestamp"] = utc_now_iso()
    validate_operator_decision_payload(payload)
    run_dir = job_path / "runs" / str(run_id)
    target = run_dir / "07_operator_decision.json"
    save_json_atomic(target, payload)
    record = registry.register(stage="operator_review", run_id=run_id, branch_id=str(run["branch_id"]), file_path=target, metadata=artifact_metadata(config, stage_key="operator_review"), role="operator_decision")
    state_store.record_operator_decision(run_id=run_id, artifact_id=record["artifact_id"], decision=payload["result"]["decision"], decision_reason=payload["result"]["decision_reason"], requested_revisions=list(payload["result"].get("requested_revisions") or []))
    return {"decision_artifact_id": record["artifact_id"], "file_path": str(target), "decision": payload["result"]["decision"]}
