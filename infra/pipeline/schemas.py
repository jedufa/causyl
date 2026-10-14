from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Mapping, Sequence

from .core import PipelineError


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise PipelineError("schema_validation_failed", f"{name} must be a mapping", details={"field": name}, exit_code=3)
    return value


def _require_string(value: Any, name: str) -> str:
    txt = str(value or "").strip()
    if not txt:
        raise PipelineError("schema_validation_failed", f"{name} must be a non-empty string", details={"field": name}, exit_code=3)
    return txt


def _require_list(value: Any, name: str) -> List[Any]:
    if not isinstance(value, list):
        raise PipelineError("schema_validation_failed", f"{name} must be a list", details={"field": name}, exit_code=3)
    return list(value)


def _validate_string_list(value: Any, name: str) -> List[str]:
    items = _require_list(value or [], name)
    out: List[str] = []
    for idx, item in enumerate(items):
        out.append(_require_string(item, f"{name}[{idx}]"))
    return out


def validate_verification_claim(value: Any, name: str = "verification_claim") -> Mapping[str, Any]:
    claim = _require_mapping(value, name)
    _validate_string_list(claim.get("implementation_files") or [], f"{name}.implementation_files")
    _validate_string_list(claim.get("changed_test_files") or [], f"{name}.changed_test_files")
    _validate_string_list(claim.get("behavior_claims") or [], f"{name}.behavior_claims")
    tests = _require_list(claim.get("new_or_updated_tests") or [], f"{name}.new_or_updated_tests")
    for idx, entry in enumerate(tests):
        item = _require_mapping(entry, f"{name}.new_or_updated_tests[{idx}]")
        _require_string(item.get("path"), f"{name}.new_or_updated_tests[{idx}].path")
        _require_string(item.get("name"), f"{name}.new_or_updated_tests[{idx}].name")
        if item.get("proves") is not None:
            _require_string(item.get("proves"), f"{name}.new_or_updated_tests[{idx}].proves")
    return claim


def _require_numeric_if_present(value: Any, name: str) -> None:
    if value is None:
        return
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise PipelineError("schema_validation_failed", f"{name} must be numeric when present", details={"field": name, "value": value}, exit_code=3)


def _validate_decision_trace(value: Any, name: str) -> None:
    if value is None:
        return
    items = _require_list(value, name)
    for idx, entry in enumerate(items):
        item = _require_mapping(entry, f"{name}[{idx}]")
        _require_string(item.get("stage") or item.get("kind") or "decision", f"{name}[{idx}].stage_or_kind")
        _require_string(item.get("summary"), f"{name}[{idx}].summary")
        if "evidence" in item:
            _require_list(item.get("evidence"), f"{name}[{idx}].evidence")
        _require_numeric_if_present(item.get("confidence"), f"{name}[{idx}].confidence")


def _validate_confidence(value: Any, name: str) -> None:
    if value is None:
        return
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return
    confidence = _require_mapping(value, name)
    if "score" in confidence:
        _require_numeric_if_present(confidence.get("score"), f"{name}.score")
    if "notes" in confidence:
        _require_list(confidence.get("notes"), f"{name}.notes")


