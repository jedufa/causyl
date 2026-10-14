from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence

from infra.controlplane import compiled_work_order_boundary_emission as compiled_boundary
from infra.controlplane import intent_family_v2_workflow_contract as workflow_contract
from infra.webui.ledger import ledger_storage
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from infra.webui.ledger.ledger_manager import ChangeLedger


SCHEMA_VERSION = "sched_v2_fanout_authoring_plan_v1"
DEFAULT_ARTIFACT_REL = Path("runtime/state/controlplane/sched_v2/fanout_authoring_plan.json")


class FanoutAuthoringError(ValueError):
    """Raised when a fanout authoring request cannot compile into v2 normal form."""


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_rows(value: Any, *, field: str) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray, Mapping)):
        raise FanoutAuthoringError(f"{field} must be a list")
    rows = []
    for index, row in enumerate(value):
        if not isinstance(row, Mapping):
            raise FanoutAuthoringError(f"{field}[{index}] must be an object")
        rows.append(dict(row))
    return tuple(rows)


def _require_text(row: Mapping[str, Any], key: str, *, field: str) -> str:
    value = _normalize_text(row.get(key))
    if not value:
        raise FanoutAuthoringError(f"{field}.{key} is required")
    return value


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _child(
    *,
    uid: str,
    title: str,
    relationship: str,
    rank: int,
    depends_on_uids: Sequence[str],
    fanout_role: str,
    objective: str = "",
) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
        "relationship": relationship,
        "critical_path_rank": int(rank),
        "depends_on_uids": list(depends_on_uids),
        "fanout_role": fanout_role,
        "objective": objective,
    }


def _node_for_child(row: Mapping[str, Any]) -> dict[str, Any]:
    relationship = _normalize_text(row.get("relationship"))
    if relationship == "intent_validation_suite":
        stage = "intent_validation"
    elif relationship == "intent_family_closeout":
        stage = "closeout"
    else:
        stage = "implement"
    return {
        "node_id": _normalize_text(row.get("uid")),
        "node_kind": relationship,
        "stage": stage,
        "title": _normalize_text(row.get("title")),
        "critical_path_rank": int(row.get("critical_path_rank") or 0),
        "depends_on_uids": list(row.get("depends_on_uids") or []),
        "fanout_role": _normalize_text(row.get("fanout_role")),
    }


