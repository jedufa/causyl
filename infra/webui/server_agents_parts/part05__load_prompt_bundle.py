def load_prompt_bundle(
    role: str,
    binding: Optional[Dict[str, str]] = None,
    *,
    prompt_file: Optional[str] = None,
    role_prompt_text: Optional[str] = None,
    workflow: bool = False,
) -> Dict[str, Any]:
    try:
        custom_role_text = str(role_prompt_text or "").strip()
        try:
            base = _read_prompt_file(_prompt_file_path("_base.md"))
        except FileNotFoundError:
            if not custom_role_text:
                raise
            base = {
                "path": "_base.md",
                "text": "",
                "mtime_utc": "",
                "sha256": _sha256_hex_bytes(b""),
            }
        if custom_role_text:
            role_prompt_file = canonical_prompt_file(
                prompt_file or active_prompt_file_for_role(role, workflow=workflow),
                role=role,
                workflow=workflow,
            )
            role_pf = {
                "path": role_prompt_file,
                "text": custom_role_text,
                "raw_text": custom_role_text,
                "mtime_utc": "",
                "sha256": _sha256_hex_bytes(custom_role_text.encode("utf-8")),
                "cache_key": _sha256_hex_bytes(f"inline:{role_prompt_file}:{custom_role_text}".encode("utf-8")),
                "dependency_paths": [],
            }
        else:
            role_prompt_file = canonical_prompt_file(
                prompt_file or active_prompt_file_for_role(role, workflow=workflow),
                role=role,
                workflow=workflow,
            )
            role_pf = _render_prompt_file(
                _prompt_file_path(
                    role_prompt_file,
                    role=role,
                    workflow=workflow,
                )
            )
        task_text = ""
        bootstrap_text = ""
        if binding is not None:
            task_text = _ledger_task_prompt(binding)
            bootstrap_text = _lab_bootstrap.render_fresh_turn_bootstrap_text(
                _repo_root(),
                recent_chat_rows=session_history_rows(role=role, binding=binding, tail=_lab_bootstrap.fresh_turn_recent_chat_rows()),
            )
        parts = [role_pf["text"]]
        if bootstrap_text.strip():
            parts.append(bootstrap_text)
        if task_text.strip():
            parts.append(task_text)
        instructions = "\n\n".join([p.strip() for p in parts if str(p).strip()])
        return {
            "base": base,
            "role": role_pf,
            "bootstrap_prompt": bootstrap_text,
            "task_prompt": task_text,
            "instructions": instructions,
        }
    except FileNotFoundError as e:
        raise PromptConfigError(f"missing prompt file: {str(e)}")


def get_prompts_payload() -> Dict[str, Any]:
    root = _prompts_root()
    out: Dict[str, Any] = {"root": str(root), "roles": {}}
    out["base"] = _read_prompt_file(_prompt_file_path("_base.md"))
    shared = _prompt_file_path("_all_codex.md")
    out["all_codex"] = _read_prompt_file(shared) if shared.exists() and shared.is_file() else None
    for role in sorted(ROLE_SET):
        out["roles"][role] = _read_prompt_file(_prompt_file_path(active_prompt_file_for_role(role)))
    out["roles"]["orchestrator"] = _read_prompt_file(_prompt_file_path("role__orchestrator_mobile.md"))
    tpl = _prompt_file_path("task_templates/task__ledger_bound.md")
    out["task_template"] = _read_prompt_file(tpl) if tpl.exists() and tpl.is_file() else None
    out["editable_targets"] = [
        "all_codex",
        "base",
        "role:builder",
        "role:assurer",
        "role:generalist",
        "role:orchestrator",
        "task_template",
    ]
    out["legacy_redirects"] = dict(PROMPT_FILE_REDIRECTS)
    out["codex_timeout_default_s"] = int(_default_codex_timeout_s())
    return out


