def execute_job(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    trigger: str = "manual",
    force: bool = False,
    now_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute a scheduled job using a spec snapshot.

    IMPORTANT: The spec and state are loaded once at the start of execution.
    This snapshot is used for the entire job lifetime. Spec mutations on disk
    during execution do not affect the in-flight job. This ensures safety when
    operators modify specs while jobs are running.
    """
    try:
        spec = load_job(job_id, repo_root=repo_root)
    except FileNotFoundError:
        # Job spec was deleted or is missing; treat as skipped
        return {
            "ok": False,
            "job_id": job_id,
            "skipped": True,
            "reason": "job_spec_missing",
        }
    except Exception as e:
        # Other errors loading spec
        return {
            "ok": False,
            "job_id": job_id,
            "skipped": True,
            "reason": f"job_spec_load_error:{str(e)}",
        }

    job_kind = _normalize_job_kind(spec.get("job_kind"))
    now = _parse_utc(now_utc) if now_utc else _utc_now()
    run_id = f"sj_{_utc_compact(now)}_{uuid.uuid4().hex[:6]}"
    max_runtime_seconds = int(((spec.get("policy") or {}).get("max_runtime_seconds") or 1800))
    claimed, _claim = _acquire_job_claim(
        job_id,
        repo_root=repo_root,
        run_id=run_id,
        trigger=trigger,
        max_runtime_seconds=max_runtime_seconds,
        spec=spec,
        force=force,
        now=now,
    )
    if not claimed:
        return {"ok": False, "job_id": job_id, "skipped": True, "reason": "claimed_elsewhere"}
    heartbeat = _ClaimHeartbeat(
        job_id=job_id,
        repo_root=repo_root,
        run_id=run_id,
        interval_s=max(5, max_runtime_seconds // 4),
    )
    scheduled_job_runtime_claim_handled = False
    claim_terminal_status = ""
    claim_release_detail = ""
    try:
        state = load_state(job_id, repo_root=repo_root)
        runtime_policy_snapshot = current_runtime_resource_policy(repo_root=repo_root)
        global_cap = int(runtime_policy_snapshot.get("global_concurrency_cap", 1))
        if not force and global_cap > 0:
            other_active_jobs = _active_job_claims(repo_root=repo_root, exclude_job_id=job_id)
            if len(other_active_jobs) >= global_cap:
                concurrency_guard = {
                    "kind": "global_concurrency_cap",
                    "global_concurrency_cap": global_cap,
                    "active_job_count": len(other_active_jobs),
                    "active_jobs": [
                        {
                            "job_id": str(item.get("job_id") or "").strip(),
                            "run_id": str(item.get("run_id") or "").strip(),
                            "started_utc": str(item.get("started_utc") or "").strip(),
                            "heartbeat_utc": str(item.get("heartbeat_utc") or "").strip(),
                            "role": str(item.get("role") or "").strip(),
                        }
                        for item in other_active_jobs
                        if isinstance(item, dict)
                    ],
                }
                due = due_status(spec, state=state, now=now, repo_root=repo_root)
                decision = _record_nonlaunch_decision(
                    spec=spec,
                    state=state,
                    due=due,
                    run_id=run_id,
                    trigger=trigger,
                    reason="global_concurrency_cap_reached",
                    status="deferred",
                    event="deferred",
                    repo_root=repo_root,
                    now=now,
                    execution_guard=concurrency_guard,
                    runtime_policy_snapshot=runtime_policy_snapshot,
                )
                return {
                    "ok": False,
                    "job_id": job_id,
                    "skipped": True,
                    "reason": "global_concurrency_cap_reached",
                    "deferred": True,
                    "active_jobs": other_active_jobs,
                    "run_id": str(decision.get("run_id") or run_id),
                    "receipt_path": str(decision.get("receipt_path") or ""),
                    "report_path": str(decision.get("report_path") or ""),
                }
        active_recovery = _active_run_recovery_status(spec, state=state, now=now, repo_root=repo_root)
        if bool(active_recovery.get("recover")):
            state = _apply_active_run_recovery(
                job_id=job_id,
                state=state,
                recovery=active_recovery,
                trigger=trigger,
                repo_root=repo_root,
                now=now,
            )
        due = due_status(spec, state=state, now=now, repo_root=repo_root)
        if trigger != "manual" and not force and not bool(due.get("eligible")):
            return {"ok": False, "job_id": job_id, "skipped": True, "reason": str(due.get("reason") or "not_due")}
        active_gate = _active_run_recovery_status(spec, state=state, now=now, repo_root=repo_root)
        if bool(active_gate.get("blocking")) and not force:
            return {"ok": False, "job_id": job_id, "skipped": True, "reason": "active_run"}
        backpressure = _scheduler_backpressure_reason(spec, trigger=trigger, force=force)
        if backpressure:
            decision = _record_nonlaunch_decision(
                spec=spec,
                state=state,
                due=due,
                run_id=run_id,
                trigger=trigger,
                reason=backpressure,
                status="deferred",
                event="deferred",
                repo_root=repo_root,
                now=now,
                runtime_policy_snapshot=runtime_policy_snapshot,
            )
            return {
                "ok": False,
                "job_id": job_id,
                "skipped": True,
                "reason": backpressure,
                "deferred": True,
                "run_id": str(decision.get("run_id") or run_id),
                "receipt_path": str(decision.get("receipt_path") or ""),
                "report_path": str(decision.get("report_path") or ""),
            }
        project_runner_policy_decision = _project_runner_launch_policy_decision(
            spec,
            repo_root=repo_root,
            runtime_policy_snapshot=runtime_policy_snapshot,
            run_id=run_id,
            now=now,
        )
        if not bool(project_runner_policy_decision.get("allowed", True)):
            decision_reason = str(project_runner_policy_decision.get("reason") or "project_runner_launch_blocked")
            decision_status = str(project_runner_policy_decision.get("status") or "blocked")
            decision_event = str(project_runner_policy_decision.get("event") or decision_status)
            decision = _record_nonlaunch_decision(
                spec=spec,
                state=state,
                due=due,
                run_id=run_id,
                trigger=trigger,
                reason=decision_reason,
                status=decision_status,
                event=decision_event,
                repo_root=repo_root,
                now=now,
                runtime_policy_snapshot=runtime_policy_snapshot,
            )
            out = {
                "ok": False,
                "job_id": job_id,
                "skipped": True,
                "reason": decision_reason,
                "run_id": str(decision.get("run_id") or run_id),
                "receipt_path": str(decision.get("receipt_path") or ""),
                "report_path": str(decision.get("report_path") or ""),
            }
            if decision_status == "deferred":
                out["deferred"] = True
            return out
        guard = _repo_guard_preflight(spec=spec, repo_root=repo_root)
        if bool(guard.get("blocked")):
            reason = str(guard.get("reason") or "repo_guard_blocked")
            decision = _record_nonlaunch_decision(
                spec=spec,
                state=state,
                due=due,
                run_id=run_id,
                trigger=trigger,
                reason=reason,
                status="blocked",
                event="blocked",
                repo_root=repo_root,
                now=now,
                execution_guard=guard,
                runtime_policy_snapshot=runtime_policy_snapshot,
            )
            return {
                "ok": False,
                "job_id": job_id,
                "skipped": True,
                "reason": reason,
                "run_id": str(decision.get("run_id") or run_id),
                "receipt_path": str(decision.get("receipt_path") or ""),
                "report_path": str(decision.get("report_path") or ""),
            }
        budget_admission = _evaluate_job_budget_admission(spec, repo_root=repo_root)
        if not bool(budget_admission.get("allowed")):
            reason = str(budget_admission.get("reason") or "job_budget_admission_blocked").strip() or "job_budget_admission_blocked"
            decision = _record_nonlaunch_decision(
                spec=spec,
                state=state,
                due=due,
                run_id=run_id,
                trigger=trigger,
                reason=reason,
                status="blocked",
                event="blocked",
                repo_root=repo_root,
                now=now,
                budget_admission=budget_admission,
                runtime_policy_snapshot=runtime_policy_snapshot,
            )
            return {
                "ok": False,
                "job_id": job_id,
                "skipped": True,
                "reason": reason,
                "run_id": str(decision.get("run_id") or run_id),
                "receipt_path": str(decision.get("receipt_path") or ""),
                "report_path": str(decision.get("report_path") or ""),
            }
        if not force:
            binding_launch_guard = _binding_launch_repeat_guard(spec, repo_root=repo_root)
            if bool(binding_launch_guard.get("blocked")):
                reason = str(binding_launch_guard.get("reason") or "binding_launch_repeat_guard").strip() or "binding_launch_repeat_guard"
                decision = _record_nonlaunch_decision(
                    spec=spec,
                    state=state,
                    due=due,
                    run_id=run_id,
                    trigger=trigger,
                    reason=reason,
                    status="blocked",
                    event="blocked",
                    repo_root=repo_root,
                    now=now,
                    execution_guard=binding_launch_guard,
                    runtime_policy_snapshot=runtime_policy_snapshot,
                )
                return {
                    "ok": False,
                    "job_id": job_id,
                    "skipped": True,
                    "reason": reason,
                    "run_id": str(decision.get("run_id") or run_id),
                    "receipt_path": str(decision.get("receipt_path") or ""),
                    "report_path": str(decision.get("report_path") or ""),
                }
        if job_kind == "project_runner" and not force:
            repeat_exit_guard = _project_runner_repeat_exit_guard(spec, repo_root=repo_root)
            if bool(repeat_exit_guard.get("blocked")):
                reason = str(repeat_exit_guard.get("reason") or "project_runner_repeat_exit_guard").strip() or "project_runner_repeat_exit_guard"
                decision = _record_nonlaunch_decision(
                    spec=spec,
                    state=state,
                    due=due,
                    run_id=run_id,
                    trigger=trigger,
                    reason=reason,
                    status="blocked",
                    event="blocked",
                    repo_root=repo_root,
                    now=now,
                    execution_guard=repeat_exit_guard,
                    runtime_policy_snapshot=runtime_policy_snapshot,
                )
                return {
                    "ok": False,
                    "job_id": job_id,
                    "skipped": True,
                    "reason": reason,
                    "repeat_exit_guard": repeat_exit_guard,
                    "run_id": str(decision.get("run_id") or run_id),
                    "receipt_path": str(decision.get("receipt_path") or ""),
                    "report_path": str(decision.get("report_path") or ""),
                }

        continuation_bootstrap = _continuation_context_attachment.continuation_bootstrap_projection_for_job_spec(
            spec,
            repo_root=repo_root,
            as_of_utc=_utc_iso(now),
        )
        if str(continuation_bootstrap.get("status") or "").strip() == "blocked":
            reason_detail = str(continuation_bootstrap.get("reason") or "").strip() or "blocked_continuation_bootstrap"
            reason = f"continuation_bootstrap_blocked:{reason_detail}"
            decision = _record_nonlaunch_decision(
                spec=spec,
                state=state,
                due=due,
                run_id=run_id,
                trigger=trigger,
                reason=reason,
                status="blocked",
                event="blocked",
                repo_root=repo_root,
                now=now,
                runtime_policy_snapshot=runtime_policy_snapshot,
                continuation_bootstrap=continuation_bootstrap,
            )
            return {
                "ok": False,
                "job_id": job_id,
                "skipped": True,
                "reason": reason,
                "run_id": str(decision.get("run_id") or run_id),
                "receipt_path": str(decision.get("receipt_path") or ""),
                "report_path": str(decision.get("report_path") or ""),
            }

        started_utc = _utc_iso(now)
        slot_key = str(due.get("slot_key") or "")
        job_shape_fields = _resolve_job_shape_fields(spec)

        spec = dict(spec)
        spec["_continuation_bootstrap"] = dict(continuation_bootstrap)
        spec_snapshot = dict(spec)
        spec_snapshot["job_shape"] = str(job_shape_fields.get("job_shape") or "")
        spec_snapshot["job_shape_source"] = str(job_shape_fields.get("job_shape_source") or "")
        spec_snapshot["job_shape_compatibility_mode"] = bool(job_shape_fields.get("job_shape_compatibility_mode"))
        spec_snapshot["execution"] = {"run_id": run_id, "trigger": trigger, "started_utc": started_utc}
        spec_snapshot["runtime_policy_snapshot"] = dict(runtime_policy_snapshot)
        spec_archive = (archive_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(run_id)}__spec.json").resolve()
        _write_json_atomic(spec_archive, spec_snapshot)
        binding_type = str((spec.get("binding") or {}).get("type") or "").strip().lower()
        launch_binding = (
            _seed_single_turn_owner_binding(spec, run_id=run_id, trigger=trigger, repo_root=repo_root)
            if binding_type in {"ledger_uid", "task_id"}
            else dict(spec.get("binding") or {})
        )
        launch_binding_result = _bind_scheduler_launch(
            spec=spec,
            binding_override=launch_binding,
            run_id=run_id,
            trigger=trigger,
            status="running",
            reason="",
            event="started",
            receipt_path="",
            report_path="",
            repo_root=repo_root,
            reference_status="execution_ref_active",
            reference_status_reason="scheduled_job_started",
            writer="scheduler.launch.bind",
            launch_cause=trigger or "scheduler",
        )
        if not bool((launch_binding_result or {}).get("ok")):
            binding_reason = str((launch_binding_result or {}).get("reason") or "binding_launch_failed").strip() or "binding_launch_failed"
            if str((launch_binding_result or {}).get("error") or "").strip():
                binding_reason = f"{binding_reason}:{str((launch_binding_result or {}).get('error') or '').strip()}"
            decision = _record_nonlaunch_decision(
                spec=spec,
                state=state,
                due=due,
                run_id=run_id,
                trigger=trigger,
                reason=binding_reason,
                status="blocked",
                event="blocked",
                repo_root=repo_root,
                now=now,
                binding_override=launch_binding,
                runtime_policy_snapshot=runtime_policy_snapshot,
            )
            return {
                "ok": False,
                "job_id": job_id,
                "skipped": True,
                "reason": binding_reason,
                "binding_result": dict(launch_binding_result or {}),
                "run_id": str(decision.get("run_id") or run_id),
                "receipt_path": str(decision.get("receipt_path") or ""),
                "report_path": str(decision.get("report_path") or ""),
            }
        _write_state(
            job_id,
            {
                **state,
                "active_run_id": run_id,
                "active_started_utc": started_utc,
                "active_loop_iteration": 0,
                "active_loop_stage": "",
                "active_loop_completed_iterations": 0,
                "active_loop_max_iterations": 0,
                "active_loop_updated_utc": "",
                "last_status": "running",
                "last_trigger": trigger,
                "run_now_requested_utc": "",
                "run_now_request_run_id": "",
                "run_now_request_source": "",
            },
            repo_root=repo_root,
        )
        _append_event(
            repo_root=repo_root,
            job_id=job_id,
            run_id=run_id,
            event="started",
            trigger=trigger,
            status="running",
            details={
                "slot_key": slot_key,
                "due_utc": str(due.get("due_utc") or ""),
                "budget_admission": dict(budget_admission or {}) if isinstance(budget_admission, dict) else {},
            },
        )
        _touch_job_claim(job_id, repo_root=repo_root, run_id=run_id, now=now)
        heartbeat.start()
        repo_residue_baseline = _parse_git_status_entries(repo_root=repo_root)
        project_runner_runtime_claims: List[Dict[str, Any]] = []
        project_runner_runtime_claim_error = ""
        scheduled_job_runtime_claim_receipt: Dict[str, Any] | None = None
        scheduled_job_runtime_claim_handled = False
        if job_kind == "project_runner":
            project_runner_runtime_claims = _record_project_runner_runtime_claim_acquired(
                spec=spec,
                run_id=run_id,
                now_utc=started_utc,
                repo_root=repo_root,
            )

        policy = _normalize_policy(spec.get("policy"), job_kind=job_kind)
        max_job_attempts = max(1, int(policy.get("job_transient_retries") or 0) + 1)
        job_retry_backoff_s = max(1, int(policy.get("job_transient_retry_backoff_seconds") or 30))
        retry_history: List[Dict[str, Any]] = []

        ok = False
        receipt: Dict[str, Any] = {}
        for run_attempt in range(1, max_job_attempts + 1):
            if job_kind == "single_turn":
                ok, receipt, _receipt_error = _execute_single_turn_job(
                    spec,
                    binding_override=launch_binding,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    trigger=trigger,
                    max_runtime_seconds=max_runtime_seconds,
                    repo_root=repo_root,
                )
            elif job_kind == "swarm_launch":
                ok, receipt, _receipt_error = _execute_swarm_launch_job(
                    spec,
                    binding_override=launch_binding,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    trigger=trigger,
                    max_runtime_seconds=max_runtime_seconds,
                    repo_root=repo_root,
                )
            elif job_kind == "project_runner":
                ok, receipt, _receipt_error = _execute_project_runner_job(
                    spec,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    trigger=trigger,
                    max_runtime_seconds=max_runtime_seconds,
                    repo_root=repo_root,
                )
            else:
                runtime = _normalize_runtime(spec.get("runtime"), job_kind=job_kind)
                if str(runtime.get("executor_agent_tech") or "") == "pipeline" and str(runtime.get("pipeline_executor_mode") or "") == PIPELINE_FULL_AUTO_MODE:
                    ok, receipt, _receipt_error = _execute_pipeline_full_job(
                        spec,
                        run_id=run_id,
                        run_attempt=run_attempt,
                        trigger=trigger,
                        max_runtime_seconds=max_runtime_seconds,
                        repo_root=repo_root,
                    )
                else:
                    ok, receipt, _receipt_error = _execute_build_verify_loop_job(
                        spec,
                        binding_override=launch_binding,
                        run_id=run_id,
                        run_attempt=run_attempt,
                        trigger=trigger,
                        max_runtime_seconds=max_runtime_seconds,
                        repo_root=repo_root,
                    )
            receipt["job_attempt"] = run_attempt
            if ok:
                break
            failure = _classify_whole_job_failure_for_retry(spec, receipt)
            failure_row = {
                "attempt": run_attempt,
                "status": str(receipt.get("status") or ""),
                "error": str(receipt.get("error") or ""),
                "failure_class": str(failure.get("class") or ""),
                "classification_reason": str(failure.get("reason") or ""),
                "retryable": bool(failure.get("retryable")),
            }
            retry_history.append(failure_row)
            can_retry = bool(failure.get("retryable")) and run_attempt < max_job_attempts
            _append_event(
                repo_root=repo_root,
                job_id=job_id,
                run_id=run_id,
                event="retrying" if can_retry else "retry_stopped",
                trigger=trigger,
                status=str(receipt.get("status") or ""),
                details={
                    **failure_row,
                    "next_attempt": run_attempt + 1 if can_retry else 0,
                    "max_attempts": max_job_attempts,
                },
            )
            if not can_retry:
                break
            time.sleep(job_retry_backoff_s)

        finished_utc = _utc_iso()
        receipt["started_utc"] = started_utc
        receipt["finished_utc"] = finished_utc
        receipt["spec_archive"] = str(spec_archive)
        receipt["runtime_policy_snapshot"] = dict(runtime_policy_snapshot)
        receipt["ownership"] = {
            "scheduler_scope": "launch_intent_and_handoff_evidence",
            "runtime_scope": "runtime_execution_and_progress",
            "handoff_state": "launched",
        }
        receipt["binding_result"] = launch_binding_result
        receipt["model_telemetry"] = _receipt_model_telemetry(receipt)
        receipt["runtime_backend"] = _runtime_backend_telemetry()
        receipt["executor_of_record"] = _scheduled_executor_of_record_audit(spec=spec, receipt=receipt)
        receipt["validation_claim_hygiene"] = _validation_claim_hygiene(spec=spec, receipt=receipt)
        if bool(budget_admission.get("enforced")):
            receipt["budget_admission"] = dict(budget_admission)
        if slot_key:
            receipt["slot_key"] = slot_key
        if ok and _normalize_job_kind(spec.get("job_kind")) == "build_verify_loop":
            try:
                child_queue = _queue_child_implementation_jobs_after_pass(spec, finished_utc=finished_utc, repo_root=repo_root)
            except Exception as exc:
                child_queue = {"enabled": True, "created_count": 0, "skipped_count": 0, "children": [], "error": str(exc)}
                ok = False
                receipt["status"] = "error"
                receipt["error"] = f"post-pass child job queueing failed: {exc}"
            receipt["child_job_queue"] = child_queue
            if ok:
                receipt["child_job_autostart"] = _request_autonomous_continuation_job(
                    child_queue,
                    finished_utc=finished_utc,
                    repo_root=repo_root,
                    request_source="governance_decomposition_handoff",
                )
        if ok and bool((spec.get("policy") or {}).get("post_success_commit_tag")):
            root = Path(repo_root or _repo_root()).resolve()
            should_attempt_commit = (root / ".git").exists() and bool(_git_status_porcelain(repo_root=root))
            verification_fragment = (
                _record_post_success_verification_fragment(spec=spec, receipt=receipt, repo_root=repo_root)
                if should_attempt_commit
                else {"ok": True, "skipped": True, "reason": "commit_not_attempted", "path": ""}
            )
            receipt["verification_commit_fragment"] = verification_fragment
            git_hook = _run_post_success_commit_tag_with_retries(
                repo_root=repo_root,
                retries=int((spec.get("policy") or {}).get("post_success_commit_tag_retries") or 0),
                backoff_seconds=int((spec.get("policy") or {}).get("post_success_commit_tag_backoff_seconds") or 60),
            )
            receipt["git_commit_tag"] = git_hook
            if not bool(git_hook.get("ok")):
                ok = False
                receipt["status"] = "error"
                receipt["error"] = f"post-success git commit/tag failed (exit_code={git_hook.get('exit_code')})"
        final_failure = _classify_whole_job_failure_for_retry(spec, receipt) if not ok else {"class": "none", "reason": ""}
        receipt["job_retry"] = {
            "policy": {
                "job_transient_retries": int(policy.get("job_transient_retries") or 0),
                "job_transient_retry_backoff_seconds": int(policy.get("job_transient_retry_backoff_seconds") or 30),
            },
            "attempt_count": int(receipt.get("job_attempt") or 1),
            "retries_used": max(0, int(receipt.get("job_attempt") or 1) - 1),
            "succeeded_after_retry": bool(ok and int(receipt.get("job_attempt") or 1) > 1),
            "history": retry_history,
            "final_failure_class": str(final_failure.get("class") or ""),
            "final_failure_reason": str(final_failure.get("reason") or ""),
        }
        compiled_execution = receipt.get("compiled_execution") if isinstance(receipt.get("compiled_execution"), dict) else {}
        if compiled_execution:
            persistence = _compiled_artifacts.persist_compiled_artifacts(
                runtime_root=_root(repo_root),
                job_id=str(spec.get("job_id") or ""),
                run_id=run_id,
                compiled_execution=compiled_execution,
                loop=receipt.get("loop") if isinstance(receipt.get("loop"), dict) else None,
            )
            compiled_execution["artifact_persistence"] = persistence
            receipt["compiled_execution"] = compiled_execution

        latest_state = load_state(job_id, repo_root=repo_root)
        receipt["repo_residue"] = _build_repo_residue_evidence(
            baseline=repo_residue_baseline,
            repo_root=repo_root,
            declared_changed_files=_collect_receipt_declared_changed_files(receipt, repo_root=repo_root),
            validation_commands=_collect_receipt_validation_commands(receipt),
            artifact_refs=_collect_receipt_artifact_refs(receipt),
            source="execution",
        )
        finished_dt = _parse_utc(finished_utc)
        claim_health = _active_claim_health(
            job_id,
            repo_root=repo_root,
            max_runtime_seconds=max_runtime_seconds,
            expected_run_id=run_id,
            now=finished_dt,
        )
        cancel_requested_utc = str(latest_state.get("cancel_requested_utc") or "").strip()
        cancel_requested = bool(cancel_requested_utc) and str(latest_state.get("cancel_request_run_id") or run_id).strip() in {"", run_id}
        timeout_expired = str(receipt.get("failure_kind") or "").strip() == "timeout" or str(receipt.get("status") or "").strip().lower() in {"timed_out", "timeout"}
        cancel_result = ""
        if cancel_requested:
            cancel_result = "cancelled" if str(receipt.get("status") or "").strip().lower() == "cancelled" else "finished_before_stop"
        receipt["lifecycle"] = {
            "schema_version": "scheduled_job_lifecycle_v1",
            "reconciled_utc": finished_utc,
            "status": str(receipt.get("status") or ""),
            "cancel": {
                "requested": bool(cancel_requested),
                "requested_utc": cancel_requested_utc,
                "reason": str(latest_state.get("cancel_request_reason") or ""),
                "request_run_id": str(latest_state.get("cancel_request_run_id") or ""),
                "result": cancel_result,
            },
            "timeout": {
                "expired": bool(timeout_expired),
                "failure_kind": str(receipt.get("failure_kind") or ""),
                "timeout_s": max_runtime_seconds,
            },
            "heartbeat": {
                "claim_present": bool(claim_health.get("present")),
                "claim_reason": str(claim_health.get("reason") or ""),
                "heartbeat_utc": str(claim_health.get("heartbeat_utc") or ""),
                "age_s": claim_health.get("age_s"),
            },
        }
        receipt["job_shape_lifecycle"] = dict(
            _job_shape_lifecycle_contract.lifecycle_projection_for_shape_record(
                str(receipt.get("job_shape") or job_shape_fields.get("job_shape") or ""),
                str(receipt.get("status") or ""),
                active_run=False,
            )
        )
        if project_runner_runtime_claims:
            release_detail = (
                f"runtime claim terminated for project runner run {run_id}; "
                f"job_status={str(receipt.get('status') or '')}; "
                f"error={str(receipt.get('error') or '')}"
            )
            try:
                released_claims = _record_project_runner_runtime_claim_release(
                    spec=spec,
                    run_id=run_id,
                    terminal_status=str(receipt.get("status") or ""),
                    recorded_at_utc=finished_utc,
                    detail=release_detail,
                    repo_root=repo_root,
                )
                for claim, released in zip(project_runner_runtime_claims, released_claims):
                    claim["terminal_status"] = str(released.get("terminal_status") or "")
                    claim["terminal_entry_uid"] = str(released.get("terminal_entry_uid") or "")
                    claim["terminal_at_utc"] = str(released.get("terminal_at_utc") or "")
                    claim["terminal_source"] = str(released.get("terminal_source") or "")
            except Exception as exc:
                project_runner_runtime_claim_error = str(exc)
        if project_runner_runtime_claims or project_runner_runtime_claim_error:
            receipt["resource_claims"] = {
                "claimant_uid": _project_runner_claimant_uid(job_id),
                "run_id": run_id,
                "claims": project_runner_runtime_claims,
                "release_error": project_runner_runtime_claim_error,
            }

        receipt_path = (receipts_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(run_id)}.json").resolve()
        report_path = (reports_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(run_id)}.md").resolve()

        projected_state = {
            **latest_state,
            "active_run_id": "",
            "active_started_utc": "",
            "active_loop_iteration": 0,
            "active_loop_stage": "",
            "active_loop_completed_iterations": 0,
            "active_loop_max_iterations": 0,
            "active_loop_updated_utc": "",
            "last_run_id": run_id,
            "last_started_utc": started_utc,
            "last_finished_utc": finished_utc,
            "last_status": str(receipt.get("status") or ""),
            "last_trigger": trigger,
            "last_receipt_path": str(receipt_path),
            "last_report_path": str(report_path),
            "last_nonlaunch_reason": "",
            "last_nonlaunch_status": "",
            "last_nonlaunch_utc": "",
            "last_nonlaunch_run_id": "",
            "last_nonlaunch_receipt_path": "",
            "last_nonlaunch_report_path": "",
            "last_nonlaunch_signature": "",
            "run_now_requested_utc": "",
            "run_now_request_run_id": "",
            "run_now_request_source": "",
            "cancel_requested_utc": "",
            "cancel_request_reason": "",
            "cancel_request_run_id": "",
            "cancel_request_receipt_path": "",
        }
        if slot_key:
            schedule_mode = str(((spec.get("schedule") or {}).get("mode") or "")).strip().lower()
            status_lower = str(receipt.get("status") or "").strip().lower()
            # For one-shot jobs, only consume the slot on success. Otherwise a failed/blocked run
            # can deadlock dependency chains behind `already_executed`.
            consume_slot = schedule_mode != "once" or _is_success_status(status_lower) or bool(receipt.get("no_auto_retry"))
            if consume_slot:
                projected_state["last_slot_key"] = slot_key
            elif schedule_mode == "once":
                projected_state.pop("last_slot_key", None)
        due_after = due_status(spec, state=projected_state, now=finished_dt, repo_root=repo_root)
        receipt["reconciliation"] = _build_scheduler_reconciliation(spec, state=projected_state, due=due_after, now=finished_dt, repo_root=repo_root)
        receipt["forensic_debug_kit"] = _build_forensic_debug_kit(
            spec=spec,
            receipt=receipt,
            receipt_path=str(receipt_path),
            report_path=str(report_path),
        )
        claim_terminal_status = str(receipt.get("status") or "")
        claim_release_detail = (
            f"runtime claim terminated for scheduled job run {run_id}; "
            f"job_status={str(receipt.get('status') or '')}; "
            f"error={str(receipt.get('error') or '')}"
        )
        scheduled_job_runtime_claim_receipt = _release_scheduled_job_runtime_claim_with_receipt(
            job_id=job_id,
            run_id=run_id,
            terminal_status=claim_terminal_status,
            detail=claim_release_detail,
            repo_root=repo_root,
        )
        scheduled_job_runtime_claim_handled = True
        if job_kind == "project_runner" and isinstance(receipt.get("resource_claims"), dict):
            receipt["runtime_claim"] = scheduled_job_runtime_claim_receipt
        else:
            receipt["resource_claims"] = scheduled_job_runtime_claim_receipt

        _write_json_atomic(receipt_path, receipt)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(_job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")

        new_state = {
            **projected_state,
            **_blank_state_receipt_projection(),
            **_receipt_state_projection(receipt),
        }
        _write_state(job_id, new_state, repo_root=repo_root)
        receipt["terminal_claim_cleanup"] = _reconcile_terminal_job_claim(
            job_id,
            repo_root=repo_root,
            terminal_status=str(receipt.get("status") or ""),
            detail=claim_release_detail or "terminal_state_reconciliation_cleanup",
        )
        _write_json_atomic(receipt_path, receipt)
        report_path.write_text(_job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")

        schedule = spec.get("schedule") if isinstance(spec.get("schedule"), dict) else {}
        schedule_mode = str(schedule.get("mode") or "").strip().lower()
        if schedule_mode == "once" and _is_success_status(receipt.get("status")):
            receipt["terminal_once_archive"] = archive_terminal_once_job(
                job_id,
                run_id=run_id,
                finished_utc=_utc_iso(finished_dt),
                reason="once_job_completed",
                repo_root=repo_root,
            )
            _write_json_atomic(receipt_path, receipt)
            report_path.write_text(_job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")

        if ok and _is_success_status(receipt.get("status")):
            try:
                family_handoff = _post_success_progress_handoff(
                    spec,
                    finished_utc=finished_utc,
                    repo_root=repo_root,
                )
            except Exception as exc:
                family_handoff = {
                    "ok": False,
                    "requested": False,
                    "reason": f"family_handoff_error:{type(exc).__name__}",
                    "error": str(exc),
                }
            receipt["family_handoff"] = family_handoff
            _write_json_atomic(receipt_path, receipt)
            report_path.write_text(_job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")

        _append_event(
            repo_root=repo_root,
            job_id=job_id,
            run_id=run_id,
            event="finished" if ok else "failed",
            trigger=trigger,
            status=str(receipt.get("status") or ""),
            details={"receipt_path": str(receipt_path), "report_path": str(report_path), "slot_key": slot_key},
        )
        return {"ok": ok, "job_id": job_id, "run_id": run_id, "receipt_path": str(receipt_path), "report_path": str(report_path), "receipt": receipt}
    except Exception as exc:
        finished_utc = _utc_iso()
        error_text = f"{type(exc).__name__}: {exc}"
        try:
            latest_state = load_state(job_id, repo_root=repo_root)
        except Exception:
            latest_state = {}
        receipt_path = (receipts_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(run_id)}.json").resolve()
        report_path = (reports_dir(repo_root) / _safe_name(job_id) / f"{_safe_name(run_id)}.md").resolve()
        claim_terminal_status = "error"
        claim_release_detail = f"runtime claim terminated after scheduled job crash for {run_id}; error={error_text}"
        try:
            runtime_claim_receipt = _release_scheduled_job_runtime_claim_with_receipt(
                job_id=job_id,
                run_id=run_id,
                terminal_status=claim_terminal_status,
                detail=claim_release_detail,
                repo_root=repo_root,
            )
            scheduled_job_runtime_claim_handled = True
        except Exception as release_exc:
            runtime_claim_receipt = {
                "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
                "run_id": run_id,
                "claims": [],
                "release_error": str(release_exc),
            }
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "job_id": job_id,
            "run_id": run_id,
            "job_kind": job_kind,
            "started_utc": str(latest_state.get("active_started_utc") or _utc_iso(now)),
            "finished_utc": finished_utc,
            "status": "error",
            "error": error_text,
            "error_traceback": traceback.format_exc(),
            "failure_kind": "scheduler_worker_exception",
            "trigger": trigger,
            "job_attempt": 1,
            "spec_archive": "",
            "resource_claims": runtime_claim_receipt,
            "lifecycle": {
                "schema_version": "scheduled_job_lifecycle_v1",
                "reconciled_utc": finished_utc,
                "status": "error",
                "cancel": {
                    "requested": False,
                    "requested_utc": "",
                    "reason": "",
                    "request_run_id": "",
                    "result": "",
                },
                "timeout": {
                    "expired": False,
                    "failure_kind": "scheduler_worker_exception",
                    "timeout_s": max_runtime_seconds,
                },
                "heartbeat": {
                    "claim_present": False,
                    "claim_reason": "worker_exception",
                    "heartbeat_utc": "",
                    "age_s": None,
                },
            },
            "job_shape_lifecycle": dict(
                _job_shape_lifecycle_contract.lifecycle_projection_for_shape_record(
                    str(_resolve_job_shape_fields(spec).get("job_shape") or ""),
                    "error",
                    active_run=False,
                )
            ),
        }
        try:
            _write_json_atomic(receipt_path, receipt)
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(_job_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")
        except Exception:
            pass
        error_state = {
            **latest_state,
            **_blank_state_receipt_projection(),
            **_receipt_state_projection(receipt),
            "active_run_id": "",
            "active_started_utc": "",
            "active_loop_iteration": 0,
            "active_loop_stage": "",
            "active_loop_completed_iterations": 0,
            "active_loop_max_iterations": 0,
            "active_loop_updated_utc": "",
            "last_run_id": run_id,
            "last_started_utc": str(latest_state.get("active_started_utc") or _utc_iso(now)),
            "last_finished_utc": finished_utc,
            "last_status": "error",
            "last_trigger": trigger,
            "last_receipt_path": str(receipt_path),
            "last_report_path": str(report_path),
            "last_nonlaunch_reason": "",
            "last_nonlaunch_status": "",
            "last_nonlaunch_utc": "",
            "last_nonlaunch_run_id": "",
            "last_nonlaunch_receipt_path": "",
            "last_nonlaunch_report_path": "",
            "last_nonlaunch_signature": "",
            "run_now_requested_utc": "",
            "run_now_request_run_id": "",
            "run_now_request_source": "",
            "updated_utc": finished_utc,
        }
        _write_state(job_id, error_state, repo_root=repo_root)
        _append_event(
            repo_root=repo_root,
            job_id=job_id,
            run_id=run_id,
            event="failed",
            trigger=trigger,
            status="error",
            details={"receipt_path": str(receipt_path), "report_path": str(report_path), "error": error_text},
        )
        return {"ok": False, "job_id": job_id, "run_id": run_id, "receipt_path": str(receipt_path), "report_path": str(report_path), "receipt": receipt}
    finally:
        heartbeat.stop()
        if not scheduled_job_runtime_claim_handled:
            _release_job_claim(
                job_id,
                repo_root=repo_root,
                run_id=run_id,
                terminal_status=claim_terminal_status,
                detail=claim_release_detail,
            )


def postpone_job(
    job_id: str,
    *,
    days: int = 1,
    repo_root: Optional[Path] = None,
    reason: str = "operator_postpone",
    evidence: Optional[Dict[str, Any]] = None,
    now_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Postpone a scheduled once-job by N days.

    If the job is actively running, record a postpone request without claiming
    the run has stopped. If the job is idle, update the run_at slot and produce
    a durable postpone receipt. Dependents will re-evaluate on the next
    scheduler tick.

    Returns a dict with receipt_path, old_run_at, new_run_at, and status.
    """
    root = Path(repo_root or _repo_root()).resolve()
    now = _parse_utc(now_utc) if now_utc else _utc_now()
    spec = load_job(job_id, repo_root=root)
    state = load_state(job_id, repo_root=root)

    # Verify this is a once-mode job
    schedule = spec.get("schedule", {})
    if str(schedule.get("mode", "")).lower() != "once":
        raise ValueError(f"job {job_id} is not schedule.mode=once")

    old_run_at = str(schedule.get("run_at", "")).strip()
    if not old_run_at:
        raise ValueError(f"job {job_id} has no run_at slot")

    # Calculate new run_at
    try:
        old_dt = _parse_utc(old_run_at)
    except Exception:
        raise ValueError(f"could not parse run_at: {old_run_at}")

    new_dt = old_dt + _dt.timedelta(days=max(1, int(days)))
    new_run_at = _utc_iso(new_dt)

    # Create postpone receipt
    active_run_id = str(state.get("active_run_id") or "").strip()
    claim_run_id = active_run_id
    run_id = active_run_id or f"sj_{_utc_compact(now)}_postpone_{uuid.uuid4().hex[:6]}"
    active_claim = _active_claim_health(
        job_id,
        repo_root=root,
        max_runtime_seconds=int((spec.get("policy") or {}).get("max_runtime_seconds") or 1800),
        expected_run_id=active_run_id,
        now=now,
    )
    stop_proof_pending = bool(active_run_id) or (bool(active_claim.get("present")) and not bool(active_claim.get("stale")))
    requested_status = "postpone_requested" if stop_proof_pending else "postponed"

    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "job_id": job_id,
        "run_id": run_id,
        "job_kind": str(spec.get("job_kind") or "single_turn"),
        "started_utc": str(state.get("active_started_utc") or _utc_iso(now)),
        "finished_utc": _utc_iso(now),
        "status": requested_status,
        "postpone_reason": str(reason or ""),
        "postpone_days": int(days),
        "postpone_old_run_at": old_run_at,
        "postpone_new_run_at": new_run_at,
        "postpone_evidence": dict(evidence or {}),
        "stop_proof_pending": stop_proof_pending,
        "error": "",
    }

    receipt_path = (receipts_dir(root) / _safe_name(job_id) / f"{_safe_name(run_id)}.json").resolve()
    binding_result = _bind_scheduler_launch(
        spec=spec,
        run_id=run_id,
        trigger="operator",
        status=requested_status,
        reason=str(reason or "operator_postpone"),
        event=requested_status,
        receipt_path=str(receipt_path),
        report_path="",
        repo_root=root,
        reference_status="execution_ref_stale",
        reference_status_reason=str(reason or "operator_postpone"),
        writer="scheduler.operator.bind",
        launch_cause="operator_action",
    )
    if binding_result:
        receipt["binding_result"] = binding_result

    # Write receipt
    _write_json_atomic(receipt_path, receipt)

    if stop_proof_pending:
        _write_json_atomic(receipt_path, receipt)
        requested_state = dict(state)
        requested_state["postpone_requested_utc"] = _utc_iso(now)
        requested_state["postpone_request_reason"] = str(reason or "")
        requested_state["postpone_request_run_id"] = run_id
        requested_state["postpone_request_new_run_at"] = new_run_at
        requested_state["postpone_request_receipt_path"] = str(receipt_path)
        requested_state["updated_utc"] = _utc_iso(now)
        _write_state(job_id, requested_state, repo_root=root)
    else:
        receipt["resource_claims"] = _release_scheduled_job_runtime_claim_with_receipt(
            job_id=job_id,
            run_id=claim_run_id or run_id,
            terminal_status="cancelled",
            detail=(
                f"runtime claim cancelled for scheduled job run {claim_run_id or run_id}; "
                f"operator_action=postpone; reason={str(reason or '')}"
            ),
            repo_root=root,
        )
        _write_json_atomic(receipt_path, receipt)
        new_spec = dict(spec)
        new_schedule = dict(new_spec.get("schedule", {}))
        new_schedule["run_at"] = new_run_at
        new_spec["schedule"] = new_schedule
        save_job(new_spec, repo_root=root)

        new_state = dict(state)
        new_state.update(_blank_state_receipt_projection())
        new_state["active_run_id"] = ""
        new_state["active_started_utc"] = ""
        new_state["active_loop_iteration"] = 0
        new_state["active_loop_stage"] = ""
        new_state["active_loop_completed_iterations"] = 0
        new_state["active_loop_max_iterations"] = 0
        new_state["active_loop_updated_utc"] = ""
        new_state["last_run_id"] = run_id
        new_state["last_started_utc"] = str(receipt.get("started_utc") or "")
        new_state["last_status"] = "postponed"
        new_state["last_finished_utc"] = _utc_iso(now)
        new_state["last_trigger"] = "operator"
        new_state["last_receipt_path"] = str(receipt_path)
        new_state["last_report_path"] = ""
        new_state["last_nonlaunch_reason"] = ""
        new_state["last_nonlaunch_status"] = ""
        new_state["last_nonlaunch_utc"] = ""
        new_state["last_nonlaunch_run_id"] = ""
        new_state["last_nonlaunch_receipt_path"] = ""
        new_state["last_nonlaunch_report_path"] = ""
        new_state["last_nonlaunch_signature"] = ""
        new_state["run_now_requested_utc"] = ""
        new_state["run_now_request_run_id"] = ""
        new_state["run_now_request_source"] = ""
        new_state.pop("last_slot_key", None)
        new_state["updated_utc"] = _utc_iso(now)
        _write_state(job_id, new_state, repo_root=root)

    _append_event(
        repo_root=root,
        job_id=job_id,
        run_id=run_id,
        event=requested_status,
        trigger="operator",
        status=requested_status,
        details={
            "reason": str(reason or ""),
            "days": int(days),
            "old_run_at": old_run_at,
            "new_run_at": new_run_at,
            "receipt_path": str(receipt_path),
            "stop_proof_pending": stop_proof_pending,
        },
    )

    return {
        "ok": True,
        "job_id": job_id,
        "run_id": run_id,
        "status": requested_status,
        "old_run_at": old_run_at,
        "new_run_at": new_run_at,
        "schedule_updated": not stop_proof_pending,
        "stop_proof_pending": stop_proof_pending,
        "receipt_path": str(receipt_path),
        "receipt": receipt,
    }


