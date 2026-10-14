"""Shared progress tracking for manifest-driven batch execution."""
from __future__ import annotations

import os
import re
import statistics
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from experiments_shared.job_manifests.wrapper_exec import _find_wrapper_dir_for_job

@dataclass
class SlotState:
    """State for one logical worker slot in the local runner.

    Notes
    -----
    A slot is a *capacity unit* (0..concurrency-1). We assign jobs to slots
    deterministically at submit-time for observability. This does not claim any
    relationship to OS threads or CPU cores.
    """
    slot_id: int
    job_id: str = ""
    device: str = ""
    started_monotonic: float = 0.0  # time.monotonic()



@dataclass
class RuntimeStats:
    """Aggregate runtime statistics for completed jobs."""
    count: int
    mean_sec: float
    stddev_sec: float



class ProgressTracker:
    """Thread-safe tracker for local runner progress and slot/job metadata.

    This is intentionally small:
    - one lock
    - O(1) updates on submit/complete
    - snapshots for renderers to avoid holding locks while formatting strings
    """

    def __init__(self, *, total_jobs: int, slots: int, seed_job_sec: float) -> None:
        """Create a progress tracker.

        Parameters
        ----------
        total_jobs:
            Total number of jobs in the manifest.
        slots:
            Number of logical worker slots (usually == concurrency).
        seed_job_sec:
            Initial per-job runtime estimate used before any completion.
        """
        import threading

        self._lock = threading.Lock()
        self.total = int(total_jobs)
        self.slots_total = int(max(1, slots))
        self.seed_job_sec = float(seed_job_sec)

        self.started_monotonic = time.monotonic()
        self.completed = 0
        self.completed_wall_times: List[float] = []
        self.slot_states: List[SlotState] = [SlotState(slot_id=i) for i in range(self.slots_total)]

        # Deterministic queue tracking (jobs not yet submitted to any slot).
        self._pending_job_ids: List[str] = []
        self._submitted_job_ids: List[str] = []
        self._completed_job_ids: List[str] = []

        # Job -> device mapping for quick GPU summaries.
        self._job_device: Dict[str, str] = {}

        # Live running (job_id -> slot_id).
        self._running: Dict[str, int] = {}

    def prime_pending(self, job_ids_in_order: List[str], job_devices: Dict[str, str]) -> None:
        """Initialize pending job order and static device mapping (deterministic)."""
        with self._lock:
            self._pending_job_ids = list(job_ids_in_order)
            self._job_device = dict(job_devices)

    def on_submit(self, *, slot_id: int, job_id: str) -> None:
        """Mark job as running on a given slot."""
        now = time.monotonic()
        with self._lock:
            st = self.slot_states[int(slot_id)]
            st.job_id = str(job_id)
            st.device = str(self._job_device.get(job_id, "") or "")
            st.started_monotonic = float(now)

            self._running[str(job_id)] = int(slot_id)
            self._submitted_job_ids.append(str(job_id))
            if self._pending_job_ids and self._pending_job_ids[0] == str(job_id):
                self._pending_job_ids.pop(0)
            elif str(job_id) in self._pending_job_ids:
                self._pending_job_ids.remove(str(job_id))

    def on_complete(self, *, job_id: str, wall_time_sec: float) -> None:
        """Mark job complete and free its slot (if tracked)."""
        with self._lock:
            self.completed += 1
            self.completed_wall_times.append(float(wall_time_sec))
            self._completed_job_ids.append(str(job_id))

            slot_id = self._running.pop(str(job_id), None)
            if slot_id is not None:
                st = self.slot_states[int(slot_id)]
                st.job_id = ""
                st.device = ""
                st.started_monotonic = 0.0

    def stats(self) -> RuntimeStats:
        """Compute runtime stats for completed jobs (mean/stddev)."""
        with self._lock:
            xs = list(self.completed_wall_times)
        if not xs:
            return RuntimeStats(count=0, mean_sec=0.0, stddev_sec=0.0)
        if len(xs) == 1:
            return RuntimeStats(count=1, mean_sec=float(xs[0]), stddev_sec=0.0)
        return RuntimeStats(count=len(xs), mean_sec=float(statistics.mean(xs)), stddev_sec=float(statistics.pstdev(xs)))

    def snapshot(self) -> Dict[str, Any]:
        """Return a stable snapshot dict suitable for renderers."""
        now = time.monotonic()
        with self._lock:
            slot_copy = [SlotState(**st.__dict__) for st in self.slot_states]
            pending = list(self._pending_job_ids)
            running = dict(self._running)
            completed = int(self.completed)
            total = int(self.total)
            seed = float(self.seed_job_sec)
            job_device = dict(self._job_device)
            completed_wall = list(self.completed_wall_times)
            started = float(self.started_monotonic)

        return {
            "now_monotonic": float(now),
            "started_monotonic": started,
            "total": total,
            "completed": completed,
            "pending_job_ids": pending,
            "running_map": running,  # job_id -> slot_id
            "slot_states": slot_copy,
            "seed_job_sec": seed,
            "job_device": job_device,
            "completed_wall_times": completed_wall,
        }



