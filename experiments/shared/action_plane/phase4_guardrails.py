"""Phase-4 regression guardrail helpers for approved upgrade proposals."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

ABSORPTION_REGRESSION_GUARDRAIL_SCHEMA_PATH = Path("governance/contracts/absorption_regression_guardrail_v1.json")


_GUARDRAIL_CLASS_BY_UPGRADE_TYPE = {
    'wrapper_helper_addition': 'targeted_regression',
    'proof_extraction_improvement': 'targeted_regression',
    'classifier_refinement': 'focused_end_to_end',
    'recovery_rule_addition': 'focused_end_to_end',
    'playbook_refinement': 'family_regression',
}

_PROOF_REQUIREMENTS_BY_CLASS = {
    'targeted_regression': ['affected_family_happy_path_green'],
    'focused_end_to_end': ['affected_family_happy_path_green', 'targeted_end_to_end_green'],
    'family_regression': ['affected_family_happy_path_green', 'family_regression_green', 'rollback_plan_present'],
}


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_absorption_regression_guardrail_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / ABSORPTION_REGRESSION_GUARDRAIL_SCHEMA_PATH)


def assign_absorption_regression_guardrails_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_absorption_regression_guardrail_schema(repo_root=repo_root)
    proposal = dict(payload.get('proposal') or {})
    proposal_id = str(proposal.get('proposal_id') or '').strip()
    if not proposal_id:
        return {
            'schema_version': 'absorption_regression_guardrail_v1',
            'proposal_id': '',
            'required_regression_suite_class': 'targeted_regression',
            'rollout_eligibility': 'blocked',
            'proof_requirements': [],
            'rollback_expectations': [],
            'blocked_reason': 'missing_proposal',
        }
    upgrade_type = str(proposal.get('proposed_upgrade_type') or '').strip()
    suite_class = _GUARDRAIL_CLASS_BY_UPGRADE_TYPE.get(upgrade_type, 'focused_end_to_end')
    proof_requirements = list(_PROOF_REQUIREMENTS_BY_CLASS.get(suite_class) or ['affected_family_happy_path_green'])
    regression_gates = list(proposal.get('required_regression_gates') or [])
    rollout_eligibility = 'eligible_for_guarded_rollout' if regression_gates else 'blocked'
    blocked_reason = '' if regression_gates else 'missing_regression_assignment'
    rollback_expectations = [
        'retain prior deterministic family behavior',
        'revert to previous approved deterministic capability on regression failure',
    ]
    return {
        'schema_version': 'absorption_regression_guardrail_v1',
        'proposal_id': proposal_id,
        'required_regression_suite_class': suite_class,
        'rollout_eligibility': rollout_eligibility,
        'proof_requirements': proof_requirements,
        'rollback_expectations': rollback_expectations,
        'blocked_reason': blocked_reason,
    }


def validate_absorption_regression_guardrail_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_absorption_regression_guardrail_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or '').strip() for x in list(schema.get('required_fields') or []) if str(x or '').strip()]:
        if field not in payload:
            errors.append({'code': 'missing_field', 'message': f'missing required field: {field}'})
    allowed_class = {str(x or '').strip() for x in list(schema.get('allowed_regression_suite_classes') or []) if str(x or '').strip()}
    suite_class = str(payload.get('required_regression_suite_class') or '').strip()
    if suite_class and suite_class not in allowed_class:
        errors.append({'code': 'unknown_regression_suite_class', 'message': f'unknown required_regression_suite_class: {suite_class}'})
    allowed_rollout = {str(x or '').strip() for x in list(schema.get('allowed_rollout_eligibility') or []) if str(x or '').strip()}
    rollout = str(payload.get('rollout_eligibility') or '').strip()
    if rollout and rollout not in allowed_rollout:
        errors.append({'code': 'unknown_rollout_eligibility', 'message': f'unknown rollout_eligibility: {rollout}'})
    return {'ok': not errors, 'errors': errors, 'payload': dict(payload)}
