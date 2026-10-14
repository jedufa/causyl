from infra.pipeline.retrieval_bundle_roles import (
    bundle_policy_for_task,
    classify_bundle_role,
    select_bundle_candidates,
    summarize_bundle,
)



def test_stage6_bundle_roles_and_summary_degraded_owner_uncertain():
    files = [
        {"path": "docs/spec.md", "primary_lane": "reference", "bundle_role": "contract_context", "duplicate_family_key": "doc:spec.md"},
        {"path": "tests/test_feature.py", "primary_lane": "witness", "bundle_role": "paired_test", "duplicate_family_key": "test:feature.py"},
    ]
    summary = summarize_bundle(files, ["family-cap:hub"], task_archetype="implementation_change", recovery_hints=["widen retrieval"])
    assert summary["owner_present"] is False
    assert summary["owner_uncertainty_flag"] is True
    assert summary["bundle_quality_status"] == "degraded_owner_uncertain"
    assert summary["family_diversity_score"] == 1.0
    assert summary["bundle_recovery_hints"] == ["widen retrieval"]



def test_stage6_classify_bundle_role_task_conditional_targets():
    entry = {"path": "config/pipeline.defaults.yaml", "primary_lane": "target", "secondary_role_hints": ["config"]}
    assert classify_bundle_role(entry, task_archetype="implementation_change", target_index=0) == "config_target"



def test_stage6_bundle_policy_differs_by_archetype():
    impl = bundle_policy_for_task("implementation_change", None, narrow_task=True)
    design = bundle_policy_for_task("design_new_surface", None, narrow_task=False)
    assert impl["target_cap"] == 1
    assert impl["fallback_hub_cap"] == 0
    assert design["reference_cap"] > impl["reference_cap"]



def test_stage6_select_bundle_candidates_enforces_caps_and_preserves_owner():
    prioritized = [
        {"path": "infra/pipeline/planning.py", "primary_lane": "support", "bundle_role": None, "hub_family": "broad_planner", "duplicate_family_key": "hub:broad_planner", "candidate_role_guess": "hub-like"},
        {"path": "src/feature_owner.py", "primary_lane": "target", "ownership_role": "core_owner", "candidate_role_guess": "owner-like", "duplicate_family_key": "base:feature_owner.py", "targetability_score": 9.0},
        {"path": "infra/pipeline/context_ranker.py", "primary_lane": "support", "hub_family": "broad_planner", "duplicate_family_key": "hub:broad_planner", "candidate_role_guess": "hub-like"},
        {"path": "tests/integration/test_feature_flow.py", "primary_lane": "witness", "candidate_role": "test_file", "duplicate_family_key": "test:feature_flow.py"},
    ]
    selection = select_bundle_candidates(
        prioritized,
        task_archetype="implementation_change",
        max_bundle_files=3,
        policy_config={"task_archetype_specific_bundle_policies": {"implementation_change": {"target_cap": 1, "fallback_hub_cap": 0}}},
        narrow_task=True,
    )
    paths = [entry["path"] for entry in selection["selected_entries"]]
    assert "src/feature_owner.py" in paths
    assert sum(1 for entry in selection["selected_entries"] if entry.get("bundle_role") == "fallback_hub") == 0
    assert any(action.startswith("owner-preservation") or "cap" in action for action in selection["anti_collapse_actions"])



def test_stage6_select_bundle_candidates_emits_recovery_hints_when_owner_missing():
    prioritized = [
        {"path": "infra/pipeline/planning.py", "primary_lane": "support", "hub_family": "broad_planner", "duplicate_family_key": "hub:broad_planner", "candidate_role_guess": "hub-like"},
        {"path": "docs/spec.md", "primary_lane": "reference", "ownership_role": "local_contract_doc", "duplicate_family_key": "doc:spec.md"},
    ]
    selection = select_bundle_candidates(
        prioritized,
        task_archetype="implementation_change",
        max_bundle_files=2,
        policy_config={},
        narrow_task=False,
    )
    summary = summarize_bundle(
        selection["selected_entries"],
        selection["anti_collapse_actions"],
        task_archetype="implementation_change",
        candidate_pool=prioritized,
        recovery_hints=selection["recovery_hints"],
    )
    assert summary["bundle_quality_status"] == "degraded_owner_uncertain"
    assert summary["bundle_recovery_hints"]


