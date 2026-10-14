"""tests/control_plane/webui/test_webui_inbox_queue.py

Phase 5d WebUI: inbox queue visibility.

This validates the Tier-1 "Upcoming commands queue" implementation, which is a
read-only view of JSON files present under <run>/inbox/.

Key requirements:
- Ignore *.tmp (no partial file exposure)
- Do not follow symlinks
- Best-effort ack mapping via viz/applied_commands_log.jsonl

Last modified: 2026-02-26 Europe/Vienna
"""

from __future__ import annotations

import json
from pathlib import Path


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_build_inbox_queue_lists_and_maps_ack(tmp_path: Path) -> None:
    from infra.webui.server import WebUIConfig, _ServerState, build_inbox_queue

    sim_root = tmp_path / "run"
    (sim_root / "inbox").mkdir(parents=True)
    (sim_root / "viz").mkdir(parents=True)

    cmd1 = {
        "schema_version": "cmd_v2",
        "command_type": "TraitInjectionCommand",
        "command_uuid": "u1",
        "target_generation": 5,
        "trait_injection": {
            "schema_version": "trait_injection_v2",
            "duration_generations": 2,
            "apply_phase": "post-reproduction-pre-selection",
            "locus_tags": ["aggression_bias"],
            "allele_values": {"aggression_bias": 0.5},
            "target_agent_ids": [],
        },
    }

    _write_json(sim_root / "inbox" / "gen000005__TraitInjectionCommand__u1.json", cmd1)

    # tmp file must be ignored.
    (sim_root / "inbox" / "gen000006__X__u2.json.tmp").write_text("{}\n", encoding="utf-8")

    # invalid json should be surfaced as parse_error.
    (sim_root / "inbox" / "gen000007__X__bad.json").write_text("{", encoding="utf-8")

    # applied_commands_log acknowledges u1.
    (sim_root / "viz" / "applied_commands_log.jsonl").write_text(
        json.dumps({"command_uuid": "u1", "ack_status": "APPLIED"}) + "\n",
        encoding="utf-8",
    )

    st = _ServerState(WebUIConfig(runs_root=tmp_path))
    out = build_inbox_queue(st, sim_root, limit=50)
    assert out["schema_version"] == "inbox_queue_v1"

    items = out["items"]
    names = [it["filename"] for it in items]
    assert "gen000005__TraitInjectionCommand__u1.json" in names
    assert "gen000006__X__u2.json.tmp" not in names
    assert "gen000007__X__bad.json" in names

    good = next(it for it in items if it.get("command_uuid") == "u1")
    assert good["ack_status"] == "APPLIED"
    assert good["queue_status"] == "ACKED"
    assert good["apply_phase"] == "post-reproduction-pre-selection"
    assert good["duration_generations"] == 2

    bad = next(it for it in items if it.get("filename") == "gen000007__X__bad.json")
    assert bad["parse_error"] is True
    assert bad["queue_status"] == "INVALID"
