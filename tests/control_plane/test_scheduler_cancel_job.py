from __future__ import annotations

import builtins

from tools.bin import scheduler_cancel_job as mod


def test_scheduler_cancel_job_cancels_selected_active_job(monkeypatch, capsys) -> None:
    rows = [
        {
            "job_id": "job_active_001",
            "display_title": "Active Job",
            "state": {"active_run_id": "sj_001"},
            "due": {},
            "created_utc": "2026-03-27T09:00:00Z",
        },
        {
            "job_id": "job_idle_001",
            "display_title": "Idle Job",
            "state": {"active_run_id": ""},
            "due": {},
            "created_utc": "2026-03-27T08:00:00Z",
        },
    ]
    monkeypatch.setattr(mod.sched, "list_jobs", lambda repo_root=None: rows)
    monkeypatch.setattr(
        mod.sched,
        "cancel_job",
        lambda job_id, repo_root=None, reason="": {
            "ok": True,
            "job_id": job_id,
            "status": "cancelled",
            "receipt_path": f"/tmp/{job_id}.json",
        },
    )

    rc = mod.main(["1", "--yes", "--backend", "legacy"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "[cancel] ok=true job_id=job_active_001 status=cancelled" in out


def test_scheduler_cancel_job_returns_1_when_no_active_jobs(monkeypatch, capsys) -> None:
    monkeypatch.setattr(mod.sched, "list_jobs", lambda repo_root=None: [])
    rc = mod.main(["--yes", "--backend", "legacy"])
    out = capsys.readouterr().out
    assert rc == 1
    assert "No active scheduled jobs found." in out


def test_scheduler_cancel_job_interactive_lists_and_defaults_to_first(monkeypatch, capsys) -> None:
    rows = [
        {
            "job_id": "job_active_001",
            "display_title": "First Active Job",
            "state": {"active_run_id": "sj_001"},
            "due": {},
            "created_utc": "2026-03-27T09:00:00Z",
        },
        {
            "job_id": "job_active_002",
            "display_title": "Second Active Job",
            "state": {"active_run_id": "sj_002"},
            "due": {},
            "created_utc": "2026-03-27T08:00:00Z",
        },
    ]
    monkeypatch.setattr(mod.sched, "list_jobs", lambda repo_root=None: rows)
    monkeypatch.setattr(builtins, "input", lambda prompt="": "")
    monkeypatch.setattr(
        mod.sched,
        "cancel_job",
        lambda job_id, repo_root=None, reason="": {
            "ok": True,
            "job_id": job_id,
            "status": "cancelled",
            "receipt_path": f"/tmp/{job_id}.json",
        },
    )

    rc = mod.main(["--yes", "--backend", "legacy"])
    out = capsys.readouterr().out
    assert rc == 0
    assert "Active jobs" in out
    assert "1. First Active Job [job_active_001]" in out
    assert "2. Second Active Job [job_active_002]" in out
    assert "[cancel] ok=true job_id=job_active_001 status=cancelled" in out
