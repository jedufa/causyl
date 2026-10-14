"""Compile intent_family_v2 ledger families into canonical graph and job specs."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from infra.controlplane import intent_family_v2_workflow_contract as workflow_contract
from infra.controlplane import job_shape_contract
from infra.controlplane import scheduled_jobs as sched


SCHEMA_VERSION = "intent_family_v2_compilation_v1"
JOB_KIND = "build_verify_loop"
JOB_SHAPE = "verification_loop_job"


class IntentFamilyV2CompileError(ValueError):
    """Raised when a family cannot be compiled into canonical artifacts."""

    def __init__(self, diagnostics: Sequence[Mapping[str, str]]):
        self.diagnostics = tuple(dict(row) for row in diagnostics)
        summary = "; ".join(f"{row.get('code', 'invalid')}:{row.get('message', '')}" for row in self.diagnostics)
        super().__init__(summary or "intent_family_v2 compilation failed")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _append(diagnostics: List[Dict[str, str]], *, code: str, message: str, field: str = "") -> None:
    diagnostics.append({"code": code, "message": message, "field": field})


def _load_workflow_definition(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root).resolve() if repo_root is not None else None
    if root is not None and not (root / workflow_contract.CONTRACT_ARTIFACT_REL).exists():
        root = None
    return workflow_contract.load_intent_family_v2_workflow_contract(repo_root=root)


def _validate_workflow_definition(*, repo_root: Path | None = None) -> Tuple[Dict[str, Any], Tuple[Dict[str, str], ...]]:
    payload = _load_workflow_definition(repo_root=repo_root)
    validation_root = Path(repo_root).resolve() if repo_root is not None else None
    if validation_root is not None and not (validation_root / workflow_contract.CONTRACT_ARTIFACT_REL).exists():
        validation_root = None
    diagnostics: List[Dict[str, str]] = []
    for issue in workflow_contract.validate_intent_family_v2_workflow_contract_v1(repo_root=validation_root):
        _append(diagnostics, code="invalid_workflow_definition", message=issue, field="workflow_definition")

    forbidden = tuple(payload.get("forbidden_schema_capabilities") or ())
    for key in forbidden:
        if key in payload:
            _append(
                diagnostics,
                code="forbidden_workflow_field",
                message=f"workflow definition may not declare runtime field {key}",
                field=f"workflow_definition.{key}",
            )
    return payload, tuple(diagnostics)


def _stage_for_child_kind(child_kind: str) -> str:
    normalized = _normalize_text(child_kind)
    if normalized == workflow_contract.VALIDATION_CHILD_KIND:
        return "intent_validation"
    if normalized == workflow_contract.REQUIRED_FINAL_CHILD_KIND:
        return "closeout"
    if normalized == "execution_child":
        return "implement"
    raise KeyError(f"unknown intent_family_v2 child kind: {normalized}")


def _detect_cycle(rows_by_uid: Mapping[str, Mapping[str, Any]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()

    def _walk(uid: str) -> bool:
        if uid in visiting:
            return True
        if uid in visited:
            return False
        visiting.add(uid)
        for dep in list(rows_by_uid.get(uid, {}).get("depends_on_uids") or []):
            dep_uid = _normalize_text(dep)
            if dep_uid in rows_by_uid and _walk(dep_uid):
                return True
        visiting.remove(uid)
        visited.add(uid)
        return False

    return any(_walk(uid) for uid in rows_by_uid)


def _family_rows(parent_uid: str, *, repo_root: Path | None = None) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=repo_root)
    if parent_dir is None:
        raise IntentFamilyV2CompileError(({"code": "missing_parent", "message": f"missing parent ledger item {parent_uid}", "field": "parent_uid"},))
    parent_meta = sched._read_json((parent_dir / "meta.json").resolve()) or {}
    child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=repo_root)
    return parent_meta, child_rows


def _ledger_item_exists(uid: str, *, repo_root: Path | None = None) -> bool:
    return sched._resolve_ledger_item_dir_for_uid(uid, repo_root=repo_root) is not None


def compile_intent_family_v2(
    *,
    parent_uid: str,
    run_at_utc: str,
    builder_model: str,
    verifier_model: str,
    max_iterations: int,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or sched._repo_root()).resolve()
    workflow_definition, diagnostics = _validate_workflow_definition(repo_root=root)
    all_diagnostics = list(diagnostics)
    parent_meta, child_rows = _family_rows(parent_uid, repo_root=root)
    parent_title = _normalize_text(parent_meta.get("title")) or parent_uid
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    source_intent_uid = _normalize_text(parent_integration.get("intent_sheet_uid"))
    required_final_child_uid = _normalize_text(parent_integration.get("required_final_child_uid"))
    required_final_child_kind = _normalize_text(parent_integration.get("required_final_child_kind"))
    validation_child_uid = _normalize_text(parent_integration.get("intent_validation_child_uid"))

    if not source_intent_uid:
        _append(all_diagnostics, code="missing_source_intent_uid", message="intent_family_v2 parent requires integration_meta.intent_sheet_uid", field="integration_meta.intent_sheet_uid")
    if not child_rows:
        _append(all_diagnostics, code="missing_children", message="intent_family_v2 family requires at least one child row", field="child_rows")

    rows_by_uid: Dict[str, Dict[str, Any]] = {}
    for row in child_rows:
        uid = _normalize_text(row.get("uid"))
        if not uid:
            _append(all_diagnostics, code="missing_child_uid", message="child row requires uid", field="child_rows.uid")
            continue
        if uid in rows_by_uid:
            _append(all_diagnostics, code="duplicate_child_uid", message=f"duplicate child uid {uid}", field="child_rows.uid")
            continue
        rows_by_uid[uid] = dict(row)

    if rows_by_uid and _detect_cycle(rows_by_uid):
        _append(all_diagnostics, code="dependency_cycle", message="child dependency graph must be acyclic", field="child_rows.depends_on_uids")

    validation_rows = [row for row in child_rows if _normalize_text(row.get("relationship")) == workflow_contract.VALIDATION_CHILD_KIND]
    closeout_rows = [row for row in child_rows if _normalize_text(row.get("relationship")) == workflow_contract.REQUIRED_FINAL_CHILD_KIND]
    execution_rows = [row for row in child_rows if _normalize_text(row.get("relationship")) == "execution_child"]

    if not execution_rows:
        _append(all_diagnostics, code="missing_execution_children", message="intent_family_v2 family requires at least one execution_child", field="child_rows.relationship")
    if len(validation_rows) != 1:
        _append(all_diagnostics, code="invalid_validation_tail", message="intent_family_v2 family requires exactly one intent_validation_suite child", field="child_rows.relationship")
    if len(closeout_rows) != 1:
        _append(all_diagnostics, code="invalid_closeout_tail", message="intent_family_v2 family requires exactly one intent_family_closeout child", field="child_rows.relationship")
    if validation_rows and validation_child_uid and _normalize_text(validation_rows[0].get("uid")) != validation_child_uid:
        _append(all_diagnostics, code="validation_uid_mismatch", message="parent integration_meta.intent_validation_child_uid disagrees with child relationship rows", field="integration_meta.intent_validation_child_uid")
    if closeout_rows and required_final_child_uid and _normalize_text(closeout_rows[0].get("uid")) != required_final_child_uid:
        _append(all_diagnostics, code="required_final_uid_mismatch", message="parent integration_meta.required_final_child_uid disagrees with child relationship rows", field="integration_meta.required_final_child_uid")
    if required_final_child_kind and required_final_child_kind != workflow_contract.REQUIRED_FINAL_CHILD_KIND:
        _append(all_diagnostics, code="illegal_final_child_kind", message="required_final_child_kind must be intent_family_closeout", field="integration_meta.required_final_child_kind")

    legal_child_kinds = {"execution_child", workflow_contract.VALIDATION_CHILD_KIND, workflow_contract.REQUIRED_FINAL_CHILD_KIND}
    for row in child_rows:
        uid = _normalize_text(row.get("uid"))
        relationship = _normalize_text(row.get("relationship"))
        if relationship not in legal_child_kinds:
            _append(all_diagnostics, code="illegal_child_kind", message=f"illegal intent_family_v2 child kind {relationship or '(empty)'} for {uid or 'child'}", field=f"child_rows[{uid}].relationship")
        for dep in list(row.get("depends_on_uids") or []):
            dep_uid = _normalize_text(dep)
            if not dep_uid:
                continue
            if dep_uid in rows_by_uid:
                continue
            if _ledger_item_exists(dep_uid, repo_root=root):
                continue
            _append(
                all_diagnostics,
                code="missing_dependency",
                message=f"child {uid} depends on missing ledger item {dep_uid}",
                field=f"child_rows[{uid}].depends_on_uids",
            )

    if all_diagnostics:
        raise IntentFamilyV2CompileError(tuple(all_diagnostics))

    verification_job_shape = job_shape_contract.canonical_job_shape_name(JOB_SHAPE)
    builder_runtime = {
        "agent_tech": str(sched._infer_agent_tech_from_model(builder_model) or "codex"),
        "model": str(builder_model),
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
        "compiled_execution_mode": "soft_gate",
    }
    verifier_runtime = {
        "agent_tech": str(sched._infer_agent_tech_from_model(verifier_model) or "codex"),
        "model": str(verifier_model),
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
        "compiled_execution_mode": "soft_gate",
    }
    planned_job_ids = {
        _normalize_text(row.get("uid")): sched._job_id_from_ledger_uid(_normalize_text(row.get("uid")), _normalize_text(row.get("title")))
        for row in child_rows
    }
    child_uids = [_normalize_text(row.get("uid")) for row in child_rows if _normalize_text(row.get("uid"))]
    recommended_execution_order = [_normalize_text(row.get("uid")) for row in sorted(child_rows, key=lambda row: (int(row.get("critical_path_rank") or 0), _normalize_text(row.get("uid"))))]

    graph_nodes: List[Dict[str, Any]] = [
        {
            "node_id": source_intent_uid,
            "node_kind": "source_intent",
            "stage": "intent",
            "title": source_intent_uid,
        },
        {
            "node_id": parent_uid,
            "node_kind": "family_parent",
            "stage": "parent",
            "title": parent_title,
        },
    ]
    graph_edges: List[Dict[str, Any]] = [
        {"from_node_id": source_intent_uid, "to_node_id": parent_uid, "edge_kind": "stage_dependency"},
    ]
    job_specs: List[Dict[str, Any]] = []

    for row in child_rows:
        uid = _normalize_text(row.get("uid"))
        title = _normalize_text(row.get("title")) or uid
        relationship = _normalize_text(row.get("relationship"))
        stage = _stage_for_child_kind(relationship)
        depends_on_uids = [_normalize_text(dep) for dep in list(row.get("depends_on_uids") or []) if _normalize_text(dep)]
        graph_nodes.append(
            {
                "node_id": uid,
                "node_kind": relationship,
                "stage": stage,
                "title": title,
                "critical_path_rank": int(row.get("critical_path_rank") or 0),
                "depends_on_uids": list(depends_on_uids),
                "fanout_role": _normalize_text(row.get("fanout_role")),
            }
        )
        if depends_on_uids:
            for dep_uid in depends_on_uids:
                graph_edges.append({"from_node_id": dep_uid, "to_node_id": uid, "edge_kind": "depends_on"})
        else:
            graph_edges.append({"from_node_id": parent_uid, "to_node_id": uid, "edge_kind": "stage_dependency"})

        spec = {
            "job_id": planned_job_ids[uid],
            "job_kind": JOB_KIND,
            "job_shape": verification_job_shape,
            "job_shape_source": "explicit",
            "job_shape_compatibility_mode": False,
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "depends_on": [planned_job_ids[dep_uid] for dep_uid in depends_on_uids if dep_uid in planned_job_ids],
            "display_title": f"{uid.split('-')[-1].lower()} {title[:80]}",
            "prompt": sched._implementation_prompt_for_child(
                uid=uid,
                title=title,
                pis_rel=_normalize_text(row.get("pis_rel")),
                status=_normalize_text(row.get("status")) or "draft",
                repo_root=root,
            ),
            "context_refs": [_normalize_text(row.get("pis_rel"))] if _normalize_text(row.get("pis_rel")) else [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": run_at_utc, "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800, "post_success_commit_tag": True},
            "runtime": dict(builder_runtime),
            "required_context_bundle": "lab_core_v1",
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": int(max_iterations),
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": dict(builder_runtime),
                "verifier_runtime": dict(verifier_runtime),
            },
            "prompt_meta": {
                "generated_from_parent_uid": parent_uid,
                "batch_scheduler": "scheduler_batch_from_parent",
                "execution_family_contract": {
                    "contract_kind": "intent_derived_execution_family_v1",
                    "workflow_kind": str(workflow_definition.get("workflow_kind") or ""),
                    "workflow_contract_version": str(workflow_definition.get("schema_version") or ""),
                    "source_intent_uid": source_intent_uid,
                    "parent_uid": parent_uid,
                    "child_uid": uid,
                    "child_kind": relationship,
                    "critical_path_rank": int(row.get("critical_path_rank") or 0),
                    "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
                    "required_final_child_uid": _normalize_text(closeout_rows[0].get("uid")),
                },
            },
        }
        job_specs.append(spec)

    family_graph = {
        "schema_version": SCHEMA_VERSION,
        "workflow_kind": str(workflow_definition.get("workflow_kind") or ""),
        "parent_uid": parent_uid,
        "source_intent_uid": source_intent_uid,
        "nodes": sorted(graph_nodes, key=lambda row: (_normalize_text(row.get("stage")), _normalize_text(row.get("node_id")))),
        "edges": sorted(graph_edges, key=lambda row: (_normalize_text(row.get("to_node_id")), _normalize_text(row.get("from_node_id")), _normalize_text(row.get("edge_kind")))),
        "recommended_execution_order": list(recommended_execution_order),
        "required_final_child_uid": _normalize_text(closeout_rows[0].get("uid")),
        "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
    }
    family_graph["graph_fingerprint"] = _fingerprint(family_graph)

    result = {
        "schema_version": SCHEMA_VERSION,
        "parent_uid": parent_uid,
        "source_intent_uid": source_intent_uid,
        "workflow_definition_version": str(workflow_definition.get("schema_version") or ""),
        "family_graph": family_graph,
        "job_specs": job_specs,
        "diagnostics": [],
    }
    result["compilation_fingerprint"] = _fingerprint(
        {
            "workflow_definition_version": result["workflow_definition_version"],
            "family_graph": family_graph,
            "job_specs": job_specs,
        }
    )
    return result
