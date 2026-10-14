def _normalize_execution_ref(ref: Dict[str, Any]) -> Dict[str, Any]:
    kind = str(ref.get("kind") or "").strip()
    ref_id = str(ref.get("ref_id") or "").strip()
    if not kind or not ref_id:
        raise TaskRegisterError("execution ref requires kind and ref_id")
    return {
        "kind": kind,
        "ref_id": ref_id,
        "label": str(ref.get("label") or "").strip(),
        "status": str(ref.get("status") or "").strip(),
        "task_id": str(ref.get("task_id") or "").strip(),
        "ledger_uid": str(ref.get("ledger_uid") or "").strip(),
        "recorded_utc": str(ref.get("recorded_utc") or _utc_now_iso()),
    }


def _is_degraded_execution_reference_state(reference: Dict[str, Any]) -> bool:
    return str(reference.get("reference_status") or "execution_ref_unresolved") in {
        "execution_ref_unresolved",
        "execution_ref_stale",
        "execution_ref_conflict",
    }


def _next_execution_surface_ownership_row(
    task_id: str,
    *,
    existing: Dict[str, Any],
    selected_surface: str,
    execution_id: str,
    execution_ref: str,
    binding_ref: str,
    job_uri: str,
    trace_id: str,
    reference_status: str,
    reference_status_reason: str,
    conflict_gate: bool,
    last_writer: str,
) -> Dict[str, Any]:
    has_existing_owner = bool(existing and isinstance(existing, dict))
    existing_owner = _normalize_execution_surface_ownership(existing) if has_existing_owner else {}
    candidate = _normalize_execution_surface_ownership(
        {
            "schema_version": TASK_EXECUTION_SURFACE_OWNERSHIP_SCHEMA_VERSION,
            "owner_uid": str(_EXECUTION_OWNER_UID),
            "selected_surface": selected_surface,
            "execution_id": str(execution_id or "").strip(),
            "execution_ref": str(execution_ref or "").strip(),
            "binding_ref": str(binding_ref or "").strip(),
            "job_uri": str(job_uri or "").strip(),
            "trace_id": str(trace_id or "").strip(),
            "reference_status": str(reference_status or "").strip(),
            "reference_status_reason": str(reference_status_reason or "").strip(),
            "reference_revision": 0,
            "owner_surface": str(selected_surface or existing_owner.get("owner_surface") or "").strip(),
            "updated_utc": _utc_now_iso(),
            "last_writer": str(last_writer or "").strip(),
        }
    )
    candidate["owner_uid"] = existing_owner.get("owner_uid") or _EXECUTION_OWNER_UID
    next_revision = int(_int_or_default(existing_owner.get("reference_revision"), default=0)) + 1
    previous_selected = str(existing_owner.get("selected_surface") or "").strip()
    previous_status = str(existing_owner.get("reference_status") or "execution_ref_unresolved").strip()
    previous_has_all_refs = has_existing_owner and _execution_refs_complete(existing_owner)
    conflict_to_different_surface = bool(previous_selected) and previous_selected != candidate["selected_surface"]
    existing_is_degraded = has_existing_owner and (
        _is_degraded_execution_reference_state(existing_owner) or not _execution_refs_complete(existing_owner)
    )
    same_surface = previous_selected == candidate["selected_surface"]
    stale_reason = str(reference_status_reason or "").strip()
    requested_stale = str(reference_status or "").strip() == "execution_ref_stale"
    candidate_execution_id = str(candidate.get("execution_id") or "").strip()
    existing_execution_id = str(existing_owner.get("execution_id") or "").strip()
    same_execution = bool(candidate_execution_id) and candidate_execution_id == existing_execution_id
    if (
        has_existing_owner
        and previous_status == "execution_ref_active"
        and same_surface
        and previous_has_all_refs
        and not conflict_gate
        and (requested_stale or same_execution or not candidate_execution_id)
        and (
            requested_stale
            or not _execution_refs_complete(candidate)
            or (
                str(existing_owner.get("execution_ref") or "").strip() != str(candidate.get("execution_ref") or "").strip()
                or str(existing_owner.get("execution_id") or "").strip() != str(candidate.get("execution_id") or "").strip()
                or str(existing_owner.get("binding_ref") or "").strip() != str(candidate.get("binding_ref") or "").strip()
                or str(existing_owner.get("job_uri") or "").strip() != str(candidate.get("job_uri") or "").strip()
                or str(existing_owner.get("trace_id") or "").strip() != str(candidate.get("trace_id") or "").strip()
            )
        )
    ):
        candidate = existing_owner
        candidate["reference_status"] = "execution_ref_stale"
        if not stale_reason:
            stale_reason = (
                f"execution ref reattach for task {task_id} detected stale run on {previous_selected or 'unknown'}; "
                f"preserving existing refs"
            )
        candidate["reference_status_reason"] = f"{str(existing_owner.get('reference_status_reason') or '').strip()} | {stale_reason}".strip(
            " |"
        )
        candidate["reference_revision"] = next_revision
        candidate["updated_utc"] = _utc_now_iso()
        candidate["last_writer"] = str(last_writer or "").strip()
        return candidate
    if conflict_to_different_surface and existing_is_degraded and not conflict_gate:
        candidate = existing_owner
        candidate["reference_status"] = "execution_ref_conflict"
        reason = str(reference_status_reason or "").strip()
        if not reason:
            reason = (
                f"conflict handoff for task {task_id} from {previous_selected or 'unknown'} "
                f"to {selected_surface or 'unknown'} without conflict gate"
            )
        candidate["reference_status_reason"] = f"{str(existing_owner.get('reference_status_reason') or '').strip()} | {reason}".strip(" |")
        candidate["reference_revision"] = next_revision
        candidate["updated_utc"] = _utc_now_iso()
        candidate["last_writer"] = str(last_writer or "").strip()
        return candidate
    candidate["reference_revision"] = next_revision
    candidate["updated_utc"] = _utc_now_iso()
    candidate["last_writer"] = str(last_writer or "").strip()
    return candidate


