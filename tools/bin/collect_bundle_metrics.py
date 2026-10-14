#!/usr/bin/env python3
"""infra/bin/collect_bundle_metrics

Category: tooling / analysis helper (NOT a test)

Purpose
-------
Summarize Emergence wrapper-job outputs into a small, shareable bundle so we stop
digging through runs/ like it's an archaeological site.

This version is **stdlib-only** (no pandas), because venvs are fickle and humans
forget dependencies.

What it scans (defaults)
------------------------
runtime/live/wrapper_jobs/*/
  - job_spec.json
  - run_meta.json
  - payload/**/(runs|r)/**/{metrics.csv, behaviors.csv, run_config.csv, config.json}
  - payload/**/{summary.json, matrix.csv, decision_debug.json, logs/*.jsonl, console.txt}
  - payload/**/(runs|r)/**/maps/tier3_regions_stats.json (if present)

Outputs
-------
- <out_dir>/bundle_metrics_summary.csv
    Per-run summary: metadata + (by default) mean of last N generations of selected metrics columns.
- <out_dir>/bundle_metrics_summary.json
    JSON equivalent of bundle_metrics_summary.csv.
- <out_dir>/bundle_case_compare.csv
    Per-case baseline vs variant aggregate (mean across seeds) using key metrics.
- <out_dir>/bundle_metrics_timeseries.csv
    Per-run per-generation time series for selected metrics columns (NEW).
- <out_dir>/bundle_behaviors_gen_agg.csv
    Per-run per-generation aggregates derived from behaviors.csv (NEW).
- <out_dir>/bundle_case_compare_timeseries.csv
    Per-case baseline vs variant time series (mean across seeds) for selected metrics columns (NEW).
- <out_dir>/bundle_share_manifest.json
- <out_dir>/bundle_share_<timestamp>.zip      (optional, includes summaries + per-run evidence)

Why the NEW outputs exist
-------------------------
The previous "tail average" summaries are useful for quick smoke comparisons, but
they throw away the important evidence for temporal selection work:

- reproduction timing distributions (and whether they shift over generations)
- adult survival tail slope and cohort survival curves
- regime shifts / plateaus (need time series)
- behavior distributions (need per-generation aggregates)

These are central to Emergence v2 / Implementation Plan v2 evaluation.

Time filtering (important)
--------------------------
Use --since-minutes N to only include runs started within the last N minutes.
This avoids collecting old junk from previous experiments.

Notes on performance
--------------------
- metrics.csv is small (generations × columns). Safe to load fully.
- behaviors.csv can be large. We stream it and only keep compact per-generation
  aggregates (counts, means, lifespan histograms) so memory use stays sane.

"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
import zipfile
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple


# ============================================================
# Console helpers (progress, ETA, formatting)
# ============================================================

def _isatty() -> bool:
    """Return True if stdout is an interactive TTY."""
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def _fmt_bytes(n: int) -> str:
    """Human-readable byte size."""
    units = ["B", "KB", "MB", "GB", "TB"]
    x = float(max(0, n))
    u = 0
    while x >= 1024.0 and u < len(units) - 1:
        x /= 1024.0
        u += 1
    if u == 0:
        return f"{int(x)} {units[u]}"
    if x >= 100:
        return f"{x:,.0f} {units[u]}"
    if x >= 10:
        return f"{x:,.1f} {units[u]}"
    return f"{x:,.2f} {units[u]}"


def _fmt_eta(seconds: float) -> str:
    """Format seconds as H:MM:SS."""
    s = int(max(0, round(seconds)))
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    if h > 0:
        return f"{h:d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


class ProgressPrinter:
    """Simple progress printer with percent + ETA.

    - In TTY: updates in-place with carriage return.
    - Non-TTY: prints periodic lines to avoid log spam.
    """

    def __init__(self, enabled: bool = True, every: int = 1) -> None:
        self.enabled = bool(enabled)
        self.every = max(1, int(every))
        self.tty = _isatty()
        self._start = time.time()
        self._last_print = 0
        self._ema_sec: Optional[float] = None  # exponential moving average per item

    def set_initial_rate(self, sec_per_item: Optional[float]) -> None:
        """Seed the ETA model with a measured or estimated seconds/item."""
        if sec_per_item is None:
            return
        try:
            v = float(sec_per_item)
        except Exception:
            return
        if v > 0:
            self._ema_sec = v

    def update(self, phase: str, done: int, total: int, detail: str = "") -> None:
        if not self.enabled:
            return
        if total <= 0:
            total = 1
        done = max(0, min(done, total))
        if (done != total) and (done % self.every != 0):
            return

        now = time.time()
        elapsed = now - self._start

        # Update EMA using observed rate (if we have at least one item).
        if done > 0:
            obs = elapsed / float(done)
            if self._ema_sec is None:
                self._ema_sec = obs
            else:
                # EMA: 20% new, 80% old
                self._ema_sec = 0.8 * self._ema_sec + 0.2 * obs

        rate = self._ema_sec if self._ema_sec is not None else (elapsed / float(done) if done > 0 else None)
        remaining = (total - done)
        eta_s = (rate * remaining) if rate is not None else 0.0

        pct = (100.0 * float(done) / float(total)) if total else 100.0
        msg = f"[{phase}] {done}/{total} ({pct:5.1f}%) | elapsed={_fmt_eta(elapsed)} | ETA={_fmt_eta(eta_s)}"
        if detail:
            msg += f" | {detail}"

        if self.tty:
            # pad to clear previous content
            pad = " " * max(0, self._last_print - len(msg))
            end = "\n" if done == total else "\r"
            sys.stdout.write(msg + pad + end)
            sys.stdout.flush()
            self._last_print = len(msg)
        else:
            print(msg)

DEFAULT_KEY_METRICS: Sequence[str] = (
    # selection + survival
    "mean_fitness",
    "std_fitness",
    "p90_fitness",
    "max_fitness",
    "cohort_survival_rate",
    "mean_survival_steps_total",
    "deaths",
    # ecology/behavior
    "mean_energy_collected_total",
    "mean_offspring_total",
    "mean_attacks_total",
    # timing (temporal asymmetry smoke alarm)
    "first_repro_step_mean_alive",
    "first_repro_frac_alive",
    # episode-0 signals
    "episode0_pred_err_mean",
    "episode0_drive_jitter_mean",
    "episode0_switches_mean",
    "episode0_oscillations_mean",
    "episode0_selfharm_mean",
    # late-life coherence lens
    "late_pred_active_frac",
    "late_pred_err_mean_alive",
    "late_pred_cost_mean_alive",
    "late_pred_err_mean_selected_parents",
    "late_pred_cost_mean_selected_parents",
    # identity/coherence (if present)
    "identity_drift_mean",
    "identity_drift_p90",
    "identity_drift_max",
    "coh_thrash_mean",
    "coh_pred_err_mean",
    "coh_penalty_total_mean",
    "drive_volatility_mean",
)

# For time-series export we default to a smaller, pressure-relevant subset.
DEFAULT_TS_METRICS: Sequence[str] = (
    "generation",
    # selection / survival curves
    "cohort_survival_rate",
    "mean_survival_steps_total",
    "mean_fitness",
    "p90_fitness",
    "max_fitness",
    "deaths",
    # reproduction timing (if present)
    "first_repro_step_mean_alive",
    "first_repro_frac_alive",
    "successful_births",
    "failed_births",
    # late-life coherence / prediction
    "episode0_pred_err_mean",
    "late_pred_active_frac",
    "late_pred_err_mean_alive",
    "late_pred_err_mean_selected_parents",
    # identity / coherence (if present)
    "identity_drift_mean",
    "identity_drift_p90",
    "identity_drift_max",
    "coh_thrash_mean",
)

# Core files we always try to capture for analysis.
ANALYSIS_FILES: Sequence[str] = (
    "metrics.csv",
    "behaviors.csv",
    "run_config.csv",
    "config.json",
)

# Extra "evidence" files (best-effort; may not exist in every run).
EVIDENCE_FILES: Sequence[str] = (
    # experiment-level
    "summary.json",
    "matrix.csv",
    "decision_debug.json",
    "manifest.json",
    # run-level
    "console.txt",
    "stdout_stderr.log",
    # logs
    "engine.jsonl",
    "tests.jsonl",
    "wrapper.jsonl",
    # maps
    "tier3_regions_stats.json",
)


@dataclass(frozen=True)
class RunRecord:
    """Represents one wrapper job folder and its summarized metrics."""

    job_dir: Path
    run_id: str
    case: str
    kind: str  # baseline / variant / unknown
    seed: Optional[int]
    status: str
    gens: Optional[int]
    started_ts: Optional[float]
    metrics: Dict[str, float]
    payload_files: Dict[str, Path]  # logical_name -> path


@dataclass
class BehaviorGenAgg:
    """Compact per-generation aggregates derived from behaviors.csv.

    We keep these aggregates small so we can ship them in the bundle and use them
    for pressure topology analysis (survival distribution, reproduction fraction, etc.).
    """

    n: int = 0
    alive_end_n: int = 0
    eligible_n: int = 0
    kept_next_n: int = 0

    # sums for means
    sum_lifespan: float = 0.0
    sum_energy: float = 0.0
    sum_offspring: float = 0.0
    sum_attacks: float = 0.0
    sum_fitness: float = 0.0

    reproducers_n: int = 0
    reproducers_offspring_sum: float = 0.0

    # lifespan histogram: index = lifespan_steps (int), value = count
    lifespan_hist: List[int] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.lifespan_hist is None:
            self.lifespan_hist = []

    def add_lifespan(self, steps: int) -> None:
        """Accumulate histogram count for lifespan steps."""
        if steps < 0:
            return
        if steps >= len(self.lifespan_hist):
            self.lifespan_hist.extend([0] * (steps - len(self.lifespan_hist) + 1))
        self.lifespan_hist[steps] += 1

    def quantile_lifespan(self, q: float) -> Optional[int]:
        """Compute a discrete lifespan quantile from the histogram."""
        if self.n <= 0:
            return None
        if not self.lifespan_hist:
            return None
        target = q * float(self.n)
        cum = 0.0
        for steps, c in enumerate(self.lifespan_hist):
            cum += float(c)
            if cum >= target:
                return steps
        # fallback
        return len(self.lifespan_hist) - 1


def _read_json(path: Path) -> Optional[dict]:
    """Read a JSON file and return dict, or None if missing/bad."""
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        print(f"[warn] failed reading json: {path} ({e})", file=sys.stderr)
        return None


def _find_first_glob(root: Path, patterns: Sequence[str]) -> Optional[Path]:
    """Return first matching file under root for any glob pattern."""
    for pat in patterns:
        hits = sorted(root.glob(pat))
        if hits:
            return hits[0]
    return None


def _parse_case_kind_seed(job_dir_name: str, *, run_id: str, job_spec: dict) -> Tuple[str, str, Optional[int]]:
    """Best-effort parsing from metadata first, then legacy naming patterns."""
    meta = job_spec.get("meta") if isinstance(job_spec.get("meta"), dict) else {}
    axes = meta.get("axes") if isinstance(meta.get("axes"), dict) else {}

    kind = "unknown"
    for k in ("ab", "arm", "kind", "variant"):
        v = str(axes.get(k) or "").strip().lower()
        if v in {"baseline", "variant"}:
            kind = v
            break

    seed: Optional[int] = None
    for raw in (meta.get("seed"), (job_spec.get("args") or {}).get("seed")):
        try:
            if raw is not None and str(raw).strip() != "":
                seed = int(raw)
                break
        except Exception:
            continue

    case = ""
    for k in ("case", "scenario", "condition"):
        v = str(axes.get(k) or "").strip()
        if v:
            case = v
            break

    if kind != "unknown" and case:
        return case, kind, seed

    # Legacy fallback: semantic run naming.
    legacy = str(run_id or job_dir_name)
    m = re.search(r"__([^_]+?)__(baseline|variant)__seed(\d+)", legacy)
    if m:
        seed_i: Optional[int]
        try:
            seed_i = int(m.group(3))
        except Exception:
            seed_i = seed
        return m.group(1), m.group(2), seed_i

    if kind == "unknown":
        kind = "baseline" if "__baseline__" in legacy else ("variant" if "__variant__" in legacy else "unknown")
    if seed is None:
        sm = re.search(r"seed(\d+)", legacy)
        seed = int(sm.group(1)) if sm else None
    if not case:
        parts = legacy.split("__")
        case = parts[2] if len(parts) >= 3 else legacy
    return case, kind, seed


def _extract_started_ts(run_meta: dict, fallback_path: Path) -> Optional[float]:
    """Try to extract a unix timestamp from run_meta; else fallback to folder mtime."""
    if isinstance(run_meta, dict):
        for k in ("start_ts", "started_ts", "started_at_ts", "start_time_ts"):
            v = run_meta.get(k)
            if isinstance(v, (int, float)):
                return float(v)
    try:
        return float(fallback_path.stat().st_mtime)
    except Exception:
        return None


def _discover_payload_files(job_dir: Path) -> Dict[str, Path]:
    """Locate analysis and evidence files within payload/**/

    Returns a mapping of logical file name -> path.
    """
    found: Dict[str, Path] = {}
    payload_root = job_dir / "payload"
    if not payload_root.exists():
        return found

    # Per-run artifacts live under either legacy "runs/" or compact "r/" trees.
    for fname in ANALYSIS_FILES:
        p = _find_first_glob(payload_root, patterns=(f"**/runs/**/{fname}", f"**/r/**/{fname}"))
        if p and p.exists():
            found[fname] = p

    # Evidence files can appear in multiple places; search broadly.
    for fname in EVIDENCE_FILES:
        if fname in found:
            continue
        if fname.endswith(".jsonl"):
            p = _find_first_glob(payload_root, patterns=(f"**/logs/{fname}", f"**/{fname}"))
        elif fname == "tier3_regions_stats.json":
            p = _find_first_glob(payload_root, patterns=(f"**/maps/{fname}",))
        else:
            p = _find_first_glob(payload_root, patterns=(f"**/{fname}",))
        if p and p.exists():
            found[fname] = p

    return found


def _read_csv_rows(path: Path) -> List[Dict[str, str]]:
    """Read a CSV into a list of dict rows."""
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader)


def _try_float(x: object) -> Optional[float]:
    """Parse float from a value, returning None if not possible."""
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    s = str(x).strip()
    if s == "":
        return None
    try:
        return float(s)
    except Exception:
        return None


def _try_int(x: object) -> Optional[int]:
    """Parse int from a value, returning None if not possible."""
    if x is None:
        return None
    if isinstance(x, int):
        return int(x)
    if isinstance(x, float):
        if x.is_integer():
            return int(x)
        return None
    s = str(x).strip()
    if s == "":
        return None
    try:
        return int(float(s))
    except Exception:
        return None


def _select_metric_columns(header: Sequence[str], base_cols: Sequence[str], extra_regexes: Sequence[str]) -> List[str]:
    """Pick which metric columns to aggregate/export."""
    cols = [c for c in base_cols if c in header]
    for rgx in extra_regexes or []:
        try:
            r = re.compile(rgx)
        except re.error as e:
            print(f"[warn] bad regex ignored: {rgx} ({e})", file=sys.stderr)
            continue
        for c in header:
            if c in cols:
                continue
            if r.search(c):
                cols.append(c)
    return cols


def _summarize_metrics(metrics_csv: Path, tail_gens: int, cols: Sequence[str]) -> Tuple[Dict[str, float], Optional[int]]:
    """Summarize last N generations from metrics.csv (mean of numeric columns)."""
    try:
        rows = _read_csv_rows(metrics_csv)
    except Exception as e:
        print(f"[warn] failed reading metrics csv: {metrics_csv} ({e})", file=sys.stderr)
        return {}, None

    if not rows:
        return {}, 0

    rows_tail = rows[-tail_gens:] if tail_gens > 0 else rows

    gens = None
    if "generation" in rows[-1]:
        g_last = _try_float(rows[-1].get("generation"))
        if g_last is not None:
            gens = int(g_last) + 1

    sums: Dict[str, float] = {}
    counts: Dict[str, int] = {}
    for r in rows_tail:
        for c in cols:
            v = _try_float(r.get(c))
            if v is None:
                continue
            sums[c] = sums.get(c, 0.0) + v
            counts[c] = counts.get(c, 0) + 1

    out: Dict[str, float] = {}
    for c, s in sums.items():
        n = counts.get(c, 0)
        if n > 0:
            out[c] = s / float(n)

    return out, gens


def _export_metrics_timeseries(
    metrics_csv: Path,
    out_csv: Path,
    run_meta: Dict[str, object],
    cols: Sequence[str],
) -> Tuple[int, List[str]]:
    """Export per-generation metrics.csv rows (selected columns) into a unified long dataset.

    The output is one big CSV where each row is:
      run_id, case, kind, seed, generation, <selected metrics columns...>

    Returns:
      (rows_written, fieldnames)
    """
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    # Ensure generation is present.
    cols2 = list(cols)
    if "generation" not in cols2:
        cols2.insert(0, "generation")

    fieldnames = ["run_id", "case", "kind", "seed"] + cols2

    rows_written = 0
    with metrics_csv.open("r", encoding="utf-8", newline="") as f_in, out_csv.open("a", encoding="utf-8", newline="") as f_out:
        reader = csv.DictReader(f_in)
        # On first write, write header if file empty.
        if out_csv.stat().st_size == 0:
            w = csv.DictWriter(f_out, fieldnames=fieldnames)
            w.writeheader()
        w = csv.DictWriter(f_out, fieldnames=fieldnames)

        for r in reader:
            row: Dict[str, object] = {
                "run_id": run_meta.get("run_id", ""),
                "case": run_meta.get("case", ""),
                "kind": run_meta.get("kind", ""),
                "seed": run_meta.get("seed", ""),
            }
            for c in cols2:
                row[c] = r.get(c, "")
            w.writerow(row)
            rows_written += 1

    return rows_written, fieldnames


def _export_behaviors_gen_agg(
    behaviors_csv: Path,
    out_csv: Path,
    run_meta: Dict[str, object],
) -> Tuple[int, List[str]]:
    """Stream behaviors.csv and export per-generation aggregates.

    Key outputs per generation:
    - cohort_n, frac_alive_end, frac_eligible, frac_kept_next
    - lifespan mean and quantiles (p50/p90)
    - energy/offpsring/attacks/fitness means
    - frac_reproducers and mean offspring among reproducers

    Returns:
      (rows_written, fieldnames)
    """
    out_csv.parent.mkdir(parents=True, exist_ok=True)

    by_gen: Dict[int, BehaviorGenAgg] = {}

    def get_agg(g: int) -> BehaviorGenAgg:
        if g not in by_gen:
            by_gen[g] = BehaviorGenAgg()
        return by_gen[g]

    with behaviors_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            g = _try_int(r.get("generation"))
            if g is None:
                continue
            agg = get_agg(g)

            agg.n += 1
            alive_end = _try_int(r.get("alive_end")) or 0
            eligible = _try_int(r.get("eligible_for_selection")) or 0
            kept_next = _try_int(r.get("kept_next")) or 0
            agg.alive_end_n += int(alive_end)
            agg.eligible_n += int(eligible)
            agg.kept_next_n += int(kept_next)

            # Lifespan
            ls = _try_int(r.get("lifespan_steps"))
            if ls is not None:
                agg.sum_lifespan += float(ls)
                agg.add_lifespan(ls)

            # Sums
            en = _try_float(r.get("energy_collected"))
            if en is not None:
                agg.sum_energy += en
            off = _try_float(r.get("offspring"))
            if off is not None:
                agg.sum_offspring += off
                if off > 0:
                    agg.reproducers_n += 1
                    agg.reproducers_offspring_sum += off
            att = _try_float(r.get("attacks_made"))
            if att is not None:
                agg.sum_attacks += att
            fit = _try_float(r.get("fitness"))
            if fit is not None:
                agg.sum_fitness += fit

    # Write output
    fieldnames = [
        "run_id",
        "case",
        "kind",
        "seed",
        "generation",
        "cohort_n",
        "frac_alive_end",
        "frac_eligible",
        "frac_kept_next",
        "lifespan_mean",
        "lifespan_p50",
        "lifespan_p90",
        "energy_collected_mean",
        "offspring_mean",
        "attacks_mean",
        "fitness_mean",
        "frac_reproducers",
        "offspring_mean_reproducers",
    ]

    rows_written = 0
    with out_csv.open("a", encoding="utf-8", newline="") as f_out:
        if out_csv.stat().st_size == 0:
            w = csv.DictWriter(f_out, fieldnames=fieldnames)
            w.writeheader()
        w = csv.DictWriter(f_out, fieldnames=fieldnames)

        for g in sorted(by_gen.keys()):
            agg = by_gen[g]
            if agg.n <= 0:
                continue
            row: Dict[str, object] = {
                "run_id": run_meta.get("run_id", ""),
                "case": run_meta.get("case", ""),
                "kind": run_meta.get("kind", ""),
                "seed": run_meta.get("seed", ""),
                "generation": g,
                "cohort_n": agg.n,
                "frac_alive_end": agg.alive_end_n / float(agg.n),
                "frac_eligible": agg.eligible_n / float(agg.n),
                "frac_kept_next": agg.kept_next_n / float(agg.n),
                "lifespan_mean": (agg.sum_lifespan / float(agg.n)) if agg.n else "",
                "lifespan_p50": agg.quantile_lifespan(0.50) if agg.n else "",
                "lifespan_p90": agg.quantile_lifespan(0.90) if agg.n else "",
                "energy_collected_mean": (agg.sum_energy / float(agg.n)) if agg.n else "",
                "offspring_mean": (agg.sum_offspring / float(agg.n)) if agg.n else "",
                "attacks_mean": (agg.sum_attacks / float(agg.n)) if agg.n else "",
                "fitness_mean": (agg.sum_fitness / float(agg.n)) if agg.n else "",
                "frac_reproducers": (agg.reproducers_n / float(agg.n)) if agg.n else "",
                "offspring_mean_reproducers": (agg.reproducers_offspring_sum / float(agg.reproducers_n)) if agg.reproducers_n else 0.0,
            }
            w.writerow(row)
            rows_written += 1

    return rows_written, fieldnames


def collect_records(
    input_dir: Path,
    tail_gens: int,
    base_metric_cols: Sequence[str],
    extra_cols_regex: Sequence[str],
    since_minutes: Optional[int],
    progress: Optional[ProgressPrinter] = None,
) -> List[RunRecord]:
    """Scan wrapper job folders and return summarized RunRecord list."""
    if not input_dir.exists():
        raise FileNotFoundError(f"input_dir does not exist: {input_dir}")

    now = time.time()
    since_ts = None
    if since_minutes is not None:
        since_ts = now - float(since_minutes) * 60.0

    records: List[RunRecord] = []

    job_dirs = sorted([p for p in input_dir.iterdir() if p.is_dir()])
    total_jobs = len(job_dirs)
    for idx, job_dir in enumerate(job_dirs, start=1):
        if progress is not None:
            progress.update("scan", idx, total_jobs, detail=job_dir.name)

        job_spec = _read_json(job_dir / "job_spec.json") or {}
        run_meta = _read_json(job_dir / "run_meta.json") or {}

        started_ts = _extract_started_ts(run_meta, fallback_path=job_dir)
        if since_ts is not None and started_ts is not None and started_ts < since_ts:
            continue

        run_id = str(
            (job_spec.get("meta") or {}).get("run_id")
            or job_spec.get("run_id")
            or job_spec.get("job_id")
            or job_dir.name
        )

        case, kind, seed = _parse_case_kind_seed(job_dir.name, run_id=run_id, job_spec=job_spec)
        if seed is None:
            seed = (job_spec.get("args", {}) or {}).get("seed")

        status = str(run_meta.get("status") or run_meta.get("state") or run_meta.get("result") or "unknown")

        payload_files = _discover_payload_files(job_dir)

        metrics: Dict[str, float] = {}
        gens: Optional[int] = None
        metrics_csv = payload_files.get("metrics.csv")
        if metrics_csv and metrics_csv.exists():
            try:
                with metrics_csv.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.reader(f)
                    header = next(reader, [])
                cols = _select_metric_columns(header, base_metric_cols, extra_cols_regex)
            except Exception:
                cols = list(base_metric_cols)
            metrics, gens = _summarize_metrics(metrics_csv, tail_gens=tail_gens, cols=cols)

        records.append(
            RunRecord(
                job_dir=job_dir,
                run_id=run_id,
                case=str(case),
                kind=str(kind),
                seed=int(seed) if seed is not None else None,
                status=status,
                gens=gens,
                started_ts=started_ts,
                metrics=metrics,
                payload_files=payload_files,
            )
        )

    return records


def _write_csv(path: Path, rows: List[Dict[str, object]], fieldnames: List[str]) -> None:
    """Write rows to CSV with fixed fieldnames."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in fieldnames})


