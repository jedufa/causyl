def _resolve_recursive_planning_admission_for_nonlaunch(
    *,
    spec: Dict[str, Any],
    reason: str,
    repo_root: Optional[Path] = None,
) -> Optional[Dict[str, Any]]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    raw = (
        prompt_meta.get("recursive_planning_nonlive_admission")
        if isinstance(prompt_meta.get("recursive_planning_nonlive_admission"), dict)
        else {}
    )
    entrypoint = raw.get("entrypoint") if isinstance(raw.get("entrypoint"), dict) else {}
    plan_result = raw.get("plan_result") if isinstance(raw.get("plan_result"), dict) else {}
    if not entrypoint or not plan_result:
        return None
    expected_reason = str(raw.get("nonlaunch_reason") or "").strip()
    if expected_reason and expected_reason != str(reason or "").strip():
        return None
    try:
        from infra.controlplane import recursive_planning_nonlive_admission as _recursive_planning_nonlive_admission

        projection = _recursive_planning_nonlive_admission.materialize_recursive_planning_nonlive_admission(
            entrypoint=entrypoint,
            plan_result=plan_result,
            request_live_launch=bool(raw.get("request_live_launch")),
            repo_root=repo_root,
        )
    except Exception:
        return None
    admission = projection.get("admission") if isinstance(projection.get("admission"), dict) else {}
    return dict(admission) if admission else None


