#!/usr/bin/env python3
"""tools/verify/merge_resume_csv.py

Merge a "reference" CSV (gen 0..G) with a "resume" CSV that starts at a later
start_gen (e.g., gen=12..G) into a single full-length CSV.

This is a harness-level utility used by Phase 4 determinism tests.
It avoids requiring simulation code to rewrite historical rows after resume.

Behavior:
- Reads the header from the reference CSV and writes it to output.
- Copies reference rows with gen < start_gen.
- Appends all resume rows (expected to start at start_gen).
- Validates:
  - Header equality (exact match)
  - First column contains integer generation index
  - Resume first row gen == start_gen

Exit codes:
- 0 on success
- 2 on validation failure

"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    """Parse CLI arguments."""
    p = argparse.ArgumentParser()
    p.add_argument("--ref", required=True, help="Reference CSV path (full run).")
    p.add_argument("--resume", required=True, help="Resume CSV path (starts at start_gen).")
    p.add_argument("--start-gen", required=True, type=int, help="Start generation for resume.")
    p.add_argument("--out", required=True, help="Output CSV path.")
    return p.parse_args()


def die(msg: str) -> None:
    """Print an error and exit."""
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(2)


def read_header(path: Path) -> list[str]:
    """Read CSV header row."""
    with path.open("r", newline="") as f:
        r = csv.reader(f)
        header = next(r, None)
        if header is None:
            die(f"Empty CSV: {path}")
        return header


def iter_rows(path: Path):
    """Iterate CSV rows excluding header."""
    with path.open("r", newline="") as f:
        r = csv.reader(f)
        _ = next(r, None)
        for row in r:
            if not row:
                continue
            yield row


def gen_of_row(row: list[str], path: Path) -> int:
    """Parse generation index from first column."""
    try:
        return int(row[0])
    except Exception as e:
        die(f"Non-integer gen in {path}: first_col={row[0]!r} ({e})")


def main() -> int:
    """Entry point."""
    args = parse_args()
    ref_p = Path(args.ref)
    res_p = Path(args.resume)
    out_p = Path(args.out)

    if not ref_p.exists():
        die(f"Missing ref CSV: {ref_p}")
    if not res_p.exists():
        die(f"Missing resume CSV: {res_p}")

    ref_h = read_header(ref_p)
    res_h = read_header(res_p)
    if ref_h != res_h:
        die("Header mismatch between ref and resume CSV")

    start_gen = int(args.start_gen)

    # Validate resume starts at start_gen
    res_iter = iter_rows(res_p)
    first_res = next(res_iter, None)
    if first_res is None:
        die(f"Resume CSV has no rows: {res_p}")
    first_gen = gen_of_row(first_res, res_p)
    if first_gen != start_gen:
        die(f"Resume first gen {first_gen} != expected start_gen {start_gen}")

    out_p.parent.mkdir(parents=True, exist_ok=True)

    with out_p.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(ref_h)

        # Copy ref rows with gen < start_gen
        for row in iter_rows(ref_p):
            g = gen_of_row(row, ref_p)
            if g >= start_gen:
                break
            w.writerow(row)

        # Write the first resume row, then remaining
        w.writerow(first_res)
        for row in res_iter:
            w.writerow(row)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
