from __future__ import annotations

from pathlib import Path

import pytest

from infra.pipeline.planning import bundle_context, maybe_model_patch_plan, rank_context
from infra.pipeline.stages.job_intake import build_job_contract
from infra.pipeline.stages.repo_retriever import build_retrieval
from infra.pipeline.stages.search_plan import build_search_plan
from infra.pipeline.stages.task_classifier import classify


pytestmark = [pytest.mark.regression]


def _base_config() -> dict:
    return {
        "limits": {
            "min_file_score": 0.1,
            "max_selected_files": 6,
            "max_snippets": 12,
            "max_bundle_files": 6,
            "max_bundle_snippets": 12,
            "max_snippet_chars": 1200,
            "max_total_context_chars": 12000,
            "max_primary_targets": 4,
            "max_targets_per_subsystem": 2,
            "score_margin_keep_secondary": 0.15,
        },
        "semantic": {
            "semantic_score_weight": 0.35,
            "semantic_mode": "off",
            "semantic_provider": "none",
            "enable_semantic_retrieval": False,
        },
        "planning": {"enable_model": False},
        "_stage": {"allow_remote_model": False},
    }


def _prompt(title: str, description: str, objective: str) -> dict:
    return {
        "title": title,
        "description": description,
        "objective": objective,
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
        "task_type_hint": "unknown",
    }


def _run_pipeline(prompt: dict, repo_root: Path) -> tuple[dict, dict, dict, dict, dict, dict]:
    config = _base_config()
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    search_plan = build_search_plan(prompt, task_profile, max_candidates=80, intake_assessment=intake)["search_plan"]
    result_obj = {
        "job_contract": {**prompt, "repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": []},
        "intake_assessment": intake,
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": []},
    }
    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    ranked = rank_context({**result_obj, "retrieval": retrieval}, config)
    bundled = bundle_context({**result_obj, **ranked}, config)
    patch_plan, _warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, config)
    return intake, task_profile, search_plan, ranked, bundled, patch_plan


def test_2cac_orchestration_checkpointing_regression(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True)
    (repo_root / "infra/webui/server_agents_orchestrator.py").write_text(
        "def checkpoint_turn():\n"
        "    persist_checkpoint()\n"
        "    prevent_duplicate_replay()\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_routes_and_main.py").write_text("route status dashboard only\n", encoding="utf-8")
    (repo_root / "tests/control_plane/webui/test_agents_orchestrator.py").write_text(
        "def test_checkpoint_restart_safety():\n    assert True\n",
        encoding="utf-8",
    )

    prompt = _prompt(
        "Crash Safe Idempotent Orchestration Turn Checkpointing",
        "ensure idempotent checkpointing and prevent duplicate execution after restart",
        "persist turn execution checkpointing and prevent duplicate replay after restart",
    )
    _intake, task_profile, _search_plan, ranked, _bundled, patch_plan = _run_pipeline(prompt, repo_root)

    assert task_profile["primary_task_type"] == "orchestration_checkpointing"
    targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    assert targets[0] == "infra/webui/server_agents_orchestrator.py"
    assert "infra/webui/server_routes_and_main.py" not in targets
    assert patch_plan["failure_mappings"]["concurrency_failure_modes"]
    assert patch_plan["required_tests"]


