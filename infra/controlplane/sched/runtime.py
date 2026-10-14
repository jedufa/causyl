from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from infra.controlplane import explicit_pass_chain_compiler as explicit_compiler
from infra.controlplane import intent_family_v2_compiler as compiler
from infra.controlplane import intent_family_v2_projection as projection
from infra.controlplane import scheduled_jobs as legacy_sched
from infra.controlplane import v2_continuity

from .ownership import load_ownership, ownership_receipt, assert_v2_can_evaluate_family
from .schema import (
    SCHEMA_VERSION,
    SUPPORTED_WORKFLOW_KIND,
    AdmissionDecision,
    SchedulerEvaluation,
    SchedulerPlan,
    V2Job,
    gate_for_node_kind,
)


SUCCESS_STATUSES = frozenset(
    {
        "ok",
        "conditional_pass_repair_applied",
        "executed_with_closeout_integrity_defect",
        "delivered_unverified",
        "delivered_with_findings",
        "delivered_builder_only",
        "succeeded",
        "verified",
    }
)
FAILED_STATUSES = frozenset({"failed", "failed_verification", "proof_gap", "error", "cancelled", "interrupted"})
TERMINAL_SUCCESS_RUNTIME_STATES = frozenset({"succeeded", "completed", "validated", "closed"})


def _repo_root(repo_root: Path | None = None) -> Path:
    return Path(repo_root or legacy_sched._repo_root()).resolve()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _job_specs_by_child_uid(compilation: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    out: dict[str, Mapping[str, Any]] = {}
    for spec in list(compilation.get("job_specs") or []):
        if not isinstance(spec, Mapping):
            continue
        binding = spec.get("binding") if isinstance(spec.get("binding"), Mapping) else {}
        child_uid = _normalize_text(binding.get("id"))
        if child_uid:
            out[child_uid] = spec
    return out


def _projection_job_ids_for_spec(spec: Mapping[str, Any]) -> tuple[str, ...]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), Mapping) else {}
    explicit = prompt_meta.get("explicit_pass_chain") if isinstance(prompt_meta.get("explicit_pass_chain"), Mapping) else {}
    projection_job_ids = tuple(
        _normalize_text(item)
        for item in list(explicit.get("projection_job_ids") or [])
        if _normalize_text(item)
    )
    if projection_job_ids:
        return projection_job_ids
    job_id = _normalize_text(spec.get("job_id"))
    return (job_id,) if job_id else ()


def _dispatch_job_id_for_spec(spec: Mapping[str, Any]) -> str:
    projection_job_ids = _projection_job_ids_for_spec(spec)
    if projection_job_ids:
        return projection_job_ids[0]
    return _normalize_text(spec.get("job_id"))


def _explicit_pass_chain_settings_for_parent(*, parent_uid: str, repo_root: Path) -> dict[str, Any]:
    jobs_root = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox"
    pass_count = 0
    builder_model = ""
    verifier_model = ""
    for path in sorted(jobs_root.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(spec, Mapping):
            continue
        prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), Mapping) else {}
        family_contract = (
            prompt_meta.get("execution_family_contract")
            if isinstance(prompt_meta.get("execution_family_contract"), Mapping)
            else {}
        )
        if _normalize_text(family_contract.get("parent_uid")) != parent_uid:
            continue
        explicit = (
            prompt_meta.get("explicit_pass_chain")
            if isinstance(prompt_meta.get("explicit_pass_chain"), Mapping)
            else {}
        )
        pass_count = max(pass_count, int(explicit.get("pass_count") or 0))
        runtime = spec.get("runtime") if isinstance(spec.get("runtime"), Mapping) else {}
        loop = spec.get("loop") if isinstance(spec.get("loop"), Mapping) else {}
        builder_runtime = loop.get("builder_runtime") if isinstance(loop.get("builder_runtime"), Mapping) else {}
        verifier_runtime = loop.get("verifier_runtime") if isinstance(loop.get("verifier_runtime"), Mapping) else {}
        if not builder_model:
            builder_model = _normalize_text(builder_runtime.get("model") or runtime.get("model"))
        if not verifier_model:
            verifier_model = _normalize_text(verifier_runtime.get("model"))
    return {
        "pass_count": pass_count,
        "builder_model": builder_model,
        "verifier_model": verifier_model,
    }


