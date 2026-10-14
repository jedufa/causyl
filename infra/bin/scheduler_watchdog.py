#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import datetime as dt
import json
import os
import shutil
import sys
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

repo_root = Path(__file__).resolve().parents[2]
if str(repo_root) not in sys.path:
    sys.path.insert(0, str(repo_root))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import surfaces as sched_v2_surfaces
from infra.controlplane import server_maintenance as maint
from tools.bin import scheduler_pipeline_healer as healer
from tools.bin import scheduler_watchdog_contract as _watchdog_contract
from tools.bin import scheduler_watchdog_timer as _watchdog_timer
from tools.git.support import commit_journal as _commit_journal
from tools import runtime_policy as _runtime_policy
from infra.webui import server_agents as _agents
from infra.webui import server_lab_assistant_execution_proof as _execution_proof
from infra.webui import server_lab_assistant_operator_snapshot as _operator_snapshot
from infra.webui import server_lab_assistant_recovery as _lab_recovery
from infra.webui import server_lab_assistant_task_register as _lab_task_register
from infra.webui import server_lab_assistant_turns as _lab_turns

_REPAIRABLE_PLANE_TRUTH_CONFLICT_KINDS = {
    "stale_task_register_with_open_work",
    "recovery_not_settled_with_only_completed_tasks",
}
_PROGRESS_RECOVERY_IDLE_MINUTES = 5
_PROGRESS_RECOVERY_MAX_HEADS = 1
_WATCHDOG_REPEAT_JOB_PROBLEM_THRESHOLD = 3
_WATCHDOG_TERMINAL_SUCCESS_RECEIPT_STATUSES = {
    "ok",
    "conditional_pass_repair_applied",
    "succeeded",
    "verified",
}
_WATCHDOG_TERMINAL_DEPENDENCY_BLOCK_STATUSES = {
    "blocked",
    "canceled",
    "cancelled",
    "error",
    "failed",
    "failed_verification",
    "proof_gap",
    "stopped",
    "timed_out",
}
_WATCHDOG_SCHEDULER_MODE_ENV = "EIEL_WATCHDOG_SCHEDULER_MODE"
_WATCHDOG_V2_PARENT_UID_ENV = "EIEL_WATCHDOG_SCHED_V2_PARENT_UID"
_WATCHDOG_SCHEDULER_MODE_POLICY_KEY = "scheduler.watchdog.backend"
_SCHED_V2_OWNER_MODE_POLICY_KEY = "scheduler.v2.owner_mode"
_SCHED_V2_PARENT_UID_POLICY_KEY = "scheduler.v2.parent_uid"
_REAL_REPO_ROOT = repo_root.resolve()


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0)


def _utc_iso(ts: dt.datetime | None = None) -> str:
    return (ts or _utc_now()).astimezone(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _vienna_iso(ts: dt.datetime | None = None) -> str:
    try:
        from zoneinfo import ZoneInfo

        return (ts or _utc_now()).astimezone(ZoneInfo("Europe/Vienna")).replace(microsecond=0).isoformat()
    except Exception:
        return _utc_iso(ts)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _normalize_watchdog_scheduler_mode(raw: Any) -> str:
    mode = str(raw or "").strip().lower() or "v2"
    if mode in {"legacy", "v1", "scheduled_jobs"}:
        return "legacy"
    if mode in {"v2", "sched_v2", "intent_family_v2"}:
        return "v2"
    if mode == "auto":
        return "auto"
    raise ValueError(f"unsupported watchdog scheduler mode: {mode}")


def _watchdog_scheduler_mode(raw: Any | None = None) -> str:
    return _normalize_watchdog_scheduler_mode(
        raw
        if raw is not None
        else os.environ.get(_WATCHDOG_SCHEDULER_MODE_ENV)
        or _runtime_policy.get_runtime_policy_text(_WATCHDOG_SCHEDULER_MODE_POLICY_KEY, "v2")
    )


def _watchdog_v2_parent_uid(raw: Any | None = None, *, repo_root: Path | None = None) -> str:
    if raw is not None:
        return str(raw or "").strip()
    env_uid = str(os.environ.get(_WATCHDOG_V2_PARENT_UID_ENV) or "").strip()
    if env_uid:
        return env_uid
    if Path(repo_root or _REAL_REPO_ROOT).resolve() != _REAL_REPO_ROOT:
        return ""
    return str(_runtime_policy.get_runtime_policy_text(_SCHED_V2_PARENT_UID_POLICY_KEY, "")).strip()


def _sched_v2_owner_mode() -> str:
    return str(
        os.environ.get("EIEL_SCHED_V2_OWNER_MODE")
        or _runtime_policy.get_runtime_policy_text(_SCHED_V2_OWNER_MODE_POLICY_KEY, "v2_primary")
    ).strip()


def _watchdog_reports_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "reports" / "scheduler" / "watchdog").resolve()


def _watchdog_escalation_root(repo_root: Path) -> Path:
    return (_watchdog_reports_root(repo_root) / "escalations").resolve()


def _watchdog_scope(*, scheduler_backend: str = "", parent_uid: str = "", binding: Dict[str, Any] | None = None) -> Dict[str, Any]:
    normalized_backend = str(scheduler_backend or "").strip().lower() or "legacy"
    normalized_parent_uid = str(parent_uid or "").strip()
    normalized_binding = dict(binding or {})
    binding_type = str(normalized_binding.get("type") or "").strip()
    binding_id = str(normalized_binding.get("id") or "").strip()
    if normalized_parent_uid:
        return {
            "scope_kind": "family_parent",
            "scheduler_backend": normalized_backend,
            "parent_uid": normalized_parent_uid,
            "binding": {"type": "ledger_uid", "id": normalized_parent_uid},
        }
    if binding_type and binding_id:
        return {
            "scope_kind": "binding",
            "scheduler_backend": normalized_backend,
            "binding": {"type": binding_type, "id": binding_id},
        }
    return {
        "scope_kind": "global",
        "scheduler_backend": normalized_backend,
        "binding": {"type": "task_id", "id": "wdg"},
    }


def _watchdog_scope_slug(scope: Dict[str, Any] | None) -> str:
    row = dict(scope or {})
    scope_kind = str(row.get("scope_kind") or "").strip()
    if scope_kind == "family_parent":
        parent_uid = str(row.get("parent_uid") or "").strip()
        if parent_uid:
            return f"__family_parent__{sched._safe_name(parent_uid)}"
    binding = dict(row.get("binding") or {})
    binding_type = str(binding.get("type") or "").strip()
    binding_id = str(binding.get("id") or "").strip()
    if scope_kind == "binding" and binding_type and binding_id:
        return f"__binding__{sched._safe_name(binding_type)}__{sched._safe_name(binding_id)}"
    return ""


def _watchdog_scoped_latest_path(root: Path, *, prefix: str, suffix: str, scope: Dict[str, Any] | None) -> Path | None:
    slug = _watchdog_scope_slug(scope)
    if not slug:
        return None
    return (Path(root).resolve() / f"{prefix}{suffix}{slug}.json").resolve()


def _watchdog_scope_matches(a: Dict[str, Any] | None, b: Dict[str, Any] | None) -> bool:
    row_a = dict(a or {})
    row_b = dict(b or {})
    scope_kind_a = str(row_a.get("scope_kind") or "").strip() or "global"
    scope_kind_b = str(row_b.get("scope_kind") or "").strip() or "global"
    if scope_kind_a != scope_kind_b:
        return False
    scheduler_backend_a = str(row_a.get("scheduler_backend") or "").strip() or "legacy"
    scheduler_backend_b = str(row_b.get("scheduler_backend") or "").strip() or "legacy"
    if scheduler_backend_a != scheduler_backend_b:
        return False
    if str(row_a.get("parent_uid") or "").strip() != str(row_b.get("parent_uid") or "").strip():
        return False
    binding_a = dict(row_a.get("binding") or {})
    binding_b = dict(row_b.get("binding") or {})
    if scope_kind_a == "global" and (not binding_a or not binding_b):
        return True
    return (
        str(binding_a.get("type") or "").strip() == str(binding_b.get("type") or "").strip()
        and str(binding_a.get("id") or "").strip() == str(binding_b.get("id") or "").strip()
    )


def _latest_pipeline_healer(repo_root: Path) -> Dict[str, Any]:
    path = (
        repo_root
        / "governance"
        / "agents"
        / "runtime"
        / "scheduled_jobs"
        / "pipeline_healer"
        / "latest_report.json"
    ).resolve()
    return _read_json(path)


