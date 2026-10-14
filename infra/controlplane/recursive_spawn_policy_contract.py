"""Recursive planning spawn-policy helpers backed by a durable contract."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import job_budget_cost_ledger_contract
from infra.controlplane import job_shape_contract
from infra.controlplane import job_shape_relationship_contract


SCHEMA_VERSION = "recursive_spawn_policy_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/recursive_spawn_policy_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_recursive_spawn_policy_v1.md")
JOB_SHAPE_RELATIONSHIP_CONTRACT_REL = Path("governance/job_shape_relationship_contract_v1.json")
BUDGET_LEDGER_CONTRACT_REL = Path("governance/job_budget_cost_ledger_contract_v1.json")
UID = "PIS-20260409-154915Z-5F24"

PLANNING_MODE_VOCAB: Tuple[str, ...] = ("dry_run_only",)
PLANNING_PARENT_SHAPES: Tuple[str, ...] = ("fanout_job", "campaign_job", "delegation_tree_job")
ALLOWED_CAMPAIGN_PHASE_CATEGORIES: Tuple[str, ...] = ("planning", "repair", "review")
PROPOSED_CHILD_DEPTH_CAPS: Dict[str, int] = {
    "verification_loop_job": 2,
    "fanout_job": 2,
    "campaign_job": 2,
    "experiment_run_job": 1,
    "delegation_tree_job": 3,
}
BUDGET_BALANCE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "job_uid",
    "unit",
    "allocated_minor_units",
    "cost_debit_minor_units",
    "cost_credit_minor_units",
    "remaining_minor_units",
)
STOP_CONDITION_CODES: Tuple[str, ...] = (
    "parent_shape_not_planning_eligible",
    "planning_mode_forbidden",
    "campaign_phase_not_planning_eligible",
    "relationship_forbidden",
    "ancestry_cycle_detected",
    "ancestor_shape_path_mismatch",
    "depth_cap_exceeded",
    "budget_stop_threshold_hit",
    "reserved_budget_exceeds_remaining",
    "budget_exhausted",
    "child_budget_exceeds_spendable",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "planning_requests_require_explicit_dry_run_mode",
    "spawn_legality_reuses_job_shape_relationship_matrix",
    "forbidden_parent_shapes_rejected_before_graph_expansion",
    "ancestry_cycle_or_depth_cap_hits_stop_recursion",
    "budget_propagation_reuses_canonical_remaining_budget_math",
    "reserved_budget_and_minimum_parent_remaining_are_hard_stop_thresholds",
    "forbidden_or_exhausted_plans_emit_explicit_non_launch_reasons",
)


@dataclass(frozen=True)
class RecursiveSpawnPolicyContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    planning_mode_vocab: Tuple[str, ...]
    planning_parent_shapes: Tuple[str, ...]
    allowed_campaign_phase_categories: Tuple[str, ...]
    proposed_child_depth_caps: Dict[str, int]
    stop_condition_codes: Tuple[str, ...]
    fail_closed_rules: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text:
            out.append(text)
    return out


def _normalize_nonnegative_int(value: Any) -> int:
    if isinstance(value, bool):
        return -1
    try:
        normalized = int(value)
    except (TypeError, ValueError):
        return -1
    return normalized if normalized >= 0 else -1


def _normalize_budget_balance(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "job_uid": _normalize_text(row.get("job_uid")),
        "unit": _normalize_text(row.get("unit")),
        "allocated_minor_units": _normalize_nonnegative_int(row.get("allocated_minor_units")),
        "cost_debit_minor_units": _normalize_nonnegative_int(row.get("cost_debit_minor_units")),
        "cost_credit_minor_units": _normalize_nonnegative_int(row.get("cost_credit_minor_units")),
        "remaining_minor_units": _normalize_nonnegative_int(row.get("remaining_minor_units")),
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def recursive_spawn_policy_contract_v1() -> RecursiveSpawnPolicyContractV1:
    return RecursiveSpawnPolicyContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        planning_mode_vocab=PLANNING_MODE_VOCAB,
        planning_parent_shapes=PLANNING_PARENT_SHAPES,
        allowed_campaign_phase_categories=ALLOWED_CAMPAIGN_PHASE_CATEGORIES,
        proposed_child_depth_caps=dict(PROPOSED_CHILD_DEPTH_CAPS),
        stop_condition_codes=STOP_CONDITION_CODES,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_recursive_spawn_policy_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_recursive_spawn_request(raw: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(raw or {})
    return {
        "parent_job_shape": _normalize_text(row.get("parent_job_shape")),
        "child_job_shape": _normalize_text(row.get("child_job_shape")),
        "planning_mode": _normalize_text(row.get("planning_mode")).lower(),
        "campaign_phase_category": _normalize_text(row.get("campaign_phase_category")).lower(),
        "ancestry_path": _normalize_text_list(row.get("ancestry_path")),
        "ancestor_job_shapes": _normalize_text_list(row.get("ancestor_job_shapes")),
        "parent_budget_balance": _normalize_budget_balance(row.get("parent_budget_balance")),
        "reserved_budget_minor_units": _normalize_nonnegative_int(row.get("reserved_budget_minor_units")),
        "child_budget_request_minor_units": _normalize_nonnegative_int(row.get("child_budget_request_minor_units")),
        "minimum_parent_remaining_minor_units": _normalize_nonnegative_int(row.get("minimum_parent_remaining_minor_units")),
    }


def _append_error(errors: List[Dict[str, str]], *, field: str, code: str, message: str) -> None:
    errors.append({"field": field, "code": code, "message": message})


def _validate_budget_balance(
    balance: Mapping[str, Any],
    *,
    errors: List[Dict[str, str]],
) -> Dict[str, Any]:
    normalized = _normalize_budget_balance(balance)
    for field_name in BUDGET_BALANCE_REQUIRED_FIELDS:
        value = normalized.get(field_name)
        if value in ("", None) or (isinstance(value, int) and value < 0):
            _append_error(
                errors,
                field=f"parent_budget_balance.{field_name}",
                code="missing_field" if value in ("", None) else "invalid_value",
                message=f"parent_budget_balance requires canonical field {field_name}",
            )
    return normalized


def evaluate_recursive_spawn_request(
    raw_request: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    normalized = normalize_recursive_spawn_request(raw_request)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    parent_shape = normalized["parent_job_shape"]
    child_shape = normalized["child_job_shape"]
    try:
        parent_shape = job_shape_contract.canonical_job_shape_name(parent_shape)
        normalized["parent_job_shape"] = parent_shape
    except KeyError:
        _append_error(errors, field="parent_job_shape", code="invalid_value", message="parent_job_shape must be canonical")
    try:
        child_shape = job_shape_contract.canonical_job_shape_name(child_shape)
        normalized["child_job_shape"] = child_shape
    except KeyError:
        _append_error(errors, field="child_job_shape", code="invalid_value", message="child_job_shape must be canonical")

    if normalized["planning_mode"] not in PLANNING_MODE_VOCAB:
        _append_error(
            errors,
            field="planning_mode",
            code="planning_mode_forbidden",
            message="recursive planning remains dry_run_only in v1",
        )

    if parent_shape and parent_shape not in PLANNING_PARENT_SHAPES:
        _append_error(
            errors,
            field="parent_job_shape",
            code="parent_shape_not_planning_eligible",
            message="parent job shape is not allowed to propose recursive descendants in planning mode",
        )

    ancestry_path = normalized["ancestry_path"]
    ancestor_job_shapes = normalized["ancestor_job_shapes"]
    proposed_child_depth = len(ancestry_path)
    if len(ancestry_path) != len(set(ancestry_path)):
        _append_error(
            errors,
            field="ancestry_path",
            code="ancestry_cycle_detected",
            message="ancestry_path must not repeat node ids while planning recursive descendants",
        )
    if ancestor_job_shapes and len(ancestor_job_shapes) != len(ancestry_path):
        _append_error(
            errors,
            field="ancestor_job_shapes",
            code="ancestor_shape_path_mismatch",
            message="ancestor_job_shapes must align one-for-one with ancestry_path",
        )
    for index, shape_name in enumerate(ancestor_job_shapes):
        try:
            ancestor_job_shapes[index] = job_shape_contract.canonical_job_shape_name(shape_name)
        except KeyError:
            _append_error(
                errors,
                field=f"ancestor_job_shapes[{index}]",
                code="invalid_value",
                message="ancestor_job_shapes entries must be canonical",
            )
    if ancestor_job_shapes and parent_shape and ancestor_job_shapes[-1] != parent_shape:
        _append_error(
            errors,
            field="ancestor_job_shapes",
            code="ancestor_shape_path_mismatch",
            message="the final ancestor_job_shapes entry must match parent_job_shape",
        )

    if parent_shape == "campaign_job":
        if normalized["campaign_phase_category"] not in ALLOWED_CAMPAIGN_PHASE_CATEGORIES:
            _append_error(
                errors,
                field="campaign_phase_category",
                code="campaign_phase_not_planning_eligible",
                message="campaign parents may only propose recursive descendants from planning, repair, or review phases",
            )
    elif normalized["campaign_phase_category"]:
        warnings.append(
            {
                "field": "campaign_phase_category",
                "code": "ignored_field",
                "message": "campaign_phase_category is ignored unless parent_job_shape is campaign_job",
            }
        )

    relationship: Dict[str, str] = {}
    if parent_shape and child_shape:
        relationship = dict(
            job_shape_relationship_contract.relationship_for_shapes(parent_shape, child_shape, repo_root=repo_root)
        )
        if relationship["legality"] == "forbidden":
            _append_error(
                errors,
                field="child_job_shape",
                code="relationship_forbidden",
                message="parent-child job-shape relationship is forbidden even in recursive planning mode",
            )

    if child_shape:
        depth_cap = PROPOSED_CHILD_DEPTH_CAPS[child_shape]
        if proposed_child_depth > depth_cap:
            _append_error(
                errors,
                field="ancestry_path",
                code="depth_cap_exceeded",
                message=f"proposed child depth {proposed_child_depth} exceeds the {child_shape} cap of {depth_cap}",
            )
    else:
        depth_cap = -1

    parent_budget_balance = _validate_budget_balance(normalized["parent_budget_balance"], errors=errors)
    reserved = normalized["reserved_budget_minor_units"]
    child_request = normalized["child_budget_request_minor_units"]
    min_parent_remaining = normalized["minimum_parent_remaining_minor_units"]

    if reserved < 0:
        _append_error(
            errors,
            field="reserved_budget_minor_units",
            code="invalid_value",
            message="reserved_budget_minor_units must be a non-negative integer",
        )
    if child_request <= 0:
        _append_error(
            errors,
            field="child_budget_request_minor_units",
            code="invalid_value",
            message="child_budget_request_minor_units must be a positive integer",
        )
    if min_parent_remaining < 0:
        _append_error(
            errors,
            field="minimum_parent_remaining_minor_units",
            code="invalid_value",
            message="minimum_parent_remaining_minor_units must be a non-negative integer",
        )

    remaining = parent_budget_balance["remaining_minor_units"]
    spendable_after_reserve = remaining - reserved
    spendable_for_child = spendable_after_reserve - min_parent_remaining
    if remaining >= 0 and remaining <= min_parent_remaining:
        _append_error(
            errors,
            field="minimum_parent_remaining_minor_units",
            code="budget_stop_threshold_hit",
            message="parent remaining budget is already at or below the configured stop threshold",
        )
    if remaining >= 0 and reserved > remaining:
        _append_error(
            errors,
            field="reserved_budget_minor_units",
            code="reserved_budget_exceeds_remaining",
            message="reserved budget cannot exceed the canonical remaining budget",
        )
    if remaining >= 0 and spendable_after_reserve <= 0:
        _append_error(
            errors,
            field="reserved_budget_minor_units",
            code="budget_exhausted",
            message="no spendable budget remains after honoring reserved budget",
        )
    if remaining >= 0 and child_request > 0 and spendable_for_child < child_request:
        _append_error(
            errors,
            field="child_budget_request_minor_units",
            code="child_budget_exceeds_spendable",
            message="child allocation request exceeds the spendable parent budget after reserve and stop threshold",
        )

    budget_decision = {
        "ceiling_minor_units": parent_budget_balance["allocated_minor_units"],
        "remaining_minor_units": remaining,
        "reserved_budget_minor_units": reserved,
        "minimum_parent_remaining_minor_units": min_parent_remaining,
        "spendable_after_reserve_minor_units": spendable_after_reserve,
        "spendable_for_child_minor_units": spendable_for_child,
        "propagated_child_allocation_minor_units": child_request if not errors else 0,
        "parent_remaining_after_child_minor_units": remaining - child_request,
        "unit": parent_budget_balance["unit"],
        "budget_source_contract_uid": job_budget_cost_ledger_contract.UID,
    }

    non_launch_reason_codes: List[str] = []
    for row in errors:
        code = row["code"]
        if code not in non_launch_reason_codes:
            non_launch_reason_codes.append(code)

    return {
        "ok": not errors,
        "failure_class": "" if not errors else "recursive_spawn_policy_rejected",
        "errors": errors,
        "warnings": warnings,
        "normalized_request": normalized,
        "relationship": relationship,
        "budget_decision": budget_decision,
        "proposed_child_depth": proposed_child_depth,
        "proposed_child_depth_cap": depth_cap,
        "non_launch_reason_codes": non_launch_reason_codes,
        "computed_fingerprint": _fingerprint(normalized),
    }


def validate_recursive_spawn_policy_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_recursive_spawn_policy_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append(f"schema_version must remain {SCHEMA_VERSION}")
    if str(payload.get("uid") or "") != UID:
        issues.append(f"uid must remain bound to {UID}")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the recursive spawn policy contract doc")
    if str(payload.get("job_shape_relationship_contract_rel") or "") != JOB_SHAPE_RELATIONSHIP_CONTRACT_REL.as_posix():
        issues.append("job_shape_relationship_contract_rel must point at the canonical job-shape relationship contract")
    if str(payload.get("budget_ledger_contract_rel") or "") != BUDGET_LEDGER_CONTRACT_REL.as_posix():
        issues.append("budget_ledger_contract_rel must point at the canonical budget ledger contract")
    if tuple(payload.get("planning_mode_vocab") or ()) != PLANNING_MODE_VOCAB:
        issues.append("planning_mode_vocab must remain stable and ordered")
    if tuple(payload.get("planning_parent_shapes") or ()) != PLANNING_PARENT_SHAPES:
        issues.append("planning_parent_shapes must remain stable and ordered")
    if tuple(payload.get("allowed_campaign_phase_categories") or ()) != ALLOWED_CAMPAIGN_PHASE_CATEGORIES:
        issues.append("allowed_campaign_phase_categories must remain stable and ordered")
    if tuple(payload.get("budget_balance_required_fields") or ()) != BUDGET_BALANCE_REQUIRED_FIELDS:
        issues.append("budget_balance_required_fields must stay aligned with the budget replay substrate")
    if tuple(payload.get("stop_condition_codes") or ()) != STOP_CONDITION_CODES:
        issues.append("stop_condition_codes must remain stable and ordered")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must remain stable and ordered")

    caps = payload.get("proposed_child_depth_caps")
    if not isinstance(caps, dict):
        issues.append("proposed_child_depth_caps must map every canonical child shape to an integer cap")
    elif tuple(caps.keys()) != job_shape_contract.CANONICAL_JOB_SHAPES:
        issues.append("proposed_child_depth_caps must list every canonical job shape in canonical order")
    else:
        for shape_name, expected_cap in PROPOSED_CHILD_DEPTH_CAPS.items():
            if caps.get(shape_name) != expected_cap:
                issues.append(f"proposed_child_depth_caps.{shape_name} drifted from the runtime helper contract")

    return tuple(issues)
