from infra.pipeline.retrieval_policy import (
    annotate_stage4_candidate,
    enforce_actionability_policy,
    lane_metadata_for_ranked,
)



def _entry(path: str, **overrides):
    base = {
        "path": path,
        "file_role": "implementation_code",
        "candidate_role": "implementation_code",
        "ownership_role": "core_owner",
        "targetability_score": 8.0,
        "reference_value_score": 4.0,
        "selection_lane": "implementation_target",
        "family_owner_fit": 1.6,
        "coverage_score": 1.0,
        "passive_relevance": {"score": 0.0},
        "reasons": ["owner"],
    }
    base.update(overrides)
    annotate_stage4_candidate(base)
    lane_metadata_for_ranked(base)
    return base



def test_stage5_multilane_ranking_owner_promoted_over_hub():
    owner = _entry("src/feature_owner.py")
    hub = _entry("infra/pipeline/planning.py", ownership_role="boundary_sibling", hub_kind="pipeline_planner_hub", family_owner_fit=0.4, targetability_score=6.0, selection_lane="reference_context")
    assert owner["primary_lane"] == "target"
    assert hub["primary_lane"] in {"support", "reference"}
    assert owner["lane_scores"]["target"] > hub["lane_scores"]["target"]



def test_stage5_multilane_ranking_passive_surface_demoted():
    passive = _entry("reports/generated_projection.py", ownership_role="projection", targetability_score=5.0, selection_lane="reference_context", passive_relevance={"score": 1.2})
    assert passive["primary_lane"] == "passive"
    assert "passive-demotion" in passive["suppression_decisions"]



def test_stage5_actionability_policy_demotes_hub_target_without_mutation_evidence():
    owner = _entry("src/feature_owner.py")
    hub = _entry(
        "infra/pipeline/planning.py",
        ownership_role="boundary_sibling",
        hub_kind="pipeline_planner_hub",
        family_owner_fit=0.25,
        owner_signal_strength=0.2,
        targetability_score=9.0,
        selection_lane="implementation_target",
        explicit_path_request=False,
        covers=[],
    )
    ranked = enforce_actionability_policy(
        [owner, hub],
        task_archetype="implementation_change",
        narrow_task=True,
        policy={"task_archetype_specific_bundle_policies": {"implementation_change": {"rank_target_cap": 1}}},
    )
    hub_after = next(item for item in ranked if item["path"].endswith("planning.py"))
    owner_after = next(item for item in ranked if item["path"].endswith("feature_owner.py"))
    assert owner_after["selection_lane"] == "implementation_target"
    assert hub_after["selection_lane"] == "reference_context"
    lane_metadata_for_ranked(hub_after)
    assert hub_after["primary_lane"] in {"support", "reference"}
    assert any("explicit mutation evidence" in reason for reason in hub_after["suppression_decisions"])



def test_stage5_cap_suppression_surfaces_as_suppressed_lane():
    entry = _entry("infra/pipeline/planning.py", selection_lane="reference_context", hub_kind="pipeline_planner_hub", ownership_role="boundary_sibling")
    entry["selection_cap_suppressed"] = True
    entry["selection_cap_reasons"] = ["hub-family-cap:broad_planner"]
    lane_metadata_for_ranked(entry)
    assert entry["primary_lane"] == "suppressed"
    assert "hub-family-cap:broad_planner" in entry["suppression_decisions"]


def test_stage5_actionability_policy_demotes_subsystem_misaligned_target_candidate():
    owner = _entry(
        "infra/controlplane/scheduled_jobs.py",
        subsystems=["scheduler_core"],
        active_subsystems=["scheduler_core", "retry_backoff"],
    )
    generic = _entry(
        "infra/pipeline/stages/search_plan.py",
        ownership_role="boundary_sibling",
        hub_kind="pipeline_stage_hub",
        family_owner_fit=0.4,
        targetability_score=8.8,
        selection_lane="implementation_target",
        active_subsystems=["scheduler_core", "retry_backoff"],
        subsystems=["query_shaping"],
        explicit_path_request=False,
        covers=["dispatch_path"],
    )
    ranked = enforce_actionability_policy(
        [generic, owner],
        task_archetype="implementation_change",
        narrow_task=True,
        policy={"task_archetype_specific_bundle_policies": {"implementation_change": {"rank_target_cap": 1}}},
    )
    generic_after = next(item for item in ranked if item["path"].endswith("search_plan.py"))
    assert generic_after["selection_lane"] == "reference_context"
    lane_metadata_for_ranked(generic_after)
    assert generic_after["primary_lane"] in {"support", "reference"}
    assert any("subsystem" in reason for reason in generic_after["suppression_decisions"])


def test_stage5_actionability_policy_blocks_context_only_contract_from_target_lane():
    owner = _entry("src/feature_owner.py")
    contract = _entry(
        "docs/contracts/feature_contract.md",
        candidate_role="documentation",
        file_role="documentation",
        ownership_role="local_contract_doc",
        targetability_score=8.5,
        reference_value_score=6.2,
        selection_lane="implementation_target",
        candidate_intent_lane="context_only",
        context_intent_type="contract_reference",
    )
    ranked = enforce_actionability_policy(
        [contract, owner],
        task_archetype="implementation_change",
        narrow_task=False,
        policy={"task_archetype_specific_bundle_policies": {"implementation_change": {"rank_target_cap": 1}}},
    )
    contract_after = next(item for item in ranked if item["path"].endswith("feature_contract.md"))
    assert contract_after["selection_lane"] == "reference_context"
    lane_metadata_for_ranked(contract_after)
    assert contract_after["primary_lane"] == "reference"
    assert any("typed context candidate blocks direct target lane" in reason for reason in contract_after["suppression_decisions"])


def test_stage5_actionability_policy_allows_controlled_fallback_target_when_no_owner_passes():
    hub = _entry(
        "src/control/router.py",
        ownership_role="boundary_sibling",
        hub_kind="webui_router_hub",
        family_owner_fit=0.4,
        owner_signal_strength=0.3,
        support_signal_strength=1.4,
        targetability_score=6.6,
        reference_value_score=5.4,
        selection_lane="implementation_target",
        candidate_intent_lane="context_only",
        context_intent_type="fallback_hub",
        active_subsystems=["operator_entrypoints"],
        subsystems=["operator_entrypoints"],
        explicit_path_request=True,
        covers=["operator_flow", "validation_path"],
    )
    ranked = enforce_actionability_policy(
        [hub],
        task_archetype="implementation_change",
        narrow_task=False,
        policy={"target_fallback_allowance": 1, "task_archetype_specific_bundle_policies": {"implementation_change": {"rank_target_cap": 1}}},
    )
    hub_after = ranked[0]
    assert hub_after["selection_lane"] == "implementation_target"
    assert hub_after["actionability_decision"]["fallback_allowed"] is True
    assert hub_after["actionability_decision"].get("fallback_target") is True
