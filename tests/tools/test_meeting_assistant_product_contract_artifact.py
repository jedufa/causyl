from __future__ import annotations

from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _artifact_path() -> Path:
    return (
        _repo_root()
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "active"
        / "2026"
        / "05"
        / "PIS-20260519-131224Z-40FA__clarify-meeting-assistant-product-contract-operator-and-surface"
        / "artifacts"
        / "clarification"
        / "meeting_assistant_product_contract.md"
    )


def test_meeting_assistant_contract_artifact_locks_boundary_and_flow() -> None:
    text = _artifact_path().read_text(encoding="utf-8")

    assert "**PIS UID:** PIS-20260519-131224Z-40FA" in text
    assert "- Primary operator: `operator on phone`" in text
    assert "- Primary runtime surface: `standalone intake helper`" in text
    assert "do **not** implement this as part of the current plane / lab-assistant product contract in this repo" in text
    assert "1. Entry:" in text
    assert "2. Structuring pass:" in text
    assert "3. Review and edit:" in text
    assert "4. Export:" in text
    assert "Markdown or task-list format" in text
    assert "mark items as `confirmed` or `uncertain`" in text
    assert "## Explicit Non-Goals" in text
    assert "Do not treat this as equivalent to the current lab-assistant mission." in text
    assert "Do not equate `task-list format` export with writing tasks directly into the plane." in text
    assert "Executable decomposition inside this repo is still blocked." in text
