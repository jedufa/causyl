"""Phase-2 controlled composition helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from infra.controlplane import context_bundles as _context_bundles

from .phase2_boundary import (
    PHASE2_ACTION_FAMILY_IDS,
    PHASE2_ENTRYPOINT_IDS,
    PHASE2_MAX_CONTEXT_TOKENS,
    PHASE2_PLAYBOOK_IDS,
    PHASE2_VERIFICATION_OWNER,
)

ACTION_ENTRYPOINTS_PATH = Path("governance/contracts/action_entrypoints_v1.json")
ACTION_FAMILY_CONTRACTS_PATH = Path("governance/contracts/action_family_contracts_v1.json")
EXECUTION_ROUTING_CONTRACT_PATH = Path("governance/contracts/execution_routing_v1.json")
ARTIFACT_STATE_PROPAGATION_CONTRACT_PATH = Path("governance/contracts/artifact_state_propagation_v2.json")
CONTEXT_BUDGET_CONTRACT_PATH = Path("governance/contracts/context_budget_contract_v1.json")
CHAIN_FAILURE_STOP_POLICY_PATH = Path("governance/contracts/chain_failure_and_stop_policy_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _contract_root(repo_root: Optional[Path] = None) -> Path:
    default_root = Path(__file__).resolve().parents[3]
    if repo_root is None:
        return default_root
    candidate = Path(repo_root).resolve()
    if (candidate / ACTION_ENTRYPOINTS_PATH).exists():
        return candidate
    return default_root


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_action_entrypoints(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_contract_root(repo_root) / ACTION_ENTRYPOINTS_PATH)


def load_action_family_contracts(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_contract_root(repo_root) / ACTION_FAMILY_CONTRACTS_PATH)


def load_execution_routing_contract(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_contract_root(repo_root) / EXECUTION_ROUTING_CONTRACT_PATH)


def load_artifact_state_propagation_contract(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_contract_root(repo_root) / ARTIFACT_STATE_PROPAGATION_CONTRACT_PATH)


def load_context_budget_contract(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_contract_root(repo_root) / CONTEXT_BUDGET_CONTRACT_PATH)


def load_chain_failure_stop_policy(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_contract_root(repo_root) / CHAIN_FAILURE_STOP_POLICY_PATH)


def _family_rows(repo_root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    rows = list(load_action_family_contracts(repo_root=repo_root).get("action_families") or [])
    return {str(row.get("action_family") or "").strip(): dict(row) for row in rows if str(row.get("action_family") or "").strip()}


def validate_phase2_contracts(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    entrypoints = load_action_entrypoints(repo_root=repo_root)
    families = load_action_family_contracts(repo_root=repo_root)
    routing = load_execution_routing_contract(repo_root=repo_root)
    artifact_contract = load_artifact_state_propagation_contract(repo_root=repo_root)
    context_budget = load_context_budget_contract(repo_root=repo_root)
    stop_policy = load_chain_failure_stop_policy(repo_root=repo_root)
    errors: List[Dict[str, str]] = []

    entrypoint_ids = tuple(str(row.get("entrypoint_id") or "").strip() for row in list(entrypoints.get("entrypoints") or []) if str(row.get("entrypoint_id") or "").strip())
    if entrypoint_ids != PHASE2_ENTRYPOINT_IDS:
        errors.append({"code": "unexpected_entrypoint_set", "message": "phase-2 entrypoints must match canonical order"})

    family_ids = tuple(str(row.get("action_family") or "").strip() for row in list(families.get("action_families") or []) if str(row.get("action_family") or "").strip())
    if family_ids != PHASE2_ACTION_FAMILY_IDS:
        errors.append({"code": "unexpected_action_family_set", "message": "phase-2 action families must match canonical order"})

    rows = _family_rows(repo_root)
    for family_id, row in rows.items():
        if str(row.get("verification_owner") or "").strip() != PHASE2_VERIFICATION_OWNER:
            errors.append({"code": "family_wrong_verification_owner", "message": f"{family_id} must use {PHASE2_VERIFICATION_OWNER}"})
        for entrypoint_id in list(row.get("supported_entrypoints") or []):
            if str(entrypoint_id or "").strip() not in PHASE2_ENTRYPOINT_IDS:
                errors.append({"code": "unknown_entrypoint_reference", "message": f"{family_id} references unknown entrypoint {entrypoint_id}"})
        playbook_id = str(row.get("default_playbook_id") or "").strip()
        if playbook_id and playbook_id not in PHASE2_PLAYBOOK_IDS:
            errors.append({"code": "unknown_playbook_id", "message": f"{family_id} references unknown playbook {playbook_id}"})

    if str(routing.get("verification_owner") or "").strip() != PHASE2_VERIFICATION_OWNER:
        errors.append({"code": "routing_wrong_verification_owner", "message": "execution routing must declare scheduler_verifier as the single verification owner"})
    family_policies = dict(routing.get("family_policies") or {})
    if set(family_policies.keys()) != set(PHASE2_ACTION_FAMILY_IDS):
        errors.append({"code": "routing_family_policy_mismatch", "message": "routing contract must cover every phase-2 action family exactly"})

    if not bool(artifact_contract.get("explicit_state_only")):
        errors.append({"code": "artifact_state_not_explicit_only", "message": "artifact propagation must remain explicit-state-only"})

    if int(context_budget.get("max_context_tokens") or 0) != PHASE2_MAX_CONTEXT_TOKENS:
        errors.append({"code": "unexpected_context_budget", "message": "phase-2 context budget must remain 20000 tokens"})
    required_include = set(str(x).strip() for x in list(context_budget.get("required_include_sections") or []) if str(x).strip())
    allowed_include = set(str(x).strip() for x in list(context_budget.get("allowed_include_sections") or []) if str(x).strip())
    if not required_include.issubset(allowed_include):
        errors.append({"code": "required_include_not_allowed", "message": "required include sections must be a subset of allowed include sections"})
    if not list(context_budget.get("forbidden_ref_substrings") or []):
        errors.append({"code": "missing_forbidden_refs", "message": "context budget contract must forbid transcript-style replay refs"})

    if str(stop_policy.get("schema_version") or "").strip() != "chain_failure_and_stop_policy_v1":
        errors.append({"code": "unexpected_stop_policy_schema", "message": "chain failure/stop policy schema mismatch"})
    required_checklist = [str(x).strip() for x in list(stop_policy.get("required_pre_escalation_checklist") or []) if str(x).strip()]
    if len(required_checklist) < 4:
        errors.append({"code": "weak_pre_escalation_checklist", "message": "pre-escalation checklist must contain the four mandatory checklist items"})

    return {
        "ok": not errors,
        "errors": errors,
        "entrypoints": entrypoints,
        "families": families,
        "routing": routing,
        "artifact_state": artifact_contract,
        "context_budget": context_budget,
        "stop_policy": stop_policy,
    }


def _normalize_entrypoint_id(payload: Mapping[str, Any]) -> str:
    explicit = str(payload.get("entrypoint_id") or "").strip()
    if explicit:
        return explicit
    source_event = str(payload.get("source_event") or "").strip()
    if source_event == "snapshot_drift":
        return "snapshot_drift_trigger"
    if source_event == "scheduler":
        return "scheduler_action_dispatch"
    if source_event == "cli":
        return "cli_action_execute"
    return "api_action_execute"


def build_snapshot_drift_action_request(snapshot_event: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    drift_id = str(snapshot_event.get("drift_id") or snapshot_event.get("snapshot_id") or "").strip()
    if not drift_id:
        raise ValueError("snapshot drift request requires drift_id or snapshot_id")
    target_uid = str(snapshot_event.get("target_uid") or "").strip()
    return {
        "task_id": f"snapshot::{drift_id}",
        "action_family": "status_reconcile",
        "execution_mode": "deterministic_then_codex",
        "context_bundle_id": str(snapshot_event.get("context_bundle_id") or "lab_core_v1"),
        "requested_by": str(snapshot_event.get("requested_by") or "snapshot_monitor"),
        "source_event": "snapshot_drift",
        "authority_scope": str(snapshot_event.get("authority_scope") or "ledger_status_only"),
        "entrypoint_id": "snapshot_drift_trigger",
        "target_uid": target_uid,
        "drift_class": str(snapshot_event.get("drift_class") or "status_drift"),
    }


def normalize_phase2_action_request(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    validation = validate_phase2_contracts(repo_root=repo_root)
    if not validation.get("ok"):
        raise ValueError("phase-2 contracts invalid")
    data = dict(payload)
    if str(data.get("source_event") or "").strip() == "snapshot_drift" and not str(data.get("action_family") or "").strip():
        data.update(build_snapshot_drift_action_request(data, repo_root=repo_root))
    data["entrypoint_id"] = _normalize_entrypoint_id(data)
    errors: List[Dict[str, str]] = []

    for key in list(validation["entrypoints"].get("required_fields") or []):
        if not str(data.get(key) or "").strip():
            errors.append({"code": "missing_required_field", "message": f"missing required field: {key}"})

    if str(data.get("entrypoint_id") or "").strip() not in PHASE2_ENTRYPOINT_IDS:
        errors.append({"code": "unknown_entrypoint", "message": f"unknown entrypoint: {data.get('entrypoint_id')}"})

    family = str(data.get("action_family") or "").strip()
    if family and family not in PHASE2_ACTION_FAMILY_IDS:
        errors.append({"code": "unknown_action_family", "message": f"unknown action family: {family}"})

    execution_mode = str(data.get("execution_mode") or "").strip()
    allowed_modes = set(str(x).strip() for x in list(validation["entrypoints"].get("allowed_execution_modes") or []) if str(x).strip())
    if execution_mode and execution_mode not in allowed_modes:
        errors.append({"code": "unknown_execution_mode", "message": f"unknown execution mode: {execution_mode}"})

    context_bundle_id = str(data.get("context_bundle_id") or "").strip()
    allowed_bundles = set(str(x).strip() for x in list(validation["context_budget"].get("allowed_context_bundle_ids") or []) if str(x).strip())
    available = set(_context_bundles.available_bundle_ids())
    if context_bundle_id and context_bundle_id not in allowed_bundles:
        errors.append({"code": "context_bundle_not_allowed", "message": f"context bundle not allowed by phase-2 contract: {context_bundle_id}"})
    if context_bundle_id and context_bundle_id not in available:
        errors.append({"code": "context_bundle_unknown", "message": f"unknown context bundle id: {context_bundle_id}"})

    family_row = _family_rows(repo_root).get(family, {})
    if family_row:
        if str(data.get("entrypoint_id") or "") not in set(str(x).strip() for x in list(family_row.get("supported_entrypoints") or []) if str(x).strip()):
            errors.append({"code": "unsupported_entrypoint_for_family", "message": f"{data.get('entrypoint_id')} not supported for {family}"})

    normalized = {
        "task_id": str(data.get("task_id") or "").strip(),
        "action_family": family,
        "execution_mode": execution_mode,
        "context_bundle_id": context_bundle_id,
        "requested_by": str(data.get("requested_by") or "system").strip() or "system",
        "source_event": str(data.get("source_event") or "api").strip() or "api",
        "authority_scope": str(data.get("authority_scope") or "read_only").strip() or "read_only",
        "entrypoint_id": str(data.get("entrypoint_id") or "").strip(),
        "target_uid": str(data.get("target_uid") or "").strip(),
        "requested_executor": str(data.get("requested_executor") or "").strip(),
    }
    return {"ok": not errors, "errors": errors, "request": normalized}


def route_phase2_action_request(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    normalized = normalize_phase2_action_request(payload, repo_root=repo_root)
    if not normalized.get("ok"):
        return {"ok": False, "errors": list(normalized.get("errors") or []), "request": normalized.get("request") or {}}
    request = dict(normalized["request"])
    routing = load_execution_routing_contract(repo_root=repo_root)
    family = str(request.get("action_family") or "")
    family_contract = _family_rows(repo_root).get(family, {})
    policy = dict((routing.get("family_policies") or {}).get(family) or {})
    execution_mode = str(request.get("execution_mode") or "")
    requested_executor = str(request.get("requested_executor") or "")
    executor = str(policy.get("default_executor") or family_contract.get("default_executor") or "controller_only")
    override_applied = False
    warnings: List[Dict[str, str]] = []

    if execution_mode == "controller_only":
        executor = "controller_only"
    elif execution_mode == "deterministic_only":
        executor = str(policy.get("deterministic_executor") or executor)
    elif requested_executor:
        allowed = set(str(x).strip() for x in list(policy.get("allowed_override_executors") or []) if str(x).strip())
        if requested_executor in allowed:
            executor = requested_executor
            override_applied = True
        else:
            warnings.append({"code": "requested_executor_ignored", "message": f"requested executor not allowed for {family}: {requested_executor}"})

    fallback_policy = dict(policy.get("fallback_policy") or {})
    return {
        "ok": True,
        "request": request,
        "routing": {
            "action_family": family,
            "executor": executor,
            "override_applied": override_applied,
            "default_playbook_id": str(family_contract.get("default_playbook_id") or ""),
            "verification_owner": str(routing.get("verification_owner") or ""),
            "fallback_policy": fallback_policy,
        },
        "warnings": warnings,
    }


def validate_phase2_context_bundle(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    contract = load_context_budget_contract(repo_root=repo_root)
    bundle_id = str(payload.get("context_bundle_id") or "").strip()
    include_sections = [str(x).strip() for x in list(payload.get("include_sections") or []) if str(x).strip()]
    extra_refs = [str(x).strip() for x in list(payload.get("extra_refs") or []) if str(x).strip()]
    estimated_tokens = int(payload.get("estimated_tokens") or 0)
    errors: List[Dict[str, str]] = []

    if bundle_id and bundle_id not in set(str(x).strip() for x in list(contract.get("allowed_context_bundle_ids") or []) if str(x).strip()):
        errors.append({"code": "bundle_not_allowed", "message": f"bundle not allowed: {bundle_id}"})
    allowed_sections = set(str(x).strip() for x in list(contract.get("allowed_include_sections") or []) if str(x).strip())
    required_sections = set(str(x).strip() for x in list(contract.get("required_include_sections") or []) if str(x).strip())
    if not required_sections.issubset(set(include_sections)):
        errors.append({"code": "missing_required_include_sections", "message": "required include sections missing"})
    for section in include_sections:
        if section not in allowed_sections:
            errors.append({"code": "include_section_not_allowed", "message": f"include section not allowed: {section}"})
    forbidden = [str(x).strip().lower() for x in list(contract.get("forbidden_ref_substrings") or []) if str(x).strip()]
    for ref in extra_refs:
        low = ref.lower()
        if any(token in low for token in forbidden):
            errors.append({"code": "forbidden_context_ref", "message": f"forbidden context ref: {ref}"})
    if estimated_tokens > int(contract.get("max_context_tokens") or PHASE2_MAX_CONTEXT_TOKENS):
        errors.append({"code": "context_budget_exceeded", "message": f"estimated tokens {estimated_tokens} exceed max budget"})
    return {
        "ok": not errors,
        "errors": errors,
        "bundle_id": bundle_id,
        "resolved_bundle_refs": _context_bundles.bundle_refs(bundle_id) if bundle_id else [],
        "max_context_tokens": int(contract.get("max_context_tokens") or PHASE2_MAX_CONTEXT_TOKENS),
        "estimated_tokens": estimated_tokens,
    }


def _checklist_complete(checklist: Mapping[str, Any], required_keys: List[str]) -> bool:
    return all(bool(checklist.get(key)) for key in required_keys)


def decide_phase2_chain_progress(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    contract = load_chain_failure_stop_policy(repo_root=repo_root)
    econ = dict(contract.get("economic_stop") or {})
    required_checklist = [str(x).strip() for x in list(contract.get("required_pre_escalation_checklist") or []) if str(x).strip()]
    checklist = dict(payload.get("pre_escalation_checklist") or {})
    step_outcome = str(payload.get("step_outcome") or "").strip()
    proof_gaps = [str(x).strip() for x in list(payload.get("proof_gaps") or []) if str(x).strip()]
    resolver_step_allowed = bool(payload.get("resolver_step_allowed"))
    deterministic_recovery_remaining = int(payload.get("deterministic_recovery_remaining") or 0)
    fallback_allowed = bool(payload.get("fallback_allowed"))
    confidence_delta = float(payload.get("confidence_delta") or 0.0)
    stagnant_iterations = int(payload.get("stagnant_iterations") or 0)
    expected_gain = float(payload.get("expected_gain") or 0.0)
    cost_per_iteration = float(payload.get("cost_per_iteration") or 0.0)
    retry_count = int(payload.get("retry_count") or 0)
    economic_reasons: List[str] = []
    if confidence_delta < float(econ.get("min_confidence_delta") or 0.0) and stagnant_iterations >= int(econ.get("stall_iterations") or 0):
        economic_reasons.append("confidence_stalled")
    if expected_gain < float(econ.get("min_expected_gain") or 0.0):
        economic_reasons.append("expected_gain_too_low")
    if cost_per_iteration > float(econ.get("max_cost_per_iteration") or 0.0):
        economic_reasons.append("cost_per_iteration_too_high")
    if retry_count >= int(econ.get("max_retries") or 0):
        economic_reasons.append("max_retries_reached")
    checklist_complete = _checklist_complete(checklist, required_checklist)

    if economic_reasons:
        decision = 'economic_stop'
    elif step_outcome == 'success' and not proof_gaps:
        decision = 'continue'
    elif proof_gaps and resolver_step_allowed and bool(contract.get('proof_gap_continuation_requires_explicit_resolver')):
        decision = 'continue_with_proof_gap_resolver'
    elif proof_gaps:
        decision = 'stop_proof_gap'
    elif step_outcome in {'failed_known', 'blocked'} and deterministic_recovery_remaining > 0:
        decision = 'retry_deterministic'
    elif step_outcome in {'failed_known', 'failed_unknown', 'blocked'} and fallback_allowed and checklist_complete:
        decision = 'escalate'
    else:
        decision = 'block'
    return {
        'decision': decision,
        'checklist_complete': checklist_complete,
        'economic_stop_reasons': economic_reasons,
        'step_outcome': step_outcome,
        'proof_gaps': proof_gaps,
    }


def dispatch_phase2_action_request(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    normalized = normalize_phase2_action_request(payload, repo_root=repo_root)
    if not normalized.get('ok'):
        return {'ok': False, 'errors': list(normalized.get('errors') or []), 'request': normalized.get('request') or {}}
    request = dict(normalized['request'])
    context_result = validate_phase2_context_bundle({
        'context_bundle_id': request.get('context_bundle_id'),
        'include_sections': list(payload.get('include_sections') or ['current_action_or_playbook', 'current_objective']),
        'extra_refs': list(payload.get('extra_refs') or []),
        'estimated_tokens': int(payload.get('estimated_tokens') or 0),
    }, repo_root=repo_root)
    if not context_result.get('ok'):
        return {'ok': False, 'errors': list(context_result.get('errors') or []), 'request': request, 'context_budget': context_result}
    routing = route_phase2_action_request(request, repo_root=repo_root)
    if not routing.get('ok'):
        return routing
    return {
        'ok': True,
        'request': request,
        'context_budget': context_result,
        'routing': routing.get('routing') or {},
        'warnings': list(routing.get('warnings') or []),
    }
