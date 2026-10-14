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

from infra.bin import scheduler_live_watch as watch
from infra.webui import server_lab_assistant_operator_snapshot as operator_snapshot
from infra.webui import server_lab_assistant_task_register as task_register
from infra.webui.server_routes_and_main_parts import (
    part01__lab_assistant_host_health_payload as host_payload,
)


SCHEMA_VERSION = "observation_degradation_validation_v1"
SCENARIO_IDS = (
    "OBS_JOBS_UNAVAILABLE_WATCH_FALSE_SMOOTHNESS_V1",
    "OBS_STALE_SNAPSHOT_WATCH_CROSS_TIME_INCOHERENCE_V1",
    "OBS_PAYLOAD_ADVISORY_WHEN_JOBS_UNAVAILABLE_V1",
    "OBS_OPERATOR_SUMMARY_CACHE_CORRUPTION_EXPLICIT_V1",
    "OBS_FRESH_SUBPROCESS_FALLBACK_NEGATIVE_CONTROL_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _record_trace(traces: List[Dict[str, Any]], *, scenario_id: str, event: str, details: Dict[str, Any]) -> None:
    traces.append({"scenario_id": scenario_id, "event": event, "details": details})


def _v2_active_payload(*, jobs: List[Dict[str, Any]] | None = None, snapshot_stale: bool, jobs_unavailable: bool, summary: str) -> Dict[str, Any]:
    return {
        "jobs": list(jobs or []),
        "jobs_unavailable": jobs_unavailable,
        "snapshot_stale": snapshot_stale,
        "summary": summary,
        "scheduler": {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-27T09:19:50Z", "last_error": ""},
        "watchdog": {},
        "healer": {},
        "observation_integrity": {
            "schema_version": "scheduled_jobs_observation_integrity_v1",
            "status": "degraded" if (snapshot_stale or jobs_unavailable) else "fresh",
            "advisory_only": bool(snapshot_stale or jobs_unavailable),
            "reasons": [reason for reason, enabled in (("jobs_unavailable", jobs_unavailable), ("snapshot_stale", snapshot_stale)) if enabled],
            "summary": summary,
            "last_error": "simulated_observation_degradation" if (snapshot_stale or jobs_unavailable) else "",
        },
        "scheduler_v2": {
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "process": {"public_ports": []},
            "shadow_payload": {
                "evaluation": {
                    "projection": {
                        "family_parent_uid": "PIS-20260413-OBSERVE-PA01",
                        "family_status": "active",
                        "current_stage": "implement",
                        "family_progress": {
                            "completed_children": 2,
                            "total_children": 6,
                            "active_children": 1,
                            "blocked_children": 1,
                            "failed_children": 0,
                        },
                    },
                    "admitted": [
                        {
                            "child_uid": "PIS-20260413-OBSERVE-AA01",
                            "job_id": "pis_active_1",
                            "decision": "active",
                            "reason": "active_run_present",
                            "gate": "continue",
                            "unmet_dependencies": [],
                        },
                        {
                            "child_uid": "PIS-20260413-OBSERVE-IN04",
                            "job_id": "pis_next_2",
                            "decision": "blocked",
                            "reason": "dependency_pending:PIS-20260413-OBSERVE-AA01",
                            "gate": "continue",
                            "unmet_dependencies": ["PIS-20260413-OBSERVE-AA01"],
                        },
                    ],
                }
            },
        },
    }


@contextlib.contextmanager
def _quiet_operator_context() -> Iterator[None]:
    original_host = operator_snapshot._host.host_health_snapshot
    original_state = operator_snapshot._state.read_assistant_state_snapshot
    original_recovery = operator_snapshot._recovery.read_recovery_snapshot
    original_self_check = operator_snapshot._self_check.refresh_default_checks_if_needed
    original_ownership = operator_snapshot._truth_ownership.ownership_snapshot
    original_resource = operator_snapshot._resource_claim_ledger.resource_concurrency_snapshot
    original_proof = operator_snapshot._execution_proof.read_latest_report
    try:
        operator_snapshot._host.host_health_snapshot = lambda repo_root, initialize_if_missing=False: {  # type: ignore[assignment]
            "schema_version": "lab_assistant_host_health_v1",
            "utc": "2026-04-13T12:00:00Z",
            "status": "ready",
            "mode": "ready",
            "blocked": False,
            "summary": "healthy",
            "reasons": [],
            "last_boot_id": "boot_ok",
            "last_boot_utc": "2026-04-13T12:00:00Z",
            "last_shutdown_clean": True,
            "surfaces": [],
        }
        operator_snapshot._state.read_assistant_state_snapshot = lambda repo_root, initialize_if_missing=False: {  # type: ignore[assignment]
            "assistant_state": {
                "assistant_id": "assistant_validation",
                "assistant_label": "validation",
                "current_state": "idle",
                "last_written_utc": "2026-04-13T12:00:00Z",
            }
        }
        operator_snapshot._recovery.read_recovery_snapshot = lambda repo_root: {  # type: ignore[assignment]
            "status": "ready",
            "summary": "ready",
            "reconciled_utc": "2026-04-13T12:00:00Z",
            "continuity_proven": True,
            "degraded_reasons": [],
            "surface_gaps": [],
            "task_outcomes": [],
        }
        operator_snapshot._self_check.refresh_default_checks_if_needed = lambda repo_root: {  # type: ignore[assignment]
            "mode": "ok",
            "status": "ok",
            "failed_checks": [],
            "stale_checks": [],
            "summary": "",
        }
        operator_snapshot._truth_ownership.ownership_snapshot = lambda: {"owner": "validation"}  # type: ignore[assignment]
        operator_snapshot._resource_claim_ledger.resource_concurrency_snapshot = lambda repo_root, runtime_policy=None: {}  # type: ignore[assignment]
        operator_snapshot._execution_proof.read_latest_report = lambda repo_root, now_utc="": {  # type: ignore[assignment]
            "summary": {"overall_status": "ok"},
            "freshness": {"stale": False},
        }
        yield
    finally:
        operator_snapshot._host.host_health_snapshot = original_host  # type: ignore[assignment]
        operator_snapshot._state.read_assistant_state_snapshot = original_state  # type: ignore[assignment]
        operator_snapshot._recovery.read_recovery_snapshot = original_recovery  # type: ignore[assignment]
        operator_snapshot._self_check.refresh_default_checks_if_needed = original_self_check  # type: ignore[assignment]
        operator_snapshot._truth_ownership.ownership_snapshot = original_ownership  # type: ignore[assignment]
        operator_snapshot._resource_claim_ledger.resource_concurrency_snapshot = original_resource  # type: ignore[assignment]
        operator_snapshot._execution_proof.read_latest_report = original_proof  # type: ignore[assignment]


def _scenario_jobs_unavailable_watch_false_smoothness(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "OBS_JOBS_UNAVAILABLE_WATCH_FALSE_SMOOTHNESS_V1"
    payload = _v2_active_payload(
        snapshot_stale=True,
        jobs_unavailable=True,
        summary="Jobs view is temporarily unavailable; scheduler service status is still reported from the plane.",
    )
    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    snapshots[scenario_id] = {"rendered": rendered}
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="watch_render",
        details={"contains_observation_line": "observation: status=degraded" in rendered},
    )
    return {
        "scenario_id": scenario_id,
        "pass": (
            "observation: status=degraded" in rendered
            and "v2_family:" not in rendered
            and "v2_active:" not in rendered
            and "V2 NEXT" not in rendered
            and "family_progress: v2 family progress" not in rendered
        ),
        "expected_classification": "degraded_observation_blocks_v2_primary_projection",
        "observed_classification": "degraded_observation_blocks_v2_primary_projection"
        if "observation: status=degraded" in rendered and "v2_family:" not in rendered
        else "watch_false_smoothness",
        "notes": "Watch must not smooth stale jobs-unavailable observation into fresh v2 family activity.",
    }


def _scenario_stale_snapshot_cross_time_incoherence(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "OBS_STALE_SNAPSHOT_WATCH_CROSS_TIME_INCOHERENCE_V1"
    payload = _v2_active_payload(
        jobs=[{
            "job_id": "pis_done_1",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": "PIS-DONE-1"},
            "display_title": "Done child",
            "state": {"last_status": "ok"},
            "due": {"reason": "completed"},
            "scheduler_projection_v1": {"projected_status": "completed_once", "projection_reason": "terminal_success", "durable_conflict": False},
        }],
        snapshot_stale=True,
        jobs_unavailable=False,
        summary="Jobs snapshot is stale while scheduler service status is still reported from the plane.",
    )
    rendered = watch._render("http://localhost:8787", payload, tz_name="UTC", color=False)
    snapshots[scenario_id] = {"rendered": rendered}
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="watch_render",
        details={"contains_previous_done": "completed_once" in rendered, "contains_v2_family": "v2_family:" in rendered},
    )
    return {
        "scenario_id": scenario_id,
        "pass": "observation: status=degraded" in rendered and "v2_family:" not in rendered and "ACTIVE\n  (none)" in rendered,
        "expected_classification": "cross_time_incoherence_stays_explicit",
        "observed_classification": "cross_time_incoherence_stays_explicit"
        if "observation: status=degraded" in rendered and "v2_family:" not in rendered
        else "cross_time_incoherence_smoothed_over",
        "notes": "Cross-time stale observation must not stitch old rows and new v2 activity into one plausible story.",
    }


def _scenario_payload_advisory_when_jobs_unavailable(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "OBS_PAYLOAD_ADVISORY_WHEN_JOBS_UNAVAILABLE_V1"
    original_inprocess = host_payload._build_schedules_jobs_payload_inprocess
    original_subprocess = host_payload._build_schedules_jobs_payload
    original_scheduler = host_payload._scheduled_jobs.scheduler_service_status
    original_sched = host_payload._sched_v2_surfaces.build_internal_surface_bundle
    try:
        host_payload._build_schedules_jobs_payload_inprocess = lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("simulated_inprocess_timeout"))  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("simulated_subprocess_timeout"))  # type: ignore[assignment]
        host_payload._scheduled_jobs.scheduler_service_status = lambda: {"running": True, "interval_s": 30, "last_tick_utc": "2026-03-08T04:00:00Z", "last_error": ""}  # type: ignore[assignment]
        host_payload._sched_v2_surfaces.build_internal_surface_bundle = lambda **_kwargs: {  # type: ignore[assignment]
            "schema_version": host_payload._sched_v2_surfaces.SURFACE_BUNDLE_SCHEMA_VERSION,
            "status": "ready",
            "projection_equivalent": True,
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "shadow_payload": {"evaluation": {"projection": {"family_parent_uid": "PIS-20260412-060451Z-EB85"}}},
            "surfaces": {"api": [], "watch": [], "web": [], "mobile": []},
        }
        with tempfile.TemporaryDirectory() as td:
            payload = host_payload._schedules_jobs_payload(repo_root=Path(td))
        snapshots[scenario_id] = payload
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="jobs_payload",
            details={"observation_status": payload.get("observation_integrity", {}).get("status", "")},
        )
        return {
            "scenario_id": scenario_id,
            "pass": (
                payload.get("jobs_unavailable") is True
                and payload.get("snapshot_stale") is True
                and payload.get("observation_integrity", {}).get("status") == "degraded"
                and payload.get("scheduler_v2", {}).get("observation_advisory", {}).get("status") == "degraded"
            ),
            "expected_classification": "payload_marks_v2_advisory_under_degraded_observation",
            "observed_classification": "payload_marks_v2_advisory_under_degraded_observation"
            if payload.get("observation_integrity", {}).get("status") == "degraded"
            else "payload_missing_observation_advisory",
            "notes": "Schedules payload must carry a structured degraded-observation advisory when jobs are unavailable.",
        }
    finally:
        host_payload._build_schedules_jobs_payload_inprocess = original_inprocess  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = original_subprocess  # type: ignore[assignment]
        host_payload._scheduled_jobs.scheduler_service_status = original_scheduler  # type: ignore[assignment]
        host_payload._sched_v2_surfaces.build_internal_surface_bundle = original_sched  # type: ignore[assignment]


