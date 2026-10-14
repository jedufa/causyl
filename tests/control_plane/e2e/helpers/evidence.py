from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def collect_evidence(*, receipt_path: Path) -> Dict[str, Any]:
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    report = json.loads(Path(receipt["report_path"]).read_text(encoding="utf-8"))
    snapshot = json.loads(Path(receipt["snapshot_path"]).read_text(encoding="utf-8"))
    stimulus = json.loads(Path(receipt["stimulus_path"]).read_text(encoding="utf-8"))
    return {
        "receipt": receipt,
        "report": report,
        "snapshot": snapshot,
        "stimulus": stimulus,
    }
