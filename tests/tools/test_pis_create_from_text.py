from __future__ import annotations

from pathlib import Path

from tools.bin import pis_create_from_text as mod
from infra.webui.ledger.ledger_manager import ChangeLedger


def test_infer_title_prefers_first_sentence() -> None:
    text = """
    Need a deterministic PIS helper for copied text. It should create draft ledger items quickly.
    """
    assert mod.infer_title(text) == "Need a deterministic PIS helper for copied text."


def test_build_pis_markdown_wraps_copied_text_with_uid() -> None:
    fields = mod._default_structured_fields("Create a helper for backlog intake.", "Backlog intake helper")
    fields["why_it_matters"] = "Backlog intake is being lost in chat."
    fields["problem_or_opportunity"] = "Operators need a durable first-pass intake flow."
    fields["failure_if_ignored"] = "Ideas will continue to be rediscovered manually."
    fields["resume_note"] = "Review overlap and tighten acceptance criteria."
    out = mod.build_pis_markdown(
        raw_text="Create a helper for backlog intake.",
        uid="PIS-20260326-220000Z-ABCD",
        fields=fields,
    )
    assert "**UID:** PIS-20260326-220000Z-ABCD" in out
    assert "## 1. Title" in out
    assert "## 8. Narrow validation expectations" in out
    assert "## 9. Intake summary" in out
    assert "## 10. Copied source text" in out
    assert "Backlog intake helper" in out
    assert "Backlog intake is being lost in chat." in out
    assert "Operators need a durable first-pass intake flow." in out
    assert "Review overlap and tighten acceptance criteria." in out
    assert "> Create a helper for backlog intake." in out


def test_build_pis_markdown_rejects_partial_full_pis() -> None:
    text = """# Patch Instruction Sheet (PIS)

**UID:** PIS-20260326-220000Z-ABCD

## 1. Title
Partial draft
"""
    try:
        fields = mod._default_structured_fields(text, "Partial draft")
        mod.build_pis_markdown(
            raw_text=text,
            uid="PIS-20260326-220000Z-ABCD",
            fields=fields,
        )
    except ValueError as exc:
        assert "missing required sections" in str(exc)
    else:
        raise AssertionError("expected ValueError")


def test_create_draft_item_writes_real_ledger_entry(tmp_path: Path) -> None:
    repo_root = tmp_path
    items_root = repo_root / "governance" / "ledger" / "items"
    items_root.mkdir(parents=True)

    result = mod.create_draft_item(
        repo_root=repo_root,
        raw_text="Need a PIS helper for copied text intake.",
        tags=["tooling", "governance"],
        uid="PIS-20260326-220500Z-T3ST",
        use_api=False,
    )

    ledger = ChangeLedger(repo_root=repo_root)
    item = ledger.read_item("PIS-20260326-220500Z-T3ST")

    assert result["uid"] == "PIS-20260326-220500Z-T3ST"
    assert item["meta"]["status"] == "draft"
    assert item["meta"]["title"] == "Need a PIS helper for copied text intake."
    assert item["meta"]["topic_tags"] == ["tooling", "governance"]
    assert "**UID:** PIS-20260326-220500Z-T3ST" in item["pis_markdown"]
    assert "## 5. Dependencies, boundary, and overlap mapping" in item["pis_markdown"]
    assert "## 7. Acceptance criteria" in item["pis_markdown"]
    assert "## 9. Intake summary" in item["pis_markdown"]
    assert "Resume from here:" in item["pis_markdown"]
    assert "> Need a PIS helper for copied text intake." in item["pis_markdown"]


def test_create_draft_item_tolerates_api_failure_and_falls_back(tmp_path: Path) -> None:
    repo_root = tmp_path
    (repo_root / "governance" / "ledger" / "items").mkdir(parents=True)

    def _boom(**_: object) -> dict:
        raise RuntimeError("simulated API failure")

    result = mod.create_draft_item(
        repo_root=repo_root,
        raw_text="Need a tolerant draft creation flow.",
        uid="PIS-20260326-221500Z-F4LL",
        use_api=True,
        response_creator=_boom,
    )

    ledger = ChangeLedger(repo_root=repo_root)
    item = ledger.read_item("PIS-20260326-221500Z-F4LL")

    assert result["uid"] == "PIS-20260326-221500Z-F4LL"
    assert item["meta"]["status"] == "draft"
    assert "Need a tolerant draft creation flow." in item["pis_markdown"]
    assert "## 7. Acceptance criteria" in item["pis_markdown"]
