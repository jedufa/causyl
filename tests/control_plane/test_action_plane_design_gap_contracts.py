from __future__ import annotations

from experiments_shared import (
    build_action_plane_alignment_map_v1,
    build_future_action_family_expansion_map_v1,
    build_multi_step_action_composition_v1,
    build_operator_verb_surface_policy_v1,
    build_request_decomposition_v1,
    build_stimulus_e2e_validation_design_v1,
    load_action_plane_alignment_map_schema,
    load_future_action_family_expansion_map_schema,
    load_governed_improvement_task_policy_schema,
    load_independence_gap_registry_schema,
    load_multi_step_action_composition_schema,
    load_operator_verb_surface_policy_schema,
    load_request_decomposition_model_schema,
    load_stimulus_e2e_validation_design_schema,
    synthesize_governed_improvement_task_v1,
    synthesize_independence_gap_registry_v1,
    validate_action_plane_alignment_map_payload,
    validate_future_action_family_expansion_map_payload,
    validate_governed_improvement_task_payload,
    validate_independence_gap_registry_payload,
    validate_multi_step_action_composition_payload,
    validate_operator_verb_surface_policy_payload,
    validate_request_decomposition_payload,
    validate_stimulus_e2e_validation_design_payload,
)


def _fallback_event(*, cost: float = 15.0, build_cost: float = 4.0) -> dict:
    return {
        "action_family": "patch_ingest_repair",
        "intent": "apply incoming patch",
        "failure_class": "semantic_conflict",
        "deterministic_steps_attempted": ["preflight", "repair"],
        "fallback_reason": "semantic_merge_required",
        "pre_escalation_checklist": {"complete": True},
        "codex_action_summary": "manual merge",
        "post_fallback_outcome": "success_after_codex_fallback",
        "solution_path_signature": "semantic_merge_then_validate",
        "bounded_authority_safe": True,
        "validator_path_clear": True,
        "expected_future_codex_cost": cost,
        "operator_friction_cost": 3.0,
        "estimated_build_cost": build_cost,
        "estimated_maintenance_cost": 1.0,
    }


def test_request_decomposition_contract_preserves_phase1_mapping() -> None:
    schema = load_request_decomposition_model_schema()
    assert schema["extends_existing_phase1_mapping"] is True
    payload = build_request_decomposition_v1(
        {
            "request_text": "apply incoming patch for this repo",
            "artifact_presence_flags": {"patch_supplied": True},
            "authority_class": "repo_mutation",
        }
    )
    assert validate_request_decomposition_payload(payload)["ok"] is True
    assert payload["phase1_mapping_reference"]["mapping_contract"] == "phase1_rule_mapping_preserved"
    assert payload["decomposition"]["capability_layer"]["fallback_policy"] == "bounded_patch_repair"


def test_independence_gap_registry_is_descriptive_only_and_maps_gap_status() -> None:
    schema = load_independence_gap_registry_schema()
    assert schema["descriptive_only"] is True
    registry = synthesize_independence_gap_registry_v1({"fallback_events": [_fallback_event(), _fallback_event(cost=16.0)]})
    assert validate_independence_gap_registry_payload(registry)["ok"] is True
    assert registry["descriptive_only"] is True
    assert registry["entries"][0]["current_status"] in {"partial", "wrapper_needed"}


def test_multi_step_composition_contract_preserves_truth_boundaries() -> None:
    schema = load_multi_step_action_composition_schema()
    assert "scheduler_replacement" in schema["non_goals"]
    composition = build_multi_step_action_composition_v1({"action_id": "job_inspect_bounded_recovery_v1"})
    assert validate_multi_step_action_composition_payload(composition)["ok"] is True
    assert composition["resume_law"]["scheduler_truth_authoritative"] is True
    assert any(stage["stage_kind"] == "validation" for stage in composition["stage_contract"])


def test_operator_verb_surface_policy_keeps_compact_truthful_surface() -> None:
    schema = load_operator_verb_surface_policy_schema()
    policy = build_operator_verb_surface_policy_v1()
    assert validate_operator_verb_surface_policy_payload(policy)["ok"] is True
    assert policy["default_surface_name"] == "the plane"
    assert set(policy["truth_visibility_requirements"]) == set(schema["required_truth_visibility"])
    assert policy["surface_policy"]["raw_tool_index_forbidden"] is True


def test_governed_improvement_task_policy_requires_evidence_and_guardrails() -> None:
    schema = load_governed_improvement_task_policy_schema()
    task = synthesize_governed_improvement_task_v1({"fallback_events": [_fallback_event(), _fallback_event(cost=18.0)]})
    assert validate_governed_improvement_task_payload(task)["ok"] is True
    assert set(schema["priority_dimensions"]).issubset(task["priority_dimensions"].keys())
    assert "no_silent_self_rewrite" in task["guardrails"]
    assert task["creation_decision"] == "queue_follow_on_task"


def test_future_action_family_expansion_map_covers_clusters_and_boundaries() -> None:
    schema = load_future_action_family_expansion_map_schema()
    payload = build_future_action_family_expansion_map_v1()
    assert validate_future_action_family_expansion_map_payload(payload)["ok"] is True
    assert set(payload["family_clusters"]) == set(schema["family_clusters"])
    assert payload["authority_boundary_matrix"]["scheduler_and_queue"]["authority_class"] == "high_impact_guarded"


def test_stimulus_e2e_validation_design_stays_stimulation_focused() -> None:
    schema = load_stimulus_e2e_validation_design_schema()
    payload = build_stimulus_e2e_validation_design_v1()
    assert validate_stimulus_e2e_validation_design_payload(payload)["ok"] is True
    assert set(payload["scenario_classes"]) == set(schema["required_scenario_classes"])
    assert payload["seam_rules"]["may_not_claim_harness_built"] is True
    assert "realistic prompts" in payload["stimulation_rule"]["definition"]


def test_alignment_map_points_to_real_umbrella_verified_families_and_open_gap_family() -> None:
    _ = load_action_plane_alignment_map_schema()
    payload = build_action_plane_alignment_map_v1()
    assert validate_action_plane_alignment_map_payload(payload)["ok"] is True
    assert payload["historical_non_authoritative_intents"][0]["intent_uid"] == "IS-20260404-170202Z-FCEE"
    assert [row["parent_uid"] for row in payload["aligned_verified_families"]] == [
        "PIS-20260404-221000Z-A1P0",
        "PIS-20260405-113000Z-W3L0",
        "PIS-20260405-123000Z-F3V0",
    ]
    assert payload["open_design_gap_family"]["parent_uid"] == "PIS-20260405-120600Z-Q2D1"