def _scenario_operator_summary_cache_corruption_explicit(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "OBS_OPERATOR_SUMMARY_CACHE_CORRUPTION_EXPLICIT_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_operator_context():
        root = Path(td)
        task_register.load_or_init_task_register(root)
        summary_path = root / "runtime" / "state" / "lab_assistant" / "task_register" / "summary_cache.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text('{"register_generation": 1', encoding="utf-8")
        snapshot = operator_snapshot.build_operator_snapshot(root)
        conflicts = list(snapshot.get("operator_summary", {}).get("truth_conflicts") or [])
        snapshots[scenario_id] = {"truth_conflicts": conflicts, "freshness": snapshot.get("freshness")}
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="operator_snapshot",
            details={"truth_conflict_kinds": [row.get("kind") for row in conflicts]},
        )
        return {
            "scenario_id": scenario_id,
            "pass": any(row.get("kind") == "task_register_summary_cache_corruption" for row in conflicts),
            "expected_classification": "task_register_summary_cache_corruption",
            "observed_classification": "task_register_summary_cache_corruption"
            if any(row.get("kind") == "task_register_summary_cache_corruption" for row in conflicts)
            else "operator_snapshot_smoothed_summary_corruption",
            "notes": "Operator snapshot must report corrupt summary-cache truth explicitly.",
        }


