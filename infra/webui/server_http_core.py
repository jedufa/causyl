"""WebUI server module (extracted from server.py to keep things maintainable).

Last modified: 2026-03-31 Europe/Vienna
"""

from __future__ import annotations

import html as _html
import shlex

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_discovery import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
from infra.webui.server_run_summaries import *  # noqa: F403,F401
from infra.controlplane import server_maintenance as _server_maintenance  # noqa: E402
from infra.webui import server_lab_assistant_operator_snapshot as _lab_operator_snapshot  # noqa: E402
from infra.webui import server_assistant_bridge as _assistant_bridge  # noqa: E402

# -----------------------------------------------------------------------------
# Re-exports
# -----------------------------------------------------------------------------
#
# This module is imported via "from ... import *" by server_routes_and_main.py.
# Python star-import excludes underscore-prefixed names unless they are listed
# in __all__. The original monolithic server.py relied on many underscore
# helpers being in the same module namespace.
#
# To keep the refactor low-risk and preserve backward compatibility, we export
# the underscore helper surface explicitly.

__all__ = [
    "_ServerState",
    "_json_response",
    "_bytes_response",
    "_redirect_response",
    "_text_response",
    "_read_request_json",
    "_read_request_bytes",
    "_file_response",
    "_admin_extract_token",
    "_admin_guard",
    "_admin_repo_root",
    "_admin_find_git_script",
    "_admin_archives_dir",
    "_admin_list_archives",
    "_admin_run_snapshot_script",
    "_admin_fs_resolve",
    "_admin_cmd_env",
    "_admin_cmd_logs_dir",
    "_admin_cmd_write_log",
    "_admin_cmd_append",
    "_admin_cmd_kill_proc",
    "_admin_cmd_finish_job",
    "_admin_cmd_job_thread",
    "_admin_cmd_start_job",
    "_admin_cmd_kill_job",
    "_lab_front_door_history_payload",
    "_mobile_plane_run_command",
    "_admin_restart_preflight",
    "_admin_restart_webui",
    "_serve_static_file",
    "_serve_docs_file",
]
# -----------------------------------------------------------------------------
# HTTP server
# -----------------------------------------------------------------------------


class _ServerState:
    """Mutable server state shared across handler instances."""

    def __init__(self, cfg: WebUIConfig):
        self.cfg = cfg
        self.lock = threading.Lock()
        # Caches keyed by absolute file path strings.
        self.epiav_caches: Dict[str, EpiAvSeriesCache] = {}
        self.metrics_caches: Dict[str, MetricsCsvCache] = {}
        self.jsonl_caches: Dict[str, JsonlCache] = {}
        self.timeline_caches: Dict[str, Dict[str, Any]] = {}
        # Run index populated from /api/runs scans.
        # Keyed by normalized run_key, values are (run_root, sim_root) absolute paths.
        # This avoids subtle path normalization mismatches for nested run keys.
        self.run_index: Dict[str, Tuple[Path, Path]] = {}
        # Active simulation processes keyed by run_key
        self.active_procs: Dict[str, subprocess.Popen] = {}

        # Admin command runner jobs keyed by job_id.
        self.admin_cmd_jobs: Dict[str, AdminCmdJob] = {}
        # Active subprocesses for admin command jobs keyed by job_id.
        self.admin_cmd_procs: Dict[str, subprocess.Popen] = {}

    def set_run_index_item(self, run_key: str, run_root: Path, sim_root: Path) -> None:
        """Store resolved run paths for a run_key."""
        rk = normalize_run_key(run_key)
        with self.lock:
            self.run_index[rk] = (Path(run_root).resolve(), Path(sim_root).resolve())

    def get_run_index_item(self, run_key: str) -> Optional[Tuple[Path, Path]]:
        """Fetch resolved run paths for a run_key if known."""
        rk = normalize_run_key(run_key)
        with self.lock:
            return self.run_index.get(rk)

    def clear_run_index(self) -> None:
        """Clear the run index (safe; it will be rebuilt on next scan)."""
        with self.lock:
            self.run_index.clear()
            # Note: active_procs intentionally not cleared here.


def _json_response(handler: BaseHTTPRequestHandler, status: int, payload: Any) -> None:
    """Write a JSON response.

    Browsers can disconnect mid-request (tab close/refresh). That produces
    BrokenPipeError/ConnectionResetError. We swallow those to avoid noisy
    stack traces in normal use.
    """
    data = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    handler.send_response(int(status))
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        return


def _bytes_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    data: bytes,
    *,
    content_type: str,
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    """Write a bytes response and swallow client disconnects."""
    handler.send_response(int(status))
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(data)))
    handler.send_header("Cache-Control", "no-store")
    if extra_headers:
        for k, v in extra_headers.items():
            try:
                handler.send_header(str(k), str(v))
            except Exception:
                continue
    handler.end_headers()
    try:
        handler.wfile.write(data)
    except (BrokenPipeError, ConnectionResetError):
        return


def _redirect_response(handler: BaseHTTPRequestHandler, status: int, location: str) -> None:
    """Send an HTTP redirect with no body.

    Why this exists:
        MkDocs (and most static sites) use relative asset URLs.
        If a user visits a directory path without a trailing slash
        (e.g. /docs instead of /docs/), browsers resolve relative URLs
        against the parent path, breaking CSS/JS.
    """

    handler.send_response(int(status))
    handler.send_header("Location", str(location))
    handler.send_header("Content-Length", "0")
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()


def _text_response(handler: BaseHTTPRequestHandler, status: int, text: str, *, content_type: str = "text/plain; charset=utf-8") -> None:
    data = text.encode("utf-8", errors="replace")
    _bytes_response(handler, status, data, content_type=content_type)


def _read_request_json(handler: BaseHTTPRequestHandler, *, max_bytes: int = 256 * 1024) -> Any:
    """Read and parse JSON request body with a hard size limit."""
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return None
    if length > int(max_bytes):
        raise ValueError("request too large")
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise ValueError(f"invalid JSON: {e}")