def test_stage6_select_bundle_candidates_preserves_support_and_witness_around_owner():
    prioritized = [
        {"path": "src/scheduler_core.py", "primary_lane": "target", "ownership_role": "core_owner", "candidate_role_guess": "owner-like", "duplicate_family_key": "base:scheduler_core.py", "subsystems": ["scheduler_core"]},
        {"path": "infra/controlplane/scheduler_retry_manager.py", "primary_lane": "support", "candidate_role_guess": "support-like", "duplicate_family_key": "base:scheduler_retry_manager.py", "subsystems": ["scheduler_core", "retry_backoff"], "support_signal_strength": 2.2},
        {"path": "tests/control_plane/test_scheduler_retry_flow.py", "primary_lane": "witness", "candidate_role": "test_file", "duplicate_family_key": "test:scheduler_retry_flow.py", "subsystems": ["scheduler_core"]},
        {"path": "docs/scheduler_contract.md", "primary_lane": "reference", "ownership_role": "local_contract_doc", "duplicate_family_key": "doc:scheduler_contract.md"},
    ]
    selection = select_bundle_candidates(
        prioritized,
        task_archetype="implementation_change",
        max_bundle_files=3,
        policy_config={"task_archetype_specific_bundle_policies": {"implementation_change": {"target_cap": 1, "support_cap": 1, "witness_cap": 1}}},
        narrow_task=False,
    )
    roles_by_path = {entry["path"]: entry["bundle_role"] for entry in selection["selected_entries"]}
    assert roles_by_path["src/scheduler_core.py"] == "primary_target"
    assert roles_by_path["infra/controlplane/scheduler_retry_manager.py"] in {"support_context", "validation_context"}
    assert roles_by_path["tests/control_plane/test_scheduler_retry_flow.py"] == "paired_test"


def test_stage6_design_bundle_prefers_contract_context_when_available():
    prioritized = [
        {"path": "src/new_surface.py", "primary_lane": "target", "ownership_role": "core_owner", "candidate_role_guess": "owner-like", "duplicate_family_key": "base:new_surface.py"},
        {"path": "docs/new_surface_contract.md", "primary_lane": "reference", "ownership_role": "local_contract_doc", "duplicate_family_key": "doc:new_surface_contract.md", "secondary_role_hints": ["reference"]},
        {"path": "src/render_support.py", "primary_lane": "support", "candidate_role_guess": "support-like", "duplicate_family_key": "base:render_support.py"},
    ]
    selection = select_bundle_candidates(
        prioritized,
        task_archetype="design_new_surface",
        max_bundle_files=2,
        policy_config={},
        narrow_task=False,
    )
    assert any(entry.get("bundle_role") == "contract_context" for entry in selection["selected_entries"])


def test_stage6_classify_bundle_role_prefers_context_intent_type_over_generic_reference_lane():
    entry = {
        "path": "docs/contracts/feature_contract.md",
        "primary_lane": "reference",
        "context_intent_type": "contract_reference",
    }
    assert classify_bundle_role(entry, task_archetype="implementation_change", target_index=0) == "contract_context"


def test_stage6_select_bundle_candidates_uses_constraint_substitution_when_witness_missing():
    prioritized = [
        {"path": "src/feature_owner.py", "primary_lane": "target", "ownership_role": "core_owner", "candidate_role_guess": "owner-like", "duplicate_family_key": "base:feature_owner.py", "subsystems": ["state_persistence"]},
        {"path": "docs/contracts/feature_contract.md", "primary_lane": "reference", "context_intent_type": "contract_reference", "ownership_role": "local_contract_doc", "duplicate_family_key": "doc:feature_contract.md"},
        {"path": "src/feature_support.py", "primary_lane": "support", "candidate_role_guess": "support-like", "duplicate_family_key": "base:feature_support.py", "subsystems": ["state_persistence"], "support_signal_strength": 1.8},
    ]
    selection = select_bundle_candidates(
        prioritized,
        task_archetype="implementation_change",
        max_bundle_files=3,
        policy_config={"task_archetype_specific_bundle_policies": {"implementation_change": {"target_cap": 1, "support_cap": 1, "witness_cap": 1}}},
        narrow_task=False,
    )
    roles = {entry["path"]: entry["bundle_role"] for entry in selection["selected_entries"]}
    assert roles["src/feature_owner.py"] == "primary_target"
    assert roles["src/feature_support.py"] in {"support_context", "validation_context"}
    assert roles["docs/contracts/feature_contract.md"] == "contract_context"
