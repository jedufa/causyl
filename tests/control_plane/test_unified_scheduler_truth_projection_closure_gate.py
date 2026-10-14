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


def test_unified_scheduler_truth_projection_intent_closure_gate_tracks_current_family_truth() -> None:
    parent_dir = _find_patch_instruction_sheet_dir("PIS-20260408-180420Z-CDCA")
    proof_dir = _find_patch_instruction_sheet_dir("PIS-20260408-181104Z-H5J5")
    closure_dir = _find_patch_instruction_sheet_dir("PIS-20260408-181105Z-J6K6")
    gate_dir = _find_patch_instruction_sheet_dir("PIS-20260408-184329Z-3C92")
    intent_dir = _find_intent_sheet_dir("IS-20260408-170440Z-B1E1")

    parent_meta = _load_json(parent_dir / "meta.json")
    proof_meta = _load_json(proof_dir / "meta.json")
    closure_meta = _load_json(closure_dir / "meta.json")
    gate_meta = _load_json(gate_dir / "meta.json")
    intent_meta = _load_json(intent_dir / "meta.json")
    gate_events = [
        json.loads(line)
        for line in (gate_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    audit_text = (
        gate_dir / "artifacts" / "verification" / "unified_scheduler_truth_projection_intent_closure_gate.md"
    ).read_text(encoding="utf-8")

    assert gate_meta["status"] == "draft"
    assert gate_meta["required_evidence"] == [
        "specialized_verifier_review",
        "intent_closure_gate_artifact",
    ]
    assert gate_meta["evidence"] == {
        "intent_closure_gate_artifact": "artifacts/verification/unified_scheduler_truth_projection_intent_closure_gate.md"
    }
    assert gate_meta["integration_meta"]["required_final_child_uid"] == "PIS-20260408-181105Z-J6K6"

    latest_meta_event = next(event for event in reversed(gate_events) if event["type"] == "meta_updated")
    latest_reconcile_event = next(event for event in reversed(gate_events) if event["type"] == "status_reconcile_reviewed")
    latest_verification_event = next(event for event in reversed(gate_events) if event["type"] == "verification_checked")

    assert latest_meta_event["payload"]["required_evidence"] == gate_meta["required_evidence"]
    assert latest_meta_event["payload"]["evidence"] == gate_meta["evidence"]
    assert latest_reconcile_event["payload"]["artifact"] == (
        "artifacts/verification/unified_scheduler_truth_projection_intent_closure_gate.md"
    )
    assert latest_reconcile_event["payload"]["verdict"] == "blocked_pending_gate_specific_verifier_review"
    assert latest_verification_event["payload"]["artifact"] == (
        "artifacts/verification/unified_scheduler_truth_projection_intent_closure_gate.md"
    )
    assert latest_verification_event["payload"]["result"] == "blocked"
    assert latest_verification_event["payload"]["stronger_status_earned"] is False

    assert parent_meta["status"] == "verified"
    assert proof_meta["status"] == "verified"
    assert closure_meta["status"] == "verified"
    assert intent_meta["status"] == "draft"
    assert intent_meta["validating_pis_uids"] == ["PIS-20260408-184329Z-3C92"]

    assert "six-child implementation stack through `PIS-20260408-181105Z-J6K6` has" in audit_text
    assert "reconciled to verified child truth" in audit_text
    assert "parent `meta.json` now names" in audit_text
    assert "scheduler_projection_family_parity_audit.md" in audit_text
    assert "unified_scheduler_truth_projection_intent_boundary_closure.md" in audit_text
    assert "unified_scheduler_truth_projection_family_closeout_audit.md" in audit_text
