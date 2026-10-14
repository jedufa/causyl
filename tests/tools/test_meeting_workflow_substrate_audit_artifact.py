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
        / "PIS-20260519-131225Z-C2AF__audit-reusable-repo-substrate-and-overlap-for-meeting-workflows"
        / "artifacts"
        / "audit"
        / "meeting_workflow_substrate_audit.md"
    )


def test_meeting_workflow_substrate_audit_artifact_locks_reuse_and_gap_claims() -> None:
    text = _artifact_path().read_text(encoding="utf-8")

    assert "**PIS UID:** PIS-20260519-131225Z-C2AF" in text
    assert "- Thin repo-local extension path: `no`" in text
    assert "PIS-20260519-131224Z-40FA" in text
    assert "## Direct Overlap" in text
    assert "`infra/webui/ledger/idea_note_manager.py`" in text
    assert "`infra/webui/ledger/intent_sheet_manager.py`" in text
    assert "`infra/webui/static/mobile_backlog.html`" in text
    assert "`infra/webui/server_agents_parts/part08__delete_latest_handoff.py`" in text
    assert "## Adjacent Precedent" in text
    assert "PIS-20260501-065402Z-B5F0" in text
    assert "PIS-20260501-070717Z-A068" in text
    assert "## Missing Substrate" in text
    assert "confirmed` vs `uncertain` semantics" in text
    assert "structured phone-first editor" in text
    assert "does **not** support a thin extension path inside the plane" in text
