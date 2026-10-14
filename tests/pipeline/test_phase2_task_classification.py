from __future__ import annotations

from pathlib import Path

from infra.pipeline.core import load_pipeline_config
from infra.pipeline.knowledge_assets import load_knowledge_assets
from infra.pipeline.stages.job_intake import build_job_contract
from infra.pipeline.stages.task_classifier import classify


def _job_contract(title: str) -> dict:
    return {
        "title": title,
        "description": "",
        "objective": "",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }


def test_operator_control_classification_uses_stage1_families_and_biases_operator_validation(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    intake = build_job_contract({"title": "Make manual pause and resume safer"}, repo_root=str(repo_root))["intake_assessment"]
    result = classify(_job_contract("Make manual pause and resume safer"), intake_assessment=intake, repo_root=str(repo_root))

    task_profile = result["task_profile"]
    assert task_profile["task_families"] == ["operator_control"]
    assert "operator_interfaces" in task_profile["retrieval_bias"]["include"]
    assert "validation" in task_profile["retrieval_bias"]["include"]
    assert "state_management" in task_profile["retrieval_bias"]["include"]
    assert "operator_safety" in task_profile["cross_cutting_flags"]
    assert "idempotency" in task_profile["cross_cutting_flags"]
    assert task_profile["confidence"]["source"] == "deterministic_task_profile_rules"
    assert task_profile["classifier_confidence"] >= 0.6


def test_scheduler_hardening_classification_elevates_risk_and_biases_recovery_state(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    title = "Harden the scheduler against retries and stale restarts"
    intake = build_job_contract({"title": title}, repo_root=str(repo_root))["intake_assessment"]
    result = classify(_job_contract(title), intake_assessment=intake, repo_root=str(repo_root))

    task_profile = result["task_profile"]
    assert "scheduler_safety" in task_profile["task_families"]
    assert "scheduler_hardening" in task_profile["task_families"]
    assert task_profile["risk_level"] == "high"
    assert "scheduler_core" in task_profile["retrieval_bias"]["include"]
    assert "recovery" in task_profile["retrieval_bias"]["include"]
    assert "state_management" in task_profile["retrieval_bias"]["include"]
    assert "validation" in task_profile["retrieval_bias"]["include"]
    assert "recovery_integrity" in task_profile["cross_cutting_flags"]
    assert "state_consistency" in task_profile["cross_cutting_flags"]


def test_design_mode_classification_sets_design_task_mode(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    title = "Design me a scheduler for delayed and retried jobs"
    intake = build_job_contract({"title": title}, repo_root=str(repo_root))["intake_assessment"]
    result = classify(_job_contract(title), intake_assessment=intake, repo_root=str(repo_root))

    task_profile = result["task_profile"]
    assert task_profile["task_mode"] == "design"
    assert task_profile["primary_task_type"] == "design"
    assert "architecture_surfaces" in task_profile["retrieval_bias"]["include"]
    assert not any(entry == "docs" for entry in task_profile["retrieval_bias"]["exclude"])
    assert any("design_intent_detected" in entry or "design_terms=" in entry for entry in task_profile["rationale"])


def test_weak_prompt_classification_is_conservative(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    title = "Make it safer"
    intake = build_job_contract({"title": title}, repo_root=str(repo_root))["intake_assessment"]
    result = classify(_job_contract(title), intake_assessment=intake, repo_root=str(repo_root))

    task_profile = result["task_profile"]
    assert task_profile["task_families"] == ["generic"]
    assert task_profile["primary_task_type"] == "code_modification"
    assert task_profile["classifier_confidence"] <= 0.4
    assert task_profile["ambiguity_score"] >= 0.6
    assert task_profile["retrieval_bias"]["include"] == ["validation", "observability"]
    assert task_profile["cross_cutting_flags"] == ["observability"]


def test_task_profile_rules_manifest_entry_and_loader_are_available() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    config = load_pipeline_config(repo_root=repo_root)
    manifest = config["_derived"]["knowledge_assets"]
    loaded = load_knowledge_assets(repo_root, config, ["task_profile_rules", "cross_cutting_flags"])

    assert "task_profile_rules" in manifest["files"]
    assert manifest["exists"]["task_profile_rules"] is True
    assert loaded["task_profile_rules"]["version"] == 1
    assert "scheduler_safety" in loaded["task_profile_rules"]["families"]
    assert any(entry["key"] == "operator_safety" for entry in loaded["cross_cutting_flags"]["flags"])


def test_classifier_falls_back_to_raw_text_family_detection_when_intake_missing() -> None:
    result = classify(_job_contract("Make manual pause and resume safer"), repo_root=".")

    task_profile = result["task_profile"]
    assert "operator_control" in task_profile["task_families"]
    assert any(entry == "family_source=raw_text_fallback" for entry in task_profile["rationale"])
    assert 0.3 <= task_profile["classifier_confidence"] < 0.65
    assert task_profile["confidence"]["source"] == "deterministic_task_profile_rules"


def test_scheduler_prompt_restores_scheduler_bias_for_human_failure_language(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    title = "make my scheduler more robust against failures, especially human failures"
    intake = build_job_contract({"title": title}, repo_root=str(repo_root))["intake_assessment"]
    result = classify(_job_contract(title), intake_assessment=intake, repo_root=str(repo_root))

    assert "scheduler_safety" in intake["task_families"]
    assert intake["failure_taxonomy"]["operator_failure_modes"]
    assert intake["invariants"]
    assert intake["derived_requirements"]["safety_requirements"]

    task_profile = result["task_profile"]
    assert "scheduler_safety" in task_profile["task_families"]
    assert task_profile["risk_level"] == "high"
    assert task_profile["expected_edit_scope"] in {"few_files", "multi_module"}
    assert "scheduler_core" in task_profile["retrieval_bias"]["include"]
    assert "state_management" in task_profile["retrieval_bias"]["include"]
    assert "operator_safety" in task_profile["cross_cutting_flags"]
    assert task_profile["knowledge_activation"]["all_requested_available"] is True


def test_optional_test_wording_does_not_override_explicit_non_test_file_target(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Fix the bug in app.py so empty input returns an empty string, and update tests if needed.",
        "description": "",
        "objective": "",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))

    task_profile = result["task_profile"]
    assert task_profile["primary_task_type"] != "test_fix"
    assert task_profile["task_archetype"] == "implementation_change"
    assert any("test_terms_demoted_by_explicit_non_test_file_hint" in entry for entry in task_profile["rationale"])


def test_scheduler_classification_uses_valid_assets_even_if_stage1_payload_is_partially_degraded() -> None:
    title = "make my scheduler more robust against failures, especially human failures"
    result = classify(
        _job_contract(title),
        intake_assessment={
            "task_families": [],
            "failure_taxonomy": {},
            "invariants": [],
            "derived_requirements": {},
        },
        repo_root=".",
    )

    task_profile = result["task_profile"]
    assert "scheduler_safety" in task_profile["task_families"]
    assert "scheduler_hardening" in task_profile["task_families"]
    assert task_profile["primary_task_type"] == "system_hardening"
    assert task_profile["risk_level"] == "high"
    assert "scheduler_core" in task_profile["retrieval_bias"]["include"]
    assert "operator_safety" in task_profile["cross_cutting_flags"]


def test_lab_assistant_memory_classification_biases_truth_reconciliation_and_docs_reference(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Research Memory, Knowledge, And Cross-Project Learning For Lab Assistant v2+",
        "description": (
            "Define the parent workstream for research memory with provenance, freshness, correction history, task register, "
            "truth ownership, execution reconciliation, transcript evidence, ledger evidence, and writeback boundaries."
        ),
        "objective": "Define the user-visible contract and child boundaries for cross-project memory retrieval and evidence ranking.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(_job_contract(prompt["title"]) | {"description": prompt["description"], "objective": prompt["objective"]}, intake_assessment=intake, repo_root=str(repo_root))

    task_profile = result["task_profile"]
    assert "lab_assistant_memory" in task_profile["task_families"]
    assert task_profile["task_mode"] == "design"
    assert task_profile["primary_task_type"] == "design"
    assert "knowledge_retrieval" in task_profile["retrieval_bias"]["include"]
    assert "truth_ownership" in task_profile["retrieval_bias"]["include"]
    assert "execution_reconciliation" in task_profile["retrieval_bias"]["include"]
    assert "task_register" in task_profile["retrieval_bias"]["include"]
    assert "turn_transcripts" in task_profile["retrieval_bias"]["include"]
    assert "ledger_evidence" in task_profile["retrieval_bias"]["include"]
    assert "writeback_projection" in task_profile["retrieval_bias"]["include"]
    assert "docs" not in task_profile["retrieval_bias"]["exclude"]
    assert "reports" not in task_profile["retrieval_bias"]["exclude"]
    assert "auditability" in task_profile["cross_cutting_flags"]
    assert "mutation_boundary" in task_profile["cross_cutting_flags"]
    assert "reconciliation_correctness" in task_profile["cross_cutting_flags"]


def test_truth_contract_classification_prefers_contract_lane_and_validation_surfaces(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Persistent Assistant Identity and Source-of-Truth Contract",
        "description": (
            "Define and enforce a durable assistant identity and runtime-state contract that uses versioned schema load, "
            "append-only assistant audit events, write arbitration, recovery markers, blocked state, and explicit ledger versus runtime versus chat precedence."
        ),
        "objective": "Define durable assistant identity reload, source-of-truth precedence, and deterministic write arbitration.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))

    task_profile = result["task_profile"]
    assert "assistant_truth_and_identity" in task_profile["task_families"]
    assert task_profile["primary_task_type"] == "truth_contract"
    assert task_profile["task_mode"] == "targeted_change"
    assert "assistant_runtime_state" in task_profile["retrieval_bias"]["include"]
    assert "truth_registry" in task_profile["retrieval_bias"]["include"]
    assert "tests" in task_profile["retrieval_bias"]["include"]
    assert "validation" in task_profile["retrieval_bias"]["include"]
    assert "docs" not in task_profile["retrieval_bias"]["exclude"]



def test_orchestration_checkpointing_routing_prefers_specific_family(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Crash Safe Idempotent Orchestration Turn Checkpointing",
        "description": "ensure idempotent checkpointing and prevent duplicate execution after restart",
        "objective": "persist turn execution checkpointing and prevent duplicate replay after restart",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))
    task_profile = result["task_profile"]

    assert "orchestration_checkpointing" in task_profile["task_families"]
    assert task_profile["primary_task_type"] == "orchestration_checkpointing"
    assert task_profile["task_mode"] == "targeted_change"
    assert "tests" in task_profile["retrieval_bias"]["include"]
    assert "validation" in task_profile["retrieval_bias"]["include"]



def test_ui_truth_projection_routing_prefers_projection_family(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "UI Projection, Staleness, Degraded, and Unreachable State Contract",
        "description": "ensure UI shows degraded vs unreachable states correctly and stale cached truth is marked honestly",
        "objective": "fix UI snapshot staleness and honest degraded or unreachable status projection",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))
    task_profile = result["task_profile"]

    assert "ui_truth_projection" in task_profile["task_families"]
    assert task_profile["primary_task_type"] == "ui_truth_projection"
    assert task_profile["task_mode"] == "targeted_change"
    assert "operator_visible_state" in task_profile["retrieval_bias"]["include"]
    assert "tests" in task_profile["retrieval_bias"]["include"]


def test_lab_assistant_bootstrap_routes_to_orchestration_checkpointing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Fresh-Turn Bootstrap and Context Reload over Oneshot Runtime",
        "description": "lab assistant must rebuild context correctly during bootstrap, reload after restart, and reattach safely on cold-start takeover",
        "objective": "fix lab assistant bootstrap reconstruction and context reload after restart",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))
    task_profile = result["task_profile"]

    assert task_profile["primary_task_type"] == "orchestration_checkpointing"
    assert "orchestration_checkpointing" in task_profile["task_families"]
    assert any(
        token in entry
        for entry in task_profile["rationale"]
        for token in ("lab_assistant_bootstrap_route", "prominent_orchestration_concepts")
    )


def test_lab_assistant_authority_routes_to_truth_contract(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Multi-Operator Authority, Tenancy, and High-Impact Approval Contract",
        "description": "enforce lab assistant task register authority, delegation policy, truth ownership, and high-impact approval boundaries",
        "objective": "define authoritative task register, delegation, and approval truth for multi-operator tenancy",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))
    task_profile = result["task_profile"]

    assert task_profile["primary_task_type"] == "truth_contract"
    assert "assistant_truth_and_identity" in task_profile["task_families"]
    assert any(
        token in entry
        for entry in task_profile["rationale"]
        for token in ("lab_assistant_truth_route", "prominent_truth_concepts")
    )



def test_retrieval_ranking_contract_routing_prefers_pipeline_ranking_family(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Cross-Project Retrieval Ranking, Freshness, and Evidence-First Answer Assembly Contract",
        "description": "improve retrieval ranking with freshness and evidence prioritization and explicit missing evidence handling",
        "objective": "rank direct evidence above stale memory and preserve applicability boundaries in answer assembly",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))
    task_profile = result["task_profile"]

    assert "retrieval_ranking_contract" in task_profile["task_families"]
    assert task_profile["primary_task_type"] == "retrieval_ranking_contract"
    assert task_profile["task_mode"] == "targeted_change"
    assert "intake_and_profile" in task_profile["retrieval_bias"]["include"]
    assert "retrieval_runtime" in task_profile["retrieval_bias"]["include"]
    assert "tests" in task_profile["retrieval_bias"]["include"]



