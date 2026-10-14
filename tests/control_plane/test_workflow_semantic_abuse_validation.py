from __future__ import annotations

import json
from pathlib import Path

from tools.bin import workflow_semantic_abuse_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_workflow_semantic_abuse_validation_writes_required_artifacts(tmp_path: Path) -> None:
    result = validation.run_validation(out_dir=tmp_path)

    assert result["matrix"]["scenario_count"] == len(validation.SCENARIO_IDS)
    assert (tmp_path / "full_workflow_abuse_matrix.json").exists()
    assert (tmp_path / "full_workflow_negative_controls.json").exists()
    assert (tmp_path / "full_workflow_truth_trace.jsonl").exists()
    assert (tmp_path / "full_workflow_snapshots.json").exists()
    assert (tmp_path / "full_workflow_metrics_summary.json").exists()


def test_workflow_semantic_abuse_validation_catches_foreign_and_stale_artifact_reuse(tmp_path: Path) -> None:
    validation.run_validation(out_dir=tmp_path)

    rows = {row["scenario_id"]: row for row in _load_json(tmp_path / "full_workflow_abuse_matrix.json")["rows"]}
    assert rows["WF_WRONG_NODE_PROMOTION_V1"]["observed_classification"] == "intent_validation_family_parent_mismatch"
    assert rows["WF_STALE_VERIFIER_REUSE_V1"]["observed_classification"] == "intent_validation_compilation_mismatch"
    assert rows["WF_DETACHED_RESULT_REPLAY_V1"]["observed_classification"] == "implementation_proof_compilation_mismatch"
    assert rows["WF_POISONED_WRITEBACK_V1"]["observed_classification"] == "implementation_proof_family_parent_mismatch"

    negatives = _load_json(tmp_path / "full_workflow_negative_controls.json")
    assert all(bool(row["pass"]) for row in negatives["rows"])

