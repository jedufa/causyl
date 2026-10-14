"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_ui_metadata import *  # noqa: F403,F401
from infra.webui.server_run_discovery import _read_first_line  # noqa: F401
_CANON_METRICS_HEADER: Optional[List[str]] = None


def _load_canon_metrics_header() -> List[str]:
    global _CANON_METRICS_HEADER
    if isinstance(_CANON_METRICS_HEADER, list):
        return list(_CANON_METRICS_HEADER)
    out: List[str] = []
    try:
        rows_py = _repo_root_from_webui() / "engine" / "simulation" / "metrics_pkg" / "rows.py"
        txt = rows_py.read_text(encoding="utf-8")
        in_cls = False
        for ln in txt.splitlines():
            if not in_cls:
                if re.match(r"^\s*class\s+MetricsRow\s*:", ln):
                    in_cls = True
                continue
            if re.match(r"^\S", ln):
                break
            m = re.match(r"^\s+([A-Za-z_][A-Za-z0-9_]*)\s*:\s*", ln)
            if m:
                out.append(str(m.group(1)))
    except Exception:
        out = []
    _CANON_METRICS_HEADER = list(out)
    return list(out)
# -----------------------------------------------------------------------------
# Data precedence helpers
# -----------------------------------------------------------------------------


def _pick_first_existing(paths: List[Path]) -> Optional[Path]:
    for p in paths:
        try:
            if p.exists() and p.is_file() and (not p.name.endswith(".tmp")):
                if p.is_symlink():
                    continue
                return p
        except Exception:
            continue
    return None