def test_c735_scheduler_bundle_regression(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/controlplane").mkdir(parents=True)
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "infra/webui/static").mkdir(parents=True)
    (repo_root / "tests/controlplane").mkdir(parents=True)
    (repo_root / "infra/controlplane/scheduled_jobs.py").write_text(
        "def run_due_jobs():\n    reclaim_stale_claim()\n    dispatch_ready_job()\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_pipeline_healer.py").write_text(
        "def heal_scheduler_pipeline():\n    reconcile_orphaned_run()\n    repair_stale_claim()\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/static/scheduled_jobs_status.js").write_text("show degraded scheduler status\n", encoding="utf-8")
    (repo_root / "tests/controlplane/test_scheduled_jobs.py").write_text(
        "def test_scheduler_recovery():\n    assert True\n",
        encoding="utf-8",
    )

    prompt = _prompt(
        "Make My Scheduler More Robust Against Failures, Especially Human Failures",
        "make my scheduler robust against stale claims, restart failures, watchdog drift, and operator-visible recovery drift",
        "harden scheduler watchdog, healing, operator visibility, and regression coverage for scheduler recovery",
    )
    _intake, task_profile, _search_plan, _ranked, bundled, _patch_plan = _run_pipeline(prompt, repo_root)

    assert "scheduler_safety" in task_profile["task_families"]
    assert _ranked["ranked_context"]["implementation_targets"][0]["path"] in {
        "infra/controlplane/scheduled_jobs.py",
        "infra/bin/scheduler_pipeline_healer.py",
    }
    files = {entry["path"] for entry in bundled["context_bundle"]["files"]}
    assert "infra/bin/scheduler_pipeline_healer.py" in files
    assert "infra/webui/static/scheduled_jobs_status.js" in files
    assert "tests/controlplane/test_scheduled_jobs.py" in files
    assert "missing_scheduler_recovery" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]
    assert "missing_scheduler_ui" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]


def test_n1s3_ui_truth_projection_bundle_regression(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "infra/webui/static").mkdir(parents=True, exist_ok=True)
    (repo_root / "tools/webui").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True)
    (repo_root / "infra/webui/server_lab_assistant_operator_snapshot.py").write_text(
        "def project_status():\n    return 'degraded when backend unreachable'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/static/operator_status.js").write_text("render degraded unreachable reconnecting states\n", encoding="utf-8")
    (repo_root / "tests/control_plane/webui/test_operator_snapshot.py").write_text(
        "def test_stale_projection_honesty():\n    assert True\n",
        encoding="utf-8",
    )

    prompt = _prompt(
        "UI Projection, Staleness, Degraded, and Unreachable State Contract",
        "ensure UI shows degraded vs unreachable states correctly and stale cached truth is marked honestly",
        "fix UI snapshot staleness and honest degraded or unreachable status projection",
    )
    _intake, task_profile, _search_plan, ranked, bundled, _patch_plan = _run_pipeline(prompt, repo_root)

    assert task_profile["primary_task_type"] == "ui_truth_projection"
    assert ranked["ranked_context"]["implementation_targets"][0]["path"] == "infra/webui/server_lab_assistant_operator_snapshot.py"
    bundle_paths = {entry["path"] for entry in bundled["context_bundle"]["files"]}
    assert "infra/webui/static/operator_status.js" in bundle_paths
    assert "tests/control_plane/webui/test_operator_snapshot.py" in bundle_paths
    assert "missing_failure_category_coverage" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]
    assert "missing_projection_owner" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]
    assert "missing_projection_ui" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]


def test_2848_truth_contract_bundle_and_contract_regression(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "infra/webui/server_lab_assistant_state.py").write_text(
        "def resolve_source_precedence():\n    assistant_id = 'alpha'\n    write_generation = 2\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_task_register.py").write_text("reconciliation task register truth boundary\n", encoding="utf-8")
    (repo_root / "infra/webui/server_lab_assistant_writeback.py").write_text("writeback boundary for runtime state\n", encoding="utf-8")
    (repo_root / "tests/control_plane/webui/test_lab_assistant_state.py").write_text(
        "def test_truth_precedence():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "docs/lab_assistant_identity_contract.md").write_text("assistant identity truth contract\n", encoding="utf-8")

    prompt = _prompt(
        "Persistent Assistant Identity and Source-of-Truth Contract",
        "define durable assistant identity, append-only evidence, deterministic write arbitration, and explicit truth precedence",
        "define durable assistant identity reload, source-of-truth precedence, and deterministic write arbitration",
    )
    _intake, task_profile, _search_plan, ranked, bundled, patch_plan = _run_pipeline(prompt, repo_root)

    assert task_profile["primary_task_type"] == "truth_contract"
    targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    assert targets == [
        "infra/webui/server_lab_assistant_state.py",
        "tests/control_plane/webui/test_lab_assistant_state.py",
    ]
    bundle_paths = {entry["path"] for entry in bundled["context_bundle"]["files"]}
    assert "infra/webui/server_lab_assistant_task_register.py" in bundle_paths
    assert "infra/webui/server_lab_assistant_writeback.py" in bundle_paths
    assert "missing_truth_boundary" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]
    assert "missing_truth_shape" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]
    assert patch_plan["outcome_type"] == "validation_only"
    assert patch_plan["failure_mappings"]["system_failure_modes"]