def _build_summary(register: Dict[str, Any]) -> Dict[str, Any]:
    raw_tasks = [dict(task) for task in list(register.get("active_tasks") or []) if isinstance(task, dict)]
    completed_tasks = [dict(task) for task in list(register.get("completed_tasks") or []) if isinstance(task, dict)]
    residue_tasks = [dict(task) for task in list(register.get("residue_tasks") or []) if isinstance(task, dict)]
    hidden_helper_task_ids = {
        _clean_text(task.get("task_id"))
        for task in raw_tasks + completed_tasks + residue_tasks
        if is_internal_helper_task(task) and _clean_text(task.get("task_id"))
    }
    suppressed_helper_task_count = sum(1 for task in raw_tasks if is_internal_helper_task(task))
    tasks = [
        task
        for task in raw_tasks
        if not is_internal_helper_task(task) and str(task.get("status") or "").lower() not in {"completed", "done"}
    ]
    raw_loops = [dict(loop) for loop in list(register.get("open_loops") or []) if isinstance(loop, dict)]
    loops = [
        loop
        for loop in raw_loops
        if str(loop.get("status") or "").lower() != "resolved"
        and _clean_text(loop.get("linked_task_id")) not in hidden_helper_task_ids
    ]
    suppressed_helper_open_loop_count = sum(
        1
        for loop in raw_loops
        if _clean_text(loop.get("linked_task_id")) in hidden_helper_task_ids
        and str(loop.get("status") or "").lower() != "resolved"
    )
    constraints = list(register.get("constraints") or [])
    refs = list(register.get("recent_execution_refs") or [])
    lines: List[str] = []
    for task in tasks[:5]:
        kind = str(task.get("kind") or "task")
        title = str(task.get("title") or task.get("task_id") or "")
        status = str(task.get("status") or "")
        next_action = str(task.get("next_expected_action") or "")
        intake = dict(task.get("intake") or {})
        intake_bits = []
        if str(intake.get("readiness") or "").strip():
            intake_bits.append(f"intake={intake.get('readiness')}")
        if list(intake.get("missing_information") or []):
            intake_bits.append(f"missing={len(list(intake.get('missing_information') or []))}")
        scope_summary = _scope_contract_summary(task)
        if scope_summary:
            intake_bits.append(scope_summary)
        acceptance_summary = _acceptance_contract_summary(task)
        if acceptance_summary:
            intake_bits.append(acceptance_summary)
        decomposition_summary = _decomposition_contract_summary(task)
        if decomposition_summary:
            intake_bits.append(decomposition_summary)
        intake_suffix = "; " + ", ".join(intake_bits) if intake_bits else ""
        lines.append(f"[{kind}] {title} ({status}; next: {next_action}{intake_suffix})")
    for loop in loops[:4]:
        lines.append(f"[{loop.get('kind')}] {str(loop.get('note') or loop.get('loop_id') or '').strip()}")
    for constraint in constraints[:4]:
        lines.append(f"[constraint] {constraint.get('key')}: {constraint.get('value')}")
    for ref in refs[:3]:
        label = str(ref.get("label") or ref.get("ref_id") or "")
        lines.append(f"[ref] {ref.get('kind')} {label} ({ref.get('status')})")
    return {
        "schema_version": TASK_REGISTER_SUMMARY_SCHEMA_VERSION,
        "derived_only": True,
        "generated_utc": _utc_now_iso(),
        "register_generation": int(register.get("write_generation") or 0),
        "active_task_count": len(tasks),
        "completed_task_count": len(
            [task for task in completed_tasks if not is_internal_helper_task(task)]
        ),
        "residue_task_count": len(
            [task for task in residue_tasks if not is_internal_helper_task(task)]
        ),
        "open_loop_count": len(loops),
        "constraint_count": len(constraints),
        "recent_execution_ref_count": len(refs),
        "suppressed_internal_helper_task_count": suppressed_helper_task_count,
        "suppressed_internal_helper_open_loop_count": suppressed_helper_open_loop_count,
        "lines": lines,
    }


