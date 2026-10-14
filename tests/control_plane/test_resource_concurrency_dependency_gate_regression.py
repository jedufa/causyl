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


def _relevant_event_utcs(events: list[dict]) -> list[str]:
    relevant_types = {
        "meta_updated",
        "status_reconcile_reviewed",
        "verification_checked",
        "status_changed",
        "ledger_status_changed",
    }
    return [event["utc"] for event in events if event["type"] in relevant_types]


def test_resource_concurrency_dependency_gate_artifact_matches_meta_and_latest_events() -> None:
    item_dir = _find_patch_instruction_sheet_dir("PIS-20260407-043008Z-Z8T9")
    artifact = _load_json(item_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json")
    meta = _load_json(item_dir / "meta.json")
    events = [
        json.loads(line)
        for line in (item_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert artifact["uid"] == "PIS-20260407-043008Z-Z8T9"
    assert artifact["decision"] == "pass_verified_resource_concurrency_family_dependency_truth"
    assert "all verified in sibling ledger metadata" in artifact["smallest_honest_claim"]
    assert meta["status"] == "verified"
    assert meta["required_evidence"] == [
        "artifacts/verification/resource_concurrency_intent_validation_summary.md",
        "artifacts/verification/resource_concurrency_dependency_gate.json",
    ]
    assert meta["evidence"]["resource_concurrency_intent_validation_summary"] == (
        "artifacts/verification/resource_concurrency_intent_validation_summary.md"
    )
    assert meta["evidence"]["resource_concurrency_dependency_gate"] == (
        "artifacts/verification/resource_concurrency_dependency_gate.json"
    )

    blocking_uids = [row["uid"] for row in artifact["blocking_prerequisites"]]
    ready_rows = artifact["ready_prerequisites"]
    ready_uids = [row["uid"] for row in ready_rows]
    assert blocking_uids == []
    assert ready_uids == [
        "PIS-20260407-043001Z-R2K1",
        "PIS-20260407-043002Z-S4M2",
        "PIS-20260407-043003Z-T6N3",
        "PIS-20260407-043004Z-V8P4",
        "PIS-20260407-043005Z-W2Q5",
        "PIS-20260407-043006Z-X4R6",
        "PIS-20260407-043007Z-Y6S7",
    ]
    assert all(row["status"] == "verified" for row in ready_rows)

    latest_meta_event = next(event for event in reversed(events) if event["type"] == "meta_updated")
    latest_reconcile_event = next(event for event in reversed(events) if event["type"] == "status_reconcile_reviewed")
    latest_verification_event = next(event for event in reversed(events) if event["type"] == "verification_checked")
    latest_status_event = next(event for event in reversed(events) if event["type"] == "status_changed")
    latest_ledger_status_event = next(event for event in reversed(events) if event["type"] == "ledger_status_changed")
    assert latest_meta_event["payload"]["required_evidence"] == meta["required_evidence"]
    assert latest_reconcile_event["payload"]["artifact"] == "artifacts/verification/resource_concurrency_dependency_gate.json"
    assert latest_reconcile_event["payload"]["verdict"] == "pass_verified_dependency_truth"
    assert latest_verification_event["payload"]["artifact"] == "artifacts/verification/resource_concurrency_dependency_gate.json"
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["payload"]["stronger_status_earned"] is True
    assert latest_status_event["payload"]["status"] == "verified"
    assert latest_ledger_status_event["payload"]["status"] == "verified"
    assert _relevant_event_utcs(events) == sorted(_relevant_event_utcs(events))
    assert (
        latest_reconcile_event["utc"]
        == latest_verification_event["utc"]
        == latest_status_event["utc"]
        == latest_ledger_status_event["utc"]
        == artifact["review_utc"]
        == meta["updated_utc"]
    )


def test_resource_concurrency_dependency_gate_tracks_sibling_meta_statuses() -> None:
    item_dir = _find_patch_instruction_sheet_dir("PIS-20260407-043008Z-Z8T9")
    artifact = _load_json(item_dir / "artifacts" / "verification" / "resource_concurrency_dependency_gate.json")

    for row in artifact["ready_prerequisites"]:
        matches = list(item_dir.parent.glob(f"{row['uid']}__*/meta.json"))
        assert matches, f"missing prerequisite meta for {row['uid']}"
        meta = _load_json(matches[0])
        assert row["status"] == meta["status"]
        assert meta["status"] == "verified"
        assert meta["title"] == row["title"]


def test_resource_concurrency_intent_validation_summary_records_gate_boundary() -> None:
    item_dir = _find_patch_instruction_sheet_dir("PIS-20260407-043008Z-Z8T9")
    summary = (item_dir / "artifacts" / "verification" / "resource_concurrency_intent_validation_summary.md").read_text(
        encoding="utf-8"
    )

    assert "Primary selector" in summary
    assert "resource_concurrency_dependency_gate.json" in summary
    assert "family-wide proof child is closure-ready" in summary


def test_resource_concurrency_family_closeout_promotes_parent_and_intent_after_final_child_proof() -> None:
    parent_dir = _find_patch_instruction_sheet_dir("PIS-20260407-041735Z-1DE7")
    intent_dir = _find_intent_sheet_dir("IS-20260403-212913Z-60F4")

    parent_meta = _load_json(parent_dir / "meta.json")
    intent_meta = _load_json(intent_dir / "meta.json")

    assert parent_meta["status"] == "verified"
    assert parent_meta["required_evidence"] == [
        "artifacts/verification/resource_concurrency_family_closeout_audit.md"
    ]
    assert parent_meta["evidence"] == {
        "closeout_audit": "artifacts/verification/resource_concurrency_family_closeout_audit.md"
    }
    assert (
        parent_dir / "artifacts" / "verification" / "resource_concurrency_family_closeout_audit.md"
    ).exists()

    assert intent_meta["status"] == "verified"
    assert intent_meta["validating_pis_uids"] == ["PIS-20260407-043008Z-Z8T9"]
