from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))



def _has_token(text: str, tokens: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(token in lowered for token in tokens)


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    del config
    for item in items:
        nodeid = item.nodeid.lower()
        path_text = str(getattr(item, "fspath", "")).lower()
        combined = f"{path_text}::{nodeid}"

        if _has_token(combined, ("regression",)):
            item.add_marker("regression")
        if _has_token(combined, ("smoke", "artifact_smoke_only")):
            item.add_marker("smoke")
        if _has_token(combined, ("e2e", "end_to_end")):
            item.add_marker("e2e")
        if _has_token(combined, ("integration",)):
            item.add_marker("integration")