def _eta_model_detailed(snapshot: Dict[str, Any], *, slots: int) -> Dict[str, Any]:
    """Compute ETA summary for detailed progress mode.

    Model (simple, deterministic, low-cost)
    --------------------------------------
    We estimate remaining work as:
      remaining_work = sum(remaining_est(job) for running jobs) + mean_est * pending_count

    Where:
      - mean_est = mean(completed_wall_times) if any, else seed_job_sec
      - remaining_est(job) = max(0, mean_est - elapsed_running)

    Then:
      eta_p50 = remaining_work / effective_parallelism
      eta_cons = remaining_work_cons / effective_parallelism
        where mean_est_cons = mean + 1.28*stddev (approx p90), with fallbacks.

    We also compute:
      - slow_threshold = mean + 2*stddev (when count>=2 and stddev>0)
      - min_remaining = max(remaining_est(job) for running jobs) (lower bound)
    """
    now = float(snapshot["now_monotonic"])
    started = float(snapshot["started_monotonic"])
    elapsed_total = max(0.0, now - started)

    completed_wall = list(snapshot.get("completed_wall_times", []))
    pending_count = len(snapshot.get("pending_job_ids", []))
    slot_states: List[SlotState] = list(snapshot.get("slot_states", []))

    eff = max(1, int(min(int(slots), int(snapshot.get("total", 0) or 0) or 1)))

    if completed_wall:
        mean = float(statistics.mean(completed_wall))
        std = float(statistics.pstdev(completed_wall)) if len(completed_wall) > 1 else 0.0
    else:
        mean = float(snapshot.get("seed_job_sec", 0.0) or 0.0)
        std = 0.0

    # Conservative estimate: mean + 1.28*std (approx p90 for normal-ish), with guardrails.
    if std > 0.0:
        mean_cons = mean + 1.28 * std
    else:
        mean_cons = mean * 1.25 if mean > 0 else 0.0

    running_remaining = []
    running_remaining_cons = []
    slow_threshold = (mean + 2.0 * std) if (len(completed_wall) >= 2 and std > 0.0) else None

    for st in slot_states:
        if not st.job_id or st.started_monotonic <= 0:
            continue
        run_for = max(0.0, now - float(st.started_monotonic))
        rem = max(0.0, mean - run_for)
        remc = max(0.0, mean_cons - run_for)
        running_remaining.append((st.job_id, run_for, rem, st.device))
        running_remaining_cons.append((st.job_id, run_for, remc, st.device))

    remaining_work = sum(x[2] for x in running_remaining) + (mean * float(pending_count))
    remaining_work_cons = sum(x[2] for x in running_remaining_cons) + (mean_cons * float(pending_count))

    eta_p50 = remaining_work / float(eff) if eff > 0 else 0.0
    eta_cons = remaining_work_cons / float(eff) if eff > 0 else 0.0

    min_remaining = max((x[2] for x in running_remaining), default=0.0)

    return {
        "elapsed_total_sec": elapsed_total,
        "mean_sec": mean,
        "stddev_sec": std,
        "eta_p50_sec": eta_p50,
        "eta_cons_sec": eta_cons,
        "min_remaining_sec": min_remaining,
        "slow_threshold_sec": slow_threshold,
        "running_details": running_remaining,  # (job_id, run_for, rem, device)
    }



def _render_progress_compact_baseline(
    *, pct: int, completed: int, total: int, elapsed_sec: float, eta_sec: float
) -> str:
    """Render the baseline compact progress line (do not change formatting)."""
    from datetime import datetime as _dt, timedelta as _td

    def _fmt_hms(seconds: float) -> str:
        s = int(max(0, seconds))
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"

    eta_s = _fmt_hms(eta_sec) if eta_sec > 0 else "--:--"
    etc_s = (_dt.now() + _td(seconds=float(eta_sec))).strftime("%H:%M:%S") if eta_sec > 0 else "--:--:--"
    return f"{pct}% ({completed}/{total}) | elapsed {_fmt_hms(elapsed_sec)} | ETA {eta_s} | ETC {etc_s}"



