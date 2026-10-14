def _apply_task_run_record_event(register: Dict[str, Any], payload: Dict[str, Any]) -> None:
    task_id = str(payload.get("task_id") or "").strip()
    if not task_id:
        raise TaskRegisterError("task_run_recorded payload is missing task_id")
    run_id = str(payload.get("run_id") or "").strip()
    if not run_id:
        raise TaskRegisterError(f"task {task_id} task_run_recorded payload is missing run_id")
    if not _TASK_RUN_ID_RE.match(run_id):
        raise TaskRegisterError(f"task {task_id} task_run_recorded has invalid run_id: {run_id}")
    current_run_id = str(payload.get("current_run_id") or "").strip()
    if current_run_id and current_run_id != run_id:
        raise TaskRegisterError(
            f"task {task_id} task_run_recorded current_run_id {current_run_id} does not match run_id {run_id}"
        )
    current_run_id = run_id
    run_row_payload = payload.get("run_row")
    if not isinstance(run_row_payload, dict):
        raise TaskRegisterError(f"task {task_id} task_run_recorded payload missing run_row")
    run_row = _normalize_task_run(dict(run_row_payload), task_id=task_id, now=_utc_now_iso())
    _validate_task_run_record(task_id=task_id, row=run_row)
    if int(_int_or_default(payload.get("run_revision"), default=-1)) != int(_int_or_default(run_row.get("revision"), default=-1)):
        raise TaskRegisterError(f"task {task_id} task_run_recorded run_revision mismatch")
    if str(task_id) != str(run_row.get("task_id") or "").strip():
        raise TaskRegisterError(f"task {task_id} task_run_recorded run_row task_id mismatch")
    if str(run_row.get("run_id") or "").strip() != run_id:
        raise TaskRegisterError(f"task {task_id} task_run_recorded run_row/run_id mismatch")

    tasks = list(register.get("active_tasks") or [])
    target_index = next((index for index, row in enumerate(tasks) if str(row.get("task_id") or "").strip() == task_id), None)
    if target_index is None:
        snapshot_payload = payload.get("task_snapshot") if isinstance(payload.get("task_snapshot"), dict) else {}
        recovered = _normalize_recovered_task_from_run_snapshot(task_id, task_snapshot=snapshot_payload, run_row=run_row)
        tasks.append(recovered)
        target_index = len(tasks) - 1
        register["active_tasks"] = tasks

    task = dict(tasks[target_index])
    task_runs = [dict(row) for row in list(task.get("task_runs") or [])]

    existing = next((row for row in task_runs if str(row.get("run_id") or "").strip() == run_id), None)
    incoming_revision = int(_int_or_default(run_row.get("revision"), default=0))
    if existing is not None:
        existing_revision = int(_int_or_default(existing.get("revision"), default=-1))
        if incoming_revision < existing_revision:
            raise TaskRegisterError(
                f"task {task_id} run {run_id} run_event has stale revision {incoming_revision} < {existing_revision}"
            )
        if incoming_revision == existing_revision:
            if str(_normalize_datetime_value(run_row.get("updated_utc"))) <= str(
                _normalize_datetime_value(existing.get("updated_utc"))
            ):
                task["task_runs"] = _normalize_task_runs(task_id=task_id, task_runs=task_runs, now=_utc_now_iso())
                _validate_task_run_linkage(
                    task_id=task_id, task_runs=task["task_runs"], current_run_id=current_run_id
                )
                tasks[target_index] = task
                register["active_tasks"] = tasks
                return
        task_runs = [row for row in task_runs if str(row.get("run_id") or "").strip() != run_id]

    task_runs.append(run_row)
    task["task_runs"] = _normalize_task_runs(task_id=task_id, task_runs=task_runs, now=_utc_now_iso())
    task["current_run_id"] = current_run_id
    _validate_task_run_linkage(task_id=task_id, task_runs=task["task_runs"], current_run_id=run_id)
    tasks[target_index] = task
    register["active_tasks"] = tasks


def _validate_task_run_linkage(task_id: str, task_runs: List[Dict[str, Any]], *, current_run_id: str) -> None:
    if not current_run_id:
        return
    known_runs = _task_runs_index(task_runs)
    if current_run_id not in known_runs:
        raise TaskRegisterError(f"task {task_id} current_run_id does not reference a known task_run")



