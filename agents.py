"""Compatibility namespace for legacy agent imports."""

from pathlib import Path

__path__ = [str(Path(__file__).resolve().parent / "infra" / "agents")]
