from __future__ import annotations

import json
from pathlib import Path

from tools.bin import unattended_restart_flap_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_unattended_restart_flap_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "unattended_abuse_matrix.json").exists()
    assert (tmp_path / "unattended_abuse_trace.jsonl").exists()
    assert (tmp_path / "unattended_window_assessment.json").exists()


def test_unattended_restart_flap_validation_blocks_positive_unattended_window_when_false_calm_survives(tmp_path: Path) -> None:
    validation.run_validation(tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "unattended_abuse_matrix.json")["rows"]}
    window = _load_json(tmp_path / "unattended_window_assessment.json")

    assert rows["UNATTENDED_DELAYED_DISCOVERY_SELFHEAL_FALSE_CALM_V1"]["observed_classification"] == "delayed_discovery_false_calm_is_not_hidden_by_healthy_audit"
    assert rows["UNATTENDED_RESTART_FLAP_CARRYOVER_WINDOW_STAYS_ZERO_V1"]["observed_classification"] == "restart_flap_carryover_keeps_unattended_window_at_zero"
    assert rows["UNATTENDED_HEALTHY_NEGATIVE_CONTROL_V1"]["observed_classification"] == "healthy_negative_control_remains_clear"
    assert window["supported_unattended_window_minutes"] == 0
    assert window["positive_window_earned"] is False
