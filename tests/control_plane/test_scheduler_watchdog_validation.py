from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_watchdog_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_watchdog_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(out_dir=tmp_path)

    assert len(result["matrix"]) == len(validation.SCENARIO_IDS)
    assert (tmp_path / "watchdog_decision_matrix.json").exists()
    assert (tmp_path / "watchdog_metrics_summary.json").exists()
    assert (tmp_path / "watchdog_negative_controls.json").exists()
    assert (tmp_path / "watchdog_truth_trace.jsonl").exists()
    assert (tmp_path / "watchdog_truth_snapshots.json").exists()


def test_watchdog_validation_captures_false_positive_seed_and_bounded_recovery(tmp_path: Path) -> None:
    validation.run_validation(out_dir=tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "watchdog_decision_matrix.json")["rows"]}
    assert rows["WATCHDOG_FALSE_POSITIVE_SEED_V1"]["correctness_verdict"] == "pass"
    assert rows["WATCHDOG_FALSE_POSITIVE_SEED_V1"]["forbidden_action"] == "resume_ready_head_while_active_exists"
    assert rows["WATCHDOG_MULTIPLE_READY_HEADS_NEGATIVE_CONTROL_V1"]["correctness_verdict"] == "pass"
    assert rows["WATCHDOG_MULTIPLE_READY_HEADS_NEGATIVE_CONTROL_V1"]["forbidden_action"] == "resume_one_ready_head_from_ambiguous_surface"
    assert rows["WATCHDOG_BOUNDED_PROGRESS_RECOVERY_V1"]["correctness_verdict"] == "pass"

    metrics = _load_json(tmp_path / "watchdog_metrics_summary.json")
    assert metrics["false_positive_count"] == 0
    assert metrics["forbidden_action_count"] == 0
    assert metrics["bugs_found_and_fixed"][0]["issue"] == "active_ready_surface_false_negative"


def test_verified_a818_artifacts_stay_in_sync() -> None:
    base = Path("governance/ledger/patch_instruction_sheets/causyl/verified/2026/04")
    root = next(base.glob("PIS-20260413-061525Z-A818__*/"))

    meta = _load_json(root / "meta.json")
    metrics = _load_json(root / "artifacts" / "validation" / "watchdog_metrics_summary.json")
    verification = _load_json(root / "artifacts" / "verification" / "status_reconcile_verification.json")
    negatives = _load_json(root / "artifacts" / "validation" / "watchdog_negative_controls.json")
    matrix = _load_json(root / "artifacts" / "validation" / "watchdog_decision_matrix.json")

    assert meta["status"] == "verified"
    assert meta["release_state"] == "released"
    assert verification["observed_metrics"]["scenario_count"] == metrics["scenario_count"] == len(matrix["rows"])
    assert verification["observed_metrics"]["negative_controls_passed"] == len(negatives["rows"])
    assert verification["bugs_found_and_fixed"] == metrics["bugs_found_and_fixed"]
    assert all("/active/" not in command for command in verification["validation_commands"])
