"""Y5Z7 cross-substrate reconciliation and durable reporting."""

from __future__ import annotations

import datetime as _dt
import json
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.webui import server_lab_assistant_execution_truth_ownership as _truth_ownership


RECONCILIATION_REPORT_SCHEMA_VERSION = "lab_assistant_execution_reconciliation_report_v1"
RECONCILIATION_RECEIPT_SCHEMA_VERSION = "lab_assistant_execution_reconciliation_receipt_v1"
RECONCILIATION_EVENT_SCHEMA_VERSION = "lab_assistant_execution_reconciliation_event_v1"
_LOCK = threading.RLock()

_SUCCESS = {"completed"}
_FAILURE = {"failed"}
_INTERRUPTED = {"interrupted", "timed_out", "cancelled"}
_BLOCKED = {"evidence_missing"}
_AMBIGUOUS = {"ambiguous_artifact"}
_WAITING = {"accepted", "running", "waiting_on_job", "waiting_on_verification"}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(value: Optional[_dt.datetime] = None) -> str:
    when = value or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_utc(value: Any) -> Optional[_dt.datetime]:
    text = _clean_text(value)
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(_dt.timezone.utc)
    except Exception:
        return None


def _safe_name(value: Any) -> str:
    text = _clean_text(value)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-")
    return safe or "x"


def _root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "execution_reconciliation").resolve()


def _reports_dir(repo_root: Path) -> Path:
    return (_root(repo_root) / "reports").resolve()


def _receipts_dir(repo_root: Path) -> Path:
    return (_root(repo_root) / "receipts").resolve()


def _latest_task_dir(repo_root: Path) -> Path:
    return (_root(repo_root) / "latest_by_task").resolve()


def _events_path(repo_root: Path) -> Path:
    return (_root(repo_root) / "reconciliation_events.jsonl").resolve()


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


def _uniq(values: List[str]) -> List[str]:
    out: List[str] = []
    for raw in values:
        text = _clean_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _category(status: str) -> str:
    state = _clean_text(status).lower()
    if state in _SUCCESS:
        return "success"
    if state in _FAILURE:
        return "failure"
    if state in _INTERRUPTED:
        return "interrupted"
    if state in _BLOCKED:
        return "blocked"
    if state in _AMBIGUOUS:
        return "ambiguous"
    if state in _WAITING:
        return "waiting"
    return "unknown"


