from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .artifact_registry import ArtifactRegistry
from .core import STAGE_FILE_SPECS, load_json, resolve_stage_io, save_json_atomic, save_text_atomic

STAGE_LABELS = {
    "script_1_job_intake": "Job intake",
    "script_2_task_classifier": "Task classification",
    "script_3_search_plan": "Search plan",
    "script_4_repo_retriever": "Repo retrieval",
    "script_5_context_ranker": "Context ranking",
    "script_6_context_bundle": "Context bundling",
    "script_7_patch_plan": "Patch planning",
    "script_8_patch_generate": "Patch generation",
    "script_9_artifact_validate": "Artifact validation",
    "script_10_patch_apply": "Patch apply",
    "script_11_verify_static": "Static verification",
    "script_12_verify_runtime": "Runtime verification",
    "script_13_contract_verify": "Contract verification",
    "script_14_repair_loop": "Repair decision",
    "script_15_finalize_job": "Finalization",
    "script_16_job_report": "Job report",
}


def _safe_load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return load_json(path)
    except Exception:
        return {}


def _string_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    seen = set()
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        out.append(text)
        seen.add(text)
    return out


def load_stage_envelopes(job_dir: str | Path, run_id: str) -> Dict[str, Dict[str, Any]]:
    job_path = Path(job_dir).resolve()
    run_path = job_path / "runs" / str(run_id)
    out: Dict[str, Dict[str, Any]] = {}
    for stage_key, spec in STAGE_FILE_SPECS.items():
        payload = _safe_load_json(run_path / spec.output_name)
        if payload:
            out[stage_key] = payload
    return out


def extract_stage_snapshot(job_dir: str | Path, run_id: str) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    envelopes = load_stage_envelopes(job_path, run_id)
    stage_result = {stage: dict(payload.get("result") or {}) for stage, payload in envelopes.items()}
    ranked = stage_result.get("script_5_context_ranker", {})
    bundle = stage_result.get("script_6_context_bundle", {})
    patch = stage_result.get("script_7_patch_plan", {})
    patch_generation = stage_result.get("script_8_patch_generate", {})
    artifact_validation = stage_result.get("script_9_artifact_validate", {})
    patch_apply = stage_result.get("script_10_patch_apply", {})
    static_verification = stage_result.get("script_11_verify_static", {})
    runtime_verification = stage_result.get("script_12_verify_runtime", {})
    contract_verification = stage_result.get("script_13_contract_verify", {})
    repair_decision = stage_result.get("script_14_repair_loop", {})
    finalization = stage_result.get("script_15_finalize_job", {})
    job_report = stage_result.get("script_16_job_report", {})
    retrieval = stage_result.get("script_4_repo_retriever", {})
    search = stage_result.get("script_3_search_plan", {})
    classify = stage_result.get("script_2_task_classifier", {})
    execution_contract = _safe_load_json(job_path / "runs" / str(run_id) / "07_execution_contract.json")
    return {
        "classification": dict(classify.get("task_profile") or {}),
        "search_plan": dict(search.get("search_plan") or {}),
        "retrieval": dict(retrieval.get("retrieval") or {}),
        "ranking": dict(ranked.get("ranked_context") or {}),
        "bundle": dict(bundle.get("context_bundle") or {}),
        "patch_plan": dict(patch.get("patch_plan") or {}),
        "patch_artifact": dict(patch_generation.get("patch_artifact") or {}),
        "validated_artifact": dict(artifact_validation.get("validated_artifact") or {}),
        "apply_result": dict(patch_apply.get("apply_result") or {}),
        "static_verification": dict(static_verification.get("static_verification") or {}),
        "runtime_verification": dict(runtime_verification.get("runtime_verification") or {}),
        "contract_verification": dict(contract_verification.get("contract_verification") or {}),
        "repair_decision": dict(repair_decision.get("repair_decision") or {}),
        "finalization": dict(finalization.get("finalization") or {}),
        "job_report": dict(job_report.get("job_report") or {}),
        "execution_contract": dict(execution_contract.get("execution_contract") or {}),
        "job_contract": dict(stage_result.get("script_1_job_intake", {}).get("job_contract") or {}),
        "intake_assessment": dict(stage_result.get("script_1_job_intake", {}).get("intake_assessment") or {}),
    }