def _validate_execution_surface_ownership(task_id: str, row: Dict[str, Any]) -> None:
    schema_version = str(row.get("schema_version") or "").strip()
    if schema_version and schema_version != TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION:
        raise TaskRegisterError(
            f"task {task_id} execution_surface_ownership has incompatible schema_version: {schema_version or '<missing>'}"
        )
    selected_surface = str(row.get("selected_surface") or "").strip()
    if selected_surface not in TASK_EXECUTION_SURFACES:
        raise TaskRegisterError(
            f"task {task_id} execution_surface_ownership selected_surface must be one of "
            f"{', '.join(sorted(TASK_EXECUTION_SURFACES))}"
        )
    if not str(row.get("owner_uid") or "").strip():
        raise TaskRegisterError(f"task {task_id} execution_surface_ownership owner_uid is required")
    reference_status = str(row.get("reference_status") or "").strip()
    if reference_status == "execution_ref_active" and not _execution_refs_complete(row):
        raise TaskRegisterError(
            f"task {task_id} execution_surface_ownership requires all reference fields when reference_status is active"
        )
    if str(row.get("reference_status") or "").strip() not in TASK_EXECUTION_REFERENCE_STATUSES:
        raise TaskRegisterError(
            f"task {task_id} execution_surface_ownership reference_status must be one of "
            f"{', '.join(sorted(TASK_EXECUTION_REFERENCE_STATUSES))}"
        )
    if not isinstance(_int_or_default(row.get("reference_revision"), default=0), int):
        raise TaskRegisterError(f"task {task_id} execution_surface_ownership reference_revision must be an integer")


def _validate_task(task: Dict[str, Any]) -> None:
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        raise TaskRegisterError("task_id is required")
    title = str(task.get("title") or "").strip()
    if not title:
        raise TaskRegisterError(f"task {task_id} title is required")
    status = str(task.get("status") or "").strip()
    if not status:
        raise TaskRegisterError(f"task {task_id} status is required")
    owner = str(task.get("owner") or "").strip()
    if not owner:
        raise TaskRegisterError(f"task {task_id} owner is required")
    next_expected_action = str(task.get("next_expected_action") or "").strip()
    if not next_expected_action:
        raise TaskRegisterError(f"task {task_id} next_expected_action is required")
    intake_path = str(task.get("intake_path") or "").strip()
    if not intake_path:
        raise TaskRegisterError(f"task {task_id} intake_path is required")
    if intake_path not in TASK_INTAKE_PATH_CLASSIFICATIONS:
        raise TaskRegisterError(f"task {task_id} intake_path is invalid: {intake_path}")
    if str(task.get("source") or "").strip() == "operator_intake" and intake_path != "canonical":
        raise TaskRegisterError(f"task {task_id} canonical source requires intake_path=canonical")
    if intake_path == "forbidden":
        raise TaskRegisterError(f"task {task_id} intake_path=forbidden writes are blocked")
    delegation_refs = task.get("delegation_refs")
    if not isinstance(delegation_refs, list):
        raise TaskRegisterError(f"task {task_id} delegation_refs must be a list")
    for ref in delegation_refs:
        if not isinstance(ref, dict):
            raise TaskRegisterError(f"task {task_id} delegation_refs rows must be objects")
        _validate_delegation_ref(ref)
    routing_decision = task.get("routing_decision")
    if routing_decision is not None:
        if not isinstance(routing_decision, dict):
            raise TaskRegisterError(f"task {task_id} routing_decision must be an object")
        _validate_routing_decision(task_id, routing_decision)
    execution_surface_ownership = task.get("execution_surface_ownership")
    if execution_surface_ownership is not None:
        if not isinstance(execution_surface_ownership, dict):
            raise TaskRegisterError(f"task {task_id} execution_surface_ownership must be an object")
        _validate_execution_surface_ownership(task_id, dict(execution_surface_ownership))
    current_run_id = str(task.get("current_run_id") or "").strip()
    if current_run_id and not _TASK_RUN_ID_RE.match(current_run_id):
        raise TaskRegisterError(f"task {task_id} current_run_id is invalid")
    task_runs = task.get("task_runs")
    if task_runs is not None:
        if not isinstance(task_runs, list):
            raise TaskRegisterError(f"task {task_id} task_runs must be a list")
        for row in task_runs:
            if not isinstance(row, dict):
                raise TaskRegisterError(f"task {task_id} task_runs rows must be objects")
            _validate_task_run_record(task_id, row)
        if current_run_id and not task_runs:
            raise TaskRegisterError(f"task {task_id} current_run_id must reference a known task_run")
        _validate_task_run_linkage(task_id, list(task_runs), current_run_id=str(task.get("current_run_id") or "").strip())
    intake = task.get("intake")
    if intake is not None:
        if not isinstance(intake, dict):
            raise TaskRegisterError(f"task {task_id} intake must be an object")
        _validate_task_intake(task_id, intake)
    scope_contract = task.get("scope_contract")
    if scope_contract is not None:
        if not isinstance(scope_contract, dict):
            raise TaskRegisterError(f"task {task_id} scope_contract must be an object")
        _validate_task_scope_contract(task_id, scope_contract)
    acceptance_contract = task.get("acceptance_contract")
    if acceptance_contract is not None:
        if not isinstance(acceptance_contract, dict):
            raise TaskRegisterError(f"task {task_id} acceptance_contract must be an object")
        _validate_task_acceptance_contract(task_id, acceptance_contract)
    decomposition_contract = task.get("decomposition_contract")
    if decomposition_contract is not None:
        if not isinstance(decomposition_contract, dict):
            raise TaskRegisterError(f"task {task_id} decomposition_contract must be an object")
        _validate_task_decomposition_contract(task_id, decomposition_contract)


