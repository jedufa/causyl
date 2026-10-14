"""Concrete Phase-4 absorption flows and regression helpers."""

from __future__ import annotations

import json

from .contract_source import load_contract
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from .phase1_runtime import synthesize_improvement_opportunity_v1
from .phase2_playbooks import validate_phase2_playbook_contracts
from .phase3_flow import execute_phase3_blocked_chain_explain_and_recommend_v1, build_phase3_regression_report
from .phase4_candidate import synthesize_absorption_candidates_v1, validate_absorption_candidate_payload
from .phase4_cluster import cluster_fallback_patterns_v1, validate_fallback_pattern_cluster_payload
from .phase4_economics import decide_absorption_economics_v1, validate_absorption_economic_decision_payload
from .phase4_upgrade_pipeline import generate_deterministic_upgrade_proposal_v1, validate_deterministic_upgrade_pipeline_payload
from .phase4_guardrails import assign_absorption_regression_guardrails_v1, validate_absorption_regression_guardrail_payload

PHASE4_OPERATOR_FLOW_REGISTRY_PATH = Path("governance/contracts/phase4_operator_flow_registry_v1.json")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_phase4_operator_flow_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _load_json(_repo_root(repo_root) / PHASE4_OPERATOR_FLOW_REGISTRY_PATH)


def validate_phase4_operator_flow_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry = load_phase4_operator_flow_registry(repo_root=repo_root)
    errors: List[Dict[str, str]] = []
    if str(registry.get('schema_version') or '').strip() != 'phase4_operator_flow_registry_v1':
        errors.append({'code': 'unexpected_schema_version', 'message': 'phase4 operator flow registry schema mismatch'})
    flows = list(registry.get('flows') or [])
    if not flows:
        errors.append({'code': 'missing_flow', 'message': 'at least one phase4 flow is required'})
    elif str(dict(flows[0]).get('flow_id') or '').strip() != 'repeated_patch_fallback_to_approved_upgrade_v1':
        errors.append({'code': 'unexpected_flow_id', 'message': 'first flow must be repeated_patch_fallback_to_approved_upgrade_v1'})
    return {'ok': not errors, 'errors': errors, 'registry': registry}


def execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry_validation = validate_phase4_operator_flow_registry(repo_root=repo_root)
    if not registry_validation.get('ok'):
        return {'ok': False, 'errors': list(registry_validation.get('errors') or []), 'phase': 'registry'}
    clustering = cluster_fallback_patterns_v1(payload, repo_root=repo_root)
    cluster_validation = validate_fallback_pattern_cluster_payload(clustering, repo_root=repo_root)
    patch_clusters = [dict(row) for row in list(clustering.get('clusters') or []) if str(row.get('action_family') or '').strip() in {'patch_apply', 'patch_ingest_repair'}]
    candidate_payload = synthesize_absorption_candidates_v1({'clusters': patch_clusters}, repo_root=repo_root)
    candidate_validation = validate_absorption_candidate_payload(candidate_payload, repo_root=repo_root)
    candidate = dict((candidate_payload.get('candidates') or [{}])[0])
    economic = decide_absorption_economics_v1({'candidate': candidate}, repo_root=repo_root) if candidate else {
        'schema_version': 'absorption_economic_decision_v1', 'candidate_id': '', 'decision': 'rejected', 'reason_class': 'no_candidate', 'expected_value_score': 0.0, 'confidence_bucket': 'high', 'required_additional_evidence': []
    }
    economic_validation = validate_absorption_economic_decision_payload(economic, repo_root=repo_root)
    proposal_output = generate_deterministic_upgrade_proposal_v1({'candidate': candidate, 'economic_decision': economic}, repo_root=repo_root)
    proposal_validation = validate_deterministic_upgrade_pipeline_payload(proposal_output, repo_root=repo_root)
    guardrail_output = None
    guardrail_validation = {'ok': True, 'errors': [], 'payload': {}}
    if proposal_output.get('proposal_created'):
        guardrail_output = assign_absorption_regression_guardrails_v1({'proposal': proposal_output.get('proposal')}, repo_root=repo_root)
        guardrail_validation = validate_absorption_regression_guardrail_payload(guardrail_output, repo_root=repo_root)
        proposal = dict(proposal_output.get('proposal') or {})
        if guardrail_output:
            proposal['guardrail_output'] = guardrail_output
            proposal['proposal_state'] = 'proposal_guarded'
            proposal_output['proposal'] = proposal
    ok = bool(cluster_validation.get('ok')) and bool(candidate_validation.get('ok')) and bool(economic_validation.get('ok')) and bool(proposal_validation.get('ok')) and bool(guardrail_validation.get('ok'))
    upgrade_proposal_stub = None
    if proposal_output.get('proposal_created'):
        upgrade_proposal_stub = dict(proposal_output.get('proposal') or {})
        upgrade_proposal_stub['proposal_state'] = 'approved_candidate_only'
        if guardrail_output:
            upgrade_proposal_stub['guardrail_status'] = 'deferred_to_phase4_guardrails'
            upgrade_proposal_stub['rollback_status'] = 'deferred_to_phase4_guardrails'
    return {
        'ok': ok,
        'flow_id': 'repeated_patch_fallback_to_approved_upgrade_v1',
        'cluster_output': clustering,
        'candidate_output': candidate_payload,
        'economic_decision_output': economic,
        'upgrade_pipeline_output': proposal_output,
        'upgrade_proposal_stub': upgrade_proposal_stub,
        'guardrail_output': guardrail_output,
        'validations': {
            'cluster_output': cluster_validation,
            'candidate_output': candidate_validation,
            'economic_decision_output': economic_validation,
            'upgrade_pipeline_output': proposal_validation,
            'guardrail_output': guardrail_validation,
        },
    }


