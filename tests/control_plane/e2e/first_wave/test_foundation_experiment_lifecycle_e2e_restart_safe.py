from __future__ import annotations

from infra.controlplane import scheduled_jobs as sched

from .helpers.assertions import read_json
from .helpers.contracts import ScenarioContract, assert_scenario_contract
from .helpers.plane import configure_plane_routes, get_json, install_success_runtime, post_json
from .helpers.runtime import write_job_state, write_stale_active_claim


CONTRACT = ScenarioContract(
    scenario_id="foundation_experiment_lifecycle_e2e_restart_safe",
    operator_job="Start work in the plane, survive interruption, and resume without hand-cleaning stale scheduler residue.",
    capability="Canonical job truth, restart-safe stale-claim recovery, run-now re-entry, and durable terminal status.",
    durable_truth=(
        "scheduled job spec",
        "scheduled job state",
        "startup claim audit output",
        "API list/detail payloads",
    ),
    manual_repair_eliminated="Deleting stale scheduled-job claim files and guessing whether the job can safely resume.",
    primary_intent_sheets=("IS-01", "IS-02"),
    supporting_intent_sheets=("IS-07", "IS-08"),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic fake runtime binding for scheduled job execution",
        "deterministic stale-claim failure injection",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "bypassing scheduler admission for the claimed execution path",
        "fabricating closeout state",
    ),
)


def test_foundation_experiment_lifecycle_e2e_restart_safe(first_wave_root, monkeypatch) -> None:
    assert_scenario_contract(CONTRACT)
    install_success_runtime(monkeypatch=monkeypatch)
    cfg = configure_plane_routes(repo_root=first_wave_root, monkeypatch=monkeypatch)

    status, saved = post_json(
        cfg,
        "/api/schedules/job/save",
        {
            "job_id": "foundation_restart_001",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "task_id", "id": "exp_foundation_001"},
            "prompt": "Run the foundation lifecycle scenario.",
            "schedule": {"mode": "once", "run_at": "2026-04-11T00:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 600},
        },
    )
    assert status == 200
    assert saved["job"]["job_id"] == "foundation_restart_001"

    detail_status, queued_detail = get_json(cfg, "/api/schedules/job?job_id=foundation_restart_001")
    assert detail_status == 200
    assert queued_detail["job"]["scheduler_projection_v1"]["projected_status"] == "queued"

    sched.request_run_now("foundation_restart_001", repo_root=first_wave_root, request_source="foundation_e2e")
    write_job_state(
        first_wave_root,
        job_id="foundation_restart_001",
        payload={
            "job_id": "foundation_restart_001",
            "active_run_id": "sj_foundation_restart_001",
            "active_started_utc": "2026-04-11T00:01:00Z",
            "last_status": "running",
            "run_now_requested_utc": "",
            "run_now_request_run_id": "",
            "run_now_request_source": "",
            "updated_utc": "2026-04-11T00:01:05Z",
        },
    )
    stale_lock = write_stale_active_claim(
        first_wave_root,
        job_id="foundation_restart_001",
        run_id="sj_foundation_restart_001",
    )

    audit = sched.startup_claim_audit(repo_root=first_wave_root)
    assert audit["ok"] is True
    assert audit["cleared_count"] == 1
    assert audit["cleared_jobs"] == ["foundation_restart_001"]
    assert not stale_lock.exists()

    recovered_state = sched.load_state("foundation_restart_001", repo_root=first_wave_root)
    assert recovered_state["active_run_id"] == ""
    assert recovered_state["last_status"] == "blocked"
    assert recovered_state["last_nonlaunch_reason"] == "startup_audit_stale_claim_cleared"

    sched.request_run_now("foundation_restart_001", repo_root=first_wave_root, request_source="foundation_e2e_resume")
    tick = sched.scheduler_tick(repo_root=first_wave_root, now_utc="2026-04-11T00:03:00Z", limit=10)
    assert tick["executed_count"] == 1

    detail_status, detail = get_json(cfg, "/api/schedules/job?job_id=foundation_restart_001")
    assert detail_status == 200
    assert detail["job"]["state"]["last_status"] == "ok"
    assert detail["job"]["scheduler_projection_v1"]["projected_status"] == "completed_once"
    assert detail["receipts"]
    assert read_json(
        first_wave_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state" / "foundation_restart_001.json"
    )["last_status"] == "ok"
