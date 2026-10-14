from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_fault_injection_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_fault_injection_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(out_dir=tmp_path)

    assert result["fault_matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "fault_matrix.json").exists()
    assert (tmp_path / "contradiction_matrix.json").exists()
    assert (tmp_path / "degraded_surface_observation.json").exists()
    assert (tmp_path / "fault_negative_controls.json").exists()
    assert (tmp_path / "fault_truth_trace.jsonl").exists()
    assert (tmp_path / "fault_truth_snapshots.json").exists()

    fault_matrix = _load_json(tmp_path / "fault_matrix.json")
    rows = {row["scenario_id"]: row for row in fault_matrix["rows"]}
    assert rows["FAULT_RECEIPT_TERMINAL_STATE_ACTIVE_V1"]["observed_classification"] == "terminal_receipt_state_conflict"
    assert rows["FAULT_STATE_CLEARED_LEASE_PRESENT_V1"]["observed_classification"] == "stale_lock"
    assert rows["FAULT_CLOCK_JUMP_V1"]["observed_classification"] == "stale_lock:future_probe_utc"


def test_fault_injection_validation_marks_degraded_surfaces_honestly(tmp_path: Path) -> None:
    validation.run_validation(out_dir=tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "degraded_surface_observation.json")["rows"]}
    assert rows["SURFACE_TIMEOUT_DURING_ACTIVE_PROGRESS_V1"]["observed_classification"] == "jobs_unavailable_snapshot_stale"
    assert rows["SURFACE_STALE_SUMMARY_CACHE_V1"]["observed_classification"] == "task_register_stale_projection"
    negative_controls = _load_json(tmp_path / "fault_negative_controls.json")
    assert all(bool(row["pass"]) for row in negative_controls["rows"])
