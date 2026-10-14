from __future__ import annotations

from tools.bin import scheduler_postpone_active_job as postpone


def test_main_returns_1_when_no_active_job(monkeypatch, capsys) -> None:
    monkeypatch.setattr(postpone.sched, "list_jobs", lambda: [])
    monkeypatch.setattr(postpone.argparse.ArgumentParser, "parse_args", lambda self: type("Args", (), {
        "job_id": "",
        "days": 7,
        "no_resume_next": False,
        "dry_run": False,
    })())

    rc = postpone.main()

    assert rc == 1
    assert "no active scheduled job found" in capsys.readouterr().err
