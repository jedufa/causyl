from __future__ import annotations

import json
from pathlib import Path

from tools.bin import distributed_restore_ownership_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_distributed_restore_ownership_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "distributed_restore_matrix.json").exists()
    assert (tmp_path / "replay_conflict_trace.jsonl").exists()
    assert (tmp_path / "ownership_claim_assessment.json").exists()


def test_distributed_restore_ownership_validation_blocks_stronger_claim_on_orphan_and_conflict(tmp_path: Path) -> None:
    validation.run_validation(tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "distributed_restore_matrix.json")["rows"]}
    assessment = _load_json(tmp_path / "ownership_claim_assessment.json")

    assert rows["DISTRIBUTED_RESTORE_ORPHAN_REMOTE_BINDING_V1"]["observed_classification"] == "orphan_remote_binding_blocks_distributed_restore_claim"
    assert rows["DISTRIBUTED_RESTORE_OWNERSHIP_CONFLICT_V1"]["observed_classification"] == "ownership_conflict_blocks_distributed_restore_claim"
    assert rows["DISTRIBUTED_RESTORE_HEALTHY_NEGATIVE_CONTROL_V1"]["observed_classification"] == "healthy_negative_control_remains_coherent"
    assert assessment["stronger_restore_safety_claim_earned"] is False
