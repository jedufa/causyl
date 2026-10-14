"""Campaign phase-gate and handoff contract helpers.

This module defines the durable gate-law surface for campaign phases. It does
not launch work or reconcile runtime state; it only validates whether a phase
decision and its handoff packet are explicit enough to unblock downstream work.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Tuple

from infra.controlplane import evidence_pack_handoff_refs


SCHEMA_VERSION = "campaign_phase_gate_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/campaign_phase_gate_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_campaign_phase_gate_v1.md")
UID = "PIS-20260409-121601Z-K9N3"

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "campaign_id",
    "phase_id",
    "decision_id",
    "verdict",
    "evidence_refs",
    "caveats",
    "proof_gaps",
    "downstream_unlock",
    "handoff",
)
TOP_LEVEL_OPTIONAL_FIELDS: Tuple[str, ...] = ()
TOP_LEVEL_KNOWN_FIELDS = frozenset(TOP_LEVEL_REQUIRED_FIELDS + TOP_LEVEL_OPTIONAL_FIELDS)

DOWNSTREAM_UNLOCK_REQUIRED_FIELDS: Tuple[str, ...] = ("policy", "allowed", "unlocked_phase_ids")
HANDOFF_REQUIRED_FIELDS: Tuple[str, ...] = ("summary", "evidence_refs", "caveats", "proof_gaps", "next_decision")
NEXT_DECISION_REQUIRED_FIELDS: Tuple[str, ...] = ("decision_owner", "question", "recommended_action")
ALLOW_EMPTY_TOP_LEVEL_LIST_FIELDS = frozenset({"caveats", "proof_gaps"})
ALLOW_EMPTY_HANDOFF_LIST_FIELDS = frozenset({"caveats", "proof_gaps"})

GATE_VERDICTS = frozenset(
    {
        "ready",
        "partial_ready",
        "blocked",
        "waiting_review",
        "terminal_success",
        "terminal_failed",
        "terminal_cancelled",
    }
)
UNLOCK_POLICIES = frozenset({"none", "full_ready", "allowed_with_caveats", "review_required"})
RECOMMENDED_ACTIONS = frozenset({"launch_downstream", "await_review", "repair", "stop", "close_phase"})

FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "missing_handoff_evidence_rejected",
    "partial_ready_without_caveats_rejected",
    "under_evidenced_ready_rejected",
    "blocked_or_review_state_cannot_unlock_downstream",
    "contradictory_unlock_policy_rejected",
    "missing_or_stale_evidence_pack_ref_rejected",
)

ERROR_CODES = MappingProxyType(
    {
        "missing_field": "missing_field",
        "invalid_type": "invalid_type",
        "invalid_value": "invalid_value",
        "unknown_field": "unknown_field",
        "missing_handoff_evidence": "missing_handoff_evidence",
        "contradictory_status": "contradictory_status",
        "proof_gap_not_allowed": "proof_gap_not_allowed",
        "partial_ready_requires_caveat": "partial_ready_requires_caveat",
        "missing_evidence_pack": "missing_evidence_pack",
        "invalid_evidence_pack": "invalid_evidence_pack",
        "fingerprint_mismatch": "fingerprint_mismatch",
    }
)


@dataclass(frozen=True)
class CampaignPhaseGateContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    top_level_required_fields: Tuple[str, ...]
    downstream_unlock_required_fields: Tuple[str, ...]
    handoff_required_fields: Tuple[str, ...]
    next_decision_required_fields: Tuple[str, ...]
    gate_verdicts: frozenset[str]
    unlock_policies: frozenset[str]
    recommended_actions: frozenset[str]
    fail_closed_rules: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def campaign_phase_gate_contract_v1() -> CampaignPhaseGateContractV1:
    return CampaignPhaseGateContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        downstream_unlock_required_fields=DOWNSTREAM_UNLOCK_REQUIRED_FIELDS,
        handoff_required_fields=HANDOFF_REQUIRED_FIELDS,
        next_decision_required_fields=NEXT_DECISION_REQUIRED_FIELDS,
        gate_verdicts=GATE_VERDICTS,
        unlock_policies=UNLOCK_POLICIES,
        recommended_actions=RECOMMENDED_ACTIONS,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_campaign_phase_gate_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return _normalize_text(value).lower() in {"1", "true", "yes"}


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _normalize_next_decision(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "decision_owner": _normalize_text(row.get("decision_owner")),
        "question": _normalize_text(row.get("question")),
        "recommended_action": _normalize_text(row.get("recommended_action")).lower(),
    }


def _normalize_downstream_unlock(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "policy": _normalize_text(row.get("policy")).lower(),
        "allowed": _normalize_bool(row.get("allowed")),
        "unlocked_phase_ids": _normalize_text_list(row.get("unlocked_phase_ids")),
    }


def _normalize_handoff(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "summary": _normalize_text(row.get("summary")),
        "evidence_refs": _normalize_text_list(row.get("evidence_refs")),
        "evidence_pack_refs": evidence_pack_handoff_refs.normalize_evidence_pack_refs(row.get("evidence_pack_refs")),
        "caveats": _normalize_text_list(row.get("caveats")),
        "proof_gaps": _normalize_text_list(row.get("proof_gaps")),
        "next_decision": _normalize_next_decision(row.get("next_decision")),
    }


def normalize_phase_gate_decision(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    return {
        "schema_version": _normalize_text(obj.get("schema_version") or SCHEMA_VERSION),
        "campaign_id": _normalize_text(obj.get("campaign_id")),
        "phase_id": _normalize_text(obj.get("phase_id")),
        "decision_id": _normalize_text(obj.get("decision_id")),
        "verdict": _normalize_text(obj.get("verdict")).lower(),
        "evidence_refs": _normalize_text_list(obj.get("evidence_refs")),
        "caveats": _normalize_text_list(obj.get("caveats")),
        "proof_gaps": _normalize_text_list(obj.get("proof_gaps")),
        "downstream_unlock": _normalize_downstream_unlock(obj.get("downstream_unlock")),
        "handoff": _normalize_handoff(obj.get("handoff")),
    }


def canonical_phase_gate_json(normalized_decision: Mapping[str, Any]) -> str:
    return json.dumps(dict(normalized_decision), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def phase_gate_fingerprint(normalized_decision: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_phase_gate_json(normalized_decision).encode("utf-8")).hexdigest()


def validate_phase_gate_decision(raw_decision: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    raw = dict(raw_decision or {})
    normalized = normalize_phase_gate_decision(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    unknown_top_level = sorted(set(raw.keys()) - TOP_LEVEL_KNOWN_FIELDS)
    for key in unknown_top_level:
        warnings.append(
            {
                "code": ERROR_CODES["unknown_field"],
                "field": str(key),
                "message": f"unknown top-level field ignored for compatibility: {key}",
            }
        )

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value) or (
            isinstance(value, list) and not value and field not in ALLOW_EMPTY_TOP_LEVEL_LIST_FIELDS
        ):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "schema_version",
                "message": f"schema_version must be {SCHEMA_VERSION}",
            }
        )

    verdict = normalized["verdict"]
    if verdict not in GATE_VERDICTS:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "verdict",
                "message": f"verdict must be one of: {', '.join(sorted(GATE_VERDICTS))}",
            }
        )

    unlock = normalized["downstream_unlock"]
    for field in DOWNSTREAM_UNLOCK_REQUIRED_FIELDS:
        value = unlock.get(field)
        if value in ("", None) or (isinstance(value, list) and not value and field != "unlocked_phase_ids"):
            errors.append(
                {
                    "code": ERROR_CODES["missing_field"],
                    "field": f"downstream_unlock.{field}",
                    "message": f"downstream_unlock.{field} is required",
                }
            )
    if unlock["policy"] not in UNLOCK_POLICIES:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "downstream_unlock.policy",
                "message": f"policy must be one of: {', '.join(sorted(UNLOCK_POLICIES))}",
            }
        )

    handoff = normalized["handoff"]
    for field in HANDOFF_REQUIRED_FIELDS:
        value = handoff.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value) or (
            isinstance(value, list) and not value and field not in ALLOW_EMPTY_HANDOFF_LIST_FIELDS
        ):
            errors.append({"code": ERROR_CODES["missing_field"], "field": f"handoff.{field}", "message": f"handoff.{field} is required"})
    for field in NEXT_DECISION_REQUIRED_FIELDS:
        if not handoff["next_decision"].get(field):
            errors.append(
                {
                    "code": ERROR_CODES["missing_field"],
                    "field": f"handoff.next_decision.{field}",
                    "message": f"handoff.next_decision.{field} is required",
                }
            )
    if handoff["next_decision"]["recommended_action"] and handoff["next_decision"]["recommended_action"] not in RECOMMENDED_ACTIONS:
        errors.append(
            {
                "code": ERROR_CODES["invalid_value"],
                "field": "handoff.next_decision.recommended_action",
                "message": f"recommended_action must be one of: {', '.join(sorted(RECOMMENDED_ACTIONS))}",
            }
        )

    errors.extend(
        evidence_pack_handoff_refs.validate_evidence_pack_refs(
            handoff["evidence_pack_refs"],
            repo_root=repo_root,
            field_prefix="handoff.evidence_pack_refs",
        )
    )

    unlock_allowed = bool(unlock["allowed"])
    unlocked_phase_ids = unlock["unlocked_phase_ids"]
    proof_gaps = normalized["proof_gaps"]
    caveats = normalized["caveats"]

    if unlock_allowed and not handoff["evidence_refs"]:
        errors.append(
            {
                "code": ERROR_CODES["missing_handoff_evidence"],
                "field": "handoff.evidence_refs",
                "message": "downstream unlock requires durable handoff evidence refs",
            }
        )
    if unlock_allowed and not unlocked_phase_ids:
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "downstream_unlock.unlocked_phase_ids",
                "message": "downstream unlock requires at least one unlocked phase id",
            }
        )
    if not unlock_allowed and unlocked_phase_ids:
        errors.append(
            {
                "code": ERROR_CODES["contradictory_status"],
                "field": "downstream_unlock.unlocked_phase_ids",
                "message": "blocked or review-only decisions cannot advertise unlocked phase ids",
            }
        )

    if verdict == "ready":
        if proof_gaps:
            errors.append(
                {
                    "code": ERROR_CODES["proof_gap_not_allowed"],
                    "field": "proof_gaps",
                    "message": "ready cannot be claimed while proof gaps remain",
                }
            )
        if unlock_allowed and unlock["policy"] != "full_ready":
            errors.append(
                {
                    "code": ERROR_CODES["contradictory_status"],
                    "field": "downstream_unlock.policy",
                    "message": "ready may unlock downstream only via the full_ready policy",
                }
            )
    elif verdict == "partial_ready":
        if not caveats:
            errors.append(
                {
                    "code": ERROR_CODES["partial_ready_requires_caveat"],
                    "field": "caveats",
                    "message": "partial_ready requires durable caveats",
                }
            )
        if unlock_allowed:
            if unlock["policy"] != "allowed_with_caveats":
                errors.append(
                    {
                        "code": ERROR_CODES["contradictory_status"],
                        "field": "downstream_unlock.policy",
                        "message": "partial_ready may unlock downstream only with allowed_with_caveats",
                    }
                )
            if not handoff["caveats"]:
                errors.append(
                    {
                        "code": ERROR_CODES["partial_ready_requires_caveat"],
                        "field": "handoff.caveats",
                        "message": "partial_ready unlock requires handoff caveats to remain operator-visible",
                    }
                )
        elif unlock["policy"] != "review_required":
            errors.append(
                {
                    "code": ERROR_CODES["contradictory_status"],
                    "field": "downstream_unlock.policy",
                    "message": "partial_ready without unlock must remain review_required",
                }
            )
    elif verdict == "blocked":
        if unlock_allowed or unlock["policy"] != "none":
            errors.append(
                {
                    "code": ERROR_CODES["contradictory_status"],
                    "field": "downstream_unlock",
                    "message": "blocked cannot unlock downstream or claim a non-none policy",
                }
            )
    elif verdict == "waiting_review":
        if unlock_allowed or unlock["policy"] != "review_required":
            errors.append(
                {
                    "code": ERROR_CODES["contradictory_status"],
                    "field": "downstream_unlock",
                    "message": "waiting_review must stay blocked on operator review",
                }
            )
    elif verdict in {"terminal_success", "terminal_failed", "terminal_cancelled"}:
        if unlock_allowed or unlock["policy"] != "none":
            errors.append(
                {
                    "code": ERROR_CODES["contradictory_status"],
                    "field": "downstream_unlock",
                    "message": "terminal decisions cannot advertise downstream unlock",
                }
            )

    return {
        "ok": not errors,
        "failure_class": "" if not errors else "campaign_phase_gate_validation_failed",
        "errors": errors,
        "warnings": warnings,
        "normalized_decision": normalized,
        "computed_fingerprint": phase_gate_fingerprint(normalized),
    }


def validate_campaign_phase_gate_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_campaign_phase_gate_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain campaign_phase_gate_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260409-121601Z-K9N3")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion phase-gate contract doc")
    if tuple(payload.get("top_level_required_fields") or ()) != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("top_level_required_fields must match the runtime phase-gate contract")
    if tuple(payload.get("downstream_unlock_required_fields") or ()) != DOWNSTREAM_UNLOCK_REQUIRED_FIELDS:
        issues.append("downstream_unlock_required_fields must match the runtime phase-gate contract")
    if tuple(payload.get("handoff_required_fields") or ()) != HANDOFF_REQUIRED_FIELDS:
        issues.append("handoff_required_fields must match the runtime phase-gate contract")
    if tuple(payload.get("next_decision_required_fields") or ()) != NEXT_DECISION_REQUIRED_FIELDS:
        issues.append("next_decision_required_fields must match the runtime phase-gate contract")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must match the runtime phase-gate contract")

    verdicts = tuple(payload.get("gate_verdicts") or ())
    if tuple(sorted(verdicts)) != tuple(sorted(GATE_VERDICTS)):
        issues.append("gate_verdicts must match the canonical campaign phase-gate vocabulary")
    policies = tuple(payload.get("unlock_policies") or ())
    if tuple(sorted(policies)) != tuple(sorted(UNLOCK_POLICIES)):
        issues.append("unlock_policies must match the canonical campaign phase-gate vocabulary")
    actions = tuple(payload.get("recommended_actions") or ())
    if tuple(sorted(actions)) != tuple(sorted(RECOMMENDED_ACTIONS)):
        issues.append("recommended_actions must match the canonical handoff next-decision vocabulary")

    return tuple(issues)
