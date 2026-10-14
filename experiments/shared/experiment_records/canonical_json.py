"""Canonical JSON helpers for deterministic experiment records."""

from __future__ import annotations

import json
from typing import Any


def dumps_canonical(obj: Any) -> str:
    """Serialize an object into canonical JSON text."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def dump_canonical_to_file(obj: Any, path: str) -> None:
    """Write canonical JSON to disk with a trailing newline."""
    with open(path, "w", encoding="utf-8") as f:
        f.write(dumps_canonical(obj))
        f.write("\n")
