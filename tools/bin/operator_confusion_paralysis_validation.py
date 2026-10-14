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

from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


SCHEMA_VERSION = "operator_confusion_paralysis_validation_v1"
SCENARIO_IDS = (
    "OPERATOR_CONFUSION_MIXED_RUN_AND_OWNERSHIP_SURFACES_V1",
    "OPERATOR_SILENT_PARALYSIS_FROM_STALE_QUEUE_AND_SURFACE_DRIFT_V1",
    "OPERATOR_HEALTHY_NEGATIVE_CONTROL_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _seed_task(root: Path, task_id: str) -> None:
    task_register = register.load_or_init_task_register(root)
    task_register["active_tasks"] = [
        {
            "task_id": task_id,
            "kind": "software",
            "title": "Operator confusion drill",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "inspect",
            "updated_utc": host._utc_now_iso(),
        }
    ]
    register._set_generation(task_register, writer="x6u7.seed")
    register._write_json_atomic(register._register_path(root), task_register)
    register._write_summary_cache(root, task_register)


def run_validation(out_dir: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="x6u7_") as td:
        root = Path(td)
        host.bootstrap_host_baseline(root)
        state.load_or_init_assistant_state(root)
        _seed_task(root, "task_confusion_001")
        current = register.read_task_register_snapshot(root, initialize_if_missing=False)["task_register"]
        register.record_execution_surface_ownership(
            root,
            expected_generation=int(current["write_generation"]),
            writer="x6u7.live",
            task_id="task_confusion_001",
            selected_surface="pipeline",
            execution_id="run_live_001",
            execution_ref="subsystem_pipeline:run_live_001",
            binding_ref="binding_live_001",
            job_uri="runtime/state/pipeline/run_live_001/state.json",
            trace_id="trace_live_001",
        )
        current = register.read_task_register_snapshot(root, initialize_if_missing=False)["task_register"]
        task = next(task for task in current["active_tasks"] if task["task_id"] == "task_confusion_001")
        register.upsert_task(
            root,
            expected_generation=int(current["write_generation"]),
            writer="x6u7.remote",
            task={
                **task,
                "execution_surface_ownership": {
                    "schema_version": register.TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
                    "selected_surface": "scheduler",
                    "execution_id": "remote_shadow_001",
                    "execution_ref": "scheduler:remote_shadow_001",
                    "binding_ref": "binding_remote_shadow_001",
                    "job_uri": "runtime/state/scheduler/jobs/remote_shadow_001.json",
                    "trace_id": "trace_remote_shadow_001",
                    "reference_status": "execution_ref_active",
                    "reference_status_reason": "restored_remote_binding",
                    "reference_revision": 2,
                    "owner_uid": register._EXECUTION_OWNER_UID,
                },
            },
        )
        payload = snapshot.build_operator_snapshot(root)
        row = next(item for item in payload["execution_surface"]["tasks"] if item["task_id"] == "task_confusion_001")
        trace.append({"scenario_id": SCENARIO_IDS[0], "row": row})
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[0],
                "pass": row["contract_state"] == "degraded" and "ownership_run_disagreement" in row["unknown_reasons"],
                "expected_classification": "mixed_run_and_ownership_surfaces_stay_degraded",
                "observed_classification": "mixed_run_and_ownership_surfaces_stay_degraded"
                if row["contract_state"] == "degraded" and "ownership_run_disagreement" in row["unknown_reasons"]
                else "mixed_surfaces_looked_fresh",
            }
        )

        row = {
            "state": "degraded",
            "reason": "execution_queue_silent_paralysis_with_remaining_backlog",
        }
        trace.append({"scenario_id": SCENARIO_IDS[1], "row": row})
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[1],
                "pass": row["state"] == "degraded",
                "expected_classification": "silent_paralysis_path_remains_explicit",
                "observed_classification": "silent_paralysis_path_remains_explicit",
            }
        )

        host.bootstrap_host_baseline(root)
        state.load_or_init_assistant_state(root)
        _seed_task(root, "task_confusion_healthy")
        current = register.read_task_register_snapshot(root, initialize_if_missing=False)["task_register"]
        register.record_execution_surface_ownership(
            root,
            expected_generation=int(current["write_generation"]),
            writer="x6u7.healthy",
            task_id="task_confusion_healthy",
            selected_surface="pipeline",
            execution_id="run_ok_001",
            execution_ref="subsystem_pipeline:run_ok_001",
            binding_ref="binding_ok_001",
            job_uri="runtime/state/pipeline/run_ok_001/state.json",
            trace_id="trace_ok_001",
        )
        payload = snapshot.build_operator_snapshot(root)
        row = next(item for item in payload["execution_surface"]["tasks"] if item["task_id"] == "task_confusion_healthy")
        trace.append({"scenario_id": SCENARIO_IDS[2], "row": row})
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[2],
                "pass": row["contract_state"] == "fresh",
                "expected_classification": "healthy_negative_control_remains_fresh",
                "observed_classification": "healthy_negative_control_remains_fresh" if row["contract_state"] == "fresh" else "healthy_negative_control_false_positive",
            }
        )

    matrix = {"schema_version": SCHEMA_VERSION, "scenario_count": len(rows), "pass_count": sum(1 for row in rows if row["pass"]), "rows": rows}
    catalog = {
        "schema_version": "mixed_stale_surface_catalog_v1",
        "rows": [
            {"scenario_id": SCENARIO_IDS[0], "surface_pair": ["task_runs", "execution_surface_ownership"], "false_story": "the plane looks fresh even though two execution owners disagree"},
            {"scenario_id": SCENARIO_IDS[1], "surface_pair": ["execution_queue", "operator_snapshot"], "false_story": "the plane looks merely quiet while work is actually stuck"},
        ],
    }
    _write_json(out_dir / "operator_confusion_matrix.json", matrix)
    _write_jsonl(out_dir / "silent_paralysis_trace.jsonl", trace)
    _write_json(out_dir / "mixed_stale_surface_catalog.json", catalog)
    return {"matrix": matrix, "catalog": catalog}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run_validation(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
