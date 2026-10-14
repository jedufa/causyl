"""Answer-vs-delegate policy and typed delegation contract (01A2).

This module owns:
- stable direct-answer vs delegate classification rules
- auditable reason codes for direct, delegated, blocked, and waiting states
- one typed delegation request schema for allowed v1 surfaces

It does not bind requests to real subsystem IDs or execute delegated work.
"""

from __future__ import annotations

import copy
import datetime as _dt
import uuid
from pathlib import Path
from typing import Any, Dict, List


POLICY_SCHEMA_VERSION = "lab_assistant_answer_delegate_policy_v1"
DELEGATION_REQUEST_SCHEMA_VERSION = "lab_assistant_typed_delegation_request_v1"
DELEGATION_RESULT_SCHEMA_VERSION = "lab_assistant_typed_delegation_result_v1"
DIRECT_ANSWER_WINDOW_S = 60
SHORT_WORK_WINDOW_S = 30
ALLOWED_V1_SURFACES = {
    "ledger",
    "execution_queue",
    "workflow",
    "scheduler",
    "watchdog",
    "healer",
    "verifier",
    "retrieval",
    "admin",
}
ENVELOPE_SURFACE_ALIASES = {
    "queue": "execution_queue",
    "turn_job": "direct_turn_job",
    "turn-job": "direct_turn_job",
    "direct-turn-job": "direct_turn_job",
}
ALLOWED_ENVELOPE_SURFACES = ALLOWED_V1_SURFACES | {"direct_turn_job", "mailbox"}
CLASSIFICATION_ALLOWED = {
    "answer_directly",
    "delegate",
    "blocked",
    "waiting_on_operator",
}
NEXT_DECISION_ALLOWED = {
    "assistant_continue",
    "operator_input",
    "review_evidence",
    "approve_launch",
    "completion_check",
}
RESULT_STATUS_ALLOWED = {
    "accepted",
    "waiting_on_job",
    "waiting_on_verification",
    "running",
    "completed",
    "partial",
    "failed",
    "timed_out",
    "interrupted",
    "superseded",
    "evidence_missing",
    "ambiguous_artifact",
}
RESULT_STATUS_ALIASES = {
    "queued": "accepted",
    "queueing": "accepted",
    "open": "running",
    "starting": "running",
    "idle": "running",
    "ok": "completed",
    "passed": "completed",
    "success": "completed",
    "error": "failed",
    "timeout": "timed_out",
    "timedout": "timed_out",
    "cancelled": "interrupted",
    "canceled": "interrupted",
}
EVIDENCE_STATUS_ALLOWED = {
    "none",
    "pending",
    "present",
    "missing",
    "ambiguous",
    "missing_native_record",
    "missing_result",
}
RESULT_CORRELATION_BASIS_ALLOWED = {
    "request_id",
    "correlation_id",
    "detached_job_id",
    "subsystem_id",
    "mailbox_path",
    "launch_turn_id",
}
MISSION_FIT_ALLOWED = {
    "mission_aligned",
    "partially_aligned",
    "mission_misaligned",
    "unclear",
}
MISSION_ALIGNED_SUMMARY_HINTS = (
    "lab",
    "laboratory",
    "science",
    "engineering",
    "fabrication",
    "materials",
    "structure",
    "software",
    "tooling",
    "experiment",
    "workflow",
    "scheduler",
    "detached job",
    "job lifecycle",
    "lifecycle",
    "delegated work",
    "regression work",
    "mailbox",
    "verifier",
    "retrieval",
    "execution queue",
    "execution_queue",
    "queue",
    "ledger",
    "direct turn",
    "direct_turn_job",
    "proof",
    "evidence",
    "writeback",
    "restart",
    "recovery",
    "continuation",
    "task register",
    "plane",
    "repo",
    "repository",
    "code",
)
MISSION_NON_GOAL_HINTS = (
    "freeform multi-agent social",
    "social behavior",
    "magical persistent thinking",
    "persistent thinking process",
    "lab director",
    "memory ranking",
    "embedding system",
    "activity theater",
    "feature growth that outruns validation",
)


