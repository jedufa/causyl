from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments_shared import experiment_registry_path
from experiments_shared import write_experiment_scaffold
from experiments_shared.experiment_registry import REGISTRY_SCHEMA_VERSION
from experiments_shared import experiment_scaffold as scaffold_mod
from experiments_shared.experiment_scaffold import build_experiment_scaffold_plan
from experiments_shared.experiment_scaffold_seams import verify_experiment_contract_scaffold_seams


def test_scaffold_regression_anchor_dry_run_parity_and_registration_preview(tmp_path: Path) -> None:
    dry_run = write_experiment_scaffold(
        "demo",
        display_name="Demo Experiment",
        family_tags=["foundation"],
        dry_run=True,
        repo_root=tmp_path,
    )
    created = write_experiment_scaffold(
        "demo",
        display_name="Demo Experiment",
        family_tags=["foundation"],
        repo_root=tmp_path,
    )

    manifest_path = tmp_path / "experiments" / "projects" / "demo" / "_scaffold" / "scaffold_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    assert dry_run["planned_paths"] == created["written_paths"]
    assert dry_run["projection"] == created["projection"]
    assert dry_run["registration"]["action"] == "create"
    assert created["registration"]["status"] == "registered"
    assert dry_run["registration"]["record"] == created["registration"]["record"]
    assert manifest["discovery_registry_path"] == "experiments/registry/scaffolded_experiments.json"
    assert any(rule["rule_id"] == "artifact_boundary_confirmation" for rule in manifest["placeholder_completion_rules"])
    assert any(rule["rule_id"] == "lifecycle_run_record_placeholder" for rule in manifest["placeholder_completion_rules"])
    assert manifest["version_contract"]["current_version"] == "experiment_scaffold_v2"
    assert manifest["version_contract"]["overwrite_generated_requires_exact_version"] is True
    assert any(seam["runtime_filename"] == "run_record.json" for seam in manifest["lifecycle_seams"])
    placeholder_paths = {row["path"] for row in manifest["placeholder_files"]}
    assert "experiments/projects/demo/_runner/run_experiment.py" in placeholder_paths
    assert "experiments/projects/demo/_runner/experiment_contract.json" in placeholder_paths
    assert "experiments/projects/demo/_lifecycle/run_record.placeholder.json" in placeholder_paths
    assert all(row["completion_mode"] == "replace_or_fill_before_real_runs" for row in manifest["placeholder_files"])


def test_scaffold_regression_anchor_rejects_invalid_projection_inputs(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="default_profile"):
        build_experiment_scaffold_plan("demo", default_profile="Bad Profile", repo_root=tmp_path)
    with pytest.raises(ValueError, match="external_data_root"):
        build_experiment_scaffold_plan(
            "demo",
            external_data_root=str((tmp_path / "experiments" / "projects" / "demo-data").resolve()),
            repo_root=tmp_path,
        )


def test_scaffold_regression_anchor_duplicate_registration_conflict(tmp_path: Path) -> None:
    registry_path = experiment_registry_path(repo_root=tmp_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    registry_path.write_text(
        json.dumps(
            {
                "schema_version": REGISTRY_SCHEMA_VERSION,
                "records": [
                    {
                        "project_id": "demo",
                        "slug": "demo",
                        "display_name": "Wrong Demo",
                        "project_root": "experiments/projects/demo",
                        "contract_path": "experiments/projects/other/_runner/experiment_contract.json",
                        "scaffold_manifest_path": "experiments/projects/demo/_scaffold/scaffold_manifest.json",
                        "scaffold_version": "experiment_scaffold_v2",
                        "discovery_status": "registered",
                        "tags": [],
                        "family_tags": [],
                        "required_context_bundle": "lab_core_v1",
                        "resource_class": "cpu_light",
                        "analysis_recipe": "summary_report",
                        "external_data_root": "~/causyl_experiment_data/demo",
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


def test_scaffold_regression_anchor_partial_failure_rolls_back_project_and_registry(tmp_path: Path, monkeypatch) -> None:
    registry_path = experiment_registry_path(repo_root=tmp_path)
    registry_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_registry = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "records": [
            {
                "project_id": "baseline",
                "slug": "baseline",
                "display_name": "Baseline Demo",
                "project_root": "experiments/projects/baseline",
                "contract_path": "experiments/projects/baseline/_runner/experiment_contract.json",
                "scaffold_manifest_path": "experiments/projects/baseline/_scaffold/scaffold_manifest.json",
                "scaffold_version": "experiment_scaffold_v2",
                "discovery_status": "registered",
                "tags": [],
                "family_tags": [],
                "required_context_bundle": "lab_core_v1",
                "resource_class": "cpu_light",
                "analysis_recipe": "summary_report",
                "external_data_root": "~/causyl_experiment_data/baseline",
            }
        ],
    }
    registry_path.write_text(json.dumps(baseline_registry, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _boom(payload: dict[str, object], *, repo_root: Path | None = None) -> Path:
        raise RuntimeError("forced registry write failure")

    monkeypatch.setattr(scaffold_mod, "write_experiment_registry", _boom)

    with pytest.raises(RuntimeError, match="forced registry write failure"):
        scaffold_mod.write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    assert not (tmp_path / "experiments" / "projects" / "demo").exists()
    assert json.loads(registry_path.read_text(encoding="utf-8")) == baseline_registry


def test_scaffold_regression_anchor_tracks_only_metadata_safe_artifact_placeholders(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    project_root = tmp_path / "experiments" / "projects" / "demo"
    policy = json.loads((project_root / "_artifacts" / "data_root_policy.json").read_text(encoding="utf-8"))

    assert not (project_root / "_artifacts" / "runs").exists()
    assert not (project_root / "_artifacts" / "checkpoints").exists()
    assert not (project_root / "_artifacts" / "datasets").exists()
    assert policy["heavy_runtime_directories_created"] == []
    assert policy["external_data_root"] == "~/causyl_experiment_data/demo"


def test_scaffold_regression_anchor_accepts_v1_manifest_without_placeholder_files(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)

    manifest_path = tmp_path / "experiments" / "projects" / "demo" / "_scaffold" / "scaffold_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload.pop("placeholder_files", None)
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is True
