from __future__ import annotations

import importlib
import multiprocessing
from pathlib import Path

import pytest

from experiments_shared import job_family_resource_key
from infra.controlplane import shared_resource_claim_ledger as ledger


RESOURCE = {
    "resource_class": "workspace",
    "scope": "repo",
    "locator": "runtime/state/lab_assistant/task_register/task_register.json",
    "canonical_key": "workspace:repo:runtime/state/lab_assistant/task_register/task_register.json",
}


def _claim_record(
    entry_uid: str,
    claim_uid: str,
    claimant_uid: str,
    *,
    status: str,
    recorded_at_utc: str,
    resource: dict | None = None,
    wait_reason_code: str = "",
    detail: str = "",
) -> dict:
    return {
        "schema_version": ledger.RECORD_SCHEMA_VERSION,
        "entry_uid": entry_uid,
        "claim_uid": claim_uid,
        "claimant_uid": claimant_uid,
        "resource": dict(resource or RESOURCE),
        "recorded_at_utc": recorded_at_utc,
        "status": status,
        "wait_reason_code": wait_reason_code,
        "detail": detail,
    }


def _recovery_record(
    recovery_uid: str,
    claim_uid: str,
    claimant_uid: str,
    *,
    resource: dict | None = None,
    recorded_at_utc: str,
    action: str,
    reason_code: str = "stale_claim",
    detail: str = "",
) -> dict:
    return {
        "schema_version": ledger.RECOVERY_RECORD_SCHEMA_VERSION,
        "recovery_uid": recovery_uid,
        "claim_uid": claim_uid,
        "claimant_uid": claimant_uid,
        "resource": dict(resource or RESOURCE),
        "recorded_at_utc": recorded_at_utc,
        "action": action,
        "reason_code": reason_code,
        "detail": detail,
    }


def test_claim_ledger_replays_restart_safe_occupancy_and_lineage(tmp_path: Path) -> None:
    ledger.append_claim_record(
        _claim_record(
            "claim_a:waiting",
            "claim_a",
            "worker.alpha",
            status="waiting",
            recorded_at_utc="2026-04-07T05:10:00Z",
            wait_reason_code="awaiting_capacity",
            detail="admission check pending",
        ),
        repo_root=tmp_path,
    )
    ledger.append_claim_record(
        _claim_record(
            "claim_a:acquired",
            "claim_a",
            "worker.alpha",
            status="acquired",
            recorded_at_utc="2026-04-07T05:10:01Z",
        ),
        repo_root=tmp_path,
    )
    ledger.append_claim_record(
        _claim_record(
            "claim_a:released",
            "claim_a",
            "worker.alpha",
            status="released",
            recorded_at_utc="2026-04-07T05:10:02Z",
        ),
        repo_root=tmp_path,
    )
    ledger.append_claim_record(
        _claim_record(
            "claim_b:waiting",
            "claim_b",
            "worker.beta",
            status="waiting",
            recorded_at_utc="2026-04-07T05:10:03Z",
            wait_reason_code="resource_busy",
            detail="plane replay shows the resource still occupied elsewhere",
        ),
        repo_root=tmp_path,
    )

    reloaded = importlib.reload(ledger)

    lineage = reloaded.read_claim_lineage("claim_a", repo_root=tmp_path)
    occupancy = reloaded.replay_resource_occupancy(RESOURCE, repo_root=tmp_path)
    state = reloaded.current_claim_state("claim_a", repo_root=tmp_path)
    waits = reloaded.wait_reasons_for_claimant("worker.beta", repo_root=tmp_path)

    assert [row["status"] for row in lineage] == ["waiting", "acquired", "released"]
    assert state is not None
    assert state["status"] == "released"
    assert state["active"] is False
    assert occupancy["active_claim_count"] == 0
    assert occupancy["waiting_claim_count"] == 1
    assert occupancy["waiting_claims"][0]["claim_uid"] == "claim_b"
    assert waits["wait_reasons"] == [
        {
            "claim_uid": "claim_b",
            "resource": dict(RESOURCE),
            "wait_reason_code": "resource_busy",
            "detail": "plane replay shows the resource still occupied elsewhere",
            "latest_entry_uid": "claim_b:waiting",
            "latest_recorded_at_utc": "2026-04-07T05:10:03Z",
        }
    ]


def test_append_claim_record_once_deduplicates_identical_retries(tmp_path: Path) -> None:
    record = _claim_record(
        "claim_a:waiting",
        "claim_a",
        "worker.alpha",
        status="waiting",
        recorded_at_utc="2026-04-07T05:10:00Z",
        wait_reason_code="awaiting_capacity",
    )

    first = ledger.append_claim_record_once(record, repo_root=tmp_path)
    second = ledger.append_claim_record_once(record, repo_root=tmp_path)

    assert first["deduped"] is False
    assert second["deduped"] is True
    assert ledger.read_claim_lineage("claim_a", repo_root=tmp_path) == [{**record, "source": ""}]


