def _execute_pipeline_full_job(
    spec: Dict[str, Any],
    *,
    run_id: str,
    run_attempt: int = 1,
    trigger: str,
    max_runtime_seconds: int,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    from infra.pipeline.automation import run_full_auto

    runtime = _apply_pis_creation_runtime_overrides(
        spec,
        _normalize_runtime(spec.get("runtime"), job_kind=_normalize_job_kind(spec.get("job_kind"))),
    )
    job_shape_fields = _resolve_job_shape_fields(spec)
    prompt_text = _resolved_prompt_text(spec, repo_root=repo_root)
    prompt_sha = _sha256_text(prompt_text)
    root = Path(repo_root or _repo_root()).resolve()
    backend_root = pipeline_backend_dir(root)
    jobs_root = (backend_root / "jobs").resolve()
    config_overrides, model_cfg = _pipeline_stage8_config_overrides(runtime=runtime, jobs_root=jobs_root)
    pipeline_job_id = f"scheduled_pipeline_full_{_safe_name(str(spec.get('job_id') or 'job'))}_{_safe_name(run_id)}"
    raw_request = {
        "job_id": pipeline_job_id,
        "repo_root": str(root),
        "title": f"Scheduled pipeline full-auto execution for {str(spec.get('job_id') or '')}",
        "description": prompt_text[:4000],
        "objective": prompt_text[:1200] or prompt_text,
        "user_notes": prompt_text,
        "task_type_hint": "scheduled_build_verify_loop",
        "constraints": [
            "This request originated from a scheduled build_verify_loop run.",
            "Pipeline full-auto is the execution owner for apply, verification, retry, and finalization.",
            "Keep edits inside the execution contract and scheduled prompt scope.",
        ],
        "acceptance_criteria": [
            "Reach a terminal pipeline finalization result with durable verification artifacts.",
            "If acceptance is not earned, stop with an explicit review or failure state instead of fabricating success.",
        ],
        "metadata": {
            "scheduled_job": True,
            "scheduler_job_id": str(spec.get("job_id") or ""),
            "scheduler_run_id": str(run_id),
            "scheduler_trigger": str(trigger or ""),
            "job_shape": str(job_shape_fields.get("job_shape") or ""),
            "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
            "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
            "binding": dict(spec.get("binding") or {}),
            "expected_output": dict(spec.get("expected_output") or {}),
            "executor_agent_tech": str(runtime.get("executor_agent_tech") or ""),
            "pipeline_executor_mode": str(runtime.get("pipeline_executor_mode") or ""),
            "selected_model": model_cfg["selected_model"],
            "selected_stage8_model": model_cfg["stage8_model"],
            "selected_stage8_agent_tech": model_cfg["stage8_agent_tech"],
            "selected_stage8_provider": model_cfg["stage8_provider"],
            "job_attempt": int(run_attempt),
            "max_runtime_seconds": int(max_runtime_seconds),
        },
    }
    ok = False
    last_error = ""
    receipt: Dict[str, Any] = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "job_kind": "build_verify_loop",
        "job_shape": str(job_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
        "job_id": str(spec.get("job_id") or ""),
        "run_id": run_id,
        "trigger": trigger,
        "status": "error",
        "binding": dict(spec.get("binding") or {}),
        "schedule": dict(spec.get("schedule") or {}),
        "expected_output": dict(spec.get("expected_output") or {}),
        "context_refs": list(spec.get("context_refs") or []),
        "required_context_bundle": str(spec.get("required_context_bundle") or ""),
        "additional_context_refs": list(spec.get("additional_context_refs") or []),
        "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
        "effective_context_refs": _effective_context_refs(spec),
        "job_shape_context": _job_shape_context_obligation_projection(spec),
        "runtime": runtime,
        "compiled_execution": _compiled_execution_shadow_bundle_for_job(
            spec,
            roles=["builder", "verifier"],
            runtime=runtime,
            repo_root=repo_root,
        ),
        "execution_contract": _scheduled_execution_contract(runtime, job_kind="build_verify_loop"),
        "resolved_prompt": {
            "prompt_ref": str(spec.get("prompt_ref") or ""),
            "prompt_sha256": prompt_sha,
            "text": prompt_text,
        },
        "pipeline_execution": {
            "mode": PIPELINE_FULL_AUTO_MODE,
            "job_id": pipeline_job_id,
            "selected_model": model_cfg["selected_model"],
            "stage8_model": model_cfg["stage8_model"],
            "stage8_agent_tech": model_cfg["stage8_agent_tech"],
            "stage8_provider": model_cfg["stage8_provider"],
            "config_overrides": config_overrides,
            "summary": {},
            "summary_paths": {},
            "repo_promotion": {},
        },
        "no_auto_retry": True,
    }
    try:
        summary = run_full_auto(
            repo_root=root,
            prompt_text=prompt_text,
            raw_request=raw_request,
            job_id=pipeline_job_id,
            config_overrides=config_overrides,
            auto_approve=True,
        )
        pipeline_state = _pipeline_full_auto_status(summary)
        receipt["status"] = str(pipeline_state.get("status") or "error")
        receipt["reason_code"] = str(pipeline_state.get("reason_code") or "")
        receipt["pipeline_summary_status"] = str(pipeline_state.get("summary_status") or str(summary.get("status") or ""))
        receipt["pipeline_final_status"] = str(pipeline_state.get("final_status") or "")
        receipt["pipeline_retry_authorization_state"] = str(pipeline_state.get("retry_authorization_state") or "")
        receipt["pipeline_blocking_reason_codes"] = [str(code) for code in list(pipeline_state.get("blocking_reason_codes") or []) if str(code).strip()]
        receipt["pipeline_execution"]["summary"] = dict(summary)
        receipt["pipeline_execution"]["summary_paths"] = dict(summary.get("summary_paths") or {}) if isinstance(summary.get("summary_paths"), dict) else {}
        receipt["pipeline_execution"]["pipeline_run_id"] = str(summary.get("run_id") or "")
        receipt["pipeline_execution"]["pipeline_best_run_id"] = str(((summary.get("controller") if isinstance(summary.get("controller"), dict) else {}) or {}).get("best_run_id") or "")
        receipt["execution_proof"] = _pipeline_execution_proof_from_summary(summary)
        if bool(receipt["execution_proof"].get("proof_gap")) and receipt["status"] == "ok":
            receipt["status"] = "proof_gap"
            pipeline_state["ok"] = False
            pipeline_state["error"] = str(receipt["execution_proof"].get("proof_gap_reason") or "pipeline proof artifacts missing")
        if receipt["status"] == "ok":
            promotion = _promote_pipeline_summary_to_repo(summary=summary, repo_root=root)
            receipt["pipeline_execution"]["repo_promotion"] = promotion
            if not bool(promotion.get("ok")):
                receipt["status"] = "error"
                pipeline_state["ok"] = False
                pipeline_state["error"] = f"pipeline repo promotion failed: {str(promotion.get('error') or promotion.get('reason') or 'unknown error')}"
        ok = bool(pipeline_state.get("ok"))
        last_error = str(pipeline_state.get("error") or "")
        receipt["error"] = last_error
        receipt["pipeline_execution"]["scheduler_projection"] = {
            "scheduler_status": str(receipt.get("status") or ""),
            "summary_status": str(receipt.get("pipeline_summary_status") or ""),
            "final_status": str(receipt.get("pipeline_final_status") or ""),
            "retry_authorization_state": str(receipt.get("pipeline_retry_authorization_state") or ""),
            "blocking_reason_codes": list(receipt.get("pipeline_blocking_reason_codes") or []),
            "reason_code": str(receipt.get("reason_code") or ""),
        }
    except Exception as exc:
        last_error = str(exc)
        receipt["error"] = last_error
        receipt["status"] = "error"
    return ok, receipt, last_error

_CLI_VERSION_PROBE_CACHE: Dict[str, Dict[str, Any]] = {}


def _codex_cli_version_probe(binary: str) -> Dict[str, Any]:
    bin_path = str(binary or "").strip()
    if not bin_path:
        return {"ok": False, "binary": "", "version": "", "error": "missing_binary"}
    if bin_path in _CLI_VERSION_PROBE_CACHE:
        return dict(_CLI_VERSION_PROBE_CACHE[bin_path])
    try:
        cp = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        version_txt = str((cp.stdout or cp.stderr or "")).strip().splitlines()
        version_line = version_txt[0].strip() if version_txt else ""
        result = {
            "ok": bool(cp.returncode == 0 and version_line),
            "binary": bin_path,
            "version": version_line,
            "exit_code": int(cp.returncode),
            "error": "" if cp.returncode == 0 else (version_line or "version_probe_failed"),
        }
        _CLI_VERSION_PROBE_CACHE[bin_path] = dict(result)
        return result
    except Exception as e:
        result = {"ok": False, "binary": bin_path, "version": "", "error": str(e)}
        _CLI_VERSION_PROBE_CACHE[bin_path] = dict(result)
        return result


def _runtime_backend_telemetry() -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "backend": "",
        "codex_default_cmd": [],
        "codex_binary": "",
        "codex_cli_version": {},
        "claude_binary": "",
        "claude_cli_version": {},
        "gemini_binary": "",
        "gemini_cli_version": {},
        "xai_binary": "",
        "xai_api_key_present": False,
        "ollama_binary": "",
        "ollama_cli_version": {},
    }
    try:
        runtime_info = _agents.refresh_live_runtime()
    except Exception as e:
        out["error"] = str(e)
        runtime_info = {}
    if isinstance(runtime_info, dict):
        out["backend"] = str(runtime_info.get("backend") or "").strip()
        codex = runtime_info.get("codex") if isinstance(runtime_info.get("codex"), dict) else {}
        default_cmd = codex.get("default_cmd") if isinstance(codex.get("default_cmd"), list) else []
        out["codex_default_cmd"] = [str(x) for x in default_cmd if str(x or "").strip()]
    if not out["codex_default_cmd"]:
        raw = str(os.environ.get("EIEL_CODEX_TURN_CMD", "") or "").strip()
        if raw:
            try:
                out["codex_default_cmd"] = [str(x) for x in shlex.split(raw) if str(x or "").strip()]
            except Exception:
                out["codex_default_cmd"] = [raw]
    if out["codex_default_cmd"]:
        out["codex_binary"] = str(out["codex_default_cmd"][0])
        out["codex_cli_version"] = _codex_cli_version_probe(out["codex_binary"])
    claude_bin = str(shutil.which("claude") or "").strip()
    if claude_bin:
        out["claude_binary"] = claude_bin
        out["claude_cli_version"] = _codex_cli_version_probe(claude_bin)
    gemini_bin = str(shutil.which("gemini") or "").strip()
    if gemini_bin:
        out["gemini_binary"] = gemini_bin
        out["gemini_cli_version"] = _codex_cli_version_probe(gemini_bin)
    ollama_bin = str(shutil.which("ollama") or "").strip()
    if ollama_bin:
        out["ollama_binary"] = ollama_bin
        out["ollama_cli_version"] = _codex_cli_version_probe(ollama_bin)
    curl_bin = str(shutil.which("curl") or "").strip()
    if curl_bin:
        out["xai_binary"] = curl_bin
        out["xai_api_key_present"] = bool(str(os.environ.get("XAI_API_KEY") or "").strip())
    return out


