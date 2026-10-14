from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List

from infra.controlplane import campaign_operator_summary as _campaign_operator_summary
from infra.controlplane import continuation_context_attachment as _continuation_attachment
from infra.webui import server_agents as _agents
from infra.webui import server_agents_orchestrator as _agent_orch
from infra.controlplane import scheduled_jobs as _scheduled_jobs
from infra.webui import server_execution_queue as _execution_queue
from infra.webui import server_lab_assistant_host as _lab_host
from infra.webui import server_lab_assistant_task_register as _task_register
from infra.webui.server_archives import runs_archives_dir as _runs_archives_dir
from infra.webui.server_http_core import _admin_archives_dir, _admin_list_archives
from infra.webui.ledger import ledger_schema as _ledger_schema
from infra.webui.ledger.idea_note_manager import IdeaNoteLedger
from infra.webui.ledger.ledger_manager import ChangeLedger


SCHEMA_COCKPIT = "mobile_cockpit_v1"
SCHEMA_CONTRACT = "mobile_cockpit_contract_v1"
SCHEMA_CAMPAIGN = "mobile_cockpit_campaign_v1"
OPEN_STATUSES = {
    "draft",
    "ready_for_implement",
    "ready_to_implement",
    "ready",
}

_ENTITY_CONTRACTS: Dict[str, Dict[str, str]] = {
    "pis": {
        "owner": "ledger",
        "id_field": "uid",
        "source": "governance/ledger/patch_instruction_sheets",
    },
    "campaign": {
        "owner": "mobile_cockpit",
        "id_field": "campaign_id",
        "source": "governance/agents/runtime/mobile_cockpit/campaigns",
    },
    "run": {
        "owner": "execution_queue_or_workflow",
        "id_field": "run_id",
        "source": "governance/agents/runtime/execution_queue",
    },
    "artifact": {
        "owner": "runtime_artifact_store",
        "id_field": "artifact_id",
        "source": "runtime/state and workflow artifacts",
    },
    "archive_snapshot": {
        "owner": "archive_subsystem",
        "id_field": "snapshot_id",
        "source": "archives and exported bundles",
    },
    "failure_case": {
        "owner": "workflow_or_execution_surface",
        "id_field": "failure_id",
        "source": "workflow status and queue failure records",
    },
    "liveness_signal": {
        "owner": "runtime_health_projection",
        "id_field": "signal_id",
        "source": "health, queue, and workflow freshness surfaces",
    },
    "chronicle_entry": {
        "owner": "timeline_projection",
        "id_field": "entry_id",
        "source": "ledger events, queue events, and operation history",
    },
}

_SHARED_STATUS_LANGUAGE = [
    "draft",
    "ready",
    "queued",
    "running",
    "waiting_on_operator",
    "blocked",
    "degraded",
    "completed",
    "failed",
    "released",
]

_SHARED_ACTION_LANGUAGE = [
    "select_pis",
    "launch_campaign",
    "refresh_projection",
    "inspect_workflow",
    "prioritize_pis",
    "update_status",
    "stop_queue",
]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_root() -> Path:
    root = (_repo_root() / "governance" / "agents" / "runtime" / "mobile_cockpit").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _campaigns_root() -> Path:
    root = (_runtime_root() / "campaigns").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _latest_campaign_path() -> Path:
    return (_runtime_root() / "latest_campaign.json").resolve()


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ").lower()


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _read_jsonl(path: Path, *, limit: int = 120) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if len(lines) > limit:
        lines = lines[-limit:]
    out: List[Dict[str, Any]] = []
    for raw in lines:
        try:
            row = json.loads(raw)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    raw = json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _read_text_preview(path: Path, *, max_chars: int = 2000) -> Dict[str, Any]:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception as exc:
        return {"available": False, "error": str(exc), "preview_text": ""}
    preview = txt[: max(0, int(max_chars))]
    return {
        "available": True,
        "preview_text": preview,
        "truncated": len(txt) > len(preview),
        "line_count": len(txt.splitlines()),
        "byte_count": len(txt.encode("utf-8", errors="replace")),
    }


def _normalize_topic_tags(item: Dict[str, Any]) -> List[str]:
    topic_tags = item.get("topic_tags")
    tags = item.get("tags")
    source = topic_tags if isinstance(topic_tags, list) else tags if isinstance(tags, list) else []
    return _ledger_schema.normalize_topic_tags(source)


def _group_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"status", "topic"} else "status"