def _scheduled_job_ids_by_binding_uid(*, repo_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    explicit_terminals: dict[str, tuple[int, str]] = {}
    jobs_root = repo_root / "governance" / "agents" / "runtime" / "scheduled_jobs" / "inbox"
    for path in sorted(jobs_root.glob("*.json")):
        try:
            spec = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(spec, Mapping):
            continue
        binding = spec.get("binding") if isinstance(spec.get("binding"), Mapping) else {}
        binding_uid = _normalize_text(binding.get("id") or binding.get("ledger_uid") or binding.get("uid"))
        job_id = _normalize_text(spec.get("job_id"))
        if binding_uid and job_id:
            out.setdefault(binding_uid, job_id)
            prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), Mapping) else {}
            explicit = (
                prompt_meta.get("explicit_pass_chain")
                if isinstance(prompt_meta.get("explicit_pass_chain"), Mapping)
                else {}
            )
            projection_uid = _normalize_text(explicit.get("projection_for_child_uid"))
            if projection_uid == binding_uid:
                pass_index = int(explicit.get("pass_index") or explicit.get("pass_count") or 0)
                current = explicit_terminals.get(binding_uid)
                if current is None or pass_index >= current[0]:
                    explicit_terminals[binding_uid] = (pass_index, job_id)
    for binding_uid, (_, job_id) in explicit_terminals.items():
        out[binding_uid] = job_id
    return out


