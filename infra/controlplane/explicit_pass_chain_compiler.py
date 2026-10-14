"""Compile intent-derived families into explicit build/improve pass chains."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from infra.controlplane import intent_family_v2_workflow_contract as workflow_contract
from infra.controlplane import job_shape_contract
from infra.controlplane import scheduled_jobs as sched


SCHEMA_VERSION = "explicit_pass_chain_compilation_v1"
WORKFLOW_KIND = "intent_family_v2"
EXECUTION_MODE = "explicit_pass_chain_v1"
IMPLEMENTATION_JOB_KIND = "build_verify_loop"
IMPLEMENTATION_JOB_SHAPE = "verification_loop_job"


class ExplicitPassChainCompileError(ValueError):
    """Raised when a family cannot be compiled into explicit pass-chain jobs."""

    def __init__(self, diagnostics: Sequence[Mapping[str, str]]):
        self.diagnostics = tuple(dict(row) for row in diagnostics)
        summary = "; ".join(f"{row.get('code', 'invalid')}:{row.get('message', '')}" for row in self.diagnostics)
        super().__init__(summary or "explicit pass-chain compilation failed")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _append(diagnostics: List[Dict[str, str]], *, code: str, message: str, field: str = "") -> None:
    diagnostics.append({"code": code, "message": message, "field": field})


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
        raise ExplicitPassChainCompileError(
            ({"code": "missing_parent", "message": f"missing parent ledger item {parent_uid}", "field": "parent_uid"},)
        )
    parent_meta = sched._read_json((parent_dir / "meta.json").resolve()) or {}
    child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=repo_root)
    return parent_meta, child_rows


def _ledger_item_exists(uid: str, *, repo_root: Path | None = None) -> bool:
    return sched._resolve_ledger_item_dir_for_uid(uid, repo_root=repo_root) is not None


def _stage_for_child_kind(child_kind: str) -> str:
    normalized = _normalize_text(child_kind)
    if normalized == workflow_contract.VALIDATION_CHILD_KIND:
        return "intent_validation"
    if normalized == workflow_contract.REQUIRED_FINAL_CHILD_KIND:
        return "closeout"
    if normalized == "execution_child":
        return "implement"
    raise KeyError(f"unknown explicit pass-chain child kind: {normalized}")


def _pass_job_id(base_job_id: str, pass_index: int, total_passes: int) -> str:
    suffix = f"p{pass_index}of{total_passes}"
    raw = f"{base_job_id}_{suffix}"[:79].rstrip("._-")
    return raw if raw else f"{base_job_id}_p{pass_index}"


def _pass_handoff_rel(child_uid: str, pass_index: int) -> str:
    return f"governance/evidence/scheduler_pass_handoffs/{child_uid}__pass_{pass_index}.json"


def _build_or_improve_prompt(
    *,
    uid: str,
    title: str,
    pis_rel: str,
    status: str,
    pass_index: int,
    total_passes: int,
    previous_handoff_rel: str,
    next_handoff_rel: str,
    repo_root: Path,
) -> str:
    base = sched._implementation_prompt_for_child(
        uid=uid,
        title=title,
        pis_rel=pis_rel,
        status=status,
        repo_root=repo_root,
    )
    lines = [
        "",
        "Explicit pass-chain contract:",
        f"- pass_kind: build_or_improve",
        f"- pass_index: {pass_index}",
        f"- total_passes: {total_passes}",
        "- this pass must inspect the current implementation state and improve it",
        "- do not act as a verifier gate or block family progress on negative findings",
        "- assume the target may already exist and can still be improved",
        "- focus first on files changed by the previous pass and directly connected interfaces/contracts",
        "- widen scope only when required to complete the bound PIS honestly",
        "",
        "Required outputs for this pass:",
        f"- write a handoff artifact at `{next_handoff_rel}`",
        "- the handoff artifact must be valid JSON with these keys:",
        "  - commit_sha",
        "  - changed_files",
        "  - implementation_summary",
        "  - known_followups",
        "  - contracts_touched",
        "  - tests_run",
        "  - remaining_risks",
        "- run focused validation that matches the actual risk changed by this pass",
        "- leave the repo in a successful implemented state for the next optional pass",
        "",
    ]
    if pass_index == 1:
        lines.extend(
            [
                "Pass-specific focus:",
                "- if nothing meaningful exists yet, build the first complete implementation slice",
                "- if something already exists, improve the highest-value missing or weak area first",
            ]
        )
    else:
        lines.extend(
            [
                "Pass-specific focus:",
                f"- read the previous pass handoff artifact at `{previous_handoff_rel}` before changing code",
                "- treat the previous pass as successful but improvable",
                "- prioritize explicit known_followups, remaining_risks, and contract/edge-case weaknesses",
            ]
        )
    return (base.rstrip() + "\n" + "\n".join(lines)).strip()


def _validation_prompt(
    *,
    uid: str,
    title: str,
    pis_rel: str,
    status: str,
    repo_root: Path,
) -> str:
    base = sched._implementation_prompt_for_child(uid=uid, title=title, pis_rel=pis_rel, status=status, repo_root=repo_root)
    extra = """

