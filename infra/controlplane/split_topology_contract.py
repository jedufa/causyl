"""ABCD split-topology contract snapshots and deterministic seam helpers."""

from __future__ import annotations

import datetime as _dt
from typing import Any, Dict, List

SPLIT_TOPOLOGY_SCHEMA_VERSION = "lab_assistant_split_topology_contract_v1"
ABCD_UID = "PIS-20260313-091500Z-ABCD"
Q3U5_UID = "PIS-20260313-100604Z-Q3U5"
R4V6_UID = "PIS-20260313-100605Z-R4V6"
S5W7_UID = "PIS-20260313-100606Z-S5W7"
T6X8_UID = "PIS-20260313-100607Z-T6X8"


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def classify_replacement_backend_startup(
    *,
    has_durable_truth: bool,
    continuity_proven: bool,
    duplicate_launch_risk: bool,
) -> Dict[str, Any]:
    if not has_durable_truth:
        return {
            "state": "blocked_missing_durable_truth",
            "authoritative": False,
            "operator_state": "blocked",
            "next_action": "restore durable receipts/state before replacement startup",
        }
    if duplicate_launch_risk:
        return {
            "state": "interrupted_reconcile_duplicate_risk",
            "authoritative": False,
            "operator_state": "degraded",
            "next_action": "reconcile prior launch markers before accepting new authority",
        }
    if not continuity_proven:
        return {
            "state": "cold_start_reconstruct_from_durable_truth",
            "authoritative": False,
            "operator_state": "reconciling",
            "next_action": "reconstruct active execution classes from durable truth only",
        }
    return {
        "state": "replacement_ready_after_reconstruction",
        "authoritative": False,
        "operator_state": "ready",
        "next_action": "replacement backend may take over after explicit authority handoff",
    }


def classify_backend_rejoin(
    *,
    durable_truth_present: bool,
    local_state_is_stale: bool,
    authority_proven: bool,
) -> Dict[str, Any]:
    if not durable_truth_present:
        return {
            "state": "blocked_missing_durable_truth",
            "authoritative": False,
            "operator_state": "blocked",
            "next_action": "do not trust returning backend memory without durable truth",
        }
    if local_state_is_stale or not authority_proven:
        return {
            "state": "non_authoritative_rejoin",
            "authoritative": False,
            "operator_state": "degraded",
            "next_action": "reconcile with durable truth before any authority is possible",
        }
    return {
        "state": "authoritative_after_rejoin_reproof",
        "authoritative": True,
        "operator_state": "ready",
        "next_action": "resume nominal authority only after reconciliation is complete",
    }


def runtime_control_guard(
    *,
    topology_mode: str,
    requested_action: str,
    execution_owner_live: bool,
) -> Dict[str, Any]:
    topo = str(topology_mode or "").strip().lower() or "single_process"
    action = str(requested_action or "").strip().lower()
    if topo == "split" and execution_owner_live and action in {"start_split_backend", "start_scheduler_owner"}:
        return {
            "ok": False,
            "status": "rejected",
            "reason_code": "authority_conflict",
            "summary": "Split-topology tooling must not start a second live execution authority.",
        }
    return {
        "ok": True,
        "status": "accepted",
        "reason_code": "",
        "summary": "Runtime-control request is compatible with the declared topology contract.",
    }


def split_topology_snapshot() -> Dict[str, Any]:
    return {
        "schema_version": SPLIT_TOPOLOGY_SCHEMA_VERSION,
        "generated_utc": _utc_now_iso(),
        "uid": ABCD_UID,
        "children": [Q3U5_UID, R4V6_UID, S5W7_UID],
        "proof_suite_uid": T6X8_UID,
        "depends_on": [
            "PIS-20260313-100600Z-L8Q1",
            "PIS-20260313-100601Z-M9R2",
            "PIS-20260313-100602Z-N1S3",
            "PIS-20260313-100603Z-P2T4",
        ],
        "replacement_backend": {
            "uid": Q3U5_UID,
            "truth_basis": [
                "scheduler receipts/state",
                "runtime detached job records",
                "canonical writeback markers",
            ],
            "states": [
                "blocked_missing_durable_truth",
                "interrupted_reconcile_duplicate_risk",
                "cold_start_reconstruct_from_durable_truth",
                "replacement_ready_after_reconstruction",
            ],
        },
        "backend_rejoin": {
            "uid": R4V6_UID,
            "states": [
                "blocked_missing_durable_truth",
                "non_authoritative_rejoin",
                "authoritative_after_rejoin_reproof",
            ],
            "rules": [
                "returning backend local memory is advisory only",
                "durable shared truth decides what remains active or interrupted",
                "rejoin is non-authoritative until truth is re-proven",
            ],
        },
        "runtime_control": {
            "uid": S5W7_UID,
            "topology_modes": ["single_process", "split"],
            "guardrails": [
                "tooling must not create two live scheduler owners",
                "runtime control must reflect, not invent, authority state",
                "package/module ownership must separate projection from execution responsibility",
            ],
            "package_migration_targets": [
                "infra/controlplane/",
                "infra/webui/",
            ],
        },
    }


def validate_split_topology_snapshot(snapshot: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = dict(snapshot or split_topology_snapshot())
    errors: List[str] = []
    children = list(payload.get("children") or [])
    if children != [Q3U5_UID, R4V6_UID, S5W7_UID]:
        errors.append("children must match the split-topology execution trio in order")
    if str(payload.get("proof_suite_uid") or "") != T6X8_UID:
        errors.append("proof_suite_uid must point at T6X8")
    replacement_states = list(((payload.get("replacement_backend") or {}).get("states")) or [])
    if "cold_start_reconstruct_from_durable_truth" not in replacement_states:
        errors.append("replacement backend contract must expose the durable reconstruction state")
    rejoin_states = list(((payload.get("backend_rejoin") or {}).get("states")) or [])
    if "non_authoritative_rejoin" not in rejoin_states:
        errors.append("backend rejoin contract must expose the non-authoritative rejoin state")
    runtime_guardrails = list(((payload.get("runtime_control") or {}).get("guardrails")) or [])
    if not any("two live scheduler owners" in str(item) for item in runtime_guardrails):
        errors.append("runtime control contract must explicitly forbid two live scheduler owners")
    return {
        "schema_version": "lab_assistant_split_topology_validation_v1",
        "uid": ABCD_UID,
        "status": "pass" if not errors else "fail",
        "errors": errors,
    }
