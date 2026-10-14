from __future__ import annotations

from pathlib import Path

from infra.pipeline.planning import bundle_context, maybe_model_patch_plan, rank_context
from infra.pipeline.stages.job_intake import build_job_contract
from infra.pipeline.stages.repo_retriever import build_retrieval
from infra.pipeline.stages.search_plan import build_search_plan
from infra.pipeline.stages.task_classifier import classify


def _base_config() -> dict:
    return {
        "limits": {
            "min_file_score": 1,
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


def test_repo_assessment_task_archetype_and_search_mode(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    prompt = {
        "title": "How reliable is my repo?",
        "description": "Assess the state of my repo, biggest technical risks, and overall maintainability.",
        "objective": "Assess repository reliability and major risk areas.",
        "task_type_hint": "unknown",
        "constraints": [],
        "issue_context": {},
    }
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    search_plan = build_search_plan(prompt, task_profile, intake_assessment=intake, max_candidates=60)["search_plan"]

    assert task_profile["task_archetype"] == "repo_assessment"
    assert task_profile["planning_profiles"]["output_profile"] == "assessment_report_contract"
    assert task_profile["output_contract_mode"] == "assessment_report_contract"
    assert search_plan["search_mode"] == "representative_assessment_bundle"


def test_design_new_surface_produces_design_contract(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "tests").mkdir(parents=True)

    (repo_root / "src/scheduler_core.py").write_text(
        "class SchedulerCore:\n    def submit(self, job):\n        return job\n",
        encoding="utf-8",
    )
    (repo_root / "src/scheduler_policy.py").write_text(
        "def validate_policy(name):\n    return name != ''\n",
        encoding="utf-8",
    )
    (repo_root / "tests/test_scheduler_core.py").write_text(
        "def test_submit():\n    assert True\n",
        encoding="utf-8",
    )
    (repo_root / "docs/scheduler_contract.md").write_text(
        "# scheduler contract\nDefines retry semantics, priority semantics, and invariants.\n",
        encoding="utf-8",
    )

    prompt = {
        "title": "Design a new scheduler admission controller file",
        "description": "Design a new file for scheduler admission control. The file does not exist yet and should follow existing scheduler contracts and code patterns.",
        "objective": "Design the new scheduler admission controller surface and integration points.",
        "task_type_hint": "unknown",
        "constraints": [],
        "acceptance_criteria": [],
        "issue_context": {},
    }
    config = _base_config()
    intake = build_job_contract(prompt, repo_root=str(repo_root))["intake_assessment"]
    task_profile = classify(prompt, intake_assessment=intake, repo_root=str(repo_root))["task_profile"]
    search_plan = build_search_plan(prompt, task_profile, intake_assessment=intake, max_candidates=60)["search_plan"]
    result_obj = {
        "job_contract": {**prompt, "repo_root": str(repo_root), "allowed_paths": [], "forbidden_paths": []},
        "intake_assessment": intake,
        "task_profile": task_profile,
        "search_plan": search_plan,
        "extracted_terms": {"file_hints": []},
    }
    result_obj["retrieval"] = build_retrieval(result_obj, repo_root=repo_root, rg_path="rg", timeout_seconds=30, config=config)["retrieval"]
    ranked = rank_context(result_obj, config=config)
    bundled = bundle_context({**result_obj, **ranked}, config=config)
    patch_plan, warnings = maybe_model_patch_plan({**result_obj, **ranked, **bundled}, config=config)

    bundle_roles = {entry["bundle_role"] for entry in bundled["context_bundle"]["files"]}

    assert task_profile["task_archetype"] == "design_new_surface"
    assert task_profile["output_contract_mode"] == "design_contract"
    assert "interface_contract_context" in bundle_roles
    assert bundle_roles.intersection({"new_file_support_context", "integration_context", "integration_target"})
    assert patch_plan["output_contract_mode"] == "design_contract"
    assert patch_plan["design_execution_contract"]["supporting_reference_files"]
    assert patch_plan["plan_status"] == "DESIGN_READY_FOR_REVIEW"
    assert patch_plan["should_escalate"] is False
    assert warnings
