"""Design-gap contracts that extend the plane without replacing runtime truth."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .phase1_runtime import validate_fallback_event_payload, synthesize_improvement_opportunity_v1
from .routing import (
    ACTION_REGISTRY_PATH,
    EXECUTION_PLAYBOOK_REGISTRY_PATH,
    classify_phase1_action_request,
    load_action_registry,
    load_execution_playbook_registry,
)

REQUEST_DECOMPOSITION_MODEL_SCHEMA_PATH = Path("governance/contracts/request_decomposition_model_v1.json")
INDEPENDENCE_GAP_REGISTRY_SCHEMA_PATH = Path("governance/contracts/independence_gap_registry_v1.json")
MULTI_STEP_ACTION_COMPOSITION_SCHEMA_PATH = Path("governance/contracts/multi_step_action_composition_v1.json")
OPERATOR_VERB_SURFACE_POLICY_SCHEMA_PATH = Path("governance/contracts/operator_verb_surface_policy_v1.json")
GOVERNED_IMPROVEMENT_TASK_POLICY_SCHEMA_PATH = Path("governance/contracts/governed_improvement_task_policy_v1.json")
FUTURE_ACTION_FAMILY_EXPANSION_MAP_SCHEMA_PATH = Path("governance/contracts/future_action_family_expansion_map_v1.json")
STIMULUS_E2E_VALIDATION_DESIGN_SCHEMA_PATH = Path("governance/contracts/stimulus_e2e_validation_design_v1.json")
ACTION_PLANE_ALIGNMENT_MAP_SCHEMA_PATH = Path("governance/contracts/action_plane_alignment_map_v1.json")

_DEFAULT_PUBLIC_VERBS = (
    "apply incoming patch",
    "assess item",
    "inspect job",
    "recover service",
    "run safe maintenance",
    "gather proof",
    "continue interrupted work",
    "prepare implementation",
    "verify outcome",
    "explain blocked state",
)
_DEFAULT_TAXONOMY = (
    "missing_wrapper_action",
    "poor_intent_classification",
    "weak_failure_classification",
    "missing_recovery_step",
    "missing_deterministic_reconciler",
    "missing_validation_bundler",
    "missing_evidence_gatherer",
    "poor_operator_affordance",
    "bad_action_decomposition",
    "excessive_ambiguity_threshold",
    "missing_safe_retry_logic",
)


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_request_decomposition_model_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / REQUEST_DECOMPOSITION_MODEL_SCHEMA_PATH)


def load_independence_gap_registry_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / INDEPENDENCE_GAP_REGISTRY_SCHEMA_PATH)


def load_multi_step_action_composition_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / MULTI_STEP_ACTION_COMPOSITION_SCHEMA_PATH)


def load_operator_verb_surface_policy_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / OPERATOR_VERB_SURFACE_POLICY_SCHEMA_PATH)


def load_governed_improvement_task_policy_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / GOVERNED_IMPROVEMENT_TASK_POLICY_SCHEMA_PATH)


def load_future_action_family_expansion_map_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / FUTURE_ACTION_FAMILY_EXPANSION_MAP_SCHEMA_PATH)


def load_stimulus_e2e_validation_design_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / STIMULUS_E2E_VALIDATION_DESIGN_SCHEMA_PATH)


def load_action_plane_alignment_map_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / ACTION_PLANE_ALIGNMENT_MAP_SCHEMA_PATH)


def _unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _phase1_mapping_reference() -> Dict[str, str]:
    return {
        "action_registry_path": str(ACTION_REGISTRY_PATH),
        "execution_playbook_registry_path": str(EXECUTION_PLAYBOOK_REGISTRY_PATH),
        "mapping_contract": "phase1_rule_mapping_preserved",
    }


def _normalize_stage_kind(stage_kind: str) -> str:
    text = str(stage_kind or "").strip()
    if text == "bounded_recovery":
        return "deterministic_recovery"
    if text == "bounded_fallback":
        return "codex_fallback"
    return text


def build_request_decomposition_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_request_decomposition_model_schema(repo_root=repo_root)
    request_text = str(payload.get("request_text") or "").strip()
    classification = dict(payload.get("phase1_classification") or {})
    if not classification and request_text:
        classification = classify_phase1_action_request(
            {
                "request_text": request_text,
                "artifact_presence_flags": dict(payload.get("artifact_presence_flags") or {}),
                "target_identifiers": dict(payload.get("target_identifiers") or {}),
            },
            repo_root=repo_root,
        )
    action_id = str(classification.get("action_id") or payload.get("action_id") or "").strip()
    fallback_policy = str(classification.get("fallback_eligibility_class") or payload.get("fallback_policy") or "none").strip()
    authority_class = str(payload.get("authority_class") or "read_only").strip()
    review_boundary = str(payload.get("review_boundary") or ("operator_review_required" if authority_class not in {"read_only", "local_mutation"} else "bounded_no_review")).strip()
    capability_class = str(payload.get("deterministic_capability_class") or ("direct_tool_exists" if action_id else "no_deterministic_path_yet")).strip()
    capability_gap_candidates = _unique(list(payload.get("capability_gap_candidates") or []))
    if not capability_gap_candidates and not action_id:
        capability_gap_candidates = ["bad_action_decomposition"]
    requested_result_class = str(payload.get("requested_result_class") or ("diagnosis_and_recommended_next_action" if "explain" in request_text.lower() else "perform_safe_action")).strip()
    stage_ladder = list(payload.get("stage_ladder") or ["deterministic_attempt", "deterministic_recovery", "codex_fallback", "validation", "learning"])
    decomposition = {
        "intent_layer": {
            "operator_request": request_text,
            "intent_family": str(payload.get("intent_family") or action_id or "unclassified_request").strip(),
            "requested_result_class": requested_result_class,
            "operator_verb": str(payload.get("operator_verb") or "").strip(),
        },
        "work_shape_layer": {
            "execution_shape": str(payload.get("execution_shape") or ("single_deterministic_action" if action_id else "compare_and_classify")).strip(),
            "stage_ladder": stage_ladder,
            "validation_mode": str(payload.get("validation_mode") or "post_execution_validation").strip(),
            "resume_safe": bool(payload.get("resume_safe", True)),
        },
        "risk_authority_layer": {
            "authority_class": authority_class,
            "review_boundary": review_boundary,
            "safety_class": str(payload.get("safety_class") or ("high_impact_guarded" if authority_class in {"deploy_affecting", "administrative"} else "bounded_safe")).strip(),
        },
        "capability_layer": {
            "deterministic_capability_class": capability_class,
            "playbook_reference": str(payload.get("playbook_reference") or action_id or "").strip(),
            "fallback_policy": fallback_policy,
            "required_evidence_gatherers": list(classification.get("required_evidence_gatherers") or payload.get("required_evidence_gatherers") or []),
        },
        "learning_layer": {
            "fallback_capture_required": bool(payload.get("fallback_capture_required", fallback_policy not in {"", "none"})),
            "capability_gap_candidates": capability_gap_candidates,
            "improvement_pressure_class": str(payload.get("improvement_pressure_class") or ("high" if fallback_policy not in {"", "none"} else "low")).strip(),
        },
    }
    return {
        "schema_version": "request_decomposition_model_v1",
        "canonical_layers": list(load_request_decomposition_model_schema(repo_root=repo_root).get("canonical_layers") or []),
        "phase1_mapping_reference": _phase1_mapping_reference(),
        "decomposition": decomposition,
    }


def validate_request_decomposition_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_request_decomposition_model_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    decomposition = dict(payload.get("decomposition") or {})
    required_layer_fields = dict(schema.get("required_layer_fields") or {})
    for layer in list(schema.get("canonical_layers") or []):
        layer_name = str(layer or "").strip()
        if layer_name not in decomposition:
            errors.append({"code": "missing_layer", "message": f"missing required layer: {layer_name}"})
            continue
        row = dict(decomposition.get(layer_name) or {})
        for field in [str(x or "").strip() for x in list(required_layer_fields.get(layer_name) or []) if str(x or "").strip()]:
            if field not in row:
                errors.append({"code": "missing_layer_field", "message": f"{layer_name} missing field: {field}"})
    reference = dict(payload.get("phase1_mapping_reference") or {})
    if not reference.get("action_registry_path") or not reference.get("execution_playbook_registry_path"):
        errors.append({"code": "missing_phase1_reference", "message": "phase1 mapping reference must preserve action and playbook registry pointers"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}


def _taxonomy_for(events: Sequence[Mapping[str, Any]], opportunities: Sequence[Mapping[str, Any]]) -> List[str]:
    seen = list(_DEFAULT_TAXONOMY)
    for row in list(events) + list(opportunities):
        for key in ("capability_miss_taxonomy", "observations"):
            value = row.get(key)
            if isinstance(value, list):
                for item in value:
                    text = str(item or "").strip()
                    if text and text not in seen:
                        seen.append(text)
    return seen


def _gap_status(opportunity: Mapping[str, Any]) -> str:
    decision = str(opportunity.get("decision") or "").strip()
    kind = str(opportunity.get("governed_follow_on_kind") or "").strip()
    if decision == "promote_opportunity" and kind == "new_wrapper_action":
        return "wrapper_needed"
    if decision == "promote_opportunity":
        return "partial"
    if decision == "reject_opportunity":
        return "missing"
    return "partial"


def synthesize_independence_gap_registry_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_independence_gap_registry_schema(repo_root=repo_root)
    fallback_events = [dict(row) for row in list(payload.get("fallback_events") or []) if isinstance(row, Mapping)]
    if any(not validate_fallback_event_payload(row, repo_root=repo_root).get("ok") for row in fallback_events):
        raise ValueError("invalid fallback event")
    opportunities = [dict(row) for row in list(payload.get("improvement_opportunities") or []) if isinstance(row, Mapping)]
    if not opportunities and fallback_events:
        opportunities = [synthesize_improvement_opportunity_v1(fallback_events, repo_root=repo_root)]
    entries: List[Dict[str, Any]] = []
    for index, row in enumerate(opportunities, start=1):
        action_family = str(row.get("action_family") or "unknown_action_family").strip()
        failure_class = str(row.get("failure_class") or "unknown_failure_class").strip()
        entries.append(
            {
                "gap_id": f"gap::{action_family}::{failure_class}::{index}",
                "operator_pain_description": str(row.get("operator_pain_description") or f"Recurring fallback pressure in {action_family} for {failure_class}.").strip(),
                "manual_or_codex_heavy_path": str(row.get("manual_or_codex_heavy_path") or "bounded Codex fallback remains required").strip(),
                "desired_action_or_playbook": str(row.get("desired_action_or_playbook") or action_family).strip(),
                "current_status": _gap_status(row),
                "pain_score": int(row.get("pain_score") or min(10, max(1, int(row.get("observations") or 1) * 2))),
                "frequency": int(row.get("frequency") or int(row.get("observations") or 1)),
                "codex_dependency_level": str(row.get("codex_dependency_level") or "medium").strip(),
                "recurrence_evidence": {
                    "observations": int(row.get("observations") or 1),
                    "decision": str(row.get("decision") or "").strip(),
                },
                "proposed_absorption_target": str(row.get("proposed_absorption_target") or row.get("governed_follow_on_kind") or "").strip(),
                "blocker_reason": str(row.get("blocker_reason") or row.get("rejection_reason") or "").strip(),
                "owner": str(row.get("owner") or "action_plane").strip(),
                "learning_source": str(row.get("learning_source") or "fallback_event").strip(),
                "descriptive_only": bool(schema.get("descriptive_only", True)),
            }
        )
    return {
        "schema_version": "independence_gap_registry_v1",
        "descriptive_only": bool(schema.get("descriptive_only", True)),
        "capability_miss_taxonomy": _taxonomy_for(fallback_events, opportunities),
        "entries": entries,
    }


def validate_independence_gap_registry_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_independence_gap_registry_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    allowed_statuses = {str(x or "").strip() for x in list(schema.get("gap_status_values") or []) if str(x or "").strip()}
    for entry in list(payload.get("entries") or []):
        if not isinstance(entry, Mapping):
            errors.append({"code": "invalid_entry", "message": "registry entries must be mappings"})
            continue
        for field in [str(x or "").strip() for x in list(schema.get("required_entry_fields") or []) if str(x or "").strip()]:
            if field not in entry:
                errors.append({"code": "missing_entry_field", "message": f"gap entry missing field: {field}"})
        current_status = str(entry.get("current_status") or "").strip()
        if current_status and current_status not in allowed_statuses:
            errors.append({"code": "invalid_gap_status", "message": f"unknown current_status: {current_status}"})
        if entry.get("descriptive_only") is not True:
            errors.append({"code": "gap_must_be_descriptive_only", "message": "independence gap entries may not claim runtime truth"})
    if payload.get("descriptive_only") is not True:
        errors.append({"code": "registry_must_be_descriptive_only", "message": "independence gap registry must remain descriptive_only"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}


def build_multi_step_action_composition_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_multi_step_action_composition_schema(repo_root=repo_root)
    playbooks = load_execution_playbook_registry(repo_root=repo_root)
    action_id = str(payload.get("action_id") or "").strip()
    selected_playbook = {}
    for row in list(playbooks.get("playbooks") or []):
        if str(row.get("action_id") or "").strip() == action_id:
            selected_playbook = dict(row)
            break
    stage_contract = [
        {
            "stage_id": f"stage_{index}",
            "stage_kind": _normalize_stage_kind(str(stage or "").strip()),
            "resume_safe": _normalize_stage_kind(str(stage or "").strip()) != "codex_fallback",
            "validator_handoff_required": _normalize_stage_kind(str(stage or "").strip()) in {"validation", "learning"},
        }
        for index, stage in enumerate(list(payload.get("stage_ladder") or selected_playbook.get("execution_ladder") or schema.get("allowed_stage_kinds") or []), start=1)
    ]
    transition_law = {
        "sequence_supported": True,
        "branch_supported": True,
        "bounded_retry_supported": True,
        "inspect_before_mutate_required_for_risky_paths": True,
        "transition_kinds": list(schema.get("allowed_transition_kinds") or []),
    }
    return {
        "schema_version": "multi_step_action_composition_v1",
        "composition_id": str(payload.get("composition_id") or f"composition::{action_id or 'generic'}").strip(),
        "stage_contract": stage_contract,
        "transition_law": transition_law,
        "resume_law": {
            "resume_from_last_verified_stage_only": True,
            "scheduler_truth_authoritative": True,
            "receipt_truth_authoritative": True,
            "verifier_truth_authoritative": True,
        },
        "truth_boundary_notes": [
            "This contract describes composition semantics only.",
            "Scheduler, receipt, and verifier evidence remain authoritative runtime truth.",
            "No domain-general planner is implied by this contract.",
        ],
    }


def validate_multi_step_action_composition_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_multi_step_action_composition_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    allowed_stage_kinds = {str(x or "").strip() for x in list(schema.get("allowed_stage_kinds") or []) if str(x or "").strip()}
    for row in list(payload.get("stage_contract") or []):
        if not isinstance(row, Mapping):
            errors.append({"code": "invalid_stage_row", "message": "stage rows must be mappings"})
            continue
        stage_kind = str(row.get("stage_kind") or "").strip()
        if stage_kind and stage_kind not in allowed_stage_kinds:
            errors.append({"code": "unknown_stage_kind", "message": f"unknown stage_kind: {stage_kind}"})
    notes = " ".join(str(x or "").strip().lower() for x in list(payload.get("truth_boundary_notes") or []))
    if "scheduler" not in notes or "verifier" not in notes:
        errors.append({"code": "missing_truth_boundary_notes", "message": "truth_boundary_notes must preserve scheduler and verifier authority"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}


def build_operator_verb_surface_policy_v1(payload: Mapping[str, Any] | None = None, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_operator_verb_surface_policy_schema(repo_root=repo_root)
    action_registry = load_action_registry(repo_root=repo_root)
    payload = payload or {}
    action_verbs = _unique(
        [str(row.get("operator_verb") or "").strip() for row in list(action_registry.get("actions") or [])]
        + list(payload.get("additional_verbs") or [])
    )
    public_verbs = _unique(list(_DEFAULT_PUBLIC_VERBS[:3]) + action_verbs + list(_DEFAULT_PUBLIC_VERBS[3:]))
    return {
        "schema_version": "operator_verb_surface_policy_v1",
        "default_surface_name": "the plane",
        "public_verbs": public_verbs,
        "surface_policy": {
            "mobile": "compact_intent_first",
            "cli": "compact_intent_first",
            "web": "compact_intent_first",
            "assistant": "compact_intent_first",
            "raw_tool_index_forbidden": True,
        },
        "extension_rules": {
            "new_verbs_require_backing_action_or_playbook": True,
            "new_verbs_require_truthful_proof_visibility": True,
            "new_verbs_must_not_outrun_deterministic_capability": True,
        },
        "truth_visibility_requirements": list(load_operator_verb_surface_policy_schema(repo_root=repo_root).get("required_truth_visibility") or []),
    }


def validate_operator_verb_surface_policy_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_operator_verb_surface_policy_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    verbs = [str(x or "").strip() for x in list(payload.get("public_verbs") or []) if str(x or "").strip()]
    if not verbs:
        errors.append({"code": "missing_public_verbs", "message": "public_verbs must not be empty"})
    if str(payload.get("default_surface_name") or "").strip() != "the plane":
        errors.append({"code": "bad_default_surface_name", "message": "default surface name must be the plane"})
    truth_visibility = set(str(x or "").strip() for x in list(payload.get("truth_visibility_requirements") or []) if str(x or "").strip())
    required_truth = set(str(x or "").strip() for x in list(schema.get("required_truth_visibility") or []) if str(x or "").strip())
    if truth_visibility != required_truth:
        errors.append({"code": "missing_truth_visibility", "message": "truth visibility requirements must match contract"})
    surface_policy = dict(payload.get("surface_policy") or {})
    if not surface_policy.get("raw_tool_index_forbidden"):
        errors.append({"code": "raw_tool_index_allowed", "message": "surface policy must forbid raw tool-index exposure"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}


def synthesize_governed_improvement_task_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_governed_improvement_task_policy_schema(repo_root=repo_root)
    opportunity = dict(payload.get("improvement_opportunity") or {})
    if not opportunity:
        fallback_events = [dict(row) for row in list(payload.get("fallback_events") or []) if isinstance(row, Mapping)]
        if not fallback_events:
            raise ValueError("governed improvement task synthesis requires improvement_opportunity or fallback_events")
        opportunity = synthesize_improvement_opportunity_v1(fallback_events, repo_root=repo_root)
    observations = int(opportunity.get("observations") or 0)
    frequency = int(payload.get("frequency") or observations)
    recurrence = int(payload.get("recurrence") or observations)
    codex_cost = float(payload.get("codex_cost") or opportunity.get("estimated_future_codex_cost_total") or 0.0)
    operator_friction = float(payload.get("operator_friction") or frequency)
    safety_value = float(payload.get("safety_value") or (4.0 if str(opportunity.get("decision") or "") == "promote_opportunity" else 1.0))
    feasibility = float(payload.get("feasibility") or (4.0 if str(opportunity.get("decision") or "") == "promote_opportunity" else 2.0))
    pain = float(payload.get("pain") or min(10.0, max(1.0, float(frequency + recurrence))))
    priority_score = round(pain + float(frequency) + float(recurrence) + codex_cost + operator_friction + safety_value + feasibility, 2)
    duplicate_signal = bool(payload.get("duplicate_existing_task"))
    creation_decision = "queue_follow_on_task"
    queue_recommendation = "queued"
    if duplicate_signal:
        creation_decision = "suppress_duplicate"
        queue_recommendation = "do_not_queue"
    elif observations < 2 or str(opportunity.get("decision") or "") != "promote_opportunity":
        creation_decision = "record_only"
        queue_recommendation = "needs_more_evidence"
    return {
        "schema_version": "governed_improvement_task_policy_v1",
        "task_id": str(payload.get("task_id") or f"improvement::{opportunity.get('action_family') or 'unknown'}::{opportunity.get('failure_class') or 'unknown'}").strip(),
        "creation_decision": creation_decision,
        "required_evidence": {
            "fallback_evidence_present": observations > 0,
            "decision": str(opportunity.get("decision") or "").strip(),
            "observations": observations,
            "governed_follow_on_kind": str(opportunity.get("governed_follow_on_kind") or "").strip(),
        },
        "priority_dimensions": {
            "pain": pain,
            "frequency": frequency,
            "recurrence": recurrence,
            "codex_cost": codex_cost,
            "operator_friction": operator_friction,
            "safety_value": safety_value,
            "feasibility": feasibility,
        },
        "priority_score": priority_score,
        "queue_recommendation": queue_recommendation,
        "guardrails": list(schema.get("guardrails") or []),
    }


def validate_governed_improvement_task_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_governed_improvement_task_policy_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    dimensions = dict(payload.get("priority_dimensions") or {})
    for field in [str(x or "").strip() for x in list(schema.get("priority_dimensions") or []) if str(x or "").strip()]:
        if field not in dimensions:
            errors.append({"code": "missing_priority_dimension", "message": f"priority_dimensions missing field: {field}"})
    guardrails = set(str(x or "").strip() for x in list(payload.get("guardrails") or []) if str(x or "").strip())
    if "no_silent_self_rewrite" not in guardrails:
        errors.append({"code": "missing_self_rewrite_guardrail", "message": "guardrails must prohibit silent self-rewrite"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}


def build_future_action_family_expansion_map_v1(payload: Mapping[str, Any] | None = None, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_future_action_family_expansion_map_schema(repo_root=repo_root)
    payload = payload or {}
    authority_boundary_matrix = {
        "patch_and_change": {
            "authority_class": "repo_mutation",
            "review_boundary": "bounded_review_when_scope_expands",
        },
        "pis_and_governance": {
            "authority_class": "repo_mutation",
            "review_boundary": "governance_review_required_for_close_or_queue",
        },
        "job_and_execution": {
            "authority_class": "scheduler_mutation",
            "review_boundary": "bounded_job_only_recovery_before_escalation",
        },
        "scheduler_and_queue": {
            "authority_class": "high_impact_guarded",
            "review_boundary": "operator_review_required",
        },
        "server_and_runtime": {
            "authority_class": "runtime_mutation",
            "review_boundary": "guarded_runtime_review_required",
        },
        "repo_and_validation": {
            "authority_class": "repo_mutation",
            "review_boundary": "validation_first_then_mutate",
        },
        "operator_assistance": {
            "authority_class": "read_only",
            "review_boundary": "descriptive_only_no_runtime_truth_override",
        },
        "long_horizon": {
            "authority_class": "high_impact_guarded",
            "review_boundary": "queued_governed_follow_on_only",
        },
    }
    return {
        "schema_version": "future_action_family_expansion_map_v1",
        "family_clusters": list(schema.get("family_clusters") or []),
        "authority_boundary_matrix": authority_boundary_matrix,
        "substrate_reuse_rules": [
            "Reuse ledger, scheduler, queue, verifier, receipts, runtime state, and transcript/artifact retrieval first.",
            "Future families may wrap substrate seams but must not create a second truth layer.",
            "Family expansion may describe growth paths but may not claim implementation readiness by naming alone.",
        ],
        "non_goals": list(schema.get("non_goals") or []),
    }


def validate_future_action_family_expansion_map_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_future_action_family_expansion_map_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    clusters = [str(x or "").strip() for x in list(payload.get("family_clusters") or []) if str(x or "").strip()]
    required_clusters = {str(x or "").strip() for x in list(schema.get("family_clusters") or []) if str(x or "").strip()}
    if set(clusters) != required_clusters:
        errors.append({"code": "bad_family_clusters", "message": "family_clusters must match contract"})
    allowed_authority = {str(x or "").strip() for x in list(schema.get("allowed_authority_classes") or []) if str(x or "").strip()}
    matrix = dict(payload.get("authority_boundary_matrix") or {})
    for cluster in required_clusters:
        row = dict(matrix.get(cluster) or {})
        authority = str(row.get("authority_class") or "").strip()
        if not authority:
            errors.append({"code": "missing_authority_boundary", "message": f"missing authority boundary for {cluster}"})
        elif authority not in allowed_authority:
            errors.append({"code": "invalid_authority_class", "message": f"invalid authority_class for {cluster}: {authority}"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}


def build_stimulus_e2e_validation_design_v1(payload: Mapping[str, Any] | None = None, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_stimulus_e2e_validation_design_schema(repo_root=repo_root)
    payload = payload or {}
    return {
        "schema_version": "stimulus_e2e_validation_design_v1",
        "scenario_classes": list(schema.get("required_scenario_classes") or []),
        "required_artifact_types": [
            "prompt_scenario",
            "patch_artifact",
            "broken_patch_artifact",
            "seeded_runtime_state",
            "expected_terminal_truth",
            "forbidden_mutation_declarations",
        ],
        "durable_evidence_surfaces": list(schema.get("durable_evidence_surfaces") or []),
        "seam_rules": {
            "prefer_real_scheduler_or_build_verify_seam": True,
            "module_only_payload_assertions_insufficient": True,
            "must_reuse_existing_substrate": True,
            "may_not_claim_harness_built": True,
        },
        "stimulation_rule": {
            "definition": "Stimulation means realistic prompts, patches, verifier failures, recovery signals, and escalation histories are driven through the narrowest real execution seam that can materialize durable evidence.",
            "simulation_forbidden_definition": "Calling exported helper functions with fabricated end-state payloads alone does not count as end-to-end proof.",
        },
    }


def validate_stimulus_e2e_validation_design_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_stimulus_e2e_validation_design_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    scenarios = {str(x or "").strip() for x in list(payload.get("scenario_classes") or []) if str(x or "").strip()}
    required_scenarios = {str(x or "").strip() for x in list(schema.get("required_scenario_classes") or []) if str(x or "").strip()}
    if scenarios != required_scenarios:
        errors.append({"code": "bad_scenario_classes", "message": "scenario_classes must match contract"})
    surfaces = {str(x or "").strip() for x in list(payload.get("durable_evidence_surfaces") or []) if str(x or "").strip()}
    required_surfaces = {str(x or "").strip() for x in list(schema.get("durable_evidence_surfaces") or []) if str(x or "").strip()}
    if surfaces != required_surfaces:
        errors.append({"code": "bad_evidence_surface_set", "message": "durable_evidence_surfaces must match contract"})
    seam_rules = dict(payload.get("seam_rules") or {})
    if not seam_rules.get("prefer_real_scheduler_or_build_verify_seam"):
        errors.append({"code": "missing_real_seam_rule", "message": "design must require a real scheduler/build-verify seam where appropriate"})
    stimulation_rule = dict(payload.get("stimulation_rule") or {})
    if "realistic prompts" not in str(stimulation_rule.get("definition") or ""):
        errors.append({"code": "weak_stimulation_definition", "message": "stimulation_rule must distinguish realistic stimulation from simulation"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}


def build_action_plane_alignment_map_v1(payload: Mapping[str, Any] | None = None, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_action_plane_alignment_map_schema(repo_root=repo_root)
    payload = payload or {}
    return {
        "schema_version": "action_plane_alignment_map_v1",
        "umbrella_intent_uid": "IS-20260405-120500Z-UAP1",
        "historical_non_authoritative_intents": [
            {
                "intent_uid": "IS-20260404-170202Z-FCEE",
                "relationship": "historical_non_authoritative_narrow_import",
                "reason": "Imported as a mistaken narrow Phase 1 source; superseded by the broader umbrella intent.",
            }
        ],
        "aligned_verified_families": [
            {
                "family_name": "phase1_deterministic_foundations",
                "parent_uid": "PIS-20260404-221000Z-A1P0",
                "status": "verified",
                "alignment_class": "aligned_existing_implementation",
            },
            {
                "family_name": "phase3_operator_intelligence",
                "parent_uid": "PIS-20260405-113000Z-W3L0",
                "status": "verified",
                "alignment_class": "aligned_existing_implementation",
            },
            {
                "family_name": "phase4_capability_absorption",
                "parent_uid": "PIS-20260405-123000Z-F3V0",
                "status": "verified",
                "alignment_class": "aligned_existing_implementation",
            },
        ],
        "open_design_gap_family": {
            "parent_uid": "PIS-20260405-120600Z-Q2D1",
            "status": "active",
            "alignment_class": "open_design_gap",
        },
        "coverage_map": [
            {
                "umbrella_clause": "deterministic_first_execution_and_bounded_fallback",
                "coverage_status": "aligned",
                "supporting_parent_uids": ["PIS-20260404-221000Z-A1P0"],
            },
            {
                "umbrella_clause": "operator_explanation_uncertainty_and_next_actions",
                "coverage_status": "aligned",
                "supporting_parent_uids": ["PIS-20260405-113000Z-W3L0"],
            },
            {
                "umbrella_clause": "capability_absorption_and_governed_upgrade_pressure",
                "coverage_status": "aligned",
                "supporting_parent_uids": ["PIS-20260405-123000Z-F3V0"],
            },
            {
                "umbrella_clause": "broader_design_contract_completion",
                "coverage_status": "open",
                "supporting_parent_uids": ["PIS-20260405-120600Z-Q2D1"],
            },
        ],
    }


def validate_action_plane_alignment_map_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_action_plane_alignment_map_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or "").strip() for x in list(schema.get("required_fields") or []) if str(x or "").strip()]:
        if field not in payload:
            errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    if str(payload.get("umbrella_intent_uid") or "").strip() != "IS-20260405-120500Z-UAP1":
        errors.append({"code": "bad_umbrella_uid", "message": "alignment map must point at the current umbrella intent"})
    historical = list(payload.get("historical_non_authoritative_intents") or [])
    if not historical:
        errors.append({"code": "missing_historical_supersession", "message": "alignment map must name the historical non-authoritative narrow FCEE import"})
    families = list(payload.get("aligned_verified_families") or [])
    required_family_fields = [str(x or "").strip() for x in list(schema.get("required_family_fields") or []) if str(x or "").strip()]
    for row in families:
        if not isinstance(row, Mapping):
            errors.append({"code": "invalid_family_row", "message": "aligned_verified_families rows must be mappings"})
            continue
        for field in required_family_fields:
            if field not in row:
                errors.append({"code": "missing_family_field", "message": f"aligned family missing field: {field}"})
        if str(row.get("status") or "").strip() != "verified":
            errors.append({"code": "family_not_verified", "message": f"aligned family must be verified: {row.get('parent_uid')}"})
    open_family = dict(payload.get("open_design_gap_family") or {})
    if str(open_family.get("parent_uid") or "").strip() != "PIS-20260405-120600Z-Q2D1":
        errors.append({"code": "bad_open_design_gap_parent", "message": "open design gap family must point at Q2D1"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}
