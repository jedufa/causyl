from __future__ import annotations

import json
import os
import datetime as dt
from pathlib import Path
import subprocess
import sys

from experiments_shared import job_family_resource_key
from experiments_shared import read_experiment_contract
from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from experiments_shared import project_runner_contract
from experiments_shared.runner_utils import ensure_project_output_env
from infra.controlplane import shared_resource_claim_ledger as resource_claim_ledger
from tools.bin import scheduler_create_project_runner_job as create_project_runner_job
from infra.controlplane import scheduled_jobs as sched


def _write_demo_project(repo_root: Path) -> None:
    runner_root = repo_root / "experiments" / "projects" / "demo" / "_runner"
    runner_root.mkdir(parents=True, exist_ok=True)
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
    (runner_root / "project_runner_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "project_runner_manifest_v1",
                "project_id": "demo",
                "project_root": "experiments/projects/demo",
                "command_prefix": ["python3", "-u"],
                "runners": {
                    "case": {
                        "entrypoint": "experiments/projects/demo/_runner/run_demo.py",
                        "cwd": "experiments/projects/demo",
                        "description": "Run the demo project runner",
                        "args": {
                            "registry": {"type": "str", "required": True},
                            "label": {"type": "str", "default": ""},
                            "tag": {"type": "string_list", "default": []},
                            "dry_run": {"type": "bool", "default": False},
                        },
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _write_demo_contract(repo_root: Path, *, break_runner: bool = False) -> None:
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
                    "command": [] if break_runner else ["python3", "-u", "experiments/projects/demo/_runner/run_demo.py"],
                    "working_dir": "" if break_runner else "experiments/projects/demo",
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
            "status_files": [
                {"path": "demo_runner_output.json", "type": "json", "priority": 1},
            ],
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


def _mutate_demo_contract(repo_root: Path, mutate) -> dict:
    contract_path = repo_root / "experiments" / "projects" / "demo" / "_runner" / "experiment_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    mutate(contract)
    contract["contract_hash"] = validate_experiment_contract(contract)["computed_contract_hash"]
    contract_path.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return contract


def _write_failing_demo_project(repo_root: Path) -> None:
    runner_root = repo_root / "experiments" / "projects" / "demo" / "_runner"
    runner_root.mkdir(parents=True, exist_ok=True)
    script = runner_root / "run_demo_fail.py"
    script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations
import sys
print("demo runner failing intentionally")
raise SystemExit(7)
""",
        encoding="utf-8",
    )
    (runner_root / "project_runner_manifest.json").write_text(
        json.dumps(
            {
                "schema_version": "project_runner_manifest_v1",
                "project_id": "demo",
                "project_root": "experiments/projects/demo",
                "command_prefix": ["python3", "-u"],
                "runners": {
                    "case": {
                        "entrypoint": "experiments/projects/demo/_runner/run_demo_fail.py",
                        "cwd": "experiments/projects/demo",
                        "description": "Run the failing demo project runner",
                        "args": {
                            "registry": {"type": "str", "required": True},
                            "dry_run": {"type": "bool", "default": False}
                        },
                    }
                },
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def test_project_runner_job_executes_manifest_backed_entrypoint(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_demo_project(tmp_path)

    saved = sched.save_job(
        {
            "job_id": "demo_project_runner_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "display_title": "Run demo project adapter",
            "schedule": {"mode": "once", "run_at": "2026-04-02T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/demo.json", "label": "smoke", "dry_run": True},
                "allow_manifest_compatibility": True,
            },
        },
        repo_root=tmp_path,
    )

    assert saved["job_kind"] == "project_runner"
    assert saved["binding"] == {"type": "none", "id": ""}
    assert saved["project_run"]["runner_id"] == "case"
    assert saved["project_run"]["args"]["tag"] == []

    result = sched.execute_job(
        "demo_project_runner_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-02T10:00:00Z",
    )

    assert result["ok"] is True
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["job_kind"] == "project_runner"
    assert receipt["job_shape"] == "experiment_run_job"
    assert receipt["job_shape_source"] == "explicit"
    assert receipt["job_shape_compatibility_mode"] is False
    assert receipt["status"] == "ok"
    assert receipt["project_run"]["project_id"] == "demo"
    assert receipt["launch_resolution_source"] == "manifest_compatibility"
    assert receipt["contract_hash"] == ""
    assert receipt["command_prefix"] == ["python3", "-u"]
    assert "--registry chemistry/demo.json" in receipt["command_display"]
    log_path = tmp_path / receipt["runner_log_path"]
    assert log_path.exists()
    report_path = Path(result["report_path"])
    report_text = report_path.read_text(encoding="utf-8")
    assert "## Project Runner" in report_text
    assert "project_id: demo" in report_text
    artifact = tmp_path / "experiments" / "projects" / "demo" / "demo_runner_output.json"
    assert artifact.exists()
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["registry"] == "chemistry/demo.json"
    assert payload["dry_run"] is True
    assert payload["tag"] == []


def test_project_runner_launch_skips_malformed_historical_shared_resource_claim_and_still_runs(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_demo_contract(tmp_path)

    resource = {
        "resource_class": "project",
        "scope": "cpu",
        "locator": "demo",
        "canonical_key": "project:cpu:demo",
    }
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "bad_claim:released",
            "claim_uid": "bad_claim",
            "claimant_uid": "scheduled_job:broken_demo_runner",
            "resource": dict(resource),
            "recorded_at_utc": "2026-04-07T05:10:00Z",
            "status": "released",
            "wait_reason_code": "",
            "detail": "historical malformed claim",
            "source": "test",
        },
        repo_root=tmp_path,
    )

    saved = sched.save_job(
        {
            "job_id": "demo_project_runner_malformed_claim_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "display_title": "Run demo project adapter with malformed historical claim present",
            "schedule": {"mode": "once", "run_at": "2026-04-02T10:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "expected_output": {"kind": "report", "format": "markdown"},
            "required_context_bundle": "lab_core_v1",
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/demo.json", "label": "smoke", "dry_run": True},
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        str(saved.get("job_id") or ""),
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-02T10:00:00Z",
    )

    assert result["ok"] is True
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "ok"
    recovery = sched._reconcile_stale_project_runner_resource_claims(
        resource=resource,
        now_utc="2026-04-02T10:01:00Z",
        repo_root=tmp_path,
    )
    assert recovery["errors"] == [
        {
            "claim_uid": "bad_claim",
            "error": "claim released before acquisition: bad_claim",
            "lineage_length": 1,
        }
    ]


def test_project_runner_job_requires_contract_by_default(tmp_path: Path) -> None:
    _write_demo_project(tmp_path)

    try:
        sched.save_job(
            {
                "job_id": "demo_project_runner_requires_contract_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-02T10:15:00Z", "timezone": "UTC"},
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "case",
                    "args": {"registry": "chemistry/demo.json"},
                },
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected missing experiment contract to block normal project-runner launch")

    assert "experiment contract required for demo" in message
    assert "allow_manifest_compatibility=true" in message


def test_project_runner_job_executes_contract_backed_entrypoint_without_manifest(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_demo_contract(tmp_path)

    saved = sched.save_job(
        {
            "job_id": "demo_project_runner_contract_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": "2026-04-02T10:30:00Z", "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/contract.json", "label": "from-contract", "dry_run": True},
            },
        },
        repo_root=tmp_path,
    )

    assert saved["project_run"]["launch_resolution_source"] == "experiment_contract"
    assert str(saved["project_run"]["contract_hash"]).startswith("sha256:")
    assert saved["project_run"]["contract_projection"]["status_surfaces"]["progress_mode"] == "none"
    assert saved["project_run"]["contract_projection"]["control_declarations"]["supported_actions"] == ["graceful_stop"]
    assert saved["project_run"]["contract_projection"]["analysis_entrypoints"]["recipes"] == ["summary_report"]

    result = sched.execute_job(
        "demo_project_runner_contract_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-02T10:30:00Z",
    )

    assert result["ok"] is True
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["launch_resolution_source"] == "experiment_contract"
    assert receipt["manifest_path"] == ""
    assert receipt["launch_definition_path"].endswith("experiments/projects/demo/_runner/experiment_contract.json")
    assert receipt["configuration_roots"]["registry_root"] == "experiments/projects/demo/_spec/registry/v1"
    assert receipt["contract_hash"] == saved["project_run"]["contract_hash"]
    assert receipt["contract_projection"] == saved["project_run"]["contract_projection"]
    artifact = tmp_path / "experiments" / "projects" / "demo" / "demo_runner_output.json"
    payload = json.loads(artifact.read_text(encoding="utf-8"))
    assert payload["registry"] == "chemistry/contract.json"
    assert payload["label"] == "from-contract"
    assert payload["dry_run"] is True
    assert receipt["required_context_bundle"] == "lab_core_v1"
    assert receipt["scheduler_policy"]["max_parallel_runs"] == 1
    assert receipt["scheduler_policy"]["resource_class"] == "cpu_light"
    assert receipt["resource_claims"]["claimant_uid"] == "scheduled_job:demo_project_runner_contract_001"
    assert receipt["resource_claims"]["release_error"] == ""
    assert [row["terminal_status"] for row in receipt["resource_claims"]["claims"]] == ["released"]
    lineage = resource_claim_ledger.read_claim_lineage(receipt["resource_claims"]["claims"][0]["claim_uid"], repo_root=tmp_path)
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert lineage[-1]["detail"].startswith("runtime claim terminated for project runner run")
    report_text = Path(result["report_path"]).read_text(encoding="utf-8")
    assert "### Declared Status Surfaces" in report_text
    assert "- status_files: demo_runner_output.json" in report_text
    assert "### Declared Controls" in report_text
    assert "- supported_actions: graceful_stop" in report_text
    assert "- unsupported_actions: (none declared)" in report_text
    assert "### Effective Control Policy" in report_text
    assert "- headline: 1 available, 0 blocked, 0 unsupported" in report_text
    assert "### Analysis Entry Points" in report_text
    assert "- recipes: summary_report" in report_text


def test_project_runner_resource_policy_releases_dead_claim_with_recovery_receipt(tmp_path: Path) -> None:
    _write_demo_contract(tmp_path)
    saved = sched.save_job(
        {
            "job_id": "demo_project_runner_recovery_001",
            "enabled": True,
            "role": "builder",
            "job_kind": "project_runner",
            "binding": {"type": "task_id", "id": "tsk_pr_recovery"},
            "prompt": "Recover stale resource claim.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T05:00:00Z", "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/demo.json", "dry_run": True},
            },
        },
        repo_root=tmp_path,
    )
    resource = dict(saved["project_run"]["contract_projection"]["shared_resource_contract"]["claim_keys"][0])
    sched._record_project_runner_runtime_claim_acquired(
        spec=saved,
        run_id="sj_dead",
        now_utc="2026-04-07T05:00:00Z",
        repo_root=tmp_path,
    )
    sched._write_state(
        "demo_project_runner_recovery_001",
        {
            "job_id": "demo_project_runner_recovery_001",
            "active_run_id": "",
            "last_status": "blocked",
        },
        repo_root=tmp_path,
    )

    decision = sched._project_runner_shared_resource_policy_decision(
        saved,
        repo_root=tmp_path,
        run_id="sj_next",
        now=dt.datetime(2026, 4, 7, 5, 5, tzinfo=dt.timezone.utc),
    )

    claim_uid = sched._project_runner_runtime_claim_uid("demo_project_runner_recovery_001", "sj_dead", resource)
    lineage = resource_claim_ledger.read_claim_lineage(claim_uid, repo_root=tmp_path)
    recoveries = resource_claim_ledger.read_recovery_records_for_claim(claim_uid, repo_root=tmp_path)
    assert decision == {"allowed": True}
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert [row["action"] for row in recoveries] == ["released"]
    assert recoveries[0]["reason_code"] == "state_not_running"


def test_project_runner_resource_policy_does_not_reclaim_live_claim_from_timeout_heuristic_alone(tmp_path: Path) -> None:
    _write_demo_contract(tmp_path)
    saved = sched.save_job(
        {
            "job_id": "demo_project_runner_live_001",
            "enabled": True,
            "role": "builder",
            "job_kind": "project_runner",
            "binding": {"type": "task_id", "id": "tsk_pr_live"},
            "prompt": "Protect live claim from timeout-only recovery.",
            "schedule": {"mode": "once", "run_at": "2026-04-07T05:00:00Z", "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 60},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/demo.json", "dry_run": True},
            },
        },
        repo_root=tmp_path,
    )
    resource = dict(saved["project_run"]["contract_projection"]["shared_resource_contract"]["claim_keys"][0])
    sched._record_project_runner_runtime_claim_acquired(
        spec=saved,
        run_id="sj_live",
        now_utc="2026-04-07T05:00:00Z",
        repo_root=tmp_path,
    )
    sched._write_state(
        "demo_project_runner_live_001",
        {
            "job_id": "demo_project_runner_live_001",
            "active_run_id": "sj_live",
            "active_started_utc": "2026-04-07T05:00:00Z",
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    claim_path = sched.locks_dir(tmp_path) / "demo_project_runner_live_001.lock.json"
    claim_path.write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "demo_project_runner_live_001",
                "run_id": "sj_live",
                "trigger": "scheduler",
                "pid": os.getpid(),
                "claimed_utc": "2026-04-07T05:00:00Z",
                "heartbeat_utc": "2026-04-07T05:01:00Z",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    decision = sched._project_runner_shared_resource_policy_decision(
        saved,
        repo_root=tmp_path,
        run_id="sj_waiting",
        now=dt.datetime(2026, 4, 7, 5, 5, tzinfo=dt.timezone.utc),
    )

    claim_uid = sched._project_runner_runtime_claim_uid("demo_project_runner_live_001", "sj_live", resource)
    lineage = resource_claim_ledger.read_claim_lineage(claim_uid, repo_root=tmp_path)
    recoveries = resource_claim_ledger.read_recovery_records_for_claim(claim_uid, repo_root=tmp_path)
    assert decision["allowed"] is False
    assert decision["status"] == "deferred"
    assert [row["status"] for row in lineage] == ["acquired"]
    assert [row["action"] for row in recoveries] == ["quarantined"]
    assert recoveries[0]["reason_code"] == "manual_review_required"


def test_scheduler_create_project_runner_job_saves_job(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(create_project_runner_job, "REPO_ROOT", tmp_path)
    _write_demo_contract(tmp_path)

    rc = create_project_runner_job.main(
        [
            "--job-id",
            "demo_project_runner_cli_001",
            "--display-title",
            "Demo project run",
            "--project-id",
            "demo",
            "--runner-id",
            "case",
            "--arg",
            "registry=chemistry/cli.json",
            "--arg",
            "label=from-cli",
            "--arg",
            "tag=alpha",
            "--arg",
            "tag=beta",
            "--flag",
            "dry_run",
            "--run-at",
            "2026-04-02T11:00:00Z",
            "--backend",
            "legacy",
        ]
    )

    assert rc == 0
    saved = sched.load_job("demo_project_runner_cli_001", repo_root=tmp_path)
    assert saved["job_kind"] == "project_runner"
    assert saved["binding"] == {"type": "none", "id": ""}
    assert saved["project_run"]["args"]["registry"] == "chemistry/cli.json"
    assert saved["project_run"]["args"]["dry_run"] is True
    assert saved["project_run"]["args"]["tag"] == ["alpha", "beta"]
    assert saved["project_run"]["launch_resolution_source"] == "experiment_contract"
    assert str(saved["project_run"]["contract_hash"]).startswith("sha256:")
    assert saved["required_context_bundle"] == "lab_core_v1"
    assert saved["project_run"]["scheduler_policy"]["max_parallel_runs"] == 1
    assert "demo_project_runner_cli_001" in capsys.readouterr().out


def test_project_runner_contract_does_not_fallback_to_manifest_when_contract_is_invalid(tmp_path: Path) -> None:
    _write_demo_project(tmp_path)
    _write_demo_contract(tmp_path, break_runner=True)

    try:
        sched.validate_job_spec(
            {
                "job_id": "demo_project_runner_invalid_contract_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-02T12:30:00Z", "timezone": "UTC"},
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "case",
                    "args": {"registry": "chemistry/blocked.json"},
                },
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected invalid contract to block launch resolution")

    assert "experiment contract" in message
    assert "manifest" not in message


def test_project_runner_contract_blocks_conflicting_context_bundle_and_forbidden_extra_refs(tmp_path: Path) -> None:
    _write_demo_contract(tmp_path)
    _mutate_demo_contract(tmp_path, lambda contract: contract["context_policy"].__setitem__("forbidden_context_refs", ["full_repo_dump"]))

    with_bundle_message = ""
    try:
        sched.validate_job_spec(
            {
                "job_id": "demo_project_runner_context_conflict_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "required_context_bundle": "lab_arch_gov_v1",
                "schedule": {"mode": "once", "run_at": "2026-04-02T12:30:00Z", "timezone": "UTC"},
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "case",
                    "args": {"registry": "chemistry/blocked.json"},
                },
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        with_bundle_message = str(exc)

    with_refs_message = ""
    try:
        sched.validate_job_spec(
            {
                "job_id": "demo_project_runner_context_forbidden_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-02T12:31:00Z", "timezone": "UTC"},
                "context_refs": ["full_repo_dump"],
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "case",
                    "args": {"registry": "chemistry/blocked.json"},
                },
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        with_refs_message = str(exc)

    assert "conflicts with experiment contract" in with_bundle_message
    assert "full_repo_dump" in with_refs_message


def test_project_runner_contract_forbids_additional_context_refs_when_contract_disallows_them(tmp_path: Path) -> None:
    _write_demo_contract(tmp_path)
    _mutate_demo_contract(tmp_path, lambda contract: contract["context_policy"].__setitem__("additional_context_refs_allowed", False))

    try:
        sched.validate_job_spec(
            {
                "job_id": "demo_project_runner_additional_context_block_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-02T12:45:00Z", "timezone": "UTC"},
                "additional_context_refs": ["docs/extra.md"],
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "case",
                    "args": {"registry": "chemistry/blocked.json"},
                },
            },
            repo_root=tmp_path,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected contract to reject additional context refs")

    assert "forbids additional context refs" in message








def test_validate_project_runner_spec_uses_explicit_repo_root(tmp_path: Path) -> None:
    _write_demo_contract(tmp_path)

    spec = sched.validate_job_spec(
        {
            "job_id": "demo_project_runner_validate_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": "2026-04-02T12:00:00Z", "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/validate.json"},
                "allow_manifest_compatibility": True,
            },
        },
        repo_root=tmp_path,
    )

    assert spec["project_run"]["project_id"] == "demo"
    assert spec["project_run"]["runner_id"] == "case"
    assert spec["required_context_bundle"] == "lab_core_v1"


def test_project_runner_failed_subprocess_writes_error_receipt(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    _write_demo_contract(tmp_path)
    failing_script = tmp_path / "experiments" / "projects" / "demo" / "_runner" / "run_demo.py"
    failing_script.write_text(
        """#!/usr/bin/env python3
from __future__ import annotations
import sys
print("demo runner failing intentionally")
raise SystemExit(7)
""",
        encoding="utf-8",
    )

    sched.save_job(
        {
            "job_id": "demo_project_runner_fail_001",
                "enabled": True,
                "binding": {"type": "none"},
                "job_kind": "project_runner",
                "schedule": {"mode": "once", "run_at": "2026-04-02T13:00:00Z", "timezone": "UTC"},
                "required_context_bundle": "lab_core_v1",
                "project_run": {
                    "project_id": "demo",
                    "runner_id": "case",
                "args": {"registry": "chemistry/fail.json"},
            },
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "demo_project_runner_fail_001",
        repo_root=tmp_path,
        trigger="manual",
        force=True,
        now_utc="2026-04-02T13:00:00Z",
    )

    assert result["ok"] is False
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["job_kind"] == "project_runner"
    assert receipt["job_shape"] == "experiment_run_job"
    assert receipt["job_shape_source"] == "explicit"
    assert receipt["job_shape_compatibility_mode"] is False
    assert receipt["status"] == "error"
    assert receipt["error"] == "project_runner_exit_code:7"
    assert receipt["exit_code"] == 7
    assert "demo runner failing intentionally" in str(receipt["stdout_tail"])
    assert receipt["resource_claims"]["release_error"] == ""
    lineage = resource_claim_ledger.read_claim_lineage(receipt["resource_claims"]["claims"][0]["claim_uid"], repo_root=tmp_path)
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert "job_status=error" in lineage[-1]["detail"]


def test_project_runner_runtime_claim_release_helper_rejects_out_of_order_and_deduplicates_duplicate_release(
    tmp_path: Path,
) -> None:
    _write_demo_contract(tmp_path)
    spec = sched.validate_job_spec(
        {
            "job_id": "demo_project_runner_claim_helper_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": "2026-04-02T14:00:00Z", "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/helper.json"},
            },
        },
        repo_root=tmp_path,
    )

    try:
        sched._record_project_runner_runtime_claim_release(
            spec=spec,
            run_id="sj_out_of_order",
            terminal_status="ok",
            recorded_at_utc="2026-04-02T14:00:02Z",
            repo_root=tmp_path,
        )
    except ValueError as exc:
        message = str(exc)
    else:
        raise AssertionError("expected release without acquisition to fail closed")
    assert "prior acquired state" in message

    claims = sched._record_project_runner_runtime_claim_acquired(
        spec=spec,
        run_id="sj_dup_release",
        now_utc="2026-04-02T14:00:00Z",
        repo_root=tmp_path,
    )
    first = sched._record_project_runner_runtime_claim_release(
        spec=spec,
        run_id="sj_dup_release",
        terminal_status="ok",
        recorded_at_utc="2026-04-02T14:00:01Z",
        repo_root=tmp_path,
    )
    second = sched._record_project_runner_runtime_claim_release(
        spec=spec,
        run_id="sj_dup_release",
        terminal_status="ok",
        recorded_at_utc="2026-04-02T14:00:01Z",
        repo_root=tmp_path,
    )

    assert claims[0]["claim_uid"] == first[0]["claim_uid"] == second[0]["claim_uid"]
    lineage = resource_claim_ledger.read_claim_lineage(claims[0]["claim_uid"], repo_root=tmp_path)
    assert [row["status"] for row in lineage] == ["acquired", "released"]


def test_project_runner_runtime_claim_release_clamps_earlier_terminal_timestamp(tmp_path: Path) -> None:
    _write_demo_contract(tmp_path)

    spec = sched.save_job(
        {
            "job_id": "demo_project_runner_claim_clamp_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": "2026-04-02T14:00:00Z", "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/helper.json"},
            },
        },
        repo_root=tmp_path,
    )

    claims = sched._record_project_runner_runtime_claim_acquired(
        spec=spec,
        run_id="sj_project_runner_future_release",
        now_utc="2026-04-02T14:00:00Z",
        repo_root=tmp_path,
    )
    released = sched._record_project_runner_runtime_claim_release(
        spec=spec,
        run_id="sj_project_runner_future_release",
        terminal_status="ok",
        recorded_at_utc="2026-04-02T13:59:59Z",
        repo_root=tmp_path,
    )

    lineage = resource_claim_ledger.read_claim_lineage(claims[0]["claim_uid"], repo_root=tmp_path)
    assert [row["status"] for row in lineage] == ["acquired", "released"]
    assert released[0]["terminal_at_utc"] == "2026-04-02T14:00:00Z"
    assert lineage[-1]["recorded_at_utc"] == "2026-04-02T14:00:00Z"


def test_project_runner_max_parallel_runs_blocks_second_launch(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setitem(sched._DISPATCH_CONFIG, "global_concurrency_cap", 10)
    _write_demo_contract(tmp_path)
    _mutate_demo_contract(tmp_path, lambda contract: contract["resources"].__setitem__("max_parallel_runs", 1))
    now_utc = sched._utc_iso()

    saved_a = sched.save_job(
        {
            "job_id": "demo_project_runner_parallel_a_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/one.json"},
            },
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": "demo_project_runner_parallel_b_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/two.json"},
            },
        },
        repo_root=tmp_path,
    )

    sched._record_project_runner_runtime_claim_acquired(
        spec=saved_a,
        run_id="sj_existing",
        now_utc=now_utc,
        repo_root=tmp_path,
    )
    sched._write_state(
        "demo_project_runner_parallel_a_001",
        {
            "job_id": "demo_project_runner_parallel_a_001",
            "active_run_id": "sj_existing",
            "active_started_utc": now_utc,
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    (sched.locks_dir(tmp_path) / "demo_project_runner_parallel_a_001.lock.json").write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "demo_project_runner_parallel_a_001",
                "run_id": "sj_existing",
                "trigger": "scheduler",
                "pid": os.getpid(),
                "claimed_utc": now_utc,
                "heartbeat_utc": now_utc,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    result = sched.execute_job(
        "demo_project_runner_parallel_b_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc=now_utc,
    )

    assert result["ok"] is False
    assert result["reason"] == "shared_resource_waiting:project:cpu:demo:1/1"


def test_project_runner_resource_class_cap_blocks_scheduler_launch_and_records_policy(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setitem(sched._DISPATCH_CONFIG, "global_concurrency_cap", 10)
    monkeypatch.setitem(sched._DISPATCH_CONFIG, "resource_class_caps", {})
    monkeypatch.setenv("EIEL_SCHEDULER_RESOURCE_CLASS_CAPS", "cpu_light:1")
    with sched._DISPATCH_GUARD:
        sched._DISPATCH_ACTIVE_JOBS.clear()
        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
        sched._DISPATCH_ACTIVE_JOB_META.clear()
    _write_demo_contract(tmp_path)
    _mutate_demo_contract(tmp_path, lambda contract: contract["resources"].__setitem__("max_parallel_runs", 2))
    now_utc = sched._utc_iso()

    saved_a = sched.save_job(
        {
            "job_id": "demo_project_runner_resource_a_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/resource-a.json"},
            },
        },
        repo_root=tmp_path,
    )
    sched.save_job(
        {
            "job_id": "demo_project_runner_resource_b_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/resource-b.json"},
            },
        },
        repo_root=tmp_path,
    )

    sched._record_project_runner_runtime_claim_acquired(
        spec=saved_a,
        run_id="sj_existing_resource",
        now_utc=now_utc,
        repo_root=tmp_path,
    )
    sched._write_state(
        "demo_project_runner_resource_a_001",
        {
            "job_id": "demo_project_runner_resource_a_001",
            "active_run_id": "sj_existing_resource",
            "active_started_utc": now_utc,
            "last_status": "running",
        },
        repo_root=tmp_path,
    )
    (sched.locks_dir(tmp_path) / "demo_project_runner_resource_a_001.lock.json").write_text(
        json.dumps(
            {
                "schema_version": "scheduled_job_claim_v1",
                "job_id": "demo_project_runner_resource_a_001",
                "run_id": "sj_existing_resource",
                "trigger": "scheduler",
                "pid": os.getpid(),
                "claimed_utc": now_utc,
                "heartbeat_utc": now_utc,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    tick = sched.scheduler_tick(repo_root=tmp_path, now_utc=now_utc, limit=10)

    assert any(
        row["job_id"] == "demo_project_runner_resource_b_001"
        and row["reason"] == "shared_resource_waiting:job_family:scheduler:cpu_light:1/1"
        for row in tick["deferred"]
    )

    result = sched.execute_job(
        "demo_project_runner_resource_b_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc=now_utc,
    )

    assert result["ok"] is False
    assert result["deferred"] is True
    assert result["reason"] == "shared_resource_waiting:job_family:scheduler:cpu_light:1/1"
    receipt = json.loads(Path(result["receipt_path"]).read_text(encoding="utf-8"))
    assert receipt["status"] == "deferred"
    assert receipt["reason"] == "shared_resource_waiting:job_family:scheduler:cpu_light:1/1"
    assert receipt["scheduler_policy"]["resource_class"] == "cpu_light"
    assert receipt["scheduler_policy"]["max_parallel_runs"] == 2


def test_project_runner_project_semaphore_wait_records_durable_wait_reason(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setitem(sched._DISPATCH_CONFIG, "global_concurrency_cap", 10)
    _write_demo_contract(tmp_path)
    _mutate_demo_contract(tmp_path, lambda contract: contract["resources"].__setitem__("max_parallel_runs", 2))
    now_utc = sched._utc_iso()

    spec = sched.validate_job_spec(
        {
            "job_id": "demo_project_runner_waiting_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/waiting.json"},
            },
        },
        repo_root=tmp_path,
    )
    sched.save_job(spec, repo_root=tmp_path)

    resource = spec["project_run"]["contract_projection"]["shared_resource_contract"]["claim_keys"][0]
    later_utc = sched._utc_iso(sched._parse_utc(now_utc) + dt.timedelta(seconds=1))
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_project_waiting:waiting",
            "claim_uid": "existing_project_waiting",
            "claimant_uid": "worker.alpha",
            "resource": dict(resource),
            "recorded_at_utc": now_utc,
            "status": "waiting",
            "wait_reason_code": "awaiting_capacity",
            "detail": "queued first",
        },
        repo_root=tmp_path,
    )
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_project_active:waiting",
            "claim_uid": "existing_project_active",
            "claimant_uid": "worker.beta",
            "resource": dict(resource),
            "recorded_at_utc": now_utc,
            "status": "waiting",
            "wait_reason_code": "awaiting_capacity",
            "detail": "acquiring slot",
        },
        repo_root=tmp_path,
    )
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_project_active:acquired",
            "claim_uid": "existing_project_active",
            "claimant_uid": "worker.beta",
            "resource": dict(resource),
            "recorded_at_utc": later_utc,
            "status": "acquired",
            "wait_reason_code": "",
            "detail": "",
        },
        repo_root=tmp_path,
    )
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_project_active_two:waiting",
            "claim_uid": "existing_project_active_two",
            "claimant_uid": "worker.gamma",
            "resource": dict(resource),
            "recorded_at_utc": now_utc,
            "status": "waiting",
            "wait_reason_code": "awaiting_capacity",
            "detail": "taking second slot",
        },
        repo_root=tmp_path,
    )
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_project_active_two:acquired",
            "claim_uid": "existing_project_active_two",
            "claimant_uid": "worker.gamma",
            "resource": dict(resource),
            "recorded_at_utc": later_utc,
            "status": "acquired",
            "wait_reason_code": "",
            "detail": "",
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "demo_project_runner_waiting_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc=now_utc,
    )

    assert result["ok"] is False
    assert result["deferred"] is True
    assert result["reason"] == "shared_resource_waiting:project:cpu:demo:2/2"
    waits = resource_claim_ledger.wait_reasons_for_claimant("scheduled_job:demo_project_runner_waiting_001", repo_root=tmp_path)
    assert waits["wait_reasons"] == [
        {
            "claim_uid": waits["wait_reasons"][0]["claim_uid"],
            "resource": dict(resource),
            "wait_reason_code": "awaiting_capacity",
            "detail": "shared resource capacity busy for project:cpu:demo; active=2/2",
            "latest_entry_uid": waits["wait_reasons"][0]["latest_entry_uid"],
            "latest_recorded_at_utc": now_utc,
        }
    ]


def test_project_runner_shared_resource_backpressure_rejects_extra_waiters(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    monkeypatch.setitem(sched._DISPATCH_CONFIG, "global_concurrency_cap", 10)
    original_get_runtime_policy_csv = sched._runtime_policy.get_runtime_policy_csv

    def _fake_runtime_policy_csv(key: str, default: str = "", *, env_names=()):
        if key == "scheduler.resource_class_caps":
            return ["cpu_light:1"]
        return original_get_runtime_policy_csv(key, default, env_names=env_names)

    monkeypatch.setattr(sched._runtime_policy, "get_runtime_policy_csv", _fake_runtime_policy_csv)
    _write_demo_contract(tmp_path)
    _mutate_demo_contract(tmp_path, lambda contract: contract["resources"].__setitem__("max_parallel_runs", 2))
    now_utc = sched._utc_iso()

    spec = sched.validate_job_spec(
        {
            "job_id": "demo_project_runner_backpressure_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/backpressure.json"},
            },
        },
        repo_root=tmp_path,
    )
    sched.save_job(spec, repo_root=tmp_path)

    resource = job_family_resource_key("cpu_light").as_dict()
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_family_active:waiting",
            "claim_uid": "existing_family_active",
            "claimant_uid": "worker.alpha",
            "resource": dict(resource),
            "recorded_at_utc": now_utc,
            "status": "waiting",
            "wait_reason_code": "awaiting_capacity",
            "detail": "acquiring slot",
        },
        repo_root=tmp_path,
    )
    later_utc = sched._utc_iso(sched._parse_utc(now_utc) + dt.timedelta(seconds=1))
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_family_active:acquired",
            "claim_uid": "existing_family_active",
            "claimant_uid": "worker.alpha",
            "resource": dict(resource),
            "recorded_at_utc": later_utc,
            "status": "acquired",
            "wait_reason_code": "",
            "detail": "",
        },
        repo_root=tmp_path,
    )
    resource_claim_ledger.append_claim_record(
        {
            "schema_version": resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": "existing_family_waiting:waiting",
            "claim_uid": "existing_family_waiting",
            "claimant_uid": "worker.beta",
            "resource": dict(resource),
            "recorded_at_utc": now_utc,
            "status": "waiting",
            "wait_reason_code": "awaiting_capacity",
            "detail": "already queued",
        },
        repo_root=tmp_path,
    )

    result = sched.execute_job(
        "demo_project_runner_backpressure_001",
        repo_root=tmp_path,
        trigger="scheduler",
        force=False,
        now_utc=now_utc,
    )

    assert result["ok"] is False
    assert "deferred" not in result
    assert result["reason"] == "shared_resource_backpressure:job_family:scheduler:cpu_light:1/1:waiting=1"
    claimant_uid = "scheduled_job:demo_project_runner_backpressure_001"
    claim_rows = resource_claim_ledger.read_claim_records_for_resource(resource, repo_root=tmp_path)
    rejected_rows = [row for row in claim_rows if row["claimant_uid"] == claimant_uid]
    assert [row["status"] for row in rejected_rows] == ["waiting", "rejected"]


def test_project_runner_bool_args_parse_from_direct_json_spec(tmp_path: Path) -> None:
    _write_demo_contract(tmp_path)

    spec = sched.validate_job_spec(
        {
            "job_id": "demo_project_runner_bool_001",
            "enabled": True,
            "binding": {"type": "none"},
            "job_kind": "project_runner",
            "schedule": {"mode": "once", "run_at": "2026-04-02T14:00:00Z", "timezone": "UTC"},
            "project_run": {
                "project_id": "demo",
                "runner_id": "case",
                "args": {"registry": "chemistry/bool.json", "dry_run": "false"},
                "allow_manifest_compatibility": True,
            },
        },
        repo_root=tmp_path,
    )

    assert spec["project_run"]["args"]["dry_run"] is False
    assert spec["required_context_bundle"] == "lab_core_v1"




def test_project_output_env_uses_contract_declared_external_data_root(tmp_path: Path) -> None:
    _write_demo_project(tmp_path)
    _write_demo_contract(tmp_path)

    env = ensure_project_output_env(
        project_id="demo",
        repo_root=tmp_path,
        project_root=tmp_path / "experiments" / "projects" / "demo",
        env={},
    )

    assert env["EXPERIMENT_OUTPUTS_DIR"] == str((tmp_path / ".." / "demo-data").resolve())


def test_project_output_env_prefers_env_override_from_contract_declaration(tmp_path: Path) -> None:
    _write_demo_project(tmp_path)
    _write_demo_contract(tmp_path)
    _mutate_demo_contract(
        tmp_path,
        lambda contract: contract["artifacts"].update({"external_data_root": "DEMO_OUTPUTS_DIR or ~/causyl_experiment_data/demo"}),
    )

    env = ensure_project_output_env(
        project_id="demo",
        repo_root=tmp_path,
        project_root=tmp_path / "experiments" / "projects" / "demo",
        env={"DEMO_OUTPUTS_DIR": "/var/tmp/demo-outputs"},
    )

    assert env["EXPERIMENT_OUTPUTS_DIR"] == "/var/tmp/demo-outputs"