def _validate_task_intake(task_id: str, intake: Dict[str, Any]) -> None:
    schema = str(intake.get("schema_version") or "").strip()
    if schema and schema != TASK_INTAKE_SCHEMA_VERSION:
        raise TaskRegisterError(f"task {task_id} intake has incompatible schema_version: {schema}")
    raw_request_text = str(intake.get("raw_request_text") or "").strip()
    inferred_intent = str(intake.get("inferred_intent") or "").strip()
    work_class = str(intake.get("work_class") or "").strip()
    work_state = str(intake.get("work_state") or "").strip()
    readiness = str(intake.get("readiness") or "").strip()
    summary = str(intake.get("summary") or "").strip()
    confidence = intake.get("confidence")
    missing_information = intake.get("missing_information")
    if not raw_request_text:
        raise TaskRegisterError(f"task {task_id} intake raw_request_text is required")
    if not inferred_intent:
        raise TaskRegisterError(f"task {task_id} intake inferred_intent is required")
    if not work_class:
        raise TaskRegisterError(f"task {task_id} intake work_class is required")
    if work_state not in TASK_INTAKE_WORK_STATES:
        raise TaskRegisterError(f"task {task_id} intake invalid work_state: {work_state or '<missing>'}")
    if readiness not in TASK_INTAKE_READINESS_STATES:
        raise TaskRegisterError(f"task {task_id} intake invalid readiness: {readiness or '<missing>'}")
    if not summary:
        raise TaskRegisterError(f"task {task_id} intake summary is required")
    if not isinstance(confidence, (int, float)):
        raise TaskRegisterError(f"task {task_id} intake confidence must be numeric")
    if float(confidence) < 0.0 or float(confidence) > 1.0:
        raise TaskRegisterError(f"task {task_id} intake confidence must be between 0 and 1")
    if not isinstance(missing_information, list):
        raise TaskRegisterError(f"task {task_id} intake missing_information must be a list")
    for row in missing_information:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} intake missing_information rows must be objects")
        field = str(row.get("field") or "").strip()
        question = str(row.get("question") or "").strip()
        reason = str(row.get("reason") or "").strip()
        required = row.get("required")
        if not field or not question or not reason:
            raise TaskRegisterError(f"task {task_id} intake missing_information rows require field, question, and reason")
        if not isinstance(required, bool):
            raise TaskRegisterError(f"task {task_id} intake missing_information.required must be boolean")


def _validate_scope_assumption(task_id: str, row: Dict[str, Any]) -> None:
    assumption_id = str(row.get("assumption_id") or "").strip()
    statement = str(row.get("statement") or "").strip()
    kind = str(row.get("kind") or "").strip()
    approval_state = str(row.get("approval_state") or "").strip()
    rationale = str(row.get("rationale") or "").strip()
    if not assumption_id:
        raise TaskRegisterError(f"task {task_id} scope assumption_id is required")
    if not statement:
        raise TaskRegisterError(f"task {task_id} assumption {assumption_id} statement is required")
    if kind not in TASK_SCOPE_ASSUMPTION_KINDS:
        raise TaskRegisterError(f"task {task_id} assumption {assumption_id} invalid kind: {kind or '<missing>'}")
    if approval_state not in TASK_SCOPE_ASSUMPTION_APPROVAL_STATES:
        raise TaskRegisterError(
            f"task {task_id} assumption {assumption_id} invalid approval_state: {approval_state or '<missing>'}"
        )
    if not rationale:
        raise TaskRegisterError(f"task {task_id} assumption {assumption_id} rationale is required")
    operator_approved = row.get("operator_approved")
    requires_confirmation = row.get("requires_operator_confirmation")
    blocking = row.get("blocking")
    if not isinstance(operator_approved, bool):
        raise TaskRegisterError(f"task {task_id} assumption {assumption_id} operator_approved must be boolean")
    if not isinstance(requires_confirmation, bool):
        raise TaskRegisterError(
            f"task {task_id} assumption {assumption_id} requires_operator_confirmation must be boolean"
        )
    if not isinstance(blocking, bool):
        raise TaskRegisterError(f"task {task_id} assumption {assumption_id} blocking must be boolean")
    if operator_approved and approval_state != "operator_confirmed":
        raise TaskRegisterError(
            f"task {task_id} assumption {assumption_id} cannot be operator-approved without operator_confirmed state"
        )
    if approval_state == "operator_confirmed" and not operator_approved:
        raise TaskRegisterError(
            f"task {task_id} assumption {assumption_id} operator_confirmed assumptions must be operator-approved"
        )
    if approval_state == "operator_confirmed" and str(row.get("confirmed_by") or "").strip().lower() != "operator":
        raise TaskRegisterError(
            f"task {task_id} assumption {assumption_id} operator_confirmed assumptions must name confirmed_by=operator"
        )
    if requires_confirmation and approval_state not in {"provisional", "needs_operator_confirmation"}:
        raise TaskRegisterError(
            f"task {task_id} assumption {assumption_id} confirmation-required assumptions must stay provisional"
        )


