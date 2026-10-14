def _detect_pipeline_paused(*, repo_root: Optional[Path] = None, alert_minutes: int = 60) -> Dict[str, Any]:
    """Detect if the pipeline has been paused (all jobs disabled).

    Returns a dict with is_paused, paused_since, and paused_duration_minutes if paused.
    """
    root = Path(repo_root or _repo_root()).resolve()
    jobs_dir_path = jobs_dir(root)

    if not jobs_dir_path.exists():
        return {"is_paused": False}

    # Count enabled and disabled jobs
    enabled_count = 0
    total_count = 0

    for spec_path in jobs_dir_path.glob("*.json"):
        spec = _read_json(spec_path)
        if not spec:
            continue
        total_count += 1
        if bool(spec.get("enabled", True)):
            enabled_count += 1

    # If no jobs at all, not really "paused"
    if total_count == 0:
        return {"is_paused": False}

    # If any job is enabled, not paused
    if enabled_count > 0:
        return {"is_paused": False}

    # All jobs are disabled; check if this is recent
    # Look at the most recent state modification time
    now = _utc_now()
    max_disabled_age_minutes = 0

    for state_path in state_dir(root).glob("*.json"):
        state = _read_json(state_path)
        if not state:
            continue

        updated_utc = str(state.get("updated_utc") or "").strip()
        if updated_utc:
            try:
                updated_dt = _parse_utc(updated_utc)
                age_minutes = int((now - updated_dt).total_seconds() / 60)
                max_disabled_age_minutes = max(max_disabled_age_minutes, age_minutes)
            except Exception:
                pass

    return {
        "is_paused": True,
        "all_jobs_disabled": True,
        "disabled_count": total_count,
        "paused_since_minutes": max_disabled_age_minutes,
        "alert_threshold_minutes": alert_minutes,
        "should_alert": max_disabled_age_minutes > alert_minutes,
    }


