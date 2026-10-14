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


def test_benchmark_v3_intent_closure_artifact_tracks_real_proof_and_stays_fail_closed() -> None:
    parent_dir = _find_patch_instruction_sheet_dir("PIS-20260408-151235Z-CBC1")
    closeout_dir = _find_patch_instruction_sheet_dir("PIS-20260408-151524Z-H8E5")
    proof_dir = _find_patch_instruction_sheet_dir("PIS-20260408-151523Z-G7D4")

    parent_meta = _load_json(parent_dir / "meta.json")
    closeout_meta = _load_json(closeout_dir / "meta.json")
    closeout_events = [
        json.loads(line)
        for line in (closeout_dir / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    proof_artifact = _load_json(
        proof_dir / "artifacts" / "verification" / "benchmark_v3_family_proof__20260408T155702Z.json"
    )
    audit_text = (
        closeout_dir / "artifacts" / "verification" / "benchmark_v3_intent_boundary_closure.md"
    ).read_text(encoding="utf-8")
    family_audit_text = (
        parent_dir / "artifacts" / "verification" / "benchmark_v3_family_closeout_audit.md"
    ).read_text(encoding="utf-8")

    assert parent_meta["status"] == "verified"
    assert parent_meta["evidence"]["closeout_audit"] == "artifacts/verification/benchmark_v3_family_closeout_audit.md"
    assert all(
        "governance/ledger/patch_instruction_sheets/causyl/active/" not in command
        for command in parent_meta["evidence"]["validation_commands"]
    )
    assert any(
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/PIS-20260408-151523Z-G7D4"
        in command
        for command in parent_meta["evidence"]["validation_commands"]
    )
    assert any(
        "governance/ledger/patch_instruction_sheets/causyl/verified/2026/04/PIS-20260408-151524Z-H8E5"
        in command
        for command in parent_meta["evidence"]["validation_commands"]
    )
    assert closeout_meta["status"] == "verified"
    assert closeout_meta["required_evidence"] == [
        "specialized_verifier_review",
        "intent_boundary_closure_artifact",
    ]
    assert closeout_meta["evidence"] == {
        "intent_boundary_closure_artifact": "artifacts/verification/benchmark_v3_intent_boundary_closure.md"
    }
    gate_restore_event = next(
        event
        for event in reversed(closeout_events)
        if event["type"] == "meta_updated"
        and "required_evidence" in event.get("payload", {})
    )
    assert closeout_events[-1]["type"] == "ledger_status_changed"
    assert closeout_events[-1]["prev_hash"] == closeout_events[-2]["hash"]
    assert closeout_events[-2]["type"] == "status_changed"
    assert gate_restore_event["payload"]["required_evidence"] == [
        "specialized_verifier_review",
        "intent_boundary_closure_artifact",
    ]
    assert gate_restore_event["payload"]["evidence"] == {
        "intent_boundary_closure_artifact": "artifacts/verification/benchmark_v3_intent_boundary_closure.md"
    }
    assert "restored specialized verifier review gating" in gate_restore_event["payload"]["note"]

    assert proof_artifact["artifact_contract"] == {
        "frontend_entrypoint": "artifacts/benchmark/chess_agent_v3.py",
        "support_root": "artifacts/benchmark/chess_agent_v3_support",
        "validation_root": "artifacts/benchmark/tests_v3",
    }
    assert proof_artifact["acceptance_matrix"]["successful_tool_using_run"]["status"] == "pass"
    assert proof_artifact["acceptance_matrix"]["negative_path_failure_case"]["status"] == "pass"
    assert proof_artifact["acceptance_matrix"]["portability_boundary_check"]["status"] == "pass"

    assert "artifacts/benchmark/chess_agent_v3.py" in audit_text
    assert "artifacts/benchmark/chess_agent_v3_support/" in audit_text
    assert "artifacts/benchmark/tests_v3/" in audit_text
    assert "External benchmark-runner evaluation remains deferred." in audit_text
    assert "family status is now reconciled" in audit_text.lower()
    assert "did not bind source-intent promotion" in audit_text

    assert "sj_20260408T152900Z_2710db" in family_audit_text
    assert "sj_20260408T153557Z_f407b8" in family_audit_text
    assert "sj_20260408T154256Z_45fbce" in family_audit_text
    assert "sj_20260408T154900Z_a50b54" in family_audit_text
    assert "sj_20260408T160044Z_adb70a" in family_audit_text
    assert "Aggregate result: `15 passed`" in family_audit_text
    assert "No integration delta found" in family_audit_text
