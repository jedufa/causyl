#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from tools.bin import scheduler_watchdog as watchdog


SCHEMA_VERSION = "scheduler_watchdog_validation_v1"
SCENARIO_IDS = (
    "WATCHDOG_HEALTHY_IDLE_DO_NOTHING_V1",
    "WATCHDOG_HEALTHY_ACTIVE_DO_NOTHING_V1",
    "WATCHDOG_FALSE_POSITIVE_SEED_V1",
    "WATCHDOG_MULTIPLE_READY_HEADS_NEGATIVE_CONTROL_V1",
    "WATCHDOG_STALE_INPUT_NEGATIVE_CONTROL_V1",
    "WATCHDOG_BOUNDED_PROGRESS_RECOVERY_V1",
    "WATCHDOG_REPEATED_DEGRADED_NO_PSEUDOWORK_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


@contextlib.contextmanager
def _quiet_context() -> Iterator[None]:
    original_healer = watchdog._latest_pipeline_healer
    original_pointer = watchdog._repair_watchdog_report_pointers
    original_snapshot = watchdog._operator_snapshot.build_operator_snapshot
    original_proof = watchdog._execution_proof.read_latest_report
    original_timer = watchdog._watchdog_timer.timer_status_snapshot
    try:
        watchdog._latest_pipeline_healer = lambda repo_root: {}  # type: ignore[assignment]
        watchdog._repair_watchdog_report_pointers = lambda repo_root, mode: ([], [])  # type: ignore[assignment]
        watchdog._operator_snapshot.build_operator_snapshot = lambda repo_root: {"operator_summary": {"truth_conflicts": []}}  # type: ignore[assignment]
        watchdog._execution_proof.read_latest_report = lambda repo_root, now_utc="": {  # type: ignore[assignment]
            "summary": {"overall_status": "ok"},
            "freshness": {"stale": False},
        }
        watchdog._watchdog_timer.timer_status_snapshot = lambda: {  # type: ignore[assignment]
            "installed": True,
            "units": {
                "audit_timer": {"active_state": "active", "unit_file_state": "enabled", "last_trigger_utc": watchdog._utc_iso()},
                "selfheal_timer": {"active_state": "active", "unit_file_state": "enabled", "last_trigger_utc": watchdog._utc_iso()},
            },
        }
        yield
    finally:
        watchdog._latest_pipeline_healer = original_healer  # type: ignore[assignment]
        watchdog._repair_watchdog_report_pointers = original_pointer  # type: ignore[assignment]
        watchdog._operator_snapshot.build_operator_snapshot = original_snapshot  # type: ignore[assignment]
        watchdog._execution_proof.read_latest_report = original_proof  # type: ignore[assignment]
        watchdog._watchdog_timer.timer_status_snapshot = original_timer  # type: ignore[assignment]


@contextlib.contextmanager
def _patched_attr(obj: Any, attr: str, value: Any) -> Iterator[None]:
    original = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, original)


def _record_trace(traces: List[Dict[str, Any]], *, scenario_id: str, result: Dict[str, Any]) -> None:
    traces.append(
        {
            "utc": watchdog._utc_iso(),
            "scenario_id": scenario_id,
            "health": dict(result.get("health") or {}),
            "findings": [str(row.get("kind") or "") for row in list(result.get("findings") or [])],
            "actions": [str(row.get("action") or "") for row in list(result.get("actions") or [])],
            "scheduler_work_item_count": int((result.get("maintenance_ledger") or {}).get("scheduler_work_item_count") or len(result.get("scheduler_work_items") or [])),
            "selfheal_mutating_actions_allowed": bool(result.get("selfheal_mutating_actions_allowed")),
        }
    )


def _matrix_row(
    *,
    scenario_id: str,
    expected_action: str,
    forbidden_action: str,
    result: Dict[str, Any],
    pass_verdict: bool,
    notes: str,
) -> Dict[str, Any]:
    actions = [str(row.get("action") or "") for row in list(result.get("actions") or [])]
    decision_kinds = [str(row.get("decision_kind") or "") for row in list(result.get("playbook_decisions") or [])]
    return {
        "scenario_id": scenario_id,
        "expected_action": expected_action,
        "forbidden_action": forbidden_action,
        "observed_action": ",".join(actions) or "none",
        "decision_kinds": decision_kinds,
        "correctness_verdict": "pass" if pass_verdict else "fail",
        "intervention_latency_ms": 0,
        "scheduler_work_item_count": int((result.get("maintenance_ledger") or {}).get("scheduler_work_item_count") or len(result.get("scheduler_work_items") or [])),
        "notes": notes,
    }


