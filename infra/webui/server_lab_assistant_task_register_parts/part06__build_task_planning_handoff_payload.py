def _build_task_planning_handoff_payload(
    *,
    register_generation: int,
    task: Dict[str, Any],
) -> TaskRegisterSnapshot:
    now = _utc_now_iso()
    task_id = str(task.get("task_id") or "").strip()
    decomposition_contract = dict(task.get("decomposition_contract") or {})
    linked_assumption_ids = [
        str(item).strip() for item in list(decomposition_contract.get("linked_assumption_ids") or []) if str(item).strip()
    ]
    linked_target_ids = [
        str(item).strip()
        for item in list(decomposition_contract.get("linked_acceptance_target_ids") or [])
        if str(item).strip()
    ]
    readiness = _planning_handoff_readiness(task)
    current_revision = _current_revision_row(decomposition_contract)
    milestones = [row for row in list(decomposition_contract.get("milestones") or []) if isinstance(row, dict)]
    plan_summary = dict(decomposition_contract.get("plan_summary") or {})
    dependency_mapping = dict(decomposition_contract.get("dependency_mapping") or {})
    checkpoint_rows: List[Dict[str, Any]] = []
    for milestone in milestones:
        milestone_id = str(milestone.get("milestone_id") or "").strip()
        for checkpoint in list(milestone.get("checkpoints") or []):
            if not isinstance(checkpoint, dict):
                continue
            checkpoint_rows.append(
                {
                    "milestone_id": milestone_id,
                    "checkpoint_id": str(checkpoint.get("checkpoint_id") or "").strip(),
                    "title": str(checkpoint.get("title") or "").strip(),
                    "rationale": str(checkpoint.get("rationale") or "").strip(),
                    "exit_criteria": list(checkpoint.get("exit_criteria") or []),
                    "assumption_ids": list(checkpoint.get("assumption_ids") or []),
                    "acceptance_target_ids": list(checkpoint.get("acceptance_target_ids") or []),
                }
            )
    return {
        "schema_version": TASK_PLANNING_HANDOFF_SCHEMA_VERSION,
        "handoff_id": f"handoff_{uuid.uuid4().hex}",
        "created_utc": now,
        "task": {
            "task_id": task_id,
            "title": str(task.get("title") or "").strip(),
            "ledger_uid": str(task.get("ledger_uid") or "").strip(),
            "register_status": str(task.get("status") or "").strip(),
            "next_expected_action": str(task.get("next_expected_action") or "").strip(),
            "blocked_reason": str(task.get("blocked_reason") or "").strip(),
            "task_register_generation": int(register_generation),
            "task_updated_utc": str(task.get("updated_utc") or "").strip(),
        },
        "launch_readiness": {
            "state": str(readiness.get("state") or "blocked"),
            "launch_ready": bool(readiness.get("launch_ready")),
            "blocked_reasons": list(readiness.get("blocked_reasons") or []),
            "partial_reasons": list(readiness.get("partial_reasons") or []),
        },
        "boundary": {
            "routing_owner_uid": _ROUTING_OWNER_UID,
            "execution_truth_owner_uid": _EXECUTION_OWNER_UID,
            "routing_decision_included": False,
            "mailbox_or_job_lifecycle_included": False,
        },
        "planning_payload": {
            "decomposition": {
                "mode": str(decomposition_contract.get("mode") or "").strip(),
                "plan_version": int(decomposition_contract.get("plan_version") or 0),
                "current_revision_id": str(decomposition_contract.get("current_revision_id") or "").strip(),
                "artifact_ref": str(decomposition_contract.get("artifact_ref") or "").strip(),
                "rationale": str(decomposition_contract.get("rationale") or "").strip(),
                "decision_summary": str(dict(decomposition_contract.get("decision") or {}).get("decision_summary") or "").strip(),
                "plan_summary": {
                    "milestone_count": int(plan_summary.get("milestone_count") or 0),
                    "checkpoint_count": int(plan_summary.get("checkpoint_count") or 0),
                    "dependency_edge_count": int(plan_summary.get("dependency_edge_count") or 0),
                    "fanout_ready_count": int(plan_summary.get("fanout_ready_count") or 0),
                    "forced_serial_count": int(plan_summary.get("forced_serial_count") or 0),
                    "fanout_group_count": int(plan_summary.get("fanout_group_count") or 0),
                    "active_milestone_id": str(plan_summary.get("active_milestone_id") or "").strip(),
                    "active_checkpoint_id": str(plan_summary.get("active_checkpoint_id") or "").strip(),
                    "direct_summary": str(plan_summary.get("direct_summary") or "").strip(),
                },
                "milestones": milestones,
                "checkpoint_rows": checkpoint_rows,
                "dependency_mapping": dependency_mapping,
                "dependency_edges": _dependency_edges(decomposition_contract),
                "replanning": dict(decomposition_contract.get("replanning") or {}),
            },
            "scope": {
                "linked_assumption_ids": linked_assumption_ids,
                "linked_assumptions": _linked_scope_rows(task, linked_assumption_ids),
            },
            "acceptance": {
                "linked_acceptance_target_ids": linked_target_ids,
                "linked_acceptance_targets": _linked_acceptance_rows(task, linked_target_ids),
                "proof_targets": list(dict(task.get("acceptance_contract") or {}).get("proof_targets") or []),
                "review_gates": list(dict(task.get("acceptance_contract") or {}).get("review_gates") or []),
                "completion_state": dict(dict(task.get("acceptance_contract") or {}).get("completion_state") or {}),
            },
            "rationale_trail": {
                "current_revision": current_revision,
                "revision_history": list(decomposition_contract.get("revision_history") or []),
            },
        },
        "execution_launch_contract": {
            "route_decision_required": True,
            "route_decision_owner_uid": _ROUTING_OWNER_UID,
            "execution_truth_owner_uid": _EXECUTION_OWNER_UID,
            "launch_identity": {
                "task_id": task_id,
                "current_revision_id": str(decomposition_contract.get("current_revision_id") or "").strip(),
                "plan_version": int(decomposition_contract.get("plan_version") or 0),
            },
        },
        "source_of_truth": {
            "task_register": "BD05 task register row",
            "decomposition_artifact_ref": str(decomposition_contract.get("artifact_ref") or "").strip(),
            "reconstructed_from_canonical_planning_state": False,
        },
    }


