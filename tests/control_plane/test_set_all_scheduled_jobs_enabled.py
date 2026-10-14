from __future__ import annotations

import json
from pathlib import Path

from tools.bin import set_all_scheduled_jobs_enabled as bulk
from infra.controlplane import scheduled_jobs as sched


def _save_job(repo_root: Path, job_id: str, *, enabled: bool) -> None:
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


def test_bulk_disable_and_enable_with_prefix(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_job(tmp_path, "overnight_one", enabled=True)
    _save_job(tmp_path, "overnight_two", enabled=False)
    _save_job(tmp_path, "other_three", enabled=True)

    rc = bulk.main(["--enabled", "false", "--prefix", "overnight"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed_job_ids"] == ["overnight_one"]
    assert sched.load_job("overnight_one", repo_root=tmp_path)["enabled"] is False
    assert sched.load_job("overnight_two", repo_root=tmp_path)["enabled"] is False
    assert sched.load_job("other_three", repo_root=tmp_path)["enabled"] is True

    rc = bulk.main(["--enabled", "true", "--prefix", "overnight"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed_job_ids"] == ["overnight_one", "overnight_two"]
    assert sched.load_job("overnight_one", repo_root=tmp_path)["enabled"] is True
    assert sched.load_job("overnight_two", repo_root=tmp_path)["enabled"] is True


def test_bulk_toggle_dry_run_does_not_write(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _save_job(tmp_path, "overnight_one", enabled=True)

    rc = bulk.main(["--enabled", "false", "--dry-run"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["changed_job_ids"] == ["overnight_one"]
    assert sched.load_job("overnight_one", repo_root=tmp_path)["enabled"] is True
