from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_resume as resume
from infra.controlplane import scheduled_jobs as sched


def _save_job(tmp_path: Path, *, job_id: str, title: str, last_started_utc: str) -> None:
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "single_turn",
            "enabled": False,
            "role": "generalist",
            "display_title": title,
            "binding": {"type": "task_id", "id": "a7k"},
            "prompt": "noop",
            "schedule": {"mode": "once", "run_at": "2026-03-27T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": {"agent_tech": "codex", "model": "gpt-5.3-codex-spark"},
        },
        repo_root=tmp_path,
    )
    sched._write_state(
        job_id,
        {
            "job_id": job_id,
            "active_run_id": "",
            "active_started_utc": "",
            "last_run_id": f"sj_{job_id}",
            "last_started_utc": last_started_utc,
            "last_finished_utc": last_started_utc,
            "last_status": "blocked",
            "last_trigger": "startup",
        },
        repo_root=tmp_path,
    )


def test_manifest_rows_are_sorted_most_recent_first(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(resume, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    _save_job(tmp_path, job_id="job_old", title="Old Job", last_started_utc="2026-03-27T05:00:00Z")
    _save_job(tmp_path, job_id="job_new", title="New Job", last_started_utc="2026-03-27T06:00:00Z")
    manifest_path = tmp_path / "runtime" / "state" / "webui" / "scheduler_pipeline_pause_state.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"enabled_job_ids_before_pause": ["job_old", "job_new"]}), encoding="utf-8")

    _manifest_path, rows, missing = resume._manifest_rows(repo_root=tmp_path)
    assert missing == []
    assert [str(row.get("job_id") or "") for row in rows] == ["job_new", "job_old"]


def test_main_resumes_selected_jobs_and_removes_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(resume, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    _save_job(tmp_path, job_id="job_old", title="Old Job", last_started_utc="2026-03-27T05:00:00Z")
    _save_job(tmp_path, job_id="job_new", title="New Job", last_started_utc="2026-03-27T06:00:00Z")
    manifest_path = tmp_path / "runtime" / "state" / "webui" / "scheduler_pipeline_pause_state.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps({"enabled_job_ids_before_pause": ["job_old", "job_new"]}), encoding="utf-8")

    rc = resume.main(["1"])
    assert rc == 0
    assert not manifest_path.exists()
    assert sched.load_job("job_new", repo_root=tmp_path)["enabled"] is True
    assert sched.load_job("job_old", repo_root=tmp_path)["enabled"] is False
