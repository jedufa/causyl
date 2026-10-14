from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from experiments_shared import EXPERIMENT_DISCOVERY_REGISTRY_PATH
from experiments_shared import list_registered_experiments
from experiments_shared import read_experiment_contract
from experiments_shared import resolve_registered_experiment
from experiments_shared import validate_experiment_contract
from experiments_shared.experiment_scaffold import build_experiment_scaffold_plan
from experiments_shared.experiment_scaffold import write_experiment_scaffold
from tools.bin import create_experiment_scaffold
from tools.bin import list_scaffolded_experiments


FIXTURE_ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "experiment_scaffold"


def _expected_tree() -> list[str]:
    return [
        line.strip()
        for line in (FIXTURE_ROOT / "expected_tree_v1.txt").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_build_experiment_scaffold_plan_matches_golden_tree_and_is_deterministic() -> None:
    plan_a = build_experiment_scaffold_plan("demo", display_name="Demo Experiment")
    plan_b = build_experiment_scaffold_plan("demo", display_name="Demo Experiment")

    expected = _expected_tree()
    actual_a = [str(row["path"]) for row in list(plan_a["files"])]
    actual_b = [str(row["path"]) for row in list(plan_b["files"])]

    assert actual_a == expected
    assert actual_b == expected
    assert json.dumps(plan_a, sort_keys=True) == json.dumps(plan_b, sort_keys=True)
    assert plan_a["registry_path"] == EXPERIMENT_DISCOVERY_REGISTRY_PATH


def test_build_experiment_scaffold_plan_projects_custom_inputs_into_generated_notes() -> None:
    plan = build_experiment_scaffold_plan(
        "demo",
        display_name="Demo Experiment",
        default_profile="baseline",
        analysis_recipe="bootstrap_summary",
        required_context_bundle="experiment_demo_core_v1",
        resource_class="gpu_heavy",
        external_data_root="/var/tmp/demo-data",
    )

    files_by_path = {str(row["path"]): str(row["content"]) for row in list(plan["files"])}
    readme = files_by_path["experiments/projects/demo/README.md"]
    operator_notes = files_by_path["experiments/projects/demo/_docs/operator_notes.md"]

    assert "_spec/registry/v1/baseline.yaml" in readme
    assert "resource class defaults to `gpu_heavy`" in operator_notes
    assert "project semaphore defaults to `gpu/demo`" in operator_notes
    assert "required context bundle defaults to `experiment_demo_core_v1`" in operator_notes
    assert "external data root placeholder: `/var/tmp/demo-data`" in operator_notes
    assert "starter profile placeholder id: `baseline`" in operator_notes


def test_write_experiment_scaffold_generates_schema_valid_contract_stub_pack_and_registration(tmp_path: Path) -> None:
    result = write_experiment_scaffold(
        "demo",
        display_name="Demo Experiment",
        owner="causyl",
        tags=["demo", "bootstrap"],
        family_tags=["foundation"],
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert result["status"] == "created"
    assert result["registration"]["status"] == "registered"
    assert result["registration"]["written_path"] == EXPERIMENT_DISCOVERY_REGISTRY_PATH
    assert (tmp_path / "experiments" / "projects" / "demo" / "README.md").exists()
    assert (tmp_path / "experiments" / "projects" / "demo" / "_docs" / "operator_notes.md").exists()
    assert (tmp_path / "experiments" / "projects" / "demo" / "analysis" / "summary_report.md").exists()
    assert (tmp_path / "experiments" / "projects" / "demo" / "_artifacts" / "data_root_policy.json").exists()
    assert (tmp_path / "experiments" / "projects" / "demo" / "_lifecycle" / "run_record.placeholder.json").exists()
    assert (tmp_path / "experiments" / "projects" / "demo" / "_lifecycle" / "run_record.interventions.placeholder.jsonl").exists()
    assert (tmp_path / "experiments" / "projects" / "demo" / "_lifecycle" / "checkpoint_bundles_v2" / "README.md").exists()

    contract_result = read_experiment_contract("demo", repo_root=tmp_path)
    assert contract_result["ok"] is True
    assert contract_result["summary"]["project_id"] == "demo"
    assert contract_result["summary"]["required_context_bundle"] == "lab_core_v1"
    assert contract_result["summary"]["supported_runners"] == ["default"]
    assert any(
        "not launch-ready" in item
        for item in list((contract_result["contract"] or {}).get("limitations") or [])
    )
    validation = validate_experiment_contract(contract_result["contract"])
    assert validation["ok"] is True

    listed = list_registered_experiments(repo_root=tmp_path)
    assert [row["project_id"] for row in listed] == ["demo"]
    assert listed[0]["family_tags"] == ["foundation"]
    assert listed[0]["scaffold_version"] == "experiment_scaffold_v2"
    assert listed[0]["artifact_boundary_policy_exists"] is True
    assert listed[0]["artifact_boundary"]["policy_path"] == "experiments/projects/demo/_artifacts/data_root_policy.json"
    assert listed[0]["artifact_boundary"]["external_data_root"] == "~/causyl_experiment_data/demo"
    assert listed[0]["compatibility_metadata"]["scaffold_version"] == "experiment_scaffold_v2"
    assert listed[0]["compatibility_metadata"]["upgrade_readiness"] == "unknown"
    assert listed[0]["compatibility_metadata"]["contract_anchors"]["canonical_contract_path"] == (
        "experiments/projects/demo/_runner/experiment_contract.json"
    )
    resolved = resolve_registered_experiment("demo", repo_root=tmp_path)
    assert resolved["ok"] is True
    assert resolved["record"]["contract_path"] == "experiments/projects/demo/_runner/experiment_contract.json"


def test_write_experiment_scaffold_overwrite_generated_refreshes_generator_files_only_and_keeps_registration(tmp_path: Path) -> None:
    write_experiment_scaffold("demo", display_name="Demo Experiment", repo_root=tmp_path)
    project_root = tmp_path / "experiments" / "projects" / "demo"

    generator_owned = project_root / "_config" / "README.md"
    operator_owned = project_root / "README.md"
    original_generator = generator_owned.read_text(encoding="utf-8")

    generator_owned.write_text("BROKEN GENERATOR NOTE\n", encoding="utf-8")
    operator_owned.write_text("operator customizations stay put\n", encoding="utf-8")

    result = write_experiment_scaffold(
        "demo",
        display_name="Demo Experiment",
        repo_root=tmp_path,
        collision_policy="overwrite_generated",
    )

    assert result["ok"] is True
    assert result["status"] == "created"
    assert result["registration"]["status"] == "unchanged"
    assert generator_owned.read_text(encoding="utf-8") == original_generator
    assert operator_owned.read_text(encoding="utf-8") == "operator customizations stay put\n"
    assert str(project_root / "README.md").replace(str(tmp_path) + "/", "") in result["skipped_paths"]
    assert len(list_registered_experiments(repo_root=tmp_path)) == 1


def test_create_experiment_scaffold_cli_dry_run_collision_reporting_and_extended_projection(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(create_experiment_scaffold, "REPO_ROOT", tmp_path)

    rc = create_experiment_scaffold.main([
        "--project-id",
        "demo",
        "--display-name",
        "Demo Experiment",
        "--family-tag",
        "foundation",
        "--default-profile",
        "baseline",
        "--analysis-recipe",
        "bootstrap_summary",
        "--dry-run",
    ])
    dry_run = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert dry_run["ok"] is True
    assert dry_run["status"] == "planned"
    assert "experiments/projects/demo/_config/profiles/baseline.json" in dry_run["planned_paths"]
    assert "experiments/projects/demo/analysis/bootstrap_summary.md" in dry_run["planned_paths"]
    assert dry_run["registration"]["action"] == "create"

    rc = create_experiment_scaffold.main([
        "--project-id",
        "demo",
        "--display-name",
        "Demo Experiment",
        "--family-tag",
        "foundation",
        "--default-profile",
        "baseline",
        "--analysis-recipe",
        "bootstrap_summary",
    ])
    created = json.loads(capsys.readouterr().out)

    assert rc == 0
    assert created["ok"] is True
    assert created["written_paths"]
    assert created["registration"]["record"]["family_tags"] == ["foundation"]
    assert created["registration"]["record"]["analysis_recipe"] == "bootstrap_summary"

    rc = create_experiment_scaffold.main([
        "--project-id",
        "demo",
        "--display-name",
        "Demo Experiment",
    ])
    collision = json.loads(capsys.readouterr().err)

    assert rc == 2
    assert collision["ok"] is False
    assert collision["status"] == "collision"
    assert any(item["type"] == "project_root_not_empty" for item in list(collision["collisions"]))


def test_list_scaffolded_experiments_cli_lists_and_resolves_registered_project(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(create_experiment_scaffold, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(list_scaffolded_experiments, "REPO_ROOT", tmp_path)
    create_experiment_scaffold.main([
        "--project-id",
        "demo",
        "--display-name",
        "Demo Experiment",
    ])
    capsys.readouterr()

    rc = list_scaffolded_experiments.main([])
    listed = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert listed["ok"] is True
    assert listed["registry_path"] == EXPERIMENT_DISCOVERY_REGISTRY_PATH
    assert [row["project_id"] for row in listed["experiments"]] == ["demo"]

    rc = list_scaffolded_experiments.main(["--project-id", "demo"])
    resolved = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert resolved["ok"] is True
    assert resolved["record"]["project_id"] == "demo"
    assert resolved["record"]["contract_exists"] is True


def test_create_experiment_scaffold_cli_script_entrypoint_runs_from_repo_root() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    result = subprocess.run(
        [
            sys.executable,
            "tools/bin/create_experiment_scaffold.py",
            "--project-id",
            "demo_scaffold_verify",
            "--display-name",
            "Demo Scaffold Verify",
            "--dry-run",
        ],
        cwd=repo_root,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["ok"] is True
    assert payload["status"] == "planned"
    assert "experiments/projects/demo_scaffold_verify/_runner/experiment_contract.json" in payload["planned_paths"]
    assert payload["registration"]["action"] == "create"
