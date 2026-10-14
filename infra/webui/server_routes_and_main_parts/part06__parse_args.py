def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    """Parse CLI args."""
    p = argparse.ArgumentParser(description="Causyl operator plane (experimental)")
    p.add_argument("--enable-operator-runtime", action="store_true",
                   help="Explicitly enable the experimental operator service and its background workers")
    p.add_argument(
        "--runs_root",
        type=str,
        default=os.environ.get("EIEL_RUNS_ROOT")
        or "runtime/adhoc",
        help="Root directory containing run folders (default: env EIEL_RUNS_ROOT or ./runtime/adhoc)",
    )
    p.add_argument("--host", type=str, default="127.0.0.1")
    p.add_argument("--port", type=int, default=8787)
    p.add_argument("--scan_depth", type=int, default=3, help="How deep to scan under runs_root (default: 3)")
    p.add_argument(
        "--docs_root",
        type=str,
        default=os.environ.get("EIEL_DOCS_ROOT") or "./site",
        help="Path to MkDocs build output directory to serve under /docs/ (default: ./site)",
    )

    # Operator routes require explicit credentials even on loopback.
    p.add_argument(
        "--disable_admin",
        action="store_true",
        help="Disable operator routes; scoped assistant routes retain their own authorization",
    )
    p.add_argument(
        "--admin_token",
        type=str,
        default=os.environ.get("EIEL_ADMIN_TOKEN", ""),
        help="Required operator token: use EIEL_ADMIN_TOKEN, browser login or X-Admin-Token; no query tokens",
    )

    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    """Main entrypoint."""
    args = parse_args(argv)
    if not args.enable_operator_runtime:
        raise SystemExit("Operator runtime is disabled by default. Use the offline demo; see docs/release/LIMITATIONS.md before explicitly enabling it.")
    if str(args.host) not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("This source preview supports loopback binding only.")
    if not args.disable_admin and not str(args.admin_token).strip():
        raise SystemExit("Set EIEL_ADMIN_TOKEN or pass --disable_admin before enabling the operator runtime.")

    # Local convenience defaults for the plane Codex backend.
    # Applied only for normal runs (not pytest) and only when variables are unset.
    if "PYTEST_CURRENT_TEST" not in os.environ:
        repo_root_for_agents = Path(__file__).resolve().parents[2]
        codex_bin = shutil.which("codex")
        if not codex_bin:
            p1 = Path("/home/user/.nvm/versions/node/v24.14.0/bin/codex")
            if p1.exists() and p1.is_file():
                codex_bin = str(p1)
        if codex_bin:
            if not str(os.environ.get("EIEL_AGENTS_BACKEND") or "").strip():
                os.environ["EIEL_AGENTS_BACKEND"] = "codex"
            if not str(os.environ.get("EIEL_CODEX_PROTOCOL") or "").strip():
                os.environ["EIEL_CODEX_PROTOCOL"] = "oneshot"
            if not str(os.environ.get("EIEL_CODEX_TURN_CMD") or "").strip():
                if str(os.environ.get("EIEL_CODEX_UNRESTRICTED") or "0").strip() == "1":
                    os.environ["EIEL_CODEX_TURN_CMD"] = (
                        f"{codex_bin} --dangerously-bypass-approvals-and-sandbox exec -C {str(repo_root_for_agents)} --skip-git-repo-check -"
                    )
                else:
                    os.environ["EIEL_CODEX_TURN_CMD"] = (
                        f"{codex_bin} -a never -s workspace-write exec -C {str(repo_root_for_agents)} --skip-git-repo-check -"
                    )

    runs_root = Path(args.runs_root).expanduser().resolve()
    repo_root = Path(__file__).resolve().parents[2]
    cfg = WebUIConfig(
        runs_root=runs_root,
        host=str(args.host),
        port=int(args.port),
        scan_depth=int(args.scan_depth),
        docs_root=Path(getattr(args, "docs_root", "./site") or "./site").expanduser().resolve(),
        admin_enabled=(not bool(getattr(args, "disable_admin", False))),
        admin_token=str(getattr(args, "admin_token", "") or "").strip(),
    )
    lab_host_boot = _bootstrap_lab_assistant_host(repo_root=_repo_root_from_webui())
    def _mobile_sessions_init_async() -> None:
        try:
            _mobile_sessions_init(cfg)
        except Exception as exc:
            try:
                sys.stderr.write(f"[webui] WARNING: mobile session init failed: {type(exc).__name__}: {exc}\n")
            except Exception:
                pass

    def _execution_queue_init_async() -> None:
        try:
            _execution_queue.ensure_worker_running()
        except Exception as exc:
            try:
                sys.stderr.write(f"[webui] WARNING: execution queue init failed: {type(exc).__name__}: {exc}\n")
            except Exception:
                pass

    threading.Thread(
        target=_mobile_sessions_init_async,
        name="webui-mobile-sessions-init",
        daemon=True,
    ).start()
    _agents.configure_host_verification_dispatch(mode="server_worker")
    threading.Thread(
        target=_execution_queue_init_async,
        name="webui-execution-queue-init",
        daemon=True,
    ).start()
    log_path = _install_persistent_stdio_log(cfg)

    if not cfg.static_dir.exists():
        raise FileNotFoundError(f"static dir missing: {cfg.static_dir}")

    # MkDocs output is optional; /docs/ will 404 if missing.
    try:
        if not cfg.docs_root.exists() or not cfg.docs_root.is_dir():
            sys.stderr.write(f"[webui] WARNING: docs_root missing: {cfg.docs_root} (requests to /docs will 404)\n")
    except Exception:
        pass

    server = ThreadingHTTPServer((cfg.host, cfg.port), WebUIRequestHandler)
    server.state = _ServerState(cfg)  # type: ignore[attr-defined]
    sched_autostart = str(os.environ.get("EIEL_SCHEDULED_JOBS_AUTOSTART", "0") or "0").strip().lower() not in {
        "0",
        "false",
        "no",
        "off",
    }
    sched_interval_s = max(
        5,
        int(str(os.environ.get("EIEL_SCHEDULED_JOBS_INTERVAL_S", _scheduled_jobs.DEFAULT_SCHEDULER_INTERVAL_S) or _scheduled_jobs.DEFAULT_SCHEDULER_INTERVAL_S)),
    )
    if sched_autostart:
        _scheduled_jobs.start_scheduler_service(
            interval_s=sched_interval_s,
            repo_root=_repo_root_from_webui(),
        )

    print(f"[webui] runs_root={cfg.runs_root}")
    print(f"[webui] http://{cfg.host}:{cfg.port}/")
    print(f"[webui] mobile admin: http://{cfg.host}:{cfg.port}/mobile")
    print(f"[webui] log_file={log_path} (rotates at 10MB, backups=3)")
    print(
        "[webui] agents backend="
        f"{str(os.environ.get('EIEL_AGENTS_BACKEND') or 'llm')} "
        f"protocol={str(os.environ.get('EIEL_CODEX_PROTOCOL') or '-')}"
    )
    print("[webui] host_verification_dispatch=server_worker")
    print(
        "[webui] lab_assistant_host="
        f"{str(lab_host_boot.get('status') or 'unknown')}"
        f" mode={str(lab_host_boot.get('mode') or 'unknown')}"
    )
    if sched_autostart:
        print(f"[webui] scheduled_jobs=enabled interval_s={sched_interval_s}")
    else:
        print("[webui] scheduled_jobs=disabled")

    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("[webui] shutdown")
        return 0
    finally:
        _scheduled_jobs.stop_scheduler_service()
        try:
            _lab_host.mark_clean_shutdown(_repo_root_from_webui(), reason="webui_main_exit")
        except Exception:
            pass


