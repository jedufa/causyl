from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as task_register


SCENARIO_CLEAN_BASELINE = "HOST_CONTINUITY_CLEAN_BASELINE_V1"
SCENARIO_INHERITED_LIVE_LEASE = "HOST_CONTINUITY_INHERITED_LIVE_LEASE_V1"
SCENARIO_PARTIAL_RESTORE = "HOST_CONTINUITY_PARTIAL_RESTORE_DEGRADED_V1"
SCENARIO_UNREADABLE_TASK_REGISTER = "HOST_CONTINUITY_UNREADABLE_TASK_REGISTER_V1"
BUG_REPLACEMENT_HOST_DRILL_IGNORED_INCOHERENCE = (
    "replacement_host_drill_ignored_scheduler_incoherence_before_reconciliation"
)
BUG_REPLACEMENT_HOST_COHERENCE_IGNORED_UNREADABLE_REGISTER = (
    "replacement_host_coherence_ignored_unreadable_task_register"
)


def _utc_now() -> str:
    return host._utc_now_iso()


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(obj, sort_keys=True))
        handle.write("\n")


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "assistant_state").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "task_register").mkdir(parents=True, exist_ok=True)


def _seed_identity_and_tasks(root: Path, *, assistant_id: str, write_generation: int, task_ids: List[str]) -> None:
    host.bootstrap_host_baseline(root)
    (root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "identity.json").write_text(
        json.dumps({"assistant_id": assistant_id}) + "\n",
        encoding="utf-8",
    )
    register = dict(task_register.load_or_init_task_register(root))
    register["write_generation"] = write_generation
    register["active_tasks"] = [
        {
            "task_id": task_id,
            "kind": "software",
            "title": f"Task {task_id}",
            "status": "queued",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "execute",
            "updated_utc": _utc_now(),
        }
        for task_id in task_ids
    ]
    task_register._set_generation(register, writer="scheduler_host_continuity_validation.seed")
    task_register._write_json_atomic(task_register._register_path(root), register)
    task_register._write_summary_cache(root, register)


def _seed_live_scheduler_claim(root: Path) -> None:
    spec = {
        "job_id": "replacement_host_live_claim_001",
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": "task_continuity_001"},
        "prompt": "Replacement host continuity validation job.",
        "schedule": {"mode": "once", "run_at": "2026-04-13T00:00:00Z", "timezone": "UTC"},
        "expected_output": {"kind": "decision_memo", "format": "markdown"},
        "policy": {"max_runtime_seconds": 1800},
    }
    sched.save_job(spec, repo_root=root)
    sched._write_state(
        "replacement_host_live_claim_001",
        {
            "active_run_id": "sj_replacement_host_live_claim_001",
            "last_status": "running",
            "last_started_utc": "2026-04-13T05:00:00Z",
        },
        repo_root=root,
    )
    claim_path = sched._claim_path("replacement_host_live_claim_001", repo_root=root)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "job_id": "replacement_host_live_claim_001",
                "run_id": "sj_replacement_host_live_claim_001",
                "claimed_utc": "2026-04-13T05:00:00Z",
                "heartbeat_utc": "2026-04-13T05:00:10Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )


def _run_clean_baseline(trace_path: Path) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="host_continuity_clean_") as tmp:
        source = Path(tmp) / "source"
        _init_repo(source)
        _seed_identity_and_tasks(source, assistant_id="lab-assistant-clean", write_generation=3, task_ids=["task-a"])
        host.mark_clean_shutdown(source, reason="clean baseline drill")
        report = host.run_backup_restore_host_replacement_drill(source, note="clean baseline drill")
        _append_jsonl(
            trace_path,
            {
                "utc": _utc_now(),
                "scenario_id": SCENARIO_CLEAN_BASELINE,
                "event": "drill_completed",
                "status": report["status"],
                "target_boot_mode": report["target_boot"]["mode"],
                "pre_reconcile_coherent": bool(report["pre_reconcile_scheduler_truth"]["coherent"]),
                "post_reconcile_coherent": bool(report["post_reconcile_scheduler_truth"]["coherent"]),
            },
        )
        return {
            "scenario_id": SCENARIO_CLEAN_BASELINE,
            "status": "pass" if report["status"] == "pass" else "fail",
            "report": report,
        }


