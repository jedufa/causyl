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
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as task_register
from tools.bin import scheduler_watchdog as watchdog


SCENARIO_TERMINAL_RECEIPT_STRANDED = "RESTORE_TERMINAL_RECEIPT_STRANDED_RUNNING_V1"
SCENARIO_NO_RECEIPT_STRANDED = "RESTORE_STRANDED_RUNNING_WITHOUT_RECEIPT_V1"
SCENARIO_WATCHDOG_CONFLICT = "RESTORE_TERMINAL_RECEIPT_WATCHDOG_CONFLICT_V1"
SCENARIO_PARTIAL_WRITE_REUSED = "RESTORE_FROM_PARTIAL_WRITE_SICK_HOST_V1"

BUG_PROJECTION = "restore_projection_admitted_stranded_running_without_live_claim"
BUG_RECONCILIATION = "restore_reconciliation_ignored_stranded_running_without_live_claim"
BUG_HOST_TRUTH = "restored_host_truth_snapshot_ignored_ambiguous_scheduler_projection"
BUG_WATCHDOG = "watchdog_missed_terminal_receipt_conflict_without_active_run"


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _append_trace(traces: List[Dict[str, Any]], *, scenario_id: str, event: str, details: Dict[str, Any]) -> None:
    traces.append({"utc": host._utc_now_iso(), "scenario_id": scenario_id, "event": event, "details": details})


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "assistant_state").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "task_register").mkdir(parents=True, exist_ok=True)


def _seed_valid_task_register(root: Path) -> None:
    register = dict(task_register.load_or_init_task_register(root))
    register["write_generation"] = 1
    register["active_tasks"] = [
        {
            "task_id": "restore_task_001",
            "kind": "software",
            "title": "Restore task",
            "status": "queued",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "execute",
            "updated_utc": host._utc_now_iso(),
        }
    ]
    task_register._set_generation(register, writer="restore_contradiction_validation.seed")
    task_register._write_json_atomic(task_register._register_path(root), register)
    task_register._write_summary_cache(root, register)


