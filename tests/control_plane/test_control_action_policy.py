from __future__ import annotations

import json
from pathlib import Path

from experiments_shared import canonicalize_control_action_policy
from experiments_shared import control_action_policy_path
from experiments_shared import resolve_effective_control_action_policy
from experiments_shared import validate_control_action_policy
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
    return validate_experiment_contract(contract)["normalized_contract"]


def _base_policy() -> dict:
    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": {
            "branch_from_checkpoint": {
                "policy_state": "disabled",
                "rollout_state": "staged",
                "note": "blocked pending storage review",
            },
            "pause": {
                "policy_state": "enabled",
                "rollout_state": "live",
                "note": "safe during wrapper checkpoints",
            },
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    return policy


def test_control_action_policy_canonicalization_is_stable() -> None:
    policy_a = _base_policy()
    policy_b = {
        "project_id": "demo",
        "policy_hash": "",
        "schema_version": "control_action_policy_v1",
        "actions": {
            "pause": {
                "note": "safe during wrapper checkpoints",
                "rollout_state": "live",
                "policy_state": "enabled",
            },
            "branch_from_checkpoint": {
                "rollout_state": "staged",
                "policy_state": "disabled",
                "note": "blocked pending storage review",
            },
        },
    }
    policy_b["policy_hash"] = validate_control_action_policy(policy_b)["computed_policy_hash"]

    result_a = validate_control_action_policy(policy_a)
    result_b = validate_control_action_policy(policy_b)

    assert result_a["ok"] is True
    assert result_b["ok"] is True
    assert result_a["computed_policy_hash"] == result_b["computed_policy_hash"]
    assert canonicalize_control_action_policy(policy_a) == canonicalize_control_action_policy(policy_b)


def test_control_action_policy_rejects_invalid_state_and_project_mismatch() -> None:
    policy = _base_policy()
    policy["project_id"] = "other"
    policy["actions"]["pause"]["policy_state"] = "sometimes"
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = validate_control_action_policy(policy, expected_project_id="demo")
    messages = {row["message"] for row in result["errors"]}

    assert result["ok"] is False
    assert "project_id mismatch: expected demo" in messages
    assert "actions.pause.policy_state must be one of: disabled, enabled, unsupported" in messages


def test_control_action_policy_rejects_non_string_note() -> None:
    policy = _base_policy()
    policy["actions"]["pause"]["note"] = {"reason": "not a string"}
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = validate_control_action_policy(policy)

    assert result["ok"] is False
    assert any("actions.pause.note must be a string" == row["message"] for row in result["errors"])


def test_control_action_policy_rejects_unknown_fields_and_non_object_entries() -> None:
    policy = _base_policy()
    policy["owner_comment"] = "free-form drift"
    policy["actions"]["pause"]["extra"] = "nope"
    policy["actions"]["resume"] = "enabled"
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = validate_control_action_policy(policy)
    messages = {row["message"] for row in result["errors"]}

    assert result["ok"] is False
    assert "unknown top-level control action policy field: owner_comment" in messages
    assert "actions.pause contains unsupported field: extra" in messages
    assert "actions.resume must be an object" in messages


def test_control_action_policy_rejects_non_object_actions_container() -> None:
    policy = _base_policy()
    policy["actions"] = ["pause"]
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = validate_control_action_policy(policy)

    assert result["ok"] is False
    assert any("actions must be an object keyed by control action id" == row["message"] for row in result["errors"])


def test_control_action_policy_defaults_optional_fields_deterministically() -> None:
    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": {
            "pause": {
                "policy_state": "disabled",
            },
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = validate_control_action_policy(policy)
    normalized = result["normalized_policy"]["actions"]["pause"]

    assert result["ok"] is True
    assert normalized["rollout_state"] == "live"
    assert normalized["note"] == ""
    assert canonicalize_control_action_policy(policy)["actions"]["pause"]["rollout_state"] == "live"
    assert canonicalize_control_action_policy(policy)["actions"]["pause"]["note"] == ""


def test_control_action_policy_rejects_blank_action_ids() -> None:
    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": {
            "   ": {
                "policy_state": "disabled",
                "note": "no anonymous toggles",
            },
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = validate_control_action_policy(policy)

    assert result["ok"] is False
    assert any("actions keys must be non-empty control action ids" == row["message"] for row in result["errors"])


def test_resolve_effective_control_action_policy_merges_overlay_and_defaults() -> None:
    contract = _base_contract()
    policy = _base_policy()

    result = resolve_effective_control_action_policy(contract, policy)
    effective = result["effective_policy"]["actions"]

    assert result["ok"] is True
    assert effective["pause"]["effective_state"] == "enabled"
    assert effective["pause"]["policy_source"] == "overlay"
    assert effective["branch_from_checkpoint"]["effective_state"] == "disabled"
    assert effective["branch_from_checkpoint"]["rollout_state"] == "staged"
    assert effective["resume"]["effective_state"] == "enabled"
    assert effective["resume"]["policy_source"] == "contract_default"
    assert effective["hot_parameter_mutation"]["effective_state"] == "unsupported"


def test_resolve_effective_control_action_policy_rejects_unknown_action() -> None:
    contract = _base_contract()
    policy = _base_policy()
    policy["actions"]["time_travel"] = {
        "policy_state": "disabled",
        "rollout_state": "hidden",
        "note": "physics remains stubborn",
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = resolve_effective_control_action_policy(contract, policy)

    assert result["ok"] is False
    assert any("control action is not declared by experiment_contract.json: time_travel" == row["message"] for row in result["errors"])


def test_resolve_effective_control_action_policy_rejects_overlay_reenable_of_contract_unsupported_action() -> None:
    contract = _base_contract()
    policy = _base_policy()
    policy["actions"]["hot_parameter_mutation"] = {
        "policy_state": "enabled",
        "rollout_state": "live",
        "note": "absolutely not",
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]

    result = resolve_effective_control_action_policy(contract, policy)
    effective = result["effective_policy"]["actions"]["hot_parameter_mutation"]

    assert result["ok"] is False
    assert effective["effective_state"] == "unsupported"
    assert any("overlay cannot enable or stage contract-unsupported action: hot_parameter_mutation" == row["message"] for row in result["errors"])


def test_control_action_policy_path_points_to_runner_overlay_file(tmp_path: Path) -> None:
    path = control_action_policy_path("demo", repo_root=tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_base_policy(), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert path == tmp_path / "experiments" / "projects" / "demo" / "_runner" / "control_action_policy.json"
