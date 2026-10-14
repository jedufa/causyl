from __future__ import annotations

from pathlib import Path

import pytest

from tests.control_plane.e2e.helpers.assertions import (
    assert_cross_surface_truth,
    assert_forbidden_mutations_absent,
)
from tests.control_plane.e2e.helpers.evidence import collect_evidence
from tests.control_plane.e2e.helpers.launch import TERMINAL_STATUSES, launch_scenario
from tests.control_plane.e2e.helpers.runtime_env import (
    bootstrap_runtime_root,
    load_scenario,
    manifest_tree,
)
from tests.control_plane.e2e.helpers.wait import wait_for_terminal_receipt


pytestmark = [pytest.mark.integration, pytest.mark.e2e]


SCENARIOS: list[dict[str, object]] = [
    {
        "name": "patch_pass_basic",
        "topic": "fcee",
        "kind": "patch_apply",
        "expected_status": "ok",
        "expected_outcome": "completed_with_pass",
    },
    {
        "name": "broken_patch_proof_gap",
        "topic": "fcee",
        "kind": "patch_apply",
        "expected_status": "proof_gap",
        "expected_terminal_reason": "proof_gap_unresolved",
    },
    {
        "name": "build_verify_recovery_to_pass",
        "topic": "fcee",
        "kind": "build_verify",
        "expected_status": "ok",
        "expected_outcome": "completed_with_pass",
        "expected_iterations": 2,
    },
    {
        "name": "status_reconcile_success_bounded",
        "topic": "fcee",
        "kind": "status_reconcile",
        "expected_status": "ok",
        "expected_outcome": "completed_with_pass",
    },
    {
        "name": "phase3_blocked_patch_recommendation",
        "topic": "fcee",
        "kind": "operator_intelligence",
        "expected_status": "proof_gap",
        "expected_primary_action": "patch_apply_chain_v1",
    },
    {
        "name": "phase3_contradiction_prefers_reconcile",
        "topic": "fcee",
        "kind": "operator_intelligence",
        "expected_status": "proof_gap",
        "expected_primary_action": "status_reconcile_v1",
    },
    {
        "name": "full_stack_blocked_chain_with_upgrade_pressure",
        "topic": "fcee",
        "kind": "all_phases",
        "expected_status": "proof_gap",
        "expected_phase4_decision": "approved",
    },
    {
        "name": "full_stack_rejected_upgrade_bad_economics",
        "topic": "fcee",
        "kind": "all_phases",
        "expected_status": "proof_gap",
        "expected_phase4_decision": "rejected",
    },
]


def _run_real_life_scenario(*, runtime_root: Path, topic: str, scenario_name: str) -> dict:
    scenario = load_scenario(topic, scenario_name)
    repo_root = bootstrap_runtime_root(runtime_root, scenario=scenario)
    before = manifest_tree(repo_root)
    launched = launch_scenario(scenario, repo_root=repo_root)
    receipt = wait_for_terminal_receipt(Path(launched["receipt_path"]), terminal_statuses=TERMINAL_STATUSES)
    evidence = collect_evidence(receipt_path=Path(receipt["receipt_path"]))
    after = manifest_tree(repo_root)
    assert_cross_surface_truth(evidence, scenario)
    allowed_prefixes = [f"runtime/state/e2e/action_plane/{scenario['name']}/"]
    allowed_prefixes.extend(list(scenario.get("allowed_mutation_prefixes") or []))
    assert_forbidden_mutations_absent(before, after, allowed_prefixes=allowed_prefixes)
    return {
        "scenario": scenario,
        "repo_root": repo_root,
        "output": launched["output"],
        "receipt": receipt,
        "evidence": evidence,
    }


@pytest.mark.parametrize("spec", SCENARIOS, ids=[str(s["name"]) for s in SCENARIOS])
def test_real_life_action_plane_use_case_matrix(spec: dict[str, object], tmp_path: Path) -> None:
    result = _run_real_life_scenario(
        runtime_root=tmp_path / f"use_case__{spec['name']}",
        topic=str(spec["topic"]),
        scenario_name=str(spec["name"]),
    )
    receipt = dict(result["receipt"])
    output = dict(result["output"])

    assert receipt["status"] == spec["expected_status"]

    kind = str(spec["kind"])
    if kind in {"patch_apply", "build_verify", "status_reconcile"}:
        report = dict(output.get("report") or {})
        if not report:
            reports = list(output.get("iteration_reports") or [])
            report = dict(reports[-1]) if reports else {}
        if "expected_outcome" in spec:
            assert report["final_chain_outcome"] == spec["expected_outcome"]
        if "expected_terminal_reason" in spec:
            assert report["terminal_reason"] == spec["expected_terminal_reason"]
        if "expected_iterations" in spec:
            assert len(list(output.get("iteration_reports") or [])) == int(spec["expected_iterations"])

    elif kind == "operator_intelligence":
        surface = dict(output.get("operator_surface") or {})
        recommendations = dict(surface.get("recommendations") or {})
        primary = dict(recommendations.get("primary") or {})
        assert primary.get("action_id") == spec["expected_primary_action"]
        assert surface.get("executive_surface", {}).get("state") in {"BLOCKED", "DEGRADED", "PASS", "RUNNING", "REVIEW_REQUIRED"}
        explanation = dict(output.get("explanation_output") or {})
        assert str(explanation.get("summary") or "").strip()
        uncertainty = dict(output.get("uncertainty_surface") or {})
        assert "proof_gap_present" in uncertainty

    elif kind == "all_phases":
        phase4 = dict(output.get("phase4") or {})
        economic = dict(phase4.get("economic_decision_output") or {})
        assert economic.get("decision") == spec["expected_phase4_decision"]
        checks = dict(output.get("checks") or {})
        assert checks.get("phase1_promotes_or_rejects_explicitly") is True
        assert checks.get("phase2_contracts_valid") is True
        assert checks.get("phase3_operator_flow_valid") is True
        assert checks.get("phase4_flow_valid") is True
        assert checks.get("phase4_targets_patch_apply_family") is True

    else:
        raise AssertionError(f"unsupported scenario kind: {kind}")


def test_real_life_suite_proves_operator_not_required(tmp_path: Path) -> None:
    executed: list[dict[str, object]] = []
    for spec in SCENARIOS:
        result = _run_real_life_scenario(
            runtime_root=tmp_path / f"operatorless__{spec['name']}",
            topic=str(spec["topic"]),
            scenario_name=str(spec["name"]),
        )
        executed.append(
            {
                "name": spec["name"],
                "status": result["receipt"]["status"],
                "entrypoint": result["scenario"]["entrypoint"],
            }
        )
    assert len(executed) == len(SCENARIOS)
    assert {row["status"] for row in executed} <= {"ok", "proof_gap", "economic_stop_no_new_signal", "failed_verification", "scope_rejected"}
    assert any(row["entrypoint"] == "build_action_plane_all_phases_report_v1" for row in executed)
    assert any(row["entrypoint"] == "execute_phase3_blocked_chain_explain_and_recommend_v1" for row in executed)