def update_prompt_text(*, target: str, text: str) -> Dict[str, Any]:
    p = _prompt_target_to_path(target)
    content = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
    if not content.endswith("\n"):
        content += "\n"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    pf = _read_prompt_file(p)
    return {
        "ok": True,
        "target": str(target or "").strip().lower(),
        "path": str(p),
        "mtime_utc": str(pf.get("mtime_utc") or ""),
        "sha256": str(pf.get("sha256") or ""),
    }


def _format_context_messages(rows: List[Dict[str, Any]], *, limit: int = 40) -> str:
    subset = rows[-max(1, min(200, int(limit))):]
    lines: List[str] = []
    for r in subset:
        sender = str(r.get("sender") or "unknown")
        content = str(r.get("content") or "").strip()
        if not content:
            continue
        lines.append(f"{sender}: {content}")
    return "\n".join(lines)


def _context_rows_for_turn(*, session_file: Path, options: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    opts = options if isinstance(options, dict) else {}
    if bool(opts.get("_scheduled_turn")):
        return []
    mode = str(opts.get("_scheduled_context_mode") or "").strip().lower()
    if mode == "run_scoped_no_history":
        return []
    return _tail_rows(session_file, limit=80)


def _is_text_like_file(path: Path) -> bool:
    suf = path.suffix.lower()
    return suf in {
        ".py", ".md", ".txt", ".json", ".jsonl", ".yaml", ".yml", ".toml",
        ".ini", ".cfg", ".sh", ".bash", ".zsh", ".js", ".ts", ".tsx", ".jsx",
        ".css", ".html", ".xml", ".csv",
    }


def _normalize_repo_path_hint(raw: str) -> str:
    s = str(raw or "").strip().strip("`'\"")
    s = s.replace("\\", "/")
    s = re.sub(r"^[./]+", "", s)
    return s


def _extract_repo_path_hints(text: str, *, limit: int = 10) -> List[str]:
    hints: List[str] = []
    for m in re.finditer(r"([A-Za-z0-9_.\-/]+?\.[A-Za-z0-9]{1,8})", str(text or "")):
        p = _normalize_repo_path_hint(m.group(1))
        if "/" not in p and "." not in p:
            continue
        if p and p not in hints:
            hints.append(p)
        if len(hints) >= max(1, int(limit)):
            break
    return hints


def _extract_repo_terms(text: str, *, limit: int = 6) -> List[str]:
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "what", "where", "when",
        "your", "you", "have", "has", "into", "then", "than", "just", "need", "please",
        "agent", "panel", "role", "task", "ledger", "bind", "send", "handoff",
    }
    out: List[str] = []
    for t in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,31}", str(text or "").lower()):
        if t in stop:
            continue
        if t not in out:
            out.append(t)
        if len(out) >= max(1, int(limit)):
            break
    return out


def _safe_resolve_under(root: Path, rel: str) -> Optional[Path]:
    try:
        p = (root / rel).resolve()
        p.relative_to(root.resolve())
        return p
    except Exception:
        return None


def _read_file_excerpt(path: Path, *, max_chars: int = 2400) -> str:
    try:
        txt = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    txt = txt[: max(200, int(max_chars))]
    return txt


def _rg_hits(repo_root: Path, terms: List[str], *, max_files: int = 4) -> List[Tuple[str, int, str]]:
    if not terms or shutil.which("rg") is None:
        return []
    patt = "|".join([re.escape(t) for t in terms[:4] if t])
    if not patt:
        return []
    cmd = [
        "rg",
        "-n",
        "-S",
        "-m",
        "1",
        "--glob",
        "!governance/ledger/patch_instruction_sheets/**",
        "--glob",
        "!runtime/live/**",
        "--glob",
        "!runtime/live/**",
        "--glob",
        "!.git/**",
        patt,
        str(repo_root),
    ]
    try:
        cp = subprocess.run(cmd, capture_output=True, text=True, check=False, timeout=2.5)
    except Exception:
        return []
    lines = [ln for ln in str(cp.stdout or "").splitlines() if ln.strip()]
    out: List[Tuple[str, int, str]] = []
    seen: set[str] = set()
    for ln in lines:
        m = re.match(r"^(.*?):(\d+):(.*)$", ln)
        if not m:
            continue
        fp = str(m.group(1) or "").strip()
        try:
            rel = Path(fp).resolve().relative_to(repo_root.resolve()).as_posix()
        except Exception:
            continue
        if rel in seen:
            continue
        seen.add(rel)
        try:
            num = int(m.group(2))
        except Exception:
            num = 0
        snippet = str(m.group(3) or "").strip()
        out.append((rel, num, snippet))
        if len(out) >= max(1, int(max_files)):
            break
    return out