def _group_rows(*, items: List[Dict[str, Any]], group_mode: str) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[str]] = {}
    if group_mode == "topic":
        for item in items:
            uid = str(item.get("uid") or "").strip()
            if not uid:
                continue
            tags = list(item.get("topic_tags") or [])
            if not tags:
                buckets.setdefault("untagged", []).append(uid)
                continue
            for tag in tags:
                buckets.setdefault(str(tag), []).append(uid)
    else:
        for item in items:
            uid = str(item.get("uid") or "").strip()
            if not uid:
                continue
            status = str(item.get("status") or "").strip().lower() or "unknown"
            key = "ready" if status.startswith("ready") else status
            buckets.setdefault(key, []).append(uid)
    rows = [{"key": key, "count": len(uids), "uids": uids[:120]} for key, uids in buckets.items()]
    rows.sort(key=lambda row: (-int(row.get("count") or 0), str(row.get("key") or "")))
    return rows


def _scheduled_projection(*, items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    by_uid = {str(item.get("uid") or "").strip(): item for item in items if str(item.get("uid") or "").strip()}
    out: Dict[str, List[Dict[str, Any]]] = {}
    if not by_uid:
        return out
    try:
        jobs = _scheduled_jobs.list_jobs()
    except Exception:
        return out
    for job in jobs:
        binding = job.get("binding") if isinstance(job.get("binding"), dict) else {}
        if str(binding.get("type") or "").strip().lower() != "ledger_uid":
            continue
        uid = str(binding.get("id") or "").strip()
        if uid not in by_uid:
            continue
        out.setdefault(uid, []).append(
            {
                "job_id": str(job.get("job_id") or ""),
                "job_kind": str(job.get("job_kind") or ""),
                "enabled": bool(job.get("enabled")),
                "due_reason": str((job.get("due") or {}).get("reason") or ""),
                "topic_tags": list(by_uid[uid].get("topic_tags") or []),
            }
        )
    return out


def _idea_backlog_rows(*, limit: int) -> List[Dict[str, Any]]:
    ledger = IdeaNoteLedger(repo_root=_repo_root())
    out: List[Dict[str, Any]] = []
    for raw in ledger.list_notes(limit=max(1, min(int(limit), 500))):
        item = dict(raw or {})
        status = str(item.get("status") or "").strip().lower()
        if status not in {"draft", "active", "exploring", "parked"}:
            continue
        item["release_state"] = "pre_intent"
        item["topic_tags"] = _normalize_topic_tags(item)
        item["backlog_kind"] = "idea_note"
        out.append(item)
    return out


def backlog_projection(*, limit: int = 250, topic_filter: str = "", group_mode: str = "status") -> Dict[str, Any]:
    limit_norm = max(1, min(int(limit), 500))
    ledger = ChangeLedger(repo_root=_repo_root())
    rows = list(ledger.list_items(limit=limit_norm)) + _idea_backlog_rows(limit=limit_norm)
    topic_filter_norm = str(topic_filter or "").strip().lower()
    if topic_filter_norm:
        normalized_topic = _ledger_schema.normalize_topic_tags([topic_filter_norm])
        topic_filter_norm = str(normalized_topic[0]) if normalized_topic else ""
    group_mode_norm = _group_mode(group_mode)
    filtered: List[Dict[str, Any]] = []
    for raw in rows:
        item = dict(raw or {})
        item.setdefault("backlog_kind", "pis" if str(item.get("uid") or "").startswith("PIS-") else "idea_note")
        status = str(item.get("status") or "").strip().lower()
        if item.get("backlog_kind") == "idea_note":
            if status not in {"draft", "active", "exploring", "parked"}:
                continue
        elif status not in OPEN_STATUSES:
            continue
        item["topic_tags"] = _normalize_topic_tags(item)
        if topic_filter_norm and topic_filter_norm not in set(item.get("topic_tags") or []):
            continue
        filtered.append(item)
    topic_catalog: Dict[str, int] = {}
    for item in filtered:
        tags = list(item.get("topic_tags") or [])
        if not tags:
            topic_catalog["untagged"] = topic_catalog.get("untagged", 0) + 1
        for tag in tags:
            topic_catalog[str(tag)] = topic_catalog.get(str(tag), 0) + 1
    draft_count = sum(1 for item in filtered if str(item.get("status") or "").strip().lower() == "draft")
    return {
        "items": filtered,
        "filters": {"topic": topic_filter_norm, "group_mode": group_mode_norm},
        "topic_catalog": [
            {"tag": tag, "count": int(count)}
            for tag, count in sorted(topic_catalog.items(), key=lambda kv: (-int(kv[1]), str(kv[0])))
        ],
        "groups": _group_rows(items=filtered, group_mode=group_mode_norm),
        "scheduled_bindings": _scheduled_projection(items=filtered),
        "counts": {
            "draft": int(draft_count),
            "ready": max(0, len(filtered) - draft_count),
            "total": int(len(filtered)),
        },
    }


def _contract_health() -> Dict[str, Any]:
    required = {
        "pis",
        "campaign",
        "run",
        "artifact",
        "archive_snapshot",
        "failure_case",
        "liveness_signal",
        "chronicle_entry",
    }
    present = set(_ENTITY_CONTRACTS.keys())
    missing = sorted(required - present)
    return {
        "ok": not missing,
        "required_entities": sorted(required),
        "missing_entities": missing,
    }


def shared_contract_payload() -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_CONTRACT,
        "entity_contracts": dict(_ENTITY_CONTRACTS),
        "shared_status_language": list(_SHARED_STATUS_LANGUAGE),
        "shared_action_language": list(_SHARED_ACTION_LANGUAGE),
        "projection_rules": [
            "source_truth_beats_mobile_presentation",
            "unknown_or_stale_state_degrades_explicitly",
            "campaign_and_run_ids_must_be_reused_across_surfaces",
            "selected_pis_launches_must_remain_reviewable_after restart",
        ],
        "contract_check": _contract_health(),
    }


