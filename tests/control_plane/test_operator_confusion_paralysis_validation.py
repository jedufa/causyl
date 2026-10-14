from __future__ import annotations

import json
from pathlib import Path

from tools.bin import operator_confusion_paralysis_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_operator_confusion_paralysis_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "operator_confusion_matrix.json").exists()
    assert (tmp_path / "silent_paralysis_trace.jsonl").exists()
    assert (tmp_path / "mixed_stale_surface_catalog.json").exists()


def test_operator_confusion_paralysis_validation_keeps_mixed_stale_surfaces_explicit(tmp_path: Path) -> None:
    validation.run_validation(tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "operator_confusion_matrix.json")["rows"]}

    assert rows["OPERATOR_CONFUSION_MIXED_RUN_AND_OWNERSHIP_SURFACES_V1"]["observed_classification"] == "mixed_run_and_ownership_surfaces_stay_degraded"
    assert rows["OPERATOR_SILENT_PARALYSIS_FROM_STALE_QUEUE_AND_SURFACE_DRIFT_V1"]["observed_classification"] == "silent_paralysis_path_remains_explicit"
    assert rows["OPERATOR_HEALTHY_NEGATIVE_CONTROL_V1"]["observed_classification"] == "healthy_negative_control_remains_fresh"