def _task_bucket_name(task: Dict[str, Any]) -> str:
    status = _clean_text(dict(task or {}).get("status")).lower()
    if status in {"completed", "done"}:
        return "completed_tasks"
    if status in {"interrupted_recovering", "failed", "blocked"}:
        return "residue_tasks"
    return "active_tasks"


def _compact_task_buckets(register: Dict[str, Any]) -> Dict[str, Any]:
    normalized_register = dict(register or {})
    by_id: Dict[str, Dict[str, Any]] = {}
    ordered_ids: List[str] = []
    for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks"):
        for raw_task in list(normalized_register.get(bucket_name) or []):
            if not isinstance(raw_task, dict):
                continue
            task = _normalize_task(raw_task)
            task_id = _clean_text(task.get("task_id"))
            if not task_id:
                continue
            if task_id not in ordered_ids:
                ordered_ids.append(task_id)
            by_id[task_id] = task
    normalized_register["active_tasks"] = []
    normalized_register["completed_tasks"] = []
    normalized_register["residue_tasks"] = []
    for task_id in ordered_ids:
        task = by_id[task_id]
        normalized_register[_task_bucket_name(task)].append(task)
    return normalized_register


def _load_register_internal(repo_root: Path) -> Dict[str, Any]:
    register = _read_json_strict(_register_path(repo_root))
    register = _normalize_register_for_legacy_load(register)
    register = _compact_task_buckets(register)
    _validate_register_obj(register)
    return register


def replay_task_register_events(repo_root: Path) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    rows_seen = 0
    register: Optional[Dict[str, Any]] = None
    saw_init = False
    saw_commit = False
    for row in _iter_jsonl_dicts_strict(_events_path(repo_root)):
        rows_seen += 1
        payload = row.get("payload")
        if not isinstance(payload, dict):
            raise TaskRegisterError("task register event log row is missing payload")
        event_type = str(row.get("event") or "")
        if event_type == "task_register_initialized":
            base = payload.get("register")
            if isinstance(base, dict):
                register = _normalize_register_for_legacy_load(base)
                saw_init = True
            else:
                raise TaskRegisterError("task_register_initialized event is missing canonical register payload")
        elif event_type == "task_register_committed":
            committed = payload.get("register")
            if not isinstance(committed, dict):
                raise TaskRegisterError("task_register_committed event is missing canonical register payload")
            normalized = _normalize_register_for_legacy_load(committed)
            _validate_register_obj(normalized)
            register = dict(normalized)
            saw_commit = True
        elif event_type == "task_run_recorded":
            if register is None:
                raise TaskRegisterError("task run record event found before task_register_initialized")
            _apply_task_run_record_event(register=register, payload=dict(payload))
    if rows_seen == 0:
        raise TaskRegisterError("task register event log cannot be replayed into a canonical register")
    if register is None:
        raise TaskRegisterError("task register event log cannot be replayed into a canonical register")
    if not saw_init:
        raise TaskRegisterError("task register event log is missing initialization record")
    if not saw_commit:
        _validate_register_obj(register)
    register["summary_stale"] = True
    _validate_register_obj(register)
    return register


