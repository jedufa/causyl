def _write_host_verification_state(
    job_dir: Path,
    *,
    status: str,
    request: Optional[Dict[str, Any]] = None,
    message: str = "",
    runner_dispatch: str = "",
    phase: str = "",
    timeout_s: Optional[int] = None,
    stall_timeout_s: Optional[int] = None,
    heartbeat_interval_s: Optional[int] = None,
    started_utc: str = "",
    finished_utc: str = "",
    last_update_utc: str = "",
    last_output_utc: str = "",
    elapsed_s: Optional[int] = None,
    output_seen: Optional[bool] = None,
    subprocess_pid: Optional[int] = None,
    progress_note: str = "",
    timeout_source: str = "",
    stall_timeout_source: str = "",
) -> Dict[str, Any]:
    request_obj = request if isinstance(request, dict) else {}
    path = _host_verification_state_path(job_dir)
    request_path = (job_dir / "request.json").resolve()
    result_path = (job_dir / "result.json").resolve()
    stdout_path = (job_dir / "stdout.txt").resolve()
    stderr_path = (job_dir / "stderr.txt").resolve()
    previous = _read_json(path)
    now_utc = _utc_now_iso()
    effective_started_utc = str(started_utc or previous.get("started_utc") or "").strip()
    effective_finished_utc = str(finished_utc or "").strip()
    if not effective_started_utc and str(status or "").strip().lower() == "running":
        effective_started_utc = now_utc
    state = {
        "schema_version": "eiel_host_verification_status_v3",
        "request_id": str(request_obj.get("request_id") or previous.get("request_id") or "").strip(),
        "workflow_id": str(request_obj.get("workflow_id") or previous.get("workflow_id") or "").strip(),
        "action_id": str(((request_obj.get("action") or {}) if isinstance(request_obj.get("action"), dict) else {}).get("action_id") or previous.get("action_id") or "").strip(),
        "status": str(status or "").strip().lower(),
        "phase": str(phase or previous.get("phase") or "").strip(),
        "message": str(message or "").strip(),
        "updated_utc": now_utc,
        "last_update_utc": str(last_update_utc or now_utc).strip(),
        "last_heartbeat_utc": now_utc,
        "started_utc": effective_started_utc,
        "finished_utc": effective_finished_utc or str(previous.get("finished_utc") or "").strip(),
        "timeout_s": int(timeout_s) if timeout_s is not None else int(previous.get("timeout_s") or 0),
        "stall_timeout_s": int(stall_timeout_s) if stall_timeout_s is not None else int(previous.get("stall_timeout_s") or 0),
        "heartbeat_interval_s": int(heartbeat_interval_s) if heartbeat_interval_s is not None else int(previous.get("heartbeat_interval_s") or 0),
        "elapsed_s": int(elapsed_s) if elapsed_s is not None else int(previous.get("elapsed_s") or 0),
        "last_output_utc": str(last_output_utc or previous.get("last_output_utc") or "").strip(),
        "output_seen": bool(previous.get("output_seen")) if output_seen is None else bool(output_seen),
        "subprocess_pid": int(subprocess_pid) if subprocess_pid is not None else int(previous.get("subprocess_pid") or 0),
        "runner_dispatch": str(runner_dispatch or previous.get("runner_dispatch") or "").strip(),
        "progress_note": str(progress_note or previous.get("progress_note") or "").strip(),
        "timeout_source": str(timeout_source or previous.get("timeout_source") or "").strip(),
        "stall_timeout_source": str(stall_timeout_source or previous.get("stall_timeout_source") or "").strip(),
        "job_dir": str(job_dir),
        "request_path": str(request_path),
        "result_path": str(result_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    started_dt = _parse_iso_utc(str(state["started_utc"] or ""))
    now_dt = _parse_iso_utc(now_utc)
    finished_dt = _parse_iso_utc(str(state["finished_utc"] or ""))
    deadline_utc = ""
    remaining_s = 0
    if started_dt is not None and int(state["timeout_s"] or 0) > 0:
        deadline_dt = started_dt + _dt.timedelta(seconds=int(state["timeout_s"]))
        deadline_utc = deadline_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if finished_dt is not None:
            remaining_s = max(0, int((deadline_dt - finished_dt).total_seconds()))
        elif now_dt is not None and str(state["status"] or "").strip().lower() not in _HOST_VERIFICATION_TERMINAL_STATES:
            remaining_s = max(0, int((deadline_dt - now_dt).total_seconds()))
    state["deadline_utc"] = deadline_utc
    state["remaining_s"] = int(remaining_s)
    _verification_contract.write_json_atomic(path, state)
    _trace_host_verification(
        "state_written",
        request=request_obj,
        job_dir=job_dir,
        message=str(message or "").strip(),
        state=status,
        phase=state["phase"],
        timeout_s=state["timeout_s"],
        stall_timeout_s=state["stall_timeout_s"],
        runner_dispatch=runner_dispatch,
        echo=False,
    )
    return state


def _read_host_verification_state(job_dir: Path) -> Dict[str, Any]:
    return _read_json(_host_verification_state_path(job_dir))


def _final_host_verification_state(result: Dict[str, Any]) -> str:
    failure_kind = str(result.get("failure_kind") or "").strip()
    if str(result.get("status") or "").strip().lower() == "passed" or bool(result.get("ok")):
        return "finished"
    if failure_kind == "timeout":
        return "timed_out"
    return "failed"


def _register_host_verification_reply_queue(request_id: str, reply_q: "queue.Queue[Dict[str, Any]]") -> None:
    key = str(request_id or "").strip()
    if not key:
        return
    with _HOST_VERIFICATION_REPLY_GUARD:
        _HOST_VERIFICATION_PENDING_REPLY_QUEUES[key] = reply_q


def _pop_host_verification_reply_queue(request_id: str) -> Optional["queue.Queue[Dict[str, Any]]"]:
    key = str(request_id or "").strip()
    if not key:
        return None
    with _HOST_VERIFICATION_REPLY_GUARD:
        return _HOST_VERIFICATION_PENDING_REPLY_QUEUES.pop(key, None)


def _notify_host_verification_waiter(*, request: Dict[str, Any], result: Dict[str, Any], source: str) -> bool:
    request_id = str((request or {}).get("request_id") or (result or {}).get("request_id") or "").strip()
    reply_q = _pop_host_verification_reply_queue(request_id)
    if reply_q is None:
        return False
    try:
        reply_q.put_nowait(result)
    except queue.Full:
        _trace_host_verification(
            "waiter_queue_full",
            request=request,
            message="host verification waiter queue was full while delivering result",
            source=source,
            level="error",
        )
        return False
    _trace_host_verification(
        "waiter_notified",
        request=request,
        message="delivered host verification result to waiting caller",
        source=source,
        ok=bool(result.get("ok")),
        failure_kind=str(result.get("failure_kind") or ""),
        echo=False,
    )
    return True


def _load_existing_host_verification_result(job_dir: Path) -> Dict[str, Any]:
    result_path = (job_dir / "result.json").resolve()
    result = _read_json(result_path)
    return result if isinstance(result, dict) else {}


def _list_filesystem_queued_host_verification_requests() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    root = _host_verification_jobs_root()
    try:
        job_dirs = sorted([p for p in root.iterdir() if p.is_dir() and not p.is_symlink()], key=lambda p: p.name)
    except Exception as exc:
        _trace_host_verification(
            "filesystem_scan_failed",
            message=f"failed to list verification jobs root: {exc}",
            level="error",
            jobs_root=root,
        )
        _write_host_verification_worker_status(state="scan_failed", message="failed to list verification jobs root", last_error=str(exc))
        return rows
    _trace_host_verification(
        "filesystem_scan_started",
        message="scanning verification job folders for queued host verification requests",
        jobs_root=root,
        job_dir_count=len(job_dirs),
        echo=False,
    )
    for job_dir in job_dirs:
        request_path = (job_dir / "request.json").resolve()
        result_path = (job_dir / "result.json").resolve()
        if not request_path.exists():
            _trace_host_verification(
                "filesystem_scan_skip_missing_request",
                job_dir=job_dir,
                message="skipping job directory because request.json is missing",
                echo=False,
            )
            continue
        if result_path.exists():
            _trace_host_verification(
                "filesystem_scan_skip_result_exists",
                job_dir=job_dir,
                message="skipping job directory because result.json already exists",
                result_path=result_path,
                echo=False,
            )
            continue
        request = _read_json(request_path)
        if not request:
            _trace_host_verification(
                "filesystem_scan_skip_invalid_request_json",
                job_dir=job_dir,
                message="skipping job directory because request.json could not be parsed into an object",
                request_path=request_path,
            )
            continue
        state = _read_host_verification_state(job_dir)
        status = str(state.get("status") or "").strip().lower()
        if status in _HOST_VERIFICATION_TERMINAL_STATES or status == "running":
            _trace_host_verification(
                "filesystem_scan_skip_nonqueue_state",
                request=request,
                job_dir=job_dir,
                message="skipping job directory because status is not queueable",
                state=status,
                echo=False,
            )
            continue
        _trace_host_verification(
            "filesystem_scan_found_queued_job",
            request=request,
            job_dir=job_dir,
            message="found queued host verification job on filesystem",
            state=status or "(missing)",
        )
        rows.append(request)
    _write_host_verification_worker_status(
        state="scan_complete",
        message="completed filesystem verification job scan",
        scan_count=len(rows),
    )
    return rows


def _claim_filesystem_host_verification_request(request: Dict[str, Any], *, dispatch: str) -> Dict[str, Any]:
    normalized = _verification_contract.normalize_request(request, repo=_repo_root())
    job_dir = _host_verification_job_dir(normalized)
    result_path = (job_dir / "result.json").resolve()
    if result_path.exists():
        _trace_host_verification(
            "claim_skipped_result_exists",
            request=normalized,
            job_dir=job_dir,
            message="not claiming job because result.json already exists",
            result_path=result_path,
        )
        return {}
    state = _read_host_verification_state(job_dir)
    status = str(state.get("status") or "").strip().lower()
    if status in _HOST_VERIFICATION_TERMINAL_STATES or status == "running":
        _trace_host_verification(
            "claim_skipped_bad_state",
            request=normalized,
            job_dir=job_dir,
            message="not claiming job because state is already running or terminal",
            state=status or "(missing)",
        )
        return {}
    _write_host_verification_state(
        job_dir,
        status="running",
        request=normalized,
        message="host verification picked up by web server worker",
        runner_dispatch=dispatch,
        phase="claimed",
        progress_note="web server worker claimed queued job and is about to launch the runner",
        timeout_source="request_policy",
    )
    _trace_host_verification(
        "job_claimed",
        request=normalized,
        job_dir=job_dir,
        message="web server worker claimed queued host verification job",
        runner_dispatch=dispatch,
    )
    return normalized


def _process_next_filesystem_host_verification_job(*, dispatch: str) -> bool:
    for request in _list_filesystem_queued_host_verification_requests():
        claimed = _claim_filesystem_host_verification_request(request, dispatch=dispatch)
        if not claimed:
            continue
        _trace_host_verification(
            "filesystem_dispatch_started",
            request=claimed,
            message="dispatching queued filesystem host verification job",
            runner_dispatch=dispatch,
        )
        result = _run_host_verification_request_inline(claimed)
        result_job_dir = _host_verification_job_dir(claimed)
        runner_meta = result.get("runner") if isinstance(result.get("runner"), dict) else {}
        result["runner"] = {**runner_meta, "dispatch": str(dispatch or "")}
        _verification_contract.write_json_atomic((result_job_dir / "result.json").resolve(), result)
        _write_host_verification_state(
            result_job_dir,
            status=_final_host_verification_state(result),
            request=claimed,
            message=str(((result.get("evidence") or {}) if isinstance(result.get("evidence"), dict) else {}).get("summary") or "").strip(),
            runner_dispatch=dispatch,
            phase="completed",
            finished_utc=str(result.get("finished_utc") or ""),
            progress_note="filesystem-queued host verification finished and result was recorded",
            timeout_source="request_policy",
        )
        _trace_host_verification(
            "filesystem_dispatch_finished",
            request=claimed,
            job_dir=result_job_dir,
            message=str((((result.get("evidence") or {}) if isinstance(result.get("evidence"), dict) else {}).get("summary") or "")).strip() or "filesystem host verification finished",
            ok=bool(result.get("ok")),
            failure_kind=str(result.get("failure_kind") or ""),
            runner_dispatch=dispatch,
        )
        _notify_host_verification_waiter(request=claimed, result=result, source="filesystem_dispatch")
        return True
    _trace_host_verification(
        "filesystem_dispatch_noop",
        message="no queued filesystem host verification jobs were ready to process",
        runner_dispatch=dispatch,
        echo=False,
    )
    return False


def configure_host_verification_dispatch(*, mode: str = "inline") -> None:
    """Configure how host verification requests are executed in this process."""
    global _HOST_VERIFICATION_DISPATCH_MODE, _HOST_VERIFICATION_WORK_QUEUE, _HOST_VERIFICATION_WORKER, _HOST_VERIFICATION_WORKER_STOP
    normalized = str(mode or "inline").strip().lower() or "inline"
    if normalized not in {"inline", "server_worker"}:
        raise ValueError("mode must be inline or server_worker")
    worker_to_join: Optional[threading.Thread] = None
    with _HOST_VERIFICATION_DISPATCH_GUARD:
        _HOST_VERIFICATION_DISPATCH_MODE = normalized
        _trace_host_verification(
            "dispatch_mode_configured",
            message=f"host verification dispatch mode set to {normalized}",
            mode=normalized,
        )
        if normalized != "server_worker":
            if _HOST_VERIFICATION_WORKER_STOP is not None:
                _HOST_VERIFICATION_WORKER_STOP.set()
            worker_to_join = _HOST_VERIFICATION_WORKER
            _HOST_VERIFICATION_WORKER = None
            _HOST_VERIFICATION_WORK_QUEUE = None
            _HOST_VERIFICATION_WORKER_STOP = None
            _write_host_verification_worker_status(state="disabled", message="host verification worker disabled")
        else:
            if _HOST_VERIFICATION_WORK_QUEUE is None:
                _HOST_VERIFICATION_WORK_QUEUE = queue.Queue()
                _trace_host_verification(
                    "worker_queue_created",
                    message="created in-process host verification work queue",
                )
            if _HOST_VERIFICATION_WORKER is not None and _HOST_VERIFICATION_WORKER.is_alive():
                _trace_host_verification(
                    "worker_already_running",
                    message="host verification worker thread already running",
                    worker_name=_HOST_VERIFICATION_WORKER.name,
                )
                _write_host_verification_worker_status(state="running", message="host verification worker already running")
                return
            stop_event = threading.Event()
            _HOST_VERIFICATION_WORKER_STOP = stop_event
            worker = threading.Thread(
                target=_host_verification_worker_loop,
                args=(stop_event,),
                name="webui_host_verification_worker",
                daemon=True,
            )
            worker.start()
            _HOST_VERIFICATION_WORKER = worker
            _write_host_verification_worker_status(state="starting", message="host verification worker thread started")
            _trace_host_verification(
                "worker_started",
                message="started host verification worker thread",
                worker_name=worker.name,
                worker_ident=int(worker.ident or 0),
            )
    if worker_to_join is not None and worker_to_join.is_alive() and threading.current_thread() is not worker_to_join:
        worker_to_join.join(timeout=2.0)


def _host_verification_worker_loop(stop_event: threading.Event) -> None:
    """Process host verification jobs inside the real web-server process."""
    global _HOST_VERIFICATION_LAST_IDLE_TRACE_AT
    q = _HOST_VERIFICATION_WORK_QUEUE
    if q is None:
        _trace_host_verification(
            "worker_exited_without_queue",
            level="error",
            message="host verification worker loop started without an initialized work queue",
        )
        _write_host_verification_worker_status(state="error", message="worker loop started without initialized queue", last_error="queue missing")
        return
    _trace_host_verification(
        "worker_loop_started",
        message="host verification worker loop is running",
        jobs_root=_host_verification_jobs_root(),
    )
    _write_host_verification_worker_status(state="running", message="host verification worker loop running")
    while True:
        if stop_event.is_set():
            _trace_host_verification(
                "worker_stop_requested",
                message="host verification worker loop stopping because dispatch mode changed",
            )
            _write_host_verification_worker_status(state="disabled", message="host verification worker loop stopped")
            return
        try:
            request, reply_q = q.get(timeout=1.0)
        except queue.Empty:
            if stop_event.is_set():
                _trace_host_verification(
                    "worker_stop_requested",
                    message="host verification worker loop stopping after queue timeout because dispatch mode changed",
                )
                _write_host_verification_worker_status(state="disabled", message="host verification worker loop stopped")
                return
            processed = _process_next_filesystem_host_verification_job(dispatch="server_worker")
            now = time.time()
            if (not processed) and ((now - float(_HOST_VERIFICATION_LAST_IDLE_TRACE_AT or 0.0)) >= _HOST_VERIFICATION_IDLE_TRACE_INTERVAL_S):
                _HOST_VERIFICATION_LAST_IDLE_TRACE_AT = now
                _trace_host_verification(
                    "worker_idle",
                    message="worker loop idle; no in-memory or filesystem jobs ready",
                    jobs_root=_host_verification_jobs_root(),
                )
                _write_host_verification_worker_status(state="idle", message="worker loop idle; no jobs ready")
            continue
        try:
            if not isinstance(request, dict):
                _trace_host_verification(
                    "worker_received_wake_signal",
                    message="worker received wake signal and will claim queued filesystem jobs",
                    echo=False,
                )
                processed = _process_next_filesystem_host_verification_job(dispatch="server_worker")
                if not processed:
                    _write_host_verification_worker_status(
                        state="idle",
                        message="worker wake signal found no queued filesystem jobs",
                    )
                q.task_done()
                continue
            _trace_host_verification(
                "worker_received_in_memory_job",
                request=request,
                message="worker received queued in-memory host verification job",
            )
            normalized = _verification_contract.normalize_request(request, repo=_repo_root())
            job_dir = _host_verification_job_dir(normalized)
            existing_result = _load_existing_host_verification_result(job_dir)
            if existing_result:
                _trace_host_verification(
                    "worker_reused_existing_result",
                    request=normalized,
                    job_dir=job_dir,
                    message="worker found an existing structured result for the queued request and will reuse it",
                    ok=bool(existing_result.get("ok")),
                    failure_kind=str(existing_result.get("failure_kind") or ""),
                )
                result = existing_result
            else:
                claimed = _claim_filesystem_host_verification_request(normalized, dispatch="server_worker")
                if claimed:
                    normalized = claimed
                else:
                    _trace_host_verification(
                        "worker_using_preclaimed_job",
                        request=normalized,
                        message="worker is using normalized request because filesystem claim returned empty",
                    )
                _write_host_verification_worker_status(state="running", message="worker executing host verification job", request=normalized)
                result = _run_host_verification_request_inline(normalized)
                _write_host_verification_state(
                    _host_verification_job_dir(normalized),
                    status=_final_host_verification_state(result),
                    request=normalized,
                    message=str(((result.get("evidence") or {}) if isinstance(result.get("evidence"), dict) else {}).get("summary") or "").strip(),
                    runner_dispatch="server_worker",
                    phase="completed",
                    finished_utc=str(result.get("finished_utc") or ""),
                )
        except Exception as exc:
            normalized = _verification_contract.normalize_request(request, repo=_repo_root())
            _trace_host_verification(
                "worker_job_crashed",
                request=normalized,
                level="error",
                message=f"web-server host verification worker crashed while processing request: {exc}",
            )
            result = _verification_contract.build_result(
                request=normalized,
                ok=False,
                status="failed",
                exit_code=1,
                started_utc=_utc_now_iso(),
                finished_utc=_utc_now_iso(),
                duration_ms=0,
                cwd=str(_repo_root()),
                argv=[],
                evidence={
                    "stdout_path": "",
                    "stderr_path": "",
                    "artifact_paths": [],
                    "summary": f"web-server host verification worker crashed: {exc}",
                },
                failure_kind="runner_internal_error",
            )
            _verification_contract.write_json_atomic((_host_verification_job_dir(normalized) / "result.json").resolve(), result)
            _write_host_verification_state(
                _host_verification_job_dir(normalized),
                status="failed",
                request=normalized,
                message=f"web-server host verification worker crashed: {exc}",
                runner_dispatch="server_worker",
                phase="crashed",
                finished_utc=_utc_now_iso(),
            )
            _write_host_verification_worker_status(
                state="error",
                message="worker crashed while processing host verification job",
                request=normalized,
                last_error=str(exc),
            )
        runner_meta = result.get("runner") if isinstance(result.get("runner"), dict) else {}
        result["runner"] = {**runner_meta, "dispatch": "server_worker"}
        _verification_contract.write_json_atomic((_host_verification_job_dir(normalized) / "result.json").resolve(), result)
        _trace_host_verification(
            "worker_job_finished",
            request=normalized,
            message=str((((result.get("evidence") or {}) if isinstance(result.get("evidence"), dict) else {}).get("summary") or "")).strip() or "worker finished host verification job",
            ok=bool(result.get("ok")),
            failure_kind=str(result.get("failure_kind") or ""),
        )
        _write_host_verification_worker_status(
            state="running",
            message="worker finished host verification job",
            request=normalized,
        )
        if not _notify_host_verification_waiter(request=normalized, result=result, source="worker_in_memory"):
            try:
                reply_q.put_nowait(result)
            except queue.Full:
                _trace_host_verification(
                    "worker_reply_queue_full",
                    request=normalized,
                    message="worker could not deliver result because the in-memory reply queue was full",
                    level="error",
                )
        q.task_done()


def _run_host_verification_request_inline(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run the host verification runner in the current process context."""
    normalized = _verification_contract.normalize_request(request, repo=_repo_root())
    job_dir = Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")).resolve()
    request_path = (job_dir / "request.json").resolve()
    _verification_contract.write_json_atomic(request_path, normalized)
    cmd = [os.environ.get("PYTHON", sys.executable), "-m", "infra.host.verification_runner", "--request", str(request_path)]
    timeout_s = int((((normalized.get("policy") or {}) if isinstance(normalized.get("policy"), dict) else {}).get("timeout_s") or 900))
    _trace_host_verification(
        "inline_runner_launch",
        request=normalized,
        job_dir=job_dir,
        message="launching infra.host.verification_runner from current process context",
        argv=cmd,
        timeout_s=timeout_s,
        cwd=str(_repo_root()),
    )
    try:
        cp = subprocess.run(
            cmd,
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=max(1, min(3600, timeout_s + 60)),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        _trace_host_verification(
            "inline_runner_timeout",
            request=normalized,
            job_dir=job_dir,
            level="error",
            message="host verification runner subprocess timed out",
            timeout_s=timeout_s,
        )
        result = _verification_contract.build_result(
            request=normalized,
            ok=False,
            status="failed",
            exit_code=124,
            started_utc=_utc_now_iso(),
            finished_utc=_utc_now_iso(),
            duration_ms=max(1, timeout_s) * 1000,
            cwd=str(_repo_root()),
            argv=cmd,
            evidence={
                "stdout_path": "",
                "stderr_path": "",
                "artifact_paths": [],
                "summary": (str(exc.stderr or "") or "host verification runner timed out")[:2000],
            },
            failure_kind="timeout",
        )
        _verification_contract.write_json_atomic((job_dir / "result.json").resolve(), result)
        return result
    result_path = (job_dir / "result.json").resolve()
    if result_path.exists():
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
            _trace_host_verification(
                "inline_runner_result_loaded",
                request=normalized,
                job_dir=job_dir,
                message="loaded structured result.json written by host verification runner",
                subprocess_exit_code=int(cp.returncode or 0),
                ok=bool(result.get("ok")),
                failure_kind=str(result.get("failure_kind") or ""),
            )
            return result
        except Exception:
            _trace_host_verification(
                "inline_runner_result_parse_failed",
                request=normalized,
                job_dir=job_dir,
                level="error",
                message="result.json existed but could not be parsed as JSON",
                subprocess_exit_code=int(cp.returncode or 0),
            )
    summary = str(cp.stderr or "").strip() or str(cp.stdout or "").strip() or "host verification runner produced no structured result"
    _trace_host_verification(
        "inline_runner_missing_structured_result",
        request=normalized,
        job_dir=job_dir,
        level="error",
        message="runner subprocess exited without producing a readable structured result",
        subprocess_exit_code=int(cp.returncode or 0),
        stdout_preview=str(cp.stdout or "")[:400],
        stderr_preview=str(cp.stderr or "")[:400],
    )
    result = _verification_contract.build_result(
        request=normalized,
        ok=False,
        status="failed",
        exit_code=int(cp.returncode or 1),
        started_utc=_utc_now_iso(),
        finished_utc=_utc_now_iso(),
        duration_ms=0,
        cwd=str(_repo_root()),
        argv=cmd,
        evidence={"stdout_path": "", "stderr_path": "", "artifact_paths": [], "summary": summary[:2000]},
        failure_kind="runner_internal_error",
    )
    _verification_contract.write_json_atomic((job_dir / "result.json").resolve(), result)
    return result


def _run_host_verification_request(request: Dict[str, Any]) -> Dict[str, Any]:
    """Run host verification via the configured dispatcher."""
    with _HOST_VERIFICATION_DISPATCH_GUARD:
        mode = str(_HOST_VERIFICATION_DISPATCH_MODE or "inline")
        worker = _HOST_VERIFICATION_WORKER
        q = _HOST_VERIFICATION_WORK_QUEUE
    fallback_reasons: List[str] = []
    if mode != "server_worker":
        fallback_reasons.append(f"dispatch_mode={mode}")
    if q is None:
        fallback_reasons.append("work_queue_missing")
    if not (worker and worker.is_alive()):
        fallback_reasons.append("worker_not_alive")
    if threading.current_thread() is worker:
        fallback_reasons.append("already_on_worker_thread")
    if fallback_reasons:
        _trace_host_verification(
            "dispatch_falling_back_inline",
            request=request,
            message="running host verification inline because server worker path is unavailable in this process context",
            fallback_reasons=fallback_reasons,
        )
        result = _run_host_verification_request_inline(request)
        runner_meta = result.get("runner") if isinstance(result.get("runner"), dict) else {}
        result["runner"] = {**runner_meta, "dispatch": "inline"}
        normalized_inline = _verification_contract.normalize_request(request, repo=_repo_root())
        _verification_contract.write_json_atomic((_host_verification_job_dir(normalized_inline) / "result.json").resolve(), result)
        return result
    normalized = _verification_contract.normalize_request(request, repo=_repo_root())
    job_dir = _host_verification_job_dir(normalized)
    timeout_s = int((((normalized.get("policy") or {}) if isinstance(normalized.get("policy"), dict) else {}).get("timeout_s") or 900))
    _verification_contract.write_json_atomic((job_dir / "request.json").resolve(), normalized)
    _write_host_verification_state(
        job_dir,
        status="queued",
        request=normalized,
        message="queued for host verification by web server worker",
        runner_dispatch="server_worker",
        phase="queued",
        timeout_s=timeout_s,
        stall_timeout_s=int((((normalized.get("policy") or {}) if isinstance(normalized.get("policy"), dict) else {}).get("stall_timeout_s") or 0)),
        progress_note="waiting for web server worker to claim queued host verification job",
        timeout_source="request_policy",
        stall_timeout_source=(
            "request_policy"
            if (((request.get("policy") or {}) if isinstance(request.get("policy"), dict) else {}).get("stall_timeout_s") not in (None, ""))
            else "runner_default"
        ),
    )
    _trace_host_verification(
        "dispatch_enqueued_for_worker",
        request=normalized,
        job_dir=job_dir,
        message="queued host verification request for web server worker",
        worker_name=str(worker.name if worker is not None else ""),
    )
    reply_q: "queue.Queue[Dict[str, Any]]" = queue.Queue(maxsize=1)
    _register_host_verification_reply_queue(str(normalized.get("request_id") or ""), reply_q)
    # Wake the worker so it claims the durable filesystem job once.
    q.put((None, None))
    try:
        result = reply_q.get(timeout=max(1, min(3660, timeout_s + 60)))
        _trace_host_verification(
            "dispatch_received_worker_result",
            request=normalized,
            job_dir=job_dir,
            message="received host verification result back from web server worker",
            ok=bool(result.get("ok")),
            failure_kind=str(result.get("failure_kind") or ""),
        )
        return result
    except queue.Empty:
        _pop_host_verification_reply_queue(str(normalized.get("request_id") or ""))
        _trace_host_verification(
            "dispatch_worker_result_timeout",
            request=normalized,
            job_dir=job_dir,
            level="error",
            message="timed out waiting for host verification result from web server worker",
            timeout_s=timeout_s,
        )
        result = _verification_contract.build_result(
            request=normalized,
            ok=False,
            status="failed",
            exit_code=124,
            started_utc=_utc_now_iso(),
            finished_utc=_utc_now_iso(),
            duration_ms=max(1, timeout_s) * 1000,
            cwd=str(_repo_root()),
            argv=[],
            evidence={
                "stdout_path": "",
                "stderr_path": "",
                "artifact_paths": [],
                "summary": "web-server host verification worker timed out waiting for a result",
            },
            failure_kind="timeout",
        )
        runner_meta = result.get("runner") if isinstance(result.get("runner"), dict) else {}
        result["runner"] = {**runner_meta, "dispatch": "server_worker"}
        _verification_contract.write_json_atomic((Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")) / "result.json").resolve(), result)
        _write_host_verification_state(
            job_dir,
            status="timed_out",
            request=normalized,
            message="web-server host verification worker timed out waiting for a result",
            runner_dispatch="server_worker",
            phase="timed_out",
            finished_utc=_utc_now_iso(),
            progress_note="worker queue wait exceeded timeout budget",
            timeout_source="request_policy",
        )
        return result


def _append_host_verification_followup(
    *,
    session_file: Path,
    role: str,
    binding: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    row = {
        "schema_version": "agent_chat_message_v1",
        "utc": _utc_now_iso(),
        "role_panel": role,
        "sender": "assistant",
        "content": "Host verification result:\n" + _host_verification_result_summary(result),
        "status": "ok" if bool(result.get("ok")) else "error",
        "binding": dict(binding or {}),
        "meta": {
            "source": "host_verification_runner",
            "request_id": str(result.get("request_id") or ""),
            "action_id": str(result.get("action_id") or ""),
            "failure_kind": str(result.get("failure_kind") or ""),
            "summary": str(evidence.get("summary") or ""),
            "stdout_path": str(evidence.get("stdout_path") or ""),
            "stderr_path": str(evidence.get("stderr_path") or ""),
            "artifact_paths": list(evidence.get("artifact_paths") or []),
        },
    }
    _append_jsonl(session_file, row)
    _refresh_mobile_bubble_projection(binding=dict(binding or {}), role=str(role or ""), session_file=session_file)
    return row
