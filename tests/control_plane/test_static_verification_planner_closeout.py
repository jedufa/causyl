from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane.static_verification.static_verification_planner_closeout import PlannerCloseoutError, build_phase_a_closeout


REPO_ROOT = Path(__file__).resolve().parents[2]
S6V7_UID = "PIS-20260413-160259Z-S6V7"


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


def test_s6v7_meta_points_to_closeout_artifacts() -> None:
    meta = _load_json(_find_dir(S6V7_UID) / "meta.json")
    assert meta["required_evidence"] == [
        "artifacts/closeout/verification_planner_assessment.json",
        "artifacts/closeout/planner_delta_examples.json",
    ]


def test_s6v7_closeout_artifacts_keep_claim_ceiling_below_runtime_proof() -> None:
    base = _find_dir(S6V7_UID) / "artifacts" / "closeout"
    assessment = _load_json(base / "verification_planner_assessment.json")
    deltas = _load_json(base / "planner_delta_examples.json")
    assert assessment["claim_ceiling"] == "planning_quality_improved_only"
    assert "No claim of scheduler execution proof." in assessment["non_claims"]
    assert len(deltas["before_after_examples"]) >= 2


def test_s6v7_closeout_builder_rejects_missing_baseline_or_ambiguous_rejection_evidence() -> None:
    payload = _load_json(_find_dir(S6V7_UID) / "artifacts" / "closeout" / "verification_planner_assessment.json")
    built = build_phase_a_closeout(payload)
    assert built["closeout_decision"] == "closable"
    bad = dict(payload)
    bad["soft_baseline_bakeoff"] = {}
    with pytest.raises(PlannerCloseoutError) as excinfo:
        build_phase_a_closeout(bad)
    assert excinfo.value.code == "soft_baseline_bakeoff_missing"
