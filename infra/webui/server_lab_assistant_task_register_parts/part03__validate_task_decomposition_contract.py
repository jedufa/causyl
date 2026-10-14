def _validate_task_decomposition_contract(task_id: str, decomposition_contract: Dict[str, Any]) -> None:
    schema = str(decomposition_contract.get("schema_version") or "").strip()
    if schema and schema != TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION:
        raise TaskRegisterError(f"task {task_id} decomposition_contract has incompatible schema_version: {schema}")
    replanning = decomposition_contract.get("replanning")
    if replanning is None:
        replanning = {"state": "settled"}
    if not isinstance(replanning, dict):
        raise TaskRegisterError(f"task {task_id} decomposition_contract replanning must be an object")
    replan_state = str(replanning.get("state") or "settled").strip() or "settled"
    if replan_state not in TASK_REPLANNING_STATES:
        raise TaskRegisterError(f"task {task_id} decomposition_contract invalid replanning.state: {replan_state or '<missing>'}")
    revision_history = decomposition_contract.get("revision_history")
    if revision_history is not None and not isinstance(revision_history, list):
        raise TaskRegisterError(f"task {task_id} decomposition_contract revision_history must be a list")
    current_revision_id = str(decomposition_contract.get("current_revision_id") or "").strip()
    assumption_ids = _validate_named_list(
        task_id,
        field="decomposition_contract linked_assumption_ids",
        values=decomposition_contract.get("linked_assumption_ids") or [],
        min_items=0,
    )
    acceptance_target_ids = _validate_named_list(
        task_id,
        field="decomposition_contract linked_acceptance_target_ids",
        values=decomposition_contract.get("linked_acceptance_target_ids") or [],
        min_items=0,
    )
    plan_summary = decomposition_contract.get("plan_summary")
    if not isinstance(plan_summary, dict):
        raise TaskRegisterError(f"task {task_id} decomposition_contract plan_summary must be an object")
    milestone_count = plan_summary.get("milestone_count")
    checkpoint_count = plan_summary.get("checkpoint_count")
    if not isinstance(milestone_count, int) or milestone_count < 0:
        raise TaskRegisterError(f"task {task_id} decomposition plan_summary milestone_count must be a non-negative integer")
    if not isinstance(checkpoint_count, int) or checkpoint_count < 0:
        raise TaskRegisterError(f"task {task_id} decomposition plan_summary checkpoint_count must be a non-negative integer")
    for field in ("dependency_edge_count", "fanout_ready_count", "forced_serial_count", "fanout_group_count"):
        value = plan_summary.get(field)
        if value is not None and (not isinstance(value, int) or value < 0):
            raise TaskRegisterError(f"task {task_id} decomposition plan_summary {field} must be a non-negative integer")
    artifact_ref = str(decomposition_contract.get("artifact_ref") or "").strip()
    if artifact_ref:
        artifact_path = Path(artifact_ref)
        if artifact_path.is_absolute() or ".." in artifact_path.parts:
            raise TaskRegisterError(f"task {task_id} decomposition artifact_ref must stay repo-relative")
    version = decomposition_contract.get("plan_version")
    if replan_state == "settled":
        mode = str(decomposition_contract.get("mode") or "").strip()
        if mode not in TASK_DECOMPOSITION_MODES:
            raise TaskRegisterError(f"task {task_id} decomposition_contract invalid mode: {mode or '<missing>'}")
        rationale = str(decomposition_contract.get("rationale") or "").strip()
        if not rationale:
            raise TaskRegisterError(f"task {task_id} decomposition_contract rationale is required")
        decision = decomposition_contract.get("decision")
        if not isinstance(decision, dict):
            raise TaskRegisterError(f"task {task_id} decomposition_contract decision must be an object")
        estimated_step_count = decision.get("estimated_step_count")
        if not isinstance(estimated_step_count, int) or estimated_step_count < 1:
            raise TaskRegisterError(f"task {task_id} decomposition decision estimated_step_count must be a positive integer")
        if not isinstance(decision.get("risk_flags") or [], list):
            raise TaskRegisterError(f"task {task_id} decomposition decision risk_flags must be a list")
        if not isinstance(decision.get("requires_operator_checkpoint"), bool):
            raise TaskRegisterError(f"task {task_id} decomposition decision requires_operator_checkpoint must be boolean")
        if not isinstance(decision.get("needs_ordering"), bool):
            raise TaskRegisterError(f"task {task_id} decomposition decision needs_ordering must be boolean")
        if not isinstance(version, int) or version < 1:
            raise TaskRegisterError(f"task {task_id} decomposition plan_version must be an integer >= 1")
        if not current_revision_id:
            raise TaskRegisterError(f"task {task_id} settled decomposition requires current_revision_id")
        if mode == "direct":
            direct_summary = str(plan_summary.get("direct_summary") or "").strip()
            if not direct_summary:
                raise TaskRegisterError(f"task {task_id} direct decomposition requires plan_summary.direct_summary")
            if milestone_count != 0 or checkpoint_count != 0:
                raise TaskRegisterError(f"task {task_id} direct decomposition cannot declare milestones or checkpoints")
            if decomposition_contract.get("dependency_mapping"):
                raise TaskRegisterError(f"task {task_id} direct decomposition cannot declare dependency_mapping")
            for field in ("dependency_edge_count", "fanout_ready_count", "forced_serial_count", "fanout_group_count"):
                if int(plan_summary.get(field) or 0) != 0:
                    raise TaskRegisterError(f"task {task_id} direct decomposition plan_summary {field} must stay zero")
            if estimated_step_count > 2 or bool(decision.get("requires_operator_checkpoint")) or bool(decision.get("needs_ordering")):
                raise TaskRegisterError(
                    f"task {task_id} direct decomposition exceeds the direct-work threshold and must be milestone_plan"
                )
        else:
            milestones = decomposition_contract.get("milestones")
            if not isinstance(milestones, list) or not milestones:
                raise TaskRegisterError(f"task {task_id} milestone_plan requires milestones")
            seen_milestones = set()
            counted_checkpoints = 0
            for milestone in milestones:
                if not isinstance(milestone, dict):
                    raise TaskRegisterError(f"task {task_id} milestones rows must be objects")
                _validate_milestone_row(task_id, milestone)
                milestone_id = str(milestone.get("milestone_id") or "").strip()
                if milestone_id in seen_milestones:
                    raise TaskRegisterError(f"task {task_id} milestone ids must be unique")
                seen_milestones.add(milestone_id)
                counted_checkpoints += len(list(milestone.get("checkpoints") or []))
            if milestone_count != len(milestones) or checkpoint_count != counted_checkpoints:
                raise TaskRegisterError(f"task {task_id} decomposition plan_summary counts do not match milestone contents")
            if estimated_step_count < 3:
                raise TaskRegisterError(f"task {task_id} milestone_plan requires estimated_step_count >= 3")
            if milestone_count < 2 and checkpoint_count < 2:
                raise TaskRegisterError(
                    f"task {task_id} decomposition split is too trivial for milestone_plan; keep it direct instead"
                )
            active_milestone_id = str(plan_summary.get("active_milestone_id") or "").strip()
            active_checkpoint_id = str(plan_summary.get("active_checkpoint_id") or "").strip()
            if active_checkpoint_id and not active_milestone_id:
                raise TaskRegisterError(
                    f"task {task_id} decomposition active_checkpoint_id requires active_milestone_id"
                )
            if active_milestone_id and active_milestone_id not in seen_milestones:
                raise TaskRegisterError(f"task {task_id} decomposition active_milestone_id does not exist in milestones")
            if active_checkpoint_id:
                active_milestone = next(
                    (row for row in milestones if str(row.get("milestone_id") or "").strip() == active_milestone_id),
                    None,
                )
                checkpoint_ids = {
                    str(checkpoint.get("checkpoint_id") or "").strip()
                    for checkpoint in list((active_milestone or {}).get("checkpoints") or [])
                }
                if active_checkpoint_id not in checkpoint_ids:
                    raise TaskRegisterError(
                        f"task {task_id} decomposition active_checkpoint_id does not belong to the active milestone"
                    )
            dependency_mapping = decomposition_contract.get("dependency_mapping")
            if dependency_mapping is not None:
                if not isinstance(dependency_mapping, dict):
                    raise TaskRegisterError(f"task {task_id} decomposition dependency_mapping must be an object")
                _validate_dependency_mapping(task_id, dependency_mapping, milestones=milestones)
                summary_counts = dict(dependency_mapping.get("summary_counts") or {})
                for field in ("dependency_edge_count", "fanout_ready_count", "forced_serial_count", "fanout_group_count"):
                    expected = int(summary_counts.get(field) or 0)
                    actual = int(plan_summary.get(field) or 0)
                    if actual != expected:
                        raise TaskRegisterError(
                            f"task {task_id} decomposition plan_summary {field} must match dependency_mapping summary_counts"
                        )
            else:
                for field in ("dependency_edge_count", "fanout_ready_count", "forced_serial_count", "fanout_group_count"):
                    if int(plan_summary.get(field) or 0) != 0:
                        raise TaskRegisterError(
                            f"task {task_id} decomposition plan_summary {field} requires dependency_mapping"
                        )
    else:
        if current_revision_id:
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must not expose current_revision_id")
        if str(decomposition_contract.get("mode") or "").strip():
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear current mode")
        if str(decomposition_contract.get("rationale") or "").strip():
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear current rationale")
        if dict(decomposition_contract.get("decision") or {}):
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear current decision")
        if isinstance(version, int) and version > 0:
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear current plan_version")
        if artifact_ref:
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear current artifact_ref")
        if list(decomposition_contract.get("milestones") or []):
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear current milestones")
        if dict(decomposition_contract.get("dependency_mapping") or {}):
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear dependency_mapping")
        if milestone_count or checkpoint_count:
            raise TaskRegisterError(f"task {task_id} unsettled decomposition must clear current plan_summary counts")
        summary = str(replanning.get("summary") or "").strip()
        if not summary:
            raise TaskRegisterError(f"task {task_id} unsettled decomposition requires replanning.summary")
        invalidated_revision_id = str(replanning.get("invalidated_revision_id") or "").strip()
        if not invalidated_revision_id:
            raise TaskRegisterError(f"task {task_id} unsettled decomposition requires invalidated_revision_id")
        invalidated_plan_version = replanning.get("invalidated_plan_version")
        if not isinstance(invalidated_plan_version, int) or invalidated_plan_version < 1:
            raise TaskRegisterError(f"task {task_id} unsettled decomposition requires invalidated_plan_version >= 1")
    if revision_history:
        seen_revision_ids = set()
        current_rows = 0
        invalidated_ids = set()
        for row in revision_history:
            if not isinstance(row, dict):
                raise TaskRegisterError(f"task {task_id} decomposition revision_history rows must be objects")
            revision_id = str(row.get("revision_id") or "").strip()
            if not revision_id:
                raise TaskRegisterError(f"task {task_id} decomposition revision_id is required")
            if revision_id in seen_revision_ids:
                raise TaskRegisterError(f"task {task_id} decomposition revision_ids must be unique")
            seen_revision_ids.add(revision_id)
            state = str(row.get("state") or "").strip()
            if state not in TASK_PLAN_REVISION_STATES:
                raise TaskRegisterError(f"task {task_id} decomposition revision {revision_id} invalid state: {state or '<missing>'}")
            row_version = row.get("plan_version")
            if not isinstance(row_version, int) or row_version < 1:
                raise TaskRegisterError(f"task {task_id} decomposition revision {revision_id} requires plan_version >= 1")
            if not str(row.get("captured_utc") or "").strip() or not str(row.get("updated_utc") or "").strip():
                raise TaskRegisterError(f"task {task_id} decomposition revision {revision_id} requires timestamps")
            trigger = dict(row.get("trigger") or {})
            kind = str(trigger.get("kind") or "").strip()
            summary = str(trigger.get("summary") or "").strip()
            if kind not in TASK_REPLAN_TRIGGER_KINDS:
                raise TaskRegisterError(f"task {task_id} decomposition revision {revision_id} invalid trigger kind: {kind or '<missing>'}")
            if not summary:
                raise TaskRegisterError(f"task {task_id} decomposition revision {revision_id} trigger summary is required")
            if state == "current":
                current_rows += 1
            if state == "invalidated":
                invalidated_ids.add(revision_id)
        if replan_state == "settled":
            if current_rows != 1:
                raise TaskRegisterError(f"task {task_id} settled decomposition requires exactly one current revision row")
            if current_revision_id not in seen_revision_ids:
                raise TaskRegisterError(f"task {task_id} settled decomposition current_revision_id is missing from revision_history")
        else:
            if current_rows != 0:
                raise TaskRegisterError(f"task {task_id} unsettled decomposition cannot keep a current revision row")
            invalidated_revision_id = str(replanning.get("invalidated_revision_id") or "").strip()
            if invalidated_revision_id not in invalidated_ids:
                raise TaskRegisterError(
                    f"task {task_id} unsettled decomposition invalidated_revision_id must reference an invalidated revision row"
                )
    if not isinstance(decomposition_contract.get("updated_utc"), str) or not str(
        decomposition_contract.get("updated_utc") or ""
    ).strip():
        raise TaskRegisterError(f"task {task_id} decomposition_contract updated_utc is required")