def _transition_stale_running_to_blocked(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Watchdog action: transition jobs from running→blocked when their claims are stale.

    This allows dependents to see a non-ok status and the healer to act.
    """
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    root = Path(repo_root or _repo_root()).resolve()
    claims_dir = locks_dir(root)
    transitioned: List[str] = []
    errors: List[Dict[str, Any]] = []

    if not claims_dir.exists():
        return {"ok": True, "transitioned_count": 0, "transitioned_jobs": [], "errors": []}

    for claim_file in claims_dir.glob("*.lock.json"):
        try:
            claim_data = _read_json(claim_file)
            if not claim_data:
                continue

            job_id = str(claim_data.get("job_id") or "").strip()
            if not job_id:
                continue

            state = load_state(job_id, repo_root=root)
            active_run_id = str(state.get("active_run_id") or "").strip()
            last_status = str(state.get("last_status") or "").lower()

            # Only transition if currently marked as running
            if last_status != "running" or not active_run_id:
                continue

            # Check if claim is stale
            spec = load_job(job_id, repo_root=root)
            policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
            max_runtime_seconds = int(policy.get("max_runtime_seconds") or 1800)

            if not _claim_is_stale(claim_file, max_runtime_seconds=max_runtime_seconds):
                continue

            now_utc = _utc_iso()
            claim_uid = _scheduled_job_runtime_claim_uid(job_id, active_run_id)
            claim_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=root)
            if claim_state is not None and str(claim_state.get("status") or "") == "acquired":
                detail = (
                    f"watchdog recovered stale scheduled-job runtime claim for {job_id} run {active_run_id}; "
                    f"claim_file={claim_file.name}"
                )
                _record_scheduled_job_runtime_claim_release(
                    job_id=job_id,
                    run_id=active_run_id,
                    terminal_status="released",
                    recorded_at_utc=now_utc,
                    detail=detail,
                    repo_root=root,
                )
                _append_shared_resource_recovery_record(
                    claim_state=claim_state,
                    recorded_at_utc=now_utc,
                    action="released",
                    reason_code="stale_pid_dead",
                    detail=detail,
                    source="watchdog.resource_recovery",
                    evidence={
                        "job_id": job_id,
                        "run_id": active_run_id,
                        "claim_file": str(claim_file),
                    },
                    repo_root=root,
                )

            # Claim is stale; transition job to blocked
            state.update(_blank_state_receipt_projection())
            state["last_status"] = "blocked"
            state["active_run_id"] = ""
            state["active_started_utc"] = ""
            state["active_loop_iteration"] = 0
            state["active_loop_stage"] = ""
            state["active_loop_completed_iterations"] = 0
            state["active_loop_max_iterations"] = 0
            state["active_loop_updated_utc"] = ""
            state["last_run_id"] = active_run_id
            state["last_finished_utc"] = now_utc
            state["last_trigger"] = "watchdog"
            state["last_receipt_path"] = ""
            state["last_report_path"] = ""
            state["run_now_requested_utc"] = ""
            state["run_now_request_run_id"] = ""
            state["run_now_request_source"] = ""
            state["last_nonlaunch_reason"] = "watchdog_stale_claim_transition"
            state["last_nonlaunch_status"] = "blocked"
            state["last_nonlaunch_utc"] = now_utc
            state["last_nonlaunch_run_id"] = active_run_id
            state["last_nonlaunch_receipt_path"] = ""
            state["last_nonlaunch_report_path"] = ""
            state["last_nonlaunch_signature"] = ""
            state["updated_utc"] = now_utc
            _write_state(job_id, state, repo_root=root)

            # Emit transition event
            _append_event(
                repo_root=root,
                job_id=job_id,
                run_id=active_run_id or f"audit_{_utc_compact()}",
                event="watchdog_stale_claim_transitioned_to_blocked",
                trigger="watchdog",
                status="blocked",
                details={"claim_file": str(claim_file)},
            )
            binding_result = _bind_scheduler_launch(
                spec=spec,
                run_id=active_run_id or f"audit_{_utc_compact()}",
                trigger="watchdog",
                status="blocked",
                reason="watchdog_stale_claim_transition",
                event="watchdog_stale_claim_transitioned_to_blocked",
                receipt_path="",
                report_path="",
                repo_root=root,
                reference_status="execution_ref_conflict",
                reference_status_reason="watchdog_stale_claim_transition",
                writer="watchdog.bind",
                launch_cause="watchdog",
            )
            if binding_result and not bool(binding_result.get("ok")):
                errors.append(
                    {
                        "job_id": job_id,
                        "claim_file": str(claim_file),
                        "error": str(binding_result.get("error") or "binding_failed"),
                    }
                )

            transitioned.append(job_id)
        except Exception as _tr_exc:
            # Record the error but don't let one bad claim block the whole sweep
            errors.append({"claim_file": str(claim_file), "error": str(_tr_exc)})

    return {
        "ok": True,
        "transitioned_count": len(transitioned),
        "transitioned_jobs": transitioned,
        "errors": errors,
    }


def _reconcile_stranded_running_without_live_claim(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Fail closed on durable `running` state that no longer has a live run.

    If runtime residue survives a restart/restore but the live run binding is
    gone, the plane must not quietly treat that job as runnable. Prefer a
    matching terminal receipt when one exists; otherwise block the job.
    """
    root = Path(repo_root or _repo_root()).resolve()
    reconciled_jobs: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []

    for state_path in state_dir(root).glob("*.json"):
        try:
            state = _read_json(state_path)
            if not state:
                continue
            job_id = str(state.get("job_id") or state_path.stem).strip()
            active_run_id = str(state.get("active_run_id") or "").strip()
            last_status = str(state.get("last_status") or "").strip().lower()
            if not job_id or last_status != "running":
                continue
            claim_path = _claim_path(job_id, repo_root=root)
            if active_run_id and claim_path.exists():
                continue
            if not active_run_id and claim_path.exists():
                continue

            now_utc = _utc_iso()
            receipt_payload: Dict[str, Any] = {}
            receipt_path = Path(str(state.get("last_receipt_path") or "").strip())
            if receipt_path.exists():
                candidate_payload = _read_json(receipt_path)
                candidate_run_id = str(candidate_payload.get("run_id") or "").strip()
                if not active_run_id or candidate_run_id == active_run_id:
                    receipt_payload = candidate_payload
                else:
                    receipt_path = Path()
            if not receipt_payload:
                recent_root = receipts_dir(root) / job_id
                for candidate in sorted(recent_root.glob("*.json"), reverse=True):
                    payload = _read_json(candidate)
                    if payload and (not active_run_id or str(payload.get("run_id") or "").strip() == active_run_id):
                        receipt_payload = payload
                        receipt_path = candidate.resolve()
                        break

            receipt_status = str(receipt_payload.get("status") or "").strip()
            receipt_status_lower = receipt_status.lower()
            receipt_run_id = str(receipt_payload.get("run_id") or "").strip()
            receipt_is_terminal = receipt_status_lower not in {"", "running", "queued", "requested", "request_queued"}

            repaired_state = dict(state)
            repaired_state.update(_blank_state_receipt_projection())
            repaired_state["active_run_id"] = ""
            repaired_state["active_started_utc"] = ""
            repaired_state["active_loop_iteration"] = 0
            repaired_state["active_loop_stage"] = ""
            repaired_state["active_loop_completed_iterations"] = 0
            repaired_state["active_loop_max_iterations"] = 0
            repaired_state["active_loop_updated_utc"] = ""
            repaired_state["last_run_id"] = receipt_run_id or str(state.get("last_run_id") or "")
            repaired_state["updated_utc"] = now_utc

            if receipt_is_terminal:
                repaired_state["last_status"] = receipt_status
                repaired_state["last_finished_utc"] = str(
                    receipt_payload.get("finished_at")
                    or receipt_payload.get("finished_utc")
                    or now_utc
                )
                repaired_state["last_trigger"] = "startup"
                repaired_state["last_receipt_path"] = str(receipt_path.resolve()) if receipt_path.exists() else ""
                repaired_state["last_report_path"] = ""
                repaired_state["last_reconciliation_reason"] = (
                    "already_executed" if _is_success_status(receipt_status) else "startup_terminal_receipt_reconciled"
                )
                action = "terminal_receipt_reconciled"
                event = "startup_reconciled_stranded_running_to_terminal_receipt"
                event_status = receipt_status_lower or "unknown"
            else:
                repaired_state["last_status"] = "blocked"
                repaired_state["last_finished_utc"] = now_utc
                repaired_state["last_trigger"] = "startup"
                repaired_state["last_receipt_path"] = ""
                repaired_state["last_report_path"] = ""
                repaired_state["last_nonlaunch_reason"] = "startup_stranded_running_without_live_claim"
                repaired_state["last_nonlaunch_status"] = "blocked"
                repaired_state["last_nonlaunch_utc"] = now_utc
                repaired_state["last_nonlaunch_run_id"] = active_run_id or str(state.get("last_run_id") or "")
                repaired_state["last_nonlaunch_receipt_path"] = ""
                repaired_state["last_nonlaunch_report_path"] = ""
                repaired_state["last_nonlaunch_signature"] = ""
                repaired_state["last_reconciliation_reason"] = "startup_stranded_running_blocked"
                action = "blocked_without_live_claim"
                event = "startup_blocked_stranded_running_without_live_claim"
                event_status = "blocked"

            _write_state(job_id, repaired_state, repo_root=root)
            _append_event(
                repo_root=root,
                job_id=job_id,
                run_id=receipt_run_id or str(state.get("last_run_id") or "") or f"audit_{_utc_compact()}",
                event=event,
                trigger="startup",
                status=event_status,
                details={
                    "action": action,
                    "receipt_status": receipt_status,
                    "receipt_path": str(receipt_path) if receipt_path else "",
                },
            )
            reconciled_jobs.append({"job_id": job_id, "action": action, "receipt_status": receipt_status})
        except Exception as exc:
            errors.append({"state_path": str(state_path), "error": str(exc)})

    return {
        "ok": not errors,
        "reconciled_count": len(reconciled_jobs),
        "reconciled_jobs": reconciled_jobs,
        "errors": errors,
    }


def startup_claim_audit(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Startup audit: clear all claim files with non-alive PIDs.

    Fixes CTRL-C or kill scenarios where the server terminates abruptly,
    leaving claim files on disk with PIDs that no longer exist. Those claims
    would block job re-launch on the next server start.

    Returns a summary dict with count of cleared claims and affected job_ids.
    """
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    root = Path(repo_root or _repo_root()).resolve()
    claims_dir = locks_dir(root)
    cleared: List[str] = []
    errors: List[Dict[str, Any]] = []

    if not claims_dir.exists():
        return {
            "ok": True,
            "cleared_count": 0,
            "cleared_jobs": [],
            "errors": [],
            "utc": _utc_iso(),
        }

    for claim_file in claims_dir.glob("*.lock.json"):
        try:
            claim_data = _read_json(claim_file)
            if not claim_data:
                # Empty claim file; remove it
                try:
                    claim_file.unlink()
                    cleared.append(claim_file.stem.replace(".lock", ""))
                except Exception as e:
                    errors.append({
                        "claim_file": str(claim_file),
                        "error": str(e),
                    })
                continue

            pid = int(claim_data.get("pid") or 0)
            job_id = str(claim_data.get("job_id") or "").strip()
            run_id = str(claim_data.get("run_id") or "").strip()

            # Check if PID is alive
            if pid > 0:
                try:
                    os.kill(pid, 0)
                    # PID is alive; keep the claim
                    continue
                except (OSError, ProcessLookupError):
                    # PID is not alive; this is a stale claim
                    pass

            # Remove stale claim file
            try:
                claim_file.unlink()
                cleared.append(job_id)
                recovery_run_id = run_id or ""
                if not recovery_run_id and job_id:
                    try:
                        state = load_state(job_id, repo_root=root)
                    except Exception:
                        state = {}
                    recovery_run_id = str(state.get("active_run_id") or "").strip()

                # Transition the job from running→blocked if needed
                if job_id:
                    state = load_state(job_id, repo_root=root)
                    active_run_id = str(state.get("active_run_id") or "").strip()
                    if recovery_run_id:
                        now_utc = _utc_iso()
                        claim_uid = _scheduled_job_runtime_claim_uid(job_id, recovery_run_id)
                        claim_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=root)
                        if claim_state is not None and str(claim_state.get("status") or "") == "acquired":
                            detail = (
                                f"startup audit recovered stale scheduled-job runtime claim for {job_id} run {recovery_run_id}; "
                                f"claim_file={claim_file.name}; pid={pid}"
                            )
                            _record_scheduled_job_runtime_claim_release(
                                job_id=job_id,
                                run_id=recovery_run_id,
                                terminal_status="released",
                                recorded_at_utc=now_utc,
                                detail=detail,
                                repo_root=root,
                            )
                            _append_shared_resource_recovery_record(
                                claim_state=claim_state,
                                recorded_at_utc=now_utc,
                                action="released",
                                reason_code="stale_pid_dead",
                                detail=detail,
                                source="startup.resource_recovery",
                                evidence={
                                    "job_id": job_id,
                                    "run_id": recovery_run_id,
                                    "claim_file": str(claim_file),
                                    "pid": pid,
                                },
                                repo_root=root,
                            )
                    if active_run_id:
                        state.update(_blank_state_receipt_projection())
                        state["active_run_id"] = ""
                        state["active_started_utc"] = ""
                        state["active_loop_iteration"] = 0
                        state["active_loop_stage"] = ""
                        state["active_loop_completed_iterations"] = 0
                        state["active_loop_max_iterations"] = 0
                        state["active_loop_updated_utc"] = ""
                        state["last_run_id"] = active_run_id
                        state["last_finished_utc"] = now_utc
                        state["last_status"] = "blocked"
                        state["last_trigger"] = "startup"
                        state["last_receipt_path"] = ""
                        state["last_report_path"] = ""
                        state["run_now_requested_utc"] = ""
                        state["run_now_request_run_id"] = ""
                        state["run_now_request_source"] = ""
                        state["last_nonlaunch_reason"] = "startup_audit_stale_claim_cleared"
                        state["last_nonlaunch_status"] = "blocked"
                        state["last_nonlaunch_utc"] = now_utc
                        state["last_nonlaunch_run_id"] = run_id or ""
                        state["last_nonlaunch_receipt_path"] = ""
                        state["last_nonlaunch_report_path"] = ""
                        state["last_nonlaunch_signature"] = ""
                        state["updated_utc"] = now_utc
                        _write_state(job_id, state, repo_root=root)

                        # Emit audit event
                        _append_event(
                            repo_root=root,
                            job_id=job_id,
                            run_id=run_id or f"audit_{_utc_compact()}",
                            event="startup_audit_stale_claim_cleared",
                            trigger="startup",
                            status="blocked",
                            details={"pid": pid, "claim_file": str(claim_file)},
                        )
            except Exception as e:
                errors.append({
                    "claim_file": str(claim_file),
                    "job_id": job_id,
                    "error": str(e),
                })
        except Exception as e:
            errors.append({
                "claim_file": str(claim_file),
                "error": f"Failed to read/process claim: {e}",
            })

    return {
        "ok": len(errors) == 0,
        "cleared_count": len(cleared),
        "cleared_jobs": cleared,
        "errors": errors,
        "utc": _utc_iso(),
    }


def scheduler_service_status() -> Dict[str, Any]:
    with _SCHEDULER_GUARD:
        state = dict(_SCHEDULER_STATE)
        th = _SCHEDULER_THREAD
        state["running"] = bool(th is not None and th.is_alive())
        return state


def _scheduler_v2_tick(
    *,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    from infra.controlplane.sched import runtime as _sched_v2_runtime
    from infra.root_tools import load_tool_module as _load_root_tool_module

    _runtime_policy = _load_root_tool_module("runtime_policy", "tools/runtime_policy.py")

    root = Path(repo_root or _repo_root()).resolve()
    current_now = now or _utc_now()
    parent_uid = str(_runtime_policy.get_runtime_policy_text("scheduler.v2.parent_uid", "")).strip()
    if not parent_uid:
        return {
            "ok": False,
            "utc": _utc_iso(current_now),
            "backend": "v2",
            "submitted": [],
            "submitted_count": 0,
            "deferred": [],
            "deferred_count": 0,
            "skipped": [{"job_id": "", "reason": "v2_parent_uid_missing"}],
            "skipped_count": 1,
            "active_count": _get_active_job_count(),
            "executed": [],
            "executed_count": 0,
        }

    plan = _sched_v2_runtime.compile_scheduler_plan(
        parent_uid=parent_uid,
        run_at_utc=_utc_iso(current_now),
        repo_root=root,
    )
    runtime_policy_snapshot = current_runtime_resource_policy(repo_root=root)
    pool = _get_dispatch_pool(max_workers=int(runtime_policy_snapshot.get("global_concurrency_cap", 1) or 1))
    submitted: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    truth_ambiguous: List[Dict[str, Any]] = []
    current_active = _get_active_job_count()
    completed_uids: set[str] = set()
    active_child_uids: List[str] = []
    active_child_job_ids: List[str] = []
    ambiguous_child_uid_reasons: Dict[str, str] = {}

    state_by_job_id: Dict[str, Dict[str, Any]] = {}
    for job in plan.jobs:
        dispatch_job_id = str(getattr(job, "dispatch_job_id", "") or job.job_id)
        state = load_state(dispatch_job_id, repo_root=root)
        state_by_job_id[job.job_id] = state
        has_success = _is_success_status(state.get("last_status"))
        active_run_id = str(state.get("active_run_id") or "").strip()
        if has_success and active_run_id:
            ambiguous_child_uid_reasons[job.child_uid] = "terminal_success_and_active_run"
            truth_ambiguous.append(
                {
                    "job_id": job.job_id,
                    "dispatch_job_id": dispatch_job_id,
                    "child_uid": job.child_uid,
                    "reason": "terminal_success_and_active_run",
                }
            )
            continue
        if has_success:
            completed_uids.add(job.child_uid)
            continue
        if active_run_id:
            active_child_uids.append(job.child_uid)
            active_child_job_ids.append(dispatch_job_id)

    multiple_active_children = len(active_child_uids) > 1
    if multiple_active_children:
        for job_id, child_uid in zip(active_child_job_ids, active_child_uids):
            truth_ambiguous.append(
                {
                    "job_id": job.job_id,
                    "dispatch_job_id": job_id,
                    "child_uid": child_uid,
                    "reason": "multiple_active_children",
                }
            )

    if current_active > 0 and not active_child_job_ids:
        deferred.append({"job_id": "", "reason": "global_concurrency_cap_reached"})

    for job in plan.jobs:
        state = state_by_job_id.get(job.job_id, {})
        dispatch_job_id = str(getattr(job, "dispatch_job_id", "") or job.job_id)
        if job.child_uid in ambiguous_child_uid_reasons:
            skipped.append({"job_id": dispatch_job_id, "reason": f"truth_ambiguous:{ambiguous_child_uid_reasons[job.child_uid]}"})
            continue
        if multiple_active_children and job.child_uid in set(active_child_uids):
            skipped.append({"job_id": dispatch_job_id, "reason": "truth_ambiguous:multiple_active_children"})
            continue
        if _is_success_status(state.get("last_status")):
            skipped.append({"job_id": dispatch_job_id, "reason": "terminal_success"})
            continue
        if str(state.get("active_run_id") or "").strip():
            skipped.append({"job_id": dispatch_job_id, "reason": "active_run"})
            continue
        if _dispatch_future_active(dispatch_job_id):
            skipped.append({"job_id": dispatch_job_id, "reason": "already_dispatched"})
            continue
        ambiguous_deps = [uid for uid in job.depends_on_uids if uid in ambiguous_child_uid_reasons]
        if ambiguous_deps:
            skipped.append({"job_id": dispatch_job_id, "reason": f"dependency_truth_ambiguous:{ambiguous_deps[0]}"})
            continue
        if multiple_active_children:
            skipped.append({"job_id": dispatch_job_id, "reason": "dependency_truth_ambiguous:multiple_active_children"})
            continue
        unmet = [uid for uid in job.depends_on_uids if uid not in completed_uids]
        if unmet:
            skipped.append({"job_id": dispatch_job_id, "reason": f"dependency_pending:{unmet[0]}"})
            continue
        if active_child_uids:
            skipped.append({"job_id": dispatch_job_id, "reason": f"dependency_pending:{active_child_uids[0]}"})
            continue
        if current_active > 0:
            skipped.append({"job_id": dispatch_job_id, "reason": "global_concurrency_cap_reached"})
            continue

        spec = load_job(dispatch_job_id, repo_root=root)
        if not bool(spec.get("enabled", True)):
            skipped.append({"job_id": dispatch_job_id, "reason": "disabled"})
            continue

        claim_path = (locks_dir(root) / f"{_safe_name(dispatch_job_id)}.lock.json").resolve()
        if claim_path.exists():
            terminal_claim_reconciliation = _reconcile_terminal_claim_before_launch(dispatch_job_id, repo_root=root)
            if not (bool(terminal_claim_reconciliation.get("ok")) and not claim_path.exists()):
                claim_path = (locks_dir(root) / f"{_safe_name(dispatch_job_id)}.lock.json").resolve()
            policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
            if claim_path.exists() and _claim_is_stale(
                claim_path,
                max_runtime_seconds=int(policy.get("max_runtime_seconds") or 1800),
                now=current_now,
            ):
                try:
                    stale_claim = _read_json(claim_path)
                    claim_path.unlink()
                    _append_event(
                        repo_root=root,
                        job_id=dispatch_job_id,
                        run_id=str((stale_claim or {}).get("run_id") or ""),
                        event="stale_claim_cleaned",
                        trigger="scheduler_v2",
                        status="blocked",
                        details={"reason": "scheduler_v2_tick_unlinked_stale_claim", "claim_path": str(claim_path)},
                    )
                except FileNotFoundError:
                    pass
                except Exception:
                    skipped.append({"job_id": dispatch_job_id, "reason": "stale_claim_cleanup_failed"})
                    continue
            elif claim_path.exists():
                skipped.append({"job_id": dispatch_job_id, "reason": "claimed_elsewhere"})
                continue

        try:
            future = pool.submit(
                execute_job,
                dispatch_job_id,
                repo_root=root,
                trigger="scheduler",
                force=True,
                now_utc=_utc_iso(current_now),
            )
            with _DISPATCH_GUARD:
                _DISPATCH_ACTIVE_JOBS[dispatch_job_id] = future
                _DISPATCH_ACTIVE_JOB_ROLES[dispatch_job_id] = str(spec.get("role") or "generalist").strip().lower() or "generalist"
                _DISPATCH_ACTIVE_JOB_META[dispatch_job_id] = {
                    "submitted_utc": _utc_iso(current_now),
                    "repo_root": str(root),
                }
            submitted.append({"job_id": dispatch_job_id, "projection_job_id": job.job_id, "child_uid": job.child_uid})
            current_active += 1
            break
        except Exception as exc:
            skipped.append({"job_id": dispatch_job_id, "reason": f"submit_failed:{exc}"})

    active_count = _get_active_job_count()
    return {
        "ok": True,
        "utc": _utc_iso(current_now),
        "backend": "v2",
        "parent_uid": parent_uid,
        "submitted": submitted,
        "submitted_count": len(submitted),
        "deferred": deferred,
        "deferred_count": len(deferred),
        "skipped": skipped,
        "skipped_count": len(skipped),
        "truth_ambiguous": truth_ambiguous,
        "truth_ambiguous_count": len(truth_ambiguous),
        "active_count": active_count,
        "executed": [],
        "executed_count": 0,
    }


def _scheduler_service_tick(
    *,
    repo_root: Optional[Path] = None,
    limit: int = 10,
) -> Dict[str, Any]:
    from infra.controlplane.sched import operator_backend as _sched_v2_backend

    backend = _sched_v2_backend.selected_backend()
    current_now = _utc_now()
    if backend == "v2":
        try:
            result = _scheduler_v2_tick(repo_root=repo_root, now=current_now, limit=limit)
            if bool(result.get("ok")):
                return result
        except Exception:
            pass
    return scheduler_tick(repo_root=repo_root, limit=limit, await_completion=False)


def _scheduler_owned_reconciliation_pass(*, repo_root: Optional[Path] = None, startup: bool = False) -> Dict[str, Any]:
    """Run the scheduler-owned stale-runtime cleanup pass.

    This intentionally folds the useful runtime repair work into scheduler-owned
    startup/tick behavior instead of relying on a separate healer authority.
    """
    out: Dict[str, Any] = {
        "startup": bool(startup),
        "claim_audit": {},
        "stranded_running_reconciliation": {},
        "stale_running_transition": {},
        "ok": True,
    }
    try:
        if startup:
            out["claim_audit"] = startup_claim_audit(repo_root=repo_root)
    except Exception as exc:
        out["claim_audit"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
    try:
        out["stranded_running_reconciliation"] = _reconcile_stranded_running_without_live_claim(repo_root=repo_root)
        if not bool(out["stranded_running_reconciliation"].get("ok", True)):
            out["ok"] = False
    except Exception as exc:
        out["stranded_running_reconciliation"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
    try:
        out["stale_running_transition"] = _transition_stale_running_to_blocked(repo_root=repo_root)
        if not bool(out["stale_running_transition"].get("ok", True)):
            out["ok"] = False
    except Exception as exc:
        out["stale_running_transition"] = {"ok": False, "error": str(exc)}
        out["ok"] = False
    return out


def start_scheduler_service(*, interval_s: int = DEFAULT_SCHEDULER_INTERVAL_S, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    interval = max(5, int(interval_s))
    root_txt = str(Path(repo_root).resolve()) if repo_root is not None else str(_repo_root())
    prior_thread: Optional[threading.Thread] = None
    prior_stop_evt: Optional[threading.Event] = None
    with _SCHEDULER_GUARD:
        global _SCHEDULER_THREAD, _SCHEDULER_STOP, _SCHEDULER_STATE
        if _SCHEDULER_THREAD is not None and _SCHEDULER_THREAD.is_alive():
            same_interval = int(_SCHEDULER_STATE.get("interval_s") or 0) == interval
            same_root = str(_SCHEDULER_STATE.get("repo_root") or "") == root_txt
            if same_interval and same_root:
                _SCHEDULER_STATE["running"] = True
                return dict(_SCHEDULER_STATE)
            prior_thread = _SCHEDULER_THREAD
            prior_stop_evt = _SCHEDULER_STOP
            _SCHEDULER_THREAD = None
            _SCHEDULER_STOP = None
            _SCHEDULER_STATE["running"] = False
        # Clear any dead thread reference so the restart below is clean
        if prior_thread is None:
            _SCHEDULER_THREAD = None
            _SCHEDULER_STOP = None

    if prior_stop_evt is not None:
        prior_stop_evt.set()
    if prior_thread is not None:
        prior_thread.join(timeout=2.0)

    with _SCHEDULER_GUARD:
        startup_reconciliation = _scheduler_owned_reconciliation_pass(repo_root=repo_root, startup=True)

        stop_evt = threading.Event()
        _SCHEDULER_STOP = stop_evt
        _SCHEDULER_STATE = {
            "running": True,
            "interval_s": interval,
            "started_utc": _utc_iso(),
            "last_tick_utc": "",
            "last_result": {},
            "last_error": "",
            "repo_root": root_txt,
            "startup_audit": dict(startup_reconciliation.get("claim_audit") or {}),
            "startup_reconciliation": startup_reconciliation,
        }

        def _runner() -> None:
            while not stop_evt.is_set():
                try:
                    reconciliation = _scheduler_owned_reconciliation_pass(repo_root=repo_root, startup=False)
                    result = _scheduler_service_tick(repo_root=repo_root, limit=10)
                    with _SCHEDULER_GUARD:
                        _SCHEDULER_STATE["last_tick_utc"] = _utc_iso()
                        _SCHEDULER_STATE["last_result"] = dict(result or {})
                        _SCHEDULER_STATE["last_reconciliation"] = reconciliation
                        _SCHEDULER_STATE["last_error"] = ""
                    try:
                        ensure_watchdog_service_liveness(repo_root=Path(repo_root or _repo_root()).resolve())
                    except Exception:
                        pass
                except Exception as e:
                    with _SCHEDULER_GUARD:
                        _SCHEDULER_STATE["last_tick_utc"] = _utc_iso()
                        _SCHEDULER_STATE["last_result"] = {}
                        _SCHEDULER_STATE["last_error"] = str(e)
                stop_evt.wait(interval)
            with _SCHEDULER_GUARD:
                _SCHEDULER_STATE["running"] = False

        # Start the watchdog before the scheduler thread so it can observe the
        # first tick. Scheduler-owned reconciliation now replaces healer-owned
        # runtime repair in the hot path.
        try:
            start_watchdog_service(interval_s=900, repo_root=repo_root)
        except Exception as e:
            _SCHEDULER_STATE["watchdog_startup_error"] = str(e)

        _SCHEDULER_THREAD = threading.Thread(
            target=_runner,
            name="scheduled_jobs_service",
            daemon=True,
        )
        _SCHEDULER_THREAD.start()

        return dict(_SCHEDULER_STATE)


def stop_scheduler_service(*, timeout_s: float = 2.0) -> Dict[str, Any]:
    # Also stop the watchdog and healer
    try:
        stop_watchdog_service(timeout_s=timeout_s)
    except Exception:
        pass

    try:
        stop_healer_service(timeout_s=timeout_s)
    except Exception:
        pass

    with _SCHEDULER_GUARD:
        global _SCHEDULER_THREAD, _SCHEDULER_STOP
        th = _SCHEDULER_THREAD
        stop_evt = _SCHEDULER_STOP
    if stop_evt is not None:
        stop_evt.set()
    if th is not None:
        th.join(timeout=max(0.1, float(timeout_s)))
    # Shutdown the dispatch thread pool so worker threads are released cleanly.
    global _DISPATCH_POOL
    with _DISPATCH_GUARD:
        pool = _DISPATCH_POOL
        _DISPATCH_POOL = None
    if pool is not None:
        try:
            pool.shutdown(wait=False)
        except Exception:
            pass
    with _SCHEDULER_GUARD:
        _SCHEDULER_THREAD = None
        _SCHEDULER_STOP = None
        _SCHEDULER_STATE["running"] = False
        return dict(_SCHEDULER_STATE)


def _classify_scheduler_health(*, attempt_restart: bool = False) -> Dict[str, Any]:
    """Classify scheduler health based on thread status, last tick age, and claims.

    If attempt_restart is True and the scheduler thread is down while the server
    is alive, attempt to restart it.
    """
    with _SCHEDULER_GUARD:
        state = dict(_SCHEDULER_STATE)
        th = _SCHEDULER_THREAD

    sched_running = state.get("running", False)
    last_tick_utc = str(state.get("last_tick_utc") or "").strip()
    started_utc = str(state.get("started_utc") or "").strip()
    interval_s = int(state.get("interval_s") or 30)
    repo_root = state.get("repo_root")
    root_value = Path(repo_root).resolve() if str(repo_root or "").strip() else _repo_root()
    now = _utc_now()
    active_claim_job_ids = _active_job_claims(repo_root=root_value)
    active_claim_count = len(active_claim_job_ids)
    active_progress = _durable_active_job_progress(repo_root=root_value, active_job_ids=active_claim_job_ids, now=now)
    last_tick_age_s = None
    start_age_s = None

    if last_tick_utc:
        try:
            last_tick_dt = _parse_utc(last_tick_utc)
            last_tick_age_s = (now - last_tick_dt).total_seconds()
        except Exception:
            pass
    if started_utc:
        try:
            started_dt = _parse_utc(started_utc)
            start_age_s = (now - started_dt).total_seconds()
        except Exception:
            pass

    # Health classification thresholds:
    # healthy: thread alive, last tick within 2×interval_s, no issues
    # degraded: last tick within 5×interval_s, or stale claims present with PID alive
    # stalled: last tick older than 5×interval_s, or no tick recorded, or stuck pipeline
    # down: thread not alive or server unreachable

    # Check if thread is actually alive
    thread_alive = th is not None and th.is_alive()

    if not thread_alive:
        if active_claim_count > 0:
            return {
                "health": "degraded",
                "reason": "scheduler_thread_down_active_claims_present",
                "active_claim_count": active_claim_count,
                "active_claim_job_ids": active_claim_job_ids,
            }
        # Scheduler thread is down; attempt restart if requested
        if attempt_restart:
            try:
                start_scheduler_service(
                    interval_s=interval_s,
                    repo_root=Path(repo_root) if repo_root else None,
                )
                return {
                    "health": "recovering",
                    "reason": "scheduler_thread_restarted",
                    "restarted": True,
                }
            except Exception as e:
                return {
                    "health": "down",
                    "reason": "scheduler_thread_restart_failed",
                    "error": str(e),
                }
        return {
            "health": "down",
            "reason": "scheduler_thread_not_alive",
        }

    if last_tick_age_s is None:
        if active_claim_count > 0:
            if bool(active_progress.get("fresh")):
                return {
                    "health": "healthy",
                    "reason": "durable_active_progress_without_tick",
                    "active_claim_count": active_claim_count,
                    "active_claim_job_ids": active_claim_job_ids,
                    "active_progress_utc": str(active_progress.get("latest_progress_utc") or ""),
                }
            return {
                "health": "degraded",
                "reason": "active_job_progress_detected_without_tick",
                "active_claim_count": active_claim_count,
                "active_claim_job_ids": active_claim_job_ids,
            }
        startup_grace_s = 2 * max(5, interval_s)
        if start_age_s is not None and start_age_s <= startup_grace_s:
            return {"health": "degraded", "reason": "starting_without_progress"}
        return {"health": "stalled", "reason": "no_tick_recorded"}

    tick_freshness_threshold_good = 2 * max(5, interval_s)
    tick_freshness_threshold_degraded = 5 * max(5, interval_s)

    if last_tick_age_s > tick_freshness_threshold_degraded:
        if active_claim_count > 0:
            return {
                "health": "degraded",
                "reason": "active_job_progress_detected",
                "active_claim_count": active_claim_count,
                "active_claim_job_ids": active_claim_job_ids,
            }
        return {"health": "stalled", "reason": "last_tick_too_old"}

    if last_tick_age_s > tick_freshness_threshold_good:
        return {
            "health": "degraded",
            "reason": "last_tick_aging",
            "active_claim_count": active_claim_count,
            "active_claim_job_ids": active_claim_job_ids,
        }

    return {
        "health": "healthy",
        "reason": "normal",
        "active_claim_count": active_claim_count,
        "active_claim_job_ids": active_claim_job_ids,
    }


def _durable_active_job_progress(
    *,
    repo_root: Optional[Path] = None,
    active_job_ids: Optional[List[str]] = None,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    ids = [str(item or "").strip() for item in list(active_job_ids or []) if str(item or "").strip()]
    if not ids:
        return {"fresh": False, "latest_progress_utc": "", "active_job_ids": []}
    current_now = now or _utc_now()
    freshest_dt: Optional[_dt.datetime] = None
    freshest_utc = ""
    fresh_ids: List[str] = []
    freshness_window_s = max(180, 4 * max(5, int(DEFAULT_SCHEDULER_INTERVAL_S)))
    for job_id in ids:
        try:
            state = load_state(job_id, repo_root=root)
        except Exception:
            continue
        progress_utc = str(
            state.get("active_loop_updated_utc")
            or state.get("updated_utc")
            or state.get("active_started_utc")
            or ""
        ).strip()
        if not progress_utc:
            continue
        try:
            progress_dt = _parse_utc(progress_utc)
        except Exception:
            continue
        age_s = (current_now - progress_dt).total_seconds()
        if age_s < 0:
            age_s = 0
        if freshest_dt is None or progress_dt > freshest_dt:
            freshest_dt = progress_dt
            freshest_utc = progress_utc
        if age_s <= freshness_window_s:
            fresh_ids.append(job_id)
    return {
        "fresh": bool(fresh_ids),
        "latest_progress_utc": freshest_utc,
        "active_job_ids": fresh_ids,
    }


def _classify_service_iteration_health(
    *,
    thread: Any,
    running: bool,
    interval_s: int,
    started_utc: str = "",
    last_progress_utc: str = "",
) -> Dict[str, Any]:
    now = _utc_now()
    thread_alive = thread is not None and thread.is_alive()
    if not thread_alive:
        return {"health": "down", "reason": "service_thread_not_alive"}
    if not running:
        return {"health": "down", "reason": "service_not_marked_running"}

    if last_progress_utc:
        try:
            last_dt = _parse_utc(last_progress_utc)
            last_age_s = (now - last_dt).total_seconds()
        except Exception:
            last_age_s = None
    else:
        last_age_s = None

    if last_age_s is None and started_utc:
        try:
            start_dt = _parse_utc(started_utc)
            start_age_s = (now - start_dt).total_seconds()
        except Exception:
            start_age_s = None
    else:
        start_age_s = None

    good_threshold_s = 2 * max(30, int(interval_s or 0))
    stale_threshold_s = 5 * max(30, int(interval_s or 0))

    if last_age_s is None:
        if start_age_s is None:
            return {"health": "stalled", "reason": "no_progress_recorded"}
        if start_age_s > stale_threshold_s:
            return {"health": "stalled", "reason": "no_progress_recorded"}
        return {"health": "degraded", "reason": "starting_without_progress"}

    if last_age_s > stale_threshold_s:
        return {"health": "stalled", "reason": "last_progress_too_old"}
    if last_age_s > good_threshold_s:
        return {"health": "degraded", "reason": "last_progress_aging"}
    return {"health": "healthy", "reason": "normal"}


def ensure_scheduler_service_liveness(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Best-effort scheduler self-heal for dead or stalled in-process service threads."""

    if str(os.environ.get("EIEL_SCHEDULED_JOBS_AUTOSTART", "1") or "1").strip().lower() in {
        "0",
        "false",
        "no",
        "off",
    }:
        return {
            "health": "disabled",
            "reason": "scheduled_jobs_autostart_disabled",
            "restart_attempted": False,
            "active_dispatch_count": _get_active_job_count(),
        }

    health = _classify_scheduler_health(attempt_restart=False)
    status = scheduler_service_status()
    interval_s = int(status.get("interval_s") or DEFAULT_SCHEDULER_INTERVAL_S)
    root_value = repo_root
    if root_value is None:
        repo_txt = str(status.get("repo_root") or "").strip()
        root_value = Path(repo_txt).resolve() if repo_txt else None

    if str(health.get("health") or "") == "healthy":
        return {
            **health,
            "restart_attempted": False,
            "active_dispatch_count": _get_active_job_count(),
        }
    if str(health.get("health") or "") not in {"stalled", "down"}:
        return {
            **health,
            "restart_attempted": False,
            "active_dispatch_count": _get_active_job_count(),
        }

    active_dispatch_count = _get_active_job_count()
    if active_dispatch_count > 0:
        return {
            **health,
            "restart_attempted": False,
            "restart_skipped_reason": "active_dispatch_present",
            "active_dispatch_count": active_dispatch_count,
        }
    active_claim_count = int(health.get("active_claim_count") or 0)
    if active_claim_count > 0:
        return {
            **health,
            "restart_attempted": False,
            "restart_skipped_reason": "active_claim_present",
            "active_dispatch_count": active_dispatch_count,
        }

    try:
        stop_scheduler_service(timeout_s=0.5)
    except Exception as exc:
        return {
            **health,
            "restart_attempted": True,
            "restart_ok": False,
            "restart_error": f"stop_failed:{type(exc).__name__}:{exc}",
            "active_dispatch_count": active_dispatch_count,
        }

    try:
        restarted = start_scheduler_service(interval_s=interval_s, repo_root=root_value)
    except Exception as exc:
        return {
            **health,
            "restart_attempted": True,
            "restart_ok": False,
            "restart_error": f"start_failed:{type(exc).__name__}:{exc}",
            "active_dispatch_count": active_dispatch_count,
        }

    refreshed = _classify_scheduler_health(attempt_restart=False)
    return {
        **refreshed,
        "restart_attempted": True,
        "restart_ok": True,
        "active_dispatch_count": active_dispatch_count,
        "service": restarted,
    }


def ensure_watchdog_service_liveness(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    with _WATCHDOG_GUARD:
        state = dict(_WATCHDOG_STATE)
        th = _WATCHDOG_THREAD
    health = _classify_service_iteration_health(
        thread=th,
        running=bool(state.get("running")),
        interval_s=int(state.get("interval_s") or 900),
        started_utc=str(state.get("started_utc") or ""),
        last_progress_utc=str(state.get("last_receipt_utc") or ""),
    )
    interval_s = int(state.get("interval_s") or 900)
    root_value = repo_root
    if root_value is None:
        repo_txt = str(state.get("repo_root") or "").strip()
        root_value = Path(repo_txt).resolve() if repo_txt else None
    if str(health.get("health") or "") == "healthy":
        return {**health, "restart_attempted": False}
    if str(health.get("health") or "") not in {"stalled", "down"}:
        return {**health, "restart_attempted": False}
    try:
        stop_watchdog_service(timeout_s=0.5)
    except Exception as exc:
        return {
            **health,
            "restart_attempted": True,
            "restart_ok": False,
            "restart_error": f"stop_failed:{type(exc).__name__}:{exc}",
        }
    try:
        restarted = start_watchdog_service(interval_s=interval_s, repo_root=root_value)
    except Exception as exc:
        return {
            **health,
            "restart_attempted": True,
            "restart_ok": False,
            "restart_error": f"start_failed:{type(exc).__name__}:{exc}",
        }
    with _WATCHDOG_GUARD:
        refreshed_state = dict(_WATCHDOG_STATE)
        refreshed_thread = _WATCHDOG_THREAD
    refreshed = _classify_service_iteration_health(
        thread=refreshed_thread,
        running=bool(refreshed_state.get("running")),
        interval_s=int(refreshed_state.get("interval_s") or interval_s),
        started_utc=str(refreshed_state.get("started_utc") or ""),
        last_progress_utc=str(refreshed_state.get("last_receipt_utc") or ""),
    )
    return {
        **refreshed,
        "restart_attempted": True,
        "restart_ok": True,
        "service": restarted,
    }


def ensure_healer_service_liveness(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    with _HEALER_GUARD:
        state = dict(_HEALER_STATE)
        th = _HEALER_THREAD
    if not bool(state.get("running")) and th is None and not str(state.get("started_utc") or "").strip():
        return {
            "health": "disabled",
            "reason": "scheduler_owned_reconciliation",
            "restart_attempted": False,
        }
    health = _classify_service_iteration_health(
        thread=th,
        running=bool(state.get("running")),
        interval_s=int(state.get("interval_s") or 300),
        started_utc=str(state.get("started_utc") or ""),
        last_progress_utc=str(state.get("last_receipt_utc") or ""),
    )
    interval_s = int(state.get("interval_s") or 300)
    root_value = repo_root
    if root_value is None:
        repo_txt = str(state.get("repo_root") or "").strip()
        root_value = Path(repo_txt).resolve() if repo_txt else None
    if str(health.get("health") or "") == "healthy":
        return {**health, "restart_attempted": False}
    if str(health.get("health") or "") not in {"stalled", "down"}:
        return {**health, "restart_attempted": False}
    try:
        stop_healer_service(timeout_s=0.5)
    except Exception as exc:
        return {
            **health,
            "restart_attempted": True,
            "restart_ok": False,
            "restart_error": f"stop_failed:{type(exc).__name__}:{exc}",
        }
    try:
        restarted = start_healer_service(interval_s=interval_s, repo_root=root_value)
    except Exception as exc:
        return {
            **health,
            "restart_attempted": True,
            "restart_ok": False,
            "restart_error": f"start_failed:{type(exc).__name__}:{exc}",
        }
    with _HEALER_GUARD:
        refreshed_state = dict(_HEALER_STATE)
        refreshed_thread = _HEALER_THREAD
    refreshed = _classify_service_iteration_health(
        thread=refreshed_thread,
        running=bool(refreshed_state.get("running")),
        interval_s=int(refreshed_state.get("interval_s") or interval_s),
        started_utc=str(refreshed_state.get("started_utc") or ""),
        last_progress_utc=str(refreshed_state.get("last_receipt_utc") or ""),
    )
    return {
        **refreshed,
        "restart_attempted": True,
        "restart_ok": True,
        "service": restarted,
    }


def watchdog_service_status() -> Dict[str, Any]:
    with _WATCHDOG_GUARD:
        state = dict(_WATCHDOG_STATE)
        th = _WATCHDOG_THREAD
        state["running"] = bool(th is not None and th.is_alive())
        return state


def start_watchdog_service(*, interval_s: int = 900, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Start the server-resident watchdog daemon thread."""
    interval = max(30, int(interval_s))
    root_txt = str(Path(repo_root).resolve()) if repo_root is not None else str(_repo_root())
    prior_thread: Optional[threading.Thread] = None
    prior_stop_evt: Optional[threading.Event] = None

    with _WATCHDOG_GUARD:
        global _WATCHDOG_THREAD, _WATCHDOG_STOP, _WATCHDOG_STATE
        if _WATCHDOG_THREAD is not None and _WATCHDOG_THREAD.is_alive():
            same_interval = int(_WATCHDOG_STATE.get("interval_s") or 0) == interval
            same_root = str(_WATCHDOG_STATE.get("repo_root") or "") == root_txt
            if same_interval and same_root:
                _WATCHDOG_STATE["running"] = True
                return dict(_WATCHDOG_STATE)
            prior_thread = _WATCHDOG_THREAD
            prior_stop_evt = _WATCHDOG_STOP
            _WATCHDOG_THREAD = None
            _WATCHDOG_STOP = None
            _WATCHDOG_STATE["running"] = False

    if prior_stop_evt is not None:
        prior_stop_evt.set()
    if prior_thread is not None:
        prior_thread.join(timeout=2.0)

    with _WATCHDOG_GUARD:
        stop_evt = threading.Event()
        _WATCHDOG_STOP = stop_evt
        _WATCHDOG_STATE = {
            "running": True,
            "interval_s": interval,
            "started_utc": _utc_iso(),
            "last_receipt_utc": "",
            "last_receipt": {},
            "last_error": "",
            "repo_root": root_txt,
        }

        def _runner() -> None:
            while not stop_evt.is_set():
                try:
                    from tools.bin import scheduler_watchdog as _scheduler_watchdog

                    receipt = _scheduler_watchdog.run_watchdog(
                        mode="selfheal",
                        repo_root=Path(repo_root or _repo_root()).resolve(),
                    )
                    with _WATCHDOG_GUARD:
                        _WATCHDOG_STATE["last_receipt_utc"] = _utc_iso()
                        _WATCHDOG_STATE["last_receipt"] = receipt
                        _WATCHDOG_STATE["last_error"] = ""
                        _WATCHDOG_STATE["last_transition_errors"] = []
                    try:
                        ensure_scheduler_service_liveness(repo_root=Path(repo_root or _repo_root()).resolve())
                    except Exception:
                        pass
                    try:
                        ensure_healer_service_liveness(repo_root=Path(repo_root or _repo_root()).resolve())
                    except Exception:
                        pass
                except Exception as e:
                    with _WATCHDOG_GUARD:
                        _WATCHDOG_STATE["last_receipt_utc"] = _utc_iso()
                        _WATCHDOG_STATE["last_error"] = str(e)
                stop_evt.wait(interval)

            with _WATCHDOG_GUARD:
                _WATCHDOG_STATE["running"] = False

        _WATCHDOG_THREAD = threading.Thread(
            target=_runner,
            name="scheduled_watchdog_service",
            daemon=True,
        )
        _WATCHDOG_THREAD.start()
        return dict(_WATCHDOG_STATE)


def stop_watchdog_service(*, timeout_s: float = 2.0) -> Dict[str, Any]:
    """Stop the watchdog daemon thread."""
    with _WATCHDOG_GUARD:
        global _WATCHDOG_THREAD, _WATCHDOG_STOP
        th = _WATCHDOG_THREAD
        stop_evt = _WATCHDOG_STOP
    if stop_evt is not None:
        stop_evt.set()
    if th is not None:
        th.join(timeout=max(0.1, float(timeout_s)))
    with _WATCHDOG_GUARD:
        _WATCHDOG_THREAD = None
        _WATCHDOG_STOP = None
        _WATCHDOG_STATE["running"] = False
        return dict(_WATCHDOG_STATE)


def healer_service_status() -> Dict[str, Any]:
    with _HEALER_GUARD:
        state = dict(_HEALER_STATE)
        th = _HEALER_THREAD
        state["running"] = bool(th is not None and th.is_alive())
        return state


def start_healer_service(*, interval_s: int = 300, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    """Start the server-resident healer daemon thread.

    The healer detects and repairs deadlocked pipelines, stale states, and
    blocked chains. It runs periodically (default: 5 minutes) and applies
    repairs within configured policy bounds.
    """
    interval = max(60, int(interval_s))
    root_txt = str(Path(repo_root).resolve()) if repo_root is not None else str(_repo_root())

    with _HEALER_GUARD:
        global _HEALER_THREAD, _HEALER_STOP, _HEALER_STATE
        if _HEALER_THREAD is not None and _HEALER_THREAD.is_alive():
            _HEALER_STATE["running"] = True
            return dict(_HEALER_STATE)

        stop_evt = threading.Event()
        _HEALER_STOP = stop_evt
        _HEALER_STATE = {
            "running": True,
            "interval_s": interval,
            "started_utc": _utc_iso(),
            "last_receipt_utc": "",
            "last_receipt": {},
            "last_error": "",
            "repo_root": root_txt,
        }

        def _runner() -> None:
            while not stop_evt.is_set():
                try:
                    # Try to import and run the external healer logic
                    try:
                        from tools.bin import scheduler_pipeline_healer as healer_module
                        detection = healer_module.detect_findings(
                            repo_root=Path(root_txt),
                            repeat_failure_threshold=3,
                            idle_minutes=60,
                        )
                        receipt = {
                            "schema_version": "scheduled_healer_receipt_v1",
                            "utc": _utc_iso(),
                            "findings_count": len(detection.get("findings", [])),
                            "sample_findings": detection.get("findings", [])[:3],
                        }
                    except Exception as _healer_exc:
                        # Healer logic unavailable; produce empty receipt and record error
                        receipt = {
                            "schema_version": "scheduled_healer_receipt_v1",
                            "utc": _utc_iso(),
                            "findings_count": 0,
                            "error": str(_healer_exc),
                        }
                        with _HEALER_GUARD:
                            _HEALER_STATE["last_error"] = str(_healer_exc)

                    with _HEALER_GUARD:
                        _HEALER_STATE["last_receipt_utc"] = _utc_iso()
                        _HEALER_STATE["last_receipt"] = receipt
                        _HEALER_STATE["last_error"] = ""
                    try:
                        ensure_scheduler_service_liveness(repo_root=Path(root_txt))
                    except Exception:
                        pass
                    try:
                        ensure_watchdog_service_liveness(repo_root=Path(root_txt))
                    except Exception:
                        pass
                except Exception as e:
                    with _HEALER_GUARD:
                        _HEALER_STATE["last_receipt_utc"] = _utc_iso()
                        _HEALER_STATE["last_error"] = str(e)
                stop_evt.wait(interval)

            with _HEALER_GUARD:
                _HEALER_STATE["running"] = False

        _HEALER_THREAD = threading.Thread(
            target=_runner,
            name="scheduled_healer_service",
            daemon=True,
        )
        _HEALER_THREAD.start()
        return dict(_HEALER_STATE)


def stop_healer_service(*, timeout_s: float = 2.0) -> Dict[str, Any]:
    """Stop the healer daemon thread."""
    with _HEALER_GUARD:
        global _HEALER_THREAD, _HEALER_STOP
        th = _HEALER_THREAD
        stop_evt = _HEALER_STOP
    if stop_evt is not None:
        stop_evt.set()
    if th is not None:
        th.join(timeout=max(0.1, float(timeout_s)))
    with _HEALER_GUARD:
        _HEALER_THREAD = None
        _HEALER_STOP = None
        _HEALER_STATE["running"] = False
        return dict(_HEALER_STATE)
