from __future__ import annotations

import json
import time
from datetime import timedelta
from pathlib import Path

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.verification_workflow_launch import create_scheduler_backed_verification_launch

from tests.control_plane.verification_workflow_test_support import (
    broad_scheduler_verification_payload,
    drive_verification_campaign_to_completion,
    stage_verification_workflow_repo,
    verification_workflow_launch_payload,
)


def test_scheduler_service_picks_up_verification_workflow_run_now_and_writes_campaign_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    stage_verification_workflow_repo(tmp_path)
    sched.stop_scheduler_service(timeout_s=0.5)
    monkeypatch.setattr(sched, "start_watchdog_service", lambda **kwargs: {"running": True})
    monkeypatch.setattr(sched, "ensure_watchdog_service_liveness", lambda **kwargs: {"health": "healthy"})
    tick_clock = {"value": 0}
    real_parse = sched._parse_utc

    def _next_utc():
        value = tick_clock["value"]
        tick_clock["value"] += 1
        return real_parse("2026-04-14T10:10:00Z") + timedelta(seconds=value)

    monkeypatch.setattr(sched, "_utc_now", _next_utc)

    launch = create_scheduler_backed_verification_launch(
        verification_workflow_launch_payload(request_id="verification_workflow_service_e2e"),
        repo_root=tmp_path,
        run_now=True,
    )
    fingerprint = launch["durable_request"]["request_fingerprint"]
    job_id = launch["scheduled_job"]["job_id"]

    real_parse = sched._parse_utc
    monkeypatch.setattr(sched, "_utc_now", lambda: real_parse("2026-04-14T10:00:00Z"))

    state = sched.start_scheduler_service(interval_s=1, repo_root=tmp_path)
    assert state["running"] is True
    try:
        deadline = time.time() + 15.0
        receipt = None
        campaign_path = tmp_path / "runtime" / "state" / "verification_workflow" / "campaign_runs" / f"{fingerprint}.json"
        while time.time() < deadline:
            receipts = sched.list_receipts(job_id, repo_root=tmp_path, limit=5)
            if receipts and any(str(row.get("status") or "") == "ok" for row in receipts) and campaign_path.exists():
                receipt = next(row for row in receipts if str(row.get("status") or "") == "ok")
                break
            time.sleep(0.05)
        assert receipt is not None
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        runner_output = json.loads(
            (tmp_path / "experiments" / "projects" / "verification_workflow" / "verification_workflow_run_output.json").read_text(
                encoding="utf-8"
            )
        )
        assert receipt["job_kind"] == "project_runner"
        assert receipt["launch_resolution_source"] == "experiment_contract"
        assert campaign["execution_status"] == "completed"
        assert campaign["anchor_report"]["dut_health_delta"]["net_dut_delta"] == "improved"
        assert runner_output["campaign_record"]["request_id"] == "verification_workflow_service_e2e"
    finally:
        sched.stop_scheduler_service(timeout_s=0.5)


def test_scheduler_service_executes_broad_scheduler_verification_goal_via_planner_bridge(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    stage_verification_workflow_repo(tmp_path)
    sched.stop_scheduler_service(timeout_s=0.5)
    monkeypatch.setattr(sched, "start_watchdog_service", lambda **kwargs: {"running": True})
    monkeypatch.setattr(sched, "ensure_watchdog_service_liveness", lambda **kwargs: {"health": "healthy"})
    tick_clock = {"value": 0}
    real_parse = sched._parse_utc

    def _next_utc():
        value = tick_clock["value"]
        tick_clock["value"] += 1
        return real_parse("2026-04-14T10:10:00Z") + timedelta(seconds=value)

    monkeypatch.setattr(sched, "_utc_now", _next_utc)

    launch = create_scheduler_backed_verification_launch(
        broad_scheduler_verification_payload(request_id="verification_workflow_broad_scheduler_service"),
        repo_root=tmp_path,
        run_now=True,
    )
    fingerprint = launch["durable_request"]["request_fingerprint"]
    job_id = launch["scheduled_job"]["job_id"]

    campaign_path = tmp_path / "runtime" / "state" / "verification_workflow" / "campaign_runs" / f"{fingerprint}.json"
    planner_path = tmp_path / "runtime" / "state" / "verification_workflow" / "plans" / f"{fingerprint}.json"
    receipt = None
    sched.scheduler_tick(repo_root=tmp_path, now_utc="2026-04-14T10:10:00Z", limit=20)
    receipts = sched.list_receipts(job_id, repo_root=tmp_path, limit=5)
    if receipts and any(str(row.get("status") or "") == "ok" for row in receipts):
        receipt = next(row for row in receipts if str(row.get("status") or "") == "ok")
    assert receipt is not None
    assert planner_path.exists()
    planner = json.loads(planner_path.read_text(encoding="utf-8"))
    campaign = drive_verification_campaign_to_completion(repo_root=tmp_path, campaign_path=campaign_path)
    assert planner["planner_output"]["output_mode"] == "child_pis_family"
    assert campaign["execution_status"] == "completed"
    assert campaign["current_wave_index"] >= 2
    assert len(campaign["child_scenarios"]) >= 8
    assert len(campaign["anchor_report"]["compiled_jobs"]) >= 8
    assert campaign["planner_packet_path"].endswith(f"{fingerprint}.json")


def test_create_scheduler_backed_verification_launch_retires_older_sibling_launcher_for_same_goal(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    stage_verification_workflow_repo(tmp_path)

    first = create_scheduler_backed_verification_launch(
        {
            "request_id": "vw_android_tabs_live",
            "verification_goal": "Very hardly verify and validate the Android agent tabs building block",
            "operator_note": "first android tabs live launch",
            "max_iterations": 4,
        },
        repo_root=tmp_path,
        run_now=False,
    )
    second = create_scheduler_backed_verification_launch(
        {
            "request_id": "verification_workflow_android_agent_tabs_goal",
            "verification_goal": "Very hardly verify and validate the Android agent tabs building block",
            "operator_note": "second android tabs live launch",
            "max_iterations": 4,
        },
        repo_root=tmp_path,
        run_now=False,
    )

    first_job_id = str(first["scheduled_job"]["job_id"])
    second_job_id = str(second["scheduled_job"]["job_id"])
    first_job = sched.load_job(first_job_id, repo_root=tmp_path)
    second_job = sched.load_job(second_job_id, repo_root=tmp_path)

    assert first_job["enabled"] is False
    assert second_job["enabled"] is True
    assert first_job_id in list(second.get("retired_job_ids") or [])