def _campaign_path(campaign_id: str) -> Path:
    safe_id = str(campaign_id or "").strip()
    return (_campaigns_root() / f"{safe_id}.json").resolve()


def _load_latest_campaign() -> Dict[str, Any]:
    latest = _read_json(_latest_campaign_path())
    campaign_id = str(latest.get("campaign_id") or "").strip()
    if not campaign_id:
        return {}
    payload = _read_json(_campaign_path(campaign_id))
    return payload if str(payload.get("campaign_id") or "").strip() == campaign_id else {}


def _campaign_summary_payload(*, latest_campaign: Dict[str, Any]) -> Dict[str, Any]:
    campaign_record = latest_campaign.get("campaign_record")
    if not isinstance(campaign_record, dict):
        return {
            "status": "degraded",
            "reason": "campaign_record_missing",
            "summary": "Campaign summary unavailable because no durable campaign record is attached yet.",
        }
    try:
        summary = _campaign_operator_summary.build_campaign_operator_summary(
            campaign_record=campaign_record,
            phase_gate_decisions=[row for row in list(latest_campaign.get("phase_gate_decisions") or []) if isinstance(row, dict)],
            phase_runtime_facts=[row for row in list(latest_campaign.get("phase_runtime_facts") or []) if isinstance(row, dict)],
            repo_root=_repo_root(),
        )
    except Exception as exc:
        return {
            "status": "blocked",
            "reason": "campaign_summary_build_failed",
            "summary": f"Campaign summary could not be derived from durable records: {exc}",
        }
    return {
        "status": "ok",
        "reason": "",
        "summary": summary["headline"],
        **summary,
    }


def _normalize_uid_list(raw: Any) -> List[str]:
    out: List[str] = []
    for value in list(raw or []):
        uid = str(value or "").strip()
        if not uid or uid in out:
            continue
        out.append(uid)
    return out


def _artifact_payload(*, primary_uid: str) -> Dict[str, Any]:
    if not primary_uid:
        return {
            "status": "degraded",
            "reason": "no_primary_uid",
            "latest_handoff": {},
            "latest_failed_action_log": {},
        }
    binding = {"type": "ledger_uid", "id": primary_uid}
    latest_handoff: Dict[str, Any] = {}
    for role in ("builder", "assurer", "generalist"):
        try:
            resolved = _agents.resolve_resume_artifact(role=role, binding=binding, kind="latest_handoff")
        except Exception:
            continue
        path = Path(str(resolved.get("path") or "")).resolve()
        if not path.exists() or not path.is_file() or path.is_symlink():
            continue
        latest_handoff = {
            "role": role,
            "artifact_kind": "latest_handoff",
            "path": str(path),
            "name": str(resolved.get("name") or path.name),
            "sha256": _sha256_file(path),
            **_read_text_preview(path),
        }
        break
    latest_failed_action_log: Dict[str, Any] = {}
    try:
        resolved = _agents.resolve_resume_artifact(role="builder", binding=binding, kind="latest_failed_action_log")
        path = Path(str(resolved.get("path") or "")).resolve()
        if path.exists() and path.is_file() and not path.is_symlink():
            latest_failed_action_log = {
                "role": "builder",
                "artifact_kind": "latest_failed_action_log",
                "path": str(path),
                "name": str(resolved.get("name") or path.name),
                "sha256": _sha256_file(path),
                **_read_text_preview(path),
            }
    except Exception:
        latest_failed_action_log = {}
    status = "ok" if latest_handoff or latest_failed_action_log else "degraded"
    return {
        "status": status,
        "reason": "" if status == "ok" else "artifact_not_available",
        "latest_handoff": latest_handoff,
        "latest_failed_action_log": latest_failed_action_log,
        "actions": [
            {"action": "launch_campaign", "available": True},
            {"action": "export_handoff", "available": bool(primary_uid)},
            {"action": "copy_artifact_preview", "available": bool(latest_handoff or latest_failed_action_log)},
        ],
    }


