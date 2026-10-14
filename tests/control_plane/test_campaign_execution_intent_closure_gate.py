from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_patch_instruction_sheet_dir(uid: str) -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("active", "verified"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{uid}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing patch instruction sheet dir for {uid}")


def _find_intent_sheet_dir(uid: str) -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "intent_sheets" / "causyl"
    for ledger_status in ("active", "verified"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{uid}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing intent sheet dir for {uid}")


def test_campaign_execution_intent_closure_gate_tracks_current_family_truth() -> None:
    parent_dir = _find_patch_instruction_sheet_dir("PIS-20260409-121217Z-2A6A")
    proof_dir = _find_patch_instruction_sheet_dir("PIS-20260409-121605Z-P4S7")
    gate_dir = _find_patch_instruction_sheet_dir("PIS-20260409-121606Z-Q5T8")
    intent_dir = _find_intent_sheet_dir("IS-20260403-212914Z-0661")

    parent_meta = _load_json(parent_dir / "meta.json")
    proof_meta = _load_json(proof_dir / "meta.json")
    gate_meta = _load_json(gate_dir / "meta.json")
    intent_meta = _load_json(intent_dir / "meta.json")
    gate_events = [
        json.loads(line)
        for line in (gate_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gate_text = (
        gate_dir
        / "artifacts"
        / "verification"
        / "is10_campaign_execution_contract_intent_closure_gate.md"
    ).read_text(encoding="utf-8")

    assert gate_meta["status"] == "verified"
    assert gate_meta["required_evidence"] == [
        "specialized_verifier_review",
        "intent_closure_gate_artifact",
    ]
    assert gate_meta["evidence"] == {
        "intent_closure_gate_artifact": (
            "artifacts/verification/is10_campaign_execution_contract_intent_closure_gate.md"
        )
    }
    assert gate_meta["integration_meta"]["required_final_child_uid"] == "PIS-20260409-121605Z-P4S7"

    latest_meta_event = next(event for event in reversed(gate_events) if event["type"] == "meta_updated")
    latest_reconcile_event = next(
        event for event in reversed(gate_events) if event["type"] == "status_reconcile_reviewed"
    )
    latest_verification_event = next(
        event for event in reversed(gate_events) if event["type"] == "verification_checked"
    )

    assert latest_meta_event["payload"]["required_evidence"] == gate_meta["required_evidence"]
    assert latest_meta_event["payload"]["evidence"] == gate_meta["evidence"]
    assert latest_reconcile_event["payload"]["artifact"] == (
        "artifacts/verification/is10_campaign_execution_contract_intent_closure_gate.md"
    )
    assert latest_reconcile_event["payload"]["verdict"] == "verified_from_durable_review_and_green_family_rerun"
    assert latest_verification_event["payload"]["artifact"] == (
        "artifacts/verification/is10_campaign_execution_contract_intent_closure_gate.md"
    )
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["payload"]["stronger_status_earned"] is True

    assert parent_meta["status"] == "verified"
    assert proof_meta["status"] == "verified"
    assert proof_meta["required_evidence"] == [
        "specialized_verifier_review",
        "family_proof_artifact",
    ]
    assert proof_meta["evidence"] == {
        "family_proof_artifact": "artifacts/verification/campaign_execution_family_proof.md"
    }
    assert intent_meta["status"] == "verified"
    assert intent_meta["validating_pis_uids"] == ["PIS-20260409-121606Z-Q5T8"]

    assert "The first-release campaign execution family now has durable contract surfaces" in gate_text
    assert "durable scheduler receipts plus a fresh family rerun support verified closeout" in gate_text
    assert "execution reconciliation now classifies durable" in gate_text
    assert "phase transitions depend on durable outputs, gates, and handoff truth" in gate_text
    assert "Promote `PIS-20260409-121606Z-Q5T8` to `verified`." in gate_text