def _write_summary_cache(repo_root: Path, register: Dict[str, Any]) -> Dict[str, Any]:
    compacted = _compact_task_buckets(register)
    register.clear()
    register.update(compacted)
    summary = _build_summary(register)
    register["summary_stale"] = False
    register["summary_cache_ref"] = _summary_cache_ref(repo_root)
    return summary


def _append_commit_event(repo_root: Path, *, register: Dict[str, Any], summary: Dict[str, Any], writer: str) -> None:
    refresh_state = _snapshot_refresh_state(register, writer=writer)
    _append_event(
        repo_root,
        event_type="task_register_committed",
        payload={
            "writer": str(writer or ""),
            "register": dict(register),
            "summary_cache": dict(summary),
        },
    )
    _append_event(
        repo_root,
        event_type="task_register_projection_refreshed",
        payload={
            "schema_version": TASK_REGISTER_SNAPSHOT_REFRESH_SCHEMA_VERSION,
            "writer": str(writer or ""),
            "write_generation": int(register.get("write_generation") or 0),
            "refresh_mode": str(refresh_state.get("refresh_mode") or "eager"),
            "summary_cache_present": bool(summary),
            "snapshot_refresh": dict(refresh_state),
        },
    )
    _maybe_compact_task_register_event_log(
        repo_root,
        register=register,
        summary=summary,
        writer=writer,
    )


def _load_or_init_task_register_locked(repo_root: Path) -> Dict[str, Any]:
    _lab_host.ensure_host_storage(repo_root)
    _task_register_root(repo_root).mkdir(parents=True, exist_ok=True)
    assistant_state = _lab_state.load_or_init_assistant_state(repo_root)
    canonical_path = _register_path(repo_root)
    if not canonical_path.exists():
        if _events_path(repo_root).exists():
            replayed = replay_task_register_events(repo_root)
            _write_json_atomic(canonical_path, replayed)
            summary = _write_summary_cache(repo_root, replayed)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return replayed
        register = _empty_register(repo_root=repo_root, assistant_id=str(assistant_state.get("assistant_id") or ""))
        _append_event(repo_root, event_type="task_register_initialized", payload={"register": dict(register)})
        summary = _write_summary_cache(repo_root, register)
        _append_commit_event(repo_root, register=register, summary=summary, writer="task_register.initialize")
        _write_json_atomic(canonical_path, register)
        _write_json_atomic(_summary_cache_path(repo_root), summary)
        return dict(register)
    register = _load_register_internal(repo_root)
    if not _summary_cache_path(repo_root).exists():
        summary = _write_summary_cache(repo_root, register)
        _append_event(
            repo_root,
            event_type="summary_rebuilt",
            payload={"reason": "summary_cache_missing", "write_generation": int(register.get("write_generation") or 0)},
        )
        _append_commit_event(repo_root, register=register, summary=summary, writer="task_register.summary_repair")
        _write_json_atomic(canonical_path, register)
        _write_json_atomic(_summary_cache_path(repo_root), summary)
    else:
        summary = _read_json(_summary_cache_path(repo_root))
        if summary and _int_or_default(summary.get("register_generation"), -1) == _int_or_default(
            register.get("write_generation"), 0
        ):
            if bool(register.get("summary_stale")):
                register["summary_stale"] = False
                register["summary_cache_ref"] = _summary_cache_ref(repo_root)
                _append_event(
                    repo_root,
                    event_type="summary_flag_cleared",
                    payload={
                        "reason": "summary_cache_consistent",
                        "write_generation": int(register.get("write_generation") or 0),
                    },
                )
                _append_commit_event(repo_root, register=register, summary=summary, writer="task_register.summary_flag_repair")
                _write_json_atomic(canonical_path, register)
    return register


