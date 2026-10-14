from __future__ import annotations

import json
import shutil
from pathlib import Path

from infra.controlplane import scheduled_jobs as sched
from experiments_shared import validate_control_action_policy
from experiments_shared import validate_experiment_contract
from infra.controlplane import verification_workflow_launch_contract as contract


REPO_ROOT = Path(__file__).resolve().parents[2]


def verification_workflow_launch_payload(*, request_id: str) -> dict:
    return {
        "request_id": request_id,
        "verification_goal": "Run one bounded scheduler-owned verification workflow slice through the plane.",
        "requested_output_mode": "child_pis_family",
        "max_iterations": 2,
        "operator_note": "Live scheduler-owned bounded verification workflow proof.",
        "selected_block_summary": {
            "schema_version": "verification_workflow_selected_block_summary_v1",
            "block_ref": "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
            "block_label": "scheduler launch handoff",
            "qualification_verdict": "expand",
            "operator_goal": "stress one real scheduler-driven verification workflow slice",
            "risk_unit_summary": "scheduler launch plus the adjacent queue-claim seam is the smallest honest unit",
            "qualification_basis": [
                "the launch handoff is the operator-facing claim under attack",
                "the queue-claim seam can change whether launch truth is honest",
            ],
            "included_paths": [
                "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py",
                "runtime/state/scheduler_watchdog/",
            ],
            "adjacent_truth_seams": [
                {
                    "seam_id": "scheduler_claim_state",
                    "relationship_to_block": "queue claim and active-run materialization",
                    "disposition": "included",
                    "note": "Needed so launch truth does not ignore claimed-but-unmaterialized runs.",
                }
            ],
            "expansion_notes": [
                "Include the queue-claim seam because it can falsify launch truth without changing the operator-selected file alone."
            ],
            "rejection_reasons": [],
            "bounded_operator_summary": (
                "The plane will launch verification planning for scheduler launch handoff with the named adjacent "
                "seams included before execution. This is a launch contract only; it does not prove runtime "
                "behavior, completion, or repair yield."
            ),
        },
        "phase_b_anchor_payload": {
            "scenarios": [
                {
                    "scenario_id": "scenario::active-busy-masks-storage-failure",
                    "execution_class": "runnable_now",
                    "scenario_summary": "storage failure must not be hidden by active_busy",
                    "required_evidence": ["job_receipt", "finding_receipt"],
                    "repo_commit_sha": "abc123",
                    "repair_triggerable": True,
                }
            ],
            "raw_findings": [
                {
                    "finding_id": "finding::1",
                    "truth_boundary": "watchdog_truth",
                    "symptom_class": "unsafe_action",
                    "operator_claim_under_attack": "safe repair offered",
                    "severity_hint": "critical",
                    "production_reachability_hint": "reachable_in_normal_operation",
                    "affected_truths": ["watchdog_truth"],
                    "repair_signal": "repair_worthy",
                }
            ],
            "budget": {"max_budget_units": 4},
            "actual": {
                "spent_budget_units": 2,
                "repeated_cluster_ratio": 0.0,
                "new_signal_ratio": 0.8,
                "dut_improvement_delta": 0.2,
                "repair_confidence": 0.7,
                "risk_score": 0.2,
                "completed_iterations": 1,
            },
            "baseline_health_snapshot": {"open_bug_classes": 3, "critical_open": 1},
            "post_campaign_health_snapshot": {"open_bug_classes": 2, "critical_open": 0},
            "reduced_bug_classes": ["active_busy_masking"],
            "unresolved_bug_classes": ["long_run_unsupervised_unproven"],
            "residual_risk": "still_not_safe_unattended",
            "blocked_claims": ["broad_robustness_unproven"],
        },
    }


def broad_scheduler_verification_payload(*, request_id: str) -> dict:
    return {
        "request_id": request_id,
        "verification_goal": "Verify the scheduler",
        "operator_note": "Broad scheduler verification campaign requested from the plane.",
        "max_iterations": 4,
    }


def drive_verification_campaign_to_completion(*, repo_root: Path, campaign_path: Path, start_minute: int = 11) -> dict:
    minute = int(start_minute)
    while True:
        campaign = json.loads(campaign_path.read_text(encoding="utf-8"))
        if str(campaign.get("execution_status") or "") == "completed":
            return campaign
        for row in list(campaign.get("queued_child_jobs") or []):
            sched.execute_job(
                str(row["job_id"]),
                repo_root=repo_root,
                trigger="manual",
                force=True,
                now_utc=f"2026-04-14T10:{minute:02d}:00Z",
            )
            minute += 1
        sched.execute_job(
            str(campaign["closeout_job_id"]),
            repo_root=repo_root,
            trigger="manual",
            force=True,
            now_utc=f"2026-04-14T10:{minute:02d}:00Z",
        )
        minute += 1


def stage_verification_workflow_repo(repo_root: Path) -> None:
    repo_root.mkdir(parents=True, exist_ok=True)
    _copy_contract_artifacts(repo_root)
    _write_project_runner_contract(repo_root)