def _validate_scope_constraint(task_id: str, row: Dict[str, Any]) -> None:
    constraint_id = str(row.get("constraint_id") or "").strip()
    statement = str(row.get("statement") or "").strip()
    if not constraint_id:
        raise TaskRegisterError(f"task {task_id} scope constraint_id is required")
    if not statement:
        raise TaskRegisterError(f"task {task_id} constraint {constraint_id} statement is required")
    if not isinstance(row.get("active"), bool):
        raise TaskRegisterError(f"task {task_id} constraint {constraint_id} active must be boolean")


def _validate_scope_non_goal(task_id: str, row: Dict[str, Any]) -> None:
    non_goal_id = str(row.get("non_goal_id") or "").strip()
    statement = str(row.get("statement") or "").strip()
    if not non_goal_id:
        raise TaskRegisterError(f"task {task_id} scope non_goal_id is required")
    if not statement:
        raise TaskRegisterError(f"task {task_id} non-goal {non_goal_id} statement is required")
    if not isinstance(row.get("active"), bool):
        raise TaskRegisterError(f"task {task_id} non-goal {non_goal_id} active must be boolean")


def _validate_scope_revision(task_id: str, row: Dict[str, Any]) -> None:
    revision_id = str(row.get("revision_id") or "").strip()
    summary = str(row.get("summary") or "").strip()
    rationale = str(row.get("rationale") or "").strip()
    if not revision_id:
        raise TaskRegisterError(f"task {task_id} scope revision_id is required")
    if not summary:
        raise TaskRegisterError(f"task {task_id} scope revision {revision_id} summary is required")
    if not rationale:
        raise TaskRegisterError(f"task {task_id} scope revision {revision_id} rationale is required")
    for key in ("in_scope", "out_of_scope"):
        if not isinstance(row.get(key), list):
            raise TaskRegisterError(f"task {task_id} scope revision {revision_id} {key} must be a list")


def _validate_task_scope_contract(task_id: str, scope_contract: Dict[str, Any]) -> None:
    schema = str(scope_contract.get("schema_version") or "").strip()
    if schema and schema != TASK_SCOPE_CONTRACT_SCHEMA_VERSION:
        raise TaskRegisterError(f"task {task_id} scope_contract has incompatible schema_version: {schema}")
    assumptions = scope_contract.get("assumptions")
    constraints = scope_contract.get("constraints")
    non_goals = scope_contract.get("non_goals")
    scope_history = scope_contract.get("scope_history")
    current_scope = scope_contract.get("current_scope")
    for key, value in (
        ("assumptions", assumptions),
        ("constraints", constraints),
        ("non_goals", non_goals),
        ("scope_history", scope_history),
    ):
        if not isinstance(value, list):
            raise TaskRegisterError(f"task {task_id} scope_contract {key} must be a list")
    if current_scope is not None and not isinstance(current_scope, dict):
        raise TaskRegisterError(f"task {task_id} scope_contract current_scope must be an object")
    for row in assumptions:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} scope_contract assumptions rows must be objects")
        _validate_scope_assumption(task_id, row)
    for row in constraints:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} scope_contract constraints rows must be objects")
        _validate_scope_constraint(task_id, row)
    for row in non_goals:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} scope_contract non_goals rows must be objects")
        _validate_scope_non_goal(task_id, row)
    for row in scope_history:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} scope_contract scope_history rows must be objects")
        _validate_scope_revision(task_id, row)
    if current_scope:
        _validate_scope_revision(task_id, current_scope)


