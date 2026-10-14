from __future__ import annotations

import subprocess
from pathlib import Path

from experiments_shared import (
    build_phase2_regression_report,
    build_snapshot_drift_action_request,
    execute_build_verify_loop_v1,
    execute_patch_apply_chain_v1,
    execute_status_reconcile_v1,
    validate_composed_execution_graphs,
    validate_phase2_playbook_contracts,
)
from infra.webui.ledger.ledger_manager import ChangeLedger


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=str(root), check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=str(root), check=True, capture_output=True, text=True)


def test_phase2_playbook_contracts_and_graphs_are_coherent() -> None:
    assert validate_phase2_playbook_contracts()["ok"] is True
    assert validate_composed_execution_graphs()["ok"] is True


def test_patch_apply_chain_executes_end_to_end_with_pass(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    target = tmp_path / "hello.txt"
    target.write_text("hello\n", encoding="utf-8")
    subprocess.run(["git", "add", "hello.txt"], cwd=str(tmp_path), check=True, capture_output=True, text=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(tmp_path), check=True, capture_output=True, text=True)

    ledger = ChangeLedger(repo_root=tmp_path)
    created = ledger.create_item(
        title="phase-2 proof item",
        slug="phase-2-proof-item",
        pis_markdown="# acceptance\n\n- prove patch chain\n",
        project_id="causyl",
    )

    patch_text = """notes before patch\ndiff --git a/hello.txt b/hello.txt\n--- a/hello.txt\n+++ b/hello.txt\n@@ -1,99 +1,99 @@\n-hello\n+hello world\n"""
    result = execute_patch_apply_chain_v1(
        {
            "task_id": "task_patch_chain_001",
            "action_family": "patch_apply",
            "execution_mode": "deterministic_then_codex",
            "context_bundle_id": "lab_delivery_safety_v1",
            "requested_by": "operator",
            "source_event": "api",
            "authority_scope": "repo_mutation",
            "patch_text": patch_text,
            "validation_commands": [
                "python -c \"from pathlib import Path; import sys; sys.exit(0 if Path('hello.txt').read_text() == 'hello world\\n' else 1)\""
            ],
            "target_pis_uid": str(created["uid"]),
            "evidence_artifacts": ["patch_input"],
            "verifier_verdict": "PASS",
        },
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert result["report"]["final_chain_outcome"] == "completed_with_pass"
    assert result["report"]["final_verifier_verdict"] == "PASS"
    assert target.read_text(encoding="utf-8") == "hello world\n"


def test_build_verify_loop_iterates_until_pass(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    result = execute_build_verify_loop_v1(
        {
            "task_id": "task_build_loop_001",
            "action_family": "build_verify",
            "execution_mode": "deterministic_then_codex",
            "context_bundle_id": "lab_core_v1",
            "requested_by": "scheduler",
            "source_event": "scheduler",
            "authority_scope": "read_only",
            "iterations": [
                {
                    "builder_summary": "iteration one incomplete",
                    "evidence_artifacts": ["candidate_report_v1"],
                    "validation_commands": [],
                    "verifier_verdict": "",
                    "expected_gain": 0.5,
                    "cost_per_iteration": 1.0,
                },
                {
                    "builder_summary": "iteration two complete",
                    "evidence_artifacts": ["candidate_report_v2"],
                    "validation_commands": ["python -c \"print('ok')\""],
                    "verifier_verdict": "PASS",
                    "expected_gain": 0.5,
                    "cost_per_iteration": 1.0,
                },
            ],
        },
        repo_root=tmp_path,
    )

    assert result["ok"] is True
    assert result["final_chain_outcome"] == "completed_with_pass"
    assert len(result["iteration_reports"]) == 2
    assert result["iteration_reports"][0]["final_chain_outcome"] == "completed_with_proof_gap"
    assert result["iteration_reports"][1]["final_chain_outcome"] == "completed_with_pass"


def test_status_reconcile_from_snapshot_drift_is_bounded_and_pass_bound(tmp_path: Path) -> None:
    _init_git_repo(tmp_path)
    request = build_snapshot_drift_action_request({"drift_id": "drift_001", "target_uid": "PIS-EXAMPLE"})
    request.update(
        {
            "current_status": "running",
            "expected_status": "blocked",
            "validation_commands": ["python -c \"print('status')\""],
            "verifier_verdict": "PASS",
        }
    )
    result = execute_status_reconcile_v1(request, repo_root=tmp_path)

    assert result["ok"] is True
    assert result["report"]["final_chain_outcome"] == "completed_with_pass"
    reconcile_payload = result["graph_result"]["artifacts"]["status_reconcile_result"]
    assert reconcile_payload["repo_mutation"] is False
    assert reconcile_payload["target_uid"] == "PIS-EXAMPLE"


def test_phase2_regression_report_requires_pass_bound_truth_and_checklist() -> None:
    build_result = {
        "iteration_reports": [
            {
                "verification_owner": "scheduler_verifier",
                "final_chain_outcome": "completed_with_pass",
                "final_verifier_verdict": "PASS",
                "step_records": [
                    {"node_id": "scheduler_verifier_gate", "decision": "terminal", "checklist_complete": True}
                ],
            }
        ],
        "final_chain_outcome": "completed_with_pass",
    }
    reconcile_result = {
        "report": {
            "verification_owner": "scheduler_verifier",
            "final_chain_outcome": "completed_with_pass",
            "final_verifier_verdict": "PASS",
            "step_records": [],
        }
    }
    patch_result = {
        "report": {
            "verification_owner": "scheduler_verifier",
            "final_chain_outcome": "completed_with_pass",
            "final_verifier_verdict": "PASS",
            "step_records": [],
        }
    }

    report = build_phase2_regression_report(
        {
            "build_verify": build_result,
            "status_reconcile": reconcile_result,
            "patch_apply": patch_result,
        }
    )

    assert report["ok"] is True
    assert report["checks"]["verification_owner_singular"] is True
