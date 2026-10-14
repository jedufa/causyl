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
        / "PIS-20260519-131226Z-DF04__define-output-schema-confidence-semantics-and-proof-corpus"
        / "artifacts"
        / "clarification"
        / "meeting_extraction_output_contract.md"
    )


def test_meeting_extraction_output_contract_artifact_locks_schema_semantics_and_corpus() -> None:
    text = _artifact_path().read_text(encoding="utf-8")

    assert "**PIS UID:** PIS-20260519-131226Z-DF04" in text
    assert "- Canonical record kind: `meeting_extraction_record_v1`" in text
    assert "1. `summary`" in text
    assert "2. `decisions`" in text
    assert "3. `action_items`" in text
    assert "4. `owners`" in text
    assert "5. `deadlines`" in text
    assert "6. `open_questions`" in text
    assert "7. `risks`" in text
    assert "8. `follow_up_message`" in text
    assert "9. `task_breakdown`" in text
    assert "`confirmation_state`: `confirmed` or `uncertain`" in text
    assert "`support_level`: `explicit_quote`, `multi_signal`, or `weak_inference`" in text
    assert "Freshly extracted items default to `confirmation_state: uncertain`" in text
    assert "Extraction alone must not set `confirmation_state: confirmed`" in text
    assert "Task-list export is intentionally lossy." in text
    assert "all nine canonical sections in the order listed above" in text
    assert "### Case A: Transcript-style with explicit assignments" in text
    assert "### Case B: Rough-note style with fragments and ambiguity" in text
    assert "### Case C: Mixed input with operator edit pressure" in text
    assert "convert ambiguous timing into invented exact dates" in text