def _observation_rows(refs: List[Dict[str, Any]], syntheses: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for idx, synthesis in enumerate(syntheses):
        ref = dict(refs[idx]) if idx < len(refs) and isinstance(refs[idx], dict) else {}
        status = _clean_text(synthesis.get("status")).lower()
        evidence_refs = _uniq([str(item) for item in list(synthesis.get("evidence_refs") or [])])
        artifact_refs = _uniq([str(item) for item in list(ref.get("artifact_refs") or [])])
        status_refs = _uniq([str(item) for item in list(ref.get("status_refs") or [])])
        rows.append(
            {
                "index": idx,
                "surface": _clean_text(ref.get("surface")),
                "subsystem_id": _clean_text(ref.get("subsystem_id")),
                "delegation_request_id": _clean_text(synthesis.get("request_id") or ref.get("delegation_request_id")),
                "status": status,
                "category": _category(status),
                "native_status": _clean_text(synthesis.get("native_status")),
                "evidence_status": _clean_text(synthesis.get("evidence_status")),
                "summary": _clean_text(synthesis.get("summary")),
                "updated_utc": _clean_text(synthesis.get("updated_utc") or ref.get("updated_utc") or ref.get("bound_utc")),
                "evidence_refs": evidence_refs,
                "artifact_refs": artifact_refs,
                "status_refs": status_refs,
                "provenance": {
                    "surface": _clean_text(ref.get("surface")),
                    "request_id": _clean_text(synthesis.get("request_id") or ref.get("delegation_request_id")),
                    "correlation_id": _clean_text(synthesis.get("correlation_id") or ref.get("correlation_id")),
                    "detached_job_id": _clean_text(synthesis.get("detached_job_id") or ref.get("detached_job_id")),
                },
            }
        )
    return rows


def _projection(observations: List[Dict[str, Any]], *, fallback_status: str) -> Dict[str, Any]:
    categories = {str(row.get("category")) for row in observations}
    statuses = {str(row.get("status")) for row in observations}
    conflicts: List[Dict[str, Any]] = []
    reason_codes: List[str] = []
    projected_status = _clean_text(fallback_status).lower() or "active"

    if "ambiguous" in categories:
        projected_status = "ambiguous_artifact"
        reason_codes.append("native_ambiguous")
    elif "success" in categories and len(categories - {"success", "unknown"}) > 0:
        projected_status = "ambiguous_artifact"
        reason_codes.append("success_conflicts_with_other_truth")
        conflicts.append(
            {
                "kind": "conflicting_terminal_truth",
                "summary": "Success records conflict with non-success substrate records.",
                "categories": sorted(categories),
                "statuses": sorted(statuses),
            }
        )
    elif "failure" in categories and ("waiting" in categories or "interrupted" in categories):
        projected_status = "ambiguous_artifact"
        reason_codes.append("failure_conflicts_with_inflight_truth")
    elif "failure" in categories:
        projected_status = "failed"
    elif "interrupted" in categories and "waiting" in categories:
        projected_status = "interrupted_recovering"
        reason_codes.append("interrupted_with_inflight_truth")
    elif "interrupted" in categories:
        projected_status = "interrupted"
    elif "blocked" in categories:
        projected_status = "evidence_missing"
    elif "waiting" in categories:
        projected_status = "waiting_on_verification" if categories == {"waiting"} and all(
            str(row.get("status")) == "waiting_on_verification" for row in observations
        ) else "waiting_on_job"
    elif "success" in categories:
        projected_status = "completed"

    all_refs: List[str] = []
    residue_refs: List[str] = []
    receipt_refs: List[str] = []
    for row in observations:
        merged_refs = _truth_ownership.filter_durable_evidence_refs(
            list(row.get("evidence_refs") or []) + list(row.get("artifact_refs") or []) + list(row.get("status_refs") or [])
        )
        for path in merged_refs:
            path = _clean_text(path)
            if not path:
                continue
            all_refs.append(path)
            if "/repo_residue/" in path or path.endswith("/repo_residue") or path.endswith("repo_residue/summary.json"):
                residue_refs.append(path)
            if "/receipts/" in path or "/scheduled_jobs/receipts/" in path:
                receipt_refs.append(path)
    all_refs = _uniq(all_refs)
    residue_refs = _uniq(residue_refs)
    receipt_refs = _uniq(receipt_refs)

    if projected_status == "completed" and not all_refs:
        projected_status = "ambiguous_artifact"
        reason_codes.append("missing_durable_pointer")
        conflicts.append(
            {
                "kind": "missing_durable_pointer",
                "summary": "Substrate reports completion but no durable status/evidence/artifact pointer was available.",
                "categories": sorted(categories),
                "statuses": sorted(statuses),
            }
        )

    summary = (
        "No substrate observations were available."
        if not observations
        else (
            "Cross-substrate reconciliation preserved ambiguity."
            if projected_status in {"ambiguous_artifact", "interrupted_recovering"}
            else f"Projected status `{projected_status}` from {len(observations)} substrate observations."
        )
    )
    return {
        "status": projected_status,
        "summary": summary,
        "reason_codes": _uniq(reason_codes),
        "conflicts": conflicts,
        "durable_refs": all_refs,
        "residue_refs": residue_refs,
        "receipt_refs": receipt_refs,
    }


def build_task_reconciliation_report(
    repo_root: Path,
    *,
    task_id: str,
    ledger_uid: str,
    task_status: str,
    refs: List[Dict[str, Any]],
    syntheses: List[Dict[str, Any]],
    writer: str,
    now_utc: str = "",
) -> Dict[str, Any]:
    now = _parse_utc(now_utc) or _utc_now()
    observations = _observation_rows(refs, syntheses)
    projection = _projection(observations, fallback_status=task_status)
    reconciliation_id = f"recon_{now.strftime('%Y%m%dt%H%M%S').lower()}_{uuid.uuid4().hex[:8]}"
    return {
        "schema_version": RECONCILIATION_REPORT_SCHEMA_VERSION,
        "reconciliation_id": reconciliation_id,
        "generated_utc": _utc_iso(now),
        "writer": _clean_text(writer),
        "task_id": _clean_text(task_id),
        "ledger_uid": _clean_text(ledger_uid),
        "task_status": _clean_text(task_status),
        "projection": {
            "status": _clean_text(projection.get("status")),
            "summary": _clean_text(projection.get("summary")),
            "reason_codes": list(projection.get("reason_codes") or []),
            "ambiguous": _clean_text(projection.get("status")) in {"ambiguous_artifact", "interrupted_recovering"},
            "conflict_count": len(list(projection.get("conflicts") or [])),
            "residue_pointer_count": len(list(projection.get("residue_refs") or [])),
            "durable_ref_count": len(list(projection.get("durable_refs") or [])),
        },
        "observations": observations,
        "conflicts": list(projection.get("conflicts") or []),
        "durable_refs": {
            "all_refs": list(projection.get("durable_refs") or []),
            "residue_refs": list(projection.get("residue_refs") or []),
            "receipt_refs": list(projection.get("receipt_refs") or []),
        },
        "report_paths": {
            "reports_dir": str(_reports_dir(repo_root)),
            "receipts_dir": str(_receipts_dir(repo_root)),
            "latest_task_dir": str(_latest_task_dir(repo_root)),
            "events_path": str(_events_path(repo_root)),
        },
    }


def record_task_reconciliation_report(
    repo_root: Path,
    *,
    task_id: str,
    ledger_uid: str,
    task_status: str,
    refs: List[Dict[str, Any]],
    syntheses: List[Dict[str, Any]],
    writer: str,
    now_utc: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_EXECUTION_RECEIPTS,
        writer=writer,
    )
    with _LOCK:
        report = build_task_reconciliation_report(
            repo_root,
            task_id=task_id,
            ledger_uid=ledger_uid,
            task_status=task_status,
            refs=refs,
            syntheses=syntheses,
            writer=writer,
            now_utc=now_utc,
        )
        report_path = (_reports_dir(repo_root) / f"{_safe_name(report['reconciliation_id'])}.json").resolve()
        receipt_path = (_receipts_dir(repo_root) / f"{_safe_name(report['reconciliation_id'])}.json").resolve()
        latest_path = (_latest_task_dir(repo_root) / f"{_safe_name(task_id)}.json").resolve()
        receipt = {
            "schema_version": RECONCILIATION_RECEIPT_SCHEMA_VERSION,
            "reconciliation_id": report["reconciliation_id"],
            "generated_utc": _clean_text(report.get("generated_utc")),
            "writer": _clean_text(writer),
            "task_id": _clean_text(task_id),
            "ledger_uid": _clean_text(ledger_uid),
            "projected_status": _clean_text(((report.get("projection") or {}) if isinstance(report.get("projection"), dict) else {}).get("status")),
            "ambiguous": bool(((report.get("projection") or {}) if isinstance(report.get("projection"), dict) else {}).get("ambiguous")),
            "conflict_count": int(((report.get("projection") or {}) if isinstance(report.get("projection"), dict) else {}).get("conflict_count") or 0),
            "durable_ref_count": int(((report.get("projection") or {}) if isinstance(report.get("projection"), dict) else {}).get("durable_ref_count") or 0),
            "residue_pointer_count": int(((report.get("projection") or {}) if isinstance(report.get("projection"), dict) else {}).get("residue_pointer_count") or 0),
            "report_path": str(report_path),
        }
        report["report_paths"]["report_path"] = str(report_path)
        report["report_paths"]["receipt_path"] = str(receipt_path)
        report["report_paths"]["latest_task_report_path"] = str(latest_path)
        _write_json_atomic(report_path, report)
        _write_json_atomic(receipt_path, receipt)
        _write_json_atomic(latest_path, report)
        _append_jsonl(
            _events_path(repo_root),
            {
                "schema_version": RECONCILIATION_EVENT_SCHEMA_VERSION,
                "event_id": uuid.uuid4().hex,
                "event": "task_execution_reconciled",
                "utc": _clean_text(report.get("generated_utc")),
                "payload": {
                    "task_id": _clean_text(task_id),
                    "reconciliation_id": report["reconciliation_id"],
                    "projected_status": _clean_text(receipt.get("projected_status")),
                    "ambiguous": bool(receipt.get("ambiguous")),
                    "report_path": str(report_path),
                    "receipt_path": str(receipt_path),
                },
            },
        )
        return report
