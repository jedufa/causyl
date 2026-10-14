from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import job_shape_contract
from infra.controlplane import job_shape_relationship_contract as contract


def test_job_shape_relationship_contract_v1_exposes_expected_shape_binding() -> None:
    payload = contract.job_shape_relationship_contract_v1()

    assert payload.version == "job_shape_relationship_contract_v1"
    assert payload.uid == "PIS-20260406-053812Z-C277"
    assert payload.canonical_artifact_rel.as_posix() == "governance/job_shape_relationship_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_job_shape_parent_child_relationship_matrix_v1.md"
    assert payload.canonical_shape_contract_rel.as_posix() == "governance/canonical_job_shape_contract_v1.json"
    assert payload.shape_names == job_shape_contract.CANONICAL_JOB_SHAPES


def test_job_shape_relationship_helpers_report_current_and_future_edges() -> None:
    assert contract.legal_child_shapes_for_parent("fanout_job") == (
        "verification_loop_job",
        "experiment_run_job",
    )
    assert contract.legal_child_shapes_for_parent("campaign_job", include_future=True) == (
        "verification_loop_job",
        "fanout_job",
        "experiment_run_job",
    )
    assert contract.relationship_for_shapes("fanout_job", "verification_loop_job")["integrator_owner"] == "parent_verifier_loop"


def test_job_shape_relationship_scheduler_lookup_uses_current_job_kind_mapping() -> None:
    assert contract.parent_shape_for_job_kind("swarm_launch") == "fanout_job"
    assert contract.legal_child_shapes_for_job_kind("swarm_launch") == (
        "verification_loop_job",
        "experiment_run_job",
    )


def test_job_shape_relationship_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/job_shape_relationship_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_shape_contract_rel"] == contract.CANONICAL_SHAPE_CONTRACT_REL.as_posix()
    assert tuple(payload["shapes"].keys()) == job_shape_contract.CANONICAL_JOB_SHAPES
    assert payload["shapes"]["fanout_job"]["children"]["verification_loop_job"]["legality"] == "allowed"
    assert payload["shapes"]["fanout_job"]["children"]["fanout_job"]["legality"] == "forbidden"
    assert payload["shapes"]["delegation_tree_job"]["children"]["delegation_tree_job"]["legality"] == "future_only"


def test_job_shape_relationship_contract_validation_passes() -> None:
    assert contract.validate_job_shape_relationship_contract_v1() == ()


def test_job_shape_relationship_helpers_read_durable_contract_matrix(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/job_shape_relationship_contract_v1.json").read_text(encoding="utf-8"))
    payload["shapes"]["fanout_job"]["children"]["verification_loop_job"]["integrator_owner"] = "artifact_owner_only"
    payload["shapes"]["fanout_job"]["children"]["verification_loop_job"]["reintegration_boundary"] = "artifact_boundary_only"

    contract_path = tmp_path / "governance" / "job_shape_relationship_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    relation = contract.relationship_for_shapes("fanout_job", "verification_loop_job", repo_root=tmp_path)
    assert relation["integrator_owner"] == "artifact_owner_only"
    assert relation["reintegration_boundary"] == "artifact_boundary_only"


def test_job_shape_relationship_contract_validation_rejects_unknown_legality_value(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/job_shape_relationship_contract_v1.json").read_text(encoding="utf-8"))
    payload["shapes"]["fanout_job"]["children"]["verification_loop_job"]["legality"] = "launch_whenever"

    contract_path = tmp_path / "governance" / "job_shape_relationship_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    issues = contract.validate_job_shape_relationship_contract_v1(repo_root=tmp_path)
    assert "fanout_job -> verification_loop_job legality must be one of: allowed, future_only, forbidden" in issues


def test_job_shape_relationship_rejects_illegal_and_future_only_pairs_by_default() -> None:
    with pytest.raises(KeyError, match="illegal parent-child job-shape relationship in v1"):
        contract.assert_legal_parent_child_relationship("fanout_job", "fanout_job")

    with pytest.raises(ValueError, match="future-only parent-child relationship is not launchable in v1"):
        contract.assert_legal_parent_child_relationship("delegation_tree_job", "delegation_tree_job")

    contract.assert_legal_parent_child_relationship("campaign_job", "fanout_job", allow_future=True)
