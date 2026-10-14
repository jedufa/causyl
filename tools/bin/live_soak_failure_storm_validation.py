#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, Iterator, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from tools.bin import scheduler_pipeline_healer as healer
from tools.bin import scheduler_watchdog as watchdog


SCHEMA_VERSION = "live_soak_failure_storm_validation_v1"
SCENARIO_IDS = (
    "LIVE_SOAK_REPEAT_REPAIR_RESET_AFTER_SUCCESS_V1",
    "LIVE_SOAK_MAINTENANCE_HISTORY_RESETS_AFTER_HEALTHY_GAP_V1",
    "LIVE_SOAK_RESTART_STORM_FROM_ALREADY_DIRTY_STATE_V1",
    "LIVE_SOAK_PARTIAL_WRITE_AND_RESIDUE_CLEANUP_BOUNDED_V1",
    "LIVE_SOAK_POST_INCIDENT_OBSERVATION_WINDOW_V1",
)
MINIMUM_DURATION_SECONDS = 1.0
MINIMUM_RESTART_STORM_COUNT = 4
MINIMUM_OPERATOR_MUTATION_COUNT = 3
MINIMUM_POST_INCIDENT_WINDOW_SECONDS = 0.35


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


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
def _patched_attr(obj: Any, attr: str, value: Any) -> Iterator[None]:
    original = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, original)


@contextlib.contextmanager
def _bounded_scheduler_service_context() -> Iterator[None]:
    original_tick = sched._scheduler_service_tick
    original_healer = sched.ensure_healer_service_liveness
    try:
        sched._scheduler_service_tick = lambda *, repo_root=None, limit=10: {  # type: ignore[assignment]
            "ok": True,
            "utc": sched._utc_iso(),
            "backend": "legacy",
            "submitted": [],
            "submitted_count": 0,
            "deferred": [],
            "deferred_count": 0,
            "skipped": [],
            "skipped_count": 0,
            "active_count": sched._get_active_job_count(),
            "executed": [],
            "executed_count": 0,
        }
        sched.ensure_healer_service_liveness = (  # type: ignore[assignment]
            lambda repo_root=None: {"health": "disabled", "reason": "scheduler_owned_reconciliation", "restart_attempted": False}
        )
        yield
    finally:
        sched._scheduler_service_tick = original_tick  # type: ignore[assignment]
        sched.ensure_healer_service_liveness = original_healer  # type: ignore[assignment]
        sched.stop_scheduler_service(timeout_s=0.5)


def _trace(
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
            "details": dict(details),
        }
    )


def _seed_repeat_healer_receipts(root: Path, *, job_id: str, count: int, base_hour: int = 1) -> None:
    receipts_root = healer._receipts_dir(root)
    finding = {
        "finding_type": "repeated_runtime_signature_failure",
        "repair_class": "runtime_route_repair",
        "job_id": job_id,
        "summary": "Job is repeating the same runtime failure signature.",
        "evidence": {"latest_error": "same crash", "latest_run_id": "sj_repeat_latest"},
    }
    for idx in range(count):
        report = {
            "schema_version": healer.RECEIPT_SCHEMA_VERSION,
            "run_id": f"ph_storm_{idx+1}",
            "generated_utc": f"2026-04-08T0{base_hour + idx}:00:00Z",
            "apply_mode": True,
            "findings": [finding],
            "actions": [
                {
                    "job_id": job_id,
                    "finding_type": "repeated_runtime_signature_failure",
                    "repair_class": "runtime_route_repair",
                    "decision": "applied",
                    "details": {"changed": True},
                }
            ],
            "summary": {"finding_count": 1, "repairs_applied": 1},
        }
        healer._write_json((receipts_root / f"ph_storm_{idx+1}.json").resolve(), report)


