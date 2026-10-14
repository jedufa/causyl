from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_campaign_jobs import CampaignJobCompileError, compile_runnable_jobs
from infra.controlplane.static_verification.static_verification_campaign_dag import CampaignDAGError, build_campaign_dag
from infra.controlplane.static_verification.static_verification_findings import normalize_findings
from infra.controlplane.static_verification.static_verification_finding_priority import prioritize_findings
from infra.controlplane.static_verification.static_verification_repairs import RepairSynthesisError, synthesize_repairs


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_phase_b_contract_artifacts_exist_for_first_five_children() -> None:
    roots = [
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170102Z-E8F9__compile-generated-scenarios-into-runnable-builder-and-verifier-jobs/artifacts/campaigns/runnable_scenario_contract_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170103Z-F9G1__define-verification-campaign-dag-fanout-retry-and-backpressure-policy/artifacts/campaigns/verification_campaign_dag_contract_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170104Z-G1H2__normalize-findings-cluster-duplicates-and-declare-repair-worthy-defect-candidates/artifacts/campaigns/finding_normalization_contract_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170105Z-H2J3__define-finding-severity-production-reachability-and-repair-priority-policy/artifacts/campaigns/finding_priority_policy_v1.json",
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260413-170106Z-J3K4__synthesize-bounded-repair-tasks-from-normalized-findings/artifacts/campaigns/repair_candidate_contract_v1.json",
    ]
    for rel in roots:
        assert (REPO_ROOT / rel).exists()


def test_e8f9_compiler_rejects_theatrical_non_runnable_scenarios() -> None:
    report = compile_runnable_jobs(
        {
            "scenarios": [
                {
                    "scenario_id": "scenario::1",
                    "execution_class": "runnable_now",
                    "scenario_summary": "real scenario",
                    "required_evidence": ["receipt"],
                },
                {
                    "scenario_id": "scenario::2",
                    "execution_class": "planning_only",
                    "scenario_summary": "theatrical but not runnable",
                },
            ]
        }
    )
    assert len(report["compiled_jobs"]) == 1
    assert report["rejected_scenarios"][0]["scenario_id"] == "scenario::2"
    with pytest.raises(CampaignJobCompileError):
        compile_runnable_jobs({"scenarios": [{"scenario_id": "", "execution_class": "runnable_now", "scenario_summary": ""}]})


def test_f9g1_dag_blocks_mixed_commit_fanout_and_duplicate_launches() -> None:
    with pytest.raises(CampaignDAGError) as excinfo:
        build_campaign_dag(
            {
                "compiled_jobs": [
                    {"job_id": "verify::1", "repo_commit_sha": "a"},
                    {"job_id": "verify::2", "repo_commit_sha": "b"},
                ]
            }
        )
    assert excinfo.value.code == "mixed_commit_fanout_blocked"

    with pytest.raises(CampaignDAGError) as excinfo2:
        build_campaign_dag(
            {"compiled_jobs": [{"job_id": "verify::1", "repo_commit_sha": "a"}, {"job_id": "verify::1", "repo_commit_sha": "a"}]}
        )
    assert excinfo2.value.code == "duplicate_launch_blocked"


def test_g1h2_normalization_collapses_duplicates_without_merging_distinct_failures() -> None:
    report = normalize_findings(
        {
            "raw_findings": [
                {
                    "finding_id": "f1",
                    "truth_boundary": "scheduler_truth",
                    "symptom_class": "stale_observation",
                    "operator_claim_under_attack": "fresh scheduler story",
                    "severity_hint": "high",
                    "production_reachability_hint": "reachable_in_normal_operation",
                    "affected_truths": ["scheduler_truth"],
                    "repair_signal": "repair_worthy",
                },
                {
                    "finding_id": "f2",
                    "truth_boundary": "scheduler_truth",
                    "symptom_class": "stale_observation",
                    "operator_claim_under_attack": "fresh scheduler story",
                    "severity_hint": "high",
                    "production_reachability_hint": "reachable_in_normal_operation",
                    "affected_truths": ["scheduler_truth"],
                    "repair_signal": "repair_worthy",
                },
                {
                    "finding_id": "f3",
                    "truth_boundary": "watchdog_truth",
                    "symptom_class": "unsafe_action",
                    "operator_claim_under_attack": "safe repair offered",
                    "severity_hint": "critical",
                    "production_reachability_hint": "reachable_under_degraded_or_faulted_operation",
                    "affected_truths": ["watchdog_truth"],
                    "repair_signal": "repair_worthy",
                },
            ]
        }
    )
    assert len(report["normalized_defect_candidates"]) == 2
    assert report["normalized_defect_candidates"][0]["evidence_count"] == 2


def test_h2j3_priority_prevents_low_value_duplicates_from_crowding_out_critical_findings() -> None:
    prioritized = prioritize_findings(
        {
            "normalized_defect_candidates": [
                {
                    "cluster_id": "watchdog_truth::unsafe_action::safe repair offered",
                    "evidence_count": 1,
                    "severity_hint": "critical",
                    "production_reachability_hint": "reachable_under_degraded_or_faulted_operation",
                    "affected_truths": ["watchdog_truth"],
                },
                {
                    "cluster_id": "scheduler_truth::minor_gap::small gap",
                    "evidence_count": 3,
                    "severity_hint": "low",
                    "production_reachability_hint": "reachable_in_normal_operation",
                    "affected_truths": ["scheduler_truth"],
                },
            ]
        }
    )
    assert prioritized["prioritized_findings"][0]["priority_class"] == "repair_now"
    assert prioritized["prioritized_findings"][0]["unattended_continuation_blocked"] is True


def test_j3k4_repair_synthesis_rejects_overbroad_repair_bundles() -> None:
    prioritized = {
        "prioritized_findings": [
            {
                "cluster_id": "repairable_one",
                "priority_class": "repair_now",
                "affected_truths": ["a", "b", "c"],
            }
        ]
    }
    with pytest.raises(RepairSynthesisError) as excinfo:
        synthesize_repairs(prioritized)
    assert excinfo.value.code == "overbroad_repair_bundle_rejected"

    repairs = synthesize_repairs(
        {
            "prioritized_findings": [
                {
                    "cluster_id": "repairable_two",
                    "priority_class": "repair_soon",
                    "affected_truths": ["scheduler_truth"],
                }
            ]
        }
    )
    assert repairs["repair_candidates"][0]["expected_follow_up_validation"] == ["targeted_revalidation", "family_level_rerun"]
