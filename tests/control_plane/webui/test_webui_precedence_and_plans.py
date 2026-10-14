import json
from pathlib import Path

import pytest

from infra.webui.server import (
    EpiAvSeriesCache,
    create_export_zip,
    create_planned_run,
    normalize_run_key,
    read_determinism_status,
    read_run_status,
    select_epiav_source,
)


def _write_json(p: Path, obj) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")


def test_run_status_and_determinism_precedence(tmp_path: Path) -> None:
    sim_root = tmp_path / "run"
    (sim_root / "viz").mkdir(parents=True)

    _write_json(
        sim_root / "run_status.json",
        {
            "schema_version": "run_status_v1",
            "status": "running",
            "current_generation": 1,
            "target_generation": 10,
            "last_update_timestamp": "2026-02-25T00:00:00Z",
            "last_error": None,
        },
    )
    _write_json(
        sim_root / "viz" / "run_status.json",
        {
            "schema_version": "run_status_v1",
            "status": "running",
            "current_generation": 2,
            "target_generation": 10,
            "last_update_timestamp": "2026-02-25T00:00:00Z",
            "last_error": None,
        },
    )

    obj, src = read_run_status(sim_root)
    assert obj is not None
    assert obj["current_generation"] == 2
    assert src is not None and "viz" in src

    _write_json(
        sim_root / "run_determinism_status.json",
        {
            "schema_version": "run_det_status_v1",
            "strict_mode": False,
            "phase4_last_result": "FAIL",
            "last_verified_generation": 5,
            "last_update_timestamp": "2026-02-25T00:00:00Z",
        },
    )
    _write_json(
        sim_root / "viz" / "run_determinism_status.json",
        {
            "schema_version": "run_det_status_v1",
            "strict_mode": True,
            "phase4_last_result": "PASS",
            "last_verified_generation": 6,
            "last_update_timestamp": "2026-02-25T00:00:00Z",
        },
    )

    dobj, dsrc = read_determinism_status(sim_root)
    assert dobj is not None
    assert dobj["phase4_last_result"] == "PASS"
    assert dsrc is not None and "viz" in dsrc


def test_epiav_source_precedence(tmp_path: Path) -> None:
    sim_root = tmp_path / "run"
    (sim_root / "viz").mkdir(parents=True)

    (sim_root / "epi_av_timeseries.csv").write_text("generation,epi,av\n1,0.1,0.2\n", encoding="utf-8")
    (sim_root / "viz" / "epi_av_summary.csv").write_text("generation,epi,av\n1,0.9,0.8\n2,0.95,0.85\n", encoding="utf-8")

    src = select_epiav_source(sim_root)
    assert src["kind"] == "csv"
    assert "viz" in src["path"]

    # Remove viz csv, should fall back to root csv.
    (sim_root / "viz" / "epi_av_summary.csv").unlink()
    src2 = select_epiav_source(sim_root)
    assert src2["kind"] == "csv"
    assert src2["path"].endswith("epi_av_timeseries.csv")

    # Remove csvs, fall back to json_latest.
    (sim_root / "epi_av_timeseries.csv").unlink()
    _write_json(sim_root / "epi_v2.json", {"schema_version": "epi_v2", "generation": 1, "epi": 0.11})
    _write_json(sim_root / "av_v2.json", {"schema_version": "av_v2", "generation": 1, "av": 0.22})
    src3 = select_epiav_source(sim_root)
    assert src3["kind"] == "json_latest"


def test_epiav_source_prefers_timeseries_when_summary_is_singleton(tmp_path: Path) -> None:
    sim_root = tmp_path / "run"
    (sim_root / "viz").mkdir(parents=True)

    # Summary has only one data row (cadence artifact early in run).
    (sim_root / "viz" / "epi_av_summary.csv").write_text("generation,epi,av\n10,0.9,\n", encoding="utf-8")
    # Timeseries has richer continuity.
    (sim_root / "epi_av_timeseries.csv").write_text(
        "generation,epi,av\n1,0.1,\n2,0.2,\n3,0.3,\n",
        encoding="utf-8",
    )

    src = select_epiav_source(sim_root)
    assert src["kind"] == "csv"
    assert src["path"].endswith("epi_av_timeseries.csv")


def test_incremental_csv_tail_ignores_partial_lines(tmp_path: Path) -> None:
    p = tmp_path / "epi_av_summary.csv"
    p.write_text("generation,epi,av\n1,0.1,0.2\n", encoding="utf-8")

    cache = EpiAvSeriesCache(p)
    cache.update()
    assert cache.latest() == {"generation": 1, "epi": 0.1, "av": 0.2}
    assert cache.cursor.provisional is False

    # Append a partial line (no newline)
    with open(p, "a", encoding="utf-8") as f:
        f.write("2,0.2,0.3")
    cache.update()

    # Still only first point.
    assert cache.latest() == {"generation": 1, "epi": 0.1, "av": 0.2}
    assert cache.cursor.provisional is True

    # Finish the line.
    with open(p, "a", encoding="utf-8") as f:
        f.write("\n")
    cache.update()

    assert cache.latest() == {"generation": 2, "epi": 0.2, "av": 0.3}
    assert cache.cursor.provisional is False

    # Append a malformed row.
    with open(p, "a", encoding="utf-8") as f:
        f.write("oops\n")
    cache.update()

    # Malformed row ignored.
    assert cache.latest()["generation"] == 2
    assert cache.cursor.parse_errors >= 1


