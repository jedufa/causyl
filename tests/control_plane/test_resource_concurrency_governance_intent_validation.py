from __future__ import annotations

import json
from pathlib import Path

import pytest

from experiments_shared import job_family_resource_key
from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import shared_resource_claim_ledger as resource_claim_ledger
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as snapshot
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register
from infra.webui import server_lab_assistant_web_operations as web_ops
from infra.webui.server_routes_and_main_parts import part01__lab_assistant_host_health_payload as host_payloads


def _init_operator_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)
    state.load_or_init_assistant_state(root)
    register.load_or_init_task_register(root)


def _write_demo_contract(repo_root: Path) -> None:
    project_root = repo_root / "experiments" / "projects" / "demo"
    runner_root = project_root / "_runner"
    runner_root.mkdir(parents=True, exist_ok=True)
    (project_root / "_config").mkdir(parents=True, exist_ok=True)
    (project_root / "_spec" / "registry" / "v1").mkdir(parents=True, exist_ok=True)
    script = runner_root / "run_demo.py"
    script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations
import argparse
import json
from pathlib import Path

p = argparse.ArgumentParser()
p.add_argument("--registry", required=True)
p.add_argument("--label", default="")
p.add_argument("--tag", action="append", default=[])
p.add_argument("--dry_run", action="store_true")
args = p.parse_args()
payload = {
    "registry": args.registry,
    "label": args.label,
    "tag": list(args.tag or []),
    "dry_run": bool(args.dry_run),
}
Path("demo_runner_output.json").write_text(json.dumps(payload, sort_keys=True), encoding="utf-8")
print(json.dumps(payload, sort_keys=True))
""",
        encoding="utf-8",
    )
    contract = {
        "schema_version": "experiment_contract_v1",
        "contract_hash": "",
        "compatibility": {
            "contract_family": "experiment_contract",
            "major": 1,
            "minor": 0,
        },
        "project": {
            "project_id": "demo",
            "display_name": "Demo Experiment",
            "owner": "causyl",
            "tags": ["demo"],
        },
        "launch": {
            "default_runner_id": "case",
            "supported_runners": ["case"],
            "entrypoints": {
                "case": {
                    "command": ["python3", "-u", "experiments/projects/demo/_runner/run_demo.py"],
                    "working_dir": "experiments/projects/demo",
                    "env": {"PYTHONUNBUFFERED": "1"},
                }
            },
            "args": {
                "required": ["registry"],
                "optional": ["label", "tag", "dry_run"],
                "types": {
                    "registry": "string",
                    "label": "string",
                    "tag": "string_list",
                    "dry_run": "bool",
                },
            },
        },
        "configuration": {
            "profiles_root": "experiments/projects/demo/_config",
            "registry_root": "experiments/projects/demo/_spec/registry/v1",
            "override_modes": ["patch"],
            "default_profile": "baseline",
        },
        "observability": {
            "status_files": [{"path": "demo_runner_output.json", "type": "json", "priority": 1}],
            "progress": {"mode": "none", "fields": []},
            "health_signals": ["exit_code"],
        },
        "control": {
            "pause_supported": False,
            "resume_supported": False,
            "stop_supported": True,
            "restart_supported": False,
            "checkpoint_supported": False,
            "branch_from_checkpoint_supported": False,
            "intervention_actions": ["graceful_stop"],
            "unsupported_actions": [],
        },
        "analysis": {
            "primary_artifacts": ["demo_runner_output.json"],
            "recipes": ["summary_report"],
            "default_recipe": "summary_report",
        },
        "resources": {
            "resource_class": "cpu_light",
            "project_semaphore": "cpu/demo",
            "max_parallel_runs": 1,
            "estimated_footprint": {
                "gpu_count": 0,
                "cpu_cores": 1,
                "ram_gb": 1,
            },
        },
        "limitations": [],
        "artifacts": {
            "repo_tracked_outputs": ["demo_runner_output.json"],
            "external_data_root": "../demo-data",
            "artifact_manifest_required": False,
        },
        "context_policy": {
            "required_context_bundle": "lab_core_v1",
            "additional_context_refs_allowed": True,
            "forbidden_context_refs": [],
        },
        "validation": {
            "schema_validation_required": True,
            "hash_validation_required": True,
            "prelaunch_checks": ["profiles_root_exists", "registry_root_exists"],
        },
    }
    contract["contract_hash"] = validate_experiment_contract(contract)["computed_contract_hash"]
    (runner_root / "experiment_contract.json").write_text(
        json.dumps(contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "demo",
        "actions": {
            "graceful_stop": {
                "policy_state": "enabled",
                "rollout_state": "live",
                "note": "default operator stop path",
            }
        },
    }
    policy["policy_hash"] = validate_control_action_policy(policy)["computed_policy_hash"]
    (runner_root / "control_action_policy.json").write_text(
        json.dumps(policy, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_runtime_policy(repo_root: Path, *, cpu_light_cap: int) -> None:
    policy_path = repo_root / "config" / "runtime_policy.properties"
    policy_path.parent.mkdir(parents=True, exist_ok=True)
    policy_path.write_text(
        "scheduler.global_concurrency_cap=10\n"
        "scheduler.role_concurrency_caps=builder:10,generalist:10\n"
        f"scheduler.resource_class_caps=cpu_light:{cpu_light_cap}\n",
        encoding="utf-8",
    )


def _project_runner_spec(job_id: str) -> dict:
    return {
        "job_id": job_id,
        "enabled": True,
        "binding": {"type": "none"},
        "job_kind": "project_runner",
        "schedule": {"mode": "once", "run_at": "2026-04-07T05:00:00Z", "timezone": "UTC"},
        "project_run": {
            "project_id": "demo",
            "runner_id": "case",
            "args": {"registry": f"chemistry/{job_id}.json", "dry_run": True},
        },
    }


def _save_project_runner_job(repo_root: Path, *, job_id: str) -> dict:
    spec = sched.validate_job_spec(_project_runner_spec(job_id), repo_root=repo_root)
    return sched.save_job(spec, repo_root=repo_root)


def _patch_refresh_console(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(web_ops._agents, "refresh_live_runtime", lambda: {"ok": True, "backend": "test"})
    monkeypatch.setattr(web_ops._agent_orch, "current_runtime_defaults", lambda: {"defaults": {}})
    monkeypatch.setattr(
        web_ops._lab_operator_snapshot,
        "build_operator_snapshot",
        lambda _root: {"generated_utc": "2026-04-07T06:00:01Z", "backend_projection": {"state": "healthy"}},
    )
    monkeypatch.setattr(
        web_ops._execution_queue,
        "status",
        lambda: {
            "state": {
                "active": False,
                "status": "idle",
                "status_detail": "idle",
                "current_workflow_id": "",
                "current_workflow_status": "",
                "stop_requested": False,
            },
            "events": [],
        },
    )
    monkeypatch.setattr(web_ops._agent_orch, "list_workflows", lambda *, limit=50: {"ok": True, "count": 0, "items": []})
    monkeypatch.setattr(web_ops._scheduled_jobs, "scheduler_service_status", lambda: {"running": True, "last_tick_utc": "2026-04-07T06:00:00Z"})
    monkeypatch.setattr(web_ops._scheduled_jobs, "list_jobs", lambda *, repo_root=None: [])
    monkeypatch.setattr(web_ops, "_utc_iso", lambda value=None: "2026-04-07T06:00:00Z")


def test_resource_concurrency_intent_validation_proves_launch_release_and_future_refresh_scope(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_operator_repo(tmp_path)
    _write_demo_contract(tmp_path)
    _write_runtime_policy(tmp_path, cpu_light_cap=1)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._runtime_policy, "_repo_root", lambda: tmp_path)

    first_job = _save_project_runner_job(tmp_path, job_id="intent_resource_refresh_before_001")
    first_execution = sched.execute_job(
        first_job["job_id"],
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc="2026-04-07T05:10:00Z",
    )

    assert first_execution["ok"] is True
    first_receipt = sched.load_receipt(first_job["job_id"], first_execution["run_id"], repo_root=tmp_path)
    assert first_receipt["runtime_policy_snapshot"]["resource_class_caps"]["cpu_light"] == 1
    assert [row["terminal_status"] for row in first_receipt["resource_claims"]["claims"]] == ["released"]

    _patch_refresh_console(monkeypatch)
    _write_runtime_policy(tmp_path, cpu_light_cap=3)
    refresh = web_ops.invoke_runtime_control(tmp_path, action_id="refresh_runtime", writer="intent_validation")

    assert refresh["confirmation"]["state"] == "confirmed"
    assert "future work" in refresh["confirmation"]["summary"].lower()
    assert "in-flight turns/jobs are unchanged" in refresh["confirmation"]["summary"].lower()

    second_job = _save_project_runner_job(tmp_path, job_id="intent_resource_refresh_after_001")
    second_execution = sched.execute_job(
        second_job["job_id"],
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc="2026-04-07T05:20:00Z",
    )

    assert second_execution["ok"] is True
    second_receipt = sched.load_receipt(second_job["job_id"], second_execution["run_id"], repo_root=tmp_path)
    assert second_receipt["runtime_policy_snapshot"]["resource_class_caps"]["cpu_light"] == 3
    assert first_receipt["runtime_policy_snapshot"]["resource_class_caps"]["cpu_light"] == 1


def test_resource_concurrency_intent_validation_proves_waiting_projection_and_admin_surface(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_operator_repo(tmp_path)
    _write_demo_contract(tmp_path)
    _write_runtime_policy(tmp_path, cpu_light_cap=1)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(sched._runtime_policy, "_repo_root", lambda: tmp_path)

    resource = job_family_resource_key("cpu_light").as_dict()
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_family_active:acquired",
            "claim_uid": "existing_family_active",
            "claimant_uid": "worker.alpha",
            "resource": dict(resource),
            "recorded_at_utc": "2026-04-07T05:10:00Z",
            "status": "acquired",
            "wait_reason_code": "",
            "detail": "",
        },
        repo_root=tmp_path,
    )

    waiting_job = _save_project_runner_job(tmp_path, job_id="intent_resource_waiting_001")
    waiting = sched.execute_job(
        waiting_job["job_id"],
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc="2026-04-07T05:10:01Z",
    )

    assert waiting["ok"] is False
    assert waiting["deferred"] is True
    assert waiting["reason"] == "shared_resource_waiting:job_family:scheduler:cpu_light:1/1"

    waits = resource_claim_ledger.wait_reasons_for_claimant(f"scheduled_job:{waiting_job['job_id']}", repo_root=tmp_path)
    assert waits["wait_reasons"][0]["wait_reason_code"] == "awaiting_capacity"

    admin_projection = host_payloads._lab_assistant_resource_concurrency_payload(repo_root=tmp_path)
    assert admin_projection["status"] == "ok"
    assert admin_projection["waiting_claim_count"] == 1
    assert admin_projection["highlights"][0]["canonical_key"] == "job_family:scheduler:cpu_light"

    operator_snapshot = snapshot.build_operator_snapshot(tmp_path)
    assert operator_snapshot["resource_concurrency"]["status"] == "ok"
    notification = next(row for row in operator_snapshot["operator_summary"]["notifications"] if row["kind"] == "shared_resource_contention")
    assert notification["severity"] == "warning"
    assert "job_family:scheduler:cpu_light is contended" in notification["message"]


def test_resource_concurrency_intent_validation_proves_invalid_policy_rejection_and_stale_claim_recovery(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _init_operator_repo(tmp_path)
    _write_demo_contract(tmp_path)
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setitem(sched._DISPATCH_CONFIG, "global_concurrency_cap", 10)

    monkeypatch.setattr(
        sched,
        "current_runtime_resource_policy",
        lambda repo_root=None: {
            "ok": True,
            "global_concurrency_cap": 10,
            "per_role_cap": {"generalist": 10, "builder": 10},
            "resource_class_caps": {"cpu_light": 0},
            "policy_source": "test",
            "policy_valid": True,
            "validation_errors": [],
            "sources": {"resource_class_caps": "test"},
        },
    )
    blocked_job = _save_project_runner_job(tmp_path, job_id="intent_resource_invalid_policy_001")
    blocked = sched.execute_job(
        blocked_job["job_id"],
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc="2026-04-07T05:15:00Z",
    )

    assert blocked["ok"] is False
    assert blocked["reason"] == "shared_resource_not_allowed:job_family:scheduler:cpu_light"
    blocked_receipt = sched.load_receipt(blocked_job["job_id"], blocked["run_id"], repo_root=tmp_path)
    assert blocked_receipt["status"] == "blocked"

    monkeypatch.setattr(
        sched,
        "current_runtime_resource_policy",
        lambda repo_root=None: {
            "ok": True,
            "global_concurrency_cap": 10,
            "per_role_cap": {"generalist": 10, "builder": 10},
            "resource_class_caps": {"cpu_light": 1},
            "policy_source": "test",
            "policy_valid": True,
            "validation_errors": [],
            "sources": {"resource_class_caps": "test"},
        },
    )
    recovering_job = _save_project_runner_job(tmp_path, job_id="intent_resource_stale_claim_001")
    resource = dict(recovering_job["project_run"]["contract_projection"]["shared_resource_contract"]["claim_keys"][0])
    sched._record_project_runner_runtime_claim_acquired(
        spec=recovering_job,
        run_id="sj_dead",
        now_utc="2026-04-07T05:16:00Z",
        repo_root=tmp_path,
    )
    sched._write_state(
        recovering_job["job_id"],
        {
            "job_id": recovering_job["job_id"],
            "active_run_id": "sj_dead",
            "last_status": "blocked",
        },
        repo_root=tmp_path,
    )

    recovered = sched.execute_job(
        recovering_job["job_id"],
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc="2026-04-07T05:20:00Z",
    )

    assert recovered["ok"] is True
    recovered_receipt = sched.load_receipt(recovering_job["job_id"], recovered["run_id"], repo_root=tmp_path)
    assert [row["terminal_status"] for row in recovered_receipt["resource_claims"]["claims"]] == ["released"]

    stale_claim_uid = sched._project_runner_runtime_claim_uid(recovering_job["job_id"], "sj_dead", resource)
    stale_lineage = resource_claim_ledger.read_claim_lineage(stale_claim_uid, repo_root=tmp_path)
    stale_recoveries = resource_claim_ledger.read_recovery_records_for_claim(stale_claim_uid, repo_root=tmp_path)
    assert [row["status"] for row in stale_lineage] == ["acquired", "released"]
    assert [row["action"] for row in stale_recoveries] == ["released"]
    assert stale_recoveries[0]["reason_code"] in {"run_id_mismatch", "state_not_running"}
