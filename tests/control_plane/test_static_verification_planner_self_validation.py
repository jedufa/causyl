from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane.static_verification.static_verification_planner_self_validation import run_seeded_validation


REPO_ROOT = Path(__file__).resolve().parents[2]
X2A3_UID = "PIS-20260413-161609Z-X2A3"


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


def test_x2a3_meta_points_to_seeded_bad_input_manifest() -> None:
    meta = _load_json(_find_dir(X2A3_UID) / "meta.json")
    assert meta["required_evidence"] == ["artifacts/planning/planner_seeded_bad_input_manifest_v1.json"]


def test_x2a3_seeded_manifest_drives_real_bad_case_detection() -> None:
    manifest = _load_json(_find_dir(X2A3_UID) / "artifacts" / "planning" / "planner_seeded_bad_input_manifest_v1.json")
    report = run_seeded_validation(manifest)
    assert report["all_cases_passed"] is True
    assert report["negative_controls_caught"] == 4
    observed = {item["case_id"]: item["observed"] for item in report["results"]}
    assert observed["ambiguous-boundary-reject"] == "boundary_still_ambiguous_after_qualification"
    assert observed["stateful-threat-without-repetition"] == "missing_repetition_overlap_accumulation_plan"
    assert observed["generation-without-scenarios"] == "scenario_examples_missing"
    assert observed["soft-baseline-plan"] == "sounds_harsh_only"