def _validate_optional_phase0_fields(result: Mapping[str, Any]) -> None:
    _validate_decision_trace(result.get("decision_trace"), "result.decision_trace")
    intake_assessment = result.get("intake_assessment")
    if intake_assessment is not None:
        intake = _require_mapping(intake_assessment, "intake_assessment")
        failure_taxonomy = _require_mapping(intake.get("failure_taxonomy", {}), "intake_assessment.failure_taxonomy")
        for key in ["operator_failure_modes", "system_failure_modes", "concurrency_failure_modes", "recovery_failure_modes"]:
            if key in failure_taxonomy:
                _require_list(failure_taxonomy.get(key), f"intake_assessment.failure_taxonomy.{key}")
        if "task_families" in intake:
            _require_list(intake.get("task_families"), "intake_assessment.task_families")
        if "invariants" in intake:
            _require_list(intake.get("invariants"), "intake_assessment.invariants")
        derived_requirements = _require_mapping(intake.get("derived_requirements", {}), "intake_assessment.derived_requirements")
        for key in ["safety_requirements", "observability_requirements", "recovery_guarantees"]:
            if key in derived_requirements:
                _require_list(derived_requirements.get(key), f"intake_assessment.derived_requirements.{key}")
        _validate_confidence(intake.get("confidence"), "intake_assessment.confidence")

    task_profile_value = result.get("task_profile")
    if task_profile_value is not None:
        task_profile = _require_mapping(task_profile_value, "task_profile")
        retrieval_bias = task_profile.get("retrieval_bias")
        if retrieval_bias is not None:
            bias = _require_mapping(retrieval_bias, "task_profile.retrieval_bias")
            for key in ["include", "exclude"]:
                if key in bias:
                    _require_list(bias.get(key), f"task_profile.retrieval_bias.{key}")
        if "cross_cutting_flags" in task_profile:
            _require_list(task_profile.get("cross_cutting_flags"), "task_profile.cross_cutting_flags")
        if "task_archetype" in task_profile and task_profile.get("task_archetype") is not None:
            _require_string(task_profile.get("task_archetype"), "task_profile.task_archetype")
        if "planning_profiles" in task_profile:
            _require_mapping(task_profile.get("planning_profiles"), "task_profile.planning_profiles")
        if "output_contract_mode" in task_profile and task_profile.get("output_contract_mode") is not None:
            _require_string(task_profile.get("output_contract_mode"), "task_profile.output_contract_mode")
        _validate_confidence(task_profile.get("confidence"), "task_profile.confidence")

    search_plan_value = result.get("search_plan")
    if search_plan_value is not None:
        search_plan = _require_mapping(search_plan_value, "search_plan")
        for key in ["lexical_queries", "conceptual_queries", "negative_targets", "subsystem_hypotheses"]:
            if key in search_plan:
                _require_list(search_plan.get(key), f"search_plan.{key}")
        if "query_coverage_map" in search_plan:
            _require_mapping(search_plan.get("query_coverage_map"), "search_plan.query_coverage_map")
        _validate_confidence(search_plan.get("confidence"), "search_plan.confidence")

    retrieval_value = result.get("retrieval")
    if retrieval_value is not None:
        retrieval = _require_mapping(retrieval_value, "retrieval")
        for idx, entry in enumerate(list(retrieval.get("file_candidates") or [])):
            item = _require_mapping(entry, f"retrieval.file_candidates[{idx}]")
            if "file_role" in item and item.get("file_role") is not None:
                _require_string(item.get("file_role"), f"retrieval.file_candidates[{idx}].file_role")
            _require_numeric_if_present(item.get("task_role_fit"), f"retrieval.file_candidates[{idx}].task_role_fit")
            if "covers" in item:
                _require_list(item.get("covers"), f"retrieval.file_candidates[{idx}].covers")
            if "match_origin" in item and item.get("match_origin") is not None:
                _require_string(item.get("match_origin"), f"retrieval.file_candidates[{idx}].match_origin")
            if "candidate_intent_lane" in item and item.get("candidate_intent_lane") is not None:
                _require_string(item.get("candidate_intent_lane"), f"retrieval.file_candidates[{idx}].candidate_intent_lane")
            if "context_intent_type" in item and item.get("context_intent_type") is not None:
                _require_string(item.get("context_intent_type"), f"retrieval.file_candidates[{idx}].context_intent_type")
            if "structural_family_tags" in item:
                _require_list(item.get("structural_family_tags"), f"retrieval.file_candidates[{idx}].structural_family_tags")
            _require_numeric_if_present(item.get("coverage_score"), f"retrieval.file_candidates[{idx}].coverage_score")
            _require_numeric_if_present(item.get("superficial_match_penalty"), f"retrieval.file_candidates[{idx}].superficial_match_penalty")
            _validate_confidence(item.get("confidence"), f"retrieval.file_candidates[{idx}].confidence")

    ranked_context_value = result.get("ranked_context")
    if ranked_context_value is not None:
        ranked_context = _require_mapping(ranked_context_value, "ranked_context")
        _validate_confidence(ranked_context.get("confidence"), "ranked_context.confidence")
        for field_name in ["selected_files", "implementation_targets", "validation_targets", "reference_context"]:
            for idx, entry in enumerate(list(ranked_context.get(field_name) or [])):
                item = _require_mapping(entry, f"ranked_context.{field_name}[{idx}]")
                if "role" in item and item.get("role") is not None:
                    _require_string(item.get("role"), f"ranked_context.{field_name}[{idx}].role")
                if "file_role" in item and item.get("file_role") is not None:
                    _require_string(item.get("file_role"), f"ranked_context.{field_name}[{idx}].file_role")
                if "bundle_role" in item and item.get("bundle_role") is not None:
                    _require_string(item.get("bundle_role"), f"ranked_context.{field_name}[{idx}].bundle_role")
                if "why_not_other_roles" in item:
                    _require_list(item.get("why_not_other_roles"), f"ranked_context.{field_name}[{idx}].why_not_other_roles")
                if "secondary_lanes" in item:
                    _require_list(item.get("secondary_lanes"), f"ranked_context.{field_name}[{idx}].secondary_lanes")
                if "major_positive_signals" in item:
                    _require_list(item.get("major_positive_signals"), f"ranked_context.{field_name}[{idx}].major_positive_signals")
                if "major_penalties" in item:
                    _require_list(item.get("major_penalties"), f"ranked_context.{field_name}[{idx}].major_penalties")
                if "suppression_decisions" in item:
                    _require_list(item.get("suppression_decisions"), f"ranked_context.{field_name}[{idx}].suppression_decisions")
                if "candidate_intent_lane" in item and item.get("candidate_intent_lane") is not None:
                    _require_string(item.get("candidate_intent_lane"), f"ranked_context.{field_name}[{idx}].candidate_intent_lane")
                if "context_intent_type" in item and item.get("context_intent_type") is not None:
                    _require_string(item.get("context_intent_type"), f"ranked_context.{field_name}[{idx}].context_intent_type")
                if "selection_cap_reasons" in item:
                    _require_list(item.get("selection_cap_reasons"), f"ranked_context.{field_name}[{idx}].selection_cap_reasons")
                _require_numeric_if_present(item.get("edit_necessity_score"), f"ranked_context.{field_name}[{idx}].edit_necessity_score")
                _validate_confidence(item.get("confidence"), f"ranked_context.{field_name}[{idx}].confidence")

    context_bundle_value = result.get("context_bundle")
    if context_bundle_value is not None:
        context_bundle = _require_mapping(context_bundle_value, "context_bundle")
        if "bundle_gap_warning" in context_bundle and not isinstance(context_bundle.get("bundle_gap_warning"), bool):
            raise PipelineError("schema_validation_failed", "context_bundle.bundle_gap_warning must be boolean", details={"field": "context_bundle.bundle_gap_warning", "value": context_bundle.get("bundle_gap_warning")}, exit_code=3)
        if "bundle_gap_details" in context_bundle:
            _require_list(context_bundle.get("bundle_gap_details"), "context_bundle.bundle_gap_details")
        if "bundle_recovery_hints" in context_bundle:
            _require_list(context_bundle.get("bundle_recovery_hints"), "context_bundle.bundle_recovery_hints")
        if "coverage_validation" in context_bundle:
            _require_mapping(context_bundle.get("coverage_validation"), "context_bundle.coverage_validation")
        _validate_confidence(context_bundle.get("confidence"), "context_bundle.confidence")
        for field_name in ["files", "editable_targets", "validation_targets", "read_only_context"]:
            for idx, entry in enumerate(list(context_bundle.get(field_name) or [])):
                item = _require_mapping(entry, f"context_bundle.{field_name}[{idx}]")
                if "file_role" in item and item.get("file_role") is not None:
                    _require_string(item.get("file_role"), f"context_bundle.{field_name}[{idx}].file_role")
                if "bundle_role" in item and item.get("bundle_role") is not None:
                    _require_string(item.get("bundle_role"), f"context_bundle.{field_name}[{idx}].bundle_role")
                if "responsibility" in item:
                    _require_list(item.get("responsibility"), f"context_bundle.{field_name}[{idx}].responsibility")
                if "covers_failure_modes" in item:
                    _require_list(item.get("covers_failure_modes"), f"context_bundle.{field_name}[{idx}].covers_failure_modes")
                if "candidate_intent_lane" in item and item.get("candidate_intent_lane") is not None:
                    _require_string(item.get("candidate_intent_lane"), f"context_bundle.{field_name}[{idx}].candidate_intent_lane")
                if "context_intent_type" in item and item.get("context_intent_type") is not None:
                    _require_string(item.get("context_intent_type"), f"context_bundle.{field_name}[{idx}].context_intent_type")
                _validate_confidence(item.get("ownership_confidence"), f"context_bundle.{field_name}[{idx}].ownership_confidence")
                _validate_confidence(item.get("confidence"), f"context_bundle.{field_name}[{idx}].confidence")

    patch_plan_value = result.get("patch_plan")
    if patch_plan_value is not None:
        patch_plan = _require_mapping(patch_plan_value, "patch_plan")
        contract = patch_plan.get("cross_cutting_contract")
        if contract is not None:
            cross_cutting = _require_mapping(contract, "patch_plan.cross_cutting_contract")
            for key in ["invariants", "validation_rules", "retry_policy", "operator_safety", "observability_guarantees", "consistency_guarantees"]:
                if key in cross_cutting:
                    _require_list(cross_cutting.get(key), f"patch_plan.cross_cutting_contract.{key}")
        if "must_not_change" in patch_plan:
            _require_list(patch_plan.get("must_not_change"), "patch_plan.must_not_change")
        if "change_boundaries" in patch_plan:
            _require_list(patch_plan.get("change_boundaries"), "patch_plan.change_boundaries")
        if "quality_metrics" in patch_plan:
            _require_mapping(patch_plan.get("quality_metrics"), "patch_plan.quality_metrics")
        if "output_contract_mode" in patch_plan and patch_plan.get("output_contract_mode") is not None:
            _require_string(patch_plan.get("output_contract_mode"), "patch_plan.output_contract_mode")
        for field in ["design_execution_contract", "assessment_report_contract", "retrieval_answer_contract"]:
            if field in patch_plan:
                _require_mapping(patch_plan.get(field), f"patch_plan.{field}")
        _validate_confidence(patch_plan.get("confidence"), "patch_plan.confidence")

    if "patch_artifact" in result:
        validate_patch_artifact({"patch_artifact": result.get("patch_artifact")})
    if "validated_artifact" in result:
        validate_validated_artifact({"validated_artifact": result.get("validated_artifact")})
    if "apply_result" in result:
        validate_apply_result({"apply_result": result.get("apply_result")})


@dataclass
class EnvelopeSchema:
    stage: str
    status: str
    job_id: str
    run_id: str
    created_at: str
    duration_ms: int
    tool_version: str
    input_digest: str
    result: Dict[str, Any]
    warnings: List[str] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EnvelopeSchema":
        obj = _require_mapping(payload, "envelope")
        result = _require_mapping(obj.get("result"), "result")
        warnings = _require_list(obj.get("warnings", []), "warnings")
        errors = _require_list(obj.get("errors", []), "errors")
        duration_ms = obj.get("duration_ms")
        if not isinstance(duration_ms, int) or duration_ms < 0:
            raise PipelineError("schema_validation_failed", "duration_ms must be a non-negative int", details={"field": "duration_ms"}, exit_code=3)
        status = _require_string(obj.get("status"), "status")
        if status not in {"success", "failure"}:
            raise PipelineError("schema_validation_failed", "status must be success or failure", details={"status": status}, exit_code=3)
        return cls(
            stage=_require_string(obj.get("stage"), "stage"),
            status=status,
            job_id=_require_string(obj.get("job_id"), "job_id"),
            run_id=_require_string(obj.get("run_id"), "run_id"),
            created_at=_require_string(obj.get("created_at"), "created_at"),
            duration_ms=duration_ms,
            tool_version=_require_string(obj.get("tool_version"), "tool_version"),
            input_digest=_require_string(obj.get("input_digest"), "input_digest"),
            result=dict(result),
            warnings=[str(x) for x in warnings],
            errors=[dict(_require_mapping(x, "errors[]")) for x in errors],
        )


def validate_stage_input(payload: Mapping[str, Any], required_result_keys: Sequence[str]) -> EnvelopeSchema:
    env = EnvelopeSchema.from_dict(payload)
    missing = [key for key in required_result_keys if key not in env.result]
    if missing:
        raise PipelineError("schema_validation_failed", "required result keys missing from upstream envelope", details={"missing": missing, "stage": env.stage}, exit_code=3)
    _validate_optional_phase0_fields(env.result)
    return env


