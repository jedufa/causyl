from __future__ import annotations

import json
from pathlib import Path

from experiments_shared import experiment_contract_path
from experiments_shared import load_experiment_contract
from experiments_shared import canonicalize_experiment_contract
from experiments_shared import project_runner_contract
from experiments_shared import read_experiment_contract
from experiments_shared import summarize_experiment_contract
from experiments_shared import validate_experiment_contract


def _base_contract() -> dict:
    contract = {
        "schema_version": "experiment_contract_v1",
        "contract_hash": "",
        "compatibility": {
            "contract_family": "experiment_contract",
            "major": 1,
            "minor": 0,
        },
        "project": {
            "project_id": "demo",
            "display_name": "Demo Experiment",
            "owner": "causyl",
            "tags": ["gpu", "experiment"],
        },
        "launch": {
            "default_runner_id": "registry_batch",
            "supported_runners": ["registry_case", "registry_batch"],
            "entrypoints": {
                "registry_batch": {
                    "command": ["python", "-m", "experiments.projects.demo.runner"],
                    "working_dir": "experiments/projects/demo",
                    "env": {"PYTHONUNBUFFERED": "1"},
                },
                "registry_case": {
                    "command": ["python", "-m", "experiments.projects.demo.case_runner"],
                    "working_dir": "experiments/projects/demo",
                    "env": {"PYTHONUNBUFFERED": "1"},
                },
            },
            "args": {
                "required": ["registry"],
                "optional": ["dry_run", "profile"],
                "types": {
                    "registry": "path",
                    "profile": "string",
                    "dry_run": "bool",
                },
            },
        },
        "configuration": {
            "profiles_root": "experiments/projects/demo/_config",
            "registry_root": "experiments/projects/demo/_spec/registry/v1",
            "override_modes": ["patch", "profile"],
            "default_profile": "baseline",
        },
        "observability": {
            "status_files": [
                {"path": "artifact_manifest.json", "type": "json", "priority": 2},
                {"path": "run_meta.json", "type": "json", "priority": 1},
            ],
            "progress": {
                "mode": "wrapper",
                "fields": ["status", "elapsed_sec", "generation"],
            },
            "health_signals": ["exit_code", "heartbeat_age_sec"],
        },
        "control": {
            "pause_supported": True,
            "resume_supported": True,
            "stop_supported": True,
            "restart_supported": False,
            "checkpoint_supported": True,
            "branch_from_checkpoint_supported": True,
            "intervention_actions": ["graceful_stop", "pause", "resume", "branch_from_checkpoint"],
            "unsupported_actions": ["hot_parameter_mutation"],
        },
        "analysis": {
            "primary_artifacts": ["reports", "metrics"],
            "recipes": ["summary_report", "regression_compare"],
            "default_recipe": "summary_report",
        },
        "resources": {
            "resource_class": "gpu_heavy",
            "project_semaphore": "gpu/demo",
            "max_parallel_runs": 2,
            "estimated_footprint": {
                "gpu_count": 1,
                "cpu_cores": 8,
                "ram_gb": 16,
            },
        },
        "limitations": [
            "More than 2 parallel runs may overload available GPU capacity.",
            "Pause may not be immediate inside a non-interruptible step.",
        ],
        "artifacts": {
            "repo_tracked_outputs": ["small manifests", "operator-readable reports"],
            "external_data_root": "../demo-data",
            "artifact_manifest_required": True,
        },
        "context_policy": {
            "required_context_bundle": "experiment_demo_core_v1",
            "additional_context_refs_allowed": True,
            "forbidden_context_refs": ["full_repo_dump", "legacy_transcript_blob"],
        },
        "validation": {
            "schema_validation_required": True,
            "hash_validation_required": True,
            "prelaunch_checks": ["profiles_root_exists", "registry_root_exists", "runner_exists"],
        },
    }
    contract["contract_hash"] = validate_experiment_contract(contract)["computed_contract_hash"]
    return contract


