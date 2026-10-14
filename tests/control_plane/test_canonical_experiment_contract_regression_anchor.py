from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments_shared import experiment_contract_path
from experiments_shared import project_runner_contract
from experiments_shared import read_experiment_contract
from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from infra.controlplane import scheduled_jobs as sched


pytestmark = [pytest.mark.regression, pytest.mark.integration]


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
        ],
        "artifacts": {
            "repo_tracked_outputs": ["small manifests", "operator-readable reports"],
            "external_data_root": "../demo-data",
            "artifact_manifest_required": True,
        },
        "context_policy": {
            "required_context_bundle": "lab_core_v1",
            "additional_context_refs_allowed": False,
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


def _write_contract(tmp_path: Path, contract: dict) -> Path:
    contract_path = experiment_contract_path(str(contract["project"]["project_id"]), repo_root=tmp_path)
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": {
            "graceful_stop": {"policy_state": "enabled", "rollout_state": "live", "note": "default operator stop path"},
            "pause": {"policy_state": "enabled", "rollout_state": "live", "note": ""},
            "resume": {"policy_state": "enabled", "rollout_state": "live", "note": ""},
            "branch_from_checkpoint": {"policy_state": "disabled", "rollout_state": "staged", "note": "storage review"},
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    (contract_path.parent / "control_action_policy.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return contract_path


def _prepare_demo_project(tmp_path: Path) -> None:
    project_root = tmp_path / "experiments" / "projects" / "demo"
    (project_root / "_config").mkdir(parents=True, exist_ok=True)
    (project_root / "_spec" / "registry" / "v1").mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / "runner.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    (project_root / "case_runner.py").write_text("def main():\n    return 0\n", encoding="utf-8")


def _write_manifest(tmp_path: Path) -> None:
    manifest_path = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "project_runner_manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": "project_runner_manifest_v1",
                "project_id": "demo",
                "project_root": "experiments/projects/demo",
                "command_prefix": ["python3", "-u"],
                "runners": {
                    "registry_batch": {
                        "entrypoint": "experiments/projects/demo/runner.py",
                        "cwd": "experiments/projects/demo",
                        "description": "Legacy manifest compatibility path",
                        "args": {
                            "registry": {"type": "path", "required": True},
                        },
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_canonical_experiment_contract_regression_anchor_hash_and_shared_reader(tmp_path: Path) -> None:
    canonical = _base_contract()
    variant = _base_contract()
    variant["project"]["tags"] = ["experiment", "gpu", "experiment"]
    variant["launch"]["supported_runners"] = ["registry_batch", "registry_case", "registry_batch"]
    variant["configuration"]["override_modes"] = ["profile", "patch", "profile"]
    variant["observability"]["status_files"] = list(reversed(variant["observability"]["status_files"]))
    variant["observability"]["progress"]["fields"] = ["generation", "status", "elapsed_sec"]
    variant["contract_hash"] = validate_experiment_contract(variant)["computed_contract_hash"]

    canonical_validation = validate_experiment_contract(canonical)
    variant_validation = validate_experiment_contract(variant)

    assert canonical_validation["ok"] is True
    assert variant_validation["ok"] is True
    assert canonical_validation["computed_contract_hash"] == variant_validation["computed_contract_hash"]

    _write_contract(tmp_path, canonical)
    _prepare_demo_project(tmp_path)
    result = read_experiment_contract("demo", repo_root=tmp_path)

    assert result["ok"] is True
    assert result["prelaunch"]["ok"] is True
    assert result["summary"]["contract_path"] == "experiments/projects/demo/_runner/experiment_contract.json"
    assert result["summary"]["required_context_bundle"] == "lab_core_v1"
    assert result["summary"]["supported_runners"] == ["registry_batch", "registry_case"]
    assert result["summary"]["artifact_boundary"]["external_data_root"]["resolution_kind"] == "path"


def test_canonical_experiment_contract_regression_anchor_negative_validation_fences() -> None:
    contract = _base_contract()
    contract["compatibility"]["major"] = 2
    contract["launch"]["default_runner_id"] = "missing_runner"
    contract["control"]["pause_supported"] = False
    contract["control"]["intervention_actions"] = ["pause", "time_travel"]
    contract["resources"]["max_parallel_runs"] = 0
    contract["context_policy"]["required_context_bundle"] = ""
    contract["contract_hash"] = "sha256:" + ("0" * 64)

    result = validate_experiment_contract(contract)
    codes = {row["code"] for row in result["errors"]}

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_contract_validation_failed"
    assert "unknown_major_version" in codes
    assert "invalid_runner_declaration" in codes
    assert "invalid_control_policy" in codes
    assert "invalid_resource_policy" in codes
    assert "invalid_context_policy" in codes
    assert "invalid_contract_hash" in codes


def test_canonical_experiment_contract_regression_anchor_scheduler_policy_and_no_manifest_fallback(
    tmp_path: Path,
) -> None:
    contract = _base_contract()
    _write_contract(tmp_path, contract)
    _prepare_demo_project(tmp_path)

    saved = sched.validate_job_spec(
        {
            "job_id": "demo_contract_anchor_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": "2026-04-03T12:30:00Z", "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "registry_batch",
                "args": {"registry": "chemistry/demo.json"},
            },
        },
        repo_root=tmp_path,
    )

    assert saved["required_context_bundle"] == "lab_core_v1"
    assert saved["forbidden_context_refs"] == ["full_repo_dump", "legacy_transcript_blob"]
    assert saved["project_run"]["launch_resolution_source"] == "experiment_contract"
    assert saved["project_run"]["scheduler_policy"]["additional_context_refs_allowed"] is False

    with pytest.raises(ValueError, match="forbids additional context refs .*full_repo_dump"):
        sched.validate_job_spec(
            {
                "job_id": "demo_contract_anchor_forbidden_ref_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-03T12:31:00Z", "timezone": "UTC"},
                "context_refs": ["full_repo_dump"],
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "registry_batch",
                    "args": {"registry": "chemistry/demo.json"},
                },
            },
            repo_root=tmp_path,
        )

    broken = _base_contract()
    broken["launch"]["entrypoints"]["registry_batch"]["command"] = []
    broken["launch"]["entrypoints"]["registry_batch"]["working_dir"] = ""
    broken["contract_hash"] = validate_experiment_contract(broken)["computed_contract_hash"]
    _write_contract(tmp_path, broken)
    _write_manifest(tmp_path)

    with pytest.raises(ValueError) as excinfo:
        sched.validate_job_spec(
            {
                "job_id": "demo_contract_anchor_invalid_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-03T12:32:00Z", "timezone": "UTC"},
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "registry_batch",
                    "args": {"registry": "chemistry/demo.json"},
                },
            },
            repo_root=tmp_path,
        )

    message = str(excinfo.value)
    assert "experiment contract" in message
    assert "manifest" not in message


def test_removed_engine_is_not_an_implicit_project() -> None:
    result = read_experiment_contract("emergence")
    assert result["ok"] is False
    with pytest.raises(ValueError, match="experiment contract required"):
        project_runner_contract.resolve_project_runner("emergence", "registry_batch")
