from __future__ import annotations

import json
from pathlib import Path

from experiments_shared import write_experiment_scaffold
from experiments_shared.experiment_scaffold_seams import build_experiment_bootstrap_walkthrough
from experiments_shared.experiment_scaffold_seams import verify_experiment_contract_scaffold_seams
from tools.bin import experiment_bootstrap_walkthrough
from tools.bin import verify_experiment_scaffold_seams


def test_verify_experiment_contract_scaffold_seams_happy_path(tmp_path: Path) -> None:
    write_experiment_scaffold(
        "demo",
        display_name="Demo Experiment",
        family_tags=["foundation"],
        repo_root=tmp_path,
    )

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is True
    assert result["semantic_truth"]["canonical_contract"] == "experiments/projects/demo/_runner/experiment_contract.json"
    assert result["semantic_truth"]["structural_layout_manifest"] == "experiments/projects/demo/_scaffold/scaffold_manifest.json"
    assert result["semantic_truth"]["discovery_registry"] == "experiments/registry/scaffolded_experiments.json"
    assert result["semantic_truth"]["lifecycle_placeholder_pack"] == "experiments/projects/demo/_lifecycle/README.md"
    assert "experiments/projects/demo/_runner/experiment_contract.json" in result["ownership_summary"]["operator_owned"]
    assert "experiments/projects/demo/_scaffold/scaffold_manifest.json" in result["ownership_summary"]["generator_owned"]


def test_verify_experiment_contract_scaffold_seams_detects_registry_contract_path_drift(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)
    registry_path = tmp_path / "experiments" / "registry" / "scaffolded_experiments.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["records"][0]["contract_path"] = "experiments/projects/demo/_runner/wrong.json"
    registry_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any(row["field"] == "registry.contract_path" for row in result["errors"])


def test_verify_experiment_contract_scaffold_seams_detects_missing_registry_compatibility_metadata(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)
    registry_path = tmp_path / "experiments" / "registry" / "scaffolded_experiments.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["records"][0].pop("compatibility_metadata", None)
    registry_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any(row["field"] == "registry.compatibility_metadata" for row in result["errors"])


def test_verify_experiment_contract_scaffold_seams_detects_artifact_boundary_policy_path_drift(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)
    registry_path = tmp_path / "experiments" / "registry" / "scaffolded_experiments.json"
    payload = json.loads(registry_path.read_text(encoding="utf-8"))
    payload["records"][0]["artifact_boundary"]["policy_path"] = "experiments/projects/demo/_artifacts/wrong.json"
    registry_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any(row["field"] == "registry.artifact_boundary.policy_path" for row in result["errors"])


def test_verify_experiment_contract_scaffold_seams_detects_missing_lifecycle_placeholder_pack(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)
    manifest_path = tmp_path / "experiments" / "projects" / "demo" / "_scaffold" / "scaffold_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["lifecycle_seams"] = [row for row in payload["lifecycle_seams"] if row["seam_id"] != "run_record"]
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any("missing required lifecycle seam: run_record" in row["message"] for row in result["errors"])


def test_verify_experiment_contract_scaffold_seams_detects_missing_placeholder_inventory_entry(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)
    manifest_path = tmp_path / "experiments" / "projects" / "demo" / "_scaffold" / "scaffold_manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    payload["placeholder_files"] = [
        row
        for row in payload["placeholder_files"]
        if row["path"] != "experiments/projects/demo/_runner/run_experiment.py"
    ]
    manifest_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    result = verify_experiment_contract_scaffold_seams("demo", repo_root=tmp_path)

    assert result["ok"] is False
    assert any("placeholder_files is missing required paths" in row["message"] for row in result["errors"])


def test_build_experiment_bootstrap_walkthrough_create_mode_runs_end_to_end(tmp_path: Path) -> None:
    result = build_experiment_bootstrap_walkthrough(
        "demo",
        display_name="Demo Experiment",
        family_tags=["foundation"],
        create=True,
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert [step["step_id"] for step in result["steps"]] == [
        "scaffold_generation",
        "discovery_resolution",
        "canonical_contract_read",
        "ownership_boundary_verification",
    ]
    assert result["steps"][0]["status"] == "created"
    assert result["steps"][3]["ok"] is True
    assert result["walkthrough_doc_path"] == "docs/10_operator/manuals/Experiment_Bootstrap_Walkthrough.html"
    assert result["migration_notes_path"] == "docs/60_reports/Experiment_Definition_Foundation_Migration_Notes.html"


def test_experiment_bootstrap_walkthrough_cli_and_seam_cli(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(experiment_bootstrap_walkthrough, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(verify_experiment_scaffold_seams, "REPO_ROOT", tmp_path)

    rc = experiment_bootstrap_walkthrough.main([
        "--project-id",
        "demo",
        "--display-name",
        "Demo Experiment",
        "--family-tag",
        "foundation",
        "--create",
    ])
    walkthrough = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert walkthrough["ok"] is True
    assert walkthrough["steps"][0]["status"] == "created"

    rc = verify_experiment_scaffold_seams.main(["--project-id", "demo"])
    seam = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert seam["ok"] is True
    assert seam["seam_contract_path"] == "docs/20_contracts/experiments/Contract_Experiment_Contract_Scaffold_Seams_V1.html"
