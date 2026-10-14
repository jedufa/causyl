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
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as task_register
from tools.bin import scheduler_watchdog as watchdog


SCHEMA_VERSION = "scheduler_host_sickness_validation_v1"
SCENARIO_IDS = (
    "HOST_SICKNESS_ACTIVE_RUNTIME_STORAGE_CRITICAL_V1",
    "HOST_SICKNESS_ACTIVE_STALE_LOCK_RECOVERY_V1",
    "HOST_SICKNESS_PHANTOM_ACTIVE_RUN_PRESSURE_V1",
    "HOST_SICKNESS_CLOCK_JUMP_ACTIVE_CLAIM_V1",
    "HOST_SICKNESS_PARTIAL_WRITE_PRESSURE_ACTIVE_TASK_REGISTER_V1",
)
BUG_ACTIVE_BUSY_MASKED_RUNTIME_STORAGE_CRITICAL = "active_busy_masked_runtime_storage_critical"
BUG_ACTIVE_BUSY_MASKED_RUNTIME_RECOVERY_NEEDED = "active_busy_masked_runtime_recovery_needed"
BUG_HOST_TRUTH_SNAPSHOT_IGNORED_PHANTOM_ACTIVE_PRESSURE = "host_truth_snapshot_ignored_phantom_active_pressure"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


@contextlib.contextmanager
def _patched_attr(obj: Any, attr: str, value: Any) -> Iterator[None]:
    original = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, original)


@contextlib.contextmanager
def _stable_context() -> Iterator[None]:
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


def _trace(traces: List[Dict[str, Any]], *, scenario_id: str, details: Dict[str, Any]) -> None:
    traces.append({"utc": watchdog._utc_iso(), "scenario_id": scenario_id, "details": dict(details)})


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "assistant_state").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "task_register").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)


def _seed_active_register(root: Path, *, task_id: str = "task_host_sickness") -> None:
    register = dict(task_register.load_or_init_task_register(root))
    register["active_tasks"] = [
        {
            "task_id": task_id,
            "kind": "software",
            "title": "Host sickness drill task",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "execute",
            "updated_utc": host._utc_now_iso(),
            "execution_surface_ownership": {"selected_surface": "scheduler", "updated_utc": host._utc_now_iso()},
            "task_runs": [{"run_id": "sj_host_sickness", "surface": "scheduler", "run_state": "running"}],
            "current_run_id": "sj_host_sickness",
        }
    ]
    task_register._set_generation(register, writer="e5q8.seed")
    task_register._write_json_atomic(task_register._register_path(root), register)
    task_register._write_summary_cache(root, register)


def _active_job_row(*, job_id: str = "host_sickness_job") -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "enabled": True,
        "state": {"active_run_id": "sj_live", "last_status": "running"},
        "due": {"eligible": False, "reason": "active_run"},
    }


def _scenario_active_runtime_storage_critical(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "HOST_SICKNESS_ACTIVE_RUNTIME_STORAGE_CRITICAL_V1"
    with tempfile.TemporaryDirectory(prefix="host_sick_storage_") as td, _stable_context():
        root = Path(td)
        _init_repo(root)
        _seed_active_register(root)
        with _patched_attr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [_active_job_row()]):
            with _patched_attr(watchdog.shutil, "disk_usage", lambda path: __import__("shutil")._ntuple_diskusage(total=10, used=9, free=400 * 1024 * 1024)):
                result = watchdog.run_watchdog(mode="observe", repo_root=root)
        _trace(traces, scenario_id=scenario_id, details={"health": result["health"], "finding_kinds": [f["kind"] for f in result["findings"]]})
        return {
            "scenario_id": scenario_id,
            "pass": result["health"]["reason"] == "runtime_storage_health"
            and any(f["kind"] == "runtime_storage_critical" for f in result["findings"]),
            "observed_health_reason": result["health"]["reason"],
            "observed_finding_kinds": [f["kind"] for f in result["findings"]],
            "bugs_found_and_fixed": [BUG_ACTIVE_BUSY_MASKED_RUNTIME_STORAGE_CRITICAL],
        }


def _scenario_active_stale_lock_recovery(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "HOST_SICKNESS_ACTIVE_STALE_LOCK_RECOVERY_V1"
    with tempfile.TemporaryDirectory(prefix="host_sick_lock_") as td, _stable_context():
        root = Path(td)
        _init_repo(root)
        _seed_active_register(root)
        with _patched_attr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [_active_job_row(job_id="host_lock_job")]):
            with _patched_attr(watchdog, "_classify_locks", lambda repo_root, now: ([{"job_id": "host_lock_job", "path": "/tmp/ghost.lock"}], [])):
                with _patched_attr(watchdog, "_find_orphan_running", lambda repo_root: []):
                    result = watchdog.run_watchdog(mode="observe", repo_root=root)
        _trace(traces, scenario_id=scenario_id, details={"health": result["health"], "finding_kinds": [f["kind"] for f in result["findings"]]})
        return {
            "scenario_id": scenario_id,
            "pass": result["health"]["reason"] == "runtime_recovery_needed"
            and any(f["kind"] == "stale_lock" for f in result["findings"]),
            "observed_health_reason": result["health"]["reason"],
            "observed_finding_kinds": [f["kind"] for f in result["findings"]],
            "bugs_found_and_fixed": [BUG_ACTIVE_BUSY_MASKED_RUNTIME_RECOVERY_NEEDED],
        }


