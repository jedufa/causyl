from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import scheduler_projection_contract as contract


def test_scheduler_projection_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.scheduler_projection_contract_v1()

    assert payload.version == "scheduler_projection_contract_v1"
    assert payload.uid == "PIS-20260408-181100Z-D1E1"
    assert payload.canonical_artifact_rel.as_posix() == "governance/scheduler_projection_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_scheduler_projection_v1.md"
    assert payload.statuses == (
        "active",
        "blocked",
        "queued",
        "idle",
        "completed_once",
        "failed",
        "ambiguous",
    )


def test_scheduler_projection_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/scheduler_projection_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["status_vocabulary"].keys()) == contract.STATUS_VOCABULARY
    assert tuple(payload["consumer_surfaces"]) == contract.CONSUMER_SURFACES


def test_scheduler_projection_contract_validation_passes() -> None:
    assert contract.validate_scheduler_projection_contract_v1() == ()


def test_project_scheduler_job_row_classifies_dependency_block_as_blocked_with_provenance() -> None:
    projection = contract.project_scheduler_job_row(
        {
            "job_id": "blocked_dep_001",
            "enabled": True,
            "display_title": "blocked dep",
            "schedule": {"mode": "once", "run_at": "2026-04-08T19:00:00Z"},
            "state": {"last_status": "blocked", "last_nonlaunch_reason": "dependency_pending:build_001"},
            "due": {"eligible": False, "reason": "dependency_pending:build_001", "due_utc": ""},
            "lifecycle_projection": {"recognized": True, "canonical_state": "blocked"},
        }
    )

    assert projection["projected_status"] == "blocked"
    assert projection["projection_reason"] == "dependency_pending:build_001"
    assert projection["next_candidate_bucket"] == "none"
    assert [row["source_path"] for row in projection["provenance"]] == [
        "state.last_status",
        "lifecycle_projection.canonical_state",
        "due.reason",
        "state.last_nonlaunch_reason",
    ]


def test_project_scheduler_job_row_fails_closed_on_conflicting_active_and_terminal_truth() -> None:
    projection = contract.project_scheduler_job_row(
        {
            "job_id": "ambiguous_001",
            "enabled": True,
            "display_title": "ambiguous job",
            "schedule": {"mode": "once", "run_at": "2026-04-08T19:00:00Z"},
            "state": {"active_run_id": "sj_live_001", "last_status": "ok"},
            "due": {"eligible": False, "reason": "active_run", "due_utc": ""},
            "lifecycle_projection": {"recognized": True, "canonical_state": "succeeded"},
        }
    )

    assert projection["projected_status"] == "ambiguous"
    assert projection["durable_conflict"] is True
    assert projection["ambiguity_reasons"] == ["active_run_conflicts_with_terminal_lifecycle"]


def test_select_next_scheduler_job_skips_blocked_and_completed_once_rows() -> None:
    next_job = contract.select_next_scheduler_job(
        [
            {
                "job_id": "job_blocked",
                "enabled": True,
                "display_title": "blocked",
                "schedule": {"mode": "once", "run_at": "2026-04-08T18:00:00Z"},
                "state": {"last_status": "blocked", "last_nonlaunch_reason": "dependency_pending:prep_001"},
                "due": {"eligible": False, "reason": "dependency_pending:prep_001", "due_utc": ""},
                "lifecycle_projection": {"recognized": True, "canonical_state": "blocked"},
            },
            {
                "job_id": "job_completed_once",
                "enabled": True,
                "display_title": "completed once",
                "schedule": {"mode": "once", "run_at": "2026-04-08T18:05:00Z"},
                "state": {"last_status": "ok", "last_reconciliation_reason": "already_executed"},
                "due": {"eligible": False, "reason": "already_executed", "due_utc": "2026-04-08T18:05:00Z"},
                "lifecycle_projection": {"recognized": True, "canonical_state": "succeeded"},
            },
            {
                "job_id": "job_due",
                "enabled": True,
                "display_title": "due now",
                "schedule": {"mode": "once", "run_at": "2026-04-08T18:10:00Z"},
                "state": {"last_status": ""},
                "due": {"eligible": True, "reason": "due", "due_utc": "2026-04-08T18:10:00Z"},
                "lifecycle_projection": {"recognized": False, "canonical_state": ""},
            },
        ]
    )

    assert next_job is not None
    assert next_job["job_id"] == "job_due"
    assert next_job["projected_status"] == "queued"
    assert next_job["next_candidate_bucket"] == "runnable_now"


def test_select_next_scheduler_job_prefers_run_now_over_plain_due() -> None:
    next_job = contract.select_next_scheduler_job(
        [
            {
                "job_id": "job_due",
                "enabled": True,
                "display_title": "due now",
                "schedule": {"mode": "once", "run_at": "2026-04-08T18:10:00Z"},
                "state": {"last_status": ""},
                "due": {"eligible": True, "reason": "due", "due_utc": "2026-04-08T18:10:00Z"},
                "lifecycle_projection": {"recognized": False, "canonical_state": ""},
            },
            {
                "job_id": "job_run_now",
                "enabled": True,
                "display_title": "run now",
                "schedule": {"mode": "once", "run_at": "2026-04-08T20:10:00Z"},
                "state": {"run_now_requested_utc": "2026-04-08T18:09:00Z"},
                "due": {"eligible": True, "reason": "run_now_requested", "due_utc": "2026-04-08T18:09:00Z"},
                "lifecycle_projection": {"recognized": False, "canonical_state": ""},
            },
        ]
    )

    assert next_job is not None
    assert next_job["job_id"] == "job_run_now"
