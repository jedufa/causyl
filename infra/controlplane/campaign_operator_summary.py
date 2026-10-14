"""Campaign operator-summary and evidence-surface helpers."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import campaign_lifecycle_reconciliation
from infra.controlplane import campaign_phase_gate_contract
from infra.controlplane import campaign_record_contract


SCHEMA_VERSION = "campaign_operator_summary_v1"
CONTRACT_ARTIFACT_REL = Path("governance/campaign_operator_summary_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_campaign_operator_summary_v1.md")
UID = "PIS-20260409-121604Z-N3R6"

OPERATOR_READINESS_STATES: Tuple[str, ...] = (
    "ready",
    "partial_ready",
    "blocked",
    "waiting_review",
    "uncertain",
)
REQUIRED_SUMMARY_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "campaign_id",
    "campaign_state",
    "operator_readiness",
    "headline",
    "current_phase",
    "current_gate",
    "next_step_logic",
    "proof_gaps",
    "budget_posture",
    "review_summary",
    "blocked_summary",
    "phase_graph",
    "operator_evidence",
)


@dataclass(frozen=True)
class CampaignOperatorSummaryContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    operator_readiness_states: Tuple[str, ...]
    required_summary_fields: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _merged_unique_text_lists(*values: Any) -> List[str]:
    out: List[str] = []
    for value in values:
        for text in _normalize_text_list(value):
            if text not in out:
                out.append(text)
    return out


def campaign_operator_summary_contract_v1() -> CampaignOperatorSummaryContractV1:
    return CampaignOperatorSummaryContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        operator_readiness_states=OPERATOR_READINESS_STATES,
        required_summary_fields=REQUIRED_SUMMARY_FIELDS,
    )


def load_campaign_operator_summary_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_campaign_operator_summary_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_campaign_operator_summary_contract(repo_root=repo_root)
    issues: List[str] = []
    if payload.get("schema_version") != SCHEMA_VERSION:
        issues.append(f"schema_version must remain {SCHEMA_VERSION}")
    if payload.get("uid") != UID:
        issues.append(f"uid must remain {UID}")
    if payload.get("canonical_doc_rel") != CONTRACT_DOC_REL.as_posix():
        issues.append(f"canonical_doc_rel must remain {CONTRACT_DOC_REL.as_posix()}")
    if tuple(payload.get("operator_readiness_states") or ()) != OPERATOR_READINESS_STATES:
        issues.append("operator_readiness_states drifted from runtime exports")
    if tuple(payload.get("required_summary_fields") or ()) != REQUIRED_SUMMARY_FIELDS:
        issues.append("required_summary_fields drifted from runtime exports")
    return tuple(issues)


def _gate_map(phase_gate_decisions: Sequence[Mapping[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for raw in phase_gate_decisions:
        if not isinstance(raw, Mapping):
            continue
        result = campaign_phase_gate_contract.validate_phase_gate_decision(raw)
        if not result["ok"]:
            continue
        normalized = dict(result["normalized_decision"])
        phase_id = _normalize_text(normalized.get("phase_id"))
        if phase_id:
            out[phase_id] = normalized
    return out


def _phase_map(campaign_record: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    record_result = campaign_record_contract.validate_campaign_record(campaign_record)
    if not record_result["ok"]:
        raise ValueError("campaign record must validate before building operator summary")
    normalized_record = dict(record_result["normalized_record"])
    return {
        _normalize_text(row.get("phase_id")): dict(row)
        for row in list(normalized_record.get("phases") or [])
        if _normalize_text(row.get("phase_id"))
    }


def _current_phase(
    *,
    reconciliation: Mapping[str, Any],
    phase_rows: Mapping[str, Mapping[str, Any]],
    gates: Mapping[str, Mapping[str, Any]],
) -> Dict[str, Any]:
    phases = list(reconciliation.get("phases") or [])
    if not phases:
        return {}

    current = None
    for row in phases:
        if _normalize_text(row.get("reconciled_state")) not in {"succeeded", "failed", "cancelled"}:
            current = dict(row)
            break
    if current is None:
        current = dict(phases[-1])

    phase_id = _normalize_text(current.get("phase_id"))
    phase_meta = dict(phase_rows.get(phase_id) or {})
    gate = dict(gates.get(phase_id) or {})
    return {
        "phase_id": phase_id,
        "label": _normalize_text(phase_meta.get("label")),
        "record_state": _normalize_text(current.get("record_state")),
        "reconciled_state": _normalize_text(current.get("reconciled_state")),
        "progress_state": _normalize_text(current.get("progress_state")),
        "gate_verdict": _normalize_text(current.get("gate_verdict")),
        "retry_state": _normalize_text((current.get("retry_boundary") if isinstance(current.get("retry_boundary"), Mapping) else {}).get("state")),
        "budget_status": _normalize_text((current.get("budget") if isinstance(current.get("budget"), Mapping) else {}).get("status")),
        "proof_gaps": _normalize_text_list(gate.get("proof_gaps")),
        "caveats": _normalize_text_list(gate.get("caveats")),
        "reasons": _normalize_text_list(current.get("reasons")),
    }


def _current_gate(current_phase: Mapping[str, Any], gates: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    phase_id = _normalize_text(current_phase.get("phase_id"))
    gate = dict(gates.get(phase_id) or {})
    downstream_unlock = gate.get("downstream_unlock") if isinstance(gate.get("downstream_unlock"), Mapping) else {}
    handoff = gate.get("handoff") if isinstance(gate.get("handoff"), Mapping) else {}
    next_decision = handoff.get("next_decision") if isinstance(handoff.get("next_decision"), Mapping) else {}
    return {
        "phase_id": phase_id,
        "decision_id": _normalize_text(gate.get("decision_id")),
        "verdict": _normalize_text(gate.get("verdict")),
        "allowed": bool(downstream_unlock.get("allowed")),
        "unlock_policy": _normalize_text(downstream_unlock.get("policy")),
        "evidence_refs": _normalize_text_list(gate.get("evidence_refs")),
        "caveats": _normalize_text_list(gate.get("caveats")),
        "proof_gaps": _normalize_text_list(gate.get("proof_gaps")),
        "summary": _normalize_text(handoff.get("summary")),
        "next_decision": {
            "decision_owner": _normalize_text(next_decision.get("decision_owner")),
            "question": _normalize_text(next_decision.get("question")),
            "recommended_action": _normalize_text(next_decision.get("recommended_action")),
        },
    }


def _operator_readiness(
    *,
    reconciliation: Mapping[str, Any],
    current_gate: Mapping[str, Any],
) -> str:
    budget_status = _normalize_text((reconciliation.get("budget_rollup") if isinstance(reconciliation.get("budget_rollup"), Mapping) else {}).get("status"))
    ambiguous_budget = budget_status in {"blocked_missing_job_refs", "blocked_conflicting_units"}
    ambiguous_reasons = {
        "duplicate_receipts",
        "conflicting_receipt_states",
        "budget_missing_job_refs",
        "budget_conflicting_units",
    }
    if ambiguous_budget:
        return "uncertain"
    for phase in list(reconciliation.get("phases") or []):
        reasons = set(_normalize_text_list(phase.get("reasons")))
        if reasons & ambiguous_reasons:
            return "uncertain"
    if list(reconciliation.get("review_phase_ids") or []):
        return "waiting_review"
    if list(reconciliation.get("blocked_phase_ids") or []):
        return "blocked"
    if _normalize_text(current_gate.get("verdict")) == "partial_ready":
        return "partial_ready"
    return "ready"


def _headline(
    *,
    current_phase: Mapping[str, Any],
    readiness: str,
    reconciliation: Mapping[str, Any],
) -> str:
    label = _normalize_text(current_phase.get("label")) or _normalize_text(current_phase.get("phase_id")) or "campaign"
    if readiness == "uncertain":
        return f"{label} is blocked by ambiguous durable truth."
    if readiness == "waiting_review":
        return f"{label} is waiting on review before the campaign may advance."
    if readiness == "blocked":
        return f"{label} is blocked; the campaign should not advance."
    if readiness == "partial_ready":
        return f"{label} is partially ready and carries caveats into the next decision."
    campaign_state = _normalize_text(reconciliation.get("reconciled_campaign_state"))
    if campaign_state == "completed":
        return f"{label} reached clean terminal success from durable records."
    return f"{label} is the active phase and the campaign remains launchable from durable truth."


def _next_step_logic(
    *,
    readiness: str,
    current_phase: Mapping[str, Any],
    current_gate: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
) -> Dict[str, Any]:
    next_decision = dict(current_gate.get("next_decision") or {})
    launchable_phase_ids = _normalize_text_list(reconciliation.get("launchable_phase_ids"))
    if readiness == "waiting_review":
        return {
            "kind": "review_gate",
            "summary": _normalize_text(current_gate.get("summary")) or "Operator review is required before the campaign may advance.",
            "decision_owner": _normalize_text(next_decision.get("decision_owner")) or "operator",
            "question": _normalize_text(next_decision.get("question")) or "Review the current phase gate.",
            "recommended_action": _normalize_text(next_decision.get("recommended_action")) or "await_review",
        }
    if readiness == "blocked":
        return {
            "kind": "blocked_recovery",
            "summary": f"Repair {_normalize_text(current_phase.get('label')) or _normalize_text(current_phase.get('phase_id'))} before progression resumes.",
            "decision_owner": _normalize_text(next_decision.get("decision_owner")) or "campaign_parent",
            "question": _normalize_text(next_decision.get("question")) or "What durable repair closes the blocked phase?",
            "recommended_action": _normalize_text(next_decision.get("recommended_action")) or "repair",
        }
    if readiness == "uncertain":
        return {
            "kind": "ambiguity_review",
            "summary": "Reduce durable ambiguity before more work burns budget.",
            "decision_owner": _normalize_text(next_decision.get("decision_owner")) or "operator",
            "question": "Which missing or contradictory campaign facts must be reconciled first?",
            "recommended_action": "repair",
        }
    if launchable_phase_ids:
        return {
            "kind": "launchable_phase",
            "summary": f"Next launchable phase: {launchable_phase_ids[0]}",
            "decision_owner": _normalize_text(next_decision.get("decision_owner")) or "campaign_parent",
            "question": _normalize_text(next_decision.get("question")) or "Should the next phase launch now?",
            "recommended_action": _normalize_text(next_decision.get("recommended_action")) or "launch_downstream",
        }
    return {
        "kind": "observe",
        "summary": "No further phase is currently launchable from durable truth.",
        "decision_owner": _normalize_text(next_decision.get("decision_owner")) or "campaign_parent",
        "question": _normalize_text(next_decision.get("question")) or "What should the campaign do next?",
        "recommended_action": _normalize_text(next_decision.get("recommended_action")) or "await_review",
    }


def _proof_gaps(
    *,
    reconciliation: Mapping[str, Any],
    gates: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    gaps: List[Dict[str, Any]] = []
    for phase in list(reconciliation.get("phases") or []):
        phase_id = _normalize_text(phase.get("phase_id"))
        gate = dict(gates.get(phase_id) or {})
        for proof_gap in _normalize_text_list(gate.get("proof_gaps")):
            gaps.append(
                {
                    "phase_id": phase_id,
                    "kind": "proof_gap",
                    "summary": proof_gap,
                    "evidence_refs": _normalize_text_list(gate.get("evidence_refs")),
                }
            )
        for reason in _normalize_text_list(phase.get("reasons")):
            if reason in {"duplicate_receipts", "conflicting_receipt_states", "budget_missing_job_refs", "budget_conflicting_units"}:
                gaps.append(
                    {
                        "phase_id": phase_id,
                        "kind": "ambiguity",
                        "summary": reason,
                        "evidence_refs": _normalize_text_list(phase.get("receipt_refs")),
                    }
                )
    return gaps


def _budget_posture(reconciliation: Mapping[str, Any]) -> Dict[str, Any]:
    budget = reconciliation.get("budget_rollup") if isinstance(reconciliation.get("budget_rollup"), Mapping) else {}
    status = _normalize_text(budget.get("status"))
    unit = _normalize_text(budget.get("unit"))
    remaining = int(budget.get("remaining_minor_units") or 0)
    if status == "ok":
        summary = f"Budget posture is healthy with {remaining} {unit or 'minor units'} remaining."
    elif status.startswith("blocked"):
        summary = f"Budget posture is blocking campaign progress: {status}."
    else:
        summary = f"Budget posture is {status or 'unknown'}."
    return {
        "status": status,
        "summary": summary,
        "unit": unit,
        "allocated_minor_units": int(budget.get("allocated_minor_units") or 0),
        "spent_minor_units": int(budget.get("spent_minor_units") or 0),
        "remaining_minor_units": remaining,
        "phase_ids_missing_budget_refs": _normalize_text_list(budget.get("phase_ids_missing_budget_refs")),
        "phase_ids_blocked_by_budget": _normalize_text_list(budget.get("phase_ids_blocked_by_budget")),
        "phase_ids_conflicting_units": _normalize_text_list(budget.get("phase_ids_conflicting_units")),
    }


def _review_summary(reconciliation: Mapping[str, Any], gates: Mapping[str, Mapping[str, Any]]) -> Dict[str, Any]:
    review_phase_ids = _normalize_text_list(reconciliation.get("review_phase_ids"))
    rows: List[Dict[str, Any]] = []
    for phase_id in review_phase_ids:
        gate = dict(gates.get(phase_id) or {})
        handoff = gate.get("handoff") if isinstance(gate.get("handoff"), Mapping) else {}
        next_decision = handoff.get("next_decision") if isinstance(handoff.get("next_decision"), Mapping) else {}
        rows.append(
            {
                "phase_id": phase_id,
                "decision_id": _normalize_text(gate.get("decision_id")),
                "summary": _normalize_text(handoff.get("summary")),
                "decision_owner": _normalize_text(next_decision.get("decision_owner")),
                "question": _normalize_text(next_decision.get("question")),
                "recommended_action": _normalize_text(next_decision.get("recommended_action")),
                "evidence_refs": _normalize_text_list(gate.get("evidence_refs")),
                "proof_gaps": _normalize_text_list(gate.get("proof_gaps")),
            }
        )
    return {
        "status": "waiting_review" if rows else "clear",
        "count": len(rows),
        "items": rows,
    }


def _blocked_summary(reconciliation: Mapping[str, Any]) -> Dict[str, Any]:
    rows: List[Dict[str, Any]] = []
    for phase in list(reconciliation.get("phases") or []):
        if _normalize_text(phase.get("reconciled_state")) != "blocked":
            continue
        rows.append(
            {
                "phase_id": _normalize_text(phase.get("phase_id")),
                "reasons": _normalize_text_list(phase.get("reasons")),
                "receipt_refs": _normalize_text_list(phase.get("receipt_refs")),
                "retry_state": _normalize_text((phase.get("retry_boundary") if isinstance(phase.get("retry_boundary"), Mapping) else {}).get("state")),
            }
        )
    return {
        "status": "blocked" if rows else "clear",
        "count": len(rows),
        "items": rows,
    }


def _phase_graph(
    *,
    reconciliation: Mapping[str, Any],
    phase_rows: Mapping[str, Mapping[str, Any]],
    gates: Mapping[str, Mapping[str, Any]],
) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for phase in list(reconciliation.get("phases") or []):
        phase_id = _normalize_text(phase.get("phase_id"))
        meta = dict(phase_rows.get(phase_id) or {})
        gate = dict(gates.get(phase_id) or {})
        out.append(
            {
                "phase_id": phase_id,
                "label": _normalize_text(meta.get("label")),
                "depends_on": _normalize_text_list(meta.get("depends_on")),
                "record_state": _normalize_text(phase.get("record_state")),
                "reconciled_state": _normalize_text(phase.get("reconciled_state")),
                "progress_state": _normalize_text(phase.get("progress_state")),
                "gate_verdict": _normalize_text(phase.get("gate_verdict")),
                "retry_state": _normalize_text((phase.get("retry_boundary") if isinstance(phase.get("retry_boundary"), Mapping) else {}).get("state")),
                "budget_status": _normalize_text((phase.get("budget") if isinstance(phase.get("budget"), Mapping) else {}).get("status")),
                "proof_gaps": _normalize_text_list(gate.get("proof_gaps")),
                "caveats": _normalize_text_list(gate.get("caveats")),
                "evidence_refs": _normalize_text_list(gate.get("evidence_refs")),
                "reasons": _normalize_text_list(phase.get("reasons")),
            }
        )
    return out


def _operator_evidence(
    *,
    current_phase: Mapping[str, Any],
    reconciliation: Mapping[str, Any],
    phase_rows: Mapping[str, Mapping[str, Any]],
    current_gate: Mapping[str, Any],
) -> Dict[str, Any]:
    phase_id = _normalize_text(current_phase.get("phase_id"))
    phase_meta = dict(phase_rows.get(phase_id) or {})
    truth_refs = phase_meta.get("truth_refs") if isinstance(phase_meta.get("truth_refs"), Mapping) else {}
    reconciled_phase = next(
        (
            dict(row)
            for row in list(reconciliation.get("phases") or [])
            if _normalize_text((row if isinstance(row, Mapping) else {}).get("phase_id")) == phase_id
        ),
        {},
    )
    budget = reconciled_phase.get("budget") if isinstance(reconciled_phase.get("budget"), Mapping) else {}
    receipt_refs = _merged_unique_text_lists(reconciled_phase.get("receipt_refs"), truth_refs.get("receipt_refs"))
    continuation_packet_ids = _merged_unique_text_lists(
        reconciled_phase.get("continuation_packet_ids"),
        truth_refs.get("continuation_packet_ids"),
    )
    job_ids = _merged_unique_text_lists(budget.get("job_ids"), truth_refs.get("job_ids"))
    return {
        "focus_phase_id": phase_id,
        "evidence_refs": _normalize_text_list(current_gate.get("evidence_refs")),
        "receipt_refs": receipt_refs,
        "artifact_refs": _normalize_text_list(truth_refs.get("artifact_refs")),
        "continuation_packet_ids": continuation_packet_ids,
        "job_ids": job_ids,
        "run_ids": _normalize_text_list(truth_refs.get("run_ids")),
        "proof_gaps": _normalize_text_list(current_gate.get("proof_gaps")),
        "caveats": _normalize_text_list(current_gate.get("caveats")),
    }


def build_campaign_operator_summary(
    *,
    campaign_record: Mapping[str, Any],
    phase_gate_decisions: Sequence[Mapping[str, Any]] = (),
    phase_runtime_facts: Sequence[Mapping[str, Any]] = (),
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    phase_rows = _phase_map(campaign_record)
    gates = _gate_map(phase_gate_decisions)
    reconciliation = campaign_lifecycle_reconciliation.reconcile_campaign_lifecycle(
        campaign_record=campaign_record,
        phase_gate_decisions=phase_gate_decisions,
        phase_runtime_facts=phase_runtime_facts,
        repo_root=repo_root,
    )
    current_phase = _current_phase(reconciliation=reconciliation, phase_rows=phase_rows, gates=gates)
    current_gate = _current_gate(current_phase, gates)
    readiness = _operator_readiness(reconciliation=reconciliation, current_gate=current_gate)
    return {
        "schema_version": SCHEMA_VERSION,
        "campaign_id": _normalize_text(reconciliation.get("campaign_id")),
        "campaign_state": _normalize_text(reconciliation.get("reconciled_campaign_state")),
        "operator_readiness": readiness,
        "headline": _headline(current_phase=current_phase, readiness=readiness, reconciliation=reconciliation),
        "current_phase": current_phase,
        "current_gate": current_gate,
        "next_step_logic": _next_step_logic(
            readiness=readiness,
            current_phase=current_phase,
            current_gate=current_gate,
            reconciliation=reconciliation,
        ),
        "proof_gaps": _proof_gaps(reconciliation=reconciliation, gates=gates),
        "budget_posture": _budget_posture(reconciliation),
        "review_summary": _review_summary(reconciliation, gates),
        "blocked_summary": _blocked_summary(reconciliation),
        "phase_graph": _phase_graph(reconciliation=reconciliation, phase_rows=phase_rows, gates=gates),
        "operator_evidence": _operator_evidence(
            current_phase=current_phase,
            reconciliation=reconciliation,
            phase_rows=phase_rows,
            current_gate=current_gate,
        ),
        "reconciliation": reconciliation,
    }