def _read_request_bytes(handler: BaseHTTPRequestHandler, *, max_bytes: int = 50 * 1024 * 1024) -> bytes:
    """Read raw request bytes with a hard size limit.

    This is used for admin endpoints that accept uploads (patch zips, file replace).

    Args:
        handler: Active request handler.
        max_bytes: Maximum allowed Content-Length.

    Returns:
        Raw bytes from the request body (may be empty).

    Raises:
        ValueError: if Content-Length is invalid or exceeds max_bytes.
    """

    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except Exception:
        length = 0
    if length <= 0:
        return b""
    if length > int(max_bytes):
        raise ValueError(f"request too large ({length} bytes > {int(max_bytes)} bytes)")
    return handler.rfile.read(length)


def _file_response(
    handler: BaseHTTPRequestHandler,
    status: int,
    file_path: Path,
    *,
    content_type: str = "application/octet-stream",
    download_name: Optional[str] = None,
    extra_headers: Optional[Dict[str, str]] = None,
) -> None:
    """Stream a file response (avoids loading large zips into RAM)."""

    p = Path(file_path)
    if not p.exists() or not p.is_file():
        _json_response(handler, HTTPStatus.NOT_FOUND, {"error": "file not found"})
        return

    try:
        size = int(p.stat().st_size)
    except Exception:
        size = 0

    handler.send_response(int(status))
    handler.send_header("Content-Type", str(content_type))
    handler.send_header("Content-Length", str(size))
    handler.send_header("Cache-Control", "no-store")
    if download_name:
        handler.send_header("Content-Disposition", f"attachment; filename=\"{download_name}\"")
    if extra_headers:
        for k, v in extra_headers.items():
            try:
                handler.send_header(str(k), str(v))
            except Exception:
                continue
    handler.end_headers()

    try:
        with open(p, "rb") as f:
            shutil.copyfileobj(f, handler.wfile)
    except (BrokenPipeError, ConnectionResetError):
        return
    except Exception:
        # Best-effort: avoid crashing the server for a failed stream.
        return


def _admin_extract_token(handler: BaseHTTPRequestHandler, qs: Optional[Dict[str, List[str]]] = None) -> str:
    """Best-effort extract an admin token from headers or query string."""

    tok = str(handler.headers.get("X-Admin-Token") or "").strip()
    if tok:
        return tok
    if qs:
        try:
            raw = (qs.get("token") or [""])[0]
            return str(raw or "").strip()
        except Exception:
            return ""
    return ""


def _admin_guard(handler: BaseHTTPRequestHandler, cfg: WebUIConfig, qs: Optional[Dict[str, List[str]]] = None) -> bool:
    """Enforce admin enablement and optional token.

    Returns:
        True if request is allowed, else False (and writes an error response).
    """

    if not bool(getattr(cfg, "admin_enabled", True)):
        _json_response(handler, HTTPStatus.FORBIDDEN, {"error": "admin endpoints are disabled"})
        return False

    required = str(getattr(cfg, "admin_token", "") or "").strip()
    if not required:
        _json_response(handler, HTTPStatus.FORBIDDEN, {"error": "admin token is not configured"})
        return False
    from infra.webui.server_request_security import operator_authorized
    if not operator_authorized(handler, cfg, lambda token: _assistant_bridge.validate_control_pane_access_token(_admin_repo_root(), token)):
        _json_response(handler, HTTPStatus.UNAUTHORIZED, {"error": "missing/invalid admin token"})
        return False

    return True


def _admin_repo_root() -> Path:
    """Return repo root for admin operations (archives, patch apply, file browser).

    This must never return None. Admin endpoints rely on it for:
      - ./archives (repo snapshots)
      - patch apply (repo-root cwd)
      - file browser (repo-relative paths)

    We intentionally avoid calling any builder helpers here.
    """

    try:
        rr = _repo_root_from_webui()
        return Path(rr).resolve()
    except Exception:
        # Absolute fallback: server.py is expected at <repo>/infra/webui/server.py
        return Path(__file__).resolve().parents[2]


def _admin_find_git_script(repo_root: Path, script_name: str) -> Path:
    """Return the path to a git helper script (best-effort).

    We support multiple historical locations because humans refactor directories
    and then act surprised when automation breaks.

    Search order:
      1) <repo>/tools/git/<script_name>
      2) <repo>/tools/git/support/<script_name>
      3) <repo>/tools/<script_name>
      4) <repo>/<script_name>

    Args:
        repo_root: Repository root path.
        script_name: Filename like "git-commit.sh" or "git-tag.sh".

    Returns:
        Resolved Path (may not exist).
    """
    rr = Path(repo_root).resolve()
    cands = [
        (rr / "tools" / "git" / script_name).resolve(),
        (rr / "tools" / "git" / "support" / script_name).resolve(),
        (rr / "tools" / script_name).resolve(),
        (rr / script_name).resolve(),
    ]
    for p in cands:
        safe = _safe_resolve_under(rr, p)
        if safe and safe.exists() and safe.is_file():
            return safe
    # Fall back to first candidate (useful for error messages).
    return cands[0]


def _admin_archives_dir(repo_root: Path) -> Path:
    """Return the archives/ directory path (created if missing)."""

    p = (Path(repo_root) / "archives").resolve()
    try:
        p.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return p


def _admin_list_archives(repo_root: Path) -> List[Dict[str, Any]]:
    """List snapshot archives under ./archives/ (zip only)."""

    adir = _admin_archives_dir(repo_root)
    out: List[Dict[str, Any]] = []
    try:
        for p in adir.glob("*.zip"):
            if not p.is_file() or p.is_symlink():
                continue
            try:
                st = p.stat()
                out.append(
                    {
                        "name": p.name,
                        "bytes": int(st.st_size),
                        "mtime": float(st.st_mtime),
                    }
                )
            except Exception:
                out.append({"name": p.name, "bytes": 0, "mtime": 0.0})
    except Exception:
        return []

    out.sort(key=lambda d: float(d.get("mtime") or 0.0), reverse=True)
    return out