def _historical_success_job_ids_by_child_uid(compilation: Mapping[str, Any], *, repo_root: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    for node in _child_nodes(compilation):
        child_uid = _normalize_text(node.get("node_id"))
        title = _normalize_text(node.get("title"))
        if not child_uid or not title:
            continue
        job_id = legacy_sched._job_id_from_ledger_uid(child_uid, title)
        state = legacy_sched.load_state(job_id, repo_root=repo_root)
        if _normalize_text(state.get("last_status")) in SUCCESS_STATUSES:
            out[child_uid] = job_id
    return out


def _child_nodes(compilation: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    family_graph = compilation.get("family_graph") if isinstance(compilation.get("family_graph"), Mapping) else {}
    order = [_normalize_text(item) for item in list(family_graph.get("recommended_execution_order") or []) if _normalize_text(item)]
    order_index = {uid: index for index, uid in enumerate(order)}
    rows = []
    for row in list(family_graph.get("nodes") or []):
        if not isinstance(row, Mapping):
            continue
        if _normalize_text(row.get("node_kind")) in {"execution_child", "intent_validation_suite", "intent_family_closeout"}:
            rows.append(row)
    rows.sort(key=lambda row: (order_index.get(_normalize_text(row.get("node_id")), 9999), _normalize_text(row.get("node_id"))))
    return rows


def compile_scheduler_plan(
    *,
    parent_uid: str,
    run_at_utc: str,
    builder_model: str = "gpt-5.4-mini",
    verifier_model: str = "gpt-5.4",
    max_iterations: int = 3,
    repo_root: Path | None = None,
) -> SchedulerPlan:
    root = _repo_root(repo_root)
    parent_dir = legacy_sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=root)
    parent_meta = legacy_sched._read_json((parent_dir / "meta.json").resolve()) if parent_dir is not None else {}
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), Mapping) else {}
    execution_mode = _normalize_text(parent_integration.get("execution_mode")).lower()
    if execution_mode in {"explicit_pass_chain_v1", "explicit_pass_chain", "build_or_improve_chain"}:
        live_settings = _explicit_pass_chain_settings_for_parent(parent_uid=parent_uid, repo_root=root)
        effective_pass_count = int(live_settings.get("pass_count") or max_iterations or 4)
        effective_builder_model = _normalize_text(live_settings.get("builder_model")) or builder_model
        effective_verifier_model = _normalize_text(live_settings.get("verifier_model")) or verifier_model
        compiled = explicit_compiler.compile_explicit_pass_chain(
            parent_uid=parent_uid,
            run_at_utc=run_at_utc,
            builder_model=effective_builder_model,
            verifier_model=effective_verifier_model,
            pass_count=effective_pass_count,
            repo_root=root,
        )
    else:
        compiled = compiler.compile_intent_family_v2(
            parent_uid=parent_uid,
            run_at_utc=run_at_utc,
            builder_model=builder_model,
            verifier_model=verifier_model,
            max_iterations=max_iterations,
            repo_root=root,
        )
    family_graph = compiled.get("family_graph") if isinstance(compiled.get("family_graph"), Mapping) else {}
    workflow_kind = _normalize_text(compiled.get("workflow_kind") or family_graph.get("workflow_kind")) or SUPPORTED_WORKFLOW_KIND
    if workflow_kind != SUPPORTED_WORKFLOW_KIND:
        raise ValueError(f"sched v2 only supports {SUPPORTED_WORKFLOW_KIND}, got {workflow_kind}")

    specs_by_uid = _job_specs_by_child_uid(compiled)
    use_historical_success_fallback = execution_mode not in {"explicit_pass_chain_v1", "explicit_pass_chain", "build_or_improve_chain"}
    historical_success_job_ids = (
        _historical_success_job_ids_by_child_uid(compiled, repo_root=root)
        if use_historical_success_fallback
        else {}
    )
    pending_jobs = []
    for node in _child_nodes(compiled):
        child_uid = _normalize_text(node.get("node_id"))
        spec = specs_by_uid.get(child_uid, {})
        node_kind = _normalize_text(node.get("node_kind"))
        projection_job_ids = tuple(_projection_job_ids_for_spec(spec))
        historical_success_job_id = _normalize_text(historical_success_job_ids.get(child_uid))
        if historical_success_job_id and historical_success_job_id not in projection_job_ids:
            projection_job_ids = projection_job_ids + (historical_success_job_id,)
        pending_jobs.append(
            {
                "job_id": _normalize_text(spec.get("job_id")),
                "dispatch_job_id": _dispatch_job_id_for_spec(spec),
                "projection_job_ids": projection_job_ids,
                "child_uid": child_uid,
                "node_kind": node_kind,
                "stage": _normalize_text(node.get("stage")),
                "title": _normalize_text(node.get("title")) or child_uid,
                "depends_on_uids": tuple(
                    _normalize_text(item) for item in list(node.get("depends_on_uids") or []) if _normalize_text(item)
                ),
                "gate": gate_for_node_kind(node_kind),
            }
        )
    job_id_by_uid = {row["child_uid"]: row["job_id"] for row in pending_jobs if row["child_uid"] and row["job_id"]}
    job_id_by_uid.update({uid: job_id for uid, job_id in _scheduled_job_ids_by_binding_uid(repo_root=root).items() if uid and job_id})
    job_id_by_uid.update(historical_success_job_ids)
    jobs = tuple(
        V2Job(
            job_id=row["job_id"],
            dispatch_job_id=row["dispatch_job_id"],
            child_uid=row["child_uid"],
            node_kind=row["node_kind"],
            stage=row["stage"],
            title=row["title"],
            depends_on_uids=row["depends_on_uids"],
            depends_on_job_ids=tuple(job_id_by_uid[uid] for uid in row["depends_on_uids"] if uid in job_id_by_uid),
            projection_job_ids=tuple(row["projection_job_ids"]),
            gate=row["gate"],
        )
        for row in pending_jobs
    )
    return SchedulerPlan(
        schema_version=SCHEMA_VERSION,
        parent_uid=_normalize_text(compiled.get("parent_uid")),
        source_intent_uid=_normalize_text(compiled.get("source_intent_uid")),
        workflow_kind=workflow_kind,
        compilation_fingerprint=_normalize_text(compiled.get("compilation_fingerprint")),
        jobs=jobs,
        raw_compilation=compiled,
    )


def _runtime_state_from_legacy_state(state: Mapping[str, Any]) -> str:
    shape_state = _normalize_text(state.get("last_shape_canonical_state"))
    if _normalize_text(state.get("active_run_id")) and shape_state in projection.ACTIVE_RUNTIME_STATES:
        return shape_state
    if _normalize_text(state.get("active_run_id")):
        return "running"
    if shape_state:
        return shape_state
    last_status = _normalize_text(state.get("last_status"))
    if last_status in SUCCESS_STATUSES:
        return "succeeded"
    if last_status in FAILED_STATUSES:
        return last_status
    return last_status