def load_or_init_task_register(repo_root: Path) -> TaskRegisterSnapshot:
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        with _task_register_lock(repo_root):
            return _load_or_init_task_register_locked(repo_root)


def _read_summary_cache(repo_root: Path, register: Dict[str, Any], *, allow_rebuild: bool) -> Dict[str, Any]:
    summary = _read_json(_summary_cache_path(repo_root))
    if summary and _int_or_default(summary.get("register_generation"), -1) == _int_or_default(
        register.get("write_generation"), 0
    ):
        if bool(register.get("summary_stale")):
            register["summary_stale"] = False
            register["summary_cache_ref"] = _summary_cache_ref(repo_root)
        return summary
    if allow_rebuild:
        return _write_summary_cache(repo_root, register)
    register["summary_stale"] = True
    return _build_summary(register)


def _snapshot_refresh_state(register: Dict[str, Any], *, writer: str) -> Dict[str, Any]:
    write_generation = int(register.get("write_generation") or 0)
    current_utc = _utc_now_iso()
    state = dict(register.get("snapshot_projection") or {})
    refresh_mode = str(state.get("refresh_mode") or "eager").strip().lower() or "eager"
    if refresh_mode not in {"eager", "lazy"}:
        refresh_mode = "eager"
    refresh_reason = f"{refresh_mode}_refresh"
    state.update(
        {
            "schema_version": TASK_REGISTER_SNAPSHOT_REFRESH_SCHEMA_VERSION,
            "refresh_generation": write_generation,
            "refresh_writer": str(writer or state.get("refresh_writer") or ""),
            "refresh_utc": str(current_utc),
            "refresh_mode": refresh_mode,
            "refresh_reason": refresh_reason,
        }
    )
    register["snapshot_projection"] = state
    return state


