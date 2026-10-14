def _maybe_dispatch_direct_host_verification(
    *,
    content: str,
    role: str,
    binding: Dict[str, Any],
    session_file: Path,
    timeout_s_override: Any = None,
) -> Dict[str, Any]:
    request = _extract_host_verification_request(content)
    if not request:
        return {}
    _trace_host_verification(
        "direct_request_detected",
        message="detected host verification request marker in agent reply",
        role=role,
        binding=dict(binding or {}),
        action_id=str(request.get("action_id") or ""),
    )
    try:
        normalized = _build_direct_host_verification_request(
            role=role,
            binding=binding,
            request=request,
            timeout_s_override=timeout_s_override,
        )
    except Exception as exc:
        _trace_host_verification(
            "direct_request_rejected",
            level="error",
            message=f"rejected direct host verification request: {exc}",
            role=role,
            binding=dict(binding or {}),
            request=request,
        )
        result = {
            "request_id": "",
            "workflow_id": "",
            "ok": False,
            "status": "failed",
            "action_id": str(request.get("action_id") or ""),
            "started_utc": _utc_now_iso(),
            "finished_utc": _utc_now_iso(),
            "duration_ms": 0,
            "exit_code": 1,
            "cwd": str(_repo_root()),
            "argv": [],
            "evidence": {
                "stdout_path": "",
                "stderr_path": "",
                "artifact_paths": [],
                "summary": f"host verification request rejected: {exc}",
            },
            "failure_kind": "invalid_request",
        }
        followup = _append_host_verification_followup(session_file=session_file, role=role, binding=binding, result=result)
        return {"requested": True, "result": result, "assistant_followup": followup}
    _trace_host_verification(
        "direct_request_normalized",
        request=normalized,
        message="normalized direct host verification request emitted by agent reply",
        role=role,
        binding=dict(binding or {}),
    )
    binding_result: Dict[str, Any] = {}
    try:
        from infra.webui import server_lab_assistant_delegation_bindings as _bindings

        action = normalized.get("action") if isinstance(normalized.get("action"), dict) else {}
        artifacts = normalized.get("artifacts") if isinstance(normalized.get("artifacts"), dict) else {}
        binding_result = _bindings.bind_surface_launch(
            _repo_root(),
            surface="verifier",
            binding=binding,
            summary=f"host verification {str(action.get('action_id') or '').strip() or 'request'}",
            requested_action=str(normalized.get("reason") or "host verification"),
            launch_result={
                "request_id": str(normalized.get("request_id") or ""),
                "workflow_id": str(normalized.get("workflow_id") or ""),
                "action_id": str(action.get("action_id") or ""),
                "params": dict(action.get("params") or {}),
                "artifacts": {"root_dir": str(artifacts.get("root_dir") or "")},
                "timeout_s": int((normalized.get("policy") or {}).get("timeout_s") or 0),
                "status": "running",
                "reference_status": "execution_ref_active",
                "reference_status_reason": "host_verification_launched",
            },
            input_payload={"role": role, "binding": dict(binding or {}), "request_id": str(normalized.get("request_id") or "")},
            writer="verifier.launch.bind",
            launch_cause="operator_action",
        )
    except Exception as exc:
        binding_result = {"ok": False, "error": str(exc)}
    result = _run_host_verification_request(normalized)
    followup = _append_host_verification_followup(session_file=session_file, role=role, binding=binding, result=result)
    return {"requested": True, "result": result, "assistant_followup": followup, "binding_result": binding_result}


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    lock = _file_lock(path)
    with lock:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def _write_text_atomic(path: Path, text: str) -> None:
    """Write text to disk atomically with the same lock discipline as JSON state."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    raw = str(text or "")
    lock = _file_lock(path)
    with lock:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def _tail_rows(path: Path, *, limit: int = 50) -> List[Dict[str, Any]]:
    lim = max(1, min(1000, int(limit)))
    rows = _read_jsonl(path)
    if len(rows) <= lim:
        return rows
    return rows[-lim:]


def _latest_assistant_row_for_turn(path: Path, *, turn_id: str) -> Optional[Dict[str, Any]]:
    wanted = str(turn_id or "").strip()
    if not wanted:
        return None
    for row in reversed(_read_jsonl(path)):
        if str(row.get("sender") or "") != "assistant":
            continue
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if str(meta.get("turn_id") or "") == wanted:
            return row
    return None


def _latest_assistant_row_for_turn_in_rows(rows: List[Dict[str, Any]], *, turn_id: str) -> Optional[Dict[str, Any]]:
    wanted = str(turn_id or "").strip()
    if not wanted:
        return None
    for row in reversed(rows):
        if str(row.get("sender") or "") != "assistant":
            continue
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        if str(meta.get("turn_id") or "") == wanted:
            return row
    return None


def _row_sha256(row: Dict[str, Any]) -> str:
    raw = json.dumps(dict(row or {}), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return _sha256_hex_bytes(raw)


def _commit_turn_transcript_row(
    *,
    role: str,
    binding: Dict[str, str],
    session_file: Path,
    turn_id: str,
    writer: str,
    assistant_row: Dict[str, Any],
    turn_event_seq: Optional[int] = None,
) -> Dict[str, Any]:
    terminal_seq = max(0, int(turn_event_seq if turn_event_seq is not None else 0))
    session_file.parent.mkdir(parents=True, exist_ok=True)
    _lab_turns.mark_turn_final_commit_pending(
        _repo_root(),
        turn_id=turn_id,
        writer=writer,
        turn_event_seq=(terminal_seq or None),
    )
    wrote = False
    committed_row: Dict[str, Any] = {}
    lock = _file_lock(session_file)
    try:
        with lock:
            rows = _read_jsonl(session_file)
            existing = _latest_assistant_row_for_turn_in_rows(rows, turn_id=turn_id)
            if existing is not None:
                committed_row = existing
            else:
                line = json.dumps(assistant_row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                with open(session_file, "a", encoding="utf-8", newline="\n") as f:
                    f.write(line)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                committed_row = dict(assistant_row)
                wrote = True
        digest = _row_sha256(committed_row)
        _lab_turns.mark_turn_final_commit_committed(
            _repo_root(),
            turn_id=turn_id,
            writer=writer,
            session_row_sha256=digest,
            turn_event_seq=(terminal_seq or None),
        )
    except Exception as exc:
        _lab_turns.mark_turn_final_commit_pending(
            _repo_root(),
            turn_id=turn_id,
            writer=writer,
            turn_event_seq=(terminal_seq or None),
            error=str(exc)[:240],
        )
        raise
    if wrote:
        _refresh_mobile_bubble_projection(binding=binding, role=role, session_file=session_file)
    return {"row": committed_row, "wrote": wrote}


def _reconcile_missing_turn_transcript_row(
    *,
    role: str,
    binding: Dict[str, str],
    session_file: Path,
    turn_id: str,
    turn: Dict[str, Any],
    writer: str,
) -> Optional[Dict[str, Any]]:
    if str(turn.get("state") or "") not in _lab_turns.DIRECT_TURN_TERMINAL_STATES:
        return None
    replay = _lab_turns.replay_turn_events(_repo_root(), turn_id=turn_id, after_event_seq=0)
    text = str(replay.get("content") or "").strip()
    if not text:
        return None
    status = "ok" if str(turn.get("state") or "") == "completed" else "error"
    assistant_row = {
        "schema_version": "agent_chat_message_v1",
        "utc": _utc_now_iso(),
        "role_panel": role,
        "sender": "assistant",
        "content": text,
        "status": status,
        "binding": binding,
        "meta": {
            "turn_id": turn_id,
            "source": "turn_event_reconcile",
            "reconciled_from_event_seq": int(turn.get("terminal_event_seq") or 0),
        },
    }
    commit = _commit_turn_transcript_row(
        role=role,
        binding=binding,
        session_file=session_file,
        turn_id=turn_id,
        writer=writer,
        assistant_row=assistant_row,
        turn_event_seq=int(turn.get("terminal_event_seq") or 0),
    )
    return dict(commit["row"])


def _coerce_optional_nonnegative_int(value: Any, *, field: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    try:
        parsed = int(value)
    except Exception:
        raise ValueError(f"{field} must be an integer")
    if parsed < 0:
        raise ValueError(f"{field} must be >= 0")
    return parsed


def _turn_options(options: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    opts = options if isinstance(options, dict) else {}
    return {
        "resend_key": str(opts.get("resend_key") or "").strip(),
        "resume_turn_id": str(opts.get("resume_turn_id") or opts.get("turn_id") or "").strip(),
        "transport_id": str(opts.get("transport_id") or "").strip(),
        "resume_after_event_seq": _coerce_optional_nonnegative_int(
            opts.get("resume_after_event_seq"),
            field="resume_after_event_seq",
        ),
        "resume_content_chars": _coerce_optional_nonnegative_int(
            opts.get("resume_content_chars"),
            field="resume_content_chars",
        ),
    }


def _turn_terminal_state(*, ok: bool, error: str = "") -> str:
    if ok:
        return "completed"
    lowered = str(error or "").strip().lower()
    if "cancel" in lowered:
        return "cancelled"
    if "interrupt" in lowered or "stop" in lowered or "timeout" in lowered:
        return "awaiting_resume"
    return "failed"


def _validate_role(role: Any) -> str:
    r = str(role or "").strip().lower()
    if r not in ROLE_SET:
        raise ValueError("invalid role")
    return r


def _normalize_binding(binding: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(binding, dict):
        raise ValueError("binding must be an object")
    bt = str(binding.get("type") or "").strip().lower()
    bid = str(binding.get("id") or "").strip()
    if bt not in ("task_id", "ledger_uid"):
        raise ValueError("binding.type must be task_id or ledger_uid")
    if bt == "task_id":
        if not TASK_ID_RE.match(bid):
            raise ValueError("task_id must match ^[a-z0-9]{3}$")
    else:
        # Validation and existence check occur through ChangeLedger.
        if not bid.startswith("PIS-"):
            raise ValueError("ledger_uid must start with PIS-")
    out = {"type": bt, "id": bid}
    task_id = str(binding.get("task_id") or "").strip()
    ledger_uid = str(binding.get("ledger_uid") or "").strip()
    if task_id:
        out["task_id"] = task_id
    if ledger_uid:
        out["ledger_uid"] = ledger_uid
    return out


def _is_mobile_task_binding(binding: Dict[str, Any]) -> bool:
    bt = str((binding or {}).get("type") or "").strip().lower()
    bid = str((binding or {}).get("id") or "").strip().lower()
    return bt == "task_id" and bid in _MOBILE_TASK_BINDING_IDS


def _resolve_paths(binding: Dict[str, str]) -> Dict[str, Any]:
    rr = _repo_root()
    bt = binding["type"]
    bid = binding["id"]
    binding_payload = {"type": bt, "id": bid}
    if str(binding.get("task_id") or "").strip():
        binding_payload["task_id"] = str(binding.get("task_id") or "").strip()
    if str(binding.get("ledger_uid") or "").strip():
        binding_payload["ledger_uid"] = str(binding.get("ledger_uid") or "").strip()
    if bt == "task_id":
        base = (rr / "governance" / "agents" / "sessions" / bid).resolve()
        chat_dir = base
        handoff_dir = (base / "handoff").resolve()
        base.mkdir(parents=True, exist_ok=True)
        handoff_dir.mkdir(parents=True, exist_ok=True)
        return {
            "binding": binding_payload,
            "base_dir": base,
            "chat_dir": chat_dir,
            "handoff_dir": handoff_dir,
            "ledger_item_dir": None,
        }

    ledger = ChangeLedger(repo_root=rr)
    item_dir = ledger.get_item_dir(bid)
    chat_dir = (item_dir / "artifacts" / "agent_chat").resolve()
    handoff_dir = (item_dir / "artifacts" / "handoff").resolve()
    chat_dir.mkdir(parents=True, exist_ok=True)
    handoff_dir.mkdir(parents=True, exist_ok=True)
    return {
        "binding": binding_payload,
        "base_dir": item_dir,
        "chat_dir": chat_dir,
        "handoff_dir": handoff_dir,
        "ledger_item_dir": item_dir,
    }


def _mobile_bubbles_root() -> Path:
    """Return the runtime directory for derived human-readable mobile bubble files."""
    return (_repo_root() / "governance" / "agents" / "mobile_bubbles").resolve()


def _mobile_bubble_projection_path(*, binding: Dict[str, str], role: str) -> Optional[Path]:
    """Map known mobile task bindings to stable per-agent bubble filenames."""
    key = (str(binding.get("type") or ""), str(binding.get("id") or "").lower(), str(role or "").lower())
    filename = MOBILE_BUBBLE_FILE_MAP.get(key)
    if not filename:
        return None
    return (_mobile_bubbles_root() / filename).resolve()


def _render_mobile_bubble_markdown(*, binding: Dict[str, str], role: str, rows: List[Dict[str, Any]]) -> str:
    """Render a readable Markdown transcript from canonical session rows."""
    header = [
        f"# {str(binding.get('id') or '').lower()} / {str(role or '').lower()}",
        "",
        f"- binding: {str(binding.get('type') or '')}:{str(binding.get('id') or '')}",
        f"- role: {str(role or '').lower()}",
        f"- updated_utc: {_utc_now_iso()}",
        "",
    ]
    body: List[str] = []
    for row in rows:
        sender = str(row.get("sender") or "").strip().lower()
        content = str(row.get("content") or "").strip()
        if sender == "assistant":
            content = _user_visible_assistant_content(content)
        utc = str(row.get("utc") or "-").strip() or "-"
        status = str(row.get("status") or "").strip().lower()
        if sender not in {"user", "assistant"} or not content:
            continue
        status_suffix = "" if not status or status == "ok" else f" [{status}]"
        body.append(f"[{utc}] {sender}{status_suffix}")
        body.append(content)
        body.append("")
    if not body:
        body = ["(no bubbles yet)", ""]
    return "\n".join(header + body).rstrip() + "\n"


def _refresh_mobile_bubble_projection(*, binding: Dict[str, str], role: str, session_file: Path) -> Optional[str]:
    """Rewrite the derived mobile bubble file for known mobile agents."""
    out_path = _mobile_bubble_projection_path(binding=binding, role=role)
    if out_path is None:
        return None
    rows = _read_jsonl(session_file, max_lines=4000)
    text = _render_mobile_bubble_markdown(binding=binding, role=role, rows=rows)
    _write_text_atomic(out_path, text)
    return str(out_path)


def _session_history_rows_from_rows(rows: List[Dict[str, Any]], *, tail: int = 200) -> List[Dict[str, str]]:
    """Project canonical transcript rows into simplified user/assistant history rows."""
    usable = list(rows)
    if int(tail) > 0 and len(usable) > int(tail):
        usable = usable[-int(tail) :]
    out: List[Dict[str, str]] = []
    for row in usable:
        if not isinstance(row, dict):
            continue
        sender = str(row.get("sender") or "").strip().lower()
        content = str(row.get("content") or "")
        if sender not in {"user", "assistant"}:
            continue
        if not content:
            continue
        if sender == "assistant":
            content = _user_visible_assistant_content(content)
            if not content:
                continue
        item = {"role": sender, "content": content}
        status = str(row.get("status") or "").strip().lower()
        if status and status not in {"ok", "complete"}:
            item["status"] = status
        meta = row.get("meta") if isinstance(row.get("meta"), dict) else {}
        label = str(meta.get("mobile_history_label") or "").strip()
        if label:
            item["label"] = label
        if bool(meta.get("plane_routed")):
            item["plane_routed"] = True
        out.append(item)
    return out


def _user_visible_session_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        item = dict(row)
        sender = str(item.get("sender") or "").strip().lower()
        if sender == "assistant":
            item["content"] = _user_visible_assistant_content(str(item.get("content") or ""))
        out.append(item)
    return out


def _merge_message_history_rows(left: List[Dict[str, str]], right: List[Dict[str, str]]) -> Tuple[List[Dict[str, str]], int]:
    """Merge two simplified histories using the largest boundary overlap."""
    lhs = [dict(row) for row in left if isinstance(row, dict)]
    rhs = [dict(row) for row in right if isinstance(row, dict)]
    if not lhs:
        return rhs, 0
    if not rhs:
        return lhs, 0
    max_overlap = min(len(lhs), len(rhs))
    overlap = 0
    for size in range(max_overlap, 0, -1):
        if lhs[-size:] == rhs[:size]:
            overlap = size
            break
    return lhs + rhs[overlap:], overlap


def _synthetic_recovery_rows(
    *,
    binding: Dict[str, str],
    role: str,
    recovered_rows: List[Dict[str, str]],
    anchor_utc: str,
) -> List[Dict[str, Any]]:
    """Build canonical transcript rows for recovered cache-only history."""
    try:
        anchor = _dt.datetime.fromisoformat(str(anchor_utc).replace("Z", "+00:00"))
    except Exception:
        anchor = _dt.datetime.now(_dt.timezone.utc)
    start = anchor - _dt.timedelta(seconds=len(recovered_rows))
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(recovered_rows):
        ts = start + _dt.timedelta(seconds=idx)
        out.append(
            {
                "schema_version": "agent_chat_message_v1",
                "utc": ts.astimezone(_dt.timezone.utc).isoformat().replace("+00:00", "Z"),
                "role_panel": str(role or "").strip().lower(),
                "sender": str(row.get("role") or "").strip().lower(),
                "content": str(row.get("content") or ""),
                "status": "ok",
                "binding": dict(binding),
                "meta": {
                    "source": "mobile_history_recovery",
                    "recovered_from": "mobile_chat_sessions",
                    "utc_origin": "synthetic_prefix_before_canonical",
                },
            }
        )
    return out


def reconcile_simplified_session_history(
    *,
    role: str,
    binding: Dict[str, Any],
    recovered_history: List[Dict[str, str]],
) -> Dict[str, Any]:
    """Merge recovered simplified history into the canonical session transcript."""
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    sfile = _session_file(rr["chat_dir"], r)
    existing_rows = _read_jsonl(sfile, max_lines=0)
    existing_history = _session_history_rows_from_rows(existing_rows, tail=0)
    merged_history, overlap = _merge_message_history_rows(recovered_history, existing_history)
    recovered_prefix = recovered_history[:-overlap] if overlap > 0 else list(recovered_history)
    if not existing_rows:
        recovered_prefix = list(merged_history)
    if not recovered_prefix:
        bubble_path = _refresh_mobile_bubble_projection(binding=rr["binding"], role=r, session_file=sfile)
        return {
            "ok": True,
            "changed": False,
            "prepended": 0,
            "session_count": len(existing_rows),
            "mobile_bubble_file": str(bubble_path or ""),
        }
    anchor_utc = str(existing_rows[0].get("utc") or _utc_now_iso()) if existing_rows else _utc_now_iso()
    synthetic_rows = _synthetic_recovery_rows(
        binding=rr["binding"],
        role=r,
        recovered_rows=recovered_prefix,
        anchor_utc=anchor_utc,
    )
    line_rows = synthetic_rows + existing_rows
    raw = "\n".join(json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False) for row in line_rows) + "\n"
    _write_text_atomic(sfile, raw)
    bubble_path = _refresh_mobile_bubble_projection(binding=rr["binding"], role=r, session_file=sfile)
    return {
        "ok": True,
        "changed": True,
        "prepended": len(synthetic_rows),
        "session_count": len(line_rows),
        "mobile_bubble_file": str(bubble_path or ""),
    }


def refresh_mobile_bubble_projections() -> List[str]:
    """Refresh all derived mobile bubble markdown files from canonical session JSONL."""
    out: List[str] = []
    for binding_type, binding_id, role in MOBILE_BUBBLE_FILE_MAP.keys():
        if binding_type != "task_id":
            continue
        rr = _resolve_paths({"type": binding_type, "id": binding_id})
        sfile = _session_file(rr["chat_dir"], role)
        if not sfile.exists() or (not sfile.is_file()):
            continue
        refreshed = _refresh_mobile_bubble_projection(binding=rr["binding"], role=role, session_file=sfile)
        if refreshed:
            out.append(refreshed)
    return out


def _session_file(chat_dir: Path, role: str) -> Path:
    return (chat_dir / f"session__{role}.jsonl").resolve()


def _inbox_file(chat_dir: Path, role: str) -> Path:
    return (chat_dir / f"inbox__{role}.jsonl").resolve()


def _stream_state_file(chat_dir: Path, role: str) -> Path:
    return (chat_dir / f"stream__{role}.json").resolve()


def _prompts_root() -> Path:
    return (_repo_root() / "governance" / "agents" / "prompts").resolve()


def _canonical_prompt_filename(filename: str, *, role: Optional[str] = None, workflow: bool = False) -> str:
    raw = str(filename or "").strip()
    if not raw:
        return active_prompt_file_for_role(role or "generalist", workflow=workflow) if role else raw
    token = Path(raw).name if ("/" in raw or "\\" in raw) else raw
    redirected = str(PROMPT_FILE_REDIRECTS.get(token, PROMPT_FILE_REDIRECTS.get(raw, token)))
    if redirected != raw:
        return redirected
    return token


def canonical_prompt_file(filename: Optional[str], *, role: Optional[str] = None, workflow: bool = False) -> str:
    """Return the canonical prompt filename for runtime use."""
    return _canonical_prompt_filename(str(filename or ""), role=role, workflow=workflow)


def _prompt_file_path(filename: str, *, role: Optional[str] = None, workflow: bool = False) -> Path:
    root = _prompts_root()
    canonical = _canonical_prompt_filename(filename, role=role, workflow=workflow)
    p = (root / canonical).resolve()
    try:
        p.relative_to(root)
    except Exception:
        raise ValueError("prompt path escapes prompts root")
    return p


def active_prompt_file_for_role(role: str, *, workflow: bool = False) -> str:
    r = _validate_role(role)
    mapping = WORKFLOW_ROLE_PROMPT_FILES if workflow else ACTIVE_ROLE_PROMPT_FILES
    return str(mapping.get(r) or f"role__{r}.md")


def _prompt_target_to_path(target: str) -> Path:
    tt = str(target or "").strip().lower()
    mapping = {
        "all_codex": _prompt_file_path("_all_codex.md"),
        "base": _prompt_file_path("_base.md"),
        "role:builder": _prompt_file_path(active_prompt_file_for_role("builder")),
        "role:assurer": _prompt_file_path(active_prompt_file_for_role("assurer")),
        "role:generalist": _prompt_file_path(active_prompt_file_for_role("generalist")),
        "role:orchestrator": _prompt_file_path("role__orchestrator_mobile.md"),
        "task_template": _prompt_file_path("task_templates/task__ledger_bound.md"),
    }
    p = mapping.get(tt)
    if p is None:
        raise ValueError("invalid prompt target")
    return p


def _default_codex_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.codex_turn_timeout_s",
        CODEX_TIMEOUT_DEFAULT_S,
        env_names=("EIEL_AGENTS_CODEX_TIMEOUT_S",),
        minimum=30,
        maximum=6 * 60 * 60,
    )


def _default_codex_max_workers() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.codex_max_workers",
        100,
        env_names=("EIEL_CODEX_MAX_WORKERS",),
        minimum=1,
        maximum=1000,
    )


def _default_codex_max_workers_per_role() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.codex_max_workers_per_role",
        25,
        env_names=("EIEL_CODEX_MAX_WORKERS_PER_ROLE",),
        minimum=1,
        maximum=500,
    )


def _read_prompt_file(path: Path) -> Dict[str, Any]:
    root = _prompts_root()
    target = path
    if target.parent == root or target.name in PROMPT_FILE_REDIRECTS:
        target = _prompt_file_path(target.name)
    if not target.exists() or not target.is_file():
        fallback_name = PROMPT_FILE_REDIRECTS.get(target.name) or PROMPT_FILE_REDIRECTS.get(path.name)
        if fallback_name:
            fallback = (root / fallback_name).resolve()
            try:
                fallback.relative_to(root)
            except Exception:
                fallback = target
            else:
                if fallback.exists() and fallback.is_file():
                    target = fallback
        if not target.exists() or not target.is_file():
            raise FileNotFoundError(str(path))
    txt = target.read_text(encoding="utf-8", errors="replace")
    st = target.stat()
    return {
        "path": str(target),
        "text": txt,
        "mtime_utc": _dt.datetime.fromtimestamp(st.st_mtime, tz=_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "sha256": _sha256_hex_bytes(txt.encode("utf-8")),
    }


def _resolve_prompt_include_path(source: Path, include_target: str) -> Path:
    root = _prompts_root()
    raw = str(include_target or "").strip()
    if not raw:
        raise PromptConfigError("prompt include target is empty")
    if "/" in raw or "\\" in raw or raw.startswith("."):
        target = (source.parent / raw).resolve()
    else:
        target = _prompt_file_path(raw)
    try:
        target.relative_to(root)
    except Exception as exc:
        raise PromptConfigError("prompt include escapes prompts root") from exc
    if not target.exists() or not target.is_file():
        raise PromptConfigError(f"missing included prompt file: {raw}")
    return target


def _render_prompt_file(path: Path, *, _stack: Optional[tuple[str, ...]] = None) -> Dict[str, Any]:
    prompt = _read_prompt_file(path)
    target = Path(str(prompt["path"])).resolve()
    stack = tuple(_stack or ())
    target_key = str(target)
    if target_key in stack:
        chain = " -> ".join([*stack, target_key])
        raise PromptConfigError(f"prompt include cycle detected: {chain}")

    rendered_lines: List[str] = []
    dependency_paths: List[Path] = [target]
    for line in str(prompt["text"]).splitlines():
        match = _PROMPT_INCLUDE_RE.match(line)
        if match is None:
            rendered_lines.append(line)
            continue
        include_path = _resolve_prompt_include_path(target, match.group(1))
        include_prompt = _render_prompt_file(include_path, _stack=(*stack, target_key))
        rendered_lines.append(str(include_prompt["text"]).rstrip("\n"))
        for dep in list(include_prompt.get("dependency_paths") or []):
            dep_path = Path(str(dep)).resolve()
            if dep_path not in dependency_paths:
                dependency_paths.append(dep_path)

    rendered_text = "\n".join(rendered_lines)
    dep_parts: List[str] = []
    for dep_path in dependency_paths:
        st = dep_path.stat()
        dep_parts.append(f"{dep_path}:{int(st.st_mtime_ns)}:{int(st.st_size)}")
    return {
        **prompt,
        "raw_text": str(prompt["text"]),
        "text": rendered_text,
        "sha256": _sha256_hex_bytes(rendered_text.encode("utf-8")),
        "cache_key": _sha256_hex_bytes("\n".join(dep_parts).encode("utf-8")),
        "dependency_paths": [str(dep) for dep in dependency_paths],
    }


def render_prompt_file(path: Path) -> Dict[str, Any]:
    """Return rendered prompt text with include directives expanded."""
    return _render_prompt_file(path)


def _ledger_task_prompt(binding: Dict[str, str]) -> str:
    if binding["type"] != "ledger_uid":
        return ""
    rr = _repo_root()
    ledger = ChangeLedger(repo_root=rr)
    item = ledger.read_item(binding["id"])
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    tpl = (_prompts_root() / "task_templates" / "task__ledger_bound.md").resolve()
    if tpl.exists() and tpl.is_file():
        raw = tpl.read_text(encoding="utf-8", errors="replace")
        return (
            raw.replace("{uid}", str(meta.get("uid") or binding["id"]))
            .replace("{title}", str(meta.get("title") or ""))
            .replace("{slug}", str(meta.get("slug") or ""))
            .replace("{status}", str(meta.get("status") or ""))
            .replace("{pis_excerpt}", str(meta.get("pis_excerpt") or ""))
        )
    return (
        "Ledger-bound task context:\n"
        f"- uid: {str(meta.get('uid') or binding['id'])}\n"
        f"- title: {str(meta.get('title') or '')}\n"
        f"- status: {str(meta.get('status') or '')}\n"
        f"- slug: {str(meta.get('slug') or '')}\n"
        f"- pis_excerpt: {str(meta.get('pis_excerpt') or '')}\n"
    )
