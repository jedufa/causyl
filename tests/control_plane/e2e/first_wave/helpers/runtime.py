from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from experiments_shared import validate_control_action_policy, validate_experiment_contract
from infra.controlplane import sched as v2_sched
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_state as assistant_state
from infra.webui import server_lab_assistant_task_register as task_register


def write_workflow_contract(root: Path) -> None:
    payload = json.loads(
        Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8")
    )
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def write_prompt_template(root: Path) -> None:
    prompt_path = root / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement `<CHILD_UID>`.\n```\n",
        encoding="utf-8",
    )


def write_production_wiring_contract(root: Path) -> None:
    payload = json.loads(
        Path("infra/controlplane/sched/production_wiring_contract.json").read_text(encoding="utf-8")
    )
    contract_path = root / "infra" / "controlplane" / "sched" / "production_wiring_contract.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def init_operator_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)
    host.bootstrap_host_baseline(root)
    assistant_state.load_or_init_assistant_state(root)
    task_register.load_or_init_task_register(root)


def write_demo_project_contract(root: Path) -> None:
    project_root = root / "experiments" / "projects" / "demo"
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
        "compatibility": {"contract_family": "experiment_contract", "major": 1, "minor": 0},
        "project": {"project_id": "demo", "display_name": "Demo Experiment", "owner": "causyl", "tags": ["demo"]},
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
                "types": {"registry": "string", "label": "string", "tag": "string_list", "dry_run": "bool"},
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
            "estimated_footprint": {"gpu_count": 0, "cpu_cores": 1, "ram_gb": 1},
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


def state_dir(root: Path) -> Path:
    path = root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "state"
    path.mkdir(parents=True, exist_ok=True)
    return path


def lock_dir(root: Path) -> Path:
    path = root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "locks"
    path.mkdir(parents=True, exist_ok=True)
    return path


def write_job_state(root: Path, *, job_id: str, payload: dict[str, Any]) -> Path:
    path = state_dir(root) / f"{job_id}.json"
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def write_success_state(root: Path, *, job_id: str) -> None:
    write_job_state(
        root,
        job_id=job_id,
        payload={
            "job_id": job_id,
            "active_run_id": "",
            "last_shape_canonical_state": "succeeded",
            "last_status": "ok",
            "last_receipt_path": f"receipts/{job_id}.json",
        },
    )


def write_failed_state(root: Path, *, job_id: str) -> None:
    write_job_state(
        root,
        job_id=job_id,
        payload={
            "job_id": job_id,
            "active_run_id": "",
            "last_shape_canonical_state": "failed",
            "last_status": "failed_verification",
            "last_receipt_path": f"receipts/{job_id}.json",
        },
    )


def write_stale_active_claim(root: Path, *, job_id: str, run_id: str, pid: int = 999_999) -> Path:
    path = lock_dir(root) / f"{job_id}.lock.json"
    path.write_text(
        json.dumps(
            {
                "job_id": job_id,
                "run_id": run_id,
                "pid": pid,
                "claimed_utc": "2026-04-11T00:02:00Z",
                "heartbeat_utc": "2026-04-11T00:02:05Z",
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def create_existing_fanout_family(root: Path) -> dict[str, Any]:
    write_workflow_contract(root)
    write_prompt_template(root)
    write_production_wiring_contract(root)
    intents = IntentSheetLedger(repo_root=root)
    intents.create_sheet(
        uid_override="IS-20260410-181000Z-FN01",
        title="Existing Fanout Intent",
        intent_markdown="# Existing Fanout Intent\n\nUse fanout as implementation methodology.\n",
        project_id="causyl",
    )
    spec = {
        "parent": {"uid": "PIS-20260410-181001Z-PA01", "title": "Materialized Fanout Parent"},
        "parallel_children": [
            {"uid": "PIS-20260410-181002Z-AA01", "title": "Explore A"},
            {"uid": "PIS-20260410-181003Z-BB02", "title": "Explore B"},
            {"uid": "PIS-20260410-181004Z-CC03", "title": "Explore C"},
        ],
        "integration_child": {"uid": "PIS-20260410-181005Z-IN04", "title": "Integrate"},
        "validation_child": {"uid": "PIS-20260410-181006Z-VA05", "title": "Validate"},
        "closeout_child": {"uid": "PIS-20260410-181007Z-CL06", "title": "Close"},
    }
    from infra.controlplane.sched.fanout import materialize_fanout_for_intent

    result = materialize_fanout_for_intent("IS-20260410-181000Z-FN01", spec, repo_root=root)
    plan = v2_sched.compile_scheduler_plan(
        parent_uid=result["parent_uid"],
        run_at_utc="2026-04-10T18:20:00Z",
        repo_root=root,
    )
    return {"materialized": result, "plan": plan}


def create_minimal_family(root: Path) -> dict[str, str]:
    write_workflow_contract(root)
    write_prompt_template(root)
    write_production_wiring_contract(root)
    parent_uid = "PIS-20260410-040000Z-PA04"
    intent_uid = "IS-20260410-040001Z-IN04"
    implementation_uid = "PIS-20260410-040002Z-IM04"
    validation_uid = "PIS-20260410-040003Z-VA04"
    closeout_uid = "PIS-20260410-040004Z-CL04"

    parent_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": [implementation_uid, validation_uid, closeout_uid],
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    intent_dir = root / "governance/ledger/intent_sheets/causyl/active/2026/04" / f"{intent_uid}__intent"
    intent_dir.mkdir(parents=True, exist_ok=True)
    (intent_dir / "intent.md").write_text("# Intent\n", encoding="utf-8")
    (intent_dir / "meta.json").write_text(
        json.dumps({"uid": intent_uid, "status": "draft"}, indent=2) + "\n",
        encoding="utf-8",
    )
    (intent_dir / "events.jsonl").write_text("", encoding="utf-8")

    rows = [
        (implementation_uid, "implementation", "Implementation", "execution_child", 1, []),
        (validation_uid, "validation", "Validation", "intent_validation_suite", 2, [implementation_uid]),
        (closeout_uid, "closeout", "Closeout", "intent_family_closeout", 3, [implementation_uid, validation_uid]),
    ]
    for uid, slug, title, relationship, rank, deps in rows:
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "draft",
                    "integration_meta": {"relationship": relationship},
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": relationship,
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    return {
        "parent": parent_uid,
        "intent": intent_uid,
        "implementation": implementation_uid,
        "validation": validation_uid,
        "closeout": closeout_uid,
    }
