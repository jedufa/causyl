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
    }
    return [event["utc"] for event in events if event["type"] in relevant_types]


def test_continuation_dependency_gate_artifact_matches_meta_and_latest_events() -> None:
    item_dir = _find_patch_instruction_sheet_dir("PIS-20260407-063108Z-H8K8")
    artifact = _load_json(item_dir / "artifacts" / "verification" / "continuation_dependency_gate.json")
    meta = _load_json(item_dir / "meta.json")
    events = [
        json.loads(line)
        for line in (item_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert artifact["uid"] == "PIS-20260407-063108Z-H8K8"
    assert artifact["decision"] == "pass_verified_dependency_truth"
    assert "prerequisite children A1C1 through G7J7 are now verified" in artifact["smallest_honest_claim"]
    assert meta["status"] == "verified"
    assert meta["required_evidence"] == [
        "artifacts/verification/continuation_intent_validation_summary.md",
        "artifacts/verification/continuation_dependency_gate.json",
    ]
    assert meta["evidence"]["continuation_intent_validation_summary"] == (
        "artifacts/verification/continuation_intent_validation_summary.md"
    )
    assert meta["evidence"]["continuation_dependency_gate"] == "artifacts/verification/continuation_dependency_gate.json"

    ready_uids = [row["uid"] for row in artifact["ready_prerequisites"]]
    assert ready_uids == [
        "PIS-20260407-063101Z-A1C1",
        "PIS-20260407-063102Z-B2D2",
        "PIS-20260407-063103Z-C3E3",
        "PIS-20260407-063104Z-D4F4",
        "PIS-20260407-063105Z-E5G5",
        "PIS-20260407-063106Z-F6H6",
        "PIS-20260407-063107Z-G7J7",
    ]
    assert artifact["blocking_prerequisites"] == []
    assert all(row["status"] == "verified" for row in artifact["ready_prerequisites"])

    latest_meta_event = next(event for event in reversed(events) if event["type"] == "meta_updated")
    latest_reconcile_event = next(
        event
        for event in reversed(events)
        if event["type"] == "status_reconcile_reviewed"
        and event["payload"].get("verdict") == "pass_verified_dependency_truth"
    )
    latest_verification_event = next(
        event
        for event in reversed(events)
        if event["type"] == "verification_checked"
        and event["payload"].get("artifact") == "artifacts/verification/continuation_dependency_gate.json"
        and event["payload"].get("result") == "pass"
    )
    assert latest_meta_event["payload"]["required_evidence"] == meta["required_evidence"]
    assert latest_reconcile_event["payload"]["artifact"] == "artifacts/verification/continuation_dependency_gate.json"
    assert latest_reconcile_event["payload"]["verdict"] == "pass_verified_dependency_truth"
    assert latest_verification_event["payload"]["artifact"] == "artifacts/verification/continuation_dependency_gate.json"
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["payload"]["stronger_status_earned"] is True
    assert _relevant_event_utcs(events) == sorted(_relevant_event_utcs(events))
    assert latest_reconcile_event["payload"]["verified_utc"] == artifact["review_utc"]
    assert latest_verification_event["utc"] >= artifact["review_utc"]


def test_continuation_dependency_gate_tracks_sibling_meta_statuses() -> None:
    item_dir = _find_patch_instruction_sheet_dir("PIS-20260407-063108Z-H8K8")
    artifact = _load_json(item_dir / "artifacts" / "verification" / "continuation_dependency_gate.json")

    for row in artifact["ready_prerequisites"]:
        matches = list(item_dir.parent.glob(f"{row['uid']}__*/meta.json"))
        assert matches, f"missing prerequisite meta for {row['uid']}"
        meta = _load_json(matches[0])
        assert row["status"] == meta["status"]
        assert meta["status"] == "verified"
        assert meta["title"] == row["title"]


def test_continuation_intent_validation_summary_records_gate_boundary() -> None:
    item_dir = _find_patch_instruction_sheet_dir("PIS-20260407-063108Z-H8K8")
    summary = (item_dir / "artifacts" / "verification" / "continuation_intent_validation_summary.md").read_text(
        encoding="utf-8"
    )

    assert "Primary selector" in summary
    assert "continuation_dependency_gate.json" in summary
    assert "family-wide proof child is not closure-ready" in summary


def test_continuation_family_closeout_gate_promotes_once_required_final_child_truth_is_earned() -> None:
    closeout_dir = _find_patch_instruction_sheet_dir("PIS-20260407-172959Z-DAB8")
    child_dir = _find_patch_instruction_sheet_dir("PIS-20260407-063108Z-H8K8")
    intent_dir = _find_intent_sheet_dir("IS-20260403-212913Z-348E")

    closeout_meta = _load_json(closeout_dir / "meta.json")
    child_meta = _load_json(child_dir / "meta.json")
    intent_meta = _load_json(intent_dir / "meta.json")
    closeout_events = [
        json.loads(line)
        for line in (closeout_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    audit_text = (closeout_dir / "artifacts" / "verification" / "continuation_family_closeout_audit.md").read_text(
        encoding="utf-8"
    )

    assert closeout_meta["status"] == "verified"
    assert closeout_meta["required_evidence"] == [
        "artifacts/verification/continuation_family_closeout_audit.md"
    ]
    assert closeout_meta["evidence"] == {
        "closeout_audit": "artifacts/verification/continuation_family_closeout_audit.md"
    }
    assert closeout_meta["integration_meta"]["required_final_child_uid"] == "PIS-20260407-063108Z-H8K8"

    latest_meta_event = next(event for event in reversed(closeout_events) if event["type"] == "meta_updated")
    latest_reconcile_event = next(
        event
        for event in reversed(closeout_events)
        if event["type"] == "status_reconcile_reviewed" and event["payload"].get("verdict") == "verified"
    )
    latest_verification_event = next(
        event
        for event in reversed(closeout_events)
        if event["type"] == "verification_checked"
        and event["payload"].get("artifact") == "artifacts/verification/continuation_family_closeout_audit.md"
        and event["payload"].get("result") == "pass"
    )
    assert latest_meta_event["payload"]["required_evidence"] == closeout_meta["required_evidence"]
    assert latest_reconcile_event["payload"]["artifact"] == "artifacts/verification/continuation_family_closeout_audit.md"
    assert latest_reconcile_event["payload"]["verdict"] == "verified"
    assert latest_verification_event["payload"]["artifact"] == "artifacts/verification/continuation_family_closeout_audit.md"
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["payload"]["stronger_status_earned"] is True

    assert child_meta["status"] == "verified"
    assert intent_meta["status"] == "verified"
    assert intent_meta["validating_pis_uids"] == ["PIS-20260407-172959Z-DAB8"]
    assert "PIS-20260407-063108Z-H8K8` is now `verified`" in audit_text
    assert "now truthfully promotes both the parent family and source intent" in audit_text
