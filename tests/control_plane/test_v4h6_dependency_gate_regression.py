from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
ITEM_DIR = (
    REPO_ROOT
    / "governance"
    / "ledger"
    / "items"
    / "PIS-20260327-172341Z-V4H6__non-human-end-to-end-platform-validation-suite"
)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v4h6_dependency_gate_artifact_matches_meta_and_latest_events() -> None:
    artifact = _load_json(ITEM_DIR / "artifacts" / "dependency_gate_review.json")
    meta = _load_json(ITEM_DIR / "meta.json")
    events = [
        json.loads(line)
        for line in (ITEM_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert artifact["uid"] == "PIS-20260327-172341Z-V4H6"
    assert artifact["decision"] == "non_human_platform_validation_passed"
    assert "scheduler-seeded work" in artifact["smallest_honest_claim"]
    assert meta["status"] == "verified"
    assert meta["required_evidence"] == ["artifacts/dependency_gate_review.json"]
    assert meta["evidence"]["dependency_gate_review"] == "artifacts/dependency_gate_review.json"

    blocking_uids = [row["uid"] for row in artifact["blocking_prerequisites"]]
    ready_uids = [row["uid"] for row in artifact["ready_prerequisites"]]
    assert blocking_uids == []
    assert ready_uids == [
        "PIS-20260327-132825Z-81D2",
        "PIS-20260327-132825Z-F1A1",
        "PIS-20260327-132825Z-3E6D",
        "PIS-20260327-132825Z-66E0",
        "PIS-20260327-132825Z-7EDB",
        "PIS-20260327-132825Z-3DF1",
        "PIS-20260327-132825Z-0EEC",
        "PIS-20260327-173634Z-K2N7",
    ]

    latest_gate_event = next(event for event in reversed(events) if event["type"] == "dependency_gate_recorded")
    assert latest_gate_event["payload"]["artifact"] == "artifacts/dependency_gate_review.json"
    assert latest_gate_event["payload"]["decision"] == artifact["decision"]
    assert latest_gate_event["payload"]["blocking_uids"] == blocking_uids
    assert latest_gate_event["payload"]["ready_uids"] == ready_uids

    latest_status_event = next(event for event in reversed(events) if event["type"] == "status_changed")
    latest_verification_event = next(event for event in reversed(events) if event["type"] == "verification_checked")
    assert latest_status_event["payload"]["status"] == meta["status"] == "verified"
    assert latest_status_event["utc"] == latest_gate_event["utc"] == latest_verification_event["utc"] == artifact["review_utc"]
    assert meta["updated_utc"] == artifact["review_utc"]
    assert latest_verification_event["payload"]["artifact"] == "artifacts/dependency_gate_review.json"
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["payload"]["stronger_status_earned"] is True


def test_v4h6_dependency_gate_artifact_matches_current_prerequisite_statuses() -> None:
    artifact = _load_json(ITEM_DIR / "artifacts" / "dependency_gate_review.json")

    for row in artifact["blocking_prerequisites"]:
        matches = list(
            (REPO_ROOT / "governance" / "ledger" / "items").glob(f"{row['uid']}__*/meta.json")
        )
        assert matches, f"missing prerequisite meta for {row['uid']}"
        meta = _load_json(matches[0])
        assert meta["status"] == row["status"]
        assert row["status"] in {"draft", "in_progress"}

    for row in artifact["ready_prerequisites"]:
        matches = list(
            (REPO_ROOT / "governance" / "ledger" / "items").glob(f"{row['uid']}__*/meta.json")
        )
        assert matches, f"missing prerequisite meta for {row['uid']}"
        meta = _load_json(matches[0])
        assert meta["status"] == row["status"] == "verified"


def test_v4h6_status_reconcile_does_not_claim_suite_or_status_upgrade() -> None:
    artifact = _load_json(ITEM_DIR / "artifacts" / "dependency_gate_review.json")
    meta = _load_json(ITEM_DIR / "meta.json")
    events = [
        json.loads(line)
        for line in (ITEM_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert meta["release_state"] == "draft"
    assert meta["actions"] == []
    assert meta["changesets"] == []
    assert meta["links"] == {"exports": [], "runs": []}
    latest_meta_event = next(event for event in reversed(events) if event["type"] == "meta_updated")
    latest_status_event = next(event for event in reversed(events) if event["type"] == "status_changed")
    latest_ledger_status_event = next(event for event in reversed(events) if event["type"] == "ledger_status_changed")
    latest_gate_event = next(event for event in reversed(events) if event["type"] == "dependency_gate_recorded")
    latest_verification_event = next(event for event in reversed(events) if event["type"] == "verification_checked")

    assert latest_meta_event["payload"]["required_evidence"] == ["artifacts/dependency_gate_review.json"]
    assert latest_status_event["payload"]["status"] == meta["status"] == "verified"
    assert latest_ledger_status_event["payload"]["status"] == "verified"
    assert latest_status_event["utc"] == latest_ledger_status_event["utc"] == latest_gate_event["utc"] == latest_verification_event["utc"] == artifact["review_utc"]
    assert latest_gate_event["payload"]["artifact"] == "artifacts/dependency_gate_review.json"
    assert latest_gate_event["payload"]["decision"] == artifact["decision"]
    assert latest_verification_event["payload"]["artifact"] == "artifacts/dependency_gate_review.json"
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["payload"]["stronger_status_earned"] is True
    assert "non-human managed-work substrate is now proven end to end" in latest_verification_event["payload"]["reason"]