def cancel_job(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    reason: str = "operator_cancel",
    evidence: Optional[Dict[str, Any]] = None,
    now_utc: Optional[str] = None,
) -> Dict[str, Any]:
    """Cancel a scheduled job (running or queued).

    If the job is actively running, record a cancel request without claiming
    that the run has actually stopped. Idle jobs may still be marked cancelled
    immediately because there is no active execution to prove.

    Returns a dict with receipt_path and status.
    """
    root = Path(repo_root or _repo_root()).resolve()
    now = _parse_utc(now_utc) if now_utc else _utc_now()
    spec = load_job(job_id, repo_root=root)
    state = load_state(job_id, repo_root=root)

    active_run_id = str(state.get("active_run_id") or "").strip()
    claim_run_id = active_run_id
    run_id = active_run_id or f"sj_{_utc_compact(now)}_cancel_{uuid.uuid4().hex[:6]}"
    active_claim = _active_claim_health(
        job_id,
        repo_root=root,
        max_runtime_seconds=int((spec.get("policy") or {}).get("max_runtime_seconds") or 1800),
        expected_run_id=active_run_id,
        now=now,
    )
    stop_proof_pending = bool(active_run_id) or (bool(active_claim.get("present")) and not bool(active_claim.get("stale")))
    if active_run_id:
        active_recovery = _active_run_recovery_status(spec, state=state, now=now, repo_root=root)
        if bool(active_recovery.get("recover")):
            state = _apply_active_run_recovery(
                job_id=job_id,
                state=state,
                recovery=active_recovery,
                trigger="operator",
                repo_root=root,
                now=now,
            )
            active_run_id = str(state.get("active_run_id") or "").strip()
            claim_run_id = claim_run_id or active_run_id
            run_id = active_run_id or f"sj_{_utc_compact(now)}_cancel_{uuid.uuid4().hex[:6]}"
            active_claim = _active_claim_health(
                job_id,
                repo_root=root,
                max_runtime_seconds=int((spec.get("policy") or {}).get("max_runtime_seconds") or 1800),
                expected_run_id=active_run_id,
                now=now,
            )
            stop_proof_pending = bool(active_run_id) or (bool(active_claim.get("present")) and not bool(active_claim.get("stale")))
    requested_status = "cancel_requested" if stop_proof_pending else "cancelled"

    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "job_id": job_id,
        "run_id": run_id,
        "job_kind": str(spec.get("job_kind") or "single_turn"),
        "started_utc": str(state.get("active_started_utc") or _utc_iso(now)),
        "finished_utc": _utc_iso(now),
        "status": requested_status,
        "cancel_reason": str(reason or ""),
        "cancel_evidence": dict(evidence or {}),
        "stop_proof_pending": stop_proof_pending,
        "error": "",
    }

    receipt_path = (receipts_dir(root) / _safe_name(job_id) / f"{_safe_name(run_id)}.json").resolve()
    binding_result = _bind_scheduler_launch(
        spec=spec,
        run_id=run_id,
        trigger="operator",
        status=requested_status,
        reason=str(reason or "operator_cancel"),
        event=requested_status,
        receipt_path=str(receipt_path),
        report_path="",
        repo_root=root,
        reference_status="execution_ref_stale",
        reference_status_reason=str(reason or "operator_cancel"),
        writer="scheduler.operator.bind",
        launch_cause="operator_action",
    )
    if binding_result:
        receipt["binding_result"] = binding_result

    if stop_proof_pending:
        _write_json_atomic(receipt_path, receipt)
        requested_state = dict(state)
        requested_state["cancel_requested_utc"] = _utc_iso(now)
        requested_state["cancel_request_reason"] = str(reason or "")
        requested_state["cancel_request_run_id"] = run_id
        requested_state["cancel_request_receipt_path"] = str(receipt_path)
        requested_state["updated_utc"] = _utc_iso(now)
        _write_state(job_id, requested_state, repo_root=root)
    else:
        receipt["resource_claims"] = _release_scheduled_job_runtime_claim_with_receipt(
            job_id=job_id,
            run_id=claim_run_id or run_id,
            terminal_status="cancelled",
            detail=(
                f"runtime claim cancelled for scheduled job run {claim_run_id or run_id}; "
                f"operator_action=cancel; reason={str(reason or '')}"
            ),
            repo_root=root,
        )
        _write_json_atomic(receipt_path, receipt)
        new_state = dict(state)
        new_state.update(_blank_state_receipt_projection())
        new_state["active_run_id"] = ""
        new_state["active_started_utc"] = ""
        new_state["active_loop_iteration"] = 0
        new_state["active_loop_stage"] = ""
        new_state["active_loop_completed_iterations"] = 0
        new_state["active_loop_max_iterations"] = 0
        new_state["active_loop_updated_utc"] = ""
        new_state["last_run_id"] = run_id
        new_state["last_started_utc"] = str(receipt.get("started_utc") or "")
        new_state["last_status"] = "cancelled"
        new_state["last_finished_utc"] = _utc_iso(now)
        new_state["last_trigger"] = "operator"
        new_state["last_receipt_path"] = str(receipt_path)
        new_state["last_report_path"] = ""
        new_state["last_nonlaunch_reason"] = ""
        new_state["last_nonlaunch_status"] = ""
        new_state["last_nonlaunch_utc"] = ""
        new_state["last_nonlaunch_run_id"] = ""
        new_state["last_nonlaunch_receipt_path"] = ""
        new_state["last_nonlaunch_report_path"] = ""
        new_state["last_nonlaunch_signature"] = ""
        new_state["run_now_requested_utc"] = ""
        new_state["run_now_request_run_id"] = ""
        new_state["run_now_request_source"] = ""
        new_state["cancel_requested_utc"] = ""
        new_state["cancel_request_reason"] = ""
        new_state["cancel_request_run_id"] = ""
        new_state["cancel_request_receipt_path"] = ""
        new_state["updated_utc"] = _utc_iso(now)
        _write_state(job_id, new_state, repo_root=root)

    _append_event(
        repo_root=root,
        job_id=job_id,
        run_id=run_id,
        event=requested_status,
        trigger="operator",
        status=requested_status,
        details={
            "reason": str(reason or ""),
            "receipt_path": str(receipt_path),
            "stop_proof_pending": stop_proof_pending,
        },
    )

    return {
        "ok": True,
        "job_id": job_id,
        "run_id": run_id,
        "status": requested_status,
        "stop_proof_pending": stop_proof_pending,
        "receipt_path": str(receipt_path),
        "receipt": receipt,
    }


