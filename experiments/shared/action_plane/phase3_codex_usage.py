"""Phase-3 Codex usage transparency helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .phase1_runtime import synthesize_improvement_opportunity_v1
from .phase3_state_view import build_operator_derived_state_view_v1

CODEX_USAGE_TRANSPARENCY_SCHEMA_PATH = Path("governance/contracts/codex_usage_transparency_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_codex_usage_transparency_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / CODEX_USAGE_TRANSPARENCY_SCHEMA_PATH)


def _as_list_of_mappings(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(value, Mapping):
        return [dict(value)]
    for item in list(value or []):
        if isinstance(item, Mapping):
            rows.append(dict(item))
    return rows


def _unique(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _load_opportunity(payload: Mapping[str, Any], events: List[Dict[str, Any]], repo_root: Optional[Path]) -> Dict[str, Any]:
    opportunities = _as_list_of_mappings(payload.get("improvement_opportunities"))
    if not opportunities and isinstance(payload.get("improvement_opportunity"), Mapping):
        opportunities = [dict(payload.get("improvement_opportunity") or {})]
    if opportunities:
        return opportunities[-1]
    if len(events) >= 2:
        try:
            return synthesize_improvement_opportunity_v1(events, repo_root=repo_root)
        except Exception:
            return {}
    return {}


def build_codex_usage_transparency_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_codex_usage_transparency_schema(repo_root=repo_root)
    view = dict(payload.get("derived_state_view") or {})
    if not view:
        view = build_operator_derived_state_view_v1(payload, repo_root=repo_root)
    events = _as_list_of_mappings(payload.get("fallback_events"))
    opportunity = _load_opportunity(payload, events, repo_root)
    latest = events[-1] if events else {}
    codex_used = bool(events) or bool(view.get("codex_used"))
    family_values = {str(row.get("action_family") or "").strip() for row in events if str(row.get("action_family") or "").strip()}
    if len(family_values) == 1:
        action_family = next(iter(family_values))
    else:
        action_family = str(latest.get("action_family") or view.get("action_family") or "").strip()
    usage_reason = str(latest.get("fallback_reason") or latest.get("failure_class") or ("fallback_history_present" if codex_used else "")).strip()
    event_count = len(events)
    decision = str(opportunity.get("decision") or "").strip()
    rejection_reason = str(opportunity.get("rejection_reason") or "").strip()
    econ_positive = bool(opportunity.get("economic_absorption_positive"))

    if not codex_used:
        recurrence = 'none'
        absorption = 'none'
        economic = 'none'
    elif event_count == 0:
        recurrence = 'unknown'
        absorption = 'unknown'
        economic = 'manual_review'
    elif event_count == 1 and not decision:
        recurrence = 'one_off'
        absorption = 'insufficient_history'
        economic = 'observe_more'
    elif decision == 'promote_opportunity' and econ_positive:
        recurrence = 'repeated_absorbable'
        absorption = 'eligible'
        economic = 'build_deterministic_capability'
    elif rejection_reason == 'bad_cost_benefit':
        recurrence = 'repeated_uneconomic'
        absorption = 'uneconomic'
        economic = 'keep_codex_fallback'
    elif decision == 'reject_opportunity':
        recurrence = 'repeated'
        absorption = 'rejected'
        economic = 'manual_review'
    else:
        recurrence = 'repeated' if event_count >= 2 else 'one_off'
        absorption = 'unknown'
        economic = 'manual_review'

    evidence_refs = _unique([
        'fallback_events' if events else '',
        'improvement_opportunity' if opportunity else '',
        'derived_state_view.codex_used' if bool(view.get('codex_used')) else '',
    ])

    return {
        'schema_version': 'codex_usage_transparency_v1',
        'codex_used': codex_used,
        'action_family': action_family,
        'usage_reason': usage_reason,
        'fallback_event_count': event_count,
        'recurrence_class': recurrence,
        'absorption_eligibility_class': absorption,
        'economic_recommendation_class': economic,
        'evidence_references': evidence_refs,
        'improvement_decision': decision,
        'rejection_reason': rejection_reason,
    }


def validate_codex_usage_transparency_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_codex_usage_transparency_schema(repo_root=repo_root)
    required = [str(x or '').strip() for x in list(schema.get('required_fields') or []) if str(x or '').strip()]
    errors: List[Dict[str, str]] = []
    for field in required:
        if field not in payload:
            errors.append({'code': 'missing_field', 'message': f'missing required field: {field}'})
    for field, allowed_key in [
        ('recurrence_class', 'allowed_recurrence_classes'),
        ('absorption_eligibility_class', 'allowed_absorption_eligibility_classes'),
        ('economic_recommendation_class', 'allowed_economic_recommendation_classes'),
    ]:
        value = str(payload.get(field) or '').strip()
        allowed = {str(x or '').strip() for x in list(schema.get(allowed_key) or []) if str(x or '').strip()}
        if value and value not in allowed:
            errors.append({'code': f'unknown_{field}', 'message': f'unknown {field}: {value}'})
    if not isinstance(payload.get('evidence_references'), list):
        errors.append({'code': 'invalid_evidence_references', 'message': 'evidence_references must be a list'})
    return {'ok': not errors, 'errors': errors, 'payload': dict(payload)}
