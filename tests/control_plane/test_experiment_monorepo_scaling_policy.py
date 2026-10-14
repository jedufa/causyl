from __future__ import annotations

import json
from pathlib import Path


def _load_json(path: str) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def test_experiment_monorepo_scaling_policy_cross_links_and_thresholds() -> None:
    policy = _load_json("governance/experiment_monorepo_scaling_policy_v1.json")
    checklist = _load_json("governance/experiment_repo_split_criteria_checklist_v1.json")
    process = _load_json("governance/experiment_storage_growth_review_process_v1.json")

    assert policy["schema_version"] == "experiment_monorepo_scaling_policy_v1"
    assert policy["metadata"]["source_intent_uid"] == "IS-20260403-212914Z-1260"
    assert policy["default_posture"]["repo_strategy"] == "monorepo_first"
    assert "external artifact roots" in policy["default_posture"]["heavy_runtime_data_rule"]
    assert policy["truth_boundary"]["upstream_contract_dependency"] == "IS-20260403-212914Z-47F9"

    tracked = policy["warning_thresholds"]["tracked_repo"]
    assert tracked["warn_file_count"] < tracked["split_candidate_file_count"]
    assert tracked["warn_total_size_mib"] < tracked["split_candidate_total_size_mib"]

    per_experiment = policy["warning_thresholds"]["per_experiment_tracked_slice"]
    assert per_experiment["warn_file_count"] < per_experiment["split_candidate_file_count"]
    assert per_experiment["warn_total_size_mib"] < per_experiment["split_candidate_total_size_mib"]

    assert checklist["source_intent_uid"] == policy["metadata"]["source_intent_uid"]
    assert "tracked_truth_growth_exceeds_thresholds_after_pruning" in checklist["mandatory_reason_keys"]
    assert "runtime directory growth by itself" in checklist["non_justifications"]

    check_keys = {row["key"] for row in checklist["required_pre_split_checks"]}
    assert "external_artifact_roots_enforced" in check_keys
    assert "central_index_preserved" in check_keys

    assert process["source_intent_uid"] == policy["metadata"]["source_intent_uid"]
    assert process["default_outcome_bias"] == "keep_monorepo_with_cleanup"
    assert "runtime/" in process["measurement_scope"]["exclude"]
    assert "repair artifact-boundary violations before discussing a repo split" in process["required_steps"]
