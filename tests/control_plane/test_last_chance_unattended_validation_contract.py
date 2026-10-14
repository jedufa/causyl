from __future__ import annotations

import json
from pathlib import Path

from tools.bin import last_chance_unattended_contract as contract


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_last_chance_contract_writes_required_artifacts(tmp_path: Path) -> None:
    contract.run_contract(tmp_path)

    assert (tmp_path / "last_chance_scorecard_v1.json").exists()
    assert (tmp_path / "last_chance_claim_blockers_v1.json").exists()
    assert (tmp_path / "last_chance_stop_rules_v1.json").exists()


def test_last_chance_contract_defaults_unattended_window_to_zero_and_keeps_blockers_in_sync(tmp_path: Path) -> None:
    contract.run_contract(tmp_path)

    scorecard = _load_json(tmp_path / "last_chance_scorecard_v1.json")
    blockers = _load_json(tmp_path / "last_chance_claim_blockers_v1.json")
    stop_rules = _load_json(tmp_path / "last_chance_stop_rules_v1.json")

    blocker_ids = {row["id"] for row in blockers["blockers"]}
    safe_blockers = set(scorecard["claim_levels"]["safe_for_unsupervised_operation_within_declared_bounds"]["blocked_by"])
    stop_rule_ids = {row["id"] for row in stop_rules["stop_rules"]}
    major_failures = {row["id"] for row in scorecard["major_failure_classes_v1"]}
    child_contracts = {row["child_uid"]: row for row in scorecard["child_execution_contracts_v1"]}

    assert scorecard["scorecard_policy"]["unsupported_unattended_window_defaults_to_zero"] is True
    assert scorecard["scorecard_policy"]["absence_of_signal_failures_are_first_class"] is True
    assert scorecard["scorecard_policy"]["stitched_truth_impossibility_is_a_major_failure_class"] is True
    assert safe_blockers == blocker_ids
    assert "unsupported_unattended_window_defaults_to_zero" in stop_rule_ids
    assert "absence_of_signal_failure" in major_failures
    assert "stitched_truth_impossibility" in major_failures
    assert child_contracts["PIS-20260413-174940Z-T2Q3"]["minimum_repetitions"] >= 4
    assert child_contracts["PIS-20260413-174941Z-U3R4"]["requires_cross_surface_disagreement"] is True
