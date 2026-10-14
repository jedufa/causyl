from __future__ import annotations

from tools.bin import scheduler_analyze_job
from tools.bin import scheduler_change_job_model
from tools.bin import scheduler_recovery_assistant
from tools.bin import scheduler_start_job
from tools.bin import scheduler_verify_job
from tools.bin import scheduler_watchdog


def test_scheduler_analyze_job_returns_130_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(scheduler_analyze_job.argparse.ArgumentParser, "parse_args", lambda self, argv=None: (_ for _ in ()).throw(KeyboardInterrupt()))
    rc = scheduler_analyze_job.main([])
    assert rc == 130
    assert "Canceled." in capsys.readouterr().err


def test_scheduler_change_job_model_returns_130_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(scheduler_change_job_model.argparse.ArgumentParser, "parse_args", lambda self: (_ for _ in ()).throw(KeyboardInterrupt()))
    rc = scheduler_change_job_model.main()
    assert rc == 130
    assert "Canceled." in capsys.readouterr().err


def test_scheduler_start_job_returns_130_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(scheduler_start_job.argparse.ArgumentParser, "parse_args", lambda self, argv=None: (_ for _ in ()).throw(KeyboardInterrupt()))
    rc = scheduler_start_job.main([])
    assert rc == 130
    assert "Canceled." in capsys.readouterr().err


def test_scheduler_verify_job_returns_130_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(scheduler_verify_job.argparse.ArgumentParser, "parse_args", lambda self, argv=None: (_ for _ in ()).throw(KeyboardInterrupt()))
    rc = scheduler_verify_job.main([])
    assert rc == 130
    assert "Canceled." in capsys.readouterr().err


def test_scheduler_watchdog_returns_130_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(scheduler_watchdog, "_parse_args", lambda: (_ for _ in ()).throw(KeyboardInterrupt()))
    rc = scheduler_watchdog.main()
    assert rc == 130
    assert "Canceled." in capsys.readouterr().err


def test_scheduler_recovery_assistant_returns_130_on_keyboard_interrupt(monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        scheduler_recovery_assistant.argparse.ArgumentParser,
        "parse_args",
        lambda self, argv=None: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    rc = scheduler_recovery_assistant.main([])
    assert rc == 130
    assert "Canceled." in capsys.readouterr().err
