_DEFAULT_SEND_MESSAGE_IMPL = send_message


def send_message_stream_events(
    *,
    role: str,
    binding: Dict[str, Any],
    message: str,
    options: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    msg = str(message or "").strip()
    if not msg:
        raise ValueError("message is required")
    resolved_agent_tech = _resolve_agent_tech(options)
    legacy_send_override = callable(globals().get("send_message")) and globals().get("send_message") is not _DEFAULT_SEND_MESSAGE_IMPL
    if resolved_agent_tech not in {"openai", "codex"} or legacy_send_override:
        result = send_message(role=r, binding=rr["binding"], message=msg, options=options)
        turn = result.get("turn") if isinstance(result.get("turn"), dict) else {}
        turn_id = str(turn.get("turn_id") or "")
        started_utc = str(turn.get("started_utc") or _utc_now_iso())
        assistant = result.get("assistant") if isinstance(result.get("assistant"), dict) else {}
        content = str(assistant.get("content") or "")
        yield {
            "type": "start",
            "role": r,
            "binding": rr["binding"],
            "started_utc": started_utc,
            "turn_id": turn_id,
        }
        if content:
            yield {
                "type": "delta",
                "delta": content,
                "content": content,
                "turn_id": turn_id,
            }
        done_payload = dict(result)
        done_payload["type"] = "done"
        yield done_payload
        return
    operator_intake = _capture_operator_intake_safe(
        role=r,
        binding=rr["binding"],
        message=msg,
        writer_prefix="agents.send_message_stream",
    )
    if not bool(operator_intake.get("ok")) and str(operator_intake.get("reason") or "") not in {"", "casual_chat"}:
        yield _attach_operator_intake(
            payload={
                "type": "done",
                "ok": False,
                "error": str(operator_intake.get("error") or "malformed operator intake"),
                "reason": (
                    "malformed_input"
                    if str(operator_intake.get("reason") or "") == "malformed_input"
                    else str(operator_intake.get("reason") or "intake_capture_failed")
                ),
                "binding": rr["binding"],
                "duplicate_suppressed": False,
                "resume_detected": False,
            },
            operator_intake=operator_intake,
        )
        return
    prompt_kwargs = {
        "prompt_file": (options or {}).get("prompt_file"),
        "workflow": bool((options or {}).get("workflow")),
    }
    if (options or {}).get("role_prompt_text") is not None:
        prompt_kwargs["role_prompt_text"] = (options or {}).get("role_prompt_text")
    pb = load_prompt_bundle(r, rr["binding"], **prompt_kwargs)

    sfile = _session_file(rr["chat_dir"], r)
    raw_model = str((options or {}).get("model") or "").strip()
    model = raw_model
    if not model and resolved_agent_tech == "openai":
        model = _default_openai_chat_model()
    if not model and resolved_agent_tech == "claude":
        model = "claude-haiku-4-5"
    if not model and resolved_agent_tech == "gemini":
        model = "gemini-2.5-pro"
    if not model and resolved_agent_tech == "xai":
        model = "grok-code-fast-1"
    if not model and resolved_agent_tech == "ollama":
        model = "qwen2.5:3b"
    if not model and resolved_agent_tech == "deepseek":
        model = "deepseek-chat"
    fallback_on_quota = bool((options or {}).get("fallback_on_quota", True))
    model_chain = _model_chain_from_options(options, default_model=model)
    reasoning_effort = str((options or {}).get("reasoning_effort") or "").strip().lower()
    turn_opts = _turn_options(options)
    turn_claim = _lab_turns.begin_or_resume_turn(
        _repo_root(),
        binding=rr["binding"],
        role=r,
        message=msg,
        writer="agents.send_message_stream",
        resend_key=turn_opts["resend_key"],
        resume_turn_id=turn_opts["resume_turn_id"],
        advisory_transport_id=turn_opts["transport_id"],
        resume_after_event_seq=turn_opts["resume_after_event_seq"],
        resume_content_chars=turn_opts["resume_content_chars"],
    )
    turn_record = dict(turn_claim["turn"])
    turn_id = str(turn_record["turn_id"])
    if bool(turn_claim.get("duplicate_suppressed")):
        terminal = _lab_turns.wait_for_turn_terminal(_repo_root(), turn_id=turn_id, timeout_s=_default_codex_timeout_s())
        assistant_row = _latest_assistant_row_for_turn(sfile, turn_id=turn_id)
        if assistant_row is None:
            assistant_row = _reconcile_missing_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                turn=terminal,
                writer="agents.send_message_stream.reconcile",
            )
        yield _attach_operator_intake(
            payload={
                "type": "done",
                "ok": assistant_row is not None and terminal["state"] == "completed",
                "assistant": assistant_row,
                "duplicate_suppressed": True,
                "resume_detected": bool(turn_claim.get("resume_detected")),
                "turn": terminal,
            },
            operator_intake=operator_intake,
        )
        return

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
        writer="agents.send_message_stream",
        new_state="running",
        state_reason="direct turn streaming",
        advisory_transport_id=turn_opts["transport_id"],
    )
    _lab_turns.append_turn_output_event(
        _repo_root(),
        turn_id=turn_id,
        writer="agents.send_message_stream",
        event_type="progress",
        status_message="streaming_started",
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
    if bool((options or {}).get("current_message_only")):
        model_input = msg

    if _agents_backend() == "codex":
        prompt_hash = _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8"))
        started = _utc_now_iso()
        yield _attach_operator_intake(
            payload={"type": "start", "role": r, "binding": rr["binding"], "started_utc": started, "turn_id": turn_id},
            operator_intake=operator_intake,
        )
        partial = ""
        try:
            mgr = _codex_manager()
            mgr.ensure_worker(binding=rr["binding"], role=r, bootstrap_text=str(pb["instructions"] or ""))
            timeout_s = int((options or {}).get("timeout_s") or _default_codex_timeout_s())
            final_evt: Dict[str, Any] = {}
            selected_model = model_chain[0] if model_chain else model
            last_error = ""
            for idx, model_name in enumerate(model_chain or [model]):
                selected_model = str(model_name or "").strip()
                try:
                    final_evt = {}
                    for evt in mgr.stream_turn(
                        binding=rr["binding"],
                        role=r,
                        message=model_input,
                        timeout_s=timeout_s,
                        model=selected_model,
                        reasoning_effort=reasoning_effort,
                    ):
                        et = str(evt.get("type") or "")
                        if et in {"commentary", "progress"}:
                            text = str(
                                evt.get("commentary")
                                or evt.get("progress")
                                or evt.get("text")
                                or evt.get("content")
                                or ""
                            ).strip()
                            if text:
                                yield {
                                    "type": "commentary",
                                    "commentary": text,
                                    **({"phase": str(evt.get("phase") or "").strip()} if str(evt.get("phase") or "").strip() else {}),
                                }
                        elif et == "delta":
                            delta = str(evt.get("delta") or "")
                            snapshot_content = str(evt.get("content") or "")
                            if not delta and snapshot_content and snapshot_content != partial:
                                delta = snapshot_content[len(partial) :] if snapshot_content.startswith(partial) else snapshot_content
                            partial = snapshot_content or partial + delta
                            if delta:
                                _lab_turns.append_turn_output_event(
                                    _repo_root(),
                                    turn_id=turn_id,
                                    writer="agents.send_message_stream.codex",
                                    event_type="output_delta",
                                    text=delta,
                                )
                                yield {"type": "delta", "delta": delta, "content": partial, "turn_id": turn_id}
                        elif et == "done":
                            final_evt = evt
                    quota_error = str(final_evt.get("error") or final_evt.get("content") or "")
                    if (
                        not partial
                        and not bool(final_evt.get("ok"))
                        and fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(quota_error)
                        and idx < len(model_chain) - 1
                    ):
                        last_error = quota_error
                        continue
                    break
                except Exception as e:
                    last_error = str(e)
                    if (
                        not partial
                        and fallback_on_quota
                        and _looks_like_quota_or_model_unavailable(last_error)
                        and idx < len(model_chain) - 1
                    ):
                        continue
                    raise
            if not final_evt and last_error:
                raise RuntimeError(last_error)
            ok = bool(final_evt.get("ok"))
            content = str(final_evt.get("content") or partial or final_evt.get("error") or "")
            visible_content = _user_visible_assistant_content(content)
            if visible_content and not partial:
                partial = visible_content
                _lab_turns.append_turn_output_event(
                    _repo_root(),
                    turn_id=turn_id,
                    writer="agents.send_message_stream.codex",
                    event_type="output_delta",
                    text=visible_content,
                )
                yield {"type": "delta", "delta": visible_content, "content": partial, "turn_id": turn_id}
            chat_header_title = _extract_chat_header_title(content)
            terminal_state = _turn_terminal_state(ok=ok, error=str(final_evt.get("error") or ""))
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message_stream.codex",
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
                    "backend": "codex",
                    "streamed": True,
                    "requested_model": model,
                    "selected_model": selected_model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "prompt_hash": prompt_hash,
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
                writer="agents.send_message_stream.codex",
                assistant_row=assistant_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            assistant_row = dict(commit["row"])
            if callable(globals().get("append_session_message")) and getattr(globals().get("append_session_message"), "__name__", "") != "append_session_message":
                try:
                    append_session_message(
                        role=r,
                        binding=rr["binding"],
                        sender="assistant",
                        content=content,
                        meta=dict(assistant_row.get("meta") or {}),
                    )
                except Exception:
                    pass
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message_stream.codex",
                new_state=terminal_state,
                state_reason=(visible_content if ok else str(final_evt.get("error") or "codex_stream_failed"))[:240],
            )
            if bool((options or {}).get("defer_host_verification")) and "HOST_VERIFICATION_REQUEST_JSON:" in content:
                host_dispatch = {
                    "deferred": True,
                    "content": content,
                    "session_file": str(sfile),
                }
            else:
                host_dispatch = _maybe_dispatch_direct_host_verification(
                    content=content,
                    role=r,
                    binding=rr["binding"],
                    session_file=sfile,
                )
            yield _attach_operator_intake(
                payload={
                    "type": "done",
                    "ok": ok,
                    "assistant": assistant_row,
                    "assistant_title": chat_header_title,
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                    "host_verification_requested": bool(host_dispatch.get("requested")),
                    "host_verification_result": dict(host_dispatch.get("result") or {}),
                    "assistant_followup": dict(host_dispatch.get("assistant_followup") or {}),
                    "deferred_host_verification_content": str(host_dispatch.get("content") or ""),
                    "deferred_host_verification_session_file": str(host_dispatch.get("session_file") or ""),
                },
                operator_intake=operator_intake,
            )
        except Exception as e:
            err_txt = f"[agent_error] {str(e)}"
            err_row = {
                "schema_version": "agent_chat_message_v1",
                "utc": _utc_now_iso(),
                "role_panel": r,
                "sender": "assistant",
                "content": err_txt,
                "status": "error",
                "binding": rr["binding"],
                "meta": {
                    "model": "codex-runtime",
                    "backend": "codex",
                    "streamed": True,
                    "requested_model": model,
                    "fallback_models": list(model_chain[1:]),
                    "reasoning_effort": reasoning_effort,
                    "error": str(e),
                    "prompt_hash": prompt_hash,
                    "turn_id": turn_id,
                    **_plane_routed_meta(operator_intake),
                },
            }
            _lab_turns.append_turn_output_event(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message_stream.codex",
                event_type="terminal",
                text=err_txt,
                terminal_state="failed",
            )
            terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
            commit = _commit_turn_transcript_row(
                role=r,
                binding=rr["binding"],
                session_file=sfile,
                turn_id=turn_id,
                writer="agents.send_message_stream.codex",
                assistant_row=err_row,
                turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
            )
            err_row = dict(commit["row"])
            _lab_turns.transition_turn(
                _repo_root(),
                turn_id=turn_id,
                writer="agents.send_message_stream.codex",
                new_state="failed",
                state_reason=str(e)[:240],
            )
            yield _attach_operator_intake(
                payload={
                    "type": "done",
                    "ok": False,
                    "assistant": err_row,
                    "error": str(e),
                    "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                    "duplicate_suppressed": False,
                    "resume_detected": bool(turn_claim.get("resume_detected")),
                },
                operator_intake=operator_intake,
            )
        return

    prompt_hash = _sha256_hex_bytes(str(pb["instructions"]).encode("utf-8"))
    partial = ""
    last_flush = 0.0
    started = _utc_now_iso()
    yield _attach_operator_intake(
        payload={"type": "start", "role": r, "binding": rr["binding"], "started_utc": started, "turn_id": turn_id},
        operator_intake=operator_intake,
    )

    try:
        final_response: Dict[str, Any] = {}
        selected_model = model_chain[0] if model_chain else model
        last_error = ""
        for idx, model_name in enumerate(model_chain or [model]):
            selected_model = str(model_name or "").strip()
            try:
                for evt in stream_response_events(
                    model=selected_model,
                    input_text=model_input,
                    instructions=str(pb["instructions"] or ""),
                    reasoning_effort=reasoning_effort,
                ):
                    et = str(evt.get("type") or "")
                    if et == "response.completed":
                        resp = evt.get("response")
                        if isinstance(resp, dict):
                            final_response = resp
                    delta = _stream_delta(evt)
                    if delta:
                        partial += delta
                        _lab_turns.append_turn_output_event(
                            _repo_root(),
                            turn_id=turn_id,
                            writer="agents.send_message_stream.llm",
                            event_type="output_delta",
                            text=delta,
                        )
                        yield {"type": "delta", "delta": delta, "content": partial, "turn_id": turn_id}
                    now = time.monotonic()
                    if (now - last_flush) >= 0.5 and partial:
                        last_flush = now
                break
            except Exception as e:
                last_error = str(e)
                if (
                    not partial
                    and fallback_on_quota
                    and _looks_like_quota_or_model_unavailable(last_error)
                    and idx < len(model_chain) - 1
                ):
                    continue
                raise
        if not final_response and last_error:
            raise RuntimeError(last_error)

        content = extract_output_text(final_response) if final_response else ""
        if not content:
            content = partial
        if not content and final_response:
            content = json.dumps(final_response, ensure_ascii=False)
        visible_content = _user_visible_assistant_content(content)
        chat_header_title = _extract_chat_header_title(content)
        _lab_turns.append_turn_output_event(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message_stream.llm",
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
                "streamed": True,
                "prompt_hash": prompt_hash,
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
            writer="agents.send_message_stream.llm",
            assistant_row=assistant_row,
            turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
        )
        assistant_row = dict(commit["row"])
        _lab_turns.transition_turn(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message_stream.llm",
            new_state="completed",
            state_reason=visible_content[:240],
        )
        host_dispatch = _maybe_dispatch_direct_host_verification(
            content=content,
            role=r,
            binding=rr["binding"],
            session_file=sfile,
        )
        yield _attach_operator_intake(
            payload={
                "type": "done",
                "ok": True,
                "assistant": assistant_row,
                "assistant_title": chat_header_title,
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
        err_txt = f"[agent_error] {str(e)}"
        err_row = {
            "schema_version": "agent_chat_message_v1",
            "utc": _utc_now_iso(),
            "role_panel": r,
            "sender": "assistant",
            "content": err_txt,
            "status": "error",
            "binding": rr["binding"],
            "meta": {
                "model": model,
                "fallback_models": list(model_chain[1:]),
                "reasoning_effort": reasoning_effort,
                "streamed": True,
                "error": str(e),
                "prompt_hash": prompt_hash,
                "turn_id": turn_id,
                **_plane_routed_meta(operator_intake),
            },
        }
        _lab_turns.append_turn_output_event(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message_stream.llm",
            event_type="terminal",
            text=err_txt,
            terminal_state="failed",
        )
        terminal_turn = _lab_turns.read_turn(_repo_root(), turn_id=turn_id)
        commit = _commit_turn_transcript_row(
            role=r,
            binding=rr["binding"],
            session_file=sfile,
            turn_id=turn_id,
            writer="agents.send_message_stream.llm",
            assistant_row=err_row,
            turn_event_seq=int(terminal_turn.get("terminal_event_seq") or 0),
        )
        err_row = dict(commit["row"])
        _lab_turns.transition_turn(
            _repo_root(),
            turn_id=turn_id,
            writer="agents.send_message_stream.llm",
            new_state="failed",
            state_reason=str(e)[:240],
        )
        yield _attach_operator_intake(
            payload={
                "type": "done",
                "ok": False,
                "assistant": err_row,
                "error": str(e),
                "turn": _lab_turns.read_turn(_repo_root(), turn_id=turn_id),
                "duplicate_suppressed": False,
                "resume_detected": bool(turn_claim.get("resume_detected")),
            },
            operator_intake=operator_intake,
        )


def send_to_role(
    *,
    from_role: str,
    to_role: str,
    binding: Dict[str, Any],
    message: str,
    also_enqueue: bool = True,
    request_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    rr = _resolve_paths(_normalize_binding(binding))
    fr = _validate_role(from_role)
    tr = _validate_role(to_role)
    msg = str(message or "").strip()
    if not msg:
        raise ValueError("message is required")
    if not bool(also_enqueue):
        return {"ok": True, "enqueued": False, "binding": rr["binding"]}
    ifile = _inbox_file(rr["chat_dir"], tr)
    row = {
        "schema_version": "agent_inbox_message_v1",
        "utc": _utc_now_iso(),
        "from_role": fr,
        "to_role": tr,
        "content": msg,
        "binding": rr["binding"],
        "request_context": (request_context if isinstance(request_context, dict) else {}),
    }
    _append_jsonl(ifile, row)
    ledger_event = None
    if rr["binding"]["type"] == "ledger_uid":
        ledger = ChangeLedger(repo_root=_repo_root())
        payload = {
            "uid": rr["binding"]["id"],
            "from_role": fr,
            "to_role": tr,
            "message_sha256": _sha256_hex_bytes(msg.encode("utf-8")),
            "created_utc": _utc_now_iso(),
            "request_context": (request_context if isinstance(request_context, dict) else {}),
        }
        ledger_event = ledger.append_event(rr["binding"]["id"], event_type="agent_request_sent", payload=payload)
    return {
        "ok": True,
        "enqueued": True,
        "binding": rr["binding"],
        "inbox_count": len(_read_jsonl(ifile)),
        "ledger_event": ledger_event,
    }


def _select_rows(rows: List[Dict[str, Any]], selection: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(selection, dict):
        return rows[-12:]
    mode = str(selection.get("mode") or "last_n").strip().lower()
    if mode == "range":
        try:
            lo = int(selection.get("from"))
            hi = int(selection.get("to"))
        except Exception:
            return rows[-12:]
        if lo < 0:
            lo = 0
        if hi < lo:
            hi = lo
        return rows[lo:hi + 1]
    try:
        n = int(selection.get("n") or 12)
    except Exception:
        n = 12
    n = max(1, min(500, n))
    return rows[-n:]


def _latest_handoff_info(base_dir: Path, handoff_dir: Path) -> Dict[str, Any]:
    if not handoff_dir.exists() or not handoff_dir.is_dir() or handoff_dir.is_symlink():
        return {}
    files = sorted([p for p in handoff_dir.glob("*.md") if p.is_file() and not p.is_symlink()])
    if not files:
        return {}
    p = files[-1]
    txt = p.read_text(encoding="utf-8", errors="replace")
    next_action = ""
    lines = txt.splitlines()
    for i, ln in enumerate(lines):
        if ln.strip() == "NEXT_ACTION:":
            if i + 1 < len(lines):
                nxt = str(lines[i + 1]).strip()
                if nxt.startswith("-"):
                    next_action = nxt[1:].strip()
            break
    try:
        rel = p.relative_to(base_dir).as_posix()
    except Exception:
        rel = str(p)
    return {"path": rel, "next_action": next_action}


def _latest_failed_action_log(base_dir: Path) -> Dict[str, Any]:
    ad = (base_dir / "artifacts" / "actions").resolve()
    if not ad.exists() or not ad.is_dir() or ad.is_symlink():
        return {}
    results = sorted([p for p in ad.glob("*.result.json") if p.is_file() and not p.is_symlink()])
    for p in reversed(results):
        try:
            obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
        try:
            code = int(obj.get("exit_code", 1))
        except Exception:
            code = 1
        if code == 0:
            continue
        log_path = str(obj.get("log_path") or "")
        try:
            result_rel = p.relative_to(base_dir).as_posix()
        except Exception:
            result_rel = str(p)
        log_rel = log_path
        if log_path:
            lp = Path(log_path)
            try:
                log_rel = lp.relative_to(base_dir).as_posix()
            except Exception:
                log_rel = log_path
        return {"result_path": result_rel, "log_path": log_rel, "exit_code": code}
    return {}


def _stream_delta(evt: Dict[str, Any]) -> str:
    t = str(evt.get("type") or "").strip()
    if t == "response.output_text.delta":
        d = evt.get("delta")
        return d if isinstance(d, str) else ""
    if t in ("response.completed", "response.output_text.done"):
        return ""
    # Fallback for variant events.
    d = evt.get("delta")
    if isinstance(d, str):
        return d
    item = evt.get("item")
    if isinstance(item, dict):
        content = item.get("content")
        if isinstance(content, list):
            parts: List[str] = []
            for c in content:
                if isinstance(c, dict):
                    txt = c.get("text")
                    if isinstance(txt, str) and txt:
                        parts.append(txt)
            if parts:
                return "".join(parts)
    return ""
