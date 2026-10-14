from __future__ import annotations

from infra.controlplane import split_topology_contract as contract


def test_split_topology_snapshot_exposes_expected_contract_surfaces() -> None:
    payload = contract.split_topology_snapshot()

    assert payload["schema_version"] == "lab_assistant_split_topology_contract_v1"
    assert payload["uid"] == "PIS-20260313-091500Z-ABCD"
    assert payload["children"] == [
        "PIS-20260313-100604Z-Q3U5",
        "PIS-20260313-100605Z-R4V6",
        "PIS-20260313-100606Z-S5W7",
    ]
    assert payload["proof_suite_uid"] == "PIS-20260313-100607Z-T6X8"
    assert "cold_start_reconstruct_from_durable_truth" in payload["replacement_backend"]["states"]
    assert "non_authoritative_rejoin" in payload["backend_rejoin"]["states"]


def test_replacement_backend_classification_fails_closed_without_durable_truth() -> None:
    result = contract.classify_replacement_backend_startup(
        has_durable_truth=False,
        continuity_proven=False,
        duplicate_launch_risk=False,
    )

    assert result["state"] == "blocked_missing_durable_truth"
    assert result["authoritative"] is False


def test_backend_rejoin_defaults_non_authoritative_until_reproved() -> None:
    result = contract.classify_backend_rejoin(
        durable_truth_present=True,
        local_state_is_stale=True,
        authority_proven=False,
    )

    assert result["state"] == "non_authoritative_rejoin"
    assert result["authoritative"] is False


def test_runtime_control_guard_rejects_second_live_authority() -> None:
    result = contract.runtime_control_guard(
        topology_mode="split",
        requested_action="start_scheduler_owner",
        execution_owner_live=True,
    )

    assert result["ok"] is False
    assert result["reason_code"] == "authority_conflict"


def test_split_topology_snapshot_validation_passes() -> None:
    result = contract.validate_split_topology_snapshot()
    assert result["status"] == "pass"
    assert result["errors"] == []