def _collect_explicit_pass_chain_runtime(
    *,
    child_uid: str,
    terminal_job_id: str,
    projection_job_ids: Sequence[str],
    repo_root: Path,
) -> dict[str, Any]:
    candidate_job_ids = tuple(_normalize_text(item) for item in projection_job_ids if _normalize_text(item))
    selected_job_id = terminal_job_id
    dispatch_job_id = candidate_job_ids[0] if candidate_job_ids else terminal_job_id
    selected_state: Mapping[str, Any] = {}
    selected_runtime_state = ""

    states_by_job_id: dict[str, Mapping[str, Any]] = {}
    runtime_by_job_id: dict[str, str] = {}
    for candidate in candidate_job_ids:
        try:
            state = legacy_sched.load_state(candidate, repo_root=repo_root)
        except FileNotFoundError:
            continue
        states_by_job_id[candidate] = state
        runtime_by_job_id[candidate] = _runtime_state_from_legacy_state(state)

    for candidate in candidate_job_ids:
        state = states_by_job_id.get(candidate)
        runtime_state = runtime_by_job_id.get(candidate, "")
        if not state:
            dispatch_job_id = candidate
            break
        if bool(_normalize_text(state.get("active_run_id"))):
            selected_job_id = candidate
            dispatch_job_id = candidate
            selected_state = state
            selected_runtime_state = runtime_state
            break
        if runtime_state in FAILED_STATUSES:
            selected_job_id = candidate
            dispatch_job_id = candidate
            selected_state = state
            selected_runtime_state = runtime_state
            break
        if runtime_state in TERMINAL_SUCCESS_RUNTIME_STATES:
            selected_job_id = candidate
            selected_state = state
            selected_runtime_state = runtime_state
            dispatch_job_id = candidate
            continue
        selected_job_id = candidate
        dispatch_job_id = candidate
        selected_state = state
        selected_runtime_state = runtime_state
        break
    else:
        if candidate_job_ids:
            last_candidate = candidate_job_ids[-1]
            last_state = states_by_job_id.get(last_candidate)
            last_runtime_state = runtime_by_job_id.get(last_candidate, "")
            if last_state and last_runtime_state in TERMINAL_SUCCESS_RUNTIME_STATES:
                selected_job_id = last_candidate
                dispatch_job_id = last_candidate
                selected_state = last_state
                selected_runtime_state = last_runtime_state

    return {
        "child_uid": child_uid,
        "job_id": selected_job_id,
        "dispatch_job_id": dispatch_job_id,
        "runtime_state": selected_runtime_state,
        "active": bool(_normalize_text(selected_state.get("active_run_id"))),
        "active_run_id": _normalize_text(selected_state.get("active_run_id")),
        "retired": selected_runtime_state in TERMINAL_SUCCESS_RUNTIME_STATES and selected_job_id == (candidate_job_ids[-1] if candidate_job_ids else selected_job_id),
        "last_status": _normalize_text(selected_state.get("last_status")),
        "last_receipt_path": _normalize_text(selected_state.get("last_receipt_path")),
        "projection_job_ids": list(candidate_job_ids),
    }