def validate_stage_output(payload: Mapping[str, Any], *, expected_stage: str, expected_job_id: str, expected_run_id: str) -> EnvelopeSchema:
    env = EnvelopeSchema.from_dict(payload)
    _validate_optional_phase0_fields(env.result)
    if "static_verification" in env.result:
        validate_static_verification_result(env.result)
    if "runtime_verification" in env.result:
        validate_runtime_verification_result(env.result)
    if "contract_verification" in env.result:
        validate_contract_verification_result(env.result)
    if "repair_decision" in env.result:
        validate_repair_decision_result(env.result)
    if "finalization" in env.result:
        validate_finalization_result(env.result)
    if "job_report" in env.result:
        validate_job_report_result(env.result)
    if env.stage != expected_stage:
        raise PipelineError("schema_validation_failed", "stage output envelope has unexpected stage", details={"expected": expected_stage, "actual": env.stage}, exit_code=3)
    if env.job_id != expected_job_id or env.run_id != expected_run_id:
        raise PipelineError("schema_validation_failed", "stage output envelope has wrong lineage", details={"expected_job_id": expected_job_id, "actual_job_id": env.job_id, "expected_run_id": expected_run_id, "actual_run_id": env.run_id}, exit_code=3)
    if env.status != "success":
        raise PipelineError("stage_output_failed", "stage output envelope reports failure", details={"stage": env.stage, "errors": env.errors}, exit_code=3)
    return env


def validate_retrieval_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    retrieval = _require_mapping(result.get("retrieval"), "retrieval")
    candidates = _require_list(retrieval.get("file_candidates"), "retrieval.file_candidates")
    if not candidates:
        raise PipelineError("schema_validation_failed", "retrieval.file_candidates must not be empty", exit_code=3)


def validate_ranked_context_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    ranked_context = _require_mapping(result.get("ranked_context"), "ranked_context")
    selected = _require_list(ranked_context.get("selected_files"), "ranked_context.selected_files")
    if not selected:
        raise PipelineError("schema_validation_failed", "ranked_context.selected_files must not be empty", exit_code=3)
    for entry in selected:
        item = _require_mapping(entry, "ranked_context.selected_files[]")
        _require_string(item.get("path"), "ranked_context.selected_files[].path")
        _require_list(item.get("reasons"), "ranked_context.selected_files[].reasons")
        snippets = _require_list(item.get("snippet_candidates"), "ranked_context.selected_files[].snippet_candidates")
        if not snippets:
            raise PipelineError("schema_validation_failed", "ranked file must contain snippet candidates", exit_code=3)
    for field_name in ["implementation_targets", "validation_targets", "reference_context"]:
        for entry in list(ranked_context.get(field_name) or []):
            item = _require_mapping(entry, f"ranked_context.{field_name}[]")
            _require_string(item.get("path"), f"ranked_context.{field_name}[].path")


def validate_context_bundle_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    context_bundle = _require_mapping(result.get("context_bundle"), "context_bundle")
    files = _require_list(context_bundle.get("files"), "context_bundle.files")
    if not files:
        raise PipelineError("schema_validation_failed", "context_bundle.files must not be empty", exit_code=3)
    metrics = _require_mapping(context_bundle.get("bundle_metrics"), "context_bundle.bundle_metrics")
    if int(metrics.get("snippet_count") or 0) <= 0:
        raise PipelineError("schema_validation_failed", "bundle_metrics.snippet_count must be positive", exit_code=3)
    for field_name in ["editable_targets", "validation_targets", "read_only_context"]:
        for entry in list(context_bundle.get(field_name) or []):
            item = _require_mapping(entry, f"context_bundle.{field_name}[]")
            _require_string(item.get("path"), f"context_bundle.{field_name}[].path")


def validate_patch_plan_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    patch_plan = _require_mapping(result.get("patch_plan"), "patch_plan")
    _require_string(patch_plan.get("objective"), "patch_plan.objective")
    modify_files = _require_list(patch_plan.get("modify_files"), "patch_plan.modify_files")
    _require_list(patch_plan.get("non_goals", []), "patch_plan.non_goals")
    _require_list(patch_plan.get("assumptions", []), "patch_plan.assumptions")
    _require_list(patch_plan.get("unresolved_uncertainties", []), "patch_plan.unresolved_uncertainties")
    _require_list(patch_plan.get("required_tests", []), "patch_plan.required_tests")
    _validate_string_list(patch_plan.get("implementation_target_files") or [], "patch_plan.implementation_target_files")
    _validate_string_list(patch_plan.get("validation_target_files") or [], "patch_plan.validation_target_files")
    if not isinstance(patch_plan.get("should_escalate"), bool):
        raise PipelineError("schema_validation_failed", "patch_plan.should_escalate must be boolean", exit_code=3)
    confidence = patch_plan.get("planning_confidence")
    if not isinstance(confidence, (int, float)):
        raise PipelineError("schema_validation_failed", "patch_plan.planning_confidence must be numeric", exit_code=3)
    for file_entry in modify_files:
        item = _require_mapping(file_entry, "patch_plan.modify_files[]")
        _require_string(item.get("path"), "patch_plan.modify_files[].path")
        _require_list(item.get("intended_modifications"), "patch_plan.modify_files[].intended_modifications")
        _require_list(item.get("rationale"), "patch_plan.modify_files[].rationale")


def validate_patch_artifact(payload: Mapping[str, Any]) -> None:
    artifact = _require_mapping(payload.get("patch_artifact"), "patch_artifact")
    artifact_type = _require_string(artifact.get("artifact_type"), "patch_artifact.artifact_type")
    if artifact_type != "unified_diff":
        raise PipelineError("schema_validation_failed", "patch_artifact.artifact_type must be unified_diff", details={"artifact_type": artifact_type}, exit_code=3)
    producer = _require_mapping(artifact.get("producer"), "patch_artifact.producer")
    _require_string(producer.get("provider"), "patch_artifact.producer.provider")
    _require_string(producer.get("model"), "patch_artifact.producer.model")
    target_files = _require_list(artifact.get("target_files"), "patch_artifact.target_files")
    if not target_files:
        raise PipelineError("schema_validation_failed", "patch_artifact.target_files must not be empty", exit_code=3)
    for idx, value in enumerate(target_files):
        _require_string(value, f"patch_artifact.target_files[{idx}]")
    _require_string(artifact.get("content"), "patch_artifact.content")


def validate_validated_artifact(payload: Mapping[str, Any]) -> None:
    artifact = _require_mapping(payload.get("validated_artifact"), "validated_artifact")
    artifact_type = _require_string(artifact.get("artifact_type"), "validated_artifact.artifact_type")
    if artifact_type != "unified_diff":
        raise PipelineError("schema_validation_failed", "validated_artifact.artifact_type must be unified_diff", details={"artifact_type": artifact_type}, exit_code=3)
    _require_string(artifact.get("normalized_content"), "validated_artifact.normalized_content")
    target_files = _require_list(artifact.get("target_files"), "validated_artifact.target_files")
    if not target_files:
        raise PipelineError("schema_validation_failed", "validated_artifact.target_files must not be empty", exit_code=3)
    for idx, value in enumerate(target_files):
        _require_string(value, f"validated_artifact.target_files[{idx}]")
    checks = _require_list(artifact.get("validation_checks"), "validated_artifact.validation_checks")
    if not checks:
        raise PipelineError("schema_validation_failed", "validated_artifact.validation_checks must not be empty", exit_code=3)
    for idx, check in enumerate(checks):
        item = _require_mapping(check, f"validated_artifact.validation_checks[{idx}]")
        _require_string(item.get("name"), f"validated_artifact.validation_checks[{idx}].name")
        status = _require_string(item.get("status"), f"validated_artifact.validation_checks[{idx}].status")
        if status not in {"passed", "failed", "skipped"}:
            raise PipelineError("schema_validation_failed", "validated_artifact.validation_checks status is invalid", details={"status": status}, exit_code=3)
        _require_string(item.get("message"), f"validated_artifact.validation_checks[{idx}].message")
    is_valid = artifact.get("is_valid")
    if not isinstance(is_valid, bool):
        raise PipelineError("schema_validation_failed", "validated_artifact.is_valid must be boolean", exit_code=3)