def test_j5u9_retrieval_ranking_owner_and_contamination_regression(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "infra/pipeline/stages").mkdir(parents=True)
    (repo_root / "tools/controlplane").mkdir(parents=True)
    (repo_root / "tests/pipeline").mkdir(parents=True)
    (repo_root / "tests/benchmarks/pipeline/benchmark-gpt/J5U9").mkdir(parents=True)
    (repo_root / "infra/pipeline/planning.py").write_text(
        "def score_retrieval_candidate():\n    return 1\n\n"
        "def rank_evidence_by_freshness():\n    choose_evidence_first_source()\n    enforce_applicability_boundary_in_answer()\n"
        "    return 'direct evidence before stale memory'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/pipeline/stages/search_plan.py").write_text(
        "def build_retrieval_search_plan():\n    expand_query_coverage_map()\n    emit_search_plan_anchors()\n"
        "    return ['freshness', 'answer assembly', 'evidence-first']\n",
        encoding="utf-8",
    )
    (repo_root / "infra/pipeline/stages/repo_retriever.py").write_text(
        "def assemble_evidence_first_answer():\n    emit_missing_evidence_reason()\n"
        "    attach_answer_provenance_and_freshness()\n    return ['direct evidence', 'freshness']\n",
        encoding="utf-8",
    )
    (repo_root / "infra/pipeline/stages/context_ranker.py").write_text(
        "def score_retrieval_candidate():\n    rank_evidence_by_freshness()\n    candidate scoring keeps freshness and applicability ordering\n    return 1\n",
        encoding="utf-8",
    )
    (repo_root / "infra/controlplane/job_registry.py").write_text(
        "def registry_status():\n    return {'freshness': 'display only'}\n",
        encoding="utf-8",
    )
    (repo_root / "tests/pipeline/test_planning_flow.py").write_text(
        "def test_rank_evidence():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "tests/benchmarks/pipeline/benchmark-gpt/J5U9/scorecard_J5U9.md").write_text(
        "benchmark draft contamination only\n",
        encoding="utf-8",
    )

    prompt = _prompt(
        "Cross-Project Retrieval Ranking, Freshness, and Evidence-First Answer Assembly Contract",
        "improve retrieval ranking, search plan shaping, and repo retrieval freshness with explicit missing evidence handling",
        "rank direct evidence above stale memory and preserve applicability boundaries in answer assembly across planning, search, and retrieval",
    )
    config = _base_config()
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    search_plan = build_search_plan(prompt, task_profile, max_candidates=80, intake_assessment=intake)["search_plan"]
    result_obj = {
        "job_contract": {**prompt, "repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": []},
        "intake_assessment": intake,
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": []},
    }
    retrieval = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    ranked = rank_context({**result_obj, "retrieval": retrieval}, config)
    bundled = bundle_context({**result_obj, **ranked}, config)
    patch_plan, _warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, config)

    assert task_profile["primary_task_type"] == "retrieval_ranking_contract"
    retrieval_candidates = {entry["path"]: entry for entry in retrieval["file_candidates"]}
    assert not any("knowledge_drafts" in path or "tests/benchmarks/pipeline" in path for path in retrieval_candidates)
    targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    refs = [entry["path"] for entry in ranked["ranked_context"]["reference_context"]]
    assert targets == ["infra/pipeline/planning.py"]
    assert "infra/pipeline/stages/search_plan.py" in refs
    assert "infra/pipeline/stages/repo_retriever.py" in refs
    assert "infra/pipeline/stages/context_ranker.py" in refs
    assert not any("knowledge_drafts" in path or "tests/benchmarks/pipeline" in path for path in targets + refs)
    assert "retrieval_owner" in retrieval_candidates["infra/pipeline/planning.py"]["covers"]
    assert "retrieval_search" in retrieval_candidates["infra/pipeline/stages/search_plan.py"]["covers"]
    assert "retrieval_answer" in retrieval_candidates["infra/pipeline/stages/repo_retriever.py"]["covers"]
    assert "retrieval_scoring" in retrieval_candidates["infra/pipeline/stages/context_ranker.py"]["covers"]
    assert "missing_retrieval_shape" not in bundled["context_bundle"]["coverage_validation"]["warning_markers"]
    assert patch_plan["required_tests"]


