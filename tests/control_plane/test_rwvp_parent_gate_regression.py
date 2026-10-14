from __future__ import annotations

import json
from pathlib import Path

from infra.webui.ledger import ledger_storage

REPO_ROOT = Path(__file__).resolve().parents[2]
LEDGER_ROOT = ledger_storage.ledger_patch_instruction_sheets_root(REPO_ROOT)
ITEM_UID = "PIS-20260327-124630Z-RWVP"


def _item_dir(uid: str) -> Path:
    item_dir = ledger_storage.item_dir_for_uid(LEDGER_ROOT, uid)
    if item_dir is None:
        raise AssertionError(f"Missing ledger item for {uid}")
    return item_dir


ITEM_DIR = _item_dir(ITEM_UID)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_rwvp_parent_gate_artifacts_exist_and_match_meta() -> None:
    meta = _load_json(ITEM_DIR / "meta.json")
    reconcile = _load_json(ITEM_DIR / "artifacts" / "status_reconcile_verification.json")
    v4h6_gate = _load_json(ITEM_DIR / "artifacts" / "verification" / "v4h6_non_human_platform_gate.json")
    d8fb_gate = _load_json(ITEM_DIR / "artifacts" / "verification" / "d8fb_operator_front_door_gate.json")

    assert meta["status"] == "verified"
    assert meta["required_evidence"] == [
        "artifacts/status_reconcile_verification.json",
        "artifacts/verification/v4h6_non_human_platform_gate.json",
        "artifacts/verification/d8fb_operator_front_door_gate.json",
    ]
    assert meta["evidence"]["v4h6_non_human_platform_gate"] == "artifacts/verification/v4h6_non_human_platform_gate.json"
    assert meta["evidence"]["d8fb_operator_front_door_gate"] == "artifacts/verification/d8fb_operator_front_door_gate.json"

    assert reconcile["evidence"]["required_evidence_missing"] == []
    assert set(reconcile["evidence"]["required_evidence_present"]) == {
        "artifacts/status_reconcile_verification.json",
        "artifacts/verification/v4h6_non_human_platform_gate.json",
        "artifacts/verification/d8fb_operator_front_door_gate.json",
    }
    assert "Parent verified status is now earned" in reconcile["reason"]

    assert v4h6_gate["uid"] == d8fb_gate["uid"] == "PIS-20260327-124630Z-RWVP"
    assert v4h6_gate["status"] == d8fb_gate["status"] == "pass"
    assert v4h6_gate["schema_version"] == d8fb_gate["schema_version"] == "parent_promotion_gate_v1"


def test_rwvp_parent_gate_artifacts_track_current_child_statuses() -> None:
    v4h6_gate = _load_json(ITEM_DIR / "artifacts" / "verification" / "v4h6_non_human_platform_gate.json")
    d8fb_gate = _load_json(ITEM_DIR / "artifacts" / "verification" / "d8fb_operator_front_door_gate.json")

    v4h6_meta = _load_json(_item_dir("PIS-20260327-172341Z-V4H6") / "meta.json")
    k2n7_meta = _load_json(_item_dir("PIS-20260327-173634Z-K2N7") / "meta.json")
    d8fb_meta = _load_json(_item_dir("PIS-20260327-132825Z-D8FB") / "meta.json")

    assert v4h6_gate["child_ref"]["uid"] == "PIS-20260327-172341Z-V4H6"
    assert v4h6_gate["child_ref"]["status"] == v4h6_meta["status"] == "verified"
    assert any(
        row["path"].endswith("artifacts/dependency_gate_review.json")
        for row in v4h6_gate["evidence_refs"]
    )
    assert any(
        row["path"].endswith("PIS-20260327-173634Z-K2N7__autonomous-codex-platform-exercise-and-repair-campaign/meta.json")
        for row in v4h6_gate["evidence_refs"]
    )
    assert k2n7_meta["status"] == "verified"

    assert d8fb_gate["child_ref"]["uid"] == "PIS-20260327-132825Z-D8FB"
    assert d8fb_gate["child_ref"]["status"] == d8fb_meta["status"] == "verified"
    assert any(
        row["path"].endswith("artifacts/verification/status_reconcile__20260329T090014Z.json")
        for row in d8fb_gate["evidence_refs"]
    )
    assert d8fb_gate["blocking_reasons"] == []


def test_rwvp_parent_latest_events_record_gate_backed_reconcile() -> None:
    reconcile = _load_json(ITEM_DIR / "artifacts" / "status_reconcile_verification.json")
    events = [
        json.loads(line)
        for line in (ITEM_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    latest_reconcile = next(
        event for event in reversed(events) if event["type"] == "status_reconcile_reviewed"
    )
    latest_check = next(event for event in reversed(events) if event["type"] == "verification_checked")

    assert latest_reconcile["payload"]["artifact"] == "artifacts/status_reconcile_verification.json"
    assert latest_reconcile["payload"]["status"] == "verified"
    assert latest_reconcile["payload"]["verdict"] == "verified"
    assert latest_check["payload"]["artifact"] == "artifacts/status_reconcile_verification.json"
    assert latest_check["payload"]["result"] == "pass"
    assert latest_check["payload"]["status"] == "verified"
    assert latest_check["payload"]["stronger_status_earned"] is True
    assert "verified" in latest_check["payload"]["reason"]
    assert "verified" in reconcile["reason"]
