from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import job_budget_cost_ledger_contract as contract


def test_job_budget_cost_ledger_contract_v1_exposes_expected_paths_and_reason_codes() -> None:
    payload = contract.job_budget_cost_ledger_contract_v1()

    assert payload.version == "job_budget_cost_ledger_contract_v1"
    assert payload.uid == "PIS-20260406-183206Z-AC5E"
    assert payload.canonical_artifact_rel.as_posix() == "governance/job_budget_cost_ledger_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_job_budget_cost_ledger_schema_v1.md"
    assert payload.storage_root_rel.as_posix() == "runtime/state/job_budget_cost_ledger"
    assert payload.allocation_log_filename == "allocations.jsonl"
    assert payload.cost_event_log_filename == "cost_events.jsonl"
    assert payload.allocation_reason_codes == (
        "initial_grant",
        "top_up",
        "manual_correction",
        "recovery_correction",
    )
    assert payload.cost_reason_codes == (
        "reservation",
        "usage",
        "refund",
        "manual_correction",
        "recovery_correction",
    )
    assert payload.cost_directions == ("debit", "credit")


def test_job_budget_cost_ledger_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/job_budget_cost_ledger_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert payload["storage_root_rel"] == contract.STORAGE_ROOT_REL.as_posix()
    assert payload["storage_layout"]["allocation_log_filename"] == contract.ALLOCATION_LOG_FILENAME
    assert payload["storage_layout"]["cost_event_log_filename"] == contract.COST_EVENT_LOG_FILENAME
    assert payload["replay_rules"]["remaining_budget_formula"] == contract.REMAINING_BUDGET_FORMULA
    assert tuple(payload["replay_rules"]["deterministic_order"]) == contract.DETERMINISTIC_REPLAY_ORDER
    assert tuple(payload["allocation_reason_codes"]) == contract.ALLOCATION_REASON_CODES
    assert tuple(payload["cost_reason_codes"]) == contract.COST_REASON_CODES
    assert tuple(payload["replay_rules"]["forbidden_derived_fields"]) == contract.FORBIDDEN_DERIVED_FIELDS


def test_job_budget_cost_ledger_contract_validation_passes() -> None:
    assert contract.validate_job_budget_cost_ledger_contract_v1() == ()


def test_contract_validation_rejects_replay_rule_drift(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/job_budget_cost_ledger_contract_v1.json").read_text(encoding="utf-8"))
    payload["replay_rules"]["deterministic_order"] = ["record_kind", "recorded_at_utc", "entry_uid"]
    contract_path = tmp_path / contract.CONTRACT_ARTIFACT_REL
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload), encoding="utf-8")

    issues = contract.validate_job_budget_cost_ledger_contract_v1(repo_root=tmp_path)

    assert issues == ("replay_rules.deterministic_order drifted from the canonical v1 order",)


def test_ledger_paths_for_job_use_canonical_partition_and_file_names() -> None:
    paths = contract.ledger_paths_for_job("job_123")

    assert paths["job_dir"].as_posix() == "runtime/state/job_budget_cost_ledger/jobs/job_123"
    assert paths["allocation_log_rel"].as_posix().endswith("/job_123/allocations.jsonl")
    assert paths["cost_event_log_rel"].as_posix().endswith("/job_123/cost_events.jsonl")


