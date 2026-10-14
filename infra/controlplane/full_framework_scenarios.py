"""V4N8 full-framework scenario catalog and deterministic validation helpers."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

from infra.controlplane import full_framework_release_contract as _release_contract

FULL_FRAMEWORK_SCENARIOS_SCHEMA_VERSION = "lab_assistant_full_framework_scenarios_v1"
V4N8_UID = "PIS-20260312-235056Z-V4N8"
STABILIZATION_REGRESSION_WAVE_UID = "PIS-20260402-062000Z-L2A8"
STABILIZATION_REGRESSION_WAVE_SCHEMA_VERSION = "lab_assistant_stabilization_regression_wave_v1"

SCENARIOS: List[Dict[str, Any]] = [
    {
        "uid": "PIS-20260313-105059Z-2B7D",
        "sequence": 1,
        "title": "Fresh Request to Completed Result Full-Framework Scenario Contract",
        "goal": "A new operator request becomes a durably completed result with evidence-backed writeback and operator-visible closure.",
        "cross_systems": ["DD8C", "BD05", "01A2", "E4F1", "S6U2", "R8T3", "2DB7", "O7P5", "B707"],
        "start_condition": "fresh request with no existing active-turn dependency",
        "success_condition": "canonical writeback and completion are durable and operator-visible",
    },
    {
        "uid": "PIS-20260313-105100Z-9434",
        "sequence": 2,
        "title": "Restart During Active Work and Honest Recovery Scenario Contract",
        "goal": "Restart during active work recovers honestly with no duplicate launch theater.",
        "cross_systems": ["ADD9", "2848", "5437", "E4F1", "S6U2", "R8T3", "2DB7", "O7P5"],
        "start_condition": "active delegated work exists before restart",
        "success_condition": "post-restart state is interrupted/reconciling until durable truth proves otherwise",
    },
    {
        "uid": "PIS-20260313-105101Z-406F",
        "sequence": 3,
        "title": "Overnight Scheduled Chain to Morning Result Scenario Contract",
        "goal": "An unattended scheduled chain can be reattached to in the morning without transcript archaeology.",
        "cross_systems": ["01A2", "D1E7", "E4F1", "S6U2", "G7B1", "B707", "BD05", "R8T3", "F4A9", "O7P5"],
        "start_condition": "operator schedules a deferred/background chain",
        "success_condition": "morning summary maps durable outcomes back to canonical next actions",
    },
    {
        "uid": "PIS-20260313-105102Z-04C8",
        "sequence": 4,
        "title": "Clarification and Replanning Mid-Execution Scenario Contract",
        "goal": "New clarification updates durable plan truth and safely transitions already-launched work.",
        "cross_systems": ["H3K1", "BD05", "E4F1", "S6U2", "R8T3", "2DB7", "O7P5"],
        "start_condition": "initial work has launched but the operator provides clarifying input",
        "success_condition": "plan revision is durable, lineage is preserved, and stale execution is visibly bounded",
    },
    {
        "uid": "PIS-20260313-105104Z-1544",
        "sequence": 5,
        "title": "Failure Handling and Honest Degradation Scenario Contract",
        "goal": "Critical subsystem failures degrade honestly, preserve recovery state, and offer next actions.",
        "cross_systems": ["E4F1", "B707", "5437", "BD05", "R8T3", "O7P5"],
        "start_condition": "one or more critical subsystems fail during active work",
        "success_condition": "blocked/degraded/interrupted states are durable, actionable, and not misreported as success",
    },
]


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def full_framework_scenarios_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": FULL_FRAMEWORK_SCENARIOS_SCHEMA_VERSION,
        "generated_utc": _utc_now_iso(),
        "uid": V4N8_UID,
        "scenario_uids": [row["uid"] for row in SCENARIOS],
        "scenarios": list(SCENARIOS),
        "rules": [
            "Scenario sequence is operator-journey ordered and deterministic.",
            "Each scenario must define explicit start and success conditions.",
            "Cross-system coverage must remain explicit so overlap with subsystem-local suites stays bounded.",
        ],
    }


def validate_full_framework_scenarios(snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(snapshot or full_framework_scenarios_snapshot())
    errors: List[str] = []
    scenarios = list(payload.get("scenarios") or [])
    observed_uids = [str(row.get("uid") or "") for row in scenarios]
    if observed_uids != [row["uid"] for row in SCENARIOS]:
        errors.append("scenario uid order does not match the canonical V4N8 sequence")
    observed_sequences = [int(row.get("sequence") or 0) for row in scenarios]
    if observed_sequences != [1, 2, 3, 4, 5]:
        errors.append("scenario sequence must be contiguous 1..5")
    for row in scenarios:
        if not str(row.get("start_condition") or "").strip():
            errors.append(f"{row.get('uid')}: missing start_condition")
        if not str(row.get("success_condition") or "").strip():
            errors.append(f"{row.get('uid')}: missing success_condition")
        if not list(row.get("cross_systems") or []):
            errors.append(f"{row.get('uid')}: missing cross_system coverage")
    return {
        "schema_version": "lab_assistant_full_framework_scenarios_validation_v1",
        "uid": V4N8_UID,
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }


STABILIZATION_REGRESSION_WAVE: List[Dict[str, Any]] = [
    {
        "seam_id": "intake_to_snapshot",
        "label": "Intake -> task register -> snapshot truth",
        "selectors": [
            "tests/control_plane/webui/test_e2e_intake_route_persist_snapshot.py::test_e2e_clarification_intake_blocks_and_snapshot_waits",
        ],
    },
    {
        "seam_id": "workflow_recovery",
        "label": "Workflow restart / orchestration recovery truth",
        "selectors": [
            "tests/control_plane/webui/test_webui_agents_orchestration.py::test_run_workflow_reconciles_inflight_restart_to_interrupted_recovery_checkpoint",
        ],
    },
    {
        "seam_id": "scheduled_forensics",
        "label": "Scheduled-job reconciliation + forensic receipt truth",
        "selectors": [
            "tests/control_plane/test_scheduled_jobs.py::test_failed_scheduled_job_receipt_includes_forensic_debug_kit",
        ],
    },
    {
        "seam_id": "operator_snapshot_language",
        "label": "Operator snapshot copy + self-check degradation truth",
        "selectors": [
            "tests/control_plane/webui/test_lab_assistant_operator_snapshot.py::test_snapshot_status_language_exposes_copy_for_waiting_on_operator",
            "tests/control_plane/webui/test_lab_assistant_operator_snapshot.py::test_snapshot_self_check_blocked_prevents_idle_success_language",
        ],
    },
    {
        "seam_id": "host_restore_recovery",
        "label": "Backup/restore and replacement-host recovery drill truth",
        "selectors": [
            "tests/control_plane/test_full_framework_scenarios.py::test_stabilization_regression_wave_includes_host_restore_recovery_seam",
            "tests/control_plane/test_full_framework_scenarios.py::test_host_recovery_drill_round_trip_preserves_durable_truth",
        ],
    },
]


def stabilization_regression_wave_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": STABILIZATION_REGRESSION_WAVE_SCHEMA_VERSION,
        "generated_utc": _utc_now_iso(),
        "uid": STABILIZATION_REGRESSION_WAVE_UID,
        "seams": list(STABILIZATION_REGRESSION_WAVE),
        "pytest_args": [selector for seam in STABILIZATION_REGRESSION_WAVE for selector in list(seam.get("selectors") or [])],
        "rules": [
            "Every stabilization seam must contribute at least one focused selector.",
            "Selectors should stay narrow enough that a failure still identifies the broken seam.",
            "The wave is a bounded seam gate, not a disguised full-repo integration run.",
        ],
    }


def validate_stabilization_regression_wave(snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(snapshot or stabilization_regression_wave_snapshot())
    errors: List[str] = []
    seams = list(payload.get("seams") or [])
    if str(payload.get("uid") or "") != STABILIZATION_REGRESSION_WAVE_UID:
        errors.append("stabilization regression wave uid mismatch")
    expected = [row["seam_id"] for row in STABILIZATION_REGRESSION_WAVE]
    observed = [str(row.get("seam_id") or "") for row in seams]
    if observed != expected:
        errors.append("stabilization seam order does not match the canonical L2A8 wave")
    for row in seams:
        selectors = [str(item).strip() for item in list(row.get("selectors") or []) if str(item).strip()]
        if not selectors:
            errors.append(f"{row.get('seam_id')}: missing selectors")
    return {
        "schema_version": "lab_assistant_stabilization_regression_wave_validation_v1",
        "uid": STABILIZATION_REGRESSION_WAVE_UID,
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }


def release_channel_integrity_snapshot() -> Dict[str, Any]:
    return _release_contract.release_channel_integrity_snapshot()


def validate_release_channel_integrity(snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return _release_contract.validate_release_channel_integrity(snapshot)
