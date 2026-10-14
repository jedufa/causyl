"""Phase-4 economic decision helpers for absorption candidates."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .phase4_candidate import synthesize_absorption_candidates_v1

ABSORPTION_ECONOMIC_DECISION_SCHEMA_PATH = Path("governance/contracts/absorption_economic_decision_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_absorption_economic_decision_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / ABSORPTION_ECONOMIC_DECISION_SCHEMA_PATH)


def decide_absorption_economics_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, min_recurrence: int = 2) -> Dict[str, Any]:
    _ = load_absorption_economic_decision_schema(repo_root=repo_root)
    candidate = dict(payload.get('candidate') or {})
    if not candidate:
        synthesized = synthesize_absorption_candidates_v1(payload, repo_root=repo_root)
        candidate = dict((synthesized.get('candidates') or [{}])[0])
    if not candidate:
        raise ValueError('candidate is required for economic decision')

    recurrence_count = int(candidate.get('recurrence_count') or 0)
    future_cost = float(candidate.get('expected_future_codex_cost_total') or 0.0) + float(candidate.get('operator_friction_cost_total') or 0.0)
    build_cost = float(candidate.get('estimated_build_cost_max') or 0.0) + float(candidate.get('estimated_maintenance_cost_max') or 0.0)
    authority_risk = str(candidate.get('authority_risk_class') or '').strip()
    validator_clarity = str(candidate.get('validator_clarity_class') or '').strip()
    expected_value_score = round(future_cost - build_cost, 3)

    if recurrence_count < int(min_recurrence):
        decision, reason, confidence = 'rejected', 'low_recurrence', 'high'
    elif authority_risk != 'bounded_safe':
        decision, reason, confidence = 'rejected', 'unsafe_authority', 'high'
    elif validator_clarity != 'clear':
        decision, reason, confidence = 'deferred', 'validator_path_unclear', 'medium'
    elif future_cost <= 0 or build_cost <= 0:
        decision, reason, confidence = 'deferred', 'insufficient_cost_confidence', 'low'
    elif expected_value_score > 0:
        decision, reason, confidence = 'approved', 'positive_cost_benefit', 'high'
    else:
        decision, reason, confidence = 'rejected', 'bad_cost_benefit', 'high'

    return {
        'schema_version': 'absorption_economic_decision_v1',
        'candidate_id': str(candidate.get('candidate_id') or '').strip(),
        'decision': decision,
        'reason_class': reason,
        'expected_value_score': expected_value_score,
        'confidence_bucket': confidence,
        'required_additional_evidence': [] if decision != 'deferred' else ['recurrence_or_cost_inputs'],
        'candidate': candidate,
    }


def validate_absorption_economic_decision_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_absorption_economic_decision_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or '').strip() for x in list(schema.get('required_fields') or []) if str(x or '').strip()]:
        if field not in payload:
            errors.append({'code': 'missing_field', 'message': f'missing required field: {field}'})
    decision = str(payload.get('decision') or '').strip()
    allowed_decisions = {str(x or '').strip() for x in list(schema.get('allowed_decisions') or []) if str(x or '').strip()}
    if decision and decision not in allowed_decisions:
        errors.append({'code': 'unknown_decision', 'message': f'unknown decision: {decision}'})
    bucket = str(payload.get('confidence_bucket') or '').strip()
    allowed_buckets = {str(x or '').strip() for x in list(schema.get('allowed_confidence_buckets') or []) if str(x or '').strip()}
    if bucket and bucket not in allowed_buckets:
        errors.append({'code': 'unknown_confidence_bucket', 'message': f'unknown confidence_bucket: {bucket}'})
    return {'ok': not errors, 'errors': errors, 'payload': dict(payload)}
