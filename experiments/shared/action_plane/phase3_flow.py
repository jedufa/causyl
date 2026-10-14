"""Concrete Phase-3 operator flow and regression helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .phase3_codex_usage import build_codex_usage_transparency_v1, validate_codex_usage_transparency_payload
from .phase3_explainer import explain_operator_state_v1, validate_state_explanation_output
from .phase3_recommender import recommend_next_safe_actions_v1, validate_next_safe_action_recommendation_payload
from .phase3_state_view import build_operator_derived_state_view_v1, validate_operator_derived_state_view_payload
from .phase3_surface import build_operator_action_surface_v1, validate_operator_action_surface_payload
from .phase3_uncertainty import build_proof_gap_uncertainty_surface_v1, validate_proof_gap_uncertainty_surface_payload

PHASE3_OPERATOR_FLOW_REGISTRY_PATH = Path("governance/contracts/phase3_operator_flow_registry_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_phase3_operator_flow_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / PHASE3_OPERATOR_FLOW_REGISTRY_PATH)


def validate_phase3_operator_flow_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry = load_phase3_operator_flow_registry(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    if str(registry.get('schema_version') or '').strip() != 'phase3_operator_flow_registry_v1':
        errors.append({'code': 'unexpected_schema_version', 'message': 'phase3 operator flow registry schema mismatch'})
    flows = list(registry.get('flows') or [])
    if not flows:
        errors.append({'code': 'missing_flow', 'message': 'at least one phase3 flow is required'})
    else:
        flow = dict(flows[0])
        if str(flow.get('flow_id') or '').strip() != 'blocked_chain_explain_and_recommend_v1':
            errors.append({'code': 'unexpected_flow_id', 'message': 'first flow must be blocked_chain_explain_and_recommend_v1'})
    return {'ok': not errors, 'errors': errors, 'registry': registry}


def execute_phase3_blocked_chain_explain_and_recommend_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry_validation = validate_phase3_operator_flow_registry(repo_root=repo_root)
    if not registry_validation.get('ok'):
        return {'ok': False, 'errors': list(registry_validation.get('errors') or []), 'phase': 'registry'}

    derived_state_view = build_operator_derived_state_view_v1(payload, repo_root=repo_root)
    uncertainty_surface = build_proof_gap_uncertainty_surface_v1({'derived_state_view': derived_state_view}, repo_root=repo_root)
    explanation_output = explain_operator_state_v1({'derived_state_view': derived_state_view, 'uncertainty_surface': uncertainty_surface}, repo_root=repo_root)
    codex_usage_output = build_codex_usage_transparency_v1({
        'derived_state_view': derived_state_view,
        'fallback_events': payload.get('fallback_events'),
        'improvement_opportunities': payload.get('improvement_opportunities'),
    }, repo_root=repo_root)
    recommendation_output = recommend_next_safe_actions_v1({
        'derived_state_view': derived_state_view,
        'uncertainty_surface': uncertainty_surface,
        'explanation_output': explanation_output,
    }, repo_root=repo_root)
    operator_surface = build_operator_action_surface_v1({
        'derived_state_view': derived_state_view,
        'uncertainty_surface': uncertainty_surface,
        'explanation_output': explanation_output,
        'recommendation_output': recommendation_output,
        'codex_usage_output': codex_usage_output,
        'artifact_references': payload.get('artifact_references'),
        'fallback_events': payload.get('fallback_events'),
        'improvement_opportunities': payload.get('improvement_opportunities'),
    }, repo_root=repo_root)

    validations = {
        'derived_state_view': validate_operator_derived_state_view_payload(derived_state_view, repo_root=repo_root),
        'uncertainty_surface': validate_proof_gap_uncertainty_surface_payload(uncertainty_surface, repo_root=repo_root),
        'explanation_output': validate_state_explanation_output(explanation_output, repo_root=repo_root),
        'codex_usage_output': validate_codex_usage_transparency_payload(codex_usage_output, repo_root=repo_root),
        'recommendation_output': validate_next_safe_action_recommendation_payload(recommendation_output, repo_root=repo_root),
        'operator_surface': validate_operator_action_surface_payload(operator_surface, repo_root=repo_root),
    }
    ok = all(bool(row.get('ok')) for row in validations.values())
    return {
        'ok': ok,
        'flow_id': 'blocked_chain_explain_and_recommend_v1',
        'derived_state_view': derived_state_view,
        'uncertainty_surface': uncertainty_surface,
        'explanation_output': explanation_output,
        'codex_usage_output': codex_usage_output,
        'recommendation_output': recommendation_output,
        'operator_surface': operator_surface,
        'validations': validations,
    }


def build_phase3_regression_report(results: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    scenarios = {str(key): dict(value) for key, value in dict(results).items()}
    checks = {
        'flows_validate': all(bool(dict(row.get('validations') or {}).get('operator_surface', {}).get('ok')) for row in scenarios.values()),
        'recommendations_approved': all(validate_next_safe_action_recommendation_payload(dict(row.get('recommendation_output') or {}), repo_root=repo_root).get('ok') for row in scenarios.values()),
        'uncertainty_visible_when_present': all((not (bool(dict(row.get('uncertainty_surface') or {}).get('proof_gap_present')) or bool(dict(row.get('uncertainty_surface') or {}).get('stale_evidence_present')) or bool(dict(row.get('uncertainty_surface') or {}).get('contradiction_present')))) or bool(dict(dict(row.get('operator_surface') or {}).get('proof_uncertainty') or {}).get('proof_gap_present')) or bool(dict(dict(row.get('operator_surface') or {}).get('proof_uncertainty') or {}).get('stale_evidence_present')) or bool(dict(dict(row.get('operator_surface') or {}).get('proof_uncertainty') or {}).get('contradiction_present')) for row in scenarios.values()),
        'codex_visibility_when_used': all((not bool(dict(row.get('codex_usage_output') or {}).get('codex_used'))) or bool(dict(dict(row.get('operator_surface') or {}).get('codex_transparency') or {}).get('codex_used')) for row in scenarios.values()),
        'derived_state_descriptive_only': all(bool(dict(row.get('derived_state_view') or {}).get('evidence_provenance')) for row in scenarios.values()),
    }
    return {
        'schema_version': 'action_plane_phase3_regression_report_v1',
        'checks': checks,
        'ok': all(bool(v) for v in checks.values()),
        'results': scenarios,
    }