def compile_fanout_authoring_plan(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Compile fanout/swarm authoring intent into the scheduler v2 normal form.

    The output is deliberately not a new scheduler workflow. It is a plan for
    creating a normal intent_family_v2 ledger family whose parallelism is fully
    expressed by dependency edges.
    """

    source_intent = spec.get("source_intent") if isinstance(spec.get("source_intent"), Mapping) else {}
    parent = spec.get("parent") if isinstance(spec.get("parent"), Mapping) else {}
    source_intent_uid = _require_text(source_intent, "uid", field="source_intent")
    parent_uid = _require_text(parent, "uid", field="parent")
    source_intent_title = _require_text(source_intent, "title", field="source_intent")
    parent_title = _require_text(parent, "title", field="parent")

    exploration_rows = _normalize_rows(
        spec.get("parallel_children") if "parallel_children" in spec else spec.get("exploration_children"),
        field="parallel_children",
    )
    if len(exploration_rows) < 2:
        raise FanoutAuthoringError("parallel_children must contain at least two independent children")

    seen_uids = {source_intent_uid, parent_uid}
    children: list[dict[str, Any]] = []
    for index, raw in enumerate(exploration_rows, start=1):
        uid = _require_text(raw, "uid", field=f"parallel_children[{index - 1}]")
        if uid in seen_uids:
            raise FanoutAuthoringError(f"duplicate uid: {uid}")
        seen_uids.add(uid)
        if raw.get("depends_on_uids"):
            raise FanoutAuthoringError("parallel_children must not declare dependencies; use the integration child for fan-in")
        children.append(
            _child(
                uid=uid,
                title=_require_text(raw, "title", field=f"parallel_children[{index - 1}]"),
                relationship="execution_child",
                rank=index,
                depends_on_uids=(),
                fanout_role="parallel_exploration",
                objective=_normalize_text(raw.get("objective")),
            )
        )

    integration = spec.get("integration_child") if isinstance(spec.get("integration_child"), Mapping) else {}
    validation = spec.get("validation_child") if isinstance(spec.get("validation_child"), Mapping) else {}
    closeout = spec.get("closeout_child") if isinstance(spec.get("closeout_child"), Mapping) else {}
    integration_uid = _require_text(integration, "uid", field="integration_child")
    validation_uid = _require_text(validation, "uid", field="validation_child")
    closeout_uid = _require_text(closeout, "uid", field="closeout_child")
    for uid in (integration_uid, validation_uid, closeout_uid):
        if uid in seen_uids:
            raise FanoutAuthoringError(f"duplicate uid: {uid}")
        seen_uids.add(uid)

    exploration_uids = [row["uid"] for row in children]
    integration_rank = len(children) + 1
    children.append(
        _child(
            uid=integration_uid,
            title=_require_text(integration, "title", field="integration_child"),
            relationship="execution_child",
            rank=integration_rank,
            depends_on_uids=exploration_uids,
            fanout_role="fan_in_integration",
            objective=_normalize_text(integration.get("objective")),
        )
    )
    children.append(
        _child(
            uid=validation_uid,
            title=_require_text(validation, "title", field="validation_child"),
            relationship=workflow_contract.VALIDATION_CHILD_KIND,
            rank=integration_rank + 1,
            depends_on_uids=(integration_uid,),
            fanout_role="intent_validation",
            objective=_normalize_text(validation.get("objective")),
        )
    )
    children.append(
        _child(
            uid=closeout_uid,
            title=_require_text(closeout, "title", field="closeout_child"),
            relationship=workflow_contract.REQUIRED_FINAL_CHILD_KIND,
            rank=integration_rank + 2,
            depends_on_uids=(validation_uid,),
            fanout_role="closeout_promotion",
            objective=_normalize_text(closeout.get("objective")),
        )
    )

    graph_nodes = [
        {"node_id": source_intent_uid, "node_kind": "source_intent", "stage": "intent", "title": source_intent_title},
        {"node_id": parent_uid, "node_kind": "family_parent", "stage": "parent", "title": parent_title},
        *[_node_for_child(row) for row in children],
    ]
    graph_edges = [{"from_node_id": source_intent_uid, "to_node_id": parent_uid, "edge_kind": "stage_dependency"}]
    for row in children:
        deps = list(row.get("depends_on_uids") or [])
        if deps:
            graph_edges.extend({"from_node_id": uid, "to_node_id": row["uid"], "edge_kind": "depends_on"} for uid in deps)
        else:
            graph_edges.append({"from_node_id": parent_uid, "to_node_id": row["uid"], "edge_kind": "stage_dependency"})

    parent_integration_meta = {
        "intent_sheet_uid": source_intent_uid,
        "child_uids": [row["uid"] for row in children],
        "intent_validation_child_uid": validation_uid,
        "required_final_child_uid": closeout_uid,
        "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
        "authoring_compiler": SCHEMA_VERSION,
        "authoring_kind": "fanout",
    }
    payload = {
        "schema_version": SCHEMA_VERSION,
        "workflow_kind": workflow_contract.WORKFLOW_KIND,
        "source_intent": {"uid": source_intent_uid, "title": source_intent_title},
        "parent": {"uid": parent_uid, "title": parent_title, "integration_meta": parent_integration_meta},
        "children": children,
        "parallel_groups": [{"group_id": "parallel_exploration", "child_uids": exploration_uids}],
        "scheduler_boundary": {
            "rule": "fanout is authoring-time only; sched_v2 receives ordinary intent_family_v2 children and dependency edges",
            "forbidden_runtime_branches": ["swarm", "fanout", "campaign"],
        },
        "family_graph": {
            "schema_version": "intent_family_v2_authoring_graph_v1",
            "workflow_kind": workflow_contract.WORKFLOW_KIND,
            "parent_uid": parent_uid,
            "source_intent_uid": source_intent_uid,
            "nodes": sorted(graph_nodes, key=lambda row: (_normalize_text(row.get("stage")), _normalize_text(row.get("node_id")))),
            "edges": sorted(graph_edges, key=lambda row: (_normalize_text(row.get("to_node_id")), _normalize_text(row.get("from_node_id")), _normalize_text(row.get("edge_kind")))),
            "recommended_execution_order": [row["uid"] for row in children],
            "required_final_child_uid": closeout_uid,
            "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
        },
    }
    payload["family_graph"]["graph_fingerprint"] = _fingerprint(payload["family_graph"])
    payload["authoring_fingerprint"] = _fingerprint(payload)
    return payload


def write_fanout_authoring_plan_artifact(
    spec: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    artifact_rel: Path = DEFAULT_ARTIFACT_REL,
) -> Path:
    root = Path(repo_root or ".").resolve()
    payload = compile_fanout_authoring_plan(spec)
    path = root / artifact_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def _parent_pis_markdown(plan: Mapping[str, Any]) -> str:
    parent = plan.get("parent") if isinstance(plan.get("parent"), Mapping) else {}
    source = plan.get("source_intent") if isinstance(plan.get("source_intent"), Mapping) else {}
    children = [row for row in list(plan.get("children") or []) if isinstance(row, Mapping)]
    lines = [
        f"# {parent.get('title')}",
        "",
        f"**UID:** {parent.get('uid')}",
        "",
        "## Scope",
        f"- Source intent: `{source.get('uid')}`",
        "- Authoring kind: fanout compiled into intent_family_v2 normal form",
        "",
        "## Children",
    ]
    for row in children:
        deps = list(row.get("depends_on_uids") or [])
        dep_text = ", ".join(f"`{dep}`" for dep in deps) if deps else "none"
        lines.append(f"- `{row.get('uid')}` {row.get('fanout_role')}: {row.get('title')} | deps: {dep_text}")
    return "\n".join(lines).strip() + "\n"


def _pis_markdown(row: Mapping[str, Any], *, parent_uid: str, intent_uid: str) -> str:
    uid = _normalize_text(row.get("uid"))
    title = _normalize_text(row.get("title")) or uid
    role = _normalize_text(row.get("fanout_role"))
    relationship = _normalize_text(row.get("relationship"))
    deps = list(row.get("depends_on_uids") or [])
    objective = _normalize_text(row.get("objective"))
    lines = [
        f"# {title}",
        "",
        f"**UID:** {uid}",
        "",
        "## Scope",
        f"- Parent PIS: `{parent_uid}`",
        f"- Source intent: `{intent_uid}`",
        f"- Relationship: `{relationship}`",
        f"- Fanout role: `{role}`",
    ]
    if objective:
        lines.extend(["", "## Objective", objective])
    lines.extend(["", "## Dependencies"])
    if deps:
        lines.extend(f"- `{dep}`" for dep in deps)
    else:
        lines.append("- none; this child is eligible for parallel admission when the parent family is scheduled")
    lines.extend(
        [
            "",
            "## Acceptance",
            "- Produce a concise implementation/report artifact for this child scope.",
            "- Do not silently absorb sibling fanout children.",
            "- Leave evidence suitable for the validation and closeout children.",
        ]
    )
    return "\n".join(lines).strip() + "\n"


def _update_parent_meta(items: ChangeLedger, plan: Mapping[str, Any]) -> None:
    parent = plan.get("parent") if isinstance(plan.get("parent"), Mapping) else {}
    items.update_meta(
        str(parent.get("uid") or ""),
        {
            "integration_meta": dict(parent.get("integration_meta") or {}),
            "tags": ["scheduler_v2", "fanout", "intent_family_v2"],
        },
    )


def _update_child_meta_and_events(items: ChangeLedger, plan: Mapping[str, Any]) -> None:
    parent = plan.get("parent") if isinstance(plan.get("parent"), Mapping) else {}
    source = plan.get("source_intent") if isinstance(plan.get("source_intent"), Mapping) else {}
    parent_uid = _normalize_text(parent.get("uid"))
    intent_uid = _normalize_text(source.get("uid"))
    for row in [item for item in list(plan.get("children") or []) if isinstance(item, Mapping)]:
        uid = _normalize_text(row.get("uid"))
        payload = {
            "parent_uid": parent_uid,
            "relationship": _normalize_text(row.get("relationship")),
            "critical_path_rank": int(row.get("critical_path_rank") or 0),
            "depends_on": list(row.get("depends_on_uids") or []),
            "fanout_role": _normalize_text(row.get("fanout_role")),
        }
        items.update_meta(
            uid,
            {
                "integration_meta": {
                    "parent_uid": parent_uid,
                    "intent_sheet_uid": intent_uid,
                    "relationship": payload["relationship"],
                    "sequence_index": payload["critical_path_rank"],
                    "fanout_role": payload["fanout_role"],
                    "authoring_compiler": SCHEMA_VERSION,
                    "authoring_kind": "fanout",
                },
                "tags": ["scheduler_v2", "fanout", str(row.get("fanout_role") or "")],
            },
        )
        items.append_event(uid, event_type="decomposed_from_parent", payload=payload)


def _materialize_plan(
    plan: Mapping[str, Any],
    *,
    project_id: str = "causyl",
    repo_root: Path,
) -> dict[str, Any]:
    root = Path(repo_root).resolve()
    source = plan.get("source_intent") if isinstance(plan.get("source_intent"), Mapping) else {}
    parent = plan.get("parent") if isinstance(plan.get("parent"), Mapping) else {}
    children = [row for row in list(plan.get("children") or []) if isinstance(row, Mapping)]
    items = ChangeLedger(repo_root=root)

    items.create_item(
        uid_override=str(parent["uid"]),
        title=str(parent["title"]),
        pis_markdown=_parent_pis_markdown(plan),
        project_id=project_id,
        tags=["scheduler_v2", "fanout", "intent_family_v2"],
        workstream_type="shared_capability",
    )
    _update_parent_meta(items, plan)
    for row in children:
        items.create_item(
            uid_override=str(row["uid"]),
            title=str(row["title"]),
            pis_markdown=_pis_markdown(row, parent_uid=str(parent["uid"]), intent_uid=str(source["uid"])),
            project_id=project_id,
            tags=["scheduler_v2", "fanout", str(row.get("fanout_role") or "")],
            workstream_type="shared_capability",
        )
    _update_child_meta_and_events(items, plan)

    parent_dir = ledger_storage.item_dir_for_uid(ledger_storage.ledger_patch_instruction_sheets_root(root), str(parent["uid"]))
    artifact_rel = (
        parent_dir.relative_to(root) / "artifacts" / "sched_v2" / "fanout_authoring_plan.json"
        if parent_dir is not None
        else DEFAULT_ARTIFACT_REL
    )
    compiled_work_order = compiled_boundary.emit_compiled_work_order_boundary(
        boundary_kind="fanout_child_brief",
        work_order_id=f"{parent['uid']}__fanout_child_brief",
        source_binding={"binding_type": "ledger_uid", "binding_id": str(parent["uid"])},
        handoff_kind="fanout_child_brief",
        scope_objective="Compile the stable scheduler-v2 fanout child-brief handoff for orchestrator-owned child PIS emission.",
        in_scope=[
            artifact_rel.as_posix(),
            "governance/fanout_child_contract_v1.json",
        ],
        out_of_scope=[
            "runtime descendant execution",
            "builder/verifier compiled-envelope consumption",
        ],
        required_context_bundle="lab_core_v1",
        additional_context_refs=[
            "governance/fanout_child_contract_v1.json",
            "governance/intent_family_v2_workflow_definition_contract_v1.json",
        ],
        forbidden_context_refs=[],
        expected_outputs=[
            {
                "output_id": "fanout_authoring_plan",
                "kind": "artifact",
                "summary": "Durable scheduler-v2 fanout authoring plan for orchestrator-owned child-brief fanout.",
                "required": True,
            }
        ],
        validation_checks=["fanout_authoring_compile", "targeted_regression_tests"],
        owner_artifact_rel=artifact_rel,
        prose_brief_present=True,
        execution_family={
            "contract_kind": "intent_family_v2",
            "workflow_kind": workflow_contract.WORKFLOW_KIND,
            "boundary_owner": "orchestrator",
        },
        repo_root=root,
    )
    artifact_path = _write_fanout_plan_artifact(
        plan,
        repo_root=root,
        artifact_rel=artifact_rel,
        compiled_work_order=compiled_work_order,
    )
    return {
        "ok": True,
        "schema_version": "sched_v2_fanout_materialization_v1",
        "intent_uid": str(source["uid"]),
        "parent_uid": str(parent["uid"]),
        "child_uids": [str(row["uid"]) for row in children],
        "artifact_path": str(artifact_path.relative_to(root)),
        "compiled_work_order": compiled_work_order,
        "plan": plan,
    }


def _write_fanout_plan_artifact(
    plan: Mapping[str, Any],
    *,
    repo_root: Path,
    artifact_rel: Path,
    compiled_work_order: Mapping[str, Any] | None = None,
) -> Path:
    root = Path(repo_root).resolve()
    path = root / artifact_rel
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    payload = dict(plan)
    if isinstance(compiled_work_order, Mapping) and compiled_work_order:
        payload["compiled_work_order"] = dict(compiled_work_order)
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    return path


def materialize_fanout_for_intent(
    intent_uid: str,
    fanout: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    project_id: str = "",
) -> dict[str, Any]:
    """Attach a fanout workflow methodology to an existing Intent Sheet."""

    root = Path(repo_root or ".").resolve()
    intent_ledger = IntentSheetLedger(repo_root=root)
    sheet = intent_ledger.read_sheet(_normalize_text(intent_uid))
    sheet_meta = sheet.get("meta") if isinstance(sheet.get("meta"), Mapping) else {}
    source_title = _normalize_text(sheet_meta.get("title")) or _normalize_text(sheet.get("uid")) or _normalize_text(intent_uid)
    sheet_project_id = _normalize_text(sheet_meta.get("project_id")) or "causyl"
    requested_source = fanout.get("source_intent") if isinstance(fanout.get("source_intent"), Mapping) else {}
    requested_source_uid = _normalize_text(requested_source.get("uid"))
    if requested_source_uid and requested_source_uid != _normalize_text(intent_uid):
        raise FanoutAuthoringError(f"source_intent.uid must match existing intent sheet uid: {intent_uid}")
    spec = dict(fanout)
    spec["source_intent"] = {"uid": _normalize_text(intent_uid), "title": source_title}
    plan = compile_fanout_authoring_plan(spec)
    result = _materialize_plan(
        plan,
        project_id=_normalize_text(project_id) or sheet_project_id,
        repo_root=root,
    )

    existing_derived = [_normalize_text(uid) for uid in list(sheet_meta.get("derived_pis_uids") or []) if _normalize_text(uid)]
    existing_validating = [_normalize_text(uid) for uid in list(sheet_meta.get("validating_pis_uids") or []) if _normalize_text(uid)]
    parent_uid = _normalize_text(result.get("parent_uid"))
    validation_uid = _normalize_text(plan["parent"]["integration_meta"]["intent_validation_child_uid"])
    intent_ledger.update_meta(
        _normalize_text(intent_uid),
        {
            "derived_pis_uids": [*existing_derived, *([] if parent_uid in existing_derived else [parent_uid])],
            "validating_pis_uids": [*existing_validating, *([] if validation_uid in existing_validating else [validation_uid])],
            "decomposition_class": "workflow",
            "decomposition_target_range": {"min": len(result["child_uids"]), "max": len(result["child_uids"])},
            "decomposition_rationale": "Existing Intent Sheet will be implemented via fanout methodology compiled into scheduler v2 intent_family_v2 normal form.",
            "mandatory_tail": {"proof": True, "closure": True},
        },
    )
    result["intent_sheet_mode"] = "existing"
    return result
