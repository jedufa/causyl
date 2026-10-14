"""WebUI server module (extracted from server.py to keep things maintainable)."""

from __future__ import annotations

from infra.webui.server_common import *  # noqa: F403,F401
from infra.webui.server_data_precedence import *  # noqa: F403,F401
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
# Incremental tail parsers (no full rescans per refresh)
# -----------------------------------------------------------------------------


class _IncrementalFileCursor:
    """Track incremental reads of an append-only file.

    Attributes
    ----------
    offset:
        Byte offset already consumed.
    size:
        Last observed file size.
    mtime_ns:
        Last observed mtime in ns.
    remainder:
        Trailing bytes that did not end with a newline (partial line).
    provisional:
        True if the last update observed a partial trailing line.
    parse_errors:
        Count of parse errors observed.
    """

    def __init__(self) -> None:
        self.offset: int = 0
        self.size: int = 0
        self.mtime_ns: int = 0
        self.remainder: bytes = b""
        self.provisional: bool = False
        self.parse_errors: int = 0


class EpiAvSeriesCache:
    """Incremental cache for epi_av_summary.csv style files."""

    def __init__(self, path: Path, *, max_points: int = 20000) -> None:
        self.path = Path(path)
        self.max_points = int(max_points)
        self.cursor = _IncrementalFileCursor()
        self.header: Optional[List[str]] = None
        self.gen_idx: Optional[int] = None
        self.epi_idx: Optional[int] = None
        self.av_idx: Optional[int] = None
        self.gens: List[int] = []
        self.points: List[Tuple[int, Optional[float], Optional[float]]] = []
        self.last_error: Optional[str] = None

    def _reset(self) -> None:
        self.cursor = _IncrementalFileCursor()
        self.header = None
        self.gen_idx = None
        self.epi_idx = None
        self.av_idx = None
        self.gens = []
        self.points = []
        self.last_error = None

    @staticmethod
    def _parse_float(v: str) -> Optional[float]:
        s = (v or "").strip()
        if s == "" or s.lower() in {"null", "none", "nan"}:
            return None
        try:
            return float(s)
        except Exception:
            return None

    def _ingest_line(self, line: bytes) -> None:
        # Decode per-line. This does not rewrite the file.
        try:
            txt = line.decode("utf-8", errors="replace").strip("\r\n")
        except Exception:
            self.cursor.parse_errors += 1
            return

        if not txt:
            return

        # Header detection.
        if self.header is None:
            cols = [c.strip() for c in txt.split(",")]
            if "generation" in cols:
                self.header = cols
                self.gen_idx = cols.index("generation")
                # Accept canonical header (generation,epi,av) and common alternates
                self.epi_idx = cols.index("epi") if "epi" in cols else (cols.index("epi_value") if "epi_value" in cols else None)
                self.av_idx  = cols.index("av")  if "av" in cols else (cols.index("av_value")  if "av_value" in cols else None)
                return
            # No header: treat as corrupt.
            self.cursor.parse_errors += 1
            self.last_error = "missing header"
            return

        # Skip duplicate header.
        if txt.startswith("generation,"):
            return

        parts = [p.strip() for p in txt.split(",")]
        if self.gen_idx is None or len(parts) <= self.gen_idx:
            self.cursor.parse_errors += 1
            self.last_error = "row missing generation"
            return

        try:
            gen = int(float(parts[self.gen_idx]))
        except Exception:
            self.cursor.parse_errors += 1
            self.last_error = "invalid generation"
            return

        epi: Optional[float] = None
        av: Optional[float] = None
        if self.epi_idx is not None and len(parts) > self.epi_idx:
            epi = self._parse_float(parts[self.epi_idx])
        if self.av_idx is not None and len(parts) > self.av_idx:
            av = self._parse_float(parts[self.av_idx])

        # Enforce monotonic-ish gens (append-only). If out of order, still accept.
        self.gens.append(int(gen))
        self.points.append((int(gen), epi, av))

        # Bound memory.
        if len(self.points) > self.max_points:
            drop = len(self.points) - self.max_points
            self.points = self.points[drop:]
            self.gens = self.gens[drop:]

    def update(self) -> None:
        """Parse newly appended bytes (if any)."""
        p = self.path
        if not p.exists() or not p.is_file():
            return
        if p.is_symlink():
            return

        try:
            st = p.stat()
        except Exception:
            return

        size = int(st.st_size)
        mtime_ns = int(st.st_mtime_ns)

        # Detect truncation or replacement.
        if size < self.cursor.offset or mtime_ns < self.cursor.mtime_ns:
            self._reset()

        # No change.
        if size == self.cursor.size and mtime_ns == self.cursor.mtime_ns:
            return

        # Read appended bytes.
        try:
            with open(p, "rb") as f:
                f.seek(self.cursor.offset)
                data = f.read()
        except Exception as e:
            self.last_error = str(e)
            return

        self.cursor.size = size
        self.cursor.mtime_ns = mtime_ns
        self.cursor.offset = size

        if not data:
            return

        blob = self.cursor.remainder + data

        # Split into complete lines, keeping trailing remainder if not newline-terminated.
        last_nl = blob.rfind(b"\n")
        complete = blob
        remainder = b""
        if last_nl != -1 and last_nl < len(blob) - 1:
            complete = blob[: last_nl + 1]
            remainder = blob[last_nl + 1 :]
        elif last_nl == -1:
            complete = b""
            remainder = blob

        self.cursor.remainder = remainder
        self.cursor.provisional = bool(remainder)

        for line in complete.splitlines(keepends=False):
            self._ingest_line(line)

    def latest(self) -> Optional[Dict[str, Any]]:
        """Return latest point, if any."""
        if not self.points:
            return None
        g, e, a = self.points[-1]
        return {"generation": int(g), "epi": e, "av": a}

    def get_points(self, *, after_gen: int = -1, limit: int = 1000) -> List[Dict[str, Any]]:
        """Return points with generation > after_gen, capped to limit."""
        if not self.points:
            return []
        try:
            ag = int(after_gen)
        except Exception:
            ag = -1

        # Find first index where gen > after_gen.
        idx = bisect.bisect_right(self.gens, ag)
        out = self.points[idx:]
        if limit and len(out) > int(limit):
            out = out[-int(limit) :]
        return [{"generation": int(g), "epi": e, "av": a} for (g, e, a) in out]


