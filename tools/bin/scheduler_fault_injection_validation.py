#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, Iterator, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_operator_snapshot as operator_snapshot
from infra.webui import server_lab_assistant_task_register as task_register
from infra.webui.server_routes_and_main_parts import (
    part01__lab_assistant_host_health_payload as host_payload,
)
from tools.bin import scheduler_watchdog as watchdog


SCHEMA_VERSION = "scheduler_fault_injection_validation_v1"
SCENARIO_IDS = (
    "FAULT_RECEIPT_TERMINAL_STATE_ACTIVE_V1",
    "FAULT_STATE_CLEARED_LEASE_PRESENT_V1",
    "FAULT_STALE_WATCHDOG_INPUT_V1",
    "FAULT_DISK_PRESSURE_V1",
    "FAULT_IO_DELAY_V1",
    "FAULT_CLOCK_JUMP_V1",
    "SURFACE_TIMEOUT_DURING_ACTIVE_PROGRESS_V1",
    "SURFACE_STALE_SUMMARY_CACHE_V1",
)


def _utc(text: str) -> str:
    if text.endswith("Z"):
        return text
    return f"{text}Z"


def _job_spec(
    job_id: str,
    *,
    run_at: str,
    prompt: str,
    max_runtime_seconds: int = 300,
) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "job_kind": "single_turn",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": job_id},
        "depends_on": [],
        "prompt": prompt,
        "schedule": {"mode": "once", "run_at": _utc(run_at), "timezone": "UTC"},
        "policy": {"max_runtime_seconds": int(max_runtime_seconds)},
    }


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_lock(repo_root: Path, *, job_id: str, run_id: str, pid: int, claimed_utc: str, heartbeat_utc: str = "") -> Path:
    path = sched.locks_dir(repo_root) / f"{job_id}.lock.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_json(
        path,
        {
            "schema_version": "scheduled_job_claim_v1",
            "job_id": job_id,
            "run_id": run_id,
            "trigger": "scheduler",
            "claimed_utc": claimed_utc,
            "heartbeat_utc": heartbeat_utc or claimed_utc,
            "pid": int(pid),
        },
    )
    return path


def _seed_register(repo_root: Path, *, summary_stale: bool, summary_generated_utc: str | None = None) -> Dict[str, Any]:
    register = {
        "schema_version": "lab_assistant_task_register_v1",
        "register_id": "fault_validation_register",
        "write_generation": 1,
        "last_written_utc": "2026-04-13T10:00:00Z",
        "summary_stale": bool(summary_stale),
        "summary_cache_ref": task_register._summary_cache_ref(repo_root),
        "active_tasks": [],
        "residue_tasks": [],
        "completed_tasks": [],
        "open_loops": [],
    }
    summary = task_register._write_summary_cache(repo_root, register)
    if summary_generated_utc:
        summary["generated_utc"] = str(summary_generated_utc)
    register["summary_stale"] = bool(summary_stale)
    task_register._write_json_atomic(task_register._register_path(repo_root), register)
    task_register._write_json_atomic(task_register._summary_cache_path(repo_root), summary)
    return register


def _record_trace(
    traces: List[Dict[str, Any]],
    *,
    scenario_id: str,
    event: str,
    details: Dict[str, Any],
) -> None:
    traces.append(
        {
            "utc": watchdog._utc_iso(),
            "scenario_id": scenario_id,
            "event": event,
            "details": details,
        }
    )