def validate_apply_result(payload: Mapping[str, Any]) -> None:
    result = _require_mapping(payload.get("apply_result"), "apply_result")
    status = _require_string(result.get("apply_status"), "apply_result.apply_status")
    if status not in {"applied_cleanly", "conflict", "rejected", "skipped"}:
        raise PipelineError("schema_validation_failed", "apply_result.apply_status is invalid", details={"apply_status": status}, exit_code=3)
    changed_files = _require_list(result.get("changed_files"), "apply_result.changed_files")
    for idx, value in enumerate(changed_files):
        _require_string(value, f"apply_result.changed_files[{idx}]")
    _require_string(result.get("workspace_path"), "apply_result.workspace_path")
    _require_string(result.get("logs"), "apply_result.logs")
    patch_stats = _require_mapping(result.get("patch_stats"), "apply_result.patch_stats")
    for key in ["files_changed", "insertions", "deletions"]:
        _require_numeric_if_present(patch_stats.get(key), f"apply_result.patch_stats.{key}")


def validate_patch_generate_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    result_kind = result.get("result_kind")
    if result_kind is not None:
        kind_value = _require_string(result_kind, "patch_generate_result.result_kind")
        if kind_value not in {"generation_succeeded", "generation_failed", "generation_skipped"}:
            raise PipelineError("schema_validation_failed", "patch_generate_result.result_kind is invalid", details={"result_kind": kind_value}, exit_code=3)
    status = result.get("generation_status")
    if status is not None:
        status_value = _require_string(status, "patch_generate_result.generation_status")
        if status_value not in {"not_applicable_due_to_execution_contract", "generation_failed", "generated"}:
            raise PipelineError(
                "schema_validation_failed",
                "patch_generate_result.generation_status is invalid",                details={"generation_status": status_value},
                exit_code=3,
            )
    if result.get("generation_skipped") is True:
        _require_string(result.get("generation_blocked_reason") or "generation skipped", "patch_generate_result.generation_blocked_reason")
        return
    metadata = _require_mapping(result.get("generation_metadata"), "patch_generate_result.generation_metadata")
    _require_string(metadata.get("provider"), "patch_generate_result.generation_metadata.provider")
    _require_string(metadata.get("model") or metadata.get("requested_model") or metadata.get("selected_model"), "patch_generate_result.generation_metadata.model_or_requested_model")
    _require_numeric_if_present(metadata.get("duration_ms"), "patch_generate_result.generation_metadata.duration_ms")
    _require_string(metadata.get("raw_output_path") or ".", "patch_generate_result.generation_metadata.raw_output_path")
    _require_string(metadata.get("output_format_requested"), "patch_generate_result.generation_metadata.output_format_requested")
    _require_string(metadata.get("output_format_received"), "patch_generate_result.generation_metadata.output_format_received")
    for field in [
        "requested_model", "requested_agent_tech", "selected_model", "selected_agent_tech", "reasoning_effort",
        "finish_reason", "selected_via", "execution_mode", "execution_source", "response_id", "runtime_summary",
        "fixture_path", "fixture_basename", "fixture_source_ref", "failure_class", "failure_source"
    ]:
        value = metadata.get(field)
        if value is not None:
            _require_string(value, f"patch_generate_result.generation_metadata.{field}")
    for field in ["usage_input_tokens", "usage_output_tokens", "usage_total_tokens"]:
        _require_numeric_if_present(metadata.get(field), f"patch_generate_result.generation_metadata.{field}")
    for field in ["simulation_mode", "retryable_hint"]:
        value = metadata.get(field)
        if value is not None and not isinstance(value, bool):
            raise PipelineError("schema_validation_failed", f"patch_generate_result.generation_metadata.{field} must be boolean", details={"field": field}, exit_code=3)
    if status == "generation_failed" or result.get("generation_status") == "generation_failed":
        _require_string(result.get("generation_failed_reason") or "generation failed", "patch_generate_result.generation_failed_reason")
        return
    if result.get("verification_claim") is not None:
        validate_verification_claim(result.get("verification_claim"), "patch_generate_result.verification_claim")
    validate_patch_artifact({"patch_artifact": result.get("patch_artifact")})


def validate_artifact_validation_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    if result.get("validation_skipped") is True:
        _require_string(result.get("generation_blocked_reason") or "validation skipped", "artifact_validation_result.generation_blocked_reason")
        return
    if result.get("validation_failed") is True:
        _require_string(result.get("validation_failed_reason") or "validation failed", "artifact_validation_result.validation_failed_reason")
        return
    if result.get("verification_claim") is not None:
        validate_verification_claim(result.get("verification_claim"), "artifact_validation_result.verification_claim")
    validate_validated_artifact({"validated_artifact": result.get("validated_artifact")})
    artifact = dict(result.get("validated_artifact") or {})
    if artifact.get("is_valid") is not True:
        raise PipelineError("schema_validation_failed", "validated_artifact.is_valid must be true for successful stage output", exit_code=3)


def _validate_verification_check(item: Mapping[str, Any], name: str) -> None:
    _require_string(item.get("name"), f"{name}.name")
    command_value = item.get("command")
    if command_value is not None:
        command = _require_list(command_value, f"{name}.command")
        for idx, token in enumerate(command):
            _require_string(token, f"{name}.command[{idx}]")
    exit_code = item.get("exit_code")
    if exit_code is not None and (not isinstance(exit_code, int) or isinstance(exit_code, bool)):
        raise PipelineError("schema_validation_failed", f"{name}.exit_code must be an integer or null", details={"field": f"{name}.exit_code", "value": exit_code}, exit_code=3)
    _require_numeric_if_present(item.get("duration_ms"), f"{name}.duration_ms")
    status = _require_string(item.get("status"), f"{name}.status")
    if status not in {"passed", "failed", "skipped"}:
        raise PipelineError("schema_validation_failed", f"{name}.status is invalid", details={"field": f"{name}.status", "value": status}, exit_code=3)
    log_path = item.get("log_path")
    if log_path is not None:
        _require_string(log_path, f"{name}.log_path")
    if "failing_tests" in item:
        _require_list(item.get("failing_tests") or [], f"{name}.failing_tests")


def _validate_diagnostics(items: Any, name: str) -> None:
    diagnostics = _require_list(items or [], name)
    for idx, entry in enumerate(diagnostics):
        item = _require_mapping(entry, f"{name}[{idx}]")
        _require_string(item.get("reason_code"), f"{name}[{idx}].reason_code")
        _require_string(item.get("message"), f"{name}[{idx}].message")
        evidence = item.get("evidence")
        if evidence is not None:
            _require_string(evidence, f"{name}[{idx}].evidence")


def validate_static_verification_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    verification = _require_mapping(result.get("static_verification"), "static_verification")
    status = _require_string(verification.get("static_verification_status"), "static_verification.static_verification_status")
    if status not in {"passed", "failed", "skipped"}:
        raise PipelineError("schema_validation_failed", "static_verification.static_verification_status is invalid", details={"status": status}, exit_code=3)
    if "required" in verification and not isinstance(verification.get("required"), bool):
        raise PipelineError("schema_validation_failed", "static_verification.required must be boolean", details={"field": "static_verification.required", "value": verification.get("required")}, exit_code=3)
    _require_string(verification.get("workspace_path"), "static_verification.workspace_path")
    source_workspace = verification.get("source_workspace_path")
    if source_workspace is not None:
        _require_string(source_workspace, "static_verification.source_workspace_path")
    executed_checks = _require_list(verification.get("executed_checks") or [], "static_verification.executed_checks")
    for idx, entry in enumerate(executed_checks):
        _validate_verification_check(_require_mapping(entry, f"static_verification.executed_checks[{idx}]"), f"static_verification.executed_checks[{idx}]")
    summary = _require_mapping(verification.get("summary"), "static_verification.summary")
    for key in ["passed", "failed", "skipped"]:
        value = summary.get(key)
        if not isinstance(value, int) or value < 0:
            raise PipelineError("schema_validation_failed", f"static_verification.summary.{key} must be a non-negative integer", details={"field": f"static_verification.summary.{key}", "value": value}, exit_code=3)
    _validate_diagnostics(verification.get("diagnostics") or [], "static_verification.diagnostics")
    _require_mapping(verification.get("artifact_lineage"), "static_verification.artifact_lineage")