def _validate_acceptance_target(task_id: str, row: Dict[str, Any]) -> None:
    target_id = str(row.get("target_id") or "").strip()
    statement = str(row.get("statement") or "").strip()
    if not target_id:
        raise TaskRegisterError(f"task {task_id} acceptance target_id is required")
    if not statement:
        raise TaskRegisterError(f"task {task_id} acceptance target {target_id} statement is required")
    if not isinstance(row.get("active"), bool):
        raise TaskRegisterError(f"task {task_id} acceptance target {target_id} active must be boolean")


def _validate_proof_target(task_id: str, row: Dict[str, Any]) -> None:
    proof_id = str(row.get("proof_id") or "").strip()
    statement = str(row.get("statement") or "").strip()
    proof_class = str(row.get("proof_class") or "").strip()
    status = str(row.get("status") or "").strip()
    required = row.get("required")
    blocking = row.get("blocking")
    if not proof_id:
        raise TaskRegisterError(f"task {task_id} proof target proof_id is required")
    if not statement:
        raise TaskRegisterError(f"task {task_id} proof target {proof_id} statement is required")
    if proof_class not in TASK_PROOF_TARGET_CLASSES:
        raise TaskRegisterError(f"task {task_id} proof target {proof_id} invalid proof_class: {proof_class or '<missing>'}")
    if status not in TASK_PROOF_TARGET_STATES:
        raise TaskRegisterError(f"task {task_id} proof target {proof_id} invalid status: {status or '<missing>'}")
    if not isinstance(required, bool):
        raise TaskRegisterError(f"task {task_id} proof target {proof_id} required must be boolean")
    if not isinstance(blocking, bool):
        raise TaskRegisterError(f"task {task_id} proof target {proof_id} blocking must be boolean")
    if status == "waived" and not str(row.get("waived_reason") or "").strip():
        raise TaskRegisterError(f"task {task_id} proof target {proof_id} waived targets require waived_reason")


def _validate_review_gate(task_id: str, row: Dict[str, Any]) -> None:
    gate_id = str(row.get("gate_id") or "").strip()
    statement = str(row.get("statement") or "").strip()
    status = str(row.get("status") or "").strip()
    applies_at = str(row.get("applies_at") or "").strip()
    blocking = row.get("blocking")
    if not gate_id:
        raise TaskRegisterError(f"task {task_id} review gate gate_id is required")
    if not statement:
        raise TaskRegisterError(f"task {task_id} review gate {gate_id} statement is required")
    if status not in TASK_REVIEW_GATE_STATES:
        raise TaskRegisterError(f"task {task_id} review gate {gate_id} invalid status: {status or '<missing>'}")
    if not applies_at:
        raise TaskRegisterError(f"task {task_id} review gate {gate_id} applies_at is required")
    if not isinstance(blocking, bool):
        raise TaskRegisterError(f"task {task_id} review gate {gate_id} blocking must be boolean")
    if status == "waived" and not str(row.get("waived_reason") or "").strip():
        raise TaskRegisterError(f"task {task_id} review gate {gate_id} waived gates require waived_reason")


def _validate_acceptance_completion_state(task_id: str, completion_state: Dict[str, Any]) -> None:
    for key in ("implementation_complete", "evidence_complete", "operator_approved"):
        if not isinstance(completion_state.get(key), bool):
            raise TaskRegisterError(f"task {task_id} completion_state {key} must be boolean")
    if not str(completion_state.get("updated_utc") or "").strip():
        raise TaskRegisterError(f"task {task_id} completion_state updated_utc is required")


