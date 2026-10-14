def _normalize_dependency_mapping(
    dependency_mapping: Dict[str, Any],
    *,
    milestones: List[Dict[str, Any]],
) -> Dict[str, Any]:
    raw_rows = list(dependency_mapping.get("milestones") or [])
    existing_rows = {}
    for item in raw_rows:
        if not isinstance(item, dict):
            continue
        milestone_id = str(item.get("milestone_id") or "").strip()
        if milestone_id:
            existing_rows[milestone_id] = dict(item)
    rows = [
        _normalize_dependency_mapping_row(
            existing_rows.get(
                str(milestone.get("milestone_id") or "").strip(),
                {"milestone_id": str(milestone.get("milestone_id") or "").strip()},
            )
        )
        for milestone in milestones
        if str(milestone.get("milestone_id") or "").strip()
    ]
    now = _utc_now_iso()
    summary_counts = {
        "dependency_edge_count": sum(len(list(row.get("depends_on") or [])) for row in rows),
        "fanout_ready_count": len([row for row in rows if str(row.get("execution_mode") or "").strip() == "fanout_safe"]),
        "forced_serial_count": len([row for row in rows if str(row.get("execution_mode") or "").strip() == "serial_only"]),
        "fanout_group_count": len(
            {str(row.get("fanout_group_id") or "").strip() for row in rows if str(row.get("fanout_group_id") or "").strip()}
        ),
    }
    normalized = {
        "schema_version": str(dependency_mapping.get("schema_version") or TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION).strip()
        or TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION,
        "fanout_policy": str(dependency_mapping.get("fanout_policy") or "serial_only").strip() or "serial_only",
        "summary": str(dependency_mapping.get("summary") or "").strip(),
        "summary_counts": summary_counts,
        "milestones": rows,
        "source_row_count": len(raw_rows),
        "updated_utc": str(dependency_mapping.get("updated_utc") or now),
    }
    _validate_dependency_mapping("decomposition_contract", normalized, milestones=milestones)
    normalized.pop("source_row_count", None)
    return normalized


def _normalize_replan_trigger(trigger: Dict[str, Any], *, default_kind: str) -> Dict[str, Any]:
    now = _utc_now_iso()
    normalized = {
        "kind": str(trigger.get("kind") or default_kind).strip() or default_kind,
        "summary": str(trigger.get("summary") or "").strip(),
        "evidence_refs": [str(item).strip() for item in list(trigger.get("evidence_refs") or []) if str(item).strip()],
        "constraint_ids": [str(item).strip() for item in list(trigger.get("constraint_ids") or []) if str(item).strip()],
        "assumption_ids": [str(item).strip() for item in list(trigger.get("assumption_ids") or []) if str(item).strip()],
        "proof_target_ids": [str(item).strip() for item in list(trigger.get("proof_target_ids") or []) if str(item).strip()],
        "captured_utc": str(trigger.get("captured_utc") or now),
    }
    if normalized["kind"] not in TASK_REPLAN_TRIGGER_KINDS:
        raise TaskRegisterError(f"invalid replanning trigger kind: {normalized['kind'] or '<missing>'}")
    if not normalized["summary"]:
        raise TaskRegisterError("replanning trigger summary is required")
    return normalized


