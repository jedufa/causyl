from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_phase_b_anchor import PhaseBAnchorError, run_phase_b_anchor


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_q9r1_anchor_artifact_exists() -> None:
    path = REPO_ROOT / (
        "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
        "PIS-20260413-170112Z-Q9R1__prove-one-generated-scenario-runs-normalizes-into-repair-and-closes-with-revalidation-and-dut-delta/"
        "artifacts/campaigns/phase_b_anchor_contract_v1.json"
    )
    assert path.exists()


def test_q9r1_anchor_runs_one_generated_scenario_end_to_end() -> None:
    report = run_phase_b_anchor(
        {
            "scenarios": [
                {
                    "scenario_id": "scenario::active-busy-masks-storage-failure",
                    "execution_class": "runnable_now",
                    "scenario_summary": "storage failure must not be hidden by active_busy",
                    "required_evidence": ["job_receipt", "finding_receipt"],
                    "repo_commit_sha": "abc123",
                    "repair_triggerable": True,
                }
            ],
            "raw_findings": [
                {
                    "finding_id": "finding::1",
                    "truth_boundary": "watchdog_truth",
                    "symptom_class": "unsafe_action",
                    "operator_claim_under_attack": "safe repair offered",
                    "severity_hint": "critical",
                    "production_reachability_hint": "reachable_in_normal_operation",
                    "affected_truths": ["watchdog_truth"],
                    "repair_signal": "repair_worthy",
                }
            ],
            "budget": {"max_budget_units": 4},
            "actual": {
                "spent_budget_units": 2,
                "repeated_cluster_ratio": 0.0,
                "new_signal_ratio": 0.8,
                "dut_improvement_delta": 0.2,
                "repair_confidence": 0.7,
                "risk_score": 0.2,
                "completed_iterations": 1,
            },
            "baseline_health_snapshot": {"open_bug_classes": 3, "critical_open": 1},
            "post_campaign_health_snapshot": {"open_bug_classes": 2, "critical_open": 0},
            "reduced_bug_classes": ["active_busy_masking"],
            "unresolved_bug_classes": ["long_run_unsupervised_unproven"],
            "residual_risk": "still_not_safe_unattended",
            "blocked_claims": ["broad_robustness_unproven"],
        }
    )
    assert report["compiled_jobs"][0]["job_id"].startswith("verify::")
    assert report["normalized_defect_candidates"][0]["repair_worthy"] is True
    assert report["repair_candidates"][0]["repair_candidate_id"].startswith("repair::")
    assert report["revalidation_plans"][0]["promotion_rule"] == "accepted_only_after_targeted_and_broader_revalidation_pass"
    assert report["dut_health_delta"]["net_dut_delta"] == "improved"
    assert report["stop_reason"] == "continue"


def test_q9r1_anchor_fails_if_no_normalized_finding_exists() -> None:
    with pytest.raises(PhaseBAnchorError) as excinfo:
        run_phase_b_anchor(
            {
                "scenarios": [
                    {
                        "scenario_id": "scenario::empty",
                        "execution_class": "runnable_now",
                        "scenario_summary": "valid job but no finding",
                        "required_evidence": ["job_receipt"],
                    }
                ],
                "raw_findings": [],
                "baseline_health_snapshot": {"open_bug_classes": 1},
                "post_campaign_health_snapshot": {"open_bug_classes": 1},
            }
        )
    assert excinfo.value.code == "no_normalized_finding"