def _build_repo_context(*, message: str, context_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    repo_root = _repo_root().resolve()
    recent = _format_context_messages(context_rows, limit=20)
    combo = f"{message}\n{recent}"
    path_hints = _extract_repo_path_hints(combo, limit=10)
    terms = _extract_repo_terms(combo, limit=6)

    files: List[Dict[str, Any]] = []
    for rel in path_hints:
        p = _safe_resolve_under(repo_root, rel)
        if p is None or not p.exists() or not p.is_file() or p.is_symlink():
            continue
        if not _is_text_like_file(p):
            continue
        excerpt = _read_file_excerpt(p, max_chars=2200)
        if not excerpt:
            continue
        files.append({"path": p.relative_to(repo_root).as_posix(), "excerpt": excerpt})
        if len(files) >= REPO_CONTEXT_MAX_FILES:
            break

    if len(files) < REPO_CONTEXT_MAX_FILES:
        for rel, line_no, snippet in _rg_hits(repo_root, terms, max_files=REPO_CONTEXT_MAX_FILES * 2):
            if any(str(f.get("path")) == rel for f in files):
                continue
            p = _safe_resolve_under(repo_root, rel)
            if p is None or not p.exists() or not p.is_file() or p.is_symlink():
                continue
            if not _is_text_like_file(p):
                continue
            excerpt = _read_file_excerpt(p, max_chars=1600)
            if not excerpt:
                continue
            files.append(
                {
                    "path": rel,
                    "hit": {"line": int(line_no), "snippet": snippet[:220]},
                    "excerpt": excerpt,
                }
            )
            if len(files) >= REPO_CONTEXT_MAX_FILES:
                break

    lines: List[str] = [
        "Repository workspace context:",
        f"- repo_root: {str(repo_root)}",
    ]
    if files:
        lines.append(f"- included_files: {len(files)}")
        for f in files:
            rel = str(f.get("path") or "")
            hit = f.get("hit")
            if isinstance(hit, dict):
                lines.append(f"\n[repo_file] {rel} (hit line {int(hit.get('line') or 0)})")
                hs = str(hit.get("snippet") or "").strip()
                if hs:
                    lines.append(f"[hit_snippet] {hs}")
            else:
                lines.append(f"\n[repo_file] {rel}")
            lines.append("```text")
            lines.append(str(f.get("excerpt") or ""))
            lines.append("```")
    else:
        lines.append("- included_files: 0")
        lines.append("- note: no direct file hints resolved from message/context; ask for a path if needed.")

    text = "\n".join(lines)
    text = text[:REPO_CONTEXT_MAX_CHARS]
    return {"text": text, "files": [str(f.get("path") or "") for f in files]}


def _prepare_reply_prompt_context(
    *,
    role: str,
    binding: Dict[str, Any],
    message: str,
    options: Optional[Dict[str, Any]],
    context_rows: List[Dict[str, Any]],
    prompt_bundle: Dict[str, Any],
) -> Dict[str, Any]:
    if bool((options or {}).get("_scheduled_turn")):
        return {
            "route_mode": "scheduled_execution_bypass",
            "route_reasons": ["scheduled_turn"],
            "pis_binding": {},
            "instructions_text": str(prompt_bundle["instructions"] or ""),
            "model_input": str(message or "").strip(),
            "repo_context_enabled": False,
            "repo_context_files": [],
            "pipeline_context_used": False,
            "pipeline_error": "",
            "cache_hit": False,
            "likely_tests": [],
        }
    scheduled_context_mode = str(((options or {}).get("_scheduled_context_mode") or "")).strip().lower()
    if scheduled_context_mode:
        return {
            "route_mode": "scheduled_execution_bypass",
            "route_reasons": ["scheduled_context_mode"],
            "pis_binding": {},
            "instructions_text": str(prompt_bundle["instructions"] or ""),
            "model_input": str(message or "").strip(),
            "repo_context_enabled": False,
            "repo_context_files": [],
            "pipeline_context_used": False,
            "pipeline_error": "",
            "cache_hit": False,
            "likely_tests": [],
        }
    recent_context = _format_context_messages(context_rows, limit=50)
    repo_ctx = _build_repo_context(message=message, context_rows=context_rows)
    route_ctx = _context_rich_reply.build_context_rich_reply_material(
        repo_root=_repo_root(),
        message=message,
        binding=binding,
        context_rows=context_rows,
    )
    route_mode = str(route_ctx.get("route_mode") or "cheap_reply")
    instructions_text = str(prompt_bundle["instructions"] or "")
    if route_mode != "cheap_reply":
        addendum = str(route_ctx.get("instructions_addendum") or "").strip()
        if addendum:
            instructions_text = f"{instructions_text}\n\n{addendum}".strip()
    reply_context_text = str(route_ctx.get("model_context_text") or "").strip()
    reply_context_files = list(route_ctx.get("repo_context_files") or [])
    if not reply_context_text:
        reply_context_text = str(repo_ctx["text"])
        reply_context_files = list(repo_ctx.get("files") or [])
    model_input = (
        "Conversation context (latest first to oldest not guaranteed):\n"
        f"{recent_context}\n\n"
        f"{reply_context_text}\n\n"
        "Respond to the latest user message.\n"
    )
    return {
        "route_mode": route_mode,
        "route_reasons": list(route_ctx.get("route_reasons") or []),
        "pis_binding": dict(route_ctx.get("pis_binding") or {}),
        "instructions_text": instructions_text,
        "model_input": model_input,
        "repo_context_enabled": True,
        "repo_context_files": reply_context_files,
        "pipeline_context_used": bool(route_ctx.get("pipeline_context_used")),
        "pipeline_error": str(route_ctx.get("pipeline_error") or ""),
        "cache_hit": bool(route_ctx.get("cache_hit")),
        "likely_tests": list(route_ctx.get("likely_tests") or []),
    }


def send_scheduled_message(*, role: str, binding: Dict[str, Any], message: str, options: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    scheduled_options = dict(options or {})
    scheduled_options["_scheduled_turn"] = True
    scheduled_options.setdefault("_scheduled_context_mode", "run_scoped_no_history")
    return send_message(role=role, binding=binding, message=message, options=scheduled_options)


def read_session(*, role: str, binding: Dict[str, Any], tail: int = 80, inbox_tail: int = 50) -> Dict[str, Any]:
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    sfile = _session_file(rr["chat_dir"], r)
    ifile = _inbox_file(rr["chat_dir"], r)
    bubble_path = _mobile_bubble_projection_path(binding=rr["binding"], role=r)
    session_rows = _user_visible_session_rows(_tail_rows(sfile, limit=tail))
    inbox_rows = _tail_rows(ifile, limit=inbox_tail)
    latest_handoff = _latest_handoff_info(rr["base_dir"], rr["handoff_dir"])
    failed_action = _latest_failed_action_log(rr["base_dir"]) if rr["binding"]["type"] == "ledger_uid" else {}
    return {
        "ok": True,
        "role": r,
        "binding": rr["binding"],
        "paths": {
            "session_file": str(sfile),
            "inbox_file": str(ifile),
            "handoff_dir": str(rr["handoff_dir"]),
            "mobile_bubble_file": str(bubble_path or ""),
        },
        "session": session_rows,
        "session_count": len(_read_jsonl(sfile)),
        "inbox": inbox_rows,
        "inbox_count": len(_read_jsonl(ifile)),
        "stream_state": None,
        "resume": {
            "latest_handoff": latest_handoff,
            "latest_failed_action": failed_action,
        },
    }


def session_history_rows(*, role: str, binding: Dict[str, Any], tail: int = 200) -> List[Dict[str, str]]:
    """Return simplified user/assistant rows from the canonical session transcript."""
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    sfile = _session_file(rr["chat_dir"], r)
    limit = int(tail)
    rows = _read_jsonl(sfile, max_lines=limit if limit > 0 else 0)
    return _session_history_rows_from_rows(rows, tail=tail)


def _session_row_equivalent_for_mobile_dedupe(prev: Dict[str, Any], cur: Dict[str, Any]) -> bool:
    prev_sender = str(prev.get("sender") or "").strip().lower()
    cur_sender = str(cur.get("sender") or "").strip().lower()
    if prev_sender != cur_sender:
        return False
    if str(prev.get("content") or "") != str(cur.get("content") or ""):
        return False
    if str(prev.get("status") or "ok").strip().lower() != str(cur.get("status") or "ok").strip().lower():
        return False
    prev_meta = prev.get("meta") if isinstance(prev.get("meta"), dict) else {}
    cur_meta = cur.get("meta") if isinstance(cur.get("meta"), dict) else {}
    if str(prev_meta.get("source") or "").strip().lower() != "mobile_chat_stream":
        return False
    if str(cur_meta.get("source") or "").strip().lower() != "mobile_chat_stream":
        return False
    prev_ts = _parse_iso_utc(str(prev.get("utc") or ""))
    cur_ts = _parse_iso_utc(str(cur.get("utc") or ""))
    if prev_ts is None or cur_ts is None:
        return False
    delta_s = abs((cur_ts - prev_ts).total_seconds())
    return delta_s <= float(_mobile_task_duplicate_window_s())


def _dedupe_adjacent_session_jsonl_lines(lines: List[str]) -> List[str]:
    out: List[str] = []
    prev_obj: Optional[Dict[str, Any]] = None
    for raw in lines:
        line = str(raw or "").strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except Exception:
            out.append(line)
            prev_obj = None
            continue
        if not isinstance(obj, dict):
            out.append(line)
            prev_obj = None
            continue
        if prev_obj is not None and _session_row_equivalent_for_mobile_dedupe(prev_obj, obj):
            continue
        out.append(line)
        prev_obj = obj
    return out


def _compact_mobile_task_session_file_if_needed(path: Path, *, force: bool = False) -> bool:
    lock = _file_lock(path)
    with lock:
        try:
            if not path.exists() or (not path.is_file()) or path.is_symlink():
                return False
            if (not force) and int(path.stat().st_size) <= _mobile_task_session_retention_max_bytes():
                return False
            lines = _tail_text_lines(path, _mobile_task_session_retention_max_lines())
            lines = _dedupe_adjacent_session_jsonl_lines(lines)
            raw = ("\n".join(lines) + "\n") if lines else ""
            tmp = (path.parent / f".{path.name}.tmp").resolve()
            with open(tmp, "w", encoding="utf-8", newline="\n") as f:
                f.write(raw)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
            return True
        except Exception:
            return False


def append_session_message(
    *,
    role: str,
    binding: Dict[str, Any],
    sender: str,
    content: str,
    status: str = "ok",
    meta: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Append one canonical session transcript row and refresh derived mobile bubble files."""
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    snd = str(sender or "").strip().lower()
    if snd not in {"user", "assistant"}:
        raise ValueError("sender must be user or assistant")
    txt = str(content or "")
    if not txt:
        raise ValueError("content is required")
    row_meta = dict(meta) if isinstance(meta, dict) and meta else {}
    if snd == "assistant":
        visible_txt = _user_visible_assistant_content(txt)
        if visible_txt != txt:
            row_meta["raw_content"] = txt
        txt = visible_txt
    row: Dict[str, Any] = {
        "schema_version": "agent_chat_message_v1",
        "utc": _utc_now_iso(),
        "role_panel": r,
        "sender": snd,
        "content": txt,
        "status": str(status or "ok"),
        "binding": rr["binding"],
    }
    if row_meta:
        row["meta"] = row_meta
    sfile = _session_file(rr["chat_dir"], r)
    needs_mobile_compaction = False
    lock = _file_lock(sfile)
    wrote = True
    with lock:
        if _is_mobile_task_binding(rr["binding"]):
            prev = _read_last_jsonl_row(sfile)
            if prev is not None and _session_row_equivalent_for_mobile_dedupe(prev, row):
                wrote = False
            else:
                line = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
                with open(sfile, "a", encoding="utf-8", newline="\n") as f:
                    f.write(line)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())
                needs_mobile_compaction = True
        else:
            line = json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
            with open(sfile, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
    if needs_mobile_compaction:
        _compact_mobile_task_session_file_if_needed(sfile)
    _refresh_mobile_bubble_projection(binding=rr["binding"], role=r, session_file=sfile)
    if wrote:
        return row
    return {**row, "deduped": True}


def clear_session_data(*, role: str, binding: Dict[str, Any], target: str = "session") -> Dict[str, Any]:
    rr = _resolve_paths(_normalize_binding(binding))
    r = _validate_role(role)
    t = str(target or "session").strip().lower()
    if t not in ("session", "inbox", "both"):
        raise ValueError("target must be session|inbox|both")
    sfile = _session_file(rr["chat_dir"], r)
    ifile = _inbox_file(rr["chat_dir"], r)
    stfile = _stream_state_file(rr["chat_dir"], r)
    if t in ("session", "both"):
        _truncate_file(sfile)
        try:
            stfile.unlink()
        except Exception:
            pass
    if t in ("inbox", "both"):
        _truncate_file(ifile)
    bubble_path = _refresh_mobile_bubble_projection(binding=rr["binding"], role=r, session_file=sfile)
    return {
        "ok": True,
        "binding": rr["binding"],
        "role": r,
        "target": t,
        "session_count": len(_read_jsonl(sfile)),
        "inbox_count": len(_read_jsonl(ifile)),
        "mobile_bubble_file": str(bubble_path or ""),
    }


def resolve_resume_artifact(*, role: str, binding: Dict[str, Any], kind: str) -> Dict[str, Any]:
    rr = _resolve_paths(_normalize_binding(binding))
    _validate_role(role)
    k = str(kind or "").strip().lower()
    if k == "latest_handoff":
        h = _latest_handoff_info(rr["base_dir"], rr["handoff_dir"])
        rel = str(h.get("path") or "")
        if not rel:
            raise FileNotFoundError("no handoff artifact")
        p = (rr["base_dir"] / rel).resolve()
    elif k == "latest_failed_action_log":
        if rr["binding"]["type"] != "ledger_uid":
            raise FileNotFoundError("no failed action artifact for task binding")
        a = _latest_failed_action_log(rr["base_dir"])
        rel = str(a.get("log_path") or "")
        if not rel:
            raise FileNotFoundError("no failed action log")
        p = (rr["base_dir"] / rel).resolve()
    else:
        raise ValueError("invalid artifact kind")
    if p.is_symlink() or not p.exists() or not p.is_file():
        raise FileNotFoundError("artifact not found")
    try:
        p.resolve().relative_to(rr["base_dir"].resolve())
    except Exception:
        raise ValueError("artifact escapes binding root")
    return {"path": str(p), "name": p.name}