# -----------------------------------------------------------------------------
# Process control helpers (start/stop/resume)
# -----------------------------------------------------------------------------

def _repo_root_for_launch() -> Path:
    """Return repo root for launching local python modules.

    NOTE:
        A previous refactor accidentally defined _repo_root_from_webui() twice,
        which caused infinite recursion via _builder_paths(). The canonical repo
        root resolver is _repo_root_from_webui() (defined near the builder helpers).

        This alias remains for readability in process-control code paths.
    """

    return _repo_root_from_webui()


def _is_planned_run_status(rs: Optional[Dict[str, Any]]) -> bool:
    """Return True if run_status indicates a PLANNED (not-yet-started) run."""
    if not isinstance(rs, dict):
        return False
    raw_status = str(rs.get("status", "") or "").lower()
    branch_state = str(rs.get("branch_state", "") or "")
    return raw_status == "planned" or branch_state == "PLANNED"


def _latest_checkpoint_entry(sim_root: Path) -> Optional[Dict[str, Any]]:
    """Return the newest checkpoint summary entry (best-effort)."""
    cps = _summarize_checkpoints(sim_root)
    if not cps:
        return None
    try:
        return sorted(cps, key=lambda d: int(d.get("generation", 0) or 0))[-1]
    except Exception:
        return cps[-1]


def _checkpoint_abs_path(sim_root: Path, rel_path: str) -> Path:
    """Resolve a checkpoint relative path against sim_root."""
    return (sim_root / Path(str(rel_path))).resolve()


def _planned_parent_run_key(plan_root: Path) -> str:
    """Best-effort parent_run_key resolver for planned runs.

    Precedence:
      1) ui/branch_plan.json -> parent_run_key
      2) ui/branch_meta.json -> created_from.parent_run_key
      3) run_manifest.json -> planned_from.parent_run_key
    """

    # 1) ui/branch_plan.json
    obj = _read_json(plan_root / "ui" / "branch_plan.json")
    if isinstance(obj, dict):
        prk = str(obj.get("parent_run_key") or "").strip()
        if prk:
            return normalize_run_key(prk)

    # 2) ui/branch_meta.json
    obj = _read_json(plan_root / "ui" / "branch_meta.json")
    if isinstance(obj, dict):
        cf = obj.get("created_from") if isinstance(obj.get("created_from"), dict) else None
        if isinstance(cf, dict):
            prk = str(cf.get("parent_run_key") or "").strip()
            if prk:
                return normalize_run_key(prk)

    # 3) run_manifest.json placeholder
    obj = _read_json(plan_root / "run_manifest.json")
    if isinstance(obj, dict):
        pf = obj.get("planned_from") if isinstance(obj.get("planned_from"), dict) else None
        if isinstance(pf, dict):
            prk = str(pf.get("parent_run_key") or "").strip()
            if prk:
                return normalize_run_key(prk)

    return ""


def _stash_planned_skeleton(cfg: WebUIConfig, rk: str, run_root: Path) -> Path:
    """Move a planned run directory out of the way into runtime/state/webui/.

    Why this exists
    ---------------
    engine.main insists on creating a *new* run_dir (it suffixes _N if
    the target already exists). Planned runs are represented as a real directory
    under runs_root (so the UI can show them and accept scheduled inbox
    commands). That conflicts with the runner's uniqueness rule.

    So we temporarily move the planned skeleton aside, let the runner create the
    real run_dir at the planned path, then restore UI-owned overlays (ui/, inbox/,
    resume_from.json, branch_plan.json).

    Returns:
        Absolute path to the backup directory.
    """
    cache_root = (_webui_runtime_root() / "planned_skeletons").resolve()
    os.makedirs(cache_root, exist_ok=True)

    safe_key = normalize_run_key(rk).replace("/", "__")
    suffix = uuid.uuid4().hex[:8]
    backup = cache_root / f"{safe_key}__{suffix}"

    i = 1
    while backup.exists():
        backup = cache_root / f"{safe_key}__{suffix}_{i}"
        i += 1

    run_root.rename(backup)
    return backup


