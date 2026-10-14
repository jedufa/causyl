from __future__ import annotations

from tools.bin import scheduler_start_job as mod


def test_scheduler_start_job_queues_run_now_request(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        mod.sched,
        "list_jobs",
        lambda repo_root=None: [
            {
                "job_id": "demo_job_001",
                "display_title": "Demo job",
                "state": {},
                "due": {"reason": "not_due_yet"},
            }
        ],
    )
    monkeypatch.setattr(
        mod.sched,
        "request_run_now",
        lambda job_id, repo_root=None, request_source="manual": {
            "ok": True,
            "job_id": job_id,
            "queued": True,
            "reason": "run_now_requested",
            "requested_utc": "2026-03-26T23:00:00Z",
        },
    )

    rc = mod.main(["1"])

    out = capsys.readouterr().out
    assert rc == 0
    assert "[start] demo_job_001" in out
    assert "[queued] ok=true queued=true reason=run_now_requested requested_utc=2026-03-26T23:00:00Z" in out