def _receipt_model_telemetry(receipt: Dict[str, Any]) -> Dict[str, Any]:
    runtime = receipt.get("runtime") if isinstance(receipt.get("runtime"), dict) else {}
    execution_contract = receipt.get("execution_contract") if isinstance(receipt.get("execution_contract"), dict) else {}
    controller_runtime = execution_contract.get("controller_runtime") if isinstance(execution_contract.get("controller_runtime"), dict) else {}
    advisory_runtime = execution_contract.get("advisory_runtime") if isinstance(execution_contract.get("advisory_runtime"), dict) else {}
    out: Dict[str, Any] = {
        "executor_agent_tech": str(controller_runtime.get("agent_tech") or runtime.get("executor_agent_tech") or "codex"),
        "advisory_agent_tech": str(advisory_runtime.get("advisory_agent_tech") or advisory_runtime.get("agent_tech") or runtime.get("advisory_agent_tech") or ""),
        "advisory_model": str(advisory_runtime.get("advisory_model") or advisory_runtime.get("model") or runtime.get("advisory_model") or ""),
        "requested_agent_tech": str(advisory_runtime.get("agent_tech") or runtime.get("agent_tech") or ""),
        "requested_model": str(runtime.get("model") or ""),
        "reasoning_effort": str(runtime.get("reasoning_effort") or ""),
        "execution_mode": str(execution_contract.get("execution_mode") or runtime.get("execution_mode") or ""),
        "compiled_execution_mode": str(execution_contract.get("compiled_execution_mode") or runtime.get("compiled_execution_mode") or ""),
        "pipeline_executor_mode": str(execution_contract.get("pipeline_executor_mode") or runtime.get("pipeline_executor_mode") or ""),
        "stages": [],
    }
    pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
    if str(pipeline_execution.get("mode") or "") == PIPELINE_FULL_AUTO_MODE:
        summary = pipeline_execution.get("summary") if isinstance(pipeline_execution.get("summary"), dict) else {}
        out["executor_agent_tech"] = "pipeline"
        out["advisory_agent_tech"] = str(runtime.get("advisory_agent_tech") or "")
        out["advisory_model"] = str(runtime.get("advisory_model") or "")
        out["requested_agent_tech"] = str(runtime.get("agent_tech") or "")
        out["stages"] = [
            {
                "stage": "pipeline_full_auto",
                "controller_called": bool(summary),
                "advisory_called": False,
                "selected_agent_tech": "pipeline",
                "selected_model": str(runtime.get("model") or "pipeline"),
                "advisory_agent_tech": "",
                "advisory_model": "",
                "requested_model_meta": str(runtime.get("model") or ""),
                "backend": "pipeline",
                "provider_model": str(runtime.get("model") or "pipeline"),
                "controller_request_payload_bytes": 0,
                "controller_response_bytes": len(json.dumps(summary, ensure_ascii=False).encode("utf-8")) if summary else 0,
                "advisory_request_payload_bytes": 0,
                "advisory_response_bytes": 0,
                "attempts": [],
                "advisory_attempts": [],
                "pipeline_job_id": str(pipeline_execution.get("job_id") or ""),
                "pipeline_run_id": str(pipeline_execution.get("pipeline_run_id") or summary.get("run_id") or ""),
                "pipeline_best_run_id": str(pipeline_execution.get("pipeline_best_run_id") or ((summary.get("controller") if isinstance(summary.get("controller"), dict) else {}) or {}).get("best_run_id") or ""),
            }
        ]
        return out
    if str(receipt.get("job_kind") or "") == "single_turn":
        runtime_send = receipt.get("runtime_send") if isinstance(receipt.get("runtime_send"), dict) else {}
        advisory_send = receipt.get("advisory_send") if isinstance(receipt.get("advisory_send"), dict) else {}
        assistant = receipt.get("assistant") if isinstance(receipt.get("assistant"), dict) else {}
        assistant_meta = assistant.get("meta") if isinstance(assistant.get("meta"), dict) else {}
        out["stages"] = [
            {
                "stage": "single_turn",
                "controller_called": bool(runtime_send),
                "advisory_called": bool(advisory_send),
                "selected_agent_tech": str(runtime_send.get("selected_agent_tech") or "codex"),
                "selected_model": str(runtime_send.get("selected_model") or ""),
                "advisory_agent_tech": str(advisory_send.get("selected_agent_tech") or advisory_runtime.get("agent_tech") or ""),
                "advisory_model": str(advisory_send.get("selected_model") or advisory_runtime.get("model") or ""),
                "requested_model_meta": str(assistant_meta.get("requested_model") or ""),
                "backend": str(assistant_meta.get("backend") or ""),
                "provider_model": str(assistant_meta.get("model") or ""),
                "compiled_dispatch_mode": str(((receipt.get("compiled_dispatch") if isinstance(receipt.get("compiled_dispatch"), dict) else {}) or {}).get("dispatch_mode") or ""),
                "controller_request_payload_bytes": int(assistant_meta.get("request_payload_bytes") or 0),
                "controller_response_bytes": len(str(assistant.get("content") or "").encode("utf-8")),
                "advisory_request_payload_bytes": int((((receipt.get("advisory_assistant") if isinstance(receipt.get("advisory_assistant"), dict) else {}) or {}).get("meta") if isinstance(((receipt.get("advisory_assistant") if isinstance(receipt.get("advisory_assistant"), dict) else {}) or {}).get("meta"), dict) else {}).get("request_payload_bytes") or 0),
                "advisory_response_bytes": len(str(((receipt.get("advisory_assistant") if isinstance(receipt.get("advisory_assistant"), dict) else {}) or {}).get("content") or "").encode("utf-8")),
                "attempts": list(runtime_send.get("attempts") or []),
                "advisory_attempts": list(advisory_send.get("attempts") or []),
            }
        ]
        return out

    if str(receipt.get("job_kind") or "") == "swarm_launch":
        swarm_row = receipt.get("swarm") if isinstance(receipt.get("swarm"), dict) else {}
        out["executor_agent_tech"] = "swarm"
        out["advisory_agent_tech"] = ""
        out["advisory_model"] = ""
        out["requested_agent_tech"] = "swarm"
        out["stages"] = [
            {
                "stage": "swarm_launch",
                "controller_called": False,
                "advisory_called": False,
                "selected_agent_tech": "swarm",
                "selected_model": str(runtime.get("model") or ""),
                "advisory_agent_tech": "",
                "advisory_model": "",
                "requested_model_meta": str(runtime.get("model") or ""),
                "backend": "swarm",
                "provider_model": str(runtime.get("model") or ""),
                "controller_request_payload_bytes": 0,
                "controller_response_bytes": len(json.dumps(swarm_row, ensure_ascii=False).encode("utf-8")) if swarm_row else 0,
                "advisory_request_payload_bytes": 0,
                "advisory_response_bytes": 0,
                "attempts": [],
                "advisory_attempts": [],
                "swarm_id": str(swarm_row.get("swarm_id") or ""),
                "swarm_status": str(swarm_row.get("final_status") or ""),
            }
        ]
        return out

    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = list(loop.get("iterations") or [])
    stages: List[Dict[str, Any]] = []
    for row in iterations:
        if not isinstance(row, dict):
            continue
        iteration = int(row.get("iteration") or 0)
        for stage_name in ("builder", "verifier"):
            stage = row.get(stage_name) if isinstance(row.get(stage_name), dict) else {}
            meta = stage.get("meta") if isinstance(stage.get("meta"), dict) else {}
            runtime_send = stage.get("runtime_send") if isinstance(stage.get("runtime_send"), dict) else {}
            advisory = stage.get("advisory") if isinstance(stage.get("advisory"), dict) else {}
            advisory_send = advisory.get("runtime_send") if isinstance(advisory.get("runtime_send"), dict) else {}
            stages.append(
                {
                    "iteration": iteration,
                    "stage": stage_name,
                    "controller_called": bool(runtime_send),
                    "advisory_called": bool(advisory_send),
                    "selected_agent_tech": str(runtime_send.get("selected_agent_tech") or "codex"),
                    "selected_model": str(runtime_send.get("selected_model") or ""),
                    "advisory_agent_tech": str(advisory_send.get("selected_agent_tech") or ""),
                    "advisory_model": str(advisory_send.get("selected_model") or ""),
                    "requested_model_meta": str(meta.get("requested_model") or ""),
                    "backend": str(meta.get("backend") or ""),
                    "provider_model": str(meta.get("model") or ""),
                    "compiled_dispatch_mode": str(((meta.get("compiled_dispatch") if isinstance(meta.get("compiled_dispatch"), dict) else {}) or {}).get("dispatch_mode") or ""),
                    "controller_request_payload_bytes": int(meta.get("request_payload_bytes") or 0),
                    "controller_response_bytes": len(str(stage.get("content") or "").encode("utf-8")),
                    "advisory_request_payload_bytes": int((((advisory.get("meta") if isinstance(advisory.get("meta"), dict) else {}) or {}).get("request_payload_bytes")) or 0),
                    "advisory_response_bytes": len(str(advisory.get("content") or "").encode("utf-8")),
                    "attempts": list(runtime_send.get("attempts") or []),
                    "advisory_attempts": list(advisory_send.get("attempts") or []),
                }
            )
    out["stages"] = stages
    return out


