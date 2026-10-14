from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_host_sickness_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_scheduler_host_sickness_validation_writes_required_artifacts_and_finds_active_host_sickness_bugs(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "artifacts" / "validation"

    result = validation.run_validation(out_dir=out_dir)

    assert result["ok"] is True
    assert result["scenario_count"] == 5
    assert result["fail_count"] == 0
    assert result["bugs_found_and_fixed"] == [
        "active_busy_masked_runtime_storage_critical",
        "active_busy_masked_runtime_recovery_needed",
        "host_truth_snapshot_ignored_phantom_active_pressure",
    ]

    matrix = _load_json(out_dir / "host_sickness_matrix.json")
    residuals = _load_json(out_dir / "host_continuity_residuals.json")
    trace_lines = (out_dir / "host_truth_trace.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert matrix["scenario_count"] == 5
    assert matrix["pass_count"] == 5
    assert residuals["residual_claims"]
    assert len(trace_lines) == 5


def test_scheduler_host_sickness_validation_marks_coupled_restore_evidence_as_reused(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"
    validation.run_validation(out_dir=out_dir)

    matrix = _load_json(out_dir / "host_sickness_matrix.json")
    rows = {row["scenario_id"]: row for row in matrix["scenarios"]}

    assert rows["HOST_SICKNESS_ACTIVE_RUNTIME_STORAGE_CRITICAL_V1"]["observed_health_reason"] == "runtime_storage_health"
    assert rows["HOST_SICKNESS_ACTIVE_STALE_LOCK_RECOVERY_V1"]["observed_health_reason"] == "runtime_recovery_needed"
    assert rows["HOST_SICKNESS_PHANTOM_ACTIVE_RUN_PRESSURE_V1"]["coherent"] is False
    assert rows["HOST_SICKNESS_CLOCK_JUMP_ACTIVE_CLAIM_V1"]["reused_evidence_for"] == ["PIS-20260413-111003Z-N4Y6"]
    assert rows["HOST_SICKNESS_PARTIAL_WRITE_PRESSURE_ACTIVE_TASK_REGISTER_V1"]["reused_evidence_for"] == [
        "PIS-20260413-111003Z-N4Y6"
    ]
