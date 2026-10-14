from __future__ import annotations

import json

from experiments_shared import summarize_experiment_contract, validate_experiment_contract
from experiments_shared.runner_utils import ensure_project_output_env

from .helpers.contracts import ScenarioContract, assert_scenario_contract
from .helpers.runtime import write_demo_project_contract


CONTRACT = ScenarioContract(
    scenario_id="artifact_boundary_and_repo_hygiene_e2e",
    operator_job="Run experiment work without letting heavy runtime data collapse back into the repo as tracked truth.",
    capability="Experiment contract enforces an external artifact root and summarizes the reverse-pointer surfaces that remain in repo truth.",
    durable_truth=(
        "experiment contract artifact",
        "experiment contract summary",
        "resolved outputs environment",
    ),
    manual_repair_eliminated="Manually deciding where heavy run outputs belong and whether a project is allowed to write runtime artifacts into the repo.",
    primary_intent_sheets=("IS-20",),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "direct contract validation and summary when no richer plane entrypoint exists yet",
        "temp-repo project contract setup",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "fabricating artifact-manifest compliance",
        "writing projection records manually",
    ),
)


def test_artifact_boundary_and_repo_hygiene_e2e(first_wave_root) -> None:
    assert_scenario_contract(CONTRACT)
    write_demo_project_contract(first_wave_root)
    contract_path = first_wave_root / "experiments" / "projects" / "demo" / "_runner" / "experiment_contract.json"
    valid = json.loads(contract_path.read_text(encoding="utf-8"))
    valid["artifacts"]["external_data_root"] = "DEMO_OUTPUTS_DIR or ~/causyl_experiment_data/demo"
    valid["contract_hash"] = validate_experiment_contract(valid, repo_root=first_wave_root)["computed_contract_hash"]
    contract_path.write_text(json.dumps(valid, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    validation = validate_experiment_contract(valid, repo_root=first_wave_root)
    assert validation["ok"] is True
    summary = summarize_experiment_contract(validation["normalized_contract"], repo_root=first_wave_root)
    assert summary["artifact_boundary"]["external_data_root"]["resolution_kind"] == "env_or_path"
    assert summary["artifact_boundary"]["external_data_root"]["env_var"] == "DEMO_OUTPUTS_DIR"
    assert summary["artifact_boundary"]["reverse_pointer_surfaces"] == ["run_record.json", "artifact_manifest.json"]

    env = ensure_project_output_env(
        project_id="demo",
        repo_root=first_wave_root,
        project_root=first_wave_root / "experiments" / "projects" / "demo",
        env={"DEMO_OUTPUTS_DIR": "/var/tmp/demo-outputs"},
    )
    assert env["EXPERIMENT_OUTPUTS_DIR"] == "/var/tmp/demo-outputs"

    invalid = json.loads(contract_path.read_text(encoding="utf-8"))
    invalid["artifacts"]["external_data_root"] = "experiments/projects/demo/_artifacts/runtime"
    invalid["contract_hash"] = validate_experiment_contract(invalid, repo_root=first_wave_root)["computed_contract_hash"]
    invalid_result = validate_experiment_contract(invalid, repo_root=first_wave_root)
    assert invalid_result["ok"] is False
    assert any(row["code"] == "invalid_artifact_policy" for row in invalid_result["errors"])
    assert any("outside the tracked repo root" in row["message"] for row in invalid_result["errors"])