def _restore_planned_overlay(backup_root: Path, new_run_root: Path) -> None:
    """Restore UI-owned overlays from a stashed planned skeleton.

    Restored:
      - ui/ (branch_meta.json, ui_meta.json, notes.md, etc)
      - inbox/ (scheduled control commands)
      - resume_from.json (legacy convenience pointer)
      - ui/branch_plan.json is already inside ui/, but we also copy branch_plan.json
        if it exists at the backup root (older snapshots).

    Not restored (by design):
      - viz/ (runner will write real run_status.json, determinism status, etc)
      - run_manifest.json placeholder (runner will write the real one)
    """
    try:
        src_ui = backup_root / "ui"
        if src_ui.exists() and src_ui.is_dir():
            dst_ui = new_run_root / "ui"
            os.makedirs(dst_ui, exist_ok=True)
            shutil.copytree(src_ui, dst_ui, dirs_exist_ok=True)
    except Exception:
        pass

    try:
        src_inbox = backup_root / "inbox"
        if src_inbox.exists() and src_inbox.is_dir():
            dst_inbox = new_run_root / "inbox"
            os.makedirs(dst_inbox, exist_ok=True)
            shutil.copytree(src_inbox, dst_inbox, dirs_exist_ok=True)
    except Exception:
        pass

    for fname in ("resume_from.json", "branch_plan.json"):
        try:
            src = backup_root / fname
            if src.exists() and src.is_file():
                shutil.copy2(src, new_run_root / fname)
        except Exception:
            continue


def _compute_additional_generations(rs: Optional[Dict[str, Any]], ckpt_gen: int) -> Optional[int]:
    """Compute remaining generations to run (best-effort).

    For resumed runs, engine.main treats --generations as *additional*
    generations to run beyond the resume checkpoint's generation.

    If target_generation is missing/invalid, we return None to let the runner
    fall back to checkpoint snapshot config.
    """
    try:
        tgt = int(rs.get("target_generation", -1) or -1) if isinstance(rs, dict) else -1
    except Exception:
        tgt = -1
    if tgt > 0 and ckpt_gen >= 0:
        rem = int(tgt) - int(ckpt_gen)
        if rem > 0:
            return rem
    return None


def _wait_for_dir(p: Path, *, timeout_s: float = 2.0) -> bool:
    """Wait briefly for a directory to appear."""
    deadline = time.time() + float(timeout_s)
    while time.time() < deadline:
        if p.exists() and p.is_dir():
            return True
        time.sleep(0.05)
    return bool(p.exists() and p.is_dir())


def _launch_evo_main(*, repo_root: Path, runs_dir: Path, run_id: str, resume_from: Optional[str], generations: Optional[int]) -> subprocess.Popen:
    """Launch engine.main as a subprocess.

    We use sys.executable to guarantee we run in the same venv as the web server.
    """
    cmd: List[str] = [
        sys.executable,
        "-m",
        "engine.main",
        "--runs_dir",
        str(runs_dir),
        "--run_id",
        str(run_id),
    ]
    if resume_from:
        cmd += ["--resume_from", str(resume_from)]
    if generations is not None:
        cmd += ["--generations", str(int(generations))]

    # start_new_session isolates signal handling (useful when stop sends SIGINT).
    return subprocess.Popen(cmd, cwd=str(repo_root), start_new_session=True)


def _registry_path(cfg: WebUIConfig) -> Path:
    """Return WebUI run-state registry path under runtime/state."""
    return (_webui_runtime_root() / "registry" / "webui_registry.json").resolve()


def _patch_backlog_path(cfg: WebUIConfig) -> Path:
    """Return canonical patch backlog path under runtime/state."""
    return (_webui_runtime_root() / "patch-backlog.json").resolve()


def _legacy_patch_backlog_path(cfg: WebUIConfig) -> Path:
    """Return legacy patch backlog path under runs root for one-time read fallback."""
    return (Path(cfg.runs_root) / "patch-backlog.json").resolve()


def _backlog_gpt_outputs_dir(cfg: WebUIConfig) -> Path:
    """Return canonical GPT backlog output directory under runtime/artifacts."""
    repo_root = _repo_root_from_webui()
    return (Path(repo_root) / "runtime" / "artifacts" / "backlog_gpt_outputs").resolve()


def _legacy_runs_archives_dir(cfg: WebUIConfig) -> Path:
    """Legacy run-archive directory under runs root (read-only fallback)."""
    return (Path(cfg.runs_root) / "archives").resolve()


def _archived_keys_index(cfg: WebUIConfig) -> set[str]:
    """Return archived run keys from canonical and legacy archive directories."""
    out: set[str] = set()
    try:
        out |= _scan_archived_run_keys_by_zip(runs_archives_dir(cfg.runs_root))
    except Exception:
        pass
    try:
        out |= _scan_archived_run_keys_by_zip(_legacy_runs_archives_dir(cfg))
    except Exception:
        pass
    return out


def _managed_marker_path(run_root: Path) -> Path:
    """Return path to the managed marker file for a run."""
    return (run_root / 'ui' / 'webui_managed.json').resolve()


def _read_pid_file(p: Path) -> int:
    """Read pid from a text file; return 0 on failure."""
    try:
        txt = p.read_text(encoding='utf-8').strip()
        return int(txt) if txt else 0
    except Exception:
        return 0


def _pid_alive(pid: int) -> bool:
    """Return True if pid appears alive on Linux/WSL."""
    if pid <= 0:
        return False
    try:
        return Path(f'/proc/{int(pid)}').exists()
    except Exception:
        return False