def test_replay_job_budget_balance_tracks_allocations_debits_and_credits() -> None:
    balance = contract.replay_job_budget_balance(
        [
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "allocation_uid": "alloc_1",
                "job_uid": "job_123",
                "recorded_at_utc": "2026-04-06T18:00:00Z",
                "amount_minor_units": 1_000,
                "unit": "usd_cents",
                "allocation_reason_code": "initial_grant",
            },
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "allocation_uid": "alloc_2",
                "job_uid": "job_123",
                "recorded_at_utc": "2026-04-06T18:05:00Z",
                "amount_minor_units": 250,
                "unit": "usd_cents",
                "allocation_reason_code": "top_up",
            },
        ],
        [
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "cost_event_uid": "cost_1",
                "job_uid": "job_123",
                "recorded_at_utc": "2026-04-06T18:10:00Z",
                "amount_minor_units": 300,
                "unit": "usd_cents",
                "direction": "debit",
                "cost_reason_code": "usage",
            },
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "cost_event_uid": "cost_2",
                "job_uid": "job_123",
                "recorded_at_utc": "2026-04-06T18:11:00Z",
                "amount_minor_units": 50,
                "unit": "usd_cents",
                "direction": "credit",
                "cost_reason_code": "refund",
            },
        ],
        job_uid="job_123",
    )

    assert balance == {
        "job_uid": "job_123",
        "unit": "usd_cents",
        "allocated_minor_units": 1_250,
        "cost_debit_minor_units": 300,
        "cost_credit_minor_units": 50,
        "remaining_minor_units": 1_000,
    }


def test_replay_rejects_malformed_or_partial_entries() -> None:
    with pytest.raises(ValueError, match="allocation record missing required fields: unit"):
        contract.validate_allocation_record(
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "allocation_uid": "alloc_1",
                "job_uid": "job_123",
                "recorded_at_utc": "2026-04-06T18:00:00Z",
                "amount_minor_units": 100,
                "allocation_reason_code": "initial_grant",
            }
        )

    with pytest.raises(ValueError, match="remaining_budget_minor_units is derived-only"):
        contract.validate_cost_event_record(
            {
                "schema_version": "job_budget_cost_ledger_record_v1",
                "cost_event_uid": "cost_1",
                "job_uid": "job_123",
                "recorded_at_utc": "2026-04-06T18:10:00Z",
                "amount_minor_units": 100,
                "unit": "usd_cents",
                "direction": "debit",
                "cost_reason_code": "usage",
                "remaining_budget_minor_units": 900,
            }
        )


def test_replay_rejects_unit_drift_and_duplicate_ids() -> None:
    with pytest.raises(ValueError, match="share exactly one unit"):
        contract.replay_job_budget_balance(
            [
                {
                    "schema_version": "job_budget_cost_ledger_record_v1",
                    "allocation_uid": "alloc_1",
                    "job_uid": "job_123",
                    "recorded_at_utc": "2026-04-06T18:00:00Z",
                    "amount_minor_units": 100,
                    "unit": "usd_cents",
                    "allocation_reason_code": "initial_grant",
                }
            ],
            [
                {
                    "schema_version": "job_budget_cost_ledger_record_v1",
                    "cost_event_uid": "cost_1",
                    "job_uid": "job_123",
                    "recorded_at_utc": "2026-04-06T18:10:00Z",
                    "amount_minor_units": 20,
                    "unit": "token_milliunits",
                    "direction": "debit",
                    "cost_reason_code": "usage",
                }
            ],
            job_uid="job_123",
        )

    with pytest.raises(ValueError, match="duplicate allocation_uid encountered during replay: alloc_1"):
        contract.replay_job_budget_balance(
            [
                {
                    "schema_version": "job_budget_cost_ledger_record_v1",
                    "allocation_uid": "alloc_1",
                    "job_uid": "job_123",
                    "recorded_at_utc": "2026-04-06T18:00:00Z",
                    "amount_minor_units": 100,
                    "unit": "usd_cents",
                    "allocation_reason_code": "initial_grant",
                },
                {
                    "schema_version": "job_budget_cost_ledger_record_v1",
                    "allocation_uid": "alloc_1",
                    "job_uid": "job_123",
                    "recorded_at_utc": "2026-04-06T18:01:00Z",
                    "amount_minor_units": 25,
                    "unit": "usd_cents",
                    "allocation_reason_code": "top_up",
                },
            ],
            [],
            job_uid="job_123",
        )