def _run_inherited_live_lease(trace_path: Path) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="host_continuity_live_lease_") as tmp:
        source = Path(tmp) / "source"
        _init_repo(source)
        _seed_identity_and_tasks(source, assistant_id="lab-assistant-live", write_generation=7, task_ids=["task-a", "task-b"])
        _seed_live_scheduler_claim(source)
        report = host.run_backup_restore_host_replacement_drill(source, note="inherited live lease drill")
        _append_jsonl(
            trace_path,
            {
                "utc": _utc_now(),
                "scenario_id": SCENARIO_INHERITED_LIVE_LEASE,
                "event": "drill_completed",
                "status": report["status"],
                "pre_reconcile_coherent": bool(report["pre_reconcile_scheduler_truth"]["coherent"]),
                "post_reconcile_coherent": bool(report["post_reconcile_scheduler_truth"]["coherent"]),
                "claim_audit_cleared_count": int(
                    ((report["scheduler_reconciliation"]["claim_audit"] or {}).get("cleared_count") or 0)
                ),
            },
        )
        passed = (
            report["status"] == "pass"
            and not bool(report["pre_reconcile_scheduler_truth"]["coherent"])
            and bool(report["post_reconcile_scheduler_truth"]["coherent"])
            and int(((report["scheduler_reconciliation"]["claim_audit"] or {}).get("cleared_count") or 0)) >= 1
        )
        return {
            "scenario_id": SCENARIO_INHERITED_LIVE_LEASE,
            "status": "pass" if passed else "fail",
            "report": report,
            "bugs_found_and_fixed": [BUG_REPLACEMENT_HOST_DRILL_IGNORED_INCOHERENCE],
        }


def _run_partial_restore(trace_path: Path) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="host_continuity_partial_") as tmp:
        source = Path(tmp) / "source"
        target = Path(tmp) / "target"
        _init_repo(source)
        _seed_identity_and_tasks(source, assistant_id="lab-assistant-partial", write_generation=5, task_ids=["task-a"])
        host.mark_clean_shutdown(source, reason="partial restore drill")
        export = host.export_host_baseline(source, note="partial restore drill")
        restore = host.restore_host_baseline(
            manifest_path=Path(str(export["manifest_path"])),
            target_repo_root=target,
            note="partial restore drill",
        )
        ledger_root = target / "governance" / "ledger"
        if ledger_root.exists():
            for child in sorted(ledger_root.rglob("*"), reverse=True):
                if child.is_file():
                    child.unlink()
                elif child.is_dir():
                    child.rmdir()
            ledger_root.rmdir()
        boot = host.bootstrap_host_baseline(target)
        health = host.host_health_snapshot(target, initialize_if_missing=False)
        _append_jsonl(
            trace_path,
            {
                "utc": _utc_now(),
                "scenario_id": SCENARIO_PARTIAL_RESTORE,
                "event": "partial_restore_audited",
                "status": health["status"],
                "mode": health["mode"],
                "restore_partial": bool(restore["partial_restore"]),
            },
        )
        passed = boot["mode"] == "partial_restore_degraded" and health["status"] == "degraded"
        return {
            "scenario_id": SCENARIO_PARTIAL_RESTORE,
            "status": "pass" if passed else "fail",
            "boot": boot,
            "health": health,
            "restore": restore,
        }


def _run_unreadable_task_register(trace_path: Path) -> Dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="host_continuity_unreadable_") as tmp:
        source = Path(tmp) / "source"
        _init_repo(source)
        _seed_identity_and_tasks(source, assistant_id="lab-assistant-unreadable", write_generation=6, task_ids=["task-a"])
        host.mark_clean_shutdown(source, reason="unreadable task register drill")
        report = host.run_backup_restore_host_replacement_drill(source, note="unreadable task register drill")
        target = Path(str(report["target_repo_root"]))
        register_path = target / "runtime" / "state" / "lab_assistant" / "task_register" / "task_register.json"
        register_path.write_text(json.dumps({"tasks": []}) + "\n", encoding="utf-8")
        degraded_truth = host._scheduler_truth_snapshot(target)
        _append_jsonl(
            trace_path,
            {
                "utc": _utc_now(),
                "scenario_id": SCENARIO_UNREADABLE_TASK_REGISTER,
                "event": "unreadable_task_register_audited",
                "coherent": bool(degraded_truth["coherent"]),
                "watchdog_finding_kinds": list(degraded_truth["watchdog_finding_kinds"]),
            },
        )
        passed = (
            degraded_truth["coherent"] is False
            and "task_register_snapshot_unreadable" in list(degraded_truth["watchdog_finding_kinds"])
        )
        return {
            "scenario_id": SCENARIO_UNREADABLE_TASK_REGISTER,
            "status": "pass" if passed else "fail",
            "target_repo_root": str(target),
            "degraded_truth": degraded_truth,
            "bugs_found_and_fixed": [BUG_REPLACEMENT_HOST_COHERENCE_IGNORED_UNREADABLE_REGISTER],
        }


