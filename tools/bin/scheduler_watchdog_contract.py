"""Bounded watchdog workstream contract helpers.

This keeps watchdog program structure, authority boundaries, and surface
ownership in one place so the watchdog runtime and operator projections stop
duplicating the same governance story in giant modules.
"""

from __future__ import annotations

from typing import Any, Dict, List


WATCHDOG_WORKSTREAMS: List[Dict[str, Any]] = [
    {
        "uid": "PIS-20260401-193000Z-A4M7",
        "scope_id": "maintenance_authority_boundary",
        "title": "Watchdog Maintenance Ledger And Authority Boundary",
        "role": "decision_only",
        "execution_authority": "scheduler",
        "read_only_surfaces": [
            "scheduler_job_state",
            "scheduler_job_lockfiles",
            "lab_recovery",
            "lab_task_register",
            "commit_journal",
        ],
        "watchdog_owned_surfaces": [
            "watchdog_maintenance_ledger",
            "watchdog_report_pointers",
        ],
        "non_goals": [
            "direct_truth_mutation",
            "scheduler_bypass_execution",
        ],
        "depends_on": [],
    },
    {
        "uid": "PIS-20260401-193100Z-B5N8",
        "scope_id": "safe_selfheal_playbooks",
        "title": "Watchdog Playbooks, Safe Self-Heal, And Containment (Decision-Only)",
        "role": "decision_only",
        "execution_authority": "scheduler",
        "read_only_surfaces": [
            "scheduler_state_projection",
            "operator_snapshot_projection",
        ],
        "watchdog_owned_surfaces": [
            "scheduler_work_item_packages",
            "containment_records",
            "playbook_decisions",
        ],
        "non_goals": [
            "unbounded_auto_repair",
            "operator_bypass",
        ],
        "depends_on": ["PIS-20260401-193000Z-A4M7"],
    },
    {
        "uid": "PIS-20260401-193200Z-C6P9",
        "scope_id": "truth_reconciliation",
        "title": "Watchdog Truth Reconciliation Engine",
        "role": "diagnose_and_classify",
        "execution_authority": "none",
        "read_only_surfaces": [
            "scheduler_state",
            "task_register",
            "recovery_state",
            "delegation_proof",
        ],
        "watchdog_owned_surfaces": [
            "truth_reconciliation_report",
        ],
        "non_goals": [
            "projection_driven_mutation",
            "invented_clean_truth",
        ],
        "depends_on": ["PIS-20260401-193000Z-A4M7"],
    },
    {
        "uid": "PIS-20260401-193300Z-D7Q1",
        "scope_id": "incident_and_prediction",
        "title": "Watchdog Incident System And Prediction",
        "role": "evidence_backed_advisory",
        "execution_authority": "none",
        "read_only_surfaces": [
            "watchdog_maintenance_ledger",
            "truth_reconciliation_report",
        ],
        "watchdog_owned_surfaces": [
            "watchdog_incident_store",
            "watchdog_prediction_store",
        ],
        "non_goals": [
            "prediction_overrides_current_evidence",
            "direct_repair_execution",
        ],
        "depends_on": [
            "PIS-20260401-193000Z-A4M7",
            "PIS-20260401-193200Z-C6P9",
        ],
    },
    {
        "uid": "PIS-20260401-193400Z-E8R2",
        "scope_id": "bounded_codex_escalation",
        "title": "Bounded Codex Escalation For Watchdog Unresolved Issues",
        "role": "advisory_or_bounded_repair_only",
        "execution_authority": "verified_followup_only",
        "read_only_surfaces": [
            "watchdog_incident_store",
            "watchdog_prediction_store",
            "truth_reconciliation_report",
        ],
        "watchdog_owned_surfaces": [
            "watchdog_escalation_bundle_store",
        ],
        "non_goals": [
            "unscoped_repo_mutation",
            "codex_as_truth_owner",
        ],
        "depends_on": [
            "PIS-20260401-193100Z-B5N8",
            "PIS-20260401-193200Z-C6P9",
        ],
    },
]


def watchdog_authority_boundary() -> Dict[str, Any]:
    return {
        "schema_version": "scheduler_watchdog_authority_boundary_v1",
        "watchdog_role": "diagnose_classify_and_bounded_progress_recover",
        "execution_authority": "scheduler_plus_bounded_watchdog_progress_recovery",
        "codex_role": "advisory_or_bounded_repair_only",
        "watchdog_owned_direct_surfaces": sorted(
            [
                "delegation_proof_report",
                "scheduler_launch_tick_idle_only",
                "scheduler_run_now_request_idle_only",
                "watchdog_codex_execution_receipts",
                "watchdog_escalation_bundle_store",
                "watchdog_incident_store",
                "watchdog_maintenance_ledger",
                "watchdog_prediction_store",
                "watchdog_report_pointers",
            ]
        ),
        "direct_watchdog_mutation_forbidden": [
            "commit_journal",
            "lab_recovery",
            "lab_task_register",
            "scheduler_job_lockfiles",
            "scheduler_job_state",
        ],
        "canonical_truth_owners": {
            "scheduler_state": "scheduler",
            "task_register": "lab_task_register",
            "recovery_state": "lab_recovery",
            "commit_journal": "commit_journal",
            "watchdog_maintenance_ledger": "watchdog",
            "watchdog_incidents": "watchdog",
            "watchdog_predictions": "watchdog",
            "watchdog_escalations": "watchdog",
            "delegation_proof_report": "delegation_proof",
        },
        "repair_execution_requires": [
            "durable_scheduler_work_item_or_job",
            "receipt_correlation_back_to_maintenance_run",
            "verified_receipt_for_truth_closure",
        ],
    }


def watchdog_workstream_projection() -> Dict[str, Any]:
    return {
        "schema_version": "scheduler_watchdog_workstream_projection_v1",
        "parent_uid": "PIS-20260402-062800Z-U1J7",
        "title": "Decompose And Bound The Watchdog Workstream Before Further Fanout",
        "decision_only_program": False,
        "rule": "watchdog remains subordinate to scheduler, task-register, recovery, and receipt truth owners, but may perform bounded idle-only progress recovery and bounded Codex escalation",
        "implementation_order": [row["uid"] for row in WATCHDOG_WORKSTREAMS],
        "workstreams": [dict(row) for row in WATCHDOG_WORKSTREAMS],
    }