def validate_runtime_verification_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    verification = _require_mapping(result.get("runtime_verification"), "runtime_verification")
    status = _require_string(verification.get("runtime_verification_status"), "runtime_verification.runtime_verification_status")
    if status not in {"passed", "failed", "skipped"}:
        raise PipelineError("schema_validation_failed", "runtime_verification.runtime_verification_status is invalid", details={"status": status}, exit_code=3)
    if "required" in verification and not isinstance(verification.get("required"), bool):
        raise PipelineError("schema_validation_failed", "runtime_verification.required must be boolean", details={"field": "runtime_verification.required", "value": verification.get("required")}, exit_code=3)
    if "require_static_pass" in verification and not isinstance(verification.get("require_static_pass"), bool):
        raise PipelineError("schema_validation_failed", "runtime_verification.require_static_pass must be boolean", details={"field": "runtime_verification.require_static_pass", "value": verification.get("require_static_pass")}, exit_code=3)
    _require_string(verification.get("workspace_path"), "runtime_verification.workspace_path")
    source_workspace = verification.get("source_workspace_path")
    if source_workspace is not None:
        _require_string(source_workspace, "runtime_verification.source_workspace_path")
    executed_checks = _require_list(verification.get("executed_checks") or [], "runtime_verification.executed_checks")
    for idx, entry in enumerate(executed_checks):
        _validate_verification_check(_require_mapping(entry, f"runtime_verification.executed_checks[{idx}]"), f"runtime_verification.executed_checks[{idx}]")
    summary = _require_mapping(verification.get("summary"), "runtime_verification.summary")
    for key in ["passed", "failed", "skipped"]:
        value = summary.get(key)
        if not isinstance(value, int) or value < 0:
            raise PipelineError("schema_validation_failed", f"runtime_verification.summary.{key} must be a non-negative integer", details={"field": f"runtime_verification.summary.{key}", "value": value}, exit_code=3)
    _validate_diagnostics(verification.get("diagnostics") or [], "runtime_verification.diagnostics")
    _require_mapping(verification.get("artifact_lineage"), "runtime_verification.artifact_lineage")


def validate_contract_verification_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    verification = _require_mapping(result.get("contract_verification"), "contract_verification")
    status = _require_string(verification.get("contract_verification_status"), "contract_verification.contract_verification_status")
    if status not in {"satisfied", "violated", "inconclusive", "skipped"}:
        raise PipelineError("schema_validation_failed", "contract_verification.contract_verification_status is invalid", details={"status": status}, exit_code=3)
    _require_string(verification.get("workspace_path"), "contract_verification.workspace_path")
    decision = _require_mapping(verification.get("decision"), "contract_verification.decision")
    decision_status = _require_string(decision.get("status"), "contract_verification.decision.status")
    if decision_status != status:
        raise PipelineError("schema_validation_failed", "contract verification decision status must match contract_verification_status", details={"contract_verification_status": status, "decision_status": decision_status}, exit_code=3)
    _require_list(decision.get("reason_codes") or [], "contract_verification.decision.reason_codes")
    _require_string(decision.get("summary"), "contract_verification.decision.summary")
    checks = _require_list(verification.get("checks") or [], "contract_verification.checks")
    for idx, entry in enumerate(checks):
        item = _require_mapping(entry, f"contract_verification.checks[{idx}]")
        _require_string(item.get("name"), f"contract_verification.checks[{idx}].name")
        check_status = _require_string(item.get("status"), f"contract_verification.checks[{idx}].status")
        if check_status not in {"passed", "failed", "skipped"}:
            raise PipelineError("schema_validation_failed", "contract_verification.check status is invalid", details={"field": f"contract_verification.checks[{idx}].status", "value": check_status}, exit_code=3)
        _require_string(item.get("evidence"), f"contract_verification.checks[{idx}].evidence")
    scope_assessment = _require_mapping(verification.get("scope_assessment"), "contract_verification.scope_assessment")
    for key in ["allowed_files_only", "required_files_touched"]:
        if not isinstance(scope_assessment.get(key), bool):
            raise PipelineError("schema_validation_failed", f"contract_verification.scope_assessment.{key} must be boolean", details={"field": f"contract_verification.scope_assessment.{key}", "value": scope_assessment.get(key)}, exit_code=3)
    _require_list(scope_assessment.get("unexpected_files_touched") or [], "contract_verification.scope_assessment.unexpected_files_touched")
    for key in ["forbidden_files_touched", "must_not_change_violations", "validated_target_mismatches", "missing_expected_new_files"]:
        if key in scope_assessment:
            _require_list(scope_assessment.get(key) or [], f"contract_verification.scope_assessment.{key}")
    if "expected_new_files_present" in scope_assessment and not isinstance(scope_assessment.get("expected_new_files_present"), bool):
        raise PipelineError("schema_validation_failed", "contract_verification.scope_assessment.expected_new_files_present must be boolean", details={"field": "contract_verification.scope_assessment.expected_new_files_present", "value": scope_assessment.get("expected_new_files_present")}, exit_code=3)
    acceptance_assessment = _require_mapping(verification.get("acceptance_assessment"), "contract_verification.acceptance_assessment")
    for key in ["required_checks_present", "static_passed", "runtime_passed"]:
        if not isinstance(acceptance_assessment.get(key), bool):
            raise PipelineError("schema_validation_failed", f"contract_verification.acceptance_assessment.{key} must be boolean", details={"field": f"contract_verification.acceptance_assessment.{key}", "value": acceptance_assessment.get(key)}, exit_code=3)
    for key in ["static_required", "runtime_required", "required_tests_executed", "required_tests_present", "acceptance_criteria_present", "acceptance_criteria_supported", "acceptance_criteria_satisfied", "core_artifacts_valid"]:
        if key in acceptance_assessment and not isinstance(acceptance_assessment.get(key), bool):
            raise PipelineError("schema_validation_failed", f"contract_verification.acceptance_assessment.{key} must be boolean", details={"field": f"contract_verification.acceptance_assessment.{key}", "value": acceptance_assessment.get(key)}, exit_code=3)
    for key in ["failed_acceptance_criteria", "unsupported_acceptance_criteria", "criterion_evaluations"]:
        if key in acceptance_assessment:
            _require_list(acceptance_assessment.get(key) or [], f"contract_verification.acceptance_assessment.{key}")
    if "diagnostics" in verification:
        _validate_diagnostics(verification.get("diagnostics") or [], "contract_verification.diagnostics")
    _require_mapping(verification.get("artifact_lineage"), "contract_verification.artifact_lineage")


def validate_patch_apply_result(result: Mapping[str, Any]) -> None:
    _validate_optional_phase0_fields(result)
    validate_apply_result({"apply_result": result.get("apply_result")})


def validate_execution_trace_payload(payload: Mapping[str, Any]) -> None:
    trace_payload = _require_mapping(payload, "execution_trace_payload")
    _require_string(trace_payload.get("job_id"), "execution_trace_payload.job_id")
    _require_string(trace_payload.get("run_id"), "execution_trace_payload.run_id")
    _require_string(trace_payload.get("stage"), "execution_trace_payload.stage")
    _require_string(trace_payload.get("status"), "execution_trace_payload.status")
    _require_string(trace_payload.get("generated_at"), "execution_trace_payload.generated_at")
    attempt = _require_mapping(trace_payload.get("attempt"), "execution_trace_payload.attempt")
    _require_numeric_if_present(attempt.get("attempt_index"), "execution_trace_payload.attempt.attempt_index")
    _require_mapping(attempt.get("retry_context", {}), "execution_trace_payload.attempt.retry_context")
    _require_string(attempt.get("branch_id"), "execution_trace_payload.attempt.branch_id")
    stage_trace = _require_list(trace_payload.get("stage_trace", []), "execution_trace_payload.stage_trace")
    for idx, entry in enumerate(stage_trace):
        item = _require_mapping(entry, f"execution_trace_payload.stage_trace[{idx}]")
        _require_string(item.get("stage"), f"execution_trace_payload.stage_trace[{idx}].stage")
        _require_string(item.get("status"), f"execution_trace_payload.stage_trace[{idx}].status")
        _require_numeric_if_present(item.get("duration_ms"), f"execution_trace_payload.stage_trace[{idx}].duration_ms")
        _validate_decision_trace(item.get("decision_trace"), f"execution_trace_payload.stage_trace[{idx}].decision_trace")


