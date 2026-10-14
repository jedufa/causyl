def _mobile_apk_meta_path(apk_path: Path) -> Path:
    return apk_path.with_name(f"{apk_path.name}.meta.json").resolve()


def _mobile_apk_read_meta(apk_path: Path) -> Dict[str, Any]:
    meta_path = _mobile_apk_meta_path(apk_path)
    if not meta_path.exists() or not meta_path.is_file():
        return {}
    try:
        obj = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _mobile_apk_find_release(repo_root: Path, channel: str) -> Optional[Dict[str, Any]]:
    rr = Path(repo_root).resolve()
    safe_channel = str(channel or "").strip().lower()
    meta = _MOBILE_APK_CHANNEL_META.get(safe_channel)
    if meta is None:
        return None
    chosen: Optional[Path] = None
    for cand in _mobile_apk_candidate_paths(rr, safe_channel):
        safe = _safe_resolve_under(rr, cand)
        if safe and safe.exists() and safe.is_file():
            chosen = safe
            break
    if chosen is None:
        return None
    st = chosen.stat()
    import datetime as _dt

    built_at = _dt.datetime.fromtimestamp(float(st.st_mtime), tz=_dt.timezone.utc)
    meta_obj = _mobile_apk_read_meta(chosen)
    # F5M6: Fail-closed release gating — a channel has a release only
    # if a valid meta sidecar exists and declares the expected schema.
    if (not isinstance(meta_obj, dict)) or (not meta_obj):
        return None
    if str(meta_obj.get("schema_version") or "") != "mobile_apk_release_meta_v1":
        return None
    version_name = str(meta_obj.get("version_name") or "").strip()
    if not version_name:
        base_version = str(meta.get("base_version_name") or "0.1.0").strip() or "0.1.0"
        version_name = f"{base_version}-debug" if safe_channel == "debug" else base_version
    try:
        version_code = int(meta_obj.get("version_code") or 1)
    except Exception:
        version_code = 1
    built_at_value = str(meta_obj.get("built_at") or "").strip() or built_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return {
        "channel": safe_channel,
        "path": chosen,
        "package_name": meta["package_name"],
        "apk_name": str(meta_obj.get("apk_name") or chosen.name),
        "version_name": version_name,
        "version_code": version_code,
        "apk_size_bytes": int(st.st_size),
        "apk_sha256": _sha256_file(chosen),
        "built_at": built_at_value,
        "source_path": _posix_relpath(chosen, rr),
    }


def _mobile_update_guard(handler: BaseHTTPRequestHandler, cfg: WebUIConfig) -> bool:
    """Allow LAN/mobile updater access without requiring the full admin token."""
    if not bool(getattr(cfg, "admin_enabled", True)):
        _json_response(handler, HTTPStatus.FORBIDDEN, {"error": "admin endpoints are disabled"})
        return False
    return True


def _mobile_apk_build_kind(channel: str) -> str:
    safe_channel = str(channel or "").strip().lower()
    return f"mobile_apk_build:{safe_channel}"


def _mobile_apk_running_build_job(state: _ServerState, channel: str) -> Optional[AdminCmdJob]:
    target_kind = _mobile_apk_build_kind(channel)
    with state.lock:
        jobs = list(state.admin_cmd_jobs.values())
    for job in reversed(jobs):
        if str(getattr(job, "kind", "") or "") == target_kind and bool(getattr(job, "running", False)):
            return job
    return None


def _mobile_apk_latest_logged_build_job(cfg: WebUIConfig, channel: str) -> Optional[Dict[str, Any]]:
    """Return the newest completed mobile APK build job from persisted logs."""

    safe_channel = str(channel or "").strip().lower()
    if safe_channel not in _MOBILE_APK_CHANNEL_META:
        return None
    logs_dir = _admin_cmd_logs_dir(cfg)
    pattern = f"*__mobile_apk_build_{safe_channel}__*.json"
    target_kind = _mobile_apk_build_kind(safe_channel)
    for path in sorted(logs_dir.glob(pattern), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("kind") or "") != target_kind:
            continue
        return payload
    return None


def _admin_cmd_logged_job_by_id(cfg: WebUIConfig, job_id: str) -> Optional[Dict[str, Any]]:
    """Resolve a finished admin command job from persisted logs by job id."""

    jid = str(job_id or "").strip()
    if not jid:
        return None
    logs_dir = _admin_cmd_logs_dir(cfg)
    for path in sorted(logs_dir.glob(f"*__{jid}.json"), reverse=True):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get("job_id") or "").strip() != jid:
            continue
        return payload
    return None


def _mobile_apk_job_payload_by_id(
    state: _ServerState,
    cfg: WebUIConfig,
    job_id: str,
) -> Optional[Dict[str, Any]]:
    """Resolve a mobile APK build job from active memory or persisted logs."""

    jid = str(job_id or "").strip()
    if not jid:
        return None
    with state.lock:
        job = state.admin_cmd_jobs.get(jid)
    if job is not None:
        return job.to_dict()
    payload = _admin_cmd_logged_job_by_id(cfg, jid)
    if not isinstance(payload, dict):
        return None
    if not str(payload.get("kind") or "").startswith("mobile_apk_build:"):
        return None
    return payload


def _mobile_apk_latest_build_job(state: _ServerState, cfg: WebUIConfig, channel: str) -> Optional[Dict[str, Any]]:
    """Return the newest known mobile APK build job, including recent failures."""

    safe_channel = str(channel or "").strip().lower()
    if safe_channel not in _MOBILE_APK_CHANNEL_META:
        return None
    target_kind = _mobile_apk_build_kind(safe_channel)
    latest_mem: Optional[Dict[str, Any]] = None
    latest_mem_key = ""
    jobs_map = getattr(state, "admin_cmd_jobs", {}) or {}
    lock = getattr(state, "lock", None)
    if lock is None:
        jobs = list(jobs_map.values())
    else:
        with lock:
            jobs = list(jobs_map.values())
    for job in jobs:
        if str(getattr(job, "kind", "") or "") != target_kind:
            continue
        payload = job.to_dict()
        key = str(payload.get("utc_started") or "")
        if key >= latest_mem_key:
            latest_mem = payload
            latest_mem_key = key
    latest_log = _mobile_apk_latest_logged_build_job(cfg, safe_channel)
    latest_log_key = str((latest_log or {}).get("utc_started") or "")
    if latest_mem is None:
        return latest_log
    if latest_log is None:
        return latest_mem
    if latest_log_key > latest_mem_key:
        return latest_log
    return latest_mem


