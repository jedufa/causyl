import json
from pathlib import Path

import pytest

from infra.webui import server


def test_filter_applied_command_items_basic_windowing():
    items = [
        {"command_uuid": f"u{i}", "ack_status": "APPLIED", "applied_generation": i}
        for i in range(10)
    ]

    out, meta = server.filter_applied_command_items(items, limit=3)
    assert [x["applied_generation"] for x in out] == [7, 8, 9]
    assert meta["min_generation"] == 7
    assert meta["max_generation"] == 9

    out, meta = server.filter_applied_command_items(items, after_gen=5, limit=3)
    assert [x["applied_generation"] for x in out] == [6, 7, 8]
    assert meta["min_generation"] == 6
    assert meta["max_generation"] == 8

    out, meta = server.filter_applied_command_items(items, before_gen=5, limit=3)
    assert [x["applied_generation"] for x in out] == [2, 3, 4]
    assert meta["min_generation"] == 2
    assert meta["max_generation"] == 4

    out, meta = server.filter_applied_command_items(items, after_gen=5, before_gen=9, limit=10)
    assert [x["applied_generation"] for x in out] == [6, 7, 8]
    assert meta["min_generation"] == 6
    assert meta["max_generation"] == 8


def test_build_run_summary_includes_branch_from_generation(tmp_path: Path):
    run_root = tmp_path / "runs" / "run_a"
    run_root.mkdir(parents=True)

    bm = {
        "branch_id": "b_child",
        "root_branch_id": "b_root",
        "branch_depth": 1,
        "parent_branch_id": "b_root",
        "parent_checkpoint_id": "ckpt_0001",
        "created_at_generation": 10,
    }
    (run_root / "branch_meta.json").write_text(json.dumps(bm), encoding="utf-8")

    cfg = server.WebUIConfig(runs_root=tmp_path / "runs")
    st = server._ServerState(cfg)

    summ = server.build_run_summary(st, "run_a", run_root, run_root)
    assert summ["lineage"]["branch_from_generation"] == 10
    assert summ["lineage"]["created_at_generation"] == 10
    assert summ["lineage"]["parent_checkpoint_id"] == "ckpt_0001"


def test_wrapper_payload_nested_runs_are_discovered_at_default_depth(tmp_path: Path):
    runs_root = tmp_path / "wrapper_jobs"
    sim_root = runs_root / "wj_001" / "payload" / "e" / "abc123" / "b20260402" / "r" / "r0001_abcd"
    sim_root.mkdir(parents=True)
    (sim_root / "manifest.json").write_text(
        json.dumps({"cli_args": {"generations": 12, "run_id": "r0001_abcd"}}),
        encoding="utf-8",
    )

    rows = list(server.iter_candidate_run_roots(runs_root, scan_depth=3))

    assert rows == [
        (
            "wj_001/payload/e/abc123/b20260402/r/r0001_abcd",
            sim_root,
        )
    ]


def test_build_run_summary_derives_generations_from_artifacts_without_status(tmp_path: Path):
    run_root = tmp_path / "runs" / "run_a"
    run_root.mkdir(parents=True)
    (run_root / "manifest.json").write_text(
        json.dumps({"cli_args": {"generations": 12, "run_id": "run_a"}}),
        encoding="utf-8",
    )
    (run_root / "metrics.csv").write_text(
        "generation,alive\n"
        "0,10\n"
        "7,8\n",
        encoding="utf-8",
    )

    cfg = server.WebUIConfig(runs_root=tmp_path / "runs")
    st = server._ServerState(cfg)

    summ = server.build_run_summary(st, "run_a", run_root, run_root)

    assert summ["current_generation"] == 7
    assert summ["target_generation"] == 12