def _record_nonlaunch_decision(
    *,
    spec: Dict[str, Any],
    state: Dict[str, Any],
    due: Dict[str, Any],
    run_id: str,
    trigger: str,
    reason: str,
    status: str,
    event: str,
    repo_root: Optional[Path] = None,
    now: Optional[_dt.datetime] = None,
    execution_guard: Optional[Dict[str, Any]] = None,
    binding_override: Optional[Dict[str, Any]] = None,
    budget_admission: Optional[Dict[str, Any]] = None,
    runtime_policy_snapshot: Optional[Dict[str, Any]] = None,
    continuation_bootstrap: Optional[Dict[str, Any]] = None,
    recursive_planning_admission: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    current = now or _utc_now()
    evaluated_utc = _utc_iso(current)
    latest_state = load_state(str(spec.get("job_id") or ""), repo_root=repo_root)
    merged_state = {**dict(state or {}), **dict(latest_state or {})}
    signature = _sha256_text(
        "|".join(
            [
                str(spec.get("job_id") or ""),
                str(trigger or ""),
                str(status or ""),
                str(reason or ""),
                str(due.get("slot_key") or ""),
                str(due.get("due_utc") or ""),
            ]
        )
    )
    if str(merged_state.get("last_nonlaunch_signature") or "") == signature:
        return {
            "recorded": False,
            "deduped": True,
            "reason": reason,
            "status": status,
            "run_id": str(merged_state.get("last_nonlaunch_run_id") or ""),
            "receipt_path": str(merged_state.get("last_nonlaunch_receipt_path") or ""),
            "report_path": str(merged_state.get("last_nonlaunch_report_path") or ""),
        }

    job_shape_fields = _resolve_job_shape_fields(spec)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "job_kind": _normalize_job_kind(spec.get("job_kind")),
        "job_shape": str(job_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
        "job_id": str(spec.get("job_id") or ""),
        "run_id": run_id,
        "trigger": trigger,
        "status": status,
        "binding": dict(spec.get("binding") or {}),
        "schedule": dict(spec.get("schedule") or {}),
        "expected_output": dict(spec.get("expected_output") or {}),
        "context_refs": list(spec.get("context_refs") or []),
        "required_context_bundle": str(spec.get("required_context_bundle") or ""),
        "additional_context_refs": list(spec.get("additional_context_refs") or []),
        "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
        "effective_context_refs": _effective_context_refs(spec),
        "job_shape_context": _job_shape_context_obligation_projection(spec),
        "reason": reason,
        "decision_utc": evaluated_utc,
        "due": {
            "eligible": bool(due.get("eligible")),
            "reason": str(due.get("reason") or ""),
            "slot_key": str(due.get("slot_key") or ""),
            "due_utc": str(due.get("due_utc") or ""),
        },
        "ownership": {
            "scheduler_scope": "launch_intent_and_nonlaunch_evidence",
            "runtime_scope": "none",
            "handoff_state": "not_launched",
        },
    }
    if receipt["job_kind"] == "project_runner":
        project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
        scheduler_policy = project_run.get("scheduler_policy") if isinstance(project_run.get("scheduler_policy"), dict) else {}
        if scheduler_policy:
            receipt["scheduler_policy"] = dict(scheduler_policy)
    if isinstance(runtime_policy_snapshot, dict) and runtime_policy_snapshot:
        receipt["runtime_policy_snapshot"] = dict(runtime_policy_snapshot)
    if isinstance(execution_guard, dict) and execution_guard:
        receipt["execution_guard"] = dict(execution_guard)
    if isinstance(budget_admission, dict) and budget_admission:
        receipt["budget_admission"] = dict(budget_admission)
    if isinstance(continuation_bootstrap, dict) and continuation_bootstrap:
        receipt["continuation_bootstrap"] = dict(continuation_bootstrap)
    if not isinstance(recursive_planning_admission, dict) or not recursive_planning_admission:
        recursive_planning_admission = _resolve_recursive_planning_admission_for_nonlaunch(
            spec=spec,
            reason=reason,
            repo_root=repo_root,
        )
    if isinstance(recursive_planning_admission, dict) and recursive_planning_admission:
        receipt["recursive_planning_admission"] = dict(recursive_planning_admission)
    receipt["lifecycle"] = {
        "schema_version": "scheduled_job_lifecycle_v1",
        "reconciled_utc": evaluated_utc,
        "status": status,
        "cancel": {"requested": False, "requested_utc": "", "reason": "", "request_run_id": "", "result": "not_launched"},
        "timeout": {"expired": False, "failure_kind": "", "timeout_s": int(((spec.get("policy") or {}).get("max_runtime_seconds") or 1800))},
        "heartbeat": {"claim_present": False, "claim_reason": "not_launched", "heartbeat_utc": ""},
    }
    receipt["job_shape_lifecycle"] = dict(
        _job_shape_lifecycle_contract.lifecycle_projection_for_shape_record(
            receipt["job_shape"],
            status,
            active_run=False,
        )
    )
    receipt["repo_residue"] = _build_repo_residue_evidence(
        baseline=_parse_git_status_entries(repo_root=repo_root),
        repo_root=repo_root,
        declared_changed_files=[],
        validation_commands=[],
        artifact_refs=[],
        source="nonlaunch",
    )
    receipt_path = (receipts_dir(repo_root) / _safe_name(str(spec.get("job_id") or "")) / f"{_safe_name(run_id)}.json").resolve()
    report_path = (reports_dir(repo_root) / _safe_name(str(spec.get("job_id") or "")) / f"{_safe_name(run_id)}.md").resolve()
    binding_result = _bind_scheduler_launch(
        spec=spec,
        binding_override=binding_override,
        run_id=run_id,
        trigger=trigger,
        status=status,
        reason=reason,
        event=event,
        receipt_path=str(receipt_path),
        report_path=str(report_path),
        repo_root=repo_root,
        reference_status="execution_ref_unresolved",
        reference_status_reason=f"nonlaunch:{reason}" if reason else "nonlaunch",
        writer="scheduler.nonlaunch.bind",
        launch_cause=trigger or "scheduler",
    )
    if binding_result:
        receipt["binding_result"] = binding_result
    projected_state = {
        **merged_state,
        "active_run_id": "",
        "active_started_utc": "",
        "active_loop_iteration": 0,
        "active_loop_stage": "",
        "active_loop_completed_iterations": 0,
        "active_loop_max_iterations": 0,
        "active_loop_updated_utc": "",
        "last_run_id": run_id,
        "last_started_utc": "",
        "last_finished_utc": evaluated_utc,
        "last_status": status,
        "last_trigger": trigger,
        "last_receipt_path": str(receipt_path),
        "last_report_path": str(report_path),
        "last_nonlaunch_reason": reason,
        "last_nonlaunch_status": status,
        "last_nonlaunch_utc": evaluated_utc,
        "last_nonlaunch_run_id": run_id,
        "last_nonlaunch_receipt_path": str(receipt_path),
        "last_nonlaunch_report_path": str(report_path),
        "last_nonlaunch_signature": signature,
        "run_now_requested_utc": "",
        "run_now_request_run_id": "",
        "run_now_request_source": "",
    }
    due_after = due_status(spec, state=projected_state, now=current, repo_root=repo_root)
    receipt["reconciliation"] = _build_scheduler_reconciliation(spec, state=projected_state, due=due_after, now=current, repo_root=repo_root)
    receipt["forensic_debug_kit"] = _build_forensic_debug_kit(
        spec=spec,
        receipt=receipt,
        receipt_path=str(receipt_path),
        report_path=str(report_path),
    )

    _write_json_atomic(receipt_path, receipt)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(_decision_report_markdown(spec=spec, receipt=receipt), encoding="utf-8")

    _write_state(
        str(spec.get("job_id") or ""),
        {
            **projected_state,
            **_blank_state_receipt_projection(),
            **_receipt_state_projection(receipt),
        },
        repo_root=repo_root,
    )
    _append_event(
        repo_root=repo_root,
        job_id=str(spec.get("job_id") or ""),
        run_id=run_id,
        event=event,
        trigger=trigger,
        status=status,
        details={
            "reason": reason,
            "due_reason": str(due.get("reason") or ""),
            "slot_key": str(due.get("slot_key") or ""),
            "receipt_path": str(receipt_path),
            "report_path": str(report_path),
            "budget_admission": dict(budget_admission or {}) if isinstance(budget_admission, dict) else {},
        },
    )
    return {
        "recorded": True,
        "deduped": False,
        "reason": reason,
        "status": status,
        "run_id": run_id,
        "receipt_path": str(receipt_path),
        "report_path": str(report_path),
        "binding_result": binding_result,
    }


def _run_post_success_commit_tag(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    if not (root / ".git").exists():
        return {
            "ok": True,
            "skipped": True,
            "reason": "git_repo_missing",
            "command": "",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }
    dirty = _git_status_porcelain(repo_root=root)
    if not dirty:
        return {
            "ok": True,
            "skipped": True,
            "reason": "worktree_clean",
            "command": "",
            "exit_code": 0,
            "stdout": "",
            "stderr": "",
        }
    script = (root / "tools" / "git" / "git-commit-tag.sh").resolve()
    if not script.exists() or not script.is_file():
        return {
            "ok": False,
            "command": f"bash {script}",
            "exit_code": 127,
            "stdout": "",
            "stderr": f"missing script: {script}",
        }
    proc = subprocess.run(
        ["bash", str(script)],
        cwd=str(Path(repo_root or _repo_root()).resolve()),
        capture_output=True,
        text=True,
        check=False,
    )
    return {
        "ok": proc.returncode == 0,
        "command": f"bash {script}",
        "exit_code": int(proc.returncode),
        "stdout": str(proc.stdout or ""),
        "stderr": str(proc.stderr or ""),
    }


def _verifier_commit_excerpt(receipt: Dict[str, Any]) -> str:
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = list(loop.get("iterations") or [])
    if not iterations:
        return ""
    verifier = iterations[-1].get("verifier") if isinstance(iterations[-1].get("verifier"), dict) else {}
    content = str(verifier.get("content") or "").strip()
    if not content:
        return ""
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    if not lines:
        return ""
    parts: List[str] = [lines[0]]
    for line in lines[1:]:
        if line.lower().startswith(("findings", "missing validation", "smallest next step", "residual risk", "residual risks")):
            continue
        parts.append(line)
        if len(" ".join(parts)) >= 320:
            break
    return " ".join(parts)[:360]


def _record_post_success_verification_fragment(
    *,
    spec: Dict[str, Any],
    receipt: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    if str(receipt.get("job_kind") or "") != "build_verify_loop":
        return {"ok": True, "skipped": True, "reason": "not_build_verify_loop", "path": ""}
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = list(loop.get("iterations") or [])
    pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
    excerpt = _verifier_commit_excerpt(receipt)
    if not excerpt:
        excerpt = _pipeline_commit_excerpt(receipt)
    if not excerpt:
        return {"ok": True, "skipped": True, "reason": "missing_verifier_excerpt", "path": ""}
    builder_runtime = {}
    verifier_runtime = {}
    if iterations:
        builder = iterations[-1].get("builder") if isinstance(iterations[-1].get("builder"), dict) else {}
        verifier = iterations[-1].get("verifier") if isinstance(iterations[-1].get("verifier"), dict) else {}
        builder_runtime = builder.get("runtime_send") if isinstance(builder.get("runtime_send"), dict) else {}
        verifier_runtime = verifier.get("runtime_send") if isinstance(verifier.get("runtime_send"), dict) else {}
    base_runtime = _apply_pis_creation_runtime_overrides(
        spec,
        _normalize_runtime(spec.get("runtime"), job_kind=_normalize_job_kind(spec.get("job_kind"))),
    )
    loop_cfg = _normalize_loop(spec.get("loop"))
    configured_builder_runtime = _merge_runtime_overrides(base_runtime, loop_cfg.get("builder_runtime") if isinstance(loop_cfg.get("builder_runtime"), dict) else {})
    configured_verifier_runtime = _merge_runtime_overrides(base_runtime, loop_cfg.get("verifier_runtime") if isinstance(loop_cfg.get("verifier_runtime"), dict) else {})

    def _runtime_label(runtime_send: Dict[str, Any], configured_runtime: Dict[str, Any]) -> str:
        selected_model = str(runtime_send.get("selected_model") or configured_runtime.get("model") or "").strip()
        selected_agent_tech = str(
            runtime_send.get("selected_agent_tech")
            or configured_runtime.get("agent_tech")
            or configured_runtime.get("executor_agent_tech")
            or ""
        ).strip()
        if not (selected_agent_tech or selected_model):
            return ""
        return f"{selected_agent_tech or '?'}:{selected_model or '?'}"

    builder_runtime_label = _runtime_label(builder_runtime, configured_builder_runtime)
    verifier_runtime_label = _runtime_label(verifier_runtime, configured_verifier_runtime)
    iteration_count = max(0, len(iterations))
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    confidence_parts = [excerpt]
    if iteration_count:
        confidence_parts.append(f"iterations={iteration_count}")
    if builder_runtime_label:
        confidence_parts.append(f"builder_runtime={builder_runtime_label}")
    if verifier_runtime_label:
        confidence_parts.append(f"verifier_runtime={verifier_runtime_label}")
    entry = {
        "title": f"scheduler verification passed for {str(spec.get('job_id') or '').strip()}",
        "summary_bullets": [
            f"verifier result: {'; '.join(confidence_parts)}",
            f"scheduled job completed with status {str(receipt.get('status') or '').strip() or 'ok'}",
        ],
        "outcome": "note_only",
        "author": {
            "role": "scheduler",
            "source": "codex",
            "binding": {"type": str(binding.get('type') or ''), "id": str(binding.get('id') or '')},
        },
    }
    path = _commit_journal.write_entry(entry, repo=Path(repo_root or _repo_root()).resolve())
    return {"ok": True, "skipped": False, "reason": "", "path": str(path)}


def _run_post_success_commit_tag_with_retries(
    *,
    repo_root: Optional[Path] = None,
    retries: int = 0,
    backoff_seconds: int = 60,
) -> Dict[str, Any]:
    attempts: List[Dict[str, Any]] = []
    max_attempts = max(1, int(retries) + 1)
    delay_s = max(1, int(backoff_seconds))
    for attempt_index in range(max_attempts):
        result = _run_post_success_commit_tag(repo_root=repo_root)
        attempt = dict(result)
        attempt["attempt"] = attempt_index + 1
        attempts.append(attempt)
        if bool(result.get("ok")):
            result["attempts"] = attempts
            return result
        if attempt_index < max_attempts - 1:
            time.sleep(delay_s)
    final = dict(attempts[-1]) if attempts else _run_post_success_commit_tag(repo_root=repo_root)
    final["attempts"] = attempts
    return final


def _error_text_from_send_result(result: Dict[str, Any], send_error: str = "") -> str:
    assistant = result.get("assistant") if isinstance(result.get("assistant"), dict) else {}
    return str(send_error or assistant.get("content") or assistant.get("status") or "agent turn failed")


def _looks_like_quota_or_model_unavailable(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return False
    needles = [
        "insufficient_quota",
        "quota",
        "credit",
        "billing",
        "rate limit",
        "429",
        "model_not_found",
        "model not found",
        "unavailable model",
        "unsupported model",
    ]
    return any(n in t for n in needles)


def _looks_like_transient_runtime_error(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return False
    needles = [
        "timeout",
        "timed out",
        "connection reset",
        "connection refused",
        "connection aborted",
        "temporarily unavailable",
        "service unavailable",
        "gateway timeout",
        "bad gateway",
        "econnreset",
        "econnrefused",
        "ehostunreach",
        "network is unreachable",
        "broken pipe",
        "name resolution",
        "dns",
        "http 502",
        "http 503",
        "http 504",
    ]
    return any(n in t for n in needles)


def _infer_agent_tech_from_model(model: str) -> str:
    m = str(model or "").strip().lower()
    if not m:
        return ""
    if m == "pipeline" or m.startswith("pipeline:"):
        return "pipeline"
    if m.startswith("claude"):
        return "claude"
    if m.startswith("gemini"):
        return "gemini"
    if m.startswith("grok"):
        return "xai"
    if "codex" in m:
        return "codex"
    if m.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if m.startswith("deepseek"):
        return "deepseek"
    if m.startswith(("qwen", "llama", "mistral", "phi", "gemma")):
        return "ollama"
    return ""


def _provider_for_agent_tech(agent_tech: str) -> str:
    tech = str(agent_tech or "").strip().lower()
    if tech == "codex":
        return "openai"
    if tech in {"openai", "claude", "gemini", "xai", "ollama", "deepseek"}:
        return tech
    return ""


def _resolved_agent_tech_for_model(requested_agent_tech: str, model: str) -> str:
    requested = str(requested_agent_tech or "").strip().lower()
    inferred = _infer_agent_tech_from_model(model)
    if not inferred:
        return requested
    if not requested:
        return inferred
    if requested == inferred:
        return requested
    if requested in {"openai", "codex"} and inferred in {"openai", "codex"}:
        return requested
    if requested == "pipeline" or inferred == "pipeline":
        return requested
    return inferred


def _resolved_provider_for_model(
    requested_provider: str,
    *,
    model: str,
    agent_tech: str = "",
    default_provider: str = "",
) -> str:
    requested = str(requested_provider or "").strip().lower()
    resolved_agent_tech = _resolved_agent_tech_for_model(agent_tech, model)
    inferred_provider = _provider_for_agent_tech(resolved_agent_tech)
    if inferred_provider:
        return inferred_provider
    fallback = str(default_provider or "").strip().lower()
    return requested or fallback


def _pipeline_backend_model_config(runtime: Dict[str, Any]) -> Dict[str, str]:
    requested = str(runtime.get("model") or "").strip()
    normalized = requested or "pipeline"
    inner_model = ""
    if normalized.lower().startswith("pipeline:"):
        inner_model = normalized.split(":", 1)[1].strip()
    elif normalized.lower() != "pipeline":
        inner_model = normalized
    inner_agent_tech = _resolved_agent_tech_for_model(str(runtime.get("agent_tech") or "").strip().lower(), inner_model)
    requested_provider = str(runtime.get("provider") or "").strip().lower()
    selected_provider = _resolved_provider_for_model(
        requested_provider,
        model=inner_model,
        agent_tech=inner_agent_tech,
        default_provider="",
    )
    return {
        "selected_model": normalized or "pipeline",
        "stage8_model": inner_model,
        "stage8_agent_tech": inner_agent_tech if inner_agent_tech and inner_agent_tech != "pipeline" else "",
        "stage8_provider": selected_provider,
    }


def _pipeline_backend_payload(
    *,
    summary: Dict[str, Any],
    stage7: Dict[str, Any],
    stage8: Dict[str, Any],
    stage8_result: Dict[str, Any],
) -> Dict[str, Any]:
    patch_artifact = dict(stage8.get("patch_artifact") or {})
    patch_plan = dict(stage7.get("patch_plan") or {})
    target_files = [str(item) for item in list(patch_artifact.get("target_files") or []) if str(item).strip()]
    modify_files = [str(item.get("path") or "") for item in list(patch_plan.get("modify_files") or []) if isinstance(item, dict)]
    summary_lines = [
        f"status={str(summary.get('status') or '').strip() or 'unknown'}",
        f"current_stage={str(summary.get('current_stage') or '').strip() or 'unknown'}",
        f"modify_files={','.join(modify_files[:4]) or 'none'}",
        f"artifact_targets={','.join(target_files[:4]) or 'none'}",
    ]
    rationale = [str(item) for item in list(patch_plan.get("rationale") or []) if str(item).strip()]
    output = {
        "backend": "pipeline",
        "response_format": "pipeline_advisory_v1",
        "summary": "; ".join(summary_lines),
        "patch_plan_status": str(patch_plan.get("plan_status") or patch_plan.get("plan_outcome") or ""),
        "patch_artifact_kind": str(patch_artifact.get("artifact_kind") or ""),
        "target_files": target_files,
        "modify_files": modify_files,
        "rationale": rationale[:6],
        "selected_model": str(((stage8_result.get("result") if isinstance(stage8_result.get("result"), dict) else {}) or {}).get("model_response", {}).get("selected_model") or ""),
    }
    return output


def _pipeline_backend_send(
    *,
    spec: Optional[Dict[str, Any]] = None,
    role: str,
    binding: Dict[str, Any],
    message: str,
    options: Dict[str, Any],
    runtime: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from infra.pipeline.core import PipelineError, save_json_atomic
    from infra.pipeline.pipeline_runner import run_pipeline

    root = Path(repo_root or _repo_root()).resolve()
    backend_root = pipeline_backend_dir(root)
    jobs_root = (backend_root / "jobs").resolve()
    model_cfg = _pipeline_backend_model_config(runtime)
    backend_job_id = f"scheduled_pipeline_{_utc_compact()}_{uuid.uuid4().hex[:8]}"
    job_shape_fields = _resolve_job_shape_fields(spec) if isinstance(spec, dict) and spec else {}
    raw_request = {
        "title": f"Scheduled pipeline advisory for {str(binding.get('type') or '')}:{str(binding.get('id') or '')}",
        "description": str(message or "").strip()[:4000],
        "objective": str(message or "").strip()[:1200],
        "user_notes": str(message or "").strip(),
        "repo_root": str(root),
        "constraints": [
            "Treat this as an advisory scheduler backend run.",
            "Do not apply repository changes in this backend path.",
        ],
        "acceptance_criteria": [
            "Produce a bounded advisory payload the Codex controller can consume.",
        ],
        "metadata": {
            "scheduled_backend": True,
            "scheduler_job_id": "" if not isinstance(spec, dict) else str(spec.get("job_id") or ""),
            "job_kind": "" if not isinstance(spec, dict) else _normalize_job_kind(spec.get("job_kind")),
            "job_shape": str(job_shape_fields.get("job_shape") or ""),
            "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
            "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
            "binding": {"type": str(binding.get("type") or ""), "id": str(binding.get("id") or "")},
            "role": str(role or ""),
            "selected_model": model_cfg["selected_model"],
        },
    }
    job_dir = jobs_root / backend_job_id
    save_json_atomic(job_dir / "input" / "00_raw_request.json", raw_request)
    stage8_overrides: Dict[str, Any] = {}
    if model_cfg["stage8_model"]:
        stage8_overrides["model"] = model_cfg["stage8_model"]
        stage8_overrides["requested_model"] = model_cfg["stage8_model"]
    if model_cfg["stage8_agent_tech"]:
        stage8_overrides["agent_tech"] = model_cfg["stage8_agent_tech"]
        stage8_overrides["requested_agent_tech"] = model_cfg["stage8_agent_tech"]
    if model_cfg["stage8_provider"]:
        stage8_overrides["provider"] = model_cfg["stage8_provider"]
    if str(runtime.get("reasoning_effort") or "").strip():
        stage8_overrides["reasoning_effort"] = str(runtime.get("reasoning_effort") or "").strip()
    config_overrides = {
        "pipeline": {
            "jobs_root": str(jobs_root),
        },
        "stages": {
            "script_8_patch_generate": stage8_overrides,
        },
    }
    try:
        summary = run_pipeline(
            str(root),
            backend_job_id,
            branch_id="BRANCH-scheduled-pipeline-backend",
            stop_after_stage="script_8_patch_generate",
            config_overrides=config_overrides,
        )
        run_id = str(summary.get("run_id") or "").strip()
        run_dir = (job_dir / "runs" / run_id).resolve()
        stage7 = _read_json(run_dir / "07_patch_plan.json")
        stage8_result = _read_json(run_dir / "08_patch_artifact.json")
        payload = _pipeline_backend_payload(
            summary=summary,
            stage7=stage7,
            stage8=dict(stage8_result.get("result") or {}),
            stage8_result=stage8_result,
        )
        return {
            "ok": True,
            "assistant": {
                "content": json.dumps(payload, ensure_ascii=False, indent=2),
                "status": "ok",
                "meta": {
                    "backend": "pipeline",
                    "agent_tech": "pipeline",
                    "model": model_cfg["selected_model"],
                    "selected_model": model_cfg["selected_model"],
                    "pipeline_job_id": backend_job_id,
                    "pipeline_run_id": run_id,
                    "pipeline_job_dir": str(job_dir),
                },
            },
        }
    except PipelineError as exc:
        error_payload = {
            "backend": "pipeline",
            "response_format": "pipeline_error_v1",
            "error_code": str(exc.code),
            "message": str(exc.message),
            "details": dict(exc.details or {}),
        }
        return {
            "ok": False,
            "assistant": {
                "content": json.dumps(error_payload, ensure_ascii=False, indent=2),
                "status": "error",
                "meta": {
                    "backend": "pipeline",
                    "agent_tech": "pipeline",
                    "model": model_cfg["selected_model"],
                    "selected_model": model_cfg["selected_model"],
                    "pipeline_job_id": backend_job_id,
                    "pipeline_job_dir": str(job_dir),
                    "error": str(exc.message),
                },
            },
        }



def _pipeline_stage8_config_overrides(*, runtime: Dict[str, Any], jobs_root: Path) -> Tuple[Dict[str, Any], Dict[str, str]]:
    model_cfg = _pipeline_backend_model_config(runtime)
    stage8_overrides: Dict[str, Any] = {}
    if model_cfg["stage8_model"]:
        stage8_overrides["model"] = model_cfg["stage8_model"]
        stage8_overrides["requested_model"] = model_cfg["stage8_model"]
    if model_cfg["stage8_agent_tech"]:
        stage8_overrides["agent_tech"] = model_cfg["stage8_agent_tech"]
        stage8_overrides["requested_agent_tech"] = model_cfg["stage8_agent_tech"]
    if model_cfg["stage8_provider"]:
        stage8_overrides["provider"] = model_cfg["stage8_provider"]
    if str(runtime.get("reasoning_effort") or "").strip():
        stage8_overrides["reasoning_effort"] = str(runtime.get("reasoning_effort") or "").strip()
    return {
        "pipeline": {
            "jobs_root": str(jobs_root),
        },
        "stages": {
            "script_8_patch_generate": stage8_overrides,
        },
    }, model_cfg


def _pipeline_full_auto_status(summary: Dict[str, Any]) -> Dict[str, Any]:
    finalization = summary.get("finalization_state") if isinstance(summary.get("finalization_state"), dict) else {}
    repair = summary.get("repair_decision_summary") if isinstance(summary.get("repair_decision_summary"), dict) else {}
    controller_stop = summary.get("controller_stop") if isinstance(summary.get("controller_stop"), dict) else {}
    controller = summary.get("controller") if isinstance(summary.get("controller"), dict) else {}
    stop_reason = controller.get("stop_reason") if isinstance(controller.get("stop_reason"), dict) else {}
    reason_code = str(controller_stop.get("reason_code") or stop_reason.get("reason_code") or "").strip()
    blocking = sorted({str(code).strip() for code in list(repair.get("blocking_reason_codes") or []) if str(code).strip()})
    final_status = str(finalization.get("final_status") or "").strip().lower()
    summary_status = str(summary.get("status") or "").strip().upper()
    retry_authorization_state = str(repair.get("retry_authorization_state") or "").strip().lower()
    base = {
        "summary_status": summary_status,
        "final_status": final_status,
        "retry_authorization_state": retry_authorization_state,
        "blocking_reason_codes": blocking,
    }
    if reason_code == "user_cancelled":
        return {
            **base,
            "status": "interrupted",
            "ok": False,
            "error": "pipeline full-auto execution was cancelled before acceptance",
            "no_auto_retry": True,
            "reason_code": reason_code,
        }
    if final_status == "accepted":
        return {
            **base,
            "status": "ok",
            "ok": True,
            "error": "",
            "no_auto_retry": True,
            "reason_code": reason_code,
        }
    if final_status == "needs_review":
        if "CONTRACT_SCOPE_VIOLATION" in blocking:
            return {
                **base,
                "status": "blocked",
                "ok": False,
                "error": "pipeline full-auto execution requires operator review because contract scope widened beyond the approved boundary",
                "no_auto_retry": True,
                "reason_code": "CONTRACT_SCOPE_VIOLATION",
            }
        return {
            **base,
            "status": "failed_verification",
            "ok": False,
            "error": "pipeline full-auto execution completed without acceptance and exhausted machine-owned repair; scheduled batch runs do not enter an operator review queue",
            "no_auto_retry": True,
            "reason_code": reason_code or "needs_review",
        }
    if final_status == "aborted":
        if blocking:
            reason = ", ".join(blocking)
            detail = f"pipeline full-auto execution stopped after pipeline-owned verification/retry with blocking reason codes: {reason}"
        else:
            detail = "pipeline full-auto execution aborted before acceptance"
        return {
            **base,
            "status": "failed_verification",
            "ok": False,
            "error": detail,
            "no_auto_retry": True,
            "reason_code": reason_code or "aborted",
        }
    if summary_status == "WAITING_REVIEW":
        return {
            **base,
            "status": "failed_verification",
            "ok": False,
            "error": "pipeline full-auto execution stopped in WAITING_REVIEW, which is treated as terminal failed verification for scheduled batch runs",
            "no_auto_retry": True,
            "reason_code": reason_code or "waiting_review",
        }
    if summary_status in {"ABORTED", "WAITING_RETRY_DECISION"}:
        return {
            **base,
            "status": "failed_verification",
            "ok": False,
            "error": "pipeline full-auto execution did not reach acceptance",
            "no_auto_retry": True,
            "reason_code": reason_code or summary_status.lower(),
        }
    return {
        **base,
        "status": "error",
        "ok": False,
        "error": "pipeline full-auto summary did not report a terminal finalization state",
        "no_auto_retry": True,
        "reason_code": reason_code or "missing_finalization_state",
    }


def _blank_state_receipt_projection() -> Dict[str, Any]:
    return {
        "last_error": "",
        "last_reason_code": "",
        "last_execution_owner": "",
        "last_pipeline_executor_mode": "",
        "last_no_auto_retry": False,
        "last_pipeline_summary_status": "",
        "last_pipeline_final_status": "",
        "last_pipeline_retry_authorization_state": "",
        "last_pipeline_blocking_reason_codes": [],
        "last_lifecycle_status": "",
        "last_lifecycle_cancel_requested": False,
        "last_lifecycle_cancel_requested_utc": "",
        "last_lifecycle_cancel_outcome": "",
        "last_lifecycle_timeout_expired": False,
        "last_lifecycle_heartbeat_state": "",
        "last_shape_canonical_state": "",
        "last_shape_summary_bucket": "",
        "last_shape_terminal_summary_class": "",
        "last_shape_recovery_class": "",
        "last_shape_lifecycle_recognized": False,
        "last_reconciliation_status": "",
        "last_reconciliation_reason": "",
        "last_reconciliation_operator_request": "",
        "last_reconciliation_queue_backpressure_reason": "",
        "last_repo_residue_changed_count": 0,
        "last_repo_residue_undeclared_count": 0,
        "last_forensic_classification": "",
        "last_forensic_next_hint": "",
        "last_forensic_evidence_count": 0,
    }


def _receipt_state_projection(receipt: Dict[str, Any]) -> Dict[str, Any]:
    projection = _blank_state_receipt_projection()
    execution_contract = receipt.get("execution_contract") if isinstance(receipt.get("execution_contract"), dict) else {}
    pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
    summary = pipeline_execution.get("summary") if isinstance(pipeline_execution.get("summary"), dict) else {}
    finalization = summary.get("finalization_state") if isinstance(summary.get("finalization_state"), dict) else {}
    repair = summary.get("repair_decision_summary") if isinstance(summary.get("repair_decision_summary"), dict) else {}
    lifecycle = receipt.get("lifecycle") if isinstance(receipt.get("lifecycle"), dict) else {}
    lifecycle_cancel = lifecycle.get("cancel") if isinstance(lifecycle.get("cancel"), dict) else {}
    lifecycle_timeout = lifecycle.get("timeout") if isinstance(lifecycle.get("timeout"), dict) else {}
    lifecycle_heartbeat = lifecycle.get("heartbeat") if isinstance(lifecycle.get("heartbeat"), dict) else {}
    lifecycle_projection = receipt.get("job_shape_lifecycle") if isinstance(receipt.get("job_shape_lifecycle"), dict) else {}
    reconciliation = receipt.get("reconciliation") if isinstance(receipt.get("reconciliation"), dict) else {}
    repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
    forensic_debug_kit = receipt.get("forensic_debug_kit") if isinstance(receipt.get("forensic_debug_kit"), dict) else {}
    blocking_codes = [
        str(code).strip()
        for code in list(receipt.get("pipeline_blocking_reason_codes") or repair.get("blocking_reason_codes") or [])
        if str(code).strip()
    ]
    projection.update(
        {
            "last_error": str(receipt.get("error") or ""),
            "last_reason_code": str(receipt.get("reason_code") or ""),
            "last_execution_owner": str(execution_contract.get("execution_owner") or ""),
            "last_pipeline_executor_mode": str(execution_contract.get("pipeline_executor_mode") or ""),
            "last_no_auto_retry": bool(receipt.get("no_auto_retry")),
            "last_pipeline_summary_status": str(receipt.get("pipeline_summary_status") or summary.get("status") or ""),
            "last_pipeline_final_status": str(receipt.get("pipeline_final_status") or finalization.get("final_status") or ""),
            "last_pipeline_retry_authorization_state": str(
                receipt.get("pipeline_retry_authorization_state") or repair.get("retry_authorization_state") or ""
            ),
            "last_pipeline_blocking_reason_codes": blocking_codes,
            "last_lifecycle_status": str(lifecycle.get("status") or receipt.get("status") or ""),
            "last_lifecycle_cancel_requested": bool(lifecycle_cancel.get("requested")),
            "last_lifecycle_cancel_requested_utc": str(lifecycle_cancel.get("requested_utc") or ""),
            "last_lifecycle_cancel_outcome": str(lifecycle_cancel.get("result") or ""),
            "last_lifecycle_timeout_expired": bool(lifecycle_timeout.get("expired")),
            "last_lifecycle_heartbeat_state": str(lifecycle_heartbeat.get("claim_reason") or ""),
            "last_shape_canonical_state": str(lifecycle_projection.get("canonical_state") or ""),
            "last_shape_summary_bucket": str(lifecycle_projection.get("summary_bucket") or ""),
            "last_shape_terminal_summary_class": str(lifecycle_projection.get("terminal_summary_class") or ""),
            "last_shape_recovery_class": str(lifecycle_projection.get("recovery_class") or ""),
            "last_shape_lifecycle_recognized": bool(lifecycle_projection.get("recognized")),
            "last_reconciliation_status": str(reconciliation.get("status_projection") or ""),
            "last_reconciliation_reason": str(reconciliation.get("blocking_reason") or ""),
            "last_reconciliation_operator_request": str(reconciliation.get("operator_request") or ""),
            "last_reconciliation_queue_backpressure_reason": str(reconciliation.get("queue_backpressure_reason") or ""),
            "last_repo_residue_changed_count": int(repo_residue.get("current_dirty_path_count") or 0),
            "last_repo_residue_undeclared_count": len(list(repo_residue.get("dirty_paths_not_declared") or [])),
            "last_forensic_classification": str(forensic_debug_kit.get("classification") or ""),
            "last_forensic_next_hint": str(forensic_debug_kit.get("next_decision_hint") or ""),
            "last_forensic_evidence_count": len(list(forensic_debug_kit.get("evidence_refs") or [])),
        }
    )
    return projection


def _pipeline_execution_proof_from_summary(summary: Dict[str, Any]) -> Dict[str, Any]:
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    required_keys = [
        "execution_contract_path",
        "validated_artifact_path",
        "apply_result_path",
        "contract_verification_path",
        "repair_decision_path",
        "finalization_path",
    ]
    missing = [key for key in required_keys if not str(artifacts.get(key) or "").strip() or not Path(str(artifacts.get(key))).exists()]
    return {
        "proof_source": "pipeline",
        "required_validation_evidence": True,
        "validation_command_count": 0,
        "validation_commands": [],
        "proof_gap": bool(missing),
        "proof_gap_reason": "missing_pipeline_artifacts:" + ",".join(missing) if missing else "",
        "artifact_paths": {key: str(artifacts.get(key) or "") for key in required_keys},
    }


def _promote_pipeline_summary_to_repo(*, summary: Dict[str, Any], repo_root: Optional[Path] = None) -> Dict[str, Any]:
    from infra.pipeline.patch_ops import apply_unified_diff_to_workspace, preflight_validated_patch

    root = Path(repo_root or _repo_root()).resolve()
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    validated_path_txt = str(artifacts.get("validated_artifact_path") or "").strip()
    if not validated_path_txt:
        return {
            "ok": False,
            "reason": "validated_artifact_missing",
            "error": "pipeline summary is missing artifacts.validated_artifact_path",
            "apply_result": {},
        }
    validated_path = Path(validated_path_txt)
    payload = _read_json(validated_path)
    validated_artifact = dict(dict(payload.get("result") or {}).get("validated_artifact") or {})
    patch_text = str(validated_artifact.get("normalized_content") or "")
    target_files = [str(path) for path in list(validated_artifact.get("target_files") or []) if str(path).strip()]
    if not patch_text or not target_files:
        return {
            "ok": False,
            "reason": "validated_artifact_incomplete",
            "error": "validated pipeline artifact is missing normalized patch content or target file declarations",
            "apply_result": {},
        }
    try:
        preflight_validated_patch(
            repo_root=str(root),
            patch_text=patch_text,
            expected_target_files=target_files,
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "validated_artifact_preflight_failed",
            "error": str(exc),
            "apply_result": {},
        }
    git_path = str(shutil.which("git") or "git")
    apply_result = apply_unified_diff_to_workspace(
        repo_root=str(root),
        workspace_path=str(root),
        patch_text=patch_text,
        target_files=target_files,
        git_path=git_path,
        timeout_seconds=180,
        git_apply_args=["--verbose"],
    )
    ok = str(apply_result.get("apply_status") or "") == "applied_cleanly"
    return {
        "ok": ok,
        "reason": "" if ok else str(((apply_result.get("diagnostics") if isinstance(apply_result.get("diagnostics"), dict) else {}) or {}).get("failure_reason_code") or "repo_promotion_failed"),
        "error": "" if ok else str(((apply_result.get("diagnostics") if isinstance(apply_result.get("diagnostics"), dict) else {}) or {}).get("failure_detail") or apply_result.get("logs") or "pipeline patch promotion failed"),
        "validated_artifact_path": str(validated_path),
        "apply_result": dict(apply_result or {}),
        "target_files": target_files,
    }


def _pipeline_commit_excerpt(receipt: Dict[str, Any]) -> str:
    pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
    summary = pipeline_execution.get("summary") if isinstance(pipeline_execution.get("summary"), dict) else {}
    if not summary:
        return ""
    finalization = summary.get("finalization_state") if isinstance(summary.get("finalization_state"), dict) else {}
    repair = summary.get("repair_decision_summary") if isinstance(summary.get("repair_decision_summary"), dict) else {}
    pieces = [
        f"pipeline final_status={str(finalization.get('final_status') or '').strip() or 'unknown'}",
        f"blocking_reason_codes={','.join(list(repair.get('blocking_reason_codes') or [])) or 'none'}",
        f"pipeline_run_id={str(summary.get('run_id') or '').strip() or 'unknown'}",
        f"requested_model={str((receipt.get('runtime') if isinstance(receipt.get('runtime'), dict) else {}).get('model') or '').strip() or 'pipeline'}",
    ]
    return "; ".join(pieces)
