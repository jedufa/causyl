from __future__ import annotations

import json
from pathlib import Path

from tools.bin import cross_time_surface_incoherence_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_cross_time_surface_incoherence_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "cross_time_surface_matrix.json").exists()
    assert (tmp_path / "plausible_impossible_trace.jsonl").exists()
    assert (tmp_path / "operator_false_story_catalog.json").exists()


def test_cross_time_surface_incoherence_validation_keeps_plausible_impossible_stories_explicit(tmp_path: Path) -> None:
    validation.run_validation(tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "cross_time_surface_matrix.json")["rows"]}
    catalog = _load_json(tmp_path / "operator_false_story_catalog.json")

    assert rows["CROSS_TIME_V2_ADVISORY_OVERRIDES_FRESH_TOP_LEVEL_V1"]["observed_classification"] == "bundle_level_advisory_overrides_local_freshness"
    assert rows["CROSS_SURFACE_PLAUSIBLE_IMPOSSIBLE_STORY_STAYS_DEGRADED_V1"]["observed_classification"] == "plausible_impossible_story_stays_explicitly_degraded"
    assert rows["CROSS_TIME_FRESH_NEGATIVE_CONTROL_V1"]["observed_classification"] == "fresh_negative_control_remains_usable"
    assert len(catalog["rows"]) >= 2
