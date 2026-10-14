"""Compatibility shim for pipeline benchmark helpers."""

from pathlib import Path

_TARGET_PACKAGE_DIR = Path(__file__).resolve().parents[2] / "infra" / "pipeline" / "benchmark"
__path__ = [str(_TARGET_PACKAGE_DIR)]