def test_broad_lab_memory_parent_does_not_get_hijacked_by_retrieval_ranking_family(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Research Memory, Knowledge, And Cross-Project Learning For Lab Assistant v2+",
        "description": (
            "Define the parent workstream that lets the laboratory remember, retrieve, correct, and reuse prior work across projects "
            "without letting summaries outrank evidence, stale conclusions steer new work, or cross-project memory turn into a second planning authority. "
            "Memory-backed answers carry confidence, provenance, freshness, and correction semantics. Retrieval can span reports, artifacts, transcripts, ledger records, and prior workcell outcomes while keeping canonical source records inspectable."
        ),
        "objective": "Define the user-visible contract and child boundaries for cross-project memory retrieval and evidence ranking.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    result = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))
    task_profile = result["task_profile"]

    assert "lab_assistant_memory" in task_profile["task_families"]
    assert task_profile["primary_task_type"] != "retrieval_ranking_contract"


def test_retrieval_ranking_contract_compacts_broad_memory_families(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    raw_request = {
        "repo_root": str(repo_root),
        "title": "Cross-Project Retrieval Ranking, Freshness, and Evidence-First Answer Assembly Contract",
        "description": "define deterministic ranking across direct evidence, memory records, and current-context facts with missing evidence surfaced explicitly",
        "objective": "rank direct evidence above stale memory and preserve applicability boundaries in answer assembly",
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
    }

    intake = build_job_contract(raw_request, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(raw_request, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]

    assert task_profile["primary_task_type"] == "retrieval_ranking_contract"
    assert task_profile["task_families"] == ["retrieval_ranking_contract"]
    assert "task_register" not in task_profile["retrieval_bias"]["include"]
    assert "execution_reconciliation" not in task_profile["retrieval_bias"]["include"]


def test_scheduler_control_prompt_prefers_scheduler_family_over_generic_recovery_mix(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Scheduler Runtime Policy Pause And Resume Safety",
        "description": "tighten scheduler runtime policy, pause, resume, watchdog, and healer safety without widening to generic recovery hubs",
        "objective": "make scheduler control-plane actions and watchdog recovery safer",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]

    assert task_profile["task_families"][0] == "scheduler_safety"
    assert "scheduler_or_control_plane" in task_profile["task_families"]
    assert "operator_control" in task_profile["task_families"]
    assert "reconciliation_recovery" not in task_profile["task_families"]
    assert "heartbeat_liveness" not in task_profile["task_families"]
    assert "scheduler_core" in task_profile["retrieval_bias"]["include"]
    assert "runtime_policy" in task_profile["retrieval_bias"]["include"]
    assert any(note == "family_resolution=scheduler_precedence" for note in task_profile["rationale"])


def test_retrieval_contract_prompt_compacts_policy_and_stage_families_deterministically(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = {
        "title": "Retrieval Ranking Evidence-First Answer Assembly And Search Plan Contract",
        "description": "improve retrieval ranking, search plan, and repo retriever freshness while keeping retrieval bias and passive relevance policies aligned",
        "objective": "rank direct evidence above stale memory and keep search-plan and retrieval policy boundaries explicit",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }

    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]

    assert task_profile["primary_task_type"] == "retrieval_ranking_contract"
    assert task_profile["task_families"] == [
        "retrieval_ranking_contract",
        "pipeline_internal_modification",
        "knowledge_and_retrieval_policy",
    ]
    assert any(note == "family_resolution=retrieval_contract_precedence" for note in task_profile["rationale"])


def test_prominent_hardening_concepts_override_background_bootstrap_mentions(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Operational hardening and preflight checks for a long-running assistant",
        "objective": "Improve operational safety, capability discovery, and preflight validation.",
        "description": "Parent notes mention restart and bootstrap history, but the work is about hardening.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "system_hardening"
    assert any("prominent_hardening_concepts" in entry for entry in task_profile["rationale"])


def test_prominent_truth_concepts_override_background_bootstrap_mentions(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Transcript reconciliation and identity truth contract",
        "objective": "Make transcript commits and operator authority the source of truth.",
        "description": "The issue references restart behavior, but the primary concern is truth ownership.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "truth_contract"
    assert any("prominent_truth_concepts" in entry for entry in task_profile["rationale"])


def test_prominent_orchestration_concepts_win_for_resume_and_checkpointing(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Workflow resume after restart with durable checkpointing",
        "objective": "Ensure recovery and checkpoint safety across disconnects.",
        "description": "Do not confuse this with general operations hardening.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "orchestration_checkpointing"
    assert any(
        token in entry
        for entry in task_profile["rationale"]
        for token in ("prominent_orchestration_concepts", "family_rules_imply_orchestration_checkpointing")
    )


def test_prominent_config_concepts_override_background_hardening_mentions(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Verifier strictness recalibration policy",
        "objective": "Adjust validator policy and configuration strictness.",
        "description": "The system also needs to remain robust, but this change is primarily configuration.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "config"
    assert any("prominent_config_concepts" in entry for entry in task_profile["rationale"])


def test_task_register_truth_language_routes_to_truth_contract(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Durable task register and open-loop truth",
        "objective": "Keep the task register and mutation log as the canonical store without a competing copy.",
        "description": "This is about truth ownership for active responsibilities, not generic hardening.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "truth_contract"
    assert any(
        token in entry
        for entry in task_profile["rationale"]
        for token in ("lab_assistant_truth_route", "prominent_truth_concepts", "family_rules_imply_truth_contract")
    )


def test_durable_turn_truth_language_routes_to_truth_contract(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Durable turn identity and session commit truth",
        "objective": "Make the turn event log and session commit the canonical durable turn truth.",
        "description": "Restart and reattach matter, but the primary concern is truth ownership for turns.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "truth_contract"
    assert any("prominent_turn_truth_concepts" in entry or "prominent_truth_concepts" in entry for entry in task_profile["rationale"])


def test_verifier_mentions_do_not_force_config_without_real_config_signals(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Overnight scheduler scenario with verifier freshness",
        "objective": "Keep evidence-backed outcomes and operational safety across unattended execution.",
        "description": "The scenario references verifier freshness, but it is not a configuration recalibration request.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "system_hardening"


def test_json_artifact_mentions_do_not_force_config_without_config_work(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Overnight execution scenario contract",
        "objective": "Keep evidence-backed outcomes and operational safety for unattended work.",
        "description": "The design references pis.md, meta.json, and events.jsonl as artifacts, but it is not a configuration task.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "system_hardening"


def test_scheduled_operational_scenario_routes_to_system_hardening(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Scheduled overnight execution and morning result scenario",
        "objective": "Keep operator-facing evidence and unattended execution outcomes truthful.",
        "description": "The scenario spans scheduled execution, durable receipts, and operational decision readiness.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] == "system_hardening"


def test_background_acceptance_test_mentions_do_not_force_test_fix(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "Transcript persistence and reconciliation contract",
        "objective": "Define the ownership and commit model for session truth.",
        "description": "Acceptance tests will be added later, but the request is not a test-only fix.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    assert task_profile["primary_task_type"] != "test_fix"
