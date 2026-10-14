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


def test_fanout_intent_closure_gate_tracks_current_family_truth() -> None:
    parent_dir = _find_patch_instruction_sheet_dir("PIS-20260409-081310Z-E085")
    proof_dir = _find_patch_instruction_sheet_dir("PIS-20260409-082104Z-E5K5")
    gate_dir = _find_patch_instruction_sheet_dir("PIS-20260409-082105Z-F6L6")
    intent_dir = _find_intent_sheet_dir("IS-20260403-212914Z-EA50")

    parent_meta = _load_json(parent_dir / "meta.json")
    proof_meta = _load_json(proof_dir / "meta.json")
    gate_meta = _load_json(gate_dir / "meta.json")
    intent_meta = _load_json(intent_dir / "meta.json")
    intent_sheet_text = (intent_dir / "intent_sheet.md").read_text(encoding="utf-8")
    gate_events = [
        json.loads(line)
        for line in (gate_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    gate_text = (
        gate_dir / "artifacts" / "verification" / "is09_fanout_job_contract_intent_closure_gate.md"
    ).read_text(encoding="utf-8")
    parent_audit_text = (
        parent_dir / "artifacts" / "verification" / "fanout_job_contract_family_closeout_audit.md"
    ).read_text(encoding="utf-8")

    assert gate_meta["status"] == "verified"
    assert gate_meta["required_evidence"] == [
        "specialized_verifier_review",
        "intent_closure_gate_artifact",
    ]
    assert gate_meta["evidence"] == {
        "intent_closure_gate_artifact": "artifacts/verification/is09_fanout_job_contract_intent_closure_gate.md"
    }
    assert gate_meta["integration_meta"]["required_final_child_uid"] == "PIS-20260409-082104Z-E5K5"
    assert proof_meta["status"] == "verified"
    assert proof_meta["required_evidence"] == ["artifacts/verification/fanout_job_family_proof.md"]
    assert proof_meta["evidence"] == {
        "family_proof_artifact": "artifacts/verification/fanout_job_family_proof.md"
    }
    assert parent_meta["status"] == "verified"
    assert parent_meta["evidence"]["closeout_audit"] == (
        "artifacts/verification/fanout_job_contract_family_closeout_audit.md"
    )
    assert parent_meta["evidence"]["rerun_mode"] == "broad_current_state_rerun_v1"
    assert intent_meta["status"] == "verified"
    assert "status: verified" in intent_sheet_text
    assert intent_meta["derived_pis_uids"] == ["PIS-20260409-081310Z-E085"]
    assert intent_meta["validating_pis_uids"] == ["PIS-20260409-082105Z-F6L6"]

    latest_meta_event = next(event for event in reversed(gate_events) if event["type"] == "meta_updated")
    latest_reconcile_event = next(event for event in reversed(gate_events) if event["type"] == "status_reconcile_reviewed")
    latest_verification_event = next(event for event in reversed(gate_events) if event["type"] == "verification_checked")

    assert latest_meta_event["payload"]["required_evidence"] == gate_meta["required_evidence"]
    assert latest_meta_event["payload"]["evidence"] == gate_meta["evidence"]
    assert latest_reconcile_event["payload"]["artifact"] == (
        "artifacts/verification/is09_fanout_job_contract_intent_closure_gate.md"
    )
    assert latest_reconcile_event["payload"]["verdict"] == (
        "verified_from_durable_review_and_green_family_rerun"
    )
    assert latest_verification_event["payload"]["artifact"] == (
        "artifacts/verification/is09_fanout_job_contract_intent_closure_gate.md"
    )
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["payload"]["stronger_status_earned"] is True

    assert "family now earns intent-boundary closure" in gate_text
    assert "specialized verifier review" in gate_text
    assert "Promote `PIS-20260409-082105Z-F6L6`" in gate_text
    assert "No integration delta was found" in gate_text

    assert "Parent UID: `PIS-20260409-081310Z-E085`" in parent_audit_text
    assert "All six child items now earn `verified`." in parent_audit_text
    assert "No integration delta found inside the family-touched repo scope." in parent_audit_text
