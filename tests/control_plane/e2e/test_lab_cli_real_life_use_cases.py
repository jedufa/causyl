from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest

from tests.control_plane.e2e.helpers.assertions import (
    assert_cross_surface_truth,
    assert_forbidden_mutations_absent,
    assert_lab_cli_prompt_contract,
    assert_lab_cli_surface_truth,
)
from tests.control_plane.e2e.helpers.evidence import collect_evidence
from tests.control_plane.e2e.helpers.launch import TERMINAL_STATUSES, launch_scenario
from tests.control_plane.e2e.helpers.runtime_env import (
    bootstrap_runtime_root,
    load_scenario,
    manifest_tree,
)
from tests.control_plane.e2e.helpers.wait import wait_for_terminal_receipt


pytestmark = [pytest.mark.integration, pytest.mark.e2e]


SCENARIO_NAMES = [
    "patch_fix_basic",
    "patch_apply_chain_pass",
    "patch_failure_explain_fallback",
    "build_verify_recovery_pass",
    "build_verify_economic_stop",
    "status_reconcile_success",
    "blocked_explain_next_action",
    "degraded_contradiction_explain",
    "learning_approved_guarded_proposal",
    "learning_rejected_absorption",
]


def _run_lab_cli_scenario(
    *,
    runtime_root: Path,
    scenario_name: str,
    prompt_override: str | None = None,
    plan_mode: bool = False,
) -> dict[str, object]:
    scenario = load_scenario("lab_cli", scenario_name)
    scenario = deepcopy(scenario)
    if prompt_override is not None:
        scenario["prompt_text"] = prompt_override
    payload = dict(scenario.get("payload") or {})
    cli_args = list(payload.get("cli_args") or [])
    if plan_mode:
        cli_args = ["--plan", *cli_args]
    payload["cli_args"] = cli_args
    scenario["payload"] = payload

    repo_root = bootstrap_runtime_root(runtime_root, scenario=scenario)
    before = manifest_tree(repo_root)
    launched = launch_scenario(scenario, repo_root=repo_root)
    receipt = wait_for_terminal_receipt(Path(launched["receipt_path"]), terminal_statuses=TERMINAL_STATUSES)
    evidence = collect_evidence(receipt_path=Path(receipt["receipt_path"]))
    after = manifest_tree(repo_root)

    allowed_prefixes = [f"runtime/state/e2e/action_plane/{scenario['name']}/"]
    allowed_prefixes.extend(list(scenario.get("allowed_mutation_prefixes") or []))
    assert_forbidden_mutations_absent(before, after, allowed_prefixes=allowed_prefixes)
    return {
        "scenario": scenario,
        "repo_root": repo_root,
        "output": launched["output"],
        "receipt": receipt,
        "evidence": evidence,
    }


@pytest.mark.parametrize("scenario_name", SCENARIO_NAMES)
def test_lab_cli_real_life_execution_matrix(scenario_name: str, tmp_path: Path) -> None:
    result = _run_lab_cli_scenario(
        runtime_root=tmp_path / scenario_name,
        scenario_name=scenario_name,
    )
    scenario = dict(result["scenario"])
    evidence = dict(result["evidence"])
    report = dict(evidence["report"])
    output = dict(report["output"])

    assert_cross_surface_truth(evidence, scenario)
    assert_lab_cli_prompt_contract(evidence, scenario)
    assert_lab_cli_surface_truth(evidence, scenario)

    if scenario_name == "build_verify_recovery_pass":
        assert len(list(dict(output.get("execution") or {}).get("iteration_reports") or [])) == 2
    if scenario_name == "patch_failure_explain_fallback":
        assert bool(dict(output.get("codex_fallback_request") or {}).get("eligible")) is True
    if scenario_name == "learning_approved_guarded_proposal":
        decision = dict(dict(output.get("execution") or {}).get("economic_decision_output") or {})
        proposal = dict(dict(output.get("execution") or {}).get("upgrade_pipeline_output") or {})
        assert decision.get("decision") == "approved"
        assert proposal.get("proposal_created") is True
    if scenario_name == "learning_rejected_absorption":
        decision = dict(dict(output.get("execution") or {}).get("economic_decision_output") or {})
        proposal = dict(dict(output.get("execution") or {}).get("upgrade_pipeline_output") or {})
        assert decision.get("decision") == "rejected"
        assert proposal.get("proposal_created") is False


@pytest.mark.parametrize("scenario_name", SCENARIO_NAMES)
def test_lab_cli_prompt_variants_normalize_to_supported_actions(scenario_name: str, tmp_path: Path) -> None:
    base = load_scenario("lab_cli", scenario_name)
    variants = list(base.get("prompt_variants") or [])
    expected_action_id = str(dict(base.get("expected_normalization") or {}).get("action_id") or "")
    for index, prompt in enumerate(variants, start=1):
        result = _run_lab_cli_scenario(
            runtime_root=tmp_path / f"{scenario_name}__variant_{index}",
            scenario_name=scenario_name,
            prompt_override=str(prompt),
            plan_mode=True,
        )
        evidence = dict(result["evidence"])
        report = dict(evidence["report"])
        output = dict(report["output"])
        scenario = dict(result["scenario"])
        expected_normalization = dict(scenario.get("expected_normalization") or {})
        expected_normalization["mode"] = "plan"
        scenario["expected_normalization"] = expected_normalization
        assert output.get("mode") == "plan"
        assert output.get("supported") is True
        assert dict(dict(output.get("classification") or {}).get("selection") or {}).get("action_id") == expected_action_id
        assert_lab_cli_prompt_contract(evidence, scenario)


def test_lab_cli_suite_covers_execution_and_learning_paths(tmp_path: Path) -> None:
    seen_actions: set[str] = set()
    seen_prompts: list[str] = []
    for index, scenario_name in enumerate(SCENARIO_NAMES, start=1):
        result = _run_lab_cli_scenario(
            runtime_root=tmp_path / f"coverage_{index}_{scenario_name}",
            scenario_name=scenario_name,
        )
        output = dict(dict(result["evidence"])["report"]["output"])
        seen_actions.add(str(dict(output.get("selection") or {}).get("action_id") or ""))
        seen_prompts.append(str(result["scenario"]["prompt_text"]))

    assert "patch_ingest_repair_v1" in seen_actions
    assert "patch_apply_chain_v1" in seen_actions
    assert "build_verify_loop_v1" in seen_actions
    assert "status_reconcile_v1" in seen_actions
    assert "blocked_chain_explain_and_recommend_v1" in seen_actions
    assert "repeated_patch_fallback_to_approved_upgrade_v1" in seen_actions
    assert any("absorb" in prompt.lower() or "recurring fallback" in prompt.lower() for prompt in seen_prompts)
