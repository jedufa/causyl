from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import job_shape_contract as contract


def test_job_shape_contract_v1_exposes_expected_allowlisted_shapes() -> None:
    payload = contract.canonical_job_shape_contract_v1()

    assert payload.version == "canonical_job_shape_contract_v1"
    assert payload.uid == "PIS-20260406-053811Z-8C0B"
    assert payload.canonical_artifact_rel.as_posix() == "governance/canonical_job_shape_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_canonical_job_shape_vocabulary_v1.md"
    assert payload.allowlisted_shape_names == (
        "verification_loop_job",
        "fanout_job",
        "campaign_job",
        "experiment_run_job",
        "delegation_tree_job",
    )
    assert payload.current_shape_names == (
        "verification_loop_job",
        "fanout_job",
        "experiment_run_job",
    )
    assert payload.future_facing_shape_names == ("campaign_job", "delegation_tree_job")


def test_job_shape_contract_rejects_unknown_or_legacy_aliases() -> None:
    for invalid_name in (
        "",
        "build_verify_loop",
        "swarm_launch",
        "project_runner",
        "campaign",
        "recursive_job",
    ):
        with pytest.raises(KeyError, match="unknown canonical job shape"):
            contract.canonical_job_shape_name(invalid_name)


def test_job_shape_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/canonical_job_shape_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["allowlisted_shape_names"]) == contract.CANONICAL_JOB_SHAPES
    assert tuple(payload["shapes"].keys()) == contract.CANONICAL_JOB_SHAPES
    assert payload["shapes"]["verification_loop_job"]["current_repo_mappings"] == [
        {"surface": "scheduled_jobs.job_kind", "value": "build_verify_loop"}
    ]
    assert payload["shapes"]["delegation_tree_job"]["future_facing"] is True
    assert payload["shapes"]["delegation_tree_job"]["recursive"] is True


def test_job_shape_contract_validation_passes() -> None:
    assert contract.validate_job_shape_contract_v1() == ()


def test_job_shape_contract_helpers_report_future_facing_and_current_mappings() -> None:
    assert contract.is_future_facing_job_shape("campaign_job") is True
    assert contract.is_future_facing_job_shape("verification_loop_job") is False
    assert contract.current_repo_mappings_for_shape("fanout_job") == ("scheduled_jobs.job_kind=swarm_launch",)