def _write_contract_fixture(tmp_path: Path, contract: dict) -> Path:
    contract_path = experiment_contract_path(str(contract["project"]["project_id"]), repo_root=tmp_path)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return contract_path


def test_experiment_shared_exports_canonicalize_alias_and_project_runner_module() -> None:
    contract = _base_contract()
    normalized = canonicalize_experiment_contract(contract)
    assert normalized["project"]["project_id"] == "demo"
    assert hasattr(project_runner_contract, "resolve_project_runner")


def _prepare_prelaunch_files(tmp_path: Path) -> None:
    (tmp_path / "experiments" / "projects" / "demo" / "_config").mkdir(parents=True, exist_ok=True)
    (tmp_path / "experiments" / "projects" / "demo" / "_spec" / "registry" / "v1").mkdir(parents=True, exist_ok=True)
    (tmp_path / "experiments" / "projects" / "demo").mkdir(parents=True, exist_ok=True)
    (tmp_path / "experiments" / "projects" / "demo" / "runner.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (tmp_path / "experiments" / "projects" / "demo" / "case_runner.py").write_text("def main():\n    return 0\n", encoding="utf-8")


def test_validate_experiment_contract_normalizes_and_hashes_stably() -> None:
    raw_a = _base_contract()
    raw_b = _base_contract()
    raw_b["project"]["tags"] = ["experiment", "gpu", "experiment"]
    raw_b["launch"]["supported_runners"] = ["registry_batch", "registry_case", "registry_batch"]
    raw_b["configuration"]["override_modes"] = ["profile", "patch", "profile"]
    raw_b["observability"]["status_files"] = list(reversed(raw_b["observability"]["status_files"]))
    raw_b["observability"]["progress"]["fields"] = ["generation", "status", "elapsed_sec"]
    raw_b["control"]["intervention_actions"] = ["resume", "pause", "branch_from_checkpoint", "graceful_stop"]
    raw_b["context_policy"]["forbidden_context_refs"] = ["legacy_transcript_blob", "full_repo_dump"]
    raw_b["validation"]["prelaunch_checks"] = ["runner_exists", "registry_root_exists", "profiles_root_exists"]
    raw_b["contract_hash"] = validate_experiment_contract(raw_b)["computed_contract_hash"]

    va = validate_experiment_contract(raw_a)
    vb = validate_experiment_contract(raw_b)

    assert va["ok"] is True
    assert vb["ok"] is True
    assert va["computed_contract_hash"] == vb["computed_contract_hash"]
    assert va["normalized_contract"]["launch"]["supported_runners"] == ["registry_batch", "registry_case"]

    raw_c = _base_contract()
    raw_c["future_optional_field"] = {"enabled": True}
    raw_c["contract_hash"] = validate_experiment_contract(raw_c)["computed_contract_hash"]
    vc = validate_experiment_contract(raw_c)

    assert vc["ok"] is True
    assert vc["computed_contract_hash"] != va["computed_contract_hash"]
    assert any(row["code"] == "unknown_field" for row in vc["warnings"])


def test_validate_experiment_contract_rejects_invalid_runner_resource_and_context_policy() -> None:
    raw = _base_contract()
    raw["launch"]["default_runner_id"] = "missing_runner"
    raw["resources"]["max_parallel_runs"] = 0
    raw["context_policy"]["required_context_bundle"] = ""
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    result = validate_experiment_contract(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert result["failure_class"] == "experiment_contract_validation_failed"
    assert "invalid_runner_declaration" in codes
    assert "invalid_resource_policy" in codes
    assert "invalid_context_policy" in codes


def test_validate_experiment_contract_rejects_ambiguous_project_semaphore() -> None:
    raw = _base_contract()
    raw["resources"]["project_semaphore"] = "demo"
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    result = validate_experiment_contract(raw)

    resource_errors = [row for row in result["errors"] if row["code"] == "invalid_resource_policy"]
    assert result["ok"] is False
    assert any("project_semaphore must use the canonical <scope>/<project_id> shape" in row["message"] for row in resource_errors)


def test_validate_experiment_contract_rejects_invalid_control_action_declaration() -> None:
    raw = _base_contract()
    raw["control"]["pause_supported"] = False
    raw["control"]["intervention_actions"] = ["pause", "time_travel"]
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    result = validate_experiment_contract(raw)

    control_errors = [row for row in result["errors"] if row["code"] == "invalid_control_policy"]
    messages = {row["message"] for row in control_errors}

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_contract_validation_failed"
    assert "pause requires pause_supported=true" in messages
    assert "unsupported intervention action declared: time_travel" in messages


def test_validate_experiment_contract_rejects_missing_or_repo_local_external_data_root(tmp_path: Path) -> None:
    missing = _base_contract()
    missing["artifacts"]["external_data_root"] = ""
    missing["contract_hash"] = validate_experiment_contract(missing, repo_root=tmp_path)["computed_contract_hash"]

    missing_result = validate_experiment_contract(missing, repo_root=tmp_path)

    assert missing_result["ok"] is False
    assert any(row["code"] == "invalid_artifact_policy" for row in missing_result["errors"])
    assert any("external_data_root is required" in row["message"] for row in missing_result["errors"])

    repo_local = _base_contract()
    repo_local["artifacts"]["external_data_root"] = "experiments/projects/demo/_artifacts/runtime"
    repo_local["contract_hash"] = validate_experiment_contract(repo_local, repo_root=tmp_path)["computed_contract_hash"]

    repo_local_result = validate_experiment_contract(repo_local, repo_root=tmp_path)

    assert repo_local_result["ok"] is False
    assert any(row["code"] == "invalid_artifact_policy" for row in repo_local_result["errors"])
    assert any("outside the tracked repo root" in row["message"] for row in repo_local_result["errors"])


def test_summarize_experiment_contract_reports_artifact_boundary_resolution_shape(tmp_path: Path) -> None:
    raw = _base_contract()
    raw["artifacts"]["external_data_root"] = "EXPERIMENT_OUTPUTS_DIR or ~/causyl_experiment_data/demo"
    raw["contract_hash"] = validate_experiment_contract(raw, repo_root=tmp_path)["computed_contract_hash"]

    validation = validate_experiment_contract(raw, repo_root=tmp_path)
    summary = summarize_experiment_contract(validation["normalized_contract"], repo_root=tmp_path)

    assert validation["ok"] is True
    assert summary["artifact_boundary"]["external_data_root"]["resolution_kind"] == "env_or_path"
    assert summary["artifact_boundary"]["external_data_root"]["env_var"] == "EXPERIMENT_OUTPUTS_DIR"
    assert summary["artifact_boundary"]["external_data_root"]["fallback_path"] == "~/causyl_experiment_data/demo"
    assert summary["artifact_boundary"]["reverse_pointer_surfaces"] == ["run_record.json", "artifact_manifest.json"]


def test_validate_experiment_contract_rejects_missing_required_sections_and_fields() -> None:
    raw = _base_contract()
    raw.pop("artifacts")
    raw["project"]["display_name"] = ""
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    result = validate_experiment_contract(raw)

    missing_fields = {row["field"] for row in result["errors"] if row["code"] == "missing_field"}

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_contract_validation_failed"
    assert "artifacts" in missing_fields
    assert "project.display_name" in missing_fields


def test_validate_experiment_contract_rejects_unknown_major_and_hash_mismatch() -> None:
    raw = _base_contract()
    raw["compatibility"]["major"] = 2
    raw["contract_hash"] = "sha256:" + ("0" * 64)

    result = validate_experiment_contract(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "unknown_major_version" in codes
    assert "invalid_contract_hash" in codes


def test_load_experiment_contract_reads_canonical_project_path(tmp_path: Path) -> None:
    raw = _base_contract()
    _write_contract_fixture(tmp_path, raw)
    _prepare_prelaunch_files(tmp_path)

    loaded = load_experiment_contract("demo", repo_root=tmp_path)

    assert loaded["project"]["project_id"] == "demo"
    assert loaded["contract_hash"].startswith("sha256:")


def test_load_experiment_contract_raises_on_prelaunch_failure(tmp_path: Path) -> None:
    raw = _base_contract()
    _write_contract_fixture(tmp_path, raw)

    try:
        load_experiment_contract("demo", repo_root=tmp_path)
        raise AssertionError("expected strict load wrapper to reject prelaunch-failing contract")
    except ValueError as exc:
        assert "experiment contract prelaunch failed for demo" in str(exc)


def test_read_experiment_contract_returns_summary_and_prelaunch_success(tmp_path: Path) -> None:
    raw = _base_contract()
    _write_contract_fixture(tmp_path, raw)
    _prepare_prelaunch_files(tmp_path)

    result = read_experiment_contract("demo", repo_root=tmp_path)

    assert result["ok"] is True
    assert result["failure_class"] == ""
    assert result["prelaunch"]["ok"] is True
    assert result["summary"]["project_id"] == "demo"
    assert result["summary"]["display_name"] == "Demo Experiment"
    assert result["summary"]["contract_path"] == "experiments/projects/demo/_runner/experiment_contract.json"
    assert result["summary"]["declared_paths"] == {
        "profiles_root": "experiments/projects/demo/_config",
        "registry_root": "experiments/projects/demo/_spec/registry/v1",
    }


def test_read_experiment_contract_reports_invalid_json(tmp_path: Path) -> None:
    contract_path = experiment_contract_path("demo", repo_root=tmp_path)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text("{\n", encoding="utf-8")

    result = read_experiment_contract("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_contract_read_failed"
    assert result["errors"][0]["code"] == "invalid_contract_json"


def test_read_experiment_contract_reports_structured_prelaunch_failures(tmp_path: Path) -> None:
    raw = _base_contract()
    raw["configuration"]["profiles_root"] = ""
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]
    _write_contract_fixture(tmp_path, raw)

    result = read_experiment_contract("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_contract_prelaunch_failed"
    codes = {row["code"] for row in result["errors"]}
    assert "missing_required_path_declaration" in codes
    assert "declared_path_not_found" in codes
    assert "runner_entrypoint_not_found" in codes


def test_read_experiment_contract_promotes_prelaunch_warnings_to_top_level(tmp_path: Path) -> None:
    raw = _base_contract()
    raw["validation"]["prelaunch_checks"] = raw["validation"]["prelaunch_checks"] + ["future_check"]
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]
    _write_contract_fixture(tmp_path, raw)
    _prepare_prelaunch_files(tmp_path)

    result = read_experiment_contract("demo", repo_root=tmp_path)

    assert result["ok"] is True
    assert any(row["code"] == "unsupported_prelaunch_check" for row in result["warnings"])
    assert any(row["code"] == "unsupported_prelaunch_check" for row in result["prelaunch"]["warnings"])


def test_summarize_experiment_contract_omits_empty_optional_fields() -> None:
    raw = _base_contract()
    raw["project"]["owner"] = ""
    raw["project"]["tags"] = []
    summary = summarize_experiment_contract(validate_experiment_contract(raw)["normalized_contract"])

    assert "owner" not in summary
    assert "tags" not in summary


def test_summarize_experiment_contract_projects_declared_status_control_and_analysis() -> None:
    summary = summarize_experiment_contract(validate_experiment_contract(_base_contract())["normalized_contract"])

    assert summary["status_surfaces"] == {
        "declared": True,
        "status_files": ["run_meta.json", "artifact_manifest.json"],
        "progress_mode": "wrapper",
        "progress_fields": ["elapsed_sec", "generation", "status"],
        "health_signals": ["exit_code", "heartbeat_age_sec"],
    }
    assert summary["control_declarations"]["supported_actions"] == [
        "branch_from_checkpoint",
        "graceful_stop",
        "pause",
        "resume",
    ]
    assert summary["control_declarations"]["unsupported_actions"] == ["hot_parameter_mutation"]
    assert summary["analysis_entrypoints"] == {
        "declared": True,
        "recipes": ["regression_compare", "summary_report"],
        "default_recipe": "summary_report",
        "primary_artifacts": ["metrics", "reports"],
    }
    assert summary["limitations"] == [
        "More than 2 parallel runs may overload available GPU capacity.",
        "Pause may not be immediate inside a non-interruptible step.",
    ]
    assert summary["shared_resource_contract"]["resource_classification"]["canonical_key"] == "job_family:scheduler:gpu_heavy"
    assert summary["shared_resource_contract"]["claim_keys"] == [
        {
            "resource_class": "project",
            "scope": "gpu",
            "locator": "demo",
            "canonical_key": "project:gpu:demo",
        }
    ]


def test_summarize_experiment_contract_keeps_absent_declarations_explicit() -> None:
    raw = _base_contract()
    raw["observability"] = {"status_files": [], "progress": {"mode": "none", "fields": []}, "health_signals": []}
    raw["control"]["intervention_actions"] = []
    raw["control"]["unsupported_actions"] = []
    raw["analysis"] = {"primary_artifacts": [], "recipes": [], "default_recipe": ""}
    raw["limitations"] = []
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    summary = summarize_experiment_contract(validate_experiment_contract(raw)["normalized_contract"])

    assert summary["status_surfaces"] == {
        "declared": False,
        "status_files": [],
        "progress_mode": "none",
        "progress_fields": [],
        "health_signals": [],
    }
    assert summary["control_declarations"]["supported_actions"] == []
    assert summary["control_declarations"]["unsupported_actions"] == []
    assert summary["analysis_entrypoints"] == {
        "declared": False,
        "recipes": [],
        "default_recipe": "",
        "primary_artifacts": [],
    }
    assert summary["limitations"] == []


def test_validate_experiment_contract_supports_runner_specific_arg_overrides() -> None:
    raw = _base_contract()
    raw["launch"]["entrypoints"]["registry_batch"]["required_args"] = ["registry"]
    raw["launch"]["entrypoints"]["registry_batch"]["optional_args"] = ["dry_run", "profile"]
    raw["launch"]["entrypoints"]["registry_case"]["required_args"] = ["profile", "registry"]
    raw["launch"]["entrypoints"]["registry_case"]["optional_args"] = ["dry_run"]
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    result = validate_experiment_contract(raw)

    assert result["ok"] is True


def test_validate_experiment_contract_rejects_runner_specific_args_without_declared_types() -> None:
    raw = _base_contract()
    raw["launch"]["entrypoints"]["registry_case"]["required_args"] = ["registry", "missing_arg"]
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    result = validate_experiment_contract(raw)

    runner_errors = [row for row in result["errors"] if row["field"] == "launch.entrypoints.registry_case"]
    assert result["ok"] is False
    assert any("runner args must be declared in launch.args.types: missing_arg" == row["message"] for row in runner_errors)


def test_validate_experiment_contract_requires_runner_working_dir() -> None:
    raw = _base_contract()
    raw["launch"]["entrypoints"]["registry_batch"]["working_dir"] = ""
    raw["contract_hash"] = validate_experiment_contract(raw)["computed_contract_hash"]

    result = validate_experiment_contract(raw)

    assert result["ok"] is False
    assert any(
        row["field"] == "launch.entrypoints.registry_batch.working_dir"
        and row["message"] == "runner working_dir is required"
        for row in result["errors"]
    )
