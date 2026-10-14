from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from infra.webui import server_timeline as timeline


def _state_stub() -> SimpleNamespace:
    return SimpleNamespace(
        lock=threading.Lock(),
        jsonl_caches={},
        metrics_caches={},
        epiav_caches={},
    )


def test_timeline_summary_no_data_exposes_ui_compatible_bins_shape(tmp_path: Path) -> None:
    sim_root = tmp_path / "run_empty"
    sim_root.mkdir(parents=True, exist_ok=True)

    out = timeline.build_timeline_summary(_state_stub(), sim_root, bins=64)
    assert str(out.get("schema_version") or "") == "timeline_summary_v1"
    assert isinstance(out.get("bins"), list)
    assert out.get("bins") == []
    assert int(out.get("bin_count") or 0) == 64
    assert int(out.get("generation_max") or -1) == -1


def test_timeline_summary_maps_series_into_ui_bins_payload(tmp_path: Path) -> None:
    sim_root = tmp_path / "run_timeline"
    events_dir = sim_root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    rows = [
        {
            "schema_version": "command_event_v1",
            "generation_applied": 3,
            "command_uuid": "u1",
            "command_payload": {"command_type": "PressureUpdateCommand"},
        },
        {
            "schema_version": "command_event_v1",
            "generation_applied": 5,
            "command_uuid": "u2",
            "command_payload": {"command_type": "TraitInjectionCommand"},
        },
    ]
    with open(events_dir / "command_events.jsonl", "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, sort_keys=True))
            f.write("\n")

    (sim_root / "metrics.csv").write_text(
        "generation,shock_active\n"
        "0,0\n"
        "4,1\n"
        "5,0\n",
        encoding="utf-8",
    )

    out = timeline.build_timeline_summary(_state_stub(), sim_root, bins=8)
    bins = out.get("bins")
    assert isinstance(bins, list)
    assert bins, "expected at least one timeline bin"
    assert int(out.get("generation_max") or -1) >= 5
    assert any(int((b.get("control_counts") or {}).get("system") or 0) > 0 for b in bins)
    assert any(bool((b.get("pressure_flags") or {}).get("shock")) for b in bins)
