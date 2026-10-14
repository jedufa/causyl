from __future__ import annotations

from pathlib import Path

import pytest

from .helpers.assertions import assert_cross_surface_truth, assert_forbidden_mutations_absent
from .helpers.evidence import collect_evidence
from .helpers.launch import TERMINAL_STATUSES, launch_scenario
from .helpers.runtime_env import bootstrap_runtime_root, load_scenario, manifest_tree
from .helpers.wait import wait_for_terminal_receipt


pytestmark = [pytest.mark.e2e, pytest.mark.integration]


def _run_scenario(*, runtime_root: Path, scenario_name: str) -> dict:
    scenario = load_scenario("fcee", scenario_name)
    repo_root = bootstrap_runtime_root(runtime_root, scenario=scenario)
    before = manifest_tree(repo_root)
    launched = launch_scenario(scenario, repo_root=repo_root)
    receipt = wait_for_terminal_receipt(Path(launched["receipt_path"]), terminal_statuses=TERMINAL_STATUSES)
    evidence = collect_evidence(receipt_path=Path(receipt["receipt_path"]))
    after = manifest_tree(repo_root)
    assert_cross_surface_truth(evidence, scenario)
    allowed_prefixes = [f"runtime/state/e2e/action_plane/{scenario['name']}/"]
    allowed_prefixes.extend(list(scenario.get("allowed_mutation_prefixes") or []))
    assert_forbidden_mutations_absent(
        before,
        after,
        allowed_prefixes=allowed_prefixes,
    )
    return {"scenario": scenario, "evidence": evidence, "repo_root": repo_root, "output": launched["output"]}


def test_fcee_e2e_patch_pass_basic(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="patch_pass_basic")
    output = result["output"]
    assert output["report"]["final_chain_outcome"] == "completed_with_pass"
    assert result["evidence"]["receipt"]["recovery_used"] is False


def test_fcee_e2e_broken_patch_surfaces_proof_gap(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="broken_patch_proof_gap")
    output = result["output"]
    assert output["report"]["terminal_reason"] == "proof_gap_unresolved"
    assert result["evidence"]["receipt"]["proof_gap"] is True


def test_fcee_e2e_build_verify_recovery_to_pass(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="build_verify_recovery_to_pass")
    output = result["output"]
    assert output["final_chain_outcome"] == "completed_with_pass"
    assert len(output["iteration_reports"]) == 2
    assert result["evidence"]["receipt"]["recovery_used"] is True


def test_fcee_e2e_failed_verification_is_honest(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="failed_verification")
    output = result["output"]
    report = dict(output["iteration_reports"][-1])
    assert report["final_verifier_verdict"] == "FAIL"
    assert report["terminal_reason"] == "missing_pass_or_evidence"


def test_fcee_e2e_scope_rejected_stays_bounded(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="scope_rejected_status_reconcile")
    report = dict(result["output"]["report"])
    proof_gaps = []
    for row in report["step_records"]:
        proof_gaps.extend(list(row.get("proof_gaps") or []))
    assert "unauthorized_repo_mutation" in proof_gaps


def test_fcee_e2e_economic_stop_no_new_signal(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="economic_stop_no_new_signal")
    output = result["output"]
    report = dict(output["iteration_reports"][-1])
    assert report["terminal_reason"] == "economic_stop"


def test_fcee_e2e_full_stack_blocked_chain_with_upgrade_pressure(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="full_stack_blocked_chain_with_upgrade_pressure")
    output = result["output"]
    assert output["phase4"]["economic_decision_output"]["decision"] == "approved"
    assert output["phase4"]["upgrade_pipeline_output"]["proposal_created"] is True
    assert output["phase3"]["operator_surface"]["status_class"] == "degraded"


def test_fcee_e2e_phase3_blocked_patch_recommends_patch_action(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="phase3_blocked_patch_recommendation")
    surface = result["output"]["operator_surface"]
    assert surface["status_class"] == "blocked"
    assert surface["recommended_actions"][0]["action_id"] == "patch_apply_chain_v1"
    assert surface["codex_transparency"]["codex_used"] is True


def test_fcee_e2e_phase3_contradiction_prefers_reconcile(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="phase3_contradiction_prefers_reconcile")
    surface = result["output"]["operator_surface"]
    assert surface["status_class"] == "degraded"
    assert surface["recommended_actions"][0]["action_id"] == "status_reconcile_v1"


def test_fcee_e2e_phase3_pass_complete_is_quiet(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="phase3_pass_complete_no_recommendation")
    surface = result["output"]["operator_surface"]
    assert surface["status_class"] == "pass"
    assert surface["recommended_actions"] == []
    assert result["output"]["codex_usage_output"]["codex_used"] is False


def test_fcee_e2e_status_reconcile_success_bounded(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="status_reconcile_success_bounded")
    report = result["output"]["report"]
    assert report["final_chain_outcome"] == "completed_with_pass"
    assert report["final_verifier_verdict"] == "PASS"


def test_fcee_e2e_full_stack_rejected_upgrade_bad_economics(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="full_stack_rejected_upgrade_bad_economics")
    output = result["output"]
    assert output["phase4"]["economic_decision_output"]["decision"] == "rejected"
    assert output["phase4"]["upgrade_pipeline_output"]["proposal_created"] is False


def test_fcee_e2e_full_stack_unsafe_recurring_fallback(e2e_runtime_root: Path) -> None:
    result = _run_scenario(runtime_root=e2e_runtime_root, scenario_name="full_stack_unsafe_recurring_fallback")
    output = result["output"]
    assert output["economic_decision_output"]["decision"] == "rejected"
    assert output["candidate_output"]["candidates"] == []
    assert output["upgrade_pipeline_output"]["proposal_created"] is False