class MetricsCsvCache:
    """Incremental cache for metrics-style CSV files.

    Goals
    -----
    - Avoid full rescans per refresh (incremental tail parsing).
    - Track only requested metric columns (plus generation).
    - Tolerate truncated/partial trailing lines.

    Notes
    -----
    - This cache is UI-only; it never writes to engine output files.
    - It is best-effort: parse errors are counted and skipped.
    """

    def __init__(
        self,
        path: Path,
        *,
        max_points: int = 20000,
        bootstrap_max_bytes: int = 64 * 1024 * 1024,
    ) -> None:
        self.path = Path(path)
        self.max_points = int(max_points)
        self.bootstrap_max_bytes = int(bootstrap_max_bytes)
        self.cursor = _IncrementalFileCursor()

        # Header/schema
        self.header: Optional[List[str]] = None
        self.col_index: Dict[str, int] = {}
        self.gen_idx: Optional[int] = None

        # Tracked series
        self.tracked: set[str] = set()
        self.gens: List[int] = []
        self.series: Dict[str, List[Optional[float]]] = {}

        self.last_error: Optional[str] = None
        self.bootstrapped: bool = False

    def _reset(self) -> None:
        tracked = set(self.tracked)
        self.cursor = _IncrementalFileCursor()
        self.header = None
        self.col_index = {}
        self.gen_idx = None
        self.gens = []
        self.series = {m: [] for m in tracked}
        self.tracked = tracked
        self.last_error = None
        self.bootstrapped = False

    def ensure_header(self) -> None:
        if self.header is not None:
            return
        try:
            header_line = _read_first_line(self.path)
        except Exception:
            header_line = ""
        if not header_line:
            self.last_error = "missing header"
            return
        try:
            row = next(csv.reader([header_line]))
            cols = [str(c).strip() for c in row]
        except Exception as e:
            self.last_error = f"header parse error: {e}"
            self.cursor.parse_errors += 1
            return

        # Defend against headerless files where the first data row is numeric
        # (e.g. "0,10,0.5"), which would otherwise leak numeric "column names"
        # into the UI metric picker.
        if (not any(c == "generation" for c in cols)) and (not any(self._looks_like_header_token(c) for c in cols)):
            # Recovery path: if column count matches canonical MetricsRow, use
            # canonical schema names so WebUI remains functional on headerless
            # metrics.csv produced by legacy/partial writers.
            canon = _load_canon_metrics_header()
            if canon and len(cols) == len(canon):
                cols = list(canon)
            else:
                self.last_error = "missing header"
                self.cursor.parse_errors += 1
                return

        self.header = cols
        self.col_index = {c: i for i, c in enumerate(cols) if c}
        self.gen_idx = self.col_index.get("generation")
        if self.gen_idx is None:
            # Fallback to first column if present.
            self.gen_idx = 0 if cols else None
            if self.gen_idx is None:
                self.last_error = "missing generation column"

    @staticmethod
    def _looks_like_header_token(token: str) -> bool:
        t = str(token or "").strip()
        if not t:
            return False
        # Metrics columns are identifier-like; numeric first rows are data, not headers.
        return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", t))

    def schema(self) -> Dict[str, Any]:
        self.ensure_header()
        return {
            "header": list(self.header or []),
            "generation_column": "generation" if (self.col_index.get("generation") is not None) else None,
        }

    def ensure_metric(self, metric: str) -> str:
        m = str(metric or "").strip()
        if not m:
            raise ValueError("metric is required")
        self.ensure_header()
        if not self.col_index:
            raise ValueError("metrics header missing")
        if m not in self.col_index:
            raise ValueError(f"metric not found: {m}")
        if m not in self.tracked:
            self.tracked.add(m)
            # Rebuild tail so history includes this column.
            self.bootstrap()
        elif not self.bootstrapped:
            self.bootstrap()
        return m

    @staticmethod
    def _parse_float(s: str) -> Optional[float]:
        t = str(s).strip()
        if not t:
            return None
        tl = t.lower()
        if tl in {"null", "nan", "none"}:
            return None
        # Allow boolean-ish metrics for plotting (e.g. shock_active).
        if tl in {"true", "t", "yes", "y"}:
            return 1.0
        if tl in {"false", "f", "no", "n"}:
            return 0.0
        try:
            return float(t)
        except Exception:
            return None

    def _truncate_to_max(self) -> None:
        extra = len(self.gens) - self.max_points
        if extra <= 0:
            return
        self.gens = self.gens[extra:]
        for m in list(self.series.keys()):
            self.series[m] = self.series[m][extra:]

    def _ingest_text_line(self, line: str, *, truncate: bool = True) -> None:
        txt = str(line).strip()
        if not txt:
            return
        # Skip header repeats.
        if txt.startswith("generation,"):
            return

        if self.gen_idx is None:
            self.cursor.parse_errors += 1
            return

        try:
            row = next(csv.reader([txt]))
        except Exception:
            self.cursor.parse_errors += 1
            return

        if self.gen_idx >= len(row):
            self.cursor.parse_errors += 1
            return

        gen = _safe_int(row[self.gen_idx], -1)
        if (not isinstance(gen, int)) or gen < 0:
            self.cursor.parse_errors += 1
            return

        self.gens.append(int(gen))
        for m in self.tracked:
            idx = self.col_index.get(m)
            val: Optional[float] = None
            if idx is not None and idx < len(row):
                val = self._parse_float(row[idx])
            self.series.setdefault(m, []).append(val)

        if truncate:
            self._truncate_to_max()

    def bootstrap(self) -> None:
        """(Re)build in-memory history from a tail read.

        This is invoked when:
        - the cache is first used for a metric
        - a new metric column is requested
        - the file is detected as truncated/rotated
        """
        self.ensure_header()
        if self.gen_idx is None:
            self.bootstrapped = True
            return

        try:
            st = self.path.stat()
        except Exception as e:
            self.last_error = f"stat failed: {e}"
            return

        self.gens = []
        self.series = {m: [] for m in self.tracked}

        # Read tail lines. This avoids full rescans of large metrics.csv files.
        lines = tail_lines(
            self.path,
            max_lines=self.max_points + 10,
            max_bytes=self.bootstrap_max_bytes,
        )
        for line in lines:
            if not line:
                continue
            if line.startswith("generation,"):
                continue
            self._ingest_text_line(line, truncate=False)

        self._truncate_to_max()

        # Set cursor to end-of-file for incremental updates.
        self.cursor.offset = st.st_size
        self.cursor.size = st.st_size
        self.cursor.mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
        self.cursor.remainder = b""
        self.cursor.provisional = False
        self.bootstrapped = True

    def update(self) -> None:
        """Read appended bytes since last cursor offset and ingest new rows."""
        self.ensure_header()
        if not self.path.exists() or not self.path.is_file():
            return

        # If we have tracked metrics but no bootstrap history, build it.
        if self.tracked and not self.bootstrapped:
            self.bootstrap()

        try:
            st = self.path.stat()
        except Exception:
            return

        mtime_ns = getattr(st, "st_mtime_ns", int(st.st_mtime * 1e9))
        if st.st_size < self.cursor.offset or mtime_ns < self.cursor.mtime_ns:
            # Truncated/rotated.
            self._reset()
            if self.tracked:
                self.bootstrap()
            return

        if st.st_size == self.cursor.offset and mtime_ns == self.cursor.mtime_ns:
            return

        try:
            with open(self.path, "rb") as f:
                f.seek(self.cursor.offset)
                data = f.read()
        except Exception:
            return

        blob = self.cursor.remainder + data
        if not blob:
            self.cursor.offset = st.st_size
            self.cursor.size = st.st_size
            self.cursor.mtime_ns = mtime_ns
            return

        last_nl = blob.rfind(b"\n")
        if last_nl == -1:
            # Still a partial line.
            self.cursor.remainder = blob
            self.cursor.provisional = True
            self.cursor.offset = st.st_size
            self.cursor.size = st.st_size
            self.cursor.mtime_ns = mtime_ns
            return

        complete = blob[: last_nl + 1]
        remainder = blob[last_nl + 1 :]

        self.cursor.remainder = remainder
        self.cursor.provisional = len(remainder) > 0
        self.cursor.offset = st.st_size
        self.cursor.size = st.st_size
        self.cursor.mtime_ns = mtime_ns

        for raw in complete.splitlines():
            if not raw:
                continue
            try:
                txt = raw.decode("utf-8", errors="replace")
            except Exception:
                self.cursor.parse_errors += 1
                continue
            self._ingest_text_line(txt)

    def get_points(self, metric: str, *, after_gen: int = -1, limit: int = 1000) -> Dict[str, Any]:
        """Return metric points with generation > after_gen (best-effort)."""
        m = self.ensure_metric(metric)
        self.update()

        vals = self.series.get(m, [])
        if not self.gens or not vals:
            return {"points": [], "latest": None}

        start = bisect.bisect_right(self.gens, int(after_gen))
        pts = [
            {"generation": int(g), "value": v}
            for g, v in zip(self.gens[start:], vals[start:])
        ]
        lim = int(limit) if int(limit) > 0 else 1000
        if len(pts) > lim:
            pts = pts[-lim:]
        latest = pts[-1] if pts else None
        return {"points": pts, "latest": latest}