def _continuation_payload(*, primary_uid: str) -> Dict[str, Any]:
    if not primary_uid:
        return {
            "status": "degraded",
            "reason": "no_primary_uid",
            "summary": "Select a PIS to inspect durable continuation state.",
            "handoff_status": "degraded",
            "authoritative_source": "none",
            "packet_status": "none",
            "packet_reason": "",
            "packet_id": "",
            "packet_revision": 0,
            "packet_created_utc": "",
            "packet_freshness": {},
            "memory_status": "none",
            "memory_reason": "",
            "memory_counts": {},
            "memory_records": [],
            "failed_paths": [],
            "open_questions": [],
            "next_steps": [],
            "visible_caveats": [],
            "inspection": {},
        }
    try:
        snapshot = _task_register.read_task_register_snapshot(_repo_root(), initialize_if_missing=False)
    except Exception:
        snapshot = {}
    task_register = snapshot.get("task_register") if isinstance(snapshot, dict) else None
    inspection = _continuation_attachment.inspect_continuation_state_for_binding(
        {"type": "ledger_uid", "id": primary_uid},
        task_register=task_register,
        repo_root=_repo_root(),
        as_of_utc=_utc_now_iso(),
    )
    handoff = inspection.get("handoff") if isinstance(inspection.get("handoff"), dict) else {}
    packet = inspection.get("packet") if isinstance(inspection.get("packet"), dict) else {}
    memory = inspection.get("memory") if isinstance(inspection.get("memory"), dict) else {}
    return {
        "status": str(handoff.get("status") or inspection.get("status") or "degraded"),
        "reason": str(packet.get("reason") or memory.get("reason") or ""),
        "summary": str(handoff.get("summary_text") or "Continuation state unavailable."),
        "handoff_status": str(handoff.get("status") or ""),
        "authoritative_source": str(handoff.get("authoritative_source") or ""),
        "packet_status": str(packet.get("status") or ""),
        "packet_reason": str(packet.get("reason") or ""),
        "packet_id": str(packet.get("packet_id") or ""),
        "packet_revision": int(packet.get("packet_revision") or 0),
        "packet_created_utc": str(packet.get("created_utc") or ""),
        "packet_freshness": dict(packet.get("freshness") or {}),
        "memory_status": str(memory.get("status") or ""),
        "memory_reason": str(memory.get("reason") or ""),
        "memory_counts": dict(memory.get("activity_counts") or {}),
        "memory_records": [
            {
                "memory_id": str((row if isinstance(row, dict) else {}).get("memory_id") or ""),
                "summary": str((row if isinstance(row, dict) else {}).get("summary") or ""),
                "status": str((row if isinstance(row, dict) else {}).get("status") or ""),
                "reason": str((row if isinstance(row, dict) else {}).get("reason") or ""),
                "expires_at_utc": str((row if isinstance(row, dict) else {}).get("expires_at_utc") or ""),
            }
            for row in list(memory.get("records") or [])
            if isinstance(row, dict)
        ],
        "failed_paths": [
            str((row if isinstance(row, dict) else {}).get("summary") or "")
            for row in list(packet.get("failed_paths") or [])
            if str((row if isinstance(row, dict) else {}).get("summary") or "").strip()
        ],
        "open_questions": [
            str((row if isinstance(row, dict) else {}).get("question") or "")
            for row in list(packet.get("open_questions") or [])
            if str((row if isinstance(row, dict) else {}).get("question") or "").strip()
        ],
        "next_steps": [
            str((row if isinstance(row, dict) else {}).get("step") or "")
            for row in list(packet.get("recommended_next_steps") or [])
            if str((row if isinstance(row, dict) else {}).get("step") or "").strip()
        ],
        "visible_caveats": [str(item or "") for item in list(handoff.get("visible_caveats") or []) if str(item or "").strip()],
        "inspection": inspection,
    }