def _validate_delegation_ref(ref: Dict[str, Any]) -> None:
    schema = str(ref.get("schema_version") or "").strip()
    if schema and schema != DELEGATION_REF_SCHEMA_VERSION:
        raise TaskRegisterError(f"incompatible delegation ref schema_version: {schema}")
    delegation_request_id = str(ref.get("delegation_request_id") or "").strip()
    surface = str(ref.get("surface") or "").strip()
    subsystem = str(ref.get("subsystem") or "").strip()
    subsystem_id = str(ref.get("subsystem_id") or "").strip()
    bound_utc = str(ref.get("bound_utc") or "").strip()
    updated_utc = str(ref.get("updated_utc") or "").strip()
    correlation_id = str(ref.get("correlation_id") or "").strip()
    launch_turn_id = str(ref.get("launch_turn_id") or "").strip()
    result_correlation_basis = str(ref.get("result_correlation_basis") or "").strip()
    if not delegation_request_id:
        raise TaskRegisterError("delegation ref delegation_request_id is required")
    if not surface:
        raise TaskRegisterError(f"delegation ref {delegation_request_id} surface is required")
    if not subsystem:
        raise TaskRegisterError(f"delegation ref {delegation_request_id} subsystem is required")
    if not subsystem_id:
        raise TaskRegisterError(f"delegation ref {delegation_request_id} subsystem_id is required")
    if not bound_utc or not updated_utc:
        raise TaskRegisterError(f"delegation ref {delegation_request_id} requires bound_utc and updated_utc")
    if not correlation_id:
        raise TaskRegisterError(f"delegation ref {delegation_request_id} correlation_id is required")
    if not launch_turn_id:
        raise TaskRegisterError(f"delegation ref {delegation_request_id} launch_turn_id is required")
    if not result_correlation_basis:
        raise TaskRegisterError(f"delegation ref {delegation_request_id} result_correlation_basis is required")
    if ref.get("idempotency_keys") is not None and not isinstance(ref.get("idempotency_keys"), list):
        raise TaskRegisterError(f"delegation ref {delegation_request_id} idempotency_keys must be a list")
    for key in ("reason_codes", "expected_evidence", "evidence_candidates", "status_refs", "artifact_refs"):
        if not isinstance(ref.get(key) or [], list):
            raise TaskRegisterError(f"delegation ref {delegation_request_id} {key} must be a list")
    next_decision = ref.get("next_decision") if isinstance(ref.get("next_decision"), dict) else {}
    if not str(next_decision.get("kind") or "").strip():
        raise TaskRegisterError(f"delegation ref {delegation_request_id} next_decision.kind is required")
    if not str(next_decision.get("summary") or "").strip():
        raise TaskRegisterError(f"delegation ref {delegation_request_id} next_decision.summary is required")
    mission_fit = ref.get("mission_fit")
    if mission_fit not in (None, {}):
        if not isinstance(mission_fit, dict):
            raise TaskRegisterError(f"delegation ref {delegation_request_id} mission_fit must be an object")
        if not str(_mission_fit_status_with_legacy_backfill(mission_fit) or "").strip():
            raise TaskRegisterError(f"delegation ref {delegation_request_id} mission_fit.status is required")


