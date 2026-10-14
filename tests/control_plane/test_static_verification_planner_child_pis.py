from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_planner_child_pis import ChildPISContractError, validate_generated_child_family


REPO_ROOT = Path(__file__).resolve().parents[2]
Y3B4_UID = "PIS-20260413-161610Z-Y3B4"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _find_dir(uid: str) -> Path:
    base = REPO_ROOT / "governance" / "ledger" / "patch_instruction_sheets" / "causyl"
    for ledger_status in ("verified", "active"):
        matches = list((base / ledger_status / "2026" / "04").glob(f"{uid}__*/"))
        for match in matches:
            if (match / "meta.json").exists():
                return match
    raise AssertionError(f"missing ledger directory for {uid}")


def _sample_child_family() -> dict:
    return {
        "child_pis_family": [
            {
                "child_key": "restart-family",
                "dominant_failure_story": "Replay can smooth stale receipts into coherent truth.",
                "closure_blockers": ["negative control must pass", "disqualifying incident must fail closed"],
                "non_goals": ["does not prove runtime robustness"],
                "max_proof_target_breadth": 1,
                "depends_on_child_keys": [],
            },
            {
                "child_key": "false-calm-family",
                "dominant_failure_story": "Observation freshness can drift while the UI still looks healthy.",
                "closure_blockers": ["must compare operator surface to durable state"],
                "non_goals": ["does not prove DUT improvement"],
                "max_proof_target_breadth": 2,
                "depends_on_child_keys": ["restart-family"],
            },
        ]
    }


def test_y3b4_meta_points_to_generated_child_closure_contract() -> None:
    meta = _load_json(_find_dir(Y3B4_UID) / "meta.json")
    assert meta["required_evidence"] == ["artifacts/planning/generated_child_pis_closure_contract_v1.json"]


def test_y3b4_contract_requires_blockers_non_goals_and_bounded_breadth() -> None:
    contract = _load_json(_find_dir(Y3B4_UID) / "artifacts" / "planning" / "generated_child_pis_closure_contract_v1.json")
    assert contract["quality_rules"]["closure_blockers_required"] is True
    assert contract["quality_rules"]["max_proof_target_breadth_range"] == [1, 3]


def test_y3b4_validator_accepts_bounded_child_family_and_rejects_missing_blockers() -> None:
    report = validate_generated_child_family(_sample_child_family())
    assert report["maximum_supported_claim"] == "generated_child_pis_family_bounded_for_planning_only"
    bad = _sample_child_family()
    bad["child_pis_family"][0]["closure_blockers"] = []
    with pytest.raises(ChildPISContractError) as excinfo:
        validate_generated_child_family(bad)
    assert excinfo.value.code == "closure_blockers_missing"
