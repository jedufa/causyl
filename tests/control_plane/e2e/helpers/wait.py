from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, Iterable


def wait_for_terminal_receipt(receipt_path: Path, *, terminal_statuses: Iterable[str], timeout_s: float = 5.0, poll_s: float = 0.05) -> Dict[str, Any]:
    deadline = time.time() + timeout_s
    terminal = set(terminal_statuses)
    latest: Dict[str, Any] = {}
    while time.time() < deadline:
        if receipt_path.exists():
            latest = json.loads(receipt_path.read_text(encoding="utf-8"))
            if str(latest.get("status") or "").strip() in terminal:
                return latest
        time.sleep(poll_s)
    raise AssertionError(f"timed out waiting for terminal receipt at {receipt_path}; last={latest}")