def _mission_fit_status_with_legacy_backfill(mission_fit: Dict[str, Any]) -> str:
    status = str(mission_fit.get("status") or "").strip().lower()
    if status:
        return status
    classification = str(mission_fit.get("classification") or "").strip().lower()
    decision = str(mission_fit.get("decision") or "").strip().lower()
    summary = " ".join(
        str(part or "").strip().lower()
        for part in (mission_fit.get("summary"), mission_fit.get("note"))
        if str(part or "").strip()
    )
    if classification == "aligned" or decision == "allow":
        return "mission_aligned"
    if classification == "needs_exception" or decision == "allow_with_exception":
        return "partially_aligned"
    if classification in {"blocked", "mismatch"} or decision == "block":
        return "mission_misaligned"
    if "fits the laboratory mission boundary" in summary or "mission boundary" in summary and "fits" in summary:
        return "mission_aligned"
    if "allowed because" in summary or "exception" in summary:
        return "partially_aligned"
    if "misaligned" in summary or "does not fit" in summary or "outside the mission" in summary:
        return "mission_misaligned"
    if not mission_fit:
        return ""
    return "unclear"


def _normalize_delegation_ref(ref: Dict[str, Any]) -> Dict[str, Any]:
    mission_fit = dict(ref.get("mission_fit") or {})
    status = _mission_fit_status_with_legacy_backfill(mission_fit)
    if status and not str(mission_fit.get("status") or "").strip():
        mission_fit["status"] = status
    out = {
        "schema_version": str(ref.get("schema_version") or DELEGATION_REF_SCHEMA_VERSION).strip() or DELEGATION_REF_SCHEMA_VERSION,
        "delegation_request_id": str(ref.get("delegation_request_id") or "").strip(),
        "surface": str(ref.get("surface") or "").strip(),
        "subsystem": str(ref.get("subsystem") or "").strip(),
        "subsystem_id": str(ref.get("subsystem_id") or "").strip(),
        "summary": str(ref.get("summary") or "").strip(),
        "requested_action": str(ref.get("requested_action") or "").strip(),
        "bound_utc": str(ref.get("bound_utc") or _utc_now_iso()),
        "updated_utc": str(ref.get("updated_utc") or _utc_now_iso()),
        "correlation_id": str(ref.get("correlation_id") or "").strip(),
        "launch_turn_id": str(ref.get("launch_turn_id") or "").strip(),
        "detached_job_id": str(ref.get("detached_job_id") or "").strip(),
        "result_correlation_basis": str(ref.get("result_correlation_basis") or "").strip(),
        "launch_cause": str(ref.get("launch_cause") or "").strip(),
        "parent_request_id": str(ref.get("parent_request_id") or "").strip(),
        "prior_result_id": str(ref.get("prior_result_id") or "").strip(),
        "idempotency_keys": [str(item).strip() for item in list(ref.get("idempotency_keys") or []) if str(item).strip()],
        "reason_codes": [str(code).strip() for code in list(ref.get("reason_codes") or []) if str(code).strip()],
        "expected_evidence": list(ref.get("expected_evidence") or []),
        "evidence_candidates": list(ref.get("evidence_candidates") or []),
        "status_refs": list(ref.get("status_refs") or []),
        "artifact_refs": list(ref.get("artifact_refs") or []),
        "native_status_hint": str(ref.get("native_status_hint") or "").strip(),
        "owner_ref": dict(ref.get("owner_ref") or {}),
        "next_decision": dict(ref.get("next_decision") or {}),
        "mission_fit": mission_fit,
    }
    _validate_delegation_ref(out)
    return out