def _write_json(path: Path, obj: object) -> None:
    """Write JSON with stable formatting."""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)


def _records_to_rows(records: Sequence[RunRecord]) -> Tuple[List[Dict[str, object]], List[str]]:
    """Flatten records into CSV rows and compute field order."""
    meta_cols = ["job_dir", "run_id", "case", "kind", "seed", "status", "gens", "started_ts"]
    metric_cols: List[str] = sorted({k for r in records for k in (r.metrics or {}).keys()})
    fieldnames = meta_cols + metric_cols

    rows: List[Dict[str, object]] = []
    for r in records:
        row: Dict[str, object] = {
            "job_dir": str(r.job_dir),
            "run_id": r.run_id,
            "case": r.case,
            "kind": r.kind,
            "seed": r.seed,
            "status": r.status,
            "gens": r.gens,
            "started_ts": r.started_ts,
        }
        row.update(r.metrics or {})
        rows.append(row)

    return rows, fieldnames


def _case_compare(rows: List[Dict[str, object]], key_metrics: Sequence[str]) -> Tuple[List[Dict[str, object]], List[str]]:
    """Aggregate baseline vs variant per case (if detectable)."""
    by_case: Dict[str, List[Dict[str, object]]] = {}
    for r in rows:
        case = str(r.get("case", ""))
        by_case.setdefault(case, []).append(r)

    out_rows: List[Dict[str, object]] = []
    fieldnames = ["case", "n_base", "n_var"]
    for m in key_metrics:
        fieldnames.extend([f"base_{m}", f"var_{m}", f"delta_{m}", f"reldelta_{m}"])

    def mean_metric(items: List[Dict[str, object]], metric: str) -> Optional[float]:
        vals: List[float] = []
        for rr in items:
            v = _try_float(rr.get(metric))
            if v is not None:
                vals.append(v)
        if not vals:
            return None
        return sum(vals) / float(len(vals))

    for case, group in sorted(by_case.items()):
        base = [r for r in group if str(r.get("kind")) == "baseline"]
        var = [r for r in group if str(r.get("kind")) == "variant"]
        if not base or not var:
            continue

        out: Dict[str, object] = {"case": case, "n_base": len(base), "n_var": len(var)}

        for m in key_metrics:
            b = mean_metric(base, m)
            v = mean_metric(var, m)
            if b is None or v is None:
                continue
            out[f"base_{m}"] = b
            out[f"var_{m}"] = v
            out[f"delta_{m}"] = v - b
            if abs(b) > 1e-12:
                out[f"reldelta_{m}"] = (v - b) / abs(b)

        out_rows.append(out)

    return out_rows, fieldnames


