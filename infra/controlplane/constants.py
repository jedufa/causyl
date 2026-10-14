"""infra.controlplane.constants

Centralized constants for the lightweight operational control-plane.

This module intentionally contains only simple, stable strings so other
modules can import it without creating circular dependencies.

"""

from __future__ import annotations

from pathlib import Path


# Root folder under runs_root that stores durable, restart-safe control records.
CONTROL_ROOT_REL = Path("_control")

# Subfolder for manifest-runner job records.
MANIFEST_JOBS_DIR_REL = CONTROL_ROOT_REL / "manifest_jobs"

# Per-manifest report directory control folder.
MANIFEST_REPORT_CONTROL_DIRNAME = ".control"

# Sentinel filenames (audit artifacts).
STOP_SENTINEL_NAME = "STOP"
PAUSE_SENTINEL_NAME = "PAUSE"
