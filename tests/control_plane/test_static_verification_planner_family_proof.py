from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PARENT_UID = "PIS-20260413-160253Z-L5P8"
INTENT_UID = "IS-20260413-160252Z-K4N7"
CHILD_UIDS = [
    "PIS-20260413-160254Z-M1Q2",
    "PIS-20260413-161605Z-T7W8",
    "PIS-20260413-161608Z-W1Z2",
    "PIS-20260413-160255Z-N2R3",
    "PIS-20260413-161606Z-U8X9",
    "PIS-20260413-161607Z-V9Y1",
    "PIS-20260413-160256Z-P3S4",
    "PIS-20260413-160257Z-Q4T5",
    "PIS-20260413-160258Z-R5U6",
    "PIS-20260413-161930Z-Z4C5",
    "PIS-20260413-161609Z-X2A3",
    "PIS-20260413-161610Z-Y3B4",
    "PIS-20260413-160259Z-S6V7",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_sheet(base: Path, uid: str) -> Path:
    matches = list(base.glob(f"{uid}__*/meta.json"))
    assert matches, f"missing meta for {uid}"
    return matches[0]


def test_phase_a_parent_manifest_matches_verified_child_stack() -> None:
    parent_dir = _find_sheet(
        REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04",
        PARENT_UID,
    ).parent
    parent_meta = _load_json(parent_dir / "meta.json")
    manifest = _load_json(parent_dir / "artifacts" / "planning" / "program_manifest.json")

    assert manifest["parent_uid"] == PARENT_UID
    assert manifest["intent_uid"] == INTENT_UID
    assert manifest["required_final_child_uid"] == "PIS-20260413-160259Z-S6V7"
    assert manifest["child_uids"] == CHILD_UIDS
    assert parent_meta["child_pis_uids"] == CHILD_UIDS
    assert parent_meta["integration_meta"]["required_final_child_uid"] == "PIS-20260413-160259Z-S6V7"


def test_phase_a_parent_closeout_proof_requires_all_children_verified() -> None:
    verified_base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "04"
    for uid in CHILD_UIDS:
        meta = _load_json(_find_sheet(verified_base, uid))
        assert meta["status"] == "verified"


def test_phase_a_parent_closeout_audit_keeps_claim_ceiling_below_phase_b() -> None:
    parent_dir = _find_sheet(
        REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "04",
        PARENT_UID,
    ).parent
    manifest = _load_json(parent_dir / "artifacts" / "planning" / "program_manifest.json")
    audit = " ".join(
        (parent_dir / "artifacts" / "verification" / "phase_a_planner_family_closeout_audit.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert manifest["claim_ceiling"] == "planning_quality_improved_only"
    assert "does not prove Phase B execution" in audit