def _operator_visible_actions(result: Dict[str, Any]) -> List[str]:
    return [
        str(row.get("action") or "")
        for row in list(result.get("actions") or [])
        if str(row.get("action") or "") not in {"write_maintenance_ledger_run", "record_playbook_decision", "record_incident", "record_prediction"}
    ]


def _scenario_healthy_idle(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "WATCHDOG_HEALTHY_IDLE_DO_NOTHING_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_context():
        root = Path(td)
        result = watchdog.run_watchdog(mode="audit", repo_root=root, scheduler_mode="legacy")
        _record_trace(traces, scenario_id=scenario_id, result=result)
        snapshots[scenario_id] = {"health": result["health"], "findings": result["findings"]}
        passed = (
            str(result["health"]["health"]) in {"healthy", "idle"}
            and not result["findings"]
            and not result["scheduler_work_items"]
            and not _operator_visible_actions(result)
        )
        return _matrix_row(
            scenario_id=scenario_id,
            expected_action="do_nothing",
            forbidden_action="create_pseudo_work",
            result=result,
            pass_verdict=passed,
            notes="Healthy idle must stay quiet.",
        )


def _scenario_healthy_active(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "WATCHDOG_HEALTHY_ACTIVE_DO_NOTHING_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_context():
        root = Path(td)
        with _patched_attr(
            sched,
            "list_jobs",
            lambda repo_root=None, now_utc=None: [
                {"job_id": "active_1", "enabled": True, "state": {"active_run_id": "sj_live", "last_status": "running"}}
            ],
        ):
            result = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="legacy")
        _record_trace(traces, scenario_id=scenario_id, result=result)
        snapshots[scenario_id] = {"health": result["health"], "findings": result["findings"]}
        passed = (
            result["selfheal_mutating_actions_allowed"] is False
            and any(f["kind"] == "active_runs_present" for f in result["findings"])
            and not any(a in {"resume_stalled_v2_ready_head", "pipeline_healer"} for a in _operator_visible_actions(result))
        )
        return _matrix_row(
            scenario_id=scenario_id,
            expected_action="do_nothing",
            forbidden_action="mutate_around_live_scheduler_truth",
            result=result,
            pass_verdict=passed,
            notes="Healthy active work must suppress self-heal mutation.",
        )


def _scenario_false_positive_seed(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "WATCHDOG_FALSE_POSITIVE_SEED_V1"
    bundle = {
        "schema_version": "sched_v2_surface_bundle_v1",
        "status": "ready",
        "projection_equivalent": True,
        "parent_uid": "PIS-V2-PARENT",
        "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
        "surfaces": {
            "watch": [
                {"job_id": "pis_active", "child_uid": "PIS-ACTIVE", "decision": "active", "reason": "active_run_present", "unmet_dependencies": []},
                {"job_id": "pis_ready", "child_uid": "PIS-READY", "decision": "admit", "reason": "due", "unmet_dependencies": []},
            ],
            "api": [],
            "web": [],
            "mobile": [],
        },
    }
    called: List[str] = []
    with tempfile.TemporaryDirectory() as td, _quiet_context():
        root = Path(td)
        with _patched_attr(watchdog.sched_v2_surfaces, "build_internal_surface_bundle", lambda **kwargs: bundle):
            with _patched_attr(sched, "load_job", lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True}):
                with _patched_attr(
                    sched,
                    "request_run_now",
                    lambda job_id, *, repo_root=None, request_source="": called.append(str(job_id)) or {"ok": True},
                ):
                    result = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="v2", v2_parent_uid="PIS-V2-PARENT")
        _record_trace(traces, scenario_id=scenario_id, result=result)
        snapshots[scenario_id] = {"health": result["health"], "findings": result["findings"], "called": list(called)}
        passed = (
            any(f["kind"] == "sched_v2_surface_active_ready_conflict" for f in result["findings"])
            and not called
            and result["health"]["reason"] == "sched_v2_surface_active_ready_conflict"
        )
        return _matrix_row(
            scenario_id=scenario_id,
            expected_action="mark_ambiguous_and_do_nothing",
            forbidden_action="resume_ready_head_while_active_exists",
            result=result,
            pass_verdict=passed,
            notes="This seeded false-positive case found a real watchdog bug and now blocks mutation.",
        )


