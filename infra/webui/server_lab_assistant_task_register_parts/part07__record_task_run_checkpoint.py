_TASK_BUCKET_NAMES = ("active_tasks", "completed_tasks", "residue_tasks")


def _find_task_with_bucket(register: Dict[str, Any], task_id: str) -> tuple[str, Dict[str, Any]] | tuple[None, None]:
    target = str(task_id or "").strip()
    for bucket_name in _TASK_BUCKET_NAMES:
        for row in list(register.get(bucket_name) or []):
            if isinstance(row, dict) and str(row.get("task_id") or "").strip() == target:
                return bucket_name, dict(row)
    return None, None


def _replace_task_in_bucket(register: Dict[str, Any], *, source_bucket: str, task: Dict[str, Any]) -> None:
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        raise TaskRegisterError("task_id is required")
    target_bucket = _task_bucket_name(task)
    for bucket_name in _TASK_BUCKET_NAMES:
        register[bucket_name] = [
            row for row in list(register.get(bucket_name) or [])
            if not (isinstance(row, dict) and str(row.get("task_id") or "").strip() == task_id)
        ]
    if target_bucket not in _TASK_BUCKET_NAMES:
        target_bucket = source_bucket if source_bucket in _TASK_BUCKET_NAMES else "active_tasks"
    register.setdefault(target_bucket, []).append(task)


