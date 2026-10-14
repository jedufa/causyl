#!/usr/bin/env python3
"""forensic_first_divergence.py

Purpose:
    Parse two EIEL *_e2e_*.log files (fresh vs resume) and find the earliest
    deterministic divergence using existing trace lines:
      - [BIRTH_CALLSITE_TRACE] <json>
      - [BIRTH_INTERNAL_TRACE] <json>
      - [BIRTH_RNG_TRACE] <json>
      - [TICK_TRACE] <json>   (if present)

Design constraints:
    - Must not introduce any RNG calls (pure log parsing).
    - Fail-fast: first mismatch reported, exit code 2.
    - Minimal dependencies: stdlib only.

Usage:
    infra/bin/forensic_first_divergence.py --fresh _e2e_fresh.log --resume _e2e_resume.log
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple


TRACE_PREFIXES = [
    "[BIRTH_CALLSITE_TRACE]",
    "[BIRTH_INTERNAL_TRACE]",
    "[BIRTH_RNG_TRACE]",
    "[TICK_TRACE]",
]


@dataclass(frozen=True)
class TraceEvent:
    """A single parsed trace event from an e2e log."""
    kind: str
    gen: Optional[int]
    tick: Optional[int]
    stage: Optional[str]
    payload: Dict[str, Any]
    raw: str


def _iter_trace_events(lines: Iterable[str]) -> Iterator[TraceEvent]:
    """Yield TraceEvent objects from log lines."""
    for line in lines:
        line = line.rstrip("\n")
        for pfx in TRACE_PREFIXES:
            if pfx in line:
                try:
                    after = line.split(pfx, 1)[1].strip()
                    payload = json.loads(after) if after else {}
                except Exception:
                    payload = {"_parse_error": True}
                gen = payload.get("gen", payload.get("generation"))
                tick = payload.get("tick")
                stage = payload.get("stage")
                yield TraceEvent(
                    kind=pfx.strip("[]"),
                    gen=gen if isinstance(gen, int) else None,
                    tick=tick if isinstance(tick, int) else None,
                    stage=stage if isinstance(stage, str) else None,
                    payload=payload if isinstance(payload, dict) else {"_non_dict": True},
                    raw=line,
                )
                break


def _load_events(path: str) -> List[TraceEvent]:
    """Load and parse events from a log file."""
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return list(_iter_trace_events(f))


def _event_key(ev: TraceEvent) -> Tuple:
    """Stable key for aligning two event streams."""
    return (ev.kind, ev.gen, ev.tick, ev.stage)


def _diff_payload(a: Dict[str, Any], b: Dict[str, Any]) -> Optional[Tuple[str, Any, Any]]:
    """Return first differing key/value pair, or None if equal."""
    keys = sorted(set(a.keys()) | set(b.keys()))
    for k in keys:
        if a.get(k) != b.get(k):
            return (k, a.get(k), b.get(k))
    return None


def _find_first_divergence(fresh: List[TraceEvent], resume: List[TraceEvent]) -> Optional[str]:
    """Find the first divergence between two event streams.

    Strategy:
        - Align by exact stream position first. If keys match, compare payload.
        - If keys differ, report the first key mismatch with nearby context.
    """
    n = min(len(fresh), len(resume))
    for i in range(n):
        a = fresh[i]
        b = resume[i]
        if _event_key(a) != _event_key(b):
            return (
                "EVENT_KEY_MISMATCH\n"
                f" index={i}\n"
                f" fresh_key={_event_key(a)}\n"
                f" resume_key={_event_key(b)}\n"
                f" fresh_raw={a.raw}\n"
                f" resume_raw={b.raw}\n"
            )
        diff = _diff_payload(a.payload, b.payload)
        if diff is not None:
            k, av, bv = diff
            return (
                "PAYLOAD_MISMATCH\n"
                f" index={i}\n"
                f" key={_event_key(a)}\n"
                f" field={k}\n"
                f" fresh_value={av!r}\n"
                f" resume_value={bv!r}\n"
                f" fresh_raw={a.raw}\n"
                f" resume_raw={b.raw}\n"
            )
    if len(fresh) != len(resume):
        return (
            "STREAM_LENGTH_MISMATCH\n"
            f" fresh_events={len(fresh)}\n"
            f" resume_events={len(resume)}\n"
        )
    return None


def main() -> int:
    """CLI entrypoint."""
    ap = argparse.ArgumentParser()
    ap.add_argument("--fresh", required=True, help="Path to _e2e_fresh.log")
    ap.add_argument("--resume", required=True, help="Path to _e2e_resume.log")
    ap.add_argument("--out", default=None, help="Write report to this path (default: stdout)")
    args = ap.parse_args()

    fresh_events = _load_events(args.fresh)
    resume_events = _load_events(args.resume)

    report = _find_first_divergence(fresh_events, resume_events)
    if report is None:
        msg = "NO_DIVERGENCE_FOUND (within parsed trace events)\n"
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                f.write(msg)
        else:
            sys.stdout.write(msg)
        return 0

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
    else:
        sys.stdout.write(report)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
