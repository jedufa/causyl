"""Read tracked policy definitions and reject incomplete safety boundaries."""
from __future__ import annotations

import json
import math
from pathlib import Path

_CHECKLIST = {
    "evidence_gathered", "failure_class_recorded",
    "deterministic_recovery_exhausted_or_inapplicable",
    "downstream_validation_contract_prepared",
}
_LAYERS = {
    "intent_layer", "work_shape_layer", "risk_authority_layer",
    "capability_layer", "learning_layer",
}
_REGISTRIES = {
    "action_registry_v1": "actions",
    "execution_playbook_registry_v1": "playbooks",
    "action_rule_mapping_v1": "rules",
    "tool_capability_registry_v1": "entries",
    "composed_execution_graph_v1": "graphs",
    "composed_playbook_registry_v1": "playbooks",
    "phase3_operator_flow_registry_v1": "flows",
    "phase4_operator_flow_registry_v1": "flows",
    "action_family_contracts_v1": "action_families",
    "execution_routing_v1": "family_policies",
    "context_budget_contract_v1": "allowed_context_bundle_ids",
    "chain_failure_and_stop_policy_v1": "required_pre_escalation_checklist",
    "action_outcome_schema_v1": "terminal_outcomes",
}


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate contract key: {key}")
        result[key] = value
    return result


def load_contract(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object)
    name = path.stem
    if not isinstance(data, dict) or data.get("schema_version") != name:
        raise ValueError(f"contract schema mismatch: {name}")
    required = _REGISTRIES.get(name, "required_fields")
    if not data.get(required):
        raise ValueError(f"incomplete contract {name}: {required}")
    for key, value in data.items():
        if key.startswith(("required_", "allowed_")) and not value:
            raise ValueError(f"empty contract constraint: {name}:{key}")
    if name == "request_decomposition_model_v1":
        if (set(data.get("canonical_layers", [])) != _LAYERS
                or data.get("extends_existing_phase1_mapping") is not True
                or set(data.get("required_layer_fields", {})) != _LAYERS
                or not all(data["required_layer_fields"].values())):
            raise ValueError("request decomposition must preserve all five layers")
    if name == "chain_failure_and_stop_policy_v1":
        if (set(data[required]) != _CHECKLIST
                or data.get("proof_gap_continuation_requires_explicit_resolver") is not True):
            raise ValueError("incomplete escalation or proof-gap boundary")
        econ = data.get("economic_stop", {})
        for field in ("min_confidence_delta", "stall_iterations", "min_expected_gain",
                      "max_cost_per_iteration", "max_retries"):
            value = econ.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise ValueError(f"invalid economic stop bound: {field}")
        for field in ("max_retries", "stall_iterations"):
            if not isinstance(econ[field], int):
                raise ValueError(f"non-integer iteration bound: {field}")
    if name == "context_budget_contract_v1":
        if (data.get("max_context_tokens") != 20000
                or set(data.get("required_include_sections", [])) != {"current_action_or_playbook", "current_objective"}
                or not data.get("forbidden_ref_substrings")):
            raise ValueError("incomplete context boundary")
    if name == "governed_improvement_task_policy_v1" and "no_silent_self_rewrite" not in data.get("guardrails", []):
        raise ValueError("missing self-rewrite guardrail")
    if name == "artifact_state_propagation_v2" and data.get("explicit_state_only") is not True:
        raise ValueError("implicit artifact state forbidden")
    return data
