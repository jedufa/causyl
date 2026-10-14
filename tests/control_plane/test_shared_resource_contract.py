from __future__ import annotations

import pytest

from experiments_shared import SharedResourceContractError
from experiments_shared import canonical_limit_semantics
from experiments_shared import map_experiment_resource_policy
from experiments_shared import map_internal_scheduler_resources
from experiments_shared import map_runtime_policy_resource_caps
from experiments_shared import map_shared_resource_keys
from experiments_shared import parse_scheduler_resource_class_caps
from experiments_shared import workspace_resource_key


def test_map_experiment_resource_policy_projects_canonical_contract() -> None:
    result = map_experiment_resource_policy(
        {
            "resource_class": "gpu_heavy",
            "project_semaphore": "gpu/demo",
            "max_parallel_runs": 2,
        }
    )

    assert result["resource_classification"]["resource_class"] == "job_family"
    assert result["resource_classification"]["canonical_key"] == "job_family:scheduler:gpu_heavy"
    assert result["claim_keys"] == [
        {
            "resource_class": "project",
            "scope": "gpu",
            "locator": "demo",
            "canonical_key": "project:gpu:demo",
        }
    ]
    assert result["limit_contracts"] == [
        {
            "resource": {
                "resource_class": "project",
                "scope": "gpu",
                "locator": "demo",
                "canonical_key": "project:gpu:demo",
            },
            "limit_kind": "max_concurrent",
            "value": 2,
        }
    ]


def test_parse_scheduler_resource_class_caps_rejects_ambiguous_entries() -> None:
    with pytest.raises(
        SharedResourceContractError,
        match="scheduler.resource_class_caps entries must use <resource_class>:<limit>",
    ):
        parse_scheduler_resource_class_caps(["gpu_heavy"])


def test_map_runtime_policy_resource_caps_projects_scheduler_caps_into_limit_contracts() -> None:
    result = map_runtime_policy_resource_caps(["gpu_heavy:2", "cpu_light=4"])

    assert result == {
        "claim_keys": [],
        "limit_contracts": [
            {
                "resource": {
                    "resource_class": "job_family",
                    "scope": "scheduler",
                    "locator": "gpu_heavy",
                    "canonical_key": "job_family:scheduler:gpu_heavy",
                },
                "limit_kind": "max_concurrent",
                "value": 2,
            },
            {
                "resource": {
                    "resource_class": "job_family",
                    "scope": "scheduler",
                    "locator": "cpu_light",
                    "canonical_key": "job_family:scheduler:cpu_light",
                },
                "limit_kind": "max_concurrent",
                "value": 4,
            },
        ],
        "source_fields": {
            "limit_contracts": ["scheduler.resource_class_caps"],
        },
    }


def test_workspace_resource_key_rejects_unsafe_paths() -> None:
    with pytest.raises(SharedResourceContractError, match="repo-relative"):
        workspace_resource_key("/tmp/secret.txt")
    with pytest.raises(SharedResourceContractError, match="must not contain empty, dot, or parent segments"):
        workspace_resource_key("../runtime/state/task_register.json")


def test_map_shared_resource_keys_normalizes_repo_paths() -> None:
    result = map_shared_resource_keys(["infra/webui/server_lab_assistant_task_register.py"])

    assert result == [
        {
            "resource_class": "workspace",
            "scope": "repo",
            "locator": "infra/webui/server_lab_assistant_task_register.py",
            "canonical_key": "workspace:repo:infra/webui/server_lab_assistant_task_register.py",
        }
    ]


def test_map_internal_scheduler_resources_projects_mutex_claims_for_live_shared_surfaces() -> None:
    result = map_internal_scheduler_resources(
        shared_resource_keys=["runtime/state/lab_assistant/task_register/task_register.json"],
        host_ids=["primary"],
        model_providers=["openai"],
        devices=[{"device_class": "gpu", "device_id": "a100"}],
    )

    assert result["claim_keys"] == [
        {
            "resource_class": "host",
            "scope": "global",
            "locator": "primary",
            "canonical_key": "host:global:primary",
        },
        {
            "resource_class": "model_provider",
            "scope": "provider",
            "locator": "openai",
            "canonical_key": "model_provider:provider:openai",
        },
        {
            "resource_class": "device",
            "scope": "gpu",
            "locator": "a100",
            "canonical_key": "device:gpu:a100",
        },
        {
            "resource_class": "workspace",
            "scope": "repo",
            "locator": "runtime/state/lab_assistant/task_register/task_register.json",
            "canonical_key": "workspace:repo:runtime/state/lab_assistant/task_register/task_register.json",
        },
    ]
    assert [row["limit_kind"] for row in result["limit_contracts"]] == [
        "mutex",
        "mutex",
        "mutex",
        "mutex",
    ]
    assert result["limit_semantics"] == canonical_limit_semantics()


def test_map_internal_scheduler_resources_rejects_unsafe_internal_workspace_aliases() -> None:
    with pytest.raises(SharedResourceContractError, match="must not contain empty, dot, or parent segments"):
        map_internal_scheduler_resources(shared_resource_keys=["../runtime/state/task_register.json"])


def test_canonical_limit_semantics_cover_counted_and_mutex_claims() -> None:
    assert canonical_limit_semantics() == [
        {
            "limit_kind": "max_concurrent",
            "value_contract": "positive_integer",
            "conflict_semantics": "counted_claims_block_when_limit_reached",
        },
        {
            "limit_kind": "mutex",
            "value_contract": "fixed_one",
            "conflict_semantics": "mutually_exclusive_claims",
        },
    ]