def _handoff_artifact_filename(task_id: str, *, plan_version: int, current_revision_id: str, created_utc: str) -> str:
    stamp = str(created_utc or "").strip().replace(":", "").replace("-", "")
    rev = _safe_task_token(str(current_revision_id or "")[:12] or "none")
    return f"handoff__{_safe_task_token(task_id)}__v{int(plan_version):03d}__{rev}__{stamp}.json"


def _write_planning_handoff_artifact(
    repo_root: Path,
    *,
    task: Dict[str, Any],
    handoff: Dict[str, Any],
) -> str:
    ledger_uid = str(task.get("ledger_uid") or "").strip()
    if not ledger_uid:
        raise TaskRegisterError(f"task {str(task.get('task_id') or '').strip()} requires ledger_uid for planning handoff storage")
    item_dir = _resolve_ledger_item_dir(repo_root, ledger_uid)
    handoff_dir = _planning_handoff_dir_for_item(item_dir)
    handoff_dir.mkdir(parents=True, exist_ok=True)
    launch_identity = dict(dict(handoff.get("execution_launch_contract") or {}).get("launch_identity") or {})
    filename = _handoff_artifact_filename(
        str(task.get("task_id") or "").strip(),
        plan_version=int(launch_identity.get("plan_version") or 0),
        current_revision_id=str(launch_identity.get("current_revision_id") or "").strip(),
        created_utc=str(handoff.get("created_utc") or _utc_now_iso()),
    )
    artifact_path = (handoff_dir / filename).resolve()
    artifact_root = (item_dir / "artifacts").resolve()
    if artifact_root not in artifact_path.parents:
        raise TaskRegisterError("planning handoff artifact path escaped ledger artifact root")
    _write_json_atomic(artifact_path, handoff)
    return str(artifact_path.relative_to(Path(repo_root).resolve()))


