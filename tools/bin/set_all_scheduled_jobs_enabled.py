from __future__ import annotations

"""Backward-compatible wrapper for the bulk scheduled job toggle CLI.

The maintained implementation lives in ``tools.bin.scheduler_set_all_scheduled_jobs_enabled``.
This wrapper preserves the older import path that parts of the test suite still
exercise.
"""

from .scheduler_set_all_scheduled_jobs_enabled import *  # noqa: F401,F403
from .scheduler_set_all_scheduled_jobs_enabled import main


if __name__ == "__main__":
    raise SystemExit(main())