def _copy_contract_artifacts(repo_root: Path) -> None:
    rel_paths = (
        contract.ARTIFACT_REL,
        contract.R5S8_LAUNCH_REQUEST_CONTRACT_ARTIFACT_REL,
        contract.S6T9_SELECTED_BLOCK_CONTRACT_ARTIFACT_REL,
    )
    for rel in rel_paths:
        src = REPO_ROOT / rel
        dst = repo_root / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)


def _write_project_runner_contract(repo_root: Path) -> None:
    project_root = repo_root / "experiments" / "projects" / "verification_workflow"
    runner_root = project_root / "_runner"
    runner_root.mkdir(parents=True, exist_ok=True)
    (project_root / "_config").mkdir(parents=True, exist_ok=True)
    (project_root / "_spec" / "registry" / "v1").mkdir(parents=True, exist_ok=True)

    script = runner_root / "run_verification_workflow.py"
    script.write_text(
        f"""#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

TMP_REPO = Path(__file__).resolve().parents[4]
ACTUAL_REPO = Path({str(REPO_ROOT)!r})
for candidate in (str(TMP_REPO), str(ACTUAL_REPO)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from infra.controlplane.verification_workflow_launch import (
    run_verification_campaign_closeout,
    run_verification_scenario_packet,
    run_verification_scheduler_packet,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run one bounded verification workflow scheduler packet.")
    parser.add_argument("--request_fingerprint", required=True)
    parser.add_argument("--scheduler_packet_path", required=True)
    parser.add_argument("--scenario_packet_path")
    parser.add_argument("--campaign_closeout_path")
    parser.add_argument("--dry_run", action="store_true")
    args = parser.parse_args()
    packet_path = (TMP_REPO / args.scheduler_packet_path).resolve()
    packet = json.loads(packet_path.read_text(encoding="utf-8"))
    if args.dry_run:
        result = {{
            "schema_version": "verification_workflow_project_runner_dry_run_v1",
            "request_fingerprint": args.request_fingerprint,
            "scheduler_packet_path": args.scheduler_packet_path,
        }}
    elif args.scenario_packet_path:
        scenario_path = (TMP_REPO / args.scenario_packet_path).resolve()
        scenario_packet = json.loads(scenario_path.read_text(encoding="utf-8"))
        result = run_verification_scenario_packet(scenario_packet, repo_root=TMP_REPO)
    elif args.campaign_closeout_path:
        closeout_path = (TMP_REPO / args.campaign_closeout_path).resolve()
        closeout_packet = json.loads(closeout_path.read_text(encoding="utf-8"))
        result = run_verification_campaign_closeout(closeout_packet, repo_root=TMP_REPO)
    else:
        result = run_verification_scheduler_packet(packet, repo_root=TMP_REPO)
    Path("verification_workflow_run_output.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\\n",
        encoding="utf-8",
    )
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""",
        encoding="utf-8",
    )

    exp_contract = {
        "schema_version": "experiment_contract_v1",
        "contract_hash": "",
        "compatibility": {"contract_family": "experiment_contract", "major": 1, "minor": 0},
        "project": {
            "project_id": "verification_workflow",
            "display_name": "Verification Workflow",
            "owner": "causyl",
            "tags": ["the_plane", "verification_workflow"],
        },
        "launch": {
            "default_runner_id": "anchor",
            "supported_runners": ["anchor"],
            "entrypoints": {
                "anchor": {
                    "command": [
                        "python3",
                        "-u",
                        "experiments/projects/verification_workflow/_runner/run_verification_workflow.py",
                    ],
                    "working_dir": "experiments/projects/verification_workflow",
                    "env": {"PYTHONUNBUFFERED": "1"},
                }
            },
            "args": {
                "required": ["request_fingerprint", "scheduler_packet_path"],
                "optional": ["dry_run", "scenario_packet_path", "campaign_closeout_path"],
                "types": {
                    "request_fingerprint": "string",
                    "scheduler_packet_path": "string",
                    "scenario_packet_path": "string",
                    "campaign_closeout_path": "string",
                    "dry_run": "bool",
                },
            },
        },
        "configuration": {
            "profiles_root": "experiments/projects/verification_workflow/_config",
            "registry_root": "experiments/projects/verification_workflow/_spec/registry/v1",
            "override_modes": ["patch"],
            "default_profile": "baseline",
        },
        "observability": {
            "status_files": [{"path": "verification_workflow_run_output.json", "type": "json", "priority": 1}],
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
            "primary_artifacts": ["verification_workflow_run_output.json"],
            "recipes": ["summary_report"],
            "default_recipe": "summary_report",
        },
        "resources": {
            "resource_class": "cpu_light",
            "project_semaphore": "cpu/verification_workflow",
            "max_parallel_runs": 1,
            "estimated_footprint": {"gpu_count": 0, "cpu_cores": 1, "ram_gb": 1},
        },
        "limitations": [],
        "artifacts": {
            "repo_tracked_outputs": ["verification_workflow_run_output.json"],
            "external_data_root": "../verification-workflow-data",
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
    exp_contract["contract_hash"] = validate_experiment_contract(exp_contract)["computed_contract_hash"]
    (runner_root / "experiment_contract.json").write_text(
        json.dumps(exp_contract, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    policy = {
        "schema_version": "control_action_policy_v1",
        "policy_hash": "",
        "project_id": "verification_workflow",
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
