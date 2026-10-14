from __future__ import annotations

import json
from pathlib import Path

from tools.pis_improvement_intent_validation import build_validation_report


def test_pis_improvement_intent_validation_writes_blocked_report_with_positive_and_negative_scenarios(
    tmp_path: Path,
) -> None:
    repo_root = Path.cwd()
    report = build_validation_report(
        repo_root=tmp_path,
        report_path=Path("artifacts/intent_validation/pis_improvement_intent_validation.json"),
        implementation_repo_root=repo_root,
        now_fn=lambda: "2026-04-12T20:30:00Z",
    )

    report_path = tmp_path / "artifacts/intent_validation/pis_improvement_intent_validation.json"
    loaded = json.loads(report_path.read_text(encoding="utf-8"))
    scenario_by_id = {row["scenario_id"]: row for row in loaded["scenario_results"]}

    assert loaded["schema_version"] == "pis_improvement_intent_validation_v1"
    assert loaded["uid"] == "PIS-20260412-062104Z-E5G7"
    assert loaded["validation_outcome"] in {
        "blocked_pending_family_reconciliation",
        "ready_for_closeout_or_reconciled",
    }
    assert sorted(row["uid"] for row in loaded["implementation_child_inputs"]) == [
        "PIS-20260412-062100Z-A1C3",
        "PIS-20260412-062101Z-B2D4",
        "PIS-20260412-062102Z-C3E5",
        "PIS-20260412-062103Z-D4F6",
    ]
    assert scenario_by_id["recent_selection_positive"]["observed"]["recent_uids"][:2] == [
        "PIS-20260412-060451Z-EB85",
        "PIS-20260412-062103Z-D4F6",
    ]
    assert scenario_by_id["cli_launch_positive"]["observed"]["status"] == "resumable"
    assert scenario_by_id["cli_launch_positive"]["observed"]["recommendation_status"] == "pending"
    assert scenario_by_id["completed_session_contract_validation_positive"]["observed"]["status"] == "completed"
    assert scenario_by_id["completed_session_contract_validation_positive"]["observed"]["current_iteration"] == 1
    assert scenario_by_id["completed_session_contract_validation_positive"]["observed"]["session_contract_ok"] is True
    assert scenario_by_id["invalid_source_selection_negative"]["observed"]["error_code"] == "invalid_request"
    assert scenario_by_id["generic_praise_rejected_negative"]["observed"]["error_code"] == "invalid_critique_packet"
    assert scenario_by_id["iteration_cap_exceeded_negative"]["observed"]["error_code"] == "iteration_cap_exceeded"
    if loaded["ledger_truth"]["blockers"]:
        assert loaded["ledger_truth"]["fail_closed_status_preserved"] is True
        assert loaded["ledger_truth"]["source_intent_status"] == "draft"
        assert loaded["ledger_truth"]["parent_status"] == "draft"
        assert "PIS-20260412-062102Z-C3E5" in loaded["ledger_truth"]["unverified_implementation_children"]
        assert "PIS-20260412-062103Z-D4F6" in loaded["ledger_truth"]["unverified_implementation_children"]
    else:
        assert loaded["ledger_truth"]["source_intent_status"] == "verified"
        assert loaded["ledger_truth"]["parent_status"] == "verified"
        assert loaded["ledger_truth"]["unverified_implementation_children"] == []
    assert "no hidden transcript-dependent evidence was used" in loaded["non_claims"]
    assert loaded["evidence_paths"]["validation_report"] == "artifacts/intent_validation/pis_improvement_intent_validation.json"
    assert loaded["artifact_fingerprint"]
    assert report["artifact_fingerprint"] == loaded["artifact_fingerprint"]


def test_pis_improvement_intent_validation_requirement_coverage_maps_to_scenarios(tmp_path: Path) -> None:
    loaded = build_validation_report(
        repo_root=tmp_path,
        report_path=Path("artifacts/intent_validation/pis_improvement_intent_validation.json"),
        implementation_repo_root=Path.cwd(),
        now_fn=lambda: "2026-04-12T20:31:00Z",
    )

    coverage = {row["requirement_id"]: row for row in loaded["requirement_coverage"]}

    assert coverage["recent_pis_selection"]["status"] == "proven_in_fixture_runs"
    assert coverage["structured_critique"]["scenario_ids"] == ["generic_praise_rejected_negative"]
    assert coverage["persistent_revision_history"]["status"] == "proven_in_fixture_runs"
    assert set(coverage["deterministic_iteration_policy"]["scenario_ids"]) == {
        "completed_session_contract_validation_positive",
        "iteration_cap_exceeded_negative",
    }
    assert coverage["shared_cli_or_agent_contract_behavior"]["status"] == "proven_in_fixture_runs"
    assert coverage["fail_closed_ledger_truth"]["status"] in {
        "blocked_by_current_ledger_truth",
        "reconciled",
    }
