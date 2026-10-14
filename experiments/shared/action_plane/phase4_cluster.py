"""Phase-4 fallback pattern clustering and normalization helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .phase1_runtime import validate_fallback_event_payload

FALLBACK_PATTERN_CLUSTER_SCHEMA_PATH = Path("governance/contracts/fallback_pattern_cluster_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_fallback_pattern_cluster_schema(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / FALLBACK_PATTERN_CLUSTER_SCHEMA_PATH)


def _as_list_of_mappings(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for item in list(value or []):
        if isinstance(item, Mapping):
            rows.append(dict(item))
    return rows


def _event_ref(row: Mapping[str, Any], index: int) -> str:
    for key in ("event_id", "fallback_event_id", "id"):
        text = str(row.get(key) or "").strip()
        if text:
            return text
    return f"fallback_event:{index}"


def _cluster_id(action_family: str, failure_class: str, solution_path_signature: str) -> str:
    parts = [action_family.strip() or 'unknown_family', failure_class.strip() or 'unknown_failure', solution_path_signature.strip() or 'unknown_solution']
    return 'cluster::' + '::'.join(part.replace(' ', '_') for part in parts)


def cluster_fallback_patterns_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, min_recurrence: int = 2) -> Dict[str, Any]:
    _ = load_fallback_pattern_cluster_schema(repo_root=repo_root)
    events = _as_list_of_mappings(payload.get('fallback_events') if isinstance(payload, Mapping) else payload)
    groups: Dict[tuple[str, str, str], List[Dict[str, Any]]] = {}
    skipped: List[Dict[str, str]] = []
    for index, row in enumerate(events):
        try:
            if not validate_fallback_event_payload(row, repo_root=repo_root).get("ok"):
                raise ValueError("invalid fallback event")
        except Exception:
            skipped.append({'event_ref': _event_ref(row, index), 'reason': 'invalid_fallback_event'})
            continue
        action_family = str(row.get('action_family') or '').strip()
        failure_class = str(row.get('failure_class') or '').strip()
        solution_path_signature = str(row.get('solution_path_signature') or '').strip()
        if not action_family or not failure_class or not solution_path_signature:
            skipped.append({'event_ref': _event_ref(row, index), 'reason': 'insufficient_pattern_keys'})
            continue
        groups.setdefault((action_family, failure_class, solution_path_signature), []).append(dict(row, _event_ref=_event_ref(row, index)))

    clusters: List[Dict[str, Any]] = []
    for key in sorted(groups.keys()):
        rows = groups[key]
        action_family, failure_class, solution_path_signature = key
        recurrence_count = len(rows)
        outcomes = sorted({str(row.get('post_fallback_outcome') or '').strip() for row in rows if str(row.get('post_fallback_outcome') or '').strip()})
        if recurrence_count < 2:
            stability_class = 'one_off'
        elif len(outcomes) > 1:
            stability_class = 'mixed_outcome'
        else:
            stability_class = 'stable'
        representative_refs = [row['_event_ref'] for row in rows[:3]]
        clusters.append({
            'cluster_id': _cluster_id(action_family, failure_class, solution_path_signature),
            'action_family': action_family,
            'failure_class': failure_class,
            'solution_path_signature': solution_path_signature,
            'recurrence_count': recurrence_count,
            'stability_class': stability_class,
            'representative_event_references': representative_refs,
            'candidate_ready': recurrence_count >= int(min_recurrence) and stability_class == 'stable',
            'expected_future_codex_cost_total': round(sum(float(row.get('expected_future_codex_cost') or 0.0) for row in rows), 3),
            'operator_friction_cost_total': round(sum(float(row.get('operator_friction_cost') or 0.0) for row in rows), 3),
            'estimated_build_cost_max': round(max(float(row.get('estimated_build_cost') or 0.0) for row in rows), 3),
            'estimated_maintenance_cost_max': round(max(float(row.get('estimated_maintenance_cost') or 0.0) for row in rows), 3),
            'bounded_authority_safe': all(bool(row.get('bounded_authority_safe')) for row in rows),
            'validator_path_clear': all(bool(row.get('validator_path_clear')) for row in rows),
        })

    return {
        'schema_version': 'fallback_pattern_cluster_v1',
        'clusters': clusters,
        'skipped_events': skipped,
    }


def validate_fallback_pattern_cluster_payload(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    schema = load_fallback_pattern_cluster_schema(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    required = [str(x or '').strip() for x in list(schema.get('required_fields') or []) if str(x or '').strip()]
    for field in required:
        if field not in payload:
            errors.append({'code': 'missing_field', 'message': f'missing required field: {field}'})
    allowed_stability = {str(x or '').strip() for x in list(schema.get('allowed_stability_classes') or []) if str(x or '').strip()}
    required_cluster = [str(x or '').strip() for x in list(schema.get('required_cluster_fields') or []) if str(x or '').strip()]
    for row in list(payload.get('clusters') or []):
        if not isinstance(row, Mapping):
            errors.append({'code': 'invalid_cluster_row', 'message': 'cluster rows must be mappings'})
            continue
        for field in required_cluster:
            if field not in row:
                errors.append({'code': 'missing_cluster_field', 'message': f'cluster missing field: {field}'})
        stability = str(row.get('stability_class') or '').strip()
        if stability and stability not in allowed_stability:
            errors.append({'code': 'unknown_stability_class', 'message': f'unknown stability_class: {stability}'})
    return {'ok': not errors, 'errors': errors, 'payload': dict(payload)}
