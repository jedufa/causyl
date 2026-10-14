"""Compatibility namespace for host verification/runtime helpers.

Canonical location: ``infra/host``.
"""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[2] / "infra" / "host")]

