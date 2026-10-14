from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_host_continuity_validation as validation


def test_scheduler_host_continuity_validation_writes_required_artifacts_and_detects_replacement_host_gap(
    tmp_path: Path,
) -> None:
    out_dir = tmp_path / "artifacts"

    result = validation.run_host_continuity_validation(out_dir=out_dir)

    assert result["ok"] is True
    assert result["scenario_count"] == 4
    assert result["fail_count"] == 0
    assert result["bugs_found_and_fixed"] == [
        "replacement_host_drill_ignored_scheduler_incoherence_before_reconciliation",
        "replacement_host_coherence_ignored_unreadable_task_register",
    ]

    metrics = json.loads((out_dir / "host_continuity_metrics.json").read_text(encoding="utf-8"))
    replacement = json.loads((out_dir / "replacement_host_reconciliation.json").read_text(encoding="utf-8"))
    checklist = json.loads((out_dir / "operational_posture_checklist.json").read_text(encoding="utf-8"))
    snapshots = json.loads((out_dir / "host_truth_snapshots.json").read_text(encoding="utf-8"))
    trace_lines = (out_dir / "host_truth_trace.jsonl").read_text(encoding="utf-8").strip().splitlines()

    assert metrics["replacement_host_reconciliation_required"] is True
    assert replacement["pre_reconcile_scheduler_truth"]["coherent"] is False
    assert replacement["post_reconcile_scheduler_truth"]["coherent"] is True
    assert replacement["scheduler_reconciliation"]["claim_audit"]["cleared_count"] == 1
    assert any(
        row["key"] == "replacement_host_reconciliation_detects_inherited_live_scheduler_truth"
        and row["status"] == "pass"
        for row in checklist["checks"]
    )
    assert any(
        row["key"] == "unreadable_task_register_blocks_coherent_truth_claim"
        and row["status"] == "pass"
        for row in checklist["checks"]
    )
    assert "HOST_CONTINUITY_INHERITED_LIVE_LEASE_V1" in snapshots["scenarios"]
    assert "HOST_CONTINUITY_UNREADABLE_TASK_REGISTER_V1" in snapshots["scenarios"]
    assert replacement["unreadable_task_register_truth"]["coherent"] is False
    assert "task_register_snapshot_unreadable" in replacement["unreadable_task_register_truth"]["watchdog_finding_kinds"]
    assert len(trace_lines) == 4
