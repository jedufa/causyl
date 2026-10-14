from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import verification_workflow_launch_contract as contract


ARTIFACT_PATH = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-043438Z-K1M4__define-verification-workflow-launch-contract-and-block-selection-surface/"
    "artifacts/stress/launch_contract_summary.json"
)
META_PATH = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-043438Z-K1M4__define-verification-workflow-launch-contract-and-block-selection-surface/meta.json"
)
R5S8_CONTRACT_PATH = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050101Z-R5S8__define-verification-launch-request-schema-preflight-validation-and-durable-reque/"
    "artifacts/stress/launch_contract/launch_request_contract_v1.json"
)
R5S8_NEGATIVE_CASES_PATH = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050101Z-R5S8__define-verification-launch-request-schema-preflight-validation-and-durable-reque/"
    "artifacts/stress/launch_contract/launch_request_negative_cases_v1.json"
)
S6T9_CONTRACT_PATH = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050102Z-S6T9__define-selected-block-qualification-verdicts-and-bounded-summary-contract/"
    "artifacts/stress/launch_contract/selected_block_summary_contract_v1.json"
)
S6T9_NEGATIVE_CASES_PATH = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260414-050102Z-S6T9__define-selected-block-qualification-verdicts-and-bounded-summary-contract/"
    "artifacts/stress/launch_contract/selected_block_negative_cases_v1.json"
)


def _sample_block(*, verdict: str = "expand") -> dict:
    payload = {
        "block_ref": "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
        "block_label": "scheduler launch handoff",
        "qualification_verdict": verdict,
        "operator_goal": "stress one real scheduler-driven verification workflow slice",
        "risk_unit_summary": "scheduler launch plus the queue-claim seam is the smallest honest risk unit",
        "qualification_basis": [
            "launch truth is the operator-facing claim under attack",
            "the queue-claim seam can falsify that claim independently",
        ],
        "included_paths": [
            "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
            "runtime/state/scheduler_watchdog/",
        ],
        "adjacent_truth_seams": [
            {
                "seam_id": "scheduler_claim_state",
                "relationship_to_block": "queue claim and active-run materialization",
                "disposition": "included",
                "note": "Needed so launch truth does not ignore claimed-but-unmaterialized runs.",
            }
        ],
    }
    if verdict == "expand":
        payload["expansion_notes"] = [
            "Include the queue-claim seam because it changes launch truth without widening to the whole subsystem."
        ]
    if verdict == "reject":
        payload["rejection_reasons"] = [
            "The chosen block is too prompt-shaped and omits the queue-claim seam that owns launch truth."
        ]
    return payload


def test_contract_export_matches_bound_artifact() -> None:
    exported = contract.verification_workflow_launch_contract_v1()
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))

    assert exported.version == "verification_workflow_launch_contract_summary_v1"
    assert exported.uid == "PIS-20260414-043438Z-K1M4"
    assert exported.workflow_family == "verification_workflow_live_stress_v1"
    assert exported.artifact_rel == contract.ARTIFACT_REL
    assert artifact["schema_version"] == exported.version
    assert artifact["workflow_family"] == exported.workflow_family
    assert artifact["request_contract"]["schema_version"] == exported.launch_request_schema
    assert artifact["selected_block_summary_contract"]["schema_version"] == exported.selected_block_summary_schema


def test_meta_points_to_launch_contract_artifact() -> None:
    meta = json.loads(META_PATH.read_text(encoding="utf-8"))

    assert meta["required_evidence"] == ["artifacts/stress/launch_contract_summary.json"]
    assert meta["evidence"]["artifact"] == "artifacts/stress/launch_contract_summary.json"
    assert meta["evidence"]["kind"] == "contract_summary"


def test_child_contract_artifacts_match_runtime_exports() -> None:
    assert json.loads(R5S8_CONTRACT_PATH.read_text(encoding="utf-8")) == contract.launch_request_contract_artifact_v1()
    assert json.loads(R5S8_NEGATIVE_CASES_PATH.read_text(encoding="utf-8")) == contract.launch_request_negative_cases_v1()
    assert json.loads(S6T9_CONTRACT_PATH.read_text(encoding="utf-8")) == contract.selected_block_summary_contract_artifact_v1()
    assert json.loads(S6T9_NEGATIVE_CASES_PATH.read_text(encoding="utf-8")) == contract.selected_block_negative_cases_v1()


