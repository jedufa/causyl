"""Recursive dry-run graph expansion and structural validation helpers."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence, Set, Tuple

from infra.controlplane import budget_adaptive_research_policy
from infra.controlplane import recursive_plan_record_contract
from infra.controlplane import recursive_planning_receipts
from infra.controlplane import recursive_spawn_policy_contract


REQUEST_SCHEMA_VERSION = "recursive_dry_run_plan_request_v1"
REPORT_SCHEMA_VERSION = "recursive_dry_run_validation_report_v1"
LIVE_ADMISSION_POLICY = "dry_run_only"


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


def _normalize_truth_refs(value: Any) -> Dict[str, List[str]]:
    row = value if isinstance(value, Mapping) else {}
    return {
        field: _normalize_text_list(row.get(field))
        for field in recursive_plan_record_contract.TRUTH_REF_FIELDS
    }


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


def _normalize_node_spec(value: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(value or {})
    descendants = [
        _normalize_node_spec(raw_descendant)
        for raw_descendant in list(row.get("descendants") or [])
        if isinstance(raw_descendant, Mapping)
    ]
    return {
        "node_id": _normalize_text(row.get("node_id")),
        "job_shape": _normalize_text(row.get("job_shape")),
        "truth_refs": _normalize_truth_refs(row.get("truth_refs")),
        "node_event_refs": _normalize_text_list(row.get("node_event_refs")),
        "dependency_node_ids": _normalize_text_list(row.get("dependency_node_ids")),
        "descendants": descendants,
        "budget_balance": _normalize_budget_balance(row.get("budget_balance")),
        "campaign_phase_category": _normalize_text(row.get("campaign_phase_category")).lower(),
        "requested_budget_minor_units": _normalize_nonnegative_int(row.get("requested_budget_minor_units")),
        "parent_reserved_budget_minor_units": _normalize_nonnegative_int(row.get("parent_reserved_budget_minor_units")),
        "minimum_parent_remaining_minor_units": _normalize_nonnegative_int(row.get("minimum_parent_remaining_minor_units")),
    }


def normalize_recursive_dry_run_request(raw_request: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_request or {})
    root_node = raw.get("root_node") if isinstance(raw.get("root_node"), Mapping) else {}
    root_binding = raw.get("root_binding") if isinstance(raw.get("root_binding"), Mapping) else {}
    return {
        "schema_version": _normalize_text(raw.get("schema_version") or REQUEST_SCHEMA_VERSION),
        "plan_id": _normalize_text(raw.get("plan_id")),
        "root_plan_id": _normalize_text(raw.get("root_plan_id") or raw.get("plan_id")),
        "parent_plan_id": _normalize_text(raw.get("parent_plan_id")),
        "parent_job_id": _normalize_text(raw.get("parent_job_id")),
        "planning_mode": _normalize_text(raw.get("planning_mode") or LIVE_ADMISSION_POLICY).lower(),
        "root_binding": {
            "binding_type": _normalize_text(root_binding.get("binding_type")).lower(),
            "binding_id": _normalize_text(root_binding.get("binding_id")),
        },
        "plan_truth_refs": _normalize_truth_refs(raw.get("plan_truth_refs")),
        "plan_event_refs": _normalize_text_list(raw.get("plan_event_refs")),
        "budget_adaptive_guidance": budget_adaptive_research_policy.normalize_budget_adaptive_planning_guidance(
            raw.get("budget_adaptive_guidance") if isinstance(raw.get("budget_adaptive_guidance"), Mapping) else {}
        ),
        "root_node": _normalize_node_spec(root_node),
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _has_truth_refs(value: Mapping[str, Sequence[str]]) -> bool:
    return any(bool(list(value.get(field) or ())) for field in recursive_plan_record_contract.TRUTH_REF_FIELDS)


def _append_error(
    errors: List[Dict[str, Any]],
    *,
    field: str,
    code: str,
    message: str,
    node_id: str = "",
    source: str = "planner",
) -> None:
    errors.append(
        {
            "field": field,
            "code": code,
            "message": message,
            "node_id": node_id,
            "source": source,
        }
    )


def _edge_id(from_node_id: str, to_node_id: str) -> str:
    return f"edge_{from_node_id}_depends_on_{to_node_id}".replace("-", "_")


def _balance_for_child(*, plan_id: str, node_id: str, unit: str, allocated_minor_units: int) -> Dict[str, Any]:
    return {
        "job_uid": f"{plan_id}:{node_id}",
        "unit": unit,
        "allocated_minor_units": allocated_minor_units,
        "cost_debit_minor_units": 0,
        "cost_credit_minor_units": 0,
        "remaining_minor_units": allocated_minor_units,
    }


def _dependency_cycle_nodes(node_ids: Sequence[str], edges: Sequence[Mapping[str, str]]) -> Set[str]:
    outgoing: Dict[str, List[str]] = {node_id: [] for node_id in node_ids}
    for edge in edges:
        source = str(edge.get("from_node_id") or "")
        target = str(edge.get("to_node_id") or "")
        if source in outgoing and target:
            outgoing[source].append(target)

    visiting: Set[str] = set()
    visited: Set[str] = set()
    in_cycle: Set[str] = set()

    def _walk(node_id: str) -> None:
        if node_id in visited:
            return
        visiting.add(node_id)
        for target in outgoing.get(node_id, []):
            if target in visiting:
                in_cycle.update({node_id, target})
                continue
            if target not in visited:
                _walk(target)
                if target in in_cycle:
                    in_cycle.add(node_id)
        visiting.discard(node_id)
        visited.add(node_id)

    for node_id in node_ids:
        if node_id not in visited:
            _walk(node_id)
    return in_cycle


def build_recursive_dry_run_plan(
    raw_request: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    receipt_repo_root = Path(repo_root).resolve() if repo_root is not None else None
    contract_repo_root = receipt_repo_root
    if contract_repo_root is not None and not (contract_repo_root / recursive_spawn_policy_contract.CONTRACT_ARTIFACT_REL).exists():
        contract_repo_root = None
    normalized = normalize_recursive_dry_run_request(raw_request)
    errors: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    nodes_by_id: Dict[str, Dict[str, Any]] = {}
    owner_by_node_id: Dict[str, str] = {}
    dependency_edge_map: Dict[Tuple[str, str], Dict[str, str]] = {}
    dependency_refs: List[Tuple[str, str]] = []
    budget_allocations: List[Dict[str, Any]] = []
    node_lineage: List[Dict[str, Any]] = []
    invalid_node_ids: Set[str] = set()
    root_node = normalized["root_node"]
    budget_adaptive_result = budget_adaptive_research_policy.evaluate_budget_adaptive_planning_guidance(
        normalized.get("budget_adaptive_guidance") or {},
        repo_root=receipt_repo_root,
    )

    for field in ("plan_id", "root_plan_id", "parent_job_id"):
        if not normalized[field]:
            _append_error(errors, field=field, code="missing_field", message=f"recursive dry-run request requires {field}")
    if normalized["schema_version"] != REQUEST_SCHEMA_VERSION:
        _append_error(
            errors,
            field="schema_version",
            code="invalid_value",
            message=f"recursive dry-run request schema_version must remain {REQUEST_SCHEMA_VERSION}",
        )
    if normalized["planning_mode"] != LIVE_ADMISSION_POLICY:
        _append_error(
            errors,
            field="planning_mode",
            code="planning_mode_forbidden",
            message="recursive dry-run planning remains dry_run_only in v1",
        )
    if normalized["root_binding"]["binding_type"] not in recursive_plan_record_contract.ROOT_BINDING_TYPES:
        _append_error(
            errors,
            field="root_binding.binding_type",
            code="invalid_value",
            message="root_binding.binding_type must use canonical ledger, campaign, or job truth",
        )
    if not normalized["root_binding"]["binding_id"]:
        _append_error(errors, field="root_binding.binding_id", code="missing_field", message="root binding requires an id")
    if not _has_truth_refs(normalized["plan_truth_refs"]):
        _append_error(
            errors,
            field="plan_truth_refs",
            code="invalid_value",
            message="dry-run plan request must point back to canonical lower-layer truth",
        )
    if not normalized["plan_event_refs"]:
        _append_error(
            errors,
            field="plan_event_refs",
            code="invalid_value",
            message="dry-run plan request requires append-only plan_event_refs",
        )

    if not root_node.get("node_id"):
        _append_error(errors, field="root_node.node_id", code="missing_field", message="root_node requires node_id")
    if not root_node.get("job_shape"):
        _append_error(errors, field="root_node.job_shape", code="missing_field", message="root_node requires job_shape")
    if not _has_truth_refs(root_node.get("truth_refs", {})):
        _append_error(
            errors,
            field="root_node.truth_refs",
            code="invalid_value",
            message="root_node requires canonical truth refs",
        )
    if not root_node.get("node_event_refs"):
        _append_error(
            errors,
            field="root_node.node_event_refs",
            code="invalid_value",
            message="root_node requires append-only node_event_refs",
        )
    for row in list(budget_adaptive_result.get("warnings") or []):
        warnings.append(
            {
                "field": str(row.get("field") or "budget_adaptive_guidance"),
                "code": str(row.get("code") or "budget_adaptive_warning"),
                "message": str(row.get("message") or "budget-adaptive planning warning"),
                "source": "budget_adaptive_policy",
            }
        )
    policy_receipt = dict(budget_adaptive_result.get("receipt") or {})
    if not budget_adaptive_result.get("ok"):
        if bool(policy_receipt.get("advisory_only")):
            warnings.append(
                {
                    "field": "budget_adaptive_guidance",
                    "code": "budget_adaptive_advisory_receipt",
                    "message": "budget-adaptive planning guidance recorded an explicit advisory downgrade",
                    "source": "budget_adaptive_policy",
                }
            )
        else:
            for row in list(budget_adaptive_result.get("errors") or []):
                _append_error(
                    errors,
                    field=str(row.get("field") or "budget_adaptive_guidance"),
                    code=str(row.get("code") or "budget_adaptive_policy_rejected"),
                    message=str(row.get("message") or "budget-adaptive planning guidance rejected"),
                    source="budget_adaptive_policy",
                )

    def _visit_node(
        spec: Mapping[str, Any],
        *,
        parent_node_id: str,
        ancestry_path: Sequence[str],
        parent_job_shape: str,
        parent_campaign_phase_category: str,
        parent_budget_balance: Mapping[str, Any] | None,
    ) -> None:
        node_id = str(spec.get("node_id") or "")
        if not node_id:
            _append_error(errors, field="node_id", code="missing_field", message="planned node requires node_id", source="expansion")
            return
        if node_id in ancestry_path:
            _append_error(
                errors,
                field=f"node:{node_id}.ancestry_path",
                code="ancestry_cycle_detected",
                message="recursive planning node ids must not repeat in the ancestry path",
                node_id=node_id,
                source="expansion",
            )
            invalid_node_ids.add(node_id)
            return

        existing_owner = owner_by_node_id.get(node_id)
        if existing_owner and existing_owner != parent_node_id:
            _append_error(
                errors,
                field=f"node:{node_id}.parent_node_id",
                code="ambiguous_parent_rejected",
                message=f"node {node_id} is claimed by multiple parents: {existing_owner} and {parent_node_id}",
                node_id=node_id,
                source="expansion",
            )
            invalid_node_ids.add(node_id)
            return
        if existing_owner == parent_node_id:
            _append_error(
                errors,
                field=f"node:{node_id}.node_id",
                code="duplicate_descendant_rejected",
                message=f"parent {parent_node_id} declares descendant {node_id} more than once",
                node_id=node_id,
                source="expansion",
            )
            invalid_node_ids.add(node_id)
            return

        owner_by_node_id[node_id] = parent_node_id
        node_truth_refs = _normalize_truth_refs(spec.get("truth_refs"))
        node_event_refs = _normalize_text_list(spec.get("node_event_refs"))
        dependency_node_ids = _normalize_text_list(spec.get("dependency_node_ids"))
        descendants = [row for row in list(spec.get("descendants") or []) if isinstance(row, Mapping)]
        node_budget_balance = _normalize_budget_balance(spec.get("budget_balance"))
        node_job_shape = _normalize_text(spec.get("job_shape"))
        node_campaign_phase_category = _normalize_text(spec.get("campaign_phase_category")).lower()

        if not _has_truth_refs(node_truth_refs):
            _append_error(
                errors,
                field=f"node:{node_id}.truth_refs",
                code="invalid_value",
                message="planned nodes require canonical truth refs",
                node_id=node_id,
                source="expansion",
            )
            invalid_node_ids.add(node_id)
        if not node_event_refs:
            _append_error(
                errors,
                field=f"node:{node_id}.node_event_refs",
                code="invalid_value",
                message="planned nodes require append-only node_event_refs",
                node_id=node_id,
                source="expansion",
            )
            invalid_node_ids.add(node_id)

        spawn_result = {
            "ok": True,
            "budget_decision": {},
            "non_launch_reason_codes": [],
        }
        computed_budget_balance = node_budget_balance
        if parent_node_id:
            if parent_budget_balance is None:
                _append_error(
                    errors,
                    field=f"node:{node_id}.parent_budget_balance",
                    code="missing_field",
                    message="recursive descendants require a canonical parent budget balance",
                    node_id=node_id,
                    source="expansion",
                )
                invalid_node_ids.add(node_id)
            else:
                spawn_result = recursive_spawn_policy_contract.evaluate_recursive_spawn_request(
                    {
                        "parent_job_shape": parent_job_shape,
                        "child_job_shape": node_job_shape,
                        "planning_mode": normalized["planning_mode"],
                        "campaign_phase_category": parent_campaign_phase_category,
                        "ancestry_path": list(ancestry_path),
                        "ancestor_job_shapes": [
                            nodes_by_id[ancestor_id]["job_shape"]
                            for ancestor_id in ancestry_path
                            if ancestor_id in nodes_by_id
                        ],
                        "parent_budget_balance": dict(parent_budget_balance),
                        "reserved_budget_minor_units": spec.get("parent_reserved_budget_minor_units"),
                        "child_budget_request_minor_units": spec.get("requested_budget_minor_units"),
                        "minimum_parent_remaining_minor_units": spec.get("minimum_parent_remaining_minor_units"),
                    },
                    repo_root=contract_repo_root,
                )
                if not spawn_result["ok"]:
                    for row in spawn_result["errors"]:
                        _append_error(
                            errors,
                            field=f"node:{node_id}.{row['field']}",
                            code=row["code"],
                            message=row["message"],
                            node_id=node_id,
                            source="spawn_policy",
                        )
                        invalid_node_ids.add(node_id)
                budget_decision = dict(spawn_result["budget_decision"] or {})
                computed_budget_balance = _balance_for_child(
                    plan_id=normalized["plan_id"],
                    node_id=node_id,
                    unit=str(budget_decision.get("unit") or parent_budget_balance.get("unit") or ""),
                    allocated_minor_units=int(budget_decision.get("propagated_child_allocation_minor_units") or 0),
                )
                budget_allocations.append(
                    {
                        "node_id": node_id,
                        "parent_node_id": parent_node_id,
                        "requested_minor_units": _normalize_nonnegative_int(spec.get("requested_budget_minor_units")),
                        "allocated_minor_units": computed_budget_balance["allocated_minor_units"],
                        "parent_remaining_after_child_minor_units": int(
                            budget_decision.get("parent_remaining_after_child_minor_units") or 0
                        ),
                        "unit": computed_budget_balance["unit"],
                        "budget_source_contract_uid": str(budget_decision.get("budget_source_contract_uid") or ""),
                        "non_launch_reason_codes": list(spawn_result.get("non_launch_reason_codes") or []),
                    }
                )

        if descendants and computed_budget_balance.get("remaining_minor_units", -1) < 0:
            _append_error(
                errors,
                field=f"node:{node_id}.budget_balance",
                code="missing_field",
                message="nodes that declare descendants require a budget_balance or a valid propagated child allocation",
                node_id=node_id,
                source="expansion",
            )
            invalid_node_ids.add(node_id)

        nodes_by_id[node_id] = {
            "node_id": node_id,
            "job_shape": node_job_shape,
            "node_status": "planned",
            "declared_depth": len(ancestry_path),
            "ancestry_path": list(ancestry_path),
            "parent_node_id": parent_node_id,
            "child_node_ids": [],
            "truth_refs": node_truth_refs,
            "node_event_refs": node_event_refs,
            "budget_balance": computed_budget_balance,
            "campaign_phase_category": node_campaign_phase_category,
        }
        node_lineage.append(
            {
                "node_id": node_id,
                "parent_node_id": parent_node_id,
                "ancestry_path": list(ancestry_path),
                "declared_depth": len(ancestry_path),
                "job_shape": node_job_shape,
            }
        )
        for dependency_node_id in dependency_node_ids:
            dependency_refs.append((node_id, dependency_node_id))
        if parent_node_id:
            dependency_edge_map[(node_id, parent_node_id)] = {
                "edge_id": _edge_id(node_id, parent_node_id),
                "from_node_id": node_id,
                "to_node_id": parent_node_id,
                "edge_kind": "depends_on",
            }

        for child_spec in descendants:
            child_node_id = str(child_spec.get("node_id") or "")
            if child_node_id:
                nodes_by_id[node_id]["child_node_ids"].append(child_node_id)
            _visit_node(
                child_spec,
                parent_node_id=node_id,
                ancestry_path=[*ancestry_path, node_id],
                parent_job_shape=node_job_shape,
                parent_campaign_phase_category=node_campaign_phase_category,
                parent_budget_balance=computed_budget_balance,
            )

    _visit_node(
        root_node,
        parent_node_id="",
        ancestry_path=[],
        parent_job_shape="",
        parent_campaign_phase_category="",
        parent_budget_balance=None,
    )

    if root_node.get("node_id") and root_node["node_id"] in nodes_by_id:
        root_balance = nodes_by_id[root_node["node_id"]]["budget_balance"]
        if root_balance.get("remaining_minor_units", -1) >= 0:
            budget_allocations.insert(
                0,
                {
                    "node_id": root_node["node_id"],
                    "parent_node_id": "",
                    "requested_minor_units": 0,
                    "allocated_minor_units": int(root_balance["allocated_minor_units"]),
                    "parent_remaining_after_child_minor_units": int(root_balance["remaining_minor_units"]),
                    "unit": str(root_balance.get("unit") or ""),
                    "budget_source_contract_uid": "request_seed_balance",
                    "non_launch_reason_codes": [],
                },
            )

    for from_node_id, to_node_id in dependency_refs:
        if to_node_id not in nodes_by_id:
            _append_error(
                errors,
                field=f"node:{from_node_id}.dependency_node_ids",
                code="missing_target_rejected",
                message=f"dependency target {to_node_id} is not declared in the recursive dry-run graph",
                node_id=from_node_id,
                source="expansion",
            )
            invalid_node_ids.add(from_node_id)
            continue
        dependency_edge_map[(from_node_id, to_node_id)] = {
            "edge_id": _edge_id(from_node_id, to_node_id),
            "from_node_id": from_node_id,
            "to_node_id": to_node_id,
            "edge_kind": "depends_on",
        }

    dependency_edges = sorted(dependency_edge_map.values(), key=lambda row: row["edge_id"])
    cycle_nodes = _dependency_cycle_nodes(sorted(nodes_by_id.keys()), dependency_edges)
    for node_id in sorted(cycle_nodes):
        _append_error(
            errors,
            field=f"node:{node_id}.dependency_edges",
            code="dependency_cycle_detected",
            message=f"dependency cycle detected for node {node_id}",
            node_id=node_id,
            source="validation",
        )
        invalid_node_ids.add(node_id)

    raw_plan_record = {
        "schema_version": recursive_plan_record_contract.SCHEMA_VERSION,
        "plan_id": normalized["plan_id"],
        "root_plan_id": normalized["root_plan_id"],
        "root_node_id": root_node.get("node_id") or "",
        "root_binding": dict(normalized["root_binding"]),
        "parent_plan_id": normalized["parent_plan_id"],
        "parent_job_id": normalized["parent_job_id"],
        "plan_status": "validated" if not errors else "invalid",
        "plan_truth_refs": dict(normalized["plan_truth_refs"]),
        "plan_event_refs": list(normalized["plan_event_refs"]),
        "budget_adaptive_receipt": dict(policy_receipt),
        "nodes": [
            {
                "node_id": node["node_id"],
                "job_shape": node["job_shape"],
                "node_status": "invalid" if node["node_id"] in invalid_node_ids else ("validated" if not errors else "planned"),
                "declared_depth": node["declared_depth"],
                "ancestry_path": list(node["ancestry_path"]),
                "parent_node_id": node["parent_node_id"],
                "child_node_ids": sorted(node["child_node_ids"]),
                "truth_refs": dict(node["truth_refs"]),
                "node_event_refs": list(node["node_event_refs"]),
            }
            for node in sorted(nodes_by_id.values(), key=lambda row: row["node_id"])
        ],
        "dependency_edges": dependency_edges,
    }

    record_validation = recursive_plan_record_contract.validate_recursive_plan_record(raw_plan_record)
    for row in record_validation["errors"]:
        code = str(row.get("code") or "")
        field = str(row.get("field") or "")
        node_id = ""
        if field.startswith("node:"):
            node_id = field.split(":", 1)[1].split(".", 1)[0]
        elif field.startswith("nodes["):
            node_id = ""
        _append_error(
            errors,
            field=field,
            code=code,
            message=str(row.get("message") or ""),
            node_id=node_id,
            source="plan_record_contract",
        )
        if node_id:
            invalid_node_ids.add(node_id)

    final_plan_record = dict(record_validation["normalized_record"])
    if errors:
        final_plan_record["plan_status"] = "invalid"
        for node in final_plan_record["nodes"]:
            if node["node_id"] in invalid_node_ids:
                node["node_status"] = "invalid"
    else:
        final_plan_record["plan_status"] = "validated"
        for node in final_plan_record["nodes"]:
            node["node_status"] = "validated"
        record_validation = recursive_plan_record_contract.validate_recursive_plan_record(final_plan_record)
        final_plan_record = dict(record_validation["normalized_record"])

    non_launch_reason_codes: List[str] = []
    for row in errors:
        code = str(row.get("code") or "")
        if code and code not in non_launch_reason_codes:
            non_launch_reason_codes.append(code)

    validation_report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "request_fingerprint": _fingerprint(normalized),
        "plan_fingerprint": record_validation["computed_fingerprint"],
        "report_status": "validated" if not errors else "invalid",
        "live_queue_admission_allowed": False,
        "admission_gate_reason": "successful dry-run validation is evidence only and never grants live recursive launch permission",
        "graph_summary": {
            "plan_id": final_plan_record["plan_id"],
            "root_plan_id": final_plan_record["root_plan_id"],
            "root_node_id": final_plan_record["root_node_id"],
            "plan_status": final_plan_record["plan_status"],
            "node_count": len(final_plan_record["nodes"]),
            "dependency_edge_count": len(final_plan_record["dependency_edges"]),
            "invalid_node_count": len(invalid_node_ids),
        },
        "node_lineage": sorted(node_lineage, key=lambda row: row["node_id"]),
        "budget_allocations": budget_allocations,
        "validation_errors": errors,
        "non_launch_reason_codes": non_launch_reason_codes,
        "budget_adaptive_receipt": policy_receipt,
        "budget_adaptive_reason_codes": list(policy_receipt.get("reason_codes") or []),
    }

    result = {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_request": normalized,
        "plan_record": final_plan_record,
        "validation_report": validation_report,
        "computed_fingerprint": validation_report["plan_fingerprint"],
    }
    if receipt_repo_root is not None and normalized["plan_id"]:
        persisted_receipts = {
            "submitted": recursive_planning_receipts.record_submitted_receipt(
                normalized,
                repo_root=receipt_repo_root,
                writer="recursive_dry_run_planner.build_recursive_dry_run_plan",
            ),
            "validation": recursive_planning_receipts.record_validation_receipt(
                result,
                repo_root=receipt_repo_root,
                writer="recursive_dry_run_planner.build_recursive_dry_run_plan",
            ),
        }
        result["durable_receipts"] = persisted_receipts
        result["reconciled_projection"] = persisted_receipts["validation"]["projection"]
    return result