def _case_compare_timeseries(
    metrics_timeseries_csv: Path,
    out_csv: Path,
    ts_metric_cols: Sequence[str],
) -> Tuple[int, List[str]]:
    """Build per-case baseline vs variant mean time series (mean across seeds).

    Input: bundle_metrics_timeseries.csv
    Output: bundle_case_compare_timeseries.csv
    """
    # We expect the unified file contains: run_id, case, kind, seed, generation, ...
    # We'll group by (case, kind, generation) and compute mean per metric.

    out_csv.parent.mkdir(parents=True, exist_ok=True)

    # Normalize columns: remove generation if present in ts_metric_cols list
    metric_cols = [c for c in ts_metric_cols if c != "generation"]

    # Aggregation maps:
    # sums[(case, kind, gen)][metric] = sum
    # counts[(case, kind, gen)][metric] = n
    sums: Dict[Tuple[str, str, int], Dict[str, float]] = {}
    counts: Dict[Tuple[str, str, int], Dict[str, int]] = {}

    def acc(key: Tuple[str, str, int], metric: str, val: float) -> None:
        sums.setdefault(key, {})
        counts.setdefault(key, {})
        sums[key][metric] = sums[key].get(metric, 0.0) + val
        counts[key][metric] = counts[key].get(metric, 0) + 1

    with metrics_timeseries_csv.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for r in reader:
            case = str(r.get("case", ""))
            kind = str(r.get("kind", ""))
            gen = _try_int(r.get("generation"))
            if gen is None:
                continue
            key = (case, kind, gen)
            for m in metric_cols:
                v = _try_float(r.get(m))
                if v is None:
                    continue
                acc(key, m, v)

    # Build baseline/variant deltas per generation per case.
    fieldnames = ["case", "generation"]
    for m in metric_cols:
        fieldnames.extend([f"base_{m}", f"var_{m}", f"delta_{m}", f"reldelta_{m}"])

    rows_written = 0
    with out_csv.open("w", encoding="utf-8", newline="") as f_out:
        w = csv.DictWriter(f_out, fieldnames=fieldnames)
        w.writeheader()

        # Index keys by case+gen
        index: Dict[Tuple[str, int], Dict[str, Dict[str, float]]] = {}
        for (case, kind, gen), md in sums.items():
            for m, s in md.items():
                n = counts[(case, kind, gen)].get(m, 0)
                if n <= 0:
                    continue
                index.setdefault((case, gen), {}).setdefault(kind, {})
                index[(case, gen)][kind][m] = s / float(n)

        for (case, gen) in sorted(index.keys(), key=lambda x: (x[0], x[1])):
            kinds = index[(case, gen)]
            if "baseline" not in kinds or "variant" not in kinds:
                continue
            out: Dict[str, object] = {"case": case, "generation": gen}
            for m in metric_cols:
                b = kinds["baseline"].get(m)
                v = kinds["variant"].get(m)
                if b is None or v is None:
                    continue
                out[f"base_{m}"] = b
                out[f"var_{m}"] = v
                out[f"delta_{m}"] = v - b
                if abs(b) > 1e-12:
                    out[f"reldelta_{m}"] = (v - b) / abs(b)
            w.writerow(out)
            rows_written += 1

    return rows_written, fieldnames