def _classify_failure(*, queue_state: Dict[str, Any], workflow_state: Dict[str, Any]) -> Dict[str, Any]:
    queue_status = str(queue_state.get("status") or "").strip().lower()
    workflow_status = str(workflow_state.get("status") or "").strip().lower()
    last_error = str(queue_state.get("last_error") or workflow_state.get("last_error") or "").strip()
    if queue_status == "failed" or workflow_status == "failed":
        return {
            "status": "failed",
            "failure_class": "workflow_failure",
            "summary": last_error or "workflow failed",
            "actions": ["inspect_workflow", "launch_campaign", "refresh_projection"],
        }
    if workflow_status == "interrupted":
        return {
            "status": "blocked",
            "failure_class": "interrupted_recovery_required",
            "summary": last_error or "workflow interrupted and requires recovery",
            "actions": ["inspect_workflow", "refresh_projection"],
        }
    if queue_status == "paused":
        return {
            "status": "waiting_on_operator",
            "failure_class": "operator_paused_queue",
            "summary": "queue paused after current item",
            "actions": ["launch_campaign", "stop_queue", "refresh_projection"],
        }
    return {
        "status": "ok",
        "failure_class": "",
        "summary": "",
        "actions": ["inspect_workflow", "refresh_projection"],
    }


def _run_liveness_payload(*, queue_payload: Dict[str, Any]) -> Dict[str, Any]:
    queue_state = dict(queue_payload.get("state") or {})
    workflow_id = str(queue_state.get("current_workflow_id") or "").strip()
    workflow_status = {}
    if workflow_id:
        try:
            workflow_status = dict(_agent_orch.get_status(workflow_id=workflow_id, events_limit=80))
        except Exception:
            workflow_status = {}
    workflow_state = dict(workflow_status.get("state") or {})
    host_health = {}
    try:
        host_health = dict(_lab_host.host_health_snapshot(_repo_root()))
    except Exception:
        host_health = {"status": "unknown", "mode": "host_health_unavailable"}
    status = str(queue_state.get("status") or "").strip().lower() or "idle"
    last_tick = str(queue_state.get("last_tick_utc") or "").strip()
    last_heartbeat = str(workflow_state.get("last_heartbeat_utc") or "").strip()
    liveness = "idle"
    if status == "running":
        liveness = "running"
        if not last_heartbeat and not last_tick:
            liveness = "degraded"
    failure = _classify_failure(queue_state=queue_state, workflow_state=workflow_state)
    return {
        "status": failure["status"] if failure["status"] != "ok" else liveness,
        "queue": {
            "run_id": str(queue_state.get("run_id") or ""),
            "current_uid": str(queue_state.get("current_uid") or ""),
            "current_workflow_id": workflow_id,
            "status": status,
            "status_detail": str(queue_state.get("status_detail") or ""),
            "last_tick_utc": last_tick,
        },
        "workflow": {
            "workflow_id": workflow_id,
            "status": str(workflow_state.get("status") or ""),
            "phase": str(workflow_state.get("phase") or workflow_state.get("current_phase") or ""),
            "last_heartbeat_utc": last_heartbeat,
            "last_error": str(workflow_state.get("last_error") or ""),
            "token_usage": dict(workflow_state.get("token_usage") or {}) if isinstance(workflow_state.get("token_usage"), dict) else {},
        },
        "host_liveness": {
            "status": str(host_health.get("status") or ""),
            "mode": str(host_health.get("mode") or ""),
            "summary": str(host_health.get("summary") or ""),
        },
        "failure_case": failure,
    }


