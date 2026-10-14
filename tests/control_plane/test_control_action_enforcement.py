from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments_shared import canonicalize_experiment_contract
from experiments_shared import enforce_control_action_request
from experiments_shared import evaluate_control_action_request
from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from experiments_shared.experiment_records import build_initial_run_record
from experiments_shared.experiment_records import read_run_record
from experiments_shared.experiment_records import record_heartbeat
from experiments_shared.experiment_records import record_terminal
from experiments_shared.experiment_records import write_run_record
from experiments_shared.control_action_enforcement import ControlActionDeniedError


def _write_demo_contract_and_policy(
    repo_root: Path,
    *,
    policy_actions: dict | None = None,
) -> Path:
    project_root = repo_root / "experiments" / "projects" / "demo"
    external_data_root = repo_root.parent / f"{repo_root.name}_external_data" / "demo"
    runner_root = project_root / "_runner"
    runner_root.mkdir(parents=True, exist_ok=True)
    (project_root / "_config").mkdir(parents=True, exist_ok=True)
    (project_root / "_spec" / "registry" / "v1").mkdir(parents=True, exist_ok=True)
    (project_root / "runner.py").write_text("def main():\n    return 0\n", encoding="utf-8")
    contract = {
        "schema_version": "experiment_contract_v1",
        "contract_hash": "",
        "compatibility": {"contract_family": "experiment_contract", "major": 1, "minor": 0},
        "project": {"project_id": "demo", "display_name": "Demo", "owner": "causyl", "tags": ["demo"]},
        "launch": {
            "default_runner_id": "registry_batch",
            "supported_runners": ["registry_batch"],
            "entrypoints": {
                "registry_batch": {
                    "command": ["python", "-m", "experiments.projects.demo.runner"],
                    "working_dir": "experiments/projects/demo",
                    "env": {"PYTHONUNBUFFERED": "1"},
                }
            },
            "args": {"required": ["registry"], "optional": [], "types": {"registry": "path"}},
        },
        "configuration": {
            "profiles_root": "experiments/projects/demo/_config",
            "registry_root": "experiments/projects/demo/_spec/registry/v1",
            "override_modes": ["patch"],
            "default_profile": "baseline",
        },
        "observability": {
            "status_files": [{"path": "run_record.json", "type": "json", "priority": 1}],
            "progress": {"mode": "wrapper", "fields": ["status"]},
            "health_signals": ["exit_code"],
        },
        "control": {
            "pause_supported": True,
            "resume_supported": True,
            "stop_supported": True,
            "restart_supported": True,
            "checkpoint_supported": True,
            "branch_from_checkpoint_supported": True,
            "intervention_actions": [
                "graceful_stop",
                "force_stop",
                "pause",
                "resume",
                "restart",
                "checkpoint",
                "branch_from_checkpoint",
            ],
            "unsupported_actions": ["hot_parameter_mutation"],
        },
        "analysis": {"primary_artifacts": ["run_record.json"], "recipes": ["summary_report"], "default_recipe": "summary_report"},
        "resources": {
            "resource_class": "cpu_light",
            "project_semaphore": "cpu/demo",
            "max_parallel_runs": 1,
            "estimated_footprint": {"gpu_count": 0, "cpu_cores": 1, "ram_gb": 1},
        },
        "limitations": [],
        "artifacts": {
            "repo_tracked_outputs": ["run_record.json"],
            "external_data_root": str(external_data_root.resolve()),
            "artifact_manifest_required": False,
        },
        "context_policy": {"required_context_bundle": "lab_core_v1", "additional_context_refs_allowed": True, "forbidden_context_refs": []},
        "validation": {
            "schema_validation_required": True,
            "hash_validation_required": True,
            "prelaunch_checks": ["profiles_root_exists", "registry_root_exists", "runner_exists"],
        },
    }
    contract["contract_hash"] = validate_experiment_contract(contract)["computed_contract_hash"]
    contract = canonicalize_experiment_contract(contract)
    (runner_root / "experiment_contract.json").write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": policy_actions
        if policy_actions is not None
        else {
            "graceful_stop": {"policy_state": "enabled", "rollout_state": "live", "note": "default stop"},
            "force_stop": {"policy_state": "enabled", "rollout_state": "live", "note": "destructive"},
            "pause": {"policy_state": "enabled", "rollout_state": "live", "note": "checkpoint drain"},
            "resume": {"policy_state": "enabled", "rollout_state": "live", "note": "resume from checkpoint"},
            "restart": {"policy_state": "enabled", "rollout_state": "live", "note": "relaunch from durable anchor"},
            "checkpoint": {"policy_state": "enabled", "rollout_state": "live", "note": "capture boundary"},
            "branch_from_checkpoint": {"policy_state": "disabled", "rollout_state": "staged", "note": "storage review"},
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    (runner_root / "control_action_policy.json").write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    run_dir = repo_root / "wrapper" / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    initial = build_initial_run_record(
        run_id="run_001",
        job_id="job-001",
        job_spec_hash="sha256:job",
        project_id="demo",
        created_at_utc="2026-04-09T04:16:30Z",
        launch={"entry_module": "experiments.projects.demo.runner"},
    )
    write_run_record(run_dir, initial)
    record_heartbeat(run_dir, heartbeat_at_utc="2026-04-09T04:16:31Z", note="wrapper_started")
    return run_dir


def _set_checkpoint(run_dir: Path, *, checkpoint_id: str = "ckpt-001", count: int = 1) -> None:
    current = read_run_record(run_dir)
    updated = dict(current)
    updated["checkpoint"] = {
        **dict(current.get("checkpoint") or {}),
        "latest_checkpoint_id": checkpoint_id,
        "latest_checkpoint_generation": 1,
        "latest_checkpoint_relpath": "checkpoint_bundles_v2/bundle_gen000001",
        "checkpoint_count": count,
        "resume_status": "checkpointed",
    }
    write_run_record(run_dir, updated, previous=current, allow_repair=True)


def test_evaluate_control_action_request_accepts_running_graceful_stop(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)

    result = evaluate_control_action_request("graceful_stop", run_dir, repo_root=tmp_path)

    assert result["ok"] is True
    assert result["request_state"] == "requested"
    assert result["decision_state"] == "accepted"
    assert result["realization_state"] == "pending"
    assert result["reason_code"] == "accepted_pending_execution"
    assert result["identity"]["run_id"] == "run_001"
    assert result["lifecycle"]["state"] == "running"


def test_evaluate_control_action_request_denies_policy_disabled_action(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)
    _set_checkpoint(run_dir)

    result = evaluate_control_action_request("branch_from_checkpoint", run_dir, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["decision_state"] == "denied"
    assert result["reason_code"] == "action_disabled_by_policy"


def test_evaluate_control_action_request_denies_lifecycle_ineligible_action(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)

    result = evaluate_control_action_request("resume", run_dir, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["reason_code"] == "lifecycle_state_ineligible"
    assert "uncertain_interrupted" in result["reason_detail"]


def test_evaluate_control_action_request_denies_unknown_action(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)

    result = evaluate_control_action_request("time_travel", run_dir, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["reason_code"] == "unknown_control_action"


def test_evaluate_control_action_request_fails_closed_when_policy_missing(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)
    (tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json").unlink()

    result = evaluate_control_action_request("graceful_stop", run_dir, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["reason_code"] == "control_action_policy_not_found"


def test_evaluate_control_action_request_fails_closed_when_run_truth_missing(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(tmp_path)

    result = evaluate_control_action_request("graceful_stop", {"project_id": "demo"}, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["reason_code"] == "missing_run_lifecycle_truth"


def test_evaluate_control_action_request_accepts_resume_with_uncertain_interrupted_checkpoint(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)
    _set_checkpoint(run_dir)
    current = read_run_record(run_dir)
    updated = dict(current)
    updated["lifecycle"] = {
        **dict(current.get("lifecycle") or {}),
        "state": "uncertain_interrupted",
        "confidence": "uncertain",
        "terminal": False,
        "transition_reason": "restart_reconciliation_gap",
    }
    updated["checkpoint"] = {
        **dict(current.get("checkpoint") or {}),
        "resume_status": "checkpointed",
    }
    write_run_record(run_dir, updated, previous=current, allow_repair=True)

    result = evaluate_control_action_request("resume", run_dir, repo_root=tmp_path)

    assert result["ok"] is True
    assert result["decision_state"] == "accepted"
    assert result["lifecycle"]["state"] == "uncertain_interrupted"


def test_enforce_control_action_request_raises_on_denial(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)

    with pytest.raises(ControlActionDeniedError, match="resume denied: lifecycle_state_ineligible"):
        enforce_control_action_request("resume", run_dir, repo_root=tmp_path)


def test_evaluate_control_action_request_denies_restart_without_terminal_or_uncertain_state(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)

    result = evaluate_control_action_request("restart", run_dir, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["reason_code"] == "lifecycle_state_ineligible"


def test_evaluate_control_action_request_denies_stop_for_terminal_run(tmp_path: Path) -> None:
    run_dir = _write_demo_contract_and_policy(tmp_path)
    record_terminal(
        run_dir,
        status="complete",
        exit_code=0,
        ended_at_utc="2026-04-09T04:17:00Z",
        transition_reason="wrapper_terminal_update",
    )

    result = evaluate_control_action_request("force_stop", run_dir, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["reason_code"] == "run_already_terminal"
