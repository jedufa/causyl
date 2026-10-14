from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import scheduled_jobs as sched


def _swarm_spec(*, mission_type: str = "delivery_patch", allowed_paths: list[str] | None = None) -> dict:
    return {
        "mission_type": mission_type,
        "objective": "Patch the scheduler seam safely.",
        "scope": "focused",
        "expected_outputs": ["unified_diff", "validation_commands"],
        "success_contract": {
            "accepted_statuses": ["finished"],
            "require_changed_files": True,
        },
        "evidence_requirements": {
            "require_summary_path": True,
            "require_worker_artifacts": False,
        },
        "write_policy": {
            "mode": "bounded_patch",
            "allowed_paths": list(allowed_paths or ["infra/controlplane"]),
            "max_changed_files": 5,
        },
        "launch_options": {
            "run_async": False,
            "started_by_role": "generalist",
        },
    }


def _job_spec(*, swarm: dict | None = None) -> dict:
    return {
        "job_id": "swarm_launch_job_001",
        "enabled": True,
        "job_kind": "swarm_launch",
        "binding": {"type": "task_id", "id": "task_swarm_001"},
        "schedule": {"mode": "once", "run_at": "2026-04-01T12:00:00Z", "timezone": "UTC"},
        "expected_output": {"kind": "report", "format": "markdown"},
        "policy": {"max_runtime_seconds": 60},
        "runtime": {"executor_agent_tech": "codex", "execution_mode": "controller_only"},
        "swarm": swarm or _swarm_spec(),
    }


def test_swarm_launch_save_and_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    saved = sched.save_job(_job_spec(), repo_root=tmp_path)
    loaded = sched.load_job("swarm_launch_job_001", repo_root=tmp_path)

    assert saved["job_kind"] == "swarm_launch"
    assert loaded["job_kind"] == "swarm_launch"
    assert loaded["swarm"]["mission_type"] == "delivery_patch"
    assert loaded["swarm"]["write_policy"]["allowed_paths"] == ["infra/controlplane"]
    assert loaded["swarm"]["retry_policy"] == {"child_relaunch_limit": 1, "master_rerun_limit": 0}
    assert loaded["swarm"]["fanout_contract"]["uid"] == "PIS-20260409-082100Z-A1F1"
    assert loaded["prompt"].startswith("[Scheduled Swarm Launch Contract]")
    assert "fanout_contract.master_owner: fanout_master_release_one" in loaded["prompt"]
    assert "mission_type: delivery_patch" in loaded["prompt"]
    assert "retry_policy.child_relaunch_limit: 1" in loaded["prompt"]


def test_swarm_launch_execute_writes_truthful_receipt_and_report(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agent_swarm,
        "start_swarm",
        lambda **kwargs: {
            "ok": True,
            "swarm_id": "swarm_demo_001",
            "state": {
                "status": "running",
                "phase": "makers",
                "integration": {},
                "artifacts": {},
                "workers": [],
                "token_usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
            },
        },
    )
    monkeypatch.setattr(
        sched._agent_swarm,
        "get_status",
        lambda swarm_id: {
            "ok": True,
            "swarm_id": swarm_id,
            "state": {
                "status": "finished",
                "phase": "completed",
                "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
                "artifacts": {"summary_path": "/tmp/swarm_summary.md"},
                "token_usage": {"input_tokens": 700, "output_tokens": 300, "total_tokens": 1000},
                "workers": [
                    {
                        "worker_id": "maker_1",
                        "summary_path": "/tmp/worker_summary.md",
                    }
                ],
            },
        },
    )

    sched.save_job(_job_spec(), repo_root=tmp_path)
    result = sched.execute_job(
        "swarm_launch_job_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-01T12:01:00Z",
    )

    assert result["ok"] is True
    receipt = sched.load_receipt("swarm_launch_job_001", result["run_id"], repo_root=tmp_path)
    assert receipt["job_kind"] == "swarm_launch"
    assert receipt["status"] == "ok"
    assert receipt["swarm"]["swarm_id"] == "swarm_demo_001"
    assert receipt["swarm"]["token_usage"]["total_tokens"] == 1000
    assert receipt["swarm"]["evaluation"]["ok"] is True
    assert receipt["swarm"]["fanout_projection"]["fanout_stage"] == "terminal_reporting"
    assert receipt["swarm"]["fanout_projection"]["master_owner"] == "fanout_master_release_one"
    assert receipt["swarm"]["integrated_result"]["classification"] == "complete_success"
    assert receipt["swarm"]["fanout_projection"]["integration_disposition"] == "integrated_clean"
    assert receipt["swarm"]["fanout_projection"]["final_verdict"] == "success_deterministic"
    assert receipt["swarm"]["fanout_projection"]["restart_reconciliation"]["status"] == "terminal_state_reconciled"
    assert receipt["swarm"]["fanout_projection"]["retry_boundaries"]["child_relaunch"]["state"] == "not_needed"
    assert receipt["swarm"]["evaluation"]["changed_files"] == ["infra/controlplane/scheduled_jobs.py"]
    assert receipt["swarm"]["evaluation"]["summary_path"] == "/tmp/swarm_summary.md"
    assert receipt["swarm"]["evaluation"]["token_usage"]["total_tokens"] == 1000

    report_path = Path(result["report_path"])
    assert report_path.exists()
    report_text = report_path.read_text(encoding="utf-8")
    assert "swarm_id: swarm_demo_001" in report_text
    assert "final_swarm_status: finished" in report_text
    assert "fanout_stage: terminal_reporting" in report_text
    assert "integrated_classification: complete_success" in report_text
    assert "restart_reconciliation_status: terminal_state_reconciled" in report_text
    assert "master_rerun_state: not_needed" in report_text