def _minutes_since(utc_text: str, *, now: dt.datetime) -> int | None:
    txt = str(utc_text or "").strip()
    if not txt:
        return None
    try:
        return int(max(0.0, (now - sched._parse_utc(txt)).total_seconds()) // 60)
    except Exception:
        return None


def _is_pid_alive(pid: int) -> bool:
    if int(pid) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


def _spec_for(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    return _read_json(sched._job_path(job_id, repo_root=repo_root))


def _state_for(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    return _read_json(sched._state_path(job_id, repo_root=repo_root))


def _clear_stale_state(job_id: str, *, repo_root: Path, reason: str) -> bool:
    state_path = sched._state_path(job_id, repo_root=repo_root)
    row = _read_json(state_path)
    if not row:
        return False
    changed = False
    for field, reset in (
        ("active_run_id", ""),
        ("active_started_utc", ""),
        ("active_loop_iteration", 0),
        ("active_loop_stage", ""),
        ("active_loop_completed_iterations", 0),
        ("active_loop_max_iterations", 0),
        ("active_loop_updated_utc", ""),
        ("run_now_requested_utc", ""),
        ("run_now_request_run_id", ""),
        ("run_now_request_source", ""),
        ("cancel_requested_utc", ""),
        ("cancel_request_reason", ""),
        ("cancel_request_run_id", ""),
        ("cancel_request_receipt_path", ""),
        ("postpone_requested_utc", ""),
        ("postpone_request_reason", ""),
        ("postpone_request_run_id", ""),
        ("postpone_request_new_run_at", ""),
        ("postpone_request_receipt_path", ""),
    ):
        current = row.get(field)
        if current != reset and str(current or "").strip():
            row[field] = reset
            changed = True
    if str(row.get("last_status") or "").strip().lower() == "running":
        row["last_status"] = "blocked"
        changed = True
    if changed:
        row["last_nonlaunch_reason"] = f"watchdog_recovered:{reason}"
        row["updated_utc"] = _utc_iso()
        _write_json(state_path, row)
    return changed


def _classify_locks(*, repo_root: Path, now: dt.datetime) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    stale: List[Dict[str, Any]] = []
    healthy: List[Dict[str, Any]] = []
    snapshot = maint.job_lock_snapshot(repo_root=repo_root)
    for lock in list(snapshot.get("active_locks") or []) + list(snapshot.get("stale_locks") or []):
        job_id = str(lock.get("job_id") or "").strip()
        lock_path = Path(str(lock.get("path") or "")).resolve()
        row = _read_json(lock_path)
        pid = int(row.get("pid") or 0) if isinstance(row, dict) else 0
        pid_alive = _is_pid_alive(pid)
        spec = _spec_for(job_id, repo_root=repo_root)
        policy = spec.get("policy") if isinstance(spec.get("policy"), dict) else {}
        max_runtime_seconds = int(policy.get("max_runtime_seconds") or 1800)
        health = sched._active_claim_health(
            job_id,
            repo_root=repo_root,
            max_runtime_seconds=max_runtime_seconds,
            expected_run_id=str(row.get("run_id") or ""),
            now=now,
        )
        out = {
            "job_id": job_id,
            "path": str(lock_path),
            "pid": pid,
            "pid_alive": bool(pid_alive),
            "claimed_utc": str(lock.get("claimed_utc") or ""),
            "heartbeat_utc": str(lock.get("heartbeat_utc") or ""),
            "claim_reason": str(health.get("reason") or ""),
            "claim_stale": bool(health.get("stale")),
            "age_s": health.get("age_s"),
            "lock_snapshot_stale_reason": str(lock.get("stale_reason") or ""),
        }
        if (not pid_alive) or bool(health.get("stale")):
            stale.append(out)
        else:
            healthy.append(out)
    return stale, healthy


def _snapshot_only_stale_lock_findings(*, repo_root: Path, known_paths: set[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    snapshot = maint.job_lock_snapshot(repo_root=repo_root)
    for lock in list(snapshot.get("stale_locks") or []):
        lock_path = str(lock.get("path") or "").strip()
        if not lock_path or lock_path in known_paths:
            continue
        findings.append(
            {
                "job_id": str(lock.get("job_id") or "").strip(),
                "path": lock_path,
                "pid": 0,
                "pid_alive": False,
                "claimed_utc": str(lock.get("claimed_utc") or ""),
                "heartbeat_utc": str(lock.get("heartbeat_utc") or ""),
                "claim_reason": "",
                "claim_stale": True,
                "age_s": None,
                "lock_snapshot_stale_reason": str(lock.get("stale_reason") or ""),
            }
        )
    return findings


def _find_orphan_running(*, repo_root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    jobs = sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())
    for row in jobs:
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        active_run_id = str(state.get("active_run_id") or "").strip()
        if not active_run_id:
            continue
        claim_present = bool(state.get("active_claim_present"))
        claim_stale = bool(state.get("active_claim_stale"))
        if claim_present and not claim_stale:
            continue
        out.append(
            {
                "job_id": str(row.get("job_id") or ""),
                "active_run_id": active_run_id,
                "claim_present": claim_present,
                "claim_stale": claim_stale,
                "claim_reason": str(state.get("active_claim_reason") or ""),
            }
        )
    return out


def _terminal_receipt_state_conflict_findings(*, repo_root: Path, jobs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for row in jobs:
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        job_id = str(row.get("job_id") or "").strip()
        active_run_id = str(state.get("active_run_id") or "").strip()
        last_status = str(state.get("last_status") or "").strip().lower()
        if not job_id or last_status != "running":
            continue

        candidate_receipts: List[Dict[str, Any]] = []
        last_receipt_path = Path(str(state.get("last_receipt_path") or "").strip())
        if last_receipt_path.exists():
            payload = _read_json(last_receipt_path)
            if payload:
                payload["_receipt_path"] = str(last_receipt_path)
                candidate_receipts.append(payload)
        if not candidate_receipts:
            candidate_receipts.extend(_recent_job_receipts(repo_root=repo_root, job_id=job_id, limit=3))

        for receipt in candidate_receipts:
            receipt_run_id = str(receipt.get("run_id") or "").strip()
            receipt_status = str(receipt.get("status") or "").strip().lower()
            if active_run_id and receipt_run_id != active_run_id:
                continue
            if receipt_status in {"", "running", "queued", "requested", "request_queued"}:
                continue
            findings.append(
                {
                    "kind": "terminal_receipt_state_conflict",
                    "details": {
                        "job_id": job_id,
                        "active_run_id": active_run_id,
                        "receipt_run_id": receipt_run_id,
                        "state_last_status": str(state.get("last_status") or ""),
                        "receipt_status": str(receipt.get("status") or ""),
                        "receipt_path": str(receipt.get("_receipt_path") or last_receipt_path),
                        "summary": (
                            f"{job_id} still projects running"
                            + (f" for {active_run_id}" if active_run_id else " without a live active_run_id")
                            + f" even though the latest matching receipt is terminal ({receipt_status or 'unknown'})."
                        ),
                    },
                }
            )
            break
    return findings


def _tick_if_due_and_idle(*, repo_root: Path) -> Dict[str, Any]:
    jobs = sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())
    due_eligible = [r for r in jobs if bool((r.get("due") or {}).get("eligible"))]
    active = [r for r in jobs if str(((r.get("state") or {}).get("active_run_id") or "")).strip()]
    if active or not due_eligible:
        return {
            "ran_tick": False,
            "due_eligible_count": len(due_eligible),
            "active_count": len(active),
        }
    tick = sched.scheduler_tick(repo_root=repo_root, limit=10)
    return {
        "ran_tick": True,
        "due_eligible_count": len(due_eligible),
        "active_count": 0,
        "executed_count": int(tick.get("executed_count") or 0),
    }


def _run_pipeline_healer_full_recovery(*, repo_root: Path, apply: bool) -> Dict[str, Any]:
    return healer.run_healer(
        repo_root=repo_root,
        apply=apply,
        repeat_failure_threshold=healer.DEFAULT_REPEAT_FAILURE_THRESHOLD,
        idle_minutes=healer.DEFAULT_IDLE_MINUTES,
        allow_enable_heads=True,
        allow_runtime_repair=True,
        allow_kick=True,
        max_repairs_per_job=healer.DEFAULT_MAX_REPAIRS_PER_JOB,
        max_repairs_global=healer.DEFAULT_MAX_REPAIRS_GLOBAL,
        allow_reconcile_jobs=True,
    )


def _active_jobs(*, repo_root: Path) -> List[Dict[str, Any]]:
    jobs = sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())
    active: List[Dict[str, Any]] = []
    for row in jobs:
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        active_run_id = str(state.get("active_run_id") or "").strip()
        if active_run_id:
            active.append(
                {
                    "job_id": str(row.get("job_id") or ""),
                    "active_run_id": active_run_id,
                    "last_status": str(state.get("last_status") or ""),
                }
            )
    return active


def _enabled_job_ids(*, repo_root: Path) -> set[str]:
    jobs = sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())
    return {str(row.get("job_id") or "").strip() for row in jobs if bool(row.get("enabled")) and str(row.get("job_id") or "").strip()}


def _disk_free_bytes(path: Path) -> int | None:
    try:
        usage = shutil.disk_usage(path)
    except Exception:
        return None
    return int(usage.free)


def _runtime_storage_preflight(*, repo_root: Path) -> list[Dict[str, Any]]:
    findings: list[Dict[str, Any]] = []
    runtime_root = (Path(repo_root).resolve() / "runtime").resolve()
    free_bytes = _disk_free_bytes(runtime_root if runtime_root.exists() else Path(repo_root).resolve())
    if free_bytes is None:
        findings.append(
            {
                "kind": "runtime_storage_check_failed",
                "details": {"path": str(runtime_root)},
            }
        )
        return findings
    warn_threshold = 1 * 1024 * 1024 * 1024
    fail_threshold = 512 * 1024 * 1024
    if free_bytes < fail_threshold:
        findings.append(
            {
                "kind": "runtime_storage_critical",
                "details": {
                    "path": str(runtime_root),
                    "free_bytes": int(free_bytes),
                    "threshold_bytes": int(fail_threshold),
                },
            }
        )
    elif free_bytes < warn_threshold:
        findings.append(
            {
                "kind": "runtime_storage_low",
                "details": {
                    "path": str(runtime_root),
                    "free_bytes": int(free_bytes),
                    "threshold_bytes": int(warn_threshold),
                },
            }
        )
    return findings


def _minutes_between(*, older_utc: str, newer: dt.datetime) -> int | None:
    txt = str(older_utc or "").strip()
    if not txt:
        return None
    try:
        older = sched._parse_utc(txt)
    except Exception:
        return None
    return int(max(0.0, (newer - older).total_seconds()) // 60)


def _watchdog_timer_findings(*, now: dt.datetime) -> tuple[list[Dict[str, Any]], Dict[str, Any]]:
    findings: list[Dict[str, Any]] = []
    try:
        snapshot = _watchdog_timer.timer_status_snapshot()
    except Exception as exc:
        findings.append(
            {
                "kind": "watchdog_timer_status_unavailable",
                "details": {"error": str(exc)},
            }
        )
        return findings, {"reachable": False, "error": str(exc)}
    units = snapshot.get("units") if isinstance(snapshot.get("units"), dict) else {}
    if not bool(snapshot.get("installed")):
        findings.append({"kind": "watchdog_timers_not_installed", "details": {}})
        return findings, dict(snapshot)
    for key in ("audit_timer", "selfheal_timer"):
        row = units.get(key) if isinstance(units.get(key), dict) else {}
        active_state = str(row.get("active_state") or "").strip().lower()
        unit_file_state = str(row.get("unit_file_state") or "").strip().lower()
        if unit_file_state not in {"enabled", "enabled-runtime", "static"}:
            findings.append(
                {
                    "kind": "watchdog_timer_disabled",
                    "details": {"timer": key, "unit_file_state": unit_file_state},
                }
            )
        if active_state not in {"active", "activating"}:
            findings.append(
                {
                    "kind": "watchdog_timer_inactive",
                    "details": {
                        "timer": key,
                        "active_state": active_state,
                        "sub_state": str(row.get("sub_state") or ""),
                    },
                }
            )
        last_trigger_utc = str(row.get("last_trigger_utc") or "")
        minutes_since_trigger = _minutes_between(older_utc=last_trigger_utc, newer=now)
        if minutes_since_trigger is not None and minutes_since_trigger > 90:
            findings.append(
                {
                    "kind": "watchdog_timer_stale_trigger",
                    "details": {
                        "timer": key,
                        "last_trigger_utc": last_trigger_utc,
                        "minutes_since_trigger": minutes_since_trigger,
                    },
                }
            )
    return findings, dict(snapshot)


def _repair_watchdog_report_pointers(*, repo_root: Path, mode: str) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    findings: list[Dict[str, Any]] = []
    actions: list[Dict[str, Any]] = []
    reports_root = _watchdog_reports_root(repo_root)
    report_prefix = "selfheal_" if mode == "selfheal" else "audit_"
    latest_json = reports_root / f"latest_{mode}.json"
    latest_md = reports_root / f"latest_{mode}.md"
    mode_reports = sorted(reports_root.glob(f"{report_prefix}*.json"))
    newest_json = mode_reports[-1] if mode_reports else None
    newest_md = None
    if newest_json is not None:
        newest_md = newest_json.with_suffix(".md")
    pointer_missing = not latest_json.exists() or not latest_md.exists()
    pointer_broken = latest_json.is_symlink() or latest_md.is_symlink()
    if pointer_missing or pointer_broken:
        findings.append(
            {
                "kind": "watchdog_latest_report_pointer_needs_repair",
                "details": {
                    "mode": mode,
                    "latest_json": str(latest_json),
                    "latest_md": str(latest_md),
                    "newest_json": str(newest_json or ""),
                    "newest_md": str(newest_md or ""),
                    "pointer_missing": bool(pointer_missing),
                    "pointer_broken": bool(pointer_broken),
                },
            }
        )
        if newest_json is not None and newest_md is not None and newest_md.exists():
            try:
                latest_json.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(newest_json, latest_json)
                shutil.copyfile(newest_md, latest_md)
                actions.append(
                    {
                        "action": "repair_watchdog_latest_report_pointer",
                        "ok": True,
                        "mode": mode,
                        "source_json": str(newest_json),
                        "source_md": str(newest_md),
                    }
                )
            except Exception as exc:
                actions.append(
                    {
                        "action": "repair_watchdog_latest_report_pointer",
                        "ok": False,
                        "mode": mode,
                        "error": str(exc),
                    }
                )
    return findings, actions


def _runtime_residue_hygiene(*, repo_root: Path, mode: str, now: dt.datetime) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    findings: list[Dict[str, Any]] = []
    actions: list[Dict[str, Any]] = []
    runtime_reports = (Path(repo_root).resolve() / "runtime" / "reports").resolve()
    candidates = sorted(runtime_reports.glob("tmp_*"))
    stale: list[Path] = []
    for path in candidates:
        try:
            age_hours = (now.timestamp() - path.stat().st_mtime) / 3600.0
        except Exception:
            continue
        if age_hours >= 24.0:
            stale.append(path)
    if stale:
        findings.append(
            {
                "kind": "runtime_residue_cleanup_needed",
                "details": {
                    "count": len(stale),
                    "paths": [str(path) for path in stale[:20]],
                    "age_threshold_hours": 24,
                },
            }
        )
        if mode == "selfheal":
            removed: list[str] = []
            failed: list[Dict[str, str]] = []
            for path in stale:
                try:
                    if path.is_dir():
                        shutil.rmtree(path)
                    else:
                        path.unlink()
                    removed.append(str(path))
                except Exception as exc:
                    failed.append({"path": str(path), "error": str(exc)})
            actions.append(
                {
                    "action": "cleanup_runtime_residue",
                    "ok": not failed,
                    "removed_count": len(removed),
                    "removed_paths": removed[:20],
                    "failed": failed[:20],
                }
            )
    return findings, actions


def _maybe_repair_commit_journal(*, repo_root: Path, mode: str) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    del mode
    findings: list[Dict[str, Any]] = []
    report = _commit_journal.scan_pending_entries(repo=repo_root)
    invalid = list(report.get("invalid") or [])
    if invalid:
        invalid_targets = _commit_journal.invalid_fragment_targets(invalid, repo=repo_root, limit=10)
        findings.append(
            {
                "kind": "commit_journal_invalid_fragments",
                "details": {
                    "invalid_count": len(invalid),
                    "invalid": invalid_targets,
                    "repair_authority": "scheduler",
                    "recommended_action": "quarantine_invalid_commit_journal_fragments",
                },
            }
        )
    return findings, []


def _operator_snapshot_needs_self_heal(snapshot: Dict[str, Any] | None) -> bool:
    return bool(_repairable_plane_truth_conflicts(snapshot))


def _repairable_plane_truth_conflicts(snapshot: Dict[str, Any] | None) -> list[Dict[str, Any]]:
    row = dict(snapshot or {})
    summary = dict(row.get("operator_summary") or {})
    conflicts = [dict(conflict) for conflict in list(summary.get("truth_conflicts") or row.get("truth_conflicts") or []) if isinstance(conflict, dict)]
    return [
        conflict
        for conflict in conflicts
        if str(conflict.get("kind") or "").strip() in _REPAIRABLE_PLANE_TRUTH_CONFLICT_KINDS
    ]


def _is_live_scheduler_job(job_id: str, *, repo_root: Path, enabled_job_ids: set[str]) -> bool:
    safe_job_id = str(job_id or "").strip()
    if not safe_job_id:
        return False
    if safe_job_id in enabled_job_ids:
        return True
    state = _state_for(safe_job_id, repo_root=repo_root)
    if str(state.get("active_run_id") or "").strip():
        return True
    return str(state.get("last_status") or "").strip().lower() in {"running", "queued", "starting", "resuming", "workflow_running"}


def _residue_age_minutes(value: Any, *, now: dt.datetime) -> int | None:
    return _minutes_since(str(value or "").strip(), now=now)


_TASK_REGISTER_TASK_BUCKETS = ("active_tasks", "completed_tasks", "residue_tasks")
_ACTIVE_TASK_RESIDUE_STATUSES = {
    "waiting_on_job",
    "waiting_on_verification",
    "interrupted_recovering",
    "ambiguous_artifact",
    "blocked",
}
_NON_ACTIVE_TASK_RESIDUE_STATUSES = {"completed", "done", "interrupted_recovering", "failed", "blocked"}


def _mixed_surface_task_has_only_stale_scheduler_residue(task: Dict[str, Any], *, scheduler_like_surfaces: set[str]) -> bool:
    owner = dict(task.get("execution_surface_ownership") or {})
    owner_surface = str(owner.get("selected_surface") or "").strip().lower()
    owner_stale = owner_surface in scheduler_like_surfaces and str(owner.get("reference_status") or "").strip().lower() == "execution_ref_stale"

    task_runs = [dict(row) for row in list(task.get("task_runs") or []) if isinstance(row, dict)]
    scheduler_run_present = False
    stale_scheduler_run_present = False
    non_scheduler_run_present = False
    for run in task_runs:
        surface = str(run.get("surface") or "").strip().lower()
        if surface in scheduler_like_surfaces:
            scheduler_run_present = True
            if str(run.get("run_state") or "").strip().lower() == "stale":
                stale_scheduler_run_present = True
        elif surface:
            non_scheduler_run_present = True
    return non_scheduler_run_present and (owner_stale or (scheduler_run_present and stale_scheduler_run_present))


def _stale_scheduler_task_register_residue(
    *,
    repo_root: Path,
    now: dt.datetime,
    stale_after_minutes: int = 30,
) -> Dict[str, Any]:
    try:
        snapshot = _lab_task_register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    except Exception as exc:
        return {
            "register": {},
            "residue_tasks": [],
            "residue_loops": [],
            "error": {
                "type": type(exc).__name__,
                "message": str(exc),
            },
        }
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    if not register:
        return {"register": {}, "residue_tasks": [], "residue_loops": []}

    enabled_job_ids = _enabled_job_ids(repo_root=repo_root)
    residue_tasks: list[Dict[str, Any]] = []
    live_task_ids: set[str] = set()
    for bucket_name in _TASK_REGISTER_TASK_BUCKETS:
        task_rows = [dict(row) for row in list(register.get(bucket_name) or []) if isinstance(row, dict)]
        eligible_statuses = _ACTIVE_TASK_RESIDUE_STATUSES if bucket_name == "active_tasks" else _NON_ACTIVE_TASK_RESIDUE_STATUSES
        for task in task_rows:
            task_id = str(task.get("task_id") or "").strip()
            binding = _lab_task_register.scheduler_binding_summary(task)
            if not bool(binding.get("has_scheduler_binding")):
                if task_id:
                    live_task_ids.add(task_id)
                continue
            if bucket_name != "active_tasks" and bool(binding.get("has_historical_scheduler_provenance_only")):
                if task_id:
                    live_task_ids.add(task_id)
                continue
            age_minutes = _residue_age_minutes(task.get("updated_utc"), now=now)
            status = str(task.get("status") or "").strip().lower()
            has_live_binding = False
            referenced_job_ids = {
                str(job_id or "").strip() for job_id in list(binding.get("job_ids") or []) if str(job_id or "").strip()
            }
            for job_id in referenced_job_ids:
                if _is_live_scheduler_job(job_id, repo_root=repo_root, enabled_job_ids=enabled_job_ids):
                    has_live_binding = True
            if (
                bucket_name != "active_tasks"
                and not has_live_binding
                and bool(binding.get("has_runtime_scheduler_binding"))
                and not bool(binding.get("has_only_scheduler_surfaces"))
            ):
                if _mixed_surface_task_has_only_stale_scheduler_residue(task, scheduler_like_surfaces=_lab_task_register._SCHEDULER_LIKE_SURFACES):
                    pass
                else:
                    if task_id:
                        live_task_ids.add(task_id)
                    continue
            if not has_live_binding and age_minutes is not None and age_minutes >= stale_after_minutes and status in eligible_statuses:
                task_copy = dict(task)
                task_copy["_residue_age_minutes"] = age_minutes
                task_copy["_referenced_job_ids"] = sorted(referenced_job_ids)
                task_copy["_binding_sources"] = list(binding.get("binding_sources") or [])
                task_copy["_live_binding_sources"] = list(binding.get("live_binding_sources") or [])
                task_copy["_task_bucket_name"] = bucket_name
                residue_tasks.append(task_copy)
            elif task_id:
                live_task_ids.add(task_id)

    residue_task_ids = {str(row.get("task_id") or "").strip() for row in residue_tasks if str(row.get("task_id") or "").strip()}
    residue_loops: list[Dict[str, Any]] = []
    for loop in [dict(row) for row in list(register.get("open_loops") or []) if isinstance(row, dict)]:
        linked_task_id = str(loop.get("linked_task_id") or "").strip()
        age_minutes = _residue_age_minutes(loop.get("updated_utc"), now=now)
        kind = str(loop.get("kind") or "").strip().lower()
        loop_status = str(loop.get("status") or "").strip().lower()
        if loop_status and loop_status != "open":
            continue
        if linked_task_id and linked_task_id in residue_task_ids:
            loop_copy = dict(loop)
            loop_copy["_residue_age_minutes"] = age_minutes
            residue_loops.append(loop_copy)
            continue
        if (
            linked_task_id
            and linked_task_id not in live_task_ids
            and age_minutes is not None
            and age_minutes >= stale_after_minutes
            and kind in {"blocked_question", "waiting_on_verification"}
        ):
            loop_copy = dict(loop)
            loop_copy["_residue_age_minutes"] = age_minutes
            residue_loops.append(loop_copy)

    return {
        "register": register,
        "residue_tasks": residue_tasks,
        "residue_loops": residue_loops,
    }


def _cleanup_stale_scheduler_task_register_residue(*, repo_root: Path, now: dt.datetime, writer: str) -> Dict[str, Any]:
    residue = _stale_scheduler_task_register_residue(repo_root=repo_root, now=now)
    register = dict(residue.get("register") or {})
    residue_tasks = [dict(row) for row in list(residue.get("residue_tasks") or []) if isinstance(row, dict)]
    residue_loops = [dict(row) for row in list(residue.get("residue_loops") or []) if isinstance(row, dict)]
    if not register or (not residue_tasks and not residue_loops):
        return {"ok": True, "removed_task_count": 0, "removed_open_loop_count": 0}

    cleanup_root = (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "task_register_cleanup").resolve()
    cleanup_root.mkdir(parents=True, exist_ok=True)
    stamp = _utc_iso(now).replace(":", "").replace("-", "")
    base = f"watchdog_stale_scheduler_residue_cleanup__{stamp}"
    manifest_path = (cleanup_root / f"{base}.manifest.json").resolve()
    archive_path = (cleanup_root / f"{base}.zip").resolve()
    residue_task_ids = {str(row.get("task_id") or "").strip() for row in residue_tasks if str(row.get("task_id") or "").strip()}
    residue_task_bucket_by_id = {
        str(row.get("task_id") or "").strip(): str(row.get("_task_bucket_name") or "").strip() or "active_tasks"
        for row in residue_tasks
        if str(row.get("task_id") or "").strip()
    }
    residue_loop_ids = {str(row.get("loop_id") or "").strip() for row in residue_loops if str(row.get("loop_id") or "").strip()}

    with _lab_task_register._LOCK:
        with _lab_task_register._task_register_lock(repo_root) if hasattr(_lab_task_register, "_task_register_lock") else contextlib.nullcontext():
            live_register = _lab_task_register._load_or_init_task_register_locked(repo_root)
            if hasattr(_lab_task_register, "_compact_task_buckets"):
                live_register = _lab_task_register._compact_task_buckets(live_register)
            before = json.loads(json.dumps(live_register))
            live_job_ids = _lab_recovery._live_scheduler_job_ids(Path(repo_root).resolve())
            removed_tasks: list[Dict[str, Any]] = []
            cleaned_tasks: list[Dict[str, Any]] = []
            kept_tasks_by_bucket: Dict[str, list[Dict[str, Any]]] = {bucket_name: [] for bucket_name in _TASK_REGISTER_TASK_BUCKETS}
            for bucket_name in _TASK_REGISTER_TASK_BUCKETS:
                for row in list(live_register.get(bucket_name) or []):
                    if not isinstance(row, dict):
                        continue
                    task = dict(row)
                    task_id = str(task.get("task_id") or "").strip()
                    if task_id not in residue_task_ids or residue_task_bucket_by_id.get(task_id, bucket_name) != bucket_name:
                        kept_tasks_by_bucket[bucket_name].append(task)
                        continue
                    binding = _lab_task_register.scheduler_binding_summary(task)
                    if bucket_name == "active_tasks" and bool(binding.get("has_only_scheduler_surfaces")):
                        removed_task = dict(task)
                        removed_task["_task_bucket_name"] = bucket_name
                        removed_tasks.append(removed_task)
                        continue
                    before_task = json.loads(json.dumps(task))
                    _lab_recovery._mark_task_scheduler_binding_stale(
                        task,
                        writer=f"{writer}.binding_cleanup",
                        live_job_ids=live_job_ids,
                    )
                    _lab_recovery._clear_task_scheduler_binding_residue(
                        task,
                        writer=f"{writer}.binding_cleanup",
                        live_job_ids=live_job_ids,
                    )
                    kept_tasks_by_bucket[bucket_name].append(task)
                    if task != before_task:
                        cleaned_tasks.append(
                            {
                                "task_id": task_id,
                                "task_bucket": bucket_name,
                                "before": before_task,
                                "after": json.loads(json.dumps(task)),
                            }
                        )
            kept_task_ids = {
                str(row.get("task_id") or "").strip()
                for bucket_name in _TASK_REGISTER_TASK_BUCKETS
                for row in kept_tasks_by_bucket[bucket_name]
                if isinstance(row, dict) and str(row.get("task_id") or "").strip()
            }
            removed_loops: list[Dict[str, Any]] = []
            kept_loops: list[Dict[str, Any]] = []
            for row in list(live_register.get("open_loops") or []):
                if not isinstance(row, dict):
                    continue
                loop = dict(row)
                loop_id = str(loop.get("loop_id") or "").strip()
                linked_task_id = str(loop.get("linked_task_id") or "").strip()
                if loop_id in residue_loop_ids and (not linked_task_id or linked_task_id not in kept_task_ids):
                    removed_loops.append(loop)
                    continue
                if linked_task_id and linked_task_id in residue_task_ids and linked_task_id not in kept_task_ids:
                    removed_loops.append(loop)
                    continue
                kept_loops.append(loop)
            for bucket_name in _TASK_REGISTER_TASK_BUCKETS:
                live_register[bucket_name] = kept_tasks_by_bucket[bucket_name]
            live_register["open_loops"] = kept_loops
            _lab_task_register._set_generation(live_register, writer=writer)
            summary = _lab_task_register._write_summary_cache(repo_root, live_register)
            _lab_task_register._append_event(
                repo_root,
                event_type="watchdog_stale_scheduler_residue_cleaned",
                payload={
                    "writer": writer,
                    "removed_task_ids": sorted(
                        str(row.get("task_id") or "").strip()
                        for row in removed_tasks
                        if str(row.get("task_id") or "").strip()
                    ),
                    "removed_task_buckets": sorted(
                        {
                            str(row.get("_task_bucket_name") or "").strip()
                            for row in removed_tasks
                            if str(row.get("_task_bucket_name") or "").strip()
                        }
                    ),
                    "removed_loop_ids": sorted(
                        str(row.get("loop_id") or "").strip()
                        for row in removed_loops
                        if str(row.get("loop_id") or "").strip()
                    ),
                    "removed_task_count": len(removed_tasks),
                    "cleaned_task_count": len(cleaned_tasks),
                    "removed_open_loop_count": len(removed_loops),
                    "manifest_path": str(manifest_path),
                    "archive_path": str(archive_path),
                },
            )
            _lab_task_register._write_generation_event(repo_root, register=live_register, writer=writer)
            _lab_task_register._append_commit_event(repo_root, register=live_register, summary=summary, writer=writer)
            _lab_task_register._write_json_atomic(_lab_task_register._register_path(repo_root), live_register)
            _lab_task_register._write_json_atomic(_lab_task_register._summary_cache_path(repo_root), summary)

    manifest = {
        "schema_version": "task_register_residue_cleanup_manifest_v1",
        "generated_utc": _utc_iso(now),
        "writer": writer,
        "reason": "watchdog removed stale scheduler/healer/swarm residue with no live jobs remaining",
        "removed_task_count": len(removed_tasks),
        "cleaned_task_count": len(cleaned_tasks),
        "removed_open_loop_count": len(removed_loops),
        "removed_task_ids": sorted(
            str(row.get("task_id") or "").strip()
            for row in removed_tasks
            if str(row.get("task_id") or "").strip()
        ),
        "removed_loop_ids": sorted(
            str(row.get("loop_id") or "").strip()
            for row in removed_loops
            if str(row.get("loop_id") or "").strip()
        ),
        "removed_tasks": removed_tasks,
        "cleaned_tasks": cleaned_tasks,
        "removed_open_loops": removed_loops,
        "before_counts": {
            "active_tasks": len(list(before.get("active_tasks") or [])),
            "completed_tasks": len(list(before.get("completed_tasks") or [])),
            "residue_tasks": len(list(before.get("residue_tasks") or [])),
            "open_loops": len(list(before.get("open_loops") or [])),
        },
        "after_counts": {
            "active_tasks": len(kept_tasks_by_bucket["active_tasks"]),
            "completed_tasks": len(kept_tasks_by_bucket["completed_tasks"]),
            "residue_tasks": len(kept_tasks_by_bucket["residue_tasks"]),
            "open_loops": len(kept_loops),
        },
    }
    _write_json(manifest_path, manifest)
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        zf.writestr("removed_tasks.json", json.dumps(removed_tasks, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        zf.writestr("cleaned_tasks.json", json.dumps(cleaned_tasks, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        zf.writestr("removed_open_loops.json", json.dumps(removed_loops, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    # Rebuild recovery truth on the cleaned register.
    recovery = _lab_recovery.reconcile_startup(repo_root, writer=f"{writer}.recovery")
    return {
        "ok": True,
        "removed_task_count": len(removed_tasks),
        "cleaned_task_count": len(cleaned_tasks),
        "removed_open_loop_count": len(removed_loops),
        "manifest_path": str(manifest_path),
        "archive_path": str(archive_path),
        "recovery_status": str(recovery.get("status") or ""),
    }


def _self_heal_plane_truth(*, repo_root: Path, writer: str) -> Dict[str, Any]:
    _lab_recovery.reconcile_startup(repo_root, writer=f"{writer}.recovery")
    _lab_task_register.rebuild_summary_cache(repo_root, writer=f"{writer}.summary_cache")
    return _operator_snapshot.build_operator_snapshot(repo_root)


def _maybe_self_heal_plane_truth(
    *,
    repo_root: Path,
    mode: str,
    mutating_selfheal_allowed: bool,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    findings: list[Dict[str, Any]] = []
    actions: list[Dict[str, Any]] = []
    snapshot = _operator_snapshot.build_operator_snapshot(repo_root)
    summary = dict(snapshot.get("operator_summary") or {})
    conflicts = [dict(row) for row in list(summary.get("truth_conflicts") or snapshot.get("truth_conflicts") or []) if isinstance(row, dict)]
    repairable_conflicts = _repairable_plane_truth_conflicts(snapshot)
    if repairable_conflicts:
        findings.append(
            {
                "kind": "plane_truth_selfheal_needed",
                "details": {
                    "conflict_kinds": [str(row.get("kind") or "") for row in repairable_conflicts],
                    "all_conflict_kinds": [str(row.get("kind") or "") for row in conflicts],
                },
            }
        )
        if mode == "selfheal" and not mutating_selfheal_allowed:
            actions.append(
                {
                    "action": "plane_truth_selfheal",
                    "ok": False,
                    "blocked": True,
                    "reason": "active_jobs_present",
                }
            )
        elif mode == "selfheal":
            try:
                healed = _self_heal_plane_truth(repo_root=repo_root, writer="scheduler_watchdog.selfheal")
                remaining = bool(_repairable_plane_truth_conflicts(healed))
                actions.append(
                    {
                        "action": "plane_truth_selfheal",
                        "ok": not remaining,
                        "remaining_conflict": bool(remaining),
                        "pre_conflict_kinds": [str(row.get("kind") or "") for row in repairable_conflicts],
                        "remaining_conflict_kinds": [
                            str(row.get("kind") or "")
                            for row in list(dict(healed.get("operator_summary") or {}).get("truth_conflicts") or [])
                            if isinstance(row, dict)
                        ],
                        "post_heal_state": str(dict(healed.get("operator_summary") or {}).get("status_language", {}).get("state") or ""),
                        "post_heal_conflict_count": int(
                            len(list(dict(healed.get("operator_summary") or {}).get("truth_conflicts") or []))
                        ),
                    }
                )
            except Exception as exc:
                actions.append(
                    {
                        "action": "plane_truth_selfheal",
                        "ok": False,
                        "error": str(exc),
                    }
                )
    elif conflicts:
        findings.append(
            {
                "kind": "plane_truth_conflict_detected_but_not_auto_repairable",
                "details": {
                    "conflict_kinds": [str(row.get("kind") or "") for row in conflicts],
                },
            }
        )
    return findings, actions


def _maybe_refresh_delegation_proof(*, repo_root: Path, mode: str, now: dt.datetime) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    findings: list[Dict[str, Any]] = []
    actions: list[Dict[str, Any]] = []
    latest = _execution_proof.read_latest_report(repo_root, now_utc=_utc_iso(now))
    freshness = dict(latest.get("freshness") or {})
    stale = bool(freshness.get("stale"))
    status = str(((latest.get("summary") or {}) if isinstance(latest.get("summary"), dict) else {}).get("overall_status") or "").strip().lower()
    if stale or status == "missing":
        findings.append(
            {
                "kind": "delegation_proof_refresh_needed",
                "details": {
                    "stale": stale,
                    "overall_status": status,
                    "reason": str(freshness.get("reason") or ""),
                },
            }
        )
        if mode == "selfheal":
            try:
                report = _execution_proof.record_execution_proof_report(
                    repo_root,
                    writer="scheduler_watchdog.selfheal",
                    now_utc=_utc_iso(now),
                )
                actions.append(
                    {
                        "action": "refresh_delegation_proof",
                        "ok": True,
                        "proof_run_id": str(report.get("proof_run_id") or ""),
                        "overall_status": str(((report.get("summary") or {}) if isinstance(report.get("summary"), dict) else {}).get("overall_status") or ""),
                    }
                )
            except Exception as exc:
                actions.append(
                    {
                        "action": "refresh_delegation_proof",
                        "ok": False,
                        "error": str(exc),
                    }
                )
    return findings, actions


def _scheduler_residue_hygiene(
    *,
    repo_root: Path,
    mode: str,
    now: dt.datetime,
    mutating_selfheal_allowed: bool,
) -> tuple[list[Dict[str, Any]], list[Dict[str, Any]]]:
    findings: list[Dict[str, Any]] = []
    actions: list[Dict[str, Any]] = []
    residue = _stale_scheduler_task_register_residue(repo_root=repo_root, now=now)
    if isinstance(residue.get("error"), dict) and residue["error"]:
        findings.append(
            {
                "kind": "task_register_snapshot_unreadable",
                "details": dict(residue["error"]),
            }
        )
        return findings, actions
    residue_tasks = [dict(row) for row in list(residue.get("residue_tasks") or []) if isinstance(row, dict)]
    residue_loops = [dict(row) for row in list(residue.get("residue_loops") or []) if isinstance(row, dict)]
    if not residue_tasks and not residue_loops:
        return findings, actions
    findings.append(
        {
            "kind": "stale_scheduler_task_register_residue",
            "details": {
                "task_ids": [str(row.get("task_id") or "") for row in residue_tasks],
                "loop_ids": [str(row.get("loop_id") or "") for row in residue_loops],
                "residue_tasks": [
                    {
                        "task_id": str(row.get("task_id") or "").strip(),
                        "referenced_job_ids": [str(job_id or "").strip() for job_id in list(row.get("_referenced_job_ids") or []) if str(job_id or "").strip()],
                        "binding_sources": [str(source or "").strip() for source in list(row.get("_binding_sources") or []) if str(source or "").strip()],
                        "live_binding_sources": [
                            str(source or "").strip() for source in list(row.get("_live_binding_sources") or []) if str(source or "").strip()
                        ],
                    }
                    for row in residue_tasks
                    if str(row.get("task_id") or "").strip()
                ],
                "task_count": len(residue_tasks),
                "open_loop_count": len(residue_loops),
            },
        }
    )
    if mode == "selfheal" and not mutating_selfheal_allowed:
        actions.append(
            {
                "action": "cleanup_stale_scheduler_task_register_residue",
                "ok": False,
                "blocked": True,
                "reason": "active_jobs_present",
            }
        )
    elif mode == "selfheal":
        try:
            result = _cleanup_stale_scheduler_task_register_residue(
                repo_root=repo_root,
                now=now,
                writer="scheduler_watchdog.selfheal",
            )
            actions.append(
                {
                    "action": "cleanup_stale_scheduler_task_register_residue",
                    **result,
                }
            )
        except Exception as exc:
            actions.append(
                {
                    "action": "cleanup_stale_scheduler_task_register_residue",
                    "ok": False,
                    "error": str(exc),
                }
            )
    return findings, actions


def _scheduler_residue_requested_action_targets(*, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    residue_tasks: List[Dict[str, Any]] = []
    residue_loops: List[Dict[str, Any]] = []
    for row in findings:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "").strip() != "stale_scheduler_task_register_residue":
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        residue_task_rows = list(details.get("residue_tasks") or [])
        for task_row in residue_task_rows:
            if not isinstance(task_row, dict):
                continue
            item = {
                "task_id": str(task_row.get("task_id") or "").strip(),
                "referenced_job_ids": [
                    str(job_id or "").strip() for job_id in list(task_row.get("referenced_job_ids") or []) if str(job_id or "").strip()
                ],
                "binding_sources": [
                    str(source or "").strip() for source in list(task_row.get("binding_sources") or []) if str(source or "").strip()
                ],
                "live_binding_sources": [
                    str(source or "").strip() for source in list(task_row.get("live_binding_sources") or []) if str(source or "").strip()
                ],
            }
            if item["task_id"] and item not in residue_tasks:
                residue_tasks.append(item)
        if not residue_task_rows:
            for task_id in list(details.get("task_ids") or []):
                item = {"task_id": str(task_id or "").strip()}
                if item["task_id"] and item not in residue_tasks:
                    residue_tasks.append(item)
        for loop_id in list(details.get("loop_ids") or []):
            item = {"loop_id": str(loop_id or "").strip()}
            if item["loop_id"] and item not in residue_loops:
                residue_loops.append(item)
    return {
        "residue_tasks": residue_tasks,
        "residue_open_loops": residue_loops,
    }


def _sanitize_pipeline_healer_result(report: Dict[str, Any], *, enabled_job_ids: set[str], now: dt.datetime) -> Dict[str, Any]:
    if not report:
        return {}
    row = dict(report)
    age_minutes = _minutes_since(str(report.get("generated_utc") or ""), now=now)
    filtered_findings: List[Dict[str, Any]] = []
    suppressed = 0
    for finding in list(report.get("findings") or []):
        if not isinstance(finding, dict):
            continue
        job_id = str(finding.get("job_id") or "").strip()
        if job_id and job_id not in enabled_job_ids:
            suppressed += 1
            continue
        filtered_findings.append(finding)
    filtered_actions: List[Dict[str, Any]] = []
    for action in list(report.get("actions") or []):
        if not isinstance(action, dict):
            continue
        job_id = str(action.get("job_id") or "").strip()
        if job_id and job_id not in enabled_job_ids:
            continue
        filtered_actions.append(action)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    default_finding_count = int(summary.get("finding_count") or 0)
    default_repairs_applied = int(summary.get("repairs_applied") or 0)
    row["findings"] = filtered_findings
    row["actions"] = filtered_actions
    row["summary"] = {
        **summary,
        "finding_count": len(filtered_findings) if filtered_findings or suppressed else default_finding_count,
        "repairs_applied": (
            len([a for a in filtered_actions if str(a.get("decision") or "").strip().lower() == "applied"])
            if filtered_actions or suppressed
            else default_repairs_applied
        ),
        "historical_findings_suppressed": suppressed,
    }
    row["stale"] = bool(age_minutes is not None and age_minutes > 120)
    row["age_minutes"] = age_minutes
    return row


def _watchdog_repeat_job_problem_findings(
    *,
    repo_root: Path,
    pipeline_healer_result: Dict[str, Any],
    threshold: int = _WATCHDOG_REPEAT_JOB_PROBLEM_THRESHOLD,
) -> List[Dict[str, Any]]:
    if int(threshold) < 1:
        return []
    current_findings = [
        dict(row)
        for row in list(pipeline_healer_result.get("findings") or [])
        if isinstance(row, dict)
    ]
    if not current_findings:
        return []
    current_keys = {
        (str(row.get("job_id") or "").strip(), str(row.get("finding_type") or "").strip())
        for row in current_findings
        if str(row.get("job_id") or "").strip() and str(row.get("finding_type") or "").strip()
    }
    if not current_keys:
        return []
    success_cutoff_by_job = {
        key[0]: _latest_terminal_success_receipt_utc(repo_root=repo_root, job_id=key[0])
        for key in current_keys
    }
    counts: Dict[Tuple[str, str], int] = {}
    latest_run_ids: Dict[Tuple[str, str], List[str]] = {}
    for key in current_keys:
        repeat_count, run_ids = _contiguous_applied_repair_count_since_success(
            repo_root=repo_root,
            key=key,
            success_cutoff_utc=success_cutoff_by_job.get(key[0]),
        )
        counts[key] = int(repeat_count)
        latest_run_ids[key] = list(run_ids)
    out: List[Dict[str, Any]] = []
    for row in current_findings:
        job_id = str(row.get("job_id") or "").strip()
        finding_type = str(row.get("finding_type") or "").strip()
        key = (job_id, finding_type)
        repeat_count = int(counts.get(key) or 0)
        if not job_id or not finding_type or repeat_count < int(threshold):
            continue
        evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
        out.append(
            {
                "kind": "watchdog_repeat_repair_exhausted",
                "details": {
                    "job_id": job_id,
                    "finding_type": finding_type,
                    "repeat_count": repeat_count,
                    "threshold": int(threshold),
                    "repair_class": str(row.get("repair_class") or ""),
                    "latest_summary": str(row.get("summary") or ""),
                    "latest_run_ids": list(latest_run_ids.get(key) or [])[: int(threshold)],
                    "latest_error": str(evidence.get("latest_error") or ""),
                    "latest_run_id": str(evidence.get("latest_run_id") or ""),
                },
            }
        )
    return out


def _watchdog_repeated_maintenance_condition_ids(
    *,
    repo_root: Path,
    current_condition_ids: List[str],
    scope: Dict[str, Any] | None = None,
    threshold: int = _WATCHDOG_REPEAT_JOB_PROBLEM_THRESHOLD,
) -> List[str]:
    if int(threshold) < 1:
        return []
    normalized = [str(row or "").strip() for row in list(current_condition_ids or []) if str(row or "").strip()]
    if not normalized:
        return []
    tracked = {row: 0 for row in normalized}
    seen: set[str] = set()
    stopped: set[str] = set()
    runs_root = _watchdog_maintenance_runs_root(Path(repo_root).resolve())
    run_paths = sorted(runs_root.glob("*.json"))
    for path in reversed(run_paths):
        payload = _read_json(path)
        if not payload:
            continue
        if not _watchdog_scope_matches(scope, payload.get("scope") if isinstance(payload.get("scope"), dict) else {}):
            continue
        condition_ids: set[str] = set()
        for row in list(payload.get("root_causes") or []):
            if not isinstance(row, dict):
                continue
            condition_id = str(row.get("condition_id") or "").strip()
            if condition_id:
                condition_ids.add(condition_id)
        for row in list(payload.get("findings") or []):
            if not isinstance(row, dict):
                continue
            kind = str(row.get("kind") or "").strip()
            if kind:
                condition_ids.add(kind)
        for condition_id in tracked.keys():
            if condition_id in stopped:
                continue
            if condition_id in condition_ids:
                tracked[condition_id] += 1
                seen.add(condition_id)
            elif condition_id in seen:
                stopped.add(condition_id)
    return [condition_id for condition_id, count in tracked.items() if int(count) + 1 >= int(threshold)]


def _classify_health(*, summary: Dict[str, Any], findings: List[Dict[str, Any]], selfheal_mutating_actions_allowed: bool) -> Dict[str, Any]:
    enabled_jobs = int(summary.get("enabled_jobs") or 0)
    active_jobs = int(summary.get("running_jobs") or 0)
    finding_kinds = {str(f.get("kind") or f.get("finding_type") or "").strip() for f in findings if isinstance(f, dict)}
    if enabled_jobs == 0 and not finding_kinds:
        return {"health": "idle", "reason": "no_enabled_scheduler_jobs"}
    if "truth_ambiguous" in finding_kinds:
        return {"health": "degraded", "reason": "truth_ambiguous"}
    if "runtime_storage_critical" in finding_kinds or "runtime_storage_low" in finding_kinds:
        return {"health": "degraded", "reason": "runtime_storage_health"}
    if "task_register_snapshot_unreadable" in finding_kinds:
        return {"health": "degraded", "reason": "task_register_snapshot_unreadable"}
    if "stale_lock" in finding_kinds or "orphan_running_state" in finding_kinds:
        return {"health": "degraded", "reason": "runtime_recovery_needed"}
    if "dependency_blocked_chain" in finding_kinds:
        return {"health": "degraded", "reason": "dependency_blocked_chain"}
    if "pipeline_healer_stale" in finding_kinds:
        return {"health": "degraded", "reason": "pipeline_healer_stale"}
    if "watchdog_timer_inactive" in finding_kinds or "watchdog_timer_stale_trigger" in finding_kinds:
        return {"health": "degraded", "reason": "watchdog_timer_drift"}
    if active_jobs > 0 and not selfheal_mutating_actions_allowed:
        return {"health": "active_busy", "reason": "mutating_repairs_suppressed_during_active_runs"}
    if finding_kinds:
        reason = "findings_present_without_enabled_jobs" if enabled_jobs == 0 else "findings_present"
        return {"health": "degraded", "reason": reason}
    return {"health": "healthy", "reason": "no_current_findings"}


def _dependency_blocked_chain_findings(*, jobs: List[Dict[str, Any]], now: dt.datetime) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for row in jobs:
        if not bool(row.get("enabled")):
            continue
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        reason = str(due.get("reason") or "").strip()
        if not (reason.startswith("dependency_not_ok:") or reason.startswith("dependency_pending:")):
            continue
        schedule = row.get("schedule") if isinstance(row.get("schedule"), dict) else {}
        run_at = str(schedule.get("run_at") or "").strip()
        overdue = False
        if run_at:
            try:
                overdue = sched._parse_utc(run_at) <= now
            except Exception:
                overdue = True
        if not overdue:
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        dep_job_id = str(due.get("dependency_job_id") or reason.split(":", 1)[-1]).strip()
        dependency_last_status = str(due.get("dependency_last_status") or "").strip()
        dep_due_reason = str(due.get("dependency_due_reason") or "").strip()
        dependency_active_run_id = str(due.get("dependency_active_run_id") or "").strip()
        dependency_run_now_requested_utc = str(due.get("dependency_run_now_requested_utc") or "").strip()
        dependency_ledger_uid = str(due.get("dependency_ledger_uid") or "").strip()
        dependency_ledger_status = str(due.get("dependency_ledger_status") or "").strip()
        dependency_ledger_missing_reasons = [
            str(item or "").strip() for item in list(due.get("dependency_ledger_missing_reasons") or []) if str(item or "").strip()
        ]
        dependency_last_status_normalized = dependency_last_status.lower()
        # A disabled dependency that already ended in a terminal non-ok state is
        # ordinary fail-closed scheduler truth, not an execution-state disagreement.
        if dep_due_reason == "disabled" and dependency_last_status_normalized in _WATCHDOG_TERMINAL_DEPENDENCY_BLOCK_STATUSES:
            continue
        # If the scheduler already knows the blocker is merely pending and the
        # blocker has never launched or recorded status, this is backlog truth,
        # not a live ownership/dependency disagreement.
        if (
            reason.startswith("dependency_pending:")
            and dep_due_reason
            and not dependency_last_status_normalized
            and not dependency_active_run_id
            and not dependency_run_now_requested_utc
        ):
            continue
        # A dependency that already succeeded but is still holding children only
        # for ledger-family handoff truth is ordinary backlog truth, not runtime
        # ownership drift.
        if (
            reason.startswith("dependency_pending:")
            and dependency_last_status_normalized in _WATCHDOG_TERMINAL_SUCCESS_RECEIPT_STATUSES
            and dependency_ledger_uid
            and dependency_ledger_missing_reasons
            and not dependency_active_run_id
            and not dependency_run_now_requested_utc
        ):
            continue
        # A child blocked behind a dependency with a real active run is normal
        # scheduler backlog truth. If that active run is stale or orphaned, the
        # dependency job itself should surface the disagreement directly.
        if (
            reason.startswith("dependency_pending:")
            and dep_due_reason == "active_run"
            and dependency_last_status_normalized == "running"
            and dependency_active_run_id
        ):
            continue
        findings.append(
            {
                "kind": "dependency_blocked_chain",
                "details": {
                    "job_id": str(row.get("job_id") or "").strip(),
                    "dependency_reason": reason,
                    "dependency_job_id": dep_job_id,
                    "run_at": run_at,
                    "last_status": str(state.get("last_status") or ""),
                    "last_nonlaunch_status": str(state.get("last_nonlaunch_status") or ""),
                    "dependency_last_status": dependency_last_status,
                    "dependency_active_run_id": dependency_active_run_id,
                    "dependency_run_now_requested_utc": dependency_run_now_requested_utc,
                    "dependency_due_reason": dep_due_reason,
                    "dependency_ledger_uid": dependency_ledger_uid,
                    "dependency_ledger_status": dependency_ledger_status,
                    "dependency_ledger_missing_reasons": dependency_ledger_missing_reasons,
                    "summary": f"{str(row.get('job_id') or '').strip()} blocked by {dep_job_id or 'unknown dependency'}",
                },
            }
        )
    return findings


def _recent_job_receipts(*, repo_root: Path, job_id: str, limit: int = 6) -> List[Dict[str, Any]]:
    job = str(job_id or "").strip()
    if not job:
        return []
    root = sched.receipts_dir(repo_root) / job
    paths = sorted(root.glob("*.json"))
    out: List[Dict[str, Any]] = []
    for path in reversed(paths):
        payload = _read_json(path)
        if not payload:
            continue
        payload["_receipt_path"] = str(path)
        out.append(payload)
        if len(out) >= int(limit):
            break
    return out


def _global_concurrency_blockers_from_receipt(receipt: Dict[str, Any]) -> List[Dict[str, str]]:
    execution_guard = receipt.get("execution_guard") if isinstance(receipt.get("execution_guard"), dict) else {}
    if str(execution_guard.get("kind") or "").strip() != "global_concurrency_cap":
        return []
    blockers: List[Dict[str, str]] = []
    for item in list(execution_guard.get("active_jobs") or []):
        if not isinstance(item, dict):
            continue
        blocker = {
            "job_id": str(item.get("job_id") or "").strip(),
            "run_id": str(item.get("run_id") or "").strip(),
            "started_utc": str(item.get("started_utc") or "").strip(),
            "heartbeat_utc": str(item.get("heartbeat_utc") or "").strip(),
            "role": str(item.get("role") or "").strip(),
        }
        if blocker["job_id"]:
            blockers.append(blocker)
    return blockers


def _parse_optional_utc(text: Any) -> dt.datetime | None:
    txt = str(text or "").strip()
    if not txt:
        return None
    try:
        return sched._parse_utc(txt)
    except Exception:
        return None


def _latest_terminal_success_receipt_utc(*, repo_root: Path, job_id: str, limit: int = 24) -> dt.datetime | None:
    latest_success: dt.datetime | None = None
    for receipt in _recent_job_receipts(repo_root=repo_root, job_id=job_id, limit=limit):
        status = str(receipt.get("status") or "").strip().lower()
        if status not in _WATCHDOG_TERMINAL_SUCCESS_RECEIPT_STATUSES:
            continue
        receipt_utc = _parse_optional_utc(
            receipt.get("finished_at")
            or receipt.get("generated_utc")
            or receipt.get("utc")
        )
        if receipt_utc is None:
            continue
        if latest_success is None or receipt_utc > latest_success:
            latest_success = receipt_utc
    return latest_success


def _contiguous_applied_repair_count_since_success(
    *,
    repo_root: Path,
    key: Tuple[str, str],
    success_cutoff_utc: dt.datetime | None,
) -> Tuple[int, List[str]]:
    counts = 0
    latest_run_ids: List[str] = []
    receipts_root = healer._receipts_dir(Path(repo_root).resolve())
    receipt_paths = sorted(receipts_root.glob("*.json"))
    for path in reversed(receipt_paths):
        report = _read_json(path)
        if not report or not bool(report.get("apply_mode")):
            continue
        report_utc = _parse_optional_utc(report.get("generated_utc"))
        if success_cutoff_utc is not None and report_utc is not None and report_utc <= success_cutoff_utc:
            break
        run_id = str(report.get("run_id") or "").strip()
        applied_keys: set[Tuple[str, str]] = set()
        for action in list(report.get("actions") or []):
            if not isinstance(action, dict):
                continue
            if str(action.get("decision") or "").strip().lower() != "applied":
                continue
            action_key = (
                str(action.get("job_id") or "").strip(),
                str(action.get("finding_type") or "").strip(),
            )
            if action_key[0] and action_key[1]:
                applied_keys.add(action_key)
        if key in applied_keys:
            counts += 1
            if run_id and run_id not in latest_run_ids:
                latest_run_ids.append(run_id)
            continue
        if counts > 0:
            break
    return counts, latest_run_ids


def _job_needs_watchdog_history_review(row: Dict[str, Any]) -> bool:
    if not bool(row.get("enabled")):
        return False
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    if str(state.get("active_run_id") or "").strip():
        return True
    last_status = str(state.get("last_status") or "").strip().lower()
    if last_status in {"blocked", "deferred", "failed_verification", "proof_gap", "error", "failed"}:
        return True
    reason = str(due.get("reason") or "").strip().lower()
    return bool(reason)


def _normalized_job_failure_signature(receipt: Dict[str, Any]) -> str:
    status = str(receipt.get("status") or "").strip().lower()
    if status not in {"blocked", "deferred", "failed_verification", "proof_gap", "error", "failed"}:
        return ""
    detail = str(receipt.get("reason") or receipt.get("error") or "").strip()
    detail = " ".join(detail.split())
    if not detail:
        return status
    for prefix in (
        "continuation_bootstrap_blocked",
        "dependency_not_ok",
        "dependency_pending",
        "global_concurrency_cap_reached",
        "project_runner_repeat_exit_guard",
        "binding_launch_repeat_guard",
        "claimed_elsewhere",
        "retry_backoff",
        "invalid PASS_PENDING_ACTIONS contract",
        "required validation evidence missing",
        "execution_family_closeout_truth_failed",
        "off_target_scope",
    ):
        if detail.startswith(prefix):
            return f"{status}:{prefix}"
    coarse = detail.split(":", 1)[0].strip()
    return f"{status}:{coarse[:120]}" if coarse else status


def _recent_job_failure_signature_findings(
    *,
    repo_root: Path,
    jobs: List[Dict[str, Any]],
    threshold: int = _WATCHDOG_REPEAT_JOB_PROBLEM_THRESHOLD,
) -> List[Dict[str, Any]]:
    if int(threshold) < 1:
        return []
    findings: List[Dict[str, Any]] = []
    for row in jobs:
        if not _job_needs_watchdog_history_review(row):
            continue
        job_id = str(row.get("job_id") or "").strip()
        if not job_id:
            continue
        receipts = _recent_job_receipts(repo_root=repo_root, job_id=job_id, limit=max(4, int(threshold) + 2))
        signatures: List[Dict[str, Any]] = []
        for receipt in receipts:
            signature = _normalized_job_failure_signature(receipt)
            if not signature:
                continue
            signatures.append(
                {
                    "signature": signature,
                    "run_id": str(receipt.get("run_id") or "").strip(),
                    "status": str(receipt.get("status") or "").strip(),
                    "reason": str(receipt.get("reason") or "").strip(),
                    "error": str(receipt.get("error") or "").strip(),
                    "path": str(receipt.get("_receipt_path") or "").strip(),
                }
            )
        if len(signatures) < int(threshold):
            continue
        latest_signature = str(signatures[0].get("signature") or "").strip()
        repeat_count = 0
        latest_runs: List[str] = []
        latest_paths: List[str] = []
        for item in signatures:
            if str(item.get("signature") or "").strip() != latest_signature:
                break
            repeat_count += 1
            if str(item.get("run_id") or "").strip():
                latest_runs.append(str(item.get("run_id") or "").strip())
            if str(item.get("path") or "").strip():
                latest_paths.append(str(item.get("path") or "").strip())
        if repeat_count < int(threshold):
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        latest_receipt = receipts[0] if receipts else {}
        if latest_signature == "deferred:global_concurrency_cap_reached":
            blockers = _global_concurrency_blockers_from_receipt(latest_receipt)
            findings.append(
                {
                    "kind": "global_concurrency_cap_starvation",
                    "details": {
                        "job_id": job_id,
                        "repeat_count": repeat_count,
                        "threshold": int(threshold),
                        "signature": latest_signature,
                        "last_status": str(state.get("last_status") or ""),
                        "last_nonlaunch_reason": str(state.get("last_nonlaunch_reason") or ""),
                        "due_reason": str(due.get("reason") or ""),
                        "active_run_id": str(state.get("active_run_id") or ""),
                        "blocking_job_ids": [str(item.get("job_id") or "") for item in blockers if str(item.get("job_id") or "")],
                        "blocking_jobs": blockers,
                        "latest_run_ids": latest_runs[: int(threshold) + 2],
                        "latest_receipt_paths": latest_paths[: int(threshold) + 2],
                        "summary": (
                            f"{job_id} was deferred by the global concurrency cap {repeat_count} time(s)"
                            " without a blocker change; review the blocking active jobs instead of re-escalating the victim job."
                        ),
                    },
                }
            )
            continue
        if latest_signature in {
            "blocked:project_runner_repeat_exit_guard",
            "deferred:project_runner_repeat_exit_guard",
            "blocked:binding_launch_repeat_guard",
            "deferred:binding_launch_repeat_guard",
        }:
            continue
        if latest_signature == "blocked:binding_launch_failed":
            try:
                spec = sched.load_job(job_id, repo_root=repo_root)
            except Exception:
                spec = {}
            guard = (
                sched._binding_launch_repeat_guard(spec, repo_root=repo_root)
                if isinstance(spec, dict) and spec
                else {}
            )
            if bool(guard.get("blocked")):
                continue
        if latest_signature == "error:project_runner_exit_code":
            try:
                spec = sched.load_job(job_id, repo_root=repo_root)
            except Exception:
                spec = {}
            guard = (
                sched._project_runner_repeat_exit_guard(spec, repo_root=repo_root)
                if isinstance(spec, dict) and spec
                else {}
            )
            if bool(guard.get("blocked")):
                continue
        findings.append(
            {
                "kind": "repeated_job_failure_signature",
                "details": {
                    "job_id": job_id,
                    "repeat_count": repeat_count,
                    "threshold": int(threshold),
                    "signature": latest_signature,
                    "last_status": str(state.get("last_status") or ""),
                    "last_nonlaunch_reason": str(state.get("last_nonlaunch_reason") or ""),
                    "due_reason": str(due.get("reason") or ""),
                    "active_run_id": str(state.get("active_run_id") or ""),
                    "latest_run_ids": latest_runs[: int(threshold) + 2],
                    "latest_receipt_paths": latest_paths[: int(threshold) + 2],
                    "summary": f"{job_id} repeated the same failure signature {repeat_count} time(s): {latest_signature}",
                },
            }
        )
    return findings


def _execution_state_requested_action_targets(*, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    stale_lock_job_ids: List[str] = []
    orphan_running_job_ids: List[str] = []
    dependency_chains: List[Dict[str, Any]] = []
    for row in findings:
        if not isinstance(row, dict):
            continue
        kind = str(row.get("kind") or "").strip()
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        if kind == "stale_lock":
            job_id = str(details.get("job_id") or "").strip()
            if job_id and job_id not in stale_lock_job_ids:
                stale_lock_job_ids.append(job_id)
        elif kind == "orphan_running_state":
            job_id = str(details.get("job_id") or "").strip()
            if job_id and job_id not in orphan_running_job_ids:
                orphan_running_job_ids.append(job_id)
        elif kind == "dependency_blocked_chain":
            dependent_job_id = str(details.get("job_id") or "").strip()
            dependency_job_id = str(details.get("dependency_job_id") or "").strip()
            chain = {
                "job_id": dependent_job_id,
                "dependency_job_id": dependency_job_id,
                "run_at": str(details.get("run_at") or ""),
                "last_status": str(details.get("last_status") or ""),
                "dependency_last_status": str(details.get("dependency_last_status") or ""),
                "dependency_due_reason": str(details.get("dependency_due_reason") or ""),
            }
            if dependent_job_id or dependency_job_id:
                dependency_chains.append(chain)
    return {
        "stale_lock_job_ids": stale_lock_job_ids,
        "orphan_running_job_ids": orphan_running_job_ids,
        "dependency_blocked_chains": dependency_chains,
    }


def _commit_journal_requested_action_targets(*, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    invalid_fragments: List[Dict[str, str]] = []
    for row in findings:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or "").strip() != "commit_journal_invalid_fragments":
            continue
        details = row.get("details") if isinstance(row.get("details"), dict) else {}
        for target in list(details.get("invalid") or []):
            if not isinstance(target, dict):
                continue
            item = {
                "path": str(target.get("path") or "").strip(),
                "rel_path": str(target.get("rel_path") or "").strip(),
                "filename": str(target.get("filename") or "").strip(),
                "error": str(target.get("error") or "").strip(),
            }
            if item["path"] and item not in invalid_fragments:
                invalid_fragments.append(item)
    return {"invalid_fragments": invalid_fragments}


def _run_at_due(*, row: Dict[str, Any], now: dt.datetime) -> bool:
    schedule = row.get("schedule") if isinstance(row.get("schedule"), dict) else {}
    run_at = str(schedule.get("run_at") or "").strip()
    if not run_at:
        return True
    try:
        return sched._parse_utc(run_at) <= now
    except Exception:
        return True


def _age_minutes_from_state(state: Dict[str, Any], *, now: dt.datetime) -> int:
    for key in ("last_finished_utc", "last_started_utc", "updated_utc"):
        value = str(state.get(key) or "").strip()
        minutes = _minutes_since(value, now=now)
        if minutes is not None:
            return minutes
    return 10**9


def _stalled_dependency_head_candidates(*, jobs: List[Dict[str, Any]], now: dt.datetime) -> List[Dict[str, Any]]:
    jobs_by_id = {
        str(row.get("job_id") or "").strip(): row
        for row in jobs
        if str(row.get("job_id") or "").strip()
    }
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in jobs:
        if not bool(row.get("enabled")) or not _run_at_due(row=row, now=now):
            continue
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        reason = str(due.get("reason") or "").strip()
        if not reason.startswith("dependency_not_ok:"):
            continue
        head_job_id = reason.split(":", 1)[-1].strip()
        if not head_job_id:
            continue
        head = jobs_by_id.get(head_job_id) if isinstance(jobs_by_id.get(head_job_id), dict) else {}
        if not head or not bool(head.get("enabled")):
            continue
        head_state = head.get("state") if isinstance(head.get("state"), dict) else {}
        if str(head_state.get("active_run_id") or "").strip():
            continue
        head_due = head.get("due") if isinstance(head.get("due"), dict) else {}
        head_due_reason = str(head_due.get("reason") or "").strip()
        head_last_status = str(head_state.get("last_status") or "").strip().lower()
        if head_due_reason not in {"retry_backoff", "due_failed_slot_reusable", "not_due_yet"} and head_last_status not in {
            "blocked",
            "failed_verification",
            "error",
        }:
            continue
        idle_minutes = _age_minutes_from_state(head_state, now=now)
        if idle_minutes < _PROGRESS_RECOVERY_IDLE_MINUTES:
            continue
        entry = grouped.setdefault(
            head_job_id,
            {
                "head_job_id": head_job_id,
                "head_due_reason": head_due_reason,
                "head_last_status": head_last_status,
                "idle_minutes": idle_minutes,
                "blocked_dependents": [],
            },
        )
        entry["idle_minutes"] = max(int(entry.get("idle_minutes") or 0), idle_minutes)
        entry["blocked_dependents"].append(str(row.get("job_id") or "").strip())
    return sorted(
        grouped.values(),
        key=lambda row: (-len(list(row.get("blocked_dependents") or [])), -int(row.get("idle_minutes") or 0), str(row.get("head_job_id") or "")),
    )


def _maybe_restore_scheduler_progress(
    *,
    repo_root: Path,
    mode: str,
    jobs: List[Dict[str, Any]],
    now: dt.datetime,
    active_job_ids: set[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    findings: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    current_jobs = list(jobs)
    if mode != "selfheal" or active_job_ids:
        return findings, actions, current_jobs
    candidates = _stalled_dependency_head_candidates(jobs=current_jobs, now=now)
    if not candidates:
        return findings, actions, current_jobs
    recovered = False
    for candidate in candidates[:_PROGRESS_RECOVERY_MAX_HEADS]:
        head_job_id = str(candidate.get("head_job_id") or "").strip()
        if not head_job_id:
            continue
        request = sched.request_run_now(
            head_job_id,
            repo_root=repo_root,
            request_source="watchdog_progress_recovery",
        )
        actions.append(
            {
                "action": "resume_stalled_dependency_head",
                "ok": bool(request.get("ok")),
                "job_id": head_job_id,
                "request": request,
                "blocked_dependents": list(candidate.get("blocked_dependents") or []),
                "idle_minutes": int(candidate.get("idle_minutes") or 0),
            }
        )
        if not bool(request.get("ok")):
            continue
        tick = sched.scheduler_tick(repo_root=repo_root, limit=10)
        actions.append(
            {
                "action": "scheduler_tick_for_progress_recovery",
                "ok": True,
                "job_id": head_job_id,
                "tick": tick,
            }
        )
        current_jobs = sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())
        active_after = {
            str(row.get("job_id") or "").strip()
            for row in current_jobs
            if str(((row.get("state") or {}).get("active_run_id") or "")).strip()
        }
        if head_job_id in active_after or any(str(request.get("run_id") or "").strip() == str(((row.get("state") or {}).get("active_run_id") or "")).strip() for row in current_jobs):
            recovered = True
            break
        head_after = next((row for row in current_jobs if str(row.get("job_id") or "").strip() == head_job_id), {})
        head_after_due = head_after.get("due") if isinstance(head_after.get("due"), dict) else {}
        if str(head_after_due.get("reason") or "").strip() == "run_now_requested":
            recovered = True
            break
    if not recovered:
        findings.append(
            {
                "kind": "stalled_progress_chain_unrecovered",
                "details": {
                    "head_job_ids": [str(row.get("head_job_id") or "") for row in candidates[:_PROGRESS_RECOVERY_MAX_HEADS]],
                    "idle_threshold_minutes": _PROGRESS_RECOVERY_IDLE_MINUTES,
                },
            }
        )
    return findings, actions, current_jobs


def _collapsed_root_causes(*, findings: List[Dict[str, Any]]) -> list[Dict[str, Any]]:
    grouped: dict[str, Dict[str, Any]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or "").strip()
        if kind in {
            "delegation_proof_refresh_needed",
            "watchdog_latest_report_pointer_needs_repair",
        }:
            root = "derived_truth_refresh"
            summary = "Derived watchdog-owned surfaces need refresh or pointer repair."
            risk = "low"
            safe = True
        elif kind in {
            "plane_truth_selfheal_needed",
        }:
            root = "plane_truth_reconciliation_request"
            summary = "Canonical recovery or task-register projection state needs bounded reconciliation through a scheduler-managed maintenance path."
            risk = "medium"
            safe = False
        elif kind in {
            "truth_ambiguous",
        }:
            root = "truth_ambiguous"
            summary = "Truth reconciliation could not safely collapse conflicting evidence."
            risk = "high"
            safe = False
        elif kind in {
            "plane_truth_conflict_detected_but_not_auto_repairable",
        }:
            root = "nonrepairable_plane_truth_conflict"
            summary = "The plane detected a truth conflict that requires operator review instead of automatic repair."
            risk = "medium"
            safe = False
        elif kind in {
            "stale_lock",
            "orphan_running_state",
            "dependency_blocked_chain",
            "global_concurrency_cap_starvation",
        }:
            root = "execution_state_disagreement"
            summary = "Scheduled execution state disagrees with live ownership or dependency truth."
            risk = "medium"
            safe = False
        elif kind in {
            "watchdog_repeat_repair_exhausted",
            "repeated_job_failure_signature",
        }:
            root = "repeat_watchdog_job_problem"
            summary = "The same job is repeating the same failure signature without durable forward progress."
            risk = "high"
            safe = False
        elif kind in {
            "runtime_storage_low",
            "runtime_storage_critical",
            "runtime_storage_check_failed",
        }:
            root = "runtime_storage_health"
            summary = "Runtime storage health is degraded and may block future receipts or repairs."
            risk = "high"
            safe = False
        elif kind in {
            "watchdog_timer_inactive",
            "watchdog_timer_disabled",
            "watchdog_timer_stale_trigger",
            "watchdog_timers_not_installed",
            "watchdog_timer_status_unavailable",
        }:
            root = "watchdog_timer_drift"
            summary = "Watchdog host timers are missing, inactive, or drifting."
            risk = "medium"
            safe = True
        elif kind in {
            "commit_journal_invalid_fragments",
        }:
            root = "commit_journal_hygiene"
            summary = "Commit journal fragments need quarantine or repair."
            risk = "low"
            safe = True
        elif kind in {
            "runtime_residue_cleanup_needed",
        }:
            root = "runtime_residue_hygiene"
            summary = "Runtime residue cleanup is needed for stale temporary artifacts."
            risk = "low"
            safe = True
        elif kind in {
            "stale_scheduler_task_register_residue",
        }:
            root = "stale_scheduler_task_register_residue"
            summary = "The plane still has stale scheduler or swarm residue in the task register."
            risk = "medium"
            safe = True
        else:
            root = kind or "unknown"
            summary = f"Unresolved watchdog finding: {kind or 'unknown'}."
            risk = "medium"
            safe = False
        row = grouped.setdefault(
            root,
            {
                "condition_id": root,
                "summary": summary,
                "risk_level": risk,
                "safe_for_agent_handoff": safe,
                "findings": [],
            },
        )
        row["findings"].append(kind)
    out: list[Dict[str, Any]] = []
    for row in grouped.values():
        row["finding_count"] = len(row["findings"])
        row["findings"] = sorted(set(row["findings"]))
        out.append(row)
    return sorted(out, key=lambda row: (str(row.get("risk_level") or ""), str(row.get("condition_id") or "")))


def _write_escalation_bundles(*, repo_root: Path, mode: str, now: dt.datetime, health: Dict[str, Any], findings: List[Dict[str, Any]]) -> list[Dict[str, Any]]:
    if str(health.get("health") or "") != "degraded":
        return []
    bundles: list[Dict[str, Any]] = []
    for root in _collapsed_root_causes(findings=findings):
        evidence = [
            finding
            for finding in findings
            if str(finding.get("kind") or "") in set(root.get("findings") or [])
        ]
        bundle = {
            "schema_version": "scheduler_watchdog_escalation_v1",
            "generated_utc": _utc_iso(now),
            "generated_vienna": _vienna_iso(now),
            "mode": mode,
            "condition_id": str(root.get("condition_id") or ""),
            "diagnosis": str(root.get("summary") or ""),
            "health_reason": str(health.get("reason") or ""),
            "risk_level": str(root.get("risk_level") or "medium"),
            "safe_for_agent_handoff": bool(root.get("safe_for_agent_handoff")),
            "affected_truth_owners": sorted({
                "scheduler_state" if any(str(f.get("kind") or "") in {"stale_lock", "orphan_running_state", "dependency_blocked_chain"} for f in evidence) else "",
                "operator_snapshot" if any(str(f.get("kind") or "") == "plane_truth_selfheal_needed" for f in evidence) else "",
                "delegation_proof" if any(str(f.get("kind") or "") == "delegation_proof_refresh_needed" for f in evidence) else "",
                "watchdog_runtime" if any(str(f.get("kind") or "").startswith("watchdog_") for f in evidence) else "",
                "runtime_storage" if any(str(f.get("kind") or "").startswith("runtime_storage_") for f in evidence) else "",
                "commit_journal" if any(str(f.get("kind") or "") == "commit_journal_invalid_fragments" for f in evidence) else "",
            } - {""}),
            "recommended_next_action": (
                "bounded_agent_followup"
                if bool(root.get("safe_for_agent_handoff"))
                else "operator_or_code_repair_review"
            ),
            "evidence": evidence,
        }
        name = f"{_utc_iso(now).replace(':', '').replace('-', '')}__{str(root.get('condition_id') or 'unknown')}.json"
        path = (_watchdog_escalation_root(repo_root) / name).resolve()
        _write_json(path, bundle)
        bundle["bundle_path"] = str(path)
        bundles.append(bundle)
    latest = {
        "schema_version": "scheduler_watchdog_escalation_index_v1",
        "generated_utc": _utc_iso(now),
        "mode": mode,
        "bundles": [
            {
                "condition_id": str(bundle.get("condition_id") or ""),
                "bundle_path": str(bundle.get("bundle_path") or ""),
                "risk_level": str(bundle.get("risk_level") or ""),
                "safe_for_agent_handoff": bool(bundle.get("safe_for_agent_handoff")),
            }
            for bundle in bundles
        ],
    }
    _write_json((_watchdog_escalation_root(repo_root) / f"latest_{mode}.json").resolve(), latest)
    return bundles




_WATCHDOG_ALLOWED_DIRECT_SURFACE_REPAIRS = {
    "refresh_delegation_proof",
    "repair_watchdog_latest_report_pointer",
    "resume_stalled_dependency_head",
    "scheduler_tick_for_progress_recovery",
    "execute_bounded_codex_escalation",
}

_WATCHDOG_MAINTENANCE_FACT_PRECEDENCE: Dict[str, List[Dict[str, Any]]] = {
    "scheduler_execution_state": [
        {"source": "scheduled_job_state", "owner": "scheduler_state", "trust_rank": 100, "canonical": True},
        {"source": "active_job_claim", "owner": "scheduler_state", "trust_rank": 95, "canonical": True},
        {"source": "pipeline_healer_report", "owner": "pipeline_healer", "trust_rank": 60, "canonical": False},
        {"source": "operator_snapshot", "owner": "operator_snapshot", "trust_rank": 40, "canonical": False},
    ],
    "plane_truth_projection": [
        {"source": "lab_recovery", "owner": "lab_recovery", "trust_rank": 100, "canonical": True},
        {"source": "task_register", "owner": "lab_task_register", "trust_rank": 95, "canonical": True},
        {"source": "operator_snapshot", "owner": "operator_snapshot", "trust_rank": 40, "canonical": False},
    ],
    "derived_watchdog_reports": [
        {"source": "watchdog_reports", "owner": "watchdog", "trust_rank": 90, "canonical": True},
        {"source": "delegation_proof", "owner": "delegation_proof", "trust_rank": 80, "canonical": True},
        {"source": "operator_snapshot", "owner": "operator_snapshot", "trust_rank": 30, "canonical": False},
    ],
    "watchdog_timer_state": [
        {"source": "watchdog_timer", "owner": "watchdog_timer", "trust_rank": 100, "canonical": True},
        {"source": "watchdog_reports", "owner": "watchdog", "trust_rank": 50, "canonical": False},
    ],
    "commit_journal_integrity": [
        {"source": "commit_journal", "owner": "commit_journal", "trust_rank": 100, "canonical": True},
        {"source": "watchdog_report", "owner": "watchdog", "trust_rank": 30, "canonical": False},
    ],
    "runtime_storage_health": [
        {"source": "disk_usage", "owner": "runtime_storage", "trust_rank": 100, "canonical": True},
        {"source": "watchdog_report", "owner": "watchdog", "trust_rank": 30, "canonical": False},
    ],
    "runtime_residue_hygiene": [
        {"source": "runtime_reports", "owner": "runtime_reports", "trust_rank": 90, "canonical": True},
        {"source": "watchdog_report", "owner": "watchdog", "trust_rank": 30, "canonical": False},
    ],
    "task_register_projection": [
        {"source": "task_register", "owner": "lab_task_register", "trust_rank": 100, "canonical": True},
        {"source": "operator_snapshot", "owner": "operator_snapshot", "trust_rank": 40, "canonical": False},
    ],
    "watchdog_truth_reconciliation": [
        {"source": "watchdog_truth_engine", "owner": "watchdog", "trust_rank": 100, "canonical": True},
    ],
}

_WATCHDOG_FINDING_FACT_CLASS: Dict[str, tuple[str, str]] = {
    "stale_lock": ("scheduler_execution_state", "active_job_claim"),
    "orphan_running_state": ("scheduler_execution_state", "scheduled_job_state"),
    "terminal_receipt_state_conflict": ("scheduler_execution_state", "scheduled_job_state"),
    "dependency_blocked_chain": ("scheduler_execution_state", "scheduled_job_state"),
    "pipeline_healer_stale": ("scheduler_execution_state", "pipeline_healer_report"),
    "plane_truth_selfheal_needed": ("plane_truth_projection", "operator_snapshot"),
    "plane_truth_conflict_detected_but_not_auto_repairable": ("plane_truth_projection", "operator_snapshot"),
    "delegation_proof_refresh_needed": ("derived_watchdog_reports", "delegation_proof"),
    "watchdog_latest_report_pointer_needs_repair": ("derived_watchdog_reports", "watchdog_reports"),
    "watchdog_timer_inactive": ("watchdog_timer_state", "watchdog_timer"),
    "watchdog_timer_disabled": ("watchdog_timer_state", "watchdog_timer"),
    "watchdog_timer_stale_trigger": ("watchdog_timer_state", "watchdog_timer"),
    "watchdog_timers_not_installed": ("watchdog_timer_state", "watchdog_timer"),
    "watchdog_timer_status_unavailable": ("watchdog_timer_state", "watchdog_timer"),
    "commit_journal_invalid_fragments": ("commit_journal_integrity", "commit_journal"),
    "runtime_storage_low": ("runtime_storage_health", "disk_usage"),
    "runtime_storage_critical": ("runtime_storage_health", "disk_usage"),
    "runtime_storage_check_failed": ("runtime_storage_health", "disk_usage"),
    "runtime_residue_cleanup_needed": ("runtime_residue_hygiene", "runtime_reports"),
    "stale_scheduler_task_register_residue": ("task_register_projection", "task_register"),
    "watchdog_repeat_repair_exhausted": ("scheduler_execution_state", "pipeline_healer_report"),
}

_WATCHDOG_AMBIGUOUS_FINDINGS = {
    "plane_truth_conflict_detected_but_not_auto_repairable",
}

_WATCHDOG_PLAYBOOK_IDS = {
    "derived_truth_refresh": "watchdog-derived-surface-refresh",
    "plane_truth_reconciliation_request": "watchdog-plane-truth-reconciliation-request",
    "truth_ambiguous": "watchdog-truth-ambiguity-containment",
    "nonrepairable_plane_truth_conflict": "watchdog-truth-conflict-containment",
    "execution_state_disagreement": "watchdog-scheduler-execution-reconcile",
    "runtime_storage_health": "watchdog-storage-containment",
    "watchdog_timer_drift": "watchdog-timer-containment",
    "commit_journal_hygiene": "watchdog-commit-journal-repair-request",
    "runtime_residue_hygiene": "watchdog-runtime-residue-repair-request",
    "stale_scheduler_task_register_residue": "watchdog-task-register-residue-repair-request",
    "repeat_watchdog_job_problem": "watchdog-repeat-job-problem-codex-escalation",
}

_WATCHDOG_SCHEDULER_ACTIONS = {
    "plane_truth_reconciliation_request": [
        "reconcile_plane_truth_projection",
    ],
    "execution_state_disagreement": [
        "reconcile_scheduler_runtime_state",
        "clear_stale_runtime_claims",
        "scheduler_pipeline_healer_full_recovery",
    ],
    "commit_journal_hygiene": ["quarantine_invalid_commit_journal_fragments"],
    "runtime_residue_hygiene": ["cleanup_runtime_residue"],
    "stale_scheduler_task_register_residue": ["cleanup_stale_scheduler_task_register_residue"],
}

_WATCHDOG_CODEX_SCOPE = {
    "plane_truth_reconciliation_request": [
        "infra/bin/scheduler_watchdog.py",
        "infra/webui/server_lab_assistant_operator_snapshot.py",
        "infra/webui/server_lab_assistant_recovery.py",
        "infra/webui/server_lab_assistant_task_register.py",
    ],
    "truth_ambiguous": [
        "infra/bin/scheduler_watchdog.py",
        "infra/webui/server_lab_assistant_operator_snapshot.py",
        "infra/webui/server_lab_assistant_recovery.py",
        "infra/webui/server_lab_assistant_task_register.py",
    ],
    "nonrepairable_plane_truth_conflict": [
        "infra/bin/scheduler_watchdog.py",
        "infra/webui/server_lab_assistant_operator_snapshot.py",
        "infra/webui/server_lab_assistant_recovery.py",
        "infra/webui/server_lab_assistant_task_register.py",
    ],
    "execution_state_disagreement": [
        "infra/bin/scheduler_watchdog.py",
        "infra/controlplane/server_maintenance.py",
        "infra/controlplane/scheduled_jobs_parts/part03__compiled_execution_shadow_bundle_for_job.py",
    ],
    "commit_journal_hygiene": [
        "infra/bin/scheduler_watchdog.py",
        "tools/git/support/commit_journal.py",
    ],
    "watchdog_timer_drift": [
        "infra/bin/scheduler_watchdog.py",
        "infra/bin/scheduler_watchdog_timer.py",
        "infra/bin/scheduler_watchdog_audit.sh",
        "infra/bin/scheduler_watchdog_selfheal.sh",
    ],
    "runtime_storage_health": [
        "infra/bin/scheduler_watchdog.py",
        "infra/bin/server_status.sh",
    ],
    "runtime_residue_hygiene": [
        "infra/bin/scheduler_watchdog.py",
    ],
    "stale_scheduler_task_register_residue": [
        "infra/bin/scheduler_watchdog.py",
        "infra/webui/server_lab_assistant_task_register.py",
        "infra/webui/server_lab_assistant_recovery.py",
    ],
    "repeat_watchdog_job_problem": [
        "infra/bin/scheduler_watchdog.py",
        "tools/bin/scheduler_pipeline_healer.py",
        "infra/controlplane/scheduled_jobs_parts/part09__execute_job.py",
    ],
}

_WATCHDOG_CODEX_VALIDATION_HINTS = {
    "plane_truth_reconciliation_request": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
        "pytest tests/control_plane/webui/test_lab_assistant_operator_snapshot.py -q",
    ],
    "truth_ambiguous": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
        "pytest tests/control_plane/webui/test_webui_lab_operations_console.py -q",
    ],
    "nonrepairable_plane_truth_conflict": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
        "pytest tests/control_plane/webui/test_lab_assistant_operator_snapshot.py -q",
    ],
    "execution_state_disagreement": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
        "pytest tests/control_plane/test_scheduled_jobs.py -q -k stale_claims",
    ],
    "commit_journal_hygiene": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
        "pytest tests/control_plane/webui/test_admin_commit_journal_status.py -q",
    ],
    "watchdog_timer_drift": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
    ],
    "runtime_storage_health": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
    ],
    "runtime_residue_hygiene": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
    ],
    "stale_scheduler_task_register_residue": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
        "pytest tests/control_plane/webui/test_lab_assistant_task_register.py -q -k residue",
    ],
    "repeat_watchdog_job_problem": [
        "pytest tests/control_plane/test_scheduler_watchdog.py -q",
        "pytest tests/control_plane/test_scheduled_jobs.py -q -k continuation_bootstrap",
    ],
}


def _watchdog_maintenance_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "scheduler_watchdog" / "maintenance").resolve()


def _watchdog_maintenance_runs_root(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "runs").resolve()


def _watchdog_maintenance_decisions_root(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "decisions").resolve()


def _watchdog_scheduler_work_items_root(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "scheduler_work_items").resolve()


def _watchdog_containments_root(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "containments").resolve()


def _watchdog_incidents_root(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "incidents").resolve()


def _watchdog_predictions_root(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "predictions").resolve()


def _watchdog_codex_runs_root(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "codex_runs").resolve()


def _latest_codex_receipt_for_condition(*, repo_root: Path, condition_id: str, binding: Dict[str, Any] | None = None) -> Dict[str, Any]:
    safe_condition = str(condition_id or "").strip()
    if not safe_condition:
        return {}
    root = _watchdog_codex_runs_root(repo_root)
    if not root.exists():
        return {}
    target_binding = dict(binding or {})
    matches = sorted(root.glob(f"*__{safe_condition}.json"))
    for path in reversed(matches):
        row = _read_json(path)
        if not row:
            continue
        receipt_binding = dict(row.get("binding") or {})
        if target_binding and (
            str(receipt_binding.get("type") or "").strip() != str(target_binding.get("type") or "").strip()
            or str(receipt_binding.get("id") or "").strip() != str(target_binding.get("id") or "").strip()
        ):
            continue
        row["_receipt_path"] = str(path)
        return row
    return {}


def _codex_receipt_is_active(receipt: Dict[str, Any], *, repo_root: Path) -> bool:
    if not receipt:
        return False
    turn_id = str(receipt.get("turn_id") or "").strip()
    if not turn_id:
        return False
    try:
        turn = _lab_turns.read_turn(Path(repo_root).resolve(), turn_id=turn_id)
    except Exception:
        return False
    return str(turn.get("state") or "").strip().lower() in _lab_turns.DIRECT_TURN_ACTIVE_STATES


def _watchdog_maintenance_events_path(repo_root: Path) -> Path:
    return (_watchdog_maintenance_root(repo_root) / "events.jsonl").resolve()


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path: Path, *, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if len(lines) > int(limit):
        lines = lines[-int(limit) :]
    out: List[Dict[str, Any]] = []
    for line in lines:
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _watchdog_authority_boundary() -> Dict[str, Any]:
    return _watchdog_contract.watchdog_authority_boundary()


def _compact_utc(now: dt.datetime) -> str:
    return _utc_iso(now).replace(":", "").replace("-", "")


def _maintenance_run_id(*, now: dt.datetime, mode: str, repo_root: Path) -> str:
    base = f"wdrun_{mode}_{_compact_utc(now)}"
    path = (_watchdog_maintenance_runs_root(repo_root) / f"{base}.json").resolve()
    if not path.exists():
        return base
    suffix = 2
    while True:
        candidate = f"{base}__{suffix:02d}"
        if not (_watchdog_maintenance_runs_root(repo_root) / f"{candidate}.json").exists():
            return candidate
        suffix += 1


def _reconcile_watchdog_truth(*, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    grouped: Dict[str, Dict[str, Any]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        kind = str(finding.get("kind") or finding.get("finding_type") or "").strip()
        if not kind:
            continue
        fact_class, observed_source = _WATCHDOG_FINDING_FACT_CLASS.get(kind, ("watchdog_truth_reconciliation", kind))
        row = grouped.setdefault(
            fact_class,
            {
                "fact_class": fact_class,
                "precedence": list(_WATCHDOG_MAINTENANCE_FACT_PRECEDENCE.get(fact_class) or []),
                "finding_kinds": [],
                "observed_sources": set(),
                "canonical_owner": str(((_WATCHDOG_MAINTENANCE_FACT_PRECEDENCE.get(fact_class) or [{}])[0]).get("owner") or ""),
            },
        )
        row["finding_kinds"].append(kind)
        row["observed_sources"].add(observed_source)
    facts: List[Dict[str, Any]] = []
    ambiguous_fact_classes: List[str] = []
    for fact_class in sorted(grouped):
        row = grouped[fact_class]
        finding_kinds = sorted(set(str(x) for x in row.get("finding_kinds") or [] if str(x)))
        observed_sources = sorted(set(str(x) for x in row.get("observed_sources") or [] if str(x)))
        state = "truth_degraded"
        ambiguity_reason = ""
        if any(kind in _WATCHDOG_AMBIGUOUS_FINDINGS for kind in finding_kinds):
            state = "truth_ambiguous"
            ambiguity_reason = "canonical_conflict_requires_review"
            ambiguous_fact_classes.append(fact_class)
        facts.append(
            {
                "fact_class": fact_class,
                "state": state,
                "canonical_owner": str(row.get("canonical_owner") or ""),
                "precedence": list(row.get("precedence") or []),
                "observed_sources": observed_sources,
                "finding_kinds": finding_kinds,
                "ambiguity_reason": ambiguity_reason,
                "fallback_order": [str(entry.get("source") or "") for entry in list(row.get("precedence") or [])],
            }
        )
    overall_state = "truth_ok"
    if facts:
        overall_state = "truth_ambiguous" if ambiguous_fact_classes else "truth_degraded"
    return {
        "schema_version": "scheduler_watchdog_truth_reconciliation_v1",
        "overall_state": overall_state,
        "fact_count": len(facts),
        "ambiguous_fact_count": len(ambiguous_fact_classes),
        "ambiguous_fact_classes": ambiguous_fact_classes,
        "facts": facts,
    }


def _playbook_targets_for_condition(condition_id: str, *, allow_pipeline_healer: bool) -> List[str]:
    actions = list(_WATCHDOG_SCHEDULER_ACTIONS.get(condition_id) or [])
    if not allow_pipeline_healer and "scheduler_pipeline_healer_full_recovery" in actions:
        actions = [action for action in actions if action != "scheduler_pipeline_healer_full_recovery"]
    return actions


def _build_watchdog_playbook_artifacts(
    *,
    repo_root: Path,
    mode: str,
    now: dt.datetime,
    run_id: str,
    root_causes: List[Dict[str, Any]],
    findings: List[Dict[str, Any]],
    truth_reconciliation: Dict[str, Any],
    direct_actions: List[Dict[str, Any]],
    allow_pipeline_healer: bool,
    active_job_ids: set[str],
) -> Dict[str, Any]:
    decisions: List[Dict[str, Any]] = []
    scheduler_work_items: List[Dict[str, Any]] = []
    containments: List[Dict[str, Any]] = []
    artifact_actions: List[Dict[str, Any]] = []
    truth_ambiguous = str(truth_reconciliation.get("overall_state") or "") == "truth_ambiguous"
    direct_action_ids = [
        str(action.get("action") or "")
        for action in direct_actions
        if str(action.get("action") or "") in _WATCHDOG_ALLOWED_DIRECT_SURFACE_REPAIRS
    ]
    decisions_root = _watchdog_maintenance_decisions_root(repo_root)
    work_items_root = _watchdog_scheduler_work_items_root(repo_root)
    containments_root = _watchdog_containments_root(repo_root)
    for idx, root in enumerate(root_causes, start=1):
        condition_id = str(root.get("condition_id") or "").strip() or f"condition_{idx:02d}"
        playbook_id = _WATCHDOG_PLAYBOOK_IDS.get(condition_id, f"watchdog-{condition_id.replace('_', '-')}")
        decision_id = f"{run_id}__decision_{idx:02d}"
        evidence_kinds = list(root.get("findings") or [])
        evidence = [
            finding
            for finding in findings
            if str(finding.get("kind") or "") in set(evidence_kinds)
        ]
        base = {
            "schema_version": "scheduler_watchdog_playbook_decision_v1",
            "generated_utc": _utc_iso(now),
            "generated_vienna": _vienna_iso(now),
            "mode": mode,
            "run_id": run_id,
            "decision_id": decision_id,
            "condition_id": condition_id,
            "playbook_id": playbook_id,
            "diagnosis": str(root.get("summary") or ""),
            "risk_level": str(root.get("risk_level") or "medium"),
            "evidence_finding_kinds": evidence_kinds,
            "authority_boundary": {
                "watchdog_execution_allowed": False,
                "scheduler_required_for_repair": True,
            },
        }
        scheduler_actions = _playbook_targets_for_condition(condition_id, allow_pipeline_healer=allow_pipeline_healer)
        if condition_id == "derived_truth_refresh":
            decision = {
                **base,
                "decision_kind": "watchdog_owned_surface_refresh" if direct_action_ids else "monitor_only",
                "allowed_direct_actions": direct_action_ids,
                "notes": "Only watchdog-owned derived surfaces may refresh directly; canonical scheduler truth remains untouched.",
            }
        elif truth_ambiguous or condition_id in {"truth_ambiguous", "nonrepairable_plane_truth_conflict", "runtime_storage_health", "watchdog_timer_drift"}:
            containment_id = f"{run_id}__contain_{len(containments) + 1:02d}"
            reason = "truth_ambiguous" if truth_ambiguous or condition_id == "truth_ambiguous" else "unsafe_for_automatic_repair"
            decision = {
                **base,
                "decision_kind": "containment",
                "containment_reason": reason,
                "containment_id": containment_id,
                "next_authority": "operator_or_bounded_codex_review",
            }
            containment = {
                "schema_version": "scheduler_watchdog_containment_v1",
                "generated_utc": _utc_iso(now),
                "generated_vienna": _vienna_iso(now),
                "run_id": run_id,
                "containment_id": containment_id,
                "condition_id": condition_id,
                "decision_id": decision_id,
                "reason": reason,
                "diagnosis": str(root.get("summary") or ""),
                "evidence_finding_kinds": evidence_kinds,
                "state": "contained",
                "execution_authority": "scheduler",
                "truth_closure_requires": ["verified_receipt_or_manual_review"],
            }
            containment_path = (containments_root / f"{containment_id}.json").resolve()
            _write_json(containment_path, containment)
            containment["artifact_path"] = str(containment_path)
            containments.append(containment)
            artifact_actions.append({
                "action": "record_containment",
                "ok": True,
                "containment_id": containment_id,
                "condition_id": condition_id,
                "path": str(containment_path),
            })
        elif condition_id in {"repeat_watchdog_job_problem"}:
            containment_id = f"{run_id}__contain_{len(containments) + 1:02d}"
            decision = {
                **base,
                "decision_kind": "containment",
                "containment_reason": "repeat_watchdog_repair_exhausted",
                "containment_id": containment_id,
                "next_authority": "bounded_codex_review",
            }
            containment = {
                "schema_version": "scheduler_watchdog_containment_v1",
                "generated_utc": _utc_iso(now),
                "generated_vienna": _vienna_iso(now),
                "run_id": run_id,
                "containment_id": containment_id,
                "condition_id": condition_id,
                "decision_id": decision_id,
                "reason": "repeat_watchdog_repair_exhausted",
                "diagnosis": str(root.get("summary") or ""),
                "evidence_finding_kinds": evidence_kinds,
                "state": "contained",
                "execution_authority": "scheduler",
                "truth_closure_requires": ["bounded_codex_receipt_or_manual_review"],
            }
            containment_path = (containments_root / f"{containment_id}.json").resolve()
            _write_json(containment_path, containment)
            containment["artifact_path"] = str(containment_path)
            containments.append(containment)
            artifact_actions.append({
                "action": "record_containment",
                "ok": True,
                "containment_id": containment_id,
                "condition_id": condition_id,
                "path": str(containment_path),
            })
        elif scheduler_actions:
            disposition = "ready_for_scheduler" if mode == "selfheal" and not active_job_ids else ("queue_when_scheduler_is_idle" if active_job_ids else "audit_only_recommendation")
            if condition_id == "execution_state_disagreement":
                action_targets = _execution_state_requested_action_targets(findings=evidence)
            elif condition_id == "commit_journal_hygiene":
                action_targets = _commit_journal_requested_action_targets(findings=evidence)
            elif condition_id == "stale_scheduler_task_register_residue":
                action_targets = _scheduler_residue_requested_action_targets(findings=evidence)
            else:
                action_targets = {}
            decision = {
                **base,
                "decision_kind": "monitor_only",
                "scheduler_disposition": disposition,
                "recommended_actions": scheduler_actions,
                "next_authority": "operator_or_scheduler",
                "notes": "Watchdog records the diagnosis but does not create scheduler work-item backlog for ordinary continuity or hygiene cases.",
            }
            if action_targets and any(list(action_targets.get(key) or []) for key in action_targets):
                decision["recommended_action_targets"] = action_targets
        else:
            decision = {
                **base,
                "decision_kind": "monitor_only",
                "notes": "No bounded self-heal playbook was selected for this condition.",
            }
        decision_path = (decisions_root / f"{decision_id}.json").resolve()
        _write_json(decision_path, decision)
        decision["artifact_path"] = str(decision_path)
        decisions.append(decision)
        artifact_actions.append({
            "action": "record_playbook_decision",
            "ok": True,
            "decision_id": decision_id,
            "condition_id": condition_id,
            "path": str(decision_path),
        })
    return {
        "playbook_decisions": decisions,
        "scheduler_work_items": scheduler_work_items,
        "containments": containments,
        "actions": artifact_actions,
    }


def _build_watchdog_incidents_and_predictions(
    *,
    repo_root: Path,
    now: dt.datetime,
    run_id: str,
    root_causes: List[Dict[str, Any]],
    truth_reconciliation: Dict[str, Any],
    scope: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    history = _read_jsonl(_watchdog_maintenance_events_path(repo_root), limit=200)
    aggregates: Dict[str, Dict[str, Any]] = {}
    current_condition_ids = {str(row.get("condition_id") or "").strip() for row in root_causes if str(row.get("condition_id") or "").strip()}

    def _absorb(event: Dict[str, Any], *, current: bool = False) -> None:
        event_utc = str(event.get("generated_utc") or event.get("utc") or _utc_iso(now))
        try:
            event_dt = sched._parse_utc(event_utc)
        except Exception:
            event_dt = now
        age = now - event_dt
        recent_24h = age <= dt.timedelta(hours=24)
        recent_7d = age <= dt.timedelta(days=7)
        for root in [dict(row) for row in list(event.get("root_causes") or []) if isinstance(row, dict)]:
            condition_id = str(root.get("condition_id") or "").strip()
            if not condition_id:
                continue
            row = aggregates.setdefault(
                condition_id,
                {
                    "condition_id": condition_id,
                    "first_seen_utc": event_utc,
                    "last_seen_utc": event_utc,
                    "occurrence_count": 0,
                    "recent_24h_count": 0,
                    "recent_7d_count": 0,
                    "risk_level": str(root.get("risk_level") or "medium"),
                    "evidence_run_ids": [],
                    "current_present": False,
                },
            )
            row["occurrence_count"] += 1
            if recent_24h:
                row["recent_24h_count"] += 1
            if recent_7d:
                row["recent_7d_count"] += 1
            if event_dt < sched._parse_utc(str(row.get("first_seen_utc") or event_utc)):
                row["first_seen_utc"] = event_utc
            if event_dt >= sched._parse_utc(str(row.get("last_seen_utc") or event_utc)):
                row["last_seen_utc"] = event_utc
                row["risk_level"] = str(root.get("risk_level") or row.get("risk_level") or "medium")
            run_ref = str(event.get("run_id") or "").strip()
            if run_ref and run_ref not in row["evidence_run_ids"]:
                row["evidence_run_ids"].append(run_ref)
            if current:
                row["current_present"] = True

    for event in history:
        if not _watchdog_scope_matches(scope, event.get("scope") if isinstance(event.get("scope"), dict) else {}):
            continue
        _absorb(event)
    _absorb({"generated_utc": _utc_iso(now), "run_id": run_id, "root_causes": root_causes}, current=True)

    incidents: List[Dict[str, Any]] = []
    predictions: List[Dict[str, Any]] = []
    artifact_actions: List[Dict[str, Any]] = []
    incidents_root = _watchdog_incidents_root(repo_root)
    predictions_root = _watchdog_predictions_root(repo_root)
    truth_state = str(truth_reconciliation.get("overall_state") or "")
    for idx, condition_id in enumerate(sorted(aggregates), start=1):
        row = aggregates[condition_id]
        decayed_score = float(row.get("recent_24h_count") or 0) + 0.5 * max(0, int(row.get("recent_7d_count") or 0) - int(row.get("recent_24h_count") or 0))
        incident_id = f"incident__{condition_id}"
        state = "open" if row.get("current_present") else ("cooling" if int(row.get("recent_7d_count") or 0) > 0 else "resolved")
        incident = {
            "schema_version": "scheduler_watchdog_incident_v1",
            "generated_utc": _utc_iso(now),
            "generated_vienna": _vienna_iso(now),
            "incident_id": incident_id,
            "condition_id": condition_id,
            "state": state,
            "risk_level": str(row.get("risk_level") or "medium"),
            "first_seen_utc": str(row.get("first_seen_utc") or ""),
            "last_seen_utc": str(row.get("last_seen_utc") or ""),
            "occurrence_count": int(row.get("occurrence_count") or 0),
            "evidence_run_ids": list(row.get("evidence_run_ids") or [])[-20:],
            "maintenance_memory": {
                "recent_24h_count": int(row.get("recent_24h_count") or 0),
                "recent_7d_count": int(row.get("recent_7d_count") or 0),
                "decayed_score": decayed_score,
                "decay_policy": {
                    "within_24h_weight": 1.0,
                    "within_7d_weight": 0.5,
                    "older_than_7d_weight": 0.0,
                },
            },
            "current_truth_state": truth_state if row.get("current_present") else "historical_only",
        }
        incident_path = (incidents_root / f"{incident_id}.json").resolve()
        _write_json(incident_path, incident)
        incident["artifact_path"] = str(incident_path)
        incidents.append(incident)
        artifact_actions.append({
            "action": "record_incident",
            "ok": True,
            "incident_id": incident_id,
            "condition_id": condition_id,
            "path": str(incident_path),
        })
        if decayed_score >= 2.0:
            prediction_id = f"prediction__{condition_id}"
            confidence = "high" if int(row.get("recent_24h_count") or 0) >= 3 else "medium"
            prediction = {
                "schema_version": "scheduler_watchdog_prediction_v1",
                "generated_utc": _utc_iso(now),
                "generated_vienna": _vienna_iso(now),
                "prediction_id": prediction_id,
                "incident_id": incident_id,
                "condition_id": condition_id,
                "confidence": confidence,
                "predicted_recurrence": True,
                "priority_influence": "boost",
                "decayed_score": decayed_score,
                "present_evidence_authority_preserved": True,
            }
            prediction_path = (predictions_root / f"{prediction_id}.json").resolve()
            _write_json(prediction_path, prediction)
            prediction["artifact_path"] = str(prediction_path)
            predictions.append(prediction)
            artifact_actions.append({
                "action": "record_prediction",
                "ok": True,
                "prediction_id": prediction_id,
                "condition_id": condition_id,
                "path": str(prediction_path),
            })
    latest_incidents = {
        "schema_version": "scheduler_watchdog_incident_index_v1",
        "generated_utc": _utc_iso(now),
        "run_id": run_id,
        "scope": dict(scope or {}),
        "open_count": len([row for row in incidents if str(row.get("state") or "") == "open"]),
        "incidents": [
            {
                "incident_id": str(row.get("incident_id") or ""),
                "condition_id": str(row.get("condition_id") or ""),
                "state": str(row.get("state") or ""),
                "risk_level": str(row.get("risk_level") or ""),
                "artifact_path": str(row.get("artifact_path") or ""),
            }
            for row in incidents
        ],
    }
    latest_predictions = {
        "schema_version": "scheduler_watchdog_prediction_index_v1",
        "generated_utc": _utc_iso(now),
        "run_id": run_id,
        "scope": dict(scope or {}),
        "prediction_count": len(predictions),
        "predictions": [
            {
                "prediction_id": str(row.get("prediction_id") or ""),
                "condition_id": str(row.get("condition_id") or ""),
                "confidence": str(row.get("confidence") or ""),
                "artifact_path": str(row.get("artifact_path") or ""),
            }
            for row in predictions
        ],
    }
    incidents_root = _watchdog_incidents_root(repo_root)
    predictions_root = _watchdog_predictions_root(repo_root)
    _write_json((incidents_root / "latest.json").resolve(), latest_incidents)
    _write_json((predictions_root / "latest.json").resolve(), latest_predictions)
    scoped_incidents_path = _watchdog_scoped_latest_path(incidents_root, prefix="latest", suffix="", scope=scope)
    scoped_predictions_path = _watchdog_scoped_latest_path(predictions_root, prefix="latest", suffix="", scope=scope)
    if scoped_incidents_path is not None:
        _write_json(scoped_incidents_path, latest_incidents)
    if scoped_predictions_path is not None:
        _write_json(scoped_predictions_path, latest_predictions)
    return {
        "incidents": incidents,
        "predictions": predictions,
        "actions": artifact_actions,
    }


def _codex_escalation_class_for_condition(condition_id: str) -> str:
    if condition_id in {
        "plane_truth_reconciliation_request",
        "execution_state_disagreement",
        "commit_journal_hygiene",
        "stale_scheduler_task_register_residue",
        "repeat_watchdog_job_problem",
    }:
        return "bounded_repair_codex"
    return "advisory_codex"


def _write_bounded_codex_escalation_bundles(
    *,
    repo_root: Path,
    mode: str,
    now: dt.datetime,
    run_id: str,
    health: Dict[str, Any],
    findings: List[Dict[str, Any]],
    truth_reconciliation: Dict[str, Any],
    playbook_decisions: List[Dict[str, Any]],
    incidents: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    scope: Dict[str, Any] | None = None,
) -> List[Dict[str, Any]]:
    unresolved: List[Dict[str, Any]] = []
    truth_ambiguous = str(truth_reconciliation.get("overall_state") or "") == "truth_ambiguous"
    stalled_progress = any(str(row.get("kind") or "").strip() == "stalled_progress_chain_unrecovered" for row in findings if isinstance(row, dict))
    for decision in playbook_decisions:
        if not isinstance(decision, dict):
            continue
        kind = str(decision.get("decision_kind") or "").strip()
        scheduler_disposition = str(decision.get("scheduler_disposition") or "").strip()
        if kind == "containment":
            unresolved.append(decision)
            continue
        if kind == "monitor_only" and scheduler_disposition and scheduler_disposition != "ready_for_scheduler":
            unresolved.append(decision)
            continue
        if kind == "scheduler_execution_request" and str(decision.get("scheduler_disposition") or "") != "ready_for_scheduler":
            unresolved.append(decision)
            continue
        if (
            kind in {"scheduler_execution_request", "monitor_only"}
            and stalled_progress
            and str(decision.get("condition_id") or "").strip() == "execution_state_disagreement"
        ):
            unresolved.append(decision)
    if str(health.get("health") or "") == "degraded" and not unresolved and truth_ambiguous:
        unresolved.extend([row for row in playbook_decisions if str(row.get("condition_id") or "") in {"truth_ambiguous", "nonrepairable_plane_truth_conflict"}])
    if not unresolved and str(health.get("health") or "") != "degraded":
        return []
    bundles: List[Dict[str, Any]] = []
    seen_conditions: set[str] = set()
    escalation_root = _watchdog_escalation_root(repo_root)
    escalation_root.mkdir(parents=True, exist_ok=True)
    incident_by_condition = {str(row.get("condition_id") or ""): row for row in incidents if isinstance(row, dict)}
    prediction_by_condition = {str(row.get("condition_id") or ""): row for row in predictions if isinstance(row, dict)}
    for row in unresolved:
        condition_id = str(row.get("condition_id") or "").strip()
        if not condition_id or condition_id in seen_conditions:
            continue
        seen_conditions.add(condition_id)
        escalation_class = _codex_escalation_class_for_condition(condition_id)
        file_scope = list(_WATCHDOG_CODEX_SCOPE.get(condition_id) or ["infra/bin/scheduler_watchdog.py"])
        validation_commands = list(_WATCHDOG_CODEX_VALIDATION_HINTS.get(condition_id) or ["pytest tests/control_plane/test_scheduler_watchdog.py -q"])
        related_incident = dict(incident_by_condition.get(condition_id) or {})
        related_prediction = dict(prediction_by_condition.get(condition_id) or {})
        evidence = [
            finding
            for finding in findings
            if str(finding.get("kind") or "") in set(row.get("evidence_finding_kinds") or [])
        ]
        bundle = {
            "schema_version": "scheduler_watchdog_codex_escalation_v1",
            "generated_utc": _utc_iso(now),
            "generated_vienna": _vienna_iso(now),
            "mode": mode,
            "run_id": run_id,
            "scope": dict(scope or {}),
            "condition_id": condition_id,
            "escalation_class": escalation_class,
            "bounded_execution_mode": "bounded_patch" if escalation_class == "bounded_repair_codex" else "analysis_only",
            "diagnosis": str(row.get("diagnosis") or ""),
            "risk_level": str(row.get("risk_level") or "medium"),
            "safe_for_agent_handoff": True,
            "file_scope": file_scope,
            "success_bar": [
                "Keep edits inside the declared file scope.",
                "Do not mutate scheduler, task register, or recovery truth without producing a verified receipt.",
                "Produce explicit verification evidence for every claimed repair.",
                "After fixing the immediate issue, add a bounded watchdog improvement for this failure type so the plane is less likely to require Codex next time.",
            ],
            "watchdog_followup_goal": "reduce_repeat_codex_escalation_for_same_failure_type",
            "verification_required": True,
            "binding": dict((scope or {}).get("binding") or {"type": "task_id", "id": "wdg"}),
            "verification_requirements": {
                "commands": validation_commands,
                "operator_review_required": True,
                "receipt_required_for_truth_closure": True,
            },
            "truth_closure_requires": [
                "verified_receipt",
                "evidence_review",
            ],
            "evidence": evidence,
            "incident": related_incident,
            "prediction": related_prediction,
        }
        bundle_path = (escalation_root / f"{run_id}__{condition_id}.json").resolve()
        _write_json(bundle_path, bundle)
        bundle["bundle_path"] = str(bundle_path)
        bundles.append(bundle)
    latest = {
        "schema_version": "scheduler_watchdog_escalation_index_v2",
        "generated_utc": _utc_iso(now),
        "mode": mode,
        "run_id": run_id,
        "scope": dict(scope or {}),
        "bundles": [
            {
                "condition_id": str(bundle.get("condition_id") or ""),
                "bundle_path": str(bundle.get("bundle_path") or ""),
                "risk_level": str(bundle.get("risk_level") or ""),
                "safe_for_agent_handoff": bool(bundle.get("safe_for_agent_handoff")),
                "escalation_class": str(bundle.get("escalation_class") or ""),
            }
            for bundle in bundles
        ],
    }
    _write_json((escalation_root / f"latest_{mode}.json").resolve(), latest)
    scoped_latest = _watchdog_scoped_latest_path(escalation_root, prefix="latest_", suffix=mode, scope=scope)
    if scoped_latest is not None:
        _write_json(scoped_latest, latest)
    return bundles


def _write_watchdog_maintenance_run(
    *,
    repo_root: Path,
    now: dt.datetime,
    mode: str,
    run_id: str,
    summary: Dict[str, Any],
    health: Dict[str, Any],
    findings: List[Dict[str, Any]],
    direct_actions: List[Dict[str, Any]],
    truth_reconciliation: Dict[str, Any],
    root_causes: List[Dict[str, Any]],
    playbook_decisions: List[Dict[str, Any]],
    scheduler_work_items: List[Dict[str, Any]],
    containments: List[Dict[str, Any]],
    incidents: List[Dict[str, Any]],
    predictions: List[Dict[str, Any]],
    escalation_bundles: List[Dict[str, Any]],
    scope: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    run_payload = {
        "schema_version": "scheduler_watchdog_maintenance_run_v1",
        "run_id": run_id,
        "mode": mode,
        "scope": dict(scope or {}),
        "generated_utc": _utc_iso(now),
        "generated_vienna": _vienna_iso(now),
        "authority_boundary": _watchdog_authority_boundary(),
        "health": health,
        "summary": summary,
        "truth_reconciliation": truth_reconciliation,
        "root_causes": root_causes,
        "playbook_decisions": playbook_decisions,
        "scheduler_work_items": scheduler_work_items,
        "containments": containments,
        "incidents": incidents,
        "predictions": predictions,
        "escalation_bundles": [
            {
                "condition_id": str(row.get("condition_id") or ""),
                "escalation_class": str(row.get("escalation_class") or ""),
                "bundle_path": str(row.get("bundle_path") or ""),
            }
            for row in escalation_bundles
        ],
        "direct_watchdog_actions": [
            action
            for action in direct_actions
            if str(action.get("action") or "") in _WATCHDOG_ALLOWED_DIRECT_SURFACE_REPAIRS
        ],
        "findings": findings,
    }
    runs_root = _watchdog_maintenance_runs_root(repo_root)
    run_path = (runs_root / f"{run_id}.json").resolve()
    _write_json(run_path, run_payload)
    latest_path = (_watchdog_maintenance_root(repo_root) / f"latest_{mode}.json").resolve()
    _write_json(latest_path, run_payload)
    scoped_latest_path = _watchdog_scoped_latest_path(
        _watchdog_maintenance_root(repo_root),
        prefix="latest_",
        suffix=mode,
        scope=scope,
    )
    if scoped_latest_path is not None:
        _write_json(scoped_latest_path, run_payload)
    event = {
        "schema_version": "scheduler_watchdog_maintenance_event_v1",
        "generated_utc": _utc_iso(now),
        "run_id": run_id,
        "mode": mode,
        "scope": dict(scope or {}),
        "health": dict(health),
        "truth_overall_state": str(truth_reconciliation.get("overall_state") or ""),
        "root_causes": [
            {
                "condition_id": str(row.get("condition_id") or ""),
                "risk_level": str(row.get("risk_level") or ""),
                "finding_count": int(row.get("finding_count") or 0),
            }
            for row in root_causes
        ],
        "scheduler_work_item_count": len(scheduler_work_items),
        "containment_count": len(containments),
        "incident_count": len(incidents),
        "prediction_count": len(predictions),
    }
    _append_jsonl(_watchdog_maintenance_events_path(repo_root), event)
    return {
        "schema_version": "scheduler_watchdog_maintenance_ledger_summary_v1",
        "run_id": run_id,
        "run_path": str(run_path),
        "latest_path": str(latest_path),
        "scoped_latest_path": str(scoped_latest_path or ""),
        "event_log_path": str(_watchdog_maintenance_events_path(repo_root)),
        "truth_overall_state": str(truth_reconciliation.get("overall_state") or ""),
        "scheduler_work_item_count": len(scheduler_work_items),
        "containment_count": len(containments),
        "incident_count": len(incidents),
        "prediction_count": len(predictions),
    }


def _codex_role_for_escalation_class(escalation_class: str) -> str:
    return "builder" if str(escalation_class or "").strip() == "bounded_repair_codex" else "generalist"


def _build_codex_escalation_message(bundle: Dict[str, Any]) -> str:
    file_scope = [str(row or "").strip() for row in list(bundle.get("file_scope") or []) if str(row or "").strip()]
    success_bar = [str(row or "").strip() for row in list(bundle.get("success_bar") or []) if str(row or "").strip()]
    commands = list((((bundle.get("verification_requirements") or {}) if isinstance(bundle.get("verification_requirements"), dict) else {}).get("commands")) or [])
    evidence = [dict(row) for row in list(bundle.get("evidence") or []) if isinstance(row, dict)]
    lines = [
        "Watchdog bounded Codex escalation bundle.",
        f"Condition: {str(bundle.get('condition_id') or '').strip()}",
        f"Escalation class: {str(bundle.get('escalation_class') or '').strip()}",
        f"Execution mode: {str(bundle.get('bounded_execution_mode') or '').strip()}",
        f"Risk level: {str(bundle.get('risk_level') or '').strip()}",
        "",
        "Diagnosis:",
        str(bundle.get("diagnosis") or "").strip(),
        "",
        "Allowed file scope:",
    ]
    if file_scope:
        lines.extend(f"- {row}" for row in file_scope)
    else:
        lines.append("- infra/bin/scheduler_watchdog.py")
    lines.append("")
    lines.append("Success bar:")
    if success_bar:
        lines.extend(f"- {row}" for row in success_bar)
    else:
        lines.append("- Produce bounded evidence-backed analysis.")
    lines.append("")
    lines.append("Watchdog follow-up requirement:")
    lines.append(
        "- After addressing the current failure, make a bounded improvement to the watchdog for this failure type to reduce future Codex escalation."
    )
    if commands:
        lines.append("")
        lines.append("Verification requirements:")
        lines.extend(f"- {str(row).strip()}" for row in commands if str(row).strip())
    if evidence:
        lines.append("")
        lines.append("Evidence findings:")
        for row in evidence[:12]:
            kind = str(row.get("kind") or row.get("finding_type") or "").strip()
            details = row.get("details") if isinstance(row.get("details"), dict) else {}
            summary = str(details.get("summary") or details.get("reason") or "").strip()
            bullet = f"- {kind}" if kind else "- finding"
            if summary:
                bullet += f": {summary}"
            lines.append(bullet)
    return "\n".join(lines).strip() + "\n"


def execute_codex_escalation_bundle(
    *,
    repo_root: Path,
    bundle_path: str,
    binding: Dict[str, Any] | None = None,
    role: str = "",
    model: str = "",
    reasoning_effort: str = "medium",
    writer: str = "scheduler_watchdog.codex_escalation",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    path = Path(bundle_path).resolve()
    bundle = _read_json(path)
    if not bundle:
        raise ValueError("bundle_path did not resolve to a readable watchdog escalation bundle")
    if str(bundle.get("schema_version") or "").strip() != "scheduler_watchdog_codex_escalation_v1":
        raise ValueError("unsupported watchdog codex escalation bundle schema")
    escalation_class = str(bundle.get("escalation_class") or "").strip()
    if escalation_class not in {"advisory_codex", "bounded_repair_codex"}:
        raise ValueError("unsupported watchdog escalation class")
    condition_id = str(bundle.get("condition_id") or "").strip()
    selected_binding = dict(binding or bundle.get("binding") or {"type": "task_id", "id": "wdg"})
    latest_receipt = _latest_codex_receipt_for_condition(repo_root=repo_root, condition_id=condition_id, binding=selected_binding)
    if _codex_receipt_is_active(latest_receipt, repo_root=repo_root):
        return {
            "ok": True,
            "deduped": True,
            "bundle": bundle,
            "receipt_path": str(latest_receipt.get("_receipt_path") or ""),
            "role": str(latest_receipt.get("role") or ""),
            "binding": dict(latest_receipt.get("binding") or {}),
            "send_result": dict(latest_receipt.get("result") or {}),
        }
    selected_role = str(role or _codex_role_for_escalation_class(escalation_class)).strip()
    message = _build_codex_escalation_message(bundle)
    options: Dict[str, Any] = {"agent_tech": "codex"}
    if str(model or "").strip():
        options["model"] = str(model).strip()
    if str(reasoning_effort or "").strip():
        options["reasoning_effort"] = str(reasoning_effort).strip()
    result = _agents.send_message(
        role=selected_role,
        binding=selected_binding,
        message=message,
        options=options,
    )
    receipt = {
        "schema_version": "scheduler_watchdog_codex_execution_receipt_v1",
        "executed_utc": _utc_iso(),
        "writer": writer,
        "bundle_path": str(path),
        "condition_id": str(bundle.get("condition_id") or ""),
        "escalation_class": escalation_class,
        "role": selected_role,
        "binding": selected_binding,
        "message_chars": len(message),
        "send_ok": bool(result.get("ok")),
        "turn_id": str(((result.get("turn") or {}) if isinstance(result.get("turn"), dict) else {}).get("turn_id") or ""),
        "assistant_status": str(((result.get("assistant") or {}) if isinstance(result.get("assistant"), dict) else {}).get("status") or ""),
        "result": result,
    }
    run_id = str(bundle.get("run_id") or "watchdog")
    condition_id = str(bundle.get("condition_id") or "unknown")
    receipt_path = (_watchdog_codex_runs_root(repo_root) / f"{run_id}__{condition_id}.json").resolve()
    _write_json(receipt_path, receipt)
    return {
        "ok": bool(result.get("ok")),
        "bundle": bundle,
        "send_result": result,
        "receipt_path": str(receipt_path),
        "role": selected_role,
        "binding": selected_binding,
    }


def _v2_ready_head_recovery_candidates(*, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    blocked_rows = [row for row in rows if str((row or {}).get("decision") or "").strip().lower() == "blocked"]
    active_rows = [row for row in rows if str((row or {}).get("decision") or "").strip().lower() == "active"]
    if active_rows or not blocked_rows:
        return []
    candidates: List[Dict[str, Any]] = []
    seen_job_ids: set[str] = set()
    for row in rows:
        if str((row or {}).get("decision") or "").strip().lower() != "admit":
            continue
        job_id = str((row or {}).get("job_id") or "").strip()
        dispatch_job_id = str((row or {}).get("dispatch_job_id") or "").strip() or job_id
        if not job_id or job_id in seen_job_ids:
            continue
        seen_job_ids.add(job_id)
        blocked_dependents = [
            str(item.get("job_id") or "").strip()
            for item in blocked_rows
            if job_id and job_id in {str(dep or "").strip() for dep in list(item.get("unmet_dependencies") or [])}
        ]
        candidates.append(
            {
                "job_id": job_id,
                "dispatch_job_id": dispatch_job_id,
                "child_uid": str((row or {}).get("child_uid") or "").strip(),
                "reason": str((row or {}).get("reason") or "").strip(),
                "blocked_dependents": [item for item in blocked_dependents if item],
            }
        )
    return candidates


def _v2_multiple_ready_head_findings(*, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    ready_rows = [row for row in rows if str((row or {}).get("decision") or "").strip().lower() == "admit"]
    job_ids = []
    child_uids = []
    for row in ready_rows:
        job_id = str((row or {}).get("job_id") or "").strip()
        child_uid = str((row or {}).get("child_uid") or "").strip()
        if job_id:
            job_ids.append(job_id)
        if child_uid:
            child_uids.append(child_uid)
    unique_job_ids = sorted(set(job_ids))
    if len(unique_job_ids) <= 1:
        return []
    return [
        {
            "kind": "sched_v2_multiple_ready_heads",
            "details": {
                "ready_job_ids": unique_job_ids,
                "ready_child_uids": sorted(set(child_uids)),
                "summary": "The scheduler v2 surface reports multiple admit heads at once, so the watchdog must treat the surface as ambiguous.",
            },
        }
    ]


def _v2_multiple_active_head_findings(*, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    active_rows = [row for row in rows if str((row or {}).get("decision") or "").strip().lower() == "active"]
    job_ids = []
    child_uids = []
    for row in active_rows:
        job_id = str((row or {}).get("job_id") or "").strip()
        child_uid = str((row or {}).get("child_uid") or "").strip()
        if job_id:
            job_ids.append(job_id)
        if child_uid:
            child_uids.append(child_uid)
    unique_job_ids = sorted(set(job_ids))
    if len(unique_job_ids) <= 1:
        return []
    return [
        {
            "kind": "sched_v2_multiple_active_heads",
            "details": {
                "active_job_ids": unique_job_ids,
                "active_child_uids": sorted(set(child_uids)),
                "summary": "The scheduler v2 surface reports multiple active heads at once, so the watchdog must treat the surface as ambiguous.",
            },
        }
    ]


def _scheduled_job_exists(job_id: str, *, repo_root: Path) -> bool:
    try:
        sched.load_job(str(job_id or "").strip(), repo_root=repo_root)
        return True
    except Exception:
        return False


def _v2_stalled_progress_findings(*, rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    for candidate in _v2_ready_head_recovery_candidates(rows=rows):
        findings.append(
            {
                "kind": "sched_v2_stalled_ready_head",
                "details": {
                    "job_id": str(candidate.get("job_id") or ""),
                    "dispatch_job_id": str(candidate.get("dispatch_job_id") or candidate.get("job_id") or ""),
                    "child_uid": str(candidate.get("child_uid") or ""),
                    "reason": str(candidate.get("reason") or ""),
                    "blocked_dependents": list(candidate.get("blocked_dependents") or []),
                    "summary": f"{str(candidate.get('job_id') or '')} is admitted but idle while dependents remain blocked.",
                },
            }
        )
    return findings

def _run_watchdog_v2(
    *,
    mode: str,
    repo_root: Path,
    parent_uid: str = "",
    owner_mode: str = "shadow",
) -> Dict[str, Any]:
    now = _utc_now()
    run_id = _maintenance_run_id(now=now, mode=mode, repo_root=repo_root)
    scope = _watchdog_scope(scheduler_backend="v2", parent_uid=parent_uid)
    bundle = sched_v2_surfaces.build_internal_surface_bundle(
        repo_root=repo_root,
        parent_uid=str(parent_uid or "").strip(),
        run_at_utc=_utc_iso(now),
        mode=owner_mode,
    )
    surfaces = bundle.get("surfaces") if isinstance(bundle.get("surfaces"), dict) else {}
    rows = list(surfaces.get("watch") or [])
    blocked_rows = [row for row in rows if str((row or {}).get("decision") or "").lower() == "blocked"]
    active_rows = [row for row in rows if str((row or {}).get("decision") or "").lower() == "active"]
    mutating_selfheal_allowed = mode == "selfheal" and not active_rows
    ready_rows = [row for row in rows if str((row or {}).get("decision") or "").lower() == "admit"]
    retired_rows = [row for row in rows if str((row or {}).get("decision") or "").lower() == "retired"]
    findings: List[Dict[str, Any]] = []
    actions: List[Dict[str, Any]] = []
    scheduler_work_items: List[Dict[str, Any]] = []
    status = str(bundle.get("status") or "")
    if status == "ready_without_family":
        findings.append(
            {
                "kind": "sched_v2_watchdog_no_parent_uid",
                "details": {
                    "env": _WATCHDOG_V2_PARENT_UID_ENV,
                    "message": "v2 watchdog mode is selected but no parent UID was provided",
                },
            }
        )
    elif status == "blocked":
        findings.append(
            {
                "kind": "sched_v2_surface_blocked",
                "details": {
                    "error": str(bundle.get("error") or ""),
                    "parent_uid": str(bundle.get("parent_uid") or ""),
                },
            }
        )
    if active_rows and ready_rows:
        findings.append(
            {
                "kind": "sched_v2_surface_active_ready_conflict",
                "details": {
                    "active_job_ids": [str(row.get("job_id") or "") for row in active_rows if str(row.get("job_id") or "")],
                    "ready_job_ids": [str(row.get("job_id") or "") for row in ready_rows if str(row.get("job_id") or "")],
                    "summary": "The scheduler v2 surface simultaneously reports active and admit decisions, so the watchdog must treat the surface as ambiguous.",
                },
            }
        )
    findings.extend(_v2_multiple_active_head_findings(rows=rows))
    findings.extend(_v2_multiple_ready_head_findings(rows=rows))
    stalled_progress_findings = _v2_stalled_progress_findings(rows=rows)
    findings.extend(stalled_progress_findings)
    ambiguity_blocks_recovery = any(
        str(row.get("kind") or "") in {
            "sched_v2_surface_active_ready_conflict",
            "sched_v2_multiple_ready_heads",
            "sched_v2_multiple_active_heads",
        }
        for row in findings
    )
    if mode == "selfheal" and stalled_progress_findings and not ambiguity_blocks_recovery:
        candidate = _v2_ready_head_recovery_candidates(rows=rows)[0]
        if not _scheduled_job_exists(str(candidate.get("job_id") or ""), repo_root=repo_root):
            findings.append(
                {
                    "kind": "sched_v2_surface_job_missing",
                    "details": {
                        "job_id": str(candidate.get("job_id") or ""),
                        "dispatch_job_id": str(candidate.get("dispatch_job_id") or ""),
                        "child_uid": str(candidate.get("child_uid") or ""),
                        "summary": f"{str(candidate.get('job_id') or '')} is surfaced as ready but missing from scheduler inbox.",
                    },
                }
            )
        else:
            request = sched.request_run_now(
                str(candidate.get("dispatch_job_id") or candidate.get("job_id") or ""),
                repo_root=repo_root,
                request_source="watchdog_v2_progress_recovery",
            )
            action = {
                "action": "resume_stalled_v2_ready_head",
                "ok": bool(request.get("ok")),
                "job_id": str(candidate.get("job_id") or ""),
                "dispatch_job_id": str(candidate.get("dispatch_job_id") or ""),
                "child_uid": str(candidate.get("child_uid") or ""),
                "request": request,
                "blocked_dependents": list(candidate.get("blocked_dependents") or []),
            }
            actions.append(action)
    summary = {
        "total_jobs": len(rows),
        "enabled_jobs": len(rows),
        "running_jobs": len(active_rows),
        "blocked_jobs": len(blocked_rows),
        "ready_jobs": len(ready_rows),
        "retired_jobs": len(retired_rows),
        "failed_verification_jobs": 0,
        "error_jobs": 1 if status == "blocked" else 0,
    }
    health = {
        "health": "degraded" if findings else "healthy",
        "reason": (
            "sched_v2_surface_blocked"
            if status == "blocked"
            else (
                "sched_v2_no_parent_uid"
                if status == "ready_without_family"
                else (
                    "sched_v2_surface_active_ready_conflict"
                    if any(str(row.get("kind") or "") == "sched_v2_surface_active_ready_conflict" for row in findings)
                    else (
                        "sched_v2_multiple_active_heads"
                        if any(str(row.get("kind") or "") == "sched_v2_multiple_active_heads" for row in findings)
                        else (
                            "sched_v2_multiple_ready_heads"
                            if any(str(row.get("kind") or "") == "sched_v2_multiple_ready_heads" for row in findings)
                            else ("sched_v2_stalled_ready_head" if stalled_progress_findings else "sched_v2_surface_ready")
                        )
                    )
                )
            )
        ),
    }
    truth_reconciliation = {
        "schema_version": "scheduler_watchdog_truth_reconciliation_v1",
        "overall_state": "truth_ok" if not findings else "truth_ambiguous",
        "fact_count": len(rows),
        "ambiguous_fact_count": len(findings),
        "ambiguous_fact_classes": [str(row.get("kind") or "") for row in findings],
    }
    maintenance_ledger = _write_watchdog_maintenance_run(
        repo_root=repo_root,
        now=now,
        mode=mode,
        run_id=run_id,
        summary=summary,
        health=health,
        findings=findings,
        direct_actions=actions,
        truth_reconciliation=truth_reconciliation,
        root_causes=[],
        playbook_decisions=[],
        scheduler_work_items=scheduler_work_items,
        containments=[],
        incidents=[],
        predictions=[],
        escalation_bundles=[],
        scope=scope,
    )
    actions.append(
        {
            "action": "write_maintenance_ledger_run",
            "ok": True,
            "run_id": run_id,
            "path": str(maintenance_ledger.get("run_path") or ""),
        }
    )
    return {
        "schema_version": "scheduler_watchdog_v3",
        "mode": mode,
        "scheduler_backend": "v2",
        "utc": _utc_iso(now),
        "vienna": _vienna_iso(now),
        "summary": summary,
        "service": {"running": None, "note": "v2 watchdog adapter reads the scheduler v2 surface bundle"},
        "timer_status": {},
        "healthy_locks": [],
        "findings": findings,
        "actions": actions,
        "tick_result": {"ran_tick": False, "decision_only_boundary": True, "scheduler_backend": "v2"},
        "pipeline_healer": {},
        "selfheal_mutating_actions_allowed": bool(mutating_selfheal_allowed),
        "selfheal_active_job_ids": [],
        "allow_pipeline_healer": False,
        "authority_boundary": _watchdog_authority_boundary(),
        "workstream_contract": _watchdog_contract.watchdog_workstream_projection(),
        "truth_reconciliation": truth_reconciliation,
        "root_causes": [],
        "playbook_decisions": [],
        "scheduler_work_items": scheduler_work_items,
        "scheduler_requests": scheduler_work_items,
        "containments": [],
        "incidents": [],
        "predictions": [],
        "maintenance_ledger": maintenance_ledger,
        "scheduler_v2": bundle,
        "health": health,
        "escalation_bundles": [],
        "ok": status != "blocked" and not stalled_progress_findings,
    }


def run_watchdog(
    *,
    mode: str,
    repo_root: Path,
    allow_pipeline_healer: bool | None = None,
    scheduler_mode: str | None = None,
    v2_parent_uid: str | None = None,
) -> Dict[str, Any]:
    selected_scheduler_mode = _watchdog_scheduler_mode(scheduler_mode)
    selected_v2_parent_uid = _watchdog_v2_parent_uid(v2_parent_uid, repo_root=repo_root)
    if scheduler_mode is None and selected_scheduler_mode == "v2" and not selected_v2_parent_uid:
        selected_scheduler_mode = "legacy"
    if selected_scheduler_mode == "auto":
        selected_scheduler_mode = "v2" if selected_v2_parent_uid else "legacy"
    if selected_scheduler_mode == "v2":
        return _run_watchdog_v2(
            mode=mode,
            repo_root=repo_root,
            parent_uid=selected_v2_parent_uid,
            owner_mode=_sched_v2_owner_mode(),
        )
    now = _utc_now()
    actions: List[Dict[str, Any]] = []
    findings: List[Dict[str, Any]] = []
    jobs = sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso(now))
    active_jobs = []
    for row in jobs:
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        active_run_id = str(state.get("active_run_id") or "").strip()
        if active_run_id:
            active_jobs.append(
                {
                    "job_id": str(row.get("job_id") or ""),
                    "active_run_id": active_run_id,
                    "last_status": str(state.get("last_status") or ""),
                }
            )
    active_job_ids = {str(row.get("job_id") or "").strip() for row in active_jobs if str(row.get("job_id") or "").strip()}
    enabled_job_ids = {str(row.get("job_id") or "").strip() for row in jobs if bool(row.get("enabled")) and str(row.get("job_id") or "").strip()}
    mutating_selfheal_allowed = mode != "selfheal" or not active_jobs
    pipeline_healer_enabled = bool(allow_pipeline_healer) if allow_pipeline_healer is not None else mode == "selfheal"

    service = {"running": None, "note": "external watchdog process; server scheduler state is checked via API"}

    findings.extend(_runtime_storage_preflight(repo_root=repo_root))

    timer_findings, timer_status = _watchdog_timer_findings(now=now)
    findings.extend(timer_findings)

    journal_findings, journal_actions = _maybe_repair_commit_journal(repo_root=repo_root, mode="audit")
    findings.extend(journal_findings)
    actions.extend(journal_actions)

    plane_findings, plane_actions = _maybe_self_heal_plane_truth(
        repo_root=repo_root,
        mode="audit",
        mutating_selfheal_allowed=False,
    )
    findings.extend(plane_findings)
    actions.extend(plane_actions)

    proof_findings, proof_actions = _maybe_refresh_delegation_proof(repo_root=repo_root, mode=mode, now=now)
    findings.extend(proof_findings)
    actions.extend(proof_actions)

    scheduler_residue_findings, scheduler_residue_actions = _scheduler_residue_hygiene(
        repo_root=repo_root,
        mode="audit",
        now=now,
        mutating_selfheal_allowed=False,
    )
    findings.extend(scheduler_residue_findings)
    actions.extend(scheduler_residue_actions)

    report_pointer_findings, report_pointer_actions = _repair_watchdog_report_pointers(repo_root=repo_root, mode=mode)
    findings.extend(report_pointer_findings)
    actions.extend(report_pointer_actions)

    residue_findings, residue_actions = _runtime_residue_hygiene(repo_root=repo_root, mode="audit", now=now)
    findings.extend(residue_findings)
    actions.extend(residue_actions)

    if mode == "selfheal" and active_jobs:
        findings.append(
            {
                "kind": "active_runs_present",
                "details": {
                    "active_jobs": active_jobs,
                },
            }
        )

    stale_locks, healthy_locks = _classify_locks(repo_root=repo_root, now=now)
    for row in stale_locks:
        findings.append({"kind": "stale_lock", "details": row})
    stale_lock_paths = {str(row.get("path") or "").strip() for row in stale_locks if str(row.get("path") or "").strip()}
    for row in _snapshot_only_stale_lock_findings(repo_root=repo_root, known_paths=stale_lock_paths):
        findings.append({"kind": "stale_lock", "details": row})

    orphan = _find_orphan_running(repo_root=repo_root)
    for row in orphan:
        findings.append({"kind": "orphan_running_state", "details": row})
    findings.extend(_terminal_receipt_state_conflict_findings(repo_root=repo_root, jobs=jobs))

    pipeline_healer_result = _sanitize_pipeline_healer_result(_latest_pipeline_healer(repo_root), enabled_job_ids=enabled_job_ids, now=now)
    if bool(pipeline_healer_result.get("stale")):
        findings.append(
            {
                "kind": "pipeline_healer_stale",
                "details": {
                    "generated_utc": str(pipeline_healer_result.get("generated_utc") or ""),
                    "age_minutes": pipeline_healer_result.get("age_minutes"),
                },
            }
        )
    findings.extend(_watchdog_repeat_job_problem_findings(repo_root=repo_root, pipeline_healer_result=pipeline_healer_result))

    tick_result = {"ran_tick": False, "decision_only_boundary": True}
    if mode == "selfheal" and active_job_ids:
        tick_result = {
            "ran_tick": False,
            "decision_only_boundary": True,
            "skipped_due_to_active_runs": True,
            "active_job_ids": sorted(active_job_ids),
        }
        findings.append(
            {
                "kind": "tick_skipped_due_to_active_runs",
                "details": {
                    "active_job_ids": sorted(active_job_ids),
                },
            }
        )

    progress_findings, progress_actions, jobs = _maybe_restore_scheduler_progress(
        repo_root=repo_root,
        mode=mode,
        jobs=jobs,
        now=now,
        active_job_ids=active_job_ids,
    )
    findings.extend(progress_findings)
    actions.extend(progress_actions)
    findings.extend(_dependency_blocked_chain_findings(jobs=jobs, now=now))
    findings.extend(_recent_job_failure_signature_findings(repo_root=repo_root, jobs=jobs))
    summary = {
        "total_jobs": len(jobs),
        "enabled_jobs": sum(1 for r in jobs if bool(r.get("enabled"))),
        "running_jobs": sum(1 for r in jobs if str(((r.get("state") or {}).get("last_status") or "")).lower() == "running"),
        "blocked_jobs": sum(1 for r in jobs if str(((r.get("state") or {}).get("last_status") or "")).lower() == "blocked"),
        "failed_verification_jobs": sum(1 for r in jobs if str(((r.get("state") or {}).get("last_status") or "")).lower() == "failed_verification"),
        "error_jobs": sum(1 for r in jobs if str(((r.get("state") or {}).get("last_status") or "")).lower() == "error"),
    }

    truth_reconciliation = _reconcile_watchdog_truth(findings=findings)
    if str(truth_reconciliation.get("overall_state") or "") == "truth_ambiguous":
        findings.append(
            {
                "kind": "truth_ambiguous",
                "details": {
                    "fact_classes": list(truth_reconciliation.get("ambiguous_fact_classes") or []),
                    "ambiguous_fact_count": int(truth_reconciliation.get("ambiguous_fact_count") or 0),
                },
            }
        )

    health = _classify_health(
        summary=summary,
        findings=findings,
        selfheal_mutating_actions_allowed=bool(mutating_selfheal_allowed),
    )
    run_id = _maintenance_run_id(now=now, mode=mode, repo_root=repo_root)
    scope = _watchdog_scope(scheduler_backend="legacy")
    root_causes = _collapsed_root_causes(findings=findings)
    playbook_artifacts = _build_watchdog_playbook_artifacts(
        repo_root=repo_root,
        mode=mode,
        now=now,
        run_id=run_id,
        root_causes=root_causes,
        findings=findings,
        truth_reconciliation=truth_reconciliation,
        direct_actions=actions,
        allow_pipeline_healer=bool(pipeline_healer_enabled),
        active_job_ids=active_job_ids,
    )
    actions.extend(list(playbook_artifacts.get("actions") or []))
    incidents_payload = _build_watchdog_incidents_and_predictions(
        repo_root=repo_root,
        now=now,
        run_id=run_id,
        root_causes=root_causes,
        truth_reconciliation=truth_reconciliation,
        scope=scope,
    )
    actions.extend(list(incidents_payload.get("actions") or []))
    escalation_bundles = _write_bounded_codex_escalation_bundles(
        repo_root=repo_root,
        mode=mode,
        now=now,
        run_id=run_id,
        health=health,
        findings=findings,
        truth_reconciliation=truth_reconciliation,
        playbook_decisions=list(playbook_artifacts.get("playbook_decisions") or []),
        incidents=list(incidents_payload.get("incidents") or []),
        predictions=list(incidents_payload.get("predictions") or []),
        scope=scope,
    )
    repeated_condition_ids = set(
        _watchdog_repeated_maintenance_condition_ids(
            repo_root=repo_root,
            current_condition_ids=[
                str(bundle.get("condition_id") or "").strip()
                for bundle in escalation_bundles
                if isinstance(bundle, dict) and str(bundle.get("condition_id") or "").strip()
            ],
            scope=scope,
        )
    )
    if mode == "selfheal" and not active_job_ids:
        auto_codex_conditions: set[str] = set()
        if any(str(finding.get("kind") or "") == "stalled_progress_chain_unrecovered" for finding in findings):
            auto_codex_conditions.add("execution_state_disagreement")
        if any(
            str(finding.get("kind") or "") in {"watchdog_repeat_repair_exhausted", "repeated_job_failure_signature"}
            for finding in findings
        ):
            auto_codex_conditions.add("repeat_watchdog_job_problem")
        auto_codex_conditions.update(repeated_condition_ids)
        for bundle in escalation_bundles:
            if str(bundle.get("escalation_class") or "").strip() != "bounded_repair_codex":
                continue
            condition_id = str(bundle.get("condition_id") or "").strip()
            if condition_id not in auto_codex_conditions or not str(bundle.get("bundle_path") or "").strip():
                continue
            try:
                executed = execute_codex_escalation_bundle(
                    repo_root=repo_root,
                    bundle_path=str(bundle.get("bundle_path") or ""),
                    writer="scheduler_watchdog.progress_recovery_codex",
                )
                actions.append(
                    {
                        "action": "execute_bounded_codex_escalation",
                        "ok": bool(executed.get("ok")),
                        "condition_id": condition_id,
                        "receipt_path": str(executed.get("receipt_path") or ""),
                        "role": str(executed.get("role") or ""),
                    }
                )
            except Exception as exc:
                actions.append(
                    {
                        "action": "execute_bounded_codex_escalation",
                        "ok": False,
                        "condition_id": condition_id,
                        "error": str(exc),
                    }
                )
    maintenance_ledger = _write_watchdog_maintenance_run(
        repo_root=repo_root,
        now=now,
        mode=mode,
        run_id=run_id,
        summary=summary,
        health=health,
        findings=findings,
        direct_actions=actions,
        truth_reconciliation=truth_reconciliation,
        root_causes=root_causes,
        playbook_decisions=list(playbook_artifacts.get("playbook_decisions") or []),
        scheduler_work_items=list(playbook_artifacts.get("scheduler_work_items") or []),
        containments=list(playbook_artifacts.get("containments") or []),
        incidents=list(incidents_payload.get("incidents") or []),
        predictions=list(incidents_payload.get("predictions") or []),
        escalation_bundles=escalation_bundles,
        scope=scope,
    )
    actions.append(
        {
            "action": "write_maintenance_ledger_run",
            "ok": True,
            "run_id": run_id,
            "path": str(maintenance_ledger.get("run_path") or ""),
        }
    )

    payload = {
        "schema_version": "scheduler_watchdog_v3",
        "mode": mode,
        "scheduler_backend": "legacy",
        "utc": _utc_iso(now),
        "vienna": _vienna_iso(now),
        "summary": summary,
        "service": service,
        "timer_status": timer_status,
        "healthy_locks": healthy_locks,
        "findings": findings,
        "actions": actions,
        "tick_result": tick_result,
        "pipeline_healer": pipeline_healer_result,
        "selfheal_mutating_actions_allowed": bool(mutating_selfheal_allowed),
        "selfheal_active_job_ids": sorted(active_job_ids),
        "allow_pipeline_healer": bool(pipeline_healer_enabled),
        "authority_boundary": _watchdog_authority_boundary(),
        "workstream_contract": _watchdog_contract.watchdog_workstream_projection(),
        "truth_reconciliation": truth_reconciliation,
        "root_causes": root_causes,
        "playbook_decisions": list(playbook_artifacts.get("playbook_decisions") or []),
        "scheduler_work_items": list(playbook_artifacts.get("scheduler_work_items") or []),
        "scheduler_requests": list(playbook_artifacts.get("scheduler_work_items") or []),
        "containments": list(playbook_artifacts.get("containments") or []),
        "incidents": list(incidents_payload.get("incidents") or []),
        "predictions": list(incidents_payload.get("predictions") or []),
        "maintenance_ledger": maintenance_ledger,
    }
    payload["health"] = health
    payload["escalation_bundles"] = escalation_bundles
    payload["ok"] = len([a for a in actions if not bool(a.get("ok", True))]) == 0
    return payload


def _to_markdown(row: Dict[str, Any]) -> str:
    summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
    findings = row.get("findings") if isinstance(row.get("findings"), list) else []
    actions = row.get("actions") if isinstance(row.get("actions"), list) else []
    service = row.get("service") if isinstance(row.get("service"), dict) else {}
    api_scheduler = row.get("api_scheduler") if isinstance(row.get("api_scheduler"), dict) else {}
    pipeline_healer = row.get("pipeline_healer") if isinstance(row.get("pipeline_healer"), dict) else {}
    timer_status = row.get("timer_status") if isinstance(row.get("timer_status"), dict) else {}
    escalation_bundles = row.get("escalation_bundles") if isinstance(row.get("escalation_bundles"), list) else []
    scheduler_running = api_scheduler.get("running") if api_scheduler.get("reachable") else service.get("running")
    lines: List[str] = []
    lines.append(f"# Scheduler Watchdog ({str(row.get('mode') or '')})")
    lines.append("")
    lines.append(f"- utc: {str(row.get('utc') or '')}")
    lines.append(f"- vienna: {str(row.get('vienna') or '')}")
    lines.append(f"- ok: {str(bool(row.get('ok'))).lower()}")
    lines.append(f"- scheduler_backend: {str(row.get('scheduler_backend') or 'legacy')}")
    health = row.get("health") if isinstance(row.get("health"), dict) else {}
    lines.append(f"- health: {str(health.get('health') or '')}")
    lines.append(f"- health_reason: {str(health.get('reason') or '')}")
    workstream_contract = row.get("workstream_contract") if isinstance(row.get("workstream_contract"), dict) else {}
    lines.append(f"- scheduler_running: {str(bool(scheduler_running)).lower()}")
    lines.append(f"- jobs: total={int(summary.get('total_jobs') or 0)} enabled={int(summary.get('enabled_jobs') or 0)} running={int(summary.get('running_jobs') or 0)} blocked={int(summary.get('blocked_jobs') or 0)} failed_verification={int(summary.get('failed_verification_jobs') or 0)} error={int(summary.get('error_jobs') or 0)}")
    scheduler_v2 = row.get("scheduler_v2") if isinstance(row.get("scheduler_v2"), dict) else {}
    if scheduler_v2:
        ownership = scheduler_v2.get("ownership") if isinstance(scheduler_v2.get("ownership"), dict) else {}
        lines.append(f"- scheduler_v2_status: {str(scheduler_v2.get('status') or '')}")
        lines.append(f"- scheduler_v2_mode: {str(ownership.get('mode') or '')}")
        lines.append(f"- scheduler_v2_projection_equivalent: {str(bool(scheduler_v2.get('projection_equivalent'))).lower()}")
    lines.append("")
    lines.append("## Watchdog Program Boundary")
    if not workstream_contract:
        lines.append("- unavailable")
    else:
        lines.append(f"- parent_uid: {str(workstream_contract.get('parent_uid') or '')}")
        lines.append(f"- decision_only_program: {str(bool(workstream_contract.get('decision_only_program'))).lower()}")
        for row_item in list(workstream_contract.get('workstreams') or []):
            if not isinstance(row_item, dict):
                continue
            lines.append(
                f"- {str(row_item.get('uid') or '')}: {str(row_item.get('scope_id') or '')} role={str(row_item.get('role') or '')} execution_authority={str(row_item.get('execution_authority') or '')}"
            )
    lines.append("")
    lines.append("## Watchdog Timers")
    if not timer_status:
        lines.append("- unavailable")
    else:
        lines.append(f"- installed: {str(bool(timer_status.get('installed'))).lower()}")
        units = timer_status.get("units") if isinstance(timer_status.get("units"), dict) else {}
        for key in ("audit_timer", "selfheal_timer"):
            unit = units.get(key) if isinstance(units.get(key), dict) else {}
            lines.append(
                f"- {key}: active_state={str(unit.get('active_state') or '')} unit_file_state={str(unit.get('unit_file_state') or '')} last_trigger_utc={str(unit.get('last_trigger_utc') or '')}"
            )
    lines.append("")
    lines.append("## Pipeline Healer")
    if not pipeline_healer:
        lines.append("- none recorded yet")
    else:
        healer_summary = pipeline_healer.get("summary") if isinstance(pipeline_healer.get("summary"), dict) else {}
        lines.append(f"- generated_utc: {str(pipeline_healer.get('generated_utc') or '')}")
        lines.append(f"- stale: {str(bool(pipeline_healer.get('stale'))).lower()}")
        lines.append(f"- age_minutes: {str(pipeline_healer.get('age_minutes') or '')}")
        lines.append(f"- apply_mode: {str(bool(pipeline_healer.get('apply_mode'))).lower()}")
        lines.append(f"- findings: {int(healer_summary.get('finding_count') or 0)}")
        lines.append(f"- repairs_applied: {int(healer_summary.get('repairs_applied') or 0)}")
        lines.append(f"- repairs_deferred: {int(healer_summary.get('repairs_deferred') or 0)}")
        lines.append(f"- historical_findings_suppressed: {int(healer_summary.get('historical_findings_suppressed') or 0)}")
    lines.append("")
    lines.append("## Findings")
    if not findings:
        lines.append("- none")
    else:
        for f in findings:
            lines.append(f"- {str(f.get('kind') or 'unknown')}: {json.dumps(f.get('details') or {}, ensure_ascii=False)}")
    lines.append("")
    lines.append("## Actions")
    if not actions:
        lines.append("- none")
    else:
        for a in actions:
            lines.append(f"- {str(a.get('action') or 'unknown')}: {json.dumps(a, ensure_ascii=False)}")
    authority_boundary = row.get("authority_boundary") if isinstance(row.get("authority_boundary"), dict) else {}
    truth_reconciliation = row.get("truth_reconciliation") if isinstance(row.get("truth_reconciliation"), dict) else {}
    maintenance_ledger = row.get("maintenance_ledger") if isinstance(row.get("maintenance_ledger"), dict) else {}
    root_causes = row.get("root_causes") if isinstance(row.get("root_causes"), list) else []
    playbook_decisions = row.get("playbook_decisions") if isinstance(row.get("playbook_decisions"), list) else []
    scheduler_work_items = row.get("scheduler_work_items") if isinstance(row.get("scheduler_work_items"), list) else []
    containments = row.get("containments") if isinstance(row.get("containments"), list) else []
    incidents = row.get("incidents") if isinstance(row.get("incidents"), list) else []
    predictions = row.get("predictions") if isinstance(row.get("predictions"), list) else []
    lines.append("")
    lines.append("## Authority Boundary")
    if not authority_boundary:
        lines.append("- unavailable")
    else:
        lines.append(f"- watchdog_role: {str(authority_boundary.get('watchdog_role') or '')}")
        lines.append(f"- execution_authority: {str(authority_boundary.get('execution_authority') or '')}")
        lines.append(f"- codex_role: {str(authority_boundary.get('codex_role') or '')}")
        forbidden = list(authority_boundary.get('direct_watchdog_mutation_forbidden') or [])
        lines.append(f"- direct_watchdog_mutation_forbidden: {', '.join(str(x) for x in forbidden) if forbidden else 'none'}")
    lines.append("")
    lines.append("## Truth Reconciliation")
    if not truth_reconciliation:
        lines.append("- unavailable")
    else:
        lines.append(f"- overall_state: {str(truth_reconciliation.get('overall_state') or '')}")
        lines.append(f"- fact_count: {int(truth_reconciliation.get('fact_count') or 0)}")
        lines.append(f"- ambiguous_fact_count: {int(truth_reconciliation.get('ambiguous_fact_count') or 0)}")
        lines.append(f"- ambiguous_fact_classes: {json.dumps(list(truth_reconciliation.get('ambiguous_fact_classes') or []), ensure_ascii=False)}")
    lines.append("")
    lines.append("## Maintenance Ledger")
    if not maintenance_ledger:
        lines.append("- unavailable")
    else:
        lines.append(f"- run_id: {str(maintenance_ledger.get('run_id') or '')}")
        lines.append(f"- run_path: {str(maintenance_ledger.get('run_path') or '')}")
        lines.append(f"- truth_overall_state: {str(maintenance_ledger.get('truth_overall_state') or '')}")
        lines.append(f"- scheduler_work_item_count: {int(maintenance_ledger.get('scheduler_work_item_count') or 0)}")
        lines.append(f"- containment_count: {int(maintenance_ledger.get('containment_count') or 0)}")
        lines.append(f"- incident_count: {int(maintenance_ledger.get('incident_count') or 0)}")
        lines.append(f"- prediction_count: {int(maintenance_ledger.get('prediction_count') or 0)}")
    lines.append("")
    lines.append("## Root Causes")
    if not root_causes:
        lines.append("- none")
    else:
        for item in root_causes:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {str(item.get('condition_id') or 'unknown')}: risk={str(item.get('risk_level') or '')} findings={int(item.get('finding_count') or 0)} summary={str(item.get('summary') or '')}")
    lines.append("")
    lines.append("## Playbook Decisions")
    if not playbook_decisions:
        lines.append("- none")
    else:
        for item in playbook_decisions:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {str(item.get('decision_id') or 'unknown')}: condition={str(item.get('condition_id') or '')} kind={str(item.get('decision_kind') or '')} path={str(item.get('artifact_path') or '')}")
    lines.append("")
    lines.append("## Scheduler Work Items")
    if not scheduler_work_items:
        lines.append("- none")
    else:
        for item in scheduler_work_items:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {str(item.get('request_id') or 'unknown')}: condition={str(item.get('condition_id') or '')} disposition={str(item.get('scheduler_disposition') or '')} requested_actions={json.dumps(list(item.get('requested_actions') or []), ensure_ascii=False)}")
    lines.append("")
    lines.append("## Containments")
    if not containments:
        lines.append("- none")
    else:
        for item in containments:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {str(item.get('containment_id') or 'unknown')}: condition={str(item.get('condition_id') or '')} reason={str(item.get('reason') or '')} path={str(item.get('artifact_path') or '')}")
    lines.append("")
    lines.append("## Incidents")
    if not incidents:
        lines.append("- none")
    else:
        for item in incidents:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {str(item.get('incident_id') or 'unknown')}: condition={str(item.get('condition_id') or '')} state={str(item.get('state') or '')} risk={str(item.get('risk_level') or '')}")
    lines.append("")
    lines.append("## Predictions")
    if not predictions:
        lines.append("- none")
    else:
        for item in predictions:
            if not isinstance(item, dict):
                continue
            lines.append(f"- {str(item.get('prediction_id') or 'unknown')}: condition={str(item.get('condition_id') or '')} confidence={str(item.get('confidence') or '')} decayed_score={str(item.get('decayed_score') or '')}")
    lines.append("")
    lines.append("## Escalation Bundles")
    if not escalation_bundles:
        lines.append("- none")
    else:
        for bundle in escalation_bundles:
            lines.append(
                f"- {str(bundle.get('condition_id') or 'unknown')}: risk={str(bundle.get('risk_level') or '')} safe_for_agent_handoff={str(bool(bundle.get('safe_for_agent_handoff'))).lower()} path={str(bundle.get('bundle_path') or '')}"
            )
    return "\n".join(lines) + "\n"


def _http_json(url: str, *, method: str = "GET", data: Dict[str, Any] | None = None, timeout: int = 20) -> Dict[str, Any]:
    payload = None
    headers = {"Accept": "application/json"}
    if data is not None:
        payload = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=payload, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = json.loads(resp.read().decode("utf-8"))
    return raw if isinstance(raw, dict) else {}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scheduler watchdog audit/self-heal.")
    parser.add_argument("--mode", choices=("audit", "selfheal"), default="audit")
    parser.add_argument("--base-url", default="http://localhost:8787")
    parser.add_argument("--json-out", default="")
    parser.add_argument("--md-out", default="")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON to stdout.")
    parser.add_argument(
        "--scheduler-mode",
        choices=("legacy", "v2", "auto"),
        default=os.environ.get(_WATCHDOG_SCHEDULER_MODE_ENV)
        or _runtime_policy.get_runtime_policy_text(_WATCHDOG_SCHEDULER_MODE_POLICY_KEY, "v2"),
        help=(
            f"Scheduler backend to inspect (default: ${_WATCHDOG_SCHEDULER_MODE_ENV}, "
            f"{_WATCHDOG_SCHEDULER_MODE_POLICY_KEY}, or auto)."
        ),
    )
    parser.add_argument(
        "--v2-parent-uid",
        default=os.environ.get(_WATCHDOG_V2_PARENT_UID_ENV)
        or _runtime_policy.get_runtime_policy_text(_SCHED_V2_PARENT_UID_POLICY_KEY, ""),
        help=(
            f"Intent-family parent UID for --scheduler-mode=v2 (default: ${_WATCHDOG_V2_PARENT_UID_ENV}, "
            f"{_SCHED_V2_PARENT_UID_POLICY_KEY}, or empty)."
        ),
    )
    parser.add_argument("--allow-pipeline-healer", dest="allow_pipeline_healer", action="store_true")
    parser.add_argument("--no-pipeline-healer", dest="allow_pipeline_healer", action="store_false")
    parser.set_defaults(allow_pipeline_healer=None)
    return parser.parse_args()


def main() -> int:
    try:
        args = _parse_args()
        repo_root = sched._repo_root()
        mode = str(args.mode)
        base_url = str(args.base_url or "http://localhost:8787").strip().rstrip("/")
        result = run_watchdog(
            mode=mode,
            repo_root=repo_root,
            allow_pipeline_healer=args.allow_pipeline_healer,
            scheduler_mode=args.scheduler_mode,
            v2_parent_uid=args.v2_parent_uid,
        )
        api_scheduler: Dict[str, Any] = {}
        try:
            jobs_payload = _http_json(f"{base_url}/api/schedules/jobs", timeout=20)
            api_scheduler = jobs_payload.get("scheduler") if isinstance(jobs_payload.get("scheduler"), dict) else {}
            result["api_scheduler"] = {
                "reachable": True,
                "running": bool(api_scheduler.get("running")),
                "interval_s": int(api_scheduler.get("interval_s") or 0),
                "last_tick_utc": str(api_scheduler.get("last_tick_utc") or ""),
                "last_error": str(api_scheduler.get("last_error") or ""),
            }
        except urllib.error.URLError as e:
            result["api_scheduler"] = {"reachable": False, "error": str(e)}
        except Exception as e:
            result["api_scheduler"] = {"reachable": False, "error": str(e)}

        # Decision-only boundary: the standalone watchdog may report scheduler reachability,
        # but it must not directly execute scheduler work or force a scheduler tick.

        if str(args.json_out or "").strip():
            _write_json(Path(str(args.json_out)).resolve(), result)
        md = _to_markdown(result)
        if str(args.md_out or "").strip():
            out = Path(str(args.md_out)).resolve()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(md, encoding="utf-8")
        if bool(args.json):
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(md, end="" if md.endswith("\n") else "\n")
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
