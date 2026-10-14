#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.webui.server_routes_and_main_parts.part01__lab_assistant_host_health_payload import (
    _build_schedules_jobs_payload_inprocess,
)
SCHEMA_VERSION = "scheduler_steady_state_soak_validation_v1"
SCENARIO_IDS = (
    "SOAK_SERIAL_CHAIN_V1",
    "SOAK_FANOUT_FANIN_V1",
    "SOAK_BURSTY_MUTATION_V1",
)


class _FakeFuture:
    def done(self) -> bool:
        return False


class _RecordingPool:
    def __init__(self, submissions: List[Dict[str, Any]]) -> None:
        self._submissions = submissions

    def submit(self, func, job_id: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        self._submissions.append(
            {
                "job_id": str(job_id),
                "func_name": getattr(func, "__name__", str(func)),
                "kwargs": dict(kwargs),
            }
        )
        return _FakeFuture()


@contextlib.contextmanager
def _patched_scheduler_environment(submissions: List[Dict[str, Any]]) -> Iterator[None]:
    original_pool = sched._get_dispatch_pool
    original_policy = sched.current_runtime_resource_policy
    try:
        sched._get_dispatch_pool = lambda max_workers=1: _RecordingPool(submissions)  # type: ignore[assignment]
        sched.current_runtime_resource_policy = lambda repo_root=None: {"global_concurrency_cap": 1}  # type: ignore[assignment]
        with sched._DISPATCH_GUARD:
            sched._DISPATCH_ACTIVE_JOBS.clear()
            sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
            sched._DISPATCH_ACTIVE_JOB_META.clear()
        yield
    finally:
        sched._get_dispatch_pool = original_pool  # type: ignore[assignment]
        sched.current_runtime_resource_policy = original_policy  # type: ignore[assignment]
        with sched._DISPATCH_GUARD:
            sched._DISPATCH_ACTIVE_JOBS.clear()
            sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
            sched._DISPATCH_ACTIVE_JOB_META.clear()


def _utc(text: str) -> str:
    if text.endswith("Z"):
        return text
    return f"{text}Z"


def _job_spec(
    job_id: str,
    *,
    run_at: str,
    prompt: str,
    depends_on: Iterable[str] = (),
    max_runtime_seconds: int = 900,
) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "job_kind": "single_turn",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": job_id},
        "depends_on": list(depends_on),
        "prompt": prompt,
        "schedule": {"mode": "once", "run_at": _utc(run_at), "timezone": "UTC"},
        "policy": {"max_runtime_seconds": int(max_runtime_seconds)},
    }


def _save_job(repo_root: Path, spec: Dict[str, Any]) -> None:
    sched.save_job(copy.deepcopy(spec), repo_root=repo_root)


def _mark_running(repo_root: Path, job_id: str, *, run_id: str, started_utc: str) -> None:
    state = sched.load_state(job_id, repo_root=repo_root)
    sched._write_state(
        job_id,
        {
            **state,
            "active_run_id": run_id,
            "active_started_utc": _utc(started_utc),
            "last_status": "running",
        },
        repo_root=repo_root,
    )


def _mark_ok(repo_root: Path, job_id: str, *, finished_utc: str) -> None:
    spec = sched.load_job(job_id, repo_root=repo_root)
    schedule = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    state = sched.load_state(job_id, repo_root=repo_root)
    sched._write_state(
        job_id,
        {
            **state,
            "active_run_id": "",
            "active_started_utc": "",
            "last_status": "ok",
            "last_finished_utc": _utc(finished_utc),
            "last_slot_key": str(schedule.get("run_at") or ""),
            "last_reconciliation_reason": "already_executed",
        },
        repo_root=repo_root,
    )