def _latest_planning_handoff_artifact_path(repo_root: Path, *, task: Dict[str, Any]) -> Optional[Path]:
    ledger_uid = str(task.get("ledger_uid") or "").strip()
    if not ledger_uid:
        return None
    try:
        item_dir = _resolve_ledger_item_dir(repo_root, ledger_uid)
    except TaskRegisterError:
        return None
    handoff_dir = _planning_handoff_dir_for_item(item_dir)
    if not handoff_dir.exists() or not handoff_dir.is_dir():
        return None
    safe_task = _safe_task_token(str(task.get("task_id") or "").strip())
    candidates = sorted(handoff_dir.glob(f"handoff__{safe_task}__*.json"))
    return candidates[-1].resolve() if candidates else None


def _revision_history_with_new_current(
    existing_contract: Dict[str, Any],
    *,
    new_revision_id: str,
    new_plan_version: int,
    mode: str,
    rationale: str,
    decision_summary: str,
    plan_summary: Dict[str, Any],
    trigger: Dict[str, Any],
    artifact_ref: str,
) -> List[Dict[str, Any]]:
    now = _utc_now_iso()
    history = _legacy_revision_history_from_contract(existing_contract)
    if existing_contract.get("revision_history"):
        history = [_normalize_plan_revision_row(row) for row in list(existing_contract.get("revision_history") or [])]
    current_prior_id = str(existing_contract.get("current_revision_id") or "").strip()
    updated_history: List[Dict[str, Any]] = []
    for row in history:
        if str(row.get("revision_id") or "").strip() == current_prior_id and str(row.get("state") or "").strip() == "current":
            revised = dict(row)
            revised["state"] = "superseded"
            revised["superseded_by_revision_id"] = new_revision_id
            revised["updated_utc"] = now
            updated_history.append(_normalize_plan_revision_row(revised))
        else:
            updated_history.append(_normalize_plan_revision_row(row))
    updated_history.append(
        _normalize_plan_revision_row(
            {
                "revision_id": new_revision_id,
                "plan_version": new_plan_version,
                "state": "current",
                "mode": mode,
                "artifact_ref": artifact_ref,
                "rationale": rationale,
                "decision_summary": decision_summary,
                "plan_summary": plan_summary,
                "supersedes_revision_id": current_prior_id,
                "trigger": trigger,
                "captured_utc": now,
                "updated_utc": now,
            }
        )
    )
    return updated_history


def _invalidate_current_revision_history(
    existing_contract: Dict[str, Any],
    *,
    trigger: Dict[str, Any],
) -> Dict[str, Any]:
    history = _legacy_revision_history_from_contract(existing_contract)
    if existing_contract.get("revision_history"):
        history = [_normalize_plan_revision_row(row) for row in list(existing_contract.get("revision_history") or [])]
    current_revision_id = str(existing_contract.get("current_revision_id") or "").strip()
    if not current_revision_id:
        raise TaskRegisterError("cannot invalidate decomposition without a current revision")
    now = _utc_now_iso()
    updated_history: List[Dict[str, Any]] = []
    invalidated_plan_version = 0
    found_current = False
    for row in history:
        if str(row.get("revision_id") or "").strip() == current_revision_id and str(row.get("state") or "").strip() == "current":
            revised = dict(row)
            revised["state"] = "invalidated"
            revised["trigger"] = trigger
            revised["updated_utc"] = now
            invalidated_plan_version = int(revised.get("plan_version") or 0)
            updated_history.append(_normalize_plan_revision_row(revised))
            found_current = True
        else:
            updated_history.append(_normalize_plan_revision_row(row))
    if not found_current:
        raise TaskRegisterError("current decomposition revision not found in revision history")
    return {
        "revision_history": updated_history,
        "invalidated_revision_id": current_revision_id,
        "invalidated_plan_version": invalidated_plan_version,
    }