def validate_execution_attempt_scaffold_payload(payload: Mapping[str, Any]) -> None:
    scaffold = _require_mapping(payload, "execution_attempt_scaffold_payload")
    _require_string(scaffold.get("job_id"), "execution_attempt_scaffold_payload.job_id")
    _require_string(scaffold.get("run_id"), "execution_attempt_scaffold_payload.run_id")
    _require_string(scaffold.get("stage"), "execution_attempt_scaffold_payload.stage")
    attempt = _require_mapping(scaffold.get("attempt"), "execution_attempt_scaffold_payload.attempt")
    _require_numeric_if_present(attempt.get("attempt_index"), "execution_attempt_scaffold_payload.attempt.attempt_index")
    _require_string(attempt.get("branch_id"), "execution_attempt_scaffold_payload.attempt.branch_id")
    _require_string(attempt.get("status"), "execution_attempt_scaffold_payload.attempt.status")
    _require_mapping(attempt.get("retry_context", {}), "execution_attempt_scaffold_payload.attempt.retry_context")
    _require_string(scaffold.get("execution_mode"), "execution_attempt_scaffold_payload.execution_mode")
    if not isinstance(scaffold.get("generation_applicable"), bool):
        raise PipelineError("schema_validation_failed", "execution_attempt_scaffold_payload.generation_applicable must be boolean", details={"field": "execution_attempt_scaffold_payload.generation_applicable"}, exit_code=3)
    _require_list(scaffold.get("planned_tool_sequence", []), "execution_attempt_scaffold_payload.planned_tool_sequence")
    tool_chain = _require_list(scaffold.get("tool_chain", []), "execution_attempt_scaffold_payload.tool_chain")
    for idx, entry in enumerate(tool_chain):
        item = _require_mapping(entry, f"execution_attempt_scaffold_payload.tool_chain[{idx}]")
        _require_string(item.get("name"), f"execution_attempt_scaffold_payload.tool_chain[{idx}].name")
        _require_string(item.get("stage_range"), f"execution_attempt_scaffold_payload.tool_chain[{idx}].stage_range")
        _require_string(item.get("produces"), f"execution_attempt_scaffold_payload.tool_chain[{idx}].produces")
        _require_list(item.get("consumes", []), f"execution_attempt_scaffold_payload.tool_chain[{idx}].consumes")