def _snapshot_projection_refresh_status(
    register: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(register, dict):
        return {
            "snapshot_projection": {},
            "stale": True,
            "stale_reasons": ["task_register_missing"],
            "refresh_generation": -1,
            "refresh_writer": "",
            "refresh_utc": "",
            "refresh_mode": "lazy",
        }
    state = dict(register.get("snapshot_projection") or {})
    registered_generation = int(register.get("write_generation") or 0)
    stale_reasons: List[str] = []
    if not state:
        stale_reasons.append("snapshot_projection_missing")
    elif _clean_text(state.get("schema_version") or "") != TASK_REGISTER_SNAPSHOT_REFRESH_SCHEMA_VERSION:
        stale_reasons.append("snapshot_projection_schema_mismatch")

    refresh_generation = _int_or_default(state.get("refresh_generation"), -1)
    if refresh_generation < 0:
        stale_reasons.append("snapshot_refresh_generation_missing")
    elif refresh_generation > registered_generation:
        stale_reasons.append("snapshot_refresh_generation_ahead")
    elif refresh_generation < registered_generation:
        stale_reasons.append("snapshot_refresh_generation_stale")

    return {
        "snapshot_projection": state,
        "stale": bool(stale_reasons),
        "stale_reasons": stale_reasons,
        "refresh_generation": refresh_generation,
        "refresh_writer": _clean_text(state.get("refresh_writer") or state.get("writer")),
        "refresh_utc": _clean_text(state.get("refresh_utc")),
        "refresh_mode": str(state.get("refresh_mode") or ("eager" if "snapshot_projection" in state else "lazy")).strip().lower() or "eager",
    }


def read_task_register_snapshot(repo_root: Path, *, initialize_if_missing: bool = True) -> TaskRegisterSnapshot:
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        if initialize_if_missing:
            with _task_register_lock(repo_root):
                register = _load_or_init_task_register_locked(repo_root)
                summary = _read_summary_cache(repo_root, register, allow_rebuild=True)
        else:
            register = _load_register_internal(repo_root) if _register_path(repo_root).exists() else None
            summary = _read_summary_cache(repo_root, register, allow_rebuild=False) if register else None
        integrity = task_register_integrity_snapshot(repo_root)
        host_health = _lab_host.host_health_snapshot(repo_root, initialize_if_missing=False)
        assistant_state_error = ""
        try:            assistant_state = _lab_state.read_assistant_state_snapshot(repo_root, initialize_if_missing=False)
        except Exception as exc:
            assistant_state = {"assistant_state": None}
            assistant_state_error = str(exc)
        refresh_status = _snapshot_projection_refresh_status(register if register is not None else None)
        snapshot_projection = dict(refresh_status.get("snapshot_projection") or {})
        registered_generation = int((register or {}).get("write_generation") or 0)
        refresh_generation = int(refresh_status.get("refresh_generation") or -1)
        stale_reasons = list(refresh_status.get("stale_reasons") or [])
        if str(dict(integrity.get("summary_cache") or {}).get("status") or "") not in {"ok", "missing"}:
            if "summary_cache_corrupt_or_partial" not in stale_reasons:
                stale_reasons.append("summary_cache_corrupt_or_partial")
            if isinstance(register, dict):
                register["summary_stale"] = True
        if bool(integrity.get("degraded")) and str(dict(integrity.get("canonical_register") or {}).get("status") or "") not in {"ok", "missing"}:
            if "canonical_register_corrupt_or_partial" not in stale_reasons:
                stale_reasons.append("canonical_register_corrupt_or_partial")
        refresh_reason = _clean_text(snapshot_projection.get("refresh_reason") or (stale_reasons[0] if stale_reasons else ""))
        return {
            "schema_version": "lab_assistant_task_register_snapshot_v1",
            "utc": _utc_now_iso(),
            "task_register": register,
            "summary_cache": summary,
            "integrity": integrity,
            "snapshot_projection": {
                "schema_version": TASK_REGISTER_SNAPSHOT_REFRESH_SCHEMA_VERSION,
                "mode": "eager" if initialize_if_missing else "lazy",                "snapshot_present": bool(bool(summary)),
                "register_generation": registered_generation,
                "refresh_generation": refresh_generation if refresh_generation >= 0 else registered_generation,
                "refresh_writer": refresh_status.get("refresh_writer", ""),
                "refresh_utc": refresh_status.get("refresh_utc", ""),
                "refresh_mode": refresh_status.get("refresh_mode", "lazy"),
                "refresh_reason": refresh_reason,
                "stale": bool(refresh_status.get("stale") or False),
                "stale_reasons": stale_reasons,
            },
            "assistant_state": assistant_state.get("assistant_state"),
            "assistant_state_error": assistant_state_error,
            "host_health": host_health,
        }


def _write_generation_event(repo_root: Path, *, register: Dict[str, Any], writer: str) -> None:
    _append_event(
        repo_root,
        event_type="task_register_generation_set",
        payload={
            "write_generation": int(register.get("write_generation") or 0),
            "writer": str(writer or ""),
            "utc": str(register.get("last_written_utc") or _utc_now_iso()),
        },
    )


def _set_generation(register: Dict[str, Any], *, writer: str) -> None:
    register["write_generation"] = int(register.get("write_generation") or 0) + 1
    register["last_writer"] = str(writer or "")
    register["last_written_utc"] = _utc_now_iso()


def _reject_stale_write(repo_root: Path, *, writer: str, expected_generation: int, current_generation: int) -> None:
    _append_event(
        repo_root,
        event_type="task_register_write_rejected",
        payload={
            "writer": str(writer or ""),
            "expected_generation": int(expected_generation),
            "current_generation": int(current_generation),
        },
    )


def _ledger_patch_instruction_sheets_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "governance" / "ledger" / "patch_instruction_sheets").resolve()


