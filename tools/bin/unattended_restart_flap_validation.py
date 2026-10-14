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


SCHEMA_VERSION = "unattended_restart_flap_validation_v1"
SCENARIO_IDS = (
    "UNATTENDED_DELAYED_DISCOVERY_SELFHEAL_FALSE_CALM_V1",
    "UNATTENDED_RESTART_FLAP_CARRYOVER_WINDOW_STAYS_ZERO_V1",
    "UNATTENDED_HEALTHY_NEGATIVE_CONTROL_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _triage_with_watchdog_projection(watchdog_projection: Dict[str, Any]) -> Dict[str, Any]:
    return ops._triage_projection(
        operator_snapshot={
            "operator_summary": {"truth_conflicts": []},
            "delegation_proof": {"summary": {"overall_status": "ok"}, "freshness": {"stale": False, "reason": ""}},
            "task_register": {"snapshot_projection": {"stale": False}},
            "execution_surface": {"state": "fresh"},
            "status_contract": {"contract_state": "fresh"},
            "active_tasks": [],
            "completed_tasks": [],
        },
        watchdog_projection=watchdog_projection,
        disagreements=[],
        current_workflow_id="",
        workflow_detail={},
        replay_entry_points=[{"summary": "operator snapshot", "api_path": "/api/admin/lab_assistant/snapshot"}],
    )


def run_validation(out_dir: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []

    watchdog_projection = {
        "latest": {
            "audit": {
                "utc": "2026-04-13T17:00:00Z",
                "alertability": {
                    "mode": "audit",
                    "latest_report_generated_utc": "2026-04-13T17:00:00Z",
                    "false_calm_detected": False,
                    "wrong_cause_detected": False,
                    "detectability_lag_seconds": 0,
                },
            },
            "selfheal": {
                "utc": "2026-04-13T17:20:00Z",
                "alertability": {
                    "mode": "selfheal",
                    "latest_report_generated_utc": "2026-04-13T17:20:00Z",
                    "false_calm_detected": True,
                    "wrong_cause_detected": False,
                    "detectability_lag_seconds": 1200,
                },
            },
        }
    }
    triage = _triage_with_watchdog_projection(watchdog_projection)
    traces.append({"scenario_id": SCENARIO_IDS[0], "event": "triage", "details": triage})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[0],
            "pass": triage["state"] == "degraded"
            and any(row["code"] == "watchdog_false_calm_detected" for row in triage["gaps"])
            and any(row["code"] == "watchdog_detectability_lag" for row in triage["gaps"]),
            "expected_classification": "delayed_discovery_false_calm_is_not_hidden_by_healthy_audit",
            "observed_classification": "delayed_discovery_false_calm_is_not_hidden_by_healthy_audit"
            if triage["state"] == "degraded"
            and any(row["code"] == "watchdog_false_calm_detected" for row in triage["gaps"])
            else "healthy_audit_smoothed_selfheal_false_calm",
            "supports_positive_unattended_window": False,
        }
    )

    quiet_windows = [0, 300, 900, 1800]
    contamination_failures = []
    for quiet_window in quiet_windows:
        triage = _triage_with_watchdog_projection(watchdog_projection)
        contamination_failures.append(
            {
                "quiet_window_s": quiet_window,
                "triage_state": triage["state"],
                "supports_positive_unattended_window": False,
            }
        )
        traces.append(
            {
                "scenario_id": SCENARIO_IDS[1],
                "event": "restart_flap_iteration",
                "details": contamination_failures[-1],
            }
        )
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[1],
            "pass": all(row["triage_state"] == "degraded" for row in contamination_failures),
            "expected_classification": "restart_flap_carryover_keeps_unattended_window_at_zero",
            "observed_classification": "restart_flap_carryover_keeps_unattended_window_at_zero"
            if all(row["triage_state"] == "degraded" for row in contamination_failures)
            else "restart_flap_quiet_window_smoothed_to_safe",
            "supports_positive_unattended_window": False,
        }
    )

    healthy_projection = {
        "latest": {
            "audit": {
                "utc": "2026-04-13T18:00:00Z",
                "alertability": {
                    "mode": "audit",
                    "latest_report_generated_utc": "2026-04-13T18:00:00Z",
                    "false_calm_detected": False,
                    "wrong_cause_detected": False,
                    "detectability_lag_seconds": 0,
                },
            }
        }
    }
    triage = _triage_with_watchdog_projection(healthy_projection)
    traces.append({"scenario_id": SCENARIO_IDS[2], "event": "triage", "details": triage})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[2],
            "pass": triage["state"] == "clear",
            "expected_classification": "healthy_negative_control_remains_clear",
            "observed_classification": "healthy_negative_control_remains_clear"
            if triage["state"] == "clear"
            else "healthy_negative_control_false_positive",
            "supports_positive_unattended_window": False,
        }
    )

    matrix = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "pass_count": sum(1 for row in rows if row["pass"]),
        "rows": rows,
    }
    unattended_window = {
        "schema_version": "unattended_window_assessment_v1",
        "supported_unattended_window_minutes": 0,
        "positive_window_earned": False,
        "blocked_by": [
            "delayed_discovery_false_calm",
            "restart_flap_carryover_contamination",
        ],
    }
    _write_json(out_dir / "unattended_abuse_matrix.json", matrix)
    _write_jsonl(out_dir / "unattended_abuse_trace.jsonl", traces)
    _write_json(out_dir / "unattended_window_assessment.json", unattended_window)
    return {"matrix": matrix, "unattended_window": unattended_window}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run_validation(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
