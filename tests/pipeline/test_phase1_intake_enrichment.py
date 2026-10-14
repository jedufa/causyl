from __future__ import annotations

from pathlib import Path

import pytest

from infra.pipeline.core import load_pipeline_config
from infra.pipeline.knowledge_assets import _load_minimal_yaml, describe_knowledge_assets, load_knowledge_assets
from infra.pipeline.stages.job_intake import build_job_contract
from infra.pipeline.stages.task_classifier import classify


def test_scheduler_prompt_expands_failure_taxonomy_invariants_and_requirements(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {"title": "Harden the scheduler control path around retries and restarts"},
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "scheduler_safety" in intake["task_families"]
    assert intake["failure_taxonomy"]["system_failure_modes"]
    assert intake["failure_taxonomy"]["recovery_failure_modes"]
    assert intake["invariants"]
    assert intake["derived_requirements"]["observability_requirements"]
    assert intake["confidence"]["computed"] is True
    assert intake["confidence"]["source"] == "deterministic_task_family_activation"


def test_operator_control_prompt_emits_operator_safety_requirements(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {"title": "Make manual pause and resume safer"},
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert intake["task_families"] == ["operator_control"]
    assert any("pause" in entry or "resume" in entry for entry in intake["failure_taxonomy"]["operator_failure_modes"])
    assert any("validate pause, resume, cancel, and restart requests" in entry for entry in intake["derived_requirements"]["safety_requirements"])
    assert "generic_fallback_used" not in intake["confidence"]["notes"]
    assert intake["confidence"]["source"] == "deterministic_task_family_activation"


def test_intake_infers_allowed_paths_from_explicit_single_file_edit_prompt(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "infra/bin").mkdir(parents=True)
    (repo_root / "infra/bin/jeff").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

    result = build_job_contract(
        {
            "title": "Edit infra/bin/jeff so its --help text mentions compact progress",
            "description": "Keep the help text concise.",
        },
        repo_root=str(repo_root),
    )

    job_contract = result["job_contract"]
    assert job_contract["allowed_paths"] == ["infra/bin/jeff"]
    assert "infra/bin/jeff" in job_contract["issue_context"]["changed_files_hint"]


def test_intake_does_not_overconstrain_single_file_prompt_when_tests_are_mentioned(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "app.py").write_text("def normalize_text(s):\n    return s.strip()\n", encoding="utf-8")
    (repo_root / "tests").mkdir()
    (repo_root / "tests/test_app.py").write_text("def test_placeholder():\n    assert True\n", encoding="utf-8")

    result = build_job_contract(
        {
            "title": "Change app.py so None returns an empty string, and update tests if needed.",
        },
        repo_root=str(repo_root),
    )

    job_contract = result["job_contract"]
    assert job_contract["allowed_paths"] == []
    assert "app.py" in job_contract["issue_context"]["changed_files_hint"]


def test_weak_prompt_fallback_is_valid_and_low_confidence(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {"title": "Make it safer"},
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert intake["task_families"] == ["generic"]
    assert intake["failure_taxonomy"]["operator_failure_modes"]
    assert intake["confidence"]["band"] == "low"
    assert "generic_fallback_used" in intake["confidence"]["notes"]
    assert "weak_prompt_signal" in intake["confidence"]["notes"]


def test_task_families_manifest_entry_and_loader_are_available() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = load_pipeline_config(repo_root=repo_root)
    manifest = config["_derived"]["knowledge_assets"]
    loaded = load_knowledge_assets(repo_root, config, ["task_families"])

    assert "task_families" in manifest["files"]
    assert manifest["exists"]["task_families"] is True
    assert "operator_control" in loaded["task_families"]["families"]


def test_required_phase1_knowledge_assets_load_non_empty_structures() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = load_pipeline_config(repo_root=repo_root)
    diagnostics = describe_knowledge_assets(repo_root, config, ["task_families", "failure_taxonomy", "invariant_templates", "derived_requirements"])
    loaded = load_knowledge_assets(repo_root, config, ["task_families", "failure_taxonomy", "invariant_templates", "derived_requirements"])

    assert diagnostics["all_requested_available"] is True
    assert diagnostics["missing_required"] == []
    assert "scheduler_safety" in loaded["task_families"]["families"]
    assert loaded["failure_taxonomy"]["families"]["scheduler_safety"]["operator_failure_modes"]
    assert loaded["invariant_templates"]["families"]["scheduler_safety"]
    assert loaded["derived_requirements"]["families"]["scheduler_safety"]["safety_requirements"]


def test_phase1_missing_required_knowledge_assets_fail_loudly(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "infra/pipeline/knowledge").mkdir(parents=True)

    with pytest.raises(Exception) as exc_info:
        build_job_contract(
            {
                "title": "make my scheduler more robust against failures, especially human failures",
                "repo_root": str(repo_root),
            },
            repo_root=str(repo_root),
            config={
                "deterministic_v2": {"enabled": True},
                "knowledge_assets": {"base_dir": "infra/pipeline/knowledge"},
            },
        )

    assert "Required Phase 1 knowledge assets could not be loaded" in str(exc_info.value)


def test_task_classifier_prefers_stage1_task_families_from_intake_assessment() -> None:
    classified = classify(
        {
            "title": "Make manual pause and resume safer",
            "description": "",
            "objective": "",
            "task_type_hint": "unknown",
            "constraints": [],
            "issue_context": {},
        },
        intake_assessment={"task_families": ["operator_control"]},
    )

    assert classified["task_profile"]["task_families"] == ["operator_control"]


def test_minimal_yaml_parser_coerces_float_scalars() -> None:
    data = _load_minimal_yaml("limits:\n  score_margin_keep_secondary: 0.15\n")
    assert data["limits"]["score_margin_keep_secondary"] == pytest.approx(0.15)


def test_lab_assistant_memory_prompt_emits_truth_boundary_requirements(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Research Memory, Knowledge, And Cross-Project Learning For Lab Assistant v2+",
            "description": (
                "Define a parent research-memory contract that preserves provenance, freshness, correction history, "
                "transcript and ledger evidence, truth ownership, execution reconciliation, and cross-project reuse boundaries."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "lab_assistant_memory" in intake["task_families"]
    assert any("provenance" in entry or "freshness" in entry for entry in intake["failure_taxonomy"]["operator_failure_modes"])
    assert any("truth-boundary" in entry or "truth boundary" in entry for entry in intake["derived_requirements"]["safety_requirements"])
    assert any("correction" in entry and "lineage" in entry for entry in intake["derived_requirements"]["observability_requirements"] + intake["derived_requirements"]["recovery_guarantees"])
    assert intake["confidence"]["source"] == "deterministic_task_family_activation"


def test_truth_contract_prompt_activates_assistant_identity_family(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Persistent Assistant Identity and Source-of-Truth Contract",
            "description": (
                "Define a durable assistant identity and runtime-state contract with append-only audit events, "
                "source-of-truth precedence, write arbitration, recovery markers, blocked state, and versioned schema load."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "assistant_truth_and_identity" in intake["task_families"]
    assert any("assistant" in entry or "runtime" in entry for entry in intake["failure_taxonomy"]["operator_failure_modes"])
    assert any("assistant_id" in entry or "assistant identity" in entry for entry in intake["invariants"])
    assert any("append" in entry or "audit" in entry for entry in intake["derived_requirements"]["observability_requirements"])


def test_orchestration_checkpointing_prompt_beats_generic_runtime_triggers(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Fresh-turn bootstrap, context reload, and interrupt recovery",
            "description": (
                "Define safe resume, degraded bootstrap behavior, idempotent re-entry, and durable checkpointing "
                "for restart-time context reconstruction without pretending process-memory continuity."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "orchestration_checkpointing" in intake["task_families"]
    assert "scheduler_safety" not in intake["task_families"]


def test_multi_assistant_prompt_activates_delegation_and_orchestration_families(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Multi-assistant coordination and typed assistant handoff",
            "description": (
                "Define assistant-to-assistant delegation, role specialization, ownership transfer, "
                "mailbox claim lease handling, and supervisory orchestration."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "lab_assistant_delegation" in intake["task_families"]
    assert "agent_orchestration_runtime" in intake["task_families"]


def test_authority_prompt_activates_truth_and_identity_family(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Authority tiers and high-impact approval provenance",
            "description": (
                "Bind high-impact approval to durable actor identity, tenancy boundaries, override denial, "
                "and explicit approval records instead of chat phrasing."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "assistant_truth_and_identity" in intake["task_families"]


def test_detached_job_reconciliation_prompt_activates_orchestration_checkpointing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Detached job timeout and boot reconciliation",
            "description": (
                "Define stale-run handling, process restart recovery, late result reconciliation, "
                "and restart-honest continuation for already-launched work."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "orchestration_checkpointing" in intake["task_families"]


def test_long_horizon_checkpoint_prompt_prefers_orchestration_over_generic_workflow(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Milestone checkpoint, review gate, and replanning contract",
            "description": (
                "Define continuation decisions, restart-safe checkpoints, and rationale-preserving replanning "
                "for long-running execution loops."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert "orchestration_checkpointing" in intake["task_families"]


def test_operational_proof_prompt_activates_audit_or_review_family(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Proof freshness and governance integrity",
            "description": (
                "Require evidence-backed completion claims, proof-gap surfacing, claim gating, provenance, "
                "and restart-safe receipts for operator-facing operations."
            ),
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert any(
        family in intake["task_families"]
        for family in ("audit_evidence", "operator_review_and_repair")
    )
