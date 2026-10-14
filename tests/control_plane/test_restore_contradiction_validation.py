from __future__ import annotations

import json
from pathlib import Path

from tools.bin import restore_contradiction_validation as validation


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_restore_contradiction_validation_writes_required_artifacts_and_finds_restore_truth_bugs(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"

    result = validation.run_validation(out_dir=out_dir)

    assert result["ok"] is True
    assert result["scenario_count"] == 4
    assert result["fail_count"] == 0
    assert result["bugs_found_and_fixed"] == [
        "restore_projection_admitted_stranded_running_without_live_claim",
        "restore_reconciliation_ignored_stranded_running_without_live_claim",
        "restored_host_truth_snapshot_ignored_ambiguous_scheduler_projection",
        "watchdog_missed_terminal_receipt_conflict_without_active_run",
    ]

    matrix = _load_json(out_dir / "restore_contradiction_matrix.json")
    summary = _load_json(out_dir / "restore_contradiction_metrics_summary.json")
    reused = _load_json(out_dir / "restore_reused_evidence_manifest.json")
    negative_controls = _load_json(out_dir / "restore_negative_controls.json")
    traces = (out_dir / "restore_under_load_trace.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert matrix["scenario_count"] == 4
    assert matrix["pass_count"] == 4
    assert summary["pass_count"] == 4
    assert reused["rows"] == [
        {
            "scenario_id": "RESTORE_FROM_PARTIAL_WRITE_SICK_HOST_V1",
            "reused_evidence_from": ["PIS-20260413-110629Z-E5Q8"],
        }
    ]
    assert all(row["pass"] is True for row in negative_controls["controls"])
    assert len(traces) == 4


def test_restore_contradiction_validation_requires_pre_reconcile_non_admission_and_post_reconcile_repair(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"
    validation.run_validation(out_dir=out_dir)

    matrix = _load_json(out_dir / "restore_contradiction_matrix.json")
    rows = {row["scenario_id"]: row for row in matrix["scenarios"]}

    assert rows["RESTORE_TERMINAL_RECEIPT_STRANDED_RUNNING_V1"]["pre_projection_status"] == "ambiguous"
    assert rows["RESTORE_TERMINAL_RECEIPT_STRANDED_RUNNING_V1"]["executed_count_before_reconcile"] == 0
    assert rows["RESTORE_TERMINAL_RECEIPT_STRANDED_RUNNING_V1"]["post_projection_status"] == "completed_once"
    assert rows["RESTORE_STRANDED_RUNNING_WITHOUT_RECEIPT_V1"]["post_projection_status"] == "blocked"
    assert rows["RESTORE_TERMINAL_RECEIPT_WATCHDOG_CONFLICT_V1"]["finding_count"] == 1