class DelegationPolicyError(RuntimeError):
    """Raised when the 01A2 delegation contract is violated."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_bool(value: Any) -> bool:
    return bool(value)


def _clean_int(value: Any, *, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return int(default)


def _non_empty_reason_codes(reason_codes: List[str]) -> List[str]:
    cleaned = [str(code).strip() for code in reason_codes if str(code).strip()]
    if not cleaned:
        raise DelegationPolicyError("reason_codes must contain at least one non-empty reason")
    return cleaned


def _normalize_owner_ref(owner_ref: Any) -> Dict[str, str]:
    ref = owner_ref if isinstance(owner_ref, dict) else {}
    normalized = {
        "task_id": _clean_text(ref.get("task_id")),
        "ledger_uid": _clean_text(ref.get("ledger_uid")),
        "open_loop_id": _clean_text(ref.get("open_loop_id")),
    }
    if not any(normalized.values()):
        raise DelegationPolicyError("owner_ref requires at least one of task_id, ledger_uid, or open_loop_id")
    return normalized


def _normalize_surface(raw: Any, *, allow_future: bool) -> str:
    surface = _clean_text(raw).lower()
    surface = ENVELOPE_SURFACE_ALIASES.get(surface, surface)
    allowed = ALLOWED_ENVELOPE_SURFACES if allow_future else ALLOWED_V1_SURFACES
    if surface not in allowed:
        kind = "delegation surface"
        raise DelegationPolicyError(f"unsupported {kind}: {surface or '<missing>'}")
    return surface


def _normalize_expected_evidence(raw: Any) -> List[Dict[str, Any]]:
    rows = raw if isinstance(raw, list) else []
    normalized: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise DelegationPolicyError("expected_evidence rows must be objects")
        kind = _clean_text(row.get("kind"))
        description = _clean_text(row.get("description"))
        if not kind or not description:
            raise DelegationPolicyError("expected_evidence rows require kind and description")
        normalized.append(
            {
                "kind": kind,
                "description": description,
                "required": bool(row.get("required", True)),
            }
        )
    if not normalized:
        raise DelegationPolicyError("expected_evidence must declare at least one evidence requirement")
    if not any(bool(item.get("required")) for item in normalized):
        raise DelegationPolicyError("expected_evidence must include at least one required evidence item")
    return normalized


def _normalize_next_decision(raw: Any) -> Dict[str, str]:
    row = raw if isinstance(raw, dict) else {}
    kind = _clean_text(row.get("kind"))
    summary = _clean_text(row.get("summary"))
    if kind not in NEXT_DECISION_ALLOWED:
        raise DelegationPolicyError(f"invalid next_decision kind: {kind or '<missing>'}")
    if not summary:
        raise DelegationPolicyError("next_decision summary is required")
    return {
        "kind": kind,
        "summary": summary,
        "owner": _clean_text(row.get("owner") or "assistant"),
    }


def _normalize_string_list(raw: Any) -> List[str]:
    values = raw if isinstance(raw, list) else []
    out: List[str] = []
    for value in values:
        text = _clean_text(value)
        if text and text not in out:
            out.append(text)
    return out


def _normalize_mission_fit(raw: Any, *, summary_fallback: str = "") -> Dict[str, Any]:
    row = raw if isinstance(raw, dict) else {}
    status = _clean_text(row.get("status")).lower()
    if status and status not in MISSION_FIT_ALLOWED:
        raise DelegationPolicyError(f"invalid mission_fit status: {status}")
    note = _clean_text(row.get("note"))
    summary = _clean_text(row.get("summary") or summary_fallback)
    source = _clean_text(row.get("source") or "unspecified")
    requires_note = bool(row.get("requires_note"))
    return {
        "status": status,
        "note": note,
        "summary": summary,
        "source": source,
        "requires_note": requires_note,
    }


def _summary_contains_any(summary: str, hints: tuple[str, ...]) -> List[str]:
    folded = summary.casefold()
    return [hint for hint in hints if hint in folded]


def _classify_mission_fit(facts: Dict[str, Any]) -> Dict[str, Any]:
    explicit_status = _clean_text(facts.get("mission_alignment") or facts.get("mission_fit_status")).lower()
    if explicit_status and explicit_status not in MISSION_FIT_ALLOWED:
        raise DelegationPolicyError(f"invalid mission alignment: {explicit_status}")

    note = _clean_text(
        facts.get("mission_alignment_note")
        or facts.get("mission_fit_note")
        or facts.get("mission_mismatch_note")
    )
    summary = _clean_text(facts.get("summary"))
    if explicit_status:
        status = explicit_status
        source = "explicit"
    else:
        non_goal_hits = _summary_contains_any(summary, MISSION_NON_GOAL_HINTS)
        aligned_hits = _summary_contains_any(summary, MISSION_ALIGNED_SUMMARY_HINTS)
        if non_goal_hits:
            status = "mission_misaligned"
            source = "inferred_non_goal_summary"
        elif aligned_hits:
            status = "mission_aligned"
            source = "inferred_summary"
        elif summary:
            status = "unclear"
            source = "summary_without_mission_signal"
        else:
            status = "unclear"
            source = "missing_summary"

    requires_note = status == "partially_aligned"
    summary_by_status = {
        "mission_aligned": "Work fits the laboratory mission boundary.",
        "partially_aligned": "Work is only partially aligned with the laboratory mission and needs an explicit note.",
        "mission_misaligned": "Work falls into an explicit laboratory non-goal or mission mismatch.",
        "unclear": "Mission fit is under-evidenced and cannot be trusted for long-horizon execution.",
    }
    return {
        "status": status,
        "note": note,
        "summary": summary_by_status[status],
        "source": source,
        "requires_note": requires_note,
    }


def _normalize_result_status(raw: Any) -> str:
    status = _clean_text(raw).lower()
    status = RESULT_STATUS_ALIASES.get(status, status)
    if status not in RESULT_STATUS_ALLOWED:
        raise DelegationPolicyError(f"invalid result status: {status or '<missing>'}")
    return status


def _normalize_evidence_status(raw: Any) -> str:
    status = _clean_text(raw).lower()
    if status not in EVIDENCE_STATUS_ALLOWED:
        raise DelegationPolicyError(f"invalid evidence_status: {status or '<missing>'}")
    return status


def _normalize_result_correlation_basis(raw: Any, *, detached_job_id: str = "") -> str:
    basis = _clean_text(raw)
    if not basis:
        raise DelegationPolicyError("result_correlation_basis is required")
    if basis not in RESULT_CORRELATION_BASIS_ALLOWED:
        raise DelegationPolicyError(f"invalid result_correlation_basis: {basis}")
    if basis == "detached_job_id" and not detached_job_id:
        raise DelegationPolicyError("detached_job_id basis requires detached_job_id")
    return basis


def _normalize_request_provenance(row: Dict[str, Any], *, owner_ref: Dict[str, str], request_id: str) -> Dict[str, str]:
    detached_job_id = _clean_text(row.get("detached_job_id"))
    correlation_id = _clean_text(row.get("correlation_id"))
    launch_turn_id = _clean_text(row.get("launch_turn_id"))
    idempotency_key = _clean_text(row.get("idempotency_key"))
    launch_cause = _clean_text(row.get("launch_cause"))
    if not correlation_id:
        raise DelegationPolicyError("correlation_id is required")
    if not launch_turn_id:
        raise DelegationPolicyError("launch_turn_id is required")
    if not idempotency_key:
        raise DelegationPolicyError("idempotency_key is required")
    if not launch_cause:
        raise DelegationPolicyError("launch_cause is required")
    result_correlation_basis = _normalize_result_correlation_basis(
        row.get("result_correlation_basis"),
        detached_job_id=detached_job_id,
    )
    return {
        "request_id": request_id,
        "correlation_id": correlation_id,
        "launch_turn_id": launch_turn_id,
        "detached_job_id": detached_job_id,
        "result_correlation_basis": result_correlation_basis,
        "idempotency_key": idempotency_key,
        "launch_cause": launch_cause,
        "parent_request_id": _clean_text(row.get("parent_request_id")),
        "prior_result_id": _clean_text(row.get("prior_result_id")),
        "parent_ledger_uid": _clean_text(row.get("parent_ledger_uid") or owner_ref.get("ledger_uid")),
        "parent_task_id": _clean_text(row.get("parent_task_id") or owner_ref.get("task_id")),
        "parent_open_loop_id": _clean_text(row.get("parent_open_loop_id") or owner_ref.get("open_loop_id")),
    }


def allowed_surface_specs() -> List[Dict[str, str]]:
    return [
        {
            "surface": "ledger",
            "purpose": "create or update durable work items and authoritative work metadata",
        },
        {
            "surface": "execution_queue",
            "purpose": "run bounded implementation work that should outlive the current turn",
        },
        {
            "surface": "workflow",
            "purpose": "run bounded multi-step or multi-role implementation and investigation flows",
        },
        {
            "surface": "scheduler",
            "purpose": "schedule reminders, audits, or later background work",
        },
        {
            "surface": "watchdog",
            "purpose": "record watchdog recovery and stale-claim remediation actions",
        },
        {
            "surface": "healer",
            "purpose": "record pipeline healer repairs and recovery actions",
        },
        {
            "surface": "verifier",
            "purpose": "request focused proof when builder-local evidence is insufficient",
        },
        {
            "surface": "retrieval",
            "purpose": "inspect saved transcripts, artifacts, logs, or runtime state for evidence",
        },
        {
            "surface": "admin",
            "purpose": "use deploy, refresh, or operator controls only when the task explicitly calls for them",
        },
    ]


def classify_answer_vs_delegate(facts: Dict[str, Any]) -> Dict[str, Any]:
    """Classify a unit of work using deterministic, auditable rules."""
    surface = _clean_text(facts.get("preferred_surface")).lower()
    duration_s = max(0, _clean_int(facts.get("estimated_duration_s"), default=0))
    requires_operator_input = _clean_bool(facts.get("requires_operator_input"))
    unsafe_to_delegate = _clean_bool(facts.get("unsafe_to_delegate"))
    requires_new_evidence = _clean_bool(facts.get("requires_new_evidence"))
    requires_external_action = _clean_bool(facts.get("requires_external_action"))
    proof_heavy = _clean_bool(facts.get("proof_heavy"))
    multi_step = _clean_bool(facts.get("multi_step"))
    outlives_turn = _clean_bool(facts.get("outlives_turn"))
    input_summary = _clean_text(facts.get("summary"))
    mission_fit = _classify_mission_fit(facts)
    mission_status = mission_fit["status"]

    reasons: List[str] = []
    if requires_operator_input:
        reasons.append("operator_input_required")
        decision = "waiting_on_operator"
    elif unsafe_to_delegate:
        reasons.append("unsafe_to_delegate")
        decision = "blocked"
    elif surface and surface not in ALLOWED_V1_SURFACES:
        reasons.append("unsupported_delegation_surface")
        decision = "blocked"
    else:
        should_delegate = any(
            (
                duration_s > DIRECT_ANSWER_WINDOW_S,
                requires_new_evidence,
                requires_external_action,
                proof_heavy,
                multi_step,
                outlives_turn,
            )
        )
        if mission_status == "mission_misaligned":
            decision = "blocked"
            reasons.append("mission_misaligned")
        elif mission_status == "partially_aligned" and mission_fit["requires_note"] and not mission_fit["note"]:
            decision = "blocked"
            reasons.append("mission_alignment_note_required")
        elif should_delegate and mission_status == "unclear":
            decision = "blocked"
            reasons.append("mission_fit_unclear_for_long_horizon_work")
        elif should_delegate:
            decision = "delegate"
            if duration_s > DIRECT_ANSWER_WINDOW_S:
                reasons.append("exceeds_direct_answer_window")
            if requires_new_evidence:
                reasons.append("missing_required_evidence")
            if requires_external_action:
                reasons.append("requires_tool_surface")
            if proof_heavy:
                reasons.append("proof_heavy")
            if multi_step:
                reasons.append("multi_step")
            if outlives_turn:
                reasons.append("outlives_turn")
        else:
            decision = "answer_directly"
            if duration_s <= SHORT_WORK_WINDOW_S:
                reasons.append("short_local_work")
            else:
                reasons.append("fits_direct_answer_window")
            if not input_summary:
                reasons.append("summary_missing_but_not_required")

    if decision not in CLASSIFICATION_ALLOWED:
        raise DelegationPolicyError(f"invalid classification result: {decision}")
    reason_codes = _non_empty_reason_codes(reasons)
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "classification": decision,
        "reason_codes": reason_codes,
        "auditable_facts": {
            "preferred_surface": surface,
            "estimated_duration_s": duration_s,
            "requires_operator_input": requires_operator_input,
            "unsafe_to_delegate": unsafe_to_delegate,
            "requires_new_evidence": requires_new_evidence,
            "requires_external_action": requires_external_action,
            "proof_heavy": proof_heavy,
            "multi_step": multi_step,
            "outlives_turn": outlives_turn,
            "summary": input_summary,
            "mission_fit": mission_fit,
        },
        "mission_fit": mission_fit,
    }


def validate_typed_delegation_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Validate and normalize the authoritative delegation-request record."""
    row = copy.deepcopy(request if isinstance(request, dict) else {})
    schema_version = _clean_text(row.get("schema_version") or DELEGATION_REQUEST_SCHEMA_VERSION)
    if schema_version != DELEGATION_REQUEST_SCHEMA_VERSION:
        raise DelegationPolicyError(f"incompatible schema_version: {schema_version or '<missing>'}")
    surface = _normalize_surface(row.get("surface"), allow_future=True)
    owner_ref = _normalize_owner_ref(row.get("owner_ref"))
    classification = _clean_text(row.get("classification")).lower()
    if classification != "delegate":
        raise DelegationPolicyError("typed delegation requests require classification=delegate")
    reason_codes = _non_empty_reason_codes(list(row.get("reason_codes") or []))
    summary = _clean_text(row.get("summary"))
    if not summary:
        raise DelegationPolicyError("summary is required")
    request_id = _clean_text(row.get("request_id"))
    if not request_id:
        raise DelegationPolicyError("request_id is required")
    requested_action = _clean_text(row.get("requested_action"))
    if not requested_action:
        raise DelegationPolicyError("requested_action is required")
    expected_evidence = _normalize_expected_evidence(row.get("expected_evidence"))
    next_decision = _normalize_next_decision(row.get("next_decision"))
    input_payload = row.get("input_payload") if isinstance(row.get("input_payload"), dict) else {}
    mission_fit = _normalize_mission_fit(
        row.get("mission_fit") or input_payload.get("mission_fit"),
        summary_fallback="Mission fit was not recorded on the delegated request.",
    )
    if not mission_fit["status"]:
        mission_fit = _classify_mission_fit(
            {
                "summary": summary,
                "preferred_surface": surface,
                "requires_new_evidence": bool(expected_evidence),
            }
        )
    provenance = _normalize_request_provenance(row, owner_ref=owner_ref, request_id=request_id)
    normalized_input_payload = dict(input_payload)
    normalized_input_payload["mission_fit"] = dict(mission_fit)
    normalized = {
        "schema_version": DELEGATION_REQUEST_SCHEMA_VERSION,
        "request_id": request_id,
        "created_utc": _clean_text(row.get("created_utc") or _utc_now_iso()),
        "surface": surface,
        "classification": "delegate",
        "reason_codes": reason_codes,
        "owner_ref": owner_ref,
        "summary": summary,
        "requested_action": requested_action,
        "input_payload": normalized_input_payload,
        "expected_evidence": expected_evidence,
        "next_decision": next_decision,
        "mission_fit": mission_fit,
        "status_on_reject": _clean_text(row.get("status_on_reject") or "blocked"),
        **provenance,
    }
    if normalized["status_on_reject"] not in {"blocked", "waiting_on_operator"}:
        raise DelegationPolicyError("status_on_reject must be blocked or waiting_on_operator")
    return normalized