def test_build_selected_block_summary_accepts_bounded_expand_case() -> None:
    summary = contract.build_selected_block_summary(_sample_block(verdict="expand"))

    assert summary["schema_version"] == "verification_workflow_selected_block_summary_v1"
    assert summary["qualification_verdict"] == "expand"
    assert summary["launch_readiness"] is True
    assert "does not prove runtime behavior" in summary["bounded_operator_summary"]
    assert summary["summary_fingerprint"] != ""


def test_build_selected_block_summary_reject_requires_reasons() -> None:
    with pytest.raises(contract.VerificationWorkflowLaunchError) as excinfo:
        contract.build_selected_block_summary(
            {
                **_sample_block(verdict="reject"),
                "rejection_reasons": [],
            }
        )

    assert excinfo.value.code == "selected_block_summary_reject_requires_reasons"


def test_normalize_launch_request_fails_closed_for_rejected_block() -> None:
    with pytest.raises(contract.VerificationWorkflowLaunchError) as excinfo:
        contract.normalize_launch_request(
            {
                "request_id": "verification_workflow_launch_rejected",
                "verification_goal": "launch the workflow anyway",
                "requested_output_mode": "child_pis_family",
                "max_iterations": 4,
                "selected_block_summary": _sample_block(verdict="reject"),
            },
            created_utc="2026-04-14T05:20:00Z",
        )

    assert excinfo.value.code == "launch_request_block_not_launchable"


def test_normalize_launch_request_rejects_invalid_mode() -> None:
    with pytest.raises(contract.VerificationWorkflowLaunchError) as excinfo:
        contract.normalize_launch_request(
            {
                "request_id": "verification_workflow_launch_bad_mode",
                "verification_goal": "launch the workflow slice",
                "requested_output_mode": "magic",
                "selected_block_summary": _sample_block(),
            },
            created_utc="2026-04-14T05:20:00Z",
        )

    assert excinfo.value.code == "launch_request_invalid_output_mode"


def test_normalize_launch_request_requires_selected_block_summary() -> None:
    with pytest.raises(contract.VerificationWorkflowLaunchError) as excinfo:
        contract.normalize_launch_request(
            {
                "request_id": "verification_workflow_launch_missing_block",
                "verification_goal": "launch the workflow slice",
                "requested_output_mode": "child_pis_family",
            },
            created_utc="2026-04-14T05:20:00Z",
        )

    assert excinfo.value.code == "launch_request_missing_selected_block_summary"


def test_normalize_launch_request_preserves_raw_and_normalized_truth() -> None:
    request = contract.normalize_launch_request(
        {
            "request_id": "verification_workflow_launch_demo",
            "verification_goal": "launch a bounded verification workflow family",
            "requested_output_mode": "child_pis_family",
            "max_iterations": 4,
            "operator_note": "Use the bounded family path first.",
            "selected_block_summary": _sample_block(),
        },
        created_utc="2026-04-14T05:20:00Z",
    )

    assert request["schema_version"] == "verification_workflow_launch_request_v1"
    assert request["durable_record_contract"]["must_be_written_before_scheduler_submission"] is True
    assert request["durable_record_contract"]["retry_reuses_existing_record_when_request_fingerprint_matches"] is True
    assert request["raw_operator_intent"]["verification_goal"] == "launch a bounded verification workflow family"
    assert request["normalized_request"]["scheduler_job_kind"] == "build_verify_loop"
    assert request["normalized_request"]["selected_block_verdict"] == "expand"
    assert request["request_fingerprint"] != ""


def test_artifact_validation_passes() -> None:
    assert contract.validate_verification_workflow_launch_contract_v1() == ()


def test_artifact_validation_detects_contract_drift(tmp_path: Path) -> None:
    artifact_path = tmp_path / contract.ARTIFACT_REL
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact = json.loads(ARTIFACT_PATH.read_text(encoding="utf-8"))
    artifact["selected_block_summary_contract"]["bounded_wording_rules"] = ["drifted"]
    artifact_path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")

    issues = contract.validate_verification_workflow_launch_contract_v1(repo_root=tmp_path)

    assert "launch contract artifact drifted from the canonical runtime helper output" in issues