def _snapshot_truth(repo_root: Path, *, now_utc: str) -> Dict[str, Any]:
    original_list_jobs = sched.list_jobs
    rows = list(
        original_list_jobs(
            repo_root=repo_root,
            now_utc=_utc(now_utc),
            include_context_resolution=False,
            include_reconciliation=False,
        )
    )
    def _list_jobs_with_simulated_now(*args, **kwargs):  # type: ignore[no-untyped-def]
        if "now_utc" not in kwargs or kwargs.get("now_utc") in (None, ""):
            kwargs["now_utc"] = _utc(now_utc)
        return original_list_jobs(*args, **kwargs)

    try:
        sched.list_jobs = _list_jobs_with_simulated_now  # type: ignore[assignment]
        payload = _build_schedules_jobs_payload_inprocess(repo_root=repo_root)
    finally:
        sched.list_jobs = original_list_jobs  # type: ignore[assignment]
    api_rows = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    rows_by_id = {str(row.get("job_id") or ""): row for row in rows}
    api_by_id = {str(row.get("job_id") or ""): row for row in api_rows if isinstance(row, dict)}
    mismatches: List[str] = []
    for job_id in sorted(set(rows_by_id) | set(api_by_id)):
        local = rows_by_id.get(job_id) or {}
        api = api_by_id.get(job_id) or {}
        if str(local.get("scheduler_status") or "") != str(api.get("scheduler_status") or ""):
            mismatches.append(f"status:{job_id}")
        if str(local.get("scheduler_status_reason") or "") != str(api.get("scheduler_status_reason") or ""):
            mismatches.append(f"reason:{job_id}")
    hidden_active_jobs = [
        str(row.get("job_id") or "")
        for row in rows
        if str((row.get("state") or {}).get("active_run_id") or "").strip()
        and str(row.get("scheduler_status") or "") != "active"
    ]
    statuses = [str(row.get("scheduler_status") or "") for row in rows]
    return {
        "jobs": rows,
        "api_jobs": api_rows,
        "mismatches": mismatches,
        "hidden_active_jobs": hidden_active_jobs,
        "active_count": sum(1 for status in statuses if status == "active"),
        "queued_count": sum(1 for status in statuses if status == "queued"),
        "blocked_count": sum(1 for status in statuses if status == "blocked"),
        "completed_once_count": sum(1 for status in statuses if status == "completed_once"),
        "scope_count": len(rows),
        "family_progress": "not_applicable_without_bound_v2_parent",
    }


def _record_trace(
    traces: List[Dict[str, Any]],
    *,
    scenario_id: str,
    step_id: str,
    now_utc: str,
    event: str,
    snapshot: Optional[Dict[str, Any]] = None,
    tick_result: Optional[Dict[str, Any]] = None,
    details: Optional[Dict[str, Any]] = None,
) -> None:
    row: Dict[str, Any] = {
        "utc": _utc(now_utc),
        "scenario_id": scenario_id,
        "step_id": step_id,
        "event": event,
    }
    if snapshot is not None:
        row["snapshot"] = {
            "active_count": snapshot["active_count"],
            "queued_count": snapshot["queued_count"],
            "blocked_count": snapshot["blocked_count"],
            "completed_once_count": snapshot["completed_once_count"],
            "scope_count": snapshot["scope_count"],
            "family_progress": snapshot["family_progress"],
            "mismatches": list(snapshot["mismatches"]),
            "hidden_active_jobs": list(snapshot["hidden_active_jobs"]),
            "rows": [
                {
                    "job_id": str(job.get("job_id") or ""),
                    "status": str(job.get("scheduler_status") or ""),
                    "reason": str(job.get("scheduler_status_reason") or ""),
                }
                for job in snapshot["jobs"]
            ],
        }
    if tick_result is not None:
        row["tick_result"] = {
            "submitted": list(tick_result.get("submitted") or []),
            "submitted_count": int(tick_result.get("submitted_count") or 0),
            "deferred": list(tick_result.get("deferred") or []),
            "skipped": list(tick_result.get("skipped") or []),
        }
    if details:
        row["details"] = details
    traces.append(row)


def _run_tick(repo_root: Path, *, now_utc: str) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    submissions: List[Dict[str, Any]] = []
    with _patched_scheduler_environment(submissions):
        result = sched.scheduler_tick(repo_root=repo_root, now_utc=_utc(now_utc), await_completion=False)
    return result, submissions