Explicit pass-chain contract:
- job_type: intent_e2e_validate
- this is an explicit end-to-end validation job for the bound intent-derived child
- inspect the implemented family state from the current repo, run the strongest honest intent-level validation slice, and repair bounded validation gaps when they are cheap and clearly in scope
- do not invent new family structure or hidden workflow steps
- include a parseable `Validation Commands Run:` section with concrete commands and outcomes
""".strip()
    return f"{base.rstrip()}\n\n{extra}".strip()


def _family_closeout_prompt(
    *,
    uid: str,
    title: str,
    pis_rel: str,
    status: str,
    parent_uid: str,
    source_intent_uid: str,
    child_uids: Sequence[str],
    repo_root: Path,
) -> str:
    base = sched._implementation_prompt_for_child(uid=uid, title=title, pis_rel=pis_rel, status=status, repo_root=repo_root)
    extra_lines = [
        "",
        "Explicit pass-chain contract:",
        "- job_type: family_closeout",
        "- this is the single family closeout job; do not create follow-on closeout passes",
        "- assess the family, repair bounded remaining issues when possible, rerun the smallest honest current-state proof slice if you changed anything, and then close the family",
        "- if residual gaps remain, close the family with explicit gaps instead of leaving it hanging",
        f"- parent_uid: {parent_uid}",
        f"- source_intent_uid: {source_intent_uid or '(none)'}",
        f"- family_child_uids: {', '.join(str(uid).strip() for uid in child_uids if str(uid).strip()) or '(none)'}",
    ]
    return (base.rstrip() + "\n" + "\n".join(extra_lines)).strip()


def compile_explicit_pass_chain(
    *,
    parent_uid: str,
    run_at_utc: str,
    builder_model: str,
    verifier_model: str,
    pass_count: int,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or sched._repo_root()).resolve()
    all_diagnostics: List[Dict[str, str]] = []
    parent_meta, child_rows = _family_rows(parent_uid, repo_root=root)
    parent_title = _normalize_text(parent_meta.get("title")) or parent_uid
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    source_intent_uid = _normalize_text(parent_integration.get("intent_sheet_uid"))
    required_final_child_uid = _normalize_text(parent_integration.get("required_final_child_uid"))
    required_final_child_kind = _normalize_text(parent_integration.get("required_final_child_kind"))
    validation_child_uid = _normalize_text(parent_integration.get("intent_validation_child_uid"))

    if not source_intent_uid:
        _append(all_diagnostics, code="missing_source_intent_uid", message="explicit pass-chain parent requires integration_meta.intent_sheet_uid", field="integration_meta.intent_sheet_uid")
    if not child_rows:
        _append(all_diagnostics, code="missing_children", message="explicit pass-chain family requires at least one child row", field="child_rows")

    rows_by_uid: Dict[str, Dict[str, Any]] = {}
    for row in child_rows:
        uid = _normalize_text(row.get("uid"))
        if not uid:
            _append(all_diagnostics, code="missing_child_uid", message="child row requires uid", field="child_rows.uid")
            continue
        rows_by_uid[uid] = dict(row)

    if rows_by_uid and _detect_cycle(rows_by_uid):
        _append(all_diagnostics, code="dependency_cycle", message="child dependency graph must be acyclic", field="child_rows.depends_on_uids")

    validation_rows = [row for row in child_rows if _normalize_text(row.get("relationship")) == workflow_contract.VALIDATION_CHILD_KIND]
    closeout_rows = [row for row in child_rows if _normalize_text(row.get("relationship")) == workflow_contract.REQUIRED_FINAL_CHILD_KIND]
    execution_rows = [row for row in child_rows if _normalize_text(row.get("relationship")) == "execution_child"]
    if not execution_rows:
        _append(all_diagnostics, code="missing_execution_children", message="explicit pass-chain family requires at least one execution_child", field="child_rows.relationship")
    if len(validation_rows) != 1:
        _append(all_diagnostics, code="invalid_validation_tail", message="explicit pass-chain family requires exactly one intent_validation_suite child", field="child_rows.relationship")
    if len(closeout_rows) != 1:
        _append(all_diagnostics, code="invalid_closeout_tail", message="explicit pass-chain family requires exactly one intent_family_closeout child", field="child_rows.relationship")
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
            _append(all_diagnostics, code="illegal_child_kind", message=f"illegal child kind {relationship or '(empty)'} for {uid or 'child'}", field=f"child_rows[{uid}].relationship")
        for dep in list(row.get("depends_on_uids") or []):
            dep_uid = _normalize_text(dep)
            if not dep_uid:
                continue
            if dep_uid in rows_by_uid or _ledger_item_exists(dep_uid, repo_root=root):
                continue
            _append(all_diagnostics, code="missing_dependency", message=f"child {uid} depends on missing ledger item {dep_uid}", field=f"child_rows[{uid}].depends_on_uids")

    if all_diagnostics:
        raise ExplicitPassChainCompileError(tuple(all_diagnostics))

    verification_job_shape = job_shape_contract.canonical_job_shape_name(IMPLEMENTATION_JOB_SHAPE)
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
    pass_count = max(1, min(6, int(pass_count or 3)))
    base_job_ids = {
        _normalize_text(row.get("uid")): sched._job_id_from_ledger_uid(_normalize_text(row.get("uid")), _normalize_text(row.get("title")))
        for row in child_rows
    }
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
    graph_edges: List[Dict[str, Any]] = [{"from_node_id": source_intent_uid, "to_node_id": parent_uid, "edge_kind": "stage_dependency"}]
    job_specs: List[Dict[str, Any]] = []
    final_job_ids_by_uid: Dict[str, str] = {}
    pass_job_ids_by_uid: Dict[str, List[str]] = {}

    child_rows_sorted = sorted(
        child_rows,
        key=lambda row: (int(row.get("critical_path_rank") or 0), _normalize_text(row.get("uid"))),
    )
    recommended_execution_order = [_normalize_text(row.get("uid")) for row in child_rows_sorted if _normalize_text(row.get("uid"))]

    prior_execution_uid = ""
    for row in child_rows_sorted:
        uid = _normalize_text(row.get("uid"))
        title = _normalize_text(row.get("title")) or uid
        relationship = _normalize_text(row.get("relationship"))
        stage = _stage_for_child_kind(relationship)
        depends_on_uids = [_normalize_text(dep) for dep in list(row.get("depends_on_uids") or []) if _normalize_text(dep)]
        if relationship == "execution_child" and prior_execution_uid and prior_execution_uid not in depends_on_uids:
            depends_on_uids.append(prior_execution_uid)
        graph_nodes.append(
            {
                "node_id": uid,
                "node_kind": relationship,
                "stage": stage,
                "title": title,
                "critical_path_rank": int(row.get("critical_path_rank") or 0),
                "depends_on_uids": list(depends_on_uids),
                "execution_mode": EXECUTION_MODE,
            }
        )
        if depends_on_uids:
            for dep_uid in depends_on_uids:
                graph_edges.append({"from_node_id": dep_uid, "to_node_id": uid, "edge_kind": "depends_on"})
        else:
            graph_edges.append({"from_node_id": parent_uid, "to_node_id": uid, "edge_kind": "stage_dependency"})

        if relationship == "execution_child":
            prior_job_id = ""
            pass_job_ids: List[str] = []
            for pass_index in range(1, pass_count + 1):
                job_id = _pass_job_id(base_job_ids[uid], pass_index, pass_count)
                depends_on_jobs = [final_job_ids_by_uid[dep_uid] for dep_uid in depends_on_uids if dep_uid in final_job_ids_by_uid]
                if prior_job_id:
                    depends_on_jobs.append(prior_job_id)
                previous_handoff_rel = _pass_handoff_rel(uid, pass_index - 1) if pass_index > 1 else ""
                next_handoff_rel = _pass_handoff_rel(uid, pass_index)
                prompt_meta = {
                    "generated_from_parent_uid": parent_uid,
                    "batch_scheduler": "scheduler_batch_from_parent",
                    "execution_family_contract": {
                        "contract_kind": "intent_derived_execution_family_v1",
                        "workflow_kind": WORKFLOW_KIND,
                        "execution_mode": EXECUTION_MODE,
                        "source_intent_uid": source_intent_uid,
                        "parent_uid": parent_uid,
                        "child_uid": uid,
                        "child_kind": relationship,
                        "critical_path_rank": int(row.get("critical_path_rank") or 0),
                        "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
                        "required_final_child_uid": _normalize_text(closeout_rows[0].get("uid")),
                    },
                    "explicit_pass_chain": {
                        "job_type": "build_or_improve",
                        "pass_index": pass_index,
                        "pass_count": pass_count,
                        "pass_handoff_artifact_rel": next_handoff_rel,
                        "previous_pass_handoff_artifact_rel": previous_handoff_rel,
                        "projection_for_child_uid": uid if pass_index == pass_count else "",
                        "projection_job_ids": [],
                    },
                }
                spec = {
                    "job_id": job_id,
                    "job_kind": IMPLEMENTATION_JOB_KIND,
                    "job_shape": verification_job_shape,
                    "job_shape_source": "explicit",
                    "job_shape_compatibility_mode": False,
                    "enabled": True,
                    "binding": {"type": "ledger_uid", "id": uid},
                    "depends_on": depends_on_jobs,
                    "display_title": f"{uid.split('-')[-1].lower()} pass {pass_index}/{pass_count} {title[:64]}",
                    "prompt": _build_or_improve_prompt(
                        uid=uid,
                        title=title,
                        pis_rel=_normalize_text(row.get("pis_rel")),
                        status=_normalize_text(row.get("status")) or "draft",
                        pass_index=pass_index,
                        total_passes=pass_count,
                        previous_handoff_rel=previous_handoff_rel,
                        next_handoff_rel=next_handoff_rel,
                        repo_root=root,
                    ),
                    "context_refs": [_normalize_text(row.get("pis_rel"))] if _normalize_text(row.get("pis_rel")) else [],
                    "expected_output": {"kind": "report", "format": "markdown"},
                    "schedule": {"mode": "once", "run_at": run_at_utc, "timezone": "UTC"},
                    "policy": {
                        "max_runtime_seconds": 1800,
                        "post_success_commit_tag": True,
                    },
                    "runtime": dict(builder_runtime),
                    "required_context_bundle": "lab_core_v1",
                    "loop": {
                        "builder_role": "builder",
                        "builder_only": True,
                        "max_iterations": 1,
                        "pass_marker": "VERDICT: PASS",
                        "builder_runtime": dict(builder_runtime),
                        "verifier_runtime": dict(verifier_runtime),
                    },
                    "prompt_meta": prompt_meta,
                }
                job_specs.append(spec)
                pass_job_ids.append(job_id)
                prior_job_id = job_id
            pass_job_ids_by_uid[uid] = list(pass_job_ids)
            final_job_ids_by_uid[uid] = pass_job_ids[-1]
            prior_execution_uid = uid
            continue

        depends_on_jobs = [final_job_ids_by_uid[dep_uid] for dep_uid in depends_on_uids if dep_uid in final_job_ids_by_uid]
        if relationship == workflow_contract.VALIDATION_CHILD_KIND:
            job_id = base_job_ids[uid]
            spec = {
                "job_id": job_id,
                "job_kind": IMPLEMENTATION_JOB_KIND,
                "job_shape": verification_job_shape,
                "job_shape_source": "explicit",
                "job_shape_compatibility_mode": False,
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": uid},
                "depends_on": depends_on_jobs,
                "display_title": f"{uid.split('-')[-1].lower()} validate {title[:70]}",
                "prompt": _validation_prompt(
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
                    "builder_only": True,
                    "max_iterations": 1,
                    "pass_marker": "VERDICT: PASS",
                    "builder_runtime": dict(builder_runtime),
                    "verifier_runtime": dict(verifier_runtime),
                },
                "prompt_meta": {
                    "generated_from_parent_uid": parent_uid,
                    "batch_scheduler": "scheduler_batch_from_parent",
                    "execution_family_contract": {
                        "contract_kind": "intent_derived_execution_family_v1",
                        "workflow_kind": WORKFLOW_KIND,
                        "execution_mode": EXECUTION_MODE,
                        "source_intent_uid": source_intent_uid,
                        "parent_uid": parent_uid,
                        "child_uid": uid,
                        "child_kind": relationship,
                        "critical_path_rank": int(row.get("critical_path_rank") or 0),
                        "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
                        "required_final_child_uid": _normalize_text(closeout_rows[0].get("uid")),
                    },
                    "explicit_pass_chain": {
                        "job_type": "intent_e2e_validate",
                        "projection_for_child_uid": uid,
                        "projection_job_ids": [job_id],
                    },
                },
            }
            job_specs.append(spec)
            final_job_ids_by_uid[uid] = job_id
            pass_job_ids_by_uid[uid] = [job_id]
            continue

        job_id = base_job_ids[uid]
        spec = {
            "job_id": job_id,
            "job_kind": IMPLEMENTATION_JOB_KIND,
            "job_shape": verification_job_shape,
            "job_shape_source": "explicit",
            "job_shape_compatibility_mode": False,
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "depends_on": depends_on_jobs,
            "display_title": f"{uid.split('-')[-1].lower()} close {title[:72]}",
            "prompt": _family_closeout_prompt(
                uid=uid,
                title=title,
                pis_rel=_normalize_text(row.get("pis_rel")),
                status=_normalize_text(row.get("status")) or "draft",
                parent_uid=parent_uid,
                source_intent_uid=source_intent_uid,
                child_uids=[_normalize_text(item.get("uid")) for item in child_rows if _normalize_text(item.get("uid"))],
                repo_root=root,
            ),
            "context_refs": [_normalize_text(row.get("pis_rel"))] if _normalize_text(row.get("pis_rel")) else [],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": run_at_utc, "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 1800,
                "post_success_commit_tag": True,
                "delivery_policy": "delivery_guaranteed",
            },
            "runtime": dict(builder_runtime),
            "required_context_bundle": "lab_core_v1",
            "loop": {
                "builder_role": "builder",
                "builder_only": True,
                "max_iterations": 1,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": dict(builder_runtime),
                "verifier_runtime": dict(verifier_runtime),
            },
            "prompt_meta": {
                "generated_from_parent_uid": parent_uid,
                "batch_scheduler": "scheduler_batch_from_parent",
                "execution_family_contract": {
                    "contract_kind": "intent_derived_execution_family_v1",
                    "workflow_kind": WORKFLOW_KIND,
                    "execution_mode": EXECUTION_MODE,
                    "source_intent_uid": source_intent_uid,
                    "parent_uid": parent_uid,
                    "child_uid": uid,
                    "child_kind": relationship,
                    "critical_path_rank": int(row.get("critical_path_rank") or 0),
                    "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
                    "required_final_child_uid": _normalize_text(closeout_rows[0].get("uid")),
                },
                "explicit_pass_chain": {
                    "job_type": "family_closeout",
                    "projection_for_child_uid": uid,
                    "projection_job_ids": [job_id],
                },
            },
        }
        job_specs.append(spec)
        final_job_ids_by_uid[uid] = job_id
        pass_job_ids_by_uid[uid] = [job_id]

    for spec in job_specs:
        prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
        explicit_meta = prompt_meta.get("explicit_pass_chain") if isinstance(prompt_meta.get("explicit_pass_chain"), dict) else {}
        projection_uid = _normalize_text(explicit_meta.get("projection_for_child_uid"))
        if projection_uid and projection_uid in pass_job_ids_by_uid:
            explicit_meta["projection_job_ids"] = list(pass_job_ids_by_uid[projection_uid])
            prompt_meta["explicit_pass_chain"] = explicit_meta
            spec["prompt_meta"] = prompt_meta

    family_graph = {
        "schema_version": SCHEMA_VERSION,
        "workflow_kind": WORKFLOW_KIND,
        "execution_mode": EXECUTION_MODE,
        "parent_uid": parent_uid,
        "source_intent_uid": source_intent_uid,
        "nodes": sorted(graph_nodes, key=lambda row: (_normalize_text(row.get("stage")), _normalize_text(row.get("node_id")))),
        "edges": sorted(graph_edges, key=lambda row: (_normalize_text(row.get("to_node_id")), _normalize_text(row.get("from_node_id")), _normalize_text(row.get("edge_kind")))),
        "recommended_execution_order": list(recommended_execution_order),
        "required_final_child_uid": _normalize_text(closeout_rows[0].get("uid")),
        "required_final_child_kind": workflow_contract.REQUIRED_FINAL_CHILD_KIND,
        "pass_count": pass_count,
    }
    family_graph["graph_fingerprint"] = _fingerprint(family_graph)
    result = {
        "schema_version": SCHEMA_VERSION,
        "parent_uid": parent_uid,
        "source_intent_uid": source_intent_uid,
        "workflow_definition_version": workflow_contract.SCHEMA_VERSION,
        "workflow_kind": WORKFLOW_KIND,
        "execution_mode": EXECUTION_MODE,
        "family_graph": family_graph,
        "job_specs": job_specs,
        "diagnostics": [],
    }
    result["compilation_fingerprint"] = _fingerprint(
        {
            "workflow_kind": result["workflow_kind"],
            "execution_mode": result["execution_mode"],
            "family_graph": family_graph,
            "job_specs": job_specs,
        }
    )
    return result