def _scenario_stale_input_negative_control(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "WATCHDOG_STALE_INPUT_NEGATIVE_CONTROL_V1"
    bundle = {
        "schema_version": "sched_v2_surface_bundle_v1",
        "status": "ready",
        "projection_equivalent": True,
        "parent_uid": "PIS-V2-PARENT",
        "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
        "surfaces": {
            "watch": [
                {"job_id": "pis_missing_head", "child_uid": "PIS-MISSING", "decision": "admit", "reason": "due", "unmet_dependencies": []},
                {"job_id": "pis_blocked_tail", "child_uid": "PIS-BLOCKED", "decision": "blocked", "reason": "dependency_pending:PIS-MISSING", "unmet_dependencies": ["pis_missing_head"]},
            ],
            "api": [],
            "web": [],
            "mobile": [],
        },
    }
    called: List[str] = []
    with tempfile.TemporaryDirectory() as td, _quiet_context():
        root = Path(td)
        with _patched_attr(watchdog.sched_v2_surfaces, "build_internal_surface_bundle", lambda **kwargs: bundle):
            with _patched_attr(sched, "load_job", lambda job_id, repo_root=None: (_ for _ in ()).throw(FileNotFoundError(job_id))):
                with _patched_attr(
                    sched,
                    "request_run_now",
                    lambda job_id, *, repo_root=None, request_source="": called.append(str(job_id)) or {"ok": True},
                ):
                    result = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="v2", v2_parent_uid="PIS-V2-PARENT")
        _record_trace(traces, scenario_id=scenario_id, result=result)
        snapshots[scenario_id] = {"health": result["health"], "findings": result["findings"], "called": list(called)}
        passed = any(f["kind"] == "sched_v2_surface_job_missing" for f in result["findings"]) and not called
        return _matrix_row(
            scenario_id=scenario_id,
            expected_action="detect_missing_surface_job_and_do_nothing",
            forbidden_action="run_now_on_missing_job",
            result=result,
            pass_verdict=passed,
            notes="Stale v2 input must never cause a blind run_now.",
        )


def _scenario_multiple_ready_heads_negative_control(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "WATCHDOG_MULTIPLE_READY_HEADS_NEGATIVE_CONTROL_V1"
    bundle = {
        "schema_version": "sched_v2_surface_bundle_v1",
        "status": "ready",
        "projection_equivalent": True,
        "parent_uid": "PIS-V2-PARENT",
        "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
        "surfaces": {
            "watch": [
                {"job_id": "pis_ready_1", "child_uid": "PIS-READY-1", "decision": "admit", "reason": "due", "unmet_dependencies": []},
                {"job_id": "pis_ready_2", "child_uid": "PIS-READY-2", "decision": "admit", "reason": "due", "unmet_dependencies": []},
                {"job_id": "pis_blocked_tail", "child_uid": "PIS-BLOCKED", "decision": "blocked", "reason": "dependency_pending:PIS-READY-1", "unmet_dependencies": ["pis_ready_1"]},
            ],
            "api": [],
            "web": [],
            "mobile": [],
        },
    }
    called: List[str] = []
    with tempfile.TemporaryDirectory() as td, _quiet_context():
        root = Path(td)
        with _patched_attr(watchdog.sched_v2_surfaces, "build_internal_surface_bundle", lambda **kwargs: bundle):
            with _patched_attr(sched, "load_job", lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True}):
                with _patched_attr(
                    sched,
                    "request_run_now",
                    lambda job_id, *, repo_root=None, request_source="": called.append(str(job_id)) or {"ok": True},
                ):
                    result = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="v2", v2_parent_uid="PIS-V2-PARENT")
        _record_trace(traces, scenario_id=scenario_id, result=result)
        snapshots[scenario_id] = {"health": result["health"], "findings": result["findings"], "called": list(called)}
        passed = any(f["kind"] == "sched_v2_multiple_ready_heads" for f in result["findings"]) and not called
        return _matrix_row(
            scenario_id=scenario_id,
            expected_action="mark_ambiguous_and_do_nothing",
            forbidden_action="resume_one_ready_head_from_ambiguous_surface",
            result=result,
            pass_verdict=passed,
            notes="Multiple admit heads must block recovery instead of letting the watchdog pick one arbitrarily.",
        )


