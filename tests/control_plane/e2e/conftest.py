from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def e2e_runtime_root(tmp_path: Path) -> Path:
    return tmp_path / "action_plane_e2e_runtime"
