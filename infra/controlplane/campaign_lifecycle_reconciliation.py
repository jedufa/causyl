"""Restart-safe campaign lifecycle and budget reconciliation helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import campaign_phase_gate_contract
from infra.controlplane import campaign_record_contract
from infra.controlplane import job_budget_cost_ledger_store


SCHEMA_VERSION = "campaign_lifecycle_reconciliation_v1"

_SUCCESS_RECEIPT_STATUSES = frozenset({"ok", "succeeded", "success", "completed"})
_FAILED_RECEIPT_STATUSES = frozenset({"failed", "error"})
_CANCELLED_RECEIPT_STATUSES = frozenset({"cancelled", "canceled"})
_RUNNING_RECEIPT_STATUSES = frozenset({"running", "started", "queued", "in_progress"})
_REVIEW_RECEIPT_STATUSES = frozenset({"waiting_review", "review"})
_BLOCKED_RECEIPT_STATUSES = frozenset({"blocked"})


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for raw in values:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _maybe_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _load_json(path: Path) -> Dict[str, Any]:
    import json

    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"receipt ref must point to a JSON object: {path}")
    return payload


def _receipt_terminal_class(status: str) -> str:
    normalized = _normalize_text(status).lower()
    if normalized in _SUCCESS_RECEIPT_STATUSES:
        return "success_terminal"
    if normalized in _FAILED_RECEIPT_STATUSES:
        return "failure_terminal"
    if normalized in _CANCELLED_RECEIPT_STATUSES:
        return "cancelled_terminal"
    if normalized in _REVIEW_RECEIPT_STATUSES:
        return "waiting_review"
    if normalized in _BLOCKED_RECEIPT_STATUSES:
        return "blocked"
    if normalized in _RUNNING_RECEIPT_STATUSES:
        return "in_flight"
    return "unknown"


def _normalize_receipt(raw: Mapping[str, Any], *, source_ref: str = "") -> Dict[str, Any]:
    run_id = _normalize_text(raw.get("run_id"))
    job_id = _normalize_text(raw.get("job_id"))
    status = _normalize_text(raw.get("status")).lower()
    if not status and isinstance(raw.get("ownership"), Mapping):
        status = _normalize_text(raw.get("ownership", {}).get("handoff_state")).lower()
    receipt_id = _normalize_text(raw.get("receipt_id")) or _normalize_text(raw.get("receipt_uid")) or source_ref or run_id
    retry = raw.get("retry") if isinstance(raw.get("retry"), Mapping) else {}
    return {
        "receipt_id": receipt_id,
        "run_id": run_id,
        "job_id": job_id,
        "status": status,
        "terminal_class": _receipt_terminal_class(status),
        "recorded_at_utc": _normalize_text(raw.get("finished_at_utc") or raw.get("recorded_at_utc") or raw.get("started_at_utc")),
        "source_ref": source_ref,
        "retry": {
            "attempt_count": _maybe_int(retry.get("attempt_count") or raw.get("attempt_count")),
            "max_attempts": _maybe_int(retry.get("max_attempts") or raw.get("max_attempts") or raw.get("retry_limit")),
            "halted": bool(retry.get("halted")) or _normalize_text(retry.get("state")).lower() == "halted",
            "reason": _normalize_text(retry.get("reason")),
        },
    }


def _merge_runtime_facts(phase: Mapping[str, Any], phase_fact: Mapping[str, Any], *, repo_root: Path | None) -> Dict[str, Any]:
    truth_refs = phase.get("truth_refs") if isinstance(phase.get("truth_refs"), Mapping) else {}
    receipt_refs = _normalize_text_list(truth_refs.get("receipt_refs"))
    receipt_refs.extend(ref for ref in _normalize_text_list(phase_fact.get("receipt_refs")) if ref not in receipt_refs)

    continuation_packet_ids = _normalize_text_list(truth_refs.get("continuation_packet_ids"))
    continuation_packet_ids.extend(
        packet for packet in _normalize_text_list(phase_fact.get("continuation_packet_ids")) if packet not in continuation_packet_ids
    )

    receipts: List[Dict[str, Any]] = []
    for ref in receipt_refs:
        if repo_root is None:
            break
        ref_path = (repo_root / ref).resolve()
        if not ref_path.exists():
            continue
        repo_root_resolved = repo_root.resolve()
        if ref_path != repo_root_resolved and repo_root_resolved not in ref_path.parents:
            raise ValueError(f"receipt ref escapes repo root: {ref}")
        receipts.append(_normalize_receipt(_load_json(ref_path), source_ref=ref))

    for raw in list(phase_fact.get("receipts") or []):
        if isinstance(raw, Mapping):
            receipts.append(_normalize_receipt(raw))

    job_ids = _normalize_text_list(truth_refs.get("job_ids"))
    for receipt in receipts:
        job_id = _normalize_text(receipt.get("job_id"))
        if job_id and job_id not in job_ids:
            job_ids.append(job_id)

    return {
        "receipt_refs": receipt_refs,
        "continuation_packet_ids": continuation_packet_ids,
        "receipts": receipts,
        "job_ids": job_ids,
    }


def _gate_map(gate_decisions: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    gates: Dict[str, Dict[str, Any]] = {}
    for raw in gate_decisions:
        if not isinstance(raw, Mapping):
            continue
        result = campaign_phase_gate_contract.validate_phase_gate_decision(raw)
        if not result["ok"]:
            raise ValueError("phase gate decision must validate before campaign reconciliation")
        normalized = dict(result["normalized_decision"])
        phase_id = _normalize_text(normalized.get("phase_id"))
        if phase_id:
            gates[phase_id] = normalized
    return gates


def _retry_boundary(receipts: Sequence[Mapping[str, Any]], gate: Mapping[str, Any] | None) -> Dict[str, Any]:
    if gate and str(gate.get("verdict") or "") == "waiting_review":
        return {"state": "halted_review", "attempt_count": 0, "max_attempts": 0, "reason": "waiting_review"}
    best_attempt = 0
    max_attempts = 0
    halted = False
    halt_reason = ""
    for receipt in receipts:
        retry = receipt.get("retry") if isinstance(receipt.get("retry"), Mapping) else {}
        best_attempt = max(best_attempt, int(retry.get("attempt_count") or 0))
        max_attempts = max(max_attempts, int(retry.get("max_attempts") or 0))
        if bool(retry.get("halted")):
            halted = True
            halt_reason = _normalize_text(retry.get("reason")) or "halted"
    if halted:
        return {
            "state": "halted",
            "attempt_count": best_attempt,
            "max_attempts": max_attempts,
            "reason": halt_reason,
        }
    if max_attempts and best_attempt >= max_attempts:
        return {
            "state": "exhausted",
            "attempt_count": best_attempt,
            "max_attempts": max_attempts,
            "reason": "retry_limit_reached",
        }
    if best_attempt or max_attempts:
        return {
            "state": "allowed",
            "attempt_count": best_attempt,
            "max_attempts": max_attempts,
            "reason": "",
        }
    return {"state": "not_declared", "attempt_count": 0, "max_attempts": 0, "reason": ""}


def _phase_budget_rollup(job_ids: Sequence[str], *, repo_root: Path | None) -> Dict[str, Any]:
    if not job_ids:
        return {
            "status": "missing_job_refs",
            "job_ids": [],
            "unit": "",
            "allocated_minor_units": 0,
            "spent_minor_units": 0,
            "remaining_minor_units": 0,
            "follow_up_statuses": [],
        }

    allocated = 0
    spent = 0
    remaining = 0
    unit = ""
    follow_up_statuses: List[str] = []
    for job_id in job_ids:
        state = job_budget_cost_ledger_store.replay_job_budget_state(job_id, repo_root=repo_root)
        if not unit:
            unit = str(state.get("unit") or "")
        elif unit != str(state.get("unit") or ""):
            return {
                "status": "conflicting_units",
                "job_ids": list(job_ids),
                "unit": "",
                "allocated_minor_units": 0,
                "spent_minor_units": 0,
                "remaining_minor_units": 0,
                "follow_up_statuses": follow_up_statuses,
            }
        allocated += int(state.get("allocated_minor_units") or 0)
        spent += int(state.get("cost_debit_minor_units") or 0) - int(state.get("cost_credit_minor_units") or 0)
        remaining += int(state.get("remaining_minor_units") or 0)
        follow_up = job_budget_cost_ledger_store.recovery_follow_up_state(job_id, repo_root=repo_root)
        status = str(follow_up.get("status") or "")
        if status:
            follow_up_statuses.append(status)

    budget_status = "ok"
    if any(status == "blocked" for status in follow_up_statuses):
        budget_status = "blocked"
    elif any(status == "review_required" for status in follow_up_statuses):
        budget_status = "review_required"
    elif remaining < 0:
        budget_status = "blocked"

    return {
        "status": budget_status,
        "job_ids": list(job_ids),
        "unit": unit,
        "allocated_minor_units": allocated,
        "spent_minor_units": spent,
        "remaining_minor_units": remaining,
        "follow_up_statuses": follow_up_statuses,
    }


def _phase_state(
    phase: Mapping[str, Any],
    *,
    gate: Mapping[str, Any] | None,
    receipts: Sequence[Mapping[str, Any]],
    continuation_packet_ids: Sequence[str],
    budget: Mapping[str, Any],
) -> Dict[str, Any]:
    phase_id = _normalize_text(phase.get("phase_id"))
    record_state = _normalize_text(phase.get("state")).lower()
    duplicate_receipt_ids = sorted(
        {
            receipt_id
            for receipt_id in [_normalize_text(row.get("receipt_id")) for row in receipts]
            if receipt_id and sum(1 for row in receipts if _normalize_text(row.get("receipt_id")) == receipt_id) > 1
        }
    )
    receipt_classes = {_normalize_text(row.get("terminal_class")) for row in receipts if _normalize_text(row.get("terminal_class"))}
    gate_verdict = _normalize_text((gate or {}).get("verdict")).lower()
    retry_boundary = _retry_boundary(receipts, gate)

    reasons: List[str] = []
    reconciled_state = record_state or "declared"
    progress_state = "not_launchable"

    if duplicate_receipt_ids:
        reasons.append("duplicate_receipts")
        reconciled_state = "blocked"
        progress_state = "blocked"
    elif len(receipt_classes - {"unknown"}) > 1:
        reasons.append("conflicting_receipt_states")
        reconciled_state = "blocked"
        progress_state = "blocked"
    elif gate_verdict == "waiting_review":
        reasons.append("waiting_review_gate")
        reconciled_state = "waiting_review"
        progress_state = "waiting_review"
    elif gate_verdict == "blocked":
        reasons.append("blocked_gate")
        reconciled_state = "blocked"
        progress_state = "blocked"
    elif gate_verdict == "terminal_failed":
        reconciled_state = "failed"
        progress_state = "terminal"
    elif gate_verdict == "terminal_cancelled":
        reconciled_state = "cancelled"
        progress_state = "terminal"
    elif gate_verdict == "terminal_success":
        reconciled_state = "succeeded"
        progress_state = "terminal"
    elif "failure_terminal" in receipt_classes:
        reconciled_state = "failed"
        progress_state = "terminal"
    elif "cancelled_terminal" in receipt_classes:
        reconciled_state = "cancelled"
        progress_state = "terminal"
    elif "success_terminal" in receipt_classes:
        if gate_verdict in {"blocked", "waiting_review"}:
            reasons.append("receipt_gate_conflict")
            reconciled_state = "blocked"
            progress_state = "blocked"
        else:
            reconciled_state = "succeeded"
            progress_state = "terminal"
    elif "waiting_review" in receipt_classes:
        reasons.append("receipt_waiting_review")
        reconciled_state = "waiting_review"
        progress_state = "waiting_review"
    elif "blocked" in receipt_classes:
        reasons.append("receipt_blocked")
        reconciled_state = "blocked"
        progress_state = "blocked"
    elif "in_flight" in receipt_classes or continuation_packet_ids:
        reasons.append("in_flight_receipt" if "in_flight" in receipt_classes else "continuation_present")
        reconciled_state = "running"
        progress_state = "running"
    elif gate_verdict in {"ready", "partial_ready"} and record_state == "declared":
        progress_state = "launchable"
    elif record_state in {"running", "blocked", "succeeded", "failed", "cancelled"}:
        reconciled_state = record_state
        progress_state = "terminal" if record_state in {"succeeded", "failed", "cancelled"} else record_state

    if retry_boundary["state"] == "exhausted" and reconciled_state not in {"succeeded", "failed", "cancelled"}:
        reasons.append("retry_exhausted")
        reconciled_state = "blocked"
        progress_state = "blocked"

    budget_status = _normalize_text(budget.get("status"))
    if budget_status in {"missing_job_refs", "conflicting_units", "blocked", "review_required"} and reconciled_state == "running":
        reasons.append(f"budget_{budget_status}")
        reconciled_state = "blocked"
        progress_state = "blocked"

    return {
        "phase_id": phase_id,
        "record_state": record_state,
        "reconciled_state": reconciled_state,
        "progress_state": progress_state,
        "gate_verdict": gate_verdict,
        "receipt_ids": [_normalize_text(row.get("receipt_id")) for row in receipts if _normalize_text(row.get("receipt_id"))],
        "receipt_refs": [_normalize_text(row.get("source_ref")) for row in receipts if _normalize_text(row.get("source_ref"))],
        "continuation_packet_ids": list(continuation_packet_ids),
        "retry_boundary": retry_boundary,
        "budget": dict(budget),
        "reasons": reasons,
    }


def _campaign_budget_rollup(phases: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    units = sorted({_normalize_text(row.get("budget", {}).get("unit")) for row in phases if _normalize_text(row.get("budget", {}).get("unit"))})
    missing = [str(row.get("phase_id") or "") for row in phases if _normalize_text(row.get("budget", {}).get("status")) == "missing_job_refs"]
    blocked = [str(row.get("phase_id") or "") for row in phases if _normalize_text(row.get("budget", {}).get("status")) in {"blocked", "review_required"}]
    conflicting = [str(row.get("phase_id") or "") for row in phases if _normalize_text(row.get("budget", {}).get("status")) == "conflicting_units"]

    if conflicting or len(units) > 1:
        return {
            "status": "blocked_conflicting_units",
            "unit": "",
            "allocated_minor_units": 0,
            "spent_minor_units": 0,
            "remaining_minor_units": 0,
            "phase_ids_missing_budget_refs": missing,
            "phase_ids_blocked_by_budget": blocked,
            "phase_ids_conflicting_units": conflicting,
        }

    if missing:
        return {
            "status": "blocked_missing_job_refs",
            "unit": units[0] if units else "",
            "allocated_minor_units": 0,
            "spent_minor_units": 0,
            "remaining_minor_units": 0,
            "phase_ids_missing_budget_refs": missing,
            "phase_ids_blocked_by_budget": blocked,
            "phase_ids_conflicting_units": conflicting,
        }

    return {
        "status": "blocked" if blocked else "ok",
        "unit": units[0] if units else "",
        "allocated_minor_units": sum(int(row.get("budget", {}).get("allocated_minor_units") or 0) for row in phases),
        "spent_minor_units": sum(int(row.get("budget", {}).get("spent_minor_units") or 0) for row in phases),
        "remaining_minor_units": sum(int(row.get("budget", {}).get("remaining_minor_units") or 0) for row in phases),
        "phase_ids_missing_budget_refs": missing,
        "phase_ids_blocked_by_budget": blocked,
        "phase_ids_conflicting_units": conflicting,
    }


def _campaign_state(phases: Sequence[Mapping[str, Any]], *, record_state: str, budget_status: str) -> str:
    states = [str(row.get("reconciled_state") or "") for row in phases]
    if any(state == "waiting_review" for state in states):
        return "review"
    if any(state == "failed" for state in states):
        return "failed"
    if states and all(state == "cancelled" for state in states):
        return "cancelled"
    if any(state == "blocked" for state in states) or budget_status.startswith("blocked"):
        return "blocked"
    if states and all(state == "succeeded" for state in states):
        return "completed"
    if any(state == "running" for state in states):
        return "active"
    if any(str(row.get("progress_state") or "") == "launchable" for row in phases):
        return "active"
    return record_state or "draft"


def reconcile_campaign_lifecycle(
    *,
    campaign_record: Mapping[str, Any],
    phase_gate_decisions: Sequence[Mapping[str, Any]] = (),
    phase_runtime_facts: Sequence[Mapping[str, Any]] = (),
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    """Reconcile campaign lifecycle and budget posture from durable facts."""

    record_validation = campaign_record_contract.validate_campaign_record(campaign_record)
    if not record_validation["ok"]:
        raise ValueError("campaign record must validate before lifecycle reconciliation")

    normalized_record = dict(record_validation["normalized_record"])
    phase_fact_map = {
        _normalize_text(row.get("phase_id")): row
        for row in phase_runtime_facts
        if isinstance(row, Mapping) and _normalize_text(row.get("phase_id"))
    }
    gates = _gate_map(phase_gate_decisions)
    root = Path(repo_root) if repo_root is not None else None

    reconciled_phases: List[Dict[str, Any]] = []
    launchable_phase_ids: List[str] = []
    blocked_phase_ids: List[str] = []
    review_phase_ids: List[str] = []
    terminal_phase_ids: List[str] = []

    terminal_success_by_id: Dict[str, bool] = {}
    for phase in list(normalized_record.get("phases") or []):
        phase_id = _normalize_text(phase.get("phase_id"))
        runtime_facts = _merge_runtime_facts(phase, phase_fact_map.get(phase_id) or {}, repo_root=root)
        budget = _phase_budget_rollup(runtime_facts["job_ids"], repo_root=root)
        reconciled = _phase_state(
            phase,
            gate=gates.get(phase_id),
            receipts=runtime_facts["receipts"],
            continuation_packet_ids=runtime_facts["continuation_packet_ids"],
            budget=budget,
        )

        depends_on = [dep for dep in _normalize_text_list(phase.get("depends_on")) if dep]
        if (
            reconciled["progress_state"] == "launchable"
            and all(terminal_success_by_id.get(dep, False) for dep in depends_on)
            and not blocked_phase_ids
            and not review_phase_ids
        ):
            launchable_phase_ids.append(phase_id)
        elif reconciled["progress_state"] == "launchable":
            reconciled["progress_state"] = "blocked"
            reconciled["reconciled_state"] = "blocked"
            reconciled["reasons"].append("dependency_not_terminal_success")
            blocked_phase_ids.append(phase_id)

        if reconciled["reconciled_state"] == "blocked" and phase_id not in blocked_phase_ids:
            blocked_phase_ids.append(phase_id)
        if reconciled["reconciled_state"] == "waiting_review":
            review_phase_ids.append(phase_id)
        if reconciled["reconciled_state"] in {"succeeded", "failed", "cancelled"}:
            terminal_phase_ids.append(phase_id)

        terminal_success_by_id[phase_id] = reconciled["reconciled_state"] == "succeeded"
        reconciled_phases.append(reconciled)

    budget_rollup = _campaign_budget_rollup(reconciled_phases)
    reconciled_campaign_state = _campaign_state(
        reconciled_phases,
        record_state=_normalize_text(normalized_record.get("campaign_state")).lower(),
        budget_status=_normalize_text(budget_rollup.get("status")),
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": _normalize_text(normalized_record.get("campaign_id")),
        "reconciled_campaign_state": reconciled_campaign_state,
        "launchable_phase_ids": launchable_phase_ids,
        "blocked_phase_ids": blocked_phase_ids,
        "review_phase_ids": review_phase_ids,
        "terminal_phase_ids": terminal_phase_ids,
        "budget_rollup": budget_rollup,
        "phases": reconciled_phases,
    }
