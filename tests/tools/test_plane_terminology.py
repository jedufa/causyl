from __future__ import annotations

from pathlib import Path

from tools.bin import check_plane_terminology as checker


def test_flags_control_plane_in_docs(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "docs" / "note.md"
    target.write_text("Use the control plane for operator work.\n", encoding="utf-8")

    violations = checker.find_violations(tmp_path, ["docs/**/*.md"])

    assert len(violations) == 1
    assert violations[0]["path"] == "docs/note.md"
    assert violations[0]["line"] == 1


def test_allows_technical_path_exception(tmp_path: Path) -> None:
    (tmp_path / "docs").mkdir(parents=True, exist_ok=True)
    target = tmp_path / "docs" / "note.md"
    target.write_text("Technical code/module paths such as infra/controlplane/ keep the legacy wording.\n", encoding="utf-8")

    violations = checker.find_violations(tmp_path, ["docs/**/*.md"])

    assert violations == []


def test_current_operator_facing_docs_use_plane_terminology() -> None:
    violations = checker.find_violations(Path(__file__).resolve().parents[2], checker.DEFAULT_TARGETS)

    assert violations == []