def _build_zip_plan(
    out_dir: Path,
    records: Sequence[RunRecord],
    summary_paths: Sequence[Path],
    include_raw_behaviors: bool,
    include_logs: bool,
) -> List[Tuple[Path, str, int]]:
    """Build an ordered (path, arcname, size_bytes) plan for zip inclusion.

    This is used both for preflight estimation and for the actual zip build.
    Ordering is important because we enforce a hard max size budget.
    """
    plan: List[Tuple[Path, str, int]] = []

    def add(p: Path, arc: str) -> None:
        try:
            if not p.exists() or not p.is_file():
                return
            size = int(p.stat().st_size)
        except Exception:
            return
        plan.append((p, arc, size))

    # 1) Summaries first (these are the reason the zip exists).
    for p in summary_paths:
        add(p, p.name)

    # 2) Per-run meta + payload evidence, prioritized.
    # Always include wrapper-level meta if present.
    wrapper_meta = ("job_spec.json", "run_meta.json", "artifact_manifest.json", "stdout_stderr.log")

    for r in records:
        for meta_name in wrapper_meta:
            mp = r.job_dir / meta_name
            add(mp, f"runs/{r.run_id}/{meta_name}")

        # payload files discovered during scan
        for logical_name, fp in (r.payload_files or {}).items():
            # Apply include rules
            if (logical_name == "behaviors.csv") and (not include_raw_behaviors):
                continue

            # crude log detection: jsonl + console/log
            if not include_logs:
                if logical_name.endswith(".jsonl"):
                    continue
                if logical_name in ("console.txt", "stdout_stderr.log"):
                    continue
                if "log" in logical_name.lower():
                    # be conservative, skip unknown large log-like artifacts
                    if logical_name not in ("run_config.csv", "metrics.csv", "config.json"):
                        continue

            add(fp, f"runs/{r.run_id}/{logical_name}")

    return plan