def build_run_summary(*, job_dir: str | Path, run_id: str, config: Mapping[str, Any], prompt_text: str = "", status: Optional[str] = None) -> Dict[str, Any]:
    job_path = Path(job_dir).resolve()
    state = _safe_load_json(job_path / "job_state.json")
    registry = ArtifactRegistry(job_path, schema_version=str(config.get("pipeline", {}).get("schema_version")), contract_version=str(config.get("pipeline", {}).get("contract_version")))
    envelopes = load_stage_envelopes(job_path, run_id)
    snapshot = extract_stage_snapshot(job_path, run_id)
    ranking = snapshot.get("ranking") or {}
    retrieval = snapshot.get("retrieval") or {}
    bundle = snapshot.get("bundle") or {}
    patch_plan = snapshot.get("patch_plan") or {}
    repair_decision = snapshot.get("repair_decision") or {}
    finalization = snapshot.get("finalization") or {}
    job_report = snapshot.get("job_report") or {}
    controller = dict(state.get("controller") or {})
    selected_files = [str(entry.get("path") or "") for entry in list(ranking.get("selected_files") or [])]
    implementation_targets = [str(entry.get("path") or "") for entry in list(ranking.get("implementation_targets") or [])]
    validation_targets = [str(entry.get("path") or "") for entry in list(ranking.get("validation_targets") or [])]
    reference_context = [str(entry.get("path") or "") for entry in list(ranking.get("reference_context") or [])]
    editable_targets = [str(entry.get("path") or "") for entry in list(bundle.get("editable_targets") or [])]
    bundled_validation_targets = [str(entry.get("path") or "") for entry in list(bundle.get("validation_targets") or [])]
    read_only_context = [str(entry.get("path") or "") for entry in list(bundle.get("read_only_context") or [])]
    stage_timings: List[Dict[str, Any]] = []
    for stage_key, spec in STAGE_FILE_SPECS.items():
        io = resolve_stage_io(job_path, run_id, stage_key)
        envelope = envelopes.get(stage_key) or {}
        exec_log = _safe_load_json(io.log_path)
        output_path = io.output_path
        if not envelope and not exec_log:
            continue
        stage_timings.append({
            "stage": stage_key,
            "label": STAGE_LABELS.get(stage_key, stage_key),
            "stage_duration_ms": int(envelope.get("duration_ms") or 0),
            "subprocess_duration_ms": int(exec_log.get("result", {}).get("duration_ms") or 0),
            "output_path": str(output_path) if output_path.exists() else None,
            "log_path": str(io.log_path),
        })
    total_elapsed_ms = sum(int(item.get("subprocess_duration_ms") or item.get("stage_duration_ms") or 0) for item in stage_timings)
    review_json = job_path / "runs" / str(run_id) / "07_planning_review.json"
    review_md = job_path / "runs" / str(run_id) / "07_planning_review.md"
    decision_json = job_path / "runs" / str(run_id) / "07_operator_decision.json"
    patch_plan_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_7_patch_plan"].output_name
    execution_contract_path = job_path / "runs" / str(run_id) / "07_execution_contract.json"
    patch_artifact_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_8_patch_generate"].output_name
    validated_artifact_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_9_artifact_validate"].output_name
    apply_result_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_10_patch_apply"].output_name
    static_verification_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_11_verify_static"].output_name
    runtime_verification_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_12_verify_runtime"].output_name
    contract_verification_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_13_contract_verify"].output_name
    repair_decision_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_14_repair_loop"].output_name
    finalization_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_15_finalize_job"].output_name
    job_report_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_16_job_report"].output_name
    execution_attempt_scaffold_path = job_path / "runs" / str(run_id) / "08_execution_attempt_scaffold.json"
    execution_trace_path = job_path / "runs" / str(run_id) / "execution_trace.json"
    user_progress_path = job_path / "job_progress.json"
    bundle_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_6_context_bundle"].output_name
    retrieval_path = job_path / "runs" / str(run_id) / STAGE_FILE_SPECS["script_4_repo_retriever"].output_name
    remediation_summary = _string_list(
        list(finalization.get("applied_remediation_summary") or [])
        + list(job_report.get("remediation_summary") or [])
        + list(repair_decision.get("auto_remediation_candidates") or [])
    )
    degraded_reporting_notes = _string_list(
        list(job_report.get("degraded_sections") or [])
        + list(job_report.get("missing_optional_inputs") or [])
    )
    provenance_refs = _string_list(job_report.get("artifact_refs") or [])
    operator_action_required = bool(
        repair_decision.get("operator_decision_required")
        or job_report.get("operator_action_needed")
        or str(finalization.get("final_status") or "").strip().lower() == "needs_review"
    )
    return {
        "job_id": str(state.get("job_id") or job_path.name),
        "run_id": str(run_id),
        "status": status or str(state.get("runs", {}).get(run_id, {}).get("status") or state.get("status") or "UNKNOWN"),
        "job_dir": str(job_path),
        "prompt_text": prompt_text,
        "selected_files": selected_files,
        "implementation_targets": implementation_targets,
        "validation_targets": validation_targets,
        "reference_context": reference_context,
        "editable_targets": editable_targets,
        "bundled_validation_targets": bundled_validation_targets,
        "read_only_context": read_only_context,
        "selected_modify_files": [str(entry.get("path") or "") for entry in list(patch_plan.get("modify_files") or [])],
        "repair_decision_summary": {
            "retry_mode": repair_decision.get("retry_mode"),
            "retry_authorization_state": repair_decision.get("retry_authorization_state"),
            "should_retry": repair_decision.get("should_retry"),
            "blocking_reason_codes": _string_list(repair_decision.get("blocking_reason_codes") or []),
        },
        "finalization_state": {
            "final_status": finalization.get("final_status"),
            "review_reason_codes": _string_list(finalization.get("review_reason_codes") or []),
            "recommended_action": finalization.get("recommended_action"),
            "recommended_first_artifact": finalization.get("recommended_first_artifact"),
        },
        "operator_action_required": operator_action_required,
        "controller": {
            "best_run_id": controller.get("best_run_id"),
            "best_progress_score": controller.get("best_progress_score"),
            "stop_reason": dict(controller.get("stop_reason") or {}),
            "recommended_next_step": controller.get("recommended_next_step"),
            "totals": dict(controller.get("totals") or {}),
        },
        "remediation_summary": remediation_summary,
        "provenance_refs": provenance_refs,
        "degraded_reporting_notes": degraded_reporting_notes,
        "stage_timings": stage_timings,
        "elapsed_time_ms": total_elapsed_ms,
        "artifacts": {
            "retrieval_path": str(retrieval_path) if retrieval_path.exists() else None,
            "bundle_path": str(bundle_path) if bundle_path.exists() else None,
            "patch_plan_path": str(patch_plan_path) if patch_plan_path.exists() else None,
            "execution_contract_path": str(execution_contract_path) if execution_contract_path.exists() else None,
            "patch_artifact_path": str(patch_artifact_path) if patch_artifact_path.exists() else None,
            "validated_artifact_path": str(validated_artifact_path) if validated_artifact_path.exists() else None,
            "apply_result_path": str(apply_result_path) if apply_result_path.exists() else None,
            "static_verification_path": str(static_verification_path) if static_verification_path.exists() else None,
            "runtime_verification_path": str(runtime_verification_path) if runtime_verification_path.exists() else None,
            "contract_verification_path": str(contract_verification_path) if contract_verification_path.exists() else None,
            "repair_decision_path": str(repair_decision_path) if repair_decision_path.exists() else None,
            "finalization_path": str(finalization_path) if finalization_path.exists() else None,
            "job_report_path": str(job_report_path) if job_report_path.exists() else None,
            "execution_attempt_scaffold_path": str(execution_attempt_scaffold_path) if execution_attempt_scaffold_path.exists() else None,
            "execution_trace_path": str(execution_trace_path) if execution_trace_path.exists() else None,
            "user_progress_path": str(user_progress_path) if user_progress_path.exists() else None,
            "review_json_path": str(review_json) if review_json.exists() else None,
            "review_markdown_path": str(review_md) if review_md.exists() else None,
            "operator_decision_path": str(decision_json) if decision_json.exists() else None,
        },
        "retrieval_provenance": {
            **dict(retrieval.get("retrieval_provenance") or {}),
            **dict(ranking.get("semantic_ranking") or {}),
            "semantic_impact": dict(ranking.get("semantic_impact") or {}),
        },
        "effective_config": {
            "semantic": {
                **dict(retrieval.get("semantic", {}).get("requested") or {}),
                **{f"effective_{k}": v for k, v in dict(retrieval.get("semantic", {}).get("effective") or {}).items()},
                **dict(retrieval.get("semantic", {}).get("runtime") or {}),
            }
        },
        "pipeline_snapshot": snapshot,
        "artifact_registry_entries": registry.lookup_by_run_id(run_id),
    }