def _mtime_ns_to_utc_iso(mtime_ns: int) -> str:
    """Convert mtime nanoseconds to UTC ISO-8601 string (seconds resolution)."""
    import datetime as _dt

    try:
        return (
            _dt.datetime.fromtimestamp(float(mtime_ns) / 1e9, tz=_dt.timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z")
        )
    except Exception:
        return _now_utc_iso()


def _safe_int(v: Any, default: int = -1) -> int:
    """Best-effort int conversion with float-string fallback."""
    try:
        return int(v)
    except Exception:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return int(default)


def select_run_status_path(sim_root: Path) -> Optional[Path]:
    """Select run status index card path (precedence per UI Spec v0.4)."""
    return _pick_first_existing([sim_root / "viz" / "run_status.json", sim_root / "run_status.json"])


def select_determinism_status_path(sim_root: Path) -> Optional[Path]:
    """Select determinism status index card path (precedence per UI Spec v0.4)."""
    return _pick_first_existing([sim_root / "viz" / "run_determinism_status.json", sim_root / "run_determinism_status.json"])


def select_checkpoints_index_path(sim_root: Path) -> Optional[Path]:
    """Select checkpoints index path (precedence per UI Spec v0.4)."""
    return _pick_first_existing([sim_root / "viz" / "checkpoints_index.json", sim_root / "checkpoints" / "index.json"])


def select_epiav_source(sim_root: Path) -> Dict[str, Any]:
    """Select EPI/AV source files (precedence per UI Spec v0.4)."""
    def _csv_has_at_least_n_data_rows(path: Path, n: int) -> bool:
        """Return True if CSV has at least `n` non-header data rows.

        Uses a bounded read for low overhead on large, append-only files.
        """
        try:
            needed = max(1, int(n))
        except Exception:
            needed = 1
        try:
            count = 0
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                # Skip header.
                _ = f.readline()
                for line in f:
                    if not line.strip():
                        continue
                    count += 1
                    if count >= needed:
                        return True
        except Exception:
            return False
        return False

    p1 = sim_root / "viz" / "epi_av_summary.csv"
    if p1.exists() and p1.is_file():
        # Early in runs, summary cadence can yield only one point, which is
        # visually ambiguous on line charts. If timeseries is available with
        # richer data, prefer it until summary has at least two rows.
        p2 = sim_root / "epi_av_timeseries.csv"
        if p2.exists() and p2.is_file() and (not _csv_has_at_least_n_data_rows(p1, 2)):
            return {"kind": "csv", "path": str(p2)}
        return {"kind": "csv", "path": str(p1)}
    p2 = sim_root / "epi_av_timeseries.csv"
    if p2.exists() and p2.is_file():
        return {"kind": "csv", "path": str(p2)}
    p3 = sim_root / "epi_v2.json"
    p4 = sim_root / "av_v2.json"
    if p3.exists() and p3.is_file() and p4.exists() and p4.is_file():
        return {"kind": "json_latest", "epi_path": str(p3), "av_path": str(p4)}
    return {"kind": "missing"}




def select_metrics_source(sim_root: Path) -> Dict[str, Any]:
    """Select metrics CSV source for optional UI plotting.

    This is not part of the Tier-1 functional contract, but is used for
    operator convenience (population/fitness/knob tracking) without touching
    determinism-critical artifacts.

    Precedence (best-effort):
      1) <run>/viz/metrics_summary.csv (if present)
      2) <run>/metrics.csv
    """
    p = _pick_first_existing([sim_root / "viz" / "metrics_summary.csv", sim_root / "metrics.csv"])
    if p is None:
        return {"kind": "missing"}
    return {"kind": "csv", "path": str(p)}

def select_applied_commands_path(sim_root: Path) -> Optional[Path]:
    """Select applied commands log path (preferred).

    Precedence (best-effort):
      1) <run>/events/command_events.jsonl (authoritative for executed commands)
      2) <run>/viz/applied_commands_log.jsonl (legacy UI log)

    Notes:
    - Prefer the events log only if it exists AND is non-empty.
      This avoids selecting an empty file and hiding legacy viz logs.
    - This selection must be deterministic and must not throw.
    """

    # Prefer events/command_events.jsonl if present and non-empty.
    try:
        p_events = sim_root / "events" / "command_events.jsonl"
        if p_events.exists() and p_events.is_file() and (not p_events.is_symlink()):
            if int(p_events.stat().st_size) > 0:
                return p_events
    except Exception:
        pass

    # Fallback to legacy viz/applied_commands_log.jsonl.
    return _pick_first_existing([sim_root / "viz" / "applied_commands_log.jsonl"])


def select_autopilot_decisions_path(sim_root: Path) -> Optional[Path]:
    """Return the autopilot decisions JSONL path, if present.

    Autopilot is per-run and writes:
      <sim_root>/autopilot/pressure_autopilot_decisions.jsonl
    """
    return _pick_first_existing([sim_root / "autopilot" / "pressure_autopilot_decisions.jsonl"])





def select_run_config_csv_path(sim_root: Path) -> Optional[Path]:
    """Select run configuration ("knobs") CSV if present."""
    return _pick_first_existing([sim_root / "run_config.csv"])


def read_run_config_rows(sim_root: Path, *, max_rows: int = 5000) -> Tuple[List[Dict[str, str]], Optional[str]]:
    """Read run_config.csv as a list of {section,key,value} dicts (best-effort)."""
    p = select_run_config_csv_path(sim_root)
    if p is None:
        return [], None
    rows: List[Dict[str, str]] = []
    try:
        with open(p, "r", encoding="utf-8", errors="replace", newline="") as f:
            rdr = csv.DictReader(f)
            for i, row in enumerate(rdr):
                if i >= int(max_rows):
                    break
                rows.append(
                    {
                        "section": str(row.get("section", "")),
                        "key": str(row.get("key", "")),
                        "value": str(row.get("value", "")),
                    }
                )
    except Exception:
        return [], str(p)
    return rows, str(p)


_TRAIT_MEAN_COL_RE = re.compile(r"^gen_(?P<trait>.+)_mean$")


def build_trait_catalog_from_metrics(metrics_path: Path) -> Dict[str, Any]:
    """Build a trait catalog from metrics.csv style files.

    This is UI convenience for the command wizard. It uses the latest complete
    metrics row to show current mean/std values for gen_*_mean columns.
    """
    p = Path(metrics_path)
    if (not p.exists()) or (not p.is_file()):
        return {"traits": [], "generation": None, "source": str(p), "provisional": False}

    header_line = _read_first_line(p)
    if not header_line:
        return {"traits": [], "generation": None, "source": str(p), "provisional": False}

    try:
        header = [str(c).strip() for c in next(csv.reader([header_line]))]
    except Exception:
        return {"traits": [], "generation": None, "source": str(p), "provisional": False}

    # Headerless metrics.csv compatibility: if first line is data and column
    # count matches canonical MetricsRow schema, recover canonical header names.
    if (
        (not any(c == "generation" for c in header))
        and (not any(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", c or "") for c in header))
        and _load_canon_metrics_header()
        and len(header) == len(_load_canon_metrics_header())
    ):
        header = list(_load_canon_metrics_header())

    col_index = {c: i for i, c in enumerate(header) if c}
    gen_idx = col_index.get("generation")

    # Identify trait mean columns.
    trait_mean_cols: Dict[str, str] = {}
    trait_std_cols: Dict[str, str] = {}
    for col in header:
        m = _TRAIT_MEAN_COL_RE.match(col)
        if not m:
            continue
        trait = m.group("trait")
        trait_mean_cols[trait] = col
        std_col = f"gen_{trait}_std"
        if std_col in col_index:
            trait_std_cols[trait] = std_col

    # Read last few lines and pick the last parseable (complete) row.
    tail = tail_lines(p, max_lines=8, max_bytes=256 * 1024)
    data_row: Optional[List[str]] = None
    provisional = False
    for line in reversed(tail):
        if not line or line.startswith("generation,"):
            continue
        try:
            row = next(csv.reader([line]))
        except Exception:
            provisional = True
            continue
        if gen_idx is None or gen_idx >= len(row):
            provisional = True
            continue
        # Require at least some columns.
        data_row = row
        break

    generation: Optional[int] = None
    if data_row is not None and gen_idx is not None:
        g = _safe_int(data_row[gen_idx], -1)
        generation = int(g) if (isinstance(g, int) and g >= 0) else None

    def pf(x: Optional[str]) -> Optional[float]:
        if x is None:
            return None
        t = str(x).strip()
        if not t or t.lower() in {"null", "nan"}:
            return None
        try:
            return float(t)
        except Exception:
            return None

    traits_out: List[Dict[str, Any]] = []
    if data_row is not None:
        for trait, mean_col in trait_mean_cols.items():
            mi = col_index.get(mean_col)
            si = col_index.get(trait_std_cols.get(trait, "")) if trait_std_cols.get(trait) else None
            mean_val = pf(data_row[mi]) if (mi is not None and mi < len(data_row)) else None
            std_val = pf(data_row[si]) if (si is not None and si < len(data_row)) else None
            traits_out.append(
                {
                    "locus_tag": trait,
                    "mean": mean_val,
                    "std": std_val,
                    "mean_col": mean_col,
                }
            )

    traits_out.sort(key=lambda d: str(d.get("locus_tag") or ""))
    return {
        "traits": traits_out,
        "generation": generation,
        "source": str(p),
        "provisional": provisional,
    }


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
