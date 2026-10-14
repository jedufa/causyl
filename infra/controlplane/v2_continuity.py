"""V2 continuity state, recovery policy, and durable receipt helpers.

This module defines the narrow continuity contract owned by
PIS-20260409-195203Z-D1Y4. It does not launch work, retire jobs, or promote
closure. It classifies recovery posture from durable runtime facts and records
recovery decisions as append-only receipts.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple


CONTRACT_ARTIFACT_REL = Path("governance/v2_continuity_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_v2_continuity_engine_recovery_v1.md")
RECEIPTS_ROOT_REL = Path("runtime/state/controlplane/v2_continuity/receipts")

SCHEMA_VERSION = "v2_continuity_contract_v1"
RECEIPT_SCHEMA_VERSION = "v2_continuity_receipt_v1"
UID = "PIS-20260409-195203Z-D1Y4"

STATE_INTERRUPTED = "interrupted"
STATE_RESUME_READY = "resume_ready"
STATE_RERUN_READY = "rerun_ready"
STATE_REPAIRING = "repairing"
STATE_BLOCKED = "blocked"
STATE_FAIL_CLOSED = "fail_closed"

RECOVERY_RESUME = "resume"
RECOVERY_RERUN = "rerun"
RECOVERY_REPAIR_THEN_RERUN = "repair_then_rerun"
RECOVERY_OPERATOR_BLOCK = "operator_block"
RECOVERY_FAIL_CLOSED = "fail_closed"

ALLOWED_STATES: Tuple[str, ...] = (
    STATE_INTERRUPTED,
    STATE_RESUME_READY,
    STATE_RERUN_READY,
    STATE_REPAIRING,
    STATE_BLOCKED,
    STATE_FAIL_CLOSED,
)
ALLOWED_OUTCOMES: Tuple[str, ...] = (
    RECOVERY_RESUME,
    RECOVERY_RERUN,
    RECOVERY_REPAIR_THEN_RERUN,
    RECOVERY_OPERATOR_BLOCK,
    RECOVERY_FAIL_CLOSED,
)
ALLOWED_SUBJECT_KINDS: Tuple[str, ...] = ("job", "family")
ALLOWED_POST_REPAIR_ACTIONS: Tuple[str, ...] = ("rerun", "focused_revalidation")

STATE_DEFINITIONS: Mapping[str, str] = {
    STATE_INTERRUPTED: "Durable runtime records show work stopped mid-flight and no continuity decision has been durably recorded yet.",
    STATE_RESUME_READY: "A replay-safe durable checkpoint exists, so continuity may resume from checkpoint truth.",
    STATE_RERUN_READY: "Resume is not authorized or not possible; restart must occur from a clean rerun or focused revalidation boundary.",
    STATE_REPAIRING: "A bounded repair is allowed, but repair may only prepare the next rerun or focused revalidation step.",
    STATE_BLOCKED: "Continuation is intentionally held for operator or dependency action instead of being guessed.",
    STATE_FAIL_CLOSED: "Continuity cannot be proven safely from durable facts, so the system must stop without promoting truth.",
}
OUTCOME_DEFINITIONS: Mapping[str, str] = {
    RECOVERY_RESUME: "Resume from durable checkpoint truth without inventing new work.",
    RECOVERY_RERUN: "Launch a fresh rerun or focused revalidation from an explicit boundary.",
    RECOVERY_REPAIR_THEN_RERUN: "Apply a bounded repair, then rerun or focused revalidate before any later promotion.",
    RECOVERY_OPERATOR_BLOCK: "Stop and surface the block explicitly for operator or dependency handling.",
    RECOVERY_FAIL_CLOSED: "Stop because the durable facts are insufficient or conflicting.",
}
DECISION_PRECEDENCE: Tuple[str, ...] = (
    "conflicting_truth_or_unbounded_gap",
    "explicit_operator_or_dependency_block",
    "bounded_repair_required",
    "resume_from_checkpoint",
    "rerun_or_revalidate",
    "otherwise_fail_closed",
)


class V2ContinuityError(ValueError):
    """Raised when v2 continuity inputs or receipts fail closed."""


@dataclass(frozen=True)
class V2ContinuityContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    receipt_schema_version: str
    allowed_states: Tuple[str, ...]
    allowed_outcomes: Tuple[str, ...]


@dataclass(frozen=True)
class ContinuityDecision:
    continuity_state: str
    recovery_outcome: str
    reason: str
    subject_kind: str
    subject_id: str
    workflow_kind: str
    bounded_repair_allowed: bool
    post_repair_action: str
    checkpoint_id: str
    revalidation_required: bool
    decision_source: str


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _validate_identifier(value: Any, *, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise V2ContinuityError(f"{field_name} must be a non-empty string")
    allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-")
    if any(ch not in allowed for ch in text):
        raise V2ContinuityError(
            f"{field_name} must contain only letters, digits, '.', '_', ':', or '-'"
        )
    return text


def _safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in value)


def _write_json_atomic(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(dict(obj), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def v2_continuity_contract_v1() -> V2ContinuityContractV1:
    return V2ContinuityContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        receipt_schema_version=RECEIPT_SCHEMA_VERSION,
        allowed_states=ALLOWED_STATES,
        allowed_outcomes=ALLOWED_OUTCOMES,
    )


def _normalize_subject_kind(value: Any) -> str:
    subject_kind = str(value or "").strip().lower()
    if subject_kind not in ALLOWED_SUBJECT_KINDS:
        raise V2ContinuityError(f"subject_kind must be one of {ALLOWED_SUBJECT_KINDS}")
    return subject_kind


def normalize_continuity_record(record: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(record or {})
    subject_kind = _normalize_subject_kind(raw.get("subject_kind"))
    subject_id = _validate_identifier(raw.get("subject_id"), field_name="subject_id")
    workflow_kind = _validate_identifier(raw.get("workflow_kind") or "intent_family_v2", field_name="workflow_kind")
    checkpoint_id = str(raw.get("checkpoint_id") or "").strip()
    if checkpoint_id:
        checkpoint_id = _validate_identifier(checkpoint_id, field_name="checkpoint_id")
    return {
        "subject_kind": subject_kind,
        "subject_id": subject_id,
        "workflow_kind": workflow_kind,
        "runtime_state": str(raw.get("runtime_state") or "").strip(),
        "interrupted": bool(raw.get("interrupted", False)),
        "replay_safe": bool(raw.get("replay_safe", False)),
        "checkpoint_id": checkpoint_id,
        "rerun_allowed": bool(raw.get("rerun_allowed", False)),
        "repair_needed": bool(raw.get("repair_needed", False)),
        "repair_allowed": bool(raw.get("repair_allowed", False)),
        "revalidation_required": bool(raw.get("revalidation_required", False)),
        "operator_block_reason": str(raw.get("operator_block_reason") or "").strip(),
        "proof_gap_reason": str(raw.get("proof_gap_reason") or "").strip(),
        "conflicting_runtime_truth": bool(raw.get("conflicting_runtime_truth", False)),
    }


def observed_continuity_state(record: Mapping[str, Any]) -> str:
    normalized = normalize_continuity_record(record)
    if normalized["conflicting_runtime_truth"] or normalized["proof_gap_reason"]:
        return STATE_FAIL_CLOSED
    if normalized["operator_block_reason"]:
        return STATE_BLOCKED
    if normalized["repair_needed"] and normalized["repair_allowed"]:
        return STATE_REPAIRING
    if normalized["interrupted"]:
        return STATE_INTERRUPTED
    if normalized["revalidation_required"] and normalized["rerun_allowed"]:
        return STATE_RERUN_READY
    return STATE_FAIL_CLOSED


def decide_continuity_recovery(record: Mapping[str, Any]) -> ContinuityDecision:
    normalized = normalize_continuity_record(record)
    if normalized["conflicting_runtime_truth"] or normalized["proof_gap_reason"]:
        return ContinuityDecision(
            continuity_state=STATE_FAIL_CLOSED,
            recovery_outcome=RECOVERY_FAIL_CLOSED,
            reason=normalized["proof_gap_reason"] or "conflicting_runtime_truth",
            subject_kind=normalized["subject_kind"],
            subject_id=normalized["subject_id"],
            workflow_kind=normalized["workflow_kind"],
            bounded_repair_allowed=False,
            post_repair_action="",
            checkpoint_id="",
            revalidation_required=bool(normalized["revalidation_required"]),
            decision_source="runtime_record",
        )
    if normalized["operator_block_reason"]:
        return ContinuityDecision(
            continuity_state=STATE_BLOCKED,
            recovery_outcome=RECOVERY_OPERATOR_BLOCK,
            reason=normalized["operator_block_reason"],
            subject_kind=normalized["subject_kind"],
            subject_id=normalized["subject_id"],
            workflow_kind=normalized["workflow_kind"],
            bounded_repair_allowed=False,
            post_repair_action="",
            checkpoint_id="",
            revalidation_required=bool(normalized["revalidation_required"]),
            decision_source="runtime_record",
        )
    if normalized["repair_needed"]:
        if not normalized["repair_allowed"]:
            return ContinuityDecision(
                continuity_state=STATE_FAIL_CLOSED,
                recovery_outcome=RECOVERY_FAIL_CLOSED,
                reason="repair_required_but_not_policy_allowed",
                subject_kind=normalized["subject_kind"],
                subject_id=normalized["subject_id"],
                workflow_kind=normalized["workflow_kind"],
                bounded_repair_allowed=False,
                post_repair_action="",
                checkpoint_id="",
                revalidation_required=True,
                decision_source="runtime_record",
            )
        return ContinuityDecision(
            continuity_state=STATE_REPAIRING,
            recovery_outcome=RECOVERY_REPAIR_THEN_RERUN,
            reason="bounded_repair_required_before_rerun",
            subject_kind=normalized["subject_kind"],
            subject_id=normalized["subject_id"],
            workflow_kind=normalized["workflow_kind"],
            bounded_repair_allowed=True,
            post_repair_action="focused_revalidation" if normalized["revalidation_required"] else "rerun",
            checkpoint_id="",
            revalidation_required=True,
            decision_source="runtime_record",
        )
    if normalized["interrupted"] and normalized["checkpoint_id"] and normalized["replay_safe"]:
        return ContinuityDecision(
            continuity_state=STATE_RESUME_READY,
            recovery_outcome=RECOVERY_RESUME,
            reason="durable_checkpoint_replay_safe",
            subject_kind=normalized["subject_kind"],
            subject_id=normalized["subject_id"],
            workflow_kind=normalized["workflow_kind"],
            bounded_repair_allowed=False,
            post_repair_action="",
            checkpoint_id=normalized["checkpoint_id"],
            revalidation_required=bool(normalized["revalidation_required"]),
            decision_source="runtime_record",
        )
    if (normalized["interrupted"] or normalized["revalidation_required"]) and normalized["rerun_allowed"]:
        return ContinuityDecision(
            continuity_state=STATE_RERUN_READY,
            recovery_outcome=RECOVERY_RERUN,
            reason="explicit_rerun_boundary_required",
            subject_kind=normalized["subject_kind"],
            subject_id=normalized["subject_id"],
            workflow_kind=normalized["workflow_kind"],
            bounded_repair_allowed=False,
            post_repair_action="",
            checkpoint_id="",
            revalidation_required=bool(normalized["revalidation_required"]),
            decision_source="runtime_record",
        )
    if normalized["interrupted"]:
        return ContinuityDecision(
            continuity_state=STATE_FAIL_CLOSED,
            recovery_outcome=RECOVERY_FAIL_CLOSED,
            reason="interrupted_without_resume_or_rerun_authority",
            subject_kind=normalized["subject_kind"],
            subject_id=normalized["subject_id"],
            workflow_kind=normalized["workflow_kind"],
            bounded_repair_allowed=False,
            post_repair_action="",
            checkpoint_id="",
            revalidation_required=bool(normalized["revalidation_required"]),
            decision_source="runtime_record",
        )
    return ContinuityDecision(
        continuity_state=STATE_BLOCKED,
        recovery_outcome=RECOVERY_OPERATOR_BLOCK,
        reason="no_recovery_trigger_provided",
        subject_kind=normalized["subject_kind"],
        subject_id=normalized["subject_id"],
        workflow_kind=normalized["workflow_kind"],
        bounded_repair_allowed=False,
        post_repair_action="",
        checkpoint_id="",
        revalidation_required=bool(normalized["revalidation_required"]),
        decision_source="runtime_record",
    )


def _decision_payload(decision: ContinuityDecision) -> Dict[str, Any]:
    return {
        "continuity_state": decision.continuity_state,
        "recovery_outcome": decision.recovery_outcome,
        "reason": decision.reason,
        "subject_kind": decision.subject_kind,
        "subject_id": decision.subject_id,
        "workflow_kind": decision.workflow_kind,
        "bounded_repair_allowed": bool(decision.bounded_repair_allowed),
        "post_repair_action": decision.post_repair_action,
        "checkpoint_id": decision.checkpoint_id,
        "revalidation_required": bool(decision.revalidation_required),
        "decision_source": decision.decision_source,
    }


def continuity_receipt_fingerprint(payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_continuity_receipt(receipt: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(receipt or {})
    if str(raw.get("schema_version") or "") != RECEIPT_SCHEMA_VERSION:
        raise V2ContinuityError("receipt schema_version must be v2_continuity_receipt_v1")
    normalized = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "decision_id": _validate_identifier(raw.get("decision_id"), field_name="decision_id"),
        "decision_utc": str(raw.get("decision_utc") or "").strip(),
        "subject_kind": _normalize_subject_kind(raw.get("subject_kind")),
        "subject_id": _validate_identifier(raw.get("subject_id"), field_name="subject_id"),
        "workflow_kind": _validate_identifier(raw.get("workflow_kind") or "intent_family_v2", field_name="workflow_kind"),
        "continuity_state": str(raw.get("continuity_state") or "").strip(),
        "recovery_outcome": str(raw.get("recovery_outcome") or "").strip(),
        "reason": str(raw.get("reason") or "").strip(),
        "bounded_repair_allowed": bool(raw.get("bounded_repair_allowed", False)),
        "post_repair_action": str(raw.get("post_repair_action") or "").strip(),
        "checkpoint_id": str(raw.get("checkpoint_id") or "").strip(),
        "revalidation_required": bool(raw.get("revalidation_required", False)),
        "decision_source": str(raw.get("decision_source") or "").strip(),
        "promoted_terminal_state": str(raw.get("promoted_terminal_state") or "").strip(),
        "evidence_refs": [str(v).strip() for v in list(raw.get("evidence_refs") or []) if str(v).strip()],
        "runtime_record": normalize_continuity_record(raw.get("runtime_record") or {}),
    }
    if normalized["continuity_state"] not in ALLOWED_STATES:
        raise V2ContinuityError(f"continuity_state must be one of {ALLOWED_STATES}")
    if normalized["recovery_outcome"] not in ALLOWED_OUTCOMES:
        raise V2ContinuityError(f"recovery_outcome must be one of {ALLOWED_OUTCOMES}")
    if normalized["promoted_terminal_state"]:
        raise V2ContinuityError("continuity receipts may not promote terminal or closure state directly")
    if normalized["recovery_outcome"] == RECOVERY_REPAIR_THEN_RERUN:
        if not normalized["bounded_repair_allowed"]:
            raise V2ContinuityError("repair_then_rerun receipts must set bounded_repair_allowed=true")
        if normalized["post_repair_action"] not in ALLOWED_POST_REPAIR_ACTIONS:
            raise V2ContinuityError("repair_then_rerun receipts must require rerun or focused_revalidation")
    elif normalized["post_repair_action"]:
        raise V2ContinuityError("post_repair_action is only legal for repair_then_rerun receipts")
    expected = continuity_receipt_fingerprint({k: v for k, v in normalized.items() if k != "receipt_fingerprint"})
    fingerprint = str(raw.get("receipt_fingerprint") or "").strip()
    if fingerprint and fingerprint != expected:
        raise V2ContinuityError("receipt_fingerprint does not match receipt payload")
    normalized["receipt_fingerprint"] = expected
    return normalized


def build_continuity_receipt(
    decision: ContinuityDecision,
    *,
    runtime_record: Mapping[str, Any],
    evidence_refs: Tuple[str, ...] | list[str] = (),
    decision_id: str = "",
    decision_utc: str = "",
) -> Dict[str, Any]:
    normalized_runtime = normalize_continuity_record(runtime_record)
    chosen_id = _validate_identifier(
        decision_id or f"{decision.subject_kind}:{decision.subject_id}:{decision.recovery_outcome}",
        field_name="decision_id",
    )
    payload: Dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "decision_id": chosen_id,
        "decision_utc": str(decision_utc or _utc_now()),
        **_decision_payload(decision),
        "promoted_terminal_state": "",
        "evidence_refs": [str(v).strip() for v in evidence_refs if str(v).strip()],
        "runtime_record": normalized_runtime,
    }
    payload["receipt_fingerprint"] = continuity_receipt_fingerprint(payload)
    return validate_continuity_receipt(payload)


def continuity_receipts_root(*, repo_root: Path | None = None) -> Path:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    return (root / RECEIPTS_ROOT_REL).resolve()


def continuity_receipt_path(
    subject_kind: str,
    subject_id: str,
    decision_id: str,
    *,
    repo_root: Path | None = None,
) -> Path:
    return (
        continuity_receipts_root(repo_root=repo_root)
        / _safe_name(_normalize_subject_kind(subject_kind))
        / _safe_name(_validate_identifier(subject_id, field_name="subject_id"))
        / f"{_safe_name(_validate_identifier(decision_id, field_name='decision_id'))}.json"
    ).resolve()


def write_continuity_receipt(receipt: Mapping[str, Any], *, repo_root: Path | None = None) -> Path:
    normalized = validate_continuity_receipt(receipt)
    path = continuity_receipt_path(
        normalized["subject_kind"],
        normalized["subject_id"],
        normalized["decision_id"],
        repo_root=repo_root,
    )
    if path.exists():
        existing = validate_continuity_receipt(json.loads(path.read_text(encoding="utf-8")))
        if existing["receipt_fingerprint"] != normalized["receipt_fingerprint"]:
            raise V2ContinuityError("refusing to overwrite an existing continuity receipt with different content")
        return path
    _write_json_atomic(path, normalized)
    return path


def load_continuity_receipt(path: Path | str) -> Dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_continuity_receipt(raw)


def latest_continuity_receipt(
    subject_kind: str,
    subject_id: str,
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any] | None:
    folder = (
        continuity_receipts_root(repo_root=repo_root)
        / _safe_name(_normalize_subject_kind(subject_kind))
        / _safe_name(_validate_identifier(subject_id, field_name="subject_id"))
    )
    if not folder.exists():
        return None
    latest: Dict[str, Any] | None = None
    for path in sorted(folder.glob("*.json")):
        receipt = load_continuity_receipt(path)
        if latest is None or (
            str(receipt["decision_utc"]),
            str(receipt["decision_id"]),
        ) > (
            str(latest["decision_utc"]),
            str(latest["decision_id"]),
        ):
            latest = receipt
    return latest


def classify_continuity_from_durable_records(
    runtime_record: Mapping[str, Any],
    *,
    latest_receipt: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized_runtime = normalize_continuity_record(runtime_record)
    if latest_receipt is not None:
        normalized_receipt = validate_continuity_receipt(latest_receipt)
        if (
            normalized_receipt["subject_kind"] != normalized_runtime["subject_kind"]
            or normalized_receipt["subject_id"] != normalized_runtime["subject_id"]
        ):
            raise V2ContinuityError("latest_receipt does not match the supplied runtime record subject")
        return {
            "subject_kind": normalized_runtime["subject_kind"],
            "subject_id": normalized_runtime["subject_id"],
            "workflow_kind": normalized_runtime["workflow_kind"],
            "continuity_state": normalized_receipt["continuity_state"],
            "recovery_outcome": normalized_receipt["recovery_outcome"],
            "reason": normalized_receipt["reason"],
            "decision_source": "durable_receipt",
            "receipt_fingerprint": normalized_receipt["receipt_fingerprint"],
        }
    observed = observed_continuity_state(normalized_runtime)
    return {
        "subject_kind": normalized_runtime["subject_kind"],
        "subject_id": normalized_runtime["subject_id"],
        "workflow_kind": normalized_runtime["workflow_kind"],
        "continuity_state": observed,
        "recovery_outcome": "",
        "reason": "runtime_observation_only",
        "decision_source": "runtime_record",
        "receipt_fingerprint": "",
    }


def validate_v2_continuity_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    artifact = json.loads((root / CONTRACT_ARTIFACT_REL).read_text(encoding="utf-8"))
    issues = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version drifted from runtime continuity contract")
    if artifact.get("uid") != UID:
        issues.append("uid drifted from runtime continuity contract")
    if tuple(artifact.get("allowed_states") or ()) != ALLOWED_STATES:
        issues.append("allowed_states drifted from runtime continuity contract")
    if tuple(artifact.get("allowed_outcomes") or ()) != ALLOWED_OUTCOMES:
        issues.append("allowed_outcomes drifted from runtime continuity contract")
    if tuple(artifact.get("decision_precedence") or ()) != DECISION_PRECEDENCE:
        issues.append("decision_precedence drifted from runtime continuity contract")
    receipt_policy = artifact.get("receipt_policy") if isinstance(artifact.get("receipt_policy"), dict) else {}
    if receipt_policy.get("schema_version") != RECEIPT_SCHEMA_VERSION:
        issues.append("receipt schema_version drifted from runtime continuity contract")
    if tuple(receipt_policy.get("allowed_post_repair_actions") or ()) != ALLOWED_POST_REPAIR_ACTIONS:
        issues.append("allowed_post_repair_actions drifted from runtime continuity contract")
    return tuple(issues)
