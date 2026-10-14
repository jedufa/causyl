from __future__ import annotations

from pathlib import Path

from infra.controlplane import continuation_packet_contract as cpc


REFERENCE_PACK_FINGERPRINT = "242bae0de4113a23933a78399d79e879c7d90bf7bd1d06df40840db494fbea87"


def _base_packet() -> dict:
    return {
        "schema_version": "continuation_packet_v1",
        "packet_id": "pkt_demo_001",
        "packet_revision": 1,
        "created_utc": "2026-04-07T07:35:00Z",
        "source_run": {
            "job_id": "job_demo",
            "run_id": "sj_20260407T073500Z_demo",
            "attempt_id": "attempt_1",
            "receipt_refs": ["governance/agents/runtime/scheduled_jobs/receipts/demo.json"],
        },
        "handoff_brief": "Parser contract landed; downstream storage work can bind to the normalized packet shape.",
        "result_summary": {
            "outcome": "partial",
            "summary": "Defined the packet contract and left storage/emission for later children.",
        },
        "evidence_created": [
            {
                "evidence_id": "ev_contract_tests",
                "kind": "unit_test",
                "summary": "Focused contract tests cover normalization and fail-closed rejection cases.",
                "refs": ["tests/control_plane/test_continuation_packet_contract.py"],
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
                "question": "Which append-only ledger path should own durable packet writes?",
            }
        ],
        "failed_paths": [
            {
                "path_id": "fp_inline_payloads",
                "summary": "Inline artifact payloads were rejected to keep packets bounded and hash-stable.",
            }
        ],
        "recommended_next_steps": [
            {
                "step_id": "step_storage",
                "priority": "now",
                "step": "Implement append-only storage using this normalized packet contract.",
            }
        ],
        "confidence": {
            "level": "high",
            "rationale": "The contract is covered by targeted schema tests and explicit fail-closed rules.",
        },
        "applicability": {
            "scope": "same_family",
            "limitations": ["Use linked lifecycle receipts for raw run status truth."],
        },
    }


def _reference_pack_ref() -> dict:
    return {
        "pack_id": "rep-20260410-reference",
        "pack_schema_version": "research_evidence_pack_v1",
        "pack_artifact_ref": "governance/research_evidence_pack_reference_v1.json",
        "pack_fingerprint": REFERENCE_PACK_FINGERPRINT,
        "summary": "Reference evidence pack can be located from durable packet handoff state.",
    }


def test_continuation_packet_contract_v1_has_expected_truth_boundaries() -> None:
    contract = cpc.continuation_packet_contract_v1()
    assert contract.version == "continuation_packet_v1"
    assert contract.uid == "PIS-20260407-063101Z-A1C1"
    assert contract.canonical_doc_rel.as_posix() == "docs/contracts/continuation_packet_v1.md"
    assert contract.packet_identity_fields == ("schema_version", "packet_id", "packet_revision")
    assert contract.truth_boundary_authorities["packet_summary"] == "continuation_packet_record"
    assert contract.truth_boundary_authorities["artifact_payloads"] == "linked_artifact_storage"
    assert contract.truth_boundary_not_authoritative["source_run_status"] == "packet_defined_status_override"
    assert contract.truth_boundary_not_authoritative["artifact_payloads"] == "embedded_heavy_payload_bytes"
    assert contract.truth_boundary_authorities["evidence_pack_payloads"] == "linked_evidence_pack_storage"
    assert contract.truth_boundary_not_authoritative["evidence_pack_payloads"] == "embedded_evidence_pack_body_or_pack_schema_copy"
    assert contract.optional_fields == ("supersedes_packet_id", "evidence_pack_refs")
    assert contract.nested_required_fields == (
        "source_run.job_id",
        "source_run.run_id",
        "source_run.receipt_refs",
        "result_summary.outcome",
        "result_summary.summary",
        "confidence.level",
        "confidence.rationale",
        "applicability.scope",
        "applicability.limitations",
    )
    assert contract.field_requirements["handoff_brief"] == "required"
    assert contract.field_requirements["source_run.attempt_id"] == "optional"
    assert contract.fail_closed_rules == (
        "missing_required_top_level_section",
        "missing_source_run_provenance",
        "negative_outcomes_require_failed_paths",
        "truth_authority_override_rejected",
        "embedded_artifact_payload_rejected",
        "missing_or_stale_evidence_pack_ref_rejected",
    )
    assert contract.malformed_packet_rejections == (
        "blank_packet_identity_rejected",
        "non_positive_packet_revision_rejected",
        "missing_result_summary_rejected",
        "missing_confidence_rationale_rejected",
        "missing_applicability_limitations_rejected",
        "negative_outcome_without_failed_paths_rejected",
        "truth_authority_override_rejected",
        "embedded_artifact_payload_rejected",
        "stale_or_missing_evidence_pack_ref_rejected",
    )


