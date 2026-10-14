"""C1J2 verification policy: proof freshness and claim-gating.

This module applies the minimum claim-gating contract for builder/verifier/
operator completion claims. It does not re-own runtime execution or UI; it
only evaluates whether a success/complete claim is currently allowed given
durable proof and freshness inputs.

Boundaries:
- Reads operator snapshot-style inputs but does not write state.
- Uses delegated execution proof freshness from G7B1 layer.
- Returns a small, explicit gate result that callers can project.
"""

from __future__ import annotations

from typing import Any, Dict, List


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def evaluate_claim_gate(snapshot: Dict[str, Any]) -> Dict[str, Any]:
    """Evaluate whether completion/success claims are allowed now.

    Inputs follow the operator snapshot shape but only a small subset is read:
    - snapshot["freshness"]["stale"]
    - snapshot["operator_summary"]["truth_conflicts"]
    - snapshot["operator_summary"]["status_language"]["state"]
    - snapshot["active_tasks"], snapshot["completed_tasks"] (for proof targets)
    - snapshot["delegation_proof"] (freshness + overall_status)

    Returns a dict with:
    - allowed: bool
    - reasons: List[str] machine-readable reason tags
    - summary: short human text
    """

    reasons: List[str] = []

    # Fail closed on globally stale durable truth.
    if bool(dict(snapshot.get("freshness") or {}).get("stale")):
        reasons.append("durable_truth_stale")

    # Fail closed on explicit truth conflicts surfaced by the snapshot layer.
    truth_conflicts = list(dict(snapshot.get("operator_summary") or {}).get("truth_conflicts") or [])
    if truth_conflicts:
        reasons.append("truth_conflict")

    # Require a fresh delegated execution proof report with overall ok.
    proof = dict(snapshot.get("delegation_proof") or {})
    proof_fresh = not bool(dict(proof.get("freshness") or {}).get("stale"))
    overall = _clean_text(dict(proof.get("summary") or {}).get("overall_status"))
    if not proof:
        reasons.append("missing_proof")
    elif not proof_fresh:
        reasons.append("stale_proof")
    elif overall not in {"ok", "pass"}:
        # Treat missing/stale/fail as not ok.
        reasons.append(f"proof_overall_{overall or 'unknown'}")

    # Block if any required proof targets remain pending across active+completed tasks.
    pending_required = 0
    for task in list(snapshot.get("active_tasks") or []) + list(snapshot.get("completed_tasks") or []):
        accept = dict(task.get("acceptance_contract") or {})
        for row in list(accept.get("proof_targets") or []):
            required = bool(row.get("required"))
            status = _clean_text(row.get("status"))
            if required and status not in {"satisfied", "waived"}:
                pending_required += 1
    if pending_required:
        reasons.append("pending_required_proof_targets")

    allowed = not reasons
    summary = (
        "allowed: completion claims permitted"
        if allowed
        else "blocked: " + ", ".join(sorted(set(reasons)))
    )
    return {"allowed": allowed, "reasons": reasons, "summary": summary}


__all__ = ["evaluate_claim_gate"]

