from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
PARENT_UID = "PIS-20260413-170101Z-D7E8"
INTENT_UID = "IS-20260413-160252Z-K4N7"
CHILD_UIDS = [
    "PIS-20260413-170102Z-E8F9",
    "PIS-20260413-170103Z-F9G1",
    "PIS-20260413-170104Z-G1H2",
    "PIS-20260413-170105Z-H2J3",
    "PIS-20260413-170106Z-J3K4",
    "PIS-20260413-170107Z-K4L5",
    "PIS-20260413-170108Z-L5M6",
    "PIS-20260413-170109Z-M6N7",
    "PIS-20260413-170110Z-N7P8",
    "PIS-20260413-170111Z-P8Q9",
    "PIS-20260413-170112Z-Q9R1",
]


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_sheet(base: Path, uid: str) -> Path:
    matches = list(base.glob(f"{uid}__*/meta.json"))
    assert matches, f"missing meta for {uid}"
    return matches[0]


def _find_parent_dir() -> Path:
    root = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for bucket in ("active", "verified"):
        matches = list((root / bucket / "2026" / "04").glob(f"{PARENT_UID}__*"))
        if matches:
            return matches[0]
    raise AssertionError(f"missing parent dir for {PARENT_UID}")


def test_phase_b_parent_manifest_matches_verified_child_stack() -> None:
    parent_dir = _find_parent_dir()
    parent_meta = _load_json(parent_dir / "meta.json")
    manifest = _load_json(parent_dir / "artifacts" / "campaigns" / "campaign_manifest.json")

    assert manifest["parent_uid"] == PARENT_UID
    assert manifest["intent_uid"] == INTENT_UID
    assert manifest["required_anchor_uid"] == "PIS-20260413-170112Z-Q9R1"
    assert manifest["required_closeout_child_uid"] == "PIS-20260413-170110Z-N7P8"
    assert manifest["child_uids"] == CHILD_UIDS
    assert parent_meta["child_pis_uids"] == CHILD_UIDS


def test_phase_b_parent_closeout_proof_requires_all_children_verified() -> None:
    verified_base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "verified" / "2026" / "04"
    for uid in CHILD_UIDS:
        meta = _load_json(_find_sheet(verified_base, uid))
        assert meta["status"] == "verified"


def test_phase_b_parent_closeout_audit_keeps_claim_ceiling_below_unsupervised_safety() -> None:
    parent_dir = _find_parent_dir()
    manifest = _load_json(parent_dir / "artifacts" / "campaigns" / "campaign_manifest.json")
    audit = " ".join(
        (parent_dir / "artifacts" / "verification" / "phase_b_execution_family_closeout_audit.md")
        .read_text(encoding="utf-8")
        .split()
    )

    assert manifest["claim_ceiling"] == "bounded_phase_b_execution_and_repair_loop_only"
    assert "does not prove long unattended robustness" in audit