def build_typed_delegation_request(
    *,
    classification: Dict[str, Any],
    surface: str,
    owner_ref: Dict[str, Any],
    summary: str,
    requested_action: str,
    expected_evidence: List[Dict[str, Any]],
    next_decision: Dict[str, Any],
    input_payload: Dict[str, Any] | None = None,
    request_id: str | None = None,
    correlation_id: str | None = None,
    launch_turn_id: str | None = None,
    detached_job_id: str | None = None,
    result_correlation_basis: str = "request_id",
    idempotency_key: str | None = None,
    launch_cause: str = "assistant_turn",
    parent_request_id: str | None = None,
    prior_result_id: str | None = None,
) -> Dict[str, Any]:
    """Create a canonical request from a prior delegate classification."""
    normalized_classification = classification if isinstance(classification, dict) else {}
    if _clean_text(normalized_classification.get("classification")).lower() != "delegate":
        raise DelegationPolicyError("cannot build typed delegation request from a non-delegate classification")
    generated_request_id = _clean_text(request_id or f"delegate_{uuid.uuid4().hex}")
    return validate_typed_delegation_request(
        {
            "schema_version": DELEGATION_REQUEST_SCHEMA_VERSION,
            "request_id": generated_request_id,
            "surface": surface,
            "classification": "delegate",
            "reason_codes": list(normalized_classification.get("reason_codes") or []),
            "owner_ref": owner_ref,
            "summary": summary,
            "requested_action": requested_action,
            "input_payload": dict(input_payload or {}),
            "expected_evidence": expected_evidence,
            "next_decision": next_decision,
            "mission_fit": dict(normalized_classification.get("mission_fit") or {}),
            "correlation_id": _clean_text(correlation_id or f"corr_{uuid.uuid4().hex}"),
            "launch_turn_id": _clean_text(launch_turn_id or f"turn_{uuid.uuid4().hex}"),
            "detached_job_id": _clean_text(detached_job_id),
            "result_correlation_basis": result_correlation_basis,
            "idempotency_key": _clean_text(idempotency_key or generated_request_id),
            "launch_cause": launch_cause,
            "parent_request_id": _clean_text(parent_request_id),
            "prior_result_id": _clean_text(prior_result_id),
        }
    )


