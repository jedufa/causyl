from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from experiments_shared import (
    assess_pis_proof_gap_v1,
    build_phase1_regression_report,
    execute_patch_ingest_repair_v1,
    inspect_job_bounded_recovery_v1,
    load_recorded_fallback_events_v1,
    record_fallback_event_v1,
    synthesize_improvement_opportunity_v1,
    validate_fallback_event_payload,
    validate_improvement_opportunity_payload,
)
from infra.webui.ledger.ledger_manager import ChangeLedger


REPO_ROOT = Path(__file__).resolve().parents[2]


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(root), check=True, capture_output=True, text=True)


def test_patch_ingest_repair_repairs_wrapper_and_bad_hunk_header(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "hello.txt"
    target.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=str(tmp_path), check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True, text=True)

    patch_text = """notes before patch
diff --git a/hello.txt b/hello.txt
--- a/hello.txt
+++ b/hello.txt
@@ -1,99 +1,99 @@
-hello
+hello world
"""

    result = execute_patch_ingest_repair_v1(
        {
            "patch_text": patch_text,
            "expected_target_files": ["hello.txt"],
            "validation_commands": ["python -c \"from pathlib import Path; import sys; sys.exit(0 if Path('hello.txt').read_text() == 'hello world\\n' else 1)\""],
            "evidence_artifacts": ["patch_input"],
            "verifier_verdict": "PASS",
        },
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert "metadata_wrapper_stripped" in result["repair_classes_applied"]
    assert "hunk_header_counts_repaired" in result["repair_classes_applied"]
    assert result["outcome"]["outcome"] == "success_after_deterministic_recovery"
    assert target.read_text(encoding="utf-8") == "hello world\n"


def test_patch_ingest_repair_blocks_fallback_when_checklist_is_incomplete(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    broken = execute_patch_ingest_repair_v1(
        {
            "patch_text": "not a patch",
            "allow_fallback": True,
            "escalation_reason": "needs help",
        },
        repo_root=tmp_path,
    )

    assert broken["ok"] is False
    assert broken["fallback_allowed"] is False
    assert broken["pre_escalation_checklist"]["patch_contract_check_performed"] is False


def test_pis_assess_requires_explicit_pass_and_evidence(tmp_path: Path) -> None:
    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(
        title="phase-1 proof item",
        slug="phase-1-proof-item",
        pis_markdown="# acceptance\n\n- prove this\n",
        project_id="causyl",
    )
    uid = str(created["uid"])

    missing = assess_pis_proof_gap_v1({"target_pis_uid": uid}, repo_root=tmp_path)
    assert missing["closeable"] is False
    assert "missing_validation_commands" in missing["proof_gaps"]
    assert missing["outcome"]["outcome"] in {"proof_gap_present", "blocked_requires_review"}

    passing = assess_pis_proof_gap_v1(
        {
            "target_pis_uid": uid,
            "evidence_artifacts": ["report.json"],
            "validation_commands_executed": ["pytest -q tests/example.py"],
            "verifier_verdict": "PASS",
        },
        repo_root=tmp_path,
    )
    assert passing["closeable"] is True
    assert passing["outcome"]["outcome"] == "success_deterministic"


def test_job_inspect_surfaces_receipt_watchdog_disagreement_and_blocks_fallback_without_note() -> None:
    result = inspect_job_bounded_recovery_v1(
        {
            "job_id": "job_001",
            "job_spec": {"job_id": "job_001", "kind": "single_turn"},
            "job_state": {"job_id": "job_001", "last_status": "blocked"},
            "receipts": [{"status": "ok"}],
            "watchdog_summary": {"status": "degraded"},
            "allow_fallback": True,
        },
        repo_root=Path("."),
    )

    assert result["state_class"] == "blocked"
    assert result["disagreement_recorded"] is True
    assert result["fallback_allowed"] is False


def test_fallback_absorption_gate_rejects_bad_economics_and_promotes_repeated_stable_cases(tmp_path: Path) -> None:
    shutil.copytree(REPO_ROOT / "governance" / "contracts", tmp_path / "governance" / "contracts")
    low_value_event = record_fallback_event_v1(
        {
            "action_family": "patch_ingest_repair",
            "intent": "apply patch",
            "failure_class": "stale_patch_state",
            "deterministic_steps_attempted": ["preflight", "repair"],
            "fallback_reason": "semantic_merge_needed",
            "pre_escalation_checklist": {"complete": True},
            "codex_action_summary": "manual merge",
            "post_fallback_outcome": "success_after_codex_fallback",
            "solution_path_signature": "manual_merge",
            "bounded_authority_safe": True,
            "validator_path_clear": True,
            "expected_future_codex_cost": 1.0,
            "operator_friction_cost": 0.5,
            "estimated_build_cost": 10.0,
            "estimated_maintenance_cost": 3.0,
        },
        repo_root=tmp_path,
    )
    assert validate_fallback_event_payload(low_value_event)["ok"] is True

    rejected = synthesize_improvement_opportunity_v1([low_value_event, dict(low_value_event)], repo_root=Path("."))
    assert rejected["decision"] == "reject_opportunity"
    assert rejected["rejection_reason"] == "bad_cost_benefit"

    high_value_event = dict(low_value_event)
    high_value_event["expected_future_codex_cost"] = 20.0
    high_value_event["operator_friction_cost"] = 5.0
    promoted = synthesize_improvement_opportunity_v1([high_value_event, dict(high_value_event)], repo_root=Path("."))
    assert promoted["decision"] == "promote_opportunity"
    assert validate_improvement_opportunity_payload(promoted)["ok"] is True


def test_recorded_fallback_events_are_persisted_and_filterable(tmp_path: Path) -> None:
    contracts_root = tmp_path / "governance" / "contracts"
    contracts_root.mkdir(parents=True, exist_ok=True)
    shutil.copy(REPO_ROOT / "governance" / "contracts" / "fallback_event_schema_v1.json", contracts_root / "fallback_event_schema_v1.json")
    first = record_fallback_event_v1(
        {
            "action_family": "operator_surface_routing",
            "intent": "server restart",
            "failure_class": "unsupported_request",
            "deterministic_steps_attempted": ["commparse_v1"],
            "fallback_reason": "unsupported_request_surface_gap",
            "pre_escalation_checklist": {"complete": True},
            "codex_action_summary": "added direct route",
            "post_fallback_outcome": "success_after_codex_fallback",
            "solution_path_signature": "route::server_restart",
            "bounded_authority_safe": True,
            "validator_path_clear": True,
            "expected_future_codex_cost": 4.0,
            "operator_friction_cost": 2.0,
            "estimated_build_cost": 1.0,
            "estimated_maintenance_cost": 0.5,
        },
        repo_root=tmp_path,
    )
    second = record_fallback_event_v1(
        {
            "action_family": "patch_apply",
            "intent": "fix this patch",
            "failure_class": "git_apply_conflict",
            "deterministic_steps_attempted": ["commparse_v1", "patch_apply_chain_v1"],
            "fallback_reason": "blocked_patch_conflict",
            "pre_escalation_checklist": {"complete": True},
            "codex_action_summary": "resolved patch conflict",
            "post_fallback_outcome": "success_after_codex_fallback",
            "solution_path_signature": "patch::conflict_resolution",
            "bounded_authority_safe": True,
            "validator_path_clear": True,
            "expected_future_codex_cost": 6.0,
            "operator_friction_cost": 3.0,
            "estimated_build_cost": 2.0,
            "estimated_maintenance_cost": 0.5,
        },
        repo_root=tmp_path,
    )
    assert first["fallback_event_id"]
    assert second["fallback_event_id"]
    rows = load_recorded_fallback_events_v1(
        repo_root=tmp_path,
        action_family="patch_apply",
        failure_class="git_apply_conflict",
    )
    assert len(rows) == 1
    assert rows[0]["intent"] == "fix this patch"


def test_phase1_regression_report_requires_pass_bound_success() -> None:
    report = build_phase1_regression_report(
        {
            "patch_ingest_repair": {"outcome": {"outcome": "success_after_deterministic_recovery", "verifier_verdict": "PASS"}},
            "pis_assess": {"outcome": {"outcome": "success_deterministic", "verifier_verdict": "PASS"}},
            "job_inspect": {"state_class": "blocked"},
            "fallback_gate": {"governed_follow_on_kind": "record_only"},
        }
    )

    assert report["ok"] is True
    assert report["checks"]["fallback_gate_governed"] is True