def test_append_claim_record_once_rejects_conflicting_duplicate_entry_uid(tmp_path: Path) -> None:
    ledger.append_claim_record_once(
        _claim_record(
            "claim_a:waiting",
            "claim_a",
            "worker.alpha",
            status="waiting",
            recorded_at_utc="2026-04-07T05:10:00Z",
            wait_reason_code="awaiting_capacity",
        ),
        repo_root=tmp_path,
    )

    with pytest.raises(ValueError, match="entry_uid already exists with different content"):
        ledger.append_claim_record_once(
            _claim_record(
                "claim_a:waiting",
                "claim_a",
                "worker.alpha",
                status="waiting",
                recorded_at_utc="2026-04-07T05:10:01Z",
                wait_reason_code="resource_busy",
            ),
            repo_root=tmp_path,
        )


def test_append_rejects_claim_uid_reused_for_different_resource(tmp_path: Path) -> None:
    other_resource = {
        "resource_class": "host",
        "scope": "global",
        "locator": "primary",
        "canonical_key": "host:global:primary",
    }
    ledger.append_claim_record_once(
        _claim_record(
            "claim_a:waiting",
            "claim_a",
            "worker.alpha",
            status="waiting",
            recorded_at_utc="2026-04-07T05:10:00Z",
            wait_reason_code="awaiting_capacity",
        ),
        repo_root=tmp_path,
    )

    with pytest.raises(ValueError, match="claim_uid already exists for different resource"):
        ledger.append_claim_record_once(
            _claim_record(
                "claim_a:host_waiting",
                "claim_a",
                "worker.alpha",
                status="waiting",
                recorded_at_utc="2026-04-07T05:10:01Z",
                resource=other_resource,
                wait_reason_code="resource_busy",
            ),
            repo_root=tmp_path,
        )