def _admin_run_snapshot_script(repo_root: Path, *, debug: bool) -> Tuple[Optional[Path], str, str, int]:
    """Run infra/bin/archive and return the created zip path.

    Returns:
        (zip_path_or_None, stdout, stderr, returncode)
    """

    rr = Path(repo_root).resolve()
    script = (rr / "tools" / "bin" / "archive").resolve()
    if not script.exists():
        script = (rr / "tools" / "bin" / "archive_complete_repo_snapshot.sh").resolve()
    safe_script = _safe_resolve_under(rr, script)
    if not safe_script or not safe_script.exists():
        return None, "", f"snapshot script not found: {script}", 2

    cmd = ["bash", str(safe_script)]
    if debug:
        cmd.append("--debug")

    env = dict(os.environ)
    env["EIEL_COLOR"] = "0"

    try:
        p = subprocess.run(
            cmd,
            cwd=str(rr),
            env=env,
            capture_output=True,
            text=True,
        )
    except Exception as e:
        return None, "", f"failed to run snapshot script: {e}", 3

    stdout = str(p.stdout or "")
    stderr = str(p.stderr or "")
    rc = int(p.returncode)

    if rc != 0:
        return None, stdout, stderr, rc

    zip_path: Optional[Path] = None
    for line in stdout.splitlines():
        s = line.strip()
        if s.startswith("SNAPSHOT_ZIP_PATH="):
            val = s.split("=", 1)[1].strip()
            if val:
                zp = Path(val)
                if not zp.is_absolute():
                    zp = (rr / zp).resolve()
                zip_path = zp
                break

    if not zip_path:
        return None, stdout, stderr, 4

    safe_zip = _safe_resolve_under(rr, zip_path)
    if not safe_zip or not safe_zip.exists() or not safe_zip.is_file():
        return None, stdout, stderr, 5

    return safe_zip, stdout, stderr, 0


def _admin_fs_resolve(repo_root: Path, rel_path: str) -> Optional[Path]:
    """Resolve a repo-relative path for admin FS endpoints (no symlink escape)."""

    rr = Path(repo_root).resolve()
    rel = str(rel_path or "").replace("\\", "/").lstrip("/")
    if rel in {"", "."}:
        return rr
    cand = (rr / rel).resolve()
    return _safe_resolve_under(rr, cand)



# -----------------------------------------------------------------------------
# Admin command runner (Option A "terminal") + Git helpers
# -----------------------------------------------------------------------------

_ADMIN_CMD_STDOUT_CAP = 200_000
_ADMIN_CMD_STDERR_CAP = 200_000
_ADMIN_CMD_POST_EXIT_DRAIN_GRACE_S = 1.0