def _normalize_plan_revision_row(row: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now_iso()
    normalized = {
        "revision_id": str(row.get("revision_id") or uuid.uuid4().hex).strip(),
        "plan_version": int(row.get("plan_version") or 0),
        "state": str(row.get("state") or "").strip(),
        "mode": str(row.get("mode") or "").strip(),
        "artifact_ref": str(row.get("artifact_ref") or "").strip(),
        "rationale": str(row.get("rationale") or "").strip(),
        "decision_summary": str(row.get("decision_summary") or "").strip(),
        "plan_summary": dict(row.get("plan_summary") or {}),
        "supersedes_revision_id": str(row.get("supersedes_revision_id") or "").strip(),
        "superseded_by_revision_id": str(row.get("superseded_by_revision_id") or "").strip(),
        "trigger": _normalize_replan_trigger(dict(row.get("trigger") or {}), default_kind="initial_plan"),
        "captured_utc": str(row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
    }
    if normalized["state"] not in TASK_PLAN_REVISION_STATES:
        raise TaskRegisterError(f"invalid plan revision state: {normalized['state'] or '<missing>'}")
    if normalized["plan_version"] < 1:
        raise TaskRegisterError("plan revision plan_version must be >= 1")
    return normalized


def _legacy_revision_history_from_contract(decomposition_contract: Dict[str, Any]) -> List[Dict[str, Any]]:
    mode = str(decomposition_contract.get("mode") or "").strip()
    if mode not in TASK_DECOMPOSITION_MODES:
        return []
    version = int(decomposition_contract.get("plan_version") or 0)
    if version < 1:
        return []
    return [
        _normalize_plan_revision_row(
            {
                "revision_id": str(decomposition_contract.get("current_revision_id") or uuid.uuid4().hex).strip(),
                "plan_version": version,
                "state": "current",
                "mode": mode,
                "artifact_ref": str(decomposition_contract.get("artifact_ref") or "").strip(),
                "rationale": str(decomposition_contract.get("rationale") or "").strip(),
                "decision_summary": str(dict(decomposition_contract.get("decision") or {}).get("decision_summary") or "").strip(),
                "plan_summary": dict(decomposition_contract.get("plan_summary") or {}),
                "trigger": {"kind": "initial_plan", "summary": "Initial durable plan snapshot."},
                "captured_utc": str(decomposition_contract.get("updated_utc") or _utc_now_iso()),
                "updated_utc": str(decomposition_contract.get("updated_utc") or _utc_now_iso()),
            }
        )
    ]


def _normalize_task_decomposition_contract(decomposition_contract: Dict[str, Any]) -> Dict[str, Any]:
    replan_state = str(dict(decomposition_contract.get("replanning") or {}).get("state") or "settled").strip() or "settled"
    mode = str(decomposition_contract.get("mode") or "").strip()
    revision_history_in = list(decomposition_contract.get("revision_history") or [])
    revision_history = (
        [_normalize_plan_revision_row(row) for row in revision_history_in if isinstance(row, dict)]
        if revision_history_in
        else _legacy_revision_history_from_contract(decomposition_contract)
    )
    normalized = {
        "schema_version": str(decomposition_contract.get("schema_version") or TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION).strip()
        or TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION,
        "linked_assumption_ids": [
            str(item).strip() for item in list(decomposition_contract.get("linked_assumption_ids") or []) if str(item).strip()
        ],
        "linked_acceptance_target_ids": [
            str(item).strip()
            for item in list(decomposition_contract.get("linked_acceptance_target_ids") or [])
            if str(item).strip()
        ],
        "revision_history": revision_history,
        "current_revision_id": str(decomposition_contract.get("current_revision_id") or "").strip(),
        "updated_utc": str(decomposition_contract.get("updated_utc") or _utc_now_iso()),
    }
    if replan_state == "settled":
        milestones = [
            _normalize_milestone_row(row)
            for row in list(decomposition_contract.get("milestones") or [])
            if isinstance(row, dict)
        ]
        plan_summary_in = dict(decomposition_contract.get("plan_summary") or {})
        checkpoint_count = sum(len(list(row.get("checkpoints") or [])) for row in milestones)
        dependency_mapping = None
        if decomposition_contract.get("dependency_mapping") and isinstance(decomposition_contract.get("dependency_mapping"), dict) and milestones:
            dependency_mapping = _normalize_dependency_mapping(
                dict(decomposition_contract.get("dependency_mapping") or {}),
                milestones=milestones,
            )
        dependency_counts = dict((dependency_mapping or {}).get("summary_counts") or {})
        current_revision_id = normalized["current_revision_id"] or (
            revision_history[-1]["revision_id"] if revision_history else uuid.uuid4().hex
        )
        normalized.update(
            {
                "replanning": {
                    "state": "settled",
                    "summary": "",
                    "invalidated_revision_id": "",
                    "invalidated_plan_version": 0,
                    "trigger": {},
                    "updated_utc": str(dict(decomposition_contract.get("replanning") or {}).get("updated_utc") or _utc_now_iso()),
                },
                "mode": mode,
                "rationale": str(decomposition_contract.get("rationale") or "").strip(),
                "decision": {
                    "estimated_step_count": int(dict(decomposition_contract.get("decision") or {}).get("estimated_step_count") or 0),
                    "risk_flags": [
                        str(item).strip()
                        for item in list(dict(decomposition_contract.get("decision") or {}).get("risk_flags") or [])
                        if str(item).strip()
                    ],
                    "requires_operator_checkpoint": bool(
                        dict(decomposition_contract.get("decision") or {}).get("requires_operator_checkpoint")
                    ),
                    "needs_ordering": bool(dict(decomposition_contract.get("decision") or {}).get("needs_ordering")),
                    "decision_summary": str(dict(decomposition_contract.get("decision") or {}).get("decision_summary") or "").strip(),
                },
                "plan_summary": {
                    "milestone_count": int(plan_summary_in.get("milestone_count") or len(milestones)),
                    "checkpoint_count": int(plan_summary_in.get("checkpoint_count") or checkpoint_count),
                    "dependency_edge_count": int(plan_summary_in.get("dependency_edge_count") or dependency_counts.get("dependency_edge_count") or 0),
                    "fanout_ready_count": int(plan_summary_in.get("fanout_ready_count") or dependency_counts.get("fanout_ready_count") or 0),
                    "forced_serial_count": int(plan_summary_in.get("forced_serial_count") or dependency_counts.get("forced_serial_count") or 0),
                    "fanout_group_count": int(plan_summary_in.get("fanout_group_count") or dependency_counts.get("fanout_group_count") or 0),
                    "active_milestone_id": str(plan_summary_in.get("active_milestone_id") or "").strip(),
                    "active_checkpoint_id": str(plan_summary_in.get("active_checkpoint_id") or "").strip(),
                    "direct_summary": str(plan_summary_in.get("direct_summary") or "").strip(),
                },
                "plan_version": int(decomposition_contract.get("plan_version") or 1),
                "artifact_ref": str(decomposition_contract.get("artifact_ref") or "").strip(),
                "milestones": milestones,
                "dependency_mapping": dependency_mapping,
                "current_revision_id": current_revision_id,
            }
        )
    else:
        replanning_in = dict(decomposition_contract.get("replanning") or {})
        normalized.update(
            {
                "replanning": {
                    "state": replan_state,
                    "summary": str(replanning_in.get("summary") or "").strip(),
                    "invalidated_revision_id": str(replanning_in.get("invalidated_revision_id") or "").strip(),
                    "invalidated_plan_version": int(replanning_in.get("invalidated_plan_version") or 0),
                    "trigger": _normalize_replan_trigger(
                        dict(replanning_in.get("trigger") or {}),
                        default_kind="changed_evidence",
                    ),
                    "updated_utc": str(replanning_in.get("updated_utc") or _utc_now_iso()),
                },
                "mode": "",
                "rationale": "",
                "decision": {},
                "plan_summary": {
                    "milestone_count": 0,
                    "checkpoint_count": 0,
                    "dependency_edge_count": 0,
                    "fanout_ready_count": 0,
                    "forced_serial_count": 0,
                    "fanout_group_count": 0,
                    "active_milestone_id": "",
                    "active_checkpoint_id": "",
                    "direct_summary": "",
                },
                "plan_version": 0,
                "artifact_ref": "",
                "milestones": [],
                "dependency_mapping": None,
                "current_revision_id": "",
            }
        )
    _validate_task_decomposition_contract("decomposition_contract", normalized)
    return normalized


def _acceptance_contract_stage(acceptance_contract: Dict[str, Any]) -> str:
    completion_state = dict(acceptance_contract.get("completion_state") or {})
    if bool(completion_state.get("operator_approved")):
        return "operator_approved"
    if bool(completion_state.get("evidence_complete")):
        return "evidence_complete"
    if bool(completion_state.get("implementation_complete")):
        return "implementation_complete"
    return "planned"


def _acceptance_contract_summary(task: Dict[str, Any]) -> str:
    acceptance_contract = dict(task.get("acceptance_contract") or {})
    if not acceptance_contract:
        return ""
    acceptance_targets = [row for row in list(acceptance_contract.get("acceptance_targets") or []) if bool(row.get("active"))]
    proof_targets = list(acceptance_contract.get("proof_targets") or [])
    pending_proof = [
        row for row in proof_targets if bool(row.get("required")) and str(row.get("status") or "").strip() not in {"satisfied", "waived"}
    ]
    review_gates = list(acceptance_contract.get("review_gates") or [])
    pending_review = [row for row in review_gates if str(row.get("status") or "").strip() not in {"approved", "waived"}]
    bits = [f"done={_acceptance_contract_stage(acceptance_contract)}"]
    if acceptance_targets:
        bits.append(f"acceptance={len(acceptance_targets)}")
    if proof_targets:
        bits.append(f"proof={len(proof_targets)}")
    if pending_proof:
        bits.append(f"proof_pending={len(pending_proof)}")
    if review_gates:
        bits.append(f"review_gates={len(review_gates)}")
    if pending_review:
        bits.append(f"review_pending={len(pending_review)}")
    return ", ".join(bits)


def _decomposition_contract_summary(task: Dict[str, Any]) -> str:
    decomposition_contract = dict(task.get("decomposition_contract") or {})
    if not decomposition_contract:
        return ""
    replanning = dict(decomposition_contract.get("replanning") or {})
    if str(replanning.get("state") or "settled").strip() != "settled":
        bits = [f"replan={str(replanning.get('state') or '').strip()}"]
        if str(replanning.get("summary") or "").strip():
            bits.append(f"summary={str(replanning.get('summary') or '').strip()}")
        return ", ".join(bits)
    plan_summary = dict(decomposition_contract.get("plan_summary") or {})
    mode = str(decomposition_contract.get("mode") or "").strip()
    bits = [f"plan={mode}"]
    if mode == "direct":
        if str(plan_summary.get("direct_summary") or "").strip():
            bits.append(f"direct={str(plan_summary.get('direct_summary') or '').strip()}")
    else:
        bits.append(f"milestones={int(plan_summary.get('milestone_count') or 0)}")
        bits.append(f"checkpoints={int(plan_summary.get('checkpoint_count') or 0)}")
        if int(plan_summary.get("dependency_edge_count") or 0) > 0:
            bits.append(f"edges={int(plan_summary.get('dependency_edge_count') or 0)}")
        if int(plan_summary.get("fanout_ready_count") or 0) > 0:
            bits.append(f"fanout_ready={int(plan_summary.get('fanout_ready_count') or 0)}")
        if int(plan_summary.get("forced_serial_count") or 0) > 0:
            bits.append(f"forced_serial={int(plan_summary.get('forced_serial_count') or 0)}")
        if str(plan_summary.get("active_milestone_id") or "").strip():
            bits.append(f"active_milestone={str(plan_summary.get('active_milestone_id') or '').strip()}")
    return ", ".join(bits)


def _scope_contract_summary(task: Dict[str, Any]) -> str:
    scope_contract = dict(task.get("scope_contract") or {})
    if not scope_contract:
        return ""
    assumptions = list(scope_contract.get("assumptions") or [])
    provisional = [
        row for row in assumptions if str(row.get("approval_state") or "").strip() in {"provisional", "needs_operator_confirmation"}
    ]
    confirmed = [row for row in assumptions if bool(row.get("operator_approved"))]
    constraints = [row for row in list(scope_contract.get("constraints") or []) if bool(row.get("active"))]
    non_goals = [row for row in list(scope_contract.get("non_goals") or []) if bool(row.get("active"))]
    current_scope = dict(scope_contract.get("current_scope") or {})
    bits = []
    if assumptions:
        bits.append(f"assumptions={len(assumptions)}")
    if confirmed:
        bits.append(f"confirmed={len(confirmed)}")
    if provisional:
        bits.append(f"provisional={len(provisional)}")
    if constraints:
        bits.append(f"constraints={len(constraints)}")
    if non_goals:
        bits.append(f"non_goals={len(non_goals)}")
    if str(current_scope.get("summary") or "").strip():
        bits.append(f"scope={str(current_scope.get('summary') or '').strip()}")
    return ", ".join(bits)


def _validate_open_loop(loop: Dict[str, Any]) -> None:
    loop_id = str(loop.get("loop_id") or "").strip()
    if not loop_id:
        raise TaskRegisterError("loop_id is required")
    kind = str(loop.get("kind") or "").strip()
    if kind not in OPEN_LOOP_ALLOWED_KINDS:
        raise TaskRegisterError(f"invalid open-loop kind: {kind or '<missing>'}")
    status = str(loop.get("status") or "").strip()
    if not status:
        raise TaskRegisterError(f"open loop {loop_id} status is required")


def _validate_register_obj(obj: Dict[str, Any]) -> None:
    schema = str(obj.get("schema_version") or "").strip()
    if schema != TASK_REGISTER_SCHEMA_VERSION:
        raise TaskRegisterError(f"incompatible schema_version: {schema or '<missing>'}")
    register_id = str(obj.get("register_id") or "").strip()
    if not register_id:
        raise TaskRegisterError("register_id is required")
    generation = obj.get("write_generation")
    if not isinstance(generation, int) or generation < 0:
        raise TaskRegisterError("write_generation must be a non-negative integer")
    tasks = obj.get("active_tasks")
    completed_tasks = obj.get("completed_tasks")
    residue_tasks = obj.get("residue_tasks")
    loops = obj.get("open_loops")
    constraints = obj.get("constraints")
    refs = obj.get("recent_execution_refs")
    if (
        not isinstance(tasks, list)
        or not isinstance(completed_tasks, list)
        or not isinstance(residue_tasks, list)
        or not isinstance(loops, list)
        or not isinstance(constraints, list)
        or not isinstance(refs, list)
    ):
        raise TaskRegisterError("task register collections must all be lists")
    for task in tasks:
        if not isinstance(task, dict):
            raise TaskRegisterError("active_tasks rows must be objects")
        _validate_task(task)
    for task in completed_tasks:
        if not isinstance(task, dict):
            raise TaskRegisterError("completed_tasks rows must be objects")
        _validate_task(task)
    for task in residue_tasks:
        if not isinstance(task, dict):
            raise TaskRegisterError("residue_tasks rows must be objects")
        _validate_task(task)
    for loop in loops:
        if not isinstance(loop, dict):
            raise TaskRegisterError("open_loops rows must be objects")
        _validate_open_loop(loop)


def _append_event(repo_root: Path, *, event_type: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        _events_path(repo_root),
        {
            "schema_version": TASK_REGISTER_EVENT_SCHEMA_VERSION,
            "event_id": uuid.uuid4().hex,
            "event": str(event_type),
            "utc": _utc_now_iso(),
            "payload": dict(payload),
        },
    )


def _empty_register(*, repo_root: Path, assistant_id: str) -> Dict[str, Any]:
    now = _utc_now_iso()
    return {
        "schema_version": TASK_REGISTER_SCHEMA_VERSION,
        "register_id": f"task_register_{uuid.uuid4().hex}",
        "assistant_id_ref": str(assistant_id),
        "write_generation": 0,
        "last_writer": "task_register.initialize",
        "last_written_utc": now,
        "active_tasks": [],
        "completed_tasks": [],
        "residue_tasks": [],
        "open_loops": [],
        "constraints": [],
        "recent_execution_refs": [],
        "summary_cache_ref": _summary_cache_ref(repo_root),
        "summary_stale": False,
    }


def _normalize_task(task: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now_iso()
    out = {
        "task_id": str(task.get("task_id") or "").strip(),
        "kind": str(task.get("kind") or "").strip(),
        "title": str(task.get("title") or "").strip(),
        "status": str(task.get("status") or "").strip(),
        "owner": str(task.get("owner") or "").strip(),
        "source": str(task.get("source") or "").strip(),
        "intake_path": _normalize_task_intake_path(task.get("intake_path"), source=str(task.get("source") or "")),
        "ledger_uid": str(task.get("ledger_uid") or "").strip(),
        "delegation_refs": [_normalize_delegation_ref(ref) for ref in list(task.get("delegation_refs") or [])],
        "next_expected_action": str(task.get("next_expected_action") or "").strip(),
        "blocked_reason": str(task.get("blocked_reason") or "").strip(),
        "created_utc": str(task.get("created_utc") or now),
        "updated_utc": str(task.get("updated_utc") or now),
    }
    if task.get("task_runs") is not None:
        normalized_runs = _normalize_task_runs(str(task.get("task_id") or "").strip(), list(task.get("task_runs") or []), now=str(task.get("updated_utc") or now))
        out["task_runs"] = normalized_runs
        current_run_id = str(task.get("current_run_id") or "").strip()
        if current_run_id:
            out["current_run_id"] = current_run_id
        elif normalized_runs:
            latest = _pick_latest_task_run(normalized_runs)
            if latest is not None:
                out["current_run_id"] = str(latest.get("run_id") or "")
    if task.get("request_fingerprint") is not None:
        out["request_fingerprint"] = str(task.get("request_fingerprint") or "").strip()
    if task.get("request_fingerprint_version") is not None:
        out["request_fingerprint_version"] = str(task.get("request_fingerprint_version") or "").strip()
    if task.get("intake") is not None:
        intake = dict(task.get("intake") or {})
        normalized_missing_information = []
        for row in list(intake.get("missing_information") or []):
            normalized_missing_information.append(
                {
                    "field": str(row.get("field") or "").strip(),
                    "question": str(row.get("question") or "").strip(),
                    "reason": str(row.get("reason") or "").strip(),
                    "required": bool(row.get("required")),
                }
            )
        out["intake"] = {
            "schema_version": str(intake.get("schema_version") or TASK_INTAKE_SCHEMA_VERSION).strip() or TASK_INTAKE_SCHEMA_VERSION,
            "raw_request_text": str(intake.get("raw_request_text") or "").strip(),
            "inferred_intent": str(intake.get("inferred_intent") or "").strip(),
            "work_class": str(intake.get("work_class") or "").strip(),
            "work_state": str(intake.get("work_state") or "").strip(),
            "readiness": str(intake.get("readiness") or "").strip(),
            "confidence": float(intake.get("confidence") or 0.0),
            "summary": str(intake.get("summary") or "").strip(),
            "missing_information": normalized_missing_information,
            "captured_utc": str(intake.get("captured_utc") or now),
            "updated_utc": str(intake.get("updated_utc") or now),
        }
    if task.get("scope_contract") is not None:
        out["scope_contract"] = _normalize_task_scope_contract(dict(task.get("scope_contract") or {}))
    if task.get("acceptance_contract") is not None:
        out["acceptance_contract"] = _normalize_task_acceptance_contract(dict(task.get("acceptance_contract") or {}))
    if task.get("decomposition_contract") is not None:
        out["decomposition_contract"] = _normalize_task_decomposition_contract(dict(task.get("decomposition_contract") or {}))
    if task.get("routing_decision") is not None:
        out["routing_decision"] = _normalize_routing_decision(dict(task.get("routing_decision") or {}))
    if task.get("merge_status") is not None:
        out["merge_status"] = str(task.get("merge_status") or "")
    if task.get("conflict_fields") is not None:
        out["conflict_fields"] = list(task.get("conflict_fields") or [])
    if task.get("execution_surface_ownership") is not None:
        out["execution_surface_ownership"] = _normalize_execution_surface_ownership(task.get("execution_surface_ownership") or {})
        out["execution_surface_ownership"]["updated_utc"] = _normalize_datetime_value(
            str(task.get("execution_surface_ownership", {}).get("updated_utc") or now)
        )
    if task.get("delegated_outcome_writeback") is not None:
        out["delegated_outcome_writeback"] = dict(task.get("delegated_outcome_writeback") or {})
    if "current_run_id" in task and "current_run_id" not in out:
        explicit_current = str(task.get("current_run_id") or "").strip()
        if explicit_current:
            out["current_run_id"] = explicit_current
    _validate_task(out)
    return out


def _run_evidence_refs_for_execution_surface(*, execution_id: str, execution_ref: str, binding_ref: str, job_uri: str, trace_id: str) -> List[str]:
    refs = _normalize_task_run_evidence_refs([execution_id, execution_ref, binding_ref, trace_id, job_uri])
    return refs


def _run_state_from_reference_status(reference_status: str, existing_state: str = "") -> str:
    normalized = str(reference_status or "").strip()
    if normalized == "execution_ref_active":
        return "running"
    if normalized == "execution_ref_stale":
        return "stale"
    if normalized == "execution_ref_conflict":
        return "conflict"
    existing = str(existing_state or "").strip().lower()
    return existing if existing in TASK_RUN_STATES else "requested"


def _build_task_run_row(
    *,
    task_id: str,
    run_id: str,
    candidate_owner: Dict[str, Any],
    reference_status: str,
    last_writer: str,
    run_evidence_refs: List[str],
    checkpoint: Optional[Dict[str, Any]] = None,
    resume_decision: Optional[Dict[str, Any]] = None,
    existing_row: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    has_existing = bool(existing_row and isinstance(existing_row, dict))
    existing = dict(existing_row) if has_existing else {}
    existing_state = str(existing.get("run_state") or "").strip()
    run_state = _run_state_from_reference_status(reference_status, existing_state=existing_state)
    checkpoint_payload = checkpoint if isinstance(checkpoint, dict) else dict(existing.get("checkpoint") or {})
    resume_payload = resume_decision if isinstance(resume_decision, dict) else dict(existing.get("resume_decision") or {})
    normalized_checkpoint = _normalize_task_run_checkpoint(checkpoint_payload) if checkpoint_payload else {}
    normalized_resume = _normalize_task_run_resume_decision(resume_payload) if resume_payload else {}
    normalized_interventions = _normalize_task_run_interventions(existing.get("interventions") or [])
    out = {
        "schema_version": TASK_RUN_RECORD_SCHEMA_VERSION,
        "run_id": run_id,
        "task_id": task_id,
        "run_state": run_state,
        "attention_state": _run_state_to_attention_state(run_state),
        "surface": str(candidate_owner.get("selected_surface") or "other").strip() or _normalize_execution_surface("other"),
        "execution_id": str(candidate_owner.get("execution_id") or "").strip(),
        "execution_ref": str(candidate_owner.get("execution_ref") or "").strip(),
        "binding_ref": str(candidate_owner.get("binding_ref") or "").strip(),
        "job_uri": str(candidate_owner.get("job_uri") or "").strip(),
        "trace_id": str(candidate_owner.get("trace_id") or "").strip(),
        "evidence_refs": _normalize_task_run_evidence_refs(
            _normalize_task_run_evidence_refs(existing.get("evidence_refs") or []) + run_evidence_refs
        ),
        "checkpoint": normalized_checkpoint,
        "resume_decision": normalized_resume,
        "interventions": normalized_interventions,
        "created_utc": str(existing.get("created_utc") or _utc_now_iso()),
        "updated_utc": _utc_now_iso(),
        "writer": str(last_writer or "").strip(),
        "revision": int(_int_or_default(existing.get("revision"), default=0)) + 1,
        "run_status_source": "execution_surface_ownership",
    }
    return out


def _classify_task_run_intervention(
    *,
    run_row: Dict[str, Any],
    action: str,
    reason: str,
    requested_by: str,
    supersedes_intervention_id: str = "",
) -> Dict[str, Any]:
    normalized_action = _clean_text(action).lower()
    if normalized_action not in TASK_RUN_INTERVENTION_ACTIONS:
        raise TaskRegisterError(f"task run intervention action is invalid: {normalized_action}")
    latest = _latest_task_run_intervention(run_row)
    run_state = _clean_text(run_row.get("run_state")).lower()
    outcome_state = "accepted"
    outcome_reason = "pending_execution_surface_application"
    conflict_with = ""

    if latest and _clean_text(latest.get("outcome_state")).lower() == "accepted" and normalized_action != _clean_text(latest.get("action")).lower():
        outcome_state = "conflict"
        outcome_reason = "existing_unsettled_intervention"
        conflict_with = _clean_text(latest.get("intervention_id"))
    elif normalized_action == "pause" and run_state not in {"requested", "active", "running", "waiting"}:
        outcome_state = "blocked"
        outcome_reason = "pause_requires_in_flight_run"
    elif normalized_action == "resume" and run_state not in {"requested", "active", "running", "waiting", "blocked", "conflict"}:
        outcome_state = "blocked"
        outcome_reason = "resume_requires_resumable_run"
    elif normalized_action == "cancel" and run_state not in {"requested", "active", "running", "waiting", "blocked", "conflict"}:
        outcome_state = "blocked"
        outcome_reason = "cancel_requires_non_terminal_run"
    elif normalized_action == "retry" and run_state not in {"failed", "stale", "blocked", "conflict"}:
        outcome_state = "blocked"
        outcome_reason = "retry_requires_failed_or_blocked_run"
    elif normalized_action == "rebind" and run_state not in {"failed", "stale", "blocked", "conflict"}:
        outcome_state = "blocked"
        outcome_reason = "rebind_requires_stale_or_conflicted_run"
    elif normalized_action == "repair" and run_state not in {"failed", "stale", "blocked", "conflict"}:
        outcome_state = "blocked"
        outcome_reason = "repair_requires_failed_or_conflicted_run"

    requested_utc = _utc_now_iso()
    return _normalize_task_run_intervention(
        {
            "intervention_id": f"intv_{uuid.uuid4().hex}",
            "action": normalized_action,
            "requested_utc": requested_utc,
            "requested_by": requested_by,
            "reason": reason,
            "outcome_state": outcome_state,
            "outcome_reason": outcome_reason,
            "resolved_utc": requested_utc if outcome_state in {"blocked", "conflict"} else "",
            "supersedes_intervention_id": supersedes_intervention_id,
            "conflict_with_intervention_id": conflict_with,
            "evidence_refs": [],
        }
    )


def _write_task_run_row(
    task: Dict[str, Any],
    *,
    selected_owner: Dict[str, Any],
    last_writer: str,
    checkpoint: Optional[Dict[str, Any]] = None,
    resume_decision: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    selected_owner = dict(selected_owner)
    task_runs = [dict(row) for row in list(task.get("task_runs") or [])]
    requested_run_id = str(selected_owner.get("run_id") or "").strip()
    selected_surface = str(selected_owner.get("selected_surface") or "").strip()
    selected_status = str(selected_owner.get("reference_status") or "").strip()
    current_run_id = str(task.get("current_run_id") or "").strip()
    current_run: Optional[Dict[str, Any]] = None
    if current_run_id:
        for row in task_runs:
            if str(row.get("run_id") or "").strip() == current_run_id:
                current_run = row
                break

    execution_id = str(selected_owner.get("execution_id") or "").strip()
    if requested_run_id:
        candidate_run_id = requested_run_id
    elif execution_id and execution_id != current_run_id:
        candidate_run_id = execution_id
    elif current_run and selected_surface == str(current_run.get("surface") or "").strip():
        candidate_run_id = current_run_id
    elif current_run and selected_status in {"execution_ref_stale", "execution_ref_unresolved"}:
        candidate_run_id = current_run_id
    elif execution_id:
        candidate_run_id = execution_id
    elif current_run_id:
        run_id = requested_run_id
        candidate_run_id = current_run_id
    elif task_runs:
        latest = _pick_latest_task_run(task_runs)
        current_run = dict(latest or {})
        candidate_run_id = str(current_run.get("run_id") or "")
    else:
        candidate_run_id = f"run_{uuid.uuid4().hex}"
        current_run = None

    if not candidate_run_id:
        candidate_run_id = f"run_{uuid.uuid4().hex}"

    existing_row = next((dict(row) for row in task_runs if str(row.get("run_id") or "").strip() == candidate_run_id), None)
    if existing_row is not None:
        current_run = existing_row

    run_evidence_refs = _run_evidence_refs_for_execution_surface(
        execution_id=str(selected_owner.get("execution_id") or "").strip(),
        execution_ref=str(selected_owner.get("execution_ref") or "").strip(),
        binding_ref=str(selected_owner.get("binding_ref") or "").strip(),
        job_uri=str(selected_owner.get("job_uri") or "").strip(),
        trace_id=str(selected_owner.get("trace_id") or "").strip(),
    )
    next_run = _build_task_run_row(
        task_id=str(task.get("task_id") or "").strip(),
        run_id=candidate_run_id,
        candidate_owner=selected_owner,
        reference_status=str(selected_owner.get("reference_status") or "").strip(),
        last_writer=last_writer,
        run_evidence_refs=run_evidence_refs,
        checkpoint=checkpoint,
        resume_decision=resume_decision,
        existing_row=current_run,
    )
    task_runs = [dict(row) for row in task_runs if str(row.get("run_id") or "").strip() != candidate_run_id]
    if next_run is not None:
        task_runs.append(next_run)
        task["current_run_id"] = candidate_run_id
    task["task_runs"] = task_runs
    return task


def _normalize_open_loop(loop: Dict[str, Any]) -> Dict[str, Any]:
    now = _utc_now_iso()
    out = {
        "loop_id": str(loop.get("loop_id") or "").strip(),
        "kind": str(loop.get("kind") or "").strip(),
        "status": str(loop.get("status") or "").strip(),
        "linked_task_id": str(loop.get("linked_task_id") or "").strip(),
        "linked_ledger_uid": str(loop.get("linked_ledger_uid") or "").strip(),
        "note": str(loop.get("note") or "").strip(),
        "next_review_utc": str(loop.get("next_review_utc") or ""),
        "created_utc": str(loop.get("created_utc") or now),
        "updated_utc": str(loop.get("updated_utc") or now),
        "resolved_utc": str(loop.get("resolved_utc") or ""),
    }
    _validate_open_loop(out)
    return out


def _normalize_constraint(constraint: Dict[str, Any]) -> Dict[str, Any]:
    key = str(constraint.get("key") or "").strip()
    if not key:
        raise TaskRegisterError("constraint key is required")
    return {
        "key": key,
        "value": str(constraint.get("value") or "").strip(),
        "scope": str(constraint.get("scope") or "global").strip(),
        "source": str(constraint.get("source") or "").strip(),
        "updated_utc": str(constraint.get("updated_utc") or _utc_now_iso()),
    }