def _mobile_apk_cancel_response_payload(
    handler: BaseHTTPRequestHandler,
    *,
    state: _ServerState,
    cfg: WebUIConfig,
    qs: Dict[str, List[str]],
) -> Tuple[HTTPStatus, Dict[str, Any]]:
    """Cancel a mobile APK build job addressed by query string or JSON body."""

    jid = ""
    ctype = str(handler.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
    if ctype == "application/json":
        obj = _read_request_json(handler, max_bytes=64 * 1024)
        if isinstance(obj, dict):
            jid = str(obj.get("job_id") or "").strip()
    if not jid:
        jid = str((qs.get("job_id") or [""])[0] or "").strip()
    if not jid:
        return HTTPStatus.BAD_REQUEST, {"error": "missing job_id"}
    job_payload = _mobile_apk_job_payload_by_id(state, cfg, jid)
    if job_payload is None:
        return HTTPStatus.NOT_FOUND, {"error": "job not found"}
    if not str(job_payload.get("kind") or "").startswith("mobile_apk_build:"):
        return HTTPStatus.FORBIDDEN, {"error": "job is not a mobile apk build"}
    if not bool(job_payload.get("running")):
        return (
            HTTPStatus.OK,
            {
                "schema_version": "mobile_apk_cancel_v1",
                "utc": _now_utc_iso(),
                "ok": False,
                "already_finished": True,
                "job": job_payload,
            },
        )
    ok = _admin_cmd_kill_job(state, jid)
    job_payload = _mobile_apk_job_payload_by_id(state, cfg, jid)
    return (
        HTTPStatus.OK,
        {
            "schema_version": "mobile_apk_cancel_v1",
            "utc": _now_utc_iso(),
            "ok": bool(ok),
            "already_finished": bool(job_payload and not job_payload.get("running")),
            "job": job_payload,
        },
    )


def _mobile_apk_build_command(repo_root: Path, channel: str) -> str:
    rr = Path(repo_root).resolve()
    safe_channel = str(channel or "").strip().lower()
    script = (rr / "infra" / "mobile" / "build_server_apk.sh").resolve()
    safe_script = _safe_resolve_under(rr, script)
    if safe_script is None or not safe_script.exists():
        raise FileNotFoundError("infra/mobile/build_server_apk.sh not found")
    return f"{shlex.quote(str(safe_script))} {shlex.quote(safe_channel)}"


def _admin_commit_journal_status(*, include_audit: bool = True, busy_reason: str = "") -> Dict[str, Any]:
    """Summarize commit-journal readiness for admin UIs."""
    report = _commit_journal.scan_pending_entries()
    entries = [dict(item["entry"]) for item in list(report.get("valid") or [])]

    def _entry_payload(entry: Dict[str, Any]) -> Dict[str, Any]:
        author = dict(entry.get("author") or {})
        binding = dict(author.get("binding") or {})
        return {
            "entry_id": str(entry.get("entry_id") or "").strip(),
            "created_utc": str(entry.get("created_utc") or "").strip(),
            "title": str(entry.get("title") or "").strip(),
            "summary_bullets": list(entry.get("summary_bullets") or []),
            "files": list(entry.get("files") or []),
            "validation": list(entry.get("validation") or []),
            "notes": list(entry.get("notes") or []),
            "outcome": str(entry.get("outcome") or "").strip(),
            "scope_hint": str(entry.get("scope_hint") or "").strip(),
            "author": {
                "role": str(author.get("role") or "").strip(),
                "source": str(author.get("source") or "").strip(),
                "binding": {
                    "type": str(binding.get("type") or "").strip(),
                    "id": str(binding.get("id") or "").strip(),
                },
            },
        }

    payload: Dict[str, Any] = {
        "pending_count": int(report.get("pending_count") or 0),
        "invalid_count": int(report.get("invalid_count") or 0),
        "outcome_counts": _commit_journal._outcome_counts(entries),  # type: ignore[attr-defined]
        "pending_entry_ids": [str(item["entry"]["entry_id"]) for item in list(report.get("valid") or [])],
        "entries": [_entry_payload(entry) for entry in entries],
        "ready": False,
        "coverage_ok": False,
        "uncovered_paths": [],
        "preview_subject": "",
        "preview_source": "",
        "error": "",
    }
    if not include_audit:
        payload["error"] = str(busy_reason or "").strip()
        return payload
    try:
        audit = _commit_journal.audit_worktree_coverage()
        payload["coverage_ok"] = bool(audit.get("ok"))
        payload["uncovered_paths"] = list(audit.get("uncovered_paths") or [])
        payload["ready"] = bool(report.get("ok")) and (
            int(report.get("pending_count") or 0) > 0 or len(list(audit.get("changed_paths") or [])) > 0
        )
        preview_entries = list(entries)
        fallback_paths = list(audit.get("uncovered_paths") or [])
        full_reconstruction = False
        generic_enrichment = False
        if not entries:
            fallback_paths = list(audit.get("changed_paths") or [])
            full_reconstruction = True
        elif audit.get("changed_paths") and all(_commit_journal._entry_needs_reconstructed_context(entry) for entry in entries):  # type: ignore[attr-defined]
            fallback_paths = list(audit.get("changed_paths") or [])
            generic_enrichment = True
        fallback_entry = _commit_journal._build_worktree_fallback_entry(  # type: ignore[attr-defined]
            fallback_paths,
            ignored_paths=list(audit.get("ignored_paths") or []),
            full_reconstruction=full_reconstruction,
            generic_enrichment=generic_enrichment,
        )
        if fallback_entry is not None:
            preview_entries.append(dict(fallback_entry))
        if preview_entries:
            subject_text, subject_source = _commit_journal._subject_from_entries(preview_entries)  # type: ignore[attr-defined]
            if subject_source == "scope_fallback":
                semantic_subject = _commit_journal._fallback_subject_from_paths(list(audit.get("changed_paths") or []))  # type: ignore[attr-defined]
                if semantic_subject and semantic_subject != "Update repo" and semantic_subject != subject_text:
                    subject_text = semantic_subject
                    subject_source = "worktree_semantic"
            payload["preview_subject"] = str(subject_text or "").strip()
            payload["preview_source"] = str(subject_source or "").strip()
    except Exception as exc:
        payload["error"] = str(exc)
    return payload


def _admin_git_status_payload(*, repo_root: Path, env: Dict[str, str]) -> Dict[str, Any]:
    """Return admin git status without spawning porcelain scans during index-lock contention."""

    def _run_git(args: List[str]) -> Tuple[int, str, str]:
        try:
            p = subprocess.run(["git"] + list(args), cwd=str(repo_root), env=env, capture_output=True, text=True)
            return int(p.returncode), str(p.stdout or ""), str(p.stderr or "")
        except Exception as e:
            return 127, "", str(e)

    rc_b, out_b, err_b = _run_git(["branch", "--show-current"])
    rc_h, out_h, err_h = _run_git(["rev-parse", "--short", "HEAD"])
    rc_d, out_d, err_d = _run_git(["describe", "--tags", "--always", "--dirty"])

    lock_path = repo_root / ".git" / "index.lock"
    repo_busy = lock_path.exists()
    if repo_busy:
        rc_s, out_s, err_s = 1, "", f"git status skipped: index lock present at {lock_path}"
    else:
        rc_s, out_s, err_s = _run_git(["status", "--porcelain=v1"])

    rc_t, out_t, err_t = _run_git(["tag", "--list", "baseline-*"])
    baseline_num_max = 0
    baseline_tag_max = ""
    desc = out_d.strip()
    try:
        tags = [ln.strip() for ln in str(out_t or "").splitlines() if ln.strip()]
        nums = []
        for tg in tags:
            m = re.match(r"^baseline-(\d+)$", tg)
            if m:
                nums.append(int(m.group(1)))
        if nums:
            baseline_num_max = max(nums)
            baseline_tag_max = f"baseline-{baseline_num_max}"
    except Exception:
        baseline_num_max = 0
        baseline_tag_max = ""

    if (not baseline_tag_max) and desc:
        try:
            m = re.search(r"baseline-(\d+)", str(desc))
            if m:
                baseline_num_max = int(m.group(1))
                baseline_tag_max = f"baseline-{baseline_num_max}"
        except Exception:
            pass

    baseline_num_suggested = int(baseline_num_max) + 1 if int(baseline_num_max) >= 0 else 1
    baseline_tag_suggested = f"baseline-{baseline_num_suggested}"
    rc = max(rc_b, rc_h, rc_d, rc_s)

    return {
        "schema_version": "admin_git_status_v1",
        "utc": _now_utc_iso(),
        "returncode": int(rc),
        "branch": out_b.strip(),
        "head": out_h.strip(),
        "describe": desc,
        "status_porcelain": out_s,
        "baseline_num_max": int(baseline_num_max),
        "baseline_num_suggested": int(baseline_num_suggested),
        "baseline_tag_max": str(baseline_tag_max),
        "baseline_tag_suggested": str(baseline_tag_suggested),
        "commit_journal": _admin_commit_journal_status(
            include_audit=not repo_busy,
            busy_reason=f"commit journal audit skipped: index lock present at {lock_path}" if repo_busy else "",
        ),
        "repo_busy": bool(repo_busy),
        "stderr": "\n".join([err_b.strip(), err_h.strip(), err_d.strip(), err_s.strip(), err_t.strip()]).strip(),
    }


def _mobile_sessions_load_file(p: Path) -> Tuple[Dict[str, List[Dict[str, str]]], Dict[str, int]]:
    rows: Dict[str, List[Dict[str, str]]] = {}
    caps: Dict[str, int] = {}
    if not p.exists() or (not p.is_file()):
        return rows, caps
    obj = _read_json(p)
    if not isinstance(obj, dict):
        return rows, caps
    raw = obj.get("sessions")
    if isinstance(raw, dict):
        for k, v in raw.items():
            if not isinstance(v, list):
                continue
            rr: List[Dict[str, str]] = []
            for it in v:
                if not isinstance(it, dict):
                    continue
                role = str(it.get("role") or "").strip().lower()
                content = str(it.get("content") or "")
                if role in {"user", "assistant"} and content:
                    rr.append({"role": role, "content": content})
            if rr:
                rows[str(k)] = rr
    raw_caps = obj.get("caps")
    if isinstance(raw_caps, dict):
        for k, v in raw_caps.items():
            try:
                iv = int(v)
            except Exception:
                continue
            if iv >= 1:
                caps[str(k)] = iv
    return rows, caps


def _mobile_sessions_payload_has_history(
    rows: Dict[str, List[Dict[str, str]]],
    caps: Dict[str, int],
) -> bool:
    """Return whether a mobile sessions payload contains recoverable state."""
    if any(isinstance(history, list) and history for history in rows.values()):
        return True
    return any(int(value) >= 1 for value in caps.values())


def _mobile_merge_history_rows(left: List[Dict[str, str]], right: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Prefer the newest known history without heuristically merging transcripts."""
    lhs = [dict(row) for row in left if isinstance(row, dict)]
    rhs = [dict(row) for row in right if isinstance(row, dict)]
    if rhs:
        return rhs
    return lhs


def _mobile_merge_session_maps(
    base_rows: Dict[str, List[Dict[str, str]]],
    new_rows: Dict[str, List[Dict[str, str]]],
) -> Dict[str, List[Dict[str, str]]]:
    merged: Dict[str, List[Dict[str, str]]] = {str(k): list(v) for k, v in base_rows.items()}
    for tid, rows in new_rows.items():
        key = str(tid)
        merged[key] = _mobile_merge_history_rows(merged.get(key, []), rows)
    return merged


def _mobile_reconcile_canonical_history(tid: str) -> None:
    """Backfill canonical mobile transcripts from persisted mobile cache history."""
    if not _MOBILE_SESSIONS_LOADED:
        return
    cached_rows = _mobile_session_history(tid)
    if not cached_rows:
        return
    role = _mobile_role_for_thread_id(tid)
    try:
        _agents.reconcile_simplified_session_history(
            role=role,
            binding={"type": "task_id", "id": str(tid)},
            recovered_history=cached_rows,
        )
    except Exception:
        return


def _mobile_sessions_legacy_candidates(cfg: WebUIConfig) -> List[Path]:
    if not _mobile_sessions_import_legacy_enabled():
        return []
    base = _repo_root_from_webui()
    primary = _mobile_sessions_primary_path()
    direct = [
        (Path(cfg.runs_root) / ".ui_cache" / "mobile_chat_sessions.json").resolve(),
        (Path(base) / "governance" / "webui" / "runtime" / "mobile_chat_sessions.json").resolve(),
        (Path(base) / "governance" / "agents" / "runtime" / "mobile_chat_sessions.json").resolve(),
        (Path(base) / "runtime" / "live" / ".ui_cache" / "mobile_chat_sessions.json").resolve(),
        (Path(base) / "runs" / ".ui_cache" / "mobile_chat_sessions.json").resolve(),
    ]
    existing_direct = [
        p
        for p in direct
        if p != primary and p.exists() and p.is_file() and _mobile_session_file_loadable_at_startup(p)
    ]
    primary_has_history = False
    if _mobile_session_file_loadable_at_startup(primary):
        try:
            primary_rows, primary_caps = _mobile_sessions_load_file(primary)
            primary_has_history = _mobile_sessions_payload_has_history(primary_rows, primary_caps)
        except Exception:
            primary_has_history = False
    if primary_has_history:
        return []
    if existing_direct:
        return existing_direct
    roots = [
        Path(cfg.runs_root).resolve(),
        (Path(base) / "runtime" / "state").resolve(),
        (Path(base) / "runtime" / "live").resolve(),
        (Path(base) / "governance" / "webui").resolve(),
        (Path(base) / "governance" / "agents" / "runtime").resolve(),
        (Path(base) / "runs").resolve(),
    ]
    discovered: List[Path] = []
    for root in roots:
        if not root.exists() or (not root.is_dir()):
            continue
        try:
            for p in root.rglob("mobile_chat_sessions.json"):
                rp = p.resolve()
                if rp == primary or (not _mobile_session_file_loadable_at_startup(rp)):
                    continue
                discovered.append(rp)
        except Exception:
            continue
    discovered = sorted(
        set(discovered),
        key=lambda p: (
            float(p.stat().st_mtime) if p.exists() else 0.0,
            str(p),
        ),
    )
    out: List[Path] = []
    seen: set[str] = set()
    for p in [*existing_direct, *discovered]:
        k = str(p)
        if k in seen:
            continue
        seen.add(k)
        out.append(p)
    return out


def _mobile_sessions_init(cfg: WebUIConfig) -> None:
    """Load persisted mobile chat sessions from stable repo path once.

    Migration:
    - New canonical location:
      <repo_root>/runtime/state/webui/mobile_chat_sessions.json
    - Legacy import is enabled by default so older cache locations reconcile
      forward automatically; set EIEL_MOBILE_SESSIONS_IMPORT_LEGACY=0 to skip it.
    """
    global _MOBILE_SESSIONS, _MOBILE_SESSION_CAPS, _MOBILE_SESSIONS_PATH, _MOBILE_SESSIONS_LOADED
    reconcile_snapshot: Dict[str, List[Dict[str, str]]] = {}
    with _MOBILE_SESSION_LOCK:
        if _MOBILE_SESSIONS_LOADED:
            return
        p = _mobile_sessions_primary_path()
        _MOBILE_SESSIONS_PATH = p
        rows: Dict[str, List[Dict[str, str]]] = {}
        caps: Dict[str, int] = {}
        try:
            sources = list(_mobile_sessions_legacy_candidates(cfg))
            if _mobile_session_file_loadable_at_startup(p):
                sources.append(p)
            for src in sources:
                if not src.exists() or (not src.is_file()):
                    continue
                loaded_rows, loaded_caps = _mobile_sessions_load_file(src)
                rows = _mobile_merge_session_maps(rows, loaded_rows)
                for key, value in loaded_caps.items():
                    prev = int(caps.get(key, 0))
                    caps[str(key)] = max(prev, int(value))
        except Exception:
            rows = {}
            caps = {}
        _MOBILE_SESSIONS = rows
        _MOBILE_SESSION_CAPS = caps
        _mobile_sessions_flush_locked()
        _MOBILE_SESSIONS_LOADED = True
        reconcile_snapshot = {str(k): list(v) for k, v in rows.items() if v}
    for tid in sorted(reconcile_snapshot.keys()):
        _mobile_reconcile_canonical_history(tid)
    canonical_snapshot: Dict[str, List[Dict[str, str]]] = {}
    known_tids = sorted(set(reconcile_snapshot.keys()) | set(_mobile_known_thread_ids()))
    for tid in known_tids:
        role = _mobile_role_for_thread_id(tid)
        try:
            rows = _agents.session_history_rows(
                role=role,
                binding={"type": "task_id", "id": str(tid)},
                tail=_mobile_history_window_rows(),
            )
        except Exception:
            continue
        if rows:
            canonical_snapshot[str(tid)] = [dict(row) for row in rows if isinstance(row, dict)]
    if canonical_snapshot:
        with _MOBILE_SESSION_LOCK:
            for tid, rows in canonical_snapshot.items():
                _MOBILE_SESSIONS[str(tid)] = [dict(row) for row in rows if isinstance(row, dict)]
            _mobile_sessions_flush_locked()
    try:
        _agents.refresh_mobile_bubble_projections()
    except Exception:
        pass


def _mobile_reset_all_sessions(*, clear_agents: bool = True) -> Dict[str, Any]:
    """Hard reset mobile chat state to recover from stale migration/session pointers."""
    cleared_bindings: List[Dict[str, str]] = []
    with _MOBILE_SESSION_LOCK:
        _MOBILE_SESSIONS.clear()
        _MOBILE_SESSION_CAPS.clear()
        _mobile_sessions_flush_locked()
    if clear_agents:
        seen: set[Tuple[str, str]] = set()
        for spec in _MOBILE_AGENT_SPECS.values():
            tid = _coerce_mobile_task_id(spec.get("thread_id") or "mbl")
            role = _mobile_role_for_thread_id(tid)
            key = (role, tid)
            if key in seen:
                continue
            seen.add(key)
            binding = {"type": "task_id", "id": tid}
            try:
                _agents.clear_session_data(role=role, binding=binding, target="both")
                cleared_bindings.append({"role": role, "thread_id": tid})
            except Exception:
                continue
    try:
        _agents.refresh_mobile_bubble_projections()
    except Exception:
        pass
    return {
        "ok": True,
        "cleared_bindings": cleared_bindings,
        "storage_file": str(_MOBILE_SESSIONS_PATH or _mobile_sessions_primary_path()),
        "legacy_import_enabled": bool(_mobile_sessions_import_legacy_enabled()),
    }


def _mobile_sessions_flush_locked() -> None:
    """Persist mobile sessions; caller must hold _MOBILE_SESSION_LOCK."""
    try:
        out = {
            "schema_version": "mobile_chat_sessions_v1",
            "updated_at_utc": _now_utc_iso(),
            "sessions": _MOBILE_SESSIONS,
            "caps": _MOBILE_SESSION_CAPS,
        }
        p = _MOBILE_SESSIONS_PATH
        if p is not None:
            p.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_canonical_json(p, out)
    except Exception:
        pass


def _mobile_session_history(tid: str) -> List[Dict[str, str]]:
    with _MOBILE_SESSION_LOCK:
        rows = _MOBILE_SESSIONS.get(tid)
        if rows is None:
            rows = []
            _MOBILE_SESSIONS[tid] = rows
        return list(rows)


def _mobile_role_for_thread_id(tid: str) -> str:
    want = str(tid or "").strip().lower()
    for spec in _MOBILE_AGENT_SPECS.values():
        if str(spec.get("thread_id") or "").strip().lower() == want:
            return str(spec.get("role") or "generalist").strip().lower() or "generalist"
    return "generalist"


def _mobile_canonical_history(tid: str, *, tail: int = 200, repo_root: Optional[Path] = None) -> List[Dict[str, str]]:
    _mobile_reconcile_canonical_history(tid)
    role = _mobile_role_for_thread_id(tid)
    plane_routed = str(tid or "").strip().lower() == "mpl"
    root = Path(repo_root or _repo_root_from_webui()).resolve()
    try:
        from infra.webui import server_lab_assistant_turns as _lab_turns

        projection = _lab_turns.recent_turn_projections(
            root,
            limit=max(1, int(tail or 200)),
            binding={"type": "task_id", "id": str(tid)},
            role=role,
        )
    except Exception:
        projection = {}
    turn_rows = list(projection.get("turns") or []) if isinstance(projection, dict) else []
    if turn_rows:
        canonical_rows: List[Dict[str, str]] = []
        ordered_rows = sorted(
            turn_rows,
            key=lambda row: (
                str(row.get("created_utc") or ""),
                str(row.get("updated_utc") or ""),
                str(row.get("turn_id") or ""),
            ),
        )
        for row in ordered_rows[-max(1, int(tail or 200)):]:
            turn_id = str(row.get("turn_id") or "").strip()
            if not turn_id:
                continue
            try:
                replay = _lab_turns.replay_turn_events(root, turn_id=turn_id, after_event_seq=0)
            except Exception:
                replay = {}
            turn = dict(replay.get("turn") or {})
            user_text = str(turn.get("request_text") or "").strip()
            assistant_text = str(replay.get("content") or "")
            replay_status = str(replay.get("replay_status") or row.get("state") or "").strip().lower()
            classification = dict(replay.get("classification") or {})
            if user_text:
                user_row: Dict[str, str] = {"role": "user", "content": user_text}
                if plane_routed or bool(classification.get("plane_routed")):
                    user_row["plane_routed"] = True
                canonical_rows.append(user_row)
            if assistant_text:
                assistant_row: Dict[str, str] = {"role": "assistant", "content": assistant_text}
                if replay_status and replay_status not in {"completed", "ok", "complete"}:
                    assistant_row["status"] = replay_status
                if plane_routed or bool(classification.get("plane_routed")):
                    assistant_row["plane_routed"] = True
                canonical_rows.append(assistant_row)
        if canonical_rows:
            return canonical_rows[-max(1, int(tail or 200)) :]
    try:
        return _agents.session_history_rows(
            role=role,
            binding={"type": "task_id", "id": str(tid)},
            tail=tail,
        )
    except Exception:
        return []


def _mobile_effective_history(tid: str, *, tail: int = 200, repo_root: Optional[Path] = None) -> List[Dict[str, str]]:
    canonical = _mobile_canonical_history(tid, tail=tail, repo_root=repo_root)
    if canonical:
        return canonical
    role = _mobile_role_for_thread_id(tid)
    try:
        agent_rows = _agents.session_history_rows(
            role=role,
            binding={"type": "task_id", "id": str(tid)},
            tail=tail,
        )
    except Exception:
        agent_rows = []
    if agent_rows:
        return agent_rows[-max(1, int(tail or 200)) :]
    session_rows = _mobile_session_history(tid)
    if session_rows:
        return session_rows[-max(1, int(tail or 200)) :]
    return session_rows


def _mobile_session_cap(tid: str) -> int:
    with _MOBILE_SESSION_LOCK:
        v = int(_MOBILE_SESSION_CAPS.get(str(tid), _MOBILE_SESSION_MAX_TURNS))
        return max(1, v)


def _mobile_session_set_cap(tid: str, cap: int) -> int:
    with _MOBILE_SESSION_LOCK:
        v = max(1, int(cap))
        _MOBILE_SESSION_CAPS[str(tid)] = v
        _mobile_sessions_flush_locked()
        return v


def _mobile_session_append(tid: str, *, user_text: str, assistant_text: str) -> None:
    if not str(user_text or "").strip():
        return
    if not str(assistant_text or "").strip():
        return
    with _MOBILE_SESSION_LOCK:
        rows = _MOBILE_SESSIONS.get(tid)
        if rows is None:
            rows = []
            _MOBILE_SESSIONS[tid] = rows
        rows.append({"role": "user", "content": str(user_text or "")})
        rows.append({"role": "assistant", "content": str(assistant_text or "")})
        cap_turns = int(_MOBILE_SESSION_CAPS.get(str(tid), _MOBILE_SESSION_MAX_TURNS))
        cap = int(max(2, max(1, cap_turns) * 2))
        if len(rows) > cap:
            del rows[: len(rows) - cap]
        _mobile_sessions_flush_locked()


def _mobile_session_append_user(tid: str, *, user_text: str) -> None:
    """Persist user message immediately, even if assistant response fails later."""
    if not str(user_text or "").strip():
        return
    with _MOBILE_SESSION_LOCK:
        rows = _MOBILE_SESSIONS.get(tid)
        if rows is None:
            rows = []
            _MOBILE_SESSIONS[tid] = rows
        rows.append({"role": "user", "content": str(user_text or "")})
        cap_turns = int(_MOBILE_SESSION_CAPS.get(str(tid), _MOBILE_SESSION_MAX_TURNS))
        # Keep room for a pending assistant row by allowing +1 message.
        cap_msgs = int(max(3, max(1, cap_turns) * 2 + 1))
        if len(rows) > cap_msgs:
            del rows[: len(rows) - cap_msgs]
        _mobile_sessions_flush_locked()


def _mobile_session_append_assistant(
    tid: str,
    *,
    assistant_text: str,
    status: str = "",
    label: str = "",
) -> None:
    if not str(assistant_text or "").strip():
        return
    with _MOBILE_SESSION_LOCK:
        rows = _MOBILE_SESSIONS.get(tid)
        if rows is None:
            rows = []
            _MOBILE_SESSIONS[tid] = rows
        row = {"role": "assistant", "content": str(assistant_text or "")}
        status_value = _mobile_effective_history_status(status=status, label=label)
        if status_value and status_value not in {"ok", "complete"}:
            row["status"] = status_value
        label_value = str(label or "").strip()
        if label_value:
            row["label"] = label_value
        rows.append(row)
        cap_turns = int(_MOBILE_SESSION_CAPS.get(str(tid), _MOBILE_SESSION_MAX_TURNS))
        cap_msgs = int(max(3, max(1, cap_turns) * 2 + 1))
        if len(rows) > cap_msgs:
            del rows[: len(rows) - cap_msgs]
        _mobile_sessions_flush_locked()


def _mobile_active_stream_key(*, tid: str, role: str) -> str:
    return f"{str(tid or '').strip().lower()}::{str(role or '').strip().lower()}"


def _mobile_active_stream_state_status(*, tid: str, role: str) -> str:
    try:
        _binding, state_path = _mobile_agent_stream_state_paths(tid=tid, role=role)
        if not state_path.exists() or not state_path.is_file():
            return ""
        row = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(row, dict):
            return ""
        return str(row.get("status") or "").strip().lower()
    except Exception:
        return ""


def _mobile_active_worker_status(*, tid: str, role: str) -> str:
    try:
        mgr = _agents._codex_manager()  # type: ignore[attr-defined]
        row = mgr.get_worker(binding={"type": "task_id", "id": str(tid)}, role=str(role))
        if not isinstance(row, dict) or not bool(row.get("exists")):
            return ""
        return str(row.get("status") or "").strip().lower()
    except Exception:
        return ""


def _mobile_active_stream_claim_is_stale(existing: Dict[str, Any], *, tid: str, role: str) -> bool:
    if str(existing.get("terminal_status") or "").strip():
        return True
    created_monotonic = float(existing.get("created_monotonic") or 0.0)
    age_s = time.monotonic() - created_monotonic if created_monotonic > 0.0 else 1_000_000.0
    grace_s = float(globals().get("_MOBILE_ACTIVE_STREAM_STALE_GRACE_S", 30.0) or 30.0)
    if age_s < grace_s:
        return False
    stream_state_status = _mobile_active_stream_state_status(tid=tid, role=role)
    if stream_state_status in {"running", "streaming"}:
        return False
    worker_status = _mobile_active_worker_status(tid=tid, role=role)
    if worker_status == "running":
        return False
    return True


def _mobile_try_claim_active_stream(*, tid: str, role: str, request_id: str = "") -> Optional[Dict[str, Any]]:
    key = _mobile_active_stream_key(tid=tid, role=role)
    normalized_request_id = str(request_id or "").strip()
    with _MOBILE_ACTIVE_STREAMS_LOCK:
        existing = _MOBILE_ACTIVE_STREAMS.get(key)
        if isinstance(existing, dict):
            if normalized_request_id and str(existing.get("request_id") or "").strip() == normalized_request_id:
                existing["superseded"] = True
            elif _mobile_active_stream_claim_is_stale(existing, tid=tid, role=role):
                _MOBILE_ACTIVE_STREAMS.pop(key, None)
            else:
                return None
        control = {
            "control_id": uuid.uuid4().hex,
            "key": key,
            "tid": str(tid or "").strip().lower(),
            "role": str(role or "").strip().lower(),
            "request_id": normalized_request_id,
            "created_utc": _now_utc_iso(),
            "created_monotonic": time.monotonic(),
            "cancel_requested": False,
            "cancel_reason": "",
            "terminal_status": "",
            "terminal_message": "",
            "terminal_event": threading.Event(),
            "superseded": False,
            "reattached": isinstance(existing, dict),
        }
        _MOBILE_ACTIVE_STREAMS[key] = control
        return control


def _mobile_get_active_stream(*, tid: str, role: str) -> Optional[Dict[str, Any]]:
    key = _mobile_active_stream_key(tid=tid, role=role)
    with _MOBILE_ACTIVE_STREAMS_LOCK:
        existing = _MOBILE_ACTIVE_STREAMS.get(key)
        return dict(existing) if isinstance(existing, dict) else None


def _mobile_release_active_stream(*, tid: str, role: str, control_id: str = "") -> None:
    key = _mobile_active_stream_key(tid=tid, role=role)
    with _MOBILE_ACTIVE_STREAMS_LOCK:
        existing = _MOBILE_ACTIVE_STREAMS.get(key)
        if not isinstance(existing, dict):
            return
        if control_id and str(existing.get("control_id") or "").strip() != str(control_id or "").strip():
            return
        _MOBILE_ACTIVE_STREAMS.pop(key, None)


def _mobile_mark_active_stream_cancel_requested(*, tid: str, role: str, reason: str) -> Optional[Dict[str, Any]]:
    key = _mobile_active_stream_key(tid=tid, role=role)
    with _MOBILE_ACTIVE_STREAMS_LOCK:
        existing = _MOBILE_ACTIVE_STREAMS.get(key)
        if not isinstance(existing, dict):
            return None
        existing["cancel_requested"] = True
        existing["cancel_reason"] = str(reason or "").strip()
        return existing


def _mobile_complete_active_stream(
    *,
    tid: str,
    role: str,
    terminal_status: str,
    terminal_message: str = "",
    control_id: str = "",
) -> None:
    key = _mobile_active_stream_key(tid=tid, role=role)
    with _MOBILE_ACTIVE_STREAMS_LOCK:
        existing = _MOBILE_ACTIVE_STREAMS.get(key)
        if not isinstance(existing, dict):
            return
        if control_id and str(existing.get("control_id") or "").strip() != str(control_id or "").strip():
            return
        existing["terminal_status"] = str(terminal_status or "").strip().lower()
        existing["terminal_message"] = str(terminal_message or "")
        terminal_event = existing.get("terminal_event")
        if isinstance(terminal_event, threading.Event):
            terminal_event.set()


def _mobile_agent_stream_state_paths(*, tid: str, role: str) -> tuple[Dict[str, str], Path]:
    binding = {"type": "task_id", "id": str(tid)}
    resolved = _agents._resolve_paths(_agents._normalize_binding(binding))  # type: ignore[attr-defined]
    return binding, _agents._stream_state_file(resolved["chat_dir"], role)  # type: ignore[attr-defined]


def _mobile_write_agent_stream_state(
    *,
    tid: str,
    role: str,
    status: str,
    content: str = "",
    started_utc: str = "",
    error: str = "",
) -> None:
    try:
        binding, state_path = _mobile_agent_stream_state_paths(tid=tid, role=role)
        payload: Dict[str, Any] = {
            "schema_version": "agent_stream_state_v1",
            "status": str(status or "").strip().lower(),
            "updated_utc": _now_utc_iso(),
            "role_panel": str(role or "").strip().lower(),
            "binding": binding,
            "model": "codex-runtime",
            "content": str(content or ""),
        }
        if str(started_utc or "").strip():
            payload["started_utc"] = str(started_utc).strip()
        if str(error or "").strip():
            payload["error"] = str(error).strip()
        _agents._write_json_atomic(state_path, payload)  # type: ignore[attr-defined]
    except Exception:
        return


def _mobile_clear_agent_stream_state(*, tid: str, role: str) -> None:
    try:
        _binding, state_path = _mobile_agent_stream_state_paths(tid=tid, role=role)
        state_path.unlink(missing_ok=True)
    except Exception:
        return


def _mobile_cancel_active_stream(*, tid: str, role: str, reason: str) -> Dict[str, Any]:
    control = _mobile_mark_active_stream_cancel_requested(tid=tid, role=role, reason=reason)
    if control is None:
        return {
            "ok": True,
            "status": "not_found",
            "thread_id": str(tid),
            "role": str(role),
            "message": "No active server turn was found for this tab.",
        }
    stop_result: Dict[str, Any] = {}
    try:
        mgr = _agents._codex_manager()  # type: ignore[attr-defined]
        stop_result = mgr.stop_worker(binding={"type": "task_id", "id": str(tid)}, role=role, reason=reason)
    except Exception as exc:
        _mobile_complete_active_stream(tid=tid, role=role, terminal_status="cancel_failed", terminal_message=str(exc))
        return {
            "ok": False,
            "status": "cancel_failed",
            "thread_id": str(tid),
            "role": str(role),
            "message": f"Failed to stop the active server turn: {str(exc)}",
        }
    terminal_event = control.get("terminal_event")
    if isinstance(terminal_event, threading.Event):
        terminal_event.wait(timeout=_MOBILE_CANCEL_WAIT_S)
    terminal_status = str(control.get("terminal_status") or "").strip().lower()
    terminal_message = str(control.get("terminal_message") or "").strip()
    if terminal_status == "cancelled":
        return {
            "ok": True,
            "status": "cancelled",
            "thread_id": str(tid),
            "role": str(role),
            "message": terminal_message or "Server turn cancelled.",
        }
    if terminal_status == "completed":
        return {
            "ok": True,
            "status": "already_completed",
            "thread_id": str(tid),
            "role": str(role),
            "message": terminal_message or "The server turn finished before the cancel request landed.",
        }
    if terminal_status == "cancel_failed":
        return {
            "ok": False,
            "status": "cancel_failed",
            "thread_id": str(tid),
            "role": str(role),
            "message": terminal_message or "Server cancel failed.",
        }
    if bool(stop_result.get("stopped")):
        return {
            "ok": True,
            "status": "cancel_requested",
            "thread_id": str(tid),
            "role": str(role),
            "message": "Server cancel requested.",
        }
    return {
        "ok": True,
        "status": "not_found",
        "thread_id": str(tid),
        "role": str(role),
        "message": terminal_message or "No active server turn was found for this tab.",
    }


_MOBILE_HOST_VERIFICATION_PROGRESS_INTERVAL_S = 5.0
_MOBILE_HOST_VERIFICATION_PROGRESS_TEXT = "Running host verification..."
_MOBILE_HOST_VERIFICATION_DEFAULT_TIMEOUT_S = _runtime_policy.get_runtime_policy_int(
    "mobile.direct_host_verification_timeout_s",
    600,
    env_names=("EIEL_MOBILE_HOST_VERIFICATION_TIMEOUT_S",),
    minimum=1,
    maximum=3600,
)


def _mobile_dispatch_host_verification_with_progress(
    *,
    content: str,
    role: str,
    binding: Dict[str, Any],
    session_file: Path,
    emit_progress: Callable[[str], bool],
) -> Dict[str, Any]:
    """Run direct host verification while emitting progress events for mobile SSE clients."""
    marker = str(getattr(_agents, "HOST_VERIFICATION_MARKER", "HOST_VERIFICATION_REQUEST_JSON:") or "HOST_VERIFICATION_REQUEST_JSON:")
    if marker not in str(content or ""):
        return {}
    result_box: Dict[str, Any] = {}
    error_box: Dict[str, Any] = {}
    finished = threading.Event()

    def _run_dispatch() -> None:
        try:
            result_box["value"] = _agents._maybe_dispatch_direct_host_verification(  # type: ignore[attr-defined]
                content=content,
                role=role,
                binding=binding,
                session_file=session_file,
                timeout_s_override=_MOBILE_HOST_VERIFICATION_DEFAULT_TIMEOUT_S,
            )
        except Exception as exc:
            error_box["value"] = exc
        finally:
            finished.set()

    worker = threading.Thread(
        target=_run_dispatch,
        name="mobile_host_verification_dispatch",
        daemon=True,
    )
    worker.start()
    last_emit = time.time()
    emitted_once = emit_progress(_MOBILE_HOST_VERIFICATION_PROGRESS_TEXT)
    while not finished.wait(timeout=0.5):
        now = time.time()
        if emitted_once and (now - last_emit) < _MOBILE_HOST_VERIFICATION_PROGRESS_INTERVAL_S:
            continue
        if emit_progress(_MOBILE_HOST_VERIFICATION_PROGRESS_TEXT):
            emitted_once = True
            last_emit = now
    if "value" in error_box:
        raise error_box["value"]
    return dict(result_box.get("value") or {})


def _mobile_build_prompt(tid: str, user_prompt: str) -> str:
    return str(user_prompt or "")


def _mobile_session_stats(tid: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    rows = _mobile_effective_history(tid, tail=_mobile_history_window_rows(), repo_root=repo_root)
    with _MOBILE_SESSION_LOCK:
        cap_turns = int(_MOBILE_SESSION_CAPS.get(tid, _MOBILE_SESSION_MAX_TURNS))
        file_path = str(_MOBILE_SESSIONS_PATH) if _MOBILE_SESSIONS_PATH is not None else ""
    chars = 0
    lines = 0
    for r in rows:
        txt = str(r.get("content") or "")
        chars += len(txt)
        lines += txt.count("\n") + (1 if txt else 0)
    file_bytes = 0
    try:
        if file_path:
            fp = Path(file_path)
            if fp.exists() and fp.is_file():
                file_bytes = int(fp.stat().st_size)
    except Exception:
        file_bytes = 0
    msg_count = int(len(rows))
    turns = int(sum(1 for r in rows if str(r.get("role") or "").strip().lower() == "user"))
    at_cap = bool(turns >= max(1, cap_turns))
    return {
        "thread_id": str(tid),
        "max_turns": int(max(1, cap_turns)),
        "turns": turns,
        "messages": msg_count,
        "chars": int(chars),
        "lines": int(lines),
        "at_cap": at_cap,
        "will_cycle_next": at_cap,
        "storage_path": file_path,
        "storage_file_bytes": int(file_bytes),
    }


def _mobile_session_history_payload(tid: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    rows = _mobile_effective_history(tid, tail=_mobile_history_window_rows(), repo_root=repo_root)
    out_rows: List[Dict[str, Any]] = []
    for r in rows:
        role = str(r.get("role") or "").strip().lower()
        content = str(r.get("content") or "")
        if role not in {"user", "assistant"}:
            continue
        if not content:
            continue
        row = {"role": role, "content": content}
        label = str(r.get("label") or "").strip()
        status = _mobile_effective_history_status(status=str(r.get("status") or ""), label=label)
        if status and status not in {"ok", "complete"}:
            row["status"] = status
        if label:
            row["label"] = label
        if bool(r.get("plane_routed")):
            row["plane_routed"] = True
        out_rows.append(row)
    return {
        "thread_id": str(tid),
        "messages": int(len(out_rows)),
        "turns": int(sum(1 for r in out_rows if str(r.get("role") or "").strip().lower() == "user")),
        "history": out_rows,
    }
