from __future__ import annotations

from pathlib import Path

from infra.controlplane import scheduled_jobs as sched
from tools.scheduler_graph import validate


def _save_job(tmp_path: Path, spec: dict) -> None:
    sched.save_job(spec, repo_root=tmp_path)


def test_scheduler_graph_validate_orders_dependency_chain(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(validate, "_repo_root", lambda: tmp_path)
    _save_job(
        tmp_path,
        {
            "job_id": "head",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "prompt": "head",
            "schedule": {"mode": "once", "run_at": "2026-03-29T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
    )
    _save_job(
        tmp_path,
        {
            "job_id": "child",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "depends_on": ["head"],
            "prompt": "child",
            "schedule": {"mode": "once", "run_at": "2026-03-29T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
    )

    report = validate.build_scheduler_graph_report(repo_root=tmp_path)

    assert report["priority_order"] == ["head", "child"]
    assert report["blocking_reasons"] == []
    assert (tmp_path / "runtime" / "state" / "scheduler_graph" / "latest_report.json").exists()


def test_scheduler_graph_validate_detects_cycle_and_missing_dependency(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(validate, "_repo_root", lambda: tmp_path)
    _save_job(
        tmp_path,
        {
            "job_id": "job_a",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "depends_on": ["job_b"],
            "prompt": "a",
            "schedule": {"mode": "once", "run_at": "2026-03-29T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
    )
    _save_job(
        tmp_path,
        {
            "job_id": "job_b",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "mb3"},
            "depends_on": ["job_a", "ghost"],
            "prompt": "b",
            "schedule": {"mode": "once", "run_at": "2026-03-29T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
        },
    )

    report = validate.build_scheduler_graph_report(repo_root=tmp_path)

    assert sorted(report["retry_plan"]["cycle_job_ids"]) == ["job_a", "job_b"]
    assert {"job_id": "job_b", "missing_dep": "ghost"} in report["missing_dependencies"]
    assert any(row["reason"] == "cycle_detected" for row in report["blocking_reasons"])