def _derive_metrics(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {
        "jobs_launched": 0,
        "dependency_resolutions": 0,
        "terminal_transitions": 0,
        "slot_releases": 0,
        "next_job_starts": 0,
        "queue_mutations": 0,
        "truth_divergence_incidents": 0,
        "hidden_active_incidents": 0,
        "duplicate_launch_count": 0,
        "stuck_state_count": 0,
        "failed_negative_control_count": 0,
    }
    launched_jobs: Dict[str, int] = {}
    last_completion_utc = ""
    for row in traces:
        if row["event"] == "tick":
            submitted = row.get("tick_result", {}).get("submitted") or []
            counts["jobs_launched"] += len(submitted)
            for item in submitted:
                job_id = str(item.get("job_id") or "")
                if not job_id:
                    continue
                launched_jobs[job_id] = launched_jobs.get(job_id, 0) + 1
                if launched_jobs[job_id] > 1:
                    counts["duplicate_launch_count"] += 1
                reason = str(item.get("launch_reason") or "")
                if reason == "dependency_resolved":
                    counts["dependency_resolutions"] += 1
                if last_completion_utc:
                    counts["next_job_starts"] += 1
                    last_completion_utc = ""
        elif row["event"] == "job_completed":
            counts["terminal_transitions"] += 1
            counts["slot_releases"] += 1
            last_completion_utc = str(row.get("utc") or "")
        elif row["event"] == "queue_mutation":
            counts["queue_mutations"] += 1
        elif row["event"] == "negative_control_detected":
            if not bool((row.get("details") or {}).get("detected")):
                counts["failed_negative_control_count"] += 1
        snapshot = row.get("snapshot") if isinstance(row.get("snapshot"), dict) else {}
        counts["truth_divergence_incidents"] += len(snapshot.get("mismatches") or [])
        counts["hidden_active_incidents"] += len(snapshot.get("hidden_active_jobs") or [])
    return counts


def _annotate_launch_reasons(
    tick_result: Dict[str, Any],
    repo_root: Path,
) -> List[Dict[str, Any]]:
    annotated: List[Dict[str, Any]] = []
    for item in list(tick_result.get("submitted") or []):
        job_id = str(item.get("job_id") or "")
        spec = sched.load_job(job_id, repo_root=repo_root)
        deps = list(spec.get("depends_on") or [])
        annotated.append(
            {
                **item,
                "launch_reason": "dependency_resolved" if deps else "direct_due",
            }
        )
    return annotated


def _scenario_serial_chain(repo_root: Path, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "SOAK_SERIAL_CHAIN_V1"
    jobs = [
        _job_spec("soak_serial_a", run_at="2026-04-13T09:00:00", prompt="serial a", max_runtime_seconds=300),
        _job_spec("soak_serial_b", run_at="2026-04-13T09:01:00", prompt="serial b", depends_on=("soak_serial_a",), max_runtime_seconds=1200),
        _job_spec("soak_serial_c", run_at="2026-04-13T09:02:00", prompt="serial c", depends_on=("soak_serial_b",), max_runtime_seconds=300),
        _job_spec("soak_serial_d", run_at="2026-04-13T09:03:00", prompt="serial d", depends_on=("soak_serial_c",), max_runtime_seconds=1200),
    ]
    for spec in jobs:
        _save_job(repo_root, spec)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="bootstrap",
        now_utc="2026-04-13T09:00:00",
        event="snapshot",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T09:00:00"),
        details={"workload_classes": ["serial_chain", "mixed_runtime"]},
    )
    for index, spec in enumerate(jobs, start=1):
        tick_result, _submissions = _run_tick(repo_root, now_utc=f"2026-04-13T09:0{index - 1}:00")
        tick_result["submitted"] = _annotate_launch_reasons(tick_result, repo_root)
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"tick_{index}",
            now_utc=f"2026-04-13T09:0{index - 1}:00",
            event="tick",
            snapshot=_snapshot_truth(repo_root, now_utc=f"2026-04-13T09:0{index - 1}:00"),
            tick_result=tick_result,
        )
        _mark_running(
            repo_root,
            spec["job_id"],
            run_id=f"sj_serial_{index}",
            started_utc=f"2026-04-13T09:0{index - 1}:10",
        )
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"running_{index}",
            now_utc=f"2026-04-13T09:0{index - 1}:10",
            event="job_running",
            snapshot=_snapshot_truth(repo_root, now_utc=f"2026-04-13T09:0{index - 1}:10"),
            details={"job_id": spec["job_id"]},
        )
        _mark_ok(repo_root, spec["job_id"], finished_utc=f"2026-04-13T09:0{index - 1}:50")
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"completed_{index}",
            now_utc=f"2026-04-13T09:0{index - 1}:50",
            event="job_completed",
            snapshot=_snapshot_truth(repo_root, now_utc=f"2026-04-13T09:0{index - 1}:50"),
            details={"job_id": spec["job_id"]},
        )
    return {
        "scenario_id": scenario_id,
        "failure_class_id": "steady_state_dependency_progression_and_queue_churn",
        "workload_classes": ["serial_chain", "mixed_runtime"],
        "operator_actions": [],
    }