def _zip_share_bundle(
    out_dir: Path,
    records: Sequence[RunRecord],
    summary_paths: Sequence[Path],
    *,
    max_zip_mb: int = 450,
    include_raw_behaviors: bool = False,
    include_logs: bool = False,
    progress: Optional[ProgressPrinter] = None,
) -> Tuple[Path, Dict[str, object]]:
    """Create a share bundle zip with summaries + essential per-run evidence files.

    Enforces a hard size cap (default 450 MB). Files are added in priority order.
    Returns: (zip_path, stats)
    """
    ts = time.strftime("%Y-%m-%d_%H%M%S", time.localtime())
    zip_path = out_dir / f"bundle_share_{ts}.zip"

    max_bytes = int(max_zip_mb) * 1024 * 1024
    plan = _build_zip_plan(
        out_dir=out_dir,
        records=records,
        summary_paths=summary_paths,
        include_raw_behaviors=include_raw_behaviors,
        include_logs=include_logs,
    )

    # Reserve a little headroom for zip headers/central directory.
    # This is conservative: we budget using raw bytes (pre-compression).
    budget = int(max_bytes * 0.98)

    included: List[Tuple[Path, str, int]] = []
    excluded: List[Dict[str, object]] = []
    total_planned_bytes = sum(sz for _p, _a, sz in plan)

    used = 0
    for p, arc, sz in plan:
        if used + sz <= budget:
            included.append((p, arc, sz))
            used += sz
        else:
            excluded.append({"path": str(p), "arcname": arc, "size_bytes": sz})

    # Build manifest and exclusions content in-memory (no leftover files on disk).
    manifest = {
        "created_unix_ts": time.time(),
        "max_zip_mb": int(max_zip_mb),
        "include_raw_behaviors": bool(include_raw_behaviors),
        "include_logs": bool(include_logs),
        "analysis_files": list(ANALYSIS_FILES),
        "evidence_files": list(EVIDENCE_FILES),
        "included_runs": [
            {
                "job_dir": str(r.job_dir),
                "run_id": r.run_id,
                "case": r.case,
                "kind": r.kind,
                "seed": r.seed,
                "status": r.status,
                "gens": r.gens,
                "started_ts": r.started_ts,
                "payload_files": {k: str(v) for k, v in (r.payload_files or {}).items()},
            }
            for r in records
        ],
        "notes": "Per-run files are stored under runs/<run_id>/ inside this zip.",
    }

    exclusions_doc = {
        "planned_total_bytes": total_planned_bytes,
        "included_total_bytes_raw": used,
        "excluded_files": excluded,
        "exclusion_note": "Files are excluded to satisfy max zip size cap. Re-run with --max-zip-mb or include flags if needed.",
    }

    # Write zip
    total_files = len(included) + 2  # + manifest + exclusions
    if progress is not None:
        progress.update("zip", 0, total_files, detail=f"budget={_fmt_bytes(max_bytes)} planned={_fmt_bytes(total_planned_bytes)}")

    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        done = 0
        for p, arc, _sz in included:
            if p.exists():
                zf.write(p, arcname=arc)
            done += 1
            if progress is not None:
                progress.update("zip", done, total_files, detail=arc)

        zf.writestr("bundle_share_manifest.json", json.dumps(manifest, indent=2, sort_keys=True))
        done += 1
        if progress is not None:
            progress.update("zip", done, total_files, detail="bundle_share_manifest.json")

        zf.writestr("bundle_share_exclusions.json", json.dumps(exclusions_doc, indent=2, sort_keys=True))
        done += 1
        if progress is not None:
            progress.update("zip", done, total_files, detail="bundle_share_exclusions.json")

    stats: Dict[str, object] = {
        "zip_path": str(zip_path),
        "max_zip_mb": int(max_zip_mb),
        "planned_total_bytes": total_planned_bytes,
        "included_total_bytes_raw": used,
        "included_files": len(included),
        "excluded_files": len(excluded),
    }
    return zip_path, stats

