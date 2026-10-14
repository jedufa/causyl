from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import fanout_master_contract as contract


def test_fanout_master_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.fanout_master_contract_v1()

    assert payload.version == "fanout_master_contract_v1"
    assert payload.uid == "PIS-20260409-082100Z-A1F1"
    assert payload.canonical_artifact_rel.as_posix() == "governance/fanout_master_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_fanout_master_ownership_v1.md"
    assert payload.master_owner == "fanout_master_release_one"
    assert payload.scheduler_terminal_stage == "terminal_reporting"


def test_fanout_master_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/fanout_master_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["lifecycle_stages"].keys()) == contract.LIFECYCLE_STAGES
    assert tuple(payload["durable_truth_fields"]) == contract.DURABLE_TRUTH_FIELDS
    assert tuple(payload["forbidden_child_claims"].keys()) == contract.FORBIDDEN_CHILD_CLAIMS


def test_fanout_master_contract_validation_passes() -> None:
    assert contract.validate_fanout_master_contract_v1() == ()


def test_project_fanout_state_maps_runtime_state_to_terminal_reporting_stage() -> None:
    projection = contract.project_fanout_state(
        {
            "status": "finished",
            "phase": "completed",
            "workers": [
                {"worker_id": "maker_1", "status": "delivered"},
                {"worker_id": "maker_2", "status": "delivered_with_known_gaps"},
            ],
            "integration": {"status": "integrated_partial"},
        },
        as_terminal_report=True,
        integrated_result={
            "restart_reconciliation": {"status": "partial_collection_reconciled"},
            "retry_boundaries": {"child_relaunch": {"state": "allowed"}},
        },
    )

    assert projection["fanout_stage"] == "terminal_reporting"
    assert projection["master_owner"] == "fanout_master_release_one"
    assert projection["child_roster"] == ["maker_1", "maker_2"]
    assert projection["child_statuses"]["maker_2"] == "delivered_with_known_gaps"
    assert projection["integration_disposition"] == "integrated_partial"
    assert projection["restart_reconciliation"]["status"] == "partial_collection_reconciled"
    assert projection["retry_boundaries"]["child_relaunch"]["state"] == "allowed"


def test_child_claim_violations_reject_master_owned_truth_drift() -> None:
    violations = contract.child_claim_violations(
        [
            {"worker_id": "maker_1", "status": "delivered"},
            {"worker_id": "maker_2", "status": "delivered", "final_verdict": "PASS", "integrator_owner": "maker_2"},
        ]
    )

    assert violations == (
        {
            "worker_id": "maker_2",
            "forbidden_claims": ["integrator_owner", "final_verdict"],
        },
    )
