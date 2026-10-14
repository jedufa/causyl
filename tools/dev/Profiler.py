#!/usr/bin/env python3
"""
engine.bin.Profiler

This file used to contain raw shell snippets, which breaks Python syntax checks.

If you want profiling, run something like:

    python -m cProfile -o prof.out -m engine.main --help

Then inspect:

    python - <<'EOF'
    import pstats
    p = pstats.Stats("prof.out")
    p.strip_dirs().sort_stats("cumtime").print_stats(25)
    EOF
"""

from __future__ import annotations

import sys


def main() -> int:
    """Print basic profiling instructions."""
    sys.stdout.write(__doc__ or "")
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
