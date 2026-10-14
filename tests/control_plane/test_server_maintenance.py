from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import server_maintenance as maint


def _save_job(repo_root: Path, job_id: str, *, enabled: bool = True) -> None:
    sched.save_job(
        {
            "job_id": job_id,
            "enabled": enabled,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "hello",
            "schedule": {"mode": "once", "run_at": "2026-03-13T09:25:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
        repo_root=repo_root,
    )


def test_pause_and_resume_only_unfinished_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_job(tmp_path, "job_ok")
    _save_job(tmp_path, "job_blocked")
    _save_job(tmp_path, "job_never_run")
    sched._write_json_atomic(sched._state_path("job_ok", repo_root=tmp_path), {"last_status": "ok"})
    sched._write_json_atomic(sched._state_path("job_blocked", repo_root=tmp_path), {"last_status": "blocked"})

    paused = maint.pause_unfinished_jobs(repo_root=tmp_path)

    assert paused["paused_job_ids"] == ["job_blocked", "job_never_run"]
    assert sched.load_job("job_ok", repo_root=tmp_path)["enabled"] is True
    assert sched.load_job("job_blocked", repo_root=tmp_path)["enabled"] is False
    assert sched.load_job("job_never_run", repo_root=tmp_path)["enabled"] is False

    sched._write_json_atomic(sched._state_path("job_blocked", repo_root=tmp_path), {"last_status": "ok"})
    resumed = maint.resume_paused_jobs(repo_root=tmp_path)

    assert resumed["resumed_job_ids"] == ["job_never_run"]
    assert resumed["skipped_ok_job_ids"] == ["job_blocked"]
    assert sched.load_job("job_never_run", repo_root=tmp_path)["enabled"] is True


def test_active_job_locks_reads_lock_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_job(tmp_path, "job_123")
    lock_dir = sched.locks_dir(tmp_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = lock_dir / "job_123.lock.json"
    lock.write_text(
        json.dumps(
            {
                "job_id": "job_123",
                "claimed_utc": "2026-03-13T13:35:43Z",
                "heartbeat_utc": "2026-03-13T13:36:10Z",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-13T13:36:30Z"))

    locks = maint.active_job_locks(repo_root=tmp_path)

    assert locks == [
        {
            "path": str(lock),
            "job_id": "job_123",
            "claimed_utc": "2026-03-13T13:35:43Z",
            "heartbeat_utc": "2026-03-13T13:36:10Z",
        }
    ]


def test_active_job_locks_ignores_stale_claim_files(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_job(tmp_path, "job_stale")
    lock_dir = sched.locks_dir(tmp_path)
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock = lock_dir / "job_stale.lock.json"
    lock.write_text(
        json.dumps(
            {
                "job_id": "job_stale",
                "claimed_utc": "2026-03-13T13:00:00Z",
                "heartbeat_utc": "2026-03-13T13:01:00Z",
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(sched, "_utc_now", lambda: sched._parse_utc("2026-03-13T13:30:00Z"))

    snapshot = maint.job_lock_snapshot(repo_root=tmp_path)

    assert snapshot["active_locks"] == []
    assert snapshot["stale_locks"] == [
        {
            "path": str(lock),
            "job_id": "job_stale",
            "claimed_utc": "2026-03-13T13:00:00Z",
            "heartbeat_utc": "2026-03-13T13:01:00Z",
            "stale": True,
            "stale_reason": "stale_heartbeat",
        }
    ]


def test_pause_does_not_clobber_existing_manifest_when_no_new_jobs_are_paused(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_job(tmp_path, "job_stranded", enabled=False)
    sched._write_json_atomic(sched._state_path("job_stranded", repo_root=tmp_path), {"last_status": "blocked"})
    maint.pause_manifest_path(repo_root=tmp_path).parent.mkdir(parents=True, exist_ok=True)
    maint.pause_manifest_path(repo_root=tmp_path).write_text(
        json.dumps(
            {
                "schema_version": "scheduler_pause_state_v1",
                "paused_job_ids": ["job_stranded"],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    paused = maint.pause_unfinished_jobs(repo_root=tmp_path)

    assert paused["paused_job_ids"] == []
    assert paused["manifest_paused_job_ids"] == ["job_stranded"]
    manifest = maint.load_pause_manifest(repo_root=tmp_path)
    assert manifest["paused_job_ids"] == ["job_stranded"]
