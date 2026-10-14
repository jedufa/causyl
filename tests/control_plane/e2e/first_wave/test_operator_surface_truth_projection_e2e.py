from __future__ import annotations

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import scheduler_projection_family_proof as proof
from infra.webui import server_routes_and_main as routes

from .helpers.contracts import ScenarioContract, assert_scenario_contract
from .helpers.plane import configure_plane_routes


CONTRACT = ScenarioContract(
    scenario_id="operator_surface_truth_projection_e2e",
    operator_job="Inspect the queue from different plane surfaces and get the same semantic truth about active, blocked, queued, and completed work.",
    capability="Shared scheduler projection across API, watch, web, and mobile-facing summaries.",
    durable_truth=(
        "scheduled job specs",
        "scheduled job state",
        "projection parity proof report",
        "plane mobile direct reply summary",
    ),
    manual_repair_eliminated="Reconciling conflicting queue/status stories across operator surfaces by hand.",
    primary_intent_sheets=("IS-19",),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic state seeding for canonical projection cases",
        "temp-repo route execution",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "writing projection records manually",
        "fabricating closeout state",
    ),
)


def _save_once_job(
    repo_root,
    *,
    job_id: str,
    run_at: str,
    prompt: str,
    depends_on: list[str] | None = None,
) -> None:
    sched.save_job(
        {
            "job_id": job_id,
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": job_id},
            "depends_on": list(depends_on or []),
            "prompt": prompt,
            "schedule": {"mode": "once", "run_at": run_at, "timezone": "UTC"},
        },
        repo_root=repo_root,
    )


def test_operator_surface_truth_projection_e2e(first_wave_root, monkeypatch) -> None:
    assert_scenario_contract(CONTRACT)
    monkeypatch.setattr(sched, "_repo_root", lambda: first_wave_root)
    configure_plane_routes(repo_root=first_wave_root, monkeypatch=monkeypatch)
    original_list_jobs = sched.list_jobs

    _save_once_job(
        first_wave_root,
        job_id="projection_active_001",
        run_at="2026-04-08T05:30:00Z",
        prompt="Active ownership case.",
    )
    sched._write_state(
        "projection_active_001",
        {
            "active_run_id": "sj_projection_active_001",
            "active_started_utc": "2026-04-08T05:31:00Z",
            "last_status": "running",
        },
        repo_root=first_wave_root,
    )

    _save_once_job(
        first_wave_root,
        job_id="projection_failed_parent_001",
        run_at="2026-04-08T05:00:00Z",
        prompt="Failed parent case.",
    )
    sched._write_state(
        "projection_failed_parent_001",
        {
            "last_status": "failed_verification",
            "last_trigger": "scheduler",
            "last_finished_utc": "2026-04-08T06:04:45Z",
            "last_slot_key": "2026-04-08T05:00:00Z",
        },
        repo_root=first_wave_root,
    )

    _save_once_job(
        first_wave_root,
        job_id="projection_blocked_child_001",
        run_at="2026-04-08T05:10:00Z",
        prompt="Dependency blocked case.",
        depends_on=["projection_failed_parent_001"],
    )
    _save_once_job(
        first_wave_root,
        job_id="projection_run_now_001",
        run_at="2026-04-08T09:00:00Z",
        prompt="Run-now queued case.",
    )
    sched._write_state(
        "projection_run_now_001",
        {
            "last_status": "ok",
            "run_now_requested_utc": "2026-04-08T06:04:00Z",
            "last_slot_key": "2026-04-08T09:00:00Z",
        },
        repo_root=first_wave_root,
    )

    _save_once_job(
        first_wave_root,
        job_id="projection_completed_once_001",
        run_at="2026-04-08T04:45:00Z",
        prompt="Completed once case.",
    )
    sched._write_state(
        "projection_completed_once_001",
        {
            "last_status": "ok",
            "last_reconciliation_reason": "already_executed",
            "last_slot_key": "2026-04-08T04:45:00Z",
        },
        repo_root=first_wave_root,
    )

    report = proof.collect_scheduler_projection_surface_proof(
        repo_root=first_wave_root,
        now_utc="2026-04-08T06:05:00Z",
    )
    assert report["all_cases_match"] is True
    assert report["all_degraded_flags_match"] is True
    assert report["mismatches"] == []
    assert report["summary"]["next_job_id"] == "projection_run_now_001"
    assert report["summary"]["running_jobs"] == 1
    assert report["summary"]["blocked_jobs"] == 1

    monkeypatch.setattr(routes._scheduled_jobs, "list_jobs", lambda *args, **kwargs: original_list_jobs(repo_root=first_wave_root))
    next_reply = routes._plane_mobile_direct_reply("what is the next scheduled job?")
    queue_reply = routes._plane_mobile_direct_reply("how many jobs are in the queue?")

    assert next_reply is not None
    assert "projection_run_now_001" in next_reply
    assert queue_reply is not None
    assert "1 are blocked" in queue_reply
    assert "projection_run_now_001" in queue_reply
