"""tests/control_plane/webui/test_webui_ui_meta.py

Phase 5d Web UI: UI-owned metadata tests.

These tests are intentionally small and fast:
- ui_meta.json label/tags are written atomically under <run_root>/ui/
- clearing label via empty string behaves as expected

"""

from __future__ import annotations

from pathlib import Path

from infra.webui.server import read_ui_meta, write_ui_meta


def test_write_ui_meta_sets_label_and_tags(tmp_path: Path) -> None:
    run_root = tmp_path / "runA"
    run_root.mkdir(parents=True, exist_ok=True)

    meta = write_ui_meta(run_root, label="My Run", tags=["alpha", "beta"])
    assert meta["label"] == "My Run"
    assert meta["tags"] == ["alpha", "beta"]

    on_disk = read_ui_meta(run_root)
    assert on_disk["label"] == "My Run"
    assert on_disk["tags"] == ["alpha", "beta"]


def test_write_ui_meta_clear_label(tmp_path: Path) -> None:
    run_root = tmp_path / "runB"
    run_root.mkdir(parents=True, exist_ok=True)

    write_ui_meta(run_root, label="X", tags=["t"])
    write_ui_meta(run_root, label="")  # empty string clears label

    on_disk = read_ui_meta(run_root)
    assert on_disk["label"] is None
    assert on_disk["tags"] == ["t"]