def validate_typed_delegation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    row = copy.deepcopy(result if isinstance(result, dict) else {})
    schema_version = _clean_text(row.get("schema_version") or DELEGATION_RESULT_SCHEMA_VERSION)
    if schema_version != DELEGATION_RESULT_SCHEMA_VERSION:
        raise DelegationPolicyError(f"incompatible schema_version: {schema_version or '<missing>'}")
    result_id = _clean_text(row.get("result_id"))
    if not result_id:
        raise DelegationPolicyError("result_id is required")
    request_id = _clean_text(row.get("request_id"))
    if not request_id:
        raise DelegationPolicyError("request_id is required")
    surface = _normalize_surface(row.get("surface"), allow_future=True)
    detached_job_id = _clean_text(row.get("detached_job_id"))
    status = _normalize_result_status(row.get("status"))
    evidence_status = _normalize_evidence_status(row.get("evidence_status"))
    summary = _clean_text(row.get("summary"))
    if not summary:
        raise DelegationPolicyError("summary is required")
    next_decision = _normalize_next_decision(row.get("next_decision"))
    correlation_id = _clean_text(row.get("correlation_id"))
    launch_turn_id = _clean_text(row.get("launch_turn_id"))
    if not correlation_id:
        raise DelegationPolicyError("correlation_id is required")
    if not launch_turn_id:
        raise DelegationPolicyError("launch_turn_id is required")
    result_correlation_basis = _normalize_result_correlation_basis(
        row.get("result_correlation_basis"),
        detached_job_id=detached_job_id,
    )
    evidence_refs = _normalize_string_list(row.get("evidence_refs"))
    artifact_refs = _normalize_string_list(row.get("artifact_refs"))
    return {
        "schema_version": DELEGATION_RESULT_SCHEMA_VERSION,
        "result_id": result_id,
        "request_id": request_id,
        "surface": surface,
        "status": status,
        "summary": summary,
        "native_status": _clean_text(row.get("native_status")).lower(),
        "evidence_status": evidence_status,
        "evidence_refs": evidence_refs,
        "artifact_refs": artifact_refs,
        "next_decision": next_decision,
        "created_utc": _clean_text(row.get("created_utc") or _utc_now_iso()),
        "updated_utc": _clean_text(row.get("updated_utc") or _utc_now_iso()),
        "correlation_id": correlation_id,
        "launch_turn_id": launch_turn_id,
        "detached_job_id": detached_job_id,
        "result_correlation_basis": result_correlation_basis,
        "worker_ref": _clean_text(row.get("worker_ref")),
        "producer": _clean_text(row.get("producer")),
        "launch_cause": _clean_text(row.get("launch_cause")),
        "parent_request_id": _clean_text(row.get("parent_request_id")),
        "prior_result_id": _clean_text(row.get("prior_result_id")),
        "owner_ref": _normalize_owner_ref(row.get("owner_ref")),
    }