def _validate_decomposition_links(task: Dict[str, Any], decomposition_contract: Dict[str, Any]) -> None:
    task_id = str(task.get("task_id") or "").strip()
    scope_contract = dict(task.get("scope_contract") or {})
    acceptance_contract = dict(task.get("acceptance_contract") or {})
    known_assumption_ids = {
        str(row.get("assumption_id") or "").strip() for row in list(scope_contract.get("assumptions") or [])
    }
    known_target_ids = {
        str(row.get("target_id") or "").strip() for row in list(acceptance_contract.get("acceptance_targets") or [])
    }
    linked_assumption_ids = set(list(decomposition_contract.get("linked_assumption_ids") or []))
    linked_acceptance_target_ids = set(list(decomposition_contract.get("linked_acceptance_target_ids") or []))
    if linked_assumption_ids - known_assumption_ids:
        missing = ", ".join(sorted(linked_assumption_ids - known_assumption_ids))
        raise TaskRegisterError(f"task {task_id} decomposition references unknown assumption_ids: {missing}")
    if linked_acceptance_target_ids - known_target_ids:
        missing = ", ".join(sorted(linked_acceptance_target_ids - known_target_ids))
        raise TaskRegisterError(f"task {task_id} decomposition references unknown acceptance_target_ids: {missing}")
    for milestone in list(decomposition_contract.get("milestones") or []):
        milestone_id = str(milestone.get("milestone_id") or "").strip()
        milestone_assumptions = set(list(milestone.get("assumption_ids") or []))
        milestone_targets = set(list(milestone.get("acceptance_target_ids") or []))
        if milestone_assumptions - known_assumption_ids:
            missing = ", ".join(sorted(milestone_assumptions - known_assumption_ids))
            raise TaskRegisterError(f"task {task_id} milestone {milestone_id} references unknown assumption_ids: {missing}")
        if milestone_targets - known_target_ids:
            missing = ", ".join(sorted(milestone_targets - known_target_ids))
            raise TaskRegisterError(
                f"task {task_id} milestone {milestone_id} references unknown acceptance_target_ids: {missing}"
            )
        for checkpoint in list(milestone.get("checkpoints") or []):
            checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
            checkpoint_assumptions = set(list(checkpoint.get("assumption_ids") or []))
            checkpoint_targets = set(list(checkpoint.get("acceptance_target_ids") or []))
            if checkpoint_assumptions - known_assumption_ids:
                missing = ", ".join(sorted(checkpoint_assumptions - known_assumption_ids))
                raise TaskRegisterError(
                    f"task {task_id} checkpoint {checkpoint_id} references unknown assumption_ids: {missing}"
                )
            if checkpoint_targets - known_target_ids:
                missing = ", ".join(sorted(checkpoint_targets - known_target_ids))
                raise TaskRegisterError(
                    f"task {task_id} checkpoint {checkpoint_id} references unknown acceptance_target_ids: {missing}"
                )


_TASK_BUCKET_NAMES = ("active_tasks", "completed_tasks", "residue_tasks")


def _find_task_bucket_index(register: Dict[str, Any], task_id: str) -> tuple[str, int] | tuple[None, None]:
    target = _clean_text(task_id)
    for bucket_name in _TASK_BUCKET_NAMES:
        for index, task in enumerate(register.get(bucket_name) or []):
            if _clean_text(dict(task).get("task_id")) == target:
                return bucket_name, index
    return None, None


