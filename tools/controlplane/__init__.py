"""Compatibility namespace for scheduler/runtime infrastructure.

Canonical location: ``infra/controlplane``.
"""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parents[2] / "infra" / "controlplane")]

