from __future__ import annotations

from infra.controlplane import continuation_long_horizon_memory as memory


def _base_packet() -> dict:
    return {
        "schema_version": "continuation_packet_v1",
        "packet_id": "pkt_demo_001",
        "packet_revision": 2,
        "created_utc": "2026-04-07T08:00:00Z",
        "source_run": {
            "job_id": "job_demo",
            "run_id": "sj_20260407T080000Z_demo",
            "attempt_id": "attempt_2",
            "receipt_refs": ["governance/agents/runtime/scheduled_jobs/receipts/demo.json"],
        },
        "handoff_brief": "Storage and bootstrap landed; reuse the durable findings instead of rediscovering them.",
        "result_summary": {
            "outcome": "partial",
            "summary": "A packet-backed checkpoint exists with bounded next work.",
        },
        "evidence_created": [
            {
                "evidence_id": "ev_memory_tests",
                "kind": "unit_test",
                "summary": "Focused memory-policy tests cover promotion and invalidation.",
                "refs": ["tests/control_plane/test_continuation_long_horizon_memory.py"],
            }
        ],
        "artifact_index": [
            {
                "artifact_id": "artifact_contract_doc",
                "kind": "doc",
                "uri": "docs/contracts/continuation_packet_v1.md",
                "summary": "Canonical packet contract.",
            }
        ],
        "open_questions": [
            {
                "question_id": "q_resume",
                "question": "Should the next worker resume from the last checkpoint or replace it?",
            }
        ],
        "failed_paths": [
            {
                "path_id": "fp_inline_payloads",
                "summary": "Inline payload blobs were rejected because they break bounded durable packets.",
            }
        ],
        "recommended_next_steps": [
            {
                "step_id": "step_resume",
                "priority": "now",
                "step": "Reuse the durable packet and continue from the last validated checkpoint.",
            }
        ],
        "confidence": {
            "level": "high",
            "rationale": "The packet is backed by targeted tests and explicit receipt provenance.",
        },
        "applicability": {
            "scope": "same_family",
            "limitations": ["Lifecycle receipts remain authoritative for raw status truth."],
        },
    }


def test_promote_packet_to_memory_records_emits_explicit_retention_and_provenance() -> None:
    result = memory.promote_packet_to_memory_records(_base_packet(), promoted_at_utc="2026-04-07T09:00:00Z")

    assert result["ok"] is True
    assert result["errors"] == []
    assert {row["memory_kind"] for row in result["records"]} == {
        "negative_finding",
        "open_question",
        "recommended_next_step",
    }
    first = result["records"][0]
    assert first["provenance_refs"]
    assert first["retention"]["policy_name"].startswith("packet_")
    assert first["retention"]["expires_at_utc"].endswith("Z")
    assert memory.evaluate_memory_record_activity(first, as_of_utc="2026-04-08T09:00:00Z")["active"] is True


def test_promote_packet_to_memory_records_rejects_missing_packet_provenance() -> None:
    raw = _base_packet()
    raw["source_run"]["receipt_refs"] = []

    result = memory.promote_packet_to_memory_records(raw, promoted_at_utc="2026-04-07T09:00:00Z")

    assert result["ok"] is False
    assert result["reason"] == "packet_validation_failed"
    assert any(row["field"] == "source_run.receipt_refs" for row in result["errors"])


def test_promote_packet_to_memory_records_rejects_unscoped_packets() -> None:
    raw = _base_packet()
    raw["applicability"]["scope"] = ""

    result = memory.promote_packet_to_memory_records(raw, promoted_at_utc="2026-04-07T09:00:00Z")

    assert result["ok"] is False
    assert result["records"] == []
    assert result["reason"] == "packet_validation_failed"
    assert any(row["field"] == "applicability.scope" for row in result["errors"])


def test_promote_packet_to_memory_records_keeps_reference_only_scope_packet_local() -> None:
    raw = _base_packet()
    raw["applicability"]["scope"] = "reference_only"

    result = memory.promote_packet_to_memory_records(raw, promoted_at_utc="2026-04-07T09:00:00Z")

    assert result["ok"] is True
    assert result["records"] == []
    assert result["reason"] == "packet_scope_not_promotable"


def test_contradicted_and_withdrawn_memory_cannot_remain_active_guidance() -> None:
    promoted = memory.promote_packet_to_memory_records(_base_packet(), promoted_at_utc="2026-04-07T09:00:00Z")
    negative = next(row for row in promoted["records"] if row["memory_kind"] == "negative_finding")
    next_step = next(row for row in promoted["records"] if row["memory_kind"] == "recommended_next_step")

    contradicted = memory.apply_memory_correction(
        negative,
        new_state="contradicted",
        reason="A newer packet disproved the earlier failure claim.",
        recorded_at_utc="2026-04-09T10:00:00Z",
        corrected_by_packet_id="pkt_demo_002",
    )
    withdrawn = memory.apply_memory_correction(
        next_step,
        new_state="withdrawn",
        reason="The operator chose a different execution path.",
        recorded_at_utc="2026-04-09T10:05:00Z",
    )

    assert contradicted["ok"] is True
    assert withdrawn["ok"] is True
    assert memory.evaluate_memory_record_activity(contradicted["record"], as_of_utc="2026-04-09T12:00:00Z")["status"] == "contradicted"
    assert memory.evaluate_memory_record_activity(withdrawn["record"], as_of_utc="2026-04-09T12:00:00Z")["status"] == "withdrawn"


def test_expired_memory_is_not_active_guidance() -> None:
    promoted = memory.promote_packet_to_memory_records(_base_packet(), promoted_at_utc="2026-04-07T09:00:00Z")
    next_step = next(row for row in promoted["records"] if row["memory_kind"] == "recommended_next_step")

    status = memory.evaluate_memory_record_activity(next_step, as_of_utc="2026-04-15T09:00:00Z")

    assert status["active"] is False
    assert status["status"] == "expired"


def test_validate_memory_record_rejects_missing_explicit_retention_and_correction_fields() -> None:
    promoted = memory.promote_packet_to_memory_records(_base_packet(), promoted_at_utc="2026-04-07T09:00:00Z")
    record = dict(promoted["records"][0])
    record["retention"] = {"policy_name": "", "retention_days": 0, "promoted_at_utc": "", "expires_at_utc": ""}
    record["correction"] = {"state": "withdrawn", "reason": "", "recorded_at_utc": "", "corrected_by_packet_id": ""}

    result = memory.validate_memory_record(record)

    assert result["ok"] is False
    fields = {row["field"] for row in result["errors"]}
    assert "retention.policy_name" in fields
    assert "retention.retention_days" in fields
    assert "correction.reason" in fields
    assert "correction.recorded_at_utc" in fields
