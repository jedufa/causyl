from __future__ import annotations

import json
from pathlib import Path

from experiments_shared import FIRST_IMPLEMENTATION_ACTION_ID
from experiments_shared import PHASE1_ACTION_IDS
from experiments_shared import classify_action_outcome
from experiments_shared import classify_phase1_action_request
from experiments_shared import load_action_registry
from experiments_shared import load_rule_mapping_registry
from experiments_shared import validate_action_plane_registries
from experiments_shared import validate_action_outcome_payload
from experiments_shared import validate_tool_capability_registry


def test_tool_capability_registry_requires_executable_backing_for_active_entries() -> None:
    result = validate_tool_capability_registry()
    assert result["ok"] is True
    entries = list(result["registry"]["entries"])
    active_entries = [row for row in entries if row.get("status") == "active"]
    assert active_entries
    assert all(row.get("has_runnable_backing") is True for row in active_entries)
    patch_helper = next(row for row in entries if row.get("tool_id") == "patch_validation_helper")
    assert patch_helper["status"] == "active"
    assert patch_helper["has_runnable_backing"] is True


def test_action_plane_registries_are_phase1_only_and_referentially_integral() -> None:
    result = validate_action_plane_registries()
    assert result["ok"] is True
    action_ids = [row["action_id"] for row in result["action_registry"]["actions"]]
    assert tuple(action_ids) == PHASE1_ACTION_IDS
    assert action_ids[0] == FIRST_IMPLEMENTATION_ACTION_ID


def test_rule_mapping_classifies_supported_phase1_requests_deterministically() -> None:
    patch_request = {"request_text": "Please apply patch and repair the diff if needed."}
    patch_result = classify_phase1_action_request(patch_request)
    assert patch_result["action_id"] == "patch_ingest_repair_v1"
    assert patch_result["matched_rule_id"] == "route_patch_ingest_repair_v1"

    proof_gap_request = {"request_text": "Assess this PIS and surface any proof gap."}
    proof_gap_result = classify_phase1_action_request(proof_gap_request)
    assert proof_gap_result["action_id"] == "pis_assess_proof_gap_v1"

    job_request = {"request_text": "Inspect this stuck scheduler job and try bounded recovery."}
    job_result = classify_phase1_action_request(job_request)
    assert job_result["action_id"] == "job_inspect_bounded_recovery_v1"

    unsupported = classify_phase1_action_request({"request_text": "Plan a chemistry research program."})
    assert unsupported["action_id"] == ""
    assert unsupported["unsupported_reason"] == "no_supported_phase1_rule_match"


def test_rule_mapping_surfaces_explicit_tie_conflicts_when_priorities_collide(tmp_path: Path) -> None:
    root = tmp_path
    for rel in [
        "governance/contracts/action_registry_v1.json",
        "governance/contracts/execution_playbook_registry_v1.json",
        "governance/contracts/action_rule_mapping_v1.json",
        "governance/contracts/tool_capability_registry_v1.json",
    ]:
        src = Path(rel)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    for rel in [
        "infra/bin/safe_exec",
        "infra/bin/apply_patch",
        "infra/bin/patch_validation_helper.py",
        "infra/bin/scheduler_health_check.sh",
        "infra/bin/scheduler_watchdog.py",
        "infra/bin/server_status.sh",
        "infra/bin/schedule_job.py",
    ]:
        src = Path(rel)
        dest = root / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
    registry = json.loads((root / "governance/contracts/action_rule_mapping_v1.json").read_text(encoding="utf-8"))
    registry["rules"].append(
        {
            "rule_id": "route_patch_ingest_repair_duplicate",
            "priority": 10,
            "action_id": "patch_ingest_repair_v1",
            "keywords_any": ["patch"],
            "required_artifact_flags": [],
            "required_target_keys": [],
            "confidence_bucket": "high",
            "required_evidence_gatherers": ["patch_diff"],
            "fallback_eligibility_class": "bounded_patch_repair",
        }
    )
    (root / "governance/contracts/action_rule_mapping_v1.json").write_text(
        json.dumps(registry, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    result = classify_phase1_action_request({"request_text": "apply this patch"}, repo_root=root)
    assert result["action_id"] == ""
    assert result["unsupported_reason"] == "rule_conflict_tie"


def test_success_outcomes_require_explicit_pass_and_evidence() -> None:
    downgraded = classify_action_outcome(
        {
            "requested_outcome": "success_after_codex_fallback",
            "validation_commands_executed": ["pytest -q tests/example.py"],
            "evidence_artifacts": ["runtime/report.json"],
            "verifier_verdict": "FAIL",
            "proof_gaps": [],
        }
    )
    assert downgraded["outcome"] == "blocked_requires_review"
    assert "missing_explicit_pass" in downgraded["downgrade_reasons"]

    with_gap = validate_action_outcome_payload(
        {
            "requested_outcome": "success_deterministic",
            "validation_commands_executed": ["pytest -q tests/example.py"],
            "evidence_artifacts": ["runtime/report.json"],
            "verifier_verdict": "PASS",
            "proof_gaps": ["residual mismatch"],
        }
    )
    assert with_gap["ok"] is True
    assert with_gap["result"]["outcome"] == "proof_gap_present"

    passing = validate_action_outcome_payload(
        {
            "requested_outcome": "success_after_deterministic_recovery",
            "validation_commands_executed": ["pytest -q tests/example.py"],
            "evidence_artifacts": ["runtime/report.json"],
            "verifier_verdict": "PASS",
            "proof_gaps": [],
        }
    )
    assert passing["result"]["outcome"] == "success_after_deterministic_recovery"