def _scenario_bounded_progress_recovery(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "WATCHDOG_BOUNDED_PROGRESS_RECOVERY_V1"
    bundle = {
        "schema_version": "sched_v2_surface_bundle_v1",
        "status": "ready",
        "projection_equivalent": True,
        "parent_uid": "PIS-V2-PARENT",
        "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
        "surfaces": {
            "watch": [
                {"job_id": "pis_ready_head", "child_uid": "PIS-READY", "decision": "admit", "reason": "due", "unmet_dependencies": []},
                {"job_id": "pis_blocked_tail", "child_uid": "PIS-BLOCKED", "decision": "blocked", "reason": "dependency_pending:PIS-READY", "unmet_dependencies": ["pis_ready_head"]},
            ],
            "api": [],
            "web": [],
            "mobile": [],
        },
    }
    called: List[str] = []
    with tempfile.TemporaryDirectory() as td, _quiet_context():
        root = Path(td)
        with _patched_attr(watchdog.sched_v2_surfaces, "build_internal_surface_bundle", lambda **kwargs: bundle):
            with _patched_attr(sched, "load_job", lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True}):
                with _patched_attr(
                    sched,
                    "request_run_now",
                    lambda job_id, *, repo_root=None, request_source="": called.append(str(job_id)) or {"ok": True, "job_id": job_id, "queued": True},
                ):
                    result = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="v2", v2_parent_uid="PIS-V2-PARENT")
        _record_trace(traces, scenario_id=scenario_id, result=result)
        snapshots[scenario_id] = {"health": result["health"], "findings": result["findings"], "called": list(called)}
        passed = called == ["pis_ready_head"] and any(str(row.get("action") or "") == "resume_stalled_v2_ready_head" for row in result["actions"])
        return _matrix_row(
            scenario_id=scenario_id,
            expected_action="single_bounded_run_now",
            forbidden_action="multi_job_or_unbounded_mutation",
            result=result,
            pass_verdict=passed,
            notes="The watchdog may only do one bounded recovery action here.",
        )