def _validate_task_acceptance_contract(task_id: str, acceptance_contract: Dict[str, Any]) -> None:
    schema = str(acceptance_contract.get("schema_version") or "").strip()
    if schema and schema != TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION:
        raise TaskRegisterError(f"task {task_id} acceptance_contract has incompatible schema_version: {schema}")
    acceptance_targets = acceptance_contract.get("acceptance_targets")
    proof_targets = acceptance_contract.get("proof_targets")
    review_gates = acceptance_contract.get("review_gates")
    completion_state = acceptance_contract.get("completion_state")
    for key, value in (
        ("acceptance_targets", acceptance_targets),
        ("proof_targets", proof_targets),
        ("review_gates", review_gates),
    ):
        if not isinstance(value, list):
            raise TaskRegisterError(f"task {task_id} acceptance_contract {key} must be a list")
    if completion_state is None or not isinstance(completion_state, dict):
        raise TaskRegisterError(f"task {task_id} acceptance_contract completion_state must be an object")
    for row in acceptance_targets:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} acceptance_contract acceptance_targets rows must be objects")
        _validate_acceptance_target(task_id, row)
    for row in proof_targets:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} acceptance_contract proof_targets rows must be objects")
        _validate_proof_target(task_id, row)
    for row in review_gates:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} acceptance_contract review_gates rows must be objects")
        _validate_review_gate(task_id, row)
    _validate_acceptance_completion_state(task_id, completion_state)
    implementation_complete = bool(completion_state.get("implementation_complete"))
    evidence_complete = bool(completion_state.get("evidence_complete"))
    operator_approved = bool(completion_state.get("operator_approved"))
    if evidence_complete and not implementation_complete:
        raise TaskRegisterError(f"task {task_id} evidence_complete requires implementation_complete")
    if operator_approved and not evidence_complete:
        raise TaskRegisterError(f"task {task_id} operator_approved requires evidence_complete")
    pending_required_proof = [
        row
        for row in proof_targets
        if bool(row.get("required")) and bool(row.get("blocking")) and str(row.get("status") or "").strip() not in {"satisfied", "waived"}
    ]
    if evidence_complete and pending_required_proof:
        raise TaskRegisterError(
            f"task {task_id} evidence_complete requires required proof targets to be satisfied or waived"
        )
    pending_review = [
        row
        for row in review_gates
        if bool(row.get("blocking")) and str(row.get("status") or "").strip() not in {"approved", "waived"}
    ]
    if operator_approved and pending_review:
        raise TaskRegisterError(f"task {task_id} operator_approved requires blocking review gates to be approved or waived")


def _validate_named_list(task_id: str, *, field: str, values: Any, min_items: int = 0) -> List[str]:
    if not isinstance(values, list):
        raise TaskRegisterError(f"task {task_id} {field} must be a list")
    normalized = [str(item).strip() for item in values if str(item).strip()]
    if len(normalized) < int(min_items):
        raise TaskRegisterError(f"task {task_id} {field} must contain at least {int(min_items)} item(s)")
    return normalized


def _validate_checkpoint_row(task_id: str, milestone_id: str, row: Dict[str, Any]) -> None:
    checkpoint_id = str(row.get("checkpoint_id") or "").strip()
    title = str(row.get("title") or "").strip()
    rationale = str(row.get("rationale") or "").strip()
    if not checkpoint_id:
        raise TaskRegisterError(f"task {task_id} milestone {milestone_id} checkpoint_id is required")
    if not title:
        raise TaskRegisterError(f"task {task_id} checkpoint {checkpoint_id} title is required")
    if not rationale:
        raise TaskRegisterError(f"task {task_id} checkpoint {checkpoint_id} rationale is required")
    _validate_named_list(task_id, field=f"checkpoint {checkpoint_id} exit_criteria", values=row.get("exit_criteria"), min_items=1)
    _validate_named_list(task_id, field=f"checkpoint {checkpoint_id} assumption_ids", values=row.get("assumption_ids") or [], min_items=0)
    _validate_named_list(
        task_id,
        field=f"checkpoint {checkpoint_id} acceptance_target_ids",
        values=row.get("acceptance_target_ids") or [],
        min_items=0,
    )


