"""Canonical job-shape lifecycle and recovery helpers backed by a durable contract."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Mapping, Tuple

from infra.controlplane import job_shape_contract


CONTRACT_ARTIFACT_REL = Path("governance/job_shape_lifecycle_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_job_shape_lifecycle_recovery_matrix_v1.md")
CANONICAL_SHAPE_CONTRACT_REL = Path("governance/canonical_job_shape_contract_v1.json")
SCHEMA_VERSION = "job_shape_lifecycle_contract_v1"
UID = "PIS-20260406-053811Z-339F"

TERMINAL_STATE_SUMMARY_CLASSES: Mapping[str, str] = {
    "succeeded": "evidence_bearing_completion",
    "failed": "evidence_bearing_completion",
    "failed_verification": "evidence_bearing_completion",
    "proof_gap": "evidence_bearing_completion",
    "skipped": "evidence_bearing_completion",
    "blocked": "operator_actionable_terminal",
    "deferred": "operator_actionable_terminal",
    "abandoned": "operator_actionable_terminal",
    "cancelled": "operator_actionable_terminal",
    "interrupted": "operator_actionable_terminal",
    "error": "operator_actionable_terminal",
}

RECOVERY_CLASS_DEFINITIONS: Mapping[str, str] = {
    "rerun_from_last_iteration_boundary": (
        "Restart from the last build-verify iteration boundary; retry and repair policy is explicit per loop receipt."
    ),
    "relaunch_children_from_parent_truth": (
        "Reconstruct child launch intent from the durable parent record and relaunch only from parent truth."
    ),
    "resume_from_durable_checkpoint_if_present": (
        "Resume from durable checkpoint truth when present; otherwise reconcile honestly and require an explicit new launch."
    ),
    "not_launchable_yet": (
        "The shape is named for taxonomy completeness but must not launch until its own runtime substrate exists."
    ),
}

CURRENT_REPO_STATE_MAPPINGS: Mapping[str, Tuple[Mapping[str, Any], ...]] = {
    "verification_loop_job": (
        {
            "surface": "scheduled_job_receipt.status",
            "canonical_state": "succeeded",
            "values": ("ok", "conditional_pass_repair_applied", "executed_with_closeout_integrity_defect"),
        },
        {"surface": "scheduled_job_receipt.status", "canonical_state": "failed_verification", "values": ("failed_verification",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "proof_gap", "values": ("proof_gap",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "blocked", "values": ("blocked",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "deferred", "values": ("deferred",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "cancelled", "values": ("cancelled",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "error", "values": ("error",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "interrupted", "values": ("interrupted",)},
    ),
    "fanout_job": (
        {"surface": "scheduled_job_receipt.status", "canonical_state": "succeeded", "values": ("ok",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "blocked", "values": ("blocked",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "deferred", "values": ("deferred",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "cancelled", "values": ("cancelled",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "error", "values": ("error",)},
    ),
    "campaign_job": (),
    "experiment_run_job": (
        {"surface": "scheduled_job_receipt.status", "canonical_state": "succeeded", "values": ("ok",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "blocked", "values": ("blocked",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "deferred", "values": ("deferred",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "cancelled", "values": ("cancelled",)},
        {"surface": "scheduled_job_receipt.status", "canonical_state": "error", "values": ("error", "timed_out")},
        {"surface": "run_record.lifecycle.state", "canonical_state": "succeeded", "values": ("succeeded",)},
        {"surface": "run_record.lifecycle.state", "canonical_state": "failed", "values": ("failed",)},
        {"surface": "run_record.lifecycle.state", "canonical_state": "skipped", "values": ("skipped",)},
        {"surface": "run_record.lifecycle.state", "canonical_state": "cancelled", "values": ("cancelled",)},
    ),
    "delegation_tree_job": (),
}

SHAPE_SPECS: Mapping[str, Mapping[str, Any]] = {
    "verification_loop_job": {
        "launch_policy": "allowed",
        "active_states": ("queued", "running_iteration", "awaiting_verifier", "awaiting_single_repair_pass"),
        "terminal_states": (
            "succeeded",
            "failed_verification",
            "proof_gap",
            "blocked",
            "deferred",
            "abandoned",
            "cancelled",
            "interrupted",
            "error",
        ),
        "evidence_bearing_terminal_states": ("succeeded", "failed_verification", "proof_gap"),
        "operator_actionable_terminal_states": ("blocked", "deferred", "abandoned", "cancelled", "interrupted", "error"),
        "recovery_class": "rerun_from_last_iteration_boundary",
        "interruption_state": "interrupted",
    },
    "fanout_job": {
        "launch_policy": "allowed",
        "active_states": ("queued", "launching_children", "waiting_on_children"),
        "terminal_states": ("succeeded", "blocked", "deferred", "abandoned", "cancelled", "interrupted", "error"),
        "evidence_bearing_terminal_states": ("succeeded",),
        "operator_actionable_terminal_states": ("blocked", "deferred", "abandoned", "cancelled", "interrupted", "error"),
        "recovery_class": "relaunch_children_from_parent_truth",
        "interruption_state": "interrupted",
    },
    "campaign_job": {
        "launch_policy": "forbidden_until_implemented",
        "active_states": (),
        "terminal_states": (),
        "evidence_bearing_terminal_states": (),
        "operator_actionable_terminal_states": (),
        "recovery_class": "not_launchable_yet",
        "interruption_state": "",
    },
    "experiment_run_job": {
        "launch_policy": "allowed",
        "active_states": ("launching", "running", "uncertain_interrupted"),
        "terminal_states": ("succeeded", "failed", "skipped", "blocked", "deferred", "cancelled", "interrupted", "error"),
        "evidence_bearing_terminal_states": ("succeeded", "failed", "skipped"),
        "operator_actionable_terminal_states": ("blocked", "deferred", "cancelled", "interrupted", "error"),
        "recovery_class": "resume_from_durable_checkpoint_if_present",
        "interruption_state": "uncertain_interrupted",
    },
    "delegation_tree_job": {
        "launch_policy": "forbidden_until_implemented",
        "active_states": (),
        "terminal_states": (),
        "evidence_bearing_terminal_states": (),
        "operator_actionable_terminal_states": (),
        "recovery_class": "not_launchable_yet",
        "interruption_state": "",
    },
}


@dataclass(frozen=True)
class JobShapeLifecycleContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    canonical_shape_contract_rel: Path
    shape_names: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def load_job_shape_lifecycle_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def job_shape_lifecycle_contract_v1() -> JobShapeLifecycleContractV1:
    return JobShapeLifecycleContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        canonical_shape_contract_rel=CANONICAL_SHAPE_CONTRACT_REL,
        shape_names=job_shape_contract.CANONICAL_JOB_SHAPES,
    )


def lifecycle_for_shape(name: str) -> Mapping[str, Any]:
    canonical_name = job_shape_contract.canonical_job_shape_name(name)
    return SHAPE_SPECS[canonical_name]


def recovery_class_for_shape(name: str) -> str:
    return str(lifecycle_for_shape(name)["recovery_class"])


def allowed_active_states_for_shape(name: str) -> Tuple[str, ...]:
    return tuple(lifecycle_for_shape(name)["active_states"])


def allowed_terminal_states_for_shape(name: str) -> Tuple[str, ...]:
    return tuple(lifecycle_for_shape(name)["terminal_states"])


def _active_state_for_shape(name: str, *, active_loop_stage: str = "") -> str:
    canonical_name = job_shape_contract.canonical_job_shape_name(name)
    stage = str(active_loop_stage or "").strip().lower()
    if canonical_name == "verification_loop_job":
        if stage in {"verifier", "verify"}:
            return "awaiting_verifier"
        if stage in {"repair", "single_repair", "single_repair_pass"}:
            return "awaiting_single_repair_pass"
        return "running_iteration"
    if canonical_name == "fanout_job":
        if stage in {"launching", "launching_children", "child_launch"}:
            return "launching_children"
        return "waiting_on_children"
    if canonical_name == "experiment_run_job":
        return "running"
    active_states = tuple(lifecycle_for_shape(canonical_name)["active_states"])
    if active_states:
        return str(active_states[0])
    raise KeyError(f"job shape {canonical_name} has no active lifecycle state")


def canonical_state_for_shape_record(
    name: str,
    raw_state: str,
    *,
    active_run: bool = False,
    active_loop_stage: str = "",
) -> str:
    canonical_name = job_shape_contract.canonical_job_shape_name(name)
    if active_run:
        return _active_state_for_shape(canonical_name, active_loop_stage=active_loop_stage)
    normalized_state = str(raw_state or "").strip()
    if not normalized_state:
        raise KeyError(f"illegal lifecycle state for {canonical_name}: {normalized_state}")
    lifecycle = lifecycle_for_shape(canonical_name)
    legal_states = set(lifecycle["active_states"]) | set(lifecycle["terminal_states"])
    if normalized_state in legal_states:
        return normalized_state
    for mapping in CURRENT_REPO_STATE_MAPPINGS[canonical_name]:
        if normalized_state in tuple(mapping.get("values") or ()):
            return str(mapping["canonical_state"])
    raise KeyError(f"illegal lifecycle state for {canonical_name}: {normalized_state}")


def lifecycle_projection_for_shape_record(
    name: str,
    raw_state: str,
    *,
    active_run: bool = False,
    active_loop_stage: str = "",
) -> Mapping[str, Any]:
    canonical_name = job_shape_contract.canonical_job_shape_name(name)
    normalized_state = str(raw_state or "").strip()
    recovery_class = recovery_class_for_shape(canonical_name)
    try:
        canonical_state = canonical_state_for_shape_record(
            canonical_name,
            normalized_state,
            active_run=active_run,
            active_loop_stage=active_loop_stage,
        )
    except KeyError:
        return {
            "job_shape": canonical_name,
            "raw_state": normalized_state,
            "canonical_state": "",
            "summary_bucket": "unknown",
            "terminal_summary_class": "",
            "recovery_class": recovery_class,
            "interruption_state": str(lifecycle_for_shape(canonical_name)["interruption_state"] or ""),
            "recognized": False,
            "active_run": bool(active_run),
        }

    summary_bucket = summary_bucket_for_shape_state(canonical_name, canonical_state)
    terminal_summary_class = TERMINAL_STATE_SUMMARY_CLASSES.get(canonical_state, "")
    return {
        "job_shape": canonical_name,
        "raw_state": normalized_state,
        "canonical_state": canonical_state,
        "summary_bucket": summary_bucket,
        "terminal_summary_class": terminal_summary_class,
        "recovery_class": recovery_class,
        "interruption_state": str(lifecycle_for_shape(canonical_name)["interruption_state"] or ""),
        "recognized": True,
        "active_run": bool(active_run),
    }


def summary_bucket_for_shape_state(name: str, state: str) -> str:
    lifecycle = lifecycle_for_shape(name)
    normalized_state = str(state or "").strip()
    if normalized_state in lifecycle["active_states"]:
        return "active_progress"
    if normalized_state in lifecycle["evidence_bearing_terminal_states"]:
        return "terminal_evidence_bearing"
    if normalized_state in lifecycle["operator_actionable_terminal_states"]:
        return "terminal_operator_actionable"
    raise KeyError(f"illegal lifecycle state for {name}: {normalized_state}")


def assert_valid_shape_state_combination(name: str, state: str, *, recovery_class: str | None = None) -> None:
    lifecycle = lifecycle_for_shape(name)
    normalized_state = str(state or "").strip()
    legal_states = set(lifecycle["active_states"]) | set(lifecycle["terminal_states"])
    if normalized_state not in legal_states:
        raise KeyError(f"illegal lifecycle state for {name}: {normalized_state}")
    if recovery_class is None:
        return
    normalized_recovery = str(recovery_class or "").strip()
    if normalized_recovery != lifecycle["recovery_class"]:
        raise ValueError(
            f"illegal recovery class for {name}: {normalized_recovery} != {lifecycle['recovery_class']}"
        )


def validate_job_shape_lifecycle_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_job_shape_lifecycle_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain job_shape_lifecycle_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260406-053811Z-339F")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the companion lifecycle docs contract")
    if str(payload.get("canonical_shape_contract_rel") or "") != CANONICAL_SHAPE_CONTRACT_REL.as_posix():
        issues.append("canonical_shape_contract_rel must point at the canonical job-shape vocabulary contract")

    shapes = payload.get("shapes")
    if not isinstance(shapes, dict):
        return ("shapes must be a mapping keyed by canonical job-shape name",)
    if tuple(shapes.keys()) != job_shape_contract.CANONICAL_JOB_SHAPES:
        issues.append("shape definitions must exist for every canonical job shape in order")

    definitions = payload.get("terminal_state_definitions")
    if not isinstance(definitions, dict):
        issues.append("terminal_state_definitions must be a mapping")
    elif tuple(definitions.keys()) != tuple(TERMINAL_STATE_SUMMARY_CLASSES.keys()):
        issues.append("terminal_state_definitions must list the canonical terminal states in order")

    for terminal_state, expected_bucket in TERMINAL_STATE_SUMMARY_CLASSES.items():
        entry = definitions.get(terminal_state) if isinstance(definitions, dict) else None
        if not isinstance(entry, dict):
            continue
        if str(entry.get("summary_class") or "") != expected_bucket:
            issues.append(f"{terminal_state} summary_class drifted from the runtime helper contract")

    recovery_definitions = payload.get("recovery_class_definitions")
    if not isinstance(recovery_definitions, dict):
        issues.append("recovery_class_definitions must be a mapping")
    elif tuple(recovery_definitions.keys()) != tuple(RECOVERY_CLASS_DEFINITIONS.keys()):
        issues.append("recovery_class_definitions must list the canonical recovery classes in order")

    for recovery_class, expected_description in RECOVERY_CLASS_DEFINITIONS.items():
        actual_description = ""
        if isinstance(recovery_definitions, dict):
            actual_description = str(recovery_definitions.get(recovery_class) or "")
        if actual_description != expected_description:
            issues.append(f"{recovery_class} definition drifted from the runtime helper contract")

    for shape_name in job_shape_contract.CANONICAL_JOB_SHAPES:
        entry = shapes.get(shape_name)
        if not isinstance(entry, dict):
            issues.append(f"{shape_name} entry must be an object")
            continue
        expected = SHAPE_SPECS[shape_name]
        for key in (
            "launch_policy",
            "active_states",
            "terminal_states",
            "evidence_bearing_terminal_states",
            "operator_actionable_terminal_states",
            "recovery_class",
            "interruption_state",
        ):
            actual_value = entry.get(key)
            expected_value = expected[key]
            if isinstance(expected_value, tuple):
                actual_value = tuple(actual_value or [])
            if actual_value != expected_value:
                issues.append(f"{shape_name} {key} drifted from the runtime helper contract")

        evidence_states = set(entry.get("evidence_bearing_terminal_states") or [])
        actionable_states = set(entry.get("operator_actionable_terminal_states") or [])
        terminal_states = set(entry.get("terminal_states") or [])
        active_states = set(entry.get("active_states") or [])
        legal_states = active_states | terminal_states
        launch_policy = str(entry.get("launch_policy") or "")
        recovery_class = str(entry.get("recovery_class") or "")
        interruption_state = str(entry.get("interruption_state") or "")

        if evidence_states & actionable_states:
            issues.append(f"{shape_name} terminal states must not be both evidence-bearing and operator-actionable")
        if not evidence_states <= terminal_states:
            issues.append(f"{shape_name} evidence-bearing states must be a subset of terminal_states")
        if not actionable_states <= terminal_states:
            issues.append(f"{shape_name} operator-actionable states must be a subset of terminal_states")
        if launch_policy not in {"allowed", "forbidden_until_implemented"}:
            issues.append(f"{shape_name} launch_policy must be an allowed canonical value")
        if recovery_class not in RECOVERY_CLASS_DEFINITIONS:
            issues.append(f"{shape_name} recovery_class must be defined in recovery_class_definitions")
        if interruption_state and interruption_state not in legal_states:
            issues.append(f"{shape_name} interruption_state must be one of the shape's legal lifecycle states")
        if shape_name in job_shape_contract.CURRENT_JOB_SHAPES and launch_policy != "allowed":
            issues.append(f"{shape_name} must remain launchable for current repo behavior")
        if shape_name in job_shape_contract.FUTURE_FACING_JOB_SHAPES and launch_policy != "forbidden_until_implemented":
            issues.append(f"{shape_name} must remain forbidden_until_implemented until its runtime substrate exists")
        actual_mappings = tuple(
            {
                "surface": str(row.get("surface") or ""),
                "canonical_state": str(row.get("canonical_state") or ""),
                "values": tuple(row.get("values") or ()),
            }
            for row in list(entry.get("current_repo_state_mappings") or [])
            if isinstance(row, dict)
        )
        expected_mappings = tuple(
            {
                "surface": str(row.get("surface") or ""),
                "canonical_state": str(row.get("canonical_state") or ""),
                "values": tuple(row.get("values") or ()),
            }
            for row in CURRENT_REPO_STATE_MAPPINGS[shape_name]
        )
        if actual_mappings != expected_mappings:
            issues.append(f"{shape_name} current_repo_state_mappings drifted from the runtime helper contract")
        raw_value_targets: Dict[Tuple[str, str], str] = {}
        for row in list(entry.get("current_repo_state_mappings") or []):
            if not isinstance(row, dict):
                continue
            surface = str(row.get("surface") or "")
            canonical_state = str(row.get("canonical_state") or "")
            if canonical_state not in terminal_states:
                issues.append(f"{shape_name} mapped canonical_state {canonical_state} must be a legal terminal state")
            for raw_value in tuple(row.get("values") or ()):
                key = (surface, str(raw_value))
                prior = raw_value_targets.get(key)
                if prior is not None and prior != canonical_state:
                    issues.append(
                        f"{shape_name} raw state {raw_value} on {surface} cannot map to both {prior} and {canonical_state}"
                    )
                else:
                    raw_value_targets[key] = canonical_state

    return tuple(issues)
