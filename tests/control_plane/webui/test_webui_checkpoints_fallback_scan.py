"""tests/control_plane/webui/test_webui_checkpoints_fallback_scan.py

Phase 5d WebUI: checkpoint explorer fallback scan.

Some runs produce an empty viz/checkpoints_index.json even when legacy checkpoint
folders exist under <run>/checkpoints/ckpt_genXXXXXX/.

Tier-1 UI should still surface these checkpoints as a best-effort read-only scan.

Last modified: 2026-02-27
"""

from __future__ import annotations

import json
from pathlib import Path


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_summarize_checkpoints_scans_when_index_empty(tmp_path: Path) -> None:
    from infra.webui.server import _summarize_checkpoints

    sim_root = tmp_path / "run"
    (sim_root / "viz").mkdir(parents=True)

    # Legacy checkpoints layout.
    (sim_root / "checkpoints" / "ckpt_gen000020").mkdir(parents=True)
    (sim_root / "checkpoints" / "ckpt_gen000030").mkdir(parents=True)

    # Engine-produced index exists but is empty.
    _write_json(
        sim_root / "viz" / "checkpoints_index.json",
        {"schema_version": "checkpoints_index_v1", "checkpoints": []},
    )

    cps = _summarize_checkpoints(sim_root)
    assert [c.get("generation") for c in cps] == [20, 30]
    assert [c.get("checkpoint_id") for c in cps] == ["ckpt_gen000020", "ckpt_gen000030"]
