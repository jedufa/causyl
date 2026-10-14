from __future__ import annotations

import json
from pathlib import Path

from infra.webui.ledger import ledger_storage

REPO_ROOT = Path(__file__).resolve().parents[2]
ITEM_UID = "PIS-20260327-173634Z-K2N7"


def _find_patch_instruction_sheet_dir(uid: str) -> Path:
    item_dir = ledger_storage.item_dir_for_uid(
        ledger_storage.ledger_patch_instruction_sheets_root(REPO_ROOT),
        uid,
    )
    if item_dir is None:
        raise AssertionError(f"missing patch instruction sheet dir for {uid}")
    return item_dir


ITEM_DIR = _find_patch_instruction_sheet_dir(ITEM_UID)


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_k2n7_dependency_gate_artifact_matches_meta_and_latest_event() -> None:
    artifact = _load_json(ITEM_DIR / "artifacts" / "dependency_gate_review.json")
    exercise = _load_json(ITEM_DIR / "artifacts" / "first_canonical_exercise.json")
    meta = _load_json(ITEM_DIR / "meta.json")
    events = [
        json.loads(line)
        for line in (ITEM_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert artifact["uid"] == ITEM_UID
    assert artifact["decision"] == "first_canonical_managed_work_exercise_attempted"
    assert "first real ledger-bound non-human managed-work scenario has now been attempted" in artifact["smallest_honest_claim"]
    assert exercise["uid"] == ITEM_UID
    assert exercise["initial_launch"]["binding_result_ok"] is False
    assert exercise["rerun"]["binding_result_ok"] is True
    assert exercise["rerun"]["unified_task_row"] is True
    assert meta["status"] == "verified"
    assert meta["required_evidence"] == [
        "artifacts/dependency_gate_review.json",
        "artifacts/first_canonical_exercise.json",
    ]
    assert meta["evidence"]["dependency_gate_review"] == "artifacts/dependency_gate_review.json"
    assert meta["evidence"]["first_canonical_exercise"] == "artifacts/first_canonical_exercise.json"

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
    ]

    latest_gate_event = next(event for event in reversed(events) if event["type"] == "dependency_gate_recorded")
    latest_exercise_event = next(event for event in reversed(events) if event["type"] == "exercise_recorded")
    latest_verification_event = next(event for event in reversed(events) if event["type"] == "verification_checked")
    assert latest_gate_event["payload"]["artifact"] == "artifacts/dependency_gate_review.json"
    assert latest_gate_event["payload"]["decision"] == artifact["decision"]
    assert latest_gate_event["payload"]["blocking_uids"] == blocking_uids
    assert latest_gate_event["payload"]["ready_uids"] == ready_uids
    assert latest_exercise_event["payload"]["artifact"] == "artifacts/first_canonical_exercise.json"
    assert latest_exercise_event["payload"]["rerun_run_id"] == exercise["rerun"]["run_id"]
    assert latest_gate_event["utc"] == latest_exercise_event["utc"] == artifact["review_utc"]
    assert latest_verification_event["payload"]["result"] == "pass"
    assert latest_verification_event["utc"] == meta["updated_utc"]


def test_k2n7_dependency_gate_artifact_matches_current_prerequisite_statuses() -> None:
    artifact = _load_json(ITEM_DIR / "artifacts" / "dependency_gate_review.json")

    for row in artifact["blocking_prerequisites"]:
        item_dir = _find_patch_instruction_sheet_dir(row["uid"])
        meta = _load_json(item_dir / "meta.json")
        assert meta["status"] == row["status"]
        assert row["status"] in {"draft", "in_progress"}

    for row in artifact["ready_prerequisites"]:
        item_dir = _find_patch_instruction_sheet_dir(row["uid"])
        meta = _load_json(item_dir / "meta.json")
        assert meta["status"] == row["status"] == "verified"


def test_k2n7_ready_checkpoint_does_not_claim_runtime_exercise() -> None:
    artifact = _load_json(ITEM_DIR / "artifacts" / "dependency_gate_review.json")
    exercise = _load_json(ITEM_DIR / "artifacts" / "first_canonical_exercise.json")
    meta = _load_json(ITEM_DIR / "meta.json")
    events = [
        json.loads(line)
        for line in (ITEM_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert artifact["decision"] == "first_canonical_managed_work_exercise_attempted"
    assert meta["actions"] == []
    assert meta["changesets"] == []
    assert meta["links"] == {"exports": [], "runs": []}
    assert any(event["type"] == "exercise_recorded" for event in events)
    assert exercise["repair"]["changed_files"] == [
        "infra/controlplane/scheduled_jobs.py",
        "infra/webui/server_agents.py",
        "tests/control_plane/test_scheduled_jobs.py",
    ]
    assert "prompt-following quality" in exercise["remaining_gaps"][0]


def test_k2n7_latest_gate_receipt_tracks_exercise_checkpoint_append_only() -> None:
    artifact = _load_json(ITEM_DIR / "artifacts" / "dependency_gate_review.json")
    exercise = _load_json(ITEM_DIR / "artifacts" / "first_canonical_exercise.json")
    meta = _load_json(ITEM_DIR / "meta.json")
    events = [
        json.loads(line)
        for line in (ITEM_DIR / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert meta["release_state"] == "draft"
    assert events[-4]["type"] == "meta_updated"
    assert events[-3]["type"] == "status_changed"
    assert events[-2]["type"] == "ledger_status_changed"
    assert events[-1]["type"] == "verification_checked"
    assert events[-3]["prev_hash"] == events[-4]["hash"]
    assert events[-2]["prev_hash"] == events[-3]["hash"]
    assert events[-1]["prev_hash"] == events[-2]["hash"]
    latest_gate_event = next(event for event in reversed(events) if event["type"] == "dependency_gate_recorded")
    latest_exercise_event = next(event for event in reversed(events) if event["type"] == "exercise_recorded")
    assert latest_gate_event["payload"]["artifact"] == "artifacts/dependency_gate_review.json"
    assert latest_gate_event["payload"]["decision"] == artifact["decision"]
    assert latest_exercise_event["payload"]["artifact"] == "artifacts/first_canonical_exercise.json"
    assert latest_exercise_event["payload"]["initial_run_id"] == exercise["initial_launch"]["run_id"]
    assert latest_exercise_event["payload"]["rerun_run_id"] == exercise["rerun"]["run_id"]
    assert events[-1]["payload"]["result"] == "pass"
