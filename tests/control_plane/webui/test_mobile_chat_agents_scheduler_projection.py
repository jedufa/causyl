from __future__ import annotations

import pytest

from infra.webui import server_routes_and_main as routes


def test_plane_mobile_direct_reply_ignores_blocked_earlier_job_for_next_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "list_jobs",
        lambda: [
            {
                "job_id": "job_a",
                "enabled": True,
                "display_title": "job a",
                "schedule": {"mode": "once", "run_at": "2026-03-30T20:00:00Z"},
                "state": {"last_status": "blocked", "last_nonlaunch_reason": "dependency_pending:prep_001"},
                "due": {"eligible": False, "reason": "dependency_pending:prep_001", "due_utc": ""},
                "lifecycle_projection": {"recognized": True, "canonical_state": "blocked"},
            },
            {
                "job_id": "job_b",
                "enabled": True,
                "display_title": "job b",
                "schedule": {"mode": "once", "run_at": "2026-03-31T20:00:00Z"},
                "state": {"last_status": ""},
                "due": {"eligible": True, "reason": "due", "due_utc": "2026-03-31T20:00:00Z"},
                "lifecycle_projection": {"recognized": False, "canonical_state": ""},
            },
        ],
    )

    reply = routes._plane_mobile_direct_reply("what is the next scheduled job?")

    assert reply is not None
    assert "job_b" in reply
    assert "job_a" not in reply


def test_plane_mobile_direct_reply_counts_blocked_jobs_from_shared_projection_not_raw_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "list_jobs",
        lambda: [
            {
                "job_id": "job_done",
                "enabled": True,
                "display_title": "job done",
                "schedule": {"mode": "once", "run_at": "2026-03-30T20:00:00Z"},
                "state": {"last_status": "blocked", "last_nonlaunch_reason": "dependency_pending:stale_001"},
                "due": {"eligible": False, "reason": "already_executed", "due_utc": "2026-03-30T20:00:00Z"},
                "lifecycle_projection": {"recognized": True, "canonical_state": "succeeded"},
                "scheduler_projection_v1": {
                    "projected_status": "completed_once",
                    "projection_reason": "already_executed",
                    "next_candidate_bucket": "none",
                    "next_due_utc": "2026-03-30T20:00:00Z",
                },
            },
            {
                "job_id": "job_blocked",
                "enabled": True,
                "display_title": "job blocked",
                "schedule": {"mode": "once", "run_at": "2026-03-31T20:00:00Z"},
                "state": {"last_status": "ok"},
                "due": {"eligible": False, "reason": "dependency_pending:prep_001", "due_utc": ""},
                "lifecycle_projection": {"recognized": True, "canonical_state": "blocked"},
                "scheduler_projection_v1": {
                    "projected_status": "blocked",
                    "projection_reason": "dependency_pending:prep_001",
                    "next_candidate_bucket": "none",
                    "next_due_utc": "",
                },
            },
            {
                "job_id": "job_next",
                "enabled": True,
                "display_title": "job next",
                "schedule": {"mode": "once", "run_at": "2026-04-01T20:00:00Z"},
                "state": {"last_status": ""},
                "due": {"eligible": True, "reason": "due", "due_utc": "2026-04-01T20:00:00Z"},
                "lifecycle_projection": {"recognized": False, "canonical_state": ""},
                "scheduler_projection_v1": {
                    "projected_status": "queued",
                    "projection_reason": "due",
                    "due_reason": "due",
                    "next_candidate_bucket": "runnable_now",
                    "next_due_utc": "2026-04-01T20:00:00Z",
                },
            },
        ],
    )

    reply = routes._plane_mobile_direct_reply("how many jobs are in the queue?")

    assert reply is not None
    assert "3 job specs" in reply
    assert "3 are enabled" in reply
    assert "1 are blocked" in reply
    assert "job_next" in reply
