"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_tail_parsers import *  # noqa: F403,F401
from infra.webui.server_data_precedence import *  # noqa: F403,F401
from infra.webui.server_run_summaries import *  # noqa: F403,F401
# -----------------------------------------------------------------------------
# Control + Pressure Timeline (Phase 5d viz)
# -----------------------------------------------------------------------------

def _timeline_collect_control_events(state: "_ServerState", sim_root: Path) -> List[Dict[str, Any]]:
    """Collect executed/applied control events for timeline visualization.

    Sources (best-effort):
      1) events/command_events.jsonl (command_event_v1; authoritative executed events)
      2) viz/applied_commands_log.jsonl (legacy applied_cmd_v1)
      3) acks/ fallback is intentionally NOT used here (acks aren't timeline-worthy unless executed)

    Returns:
        List of dicts with at least:
          - generation (int)
          - command_uuid (str|None)
          - command_type (str|None)
          - schema_version (str)
          - raw (dict)  (original record)
    """
    out: List[Dict[str, Any]] = []
    cache = _load_applied_cmd_cache(state, sim_root)
    if cache is None:
        return out

    for it in (cache.items or []):
        if not isinstance(it, dict):
            continue
        sv = str(it.get("schema_version") or "")
        gen: Optional[int] = None
        cmd_type: Optional[str] = None
        cmd_uuid: Optional[str] = None

        if sv == "command_event_v1":
            # ControlPlane executed event log
            gen = _safe_int(it.get("generation_applied"), -1)
            if gen < 0:
                continue
            cmd_uuid = str(it.get("command_uuid") or "") or None
            payload = it.get("command_payload")
            if isinstance(payload, dict):
                cmd_type = str(payload.get("command_type") or "") or None
            out.append(
                {
                    "schema_version": sv,
                    "generation": int(gen),
                    "command_uuid": cmd_uuid,
                    "command_type": cmd_type,
                    "raw": it,
                }
            )
            continue

        # Legacy applied_cmd_v1-ish
        gen2 = _applied_cmd_generation(it)
        if gen2 is None or gen2 < 0:
            continue
        cmd_uuid = str(it.get("command_uuid") or "") or None
        cmd_type = str(it.get("command_type") or "") or None
        out.append(
            {
                "schema_version": sv or "applied_cmd_v1",
                "generation": int(gen2),
                "command_uuid": cmd_uuid,
                "command_type": cmd_type,
                "raw": it,
            }
        )

    # Stable ordering
    out.sort(key=lambda d: (int(d.get("generation", 0)), str(d.get("command_uuid") or "")))
    return out


def _timeline_collect_pressure_points(state: "_ServerState", sim_root: Path) -> List[Dict[str, Any]]:
    """Collect pressure/metric points used in the timeline.

    Current implementation: uses metrics.csv (or viz/metrics_summary.csv) for:
      - shock_active (bool/float)

    Returns:
        List of dicts:
          - generation (int)
          - shock_active (float|None)
    """
    # Prefer full metrics.csv for richer columns; fall back to summary.
    candidates = [
        sim_root / "metrics.csv",
        sim_root / "viz" / "metrics_summary.csv",
    ]
    cache: Optional[MetricsCsvCache] = None
    chosen_path: Optional[Path] = None
    for p in candidates:
        try:
            if p.exists() and p.is_file() and (not p.is_symlink()):
                c = _get_metrics_cache(state, p)
                c.ensure_header()
                # Require generation + at least one pressure-ish column.
                if (c.col_index.get("shock_active") is not None) or (c.col_index.get("shock") is not None):
                    cache = c
                    chosen_path = p
                    break
        except Exception:
            continue

    if cache is None:
        return []

    # Normalize column name: shock_active preferred.
    metric = "shock_active" if "shock_active" in (cache.col_index or {}) else ("shock" if "shock" in (cache.col_index or {}) else "")
    if not metric:
        return []

    try:
        payload = cache.get_points(metric, after_gen=-1, limit=max(1000, int(cache.max_points)))
    except Exception:
        return []

    pts = payload.get("points") if isinstance(payload, dict) else None
    if not isinstance(pts, list):
        return []

    out: List[Dict[str, Any]] = []
    for it in pts:
        if not isinstance(it, dict):
            continue
        g = it.get("generation")
        v = it.get("value")
        try:
            gi = int(g)
        except Exception:
            continue
        vv: Optional[float] = None
        if v is None:
            vv = None
        elif isinstance(v, bool):
            vv = 1.0 if v else 0.0
        elif isinstance(v, (int, float)):
            vv = float(v)
        else:
            try:
                vv = float(str(v).strip())
            except Exception:
                vv = None
        out.append({"generation": int(gi), "shock_active": vv})

    out.sort(key=lambda d: int(d.get("generation", 0)))
    return out