def _request_next_family_job_after_success(
    spec: Dict[str, Any],
    *,
    finished_utc: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    parent_uid = str(prompt_meta.get("generated_from_parent_uid") or "").strip()
    if not parent_uid:
        queue_cfg = (
            prompt_meta.get("queue_child_implementation_jobs")
            if isinstance(prompt_meta.get("queue_child_implementation_jobs"), dict)
            else {}
        )
        binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
        if bool(queue_cfg.get("enabled")) and str(binding.get("type") or "").strip() == "ledger_uid":
            parent_uid = str(binding.get("id") or "").strip()
    if not parent_uid:
        return {"ok": True, "requested": False, "reason": "no_family_parent_uid"}

    root = Path(repo_root or _repo_root()).resolve()
    now = _parse_utc(finished_utc)
    current_job_id = str(spec.get("job_id") or "").strip()
    candidates: List[Dict[str, Any]] = []
    for spec_path in jobs_dir(root).glob("*.json"):
        job_id = str(spec_path.stem or "").strip()
        if not job_id or job_id == current_job_id:
            continue
        try:
            candidate_spec = load_job(job_id, repo_root=root)
        except Exception:
            continue
        candidate_prompt_meta = (
            candidate_spec.get("prompt_meta") if isinstance(candidate_spec.get("prompt_meta"), dict) else {}
        )
        if str(candidate_prompt_meta.get("generated_from_parent_uid") or "").strip() != parent_uid:
            continue
        candidate_state = load_state(job_id, repo_root=root)
        if str(candidate_state.get("active_run_id") or "").strip():
            continue
        if str(candidate_state.get("run_now_requested_utc") or "").strip():
            continue
        due = due_status(candidate_spec, state=candidate_state, now=now, repo_root=root)
        if not bool(due.get("eligible")):
            continue
        candidates.append(
            {
                "job_id": job_id,
                "due_utc": str(due.get("due_utc") or ""),
                "reason": str(due.get("reason") or ""),
            }
        )
    candidates.sort(key=lambda row: (str(row.get("due_utc") or ""), str(row.get("job_id") or "")))
    if not candidates:
        return {"ok": True, "requested": False, "reason": "no_eligible_family_child", "parent_uid": parent_uid}

    chosen = candidates[0]
    request = request_run_now(
        str(chosen.get("job_id") or ""),
        repo_root=root,
        requested_utc=finished_utc,
        request_source="family_progress_handoff",
    )
    return {
        "ok": bool(request.get("ok")),
        "requested": bool(request.get("ok")),
        "parent_uid": parent_uid,
        "requested_job_id": str(chosen.get("job_id") or ""),
        "candidate_count": len(candidates),
        "request_result": dict(request),
    }


def _post_success_progress_handoff(
    spec: Dict[str, Any],
    *,
    finished_utc: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    execution_family_contract = (
        prompt_meta.get("execution_family_contract")
        if isinstance(prompt_meta.get("execution_family_contract"), dict)
        else {}
    )
    explicit_pass_chain = (
        prompt_meta.get("explicit_pass_chain")
        if isinstance(prompt_meta.get("explicit_pass_chain"), dict)
        else {}
    )
    execution_mode = str(
        execution_family_contract.get("execution_mode")
        or prompt_meta.get("execution_mode")
        or ""
    ).strip().lower()
    if explicit_pass_chain or execution_mode == "explicit_pass_chain_v1":
        return _request_next_family_job_after_success(
            spec,
            finished_utc=finished_utc,
            repo_root=repo_root,
        )
    ledger_truth = _ledger_bound_family_truth_projection(spec, repo_root=repo_root)
    if bool(ledger_truth.get("applies")) and not bool(ledger_truth.get("generated_by_reconcile")) and not bool(ledger_truth.get("ready")):
        parent_uid = str(ledger_truth.get("generated_from_parent_uid") or "").strip()
        reconcile = _queue_status_reconcile_job_for_uid(
            str(ledger_truth.get("ledger_uid") or ""),
            source_job_id=str(spec.get("job_id") or "").strip(),
            generated_from_parent_uid=parent_uid,
            repo_root=repo_root,
        )
        requested = {}
        reconcile_job_id = str(reconcile.get("reconcile_job_id") or "").strip()
        if bool(reconcile.get("ok")) and reconcile_job_id:
            requested = request_run_now(
                reconcile_job_id,
                repo_root=repo_root,
                requested_utc=finished_utc,
                request_source="ledger_truth_handoff",
            )
        return {
            "ok": bool(reconcile.get("ok")),
            "requested": bool(requested.get("ok")),
            "handoff_type": "ledger_truth_reconcile",
            "parent_uid": parent_uid,
            "ledger_uid": str(ledger_truth.get("ledger_uid") or ""),
            "ledger_missing_reasons": list(ledger_truth.get("missing_reasons") or []),
            "reconcile_job_id": reconcile_job_id,
            "reconcile_result": dict(reconcile),
            "request_result": dict(requested),
        }
    return _request_next_family_job_after_success(
        spec,
        finished_utc=finished_utc,
        repo_root=repo_root,
    )


def _scheduler_tick_rows(
    *,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
) -> List[Dict[str, Any]]:
    """Return the minimal per-job rows needed by scheduler_tick.

    The tick path only needs spec/state/due and dependency references. Avoid the
    heavyweight operator summary path so the in-process scheduler can keep
    advancing families even when broader watch surfaces are expensive.
    """
    root = Path(repo_root or _repo_root()).resolve()
    current_now = now or _utc_now()
    rows: List[Dict[str, Any]] = []
    for path in sorted(jobs_dir(root).glob("*.json"), key=lambda candidate: candidate.name):
        spec = _read_json(path)
        if not spec:
            continue
        spec = _persist_legacy_job_shape_migration_marker(spec, repo_root=root)
        job_id = str(spec.get("job_id") or "").strip()
        if not job_id:
            continue
        state = load_state(job_id, repo_root=root)
        active_recovery = _active_run_recovery_status(spec, state=state, now=current_now, repo_root=root)
        if bool(active_recovery.get("recover")):
            state = _apply_active_run_recovery(
                job_id=job_id,
                state=state,
                recovery=active_recovery,
                trigger="scheduler",
                repo_root=root,
                now=current_now,
            )
        due = due_status(spec, state=state, now=current_now, repo_root=root)
        rows.append(
            {
                "job_id": job_id,
                "enabled": bool(spec.get("enabled")),
                "schedule": dict(spec.get("schedule") or {}),
                "depends_on": list(spec.get("depends_on") or []),
                "state": dict(state),
                "due": dict(due),
            }
        )
    return rows


def _execute_project_runner_job(
    spec: Dict[str, Any],
    *,
    run_id: str,
    run_attempt: int,
    trigger: str,
    max_runtime_seconds: int,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    from experiments_shared import project_runner_contract as _project_runner_contract

    def _tail_runner_log(path: Path, *, max_bytes: int = 8000) -> str:
        try:
            if not path.exists():
                return ""
            with path.open("rb") as handle:
                handle.seek(0, os.SEEK_END)
                size = handle.tell()
                handle.seek(max(0, size - max_bytes))
                data = handle.read()
            if size > max_bytes:
                newline = data.find(b"\n")
                if newline != -1:
                    data = data[newline + 1 :]
            return data.decode("utf-8", errors="replace")
        except Exception:
            return ""

    root = Path(repo_root or _repo_root()).resolve()
    job_id = str(spec.get("job_id") or "").strip()
    started_utc = _utc_iso()
    runtime = _normalize_runtime(spec.get("runtime"), job_kind="project_runner")
    job_shape_fields = _resolve_job_shape_fields(spec)
    project_run = _project_runner_contract.normalize_project_run(
        spec.get("project_run"),
        repo_root=root,
    )
    scheduler_policy = project_run.get("scheduler_policy") if isinstance(project_run.get("scheduler_policy"), dict) else {}
    command_info = _project_runner_contract.build_project_run_command(project_run, repo_root=root)
    command = list(command_info.get("command") or [])
    command_display = shlex.join(command)
    runner_log_path = (reports_dir(root) / _safe_name(job_id) / f"{_safe_name(run_id)}__runner.log").resolve()
    runner_log_path.parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env.setdefault("PYTHONUNBUFFERED", "1")
    env["CAUSYL_PROJECT_RUN_JOB_ID"] = job_id
    env["CAUSYL_PROJECT_RUN_ID"] = run_id
    env["CAUSYL_PROJECT_ID"] = str(project_run.get("project_id") or "")
    env["CAUSYL_PROJECT_RUNNER_ID"] = str(project_run.get("runner_id") or "")
    env["CAUSYL_PROJECT_RUN_RESOLUTION_SOURCE"] = str(command_info.get("launch_resolution_source") or "")
    if str(command_info.get("contract_hash") or "").strip():
        env["CAUSYL_EXPERIMENT_CONTRACT_HASH"] = str(command_info.get("contract_hash") or "").strip()
    try:
        with runner_log_path.open("w", encoding="utf-8", newline="\n") as log_handle:
            proc = subprocess.Popen(
                command,
                cwd=str(command_info.get("cwd") or root),
                stdout=log_handle,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
            )
            try:
                return_code = int(proc.wait(timeout=max_runtime_seconds))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
                combined = _tail_runner_log(runner_log_path)
                receipt = {
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "job_id": job_id,
                    "run_id": run_id,
                    "job_kind": "project_runner",
                    "job_shape": str(job_shape_fields.get("job_shape") or ""),
                    "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
                    "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
                    "trigger": str(trigger or ""),
                    "status": "timed_out",
                    "error": f"project_runner_timeout:{max_runtime_seconds}",
                    "started_utc": started_utc,
                    "finished_utc": _utc_iso(),
                    "runtime": runtime,
                    "run_attempt": int(run_attempt),
                    "project_run": project_run,
                    "required_context_bundle": str(spec.get("required_context_bundle") or ""),
                    "additional_context_refs": list(spec.get("additional_context_refs") or []),
                    "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
                    "effective_context_refs": _effective_context_refs(spec),
                    "job_shape_context": _job_shape_context_obligation_projection(spec),
                    "scheduler_policy": dict(scheduler_policy),
                    "manifest_path": str(command_info.get("manifest_path") or ""),
                    "launch_definition_path": str(command_info.get("launch_definition_path") or ""),
                    "launch_resolution_source": str(command_info.get("launch_resolution_source") or ""),
                    "contract_hash": str(command_info.get("contract_hash") or ""),
                    "contract_projection": dict(command_info.get("contract_projection") or {}),
                    "configuration_roots": dict(command_info.get("configuration_roots") or {}),
                    "entrypoint": str(command_info.get("entrypoint") or ""),
                    "cwd": str(command_info.get("cwd") or ""),
                    "command": command,
                    "command_display": command_display,
                    "command_prefix": list(command_info.get("command_prefix") or []),
                    "description": str(command_info.get("description") or ""),
                    "exit_code": "",
                    "runner_log_path": _rel_repo_path(runner_log_path, repo_root=root),
                    "stdout_tail": combined[-8000:] if combined else "",
                    "execution_contract": {
                        "execution_owner": "external_process",
                        "execution_mode": "controller_only",
                        "controller_runtime": runtime,
                    },
                }
                return False, receipt, str(receipt.get("error") or "")
        combined = _tail_runner_log(runner_log_path)
        proc = None  # consumed
        ok = return_code == 0
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "job_id": job_id,
            "run_id": run_id,
            "job_kind": "project_runner",
            "job_shape": str(job_shape_fields.get("job_shape") or ""),
            "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
            "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
            "trigger": str(trigger or ""),
            "status": "ok" if ok else "error",
            "error": "" if ok else f"project_runner_exit_code:{return_code}",
            "started_utc": started_utc,
            "finished_utc": _utc_iso(),
            "runtime": runtime,
            "run_attempt": int(run_attempt),
            "project_run": project_run,
            "required_context_bundle": str(spec.get("required_context_bundle") or ""),
            "additional_context_refs": list(spec.get("additional_context_refs") or []),
            "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
            "effective_context_refs": _effective_context_refs(spec),
            "job_shape_context": _job_shape_context_obligation_projection(spec),
            "scheduler_policy": dict(scheduler_policy),
            "manifest_path": str(command_info.get("manifest_path") or ""),
            "launch_definition_path": str(command_info.get("launch_definition_path") or ""),
            "launch_resolution_source": str(command_info.get("launch_resolution_source") or ""),
            "contract_hash": str(command_info.get("contract_hash") or ""),
            "contract_projection": dict(command_info.get("contract_projection") or {}),
            "configuration_roots": dict(command_info.get("configuration_roots") or {}),
            "entrypoint": str(command_info.get("entrypoint") or ""),
            "cwd": str(command_info.get("cwd") or ""),
            "command": command,
            "command_display": command_display,
            "command_prefix": list(command_info.get("command_prefix") or []),
            "description": str(command_info.get("description") or ""),
            "exit_code": int(return_code),
            "runner_log_path": _rel_repo_path(runner_log_path, repo_root=root),
            "stdout_tail": (combined[-8000:] if combined else ""),
            "execution_contract": {
                "execution_owner": "external_process",
                "execution_mode": "controller_only",
                "controller_runtime": runtime,
            },
        }
        return ok, receipt, ""
    except Exception as exc:
        receipt = {
            "schema_version": RECEIPT_SCHEMA_VERSION,
            "job_id": job_id,
            "run_id": run_id,
            "job_kind": "project_runner",
            "job_shape": str(job_shape_fields.get("job_shape") or ""),
            "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
            "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
            "trigger": str(trigger or ""),
            "status": "error",
            "error": f"project_runner_launch_failed:{exc}",
            "started_utc": started_utc,
            "finished_utc": _utc_iso(),
            "runtime": runtime,
            "run_attempt": int(run_attempt),
            "project_run": project_run,
            "required_context_bundle": str(spec.get("required_context_bundle") or ""),
            "additional_context_refs": list(spec.get("additional_context_refs") or []),
            "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
            "effective_context_refs": _effective_context_refs(spec),
            "scheduler_policy": dict(scheduler_policy),
            "manifest_path": str(command_info.get("manifest_path") or ""),
            "launch_definition_path": str(command_info.get("launch_definition_path") or ""),
            "launch_resolution_source": str(command_info.get("launch_resolution_source") or ""),
            "contract_hash": str(command_info.get("contract_hash") or ""),
            "contract_projection": dict(command_info.get("contract_projection") or {}),
            "configuration_roots": dict(command_info.get("configuration_roots") or {}),
            "entrypoint": str(command_info.get("entrypoint") or ""),
            "cwd": str(command_info.get("cwd") or ""),
            "command": command,
            "command_display": command_display,
            "command_prefix": list(command_info.get("command_prefix") or []),
            "description": str(command_info.get("description") or ""),
            "exit_code": "",
            "runner_log_path": "",
            "stdout_tail": "",
            "execution_contract": {
                "execution_owner": "external_process",
                "execution_mode": "controller_only",
                "controller_runtime": runtime,
            },
        }
        return False, receipt, str(receipt.get("error") or "")


_PROJECT_RUNNER_REPEAT_EXIT_GUARD_THRESHOLD = 3
_BINDING_LAUNCH_REPEAT_GUARD_THRESHOLD = 3


def _binding_launch_repeat_identity(binding: Dict[str, Any]) -> Dict[str, str]:
    row = dict(binding or {})
    return {
        "type": str(row.get("type") or "").strip().lower(),
        "id": str(row.get("id") or "").strip(),
        "task_id": str(row.get("task_id") or "").strip(),
        "ledger_uid": str(row.get("ledger_uid") or "").strip(),
    }


def _binding_launch_repeat_guard(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    threshold: int = _BINDING_LAUNCH_REPEAT_GUARD_THRESHOLD,
) -> Dict[str, Any]:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    current_identity = _binding_launch_repeat_identity(binding)
    if not any(current_identity.values()):
        return {}
    job_id = str(spec.get("job_id") or "").strip()
    if not job_id:
        return {}
    limit = max(6, int(threshold) + 2)
    receipts = list_receipts(job_id, repo_root=repo_root, limit=limit)
    if not receipts:
        return {}
    repeat_count = 0
    latest_reason = ""
    latest_error = ""
    latest_run_ids: List[str] = []
    latest_receipt_paths: List[str] = []
    for row in receipts:
        receipt = dict(row or {})
        run_id = str(receipt.get("run_id") or "").strip()
        if run_id:
            receipt = load_receipt(job_id, run_id, repo_root=repo_root)
        if str(receipt.get("status") or "").strip().lower() != "blocked":
            break
        reason = str(receipt.get("reason") or "").strip()
        if not reason.startswith("binding_launch_failed"):
            break
        candidate_identity = _binding_launch_repeat_identity(
            receipt.get("binding") if isinstance(receipt.get("binding"), dict) else {}
        )
        if candidate_identity != current_identity:
            break
        if not latest_reason:
            latest_reason = reason
            latest_error = str(receipt.get("error") or "").strip()
        if reason != latest_reason:
            break
        if str(receipt.get("error") or "").strip() != latest_error:
            break
        repeat_count += 1
        if run_id:
            latest_run_ids.append(run_id)
        receipt_path = str(receipt.get("_receipt_path") or "").strip()
        if receipt_path:
            latest_receipt_paths.append(receipt_path)
    if repeat_count < int(threshold):
        return {}
    failure_detail = latest_reason.split(":", 1)[1].strip() if ":" in latest_reason else ""
    return {
        "blocked": True,
        "kind": "binding_launch_repeat_guard",
        "reason": f"binding_launch_repeat_guard:{failure_detail or 'unchanged'}",
        "repeat_count": repeat_count,
        "threshold": int(threshold),
        "latest_reason": latest_reason,
        "latest_error": latest_error,
        "job_id": job_id,
        "latest_run_ids": latest_run_ids[:limit],
        "latest_receipt_paths": latest_receipt_paths[:limit],
        "identity": current_identity,
        "summary": (
            f"{job_id} hit the same binding launch failure {repeat_count} time(s) for unchanged binding input; "
            "launches are now blocked until the binding changes or an operator forces a retry."
        ),
    }


def _project_runner_repeat_exit_identity(project_run: Dict[str, Any]) -> Dict[str, str]:
    row = dict(project_run or {})
    args = row.get("args") if isinstance(row.get("args"), dict) else {}
    identity = {
        "project_id": str(row.get("project_id") or "").strip(),
        "runner_id": str(row.get("runner_id") or "").strip(),
        "request_fingerprint": str(args.get("request_fingerprint") or "").strip(),
        "scenario_packet_path": str(args.get("scenario_packet_path") or "").strip(),
        "scheduler_packet_path": str(args.get("scheduler_packet_path") or "").strip(),
    }
    return identity


def _project_runner_repeat_exit_guard(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    threshold: int = _PROJECT_RUNNER_REPEAT_EXIT_GUARD_THRESHOLD,
) -> Dict[str, Any]:
    if _normalize_job_kind(spec.get("job_kind")) != "project_runner":
        return {}
    limit = max(6, int(threshold) + 2)
    project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
    current_identity = _project_runner_repeat_exit_identity(project_run)
    if not any(current_identity.values()):
        return {}
    job_id = str(spec.get("job_id") or "").strip()
    if not job_id:
        return {}
    receipts = list_receipts(job_id, repo_root=repo_root, limit=limit)
    if not receipts:
        return {}
    repeat_count = 0
    latest_error = ""
    latest_run_ids: List[str] = []
    latest_receipt_paths: List[str] = []
    for row in receipts:
        receipt = dict(row or {})
        run_id = str(receipt.get("run_id") or "").strip()
        if run_id:
            receipt = load_receipt(job_id, run_id, repo_root=repo_root)
        if str(receipt.get("status") or "").strip().lower() != "error":
            break
        error = str(receipt.get("error") or "").strip()
        if not error.startswith("project_runner_exit_code:"):
            break
        candidate_identity = _project_runner_repeat_exit_identity(
            receipt.get("project_run") if isinstance(receipt.get("project_run"), dict) else {}
        )
        if candidate_identity != current_identity:
            break
        if not latest_error:
            latest_error = error
        if error != latest_error:
            break
        repeat_count += 1
        if run_id:
            latest_run_ids.append(run_id)
        receipt_path = str(receipt.get("_receipt_path") or "").strip()
        if receipt_path:
            latest_receipt_paths.append(receipt_path)
    if repeat_count < int(threshold):
        return {}
    exit_code = latest_error.split(":", 1)[1].strip() if ":" in latest_error else ""
    return {
        "blocked": True,
        "kind": "project_runner_repeat_exit_guard",
        "reason": f"project_runner_repeat_exit_guard:{exit_code or 'unknown'}",
        "repeat_count": repeat_count,
        "threshold": int(threshold),
        "exit_code": exit_code,
        "latest_error": latest_error,
        "job_id": job_id,
        "latest_run_ids": latest_run_ids[:limit],
        "latest_receipt_paths": latest_receipt_paths[:limit],
        "identity": current_identity,
        "summary": (
            f"{job_id} hit the same project_runner exit code {repeat_count} time(s) for unchanged runner input; "
            "launches are now blocked until the workflow input changes or an operator forces a retry."
        ),
    }


def _get_dispatch_pool(*, max_workers: int = 3) -> concurrent.futures.ThreadPoolExecutor:
    """Get or create the job dispatch thread pool."""
    global _DISPATCH_POOL, _DISPATCH_POOL_MAX_WORKERS
    with _DISPATCH_GUARD:
        normalized_max_workers = max(1, min(20, int(max_workers)))
        if _DISPATCH_POOL is not None and _DISPATCH_POOL_MAX_WORKERS != normalized_max_workers:
            _DISPATCH_POOL.shutdown(wait=False)
            _DISPATCH_POOL = None
        if _DISPATCH_POOL is None:
            _DISPATCH_POOL = concurrent.futures.ThreadPoolExecutor(
                max_workers=normalized_max_workers,
                thread_name_prefix="job_dispatch_",
            )
            _DISPATCH_POOL_MAX_WORKERS = normalized_max_workers
        return _DISPATCH_POOL


def _cleanup_dispatch_tracking_locked() -> None:
    """Drop completed dispatch futures and their cached role metadata."""
    to_remove = []
    for job_id, future in _DISPATCH_ACTIVE_JOBS.items():
        if future.done():
            to_remove.append(job_id)
            continue
        meta = _DISPATCH_ACTIVE_JOB_META.get(job_id) if isinstance(_DISPATCH_ACTIVE_JOB_META.get(job_id), dict) else {}
        submitted_utc = str(meta.get("submitted_utc") or "").strip()
        if submitted_utc:
            try:
                submitted_dt = _parse_utc(submitted_utc)
                if (_utc_now() - submitted_dt).total_seconds() < int(_DISPATCH_ORPHAN_GRACE_SECONDS):
                    continue
            except Exception:
                pass
        try:
            spec = load_job(job_id)
        except Exception:
            to_remove.append(job_id)
            continue
        claim_path = (locks_dir() / f"{_safe_name(job_id)}.lock.json").resolve()
        policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
        if claim_path.exists() and (
            not _claim_is_stale(claim_path, max_runtime_seconds=int(policy.get("max_runtime_seconds") or 1800))
        ):
            continue
        try:
            state = load_state(job_id)
        except Exception:
            to_remove.append(job_id)
            continue
        if (
            not str(state.get("active_run_id") or "").strip()
            and str(state.get("last_status") or "").strip().lower() in {"ok", "succeeded", "verified", "completed"}
        ):
            try:
                _release_job_claim(
                    job_id,
                    terminal_status=str(state.get("last_status") or ""),
                    detail="terminal_state_reconciliation_cleanup",
                )
            except Exception:
                pass
            to_remove.append(job_id)
            continue
        if str(state.get("active_run_id") or "").strip():
            recovery = _active_run_recovery_status(spec, state=state, now=_utc_now())
            if not bool(recovery.get("recover")):
                continue
        to_remove.append(job_id)
    for job_id in to_remove:
        del _DISPATCH_ACTIVE_JOBS[job_id]
        _DISPATCH_ACTIVE_JOB_ROLES.pop(job_id, None)
        _DISPATCH_ACTIVE_JOB_META.pop(job_id, None)


def _get_active_job_count() -> int:
    """Count currently active jobs in the dispatch pool."""
    with _DISPATCH_GUARD:
        _cleanup_dispatch_tracking_locked()
        return len(_DISPATCH_ACTIVE_JOBS)

def _dispatch_future_active(job_id: str) -> bool:
    """Return True when a job is already submitted in the dispatch pool."""
    if not str(job_id or "").strip():
        return False
    with _DISPATCH_GUARD:
        _cleanup_dispatch_tracking_locked()
        future = _DISPATCH_ACTIVE_JOBS.get(str(job_id))
        if future is None:
            return False
        return not future.done()


def _active_job_claims(*, repo_root: Optional[Path] = None, exclude_job_id: str = "") -> List[str]:
    root = Path(repo_root or _repo_root()).resolve()
    claims: List[str] = []
    for claim_path in locks_dir(root).glob("*.lock.json"):
        try:
            row = _read_json(claim_path)
            if not row:
                continue
            job_id = str(row.get("job_id") or "").strip()
            if not job_id or job_id == exclude_job_id:
                continue
            claim_run_id = str(row.get("run_id") or "").strip()
            spec = load_job(job_id, repo_root=root)
            state = load_state(job_id, repo_root=root)
            state_active_run_id = str(state.get("active_run_id") or "").strip()
            if not state_active_run_id:
                _reconcile_terminal_claim_before_launch(job_id, repo_root=root)
                continue
            if claim_run_id and state_active_run_id != claim_run_id:
                continue
            policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
            if _claim_is_stale(claim_path, max_runtime_seconds=int(policy.get("max_runtime_seconds") or 1800)):
                continue
            claims.append(job_id)
        except Exception:
            continue
    return claims


def _runtime_policy_raw_value(
    key: str,
    *,
    env_names: tuple[str, ...] = (),
    repo_root: Optional[Path] = None,
) -> Tuple[str, str]:
    for env_name in env_names:
        raw = str(os.environ.get(env_name, "") or "").strip()
        if raw:
            return raw, f"environment:{env_name}"
    policy_map: Dict[str, Any]
    if repo_root is not None:
        policy_path = (Path(repo_root).resolve() / "config" / "runtime_policy.properties").resolve()
        policy_map = _runtime_policy._load_properties(policy_path)
    else:
        policy_map = _runtime_policy.load_runtime_policy()
    if key in {"scheduler.role_concurrency_caps", "scheduler.resource_class_caps"}:
        raw_csv = str(policy_map.get(key, "") or "").strip()
        if not raw_csv:
            raw_csv = ",".join(
                str(item or "").strip()
                for item in _runtime_policy.get_runtime_policy_csv(key, "")
                if str(item or "").strip()
            )
        if raw_csv:
            return raw_csv, "runtime_policy"
    raw = str(policy_map.get(key, "") or "").strip()
    if raw:
        return raw, "runtime_policy"
    return "", "default"


def _parse_named_positive_int_caps(
    raw: str,
    *,
    field_name: str,
    allowed_names: Optional[set[str]] = None,
) -> Tuple[Dict[str, int], List[str]]:
    out: Dict[str, int] = {}
    errors: List[str] = []
    for item in str(raw or "").split(","):
        entry = str(item or "").strip()
        if not entry:
            continue
        if ":" in entry:
            name, _, value = entry.partition(":")
        elif "=" in entry:
            name, _, value = entry.partition("=")
        else:
            errors.append(f"{field_name}:invalid_format:{entry}")
            continue
        normalized_name = str(name or "").strip().lower()
        if not normalized_name:
            errors.append(f"{field_name}:missing_name:{entry}")
            continue
        if allowed_names is not None and normalized_name not in allowed_names:
            errors.append(f"{field_name}:unknown_name:{normalized_name}")
            continue
        if normalized_name in out:
            errors.append(f"{field_name}:duplicate_name:{normalized_name}")
            continue
        try:
            parsed = int(str(value or "").strip())
        except Exception:
            errors.append(f"{field_name}:invalid_int:{entry}")
            continue
        if parsed <= 0:
            errors.append(f"{field_name}:non_positive:{entry}")
            continue
        out[normalized_name] = parsed
    return out, errors


def current_runtime_resource_policy(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    current_global_cap = int(_DISPATCH_CONFIG.get("global_concurrency_cap", 1) or 1)
    current_role_caps = dict(_DISPATCH_CONFIG.get("per_role_cap") or _DEFAULT_ROLE_CONCURRENCY_CAPS)
    current_resource_caps = dict(_DISPATCH_CONFIG.get("resource_class_caps") or {})
    global_cap_raw, global_cap_source = _runtime_policy_raw_value(
        "scheduler.global_concurrency_cap",
        env_names=("EIEL_SCHEDULER_GLOBAL_CONCURRENCY_CAP",),
        repo_root=repo_root,
    )
    role_caps_raw, role_caps_source = _runtime_policy_raw_value(
        "scheduler.role_concurrency_caps",
        env_names=("EIEL_SCHEDULER_ROLE_CONCURRENCY_CAPS",),
        repo_root=repo_root,
    )
    resource_caps_raw, resource_caps_source = _runtime_policy_raw_value(
        "scheduler.resource_class_caps",
        env_names=("EIEL_SCHEDULER_RESOURCE_CLASS_CAPS",),
        repo_root=repo_root,
    )

    errors: List[str] = []
    global_cap = current_global_cap
    if global_cap_raw:
        try:
            parsed_global_cap = int(global_cap_raw)
            if parsed_global_cap <= 0:
                raise ValueError("non_positive")
            global_cap = parsed_global_cap
        except Exception:
            errors.append(f"scheduler.global_concurrency_cap:invalid_int:{global_cap_raw}")

    role_caps = dict(current_role_caps)
    if role_caps_raw:
        parsed_role_caps, role_errors = _parse_named_positive_int_caps(
            role_caps_raw,
            field_name="scheduler.role_concurrency_caps",
            allowed_names=set(_DEFAULT_ROLE_CONCURRENCY_CAPS.keys()),
        )
        errors.extend(role_errors)
        if not role_errors:
            role_caps = {**dict(_DEFAULT_ROLE_CONCURRENCY_CAPS), **parsed_role_caps}

    resource_class_caps: Dict[str, int] = dict(current_resource_caps)
    if resource_caps_raw:
        parsed_resource_caps, resource_errors = _parse_named_positive_int_caps(
            resource_caps_raw,
            field_name="scheduler.resource_class_caps",
            allowed_names=None,
        )
        errors.extend(resource_errors)
        if not resource_errors:
            resource_class_caps = parsed_resource_caps

    if errors:
        return {
            "ok": False,
            "policy_valid": False,
            "fallback_to_last_known_good": True,
            "global_concurrency_cap": int(_DISPATCH_CONFIG.get("global_concurrency_cap", 1) or 1),
            "per_role_cap": dict(_DISPATCH_CONFIG.get("per_role_cap") or _DEFAULT_ROLE_CONCURRENCY_CAPS),
            "resource_class_caps": dict(_DISPATCH_CONFIG.get("resource_class_caps") or {}),
            "policy_source": str(_DISPATCH_CONFIG.get("policy_source") or "last_known_good"),
            "validation_errors": list(errors),
            "effective_scope": "future_work_started_after_refresh",
            "sources": {
                "global_concurrency_cap": global_cap_source,
                "role_concurrency_caps": role_caps_source,
                "resource_class_caps": resource_caps_source,
            },
        }

    _DISPATCH_CONFIG["global_concurrency_cap"] = int(global_cap)
    _DISPATCH_CONFIG["per_role_cap"] = dict(role_caps)
    _DISPATCH_CONFIG["resource_class_caps"] = dict(resource_class_caps)
    _DISPATCH_CONFIG["policy_source"] = "runtime_policy" if any(
        source != "default"
        for source in (global_cap_source, role_caps_source, resource_caps_source)
    ) else "defaults"
    _DISPATCH_CONFIG["policy_valid"] = True
    _DISPATCH_CONFIG["validation_errors"] = []

    return {
        "ok": True,
        "policy_valid": True,
        "fallback_to_last_known_good": False,
        "global_concurrency_cap": int(global_cap),
        "per_role_cap": dict(role_caps),
        "resource_class_caps": dict(resource_class_caps),
        "policy_source": str(_DISPATCH_CONFIG.get("policy_source") or "defaults"),
        "validation_errors": [],
        "effective_scope": "future_work_started_after_refresh",
        "sources": {
            "global_concurrency_cap": global_cap_source,
            "role_concurrency_caps": role_caps_source,
            "resource_class_caps": resource_caps_source,
        },
    }


def _scheduler_resource_class_caps(
    *,
    runtime_policy_snapshot: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, int]:
    snapshot = (
        runtime_policy_snapshot
        if isinstance(runtime_policy_snapshot, dict)
        else current_runtime_resource_policy(repo_root=repo_root)
    )
    return dict(snapshot.get("resource_class_caps") or {})


def _active_project_runner_specs(*, repo_root: Optional[Path] = None, exclude_job_id: str = "") -> List[Dict[str, Any]]:
    active_job_ids = set(_active_job_claims(repo_root=repo_root, exclude_job_id=exclude_job_id))
    with _DISPATCH_GUARD:
        _cleanup_dispatch_tracking_locked()
        for active_job_id, future in _DISPATCH_ACTIVE_JOBS.items():
            if future.done():
                continue
            if str(active_job_id or "").strip() and active_job_id != exclude_job_id:
                active_job_ids.add(str(active_job_id))

    specs: List[Dict[str, Any]] = []
    for active_job_id in sorted(active_job_ids):
        try:
            other_spec = load_job(active_job_id, repo_root=repo_root)
        except Exception:
            continue
        if _normalize_job_kind(other_spec.get("job_kind")) == "project_runner":
            specs.append(other_spec)
    return specs


def _project_runner_claimant_uid(job_id: str) -> str:
    return f"scheduled_job:{str(job_id or '').strip()}"


def _project_runner_claim_uid(job_id: str, resource: Mapping[str, Any]) -> str:
    return f"{_project_runner_claimant_uid(job_id)}:{_sha256_text(str((resource or {}).get('canonical_key') or ''))[:12]}"


def _project_runner_runtime_claim_uid(job_id: str, run_id: str, resource: Mapping[str, Any]) -> str:
    return (
        f"{_project_runner_claimant_uid(job_id)}:{str(run_id or '').strip()}:"
        f"{_sha256_text(str((resource or {}).get('canonical_key') or ''))[:12]}"
    )


def _project_runner_claim_resources(spec: Mapping[str, Any]) -> List[Dict[str, Any]]:
    if _normalize_job_kind(spec.get("job_kind")) != "project_runner":
        return []
    project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
    contract_projection = (
        project_run.get("contract_projection")
        if isinstance(project_run.get("contract_projection"), dict)
        else {}
    )
    shared_resource_contract = (
        contract_projection.get("shared_resource_contract")
        if isinstance(contract_projection.get("shared_resource_contract"), dict)
        else {}
    )
    resources: List[Dict[str, Any]] = []
    for row in list(shared_resource_contract.get("claim_keys") or []):
        if not isinstance(row, dict):
            continue
        if not str(row.get("canonical_key") or "").strip():
            continue
        resources.append(dict(row))
    return resources


def _record_project_runner_runtime_claim_acquired(
    *,
    spec: Mapping[str, Any],
    run_id: str,
    now_utc: str,
    repo_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    job_id = str(spec.get("job_id") or "").strip()
    claims: List[Dict[str, Any]] = []
    for resource in _project_runner_claim_resources(spec):
        canonical_key_digest = _sha256_text(str(resource.get("canonical_key") or ""))[:8]
        claim_uid = _project_runner_runtime_claim_uid(job_id, run_id, resource)
        entry_uid = f"{run_id}:acquired:{canonical_key_digest}"
        existing_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=repo_root)
        if existing_state is not None:
            if (
                str(existing_state.get("status") or "") == "acquired"
                and str(existing_state.get("latest_entry_uid") or "") == entry_uid
            ):
                record = {
                    "entry_uid": str(existing_state.get("latest_entry_uid") or entry_uid),
                    "recorded_at_utc": str(existing_state.get("latest_recorded_at_utc") or now_utc),
                    "source": "scheduler.runtime_claim",
                }
            else:
                raise ValueError(f"project runner runtime claim already exists for {claim_uid}")
        else:
            write_result = _resource_claim_ledger.append_claim_record_once(
                {
                    "schema_version": _resource_claim_ledger.RECORD_SCHEMA_VERSION,
                    "entry_uid": entry_uid,
                    "claim_uid": claim_uid,
                    "claimant_uid": _project_runner_claimant_uid(job_id),
                    "resource": dict(resource),
                    "recorded_at_utc": now_utc,
                    "status": "acquired",
                    "wait_reason_code": "",
                    "detail": f"runtime acquired shared resource for project runner run {run_id}",
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
                raise ValueError(f"project runner runtime claim acquire did not persist cleanly for {claim_uid}")
        claims.append(
            {
                "claim_uid": claim_uid,
                "resource": dict(resource),
                "acquired_entry_uid": str(record.get("entry_uid") or ""),
                "acquired_at_utc": str(record.get("recorded_at_utc") or now_utc),
                "acquired_source": str(record.get("source") or ""),
            }
        )
    return claims


def _release_scheduled_job_runtime_claim_with_receipt(
    *,
    job_id: str,
    run_id: str,
    terminal_status: str,
    detail: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    claim_uid = _scheduled_job_runtime_claim_uid(job_id, run_id)
    claim_row: Dict[str, Any] = {
        "claim_uid": claim_uid,
        "resource": _scheduled_job_runtime_claim_resource(),
        "acquired_entry_uid": "",
        "acquired_at_utc": "",
        "acquired_source": "",
        "terminal_entry_uid": "",
        "terminal_at_utc": "",
        "terminal_status": "",
        "terminal_source": "",
    }
    existing_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=repo_root)
    if existing_state is not None:
        if str(existing_state.get("status") or "") == "acquired":
            claim_row["acquired_entry_uid"] = str(existing_state.get("latest_entry_uid") or "")
            claim_row["acquired_at_utc"] = str(existing_state.get("latest_recorded_at_utc") or "")
            claim_row["acquired_source"] = str(existing_state.get("latest_source") or "")
        elif str(existing_state.get("status") or "") in {"released", "cancelled"}:
            claim_row["terminal_entry_uid"] = str(existing_state.get("latest_entry_uid") or "")
            claim_row["terminal_at_utc"] = str(existing_state.get("latest_recorded_at_utc") or "")
            claim_row["terminal_status"] = str(existing_state.get("status") or "")
            claim_row["terminal_source"] = str(existing_state.get("latest_source") or "")

    release_error = ""
    try:
        release_result = _release_job_claim(
            job_id,
            repo_root=repo_root,
            run_id=run_id,
            terminal_status=terminal_status,
            detail=detail,
        )
    except Exception as exc:
        release_result = {"released": False, "reason": str(exc), "record": {}}
        release_error = str(exc)
    record = dict(release_result.get("record") or {})
    if record:
        claim_row["terminal_entry_uid"] = str(record.get("entry_uid") or "")
        claim_row["terminal_at_utc"] = str(record.get("recorded_at_utc") or "")
        claim_row["terminal_status"] = str(record.get("status") or "")
        claim_row["terminal_source"] = str(record.get("source") or "")
    elif (
        not release_error
        and str(release_result.get("reason") or "") == "missing_lock"
        and str(claim_row.get("terminal_status") or "") in {"released", "cancelled"}
    ):
        release_error = ""
    elif not release_error:
        release_error = str(release_result.get("reason") or "release_record_missing")

    claims = [claim_row] if any(str(claim_row.get(key) or "") for key in ("acquired_entry_uid", "terminal_entry_uid", "claim_uid")) else []
    return {
        "claimant_uid": _scheduled_job_runtime_claimant_uid(job_id),
        "run_id": run_id,
        "claims": claims,
        "release_error": release_error,
    }


def _reconcile_terminal_job_claim(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
    terminal_status: str = "",
    detail: str = "",
) -> Dict[str, Any]:
    """Best-effort lock cleanup when durable state is already terminal."""
    try:
        state = load_state(job_id, repo_root=repo_root)
    except Exception as exc:
        return {"ok": False, "reason": f"state_load_failed:{exc}"}
    if str(state.get("active_run_id") or "").strip():
        return {"ok": False, "reason": "active_run_present"}
    status = str(terminal_status or state.get("last_status") or "").strip()
    if status.lower() not in {"ok", "succeeded", "verified", "completed", "failed", "failed_verification", "error", "cancelled", "interrupted"}:
        return {"ok": False, "reason": "non_terminal_state"}
    try:
        result = _release_job_claim(
            job_id,
            repo_root=repo_root,
            terminal_status=status,
            detail=detail or "terminal_state_reconciliation_cleanup",
        )
    except Exception as exc:
        return {"ok": False, "reason": f"release_failed:{exc}"}
    if bool(result.get("released")):
        return {"ok": True, "released": True, "record": dict(result.get("record") or {})}
    if str(result.get("reason") or "") == "missing_lock":
        return {"ok": True, "released": False, "reason": "missing_lock"}
    return {"ok": False, "released": False, "reason": str(result.get("reason") or "")}


def _reconcile_terminal_claim_before_launch(
    job_id: str,
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Clear a leftover claim when durable state already says the job is terminal.

    This runs in scheduler tick before classifying a job as claimed elsewhere.
    It protects operational continuity when an older lock survives after a newer
    terminal run has already written durable state.
    """
    try:
        state = load_state(job_id, repo_root=repo_root)
    except Exception as exc:
        return {"ok": False, "reason": f"state_load_failed:{exc}"}
    if str(state.get("active_run_id") or "").strip():
        return {"ok": False, "reason": "active_run_present"}
    status = str(state.get("last_status") or "").strip()
    if status.lower() not in {"ok", "succeeded", "verified", "completed", "failed", "failed_verification", "error", "cancelled", "interrupted"}:
        return {"ok": False, "reason": "non_terminal_state"}
    detail = (
        f"prelaunch terminal claim reconciliation for {job_id}; "
        f"last_status={status}; last_run_id={str(state.get('last_run_id') or '')}"
    )
    result = _reconcile_terminal_job_claim(
        job_id,
        repo_root=repo_root,
        terminal_status=status,
        detail=detail,
    )
    if bool(result.get("ok")) and bool(result.get("released")):
        try:
            _append_event(
                repo_root=repo_root,
                job_id=job_id,
                run_id=str(state.get("last_run_id") or ""),
                event="terminal_claim_reconciled_prelaunch",
                trigger="scheduler",
                status=status,
                details={"reason": "scheduler_tick_terminal_claim_reconciliation"},
            )
        except Exception:
            pass
    return result


def _record_project_runner_runtime_claim_release(
    *,
    spec: Mapping[str, Any],
    run_id: str,
    terminal_status: str,
    recorded_at_utc: str,
    detail: str = "",
    repo_root: Optional[Path] = None,
) -> List[Dict[str, Any]]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    job_id = str(spec.get("job_id") or "").strip()
    status_lower = str(terminal_status or "").strip().lower()
    claim_status = "cancelled" if status_lower == "cancelled" else "released"
    claims: List[Dict[str, Any]] = []
    for resource in _project_runner_claim_resources(spec):
        canonical_key_digest = _sha256_text(str(resource.get("canonical_key") or ""))[:8]
        claim_uid = _project_runner_runtime_claim_uid(job_id, run_id, resource)
        entry_uid = f"{run_id}:{claim_status}:{canonical_key_digest}"
        existing_state = _resource_claim_ledger.current_claim_state(claim_uid, repo_root=repo_root)
        if existing_state is None:
            raise ValueError(f"project runner runtime claim release requires prior acquired state: {claim_uid}")
        if str(existing_state.get("status") or "") != "acquired":
            if (
                str(existing_state.get("status") or "") == claim_status
                and str(existing_state.get("latest_entry_uid") or "") == entry_uid
            ):
                record = {
                    "entry_uid": str(existing_state.get("latest_entry_uid") or entry_uid),
                    "recorded_at_utc": str(existing_state.get("latest_recorded_at_utc") or recorded_at_utc),
                    "source": "scheduler.runtime_claim",
                }
            else:
                raise ValueError(f"project runner runtime claim release requires prior acquired state: {claim_uid}")
        else:
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
                    "claimant_uid": _project_runner_claimant_uid(job_id),
                    "resource": dict(resource),
                    "recorded_at_utc": normalized_recorded_at_utc,
                    "status": claim_status,
                    "wait_reason_code": "",
                    "detail": detail or f"runtime {claim_status} shared resource for project runner run {run_id}",
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
                raise ValueError(f"project runner runtime claim release did not persist cleanly for {claim_uid}")
        claims.append(
            {
                "claim_uid": claim_uid,
                "resource": dict(resource),
                "terminal_entry_uid": str(record.get("entry_uid") or ""),
                "terminal_at_utc": str(record.get("recorded_at_utc") or normalized_recorded_at_utc),
                "terminal_status": claim_status,
                "terminal_source": str(record.get("source") or ""),
            }
        )
    return claims


def _project_runner_job_id_from_claimant_uid(claimant_uid: str) -> str:
    text = str(claimant_uid or "").strip()
    prefix = "scheduled_job:"
    if not text.startswith(prefix):
        return ""
    return text[len(prefix):].strip()


def _project_runner_runtime_claim_run_id(claim_uid: str, claimant_uid: str) -> str:
    prefix = f"{str(claimant_uid or '').strip()}:"
    text = str(claim_uid or "").strip()
    if not text.startswith(prefix):
        return ""
    remainder = text[len(prefix):]
    parts = remainder.split(":")
    if len(parts) < 2:
        return ""
    return str(parts[0] or "").strip()


def _append_shared_resource_recovery_record(
    *,
    claim_state: Mapping[str, Any],
    recorded_at_utc: str,
    action: str,
    reason_code: str,
    detail: str,
    source: str,
    evidence: Mapping[str, Any] | None = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    recovery_uid = (
        f"{str(claim_state.get('claim_uid') or '').strip()}:"
        f"{str(claim_state.get('latest_entry_uid') or '').strip()}:"
        f"{str(action or '').strip()}:"
        f"{str(reason_code or '').strip()}"
    )
    result = _resource_claim_ledger.append_recovery_record_once(
        {
            "schema_version": _resource_claim_ledger.RECOVERY_RECORD_SCHEMA_VERSION,
            "recovery_uid": recovery_uid,
            "claim_uid": str(claim_state.get("claim_uid") or ""),
            "claimant_uid": str(claim_state.get("claimant_uid") or ""),
            "resource": dict(claim_state.get("resource") or {}),
            "recorded_at_utc": recorded_at_utc,
            "action": action,
            "reason_code": reason_code,
            "detail": detail,
            "source": source,
            "evidence": dict(evidence or {}),
        },
        repo_root=repo_root,
    )
    return dict(result.get("record") or {})


def _reconcile_stale_project_runner_resource_claims(
    *,
    resource: Mapping[str, Any],
    now_utc: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    released_claim_uids: List[str] = []
    quarantined_claim_uids: List[str] = []
    errors: List[Dict[str, Any]] = []
    now = _parse_utc(now_utc)
    occupancy = _resource_claim_ledger.list_claim_states_for_resource_lenient(resource, repo_root=repo_root)
    errors.extend(list(occupancy.get("errors") or []))
    for claim_state in list(occupancy.get("states") or []):
        if str(claim_state.get("status") or "") != "acquired":
            continue
        claimant_uid = str(claim_state.get("claimant_uid") or "")
        job_id = _project_runner_job_id_from_claimant_uid(claimant_uid)
        run_id = _project_runner_runtime_claim_run_id(str(claim_state.get("claim_uid") or ""), claimant_uid)
        if not job_id or not run_id:
            continue
        try:
            state = load_state(job_id, repo_root=repo_root)
            claim_spec = load_job(job_id, repo_root=repo_root)
        except Exception as exc:
            errors.append({"claim_uid": str(claim_state.get("claim_uid") or ""), "error": str(exc)})
            continue
        claim_policy = _normalize_policy(
            claim_spec.get("policy"),
            job_kind=_normalize_job_kind(claim_spec.get("job_kind")),
        )
        claim_health = _active_claim_health(
            job_id,
            repo_root=repo_root,
            expected_run_id=run_id,
            max_runtime_seconds=int(claim_policy.get("max_runtime_seconds") or 1800),
            now=now,
        )
        state_active_run_id = str(state.get("active_run_id") or "").strip()
        state_last_status = str(state.get("last_status") or "").strip().lower()
        evidence = {
            "state_active_run_id": state_active_run_id,
            "state_last_status": state_last_status,
            "claim_health": dict(claim_health),
        }
        detail = (
            f"reconciled stale shared-resource claim for {job_id} run {run_id}; "
            f"state={state_last_status or 'unknown'}; "
            f"claim_reason={str(claim_health.get('reason') or 'unknown')}"
        )
        safe_to_release = False
        reason_code = ""
        if str(claim_health.get("reason") or "") == "run_id_mismatch":
            safe_to_release = True
            reason_code = "run_id_mismatch"
        elif state_active_run_id and state_active_run_id != run_id:
            safe_to_release = True
            reason_code = "run_id_mismatch"
        elif state_last_status and state_last_status != "running":
            safe_to_release = True
            reason_code = "state_not_running"
        elif bool(claim_health.get("stale")) and claim_health.get("pid_alive") is False:
            safe_to_release = True
            reason_code = "stale_pid_dead"
        if safe_to_release:
            release_detail = detail + "; capacity released by scheduler recovery"
            try:
                _record_project_runner_runtime_claim_release(
                    spec={"job_id": job_id, "job_kind": "project_runner", "project_run": {"contract_projection": {"shared_resource_contract": {"claim_keys": [dict(resource)]}}}},
                    run_id=run_id,
                    terminal_status="released",
                    recorded_at_utc=now_utc,
                    detail=release_detail,
                    repo_root=repo_root,
                )
                _append_shared_resource_recovery_record(
                    claim_state=claim_state,
                    recorded_at_utc=now_utc,
                    action="released",
                    reason_code=reason_code,
                    detail=release_detail,
                    source="scheduler.resource_recovery",
                    evidence=evidence,
                    repo_root=repo_root,
                )
                released_claim_uids.append(str(claim_state.get("claim_uid") or ""))
            except Exception as exc:
                errors.append({"claim_uid": str(claim_state.get("claim_uid") or ""), "error": str(exc)})
            continue

        if not bool(claim_health.get("stale")):
            continue
        quarantine_reason = str(claim_health.get("reason") or "unresolved")
        quarantine_detail = (
            f"shared-resource recovery left claim blocked because live work could not be disproven; "
            f"job_id={job_id}; run_id={run_id}; claim_reason={quarantine_reason}"
        )
        try:
            _append_shared_resource_recovery_record(
                claim_state=claim_state,
                recorded_at_utc=now_utc,
                action="quarantined",
                reason_code="manual_review_required",
                detail=quarantine_detail,
                source="scheduler.resource_recovery",
                evidence=evidence,
                repo_root=repo_root,
            )
            quarantined_claim_uids.append(str(claim_state.get("claim_uid") or ""))
        except Exception as exc:
            errors.append({"claim_uid": str(claim_state.get("claim_uid") or ""), "error": str(exc)})
    return {
        "released_claim_uids": released_claim_uids,
        "quarantined_claim_uids": quarantined_claim_uids,
        "errors": errors,
    }


def _project_runner_active_count_for_resource(
    spec: Dict[str, Any],
    *,
    resource: Mapping[str, Any],
    repo_root: Optional[Path] = None,
) -> int:
    resource_class = str(resource.get("resource_class") or "").strip().lower()
    if resource_class == "job_family":
        project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
        scheduler_policy = project_run.get("scheduler_policy") if isinstance(project_run.get("scheduler_policy"), dict) else {}
        expected_class = str(scheduler_policy.get("resource_class") or "").strip().lower()
        active_matches = 0
        for other_spec in _active_project_runner_specs(repo_root=repo_root, exclude_job_id=str(spec.get("job_id") or "")):
            other_project_run = other_spec.get("project_run") if isinstance(other_spec.get("project_run"), dict) else {}
            other_policy = other_project_run.get("scheduler_policy") if isinstance(other_project_run.get("scheduler_policy"), dict) else {}
            if str(other_policy.get("resource_class") or "").strip().lower() == expected_class:
                active_matches += 1
        return active_matches
    if resource_class == "project":
        project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
        scheduler_policy = project_run.get("scheduler_policy") if isinstance(project_run.get("scheduler_policy"), dict) else {}
        semaphore_key = str(scheduler_policy.get("project_semaphore") or "").strip().lower()
        active_matches = 0
        for active_job_id in _active_job_claims(repo_root=repo_root, exclude_job_id=str(spec.get("job_id") or "")):
            try:
                other_spec = load_job(active_job_id, repo_root=repo_root)
            except Exception:
                continue
            if _normalize_job_kind(other_spec.get("job_kind")) != "project_runner":
                continue
            other_project_run = other_spec.get("project_run") if isinstance(other_spec.get("project_run"), dict) else {}
            other_policy = other_project_run.get("scheduler_policy") if isinstance(other_project_run.get("scheduler_policy"), dict) else {}
            if str(other_policy.get("project_semaphore") or "").strip().lower() == semaphore_key:
                active_matches += 1
        return active_matches
    return 0


def _record_project_runner_resource_claim_decision(
    *,
    job_id: str,
    run_id: str,
    resource: Mapping[str, Any],
    terminal_status: str,
    wait_reason_code: str,
    detail: str,
    now_utc: str,
    repo_root: Optional[Path] = None,
) -> None:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    claim_uid = _project_runner_claim_uid(job_id, resource)
    canonical_key_digest = _sha256_text(str((resource or {}).get("canonical_key") or ""))[:8]
    waiting_record = {
        "schema_version": _resource_claim_ledger.RECORD_SCHEMA_VERSION,
        "entry_uid": f"{run_id}:waiting:{canonical_key_digest}",
        "claim_uid": claim_uid,
        "claimant_uid": _project_runner_claimant_uid(job_id),
        "resource": dict(resource),
        "recorded_at_utc": now_utc,
        "status": "waiting",
        "wait_reason_code": wait_reason_code,
        "detail": detail,
        "source": "scheduler.resource_admission",
    }
    _resource_claim_ledger.append_claim_record(waiting_record, repo_root=repo_root)
    if terminal_status != "rejected":
        return
    rejected_utc = _utc_iso(_parse_utc(now_utc) + _dt.timedelta(seconds=1))
    _resource_claim_ledger.append_claim_record(
        {
            **waiting_record,
            "entry_uid": f"{run_id}:rejected:{canonical_key_digest}",
            "recorded_at_utc": rejected_utc,
            "status": "rejected",
        },
        repo_root=repo_root,
    )


def _project_runner_shared_resource_policy_decision(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    runtime_policy_snapshot: Optional[Dict[str, Any]] = None,
    run_id: str,
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    from infra.controlplane import shared_resource_claim_ledger as _resource_claim_ledger

    if _normalize_job_kind(spec.get("job_kind")) != "project_runner":
        return {"allowed": True}
    project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
    scheduler_policy = project_run.get("scheduler_policy") if isinstance(project_run.get("scheduler_policy"), dict) else {}
    project_id = str(project_run.get("project_id") or "").strip() or "unknown_project"
    resource_class = str(scheduler_policy.get("resource_class") or "").strip().lower()
    if not resource_class:
        return {
            "allowed": False,
            "status": "blocked",
            "event": "blocked",
            "reason": f"project_runner_contract_policy_missing_resource_class:{project_id}",
        }

    contract_projection = project_run.get("contract_projection") if isinstance(project_run.get("contract_projection"), dict) else {}
    shared_resource_contract = contract_projection.get("shared_resource_contract") if isinstance(contract_projection.get("shared_resource_contract"), dict) else {}
    limit_contracts = [dict(row) for row in list(shared_resource_contract.get("limit_contracts") or []) if isinstance(row, dict)]
    caps = _scheduler_resource_class_caps(
        runtime_policy_snapshot=runtime_policy_snapshot,
        repo_root=repo_root,
    )
    if caps:
        class_cap = caps.get(resource_class)
        classification = shared_resource_contract.get("resource_classification") if isinstance(shared_resource_contract.get("resource_classification"), dict) else {}
        if not isinstance(classification, dict) or not str(classification.get("canonical_key") or "").strip():
            classification = {
                "resource_class": "job_family",
                "scope": "scheduler",
                "locator": resource_class,
                "canonical_key": f"job_family:scheduler:{resource_class}",
            }
        if class_cap is None or int(class_cap) <= 0:
            return {
                "allowed": False,
                "status": "blocked",
                "event": "blocked",
                "reason": f"shared_resource_not_allowed:{classification['canonical_key']}",
            }
        limit_contracts.append(
            {
                "resource": dict(classification),
                "limit_kind": "max_concurrent",
                "value": int(class_cap),
                "source": "runtime_policy",
            }
        )

    now_utc = _utc_iso(now or _utc_now())
    for limit_contract in limit_contracts:
        resource = limit_contract.get("resource") if isinstance(limit_contract.get("resource"), dict) else {}
        canonical_key = str(resource.get("canonical_key") or "").strip()
        limit_kind = str(limit_contract.get("limit_kind") or "").strip().lower()
        try:
            limit_value = int(limit_contract.get("value") or 0)
        except Exception:
            limit_value = 0
        if not canonical_key or limit_kind not in {"max_concurrent", "mutex"}:
            return {
                "allowed": False,
                "status": "blocked",
                "event": "blocked",
                "reason": f"shared_resource_invalid_limit_contract:{project_id}:{canonical_key or 'missing'}",
            }
        effective_limit = 1 if limit_kind == "mutex" else limit_value
        if effective_limit <= 0:
            return {
                "allowed": False,
                "status": "blocked",
                "event": "blocked",
                "reason": f"shared_resource_invalid_limit:{canonical_key}",
            }

        claim_uid = _project_runner_claim_uid(str(spec.get("job_id") or ""), resource)
        _reconcile_stale_project_runner_resource_claims(
            resource=resource,
            now_utc=now_utc,
            repo_root=repo_root,
        )
        occupancy = _resource_claim_ledger.replay_resource_occupancy(
            resource,
            repo_root=repo_root,
            skip_malformed=True,
        )
        active_claim_count = max(
            int(occupancy.get("active_claim_count") or 0),
            _project_runner_active_count_for_resource(spec, resource=resource, repo_root=repo_root),
        )
        waiting_claim_count = len(
            [
                row
                for row in list(occupancy.get("waiting_claims") or [])
                if str(row.get("claim_uid") or "") != claim_uid
            ]
        )
        if active_claim_count < effective_limit:
            continue
        if waiting_claim_count >= effective_limit:
            _record_project_runner_resource_claim_decision(
                job_id=str(spec.get("job_id") or ""),
                run_id=run_id,
                resource=resource,
                terminal_status="rejected",
                wait_reason_code="queue_backpressure",
                detail=(
                    f"shared resource queue backpressure for {canonical_key}; "
                    f"active={active_claim_count}/{effective_limit}; waiting={waiting_claim_count}"
                ),
                now_utc=now_utc,
                repo_root=repo_root,
            )
            return {
                "allowed": False,
                "status": "blocked",
                "event": "blocked",
                "reason": f"shared_resource_backpressure:{canonical_key}:{active_claim_count}/{effective_limit}:waiting={waiting_claim_count}",
            }
        _record_project_runner_resource_claim_decision(
            job_id=str(spec.get("job_id") or ""),
            run_id=run_id,
            resource=resource,
            terminal_status="waiting",
            wait_reason_code="awaiting_capacity",
            detail=f"shared resource capacity busy for {canonical_key}; active={active_claim_count}/{effective_limit}",
            now_utc=now_utc,
            repo_root=repo_root,
        )
        return {
            "allowed": False,
            "status": "deferred",
            "event": "deferred",
            "reason": f"shared_resource_waiting:{canonical_key}:{active_claim_count}/{effective_limit}",
        }
    return {"allowed": True}


def _project_runner_launch_policy_decision(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    runtime_policy_snapshot: Optional[Dict[str, Any]] = None,
    run_id: str = "",
    now: Optional[_dt.datetime] = None,
) -> Dict[str, Any]:
    if _normalize_job_kind(spec.get("job_kind")) != "project_runner":
        return {"allowed": True}
    project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
    scheduler_policy = project_run.get("scheduler_policy") if isinstance(project_run.get("scheduler_policy"), dict) else {}
    if not scheduler_policy:
        return {"allowed": True}

    project_id = str(project_run.get("project_id") or "").strip()
    required_bundle = str(scheduler_policy.get("required_context_bundle") or "").strip()
    current_bundle = str(spec.get("required_context_bundle") or "").strip()
    if not required_bundle:
        return {
            "allowed": False,
            "status": "blocked",
            "event": "blocked",
            "reason": f"project_runner_contract_policy_missing_required_context_bundle:{project_id or 'unknown_project'}",
        }
    if current_bundle != required_bundle:
        return {
            "allowed": False,
            "status": "blocked",
            "event": "blocked",
            "reason": (
                "project_runner_context_bundle_mismatch:"
                f"{project_id or 'unknown_project'}:{current_bundle or 'missing'}!={required_bundle}"
            ),
        }

    explicit_extra_refs = [str(item or "").strip() for item in list(spec.get("context_refs") or []) + list(spec.get("additional_context_refs") or []) if str(item or "").strip()]
    if explicit_extra_refs and not bool(scheduler_policy.get("additional_context_refs_allowed")):
        return {
            "allowed": False,
            "status": "blocked",
            "event": "blocked",
            "reason": (
                "project_runner_additional_context_refs_forbidden:"
                f"{project_id or 'unknown_project'}:{','.join(explicit_extra_refs)}"
            ),
        }

    forbidden_refs = {
        str(item or "").strip()
        for item in list(scheduler_policy.get("forbidden_context_refs") or [])
        if str(item or "").strip()
    }
    violating_refs = [ref for ref in explicit_extra_refs if ref in forbidden_refs]
    if violating_refs:
        return {
            "allowed": False,
            "status": "blocked",
            "event": "blocked",
            "reason": (
                "project_runner_forbidden_context_refs_requested:"
                f"{project_id or 'unknown_project'}:{','.join(violating_refs)}"
            ),
        }

    shared_resource_decision = _project_runner_shared_resource_policy_decision(
        spec,
        repo_root=repo_root,
        runtime_policy_snapshot=runtime_policy_snapshot,
        run_id=run_id or f"preflight_{str(spec.get('job_id') or '')}",
        now=now,
    )
    if not bool(shared_resource_decision.get("allowed", True)):
        return shared_resource_decision

    try:
        max_parallel_runs = int(scheduler_policy.get("max_parallel_runs") or 0)
    except Exception:
        max_parallel_runs = 0
    if max_parallel_runs <= 0:
        return {
            "allowed": False,
            "status": "blocked",
            "event": "blocked",
            "reason": f"project_runner_invalid_max_parallel_runs:{project_id or 'unknown_project'}",
        }
    return {"allowed": True}


def _can_launch_job(
    *,
    job_spec: Dict[str, Any],
    current_active_count: int,
    repo_root: Optional[Path] = None,
    runtime_policy_snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[bool, str]:
    """Check if a job can be launched given concurrency caps and role limits.

    Returns (can_launch, reason).
    """
    snapshot = (
        runtime_policy_snapshot
        if isinstance(runtime_policy_snapshot, dict)
        else current_runtime_resource_policy(repo_root=repo_root)
    )
    global_cap = snapshot.get("global_concurrency_cap", 1)
    per_role_cap = snapshot.get("per_role_cap", {})

    if current_active_count >= int(global_cap):
        return False, "global_concurrency_cap_reached"

    role = str(job_spec.get("role") or "generalist").strip().lower() or "generalist"
    role_cap = int(per_role_cap.get(role, global_cap))

    with _DISPATCH_GUARD:
        _cleanup_dispatch_tracking_locked()
        active_for_role = sum(1 for active_role in _DISPATCH_ACTIVE_JOB_ROLES.values() if str(active_role or "") == role)

    if active_for_role >= int(role_cap):
        return False, f"role_{role}_cap_reached"

    project_runner_policy_decision = _project_runner_launch_policy_decision(
        job_spec,
        repo_root=repo_root,
        runtime_policy_snapshot=snapshot,
        run_id=f"tick_{_utc_compact(_utc_now())}_{str(job_spec.get('job_id') or '')}",
    )
    if not bool(project_runner_policy_decision.get("allowed", True)):
        return False, str(project_runner_policy_decision.get("reason") or "project_runner_launch_blocked")

    return True, ""

def scheduler_tick(
    *,
    repo_root: Optional[Path] = None,
    now_utc: Optional[str] = None,
    limit: int = 10,
    await_completion: bool = True,
) -> Dict[str, Any]:
    """Run one scheduler cycle using parallel dispatch.

    This function submits eligible jobs to a thread pool (up to limit) and returns
    immediately without waiting for execution. The actual job execution happens
    concurrently in the pool.

    Returns a summary of submitted jobs, deferred jobs, and skipped jobs.
    """
    try:
        from infra.controlplane.verification_workflow_launch import reconcile_verification_workflow_campaigns as _reconcile_verification_workflow_campaigns

        _reconcile_verification_workflow_campaigns(repo_root=repo_root)
    except Exception:
        pass
    now = _parse_utc(now_utc) if now_utc else _utc_now()
    jobs = _scheduler_tick_rows(repo_root=repo_root, now=now)
    jobs_by_id = {str(row.get("job_id") or ""): row for row in jobs if str(row.get("job_id") or "").strip()}

    # Sort eligible jobs by fairness: due_utc ascending, job_id ascending
    eligible_jobs = [
        row
        for row in jobs
        if bool(row.get("due", {}).get("eligible"))
    ]
    eligible_jobs.sort(key=lambda r: (
        str(r.get("due", {}).get("due_utc") or ""),
        str(r.get("job_id") or ""),
    ))

    runtime_policy_snapshot = current_runtime_resource_policy(repo_root=repo_root)
    pool = _get_dispatch_pool(max_workers=int(runtime_policy_snapshot.get("global_concurrency_cap", 1) or 1))
    submitted: List[Dict[str, Any]] = []
    submitted_job_ids: set[str] = set()
    deferred: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    current_active = _get_active_job_count()

    for row in eligible_jobs:
        if len(submitted) >= max(1, min(100, int(limit))):
            break

        job_id = str(row.get("job_id") or "")
        if _dispatch_future_active(job_id):
            skipped.append({"job_id": job_id, "reason": "already_dispatched"})
            continue
        spec = load_job(job_id, repo_root=repo_root)

        # Check if we can launch this job
        can_launch, reason = _can_launch_job(
            job_spec=spec,
            current_active_count=current_active,
            repo_root=repo_root,
            runtime_policy_snapshot=runtime_policy_snapshot,
        )
        if not can_launch:
            deferred.append({"job_id": job_id, "reason": reason})
            continue

        # Check if job is already claimed elsewhere
        claim_path = (locks_dir(repo_root) / f"{_safe_name(job_id)}.lock.json").resolve()
        if claim_path.exists():
            hidden_claim_row = _read_json(claim_path) if claim_path.exists() else {}
            state = load_state(job_id, repo_root=repo_root)
            hidden_claim_probe_utc = str(
                (hidden_claim_row or {}).get("heartbeat_utc")
                or (hidden_claim_row or {}).get("claimed_utc")
                or ""
            ).strip()
            hidden_claim_age_s: Optional[float] = None
            if hidden_claim_probe_utc:
                try:
                    hidden_claim_age_s = max(0.0, (now - _parse_utc(hidden_claim_probe_utc)).total_seconds())
                except Exception:
                    hidden_claim_age_s = None
            hidden_claim_without_active_run = (
                not str(state.get("active_run_id") or "").strip()
                and bool(str(state.get("run_now_requested_utc") or "").strip())
                and not _dispatch_future_active(job_id)
                and (hidden_claim_age_s is None or hidden_claim_age_s >= 30.0)
            )
            if hidden_claim_without_active_run:
                hidden_release = _release_job_claim(
                    job_id,
                    repo_root=repo_root,
                    terminal_status="interrupted",
                    detail="scheduler_tick cleared hidden runtime claim without active_run_id or live dispatch future",
                )
                if bool(hidden_release.get("released")):
                    _append_event(
                        repo_root=repo_root,
                        job_id=job_id,
                        run_id=str((hidden_claim_row or {}).get("run_id") or ""),
                        event="hidden_claim_cleaned",
                        trigger="scheduler",
                        status="interrupted",
                        details={"reason": "scheduler_tick_hidden_claim_without_active_run"},
                    )
                claim_path = (locks_dir(repo_root) / f"{_safe_name(job_id)}.lock.json").resolve()
            terminal_claim_reconciliation = _reconcile_terminal_claim_before_launch(job_id, repo_root=repo_root)
            if bool(terminal_claim_reconciliation.get("ok")) and not claim_path.exists():
                pass
            elif bool(terminal_claim_reconciliation.get("ok")) and bool(terminal_claim_reconciliation.get("released")):
                pass
            else:
                claim_path = (locks_dir(repo_root) / f"{_safe_name(job_id)}.lock.json").resolve()
            policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
            if claim_path.exists() and _claim_is_stale(claim_path, max_runtime_seconds=int(policy.get("max_runtime_seconds") or 1800), now=now):
                try:
                    stale_claim = _read_json(claim_path)
                    claim_path.unlink()
                    _append_event(
                        repo_root=repo_root,
                        job_id=job_id,
                        run_id=str((stale_claim or {}).get("run_id") or ""),
                        event="stale_claim_cleaned",
                        trigger="scheduler",
                        status="blocked",
                        details={"reason": "scheduler_tick_unlinked_stale_claim", "claim_path": str(claim_path)},
                    )
                except FileNotFoundError:
                    pass
                except Exception:
                    skipped.append({"job_id": job_id, "reason": "stale_claim_cleanup_failed"})
                    continue
            elif claim_path.exists():
                skipped.append({"job_id": job_id, "reason": "claimed_elsewhere"})
                continue

        # Submit job to pool
        try:
            future = pool.submit(
                execute_job,
                job_id,
                repo_root=repo_root,
                trigger="scheduler",
                now_utc=_utc_iso(now),
            )
            with _DISPATCH_GUARD:
                _DISPATCH_ACTIVE_JOBS[job_id] = future
                _DISPATCH_ACTIVE_JOB_ROLES[job_id] = str(spec.get("role") or "generalist").strip().lower() or "generalist"
                _DISPATCH_ACTIVE_JOB_META[job_id] = {
                    "submitted_utc": _utc_iso(now),
                    "repo_root": str(Path(repo_root or _repo_root()).resolve()),
                }
            submitted.append({"job_id": job_id})
            submitted_job_ids.add(job_id)
            current_active += 1
        except Exception as e:
            skipped.append({"job_id": job_id, "reason": f"submit_failed:{str(e)}"})

    # Handle non-eligible jobs (same as before)
    ineligible_jobs = [
        row
        for row in jobs
        if not bool(row.get("due", {}).get("eligible"))
    ]
    for row in ineligible_jobs:
        due = row.get("due", {})
        reason = str(due.get("reason") or "")
        skipped_row = {"job_id": str(row.get("job_id") or ""), "reason": reason}

        if reason.startswith("dependency_not_ok:"):
            dep_job_id = str(reason.split(":", 1)[1] or "").strip()
            dep_row = jobs_by_id.get(dep_job_id, {})
            dep_state = dep_row.get("state") if isinstance(dep_row.get("state"), dict) else {}
            dep_due = dep_row.get("due") if isinstance(dep_row.get("due"), dict) else {}
            dep_due_reason = str(dep_due.get("reason") or "").strip()
            dep_pending = bool(dep_job_id) and (
                dep_job_id in submitted_job_ids
                or _dispatch_future_active(dep_job_id)
                or str(dep_state.get("active_run_id") or "").strip() != ""
                or str(dep_state.get("run_now_requested_utc") or "").strip() != ""
                or bool(dep_due.get("eligible"))
                or dep_due_reason in {"active_run", "run_now_requested", "due", "due_failed_slot_reusable", "retry_backoff", "not_due_yet"}
                or dep_due_reason.startswith("dependency_not_ok:")
            )
            if dep_pending:
                skipped_row["reason"] = f"dependency_pending:{dep_job_id}"
                skipped.append(skipped_row)
                continue
            spec = load_job(skipped_row["job_id"], repo_root=repo_root)
            state = load_state(skipped_row["job_id"], repo_root=repo_root)
            decision = _record_nonlaunch_decision(
                spec=spec,
                state=state,
                due=due,
                run_id=f"sj_{_utc_compact(now)}_{uuid.uuid4().hex[:6]}",
                trigger="scheduler",
                reason=reason,
                status="blocked",
                event="blocked",
                repo_root=repo_root,
                now=now,
                runtime_policy_snapshot=runtime_policy_snapshot,
            )
            if str(decision.get("receipt_path") or ""):
                skipped_row["receipt_path"] = str(decision.get("receipt_path") or "")
                skipped_row["report_path"] = str(decision.get("report_path") or "")
                skipped_row["decision_status"] = str(decision.get("status") or "")
        skipped.append(skipped_row)

    executed_results = []
    newly_deferred_skipped = []
    if await_completion:
        # For backward compatibility with tests and direct operator invocations,
        # optionally wait briefly for submitted jobs to complete and collect
        # their results. The background scheduler service disables this so the
        # tick loop never blocks on real job execution.
        for job_info in submitted:
            job_id = job_info.get("job_id", "")
            if job_id:
                with _DISPATCH_GUARD:
                    future = _DISPATCH_ACTIVE_JOBS.get(job_id)
                    if future:
                        try:
                            result = future.result(timeout=10)
                            if bool(result.get("deferred")):
                                newly_deferred_skipped.append({"job_id": job_id, "reason": str(result.get("reason", ""))})
                            else:
                                executed_results.append(result)
                        except concurrent.futures.TimeoutError:
                            executed_results.append({"ok": True, "job_id": job_id, "still_running": True})
                        except Exception as e:
                            executed_results.append({"ok": False, "job_id": job_id, "error": str(e)})

    # Eagerly clean up completed futures to prevent unbounded dict growth.
    # _get_active_job_count() also does this, but calling it explicitly here
    # ensures cleanup even when the dict is large.
    active_count = _get_active_job_count()

    return {
        "ok": True,
        "utc": _utc_iso(now),
        "submitted": submitted,
        "submitted_count": len(submitted),
        "deferred": deferred,
        "deferred_count": len(deferred),
        "skipped": skipped + newly_deferred_skipped,
        "skipped_count": len(skipped) + len(newly_deferred_skipped),
        "active_count": active_count,
        # Backward compatibility: old tests expect 'executed' with full execution results
        "executed": executed_results,
        "executed_count": len(executed_results),
    }