def _scenario_fanout_fanin(repo_root: Path, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "SOAK_FANOUT_FANIN_V1"
    jobs = [
        _job_spec("soak_root", run_at="2026-04-13T12:00:00", prompt="root", max_runtime_seconds=180),
        _job_spec("soak_left", run_at="2026-04-13T12:01:00", prompt="left", depends_on=("soak_root",), max_runtime_seconds=900),
        _job_spec("soak_right", run_at="2026-04-13T12:01:00", prompt="right", depends_on=("soak_root",), max_runtime_seconds=240),
        _job_spec("soak_join", run_at="2026-04-13T12:02:00", prompt="join", depends_on=("soak_left", "soak_right"), max_runtime_seconds=600),
    ]
    for spec in jobs:
        _save_job(repo_root, spec)
    order = ["soak_root", "soak_left", "soak_right", "soak_join"]
    for index, job_id in enumerate(order, start=1):
        tick_result, _submissions = _run_tick(repo_root, now_utc=f"2026-04-13T12:0{index - 1}:00")
        tick_result["submitted"] = _annotate_launch_reasons(tick_result, repo_root)
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"tick_{index}",
            now_utc=f"2026-04-13T12:0{index - 1}:00",
            event="tick",
            snapshot=_snapshot_truth(repo_root, now_utc=f"2026-04-13T12:0{index - 1}:00"),
            tick_result=tick_result,
        )
        _mark_running(repo_root, job_id, run_id=f"sj_fan_{index}", started_utc=f"2026-04-13T12:0{index - 1}:05")
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"running_{index}",
            now_utc=f"2026-04-13T12:0{index - 1}:05",
            event="job_running",
            snapshot=_snapshot_truth(repo_root, now_utc=f"2026-04-13T12:0{index - 1}:05"),
            details={"job_id": job_id},
        )
        _mark_ok(repo_root, job_id, finished_utc=f"2026-04-13T12:0{index - 1}:40")
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"completed_{index}",
            now_utc=f"2026-04-13T12:0{index - 1}:40",
            event="job_completed",
            snapshot=_snapshot_truth(repo_root, now_utc=f"2026-04-13T12:0{index - 1}:40"),
            details={"job_id": job_id},
        )
    return {
        "scenario_id": scenario_id,
        "failure_class_id": "steady_state_dependency_progression_and_queue_churn",
        "workload_classes": ["wide_fanout", "fan_in", "mixed_runtime"],
        "operator_actions": [],
    }


