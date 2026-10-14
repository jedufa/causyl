"""T6U1 web operations console read model and runtime-control wrappers.

This module stays read/projection-focused over existing durable truth owners.
It does not change queue/workflow/scheduler execution semantics.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools.bin import scheduler_watchdog as _watchdog
from tools.bin import scheduler_watchdog_contract as _watchdog_contract
from tools.bin import scheduler_watchdog_timer as _watchdog_timer
from tools import runtime_policy as _runtime_policy
from infra.controlplane import plane_mission_contract as _plane_mission_contract
from infra.controlplane import scheduled_jobs as _scheduled_jobs
from infra.webui import server_agents as _agents
from infra.webui import server_agents_orchestrator as _agent_orch
from infra.webui import server_control_action_audit as _control_action_audit
from infra.webui import server_execution_queue as _execution_queue
from infra.webui import server_lab_assistant_operator_snapshot as _lab_operator_snapshot
from experiments_shared import refresh_experiment_control_policy_cache as _refresh_experiment_control_policy_cache


_CONTROL_EVENT_SCHEMA = "lab_assistant_web_ops_control_event_v1"
_LOCK = threading.RLock()
_RUNNING_QUEUE_STATES = {"running", "starting", "resuming", "workflow_running"}
_STOPPED_QUEUE_STATES = {"paused", "idle", "failed"}


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(value: Optional[_dt.datetime] = None) -> str:
    dt = value or _utc_now()
    return dt.isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> Optional[_dt.datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    except Exception:
        return None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return row if isinstance(row, dict) else {}


def _root(repo_root: Path) -> Path:
    root = (Path(repo_root).resolve() / "runtime" / "state" / "webui" / "lab_assistant_web_operations").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _control_events_path(repo_root: Path) -> Path:
    return (_root(repo_root) / "control_events.jsonl").resolve()


def _watchdog_reports_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "reports" / "scheduler" / "watchdog").resolve()


def _watchdog_maintenance_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "scheduler_watchdog" / "maintenance").resolve()


def _watchdog_latest_generated_utc(row: Dict[str, Any]) -> str:
    payload = dict(row or {})
    return _clean_text(payload.get("generated_utc") or payload.get("utc"))


def _watchdog_latest_index(repo_root: Path, *, kind: str) -> Dict[str, Any]:
    path = (_watchdog_maintenance_root(repo_root) / kind / "latest.json").resolve()
    return _read_json_file(path) if path.exists() else {}


def _watchdog_latest_report(repo_root: Path, *, mode: str) -> Dict[str, Any]:
    root = _watchdog_reports_root(repo_root)
    json_path = root / f"latest_{mode}.json"
    return _read_json_file(json_path) if json_path.exists() else {}


def _watchdog_alertability_projection(repo_root: Path, *, mode: str, latest_row: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(latest_row or {})
    incident_index = _watchdog_latest_index(repo_root, kind="incidents")
    prediction_index = _watchdog_latest_index(repo_root, kind="predictions")
    report_generated_utc = _watchdog_latest_generated_utc(row)
    incident_generated_utc = _clean_text(incident_index.get("generated_utc"))
    report_dt = _parse_utc(report_generated_utc)
    incident_dt = _parse_utc(incident_generated_utc)
    report_health = _clean_text(dict(row.get("health") or {}).get("health")).lower()
    open_incident_count = max(int(len(list(row.get("incidents") or []))), int(incident_index.get("open_count") or 0))
    prediction_count = max(int(len(list(row.get("predictions") or []))), int(prediction_index.get("prediction_count") or 0))
    incident_conditions = {
        _clean_text(item.get("condition_id")).lower()
        for item in list(incident_index.get("incidents") or [])
        if isinstance(item, dict) and _clean_text(item.get("condition_id"))
    }
    root_causes = {
        _clean_text(item.get("condition_id")).lower()
        for item in list(row.get("root_causes") or [])
        if isinstance(item, dict) and _clean_text(item.get("condition_id"))
    }
    false_calm_detected = open_incident_count > 0 and (not row or report_health == "healthy")
    detectability_lag_seconds = 0
    if incident_dt is not None and report_dt is not None and incident_dt > report_dt:
        detectability_lag_seconds = int((incident_dt - report_dt).total_seconds())
    elif incident_dt is not None and report_dt is None and open_incident_count > 0:
        detectability_lag_seconds = -1
    wrong_cause_detected = bool(incident_conditions) and bool(root_causes) and not bool(incident_conditions & root_causes)
    state = "healthy"
    if false_calm_detected or wrong_cause_detected or detectability_lag_seconds != 0:
        state = "degraded"
    return {
        "state": state,
        "mode": _clean_text(mode).lower(),
        "latest_report_generated_utc": report_generated_utc,
        "latest_incident_generated_utc": incident_generated_utc,
        "open_incident_count": open_incident_count,
        "prediction_count": prediction_count,
        "false_calm_detected": bool(false_calm_detected),
        "wrong_cause_detected": bool(wrong_cause_detected),
        "detectability_lag_seconds": int(detectability_lag_seconds),
        "lag_missing_latest_report": bool(detectability_lag_seconds == -1),
        "incident_condition_ids": sorted(condition for condition in incident_conditions if condition),
        "root_cause_condition_ids": sorted(condition for condition in root_causes if condition),
    }


def _watchdog_mode_projection(repo_root: Path, *, mode: str) -> Dict[str, Any]:
    root = _watchdog_reports_root(repo_root)
    json_path = root / f"latest_{mode}.json"
    md_path = root / f"latest_{mode}.md"
    row = _watchdog_latest_report(repo_root, mode=mode)
    truth = dict(row.get("truth_reconciliation") or {})
    authority = dict(row.get("authority_boundary") or {})
    maintenance = dict(row.get("maintenance_ledger") or {})
    root_causes = [dict(item) for item in list(row.get("root_causes") or []) if isinstance(item, dict)]
    alertability = _watchdog_alertability_projection(repo_root, mode=mode, latest_row=row)
    return {
        "json_path": str(json_path),
        "md_path": str(md_path),
        "exists": bool(row),
        "utc": _watchdog_latest_generated_utc(row),
        "health": dict(row.get("health") or {}),
        "summary": dict(row.get("summary") or {}),
        "ok": bool(row.get("ok")) if row else None,
        "truth_reconciliation": {
            "overall_state": _clean_text(truth.get("overall_state")),
            "fact_count": int(truth.get("fact_count") or 0),
            "ambiguous_fact_count": int(truth.get("ambiguous_fact_count") or 0),
            "ambiguous_fact_classes": list(truth.get("ambiguous_fact_classes") or []),
        },
        "authority_boundary": {
            "execution_authority": _clean_text(authority.get("execution_authority")),
            "watchdog_role": _clean_text(authority.get("watchdog_role")),
            "codex_role": _clean_text(authority.get("codex_role")),
            "direct_watchdog_mutation_forbidden_count": int(len(list(authority.get("direct_watchdog_mutation_forbidden") or []))),
        },
        "maintenance_ledger": maintenance,
        "decision_count": int(len(list(row.get("playbook_decisions") or []))),
        "scheduler_request_count": int(len(list(row.get("scheduler_work_items") or []))),
        "containment_count": int(len(list(row.get("containments") or []))),
        "incident_count": int(alertability.get("open_incident_count") or 0),
        "prediction_count": int(alertability.get("prediction_count") or 0),
        "escalation_count": int(len(list(row.get("escalation_bundles") or []))),
        "alertability": alertability,
        "root_causes": [
            {
                "condition_id": _clean_text(item.get("condition_id")),
                "risk_level": _clean_text(item.get("risk_level")),
                "summary": _clean_text(item.get("summary")),
                "finding_count": int(item.get("finding_count") or 0),
            }
            for item in root_causes
        ],
    }


def _watchdog_projection(repo_root: Path) -> Dict[str, Any]:
    latest = {mode: _watchdog_mode_projection(repo_root, mode=mode) for mode in ("audit", "selfheal")}
    return {
        "timer_status": _watchdog_timer.timer_status_snapshot(),
        "workstream_contract": _watchdog_contract.watchdog_workstream_projection(),
        "latest": latest,
    }


def _watchdog_primary_alertability_projection(watchdog_projection: Dict[str, Any]) -> Dict[str, Any]:
    candidates = _watchdog_alertability_candidates(watchdog_projection)
    if not candidates:
        return {}

    def _score(item: Dict[str, Any]) -> tuple[int, int, _dt.datetime]:
        severity = 0
        if bool(item.get("false_calm_detected")):
            severity += 4
        if bool(item.get("wrong_cause_detected")):
            severity += 2
        lag = int(item.get("detectability_lag_seconds") or 0)
        if lag != 0:
            severity += 1
        report_dt = _parse_utc(item.get("latest_report_generated_utc")) or _dt.datetime.min.replace(tzinfo=_dt.timezone.utc)
        return (severity, lag, report_dt)

    return max(candidates, key=_score)


def _watchdog_alertability_candidates(watchdog_projection: Dict[str, Any]) -> List[Dict[str, Any]]:
    latest = watchdog_projection.get("latest") if isinstance(watchdog_projection.get("latest"), dict) else {}
    candidates: List[Dict[str, Any]] = []
    for mode, row in latest.items():
        if not isinstance(row, dict):
            continue
        alertability = row.get("alertability") if isinstance(row.get("alertability"), dict) else {}
        merged = dict(alertability)
        merged["mode"] = _clean_text(merged.get("mode") or mode).lower()
        merged["latest_report_generated_utc"] = _clean_text(
            merged.get("latest_report_generated_utc") or row.get("utc")
        )
        candidates.append(merged)
    return candidates


def watchdog_snapshot(repo_root: Path) -> Dict[str, Any]:
    return _watchdog_projection(repo_root)


def watchdog_latest_snapshot(repo_root: Path, *, mode: str = "") -> Dict[str, Any]:
    safe_mode = _clean_text(mode).lower()
    if safe_mode in {"audit", "selfheal"}:
        row = _watchdog_latest_report(repo_root, mode=safe_mode)
        return {safe_mode: row if row else _watchdog_mode_projection(repo_root, mode=safe_mode)}
    return {
        safe: (_watchdog_latest_report(repo_root, mode=safe) or _watchdog_mode_projection(repo_root, mode=safe))
        for safe in ("audit", "selfheal")
    }


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_jsonl(path: Path, *, limit: int = 100) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if len(lines) > int(limit):
        lines = lines[-int(limit) :]
    out: List[Dict[str, Any]] = []
    for raw in lines:
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _tail_text_file(path: Path, *, lines: int = 80) -> List[str]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        rows = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    return rows[-max(1, int(lines)) :]


def _recent_controls(repo_root: Path, *, limit: int = 12) -> List[Dict[str, Any]]:
    root = Path(repo_root).resolve()
    operator_snapshot = _lab_operator_snapshot.build_operator_snapshot(root)
    queue_payload = _execution_queue.status()
    control_events = _read_jsonl(_control_events_path(root), limit=max(1, int(limit)))
    recent_controls: List[Dict[str, Any]] = []
    for row in reversed(control_events):
        action_id = _clean_text(row.get("action_id"))
        if not action_id:
            continue
        recent_controls.append(
            {
                "control_request_id": _clean_text(row.get("control_request_id")),
                "action_id": action_id,
                "requested_utc": _clean_text(row.get("requested_utc")),
                "writer": _clean_text(row.get("writer")),
                "params": dict(row.get("params") or {}),
                "invocation": dict(row.get("invocation") or {}),
                "confirmation": _confirmation_projection(
                    action_id=action_id,
                    event=row,
                    queue_status=queue_payload,
                    operator_snapshot=operator_snapshot,
                ),
            }
        )
    return recent_controls


def watchdog_activity_snapshot(
    repo_root: Path,
    *,
    event_limit: int = 12,
    control_limit: int = 12,
    log_lines: int = 80,
    action_limit: int = 20,
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    maintenance_events = _read_jsonl(_watchdog._watchdog_maintenance_events_path(root), limit=max(1, int(event_limit)))
    recent_controls = _recent_controls(root, limit=max(1, int(control_limit)))
    latest_rows = {mode: _watchdog_latest_report(root, mode=mode) for mode in ("audit", "selfheal")}
    newest_mode = ""
    newest_utc: Optional[_dt.datetime] = None
    for mode, row in latest_rows.items():
        if not row:
            continue
        parsed = _parse_utc(_watchdog_latest_generated_utc(row))
        if parsed and (newest_utc is None or parsed >= newest_utc):
            newest_mode = mode
            newest_utc = parsed
    log_path = _watchdog_reports_root(root) / f"latest_{newest_mode}.md" if newest_mode else Path("")
    recent_actions: List[Dict[str, Any]] = []
    for mode in ("audit", "selfheal"):
        row = dict(latest_rows.get(mode) or {})
        if not row:
            continue
        for action in list(row.get("direct_watchdog_actions") or []):
            if isinstance(action, dict):
                recent_actions.append(
                    {
                        "source": "direct_watchdog_action",
                        "mode": mode,
                        "action": _clean_text(action.get("action")),
                        "ok": bool(action.get("ok")),
                    }
                )
        for decision in list(row.get("playbook_decisions") or []):
            if isinstance(decision, dict):
                recent_actions.append(
                    {
                        "source": "playbook_decision",
                        "mode": mode,
                        "decision_kind": _clean_text(decision.get("decision_kind")),
                        "condition_id": _clean_text(decision.get("condition_id")),
                        "risk_level": _clean_text(decision.get("risk_level")),
                    }
                )
        for item in list(row.get("scheduler_work_items") or []):
            if isinstance(item, dict):
                recent_actions.append(
                    {
                        "source": "scheduler_work_item",
                        "mode": mode,
                        "condition_id": _clean_text(item.get("condition_id")),
                        "scheduler_disposition": _clean_text(item.get("scheduler_disposition")),
                        "requested_actions": list(item.get("requested_actions") or []),
                    }
                )
    return {
        "schema_version": "scheduler_watchdog_activity_snapshot_v1",
        "utc": _utc_iso(),
        "recent_controls": recent_controls,
        "recent_maintenance_events": maintenance_events,
        "recent_actions": recent_actions[: max(1, int(action_limit))],
        "recent_log": {
            "mode": newest_mode,
            "path": str(log_path) if newest_mode else "",
            "lines": _tail_text_file(log_path, lines=max(1, int(log_lines))) if newest_mode else [],
        },
    }


def _workflow_status_category(value: Any) -> str:
    s = _clean_text(value).lower()
    if s in {"running", "accepted", "started", "starting", "waiting_on_job", "waiting_on_verification"}:
        return "running"
    if s in {"failed", "error"}:
        return "failed"
    if s in {"cancelled", "timed_out", "interrupted"}:
        return "interrupted"
    if s in {"completed", "done"}:
        return "completed"
    if s in {"", "unknown"}:
        return "unknown"
    return "other"


def _queue_status_category(value: Any) -> str:
    s = _clean_text(value).lower()
    if s in {"running", "workflow_running", "starting", "resuming"}:
        return "running"
    if s in {"failed"}:
        return "failed"
    if s in {"paused"}:
        return "interrupted"
    if s in {"completed", "idle"}:
        return "completed"
    if s in {"", "unknown"}:
        return "unknown"
    return "other"


def _refresh_capability_check() -> Dict[str, Any]:
    """G6N7: Check whether live runtime refresh is supported in this deployment.

    Live refresh applies only to policy/prompt changes.
    Server code, Android APK, or already-running jobs require full deploy/restart.

    Returns:
        capability: dict with 'supported' bool and 'reason' if unsupported
    """
    try:
        # If we can call refresh_live_runtime without exception, capability exists.
        _agents.refresh_live_runtime()
        return {
            "supported": True,
            "reason": None,
            "check_utc": _utc_iso(),
        }
    except Exception as exc:
        error_text = str(exc).lower()
        # Distinguish between "endpoint not implemented" vs other transient errors
        if "404" in error_text or "not found" in error_text or "not implemented" in error_text:
            return {
                "supported": False,
                "reason": "refresh_endpoint_not_found",
                "detail": "Live runtime refresh is not available in this server version. Server code or deployment changes require a restart.",
                "check_utc": _utc_iso(),
                "remediation": "Restart the server to apply code or policy changes.",
            }
        # Other errors: fail closed, do not assume refresh works
        return {
            "supported": False,
            "reason": "refresh_capability_check_failed",
            "detail": str(exc),
            "check_utc": _utc_iso(),
            "remediation": "Verify server health and check logs before retrying.",
        }


def _runtime_refresh_payload() -> Dict[str, Any]:
    """G6N7: Harden runtime refresh semantics with explicit scope boundaries and drift handling.

    Live refresh applies ONLY to policy/prompt/config changes that can take effect
    immediately for new work. Code changes, APK builds, and in-flight job state
    are NOT affected by live refresh.

    Drift detection: if refresh endpoint is unavailable (404), fail closed with
    explicit remediation steps.
    """
    getattr(_runtime_policy.load_runtime_policy, "cache_clear", lambda: None)()
    refresh_inventory = _runtime_policy.runtime_refresh_boundary_inventory()
    mission_contract = _plane_mission_contract.read_plane_mission_contract()
    if not bool(mission_contract.get("ok")):
        return {
            "schema_version": "admin_server_runtime_refresh_v1",
            "utc": _utc_iso(),
            "ok": False,
            "reason": "mission_contract_unavailable",
            "detail": _clean_text(mission_contract.get("summary")) or "Canonical plane mission contract could not be loaded.",
            "remediation": "Repair the canonical plane mission contract before refreshing runtime-facing framing.",
            "message": "Live runtime refresh blocked because mission framing is unavailable.",
            "scope": {
                "applies_to": "future_work_started_after_refresh",
                "does_not_apply_to": "in_flight_turns_or_jobs",
                "source_of_truth": "mission_contract_failure",
            },
            "mission_contract": mission_contract,
            "runtime_refresh_boundary": refresh_inventory,
        }
    mission_projection = dict(mission_contract.get("runtime_projection") or {})
    refresh_scope = {
        "applies_to": "future_work_started_after_refresh",
        "does_not_apply_to": "in_flight_turns_or_jobs",
        "live_refreshable": [
            "runtime_policy.properties",
            "prompt_files",
            "agent_config",
            "orchestrator_defaults",
            "experiment_control_action_policy",
        ],
        "future_turn_only_keys": list(refresh_inventory.get("future_turn_only_keys") or []),
        "future_job_only_keys": list(refresh_inventory.get("future_job_only_keys") or []),
        "restart_required_keys": list(refresh_inventory.get("restart_required_keys") or []),
        "env_override_keys": [
            str(row.get("key") or "")
            for row in list(refresh_inventory.get("env_overrides_active") or [])
            if str(row.get("key") or "").strip()
        ],
        "deploy_gated": [
            "server_code",
            "android_apk",
            "in_flight_job_state",
            "runtime_deployment",
        ],
        "mission_refresh_scope_summary": _clean_text(mission_projection.get("refresh_scope_summary")),
        "mission_claim_ceiling_summary": _clean_text(mission_projection.get("claim_ceiling_summary")),
        "mission_overreach_guardrails": list(mission_projection.get("overreach_guardrails") or []),
        "mission_refresh_scope_reload_limits": list(mission_projection.get("refresh_scope_reload_limits") or []),
        "mission_contract_source_fingerprint": _clean_text(mission_projection.get("source_fingerprint")),
        "code_reload": "not_performed",
        "drift_semantics": "unsupported_refresh claims are treated as capability gaps, not transient errors",
        "source_of_truth": "plane_mission_contract_and_runtime_policy_inventory",
    }

    # G6N7: Capability check first. If refresh is unsupported, fail closed.
    capability = _refresh_capability_check()
    if not capability.get("supported", False):
        return {
            "schema_version": "admin_server_runtime_refresh_v1",
            "utc": _utc_iso(),
            "ok": False,
            "reason": capability.get("reason"),
            "detail": capability.get("detail"),
            "remediation": capability.get("remediation"),
            "message": "Live runtime refresh is not supported; server restart required for changes.",
            "scope": {
                **refresh_scope,
                "live_refreshable": [],
                "source_of_truth": "capability_check_result",
            },
            "capability_check": capability,
            "mission_contract": {
                "ok": True,
                "runtime_projection": mission_projection,
            },
            "runtime_refresh_boundary": refresh_inventory,
        }

    # Refresh succeeded. Report what changed and explicit scope.
    agents_state = _agents.refresh_live_runtime()
    orchestrator_state = _agent_orch.current_runtime_defaults()
    control_policy_refresh = _refresh_experiment_control_policy_cache()
    ok = bool(control_policy_refresh.get("ok", True))
    message = _clean_text(mission_projection.get("refresh_scope_summary"))
    if not ok:
        message = "Live runtime refresh kept the last known-good experiment control policy for some cached projects. Future work keeps using the retained snapshot until a later refresh succeeds."
    return {
        "schema_version": "admin_server_runtime_refresh_v1",
        "utc": _utc_iso(),
        "ok": ok,
        "message": message,
        "scope": {
            **refresh_scope,
            "source_of_truth": "capability_check_passed",
        },
        "mission_contract": {
            "ok": True,
            "runtime_projection": mission_projection,
        },
        "agents": agents_state,
        "orchestrator": orchestrator_state,
        "experiment_control_policy_refresh": control_policy_refresh,
        "capability_check": {
            "supported": True,
            "check_utc": _utc_iso(),
        },
        "runtime_refresh_boundary": refresh_inventory,
    }

def _confirmation_projection(
    *,
    action_id: str,
    event: Dict[str, Any],
    queue_status: Dict[str, Any],
    operator_snapshot: Dict[str, Any],
) -> Dict[str, Any]:
    invocation = dict(event.get("invocation") or {})
    if invocation.get("ok") is False:
        return {
            "state": "failed",
            "summary": _clean_text(invocation.get("error")) or "Control invocation failed.",
            "durable_signal": "invocation_error",
        }

    queue_state = dict(queue_status.get("state") or {})
    queue_status_detail = _clean_text(queue_state.get("status_detail") or queue_state.get("status")).lower()
    queue_running = bool(queue_state.get("active")) or queue_status_detail in _RUNNING_QUEUE_STATES
    stop_requested = bool(queue_state.get("stop_requested"))
    queue_stopped = (not bool(queue_state.get("active"))) and (_clean_text(queue_state.get("status")).lower() in _STOPPED_QUEUE_STATES)

    if action_id == "execution_queue_start":
        if queue_running:
            return {
                "state": "confirmed",
                "summary": "Execution queue shows active/running state from durable queue truth.",
                "durable_signal": "execution_queue.state.active",
            }
        return {
            "state": "pending",
            "summary": "Start was requested, but durable queue state has not shown active/running yet.",
            "durable_signal": "execution_queue.state.active=false",
        }

    if action_id == "execution_queue_stop_after_current":
        if stop_requested or queue_stopped:
            return {
                "state": "confirmed",
                "summary": "Queue stop intent is reflected by durable queue stop_requested/paused truth.",
                "durable_signal": "execution_queue.stop_requested_or_stopped",
            }
        return {
            "state": "pending",
            "summary": "Stop was requested, but durable queue state has not reflected stop intent yet.",
            "durable_signal": "execution_queue.stop_requested=false",
        }

    if action_id == "refresh_runtime":
        # G6N7: Explicit confirmation semantics for runtime refresh.
        # Only explicit unsupported-capability signals are permanent failures.
        invocation = dict(event.get("invocation") or {})
        response = dict(invocation.get("response") or {})

        if invocation.get("ok") is True and response.get("ok") is False:
            reason = _clean_text(response.get("reason", "")).lower()
            remediation = _clean_text(response.get("remediation", ""))
            if "endpoint_not_found" in reason or reason == "refresh_endpoint_not_found":
                return {
                    "state": "failed",
                    "summary": f"Runtime refresh not supported in this deployment: {response.get('detail', reason)}. {remediation}",
                    "durable_signal": "refresh_capability_unavailable",
                }
            return {
                "state": "pending",
                "summary": f"Runtime refresh capability probe did not confirm success yet: {response.get('detail', reason) or reason}. {remediation}".strip(),
                "durable_signal": "refresh_capability_probe_inconclusive",
            }

        snapshot_utc = _parse_utc(operator_snapshot.get("generated_utc") or operator_snapshot.get("utc"))
        requested_utc = _parse_utc(event.get("requested_utc"))
        backend = dict(operator_snapshot.get("backend_projection") or {})
        backend_state = _clean_text(backend.get("state")).lower()
        refresh_scope = dict(response.get("scope") or {})
        capability_check = dict(response.get("capability_check") or {})
        refresh_confirmed = bool(invocation.get("ok") is True and response.get("ok") is True and capability_check.get("supported") is not False)
        if (
            refresh_confirmed
            and snapshot_utc is not None
            and requested_utc is not None
            and snapshot_utc >= requested_utc
            and backend_state != "unreachable"
            and _clean_text(refresh_scope.get("applies_to")) == "future_work_started_after_refresh"
        ):
            return {
                "state": "confirmed",
                "summary": "Runtime refresh confirmed for future work by post-request durable projection evidence. In-flight turns/jobs are unchanged.",
                "durable_signal": "refresh_response_ok_and_operator_snapshot.generated_utc>=requested_utc",
                "scope_note": "Changes apply only to future work; in-flight turns/jobs retain prior settings.",
            }
        return {
            "state": "pending",
            "summary": "Runtime refresh requested for future work; waiting for post-request durable projection evidence. In-flight turns/jobs remain unchanged.",
            "durable_signal": "operator_snapshot_not_newer",
            "scope_note": "When confirmed, changes apply only to future work.",
        }

    if action_id in {
        "watchdog_step_audit",
        "watchdog_step_selfheal",
        "watchdog_enable_timers",
        "watchdog_disable_timers",
        "watchdog_start_timers",
        "watchdog_stop_timers",
    }:
        response = dict(invocation.get("response") or {})
        if bool(response.get("ok")):
            return {
                "state": "confirmed",
                "summary": "Watchdog timer action completed.",
                "durable_signal": "watchdog_timer_action_ok",
            }
        return {
            "state": "failed",
            "summary": _clean_text(response.get("error")) or "Watchdog timer action failed.",
            "durable_signal": "watchdog_timer_action_failed",
        }

    return {
        "state": "unknown",
        "summary": "No confirmation rule exists for this control action.",
        "durable_signal": "missing_rule",
    }


def _drill_down(
    *,
    current_workflow_id: str,
    workflow_items: List[Dict[str, Any]],
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "queue": {
            "api_path": "/api/mobile/backlog/execution_queue",
            "label": "Execution queue state and events",
        },
        "workflows": {
            "api_path": "/api/agents/orchestration/list?limit=50",
            "label": "Recent orchestration workflows",
        },
        "scheduler": {
            "api_path": "/api/schedules/jobs",
            "label": "Scheduled jobs and scheduler service status",
        },
    }
    if current_workflow_id:
        out["workflow_current"] = {
            "api_path": f"/api/agents/orchestration/status?workflow_id={current_workflow_id}&events_limit=120",
            "label": "Current workflow runtime detail",
        }
    elif workflow_items:
        first = _clean_text(workflow_items[0].get("workflow_id"))
        if first:
            out["workflow_current"] = {
                "api_path": f"/api/agents/orchestration/status?workflow_id={first}&events_limit=120",
                "label": "Most recent workflow runtime detail",
            }
    return out


def _forensic_entry_points(
    *,
    current_workflow_id: str,
    workflow_items: List[Dict[str, Any]],
    scheduled_jobs: List[Dict[str, Any]],
) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = [
        {
            "kind": "operator_snapshot",
            "label": "Operator snapshot with truth conflicts and evidence sections",
            "api_path": "/api/admin/lab_assistant/snapshot",
        },
        {
            "kind": "delegation_proof",
            "label": "Delegated execution proof report and receipt pointers",
            "api_path": "/api/admin/lab_assistant/delegation_proof",
        },
        {
            "kind": "execution_truth_ownership",
            "label": "Execution truth ownership and durable evidence ownership boundaries",
            "api_path": "/api/admin/lab_assistant/execution_truth_ownership",
        },
        {
            "kind": "queue_runtime",
            "label": "Execution queue runtime status and recent events",
            "api_path": "/api/mobile/backlog/execution_queue",
        },
    ]

    if current_workflow_id:
        out.append(
            {
                "kind": "workflow_status",
                "label": "Current workflow runtime state and event stream",
                "api_path": f"/api/agents/orchestration/status?workflow_id={current_workflow_id}&events_limit=120",
            }
        )
        out.append(
            {
                "kind": "workflow_conversation",
                "label": "Current workflow role conversation replay",
                "api_path": f"/api/agents/orchestration/conversation?workflow_id={current_workflow_id}&events_limit=240",
            }
        )

    for row in list(workflow_items or [])[:3]:
        binding = dict(row.get("binding") or {})
        binding_type = _clean_text(binding.get("type")).lower()
        binding_id = _clean_text(binding.get("id"))
        workflow_id = _clean_text(row.get("workflow_id"))
        if binding_type not in {"ledger_uid", "task_id"} or not binding_id:
            continue
        for role in ("orchestrator", "builder", "verifier"):
            out.append(
                {
                    "kind": "bound_transcript",
                    "label": f"{role} session transcript for bound workflow {workflow_id or binding_id}",
                    "api_path": f"/api/agents/session?role={role}&{binding_type}={binding_id}&tail=120",
                }
            )

    for row in list(scheduled_jobs or [])[:3]:
        job_id = _clean_text(row.get("job_id"))
        if not job_id:
            continue
        out.append(
            {
                "kind": "scheduled_job",
                "label": f"Scheduled job detail and receipt list for {job_id}",
                "api_path": f"/api/schedules/job?job_id={job_id}",
            }
        )

    return out


def _artifact_inspection(
    *,
    operator_snapshot: Dict[str, Any],
    workflow_items: List[Dict[str, Any]],
    scheduled_jobs: List[Dict[str, Any]],
) -> Dict[str, Any]:
    task_rows = list(operator_snapshot.get("active_tasks") or [])
    proof = dict(operator_snapshot.get("delegation_proof") or {})
    proof_summary = dict(proof.get("summary") or {})
    proof_paths = dict(proof.get("report_paths") or {})

    evidence_refs: List[str] = []
    for task in task_rows:
        for raw in list(task.get("evidence_refs") or []):
            ref = _clean_text(raw)
            if ref and ref not in evidence_refs:
                evidence_refs.append(ref)

    classified = {
        "receipt": 0,
        "report": 0,
        "residue": 0,
        "transcript": 0,
        "other": 0,
    }
    for ref in evidence_refs:
        lower = ref.lower()
        if "/repo_residue/" in lower or lower.endswith("/repo_residue") or lower.endswith("repo_residue/summary.json"):
            classified["residue"] += 1
        elif "/receipts/" in lower or "/scheduled_jobs/receipts/" in lower:
            classified["receipt"] += 1
        elif "/reports/" in lower:
            classified["report"] += 1
        elif "/agent_chat/" in lower or "session__" in lower:
            classified["transcript"] += 1
        else:
            classified["other"] += 1

    links: List[Dict[str, str]] = [
        {
            "artifact_kind": "proof_report",
            "summary": "Latest delegated execution proof report and receipt metadata.",
            "api_path": "/api/admin/lab_assistant/delegation_proof",
        },
        {
            "artifact_kind": "snapshot_evidence",
            "summary": "Operator snapshot evidence section with conflict context.",
            "api_path": "/api/admin/lab_assistant/snapshot",
        },
        {
            "artifact_kind": "execution_reconciliation",
            "summary": "Durable execution truth ownership and reconciliation boundaries.",
            "api_path": "/api/admin/lab_assistant/execution_truth_ownership",
        },
    ]

    for row in list(workflow_items or [])[:2]:
        workflow_id = _clean_text(row.get("workflow_id"))
        if not workflow_id:
            continue
        links.append(
            {
                "artifact_kind": "workflow_runtime",
                "summary": f"Workflow event history for {workflow_id}.",
                "api_path": f"/api/agents/orchestration/status?workflow_id={workflow_id}&events_limit=120",
            }
        )

    for row in list(scheduled_jobs or [])[:2]:
        job_id = _clean_text(row.get("job_id"))
        if not job_id:
            continue
        links.append(
            {
                "artifact_kind": "scheduled_receipts",
                "summary": f"Scheduled receipts and runs for {job_id}.",
                "api_path": f"/api/schedules/job?job_id={job_id}",
            }
        )

    return {
        "evidence_ref_count": len(evidence_refs),
        "classified_ref_counts": classified,
        "proof_overall_status": _clean_text(proof_summary.get("overall_status")),
        "proof_run_id": _clean_text(proof.get("proof_run_id")),
        "proof_receipts_dir": _clean_text(proof_paths.get("receipts_dir")),
        "proof_reports_dir": _clean_text(proof_paths.get("reports_dir")),
        "inspection_links": links,
    }


def _triage_projection(
    *,
    operator_snapshot: Dict[str, Any],
    queue_status: Dict[str, Any],
    watchdog_projection: Dict[str, Any],
    disagreements: List[Dict[str, Any]],
    current_workflow_id: str,
    workflow_detail: Dict[str, Any],
    replay_entry_points: List[Dict[str, str]],
) -> Dict[str, Any]:
    conflicts: List[Dict[str, str]] = []
    for row in list((operator_snapshot.get("operator_summary") or {}).get("truth_conflicts") or []):
        conflicts.append(
            {
                "source": "operator_snapshot",
                "kind": _clean_text(row.get("kind")),
                "summary": _clean_text(row.get("summary")),
                "task_id": _clean_text(row.get("task_id")),
            }
        )
    for row in disagreements:
        conflicts.append(
            {
                "source": "queue_vs_workflow",
                "kind": _clean_text(row.get("kind")),
                "summary": _clean_text(row.get("summary")),
                "task_id": "",
            }
        )

    proof = dict(operator_snapshot.get("delegation_proof") or {})
    proof_summary = dict(proof.get("summary") or {})
    proof_freshness = dict(proof.get("freshness") or {})
    task_register = dict(operator_snapshot.get("task_register") or {})
    snapshot_projection = dict(task_register.get("snapshot_projection") or {})
    execution_surface = dict(operator_snapshot.get("execution_surface") or {})
    status_contract = dict(operator_snapshot.get("status_contract") or {})
    queue_batch = dict(queue_status.get("batch_report") or {})
    queue_state = dict(queue_status.get("state") or {})
    task_status_contracts = [
        dict(row.get("status_contract") or {})
        for row in list(operator_snapshot.get("active_tasks") or []) + list(operator_snapshot.get("completed_tasks") or [])
        if isinstance(row, dict)
    ]

    gaps: List[Dict[str, str]] = []
    if current_workflow_id and workflow_detail and _clean_text(workflow_detail.get("error")):
        gaps.append(
            {
                "code": "workflow_detail_unavailable",
                "summary": "Current workflow detail could not be loaded from durable orchestration status.",
                "next_action": "Inspect orchestration status and conversation endpoints for this workflow.",
            }
        )

    proof_state = _clean_text(proof_summary.get("overall_status")).lower()
    proof_reason = _clean_text(proof_freshness.get("reason"))
    if proof_state in {"missing", "stale", "proof_gap"} or proof_reason:
        gaps.append(
            {
                "code": "delegation_proof_missing_or_stale",
                "summary": "Delegated execution proof is missing or stale; completion claims are under-evidenced.",
                "next_action": "Open delegation proof and execution truth ownership views before confirming outcome.",
            }
        )

    if bool(snapshot_projection.get("stale")):
        gaps.append(
            {
                "code": "task_register_snapshot_projection_stale",
                "summary": "Task-register snapshot projection is stale; operator freshness is under-evidenced.",
                "next_action": "Refresh or rebuild task-register projection before trusting status claims.",
            }
        )

    task_contract_conflicts = [
        row for row in task_status_contracts if _clean_text(row.get("contract_state")).lower() == "degraded" and bool(row.get("mismatch"))
    ]
    for row in task_contract_conflicts:
        conflicts.append(
            {
                "source": "task_status_contract",
                "kind": "task_status_projection_mismatch",
                "summary": _clean_text(row.get("mismatch_reason")) or "Canonical task status disagrees with projected status.",
                "task_id": _clean_text(row.get("task_id")),
            }
        )

    execution_surface_state = _clean_text(execution_surface.get("state")).lower()
    if execution_surface_state in {"degraded", "unknown"}:
        gaps.append(
            {
                "code": "execution_surface_projection_degraded",
                "summary": "Execution-surface projection is degraded or unknown; surface ownership truth is incomplete.",
                "next_action": "Inspect execution surface bindings before treating the snapshot as settled.",
            }
        )

    status_contract_state = _clean_text(status_contract.get("contract_state")).lower()
    if status_contract_state in {"degraded", "unknown"}:
        gaps.append(
            {
                "code": "status_contract_degraded",
                "summary": "Lower-level status-contract fields are degraded or unknown even though top-level truth conflicts may be empty.",
                "next_action": "Inspect task status projection and execution surface samples in the operator snapshot.",
            }
        )
    batch_outcome = _clean_text(queue_batch.get("outcome")).lower()
    remaining_count = int(queue_batch.get("remaining_count") or 0)
    last_failed_stage = _clean_text(queue_state.get("last_failed_stage")).lower()
    if remaining_count > 0 and batch_outcome in {"failed_partial", "paused_partial"}:
        gaps.append(
            {
                "code": "execution_queue_remaining_backlog_after_partial_failure",
                "summary": "The queue still has remaining backlog after a partial failure or paused run, so the plane may be in slow wrongness rather than complete recovery.",
                "next_action": "Inspect queue batch_report, remaining items, and failure stage before trusting apparent idle or partial progress.",
            }
        )
    elif remaining_count > 0 and not bool(queue_state.get("active")) and last_failed_stage:
        gaps.append(
            {
                "code": "execution_queue_silent_paralysis_with_remaining_backlog",
                "summary": "The queue is not active, a failure stage is recorded, and backlog still remains; this is a silent paralysis risk.",
                "next_action": "Inspect queue failure stage, remaining backlog, and resumability before assuming work is merely quiet.",
            }
        )

    alertability_candidates = _watchdog_alertability_candidates(watchdog_projection)
    alertability = _watchdog_primary_alertability_projection(watchdog_projection)
    if not alertability_candidates and alertability:
        alertability_candidates = [alertability]
    if any(bool(item.get("false_calm_detected")) for item in alertability_candidates):
        gaps.append(
            {
                "code": "watchdog_false_calm_detected",
                "summary": "Open watchdog incidents exist while the latest watchdog summary is healthy or missing.",
                "next_action": "Inspect watchdog incident indexes and latest maintenance run before trusting the healthy-looking summary.",
            }
        )
    if any(bool(item.get("wrong_cause_detected")) for item in alertability_candidates):
        gaps.append(
            {
                "code": "watchdog_wrong_cause_projection",
                "summary": "Open watchdog incident causes do not match the latest surfaced watchdog root causes.",
                "next_action": "Inspect watchdog incident and root-cause artifacts to determine which failure cause is being hidden.",
            }
        )
    lag_seconds = 0
    for item in alertability_candidates:
        candidate_lag = int(item.get("detectability_lag_seconds") or 0)
        if candidate_lag != 0:
            lag_seconds = candidate_lag
            break
    if lag_seconds != 0:
        gaps.append(
            {
                "code": "watchdog_detectability_lag",
                "summary": "Watchdog incident truth is newer than the latest surfaced watchdog summary, so operator detectability is lagging.",
                "next_action": "Inspect latest watchdog report freshness and incident indexes before assuming the current summary is complete.",
            }
        )

    if not replay_entry_points:
        gaps.append(
            {
                "code": "replay_navigation_missing",
                "summary": "No forensic replay entry points were available for the current snapshot.",
                "next_action": "Verify workflow, scheduler, and transcript substrates are reachable.",
            }
        )

    if conflicts:
        state = "conflicted"
        headline = "Durable sources disagree; triage from provenance views before deciding."
    elif gaps:
        state = "degraded"
        headline = "Evidence is partial or stale; inspect replay artifacts before claiming completion."
    else:
        state = "clear"
        headline = "No cross-source conflict detected in this snapshot."

    next_steps = [
        {
            "summary": "Check operator snapshot conflicts and evidence sections.",
            "api_path": "/api/admin/lab_assistant/snapshot",
        },
        {
            "summary": "Review delegated execution proof freshness and traces.",
            "api_path": "/api/admin/lab_assistant/delegation_proof",
        },
        {
            "summary": "Inspect canonical ownership boundaries for execution truth.",
            "api_path": "/api/admin/lab_assistant/execution_truth_ownership",
        },
    ]
    if current_workflow_id:
        next_steps.append(
            {
                "summary": f"Replay current workflow events for {current_workflow_id}.",
                "api_path": f"/api/agents/orchestration/status?workflow_id={current_workflow_id}&events_limit=120",
            }
        )

    return {
        "state": state,
        "headline": headline,
        "conflict_count": len(conflicts),
        "conflicts": conflicts,
        "gap_count": len(gaps),
        "gaps": gaps,
        "next_steps": next_steps,
    }


def _ui_projection_contract(operator_snapshot: Dict[str, Any]) -> Dict[str, Any]:
    backend_projection = dict(operator_snapshot.get("backend_projection") or {})
    status_contract = dict(operator_snapshot.get("status_contract") or {})
    projection_contract = dict(operator_snapshot.get("projection_contract") or {})
    backend_state = _clean_text(backend_projection.get("state")).lower() or "unknown"
    status_contract_state = _clean_text(status_contract.get("state") or status_contract.get("contract_state")).lower() or "unknown"
    state = backend_state if backend_state in {"reachable", "stale", "degraded", "reconnecting", "unreachable"} else "unknown"
    if state == "reachable" and status_contract_state in {"degraded", "unknown"}:
        state = "degraded"
    headline_by_state = {
        "reachable": "Live backend truth is reachable.",
        "stale": "Cached projection is visible and must stay marked stale.",
        "degraded": "Projection is degraded; partial truth must stay explicit.",
        "reconnecting": "Backend is reconnecting; continuity is not settled yet.",
        "unreachable": "Backend is unreachable; cached truth is advisory only.",
        "unknown": "Projection state is unknown; live truth is not established.",
    }
    return {
        "state": state,
        "headline": headline_by_state.get(state, headline_by_state["unknown"]),
        "canonical_operator_projection_api_path": _clean_text(
            dict(projection_contract.get("default_operator_projection") or {}).get("api_path")
        )
        or "/api/admin/lab_assistant/snapshot",
        "cached_truth_must_be_marked": bool(
            dict(projection_contract.get("projection_state") or {}).get("cached_truth_must_be_marked")
        ),
        "uses_cached_projection": bool(backend_projection.get("uses_cached_projection")),
        "projection_owner": _clean_text(dict(projection_contract.get("ownership") or {}).get("projection_owner"))
        or "web_console_projection",
        "canonical_truth_owner": _clean_text(dict(projection_contract.get("ownership") or {}).get("canonical_truth_owner"))
        or "BD05.task_register",
        "status_contract_state": status_contract_state,
    }


def operations_console_snapshot(repo_root: Path, *, workflow_limit: int = 50, control_limit: int = 30) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    workflow_lim = max(1, min(200, int(workflow_limit)))
    controls_lim = max(1, min(200, int(control_limit)))

    operator_snapshot = _lab_operator_snapshot.build_operator_snapshot(root)
    queue_status = _execution_queue.status()
    scheduler_service = _scheduled_jobs.scheduler_service_status()
    scheduled_jobs = _scheduled_jobs.list_jobs(repo_root=root)
    workflows = _agent_orch.list_workflows(limit=workflow_lim)

    queue_state = dict(queue_status.get("state") or {})
    current_workflow_id = _clean_text(queue_state.get("current_workflow_id"))
    current_queue_workflow_status = _clean_text(queue_state.get("current_workflow_status"))
    workflow_detail: Dict[str, Any] = {}
    workflow_status = ""
    if current_workflow_id:
        try:
            workflow_detail = _agent_orch.get_status(workflow_id=current_workflow_id, events_limit=120)
            workflow_state = dict(workflow_detail.get("state") or {})
            workflow_status = _clean_text(workflow_state.get("status"))
        except Exception as exc:
            workflow_detail = {"error": str(exc), "workflow_id": current_workflow_id}

    disagreements: List[Dict[str, Any]] = []
    if current_workflow_id and current_queue_workflow_status and workflow_status:
        queue_cat = _queue_status_category(current_queue_workflow_status)
        workflow_cat = _workflow_status_category(workflow_status)
        if queue_cat != "unknown" and workflow_cat != "unknown" and queue_cat != workflow_cat:
            disagreements.append(
                {
                    "kind": "queue_workflow_status_disagreement",
                    "summary": "Queue and workflow panels disagree on the same active workflow status.",
                    "workflow_id": current_workflow_id,
                    "queue_status": current_queue_workflow_status,
                    "workflow_status": workflow_status,
                    "queue_category": queue_cat,
                    "workflow_category": workflow_cat,
                }
            )

    control_events = _read_jsonl(_control_events_path(root), limit=controls_lim)
    recent_controls: List[Dict[str, Any]] = []
    for row in reversed(control_events):
        action_id = _clean_text(row.get("action_id"))
        if not action_id:
            continue
        recent_controls.append(
            {
                "control_request_id": _clean_text(row.get("control_request_id")),
                "action_id": action_id,
                "requested_utc": _clean_text(row.get("requested_utc")),
                "writer": _clean_text(row.get("writer")),
                "params": dict(row.get("params") or {}),
                "invocation": dict(row.get("invocation") or {}),
                "confirmation": _confirmation_projection(
                    action_id=action_id,
                    event=row,
                    queue_status=queue_status,
                    operator_snapshot=operator_snapshot,
                ),
            }
        )

    workflow_items = list(workflows.get("items") or []) if isinstance(workflows.get("items"), list) else []
    mission_contract = _plane_mission_contract.read_plane_mission_contract()
    mission_projection = dict(mission_contract.get("runtime_projection") or {}) if bool(mission_contract.get("ok")) else {}
    dashboard = {
        "operator_state": _clean_text(operator_snapshot.get("operator_state")),
        "status_language": dict((operator_snapshot.get("operator_summary") or {}).get("status_language") or {}),
        "active_task_count": int(len(list(operator_snapshot.get("active_tasks") or []))),
        "open_loop_count": int(len(list(operator_snapshot.get("open_loops") or []))),
        "queue_status": _clean_text(queue_state.get("status")),
        "queue_status_detail": _clean_text(queue_state.get("status_detail")),
        "queue_active": bool(queue_state.get("active")),
        "workflow_count": int(workflows.get("count") or 0),
        "scheduler_running": bool(scheduler_service.get("running")),
        "scheduler_last_tick_utc": _clean_text(scheduler_service.get("last_tick_utc")),
        "scheduled_job_count": int(len(scheduled_jobs)),
        "backend_projection": dict(operator_snapshot.get("backend_projection") or {}),
        "projection_state": _ui_projection_contract(operator_snapshot),
    }

    runtime_controls = {
        "available_actions": [
            {
                "action_id": "refresh_runtime",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": _clean_text(mission_projection.get("refresh_scope_summary"))
                or "Refresh runtime policy snapshots for future work only; in-flight turns/jobs keep prior settings.",
            },
            {
                "action_id": "execution_queue_start",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": "Start or resume execution queue processing.",
            },
            {
                "action_id": "execution_queue_stop_after_current",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": "Request queue stop after current item reaches a checkpoint.",
            },
            {
                "action_id": "watchdog_step_audit",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": "Run the watchdog audit immediately from the plane.",
            },
            {
                "action_id": "watchdog_step_selfheal",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": "Run the watchdog self-heal decision pass immediately; repair work is packaged for scheduler execution and direct watchdog mutation stays forbidden.",
            },
            {
                "action_id": "watchdog_enable_timers",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": "Enable the host watchdog timers.",
            },
            {
                "action_id": "watchdog_disable_timers",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": "Disable the host watchdog timers.",
            },
            {
                "action_id": "watchdog_restart_timers",
                "method": "POST",
                "api_path": "/api/admin/lab_assistant/web_operations/control",
                "summary": "Restart the host watchdog timers without changing their enablement policy.",
            },
        ],
        "refresh_scope": {
            "applies_to": "future_work_started_after_refresh",
            "does_not_apply_to": "in_flight_turns_or_jobs",
            "summary": _clean_text(mission_projection.get("refresh_scope_summary")),
            "claim_ceiling_summary": _clean_text(mission_projection.get("claim_ceiling_summary")),
            "source_fingerprint": _clean_text(mission_projection.get("source_fingerprint")),
            "evidence_source": "operator_snapshot",
        },
        "watchdog": _watchdog_projection(root),
        "recent_controls": recent_controls,
        "audit_history": _control_action_audit.recent_control_action_history(root, limit=12),
    }

    replay_entry_points = _forensic_entry_points(
        current_workflow_id=current_workflow_id,
        workflow_items=workflow_items,
        scheduled_jobs=scheduled_jobs,
    )
    artifact_inspection = _artifact_inspection(
        operator_snapshot=operator_snapshot,
        workflow_items=workflow_items,
        scheduled_jobs=scheduled_jobs,
    )
    error_triage = _triage_projection(
        operator_snapshot=operator_snapshot,
        queue_status=queue_status,
        watchdog_projection=runtime_controls["watchdog"],
        disagreements=disagreements,
        current_workflow_id=current_workflow_id,
        workflow_detail=workflow_detail,
        replay_entry_points=replay_entry_points,
    )

    return {
        "schema_version": "lab_assistant_web_operations_console_v1",
        "utc": _utc_iso(),
        "dashboard": dashboard,
        "projection_contract": dict(operator_snapshot.get("projection_contract") or {}),
        "drill_down": _drill_down(current_workflow_id=current_workflow_id, workflow_items=workflow_items),
        "queue": {
            "status": queue_status,
            "current_workflow": workflow_detail if current_workflow_id else {},
        },
        "workflows": workflows,
        "scheduler": {
            "service": scheduler_service,
            "jobs": scheduled_jobs,
        },
        "error_triage": error_triage,
        "forensic_replay": {
            "entry_points": replay_entry_points,
            "artifact_inspection": artifact_inspection,
            "gaps": list(error_triage.get("gaps") or []),
        },
        "watchdog": dict(runtime_controls.get("watchdog") or {}),
        "runtime_controls": runtime_controls,
        "disagreements": disagreements,
    }


def invoke_runtime_control(
    repo_root: Path,
    *,
    action_id: str,
    params: Optional[Dict[str, Any]] = None,
    writer: str = "web_operations_console",
) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    safe_action = _clean_text(action_id).lower()
    request_id = f"wop_{uuid.uuid4().hex[:16]}"
    requested_utc = _utc_iso()
    args = dict(params or {})

    if safe_action not in {
        "refresh_runtime",
        "execution_queue_start",
        "execution_queue_stop_after_current",
        "watchdog_step_audit",
        "watchdog_step_selfheal",
        "watchdog_enable_timers",
        "watchdog_disable_timers",
        "watchdog_restart_timers",
        "watchdog_start_timers",
        "watchdog_stop_timers",
    }:
        raise ValueError("unsupported action_id")

    invocation: Dict[str, Any]
    try:
        if safe_action == "refresh_runtime":
            invocation = {"ok": True, "response": _runtime_refresh_payload()}
        elif safe_action == "execution_queue_start":
            invocation = {"ok": True, "response": _execution_queue.start_queue(options=args.get("queue_options"))}
        elif safe_action == "watchdog_step_audit":
            invocation = {"ok": True, "response": _watchdog_timer.invoke_action("step_audit")}
        elif safe_action == "watchdog_step_selfheal":
            invocation = {"ok": True, "response": _watchdog_timer.invoke_action("step_selfheal")}
        elif safe_action == "watchdog_enable_timers":
            invocation = {"ok": True, "response": _watchdog_timer.invoke_action("enable")}
        elif safe_action == "watchdog_disable_timers":
            invocation = {"ok": True, "response": _watchdog_timer.invoke_action("disable")}
        elif safe_action == "watchdog_restart_timers":
            invocation = {"ok": True, "response": _watchdog_timer.invoke_action("restart")}
        elif safe_action == "watchdog_start_timers":
            invocation = {"ok": True, "response": _watchdog_timer.invoke_action("start")}
        elif safe_action == "watchdog_stop_timers":
            invocation = {"ok": True, "response": _watchdog_timer.invoke_action("stop")}
        else:
            invocation = {"ok": True, "response": _execution_queue.stop_queue(mode="after_current")}
    except Exception as exc:
        invocation = {"ok": False, "error": str(exc)}

    # G6N7: Capture full response for drift detection and failure semantics.
    # For refresh_runtime, the response payload itself contains ok/reason/detail.
    event_invocation: Dict[str, Any] = {
        "ok": bool(invocation.get("ok")),
        "error": _clean_text(invocation.get("error")),
    }
    if safe_action == "refresh_runtime" and invocation.get("response"):
        response = dict(invocation.get("response") or {})
        event_invocation["response"] = {
            "ok": response.get("ok"),
            "reason": response.get("reason"),
            "detail": response.get("detail"),
            "scope": dict(response.get("scope") or {}),
            "capability_check": dict(response.get("capability_check") or {}),
        }
    elif safe_action.startswith("watchdog_") and invocation.get("response"):
        response = dict(invocation.get("response") or {})
        event_invocation["response"] = {
            "ok": response.get("ok"),
            "action": response.get("action"),
            "returncode": response.get("returncode"),
        }

    event = {
        "schema_version": _CONTROL_EVENT_SCHEMA,
        "event": "control_invoked",
        "control_request_id": request_id,
        "requested_utc": requested_utc,
        "writer": _clean_text(writer) or "web_operations_console",
        "action_id": safe_action,
        "params": args,
        "invocation": event_invocation,
    }
    with _LOCK:
        _append_jsonl(_control_events_path(root), event)

    snapshot = operations_console_snapshot(root)
    confirmation = {"state": "unknown", "summary": "No durable confirmation available."}
    for row in list((snapshot.get("runtime_controls") or {}).get("recent_controls") or []):
        if _clean_text(row.get("control_request_id")) == request_id:
            confirmation = dict(row.get("confirmation") or {})
            break

    return {
        "schema_version": "lab_assistant_web_operations_control_result_v1",
        "utc": _utc_iso(),
        "control_request_id": request_id,
        "action_id": safe_action,
        "invocation": event["invocation"],
        "confirmation": confirmation,
    }
