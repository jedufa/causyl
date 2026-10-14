from __future__ import annotations

import multiprocessing
from pathlib import Path

import pytest

from infra.controlplane import job_budget_cost_ledger_store as store


def _allocation_record(allocation_uid: str, *, amount: int, job_uid: str = "job_123") -> dict:
    return {
        "schema_version": "job_budget_cost_ledger_record_v1",
        "allocation_uid": allocation_uid,
        "job_uid": job_uid,
        "recorded_at_utc": "2026-04-06T18:00:00Z",
        "amount_minor_units": amount,
        "unit": "usd_cents",
        "allocation_reason_code": "initial_grant",
    }


def _cost_record(
    cost_event_uid: str,
    *,
    amount: int,
    recorded_at_utc: str,
    direction: str = "debit",
    reason: str = "usage",
    job_uid: str = "job_123",
) -> dict:
    return {
        "schema_version": "job_budget_cost_ledger_record_v1",
        "cost_event_uid": cost_event_uid,
        "job_uid": job_uid,
        "recorded_at_utc": recorded_at_utc,
        "amount_minor_units": amount,
        "unit": "usd_cents",
        "direction": direction,
        "cost_reason_code": reason,
    }


def test_append_and_restart_safe_replay_queries_use_durable_files(tmp_path: Path) -> None:
    store.append_allocation_record(_allocation_record("alloc_1", amount=1_000), repo_root=tmp_path)
    store.append_cost_event_record(
        _cost_record("cost_1", amount=250, recorded_at_utc="2026-04-06T18:10:00Z"),
        repo_root=tmp_path,
    )
    store.append_cost_event_record(
        _cost_record(
            "cost_2",
            amount=50,
            recorded_at_utc="2026-04-06T18:11:00Z",
            direction="credit",
            reason="refund",
        ),
        repo_root=tmp_path,
    )

    paths = store.ledger_paths_for_job("job_123", repo_root=tmp_path)
    assert paths["allocation_log"].exists()
    assert paths["cost_event_log"].exists()

    assert store.total_allocated_minor_units("job_123", repo_root=tmp_path) == 1_000
    assert store.total_spent_minor_units("job_123", repo_root=tmp_path) == 200
    assert store.remaining_budget_minor_units("job_123", repo_root=tmp_path) == 800
    assert store.replay_job_budget_state("job_123", repo_root=tmp_path)["unit"] == "usd_cents"
    assert store.last_durable_event("job_123", repo_root=tmp_path) == {
        "schema_version": "job_budget_cost_ledger_record_v1",
        "cost_event_uid": "cost_2",
        "job_uid": "job_123",
        "recorded_at_utc": "2026-04-06T18:11:00Z",
        "amount_minor_units": 50,
        "unit": "usd_cents",
        "direction": "credit",
        "cost_reason_code": "refund",
        "record_kind": "cost_event",
        "entry_uid": "cost_2",
    }


