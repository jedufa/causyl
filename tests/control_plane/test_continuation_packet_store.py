from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import continuation_packet_store as store


def _base_packet(*, packet_id: str = "pkt_demo_001", packet_revision: int = 1, supersedes_packet_id: str = "") -> dict:
    packet = {
        "schema_version": "continuation_packet_v1",
        "packet_id": packet_id,
        "packet_revision": packet_revision,
        "created_utc": f"2026-04-07T07:3{packet_revision}:00Z",
        "source_run": {
            "job_id": "job_demo",
            "run_id": "sj_20260407T073500Z_demo",
            "attempt_id": "attempt_1",
            "receipt_refs": ["governance/agents/runtime/scheduled_jobs/receipts/demo.json"],
        },
        "handoff_brief": "Parser contract landed; downstream storage work can bind to the normalized packet shape.",
        "result_summary": {
            "outcome": "partial",
            "summary": "Defined the packet contract and durable storage behavior.",
        },
        "evidence_created": [
            {
                "evidence_id": "ev_store_tests",
                "kind": "unit_test",
                "summary": "Focused storage tests cover append-only replay and fail-closed lookup.",
                "refs": ["tests/control_plane/test_continuation_packet_store.py"],
            }
        ],
        "artifact_index": [
            {
                "artifact_id": "artifact_contract_doc",
                "kind": "doc",
                "uri": "docs/contracts/continuation_packet_v1.md",
                "summary": "Canonical packet truth-boundary notes.",
            }
        ],
        "open_questions": [
            {
                "question_id": "q_storage",
                "question": "Which successor path should consume the durable packet first?",
            }
        ],
        "failed_paths": [
            {
                "path_id": "fp_inline_payloads",
                "summary": "Inline artifact payloads remain rejected to keep packets bounded and hash-stable.",
            }
        ],
        "recommended_next_steps": [
            {
                "step_id": "step_storage",
                "priority": "now",
                "step": "Replay the durable packet log and consume the latest valid packet.",
            }
        ],
        "confidence": {
            "level": "high",
            "rationale": "The storage surface is covered by focused append-only and fail-closed tests.",
        },
        "applicability": {
            "scope": "same_family",
            "limitations": ["Use linked lifecycle receipts for raw run status truth."],
        },
    }
    if supersedes_packet_id:
        packet["supersedes_packet_id"] = supersedes_packet_id
    return packet


def test_append_revision_and_restart_safe_latest_lookup(tmp_path: Path) -> None:
    first = store.append_packet_revision_once(
        _base_packet(),
        lookup_keys={"job_family": "scheduler:cpu_light", "campaign_id": "camp_alpha"},
        repo_root=tmp_path,
    )
    second_packet = _base_packet(packet_revision=2)
    second = store.append_packet_revision_once(
        second_packet,
        lookup_keys={"job_family": "scheduler:cpu_light", "campaign_id": "camp_alpha"},
        repo_root=tmp_path,
    )

    assert first["deduped"] is False
    assert second["deduped"] is False

    latest_run = store.latest_valid_packet(
        lookup_key="run_id",
        lookup_value="sj_20260407T073500Z_demo",
        repo_root=tmp_path,
    )
    latest_family = store.latest_valid_packet(
        lookup_key="job_family",
        lookup_value="scheduler:cpu_light",
        repo_root=tmp_path,
    )
    rows = store.read_store_records(repo_root=tmp_path)

    assert latest_run is not None
    assert latest_family is not None
    assert latest_run["packet_revision"] == 2
    assert latest_family["packet_revision"] == 2
    assert [row["record_kind"] for row in rows] == ["packet_revision", "packet_revision", "packet_status"]
    assert rows[-1]["status"] == "superseded"
    assert rows[-1]["replacement_packet_revision"] == 2


def test_cross_packet_supersession_preserves_history_and_latest_valid_lookup(tmp_path: Path) -> None:
    store.append_packet_revision_once(
        _base_packet(packet_id="pkt_old"),
        lookup_keys={"job_family": "scheduler:gpu_heavy", "campaign_id": "camp_alpha"},
        repo_root=tmp_path,
    )
    store.append_packet_revision_once(
        _base_packet(packet_id="pkt_new", supersedes_packet_id="pkt_old"),
        lookup_keys={"job_family": "scheduler:gpu_heavy", "campaign_id": "camp_alpha"},
        repo_root=tmp_path,
    )

    latest = store.latest_valid_packet(
        lookup_key="campaign_id",
        lookup_value="camp_alpha",
        repo_root=tmp_path,
    )
    rows = store.read_store_records(repo_root=tmp_path)

    assert latest is not None
    assert latest["packet_id"] == "pkt_new"
    assert [row["record_kind"] for row in rows] == ["packet_revision", "packet_revision", "packet_status"]
    assert rows[-1]["replacement_packet_id"] == "pkt_new"