def _derive_run_state(run_root: Path, sim_root: Path) -> str:
    """Derive liveness state (RUNNING/HUNG/STOPPED) without affecting visibility.

    Uses:
    - ui/pid.txt (if present) to check /proc/<pid>
    - viz/run_heartbeat.json (preferred) or heartbeat.json (fallback) for freshness (<60s)
    """
    pid_alive = False
    heartbeat_fresh = False

    pid_file = Path(run_root) / "ui" / "pid.txt"
    try:
        if pid_file.exists() and pid_file.is_file() and not pid_file.is_symlink():
            pid = int(pid_file.read_text(encoding="utf-8", errors="ignore").strip() or "0")
            if pid > 0:
                pid_alive = Path(f"/proc/{pid}").exists()
    except Exception:
        pid_alive = False

    hb_path = Path(sim_root) / "viz" / "run_heartbeat.json"
    if not hb_path.exists():
        hb_path = Path(sim_root) / "heartbeat.json"
    try:
        if hb_path.exists() and hb_path.is_file() and not hb_path.is_symlink():
            import datetime as _dt
            obj = _read_json(hb_path) or {}
            ts_s = str(obj.get("heartbeat_utc") or obj.get("utc") or obj.get("timestamp_utc") or "")
            if ts_s:
                ts = _dt.datetime.fromisoformat(ts_s.replace("Z", "+00:00")).replace(tzinfo=None)
                age = (_dt.datetime.utcnow() - ts).total_seconds()
                heartbeat_fresh = (age >= 0) and (age < 60)
    except Exception:
        heartbeat_fresh = False

    if pid_alive and heartbeat_fresh:
        return "RUNNING"
    if pid_alive:
        return "HUNG"
    return "STOPPED"
def _heartbeat_info(sim_root: Path) -> Dict[str, Any]:
    """Read heartbeat.json and return {gen, age_s}."""
    hb = sim_root / 'heartbeat.json'
    if not hb.exists() or not hb.is_file() or hb.is_symlink():
        return {'gen': None, 'age_s': None}
    try:
        obj = _read_json(hb)
        if not isinstance(obj, dict):
            return {'gen': None, 'age_s': None}
        ts = str(obj.get('ts') or obj.get('utc') or '')
        gen = obj.get('gen') if obj.get('gen') is not None else obj.get('generation')
        age = None
        if ts:
            # Parse naive ISO like 2026-...Z (best-effort)
            try:
                # Convert to epoch using time.strptime (UTC)
                import time as _t
                t = ts.replace('Z','')
                fmt = '%Y-%m-%dT%H:%M:%S'
                if '.' in t:
                    t = t.split('.',1)[0]
                epoch = int(_t.mktime(_t.strptime(t, fmt)))
                now = int(_t.time())
                age = max(0, now - epoch)
            except Exception:
                age = None
        try:
            gen_i = int(gen) if gen is not None else None
        except Exception:
            gen_i = None
        return {'gen': gen_i, 'age_s': age}
    except Exception:
        return {'gen': None, 'age_s': None}


def _ensure_start_cmd(cfg: WebUIConfig, run_root: Path, sim_root: Path, rk: str) -> Path:
    """Ensure ui/start_cmd.json exists; create a minimal one for planned runs if missing."""
    ui_dir = run_root / 'ui'
    ui_dir.mkdir(parents=True, exist_ok=True)
    p = ui_dir / 'start_cmd.json'
    if p.exists():
        return p
    # Only synthesize for planned runs (branch_plan.json + resume_from.json).
    ptr = _read_json(run_root / 'resume_from.json')
    plan = _read_json(ui_dir / 'branch_plan.json')
    if not isinstance(ptr, dict) or not isinstance(plan, dict):
        raise ValueError('missing start_cmd.json (not a planned run)')
    rel = str(ptr.get('checkpoint_dir') or '').strip()
    parent_key = str(plan.get('parent_run_key') or '').strip()
    if not rel or not parent_key:
        raise ValueError('planned run missing checkpoint_dir or parent_run_key')
    _pr, parent_sim = resolve_run_dirs(cfg.runs_root, parent_key)
    resume_path = str(_checkpoint_abs_path(parent_sim, rel))
    rs, _ = read_run_status(sim_root)
    cur = _safe_int(rs.get('current_generation'), -1) if isinstance(rs, dict) else -1
    tgt = _safe_int(rs.get('target_generation'), -1) if isinstance(rs, dict) else -1
    add = (tgt - cur) if (cur >= 0 and tgt > cur) else None
    argv = ['python','-m','engine.main','--runs_dir', str(cfg.runs_root), '--run_id', rk, '--resume_from', resume_path, '--allow_existing_run_dir']
    if add is not None and add > 0:
        argv += ['--generations', str(int(add))]
    atomic_write_canonical_json(p, {'schema_version':'start_cmd_v1','created_at_utc': _now_utc_iso(), 'argv': argv, 'env_add': {'EIEL_RUNS_ROOT': str(cfg.runs_root)}, 'cwd': str(_repo_root_from_webui())})
    return p


def _count_active_managed(cfg: WebUIConfig) -> int:
    """Count managed runs that appear alive (STARTING/RUNNING with live pid)."""
    try:
        from infra.webui import run_registry as _rr  # noqa: WPS433
    except Exception:
        import infra.webui.run_registry as _rr  # type: ignore  # noqa: WPS433
    regp = _registry_path(cfg)
    reg = _rr.load_registry(regp)
    runs = reg.get('runs') if isinstance(reg.get('runs'), dict) else {}
    n = 0
    for _rk, rec in runs.items():
        if not isinstance(rec, dict):
            continue
        st = str(rec.get('state') or '').upper()
        if st not in {'STARTING','RUNNING','HUNG'}:
            continue
        pid = int(rec.get('pid') or 0)
        if pid and _pid_alive(pid):
            n += 1
    return n