def render_run_summary_markdown(summary: Mapping[str, Any]) -> str:
    lines = [
        f"# Full Auto Run Summary: {summary.get('job_id')}",
        "",
        f"- Run ID: `{summary.get('run_id')}`",
        f"- Status: `{summary.get('status')}`",
        f"- Elapsed: `{summary.get('elapsed_time_ms')}` ms",
        "",
        "## Selected Files",
    ]
    for path in list(summary.get("selected_files") or []):
        lines.append(f"- `{path}`")
    if summary.get("implementation_targets"):
        lines.extend(["", "## Implementation Targets"])
        for path in list(summary.get("implementation_targets") or []):
            lines.append(f"- `{path}`")
    if summary.get("selected_modify_files"):
        lines.extend(["", "## Planned Modify Files"])
        for path in list(summary.get("selected_modify_files") or []):
            lines.append(f"- `{path}`")
    repair = dict(summary.get("repair_decision_summary") or {})
    finalization = dict(summary.get("finalization_state") or {})
    lines.extend([
        "",
        "## Decision Tail",
        f"- Repair mode: `{repair.get('retry_mode')}`",
        f"- Retry authorization: `{repair.get('retry_authorization_state')}`",
        f"- Blocking reason codes: `{', '.join(list(repair.get('blocking_reason_codes') or [])) or 'none'}`",
        f"- Final status: `{finalization.get('final_status')}`",
        f"- Operator action required: `{bool(summary.get('operator_action_required'))}`",
    ])
    if summary.get("remediation_summary"):
        lines.extend(["", "## Remediation Summary"])
        for item in list(summary.get("remediation_summary") or []):
            lines.append(f"- `{item}`")
    if summary.get("provenance_refs"):
        lines.extend(["", "## Provenance Refs"])
        for item in list(summary.get("provenance_refs") or []):
            lines.append(f"- `{item}`")
    if summary.get("degraded_reporting_notes"):
        lines.extend(["", "## Degraded Reporting Notes"])
        for item in list(summary.get("degraded_reporting_notes") or []):
            lines.append(f"- `{item}`")
    lines.extend(["", "## Stage Timings"])
    for stage in list(summary.get("stage_timings") or []):
        lines.append(f"- `{stage.get('stage')}` {stage.get('label')}: stage={stage.get('stage_duration_ms')} ms, subprocess={stage.get('subprocess_duration_ms')} ms")
    provenance = dict(summary.get("retrieval_provenance") or {})
    lines.extend([
        "",
        "## Retrieval Provenance",
        f"- Strategy: `{provenance.get('strategy')}`",
        f"- Semantic status: `{provenance.get('status') or provenance.get('semantic_status')}`",
        f"- Requested mode: `{provenance.get('requested_mode')}`",
        f"- Effective mode: `{provenance.get('effective_mode')}`",
        f"- Requested provider: `{provenance.get('requested_provider')}`",
        f"- Effective provider: `{provenance.get('effective_provider')}`",
        f"- Requested model: `{provenance.get('requested_model')}`",
        f"- Effective model: `{provenance.get('effective_model')}`",
        f"- Disabled reason: `{provenance.get('disabled_reason')}`",
    ])
    impact = dict(provenance.get("semantic_impact") or {})
    if impact:
        lines.extend([
            "",
            "## Semantic Impact",
            f"- Changed selected files: `{impact.get('changed_selected_files')}`",
            f"- Changed ranking order: `{impact.get('changed_ranking_order')}`",
            f"- Semantic-only candidate count: `{impact.get('semantic_only_candidate_count')}`",
            f"- Semantic-only selected count: `{impact.get('semantic_only_selected_count')}`",
        ])
    lines.extend(["", "## Artifact Paths"])
    for key, value in dict(summary.get("artifacts") or {}).items():
        if value:
            lines.append(f"- {key}: `{value}`")
    return "\n".join(lines).rstrip() + "\n"


def persist_run_summary(*, job_dir: str | Path, summary: Mapping[str, Any], file_name: str = "full_auto_summary.json", markdown_name: str = "full_auto_summary.md") -> Dict[str, str]:
    job_path = Path(job_dir).resolve()
    json_path = job_path / file_name
    md_path = job_path / markdown_name
    save_json_atomic(json_path, dict(summary))
    save_text_atomic(md_path, render_run_summary_markdown(summary))
    return {"json_path": str(json_path), "markdown_path": str(md_path)}