def test_replay_rejects_malformed_durable_entries(tmp_path: Path) -> None:
    paths = ledger.ledger_paths_for_resource(RESOURCE, repo_root=tmp_path)
    paths["claims_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["claims_log"].write_text('{"schema_version":"shared_resource_claim_record_v1"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="malformed JSONL entry"):
        ledger.read_claim_records_for_resource(RESOURCE, repo_root=tmp_path)


def test_resource_concurrency_snapshot_projects_waiting_and_recovery_truth(tmp_path: Path) -> None:
    resource = job_family_resource_key("cpu_light").as_dict()
    ledger.append_claim_record(
        _claim_record(
            "claim_a:acquired",
            "claim_a",
            "worker.alpha",
            resource=resource,
            status="acquired",
            recorded_at_utc="2026-04-07T05:10:00Z",
        ),
        repo_root=tmp_path,
    )
    ledger.append_claim_record(
        _claim_record(
            "claim_b:acquired",
            "claim_b",
            "worker.beta",
            resource=resource,
            status="acquired",
            recorded_at_utc="2026-04-07T05:10:01Z",
        ),
        repo_root=tmp_path,
    )
    ledger.append_claim_record(
        _claim_record(
            "claim_c:waiting",
            "claim_c",
            "worker.gamma",
            resource=resource,
            status="waiting",
            recorded_at_utc="2026-04-07T05:10:02Z",
            wait_reason_code="awaiting_capacity",
            detail="shared resource capacity busy for job_family:scheduler:cpu_light; active=2/2",
        ),
        repo_root=tmp_path,
    )


def test_lenient_claim_state_listing_skips_malformed_historical_lineage_and_preserves_valid_claims(tmp_path: Path) -> None:
    resource = job_family_resource_key("cpu_light").as_dict()
    ledger.append_claim_record(
        _claim_record(
            "claim_good:acquired",
            "claim_good",
            "worker.good",
            resource=resource,
            status="acquired",
            recorded_at_utc="2026-04-07T05:10:00Z",
        ),
        repo_root=tmp_path,
    )
    ledger.append_claim_record(
        _claim_record(
            "claim_bad:released",
            "claim_bad",
            "worker.bad",
            resource=resource,
            status="released",
            recorded_at_utc="2026-04-07T05:10:01Z",
        ),
        repo_root=tmp_path,
    )

    with pytest.raises(ValueError, match="claim released before acquisition"):
        ledger.list_claim_states_for_resource(resource, repo_root=tmp_path)

    lenient = ledger.list_claim_states_for_resource_lenient(resource, repo_root=tmp_path)
    occupancy = ledger.replay_resource_occupancy(resource, repo_root=tmp_path, skip_malformed=True)

    assert [row["claim_uid"] for row in lenient["states"]] == ["claim_good"]
    assert lenient["states"][0]["status"] == "acquired"
    assert lenient["errors"] == [
        {
            "claim_uid": "claim_bad",
            "error": "claim released before acquisition: claim_bad",
            "lineage_length": 1,
        }
    ]
    assert occupancy["active_claim_count"] == 1
    assert occupancy["active_claims"][0]["claim_uid"] == "claim_good"
    assert occupancy["malformed_claim_count"] == 1
    assert occupancy["malformed_claims"] == [{"claim_uid": "claim_bad", "lineage_length": 1}]
    ledger.append_recovery_record_once(
        _recovery_record(
            "recovery_claim_a",
            "claim_a",
            "worker.alpha",
            resource=resource,
            recorded_at_utc="2026-04-07T05:10:03Z",
            action="released",
            detail="scheduler released stale claim after restart",
        ),
        repo_root=tmp_path,
    )

    payload = ledger.resource_concurrency_snapshot(
        repo_root=tmp_path,
        runtime_policy={"policy_valid": True, "policy_source": "runtime_policy", "resource_class_caps": {"cpu_light": 2}},
    )

    assert payload["status"] == "ok"
    assert payload["resource_count"] == 1
    assert payload["active_claim_count"] == 2
    assert payload["waiting_claim_count"] == 1
    assert payload["saturated_resource_count"] == 1
    assert payload["recovered_resource_count"] == 1
    row = payload["resources"][0]
    assert row["resource"] == resource
    assert row["limit"] == {
        "known": True,
        "value": 2,
        "source": "runtime_policy",
        "source_detail": "scheduler.resource_class_caps",
    }
    assert row["saturation_state"] == "saturated"
    assert row["waiting_claims"][0]["claim_uid"] == "claim_c"
    assert row["recovery_state"] == "recovered"
    assert row["recovery"]["counts_by_action"]["released"] == 1
    assert row["operator_summary"]["state"] == "contended"
    assert row["operator_summary"]["dominant_wait_reason_code"] == "awaiting_capacity"
    assert row["operator_summary"]["wait_reason_counts"] == {"awaiting_capacity": 1}
    assert "limit 2 from runtime policy" in row["operator_summary"]["message"]
    assert payload["highlights"][0]["canonical_key"] == "job_family:scheduler:cpu_light"
    assert "awaiting_capacity" in payload["highlights"][0]["message"]


def test_resource_concurrency_snapshot_reports_degraded_when_ledger_is_malformed(tmp_path: Path) -> None:
    paths = ledger.ledger_paths_for_resource(RESOURCE, repo_root=tmp_path)
    paths["claims_log"].parent.mkdir(parents=True, exist_ok=True)
    paths["claims_log"].write_text('{"schema_version":"shared_resource_claim_record_v1"\n', encoding="utf-8")

    payload = ledger.resource_concurrency_snapshot(repo_root=tmp_path, runtime_policy={})

    assert payload["status"] == "degraded"
    assert payload["resources"] == []
    assert payload["degraded_reasons"]
    assert payload["highlights"] == []


def test_resource_concurrency_snapshot_reports_unknown_without_inventing_occupancy(tmp_path: Path) -> None:
    payload = ledger.resource_concurrency_snapshot(repo_root=tmp_path, runtime_policy={})

    assert payload["status"] == "unknown"
    assert payload["resource_count"] == 0
    assert payload["active_claim_count"] == 0
    assert payload["waiting_claim_count"] == 0
    assert payload["highlights"] == []
    assert payload["resources"] == []


def _append_waiting_claim(repo_root: str, index: int) -> None:
    record = _claim_record(
        f"claim_{index}:waiting",
        f"claim_{index}",
        f"worker.{index}",
        status="waiting",
        recorded_at_utc=f"2026-04-07T05:10:{index:02d}Z",
        wait_reason_code="resource_busy",
    )
    ledger.append_claim_record_once(record, repo_root=Path(repo_root))


def test_concurrent_process_writers_do_not_lose_claim_events(tmp_path: Path) -> None:
    workers = [
        multiprocessing.Process(target=_append_waiting_claim, args=(str(tmp_path), idx))
        for idx in range(10)
    ]

    for worker in workers:
        worker.start()
    for worker in workers:
        worker.join(timeout=10)
        assert worker.exitcode == 0

    occupancy = ledger.replay_resource_occupancy(RESOURCE, repo_root=tmp_path)
    assert occupancy["waiting_claim_count"] == 10
    assert {row["claim_uid"] for row in occupancy["waiting_claims"]} == {f"claim_{idx}" for idx in range(10)}
