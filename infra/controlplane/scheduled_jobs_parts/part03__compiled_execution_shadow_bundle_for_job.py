def _compiled_execution_shadow_bundle_for_job(
    spec: Dict[str, Any],
    *,
    roles: List[str],
    runtime: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    ir_result = compiled_execution_ir_from_job_spec(spec)
    manifest_result = compiled_context_manifest_from_job_spec(spec, repo_root=repo_root)
    return _compiled_execution_integration.build_compiled_execution_shadow_bundle(
        ir_result,
        manifest_result,
        roles=roles,
        execution_mode=_compiled_execution_mode_for_job(spec, runtime or {}),
    )


def _compiled_prompt_dispatch_for_role(
    *,
    compiled_execution: Dict[str, Any],
    role: str,
) -> Dict[str, Any]:
    prompts = compiled_execution.get("prompts") if isinstance(compiled_execution.get("prompts"), dict) else {}
    artifact = prompts.get(role) if isinstance(prompts.get(role), dict) else {}
    execution_mode = str(compiled_execution.get("execution_mode") or "").strip().lower()
    prompt_text = str(artifact.get("prompt_text") or "")
    ok = True
    failure_class = ""
    errors: List[Dict[str, Any]] = []
    dispatch_mode = "legacy_message"
    if execution_mode in {"soft_gate", "hard_gate"}:
        if prompt_text:
            dispatch_mode = "compiled_prompt"
        else:
            ok = False
            failure_class = "compiled_prompt_unavailable"
            dispatch_mode = "blocked_compiled_prompt"
            errors.append({"code": "compiled_prompt_unavailable", "message": f"{role} compiled prompt is required in {execution_mode}"})
    return {
        "ok": ok,
        "execution_mode": execution_mode,
        "dispatch_mode": dispatch_mode,
        "prompt_text": prompt_text,
        "prompt_sha256": str(artifact.get("prompt_sha256") or ""),
        "prompt_version": str(artifact.get("prompt_version") or ""),
        "role": role,
        "failure_class": failure_class,
        "errors": errors,
    }


def _compiled_dispatch_blocked_result(*, role: str, dispatch: Dict[str, Any]) -> Dict[str, Any]:
    failure_class = str(dispatch.get("failure_class") or "compiled_dispatch_blocked")
    errors = list(dispatch.get("errors") or [])
    message = f"[compiled_execution_blocked] {failure_class}"
    if errors:
        detail = str((errors[0] or {}).get("message") or "").strip()
        if detail:
            message = f"{message}: {detail}"
    return {
        "ok": False,
        "assistant": {
            "content": message,
            "status": "error",
            "meta": {
                "error": failure_class,
                "compiled_dispatch_blocked": True,
                "compiled_dispatch_errors": errors,
                "role": role,
            },
        },
    }


def load_job(job_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    path = _job_path(job_id, repo_root)
    obj = _read_json(path)
    if not obj:
        raise FileNotFoundError(f"scheduled job not found: {job_id}")
    obj = _persist_legacy_job_shape_migration_marker(obj, repo_root=repo_root)
    prompt_ref = str(obj.get("prompt_ref") or "").strip()
    if prompt_ref:
        obj["prompt"] = _read_prompt_text_from_ref(prompt_ref, repo_root=repo_root)
    elif _normalize_job_kind(obj.get("job_kind")) == "swarm_launch":
        obj["prompt"] = _render_swarm_launch_prompt(_normalize_swarm_launch(obj.get("swarm")))
    obj.update(_resolve_job_shape_fields(obj))
    return obj


def load_state(job_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    return _read_json(_state_path(job_id, repo_root))


def _spec_for_state_identity(spec: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(spec or {})
    row.pop("updated_utc", None)
    row.pop("created_utc", None)
    return row


def save_job(spec: JobSpec, *, repo_root: Optional[Path] = None) -> JobSpec:
    existing = {}
    try:
        existing = _read_json(_job_path(str(spec.get("job_id") or ""), repo_root))
    except Exception:
        existing = {}
    merged = _materialize_prompt_spec(dict(spec or {}), existing=existing, repo_root=repo_root)
    _populate_missing_required_context_bundle(merged)
    if existing.get("created_utc") and not merged.get("created_utc"):
        merged["created_utc"] = existing["created_utc"]
    canonical = validate_job_spec(
        merged,
        now_utc=_utc_iso(),
        repo_root=repo_root,
        allow_legacy_job_shape_compatibility=_allow_legacy_job_shape_compatibility(existing),
    )
    canonical.pop("prompt", None)
    state_path = _state_path(str(canonical["job_id"]), repo_root)
    try:
        existing_state = _read_json(state_path)
    except Exception:
        existing_state = {}
    if existing and existing_state:
        active_run_id = str(existing_state.get("active_run_id") or "").strip()
        if not active_run_id and _spec_for_state_identity(existing) != _spec_for_state_identity(canonical):
            state_path.unlink(missing_ok=True)
    path = _job_path(str(canonical["job_id"]), repo_root)
    _write_json_atomic(path, canonical)
    return load_job(str(canonical["job_id"]), repo_root=repo_root)


def _populate_missing_required_context_bundle(spec: Dict[str, Any]) -> None:
    if str(spec.get("required_context_bundle") or "").strip():
        return
    try:
        candidate = dict(spec)
        candidate["job_kind"] = _normalize_job_kind(candidate.get("job_kind"))
        obligations = _resolved_job_shape_context_obligations(candidate)
    except Exception:
        return
    required_bundle = str(obligations.get("required_context_bundle") or "").strip()
    if required_bundle:
        spec["required_context_bundle"] = required_bundle


def _allow_legacy_job_shape_compatibility(existing: Dict[str, Any]) -> bool:
    if not isinstance(existing, dict) or not existing:
        return False
    if not str(existing.get("job_id") or "").strip():
        return False
    if not str(existing.get("job_shape") or "").strip():
        return True
    return str(existing.get("job_shape_migration_status") or "").strip() in {
        "legacy_migration_required",
        "legacy_pending_backfill",
        "legacy_backfilled",
        "legacy_incompatible",
    }


def _persist_legacy_job_shape_migration_marker(
    raw: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    now_utc: Optional[str] = None,
) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        return {}
    if str(raw.get("job_shape") or "").strip():
        return raw
    if str(raw.get("job_shape_migration_status") or "").strip():
        return raw

    job_id = str(raw.get("job_id") or "").strip()
    if not job_id:
        return raw

    state = load_state(job_id, repo_root=repo_root)
    active_legacy_record = bool(raw.get("enabled", True)) or bool(str(state.get("active_run_id") or "").strip())
    timestamp = str(now_utc or _utc_iso()).strip() or _utc_iso()
    persisted = dict(raw)
    persisted["updated_utc"] = timestamp
    validated_migration_status = "legacy_pending_backfill"
    persisted_migration_status = "legacy_pending_backfill" if active_legacy_record else "legacy_migration_required"
    persisted_migration_reason = "" if active_legacy_record else "legacy record requires explicit job-shape backfill before launch"

    try:
        inferred = _resolve_job_shape_fields(raw)
        candidate = dict(raw)
        candidate.update(
            {
                "job_shape": str(inferred.get("job_shape") or ""),
                "job_shape_source": "compatibility_inferred_from_job_kind",
                "job_shape_compatibility_mode": True,
                "job_shape_migration_status": validated_migration_status,
                "job_shape_migration_reason": "",
            }
        )
        validate_job_spec(
            candidate,
            now_utc=timestamp,
            repo_root=repo_root,
            allow_legacy_job_shape_compatibility=True,
        )
        persisted["job_shape_migration_status"] = persisted_migration_status
        persisted["job_shape_migration_reason"] = persisted_migration_reason
    except Exception as exc:
        persisted["job_shape_migration_status"] = "legacy_incompatible"
        persisted["job_shape_migration_reason"] = str(exc)

    _write_json_atomic(_job_path(job_id, repo_root), persisted)
    return persisted


def backfill_legacy_job_shape_records(
    *,
    repo_root: Optional[Path] = None,
    job_ids: Optional[List[str]] = None,
    active_only: bool = True,
    now_utc: Optional[str] = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    requested_ids = {
        str(item or "").strip()
        for item in list(job_ids or [])
        if str(item or "").strip()
    }
    processed: List[Dict[str, Any]] = []
    timestamp = str(now_utc or _utc_iso()).strip() or _utc_iso()
    for path in sorted(jobs_dir(root).glob("*.json"), key=lambda item: item.name):
        raw = _read_json(path)
        if not raw:
            continue
        job_id = str(raw.get("job_id") or path.stem).strip()
        if requested_ids and job_id not in requested_ids:
            continue
        if str(raw.get("job_shape") or "").strip():
            continue
        if active_only and not bool(raw.get("enabled", True)):
            state = load_state(job_id, repo_root=root)
            if not str(state.get("active_run_id") or "").strip():
                continue
        try:
            inferred = _resolve_job_shape_fields(raw)
            candidate = dict(raw)
            candidate.update(
                {
                    "job_shape": str(inferred.get("job_shape") or ""),
                    "job_shape_source": "backfilled_from_job_kind_contract",
                    "job_shape_compatibility_mode": False,
                    "job_shape_migration_status": "legacy_backfilled",
                    "job_shape_migration_reason": "",
                    "updated_utc": timestamp,
                }
            )
            canonical = validate_job_spec(
                candidate,
                now_utc=timestamp,
                repo_root=root,
                allow_legacy_job_shape_compatibility=True,
            )
            canonical.pop("prompt", None)
            _write_json_atomic(path, canonical)
            processed.append(
                {
                    "job_id": job_id,
                    "status": "backfilled",
                    "job_shape": str(canonical.get("job_shape") or ""),
                    "path": str(path),
                }
            )
        except Exception as exc:
            rejected = dict(raw)
            rejected["job_shape_migration_status"] = "legacy_incompatible"
            rejected["job_shape_migration_reason"] = str(exc)
            rejected["updated_utc"] = timestamp
            _write_json_atomic(path, rejected)
            processed.append(
                {
                    "job_id": job_id,
                    "status": "incompatible",
                    "reason": str(exc),
                    "path": str(path),
                }
            )
    return {
        "ok": True,
        "processed_count": len(processed),
        "backfilled_count": sum(1 for row in processed if str(row.get("status") or "") == "backfilled"),
        "incompatible_count": sum(1 for row in processed if str(row.get("status") or "") == "incompatible"),
        "records": processed,
    }


def delete_job(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    purge_history: bool = False,
) -> Dict[str, Any]:
    spec = load_job(job_id, repo_root=repo_root)
    state = load_state(job_id, repo_root=repo_root)
    active_run_id = str(state.get("active_run_id") or "").strip()
    if active_run_id:
        raise ValueError(
            f"cannot delete scheduled job with active_run_id={active_run_id}; "
            f"cancel it first with infra/user/scheduler-cancel-job"
        )
    claim_health = _active_claim_health(
        job_id,
        repo_root=repo_root,
        max_runtime_seconds=int((spec.get("policy") or {}).get("max_runtime_seconds") or 1800),
        expected_run_id="",
        now=_utc_now(),
    )
    if bool(claim_health.get("present")) and not bool(claim_health.get("stale")):
        raise ValueError(
            "cannot delete scheduled job while a live runtime claim still exists; "
            f"claim_run_id={str(claim_health.get('run_id') or '').strip() or '<unknown>'}"
        )

    deleted_utc = _utc_iso()
    archive_path = (archive_dir(repo_root) / _safe_name(job_id) / f"deleted__{_utc_compact()}.json").resolve()
    _write_json_atomic(
        archive_path,
        {
            "schema_version": "scheduled_job_deleted_v1",
            "job_id": job_id,
            "deleted_utc": deleted_utc,
            "purge_history": bool(purge_history),
            "job": spec,
            "state": state,
        },
    )
    _job_path(job_id, repo_root).unlink(missing_ok=True)
    if purge_history:
        _state_path(job_id, repo_root).unlink(missing_ok=True)
        shutil.rmtree((receipts_dir(repo_root) / _safe_name(job_id)).resolve(), ignore_errors=True)
        shutil.rmtree(job_prompts_dir(job_id, repo_root), ignore_errors=True)
    return {
        "ok": True,
        "job_id": job_id,
        "deleted": True,
        "purge_history": bool(purge_history),
        "deleted_utc": deleted_utc,
        "archive_path": str(archive_path),
    }


def archive_terminal_once_job(
    job_id: str,
    *,
    run_id: str,
    finished_utc: str,
    reason: str = "once_job_completed",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    spec = load_job(job_id, repo_root=repo_root)
    state = load_state(job_id, repo_root=repo_root)
    archived_spec = dict(spec)
    archived_prompt_meta = (
        dict(archived_spec.get("prompt_meta") or {})
        if isinstance(archived_spec.get("prompt_meta"), dict)
        else {}
    )
    archived_prompt_meta["retired_terminal_once"] = {
        "retired_utc": str(finished_utc or _utc_iso()).strip() or _utc_iso(),
        "run_id": str(run_id or "").strip(),
        "reason": str(reason or "once_job_completed").strip() or "once_job_completed",
    }
    archived_spec["prompt_meta"] = archived_prompt_meta
    archived_spec["enabled"] = False
    archive_path = (archive_dir(repo_root) / _safe_name(job_id) / f"retired_terminal_once__{_utc_compact()}.json").resolve()
    _write_json_atomic(
        archive_path,
        {
            "schema_version": "scheduled_job_retired_terminal_once_v1",
            "job_id": job_id,
            "archived_utc": str(finished_utc or _utc_iso()).strip() or _utc_iso(),
            "run_id": str(run_id or "").strip(),
            "reason": str(reason or "once_job_completed").strip() or "once_job_completed",
            "job": archived_spec,
            "state": state,
        },
    )
    _job_path(job_id, repo_root).unlink(missing_ok=True)
    return {
        "ok": True,
        "job_id": job_id,
        "archived": True,
        "archive_path": str(archive_path),
    }


def set_job_enabled(job_id: str, enabled: bool, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    spec = load_job(job_id, repo_root=repo_root)
    spec["enabled"] = bool(enabled)
    return save_job(spec, repo_root=repo_root)


def update_job_run_profile(
    job_id: str,
    *,
    model: Optional[str] = None,
    max_iterations: Optional[int] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    spec = load_job(job_id, repo_root=repo_root)

    normalized_model = str(model or "").strip()
    if normalized_model:
        runtime = dict(spec.get("runtime") or {})
        runtime["model"] = normalized_model
        inferred_tech = _infer_agent_tech_from_model(normalized_model)
        if inferred_tech:
            runtime["agent_tech"] = inferred_tech
        spec["runtime"] = runtime
        if _normalize_job_kind(spec.get("job_kind")) == "build_verify_loop":
            loop = dict(spec.get("loop") or {})
            for key in ("builder_runtime", "verifier_runtime"):
                current = loop.get(key)
                runtime_override = dict(current or {})
                runtime_override["model"] = normalized_model
                if inferred_tech:
                    runtime_override["agent_tech"] = inferred_tech
                loop[key] = runtime_override
            spec["loop"] = loop

    if max_iterations is not None and _normalize_job_kind(spec.get("job_kind")) == "build_verify_loop":
        loop = dict(spec.get("loop") or {})
        loop["max_iterations"] = int(max_iterations)
        spec["loop"] = loop

    return save_job(spec, repo_root=repo_root)


def list_jobs(
    *,
    repo_root: Optional[Path] = None,
    now_utc: Optional[str] = None,
    include_context_resolution: bool = True,
    include_reconciliation: bool = True,
) -> List[Dict[str, Any]]:
    now = _parse_utc(now_utc) if now_utc else _utc_now()
    records: List[Dict[str, Any]] = []
    rows_by_id: Dict[str, Dict[str, Any]] = {}
    out: List[Dict[str, Any]] = []
    for path in sorted(jobs_dir(repo_root).glob("*.json"), key=lambda p: p.name):
        spec = _read_json(path)
        if not spec:
            continue
        spec = _persist_legacy_job_shape_migration_marker(spec, repo_root=repo_root)
        state = load_state(str(spec.get("job_id") or ""), repo_root=repo_root)
        due = due_status(spec, state=state, now=now, repo_root=repo_root)
        record = {"spec": spec, "state": state, "due": due}
        records.append(record)
        job_id = str(spec.get("job_id") or "").strip()
        if job_id:
            rows_by_id[job_id] = {
                "job_id": job_id,
                "enabled": bool(spec.get("enabled")),
                "schedule": dict(spec.get("schedule") or {}),
                "depends_on": list(spec.get("depends_on") or []),
                "state": dict(state),
                "due": dict(due),
            }
    for record in records:
        out.append(
            job_summary(
                record["spec"],
                state=record["state"],
                due=record["due"],
                repo_root=repo_root,
                now=now,
                include_context_resolution=include_context_resolution,
                include_reconciliation=include_reconciliation,
                rows_by_id=rows_by_id,
            )
        )
    return out


def job_summary(
    spec: Dict[str, Any],
    *,
    state: Optional[Dict[str, Any]] = None,
    due: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    include_context_resolution: bool = True,
    include_reconciliation: bool = True,
    rows_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    st = dict(state or {})
    active_run_id = str(st.get("active_run_id") or "").strip()
    if active_run_id:
        policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
        claim_health = _active_claim_health(
            str(spec.get("job_id") or ""),
            repo_root=repo_root,
            max_runtime_seconds=int(policy.get("max_runtime_seconds") or 1800),
            expected_run_id=active_run_id,
            now=now,
        )
        st["active_claim_present"] = bool(claim_health.get("present"))
        st["active_claim_stale"] = bool(claim_health.get("stale"))
        st["active_claim_reason"] = str(claim_health.get("reason") or "")
        st["active_claim_heartbeat_utc"] = str(claim_health.get("heartbeat_utc") or "")
        st["active_claim_age_s"] = claim_health.get("age_s")
    else:
        st["active_claim_present"] = False
        st["active_claim_stale"] = False
        st["active_claim_reason"] = ""
        st["active_claim_heartbeat_utc"] = ""
        st["active_claim_age_s"] = None
    dd = dict(due or due_status(spec, state=st, now=now, repo_root=repo_root))
    reconciliation = _build_scheduler_reconciliation(spec, state=st, due=dd, now=now, repo_root=repo_root) if include_reconciliation else {}
    job_kind = _normalize_job_kind(spec.get("job_kind"))
    job_shape_semantics = _job_shape_semantics_projection(
        spec,
        raw_state=str(st.get("last_status") or ""),
        active_run=bool(active_run_id),
        active_loop_stage=str(st.get("active_loop_stage") or ""),
    )
    job_shape_fields = {
        "job_shape": str(job_shape_semantics.get("job_shape") or ""),
        "job_shape_source": str(job_shape_semantics.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_semantics.get("job_shape_compatibility_mode")),
        "job_shape_migration_status": str(job_shape_semantics.get("job_shape_migration_status") or ""),
        "job_shape_migration_reason": str(job_shape_semantics.get("job_shape_migration_reason") or ""),
    }
    lifecycle_projection = dict(job_shape_semantics.get("lifecycle_projection") or {})
    job_shape_context = _job_shape_context_obligation_projection(spec) if include_context_resolution else {}
    role_meta = _role_target(spec.get("role") or "generalist") if job_kind == "single_turn" else None
    budget_status = _job_budget_status_projection(spec, state=st, repo_root=repo_root)
    scheduler_projection = scheduler_projection_for_job(
        spec,
        state=st,
        due=dd,
        repo_root=repo_root,
        now=now,
        rows_by_id=rows_by_id,
    )
    scheduler_projection_v1 = _scheduler_projection_contract.project_scheduler_job_row(
        {
            "job_id": str(spec.get("job_id") or ""),
            "enabled": bool(spec.get("enabled")),
            "display_title": job_display_title(spec),
            "schedule": dict(spec.get("schedule") or {}),
            "state": st,
            "due": dd,
            "lifecycle_projection": lifecycle_projection,
        }
    )
    scheduler_status = str(scheduler_projection_v1.get("projected_status") or "").strip()
    scheduler_status_reason = str(scheduler_projection_v1.get("projection_reason") or "").strip()
    scheduler_truth_degraded = bool(scheduler_projection_v1.get("durable_conflict")) or scheduler_status == "ambiguous"
    return {
        "job_id": str(spec.get("job_id") or ""),
        "job_kind": job_kind,
        "job_shape": str(job_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
        "job_shape_migration_status": str(job_shape_fields.get("job_shape_migration_status") or ""),
        "job_shape_migration_reason": str(job_shape_fields.get("job_shape_migration_reason") or ""),
        "enabled": bool(spec.get("enabled")),
        "role": str(spec.get("role") or ""),
        "runtime_role": "" if role_meta is None else role_meta["runtime_role"],
        "binding": dict(spec.get("binding") or {}),
        "depends_on": list(spec.get("depends_on") or []),
        "schedule": dict(spec.get("schedule") or {}),
        "expected_output": dict(spec.get("expected_output") or {}),
        "policy": dict(spec.get("policy") or {}),
        "runtime": dict(spec.get("runtime") or {}),
        "swarm": dict(spec.get("swarm") or {}),
        "project_run": dict(spec.get("project_run") or {}),
        "display_title": job_display_title(spec),
        "prompt_ref": str(spec.get("prompt_ref") or ""),
        "prompt_sha256": str(spec.get("prompt_sha256") or ""),
        "prompt_preview": str(spec.get("prompt_preview") or _normalize_prompt_preview(str(spec.get("prompt") or ""))),
        "context_refs": list(spec.get("context_refs") or []),
        "required_context_bundle": str(spec.get("required_context_bundle") or ""),
        "additional_context_refs": list(spec.get("additional_context_refs") or []),
        "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
        "effective_context_refs": _effective_context_refs(spec) if include_context_resolution else [],
        "job_shape_context": job_shape_context,
        "budget_status": budget_status,
        "job_shape_summary": {
            "recognized": bool(job_shape_semantics.get("recognized")),
            "operator_summary": str(job_shape_semantics.get("operator_summary") or ""),
            "progress_semantics": str(job_shape_semantics.get("progress_semantics") or ""),
            "recovery_semantics": str(job_shape_semantics.get("recovery_semantics") or ""),
            "child_launch_semantics": str(job_shape_semantics.get("child_launch_semantics") or ""),
            "integrator_owner": str(job_shape_semantics.get("integrator_owner") or ""),
            "legal_child_shapes": list(job_shape_semantics.get("legal_child_shapes") or []),
            "future_child_shapes": list(job_shape_semantics.get("future_child_shapes") or []),
            "error": str(job_shape_semantics.get("error") or ""),
        },
        "loop": dict(spec.get("loop") or {}),
        "created_utc": str(spec.get("created_utc") or ""),
        "updated_utc": str(spec.get("updated_utc") or ""),
        "state": st,
        "due": dd,
        "lifecycle_projection": lifecycle_projection,
        "scheduler_projection": scheduler_projection,
        "scheduler_projection_v1": scheduler_projection_v1,
        "scheduler_status": scheduler_status,
        "scheduler_status_reason": scheduler_status_reason,
        "scheduler_truth_degraded": scheduler_truth_degraded,
        "scheduler_status_source": "scheduler_projection_v1",
        "reconciliation": reconciliation,
    }


def _scheduler_projection_failure_status(state: Dict[str, Any]) -> str:
    status = str(state.get("last_status") or "").strip().lower()
    if status in {"error", "failed", "failed_verification", "proof_gap", "cancelled", "blocked"}:
        return status
    return ""


def _scheduler_projection_supporting_row(
    job_id: str,
    *,
    rows_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
) -> Optional[Dict[str, Any]]:
    normalized = str(job_id or "").strip()
    if not normalized:
        return None
    if isinstance(rows_by_id, dict) and normalized in rows_by_id:
        row = rows_by_id.get(normalized)
        return dict(row or {}) if isinstance(row, dict) else None
    try:
        spec = load_job(normalized, repo_root=repo_root)
    except FileNotFoundError:
        return None
    state = load_state(normalized, repo_root=repo_root)
    due = due_status(spec, state=state, now=now or _utc_now(), repo_root=repo_root)
    return {
        "job_id": normalized,
        "enabled": bool(spec.get("enabled")),
        "schedule": dict(spec.get("schedule") or {}),
        "depends_on": list(spec.get("depends_on") or []),
        "state": dict(state),
        "due": dict(due),
    }


def _scheduler_projection_priority(
    *,
    classification: str,
    due: Dict[str, Any],
) -> Dict[str, Any]:
    due_reason = str(due.get("reason") or "")
    due_utc = str(due.get("due_utc") or "")
    if classification == "active":
        bucket = "active"
        rank = -1
        executable = False
    elif classification == "queued_run_now":
        bucket = "queued_run_now"
        rank = 0
        executable = True
    elif classification == "ready":
        bucket = "ready"
        rank = 1
        executable = True
    elif classification == "idle":
        bucket = "idle"
        rank = 2
        executable = False
    elif classification == "blocked":
        bucket = "blocked"
        rank = 3
        executable = False
    elif classification == "failed":
        bucket = "failed"
        rank = 4
        executable = False
    elif classification == "completed_once":
        bucket = "completed_once"
        rank = 5
        executable = False
    elif classification == "disabled":
        bucket = "disabled"
        rank = 6
        executable = False
    else:
        bucket = "ambiguous"
        rank = 7
        executable = False
    return {
        "priority_bucket": bucket,
        "priority_rank": rank,
        "due_utc": due_utc,
        "due_reason": due_reason,
        "slot_key": str(due.get("slot_key") or ""),
        "executable_now": executable,
    }


def scheduler_projection_for_job(
    spec: Dict[str, Any],
    *,
    state: Optional[Dict[str, Any]] = None,
    due: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    rows_by_id: Optional[Dict[str, Dict[str, Any]]] = None,
    _memo: Optional[Dict[str, Dict[str, Any]]] = None,
    _stack: Optional[set[str]] = None,
) -> Dict[str, Any]:
    st = dict(state or {})
    dd = dict(due or due_status(spec, state=st, now=now, repo_root=repo_root))
    current = now or _utc_now()
    memo = _memo if isinstance(_memo, dict) else {}
    stack = _stack if isinstance(_stack, set) else set()
    job_id = str(spec.get("job_id") or "").strip()
    if job_id and job_id in memo:
        return memo[job_id]

    enabled = bool(spec.get("enabled"))
    schedule = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    schedule_mode = str(schedule.get("mode") or "").strip().lower()
    active_run_id = str(st.get("active_run_id") or "").strip()
    active_claim_present = bool(st.get("active_claim_present"))
    active_claim_stale = bool(st.get("active_claim_stale"))
    run_now_requested_utc = str(st.get("run_now_requested_utc") or "").strip()
    last_nonlaunch_status = str(st.get("last_nonlaunch_status") or "").strip().lower()
    last_nonlaunch_reason = str(st.get("last_nonlaunch_reason") or "").strip()
    failure_status = _scheduler_projection_failure_status(st)
    completed_once = schedule_mode == "once" and (
        str(dd.get("reason") or "") == "already_executed"
        or (_is_success_status(st.get("last_status")) and not run_now_requested_utc)
    )
    queue_requested = bool(run_now_requested_utc)
    supporting_sources: List[str] = []
    ignored_sources: List[str] = []
    conflicts: List[Dict[str, Any]] = []
    dependency_status: Dict[str, Any] = {
        "blocked": False,
        "blocking_job_id": "",
        "blocking_classification": "",
        "blocking_reason": "",
        "missing_dependency_ids": [],
    }

    stack_key = job_id or f"anon:{id(spec)}"
    if stack_key in stack:
        projection = {
            "schema_version": "scheduled_job_truth_projection_v1",
            "job_id": job_id,
            "evaluated_utc": _utc_iso(current),
            "classification": "ambiguous",
            "status": "ambiguous",
            "terminal": False,
            "ambiguous": True,
            "completed_once": False,
            "failed": False,
            "blocked": False,
            "active": False,
            "queued_run_now": False,
            "provenance": {
                "decision_source": "dependency_cycle",
                "supporting_sources": ["dependency_cycle"],
                "ignored_sources": [],
                "conflicts": [{"code": "dependency_cycle", "detail": "recursive dependency projection encountered"}],
            },
            "freshness": {
                "derived_utc": _utc_iso(current),
                "state_updated_utc": str(st.get("last_finished_utc") or st.get("last_started_utc") or ""),
                "active_claim_heartbeat_utc": str(st.get("active_claim_heartbeat_utc") or ""),
                "degraded": True,
                "degraded_reason": "dependency_cycle",
            },
            "dependency": dependency_status,
            "ordering": _scheduler_projection_priority(classification="ambiguous", due=dd),
        }
        if job_id:
            memo[job_id] = projection
        return projection

    if active_run_id and (not active_claim_present or active_claim_stale):
        conflicts.append(
            {
                "code": "active_claim_untrusted",
                "detail": "active_run_id is present but the durable active claim is missing or stale",
            }
        )

    if str(st.get("last_status") or "").strip().lower() == "running" and not active_run_id:
        conflicts.append(
            {
                "code": "running_without_active_run",
                "detail": "durable scheduler state still says running but no active_run_id survives",
            }
        )

    if active_claim_present and not active_run_id:
        conflicts.append(
            {
                "code": "active_claim_without_active_run",
                "detail": "a durable active claim survived without a matching active_run_id",
            }
        )

    if active_run_id and (completed_once or failure_status or last_nonlaunch_status):
        if completed_once:
            ignored_sources.append("completed_once")
        if failure_status:
            ignored_sources.append(f"failure:{failure_status}")
        if last_nonlaunch_status:
            ignored_sources.append(f"nonlaunch:{last_nonlaunch_status}")
        conflicts.append(
            {
                "code": "active_conflicts_with_terminal_or_blocked_truth",
                "detail": "active run overlaps stronger non-active durable evidence",
            }
        )

    if completed_once and failure_status:
        conflicts.append(
            {
                "code": "terminal_success_failure_conflict",
                "detail": f"job shows both completed_once and failure status={failure_status}",
            }
        )

    if enabled and not completed_once:
        stack.add(stack_key)
        for dep in list(spec.get("depends_on") or []):
            dep_job_id = str(dep or "").strip()
            if not dep_job_id:
                continue
            dep_row = _scheduler_projection_supporting_row(
                dep_job_id,
                rows_by_id=rows_by_id,
                repo_root=repo_root,
                now=current,
            )
            if dep_row is None:
                dependency_status["blocked"] = True
                dependency_status["blocking_job_id"] = dep_job_id
                dependency_status["blocking_reason"] = "missing_dependency_truth"
                dependency_status["missing_dependency_ids"].append(dep_job_id)
                conflicts.append(
                    {
                        "code": "missing_dependency_truth",
                        "detail": f"dependency {dep_job_id} could not be loaded from durable scheduler truth",
                    }
                )
                break
            dep_projection = scheduler_projection_for_job(
                {"job_id": dep_job_id, "enabled": dep_row.get("enabled"), "schedule": dep_row.get("schedule"), "depends_on": dep_row.get("depends_on")},
                state=dep_row.get("state"),
                due=dep_row.get("due"),
                repo_root=repo_root,
                now=current,
                rows_by_id=rows_by_id,
                _memo=memo,
                _stack=stack,
            )
            if bool(dep_projection.get("ambiguous")):
                dependency_status["blocked"] = True
                dependency_status["blocking_job_id"] = dep_job_id
                dependency_status["blocking_classification"] = str(dep_projection.get("classification") or "")
                dependency_status["blocking_reason"] = "dependency_ambiguous"
                conflicts.append(
                    {
                        "code": "dependency_ambiguous",
                        "detail": f"dependency {dep_job_id} projects ambiguous truth",
                    }
                )
                break
            if not bool(dep_projection.get("completed_once")):
                dependency_status["blocked"] = True
                dependency_status["blocking_job_id"] = dep_job_id
                dependency_status["blocking_classification"] = str(dep_projection.get("classification") or "")
                dependency_status["blocking_reason"] = str(
                    (dep_projection.get("ordering") or {}).get("due_reason")
                    or dep_projection.get("classification")
                    or "dependency_not_ready"
                )
                break
        stack.discard(stack_key)

    ambiguous = bool(conflicts)
    decision_source = ""
    classification = "idle"
    if not enabled:
        classification = "disabled"
        decision_source = "disabled_spec"
    elif ambiguous:
        classification = "ambiguous"
        decision_source = "conflicting_durable_truth"
    elif active_run_id:
        classification = "active"
        decision_source = "active_run_claim"
        supporting_sources.append("active_run_claim")
    elif completed_once:
        classification = "completed_once"
        decision_source = "completed_once"
        supporting_sources.append("completed_once")
    elif dependency_status["blocked"]:
        classification = "blocked"
        decision_source = "dependency_truth"
        supporting_sources.append("dependency_truth")
    elif last_nonlaunch_status and last_nonlaunch_reason:
        classification = "blocked"
        decision_source = "nonlaunch_receipt"
        supporting_sources.append("nonlaunch_receipt")
    elif queue_requested:
        classification = "queued_run_now"
        decision_source = "run_now_intent"
        supporting_sources.append("run_now_intent")
    elif failure_status:
        classification = "failed"
        decision_source = f"terminal_failure:{failure_status}"
        supporting_sources.append(f"terminal_failure:{failure_status}")
    elif bool(dd.get("eligible")) and str(dd.get("reason") or "") in {"due", "due_failed_slot_reusable"}:
        classification = "ready"
        decision_source = str(dd.get("reason") or "due")
        supporting_sources.append(decision_source)
    else:
        classification = "idle"
        decision_source = str(dd.get("reason") or "not_due_yet")
        supporting_sources.append(decision_source)

    degraded_reason = ""
    if ambiguous:
        degraded_reason = "conflicting_durable_truth"
    elif active_run_id and (not active_claim_present or active_claim_stale):
        degraded_reason = "active_claim_untrusted"
    elif dependency_status["missing_dependency_ids"]:
        degraded_reason = "missing_dependency_truth"

    projection = {
        "schema_version": "scheduled_job_truth_projection_v1",
        "job_id": job_id,
        "evaluated_utc": _utc_iso(current),
        "classification": classification,
        "status": classification,
        "terminal": classification in {"completed_once", "failed", "disabled"},
        "ambiguous": ambiguous,
        "completed_once": classification == "completed_once",
        "failed": classification == "failed",
        "blocked": classification == "blocked",
        "active": classification == "active",
        "queued_run_now": classification == "queued_run_now",
        "provenance": {
            "decision_source": decision_source,
            "supporting_sources": supporting_sources,
            "ignored_sources": ignored_sources,
            "conflicts": conflicts,
            "schedule_mode": schedule_mode,
            "run_now_requested_utc": run_now_requested_utc,
            "last_nonlaunch_status": last_nonlaunch_status,
            "last_nonlaunch_reason": last_nonlaunch_reason,
            "last_status": str(st.get("last_status") or ""),
        },
        "freshness": {
            "derived_utc": _utc_iso(current),
            "state_updated_utc": str(st.get("last_finished_utc") or st.get("last_started_utc") or st.get("active_started_utc") or ""),
            "active_claim_heartbeat_utc": str(st.get("active_claim_heartbeat_utc") or ""),
            "degraded": bool(degraded_reason),
            "degraded_reason": degraded_reason,
        },
        "dependency": dependency_status,
        "ordering": _scheduler_projection_priority(classification=classification, due=dd),
    }
    if job_id:
        memo[job_id] = projection
    return projection


def _job_budget_status_projection(
    spec: Dict[str, Any],
    *,
    state: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    require_budget_admission = bool(policy.get("require_budget_admission"))
    job_id = str(spec.get("job_id") or "").strip()
    last_reason_code = str((state or {}).get("last_reason_code") or "").strip()
    base = {
        "schema_version": "scheduled_job_budget_status_v1",
        "job_id": job_id,
        "require_budget_admission": require_budget_admission,
        "present": False,
        "status": "not_configured",
        "attention_state": "none",
        "blocked": False,
        "remaining_minor_units": None,
        "unit": "",
        "snapshot": {},
        "recovery_follow_up": {},
        "last_durable_event": None,
        "operator_summary": "",
        "reason_code": last_reason_code,
        "error": "",
    }
    if not job_id:
        return {
            **base,
            "status": "job_id_missing",
            "attention_state": "danger" if require_budget_admission else "none",
            "blocked": require_budget_admission,
            "operator_summary": (
                "Budget admission is required but the job_id is missing."
                if require_budget_admission
                else "No budget state is available because the job_id is missing."
            ),
        }

    try:
        recovery_follow_up = _job_budget_cost_ledger_store.recovery_follow_up_state(job_id, repo_root=repo_root)
        allocations = _job_budget_cost_ledger_store.read_allocation_records(job_id, repo_root=repo_root)
        cost_events = _job_budget_cost_ledger_store.read_cost_event_records(job_id, repo_root=repo_root)
        has_budget_activity = bool(allocations or cost_events or recovery_follow_up.get("present"))
        if not has_budget_activity and not require_budget_admission:
            return base

        last_durable_event = _job_budget_cost_ledger_store.last_durable_event(job_id, repo_root=repo_root)
        if allocations or cost_events:
            snapshot = _job_budget_cost_ledger_store.replay_job_budget_state(job_id, repo_root=repo_root)
            snapshot = {
                **dict(snapshot),
                "allocation_count": len(allocations),
                "cost_event_count": len(cost_events),
                "last_durable_event": last_durable_event,
            }
        else:
            snapshot = {
                "job_uid": job_id,
                "unit": "",
                "allocated_minor_units": 0,
                "cost_debit_minor_units": 0,
                "cost_credit_minor_units": 0,
                "remaining_minor_units": 0,
                "allocation_count": 0,
                "cost_event_count": 0,
                "last_durable_event": last_durable_event,
            }
        remaining_minor_units = int(snapshot.get("remaining_minor_units") or 0)
        unit = str(snapshot.get("unit") or "").strip() or "minor_units"
        status = "clear"
        attention_state = "info"
        blocked = False
        recovery_status = str(recovery_follow_up.get("status") or "")
        recovery_message = str(recovery_follow_up.get("message") or "").strip()
        if not allocations and not cost_events and require_budget_admission and not recovery_follow_up.get("present"):
            status = "ledger_missing"
            attention_state = "danger"
            blocked = True
            operator_summary = "Budget admission is required, but no durable budget ledger is present."
        elif recovery_status == "blocked":
            status = "recovery_blocked"
            attention_state = "danger"
            blocked = True
            operator_summary = recovery_message or f"Budget recovery is blocking future runs with {remaining_minor_units} {unit} remaining."
        elif recovery_status == "review_required":
            status = "review_required"
            attention_state = "danger"
            blocked = True
            operator_summary = recovery_message or "Budget recovery requires manual review before future runs."
        elif remaining_minor_units <= 0 and (allocations or cost_events or require_budget_admission):
            status = "exhausted"
            attention_state = "danger"
            blocked = True
            operator_summary = f"Budget is exhausted with {remaining_minor_units} {unit} remaining."
        elif recovery_status == "warning":
            status = "warning"
            attention_state = "warning"
            operator_summary = recovery_message or f"Budget recovery warning is active with {remaining_minor_units} {unit} remaining."
        else:
            operator_summary = (
                f"Budget clear with {remaining_minor_units} {unit} remaining "
                f"from {int(snapshot.get('allocated_minor_units') or 0)} allocated."
            )
        return {
            **base,
            "present": bool(has_budget_activity or require_budget_admission),
            "status": status,
            "attention_state": attention_state,
            "blocked": blocked,
            "remaining_minor_units": remaining_minor_units,
            "unit": str(snapshot.get("unit") or ""),
            "snapshot": snapshot,
            "recovery_follow_up": dict(recovery_follow_up or {}),
            "last_durable_event": last_durable_event,
            "operator_summary": operator_summary,
        }
    except Exception as exc:
        try:
            recovery_follow_up = _job_budget_cost_ledger_store.recovery_follow_up_state(job_id, repo_root=repo_root)
        except Exception:
            recovery_follow_up = {}
        recovery_status = str(recovery_follow_up.get("status") or "")
        recovery_message = str(recovery_follow_up.get("message") or "").strip()
        if recovery_status in {"blocked", "review_required", "warning"}:
            return {
                **base,
                "present": True,
                "status": "recovery_blocked" if recovery_status == "blocked" else recovery_status,
                "attention_state": "danger" if recovery_status in {"blocked", "review_required"} else "warning",
                "blocked": recovery_status in {"blocked", "review_required"},
                "recovery_follow_up": dict(recovery_follow_up or {}),
                "operator_summary": recovery_message or f"Durable budget state is unreadable: {exc}",
                "error": str(exc),
            }
        return {
            **base,
            "present": require_budget_admission,
            "status": "ledger_unreadable",
            "attention_state": "danger" if require_budget_admission else "warning",
            "blocked": require_budget_admission,
            "operator_summary": f"Durable budget state is unreadable: {exc}",
            "error": str(exc),
        }


def _daily_due_info(spec: Dict[str, Any], *, created: _dt.datetime, now: _dt.datetime) -> Dict[str, Any]:
    sched = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    tz = ZoneInfo(str(sched.get("timezone") or "UTC"))
    tod = str(sched.get("time_of_day") or "00:00")
    hh = int(tod[:2])
    mm = int(tod[3:5])
    now_local = now.astimezone(tz)
    candidate_date = now_local.date()
    candidate_local = _dt.datetime.combine(candidate_date, _dt.time(hour=hh, minute=mm), tzinfo=tz)
    if now_local < candidate_local:
        candidate_date = candidate_date - _dt.timedelta(days=1)
        candidate_local = _dt.datetime.combine(candidate_date, _dt.time(hour=hh, minute=mm), tzinfo=tz)
    candidate_utc = candidate_local.astimezone(_dt.timezone.utc)
    if candidate_utc < created:
        today_local = _dt.datetime.combine(now_local.date(), _dt.time(hour=hh, minute=mm), tzinfo=tz)
        today_utc = today_local.astimezone(_dt.timezone.utc)
        if today_utc > now or today_utc < created:
            return {
                "eligible": False,
                "slot_key": "",
                "due_utc": _utc_iso(today_utc),
                "reason": "not_due_yet",
            }
        candidate_local = today_local
        candidate_utc = today_utc
        candidate_date = now_local.date()
    return {
        "eligible": candidate_utc <= now,
        "slot_key": str(candidate_date.isoformat()),
        "due_utc": _utc_iso(candidate_utc),
        "reason": "due" if candidate_utc <= now else "not_due_yet",
    }


def _interval_due_info(spec: Dict[str, Any], *, created: _dt.datetime, now: _dt.datetime) -> Dict[str, Any]:
    sched = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    try:
        every_minutes = int(sched.get("every_minutes") or 0)
    except Exception:
        every_minutes = 0
    every_minutes = max(5, min(1440, every_minutes or 30))
    anchor_txt = str(sched.get("start_at_utc") or "").strip()
    anchor = _parse_utc(anchor_txt) if anchor_txt else created
    if now < anchor:
        return {
            "eligible": False,
            "slot_key": "",
            "due_utc": _utc_iso(anchor),
            "reason": "not_due_yet",
        }
    step_s = every_minutes * 60
    elapsed_s = int((now - anchor).total_seconds())
    slot_index = elapsed_s // step_s
    due_utc = anchor + _dt.timedelta(seconds=slot_index * step_s)
    return {
        "eligible": due_utc <= now,
        "slot_key": f"interval:{slot_index}",
        "due_utc": _utc_iso(due_utc),
        "reason": "due" if due_utc <= now else "not_due_yet",
    }


def _active_run_recovery_status(
    spec: Dict[str, Any],
    *,
    state: Dict[str, Any],
    now: Optional[_dt.datetime] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    st = dict(state or {})
    active_run_id = str(st.get("active_run_id") or "").strip()
    if not active_run_id:
        return {"blocking": False, "reason": "", "recover": False}
    job_kind = _normalize_job_kind(spec.get("job_kind"))
    job_shape = str(_resolve_job_shape_fields(spec).get("job_shape") or "").strip()
    policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    if bool(policy.get("allow_overlap")):
        return {"blocking": False, "reason": "", "recover": False}
    cur = now or _utc_now()
    max_runtime_seconds = int(policy.get("max_runtime_seconds") or 1800)
    claim_health = _active_claim_health(
        str(spec.get("job_id") or ""),
        repo_root=repo_root,
        max_runtime_seconds=max_runtime_seconds,
        expected_run_id=active_run_id,
        now=cur,
    )
    claim_reason = str(claim_health.get("reason") or "")
    pid_alive = claim_health.get("pid_alive")
    if bool(claim_health.get("present")) and pid_alive is False:
        return {
            "blocking": False,
            "reason": "",
            "recover": True,
            "recovery_reason": "active_run_claim_pid_dead",
            "active_run_id": active_run_id,
            "claim_reason": "pid_dead",
        }
    if bool(claim_health.get("present")) and not bool(claim_health.get("stale")):
        return {"blocking": True, "reason": "active_run", "recover": False}
    dead_or_missing_process = pid_alive is False or pid_alive is None
    if claim_reason in {"run_id_mismatch", "invalid_probe_utc", "missing_probe_utc"}:
        return {
            "blocking": False,
            "reason": "",
            "recover": True,
            "recovery_reason": f"active_run_claim_{claim_reason}",
            "active_run_id": active_run_id,
            "claim_reason": claim_reason,
        }
    if claim_reason == "stale_heartbeat" and dead_or_missing_process:
        return {
            "blocking": False,
            "reason": "",
            "recover": True,
            "recovery_reason": f"active_run_claim_{claim_reason}",
            "active_run_id": active_run_id,
            "claim_reason": claim_reason,
        }
    if claim_reason == "missing":
        if job_kind == "build_verify_loop" or job_shape == "verification_loop_job":
            return {
                "blocking": True,
                "reason": "truth_ambiguous_running_missing_claim",
                "recover": False,
                "active_run_id": active_run_id,
                "claim_reason": claim_reason,
            }
        started_txt = str(st.get("active_started_utc") or "").strip()
        try:
            started = _parse_utc(started_txt)
        except Exception:
            started = None
        if started is not None:
            active_age_s = max(0.0, (cur - started).total_seconds())
            missing_claim_grace_s = min(60.0, max(15.0, float(max_runtime_seconds) / 10.0))
            if active_age_s > missing_claim_grace_s:
                return {
                    "blocking": False,
                    "reason": "",
                    "recover": True,
                    "recovery_reason": "active_run_claim_missing_grace_exceeded",
                    "active_run_id": active_run_id,
                    "claim_reason": claim_reason,
                    "active_age_s": active_age_s,
                }
            if active_age_s > float(max_runtime_seconds):
                return {
                    "blocking": False,
                    "reason": "",
                    "recover": True,
                    "recovery_reason": "active_run_claim_missing_runtime_exceeded",
                    "active_run_id": active_run_id,
                    "claim_reason": claim_reason,
                    "active_age_s": active_age_s,
                }
    return {"blocking": True, "reason": "active_run", "recover": False}


def due_status(
    spec: Dict[str, Any],
    *,
    state: Optional[Dict[str, Any]] = None,
    now: Optional[_dt.datetime] = None,
    repo_root: Optional[Path] = None,
    _dependency_chain: Optional[set[str]] = None,
) -> Dict[str, Any]:
    st = dict(state or {})
    cur = now or _utc_now()
    dependency_chain = {str(item or "").strip() for item in set(_dependency_chain or set()) if str(item or "").strip()}
    job_id = str(spec.get("job_id") or "").strip()
    if job_id:
        dependency_chain.add(job_id)
    enabled = bool(spec.get("enabled"))
    job_shape_fields = _resolve_job_shape_fields(spec)
    migration_status = str(job_shape_fields.get("job_shape_migration_status") or "").strip()
    if migration_status == "legacy_incompatible":
        return {
            "eligible": False,
            "reason": "legacy_job_shape_incompatible",
            "slot_key": "",
            "due_utc": "",
        }
    if migration_status == "legacy_migration_required":
        return {
            "eligible": False,
            "reason": "legacy_job_shape_migration_required",
            "slot_key": "",
            "due_utc": "",
        }
    if not enabled:
        return {"eligible": False, "reason": "disabled", "slot_key": "", "due_utc": ""}
    for dep in list(spec.get("depends_on") or []):
        dep_job_id = str(dep or "").strip()
        dep_state = load_state(dep_job_id, repo_root=repo_root)
        if not _is_success_status(dep_state.get("last_status")):
            dep_due_reason = ""
            dep_due_eligible = False
            dep_pending = False
            if dep_job_id and dep_job_id not in dependency_chain:
                try:
                    dep_spec = load_job(dep_job_id, repo_root=repo_root)
                except FileNotFoundError:
                    dep_spec = {}
                if dep_spec:
                    dep_due = due_status(
                        dep_spec,
                        state=dep_state,
                        now=cur,
                        repo_root=repo_root,
                        _dependency_chain=dependency_chain | {dep_job_id},
                    )
                    dep_due_reason = str(dep_due.get("reason") or "").strip()
                    dep_due_eligible = bool(dep_due.get("eligible"))
                    dep_pending = dep_due_eligible or dep_due_reason in {
                        "active_run",
                        "run_now_requested",
                        "due",
                        "due_failed_slot_reusable",
                        "retry_backoff",
                        "not_due_yet",
                    } or dep_due_reason.startswith("dependency_")
            reason_prefix = "dependency_pending" if dep_pending else "dependency_not_ok"
            return {
                "eligible": False,
                "reason": f"{reason_prefix}:{dep_job_id}",
                "slot_key": "",
                "due_utc": "",
                "dependency_job_id": dep_job_id,
                "dependency_last_status": str(dep_state.get("last_status") or ""),
                "dependency_active_run_id": str(dep_state.get("active_run_id") or ""),
                "dependency_run_now_requested_utc": str(dep_state.get("run_now_requested_utc") or ""),
                "dependency_due_reason": dep_due_reason,
                "dependency_due_eligible": dep_due_eligible,
                "dependency_pending": dep_pending,
            }
        try:
            dep_spec = load_job(dep_job_id, repo_root=repo_root)
        except FileNotFoundError:
            dep_spec = {}
        if dep_spec:
            dep_truth = _ledger_bound_family_truth_projection(dep_spec, repo_root=repo_root)
            if bool(dep_truth.get("applies")) and not bool(dep_truth.get("ready")):
                return {
                    "eligible": False,
                    "reason": f"dependency_pending:{dep_job_id}",
                    "slot_key": "",
                    "due_utc": "",
                    "dependency_job_id": dep_job_id,
                    "dependency_last_status": str(dep_state.get("last_status") or ""),
                    "dependency_ledger_uid": str(dep_truth.get("ledger_uid") or ""),
                    "dependency_ledger_status": str(dep_truth.get("status") or ""),
                    "dependency_ledger_missing_reasons": list(dep_truth.get("missing_reasons") or []),
                    "dependency_pending": True,
                }
    created = _parse_utc(spec.get("created_utc") or _utc_iso())
    active_gate = _active_run_recovery_status(spec, state=st, now=cur, repo_root=repo_root)
    if bool(active_gate.get("blocking")):
        return {"eligible": False, "reason": "active_run", "slot_key": "", "due_utc": ""}
    if str(st.get("last_status") or "").strip().lower() == "running" and not str(st.get("active_run_id") or "").strip():
        return {"eligible": False, "reason": "truth_ambiguous_running_without_active_run", "slot_key": "", "due_utc": ""}
    if bool(st.get("active_claim_present")) and not str(st.get("active_run_id") or "").strip():
        return {"eligible": False, "reason": "truth_ambiguous_active_claim_without_active_run", "slot_key": "", "due_utc": ""}
    run_now_requested_utc = str(st.get("run_now_requested_utc") or "").strip()
    if run_now_requested_utc:
        try:
            requested_dt = _parse_utc(run_now_requested_utc)
            requested_utc = _utc_iso(requested_dt)
        except Exception:
            requested_utc = _utc_iso(cur)
        return {
            "eligible": True,
            "reason": "run_now_requested",
            "slot_key": f"manual:{requested_utc}",
            "due_utc": requested_utc,
        }
    sched = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    mode = str(sched.get("mode") or "").strip().lower()
    if mode == "once":
        due_utc = _parse_utc(sched.get("run_at"))
        slot_key = _utc_iso(due_utc)
        last_status = str(st.get("last_status") or "").strip().lower()
        if _is_success_status(st.get("last_status")) and str(st.get("last_reconciliation_reason") or "").strip() == "already_executed":
            return {"eligible": False, "reason": "already_executed", "slot_key": slot_key, "due_utc": slot_key}
        if due_utc <= cur and last_status not in {"", "running", CONDITIONAL_PASS_TERMINAL_STATUS, "ok"} and str(st.get("last_trigger") or "") == "scheduler" and not bool(st.get("last_no_auto_retry")):
            policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
            backoff_s = int(policy.get("scheduler_retry_backoff_seconds") or 60)
            probe_txt = str(st.get("last_finished_utc") or st.get("last_started_utc") or "").strip()
            if probe_txt:
                try:
                    probe_dt = _parse_utc(probe_txt)
                except Exception:
                    probe_dt = None
                if probe_dt is not None:
                    retry_utc = probe_dt + _dt.timedelta(seconds=max(30, backoff_s))
                    if cur < retry_utc:
                        return {
                            "eligible": False,
                            "reason": "retry_backoff",
                            "slot_key": slot_key,
                            "due_utc": _utc_iso(retry_utc),
                        }
        if str(st.get("last_slot_key") or "") == slot_key:
            if _is_success_status(st.get("last_status")):
                return {"eligible": False, "reason": "already_executed", "slot_key": slot_key, "due_utc": slot_key}
            if bool(st.get("last_no_auto_retry")):
                return {"eligible": False, "reason": "terminal_no_auto_retry", "slot_key": slot_key, "due_utc": slot_key}
            if due_utc > cur:
                return {"eligible": False, "reason": "not_due_yet", "slot_key": slot_key, "due_utc": slot_key}
            return {"eligible": True, "reason": "due_failed_slot_reusable", "slot_key": slot_key, "due_utc": slot_key}
        if due_utc > cur:
            return {"eligible": False, "reason": "not_due_yet", "slot_key": slot_key, "due_utc": slot_key}
        return {"eligible": True, "reason": "due", "slot_key": slot_key, "due_utc": slot_key}
    if mode == "interval":
        info = _interval_due_info(spec, created=created, now=cur)
        slot_key = str(info.get("slot_key") or "")
        if slot_key and str(st.get("last_slot_key") or "") == slot_key:
            return {"eligible": False, "reason": "already_executed", "slot_key": slot_key, "due_utc": str(info.get("due_utc") or "")}
        return info
    info = _daily_due_info(spec, created=created, now=cur)
    slot_key = str(info.get("slot_key") or "")
    if slot_key and str(st.get("last_slot_key") or "") == slot_key:
        return {"eligible": False, "reason": "already_executed", "slot_key": slot_key, "due_utc": str(info.get("due_utc") or "")}
    return info


def list_execution_events(*, repo_root: Optional[Path] = None, limit: int = 100) -> List[Dict[str, Any]]:
    return _read_jsonl(ledger_file(repo_root), limit=max(1, min(1000, int(limit))))


def request_run_now(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    requested_utc: Optional[str] = None,
    request_source: str = "manual",
) -> Dict[str, Any]:
    spec = load_job(job_id, repo_root=repo_root)
    if not bool(spec.get("enabled")):
        return {"ok": False, "job_id": job_id, "queued": False, "reason": "disabled"}
    now = _parse_utc(requested_utc) if requested_utc else _utc_now()
    run_id = f"run_now_{_utc_compact(now)}_{uuid.uuid4().hex[:6]}"
    state = load_state(job_id, repo_root=repo_root)
    active_recovery = _active_run_recovery_status(spec, state=state, now=now, repo_root=repo_root)
    if bool(active_recovery.get("recover")):
        state = _apply_active_run_recovery(
            job_id=job_id,
            state=state,
            recovery=active_recovery,
            trigger=str(request_source or "manual"),
            repo_root=repo_root,
            now=now,
        )
    active_gate = _active_run_recovery_status(spec, state=state, now=now, repo_root=repo_root)
    if bool(active_gate.get("blocking")):
        return {"ok": False, "job_id": job_id, "queued": False, "reason": "active_run"}
    request_utc = _utc_iso(now)
    if str(state.get("run_now_requested_utc") or "").strip():
        return {
            "ok": True,
            "job_id": job_id,
            "run_id": str(state.get("run_now_request_run_id") or ""),
            "queued": True,
            "status": "queued",
            "deduped": True,
            "reason": "already_requested",
            "requested_utc": str(state.get("run_now_requested_utc") or ""),
        }
    _write_state(
        job_id,
        {
            **state,
            "run_now_requested_utc": request_utc,
            "run_now_request_run_id": run_id,
            "run_now_request_source": str(request_source or "manual"),
        },
        repo_root=repo_root,
    )
    _append_event(
        repo_root=repo_root,
        job_id=job_id,
        run_id=run_id,
        event="run_now_requested",
        trigger=str(request_source or "manual"),
        status=str(state.get("last_status") or ""),
        details={"requested_utc": request_utc},
    )
    binding_result = _bind_scheduler_launch(
        spec=spec,
        run_id=run_id,
        trigger=str(request_source or "manual"),
        status="run_now_requested",
        reason="run_now_requested",
        event="run_now_requested",
        receipt_path="",
        report_path="",
        repo_root=repo_root,
        reference_status="execution_ref_unresolved",
        reference_status_reason="operator_run_now_requested",
        writer="scheduler.operator.bind",
        launch_cause="operator_action",
    )
    return {
        "ok": True,
        "job_id": job_id,
        "run_id": run_id,
        "queued": True,
        "status": "queued",
        "requested_utc": request_utc,
        "reason": "run_now_requested",
        "binding_result": binding_result,
    }


def _request_autonomous_continuation_job(
    child_queue: Dict[str, Any],
    *,
    finished_utc: str,
    repo_root: Optional[Path] = None,
    request_source: str = "autonomous_continuation",
) -> Dict[str, Any]:
    queue = child_queue if isinstance(child_queue, dict) else {}
    next_job_id = str(queue.get("next_ready_job_id") or "").strip()
    if not next_job_id:
        return {"ok": True, "requested": False, "reason": "no_next_ready_job"}
    request = request_run_now(
        next_job_id,
        repo_root=repo_root,
        requested_utc=finished_utc,
        request_source=request_source,
    )
    return {
        "ok": bool(request.get("ok")),
        "requested": bool(request.get("ok")),
        "requested_job_id": next_job_id,
        "request_source": request_source,
        "request_result": dict(request),
    }


def list_receipts(job_id: str, *, repo_root: Optional[Path] = None, limit: int = 50) -> List[Dict[str, Any]]:
    base = (receipts_dir(repo_root) / _safe_name(job_id)).resolve()
    if not base.exists() or not base.is_dir():
        return []
    rows: List[Dict[str, Any]] = []
    for path in sorted(base.glob("*.json"), key=lambda p: p.name, reverse=True):
        obj = _read_json(path)
        if obj:
            obj.update(_resolve_job_shape_fields(obj))
            rows.append(obj)
        if len(rows) >= max(1, min(200, int(limit))):
            break
    return rows


def load_receipt(job_id: str, run_id: str, *, repo_root: Optional[Path] = None) -> JobReceipt:
    path = (receipts_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(run_id)}.json").resolve()
    obj = _read_json(path)
    if not obj:
        raise FileNotFoundError(f"scheduled receipt not found: {job_id}/{run_id}")
    obj.update(_resolve_job_shape_fields(obj))
    return obj


def _write_state(job_id: str, state: Dict[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    row = dict(state or {})
    row["job_id"] = str(job_id or "")
    row["updated_utc"] = _utc_iso()
    _write_json_atomic(_state_path(job_id, repo_root), row)
    return row


def _write_active_loop_state(
    job_id: str,
    *,
    run_id: str,
    iteration: int,
    stage: str,
    completed_iterations: int,
    max_iterations: int,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    state = load_state(job_id, repo_root=repo_root)
    if str(state.get("active_run_id") or "").strip() != str(run_id or "").strip():
        return state
    state["active_loop_iteration"] = int(iteration)
    state["active_loop_stage"] = str(stage or "").strip()
    state["active_loop_completed_iterations"] = int(completed_iterations)
    state["active_loop_max_iterations"] = int(max_iterations)
    state["active_loop_updated_utc"] = _utc_iso()
    return _write_state(job_id, state, repo_root=repo_root)


def _touch_active_loop_state(
    job_id: str,
    *,
    run_id: str,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    state = load_state(job_id, repo_root=repo_root)
    if str(state.get("active_run_id") or "").strip() != str(run_id or "").strip():
        return state
    state["active_loop_updated_utc"] = _utc_iso(now or _utc_now())
    return _write_state(job_id, state, repo_root=repo_root)


def _append_event(
    *,
    repo_root: Optional[Path] = None,
    job_id: str,
    run_id: str = "",
    event: str,
    trigger: str,
    status: str = "",
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row = {
        "schema_version": LEDGER_SCHEMA_VERSION,
        "utc": _utc_iso(),
        "job_id": str(job_id or ""),
        "run_id": str(run_id or ""),
        "event": str(event or ""),
        "trigger": str(trigger or ""),
        "status": str(status or ""),
    }
    if isinstance(details, dict) and details:
        row["details"] = dict(details)
    _append_jsonl(ledger_file(repo_root), row)
    return row


def _apply_active_run_recovery(
    *,
    job_id: str,
    state: Dict[str, Any],
    recovery: Dict[str, Any],
    trigger: str,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    if not bool(recovery.get("recover")):
        return dict(state or {})
    st = dict(state or {})
    recovered_run_id = str(st.get("active_run_id") or "").strip()
    if not recovered_run_id:
        return st
    recovered_utc = _utc_iso(now or _utc_now())
    updated = {
        **st,
        **_blank_state_receipt_projection(),
        "active_run_id": "",
        "active_started_utc": "",
        "active_loop_iteration": 0,
        "active_loop_stage": "",
        "active_loop_completed_iterations": 0,
        "active_loop_max_iterations": 0,
        "active_loop_updated_utc": "",
        "last_run_id": recovered_run_id,
        "last_started_utc": str(st.get("active_started_utc") or ""),
        "last_finished_utc": recovered_utc,
        "last_status": "interrupted",
        # Recovery should not arm the normal scheduler retry backoff gate.
        "last_trigger": "recovery",
        "last_receipt_path": "",
        "last_report_path": "",
        "run_now_requested_utc": "",
        "run_now_request_run_id": "",
        "run_now_request_source": "",
        "last_nonlaunch_reason": "",
        "last_nonlaunch_status": "",
        "last_nonlaunch_utc": "",
        "last_nonlaunch_run_id": "",
        "last_nonlaunch_receipt_path": "",
        "last_nonlaunch_report_path": "",
        "last_nonlaunch_signature": "",
        "last_recovered_run_id": recovered_run_id,
        "last_recovered_reason": str(recovery.get("recovery_reason") or "active_run_recovered"),
        "last_recovered_utc": recovered_utc,
    }
    try:
        recovered_spec = load_job(job_id, repo_root=repo_root)
    except FileNotFoundError:
        recovered_spec = {}
    synthesized_success = _maybe_recover_completed_governance_decomposition(
        job_id=job_id,
        state=st,
        spec=recovered_spec,
        trigger=trigger,
        recovered_run_id=recovered_run_id,
        recovered_utc=recovered_utc,
        repo_root=repo_root,
    )
    if synthesized_success:
        return synthesized_success
    recovered_shape = str(_resolve_job_shape_fields(recovered_spec).get("job_shape") or "") if recovered_spec else ""
    if recovered_shape:
        lifecycle_projection = _job_shape_lifecycle_contract.lifecycle_projection_for_shape_record(
            recovered_shape,
            "interrupted",
            active_run=False,
        )
        updated["last_shape_canonical_state"] = str(lifecycle_projection.get("canonical_state") or "")
        updated["last_shape_summary_bucket"] = str(lifecycle_projection.get("summary_bucket") or "")
        updated["last_shape_terminal_summary_class"] = str(lifecycle_projection.get("terminal_summary_class") or "")
        updated["last_shape_recovery_class"] = str(lifecycle_projection.get("recovery_class") or "")
        updated["last_shape_lifecycle_recognized"] = bool(lifecycle_projection.get("recognized"))
    _write_state(job_id, updated, repo_root=repo_root)
    details = {
        "recovered_run_id": recovered_run_id,
        "recovery_reason": str(recovery.get("recovery_reason") or "active_run_recovered"),
        "claim_reason": str(recovery.get("claim_reason") or ""),
    }
    if recovery.get("active_age_s") is not None:
        details["active_age_s"] = float(recovery.get("active_age_s"))
    _append_event(
        repo_root=repo_root,
        job_id=job_id,
        run_id=recovered_run_id,
        event="active_run_recovered",
        trigger=trigger,
        status="interrupted",
        details=details,
    )
    _release_job_claim(
        job_id,
        repo_root=repo_root,
        run_id=recovered_run_id,
        terminal_status="interrupted",
        detail=(
            f"runtime claim released for scheduled job run {recovered_run_id}; "
            f"recovery_reason={str(recovery.get('recovery_reason') or 'active_run_recovered')}"
        ),
    )
    return updated


def _maybe_recover_completed_governance_decomposition(
    *,
    job_id: str,
    state: Dict[str, Any],
    spec: Dict[str, Any],
    trigger: str,
    recovered_run_id: str,
    recovered_utc: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    if _normalize_job_kind(spec.get("job_kind")) != "build_verify_loop":
        return {}
    expected_output = spec.get("expected_output") if isinstance(spec.get("expected_output"), dict) else {}
    if str(expected_output.get("kind") or "").strip() != "governance_decomposition_patch":
        return {}
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    if str(binding.get("type") or "").strip() != "ledger_uid":
        return {}
    parent_uid = str(binding.get("id") or "").strip()
    if not parent_uid:
        return {}
    parent_dir = _resolve_ledger_item_dir_for_uid(parent_uid, repo_root=repo_root)
    if parent_dir is None:
        return {}
    parent_meta = _read_json((parent_dir / "meta.json").resolve()) or {}
    integration_meta = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    child_uids = [str(x).strip() for x in list(integration_meta.get("child_uids") or []) if str(x).strip()]
    if not child_uids:
        return {}

    prompt_text = _resolved_prompt_text(spec, repo_root=repo_root)
    prompt_sha = _sha256_text(prompt_text)
    loop = spec.get("loop") if isinstance(spec.get("loop"), dict) else {}
    runtime = dict(spec.get("runtime") or {})
    job_shape_fields = _resolve_job_shape_fields(spec)
    child_queue = _queue_child_implementation_jobs_after_pass(spec, finished_utc=recovered_utc, repo_root=repo_root)
    continuation_request = _request_autonomous_continuation_job(
        child_queue,
        finished_utc=recovered_utc,
        repo_root=repo_root,
        request_source="governance_decomposition_recovery_handoff",
    )
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "job_kind": "build_verify_loop",
        "job_shape": str(job_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
        "job_id": str(spec.get("job_id") or job_id),
        "run_id": recovered_run_id,
        "trigger": "recovery",
        "status": "ok",
        "role": "",
        "runtime_role": "",
        "binding": dict(binding),
        "schedule": dict(spec.get("schedule") or {}),
        "expected_output": dict(expected_output),
        "context_refs": list(spec.get("context_refs") or []),
        "required_context_bundle": str(spec.get("required_context_bundle") or ""),
        "additional_context_refs": list(spec.get("additional_context_refs") or []),
        "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
        "effective_context_refs": _effective_context_refs(spec),
        "job_shape_context": _job_shape_context_obligation_projection(spec),
        "runtime": runtime,
        "execution_contract": _scheduled_execution_contract(runtime, job_kind="build_verify_loop"),
        "resolved_prompt": {
            "prompt_ref": str(spec.get("prompt_ref") or ""),
            "prompt_sha256": prompt_sha,
            "text": prompt_text,
        },
        "loop": {
            "loop_policy": str(loop.get("loop_policy") or ""),
            "builder_role": str(loop.get("builder_role") or ""),
            "builder_runtime_role": "",
            "builder_runtime": dict(loop.get("builder_runtime") or {}),
            "verifier_role": str(loop.get("verifier_role") or ""),
            "verifier_runtime_role": "",
            "verifier_runtime": dict(loop.get("verifier_runtime") or {}),
            "max_iterations": int(loop.get("max_iterations") or 1),
            "completed_iterations": 1,
            "pass_marker": str(loop.get("pass_marker") or ""),
            "conditional_repair_applied": False,
            "final_verifier_confirmation": False,
            "iterations": [],
        },
        "started_utc": str(state.get("active_started_utc") or ""),
        "finished_utc": recovered_utc,
        "child_job_queue": child_queue,
        "child_job_autostart": continuation_request,
        "decomposition_recovery": {
            "mode": "synthetic_success_from_durable_decomposition_state",
            "parent_uid": parent_uid,
            "child_uids": child_uids,
            "recommended_execution_order": list(integration_meta.get("recommended_execution_order") or []),
            "recommended_first_child_uid": str(integration_meta.get("recommended_first_child_uid") or ""),
            "required_final_child_uid": str(integration_meta.get("required_final_child_uid") or ""),
            "deferred_parent_sections": list(integration_meta.get("deferred_parent_sections") or []),
            "reuse_notes": str(integration_meta.get("reuse_notes") or ""),
        },
        "recovery": {
            "recovered_run_id": recovered_run_id,
            "recovery_reason": "durable_decomposition_state_present",
            "synthetic_closeout": True,
        },
    }
    receipt["job_shape_lifecycle"] = dict(
        _job_shape_lifecycle_contract.lifecycle_projection_for_shape_record(
            str(receipt.get("job_shape") or ""),
            "ok",
            active_run=False,
        )
    )
    receipt_path = (receipts_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(recovered_run_id)}.json").resolve()
    report_path = (reports_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(recovered_run_id)}.md").resolve()
    _write_json_atomic(receipt_path, receipt)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")

    updated = {
        **dict(state or {}),
        **_blank_state_receipt_projection(),
        "active_run_id": "",
        "active_started_utc": "",
        "active_loop_iteration": 0,
        "active_loop_stage": "",
        "active_loop_completed_iterations": 0,
        "active_loop_max_iterations": 0,
        "active_loop_updated_utc": "",
        "last_run_id": recovered_run_id,
        "last_started_utc": str(state.get("active_started_utc") or ""),
        "last_finished_utc": recovered_utc,
        "last_status": "ok",
        "last_trigger": "recovery",
        "last_receipt_path": str(receipt_path),
        "last_report_path": str(report_path),
        "run_now_requested_utc": "",
        "run_now_request_run_id": "",
        "run_now_request_source": "",
        "last_nonlaunch_reason": "",
        "last_nonlaunch_status": "",
        "last_nonlaunch_utc": "",
        "last_nonlaunch_run_id": "",
        "last_nonlaunch_receipt_path": "",
        "last_nonlaunch_report_path": "",
        "last_nonlaunch_signature": "",
        "last_recovered_run_id": recovered_run_id,
        "last_recovered_reason": "durable_decomposition_state_present",
        "last_recovered_utc": recovered_utc,
    }
    schedule = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
    if str(schedule.get("mode") or "").strip().lower() == "once":
        try:
            updated["last_slot_key"] = _utc_iso(_parse_utc(schedule.get("run_at")))
        except Exception:
            pass
    updated.update(_receipt_state_projection(receipt))
    _write_state(job_id, updated, repo_root=repo_root)
    _release_job_claim(
        job_id,
        repo_root=repo_root,
        run_id=recovered_run_id,
        terminal_status="ok",
        detail=(
            f"runtime claim released for scheduled job run {recovered_run_id}; "
            "recovery_reason=durable_decomposition_state_present"
        ),
    )
    _append_event(
        repo_root=repo_root,
        job_id=job_id,
        run_id=recovered_run_id,
        event="active_run_recovered_as_success",
        trigger=trigger,
        status="ok",
        details={
            "recovery_reason": "durable_decomposition_state_present",
            "child_count": len(child_uids),
            "receipt_path": str(receipt_path),
            "report_path": str(report_path),
        },
    )
    return updated


def _read_claim(path: Path) -> Dict[str, Any]:
    """Return a normalized claim row for a scheduled job lock file.

    Acquires the per-path file lock to prevent tearing from concurrent writes.
    Uses RLock so callers that already hold the lock (e.g. _touch_job_claim) do
    not deadlock.
    """
    lk = _file_lock(path)
    with lk:
        row = _read_json(path)
    return row if isinstance(row, dict) else {}


def _scheduled_job_runtime_claimant_uid(job_id: str) -> str:
    return f"scheduled_job:{_safe_name(job_id)}"


def _scheduled_job_runtime_claim_uid(job_id: str, run_id: str) -> str:
    return f"scheduled_job_runtime:{_safe_name(job_id)}:{_safe_name(run_id)}"


def _scheduled_job_runtime_claim_resource() -> Dict[str, str]:
    from experiments_shared import job_family_resource_key as _job_family_resource_key

    return _job_family_resource_key("scheduled_job_runtime").as_dict()


def _record_scheduled_job_runtime_claim_acquired(
    *,
    job_id: str,
    run_id: str,
    trigger: str,
    claimed_at_utc: str,
    claim_row: Optional[Mapping[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    claim_uid = _scheduled_job_runtime_claim_uid(job_id, run_id)
    entry_uid = f"{_safe_name(run_id)}:acquired"
    existing_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=repo_root)
    if existing_state is not None:
        if (
            str(existing_state.get("status") or "") == "acquired"
            and str(existing_state.get("latest_entry_uid") or "") == entry_uid
        ):
            return {
                "claim_uid": claim_uid,
                "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
                "resource": _scheduled_job_runtime_claim_resource(),
                "entry_uid": str(existing_state.get("latest_entry_uid") or entry_uid),
                "recorded_at_utc": str(existing_state.get("latest_recorded_at_utc") or claimed_at_utc),
                "status": "acquired",
                "source": "scheduler.runtime_claim",
            }
        raise ValueError(f"scheduled job runtime claim already exists for {claim_uid}")
    job_kind = _normalize_job_kind((claim_row or {}).get("job_kind"))
    write_result = _resource_claim_ledger.append_claim_record_once(
        {
            "schema_version": _resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": entry_uid,
            "claim_uid": claim_uid,
            "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
            "resource": _scheduled_job_runtime_claim_resource(),
            "recorded_at_utc": claimed_at_utc,
            "status": "acquired",
            "wait_reason_code": "",
            "detail": (
                f"runtime acquired scheduled job claim for {job_id} run {run_id}; "
                f"trigger={str(trigger or '').strip() or 'unknown'}; "
                f"job_kind={job_kind or 'unknown'}"
            ),
            "source": "scheduler.runtime_claim",
        },
        repo_root=repo_root,
    )
    record = dict(write_result.get("record") or {})
    current_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=repo_root) or {}
    if (
        str(current_state.get("status") or "") != "acquired"
        or str(current_state.get("latest_entry_uid") or "") != entry_uid
    ):
        raise ValueError(f"scheduled job runtime claim acquire did not persist cleanly for {claim_uid}")
    return {
        "claim_uid": claim_uid,
        "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
        "resource": _scheduled_job_runtime_claim_resource(),
        "entry_uid": str(record.get("entry_uid") or entry_uid),
        "recorded_at_utc": str(record.get("recorded_at_utc") or claimed_at_utc),
        "status": "acquired",
        "source": str(record.get("source") or "scheduler.runtime_claim"),
    }


def _record_scheduled_job_runtime_claim_release(
    *,
    job_id: str,
    run_id: str,
    terminal_status: str,
    recorded_at_utc: str,
    detail: str = "",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    claim_uid = _scheduled_job_runtime_claim_uid(job_id, run_id)
    claim_status = "cancelled" if str(terminal_status or "").strip().lower() == "cancelled" else "released"
    entry_uid = f"{_safe_name(run_id)}:{claim_status}"
    existing_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=repo_root)
    if existing_state is None:
        raise ValueError(f"scheduled job runtime claim release requires prior acquired state: {claim_uid}")
    if str(existing_state.get("status") or "") != "acquired":
        if (
            str(existing_state.get("status") or "") == claim_status
            and str(existing_state.get("latest_entry_uid") or "") == entry_uid
        ):
            return {
                "claim_uid": claim_uid,
                "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
                "resource": _scheduled_job_runtime_claim_resource(),
                "entry_uid": str(existing_state.get("latest_entry_uid") or entry_uid),
                "recorded_at_utc": str(existing_state.get("latest_recorded_at_utc") or recorded_at_utc),
                "status": claim_status,
                "source": "scheduler.runtime_claim",
            }
        raise ValueError(f"scheduled job runtime claim release requires prior acquired state: {claim_uid}")
    normalized_recorded_at_utc = recorded_at_utc
    acquired_at_utc = str(existing_state.get("latest_recorded_at_utc") or "").strip()
    if acquired_at_utc:
        try:
            if _parse_utc(normalized_recorded_at_utc) < _parse_utc(acquired_at_utc):
                normalized_recorded_at_utc = acquired_at_utc
        except Exception:
            normalized_recorded_at_utc = acquired_at_utc
    write_result = _resource_claim_ledger.append_claim_record_once(
        {
            "schema_version": _resource_claim_ledger.RECORD_SCHEMA_VERSION,
            "entry_uid": entry_uid,
            "claim_uid": claim_uid,
            "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
            "resource": _scheduled_job_runtime_claim_resource(),
            "recorded_at_utc": normalized_recorded_at_utc,
            "status": claim_status,
            "wait_reason_code": "",
            "detail": detail or f"runtime {claim_status} scheduled job claim for {job_id} run {run_id}",
            "source": "scheduler.runtime_claim",
        },
        repo_root=repo_root,
    )
    record = dict(write_result.get("record") or {})
    current_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=repo_root) or {}
    if (
        str(current_state.get("status") or "") != claim_status
        or str(current_state.get("latest_entry_uid") or "") != entry_uid
    ):
        raise ValueError(f"scheduled job runtime claim release did not persist cleanly for {claim_uid}")
    return {
        "claim_uid": claim_uid,
        "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
        "resource": _scheduled_job_runtime_claim_resource(),
        "entry_uid": str(record.get("entry_uid") or entry_uid),
        "recorded_at_utc": str(record.get("recorded_at_utc") or normalized_recorded_at_utc),
        "status": claim_status,
        "source": str(record.get("source") or "scheduler.runtime_claim"),
    }


def _claim_is_stale(path: Path, *, max_runtime_seconds: int, now: Optional[_dt.datetime] = None) -> bool:
    """Decide whether a job claim is stale based on its latest heartbeat."""
    row = _read_claim(path)
    pid = int(row.get("pid") or 0)
    if pid > 0:
        try:
            os.kill(pid, 0)
        except (OSError, ProcessLookupError):
            return True
    probe_utc = str(row.get("heartbeat_utc") or row.get("claimed_utc") or "").strip()
    if not probe_utc:
        return True
    try:
        claimed = _parse_utc(probe_utc)
    except Exception:
        return True
    ttl_s = max(60, min(172800, int(max_runtime_seconds) * 2))
    cur = now or _utc_now()
    return (cur - claimed).total_seconds() > ttl_s


def _active_claim_health(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    max_runtime_seconds: int = 1800,
    expected_run_id: str = "",
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    """Compute a read-only heartbeat health view for a job's current claim."""
    cur = now or _utc_now()
    path = _claim_path(job_id, repo_root)
    row = _read_claim(path)
    if not row:
        return {
            "present": False,
            "stale": True,
            "reason": "missing",
            "run_id": "",
            "claimed_utc": "",
            "heartbeat_utc": "",
            "pid": 0,
            "pid_alive": None,
            "age_s": None,
        }
    run_id = str(row.get("run_id") or "").strip()
    pid = int(row.get("pid") or 0)
    pid_alive: Optional[bool] = None
    if pid > 0:
        try:
            os.kill(pid, 0)
            pid_alive = True
        except (OSError, ProcessLookupError):
            pid_alive = False
    if expected_run_id and run_id and run_id != expected_run_id:
        return {
            "present": True,
            "stale": True,
            "reason": "run_id_mismatch",
            "run_id": run_id,
            "claimed_utc": str(row.get("claimed_utc") or ""),
            "heartbeat_utc": str(row.get("heartbeat_utc") or ""),
            "pid": pid,
            "pid_alive": pid_alive,
            "age_s": None,
        }
    probe_utc = str(row.get("heartbeat_utc") or row.get("claimed_utc") or "").strip()
    if not probe_utc:
        return {
            "present": True,
            "stale": True,
            "reason": "missing_probe_utc",
            "run_id": run_id,
            "claimed_utc": str(row.get("claimed_utc") or ""),
            "heartbeat_utc": str(row.get("heartbeat_utc") or ""),
            "pid": pid,
            "pid_alive": pid_alive,
            "age_s": None,
        }
    try:
        probe_dt = _parse_utc(probe_utc)
    except Exception:
        return {
            "present": True,
            "stale": True,
            "reason": "invalid_probe_utc",
            "run_id": run_id,
            "claimed_utc": str(row.get("claimed_utc") or ""),
            "heartbeat_utc": str(row.get("heartbeat_utc") or ""),
            "pid": pid,
            "pid_alive": pid_alive,
            "age_s": None,
        }
    if probe_dt > (cur + _dt.timedelta(minutes=1)):
        return {
            "present": True,
            "stale": True,
            "reason": "future_probe_utc",
            "run_id": run_id,
            "claimed_utc": str(row.get("claimed_utc") or ""),
            "heartbeat_utc": str(row.get("heartbeat_utc") or ""),
            "pid": pid,
            "pid_alive": pid_alive,
            "age_s": None,
        }
    stale = _claim_is_stale(path, max_runtime_seconds=max_runtime_seconds, now=cur)
    return {
        "present": True,
        "stale": bool(stale),
        "reason": "stale_heartbeat" if stale else "fresh",
        "run_id": run_id,
        "claimed_utc": str(row.get("claimed_utc") or ""),
        "heartbeat_utc": str(row.get("heartbeat_utc") or ""),
        "pid": pid,
        "pid_alive": pid_alive,
        "age_s": max(0.0, (cur - probe_dt).total_seconds()),
    }


def _touch_job_claim(job_id: str, *, repo_root: Optional[Path] = None, run_id: str = "", now: Optional[_dt.datetime] = None) -> bool:
    """Refresh the heartbeat timestamp for the currently-held claim."""
    path = _claim_path(job_id, repo_root)
    lk = _file_lock(path)
    with lk:
        row = _read_claim(path)
        if not row:
            return False
        if run_id and str(row.get("run_id") or "").strip() != str(run_id):
            return False
        row["heartbeat_utc"] = _utc_iso(now or _utc_now())
        tmp = (path.parent / f".{path.name}.tmp").resolve()
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    return True


def _acquire_job_claim(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    run_id: str,
    trigger: str,
    max_runtime_seconds: int,
    spec: Optional[Dict[str, Any]] = None,
    force: bool = False,
    now: Optional[_dt.datetime] = None,
) -> Tuple[bool, Path]:
    claim_shape_fields = _resolve_job_shape_fields(spec) if isinstance(spec, dict) and spec else {}
    path = _claim_path(job_id, repo_root)
    row = {
        "schema_version": "scheduled_job_claim_v1",
        "job_id": str(job_id or ""),
        "run_id": str(run_id or ""),
        "trigger": str(trigger or ""),
        "job_kind": "" if not isinstance(spec, dict) else _normalize_job_kind(spec.get("job_kind")),
        "job_shape": str(claim_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(claim_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(claim_shape_fields.get("job_shape_compatibility_mode")),
        "claimed_utc": _utc_iso(now or _utc_now()),
        "runtime_claim_uid": _scheduled_job_runtime_claim_uid(job_id, run_id),
        "runtime_claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
        "pid": int(os.getpid()),
        "hostname": socket.gethostname(),
    }
    raw = json.dumps(row, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    while True:
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            if _claim_is_stale(path, max_runtime_seconds=max_runtime_seconds, now=now):
                try:
                    path.unlink()
                except FileNotFoundError:
                    continue
                except Exception:
                    return False, path
                continue
            return False, path
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            _record_scheduled_job_runtime_claim_acquired(
                job_id=job_id,
                run_id=run_id,
                trigger=trigger,
                claimed_at_utc=str(row.get("claimed_utc") or ""),
                claim_row=row,
                repo_root=repo_root,
            )
            return True, path
        except Exception:
            try:
                path.unlink()
            except Exception:
                pass
            raise


def _release_job_claim(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    run_id: str = "",
    terminal_status: str = "",
    detail: str = "",
) -> Dict[str, Any]:
    path = _claim_path(job_id, repo_root)
    if not path.exists():
        return {"released": False, "reason": "missing_lock"}
    row = _read_json(path)
    if run_id and str(row.get("run_id") or "").strip() and str(row.get("run_id") or "").strip() != str(run_id):
        return {"released": False, "reason": "run_id_mismatch"}
    claim_run_id = str(row.get("run_id") or run_id or "").strip()
    release_record = {}
    release_record_error = ""
    if claim_run_id:
        try:
            release_record = _record_scheduled_job_runtime_claim_release(
                job_id=job_id,
                run_id=claim_run_id,
                terminal_status=terminal_status,
                recorded_at_utc=_utc_iso(),
                detail=detail,
                repo_root=repo_root,
            )
        except Exception as exc:
            # Operational continuity wins here. If durable job state is already
            # terminal, leaving the lock in place is worse than preserving a
            # bookkeeping gap in the auxiliary claim ledger.
            release_record_error = str(exc)
    try:
        path.unlink()
    except FileNotFoundError:
        return {
            "released": False,
            "reason": "missing_lock_after_release_record",
            "record": release_record,
            "release_record_error": release_record_error,
        }
    return {"released": True, "reason": "", "record": release_record, "release_record_error": release_record_error}


class _ClaimHeartbeat:
    """Background heartbeat that keeps a live claim from being stolen."""

    def __init__(self, *, job_id: str, repo_root: Optional[Path], run_id: str, interval_s: int) -> None:
        self._job_id = job_id
        self._repo_root = repo_root
        self._run_id = run_id
        self._interval_s = max(5, int(interval_s))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        """Start the heartbeat thread if it is not already running."""
        if self._thread is not None:
            return

        def _runner() -> None:
            while not self._stop.wait(self._interval_s):
                try:
                    if not _touch_job_claim(self._job_id, repo_root=self._repo_root, run_id=self._run_id):
                        return
                    _touch_active_loop_state(
                        self._job_id,
                        run_id=self._run_id,
                        repo_root=self._repo_root,
                    )
                except Exception as _hb_exc:
                    # Heartbeat failed; log to job event ledger and exit so the
                    # watchdog can detect the stale claim and transition the job.
                    try:
                        _append_event(
                            repo_root=self._repo_root,
                            job_id=self._job_id,
                            run_id=self._run_id,
                            event="heartbeat_error",
                            trigger="heartbeat",
                            status="running",
                            details={"error": str(_hb_exc)},
                        )
                    except Exception:
                        pass
                    return

        self._thread = threading.Thread(
            target=_runner,
            name=f"scheduled_job_claim_{_safe_name(self._job_id)}",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        """Stop the heartbeat thread and wait briefly for shutdown."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=max(1.0, float(self._interval_s) + 1.0))
            self._thread = None


def _build_dispatch_message(spec: Dict[str, Any], *, trigger: str, run_id: str = "", repo_root: Optional[Path] = None) -> str:
    role_meta = _role_target(spec.get("role") or "generalist")
    lines = [
        _scheduled_controller_contract(spec, stage="single_turn"),
        "",
        "[Scheduled Job]",
        f"job_id: {str(spec.get('job_id') or '').strip()}",
        f"run_id: {str(run_id or '').strip()}",
        f"trigger: {str(trigger or '').strip() or 'scheduler'}",
        f"target_role: {role_meta['role']}",
        f"runtime_role: {role_meta['runtime_role']}",
    ]
    expected = spec.get("expected_output") if isinstance(spec.get("expected_output"), dict) else {}
    kind = str(expected.get("kind") or "").strip()
    fmt = str(expected.get("format") or "").strip()
    if kind or fmt:
        lines.append(f"expected_output: kind={kind or 'report'} format={fmt or 'markdown'}")
    bundle_id = str(spec.get("required_context_bundle") or "").strip()
    job_shape_context = _job_shape_context_obligation_projection(spec)
    if bundle_id:
        lines.append(f"required_context_bundle: {bundle_id}")
    add_refs = [str(x).strip() for x in list(spec.get("additional_context_refs") or []) if str(x).strip()]
    if add_refs:
        lines.extend(["additional_context_refs:"] + [f"- {ref}" for ref in add_refs])
    ban_refs = [str(x).strip() for x in list(spec.get("forbidden_context_refs") or []) if str(x).strip()]
    if ban_refs:
        lines.extend(["forbidden_context_refs:"] + [f"- {ref}" for ref in ban_refs])
    refs = _effective_context_refs(spec)
    if refs:
        lines.extend(["resolved_context_refs:"] + [f"- {ref}" for ref in refs])
    if job_shape_context:
        lines.extend(
            [
                f"context_reentry_precedence: {str(job_shape_context.get('reentry_precedence') or '')}",
                f"context_transcript_summary_policy: {str(job_shape_context.get('transcript_summary_policy') or '')}",
            ]
        )
    lines.extend(
        [
            "",
            *_scheduled_reentry_section(
                run_id=run_id,
                stage="single_turn",
                iteration=1,
                max_iterations=1,
                current_objective=_resolved_prompt_text(spec, repo_root=repo_root),
                current_work_item="Execute this scheduled task directly from the current objective and attached context only.",
                reentry_reason="single scheduled turn with structured state only; no session-global history replay",
                spec=spec,
                repo_root=repo_root,
            ),
            "",
            "Task:",
            _resolved_prompt_text(spec, repo_root=repo_root),
        ]
    )
    return "\n".join(lines).strip()