def _normalize_routing_decision(row: Dict[str, Any]) -> Dict[str, Any]:
    schema_version = str(row.get("schema_version") or "").strip() or TASK_ROUTING_DECISION_SCHEMA_VERSION
    owner_ref = dict(row.get("owner_ref") or {})
    return {
        "schema_version": schema_version,
        "decision_id": str(row.get("decision_id") or "").strip(),
        "decision_revision": int(_int_or_default(row.get("decision_revision"), default=0)),
        "created_utc": str(row.get("created_utc") or _utc_now_iso()),
        "request_id": str(row.get("request_id") or "").strip(),
        "correlation_id": str(row.get("correlation_id") or "").strip(),
        "launch_turn_id": str(row.get("launch_turn_id") or "").strip(),
        "owner_ref": owner_ref,
        "requested_surface": str(row.get("requested_surface") or "").strip(),
        "selected_surface": str(row.get("selected_surface") or "").strip(),
        "status": str(row.get("status") or "").strip(),
        "decision_outcome": str(row.get("decision_outcome") or "").strip(),
        "summary": str(row.get("summary") or "").strip(),
        "reason_codes": [str(code).strip() for code in list(row.get("reason_codes") or []) if str(code).strip()],
        "rerouted": bool(row.get("rerouted")),
        "auditable_facts": dict(row.get("auditable_facts") or {}),
        "expected_execution_contract": dict(row.get("expected_execution_contract") or {}),
        "rejection_status": str(row.get("rejection_status") or "").strip(),
        "request_factors": dict(row.get("request_factors") or {}),
        "mission_fit": dict(row.get("mission_fit") or {}),
        "constraint_flags": [str(item).strip() for item in list(row.get("constraint_flags") or []) if str(item).strip()],
        "expected_evidence_tags": [str(item).strip() for item in list(row.get("expected_evidence_tags") or []) if str(item).strip()],
        "execution_surface_ownership": dict(row.get("execution_surface_ownership") or {}),
    }