def build_typed_delegation_result(
    *,
    request: Dict[str, Any],
    status: str,
    summary: str,
    native_status: str,
    evidence_status: str,
    evidence_refs: List[str] | None,
    next_decision: Dict[str, Any],
    artifact_refs: List[str] | None = None,
    result_id: str | None = None,
    updated_utc: str | None = None,
    worker_ref: str | None = None,
    producer: str | None = None,
    prior_result_id: str | None = None,
) -> Dict[str, Any]:
    normalized_request = validate_typed_delegation_request(request)
    return validate_typed_delegation_result(
        {
            "schema_version": DELEGATION_RESULT_SCHEMA_VERSION,
            "result_id": _clean_text(result_id or f"result_{uuid.uuid4().hex}"),
            "request_id": normalized_request["request_id"],
            "surface": normalized_request["surface"],
            "status": status,
            "summary": summary,
            "native_status": native_status,
            "evidence_status": evidence_status,
            "evidence_refs": list(evidence_refs or []),
            "artifact_refs": list(artifact_refs or []),
            "next_decision": next_decision,
            "updated_utc": _clean_text(updated_utc or _utc_now_iso()),
            "correlation_id": normalized_request["correlation_id"],
            "launch_turn_id": normalized_request["launch_turn_id"],
            "detached_job_id": normalized_request["detached_job_id"],
            "result_correlation_basis": normalized_request["result_correlation_basis"],
            "worker_ref": _clean_text(worker_ref),
            "producer": _clean_text(producer),
            "launch_cause": normalized_request["launch_cause"],
            "parent_request_id": normalized_request["parent_request_id"],
            "prior_result_id": _clean_text(prior_result_id or normalized_request["prior_result_id"]),
            "owner_ref": dict(normalized_request["owner_ref"]),
        }
    )