def _scheduled_resend_key(*, job_id: str, run_id: str, stage: str, iteration: int = 0, run_attempt: int = 1) -> str:
    base = f"scheduled_job::{job_id}::{run_id}::{stage}"
    if int(run_attempt) > 1:
        base = f"{base}::job_try_{int(run_attempt)}"
    if iteration > 0:
        base = f"{base}::iter_{iteration}"
    return base


def _agent_send_with_retry_and_fallback(
    *,
    spec: Dict[str, Any],
    role: str,
    binding: Dict[str, Any],
    message: str,
    options: Dict[str, Any],
    retries: int,
    backoff_seconds: int,
    runtime: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    allowed_techs = {""} | AGENT_TECH_SET
    primary_agent_tech = str(runtime.get("agent_tech") or "").strip().lower()
    fallback_agent_tech = str(runtime.get("fallback_agent_tech") or "").strip().lower()
    raw_fallback_agent_techs = runtime.get("fallback_agent_techs")
    raw_sequence = runtime.get("tech_model_sequence")
    sequence_pairs: List[Tuple[str, str]] = []
    if isinstance(raw_sequence, list):
        for item in raw_sequence:
            if not isinstance(item, dict):
                continue
            seq_tech = str(item.get("agent_tech") or "").strip().lower()
            seq_model = str(item.get("model") or "").strip()
            if seq_tech in allowed_techs:
                sequence_pairs.append((seq_tech, seq_model))
    techs: List[str] = []
    if not sequence_pairs:
        if primary_agent_tech:
            techs.append(primary_agent_tech)
        if isinstance(raw_fallback_agent_techs, list):
            for item in raw_fallback_agent_techs:
                txt = str(item or "").strip().lower()
                if txt and txt in allowed_techs:
                    techs.append(txt)
        elif fallback_agent_tech and fallback_agent_tech in allowed_techs:
            techs.append(fallback_agent_tech)
        if not techs:
            techs = [""]
    primary_model = str(runtime.get("model") or "").strip()
    fallback_model = str(runtime.get("fallback_model") or "").strip()
    fallback_models = runtime.get("fallback_models")
    fallback_on_quota = bool(runtime.get("fallback_on_quota", True))
    models: List[str] = []
    if not sequence_pairs:
        if primary_model:
            models.append(primary_model)
        if isinstance(fallback_models, list):
            for item in fallback_models:
                txt = str(item or "").strip()
                if txt and txt not in models:
                    models.append(txt)
        elif fallback_model and fallback_model not in models:
            models.append(fallback_model)
        if not models:
            models = [""]
    attempt_pairs: List[Tuple[str, str]] = []
    if sequence_pairs:
        attempt_pairs = list(sequence_pairs)
    else:
        used_techs: List[str] = []
        for idx, model_name in enumerate(models):
            inferred = _infer_agent_tech_from_model(model_name)
            tech_name = ""
            if idx < len(techs) and str(techs[idx] or "").strip().lower():
                tech_name = str(techs[idx] or "").strip().lower()
            elif techs:
                tech_name = str(techs[-1] or "").strip().lower()
            elif inferred and inferred in allowed_techs:
                tech_name = inferred
            if tech_name != "codex":
                tech_name = _resolved_agent_tech_for_model(tech_name, model_name)
            attempt_pairs.append((tech_name, model_name))
            if tech_name and tech_name not in used_techs:
                used_techs.append(tech_name)
        # If configured fallback techs were not exercised by explicit model entries,
        # try their provider defaults as last-resort quota fallback.
        for tech_name in techs:
            txt = str(tech_name or "").strip().lower()
            if txt and txt not in used_techs:
                attempt_pairs.append((txt, ""))
        if not attempt_pairs:
            attempt_pairs = [("", "")]
    attempts: List[Dict[str, Any]] = []
    max_attempts = max(1, int(retries) + 1)
    delay_s = max(1, int(backoff_seconds))
    last_result: Dict[str, Any] = {"ok": False, "assistant": {"content": "", "status": "error", "meta": {}}}
    selected_model = ""
    resend_key_base = str((options or {}).get("resend_key") or "").strip()
    selected_agent_tech = ""
    for pair_idx, pair in enumerate(attempt_pairs):
        tech_name = str(pair[0] or "").strip().lower()
        model_name = str(pair[1] or "").strip()
        previous_send_raised = False
        pair_resend_key = (
            resend_key_base
            if not resend_key_base or pair_idx == 0
            else f"{resend_key_base}::pair_{pair_idx + 1}_{tech_name or 'default'}_{model_name or 'default'}"
        )
        for attempt_idx in range(max_attempts):
            call_opts = dict(options or {})
            if pair_resend_key:
                attempt_resend_key = pair_resend_key
                if attempt_idx > 0 and not previous_send_raised:
                    attempt_resend_key = f"{pair_resend_key}::retry_{attempt_idx + 1}"
                call_opts["resend_key"] = attempt_resend_key
            if tech_name:
                call_opts["agent_tech"] = tech_name
            else:
                call_opts.pop("agent_tech", None)
            if model_name:
                call_opts["model"] = model_name
            else:
                call_opts.pop("model", None)
            send_error = ""
            try:
                if tech_name == "pipeline":
                    result = _pipeline_backend_send(
                        spec=spec,
                        role=role,
                        binding=binding,
                        message=message,
                        options=call_opts,
                        runtime=runtime,
                    )
                else:
                    result = _agents.send_scheduled_message(role=role, binding=binding, message=message, options=call_opts)
            except Exception as e:
                send_error = str(e)
                result = {
                    "ok": False,
                    "assistant": {
                        "content": f"[scheduler_error] {send_error}",
                        "status": "error",
                        "meta": {"error": send_error},
                    },
                }
            previous_send_raised = bool(send_error)
            last_result = result
            assistant = result.get("assistant") if isinstance(result.get("assistant"), dict) else {}
            err_text = _error_text_from_send_result(result, send_error=send_error)
            quota_like = _looks_like_quota_or_model_unavailable(err_text)
            transient_like = _looks_like_transient_runtime_error(err_text)
            attempts.append(
                {
                    "agent_tech": tech_name,
                    "model": model_name,
                    "attempt": attempt_idx + 1,
                    "ok": bool(result.get("ok")),
                    "quota_like": quota_like,
                    "transient_like": transient_like,
                    "error": err_text if not bool(result.get("ok")) else "",
                    "assistant_status": str(assistant.get("status") or ""),
                }
            )
            if bool(result.get("ok")):
                selected_agent_tech = tech_name
                selected_model = model_name
                return result, {
                    "selected_agent_tech": selected_agent_tech,
                    "selected_model": selected_model,
                    "attempts": attempts,
                    "resend_key": str(call_opts.get("resend_key") or ""),
                    "resend_key_base": resend_key_base,
                }
            if quota_like and fallback_on_quota:
                break
            if attempt_idx < max_attempts - 1:
                time.sleep(delay_s)
    return last_result, {
        "selected_agent_tech": selected_agent_tech,
        "selected_model": selected_model,
        "attempts": attempts,
        "resend_key": str(call_opts.get("resend_key") or ""),
        "resend_key_base": resend_key_base,
    }


def _execute_scheduled_controlled_turn(
    *,
    spec: Dict[str, Any],
    role: str,
    binding: Dict[str, Any],
    message: str,
    compiled_prompt_text: str = "",
    compiled_dispatch_mode: str = "legacy_message",
    options: Dict[str, Any],
    retries: int,
    backoff_seconds: int,
    runtime: Dict[str, Any],
) -> Tuple[Dict[str, Any], Dict[str, Any], Dict[str, Any], Dict[str, Any]]:
    contract = _scheduled_execution_contract(runtime, job_kind=_normalize_job_kind(spec.get("job_kind")))
    scheduled_stage = str(options.get("_scheduled_stage") or "single_turn")
    execution_mode = str(contract.get("execution_mode") or "controller_only")
    advisory_runtime = contract.get("advisory_runtime") if isinstance(contract.get("advisory_runtime"), dict) else {}
    advisory_result: Dict[str, Any] = {}
    advisory_send: Dict[str, Any] = {}
    controller_message = str(message or "")
    if advisory_runtime and execution_mode in {"advisory_then_controller", "advisory_only"}:
        advisory_opts = dict(options or {})
        advisory_opts.pop("_scheduled_stage", None)
        advisory_resend_key = str(advisory_opts.get("resend_key") or "").strip()
        if advisory_resend_key:
            advisory_opts["resend_key"] = f"{advisory_resend_key}::advisory"
        if str(advisory_runtime.get("model") or "").strip():
            advisory_opts["model"] = str(advisory_runtime.get("model") or "").strip()
        if str(advisory_runtime.get("reasoning_effort") or "").strip():
            advisory_opts["reasoning_effort"] = str(advisory_runtime.get("reasoning_effort") or "").strip()
        advisory_result, advisory_send = _agent_send_with_retry_and_fallback(
            spec=spec,
            role=role,
            binding=binding,
            message=_build_advisory_backend_message(spec, stage=scheduled_stage, base_message=message),
            options=advisory_opts,
            retries=retries,
            backoff_seconds=backoff_seconds,
            runtime=advisory_runtime,
        )
        controller_message = _append_advisory_backend_context(
            message,
            advisory_runtime=advisory_runtime,
            advisory_result=advisory_result,
            advisory_send=advisory_send,
        )
    if str(compiled_dispatch_mode or "").strip().lower() == "compiled_prompt" and str(compiled_prompt_text or "").strip():
        if scheduled_stage in {"builder", "verifier", "repair"}:
            controller_message = (
                str(compiled_prompt_text or "").rstrip()
                + "\n\n"
                + "[Scheduled Stage Contract]\n"
                + str(controller_message or "").strip()
            ).strip()
        else:
            controller_message = str(compiled_prompt_text or "")
    controller_runtime = contract.get("controller_runtime") if isinstance(contract.get("controller_runtime"), dict) else {}
    if execution_mode == "advisory_only":
        return advisory_result, advisory_send, advisory_result, advisory_send
    controller_opts = dict(options or {})
    controller_opts.pop("_scheduled_stage", None)
    controller_resend_key = str(controller_opts.get("resend_key") or "").strip()
    if advisory_runtime and controller_resend_key:
        controller_opts["resend_key"] = f"{controller_resend_key}::controller"
    if str(controller_runtime.get("model") or "").strip():
        controller_opts["model"] = str(controller_runtime.get("model") or "").strip()
    if str(controller_runtime.get("reasoning_effort") or "").strip():
        controller_opts["reasoning_effort"] = str(controller_runtime.get("reasoning_effort") or "").strip()
    controller_result, controller_send = _agent_send_with_retry_and_fallback(
        spec=spec,
        role=role,
        binding=binding,
        message=controller_message,
        options=controller_opts,
        retries=retries,
        backoff_seconds=backoff_seconds,
        runtime=controller_runtime,
    )
    return controller_result, controller_send, advisory_result, advisory_send


def _execute_single_turn_job(
    spec: Dict[str, Any],
    *,
    binding_override: Optional[Dict[str, Any]] = None,
    run_id: str,
    run_attempt: int = 1,
    trigger: str,
    max_runtime_seconds: int,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    role_meta = _role_target(spec.get("role") or "generalist")
    runtime = _apply_pis_creation_runtime_overrides(
        spec,
        _normalize_runtime(spec.get("runtime"), job_kind=_normalize_job_kind(spec.get("job_kind"))),
    )
    policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    msg_options: Dict[str, Any] = {
        "timeout_s": max_runtime_seconds,
        "resend_key": _scheduled_resend_key(
            job_id=str(spec.get("job_id") or ""),
            run_id=run_id,
            stage="single_turn",
            run_attempt=run_attempt,
        ),
        "_scheduled_stage": "single_turn",
        "_scheduled_context_mode": "run_scoped_no_history",
        **({"prompt_file": role_meta["prompt_file"]} if role_meta["prompt_file"] else {}),
    }
    if str(runtime.get("model") or "").strip():
        msg_options["model"] = str(runtime.get("model") or "").strip()
    if str(runtime.get("reasoning_effort") or "").strip():
        msg_options["reasoning_effort"] = str(runtime.get("reasoning_effort") or "").strip()
    prompt_text = _resolved_prompt_text(spec, repo_root=repo_root)
    prompt_sha = _sha256_text(prompt_text)
    dispatched_message = _build_dispatch_message(spec, trigger=trigger, run_id=run_id, repo_root=repo_root)
    compiled_shadow = _compiled_execution_shadow_bundle_for_job(
        spec,
        roles=[role_meta["role"]],
        runtime=runtime,
        repo_root=repo_root,
    )
    compiled_dispatch = _compiled_prompt_dispatch_for_role(
        compiled_execution=compiled_shadow,
        role=role_meta["role"],
    )
    runtime_binding = dict(spec.get("binding") or {})
    if isinstance(binding_override, dict):
        if str(binding_override.get("task_id") or "").strip():
            runtime_binding["task_id"] = str(binding_override.get("task_id") or "").strip()
        if str(binding_override.get("ledger_uid") or "").strip():
            runtime_binding["ledger_uid"] = str(binding_override.get("ledger_uid") or "").strip()
    if not bool(compiled_dispatch.get("ok", True)):
        result = _compiled_dispatch_blocked_result(role=role_meta["runtime_role"], dispatch=compiled_dispatch)
        runtime_send_meta = {"selected_model": "", "attempts": []}
        advisory_result = {"ok": False, "assistant": {"content": "", "status": "skipped", "meta": {}}}
        advisory_send_meta = {"selected_model": "", "attempts": []}
    else:
        result, runtime_send_meta, advisory_result, advisory_send_meta = _execute_scheduled_controlled_turn(
            spec=spec,
            role=role_meta["runtime_role"],
            binding=runtime_binding,
            message=dispatched_message,
            compiled_prompt_text=str(compiled_dispatch.get("prompt_text") or ""),
            compiled_dispatch_mode=str(compiled_dispatch.get("dispatch_mode") or "legacy_message"),
            options=msg_options,
            retries=int(policy.get("agent_turn_retries") or 0),
            backoff_seconds=int(policy.get("agent_turn_retry_backoff_seconds") or 5),
            runtime=runtime,
        )
    ok = bool(result.get("ok"))
    assistant = result.get("assistant") if isinstance(result.get("assistant"), dict) else {}
    job_shape_fields = _resolve_job_shape_fields(spec)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "job_kind": "single_turn",
        "job_shape": str(job_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
        "job_id": str(spec.get("job_id") or ""),
        "run_id": run_id,
        "trigger": trigger,
        "status": "ok" if ok else "error",
        "role": role_meta["role"],
        "runtime_role": role_meta["runtime_role"],
        "binding": dict(spec.get("binding") or {}),
        "schedule": dict(spec.get("schedule") or {}),
        "expected_output": dict(spec.get("expected_output") or {}),
        "context_refs": list(spec.get("context_refs") or []),
        "required_context_bundle": str(spec.get("required_context_bundle") or ""),
        "additional_context_refs": list(spec.get("additional_context_refs") or []),
        "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
        "effective_context_refs": _effective_context_refs(spec),
        "runtime": runtime,
        "compiled_execution": compiled_shadow,
        "execution_contract": _scheduled_execution_contract(runtime, job_kind="single_turn"),
        "runtime_send": dict(runtime_send_meta or {}),
        "advisory_send": dict(advisory_send_meta or {}),
        "resolved_prompt": {
            "prompt_ref": str(spec.get("prompt_ref") or ""),
            "prompt_sha256": prompt_sha,
            "text": prompt_text,
        },
        "dispatched_message": dispatched_message,
        "dispatched_message_sha256": _sha256_text(dispatched_message),
        "compiled_dispatch": compiled_dispatch,
        "advisory_assistant": {
            "content": str(((advisory_result.get("assistant") if isinstance(advisory_result.get("assistant"), dict) else {}) or {}).get("content") or ""),
            "status": str(((advisory_result.get("assistant") if isinstance(advisory_result.get("assistant"), dict) else {}) or {}).get("status") or ""),
            "meta": dict(((advisory_result.get("assistant") if isinstance(advisory_result.get("assistant"), dict) else {}) or {}).get("meta") or {}),
            "normalized_payload": _normalized_advisory_backend_payload(
                str(((advisory_result.get("assistant") if isinstance(advisory_result.get("assistant"), dict) else {}) or {}).get("content") or "")
            ),
        },
        "assistant": {
            "content": str(assistant.get("content") or ""),
            "status": str(assistant.get("status") or ""),
            "meta": dict(assistant.get("meta") or {}),
        },
    }
    if not ok:
        receipt["error"] = _error_text_from_send_result(result)
    return ok, receipt, str(receipt.get("error") or "")


def _stage_payload(
    *,
    assistant: Dict[str, Any],
    runtime_send: Dict[str, Any],
    advisory_result: Dict[str, Any],
    advisory_send: Dict[str, Any],
) -> Dict[str, Any]:
    advisory_assistant = advisory_result.get("assistant") if isinstance(advisory_result.get("assistant"), dict) else {}
    return {
        "content": str((assistant or {}).get("content") or ""),
        "status": str((assistant or {}).get("status") or ""),
        "meta": dict((assistant or {}).get("meta") or {}),
        "runtime_send": dict(runtime_send or {}),
        "advisory": {
            "content": str((advisory_assistant or {}).get("content") or ""),
            "status": str((advisory_assistant or {}).get("status") or ""),
            "meta": dict((advisory_assistant or {}).get("meta") or {}),
            "runtime_send": dict(advisory_send or {}),
            "normalized_payload": _normalized_advisory_backend_payload(
                str((advisory_assistant or {}).get("content") or "")
            ),
        },
    }


def _append_iteration(
    *,
    iterations: List[Dict[str, Any]],
    iteration: int,
    builder_message: str,
    builder_payload: Dict[str, Any],
    verifier_message: str,
    verifier_payload: Dict[str, Any],
) -> None:
    iterations.append(
        {
            "iteration": iteration,
            "builder_message": builder_message,
            "builder": builder_payload,
            "verifier_message": verifier_message,
            "verifier": verifier_payload,
        }
    )


def _execute_swarm_launch_job(
    spec: Dict[str, Any],
    *,
    binding_override: Optional[Dict[str, Any]] = None,
    run_id: str,
    run_attempt: int = 1,
    trigger: str,
    max_runtime_seconds: int,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    from infra.controlplane import fanout_master_contract as _fanout_master_contract

    del run_attempt
    runtime = _normalize_runtime(spec.get("runtime"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    swarm = _normalize_swarm_launch(spec.get("swarm"))
    prompt_text = _resolved_prompt_text(spec, repo_root=repo_root)
    prompt_sha = _sha256_text(prompt_text)

    runtime_binding = dict(spec.get("binding") or {})
    if isinstance(binding_override, dict):
        if str(binding_override.get("task_id") or "").strip():
            runtime_binding["type"] = "task_id"
            runtime_binding["id"] = str(binding_override.get("task_id") or "").strip()
        elif str(binding_override.get("ledger_uid") or "").strip():
            runtime_binding["type"] = "ledger_uid"
            runtime_binding["id"] = str(binding_override.get("ledger_uid") or "").strip()

    launch_options = dict(swarm.get("launch_options") or {})
    run_async = bool(launch_options.pop("run_async", False))
    result = _agent_swarm.start_swarm(
        binding=runtime_binding,
        objective=str(swarm.get("objective") or ""),
        options=launch_options,
        run_async=run_async,
    )
    swarm_id = str(result.get("swarm_id") or "").strip()
    state = result.get("state") if isinstance(result.get("state"), dict) else {}
    terminal_statuses = {"finished", "partial", "failed", "cancelled", "timed_out", "interrupted"}
    deadline = time.monotonic() + max(1, int(max_runtime_seconds))
    while swarm_id and str(state.get("status") or "").strip().lower() not in terminal_statuses and time.monotonic() < deadline:
        time.sleep(0.05)
        polled = _agent_swarm.get_status(swarm_id=swarm_id)
        state = polled.get("state") if isinstance(polled.get("state"), dict) else state
    evaluation = _evaluate_swarm_launch_result(state=state, swarm=swarm)
    ok = bool(evaluation.get("ok"))
    integrated_result = (
        evaluation.get("integrated_result")
        if isinstance(evaluation.get("integrated_result"), dict)
        else {}
    )
    fanout_projection = _fanout_master_contract.project_fanout_state(
        state,
        as_terminal_report=True,
        integrated_result=integrated_result,
    )
    execution_contract = {
        "execution_mode": "swarm_launch",
        "compiled_execution_mode": "off",
        "controller_runtime": {},
        "advisory_runtime": {},
        "requested_backend_runtime": dict(runtime),
        "write_capable": True,
        "execution_owner": "swarm",
        "proof_owner": "scheduler",
        "retry_owner": "scheduler",
        "pipeline_executor_mode": "",
    }
    job_shape_fields = _resolve_job_shape_fields(spec)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "job_kind": "swarm_launch",
        "job_shape": str(job_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
        "job_id": str(spec.get("job_id") or ""),
        "run_id": run_id,
        "trigger": trigger,
        "status": ("ok" if ok else str(evaluation.get("status") or str(state.get("status") or "error"))),
        "role": "",
        "runtime_role": "",
        "binding": dict(spec.get("binding") or {}),
        "schedule": dict(spec.get("schedule") or {}),
        "expected_output": dict(spec.get("expected_output") or {}),
        "context_refs": list(spec.get("context_refs") or []),
        "required_context_bundle": str(spec.get("required_context_bundle") or ""),
        "additional_context_refs": list(spec.get("additional_context_refs") or []),
        "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
        "effective_context_refs": _effective_context_refs(spec),
        "runtime": runtime,
        "execution_contract": execution_contract,
        "resolved_prompt": {
            "prompt_ref": str(spec.get("prompt_ref") or ""),
            "prompt_sha256": prompt_sha,
            "text": prompt_text,
        },
        "swarm": {
            **dict(swarm),
            "swarm_id": swarm_id,
            "run_async": run_async,
            "launch_options_used": dict(launch_options),
            "final_status": str(state.get("status") or ""),
            "final_phase": str(state.get("phase") or ""),
            "fanout_projection": fanout_projection,
            "integrated_result": integrated_result,
            "token_usage": dict((state.get("token_usage") if isinstance(state.get("token_usage"), dict) else {}) or {}),
            "state": dict(state or {}),
            "evaluation": dict(evaluation),
        },
    }
    if not ok:
        receipt["error"] = str(evaluation.get("error") or f"swarm launch failed with status={str(state.get('status') or 'unknown')}")
    return ok, receipt, str(receipt.get("error") or "")
