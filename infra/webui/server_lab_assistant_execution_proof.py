"""G7B1 delegated execution proof, audit, and self-check layer.

This module derives proof from existing durable delegated-execution sources. It
does not replace canonical request, lifecycle, correlation, writeback, or task
truth. Proof artifacts are receipts about that truth.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from infra.webui import server_lab_assistant_correlation as _correlation
from infra.webui import server_lab_assistant_delegation_bindings as _bindings
from infra.webui import server_lab_assistant_job_lifecycle as _jobs
from infra.webui import server_lab_assistant_mailbox as _mailbox
from infra.webui import server_lab_assistant_task_register as _register
from infra.webui import server_lab_assistant_writeback as _writeback


PROOF_REPORT_SCHEMA_VERSION = "lab_assistant_delegated_execution_proof_report_v1"
PROOF_RECEIPT_SCHEMA_VERSION = "lab_assistant_delegated_execution_proof_receipt_v1"
PROOF_EVENT_SCHEMA_VERSION = "lab_assistant_delegated_execution_proof_event_v1"
DEFAULT_PROOF_MAX_AGE_S = 1800
_LOCK = threading.RLock()


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(value: Optional[_dt.datetime] = None) -> str:
    when = value or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    except Exception:
        return None


def _age_s(*, now: _dt.datetime, value: Any) -> Optional[int]:
    parsed = _parse_utc(value)
    if parsed is None:
        return None
    return max(0, int((now - parsed).total_seconds()))


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _safe_name(value: Any) -> str:
    text = _clean_text(value)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-")
    return safe or "x"


def _proof_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "delegation_proof").resolve()


def _reports_dir(repo_root: Path) -> Path:
    return (_proof_root(repo_root) / "reports").resolve()


def _receipts_dir(repo_root: Path) -> Path:
    return (_proof_root(repo_root) / "receipts").resolve()


def _events_path(repo_root: Path) -> Path:
    return (_proof_root(repo_root) / "proof_events.jsonl").resolve()


def _latest_report_path(repo_root: Path) -> Path:
    return (_proof_root(repo_root) / "latest_report.json").resolve()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    _ensure_parent(path)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = path.parent / f".{path.name}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    _ensure_parent(path)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _source_path_from_snapshot_rows(rows: List[Dict[str, Any]], key: str) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in rows:
        row_key = _clean_text(row.get(key))
        row_path = _clean_text(row.get("path"))
        if row_key and row_path:
            out[row_key] = row_path
    return out


def _status_rank(value: str) -> int:
    order = {"pass": 0, "warn": 1, "stale": 2, "fail": 3}
    return order.get(_clean_text(value).lower(), 3)


def _combine_status(values: List[str]) -> str:
    if not values:
        return "warn"
    worst = max(values, key=_status_rank)
    return worst


def _check(check_id: str, status: str, summary: str, *, sources: List[str], details: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "check_id": _clean_text(check_id),
        "status": _clean_text(status).lower(),
        "summary": _clean_text(summary),
        "source_paths": [row for row in [_clean_text(item) for item in sources] if row],
        "details": dict(details or {}),
    }


def _route_decision_for_trace(trace: Dict[str, Any]) -> Dict[str, Any]:
    for candidate in (trace.get("mailbox_request") or {}, trace.get("delegation_ref") or {}):
        payload = candidate.get("request") if "request" in candidate else candidate
        raw_input = payload.get("input_payload") if isinstance(payload.get("input_payload"), dict) else {}
        decision = raw_input.get("routing_decision") if isinstance(raw_input.get("routing_decision"), dict) else {}
        if decision:
            return dict(decision)
        if isinstance(candidate.get("routing_decision"), dict) and candidate.get("routing_decision"):
            return dict(candidate.get("routing_decision"))
    return {}


def _trace_rows(repo_root: Path) -> Tuple[List[Dict[str, Any]], Dict[str, str]]:
    register_snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    task_register = dict(register_snapshot.get("task_register") or {})
    mailbox_snapshot = _mailbox.read_mailbox_snapshot(repo_root, initialize_if_missing=False)
    job_snapshot = _jobs.read_snapshot(repo_root, initialize_if_missing=False)
    correlation_snapshot = _correlation.read_snapshot(repo_root, initialize_if_missing=False)
    operator_snapshot = {}
    from infra.webui import server_lab_assistant_operator_snapshot as _operator_snapshot

    operator_snapshot = _operator_snapshot.build_operator_snapshot(repo_root)

    mailbox_by_request = {
        _clean_text(row.get("request_id")): dict(row) for row in list(mailbox_snapshot.get("requests") or []) if _clean_text(row.get("request_id"))
    }
    job_by_id = {
        _clean_text(row.get("detached_job_id")): dict(row) for row in list(job_snapshot.get("jobs") or []) if _clean_text(row.get("detached_job_id"))
    }
    record_paths = _source_path_from_snapshot_rows(list(correlation_snapshot.get("records") or []), "correlation_id")
    result_paths = _source_path_from_snapshot_rows(list(correlation_snapshot.get("results") or []), "result_id")
    corr_by_id = {
        _clean_text(row.get("correlation_id")): dict(row) for row in list(correlation_snapshot.get("records") or []) if _clean_text(row.get("correlation_id"))
    }
    latest_result_by_corr: Dict[str, Dict[str, Any]] = {}
    for row in list(correlation_snapshot.get("results") or []):
        attached = _clean_text(row.get("attached_correlation_id"))
        if attached:
            latest_result_by_corr[attached] = dict(row)
    delegated_work_by_request = {
        _clean_text(row.get("delegation_request_id")): dict(row)
        for row in list(operator_snapshot.get("delegated_work") or [])
        if _clean_text(row.get("delegation_request_id"))
    }
    task_projection_by_id = {
        _clean_text(row.get("task_id")): dict(row)
        for row in (
            list(operator_snapshot.get("active_tasks") or [])
            + list(operator_snapshot.get("completed_tasks") or [])
            + list(operator_snapshot.get("residue_tasks") or [])
        )
        if _clean_text(row.get("task_id"))
    }
    traces: List[Dict[str, Any]] = []
    task_rows = (
        list(task_register.get("active_tasks") or [])
        + list(task_register.get("completed_tasks") or [])
        + list(task_register.get("residue_tasks") or [])
    )
    for task in task_rows:
        task_row = dict(task)
        task_id = _clean_text(task_row.get("task_id"))
        writeback_record = _writeback.read_task_record(repo_root, task_id=task_id) if task_id else {}
        for ref in list(task_row.get("delegation_refs") or []):
            delegation_ref = dict(ref)
            correlation_id = _clean_text(delegation_ref.get("correlation_id"))
            request_id = _clean_text(delegation_ref.get("delegation_request_id"))
            corr = dict(corr_by_id.get(correlation_id) or {})
            mailbox_record = dict(mailbox_by_request.get(request_id) or {})
            detached_job_id = _clean_text(corr.get("detached_job_id") or delegation_ref.get("detached_job_id"))
            latest_result = dict(latest_result_by_corr.get(correlation_id) or {})
            traces.append(
                {
                    "task_id": task_id,
                    "ledger_uid": _clean_text(task_row.get("ledger_uid")),
                    "task": task_row,
                    "task_projection": dict(task_projection_by_id.get(task_id) or {}),
                    "delegation_ref": delegation_ref,
                    "mailbox_request": mailbox_record,
                    "correlation_record": corr,
                    "job_record": dict(job_by_id.get(detached_job_id) or {}),
                    "latest_result": latest_result,
                    "writeback_record": dict(writeback_record),
                    "operator_delegated_work": dict(delegated_work_by_request.get(request_id) or {}),
                }
            )
    source_paths = {
        "task_register": _clean_text(register_snapshot.get("task_register_path")),
        "mailbox_snapshot": _clean_text(mailbox_snapshot.get("snapshot_path") or _mailbox.snapshot_path(repo_root)),
        "job_snapshot": _clean_text(job_snapshot.get("events_path")).replace("job_events.jsonl", "job_snapshot.json")
        if _clean_text(job_snapshot.get("events_path"))
        else str(_jobs.snapshot_path(repo_root)),
        "correlation_snapshot": str(_correlation.snapshot_path(repo_root)),
        "writeback_snapshot": str(_writeback._snapshot_path(repo_root)),  # type: ignore[attr-defined]
        "operator_snapshot": "",
        "correlation_record_paths": record_paths,
        "correlation_result_paths": result_paths,
    }
    return traces, source_paths


def _trace_stage_status(trace: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    ref = dict(trace.get("delegation_ref") or {})
    corr = dict(trace.get("correlation_record") or {})
    mailbox_record = dict(trace.get("mailbox_request") or {})
    job = dict(trace.get("job_record") or {})
    result = dict(trace.get("latest_result") or {})
    writeback_record = dict(trace.get("writeback_record") or {})
    route_decision = _route_decision_for_trace(trace)
    job_surface = _clean_text(job.get("job_surface") or corr.get("job_surface"))
    selected_surface = _clean_text(route_decision.get("selected_surface") or ref.get("surface"))
    route_target = job_surface or _clean_text(ref.get("surface"))
    route_status = "pass" if route_decision and selected_surface == route_target else ("warn" if not route_decision else "fail")
    launch_status = (
        "pass"
        if corr and _clean_text(corr.get("request_id")) == _clean_text(ref.get("delegation_request_id")) and _clean_text(corr.get("launch_turn_id")) == _clean_text(ref.get("launch_turn_id"))
        else "fail"
    )
    claim_status = "pass"
    if _clean_text(ref.get("surface")) == "mailbox":
        state = _clean_text(mailbox_record.get("state"))
        if not mailbox_record:
            claim_status = "fail"
        elif state in {"claimed", "handed_off"}:
            claim_status = "pass" if _clean_text(((mailbox_record.get("active_claim") or {}) if isinstance(mailbox_record.get("active_claim"), dict) else {}).get("claim_id")) else "fail"
        elif state in {"queued", "replayed"}:
            claim_status = "warn"
        else:
            claim_status = "warn"
    execution_status = "pass"
    if job_surface or _clean_text(ref.get("surface")) == "mailbox":
        if not job:
            execution_status = "fail" if _clean_text(mailbox_record.get("state")) == "handed_off" else "warn"
        else:
            execution_status = "pass" if _clean_text(job.get("state")) else "warn"
    result_status = "warn"
    if result:
        result_status = "pass" if _clean_text(result.get("delivery_status")) in {"pending_reattach", "delivered", "ambiguous", "unresolved"} else "fail"
    elif _clean_text(((job.get("terminal_result") or {}) if isinstance(job.get("terminal_result"), dict) else {}).get("result_id")):
        result_status = "fail"
    writeback_status = "warn"
    if writeback_record:
        marker = dict((trace.get("task") or {}).get("delegated_outcome_writeback") or {})
        writeback_status = (
            "pass"
            if marker
            and _clean_text(marker.get("assimilation_status")) == _clean_text(writeback_record.get("assimilation_status"))
            and _clean_text(marker.get("decision_reason")) == _clean_text(writeback_record.get("decision_reason"))
            and _clean_text(writeback_record.get("task_status_after")) == _clean_text((trace.get("task") or {}).get("status"))
            else "fail"
        )
    operator_status = "warn"
    operator_row = dict(trace.get("operator_delegated_work") or {})
    task_projection = dict(trace.get("task_projection") or {})
    if operator_row and task_projection:
        operator_status = (
            "pass"
            if _clean_text(operator_row.get("status")) == _clean_text(task_projection.get("status"))
            and _clean_text(operator_row.get("delegation_request_id")) == _clean_text(ref.get("delegation_request_id"))
            else "fail"
        )
    return {
        "launch": {"status": launch_status, "summary": "delegation binding matches durable correlation record"},
        "route_selection": {"status": route_status, "summary": "durable route breadcrumb matches actual execution surface"},
        "claim": {"status": claim_status, "summary": "mailbox claim/handoff truth is internally coherent"},
        "execution": {"status": execution_status, "summary": "execution surface has durable runtime state"},
        "result_delivery": {"status": result_status, "summary": "result delivery is backed by the correlation result store"},
        "writeback": {"status": writeback_status, "summary": "canonical task writeback matches task marker truth"},
        "operator_surface": {"status": operator_status, "summary": "operator-visible delegated status matches durable synthesis"},
    }


def _self_checks(repo_root: Path, traces: List[Dict[str, Any]], *, now: _dt.datetime) -> List[Dict[str, Any]]:
    checks: List[Dict[str, Any]] = []
    source_paths: List[str] = [
        str(_mailbox.snapshot_path(repo_root)),
        str(_jobs.snapshot_path(repo_root)),
        str(_correlation.snapshot_path(repo_root)),
        str(_writeback._snapshot_path(repo_root)),  # type: ignore[attr-defined]
    ]

    launch_failures = [
        {
            "task_id": trace["task_id"],
            "request_id": _clean_text((trace.get("delegation_ref") or {}).get("delegation_request_id")),
            "correlation_id": _clean_text((trace.get("delegation_ref") or {}).get("correlation_id")),
        }
        for trace in traces
        if _trace_stage_status(trace)["launch"]["status"] == "fail"
    ]
    checks.append(
        _check(
            "request_launch_and_correlation",
            "fail" if launch_failures else "pass",
            "Delegated launch proof is present for every bound delegation ref." if not launch_failures else "Some delegation refs are missing matching durable correlation launch proof.",
            sources=source_paths,
            details={"failures": launch_failures, "trace_count": len(traces)},
        )
    )

    route_failures: List[Dict[str, Any]] = []
    route_gaps: List[Dict[str, Any]] = []
    for trace in traces:
        ref = dict(trace.get("delegation_ref") or {})
        decision = _route_decision_for_trace(trace)
        status = _trace_stage_status(trace)["route_selection"]["status"]
        target = _clean_text((trace.get("job_record") or {}).get("job_surface") or (trace.get("correlation_record") or {}).get("job_surface") or ref.get("surface"))
        entry = {
            "task_id": trace["task_id"],
            "request_id": _clean_text(ref.get("delegation_request_id")),
            "selected_surface": _clean_text(decision.get("selected_surface")),
            "actual_surface": target,
        }
        if status == "fail":
            route_failures.append(entry)
        elif status == "warn":
            route_gaps.append(entry)
    route_status = "fail" if route_failures else ("warn" if route_gaps else "pass")
    route_summary = (
        "Route-selection proof is durably persisted for every traced delegation."
        if route_status == "pass"
        else ("Some route selections are durably persisted but others are missing explicit route breadcrumbs." if route_status == "warn" else "Some persisted route decisions disagree with the actual execution surface.")
    )
    checks.append(
        _check(
            "route_selection_audit",
            route_status,
            route_summary,
            sources=source_paths,
            details={"missing_route_breadcrumbs": route_gaps, "mismatches": route_failures},
        )
    )

    duplicate_map: Dict[str, List[str]] = {}
    mailbox_snapshot = _mailbox.read_mailbox_snapshot(repo_root, initialize_if_missing=False)
    for row in list(mailbox_snapshot.get("requests") or []):
        request_id = _clean_text(row.get("request_id"))
        for key in list(row.get("idempotency_keys") or []):
            text = _clean_text(key)
            if not text:
                continue
            current = duplicate_map.setdefault(text, [])
            if request_id and request_id not in current:
                current.append(request_id)
    duplicate_failures = {key: value for key, value in duplicate_map.items() if len(value) > 1}
    checks.append(
        _check(
            "duplicate_suppression_self_check",
            "fail" if duplicate_failures else "pass",
            "Mailbox duplicate suppression still collapses each durable idempotency key to one request." if not duplicate_failures else "At least one idempotency key points at multiple mailbox requests.",
            sources=[str(_mailbox.snapshot_path(repo_root))],
            details={"duplicate_keys": duplicate_failures},
        )
    )

    restart_failures: List[Dict[str, Any]] = []
    for trace in traces:
        job = dict(trace.get("job_record") or {})
        operator = dict(trace.get("operator_delegated_work") or {})
        if _clean_text(job.get("state")) not in {"heartbeat_stale", "interrupted", "timed_out"}:
            continue
        if _clean_text(operator.get("status")) == "completed":
            restart_failures.append(
                {
                    "task_id": trace["task_id"],
                    "request_id": _clean_text((trace.get("delegation_ref") or {}).get("delegation_request_id")),
                    "job_state": _clean_text(job.get("state")),
                    "operator_status": _clean_text(operator.get("status")),
                }
            )
    checks.append(
        _check(
            "restart_truth_and_reconciliation",
            "fail" if restart_failures else "pass",
            "Restart/reconciliation truth does not claim completion for stale or interrupted jobs." if not restart_failures else "Operator-visible truth still claims completion for interrupted or stale detached jobs.",
            sources=source_paths,
            details={"failures": restart_failures},
        )
    )

    writeback_failures = [
        {
            "task_id": trace["task_id"],
            "request_id": _clean_text((trace.get("delegation_ref") or {}).get("delegation_request_id")),
            "assimilation_status": _clean_text((trace.get("writeback_record") or {}).get("assimilation_status")),
        }
        for trace in traces
        if _trace_stage_status(trace)["writeback"]["status"] == "fail"
    ]
    checks.append(
        _check(
            "writeback_integrity",
            "fail" if writeback_failures else "pass",
            "Writeback records, task markers, and canonical task status remain aligned." if not writeback_failures else "Some writeback records disagree with canonical task markers or task status.",
            sources=source_paths,
            details={"failures": writeback_failures},
        )
    )

    operator_failures = [
        {
            "task_id": trace["task_id"],
            "request_id": _clean_text((trace.get("delegation_ref") or {}).get("delegation_request_id")),
            "operator_status": _clean_text((trace.get("operator_delegated_work") or {}).get("status")),
            "task_status": _clean_text((trace.get("task_projection") or {}).get("status")),
        }
        for trace in traces
        if _trace_stage_status(trace)["operator_surface"]["status"] == "fail"
    ]
    checks.append(
        _check(
            "operator_surface_truth",
            "fail" if operator_failures else "pass",
            "Operator-visible delegated truth matches durable per-task synthesis." if not operator_failures else "The operator snapshot disagrees with durable delegated synthesis for at least one request.",
            sources=source_paths,
            details={"failures": operator_failures},
        )
    )

    return checks


def _proof_summary(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    counts = {"pass": 0, "warn": 0, "stale": 0, "fail": 0}
    for row in checks:
        status = _clean_text(row.get("status")).lower()
        counts[status] = counts.get(status, 0) + 1
    overall = "ok"
    if counts["fail"]:
        overall = "failed"
    elif counts["stale"]:
        overall = "stale"
    elif counts["warn"]:
        overall = "proof_gap"
    return {
        "overall_status": overall,
        "counts": counts,
    }


def build_execution_proof_report(
    repo_root: Path,
    *,
    writer: str,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    now = _parse_utc(now_utc) or _utc_now()
    traces, source_paths = _trace_rows(repo_root)
    trace_rows: List[Dict[str, Any]] = []
    for trace in traces:
        stages = _trace_stage_status(trace)
        request_id = _clean_text((trace.get("delegation_ref") or {}).get("delegation_request_id"))
        correlation_id = _clean_text((trace.get("delegation_ref") or {}).get("correlation_id"))
        task_id = _clean_text(trace.get("task_id"))
        route_decision = _route_decision_for_trace(trace)
        trace_rows.append(
            {
                "task_id": task_id,
                "ledger_uid": _clean_text(trace.get("ledger_uid")),
                "request_id": request_id,
                "correlation_id": correlation_id,
                "route_decision_id": _clean_text(route_decision.get("decision_id")),
                "selected_surface": _clean_text(route_decision.get("selected_surface")),
                "mailbox_state": _clean_text((trace.get("mailbox_request") or {}).get("state")),
                "job_id": _clean_text((trace.get("job_record") or {}).get("detached_job_id")),
                "job_state": _clean_text((trace.get("job_record") or {}).get("state")),
                "result_id": _clean_text((trace.get("latest_result") or {}).get("result_id")),
                "result_delivery_status": _clean_text((trace.get("latest_result") or {}).get("delivery_status")),
                "writeback_status": _clean_text((trace.get("writeback_record") or {}).get("assimilation_status")),
                "operator_status": _clean_text((trace.get("operator_delegated_work") or {}).get("status")),
                "stage_statuses": stages,
                "overall_stage_status": _combine_status([row["status"] for row in stages.values()]),
                "source_paths": [
                    _clean_text(source_paths.get("task_register")),
                    _clean_text(source_paths.get("correlation_record_paths", {}).get(correlation_id)),
                    _clean_text(source_paths.get("correlation_result_paths", {}).get(_clean_text((trace.get("latest_result") or {}).get("result_id")))),
                ],
            }
        )
    checks = _self_checks(repo_root, traces, now=now)
    summary = _proof_summary(checks)
    proof_run_id = f"proof_{now.strftime('%Y%m%dt%H%M%S').lower()}_{uuid.uuid4().hex[:8]}"
    return {
        "schema_version": PROOF_REPORT_SCHEMA_VERSION,
        "proof_run_id": proof_run_id,
        "writer": _clean_text(writer),
        "generated_utc": _utc_iso(now),
        "proof_max_age_s": DEFAULT_PROOF_MAX_AGE_S,
        "summary": {
            **summary,
            "trace_count": len(trace_rows),
        },
        "checks": checks,
        "traces": trace_rows,
        "source_paths": {
            **{key: value for key, value in source_paths.items() if key not in {"correlation_record_paths", "correlation_result_paths"}},
            "record_count": len(source_paths.get("correlation_record_paths", {})),
            "result_count": len(source_paths.get("correlation_result_paths", {})),
        },
        "report_paths": {
            "latest_report_path": str(_latest_report_path(repo_root)),
            "reports_dir": str(_reports_dir(repo_root)),
            "receipts_dir": str(_receipts_dir(repo_root)),
            "events_path": str(_events_path(repo_root)),
        },
    }


def record_execution_proof_report(
    repo_root: Path,
    *,
    writer: str,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        report = build_execution_proof_report(repo_root, writer=writer, now_utc=now_utc)
        report_path = (_reports_dir(repo_root) / f"{_safe_name(report['proof_run_id'])}.json").resolve()
        receipt_path = (_receipts_dir(repo_root) / f"{_safe_name(report['proof_run_id'])}.json").resolve()
        receipt = {
            "schema_version": PROOF_RECEIPT_SCHEMA_VERSION,
            "proof_run_id": report["proof_run_id"],
            "writer": _clean_text(writer),
            "generated_utc": _clean_text(report.get("generated_utc")),
            "overall_status": _clean_text(((report.get("summary") or {}) if isinstance(report.get("summary"), dict) else {}).get("overall_status")),
            "trace_count": int(((report.get("summary") or {}) if isinstance(report.get("summary"), dict) else {}).get("trace_count") or 0),
            "check_counts": dict(((report.get("summary") or {}) if isinstance(report.get("summary"), dict) else {}).get("counts") or {}),
            "report_path": str(report_path),
        }
        report["report_paths"]["report_path"] = str(report_path)
        report["report_paths"]["receipt_path"] = str(receipt_path)
        _write_json_atomic(report_path, report)
        _write_json_atomic(_latest_report_path(repo_root), report)
        _write_json_atomic(receipt_path, receipt)
        _append_jsonl(
            _events_path(repo_root),
            {
                "schema_version": PROOF_EVENT_SCHEMA_VERSION,
                "event_id": uuid.uuid4().hex,
                "event": "proof_report_recorded",
                "utc": _clean_text(report.get("generated_utc")),
                "payload": {
                    "proof_run_id": report["proof_run_id"],
                    "writer": _clean_text(writer),
                    "overall_status": _clean_text(receipt.get("overall_status")),
                    "report_path": str(report_path),
                    "receipt_path": str(receipt_path),
                },
            },
        )
        return report


def _missing_report_payload(repo_root: Path, *, now: _dt.datetime, max_age_s: int) -> Dict[str, Any]:
    return {
        "schema_version": PROOF_REPORT_SCHEMA_VERSION,
        "proof_run_id": "",
        "writer": "",
        "generated_utc": "",
        "proof_max_age_s": int(max_age_s),
        "summary": {
            "overall_status": "missing",
            "counts": {"pass": 0, "warn": 0, "stale": 1, "fail": 0},
            "trace_count": 0,
        },
        "checks": [],
        "traces": [],
        "source_paths": {},
        "report_paths": {
            "latest_report_path": str(_latest_report_path(repo_root)),
            "reports_dir": str(_reports_dir(repo_root)),
            "receipts_dir": str(_receipts_dir(repo_root)),
            "events_path": str(_events_path(repo_root)),
        },
        "freshness": {
            "stale": True,
            "age_s": None,
            "max_age_s": int(max_age_s),
            "reason": "missing_proof_report",
        },
        "summary_note": "No durable delegated execution proof report has been recorded yet.",
    }


def read_latest_report(
    repo_root: Path,
    *,
    now_utc: str = "",
    max_age_s: int = DEFAULT_PROOF_MAX_AGE_S,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    now = _parse_utc(now_utc) or _utc_now()
    report = _read_json(_latest_report_path(repo_root))
    if not report:
        return _missing_report_payload(repo_root, now=now, max_age_s=max_age_s)
    generated_utc = _clean_text(report.get("generated_utc"))
    age = _age_s(now=now, value=generated_utc)
    stale = age is None or age > int(max_age_s)
    out = dict(report)
    out["proof_max_age_s"] = int(max_age_s)
    out["freshness"] = {
        "stale": stale,
        "age_s": age,
        "max_age_s": int(max_age_s),
        "reason": "stale_proof_report" if stale else "",
    }
    if stale:
        summary = dict(out.get("summary") or {})
        if _clean_text(summary.get("overall_status")) == "ok":
            summary["overall_status"] = "stale"
        out["summary"] = summary
    return out
