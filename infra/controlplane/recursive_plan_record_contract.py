"""Recursive plan-record lineage and dependency contract helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import budget_adaptive_research_policy
from infra.controlplane import job_shape_contract


SCHEMA_VERSION = "recursive_plan_record_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/recursive_plan_record_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_recursive_plan_record_v1.md")
UID = "PIS-20260409-154915Z-E6CF"

PLAN_STATUS_VOCAB: Tuple[str, ...] = (
    "draft",
    "blocked",
    "invalid",
    "validated",
    "admitted",
    "materialized",
    "superseded",
    "cancelled",
)
NODE_STATUS_VOCAB: Tuple[str, ...] = (
    "planned",
    "blocked",
    "invalid",
    "validated",
    "admitted",
    "materialized",
    "cancelled",
)
EDGE_KIND_VOCAB: Tuple[str, ...] = ("depends_on",)
ROOT_BINDING_TYPES: Tuple[str, ...] = ("ledger_uid", "campaign_id", "job_id")
PLAN_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "plan_id",
    "root_plan_id",
    "root_node_id",
    "root_binding",
    "parent_plan_id",
    "parent_job_id",
    "plan_status",
    "plan_truth_refs",
    "plan_event_refs",
    "budget_adaptive_receipt",
    "nodes",
    "dependency_edges",
)
NODE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "node_id",
    "job_shape",
    "node_status",
    "declared_depth",
    "ancestry_path",
    "parent_node_id",
    "child_node_ids",
    "truth_refs",
    "node_event_refs",
)
EDGE_REQUIRED_FIELDS: Tuple[str, ...] = ("edge_id", "from_node_id", "to_node_id", "edge_kind")
TRUTH_REF_FIELDS: Tuple[str, ...] = (
    "ledger_uids",
    "campaign_ids",
    "job_ids",
    "run_ids",
    "receipt_refs",
    "continuation_packet_ids",
    "artifact_refs",
)
FORBIDDEN_INLINE_TRUTH_FIELDS: Tuple[str, ...] = (
    "task_register_snapshot",
    "task_records",
    "job_receipts",
    "receipt_payloads",
    "run_record",
    "transcript_summary",
)
FORBIDDEN_NODE_INLINE_FIELDS: Tuple[str, ...] = (
    "task_snapshot",
    "task_record",
    "receipt_payload",
    "run_payload",
    "transcript_summary",
)
APPEND_ONLY_FIELDS: Tuple[str, ...] = ("plan_event_refs", "node_event_refs")
ALLOW_EMPTY_PLAN_FIELDS = frozenset({"parent_plan_id", "parent_job_id"})
ALLOW_EMPTY_NODE_FIELDS = frozenset({"ancestry_path", "parent_node_id", "child_node_ids"})


@dataclass(frozen=True)
class RecursivePlanRecordContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    plan_status_vocab: Tuple[str, ...]
    node_status_vocab: Tuple[str, ...]
    edge_kind_vocab: Tuple[str, ...]
    append_only_fields: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(value: Any, *, dedupe: bool = True) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    seen: set[str] = set()
    for raw in value:
        text = _normalize_text(raw)
        if not text:
            continue
        if dedupe:
            if text in seen:
                continue
            seen.add(text)
        out.append(text)
    return out


def _normalize_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


def _normalize_truth_refs(value: Any) -> Dict[str, List[str]]:
    row = value if isinstance(value, Mapping) else {}
    return {field: _normalize_text_list(row.get(field)) for field in TRUTH_REF_FIELDS}


def _normalize_binding(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "binding_type": _normalize_text(row.get("binding_type")).lower(),
        "binding_id": _normalize_text(row.get("binding_id")),
    }


def normalize_plan_node(raw: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(raw or {})
    return {
        "node_id": _normalize_text(row.get("node_id")),
        "job_shape": _normalize_text(row.get("job_shape")),
        "node_status": _normalize_text(row.get("node_status")).lower(),
        "declared_depth": _normalize_int(row.get("declared_depth")),
        "ancestry_path": _normalize_text_list(row.get("ancestry_path"), dedupe=False),
        "parent_node_id": _normalize_text(row.get("parent_node_id")),
        "child_node_ids": _normalize_text_list(row.get("child_node_ids"), dedupe=False),
        "truth_refs": _normalize_truth_refs(row.get("truth_refs")),
        "node_event_refs": _normalize_text_list(row.get("node_event_refs"), dedupe=False),
    }


def normalize_dependency_edge(raw: Mapping[str, Any]) -> Dict[str, str]:
    row = dict(raw or {})
    return {
        "edge_id": _normalize_text(row.get("edge_id")),
        "from_node_id": _normalize_text(row.get("from_node_id")),
        "to_node_id": _normalize_text(row.get("to_node_id")),
        "edge_kind": _normalize_text(row.get("edge_kind")).lower(),
    }


def normalize_recursive_plan_record(raw: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(raw or {})
    nodes = [normalize_plan_node(value) for value in list(row.get("nodes") or []) if isinstance(value, Mapping)]
    edges = [normalize_dependency_edge(value) for value in list(row.get("dependency_edges") or []) if isinstance(value, Mapping)]
    nodes.sort(key=lambda value: value["node_id"])
    edges.sort(key=lambda value: value["edge_id"])
    return {
        "schema_version": _normalize_text(row.get("schema_version") or SCHEMA_VERSION),
        "plan_id": _normalize_text(row.get("plan_id")),
        "root_plan_id": _normalize_text(row.get("root_plan_id")),
        "root_node_id": _normalize_text(row.get("root_node_id")),
        "root_binding": _normalize_binding(row.get("root_binding")),
        "parent_plan_id": _normalize_text(row.get("parent_plan_id")),
        "parent_job_id": _normalize_text(row.get("parent_job_id")),
        "plan_status": _normalize_text(row.get("plan_status")).lower(),
        "plan_truth_refs": _normalize_truth_refs(row.get("plan_truth_refs")),
        "plan_event_refs": _normalize_text_list(row.get("plan_event_refs"), dedupe=False),
        "budget_adaptive_receipt": budget_adaptive_research_policy.normalize_budget_adaptive_planning_receipt(
            row.get("budget_adaptive_receipt") if isinstance(row.get("budget_adaptive_receipt"), Mapping) else {}
        ),
        "nodes": nodes,
        "dependency_edges": edges,
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _has_nonempty_truth_ref(value: Mapping[str, Sequence[str]]) -> bool:
    return any(bool(list(value.get(field) or ())) for field in TRUTH_REF_FIELDS)


def recursive_plan_record_contract_v1() -> RecursivePlanRecordContractV1:
    return RecursivePlanRecordContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        plan_status_vocab=PLAN_STATUS_VOCAB,
        node_status_vocab=NODE_STATUS_VOCAB,
        edge_kind_vocab=EDGE_KIND_VOCAB,
        append_only_fields=APPEND_ONLY_FIELDS,
    )


def load_recursive_plan_record_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_recursive_plan_record(raw_record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    raw = dict(raw_record or {})
    normalized = normalize_recursive_plan_record(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field in PLAN_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", [], {}) and field not in ALLOW_EMPTY_PLAN_FIELDS or value is None:
            errors.append({"field": field, "code": "missing_field", "message": f"recursive plan record requires {field}"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": "recursive plan schema_version drifted"})
    if normalized["plan_status"] not in PLAN_STATUS_VOCAB:
        errors.append({"field": "plan_status", "code": "invalid_value", "message": "illegal recursive plan status"})

    if not _has_nonempty_truth_ref(normalized["plan_truth_refs"]):
        errors.append(
            {
                "field": "plan_truth_refs",
                "code": "invalid_value",
                "message": "recursive plan record must point back to canonical lower-layer truth",
            }
        )
    if not normalized["plan_event_refs"]:
        errors.append(
            {
                "field": "plan_event_refs",
                "code": "invalid_value",
                "message": "recursive plan record requires append-only event refs",
            }
        )
    if not normalized["budget_adaptive_receipt"]:
        errors.append(
            {
                "field": "budget_adaptive_receipt",
                "code": "missing_field",
                "message": "recursive plan record requires a durable budget-adaptive planning receipt",
            }
        )
    else:
        errors.extend(
            budget_adaptive_research_policy.validate_budget_adaptive_planning_receipt(
                normalized["budget_adaptive_receipt"],
                repo_root=repo_root,
                field_prefix="budget_adaptive_receipt",
            )
        )

    root_binding = normalized["root_binding"]
    if root_binding["binding_type"] not in ROOT_BINDING_TYPES:
        errors.append(
            {
                "field": "root_binding.binding_type",
                "code": "invalid_value",
                "message": "root_binding.binding_type must reference canonical ledger, campaign, or job truth",
            }
        )
    if not root_binding["binding_id"]:
        errors.append({"field": "root_binding.binding_id", "code": "missing_field", "message": "root binding requires an id"})

    is_root_plan = normalized["plan_id"] == normalized["root_plan_id"]
    if not is_root_plan and not normalized["parent_plan_id"]:
        errors.append(
            {
                "field": "parent_plan_id",
                "code": "missing_field",
                "message": "non-root recursive plans must link back to an explicit parent_plan_id",
            }
        )
    if is_root_plan and normalized["parent_plan_id"]:
        warnings.append(
            {
                "field": "parent_plan_id",
                "code": "root_plan_with_parent",
                "message": "root plans normally omit parent_plan_id; value ignored for canonical lineage decisions",
            }
        )

    for field in FORBIDDEN_INLINE_TRUTH_FIELDS:
        if field in raw and raw.get(field) not in (None, "", [], {}, ()):
            errors.append(
                {
                    "field": field,
                    "code": "forbidden_inline_truth",
                    "message": "recursive plan records must carry durable refs instead of embedded task, run, or receipt truth",
                }
            )

    node_ids_seen: List[str] = []
    normalized_nodes = normalized["nodes"]
    for index, raw_node in enumerate(list(raw.get("nodes") or [])):
        if not isinstance(raw_node, Mapping):
            errors.append({"field": f"nodes[{index}]", "code": "invalid_type", "message": "plan node must be an object"})
            continue
        node = normalize_plan_node(raw_node)
        for field in NODE_REQUIRED_FIELDS:
            value = node.get(field)
            if value in ("", {}) and field not in ALLOW_EMPTY_NODE_FIELDS or value is None or (
                value == [] and field not in ALLOW_EMPTY_NODE_FIELDS
            ):
                errors.append({"field": f"nodes[{index}].{field}", "code": "missing_field", "message": f"plan node requires {field}"})
        if node["node_status"] not in NODE_STATUS_VOCAB:
            errors.append({"field": f"nodes[{index}].node_status", "code": "invalid_value", "message": "illegal plan node status"})
        if node["declared_depth"] < 0:
            errors.append(
                {
                    "field": f"nodes[{index}].declared_depth",
                    "code": "invalid_value",
                    "message": "declared_depth must be a non-negative integer",
                }
            )
        if len(node["child_node_ids"]) != len(set(node["child_node_ids"])):
            errors.append(
                {
                    "field": f"nodes[{index}].child_node_ids",
                    "code": "duplicate_child_node_id",
                    "message": "child_node_ids must not repeat a child identifier",
                }
            )
        if len(node["ancestry_path"]) != len(set(node["ancestry_path"])):
            errors.append(
                {
                    "field": f"nodes[{index}].ancestry_path",
                    "code": "malformed_ancestry_path",
                    "message": "ancestry_path must not repeat ancestor ids in v1",
                }
            )
        if node["declared_depth"] >= 0 and node["declared_depth"] != len(node["ancestry_path"]):
            errors.append(
                {
                    "field": f"nodes[{index}].declared_depth",
                    "code": "malformed_ancestry_path",
                    "message": "declared_depth must equal len(ancestry_path)",
                }
            )
        if node["node_id"] == normalized["root_node_id"]:
            if node["parent_node_id"]:
                errors.append(
                    {
                        "field": f"nodes[{index}].parent_node_id",
                        "code": "root_parent_mismatch",
                        "message": "root node must not claim a parent_node_id",
                    }
                )
            if node["declared_depth"] not in {-1, 0} or node["ancestry_path"]:
                errors.append(
                    {
                        "field": f"nodes[{index}].ancestry_path",
                        "code": "malformed_ancestry_path",
                        "message": "root node must have declared_depth=0 and an empty ancestry_path",
                    }
                )
        else:
            if not node["parent_node_id"]:
                errors.append(
                    {
                        "field": f"nodes[{index}].parent_node_id",
                        "code": "missing_field",
                        "message": "non-root plan nodes require parent_node_id",
                    }
                )
            if node["ancestry_path"]:
                if node["ancestry_path"][0] != normalized["root_node_id"]:
                    errors.append(
                        {
                            "field": f"nodes[{index}].ancestry_path",
                            "code": "malformed_ancestry_path",
                            "message": "non-root ancestry_path must begin with root_node_id",
                        }
                    )
                if node["parent_node_id"] and node["ancestry_path"][-1] != node["parent_node_id"]:
                    errors.append(
                        {
                            "field": f"nodes[{index}].ancestry_path",
                            "code": "malformed_ancestry_path",
                            "message": "ancestry_path must end with parent_node_id",
                        }
                    )
        if not _has_nonempty_truth_ref(node["truth_refs"]):
            errors.append(
                {
                    "field": f"nodes[{index}].truth_refs",
                    "code": "invalid_value",
                    "message": "planned node must point back to canonical lower-layer truth refs",
                }
            )
        if not node["node_event_refs"]:
            errors.append(
                {
                    "field": f"nodes[{index}].node_event_refs",
                    "code": "invalid_value",
                    "message": "planned node requires append-only event refs",
                }
            )
        try:
            job_shape_contract.canonical_job_shape_name(node["job_shape"])
        except KeyError:
            errors.append(
                {"field": f"nodes[{index}].job_shape", "code": "invalid_value", "message": "planned node job_shape must be canonical"}
            )
        if node["node_id"] in node_ids_seen:
            errors.append({"field": f"nodes[{index}].node_id", "code": "duplicate_node_id", "message": "node_id must be unique"})
        if node["node_id"]:
            node_ids_seen.append(node["node_id"])
        for forbidden in FORBIDDEN_NODE_INLINE_FIELDS:
            if forbidden in raw_node and raw_node.get(forbidden) not in (None, "", [], {}, ()):
                errors.append(
                    {
                        "field": f"nodes[{index}].{forbidden}",
                        "code": "forbidden_inline_truth",
                        "message": "plan nodes must carry refs rather than embedded task, run, or receipt truth",
                    }
                )
        for key in raw_node.keys():
            if key not in NODE_REQUIRED_FIELDS:
                warnings.append(
                    {
                        "field": f"nodes[{index}].{key}",
                        "code": "unknown_field",
                        "message": "node field is outside the v1 contract and ignored for canonicalization",
                    }
                )

    node_map = {node["node_id"]: node for node in normalized_nodes if node["node_id"]}
    if normalized["root_node_id"] and normalized["root_node_id"] not in node_map:
        errors.append(
            {
                "field": "root_node_id",
                "code": "undeclared_root_node",
                "message": "root_node_id must point at a declared node_id",
            }
        )

    derived_children: Dict[str, List[str]] = {node_id: [] for node_id in node_map}
    for node in normalized_nodes:
        parent_node_id = node["parent_node_id"]
        if not parent_node_id:
            continue
        if parent_node_id not in node_map:
            errors.append(
                {
                    "field": f"node:{node['node_id']}.parent_node_id",
                    "code": "undeclared_parent_node",
                    "message": f"parent_node_id {parent_node_id} is not declared in the recursive plan",
                }
            )
            continue
        derived_children[parent_node_id].append(node["node_id"])

    for node in normalized_nodes:
        declared_children = sorted(node["child_node_ids"])
        actual_children = sorted(derived_children.get(node["node_id"], []))
        for child_id in node["child_node_ids"]:
            if child_id not in node_map:
                errors.append(
                    {
                        "field": f"node:{node['node_id']}.child_node_ids",
                        "code": "undeclared_child_node",
                        "message": f"child_node_id {child_id} is not declared in the recursive plan",
                    }
                )
        if declared_children != actual_children:
            errors.append(
                {
                    "field": f"node:{node['node_id']}.child_node_ids",
                    "code": "parent_child_mismatch",
                    "message": "child_node_ids must exactly match nodes that declare this node as parent_node_id",
                }
            )

    edge_ids_seen: List[str] = []
    for index, raw_edge in enumerate(list(raw.get("dependency_edges") or [])):
        if not isinstance(raw_edge, Mapping):
            errors.append(
                {"field": f"dependency_edges[{index}]", "code": "invalid_type", "message": "dependency edge must be an object"}
            )
            continue
        edge = normalize_dependency_edge(raw_edge)
        for field in EDGE_REQUIRED_FIELDS:
            if not edge.get(field):
                errors.append(
                    {"field": f"dependency_edges[{index}].{field}", "code": "missing_field", "message": f"dependency edge requires {field}"}
                )
        if edge["edge_kind"] not in EDGE_KIND_VOCAB:
            errors.append(
                {"field": f"dependency_edges[{index}].edge_kind", "code": "invalid_value", "message": "illegal dependency edge kind"}
            )
        if edge["edge_id"] in edge_ids_seen:
            errors.append(
                {"field": f"dependency_edges[{index}].edge_id", "code": "duplicate_edge_id", "message": "edge_id must be unique"}
            )
        if edge["edge_id"]:
            edge_ids_seen.append(edge["edge_id"])
        if edge["from_node_id"] == edge["to_node_id"] and edge["from_node_id"]:
            errors.append(
                {
                    "field": f"dependency_edges[{index}]",
                    "code": "self_edge",
                    "message": "dependency edges must not point from a node to itself",
                }
            )
        if edge["from_node_id"] and edge["from_node_id"] not in node_map:
            errors.append(
                {
                    "field": f"dependency_edges[{index}].from_node_id",
                    "code": "undeclared_edge_target",
                    "message": f"dependency source {edge['from_node_id']} is not declared in the plan",
                }
            )
        if edge["to_node_id"] and edge["to_node_id"] not in node_map:
            errors.append(
                {
                    "field": f"dependency_edges[{index}].to_node_id",
                    "code": "undeclared_edge_target",
                    "message": f"dependency target {edge['to_node_id']} is not declared in the plan",
                }
            )
        for key in raw_edge.keys():
            if key not in EDGE_REQUIRED_FIELDS:
                warnings.append(
                    {
                        "field": f"dependency_edges[{index}].{key}",
                        "code": "unknown_field",
                        "message": "dependency edge field is outside the v1 contract and ignored for canonicalization",
                    }
                )

    for key in raw.keys():
        if key not in PLAN_REQUIRED_FIELDS:
            warnings.append(
                {
                    "field": key,
                    "code": "unknown_field",
                    "message": "recursive plan field is outside the v1 contract and ignored for canonicalization",
                }
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_record": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def validate_recursive_plan_record_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_recursive_plan_record_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append(f"schema_version must remain {SCHEMA_VERSION}")
    if str(payload.get("uid") or "") != UID:
        issues.append(f"uid must remain bound to {UID}")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the recursive plan record contract doc")
    if tuple(payload.get("plan_status_vocab") or ()) != PLAN_STATUS_VOCAB:
        issues.append("plan_status_vocab must match the runtime helper contract")
    if tuple(payload.get("node_status_vocab") or ()) != NODE_STATUS_VOCAB:
        issues.append("node_status_vocab must match the runtime helper contract")
    if tuple(payload.get("edge_kind_vocab") or ()) != EDGE_KIND_VOCAB:
        issues.append("edge_kind_vocab must match the runtime helper contract")
    if tuple(payload.get("root_binding_types") or ()) != ROOT_BINDING_TYPES:
        issues.append("root_binding_types must remain stable and ordered")
    if tuple(payload.get("plan_required_fields") or ()) != PLAN_REQUIRED_FIELDS:
        issues.append("plan_required_fields must remain stable and ordered")
    if tuple(payload.get("node_required_fields") or ()) != NODE_REQUIRED_FIELDS:
        issues.append("node_required_fields must remain stable and ordered")
    if tuple(payload.get("edge_required_fields") or ()) != EDGE_REQUIRED_FIELDS:
        issues.append("edge_required_fields must remain stable and ordered")
    if tuple(payload.get("truth_ref_fields") or ()) != TRUTH_REF_FIELDS:
        issues.append("truth_ref_fields must remain stable and ordered")

    forbidden_top = payload.get("forbidden_inline_truth_fields")
    if not isinstance(forbidden_top, dict) or tuple(forbidden_top.keys()) != FORBIDDEN_INLINE_TRUTH_FIELDS:
        issues.append("forbidden_inline_truth_fields must list the canonical forbidden top-level truth fields in order")
    forbidden_node = payload.get("forbidden_node_inline_fields")
    if not isinstance(forbidden_node, dict) or tuple(forbidden_node.keys()) != FORBIDDEN_NODE_INLINE_FIELDS:
        issues.append("forbidden_node_inline_fields must list the canonical forbidden node truth fields in order")
    if tuple(payload.get("append_only_fields") or ()) != APPEND_ONLY_FIELDS:
        issues.append("append_only_fields must remain aligned with the append-only contract fields")

    return tuple(issues)
