"""infra.webui.ledger

Change Ledger backend modules.

This package provides a filesystem-backed, governance-friendly "change ledger"
for PIS items (change proposals/instructions) with:

- Stable UID + human slug
- Workflow status
- Revision lineage (supersedes)
- Regression linkage
- Optional append-only event chain (tamper-evident)
- Best-effort git commit linkage

The WebUI server (infra.webui.server) imports these modules to implement
HTTP API endpoints.
"""

from __future__ import annotations