def build_phase4_regression_report(results: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    scenarios = {str(key): dict(value) for key, value in dict(results).items()}
    checks = {
        'all_validations_green': all(all(bool(v.get('ok')) for v in dict(row.get('validations') or {}).values()) for row in scenarios.values()),
        'approved_cases_guarded': all((dict(row.get('economic_decision_output') or {}).get('decision') != 'approved') or bool(dict(row.get('guardrail_output') or {}).get('proposal_id')) for row in scenarios.values()),
        'rejected_cases_no_proposal': all((dict(row.get('economic_decision_output') or {}).get('decision') != 'rejected') or not bool(dict(row.get('upgrade_pipeline_output') or {}).get('proposal_created')) for row in scenarios.values()),
        'proposal_stage_only': all((not bool(dict(row.get('upgrade_pipeline_output') or {}).get('proposal_created'))) or str(dict(dict(row.get('upgrade_pipeline_output') or {}).get('proposal') or {}).get('proposal_state') or '').strip() in {'proposal_ready', 'proposal_guarded'} for row in scenarios.values()),
    }
    return {
        'schema_version': 'action_plane_phase4_regression_report_v1',
        'checks': checks,
        'ok': all(bool(v) for v in checks.values()),
        'results': scenarios,
    }


def build_action_plane_all_phases_report_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    fallback_events = list(payload.get('fallback_events') or [])
    blocked_report = dict(payload.get('report') or {
        'chain_id': 'patch_apply_chain_v1::coherence',
        'playbook_id': 'patch_apply_chain_v1',
        'action_family': 'patch_apply',
        'final_chain_outcome': 'blocked',
        'final_verifier_verdict': '',
        'terminal_reason': 'patch_contract_invalid',
        'step_records': [{'node_id': 'patch_ingest', 'step_outcome': 'failed_known', 'proof_gaps': ['missing_validation_commands'], 'decision': 'block', 'checklist_complete': True}],
        'failure_escalation_log': [],
    })
    phase1 = synthesize_improvement_opportunity_v1(fallback_events[:2] or fallback_events, repo_root=repo_root) if fallback_events else {}
    phase2 = validate_phase2_playbook_contracts(repo_root=repo_root)
    phase3 = execute_phase3_blocked_chain_explain_and_recommend_v1({'report': blocked_report, 'fallback_events': fallback_events}, repo_root=repo_root)
    phase3_regression = build_phase3_regression_report({'blocked': phase3}, repo_root=repo_root)
    phase4 = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1({'fallback_events': fallback_events}, repo_root=repo_root)
    checks = {
        'phase1_promotes_or_rejects_explicitly': bool(phase1) and str(phase1.get('decision') or '').strip() in {'promote_opportunity', 'reject_opportunity'},
        'phase2_contracts_valid': bool(phase2.get('ok')),
        'phase3_operator_flow_valid': bool(phase3.get('ok')) and bool(phase3_regression.get('ok')),
        'phase3_recommends_approved_patch_action': any(str(row.get('action_id') or '').strip() == 'patch_apply_chain_v1' for row in list(dict(phase3.get('recommendation_output') or {}).get('recommendations') or [])),
        'phase4_flow_valid': bool(phase4.get('ok')),
        'phase4_targets_patch_apply_family': str(dict((dict(phase4.get('candidate_output') or {}).get('candidates') or [{}])[0]).get('target_action_family') or '').strip() == 'patch_apply',
        'phase4_guardrails_present_when_approved': (dict(phase4.get('economic_decision_output') or {}).get('decision') != 'approved') or bool(phase4.get('guardrail_output')),
        'phases_align_on_patch_family': str(phase1.get('action_family') or '').strip() == 'patch_apply' and str(dict(phase3.get('derived_state_view') or {}).get('action_family') or '').strip() == 'patch_apply',
    }
    return {
        'schema_version': 'action_plane_all_phases_report_v1',
        'checks': checks,
        'ok': all(bool(v) for v in checks.values()),
        'phase1': phase1,
        'phase2': phase2,
        'phase3': phase3,
        'phase4': phase4,
    }
