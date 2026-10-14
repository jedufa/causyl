from __future__ import annotations

from pathlib import Path

from infra.pipeline.stages.job_intake import build_job_contract
from infra.pipeline.stages.search_plan import _extract_terms, build_search_plan
from infra.pipeline.stages.task_classifier import classify


def _stage3_result(title: str, repo_root: Path) -> tuple[dict, dict, dict]:
    prompt = {
        "title": title,
        "description": title,
        "objective": title,
        "task_type_hint": "unknown",
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    search_plan = build_search_plan(prompt, task_profile, max_candidates=80, intake_assessment=intake)["search_plan"]
    return search_plan, intake, task_profile


def test_stage3_scheduler_hardening_builds_cross_cutting_search_plan(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, intake, task_profile = _stage3_result(
        "make my scheduler more robust against failures, especially human failures",
        repo_root,
    )

    lexical = set(search_plan["lexical_queries"])
    conceptual = [entry.lower() for entry in search_plan["conceptual_queries"]]
    subsystems = set(search_plan["subsystem_hypotheses"])

    assert "scheduler_safety" in intake["task_families"]
    assert "scheduler_core" in task_profile["retrieval_bias"]["include"]

    assert "scheduler_watchdog.py" in lexical
    assert "scheduler_pipeline_healer.py" in lexical
    assert "scheduler_recovery_assistant.py" in lexical
    assert "scheduler_schedule_job.py" in lexical

    assert any("stale job claims" in entry or "reclaims" in entry for entry in conceptual)
    assert any("same logical job more than once" in entry for entry in conceptual)
    assert any("skip validation" in entry or "manual actions" in entry or "manual control actions" in entry for entry in conceptual) or "operator_entrypoints" in subsystems

    assert {"scheduler_core", "recovery_mechanisms", "state_persistence"}.issubset(subsystems)
    assert "docs/" in search_plan["negative_targets"]
    assert "reports/" in search_plan["negative_targets"]
    assert "runtime/" in search_plan["negative_targets"]
    assert search_plan["confidence"]["score"] >= 0.6



def test_stage3_small_prompt_uses_low_confidence_fallback_expansion(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, task_profile = _stage3_result("make it safer", repo_root)

    assert task_profile["task_families"] == ["generic"]
    assert search_plan["conceptual_queries"]
    assert len(search_plan["lexical_queries"]) <= 35
    assert len(search_plan["subsystem_hypotheses"]) >= 4
    assert search_plan["confidence"]["band"] == "low"
    assert search_plan["confidence"]["score"] < 0.6
    assert any(query in search_plan["conceptual_queries"] for query in ["partial write recovery", "durable commit guarantee"])
    assert "low_confidence_breadth_expansion=true" in search_plan["notes"]
    assert "fallback_expansion=deterministic_asset_widening" in search_plan["notes"]



def test_stage3_operator_task_prioritizes_operator_entrypoints_and_operator_queries(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, task_profile = _stage3_result("make pause and resume safer", repo_root)

    lexical = set(search_plan["lexical_queries"])
    conceptual = [entry.lower() for entry in search_plan["conceptual_queries"]]

    assert task_profile["task_families"] == ["operator_control"]
    assert "operator_entrypoints" in search_plan["subsystem_hypotheses"]
    assert "pause" in lexical
    assert "resume" in lexical
    assert any("manual actions" in entry or "manual control actions" in entry for entry in conceptual)
    assert any("resume logic" in entry or "state transition" in entry for entry in conceptual)


def test_stage3_scheduler_control_prompt_propagates_scheduler_bias_without_recovery_hijack(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, task_profile = _stage3_result(
        "Scheduler runtime policy pause and resume safety with watchdog and healer safeguards",
        repo_root,
    )

    assert task_profile["task_families"][0] == "scheduler_safety"
    assert "scheduler_or_control_plane" in task_profile["task_families"]
    assert "runtime_policy" in task_profile["retrieval_bias"]["include"]
    assert "scheduler_core" in search_plan["subsystem_hypotheses"]
    assert any(item in search_plan["subsystem_hypotheses"] for item in ["runtime_policy", "operator_entrypoints"])
    assert "scheduler_watchdog.py" in search_plan["lexical_queries"]
    assert "scheduled_jobs.py" not in search_plan["lexical_queries"]


def test_stage3_retrieval_contract_adds_ranking_and_query_shaping_subsystems(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = "Cross-project retrieval ranking with evidence-first answer assembly, missing evidence handling, and explicit search-plan shaping"
    search_plan, _intake, _task_profile = _stage3_result(prompt, repo_root)

    subsystems = set(search_plan["subsystem_hypotheses"])
    semantic_text = str(search_plan["semantic_query_text"]).lower()

    assert "retrieval_ranker" in subsystems
    assert "query_shaping" in subsystems
    assert "answer_assembly" in subsystems
    assert "evidence-first answer assembly" in semantic_text


def test_stage3_real_j5u9_prompt_keeps_query_shaping_for_narrow_contract(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = "Cross-project retrieval ranking, freshness, and evidence-first answer assembly contract"
    search_plan, _intake, _task_profile = _stage3_result(prompt, repo_root)

    subsystems = set(search_plan["subsystem_hypotheses"])
    assert "retrieval_ranker" in subsystems
    assert "query_shaping" in subsystems
    assert "answer_assembly" in subsystems


def test_stage3_truth_contract_task_surfaces_owner_queries_and_local_contract_docs(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, intake, task_profile = _stage3_result(
        "Persistent Assistant Identity and Source-of-Truth Contract with append-only audit events and deterministic write arbitration",
        repo_root,
    )

    lexical = set(search_plan["lexical_queries"])
    conceptual = [entry.lower() for entry in search_plan["conceptual_queries"]]

    assert "assistant_truth_and_identity" in intake["task_families"]
    assert task_profile["primary_task_type"] == "truth_contract"
    assert "assistant_runtime_state" in search_plan["subsystem_hypotheses"] or "runtime_state" in search_plan["subsystem_hypotheses"]
    assert "server_lab_assistant_state.py" in lexical
    assert "lab_assistant_identity_contract.md" in lexical
    assert any("assistant identity" in entry or "runtime state" in entry for entry in conceptual)
    assert any("append-only" in entry or "write-generation" in entry or "durable evidence" in entry for entry in conceptual)
    assert "docs_reference_allowed_for_truth_contract=true" in search_plan["notes"]


def test_stage3_retry_task_prunes_lexical_noise_without_losing_precision(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    prompt = "fix scheduler retry logic"
    search_plan, _intake, _task_profile = _stage3_result(prompt, repo_root)

    assert search_plan["confidence"]["score"] > 0.8
    assert len(search_plan["lexical_queries"]) <= 35
    assert "scheduler_watchdog.py" in search_plan["lexical_queries"]
    assert any("retry budgets" in entry for entry in search_plan["conceptual_queries"])


def test_stage3_system_failure_task_surfaces_subtype_coverage_and_recovery_precision(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result("prevent data corruption on restart", repo_root)

    covers = {
        cover
        for meta in search_plan["query_coverage_map"].values()
        for cover in meta.get("covers", [])
    }
    conceptual = [entry.lower() for entry in search_plan["conceptual_queries"]]

    assert "system_failure_modes" in covers
    assert "corruption_failure" in covers
    assert "crash_failure" in covers or "persistence_failure" in covers
    assert "state_persistence" in search_plan["subsystem_hypotheses"]
    assert "recovery_mechanisms" in search_plan["subsystem_hypotheses"]
    assert any("corrupted persisted state" in entry or "crash" in entry for entry in conceptual)


def test_stage3_multi_assistant_coordination_surfaces_agent_orchestration(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Multi-assistant coordination and delegation with supervisory orchestration and workcell routing",
        repo_root,
    )

    assert "agent_orchestration" in search_plan["subsystem_hypotheses"]


def test_stage3_task_register_prompt_surfaces_task_register_and_transcripts(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Task register bootstrap with open-loop memory, transcript recovery, and correction workflow",
        repo_root,
    )

    subsystems = set(search_plan["subsystem_hypotheses"])
    assert "task_register" in subsystems
    assert "turn_transcripts" in subsystems
    assert "execution_reconciliation" in subsystems


def test_stage3_extract_terms_keeps_bare_filenames_and_paths_as_file_hints() -> None:
    extracted = _extract_terms(
        {
            "title": "Fix app.py so empty input returns an empty string and update tests/test_app.py if needed",
            "description": "",
            "objective": "",
            "issue_context": {},
        }
    )

    assert "app.py" in extracted["file_hints"]
    assert "tests/test_app.py" in extracted["file_hints"]


def test_stage3_dependency_registry_prompt_surfaces_dependency_gating(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Typed endpoint registry with capability checks, dependency status, and launch preflight",
        repo_root,
    )

    assert "dependency_gating" in search_plan["subsystem_hypotheses"]


def test_stage3_chat_persistence_prompt_surfaces_transcript_resume_subsystems(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Direct chat persistence and hydration with session snapshot recovery and stable turn id after restart",
        repo_root,
    )

    subsystems = set(search_plan["subsystem_hypotheses"])
    assert "turn_transcripts" in subsystems
    assert "state_persistence" in subsystems
    assert "checkpoint_resume" in subsystems


def test_stage3_bootstrap_reconstruction_prompt_surfaces_task_register_and_recovery(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Bootstrap reconstruction from task register truth with degraded bootstrap handling and repeated fresh-turn reload",
        repo_root,
    )

    subsystems = set(search_plan["subsystem_hypotheses"])
    assert "task_register" in subsystems
    assert "checkpoint_resume" in subsystems
    assert "recovery_mechanisms" in subsystems


def test_stage3_detached_job_lifecycle_prompt_surfaces_worker_heartbeat_and_scheduler(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Detached long-running job heartbeat timeout cancellation and overnight scheduled recovery",
        repo_root,
    )

    subsystems = set(search_plan["subsystem_hypotheses"])
    assert "heartbeat_liveness" in subsystems
    assert "worker_runtime" in subsystems
    assert any(item in subsystems for item in ["scheduler_core", "schedule_calculation"])


def test_stage3_transcript_idempotency_prompt_surfaces_idempotency_store(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Final transcript commit idempotency with duplicate replay guard and turn-to-session reconciliation",
        repo_root,
    )

    subsystems = set(search_plan["subsystem_hypotheses"])
    assert "idempotency_store" in subsystems
    assert "turn_transcripts" in subsystems


def test_stage3_scheduled_scenario_prioritizes_scheduler_subsystems(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Overnight scheduled chain with morning result summary, unattended operation, and durable outcome receipts",
        repo_root,
    )

    top = search_plan["subsystem_hypotheses"][:5]
    assert "scheduler_core" in top
    assert "schedule_calculation" in top
    assert "state_persistence" in top


def test_stage3_verifier_audit_prompt_prioritizes_validation_and_audit(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "One-iteration PIS quality audit with verifier strictness recalibration and dependency gating",
        repo_root,
    )

    top = search_plan["subsystem_hypotheses"][:5]
    assert "validation" in top
    assert "audit_receipts" in top
    assert "dependency_gating" in top


def test_stage3_ui_projection_prompt_prioritizes_projection_surfaces(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "UI projection with staleness, degraded state, unreachable state, and operator-visible writeback",
        repo_root,
    )

    top = search_plan["subsystem_hypotheses"][:5]
    assert "operator_visible_state" in top
    assert "writeback_projection" in top
    assert "recovery_mechanisms" in top


def test_stage3_local_tooling_prompt_prioritizes_operator_and_dependency_surfaces(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    search_plan, _intake, _task_profile = _stage3_result(
        "Deterministic local tooling layer with token-efficiency fabric, operator tool contract, and category model",
        repo_root,
    )

    top = search_plan["subsystem_hypotheses"][:5]
    assert "dependency_gating" in top
    assert "operator_entrypoints" in top
    assert "validation" in top