def _benchmark_extract_per_run(
    records: Sequence[RunRecord],
    *,
    do_timeseries: bool,
    do_behavior_agg: bool,
    extra_cols: Sequence[str],
    sample_n: int,
) -> Optional[float]:
    """Benchmark per-run extraction time to seed ETA estimation.

    We run the same extraction logic on up to `sample_n` runs and return average seconds/run.
    The benchmark writes to a temporary directory and cleans up after itself.

    Returns None if no suitable run is found.
    """
    sample_n = max(0, int(sample_n))
    if sample_n <= 0:
        return None

    # pick runs that actually have the needed files
    candidates: List[RunRecord] = []
    for r in records:
        if do_timeseries and ("metrics.csv" not in (r.payload_files or {})):
            continue
        if do_behavior_agg and ("behaviors.csv" not in (r.payload_files or {})):
            continue
        candidates.append(r)
        if len(candidates) >= sample_n:
            break

    if not candidates:
        return None

    times: List[float] = []
    with tempfile.TemporaryDirectory(prefix="bundle_bench_") as td:
        tmp = Path(td)
        ts_csv = tmp / "ts.csv"
        beh_csv = tmp / "beh.csv"

        # ensure clean
        if ts_csv.exists():
            ts_csv.unlink()
        if beh_csv.exists():
            beh_csv.unlink()

        for r in candidates:
            run_meta = {"run_id": r.run_id, "case": r.case, "kind": r.kind, "seed": r.seed}
            t0 = time.time()

            if do_timeseries:
                metrics_csv = r.payload_files.get("metrics.csv")
                if metrics_csv and metrics_csv.exists():
                    try:
                        with metrics_csv.open("r", encoding="utf-8", newline="") as f:
                            reader = csv.reader(f)
                            header = next(reader, [])
                    except Exception:
                        header = []
                    base_cols = [c for c in DEFAULT_TS_METRICS if c in header]
                    cols = _select_metric_columns(header, base_cols, list(extra_cols))
                    _export_metrics_timeseries(metrics_csv, out_csv=ts_csv, run_meta=run_meta, cols=cols)

            if do_behavior_agg:
                behaviors_csv = r.payload_files.get("behaviors.csv")
                if behaviors_csv and behaviors_csv.exists():
                    _export_behaviors_gen_agg(behaviors_csv, out_csv=beh_csv, run_meta=run_meta)

            times.append(time.time() - t0)

    if not times:
        return None
    return sum(times) / float(len(times))

