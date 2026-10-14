"""Phase-3 operator action surface helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .phase3_codex_usage import build_codex_usage_transparency_v1
from .phase3_explainer import explain_operator_state_v1
from .phase3_recommender import recommend_next_safe_actions_v1
from .phase3_state_view import build_operator_derived_state_view_v1
from .phase3_uncertainty import build_proof_gap_uncertainty_surface_v1

OPERATOR_ACTION_SURFACE_SCHEMA_PATH = Path("governance/contracts/operator_action_surface_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_operator_action_surface_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / OPERATOR_ACTION_SURFACE_SCHEMA_PATH)


def _unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or '').strip()
        if text and text not in out:
            out.append(text)
    return out


def _flatten_provenance(provenance: Mapping[str, Any]) -> List[str]:
    out: List[str] = []
    for value in provenance.values():
        if isinstance(value, list):
            out.extend(str(x or '').strip() for x in value if str(x or '').strip())
    return _unique(out)


def _executive_state(*, status_class: str, manual_review_required: bool) -> str:
    if manual_review_required or status_class in {"failed", "unknown"}:
        return "REVIEW_REQUIRED"
    mapping = {
        "pass": "PASS",
        "blocked": "BLOCKED",
        "degraded": "DEGRADED",
        "running": "RUNNING",
    }
    return mapping.get(status_class, "REVIEW_REQUIRED")


def _executive_confidence(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if normalized == "high":
        return "High"
    if normalized == "medium":
        return "Medium"
    if normalized in {"low", "degraded", "contradictory"}:
        return "Low"
    return "Unknown"


def _primary_and_alternatives(recommendations: Mapping[str, Any], *, manual_review_required: bool) -> Dict[str, Any]:
    rows = [dict(row) for row in list(recommendations.get("recommendations") or []) if isinstance(row, Mapping)]
    if rows:
        primary_row = dict(rows[0])
        primary = {
            "action_id": str(primary_row.get("action_id") or "").strip(),
            "label": str(primary_row.get("title") or primary_row.get("action_id") or "").strip(),
            "reason": str(primary_row.get("reason") or "").strip(),
            "semantic_role": "primary",
        }
        alternatives = [
            {
                "action_id": str(row.get("action_id") or "").strip(),
                "label": str(row.get("title") or row.get("action_id") or "").strip(),
                "reason": str(row.get("reason") or "").strip(),
            }
            for row in rows[1:]
        ]
        return {"primary": primary, "alternatives": alternatives}
    if manual_review_required:
        return {
            "primary": {
                "action_id": "manual_review",
                "label": "Manual review required",
                "reason": "No safe deterministic or approved next step exists.",
                "semantic_role": "primary",
            },
            "alternatives": [],
        }
    return {"primary": {}, "alternatives": []}


def build_operator_action_surface_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_operator_action_surface_schema(repo_root=repo_root)
    view = dict(payload.get('derived_state_view') or {})
    if not view:
        view = build_operator_derived_state_view_v1(payload, repo_root=repo_root)
    uncertainty = dict(payload.get('uncertainty_surface') or {})
    if not uncertainty:
        uncertainty = build_proof_gap_uncertainty_surface_v1({'derived_state_view': view}, repo_root=repo_root)
    explanation = dict(payload.get('explanation_output') or {})
    if not explanation:
        explanation = explain_operator_state_v1({'derived_state_view': view, 'uncertainty_surface': uncertainty}, repo_root=repo_root)
    recommendations = dict(payload.get('recommendation_output') or {})
    if not recommendations:
        recommendations = recommend_next_safe_actions_v1({
            'derived_state_view': view,
            'uncertainty_surface': uncertainty,
            'explanation_output': explanation,
        }, repo_root=repo_root)
    codex = dict(payload.get('codex_usage_output') or {})
    if not codex:
        codex = build_codex_usage_transparency_v1({
            'derived_state_view': view,
            'fallback_events': payload.get('fallback_events'),
            'improvement_opportunities': payload.get('improvement_opportunities'),
        }, repo_root=repo_root)

    artifact_refs = _unique(list(payload.get('artifact_references') or []) + _flatten_provenance(dict(view.get('evidence_provenance') or {})))
    manual_review_required = bool(recommendations.get('manual_review_required'))
    status_class = str(explanation.get('status_class') or view.get('state_class') or 'unknown').strip()
    recommendation_hierarchy = _primary_and_alternatives(recommendations, manual_review_required=manual_review_required)
    primary = dict(recommendation_hierarchy.get("primary") or {})
    executive_surface = {
        "state": _executive_state(status_class=status_class, manual_review_required=manual_review_required),
        "primary_next_action": {
            "action_id": str(primary.get("action_id") or "").strip(),
            "label": str(primary.get("label") or "").strip(),
        },
        "confidence": _executive_confidence(str(uncertainty.get('confidence_bucket') or '').strip()),
        "short_reason": str(explanation.get('primary_reason') or '').strip(),
    }
    explanation_layer = {
        "summary": str(explanation.get('summary') or '').strip(),
        "status_class": status_class,
        "supporting_evidence_refs": list(explanation.get('evidence_references') or []),
        "deterministic_path_available": bool(primary.get("action_id")) and str(primary.get("action_id") or "").strip() != "manual_review",
    }
    uncertainty_layer = {
        "proof_gap_present": bool(uncertainty.get('proof_gap_present')),
        "unresolved_items": list(uncertainty.get('unresolved_items') or []),
        "stale_evidence_present": bool(uncertainty.get('stale_evidence_present')),
        "contradiction_present": bool(uncertainty.get('contradiction_present')),
        "confidence_bucket": _executive_confidence(str(uncertainty.get('confidence_bucket') or '').strip()),
        "required_next_proof_steps": list(uncertainty.get('required_next_proof_steps') or []),
    }
    codex_layer = {
        "codex_used": bool(codex.get('codex_used')),
        "reason": str(codex.get('usage_reason') or '').strip(),
        "family": str(codex.get('action_family') or '').strip(),
        "recurrence_class": str(codex.get('recurrence_class') or '').strip(),
        "absorption_eligibility_class": str(codex.get('absorption_eligibility_class') or '').strip(),
        "economic_recommendation_class": str(codex.get('economic_recommendation_class') or '').strip(),
    }
    sections = [
        'executive_surface',
        'explanation',
        'uncertainty',
        'recommendations',
        'codex_transparency',
        'evidence_refs',
    ]
    return {
        'schema_version': 'operator_action_surface_v1',
        'status_class': status_class,
        'status_summary': str(explanation.get('summary') or '').strip(),
        'primary_explanation': {
            'primary_reason': str(explanation.get('primary_reason') or '').strip(),
            'proof_state': str(explanation.get('proof_state') or '').strip(),
            'evidence_references': list(explanation.get('evidence_references') or []),
        },
        'proof_uncertainty': {
            'proof_gap_present': bool(uncertainty.get('proof_gap_present')),
            'unresolved_items': list(uncertainty.get('unresolved_items') or []),
            'stale_evidence_present': bool(uncertainty.get('stale_evidence_present')),
            'contradiction_present': bool(uncertainty.get('contradiction_present')),
            'confidence_bucket': str(uncertainty.get('confidence_bucket') or '').strip(),
            'required_next_proof_steps': list(uncertainty.get('required_next_proof_steps') or []),
        },
        'recommended_actions': list(recommendations.get('recommendations') or []),
        'manual_review_required': manual_review_required,
        'codex_transparency': codex_layer,
        'artifact_references': artifact_refs,
        'section_order': sections,
        'executive_surface': executive_surface,
        'explanation': explanation_layer,
        'uncertainty': uncertainty_layer,
        'recommendations': recommendation_hierarchy,
        'evidence_refs': artifact_refs,
    }


def validate_operator_action_surface_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_operator_action_surface_schema(repo_root=repo_root)
    required = [str(x or '').strip() for x in list(schema.get('required_fields') or []) if str(x or '').strip()]
    errors: List[Dict[str, str]] = []
    for field in required:
        if field not in payload:
            errors.append({'code': 'missing_field', 'message': f'missing required field: {field}'})
    required_sections = [str(x or '').strip() for x in list(schema.get('required_sections') or []) if str(x or '').strip()]
    if list(payload.get('section_order') or []) != required_sections:
        errors.append({'code': 'bad_section_order', 'message': 'section_order must match required section sequence'})
    if not isinstance(payload.get('recommended_actions'), list):
        errors.append({'code': 'invalid_recommended_actions', 'message': 'recommended_actions must be a list'})
    if not isinstance(payload.get('codex_transparency'), Mapping):
        errors.append({'code': 'invalid_codex_transparency', 'message': 'codex_transparency must be a mapping'})
    executive = dict(payload.get("executive_surface") or {})
    if not executive:
        errors.append({'code': 'missing_executive_surface', 'message': 'executive_surface must be present'})
    else:
        allowed_states = {str(x or '').strip() for x in list(schema.get("allowed_executive_states") or []) if str(x or '').strip()}
        allowed_confidence = {str(x or '').strip() for x in list(schema.get("allowed_executive_confidence") or []) if str(x or '').strip()}
        state = str(executive.get("state") or "").strip()
        confidence = str(executive.get("confidence") or "").strip()
        if state not in allowed_states:
            errors.append({'code': 'bad_executive_state', 'message': f'unknown executive state: {state}'})
        if confidence not in allowed_confidence:
            errors.append({'code': 'bad_executive_confidence', 'message': f'unknown executive confidence: {confidence}'})
        if len([key for key in ("state", "primary_next_action", "confidence", "short_reason") if key in executive and executive.get(key) not in (None, {}, [])]) > 4:
            errors.append({'code': 'too_many_executive_signals', 'message': 'executive surface may show at most 4 top-level signals'})
    recs = dict(payload.get("recommendations") or {})
    primary = dict(recs.get("primary") or {})
    executive_state = str(executive.get("state") or "").strip()
    if recs and not primary and executive_state != "PASS":
        errors.append({'code': 'missing_primary_recommendation', 'message': 'recommendations.primary must be present unless state is PASS'})
    if primary and str(primary.get("action_id") or "").strip() == "":
        errors.append({'code': 'empty_primary_action', 'message': 'recommendations.primary.action_id must be non-empty'})
    return {'ok': not errors, 'errors': errors, 'payload': dict(payload)}
