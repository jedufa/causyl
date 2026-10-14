"""Phase-4 deterministic upgrade proposal pipeline helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

DETERMINISTIC_UPGRADE_PIPELINE_SCHEMA_PATH = Path("governance/contracts/deterministic_upgrade_pipeline_v1.json")


_IMPLEMENTATION_PATH_BY_TYPE = {
    'classifier_refinement': 'classifier_logic_change',
    'recovery_rule_addition': 'recovery_rule_change',
    'wrapper_helper_addition': 'wrapper_helper_addition',
    'playbook_refinement': 'playbook_logic_change',
    'proof_extraction_improvement': 'reporting_logic_change',
}

_REGRESSION_GATES_BY_PATH = {
    'classifier_logic_change': ['targeted_classifier_regression', 'proof_contract_regression'],
    'recovery_rule_change': ['targeted_recovery_regression', 'family_happy_path_regression'],
    'wrapper_helper_addition': ['targeted_helper_regression'],
    'playbook_logic_change': ['focused_playbook_regression', 'family_happy_path_regression'],
    'reporting_logic_change': ['targeted_reporting_regression'],
}


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_deterministic_upgrade_pipeline_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / DETERMINISTIC_UPGRADE_PIPELINE_SCHEMA_PATH)


def generate_deterministic_upgrade_proposal_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_deterministic_upgrade_pipeline_schema(repo_root=repo_root)
    candidate = dict(payload.get('candidate') or {})
    decision = dict(payload.get('economic_decision') or {})
    decision_value = str(decision.get('decision') or '').strip()
    candidate_id = str(candidate.get('candidate_id') or '').strip()
    if decision_value != 'approved' or not candidate_id:
        return {
            'schema_version': 'deterministic_upgrade_pipeline_v1',
            'candidate_id': candidate_id,
            'decision': decision_value or 'rejected',
            'proposal_created': False,
            'proposal': None,
        }
    upgrade_type = str(candidate.get('proposed_upgrade_type') or '').strip()
    implementation_path = _IMPLEMENTATION_PATH_BY_TYPE.get(upgrade_type, 'classifier_logic_change')
    required_regression_gates = list(_REGRESSION_GATES_BY_PATH.get(implementation_path) or ['targeted_regression'])
    proposal = {
        'proposal_id': f"upgrade::{candidate_id}",
        'candidate_id': candidate_id,
        'target_action_family': str(candidate.get('target_action_family') or '').strip(),
        'target_playbook_id': str(candidate.get('target_playbook_id') or '').strip(),
        'proposed_upgrade_type': upgrade_type,
        'implementation_path_class': implementation_path,
        'required_regression_gates': required_regression_gates,
        'governance_item_payload': {
            'title': f"Absorb {upgrade_type} for {str(candidate.get('target_action_family') or 'unknown').strip()}",
            'kind': 'deterministic_upgrade_proposal',
            'candidate_id': candidate_id,
            'approval_reason_class': str(decision.get('reason_class') or '').strip(),
        },
        'evidence_references': list(candidate.get('evidence_references') or []),
        'proposal_state': 'proposal_ready',
        'approval_reason_class': str(decision.get('reason_class') or '').strip(),
    }
    return {
        'schema_version': 'deterministic_upgrade_pipeline_v1',
        'candidate_id': candidate_id,
        'decision': decision_value,
        'proposal_created': True,
        'proposal': proposal,
    }


def validate_deterministic_upgrade_pipeline_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_deterministic_upgrade_pipeline_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or '').strip() for x in list(schema.get('required_fields') or []) if str(x or '').strip()]:
        if field not in payload:
            errors.append({'code': 'missing_field', 'message': f'missing required field: {field}'})
    if bool(payload.get('proposal_created')):
        proposal = payload.get('proposal')
        if not isinstance(proposal, Mapping):
            errors.append({'code': 'missing_proposal', 'message': 'proposal_created requires proposal mapping'})
        else:
            for field in [str(x or '').strip() for x in list(schema.get('required_proposal_fields') or []) if str(x or '').strip()]:
                if field not in proposal:
                    errors.append({'code': 'missing_proposal_field', 'message': f'proposal missing field: {field}'})
            value = str(proposal.get('implementation_path_class') or '').strip()
            allowed = {str(x or '').strip() for x in list(schema.get('allowed_implementation_path_classes') or []) if str(x or '').strip()}
            if value and value not in allowed:
                errors.append({'code': 'unknown_implementation_path_class', 'message': f'unknown implementation_path_class: {value}'})
    return {'ok': not errors, 'errors': errors, 'payload': dict(payload)}
