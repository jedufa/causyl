from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture
def first_wave_root(tmp_path: Path) -> Path:
    return tmp_path