def test_truth_contract_filters_scheduler_pipeline_and_governance_noise_from_selected_files(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "tools/controlplane").mkdir(parents=True)
    (repo_root / "infra/pipeline/stages").mkdir(parents=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "governance/ledger/patch_instruction_sheets/PIS-20260312-161622Z-2848__persistent-assistant-identity-and-source-of-truth-contract").mkdir(parents=True)
    (repo_root / "governance/ledger/gold_items_large/PIS-20260312-161622Z-2848__persistent-assistant-identity-and-source-of-truth-contract").mkdir(parents=True)
    (repo_root / "infra/webui/server_lab_assistant_state.py").write_text(
        "def load_runtime_state():\n    persist_truth_identity()\n    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_execution_truth_ownership.py").write_text(
        "def enforce_truth_ownership():\n    arbitrate_truth_boundary()\n    return True\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/webui/test_lab_assistant_state.py").write_text(
        "def test_truth_identity_reload():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "docs/lab_assistant_identity_contract.md").write_text("truth identity contract\n", encoding="utf-8")
    (repo_root / "infra/controlplane/scheduled_jobs.py").write_text("scheduled jobs watchdog heartbeat retry\n", encoding="utf-8")
    (repo_root / "infra/pipeline/stages/search_plan.py").write_text("planning pipeline draft truth identity\n", encoding="utf-8")
    (repo_root / "governance/ledger/patch_instruction_sheets/PIS-20260312-161622Z-2848__persistent-assistant-identity-and-source-of-truth-contract/pis.md").write_text(
        "persistent assistant identity and source-of-truth contract\n",
        encoding="utf-8",
    )
    (repo_root / "governance/ledger/gold_items_large/PIS-20260312-161622Z-2848__persistent-assistant-identity-and-source-of-truth-contract/pis.md").write_text(
        "persistent assistant identity and source-of-truth contract\n",
        encoding="utf-8",
    )

    prompt = _prompt(
        "Persistent Assistant Identity and Source-of-Truth Contract",
        "define durable assistant identity and explicit truth precedence over projections and restarts",
        "persist durable assistant identity, truth precedence, and honest state reload",
    )
    _intake, task_profile, _search_plan, ranked, _bundled, _patch_plan = _run_pipeline(prompt, repo_root)

    selected = [entry["path"] for entry in ranked["ranked_context"]["selected_files"]]
    refs = [entry["path"] for entry in ranked["ranked_context"]["reference_context"]]

    assert task_profile["primary_task_type"] == "truth_contract"
    assert "infra/webui/server_lab_assistant_state.py" in selected
    assert "infra/controlplane/scheduled_jobs.py" not in selected
    assert "infra/pipeline/stages/search_plan.py" not in selected
    assert not any(path.startswith("governance/ledger/") for path in refs)
    assert not any(path.startswith("governance/ledger/") for path in selected)


def test_lab_assistant_authority_truth_contract_prefers_truth_owners_over_scheduler(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "tools/controlplane").mkdir(parents=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True)
    (repo_root / "infra/webui/server_lab_assistant_execution_truth_ownership.py").write_text(
        "def enforce_truth_ownership():\n    require_operator_authority()\n    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_task_register.py").write_text(
        "def mutate_task_register():\n    preserve_multi_operator_tenancy()\n    return True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_delegation_policy.py").write_text(
        "def validate_delegation_policy():\n    require_high_impact_approval()\n    return True\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/webui/test_lab_assistant_task_register.py").write_text(
        "def test_task_register_authority():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "infra/controlplane/scheduled_jobs.py").write_text(
        "scheduled jobs watchdog heartbeat retry\n",
        encoding="utf-8",
    )

    prompt = _prompt(
        "Multi-Operator Authority, Tenancy, and High-Impact Approval Contract",
        "enforce lab assistant task register authority, delegation policy, and truth ownership for high-impact approval flows",
        "define authoritative task register and approval truth for multi-operator tenancy",
    )
    _intake, task_profile, _search_plan, ranked, _bundled, _patch_plan = _run_pipeline(prompt, repo_root)

    targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"]]
    selected = [entry["path"] for entry in ranked["ranked_context"]["selected_files"]]

    assert task_profile["primary_task_type"] == "truth_contract"
    assert "infra/webui/server_lab_assistant_execution_truth_ownership.py" in targets
    assert "infra/controlplane/scheduled_jobs.py" not in targets
    assert "infra/webui/server_lab_assistant_task_register.py" in selected


def test_bundle_caps_duplicate_broad_hubs_for_ui_truth_projection(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "tools/webui").mkdir(parents=True, exist_ok=True)
    (repo_root / "infra/webui/static").mkdir(parents=True, exist_ok=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True, exist_ok=True)
    (repo_root / "infra/webui/server_lab_assistant_operator_snapshot.py").write_text(
        "def project_status():\n    return 'degraded unreachable stale snapshot'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_routes_and_main.py").write_text(
        "route status projection fallback only\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_agents_orchestrator.py").write_text(
        "orchestrator status projection fallback only\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/static/operator_status.js").write_text(
        "render degraded unreachable reconnecting states\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/webui/test_operator_snapshot.py").write_text(
        "def test_stale_projection_honesty():\n    assert True\n",
        encoding="utf-8",
    )

    result_obj = {
        "job_contract": {
            "repo_root": str(repo_root),
            "title": "UI Projection, Staleness, Degraded, and Unreachable State Contract",
            "description": "ensure UI shows degraded vs unreachable states correctly and stale cached truth is marked honestly",
            "objective": "fix UI snapshot staleness and honest degraded or unreachable status projection",
            "allowed_paths": [],
            "forbidden_paths": [],
            "issue_context": {},
            "constraints": [],
            "acceptance_criteria": [],
        },
        "task_profile": {
            "primary_task_type": "ui_truth_projection",
            "task_families": ["ui_truth_projection"],
            "secondary_tags": ["ui_truth_projection"],
            "task_archetype": "implementation_change",
            "retrieval_bias": {},
        },
        "search_plan": {
            "queries": [],
            "subsystem_hypotheses": ["operator_status_projection", "webui_router", "webui_orchestrator"],
            "notes": [],
            "max_candidates": 20,
        },
        "retrieval": {"file_candidates": []},
        "ranked_context": {
            "selection_mode": "targeted_ranked_context",
            "replace_spec": {},
            "implementation_targets": [
                {
                    "path": "infra/webui/server_lab_assistant_operator_snapshot.py",
                    "selection_lane": "implementation_target",
                    "edit_allowed": True,
                    "ownership_role": "core_owner",
                    "candidate_role": "code",
                    "score": 9.5,
                    "targetability_score": 9.5,
                    "reference_value_score": 9.0,
                    "reasons": ["projection owner"],
                    "snippet_candidates": [{"start_line": 1, "end_line": 1, "reason": "owner", "kind": "line_window"}],
                }
            ],
            "reference_context": [
                {
                    "path": "infra/webui/server_routes_and_main.py",
                    "selection_lane": "reference_context",
                    "edit_allowed": False,
                    "ownership_role": "hub",
                    "candidate_role": "code",
                    "hub_kind": "webui_router_hub",
                    "hub_surface_penalty": 1.1,
                    "owner_signal_strength": 0.0,
                    "score": 7.0,
                    "reference_value_score": 7.0,
                    "reasons": ["router hub"],
                    "snippet_candidates": [{"start_line": 1, "end_line": 1, "reason": "route", "kind": "line_window"}],
                },
                {
                    "path": "infra/webui/server_agents_orchestrator.py",
                    "selection_lane": "reference_context",
                    "edit_allowed": False,
                    "ownership_role": "hub",
                    "candidate_role": "code",
                    "hub_kind": "webui_orchestrator_hub",
                    "hub_surface_penalty": 0.9,
                    "owner_signal_strength": 0.0,
                    "score": 6.8,
                    "reference_value_score": 6.8,
                    "reasons": ["orchestrator hub"],
                    "snippet_candidates": [{"start_line": 1, "end_line": 1, "reason": "orch", "kind": "line_window"}],
                },
                {
                    "path": "infra/webui/static/operator_status.js",
                    "selection_lane": "reference_context",
                    "edit_allowed": False,
                    "ownership_role": "projection",
                    "candidate_role": "code",
                    "score": 6.6,
                    "reference_value_score": 6.6,
                    "reasons": ["ui projection surface"],
                    "snippet_candidates": [{"start_line": 1, "end_line": 1, "reason": "ui", "kind": "line_window"}],
                },
                {
                    "path": "tests/control_plane/webui/test_operator_snapshot.py",
                    "selection_lane": "reference_context",
                    "edit_allowed": False,
                    "ownership_role": "test_surface",
                    "candidate_role": "test_file",
                    "score": 6.4,
                    "reference_value_score": 6.4,
                    "reasons": ["truthful stale projection regression"],
                    "snippet_candidates": [{"start_line": 1, "end_line": 1, "reason": "test", "kind": "line_window"}],
                },
            ],
            "ranked_files": [],
            "selected_files": [],
        },
    }

    bundled = bundle_context(result_obj, {"limits": {"max_bundle_files": 6, "max_bundle_snippets": 10, "max_snippet_chars": 1200, "max_total_context_chars": 12000}})
    paths = [str(item.get("path") or "") for item in bundled["context_bundle"]["files"]]
    assert "infra/webui/server_lab_assistant_operator_snapshot.py" in paths
    assert "infra/webui/static/operator_status.js" in paths
    assert "tests/control_plane/webui/test_operator_snapshot.py" in paths
    broad_hubs = [path for path in paths if path in {"infra/webui/server_routes_and_main.py", "infra/webui/server_agents_orchestrator.py"}]
    assert len(broad_hubs) <= 1


def _build_p4r6_repo(repo_root: Path) -> None:
    (repo_root / "tools/webui").mkdir(parents=True)
    (repo_root / "tools/bin").mkdir(parents=True)
    (repo_root / "tools/controlplane").mkdir(parents=True)
    (repo_root / "tests/control_plane/webui").mkdir(parents=True)
    (repo_root / "tests/control_plane").mkdir(parents=True, exist_ok=True)
    (repo_root / "infra/webui/server_lab_assistant_mailbox.py").write_text(
        "def claim_mailbox_message():\n"
        "    validate_mailbox_claim()\n"
        "    deliver_detached_job_request()\n"
        "    return 'mailbox claim'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_job_lifecycle.py").write_text(
        "def reconcile_job_lifecycle():\n"
        "    validate_terminal_transition()\n"
        "    persist_detached_job_terminal_state()\n"
        "    return 'job lifecycle'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_writeback.py").write_text(
        "def canonical_writeback():\n"
        "    validate_canonical_task_truth_writeback()\n"
        "    ensure_idempotent_task_truth_reconciliation()\n"
        "    return 'writeback'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_task_register.py").write_text(
        "def update_task_register():\n"
        "    validate_task_register_state()\n"
        "    reconcile_canonical_task_truth()\n"
        "    return 'task register'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_execution_truth_ownership.py").write_text(
        "def truth_owner():\n"
        "    validate_truth_boundary()\n"
        "    enforce_execution_truth_ownership()\n"
        "    return 'truth ownership'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/webui/server_lab_assistant_recovery.py").write_text(
        "def recover_jobs():\n"
        "    validate_recovery_state()\n"
        "    reconcile_stale_job_state()\n"
        "    return 'recovery'\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_pipeline_healer.py").write_text(
        "def heal_scheduler_pipeline():\n"
        "    repair_stale_claim()\n"
        "    reconcile_orphaned_run()\n",
        encoding="utf-8",
    )
    (repo_root / "infra/bin/scheduler_watchdog.py").write_text(
        "def watchdog():\n"
        "    monitor_scheduled_jobs()\n"
        "    emit_scheduler_recovery_status()\n",
        encoding="utf-8",
    )
    (repo_root / "infra/controlplane/scheduled_jobs.py").write_text(
        "def run_due_jobs():\n"
        "    schedule_due_job()\n"
        "    retry_terminal_no_auto_retry()\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/webui/test_lab_assistant_mailbox.py").write_text(
        "def test_mailbox_claim_idempotent():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/webui/test_lab_assistant_job_lifecycle.py").write_text(
        "def test_job_lifecycle_terminal_writeback():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/webui/test_lab_assistant_writeback.py").write_text(
        "def test_canonical_writeback_task_truth():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "tests/control_plane/test_scheduled_jobs.py").write_text(
        "def test_scheduler_recovery():\n    assert True\n",
        encoding="utf-8",
    )


def test_p4r6_title_only_mailbox_writeback_seam_regression(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _build_p4r6_repo(repo_root)

    prompt = _prompt(
        "Mailbox Lifecycle to Canonical Writeback and Idempotent Task Truth Contract (P4R6,impl) due=terminal_no_auto_retry@2026-03-30 01:00:00 CEST deps=1 iteration budget: up to 6",
        "",
        "",
    )
    _intake, task_profile, search_plan, ranked, _bundled, _patch_plan = _run_pipeline(prompt, repo_root)

    assert task_profile["primary_task_type"] == "truth_contract"
    assert "mailbox_writeback_seam" in task_profile["secondary_tags"]
    lexical_queries = list(search_plan.get("lexical_queries") or [])[:8]
    assert "server_lab_assistant_mailbox.py" in lexical_queries
    assert "server_lab_assistant_job_lifecycle.py" in lexical_queries
    assert "server_lab_assistant_writeback.py" in lexical_queries
    assert "scheduler_pipeline_healer.py" not in lexical_queries
    assert "scheduler_watchdog.py" not in lexical_queries
    targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"][:3]]
    assert targets == [
        "infra/webui/server_lab_assistant_mailbox.py",
        "infra/webui/server_lab_assistant_job_lifecycle.py",
        "infra/webui/server_lab_assistant_writeback.py",
    ]


def test_p4r6_full_mailbox_writeback_truth_contract_regression(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    _build_p4r6_repo(repo_root)

    prompt = _prompt(
        "Mailbox Lifecycle to Canonical Writeback and Idempotent Task Truth Contract",
        "ensure mailbox claim delivery, detached job lifecycle truth, canonical writeback, and idempotent task truth reconciliation",
        "target mailbox -> job lifecycle -> writeback seam and avoid scheduler-only or generic task register fixes",
    )
    _intake, task_profile, _search_plan, ranked, bundled, _patch_plan = _run_pipeline(prompt, repo_root)

    assert task_profile["primary_task_type"] == "truth_contract"
    assert "mailbox_writeback_seam" in task_profile["secondary_tags"]
    targets = [entry["path"] for entry in ranked["ranked_context"]["implementation_targets"][:3]]
    assert targets == [
        "infra/webui/server_lab_assistant_mailbox.py",
        "infra/webui/server_lab_assistant_job_lifecycle.py",
        "infra/webui/server_lab_assistant_writeback.py",
    ]
    assert not any("scheduler" in path for path in targets)
    bundle_paths = {entry["path"] for entry in bundled["context_bundle"]["files"]}
    assert "tests/control_plane/webui/test_lab_assistant_mailbox.py" in bundle_paths
    assert "tests/control_plane/webui/test_lab_assistant_job_lifecycle.py" in bundle_paths
    assert "tests/control_plane/webui/test_lab_assistant_writeback.py" in bundle_paths