def main(argv: Optional[Sequence[str]] = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Summarize Emergence wrapper-job metrics into a bundle (stdlib-only).")
    parser.add_argument("--input", default="runtime/live/wrapper_jobs", help="Directory containing wrapper job folders.")
    parser.add_argument("--out", default="runs/_bundles/latest", help="Output directory for summary artifacts.")
    parser.add_argument("--tail-gens", type=int, default=3, help="How many last generations to average (default: 3). Use 0 for all.")
    parser.add_argument("--zip", dest="zip", action="store_true", help="Create a share zip (default: enabled).")
    parser.add_argument("--no-zip", dest="zip", action="store_false", help="Disable zip creation.")
    parser.set_defaults(zip=True)

    parser.add_argument("--max-zip-mb", type=int, default=450, help="Maximum size for the share zip in MB (default: 450).")
    parser.add_argument(
        "--zip-include-raw-behaviors",
        action="store_true",
        help="Include raw behaviors.csv in the zip (can be large; disabled by default).",
    )
    parser.add_argument(
        "--zip-include-logs",
        action="store_true",
        help="Include log-like artifacts (jsonl/console/logs) in the zip (can be large; disabled by default).",
    )

    parser.add_argument("--no-progress", action="store_true", help="Disable progress/ETA output.")
    parser.add_argument("--progress-every", type=int, default=1, help="Update progress output every N items (default: 1).")
    parser.add_argument(
        "--preflight-sample",
        type=int,
        default=1,
        help="Number of runs to benchmark for ETA estimation before processing (default: 1).",
    )
    parser.add_argument("--since-minutes", type=int, default=None, help="Only include runs started within the last N minutes.")
    parser.add_argument(
        "--extra-cols",
        action="append",
        default=[],
        help="Regex for extra metric columns to include in summary & time series (may be repeated).",
    )
    parser.add_argument(
        "--no-timeseries",
        action="store_true",
        help="Disable exporting per-generation metrics time series (bundle_metrics_timeseries.csv).",
    )
    parser.add_argument(
        "--no-behavior-agg",
        action="store_true",
        help="Disable exporting per-generation behavior aggregates (bundle_behaviors_gen_agg.csv).",
    )

    args = parser.parse_args(argv)

    input_dir = Path(args.input)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    progress_scan = ProgressPrinter(enabled=(not args.no_progress), every=int(args.progress_every))

    records = collect_records(
        input_dir=input_dir,
        tail_gens=int(args.tail_gens),
        base_metric_cols=list(DEFAULT_KEY_METRICS),
        extra_cols_regex=list(args.extra_cols),
        since_minutes=args.since_minutes,
        progress=progress_scan,
    )

    # Preflight: estimate size and benchmark per-run extraction time for ETA.
    plan = _build_zip_plan(
        out_dir=out_dir,
        records=records,
        summary_paths=[],
        include_raw_behaviors=bool(args.zip_include_raw_behaviors),
        include_logs=bool(args.zip_include_logs),
    )
    planned_bytes = sum(sz for _p, _a, sz in plan)
    max_bytes = int(args.max_zip_mb) * 1024 * 1024
    budget = int(max_bytes * 0.98)
    included_bytes = 0
    for _p, _a, sz in plan:
        if included_bytes + sz <= budget:
            included_bytes += sz
        else:
            break

    bench_sec = _benchmark_extract_per_run(
        records,
        do_timeseries=(not args.no_timeseries),
        do_behavior_agg=(not args.no_behavior_agg),
        extra_cols=list(args.extra_cols),
        sample_n=int(args.preflight_sample),
    )

    if bench_sec is not None:
        est_total = bench_sec * float(len(records))
        print(
            f"[preflight] runs={len(records)} | planned_raw={_fmt_bytes(planned_bytes)} | "
            f"zip_budget={_fmt_bytes(max_bytes)} | est_included_raw≈{_fmt_bytes(included_bytes)} | "
            f"bench≈{bench_sec:.2f}s/run | est_total≈{_fmt_eta(est_total)}",
            file=sys.stderr,
        )
    else:
        print(
            f"[preflight] runs={len(records)} | planned_raw={_fmt_bytes(planned_bytes)} | "
            f"zip_budget={_fmt_bytes(max_bytes)} | est_included_raw≈{_fmt_bytes(included_bytes)} | "
            f"bench=unavailable",
            file=sys.stderr,
        )

    progress_export = ProgressPrinter(enabled=(not args.no_progress), every=int(args.progress_every))
    progress_export.set_initial_rate(bench_sec)

    rows, fieldnames = _records_to_rows(records)
    summary_csv = out_dir / "bundle_metrics_summary.csv"
    _write_csv(summary_csv, rows, fieldnames)

    summary_json = out_dir / "bundle_metrics_summary.json"
    _write_json(summary_json, {"rows": rows})

    compare_rows, compare_fields = _case_compare(rows, key_metrics=list(DEFAULT_KEY_METRICS))
    compare_csv = out_dir / "bundle_case_compare.csv"
    _write_csv(compare_csv, compare_rows, compare_fields)

    # NEW: per-generation time series exports
    ts_csv = out_dir / "bundle_metrics_timeseries.csv"
    beh_agg_csv = out_dir / "bundle_behaviors_gen_agg.csv"
    case_ts_csv = out_dir / "bundle_case_compare_timeseries.csv"

    # We build the time series columns by intersecting DEFAULT_TS_METRICS with actual headers per run,
    # and we also apply --extra-cols regex matching.
    ts_rows_written = 0
    beh_rows_written = 0

    if not args.no_timeseries:
        # Ensure the output file starts fresh for this invocation.
        if ts_csv.exists():
            ts_csv.unlink()

    if not args.no_behavior_agg:
        if beh_agg_csv.exists():
            beh_agg_csv.unlink()

    # Export per-run data
    total_runs = len(records)
    for idx_r, r in enumerate(records, start=1):
        if progress_export is not None:
            progress_export.update("export", idx_r - 1, total_runs, detail=r.run_id)
        run_meta = {"run_id": r.run_id, "case": r.case, "kind": r.kind, "seed": r.seed}

        metrics_csv = r.payload_files.get("metrics.csv")
        if metrics_csv and metrics_csv.exists() and not args.no_timeseries:
            try:
                with metrics_csv.open("r", encoding="utf-8", newline="") as f:
                    reader = csv.reader(f)
                    header = next(reader, [])
            except Exception:
                header = []
            base_cols = [c for c in DEFAULT_TS_METRICS if c in header]
            cols = _select_metric_columns(header, base_cols, list(args.extra_cols))
            n, _fields = _export_metrics_timeseries(metrics_csv, out_csv=ts_csv, run_meta=run_meta, cols=cols)
            ts_rows_written += n

        behaviors_csv = r.payload_files.get("behaviors.csv")
        if behaviors_csv and behaviors_csv.exists() and not args.no_behavior_agg:
            n2, _fields2 = _export_behaviors_gen_agg(behaviors_csv, out_csv=beh_agg_csv, run_meta=run_meta)
            beh_rows_written += n2

        if progress_export is not None:
            progress_export.update("export", idx_r, total_runs, detail=r.run_id)

    # NEW: per-case baseline vs variant time series deltas
    if not args.no_timeseries and ts_csv.exists():
        # We need a consistent list of metric columns to compute deltas on.
        # We'll use DEFAULT_TS_METRICS (minus generation) + any regex-derived columns found in the first run.
        # For robustness, we read header from ts_csv itself.
        with ts_csv.open("r", encoding="utf-8", newline="") as f:
            ts_header = next(csv.reader(f), [])
        # ts_csv header includes run_id/case/kind/seed plus metrics columns
        ts_metric_cols = [c for c in ts_header if c not in ("run_id", "case", "kind", "seed")]
        _case_compare_timeseries(ts_csv, out_csv=case_ts_csv, ts_metric_cols=ts_metric_cols)

    summary_paths = [summary_csv, summary_json, compare_csv]
    if ts_csv.exists():
        summary_paths.append(ts_csv)
    if beh_agg_csv.exists():
        summary_paths.append(beh_agg_csv)
    if case_ts_csv.exists():
        summary_paths.append(case_ts_csv)
    if args.zip:
        progress_zip = ProgressPrinter(enabled=(not args.no_progress), every=int(args.progress_every))
        zpath, zstats = _zip_share_bundle(
            out_dir,
            records=records,
            summary_paths=summary_paths,
            max_zip_mb=int(args.max_zip_mb),
            include_raw_behaviors=bool(args.zip_include_raw_behaviors),
            include_logs=bool(args.zip_include_logs),
            progress=progress_zip,
        )

        # Print the zip path to stdout (machine-friendly).
        print(str(zpath))

        # Cleanup: remove generated artifacts so only the zip remains.
        for p in summary_paths:
            try:
                if p.exists():
                    p.unlink()
            except Exception as e:
                print(f"[warn] failed deleting {p}: {e}", file=sys.stderr)

        # Human-friendly note to stderr (stdout stays just the zip path).
        print(
            f"[info] bundle={Path(str(zpath)).name} | included_raw={_fmt_bytes(int(zstats.get('included_total_bytes_raw', 0)))} | "
            f"excluded_files={int(zstats.get('excluded_files', 0))}",
            file=sys.stderr,
        )

    if not args.zip:
        print(f"Wrote: {summary_csv}")
        print(f"Wrote: {summary_json}")
        print(f"Wrote: {compare_csv}")

        if not args.no_timeseries:
            print(f"Wrote: {ts_csv} (rows={ts_rows_written})")
            if case_ts_csv.exists():
                print(f"Wrote: {case_ts_csv}")

        if not args.no_behavior_agg:
            print(f"Wrote: {beh_agg_csv} (rows={beh_rows_written})")

    return 0


# ============================================================
# Safe CLI wrapper (no Python traceback by default)
# ============================================================

def _main_safe() -> int:
    """Run main() with friendly error reporting.

    Set environment variable CAUSYL_TRACEBACK=1 to print full tracebacks.
    """
    import os
    import traceback
    try:
        return int(main())
    except SystemExit as e:
        code = e.code
        if code is None:
            return 0
        if isinstance(code, int):
            return code
        # SystemExit with message/string
        print(f"[ERROR] {code}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("[ERROR] Interrupted (KeyboardInterrupt).", file=sys.stderr)
        return 130
    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}", file=sys.stderr)
        if os.environ.get("CAUSYL_TRACEBACK", "") == "1":
            traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(_main_safe())
