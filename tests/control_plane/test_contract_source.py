"""Negative controls for restored source policy and proof boundaries."""
import json
from pathlib import Path

import pytest

from experiments.shared.action_plane.contract_source import load_contract
from experiments.shared.action_plane.phase2_runtime import (
    decide_phase2_chain_progress, normalize_phase2_action_request,
)

ROOT = Path(__file__).resolve().parents[2]
CONTRACTS = ROOT / "governance/contracts"


def test_all_source_contracts_are_complete_and_versioned():
    paths = sorted(CONTRACTS.glob("*.json"))
    assert len(paths) == 37
    for path in paths:
        assert load_contract(path)["schema_version"] == path.stem


@pytest.mark.parametrize("name,key,value", [
    ("request_decomposition_model_v1", "canonical_layers", []),
    ("request_decomposition_model_v1", "required_layer_fields", {}),
    ("request_decomposition_model_v1", "extends_existing_phase1_mapping", False),
    ("chain_failure_and_stop_policy_v1", "required_pre_escalation_checklist", ["arbitrary"] * 4),
    ("chain_failure_and_stop_policy_v1", "proof_gap_continuation_requires_explicit_resolver", False),
    ("context_budget_contract_v1", "max_context_tokens", 1000000),
    ("context_budget_contract_v1", "forbidden_ref_substrings", []),
    ("artifact_state_propagation_v2", "explicit_state_only", False),
    ("governed_improvement_task_policy_v1", "guardrails", ["rewrite_without_review"]),
    ("operator_action_surface_v1", "required_fields", []),
    ("action_registry_v1", "actions", []),
])
def test_weakened_contract_is_rejected(tmp_path, name, key, value):
    payload = json.loads((CONTRACTS / (name + ".json")).read_text())
    payload[key] = value
    path = tmp_path / (name + ".json")
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_contract(path)


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), float("nan"), "unlimited"])
def test_unbounded_economic_stop_rejected(tmp_path, value):
    name = "chain_failure_and_stop_policy_v1.json"
    payload = json.loads((CONTRACTS / name).read_text())
    payload["economic_stop"]["max_retries"] = value
    path = tmp_path / name
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        load_contract(path)


def test_missing_task_contract_is_not_accepted():
    result = normalize_phase2_action_request({})
    assert not result["ok"]
    assert sum(row["code"] == "missing_required_field" for row in result["errors"]) == 4


def test_proof_gap_and_incomplete_checklist_cannot_escalate_or_pass():
    payload = dict(step_outcome="success", proof_gaps=["validation_missing"],
                   confidence_delta=1, expected_gain=1, cost_per_iteration=1,
                   retry_count=0, fallback_allowed=True)
    assert decide_phase2_chain_progress(payload)["decision"] == "stop_proof_gap"
    payload.update(step_outcome="failed_known", proof_gaps=[], pre_escalation_checklist={"evidence_gathered": True})
    assert decide_phase2_chain_progress(payload)["decision"] == "block"


def test_duplicate_json_fields_rejected(tmp_path):
    path = tmp_path / "action_registry_v1.json"
    path.write_text('{"schema_version":"action_registry_v1","actions":[1],"actions":[]}')
    with pytest.raises(ValueError, match="duplicate"):
        load_contract(path)


def test_synthesis_does_not_record_or_duplicate_fallback_history(tmp_path):
    import shutil
    from experiments.shared.action_plane.phase1_runtime import synthesize_improvement_opportunity_v1
    shutil.copytree(CONTRACTS, tmp_path / "governance/contracts")
    event = dict(action_family="patch_apply", intent="repair fixture patch",
        failure_class="conflict", deterministic_steps_attempted=["preflight"],
        fallback_reason="bounded_repair", pre_escalation_checklist={"complete": True},
        post_fallback_outcome="proof_gap_present", solution_path_signature="repair_validate",
        bounded_authority_safe=True, validator_path_clear=True,
        expected_future_codex_cost=10, estimated_build_cost=1)
    result = synthesize_improvement_opportunity_v1([event, event], repo_root=tmp_path)
    assert result["observations"] == 2
    assert not (tmp_path / "runtime").exists()
