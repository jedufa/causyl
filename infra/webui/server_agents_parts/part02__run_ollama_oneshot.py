def _run_ollama_oneshot(
    *,
    model_input: str,
    instructions: str,
    model: str,
    timeout_s: int,
) -> Dict[str, Any]:
    curl_bin = str(shutil.which("curl") or "").strip()
    if not curl_bin:
        raise RuntimeError("curl not found on PATH for ollama backend")
    base_url = str(os.environ.get("EIEL_OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")
    payload = {
        "model": str(model or "").strip() or "qwen2.5:3b",
        "stream": False,
        "messages": [
            {"role": "system", "content": str(instructions or "").strip()},
            {"role": "user", "content": str(model_input or "").strip()},
        ],
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_bytes = len(payload_json.encode("utf-8"))
    payload_file = None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as pf:
        pf.write(payload_json)
        pf.flush()
        payload_file = pf.name
    cmd: List[str] = [
        curl_bin,
        "-sS",
        f"{base_url}/api/chat",
        "-H",
        "Content-Type: application/json",
        "--data-binary",
        f"@{payload_file}",
    ]
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(30, int(timeout_s or CODEX_TIMEOUT_DEFAULT_S)),
        )
    finally:
        try:
            if payload_file:
                Path(payload_file).unlink(missing_ok=True)
        except Exception:
            pass
    raw_out = str(cp.stdout or "").strip()
    raw_err = str(cp.stderr or "").strip()
    body: Dict[str, Any] = {}
    try:
        body = json.loads(raw_out) if raw_out else {}
    except Exception:
        body = {}
    if cp.returncode != 0:
        return {
            "ok": False,
            "content": raw_err or raw_out or "",
            "error": raw_err or raw_out or f"ollama_exit_{int(cp.returncode)}",
            "exit_code": int(cp.returncode),
            "selected_model": str(payload.get("model") or ""),
            "binary": curl_bin,
            "base_url": base_url,
            "request_payload_bytes": int(payload_bytes),
            "transport": "temp_file_http_body",
        }
    message = ""
    if isinstance(body, dict):
        msg = body.get("message") if isinstance(body.get("message"), dict) else {}
        message = str(msg.get("content") or "").strip()
    if message:
        return {
            "ok": True,
            "content": message,
            "error": "",
            "exit_code": int(cp.returncode),
            "selected_model": str(body.get("model") or payload.get("model") or ""),
            "binary": curl_bin,
            "base_url": base_url,
            "request_payload_bytes": int(payload_bytes),
            "transport": "temp_file_http_body",
        }
    err_text = str(body.get("error") or "").strip() if isinstance(body, dict) else ""
    merged = err_text or raw_out or raw_err or "ollama_empty_response"
    return {
        "ok": False,
        "content": merged,
        "error": merged,
        "exit_code": int(cp.returncode),
        "selected_model": str(payload.get("model") or ""),
        "binary": curl_bin,
        "base_url": base_url,
        "request_payload_bytes": int(payload_bytes),
        "transport": "temp_file_http_body",
    }


def _codex_manager() -> CodexRuntimeManager:
    global _CODEX_MANAGER
    with _CODEX_MANAGER_GUARD:
        max_workers = _default_codex_max_workers()
        max_workers_per_role = _default_codex_max_workers_per_role()
        default_cmd = codex_turn_command_from_env()
        if _CODEX_MANAGER is None:
            _CODEX_MANAGER = CodexRuntimeManager(
                repo_root=_repo_root(),
                max_workers=max_workers,
                max_workers_per_role=max_workers_per_role,
                default_cmd=default_cmd,
            )
        else:
            _CODEX_MANAGER.reconfigure(
                max_workers=max_workers,
                max_workers_per_role=max_workers_per_role,
                default_cmd=default_cmd,
            )
        return _CODEX_MANAGER


def refresh_live_runtime() -> Dict[str, Any]:
    """Apply live policy-backed runtime changes without a process restart."""
    getattr(_runtime_policy.load_runtime_policy, "cache_clear", lambda: None)()
    runtime: Dict[str, Any]
    try:
        mgr = _codex_manager()
        runtime = mgr.reconfigure(
            max_workers=_default_codex_max_workers(),
            max_workers_per_role=_default_codex_max_workers_per_role(),
            default_cmd=codex_turn_command_from_env(),
        )
    except Exception as exc:
        runtime = {
            "ok": False,
            "changed": False,
            "error": str(exc),
            "max_workers": int(_default_codex_max_workers()),
            "max_workers_per_role": int(_default_codex_max_workers_per_role()),
            "default_cmd": list(codex_turn_command_from_env()),
        }
    return {
        "ok": True,
        "backend": _agents_backend(),
        "default_agent_tech": _default_agent_tech(),
        "codex": runtime,
        "claude": _claude_cli_version_probe(),
        "gemini": _gemini_cli_version_probe(),
        "xai": _xai_runtime_probe(),
        "ollama": _ollama_runtime_probe(),
        "deepseek": _deepseek_runtime_probe(),
        "codex_timeout_s": int(_default_codex_timeout_s()),
        "host_verification_default_timeout_s": int(_default_host_verification_timeout_policy_s()),
    }


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_hex_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "").strip()).strip("-") or "x"


