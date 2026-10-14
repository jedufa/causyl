#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, TypeVar

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore[assignment]

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_URL = "http://localhost:8787"
DEFAULT_TIMEZONE = "Europe/Vienna"
DEFAULT_STARTUP_GRACE_S = 30.0
PIS_UID_RE = re.compile(r"(PIS-\d{8}-\d{6}Z-([A-Za-z0-9]{4}))")
JOB_UID4_RE = re.compile(r"(?:^|[_-])([A-Za-z0-9]{4})(?:[_-]|$)")
EXPLICIT_PASS_JOB_RE = re.compile(r"_p(\d+)of(\d+)$")

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduler_projection_contract as _scheduler_projection_contract
from infra.controlplane import scheduled_jobs as _scheduled_jobs
from infra.controlplane.sched import operator_backend as _sched_v2_backend
from infra.webui.ledger import ledger_storage


_LEDGER_META_CACHE: Dict[str, Dict[str, Any]] = {}
_LEDGER_DIR_CACHE: Dict[str, Optional[Path]] = {}


def _utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clear() -> None:
    sys.stdout.write("\033[2J\033[H")
    sys.stdout.flush()


def _api_get(base_url: str, path: str) -> Dict[str, Any]:
    url = urllib.parse.urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
    req = urllib.request.Request(url, headers={"Accept": "application/json"}, method="GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return data if isinstance(data, dict) else {}


def _is_local_base_url(base_url: str) -> bool:
    parsed = urllib.parse.urlparse(str(base_url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    return host in {"", "localhost", "127.0.0.1", "::1"}


def _local_jobs_payload(base_url: str, *, reason: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": "scheduled_jobs_list_v1",
        "utc": _utc_now(),
        "jobs": _scheduled_jobs.list_jobs(
            repo_root=REPO_ROOT,
            include_context_resolution=False,
            include_reconciliation=False,
        ),
        "events": _scheduled_jobs.list_execution_events(repo_root=REPO_ROOT, limit=50),
        "local_fallback": True,
    }
    if reason:
        payload["summary"] = f"watch local fallback active: {reason}"
    try:
        if _sched_v2_backend.selected_backend() == "v2":
            payload["scheduler_v2"] = dict(_sched_v2_backend.v2_surface_bundle(repo_root=REPO_ROOT))
    except Exception as exc:
        payload["scheduler_v2_error"] = str(exc)
    try:
        health = _api_get(base_url, "/api/schedules/health")
    except Exception:
        health = {}
    if isinstance(health, dict):
        for key in ("scheduler", "scheduler_health", "watchdog", "watchdog_health", "healer", "healer_health", "scheduler_v2"):
            if key in health:
                payload[key] = health.get(key)
    return payload


def _read_json_file(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _parse_iso(raw: str) -> float:
    txt = str(raw or "").strip()
    if not txt:
        return 0.0
    try:
        return dt.datetime.fromisoformat(txt.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _fmt_duration(seconds: float) -> str:
    total = max(0, int(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes:02d}m {secs:02d}s"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def _estimate_tokens_from_text(text: Any) -> int:
    payload = str(text or "")
    if not payload:
        return 0
    return max(1, int(round(len(payload.encode("utf-8")) / 4.0)))


def _estimate_tokens_from_bytes(raw: Any) -> int:
    try:
        count = int(raw or 0)
    except Exception:
        count = 0
    if count <= 0:
        return 0
    return max(1, int(round(count / 4.0)))


def _parse_utc_dt(raw: Any) -> Optional[datetime]:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None


def _active_timing(row: Dict[str, Any]) -> Dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    policy = row.get("policy") if isinstance(row.get("policy"), dict) else {}
    started_utc = str(state.get("active_started_utc") or "").strip()
    started_ts = _parse_iso(started_utc)
    timeout_s = int(policy.get("max_runtime_seconds") or 0)
    now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
    if not started_ts or timeout_s <= 0:
        return {
            "started_utc": started_utc,
            "elapsed_s": 0,
            "timeout_s": timeout_s,
            "remaining_s": 0,
            "progress_ratio": 0.0,
            "active": False,
        }
    elapsed_s = max(0.0, now_ts - started_ts)
    remaining_s = max(0.0, float(timeout_s) - elapsed_s)
    progress_ratio = min(1.0, elapsed_s / float(timeout_s)) if timeout_s > 0 else 0.0
    return {
        "started_utc": started_utc,
        "elapsed_s": elapsed_s,
        "timeout_s": timeout_s,
        "remaining_s": remaining_s,
        "progress_ratio": progress_ratio,
        "active": True,
    }


def _progress_bar(ratio: float, *, width: int = 20) -> str:
    bounded = max(0.0, min(1.0, float(ratio)))
    filled = int(round(bounded * width))
    if filled > width:
        filled = width
    return "[" + ("#" * filled) + ("-" * (width - filled)) + f"] {int(round(bounded * 100)):d}%"


def _loop_iteration_status(row: Dict[str, Any], *, mode: str) -> str:
    if str(row.get("job_kind") or "").strip() != "build_verify_loop":
        return ""
    loop = row.get("loop") if isinstance(row.get("loop"), dict) else {}
    max_iterations = int(loop.get("max_iterations") or 0)
    if max_iterations <= 0:
        return ""
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    active_run_id = str(state.get("active_run_id") or "").strip()
    if mode == "active" and active_run_id:
        active_iteration = int(state.get("active_loop_iteration") or 0)
        active_stage = str(state.get("active_loop_stage") or "").strip()
        completed = int(state.get("active_loop_completed_iterations") or 0)
        if active_iteration > 0 and active_stage:
            stage_txt = f", {active_stage} running"
            return f"iteration: {active_iteration}/{max_iterations}{stage_txt}, completed={completed}/{max_iterations}"
        return f"iteration: active, exact step not tracked, budget={max_iterations}"
    if mode == "previous":
        receipt_path = str(state.get("last_receipt_path") or "").strip()
        if receipt_path:
            try:
                receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
            except Exception:
                receipt = {}
            loop_receipt = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
            completed = int(loop_receipt.get("completed_iterations") or 0)
            if completed > 0:
                return f"iterations: completed {completed}/{max_iterations}"
    return f"iteration budget: up to {max_iterations}"


def _fmt_time(raw: str, *, tz_name: str) -> str:
    txt = str(raw or "").strip()
    if not txt:
        return ""
    try:
        d = dt.datetime.fromisoformat(txt.replace("Z", "+00:00"))
        if ZoneInfo is not None:
            d = d.astimezone(ZoneInfo(tz_name))
            return d.strftime("%Y-%m-%d %H:%M:%S %Z")
        return d.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return txt


def _truncate(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if limit <= 0 or len(value) <= limit:
        return value
    if limit <= 1:
        return value[:limit]
    return value[: limit - 1] + "…"


def _short_clock(raw: str, *, tz_name: str) -> str:
    txt = _fmt_time(raw, tz_name=tz_name)
    if not txt:
        return ""
    parts = txt.split()
    if len(parts) >= 2:
        return parts[1]
    return txt


def _service_next_run(status: Dict[str, Any]) -> Tuple[str, str]:
    interval_s = int(status.get("interval_s") or 0)
    last_receipt_utc = str(status.get("last_receipt_utc") or "").strip()
    started_utc = str(status.get("started_utc") or "").strip()
    base_raw = last_receipt_utc or started_utc
    if interval_s <= 0 or not base_raw:
        return "", ""
    try:
        next_ts = _parse_iso(base_raw) + float(interval_s)
        now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
        remaining = max(0.0, next_ts - now_ts)
        next_utc = dt.datetime.fromtimestamp(next_ts, tz=dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        return next_utc, _fmt_duration(remaining)
    except Exception:
        return "", ""


def _watchdog_summary_lines(status: Dict[str, Any], *, tz_name: str, verbose: bool = False) -> List[str]:
    receipt = status.get("last_receipt") if isinstance(status.get("last_receipt"), dict) else {}
    if not receipt:
        return ["  summary: no watchdog receipt yet"]
    health = receipt.get("health") if isinstance(receipt.get("health"), dict) else {}
    findings = [str((row or {}).get("kind") or "").strip() for row in list(receipt.get("findings") or []) if isinstance(row, dict)]
    actions = [dict(row) for row in list(receipt.get("actions") or []) if isinstance(row, dict)]
    lines: List[str] = []
    health_txt = str(health.get("health") or "").strip()
    reason_txt = str(health.get("reason") or "").strip()
    generated = _fmt_time(str(receipt.get("utc") or receipt.get("generated_utc") or ""), tz_name=tz_name)
    if health_txt or reason_txt or generated:
        parts = []
        if generated:
            parts.append(f"last_run={generated}")
        if health_txt:
            parts.append(f"health={health_txt}")
        if reason_txt:
            parts.append(f"reason={reason_txt}")
        lines.append("  summary: " + " ".join(parts))
    if findings:
        lines.append("  findings: " + ", ".join(findings[:6]))
    action_names = [str(row.get("action") or "").strip() for row in actions if str(row.get("action") or "").strip()]
    if action_names:
        lines.append("  actions: " + ", ".join(action_names[:6]))
    interesting_actions = []
    for row in actions:
        action = str(row.get("action") or "").strip()
        if action == "resume_stalled_dependency_head":
            interesting_actions.append(
                f"  intervention: resumed {str(row.get('job_id') or '')} ok={str(bool(row.get('ok'))).lower()} idle_minutes={int(row.get('idle_minutes') or 0)}"
            )
        elif action == "scheduler_tick_for_progress_recovery":
            tick = row.get("tick") if isinstance(row.get("tick"), dict) else {}
            interesting_actions.append(
                f"  intervention: scheduler tick for {str(row.get('job_id') or '')} executed_count={int(tick.get('executed_count') or 0)}"
            )
        elif action == "execute_bounded_codex_escalation":
            interesting_actions.append(
                f"  intervention: codex escalation condition={str(row.get('condition_id') or '')} ok={str(bool(row.get('ok'))).lower()}"
            )
    if interesting_actions:
        lines.extend(interesting_actions[:4])
    elif not action_names:
        lines.append("  intervention: none")
    return lines


def _watchdog_busy_line(status: Dict[str, Any]) -> str:
    if not _watchdog_run_active(status):
        return ""
    activity = _watchdog_activity_hint(status)
    return activity or "audit/self-heal running"


def _active_count_disagreement_line(payload: Dict[str, Any], *, computed_active_count: int, color: bool) -> str:
    scheduler = payload.get("scheduler") if isinstance(payload.get("scheduler"), dict) else {}
    last_result = scheduler.get("last_result") if isinstance(scheduler.get("last_result"), dict) else {}
    try:
        reported_active_count = int(last_result.get("active_count"))
    except Exception:
        return ""
    if reported_active_count == int(computed_active_count):
        return ""
    return (
        f"{_label(color, 'watch_consistency', '1;33')} "
        f"{_style(color, f'row truth active={int(computed_active_count)} but scheduler.last_result active_count={reported_active_count}', '33')}"
    )


def _observation_integrity(payload: Dict[str, Any]) -> Dict[str, Any]:
    row = payload.get("observation_integrity") if isinstance(payload.get("observation_integrity"), dict) else {}
    scheduler_v2 = payload.get("scheduler_v2") if isinstance(payload.get("scheduler_v2"), dict) else {}
    advisory = (
        scheduler_v2.get("observation_advisory")
        if isinstance(scheduler_v2.get("observation_advisory"), dict)
        else {}
    )
    if row:
        merged = dict(row)
        if advisory:
            base_reasons = [
                str(item).strip()
                for item in list(merged.get("reasons") or [])
                if str(item).strip()
            ]
            advisory_reasons = [
                str(item).strip()
                for item in list(advisory.get("reasons") or [])
                if str(item).strip()
            ]
            merged["reasons"] = list(dict.fromkeys(base_reasons + advisory_reasons))
            base_status = str(merged.get("status") or "").strip().lower()
            advisory_status = str(advisory.get("status") or "").strip().lower()
            if advisory_status == "degraded" and base_status != "degraded":
                merged["status"] = "degraded"
                merged["advisory_only"] = True
                merged["summary"] = str(advisory.get("summary") or merged.get("summary") or "").strip()
                merged["last_error"] = str(advisory.get("last_error") or merged.get("last_error") or "").strip()
        return merged
    reasons: List[str] = []
    if bool(payload.get("jobs_unavailable")):
        reasons.append("jobs_unavailable")
    if bool(payload.get("snapshot_stale")):
        reasons.append("snapshot_stale")
    if bool(payload.get("snapshot_pending")):
        reasons.append("snapshot_pending")
    if bool(payload.get("snapshot_refreshing")):
        reasons.append("snapshot_refreshing")
    if str(payload.get("snapshot_last_error") or "").strip():
        reasons.append("snapshot_last_error")
    if advisory:
        reasons.extend(
            str(item).strip()
            for item in list(advisory.get("reasons") or [])
            if str(item).strip()
        )
    summary = str(payload.get("summary") or "").strip()
    last_error = str(payload.get("snapshot_last_error") or "").strip()
    if advisory:
        if not summary:
            summary = str(advisory.get("summary") or "").strip()
        if not last_error:
            last_error = str(advisory.get("last_error") or "").strip()
    reasons = list(dict.fromkeys(reasons))
    return {
        "status": "degraded"
        if reasons or str(advisory.get("status") or "").strip().lower() == "degraded"
        else "fresh",
        "advisory_only": bool(reasons),
        "reasons": reasons,
        "summary": summary,
        "last_error": last_error,
    }


def _observation_degraded(payload: Dict[str, Any]) -> bool:
    observation = _observation_integrity(payload)
    return str(observation.get("status") or "").strip().lower() == "degraded"


def _observation_summary_line(payload: Dict[str, Any], *, color: bool) -> str:
    observation = _observation_integrity(payload)
    if str(observation.get("status") or "").strip().lower() != "degraded":
        return ""
    reasons = [str(item).strip() for item in list(observation.get("reasons") or []) if str(item).strip()]
    summary = str(observation.get("summary") or "").strip()
    parts = ["status=degraded"]
    if reasons:
        parts.append("reasons=" + ",".join(reasons))
    if summary:
        parts.append(summary)
    return f"{_label(color, 'observation', '1;33')} " + _style(color, " ".join(parts), "33")


def _fresh_watchdog_status(status: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(status or {})
    root = (REPO_ROOT / "runtime" / "state" / "scheduler_watchdog" / "maintenance").resolve()
    candidates: List[Dict[str, Any]] = []
    for name in ("latest_audit.json", "latest_selfheal.json"):
        payload = _read_json_file(root / name)
        if payload:
            payload["_source_file"] = name
            candidates.append(payload)
    if not candidates:
        return current
    freshest = max(candidates, key=lambda row: _parse_iso(str(row.get("generated_utc") or row.get("utc") or "")))
    freshest_ts = _parse_iso(str(freshest.get("generated_utc") or freshest.get("utc") or ""))
    current_ts = _parse_iso(str(current.get("last_receipt_utc") or ""))
    if freshest_ts <= 0 or freshest_ts < current_ts:
        return current
    merged = dict(current)
    merged["last_receipt"] = freshest
    merged["last_receipt_utc"] = str(freshest.get("generated_utc") or freshest.get("utc") or "")
    health = freshest.get("health") if isinstance(freshest.get("health"), dict) else {}
    merged["last_error"] = "" if str(health.get("health") or "").strip().lower() == "healthy" else str(health.get("reason") or "")
    return merged


def _fresh_healer_status(status: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(status or {})
    if (
        not bool(current.get("running"))
        and not str(current.get("started_utc") or "").strip()
        and not str(current.get("last_receipt_utc") or "").strip()
    ):
        return current
    latest = _read_json_file(
        (REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs" / "pipeline_healer" / "latest_report.json").resolve()
    )
    if not latest:
        return current
    latest_ts = _parse_iso(str(latest.get("generated_utc") or ""))
    current_ts = _parse_iso(str(current.get("last_receipt_utc") or ""))
    if latest_ts <= 0 or latest_ts < current_ts:
        return current
    merged = dict(current)
    merged["last_receipt"] = latest
    merged["last_receipt_utc"] = str(latest.get("generated_utc") or "")
    merged["last_error"] = ""
    return merged


def _watchdog_run_active(status: Dict[str, Any]) -> bool:
    if not bool(status.get("running")):
        return False
    started_utc = str(status.get("started_utc") or "").strip()
    if not started_utc:
        return False
    started_ts = _parse_iso(started_utc)
    if started_ts <= 0:
        return True
    last_receipt_ts = _parse_iso(str(status.get("last_receipt_utc") or "").strip())
    if last_receipt_ts <= 0:
        receipt = status.get("last_receipt") if isinstance(status.get("last_receipt"), dict) else {}
        last_receipt_ts = _parse_iso(str(receipt.get("generated_utc") or receipt.get("utc") or "").strip())
    return started_ts > last_receipt_ts


def _watchdog_activity_hint(status: Dict[str, Any]) -> str:
    receipt = status.get("last_receipt") if isinstance(status.get("last_receipt"), dict) else {}
    actions = [dict(row) for row in list(receipt.get("actions") or []) if isinstance(row, dict)]
    for row in actions:
        action = str(row.get("action") or "").strip()
        if action == "execute_bounded_codex_escalation":
            if bool(row.get("ok")):
                return "codex launched"
            target = str(row.get("condition_id") or "").strip() or "codex escalation"
            return f"codex escalation {target}"
        if action == "resume_stalled_dependency_head":
            return f"resuming {str(row.get('job_id') or '').strip() or 'dependency head'}"
        if action == "scheduler_tick_for_progress_recovery":
            return f"tick {str(row.get('job_id') or '').strip() or 'scheduler'}"
    findings = [str((row or {}).get("kind") or "").strip() for row in list(receipt.get("findings") or []) if isinstance(row, dict)]
    if findings:
        first = findings[0]
        hint_map = {
            "dependency_blocked_chain": "audit blocked chain",
            "stalled_progress_chain_unrecovered": "audit stalled chain",
            "stale_lock": "audit stale lock",
            "orphan_running_state": "audit orphan run",
            "watchdog_repeat_repair_exhausted": "audit repeated repairs",
        }
        return hint_map.get(first, _truncate(first.replace("_", " "), 28))
    return ""


def _blocked_reason_hint(reason: str) -> str:
    value = str(reason or "").strip()
    if not value:
        return ""
    hints = {
        "repo_guard_commit_journal_invalid": "fix invalid commit-journal entries, then start or wait for healer/watchdog",
        "repo_guard_dirty_worktree": "clean or checkpoint the repo, or let the chain continue if dirty starts are allowed for this job type",
        "repo_guard_git_status_error": "check git status access in this repo",
        "repo_guard_commit_journal_scan_error": "inspect commit-journal scan errors under runtime/state/commit_journal",
        "watchdog_stale_claim_transition": "job claim went stale; wait for healer or restart the job",
        "startup_audit_stale_claim_cleared": "startup cleared a stale claim; restart the job",
    }
    if value in hints:
        return hints[value]
    if value.startswith("dependency_not_ok"):
        return "upstream dependency is not ok yet"
    if value.startswith("repo_guard_"):
        return "repo guard blocked launch; inspect repo/policy prerequisites"
    return ""


def _style(enabled: bool, text: str, code: str) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _label(enabled: bool, text: str, code: str = "1;36") -> str:
    palette = {
        "vienna_now": "1;36",
        "url": "1;34",
        "scheduler": "1;32",
        "watchdog": "1;31",
        "watchdog_busy": "1;31",
        "healer": "1;33",
        "scope": "1;35",
        "pis_uid4": "1;34",
        "intent_uid4": "1;34",
        "family_progress": "1;36",
        "filter_prefix": "1;35",
    }
    return _style(enabled, f"{text}:", palette.get(str(text), code))


def _status_color(status: str) -> str:
    s = str(status or "").lower()
    if s in {"ok", "completed_once"}:
        return "32"  # green
    if s in {"running", "active"}:
        return "36"  # cyan
    if s in {"queued"}:
        return "35"  # magenta
    if s in {"blocked", "ambiguous"}:
        return "33"  # yellow
    if s in {"error", "failed"}:
        return "31"  # red
    return "37"  # neutral


def _row_scheduler_projection_v1(row: Dict[str, Any]) -> Dict[str, Any]:
    existing = row.get("scheduler_projection_v1")
    if isinstance(existing, dict) and str(existing.get("projected_status") or "").strip():
        return dict(existing)
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    schedule = row.get("schedule") if isinstance(row.get("schedule"), dict) else {}
    lifecycle = row.get("lifecycle_projection") if isinstance(row.get("lifecycle_projection"), dict) else {}
    return _scheduler_projection_contract.project_scheduler_job_row(
        {
            "job_id": str(row.get("job_id") or ""),
            "enabled": bool(row.get("enabled", True)),
            "display_title": str(row.get("display_title") or row.get("job_id") or ""),
            "schedule": schedule,
            "state": state,
            "due": due,
            "lifecycle_projection": lifecycle,
        }
    )


def _projection_truth_for_watch(row: Dict[str, Any]) -> Dict[str, Any]:
    projection = _row_scheduler_projection_v1(row)
    projected_status = str(projection.get("projected_status") or "").strip().lower()
    projection_reason = str(projection.get("projection_reason") or "").strip()
    blocking_dep = ""
    if projection_reason.startswith("dependency_") and ":" in projection_reason:
        blocking_dep = projection_reason.split(":", 1)[1].strip()
    return {
        "status": projected_status or "unknown",
        "reason": projection_reason or "unknown",
        "completed_success": projected_status == "completed_once",
        "active": projected_status == "active",
        "terminal": projected_status in {"completed_once", "failed", "ambiguous"},
        "blocking_dep": blocking_dep,
        "degraded": projected_status == "ambiguous" or bool(projection.get("durable_conflict")),
        "projection": projection,
    }


def _durable_active_truth_override(row: Dict[str, Any]) -> Dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    lifecycle = row.get("lifecycle_projection") if isinstance(row.get("lifecycle_projection"), dict) else {}
    active_run_id = str(state.get("active_run_id") or "").strip()
    active_started_utc = str(state.get("active_started_utc") or "").strip()
    due_reason = str(due.get("reason") or "").strip().lower()
    last_status = str(state.get("last_status") or "").strip().lower()
    lifecycle_active = bool(lifecycle.get("active_run"))

    if not (active_run_id or due_reason == "active_run" or lifecycle_active):
        return {}

    reason = "active_run_present"
    degraded = False
    if not active_run_id:
        degraded = True
        if due_reason == "active_run":
            reason = "durable_due_active_run_without_active_run_id"
        elif lifecycle_active:
            reason = "lifecycle_active_without_active_run_id"
        elif active_started_utc or last_status == "running":
            reason = "durable_running_without_active_run_id"

    return {
        "status": "active",
        "reason": reason,
        "completed_success": False,
        "active": True,
        "terminal": False,
        "blocking_dep": "",
        "degraded": degraded,
    }


def _watch_truth_for_row(row: Dict[str, Any]) -> Dict[str, Any]:
    projection_truth = _projection_truth_for_watch(row)
    durable_active = _durable_active_truth_override(row)
    if durable_active:
        merged = dict(projection_truth)
        merged.update(durable_active)
        return merged
    return projection_truth


def _row_running_now(row: Dict[str, Any]) -> bool:
    return str(_watch_truth_for_row(row).get("status") or "") == "active"


def _project_row_truth(
    row: Dict[str, Any],
    rows_by_id: Dict[str, Dict[str, Any]],
    *,
    now_ts: float,
    memo: Optional[Dict[str, Dict[str, Any]]] = None,
    stack: Optional[set[str]] = None,
) -> Dict[str, Any]:
    memo = memo if isinstance(memo, dict) else {}
    job_id = str(row.get("job_id") or "").strip()
    if job_id and job_id in memo:
        return memo[job_id]

    result = _projection_truth_for_watch(row)
    durable_active = _durable_active_truth_override(row)
    if durable_active:
        result.update(durable_active)
    if not bool(row.get("enabled")) and not str(result.get("status") or "").strip():
        result = {
            "status": "disabled",
            "reason": "disabled",
            "completed_success": False,
            "active": False,
            "terminal": True,
            "blocking_dep": "",
        }
    if job_id:
        memo[job_id] = result
    return result


def _runtime_model_summary(row: Dict[str, Any]) -> str:
    runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
    models: List[str] = []
    executor = str(runtime.get("executor_agent_tech") or "codex").strip()
    advisory_tech = str(runtime.get("advisory_agent_tech") or runtime.get("agent_tech") or "").strip()
    primary = str(runtime.get("advisory_model") or runtime.get("model") or "").strip()
    fallback = str(runtime.get("fallback_model") or "").strip()
    if executor:
        models.append(f"executor={executor}")
    if advisory_tech:
        models.append(f"advisory_tech={advisory_tech}")
    if primary:
        models.append(f"advisory_model={primary}")
    if fallback and fallback != primary:
        models.append(f"fallback={fallback}")
    seq = runtime.get("tech_model_sequence") if isinstance(runtime.get("tech_model_sequence"), list) else []
    seq_entries = []
    for entry in seq:
        if not isinstance(entry, dict):
            continue
        entry_model = str(entry.get("model") or "").strip()
        if entry_model and entry_model not in seq_entries:
            seq_entries.append(entry_model)
    if seq_entries:
        models.append("history=" + ",".join(seq_entries))
    return ", ".join(models)


def _ledger_item_dir(uid: str) -> Optional[Path]:
    normalized = str(uid or "").strip().upper()
    if not normalized:
        return None
    if normalized in _LEDGER_DIR_CACHE:
        return _LEDGER_DIR_CACHE[normalized]
    root = REPO_ROOT / "governance" / "ledger"
    pattern = f"**/{normalized}__*/meta.json"
    matches = sorted(root.glob(pattern))
    resolved = matches[0].parent.resolve() if len(matches) == 1 else None
    _LEDGER_DIR_CACHE[normalized] = resolved
    return resolved


def _pis_title_and_summary(uid: str) -> Tuple[str, str]:
    title = ""
    summary = ""
    item_dir = _ledger_item_dir(uid)
    if item_dir is None:
        meta = _ledger_meta_for_uid(uid)
        return str(meta.get("title") or "").strip(), str(meta.get("pis_excerpt") or "").strip()[:180]
    p = item_dir / "pis.md"
    if not p.exists():
        meta = _ledger_meta_for_uid(uid)
        return str(meta.get("title") or "").strip(), str(meta.get("pis_excerpt") or "").strip()[:180]
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    in_title = False
    in_scope = False
    for line in lines:
        s = line.strip()
        if s.startswith("## 1. Title"):
            in_title = True
            in_scope = False
            continue
        if s.startswith("## 3. Scope") or s.startswith("## 2. Goal"):
            in_scope = True
            in_title = False
            continue
        if s.startswith("## "):
            in_title = False
            in_scope = False
        if in_title and s and not s.startswith("## "):
            title = s
            in_title = False
            continue
        if in_scope and s:
            cleaned = s.lstrip("-").strip()
            if cleaned and not cleaned.lower().startswith("in scope"):
                summary = cleaned
                break
    return title, summary


def _job_title_and_summary(row: Dict[str, Any]) -> Tuple[str, str]:
    prompt_full = str(row.get("prompt") or "")
    if prompt_full:
        target_line = ""
        layer_line = ""
        for line in prompt_full.splitlines():
            s = line.strip()
            if s.startswith("Target child PIS:"):
                target_line = s.replace("Target child PIS:", "", 1).strip()
            elif s.startswith("Layer:"):
                layer_line = s.replace("Layer:", "", 1).strip()
            if target_line and layer_line:
                break
        if target_line:
            return target_line[:160], f"layer={layer_line}" if layer_line else target_line[:180]

    display_title = str(row.get("display_title") or "").strip()
    if display_title:
        preview = str(row.get("prompt_preview") or "").strip()
        summary = preview.splitlines()[0].strip()[:180] if preview else ""
        return display_title[:160], summary

    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    btype = str(binding.get("type") or "").strip()
    bid = str(binding.get("id") or "").strip()
    if btype == "ledger_uid" and bid.startswith("PIS-"):
        t, s = _pis_title_and_summary(bid)
        if t or s:
            return t, s
    preview = str(row.get("prompt_preview") or row.get("prompt") or "").strip()
    if not preview:
        return "", ""
    one = preview.splitlines()[0].strip()
    return one[:120], one[:180]


def _ledger_meta_for_uid(uid: str) -> Dict[str, Any]:
    normalized = str(uid or "").strip().upper()
    if not normalized:
        return {}
    cached = _LEDGER_META_CACHE.get(normalized)
    if isinstance(cached, dict):
        return cached
    item_dir = _ledger_item_dir(normalized)
    if item_dir is None:
        _LEDGER_META_CACHE[normalized] = {}
        return {}
    meta_path = item_dir / "meta.json"
    if not meta_path.exists():
        _LEDGER_META_CACHE[normalized] = {}
        return {}
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        meta = {}
    meta = meta if isinstance(meta, dict) else {}
    _LEDGER_META_CACHE[normalized] = meta
    return meta


def _ledger_integration_for_uid(uid: str) -> Dict[str, Any]:
    meta = _ledger_meta_for_uid(uid)
    integration = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
    return integration if isinstance(integration, dict) else {}


def _ledger_slug_for_uid(uid: str) -> str:
    meta = _ledger_meta_for_uid(uid)
    return str(meta.get("slug") or "").strip()


def _pis_is_terminal(uid: str) -> bool:
    meta = _ledger_meta_for_uid(uid)
    status = str(meta.get("status") or "").strip().lower()
    return status in {"verified", "done", "completed", "deprecated", "superseded"}


def _job_family_slugs(row: Dict[str, Any]) -> Dict[str, str]:
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    bound_uid = str(binding.get("id") or "").strip().upper()
    if str(binding.get("type") or "").strip() != "ledger_uid" or not bound_uid.startswith("PIS-"):
        return {"parent_uid": "", "parent_slug": "", "intent_uid": "", "intent_slug": ""}
    meta = _ledger_meta_for_uid(bound_uid)
    integration = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
    parent_uid = str(meta.get("parent_uid") or integration.get("parent_uid") or "").strip().upper()
    if not parent_uid:
        relationship = str(integration.get("relationship") or "").strip().lower()
        if relationship == "execution_parent" or bound_uid.startswith("PIS-"):
            parent_uid = bound_uid
    intent_uid = str(
        integration.get("source_intent_sheet_uid")
        or integration.get("intent_sheet_uid")
        or meta.get("source_intent_sheet_uid")
        or ""
    ).strip().upper()
    return {
        "parent_uid": parent_uid,
        "parent_slug": _ledger_slug_for_uid(parent_uid) if parent_uid else "",
        "intent_uid": intent_uid,
        "intent_slug": _ledger_slug_for_uid(intent_uid) if intent_uid else "",
    }


def _active_family_scope_line(row: Optional[Dict[str, Any]], rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, str]:
    if not isinstance(row, dict):
        return {}
    family = _job_family_slugs(row)
    intent_uid = str(family.get("intent_uid") or "").strip().upper()
    parent_uid = str(family.get("parent_uid") or "").strip().upper()
    if not intent_uid or not parent_uid:
        return {}
    parent_meta = _ledger_meta_for_uid(parent_uid)
    integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    child_uids = [str(item or "").strip().upper() for item in list(integration.get("child_uids") or []) if str(item or "").strip()]
    implementation_child_uids = [
        str(item or "").strip().upper()
        for item in list(integration.get("implementation_child_uids") or [])
        if str(item or "").strip()
    ]
    if not implementation_child_uids:
        implementation_child_uids = [
            uid
            for uid in child_uids
            if str(_ledger_integration_for_uid(uid).get("relationship") or "").strip().lower() == "execution_child"
        ]
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    bound_uid = str(binding.get("id") or "").strip().upper()
    job_id = str(row.get("job_id") or "").strip()
    total = len(child_uids)
    implementation_total = len(implementation_child_uids) if implementation_child_uids else total
    parent_ref = parent_uid[-4:] if len(parent_uid) >= 4 else parent_uid
    intent_ref = intent_uid[-4:] if len(intent_uid) >= 4 else intent_uid
    include_parent = bool(bound_uid and bound_uid == parent_uid)
    parent_terminal = _pis_is_terminal(parent_uid)
    intent_terminal = _pis_is_terminal(intent_uid)
    bound_rows = list(rows or [])
    executed = 0
    if (implementation_child_uids or child_uids) and bound_rows:
        job_rows_by_uid: Dict[str, Dict[str, Any]] = {}
        for candidate in bound_rows:
            candidate_binding = candidate.get("binding") if isinstance(candidate.get("binding"), dict) else {}
            candidate_uid = str(candidate_binding.get("id") or "").strip().upper()
            if candidate_uid.startswith("PIS-"):
                job_rows_by_uid[candidate_uid] = candidate
        executed = sum(
            1
            for uid in (implementation_child_uids or child_uids)
            if str(((job_rows_by_uid.get(uid) or {}).get("state") or {}).get("last_status") or "").strip().lower() == "ok"
        )
    if total <= 0:
        return {
            "pis_uid4": parent_ref,
            "intent_uid4": intent_ref,
            "progress": f"PIS implemented: 0 of {1 if include_parent else 0}, verified: 0 of {1 if include_parent else 0} ({parent_ref} in progress)",
        }
    completed = sum(1 for uid in child_uids if _pis_is_terminal(uid))
    total_with_parent = total + (1 if include_parent else 0)
    implementation_total_with_parent = implementation_total + (1 if include_parent else 0)
    all_children_terminal = completed == total
    if parent_terminal and intent_terminal and all_children_terminal:
        rerun_ref = _extract_ref4(bound_uid) or _extract_ref4(job_id) or parent_ref
        return {
            "pis_uid4": parent_ref,
            "intent_uid4": intent_ref,
            "progress": f"verified family rerun: {rerun_ref} (family already implemented and verified)",
        }
    return {
        "pis_uid4": parent_ref,
        "intent_uid4": intent_ref,
        "progress": f"PIS implemented: {executed} of {implementation_total_with_parent}, verified: {completed} of {total_with_parent} ({parent_ref} in progress)",
    }


def _extract_pis_uid4(row: Dict[str, Any], title: str) -> str:
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    bid = str(binding.get("id") or "").strip()
    if bid.startswith("PIS-") and len(bid) >= 4:
        return bid[-4:]
    m = PIS_UID_RE.search(str(title or ""))
    if m:
        return str(m.group(2))
    return ""


def _extract_job_tail8(job_id: str) -> str:
    jid = str(job_id or "").strip()
    if not jid:
        return ""
    last_token = jid.split("_")[-1]
    if last_token:
        if len(last_token) <= 8:
            return last_token
        return last_token[-8:]
    if len(jid) <= 8:
        return jid
    return jid[-8:]


def _extract_ref4(text: str) -> str:
    value = str(text or "").strip()
    if not value:
        return ""
    m = PIS_UID_RE.search(value)
    if m:
        return str(m.group(2) or "").upper()
    m = JOB_UID4_RE.search(value)
    if m:
        return str(m.group(1) or "").upper()
    tail = _extract_job_tail8(value)
    return tail.upper()


def _report_hint(row: Dict[str, Any]) -> str:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    report_path = str(state.get("last_report_path") or "").strip()
    if not report_path:
        return ""
    p = Path(report_path)
    if not p.exists():
        return ""
    txt = p.read_text(encoding="utf-8", errors="replace")
    for line in txt.splitlines():
        s = line.strip()
        if s.startswith("VERDICT:"):
            return s
    for line in txt.splitlines()[:20]:
        s = line.strip()
        if s.startswith("- status:"):
            return s.replace("- ", "", 1)
    return ""


def _job_family_identifier(row: Dict[str, Any]) -> str:
    jid = str(row.get("job_id") or "").strip()
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    bound_uid = str(binding.get("id") or "").strip().upper()
    title, _summary = _job_title_and_summary(row)
    lowered_title = str(title or "").strip().lower()
    preview = str(row.get("prompt_preview") or row.get("prompt") or "").strip().lower()
    family = _job_family_slugs(row)
    parent_uid = str(family.get("parent_uid") or "").strip().upper()
    intent_uid = str(family.get("intent_uid") or "").strip().upper()

    def _intent_ref() -> str:
        if not intent_uid:
            candidate_meta = _ledger_meta_for_uid(parent_uid or bound_uid)
            tags = [str(item or "").strip().upper() for item in list(candidate_meta.get("tags") or []) if str(item or "").strip()]
            for tag in tags:
                if re.fullmatch(r"IS-\d{2}", tag):
                    return tag
                if re.fullmatch(r"IS-[A-Z]\d{2}", tag):
                    return tag
            return ""
        meta = _ledger_meta_for_uid(intent_uid)
        integration = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
        sheet_id = str(integration.get("sheet_id") or "").strip().upper()
        if sheet_id:
            return sheet_id
        slug = str(meta.get("slug") or "").strip()
        if slug:
            return slug
        return intent_uid[-4:] if len(intent_uid) >= 4 else intent_uid

    def _tag(body: str) -> str:
        ref = _intent_ref()
        if ref:
            return f"[{body} | {ref}]"
        return f"[{body}]"

    if jid.startswith("close_") and "execution_family" in jid:
        return _tag("Family Closeout")
    if jid.startswith("implement_") and "decompose parent ledger item" in preview:
        return _tag("Parent")
    if bound_uid.startswith("PIS-") and parent_uid:
        parent_meta = _ledger_meta_for_uid(parent_uid)
        parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
        child_uids = [str(item or "").strip().upper() for item in list(parent_integration.get("child_uids") or []) if str(item or "").strip()]
        implementation_child_uids = [
            str(item or "").strip().upper()
            for item in list(parent_integration.get("implementation_child_uids") or [])
            if str(item or "").strip()
        ]
        child_meta = _ledger_meta_for_uid(bound_uid)
        child_integration = child_meta.get("integration_meta") if isinstance(child_meta.get("integration_meta"), dict) else {}
        relationship = str(child_integration.get("relationship") or "").strip().lower()
        if not implementation_child_uids:
            implementation_child_uids = [
                uid
                for uid in child_uids
                if str(_ledger_integration_for_uid(uid).get("relationship") or "").strip().lower() == "execution_child"
            ]
        if bound_uid in implementation_child_uids:
            idx = implementation_child_uids.index(bound_uid) + 1
            return _tag(f"PIS {idx} of {len(implementation_child_uids)}")
        if bound_uid in child_uids:
            if relationship == "intent_validation_suite" or "prove" in lowered_title or "end to end" in lowered_title or "e2e" in lowered_title or "validate" in lowered_title:
                return _tag("E2E 1 of 1")
            if relationship == "intent_family_closeout" or "close" in lowered_title:
                return _tag("Closeout 1 of 1")
        if jid.startswith("implement_"):
            return _tag("Parent")
    return ""


def _artifact_paths(row: Dict[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {"pis": "", "spec": "", "receipt": "", "report": ""}
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    bid = str(binding.get("id") or "").strip()
    btype = str(binding.get("type") or "").strip()
    if btype == "ledger_uid" and bid.startswith("PIS-"):
        item_dir = _ledger_item_dir(bid)
        if item_dir is not None:
            out["pis"] = str(item_dir / "pis.md")
    jid = str(row.get("job_id") or "")
    spec_path = (REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox" / f"{jid}.json").resolve()
    if spec_path.exists():
        out["spec"] = str(spec_path)
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    rc = str(state.get("last_receipt_path") or "").strip()
    rp = str(state.get("last_report_path") or "").strip()
    if rc:
        out["receipt"] = rc
    if rp:
        out["report"] = rp
    return out


def _ledger_item_dir_from_binding(binding: Dict[str, Any]) -> Optional[Path]:
    if str(binding.get("type") or "").strip() != "ledger_uid":
        return None
    uid = str(binding.get("id") or "").strip()
    if not uid:
        return None
    return _ledger_item_dir(uid)


def _load_jsonl(path: Path) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            value = json.loads(line)
        except Exception:
            continue
        if isinstance(value, dict):
            rows.append(value)
    return rows


def _fmt_tokens(value: Any) -> str:
    try:
        return f"{int(value or 0):,}"
    except Exception:
        return "0"


def _estimate_tokens_for_stage(stage: Dict[str, Any]) -> Dict[str, int]:
    part = stage if isinstance(stage, dict) else {}
    advisory = part.get("advisory") if isinstance(part.get("advisory"), dict) else {}
    inbound = 0
    outbound = 0
    meta = part.get("meta") if isinstance(part.get("meta"), dict) else {}
    inbound += _estimate_tokens_from_bytes(meta.get("request_payload_bytes"))
    outbound += _estimate_tokens_from_text(part.get("content"))
    advisory_meta = advisory.get("meta") if isinstance(advisory.get("meta"), dict) else {}
    inbound += _estimate_tokens_from_bytes(advisory_meta.get("request_payload_bytes"))
    outbound += _estimate_tokens_from_text(advisory.get("content"))
    return {
        "inbound_tokens_est": inbound,
        "outbound_tokens_est": outbound,
        "total_tokens_est": inbound + outbound,
    }


def _estimate_tokens_for_receipt(receipt: Dict[str, Any]) -> Dict[str, Any]:
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = loop.get("iterations") if isinstance(loop.get("iterations"), list) else []
    per_iteration: List[Dict[str, Any]] = []
    inbound_total = 0
    outbound_total = 0
    for item in iterations:
        if not isinstance(item, dict):
            continue
        iteration = int(item.get("iteration") or 0)
        builder = _estimate_tokens_for_stage(item.get("builder") if isinstance(item.get("builder"), dict) else {})
        verifier = _estimate_tokens_for_stage(item.get("verifier") if isinstance(item.get("verifier"), dict) else {})
        inbound = int(builder["inbound_tokens_est"]) + int(verifier["inbound_tokens_est"])
        outbound = int(builder["outbound_tokens_est"]) + int(verifier["outbound_tokens_est"])
        total = inbound + outbound
        inbound_total += inbound
        outbound_total += outbound
        per_iteration.append(
            {
                "iteration": iteration,
                "inbound_tokens_est": inbound,
                "outbound_tokens_est": outbound,
                "total_tokens_est": total,
            }
        )
    return {
        "inbound_tokens_est": inbound_total,
        "outbound_tokens_est": outbound_total,
        "total_tokens_est": inbound_total + outbound_total,
        "per_iteration": per_iteration,
        "source": "receipt_loop_stage_bytes",
    }


def _estimate_tokens_for_active_row(row: Dict[str, Any]) -> Dict[str, Any]:
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    item_dir = _ledger_item_dir_from_binding(binding)
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    started = _parse_utc_dt(state.get("active_started_utc"))
    if item_dir is None or started is None:
        return {"inbound_tokens_est": 0, "outbound_tokens_est": 0, "total_tokens_est": 0, "source": "none"}
    window_start = started - timedelta(minutes=2)
    window_end = datetime.now(timezone.utc) + timedelta(seconds=5)
    inbound_prompt = 0
    inbound_messages = 0
    outbound = 0
    transcript_rows = 0
    for path in sorted((item_dir / "artifacts" / "agent_chat").glob("session__*.jsonl")):
        for row_obj in _load_jsonl(path):
            event_utc = _parse_utc_dt(row_obj.get("utc"))
            if event_utc is None or not (window_start <= event_utc <= window_end):
                continue
            transcript_rows += 1
            sender = str(row_obj.get("sender") or "").strip().lower()
            content = str(row_obj.get("content") or "")
            meta = row_obj.get("meta") if isinstance(row_obj.get("meta"), dict) else {}
            if sender == "assistant":
                outbound += _estimate_tokens_from_text(content)
                inbound_prompt += _estimate_tokens_from_bytes(meta.get("request_payload_bytes"))
            elif sender == "user":
                inbound_messages += _estimate_tokens_from_text(content)
    inbound = inbound_prompt or inbound_messages
    return {
        "inbound_tokens_est": inbound,
        "outbound_tokens_est": outbound,
        "total_tokens_est": inbound + outbound,
        "transcript_rows": transcript_rows,
        "source": "active_transcript_window",
    }


def _token_summary_for_row(row: Dict[str, Any], *, mode: str) -> Dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    if mode == "active" and str(state.get("active_run_id") or "").strip():
        return _estimate_tokens_for_active_row(row)
    receipt_path = str(state.get("last_receipt_path") or "").strip()
    if receipt_path:
        try:
            receipt = json.loads(Path(receipt_path).read_text(encoding="utf-8"))
            return _estimate_tokens_for_receipt(receipt)
        except Exception:
            pass
    return {"inbound_tokens_est": 0, "outbound_tokens_est": 0, "total_tokens_est": 0, "source": "none"}


def _fmt_job_block(
    label: str,
    row: Optional[Dict[str, Any]],
    *,
    tz_name: str,
    color: bool,
    active_marker: str = "",
    mode: str = "job",
) -> List[str]:
    out = [f"{label}"]
    if not row:
        out.append("  job: (none)")
        return out
    jid = str(row.get("job_id") or "")
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    truth = _projection_truth_for_watch(row)
    projection = truth.get("projection") if isinstance(truth.get("projection"), dict) else {}
    active_run_id = str(state.get("active_run_id") or "").strip()
    last_run_id = str(state.get("last_run_id") or "").strip()
    status = str(truth.get("status") or "")
    title, summary = _job_title_and_summary(row)
    status_txt = status or "unknown"
    status_colored = _style(color, status_txt, _status_color(status_txt))
    out.append(f"  job: {jid}")
    out.append(f"  status: {status_colored}")
    out.append(f"  run: active={active_run_id} last={last_run_id}")
    if mode == "previous":
        finished_utc = str(state.get("last_finished_utc") or "").strip()
        finished_human = _fmt_time(finished_utc, tz_name=tz_name)
        if finished_human:
            out.append(f"  finished: {finished_human}")
        elif finished_utc:
            out.append(f"  finished: {finished_utc}")
    else:
        due_human = _fmt_time(str(due.get("due_utc") or ""), tz_name=tz_name)
        due_raw = str(due.get("due_utc") or "")
        due_line = f"  due: {str(truth.get('reason') or due.get('reason') or '')}"
        if due_human:
            due_line += f"  @ {due_human}"
        elif due_raw:
            due_line += f"  @ {due_raw}"
        out.append(due_line)
        blocked_reason = str(truth.get("reason") or "").strip()
        if str(status_txt).lower() == "blocked" and blocked_reason:
            out.append(f"  blocked_reason: {blocked_reason}")
            hint = _blocked_reason_hint(blocked_reason)
            if hint:
                out.append(f"  action_hint: {hint}")
        if bool(truth.get("degraded")):
            out.append("  truth_state: degraded")
            out.append(f"  projection_reason: {str(projection.get('projection_reason') or blocked_reason or '')}")
    if active_marker:
        out.append(f"  active: {active_marker}")
    loop_status = _loop_iteration_status(row, mode=mode)
    if loop_status:
        out.append(f"  {loop_status}")
    token_summary = _token_summary_for_row(row, mode=mode)
    if int(token_summary.get("total_tokens_est") or 0) > 0:
        label_txt = "tokens_so_far_est" if mode == "active" and active_run_id else "tokens_est"
        out.append(
            "  "
            f"{label_txt}: total={_fmt_tokens(token_summary.get('total_tokens_est'))} "
            f"in={_fmt_tokens(token_summary.get('inbound_tokens_est'))} "
            f"out={_fmt_tokens(token_summary.get('outbound_tokens_est'))} "
            f"source={str(token_summary.get('source') or '')}"
        )
        per_iteration = token_summary.get("per_iteration") if isinstance(token_summary.get("per_iteration"), list) else []
        if per_iteration:
            parts = []
            for item in per_iteration[:6]:
                if not isinstance(item, dict):
                    continue
                parts.append(
                    f"i{int(item.get('iteration') or 0)}={_fmt_tokens(item.get('total_tokens_est'))}"
                )
            if parts:
                out.append("  tokens_by_iteration_est: " + " ".join(parts))
    if mode == "active" and active_run_id:
        timing = _active_timing(row)
        if timing.get("active"):
            started_human = _fmt_time(str(timing.get("started_utc") or ""), tz_name=tz_name)
            if started_human:
                out.append(f"  started: {started_human}")
            out.append(
                "  timeout: "
                f"elapsed={_fmt_duration(float(timing.get('elapsed_s') or 0))} "
                f"remaining={_fmt_duration(float(timing.get('remaining_s') or 0))} "
                f"limit={_fmt_duration(float(timing.get('timeout_s') or 0))}"
            )
            out.append(f"  progress: {_progress_bar(float(timing.get('progress_ratio') or 0.0))}")
    if title:
        out.append(f"  title: {title}")
    if summary:
        out.append(f"  summary: {summary}")
    family_slugs = _job_family_slugs(row)
    if family_slugs.get("parent_slug"):
        out.append(f"  parent_pis_slug: {family_slugs['parent_slug']}")
    if family_slugs.get("intent_slug"):
        out.append(f"  intent_sheet_slug: {family_slugs['intent_slug']}")
    if mode == "previous":
        hint = _report_hint(row)
        if hint:
            out.append(f"  result_hint: {hint}")
    paths = _artifact_paths(row)
    if paths.get("pis"):
        out.append(f"  pis_path: {paths['pis']}")
#   if paths.get("spec"):
#       out.append(f"  spec_path: {paths['spec']}")
#   if paths.get("receipt"):
#       out.append(f"  receipt_path: {paths['receipt']}")
    if mode == "previous" and paths.get("report"):
        out.append(f"  report_path: {paths['report']}")
    model_line = _runtime_model_summary(row)
    if model_line:
        out.append(f"  model: {model_line}")
    return out


def _compact_due_reason(reason: str) -> str:
    value = str(reason or "").strip()
    if not value:
        return ""
    if value == "active_run":
        return ""
    if value.startswith("dependency_not_ok:") or value.startswith("dependency_pending:"):
        dep = value.split(":", 1)[1].strip()
        return f"wait={_extract_ref4(dep)}" if dep else "wait"
    return value


def _compact_loop_status(row: Dict[str, Any]) -> str:
    jid = str(row.get("job_id") or "").strip()
    if EXPLICIT_PASS_JOB_RE.search(jid):
        return ""
    if str(row.get("job_kind") or "").strip() != "build_verify_loop":
        return ""
    loop = row.get("loop") if isinstance(row.get("loop"), dict) else {}
    max_iterations = int(loop.get("max_iterations") or 0)
    if max_iterations <= 0:
        return ""
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    active_run_id = str(state.get("active_run_id") or "").strip()
    if active_run_id:
        active_iteration = int(state.get("active_loop_iteration") or 0)
        active_stage = str(state.get("active_loop_stage") or "").strip()
        if active_iteration > 0:
            return f"iter={active_iteration}/{max_iterations}{('/' + active_stage) if active_stage else ''}"
        return f"iter=?/{max_iterations}"
    if max_iterations > 1:
        return f"iter<={max_iterations}"
    return ""


def _explicit_pass_suffix(job_id: str) -> Tuple[int, int]:
    match = EXPLICIT_PASS_JOB_RE.search(str(job_id or "").strip())
    if not match:
        return 0, 0
    try:
        return int(match.group(1)), int(match.group(2))
    except Exception:
        return 0, 0


def _state_snapshot_for_job_id(job_id: str) -> Dict[str, Any]:
    jid = str(job_id or "").strip()
    if not jid:
        return {}
    path = (REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state" / f"{jid}.json").resolve()
    return _read_json_file(path)


def _merge_state_snapshot(durable: Dict[str, Any], live: Dict[str, Any]) -> Dict[str, Any]:
    merged = dict(durable or {})
    for key, value in dict(live or {}).items():
        if value in ("", [], {}, None):
            continue
        merged[key] = value
    return merged


def _child_display_title(uid: str) -> str:
    meta = _ledger_meta_for_uid(uid)
    return str(meta.get("title") or uid).strip()


def _family_execution_mode(parent_uid: str) -> str:
    integration = _ledger_integration_for_uid(parent_uid)
    return str(integration.get("execution_mode") or "").strip().lower()


def _family_chain_parent_uid(scope_row: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]) -> str:
    active_parent = _family_parent_uid_for_row(scope_row)
    if active_parent:
        return active_parent
    for row in rows:
        parent_uid = _family_parent_uid_for_row(row)
        if parent_uid:
            return parent_uid
    return ""


def _synthetic_chain_row(
    *,
    job_id: str,
    title: str,
    binding_uid: str,
    relationship: str,
    state: Dict[str, Any],
    enabled: bool = False,
    depends_on: Optional[List[str]] = None,
) -> Dict[str, Any]:
    display_title = str(title or job_id).strip()
    pass_index, pass_total = _explicit_pass_suffix(job_id)
    if pass_total > 0 and "pass " not in display_title.lower():
        display_title = f"pass {pass_index}/{pass_total} {display_title}".strip()
    row_state = dict(state or {})
    row_state.setdefault("job_id", job_id)
    row_state.setdefault("last_status", str(row_state.get("last_status") or "").strip())
    return {
        "job_id": job_id,
        "job_kind": "build_verify_loop",
        "enabled": bool(enabled),
        "display_title": display_title,
        "binding": {"type": "ledger_uid", "id": binding_uid},
        "state": row_state,
        "due": {},
        "depends_on": list(depends_on or []),
        "lifecycle_projection": {"active_run": bool(str(row_state.get("active_run_id") or "").strip())},
        "scheduler_projection_v1": {},
        "prompt_meta": {"relationship": relationship, "synthetic_chain_row": True},
        "ui_label": display_title,
    }


def _family_chain_items(scope_row: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parent_uid = _family_chain_parent_uid(scope_row, rows)
    if not parent_uid:
        return []
    if _family_execution_mode(parent_uid) != "explicit_pass_chain_v1":
        return []
    integration = _ledger_integration_for_uid(parent_uid)
    order = [
        str(uid or "").strip().upper()
        for uid in list(integration.get("recommended_execution_order") or integration.get("child_uids") or [])
        if str(uid or "").strip()
    ]
    if not order:
        return []
    rows_by_uid: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        bound_uid = _bound_ledger_uid_for_row(row)
        if bound_uid:
            rows_by_uid.setdefault(bound_uid, []).append(row)
    chain: List[Dict[str, Any]] = []
    for uid in order:
        child_integration = _ledger_integration_for_uid(uid)
        relationship = str(child_integration.get("relationship") or "").strip().lower()
        title = _child_display_title(uid)
        current_rows = rows_by_uid.get(uid, [])
        if relationship == "execution_child":
            candidate_ids = {
                str(row.get("job_id") or "").strip()
                for row in current_rows
                if EXPLICIT_PASS_JOB_RE.search(str(row.get("job_id") or "").strip())
            }
            for path in (REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state").glob(f"*{uid[-4:].lower()}*p*of*.json"):
                candidate_ids.add(path.stem)
            pass_ids: List[Tuple[int, int, str]] = []
            for jid in candidate_ids:
                pass_index, pass_total = _explicit_pass_suffix(jid)
                if pass_total > 0:
                    pass_ids.append((pass_index, pass_total, jid))
            if not pass_ids:
                continue
            pass_ids.sort(key=lambda item: (item[0], item[1], item[2]))
            for pass_index, pass_total, jid in pass_ids:
                live_row = next((row for row in current_rows if str(row.get("job_id") or "").strip() == jid), None)
                if live_row is not None:
                    state = _state_snapshot_for_job_id(jid)
                    merged_row = dict(live_row)
                    live_state = live_row.get("state") if isinstance(live_row.get("state"), dict) else {}
                    merged_row["state"] = _merge_state_snapshot(state, live_state)
                    chain.append(merged_row)
                    continue
                state = _state_snapshot_for_job_id(jid)
                chain.append(
                    _synthetic_chain_row(
                        job_id=jid,
                        title=title,
                        binding_uid=uid,
                        relationship=relationship,
                        state=state,
                    )
                )
            continue
        live_row = current_rows[0] if current_rows else None
        if live_row is not None:
            state = _state_snapshot_for_job_id(str(live_row.get("job_id") or "").strip())
            merged_row = dict(live_row)
            live_state = live_row.get("state") if isinstance(live_row.get("state"), dict) else {}
            merged_row["state"] = _merge_state_snapshot(state, live_state)
            chain.append(merged_row)
            continue
        synthetic_id = ""
        for path in (REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state").glob(f"*{uid[-4:].lower()}*.json"):
            if EXPLICIT_PASS_JOB_RE.search(path.stem):
                continue
            synthetic_id = path.stem
            break
        if synthetic_id:
            chain.append(
                _synthetic_chain_row(
                    job_id=synthetic_id,
                    title=title,
                    binding_uid=uid,
                    relationship=relationship,
                    state=_state_snapshot_for_job_id(synthetic_id),
                )
            )
    return chain


def _v2_primary_chain_rows(payload: Dict[str, Any], scope_row: Optional[Dict[str, Any]], rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not _scheduler_v2_is_primary(payload):
        return []
    projection = _scheduler_v2_projection(payload)
    parent_uid = str(projection.get("family_parent_uid") or "").strip().upper()
    if not parent_uid:
        parent_uid = _family_chain_parent_uid(scope_row, rows)
    if not parent_uid:
        return []
    integration = _ledger_integration_for_uid(parent_uid)
    order = [
        str(uid or "").strip().upper()
        for uid in list(integration.get("recommended_execution_order") or integration.get("child_uids") or [])
        if str(uid or "").strip()
    ]
    children = [dict(row) for row in list(projection.get("children") or []) if isinstance(row, dict)]
    child_by_uid = {str(row.get("child_uid") or "").strip().upper(): row for row in children if str(row.get("child_uid") or "").strip()}
    rows_by_uid: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        bound_uid = _bound_ledger_uid_for_row(row)
        if bound_uid:
            rows_by_uid.setdefault(bound_uid, []).append(row)
    out: List[Dict[str, Any]] = []
    for uid in order:
        child = child_by_uid.get(uid, {})
        child_status = str(child.get("child_status") or "").strip().lower()
        if not child_status:
            continue
        current_rows = rows_by_uid.get(uid, [])
        chosen = None
        active_job_id = str(child.get("active_job_id") or child.get("job_id") or "").strip()
        if active_job_id:
            chosen = next((row for row in current_rows if str(row.get("job_id") or "").strip() == active_job_id), None)
        if chosen is None and current_rows:
            chosen = current_rows[0]
        title = str(child.get("title") or _child_display_title(uid) or uid).strip()
        relationship = str(child.get("relationship") or _ledger_integration_for_uid(uid).get("relationship") or "").strip().lower()
        if chosen is not None:
            row = dict(chosen)
            state = _state_snapshot_for_job_id(str(row.get("job_id") or "").strip())
            live_state = row.get("state") if isinstance(row.get("state"), dict) else {}
            row["state"] = _merge_state_snapshot(state, live_state)
        else:
            synthetic_id = active_job_id or f"{uid[-4:].lower()}_{relationship or 'child'}"
            row = _synthetic_chain_row(
                job_id=synthetic_id,
                title=title,
                binding_uid=uid,
                relationship=relationship,
                state=_state_snapshot_for_job_id(active_job_id) if active_job_id else {},
            )
        forced_status = ""
        if child_status in {"retired", "succeeded", "completed", "validated", "closed"}:
            forced_status = "completed_once"
        elif child_status == "active":
            forced_status = "active"
        elif child_status == "ready":
            forced_status = "queued"
        elif child_status == "waiting":
            forced_status = "blocked"
        elif child_status == "failed":
            forced_status = "failed"
        row["_forced_chain_truth"] = {
            "status": forced_status,
            "reason": str(child.get("status_reason") or ""),
            "completed_success": child_status == "retired",
            "active": child_status == "active",
            "terminal": child_status in {"retired", "failed"},
        }
        out.append(row)
    return out


def _compact_job_line(
    row: Dict[str, Any],
    *,
    index: int,
    tz_name: str,
    truth: Optional[Dict[str, Any]] = None,
) -> str:
    jid = str(row.get("job_id") or "")
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    title, _summary = _job_title_and_summary(row)
    title_txt = _truncate(title or jid or "(no title)", 68)
    effective_truth = dict(truth or _projection_truth_for_watch(row))
    projection = effective_truth.get("projection") if isinstance(effective_truth.get("projection"), dict) else {}
    status = str(effective_truth.get("status") or "unknown")
    uid4 = _extract_pis_uid4(row, title_txt)
    family_id = _job_family_identifier(row)
    parts: List[str] = [f"{index:02d}.", f"[{status}]"]
    if family_id:
        parts.append(family_id)
    parts.append(title_txt)
    if uid4:
        parts.append(f"({uid4})")
    reason = str(effective_truth.get("reason") or due.get("reason") or "")
    if reason:
        part = _compact_due_reason(reason)
        due_human = _fmt_time(str(due.get("due_utc") or ""), tz_name=tz_name)
        if due_human and part and not part.startswith("wait"):
            part += f"@{_short_clock(str(due.get('due_utc') or ''), tz_name=tz_name)}"
        if part:
            parts.append(part)
    deps = row.get("depends_on") if isinstance(row.get("depends_on"), list) else []
    if deps:
        parts.append(f"deps={len(deps)}")
    loop_status = _compact_loop_status(row)
    if loop_status:
        parts.append(loop_status)
    token_mode = "active" if str(state.get("active_run_id") or "").strip() else "previous"
    token_summary = _token_summary_for_row(row, mode=token_mode)
    total_tokens_est = int(token_summary.get("total_tokens_est") or 0)
    if total_tokens_est > 0:
        parts.append(f"tok={_fmt_tokens(total_tokens_est)}")
    if str(state.get("active_run_id") or "").strip():
        timing = _active_timing(row)
        if timing.get("active"):
            parts.append(f"left={_fmt_duration(float(timing.get('remaining_s') or 0))}")
    if str(status).lower() == "blocked" and reason and not any(part.startswith("wait=") for part in parts):
        wait_ref = ""
        if reason.startswith("dependency_not_ok:") or reason.startswith("dependency_pending:"):
            wait_ref = _extract_ref4(reason.split(":", 1)[1])
        elif reason.startswith("continuation_bootstrap_blocked:"):
            candidate = reason.split(":", 1)[1]
            extracted = _extract_ref4(candidate)
            if extracted and extracted not in {"NONE", "UNKNOWN", "REACHED"}:
                wait_ref = extracted
        if wait_ref:
            parts.append(f"wait={wait_ref}")
    if bool(effective_truth.get("degraded")) or bool(projection.get("durable_conflict")):
        parts.append("degraded")
    return " ".join([part for part in parts if part])


def _pipeline_job_lines(
    row: Dict[str, Any],
    *,
    index: int,
    tz_name: str,
    color: bool,
    truth: Dict[str, Any],
) -> List[str]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    title, _summary = _job_title_and_summary(row)
    title_txt = title or str(row.get("job_id") or "(no title)")
    raw_status = str(state.get("last_status") or "").strip().lower()
    status_txt = str(truth.get("status") or raw_status or "unknown")
    enabled_txt = "enabled" if bool(row.get("enabled")) else "disabled"
    family_id = _job_family_identifier(row)
    title_line = f"  {index:02d}. [{status_txt}] [{enabled_txt}]"
    if family_id:
        title_line += f" {family_id}"
    title_line += f" {_truncate(title_txt, 88)}"
    lines = [title_line]
    if raw_status and raw_status != status_txt:
        lines.append(f"      last_terminal={raw_status}")
    loop_status = _loop_iteration_status(row, mode="active" if str(state.get("active_run_id") or "").strip() else "job")
    if loop_status:
        lines.append(f"      {loop_status}")
    return lines


def _pick_previous(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    best: Optional[Dict[str, Any]] = None
    best_ts = 0.0
    for r in rows:
        if _row_running_now(r):
            continue
        state = r.get("state") if isinstance(r.get("state"), dict) else {}
        ts = _parse_iso(str(state.get("last_finished_utc") or ""))
        if ts > best_ts:
            best_ts = ts
            best = r
    return best


def _pick_active(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    act = [r for r in rows if _row_running_now(r)]
    if not act:
        return None
    primary = [r for r in act if not _is_auxiliary_reconcile_sidecar(r)]
    if primary:
        act = primary
    act.sort(key=lambda r: _parse_iso(str((r.get("state") or {}).get("active_started_utc") or "")))
    return act[0]


def _pick_next(rows: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    next_projection = _scheduler_projection_contract.select_next_scheduler_job(rows)
    if not isinstance(next_projection, dict):
        return None
    next_job_id = str(next_projection.get("job_id") or "").strip()
    fallback: Optional[Dict[str, Any]] = None
    for row in rows:
        if str(row.get("job_id") or "").strip() == next_job_id:
            if _is_auxiliary_reconcile_sidecar(row):
                fallback = row
                continue
            return row
    return fallback


def _is_status_reconcile_job(row: Dict[str, Any]) -> bool:
    job_id = str(row.get("job_id") or "").strip().lower()
    return job_id.startswith("verify_") and job_id.endswith("_status_reconcile")


def _is_auxiliary_reconcile_sidecar(row: Dict[str, Any]) -> bool:
    prompt_meta = row.get("prompt_meta") if isinstance(row.get("prompt_meta"), dict) else {}
    if bool(prompt_meta.get("generated_by_closeout_truth_reconcile")):
        return True
    return _is_status_reconcile_job(row)


def _should_show_auxiliary_reconcile_sidecar(row: Dict[str, Any]) -> bool:
    if not _is_auxiliary_reconcile_sidecar(row):
        return False
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    if str(state.get("active_run_id") or "").strip():
        return True
    if str(state.get("run_now_requested_utc") or "").strip():
        return True
    if bool(due.get("eligible")):
        return True
    return str(due.get("reason") or "").strip().lower() not in {"", "disabled"}


def _priority_key(row: Dict[str, Any]) -> Tuple[int, float, str]:
    truth = _watch_truth_for_row(row)
    projection = truth.get("projection") if isinstance(truth.get("projection"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    active = str(truth.get("status") or "") == "active"
    eligible = str(projection.get("next_candidate_bucket") or "") == "runnable_now"
    helper_penalty = 1 if _is_status_reconcile_job(row) and not active else 0
    due_ts = _parse_iso(str(projection.get("next_due_utc") or due.get("due_utc") or ""))
    if due_ts <= 0:
        due_ts = 9e18
    return (
        0 if active else 1,
        helper_penalty,
        0 if eligible else 1,
        due_ts,
        str(row.get("job_id") or ""),
    )


def _is_next_in_line_candidate(row: Dict[str, Any]) -> bool:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    if (
        not bool(row.get("enabled"))
        and not str(state.get("active_run_id") or "").strip()
        and not str(state.get("run_now_requested_utc") or "").strip()
        and not _family_parent_uid_for_row(row)
        and not list(row.get("depends_on") or [])
    ):
        return False
    rows_by_id = {str(row.get("job_id") or ""): row}
    truth = _project_row_truth(row, rows_by_id, now_ts=dt.datetime.now(dt.timezone.utc).timestamp(), memo={})
    if str(truth.get("status") or "") == "disabled":
        return False
    if str(truth.get("status") or "") == "active":
        return False
    if bool(truth.get("completed_success")):
        return False
    if bool(truth.get("terminal")):
        return False
    if _is_status_reconcile_job(row):
        bound_uid = _bound_ledger_uid_for_row(row)
        if bound_uid and _pis_is_terminal(bound_uid):
            return False
    return True


def _is_completed_status(status: str) -> bool:
    return str(status or "").strip().lower() in {"ok", "complete", "completed", "passed", "success", "succeeded"}


def _chain_truth_for_row(row: Dict[str, Any], *, rows_by_id: Dict[str, Dict[str, Any]], now_ts: float, memo: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    forced = row.get("_forced_chain_truth") if isinstance(row.get("_forced_chain_truth"), dict) else {}
    if forced:
        return {**_project_row_truth(row, rows_by_id, now_ts=now_ts, memo=memo), **forced}
    truth = _project_row_truth(row, rows_by_id, now_ts=now_ts, memo=memo)
    if str(truth.get("status") or "").strip() not in {"unknown", ""}:
        return truth
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    last_status = str(state.get("last_status") or "").strip().lower()
    if str(state.get("active_run_id") or "").strip():
        return {**truth, "status": "active", "reason": "active_run_present", "active": True, "terminal": False}
    if _is_completed_status(last_status):
        return {**truth, "status": "completed_once", "reason": "already_executed", "completed_success": True, "terminal": True}
    if last_status in {"running"}:
        return {**truth, "status": "active", "reason": "durable_running_without_active_run_id", "active": True, "terminal": False, "degraded": True}
    return truth


def _family_parent_uid_for_row(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return ""
    family = _job_family_slugs(row)
    return str(family.get("parent_uid") or "").strip().upper()


def _bound_ledger_uid_for_row(row: Optional[Dict[str, Any]]) -> str:
    if not isinstance(row, dict):
        return ""
    binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
    if str(binding.get("type") or "").strip() != "ledger_uid":
        return ""
    return str(binding.get("id") or "").strip().upper()


def _pipeline_rows(rows: List[Dict[str, Any]], *, include_done: bool = False, limit: int = 40) -> List[Dict[str, Any]]:
    rows_by_id: Dict[str, Dict[str, Any]] = {str(r.get("job_id") or ""): r for r in rows if str(r.get("job_id") or "")}
    memo: Dict[str, Dict[str, Any]] = {}
    now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
    dependency_keep: set[str] = set()
    if not include_done:
        for r in rows:
            truth = _project_row_truth(r, rows_by_id, now_ts=now_ts, memo=memo)
            if bool(truth.get("completed_success")):
                continue
            deps = r.get("depends_on") if isinstance(r.get("depends_on"), list) else []
            for dep in deps:
                dep_id = str(dep or "").strip()
                if dep_id:
                    dependency_keep.add(dep_id)
    filtered: List[Dict[str, Any]] = []
    for r in rows:
        due = r.get("due") if isinstance(r.get("due"), dict) else {}
        truth = _project_row_truth(r, rows_by_id, now_ts=now_ts, memo=memo)
        jid = str(r.get("job_id") or "").strip()
        if not include_done and bool(truth.get("completed_success")) and jid not in dependency_keep:
            continue
        if not include_done and str(truth.get("status") or "") == "disabled":
            continue
        filtered.append(r)
    if not filtered:
        return []

    by_id: Dict[str, Dict[str, Any]] = {str(r.get("job_id") or ""): r for r in filtered if str(r.get("job_id") or "")}
    indeg: Dict[str, int] = {jid: 0 for jid in by_id}
    followers: Dict[str, List[str]] = {jid: [] for jid in by_id}

    for jid, row in by_id.items():
        deps = row.get("depends_on") if isinstance(row.get("depends_on"), list) else []
        for dep in deps:
            dep_id = str(dep or "")
            if dep_id in by_id:
                indeg[jid] += 1
                followers[dep_id].append(jid)

    ready: List[str] = [jid for jid, deg in indeg.items() if deg == 0]
    ready.sort(key=lambda jid: _priority_key(by_id[jid]))
    ordered_ids: List[str] = []

    while ready:
        jid = ready.pop(0)
        ordered_ids.append(jid)
        for nxt in followers.get(jid, []):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                ready.append(nxt)
        ready.sort(key=lambda rid: _priority_key(by_id[rid]))

    if len(ordered_ids) < len(by_id):
        unresolved = [jid for jid in by_id if jid not in set(ordered_ids)]
        unresolved.sort(key=lambda jid: _priority_key(by_id[jid]))
        ordered_ids.extend(unresolved)

    out: List[Dict[str, Any]] = []
    for idx, jid in enumerate(ordered_ids[: max(1, limit)], start=1):
        row = by_id[jid]
        deps = row.get("depends_on") if isinstance(row.get("depends_on"), list) else []
        out.append({"index": idx, "row": row, "deps": [str(d) for d in deps if str(d)]})
    return out


def _scheduler_v2_summary_line(payload: Dict[str, Any], *, color: bool) -> str:
    bundle = payload.get("scheduler_v2") if isinstance(payload.get("scheduler_v2"), dict) else {}
    if not bundle:
        return ""
    ownership = bundle.get("ownership") if isinstance(bundle.get("ownership"), dict) else {}
    process = bundle.get("process") if isinstance(bundle.get("process"), dict) else {}
    ports = process.get("public_ports") if isinstance(process.get("public_ports"), list) else []
    status = str(bundle.get("status") or "unknown")
    status_colored = _style(color, status, _status_color("blocked" if status == "blocked" else "ready"))
    parts = [
        f"status={status_colored}",
        f"mode={ownership.get('mode') or 'unknown'}",
        f"owner={ownership.get('canonical_owner') or 'unknown'}",
        f"projection_equivalent={bool(bundle.get('projection_equivalent'))}",
        f"ports={len(ports)}",
    ]
    error = str(bundle.get("error") or "").strip()
    if error:
        parts.append(f"err={error}")
    return f"{_label(color, 'scheduler_v2')} " + " ".join(parts)


def _scheduler_v2_projection(payload: Dict[str, Any]) -> Dict[str, Any]:
    bundle = payload.get("scheduler_v2") if isinstance(payload.get("scheduler_v2"), dict) else {}
    shadow = bundle.get("shadow_payload") if isinstance(bundle.get("shadow_payload"), dict) else {}
    evaluation = shadow.get("evaluation") if isinstance(shadow.get("evaluation"), dict) else {}
    projection = evaluation.get("projection") if isinstance(evaluation.get("projection"), dict) else {}
    return dict(projection)


def _scheduler_v2_is_primary(payload: Dict[str, Any]) -> bool:
    bundle = payload.get("scheduler_v2") if isinstance(payload.get("scheduler_v2"), dict) else {}
    if not bundle:
        return False
    projection = _scheduler_v2_projection(payload)
    if not projection:
        return False
    if bundle.get("projection_equivalent") is False:
        return False
    status = str(bundle.get("status") or "").strip().lower()
    if status and status not in {"ready", "pending_decomposition"}:
        return False
    ownership = bundle.get("ownership") if isinstance(bundle.get("ownership"), dict) else {}
    mode = str(ownership.get("mode") or "").strip().lower()
    return mode in {"", "v2_primary", "v2_shadow"}


def _scheduler_v2_admitted(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    bundle = payload.get("scheduler_v2") if isinstance(payload.get("scheduler_v2"), dict) else {}
    shadow = bundle.get("shadow_payload") if isinstance(bundle.get("shadow_payload"), dict) else {}
    evaluation = shadow.get("evaluation") if isinstance(shadow.get("evaluation"), dict) else {}
    return [dict(row) for row in list(evaluation.get("admitted") or []) if isinstance(row, dict)]


def _scheduler_v2_primary_rows(payload: Dict[str, Any]) -> Tuple[Optional[str], List[str]]:
    if not _scheduler_v2_is_primary(payload):
        return None, []
    projection = _scheduler_v2_projection(payload)
    if not projection:
        return None, []
    active_job = projection.get("active_job") if isinstance(projection.get("active_job"), dict) else {}
    active_job_id = str(active_job.get("job_id") or "").strip()
    next_job_ids = [
        str(row.get("job_id") or "").strip()
        for row in list(projection.get("next_jobs") or [])
        if isinstance(row, dict) and str(row.get("job_id") or "").strip()
    ]
    if active_job_id:
        next_job_ids = [jid for jid in next_job_ids if jid != active_job_id]
    if active_job_id or next_job_ids:
        return active_job_id or None, next_job_ids
    admitted = _scheduler_v2_admitted(payload)
    fallback_active = ""
    fallback_next: List[str] = []
    for row in admitted:
        job_id = str(row.get("job_id") or "").strip()
        decision = str(row.get("decision") or "").strip().lower()
        if not job_id:
            continue
        if decision == "active" and not fallback_active:
            fallback_active = job_id
        elif decision in {"admit", "blocked"} and job_id not in fallback_next:
            fallback_next.append(job_id)
    if fallback_active:
        fallback_next = [jid for jid in fallback_next if jid != fallback_active]
    return fallback_active or None, fallback_next


def _scheduler_v2_family_progress_line(payload: Dict[str, Any]) -> Dict[str, str]:
    if not _scheduler_v2_is_primary(payload):
        return {}
    projection = _scheduler_v2_projection(payload)
    progress = projection.get("family_progress") if isinstance(projection.get("family_progress"), dict) else {}
    parent_uid = str(projection.get("family_parent_uid") or "").strip().upper()
    if not parent_uid:
        return {}
    parent_ref = parent_uid[-4:] if len(parent_uid) >= 4 else parent_uid
    intent_uid = str(_ledger_integration_for_uid(parent_uid).get("intent_sheet_uid") or "").strip().upper()
    intent_ref = intent_uid[-4:] if len(intent_uid) >= 4 else intent_uid
    completed = int(progress.get("completed_children") or 0)
    total = int(progress.get("total_children") or 0)
    active = int(progress.get("active_children") or 0)
    blocked = int(progress.get("blocked_children") or 0)
    failed = int(progress.get("failed_children") or 0)
    return {
        "pis_uid4": parent_ref,
        "intent_uid4": intent_ref,
        "progress": (
            f"v2 family progress: completed={completed}/{total} "
            f"active={active} blocked={blocked} failed={failed} ({parent_ref})"
        ),
    }


def _scheduler_v2_previous_row(
    rows: List[Dict[str, Any]],
    *,
    active_row: Optional[Dict[str, Any]],
    next_rows: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    family_parent_uid = _family_parent_uid_for_row(active_row)
    if not family_parent_uid:
        for row in next_rows:
            family_parent_uid = _family_parent_uid_for_row(row)
            if family_parent_uid:
                break
    active_job_id = str((active_row or {}).get("job_id") or "").strip()
    next_ids = {str(row.get("job_id") or "").strip() for row in next_rows if str(row.get("job_id") or "").strip()}
    best: Optional[Dict[str, Any]] = None
    best_ts = 0.0
    for row in rows:
        job_id = str(row.get("job_id") or "").strip()
        if job_id and (job_id == active_job_id or job_id in next_ids):
            continue
        if family_parent_uid and _family_parent_uid_for_row(row) != family_parent_uid:
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        truth = _projection_truth_for_watch(row)
        last_status = str(state.get("last_status") or "").strip()
        if not bool(truth.get("completed_success")) and not _is_completed_status(last_status):
            continue
        ts = _parse_iso(str(state.get("last_finished_utc") or ""))
        if ts > best_ts:
            best_ts = ts
            best = row
    return best


def _scheduler_v2_row_truth(payload: Dict[str, Any], job_id: str) -> Dict[str, Any]:
    target = str(job_id or "").strip()
    if not target or not _scheduler_v2_is_primary(payload):
        return {}
    projection = _scheduler_v2_projection(payload)
    rows: List[Dict[str, Any]] = []
    active_job = projection.get("active_job") if isinstance(projection.get("active_job"), dict) else {}
    if active_job:
        rows.append(dict(active_job))
    rows.extend(dict(row) for row in list(projection.get("next_jobs") or []) if isinstance(row, dict))
    for row in rows:
        if str(row.get("job_id") or "").strip() != target:
            continue
        child_status = str(row.get("child_status") or "").strip().lower()
        status_reason = str(row.get("status_reason") or "").strip()
        projected_status = ""
        if child_status == "active":
            projected_status = "active"
        elif child_status == "waiting":
            projected_status = "blocked"
        elif child_status == "ready":
            projected_status = "queued"
        elif child_status == "retired":
            projected_status = "completed_once"
        out: Dict[str, Any] = {}
        if projected_status:
            out["status"] = projected_status
        if status_reason:
            out["reason"] = status_reason
        return out
    return {}


def _durable_truth_override(row: Dict[str, Any]) -> Dict[str, Any]:
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    last_status = str(state.get("last_status") or "").strip()
    if _is_completed_status(last_status):
        return {
            "status": "completed_once",
            "reason": "terminal_success",
            "completed_success": True,
            "active": False,
            "terminal": True,
        }
    return {}


def _scheduler_v2_detail_lines(payload: Dict[str, Any], *, color: bool, limit: int = 6) -> List[str]:
    projection = _scheduler_v2_projection(payload)
    if not projection:
        return []
    progress = projection.get("family_progress") if isinstance(projection.get("family_progress"), dict) else {}
    admitted = _scheduler_v2_admitted(payload)
    children = [dict(row) for row in list(projection.get("children") or []) if isinstance(row, dict)]
    child_by_uid = {str(row.get("child_uid") or "").strip(): row for row in children if str(row.get("child_uid") or "").strip()}
    lines = [
        f"{_label(color, 'v2_family')} parent={projection.get('family_parent_uid') or ''} "
        f"status={projection.get('family_status') or ''} stage={projection.get('current_stage') or ''} "
        f"progress={int(progress.get('completed_children') or 0)}/{int(progress.get('total_children') or 0)} "
        f"active={int(progress.get('active_children') or 0)} blocked={int(progress.get('blocked_children') or 0)} failed={int(progress.get('failed_children') or 0)}"
    ]
    role_groups: Dict[str, List[str]] = {}
    for row in children:
        role = str(row.get("fanout_role") or "").strip()
        uid = str(row.get("child_uid") or "").strip()
        if role and uid:
            role_groups.setdefault(role, []).append(uid.rsplit("-", 1)[-1])
    if role_groups:
        chunks = [f"{role}={','.join(uids)}" for role, uids in sorted(role_groups.items())]
        lines.append(f"{_label(color, 'v2_roles')} " + " ".join(chunks))
    active_rows = [row for row in admitted if str(row.get("decision") or "") == "active"]
    next_rows = [row for row in admitted if str(row.get("decision") or "") in {"admit", "blocked"}]
    if active_rows:
        row = active_rows[0]
        node = child_by_uid.get(str(row.get("child_uid") or "").strip(), {})
        lines.append(
            f"{_label(color, 'v2_active')} {str(row.get('child_uid') or '')[-4:]} "
            f"{node.get('title') or row.get('child_uid')} gate={row.get('gate') or ''} reason={row.get('reason') or ''}"
        )
    elif next_rows:
        lines.append(f"{_label(color, 'v2_active')} (none)")
    if next_rows:
        lines.append(_style(color, f"V2 NEXT (showing {min(len(next_rows), max(1, int(limit)))})", "1;37"))
    for idx, row in enumerate(next_rows[: max(1, int(limit))], start=1):
        uid = str(row.get("child_uid") or "").strip()
        node = child_by_uid.get(uid, {})
        role = str(node.get("fanout_role") or "").strip()
        role_part = f" role={role}" if role else ""
        lines.append(
            f"  {idx:02d}. [{row.get('decision') or ''}] {uid[-4:]} "
            f"{node.get('title') or uid} gate={row.get('gate') or ''}{role_part} reason={row.get('reason') or ''}"
        )
    return lines


def _render(
    base_url: str,
    payload: Dict[str, Any],
    *,
    prefix: str = "",
    tz_name: str = DEFAULT_TIMEZONE,
    color: bool = True,
    frame: int = 0,
    pipeline_limit: int = 40,
    include_done: bool = False,
    verbose: bool = False,
) -> str:
    rows = payload.get("jobs") if isinstance(payload.get("jobs"), list) else []
    if prefix:
        rows = [r for r in rows if str(r.get("job_id") or "").startswith(prefix)]
    rows_by_id: Dict[str, Dict[str, Any]] = {str(r.get("job_id") or ""): r for r in rows if str(r.get("job_id") or "")}
    truth_memo: Dict[str, Dict[str, Any]] = {}
    now_ts = dt.datetime.now(dt.timezone.utc).timestamp()
    scheduler = payload.get("scheduler") if isinstance(payload.get("scheduler"), dict) else {}
    watchdog = payload.get("watchdog") if isinstance(payload.get("watchdog"), dict) else {}
    healer = payload.get("healer") if isinstance(payload.get("healer"), dict) else {}
    watchdog = _fresh_watchdog_status(watchdog)
    healer = _fresh_healer_status(healer)

    active_count = sum(1 for r in rows if str(_project_row_truth(r, rows_by_id, now_ts=now_ts, memo=truth_memo).get("status") or "") == "active")
    enabled_count = sum(1 for r in rows if bool(r.get("enabled")))
    blocked_enabled = sum(
        1 for r in rows if bool(r.get("enabled")) and str(_project_row_truth(r, rows_by_id, now_ts=now_ts, memo=truth_memo).get("status") or "").lower() == "blocked"
    )
    error_enabled = sum(
        1 for r in rows if bool(r.get("enabled")) and str(_project_row_truth(r, rows_by_id, now_ts=now_ts, memo=truth_memo).get("status") or "").lower() in {"failed"}
    )

    previous = _pick_previous(rows)
    active = _pick_active(rows)
    next_job = _pick_next(rows)
    spinner = ["⠁", "⠂", "⠄", "⠂"]
    marker = spinner[frame % len(spinner)]

    lines: List[str] = []
    lines.append(_style(color, "Scheduler Live Watch (Ctrl+C to stop)", "1;37"))
    now_utc = _utc_now()
    lines.append(f"{_label(color, 'vienna_now')} {_fmt_time(now_utc, tz_name=tz_name)}")
    if verbose:
        lines.append(f"{_label(color, 'url')} {base_url}")
    last_tick = str(scheduler.get("last_tick_utc") or "")
    last_tick_human = _short_clock(last_tick, tz_name=tz_name)
    scheduler_parts = [
        f"up={_style(color, str(bool(scheduler.get('running'))), '32' if bool(scheduler.get('running')) else '31')}",
        f"tick={last_tick_human or last_tick}",
        f"int={int(scheduler.get('interval_s') or 0)}s",
    ]
    last_error = str(scheduler.get("last_error") or "").strip()
    if last_error:
        scheduler_parts.append(f"err={last_error}")
    lines.append(
        f"{_label(color, 'scheduler')} " + " ".join(scheduler_parts)
    )
    active_count_disagreement = _active_count_disagreement_line(payload, computed_active_count=active_count, color=color)
    if active_count_disagreement:
        lines.append(active_count_disagreement)
    observation_line = _observation_summary_line(payload, color=color)
    if observation_line:
        lines.append(observation_line)
    scheduler_v2_line = _scheduler_v2_summary_line(payload, color=color)
    if scheduler_v2_line:
        lines.append(scheduler_v2_line)
        if not _observation_degraded(payload):
            lines.extend(_scheduler_v2_detail_lines(payload, color=color, limit=pipeline_limit))
    v2_primary_usable = _scheduler_v2_is_primary(payload) and not _observation_degraded(payload)
    if v2_primary_usable:
        v2_active_job_id, v2_next_job_ids = _scheduler_v2_primary_rows(payload)
        if v2_active_job_id:
            active = rows_by_id.get(v2_active_job_id) or active
        v2_next_rows = [rows_by_id[jid] for jid in v2_next_job_ids if jid in rows_by_id]
        if v2_next_rows:
            next_job = v2_next_rows[0]
        previous = _scheduler_v2_previous_row(rows, active_row=active, next_rows=v2_next_rows) or previous
    watchdog_next_utc, watchdog_remaining = _service_next_run(watchdog)
    healer_next_utc, healer_remaining = _service_next_run(healer)
    watchdog_next_human = _fmt_time(watchdog_next_utc, tz_name=tz_name) if watchdog_next_utc else ""
    healer_next_human = _fmt_time(healer_next_utc, tz_name=tz_name) if healer_next_utc else ""
    watchdog_parts = [
        f"running={_style(color, str(bool(watchdog.get('running'))), '32' if bool(watchdog.get('running')) else '31')}",
        f"interval_s={int(watchdog.get('interval_s') or 0)}",
    ]
    last_watchdog = _fmt_time(str(watchdog.get("last_receipt_utc") or ""), tz_name=tz_name)
    if last_watchdog:
        watchdog_parts.append(f"last={last_watchdog}")
    if watchdog_next_utc:
        watchdog_parts.append(
            f"next={watchdog_next_human or watchdog_next_utc}{f' (in {watchdog_remaining})' if watchdog_remaining else ''}"
        )
    watchdog_error = str(watchdog.get("last_error") or "").strip()
    if watchdog_error:
        watchdog_parts.append(f"err={watchdog_error}")
    lines.append(f"{_label(color, 'watchdog')} " + " ".join(watchdog_parts))
    watchdog_busy = _watchdog_busy_line(watchdog)
    if watchdog_busy:
        lines.append(f"{_label(color, 'watchdog_busy', '1;31')} {_style(color, watchdog_busy, '31')}")
    lines.extend(_watchdog_summary_lines(watchdog, tz_name=tz_name, verbose=verbose))
    healer_parts = [
        f"running={_style(color, str(bool(healer.get('running'))), '32' if bool(healer.get('running')) else '31')}",
        f"interval_s={int(healer.get('interval_s') or 0)}",
    ]
    last_healer = _fmt_time(str(healer.get("last_receipt_utc") or ""), tz_name=tz_name)
    if last_healer:
        healer_parts.append(f"last={last_healer}")
    if healer_next_utc:
        healer_parts.append(
            f"next={healer_next_human or healer_next_utc}{f' (in {healer_remaining})' if healer_remaining else ''}"
        )
    healer_error = str(healer.get("last_error") or "").strip()
    if healer_error:
        healer_parts.append(f"err={healer_error}")
    lines.append(f"{_label(color, 'healer')} " + " ".join(healer_parts))
    actionable_rows = [
        row
        for row in rows
        if bool(row.get("enabled"))
        or bool(str(((row.get("state") or {}).get("active_run_id") or "")).strip())
        or bool(str(((row.get("state") or {}).get("run_now_requested_utc") or "")).strip())
    ]
    lines.append(
        f"{_label(color, 'scope')} {len(actionable_rows)} jobs active={active_count} blocked={blocked_enabled} error={error_enabled}"
    )
    pipeline = _pipeline_rows(rows, include_done=include_done, limit=max(max(1, int(pipeline_limit)), len(rows) or 1))
    active_job_id = str(active.get("job_id") or "").strip() if isinstance(active, dict) else ""
    scope_row = active or ((pipeline[0].get("row") if pipeline else None) if isinstance(pipeline, list) else None) or next_job
    auxiliary_sidecars = [
        item
        for item in pipeline
        if str((item.get("row") or {}).get("job_id") or "").strip() != active_job_id
        and _should_show_auxiliary_reconcile_sidecar(item.get("row") if isinstance(item.get("row"), dict) else {})
    ]
    primary_chain = _family_chain_items(scope_row, rows)
    if v2_primary_usable:
        v2_chain = _v2_primary_chain_rows(payload, scope_row, rows)
        if v2_chain:
            primary_chain = v2_chain
    if v2_primary_usable:
        _v2_active_job_id, v2_next_job_ids = _scheduler_v2_primary_rows(payload)
        if _v2_active_job_id:
            active_job_id = _v2_active_job_id
        next_in_line = [
            {"index": idx, "row": rows_by_id[job_id], "deps": list((rows_by_id[job_id].get("depends_on") or []))}
            for idx, job_id in enumerate(v2_next_job_ids, start=1)
            if job_id in rows_by_id
        ]
    else:
        next_in_line = [
            item
            for item in pipeline
            if str((item.get("row") or {}).get("job_id") or "").strip() != active_job_id
            and _is_next_in_line_candidate(item.get("row") if isinstance(item.get("row"), dict) else {})
        ]
    next_in_line = [
        item
        for item in next_in_line
        if not _is_auxiliary_reconcile_sidecar(item.get("row") if isinstance(item.get("row"), dict) else {})
    ]
    active_parent_uid = _family_parent_uid_for_row(scope_row)
    if next_in_line and active_parent_uid:
        family_only = [
            item
            for item in next_in_line
            if _family_parent_uid_for_row(item.get("row") if isinstance(item.get("row"), dict) else {}) == active_parent_uid
        ]
        if family_only:
            next_in_line = family_only
    if next_in_line and active_parent_uid:
        next_in_line.sort(
            key=lambda item: (
                0
                if _family_parent_uid_for_row(item.get("row") if isinstance(item.get("row"), dict) else {}) == active_parent_uid
                else 1,
                int(item.get("index") or 0),
            )
        )
    next_in_line = next_in_line[: max(1, int(pipeline_limit))]
    active_scope_line = _scheduler_v2_family_progress_line(payload) if v2_primary_usable else _active_family_scope_line(scope_row, rows)
    if active_scope_line:
        pis_uid4 = str(active_scope_line.get("pis_uid4") or "").strip()
        intent_uid4 = str(active_scope_line.get("intent_uid4") or "").strip()
        progress = str(active_scope_line.get("progress") or "").strip()
        if pis_uid4:
            lines.append(f"{_label(color, 'pis_uid4')} {pis_uid4}")
        if intent_uid4:
            lines.append(f"{_label(color, 'intent_uid4')} {intent_uid4}")
        if progress:
            lines.append(f"{_label(color, 'family_progress')} {progress}")
    if prefix:
        lines.append(f"{_label(color, 'filter_prefix')} {prefix}")
    lines.append("")
    if verbose:
        lines.extend(_fmt_job_block("PREVIOUS", previous, tz_name=tz_name, color=color, mode="previous"))
        lines.append("")
        lines.extend(_fmt_job_block("ACTIVE", active, tz_name=tz_name, color=color, active_marker=marker if active else "", mode="active"))
        lines.append("")
        lines.extend(_fmt_job_block("NEXT", next_job, tz_name=tz_name, color=color))
        lines.append("")
    else:
        lines.append(_style(color, "ACTIVE", "1;37"))
        if active:
            lines.append(
                "  "
                + _compact_job_line(
                    active,
                    index=1,
                    tz_name=tz_name,
                    truth={
                        **_project_row_truth(active, rows_by_id, now_ts=now_ts, memo=truth_memo),
                        **_scheduler_v2_row_truth(payload, str(active.get("job_id") or "")),
                    },
                )
            )
        else:
            lines.append("  (none)")
        lines.append("")
    if verbose and primary_chain:
        lines.append(_style(color, f"PRIMARY CHAIN (showing {min(len(primary_chain), max(1, int(pipeline_limit)))})", "1;37"))
        primary_rows_by_id: Dict[str, Dict[str, Any]] = {
            str(row.get("job_id") or "").strip(): row
            for row in primary_chain
            if str(row.get("job_id") or "").strip()
        }
        primary_truth_memo: Dict[str, Dict[str, Any]] = {}
        for local_idx, row in enumerate(primary_chain[: max(1, int(pipeline_limit))], start=1):
            truth = _chain_truth_for_row(row, rows_by_id=primary_rows_by_id, now_ts=now_ts, memo=primary_truth_memo)
            lines.append("  " + _compact_job_line(row, index=local_idx, tz_name=tz_name, truth=truth))
        lines.append("")
    if verbose or next_in_line:
        lines.append(_style(color, f"NEXT IN LINE (showing {len(next_in_line)})", "1;37"))
    if verbose and not next_in_line:
        lines.append("  (none)")
    for local_idx, item in enumerate(next_in_line, start=1):
        idx = local_idx
        row = item["row"]
        deps = item["deps"]
        jid = str(row.get("job_id") or "")
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        due = row.get("due") if isinstance(row.get("due"), dict) else {}
        truth = _project_row_truth(row, rows_by_id, now_ts=now_ts, memo=truth_memo)
        if verbose:
            title, summary = _job_title_and_summary(row)
            status = str(truth.get("status") or "unknown")
            status_colored = _style(color, status, _status_color(status))
            due_human = _fmt_time(str(due.get("due_utc") or ""), tz_name=tz_name)
            reason = str(truth.get("reason") or due.get("reason") or "")
            deps_txt = ",".join(deps[:3]) + ("..." if len(deps) > 3 else "")
            title_txt = title or "(no title)"
            uid4 = _extract_pis_uid4(row, title_txt)
            job8 = _extract_job_tail8(jid)
            title_colored = _style(color, title_txt, "1;36")
            tags = ", ".join([x for x in [uid4, job8] if x])
            enabled_txt = "enabled" if bool(row.get("enabled")) else "disabled"
            enabled_colored = _style(color, enabled_txt, "32" if enabled_txt == "enabled" else "90")
            lines.append(f"  {idx:02d}. [{status_colored}] [{enabled_colored}] {title_colored}{', ' + tags if tags else ''}")
            family_slugs = _job_family_slugs(row)
            if summary:
                lines.append(f"      {summary}")
            if family_slugs.get("parent_slug") or family_slugs.get("intent_slug"):
                slug_parts: List[str] = []
                if family_slugs.get("parent_slug"):
                    slug_parts.append(f"parent={family_slugs['parent_slug']}")
                if family_slugs.get("intent_slug"):
                    slug_parts.append(f"intent={family_slugs['intent_slug']}")
                lines.append("      " + " ".join(slug_parts))
            lines.append(f"      due={reason}{' @ ' + due_human if due_human else ''}{' deps=' + deps_txt if deps_txt else ''}")
            model_line = _runtime_model_summary(row)
            if model_line:
                lines.append(f"      model: {model_line}")
        else:
            lines.append(
                "  "
                + _compact_job_line(
                    row,
                    index=idx,
                    tz_name=tz_name,
                    truth={
                        **truth,
                        **_scheduler_v2_row_truth(payload, str(row.get("job_id") or "")),
                    },
                )
            )
    if auxiliary_sidecars:
        lines.append("")
        lines.append(_style(color, f"AUXILIARY (showing {len(auxiliary_sidecars)})", "1;37"))
        for local_idx, item in enumerate(auxiliary_sidecars[: max(1, int(pipeline_limit))], start=1):
            row = item["row"]
            truth = _project_row_truth(row, rows_by_id, now_ts=now_ts, memo=truth_memo)
            lines.append("  " + _compact_job_line(row, index=local_idx, tz_name=tz_name, truth=truth))
    return "\n".join(lines)


T = TypeVar("T")


def _prompt_value(prompt: str, default: Any, cast: type[T]) -> T:
    while True:
        try:
            raw = input(f"{prompt} [default: {default}]: ").strip()
        except EOFError:
            return default
        if raw == "":
            return default
        try:
            return cast(raw)
        except ValueError:
            print(f"Invalid {prompt}; expected {cast.__name__}.", flush=True)


def _argv_has_flag(name: str) -> bool:
    flag = f"--{name}"
    return any(arg == flag or str(arg).startswith(flag + "=") for arg in sys.argv[1:])


def _color_enabled(*, no_color: bool) -> bool:
    if bool(no_color):
        return False
    if str(os.environ.get("NO_COLOR") or "").strip():
        return False
    if str(os.environ.get("TERM") or "").strip().lower() == "dumb":
        return False
    return True


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live scheduler monitor (previous/active/next).")
    p.add_argument("--url", default=DEFAULT_URL, help="WebUI base URL (default: http://localhost:8787)")
    p.add_argument("--interval", type=float, default=2.0, help="Refresh interval seconds (default: 2.0)")
    p.add_argument("--prefix", default="", help="Optional job_id prefix filter")
    p.add_argument("--timezone", default=DEFAULT_TIMEZONE, help="Display timezone (default: Europe/Vienna)")
    p.add_argument("--no-color", action="store_true", help="Disable ANSI colors")
    p.add_argument("--no-clear", action="store_true", help="Do not clear screen on refresh")
    p.add_argument("--pipeline-limit", type=int, default=40, help="Max rows to show in pipeline list (default: 40)")
    p.add_argument("--include-done", action="store_true", help="Include already-executed jobs in pipeline list")
    p.add_argument("--verbose", action="store_true", help="Show detailed PREVIOUS/ACTIVE/NEXT blocks and multi-line pipeline rows")
    p.add_argument("--oneshot", action="store_true", help="Render one snapshot and exit")
    p.add_argument("--startup-grace", type=float, default=DEFAULT_STARTUP_GRACE_S, help="Grace window after first connection failure before treating startup as a hard error (default: 30)")
    args = p.parse_args()
    if sys.stdin.isatty():
        if not _argv_has_flag("interval"):
            args.interval = _prompt_value("interval", args.interval, float)
        if not _argv_has_flag("pipeline-limit"):
            args.pipeline_limit = _prompt_value("pipeline-limit", args.pipeline_limit, int)
    return args


def main() -> int:
    args = parse_args()
    frame = 0
    color = _color_enabled(no_color=bool(args.no_color))
    first_error_ts = 0.0
    while True:
        try:
            if _is_local_base_url(args.url):
                # For local operator use, durable on-disk scheduler state is the
                # authoritative watch source.  The HTTP jobs endpoint has been a
                # recurrent source of stale or partial views under load.
                payload = _local_jobs_payload(args.url, reason="local_authoritative_watch")
            else:
                payload = _api_get(args.url, "/api/schedules/jobs")
            first_error_ts = 0.0
            view = _render(
                args.url,
                payload,
                prefix=str(args.prefix or "").strip(),
                tz_name=str(args.timezone or DEFAULT_TIMEZONE),
                color=color,
                frame=frame,
                pipeline_limit=max(1, int(args.pipeline_limit)),
                include_done=bool(args.include_done),
                verbose=bool(args.verbose),
            )
            if not args.no_clear:
                _clear()
            print(view, flush=True)
            if bool(args.oneshot):
                return 0
            frame += 1
        except (urllib.error.URLError, TimeoutError, socket.timeout) as e:
            if _is_local_base_url(args.url):
                try:
                    payload = _local_jobs_payload(args.url, reason=str(e))
                    first_error_ts = 0.0
                    view = _render(
                        args.url,
                        payload,
                        prefix=str(args.prefix or "").strip(),
                        tz_name=str(args.timezone or DEFAULT_TIMEZONE),
                        color=color,
                        frame=frame,
                        pipeline_limit=max(1, int(args.pipeline_limit)),
                        include_done=bool(args.include_done),
                        verbose=bool(args.verbose),
                    )
                    if not args.no_clear:
                        _clear()
                    print(view, flush=True)
                    if bool(args.oneshot):
                        return 0
                    frame += 1
                    time.sleep(max(0.5, float(args.interval)))
                    continue
                except Exception:
                    pass
            if not args.no_clear:
                _clear()
            now = time.time()
            if first_error_ts <= 0.0:
                first_error_ts = now
            elapsed = max(0.0, now - first_error_ts)
            if elapsed < max(0.0, float(args.startup_grace)):
                print(
                    f"Scheduler Live Watch: server not ready yet ({e}); waiting for startup, {max(0.0, float(args.startup_grace) - elapsed):.0f}s grace left",
                    flush=True,
                )
            else:
                print(f"Scheduler Live Watch error: {e}", flush=True)
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
            return 0
        except Exception as e:
            if not args.no_clear:
                _clear()
            print(f"Scheduler Live Watch unexpected error: {e}", flush=True)
        try:
            time.sleep(max(0.5, float(args.interval)))
        except KeyboardInterrupt:
            print("\nStopped.", flush=True)
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
