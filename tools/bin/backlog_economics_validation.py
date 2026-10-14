#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui import server_lab_assistant_web_operations as ops


SCHEMA_VERSION = "backlog_economics_validation_v1"
SCENARIO_IDS = (
    "BACKLOG_PARTIAL_FAILURE_REMAINING_ITEMS_DEGRADED_V1",
    "BACKLOG_SILENT_PARALYSIS_WITH_FAILED_STAGE_V1",
    "BACKLOG_HEALTHY_NEGATIVE_CONTROL_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _triage(queue_status: Dict[str, Any]) -> Dict[str, Any]:
    return ops._triage_projection(
        operator_snapshot={
            "operator_summary": {"truth_conflicts": []},
            "delegation_proof": {"summary": {"overall_status": "ok"}, "freshness": {"stale": False, "reason": ""}},
            "task_register": {"snapshot_projection": {"stale": False}},
            "execution_surface": {"state": "fresh", "tasks": []},
            "status_contract": {"contract_state": "fresh", "fields": []},
            "active_tasks": [],
            "completed_tasks": [],
        },
        queue_status=queue_status,
        watchdog_projection={"latest": {}},
        disagreements=[],
        current_workflow_id="",
        workflow_detail={},
        replay_entry_points=[{"summary": "queue", "api_path": "/api/mobile/backlog/execution_queue"}],
    )


def run_validation(out_dir: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []

    triage = _triage(
        {
            "state": {"active": False, "status": "failed", "status_detail": "failed", "last_failed_stage": "workflow"},
            "batch_report": {"outcome": "failed_partial", "remaining_count": 3, "failed_count": 1, "completed_count": 2},
        }
    )
    trace.append({"scenario_id": SCENARIO_IDS[0], "triage": triage})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[0],
            "pass": triage["state"] == "degraded"
            and any(row["code"] == "execution_queue_remaining_backlog_after_partial_failure" for row in triage["gaps"]),
            "expected_classification": "partial_failure_backlog_is_explicitly_degraded",
            "observed_classification": "partial_failure_backlog_is_explicitly_degraded"
            if triage["state"] == "degraded"
            and any(row["code"] == "execution_queue_remaining_backlog_after_partial_failure" for row in triage["gaps"])
            else "partial_failure_backlog_looked_quiet",
        }
    )

    triage = _triage(
        {
            "state": {"active": False, "status": "idle", "status_detail": "idle", "last_failed_stage": "reconcile"},
            "batch_report": {"outcome": "idle", "remaining_count": 2, "failed_count": 0, "completed_count": 1},
        }
    )
    trace.append({"scenario_id": SCENARIO_IDS[1], "triage": triage})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[1],
            "pass": triage["state"] == "degraded"
            and any(row["code"] == "execution_queue_silent_paralysis_with_remaining_backlog" for row in triage["gaps"]),
            "expected_classification": "silent_paralysis_with_backlog_stays_explicit",
            "observed_classification": "silent_paralysis_with_backlog_stays_explicit"
            if triage["state"] == "degraded"
            and any(row["code"] == "execution_queue_silent_paralysis_with_remaining_backlog" for row in triage["gaps"])
            else "silent_paralysis_looked_idle",
        }
    )

    triage = _triage(
        {
            "state": {"active": True, "status": "running", "status_detail": "workflow_running", "last_failed_stage": ""},
            "batch_report": {"outcome": "running", "remaining_count": 1, "failed_count": 0, "completed_count": 0},
        }
    )
    trace.append({"scenario_id": SCENARIO_IDS[2], "triage": triage})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[2],
            "pass": triage["state"] == "clear",
            "expected_classification": "healthy_negative_control_remains_clear",
            "observed_classification": "healthy_negative_control_remains_clear" if triage["state"] == "clear" else "healthy_negative_control_false_positive",
        }
    )

    matrix = {"schema_version": SCHEMA_VERSION, "scenario_count": len(rows), "pass_count": sum(1 for row in rows if row["pass"]), "rows": rows}
    fatigue = {
        "schema_version": "alert_fatigue_summary_v1",
        "false_calm_risk_surfaces": 0,
        "slow_wrongness_surfaces": 2,
        "economics_note": "Cheap/high-yield detection should catch partial failure and silent backlog paralysis before longer live campaigns.",
    }
    _write_json(out_dir / "backlog_economics_matrix.json", matrix)
    _write_jsonl(out_dir / "residue_drift_trace.jsonl", trace)
    _write_json(out_dir / "alert_fatigue_summary.json", fatigue)
    return {"matrix": matrix, "fatigue": fatigue}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run_validation(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
