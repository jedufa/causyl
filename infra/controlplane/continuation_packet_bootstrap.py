"""Fail-closed continuation-packet selection for resumed or successor bootstrap."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.controlplane import continuation_packet_store as _continuation_packet_store


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _current_run(task: Dict[str, Any]) -> Dict[str, Any]:
    run_id = _clean_text(task.get("current_run_id"))
    runs = [dict(row) for row in list(task.get("task_runs") or []) if isinstance(row, dict)]
    if run_id:
        for row in runs:
            if _clean_text(row.get("run_id")) == run_id:
                return dict(row)
    return dict(runs[-1]) if runs else {}


def _task_requires_packet(task: Dict[str, Any], current_run: Dict[str, Any]) -> bool:
    if current_run.get("checkpoint"):
        return True
    if current_run.get("resume_decision"):
        return True
    run_state = _clean_text(current_run.get("run_state")).lower()
    return run_state in {"completed", "failed", "ambiguous", "interrupted", "timed_out", "heartbeat_stale", "stale"}


def _lookup_candidates(task: Dict[str, Any], current_run: Dict[str, Any]) -> List[Dict[str, str]]:
    owner = dict(task.get("execution_surface_ownership") or {})
    out: List[Dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for source, value in (
        ("current_run_id", task.get("current_run_id")),
        ("task_runs.run_id", current_run.get("run_id")),
    ):
        text = _clean_text(value)
        key = ("run_id", text)
        if not text or key in seen:
            continue
        seen.add(key)
        out.append({"lookup_key": "run_id", "lookup_value": text, "source": source})
    text = _clean_text(task.get("current_run_id"))
    key = ("job_id", text)
    if text and key not in seen:
        seen.add(key)
        out.append({"lookup_key": "job_id", "lookup_value": text, "source": "current_run_id"})
    text = _clean_text(owner.get("execution_id"))
    key = ("job_id", text)
    if text and key not in seen:
        seen.add(key)
        out.append({"lookup_key": "job_id", "lookup_value": text, "source": "execution_surface_ownership.execution_id"})
    return out


def _task_activity_stamp(task: Dict[str, Any]) -> str:
    current_run = _current_run(task)
    for value in (
        current_run.get("updated_utc"),
        current_run.get("created_utc"),
        task.get("updated_utc"),
        task.get("created_utc"),
    ):
        text = _clean_text(value)
        if text:
            return text
    return ""


def _select_task_for_binding(matches: List[Dict[str, Any]], *, binding_type: str) -> Dict[str, Any] | None:
    if len(matches) <= 1:
        return dict(matches[0]) if matches else None
    if binding_type != "ledger_uid":
        return None
    non_intake = [dict(task) for task in matches if _clean_text(task.get("source")).lower() != "operator_intake"]
    if len(non_intake) == 1:
        return dict(non_intake[0])

    def _selection_score(task: Dict[str, Any]) -> tuple:
        source = _clean_text(task.get("source")).lower()
        owner = _clean_text(task.get("owner")).lower()
        status = _clean_text(task.get("status")).lower()
        title = _clean_text(task.get("title"))
        current_run = _current_run(task)
        is_controller = title.startswith("[Codex Scheduled Controller Contract]")
        is_advisory = title.startswith("[Scheduled Advisory Backend Request]")
        has_current_run = bool(_clean_text(task.get("current_run_id")) or current_run)
        return (
            1 if source != "operator_intake" else 0,
            1 if owner in {"scheduler", "pipeline", "watchdog", "healer"} else 0,
            1 if is_controller else 0,
            0 if is_advisory else 1,
            1 if status == "active" else 0,
            1 if has_current_run else 0,
            _task_activity_stamp(task),
        )

    ranked = sorted(
        (dict(task) for task in matches),
        key=lambda task: (_selection_score(task), _clean_text(task.get("task_id"))),
        reverse=True,
    )
    if not ranked:
        return None
    if len(ranked) == 1 or _selection_score(ranked[0]) > _selection_score(ranked[1]):
        return dict(ranked[0])
    return None


def packet_snapshot_for_task(task: Dict[str, Any], *, repo_root: Path) -> Dict[str, Any]:
    current_run = _current_run(task)
    required = _task_requires_packet(task, current_run)
    candidates = _lookup_candidates(task, current_run)
    snapshot: Dict[str, Any] = {
        "task_id": _clean_text(task.get("task_id")),
        "ledger_uid": _clean_text(task.get("ledger_uid")),
        "required": bool(required),
        "mode": "none",
        "reason": "",
        "lookup_candidates": candidates,
        "packet_record": None,
    }
    if not candidates:
        if required:
            snapshot["mode"] = "degraded"
            snapshot["reason"] = "missing_lookup_identity_for_packet_bootstrap"
        return snapshot
    for candidate in candidates:
        try:
            packet = _continuation_packet_store.latest_valid_packet(
                lookup_key=str(candidate["lookup_key"]),
                lookup_value=str(candidate["lookup_value"]),
                repo_root=repo_root,
            )
        except _continuation_packet_store.AmbiguousLatestValidPacketError as exc:
            snapshot["mode"] = "blocked"
            snapshot["reason"] = str(exc)
            snapshot["selected_lookup"] = dict(candidate)
            return snapshot
        except _continuation_packet_store.ContinuationPacketStoreError as exc:
            snapshot["mode"] = "blocked"
            snapshot["reason"] = str(exc)
            snapshot["selected_lookup"] = dict(candidate)
            return snapshot
        if packet is not None:
            snapshot["mode"] = "ready"
            snapshot["selected_lookup"] = dict(candidate)
            snapshot["packet_record"] = dict(packet)
            return snapshot
    if required:
        snapshot["mode"] = "degraded"
        snapshot["reason"] = "no_valid_continuation_packet_for_resumed_or_successor_work"
    return snapshot


def packet_snapshot_for_binding(
    binding: Dict[str, Any] | None,
    *,
    task_register: Dict[str, Any] | None,
    repo_root: Path,
) -> Dict[str, Any]:
    register_obj = task_register if isinstance(task_register, dict) else {}
    tasks = [dict(row) for row in list(register_obj.get("active_tasks") or []) if isinstance(row, dict)]
    binding_obj = binding if isinstance(binding, dict) else {}
    binding_type = _clean_text(binding_obj.get("type"))
    binding_id = _clean_text(binding_obj.get("id"))
    if not binding_type or not binding_id:
        return {"mode": "none", "reason": "binding_not_task_addressable"}
    if binding_type == "task_id":
        matches = [task for task in tasks if _clean_text(task.get("task_id")) == binding_id]
    elif binding_type == "ledger_uid":
        matches = [task for task in tasks if _clean_text(task.get("ledger_uid")) == binding_id]
    else:
        return {"mode": "none", "reason": f"binding_type_not_supported:{binding_type}"}
    if not matches:
        return {"mode": "none", "reason": "binding_task_not_present"}
    if len(matches) > 1:
        selected = _select_task_for_binding(matches, binding_type=binding_type)
        if selected is None:
            return {
                "mode": "blocked",
                "reason": f"multiple active tasks match binding {binding_type}={binding_id}",
                "match_count": len(matches),
            }
        snapshot = packet_snapshot_for_task(selected, repo_root=repo_root)
        snapshot["binding_match_count"] = len(matches)
        snapshot["binding_selected_task_id"] = _clean_text(selected.get("task_id"))
        return snapshot
    return packet_snapshot_for_task(matches[0], repo_root=repo_root)


def register_packet_snapshot(task_register: Dict[str, Any] | None, *, repo_root: Path) -> Dict[str, Any]:
    register_obj = task_register if isinstance(task_register, dict) else {}
    tasks = [dict(row) for row in list(register_obj.get("active_tasks") or []) if isinstance(row, dict)]
    rows = [packet_snapshot_for_task(task, repo_root=repo_root) for task in tasks]
    selected = [row for row in rows if row.get("required") or row.get("mode") == "ready"]
    return {
        "rows": selected,
        "ready_count": sum(1 for row in selected if row.get("mode") == "ready"),
        "degraded_count": sum(1 for row in selected if row.get("mode") == "degraded"),
        "blocked_count": sum(1 for row in selected if row.get("mode") == "blocked"),
    }


def render_packet_line(snapshot: Dict[str, Any]) -> str:
    mode = _clean_text(snapshot.get("mode")) or "none"
    task_id = _clean_text(snapshot.get("task_id")) or _clean_text(snapshot.get("ledger_uid")) or "binding"
    if mode == "ready":
        packet_record = dict(snapshot.get("packet_record") or {})
        packet = dict(packet_record.get("packet") or packet_record)
        selected_lookup = dict(snapshot.get("selected_lookup") or {})
        result_summary = dict(packet.get("result_summary") or {})
        packet_id = _clean_text(packet_record.get("packet_id") or packet.get("packet_id"))
        packet_revision = int(packet_record.get("packet_revision") or packet.get("packet_revision") or 0)
        lookup_key = _clean_text(selected_lookup.get("lookup_key"))
        lookup_value = _clean_text(selected_lookup.get("lookup_value"))
        outcome = _clean_text(result_summary.get("outcome"))
        summary = _clean_text(result_summary.get("summary"))
        return (
            f"- [{task_id}] ready; packet={packet_id}:r{packet_revision}; "
            f"via={lookup_key}={lookup_value}; outcome={outcome or 'unknown'}; "
            f"summary={summary or 'durable continuation packet selected'}"
        )
    if mode in {"degraded", "blocked"}:
        return f"- [{task_id}] {mode}; reason={_clean_text(snapshot.get('reason')) or 'unknown'}"
    return f"- [{task_id}] none; reason={_clean_text(snapshot.get('reason')) or 'packet_not_required'}"