def _validate_routing_decision(task_id: str, decision: Dict[str, Any]) -> None:
    schema_version = str(decision.get("schema_version") or "").strip()
    if schema_version and schema_version != TASK_ROUTING_DECISION_SCHEMA_VERSION:
        raise TaskRegisterError(
            f"task {task_id} routing_decision has incompatible schema_version: {schema_version or '<missing>'}"
        )
    if not str(decision.get("decision_id") or "").strip():
        raise TaskRegisterError(f"task {task_id} routing_decision decision_id is required")
    decision_outcome = str(decision.get("decision_outcome") or "").strip()
    if decision_outcome not in TASK_ROUTING_DECISION_OUTCOMES:
        raise TaskRegisterError(
            f"task {task_id} routing_decision decision_outcome must be one of "
            f"{', '.join(sorted(TASK_ROUTING_DECISION_OUTCOMES))}"
        )
    try:
        decision_revision = int(decision.get("decision_revision") or 0)
    except Exception as exc:
        raise TaskRegisterError(f"task {task_id} routing_decision decision_revision must be integer") from exc
    if decision_revision <= 0:
        raise TaskRegisterError(f"task {task_id} routing_decision decision_revision must be > 0")
    owner_ref = decision.get("execution_surface_ownership")
    if not isinstance(owner_ref, dict):
        raise TaskRegisterError(f"task {task_id} routing_decision execution_surface_ownership must be an object")
    if not str((owner_ref or {}).get("owner_uid") or "").strip():
        raise TaskRegisterError(
            f"task {task_id} routing_decision execution_surface_ownership.owner_uid is required"
        )
    if not str(decision.get("status") or "").strip():
        raise TaskRegisterError(f"task {task_id} routing_decision status is required")
    request_factors = decision.get("request_factors")
    if request_factors is not None and not isinstance(request_factors, dict):
        raise TaskRegisterError(f"task {task_id} routing_decision request_factors must be an object")
    for key in ("constraint_flags", "expected_evidence_tags"):
        flags = decision.get(key)
        if flags is not None and not isinstance(flags, list):
            raise TaskRegisterError(f"task {task_id} routing_decision {key} must be a list")
        for item in list(flags or []):
            if not str(item).strip():
                raise TaskRegisterError(f"task {task_id} routing_decision {key} must not contain empty values")
    if decision_outcome in {"delegate"} and not str(decision.get("selected_surface") or "").strip():
        raise TaskRegisterError(f"task {task_id} delegate routing_decision selected_surface is required")


def _routing_decision_conflicts(
    task_id: str,
    existing: Dict[str, Any],
    incoming: Dict[str, Any],
) -> List[str]:
    existing_decision = existing.get("routing_decision")
    incoming_decision = incoming.get("routing_decision")
    if not existing_decision or not incoming_decision:
        return []
    existing_rev = int(existing_decision.get("decision_revision") or 0)
    incoming_rev = int(incoming_decision.get("decision_revision") or 0)
    if incoming_rev < existing_rev:
        raise TaskRegisterError(
            f"stale routing decision rejected for task {task_id}: "
            f"decision_revision={incoming_rev} current={existing_rev}"
        )
    if incoming_rev == existing_rev and dict(incoming_decision) != dict(existing_decision):
        return ["routing_decision"]
    return []


