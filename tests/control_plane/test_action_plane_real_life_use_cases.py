"""Compatibility shim.

The real-life use-case suite lives under ``tests/control_plane/e2e/`` so it
runs through the actual end-to-end harness.

This module remains only to preserve historical import paths and intentionally
defines no pytest tests.
"""

from tests.control_plane.e2e.test_action_plane_real_life_use_cases import SCENARIOS

__all__ = ["SCENARIOS"]