def collect_current_runtime_records(plan: SchedulerPlan, *, repo_root: Path | None = None) -> tuple[Mapping[str, Any], ...]:
    root = _repo_root(repo_root)
    out = []
    seen_child_uids: set[str] = set()

    def append_runtime(*, child_uid: str, job_id: str, projection_job_ids: Sequence[str] = ()) -> None:
        if child_uid in seen_child_uids:
            return
        seen_child_uids.add(child_uid)
        normalized_projection_job_ids = tuple(_normalize_text(item) for item in projection_job_ids if _normalize_text(item))
        if len(normalized_projection_job_ids) > 1:
            out.append(
                _collect_explicit_pass_chain_runtime(
                    child_uid=child_uid,
                    terminal_job_id=_normalize_text(job_id),
                    projection_job_ids=normalized_projection_job_ids,
                    repo_root=root,
                )
            )
            return
        candidate_job_ids = normalized_projection_job_ids or ((_normalize_text(job_id),) if _normalize_text(job_id) else ())
        selected_job_id = _normalize_text(job_id)
        state = {}
        runtime_state = ""
        for candidate in reversed(candidate_job_ids):
            try:
                candidate_state = legacy_sched.load_state(candidate, repo_root=root)
            except FileNotFoundError:
                continue
            candidate_runtime_state = _runtime_state_from_legacy_state(candidate_state)
            if not state:
                state = candidate_state
                runtime_state = candidate_runtime_state
                selected_job_id = candidate
            if bool(_normalize_text(candidate_state.get("active_run_id"))):
                state = candidate_state
                runtime_state = candidate_runtime_state
                selected_job_id = candidate
                break
            if candidate_runtime_state in TERMINAL_SUCCESS_RUNTIME_STATES:
                state = candidate_state
                runtime_state = candidate_runtime_state
                selected_job_id = candidate
                break
            if candidate_runtime_state in FAILED_STATUSES:
                state = candidate_state
                runtime_state = candidate_runtime_state
                selected_job_id = candidate
                break
        out.append(
            {
                "child_uid": child_uid,
                "job_id": selected_job_id,
                "dispatch_job_id": selected_job_id,
                "runtime_state": runtime_state,
                "active": bool(_normalize_text(state.get("active_run_id"))),
                "active_run_id": _normalize_text(state.get("active_run_id")),
                "retired": runtime_state in TERMINAL_SUCCESS_RUNTIME_STATES,
                "last_status": _normalize_text(state.get("last_status")),
                "last_receipt_path": _normalize_text(state.get("last_receipt_path")),
                "projection_job_ids": list(candidate_job_ids),
            }
        )

    for job in plan.jobs:
        append_runtime(child_uid=job.child_uid, job_id=job.job_id, projection_job_ids=job.projection_job_ids)
    job_ids_by_uid = {
        uid: job_id
        for job in plan.jobs
        for uid, job_id in zip(job.depends_on_uids, job.depends_on_job_ids)
        if uid and job_id
    }
    for child_uid, job_id in job_ids_by_uid.items():
        append_runtime(child_uid=child_uid, job_id=job_id)
    return tuple(out)


def _completed_uids(runtime_records: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        _normalize_text(row.get("child_uid"))
        for row in runtime_records
        if bool(row.get("retired"))
    }


def _admission_decisions(plan: SchedulerPlan, runtime_records: Sequence[Mapping[str, Any]]) -> tuple[AdmissionDecision, ...]:
    completed = _completed_uids(runtime_records)
    runtime_by_uid = {_normalize_text(row.get("child_uid")): row for row in runtime_records}
    ambiguous_by_uid: dict[str, str] = {}
    active_uids: list[str] = []
    for row in runtime_records:
        child_uid = _normalize_text(row.get("child_uid"))
        runtime_state = _normalize_text(row.get("runtime_state"))
        active = bool(row.get("active"))
        if not child_uid:
            continue
        if active and runtime_state in TERMINAL_SUCCESS_RUNTIME_STATES:
            ambiguous_by_uid[child_uid] = "terminal_success_and_active_run"
            continue
        if active or runtime_state in {"running", "queued", "launching"}:
            active_uids.append(child_uid)
    multiple_active_children = len(set(active_uids)) > 1
    out = []
    for job in plan.jobs:
        runtime = runtime_by_uid.get(job.child_uid, {})
        runtime_state = _normalize_text(runtime.get("runtime_state"))
        unmet = tuple(uid for uid in job.depends_on_uids if uid not in completed)
        if job.child_uid in ambiguous_by_uid:
            decision = "blocked"
            reason = f"truth_ambiguous:{ambiguous_by_uid[job.child_uid]}"
        elif multiple_active_children and job.child_uid in set(active_uids):
            decision = "blocked"
            reason = "truth_ambiguous:multiple_active_children"
        elif any(uid in ambiguous_by_uid for uid in job.depends_on_uids):
            decision = "blocked"
            reason = f"dependency_truth_ambiguous:{next(uid for uid in job.depends_on_uids if uid in ambiguous_by_uid)}"
        elif multiple_active_children:
            decision = "blocked"
            reason = "dependency_truth_ambiguous:multiple_active_children"
        elif bool(runtime.get("retired")):
            decision = "retired"
            reason = "terminal_success"
        elif runtime_state in FAILED_STATUSES:
            decision = "blocked"
            reason = runtime_state
        elif bool(runtime.get("active")) or runtime_state in {"running", "queued", "launching"}:
            decision = "active"
            reason = "active_run_present" if bool(runtime.get("active")) else (runtime_state or "active")
        elif unmet:
            decision = "blocked"
            reason = f"dependency_pending:{unmet[0]}"
        else:
            decision = "admit"
            reason = "dependencies_satisfied"
        out.append(
            AdmissionDecision(
                child_uid=job.child_uid,
                job_id=job.job_id,
                dispatch_job_id=_normalize_text(runtime.get("dispatch_job_id")) or job.dispatch_job_id,
                decision=decision,
                reason=reason,
                gate=job.gate,
                unmet_dependencies=unmet,
            )
        )
    return tuple(out)


