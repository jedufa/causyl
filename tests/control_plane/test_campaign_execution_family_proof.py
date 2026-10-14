from __future__ import annotations

from pathlib import Path

from infra.controlplane import campaign_execution_family_proof as proof


def _bound_proof_artifact() -> Path:
    base = Path("governance/ledger/patch_instruction_sheets/causyl")
    rel = (
        Path("2026")
        / "04"
        / "PIS-20260409-121605Z-P4S7__prove-campaign-execution-contract-end-to-end"
        / "artifacts"
        / "verification"
        / "campaign_execution_family_proof.md"
    )
    for ledger_status in ("active", "verified"):
        candidate = base / ledger_status / rel
        if candidate.exists():
            return candidate
    return base / "active" / rel


def test_campaign_execution_family_proof_covers_happy_path_partial_ready_blocked_restart_and_invalid_claims(
    tmp_path: Path,
) -> None:
    report = proof.collect_campaign_execution_family_proof(repo_root=tmp_path)

    assert report["schema_version"] == "campaign_execution_family_proof_v1"
    assert report["proof_child_uid"] == "PIS-20260409-121605Z-P4S7"
    assert report["parent_uid"] == "PIS-20260409-121217Z-2A6A"
    assert report["child_uids"] == [
        "PIS-20260409-121600Z-J8M2",
        "PIS-20260409-121601Z-K9N3",
        "PIS-20260409-121602Z-L1P4",
        "PIS-20260409-121603Z-M2Q5",
        "PIS-20260409-121604Z-N3R6",
    ]
    assert report["all_cases_passed"] is True
    assert report["case_count"] == 5

    cases = {row["case_id"]: row for row in report["cases"]}

    happy = cases["happy_path_bounded_campaign_completion"]
    assert happy["record_ok"] is True
    assert happy["gate_ok"] is True
    assert happy["launch_ok"] is True
    assert happy["reconciled_campaign_state"] == "completed"
    assert happy["operator_readiness"] == "ready"
    assert happy["terminal_phase_ids"] == ["phase_define_contract", "phase_launch_execution"]
    assert happy["launch_contract_id"].startswith("phase_launch::")

    partial = cases["partial_ready_caveated_launch_remains_explicit"]
    assert partial["gate_ok"] is True
    assert partial["launch_ok"] is True
    assert partial["reconciled_campaign_state"] == "active"
    assert partial["launchable_phase_ids"] == ["phase_launch_execution"]
    assert partial["operator_readiness"] == "partial_ready"
    assert partial["next_step_kind"] == "launchable_phase"

    blocked = cases["blocked_gate_prevents_phase_progression"]
    assert blocked["launch_ok"] is False
    assert "gate_not_launchable" in blocked["launch_error_codes"]
    assert blocked["reconciled_campaign_state"] == "blocked"
    assert blocked["blocked_phase_ids"] == ["phase_launch_execution"]

    restart = cases["restart_reconciliation_keeps_review_gate_visible"]
    assert restart["reconciled_campaign_state"] == "review"
    assert restart["review_phase_ids"] == ["phase_launch_execution"]
    assert restart["operator_readiness"] == "waiting_review"
    assert restart["next_step_kind"] == "review_gate"

    invalid = cases["invalid_handoff_and_recursive_launch_claims_fail_closed"]
    assert invalid["gate_ok"] is False
    assert "missing_handoff_evidence" in invalid["gate_error_codes"]
    assert "partial_ready_requires_caveat" in invalid["gate_error_codes"]
    assert invalid["launch_ok"] is False
    assert any(
        "illegal parent-child job-shape relationship in v1: fanout_job -> fanout_job" in message
        for message in invalid["launch_error_messages"]
    )


def test_bound_campaign_execution_family_proof_artifact_lists_cases_children_and_antipatterns() -> None:
    artifact = _bound_proof_artifact()
    assert artifact.exists()
    body = artifact.read_text(encoding="utf-8")

    assert "PIS-20260409-121605Z-P4S7" in body
    assert "PIS-20260409-121217Z-2A6A" in body
    assert "PIS-20260409-121600Z-J8M2" in body
    assert "PIS-20260409-121601Z-K9N3" in body
    assert "PIS-20260409-121602Z-L1P4" in body
    assert "PIS-20260409-121603Z-M2Q5" in body
    assert "PIS-20260409-121604Z-N3R6" in body
    assert "happy_path_bounded_campaign_completion" in body
    assert "partial_ready_caveated_launch_remains_explicit" in body
    assert "blocked_gate_prevents_phase_progression" in body
    assert "restart_reconciliation_keeps_review_gate_visible" in body
    assert "invalid_handoff_and_recursive_launch_claims_fail_closed" in body
    assert "partial_ready_claimed_as_full_ready" in body
    assert "specialized verifier review now passed" in body