def _validate_dependency_mapping_row(task_id: str, row: Dict[str, Any], *, known_milestone_ids: List[str]) -> None:
    milestone_id = str(row.get("milestone_id") or "").strip()
    execution_mode = str(row.get("execution_mode") or "").strip()
    reason_summary = str(row.get("reason_summary") or "").strip()
    if not milestone_id:
        raise TaskRegisterError(f"task {task_id} dependency mapping milestone_id is required")
    if milestone_id not in known_milestone_ids:
        raise TaskRegisterError(f"task {task_id} dependency mapping references unknown milestone_id: {milestone_id}")
    if execution_mode not in TASK_DEPENDENCY_EXECUTION_MODES:
        raise TaskRegisterError(
            f"task {task_id} dependency mapping {milestone_id} invalid execution_mode: {execution_mode or '<missing>'}"
        )
    if not reason_summary:
        raise TaskRegisterError(f"task {task_id} dependency mapping {milestone_id} reason_summary is required")
    depends_on = _validate_named_list(
        task_id,
        field=f"dependency mapping {milestone_id} depends_on",
        values=row.get("depends_on") or [],
        min_items=0,
    )
    if milestone_id in depends_on:
        raise TaskRegisterError(f"task {task_id} dependency mapping {milestone_id} cannot depend on itself")
    unknown_dependencies = sorted(set(depends_on) - set(known_milestone_ids))
    if unknown_dependencies:
        raise TaskRegisterError(
            f"task {task_id} dependency mapping {milestone_id} references unknown depends_on milestone ids: {', '.join(unknown_dependencies)}"
        )
    reason_codes = _validate_named_list(
        task_id,
        field=f"dependency mapping {milestone_id} reason_codes",
        values=row.get("reason_codes") or [],
        min_items=1,
    )
    caution_flags = _validate_named_list(
        task_id,
        field=f"dependency mapping {milestone_id} caution_flags",
        values=row.get("caution_flags") or [],
        min_items=0,
    )
    shared_resource_keys = _validate_named_list(
        task_id,
        field=f"dependency mapping {milestone_id} shared_resource_keys",
        values=row.get("shared_resource_keys") or [],
        min_items=0,
    )
    fanout_group_id = str(row.get("fanout_group_id") or "").strip()
    if execution_mode == "fanout_safe":
        if caution_flags:
            raise TaskRegisterError(
                f"task {task_id} dependency mapping {milestone_id} cannot be fanout_safe while caution_flags are present"
            )
        if shared_resource_keys:
            raise TaskRegisterError(
                f"task {task_id} dependency mapping {milestone_id} cannot be fanout_safe while shared_resource_keys are present"
            )
        if not fanout_group_id:
            raise TaskRegisterError(
                f"task {task_id} dependency mapping {milestone_id} fanout_safe execution requires fanout_group_id"
            )
    elif fanout_group_id:
        raise TaskRegisterError(
            f"task {task_id} dependency mapping {milestone_id} fanout_group_id is only allowed for fanout_safe execution"
        )
    if "shared_resource" in caution_flags and not shared_resource_keys:
        raise TaskRegisterError(
            f"task {task_id} dependency mapping {milestone_id} shared_resource caution requires shared_resource_keys"
        )
    if shared_resource_keys and "shared_resource" not in caution_flags:
        raise TaskRegisterError(
            f"task {task_id} dependency mapping {milestone_id} shared_resource_keys require shared_resource caution"
        )
    if execution_mode == "fanout_safe" and any(code in {"hidden_dependency", "shared_resource"} for code in reason_codes):
        raise TaskRegisterError(
            f"task {task_id} dependency mapping {milestone_id} fanout_safe execution cannot claim blocking risk reason_codes"
        )