def test_validate_continuation_packet_normalizes_and_fingerprints_stably() -> None:
    raw_a = _base_packet()
    raw_b = _base_packet()
    raw_b["source_run"]["receipt_refs"] = list(reversed(raw_b["source_run"]["receipt_refs"]))
    raw_b["applicability"]["limitations"] = list(reversed(raw_b["applicability"]["limitations"]))
    raw_b["unknown_future_field"] = {"enabled": True}

    va = cpc.validate_continuation_packet(raw_a)
    vb = cpc.validate_continuation_packet(raw_b)

    assert va["ok"] is True
    assert vb["ok"] is True
    assert va["computed_fingerprint"] != ""
    assert va["computed_fingerprint"] == vb["computed_fingerprint"]
    assert any(row["code"] == "unknown_field" for row in vb["warnings"])


def test_validate_continuation_packet_persists_reloadable_evidence_pack_refs() -> None:
    raw = _base_packet()
    raw["evidence_pack_refs"] = [_reference_pack_ref()]

    result = cpc.validate_continuation_packet(raw, repo_root=Path("."))

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["normalized_packet"]["evidence_pack_refs"][0]["pack_id"] == "rep-20260410-reference"


def test_validate_continuation_packet_rejects_stale_evidence_pack_fingerprint() -> None:
    raw = _base_packet()
    pack_ref = _reference_pack_ref()
    pack_ref["pack_fingerprint"] = "stale"
    raw["evidence_pack_refs"] = [pack_ref]

    result = cpc.validate_continuation_packet(raw, repo_root=Path("."))

    assert result["ok"] is False
    assert any(row["code"] == "fingerprint_mismatch" for row in result["errors"])


def test_truth_boundary_authority_rejects_unknown_boundary() -> None:
    try:
        cpc.truth_boundary_authority("not_real")
    except KeyError as exc:
        assert "unknown truth boundary" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected unknown truth boundary to raise")


def test_packet_field_requirement_and_truth_boundary_non_authority_reject_unknown_inputs() -> None:
    assert cpc.packet_field_requirement("recommended_next_steps") == "required"
    assert cpc.packet_field_requirement("supersedes_packet_id") == "optional"
    assert cpc.truth_boundary_non_authority("operator_or_transcript_prose") == "durable_truth_override"

    try:
        cpc.packet_field_requirement("not_real")
    except KeyError as exc:
        assert "unknown continuation packet field" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected unknown packet field to raise")

    try:
        cpc.truth_boundary_non_authority("not_real")
    except KeyError as exc:
        assert "unknown truth boundary" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected unknown truth boundary to raise")


def test_validate_continuation_packet_rejects_incomplete_negative_path_and_uncertainty_shape() -> None:
    raw = _base_packet()
    raw.pop("open_questions")
    raw["applicability"]["limitations"] = []
    raw["result_summary"]["outcome"] = "failed"
    raw["failed_paths"] = []

    result = cpc.validate_continuation_packet(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["failure_class"] == "continuation_packet_validation_failed"
    assert "missing_field" in codes
    assert "negative_path_required" in codes


def test_validate_continuation_packet_rejects_blank_identity_and_missing_required_summary_fields() -> None:
    raw = _base_packet()
    raw["packet_id"] = ""
    raw["packet_revision"] = 0
    raw["result_summary"]["summary"] = ""
    raw["confidence"]["rationale"] = ""

    result = cpc.validate_continuation_packet(raw)

    errors_by_field = {row["field"]: row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert errors_by_field["packet_id"] == "missing_field"
    assert errors_by_field["packet_revision"] == "invalid_type"
    assert errors_by_field["result_summary.summary"] == "missing_field"
    assert errors_by_field["confidence.rationale"] == "missing_field"


def test_validate_continuation_packet_rejects_missing_source_run_provenance() -> None:
    raw = _base_packet()
    raw["source_run"]["run_id"] = ""
    raw["source_run"]["receipt_refs"] = []

    result = cpc.validate_continuation_packet(raw)

    errors_by_field = {row["field"]: row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert errors_by_field["source_run.run_id"] == "missing_field"
    assert errors_by_field["source_run.receipt_refs"] == "missing_field"


def test_validate_continuation_packet_rejects_truth_override_and_embedded_artifact_payload() -> None:
    raw = _base_packet()
    raw["truth_authority_override"] = "packet_beats_lifecycle"
    raw["artifact_index"][0]["content"] = "huge inline blob"

    result = cpc.validate_continuation_packet(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "ambiguous_truth_boundary" in codes
    assert "artifact_payload_not_allowed" in codes


def test_validate_continuation_packet_contract_v1_returns_no_issues() -> None:
    assert cpc.validate_continuation_packet_contract_v1() == ()
