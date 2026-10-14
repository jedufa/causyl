"""Deterministic Phase-3 state explanation engine."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .phase3_state_view import build_operator_derived_state_view_v1
from .phase3_uncertainty import build_proof_gap_uncertainty_surface_v1

STATE_EXPLANATION_OUTPUT_SCHEMA_PATH = Path("governance/contracts/state_explanation_output_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_state_explanation_output_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / STATE_EXPLANATION_OUTPUT_SCHEMA_PATH)


def explain_operator_state_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_state_explanation_output_schema(repo_root=repo_root)
    view = dict(payload.get("derived_state_view") or {})
    if not view:
        view = build_operator_derived_state_view_v1(payload, repo_root=repo_root)
    uncertainty = dict(payload.get("uncertainty_surface") or {})
    if not uncertainty:
        uncertainty = build_proof_gap_uncertainty_surface_v1({"derived_state_view": view}, repo_root=repo_root)

    state_class = str(view.get("state_class") or "unknown").strip()
    reason = str(view.get("primary_reason_hint") or "insufficient_evidence").strip() or "insufficient_evidence"
    proof_gap_present = bool(uncertainty.get("proof_gap_present"))
    stale = bool(uncertainty.get("stale_evidence_present"))
    contradiction = bool(uncertainty.get("contradiction_present"))

    if contradiction:
        summary = "State is degraded because supporting evidence sources disagree."
        proof_state = "uncertain"
    elif stale:
        summary = "State is degraded because supporting evidence is stale."
        proof_state = "uncertain"
    elif state_class == "pass":
        summary = "Run completed with explicit PASS and no unresolved proof gaps."
        proof_state = "pass"
    elif state_class == "blocked" and proof_gap_present:
        first_gap = next(iter(list(uncertainty.get("unresolved_items") or [])), "unresolved_proof_gap")
        summary = f"Run is blocked and still has unresolved proof gaps: {first_gap}."
        proof_state = "proof_gap"
    elif state_class == "blocked":
        summary = f"Run is blocked because {reason}."
        proof_state = "no_pass"
    elif state_class == "failed":
        summary = f"Run failed because {reason}."
        proof_state = "no_pass"
    elif state_class == "running":
        summary = "Run appears active; final PASS has not yet been earned."
        proof_state = "no_pass"
    else:
        summary = "State is unknown because the available evidence is insufficient."
        proof_state = "uncertain"

    evidence_refs: List[str] = []
    provenance = dict(view.get("evidence_provenance") or {})
    for key in ("state_class", "proof_gaps", "final_verifier_verdict", "freshness"):
        for ref in list(provenance.get(key) or []):
            text = str(ref or "").strip()
            if text and text not in evidence_refs:
                evidence_refs.append(text)

    return {
        "schema_version": "state_explanation_output_v1",
        "summary": summary,
        "status_class": state_class,
        "primary_reason": reason,
        "evidence_references": evidence_refs,
        "proof_state": proof_state,
        "recommendation_eligible": state_class != "pass",
        "contradiction_present": contradiction,
        "stale_evidence_present": stale,
    }


def validate_state_explanation_output(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_state_explanation_output_schema(repo_root=repo_root)
    required = [str(item or "").strip() for item in list(schema.get("required_fields") or []) if str(item or "").strip()]
    missing = [field for field in required if field not in payload]
    errors: List[Dict[str, str]] = []
    for field in missing:
        errors.append({"code": "missing_field", "message": f"missing required field: {field}"})
    proof_state = str(payload.get("proof_state") or "").strip()
    allowed = {str(item or "").strip() for item in list(schema.get("allowed_proof_states") or []) if str(item or "").strip()}
    if proof_state and proof_state not in allowed:
        errors.append({"code": "unknown_proof_state", "message": f"unknown proof state: {proof_state}"})
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}
