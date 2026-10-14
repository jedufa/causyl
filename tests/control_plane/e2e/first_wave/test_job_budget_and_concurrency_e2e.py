from __future__ import annotations

from experiments_shared import job_family_resource_key
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import shared_resource_claim_ledger as resource_claim_ledger
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui.server_routes_and_main_parts import part01__lab_assistant_host_health_payload as host_payloads

from .helpers.contracts import ScenarioContract, assert_scenario_contract
from .helpers.runtime import init_operator_repo, write_demo_project_contract


CONTRACT = ScenarioContract(
    scenario_id="job_budget_and_concurrency_e2e",
    operator_job="Run governed jobs under capacity pressure and get honest blocking rather than silent overcommit.",
    capability="Shared-resource admission blocks a waiting job durably and exposes that contention to operator surfaces.",
    durable_truth=(
        "project runner contract",
        "scheduled job state and receipts",
        "resource concurrency payload",
        "operator snapshot notification",
    ),
    manual_repair_eliminated="Guessing whether a blocked job is safe to launch while capacity is already occupied.",
    primary_intent_sheets=("IS-04", "IS-05"),
    allowed_shortcuts=(
        "fixture seeding before scenario start",
        "deterministic live-claim injection for occupied capacity",
        "temp-repo operator snapshot evaluation",
    ),
    forbidden_shortcuts=(
        "direct state mutation after scenario start to fake success",
        "bypassing scheduler admission for the claimed execution path",
        "writing projection records manually",
    ),
)


def test_job_budget_and_concurrency_e2e(first_wave_root, monkeypatch) -> None:
    assert_scenario_contract(CONTRACT)
    init_operator_repo(first_wave_root)
    write_demo_project_contract(first_wave_root)
    monkeypatch.setattr(sched, "_repo_root", lambda: first_wave_root)
    monkeypatch.setattr(sched._runtime_policy, "_repo_root", lambda: first_wave_root)
    runtime_policy_path = first_wave_root / "config" / "runtime_policy.properties"
    runtime_policy_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_policy_path.write_text(
        "scheduler.global_concurrency_cap=10\n"
        "scheduler.role_concurrency_caps=builder:10,generalist:10\n"
        "scheduler.resource_class_caps=cpu_light:1\n",
        encoding="utf-8",
    )

    resource = job_family_resource_key("cpu_light").as_dict()
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_family_active:acquired",
            "claim_uid": "existing_family_active",
            "claimant_uid": "worker.alpha",
            "resource": dict(resource),
            "recorded_at_utc": "2026-04-11T01:00:00Z",
            "status": "acquired",
            "wait_reason_code": "",
            "detail": "",
        },
        repo_root=first_wave_root,
    )

    waiting_job = sched.save_job(
        sched.validate_job_spec(
            {
                "job_id": "intent_resource_waiting_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-11T01:00:00Z", "timezone": "UTC"},
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "case",
                    "args": {"registry": "chemistry/intent_resource_waiting_001.json", "dry_run": True},
                },
            },
            repo_root=first_wave_root,
        ),
        repo_root=first_wave_root,
    )

    result = sched.execute_job(
        waiting_job["job_id"],
        repo_root=first_wave_root,
        trigger="scheduler",
        force=False,
        now_utc="2026-04-11T01:00:01Z",
    )
    assert result["ok"] is False
    assert result["deferred"] is True
    assert result["reason"] == "shared_resource_waiting:job_family:scheduler:cpu_light:1/1"

    waits = resource_claim_ledger.wait_reasons_for_claimant(
        f"scheduled_job:{waiting_job['job_id']}",
        repo_root=first_wave_root,
    )
    assert waits["wait_reasons"][0]["wait_reason_code"] == "awaiting_capacity"

    admin_projection = host_payloads._lab_assistant_resource_concurrency_payload(repo_root=first_wave_root)
    assert admin_projection["status"] == "ok"
    assert admin_projection["waiting_claim_count"] == 1
    assert admin_projection["highlights"][0]["canonical_key"] == "job_family:scheduler:cpu_light"

    operator_snapshot = snapshot.build_operator_snapshot(first_wave_root)
    assert operator_snapshot["resource_concurrency"]["status"] == "ok"
    notification = next(
        row for row in operator_snapshot["operator_summary"]["notifications"] if row["kind"] == "shared_resource_contention"
    )
    assert notification["severity"] == "warning"
    assert "job_family:scheduler:cpu_light is contended" in notification["message"]