def test_invalid_supersedes_target_fails_closed_without_partial_append(tmp_path: Path) -> None:
    with pytest.raises(store.ContinuationPacketStoreError, match="does not reference an active durable packet"):
        store.append_packet_revision_once(
            _base_packet(packet_id="pkt_new", supersedes_packet_id="pkt_missing"),
            lookup_keys={"campaign_id": "camp_alpha"},
            repo_root=tmp_path,
        )

    assert store.read_store_records(repo_root=tmp_path) == []


def test_invalidation_removes_latest_valid_candidate_without_deleting_history(tmp_path: Path) -> None:
    store.append_packet_revision_once(
        _base_packet(),
        lookup_keys={"campaign_id": "camp_alpha"},
        repo_root=tmp_path,
    )

    marker = store.invalidate_packet(
        "pkt_demo_001",
        reason="operator_rejected_packet",
        recorded_at_utc="2026-04-07T08:00:00Z",
        repo_root=tmp_path,
    )
    latest = store.latest_valid_packet(
        lookup_key="campaign_id",
        lookup_value="camp_alpha",
        repo_root=tmp_path,
    )
    rows = store.read_store_records(repo_root=tmp_path)

    assert marker["status"] == "invalidated"
    assert latest is None
    assert [row["record_kind"] for row in rows] == ["packet_revision", "packet_status"]


def test_repeat_invalidation_of_same_revision_is_idempotent(tmp_path: Path) -> None:
    store.append_packet_revision_once(
        _base_packet(),
        lookup_keys={"campaign_id": "camp_alpha"},
        repo_root=tmp_path,
    )
    first = store.invalidate_packet(
        "pkt_demo_001",
        packet_revision=1,
        reason="operator_rejected_packet",
        recorded_at_utc="2026-04-07T08:00:00Z",
        repo_root=tmp_path,
    )
    second = store.invalidate_packet(
        "pkt_demo_001",
        packet_revision=1,
        reason="ignored_second_try",
        recorded_at_utc="2026-04-07T08:01:00Z",
        repo_root=tmp_path,
    )

    rows = store.read_store_records(repo_root=tmp_path)

    assert first == second
    assert [row["record_kind"] for row in rows] == ["packet_revision", "packet_status"]
    assert rows[-1]["reason"] == "operator_rejected_packet"


def test_duplicate_revision_uid_with_different_content_fails_closed(tmp_path: Path) -> None:
    store.append_packet_revision_once(_base_packet(), repo_root=tmp_path)
    conflicting = _base_packet()
    conflicting["handoff_brief"] = "conflicting rewrite"

    with pytest.raises(store.ContinuationPacketStoreError, match="conflicting duplicate packet revision identity"):
        store.append_packet_revision_once(conflicting, repo_root=tmp_path)


def test_new_packet_that_would_create_ambiguous_latest_lookup_is_rejected(tmp_path: Path) -> None:
    store.append_packet_revision_once(
        _base_packet(packet_id="pkt_old"),
        lookup_keys={"campaign_id": "camp_alpha", "job_family": "scheduler:cpu_light"},
        repo_root=tmp_path,
    )

    with pytest.raises(store.AmbiguousLatestValidPacketError, match="ambiguous latest-valid lookup"):
        store.append_packet_revision_once(
            _base_packet(packet_id="pkt_other"),
            lookup_keys={"campaign_id": "camp_alpha", "job_family": "scheduler:cpu_light"},
            repo_root=tmp_path,
        )


def test_write_rejects_missing_explicit_schema_version_even_if_contract_normalizes_it(tmp_path: Path) -> None:
    packet = _base_packet()
    packet.pop("schema_version")

    with pytest.raises(store.ContinuationPacketStoreError, match="schema_version must be explicit"):
        store.append_packet_revision_once(packet, repo_root=tmp_path)


def test_read_rejects_malformed_store_log(tmp_path: Path) -> None:
    records_path = store.store_paths(repo_root=tmp_path)["records_log"]
    records_path.parent.mkdir(parents=True, exist_ok=True)
    records_path.write_text('{"schema_version":"continuation_packet_store_v1"\n', encoding="utf-8")

    with pytest.raises(store.ContinuationPacketStoreError, match="malformed JSONL entry"):
        store.read_store_records(repo_root=tmp_path)


def test_read_rejects_fingerprint_drift_on_restart_style_rediscovery(tmp_path: Path) -> None:
    store.append_packet_revision_once(_base_packet(), repo_root=tmp_path)
    records_path = store.store_paths(repo_root=tmp_path)["records_log"]
    rows = [json.loads(line) for line in records_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows[0]["packet_fingerprint"] = "badf00d"
    records_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")

    with pytest.raises(store.ContinuationPacketStoreError, match="fingerprint drift detected"):
        store.read_store_records(repo_root=tmp_path)