def test_export_zip_includes_viz_and_ui_excludes_inbox(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    run_root = runs_root / "r1"
    sim_root = run_root

    (run_root / "viz").mkdir(parents=True)
    (run_root / "ui").mkdir(parents=True)
    (run_root / "inbox").mkdir(parents=True)

    _write_json(run_root / "viz" / "run_status.json", {"schema_version": "run_status_v1", "status": "running"})
    (run_root / "viz" / "epi_av_summary.csv").write_text("generation,epi,av\n1,0.1,0.2\n", encoding="utf-8")
    (run_root / "ui" / "notes.md").write_text("hello\n", encoding="utf-8")
    (run_root / "inbox" / "gen000001__x.json").write_text("{}\n", encoding="utf-8")

    res = create_export_zip("r1", run_root, sim_root, include_checkpoints=False)
    assert res["status"] == "ok"

    zip_path = run_root / res["relpath"]
    assert zip_path.exists()

    import zipfile

    with zipfile.ZipFile(zip_path, "r") as z:
        names = z.namelist()

        # Export must always include a self-describing manifest.
        assert "analysis/snapshot_manifest.json" in names

    assert "viz/run_status.json" in names
    assert "ui/notes.md" in names

    # The inbox is intentionally excluded.
    assert not any(n.startswith("inbox/") for n in names)

    # Exports should not include exports (no recursion)
    assert not any(n.startswith("ui/exports/") for n in names)


def test_plan_run_creates_new_folder_without_mutating_parent(tmp_path: Path) -> None:
    runs_root = tmp_path / "runs"
    parent_root = runs_root / "parent"
    sim_root = parent_root

    (sim_root / "viz").mkdir(parents=True)

    # Parent determinism PASS.
    _write_json(
        sim_root / "viz" / "run_determinism_status.json",
        {
            "schema_version": "run_det_status_v1",
            "strict_mode": True,
            "phase4_last_result": "PASS",
            "last_verified_generation": 10,
            "last_update_timestamp": "2026-02-25T00:00:00Z",
        },
    )

    # Parent run_status with target.
    _write_json(
        sim_root / "viz" / "run_status.json",
        {
            "schema_version": "run_status_v1",
            "status": "running",
            "current_generation": 10,
            "target_generation": 100,
            "last_update_timestamp": "2026-02-25T00:00:00Z",
            "last_error": None,
        },
    )

    # Parent branch_meta.
    _write_json(
        sim_root / "branch_meta.json",
        {
            "schema_version": "branch_meta_v1",
            "campaign_id": "camp",
            "branch_id": "branch-parent",
            "parent_branch_id": None,
            "root_branch_id": "branch-parent",
            "parent_checkpoint_id": None,
            "root_checkpoint_id": "ckpt-root",
            "branch_depth": 0,
            "created_at_generation": 0,
            "created_at_utc": "2026-02-25T00:00:00Z",
            "baseline_config_hash": "sha256:deadbeef",
            "code_commit_hash": "abc",
            "submission_sequence_start": 0,
            "submission_sequence_next": 1,
            "notes": "",
        },
    )

    # Checkpoint index.
    ck_rel = "checkpoint_bundles_v2/bundle_gen000010"
    _write_json(
        sim_root / "viz" / "checkpoints_index.json",
        {
            "schema_version": "checkpoints_index_v1",
            "checkpoints": [
                {"generation": 10, "checkpoint_id": "ckpt-10", "relative_path": ck_rel, "size_bytes": 123}
            ],
        },
    )

    # Checkpoint bundle with metadata.
    bundle_dir = sim_root / ck_rel
    bundle_dir.mkdir(parents=True)
    _write_json(
        bundle_dir / "metadata.json",
        {
            "schema_version": "checkpoint_bundle_v2",
            "checkpoint_id": "ckpt-10",
            "generation_index": 10,
            "branch_id": "branch-parent",
            "campaign_id": "camp",
            "code_commit_hash": "abc",
            "control_plane": {"schema_version": "control_plane_meta_v1", "submission_sequence_number": 5},
        },
    )

    # Snapshot parent contents.
    before = sorted([p.relative_to(parent_root).as_posix() for p in parent_root.rglob("*") if p.is_file()])

    res = create_planned_run(
        runs_root=runs_root,
        parent_run_key="parent",
        checkpoint_id="ckpt-10",
        checkpoint_generation=None,
        run_kind="branch",
        target_generation=200,
        label="Child",
        tags=["t1"],
        changesets=[],
        override_determinism_fail=False,
    )

    assert res["status"] == "ok"
    new_key = res["new_run_key"]
    assert normalize_run_key(new_key).startswith("planned/")

    # Parent unchanged.
    after = sorted([p.relative_to(parent_root).as_posix() for p in parent_root.rglob("*") if p.is_file()])
    assert before == after

    # Child contains required files.
    child_root = runs_root / Path(new_key)
    assert (child_root / "ui" / "branch_meta.json").exists()
    assert (child_root / "ui" / "branch_plan.json").exists()
    assert (child_root / "viz" / "run_status.json").exists()
    assert (child_root / "run_manifest.json").exists()
    assert (child_root / "resume_from.json").exists()
