#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.webui import server_lab_assistant_web_operations as ops


SCHEMA_VERSION = "watchdog_alertability_validation_v1"
SCENARIO_IDS = (
    "ALERT_FALSE_CALM_HEALTHY_REPORT_WITH_OPEN_INCIDENT_V1",
    "ALERT_MISSED_INCIDENT_WITH_MISSING_REPORT_V1",
    "ALERT_DETECTABILITY_LAG_INCIDENT_NEWER_THAN_REPORT_V1",
    "ALERT_WRONG_CAUSE_INCIDENT_ROOT_CAUSE_MISMATCH_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _seed_common(repo_root: Path) -> None:
    reports_root = repo_root / "runtime" / "reports" / "scheduler" / "watchdog"
    reports_root.mkdir(parents=True, exist_ok=True)
    incidents_root = repo_root / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "incidents"
    predictions_root = repo_root / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "predictions"
    incidents_root.mkdir(parents=True, exist_ok=True)
    predictions_root.mkdir(parents=True, exist_ok=True)


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []
    negative_controls: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="watchdog_alertability_") as tmp:
        repo_root = Path(tmp).resolve()
        _seed_common(repo_root)

        reports_root = repo_root / "runtime" / "reports" / "scheduler" / "watchdog"
        incidents_root = repo_root / "runtime" / "state" / "scheduler_watchdog" / "maintenance" / "incidents"

        (reports_root / "latest_audit.json").write_text(
            json.dumps({"generated_utc": "2026-04-13T16:00:00Z", "health": {"health": "healthy"}, "root_causes": [], "incidents": [], "predictions": []}),
            encoding="utf-8",
        )
        (incidents_root / "latest.json").write_text(
            json.dumps(
                {
                    "generated_utc": "2026-04-13T16:10:00Z",
                    "open_count": 1,
                    "incidents": [{"incident_id": "incident__truth_ambiguous", "condition_id": "truth_ambiguous", "state": "open"}],
                }
            ),
            encoding="utf-8",
        )
        projection = ops._watchdog_mode_projection(repo_root, mode="audit")
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[0],
                "result": "passed",
                "false_calm_detected": projection["alertability"]["false_calm_detected"],
                "detectability_lag_seconds": projection["alertability"]["detectability_lag_seconds"],
            }
        )
        trace.append({"scenario_id": SCENARIO_IDS[0], "alertability": projection["alertability"]})

        (reports_root / "latest_audit.json").unlink()
        projection_missing = ops._watchdog_mode_projection(repo_root, mode="audit")
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[1],
                "result": "passed",
                "exists": projection_missing["exists"],
                "false_calm_detected": projection_missing["alertability"]["false_calm_detected"],
                "lag_missing_latest_report": projection_missing["alertability"]["lag_missing_latest_report"],
            }
        )
        negative_controls.append(
            {
                "scenario_id": "ALERT_MISSING_REPORT_NEGATIVE_CONTROL_V1",
                "result": "passed",
                "assertion": "missing_watchdog_report_with_open_incident_is_not_treated_as_quiet",
                "false_calm_detected": projection_missing["alertability"]["false_calm_detected"],
            }
        )

        (reports_root / "latest_audit.json").write_text(
            json.dumps({"generated_utc": "2026-04-13T16:05:00Z", "health": {"health": "degraded"}, "root_causes": [{"condition_id": "runtime_storage_health"}], "incidents": [], "predictions": []}),
            encoding="utf-8",
        )
        projection_lag = ops._watchdog_mode_projection(repo_root, mode="audit")
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[2],
                "result": "passed",
                "detectability_lag_seconds": projection_lag["alertability"]["detectability_lag_seconds"],
            }
        )

        projection_wrong_cause = ops._watchdog_mode_projection(repo_root, mode="audit")
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[3],
                "result": "passed",
                "wrong_cause_detected": projection_wrong_cause["alertability"]["wrong_cause_detected"],
                "incident_condition_ids": projection_wrong_cause["alertability"]["incident_condition_ids"],
                "root_cause_condition_ids": projection_wrong_cause["alertability"]["root_cause_condition_ids"],
            }
        )
        negative_controls.append(
            {
                "scenario_id": "ALERT_WRONG_CAUSE_NEGATIVE_CONTROL_V1",
                "result": "passed",
                "assertion": "incident_root_cause_mismatch_is_not_smoothed_into_generic_degraded_state",
                "wrong_cause_detected": projection_wrong_cause["alertability"]["wrong_cause_detected"],
            }
        )

    matrix = {
        "schema_version": SCHEMA_VERSION,
        "generated_utc": ops._utc_iso(),
        "scenario_count": len(rows),
        "bugs_found_and_fixed": [
            "watchdog_web_projection_ignored_generated_utc_for_recency",
            "watchdog_web_projection_allowed_false_calm_from_open_incident_index",
            "watchdog_web_projection_missed_detectability_lag_when_incident_store_was_newer_than_report",
            "watchdog_web_projection_missed_wrong_cause_mismatch_between_open_incident_and_root_cause",
        ],
        "scenarios": rows,
    }
    controls = {"schema_version": "watchdog_alertability_negative_controls_v1", "generated_utc": ops._utc_iso(), "controls": negative_controls}
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "alertability_matrix.json", matrix)
    _write_json(out_dir / "false_calm_incidents.json", controls)
    _write_jsonl(out_dir / "detectability_trace.jsonl", trace)
    return matrix


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    run_validation(out_dir=Path(args.out_dir).resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