def _safe_int(v: Any, default: int = -1) -> int:
    """Best-effort int conversion with float-string fallback."""
    try:
        return int(v)
    except Exception:
        try:
            return int(float(str(v).strip()))
        except Exception:
            return int(default)



class JsonlCache:
    """Incremental cache for append-only JSONL logs."""

    def __init__(self, path: Path, *, max_items: int = 20000) -> None:
        self.path = Path(path)
        self.max_items = int(max_items)
        self.cursor = _IncrementalFileCursor()
        self.items: List[Dict[str, Any]] = []
        self.last_error: Optional[str] = None

    def _reset(self) -> None:
        self.cursor = _IncrementalFileCursor()
        self.items = []
        self.last_error = None

    def update(self) -> None:
        p = self.path
        if not p.exists() or not p.is_file():
            return
        if p.is_symlink():
            return

        try:
            st = p.stat()
        except Exception:
            return

        size = int(st.st_size)
        mtime_ns = int(st.st_mtime_ns)

        if size < self.cursor.offset or mtime_ns < self.cursor.mtime_ns:
            self._reset()

        if size == self.cursor.size and mtime_ns == self.cursor.mtime_ns:
            return

        try:
            with open(p, "rb") as f:
                f.seek(self.cursor.offset)
                data = f.read()
        except Exception as e:
            self.last_error = str(e)
            return

        self.cursor.size = size
        self.cursor.mtime_ns = mtime_ns
        self.cursor.offset = size

        if not data:
            return

        blob = self.cursor.remainder + data
        last_nl = blob.rfind(b"\n")
        complete = blob
        remainder = b""
        if last_nl != -1 and last_nl < len(blob) - 1:
            complete = blob[: last_nl + 1]
            remainder = blob[last_nl + 1 :]
        elif last_nl == -1:
            complete = b""
            remainder = blob

        self.cursor.remainder = remainder
        self.cursor.provisional = bool(remainder)

        for raw in complete.splitlines(keepends=False):
            try:
                txt = raw.decode("utf-8", errors="replace").strip()
                if not txt:
                    continue
                obj = json.loads(txt)
                if isinstance(obj, dict):
                    self.items.append(obj)
                else:
                    self.cursor.parse_errors += 1
            except Exception:
                self.cursor.parse_errors += 1

        if len(self.items) > self.max_items:
            self.items = self.items[-self.max_items :]

    def get_items(self, *, limit: int = 200) -> List[Dict[str, Any]]:
        if not self.items:
            return []
        if limit and len(self.items) > int(limit):
            return self.items[-int(limit) :]
        return list(self.items)


# -----------------------------------------------------------------------------
# Star-import compatibility (monolith behavior)
# -----------------------------------------------------------------------------
# This module is imported with `from ... import *` by server_routes_and_main.
# Python excludes underscore-prefixed names unless they are listed in __all__.
# To preserve backward behavior from the monolithic server.py, we export all
# public *and* underscore helper names (excluding dunder).
__all__ = [k for k in globals().keys() if not k.startswith("__")]