def _render_progress_detailed(
    *, snapshot: Dict[str, Any], eta: Dict[str, Any], conc: int
) -> str:
    """Render a multi-line operator dashboard for --progress-mode detailed."""
    from datetime import datetime as _dt, timedelta as _td

    total = int(snapshot.get("total", 0) or 0)
    completed = int(snapshot.get("completed", 0) or 0)
    pct = int(100 * completed / total) if total else 100

    def _fmt_hms(seconds: float) -> str:
        s = int(max(0, seconds))
        h = s // 3600
        m = (s % 3600) // 60
        sec = s % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{sec:02d}"
        return f"{m:02d}:{sec:02d}"

    elapsed = float(eta.get("elapsed_total_sec", 0.0) or 0.0)
    eta_p50 = float(eta.get("eta_p50_sec", 0.0) or 0.0)
    eta_cons = float(eta.get("eta_cons_sec", 0.0) or 0.0)
    min_rem = float(eta.get("min_remaining_sec", 0.0) or 0.0)

    etc_p50 = (_dt.now() + _td(seconds=float(eta_p50))).strftime("%H:%M:%S") if eta_p50 > 0 else "--:--:--"
    etc_cons = (_dt.now() + _td(seconds=float(eta_cons))).strftime("%H:%M:%S") if eta_cons > 0 else "--:--:--"

    mean = float(eta.get("mean_sec", 0.0) or 0.0)
    std = float(eta.get("stddev_sec", 0.0) or 0.0)
    slow_thr = eta.get("slow_threshold_sec", None)

    lines: List[str] = []
    lines.append("============================================================")
    lines.append(f"PROGRESS {pct:3d}% ({completed}/{total}) | elapsed {_fmt_hms(elapsed)} | slots {conc}")
    lines.append(
        f"ETA P50 {_fmt_hms(eta_p50)} (ETC {etc_p50}) | conservative {_fmt_hms(eta_cons)} (ETC {etc_cons})"
    )
    if slow_thr is not None:
        lines.append(f"Runtime mean {_fmt_hms(mean)} | stddev {_fmt_hms(std)} | slow threshold > {_fmt_hms(float(slow_thr))}")
    else:
        lines.append(f"Runtime mean {_fmt_hms(mean)} | stddev {_fmt_hms(std)} | slow threshold: n/a (need variance)")
    lines.append(f"Lower bound: minimum remaining time {_fmt_hms(min_rem)} (longest running job estimate)")
    lines.append("------------------------------------------------------------")

    # Slot view
    now = float(snapshot.get("now_monotonic", 0.0) or 0.0)
    slot_states: List[SlotState] = list(snapshot.get("slot_states", []))
    # Build quick lookup for slow marking
    slow_set = set()
    if slow_thr is not None:
        for (jid, run_for, _rem, _dev) in eta.get("running_details", []):
            if float(run_for) > float(slow_thr):
                slow_set.add(str(jid))

    for st in slot_states:
        if st.job_id:
            run_for = max(0.0, now - float(st.started_monotonic))
            tag = " [SLOW]" if st.job_id in slow_set else ""
            dev = f" | {st.device}" if st.device else ""
            lines.append(f"Slot {st.slot_id}: {st.job_id}{tag}{dev} (running {_fmt_hms(run_for)})")
        else:
            lines.append(f"Slot {st.slot_id}: idle")

    # GPU view (based on assigned devices, no querying)
    gpu_counts: Dict[str, int] = {}
    for st in slot_states:
        if st.job_id and st.device:
            gpu_counts[st.device] = gpu_counts.get(st.device, 0) + 1

    # Normalize "cuda:0" -> "GPU 0" display.
    if gpu_counts:
        lines.append("------------------------------------------------------------")
        # Keep stable ordering
        for dev in sorted(gpu_counts.keys()):
            label = dev
            m = re.match(r"cuda:(\d+)$", dev)
            if m:
                label = f"GPU {m.group(1)}"
            lines.append(f"{label}: {gpu_counts[dev]} job(s) running")
    lines.append("============================================================")
    return "\n".join(lines)


artifact_manifest_rel: str