def _economics_payload(*, queue_payload: Dict[str, Any], liveness_payload: Dict[str, Any]) -> Dict[str, Any]:
    workflow = dict(liveness_payload.get("workflow") or {})
    queue = dict(liveness_payload.get("queue") or {})
    token_usage = workflow.get("token_usage") if isinstance(workflow.get("token_usage"), dict) else {}
    if not token_usage:
        token_usage = (
            (queue_payload.get("state") if isinstance(queue_payload.get("state"), dict) else {}).get("token_usage")
            if isinstance((queue_payload.get("state") if isinstance(queue_payload.get("state"), dict) else {}).get("token_usage"), dict)
            else {}
        )
    total_tokens = int((token_usage or {}).get("total_tokens") or 0)
    batch_report = dict(queue_payload.get("batch_report") or {})
    completed_count = int(batch_report.get("completed_count") or 0)
    active = str(queue.get("status") or "").strip().lower() == "running"
    missing = not bool(token_usage)
    waste_warning = ""
    status = "ok"
    if missing:
        status = "degraded"
        waste_warning = "token_usage_unavailable"
    elif active and total_tokens > 0 and completed_count <= 0:
        status = "warn"
        waste_warning = "tokens_spent_without_completed_items"
    return {
        "status": status,
        "campaign_id": str((_load_latest_campaign() or {}).get("campaign_id") or ""),
        "run_id": str(batch_report.get("run_id") or queue.get("run_id") or ""),
        "token_usage": {
            "input_tokens": int((token_usage or {}).get("input_tokens") or 0),
            "output_tokens": int((token_usage or {}).get("output_tokens") or 0),
            "total_tokens": total_tokens,
        },
        "signal_count": completed_count,
        "cost_per_signal_heuristic": float(total_tokens / completed_count) if total_tokens > 0 and completed_count > 0 else None,
        "waste_warning": waste_warning,
    }


def _run_archive_rows() -> List[Dict[str, Any]]:
    root = _runs_archives_dir(_repo_root())
    out: List[Dict[str, Any]] = []
    try:
        for path in root.glob("*.zip"):
            if not path.is_file() or path.is_symlink():
                continue
            st = path.stat()
            out.append({"name": path.name, "bytes": int(st.st_size), "mtime": float(st.st_mtime)})
    except Exception:
        return []
    out.sort(key=lambda row: float(row.get("mtime") or 0.0), reverse=True)
    return out[:12]


def _archive_snapshot_payload() -> Dict[str, Any]:
    repo_archives = list(_admin_list_archives(_repo_root()) or [])[:12]
    run_archives = _run_archive_rows()
    latest_repo = repo_archives[0] if repo_archives else {}
    return {
        "status": "ok" if repo_archives or run_archives else "degraded",
        "repo_archives": repo_archives,
        "run_archives": run_archives,
        "latest_repo_snapshot": {
            "snapshot_id": str(latest_repo.get("name") or ""),
            "bytes": int(latest_repo.get("bytes") or 0),
            "mtime": float(latest_repo.get("mtime") or 0.0),
            "download_path": f"/api/admin/archive/download/{latest_repo.get('name')}" if latest_repo else "",
        },
        "actions": [
            {"action": "create_archive_snapshot", "available": True},
            {"action": "download_latest_repo_snapshot", "available": bool(latest_repo)},
        ],
    }


