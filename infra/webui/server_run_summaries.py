"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_run_meta import *  # noqa: F403,F401
from infra.webui.server_run_discovery import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Run summaries for overview/matrix
# -----------------------------------------------------------------------------

def _read_run_status_late(sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    fn = globals().get("read_run_status")
    if callable(fn):
        return fn(sim_root)
    from infra.webui import server_run_meta as _run_meta
    return _run_meta.read_run_status(sim_root)


def _read_determinism_status_late(sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    fn = globals().get("read_determinism_status")
    if callable(fn):
        return fn(sim_root)
    from infra.webui import server_run_meta as _run_meta
    return _run_meta.read_determinism_status(sim_root)


def _status_badge_from_run_status_late(obj: Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    fn = globals().get("_status_badge_from_run_status")
    if callable(fn):
        return fn(obj)
    from infra.webui import server_run_meta as _run_meta
    return _run_meta._status_badge_from_run_status(obj)


def _det_badge_from_det_status_late(obj: Optional[Dict[str, Any]]) -> Tuple[str, bool, int]:
    fn = globals().get("_det_badge_from_det_status")
    if callable(fn):
        return fn(obj)
    from infra.webui import server_run_meta as _run_meta
    return _run_meta._det_badge_from_det_status(obj)


def _read_branch_meta_any_late(run_root: Path, sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    fn = globals().get("read_branch_meta_any")
    if callable(fn):
        return fn(run_root, sim_root)
    from infra.webui import server_run_meta as _run_meta
    return _run_meta.read_branch_meta_any(run_root, sim_root)


def _read_checkpoints_index_late(sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    fn = globals().get("read_checkpoints_index")
    if callable(fn):
        return fn(sim_root)
    from infra.webui import server_run_meta as _run_meta
    return _run_meta.read_checkpoints_index(sim_root)


def _mtime_ns_to_utc_iso_late(mtime_ns: int) -> str:
    fn = globals().get("_mtime_ns_to_utc_iso")
    if callable(fn):
        return fn(mtime_ns)
    from infra.webui import server_data_precedence as _precedence
    return _precedence._mtime_ns_to_utc_iso(mtime_ns)


def _safe_int_late(v: Any, default: int = -1) -> int:
    fn = globals().get("_safe_int")
    if callable(fn):
        return fn(v, default)
    from infra.webui import server_run_meta as _run_meta
    fn = getattr(_run_meta, "_safe_int", None)
    if callable(fn):
        return fn(v, default)
    try:
        return int(v)
    except Exception:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return int(default)


def _nonnegative_int_or_none(v: Any) -> Optional[int]:
    try:
        n = int(v)
    except Exception:
        try:
            n = int(float(str(v).strip()))
        except Exception:
            return None
    return n if n >= 0 else None


def _first_nested_nonnegative_int(obj: Any, paths: List[Tuple[str, ...]]) -> Optional[int]:
    if not isinstance(obj, dict):
        return None
    for path in paths:
        cur: Any = obj
        for key in path:
            if not isinstance(cur, dict) or key not in cur:
                cur = None
                break
            cur = cur.get(key)
        n = _nonnegative_int_or_none(cur)
        if n is not None:
            return n
    return None


def _derive_target_generation_from_artifacts(sim_root: Path) -> Optional[int]:
    """Best-effort target generation fallback for runs without run_status.json."""

    manifest = _read_json(sim_root / "manifest.json")
    n = _first_nested_nonnegative_int(
        manifest,
        [
            ("cli_args", "generations"),
            ("config", "sim_cfg", "generations"),
            ("sim_cfg", "generations"),
            ("generations",),
        ],
    )
    if n is not None:
        return n

    config = _read_json(sim_root / "config.json")
    n = _first_nested_nonnegative_int(
        config,
        [
            ("sim_cfg", "generations"),
            ("config", "sim_cfg", "generations"),
            ("generations",),
        ],
    )
    if n is not None:
        return n

    try:
        rows, _src = read_run_config_rows(sim_root, max_rows=2000)
        for row in rows:
            if str(row.get("section") or "").strip() == "sim" and str(row.get("key") or "").strip() == "generations":
                n = _nonnegative_int_or_none(row.get("value"))
                if n is not None:
                    return n
    except Exception:
        pass
    return None


def _derive_current_generation_from_artifacts(sim_root: Path) -> Optional[int]:
    """Best-effort current generation fallback for runs without run_status.json."""

    try:
        src = select_metrics_source(sim_root)
        if src.get("kind") == "csv":
            n = _read_last_generation_from_csv(Path(src.get("path")))
            if n is not None and n >= 0:
                return int(n)
    except Exception:
        pass

    for rel in ("phase_e_e1_latest.json", "epi_v2.json", "av_v2.json"):
        try:
            obj = _read_json(sim_root / rel)
            n = _nonnegative_int_or_none((obj or {}).get("generation"))
            if n is not None:
                return n
        except Exception:
            continue
    return None


def _derive_run_state(run_root: Path, sim_root: Path) -> str:
    """Derive liveness state (RUNNING/HUNG/STOPPED) from pid + heartbeat."""
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


def _summarize_checkpoints(sim_root: Path) -> List[Dict[str, Any]]:
    idx, _src = _read_checkpoints_index_late(sim_root)
    if not idx or str(idx.get("schema_version")) != "checkpoints_index_v1":
        # Best-effort fallback when no index card exists. This is DSIP-safe:
        # read-only scan of known checkpoint directory layouts.
        return _scan_checkpoints_fallback(sim_root)
    cps = idx.get("checkpoints") if isinstance(idx.get("checkpoints"), list) else []
    if not cps:
        # Some runs produce an empty index even when checkpoint folders exist.
        # Best-effort DSIP-safe fallback: scan known checkpoint layouts.
        scanned = _scan_checkpoints_fallback(sim_root)
        if scanned:
            return scanned
        return []

    out: List[Dict[str, Any]] = []
    for c in cps:
        if not isinstance(c, dict):
            continue
        try:
            gen = int(c.get("generation"))
        except Exception:
            continue
        cid = str(c.get("checkpoint_id", "") or "")
        rel = str(c.get("relative_path", "") or "")
        out.append({"generation": gen, "checkpoint_id": cid, "relative_path": rel})
    out.sort(key=lambda d: int(d.get("generation", 0)))
    return out


def _scan_checkpoints_fallback(sim_root: Path) -> List[Dict[str, Any]]:
    """Best-effort scan of checkpoint directories when no checkpoints index is present.

    DSIP-safe: this function is strictly read-only.

    Supported layouts:
    - checkpoint_bundles_v2/bundle_gen000123/
    - checkpoints/ckpt_gen000123/

    Returns a list of dicts compatible with checkpoints_index_v1 entries:
      {generation:int, checkpoint_id:str, relative_path:str}
    """

    out: List[Dict[str, Any]] = []

    # Layout A: checkpoint bundle v2
    bundles = sim_root / "checkpoint_bundles_v2"
    if bundles.exists() and bundles.is_dir() and (not bundles.is_symlink()):
        for d in sorted(bundles.iterdir(), key=lambda p: p.name):
            if (not d.is_dir()) or d.is_symlink():
                continue
            m = re.match(r"bundle_gen(?P<gen>\d{6})$", d.name)
            if not m:
                continue
            try:
                gen = int(m.group("gen"))
            except Exception:
                continue
            meta = _read_json(d / "metadata.json") or {}
            ckpt_id = str(meta.get("checkpoint_id", "") or "")
            if not ckpt_id:
                ckpt_id = d.name
            out.append({"generation": int(gen), "checkpoint_id": ckpt_id, "relative_path": _posix_relpath(d, sim_root)})

    if out:
        out.sort(key=lambda x: int(x.get("generation", 0)))
        return out

    # Layout B: legacy checkpoints directory (Checkpoint v1)
    ck_dir = sim_root / "checkpoints"
    if ck_dir.exists() and ck_dir.is_dir() and (not ck_dir.is_symlink()):
        for d in sorted(ck_dir.iterdir(), key=lambda p: p.name):
            if (not d.is_dir()) or d.is_symlink():
                continue
            m = re.match(r"ckpt_gen(?P<gen>\d{6})$", d.name)
            if not m:
                continue
            try:
                gen = int(m.group("gen"))
            except Exception:
                continue
            # Checkpoint v1 does not provide checkpoint_id consistently.
            # Use folder name as a stable surrogate.
            out.append({"generation": int(gen), "checkpoint_id": d.name, "relative_path": _posix_relpath(d, sim_root)})

    out.sort(key=lambda x: int(x.get("generation", 0)))
    return out


def _load_epiav_cache(state: "_ServerState", sim_root: Path) -> Optional[EpiAvSeriesCache]:
    src = select_epiav_source(sim_root)
    if src.get("kind") == "csv":
        path = Path(src.get("path"))
    else:
        return None

    key = str(path)
    with state.lock:
        cache = state.epiav_caches.get(key)
        if cache is None:
            cache = EpiAvSeriesCache(path)
            state.epiav_caches[key] = cache
    cache.update()
    return cache






def _get_metrics_cache(state: "_ServerState", path: Path) -> MetricsCsvCache:
    """Return a cached MetricsCsvCache for the given path.

    MetricsCsvCache is read-only and uses an incremental tail cursor, so caching is safe and
    prevents redundant header parsing across requests.
    """

    key = str(path)
    with state.lock:
        cache = state.metrics_caches.get(key)
        if cache is None:
            cache = MetricsCsvCache(path)
            state.metrics_caches[key] = cache
    return cache
def _load_metrics_cache(state: "_ServerState", sim_root: Path) -> Optional[MetricsCsvCache]:
    """Load (or create) a MetricsCsvCache for the run, if metrics exist."""
    src = select_metrics_source(sim_root)
    if src.get("kind") != "csv":
        return None
    path = Path(src.get("path"))
    key = str(path)
    with state.lock:
        cache = state.metrics_caches.get(key)
        if cache is None:
            cache = MetricsCsvCache(path)
            state.metrics_caches[key] = cache
    return cache

def _load_applied_cmd_cache(state: "_ServerState", sim_root: Path) -> Optional[JsonlCache]:
    p = select_applied_commands_path(sim_root)
    if p is None:
        return None
    key = str(p)
    with state.lock:
        cache = state.jsonl_caches.get(key)
        if cache is None:
            cache = JsonlCache(p)
            state.jsonl_caches[key] = cache
    cache.update()
    return cache



def _load_autopilot_cache(state: "_ServerState", sim_root: Path) -> Optional[JsonlCache]:
    """Load (or create) a JsonlCache for autopilot decisions, if present."""
    p = select_autopilot_decisions_path(sim_root)
    if p is None:
        return None
    key = str(p)
    with state.lock:
        cache = state.jsonl_caches.get(key)
        if cache is None:
            cache = JsonlCache(p)
            state.jsonl_caches[key] = cache
    cache.update()
    return cache


def _autopilot_generation(obj: Any) -> Optional[int]:
    """Extract the generation index from an autopilot decision record."""
    if not isinstance(obj, dict):
        return None
    for k in ("generation", "gen", "generation_index", "tick_generation"):
        v = obj.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except Exception:
            continue
    return None


def filter_autopilot_items(
    items: List[Dict[str, Any]], *, after_gen: int, before_gen: Optional[int], limit: int
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Filter autopilot decision records with the same paging semantics as applied_commands."""

    def key_fn(it: Dict[str, Any]) -> int:
        g = _autopilot_generation(it)
        return g if g is not None else -1

    items_sorted = sorted(items, key=key_fn)

    if after_gen is None:
        after_gen = -1

    if before_gen is not None:
        items_sorted = [it for it in items_sorted if (key_fn(it) >= 0 and key_fn(it) < before_gen)]
    if after_gen >= 0:
        items_sorted = [it for it in items_sorted if (key_fn(it) > after_gen)]

    if limit and len(items_sorted) > limit:
        items_sorted = items_sorted[-limit:]

    min_g = None
    max_g = None
    if items_sorted:
        gens = [key_fn(it) for it in items_sorted if key_fn(it) >= 0]
        if gens:
            min_g = min(gens)
            max_g = max(gens)

    window = {
        "after_gen": after_gen,
        "before_gen": before_gen,
        "min_generation": min_g,
        "max_generation": max_g,
    }
    return items_sorted, window




def _applied_cmd_generation(obj: Any) -> Optional[int]:
    """Extract a best-effort applied generation from an applied command record.

    Phase 5b / Phase 5d artifacts typically expose `applied_generation`, but this helper is
    intentionally tolerant to minor schema variants so the UI degrades gracefully instead of
    silently failing.
    """
    if not isinstance(obj, dict):
        return None
    for k in ("applied_generation", "applied_gen", "generation", "gen"):
        v = obj.get(k)
        if v is None:
            continue
        try:
            return int(v)
        except Exception:
            try:
                return int(float(str(v).strip()))
            except Exception:
                continue
    return None


def _normalize_command_event_v1_to_applied_cmd(obj: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize ControlPlane command_event_v1 records into applied_cmd_v1 for WebUI.

    The WebUI "Applied Commands" timeline historically consumed applied_cmd_v1 records.
    ControlPlane's authoritative executed log is events/command_events.jsonl with
    command_event_v1 schema.

    This adapter is intentionally best-effort and must not throw: if fields are
    missing, we emit None so the UI can still display something.
    """

    payload = obj.get("command_payload")
    if not isinstance(payload, dict):
        payload = {}

    # Map the executed event to the UI record.
    return {
        "schema_version": "applied_cmd_v1",
        "command_uuid": obj.get("command_uuid"),
        "command_type": payload.get("command_type"),
        "apply_phase": payload.get("apply_phase"),
        "applied_generation": obj.get("generation_applied"),
        "payload_hash": obj.get("command_payload_hash"),
        # UI historically shows ack status; for executed events we label as APPLIED.
        "ack_status": "APPLIED",
        "error_reason": None,
        "timestamp": None,
        "event_id": obj.get("event_id"),
        "source": "events",
    }


def filter_applied_command_items(
    items: List[Dict[str, Any]],
    *,
    after_gen: int = -1,
    before_gen: Optional[int] = None,
    limit: int = 200,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Filter + slice applied commands for gen-paged UI timelines.

    Behavior (deterministic and stable):
    - No gen filters: return the most recent `limit` items (by applied generation).
    - after_gen only: return the next `limit` items with gen > after_gen (ascending).
    - before_gen only: return the previous `limit` items with gen < before_gen (ascending).
    - both: return items with after_gen < gen < before_gen (ascending), truncated to `limit`.

    Items with missing/invalid generations are included only when no gen filters are active.
    """

    lim = int(limit) if int(limit) > 0 else 200
    ag = int(after_gen) if after_gen is not None else -1
    bg = int(before_gen) if before_gen is not None else None

    # Normalize + sort by generation (None goes last).
    norm: List[Tuple[Tuple[int, int], Dict[str, Any]]] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        g = _applied_cmd_generation(it)
        # Sort key: (is_missing, generation)
        sk = (1, 0) if g is None else (0, int(g))
        norm.append((sk, it))
    norm.sort(key=lambda x: x[0])
    sorted_items = [it for _, it in norm]

    # Apply gen filters.
    filtered: List[Dict[str, Any]] = []
    gen_filters_active = (ag is not None and ag >= 0) or (bg is not None)
    for it in sorted_items:
        g = _applied_cmd_generation(it)
        if gen_filters_active and g is None:
            continue
        if ag is not None and ag >= 0 and g is not None and g <= ag:
            continue
        if bg is not None and g is not None and g >= bg:
            continue
        filtered.append(it)

    # Slice for paging.
    if bg is not None and (ag is None or ag < 0):
        # Older page: take the tail (most recent items below before_gen).
        if lim and len(filtered) > lim:
            filtered = filtered[-lim:]
    elif ag is not None and ag >= 0 and bg is None:
        # Newer page: take the head (next items after after_gen).
        if lim and len(filtered) > lim:
            filtered = filtered[:lim]
    else:
        # No filters or both bounds: default to tail so the UI shows "latest" by default.
        if lim and len(filtered) > lim:
            filtered = filtered[-lim:]

    # Window metadata.
    gens = [g for g in (_applied_cmd_generation(it) for it in filtered) if g is not None]
    meta = {
        "after_gen": ag,
        "before_gen": bg,
        "returned": len(filtered),
        "min_generation": (min(gens) if gens else None),
        "max_generation": (max(gens) if gens else None),
        "total_in_cache": len(items),
    }
    return filtered, meta


def build_inbox_queue(state: "_ServerState", sim_root: Path, *, limit: int = 200) -> Dict[str, Any]:
    """Build an inbox queue view from <sim_root>/inbox/ and <sim_root>/inbox_processed/.

    This is a *best-effort* visibility feature for operators.

    It is read-only and DSIP-safe:
    - Ignores *.tmp files
    - Never modifies inbox contents
    - Never follows symlinks

    Notes
    -----
    Some engines move accepted commands into inbox_processed immediately (even if scheduled for a future
    generation). The UI should still surface those as "SCHEDULED" rather than pretending the command
    vanished.
    """

    lim = int(limit) if int(limit) > 0 else 200

    # Current generation is used only for labeling (PENDING vs SCHEDULED vs PROCESSED).
    cur_gen = -1
    try:
        rs, _src = _read_run_status_late(sim_root)
        if rs and rs.get("current_generation") is not None:
            cur_gen = _safe_int_late(rs.get("current_generation"), -1)
    except Exception:
        cur_gen = -1

    # Build an ack_status lookup table (best-effort).
    ack_by_uuid: Dict[str, str] = {}
    cache = _load_applied_cmd_cache(state, sim_root)
    if cache is not None:
        for obj in cache.get_items(limit=5000):
            if not isinstance(obj, dict):
                continue
            cu = str(obj.get("command_uuid", "") or "")
            if not cu:
                continue
            st = str(obj.get("ack_status", "") or "").upper()
            if st:
                ack_by_uuid[cu] = st
    else:
        acks_dir = sim_root / "acks"
        if acks_dir.exists() and acks_dir.is_dir() and (not acks_dir.is_symlink()):
            for p in sorted(acks_dir.glob("ack_*.json"), key=lambda x: x.name):
                if (not p.is_file()) or p.is_symlink():
                    continue
                obj = _read_json(p)
                if not isinstance(obj, dict):
                    continue
                cu = str(obj.get("command_uuid", "") or "")
                if not cu:
                    continue
                st = str(obj.get("status", "") or "").upper()
                if st:
                    ack_by_uuid[cu] = st

    def scan_dir(dir_path: Path, *, location: str) -> Tuple[List[Dict[str, Any]], int]:
        items: List[Dict[str, Any]] = []
        parse_errors = 0

        if not (dir_path.exists() and dir_path.is_dir() and (not dir_path.is_symlink())):
            return items, parse_errors

        for p in sorted(dir_path.glob("*.json"), key=lambda x: x.name):
            if p.is_symlink() or (not p.is_file()):
                continue
            if p.name.endswith(".tmp") or p.name.startswith("."):
                continue

            try:
                st = p.stat()
                size_bytes: Optional[int] = int(st.st_size)
                mtime_ns: Optional[int] = int(st.st_mtime_ns)
            except Exception:
                size_bytes = None
                mtime_ns = None

            obj = _read_json(p)
            if not isinstance(obj, dict):
                parse_errors += 1
                items.append(
                    {
                        "filename": p.name,
                        "location": location,
                        "queue_status": "INVALID",
                        "parse_error": True,
                        "mtime_utc": _mtime_ns_to_utc_iso_late(mtime_ns) if mtime_ns is not None else None,
                        "size_bytes": size_bytes,
                        "command_uuid": None,
                        "command_type": None,
                        "schema_version": None,
                        "target_generation": None,
                        "apply_phase": None,
                        "duration_generations": None,
                        "ack_status": None,
                        "payload": None,
                    }
                )
                continue

            cu = str(obj.get("command_uuid", "") or "") or None
            ctype = str(obj.get("command_type", "") or "") or None
            schema = str(obj.get("schema_version", "") or "") or None
            try:
                tg = int(obj.get("target_generation"))
            except Exception:
                tg = None

            apply_phase: Optional[str] = None
            duration_gens: Optional[int] = None
            ti = obj.get("trait_injection")
            if isinstance(ti, dict):
                ap = str(ti.get("apply_phase", "") or "")
                apply_phase = ap or None
                try:
                    duration_gens = int(ti.get("duration_generations"))
                except Exception:
                    duration_gens = None

            ack = ack_by_uuid.get(cu) if cu else None

            if ack:
                qst = "ACKED"
            elif location == "inbox_processed":
                if tg is not None and cur_gen >= 0 and tg > cur_gen:
                    qst = "SCHEDULED"
                else:
                    qst = "PROCESSED"
            else:
                qst = "PENDING"

            items.append(
                {
                    "filename": p.name,
                    "location": location,
                    "queue_status": qst,
                    "parse_error": False,
                    "mtime_utc": _mtime_ns_to_utc_iso_late(mtime_ns) if mtime_ns is not None else None,
                    "size_bytes": size_bytes,
                    "command_uuid": cu,
                    "command_type": ctype,
                    "schema_version": schema,
                    "target_generation": tg,
                    "apply_phase": apply_phase,
                    "duration_generations": duration_gens,
                    "ack_status": ack,
                    "payload": obj,
                }
            )

        return items, parse_errors

    items_inbox, pe1 = scan_dir(sim_root / "inbox", location="inbox")
    items_proc, pe2 = scan_dir(sim_root / "inbox_processed", location="inbox_processed")

    items = items_inbox + items_proc
    parse_errors = int(pe1) + int(pe2)

    def sort_key(it: Dict[str, Any]) -> Tuple[str, int, int]:
        # Prefer "most recently touched" so operators see what they just submitted.
        # ISO timestamps sort lexicographically.
        mt = str(it.get("mtime_utc") or "")
        tg = it.get("target_generation")
        try:
            g = int(tg) if tg is not None else 10**18
        except Exception:
            g = 10**18
        loc = str(it.get("location") or "")
        return (mt, g, 0 if loc == "inbox" else 1)

    items.sort(key=sort_key)
    if lim and len(items) > lim:
        items = items[-lim:]

    return {
        "schema_version": "inbox_queue_v1",
        "utc": _now_utc_iso(),
        "source": "inbox+inbox_processed",
        "current_generation": int(cur_gen),
        "parse_errors": int(parse_errors),
        "items": items,
    }


def build_run_summary(state: "_ServerState", run_key: str, run_root: Path, sim_root: Path, archived_keys_by_zip=None) -> Dict[str, Any]:
    """Build a run summary dict used by overview + matrix."""
    ui_meta = read_ui_meta(run_root)
    label = ui_meta.get("label")

    status_obj, status_src = _read_run_status_late(sim_root)
    det_obj, det_src = _read_determinism_status_late(sim_root)

    status_badge, status_extra = _status_badge_from_run_status_late(status_obj)
    det_badge, strict_mode, last_verified_gen = _det_badge_from_det_status_late(det_obj)

    cur_gen = _safe_int_late(status_obj.get("current_generation"), -1) if status_obj else -1
    tgt_gen = _safe_int_late(status_obj.get("target_generation"), -1) if status_obj else -1
    if cur_gen < 0:
        derived_cur = _derive_current_generation_from_artifacts(sim_root)
        if derived_cur is not None:
            cur_gen = int(derived_cur)
    if tgt_gen < 0:
        derived_tgt = _derive_target_generation_from_artifacts(sim_root)
        if derived_tgt is not None:
            tgt_gen = int(derived_tgt)

    epi_latest: Optional[float] = None
    av_latest: Optional[float] = None
    epiav_provisional = False
    epiav_src: Optional[str] = None

    epiav_cache = _load_epiav_cache(state, sim_root)
    if epiav_cache is not None:
        latest = epiav_cache.latest()
        epiav_provisional = bool(epiav_cache.cursor.provisional) or (epiav_cache.cursor.parse_errors > 0)
        epiav_src = str(epiav_cache.path)
        if latest:
            epi_latest = latest.get("epi")
            av_latest = latest.get("av")

    # Fallback latest-only JSON if CSV missing.
    if epiav_cache is None:
        src = select_epiav_source(sim_root)
        if src.get("kind") == "json_latest":
            epi = _read_json(Path(src.get("epi_path")))
            av = _read_json(Path(src.get("av_path")))
            if epi and isinstance(epi.get("epi"), (int, float)):
                epi_latest = float(epi.get("epi"))
            if av and isinstance(av.get("av"), (int, float)):
                av_latest = float(av.get("av"))
            epiav_src = "json_latest"

    # Lineage.
    bm_obj, bm_src = _read_branch_meta_any_late(run_root, sim_root)
    branch_id = str(bm_obj.get("branch_id")) if isinstance(bm_obj, dict) and bm_obj.get("branch_id") else None
    parent_branch_id = str(bm_obj.get("parent_branch_id")) if isinstance(bm_obj, dict) and bm_obj.get("parent_branch_id") else None
    root_branch_id = str(bm_obj.get("root_branch_id")) if isinstance(bm_obj, dict) and bm_obj.get("root_branch_id") else None
    branch_depth = int(bm_obj.get("branch_depth")) if isinstance(bm_obj, dict) and bm_obj.get("branch_depth") is not None else 0
    parent_checkpoint_id = str(bm_obj.get("parent_checkpoint_id")) if isinstance(bm_obj, dict) and bm_obj.get("parent_checkpoint_id") else None
    root_checkpoint_id = str(bm_obj.get("root_checkpoint_id")) if isinstance(bm_obj, dict) and bm_obj.get("root_checkpoint_id") else None

    # Branch-from generation is a critical UI affordance (forest + detail view).
    # Phase 5c uses `created_at_generation`; UI spec v0.4 uses `branch_from_generation`.
    branch_from_generation: Optional[int] = None
    if isinstance(bm_obj, dict):
        for k in ("branch_from_generation", "created_at_generation"):
            if bm_obj.get(k) is None:
                continue
            try:
                branch_from_generation = int(bm_obj.get(k))
                break
            except Exception:
                try:
                    branch_from_generation = int(float(str(bm_obj.get(k)).strip()))
                    break
                except Exception:
                    continue


    # Checkpoints summary.
    cps = _summarize_checkpoints(sim_root)

    # Notes excerpt.
    n_excerpt = notes_excerpt(run_root)
    ident = _display_identity(
        canonical_id=normalize_run_key(run_key),
        label=str(label or ""),
        module=str(Path(normalize_run_key(run_key)).name),
        stable_hint=str(Path(normalize_run_key(run_key)).name),
        fallback="run",
    )

    return {
        "schema_version": "run_summary_v1",
        "ui_version": UI_SCHEMA_VERSION,
        "run_key": normalize_run_key(run_key),
        "run_id": Path(normalize_run_key(run_key)).name,
        **ident,
        "label": label,
        "tags": ui_meta.get("tags") if isinstance(ui_meta.get("tags"), list) else [],
        "managed": bool((run_root / "ui" / "webui_managed.json").exists()),
        "managed_origin": "webui" if bool((run_root / "ui" / "webui_managed.json").exists()) else "external",
        "live_state": _derive_run_state(run_root, sim_root),
        "archived_by_zip": bool(archived_keys_by_zip and normalize_run_key(run_key) in archived_keys_by_zip),
        "archived": bool(ui_meta.get("archived", False)) or bool(archived_keys_by_zip and normalize_run_key(run_key) in archived_keys_by_zip),
        "status": status_badge,
        "status_raw": status_extra.get("raw_status"),
        "branch_state": status_extra.get("branch_state"),
        "determinism": det_badge,
        "strict_mode": bool(strict_mode),
        "last_verified_generation": int(last_verified_gen),
        "current_generation": int(cur_gen),
        "target_generation": int(tgt_gen),
        "epi": epi_latest,
        "av": av_latest,
        "epiav_source": epiav_src,
        "epiav_provisional": bool(epiav_provisional),
        "lineage": {
            "branch_id": branch_id,
            "parent_branch_id": parent_branch_id,
            "root_branch_id": root_branch_id,
            "branch_depth": int(branch_depth),
            "parent_checkpoint_id": parent_checkpoint_id,
            "root_checkpoint_id": root_checkpoint_id,
            "branch_from_generation": branch_from_generation,
            # Back-compat alias (Phase 5c naming).
            "created_at_generation": branch_from_generation,
            "source": bm_src,
        },
        "notes_excerpt": n_excerpt,
        "sources": {
            "run_status": status_src,
            "run_determinism": det_src,
        },
        "checkpoints": cps,
    }


def _build_parent_links(runs: List[Dict[str, Any]]) -> None:
    """Mutate run summaries in-place to add parent_run_key when possible."""
    # Build branch_id -> run_key mapping (first seen).
    by_branch: Dict[str, str] = {}
    for r in runs:
        bid = r.get("lineage", {}).get("branch_id")
        if isinstance(bid, str) and bid and bid not in by_branch:
            by_branch[bid] = str(r.get("run_key"))

    for r in runs:
        pbid = r.get("lineage", {}).get("parent_branch_id")
        if isinstance(pbid, str) and pbid:
            r["parent_run_key"] = by_branch.get(pbid)
        else:
            r["parent_run_key"] = None

        # Root lineage grouping.
        rid = r.get("lineage", {}).get("root_branch_id")
        r["root_lineage_id"] = str(rid) if isinstance(rid, str) and rid else str(r.get("run_key"))


def _scheduled_job_matrix_status(job: Dict[str, Any]) -> str:
    projection = job.get("scheduler_projection_v1") if isinstance(job.get("scheduler_projection_v1"), dict) else {}
    state = job.get("state") if isinstance(job.get("state"), dict) else {}
    status = str(job.get("scheduler_status") or projection.get("projected_status") or "").strip().lower()
    reason = str(job.get("scheduler_status_reason") or projection.get("projection_reason") or "").strip().lower()
    last_status = str(state.get("last_status") or "").strip().lower()
    if str(state.get("active_run_id") or "").strip() or status in {"running", "active"}:
        return "RUNNING"
    if status in {"queued", "ready", "runnable_now", "pending"}:
        return "PLANNED"
    if status in {"completed_once", "complete", "completed", "ok", "success"}:
        return "COMPLETE"
    if last_status in {"completed_once", "complete", "completed", "ok", "success"}:
        return "COMPLETE"
    if reason in {"already_executed", "completed_once"}:
        return "COMPLETE"
    if status in {"failed", "blocked", "ambiguous", "cancelled", "cancel_failed", "error"}:
        return "FAILED"
    if last_status in {"failed", "blocked", "ambiguous", "cancelled", "cancel_failed", "error"}:
        return "FAILED"
    if status in {"disabled"}:
        return "INCOMPLETE"
    return "INCOMPLETE"


def _scheduled_job_matrix_rows(*, repo_root: Path) -> List[Dict[str, Any]]:
    try:
        from infra.controlplane import scheduled_jobs as _scheduled_jobs
        jobs = _scheduled_jobs.list_jobs(
            repo_root=repo_root,
            include_context_resolution=False,
            include_reconciliation=False,
        )
    except Exception:
        return []
    rows: List[Dict[str, Any]] = []
    for job in jobs:
        if not isinstance(job, dict):
            continue
        job_id = str(job.get("job_id") or "").strip()
        if not job_id:
            continue
        projection = job.get("scheduler_projection_v1") if isinstance(job.get("scheduler_projection_v1"), dict) else {}
        state = job.get("state") if isinstance(job.get("state"), dict) else {}
        due = job.get("due") if isinstance(job.get("due"), dict) else {}
        scheduler_status = str(job.get("scheduler_status") or projection.get("projected_status") or "").strip()
        reason = str(job.get("scheduler_status_reason") or projection.get("projection_reason") or due.get("reason") or "").strip()
        active_run_id = str(state.get("active_run_id") or "").strip()
        label = str(job.get("display_title") or job_id).strip()
        prompt_preview = str(job.get("prompt_preview") or "").strip()
        notes = " | ".join(
            part
            for part in (
                f"scheduled job: {scheduler_status or 'unknown'}",
                f"reason: {reason}" if reason else "",
                f"active_run_id: {active_run_id}" if active_run_id else "",
                prompt_preview,
            )
            if part
        )
        rows.append(
            {
                "schema_version": "scheduled_job_matrix_row_v1",
                "source_kind": "scheduled_job",
                "run_key": f"scheduled:{job_id}",
                "run_id": job_id,
                "job_id": job_id,
                "canonical_id": job_id,
                "display_id": label,
                "display_suffix": str(job.get("job_kind") or "scheduled"),
                "label": label,
                "tags": [
                    "scheduled-job",
                    str(job.get("job_kind") or "job").strip() or "job",
                    scheduler_status or "unknown",
                ],
                "managed": True,
                "managed_origin": "scheduler",
                "archived": False,
                "archived_by_zip": False,
                "live_state": "RUNNING" if active_run_id else "STOPPED",
                "status": _scheduled_job_matrix_status(job),
                "status_raw": scheduler_status,
                "branch_state": reason,
                "determinism": "UNKNOWN",
                "strict_mode": False,
                "last_verified_generation": -1,
                "current_generation": -1,
                "target_generation": -1,
                "epi": None,
                "av": None,
                "epiav_source": "scheduled_job",
                "epiav_provisional": False,
                "lineage": {
                    "branch_id": None,
                    "parent_branch_id": None,
                    "root_branch_id": None,
                    "branch_depth": 0,
                    "parent_checkpoint_id": None,
                    "root_checkpoint_id": None,
                    "branch_from_generation": None,
                    "created_at_generation": None,
                    "source": "scheduled_jobs",
                },
                "parent_run_key": None,
                "root_lineage_id": f"scheduled:{job_id}",
                "notes_excerpt": notes,
                "sources": {"scheduled_job": job_id},
                "checkpoints": [],
                "scheduler_projection_v1": projection,
                "scheduler_status": scheduler_status,
                "scheduler_status_reason": reason,
                "scheduler_truth_degraded": bool(job.get("scheduler_truth_degraded")),
                "schedule": dict(job.get("schedule") or {}),
                "state": state,
                "due": due,
            }
        )
    return rows


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
