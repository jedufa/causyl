from __future__ import annotations

import json
from pathlib import Path

from experiments_shared import experiment_registry_path
from experiments_shared import write_experiment_scaffold
from experiments_shared.experiment_registry import REGISTRY_SCHEMA_VERSION
from experiments_shared.experiment_scaffold_seams import verify_experiment_contract_scaffold_seams


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "experiment_scaffold"


def _proof_matrix_summary_fixture() -> dict[str, object]:
    return json.loads((FIXTURE_ROOT / "proof_matrix_summary_v1.json").read_text(encoding="utf-8"))


def _proof_matrix_summary(repo_root: Path) -> dict[str, object]:
    dry_run = write_experiment_scaffold(
        "demo",
        display_name="Demo Experiment",
        family_tags=["foundation"],
        dry_run=True,
        repo_root=repo_root,
    )
    created = write_experiment_scaffold(
        "demo",
        display_name="Demo Experiment",
        family_tags=["foundation"],
        repo_root=repo_root,
    )
    seam = verify_experiment_contract_scaffold_seams("demo", repo_root=repo_root)

    manifest = json.loads(
        (repo_root / "experiments" / "projects" / "demo" / "_scaffold" / "scaffold_manifest.json").read_text(
            encoding="utf-8"
        )
    )
    registry_record = json.loads(
        (repo_root / "experiments" / "registry" / "scaffolded_experiments.json").read_text(encoding="utf-8")
    )["records"][0]

    return {
        "dry_run_planned_paths": dry_run["planned_paths"],
        "created_written_paths": created["written_paths"],
        "registration_action": dry_run["registration"]["action"],
        "registration_status": created["registration"]["status"],
        "registry_record": {
            "project_id": registry_record["project_id"],
            "scaffold_version": registry_record["scaffold_version"],
            "required_context_bundle": registry_record["required_context_bundle"],
            "artifact_boundary": registry_record["artifact_boundary"],
            "compatibility_metadata": registry_record["compatibility_metadata"],
        },
        "manifest_summary": {
            "schema_version": manifest["schema_version"],
            "scaffold_version": manifest["scaffold_version"],
            "discovery_registry_path": manifest["discovery_registry_path"],
            "placeholder_rule_ids": [row["rule_id"] for row in manifest["placeholder_completion_rules"]],
            "placeholder_paths": [row["path"] for row in manifest["placeholder_files"]],
            "lifecycle_seams": manifest["lifecycle_seams"],
            "reserved_paths": manifest["reserved_paths"],
        },
        "seam_semantic_truth": seam["semantic_truth"],
    }


def test_experiment_scaffold_proof_matrix_matches_golden_fixture(tmp_path: Path) -> None:
    summary = _proof_matrix_summary(tmp_path)

    assert summary["dry_run_planned_paths"] == summary["created_written_paths"]
    assert summary == _proof_matrix_summary_fixture()


def test_experiment_scaffold_proof_matrix_rejects_repeat_create_collisions(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    result = write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    assert result["ok"] is False
    assert result["status"] == "collision"
    assert any(row["type"] == "project_root_not_empty" for row in result["collisions"])
    assert any(row["type"] == "file_exists" for row in result["collisions"])


def test_experiment_scaffold_proof_matrix_rejects_duplicate_registration_conflicts(tmp_path: Path) -> None:
    registry_path = experiment_registry_path(repo_root=tmp_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "records": [
                    {
                        "project_id": "demo",
                        "contract_path": "experiments/projects/other/_runner/experiment_contract.json",
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    result = write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    assert result["ok"] is False
    assert result["status"] == "collision"
    assert any(row["type"] == "duplicate_project_id_conflict" for row in result["collisions"])


def test_experiment_scaffold_proof_matrix_detects_artifact_boundary_and_compatibility_drift(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    registry_path = tmp_path / "experiments" / "registry" / "scaffolded_experiments.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["records"][0]["artifact_boundary"]["reserved_runtime_roots"] = [
        "experiments/projects/demo/_artifacts/proofs"
    ]
    payload["records"][0]["compatibility_metadata"]["contract_anchors"]["run_lifecycle_contract_path"] = "docs/contracts/wrong.md"
    registry_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any(row["field"] == "registry.artifact_boundary.reserved_runtime_roots" for row in result["errors"])
    assert any(
        row["field"] == "registry.compatibility_metadata.contract_anchors.run_lifecycle_contract_path"
        for row in result["errors"]
    )


def test_experiment_scaffold_proof_matrix_detects_lifecycle_placeholder_drift(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    manifest_path = tmp_path / "experiments" / "projects" / "demo" / "_scaffold" / "scaffold_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["lifecycle_seams"][0]["placeholder_path"] = "experiments/projects/demo/_lifecycle/wrong.placeholder.json"
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any(
        row["field"] == "manifest.lifecycle_seams[run_record].placeholder_path" for row in result["errors"]
    )


def test_experiment_scaffold_proof_matrix_detects_reserved_artifact_boundary_overlap(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    manifest_path = tmp_path / "experiments" / "projects" / "demo" / "_scaffold" / "scaffold_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["generated_files"].append(
        {
            "path": "experiments/projects/demo/_artifacts/proofs",
            "kind": "runtime_directory",
            "ownership": "downstream_runtime_owned",
            "purpose": "incorrectly tracked runtime path",
        }
    )
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any(row["field"] == "manifest.reserved_paths" for row in result["errors"])