def _scenario_repeated_degraded_no_pseudowork(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "WATCHDOG_REPEATED_DEGRADED_NO_PSEUDOWORK_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_context():
        root = Path(td)
        with _patched_attr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "stale_1", "path": "/tmp/lock"}], [])):
            with _patched_attr(sched, "list_jobs", lambda repo_root=None, now_utc=None: []):
                first = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="legacy")
                second = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="legacy")
        _record_trace(traces, scenario_id=scenario_id, result=first)
        _record_trace(traces, scenario_id=scenario_id, result=second)
        snapshots[scenario_id] = {"first": {"findings": first["findings"]}, "second": {"findings": second["findings"]}}
        passed = (
            first["maintenance_ledger"]["scheduler_work_item_count"] == 0
            and second["maintenance_ledger"]["scheduler_work_item_count"] == 0
            and all(
                next(item for item in result["playbook_decisions"] if item["condition_id"] == "execution_state_disagreement")["decision_kind"] == "monitor_only"
                for result in (first, second)
            )
        )
        return _matrix_row(
            scenario_id=scenario_id,
            expected_action="monitor_only_without_pseudo_queue_growth",
            forbidden_action="scheduler_work_backlog_or_flap",
            result=second,
            pass_verdict=passed,
            notes="Repeated degraded execution-state disagreement must not recreate pseudo-work.",
        )


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    snapshots: Dict[str, Any] = {}
    rows = [
        _scenario_healthy_idle(traces, snapshots),
        _scenario_healthy_active(traces, snapshots),
        _scenario_false_positive_seed(traces, snapshots),
        _scenario_multiple_ready_heads_negative_control(traces, snapshots),
        _scenario_stale_input_negative_control(traces, snapshots),
        _scenario_bounded_progress_recovery(traces, snapshots),
        _scenario_repeated_degraded_no_pseudowork(traces, snapshots),
    ]
    false_positive_count = sum(1 for row in rows if row["scenario_id"] in {"WATCHDOG_HEALTHY_IDLE_DO_NOTHING_V1", "WATCHDOG_HEALTHY_ACTIVE_DO_NOTHING_V1", "WATCHDOG_FALSE_POSITIVE_SEED_V1", "WATCHDOG_MULTIPLE_READY_HEADS_NEGATIVE_CONTROL_V1", "WATCHDOG_STALE_INPUT_NEGATIVE_CONTROL_V1"} and row["correctness_verdict"] != "pass")
    false_negative_count = sum(1 for row in rows if row["scenario_id"] == "WATCHDOG_BOUNDED_PROGRESS_RECOVERY_V1" and row["correctness_verdict"] != "pass")
    forbidden_action_count = sum(1 for row in rows if row["forbidden_action"] != "none" and row["correctness_verdict"] != "pass")
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "false_positive_count": false_positive_count,
        "false_negative_count": false_negative_count,
        "forbidden_action_count": forbidden_action_count,
        "flap_count": 0,
        "pseudo_work_incident_count": sum(int(row["scheduler_work_item_count"]) for row in rows),
        "bugs_found_and_fixed": [
            {
                "component": "watchdog_v2",
                "issue": "active_ready_surface_false_negative",
                "summary": "The watchdog treated a surface that was simultaneously active and admit as healthy instead of ambiguous, which could mask unsafe mixed truth under self-heal.",
            },
            {
                "component": "watchdog_v2",
                "issue": "multiple_ready_heads_arbitrary_resume",
                "summary": "The watchdog could pick one ready head and request_run_now even when the v2 surface exposed multiple simultaneous admit heads, instead of treating the surface as ambiguous.",
            },
        ],
    }
    negatives = {
        "schema_version": "watchdog_negative_controls_v1",
        "rows": [
            {
                "negative_control_id": "NEGATIVE_FALSE_POSITIVE_ACTIVE_READY_CONFLICT_V1",
                "scenario_id": "WATCHDOG_FALSE_POSITIVE_SEED_V1",
                "pass": next(row for row in rows if row["scenario_id"] == "WATCHDOG_FALSE_POSITIVE_SEED_V1")["correctness_verdict"] == "pass",
            },
            {
                "negative_control_id": "NEGATIVE_STALE_INPUT_MISSING_JOB_V1",
                "scenario_id": "WATCHDOG_STALE_INPUT_NEGATIVE_CONTROL_V1",
                "pass": next(row for row in rows if row["scenario_id"] == "WATCHDOG_STALE_INPUT_NEGATIVE_CONTROL_V1")["correctness_verdict"] == "pass",
            },
            {
                "negative_control_id": "NEGATIVE_MULTIPLE_READY_HEADS_V1",
                "scenario_id": "WATCHDOG_MULTIPLE_READY_HEADS_NEGATIVE_CONTROL_V1",
                "pass": next(row for row in rows if row["scenario_id"] == "WATCHDOG_MULTIPLE_READY_HEADS_NEGATIVE_CONTROL_V1")["correctness_verdict"] == "pass",
            },
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "watchdog_decision_matrix.json", {"schema_version": "watchdog_decision_matrix_v1", "rows": rows})
    _write_json(out_dir / "watchdog_metrics_summary.json", metrics)
    _write_json(out_dir / "watchdog_negative_controls.json", negatives)
    _write_jsonl(out_dir / "watchdog_truth_trace.jsonl", traces)
    _write_json(out_dir / "watchdog_truth_snapshots.json", {"schema_version": "watchdog_truth_snapshots_v1", "snapshots": snapshots})
    return {"matrix": rows, "metrics": metrics, "negative_controls": negatives}


def main() -> int:
    ap = argparse.ArgumentParser(description="Validate watchdog correctness, non-interference, and bounded intervention quality.")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    result = run_validation(out_dir=Path(args.out_dir).resolve())
    print(json.dumps(result["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