def delegation_policy_snapshot(*, repo_root: Path | None = None) -> Dict[str, Any]:
    """Return a read-only summary of the current 01A2 contract."""
    _ = repo_root
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "utc": _utc_now_iso(),
        "direct_answer_window_s": DIRECT_ANSWER_WINDOW_S,
        "short_work_window_s": SHORT_WORK_WINDOW_S,
        "allowed_surfaces": allowed_surface_specs(),
        "allowed_envelope_surfaces": sorted(ALLOWED_ENVELOPE_SURFACES),
        "supported_classifications": sorted(CLASSIFICATION_ALLOWED),
        "next_decision_kinds": sorted(NEXT_DECISION_ALLOWED),
        "result_statuses": sorted(RESULT_STATUS_ALLOWED),
        "evidence_statuses": sorted(EVIDENCE_STATUS_ALLOWED),
        "result_correlation_basis": sorted(RESULT_CORRELATION_BASIS_ALLOWED),
        "mission_fit_statuses": sorted(MISSION_FIT_ALLOWED),
        "blocked_fail_closed_reasons": [
            "unsupported_delegation_surface",
            "unsafe_to_delegate",
            "mission_misaligned",
            "mission_alignment_note_required",
            "mission_fit_unclear_for_long_horizon_work",
            "typed_request_missing_required_fields",
        ],
        "notes": [
            "Delegate when work is long-running, proof-heavy, multi-step, or requires tool-mediated evidence.",
            "Direct answers require enough current evidence inside the brief turn window.",
            "Long-horizon delegated work must also be mission-aligned; partial alignment needs an explicit note and unclear fit fails closed.",
            "Typed delegation requests must declare expected evidence, next decision, and durable provenance before launch.",
            "Typed delegation results normalize status, evidence, and correlation fields across substrates.",
            "Binding to real subsystem IDs belongs to DF0C, not this policy contract.",
        ],
    }


def render_answer_delegate_policy_text(*, repo_root: Path | None = None) -> str:
    snapshot = delegation_policy_snapshot(repo_root=repo_root)
    lines = [
        "Answer-vs-delegate policy:",
        (
            f"- answer directly only for short local work inside {snapshot['direct_answer_window_s']}s "
            "when current evidence is already available"
        ),
        "- delegate when work is long-running, multi-step, proof-heavy, or needs tool-mediated evidence",
        "- fail closed for unsafe or unsupported delegation; use blocked or waiting_on_operator instead",
        "- every delegated request must declare owner_ref, expected_evidence, and next_decision before launch",
        "- allowed v1 surfaces: "
        + ", ".join(spec["surface"] for spec in snapshot["allowed_surfaces"]),
    ]
    return "\n".join(lines)
