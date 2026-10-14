def send_message(*, role: str, binding: Dict[str, Any], message: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    msg = str(message or "").strip()
    if not msg:
        raise ValueError("message is required")
    operator_intake = _capture_operator_intake_safe(
        role=r,
        binding=rr["binding"],
        message=msg,
        writer_prefix="agents.send_message",
    )
    if not bool(operator_intake.get("ok")) and str(operator_intake.get("reason") or "") not in {"", "casual_chat"}:
        return {
            "ok": False,
            "error": str(operator_intake.get("error") or "malformed operator intake"),
            "reason": (
                "malformed_input"
                if str(operator_intake.get("reason") or "") == "malformed_input"
                else str(operator_intake.get("reason") or "intake_capture_failed")
            ),
            "operator_intake": operator_intake,
            "binding": rr["binding"],
            "duplicate_suppressed": False,
            "resume_detected": False,
        }
    pb = load_prompt_bundle(
        r,
        rr["binding"],
        prompt_file=(options or {}).get("prompt_file"),
        workflow=bool((options or {}).get("workflow")),
    )

    sfile = _session_file(rr["chat_dir"], r)
    raw_model = str((options or {}).get("model") or "").strip()
    fallback_on_quota = bool((options or {}).get("fallback_on_quota", True))
    reasoning_effort = str((options or {}).get("reasoning_effort") or "").strip().lower()
    resolved_agent_tech = _resolve_agent_tech(options)
    model = raw_model
    if not model and resolved_agent_tech == "openai":
        model = _default_openai_chat_model()
    if not model and resolved_agent_tech == "claude":
        model = "claude-haiku-4-5"
    if not model and resolved_agent_tech == "gemini":        model = "gemini-2.5-pro"
    if not model and resolved_agent_tech == "xai":
        model = "grok-code-fast-1"
    if not model and resolved_agent_tech == "ollama":
        model = "qwen2.5:3b"
    if not model and resolved_agent_tech == "deepseek":
        model = "deepseek-chat"
    opts = dict(options or {})
    if resolved_agent_tech == "claude":
        raw_fb = opts.get("fallback_models")
        fb_models: List[str] = []
        if isinstance(raw_fb, list):
            for item in raw_fb:
                txt = str(item or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        elif isinstance(raw_fb, str):
            for part in str(raw_fb).split(","):
                txt = str(part or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        if "claude-sonnet-4-6" not in fb_models and model != "claude-sonnet-4-6":
            fb_models.append("claude-sonnet-4-6")
        opts["fallback_models"] = fb_models
    if resolved_agent_tech == "gemini":
        raw_fb = opts.get("fallback_models")
        fb_models = []
        if isinstance(raw_fb, list):
            for item in raw_fb:
                txt = str(item or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        elif isinstance(raw_fb, str):
            for part in str(raw_fb).split(","):
                txt = str(part or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        opts["fallback_models"] = fb_models
    if resolved_agent_tech == "xai":
        raw_fb = opts.get("fallback_models")
        fb_models = []
        if isinstance(raw_fb, list):
            for item in raw_fb:
                txt = str(item or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        elif isinstance(raw_fb, str):
            for part in str(raw_fb).split(","):
                txt = str(part or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        opts["fallback_models"] = fb_models
    if resolved_agent_tech == "ollama":
        raw_fb = opts.get("fallback_models")
        fb_models = []
        if isinstance(raw_fb, list):
            for item in raw_fb:
                txt = str(item or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        elif isinstance(raw_fb, str):
            for part in str(raw_fb).split(","):
                txt = str(part or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        opts["fallback_models"] = fb_models
    if resolved_agent_tech == "deepseek":
        raw_fb = opts.get("fallback_models")
        fb_models = []
        if isinstance(raw_fb, list):
            for item in raw_fb:
                txt = str(item or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        elif isinstance(raw_fb, str):
            for part in str(raw_fb).split(","):
                txt = str(part or "").strip()
                if txt and txt not in fb_models:
                    fb_models.append(txt)
        for candidate in ("deepseek-chat", "deepseek-reasoner"):
            if candidate not in fb_models and model != candidate:
                fb_models.append(candidate)
        opts["fallback_models"] = fb_models
    model_chain = _model_chain_from_options(opts, default_model=model)
    turn_opts = _turn_options(options)
    turn_claim = _lab_turns.begin_or_resume_turn(
        _repo_root(),
        binding=rr["binding"],
        role=r,
        message=msg,
        writer="agents.send_message",
        resend_key=turn_opts["resend_key"],
        resume_turn_id=turn_opts["resume_turn_id"],
        advisory_transport_id=turn_opts["transport_id"],
        resume_after_event_seq=turn_opts["resume_after_event_seq"],
        resume_content_chars=turn_opts["resume_content_chars"],
    )
    turn_record = dict(turn_claim["turn"])
    turn_id = str(turn_record["turn_id"])
    try:
        timeout_s = int((options or {}).get("timeout_s") or _default_codex_timeout_s())
    except Exception:
        timeout_s = _default_codex_timeout_s()
    if bool(turn_claim.get("duplicate_suppressed")):
        terminal = _lab_turns.wait_for_turn_terminal(_repo_root(), turn_id=turn_id, timeout_s=timeout_s)
        assistant_row = _latest_assistant_row_for_turn(sfile, turn_id=turn_id)
        if assistant_row is None:
            assistant_row = _reconcile_missing_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                turn=terminal,
                writer="agents.send_message.reconcile",
            )
        return _attach_operator_intake(
            payload={
                "ok": assistant_row is not None and terminal["state"] == "completed",
                "assistant": assistant_row,
                "session": _tail_rows(sfile, limit=80),
                "binding": rr["binding"],
                "duplicate_suppressed": True,
                "resume_detected": bool(turn_claim.get("resume_detected")),
                "turn": terminal,
            },
            operator_intake=operator_intake,
        )
    if bool(turn_claim.get("created")):
        user_row = {
            "schema_version": "agent_chat_message_v1",
            "utc": _utc_now_iso(),
            "role_panel": r,
            "sender": "user",
            "content": msg,
            "status": "ok",
            "binding": rr["binding"],
            "meta": {"turn_id": turn_id, **_plane_routed_meta(operator_intake)},
        }
        _append_jsonl(sfile, user_row)
        _refresh_mobile_bubble_projection(binding=rr["binding"], role=r, session_file=sfile)
    _lab_turns.transition_turn(
        _repo_root(),
        turn_id=turn_id,
        writer="agents.send_message",
        new_state="running",
        state_reason="direct turn executing",
        advisory_transport_id=turn_opts["transport_id"],
    )

    context_rows = _context_rows_for_turn(session_file=sfile, options=options)
    reply_ctx = _prepare_reply_prompt_context(
        role=r,
        binding=rr["binding"],
        message=msg,
        options=options,
        context_rows=context_rows,
        prompt_bundle=pb,
    )
    instructions_text = str(reply_ctx["instructions_text"] or "")
    pb = dict(pb)
    pb["instructions"] = instructions_text
    repo_ctx = {"files": list(reply_ctx.get("repo_context_files") or [])}
    model_input = str(reply_ctx["model_input"] or "")

    if resolved_agent_tech == "claude":
        selected_model = model_chain[0] if model_chain else model
        try:
            turn: Dict[str, Any] = {}
            last_error = ""
            for idx, model_name in enumerate(model_chain or [model]):
                selected_model = str(model_name or "").strip()
                try:
                    turn = _run_claude_oneshot(
                        model_input=model_input,
                        instructions=instructions_text,
                        model=selected_model,
                        reasoning_effort=reasoning_effort,
                        timeout_s=timeout_s,
                    )
                    if bool(turn.get("ok")):
                        break
                    last_error = str(turn.get("error") or "")
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    break
                except Exception as e:
                    last_error = str(e)
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    raise
            if not turn:
                raise RuntimeError(last_error or "claude runtime returned no result")
            ok = bool(turn.get("ok"))
            content = str(turn.get("content") or "")
            if not content:
                content = str(turn.get("error") or "claude runtime returned empty content")
            visible_content = _user_visible_assistant_content(content)
            chat_header_title = _extract_chat_header_title(content)
            terminal_state = _turn_terminal_state(ok=ok, error=str(turn.get("error") or ""))
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.claude",
                event_type="terminal",
                text=visible_content,
                terminal_state=terminal_state,
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            assistant_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": visible_content,
                "status": ("ok" if ok else "error"),
                "binding": rr["binding"],
                "meta": {
                    "model": str(selected_model or "claude-runtime"),
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "claude",
                    "claude_binary": str(turn.get("binary") or ""),
                    "transport": str(turn.get("transport") or ""),
                    "exit_code": int(turn.get("exit_code") or 0),
                    "request_payload_bytes": int(turn.get("request_payload_bytes") or 0),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "base_prompt_hash": str(pb["base"]["sha256"]),
                    "role_prompt_hash": str(pb["role"]["sha256"]),
                    "repo_context_enabled": True,
                    "repo_context_files": list(repo_ctx.get("files") or []),
                    "turn_id": turn_id,
                    "agent_tech": "claude",
                    **_plane_routed_meta(operator_intake),
                    **({"chat_header_title": chat_header_title} if chat_header_title else {}),
                    **({"raw_content": content} if visible_content != content else {}),
                },
            }
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.claude",
                assistant_row=assistant_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            assistant_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.claude",
                new_state=terminal_state,
                state_reason=(visible_content if ok else str(turn.get("error") or "claude_turn_failed"))[:240],
            )
            late_error = ""
            try:
                host_dispatch = _maybe_dispatch_direct_host_verification(
                    content=content,
                    role=r,
                    binding=rr["binding"],
                    session_file=sfile,
                )
            except Exception as e:
                host_dispatch = {"requested": False, "result": {}, "assistant_followup": {}}
                late_error = str(e)
            return _attach_operator_intake(
                payload={
                    "ok": ok,
                    "assistant": assistant_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                    "host_verification_requested": bool(host_dispatch.get("requested")),
                    "host_verification_result": dict(host_dispatch.get("result") or {}),
                    "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
                    **({"late_error": late_error} if late_error else {}),
                },
                operator_intake=operator_intake,
            )
        except Exception as e:
            current_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            if str(current_turn.get("state") or "") == "completed":
                session_rows = _tail_rows(sfile, limit=80)
                assistant_row = next(
                    (dict(row) for row in reversed(session_rows) if str(row.get("sender") or "") == "assistant"),
                    {
                        "schema_version": "agent_chat_message_v1",
                        "utc": _utc_now_iso(),
                        "role_panel": r,
                        "sender": "assistant",
                        "content": "",
                        "status": "ok",
                        "binding": rr["binding"],
                    },
                )
                return _attach_operator_intake(
                    payload={
                        "ok": True,
                        "assistant": assistant_row,
                        "session": session_rows,
                        "binding": rr["binding"],
                        "turn": current_turn,
                        "duplicate_suppressed": False,
                        "resume_detected": bool(turn_claim.get("resume_detected")),
                        "late_error": str(e),
                    },
                    operator_intake=operator_intake,
                )
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": f"[agent_error] {str(e)}",
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": selected_model or model,
                    "requested_model": model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "claude",
                    "transport": str((turn or {}).get("transport") or "stdin"),
                    "request_payload_bytes": int((turn or {}).get("request_payload_bytes") or _combined_prompt_bytes(instructions=str(pb["instructions"] or ""), model_input=model_input)),
                    "error": str(e),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "turn_id": turn_id,
                    "agent_tech": "claude",
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.claude",
                event_type="terminal",
                text=err_row["content"],
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.claude",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.claude",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            return _attach_operator_intake(
                payload={
                    "ok": False,
                    "assistant": err_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )

    if resolved_agent_tech == "gemini":
        selected_model = model_chain[0] if model_chain else model
        try:
            turn: Dict[str, Any] = {}
            last_error = ""
            for idx, model_name in enumerate(model_chain or [model]):
                selected_model = str(model_name or "").strip()
                try:
                    turn = _run_gemini_oneshot(
                        model_input=model_input,
                        instructions=instructions_text,
                        model=selected_model,
                        timeout_s=timeout_s,
                    )
                    if bool(turn.get("ok")):
                        break
                    last_error = str(turn.get("error") or "")
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    break
                except Exception as e:
                    last_error = str(e)
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    raise
            if not turn:
                raise RuntimeError(last_error or "gemini runtime returned no result")
            ok = bool(turn.get("ok"))
            content = str(turn.get("content") or "")
            if not content:
                content = str(turn.get("error") or "gemini runtime returned empty content")
            visible_content = _user_visible_assistant_content(content)
            chat_header_title = _extract_chat_header_title(content)
            terminal_state = _turn_terminal_state(ok=ok, error=str(turn.get("error") or ""))
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.gemini",
                event_type="terminal",
                text=visible_content,
                terminal_state=terminal_state,
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            assistant_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": visible_content,
                "status": ("ok" if ok else "error"),
                "binding": rr["binding"],
                "meta": {
                    "model": str(selected_model or "gemini-runtime"),
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "gemini",
                    "gemini_binary": str(turn.get("binary") or ""),
                    "transport": str(turn.get("transport") or ""),
                    "exit_code": int(turn.get("exit_code") or 0),
                    "request_payload_bytes": int(turn.get("request_payload_bytes") or 0),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "base_prompt_hash": str(pb["base"]["sha256"]),
                    "role_prompt_hash": str(pb["role"]["sha256"]),
                    "repo_context_enabled": True,
                    "repo_context_files": list(repo_ctx.get("files") or []),
                    "turn_id": turn_id,
                    "agent_tech": "gemini",
                    **({"chat_header_title": chat_header_title} if chat_header_title else {}),
                    **({"raw_content": content} if visible_content != content else {}),
                },
            }
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.gemini",
                assistant_row=assistant_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            assistant_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.gemini",
                new_state=terminal_state,
                state_reason=(visible_content if ok else str(turn.get("error") or "gemini_turn_failed"))[:240],
            )
            late_error = ""
            try:
                host_dispatch = _maybe_dispatch_direct_host_verification(
                    content=content,
                    role=r,
                    binding=rr["binding"],
                    session_file=sfile,
                )
            except Exception as e:
                host_dispatch = {"requested": False, "result": {}, "assistant_followup": {}}
                late_error = str(e)
            return _attach_operator_intake(
                payload={
                    "ok": ok,
                    "assistant": assistant_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                    "host_verification_requested": bool(host_dispatch.get("requested")),
                    "host_verification_result": dict(host_dispatch.get("result") or {}),
                    "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
                    **({"late_error": late_error} if late_error else {}),
                },
                operator_intake=operator_intake,
            )
        except Exception as e:
            current_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            if str(current_turn.get("state") or "") == "completed":
                session_rows = _tail_rows(sfile, limit=80)
                assistant_row = next(
                    (dict(row) for row in reversed(session_rows) if str(row.get("sender") or "") == "assistant"),
                    {
                        "schema_version": "agent_chat_message_v1",
                        "utc": _utc_now_iso(),
                        "role_panel": r,
                        "sender": "assistant",
                        "content": "",
                        "status": "ok",
                        "binding": rr["binding"],
                    },
                )
                return _attach_operator_intake(
                    payload={
                        "ok": True,
                        "assistant": assistant_row,
                        "session": session_rows,
                        "binding": rr["binding"],
                        "turn": current_turn,
                        "duplicate_suppressed": False,
                        "resume_detected": bool(turn_claim.get("resume_detected")),
                        "late_error": str(e),
                    },
                    operator_intake=operator_intake,
                )
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": f"[agent_error] {str(e)}",
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": selected_model or model,
                    "requested_model": model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "gemini",
                    "transport": str((turn or {}).get("transport") or ""),
                    "request_payload_bytes": int((turn or {}).get("request_payload_bytes") or 0),
                    "error": str(e),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "turn_id": turn_id,
                    "agent_tech": "gemini",
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.gemini",
                event_type="terminal",
                text=err_row["content"],
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.gemini",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.gemini",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            return _attach_operator_intake(
                payload={
                    "ok": False,
                    "assistant": err_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )

    if resolved_agent_tech == "xai":
        selected_model = model_chain[0] if model_chain else model
        try:
            turn: Dict[str, Any] = {}
            last_error = ""
            for idx, model_name in enumerate(model_chain or [model]):
                selected_model = str(model_name or "").strip()
                try:
                    turn = _run_xai_oneshot(
                        model_input=model_input,
                        instructions=instructions_text,
                        model=selected_model,
                        timeout_s=timeout_s,
                    )
                    if bool(turn.get("ok")):
                        break
                    last_error = str(turn.get("error") or "")
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    break
                except Exception as e:
                    last_error = str(e)
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    raise
            if not turn:
                raise RuntimeError(last_error or "xai runtime returned no result")
            ok = bool(turn.get("ok"))
            content = str(turn.get("content") or "")
            if not content:
                content = str(turn.get("error") or "xai runtime returned empty content")
            visible_content = _user_visible_assistant_content(content)
            chat_header_title = _extract_chat_header_title(content)
            terminal_state = _turn_terminal_state(ok=ok, error=str(turn.get("error") or ""))
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.xai",
                event_type="terminal",
                text=visible_content,
                terminal_state=terminal_state,
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            assistant_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": visible_content,
                "status": ("ok" if ok else "error"),
                "binding": rr["binding"],
                "meta": {
                    "model": str(selected_model or "xai-runtime"),
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "xai",
                    "transport": str(turn.get("transport") or ""),
                    "xai_binary": str(turn.get("binary") or ""),
                    "exit_code": int(turn.get("exit_code") or 0),
                    "request_payload_bytes": int(turn.get("request_payload_bytes") or 0),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "base_prompt_hash": str(pb["base"]["sha256"]),
                    "role_prompt_hash": str(pb["role"]["sha256"]),
                    "repo_context_enabled": True,
                    "repo_context_files": list(repo_ctx.get("files") or []),
                    "turn_id": turn_id,
                    "agent_tech": "xai",
                    **({"chat_header_title": chat_header_title} if chat_header_title else {}),
                    **({"raw_content": content} if visible_content != content else {}),
                },
            }
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.xai",
                assistant_row=assistant_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            assistant_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.xai",
                new_state=terminal_state,
                state_reason=(visible_content if ok else str(turn.get("error") or "xai_turn_failed"))[:240],
            )
            host_dispatch = _maybe_dispatch_direct_host_verification(
                content=content,
                role=r,
                binding=rr["binding"],
                session_file=sfile,
            )
            return _attach_operator_intake(
                payload={
                    "ok": ok,
                    "assistant": assistant_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                    "host_verification_requested": bool(host_dispatch.get("requested")),
                    "host_verification_result": dict(host_dispatch.get("result") or {}),
                    "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
                },
                operator_intake=operator_intake,
            )
        except Exception as e:
            current_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            if str(current_turn.get("state") or "") == "completed":
                session_rows = _tail_rows(sfile, limit=80)
                assistant_row = next(
                    (dict(row) for row in reversed(session_rows) if str(row.get("sender") or "") == "assistant"),
                    {
                        "schema_version": "agent_chat_message_v1",
                        "utc": _utc_now_iso(),
                        "role_panel": r,
                        "sender": "assistant",
                        "content": "",
                        "status": "ok",
                        "binding": rr["binding"],
                    },
                )
                return _attach_operator_intake(
                    payload={
                        "ok": True,
                        "assistant": assistant_row,
                        "session": session_rows,
                        "binding": rr["binding"],
                        "turn": current_turn,
                        "duplicate_suppressed": False,
                        "resume_detected": bool(turn_claim.get("resume_detected")),
                        "late_error": str(e),
                    },
                    operator_intake=operator_intake,
                )
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": f"[agent_error] {str(e)}",
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": selected_model or model,
                    "requested_model": model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "xai",
                    "transport": str((turn or {}).get("transport") or "temp_file_http_body"),
                    "request_payload_bytes": int((turn or {}).get("request_payload_bytes") or 0),
                    "error": str(e),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "turn_id": turn_id,
                    "agent_tech": "xai",
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.xai",
                event_type="terminal",
                text=err_row["content"],
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.xai",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.xai",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            return _attach_operator_intake(
                payload={
                    "ok": False,
                    "assistant": err_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )

    if resolved_agent_tech == "ollama":
        selected_model = model_chain[0] if model_chain else model
        def _estimate_ollama_payload_bytes(model_name: str) -> int:
            payload = {
                "model": str(model_name or "").strip() or "qwen2.5:3b",
                "stream": False,
                "messages": [
                    {"role": "system", "content": str(pb["instructions"] or "").strip()},
                    {"role": "user", "content": str(model_input or "").strip()},
                ],
            }
            return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        try:
            turn: Dict[str, Any] = {}
            last_error = ""
            for idx, model_name in enumerate(model_chain or [model]):
                selected_model = str(model_name or "").strip()
                try:
                    turn = _run_ollama_oneshot(
                        model_input=model_input,
                        instructions=str(pb["instructions"] or ""),
                        model=selected_model,
                        timeout_s=timeout_s,
                    )
                    if bool(turn.get("ok")):
                        break
                    last_error = str(turn.get("error") or "")
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    break
                except Exception as e:
                    last_error = str(e)
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    raise
            if not turn:
                raise RuntimeError(last_error or "ollama runtime returned no result")
            ok = bool(turn.get("ok"))
            content = str(turn.get("content") or "")
            if not content:
                content = str(turn.get("error") or "ollama runtime returned empty content")
            visible_content = _user_visible_assistant_content(content)
            chat_header_title = _extract_chat_header_title(content)
            terminal_state = _turn_terminal_state(ok=ok, error=str(turn.get("error") or ""))
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.ollama",
                event_type="terminal",
                text=visible_content,
                terminal_state=terminal_state,
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            assistant_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": visible_content,
                "status": ("ok" if ok else "error"),
                "binding": rr["binding"],
                "meta": {
                    "model": str(selected_model or "ollama-runtime"),
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "ollama",
                    "ollama_base_url": str(turn.get("base_url") or ""),
                    "transport": str(turn.get("transport") or ""),
                    "exit_code": int(turn.get("exit_code") or 0),
                    "request_payload_bytes": int(
                        turn.get("request_payload_bytes")
                        or _estimate_ollama_payload_bytes(selected_model)
                    ),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "base_prompt_hash": str(pb["base"]["sha256"]),
                    "role_prompt_hash": str(pb["role"]["sha256"]),
                    "repo_context_enabled": True,
                    "repo_context_files": list(repo_ctx.get("files") or []),
                    "turn_id": turn_id,
                    "agent_tech": "ollama",
                    **({"chat_header_title": chat_header_title} if chat_header_title else {}),
                    **({"raw_content": content} if visible_content != content else {}),
                },
            }
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.ollama",
                assistant_row=assistant_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            assistant_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.ollama",
                new_state=terminal_state,
                state_reason=(visible_content if ok else str(turn.get("error") or "ollama_turn_failed"))[:240],
            )
            host_dispatch = _maybe_dispatch_direct_host_verification(
                content=content,
                role=r,
                binding=rr["binding"],
                session_file=sfile,
            )
            return _attach_operator_intake(
                payload={
                    "ok": ok,
                    "assistant": assistant_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                    "host_verification_requested": bool(host_dispatch.get("requested")),
                    "host_verification_result": dict(host_dispatch.get("result") or {}),
                    "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
                },
                operator_intake=operator_intake,
            )
        except Exception as e:
            current_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            if str(current_turn.get("state") or "") == "completed":
                session_rows = _tail_rows(sfile, limit=80)
                assistant_row = next(
                    (dict(row) for row in reversed(session_rows) if str(row.get("sender") or "") == "assistant"),
                    {
                        "schema_version": "agent_chat_message_v1",
                        "utc": _utc_now_iso(),
                        "role_panel": r,
                        "sender": "assistant",
                        "content": "",
                        "status": "ok",
                        "binding": rr["binding"],
                    },
                )
                return _attach_operator_intake(
                    payload={
                        "ok": True,
                        "assistant": assistant_row,
                        "session": session_rows,
                        "binding": rr["binding"],
                        "turn": current_turn,
                        "duplicate_suppressed": False,
                        "resume_detected": bool(turn_claim.get("resume_detected")),
                        "late_error": str(e),
                    },
                    operator_intake=operator_intake,
                )
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": f"[agent_error] {str(e)}",
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": selected_model or model,
                    "requested_model": model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "ollama",
                    "transport": str((turn or {}).get("transport") or "temp_file_http_body"),
                    "error": str(e),
                    "request_payload_bytes": int(_estimate_ollama_payload_bytes(selected_model or model)),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "turn_id": turn_id,
                    "agent_tech": "ollama",
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.ollama",
                event_type="terminal",
                text=err_row["content"],
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.ollama",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.ollama",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            return _attach_operator_intake(
                payload={
                    "ok": False,
                    "assistant": err_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )

    if resolved_agent_tech == "deepseek":
        selected_model = model_chain[0] if model_chain else model

        def _estimate_deepseek_payload_bytes(model_name: str) -> int:
            payload = {
                "model": str(model_name or "").strip() or "deepseek-chat",
                "stream": False,
                "messages": [
                    {"role": "system", "content": str(pb["instructions"] or "").strip()},
                    {"role": "user", "content": str(model_input or "").strip()},
                ],
            }
            return len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))

        try:
            turn: Dict[str, Any] = {}
            last_error = ""
            for idx, model_name in enumerate(model_chain or [model]):
                selected_model = str(model_name or "").strip()
                try:
                    turn = _run_deepseek_oneshot(
                        model_input=model_input,
                        instructions=str(pb["instructions"] or ""),
                        model=selected_model,
                        timeout_s=timeout_s,
                    )
                    if bool(turn.get("ok")):
                        break
                    last_error = str(turn.get("error") or "")
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    break
                except Exception as e:
                    last_error = str(e)
                    if (
                        fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    raise
            if not turn:
                raise RuntimeError(last_error or "deepseek runtime returned no result")
            ok = bool(turn.get("ok"))
            content = str(turn.get("content") or "")
            if not content:
                content = str(turn.get("error") or "deepseek runtime returned empty content")
            visible_content = _user_visible_assistant_content(content)
            chat_header_title = _extract_chat_header_title(content)
            terminal_state = _turn_terminal_state(ok=ok, error=str(turn.get("error") or ""))
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.deepseek",
                event_type="terminal",
                text=visible_content,
                terminal_state=terminal_state,
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            assistant_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": visible_content,
                "status": ("ok" if ok else "error"),
                "binding": rr["binding"],
                "meta": {
                    "model": str(selected_model or "deepseek-runtime"),
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "deepseek",
                    "deepseek_base_url": str(turn.get("base_url") or ""),
                    "transport": str(turn.get("transport") or ""),
                    "exit_code": int(turn.get("exit_code") or 0),
                    "request_payload_bytes": int(
                        turn.get("request_payload_bytes")
                        or _estimate_deepseek_payload_bytes(selected_model)
                    ),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "base_prompt_hash": str(pb["base"]["sha256"]),
                    "role_prompt_hash": str(pb["role"]["sha256"]),
                    "repo_context_enabled": True,
                    "repo_context_files": list(repo_ctx.get("files") or []),
                    "turn_id": turn_id,
                    "agent_tech": "deepseek",
                    **({"chat_header_title": chat_header_title} if chat_header_title else {}),
                    **({"raw_content": content} if visible_content != content else {}),
                },
            }
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.deepseek",
                assistant_row=assistant_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            assistant_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.deepseek",
                new_state=terminal_state,
                state_reason=(visible_content if ok else str(turn.get("error") or "deepseek_turn_failed"))[:240],
            )
            host_dispatch = _maybe_dispatch_direct_host_verification(
                content=content,
                role=r,
                binding=rr["binding"],
                session_file=sfile,
            )
            return _attach_operator_intake(
                payload={
                    "ok": ok,
                    "assistant": assistant_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                    "host_verification_requested": bool(host_dispatch.get("requested")),
                    "host_verification_result": dict(host_dispatch.get("result") or {}),
                    "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
                },
                operator_intake=operator_intake,
            )
        except Exception as e:
            current_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            if str(current_turn.get("state") or "") == "completed":
                session_rows = _tail_rows(sfile, limit=80)
                assistant_row = next(
                    (dict(row) for row in reversed(session_rows) if str(row.get("sender") or "") == "assistant"),
                    {
                        "schema_version": "agent_chat_message_v1",
                        "utc": _utc_now_iso(),
                        "role_panel": r,
                        "sender": "assistant",
                        "content": "",
                        "status": "ok",
                        "binding": rr["binding"],
                    },
                )
                return _attach_operator_intake(
                    payload={
                        "ok": True,
                        "assistant": assistant_row,
                        "session": session_rows,
                        "binding": rr["binding"],
                        "turn": current_turn,
                        "duplicate_suppressed": False,
                        "resume_detected": bool(turn_claim.get("resume_detected")),
                        "late_error": str(e),
                    },
                    operator_intake=operator_intake,
                )
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": f"[agent_error] {str(e)}",
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": selected_model or model,
                    "requested_model": model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "deepseek",
                    "transport": str((turn or {}).get("transport") or "temp_file_http_body"),
                    "error": str(e),
                    "request_payload_bytes": int(_estimate_deepseek_payload_bytes(selected_model or model)),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "turn_id": turn_id,
                    "agent_tech": "deepseek",
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.deepseek",
                event_type="terminal",
                text=err_row["content"],
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.deepseek",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.deepseek",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            return _attach_operator_intake(
                payload={
                    "ok": False,
                    "assistant": err_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )

    if resolved_agent_tech == "codex":
        selected_model = model_chain[0] if model_chain else model
        turn: Dict[str, Any] = {}
        start_info: Dict[str, Any] = {}
        surface_run = {"ok": False, "launched": False, "task_id": "", "run_id": turn_id}
        force_codex_backend = bool(opts.get("_scheduled_turn")) or bool(opts.get("_force_codex_backend"))
        try:
            with _forced_agents_backend("codex" if force_codex_backend else ""):
                mgr = _codex_manager()
                start_info = mgr.ensure_worker(
                    binding=rr["binding"],
                    role=r,
                    bootstrap_text=str(pb["instructions"] or ""),
                )
                surface_run = _launch_codex_surface_run(
                    binding=rr["binding"],
                    role=r,
                    operator_intake=operator_intake,
                    turn_id=turn_id,
                    worker_id=str(start_info.get("worker_id") or ""),
                )
                last_error = ""
                for idx, model_name in enumerate(model_chain or [model]):
                    selected_model = str(model_name or "").strip()
                    try:
                        turn = mgr.send_turn(
                            binding=rr["binding"],
                            role=r,
                            message=model_input,
                            timeout_s=timeout_s,
                            model=selected_model,
                            reasoning_effort=reasoning_effort,
                        )
                        quota_error = str(turn.get("error") or turn.get("content") or "")
                        if (
                            not bool(turn.get("ok"))
                            and fallback_on_quota
                            and _looks_like_quota_or_model_unavailable(quota_error)
                            and idx < len(model_chain) - 1
                        ):
                            last_error = quota_error
                            continue
                        break
                    except CodexRuntimeError as e:
                        last_error = str(e)
                        if (
                            fallback_on_quota
                            and _looks_like_quota_or_model_unavailable(last_error)
                            and idx < len(model_chain) - 1
                        ):
                            continue
                        raise
                    except Exception as e:
                        last_error = str(e)
                        if (
                            fallback_on_quota
                            and _looks_like_quota_or_model_unavailable(last_error)
                            and idx < len(model_chain) - 1
                        ):
                            continue
                        raise
            if not turn:
                raise CodexRuntimeError(last_error or "codex runtime returned no result")
            ok = bool(turn.get("ok"))
            content = str(turn.get("content") or "")
            if not content:
                content = str(turn.get("error") or "codex runtime returned empty content")
            visible_content = _user_visible_assistant_content(content)
            chat_header_title = _extract_chat_header_title(content)
            terminal_state = _turn_terminal_state(ok=ok, error=str(turn.get("error") or ""))
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.codex",
                event_type="terminal",
                text=visible_content,
                terminal_state=terminal_state,
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            assistant_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": visible_content,
                "status": ("ok" if ok else "error"),
                "binding": rr["binding"],
                "meta": {
                    "model": "codex-runtime",
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "codex",
                    "agent_tech": "codex",
                    "worker_id": str(turn.get("worker_id") or start_info.get("worker_id") or ""),
                    "transport": str(turn.get("protocol") or "runtime_worker"),
                    "protocol": str(turn.get("protocol") or ""),
                    "request_payload_bytes": int(_combined_prompt_bytes(instructions=str(pb["instructions"] or ""), model_input=model_input)),
                    "exit_code": int(turn.get("exit_code") or 0),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "base_prompt_hash": str(pb["base"]["sha256"]),
                    "role_prompt_hash": str(pb["role"]["sha256"]),
                    "repo_context_enabled": True,
                    "repo_context_files": list(repo_ctx.get("files") or []),
                    "turn_id": turn_id,
                    **_plane_routed_meta(operator_intake),
                    **({"chat_header_title": chat_header_title} if chat_header_title else {}),
                    **({"raw_content": content} if visible_content != content else {}),
                },
            }
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.codex",
                assistant_row=assistant_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            assistant_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.codex",
                new_state=terminal_state,
                state_reason=(visible_content if ok else str(turn.get("error") or "codex_turn_failed"))[:240],
            )
            if bool(surface_run.get("launched")):
                _write_codex_surface_checkpoint(
                    task_id=str(surface_run.get("task_id") or ""),
                    run_id=str(surface_run.get("run_id") or ""),
                    ok=ok,
                    terminal_state=terminal_state,
                    summary_text=(visible_content if ok else str(turn.get("error") or visible_content or "codex_turn_failed")),
                    worker_id=str(turn.get("worker_id") or start_info.get("worker_id") or ""),
                    role=r,
                    binding=rr["binding"],
                    session_file=sfile,
                )
            host_dispatch = _maybe_dispatch_direct_host_verification(
                content=content,
                role=r,
                binding=rr["binding"],
                session_file=sfile,
            )
            return _attach_operator_intake(
                payload={
                    "ok": ok,
                    "assistant": assistant_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                    "host_verification_requested": bool(host_dispatch.get("requested")),
                    "host_verification_result": dict(host_dispatch.get("result") or {}),
                    "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
                },
                operator_intake=operator_intake,
            )
        except CodexRuntimeError as e:
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": f"[agent_error] {str(e)}",
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": "codex-runtime",
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "codex",
                    "agent_tech": "codex",
                    "transport": str((turn or {}).get("protocol") or "runtime_worker"),
                    "request_payload_bytes": int(_combined_prompt_bytes(instructions=str(pb["instructions"] or ""), model_input=model_input)),
                    "error": str(e),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "turn_id": turn_id,
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.codex",
                event_type="terminal",
                text=err_row["content"],
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.codex",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.codex",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            if bool(surface_run.get("launched")):
                _write_codex_surface_checkpoint(
                    task_id=str(surface_run.get("task_id") or ""),
                    run_id=str(surface_run.get("run_id") or ""),
                    ok=False,
                    terminal_state="failed",
                    summary_text=str(e),
                    worker_id=str((turn or {}).get("worker_id") or start_info.get("worker_id") or ""),
                    role=r,
                    binding=rr["binding"],
                    session_file=sfile,
                )
            return _attach_operator_intake(
                payload={
                    "ok": False,
                    "assistant": err_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )
        except Exception as e:
            current_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            if str(current_turn.get("state") or "") == "completed":
                session_rows = _tail_rows(sfile, limit=80)
                assistant_row = next(
                    (dict(row) for row in reversed(session_rows) if str(row.get("sender") or "") == "assistant"),
                    {
                        "schema_version": "agent_chat_message_v1",
                        "utc": _utc_now_iso(),
                        "role_panel": r,
                        "sender": "assistant",
                        "content": "",
                        "status": "ok",
                        "binding": rr["binding"],
                    },
                )
                return _attach_operator_intake(
                    payload={
                        "ok": True,
                        "assistant": assistant_row,
                        "session": session_rows,
                        "binding": rr["binding"],
                        "turn": current_turn,
                        "duplicate_suppressed": False,
                        "resume_detected": bool(turn_claim.get("resume_detected")),
                        "late_error": str(e),
                    },
                    operator_intake=operator_intake,
                )
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": f"[agent_error] {str(e)}",
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": "codex-runtime",
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "backend": "codex",
                    "agent_tech": "codex",
                    "transport": str((turn or {}).get("protocol") or "runtime_worker"),
                    "request_payload_bytes": int(_combined_prompt_bytes(instructions=str(pb["instructions"] or ""), model_input=model_input)),
                    "error": str(e),
                    "prompt_hash": _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8")),
                    "turn_id": turn_id,
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.codex",
                event_type="terminal",
                text=err_row["content"],
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message.codex",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message.codex",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            if bool(surface_run.get("launched")):
                _write_codex_surface_checkpoint(
                    task_id=str(surface_run.get("task_id") or ""),
                    run_id=str(surface_run.get("run_id") or ""),
                    ok=False,
                    terminal_state="failed",
                    summary_text=str(e),
                    worker_id=str((turn or {}).get("worker_id") or start_info.get("worker_id") or ""),
                    role=r,
                    binding=rr["binding"],
                    session_file=sfile,
                )
            return _attach_operator_intake(
                payload={
                    "ok": False,
                    "assistant": err_row,
                    "session": _tail_rows(sfile, limit=80),
                    "binding": rr["binding"],
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )

    try:
        resp: Dict[str, Any] = {}
        selected_model = model_chain[0] if model_chain else model
        last_error = ""
        for idx, model_name in enumerate(model_chain or [model]):
            selected_model = str(model_name or "").strip()
            try:
                resp = create_response(
                    model=selected_model,
                    input_text=model_input,
                    instructions=str(pb["instructions"] or ""),
                    reasoning_effort=reasoning_effort,
                )
                break
            except Exception as e:
                last_error = str(e)
                if (
                    fallback_on_quota
                    and _looks_like_quota_or_model_unavailable(last_error)
                    and idx < len(model_chain) - 1
                ):
                    continue
                raise
        if not resp:
            raise RuntimeError(last_error or "llm runtime returned no result")
        content = extract_output_text(resp)
        if not content:
            content = json.dumps(resp, ensure_ascii=False)
        visible_content = _user_visible_assistant_content(content)
        chat_header_title = _extract_chat_header_title(content)
        _lab_turns.append_turn_output_event(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message.llm",
            event_type="terminal",
            text=visible_content,
            terminal_state="completed",
        )
        terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
        assistant_row = {
            "schema_version": "agent_chat_message_v1",
            "utc": _utc_now_iso(),
            "role_panel": r,
            "sender": "assistant",
            "content": visible_content,
            "status": "ok",
            "binding": rr["binding"],
            "meta": {
                "model": selected_model,
                "requested_model": model,
                "fallback_models": list(model_chain[1:]),
                "reasoning_effort": reasoning_effort,
                "backend": "openai",
                "agent_tech": "openai",
                "transport": "sdk_http_body",
                "request_payload_bytes": int(_combined_prompt_bytes(instructions=instructions_text, model_input=model_input)),
                "prompt_hash": _sha256_hex_bytes(instructions_text.encode("utf-8")),
                "base_prompt_hash": str(pb["base"]["sha256"]),
                "role_prompt_hash": str(pb["role"]["sha256"]),
                "repo_context_enabled": bool(reply_ctx.get("repo_context_enabled")),
                "repo_context_files": list(reply_ctx.get("repo_context_files") or []),
                "reply_route_mode": str(reply_ctx.get("route_mode") or ""),
                "reply_route_reasons": list(reply_ctx.get("route_reasons") or []),
                "pipeline_context_used": bool(reply_ctx.get("pipeline_context_used")),
                "context_cache_hit": bool(reply_ctx.get("cache_hit")),
                "likely_tests": list(reply_ctx.get("likely_tests") or []),
                "pis_binding": dict(reply_ctx.get("pis_binding") or {}),
                **({"pipeline_context_error": str(reply_ctx.get("pipeline_error") or "")} if str(reply_ctx.get("pipeline_error") or "") else {}),
                "turn_id": turn_id,
                **({"chat_header_title": chat_header_title} if chat_header_title else {}),
                **({"raw_content": content} if visible_content != content else {}),
            },
        }
        commit = _commit_turn_transcript_row(
            role=r,
            binding=rr["binding"],
            session_file=sfile,
            turn_id=turn_id,
            writer="agents.send_message.llm",
            assistant_row=assistant_row,
            turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
        )
        assistant_row = dict(commit["row"])
        _lab_turns.transition_turn(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message.llm",
            new_state="completed",
            state_reason=visible_content[:240],
        )
        host_dispatch = _maybe_dispatch_direct_host_verification(
            content=content,
            role=r,
            binding=rr["binding"],
            session_file=sfile,
        )
        return _attach_operator_intake(
            payload={
                "ok": True,
                "assistant": assistant_row,
                "session": _tail_rows(sfile, limit=80),
                "binding": rr["binding"],
                "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                "duplicate_suppressed": False,
                "resume_detected": bool(turn_claim.get("resume_detected")),
                "host_verification_requested": bool(host_dispatch.get("requested")),
                "host_verification_result": dict(host_dispatch.get("result") or {}),
                "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
            },
            operator_intake=operator_intake,
        )
    except Exception as e:
        err_row = {
            "schema_version": "agent_chat_message_v1",
            "utc": _utc_now_iso(),
            "role_panel": r,
            "sender": "assistant",
            "content": f"[agent_error] {str(e)}",
            "status": "error",
            "binding": rr["binding"],
            "meta": {
                "model": model,
                "fallback_models": list(model_chain[1:]),
                "reasoning_effort": reasoning_effort,
                "backend": "openai",
                "agent_tech": "openai",
                "transport": "sdk_http_body",
                "request_payload_bytes": int(_combined_prompt_bytes(instructions=instructions_text, model_input=model_input)),
                "error": str(e),
                "prompt_hash": _sha256_hex_bytes(instructions_text.encode("utf-8")),
                "reply_route_mode": str(reply_ctx.get("route_mode") or ""),
                "reply_route_reasons": list(reply_ctx.get("route_reasons") or []),
                "turn_id": turn_id,
            },
        }
        _lab_turns.append_turn_output_event(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message.llm",
            event_type="terminal",
            text=err_row["content"],
            terminal_state="failed",
        )
        terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
        commit = _commit_turn_transcript_row(
            role=r,
            binding=rr["binding"],
            session_file=sfile,
            turn_id=turn_id,
            writer="agents.send_message.llm",
            assistant_row=err_row,
            turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
        )
        err_row = dict(commit["row"])
        _lab_turns.transition_turn(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message.llm",
            new_state="failed",
            state_reason=str(e)[:240],
        )
        return _attach_operator_intake(
            payload={
                "ok": False,
                "assistant": err_row,
                "session": _tail_rows(sfile, limit=80),
                "binding": rr["binding"],
                "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                "duplicate_suppressed": False,
                "resume_detected": bool(turn_claim.get("resume_detected")),
            },
            operator_intake=operator_intake,
        )