def _validate_dependency_mapping(task_id: str, dependency_mapping: Dict[str, Any], *, milestones: List[Dict[str, Any]]) -> None:
    schema = str(dependency_mapping.get("schema_version") or "").strip()
    if schema and schema != TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION:
        raise TaskRegisterError(f"task {task_id} dependency_mapping has incompatible schema_version: {schema}")
    fanout_policy = str(dependency_mapping.get("fanout_policy") or "").strip()
    if fanout_policy not in TASK_DEPENDENCY_FANOUT_POLICIES:
        raise TaskRegisterError(
            f"task {task_id} dependency_mapping invalid fanout_policy: {fanout_policy or '<missing>'}"
        )
    summary = str(dependency_mapping.get("summary") or "").strip()
    if not summary:
        raise TaskRegisterError(f"task {task_id} dependency_mapping summary is required")
    known_milestone_ids = [str(row.get("milestone_id") or "").strip() for row in milestones]
    rows = dependency_mapping.get("milestones")
    if not isinstance(rows, list):
        raise TaskRegisterError(f"task {task_id} dependency_mapping milestones must be a list")
    source_row_count = int(dependency_mapping.get("source_row_count") or len(rows))
    if source_row_count != len(known_milestone_ids):
        raise TaskRegisterError(
            f"task {task_id} dependency_mapping must not contain extra or missing milestone rows"
        )
    if len(rows) != len(known_milestone_ids):
        raise TaskRegisterError(
            f"task {task_id} dependency_mapping must include exactly one row per milestone"
        )
    seen = set()
    edge_count = 0
    fanout_groups: Dict[str, List[str]] = {}
    for row in rows:
        if not isinstance(row, dict):
            raise TaskRegisterError(f"task {task_id} dependency_mapping milestone rows must be objects")
        _validate_dependency_mapping_row(task_id, row, known_milestone_ids=known_milestone_ids)
        milestone_id = str(row.get("milestone_id") or "").strip()
        if milestone_id in seen:
            raise TaskRegisterError(f"task {task_id} dependency_mapping milestone ids must be unique")
        seen.add(milestone_id)
        depends_on = [str(item).strip() for item in list(row.get("depends_on") or []) if str(item).strip()]
        if len(depends_on) != len(set(depends_on)):
            raise TaskRegisterError(f"task {task_id} dependency mapping {milestone_id} depends_on ids must be unique")
        edge_count += len(depends_on)
        fanout_group_id = str(row.get("fanout_group_id") or "").strip()
        if fanout_group_id:
            fanout_groups.setdefault(fanout_group_id, []).append(milestone_id)
    missing = sorted(set(known_milestone_ids) - seen)
    if missing:
        raise TaskRegisterError(
            f"task {task_id} dependency_mapping missing milestone rows: {', '.join(missing)}"
        )
    if fanout_policy == "serial_only":
        fanout_rows = [
            str(row.get("milestone_id") or "").strip()
            for row in rows
            if str(row.get("execution_mode") or "").strip() == "fanout_safe"
        ]
        if fanout_rows:
            raise TaskRegisterError(
                f"task {task_id} dependency_mapping serial_only policy cannot contain fanout_safe milestones: {', '.join(fanout_rows)}"
            )
    for fanout_group_id, members in fanout_groups.items():
        if len(members) < 2:
            raise TaskRegisterError(
                f"task {task_id} dependency_mapping fanout_group {fanout_group_id} must contain at least two milestones"
            )
    row_map = {str(row.get("milestone_id") or "").strip(): row for row in rows}
    serial_count = 0
    for milestone_id, row in row_map.items():
        depends_on = {str(item).strip() for item in list(row.get("depends_on") or []) if str(item).strip()}
        if str(row.get("execution_mode") or "").strip() == "serial_only":
            serial_count += 1
        fanout_group_id = str(row.get("fanout_group_id") or "").strip()
        if fanout_group_id:
            for dep in depends_on:
                if str(row_map.get(dep, {}).get("fanout_group_id") or "").strip() == fanout_group_id:
                    raise TaskRegisterError(
                        f"task {task_id} dependency mapping fanout_group {fanout_group_id} cannot contain dependent milestones"
                    )
    if not isinstance(dependency_mapping.get("updated_utc"), str) or not str(dependency_mapping.get("updated_utc") or "").strip():
        raise TaskRegisterError(f"task {task_id} dependency_mapping updated_utc is required")
    summary_counts = dict(dependency_mapping.get("summary_counts") or {})
    if summary_counts:
        if int(summary_counts.get("dependency_edge_count") or 0) != edge_count:
            raise TaskRegisterError(f"task {task_id} dependency_mapping summary_counts dependency_edge_count does not match edges")
        fanout_ready_count = len(
            [row for row in rows if str(row.get("execution_mode") or "").strip() == "fanout_safe"]
        )
        if int(summary_counts.get("fanout_ready_count") or 0) != fanout_ready_count:
            raise TaskRegisterError(f"task {task_id} dependency_mapping summary_counts fanout_ready_count does not match rows")
        if int(summary_counts.get("forced_serial_count") or 0) != serial_count:
            raise TaskRegisterError(f"task {task_id} dependency_mapping summary_counts forced_serial_count does not match rows")
        if int(summary_counts.get("fanout_group_count") or 0) != len(fanout_groups):
            raise TaskRegisterError(f"task {task_id} dependency_mapping summary_counts fanout_group_count does not match groups")


def _validate_milestone_row(task_id: str, row: Dict[str, Any]) -> None:
    milestone_id = str(row.get("milestone_id") or "").strip()
    title = str(row.get("title") or "").strip()
    rationale = str(row.get("rationale") or "").strip()
    if not milestone_id:
        raise TaskRegisterError(f"task {task_id} milestone_id is required")
    if not title:
        raise TaskRegisterError(f"task {task_id} milestone {milestone_id} title is required")
    if not rationale:
        raise TaskRegisterError(f"task {task_id} milestone {milestone_id} rationale is required")
    _validate_named_list(task_id, field=f"milestone {milestone_id} exit_criteria", values=row.get("exit_criteria"), min_items=1)
    _validate_named_list(task_id, field=f"milestone {milestone_id} assumption_ids", values=row.get("assumption_ids") or [], min_items=0)
    _validate_named_list(
        task_id,
        field=f"milestone {milestone_id} acceptance_target_ids",
        values=row.get("acceptance_target_ids") or [],
        min_items=0,
    )
    checkpoints = row.get("checkpoints")
    if not isinstance(checkpoints, list) or not checkpoints:
        raise TaskRegisterError(f"task {task_id} milestone {milestone_id} must contain at least one checkpoint")
    seen = set()
    for checkpoint in checkpoints:
        if not isinstance(checkpoint, dict):
            raise TaskRegisterError(f"task {task_id} milestone {milestone_id} checkpoint rows must be objects")
        _validate_checkpoint_row(task_id, milestone_id, checkpoint)
        checkpoint_id = str(checkpoint.get("checkpoint_id") or "").strip()
        if checkpoint_id in seen:
            raise TaskRegisterError(f"task {task_id} milestone {milestone_id} checkpoint ids must be unique")
        seen.add(checkpoint_id)
