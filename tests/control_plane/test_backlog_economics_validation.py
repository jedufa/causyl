from __future__ import annotations

import json
from pathlib import Path

from tools.bin import backlog_economics_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_backlog_economics_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "backlog_economics_matrix.json").exists()
    assert (tmp_path / "residue_drift_trace.jsonl").exists()
    assert (tmp_path / "alert_fatigue_summary.json").exists()


def test_backlog_economics_validation_keeps_slow_wrongness_explicit(tmp_path: Path) -> None:
    validation.run_validation(tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "backlog_economics_matrix.json")["rows"]}

    assert rows["BACKLOG_PARTIAL_FAILURE_REMAINING_ITEMS_DEGRADED_V1"]["observed_classification"] == "partial_failure_backlog_is_explicitly_degraded"
    assert rows["BACKLOG_SILENT_PARALYSIS_WITH_FAILED_STAGE_V1"]["observed_classification"] == "silent_paralysis_with_backlog_stays_explicit"
    assert rows["BACKLOG_HEALTHY_NEGATIVE_CONTROL_V1"]["observed_classification"] == "healthy_negative_control_remains_clear"