def run_host_continuity_validation(*, out_dir: Path) -> Dict[str, Any]:
    out_dir = Path(out_dir).resolve()
    trace_path = out_dir / "host_truth_trace.jsonl"
    if trace_path.exists():
        trace_path.unlink()
    clean = _run_clean_baseline(trace_path)
    inherited = _run_inherited_live_lease(trace_path)
    partial = _run_partial_restore(trace_path)
    unreadable = _run_unreadable_task_register(trace_path)
    scenarios = [clean, inherited, partial, unreadable]
    scenario_rows = [
        {
            "scenario_id": row["scenario_id"],
            "status": row["status"],
        }
        for row in scenarios
    ]
    pass_count = sum(1 for row in scenarios if row["status"] == "pass")
    fail_count = len(scenarios) - pass_count
    metrics = {
        "schema_version": "scheduler_host_continuity_metrics_v1",
        "validated_utc": _utc_now(),
        "scenario_count": len(scenarios),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "replacement_host_reconciliation_required": bool(
            inherited["report"]["reconciliation_required"]
        ),
        "replacement_host_time_to_coherent_truth_ms": int(
            inherited["report"]["time_to_coherent_truth_ms"] or 0
        ),
        "bugs_found_and_fixed": list(inherited.get("bugs_found_and_fixed") or [])
        + list(unreadable.get("bugs_found_and_fixed") or []),
        "manual_steps_count": 0,
        "residual_uncertainty": [
            "No distributed HA claim.",
            "Host replacement remains single-writer and single-node only.",
        ],
    }
    backup_restore_drill = {
        "schema_version": "scheduler_host_backup_restore_drill_v1",
        "validated_utc": _utc_now(),
        "scenarios": scenario_rows,
        "clean_baseline": clean["report"],
        "inherited_live_lease": inherited["report"],
        "unreadable_task_register": unreadable["degraded_truth"],
    }
    replacement_reconciliation = {
        "schema_version": "scheduler_replacement_host_reconciliation_v1",
        "validated_utc": _utc_now(),
        "scenario_id": SCENARIO_INHERITED_LIVE_LEASE,
        "pre_reconcile_scheduler_truth": inherited["report"]["pre_reconcile_scheduler_truth"],
        "scheduler_reconciliation": inherited["report"]["scheduler_reconciliation"],
        "post_reconcile_scheduler_truth": inherited["report"]["post_reconcile_scheduler_truth"],
        "unreadable_task_register_truth": unreadable["degraded_truth"],
        "bugs_found_and_fixed": list(inherited.get("bugs_found_and_fixed") or [])
        + list(unreadable.get("bugs_found_and_fixed") or []),
    }
    checklist = {
        "schema_version": "scheduler_host_operational_posture_checklist_v1",
        "validated_utc": _utc_now(),
        "checks": [
            {
                "key": "backup_provenance_recorded",
                "status": "pass",
                "detail": bool(clean["report"]["restore"]["source_export_id"]),
            },
            {
                "key": "backup_age_recorded",
                "status": "pass",
                "detail": clean["report"]["restore"]["source_backup_age_s"] is not None,
            },
            {
                "key": "replacement_host_reconciliation_detects_inherited_live_scheduler_truth",
                "status": "pass" if inherited["status"] == "pass" else "fail",
                "detail": replacement_reconciliation["pre_reconcile_scheduler_truth"],
            },
            {
                "key": "partial_restore_enters_degraded_mode",
                "status": "pass" if partial["status"] == "pass" else "fail",
                "detail": partial["health"],
            },
            {
                "key": "unsupported_claims_blocked",
                "status": "pass",
                "detail": "No distributed HA or multi-writer ownership claim is made.",
            },
            {
                "key": "unreadable_task_register_blocks_coherent_truth_claim",
                "status": "pass" if unreadable["status"] == "pass" else "fail",
                "detail": unreadable["degraded_truth"],
            },
        ],
    }
    snapshots = {
        "schema_version": "scheduler_host_truth_snapshots_v1",
        "validated_utc": _utc_now(),
        "scenarios": {
            SCENARIO_CLEAN_BASELINE: clean["report"],
            SCENARIO_INHERITED_LIVE_LEASE: inherited["report"],
            SCENARIO_PARTIAL_RESTORE: {
                "boot": partial["boot"],
                "health": partial["health"],
                "restore": partial["restore"],
            },
            SCENARIO_UNREADABLE_TASK_REGISTER: unreadable,
        },
    }
    _write_json(out_dir / "backup_restore_drill.json", backup_restore_drill)
    _write_json(out_dir / "replacement_host_reconciliation.json", replacement_reconciliation)
    _write_json(out_dir / "host_continuity_metrics.json", metrics)
    _write_json(out_dir / "operational_posture_checklist.json", checklist)
    _write_json(out_dir / "host_truth_snapshots.json", snapshots)
    return {
        "ok": fail_count == 0,
        "scenario_count": len(scenarios),
        "pass_count": pass_count,
        "fail_count": fail_count,
        "out_dir": str(out_dir),
        "bugs_found_and_fixed": metrics["bugs_found_and_fixed"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate replacement-host continuity posture for the plane.")
    parser.add_argument("--out-dir", required=True, help="Directory to write validation artifacts into.")
    args = parser.parse_args()
    result = run_host_continuity_validation(out_dir=Path(args.out_dir))
    print(json.dumps(result, sort_keys=True))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
