from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import campaign_phase_gate_contract as contract


def _base_decision() -> dict:
    return {
        "schema_version": "campaign_phase_gate_contract_v1",
        "campaign_id": "camp-20260409-phase-gates",
        "phase_id": "phase_define_gate_law",
        "decision_id": "gate-001",
        "verdict": "partial_ready",
        "evidence_refs": [
            "governance/campaign_phase_gate_contract_v1.json",
            "tests/control_plane/test_campaign_phase_gate_contract.py",
        ],
        "caveats": ["Downstream launch is legal only for the review-prep phase and must carry proof-gap context."],
        "proof_gaps": ["operator_review_for_full_completion"],
        "downstream_unlock": {
            "policy": "allowed_with_caveats",
            "allowed": True,
            "unlocked_phase_ids": ["phase_review_prep"],
        },
        "handoff": {
            "summary": "Gate contract is settled; review-prep may proceed with caveats attached.",
            "evidence_refs": ["governance/campaign_phase_gate_contract_v1.json"],
            "caveats": ["Full ready is not yet earned."],
            "proof_gaps": ["operator_review_for_full_completion"],
            "next_decision": {
                "decision_owner": "campaign_parent",
                "question": "Proceed only with the review-prep child while full closure waits for review?",
                "recommended_action": "launch_downstream",
            },
        },
    }


def _reference_pack_ref() -> dict:
    return {
        "pack_id": "rep-20260410-reference",
        "pack_schema_version": "research_evidence_pack_v1",
        "pack_artifact_ref": "governance/research_evidence_pack_reference_v1.json",
        "pack_fingerprint": "242bae0de4113a23933a78399d79e879c7d90bf7bd1d06df40840db494fbea87",
        "summary": "Reference evidence pack remains linked for the downstream campaign handoff.",
    }


def test_campaign_phase_gate_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.campaign_phase_gate_contract_v1()

    assert payload.version == "campaign_phase_gate_contract_v1"
    assert payload.uid == "PIS-20260409-121601Z-K9N3"
    assert payload.canonical_artifact_rel.as_posix() == "governance/campaign_phase_gate_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_campaign_phase_gate_v1.md"
    assert payload.top_level_required_fields == (
        "schema_version",
        "campaign_id",
        "phase_id",
        "decision_id",
        "verdict",
        "evidence_refs",
        "caveats",
        "proof_gaps",
        "downstream_unlock",
        "handoff",
    )


def test_campaign_phase_gate_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/campaign_phase_gate_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["top_level_required_fields"]) == contract.TOP_LEVEL_REQUIRED_FIELDS
    assert tuple(payload["downstream_unlock_required_fields"]) == contract.DOWNSTREAM_UNLOCK_REQUIRED_FIELDS
    assert tuple(payload["handoff_required_fields"]) == contract.HANDOFF_REQUIRED_FIELDS
    assert tuple(payload["next_decision_required_fields"]) == contract.NEXT_DECISION_REQUIRED_FIELDS


def test_validate_phase_gate_accepts_partial_ready_unlock_with_caveats() -> None:
    result = contract.validate_phase_gate_decision(_base_decision())

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["normalized_decision"]["downstream_unlock"]["policy"] == "allowed_with_caveats"
    assert result["computed_fingerprint"] != ""


def test_validate_phase_gate_handoff_carries_reloadable_evidence_pack_ref() -> None:
    raw = _base_decision()
    raw["handoff"]["evidence_pack_refs"] = [_reference_pack_ref()]

    result = contract.validate_phase_gate_decision(raw, repo_root=Path("."))

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["normalized_decision"]["handoff"]["evidence_pack_refs"][0]["pack_id"] == "rep-20260410-reference"


def test_validate_phase_gate_rejects_missing_handoff_evidence_pack_ref() -> None:
    raw = _base_decision()
    pack_ref = _reference_pack_ref()
    pack_ref["pack_artifact_ref"] = "governance/missing_evidence_pack.json"
    raw["handoff"]["evidence_pack_refs"] = [pack_ref]

    result = contract.validate_phase_gate_decision(raw, repo_root=Path("."))

    assert result["ok"] is False
    assert any(row["code"] == "missing_evidence_pack" for row in result["errors"])


def test_validate_phase_gate_accepts_ready_without_proof_gap() -> None:
    raw = _base_decision()
    raw["verdict"] = "ready"
    raw["caveats"] = []
    raw["proof_gaps"] = []
    raw["downstream_unlock"] = {
        "policy": "full_ready",
        "allowed": True,
        "unlocked_phase_ids": ["phase_execution"],
    }
    raw["handoff"]["caveats"] = []
    raw["handoff"]["proof_gaps"] = []

    result = contract.validate_phase_gate_decision(raw)

    assert result["ok"] is True
    assert result["errors"] == []


def test_validate_phase_gate_rejects_partial_ready_unlock_without_caveats_or_handoff_evidence() -> None:
    raw = _base_decision()
    raw["caveats"] = []
    raw["handoff"]["caveats"] = []
    raw["handoff"]["evidence_refs"] = []

    result = contract.validate_phase_gate_decision(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "partial_ready_requires_caveat" in codes
    assert "missing_handoff_evidence" in codes


def test_validate_phase_gate_rejects_ready_with_known_proof_gap() -> None:
    raw = _base_decision()
    raw["verdict"] = "ready"
    raw["downstream_unlock"]["policy"] = "full_ready"

    result = contract.validate_phase_gate_decision(raw)

    errors_by_field = {row["field"]: row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert errors_by_field["proof_gaps"] == "proof_gap_not_allowed"


def test_validate_phase_gate_rejects_waiting_review_or_blocked_unlock_claims() -> None:
    waiting_review = _base_decision()
    waiting_review["verdict"] = "waiting_review"
    waiting_review["downstream_unlock"] = {
        "policy": "allowed_with_caveats",
        "allowed": True,
        "unlocked_phase_ids": ["phase_review_prep"],
    }

    blocked = _base_decision()
    blocked["verdict"] = "blocked"
    blocked["downstream_unlock"] = {
        "policy": "full_ready",
        "allowed": True,
        "unlocked_phase_ids": ["phase_execution"],
    }

    waiting_result = contract.validate_phase_gate_decision(waiting_review)
    blocked_result = contract.validate_phase_gate_decision(blocked)

    assert waiting_result["ok"] is False
    assert blocked_result["ok"] is False
    assert any(row["code"] == "contradictory_status" for row in waiting_result["errors"])
    assert any(row["code"] == "contradictory_status" for row in blocked_result["errors"])


def test_validate_campaign_phase_gate_contract_v1_returns_no_issues() -> None:
    assert contract.validate_campaign_phase_gate_contract_v1() == ()