def _system_process_match_info(
    *,
    cfg: WebUIConfig,
    args: str,
    pid: int,
    known_jobs: Dict[int, Dict[str, Any]],
) -> Tuple[bool, str, str]:
    """Classify whether a process looks like a simulation or plane-runtime target."""
    arg_txt = str(args or "")
    repo_root = str(_repo_root_from_webui())
    runs_root = str(Path(cfg.runs_root).resolve())
    low = arg_txt.lower()
    if "infra.webui.server" in low:
        return False, "", ""

    job = known_jobs.get(int(pid))
    if job:
        module = str(job.get("launch_target_module") or job.get("selected_target") or "").strip()
        return True, "builder_job", module

    tokens = (
        "tools.run_job",
        "tools.manifest.manifest_runner",
        "infra.webui.run_launcher",
        "experiments.harnesses.generated.",
        "experiments.runs.generated.",
        "experiments.harness.run_experiments",
        "engine.main",
    )
    if any(tok in arg_txt for tok in tokens):
        kind = "simulation"
        if "infra.webui.run_launcher" in arg_txt:
            kind = "launcher"
        elif "tools.run_job" in arg_txt:
            kind = "wrapper"
        return True, kind, ""

    if (repo_root and repo_root in arg_txt) or (runs_root and runs_root in arg_txt):
        if "--run_id" in arg_txt or "--runs_dir" in arg_txt or "wrapper_jobs" in arg_txt:
            return True, "simulation", ""

    return False, "", ""


def _system_processes_payload(cfg: WebUIConfig, *, include_all: bool = False) -> Dict[str, Any]:
    """Return host process snapshots for simulation-like processes."""
    try:
        jobs = _jobreg.reconcile_all(cfg.runs_root)
    except Exception:
        jobs = _jobreg.list_jobs(cfg.runs_root)

    known_jobs: Dict[int, Dict[str, Any]] = {}
    for rec in list(jobs or []):
        if not isinstance(rec, dict):
            continue
        try:
            pid = int(rec.get("pid") or 0)
        except Exception:
            pid = 0
        if pid > 0:
            known_jobs[pid] = dict(rec)

    cmd = ["ps", "-eo", "pid=,ppid=,pgid=,stat=,etime=,pcpu=,rss=,nlwp=,args="]
    txt = subprocess.check_output(cmd, text=True, errors="replace")
    out: List[Dict[str, Any]] = []
    total_scanned = 0
    for ln in str(txt or "").splitlines():
        parts = str(ln or "").strip().split(None, 8)
        if len(parts) < 9:
            continue
        total_scanned += 1
        try:
            pid = int(parts[0])
            ppid = int(parts[1])
            pgid = int(parts[2])
            threads = int(parts[7])
        except Exception:
            continue
        stat = str(parts[3] or "").strip()
        etime = str(parts[4] or "").strip()
        try:
            pcpu = float(parts[5])
        except Exception:
            pcpu = 0.0
        try:
            rss_kb = int(parts[6])
        except Exception:
            rss_kb = 0
        args = str(parts[8] or "").strip()
        matched, process_kind, detected_module = _system_process_match_info(
            cfg=cfg,
            args=args,
            pid=pid,
            known_jobs=known_jobs,
        )
        if (not include_all) and (not matched):
            continue
        job = known_jobs.get(pid) or {}
        ident = _display_identity(
            canonical_id=str(job.get("job_id") or pid),
            label=str(job.get("launch_target_module") or detected_module or process_kind or f"pid-{pid}"),
            module=str(job.get("launch_target_module") or detected_module or ""),
            stable_hint=str(job.get("job_spec_hash") or pid),
            fallback="process",
        )
        out.append(
            {
                "pid": int(pid),
                "ppid": int(ppid),
                "pgid": int(pgid),
                "stat": stat,
                "elapsed": etime,
                "pcpu": float(pcpu),
                "rss_kb": int(rss_kb),
                "threads": int(threads),
                "args": args,
                "matches_simulation": bool(matched),
                "process_kind": process_kind,
                "launch_target_module": str(job.get("launch_target_module") or detected_module or "").strip(),
                "job_id": str(job.get("job_id") or "").strip(),
                "report_dir": str(job.get("report_dir") or "").strip(),
                **ident,
            }
        )

    out.sort(key=lambda row: (0 if row.get("matches_simulation") else 1, -int(row.get("pid") or 0)))
    return {
        "schema_version": "system_processes_v1",
        "utc": _now_utc_iso(),
        "include_all": bool(include_all),
        "total_scanned": int(total_scanned),
        "processes": out,
    }


def _system_signal_process(cfg: WebUIConfig, *, pid: int, hard: bool = False) -> Dict[str, Any]:
    """Send SIGTERM/SIGKILL to a simulation-like process group."""
    target_pid = int(pid or 0)
    if target_pid <= 0:
        raise ValueError("invalid pid")
    snapshot = _system_processes_payload(cfg, include_all=True)
    proc = None
    for row in list(snapshot.get("processes") or []):
        if int(row.get("pid") or 0) == target_pid:
            proc = dict(row)
            break
    if not proc:
        raise ValueError("process not found")
    if not bool(proc.get("matches_simulation")):
        raise ValueError("refusing to signal non-simulation process")

    sig = signal.SIGKILL if hard else signal.SIGTERM
    pgid = int(proc.get("pgid") or 0)
    method = "pid"
    if pgid > 0:
        try:
            os.killpg(pgid, sig)
            method = "pgid"
        except ProcessLookupError:
            pass
        except Exception:
            os.kill(target_pid, sig)
    else:
        os.kill(target_pid, sig)

    return {
        "schema_version": "system_process_signal_v1",
        "utc": _now_utc_iso(),
        "pid": int(target_pid),
        "pgid": int(pgid),
        "signal": "SIGKILL" if hard else "SIGTERM",
        "method": method,
        "process_kind": str(proc.get("process_kind") or ""),
        "job_id": str(proc.get("job_id") or ""),
        "display_id": str(proc.get("display_id") or ""),
    }


