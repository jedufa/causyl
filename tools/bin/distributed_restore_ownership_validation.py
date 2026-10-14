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
from infra.webui import server_lab_assistant_task_register as register


SCHEMA_VERSION = "distributed_restore_ownership_validation_v1"
SCENARIO_IDS = (
    "DISTRIBUTED_RESTORE_ORPHAN_REMOTE_BINDING_V1",
    "DISTRIBUTED_RESTORE_OWNERSHIP_CONFLICT_V1",
    "DISTRIBUTED_RESTORE_HEALTHY_NEGATIVE_CONTROL_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _seed_repo(root: Path) -> None:
    host.bootstrap_host_baseline(root)
    task_register = register.load_or_init_task_register(root)
    task_register["active_tasks"] = []
    register._set_generation(task_register, writer="v4s5.seed")
    register._write_json_atomic(register._register_path(root), task_register)
    register._write_summary_cache(root, task_register)


def run_validation(out_dir: Path) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    trace: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory(prefix="v4s5_") as td:
        root = Path(td)
        _seed_repo(root)
        task_register = register.read_task_register_snapshot(root, initialize_if_missing=False)["task_register"]
        task_register["active_tasks"] = [
            {
                "task_id": "remote_task_001",
                "kind": "software",
                "title": "Remote active restore claim",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "next_expected_action": "reconcile",
                "updated_utc": host._utc_now_iso(),
                "execution_surface_ownership": {
                    "schema_version": register.TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
                    "selected_surface": "scheduler",
                    "execution_id": "remote_job_001",
                    "execution_ref": "scheduler:remote_job_001",
                    "binding_ref": "execution_queue:remote_job_001",
                    "job_uri": "governance/agents/runtime/execution_queue/state.json",
                    "trace_id": "trace_remote_job_001",
                    "reference_status": "execution_ref_active",
                    "reference_status_reason": "restored_from_other_host",
                    "reference_revision": 1,
                    "owner_uid": register._EXECUTION_OWNER_UID,
                },
            }
        ]
        register._set_generation(task_register, writer="v4s5.orphan")
        register._write_json_atomic(register._register_path(root), task_register)
        register._write_summary_cache(root, task_register)
        truth = host._scheduler_truth_snapshot(root)
        trace.append({"scenario_id": SCENARIO_IDS[0], "truth": truth})
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[0],
                "pass": truth["coherent"] is False and truth["orphan_scheduler_binding_task_ids"] == ["remote_task_001"],
                "expected_classification": "orphan_remote_binding_blocks_distributed_restore_claim",
                "observed_classification": "orphan_remote_binding_blocks_distributed_restore_claim"
                if truth["coherent"] is False and truth["orphan_scheduler_binding_task_ids"] == ["remote_task_001"]
                else "remote_binding_looked_coherent",
            }
        )

        task_register = register.read_task_register_snapshot(root, initialize_if_missing=False)["task_register"]
        task_register["active_tasks"] = [
            {
                "task_id": "conflict_task_001",
                "kind": "software",
                "title": "Conflicting restore ownership",
                "status": "active",
                "owner": "assistant",
                "source": "operator",
                "next_expected_action": "reconcile",
                "updated_utc": host._utc_now_iso(),
                "execution_surface_ownership": {
                    "schema_version": register.TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
                    "selected_surface": "scheduler",
                    "execution_id": "remote_job_002",
                    "execution_ref": "scheduler:remote_job_002",
                    "binding_ref": "execution_queue:remote_job_002",
                    "job_uri": "governance/agents/runtime/execution_queue/state.json",
                    "trace_id": "trace_remote_job_002",
                    "reference_status": "execution_ref_conflict",
                    "reference_status_reason": "two_hosts_claim_same_task",
                    "reference_revision": 2,
                    "owner_uid": register._EXECUTION_OWNER_UID,
                },
            }
        ]
        register._set_generation(task_register, writer="v4s5.conflict")
        register._write_json_atomic(register._register_path(root), task_register)
        register._write_summary_cache(root, task_register)
        truth = host._scheduler_truth_snapshot(root)
        trace.append({"scenario_id": SCENARIO_IDS[1], "truth": truth})
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[1],
                "pass": truth["coherent"] is False and truth["scheduler_binding_conflict_task_ids"] == ["conflict_task_001"],
                "expected_classification": "ownership_conflict_blocks_distributed_restore_claim",
                "observed_classification": "ownership_conflict_blocks_distributed_restore_claim"
                if truth["coherent"] is False and truth["scheduler_binding_conflict_task_ids"] == ["conflict_task_001"]
                else "ownership_conflict_looked_coherent",
            }
        )

        _seed_repo(root)
        truth = host._scheduler_truth_snapshot(root)
        trace.append({"scenario_id": SCENARIO_IDS[2], "truth": truth})
        rows.append(
            {
                "scenario_id": SCENARIO_IDS[2],
                "pass": truth["coherent"] is True,
                "expected_classification": "healthy_negative_control_remains_coherent",
                "observed_classification": "healthy_negative_control_remains_coherent"
                if truth["coherent"] is True
                else "healthy_negative_control_false_positive",
            }
        )

    matrix = {"schema_version": SCHEMA_VERSION, "scenario_count": len(rows), "pass_count": sum(1 for row in rows if row["pass"]), "rows": rows}
    assessment = {
        "schema_version": "ownership_claim_assessment_v1",
        "stronger_restore_safety_claim_earned": False,
        "blocked_by": ["orphan_remote_binding", "ownership_conflict"],
    }
    _write_json(out_dir / "distributed_restore_matrix.json", matrix)
    _write_jsonl(out_dir / "replay_conflict_trace.jsonl", trace)
    _write_json(out_dir / "ownership_claim_assessment.json", assessment)
    return {"matrix": matrix, "assessment": assessment}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out-dir", type=Path, required=True)
    args = parser.parse_args()
    run_validation(args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
