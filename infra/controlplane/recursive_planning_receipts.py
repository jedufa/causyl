"""Durable recursive-planning receipts, reconciliation, and inspection helpers."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping


RECEIPT_SCHEMA_VERSION = "recursive_planning_receipt_v1"
PROJECTION_SCHEMA_VERSION = "recursive_planning_projection_v1"
_LOCK = threading.RLock()

_STAGE_STATUS = {
    "submitted": "submitted",
    "validated": "validated",
    "blocked": "blocked",
    "non_live_admitted": "non_live_admitted",
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0)


def _utc_iso(value: _dt.datetime | None = None) -> str:
    when = value or _utc_now()
    return when.astimezone(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return str(value or "").strip()


def _safe_name(value: Any) -> str:
    text = _clean_text(value)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-")
    return safe or "x"


def _plan_key(plan_id: str) -> str:
    normalized = _clean_text(plan_id)
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:10]
    return f"{_safe_name(normalized)}__{digest}"


def _root(repo_root: Path | None = None) -> Path:
    base = Path(repo_root) if repo_root is not None else _repo_root()
    return (base.resolve() / "runtime" / "state" / "controlplane" / "recursive_planning").resolve()


def _receipts_dir(repo_root: Path | None = None) -> Path:
    return (_root(repo_root) / "receipts").resolve()


def _latest_dir(repo_root: Path | None = None) -> Path:
    return (_root(repo_root) / "latest_by_plan").resolve()


def _plan_receipts_dir(plan_id: str, repo_root: Path | None = None) -> Path:
    return (_receipts_dir(repo_root) / _plan_key(plan_id)).resolve()


def _latest_path(plan_id: str, repo_root: Path | None = None) -> Path:
    return (_latest_dir(repo_root) / f"{_plan_key(plan_id)}.json").resolve()


def _ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _write_json_atomic(path: Path, obj: Mapping[str, Any]) -> None:
    _ensure_parent(path)
    raw = json.dumps(dict(obj), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = path.parent / f".{path.name}.tmp"
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    return dict(payload) if isinstance(payload, dict) else {}


def _uniq(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for raw in values:
        text = _clean_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _copy_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _copy_list(value: Any) -> List[Any]:
    return list(value) if isinstance(value, list) else []


def _parse_declared_depth(value: Any) -> int:
    try:
        return max(0, int(value))
    except Exception:
        return 0


def _budget_summary(plan_record: Mapping[str, Any], validation_report: Mapping[str, Any]) -> Dict[str, Any]:
    nodes = [row for row in _copy_list(plan_record.get("nodes")) if isinstance(row, Mapping)]
    allocations = [row for row in _copy_list(validation_report.get("budget_allocations")) if isinstance(row, Mapping)]
    return {
        "node_count": len(nodes),
        "allocation_count": len(allocations),
        "max_declared_depth": max((_parse_declared_depth(row.get("declared_depth")) for row in nodes), default=0),
        "total_allocated_minor_units": sum(int(row.get("allocated_minor_units") or 0) for row in allocations),
        "total_remaining_minor_units": sum(
            int(row.get("parent_remaining_after_child_minor_units") or 0) for row in allocations if str(row.get("parent_node_id") or "").strip()
        ),
    }


def _planned_graph_summary(plan_record: Mapping[str, Any], validation_report: Mapping[str, Any]) -> Dict[str, Any]:
    nodes = [dict(row) for row in _copy_list(plan_record.get("nodes")) if isinstance(row, Mapping)]
    edges = [dict(row) for row in _copy_list(plan_record.get("dependency_edges")) if isinstance(row, Mapping)]
    graph_summary = _copy_mapping(validation_report.get("graph_summary"))
    return {
        "root_node_id": _clean_text(plan_record.get("root_node_id")),
        "plan_status": _clean_text(plan_record.get("plan_status")),
        "budget_adaptive_receipt": _copy_mapping(plan_record.get("budget_adaptive_receipt")),
        "node_count": int(graph_summary.get("node_count") or len(nodes)),
        "dependency_edge_count": int(graph_summary.get("dependency_edge_count") or len(edges)),
        "invalid_node_count": int(graph_summary.get("invalid_node_count") or 0),
        "nodes": [
            {
                "node_id": _clean_text(row.get("node_id")),
                "job_shape": _clean_text(row.get("job_shape")),
                "node_status": _clean_text(row.get("node_status")),
                "declared_depth": _parse_declared_depth(row.get("declared_depth")),
                "parent_node_id": _clean_text(row.get("parent_node_id")),
                "child_node_ids": _copy_list(row.get("child_node_ids")),
            }
            for row in nodes
        ],
        "dependency_edges": edges,
    }


def _receipt_filename(*, recorded_at_utc: str, stage: str, plan_fingerprint: str) -> str:
    stamp = recorded_at_utc.replace(":", "").replace("-", "").replace("Z", "z")
    digest = hashlib.sha256(f"{stage}|{plan_fingerprint}|{recorded_at_utc}".encode("utf-8")).hexdigest()[:12]
    return f"{stamp}__{_safe_name(stage)}__{digest}.json"


def _write_receipt(receipt: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    payload = dict(receipt)
    plan_id = _clean_text(payload.get("plan_id"))
    if not plan_id:
        raise ValueError("plan_id is required")
    receipt_dir = _plan_receipts_dir(plan_id, repo_root=repo_root)
    filename = _receipt_filename(
        recorded_at_utc=_clean_text(payload.get("recorded_at_utc")) or _utc_iso(),
        stage=_clean_text(payload.get("stage")) or "unknown",
        plan_fingerprint=_clean_text(payload.get("plan_fingerprint")) or _clean_text(payload.get("request_fingerprint")) or plan_id,
    )
    path = (receipt_dir / filename).resolve()
    _write_json_atomic(path, payload)
    saved = dict(payload)
    saved["receipt_path"] = str(path)
    return saved


def _read_receipts_for_plan(plan_id: str, *, repo_root: Path | None = None) -> List[Dict[str, Any]]:
    receipt_dir = _plan_receipts_dir(plan_id, repo_root=repo_root)
    rows: List[Dict[str, Any]] = []
    if not receipt_dir.exists():
        return rows
    for path in sorted((item for item in receipt_dir.iterdir() if item.is_file() and item.suffix == ".json"), key=lambda item: item.name):
        payload = _read_json(path)
        if not payload:
            continue
        payload["receipt_path"] = str(path)
        rows.append(payload)
    return rows


def record_submitted_receipt(
    raw_request: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    writer: str = "recursive_planning.submit",
    recorded_at_utc: str = "",
) -> Dict[str, Any]:
    from infra.controlplane import recursive_dry_run_planner as _planner

    normalized = _planner.normalize_recursive_dry_run_request(raw_request)
    recorded_at = _clean_text(recorded_at_utc) or _utc_iso()
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "plan_id": _clean_text(normalized.get("plan_id")),
        "root_plan_id": _clean_text(normalized.get("root_plan_id")),
        "parent_plan_id": _clean_text(normalized.get("parent_plan_id")),
        "parent_job_id": _clean_text(normalized.get("parent_job_id")),
        "root_binding": _copy_mapping(normalized.get("root_binding")),
        "stage": "submitted",
        "status": _STAGE_STATUS["submitted"],
        "recorded_at_utc": recorded_at,
        "writer": writer,
        "request_fingerprint": _planner._fingerprint(normalized),
        "plan_fingerprint": "",
        "plan_truth_refs": _copy_mapping(normalized.get("plan_truth_refs")),
        "plan_event_refs": _copy_list(normalized.get("plan_event_refs")),
        "normalized_request": normalized,
        "source_of_truth": "durable_recursive_planning_receipts",
    }
    with _LOCK:
        saved = _write_receipt(receipt, repo_root=repo_root)
        projection = reconcile_plan_state(saved["plan_id"], repo_root=repo_root)
        _write_json_atomic(_latest_path(saved["plan_id"], repo_root=repo_root), projection)
    return {"ok": True, "receipt": saved, "projection": projection}


def record_validation_receipt(
    plan_result: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    writer: str = "recursive_planning.validation",
    recorded_at_utc: str = "",
) -> Dict[str, Any]:
    plan_record = _copy_mapping(plan_result.get("plan_record"))
    validation_report = _copy_mapping(plan_result.get("validation_report"))
    plan_id = _clean_text(plan_record.get("plan_id"))
    if not plan_id:
        raise ValueError("validated plan_result requires plan_record.plan_id")
    status = "validated" if bool(plan_result.get("ok")) else "blocked"
    recorded_at = _clean_text(recorded_at_utc) or _utc_iso()
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "plan_id": plan_id,
        "root_plan_id": _clean_text(plan_record.get("root_plan_id")),
        "parent_plan_id": _clean_text(plan_record.get("parent_plan_id")),
        "parent_job_id": _clean_text(plan_record.get("parent_job_id")),
        "root_binding": _copy_mapping(plan_record.get("root_binding")),
        "stage": status,
        "status": _STAGE_STATUS[status],
        "recorded_at_utc": recorded_at,
        "writer": writer,
        "request_fingerprint": _clean_text(validation_report.get("request_fingerprint")),
        "plan_fingerprint": _clean_text(validation_report.get("plan_fingerprint")),
        "plan_truth_refs": _copy_mapping(plan_record.get("plan_truth_refs")),
        "plan_event_refs": _copy_list(plan_record.get("plan_event_refs")),
        "plan_record": plan_record,
        "validation_report": validation_report,
        "source_of_truth": "durable_recursive_planning_receipts",
    }
    with _LOCK:
        saved = _write_receipt(receipt, repo_root=repo_root)
        projection = reconcile_plan_state(plan_id, repo_root=repo_root)
        _write_json_atomic(_latest_path(plan_id, repo_root=repo_root), projection)
    return {"ok": True, "receipt": saved, "projection": projection}


def record_non_live_admission_receipt(
    admission: Mapping[str, Any],
    *,
    plan_result: Mapping[str, Any] | None = None,
    repo_root: Path | None = None,
    writer: str = "recursive_planning.non_live_admission",
    recorded_at_utc: str = "",
) -> Dict[str, Any]:
    admission_payload = dict(admission)
    plan_projection = _copy_mapping(admission_payload.get("plan_projection"))
    plan_id = _clean_text(plan_projection.get("plan_id"))
    if not plan_id and isinstance(plan_result, Mapping):
        plan_record = _copy_mapping(plan_result.get("plan_record"))
        plan_id = _clean_text(plan_record.get("plan_id"))
    if not plan_id:
        raise ValueError("non-live admission requires admission.plan_projection.plan_id")
    plan_record = _copy_mapping(_copy_mapping(plan_result or {}).get("plan_record"))
    validation_report = _copy_mapping(_copy_mapping(plan_result or {}).get("validation_report"))
    recorded_at = _clean_text(recorded_at_utc) or _utc_iso()
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "plan_id": plan_id,
        "root_plan_id": _clean_text(plan_projection.get("root_plan_id")),
        "parent_plan_id": _clean_text(plan_projection.get("parent_plan_id")),
        "parent_job_id": _clean_text(plan_projection.get("parent_job_id")),
        "root_binding": _copy_mapping(_copy_mapping(plan_record).get("root_binding") or admission_payload.get("entrypoint")),
        "stage": "non_live_admitted",
        "status": _STAGE_STATUS["non_live_admitted"],
        "recorded_at_utc": recorded_at,
        "writer": writer,
        "request_fingerprint": _clean_text(plan_projection.get("request_fingerprint")),
        "plan_fingerprint": _clean_text(plan_projection.get("plan_fingerprint")),
        "plan_truth_refs": _copy_mapping(_copy_mapping(plan_record).get("plan_truth_refs")),
        "plan_event_refs": _copy_list(_copy_mapping(plan_record).get("plan_event_refs")),
        "admission": admission_payload,
        "plan_record": plan_record,
        "validation_report": validation_report,
        "source_of_truth": "durable_recursive_planning_receipts",
    }
    with _LOCK:
        saved = _write_receipt(receipt, repo_root=repo_root)
        projection = reconcile_plan_state(plan_id, repo_root=repo_root)
        _write_json_atomic(_latest_path(plan_id, repo_root=repo_root), projection)
    return {"ok": True, "receipt": saved, "projection": projection}


def reconcile_plan_state(plan_id: str, *, repo_root: Path | None = None) -> Dict[str, Any]:
    normalized_plan_id = _clean_text(plan_id)
    receipts = _read_receipts_for_plan(normalized_plan_id, repo_root=repo_root)
    latest_submission: Dict[str, Any] = {}
    latest_validation: Dict[str, Any] = {}
    latest_admission: Dict[str, Any] = {}
    for receipt in receipts:
        stage = _clean_text(receipt.get("stage"))
        if stage == "submitted":
            latest_submission = receipt
        elif stage in {"validated", "blocked"}:
            latest_validation = receipt
        elif stage == "non_live_admitted":
            latest_admission = receipt

    plan_record = _copy_mapping(latest_validation.get("plan_record")) or _copy_mapping(latest_admission.get("plan_record"))
    validation_report = _copy_mapping(latest_validation.get("validation_report")) or _copy_mapping(latest_admission.get("validation_report"))
    admission = _copy_mapping(latest_admission.get("admission"))
    binding = (
        _copy_mapping(plan_record.get("root_binding"))
        or _copy_mapping(latest_submission.get("root_binding"))
        or _copy_mapping(latest_validation.get("root_binding"))
        or _copy_mapping(latest_admission.get("root_binding"))
    )

    if latest_admission:
        projected_status = "non_live_admitted"
    elif latest_validation:
        projected_status = _clean_text(latest_validation.get("status")) or "blocked"
    elif latest_submission:
        projected_status = "submitted"
    else:
        projected_status = "missing"

    blocking_reason_codes = _uniq(
        _copy_list(validation_report.get("non_launch_reason_codes"))
        + _copy_list(_copy_mapping(admission.get("plan_projection")).get("non_launch_reason_codes"))
        + _copy_list(admission.get("non_launch_reason_codes"))
    )
    if not blocking_reason_codes:
        scheduler_admission = _copy_mapping(admission.get("scheduler_admission"))
        reason = _clean_text(scheduler_admission.get("nonlaunch_reason"))
        if reason:
            blocking_reason_codes = [reason]

    projection = {
        "schema_version": PROJECTION_SCHEMA_VERSION,
        "plan_id": normalized_plan_id,
        "root_plan_id": _clean_text(plan_record.get("root_plan_id")) or _clean_text(latest_submission.get("root_plan_id")),
        "parent_plan_id": _clean_text(plan_record.get("parent_plan_id")) or _clean_text(latest_submission.get("parent_plan_id")),
        "parent_job_id": _clean_text(plan_record.get("parent_job_id")) or _clean_text(latest_submission.get("parent_job_id")),
        "root_binding": binding,
        "projection_source": "durable_receipts_only",
        "status": projected_status,
        "submission_present": bool(latest_submission),
        "validation_present": bool(latest_validation),
        "non_live_admission_present": bool(latest_admission),
        "submission_receipt_path": _clean_text(latest_submission.get("receipt_path")),
        "validation_receipt_path": _clean_text(latest_validation.get("receipt_path")),
        "non_live_admission_receipt_path": _clean_text(latest_admission.get("receipt_path")),
        "receipt_paths": _uniq(receipt.get("receipt_path") for receipt in receipts),
        "request_fingerprint": _clean_text(latest_validation.get("request_fingerprint"))
        or _clean_text(latest_admission.get("request_fingerprint"))
        or _clean_text(latest_submission.get("request_fingerprint")),
        "plan_fingerprint": _clean_text(latest_validation.get("plan_fingerprint")) or _clean_text(latest_admission.get("plan_fingerprint")),
        "planned_graph": _planned_graph_summary(plan_record, validation_report) if plan_record else {},
        "budget_depth_status": _budget_summary(plan_record, validation_report) if plan_record else {},
        "blocking_reason_codes": blocking_reason_codes,
        "budget_adaptive_receipt": _copy_mapping(plan_record.get("budget_adaptive_receipt"))
        or _copy_mapping(validation_report.get("budget_adaptive_receipt")),
        "budget_adaptive_reason_codes": _copy_list(validation_report.get("budget_adaptive_reason_codes")),
        "non_live_admission": {
            "scheduler_admission": _copy_mapping(admission.get("scheduler_admission")),
            "campaign_context": _copy_mapping(admission.get("campaign_context")),
            "queue_law": _copy_mapping(admission.get("queue_law")),
        }
        if admission
        else {},
        "latest_receipts": {
            "submitted": _clean_text(latest_submission.get("receipt_path")),
            "validation": _clean_text(latest_validation.get("receipt_path")),
            "non_live_admission": _clean_text(latest_admission.get("receipt_path")),
        },
        "reconciled_at_utc": _utc_iso(),
    }
    return projection


def list_plan_projections(
    *,
    repo_root: Path | None = None,
    limit: int = 50,
    binding_type: str = "",
    binding_id: str = "",
) -> Dict[str, Any]:
    latest_dir = _latest_dir(repo_root)
    rows: List[Dict[str, Any]] = []
    if latest_dir.exists():
        for path in sorted((item for item in latest_dir.iterdir() if item.is_file() and item.suffix == ".json"), key=lambda item: item.name, reverse=True):
            payload = _read_json(path)
            if not payload:
                continue
            binding = _copy_mapping(payload.get("root_binding"))
            if binding_type and _clean_text(binding.get("binding_type")).lower() != _clean_text(binding_type).lower():
                continue
            if binding_id and _clean_text(binding.get("binding_id")) != _clean_text(binding_id):
                continue
            rows.append(payload)
    rows.sort(
        key=lambda item: (
            _clean_text(item.get("non_live_admission_receipt_path"))
            or _clean_text(item.get("validation_receipt_path"))
            or _clean_text(item.get("submission_receipt_path")),
            _clean_text(item.get("plan_id")),
        ),
        reverse=True,
    )
    limited = rows[: max(1, int(limit))]
    return {
        "ok": True,
        "projections": limited,
        "total_plans": len(rows),
        "filter": {
            "binding_type": _clean_text(binding_type).lower(),
            "binding_id": _clean_text(binding_id),
        },
    }
