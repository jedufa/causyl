from infra.pipeline.retrieval_policy import annotate_stage4_candidate


def test_stage4_candidate_annotations_owner_and_hub_fields_present():
    candidate = {
        "path": "infra/pipeline/planning.py",
        "file_role": "implementation_code",
        "ownership_role": "core_owner",
        "hub_kind": "pipeline_planner_hub",
        "family_owner_fit": 1.8,
        "coverage_score": 1.2,
        "passive_relevance": {"score": 0.1},
        "targetability_score": 8.4,
        "reasons": ["dominant retrieval owner"],
    }
    out = annotate_stage4_candidate(candidate, narrow_task=True)
    assert out["candidate_role_guess"] == "owner-like"
    assert out["hub_family"] == "broad_planner"
    assert out["duplicate_family_key"] == "hub:broad_planner"
    assert out["candidate_role_confidence"] > 0.7
    assert out["owner_signal_strength"] > out["support_signal_strength"]
    assert "target" in out["secondary_role_hints"]


def test_stage4_candidate_annotations_mark_passive_surface():
    candidate = {
        "path": "docs/summary_report.md",
        "file_role": "documentation",
        "ownership_role": "projection",
        "passive_relevance": {"score": 1.1},
        "reference_value_score": 5.0,
    }
    out = annotate_stage4_candidate(candidate)
    assert out["passive_surface_flag"] is True
    assert out["candidate_role_guess"] == "passive-like"
    assert "passive-surface" in out["annotation_reasons"]


def test_stage4_candidate_annotations_surface_subsystem_alignment_for_owner_like_candidate():
    candidate = {
        "path": "infra/controlplane/scheduler_retry_manager.py",
        "file_role": "implementation_code",
        "ownership_role": "core_owner",
        "family_owner_fit": 1.3,
        "coverage_score": 1.0,
        "subsystems": ["retry_backoff", "scheduler_core"],
        "active_subsystems": ["scheduler_core", "retry_backoff"],
        "passive_relevance": {"score": 0.0},
        "targetability_score": 7.2,
    }
    out = annotate_stage4_candidate(candidate)
    assert out["subsystem_alignment_score"] > 0.9
    assert any(reason.startswith("subsystems:") for reason in out["annotation_reasons"])
    assert out["owner_signal_strength"] > 2.0


def test_stage4_candidate_annotations_assigns_typed_context_lane_for_contract_doc():
    candidate = {
        "path": "docs/contracts/runtime_contract.md",
        "file_role": "documentation",
        "ownership_role": "local_contract_doc",
        "reference_value_score": 4.2,
        "passive_relevance": {"score": 0.2},
        "subsystems": ["state_persistence"],
    }
    out = annotate_stage4_candidate(candidate)
    assert out["candidate_intent_lane"] == "context_only"
    assert out["context_intent_type"] == "contract_reference"
    assert "config_boundary" in out["structural_family_tags"]


def test_stage4_candidate_annotations_assigns_edit_likely_lane_for_aligned_owner():
    candidate = {
        "path": "src/runtime/state_store.py",
        "file_role": "implementation_code",
        "ownership_role": "core_owner",
        "family_owner_fit": 1.25,
        "coverage_score": 1.0,
        "targetability_score": 6.9,
        "subsystems": ["state_persistence"],
        "active_subsystems": ["state_persistence", "checkpoint_resume"],
        "passive_relevance": {"score": 0.0},
    }
    out = annotate_stage4_candidate(candidate)
    assert out["candidate_intent_lane"] == "edit_likely"
    assert out["context_intent_type"] is None
    assert "persistence_state" in out["structural_family_tags"]
