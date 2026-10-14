from __future__ import annotations

import json
from pathlib import Path

from pathlib import Path

from infra.controlplane import scheduled_jobs as sched


def _fake_send_message(*, role: str, binding: dict, message: str, options: dict | None = None) -> dict:
    return {
        "ok": True,
        "assistant": {
            "content": f"role={role} binding={binding['type']}:{binding['id']} prompt={message}",
            "status": "ok",
            "meta": {"options": dict(options or {})},
        },
    }


def test_request_run_now_makes_future_job_due_now(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "manual_same_scheduler_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "m11"},
            "prompt": "Run through the normal scheduler path.",
            "schedule": {"mode": "once", "run_at": "2026-03-30T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    requested = sched.request_run_now(
        "manual_same_scheduler_001",
        repo_root=tmp_path,
        requested_utc="2026-03-26T23:30:00Z",
        request_source="manual",
    )

    assert requested["ok"] is True
    summary = sched.list_jobs(repo_root=tmp_path, now_utc="2026-03-26T23:30:01Z")[0]
    assert summary["due"]["eligible"] is True
    assert summary["due"]["reason"] == "run_now_requested"
    assert summary["state"]["run_now_requested_utc"] == "2026-03-26T23:30:00Z"


def test_scheduler_tick_executes_run_now_request_via_normal_scheduler_path(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "manual_same_scheduler_002",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "m12"},
            "prompt": "Run through scheduler_tick after run-now request.",
            "schedule": {"mode": "once", "run_at": "2026-03-30T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        }
    )

    queued = sched.request_run_now(
        "manual_same_scheduler_002",
        repo_root=tmp_path,
        requested_utc="2026-03-26T23:31:00Z",
        request_source="manual",
    )
    assert queued["ok"] is True

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-26T23:31:05Z")

    assert result["submitted_count"] == 1
    assert result["submitted"][0]["job_id"] == "manual_same_scheduler_002"
    state = sched.load_state("manual_same_scheduler_002", repo_root=tmp_path)
    assert state["last_status"] == "ok"
    assert state["run_now_requested_utc"] == ""
    assert Path(state["last_receipt_path"]).exists()


def test_request_run_now_recovers_stale_active_run_before_queueing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "manual_stale_recovery_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "m13"},
            "prompt": "Recover stale active run before queueing.",
            "schedule": {"mode": "once", "run_at": "2026-03-30T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        }
    )

    sched._write_state(
        "manual_stale_recovery_001",
        {
            "job_id": "manual_stale_recovery_001",
            "active_run_id": "sj_stale_001",
            "active_started_utc": "2026-03-26T20:00:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    claim_path = sched._claim_path("manual_stale_recovery_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "manual_stale_recovery_001",
                "run_id": "sj_stale_001",
                "pid": 999999,
                "claimed_utc": "2026-03-26T20:00:00Z",
                "heartbeat_utc": "2026-03-26T20:00:00Z",
            }
        ),
        encoding="utf-8",
    )

    requested = sched.request_run_now(
        "manual_stale_recovery_001",
        repo_root=tmp_path,
        requested_utc="2026-03-26T23:30:00Z",
        request_source="manual",
    )

    assert requested["ok"] is True
    state = sched.load_state("manual_stale_recovery_001", repo_root=tmp_path)
    assert state["active_run_id"] == ""
    assert state["last_status"] == "interrupted"
    assert state["run_now_requested_utc"] == "2026-03-26T23:30:00Z"


def test_request_run_now_recovers_dead_active_run_with_fresh_heartbeat(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    sched.save_job(
        {
            "job_id": "manual_dead_pid_recovery_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "m14"},
            "prompt": "Recover dead active run with fresh heartbeat.",
            "schedule": {"mode": "once", "run_at": "2026-03-30T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
        }
    )

    sched._write_state(
        "manual_dead_pid_recovery_001",
        {
            "job_id": "manual_dead_pid_recovery_001",
            "active_run_id": "sj_dead_001",
            "active_started_utc": "2026-03-26T23:20:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    claim_path = sched._claim_path("manual_dead_pid_recovery_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "manual_dead_pid_recovery_001",
                "run_id": "sj_dead_001",
                "pid": 999999,
                "claimed_utc": "2026-03-26T23:20:00Z",
                "heartbeat_utc": "2026-03-26T23:20:00Z",
            }
        ),
        encoding="utf-8",
    )

    requested = sched.request_run_now(
        "manual_dead_pid_recovery_001",
        repo_root=tmp_path,
        requested_utc="2026-03-26T23:30:00Z",
        request_source="manual",
    )

    assert requested["ok"] is True
    state = sched.load_state("manual_dead_pid_recovery_001", repo_root=tmp_path)
    assert state["active_run_id"] == ""
    assert state["last_status"] == "interrupted"
    assert state["run_now_requested_utc"] == "2026-03-26T23:30:00Z"


def test_scheduler_tick_cleans_dead_claim_with_fresh_heartbeat(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._agents, "send_message", _fake_send_message)

    sched.save_job(
        {
            "job_id": "manual_dead_claim_tick_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "m15"},
            "prompt": "Recover dead claim and execute via scheduler tick.",
            "schedule": {"mode": "once", "run_at": "2026-03-30T03:30:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
        }
    )

    sched._write_state(
        "manual_dead_claim_tick_001",
        {
            "job_id": "manual_dead_claim_tick_001",
            "active_run_id": "sj_dead_tick_001",
            "active_started_utc": "2026-03-26T23:20:00Z",
            "last_status": "running",
            "run_now_requested_utc": "2026-03-26T23:30:00Z",
            "run_now_request_run_id": "run_now_existing_001",
            "run_now_request_source": "manual",
        },
        repo_root=tmp_path,
    )
    claim_path = sched._claim_path("manual_dead_claim_tick_001", repo_root=tmp_path)
    claim_path.parent.mkdir(parents=True, exist_ok=True)
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "manual_dead_claim_tick_001",
                "run_id": "sj_dead_tick_001",
                "pid": 999999,
                "claimed_utc": "2026-03-26T23:20:00Z",
                "heartbeat_utc": "2026-03-26T23:20:00Z",
            }
        ),
        encoding="utf-8",
    )

    result = sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-03-26T23:30:05Z")

    assert result["submitted_count"] == 0
    assert result["skipped"] == [{"job_id": "manual_dead_claim_tick_001", "reason": "not_due_yet"}]
    state = sched.load_state("manual_dead_claim_tick_001", repo_root=tmp_path)
    assert state["active_run_id"] == ""
    assert state["last_status"] == "interrupted"
    assert state["run_now_requested_utc"] == ""
    assert not claim_path.exists()
