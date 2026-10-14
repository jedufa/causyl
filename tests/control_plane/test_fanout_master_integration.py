from __future__ import annotations

from infra.controlplane import fanout_master_integration as integration


def _swarm(allowed_paths: list[str] | None = None) -> dict:
    return {
        "success_contract": {
            "accepted_statuses": ["finished"],
            "require_changed_files": True,
        },
        "evidence_requirements": {
            "require_summary_path": True,
            "require_worker_artifacts": False,
        },
        "write_policy": {
            "allowed_paths": list(allowed_paths or ["infra/controlplane"]),
            "max_changed_files": 5,
        },
        "retry_policy": {
            "child_relaunch_limit": 1,
            "master_rerun_limit": 0,
        },
        "launch_options": {},
    }


def test_integrate_swarm_result_classifies_complete_success() -> None:
    result = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
            "artifacts": {"summary_path": "/tmp/swarm.md"},
            "workers": [
                {
                    "worker_id": "maker_1",
                    "status": "delivered",
                    "changed_files": ["infra/controlplane/scheduled_jobs.py"],
                    "summary_path": "/tmp/worker_1.md",
                }
            ],
        },
        swarm=_swarm(),
    )

    assert result["classification"] == "complete_success"
    assert result["integration_disposition"] == "integrated_clean"
    assert result["final_verdict"] == "success_deterministic"
    assert result["keep_child_ids"] == ["maker_1"]
    assert result["rerun_child_ids"] == []


def test_integrate_swarm_result_classifies_partial_success_with_rerun_boundary() -> None:
    result = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
            "artifacts": {"summary_path": "/tmp/swarm.md"},
            "workers": [
                {
                    "worker_id": "maker_1",
                    "status": "delivered",
                    "changed_files": ["infra/controlplane/scheduled_jobs.py"],
                    "summary_path": "/tmp/worker_1.md",
                },
                {
                    "worker_id": "maker_2",
                    "status": "failed_with_evidence",
                    "changed_files": ["infra/controlplane/fanout_master_contract.py"],
                    "summary_path": "/tmp/worker_2.md",
                },
            ],
        },
        swarm=_swarm(),
    )

    assert result["classification"] == "partial_success"
    assert result["integration_disposition"] == "integrated_partial"
    assert result["final_verdict"] == "partial_success"
    assert result["keep_child_ids"] == ["maker_1"]
    assert result["rerun_child_ids"] == ["maker_2"]
    assert result["restart_reconciliation"]["status"] == "partial_collection_reconciled"
    assert result["retry_boundaries"]["child_relaunch"]["state"] == "allowed"
    assert result["retry_boundaries"]["child_relaunch"]["eligible_child_ids"] == ["maker_2"]


def test_integrate_swarm_result_blocks_duplicate_child_receipts() -> None:
    result = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
            "artifacts": {"summary_path": "/tmp/swarm.md"},
            "workers": [
                {
                    "worker_id": "maker_1",
                    "status": "delivered",
                    "changed_files": ["infra/controlplane/scheduled_jobs.py"],
                    "summary_path": "/tmp/worker_1.md",
                },
                {
                    "worker_id": "maker_1",
                    "status": "delivered",
                    "changed_files": ["infra/controlplane/fanout_master_contract.py"],
                    "summary_path": "/tmp/worker_1_retry.md",
                },
            ],
        },
        swarm=_swarm(),
    )

    assert result["classification"] == "conflicting_outputs"
    assert result["restart_reconciliation"]["status"] == "blocked_on_conflicting_receipts"
    assert result["restart_reconciliation"]["duplicate_child_ids"] == ["maker_1"]
    assert result["retry_boundaries"]["child_relaunch"]["state"] == "blocked_by_conflict"
    assert result["conflicts"] == [
        {
            "kind": "duplicate_child_receipts",
            "paths": [],
            "child_ids": ["maker_1"],
            "summary": "duplicate durable receipts detected for maker_1",
            "receipt_count": 2,
        }
    ]


def test_integrate_swarm_result_classifies_conflicting_outputs_on_overlap() -> None:
    result = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
            "artifacts": {"summary_path": "/tmp/swarm.md"},
            "workers": [
                {
                    "worker_id": "maker_1",
                    "status": "delivered",
                    "changed_files": ["infra/controlplane/scheduled_jobs.py"],
                    "summary_path": "/tmp/worker_1.md",
                },
                {
                    "worker_id": "maker_2",
                    "status": "delivered",
                    "changed_files": ["infra/controlplane/scheduled_jobs.py"],
                    "summary_path": "/tmp/worker_2.md",
                },
            ],
        },
        swarm=_swarm(),
    )

    assert result["classification"] == "conflicting_outputs"
    assert result["integration_disposition"] == "conflict_blocked"
    assert result["final_verdict"] == "blocked_requires_review"
    assert result["conflicts"] == [
        {
            "kind": "overlapping_changed_files",
            "paths": ["infra/controlplane/scheduled_jobs.py"],
            "child_ids": ["maker_1", "maker_2"],
            "summary": "multiple kept children changed infra/controlplane/scheduled_jobs.py",
        }
    ]


def test_integrate_swarm_result_classifies_unusable_child_packets() -> None:
    result = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
            "artifacts": {"summary_path": "/tmp/swarm.md"},
            "workers": [
                {
                    "worker_id": "maker_1",
                    "changed_files": [],
                }
            ],
        },
        swarm=_swarm(),
    )

    assert result["classification"] == "unusable_child_packets"
    assert result["integration_disposition"] == "rerun_required"
    assert result["final_verdict"] == "proof_gap_present"
    assert result["rerun_child_ids"] == ["maker_1"]
    assert result["retry_boundaries"]["child_relaunch"]["state"] == "allowed"
    assert result["packet_errors"] == [
        {"child_id": "maker_1", "reason": "worker_status:missing"}
    ]


def test_integrate_swarm_result_classifies_master_blocked_outcome_for_off_target_scope() -> None:
    result = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {
                "changed_files": ["infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminScreen.kt"]
            },
            "artifacts": {"summary_path": "/tmp/swarm.md"},
            "workers": [],
        },
        swarm=_swarm(),
    )

    assert result["classification"] == "master_blocked_outcome"
    assert result["integration_disposition"] == "master_blocked"
    assert result["final_verdict"] == "blocked_requires_review"
    assert result["evaluation_status"] == "off_target_scope"
    assert result["master_blockers"] == [
        {"code": "off_target_scope", "summary": "swarm changed files outside allowed_paths"}
    ]
    assert result["off_target_paths"] == [
        "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminScreen.kt"
    ]


def test_integrate_swarm_result_marks_child_retry_exhaustion_explicitly() -> None:
    result = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
            "artifacts": {"summary_path": "/tmp/swarm.md"},
            "workers": [
                {
                    "worker_id": "maker_2",
                    "status": "failed_with_evidence",
                    "attempt_count": 2,
                    "summary_path": "/tmp/worker_2.md",
                },
            ],
        },
        swarm=_swarm(),
    )

    assert result["rerun_child_ids"] == ["maker_2"]
    assert result["retry_boundaries"]["child_relaunch"]["state"] == "exhausted"
    assert result["retry_boundaries"]["child_relaunch"]["exhausted_child_ids"] == ["maker_2"]
