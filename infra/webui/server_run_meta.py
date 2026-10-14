"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_tail_parsers import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
from infra.webui.server_data_precedence import *  # noqa: F403,F401
from infra.webui.server_timeline import *  # noqa: F403,F401
from infra.webui.server_run_discovery import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Run metadata loaders
# -----------------------------------------------------------------------------


def read_run_status(sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read run status index card.

    Returns (obj, source_path_str).
    """
    p = select_run_status_path(sim_root)
    if p is None:
        return None, None
    obj = _read_json(p)
    return obj, str(p)


def read_determinism_status(sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read determinism status index card.

    Returns (obj, source_path_str).
    """
    p = select_determinism_status_path(sim_root)
    if p is None:
        return None, None
    obj = _read_json(p)
    return obj, str(p)


def read_branch_meta_any(run_root: Path, sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read branch_meta.json for lineage.

    Precedence:
    1) <run_root>/ui/branch_meta.json (UI-owned overlay)
    2) <sim_root>/branch_meta.json (Phase 5c tool output)

    Returns (obj, source_path_str).
    """
    p1 = ui_dir(run_root) / "branch_meta.json"
    obj = _read_json(p1)
    if obj is not None:
        return obj, str(p1)
    p2 = sim_root / "branch_meta.json"
    obj2 = _read_json(p2)
    if obj2 is not None:
        return obj2, str(p2)
    return None, None


def read_checkpoints_index(sim_root: Path) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Read checkpoints index (best-effort) with spec precedence.

    IMPORTANT:
      The UI must be able to plan branches/continuations even when the engine
      did not write an index file. In that case we synthesize an in-memory
      checkpoints_index_v1 structure via a DSIP-safe filesystem scan.

    Returns:
      (index_dict_or_None, source_string_or_None)

    Where source_string is either a filesystem path (when an index file exists)
    or the literal "scan" when synthesized.
    """
    p = select_checkpoints_index_path(sim_root)

    # Preferred: read an existing index file.
    if p is not None:
        obj = _read_json(p)
        if isinstance(obj, dict) and str(obj.get("schema_version")) == "checkpoints_index_v1":
            return obj, str(p)
        # If an index exists but is missing/invalid, fall through to scan.

    # Fallback: synthesize a checkpoints_index_v1 view from disk.
    cps = _scan_checkpoints_fallback(sim_root)
    if cps:
        return {"schema_version": "checkpoints_index_v1", "checkpoints": cps}, "scan"

    return None, (str(p) if p is not None else None)


def _scan_checkpoints_fallback(sim_root: Path) -> List[Dict[str, Any]]:
    """Best-effort scan of checkpoint directories when no index is present."""
    out: List[Dict[str, Any]] = []

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
            ckpt_id = str(meta.get("checkpoint_id", "") or "") or d.name
            out.append({"generation": int(gen), "checkpoint_id": ckpt_id, "relative_path": _posix_relpath(d, sim_root)})

    if out:
        out.sort(key=lambda x: int(x.get("generation", 0)))
        return out

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
            out.append({"generation": int(gen), "checkpoint_id": d.name, "relative_path": _posix_relpath(d, sim_root)})

    out.sort(key=lambda x: int(x.get("generation", 0)))
    return out


def _status_badge_from_run_status(obj: Optional[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    """Map run_status.json into UI status badge.

    Returns (status_badge, extra_fields)
    """
    if not obj:
        return "INCOMPLETE", {"reason": "missing run_status"}

    raw_status = str(obj.get("status", "") or "").lower()
    branch_state = str(obj.get("branch_state", "") or "")

    # Primary mapping.
    if raw_status in {"running", "run", "active"} or branch_state == "RUNNING":
        badge = "RUNNING"
    elif raw_status in {"complete", "completed", "done"} or branch_state == "COMPLETE":
        badge = "COMPLETE"
    elif raw_status in {"failed", "error"} or branch_state == "FAILED":
        badge = "FAILED"
    elif raw_status in {"planned"} or branch_state == "PLANNED":
        badge = "PLANNED"
    else:
        badge = "INCOMPLETE"

    # Stalled detection (only meaningful when "running").
    last_ts = obj.get("last_update_timestamp")
    stalled = False
    if badge == "RUNNING" and last_ts:
        t = _parse_utc_iso(str(last_ts))
        if t is not None:
            age_s = time.time() - float(t)
            if age_s > 120.0:
                stalled = True
                badge = "STALLED"

    extra = {
        "raw_status": raw_status,
        "branch_state": branch_state or None,
        "stalled": bool(stalled),
    }
    return badge, extra


def _det_badge_from_det_status(obj: Optional[Dict[str, Any]]) -> Tuple[str, bool, int]:
    """Map run_determinism_status.json into (badge, strict_mode, last_verified_gen)."""
    if not obj:
        return "UNKNOWN", False, -1

    strict_mode = bool(obj.get("strict_mode", False))
    last_result = str(obj.get("phase4_last_result", "UNKNOWN") or "UNKNOWN").upper()
    if last_result not in {"PASS", "FAIL", "UNKNOWN"}:
        last_result = "UNKNOWN"

    try:
        lvg = int(obj.get("last_verified_generation", -1))
    except Exception:
        lvg = -1

    return last_result, strict_mode, lvg


def _safe_int(v: Any, default: int = -1) -> int:
    """Best-effort int conversion.

    Accepts ints, floats, and float-like strings (e.g. '3000.0').
    """
    try:
        return int(v)
    except Exception:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return int(default)


def _compute_baseline_config_hash(sim_root: Path) -> str:
    """Compute baseline_config_hash similar to Phase 5c tool.

    Best-effort:
    - If config.json exists, hash its bytes canonicalized by json dumps.
    - Else return "unknown".
    """
    p = sim_root / "config.json"
    if not p.exists() or not p.is_file():
        return "unknown"
    try:
        raw = p.read_text(encoding="utf-8")
        obj = json.loads(raw)
        txt = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return "sha256:" + hashlib.sha256(txt.encode("utf-8")).hexdigest()
    except Exception:
        try:
            return "sha256:" + _sha256_file(p)
        except Exception:
            return "unknown"


def _detect_trait_apply_phase_frozen(sim_root: Path) -> Optional[str]:
    """Best-effort detection of campaign-frozen trait injection apply phase.

    Source options (best-effort):
    - config.json: look for trait_injection_apply_phase at top-level or under sim_cfg.

    Returns allowed phase string or None if unknown.
    """
    p = sim_root / "config.json"
    if not p.exists() or not p.is_file():
        return None
    try:
        obj = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None

    val: Optional[str] = None
    if isinstance(obj, dict):
        if isinstance(obj.get("trait_injection_apply_phase"), str):
            val = str(obj.get("trait_injection_apply_phase"))
        elif isinstance(obj.get("sim_cfg"), dict) and isinstance(obj["sim_cfg"].get("trait_injection_apply_phase"), str):
            val = str(obj["sim_cfg"].get("trait_injection_apply_phase"))

    if val in {"post-reproduction-pre-selection", "post-selection-pre-mutation"}:
        return val
    return None


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