def _resolve_ledger_item_dir(repo_root: Path, ledger_uid: str) -> Path:
    normalized = str(ledger_uid or "").strip()
    if not _LEDGER_UID_RE.match(normalized):
        raise TaskRegisterError(f"invalid ledger_uid for decomposition artifact: {normalized or '<missing>'}")
    item_dir = _ledger_storage.item_dir_for_uid(_ledger_patch_instruction_sheets_root(repo_root), normalized)
    if item_dir is None:
        raise TaskRegisterError(f"could not resolve unique ledger item directory for {normalized}")
    item_dir = item_dir.resolve()
    root = _ledger_patch_instruction_sheets_root(repo_root).resolve()
    if root not in item_dir.parents:
        raise TaskRegisterError("resolved ledger item directory escaped governance/ledger/patch_instruction_sheets")
    return item_dir


def _write_decomposition_artifact(
    repo_root: Path,
    *,
    task: Dict[str, Any],
    decomposition_contract: Dict[str, Any],
) -> str:
    ledger_uid = str(task.get("ledger_uid") or "").strip()
    if not ledger_uid:
        raise TaskRegisterError(f"task {str(task.get('task_id') or '').strip()} requires ledger_uid for decomposition artifact storage")
    item_dir = _resolve_ledger_item_dir(repo_root, ledger_uid)
    safe_task_id = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task.get("task_id") or "").strip()).strip("-") or "task"
    version = int(decomposition_contract.get("plan_version") or 1)
    artifact_path = (
        item_dir / "artifacts" / "planning" / f"decomposition__{safe_task_id}__v{version:03d}.json"
    ).resolve()
    artifact_root = (item_dir / "artifacts").resolve()
    if artifact_root not in artifact_path.parents:
        raise TaskRegisterError("decomposition artifact path escaped ledger artifact root")
    artifact_obj = {
        "schema_version": TASK_DECOMPOSITION_ARTIFACT_SCHEMA_VERSION,
        "task_id": str(task.get("task_id") or "").strip(),
        "ledger_uid": ledger_uid,
        "task_title": str(task.get("title") or "").strip(),
        "current_revision_id": str(decomposition_contract.get("current_revision_id") or "").strip(),
        "mode": str(decomposition_contract.get("mode") or "").strip(),
        "plan_version": version,
        "updated_utc": str(decomposition_contract.get("updated_utc") or _utc_now_iso()),
        "replanning": dict(decomposition_contract.get("replanning") or {}),
        "rationale": str(decomposition_contract.get("rationale") or "").strip(),
        "decision": dict(decomposition_contract.get("decision") or {}),
        "linked_assumption_ids": list(decomposition_contract.get("linked_assumption_ids") or []),
        "linked_acceptance_target_ids": list(decomposition_contract.get("linked_acceptance_target_ids") or []),
        "plan_summary": dict(decomposition_contract.get("plan_summary") or {}),
        "milestones": list(decomposition_contract.get("milestones") or []),
        "dependency_mapping": dict(decomposition_contract.get("dependency_mapping") or {}),
        "revision_history": list(decomposition_contract.get("revision_history") or []),
    }
    _write_json_atomic(artifact_path, artifact_obj)
    return str(artifact_path.relative_to(Path(repo_root).resolve()))


def _planning_handoff_dir_for_item(item_dir: Path) -> Path:
    return (item_dir / "artifacts" / "planning" / "handoffs").resolve()


def _safe_task_token(task_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id or "").strip()).strip("-") or "task"


def _linked_scope_rows(task: Dict[str, Any], linked_ids: List[str]) -> List[Dict[str, Any]]:
    assumptions = {
        str(row.get("assumption_id") or "").strip(): dict(row)
        for row in list(dict(task.get("scope_contract") or {}).get("assumptions") or [])
        if str(row.get("assumption_id") or "").strip()
    }
    return [assumptions[assumption_id] for assumption_id in linked_ids if assumption_id in assumptions]


def _linked_acceptance_rows(task: Dict[str, Any], linked_ids: List[str]) -> List[Dict[str, Any]]:
    targets = {
        str(row.get("target_id") or "").strip(): dict(row)
        for row in list(dict(task.get("acceptance_contract") or {}).get("acceptance_targets") or [])
        if str(row.get("target_id") or "").strip()
    }
    return [targets[target_id] for target_id in linked_ids if target_id in targets]