def _continuity_for_family(canonical: Mapping[str, Any], plan: SchedulerPlan) -> dict[str, str]:
    family_status = _normalize_text(canonical.get("family_status"))
    if family_status in {"queued", "active", "completed"}:
        return {
            "continuity_state": family_status,
            "recovery_outcome": "none",
            "reason": "terminal_family" if family_status == "completed" else "normal_family_progress",
            "decision_source": "canonical_projection",
        }
    continuity_record = {
        "subject_kind": "family",
        "subject_id": plan.parent_uid,
        "workflow_kind": SUPPORTED_WORKFLOW_KIND,
        "runtime_state": family_status,
        "rerun_allowed": True,
        "revalidation_required": family_status in {"failed", "blocked"},
    }
    continuity_decision = v2_continuity.decide_continuity_recovery(continuity_record)
    return {
        "continuity_state": continuity_decision.continuity_state,
        "recovery_outcome": continuity_decision.recovery_outcome,
        "reason": continuity_decision.reason,
        "decision_source": continuity_decision.decision_source,
    }


def evaluate_scheduler_plan(
    plan: SchedulerPlan,
    *,
    runtime_records: Sequence[Mapping[str, Any]],
    mode: str = "shadow",
    legacy_projection: Mapping[str, Any] | None = None,
) -> SchedulerEvaluation:
    ownership = load_ownership(mode=mode)
    assert_v2_can_evaluate_family(ownership, parent_uid=plan.parent_uid)
    canonical = projection.project_intent_family_v2_family(
        plan.raw_compilation,
        runtime_records=runtime_records,
    )
    continuity = _continuity_for_family(canonical, plan)
    shadow_report = (
        projection.compare_intent_family_v2_shadow(canonical, legacy_projection=legacy_projection)
        if legacy_projection is not None
        else {
            "schema_version": projection.SHADOW_SCHEMA_VERSION,
            "uid": projection.UID,
            "status": "not_compared",
            "parity_ok": False,
            "diffs": [],
            "reason": "independent_legacy_projection_not_supplied",
            "isolated_artifacts": {
                "canonical_projection": canonical,
                "legacy_projection": {},
                "compiled_shadow": {},
                "runtime_shadow": {},
            },
        }
    )
    return SchedulerEvaluation(
        schema_version=SCHEMA_VERSION,
        parent_uid=plan.parent_uid,
        mode=mode,
        family_status=_normalize_text(canonical.get("family_status")),
        current_stage=_normalize_text(canonical.get("current_stage")),
        admitted=_admission_decisions(plan, runtime_records),
        projection=canonical,
        continuity=continuity,
        shadow_report=shadow_report,
        mutates_canonical_state=bool(ownership.v2_can_write_canonical),
    )


def run_shadow_tick(
    *,
    parent_uid: str,
    run_at_utc: str,
    repo_root: Path | None = None,
    legacy_projection: Mapping[str, Any] | None = None,
    mode: str = "shadow",
) -> dict[str, Any]:
    plan = compile_scheduler_plan(parent_uid=parent_uid, run_at_utc=run_at_utc, repo_root=repo_root)
    runtime_records = collect_current_runtime_records(plan, repo_root=repo_root)
    evaluation = evaluate_scheduler_plan(
        plan,
        runtime_records=runtime_records,
        mode=mode,
        legacy_projection=legacy_projection,
    )
    ownership = load_ownership(mode=mode)
    return {
        "schema_version": SCHEMA_VERSION,
        "mode": mode,
        "ownership": dict(ownership_receipt(ownership, parent_uid=plan.parent_uid)),
        "plan": plan.to_dict(),
        "runtime_records": list(runtime_records),
        "evaluation": evaluation.to_dict(),
    }


def dumps_shadow_tick(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), indent=2, sort_keys=True) + "\n"