def record_task_run_checkpoint(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    checkpoint: Dict[str, Any],
    run_id: str = "",
    resume_decision: Optional[Dict[str, Any]] = None,
    auto_decide: bool = True,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target = str(task_id or "").strip()
    if not target:
        raise TaskRegisterError("task_id is required")
    checkpoint_row = dict(checkpoint or {})
    checkpoint_row.setdefault("checkpoint_source", writer)
    normalized_checkpoint = _normalize_task_run_checkpoint(checkpoint_row)
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
            task_idx = next(
                (
                    index
                    for index, task in enumerate(register.get("active_tasks") or [])
                    if str(task.get("task_id") or "").strip() == target
                ),
                None,
            )
            if task_idx is None:
                raise TaskRegisterError(f"task not found: {target}")
            task = dict(register["active_tasks"][task_idx]) if isinstance(register["active_tasks"][task_idx], dict) else {}
            task_runs = [dict(row) for row in list(task.get("task_runs") or [])]
            selected_run_id = _clean_text(run_id) or _clean_text(task.get("current_run_id"))
            if not selected_run_id:
                raise TaskRegisterError("task run checkpoint requires run_id")
            current_run = next((row for row in task_runs if _clean_text(row.get("run_id")) == selected_run_id), None)
            if current_run is None:
                raise TaskRegisterError(f"task run not found: {selected_run_id}")
            updated_run = dict(current_run)
            updated_run["checkpoint"] = normalized_checkpoint
            decision_payload = None
            if resume_decision:
                decision_payload = dict(resume_decision)
                decision_payload.setdefault("decision_source", writer)
                decision_payload.setdefault("checkpoint_id", normalized_checkpoint.get("checkpoint_id"))
                normalized_decision = _normalize_task_run_resume_decision(decision_payload)
            elif auto_decide:
                normalized_decision = _classify_run_resume_decision(
                    run_row=updated_run,
                    checkpoint=normalized_checkpoint,
                    decision_source=writer,
                )
            else:
                normalized_decision = dict(updated_run.get("resume_decision") or {})
            if normalized_decision:
                updated_run["resume_decision"] = normalized_decision
            updated_run["updated_utc"] = _utc_now_iso()
            updated_run["writer"] = _clean_text(writer)
            updated_run["revision"] = int(_int_or_default(updated_run.get("revision"), default=0)) + 1
            updated_run = _normalize_task_run(updated_run, task_id=target, now=_utc_now_iso())
            _validate_task_run_record(task_id=target, row=updated_run)
            task_runs = [row for row in task_runs if _clean_text(row.get("run_id")) != selected_run_id]
            task_runs.append(updated_run)
            task["task_runs"] = task_runs
            task["current_run_id"] = selected_run_id
            register["active_tasks"][task_idx] = task
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_run_recorded",
                payload=_build_task_run_record_payload(
                    task_id=target,
                    run_row=updated_run,
                    write_generation=int(register.get("write_generation") or 0),
                    write_utc=str(register.get("last_written_utc") or _utc_now_iso()),
                ),
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def record_task_run_resume_decision(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    run_id: str = "",
    resume_decision: Optional[Dict[str, Any]] = None,
    auto_decide: bool = False,
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
            task_idx = next(
                (
                    index
                    for index, task in enumerate(register.get("active_tasks") or [])
                    if str(task.get("task_id") or "").strip() == target
                ),
                None,
            )
            if task_idx is None:
                raise TaskRegisterError(f"task not found: {target}")
            task = dict(register["active_tasks"][task_idx]) if isinstance(register["active_tasks"][task_idx], dict) else {}
            task_runs = [dict(row) for row in list(task.get("task_runs") or [])]
            selected_run_id = _clean_text(run_id) or _clean_text(task.get("current_run_id"))
            if not selected_run_id:
                raise TaskRegisterError("task run resume decision requires run_id")
            current_run = next((row for row in task_runs if _clean_text(row.get("run_id")) == selected_run_id), None)
            if current_run is None:
                raise TaskRegisterError(f"task run not found: {selected_run_id}")
            updated_run = dict(current_run)
            if resume_decision:
                decision_payload = dict(resume_decision)
                decision_payload.setdefault("decision_source", writer)
                decision_payload.setdefault("checkpoint_id", _clean_text(dict(updated_run.get("checkpoint") or {}).get("checkpoint_id")))
                normalized_decision = _normalize_task_run_resume_decision(decision_payload)
            elif auto_decide:
                normalized_decision = _classify_run_resume_decision(
                    run_row=updated_run,
                    checkpoint=dict(updated_run.get("checkpoint") or {}),
                    decision_source=writer,
                )
            else:
                raise TaskRegisterError("resume_decision is required unless auto_decide is enabled")
            updated_run["resume_decision"] = normalized_decision
            updated_run["updated_utc"] = _utc_now_iso()
            updated_run["writer"] = _clean_text(writer)
            updated_run["revision"] = int(_int_or_default(updated_run.get("revision"), default=0)) + 1
            updated_run = _normalize_task_run(updated_run, task_id=target, now=_utc_now_iso())
            _validate_task_run_record(task_id=target, row=updated_run)
            task_runs = [row for row in task_runs if _clean_text(row.get("run_id")) != selected_run_id]
            task_runs.append(updated_run)
            task["task_runs"] = task_runs
            task["current_run_id"] = selected_run_id
            register["active_tasks"][task_idx] = task
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_run_recorded",
                payload=_build_task_run_record_payload(
                    task_id=target,
                    run_row=updated_run,
                    write_generation=int(register.get("write_generation") or 0),
                    write_utc=str(register.get("last_written_utc") or _utc_now_iso()),
                ),
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def record_task_run_intervention(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    action: str,
    reason: str,
    run_id: str = "",
    requested_by: str = "",
    supersedes_intervention_id: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _truth_ownership.assert_truth_write_allowed(
        domain_id=_truth_ownership.DOMAIN_TASK_WRITEBACK,
        writer=writer,
    )
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
            task_idx = next(
                (
                    index
                    for index, task in enumerate(register.get("active_tasks") or [])
                    if str(task.get("task_id") or "").strip() == target
                ),
                None,
            )
            if task_idx is None:
                raise TaskRegisterError(f"task not found: {target}")
            task = dict(register["active_tasks"][task_idx]) if isinstance(register["active_tasks"][task_idx], dict) else {}
            task_runs = [dict(row) for row in list(task.get("task_runs") or [])]
            selected_run_id = _clean_text(run_id) or _clean_text(task.get("current_run_id"))
            if not selected_run_id:
                raise TaskRegisterError("task run intervention requires run_id")
            current_run = next((row for row in task_runs if _clean_text(row.get("run_id")) == selected_run_id), None)
            if current_run is None:
                raise TaskRegisterError(f"task run not found: {selected_run_id}")
            updated_run = dict(current_run)
            intervention = _classify_task_run_intervention(
                run_row=updated_run,
                action=action,
                reason=reason,
                requested_by=_clean_text(requested_by) or _clean_text(writer),
                supersedes_intervention_id=supersedes_intervention_id,
            )
            interventions = [dict(row) for row in list(updated_run.get("interventions") or [])]
            interventions.append(intervention)
            updated_run["interventions"] = interventions
            updated_run["updated_utc"] = _utc_now_iso()
            updated_run["writer"] = _clean_text(writer)
            updated_run["revision"] = int(_int_or_default(updated_run.get("revision"), default=0)) + 1
            updated_run = _normalize_task_run(updated_run, task_id=target, now=_utc_now_iso())
            _validate_task_run_record(task_id=target, row=updated_run)
            task_runs = [row for row in task_runs if _clean_text(row.get("run_id")) != selected_run_id]
            task_runs.append(updated_run)
            task["task_runs"] = task_runs
            task["current_run_id"] = selected_run_id
            register["active_tasks"][task_idx] = task
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_run_recorded",
                payload=_build_task_run_record_payload(
                    task_id=target,
                    run_row=updated_run,
                    write_generation=int(register.get("write_generation") or 0),
                    write_utc=str(register.get("last_written_utc") or _utc_now_iso()),
                ),
            )
            _append_event(
                repo_root,
                event_type="task_run_intervention_recorded",
                payload={
                    "task_id": target,
                    "run_id": selected_run_id,
                    "intervention": dict(intervention),
                },
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register), "intervention": dict(intervention)}


def update_task_scope_contract(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    assumptions: Optional[List[Dict[str, Any]]] = None,
    constraints: Optional[List[Dict[str, Any]]] = None,
    non_goals: Optional[List[Dict[str, Any]]] = None,
    scope_revision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target_task_id = str(task_id or "").strip()
    if not target_task_id:
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
            task_bucket, existing_task = _find_task_with_bucket(register, target_task_id)
            if existing_task is None:
                raise TaskRegisterError(f"task not found: {target_task_id}")
            existing_scope = dict(existing_task.get("scope_contract") or {})
            scope_contract = _normalize_task_scope_contract(existing_scope) if existing_scope else {
                "schema_version": TASK_SCOPE_CONTRACT_SCHEMA_VERSION,
                "assumptions": [],
                "constraints": [],
                "non_goals": [],
                "current_scope": None,
                "scope_history": [],
                "updated_utc": _utc_now_iso(),
            }
            if assumptions is not None:
                existing_by_id = {
                    str(row.get("assumption_id") or "").strip(): dict(row)
                    for row in list(scope_contract.get("assumptions") or [])
                }
                merged = []
                seen_ids = set()
                for row in assumptions:
                    assumption_id = str(row.get("assumption_id") or "").strip()
                    normalized = _normalize_scope_assumption(row, existing=existing_by_id.get(assumption_id))
                    merged.append(normalized)
                    seen_ids.add(normalized["assumption_id"])
                for assumption_id, row in existing_by_id.items():
                    if assumption_id not in seen_ids:
                        merged.append(row)
                scope_contract["assumptions"] = merged
            if constraints is not None:
                existing_by_id = {
                    str(row.get("constraint_id") or "").strip(): dict(row)
                    for row in list(scope_contract.get("constraints") or [])
                }
                merged = []
                seen_ids = set()
                for row in constraints:
                    constraint_id = str(row.get("constraint_id") or "").strip()
                    normalized = _normalize_scope_constraint_row(row, existing=existing_by_id.get(constraint_id))
                    merged.append(normalized)
                    seen_ids.add(normalized["constraint_id"])
                for constraint_id, row in existing_by_id.items():
                    if constraint_id not in seen_ids:
                        merged.append(row)
                scope_contract["constraints"] = merged
            if non_goals is not None:
                existing_by_id = {
                    str(row.get("non_goal_id") or "").strip(): dict(row)
                    for row in list(scope_contract.get("non_goals") or [])
                }
                merged = []
                seen_ids = set()
                for row in non_goals:
                    non_goal_id = str(row.get("non_goal_id") or "").strip()
                    normalized = _normalize_scope_non_goal_row(row, existing=existing_by_id.get(non_goal_id))
                    merged.append(normalized)
                    seen_ids.add(normalized["non_goal_id"])
                for non_goal_id, row in existing_by_id.items():
                    if non_goal_id not in seen_ids:
                        merged.append(row)
                scope_contract["non_goals"] = merged
            if scope_revision is not None:
                current_scope = dict(scope_contract.get("current_scope") or {})
                prior_revision_id = str(current_scope.get("revision_id") or "").strip()
                normalized_scope = _normalize_scope_revision(
                    target_task_id,
                    dict(scope_revision),
                    prior_revision_id=prior_revision_id,
                )
                scope_contract["current_scope"] = normalized_scope
                scope_contract["scope_history"] = list(scope_contract.get("scope_history") or []) + [normalized_scope]
            scope_contract["updated_utc"] = _utc_now_iso()
            existing_task["scope_contract"] = scope_contract
            normalized_task = _normalize_task(existing_task)
            _replace_task_in_bucket(register, source_bucket=str(task_bucket or ""), task=normalized_task)
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_scope_contract_updated",
                payload={
                    "writer": str(writer or ""),
                    "task_id": target_task_id,
                    "assumption_count": len(list(scope_contract.get("assumptions") or [])),
                    "constraint_count": len(list(scope_contract.get("constraints") or [])),
                    "non_goal_count": len(list(scope_contract.get("non_goals") or [])),
                    "scope_revision_id": str(dict(scope_contract.get("current_scope") or {}).get("revision_id") or ""),
                },
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def update_task_acceptance_contract(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    acceptance_targets: Optional[List[Dict[str, Any]]] = None,
    proof_targets: Optional[List[Dict[str, Any]]] = None,
    review_gates: Optional[List[Dict[str, Any]]] = None,
    completion_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target_task_id = str(task_id or "").strip()
    if not target_task_id:
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
            task_bucket, existing_task = _find_task_with_bucket(register, target_task_id)
            if existing_task is None:
                raise TaskRegisterError(f"task not found: {target_task_id}")
            existing_contract = dict(existing_task.get("acceptance_contract") or {})
            contract = _normalize_task_acceptance_contract(existing_contract) if existing_contract else {
                "schema_version": TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION,
                "acceptance_targets": [],
                "proof_targets": [],
                "review_gates": [],
                "completion_state": _normalize_acceptance_completion_state({}),
                "updated_utc": _utc_now_iso(),
            }
            if acceptance_targets is not None:
                existing_by_id = {
                    str(row.get("target_id") or "").strip(): dict(row)
                    for row in list(contract.get("acceptance_targets") or [])
                }
                merged = []
                seen_ids = set()
                for row in acceptance_targets:
                    target_id = str(row.get("target_id") or "").strip()
                    normalized = _normalize_acceptance_target_row(row, existing=existing_by_id.get(target_id))
                    merged.append(normalized)
                    seen_ids.add(normalized["target_id"])
                for target_id, row in existing_by_id.items():
                    if target_id not in seen_ids:
                        merged.append(row)
                contract["acceptance_targets"] = merged
            if proof_targets is not None:
                existing_by_id = {
                    str(row.get("proof_id") or "").strip(): dict(row)
                    for row in list(contract.get("proof_targets") or [])
                }
                merged = []
                seen_ids = set()
                for row in proof_targets:
                    proof_id = str(row.get("proof_id") or "").strip()
                    normalized = _normalize_proof_target_row(row, existing=existing_by_id.get(proof_id))
                    merged.append(normalized)
                    seen_ids.add(normalized["proof_id"])
                for proof_id, row in existing_by_id.items():
                    if proof_id not in seen_ids:
                        merged.append(row)
                contract["proof_targets"] = merged
            if review_gates is not None:
                existing_by_id = {
                    str(row.get("gate_id") or "").strip(): dict(row)
                    for row in list(contract.get("review_gates") or [])
                }
                merged = []
                seen_ids = set()
                for row in review_gates:
                    gate_id = str(row.get("gate_id") or "").strip()
                    normalized = _normalize_review_gate_row(row, existing=existing_by_id.get(gate_id))
                    merged.append(normalized)
                    seen_ids.add(normalized["gate_id"])
                for gate_id, row in existing_by_id.items():
                    if gate_id not in seen_ids:
                        merged.append(row)
                contract["review_gates"] = merged
            if completion_state is not None:
                contract["completion_state"] = _normalize_acceptance_completion_state(
                    dict(completion_state),
                    existing=dict(contract.get("completion_state") or {}),
                )
            contract["updated_utc"] = _utc_now_iso()
            _validate_task_acceptance_contract(target_task_id, contract)
            existing_task["acceptance_contract"] = contract
            normalized_task = _normalize_task(existing_task)
            _replace_task_in_bucket(register, source_bucket=str(task_bucket or ""), task=normalized_task)
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_acceptance_contract_updated",
                payload={
                    "writer": str(writer or ""),
                    "task_id": target_task_id,
                    "acceptance_target_count": len(list(contract.get("acceptance_targets") or [])),
                    "proof_target_count": len(list(contract.get("proof_targets") or [])),
                    "review_gate_count": len(list(contract.get("review_gates") or [])),
                    "done_state": _acceptance_contract_stage(contract),
                },
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def update_task_decomposition(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    mode: str,
    rationale: str,
    decision: Dict[str, Any],
    linked_assumption_ids: Optional[List[str]] = None,
    linked_acceptance_target_ids: Optional[List[str]] = None,
    direct_summary: str = "",
    milestones: Optional[List[Dict[str, Any]]] = None,
    dependency_mapping: Optional[Dict[str, Any]] = None,
    active_milestone_id: str = "",
    active_checkpoint_id: str = "",
    revision_trigger: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target_task_id = str(task_id or "").strip()
    normalized_mode = str(mode or "").strip()
    if not target_task_id:
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
            task_bucket, existing_task = _find_task_with_bucket(register, target_task_id)
            if existing_task is None:
                raise TaskRegisterError(f"task not found: {target_task_id}")
            existing_contract = dict(existing_task.get("decomposition_contract") or {})
            next_version = int(existing_contract.get("plan_version") or 0) + 1
            if next_version < 1:
                next_version = 1
            normalized_milestones = [_normalize_milestone_row(row) for row in list(milestones or [])]
            plan_summary = {
                "milestone_count": len(normalized_milestones),
                "checkpoint_count": sum(len(list(row.get("checkpoints") or [])) for row in normalized_milestones),
                "active_milestone_id": str(active_milestone_id or "").strip(),
                "active_checkpoint_id": str(active_checkpoint_id or "").strip(),
                "direct_summary": str(direct_summary or "").strip(),
            }
            if existing_contract and not revision_trigger:
                raise TaskRegisterError("revision_trigger is required when revising an existing plan")
            trigger = _normalize_replan_trigger(
                dict(revision_trigger or {"kind": "initial_plan", "summary": "Initial durable plan snapshot."}),
                default_kind="initial_plan" if not existing_contract else "changed_evidence",
            )
            new_revision_id = uuid.uuid4().hex
            decomposition_contract = _normalize_task_decomposition_contract(
                {
                    "schema_version": TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION,
                    "replanning": {"state": "settled"},
                    "mode": normalized_mode,
                    "rationale": str(rationale or "").strip(),
                    "decision": dict(decision or {}),
                    "linked_assumption_ids": list(linked_assumption_ids or []),
                    "linked_acceptance_target_ids": list(linked_acceptance_target_ids or []),
                    "plan_summary": plan_summary,
                    "plan_version": next_version,
                    "milestones": normalized_milestones,
                    "dependency_mapping": dict(dependency_mapping or {}) if dependency_mapping else None,
                    "current_revision_id": new_revision_id,
                    "updated_utc": _utc_now_iso(),
                }
            )
            _validate_decomposition_links(existing_task, decomposition_contract)
            artifact_ref = _write_decomposition_artifact(repo_root, task=existing_task, decomposition_contract=decomposition_contract)
            decomposition_contract["artifact_ref"] = artifact_ref
            decomposition_contract["revision_history"] = _revision_history_with_new_current(
                existing_contract,
                new_revision_id=new_revision_id,
                new_plan_version=int(decomposition_contract.get("plan_version") or 0),
                mode=str(decomposition_contract.get("mode") or "").strip(),
                rationale=str(decomposition_contract.get("rationale") or "").strip(),
                decision_summary=str(dict(decomposition_contract.get("decision") or {}).get("decision_summary") or "").strip(),
                plan_summary=dict(decomposition_contract.get("plan_summary") or {}),
                trigger=trigger,
                artifact_ref=artifact_ref,
            )
            _write_decomposition_artifact(repo_root, task=existing_task, decomposition_contract=decomposition_contract)
            _validate_task_decomposition_contract(target_task_id, decomposition_contract)
            existing_task["decomposition_contract"] = decomposition_contract
            normalized_task = _normalize_task(existing_task)
            _replace_task_in_bucket(register, source_bucket=str(task_bucket or ""), task=normalized_task)
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_decomposition_updated",
                payload={
                    "writer": str(writer or ""),
                    "task_id": target_task_id,
                    "mode": normalized_mode,
                    "plan_version": int(decomposition_contract.get("plan_version") or 0),
                    "current_revision_id": str(decomposition_contract.get("current_revision_id") or ""),
                    "artifact_ref": artifact_ref,
                    "milestone_count": int(dict(decomposition_contract.get("plan_summary") or {}).get("milestone_count") or 0),
                    "checkpoint_count": int(dict(decomposition_contract.get("plan_summary") or {}).get("checkpoint_count") or 0),
                    "dependency_edge_count": int(
                        dict(decomposition_contract.get("plan_summary") or {}).get("dependency_edge_count") or 0
                    ),
                    "fanout_ready_count": int(
                        dict(decomposition_contract.get("plan_summary") or {}).get("fanout_ready_count") or 0
                    ),
                    "revision_trigger_kind": str(trigger.get("kind") or ""),
                },
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "changed": True, "task_register": dict(register), "artifact_ref": artifact_ref}
