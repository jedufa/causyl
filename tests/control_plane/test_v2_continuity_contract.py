from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import v2_continuity


def test_v2_continuity_contract_v1_exports_expected_metadata() -> None:
    contract = v2_continuity.v2_continuity_contract_v1()
    assert contract.version == "v2_continuity_contract_v1"
    assert contract.uid == "PIS-20260409-195203Z-D1Y4"
    assert contract.canonical_artifact_rel.as_posix() == "governance/v2_continuity_contract_v1.json"
    assert contract.canonical_doc_rel.as_posix() == "docs/contracts/contract_v2_continuity_engine_recovery_v1.md"
    assert contract.receipt_schema_version == "v2_continuity_receipt_v1"
    assert contract.allowed_states == (
        "interrupted",
        "resume_ready",
        "rerun_ready",
        "repairing",
        "blocked",
        "fail_closed",
    )


def test_validate_v2_continuity_contract_v1_returns_no_issues() -> None:
    assert v2_continuity.validate_v2_continuity_contract_v1() == ()


def test_decide_continuity_recovery_returns_resume_when_checkpoint_is_replay_safe() -> None:
    decision = v2_continuity.decide_continuity_recovery(
        {
            "subject_kind": "job",
            "subject_id": "job.demo",
            "workflow_kind": "intent_family_v2",
            "runtime_state": "running",
            "interrupted": True,
            "replay_safe": True,
            "checkpoint_id": "cp-001",
        }
    )
    assert decision.continuity_state == "resume_ready"
    assert decision.recovery_outcome == "resume"
    assert decision.checkpoint_id == "cp-001"


def test_decide_continuity_recovery_returns_rerun_when_explicit_boundary_exists() -> None:
    decision = v2_continuity.decide_continuity_recovery(
        {
            "subject_kind": "family",
            "subject_id": "family.demo",
            "workflow_kind": "intent_family_v2",
            "runtime_state": "interrupted",
            "interrupted": True,
            "rerun_allowed": True,
            "revalidation_required": True,
        }
    )
    assert decision.continuity_state == "rerun_ready"
    assert decision.recovery_outcome == "rerun"
    assert decision.revalidation_required is True


def test_decide_continuity_recovery_returns_repair_then_rerun_when_bounded_repair_is_allowed() -> None:
    decision = v2_continuity.decide_continuity_recovery(
        {
            "subject_kind": "job",
            "subject_id": "job.needs_repair",
            "workflow_kind": "intent_family_v2",
            "runtime_state": "failed_verification",
            "repair_needed": True,
            "repair_allowed": True,
            "revalidation_required": True,
        }
    )
    assert decision.continuity_state == "repairing"
    assert decision.recovery_outcome == "repair_then_rerun"
    assert decision.post_repair_action == "focused_revalidation"


def test_decide_continuity_recovery_returns_operator_block_for_explicit_block() -> None:
    decision = v2_continuity.decide_continuity_recovery(
        {
            "subject_kind": "job",
            "subject_id": "job.blocked",
            "workflow_kind": "intent_family_v2",
            "runtime_state": "blocked",
            "operator_block_reason": "awaiting_operator_confirmation",
        }
    )
    assert decision.continuity_state == "blocked"
    assert decision.recovery_outcome == "operator_block"


def test_decide_continuity_recovery_fails_closed_when_repair_is_not_allowed() -> None:
    decision = v2_continuity.decide_continuity_recovery(
        {
            "subject_kind": "job",
            "subject_id": "job.fail_closed",
            "workflow_kind": "intent_family_v2",
            "runtime_state": "failed_verification",
            "repair_needed": True,
            "repair_allowed": False,
        }
    )
    assert decision.continuity_state == "fail_closed"
    assert decision.recovery_outcome == "fail_closed"


def test_build_and_reload_continuity_receipt_reconstructs_same_classification(tmp_path: Path) -> None:
    record = {
        "subject_kind": "job",
        "subject_id": "job.reload",
        "workflow_kind": "intent_family_v2",
        "runtime_state": "interrupted",
        "interrupted": True,
        "rerun_allowed": True,
    }
    decision = v2_continuity.decide_continuity_recovery(record)
    receipt = v2_continuity.build_continuity_receipt(
        decision,
        runtime_record=record,
        decision_id="job.reload.rerun",
        decision_utc="2026-04-09T20:50:00Z",
        evidence_refs=["runtime/state/demo_receipt.json"],
    )
    path = v2_continuity.write_continuity_receipt(receipt, repo_root=tmp_path)
    loaded = v2_continuity.load_continuity_receipt(path)
    projection = v2_continuity.classify_continuity_from_durable_records(record, latest_receipt=loaded)
    assert projection["continuity_state"] == "rerun_ready"
    assert projection["recovery_outcome"] == "rerun"
    assert projection["decision_source"] == "durable_receipt"
    assert v2_continuity.latest_continuity_receipt("job", "job.reload", repo_root=tmp_path)["decision_id"] == "job.reload.rerun"


def test_validate_continuity_receipt_rejects_repair_that_promotes_closure() -> None:
    record = {
        "subject_kind": "job",
        "subject_id": "job.bad_receipt",
        "workflow_kind": "intent_family_v2",
        "runtime_state": "failed_verification",
        "repair_needed": True,
        "repair_allowed": True,
    }
    decision = v2_continuity.decide_continuity_recovery(record)
    receipt = v2_continuity.build_continuity_receipt(decision, runtime_record=record)
    receipt["promoted_terminal_state"] = "succeeded"
    with pytest.raises(ValueError, match="may not promote terminal or closure state directly"):
        v2_continuity.validate_continuity_receipt(receipt)


def test_validate_continuity_receipt_rejects_repair_without_required_follow_up_action() -> None:
    record = {
        "subject_kind": "job",
        "subject_id": "job.bad_repair_follow_up",
        "workflow_kind": "intent_family_v2",
        "runtime_state": "failed_verification",
        "repair_needed": True,
        "repair_allowed": True,
    }
    decision = v2_continuity.decide_continuity_recovery(record)
    receipt = v2_continuity.build_continuity_receipt(decision, runtime_record=record)
    receipt["post_repair_action"] = "complete"
    receipt["receipt_fingerprint"] = v2_continuity.continuity_receipt_fingerprint(
        {k: v for k, v in receipt.items() if k != "receipt_fingerprint"}
    )
    with pytest.raises(ValueError, match="must require rerun or focused_revalidation"):
        v2_continuity.validate_continuity_receipt(receipt)


def test_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/v2_continuity_contract_v1.json").read_text(encoding="utf-8"))
    assert tuple(payload["allowed_states"]) == v2_continuity.ALLOWED_STATES
    assert tuple(payload["allowed_outcomes"]) == v2_continuity.ALLOWED_OUTCOMES
    assert tuple(payload["decision_precedence"]) == v2_continuity.DECISION_PRECEDENCE
    assert payload["receipt_policy"]["schema_version"] == v2_continuity.RECEIPT_SCHEMA_VERSION