def _dependency_edges(decomposition_contract: Dict[str, Any]) -> List[Dict[str, str]]:
    mapping = dict(decomposition_contract.get("dependency_mapping") or {})
    rows = [row for row in list(mapping.get("milestones") or []) if isinstance(row, dict)]
    edges: List[Dict[str, str]] = []
    for row in rows:
        milestone_id = str(row.get("milestone_id") or "").strip()
        if not milestone_id:
            continue
        for dep in [str(item).strip() for item in list(row.get("depends_on") or []) if str(item).strip()]:
            edges.append({"from_milestone_id": dep, "to_milestone_id": milestone_id})
    return edges


def _planning_handoff_readiness(task: Dict[str, Any]) -> Dict[str, Any]:
    blocked_reasons: List[str] = []
    partial_reasons: List[str] = []
    decomposition_contract = dict(task.get("decomposition_contract") or {})
    scope_contract = dict(task.get("scope_contract") or {})
    acceptance_contract = dict(task.get("acceptance_contract") or {})
    replanning = dict(decomposition_contract.get("replanning") or {})
    replan_state = str(replanning.get("state") or "settled").strip() or "settled"
    mode = str(decomposition_contract.get("mode") or "").strip()
    current_revision_id = str(decomposition_contract.get("current_revision_id") or "").strip()
    plan_version = int(decomposition_contract.get("plan_version") or 0)
    artifact_ref = str(decomposition_contract.get("artifact_ref") or "").strip()
    linked_assumption_ids = [
        str(item).strip() for item in list(decomposition_contract.get("linked_assumption_ids") or []) if str(item).strip()
    ]
    linked_target_ids = [
        str(item).strip()
        for item in list(decomposition_contract.get("linked_acceptance_target_ids") or [])
        if str(item).strip()
    ]
    if not decomposition_contract:
        blocked_reasons.append("missing_decomposition_contract")
    if replan_state != "settled":
        blocked_reasons.append(f"replanning_state_{replan_state}")
    if decomposition_contract and mode not in TASK_DECOMPOSITION_MODES:
        blocked_reasons.append("missing_or_invalid_plan_mode")
    if decomposition_contract and plan_version < 1:
        blocked_reasons.append("missing_or_invalid_plan_version")
    if decomposition_contract and not current_revision_id:
        blocked_reasons.append("missing_current_revision_id")
    if decomposition_contract and not artifact_ref:
        blocked_reasons.append("missing_decomposition_artifact_ref")
    if decomposition_contract and mode == "milestone_plan":
        checkpoint_count = int(dict(decomposition_contract.get("plan_summary") or {}).get("checkpoint_count") or 0)
        if checkpoint_count <= 0:
            blocked_reasons.append("milestone_plan_missing_checkpoints")
    if not scope_contract:
        partial_reasons.append("missing_scope_contract")
    if not acceptance_contract:
        partial_reasons.append("missing_acceptance_contract")
    known_assumptions = {
        str(row.get("assumption_id") or "").strip()
        for row in list(scope_contract.get("assumptions") or [])
        if str(row.get("assumption_id") or "").strip()
    }
    known_targets = {
        str(row.get("target_id") or "").strip()
        for row in list(acceptance_contract.get("acceptance_targets") or [])
        if str(row.get("target_id") or "").strip()
    }
    if set(linked_assumption_ids) - known_assumptions:
        blocked_reasons.append("linked_assumptions_missing_from_scope_contract")
    if set(linked_target_ids) - known_targets:
        blocked_reasons.append("linked_acceptance_targets_missing_from_acceptance_contract")
    if blocked_reasons:
        state = "blocked"
        launch_ready = False
    elif partial_reasons:
        state = "partial"
        launch_ready = False
    else:
        state = "ready"
        launch_ready = True
    return {
        "state": state,
        "launch_ready": launch_ready,
        "blocked_reasons": sorted(set(blocked_reasons)),
        "partial_reasons": sorted(set(partial_reasons)),
    }


def _current_revision_row(decomposition_contract: Dict[str, Any]) -> Dict[str, Any]:
    current_revision_id = str(decomposition_contract.get("current_revision_id") or "").strip()
    for row in list(decomposition_contract.get("revision_history") or []):
        if not isinstance(row, dict):
            continue
        if str(row.get("revision_id") or "").strip() == current_revision_id:
            return dict(row)
    return {}
