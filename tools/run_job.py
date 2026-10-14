#!/usr/bin/env python3
"""tools.run_job

Public module entrypoint for the Phase-1 job wrapper.

Why this file exists
--------------------
Some tooling and UI paths invoke the wrapper via:

    python -m tools.run_job ...

In baseline-233, a compatibility shim exists under tools/manifest/run_job.py,
but that is *not* importable as tools.run_job, so callers that rely on the
public module path break.

This file restores the expected entrypoint without changing wrapper behavior.

Implementation
--------------
Delegates to: tools.core.run_job.main
"""

from __future__ import annotations


def main() -> int:
    """CLI entrypoint.

    Returns
    -------
    int
        Process exit code.
    """
    from tools.core.run_job import main as _main

    return int(_main())


if __name__ == "__main__":
    raise SystemExit(main())