def build_timeline_summary(state: "_ServerState", sim_root: Path, *, bins: int = 200) -> Dict[str, Any]:
    """Build binned timeline summary for the run detail timeline.

    Bins are computed over generation range [0..max_gen] (inclusive).

    Payload shape (stable):
      {
        "schema_version": "timeline_summary_v1",
        "utc": "...",
        "source": { "commands": "...", "metrics": "..." },
        "bins": <int>,
        "gen_min": 0,
        "gen_max": <int>,
        "series": [ { "i":0, "gen_min":0, "gen_max":9, "control_n":1, "shock":true }, ... ],
        "note": "..."
      }
    """
    b = int(bins) if int(bins) > 0 else 200
    b = max(10, min(2000, b))

    control = _timeline_collect_control_events(state, sim_root)
    pressure = _timeline_collect_pressure_points(state, sim_root)

    max_gen = -1
    if control:
        try:
            max_gen = max(max_gen, int(max([c.get("generation", -1) for c in control])))
        except Exception:
            pass
    if pressure:
        try:
            max_gen = max(max_gen, int(max([p.get("generation", -1) for p in pressure])))
        except Exception:
            pass

    # Also consider run_status current_generation if present.
    try:
        rs, _src = read_run_status(sim_root)
        if isinstance(rs, dict) and rs.get("current_generation") is not None:
            cg = _safe_int(rs.get("current_generation"), -1)
            if cg >= 0:
                max_gen = max(max_gen, int(cg))
    except Exception:
        pass

    if max_gen < 0:
        return {
            "schema_version": "timeline_summary_v1",
            "utc": _now_utc_iso(),
            "bins": [],
            "bin_count": int(b),
            "gen_min": 0,
            "gen_max": -1,
            "generation_min": 0,
            "generation_max": -1,
            "series": [],
            "note": "No timeline data (events/metrics missing).",
        }

    span = int(max_gen) + 1
    bin_w = max(1, int(math.ceil(span / float(b))))

    # Pre-fill bins.
    series: List[Dict[str, Any]] = []
    for i in range(b):
        g0 = i * bin_w
        g1 = min(max_gen, (i + 1) * bin_w - 1)
        if g0 > max_gen:
            break
        series.append({"i": int(i), "gen_min": int(g0), "gen_max": int(g1), "control_n": 0, "shock": False})

    def _bin_index(gen: int) -> int:
        return int(gen) // int(bin_w)

    # Accumulate control events.
    for ev in control:
        try:
            g = int(ev.get("generation", -1))
        except Exception:
            continue
        if g < 0:
            continue
        bi = _bin_index(g)
        if 0 <= bi < len(series):
            series[bi]["control_n"] = int(series[bi].get("control_n", 0) or 0) + 1

    # Accumulate shock-active.
    for pt in pressure:
        try:
            g = int(pt.get("generation", -1))
        except Exception:
            continue
        if g < 0:
            continue
        v = pt.get("shock_active")
        shock = False
        if v is None:
            shock = False
        else:
            try:
                shock = float(v) >= 0.5
            except Exception:
                shock = False
        if not shock:
            continue
        bi = _bin_index(g)
        if 0 <= bi < len(series):
            series[bi]["shock"] = True

    # UI compatibility payload:
    # The run-detail timeline canvas expects:
    # - summary.bins: list of bins with control_counts + pressure_flags
    # - summary.generation_max
    # Keep legacy `series` as-is for any older callers.
    bins_payload: List[Dict[str, Any]] = []
    for s in series:
        try:
            control_n = int(s.get("control_n", 0) or 0)
        except Exception:
            control_n = 0
        bins_payload.append(
            {
                "i": int(s.get("i", 0) or 0),
                "gen_min": int(s.get("gen_min", 0) or 0),
                "gen_max": int(s.get("gen_max", 0) or 0),
                "control_counts": {
                    # Current back-end aggregation has only a total count.
                    # Map it into `system` lane so timeline remains informative.
                    "user": 0,
                    "autopilot": 0,
                    "system": int(control_n),
                },
                "pressure_flags": {
                    "shock": bool(s.get("shock", False)),
                    # Not currently derived in summary pass; explicit false for UI contract.
                    "multiplier_change": False,
                },
            }
        )

    # Provide minimal source hints for debugging.
    src_cmd = None
    try:
        p = select_applied_commands_path(sim_root)
        src_cmd = str(p) if p else None
    except Exception:
        src_cmd = None
    src_metrics = None
    try:
        if (sim_root / "metrics.csv").exists():
            src_metrics = str(sim_root / "metrics.csv")
        elif (sim_root / "viz" / "metrics_summary.csv").exists():
            src_metrics = str(sim_root / "viz" / "metrics_summary.csv")
    except Exception:
        src_metrics = None

    return {
        "schema_version": "timeline_summary_v1",
        "utc": _now_utc_iso(),
        "bins": bins_payload,
        "bin_count": int(b),
        "gen_min": 0,
        "gen_max": int(max_gen),
        "generation_min": 0,
        "generation_max": int(max_gen),
        "bin_width": int(bin_w),
        "series": series,
        "sources": {"commands": src_cmd, "metrics": src_metrics},
    }


