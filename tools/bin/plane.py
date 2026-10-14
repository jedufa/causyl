from __future__ import annotations

"""Backward-compatible wrapper for the plane CLI."""

from infra.bin.plane import *  # noqa: F401,F403
from infra.bin.plane import main


if __name__ == "__main__":
    raise SystemExit(main())
