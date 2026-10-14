from __future__ import annotations

import json
from pathlib import Path

from experiments_shared import refresh_experiment_control_policy_cache
from experiments_shared import resolve_experiment_control_policy
from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from experiments_shared.experiment_records import build_initial_run_record
from experiments_shared.experiment_records import write_run_record


def _write_demo_contract_and_policy(
    repo_root: Path,
    *,
    policy_actions: dict | None = None,
) -> None:
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
            "restart_supported": False,
            "checkpoint_supported": False,
            "branch_from_checkpoint_supported": True,
            "intervention_actions": ["graceful_stop", "pause", "resume", "branch_from_checkpoint"],
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
    (runner_root / "experiment_contract.json").write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": policy_actions
        if policy_actions is not None
        else {
            "pause": {"policy_state": "enabled", "rollout_state": "live", "note": "safe"},
            "branch_from_checkpoint": {"policy_state": "disabled", "rollout_state": "staged", "note": "storage review"},
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    (runner_root / "control_action_policy.json").write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def test_resolve_experiment_control_policy_from_run_record_path_builds_compact_summary(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(tmp_path)
    run_dir = tmp_path / "wrapper" / "run_001"
    run_dir.mkdir(parents=True, exist_ok=True)
    write_run_record(
        run_dir,
        build_initial_run_record(
            run_id="run_001",
            job_id="job-001",
            job_spec_hash="sha256:job",
            project_id="demo",
            created_at_utc="2026-04-09T04:16:30Z",
        ),
    )

    result = resolve_experiment_control_policy(run_dir, repo_root=tmp_path)

    assert result["ok"] is True
    assert result["identity"]["resolution_source"] == "run_record"
    assert result["control_policy_summary"]["headline"] == "3 available, 1 blocked, 1 unsupported"
    assert result["control_policy_summary"]["available_actions"] == ["graceful_stop", "pause", "resume"]
    assert result["control_policy_summary"]["staged_actions"] == ["branch_from_checkpoint"]


def test_resolve_experiment_control_policy_fails_closed_when_overlay_missing(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(tmp_path)
    (tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json").unlink()

    result = resolve_experiment_control_policy({"project_run": {"project_id": "demo"}}, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_control_policy_read_failed"
    assert result["errors"][0]["code"] == "control_action_policy_not_found"


def test_resolve_experiment_control_policy_reports_unresolved_identity_stably(tmp_path: Path) -> None:
    result = resolve_experiment_control_policy({}, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_control_policy_resolution_failed"
    assert result["errors"][0]["code"] == "experiment_identity_unresolved"


def test_resolve_experiment_control_policy_reports_contradictory_overlay(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(
        tmp_path,
        policy_actions={
            "time_travel": {"policy_state": "disabled", "rollout_state": "hidden", "note": "no"},
        },
    )

    result = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_class"] == "experiment_control_policy_conflict"
    assert result["errors"][0]["code"] == "unknown_control_action"


def test_resolve_experiment_control_policy_marks_cached_snapshot_stale_until_refresh(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(tmp_path)

    first = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)
    assert first["ok"] is True
    assert first["policy_refresh_state"]["status"] == "fresh"

    policy_path = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["actions"]["pause"]["policy_state"] = "disabled"
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    second = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    assert second["ok"] is True
    assert second["policy_refresh_state"]["stale"] is True
    assert second["policy_refresh_state"]["status"] == "stale_snapshot"
    assert second["policy_refresh_state"]["stale_reason"] == "control_action_policy_changed_since_last_refresh"
    assert "pause" in second["control_policy_summary"]["available_actions"]


def test_refresh_experiment_control_policy_cache_reloads_cached_project_after_policy_change(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(tmp_path)
    resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    policy_path = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["actions"]["pause"]["policy_state"] = "disabled"
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    refresh = refresh_experiment_control_policy_cache(repo_root=tmp_path)
    result = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    assert refresh["ok"] is True
    assert refresh["refreshed_projects"][0]["project_id"] == "demo"
    assert result["policy_refresh_state"]["stale"] is False
    assert result["policy_refresh_state"]["status"] == "fresh"
    assert "pause" in result["control_policy_summary"]["blocked_actions"]


def test_refresh_experiment_control_policy_cache_retains_last_known_good_on_invalid_reload(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(tmp_path)
    baseline = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    policy_path = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["actions"]["pause"]["policy_state"] = "broken"
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    refresh = refresh_experiment_control_policy_cache(repo_root=tmp_path)
    result = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    assert baseline["control_policy_summary"]["available_actions"] == result["control_policy_summary"]["available_actions"]
    assert refresh["ok"] is False
    assert refresh["refreshed_projects"][0]["status"] == "reload_failed"
    assert result["policy_refresh_state"]["stale"] is True
    assert result["policy_refresh_state"]["status"] == "reload_failed"
    assert result["warnings"][-1]["code"] == "control_action_policy_refresh_failed_last_known_good_retained"


def test_resolve_experiment_control_policy_uses_snapshot_override_for_in_flight_work(tmp_path: Path) -> None:
    _write_demo_contract_and_policy(tmp_path)
    snapshot = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    policy_path = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["actions"]["pause"]["policy_state"] = "disabled"
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    refresh_experiment_control_policy_cache(repo_root=tmp_path)

    in_flight = resolve_experiment_control_policy(
        {"project_id": "demo", "control_action_policy_snapshot": snapshot},
        repo_root=tmp_path,
    )
    fresh = resolve_experiment_control_policy({"project_id": "demo"}, repo_root=tmp_path)

    assert in_flight["policy_refresh_state"]["status"] == "snapshot_override"
    assert in_flight["policy_refresh_state"]["effective_scope"] == "in_flight_work_keeps_prior_policy_snapshot"
    assert "pause" in in_flight["control_policy_summary"]["available_actions"]
    assert "pause" in fresh["control_policy_summary"]["blocked_actions"]
