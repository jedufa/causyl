from __future__ import annotations

import json
from typing import Any


def dumps_canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