def _start_run(state: _ServerState, cfg: WebUIConfig, rk: str) -> Dict[str, Any]:
    """Start a PLANNED run via detached launcher (WebUI-managed)."""
    rk = normalize_run_key(rk)
    run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
    rs, _ = read_run_status(sim_root)
    if not _is_planned_run_status(rs):
        raise ValueError('start only supports PLANNED runs')

    # Concurrency guard (managed only).
    max_n = int(os.environ.get('WEBUI_MAX_CONCURRENT_RUNS', '2') or '2')
    if _count_active_managed(cfg) >= max_n:
        raise ValueError(f'limit_reached: max={max_n}')

    # Ensure start cmd exists.
    _ = _ensure_start_cmd(cfg, run_root, sim_root, rk)

    # Mark as managed.
    mm = _managed_marker_path(run_root)
    mm.parent.mkdir(parents=True, exist_ok=True)
    if not mm.exists():
        atomic_write_canonical_json(mm, {'schema_version':'webui_managed_v1','managed': True,'origin':'webui','created_at_utc': _now_utc_iso()})

    # Update registry state.
    try:
        from infra.webui import run_registry as _rr  # noqa: WPS433
    except Exception:
        import infra.webui.run_registry as _rr  # type: ignore  # noqa: WPS433
    regp = _registry_path(cfg)
    _rr.upsert_run(regp, run_key=rk, run_dir=str(run_root), origin='webui', state='STARTING')

    # Launch detached launcher.
    repo_root = _repo_root_for_launch()
    cmd = [sys.executable, '-m', 'infra.webui.run_launcher', '--run_dir', str(run_root), '--repo_root', str(repo_root)]
    proc = subprocess.Popen(cmd, cwd=str(repo_root), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)

    # Briefly check for immediate failure (launcher crashes fast if config is invalid).
    try:
        time.sleep(0.2)
        rc = proc.poll()
        if rc is not None and int(rc) != 0:
            return {
                'status': 'error',
                'run_key': rk,
                'managed': True,
                'launcher_pid': int(proc.pid or 0),
                'returncode': int(rc),
                'hint': 'launcher exited immediately; check infra/webui/run_launcher logs or runner.log in run dir',
            }
    except Exception:
        pass

    # We do not track the launcher process; it exits quickly.
    return {'status':'starting','run_key': rk, 'launcher_pid': int(proc.pid or 0), 'managed': True, 'max_concurrent': max_n}


def _discover_run_bound_pids(rr: Path) -> List[int]:
    """Best-effort discovery of live run processes by runs_dir/run_id argv."""
    out: List[int] = []
    try:
        runs_dir = str(rr.parent.resolve())
        run_id = str(rr.name)
        txt = subprocess.check_output(["ps", "-eo", "pid,args"], text=True, errors="replace")
        for ln in str(txt or "").splitlines():
            s = str(ln or "").strip()
            if not s:
                continue
            parts = s.split(None, 1)
            if len(parts) < 2:
                continue
            try:
                p = int(parts[0])
            except Exception:
                continue
            cmd = str(parts[1] or "")
            if p <= 0:
                continue
            if (f"--runs_dir {runs_dir}" in cmd) and (f"--run_id {run_id}" in cmd):
                out.append(int(p))
    except Exception:
        return []
    return out

