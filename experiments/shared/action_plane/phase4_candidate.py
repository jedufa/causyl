"""Phase-4 absorption candidate synthesis helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .phase4_cluster import cluster_fallback_patterns_v1

ABSORPTION_CANDIDATE_SCHEMA_PATH = Path("governance/contracts/absorption_candidate_v1.json")

_PLAYBOOK_BY_FAMILY = {
    'patch_apply': 'patch_apply_chain_v1',
    'build_verify': 'build_verify_loop_v1',
    'status_reconcile': 'status_reconcile_v1',
}


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_absorption_candidate_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / ABSORPTION_CANDIDATE_SCHEMA_PATH)


def _pick_upgrade_type(cluster: Mapping[str, Any]) -> str:
    failure_class = str(cluster.get('failure_class') or '').lower()
    if 'patch_contract' in failure_class or 'metadata_wrapper' in failure_class:
        return 'wrapper_helper_addition'
    if 'semantic' in failure_class or 'stale' in failure_class or 'apply' in failure_class:
        return 'recovery_rule_addition'
    if 'proof' in failure_class or 'validation' in failure_class:
        return 'proof_extraction_improvement'
    return 'classifier_refinement'


def synthesize_absorption_candidates_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    _ = load_absorption_candidate_schema(repo_root=repo_root)
    clusters_payload = payload.get('clusters') if isinstance(payload, Mapping) else None
    clusters = list(clusters_payload or [])
    if not clusters:
        clusters = list(cluster_fallback_patterns_v1(payload, repo_root=repo_root).get('clusters') or [])
    existing_ids = {str(row.get('candidate_id') or '').strip() for row in list(payload.get('existing_candidates') or []) if isinstance(row, Mapping)}
    candidates: List[Dict[str, Any]] = []
    for row in clusters:
        if not isinstance(row, Mapping):
            continue
        if not bool(row.get('candidate_ready')):
            continue
        if not bool(row.get('bounded_authority_safe')):
            continue
        if not bool(row.get('validator_path_clear')):
            continue
        action_family = str(row.get('action_family') or '').strip()
        cluster_id = str(row.get('cluster_id') or '').strip()
        candidate_id = f"candidate::{cluster_id}"
        duplicate_of = candidate_id if candidate_id in existing_ids else ''
        if duplicate_of:
            continue
        candidates.append({
            'candidate_id': candidate_id,
            'cluster_id': cluster_id,
            'target_action_family': action_family,
            'target_playbook_id': _PLAYBOOK_BY_FAMILY.get(action_family, ''),
            'proposed_upgrade_type': _pick_upgrade_type(row),
            'validator_clarity_class': 'clear' if bool(row.get('validator_path_clear')) else 'unclear',
            'authority_risk_class': 'bounded_safe' if bool(row.get('bounded_authority_safe')) else 'broad_or_risky',
            'recurrence_count': int(row.get('recurrence_count') or 0),
            'stability_class': str(row.get('stability_class') or '').strip(),
            'expected_future_codex_cost_total': float(row.get('expected_future_codex_cost_total') or 0.0),
            'operator_friction_cost_total': float(row.get('operator_friction_cost_total') or 0.0),
            'estimated_build_cost_max': float(row.get('estimated_build_cost_max') or 0.0),
            'estimated_maintenance_cost_max': float(row.get('estimated_maintenance_cost_max') or 0.0),
            'evidence_references': list(row.get('representative_event_references') or []),
        })
    return {
        'schema_version': 'absorption_candidate_v1',
        'candidates': candidates,
    }


def validate_absorption_candidate_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_absorption_candidate_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    for field in [str(x or '').strip() for x in list(schema.get('required_fields') or []) if str(x or '').strip()]:
        if field not in payload:
            errors.append({'code': 'missing_field', 'message': f'missing required field: {field}'})
    allowed_types = {str(x or '').strip() for x in list(schema.get('allowed_upgrade_types') or []) if str(x or '').strip()}
    for row in list(payload.get('candidates') or []):
        if not isinstance(row, Mapping):
            errors.append({'code': 'invalid_candidate_row', 'message': 'candidate rows must be mappings'})
            continue
        for field in [str(x or '').strip() for x in list(schema.get('required_candidate_fields') or []) if str(x or '').strip()]:
            if field not in row:
                errors.append({'code': 'missing_candidate_field', 'message': f'candidate missing field: {field}'})
        upgrade_type = str(row.get('proposed_upgrade_type') or '').strip()
        if upgrade_type and upgrade_type not in allowed_types:
            errors.append({'code': 'unknown_upgrade_type', 'message': f'unknown proposed_upgrade_type: {upgrade_type}'})
    return {'ok': not errors, 'errors': errors, 'payload': dict(payload)}