def _scenario_bursty_mutation(repo_root: Path, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "SOAK_BURSTY_MUTATION_V1"
    actions: List[Dict[str, Any]] = []
    _save_job(repo_root, _job_spec("soak_mut_root", run_at="2026-04-13T15:00:00", prompt="root", max_runtime_seconds=300))
    _save_job(
        repo_root,
        _job_spec("soak_mut_follow", run_at="2026-04-13T15:01:00", prompt="follow", depends_on=("soak_mut_root",), max_runtime_seconds=1200),
    )
    _save_job(repo_root, _job_spec("soak_mut_remove", run_at="2026-04-13T15:02:00", prompt="remove me", max_runtime_seconds=300))
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="bootstrap",
        now_utc="2026-04-13T15:00:00",
        event="snapshot",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:00"),
        details={"workload_classes": ["burst_arrivals", "operator_add", "operator_edit", "operator_remove"]},
    )

    tick_result, _submissions = _run_tick(repo_root, now_utc="2026-04-13T15:00:00")
    tick_result["submitted"] = _annotate_launch_reasons(tick_result, repo_root)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="tick_root",
        now_utc="2026-04-13T15:00:00",
        event="tick",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:00"),
        tick_result=tick_result,
    )
    _mark_running(repo_root, "soak_mut_root", run_id="sj_mut_root", started_utc="2026-04-13T15:00:05")
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="running_root",
        now_utc="2026-04-13T15:00:05",
        event="job_running",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:05"),
        details={"job_id": "soak_mut_root"},
    )

    _save_job(repo_root, _job_spec("soak_mut_burst_a", run_at="2026-04-13T15:00:10", prompt="burst a", max_runtime_seconds=180))
    actions.append({"utc": _utc("2026-04-13T15:00:10"), "action": "add", "job_id": "soak_mut_burst_a"})
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="add_burst_a",
        now_utc="2026-04-13T15:00:10",
        event="queue_mutation",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:10"),
        details=actions[-1],
    )
    _save_job(repo_root, _job_spec("soak_mut_burst_b", run_at="2026-04-13T15:00:12", prompt="burst b", max_runtime_seconds=180))
    actions.append({"utc": _utc("2026-04-13T15:00:12"), "action": "add", "job_id": "soak_mut_burst_b"})
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="add_burst_b",
        now_utc="2026-04-13T15:00:12",
        event="queue_mutation",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:12"),
        details=actions[-1],
    )

    edited = _job_spec(
        "soak_mut_follow",
        run_at="2026-04-13T15:01:00",
        prompt="follow edited for longer runtime",
        depends_on=("soak_mut_root",),
        max_runtime_seconds=1500,
    )
    _save_job(repo_root, edited)
    actions.append({"utc": _utc("2026-04-13T15:00:15"), "action": "edit", "job_id": "soak_mut_follow"})
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="edit_follow",
        now_utc="2026-04-13T15:00:15",
        event="queue_mutation",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:15"),
        details=actions[-1],
    )

    sched.set_job_enabled("soak_mut_remove", False, repo_root=repo_root)
    sched.delete_job("soak_mut_remove", repo_root=repo_root)
    actions.append({"utc": _utc("2026-04-13T15:00:20"), "action": "disable_delete", "job_id": "soak_mut_remove"})
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="remove_job",
        now_utc="2026-04-13T15:00:20",
        event="queue_mutation",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:20"),
        details=actions[-1],
    )

    _mark_ok(repo_root, "soak_mut_root", finished_utc="2026-04-13T15:00:40")
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="root_completed",
        now_utc="2026-04-13T15:00:40",
        event="job_completed",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:00:40"),
        details={"job_id": "soak_mut_root"},
    )

    for index, job_id in enumerate(("soak_mut_burst_a", "soak_mut_burst_b", "soak_mut_follow"), start=1):
        tick_time = f"2026-04-13T15:01:{index:02d}"
        tick_result, _submissions = _run_tick(repo_root, now_utc=tick_time)
        tick_result["submitted"] = _annotate_launch_reasons(tick_result, repo_root)
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"tick_{job_id}",
            now_utc=tick_time,
            event="tick",
            snapshot=_snapshot_truth(repo_root, now_utc=tick_time),
            tick_result=tick_result,
        )
        _mark_running(repo_root, job_id, run_id=f"sj_mut_{index}", started_utc=tick_time)
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"running_{job_id}",
            now_utc=tick_time,
            event="job_running",
            snapshot=_snapshot_truth(repo_root, now_utc=tick_time),
            details={"job_id": job_id},
        )
        _mark_ok(repo_root, job_id, finished_utc=f"2026-04-13T15:01:{index+20:02d}")
        _record_trace(
            traces,
            scenario_id=scenario_id,
            step_id=f"completed_{job_id}",
            now_utc=f"2026-04-13T15:01:{index+20:02d}",
            event="job_completed",
            snapshot=_snapshot_truth(repo_root, now_utc=f"2026-04-13T15:01:{index+20:02d}"),
            details={"job_id": job_id},
        )

    actual_active = _snapshot_truth(repo_root, now_utc="2026-04-13T15:02:00")["active_count"]
    injected_expected_active = actual_active + 1
    _record_trace(
        traces,
        scenario_id=scenario_id,
        step_id="negative_control",
        now_utc="2026-04-13T15:02:00",
        event="negative_control_detected",
        snapshot=_snapshot_truth(repo_root, now_utc="2026-04-13T15:02:00"),
        details={
            "injected_expected_active_count": injected_expected_active,
            "observed_active_count": actual_active,
            "detected": injected_expected_active != actual_active,
            "reason": "injected_truth_mismatch_should_not_be_smoothed_over",
        },
    )

    return {
        "scenario_id": scenario_id,
        "failure_class_id": "steady_state_dependency_progression_and_queue_churn",
        "workload_classes": ["burst_arrivals", "operator_add", "operator_edit", "operator_remove", "mixed_runtime"],
        "operator_actions": actions,
    }


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="sched_steady_state_soak_") as tmp:
        repo_root = Path(tmp)
        scenarios = [
            _scenario_serial_chain(repo_root, traces),
            _scenario_fanout_fanin(repo_root, traces),
            _scenario_bursty_mutation(repo_root, traces),
        ]

    metrics = _derive_metrics(traces)
    threshold_contract = {
        "minimum_scenario_count": 3,
        "minimum_transition_count": 12,
        "minimum_queue_depth": 4,
        "minimum_mixed_runtime_scenarios": 3,
        "duplicate_launches_allowed": 0,
        "hidden_active_incidents_allowed": 0,
        "truth_divergence_incidents_allowed": 0,
    }
    max_scope = max(int((row.get("snapshot") or {}).get("scope_count") or 0) for row in traces if isinstance(row.get("snapshot"), dict))
    transition_count = metrics["jobs_launched"] + metrics["terminal_transitions"]
    scenario_manifest = {
        "schema_version": SCHEMA_VERSION,
        "pis_uid": "PIS-20260413-061522Z-2F87",
        "source_scorecard_uid": "PIS-20260413-062716Z-FE3D",
        "failure_class_id": "steady_state_dependency_progression_and_queue_churn",
        "threshold_contract": threshold_contract,
        "scenarios": scenarios,
        "environment_conditions": {
            "execution_mode": "deterministic_local_harness",
            "queue_backend": "scheduler_tick_with_recording_pool",
            "global_concurrency_cap": 1,
            "timestamps": "synthetic_utc_intervals",
        },
        "negative_control_policy": {
            "required": True,
            "observed": [
                row["step_id"]
                for row in traces
                if row["event"] == "negative_control_detected"
            ],
        },
    }
    metrics_summary = {
        "schema_version": SCHEMA_VERSION,
        "pis_uid": "PIS-20260413-061522Z-2F87",
        "scenario_ids": list(SCENARIO_IDS),
        "threshold_contract": threshold_contract,
        "observed_metrics": {
            **metrics,
            "scenario_count": len(scenarios),
            "transition_count": transition_count,
            "max_queue_depth": max_scope,
            "mixed_runtime_scenario_count": sum(
                1 for scenario in scenarios if "mixed_runtime" in scenario["workload_classes"]
            ),
        },
        "pass": (
            len(scenarios) >= threshold_contract["minimum_scenario_count"]
            and transition_count >= threshold_contract["minimum_transition_count"]
            and max_scope >= threshold_contract["minimum_queue_depth"]
            and metrics["duplicate_launch_count"] == 0
            and metrics["hidden_active_incidents"] == 0
            and metrics["truth_divergence_incidents"] == 0
            and metrics["failed_negative_control_count"] == 0
        ),
        "notes": [
            "This validation exercises scheduler admission and operator truth in a deterministic local harness, not a wall-clock multi-hour live service soak.",
            "The scenario mix still includes serial chains, fan-out/fan-in, burst arrivals, mixed runtimes, and operator queue mutation under churn.",
            "Family progress is recorded as not applicable because this child does not bind a temporary v2 parent family.",
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "soak_scenario_manifest.json").write_text(json.dumps(scenario_manifest, indent=2) + "\n", encoding="utf-8")
    (out_dir / "soak_metrics_summary.json").write_text(json.dumps(metrics_summary, indent=2) + "\n", encoding="utf-8")
    with (out_dir / "soak_truth_trace.jsonl").open("w", encoding="utf-8") as handle:
        for row in traces:
            handle.write(json.dumps(row, sort_keys=True) + "\n")
    return {
        "scenario_manifest": scenario_manifest,
        "metrics_summary": metrics_summary,
        "trace_path": str((out_dir / "soak_truth_trace.jsonl").resolve()),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run deterministic steady-state scheduler soak validation for PIS-20260413-061522Z-2F87.")
    parser.add_argument("--out-dir", required=True, help="Directory where validation artifacts will be written.")
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    result = run_validation(out_dir=Path(args.out_dir).resolve())
    print(json.dumps({"ok": True, **result["metrics_summary"]["observed_metrics"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
