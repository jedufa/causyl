"""Phase-3 operator derived state view helpers.

The derived view is descriptive only. It summarizes current operator-relevant
state from existing substrate evidence without claiming ownership of runtime
truth.
"""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

OPERATOR_DERIVED_STATE_VIEW_SCHEMA_PATH = Path("governance/contracts/operator_derived_state_view_v1.json")

_STALE_VALUES = {"stale", "old", "expired", "outdated"}


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_operator_derived_state_view_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / OPERATOR_DERIVED_STATE_VIEW_SCHEMA_PATH)


def _as_mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _as_list_of_mappings(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in list(value or []):
        if isinstance(item, Mapping):
            rows.append(dict(item))
    return rows


def _normalize_status(value: Any) -> str:
    text = str(value or "").strip().lower()
    mapping = {
        "completed_with_pass": "pass",
        "pass": "pass",
        "success": "pass",
        "completed_with_proof_gap": "proof_gap",
        "proof_gap_present": "proof_gap",
        "proof_gap": "proof_gap",
        "blocked": "blocked",
        "escalated_and_blocked": "blocked",
        "failed": "failed",
        "failed_known": "failed",
        "failed_unknown": "failed",
        "degraded": "degraded",
        "running": "running",
        "in_progress": "running",
        "completed": "pass",
        "unknown": "unknown",
    }
    return mapping.get(text, text or "unknown")


def _status_from_receipts(receipts: Iterable[Mapping[str, Any]]) -> str:
    rows = list(receipts)
    if not rows:
        return ""
    last = rows[-1]
    for key in ("status", "last_status", "state_class"):
        text = str(last.get(key) or "").strip()
        if text:
            return _normalize_status(text)
    return ""


def _is_stale(value: Mapping[str, Any]) -> bool:
    if not value:
        return False
    if bool(value.get("is_stale") or value.get("stale")):
        return True
    freshness = str(value.get("freshness") or value.get("freshness_state") or "").strip().lower()
    if freshness in _STALE_VALUES:
        return True
    age_seconds = value.get("age_seconds")
    max_age = value.get("max_fresh_age_seconds")
    if isinstance(age_seconds, (int, float)) and isinstance(max_age, (int, float)):
        return float(age_seconds) > float(max_age)
    return False


def _gather_report_proof_gaps(report: Mapping[str, Any]) -> List[str]:
    gaps: List[str] = []
    for record in list(report.get("step_records") or []):
        if not isinstance(record, Mapping):
            continue
        for gap in list(record.get("proof_gaps") or []):
            text = str(gap or "").strip()
            if text and text not in gaps:
                gaps.append(text)
    for row in list(report.get("failure_escalation_log") or []):
        if not isinstance(row, Mapping):
            continue
        for gap in list(row.get("proof_gaps") or []):
            text = str(gap or "").strip()
            if text and text not in gaps:
                gaps.append(text)
    return gaps


def _gather_verifier_proof_gaps(verifier: Mapping[str, Any], payload: Mapping[str, Any]) -> List[str]:
    gaps: List[str] = []
    for source in (verifier, payload):
        for key in ("proof_gaps", "unresolved_proof_gaps"):
            for gap in list(source.get(key) or []):
                text = str(gap or "").strip()
                if text and text not in gaps:
                    gaps.append(text)
    return gaps


def _detect_contradiction(statuses: Mapping[str, str]) -> bool:
    concrete = {key: value for key, value in statuses.items() if value and value not in {"", "unknown", "degraded"}}
    unique = set(concrete.values())
    if not unique:
        return False
    if unique <= {"blocked", "proof_gap"}:
        return False
    return len(unique) > 1


def _unique_refs(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def build_operator_derived_state_view_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_operator_derived_state_view_schema(repo_root=repo_root)
    snapshot = _as_mapping(payload.get("snapshot"))
    receipts = _as_list_of_mappings(payload.get("receipts"))
    watchdog = _as_mapping(payload.get("watchdog_summary"))
    verifier = _as_mapping(payload.get("verifier_result"))
    report = _as_mapping(payload.get("composed_action_report") or payload.get("report"))
    fallback_events = _as_list_of_mappings(payload.get("fallback_events"))

    snapshot_status = _normalize_status(snapshot.get("status") or snapshot.get("current_status"))
    receipt_status = _status_from_receipts(receipts)
    watchdog_status = _normalize_status(watchdog.get("status") or watchdog.get("state_class"))
    report_status = _normalize_status(report.get("final_chain_outcome") or report.get("status"))

    proof_gaps = _gather_report_proof_gaps(report)
    for gap in _gather_verifier_proof_gaps(verifier, payload):
        if gap not in proof_gaps:
            proof_gaps.append(gap)

    stale_by_source = {
        "snapshot": _is_stale(snapshot),
        "receipts": any(_is_stale(row) for row in receipts),
        "watchdog": _is_stale(watchdog),
        "verifier": _is_stale(verifier),
        "composed_action_report": _is_stale(report),
    }
    stale_evidence_present = any(stale_by_source.values())
    contradiction_present = _detect_contradiction({
        "snapshot": snapshot_status,
        "receipts": receipt_status,
        "watchdog": watchdog_status,
        "composed_action_report": report_status,
    })

    verifier_verdict = str(
        report.get("final_verifier_verdict")
        or verifier.get("verifier_verdict")
        or verifier.get("verdict")
        or payload.get("verifier_verdict")
        or ""
    ).strip().upper()

    if contradiction_present:
        state_class = "degraded"
        primary_reason_hint = "contradictory_evidence"
    elif stale_evidence_present:
        state_class = "degraded"
        primary_reason_hint = "stale_evidence"
    elif report_status == "pass" and verifier_verdict == "PASS" and not proof_gaps:
        state_class = "pass"
        primary_reason_hint = "explicit_pass"
    elif report_status == "blocked":
        state_class = "blocked"
        primary_reason_hint = str(report.get("terminal_reason") or "blocked_step").strip() or "blocked_step"
    elif report_status == "failed":
        state_class = "failed"
        primary_reason_hint = str(report.get("terminal_reason") or "failed_step").strip() or "failed_step"
    elif report_status == "proof_gap" or proof_gaps:
        state_class = "degraded"
        primary_reason_hint = proof_gaps[0] if proof_gaps else "proof_gap_present"
    elif any(status == "running" for status in (snapshot_status, receipt_status, watchdog_status, report_status)):
        state_class = "running"
        primary_reason_hint = "active_or_in_progress"
    else:
        state_class = "unknown"
        primary_reason_hint = "insufficient_evidence"

    provenance = {
        "state_class": _unique_refs([
            "composed_action_report.final_chain_outcome" if report_status != "unknown" else "",
            "snapshot.status" if snapshot_status != "unknown" else "",
            "receipts[-1].status" if receipt_status else "",
            "watchdog_summary.status" if watchdog_status != "unknown" else "",
        ]),
        "final_verifier_verdict": _unique_refs([
            "composed_action_report.final_verifier_verdict" if report.get("final_verifier_verdict") else "",
            "verifier_result.verifier_verdict" if verifier else "",
        ]),
        "proof_gaps": _unique_refs([
            "composed_action_report.step_records[].proof_gaps" if proof_gaps else "",
            "verifier_result.proof_gaps" if _gather_verifier_proof_gaps(verifier, payload) else "",
        ]),
        "freshness": _unique_refs([f"{name}.freshness" for name, stale in stale_by_source.items() if stale]),
        "codex_used": ["fallback_events"] if fallback_events else [],
    }

    view_id = str(
        report.get("chain_id")
        or report.get("request_classification", {}).get("task_id")
        or snapshot.get("drift_id")
        or snapshot.get("target_uid")
        or payload.get("task_id")
        or "operator_state_view_v1"
    ).strip()
    action_family = str(report.get("action_family") or payload.get("action_family") or "").strip()
    subject_kind = "chain" if report else ("snapshot" if snapshot else "unknown")

    return {
        "schema_version": "operator_derived_state_view_v1",
        "view_id": view_id,
        "subject_kind": subject_kind,
        "action_family": action_family,
        "state_class": state_class,
        "primary_reason_hint": primary_reason_hint,
        "final_verifier_verdict": verifier_verdict,
        "proof_gap_present": bool(proof_gaps),
        "unresolved_proof_gaps": proof_gaps,
        "stale_evidence_present": stale_evidence_present,
        "contradiction_present": contradiction_present,
        "source_statuses": {
            "snapshot": snapshot_status,
            "receipts": receipt_status,
            "watchdog": watchdog_status,
            "composed_action_report": report_status,
        },
        "freshness_by_source": stale_by_source,
        "codex_used": bool(fallback_events),
        "evidence_provenance": provenance,
    }


def validate_operator_derived_state_view_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_operator_derived_state_view_schema(repo_root=repo_root)
    required = [str(item or "").strip() for item in list(schema.get("required_fields") or []) if str(item or "").strip()]
    missing = [field for field in required if field not in payload]
    errors: List[Dict[str, str]] = []
    for field in missing:
        errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    state_class = str(payload.get("state_class") or "").strip()
    allowed = {str(item or "").strip() for item in list(schema.get("allowed_state_classes") or []) if str(item or "").strip()}
    if state_class and state_class not in allowed:
        errors.append({"code": "unknown_state_class", "message": f"unknown state class: {state_class}"})
    if not isinstance(payload.get("evidence_provenance"), Mapping):
        errors.append({"code": "invalid_evidence_provenance", "message": "evidence_provenance must be a mapping"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}