def _stop_run(state: _ServerState, rk: str) -> Dict[str, Any]:
    """Stop a RUNNING run by SIGTERM (managed and external)."""
    rk = normalize_run_key(rk)
    # Resolve without cfg here: run index has it or it exists in runs_root.
    cfg = state.cfg
    run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)

    if not _managed_marker_path(run_root).exists():
        # External run path: reuse existing terminate logic, but expose unified
        # "stop" semantics to the UI.
        ext = _terminate_unmanaged_run(state, rk, force=True)
        return {
            "status": "stopping",
            "run_key": rk,
            "pid": None,
            "pid_source": "external_discovery",
            "pids_signaled": [int(x) for x in list(ext.get("pids_signaled") or [])],
            "source": "external",
        }

    pid = _read_pid_file(run_root / 'ui' / 'pid.txt')
    pid_source = "pid.txt"
    pids_to_signal: List[int] = []
    if pid > 0:
        pids_to_signal.append(int(pid))
    if pid <= 0:
        # Wrapper-managed runs (tools.run_job) do not populate run/ui/pid.txt.
        # Fallback: discover the owning managed builder job whose report_dir
        # contains this run path, and use its process PID for stop signaling.
        rr = Path(run_root).resolve()
        try:
            jobs = _jobreg.reconcile_all(cfg.runs_root)
        except Exception:
            jobs = _jobreg.list_jobs(cfg.runs_root)
        for rec in list(jobs or []):
            if not isinstance(rec, dict):
                continue
            report_dir = str(rec.get('report_dir') or '').strip()
            if not report_dir:
                continue
            try:
                rep = Path(report_dir).resolve()
            except Exception:
                continue
            if _safe_resolve_under(rep, rr) is None:
                continue
            cand = int(rec.get('pid') or 0)
            if cand > 0:
                pid = cand
                pid_source = "job_registry"
                pids_to_signal.append(int(cand))
                break

    for p in _discover_run_bound_pids(rr):
        if int(p) not in pids_to_signal:
            pids_to_signal.append(int(p))

    if not pids_to_signal:
        raise ValueError('missing pid.txt (run may not have started)')

    for spid in list(pids_to_signal):
        try:
            os.killpg(int(spid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            # Fallback: signal pid directly.
            try:
                os.kill(int(spid), signal.SIGTERM)
            except Exception:
                pass

    # Managed stop is best-effort SIGTERM only; avoid unconditional hard-kill
    # escalation in this path to keep signal semantics stable for wrappers and tests.
    hard_killed: List[int] = []

    try:
        from infra.webui import run_registry as _rr  # noqa: WPS433
    except Exception:
        import infra.webui.run_registry as _rr  # type: ignore  # noqa: WPS433
    _rr.set_state(_registry_path(cfg), run_key=rk, state='STOPPED', pid=int(pid), message='SIGTERM sent')
    return {
        'status':'stopping',
        'run_key': rk,
        'pid': int(pid),
        'pid_source': pid_source,
        'pids_signaled': [int(x) for x in pids_to_signal],
        'pids_hard_killed': [int(x) for x in hard_killed],
    }


def _terminate_unmanaged_run(state: _ServerState, rk: str, *, force: bool = False) -> Dict[str, Any]:
    """Stop unmanaged run process group(s) discovered from argv/pid hints.

    Why:
    - Legacy external runs can appear as HUNG/STOPPED in run_status while the
      engine process is still alive.
    - argv formats vary; keep stop resilient by combining multiple pid sources.
    """
    rk = normalize_run_key(rk)
    cfg = state.cfg
    run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
    if _managed_marker_path(run_root).exists():
        raise ValueError("managed run: use stop action")

    rs, _ = read_run_status(sim_root)
    badge, _extra = _status_badge_from_run_status(rs if isinstance(rs, dict) else {})

    rr = Path(run_root).resolve()
    pids_to_signal: List[int] = [int(p) for p in _discover_run_bound_pids(rr) if int(p) > 0]
    # Legacy run-ui pid hint (when present).
    ui_pid = _read_pid_file(run_root / 'ui' / 'pid.txt')
    if int(ui_pid) > 0 and int(ui_pid) not in pids_to_signal:
        pids_to_signal.append(int(ui_pid))
    # run_status pid hint (best-effort).
    if isinstance(rs, dict):
        st_pid = _safe_int(rs.get('pid'), 0)
        if int(st_pid) > 0 and int(st_pid) not in pids_to_signal:
            pids_to_signal.append(int(st_pid))

    if not pids_to_signal:
        if str(badge or "").upper() != "RUNNING":
            raise ValueError(f"unmanaged run not running (status={badge}) and no process found")
        raise ValueError("no process found for unmanaged run")

    for spid in list(pids_to_signal):
        try:
            os.killpg(int(spid), signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception:
            try:
                os.kill(int(spid), signal.SIGTERM)
            except Exception:
                pass

    hard_killed: List[int] = []
    if force:
        time.sleep(0.35)
        for spid in list(pids_to_signal):
            try:
                os.kill(int(spid), 0)
            except Exception:
                continue
            try:
                os.killpg(int(spid), signal.SIGKILL)
                hard_killed.append(int(spid))
            except ProcessLookupError:
                pass
            except Exception:
                try:
                    os.kill(int(spid), signal.SIGKILL)
                    hard_killed.append(int(spid))
                except Exception:
                    pass

    return {
        "status": "stopping_unmanaged",
        "run_key": rk,
        "managed": False,
        "pids_signaled": [int(x) for x in pids_to_signal],
        "force": bool(force),
        "pids_hard_killed": [int(x) for x in hard_killed],
    }


def _resume_run(state: _ServerState, cfg: WebUIConfig, rk: str) -> Dict[str, Any]:
    """Resume semantics: if PLANNED -> start; else create continuation plan then start."""
    rk = normalize_run_key(rk)
    run_root, sim_root = resolve_run_dirs(cfg.runs_root, rk)
    rs, _ = read_run_status(sim_root)
    if _is_planned_run_status(rs):
        return _start_run(state, cfg, rk)
    ck = _latest_checkpoint_entry(sim_root)
    if not ck:
        raise ValueError('no checkpoint available to resume')
    ckpt_gen = _safe_int(ck.get('generation'), -1)
    ckpt_id = str(ck.get('checkpoint_id') or '').strip() or None
    tgt = _safe_int(rs.get('target_generation'), -1) if isinstance(rs, dict) else -1
    tg = int(tgt) if tgt > 0 else None
    plan = create_planned_run(
        runs_root=Path(cfg.runs_root),
        parent_run_key=rk,
        checkpoint_generation=int(ckpt_gen),
        checkpoint_id=str(ckpt_id) if ckpt_id else None,
        run_kind='continuation',
        target_generation=tg,
        label=f'resume__{Path(rk).name}',
        tags=['resume'],
        changesets=None,
        override_determinism_fail=True,
    )
    new_rk = str(plan.get('new_run_key') or '').strip()
    if not new_rk:
        raise RuntimeError('failed to create continuation plan')
    started = _start_run(state, cfg, new_rk)
    started['planned_from'] = rk
    started['new_run_key'] = new_rk
    return started

if __name__ == "__main__":
    raise SystemExit(main())


def _mobile_apk_release_receipt_paths(repo_root: Path, channel: str) -> List[Path]:
    rr = Path(repo_root).resolve()
    safe_channel = str(channel or '').strip().lower()
    base = (rr / 'runtime' / 'state' / 'mobile_apk' / 'releases' / safe_channel / 'receipts').resolve()
    try:
        base.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    return sorted(base.glob('rr_*__*.json'), reverse=True)


def _mobile_apk_release_required_receipt_fields() -> List[str]:
    return [
        "channel_publication_state",
        "validation_scope",
        "proof_gap",
        "proof_gap_reason",
        "artifact_lineage",
        "source_snapshot_identity",
    ]


def _mobile_apk_release_integrity(repo_root: Path, channel: str) -> Dict[str, Any]:
    """Validate release lineage plus validation-scope honesty for a published APK."""

    rr = Path(repo_root).resolve()
    safe_channel = str(channel or '').strip().lower()

    def _clean(value: Any) -> str:
        return " ".join(str(value or "").split()).strip()

    payload: Dict[str, Any] = {
        "schema_version": "mobile_apk_release_integrity_v1",
        "utc": _now_utc_iso(),
        "channel": safe_channel,
        "release_found": False,
        "integrity_ok": False,
        "status": "missing_release",
        "reason": "published release artifact not found",
        "receipt_path": "",
        "receipt_utc": "",
        "mismatch_fields": [],
        "missing_receipt_fields": [],
        "release": {},
        "receipt": {},
        "validation_scope": "",
        "proof_gap": True,
        "proof_gap_reason": "release receipt missing; validation scope is unknown",
        "source_snapshot_identity": "",
        "artifact_lineage": {},
    }
    if safe_channel not in _MOBILE_APK_CHANNEL_META:
        payload.update({"status": "invalid_channel", "reason": "channel must be debug or release"})
        return payload

    release = _mobile_apk_find_release(rr, safe_channel)
    if release is None:
        return payload

    release_path = Path(release.get("path") or "")
    meta_path = _mobile_apk_meta_path(release_path) if release_path else Path("")
    payload["release_found"] = True
    payload["release"] = {
        "channel": _clean(release.get("channel")),
        "apk_name": _clean(release.get("apk_name")),
        "apk_sha256": _clean(release.get("apk_sha256")),
        "version_code": int(release.get("version_code") or 0),
        "version_name": _clean(release.get("version_name")),
        "built_at": _clean(release.get("built_at")),
        "source_path": _clean(release.get("source_path")),
        "meta_sidecar_path": _posix_relpath(meta_path, rr) if meta_path else "",
        "channel_publication_state": "published_local_artifact",
    }

    receipt_paths = _mobile_apk_release_receipt_paths(rr, safe_channel)
    if not receipt_paths:
        payload.update({
            "status": "proof_gap",
            "reason": "release receipt missing; artifact lineage and validation scope are incomplete",
        })
        return payload

    def _receipt_mismatch_fields(receipt: Dict[str, Any]) -> List[str]:
        mismatches: List[str] = []
        expected_pairs = {
            "schema_version": "mobile_apk_release_receipt_v1",
            "channel": _clean(release.get("channel")),
            "apk_name": _clean(release.get("apk_name")),
            "apk_sha256": _clean(release.get("apk_sha256")),
            "version_name": _clean(release.get("version_name")),
            "built_at": _clean(release.get("built_at")),
            "source_path": _clean(release.get("source_path")),
            "channel_publication_state": "published_local_artifact",
        }
        for key, expected in expected_pairs.items():
            actual = _clean(receipt.get(key))
            if actual != expected:
                mismatches.append(key)
        try:
            actual_version_code = int(receipt.get("version_code") or 0)
        except Exception:
            actual_version_code = -1
        if actual_version_code != int(release.get("version_code") or 0):
            mismatches.append("version_code")
        lineage = dict(receipt.get("artifact_lineage") or {})
        expected_lineage = {
            "published_apk_path": _clean(release.get("source_path")),
            "meta_sidecar_path": _posix_relpath(meta_path, rr) if meta_path else "",
        }
        for key, expected in expected_lineage.items():
            actual = _clean(lineage.get(key))
            if actual != expected:
                mismatches.append(f"artifact_lineage.{key}")
        return mismatches

    def _missing_required_fields(receipt: Dict[str, Any], receipt_path: Path) -> List[str]:
        missing: List[str] = []
        for key in _mobile_apk_release_required_receipt_fields():
            if key == "proof_gap":
                if "proof_gap" not in receipt:
                    missing.append(key)
                continue
            value = receipt.get(key)
            if isinstance(value, dict):
                if not value:
                    missing.append(key)
            elif not _clean(value):
                missing.append(key)
        lineage = dict(receipt.get("artifact_lineage") or {})
        expected_receipt_relpath = _posix_relpath(receipt_path, rr)
        if _clean(lineage.get("receipt_relpath")) != expected_receipt_relpath:
            missing.append("artifact_lineage.receipt_relpath")
        if not isinstance(receipt.get("validation_commands", []), list):
            missing.append("validation_commands")
        return sorted(set(missing))

    candidate_receipt: Dict[str, Any] = {}
    candidate_path = Path("")
    candidate_mismatches: List[str] = []
    candidate_missing: List[str] = []
    for receipt_path in receipt_paths:
        receipt = _read_json_object(receipt_path)
        if not receipt:
            continue
        mismatches = _receipt_mismatch_fields(receipt)
        missing = _missing_required_fields(receipt, receipt_path)
        if not candidate_receipt:
            candidate_receipt = dict(receipt)
            candidate_path = receipt_path
            candidate_mismatches = list(mismatches)
            candidate_missing = list(missing)
        if mismatches or missing:
            continue
        proof_gap = bool(receipt.get("proof_gap"))
        proof_gap_reason = _clean(receipt.get("proof_gap_reason"))
        validation_scope = _clean(receipt.get("validation_scope"))
        payload.update(
            {
                "integrity_ok": True,
                "status": "proof_gap" if proof_gap else "ok",
                "reason": (
                    f"artifact lineage agrees, but validation scope is limited: {validation_scope}"
                    if proof_gap
                    else "release artifact, meta sidecar, and durable receipt agree"
                ),
                "receipt_path": str(receipt_path),
                "receipt_utc": _clean(receipt.get("utc")),
                "mismatch_fields": [],
                "missing_receipt_fields": [],
                "receipt": dict(receipt),
                "validation_scope": validation_scope,
                "proof_gap": proof_gap,
                "proof_gap_reason": proof_gap_reason,
                "source_snapshot_identity": _clean(receipt.get("source_snapshot_identity")),
                "artifact_lineage": dict(receipt.get("artifact_lineage") or {}),
            }
        )
        return payload

    payload.update(
        {
            "status": "proof_gap" if candidate_missing else ("receipt_mismatch" if candidate_receipt else "proof_gap"),
            "reason": (
                "release receipt is present but missing required channel, validation-scope, or lineage fields"
                if candidate_missing
                else (
                    "latest receipt does not match the published artifact lineage"
                    if candidate_receipt
                    else "release receipts are missing or unreadable"
                )
            ),
            "receipt_path": str(candidate_path) if candidate_path else "",
            "receipt_utc": _clean(candidate_receipt.get("utc")),
            "mismatch_fields": list(candidate_mismatches),
            "missing_receipt_fields": list(candidate_missing),
            "receipt": dict(candidate_receipt),
            "validation_scope": _clean(candidate_receipt.get("validation_scope")),
            "proof_gap": True,
            "proof_gap_reason": _clean(candidate_receipt.get("proof_gap_reason")) or "receipt_scope_metadata_incomplete",
            "source_snapshot_identity": _clean(candidate_receipt.get("source_snapshot_identity")),
            "artifact_lineage": dict(candidate_receipt.get("artifact_lineage") or {}),
        }
    )
    return payload