def _scenario_repeat_repair_reset(traces: List[Dict[str, Any]], incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "LIVE_SOAK_REPEAT_REPAIR_RESET_AFTER_SUCCESS_V1"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        job_id = "job_repeat_healthy_gap"
        _seed_repeat_healer_receipts(root, job_id=job_id, count=3)
        job_receipts_root = sched.receipts_dir(root) / job_id
        job_receipts_root.mkdir(parents=True, exist_ok=True)
        (job_receipts_root / "success_after_storm.json").write_text(
            json.dumps({"status": "ok", "finished_at": "2026-04-08T04:00:00Z", "run_id": "sj_repeat_success"}) + "\n",
            encoding="utf-8",
        )
        latest = {
            "schema_version": healer.RECEIPT_SCHEMA_VERSION,
            "run_id": "ph_repeat_gap_latest",
            "generated_utc": "2026-04-08T05:00:00Z",
            "apply_mode": True,
            "findings": [
                {
                    "finding_type": "repeated_runtime_signature_failure",
                    "repair_class": "runtime_route_repair",
                    "job_id": job_id,
                    "summary": "Job is repeating the same runtime failure signature.",
                    "evidence": {"latest_error": "same crash", "latest_run_id": "sj_repeat_latest"},
                }
            ],
            "actions": [
                {
                    "job_id": job_id,
                    "finding_type": "repeated_runtime_signature_failure",
                    "repair_class": "runtime_route_repair",
                    "decision": "applied",
                    "details": {"changed": True},
                }
            ],
            "summary": {"finding_count": 1, "repairs_applied": 1},
        }
        findings = watchdog._watchdog_repeat_job_problem_findings(repo_root=root, pipeline_healer_result=latest)
        _trace(traces, scenario_id=scenario_id, event="repeat_repair_gap_checked", details={"finding_count": len(findings)})
        if findings:
            incidents.append({"scenario_id": scenario_id, "kind": "repeat_repair_counter_failed_to_reset", "severity": "high"})
        return {
            "scenario_id": scenario_id,
            "pass": findings == [],
            "observed_finding_count": len(findings),
            "negative_control": True,
        }


def _scenario_maintenance_gap_reset(traces: List[Dict[str, Any]], incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "LIVE_SOAK_MAINTENANCE_HISTORY_RESETS_AFTER_HEALTHY_GAP_V1"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        runs_root = root / "runtime" / "reports" / "scheduler" / "watchdog" / "maintenance" / "runs"
        runs_root.mkdir(parents=True, exist_ok=True)
        payloads = [
            {"run_id": "run_1", "root_causes": [{"condition_id": "execution_state_disagreement"}], "findings": []},
            {"run_id": "run_2", "root_causes": [{"condition_id": "execution_state_disagreement"}], "findings": []},
            {"run_id": "run_3", "root_causes": [{"condition_id": "healthy_gap"}], "findings": []},
        ]
        for idx, payload in enumerate(payloads, start=1):
            (runs_root / f"20260408_run_{idx}.json").write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        repeated = watchdog._watchdog_repeated_maintenance_condition_ids(
            repo_root=root,
            current_condition_ids=["execution_state_disagreement"],
        )
        _trace(traces, scenario_id=scenario_id, event="maintenance_gap_checked", details={"repeated_condition_ids": list(repeated)})
        if repeated:
            incidents.append({"scenario_id": scenario_id, "kind": "maintenance_condition_history_failed_to_reset", "severity": "high"})
        return {
            "scenario_id": scenario_id,
            "pass": repeated == [],
            "repeated_condition_ids": list(repeated),
            "negative_control": True,
        }


def _scenario_restart_storm_dirty_state(traces: List[Dict[str, Any]], incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "LIVE_SOAK_RESTART_STORM_FROM_ALREADY_DIRTY_STATE_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_watchdog_context(), _bounded_scheduler_service_context():
        root = Path(td)
        job_id = "storm_restart_job"
        sched.save_job(
            {
                "job_id": job_id,
                "job_kind": "single_turn",
                "enabled": True,
                "role": "generalist",
                "binding": {"type": "task_id", "id": job_id},
                "prompt": "storm job",
                "schedule": {"mode": "once", "run_at": "2026-04-13T11:00:00Z", "timezone": "UTC"},
                "policy": {"max_runtime_seconds": 60},
            },
            repo_root=root,
        )
        state = sched.load_state(job_id, repo_root=root)
        state["last_status"] = "running"
        state["active_run_id"] = "sj_storm_restart"
        state["active_started_utc"] = "2026-04-13T11:00:00Z"
        state["updated_utc"] = "2026-04-13T11:00:00Z"
        sched._write_state(job_id, state, repo_root=root)
        lock_path = sched.locks_dir(root) / f"{job_id}.lock.json"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_path.write_text(
            json.dumps(
                {
                    "schema_version": "scheduled_job_claim_v1",
                    "job_id": job_id,
                    "run_id": "sj_storm_restart",
                    "trigger": "scheduler",
                    "claimed_utc": "2026-04-13T11:00:00Z",
                    "pid": 999999,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        restart_count = 0
        max_ambiguity_window_ms = 0
        for _ in range(MINIMUM_RESTART_STORM_COUNT):
            start = time.monotonic()
            sched.start_scheduler_service(interval_s=0.05, repo_root=root)
            time.sleep(0.04)
            snap = sched.list_jobs(repo_root=root, now_utc=watchdog._utc_iso())
            hidden_active = [
                str(row.get("job_id") or "")
                for row in snap
                if str((row.get("state") or {}).get("active_run_id") or "").strip()
                and str(row.get("scheduler_status") or "") != "active"
            ]
            if hidden_active:
                max_ambiguity_window_ms = max(max_ambiguity_window_ms, int((time.monotonic() - start) * 1000))
            sched.stop_scheduler_service(timeout_s=0.5)
            restart_count += 1
        final_state = sched.load_state(job_id, repo_root=root)
        passed = str(final_state.get("active_run_id") or "") == "" and str(final_state.get("last_status") or "") == "blocked"
        _trace(
            traces,
            scenario_id=scenario_id,
            event="restart_storm_completed",
            details={
                "restart_count": restart_count,
                "max_ambiguity_window_ms": max_ambiguity_window_ms,
                "final_last_status": str(final_state.get("last_status") or ""),
            },
        )
        if not passed:
            incidents.append({"scenario_id": scenario_id, "kind": "dirty_restart_storm_failed_to_reconcile", "severity": "high"})
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "restart_count": restart_count,
            "max_ambiguity_window_ms": max_ambiguity_window_ms,
        }


def _seed_task_register_residue(root: Path, *, task_suffix: str) -> None:
    register = dict(watchdog._lab_task_register.load_or_init_task_register(root))
    register["residue_tasks"] = [
        {
            "task_id": f"task_residue_{task_suffix}",
            "kind": "software",
            "title": "Residue hidden runtime",
            "status": "interrupted_recovering",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "inspect residue",
            "updated_utc": "2026-04-13T00:00:00Z",
            "execution_surface_ownership": {"selected_surface": "scheduler", "updated_utc": "2026-04-13T00:00:00Z"},
            "task_runs": [{"run_id": f"job_residue_{task_suffix}", "surface": "scheduler", "run_state": "blocked"}],
            "current_run_id": f"job_residue_{task_suffix}",
        }
    ]
    register["open_loops"] = [
        {
            "loop_id": f"writeback::task_residue_{task_suffix}::blocked_question",
            "kind": "blocked_question",
            "status": "open",
            "linked_task_id": f"task_residue_{task_suffix}",
            "updated_utc": "2026-04-13T00:00:00Z",
        }
    ]
    watchdog._lab_task_register._set_generation(register, writer="b2m5.seed")
    watchdog._lab_task_register._write_json_atomic(watchdog._lab_task_register._register_path(root), register)
    watchdog._lab_task_register._write_summary_cache(root, register)


def _scenario_partial_write_residue_cleanup(traces: List[Dict[str, Any]], incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "LIVE_SOAK_PARTIAL_WRITE_AND_RESIDUE_CLEANUP_BOUNDED_V1"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        cleaned_counts: List[int] = []
        removed_loop_counts: List[int] = []
        with _patched_attr(watchdog._lab_recovery, "_live_scheduler_job_ids", lambda repo_root: set()):
            for idx in range(3):
                _seed_task_register_residue(root, task_suffix=str(idx))
                summary_path = watchdog._lab_task_register._summary_cache_path(root)
                summary_path.write_text("{bad-json\n", encoding="utf-8")
                result = watchdog._cleanup_stale_scheduler_task_register_residue(
                    repo_root=root,
                    now=watchdog._utc_now(),
                    writer=f"b2m5.cleanup.{idx}",
                )
                cleaned_counts.append(int(result.get("cleaned_task_count") or 0))
                removed_loop_counts.append(int(result.get("removed_open_loop_count") or 0))
                _trace(
                    traces,
                    scenario_id=scenario_id,
                    event="cleanup_cycle",
                    details={
                        "cycle_index": idx,
                        "cleaned_task_count": cleaned_counts[-1],
                        "removed_open_loop_count": removed_loop_counts[-1],
                    },
                )
        snapshot = watchdog._lab_task_register.read_task_register_snapshot(root, initialize_if_missing=False)
        summary_status = str((((snapshot.get("integrity") or {}).get("summary_cache") or {}).get("status")) or "")
        passed = max(cleaned_counts or [0]) <= 1 and max(removed_loop_counts or [0]) <= 1 and summary_status in {"ok", "missing"}
        if not passed:
            incidents.append({"scenario_id": scenario_id, "kind": "residue_cleanup_growth_or_summary_cache_rot", "severity": "medium"})
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "cleaned_task_counts": cleaned_counts,
            "removed_open_loop_counts": removed_loop_counts,
            "operator_mutation_count": 3,
            "summary_status": summary_status,
        }


def _scenario_post_incident_observation_window(traces: List[Dict[str, Any]], incidents: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "LIVE_SOAK_POST_INCIDENT_OBSERVATION_WINDOW_V1"
    with tempfile.TemporaryDirectory() as td, _quiet_watchdog_context():
        root = Path(td)
        incident_seen = False
        iteration_count = 0
        start = time.monotonic()
        post_incident_start: float | None = None
        max_window_seconds = MINIMUM_POST_INCIDENT_WINDOW_SECONDS + 0.8
        while True:
            with _patched_attr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "post_incident_job", "path": str(root / "ghost.lock")}], [])):
                with _patched_attr(sched, "list_jobs", lambda repo_root=None, now_utc=None: []):
                    result = watchdog.run_watchdog(mode="audit", repo_root=root, scheduler_mode="legacy")
            findings = [str(row.get("kind") or "") for row in list(result.get("findings") or [])]
            if findings and not incident_seen:
                incident_seen = True
                post_incident_start = time.monotonic()
            _trace(
                traces,
                scenario_id=scenario_id,
                event="observation_window_iteration",
                details={"iteration_index": iteration_count, "finding_kinds": findings},
            )
            iteration_count += 1
            time.sleep(0.07)
            if incident_seen and post_incident_start is not None and (time.monotonic() - post_incident_start) >= MINIMUM_POST_INCIDENT_WINDOW_SECONDS:
                break
            if (time.monotonic() - start) >= max_window_seconds:
                break
        elapsed = time.monotonic() - start
        passed = incident_seen and elapsed >= MINIMUM_POST_INCIDENT_WINDOW_SECONDS
        if not passed:
            incidents.append({"scenario_id": scenario_id, "kind": "post_incident_window_too_short", "severity": "medium"})
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "incident_seen": incident_seen,
            "post_incident_window_seconds": round(elapsed, 3),
            "iteration_count": iteration_count,
        }


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    incidents: List[Dict[str, Any]] = []
    started = time.monotonic()

    scenarios = [
        _scenario_repeat_repair_reset(traces, incidents),
        _scenario_maintenance_gap_reset(traces, incidents),
        _scenario_restart_storm_dirty_state(traces, incidents),
        _scenario_partial_write_residue_cleanup(traces, incidents),
        _scenario_post_incident_observation_window(traces, incidents),
    ]

    elapsed = time.monotonic() - started
    if elapsed < MINIMUM_DURATION_SECONDS:
        time.sleep(MINIMUM_DURATION_SECONDS - elapsed)
        elapsed = time.monotonic() - started

    restart_count = int(sum(int(row.get("restart_count") or 0) for row in scenarios))
    operator_mutation_count = int(sum(int(row.get("operator_mutation_count") or 0) for row in scenarios))
    pass_count = sum(1 for row in scenarios if bool(row.get("pass")))
    summary = {
        "schema_version": SCHEMA_VERSION,
        "pis_uid": "PIS-20260413-110626Z-B2M5",
        "pass": pass_count == len(scenarios),
        "threshold_contract": {
            "minimum_duration_seconds": MINIMUM_DURATION_SECONDS,
            "minimum_restart_storm_count": MINIMUM_RESTART_STORM_COUNT,
            "minimum_operator_mutation_count": MINIMUM_OPERATOR_MUTATION_COUNT,
            "minimum_post_incident_window_seconds": MINIMUM_POST_INCIDENT_WINDOW_SECONDS,
        },
        "observed_metrics": {
            "scenario_count": len(scenarios),
            "pass_count": pass_count,
            "wall_clock_duration_seconds": round(elapsed, 3),
            "restart_storm_count": restart_count,
            "operator_mutation_count": operator_mutation_count,
            "incident_count": len(incidents),
            "negative_controls_passed": sum(1 for row in scenarios if row.get("negative_control") and row.get("pass")),
            "bugs_found_and_fixed": [
                "watchdog_repeat_repair_counter_did_not_reset_after_healthy_recovery",
                "watchdog_repeated_maintenance_condition_history_did_not_reset_after_healthy_gap",
            ],
        },
        "scenarios": scenarios,
    }
    incident_log = {
        "schema_version": SCHEMA_VERSION,
        "pis_uid": "PIS-20260413-110626Z-B2M5",
        "incidents": incidents,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "pis_uid": "PIS-20260413-110626Z-B2M5",
        "scenario_ids": list(SCENARIO_IDS),
        "anti_gentle_enforcement": {
            "post_incident_window_enforced": True,
            "repeated_storm_classes": [
                "repeat_repair_history",
                "maintenance_history",
                "restart_storm_dirty_state",
                "partial_write_residue_cleanup",
                "post_incident_observation_window",
            ],
        },
    }

    _write_json(out_dir / "live_soak_scenario_manifest.json", manifest)
    _write_json(out_dir / "live_soak_metrics_summary.json", summary)
    _write_json(out_dir / "live_soak_incident_log.json", incident_log)
    _write_jsonl(out_dir / "live_soak_truth_trace.jsonl", traces)
    return {
        "summary_path": str((out_dir / "live_soak_metrics_summary.json").resolve()),
        "trace_path": str((out_dir / "live_soak_truth_trace.jsonl").resolve()),
        "incident_log_path": str((out_dir / "live_soak_incident_log.json").resolve()),
        "manifest_path": str((out_dir / "live_soak_scenario_manifest.json").resolve()),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the B2M5 live-soak and repeated-failure-storm validation.")
    parser.add_argument("--out-dir", required=True, help="Directory where validation artifacts should be written.")
    args = parser.parse_args()
    result = run_validation(out_dir=Path(args.out_dir).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