def upsert_task(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    include_routing_decision = "routing_decision" in dict(task or {})
    include_task_runs = "task_runs" in dict(task or {})
    include_current_run_id = "current_run_id" in dict(task or {})
    incoming_task = dict(task or {})
    normalized_task_id = _clean_text(incoming_task.get("task_id"))
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            existing = next((t for bucket_name in _TASK_BUCKET_NAMES for t in list(register.get(bucket_name) or []) if str(t.get("task_id") or "") == normalized_task_id), None)
            if existing is not None and "intake_path" not in incoming_task and "intake_path" in existing:
                incoming_task["intake_path"] = existing.get("intake_path")
            normalized = _normalize_task(incoming_task)
            conflict_fields: List[str] = []
            if existing:
                if not include_task_runs and isinstance(existing, dict) and existing.get("task_runs") is not None:
                    normalized["task_runs"] = [dict(row) for row in list(existing.get("task_runs") or [])]
                if not include_current_run_id and isinstance(existing, dict) and "current_run_id" in existing and "current_run_id" not in normalized:
                    normalized["current_run_id"] = str(existing.get("current_run_id") or "")
                if not include_routing_decision and "routing_decision" in existing:
                    normalized["routing_decision"] = dict(existing["routing_decision"])
                for key in ("kind", "title", "source", "ledger_uid", "intake_path"):
                    if str(existing.get(key) or "") != str(normalized.get(key) or "") and str(existing.get(key) or ""):
                        conflict_fields.append(key)
                conflict_fields.extend(_routing_decision_conflicts(task_id=normalized["task_id"], existing=existing, incoming=normalized))
            if conflict_fields:
                normalized["merge_status"] = "merge_needed"
                normalized["conflict_fields"] = sorted(set(conflict_fields))
            for bucket_name in _TASK_BUCKET_NAMES:
                register[bucket_name] = [
                    t for t in list(register.get(bucket_name) or []) if str(t.get("task_id") or "") != normalized["task_id"]
                ]
            register["active_tasks"].append(normalized)
            _set_generation(register, writer=writer)
            _append_event(repo_root, event_type="task_upserted", payload={"task": normalized})
            if conflict_fields:
                _append_event(
                    repo_root,
                    event_type="task_conflict_detected",
                    payload={"task_id": normalized["task_id"], "conflict_fields": conflict_fields},
                )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def upsert_open_loop(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    open_loop: Dict[str, Any],
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    normalized = _normalize_open_loop(open_loop)
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            register["open_loops"] = [l for l in register["open_loops"] if str(l.get("loop_id") or "") != normalized["loop_id"]]
            register["open_loops"].append(normalized)
            _set_generation(register, writer=writer)
            _append_event(repo_root, event_type="open_loop_upserted", payload={"open_loop": normalized})
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def resolve_open_loop(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    loop_id: str,
    resolution_note: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target = str(loop_id or "").strip()
    if not target:
        raise TaskRegisterError("loop_id is required")
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            found = False
            for loop in register["open_loops"]:
                if str(loop.get("loop_id") or "") == target:
                    loop["status"] = "resolved"
                    loop["updated_utc"] = _utc_now_iso()
                    loop["resolved_utc"] = _utc_now_iso()
                    if resolution_note:
                        loop["note"] = str(resolution_note)
                    found = True
                    break
            if not found:
                raise TaskRegisterError(f"open loop not found: {target}")
            _set_generation(register, writer=writer)
            _append_event(repo_root, event_type="open_loop_resolved", payload={"loop_id": target, "resolved_utc": _utc_now_iso(), "note": resolution_note})
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def retire_task(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    resolution_note: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target = str(task_id or "").strip()
    if not target:
        raise TaskRegisterError("task_id is required")
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            existing = next((dict(row) for bucket_name in _TASK_BUCKET_NAMES for row in list(register.get(bucket_name) or []) if str(row.get("task_id") or "").strip() == target), None)
            if existing is None:
                raise TaskRegisterError(f"task not found: {target}")
            now_utc = _utc_now_iso()
            updated_loops: List[Dict[str, Any]] = []
            resolved_loop_ids: List[str] = []
            for row in list(register.get("open_loops") or []):
                loop = dict(row)
                linked_task_id = str(loop.get("linked_task_id") or "").strip()
                loop_id = str(loop.get("loop_id") or "").strip()
                if linked_task_id == target or loop_id.startswith(f"writeback::{target}::"):
                    if str(loop.get("status") or "").strip() != "resolved":
                        resolved_loop_ids.append(loop_id)
                    loop["status"] = "resolved"
                    loop["updated_utc"] = now_utc
                    loop["resolved_utc"] = now_utc
                    if resolution_note:
                        loop["note"] = str(resolution_note)
                updated_loops.append(loop)
            for bucket_name in _TASK_BUCKET_NAMES:
                register[bucket_name] = [
                    row for row in list(register.get(bucket_name) or []) if str(row.get("task_id") or "").strip() != target
                ]
            register["open_loops"] = updated_loops
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_retired",
                payload={
                    "task_id": target,
                    "title": str(existing.get("title") or ""),
                    "previous_status": str(existing.get("status") or ""),
                    "resolved_loop_ids": resolved_loop_ids,
                    "note": resolution_note,
                    "retired_utc": now_utc,
                },
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def set_constraint(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    constraint: Dict[str, Any],
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    normalized = _normalize_constraint(constraint)
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            register["constraints"] = [c for c in register["constraints"] if str(c.get("key") or "") != normalized["key"]]
            register["constraints"].append(normalized)
            _set_generation(register, writer=writer)
            _append_event(repo_root, event_type="constraint_set", payload={"constraint": normalized})
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def record_execution_ref(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    execution_ref: Dict[str, Any],
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    normalized = _normalize_execution_ref(execution_ref)
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            key = f"{normalized['kind']}:{normalized['ref_id']}"
            register["recent_execution_refs"] = [
                r for r in register["recent_execution_refs"] if f"{r.get('kind')}:{r.get('ref_id')}" != key
            ]
            register["recent_execution_refs"].append(normalized)
            register["recent_execution_refs"] = register["recent_execution_refs"][-25:]
            _set_generation(register, writer=writer)
            _append_event(repo_root, event_type="execution_ref_recorded", payload={"execution_ref": normalized})
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def record_execution_surface_ownership(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    selected_surface: str,
    execution_id: str,
    execution_ref: str,
    binding_ref: str,
    job_uri: str,
    trace_id: str,
    checkpoint: Optional[Dict[str, Any]] = None,
    resume_decision: Optional[Dict[str, Any]] = None,
    reference_status: str = "",
    reference_status_reason: str = "",
    allow_conflict_overwrite: bool = False,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target = str(task_id or "").strip()
    if not target:
        raise TaskRegisterError("task_id is required")
    normalized_surface = _normalize_execution_surface(selected_surface)
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            bucket_name, task_idx = _find_task_bucket_index(register, target)
            if bucket_name is None or task_idx is None:
                raise TaskRegisterError(f"task not found: {target}")
            task = dict(register[bucket_name][task_idx]) if isinstance(register[bucket_name][task_idx], dict) else {}
            existing_owner = dict(task.get("execution_surface_ownership") or {})
            next_owner = _next_execution_surface_ownership_row(
                target,
                existing=existing_owner,
                selected_surface=normalized_surface,
                execution_id=execution_id,
                execution_ref=execution_ref,
                binding_ref=binding_ref,
                job_uri=job_uri,
                trace_id=trace_id,
                reference_status=reference_status,
                reference_status_reason=reference_status_reason,
                conflict_gate=allow_conflict_overwrite,
                last_writer=writer,
            )
            _validate_execution_surface_ownership(target, dict(next_owner))
            task["execution_surface_ownership"] = next_owner
            task = _write_task_run_row(
                task,
                selected_owner=next_owner,
                last_writer=writer,
                checkpoint=checkpoint,
                resume_decision=resume_decision,
            )
            current_run_id = str(task.get("current_run_id") or "").strip()
            run_rows = [dict(row) for row in list(task.get("task_runs") or [])]
            current_run = next(
                (dict(row) for row in run_rows if str(row.get("run_id") or "").strip() == current_run_id),
                None,
            )
            if current_run is None:
                raise TaskRegisterError("recorded execution_surface_ownership failed to emit a task run row")
            register[bucket_name][task_idx] = task
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_run_recorded",
                payload=_build_task_run_record_payload(
                    task_id=target,
                    run_row=current_run,
                    write_generation=int(register.get("write_generation") or 0),
                    write_utc=str(register.get("last_written_utc") or _utc_now_iso()),
                ),
            )
            _append_event(
                repo_root,
                event_type="task_execution_surface_ownership_recorded",
                payload={
                    "task_id": target,
                    "selected_surface": normalized_surface,
                    "reference_revision": int(next_owner.get("reference_revision") or 0),
                    "reference_status": str(next_owner.get("reference_status") or ""),
                    "allow_conflict_overwrite": bool(allow_conflict_overwrite),
                },
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}