def _safe_json_from_text(text: str) -> Dict[str, Any]:
    s = str(text or "").strip()
    if not s:
        return {}
    try:
        obj, _ = json.JSONDecoder().raw_decode(s)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _parse_iso_utc(value: str) -> Any | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _file_lock(path: Path) -> threading.Lock:
    return _fsio.file_lock(path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    _fsio.append_jsonl(path, obj)


def _mobile_task_session_retention_max_lines() -> int:
    raw = str(os.environ.get("EIEL_MOBILE_TASK_SESSION_MAX_LINES", "12000") or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = 12000
    return max(200, min(value, 50000))


def _mobile_task_session_retention_max_bytes() -> int:
    raw = str(os.environ.get("EIEL_MOBILE_TASK_SESSION_MAX_BYTES", str(32 * 1024 * 1024)) or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = 32 * 1024 * 1024
    return max(1 * 1024 * 1024, min(value, 512 * 1024 * 1024))


def _mobile_task_duplicate_window_s() -> int:
    raw = str(os.environ.get("EIEL_MOBILE_TASK_DUPLICATE_WINDOW_S", "30") or "").strip()
    try:
        value = int(raw)
    except Exception:
        value = 30
    return max(0, min(value, 3600))


def _read_lines(path: Path) -> List[str]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []


def _append_lines(path: Path, lines: List[str]) -> None:
    if not lines:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        for ln in lines:
            s = str(ln or "").rstrip("\n")
            if not s:
                continue
            f.write(s)
            f.write("\n")
        f.flush()
        os.fsync(f.fileno())


def _truncate_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = _file_lock(path)
    with lock:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("")
            f.flush()
            os.fsync(f.fileno())


def _lock_many(paths: List[Path]) -> List[threading.Lock]:
    uniq = sorted({str(p.resolve()) for p in paths})
    locks: List[threading.Lock] = []
    for s in uniq:
        lk = _file_lock(Path(s))
        lk.acquire()
        locks.append(lk)
    return locks


def _read_jsonl(path: Path, *, max_lines: int = 5000) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    out: List[Dict[str, Any]] = []
    try:
        if int(max_lines) > 0:
            lines = _tail_text_lines(path, int(max_lines))
        else:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                lines = f.read().splitlines()
    except Exception:
        return []
    for raw in lines:
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _tail_text_lines(path: Path, max_lines: int) -> List[str]:
    """Read the last N text lines without materializing the whole file."""
    want = max(1, int(max_lines))
    block_size = 64 * 1024
    chunks: List[bytes] = []
    newline_count = 0
    with open(path, "rb") as f:
        f.seek(0, os.SEEK_END)
        pos = f.tell()
        while pos > 0 and newline_count <= want:
            read_size = min(block_size, pos)
            pos -= read_size
            f.seek(pos, os.SEEK_SET)
            chunk = f.read(read_size)
            if not chunk:
                break
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
    if not chunks:
        return []
    text = b"".join(reversed(chunks)).decode("utf-8", errors="replace")
    lines = text.splitlines()
    if len(lines) > want:
        return lines[-want:]
    return lines


def _read_last_jsonl_row(path: Path) -> Optional[Dict[str, Any]]:
    try:
        lines = _tail_text_lines(path, 1)
    except Exception:
        return None
    if not lines:
        return None
    try:
        obj = json.loads(lines[-1])
    except Exception:
        return None
    return obj if isinstance(obj, dict) else None


def _read_json(path: Path) -> Dict[str, Any]:
    return _fsio.read_json_dict(path)


def _host_verification_runtime_root() -> Path:
    root = (_repo_root() / "runtime" / "state" / "webui").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _host_verification_trace_log_path() -> Path:
    return (_host_verification_runtime_root() / "host_verification_trace.jsonl").resolve()


def _host_verification_worker_status_path() -> Path:
    return (_host_verification_runtime_root() / "host_verification_worker_status.json").resolve()


def _host_verification_job_trace_path(job_dir: Path) -> Path:
    return (job_dir / "trace.jsonl").resolve()


def _trace_host_verification(
    event: str,
    *,
    request: Optional[Dict[str, Any]] = None,
    job_dir: Optional[Path] = None,
    message: str = "",
    level: str = "info",
    echo: bool = True,
    **fields: Any,
) -> Dict[str, Any]:
    request_obj = request if isinstance(request, dict) else {}
    root_dir = ""
    try:
        root_dir = str((((request_obj.get("artifacts") or {}) if isinstance(request_obj.get("artifacts"), dict) else {}).get("root_dir") or "")).strip()
    except Exception:
        root_dir = ""
    resolved_job_dir: Optional[Path] = None
    if isinstance(job_dir, Path):
        resolved_job_dir = job_dir.resolve()
    elif root_dir:
        try:
            resolved_job_dir = Path(root_dir).resolve()
        except Exception:
            resolved_job_dir = None
    queue_size = -1
    try:
        if _HOST_VERIFICATION_WORK_QUEUE is not None:
            queue_size = int(_HOST_VERIFICATION_WORK_QUEUE.qsize())
    except Exception:
        queue_size = -1
    row: Dict[str, Any] = {
        "schema_version": "eiel_host_verification_trace_v1",
        "utc": _utc_now_iso(),
        "pid": int(os.getpid()),
        "thread": threading.current_thread().name,
        "event": str(event or "").strip(),
        "level": str(level or "info").strip().lower() or "info",
        "message": str(message or "").strip(),
        "dispatch_mode": str(_HOST_VERIFICATION_DISPATCH_MODE or "").strip(),
        "queue_size": queue_size,
        "request_id": str(request_obj.get("request_id") or "").strip(),
        "workflow_id": str(request_obj.get("workflow_id") or "").strip(),
        "action_id": str((((request_obj.get("action") or {}) if isinstance(request_obj.get("action"), dict) else {}).get("action_id") or "")).strip(),
        "requested_by_role": str(request_obj.get("requested_by_role") or "").strip(),
        "job_dir": str(resolved_job_dir or ""),
    }
    for key, value in dict(fields or {}).items():
        if value is None:
            continue
        if isinstance(value, Path):
            row[str(key)] = str(value.resolve())
        elif isinstance(value, (str, int, float, bool, list, dict)):
            row[str(key)] = value
        else:
            row[str(key)] = str(value)
    try:
        _append_jsonl(_host_verification_trace_log_path(), row)
    except Exception:
        pass
    if resolved_job_dir is not None:
        try:
            _append_jsonl(_host_verification_job_trace_path(resolved_job_dir), row)
        except Exception:
            pass
    if echo:
        try:
            print(
                "[host_verification]"
                f" event={row['event']}"
                f" level={row['level']}"
                f" request_id={row['request_id'] or '-'}"
                f" action_id={row['action_id'] or '-'}"
                f" queue_size={queue_size}"
                f" msg={row['message'] or '-'}",
                flush=True,
            )
        except Exception:
            pass
    return row


def _write_host_verification_worker_status(
    *,
    state: str,
    message: str = "",
    request: Optional[Dict[str, Any]] = None,
    last_error: str = "",
    scan_count: Optional[int] = None,
    queue_size: Optional[int] = None,
) -> Dict[str, Any]:
    request_obj = request if isinstance(request, dict) else {}
    try:
        qsize = int(_HOST_VERIFICATION_WORK_QUEUE.qsize()) if _HOST_VERIFICATION_WORK_QUEUE is not None else 0
    except Exception:
        qsize = -1
    if queue_size is not None:
        qsize = int(queue_size)
    worker_alive = False
    worker_name = ""
    try:
        worker_alive = bool(_HOST_VERIFICATION_WORKER and _HOST_VERIFICATION_WORKER.is_alive())
        worker_name = str(_HOST_VERIFICATION_WORKER.name if _HOST_VERIFICATION_WORKER is not None else "")
    except Exception:
        worker_alive = False
        worker_name = ""
    payload = {
        "schema_version": "eiel_host_verification_worker_status_v1",
        "utc": _utc_now_iso(),
        "pid": int(os.getpid()),
        "thread": threading.current_thread().name,
        "state": str(state or "").strip(),
        "message": str(message or "").strip(),
        "dispatch_mode": str(_HOST_VERIFICATION_DISPATCH_MODE or "").strip(),
        "worker_alive": bool(worker_alive),
        "worker_name": worker_name,
        "queue_size": qsize,
        "scan_count": int(scan_count) if scan_count is not None else None,
        "request_id": str(request_obj.get("request_id") or "").strip(),
        "action_id": str((((request_obj.get("action") or {}) if isinstance(request_obj.get("action"), dict) else {}).get("action_id") or "")).strip(),
        "last_error": str(last_error or "").strip(),
        "jobs_root": str(_host_verification_jobs_root()),
    }
    _write_json_atomic(_host_verification_worker_status_path(), payload)
    return payload


def _extract_host_verification_request(text: str, *, trace_invalid: bool = True) -> Dict[str, Any]:
    s = str(text or "")
    idx = s.find(HOST_VERIFICATION_MARKER)
    payload = _agents_parsing.extract_host_verification_request(text)
    if not payload:
        if idx >= 0 and trace_invalid:
            _trace_host_verification(
                "request_marker_present_but_invalid_json",
                message="agent reply contained HOST_VERIFICATION_REQUEST_JSON marker but no valid JSON object followed it",
                marker_index=idx,
                preview=str(s[idx : idx + 240]),
            )
        return {}
    action_id = str(payload.get("action_id") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not action_id:
        _trace_host_verification(
            "request_marker_missing_action_id",
            message="agent reply contained HOST_VERIFICATION_REQUEST_JSON marker but action_id was missing",
            payload=payload,
        )
        return {}
    timeout_raw = payload.get("timeout_s")
    timeout_s = None
    if timeout_raw is not None:
        try:
            timeout_s = max(1, min(3600, int(timeout_raw)))
        except Exception:
            _trace_host_verification(
                "request_marker_invalid_timeout_s",
                message="agent reply contained HOST_VERIFICATION_REQUEST_JSON marker but timeout_s was invalid",
                payload=payload,
            )
            timeout_s = None
    stall_timeout_raw = payload.get("stall_timeout_s")
    stall_timeout_s = None
    if stall_timeout_raw is not None:
        try:
            stall_timeout_s = max(1, min(3600, int(stall_timeout_raw)))
            if timeout_s is not None:
                stall_timeout_s = min(int(timeout_s), int(stall_timeout_s))
        except Exception:
            _trace_host_verification(
                "request_marker_invalid_stall_timeout_s",
                message="agent reply contained HOST_VERIFICATION_REQUEST_JSON marker but stall_timeout_s was invalid",
                payload=payload,
            )
            stall_timeout_s = None
    return {
        "action_id": action_id,
        "params": dict(params),
        "reason": str(payload.get("reason") or "").strip(),
        "timeout_s": timeout_s,
        "stall_timeout_s": stall_timeout_s,
    }


def _user_visible_assistant_content(text: str) -> str:
    raw = str(text or "")
    visible = _strip_chat_header_title_marker(raw)
    idx = raw.find(HOST_VERIFICATION_MARKER)
    if idx >= 0:
        visible = _strip_chat_header_title_marker(raw[:idx].rstrip())
        request = _extract_host_verification_request(raw, trace_invalid=False)
        if not visible.strip():
            action_id = str(request.get("action_id") or "").strip()
            visible = f"Requested host verification for {action_id}." if action_id else "Requested host verification."
    stripped = visible.strip()
    if not stripped:
        return stripped
    return _compact_host_verification_result_text(stripped)


def _extract_chat_header_title(text: str) -> str:
    normalized = _agents_parsing.extract_chat_header_title(text)
    if not normalized:
        return ""
    words = normalized.split(" ")
    if not 3 <= len(words) <= 4:
        return ""
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9'-]*", word) for word in words):
        return ""
    return normalized


def _strip_chat_header_title_marker(text: str) -> str:
    stripped = _agents_parsing.strip_chat_header_title_marker(text)
    return re.sub(r"\n{3,}", "\n\n", stripped).strip()


def _normalize_chat_header_title(text: str) -> str:
    title = _agents_parsing.normalize_chat_header_title(text)
    if not title:
        return ""
    words = title.split(" ")
    if not 3 <= len(words) <= 4:
        return ""
    if not all(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9'-]*", word) for word in words):
        return ""
    return title


def _compact_host_verification_result_text(text: str) -> str:
    raw = str(text or "").strip()
    if not raw.startswith("Host verification result:"):
        return raw
    lines = raw.splitlines()
    if len(lines) <= 1:
        return raw
    parsed: Dict[str, str] = {}
    for line in lines[1:]:
        match = re.match(r"^\s*-\s*([a-z_]+)\s*:\s*(.*)$", line)
        if not match:
            return raw
        parsed[str(match.group(1) or "").strip().lower()] = str(match.group(2) or "").strip()
    if not parsed:
        return raw
    ok = str(parsed.get("ok") or "").strip().lower() == "true"
    action_id = str(parsed.get("action_id") or "").strip() or "host verification"
    summary = str(parsed.get("summary") or "").strip()
    failure_kind = str(parsed.get("failure_kind") or "").strip()
    status_line = f"{'Passed' if ok else 'Failed'}: {action_id}"
    if not ok and failure_kind and failure_kind != "(none)":
        status_line += f" ({failure_kind})"
    out = ["Host verification result:", status_line]
    if summary and summary != "(no summary)":
        out.append(summary)
    return "\n".join(out)


def _host_verification_result_summary(result: Dict[str, Any]) -> str:
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    summary = str(evidence.get("summary") or "").strip() or "(no summary)"
    action_id = str(result.get("action_id") or "").strip() or "host verification"
    ok = bool(result.get("ok"))
    failure_kind = str(result.get("failure_kind") or "").strip()
    out = [
        f"{'Passed' if ok else 'Failed'}: {action_id}"
        + (f" ({failure_kind})" if (not ok and failure_kind) else ""),
    ]
    if summary and summary != "(no summary)":
        out.append(summary)
    return "\n".join(out)


def _build_direct_host_verification_request(
    *,
    role: str,
    binding: Dict[str, Any],
    request: Dict[str, Any],
    timeout_s_override: Any = None,
) -> Dict[str, Any]:
    binding_obj = dict(binding or {})
    binding_type = str(binding_obj.get("type") or "").strip() or "task_id"
    binding_id = str(binding_obj.get("id") or "").strip() or "unknown"
    workflow_id = f"agent-direct-{binding_type}-{binding_id}-{str(role or '').strip()}"
    action_id = str(request.get("action_id") or "").strip()
    timeout_s = _default_host_verification_timeout_s(
        action_id=action_id,
        requested_timeout_s=request.get("timeout_s"),
        default_timeout_s_override=timeout_s_override,
    )
    requested_stall_timeout_s = request.get("stall_timeout_s")
    stall_timeout_s = None
    if requested_stall_timeout_s is not None:
        try:
            stall_timeout_s = max(1, min(int(timeout_s), int(requested_stall_timeout_s)))
        except Exception:
            stall_timeout_s = None
    req = {
        "workflow_id": workflow_id,
        "turn": "direct_agent_reply",
        "requested_by_role": str(role or "").strip() or "generalist",
        "binding": {"type": binding_type, "id": binding_id},
        "action": {
            "action_id": action_id,
            "params": dict(request.get("params") or {}),
        },
        "artifacts": {"capture_stdout": True, "capture_stderr": True},
        "policy": {
            "timeout_s": timeout_s,
            "stall_timeout_s": stall_timeout_s,
            "allow_network": False,
            "require_clean_exit": True,
        },
        "reason": str(request.get("reason") or "").strip() or "direct agent host verification",
        "created_utc": _utc_now_iso(),
    }
    return _verification_contract.normalize_request(req, repo=_repo_root())


def _host_verification_jobs_root() -> Path:
    return _verification_contract.verification_jobs_root(repo=_repo_root())


def _host_verification_job_dir(request: Dict[str, Any]) -> Path:
    root_dir = str(((request.get("artifacts") or {}) if isinstance(request.get("artifacts"), dict) else {}).get("root_dir") or "").strip()
    if root_dir:
        return Path(root_dir).resolve()
    request_id = str(request.get("request_id") or "").strip()
    if request_id:
        return _verification_contract.ensure_job_dir(request_id, repo=_repo_root())
    raise ValueError("host verification request missing artifacts.root_dir and request_id")


def _host_verification_state_path(job_dir: Path) -> Path:
    return (job_dir / "status.json").resolve()


def _default_host_verification_timeout_s(
    *,
    action_id: str,
    requested_timeout_s: Any = None,
    default_timeout_s_override: Any = None,
) -> int:
    try:
        if requested_timeout_s is not None:
            return max(1, min(3600, int(requested_timeout_s)))
    except Exception:
        pass
    try:
        if default_timeout_s_override is not None:
            return max(1, min(3600, int(default_timeout_s_override)))
    except Exception:
        pass
    try:
        spec = _verification_actions.get_action_spec(str(action_id or "").strip())
        return max(1, min(3600, int(spec.timeout_s_default)))
    except Exception:
        return int(_default_host_verification_timeout_policy_s())


def _default_host_verification_timeout_policy_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "host_verification.default_timeout_s",
        _HOST_VERIFICATION_DEFAULT_TIMEOUT_S,
        minimum=1,
        maximum=3600,
    )
