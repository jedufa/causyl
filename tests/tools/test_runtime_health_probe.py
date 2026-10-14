from __future__ import annotations

import json
from pathlib import Path

from infra.runtime_health import probe


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def test_build_report_classifies_server_down(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(probe, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(probe, "_is_pid_alive", lambda pid: False)
    monkeypatch.setattr(probe, "_http_ready", lambda url: (False, "connection refused"))
    monkeypatch.setattr(probe, "_job_rows", lambda repo_root: [])
    monkeypatch.setattr(probe.maint, "active_job_locks", lambda repo_root=None: [])
    (tmp_path / "runtime" / "state" / "webui").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "state" / "webui" / "webui_server.pid").write_text("123\n", encoding="utf-8")

    report = probe.build_report(repo_root=tmp_path)

    assert report["schema_version"] == "runtime_failure_report_v1"
    assert report["incident_classification"]["severity"] == "critical"
    assert "server_not_running" in report["incident_classification"]["codes"]
    assert report["recommended_actions"][0] == "start_server"
    assert (tmp_path / "runtime" / "state" / "runtime_health" / "latest_report.json").exists()


def test_build_report_classifies_stale_claims_and_blocked_jobs(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(probe, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(probe, "_is_pid_alive", lambda pid: True)
    monkeypatch.setattr(probe, "_http_ready", lambda url: (True, ""))
    monkeypatch.setattr(
        probe,
        "_job_rows",
        lambda repo_root: [
            {"job_id": "a_job", "state": {"active_claim_stale": True, "active_run_id": "sj_1", "active_claim_present": False, "last_status": "blocked"}},
            {"job_id": "b_job", "state": {"last_status": "ok"}},
        ],
    )
    monkeypatch.setattr(probe.maint, "active_job_locks", lambda repo_root=None: [{"job_id": "a_job", "path": "/tmp/a.lock"}])
    (tmp_path / "runtime" / "state" / "webui").mkdir(parents=True, exist_ok=True)
    (tmp_path / "runtime" / "state" / "webui" / "webui_server.pid").write_text("123\n", encoding="utf-8")

    report = probe.build_report(repo_root=tmp_path)

    assert report["incident_classification"]["severity"] == "critical"
    assert "stale_claims" in report["incident_classification"]["codes"]
    assert "orphan_running_state" in report["incident_classification"]["codes"]
    assert "run_scheduler_watchdog_selfheal" in report["recommended_actions"]
    assert "clear_orphan_running_state" in report["recommended_actions"]