def _normalize_scope_assumption(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    approval_state = str(row.get("approval_state") or "").strip() or "provisional"
    operator_approved = bool(row.get("operator_approved")) if "operator_approved" in row else approval_state == "operator_confirmed"
    requires_confirmation = (
        bool(row.get("requires_operator_confirmation"))
        if "requires_operator_confirmation" in row
        else approval_state in {"provisional", "needs_operator_confirmation"}
    )
    blocking = bool(row.get("blocking")) if "blocking" in row else approval_state == "needs_operator_confirmation"
    normalized = {
        "assumption_id": str(row.get("assumption_id") or (existing or {}).get("assumption_id") or "").strip(),
        "statement": str(row.get("statement") or "").strip(),
        "kind": str(row.get("kind") or (existing or {}).get("kind") or "").strip() or "inferred",
        "approval_state": approval_state,
        "operator_approved": operator_approved,
        "requires_operator_confirmation": requires_confirmation,
        "blocking": blocking,
        "source": str(row.get("source") or (existing or {}).get("source") or "").strip(),
        "rationale": str(row.get("rationale") or "").strip(),
        "confirmed_by": str(row.get("confirmed_by") or (existing or {}).get("confirmed_by") or "").strip(),
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
        "confirmed_utc": "",
    }
    if normalized["approval_state"] == "operator_confirmed":
        normalized["confirmed_utc"] = str(row.get("confirmed_utc") or (existing or {}).get("confirmed_utc") or now)
    elif existing:
        normalized["confirmed_utc"] = str(existing.get("confirmed_utc") or "")
    _validate_scope_assumption("scope_contract", normalized)
    return normalized


def _normalize_scope_constraint_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    normalized = {
        "constraint_id": str(row.get("constraint_id") or (existing or {}).get("constraint_id") or "").strip(),
        "statement": str(row.get("statement") or "").strip(),
        "scope": str(row.get("scope") or (existing or {}).get("scope") or "task").strip() or "task",
        "source": str(row.get("source") or (existing or {}).get("source") or "").strip(),
        "rationale": str(row.get("rationale") or (existing or {}).get("rationale") or "").strip(),
        "active": bool(row.get("active")) if "active" in row else bool((existing or {}).get("active", True)),
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
    }
    _validate_scope_constraint("scope_contract", normalized)
    return normalized


def _normalize_scope_non_goal_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    normalized = {
        "non_goal_id": str(row.get("non_goal_id") or (existing or {}).get("non_goal_id") or "").strip(),
        "statement": str(row.get("statement") or "").strip(),
        "source": str(row.get("source") or (existing or {}).get("source") or "").strip(),
        "rationale": str(row.get("rationale") or (existing or {}).get("rationale") or "").strip(),
        "active": bool(row.get("active")) if "active" in row else bool((existing or {}).get("active", True)),
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
    }
    _validate_scope_non_goal("scope_contract", normalized)
    return normalized


def _normalize_scope_revision(task_id: str, row: Dict[str, Any], *, prior_revision_id: str = "") -> Dict[str, Any]:
    now = _utc_now_iso()
    normalized = {
        "revision_id": str(row.get("revision_id") or uuid.uuid4().hex).strip(),
        "summary": str(row.get("summary") or "").strip(),
        "in_scope": [str(item).strip() for item in list(row.get("in_scope") or []) if str(item).strip()],
        "out_of_scope": [str(item).strip() for item in list(row.get("out_of_scope") or []) if str(item).strip()],
        "rationale": str(row.get("rationale") or "").strip(),
        "source": str(row.get("source") or "").strip(),
        "captured_utc": str(row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
        "supersedes_revision_id": str(row.get("supersedes_revision_id") or prior_revision_id).strip(),
    }
    _validate_scope_revision(task_id, normalized)
    return normalized


def _normalize_task_scope_contract(scope_contract: Dict[str, Any]) -> Dict[str, Any]:
    assumptions = [_normalize_scope_assumption(row) for row in list(scope_contract.get("assumptions") or [])]
    constraints = [_normalize_scope_constraint_row(row) for row in list(scope_contract.get("constraints") or [])]
    non_goals = [_normalize_scope_non_goal_row(row) for row in list(scope_contract.get("non_goals") or [])]
    scope_history = [_normalize_scope_revision("scope_contract", row) for row in list(scope_contract.get("scope_history") or [])]
    current_scope = None
    if scope_contract.get("current_scope") is not None:
        current_scope = _normalize_scope_revision("scope_contract", dict(scope_contract.get("current_scope") or {}))
    normalized = {
        "schema_version": str(scope_contract.get("schema_version") or TASK_SCOPE_CONTRACT_SCHEMA_VERSION).strip()
        or TASK_SCOPE_CONTRACT_SCHEMA_VERSION,
        "assumptions": assumptions,
        "constraints": constraints,
        "non_goals": non_goals,
        "current_scope": current_scope,
        "scope_history": scope_history,
        "updated_utc": str(scope_contract.get("updated_utc") or _utc_now_iso()),
    }
    _validate_task_scope_contract("scope_contract", normalized)
    return normalized


def _normalize_acceptance_target_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    normalized = {
        "target_id": str(row.get("target_id") or (existing or {}).get("target_id") or "").strip(),
        "statement": str(row.get("statement") or "").strip(),
        "target_type": str(row.get("target_type") or (existing or {}).get("target_type") or "deliverable").strip() or "deliverable",
        "milestone_id": str(row.get("milestone_id") or (existing or {}).get("milestone_id") or "").strip(),
        "source": str(row.get("source") or (existing or {}).get("source") or "").strip(),
        "rationale": str(row.get("rationale") or (existing or {}).get("rationale") or "").strip(),
        "active": bool(row.get("active")) if "active" in row else bool((existing or {}).get("active", True)),
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
    }
    _validate_acceptance_target("acceptance_contract", normalized)
    return normalized


def _normalize_proof_target_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    status = str(row.get("status") or (existing or {}).get("status") or "pending").strip() or "pending"
    normalized = {
        "proof_id": str(row.get("proof_id") or (existing or {}).get("proof_id") or "").strip(),
        "statement": str(row.get("statement") or "").strip(),
        "proof_class": str(row.get("proof_class") or (existing or {}).get("proof_class") or "other").strip() or "other",
        "required": bool(row.get("required")) if "required" in row else bool((existing or {}).get("required", True)),
        "blocking": bool(row.get("blocking")) if "blocking" in row else bool((existing or {}).get("blocking", True)),
        "status": status,
        "artifact_ref": str(row.get("artifact_ref") or (existing or {}).get("artifact_ref") or "").strip(),
        "source": str(row.get("source") or (existing or {}).get("source") or "").strip(),
        "rationale": str(row.get("rationale") or (existing or {}).get("rationale") or "").strip(),
        "waived_reason": str(row.get("waived_reason") or (existing or {}).get("waived_reason") or "").strip(),
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
        "satisfied_utc": "",
    }
    if status == "satisfied":
        normalized["satisfied_utc"] = str(row.get("satisfied_utc") or (existing or {}).get("satisfied_utc") or now)
    elif existing:
        normalized["satisfied_utc"] = str(existing.get("satisfied_utc") or "")
    _validate_proof_target("acceptance_contract", normalized)
    return normalized


def _normalize_review_gate_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    status = str(row.get("status") or (existing or {}).get("status") or "pending").strip() or "pending"
    normalized = {
        "gate_id": str(row.get("gate_id") or (existing or {}).get("gate_id") or "").strip(),
        "statement": str(row.get("statement") or "").strip(),
        "reviewer_role": str(row.get("reviewer_role") or (existing or {}).get("reviewer_role") or "operator").strip()
        or "operator",
        "applies_at": str(row.get("applies_at") or (existing or {}).get("applies_at") or "").strip(),
        "status": status,
        "blocking": bool(row.get("blocking")) if "blocking" in row else bool((existing or {}).get("blocking", True)),
        "source": str(row.get("source") or (existing or {}).get("source") or "").strip(),
        "rationale": str(row.get("rationale") or (existing or {}).get("rationale") or "").strip(),
        "waived_reason": str(row.get("waived_reason") or (existing or {}).get("waived_reason") or "").strip(),
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
        "approved_utc": "",
    }
    if status == "approved":
        normalized["approved_utc"] = str(row.get("approved_utc") or (existing or {}).get("approved_utc") or now)
    elif existing:
        normalized["approved_utc"] = str(existing.get("approved_utc") or "")
    _validate_review_gate("acceptance_contract", normalized)
    return normalized


def _normalize_acceptance_completion_state(
    completion_state: Dict[str, Any],
    *,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    now = _utc_now_iso()
    implementation_complete = (
        bool(completion_state.get("implementation_complete"))
        if "implementation_complete" in completion_state
        else bool((existing or {}).get("implementation_complete"))
    )
    evidence_complete = (
        bool(completion_state.get("evidence_complete"))
        if "evidence_complete" in completion_state
        else bool((existing or {}).get("evidence_complete"))
    )
    operator_approved = (
        bool(completion_state.get("operator_approved"))
        if "operator_approved" in completion_state
        else bool((existing or {}).get("operator_approved"))
    )
    normalized = {
        "implementation_complete": implementation_complete,
        "evidence_complete": evidence_complete,
        "operator_approved": operator_approved,
        "summary": str(completion_state.get("summary") or (existing or {}).get("summary") or "").strip(),
        "updated_utc": str(completion_state.get("updated_utc") or now),
        "implementation_completed_utc": "",
        "evidence_completed_utc": "",
        "operator_approved_utc": "",
    }
    if implementation_complete:
        normalized["implementation_completed_utc"] = str(
            completion_state.get("implementation_completed_utc") or (existing or {}).get("implementation_completed_utc") or now
        )
    elif existing:
        normalized["implementation_completed_utc"] = str(existing.get("implementation_completed_utc") or "")
    if evidence_complete:
        normalized["evidence_completed_utc"] = str(
            completion_state.get("evidence_completed_utc") or (existing or {}).get("evidence_completed_utc") or now
        )
    elif existing:
        normalized["evidence_completed_utc"] = str(existing.get("evidence_completed_utc") or "")
    if operator_approved:
        normalized["operator_approved_utc"] = str(
            completion_state.get("operator_approved_utc") or (existing or {}).get("operator_approved_utc") or now
        )
    elif existing:
        normalized["operator_approved_utc"] = str(existing.get("operator_approved_utc") or "")
    _validate_acceptance_completion_state("acceptance_contract", normalized)
    return normalized


def _normalize_task_acceptance_contract(acceptance_contract: Dict[str, Any]) -> Dict[str, Any]:
    acceptance_targets = [_normalize_acceptance_target_row(row) for row in list(acceptance_contract.get("acceptance_targets") or [])]
    proof_targets = [_normalize_proof_target_row(row) for row in list(acceptance_contract.get("proof_targets") or [])]
    review_gates = [_normalize_review_gate_row(row) for row in list(acceptance_contract.get("review_gates") or [])]
    completion_state = _normalize_acceptance_completion_state(dict(acceptance_contract.get("completion_state") or {}))
    normalized = {
        "schema_version": str(acceptance_contract.get("schema_version") or TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION).strip()
        or TASK_ACCEPTANCE_CONTRACT_SCHEMA_VERSION,
        "acceptance_targets": acceptance_targets,
        "proof_targets": proof_targets,
        "review_gates": review_gates,
        "completion_state": completion_state,
        "updated_utc": str(acceptance_contract.get("updated_utc") or _utc_now_iso()),
    }
    _validate_task_acceptance_contract("acceptance_contract", normalized)
    return normalized


def _normalize_checkpoint_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    normalized = {
        "checkpoint_id": str(row.get("checkpoint_id") or (existing or {}).get("checkpoint_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "rationale": str(row.get("rationale") or "").strip(),
        "status": str(row.get("status") or (existing or {}).get("status") or "planned").strip() or "planned",
        "owner": str(row.get("owner") or (existing or {}).get("owner") or "assistant").strip() or "assistant",
        "exit_criteria": [
            str(item).strip() for item in list(row.get("exit_criteria") or []) if str(item).strip()
        ],
        "assumption_ids": [str(item).strip() for item in list(row.get("assumption_ids") or []) if str(item).strip()],
        "acceptance_target_ids": [
            str(item).strip() for item in list(row.get("acceptance_target_ids") or []) if str(item).strip()
        ],
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
    }
    _validate_checkpoint_row("decomposition_contract", str(normalized.get("checkpoint_id") or ""), normalized)
    return normalized


def _normalize_milestone_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    existing_checkpoints = {
        str(item.get("checkpoint_id") or "").strip(): dict(item) for item in list((existing or {}).get("checkpoints") or [])
    }
    normalized = {
        "milestone_id": str(row.get("milestone_id") or (existing or {}).get("milestone_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "rationale": str(row.get("rationale") or "").strip(),
        "status": str(row.get("status") or (existing or {}).get("status") or "planned").strip() or "planned",
        "owner": str(row.get("owner") or (existing or {}).get("owner") or "assistant").strip() or "assistant",
        "exit_criteria": [str(item).strip() for item in list(row.get("exit_criteria") or []) if str(item).strip()],
        "assumption_ids": [str(item).strip() for item in list(row.get("assumption_ids") or []) if str(item).strip()],
        "acceptance_target_ids": [
            str(item).strip() for item in list(row.get("acceptance_target_ids") or []) if str(item).strip()
        ],
        "checkpoints": [
            _normalize_checkpoint_row(checkpoint, existing=existing_checkpoints.get(str(checkpoint.get("checkpoint_id") or "").strip()))
            for checkpoint in list(row.get("checkpoints") or [])
        ],
        "captured_utc": str((existing or {}).get("captured_utc") or row.get("captured_utc") or now),
        "updated_utc": str(row.get("updated_utc") or now),
    }
    _validate_milestone_row("decomposition_contract", normalized)
    return normalized


def _normalize_dependency_mapping_row(row: Dict[str, Any], *, existing: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    now = _utc_now_iso()
    return {
        "milestone_id": str(row.get("milestone_id") or (existing or {}).get("milestone_id") or "").strip(),
        "depends_on": [str(item).strip() for item in list(row.get("depends_on") or []) if str(item).strip()],
        "execution_mode": str(row.get("execution_mode") or (existing or {}).get("execution_mode") or "serial_only").strip()
        or "serial_only",
        "fanout_group_id": str(row.get("fanout_group_id") or (existing or {}).get("fanout_group_id") or "").strip(),
        "reason_codes": [str(item).strip() for item in list(row.get("reason_codes") or []) if str(item).strip()],
        "reason_summary": str(row.get("reason_summary") or "").strip(),
        "caution_flags": [str(item).strip() for item in list(row.get("caution_flags") or []) if str(item).strip()],
        "shared_resource_keys": [
            str(item).strip() for item in list(row.get("shared_resource_keys") or []) if str(item).strip()
        ],
        "updated_utc": str(row.get("updated_utc") or now),
    }
