#!/usr/bin/env python3
"""tools/verify/phase4_write_determinism_summary.py

Writes the Phase 4 harness JSON summary artifact.

This module is intentionally stdlib-only and CI-friendly.

Output: phase4_determinism_summary.json

Stable JSON requirements:
  - UTF-8
  - sorted keys
  - separators=(',', ':') (no whitespace)

The harness (bash) records:
  - runs.tsv:  label<TAB>path
  - compares.tsv: name<TAB>PASS|FAIL<TAB>detail

The summary consolidates those into a single JSON document.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class CompareRecord:
    """Represents one comparison outcome."""

    name: str
    status: str
    detail: str


def _read_tsv_rows(path: Path, expected_cols: int) -> List[Tuple[str, ...]]:
    """Read a TSV file and return rows.

    Lines that are empty are ignored.
    """
    rows: List[Tuple[str, ...]] = []
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip("\n")
        if not line:
            continue
        parts = line.split("\t")
        if len(parts) < expected_cols:
            # best-effort: pad missing
            parts = parts + [""] * (expected_cols - len(parts))
        rows.append(tuple(parts[:expected_cols]))
    return rows


def build_summary(
    *,
    seed: int,
    gen_total: int,
    checkpoint_interval: int,
    checkpoint_gen: int,
    modes: str,
    runs_tsv: Path,
    compares_tsv: Path,
    fail_rc: int,
    fail_msg: str,
) -> Dict[str, object]:
    """Build the phase4_determinism_summary.json object."""

    runs_rows = _read_tsv_rows(runs_tsv, expected_cols=2)
    cmp_rows = _read_tsv_rows(compares_tsv, expected_cols=3)

    runs: Dict[str, str] = {}
    for label, path in runs_rows:
        if label:
            runs[label] = path

    compares: Dict[str, Dict[str, str]] = {}
    for name, status, detail in cmp_rows:
        if not name:
            continue
        compares[name] = {"status": status, "detail": detail}

    overall_status = "PASS" if fail_rc == 0 else "FAIL"

    return {
        "schema_version": "phase4_determinism_summary_v1",
        "seed": int(seed),
        "G": int(gen_total),
        "K": int(checkpoint_interval),
        "C": int(checkpoint_gen),
        "modes_executed": modes,
        "overall": {"status": overall_status, "fail_rc": int(fail_rc), "fail_msg": fail_msg},
        "runs": runs,
        "compares": compares,
    }


def write_summary(out_path: Path, payload: Dict[str, object]) -> None:
    """Write the summary payload using stable JSON formatting."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False), encoding="utf-8")


def main() -> int:
    """CLI entrypoint."""
    p = argparse.ArgumentParser()
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--seed", required=True, type=int)
    p.add_argument("--gen-total", required=True, type=int)
    p.add_argument("--checkpoint-interval", required=True, type=int)
    p.add_argument("--checkpoint-gen", required=True, type=int)
    p.add_argument("--modes", required=True, type=str)
    p.add_argument("--runs-tsv", required=True, type=Path)
    p.add_argument("--compares-tsv", required=True, type=Path)
    p.add_argument("--fail-rc", required=True, type=int)
    p.add_argument("--fail-msg", required=True, type=str)
    args = p.parse_args()

    payload = build_summary(
        seed=args.seed,
        gen_total=args.gen_total,
        checkpoint_interval=args.checkpoint_interval,
        checkpoint_gen=args.checkpoint_gen,
        modes=args.modes,
        runs_tsv=args.runs_tsv,
        compares_tsv=args.compares_tsv,
        fail_rc=args.fail_rc,
        fail_msg=args.fail_msg,
    )
    write_summary(args.out, payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
