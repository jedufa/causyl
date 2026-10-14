from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from experiments_shared import (
    build_snapshot_drift_action_request,
    decide_phase2_chain_progress,
    dispatch_phase2_action_request,
    route_phase2_action_request,
    validate_phase2_context_bundle,
    validate_phase2_contracts,
)


def test_phase2_contracts_are_coherent_and_singular_verification_owner() -> None:
    result = validate_phase2_contracts()
    assert result["ok"] is True
    assert result["routing"]["verification_owner"] == "scheduler_verifier"


def test_patch_apply_request_routes_deterministically_to_pipeline_with_scheduler_verifier() -> None:
    result = route_phase2_action_request(
        {
            "task_id": "task_001",
            "action_family": "patch_apply",
            "execution_mode": "deterministic_then_codex",
            "context_bundle_id": "lab_delivery_safety_v1",
            "requested_by": "operator",
            "source_event": "api",
            "authority_scope": "repo_mutation",
        }
    )
    assert result["ok"] is True
    assert result["routing"]["executor"] == "pipeline"
    assert result["routing"]["verification_owner"] == "scheduler_verifier"
    assert result["routing"]["fallback_policy"]["pipeline_fail_to_codex"] is True


def test_snapshot_drift_builds_status_reconcile_request() -> None:
    request = build_snapshot_drift_action_request({"drift_id": "drift_01", "target_uid": "PIS-EXAMPLE"})
    assert request["action_family"] == "status_reconcile"
    assert request["entrypoint_id"] == "snapshot_drift_trigger"
    assert request["source_event"] == "snapshot_drift"


def test_context_budget_blocks_transcript_replay_and_budget_overflow() -> None:
    result = validate_phase2_context_bundle(
        {
            "context_bundle_id": "lab_core_v1",
            "include_sections": ["current_action_or_playbook", "current_objective"],
            "extra_refs": ["full transcript of last session"],
            "estimated_tokens": 25000,
        }
    )
    assert result["ok"] is False
    codes = {row["code"] for row in result["errors"]}
    assert "forbidden_context_ref" in codes
    assert "context_budget_exceeded" in codes


def test_chain_progress_enforces_escalation_checklist_and_economic_stop() -> None:
    blocked = decide_phase2_chain_progress(
        {
            "step_outcome": "failed_known",
            "proof_gaps": [],
            "deterministic_recovery_remaining": 0,
            "fallback_allowed": True,
            "pre_escalation_checklist": {
                "evidence_gathered": True,
                "failure_class_recorded": True,
            },
            "confidence_delta": 0.5,
            "stagnant_iterations": 0,
            "expected_gain": 1.0,
            "cost_per_iteration": 1.0,
            "retry_count": 0,
        }
    )
    assert blocked["decision"] == "block"
    assert blocked["checklist_complete"] is False

    stopped = decide_phase2_chain_progress(
        {
            "step_outcome": "failed_known",
            "proof_gaps": [],
            "deterministic_recovery_remaining": 0,
            "fallback_allowed": True,
            "pre_escalation_checklist": {
                "evidence_gathered": True,
                "failure_class_recorded": True,
                "deterministic_recovery_exhausted_or_inapplicable": True,
                "downstream_validation_contract_prepared": True,
            },
            "confidence_delta": 0.0,
            "stagnant_iterations": 2,
            "expected_gain": 0.0,
            "cost_per_iteration": 30.0,
            "retry_count": 2,
        }
    )
    assert stopped["decision"] == "economic_stop"
    assert stopped["economic_stop_reasons"]


def test_dispatch_cli_returns_normalized_routing_plan() -> None:
    payload = {
        "task_id": "task_cli_001",
        "action_family": "build_verify",
        "execution_mode": "deterministic_only",
        "context_bundle_id": "lab_core_v1",
        "requested_by": "operator",
        "source_event": "cli",
        "authority_scope": "read_only",
        "include_sections": ["current_action_or_playbook", "current_objective"],
        "estimated_tokens": 1200,
    }
    proc = subprocess.run(
        [sys.executable, "infra/bin/action_execute.py"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["routing"]["executor"] == "pipeline"
    assert data["routing"]["verification_owner"] == "scheduler_verifier"
