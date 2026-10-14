from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_patch_instruction_sheet_dir(uid: str) -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{uid}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing patch instruction sheet dir for {uid}")


def _find_intent_sheet_dir(uid: str) -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "intent_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{uid}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing intent sheet dir for {uid}")


def test_experiment_scaffold_intent_closure_gate_tracks_validating_pointer_and_verified_evidence() -> None:
    intent_dir = _find_intent_sheet_dir("IS-20260403-212913Z-E983")
    intent_meta = _load_json(intent_dir / "meta.json")

    assert intent_meta["validating_pis_uids"] == ["PIS-20260408-042709Z-2E46"]

    item_dir = _find_patch_instruction_sheet_dir("PIS-20260408-042709Z-2E46")
    item_meta = _load_json(item_dir / "meta.json")
    summary_path = item_dir / "artifacts" / "verification" / "experiment_scaffolding_intent_closure_gate.md"
    summary = summary_path.read_text(encoding="utf-8")

    assert item_meta["status"] == "verified"
    assert item_meta["required_evidence"] == [
        "artifacts/verification/experiment_scaffolding_intent_closure_gate.md"
    ]
    assert item_meta["evidence"] == {
        "intent_closure_gate": "artifacts/verification/experiment_scaffolding_intent_closure_gate.md"
    }
    assert item_meta["depends_on"] == [
        "PIS-20260407-202100Z-K2S1",
        "PIS-20260407-202101Z-L3T2",
        "PIS-20260407-202102Z-M4U3",
        "PIS-20260407-202103Z-N5V4",
        "PIS-20260407-202104Z-P6W5",
    ]
    assert summary_path.exists()
    assert "Focused proof child reused: PIS-20260407-202103Z-N5V4" in summary
    assert "Family closure child reused: PIS-20260407-202104Z-P6W5" in summary
    assert "the source intent is now promoted to verified" in summary