@dataclass
class AdminCmdJob:
    """A non-interactive admin command job.

    This is deliberately *not* a PTY-backed terminal. It's a "run a command, show
    stdout/stderr" job runner designed for mobile use.

    Notes:
      - Commands run under: bash -lc <cmd>
      - Each job captures stdout/stderr up to a fixed cap (tail-preserving).
      - Jobs are killable via process-group signals (start_new_session=True).
      - Jobs are logged under: <repo_root>/governance/webui/runtime/admin_cmd_logs/
    """

    job_id: str
    kind: str
    cmd: str
    cwd_rel: str
    cwd_abs: str
    utc_started: str
    t_started: float
    timeout_sec: float

    pid: int = 0
    running: bool = True
    returncode: Optional[int] = None
    utc_finished: str = ""
    t_finished: float = 0.0
    killed: bool = False
    error: str = ""

    stdout: str = ""
    stderr: str = ""
    stdout_truncated: bool = False
    stderr_truncated: bool = False
    last_output_utc: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON-serializable view of this job."""

        dur = 0.0
        try:
            t_end = self.t_finished if self.t_finished else time.time()
            dur = float(max(0.0, t_end - float(self.t_started)))
        except Exception:
            dur = 0.0

        return {
            "schema_version": "admin_cmd_job_v1",
            "job_id": self.job_id,
            "kind": self.kind,
            "cmd": self.cmd,
            "cwd_rel": self.cwd_rel,
            "cwd_abs": self.cwd_abs,
            "utc_started": self.utc_started,
            "utc_finished": self.utc_finished,
            "pid": int(self.pid or 0),
            "running": bool(self.running),
            "killed": bool(self.killed),
            "returncode": None if self.returncode is None else int(self.returncode),
            "timeout_sec": float(self.timeout_sec or 0.0),
            "duration_sec": float(dur),
            "error": str(self.error or ""),
            "stdout": str(self.stdout or ""),
            "stderr": str(self.stderr or ""),
            "output_seen": bool((self.stdout or "") or (self.stderr or "")),
            "last_output_utc": str(self.last_output_utc or ""),
            "stdout_truncated": bool(self.stdout_truncated),
            "stderr_truncated": bool(self.stderr_truncated),
        }


def _admin_cmd_env() -> Dict[str, str]:
    """Return a safe-ish environment for admin command execution."""

    env = dict(os.environ)
    # Avoid raw ANSI escapes in a <pre>.
    env["EIEL_COLOR"] = "0"
    env.setdefault("NO_COLOR", "1")
    env.setdefault("TERM", "dumb")
    env.setdefault("PAGER", "cat")
    env.setdefault("GIT_PAGER", "cat")
    env.setdefault("PYTHONUNBUFFERED", "1")
    return env


def _admin_cmd_logs_dir(cfg: WebUIConfig) -> Path:
    """Return the directory for admin command logs (created if missing)."""

    base = (_webui_runtime_root() / "admin_cmd_logs").resolve()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return base


def _admin_cmd_write_log(cfg: WebUIConfig, job: AdminCmdJob) -> None:
    """Persist a completed job (best-effort; failures are ignored)."""

    try:
        logs = _admin_cmd_logs_dir(cfg)
        # Timestamp prefix keeps files in chronological order.
        ts = str(job.utc_started or _now_utc_iso()).replace(":", "").replace("-", "").replace("Z", "Z")
        kind = re.sub(r"[^A-Za-z0-9._-]+", "_", str(job.kind or "cmd"))
        name = f"{ts}__{kind}__{job.job_id}.json"
        out = logs / name
        out.write_text(json.dumps(job.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        return


def _admin_cmd_append(existing: str, add: str, cap: int) -> Tuple[str, bool]:
    """Append text while preserving only the last `cap` characters."""

    try:
        s = (existing or "") + (add or "")
        if cap > 0 and len(s) > cap:
            return s[-cap:], True
        return s, False
    except Exception:
        return (existing or ""), True


def _admin_cmd_kill_proc(proc: subprocess.Popen, *, hard: bool = False) -> None:
    """Kill a process group (best-effort).

    Args:
        proc: Popen instance started with start_new_session=True.
        hard: If True, send SIGKILL instead of SIGTERM.
    """

    try:
        pgid = int(proc.pid)
        sig = signal.SIGKILL if hard else signal.SIGTERM
        os.killpg(pgid, sig)
    except Exception:
        return


def _admin_cmd_finish_job(state: _ServerState, cfg: WebUIConfig, job_id: str, *, rc: Optional[int] = None, err: str = "") -> None:
    """Mark a job as finished and log it."""

    with state.lock:
        job = state.admin_cmd_jobs.get(job_id)
        if not job:
            return
        job.running = False
        if rc is not None:
            job.returncode = int(rc)
        job.t_finished = float(time.time())
        job.utc_finished = _now_utc_iso()
        if err:
            job.error = str(err)
        # Cleanup proc reference.
        state.admin_cmd_procs.pop(job_id, None)

    # Best-effort log outside the lock.
    try:
        _admin_cmd_write_log(cfg, job)
    except Exception:
        pass


def _admin_cmd_job_thread(state: _ServerState, cfg: WebUIConfig, job_id: str) -> None:
    """Background reader thread for an admin command job."""

    with state.lock:
        job = state.admin_cmd_jobs.get(job_id)
        proc = state.admin_cmd_procs.get(job_id)

    if not job or not proc:
        _admin_cmd_finish_job(state, cfg, job_id, rc=127, err="missing job/proc")
        return

    sel = selectors.DefaultSelector()
    try:
        if proc.stdout is not None:
            sel.register(proc.stdout, selectors.EVENT_READ, data="stdout")
        if proc.stderr is not None:
            sel.register(proc.stderr, selectors.EVENT_READ, data="stderr")
    except Exception:
        # If selector setup fails, fall back to communicate().
        try:
            out_b, err_b = proc.communicate(timeout=float(job.timeout_sec or 0.0) or None)
            out_s = (out_b or b"").decode("utf-8", errors="replace")
            err_s = (err_b or b"").decode("utf-8", errors="replace")
            with state.lock:
                j2 = state.admin_cmd_jobs.get(job_id)
                if j2:
                    j2.stdout, j2.stdout_truncated = _admin_cmd_append(j2.stdout, out_s, _ADMIN_CMD_STDOUT_CAP)
                    j2.stderr, j2.stderr_truncated = _admin_cmd_append(j2.stderr, err_s, _ADMIN_CMD_STDERR_CAP)
                    if out_s or err_s:
                        j2.last_output_utc = _now_utc_iso()
            _admin_cmd_finish_job(state, cfg, job_id, rc=proc.returncode)
            return
        except subprocess.TimeoutExpired:
            try:
                _admin_cmd_kill_proc(proc, hard=False)
                time.sleep(0.3)
                _admin_cmd_kill_proc(proc, hard=True)
            except Exception:
                pass
            _admin_cmd_finish_job(state, cfg, job_id, rc=124, err="timeout")
            return
        except Exception as e:
            _admin_cmd_finish_job(state, cfg, job_id, rc=126, err=f"communicate failed: {e}")
            return

    try:
        # Drain stdout/stderr until both pipes close and the process exits.
        exit_seen_at: Optional[float] = None
        last_activity_at = time.time()
        while True:
            # Timeout enforcement (best-effort).
            try:
                if job.timeout_sec and (time.time() - float(job.t_started)) > float(job.timeout_sec):
                    with state.lock:
                        j2 = state.admin_cmd_jobs.get(job_id)
                        if j2 and not j2.killed:
                            j2.killed = True
                            j2.error = "timeout"
                    _admin_cmd_kill_proc(proc, hard=False)
                    time.sleep(0.3)
                    _admin_cmd_kill_proc(proc, hard=True)
            except Exception:
                pass

            events = sel.select(timeout=0.2)
            for key, _mask in events:
                stream = str(key.data or "")
                try:
                    chunk = os.read(key.fileobj.fileno(), 4096)
                except Exception:
                    chunk = b""
                if not chunk:
                    try:
                        sel.unregister(key.fileobj)
                    except Exception:
                        pass
                    continue
                txt = chunk.decode("utf-8", errors="replace")
                last_activity_at = time.time()
                with state.lock:
                    j2 = state.admin_cmd_jobs.get(job_id)
                    if not j2:
                        continue
                    j2.last_output_utc = _now_utc_iso()
                    if stream == "stderr":
                        j2.stderr, tr = _admin_cmd_append(j2.stderr, txt, _ADMIN_CMD_STDERR_CAP)
                        j2.stderr_truncated = bool(j2.stderr_truncated or tr)
                    else:
                        j2.stdout, tr = _admin_cmd_append(j2.stdout, txt, _ADMIN_CMD_STDOUT_CAP)
                        j2.stdout_truncated = bool(j2.stdout_truncated or tr)
            rc = proc.poll()
            if rc is None:
                exit_seen_at = None
                continue
            if exit_seen_at is None:
                exit_seen_at = time.time()
            if not sel.get_map():
                _admin_cmd_finish_job(state, cfg, job_id, rc=rc)
                return
            # Some commands leave descendants holding the inherited stdout/stderr
            # pipes after the tracked parent has already exited. Once the parent is
            # gone and no new output has arrived for a short grace period, stop
            # waiting for pipe EOF and finalize the job from the parent exit code.
            quiet_for = time.time() - max(exit_seen_at, last_activity_at)
            if quiet_for >= _ADMIN_CMD_POST_EXIT_DRAIN_GRACE_S:
                for key in list(sel.get_map().values()):
                    try:
                        sel.unregister(key.fileobj)
                    except Exception:
                        pass
                _admin_cmd_finish_job(state, cfg, job_id, rc=rc)
                return

    except Exception as e:
        try:
            rc = proc.poll()
        except Exception:
            rc = None
        _admin_cmd_finish_job(state, cfg, job_id, rc=rc, err=f"reader failed: {e}")
        return
    finally:
        try:
            sel.close()
        except Exception:
            pass


def _admin_cmd_start_job(
    state: _ServerState,
    cfg: WebUIConfig,
    *,
    cmd: str,
    cwd_rel: str = "",
    kind: str = "cmd",
    timeout_sec: float = 60.0,
    stdin_bytes: Optional[bytes] = None,
) -> AdminCmdJob:
    """Start an admin command job and return its job record."""

    rr = _admin_repo_root()

    cwd_abs = rr
    try:
        cand = _admin_fs_resolve(rr, cwd_rel)
        if cand and cand.exists() and cand.is_dir() and (not cand.is_symlink()):
            cwd_abs = cand
    except Exception:
        cwd_abs = rr

    job_id = uuid.uuid4().hex[:12]
    job = AdminCmdJob(
        job_id=job_id,
        kind=str(kind or "cmd"),
        cmd=str(cmd or "").strip(),
        cwd_rel=str(cwd_rel or ""),
        cwd_abs=str(cwd_abs),
        utc_started=_now_utc_iso(),
        t_started=float(time.time()),
        timeout_sec=float(timeout_sec or 0.0),
    )

    # Command runner uses bash to support pipes/&& etc (phone UX > purity).
    popen_cmd = ["bash", "-lc", job.cmd]

    try:
        # Attach job id for child processes (e.g. build scripts writing receipts)
        env = _admin_cmd_env()
        # Ensure child processes can bind receipts to this admin job id.
        env["EIEL_ADMIN_JOB_ID"] = job.job_id
        proc = subprocess.Popen(
            popen_cmd,
            cwd=str(cwd_abs),
            env=env,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # gives us a process group for kill
        )
        job.pid = int(proc.pid or 0)
    except Exception as e:
        job.running = False
        job.returncode = 127
        job.error = f"spawn failed: {e}"
        return job

    with state.lock:
        state.admin_cmd_jobs[job_id] = job
        state.admin_cmd_procs[job_id] = proc

    # Send stdin (best-effort) and close it, so scripts that read stdin terminate.
    if stdin_bytes is not None:
        try:
            if proc.stdin is not None:
                proc.stdin.write(stdin_bytes)
                proc.stdin.close()
        except Exception:
            try:
                if proc.stdin is not None:
                    proc.stdin.close()
            except Exception:
                pass

    t = threading.Thread(target=_admin_cmd_job_thread, args=(state, cfg, job_id), daemon=True)
    t.start()

    return job


def _lab_front_door_sessions_root() -> Path:
    return _admin_repo_root() / "governance" / "agents" / "sessions" / "lab_front_door"


def _labsh_history_path() -> Path:
    return _admin_repo_root() / "governance" / "agents" / "runtime" / "labsh_history.txt"


def _append_labsh_history_line(prompt: str) -> None:
    line = str(prompt or "").strip()
    if not line:
        return
    path = _labsh_history_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = ""
    if path.exists():
        try:
            with path.open("rb") as handle:
                try:
                    handle.seek(-4096, os.SEEK_END)
                except OSError:
                    handle.seek(0)
                tail = handle.read().decode("utf-8", errors="ignore").splitlines()
                if tail:
                    previous = str(tail[-1]).strip()
        except Exception:
            previous = ""
    if previous == line:
        return
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)
        handle.write("\n")


def _lab_front_door_history_payload(limit: int = 100) -> Dict[str, Any]:
    entries: List[Dict[str, Any]] = []
    history_path = _labsh_history_path()
    if history_path.exists():
        try:
            lines = history_path.read_text(encoding="utf-8").splitlines()
            for line in reversed(lines):
                prompt = str(line or "").strip()
                if not prompt:
                    continue
                entries.append(
                    {
                        "session_id": "labsh_history",
                        "utc": "",
                        "prompt": prompt,
                        "authority_scope": "",
                    }
                )
        except Exception:
            pass
    root = _lab_front_door_sessions_root()
    if root.exists():
        session_files = sorted(
            root.glob("*/session__lab.jsonl"),
            key=lambda path: path.stat().st_mtime if path.exists() else 0.0,
            reverse=True,
        )
        seen_session_entries = set()
        for session_file in session_files[:250]:
            try:
                for raw_line in session_file.read_text(encoding="utf-8").splitlines():
                    raw_line = raw_line.strip()
                    if not raw_line:
                        continue
                    try:
                        record = json.loads(raw_line)
                    except Exception:
                        continue
                    if str(record.get("kind") or "") != "message":
                        continue
                    if str(record.get("sender") or "") != "user":
                        continue
                    prompt = str(record.get("content") or "").strip()
                    if not prompt:
                        continue
                    session_id = str(record.get("session_id") or session_file.parent.name).strip()
                    utc = str(record.get("utc") or "").strip()
                    dedupe_key = (session_id, utc or prompt)
                    if dedupe_key in seen_session_entries:
                        continue
                    seen_session_entries.add(dedupe_key)
                    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
                    entries.append(
                        {
                            "session_id": session_id,
                            "utc": utc,
                            "prompt": prompt,
                            "authority_scope": str(payload.get("authority_scope") or "").strip(),
                        }
                    )
            except Exception:
                continue
    deduped: List[Dict[str, Any]] = []
    seen_prompts = set()
    for entry in entries:
        prompt = str(entry.get("prompt") or "").strip()
        if not prompt or prompt in seen_prompts:
            continue
        seen_prompts.add(prompt)
        deduped.append(entry)
        if len(deduped) >= max(1, min(500, int(limit or 100))):
            break
    return {"entries": deduped}


def _mobile_plane_run_command(
    *,
    prompt: str,
    plan: bool = False,
    verbose: bool = False,
    options: Optional[Mapping[str, Any]] = None,
) -> str:
    prompt_text = str(prompt or "").strip()
    if not prompt_text:
        raise ValueError("prompt is required")
    cmd_parts = ["python", "-m", "infra.bin.lab"]
    if plan:
        cmd_parts.append("--plan")
    if verbose:
        cmd_parts.append("--verbose")
    opts = dict(options or {})
    flag_map = {
        "patch_path": "--patch",
        "zip_path": "--zip-path",
        "report_file": "--report-file",
        "snapshot_file": "--snapshot-file",
        "receipts_file": "--receipts-file",
        "watchdog_file": "--watchdog-file",
        "iterations_file": "--iterations-file",
        "fallback_events_file": "--fallback-events-file",
        "target_pis_uid": "--target-pis-uid",
        "target_uid": "--target-uid",
        "job_id": "--job-id",
        "task_id": "--task-id",
        "current_status": "--current-status",
        "expected_status": "--expected-status",
        "verifier_verdict": "--verifier-verdict",
    }
    for key, flag in flag_map.items():
        value = str(opts.get(key) or "").strip()
        if value:
            cmd_parts.extend([flag, value])
    for command in list(opts.get("validation_commands") or []):
        text = str(command or "").strip()
        if text:
            cmd_parts.extend(["--validation-command", text])
    if bool(opts.get("allow_fallback")):
        cmd_parts.append("--allow-fallback")
    if bool(opts.get("no_fallback")):
        cmd_parts.append("--no-fallback")
    if bool(opts.get("allow_recovery")):
        cmd_parts.append("--allow-recovery")
    cmd_parts.append(prompt_text)
    return "env NO_COLOR=1 PYTHONPATH=. " + " ".join(shlex.quote(part) for part in cmd_parts)


def _admin_cmd_kill_job(state: _ServerState, job_id: str) -> bool:
    """Kill a running admin command job.

    Returns:
        True if a running process was signaled, else False.
    """

    jid = str(job_id or "").strip()
    if not jid:
        return False

    with state.lock:
        job = state.admin_cmd_jobs.get(jid)
        proc = state.admin_cmd_procs.get(jid)

    if not job or not proc:
        return False

    try:
        if proc.poll() is not None:
            return False
    except Exception:
        pass

    # Mark before kill so UI sees intent immediately.
    with state.lock:
        j2 = state.admin_cmd_jobs.get(jid)
        if j2:
            j2.killed = True

    _admin_cmd_kill_proc(proc, hard=False)
    time.sleep(0.25)
    _admin_cmd_kill_proc(proc, hard=True)
    return True


def _admin_restart_preflight(cfg: WebUIConfig, *, state: Optional[_ServerState] = None) -> Dict[str, Any]:
    """Return whether a WebUI restart is safe for the operator-facing plane."""

    repo_root = _admin_repo_root()
    operator_snapshot = _lab_operator_snapshot.build_operator_snapshot(repo_root)
    active_tasks = list(operator_snapshot.get("active_tasks") or [])
    open_loops = list(operator_snapshot.get("open_loops") or [])
    job_lock_snapshot = _server_maintenance.job_lock_snapshot(repo_root=repo_root)
    active_job_locks = list(job_lock_snapshot.get("active_locks") or [])
    stale_job_locks = list(job_lock_snapshot.get("stale_locks") or [])
    active_admin_cmd_jobs = 0
    if state is not None:
        with state.lock:
            for job in state.admin_cmd_jobs.values():
                if bool(getattr(job, "running", False)):
                    active_admin_cmd_jobs += 1

    blockers: List[Dict[str, Any]] = []
    warnings: List[Dict[str, Any]] = []
    if active_tasks:
        warnings.append(
            {
                "kind": "active_tasks_present",
                "count": len(active_tasks),
                "summary": f"{len(active_tasks)} active task(s) are still visible to the plane.",
            }
        )
    if open_loops:
        warnings.append(
            {
                "kind": "open_loops_present",
                "count": len(open_loops),
                "summary": f"{len(open_loops)} open loop(s) are still visible to the plane.",
            }
        )
    if active_job_locks:
        blockers.append(
            {
                "kind": "scheduler_job_locks_present",
                "count": len(active_job_locks),
                "summary": f"{len(active_job_locks)} scheduler job lock(s) are still active.",
            }
        )
    if active_admin_cmd_jobs:
        blockers.append(
            {
                "kind": "admin_command_jobs_present",
                "count": active_admin_cmd_jobs,
                "summary": f"{active_admin_cmd_jobs} admin command job(s) are still running.",
            }
        )

    return {
        "schema_version": "admin_server_restart_preflight_v1",
        "utc": _now_utc_iso(),
        "ok": not blockers,
        "safe_to_restart": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "operator_snapshot_summary": {
            "operator_state": str(operator_snapshot.get("operator_state") or ""),
            "active_task_count": len(active_tasks),
            "open_loop_count": len(open_loops),
            "backend_state": str((operator_snapshot.get("backend_projection") or {}).get("state") or ""),
        },
        "scheduler": {
            "active_job_lock_count": len(active_job_locks),
            "stale_job_lock_count": len(stale_job_locks),
            "stale_job_locks": stale_job_locks,
        },
        "admin_commands": {
            "active_job_count": active_admin_cmd_jobs,
        },
    }


def _admin_restart_webui(cfg: WebUIConfig) -> None:
    """Restart the WebUI process via the detached repo wrapper when available."""

    repo_root = _admin_repo_root()
    wrapper = (repo_root / "tools" / "bin" / "server_start.sh").resolve()
    wrapper_ok = wrapper.exists() and wrapper.is_file()
    if wrapper_ok:
        args = [
            str(wrapper),
            "--replace-running",
            "--runs_root",
            str(cfg.runs_root),
            "--host",
            str(cfg.host),
            "--port",
            str(int(cfg.port)),
            "--scan_depth",
            str(int(cfg.scan_depth)),
            "--docs_root",
            str(cfg.docs_root),
        ]
        if not bool(cfg.admin_enabled):
            args.append("--disable_admin")
        if str(cfg.admin_token or "").strip():
            args += ["--admin_token", str(cfg.admin_token)]
        launcher_code = (
            "import subprocess,time;"
            f"time.sleep(1.0);"
            f"subprocess.Popen({args!r}, cwd={str(repo_root)!r}, start_new_session=True, "
            "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)"
        )
        subprocess.Popen(
            [sys.executable, "-c", launcher_code],
            cwd=str(repo_root),
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        os._exit(0)

    args = [
        sys.executable,
        "-m",
        "infra.webui.server",
        "--runs_root",
        str(cfg.runs_root),
        "--host",
        str(cfg.host),
        "--port",
        str(int(cfg.port)),
        "--scan_depth",
        str(int(cfg.scan_depth)),
        "--docs_root",
        str(cfg.docs_root),
    ]
    if not bool(cfg.admin_enabled):
        args.append("--disable_admin")
    if str(cfg.admin_token or "").strip():
        args += ["--admin_token", str(cfg.admin_token)]

    try:
        os.chdir(str(repo_root))
    except Exception:
        pass

    os.execv(sys.executable, args)



def _serve_static_file(handler: BaseHTTPRequestHandler, static_dir: Path, rel: str) -> bool:
    """Serve a file from static_dir.

    Supports two common layouts:
    - static_dir points directly at the folder containing assets (app.js, index.html, ...)
    - static_dir points one level above, with assets under static_dir/"static"/

    Returns True if served, else False.
    """
    rel = rel.lstrip("/")

    # Prevent traversal.
    if ".." in rel or rel.startswith("/"):
        return False

    static_dir = Path(static_dir)
    bases = [static_dir, static_dir / "static"]

    # Try resolving against both base candidates.
    for base in bases:
        try:
            base_resolved = base.resolve()
        except Exception:
            continue

        p = (base_resolved / rel).resolve()
        try:
            p.relative_to(base_resolved)
        except Exception:
            continue

        if not p.exists() or not p.is_file():
            continue

        # Basic content type.
        ctype = "application/octet-stream"
        if p.suffix == ".html":
            ctype = "text/html; charset=utf-8"
        elif p.suffix == ".css":
            ctype = "text/css; charset=utf-8"
        elif p.suffix == ".js":
            ctype = "application/javascript; charset=utf-8"
        elif p.suffix == ".svg":
            ctype = "image/svg+xml"

        data = p.read_bytes()
        _bytes_response(handler, HTTPStatus.OK, data, content_type=ctype)
        return True

    return False


def _serve_docs_file(handler: BaseHTTPRequestHandler, docs_root: Path, url_path: str) -> bool:
    """Serve MkDocs build output mounted under /docs/.

    Args:
        handler: request handler.
        docs_root: filesystem directory containing the built site (e.g. ./site).
        url_path: request path (e.g. /docs/, /docs/index.html, /docs/page/).

    Returns:
        True if a file was served.

    Security:
        - Prevent path traversal.
        - Never follow symlinks.
    """

    try:
        root = Path(docs_root).resolve()
    except Exception:
        return False

    if not root.exists() or not root.is_dir():
        return False

    pth = str(url_path or "")
    if pth == "/docs":
        # Redirect to preserve relative asset resolution.
        _redirect_response(handler, HTTPStatus.MOVED_PERMANENTLY, "/docs/")
        return True
    if pth == "/docs/":
        rel = "index.html"
    else:
        if not pth.startswith("/docs/"):
            return False
        rel = pth[len("/docs/") :]

    # Decode percent-encoded segments.
    rel = unquote(rel or "")
    rel = rel.lstrip("/")

    # Prevent traversal.
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if any(p in ("..", ".") for p in parts):
        return False

    # Resolve candidate under docs_root.
    raw_cand = (root / Path(rel)).resolve() if rel else (root / "index.html")
    try:
        raw_cand.relative_to(root)
    except Exception:
        return False

    # If the URL is missing a trailing slash but maps to a directory,
    # redirect so browsers resolve relative asset URLs correctly.
    try:
        if raw_cand.exists() and raw_cand.is_dir() and not pth.endswith("/"):
            _redirect_response(handler, HTTPStatus.MOVED_PERMANENTLY, pth + "/")
            return True
    except Exception:
        pass

    cand = raw_cand

    # Directory -> index.html
    try:
        if cand.exists() and cand.is_dir():
            cand = (cand / "index.html").resolve()
            cand.relative_to(root)
    except Exception:
        return False

    try:
        if not cand.exists() or not cand.is_file() or cand.is_symlink():
            return False
    except Exception:
        return False

    # Basic content type (extend beyond UI static assets).
    suf = cand.suffix.lower()
    ctype = "application/octet-stream"
    if suf in {".html", ".htm"}:
        ctype = "text/html; charset=utf-8"
    elif suf == ".css":
        ctype = "text/css; charset=utf-8"
    elif suf == ".js":
        ctype = "application/javascript; charset=utf-8"
    elif suf == ".json" or suf == ".map":
        ctype = "application/json; charset=utf-8"
    elif suf == ".svg":
        ctype = "image/svg+xml"
    elif suf == ".png":
        ctype = "image/png"
    elif suf in {".jpg", ".jpeg"}:
        ctype = "image/jpeg"
    elif suf == ".gif":
        ctype = "image/gif"
    elif suf == ".webp":
        ctype = "image/webp"
    elif suf == ".ico":
        ctype = "image/x-icon"
    elif suf == ".woff":
        ctype = "font/woff"
    elif suf == ".woff2":
        ctype = "font/woff2"
    elif suf == ".ttf":
        ctype = "font/ttf"
    elif suf == ".otf":
        ctype = "font/otf"

    data = cand.read_bytes()
    _bytes_response(handler, HTTPStatus.OK, data, content_type=ctype)
    return True


def _experiments_projects_root(repo_root: Optional[Path] = None) -> Path:
    return (Path(repo_root or _repo_root()).resolve() / "experiments" / "projects").resolve()


def _project_docs_root(repo_root: Optional[Path], project_id: str) -> Optional[Dict[str, str]]:
    base = (_experiments_projects_root(repo_root) / str(project_id or "")).resolve()
    try:
        base.relative_to(_experiments_projects_root(repo_root))
    except Exception:
        return None
    site_root = (base / "site").resolve()
    if site_root.exists() and site_root.is_dir() and (site_root / "index.html").exists():
        return {
            "project_id": str(project_id or ""),
            "project_root": str(base),
            "docs_root": str(site_root),
            "docs_kind": "site",
            "docs_url": f"/projects/{project_id}/docs/",
        }
    docs_root = (base / "docs").resolve()
    if docs_root.exists() and docs_root.is_dir():
        return {
            "project_id": str(project_id or ""),
            "project_root": str(base),
            "docs_root": str(docs_root),
            "docs_kind": "docs",
            "docs_url": f"/projects/{project_id}/docs/",
        }
    return None


def _discover_project_docs_sites(repo_root: Optional[Path] = None) -> List[Dict[str, str]]:
    root = _experiments_projects_root(repo_root)
    if not root.exists() or not root.is_dir():
        return []
    out: List[Dict[str, str]] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        row = _project_docs_root(repo_root, child.name)
        if row:
            out.append(row)
    return out


def _project_docs_index_html(repo_root: Optional[Path] = None) -> str:
    projects = _discover_project_docs_sites(repo_root)
    items: List[str] = []
    for row in projects:
        project_id = _html.escape(str(row.get("project_id") or ""))
        docs_url = _html.escape(str(row.get("docs_url") or ""))
        items.append(f'<li><a href="{docs_url}">{project_id}</a></li>')
    if not items:
        items.append("<li>No built project docs found under experiments/projects.</li>")
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        "<title>Project Docs</title>"
        "<style>body{font-family:Roboto,Arial,sans-serif;background:#111;color:#eee;padding:2rem;}"
        "a{color:#8ec5ff}code{background:#222;padding:.1rem .3rem;border-radius:.2rem}</style></head><body>"
        "<h1>Project Docs</h1>"
        "<p>Auto-discovered project docs served from <code>experiments/projects/&lt;project&gt;/site</code> when built, or from <code>experiments/projects/&lt;project&gt;/docs</code> as a fallback.</p>"
        f"<ul>{''.join(items)}</ul>"
        "</body></html>"
    )


def _project_docs_directory_index_html(project_id: str, docs_root: Path, rel_parts: List[str]) -> str:
    current = docs_root
    for part in rel_parts:
        current = (current / part).resolve()
    rel_url = "/".join(rel_parts)
    base_url = f"/projects/{project_id}/docs/"
    if rel_url:
        base_url += rel_url.rstrip("/") + "/"
    parent_link = ""
    if rel_parts:
        parent_parts = rel_parts[:-1]
        parent_url = f"/projects/{project_id}/docs/"
        if parent_parts:
            parent_url += "/".join(parent_parts) + "/"
        parent_link = f'<li><a href="{_html.escape(parent_url)}">..</a></li>'
    rows: List[str] = []
    for child in sorted(current.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower())):
        if child.name.startswith("."):
            continue
        label = _html.escape(child.name + ("/" if child.is_dir() else ""))
        href = _html.escape(base_url + child.name + ("/" if child.is_dir() else ""))
        rows.append(f'<li><a href="{href}">{label}</a></li>')
    if not rows:
        rows.append("<li>(empty)</li>")
    crumb = "/".join(rel_parts) if rel_parts else ""
    title = f"{project_id} docs"
    if crumb:
        title += f" / {crumb}"
    return (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='viewport' content='width=device-width, initial-scale=1'>"
        f"<title>{_html.escape(title)}</title>"
        "<style>body{font-family:Roboto,Arial,sans-serif;background:#111;color:#eee;padding:2rem;}"
        "a{color:#8ec5ff}code{background:#222;padding:.1rem .3rem;border-radius:.2rem} ul{line-height:1.8}</style></head><body>"
        f"<h1>{_html.escape(project_id)} docs</h1>"
        f"<p>Browsing <code>{_html.escape(str(current.relative_to(docs_root)) if rel_parts else '.')}</code></p>"
        f"<ul>{parent_link}{''.join(rows)}</ul>"
        "</body></html>"
    )


def _serve_project_docs_source_file(handler: BaseHTTPRequestHandler, project_id: str, docs_root: Path, remainder: str) -> bool:
    rel = unquote(str(remainder or "")).lstrip("/")
    parts = [p for p in rel.replace("\\", "/").split("/") if p]
    if any(p in {"..", "."} for p in parts):
        return False
    target = docs_root
    for part in parts:
        target = (target / part).resolve()
    try:
        target.relative_to(docs_root)
    except Exception:
        return False
    if not parts:
        _bytes_response(
            handler,
            HTTPStatus.OK,
            _project_docs_directory_index_html(project_id, docs_root, []).encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )
        return True
    if target.exists() and target.is_dir():
        pth = f"/projects/{project_id}/docs/" + "/".join(parts)
        if not str(remainder or "").endswith("/"):
            _redirect_response(handler, HTTPStatus.MOVED_PERMANENTLY, pth + "/")
            return True
        _bytes_response(
            handler,
            HTTPStatus.OK,
            _project_docs_directory_index_html(project_id, docs_root, parts).encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )
        return True
    if not target.exists() or not target.is_file() or target.is_symlink():
        return False
    suf = target.suffix.lower()
    ctype = "application/octet-stream"
    if suf in {".html", ".htm"}:
        ctype = "text/html; charset=utf-8"
    elif suf in {".md", ".txt", ".log", ".yaml", ".yml", ".json"}:
        ctype = "text/plain; charset=utf-8"
    elif suf == ".pdf":
        ctype = "application/pdf"
    elif suf == ".docx":
        ctype = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    elif suf == ".xlsx":
        ctype = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    elif suf in {".png"}:
        ctype = "image/png"
    elif suf in {".jpg", ".jpeg"}:
        ctype = "image/jpeg"
    elif suf == ".svg":
        ctype = "image/svg+xml"
    data = target.read_bytes()
    _bytes_response(handler, HTTPStatus.OK, data, content_type=ctype)
    return True


def _serve_project_docs_file(handler: BaseHTTPRequestHandler, repo_root: Optional[Path], url_path: str) -> bool:
    pth = str(url_path or "")
    if pth in {"/projects/docs", "/experiments/docs"}:
        _redirect_response(handler, HTTPStatus.MOVED_PERMANENTLY, pth + "/")
        return True
    if pth in {"/projects/docs/", "/experiments/docs/"}:
        _bytes_response(
            handler,
            HTTPStatus.OK,
            _project_docs_index_html(repo_root).encode("utf-8"),
            content_type="text/html; charset=utf-8",
        )
        return True
    for prefix in ("/projects/", "/experiments/projects/"):
        if not pth.startswith(prefix):
            continue
        suffix = pth[len(prefix) :]
        project_id, sep, remainder = suffix.partition("/docs")
        if not sep:
            return False
        project_id = unquote(project_id.strip("/"))
        if not re.fullmatch(r"[A-Za-z0-9._-]+", project_id):
            return False
        row = _project_docs_root(repo_root, project_id)
        if not row:
            return False
        docs_root = Path(str(row.get("docs_root") or "")).resolve()
        docs_kind = str(row.get("docs_kind") or "")
        if docs_kind == "site":
            mapped_url = "/docs" + str(remainder or "")
            return _serve_docs_file(handler, docs_root, mapped_url)
        if docs_kind == "docs":
            return _serve_project_docs_source_file(handler, project_id, docs_root, str(remainder or ""))
        return False
    return False


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
