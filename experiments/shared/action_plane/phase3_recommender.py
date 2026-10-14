"""Deterministic Phase-3 next-safe-action recommendation engine."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .phase3_state_view import build_operator_derived_state_view_v1
from .phase3_uncertainty import build_proof_gap_uncertainty_surface_v1
from .phase3_explainer import explain_operator_state_v1
from .routing import load_action_registry
from .phase2_playbooks import load_composed_playbook_registry

NEXT_SAFE_ACTION_RECOMMENDATION_SCHEMA_PATH = Path("governance/contracts/next_safe_action_recommendation_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_next_safe_action_recommendation_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / NEXT_SAFE_ACTION_RECOMMENDATION_SCHEMA_PATH)


def _approved_action_map(*, repo_root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    approved: Dict[str, Dict[str, Any]] = {}
    for row in list(load_action_registry(repo_root=repo_root).get("actions") or []):
        action_id = str(row.get("action_id") or "").strip()
        if action_id:
            approved[action_id] = {
                "action_id": action_id,
                "title": str(row.get("title") or action_id).strip(),
                "operator_verb": str(row.get("operator_verb") or row.get("title") or action_id).strip(),
                "kind": "phase1_action",
            }
    for row in list(load_composed_playbook_registry(repo_root=repo_root).get("playbooks") or []):
        playbook_id = str(row.get("playbook_id") or "").strip()
        if playbook_id:
            approved[playbook_id] = {
                "action_id": playbook_id,
                "title": playbook_id.replace("_", " "),
                "operator_verb": playbook_id.replace("_", " "),
                "kind": "phase2_playbook",
            }
    return approved


def recommend_next_safe_actions_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_next_safe_action_recommendation_schema(repo_root=repo_root)
    approved = _approved_action_map(repo_root=repo_root)
    view = dict(payload.get("derived_state_view") or {})
    if not view:
        view = build_operator_derived_state_view_v1(payload, repo_root=repo_root)
    uncertainty = dict(payload.get("uncertainty_surface") or {})
    if not uncertainty:
        uncertainty = build_proof_gap_uncertainty_surface_v1({"derived_state_view": view}, repo_root=repo_root)
    explanation = dict(payload.get("explanation_output") or {})
    if not explanation:
        explanation = explain_operator_state_v1({"derived_state_view": view, "uncertainty_surface": uncertainty}, repo_root=repo_root)

    action_family = str(view.get("action_family") or "").strip()
    state_class = str(view.get("state_class") or "unknown").strip()
    proof_gap_present = bool(uncertainty.get("proof_gap_present"))
    contradiction = bool(uncertainty.get("contradiction_present"))
    stale = bool(uncertainty.get("stale_evidence_present"))
    evidence_refs = list(explanation.get("evidence_references") or [])

    recommendations: List[Dict[str, Any]] = []

    def _add(action_id: str, reason: str, safety_class: str, *, requires_manual_review: bool = False) -> None:
        if not action_id or action_id not in approved:
            return
        if any(row.get("action_id") == action_id for row in recommendations):
            return
        recommendations.append({
            "action_id": action_id,
            "title": approved[action_id]["title"],
            "rank": len(recommendations) + 1,
            "reason": reason,
            "evidence_references": evidence_refs,
            "safety_class": safety_class,
            "requires_manual_review": requires_manual_review,
        })

    if state_class == "pass" and not proof_gap_present and not stale and not contradiction:
        no_recommendation_reason = "state_pass_complete"
        manual_review_required = False
    else:
        no_recommendation_reason = ""
        manual_review_required = False
        if contradiction:
            _add("status_reconcile_v1", "Evidence sources disagree; reconcile state before further mutation.", "safe_reconcile")
        if action_family == "patch_apply":
            if state_class == "blocked":
                _add("patch_apply_chain_v1", "Patch apply chain is blocked and should be retried only through the bounded playbook.", "repo_mutation")
            if proof_gap_present:
                _add("pis_assess_proof_gap_v1", "Proof gaps remain after patch and validation evidence collection.", "read_only")
        elif action_family == "build_verify":
            if state_class in {"blocked", "degraded"} or proof_gap_present or stale:
                _add("build_verify_loop_v1", "Build/verify loop can refresh validation and verifier evidence.", "read_only")
            if contradiction:
                _add("status_reconcile_v1", "Reconcile contradictory build/verify state before retrying.", "safe_reconcile")
        elif action_family == "status_reconcile" or contradiction:
            _add("status_reconcile_v1", "State reconcile is the safest bounded action for conflicting or drifting state.", "safe_reconcile")
        elif action_family == "job_inspect_bounded_recovery" or state_class == "running":
            _add("job_inspect_bounded_recovery_v1", "Bounded job inspection is the safest next step for degraded job state.", "read_only")
        if proof_gap_present and not any(row["action_id"] == "pis_assess_proof_gap_v1" for row in recommendations):
            _add("pis_assess_proof_gap_v1", "Proof gaps remain unresolved and need explicit evidence classification.", "read_only")
        if not recommendations and state_class != "pass":
            manual_review_required = True
            no_recommendation_reason = "no_safe_approved_action"

    return {
        "schema_version": "next_safe_action_recommendation_v1",
        "recommendations": recommendations,
        "manual_review_required": manual_review_required,
        "no_recommendation_reason": no_recommendation_reason,
    }


def validate_next_safe_action_recommendation_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_next_safe_action_recommendation_schema(repo_root=repo_root)
    required = [str(item or "").strip() for item in list(schema.get("required_fields") or []) if str(item or "").strip()]
    missing = [field for field in required if field not in payload]
    errors: List[Dict[str, str]] = []
    for field in missing:
        errors.append({"code": "missing_field", "message": f"missing required field: {field}"})

    approved = _approved_action_map(repo_root=repo_root)
    required_rec_fields = [str(item or "").strip() for item in list(schema.get("required_recommendation_fields") or []) if str(item or "").strip()]
    seen: set[str] = set()
    for row in list(payload.get("recommendations") or []):
        if not isinstance(row, Mapping):
            errors.append({"code": "invalid_recommendation_row", "message": "recommendation rows must be mappings"})
            continue
        for field in required_rec_fields:
            if field not in row:
                errors.append({"code": "missing_recommendation_field", "message": f"recommendation missing field: {field}"})
        action_id = str(row.get("action_id") or "").strip()
        if action_id and action_id not in approved:
            errors.append({"code": "unapproved_action", "message": f"recommendation references unapproved action: {action_id}"})
        if action_id and action_id in seen:
            errors.append({"code": "duplicate_action", "message": f"duplicate recommended action: {action_id}"})
        seen.add(action_id)
    return {"ok": not errors, "errors": errors, "payload": dict(payload)}
