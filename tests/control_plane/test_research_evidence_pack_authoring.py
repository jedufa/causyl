from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import research_evidence_pack_authoring as authoring


def _reference_pack() -> dict:
    return json.loads(Path("governance/research_evidence_pack_reference_v1.json").read_text(encoding="utf-8"))


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_create_evidence_pack_file_validates_and_writes_receipt(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    receipt_path = tmp_path / "receipt.json"

    result = authoring.create_evidence_pack_file(
        pack_path,
        _reference_pack(),
        receipt_path=receipt_path,
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert _read(pack_path)["pack_id"] == "rep-20260410-reference"
    receipt = _read(receipt_path)
    assert receipt["schema_version"] == authoring.RECEIPT_SCHEMA_VERSION
    assert receipt["action"] == "create"
    assert receipt["result"] == "ok"
    assert receipt["pack_fingerprint"] == result["computed_fingerprint"]


def test_append_claim_preserves_existing_identity_and_updates_pack(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    authoring.create_evidence_pack_file(pack_path, _reference_pack())

    result = authoring.append_claim(
        pack_path,
        claim={
            "claim_id": "claim-new",
            "statement": "A second sourced observation remains explicit.",
            "claim_type": "finding",
            "status": "clean",
        },
        claim_source_link={
            "link_id": "link-new",
            "claim_id": "claim-new",
            "source_id": "src-clean-paper",
            "support_type": "supports",
            "evidence_locator": "doi:10.0000/reference-clean#supplement",
        },
    )

    assert result["ok"] is True
    pack = _read(pack_path)
    assert [row["claim_id"] for row in pack["claims"]].count("claim-clean") == 1
    assert any(row["claim_id"] == "claim-new" for row in pack["claims"])
    assert any(row["link_id"] == "link-new" for row in pack["claim_source_links"])


def test_invalid_append_leaves_original_pack_unchanged(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    authoring.create_evidence_pack_file(pack_path, _reference_pack())
    before = _read(pack_path)

    result = authoring.append_claim(
        pack_path,
        claim={
            "claim_id": "claim-bad",
            "statement": "This claim points at a missing source.",
            "claim_type": "finding",
            "status": "clean",
        },
        claim_source_link={
            "link_id": "link-bad",
            "claim_id": "claim-bad",
            "source_id": "src-missing",
            "support_type": "supports",
            "evidence_locator": "missing#locator",
        },
    )

    assert result["ok"] is False
    assert any(row["code"] == "unknown_ref" and row["field"] == "claim_source_links[3].source_id" for row in result["errors"])
    assert _read(pack_path) == before


def test_duplicate_append_identity_fails_before_write(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    authoring.create_evidence_pack_file(pack_path, _reference_pack())
    before = _read(pack_path)

    result = authoring.append_source(
        pack_path,
        source={
            "source_id": "src-clean-paper",
            "source_type": "paper",
            "title": "Duplicate source",
            "locator": "doi:10.0000/duplicate",
            "captured_at_utc": "2026-04-10T10:26:37Z",
        },
        source_quality_note={
            "source_id": "src-clean-paper",
            "quality_level": "strong",
            "note": "Duplicate attempt.",
        },
    )

    assert result["ok"] is False
    assert any(row["code"] == "append_would_overwrite_identity" for row in result["errors"])
    assert _read(pack_path) == before


def test_append_contradiction_note_and_summary_layer_receipts(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    receipt_path = tmp_path / "append-receipt.json"
    pack = _reference_pack()
    pack["claims"].extend(
        [
            {
                "claim_id": "claim-a",
                "statement": "A claim for contradiction append testing.",
                "claim_type": "finding",
                "status": "contested",
            },
            {
                "claim_id": "claim-b",
                "statement": "A conflicting claim for contradiction append testing.",
                "claim_type": "finding",
                "status": "contested",
            },
        ]
    )
    pack["claim_source_links"].extend(
        [
            {
                "link_id": "link-a",
                "claim_id": "claim-a",
                "source_id": "src-clean-paper",
                "support_type": "supports",
                "evidence_locator": "doi:10.0000/reference-clean#a",
            },
            {
                "link_id": "link-b",
                "claim_id": "claim-b",
                "source_id": "src-conflict-preprint",
                "support_type": "supports",
                "evidence_locator": "arxiv:0000.00000#b",
            },
        ]
    )
    authoring.create_evidence_pack_file(pack_path, pack)

    result = authoring.append_contradiction_note(
        pack_path,
        contradiction={
            "contradiction_id": "contradiction-new",
            "source_claim_id": "claim-a",
            "target_claim_id": "claim-b",
            "conflict_type": "direct_conflict",
            "severity": "high",
            "status": "unresolved",
            "evidence_link_ids": ["link-a", "link-b"],
        },
        unresolved_conflict_note={
            "note_id": "note-new",
            "contradiction_id": "contradiction-new",
            "note": "Keep the newly appended contradiction visible.",
            "next_resolution_step": "Compare methods.",
        },
        receipt_path=receipt_path,
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert _read(receipt_path)["action"] == "append"
    summary_result = authoring.append_summary_layer(
        pack_path,
        summary_layer={
            "layer_id": "open-questions-new",
            "summary_type": "open_questions",
            "summary_text": "New contradiction remains unresolved.",
            "claim_ids": ["claim-a", "claim-b"],
            "contradiction_ids": ["contradiction-new"],
        },
    )
    assert summary_result["ok"] is True
    assert any(row["layer_id"] == "open-questions-new" for row in _read(pack_path)["summary_layers"])


def test_validate_evidence_pack_file_does_not_mutate_pack(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    baseline = _reference_pack()
    authoring.create_evidence_pack_file(pack_path, copy.deepcopy(baseline))

    result = authoring.validate_evidence_pack_file(pack_path)

    assert result["ok"] is True
    assert _read(pack_path) == authoring.contract.normalize_research_evidence_pack(baseline)


def test_compact_result_for_output_caps_operator_diagnostics(tmp_path: Path) -> None:
    pack_path = tmp_path / "pack.json"
    authoring.create_evidence_pack_file(pack_path, _reference_pack())

    result = authoring.append_evidence_pack_records(pack_path, claims=["not-an-object"])  # type: ignore[list-item]
    compact = authoring.compact_result_for_output(result)

    assert compact["ok"] is False
    assert "normalized_pack" not in compact
    assert compact["error_count"] == 1
    assert compact["errors"][0]["code"] == "invalid_append_payload"