def _scenario_phantom_active_run_pressure(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "HOST_SICKNESS_PHANTOM_ACTIVE_RUN_PRESSURE_V1"
    with tempfile.TemporaryDirectory(prefix="host_sick_phantom_") as td:
        root = Path(td)
        _init_repo(root)
        with _patched_attr(
            sched,
            "list_jobs",
            lambda repo_root=None, now_utc=None: [
                {"job_id": "ghost_waiter", "enabled": True, "state": {"active_run_id": "", "active_claim_present": False}, "due": {"reason": "active_run"}}
            ],
        ):
            with _patched_attr(
                watchdog,
                "run_watchdog",
                lambda *, repo_root=None, mode="observe": {
                    "health": {"health": "degraded", "reason": "runtime_storage_health"},
                    "findings": [{"kind": "runtime_storage_critical"}],
                },
            ):
                truth = host._scheduler_truth_snapshot(root)
        _trace(traces, scenario_id=scenario_id, details=truth)
        return {
            "scenario_id": scenario_id,
            "pass": truth["coherent"] is False
            and truth["blocked_by_active_run_job_ids"] == ["ghost_waiter"]
            and "runtime_storage_critical" in truth["watchdog_finding_kinds"],
            "coherent": bool(truth["coherent"]),
            "blocked_by_active_run_job_ids": list(truth["blocked_by_active_run_job_ids"]),
            "watchdog_finding_kinds": list(truth["watchdog_finding_kinds"]),
            "bugs_found_and_fixed": [BUG_HOST_TRUTH_SNAPSHOT_IGNORED_PHANTOM_ACTIVE_PRESSURE],
        }


def _scenario_clock_jump_active_claim(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "HOST_SICKNESS_CLOCK_JUMP_ACTIVE_CLAIM_V1"
    with tempfile.TemporaryDirectory(prefix="host_sick_clock_") as td, _stable_context():
        root = Path(td)
        _init_repo(root)
        _seed_active_register(root)
        with _patched_attr(sched, "list_jobs", lambda repo_root=None, now_utc=None: [_active_job_row(job_id="clock_jump_job")]):
            with _patched_attr(
                watchdog,
                "_classify_locks",
                lambda repo_root, now: (
                    [
                        {
                            "job_id": "clock_jump_job",
                            "path": "/tmp/future.lock",
                            "lock_snapshot_stale_reason": "future_probe_utc",
                        }
                    ],
                    [],
                ),
            ):
                with _patched_attr(watchdog, "_find_orphan_running", lambda repo_root: []):
                    result = watchdog.run_watchdog(mode="observe", repo_root=root)
        _trace(traces, scenario_id=scenario_id, details={"health": result["health"], "finding_kinds": [f["kind"] for f in result["findings"]]})
        return {
            "scenario_id": scenario_id,
            "pass": result["health"]["reason"] == "runtime_recovery_needed"
            and any(f["kind"] == "stale_lock" for f in result["findings"]),
            "observed_health_reason": result["health"]["reason"],
            "observed_finding_kinds": [f["kind"] for f in result["findings"]],
            "reused_evidence_for": ["PIS-20260413-111003Z-N4Y6"],
        }


def _scenario_partial_write_pressure_active_task_register(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "HOST_SICKNESS_PARTIAL_WRITE_PRESSURE_ACTIVE_TASK_REGISTER_V1"
    with tempfile.TemporaryDirectory(prefix="host_sick_partial_") as td, _stable_context():
        root = Path(td)
        _init_repo(root)
        _seed_active_register(root)
        register_path = root / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
        register_path.write_text("{bad-json\n", encoding="utf-8")
        truth = host._scheduler_truth_snapshot(root)
        _trace(traces, scenario_id=scenario_id, details=truth)
        return {
            "scenario_id": scenario_id,
            "pass": truth["coherent"] is False and "task_register_snapshot_unreadable" in truth["watchdog_finding_kinds"],
            "coherent": bool(truth["coherent"]),
            "watchdog_finding_kinds": list(truth["watchdog_finding_kinds"]),
            "reused_evidence_for": ["PIS-20260413-111003Z-N4Y6"],
        }


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    scenarios = [
        _scenario_active_runtime_storage_critical(traces),
        _scenario_active_stale_lock_recovery(traces),
        _scenario_phantom_active_run_pressure(traces),
        _scenario_clock_jump_active_claim(traces),
        _scenario_partial_write_pressure_active_task_register(traces),
    ]
    pass_count = sum(1 for row in scenarios if bool(row.get("pass")))
    bugs: List[str] = []
    for row in scenarios:
        for bug in list(row.get("bugs_found_and_fixed") or []):
            if bug not in bugs:
                bugs.append(str(bug))
    matrix = {
        "schema_version": SCHEMA_VERSION,
        "pis_uid": "PIS-20260413-110629Z-E5Q8",
        "scenario_count": len(scenarios),
        "pass_count": pass_count,
        "fail_count": len(scenarios) - pass_count,
        "bugs_found_and_fixed": bugs,
        "scenarios": scenarios,
    }
    residuals = {
        "schema_version": SCHEMA_VERSION,
        "pis_uid": "PIS-20260413-110629Z-E5Q8",
        "residual_claims": [
            "This slice is harsh on active host sickness correctness, but it is still a bounded local abuse harness rather than a multi-hour real host degradation run.",
            "Clock-jump and partial-write scenarios are coupled evidence for restore/recovery work and must not be treated as independent confirmation for N4Y6.",
        ],
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "host_sickness_matrix.json", matrix)
    _write_json(out_dir / "host_continuity_residuals.json", residuals)
    _write_jsonl(out_dir / "host_truth_trace.jsonl", traces)
    return {
        "ok": pass_count == len(scenarios),
        "scenario_count": len(scenarios),
        "fail_count": len(scenarios) - pass_count,
        "bugs_found_and_fixed": bugs,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run active host sickness correctness abuse validation.")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    result = run_validation(out_dir=Path(args.out_dir).resolve())
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