def _chronicle_payload(*, primary_uid: str, latest_campaign: Dict[str, Any], queue_payload: Dict[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    campaign_id = str(latest_campaign.get("campaign_id") or "").strip()
    if campaign_id:
        rows.append(
            {
                "entry_id": f"campaign:{campaign_id}",
                "utc": str(latest_campaign.get("updated_utc") or latest_campaign.get("created_utc") or ""),
                "kind": "campaign",
                "summary": f"Campaign {campaign_id} {str(latest_campaign.get('launch_outcome') or 'recorded')}",
                "source": "mobile_cockpit_campaign",
                "cross_links": {"campaign_id": campaign_id, "run_id": str(latest_campaign.get("queue_run_id") or "")},
            }
        )
    queue_events = _read_jsonl((_repo_root() / "governance" / "agents" / "runtime" / "execution_queue" / "events.jsonl").resolve(), limit=40)
    for row in queue_events[-20:]:
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        rows.append(
            {
                "entry_id": f"queue:{str(row.get('utc') or '')}:{str(row.get('event') or '')}",
                "utc": str(row.get("utc") or ""),
                "kind": "run",
                "summary": str(row.get("event") or ""),
                "source": "execution_queue",
                "cross_links": {
                    "run_id": str((queue_payload.get("state") if isinstance(queue_payload.get("state"), dict) else {}).get("run_id") or ""),
                    "workflow_id": str(payload.get("workflow_id") or ""),
                    "uid": str(payload.get("uid") or ""),
                },
            }
        )
    if primary_uid:
        try:
            ledger = ChangeLedger(repo_root=_repo_root())
            item = ledger.read_item(primary_uid)
            item_dir = (_repo_root() / "governance" / "ledger" / "items").resolve()
            matched = list(item_dir.glob(f"**/{primary_uid}__*/events.jsonl"))
            events_path = matched[0] if matched else None
            if events_path:
                for row in _read_jsonl(events_path, limit=20):
                    rows.append(
                        {
                            "entry_id": f"ledger:{primary_uid}:{str(row.get('utc') or '')}:{str(row.get('event_type') or '')}",
                            "utc": str(row.get("utc") or ""),
                            "kind": "artifact" if "handoff" in str(row.get("event_type") or "") else "pis",
                            "summary": str(row.get("event_type") or ""),
                            "source": "ledger_event_chain",
                            "cross_links": {"uid": primary_uid},
                        }
                    )
        except Exception:
            pass
    archive_snapshot = _archive_snapshot_payload()
    latest_repo = dict(archive_snapshot.get("latest_repo_snapshot") or {})
    if latest_repo.get("snapshot_id"):
        rows.append(
            {
                "entry_id": f"archive:{str(latest_repo.get('snapshot_id') or '')}",
                "utc": "",
                "kind": "archive_snapshot",
                "summary": f"Repo snapshot {str(latest_repo.get('snapshot_id') or '')}",
                "source": "admin_archive_index",
                "cross_links": {"snapshot_id": str(latest_repo.get("snapshot_id") or "")},
            }
        )
    rows.sort(key=lambda row: (str(row.get("utc") or ""), str(row.get("entry_id") or "")), reverse=True)
    return {
        "status": "ok" if rows else "degraded",
        "entries": rows[:30],
    }


def _shell_payload(
    *,
    contracts: Dict[str, Any],
    artifacts: Dict[str, Any],
    continuation: Dict[str, Any],
    run_triage: Dict[str, Any],
    economics: Dict[str, Any],
    archive_snapshots: Dict[str, Any],
    latest_campaign: Dict[str, Any],
) -> Dict[str, Any]:
    warnings: List[str] = []
    fallback_actions: List[str] = []
    for label, payload, action in [
        ("contracts", contracts.get("contract_check") if isinstance(contracts.get("contract_check"), dict) else {}, "refresh_projection"),
        ("artifacts", artifacts, "export_handoff"),
        ("continuation", continuation, "refresh_projection"),
        ("run_triage", run_triage, "inspect_workflow"),
        ("economics", economics, "refresh_projection"),
        ("archive_snapshots", archive_snapshots, "create_archive_snapshot"),
    ]:
        status = str((payload or {}).get("status") or ("ok" if label == "contracts" and bool((payload or {}).get("ok")) else "")).strip().lower()
        if label == "contracts":
            status = "ok" if bool((payload or {}).get("ok")) else "degraded"
        if status in {"degraded", "failed", "blocked", "warn", "waiting_on_operator"}:
            warnings.append(f"{label}:{status}")
            if action not in fallback_actions:
                fallback_actions.append(action)
    queue_run_id = str(latest_campaign.get("queue_run_id") or "")
    return {
        "status": "low_trust" if warnings else "ok",
        "warnings": warnings,
        "executor_identity": {
            "campaign_id": str(latest_campaign.get("campaign_id") or ""),
            "queue_run_id": queue_run_id,
            "workflow_id": str(((run_triage.get("workflow") if isinstance(run_triage.get("workflow"), dict) else {}) or {}).get("workflow_id") or ""),
        },
        "fallback_actions": fallback_actions or ["refresh_projection"],
        "state_language": list(_SHARED_STATUS_LANGUAGE),
        "action_language": list(_SHARED_ACTION_LANGUAGE),
    }


def cockpit_payload(
    *,
    limit: int = 250,
    topic_filter: str = "",
    group_mode: str = "status",
    selected_uids: List[str] | None = None,
) -> Dict[str, Any]:
    backlog = backlog_projection(limit=limit, topic_filter=topic_filter, group_mode=group_mode)
    items = list(backlog.get("items") or [])
    available_uids = {str(item.get("uid") or "").strip() for item in items if str(item.get("uid") or "").strip()}
    normalized_selected = [uid for uid in _normalize_uid_list(selected_uids or []) if uid in available_uids]
    latest_campaign = _load_latest_campaign()
    queue_payload = _execution_queue.status()
    primary_uid = normalized_selected[0] if normalized_selected else ""
    contracts_payload = shared_contract_payload()
    artifact_payload = _artifact_payload(primary_uid=primary_uid)
    continuation_payload = _continuation_payload(primary_uid=primary_uid)
    liveness_payload = _run_liveness_payload(queue_payload=queue_payload)
    economics_payload = _economics_payload(queue_payload=queue_payload, liveness_payload=liveness_payload)
    archive_payload = _archive_snapshot_payload()
    chronicle_payload = _chronicle_payload(primary_uid=primary_uid, latest_campaign=latest_campaign, queue_payload=queue_payload)
    campaign_summary_payload = _campaign_summary_payload(latest_campaign=latest_campaign)
    shell_payload = _shell_payload(
        contracts=contracts_payload,
        artifacts=artifact_payload,
        continuation=continuation_payload,
        run_triage=liveness_payload,
        economics=economics_payload,
        archive_snapshots=archive_payload,
        latest_campaign=latest_campaign,
    )
    return {
        "schema_version": SCHEMA_COCKPIT,
        "utc": _utc_now_iso(),
        "contracts": contracts_payload,
        "selection": {
            "selected_uids": normalized_selected,
            "primary_uid": normalized_selected[0] if normalized_selected else "",
            "selected_count": len(normalized_selected),
        },
        "pis": backlog,
        "campaigns": {
            "latest": latest_campaign,
            "summary": campaign_summary_payload,
        },
        "execution_queue": {
            "state": dict(queue_payload.get("state") or {}),
            "queue_preview": list(queue_payload.get("queue_preview") or []),
            "batch_report": dict(queue_payload.get("batch_report") or {}),
            "worker_alive": bool(queue_payload.get("worker_alive")),
        },
        "artifacts": artifact_payload,
        "continuation": continuation_payload,
        "run_triage": liveness_payload,
        "economics": economics_payload,
        "archive_snapshots": archive_payload,
        "chronicle": chronicle_payload,
        "shell": shell_payload,
    }


def launch_campaign(
    *,
    selected_uids: List[str],
    dependency_mode: str = "selected_order",
    phase_label: str = "",
    workflow_options: Dict[str, Any] | None = None,
    commit_each_success: bool = True,
    origin_surface: str = "mobile_backlog",
) -> Dict[str, Any]:
    normalized_uids = _normalize_uid_list(selected_uids)
    if not normalized_uids:
        raise ValueError("selected_uids must include at least one PIS uid")
    safe_dependency_mode = str(dependency_mode or "selected_order").strip().lower() or "selected_order"
    if safe_dependency_mode not in {"selected_order", "priority_order"}:
        raise ValueError("dependency_mode must be selected_order or priority_order")
    campaign_id = f"mcamp-{_utc_compact()}"
    created_utc = _utc_now_iso()
    campaign = {
        "schema_version": SCHEMA_CAMPAIGN,
        "campaign_id": campaign_id,
        "created_utc": created_utc,
        "updated_utc": created_utc,
        "origin_surface": str(origin_surface or "mobile_backlog"),
        "selected_uids": list(normalized_uids),
        "member_count": len(normalized_uids),
        "dependency_mode": safe_dependency_mode,
        "phase_label": str(phase_label or "").strip(),
        "launch_outcome": "pending",
        "queue_run_id": "",
        "queue_status": "",
        "error": "",
    }
    try:
        queue_payload = _execution_queue.start_queue(
            options={
                "phase_label": str(phase_label or ""),
                "workflow_options": dict(workflow_options or {}),
                "commit_each_success": bool(commit_each_success),
                "selected_uids": list(normalized_uids),
                "selection_mode": safe_dependency_mode,
            }
        )
        state = dict(queue_payload.get("state") or {})
        campaign["queue_run_id"] = str(state.get("run_id") or "")
        campaign["queue_status"] = str(state.get("status") or "")
        campaign["launch_outcome"] = "started" if bool(state.get("active")) else str(queue_payload.get("noop_reason") or "accepted")
        campaign["queue_preview"] = list(queue_payload.get("queue_preview") or [])
        campaign["batch_report"] = dict(queue_payload.get("batch_report") or {})
    except Exception as exc:
        campaign["launch_outcome"] = "rejected"
        campaign["queue_status"] = "failed_to_start"
        campaign["error"] = str(exc)
    campaign["updated_utc"] = _utc_now_iso()
    _write_json_atomic(_campaign_path(campaign_id), campaign)
    _write_json_atomic(_latest_campaign_path(), {"campaign_id": campaign_id, "updated_utc": campaign["updated_utc"]})
    return {
        "schema_version": SCHEMA_CAMPAIGN,
        "utc": _utc_now_iso(),
        "campaign": campaign,
    }