@contextlib.contextmanager
def _quiet_watchdog_context() -> Iterator[None]:
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
            "utc": watchdog._utc_iso(),
            "status": "ready",
            "mode": "ready",
            "blocked": False,
            "summary": "healthy",
            "reasons": [],
            "last_boot_id": "boot_ok",
            "last_boot_utc": watchdog._utc_iso(),
            "last_shutdown_clean": True,
            "surfaces": [],
        }
        operator_snapshot._state.read_assistant_state_snapshot = lambda repo_root, initialize_if_missing=False: {  # type: ignore[assignment]
            "assistant_state": {
                "assistant_id": "assistant_validation",
                "assistant_label": "validation",
                "current_state": "idle",
                "last_written_utc": watchdog._utc_iso(),
            }
        }
        operator_snapshot._recovery.read_recovery_snapshot = lambda repo_root: {  # type: ignore[assignment]
            "status": "ready",
            "summary": "ready",
            "reconciled_utc": watchdog._utc_iso(),
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


def _scenario_receipt_terminal_state_active(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "FAULT_RECEIPT_TERMINAL_STATE_ACTIVE_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_watchdog_context():
        root = Path(td)
        now_utc = watchdog._utc_iso()
        job_id = "receipt_conflict_job"
        run_id = "sj_receipt_conflict"
        sched.save_job(_job_spec(job_id, run_at="2026-04-13T10:00:00", prompt="receipt contradiction"), repo_root=root)
        receipt_path = sched.receipts_dir(root) / job_id / f"{run_id}.json"
        _write_json(receipt_path, {"job_id": job_id, "run_id": run_id, "status": "ok", "finished_at": now_utc})
        sched._write_state(
            job_id,
            {
                "job_id": job_id,
                "active_run_id": run_id,
                "active_started_utc": now_utc,
                "last_status": "running",
                "last_receipt_path": str(receipt_path),
                "updated_utc": now_utc,
            },
            repo_root=root,
        )
        _write_lock(root, job_id=job_id, run_id=run_id, pid=os.getpid(), claimed_utc=now_utc, heartbeat_utc=now_utc)
        result = watchdog.run_watchdog(mode="audit", repo_root=root, scheduler_mode="legacy")
        findings = [f for f in result["findings"] if f["kind"] == "terminal_receipt_state_conflict"]
        snapshots[scenario_id] = {
            "health": result["health"],
            "truth_reconciliation": result["truth_reconciliation"],
            "findings": findings,
        }
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="watchdog_audit",
            details={"finding_kinds": [f["kind"] for f in result["findings"]], "health_reason": result["health"]["reason"]},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "contradiction_class_state_drift_and_fault_recovery_honesty",
            "pass": bool(findings),
            "claim_impact": "blocks_robust_if_missing",
            "expected_classification": "terminal_receipt_state_conflict",
            "observed_classification": findings[0]["kind"] if findings else "",
            "notes": findings[0]["details"]["summary"] if findings else "watchdog missed terminal receipt contradiction",
        }


def _scenario_state_cleared_lease_present(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "FAULT_STATE_CLEARED_LEASE_PRESENT_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_watchdog_context():
        root = Path(td)
        job_id = "ghost_lock_job"
        sched.save_job(_job_spec(job_id, run_at="2026-04-13T10:00:00", prompt="stale lock"), repo_root=root)
        _write_lock(root, job_id=job_id, run_id="sj_ghost_lock", pid=999999, claimed_utc="2026-04-13T09:00:00Z")
        result = watchdog.run_watchdog(mode="audit", repo_root=root, scheduler_mode="legacy")
        findings = [f for f in result["findings"] if f["kind"] == "stale_lock" and f["details"]["job_id"] == job_id]
        snapshots[scenario_id] = {"findings": findings, "health": result["health"]}
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="watchdog_audit",
            details={"finding_kinds": [f["kind"] for f in result["findings"]]},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "contradiction_class_state_drift_and_fault_recovery_honesty",
            "pass": bool(findings),
            "claim_impact": "blocks_robust_if_missing",
            "expected_classification": "stale_lock",
            "observed_classification": findings[0]["kind"] if findings else "",
            "notes": findings[0]["details"].get("lock_snapshot_stale_reason", "") if findings else "watchdog missed stale lock with cleared state",
        }


def _scenario_stale_watchdog_input(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "FAULT_STALE_WATCHDOG_INPUT_V1"
    original_bundle = watchdog.sched_v2_surfaces.build_internal_surface_bundle
    original_load_job = sched.load_job
    original_request = sched.request_run_now
    requested = {"called": False}
    try:
        watchdog.sched_v2_surfaces.build_internal_surface_bundle = lambda **kwargs: {  # type: ignore[assignment]
            "schema_version": "sched_v2_surface_bundle_v1",
            "status": "ready",
            "projection_equivalent": True,
            "parent_uid": "PIS-V2-PARENT",
            "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
            "surfaces": {
                "watch": [
                    {"job_id": "pis_missing_head", "child_uid": "PIS-MISSING", "decision": "admit", "reason": "due", "unmet_dependencies": []},
                    {"job_id": "pis_tail", "child_uid": "PIS-TAIL", "decision": "blocked", "reason": "dependency_pending:PIS-MISSING", "unmet_dependencies": ["pis_missing_head"]},
                ],
                "api": [],
                "web": [],
                "mobile": [],
            },
        }
        sched.load_job = lambda job_id, repo_root=None: (_ for _ in ()).throw(FileNotFoundError(job_id))  # type: ignore[assignment]

        def _fake_request(job_id: str, *, repo_root=None, request_source=""):
            requested["called"] = True
            return {"ok": True}

        sched.request_run_now = _fake_request  # type: ignore[assignment]
        with tempfile.TemporaryDirectory() as td:
            result = watchdog.run_watchdog(
                mode="selfheal",
                repo_root=Path(td),
                scheduler_mode="v2",
                v2_parent_uid="PIS-V2-PARENT",
            )
        findings = [f for f in result["findings"] if f["kind"] == "sched_v2_surface_job_missing"]
        snapshots[scenario_id] = {"findings": findings, "requested_called": requested["called"]}
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="watchdog_v2_selfheal",
            details={"finding_kinds": [f["kind"] for f in result["findings"]], "requested_called": requested["called"]},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "contradiction_class_state_drift_and_fault_recovery_honesty",
            "pass": bool(findings) and not requested["called"],
            "claim_impact": "blocks_robust_if_missing",
            "expected_classification": "sched_v2_surface_job_missing",
            "observed_classification": findings[0]["kind"] if findings else "",
            "notes": "watchdog must refuse to mutate from stale v2 surface input",
        }
    finally:
        watchdog.sched_v2_surfaces.build_internal_surface_bundle = original_bundle  # type: ignore[assignment]
        sched.load_job = original_load_job  # type: ignore[assignment]
        sched.request_run_now = original_request  # type: ignore[assignment]


def _scenario_disk_pressure(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "FAULT_DISK_PRESSURE_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_operator_context():
        root = Path(td)
        _seed_register(root, summary_stale=False)
        task_register._summary_cache_path(root).write_text("{", encoding="utf-8")
        snapshot = operator_snapshot.build_operator_snapshot(root)
        truth_conflicts = list(snapshot.get("operator_summary", {}).get("truth_conflicts") or [])
        findings = [row for row in truth_conflicts if row.get("kind") == "task_register_summary_cache_corruption"]
        snapshots[scenario_id] = {
            "backend_projection": snapshot.get("backend_projection"),
            "truth_conflicts": truth_conflicts,
        }
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="operator_snapshot",
            details={"backend_state": snapshot["backend_projection"]["state"], "truth_conflict_kinds": [row.get("kind") for row in truth_conflicts]},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "degraded_host_conditions_and_partial_write_pressure",
            "pass": bool(findings),
            "claim_impact": "blocks_robust_if_missing",
            "expected_classification": "task_register_summary_cache_corruption",
            "observed_classification": findings[0]["kind"] if findings else "",
            "notes": "partial-write pressure must degrade the operator projection honestly",
        }


def _scenario_io_delay(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "FAULT_IO_DELAY_V1"
    original_inprocess = host_payload._build_schedules_jobs_payload_inprocess
    original_subprocess = host_payload._build_schedules_jobs_payload
    try:
        def _raise_inprocess(*, repo_root=None):
            raise RuntimeError("simulated_inprocess_delay")

        def _fallback_payload(*, repo_root=None, timeout_s=8.0):
            return {
                "schema_version": "scheduled_jobs_list_v1",
                "utc": watchdog._utc_iso(),
                "jobs": [],
                "events": [],
                "scheduler_projection_contract": host_payload._scheduler_projection_contract_payload(),
            }

        host_payload._build_schedules_jobs_payload_inprocess = _raise_inprocess  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = _fallback_payload  # type: ignore[assignment]
        with tempfile.TemporaryDirectory() as td:
            payload = host_payload._schedules_jobs_payload(repo_root=Path(td))
        snapshots[scenario_id] = payload
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="jobs_payload_fallback",
            details={"snapshot_last_error": payload.get("snapshot_last_error", ""), "summary": payload.get("summary", "")},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "degraded_host_conditions_and_partial_write_pressure",
            "pass": "subprocess fallback" in str(payload.get("summary") or "").lower() and not bool(payload.get("snapshot_stale")),
            "claim_impact": "advisory_only",
            "expected_classification": "fresh_subprocess_fallback_after_inprocess_delay",
            "observed_classification": "fresh_subprocess_fallback_after_inprocess_delay" if "subprocess fallback" in str(payload.get("summary") or "").lower() else "",
            "notes": "delay in one observation path should degrade to an honest fresh fallback, not a clean in-process lie",
        }
    finally:
        host_payload._build_schedules_jobs_payload_inprocess = original_inprocess  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = original_subprocess  # type: ignore[assignment]


def _scenario_clock_jump(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "FAULT_CLOCK_JUMP_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_watchdog_context():
        root = Path(td)
        job_id = "future_probe_job"
        run_id = "sj_future_probe"
        sched.save_job(_job_spec(job_id, run_at="2026-04-13T10:00:00", prompt="future probe"), repo_root=root)
        sched._write_state(
            job_id,
            {
                "job_id": job_id,
                "active_run_id": run_id,
                "active_started_utc": "2026-04-13T10:00:00Z",
                "last_status": "running",
                "updated_utc": "2026-04-13T10:00:00Z",
            },
            repo_root=root,
        )
        _write_lock(root, job_id=job_id, run_id=run_id, pid=os.getpid(), claimed_utc="2099-01-01T00:00:00Z", heartbeat_utc="2099-01-01T00:00:00Z")
        result = watchdog.run_watchdog(mode="audit", repo_root=root, scheduler_mode="legacy")
        findings = [
            f for f in result["findings"]
            if f["kind"] == "stale_lock" and f["details"]["job_id"] == job_id and f["details"]["claim_reason"] == "future_probe_utc"
        ]
        snapshots[scenario_id] = {"findings": findings, "health": result["health"]}
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="watchdog_audit",
            details={"finding_kinds": [f["kind"] for f in result["findings"]], "claim_reasons": [f["details"].get("claim_reason") for f in result["findings"] if f["kind"] == "stale_lock"]},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "degraded_host_conditions_and_partial_write_pressure",
            "pass": bool(findings),
            "claim_impact": "blocks_robust_if_missing",
            "expected_classification": "stale_lock:future_probe_utc",
            "observed_classification": "stale_lock:future_probe_utc" if findings else "",
            "notes": "future-dated lease probes must be treated as impossible clock-jump evidence, not healthy runtime",
        }


def _scenario_surface_timeout_during_active_progress(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "SURFACE_TIMEOUT_DURING_ACTIVE_PROGRESS_V1"
    original_inprocess = host_payload._build_schedules_jobs_payload_inprocess
    original_subprocess = host_payload._build_schedules_jobs_payload
    try:
        host_payload._build_schedules_jobs_payload_inprocess = lambda repo_root=None: (_ for _ in ()).throw(RuntimeError("simulated_inprocess_timeout"))  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = lambda repo_root=None, timeout_s=8.0: (_ for _ in ()).throw(TimeoutError("simulated_subprocess_timeout"))  # type: ignore[assignment]
        with tempfile.TemporaryDirectory() as td:
            payload = host_payload._schedules_jobs_payload(repo_root=Path(td))
        snapshots[scenario_id] = payload
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="jobs_payload_unavailable",
            details={"jobs_unavailable": payload.get("jobs_unavailable"), "snapshot_stale": payload.get("snapshot_stale")},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "degraded_operator_api_observation_while_runtime_truth_evolves",
            "pass": bool(payload.get("jobs_unavailable")) and bool(payload.get("snapshot_stale")),
            "claim_impact": "blocks_robust_if_missing",
            "expected_classification": "jobs_unavailable_snapshot_stale",
            "observed_classification": "jobs_unavailable_snapshot_stale" if payload.get("jobs_unavailable") and payload.get("snapshot_stale") else "",
            "notes": "fully degraded observation path must declare jobs unavailable instead of smoothing the failure over",
        }
    finally:
        host_payload._build_schedules_jobs_payload_inprocess = original_inprocess  # type: ignore[assignment]
        host_payload._build_schedules_jobs_payload = original_subprocess  # type: ignore[assignment]


def _scenario_surface_stale_summary_cache(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "SURFACE_STALE_SUMMARY_CACHE_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_operator_context():
        root = Path(td)
        _seed_register(root, summary_stale=True, summary_generated_utc="2026-04-12T00:00:00Z")
        snapshot = operator_snapshot.build_operator_snapshot(root)
        stale_sources = list(snapshot.get("freshness", {}).get("stale_sources") or [])
        snapshots[scenario_id] = {"backend_projection": snapshot.get("backend_projection"), "freshness": snapshot.get("freshness")}
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="operator_snapshot",
            details={"backend_state": snapshot["backend_projection"]["state"], "stale_sources": stale_sources},
        )
        return {
            "scenario_id": scenario_id,
            "failure_class_id": "degraded_operator_api_observation_while_runtime_truth_evolves",
            "pass": "task_register" in stale_sources and str(snapshot["backend_projection"]["state"]) == "stale",
            "claim_impact": "blocks_robust_if_missing",
            "expected_classification": "task_register_stale_projection",
            "observed_classification": "task_register_stale_projection" if "task_register" in stale_sources else "",
            "notes": "stale summary cache must remain visibly advisory",
        }


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    snapshots: Dict[str, Any] = {}
    rows = [
        _scenario_receipt_terminal_state_active(traces, snapshots),
        _scenario_state_cleared_lease_present(traces, snapshots),
        _scenario_stale_watchdog_input(traces, snapshots),
        _scenario_disk_pressure(traces, snapshots),
        _scenario_io_delay(traces, snapshots),
        _scenario_clock_jump(traces, snapshots),
        _scenario_surface_timeout_during_active_progress(traces, snapshots),
        _scenario_surface_stale_summary_cache(traces, snapshots),
    ]
    fault_matrix = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "failed_count": sum(1 for row in rows if not bool(row.get("pass"))),
        "rows": rows,
        "bugs_found_and_fixed": [
            {
                "component": "watchdog",
                "issue": "stale_lock_snapshot_blind_spot",
                "summary": "The watchdog ignored lease files that only appeared in the stale-lock snapshot and therefore missed state-cleared/lease-present contradictions.",
            },
            {
                "component": "scheduler_claim_health",
                "issue": "future_probe_utc_treated_as_healthy",
                "summary": "Future-dated heartbeat timestamps could look healthy under live-PID claims and hide clock-jump contradictions.",
            },
            {
                "component": "watchdog",
                "issue": "terminal_receipt_running_state_conflict_not_classified",
                "summary": "The watchdog did not surface a contradiction when durable receipts were terminal but scheduler state still projected a live run.",
            },
        ],
    }
    contradiction_matrix = {
        "schema_version": "fault_contradiction_matrix_v1",
        "rows": [
            row
            for row in rows
            if row["scenario_id"]
            in {
                "FAULT_RECEIPT_TERMINAL_STATE_ACTIVE_V1",
                "FAULT_STATE_CLEARED_LEASE_PRESENT_V1",
                "FAULT_STALE_WATCHDOG_INPUT_V1",
                "FAULT_CLOCK_JUMP_V1",
            }
        ],
    }
    degraded_surface_observation = {
        "schema_version": "degraded_surface_observation_v1",
        "rows": [
            row
            for row in rows
            if row["scenario_id"]
            in {
                "FAULT_DISK_PRESSURE_V1",
                "FAULT_IO_DELAY_V1",
                "SURFACE_TIMEOUT_DURING_ACTIVE_PROGRESS_V1",
                "SURFACE_STALE_SUMMARY_CACHE_V1",
            }
        ],
    }
    negative_controls = {
        "schema_version": "fault_negative_controls_v1",
        "rows": [
            {
                "negative_control_id": "NEGATIVE_CONTROL_TERMINAL_RECEIPT_CONFLICT_MUST_BE_DETECTED_V1",
                "seeded_scenario_id": "FAULT_RECEIPT_TERMINAL_STATE_ACTIVE_V1",
                "pass": bool(next(row for row in rows if row["scenario_id"] == "FAULT_RECEIPT_TERMINAL_STATE_ACTIVE_V1")["pass"]),
            },
            {
                "negative_control_id": "NEGATIVE_CONTROL_STALE_LOCK_WITHOUT_ACTIVE_STATE_MUST_BE_DETECTED_V1",
                "seeded_scenario_id": "FAULT_STATE_CLEARED_LEASE_PRESENT_V1",
                "pass": bool(next(row for row in rows if row["scenario_id"] == "FAULT_STATE_CLEARED_LEASE_PRESENT_V1")["pass"]),
            },
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "fault_matrix.json", fault_matrix)
    _write_json(out_dir / "contradiction_matrix.json", contradiction_matrix)
    _write_json(out_dir / "degraded_surface_observation.json", degraded_surface_observation)
    _write_json(out_dir / "fault_negative_controls.json", negative_controls)
    _write_jsonl(out_dir / "fault_truth_trace.jsonl", traces)
    _write_json(out_dir / "fault_truth_snapshots.json", {"schema_version": "fault_truth_snapshots_v1", "snapshots": snapshots})
    return {
        "out_dir": str(out_dir),
        "fault_matrix": fault_matrix,
        "contradiction_matrix": contradiction_matrix,
        "degraded_surface_observation": degraded_surface_observation,
        "fault_negative_controls": negative_controls,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Run durability fault and contradiction validation for the plane scheduler/watchdog.")
    ap.add_argument("--out-dir", required=True)
    args = ap.parse_args()
    result = run_validation(out_dir=Path(args.out_dir).resolve())
    print(json.dumps(result["fault_matrix"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