def test_swarm_launch_rejects_off_target_scope_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agent_swarm,
        "start_swarm",
        lambda **kwargs: {
            "ok": True,
            "swarm_id": "swarm_demo_scope",
            "state": {"status": "running", "phase": "makers"},
        },
    )
    monkeypatch.setattr(
        sched._agent_swarm,
        "get_status",
        lambda swarm_id: {
            "ok": True,
            "swarm_id": swarm_id,
            "state": {
                "status": "finished",
                "phase": "completed",
                "integration": {"changed_files": ["infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminScreen.kt"]},
                "artifacts": {"summary_path": "/tmp/swarm_summary.md"},
                "workers": [],
            },
        },
    )

    sched.save_job(_job_spec(), repo_root=tmp_path)
    result = sched.execute_job(
        "swarm_launch_job_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-01T12:01:00Z",
    )

    assert result["ok"] is False
    receipt = sched.load_receipt("swarm_launch_job_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "off_target_scope"
    assert receipt["swarm"]["evaluation"]["off_target_paths"] == [
        "infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AdminScreen.kt"
    ]
    assert receipt["swarm"]["integrated_result"]["classification"] == "master_blocked_outcome"
    assert receipt["swarm"]["integrated_result"]["integration_disposition"] == "master_blocked"
    assert receipt["swarm"]["integrated_result"]["final_verdict"] == "blocked_requires_review"
    assert receipt["swarm"]["fanout_projection"]["integration_disposition"] == "master_blocked"
    assert receipt["swarm"]["fanout_projection"]["final_verdict"] == "blocked_requires_review"
    assert "outside allowed_paths" in str(receipt.get("error") or "")


def test_swarm_launch_fails_closed_on_invalid_mission_type(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)

    with pytest.raises(ValueError, match="swarm.mission_type must be one of"):
        sched.save_job(
            _job_spec(
                swarm=_swarm_spec(mission_type="totally_freeform"),
            ),
            repo_root=tmp_path,
        )


def test_swarm_launch_rejects_semantically_partial_finished_swarm(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agent_swarm,
        "start_swarm",
        lambda **kwargs: {
            "ok": True,
            "swarm_id": "swarm_demo_partial_truth",
            "state": {"status": "running", "phase": "makers"},
        },
    )
    monkeypatch.setattr(
        sched._agent_swarm,
        "get_status",
        lambda swarm_id: {
            "ok": True,
            "swarm_id": swarm_id,
            "state": {
                "status": "finished",
                "phase": "completed",
                "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
                "artifacts": {"summary_path": "/tmp/swarm_summary.md"},
                "workers": [
                    {
                        "worker_id": "maker_1",
                        "status": "delivered_with_known_gaps",
                        "known_gaps": ["wrapper salvage"],
                        "summary_path": "/tmp/worker_summary.md",
                    }
                ],
            },
        },
    )

    sched.save_job(_job_spec(), repo_root=tmp_path)
    result = sched.execute_job(
        "swarm_launch_job_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-01T12:01:00Z",
    )

    assert result["ok"] is False
    receipt = sched.load_receipt("swarm_launch_job_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "semantic_partial"
    assert receipt["swarm"]["integrated_result"]["classification"] == "partial_success"
    assert receipt["swarm"]["integrated_result"]["rerun_child_ids"] == []
    assert receipt["swarm"]["evaluation"]["semantically_partial_workers"] == ["maker_1"]
    assert receipt["swarm"]["fanout_projection"]["restart_reconciliation"]["status"] == "partial_collection_reconciled"


def test_swarm_launch_fails_closed_when_total_tokens_exceed_cap(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agent_swarm,
        "start_swarm",
        lambda **kwargs: {
            "ok": True,
            "swarm_id": "swarm_demo_tokens",
            "state": {"status": "running", "phase": "makers"},
        },
    )
    monkeypatch.setattr(
        sched._agent_swarm,
        "get_status",
        lambda swarm_id: {
            "ok": True,
            "swarm_id": swarm_id,
            "state": {
                "status": "finished",
                "phase": "completed",
                "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
                "artifacts": {"summary_path": "/tmp/swarm_summary.md"},
                "token_usage": {"input_tokens": 700, "output_tokens": 350, "total_tokens": 1050},
                "workers": [
                    {
                        "worker_id": "maker_1",
                        "status": "delivered",
                        "summary_path": "/tmp/worker_summary.md",
                    }
                ],
            },
        },
    )

    spec = _job_spec(swarm=_swarm_spec())
    spec["swarm"]["launch_options"]["max_total_tokens"] = 1000
    sched.save_job(spec, repo_root=tmp_path)
    result = sched.execute_job(
        "swarm_launch_job_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-01T12:01:00Z",
    )

    assert result["ok"] is False
    receipt = sched.load_receipt("swarm_launch_job_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "token_budget_exceeded"
    assert receipt["swarm"]["evaluation"]["token_usage"]["total_tokens"] == 1050
    assert "exceeds max_total_tokens=1000" in str(receipt.get("error") or "")


def test_swarm_launch_rejects_child_claimed_master_verdict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agent_swarm,
        "start_swarm",
        lambda **kwargs: {
            "ok": True,
            "swarm_id": "swarm_demo_drift",
            "state": {"status": "running", "phase": "makers"},
        },
    )
    monkeypatch.setattr(
        sched._agent_swarm,
        "get_status",
        lambda swarm_id: {
            "ok": True,
            "swarm_id": swarm_id,
            "state": {
                "status": "finished",
                "phase": "completed",
                "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
                "artifacts": {"summary_path": "/tmp/swarm_summary.md"},
                "workers": [
                    {
                        "worker_id": "maker_1",
                        "status": "delivered",
                        "summary_path": "/tmp/worker_summary.md",
                        "final_verdict": "PASS",
                    }
                ],
            },
        },
    )

    sched.save_job(_job_spec(), repo_root=tmp_path)
    result = sched.execute_job(
        "swarm_launch_job_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-01T12:01:00Z",
    )

    assert result["ok"] is False
    receipt = sched.load_receipt("swarm_launch_job_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "ownership_drift"
    assert receipt["swarm"]["integrated_result"]["classification"] == "conflicting_outputs"
    assert receipt["swarm"]["evaluation"]["ownership_drift"] == [
        {"worker_id": "maker_1", "forbidden_claims": ["final_verdict"]}
    ]


def test_swarm_launch_rejects_overlapping_kept_child_outputs_as_conflict(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(
        sched._agent_swarm,
        "start_swarm",
        lambda **kwargs: {
            "ok": True,
            "swarm_id": "swarm_demo_conflict_overlap",
            "state": {"status": "running", "phase": "makers"},
        },
    )
    monkeypatch.setattr(
        sched._agent_swarm,
        "get_status",
        lambda swarm_id: {
            "ok": True,
            "swarm_id": swarm_id,
            "state": {
                "status": "finished",
                "phase": "completed",
                "integration": {"changed_files": ["infra/controlplane/scheduled_jobs.py"]},
                "artifacts": {"summary_path": "/tmp/swarm_summary.md"},
                "workers": [
                    {
                        "worker_id": "maker_1",
                        "status": "delivered",
                        "changed_files": ["infra/controlplane/scheduled_jobs.py"],
                        "summary_path": "/tmp/worker_1.md",
                    },
                    {
                        "worker_id": "maker_2",
                        "status": "delivered",
                        "changed_files": ["infra/controlplane/scheduled_jobs.py"],
                        "summary_path": "/tmp/worker_2.md",
                    },
                ],
            },
        },
    )

    sched.save_job(_job_spec(), repo_root=tmp_path)
    result = sched.execute_job(
        "swarm_launch_job_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-01T12:01:00Z",
    )

    assert result["ok"] is False
    receipt = sched.load_receipt("swarm_launch_job_001", result["run_id"], repo_root=tmp_path)
    assert receipt["status"] == "conflicting_outputs"
    assert receipt["swarm"]["integrated_result"]["classification"] == "conflicting_outputs"
    assert receipt["swarm"]["integrated_result"]["conflicts"] == [
        {
            "kind": "overlapping_changed_files",
            "paths": ["infra/controlplane/scheduled_jobs.py"],
            "child_ids": ["maker_1", "maker_2"],
            "summary": "multiple kept children changed infra/controlplane/scheduled_jobs.py",
        }
    ]
