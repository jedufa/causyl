from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def assert_projection_equivalent(bundle: dict[str, Any]) -> None:
    assert bundle["projection_equivalent"] is True
    assert bundle["surfaces"]["api"] == bundle["surfaces"]["watch"]
    assert bundle["surfaces"]["watch"] == bundle["surfaces"]["web"]
    assert bundle["surfaces"]["web"] == bundle["surfaces"]["mobile"]


def assert_no_fake_promotion(*, parent_meta_path: Path, intent_meta_path: Path) -> None:
    assert read_json(parent_meta_path)["status"] == "draft"
    assert read_json(intent_meta_path)["status"] == "draft"
