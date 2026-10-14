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

from infra.bin import scheduler_live_watch as watch


SCHEMA_VERSION = "cross_time_surface_incoherence_validation_v1"
SCENARIO_IDS = (
    "CROSS_TIME_V2_ADVISORY_OVERRIDES_FRESH_TOP_LEVEL_V1",
    "CROSS_SURFACE_PLAUSIBLE_IMPOSSIBLE_STORY_STAYS_DEGRADED_V1",
    "CROSS_TIME_FRESH_NEGATIVE_CONTROL_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def run_validation(out_dir: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    traces: List[Dict[str, Any]] = []

    advisory_payload = {
        "jobs": [],
        "summary": "",
        "observation_integrity": {
            "status": "fresh",
            "advisory_only": False,
            "reasons": [],
            "summary": "",
            "last_error": "",
        },
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "process": {"public_ports": []},
            "observation_advisory": {
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable", "cross_time_skew"],
                "summary": "scheduler_v2 bundle is fresher than operator job rows",
                "last_error": "simulated_bundle_skew",
            },
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260413-LAST-PA01",
                        "family_status": "active",
                        "current_stage": "implement",
                    }
                }
            },
        },
    }
    observation = watch._observation_integrity(advisory_payload)
    rendered = watch._render("http://localhost:8787", advisory_payload, tz_name="UTC", color=False)
    traces.append({"scenario_id": SCENARIO_IDS[0], "event": "render", "details": {"observation": observation}})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[0],
            "pass": observation["status"] == "degraded"
            and "cross_time_skew" in observation["reasons"]
            and "v2_family:" not in rendered,
            "expected_classification": "bundle_level_advisory_overrides_local_freshness",
            "observed_classification": "bundle_level_advisory_overrides_local_freshness"
            if observation["status"] == "degraded" and "v2_family:" not in rendered
            else "fresh_top_level_smoothed_bundle_advisory",
            "operator_false_story": "fresh top-level snapshot makes active family progress look safe",
        }
    )

    plausible_payload = {
        "jobs": [
            {
                "job_id": "child_prev_ok",
                "display_title": "Previous child",
                "enabled": True,
                "state": {"last_status": "ok"},
                "due": {"reason": "already_executed"},
                "scheduler_projection_v1": {
                    "projected_status": "completed_once",
                    "projection_reason": "terminal_success",
                    "durable_conflict": False,
                },
            }
        ],
        "observation_integrity": {
            "status": "fresh",
            "advisory_only": False,
            "reasons": [],
            "summary": "",
            "last_error": "",
        },
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "process": {"public_ports": []},
            "observation_advisory": {
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["stale_family_progress", "cross_surface_story_impossible"],
                "summary": "previous completed child and active family projection cannot both be trusted",
                "last_error": "",
            },
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260413-LAST-PA01",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {
                            "completed_children": 0,
                            "total_children": 6,
                            "active_children": 1,
                            "blocked_children": 0,
                            "failed_children": 0,
                        },
                    },
                    "admitted": [
                        {
                            "child_uid": "PIS-20260413-LAST-AA01",
                            "job_id": "active_child",
                            "decision": "active",
                            "reason": "active_run_present",
                            "gate": "continue",
                            "unmet_dependencies": [],
                        }
                    ],
                }
            },
        },
    }
    observation = watch._observation_integrity(plausible_payload)
    rendered = watch._render("http://localhost:8787", plausible_payload, tz_name="UTC", color=False)
    traces.append({"scenario_id": SCENARIO_IDS[1], "event": "render", "details": {"observation": observation}})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[1],
            "pass": observation["status"] == "degraded"
            and "cross_surface_story_impossible" in observation["reasons"]
            and "v2_family:" not in rendered,
            "expected_classification": "plausible_impossible_story_stays_explicitly_degraded",
            "observed_classification": "plausible_impossible_story_stays_explicitly_degraded"
            if observation["status"] == "degraded" and "v2_family:" not in rendered
            else "plausible_impossible_story_smoothed_over",
            "operator_false_story": "the plane looks like it both completed the previous child and is safely progressing the same family now",
        }
    )

    healthy_payload = {
        "jobs": [],
        "observation_integrity": {"status": "fresh", "advisory_only": False, "reasons": [], "summary": "", "last_error": ""},
        "scheduler_v2": {},
    }
    observation = watch._observation_integrity(healthy_payload)
    traces.append({"scenario_id": SCENARIO_IDS[2], "event": "observation", "details": observation})
    rows.append(
        {
            "scenario_id": SCENARIO_IDS[2],
            "pass": observation["status"] == "fresh",
            "expected_classification": "fresh_negative_control_remains_usable",
            "observed_classification": "fresh_negative_control_remains_usable"
            if observation["status"] == "fresh"
            else "fresh_negative_control_false_positive",
            "operator_false_story": "",
        }
    )

    matrix = {"schema_version": SCHEMA_VERSION, "scenario_count": len(rows), "pass_count": sum(1 for row in rows if row["pass"]), "rows": rows}
    catalog = {
        "schema_version": "operator_false_story_catalog_v1",
        "rows": [
            {
                "scenario_id": row["scenario_id"],
                "operator_false_story": row["operator_false_story"],
            }
            for row in rows
            if row["operator_false_story"]
        ],
    }
    _write_json(out_dir / "cross_time_surface_matrix.json", matrix)
    _write_jsonl(out_dir / "plausible_impossible_trace.jsonl", traces)
    _write_json(out_dir / "operator_false_story_catalog.json", catalog)
    return {"matrix": matrix, "catalog": catalog}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run_validation(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