def build_timeline_range(
    sim_root: Path,
    *,
    gen_min: int,
    gen_max: int,
    kind: str = "control",
    limit: int = 200,
) -> Dict[str, Any]:
    """Return raw timeline payloads for a selected generation window.

    Args:
        sim_root: simulation root.
        gen_min/gen_max: inclusive generation window.
        kind: "control" or "pressure".
        limit: max items returned.

    Returns:
        JSON dict with schema_version and items.
    """
    g0 = int(gen_min)
    g1 = int(gen_max)
    if g1 < g0:
        g0, g1 = g1, g0
    lim = int(limit) if int(limit) > 0 else 200
    lim = max(1, min(5000, lim))

    if kind == "pressure":
        # Read shock_active points in range.
        out_pts: List[Dict[str, Any]] = []
        try:
            # We cannot access server state here; do minimal non-cached read using tail parsers? Nope.
            # So we do a cheap full-file tail approach: use MetricsCsvCache via a temporary cache object.
            # (This endpoint is click-driven, not refresh-driven.)
            p = sim_root / "metrics.csv"
            if not (p.exists() and p.is_file() and (not p.is_symlink())):
                p = sim_root / "viz" / "metrics_summary.csv"
            if p.exists() and p.is_file() and (not p.is_symlink()):
                tmp_cache = MetricsCsvCache(p, max_points=200000)
                # Ensure header then fetch all points (bounded).
                tmp_cache.ensure_header()
                metric = "shock_active" if "shock_active" in (tmp_cache.col_index or {}) else ("shock" if "shock" in (tmp_cache.col_index or {}) else "")
                if metric:
                    payload = tmp_cache.get_points(metric, after_gen=-1, limit=200000)
                    pts = payload.get("points") if isinstance(payload, dict) else []
                    for it in pts:
                        if not isinstance(it, dict):
                            continue
                        try:
                            g = int(it.get("generation"))
                        except Exception:
                            continue
                        if g < g0 or g > g1:
                            continue
                        v = it.get("value")
                        vv = None
                        if isinstance(v, bool):
                            vv = 1.0 if v else 0.0
                        elif isinstance(v, (int, float)):
                            vv = float(v)
                        else:
                            try:
                                vv = float(str(v).strip())
                            except Exception:
                                vv = None
                        out_pts.append({"generation": int(g), "shock_active": vv})
        except Exception:
            out_pts = []

        if len(out_pts) > lim:
            out_pts = out_pts[-lim:]

        return {
            "schema_version": "timeline_range_v1",
            "utc": _now_utc_iso(),
            "kind": "pressure",
            "gen_min": int(g0),
            "gen_max": int(g1),
            "items": out_pts,
        }

    # kind == control (default): return executed command payloads in range.
    items: List[Dict[str, Any]] = []
    p = select_applied_commands_path(sim_root)
    if p is None or (not p.exists()) or (not p.is_file()) or p.is_symlink():
        return {
            "schema_version": "timeline_range_v1",
            "utc": _now_utc_iso(),
            "kind": "control",
            "gen_min": int(g0),
            "gen_max": int(g1),
            "items": [],
            "note": "command events log not found",
        }

    try:
        # Click-driven endpoint: line-scan the file once (bounded by limit + some slop).
        # This avoids needing server state plumbed into build_timeline_range.
        # We scan from the tail to bias toward recent events.
        lines = tail_lines(p, max_lines=max(2000, lim * 5), max_bytes=5 * 1024 * 1024)
        for line in lines:
            s = (line or "").strip()
            if not s:
                continue
            try:
                obj = json.loads(s)
            except Exception:
                continue
            if not isinstance(obj, dict):
                continue

            sv = str(obj.get("schema_version") or "")
            gen: Optional[int] = None

            if sv == "command_event_v1":
                gen = _safe_int(obj.get("generation_applied"), -1)
                if gen < 0:
                    continue
                if gen < g0 or gen > g1:
                    continue
                payload = obj.get("command_payload") if isinstance(obj.get("command_payload"), dict) else {}
                items.append(
                    {
                        "generation": int(gen),
                        "command_uuid": obj.get("command_uuid"),
                        "command_type": payload.get("command_type"),
                        "payload": payload,
                        "event": obj,
                        "source": "events",
                    }
                )
                continue

            gen2 = _applied_cmd_generation(obj)
            if gen2 is None or gen2 < 0:
                continue
            if gen2 < g0 or gen2 > g1:
                continue
            items.append(
                {
                    "generation": int(gen2),
                    "command_uuid": obj.get("command_uuid"),
                    "command_type": obj.get("command_type"),
                    "payload": obj,
                    "source": "applied",
                }
            )

        items.sort(key=lambda d: (int(d.get("generation", 0)), str(d.get("command_uuid") or "")))
        if len(items) > lim:
            items = items[-lim:]
    except Exception:
        items = []

    return {
        "schema_version": "timeline_range_v1",
        "utc": _now_utc_iso(),
        "kind": "control",
        "gen_min": int(g0),
        "gen_max": int(g1),
        "items": items,
        "source": str(p),
    }


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
