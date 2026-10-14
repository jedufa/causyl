from __future__ import annotations

import json
from pathlib import Path

from tools.bin import unsupervised_autonomy_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_unsupervised_autonomy_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(out_dir=tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "unsupervised_autonomy_matrix.json").exists()
    assert (tmp_path / "delayed_operator_action_trace.jsonl").exists()
    assert (tmp_path / "autonomy_negative_controls.json").exists()


def test_unsupervised_autonomy_validation_rejects_stale_actions_and_blocks_hidden_delete(tmp_path: Path) -> None:
    validation.run_validation(out_dir=tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "unsupervised_autonomy_matrix.json")["rows"]}
    assert rows["STALE_TOGGLE_CONTRADICTS_LIVE_TRUTH_V1"]["observed_classification"] == "stale_toggle_rejected"
    assert rows["DEGRADED_RUN_NOW_REQUIRES_STALE_ACK_V1"]["observed_classification"] == "degraded_run_now_rejected"
    assert rows["STALE_OVERRIDE_FORBIDDEN_FOR_TRIGGER_V1"]["observed_classification"] == "stale_override_forbidden_for_trigger"
    assert rows["EXPLICIT_STALE_OVERRIDE_CANCEL_AUDITED_V1"]["observed_classification"] == "explicit_stale_override_is_supervised_only"
    assert rows["STALE_OVERRIDE_CANCEL_REQUIRES_EVIDENCE_V1"]["observed_classification"] == "stale_override_requires_evidence"
    assert rows["REPEATED_STALE_TRIGGER_STORM_V1"]["observed_classification"] == "repeated_stale_trigger_storm_blocked"
    assert rows["HIDDEN_ACTIVE_DELETE_BLOCKED_V1"]["observed_classification"] == "hidden_active_delete_blocked"
    assert rows["FRESH_MUTATION_NEGATIVE_CONTROL_V1"]["observed_classification"] == "fresh_mutation_remains_usable"

    negatives = _load_json(tmp_path / "autonomy_negative_controls.json")
    assert negatives["pass"] is True
