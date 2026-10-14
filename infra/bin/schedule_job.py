from __future__ import annotations

"""Backward-compatible wrapper for the scheduled job CLI.

Historically the import path used by tests and a few scripts was
``tools.bin.schedule_job``. The canonical implementation moved to
``tools.bin.scheduler_schedule_job``. Keep this thin wrapper so the import
surface stays stable.
"""

from .scheduler_schedule_job import *  # noqa: F401,F403
from .scheduler_schedule_job import main


if __name__ == "__main__":
    raise SystemExit(main())