def test_replay_rejects_malformed_durable_entries(tmp_path: Path) -> None:
    paths = store.ledger_paths_for_job("job_123", repo_root=tmp_path)
    paths["allocation_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["allocation_log"].write_text('{"schema_version":"job_budget_cost_ledger_record_v1"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="malformed JSONL entry"):
        store.read_allocation_records("job_123", repo_root=tmp_path)


def _append_allocation_in_process(repo_root: str, index: int) -> None:
    record = {
        "schema_version": "job_budget_cost_ledger_record_v1",
        "allocation_uid": f"alloc_{index}",
        "job_uid": "job_123",
        "recorded_at_utc": f"2026-04-06T18:00:{index:02d}Z",
        "amount_minor_units": 10,
        "unit": "usd_cents",
        "allocation_reason_code": "top_up",
    }
    store.append_allocation_record(record, repo_root=Path(repo_root))


def test_concurrent_process_writers_do_not_lose_or_duplicate_events(tmp_path: Path) -> None:
    workers = [
        multiprocessing.Process(target=_append_allocation_in_process, args=(str(tmp_path), idx))
        for idx in range(12)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    allocations = store.read_allocation_records("job_123", repo_root=tmp_path)
    assert len(allocations) == 12
    assert {row["allocation_uid"] for row in allocations} == {f"alloc_{idx}" for idx in range(12)}
    assert store.total_allocated_minor_units("job_123", repo_root=tmp_path) == 120


def test_append_cost_event_record_once_deduplicates_identical_checkpoint_replays(tmp_path: Path) -> None:
    record = _cost_record("run_001:launch", amount=1, recorded_at_utc="2026-04-06T18:10:00Z")

    first = store.append_cost_event_record_once(record, repo_root=tmp_path)
    second = store.append_cost_event_record_once(record, repo_root=tmp_path)

    assert first["deduped"] is False
    assert second["deduped"] is True
    rows = store.read_cost_event_records("job_123", repo_root=tmp_path)
    assert rows == [record]


def test_append_allocation_record_once_deduplicates_identical_retries(tmp_path: Path) -> None:
    record = _allocation_record("alloc_001", amount=100)

    first = store.append_allocation_record_once(record, repo_root=tmp_path)
    second = store.append_allocation_record_once(record, repo_root=tmp_path)

    assert first["deduped"] is False
    assert second["deduped"] is True
    rows = store.read_allocation_records("job_123", repo_root=tmp_path)
    assert rows == [record]


def test_append_allocation_record_once_rejects_conflicting_duplicate_uid(tmp_path: Path) -> None:
    store.append_allocation_record_once(_allocation_record("alloc_001", amount=100), repo_root=tmp_path)

    with pytest.raises(ValueError, match="already exists with different content"):
        store.append_allocation_record_once(_allocation_record("alloc_001", amount=200), repo_root=tmp_path)


def test_append_cost_event_record_once_rejects_conflicting_duplicate_uid(tmp_path: Path) -> None:
    store.append_cost_event_record_once(
        _cost_record("run_001:launch", amount=1, recorded_at_utc="2026-04-06T18:10:00Z"),
        repo_root=tmp_path,
    )

    with pytest.raises(ValueError, match="already exists with different content"):
        store.append_cost_event_record_once(
            _cost_record("run_001:launch", amount=2, recorded_at_utc="2026-04-06T18:11:00Z"),
            repo_root=tmp_path,
        )


def test_ensure_recovery_outcome_records_overspend_and_exposes_latest_policy(tmp_path: Path) -> None:
    store.append_allocation_record(_allocation_record("alloc_1", amount=100), repo_root=tmp_path)
    store.append_cost_event_record(
        _cost_record("cost_1", amount=125, recorded_at_utc="2026-04-06T18:10:00Z"),
        repo_root=tmp_path,
    )

    ensured = store.ensure_recovery_outcome_recorded(
        "job_123",
        recorded_at_utc="2026-04-06T18:11:00Z",
        repo_root=tmp_path,
    )

    assert ensured["status"] == "blocked"
    assert ensured["allowed"] is False
    rows = store.read_recovery_outcome_records("job_123", repo_root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "overspend_detected"
    assert rows[0]["follow_up_policy"] == "block_future_runs"
    assert rows[0]["actor"] == "system"
    assert rows[0]["evidence"]["remaining_minor_units"] == -25


def test_manual_recovery_outcome_appends_audit_trail_and_changes_effective_policy(tmp_path: Path) -> None:
    store.append_recovery_outcome_record(
        {
            "schema_version": store.RECOVERY_OUTCOME_SCHEMA_VERSION,
            "recovery_outcome_uid": "job_123:overspend:cost_1:25",
            "job_uid": "job_123",
            "recorded_at_utc": "2026-04-06T18:11:00Z",
            "actor": "system",
            "reason_code": "overspend_detected",
            "follow_up_policy": "block_future_runs",
            "message": "overspend detected",
        },
        repo_root=tmp_path,
    )
    store.append_recovery_outcome_record(
        {
            "schema_version": store.RECOVERY_OUTCOME_SCHEMA_VERSION,
            "recovery_outcome_uid": "job_123:manual_override:001",
            "job_uid": "job_123",
            "recorded_at_utc": "2026-04-06T18:12:00Z",
            "actor": "operator",
            "reason_code": "manual_override",
            "follow_up_policy": "allow_future_runs",
            "message": "operator approved continued runs",
            "evidence": {"ticket": "OPS-001"},
        },
        repo_root=tmp_path,
    )

    rows = store.read_recovery_outcome_records("job_123", repo_root=tmp_path)
    latest = store.latest_recovery_outcome("job_123", repo_root=tmp_path)
    state = store.recovery_follow_up_state("job_123", repo_root=tmp_path)

    assert [row["reason_code"] for row in rows] == ["overspend_detected", "manual_override"]
    assert latest is not None
    assert latest["reason_code"] == "manual_override"
    assert latest["evidence"]["ticket"] == "OPS-001"
    assert state["status"] == "clear"
    assert state["allowed"] is True


def test_ensure_recovery_outcome_records_ledger_inconsistency_for_manual_review(tmp_path: Path) -> None:
    paths = store.ledger_paths_for_job("job_123", repo_root=tmp_path)
    paths["allocation_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["allocation_log"].write_text("{not-json}\n", encoding="utf-8")

    ensured = store.ensure_recovery_outcome_recorded(
        "job_123",
        recorded_at_utc="2026-04-06T18:11:00Z",
        repo_root=tmp_path,
    )

    assert ensured["status"] == "review_required"
    assert ensured["allowed"] is False
    rows = store.read_recovery_outcome_records("job_123", repo_root=tmp_path)
    assert len(rows) == 1
    assert rows[0]["reason_code"] == "ledger_inconsistency_detected"
    assert rows[0]["follow_up_policy"] == "require_manual_review"
