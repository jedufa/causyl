"""infra.controlplane

Small, filesystem-backed operational control-plane helpers.

This package is *not* the simulation ControlPlane (SafeBoundary command WAL etc).
It exists to support operational orchestration around long-running tooling
processes (e.g. manifest runners) in a restart-safe way.

Key properties
--------------
- Disk-backed records (atomic writes)
- No dependency on web server in-memory state
- Best-effort liveness reconciliation via /proc

"""

from __future__ import annotations