def validate_execution_contract_payload(payload: Mapping[str, Any]) -> None:
    contract_payload = _require_mapping(payload, "execution_contract_payload")
    _require_string(contract_payload.get("job_id"), "execution_contract_payload.job_id")
    _require_string(contract_payload.get("run_id"), "execution_contract_payload.run_id")
    stage = _require_string(contract_payload.get("stage"), "execution_contract_payload.stage")
    if stage != "script_7_patch_plan":
        raise PipelineError("schema_validation_failed", "execution contract stage must be script_7_patch_plan", details={"stage": stage}, exit_code=3)
    _require_string(contract_payload.get("source_patch_plan_artifact"), "execution_contract_payload.source_patch_plan_artifact")
    _require_string(contract_payload.get("source_context_bundle_artifact"), "execution_contract_payload.source_context_bundle_artifact")
    execution_contract = _require_mapping(contract_payload.get("execution_contract"), "execution_contract_payload.execution_contract")
    _require_string(execution_contract.get("task_mode"), "execution_contract.task_mode")
    _require_string(execution_contract.get("execution_mode"), "execution_contract.execution_mode")
    _require_string(execution_contract.get("output_contract_mode"), "execution_contract.output_contract_mode")
    _require_string(execution_contract.get("objective"), "execution_contract.objective")
    if not isinstance(execution_contract.get("generation_applicable"), bool):
        raise PipelineError("schema_validation_failed", "execution_contract.generation_applicable must be boolean", details={"field": "execution_contract.generation_applicable"}, exit_code=3)
    _require_list(execution_contract.get("allowed_edit_files", []), "execution_contract.allowed_edit_files")
    _require_list(execution_contract.get("forbidden_edit_files", []), "execution_contract.forbidden_edit_files")
    _validate_string_list(execution_contract.get("implementation_target_files") or [], "execution_contract.implementation_target_files")
    _validate_string_list(execution_contract.get("validation_target_files") or [], "execution_contract.validation_target_files")
    if not isinstance(execution_contract.get("must_not_create_files"), bool):
        raise PipelineError("schema_validation_failed", "execution_contract.must_not_create_files must be boolean", details={"field": "execution_contract.must_not_create_files"}, exit_code=3)
    if not isinstance(execution_contract.get("may_create_files"), bool):
        raise PipelineError("schema_validation_failed", "execution_contract.may_create_files must be boolean", details={"field": "execution_contract.may_create_files"}, exit_code=3)
    _require_list(execution_contract.get("expected_new_files", []), "execution_contract.expected_new_files")
    included_files = _require_list(execution_contract.get("included_files", []), "execution_contract.included_files")
    generation_files = _require_list(execution_contract.get("generation_files", []), "execution_contract.generation_files")
    valid_include_modes = {"full_file", "snippets_only", "reference_only"}
    for idx, entry in enumerate(included_files):
        item = _require_mapping(entry, f"execution_contract.included_files[{idx}]")
        _require_string(item.get("path"), f"execution_contract.included_files[{idx}].path")
        _require_string(item.get("file_role"), f"execution_contract.included_files[{idx}].file_role")
        _require_string(item.get("bundle_role"), f"execution_contract.included_files[{idx}].bundle_role")
        include_mode = _require_string(item.get("include_mode"), f"execution_contract.included_files[{idx}].include_mode")
        if include_mode not in valid_include_modes:
            raise PipelineError("schema_validation_failed", "invalid execution contract include_mode", details={"field": f"execution_contract.included_files[{idx}].include_mode", "value": include_mode}, exit_code=3)
        _require_string(item.get("reason_for_inclusion"), f"execution_contract.included_files[{idx}].reason_for_inclusion")
    for idx, entry in enumerate(generation_files):
        item = _require_mapping(entry, f"execution_contract.generation_files[{idx}]")
        _require_string(item.get("path"), f"execution_contract.generation_files[{idx}].path")
        include_mode = _require_string(item.get("include_mode"), f"execution_contract.generation_files[{idx}].include_mode")
        if include_mode not in valid_include_modes:
            raise PipelineError("schema_validation_failed", "invalid generation_files include_mode", details={"field": f"execution_contract.generation_files[{idx}].include_mode", "value": include_mode}, exit_code=3)
        if include_mode == "full_file":
            _require_string(item.get("content"), f"execution_contract.generation_files[{idx}].content")
        elif include_mode == "snippets_only":
            snippets = _require_list(item.get("snippets", []), f"execution_contract.generation_files[{idx}].snippets")
            for s_idx, snippet in enumerate(snippets):
                snippet_item = _require_mapping(snippet, f"execution_contract.generation_files[{idx}].snippets[{s_idx}]")
                _require_numeric_if_present(snippet_item.get("start_line"), f"execution_contract.generation_files[{idx}].snippets[{s_idx}].start_line")
                _require_numeric_if_present(snippet_item.get("end_line"), f"execution_contract.generation_files[{idx}].snippets[{s_idx}].end_line")
                _require_string(snippet_item.get("content"), f"execution_contract.generation_files[{idx}].snippets[{s_idx}].content")
    constraints = _require_mapping(execution_contract.get("constraints"), "execution_contract.constraints")
    for key in ["acceptance_criteria", "required_tests", "must_not_change", "assumptions", "unresolved_uncertainties", "execution_risks"]:
        _require_list(constraints.get(key, []), f"execution_contract.constraints.{key}")
    stage8 = _require_mapping(execution_contract.get("stage8_output_contract"), "execution_contract.stage8_output_contract")
    if str(stage8.get("required_patch_artifact_type") or "") != "unified_diff":
        raise PipelineError("schema_validation_failed", "execution_contract.stage8_output_contract.required_patch_artifact_type must be unified_diff", exit_code=3)
    for key in ["allow_full_file_replacements", "allow_zip_artifacts", "return_prose"]:
        if not isinstance(stage8.get(key), bool):
            raise PipelineError("schema_validation_failed", f"execution_contract.stage8_output_contract.{key} must be boolean", details={"field": f"execution_contract.stage8_output_contract.{key}"}, exit_code=3)
    stage9 = _require_mapping(execution_contract.get("stage9_validation_requirements"), "execution_contract.stage9_validation_requirements")
    for key in ["diff_must_parse", "paths_must_normalize_under_repo_root", "no_out_of_scope_paths", "no_absolute_paths", "no_path_traversal", "artifact_size_below_limit", "git_apply_check_required"]:
        if not isinstance(stage9.get(key), bool):
            raise PipelineError("schema_validation_failed", f"execution_contract.stage9_validation_requirements.{key} must be boolean", details={"field": f"execution_contract.stage9_validation_requirements.{key}"}, exit_code=3)
    _require_numeric_if_present(stage9.get("artifact_size_limit_chars"), "execution_contract.stage9_validation_requirements.artifact_size_limit_chars")
    stage10 = _require_mapping(execution_contract.get("stage10_apply_requirements"), "execution_contract.stage10_apply_requirements")
    for key in ["isolated_workspace_required", "source_repo_mutation_forbidden", "changed_files_must_be_enumerated", "apply_conflict_must_fail_closed"]:
        if not isinstance(stage10.get(key), bool):
            raise PipelineError("schema_validation_failed", f"execution_contract.stage10_apply_requirements.{key} must be boolean", details={"field": f"execution_contract.stage10_apply_requirements.{key}"}, exit_code=3)
    prompt_budget = _require_mapping(execution_contract.get("prompt_budget"), "execution_contract.prompt_budget")
    for key in ["file_count", "full_file_count", "snippet_count", "reference_only_count", "estimated_prompt_size_chars", "estimated_prompt_tokens", "total_chars", "max_total_chars"]:
        _require_numeric_if_present(prompt_budget.get(key), f"execution_contract.prompt_budget.{key}")
    _require_string(prompt_budget.get("degradation_policy"), "execution_contract.prompt_budget.degradation_policy")
    if not isinstance(prompt_budget.get("degradation_applied"), bool):
        raise PipelineError("schema_validation_failed", "execution_contract.prompt_budget.degradation_applied must be boolean", details={"field": "execution_contract.prompt_budget.degradation_applied"}, exit_code=3)
    _require_list(prompt_budget.get("degradation_actions", []), "execution_contract.prompt_budget.degradation_actions")

    stage11 = execution_contract.get("stage11_static_verification")
    if stage11 is not None:
        section = _require_mapping(stage11, "execution_contract.stage11_static_verification")
        if not isinstance(section.get("required"), bool):
            raise PipelineError("schema_validation_failed", "execution_contract.stage11_static_verification.required must be boolean", details={"field": "execution_contract.stage11_static_verification.required"}, exit_code=3)
        _require_list(section.get("blocked_apply_statuses", []), "execution_contract.stage11_static_verification.blocked_apply_statuses")
        checks = _require_list(section.get("checks", []), "execution_contract.stage11_static_verification.checks")
        for idx, entry in enumerate(checks):
            item = _require_mapping(entry, f"execution_contract.stage11_static_verification.checks[{idx}]")
            _require_string(item.get("name"), f"execution_contract.stage11_static_verification.checks[{idx}].name")
            _require_string(item.get("type"), f"execution_contract.stage11_static_verification.checks[{idx}].type")
            if "required" in item and not isinstance(item.get("required"), bool):
                raise PipelineError("schema_validation_failed", f"execution_contract.stage11_static_verification.checks[{idx}].required must be boolean", details={"field": f"execution_contract.stage11_static_verification.checks[{idx}].required"}, exit_code=3)
            if "timeout_seconds" in item:
                _require_numeric_if_present(item.get("timeout_seconds"), f"execution_contract.stage11_static_verification.checks[{idx}].timeout_seconds")
            if item.get("type") == "command":
                command = item.get("command")
                if not isinstance(command, (str, list)):
                    raise PipelineError("schema_validation_failed", f"execution_contract.stage11_static_verification.checks[{idx}].command must be a string or list", details={"field": f"execution_contract.stage11_static_verification.checks[{idx}].command"}, exit_code=3)
    stage12 = execution_contract.get("stage12_runtime_verification")
    if stage12 is not None:
        section = _require_mapping(stage12, "execution_contract.stage12_runtime_verification")
        for key in ["required", "require_static_pass"]:
            if not isinstance(section.get(key), bool):
                raise PipelineError("schema_validation_failed", f"execution_contract.stage12_runtime_verification.{key} must be boolean", details={"field": f"execution_contract.stage12_runtime_verification.{key}"}, exit_code=3)
        _require_list(section.get("blocked_apply_statuses", []), "execution_contract.stage12_runtime_verification.blocked_apply_statuses")
        checks = _require_list(section.get("checks", []), "execution_contract.stage12_runtime_verification.checks")
        for idx, entry in enumerate(checks):
            item = _require_mapping(entry, f"execution_contract.stage12_runtime_verification.checks[{idx}]")
            _require_string(item.get("name"), f"execution_contract.stage12_runtime_verification.checks[{idx}].name")
            _require_string(item.get("type"), f"execution_contract.stage12_runtime_verification.checks[{idx}].type")
            if "required" in item and not isinstance(item.get("required"), bool):
                raise PipelineError("schema_validation_failed", f"execution_contract.stage12_runtime_verification.checks[{idx}].required must be boolean", details={"field": f"execution_contract.stage12_runtime_verification.checks[{idx}].required"}, exit_code=3)
            if "timeout_seconds" in item:
                _require_numeric_if_present(item.get("timeout_seconds"), f"execution_contract.stage12_runtime_verification.checks[{idx}].timeout_seconds")
            if item.get("type") == "command":
                command = item.get("command")
                if not isinstance(command, (str, list)):
                    raise PipelineError("schema_validation_failed", f"execution_contract.stage12_runtime_verification.checks[{idx}].command must be a string or list", details={"field": f"execution_contract.stage12_runtime_verification.checks[{idx}].command"}, exit_code=3)
    stage13 = execution_contract.get("stage13_contract_verification")
    if stage13 is not None:
        section = _require_mapping(stage13, "execution_contract.stage13_contract_verification")
        for key in ["require_static_success", "require_runtime_success", "fail_closed_on_missing_evidence", "enforce_required_files_touched", "enforce_scope"]:
            if not isinstance(section.get(key), bool):
                raise PipelineError("schema_validation_failed", f"execution_contract.stage13_contract_verification.{key} must be boolean", details={"field": f"execution_contract.stage13_contract_verification.{key}"}, exit_code=3)


def validate_planning_review_payload(payload: Mapping[str, Any]) -> None:
    review = _require_mapping(payload.get("review"), "review")
    _require_list(review.get("selected_files"), "review.selected_files")
    _require_list(review.get("bundle_summary"), "review.bundle_summary")
    _require_mapping(review.get("patch_plan_summary"), "review.patch_plan_summary")
    _require_list(review.get("operator_actions"), "review.operator_actions")


def validate_operator_decision_payload(payload: Mapping[str, Any]) -> None:
    result = _require_mapping(payload.get("result"), "result")
    decision = _require_string(result.get("decision"), "result.decision")
    if decision not in {"APPROVE", "REJECT", "REVISE_AND_RERUN"}:
        raise PipelineError("schema_validation_failed", "invalid operator decision", details={"decision": decision}, exit_code=3)
    _require_string(result.get("reviewed_patch_plan_artifact_id"), "result.reviewed_patch_plan_artifact_id")
    _require_string(result.get("reviewed_review_artifact_id"), "result.reviewed_review_artifact_id")


