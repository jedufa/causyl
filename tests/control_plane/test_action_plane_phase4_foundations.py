from __future__ import annotations

from experiments_shared import (
    cluster_fallback_patterns_v1,
    decide_absorption_economics_v1,
    execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1,
    synthesize_absorption_candidates_v1,
    validate_absorption_candidate_payload,
    validate_absorption_economic_decision_payload,
    validate_fallback_pattern_cluster_payload,
    validate_phase4_operator_flow_registry,
)


def _fallback_event(*, failure_class: str = 'semantic_conflict', cost: float = 10.0, build_cost: float = 5.0, maintenance: float = 1.0) -> dict:
    return {
        'action_family': 'patch_apply',
        'intent': 'apply incoming patch',
        'failure_class': failure_class,
        'deterministic_steps_attempted': ['patch_ingest_repair_v1', 'validation_subset_v1'],
        'fallback_reason': 'semantic_merge_required',
        'pre_escalation_checklist': {
            'evidence_gathered': True,
            'failure_class_recorded': True,
            'deterministic_recovery_exhausted_or_inapplicable': True,
            'downstream_validation_contract_prepared': True,
        },
        'codex_action_summary': 'manual merge',
        'post_fallback_outcome': 'success_after_codex_fallback',
        'solution_path_signature': 'semantic_merge_then_validate',
        'bounded_authority_safe': True,
        'validator_path_clear': True,
        'expected_future_codex_cost': cost,
        'operator_friction_cost': 1.0,
        'estimated_build_cost': build_cost,
        'estimated_maintenance_cost': maintenance,
    }


def test_phase4_clustering_groups_repeated_patch_fallbacks() -> None:
    payload = {'fallback_events': [_fallback_event(), _fallback_event(cost=12.0)]}
    clustered = cluster_fallback_patterns_v1(payload)
    assert validate_fallback_pattern_cluster_payload(clustered)['ok'] is True
    assert len(clustered['clusters']) == 1
    cluster = clustered['clusters'][0]
    assert cluster['candidate_ready'] is True
    assert cluster['recurrence_count'] == 2
    assert cluster['stability_class'] == 'stable'


def test_phase4_candidate_synthesis_creates_patch_candidate() -> None:
    clustered = cluster_fallback_patterns_v1({'fallback_events': [_fallback_event(), _fallback_event(cost=12.0)]})
    candidates = synthesize_absorption_candidates_v1({'clusters': clustered['clusters']})
    assert validate_absorption_candidate_payload(candidates)['ok'] is True
    assert len(candidates['candidates']) == 1
    candidate = candidates['candidates'][0]
    assert candidate['target_action_family'] == 'patch_apply'
    assert candidate['target_playbook_id'] == 'patch_apply_chain_v1'


def test_phase4_economic_decision_approves_positive_patch_case() -> None:
    clustered = cluster_fallback_patterns_v1({'fallback_events': [_fallback_event(cost=20.0), _fallback_event(cost=22.0)]})
    candidate = synthesize_absorption_candidates_v1({'clusters': clustered['clusters']})['candidates'][0]
    decision = decide_absorption_economics_v1({'candidate': candidate})
    assert validate_absorption_economic_decision_payload(decision)['ok'] is True
    assert decision['decision'] == 'approved'
    assert decision['reason_class'] == 'positive_cost_benefit'


def test_phase4_economic_decision_rejects_bad_cost_benefit() -> None:
    clustered = cluster_fallback_patterns_v1({'fallback_events': [_fallback_event(cost=1.0, build_cost=30.0), _fallback_event(cost=1.5, build_cost=30.0)]})
    candidate = synthesize_absorption_candidates_v1({'clusters': clustered['clusters']})['candidates'][0]
    decision = decide_absorption_economics_v1({'candidate': candidate})
    assert decision['decision'] == 'rejected'
    assert decision['reason_class'] == 'bad_cost_benefit'


def test_phase4_concrete_flow_emits_governed_proposal_stub_only_when_approved() -> None:
    assert validate_phase4_operator_flow_registry()['ok'] is True
    approved = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1({
        'fallback_events': [_fallback_event(cost=25.0), _fallback_event(cost=24.0)]
    })
    assert approved['ok'] is True
    assert approved['economic_decision_output']['decision'] == 'approved'
    assert approved['upgrade_proposal_stub']['proposal_state'] == 'approved_candidate_only'
    assert approved['upgrade_proposal_stub']['guardrail_status'] == 'deferred_to_phase4_guardrails'

    rejected = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1({
        'fallback_events': [_fallback_event(cost=1.0, build_cost=50.0), _fallback_event(cost=1.5, build_cost=50.0)]
    })
    assert rejected['economic_decision_output']['decision'] == 'rejected'
    assert rejected['upgrade_proposal_stub'] is None
