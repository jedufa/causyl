from infra.pipeline.retrieval_diagnostics import compute_bundle_diagnostics, compute_rank_diagnostics



def test_benchmark_diagnostics_rank_metrics():
    ranked = [
        {"path": "src/owner.py", "primary_lane": "target", "candidate_role_guess": "owner-like", "hub_family": "", "duplicate_family_key": "base:owner.py", "passive_surface_flag": False},
        {"path": "infra/pipeline/planning.py", "primary_lane": "suppressed", "candidate_role_guess": "hub-like", "hub_family": "broad_planner", "duplicate_family_key": "hub:broad_planner", "passive_surface_flag": False, "selection_cap_suppressed": True},
        {"path": "reports/view.py", "primary_lane": "passive", "candidate_role_guess": "passive-like", "hub_family": "", "duplicate_family_key": "base:view.py", "passive_surface_flag": True},
    ]
    metrics = compute_rank_diagnostics(ranked, top_k=3)
    assert metrics["hotspot_topk_count"] == 1
    assert metrics["passive_surface_topk_count"] == 1
    assert metrics["target_lane_purity"] == 1.0
    assert metrics["suppressed_topk_count"] == 1



def test_benchmark_diagnostics_bundle_metrics():
    bundle = [
        {"path": "src/owner.py", "bundle_role": "primary_target", "duplicate_family_key": "base:owner.py"},
        {"path": "tests/test_owner.py", "bundle_role": "paired_test", "duplicate_family_key": "test:owner.py"},
    ]
    summary = {
        "anti_collapse_actions_applied": ["hub-family-cap:broad_planner:infra/pipeline/planning.py"],
        "owner_uncertainty_flag": False,
        "bundle_quality_status": "normal",
        "bundle_recovery_hints": [],
    }
    metrics = compute_bundle_diagnostics(bundle, bundle_summary=summary)
    assert metrics["bundle_owner_present"] is True
    assert metrics["bundle_family_diversity_score"] == 1.0
    assert metrics["bundle_hub_overflow_count"] == 1
    assert metrics["bundle_quality_status"] == "normal"