def validate_repair_decision_result(result: Mapping[str, Any]) -> None:
    decision = _require_mapping(result.get("repair_decision"), "repair_decision")
    if not isinstance(decision.get("should_retry"), bool):
        raise PipelineError("schema_validation_failed", "repair_decision.should_retry must be boolean", exit_code=3)
    _require_string(decision.get("retry_reason") or "none", "repair_decision.retry_reason")
    if _require_string(decision.get("retry_mode"), "repair_decision.retry_mode") not in {"none", "patch_regenerate", "patch_repair", "escalate", "abort"}:
        raise PipelineError("schema_validation_failed", "repair_decision.retry_mode is invalid", exit_code=3)
    if _require_string(decision.get("retry_authorization_state"), "repair_decision.retry_authorization_state") not in {"authorized", "requires_operator", "not_allowed"}:
        raise PipelineError("schema_validation_failed", "repair_decision.retry_authorization_state is invalid", exit_code=3)
    if _require_string(decision.get("branch_action"), "repair_decision.branch_action") not in {"no_action", "same_branch_reentry", "fork_new_branch", "terminal_abort"}:
        raise PipelineError("schema_validation_failed", "repair_decision.branch_action is invalid", exit_code=3)
    if decision.get("recommended_reentry_stage") is not None:
        _require_string(decision.get("recommended_reentry_stage"), "repair_decision.recommended_reentry_stage")
    _require_string(decision.get("failure_summary") or "none", "repair_decision.failure_summary")
    failure_classification = _require_mapping(decision.get("failure_classification") or {}, "repair_decision.failure_classification")
    if failure_classification.get("primary_hypothesis") is not None:
        _require_string(failure_classification.get("primary_hypothesis"), "repair_decision.failure_classification.primary_hypothesis")
    _require_list(failure_classification.get("competing_hypotheses") or [], "repair_decision.failure_classification.competing_hypotheses")
    _require_numeric_if_present(failure_classification.get("confidence_score"), "repair_decision.failure_classification.confidence_score")
    _require_list(decision.get("rationale_trace") or [], "repair_decision.rationale_trace")
    bundle = _require_mapping(decision.get("minimal_feedback_bundle") or {}, "repair_decision.minimal_feedback_bundle")
    for field in ["failing_checks", "failing_tests", "affected_files", "excerpts"]:
        _require_list(bundle.get(field) or [], f"repair_decision.minimal_feedback_bundle.{field}")
    for field in ["retry_count", "retry_limit"]:
        value = decision.get(field)
        if not isinstance(value, int) or value < 0:
            raise PipelineError("schema_validation_failed", f"repair_decision.{field} must be a non-negative integer", exit_code=3)
    if not isinstance(decision.get("operator_decision_required"), bool):
        raise PipelineError("schema_validation_failed", "repair_decision.operator_decision_required must be boolean", exit_code=3)
    if decision.get("operator_decision_reason") is not None:
        _require_string(decision.get("operator_decision_reason"), "repair_decision.operator_decision_reason")
    _require_list(decision.get("approval_items") or [], "repair_decision.approval_items")
    _require_list(decision.get("auto_remediation_candidates") or [], "repair_decision.auto_remediation_candidates")
    _require_list(decision.get("blocking_reason_codes") or [], "repair_decision.blocking_reason_codes")



def validate_finalization_result(result: Mapping[str, Any]) -> None:
    finalization = _require_mapping(result.get("finalization"), "finalization")
    if _require_string(finalization.get("final_status"), "finalization.final_status") not in {"accepted", "needs_review", "aborted"}:
        raise PipelineError("schema_validation_failed", "finalization.final_status is invalid", exit_code=3)
    for field in ["final_workspace_path", "final_branch", "commit_sha", "recommended_action", "recommended_first_artifact"]:
        value = finalization.get(field)
        if value is not None:
            _require_string(value, f"finalization.{field}")
    _require_list(finalization.get("persisted_artifacts") or [], "finalization.persisted_artifacts")
    if not isinstance(finalization.get("commit_created"), bool):
        raise PipelineError("schema_validation_failed", "finalization.commit_created must be boolean", exit_code=3)
    decision_basis = _require_mapping(finalization.get("decision_basis") or {}, "finalization.decision_basis")
    if not isinstance(decision_basis.get("contract_passed"), bool):
        raise PipelineError("schema_validation_failed", "finalization.decision_basis.contract_passed must be boolean", exit_code=3)
    if not isinstance(decision_basis.get("required_artifacts_present"), bool):
        raise PipelineError("schema_validation_failed", "finalization.decision_basis.required_artifacts_present must be boolean", exit_code=3)
    if not isinstance(decision_basis.get("operator_decision_required"), bool):
        raise PipelineError("schema_validation_failed", "finalization.decision_basis.operator_decision_required must be boolean", exit_code=3)
    for field in ["static_status", "runtime_status", "retry_mode", "retry_authorization_state", "status_precedence_rule_applied"]:
        value = decision_basis.get(field)
        if value is not None:
            _require_string(value, f"finalization.decision_basis.{field}")
    for field in ["optional_artifacts_missing", "blocking_reason_codes"]:
        _require_list(decision_basis.get(field) or [], f"finalization.decision_basis.{field}")
    for field in ["unresolved_items", "applied_remediation_summary", "approval_pending_summary", "review_reason_codes"]:
        _require_list(finalization.get(field) or [], f"finalization.{field}")
    if finalization.get("final_status") == "needs_review":
        if finalization.get("recommended_action") is None or finalization.get("recommended_first_artifact") is None:
            raise PipelineError("schema_validation_failed", "needs_review finalization must emit recommended_action and recommended_first_artifact", exit_code=3)



def validate_job_report_result(result: Mapping[str, Any]) -> None:
    report = _require_mapping(result.get("job_report"), "job_report")
    _require_string(report.get("summary") or "summary", "job_report.summary")
    final_status = _require_string(report.get("final_status"), "job_report.final_status")
    if final_status not in {"accepted", "needs_review", "aborted"}:
        raise PipelineError("schema_validation_failed", "job_report.final_status is invalid", exit_code=3)
    for field in ["changed_files", "verification_summary", "residual_risks", "operator_action_items", "remediation_summary", "approval_summary", "artifact_refs", "missing_optional_inputs", "degraded_sections"]:
        _require_list(report.get(field) or [], f"job_report.{field}")
    artifact_ref_groups = report.get("artifact_ref_groups")
    if artifact_ref_groups is not None:
        groups = _require_mapping(artifact_ref_groups, "job_report.artifact_ref_groups")
        for key, value in groups.items():
            _require_list(value or [], f"job_report.artifact_ref_groups.{key}")
    timeline = _require_list(report.get("timeline") or [], "job_report.timeline")
    for idx, entry in enumerate(timeline):
        item = _require_mapping(entry, f"job_report.timeline[{idx}]")
        _require_string(item.get("stage"), f"job_report.timeline[{idx}].stage")
        _require_string(item.get("status") or "unknown", f"job_report.timeline[{idx}].status")
        duration_ms = item.get("duration_ms")
        if duration_ms is not None and not isinstance(duration_ms, (int, float)):
            raise PipelineError("schema_validation_failed", "job_report.timeline.duration_ms must be numeric or null", exit_code=3)
    if not isinstance(report.get("operator_action_needed"), bool):
        raise PipelineError("schema_validation_failed", "job_report.operator_action_needed must be boolean", exit_code=3)
    model_response_review = report.get("model_response_review")
    if model_response_review is not None:
        review = _require_mapping(model_response_review, "job_report.model_response_review")
        if "available" in review and not isinstance(review.get("available"), bool):
            raise PipelineError("schema_validation_failed", "job_report.model_response_review.available must be boolean", exit_code=3)
        for field in [
            "reason",
            "provider",
            "requested_model",
            "selected_model",
            "execution_mode",
            "execution_source",
            "output_format_received",
            "raw_output_path",
            "preview_text",
        ]:
            value = review.get(field)
            if value is not None:
                _require_string(value, f"job_report.model_response_review.{field}")
        for field in ["raw_output_chars", "raw_output_lines"]:
            value = review.get(field)
            if value is not None and not isinstance(value, (int, float)):
                raise PipelineError("schema_validation_failed", f"job_report.model_response_review.{field} must be numeric", exit_code=3)
        for field in ["simulation_mode", "preview_truncated"]:
            value = review.get(field)
            if value is not None and not isinstance(value, bool):
                raise PipelineError("schema_validation_failed", f"job_report.model_response_review.{field} must be boolean", exit_code=3)
    next_action = report.get("next_action")
    if final_status == "needs_review":
        next_action_map = _require_mapping(next_action, "job_report.next_action")
        for field in ["type", "reason", "recommended_first_artifact"]:
            _require_string(next_action_map.get(field), f"job_report.next_action.{field}")
    elif next_action is not None:
        next_action_map = _require_mapping(next_action, "job_report.next_action")
        for field in ["type", "reason", "recommended_first_artifact"]:
            _require_string(next_action_map.get(field), f"job_report.next_action.{field}")
