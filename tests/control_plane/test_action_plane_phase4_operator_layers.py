from __future__ import annotations

from experiments_shared import (
    assign_absorption_regression_guardrails_v1,
    build_action_plane_all_phases_report_v1,
    build_phase4_regression_report,
    cluster_fallback_patterns_v1,
    decide_absorption_economics_v1,
    execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1,
    generate_deterministic_upgrade_proposal_v1,
    synthesize_absorption_candidates_v1,
    validate_absorption_regression_guardrail_payload,
    validate_deterministic_upgrade_pipeline_payload,
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


def _blocked_patch_report() -> dict:
    return {
        'chain_id': 'patch_apply_chain_v1::1',
        'playbook_id': 'patch_apply_chain_v1',
        'action_family': 'patch_apply',
        'final_chain_outcome': 'blocked',
        'final_verifier_verdict': '',
        'terminal_reason': 'patch_contract_invalid',
        'step_records': [
            {
                'node_id': 'patch_ingest',
                'step_outcome': 'failed_known',
                'proof_gaps': ['missing_validation_commands'],
                'decision': 'block',
                'checklist_complete': True,
            }
        ],
        'failure_escalation_log': [],
    }


def test_upgrade_pipeline_and_guardrails_attach_to_approved_candidate() -> None:
    assert validate_phase4_operator_flow_registry()['ok'] is True
    clustered = cluster_fallback_patterns_v1({'fallback_events': [_fallback_event(cost=25.0), _fallback_event(cost=24.0)]})
    candidate = synthesize_absorption_candidates_v1({'clusters': clustered['clusters']})['candidates'][0]
    decision = decide_absorption_economics_v1({'candidate': candidate})
    proposal_output = generate_deterministic_upgrade_proposal_v1({'candidate': candidate, 'economic_decision': decision})
    assert validate_deterministic_upgrade_pipeline_payload(proposal_output)['ok'] is True
    assert proposal_output['proposal_created'] is True
    guardrails = assign_absorption_regression_guardrails_v1({'proposal': proposal_output['proposal']})
    assert validate_absorption_regression_guardrail_payload(guardrails)['ok'] is True
    assert guardrails['rollout_eligibility'] == 'eligible_for_guarded_rollout'


def test_phase4_flow_emits_guarded_proposal_when_approved() -> None:
    result = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1({'fallback_events': [_fallback_event(cost=25.0), _fallback_event(cost=24.0)]})
    assert result['ok'] is True
    assert result['economic_decision_output']['decision'] == 'approved'
    assert result['upgrade_pipeline_output']['proposal_created'] is True
    assert result['guardrail_output']['rollout_eligibility'] == 'eligible_for_guarded_rollout'


def test_phase4_regression_report_and_all_phases_report_hold() -> None:
    approved = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1({'fallback_events': [_fallback_event(cost=25.0), _fallback_event(cost=24.0)]})
    rejected = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1({'fallback_events': [_fallback_event(cost=1.0, build_cost=50.0), _fallback_event(cost=1.5, build_cost=50.0)]})
    regression = build_phase4_regression_report({'approved': approved, 'rejected': rejected})
    assert regression['ok'] is True
    assert regression['checks']['approved_cases_guarded'] is True
    assert regression['checks']['rejected_cases_no_proposal'] is True

    all_phases = build_action_plane_all_phases_report_v1({
        'fallback_events': [_fallback_event(cost=25.0), _fallback_event(cost=24.0)],
        'report': _blocked_patch_report(),
    })
    assert all_phases['ok'] is True
    assert all_phases['checks']['phase1_promotes_or_rejects_explicitly'] is True
    assert all_phases['checks']['phase2_contracts_valid'] is True
    assert all_phases['checks']['phase3_operator_flow_valid'] is True
    assert all_phases['checks']['phase4_flow_valid'] is True
    assert all_phases['checks']['phase4_guardrails_present_when_approved'] is True