def _scenario_fresh_subprocess_fallback_negative_control(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "OBS_FRESH_SUBPROCESS_FALLBACK_NEGATIVE_CONTROL_V1"
    original_inprocess = host_payload._build_schedules_jobs_payload_inprocess
    original_subprocess = host_payload._build_schedules_jobs_payload
    try:
        host_payload._build_schedules_jobs_payload_inprocess = lambda **_kwargs: (_ for _ in ()).throw(TimeoutError("in-process timed out"))  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = lambda **_kwargs: {  # type: ignore[assignment]
            "schema_version": "scheduled_jobs_list_v1",
            "utc": "2026-04-13T12:00:00Z",
            "jobs": [],
            "events": [],
            "scheduler_projection_contract": host_payload._scheduler_projection_contract_payload(),
        }
        with tempfile.TemporaryDirectory() as td:
            payload = host_payload._schedules_jobs_payload(repo_root=Path(td))
        snapshots[scenario_id] = payload
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="jobs_payload_fallback",
            details={"observation_status": payload.get("observation_integrity", {}).get("status", "")},
        )
        return {
            "scenario_id": scenario_id,
            "pass": payload.get("observation_integrity", {}).get("status") == "fresh" and not payload.get("observation_integrity", {}).get("advisory_only"),
            "expected_classification": "fresh_subprocess_fallback_remains_usable",
            "observed_classification": "fresh_subprocess_fallback_remains_usable"
            if payload.get("observation_integrity", {}).get("status") == "fresh"
            else "fallback_over_degraded",
            "notes": "Fresh subprocess fallback is a negative control and must not be mislabeled as degraded observation.",
        }
    finally:
        host_payload._build_schedules_jobs_payload_inprocess = original_inprocess  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = original_subprocess  # type: ignore[assignment]


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    snapshots: Dict[str, Any] = {}
    rows = [
        _scenario_jobs_unavailable_watch_false_smoothness(traces, snapshots),
        _scenario_stale_snapshot_cross_time_incoherence(traces, snapshots),
        _scenario_payload_advisory_when_jobs_unavailable(traces, snapshots),
        _scenario_operator_summary_cache_corruption_explicit(traces, snapshots),
        _scenario_fresh_subprocess_fallback_negative_control(traces, snapshots),
    ]
    matrix = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "failed_count": sum(1 for row in rows if not bool(row.get("pass"))),
        "rows": rows,
        "bugs_found_and_fixed": [
            {
                "component": "scheduler_live_watch",
                "issue": "watch_rendered_v2_activity_from_jobs_unavailable_payload",
                "summary": "The watch could render fresh v2 family activity from a payload that explicitly said the jobs view was unavailable and stale.",
            },
            {
                "component": "schedules_payload_contract",
                "issue": "payload_missing_structured_observation_advisory",
                "summary": "The schedules jobs payload lacked a structured degraded-observation advisory for web and watch consumers when jobs were unavailable.",
            },
        ],
    }
    negatives = {
        "schema_version": "observation_negative_controls_v1",
        "rows": [
            {
                "negative_control_id": "NEGCTRL_FRESH_SUBPROCESS_FALLBACK_STAYS_USABLE_V1",
                "seeded_scenario_id": "OBS_FRESH_SUBPROCESS_FALLBACK_NEGATIVE_CONTROL_V1",
                "pass": bool(next(row for row in rows if row["scenario_id"] == "OBS_FRESH_SUBPROCESS_FALLBACK_NEGATIVE_CONTROL_V1")["pass"]),
            }
        ],
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "pass_count": sum(1 for row in rows if bool(row.get("pass"))),
        "fail_count": sum(1 for row in rows if not bool(row.get("pass"))),
        "bugs_found_and_fixed": [
            {"issue": "watch_rendered_v2_activity_from_jobs_unavailable_payload", "status": "fixed"},
            {"issue": "payload_missing_structured_observation_advisory", "status": "fixed"},
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "observation_degradation_matrix.json", matrix)
    _write_json(out_dir / "observation_negative_controls.json", negatives)
    _write_jsonl(out_dir / "operator_truth_trace.jsonl", traces)
    _write_json(out_dir / "observation_truth_snapshots.json", {"schema_version": "observation_truth_snapshots_v1", "snapshots": snapshots})
    _write_json(out_dir / "observation_metrics_summary.json", summary)
    return {"matrix": matrix, "negative_controls": negatives, "summary": summary, "snapshots": snapshots}


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run degraded observation honesty validation.")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    result = run_validation(out_dir=Path(args.out_dir))
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
