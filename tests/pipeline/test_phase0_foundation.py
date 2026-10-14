from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from infra.pipeline.core import _parse_simple_yaml, load_pipeline_config
from infra.pipeline.knowledge_assets import DEFAULT_KNOWLEDGE_ASSET_FILES, _load_minimal_yaml, load_knowledge_assets
from infra.pipeline.planning_ontology import classify_file_role
from infra.pipeline.stages.repo_retriever import _count_searchable_repo_files
from infra.pipeline.pipeline_runner import PipelineStage
from infra.pipeline.schemas import (
    validate_context_bundle_result,
    validate_patch_plan_result,
    validate_ranked_context_result,
    validate_retrieval_result,
)
from infra.pipeline.stages.job_intake import build_job_contract
from infra.pipeline.v2_foundation import (
    ensure_context_bundle_foundation,
    ensure_intake_assessment,
    ensure_patch_plan_foundation,
    ensure_ranked_context_foundation,
    ensure_retrieval_candidate_foundation,
)


def test_build_job_contract_emits_phase1_generic_fallback_on_weak_prompt(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    result = build_job_contract(
        {
            "title": "Make it safer",
            "description": "",
            "objective": "",
        },
        repo_root=str(repo_root),
    )

    intake = result["intake_assessment"]
    assert intake["task_families"] == ["generic"]
    assert intake["failure_taxonomy"]["operator_failure_modes"]
    assert intake["invariants"]
    assert intake["derived_requirements"]["safety_requirements"]
    assert intake["confidence"]["stage"] == "script_1_job_intake"
    assert intake["confidence"]["computed"] is True
    assert intake["confidence"]["source"] == "deterministic_generic_fallback"
    assert "generic_fallback_used" in intake["confidence"]["notes"]



def test_load_pipeline_config_exposes_knowledge_asset_manifest() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    config = load_pipeline_config(repo_root=repo_root)
    manifest = config["_derived"]["knowledge_assets"]
    loaded = load_knowledge_assets(repo_root, config, ["task_families", "failure_taxonomy", "patch_contract_templates"])

    assert Path(config["_derived"]["knowledge_assets_root"]).exists()
    assert sorted(manifest["files"].keys()) == sorted(DEFAULT_KNOWLEDGE_ASSET_FILES.keys())
    assert all(manifest["exists"].values())
    assert loaded["task_families"]["version"] == 1
    assert "scheduler_safety" in loaded["task_families"]["families"]
    assert loaded["failure_taxonomy"]["version"] == 1
    assert loaded["patch_contract_templates"]["templates"]["default"]["invariants"]


def test_parse_simple_yaml_accepts_pipeline_defaults_lists() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    raw = (repo_root / "config" / "pipeline.defaults.yaml").read_text(encoding="utf-8")

    parsed = _parse_simple_yaml(raw)

    assert parsed["strategy_selection"]["order"] == [
        "local_fix",
        "plan_revision",
        "context_expansion",
        "task_reinterpretation",
    ]
    assert parsed["strategy_selection"]["rules"]


def test_load_pipeline_config_falls_back_to_embedded_defaults_for_external_repo(tmp_path: Path) -> None:
    repo_root = tmp_path / "disposable_repo"
    repo_root.mkdir()

    config = load_pipeline_config(repo_root=repo_root)
    loaded = load_knowledge_assets(repo_root, config, ["task_families", "failure_taxonomy"])

    assert config["_derived"]["repo_root"] == str(repo_root.resolve())
    assert config["_derived"]["jobs_root"] == str((repo_root / "jobs").resolve())
    assert config["_derived"]["workspace_root"] == str((repo_root / ".pipeline_workspaces").resolve())
    assert Path(config["_derived"]["config_root"]) == Path(__file__).resolve().parents[2]
    assert Path(config["_derived"]["knowledge_assets_root"]).exists()
    assert config["timeouts"]["subprocess_default_seconds"] > 0
    assert loaded["task_families"]["families"]
    assert loaded["failure_taxonomy"]["version"] == 1


def test_load_pipeline_config_ignores_blank_env_overrides() -> None:
    repo_root = Path(__file__).resolve().parents[2]

    config = load_pipeline_config(
        repo_root=repo_root,
        env={
            "PIPELINE_LIMITS__SCORE_MARGIN_KEEP_SECONDARY": "",
            "PIPELINE_TIMEOUTS__MODEL_SECONDS": "1234",
        },
    )

    assert config["limits"]["score_margin_keep_secondary"] == 0.15
    assert config["timeouts"]["model_seconds"] == 1234


def test_manifest_canonical_knowledge_assets_are_wired_and_loadable() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    manifest_path = repo_root / "infra/pipeline/knowledge/runtime_asset_manifest.yaml"

    import yaml

    manifest_data = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    canonical_files = list(manifest_data.get("canonical_runtime_files") or [])
    expected_asset_names = {Path(name).stem for name in canonical_files}
    expected_asset_names.add("bundle_coverage_rules")

    assert expected_asset_names.issubset(DEFAULT_KNOWLEDGE_ASSET_FILES.keys())

    config = load_pipeline_config(repo_root=repo_root)
    loaded = load_knowledge_assets(repo_root, config, sorted(expected_asset_names))

    for asset_name in sorted(expected_asset_names):
        assert asset_name in loaded
        assert isinstance(loaded[asset_name], Mapping)
        assert loaded[asset_name], asset_name


def test_phase0_optional_schema_fields_are_accepted() -> None:
    intake_assessment = ensure_intake_assessment(
        {
            "task_families": ["generic", "scheduler_safety"],
            "invariants": ["persisted state must remain valid"],
            "derived_requirements": {"safety_requirements": ["emit operator-visible evidence"]},
        }
    )

    retrieval_result = {
        "retrieval": {
            "file_candidates": [
                ensure_retrieval_candidate_foundation(
                    {
                        "path": "infra/pipeline/planning.py",
                        "evidence_sources": ["deterministic"],
                        "covers": ["state_consistency"],
                        "coverage_score": 0.75,
                    }
                )
            ]
        }
    }
    validate_retrieval_result(retrieval_result)

    ranked_result = {
        "ranked_context": ensure_ranked_context_foundation(
            {
                "selected_files": [
                    {
                        "path": "infra/pipeline/planning.py",
                        "reasons": ["owns patch planning"],
                        "snippet_candidates": [{"start_line": 1, "end_line": 10, "reason": "entrypoint"}],
                        "role": "implementation_target",
                        "why_not_other_roles": ["docs path excluded"],
                    }
                ],
                "implementation_targets": [],
                "reference_context": [],
            }
        )
    }
    validate_ranked_context_result(ranked_result)

    bundle_result = {
        "context_bundle": ensure_context_bundle_foundation(
            {
                "files": [
                    {
                        "path": "infra/pipeline/planning.py",
                        "snippets": [{"snippet_id": "s1", "content": "def maybe_model_patch_plan(...):"}],
                        "responsibility": ["recovery"],
                        "covers_failure_modes": ["operator_failure"],
                    }
                ],
                "editable_targets": [],
                "read_only_context": [],
                "bundle_metrics": {"snippet_count": 1},
                "bundle_gap_warning": True,
                "bundle_gap_details": ["concurrency coverage still empty"],
                "coverage_validation": {"bundle_ready_for_execution": False},
            }
        )
    }
    validate_context_bundle_result(bundle_result)

    patch_plan_result = {
        "patch_plan": ensure_patch_plan_foundation(
            {
                "objective": "Prepare deterministic-first V2 foundation",
                "modify_files": [
                    {
                        "path": "infra/pipeline/schemas.py",
                        "intended_modifications": ["extend optional schema fields"],
                        "rationale": ["Phase 0 structure only"],
                    }
                ],
                "non_goals": ["No Stage 1-7 reasoning implementation"],
                "assumptions": ["Later phases will populate these fields"],
                "unresolved_uncertainties": [],
                "required_tests": ["schema acceptance regression"],
                "planning_confidence": 0.64,
                "should_escalate": False,
            },
            intake_assessment=intake_assessment,
        )
    }
    validate_patch_plan_result(patch_plan_result)
    assert patch_plan_result["patch_plan"]["cross_cutting_contract"]["invariants"] == [
        "persisted state must remain valid"
    ]


def test_frontend_assets_are_treated_as_implementation_surfaces() -> None:
    assert classify_file_role("web/templates/schedules.html", "<div></div>") == "implementation_code"
    assert classify_file_role("web/static/schedules.css", ".grid {}") == "implementation_code"
    assert classify_file_role("web/static/schedules.scss", "$gap: 1rem;") == "implementation_code"
    assert classify_file_role("web/static/schedules.sass", ".grid\n  display: grid") == "implementation_code"


def test_pipeline_stage_command_can_resolve_script_from_pipeline_repo(tmp_path: Path) -> None:
    target_repo = tmp_path / "target"
    target_repo.mkdir()
    pipeline_repo = Path(__file__).resolve().parents[2]
    stage = PipelineStage("script_1_job_intake", "infra/pipeline/stages/job_intake.py")

    command = stage.command(
        python_exe="python3",
        io_input=tmp_path / "in.json",
        io_output=tmp_path / "out.json",
        log_level="INFO",
        repo_root=target_repo,
        script_root=pipeline_repo,
    )

    assert command[1] == str((pipeline_repo / "infra/pipeline/stages/job_intake.py").resolve())
    assert command[-1] == str(target_repo.resolve())


def test_minimal_yaml_loader_supports_folded_scalar_continuations() -> None:
    parsed = _load_minimal_yaml(
        "version: 1\n"
        "notes: Deterministic Stage 1 task-family activation rules. Canonical file with additive\n"
        "  repo/domain families merged in.\n"
        "families:\n"
        "  generic:\n"
        "    fallback_only: true\n"
    )

    assert parsed["version"] == 1
    assert parsed["notes"] == "Deterministic Stage 1 task-family activation rules. Canonical file with additive repo/domain families merged in."
    assert parsed["families"]["generic"]["fallback_only"] is True


def test_minimal_yaml_loader_supports_folded_list_item_continuations() -> None:
    parsed = _load_minimal_yaml(
        "families:\n"
        "  cleanup_retention:\n"
        "    system_failure_modes:\n"
        "    - cleanup job deletes primary records before derivative references are removed\n"
        "      or archived\n"
    )

    assert parsed["families"]["cleanup_retention"]["system_failure_modes"] == [
        "cleanup job deletes primary records before derivative references are removed or archived"
    ]


def test_count_searchable_repo_files_ignores_pipeline_runtime_noise(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    (repo_root / "jobs").mkdir()
    (repo_root / "reports").mkdir()
    (repo_root / "jobs" / "job_state.json").write_text("{}\n", encoding="utf-8")
    (repo_root / "reports" / "comparison_report.json").write_text("{}\n", encoding="utf-8")

    assert _count_searchable_repo_files(repo_root, {}) == 0

    (repo_root / "app.py").write_text("def greet(x):\n    return x\n", encoding="utf-8")
    assert _count_searchable_repo_files(repo_root, {}) == 1