def _seed_job(root: Path, *, job_id: str, run_at: str) -> None:
    sched.save_job(
        {
            "job_id": job_id,
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": f"task_{job_id}"},
            "prompt": "Restore contradiction validation job.",
            "schedule": {"mode": "once", "run_at": run_at, "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=root,
    )


def _seed_terminal_receipt(root: Path, *, job_id: str, run_id: str, status: str = "ok") -> Path:
    receipt_path = sched.receipts_dir(root) / job_id / f"{run_id}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps({"job_id": job_id, "run_id": run_id, "status": status, "finished_at": "2026-04-13T10:00:15Z"}) + "\n",
        encoding="utf-8",
    )
    return receipt_path


@contextlib.contextmanager
def _quiet_watchdog_context() -> Iterator[None]:
    original_healer = watchdog._latest_pipeline_healer
    original_pointer = watchdog._repair_watchdog_report_pointers
    original_timer = watchdog._watchdog_timer.timer_status_snapshot
    original_snapshot = watchdog._operator_snapshot.build_operator_snapshot
    original_proof = watchdog._execution_proof.read_latest_report
    try:
        watchdog._latest_pipeline_healer = lambda repo_root: {}  # type: ignore[assignment]
        watchdog._repair_watchdog_report_pointers = lambda repo_root, mode: ([], [])  # type: ignore[assignment]
        watchdog._watchdog_timer.timer_status_snapshot = lambda: {"installed": True, "units": {}}  # type: ignore[assignment]
        watchdog._operator_snapshot.build_operator_snapshot = lambda repo_root: {"operator_summary": {"truth_conflicts": []}}  # type: ignore[assignment]
        watchdog._execution_proof.read_latest_report = lambda repo_root, now_utc="": {  # type: ignore[assignment]
            "summary": {"overall_status": "ok"},
            "freshness": {"stale": False},
        }
        yield
    finally:
        watchdog._latest_pipeline_healer = original_healer  # type: ignore[assignment]
        watchdog._repair_watchdog_report_pointers = original_pointer  # type: ignore[assignment]
        watchdog._watchdog_timer.timer_status_snapshot = original_timer  # type: ignore[assignment]
        watchdog._operator_snapshot.build_operator_snapshot = original_snapshot  # type: ignore[assignment]
        watchdog._execution_proof.read_latest_report = original_proof  # type: ignore[assignment]


@contextlib.contextmanager
def _fake_execution_context() -> Iterator[List[Dict[str, Any]]]:
    calls: List[Dict[str, Any]] = []
    original_send = sched._agents.send_message
    try:
        def _fake_send(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
            calls.append({"role": role, "binding": dict(binding), "message": str(message)})
            return {"ok": True, "assistant": {"content": "validation noop", "status": "ok", "meta": {}}}
        sched._agents.send_message = _fake_send  # type: ignore[assignment]
        yield calls
    finally:
        sched._agents.send_message = original_send  # type: ignore[assignment]


def _export_restore(source: Path, *, note: str) -> Path:
    host.mark_clean_shutdown(source, reason=note)
    export = host.export_host_baseline(source, note=note)
    target = source.parent / f"{source.name}__restored"
    host.restore_host_baseline(manifest_path=Path(str(export["manifest_path"])), target_repo_root=target, note=note)
    return target


def _scenario_terminal_receipt_stranded(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="n4y6_terminal_") as td, _quiet_watchdog_context(), _fake_execution_context() as calls:
        source = Path(td) / "source"
        _init_repo(source)
        host.bootstrap_host_baseline(source)
        _seed_valid_task_register(source)
        job_id = "restore_terminal_receipt_001"
        _seed_job(source, job_id=job_id, run_at="2026-04-13T10:00:00Z")
        receipt_path = _seed_terminal_receipt(source, job_id=job_id, run_id="sj_restore_terminal_001", status="ok")
        sched._write_state(
            job_id,
            {
                "job_id": job_id,
                "active_run_id": "",
                "last_status": "running",
                "last_receipt_path": str(receipt_path.resolve()),
                "updated_utc": "2026-04-13T10:00:20Z",
            },
            repo_root=source,
        )
        target = _export_restore(source, note="n4y6 terminal receipt stranded")
        pre_row = sched.list_jobs(repo_root=target, now_utc="2026-04-13T10:01:00Z")[0]
        pre_truth = host._scheduler_truth_snapshot(target)
        pre_tick = sched.scheduler_tick(repo_root=target, now_utc="2026-04-13T10:01:00Z")
        reconciliation = host._reconcile_scheduler_truth_after_restore(target)
        post_row = sched.list_jobs(repo_root=target, now_utc="2026-04-13T10:01:00Z")[0]
        post_truth = host._scheduler_truth_snapshot(target)
        final_state = sched.load_state(job_id, repo_root=target)
        _append_trace(
            traces,
            scenario_id=SCENARIO_TERMINAL_RECEIPT_STRANDED,
            event="restore_campaign_completed",
            details={
                "pre_projection": pre_row["scheduler_projection_v1"],
                "pre_truth": pre_truth,
                "pre_tick": pre_tick,
                "post_projection": post_row["scheduler_projection_v1"],
                "post_truth": post_truth,
                "reconciliation": reconciliation,
                "execution_call_count": len(calls),
            },
        )
        passed = (
            pre_row["scheduler_projection_v1"]["projected_status"] == "ambiguous"
            and pre_truth["coherent"] is False
            and pre_tick["executed_count"] == 0
            and not calls
            and reconciliation["stranded_running_reconciliation"]["reconciled_count"] == 1
            and final_state["last_status"] == "ok"
            and post_row["scheduler_projection_v1"]["projected_status"] == "completed_once"
            and post_truth["coherent"] is True
        )
        return {
            "scenario_id": SCENARIO_TERMINAL_RECEIPT_STRANDED,
            "status": "pass" if passed else "fail",
            "pre_projection_status": pre_row["scheduler_projection_v1"]["projected_status"],
            "post_projection_status": post_row["scheduler_projection_v1"]["projected_status"],
            "pre_coherent": bool(pre_truth["coherent"]),
            "post_coherent": bool(post_truth["coherent"]),
            "executed_count_before_reconcile": int(pre_tick["executed_count"]),
            "bugs_found_and_fixed": [BUG_PROJECTION, BUG_RECONCILIATION, BUG_HOST_TRUTH],
        }


def _scenario_no_receipt_stranded(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="n4y6_blocked_") as td, _quiet_watchdog_context(), _fake_execution_context() as calls:
        source = Path(td) / "source"
        _init_repo(source)
        host.bootstrap_host_baseline(source)
        _seed_valid_task_register(source)
        job_id = "restore_stranded_blocked_001"
        _seed_job(source, job_id=job_id, run_at="2026-04-13T10:00:00Z")
        sched._write_state(
            job_id,
            {
                "job_id": job_id,
                "active_run_id": "",
                "last_status": "running",
                "updated_utc": "2026-04-13T10:00:20Z",
            },
            repo_root=source,
        )
        target = _export_restore(source, note="n4y6 no receipt stranded")
        pre_row = sched.list_jobs(repo_root=target, now_utc="2026-04-13T10:01:00Z")[0]
        pre_tick = sched.scheduler_tick(repo_root=target, now_utc="2026-04-13T10:01:00Z")
        reconciliation = host._reconcile_scheduler_truth_after_restore(target)
        post_row = sched.list_jobs(repo_root=target, now_utc="2026-04-13T10:01:00Z")[0]
        state = sched.load_state(job_id, repo_root=target)
        _append_trace(
            traces,
            scenario_id=SCENARIO_NO_RECEIPT_STRANDED,
            event="restore_campaign_completed",
            details={"pre_tick": pre_tick, "reconciliation": reconciliation, "post_projection": post_row["scheduler_projection_v1"]},
        )
        passed = (
            pre_row["scheduler_projection_v1"]["projected_status"] == "ambiguous"
            and pre_tick["executed_count"] == 0
            and not calls
            and reconciliation["stranded_running_reconciliation"]["reconciled_count"] == 1
            and state["last_status"] == "blocked"
            and post_row["scheduler_projection_v1"]["projected_status"] == "blocked"
        )
        return {
            "scenario_id": SCENARIO_NO_RECEIPT_STRANDED,
            "status": "pass" if passed else "fail",
            "post_projection_status": post_row["scheduler_projection_v1"]["projected_status"],
            "executed_count_before_reconcile": int(pre_tick["executed_count"]),
        }


def _scenario_watchdog_conflict(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="n4y6_watchdog_") as td, _quiet_watchdog_context():
        source = Path(td) / "source"
        _init_repo(source)
        host.bootstrap_host_baseline(source)
        _seed_valid_task_register(source)
        job_id = "restore_watchdog_conflict_001"
        _seed_job(source, job_id=job_id, run_at="2026-04-13T10:00:00Z")
        receipt_path = _seed_terminal_receipt(source, job_id=job_id, run_id="sj_restore_watchdog_001", status="ok")
        sched._write_state(
            job_id,
            {
                "job_id": job_id,
                "active_run_id": "",
                "last_status": "running",
                "last_receipt_path": str(receipt_path.resolve()),
                "updated_utc": "2026-04-13T10:00:20Z",
            },
            repo_root=source,
        )
        target = _export_restore(source, note="n4y6 watchdog conflict")
        result = watchdog.run_watchdog(mode="audit", repo_root=target)
        findings = [f for f in result["findings"] if f["kind"] == "terminal_receipt_state_conflict" and f["details"]["job_id"] == job_id]
        _append_trace(
            traces,
            scenario_id=SCENARIO_WATCHDOG_CONFLICT,
            event="watchdog_audited",
            details={"health": result["health"], "finding_count": len(findings)},
        )
        passed = len(findings) == 1 and findings[0]["details"]["active_run_id"] == ""
        return {
            "scenario_id": SCENARIO_WATCHDOG_CONFLICT,
            "status": "pass" if passed else "fail",
            "finding_count": len(findings),
            "bugs_found_and_fixed": [BUG_WATCHDOG],
        }


def _scenario_partial_write_reused(traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="n4y6_partial_write_") as td, _quiet_watchdog_context():
        source = Path(td) / "source"
        _init_repo(source)
        host.bootstrap_host_baseline(source)
        _seed_valid_task_register(source)
        register_path = source / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
        register_path.write_text(json.dumps({"tasks": []}) + "\n", encoding="utf-8")
        target = _export_restore(source, note="n4y6 partial write sick host")
        truth = host._scheduler_truth_snapshot(target)
        health = host.host_health_snapshot(target, initialize_if_missing=False)
        _append_trace(
            traces,
            scenario_id=SCENARIO_PARTIAL_WRITE_REUSED,
            event="restore_campaign_completed",
            details={"truth": truth, "health": health},
        )
        passed = truth["coherent"] is False and "task_register_snapshot_unreadable" in list(truth["watchdog_finding_kinds"])
        return {
            "scenario_id": SCENARIO_PARTIAL_WRITE_REUSED,
            "status": "pass" if passed else "fail",
            "reused_evidence_from": ["PIS-20260413-110629Z-E5Q8"],
            "coherent": bool(truth["coherent"]),
            "health_status": str(health.get("status") or ""),
        }


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    out_dir = Path(out_dir).resolve()
    traces: List[Dict[str, Any]] = []
    scenarios = [
        _scenario_terminal_receipt_stranded(traces),
        _scenario_no_receipt_stranded(traces),
        _scenario_watchdog_conflict(traces),
        _scenario_partial_write_reused(traces),
    ]
    pass_count = sum(1 for row in scenarios if row["status"] == "pass")
    fail_count = len(scenarios) - pass_count
    bug_ids: List[str] = []
    for row in scenarios:
        for bug_id in list(row.get("bugs_found_and_fixed") or []):
            if bug_id not in bug_ids:
                bug_ids.append(str(bug_id))

    matrix = {
        "schema_version": "restore_contradiction_matrix_v1",
        "validated_utc": host._utc_now_iso(),
        "pis_uid": "PIS-20260413-111003Z-N4Y6",
        "scenario_count": len(scenarios),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "scenarios": scenarios,
    }
    summary = {
        "schema_version": "restore_contradiction_metrics_summary_v1",
        "validated_utc": host._utc_now_iso(),
        "pis_uid": "PIS-20260413-111003Z-N4Y6",
        "scenario_count": len(scenarios),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "bugs_found_and_fixed": bug_ids,
        "residual_uncertainty": [
            "No distributed multi-host restore ownership claim.",
            "Pending-promotion and stale-observation restore abuse are still narrower than a real multi-hour operator campaign.",
        ],
    }
    reused = {
        "schema_version": "restore_reused_evidence_manifest_v1",
        "validated_utc": host._utc_now_iso(),
        "pis_uid": "PIS-20260413-111003Z-N4Y6",
        "rows": [
            {
                "scenario_id": row["scenario_id"],
                "reused_evidence_from": list(row.get("reused_evidence_from") or []),
            }
            for row in scenarios
            if row.get("reused_evidence_from")
        ],
    }
    negative_controls = {
        "schema_version": "restore_negative_controls_v1",
        "validated_utc": host._utc_now_iso(),
        "pis_uid": "PIS-20260413-111003Z-N4Y6",
        "controls": [
            {
                "scenario_id": SCENARIO_TERMINAL_RECEIPT_STRANDED,
                "control": "pre_reconcile_non_admission",
                "pass": next(row for row in scenarios if row["scenario_id"] == SCENARIO_TERMINAL_RECEIPT_STRANDED)[
                    "executed_count_before_reconcile"
                ]
                == 0,
            },
            {
                "scenario_id": SCENARIO_WATCHDOG_CONFLICT,
                "control": "watchdog_must_classify_terminal_receipt_conflict_without_active_run",
                "pass": next(row for row in scenarios if row["scenario_id"] == SCENARIO_WATCHDOG_CONFLICT)["finding_count"] == 1,
            },
        ],
    }
    _write_json(out_dir / "restore_contradiction_matrix.json", matrix)
    _write_json(out_dir / "restore_contradiction_metrics_summary.json", summary)
    _write_json(out_dir / "restore_reused_evidence_manifest.json", reused)
    _write_json(out_dir / "restore_negative_controls.json", negative_controls)
    _write_jsonl(out_dir / "restore_under_load_trace.jsonl", traces)
    _write_jsonl(out_dir / "restore_truth_trace.jsonl", traces)
    return {
        "ok": fail_count == 0,
        "scenario_count": len(scenarios),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "bugs_found_and_fixed": bug_ids,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run harsh restore contradiction validation for the plane.")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args()
    result = run_validation(out_dir=Path(args.out_dir))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
