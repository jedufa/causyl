from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.webui.ledger.idea_note_manager import IdeaNoteLedger, _derived_intent_uid_for_note
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from tools.bin import idea_note_create_from_text as mod
from tools.bin import idea_note_promote_to_intent_sheet as promote_mod


def test_create_idea_note_writes_real_ledger_entry(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)

    created = ledger.create_note(
        title="Possible experiment gallery page",
        idea_markdown="# Idea Note\n\nrough page idea\n",
        uid_override="IDEA-20260404-120000Z-T3ST",
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
        related_intent_sheet_uids=["IS-20260403-212913Z-E494"],
        related_pis_uids=["PIS-20260404-193200Z-63AA"],
        promotion_target_uid="IS-20260403-212913Z-E494",
        why_now="Operators keep losing early visual ideas before they become real work.",
        priority_signal="high",
        resume_context="Pick one small operator surface and prove the note survives restart.",
        resume_blockers=["Need one thin view that can render idea-note summaries."],
    )

    loaded = ledger.read_note("IDEA-20260404-120000Z-T3ST")
    assert created["uid"] == "IDEA-20260404-120000Z-T3ST"
    assert loaded["meta"]["status"] == "draft"
    assert loaded["meta"]["project_id"] == "causyl"
    assert loaded["meta"]["related_intent_sheet_uids"] == ["IS-20260403-212913Z-E494"]
    assert loaded["meta"]["related_pis_uids"] == ["PIS-20260404-193200Z-63AA"]
    assert loaded["meta"]["promotion_target_uid"] == "IS-20260403-212913Z-E494"
    assert loaded["meta"]["why_now"] == "Operators keep losing early visual ideas before they become real work."
    assert loaded["meta"]["priority_signal"] == "high"
    assert loaded["meta"]["resume_context"] == "Pick one small operator surface and prove the note survives restart."
    assert loaded["meta"]["resume_blockers"] == ["Need one thin view that can render idea-note summaries."]
    assert "rough page idea" in loaded["idea_markdown"]


def test_idea_note_status_move_uses_project_sharded_bucket(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Emergence sim viewer variant",
        idea_markdown="# Idea Note\n\nmaybe later\n",
        uid_override="IDEA-20260404-120500Z-M0VE",
        project_id="emergence",
        workstream_type="project",
        scope_boundary="project-local",
        resume_context="Keep this parked until the project gets a real operator backlog surface.",
    )
    ledger.update_status("IDEA-20260404-120500Z-M0VE", "parked", note="keep for later")
    d = ledger.get_note_dir("IDEA-20260404-120500Z-M0VE")
    rel = str(d.relative_to(tmp_path / "governance" / "ledger" / "idea_notes")).replace("\\", "/")
    assert rel.startswith("emergence/deferred/")
    assert rel.endswith("/IDEA-20260404-120500Z-M0VE__emergence-sim-viewer-variant")


def test_lifecycle_actions_persist_resume_context_and_audit_history(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Thin backlog slice for idea notes",
        idea_markdown="# Idea Note\n\nNeed a real pre-intent list.\n",
        uid_override="IDEA-20260404-122000Z-ACTN",
        project_id="causyl",
    )

    parked = ledger.apply_lifecycle_action(
        "IDEA-20260404-122000Z-ACTN",
        "park",
        note="waiting_on_operator_surface",
        resume_context="Resume once the first-class backlog card exists.",
        resume_blockers=["Need one operator-visible backlog slice."],
    )
    resumed = ledger.apply_lifecycle_action(
        "IDEA-20260404-122000Z-ACTN",
        "resume",
        note="backlog_card_ready",
        why_now="The backlog slice now exists, so this can become selectable work.",
        priority_signal="now",
    )

    loaded = ledger.read_note("IDEA-20260404-122000Z-ACTN")
    events = [
        json.loads(line)
        for line in (ledger.get_note_dir("IDEA-20260404-122000Z-ACTN") / "events.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    assert parked["status"] == "parked"
    assert resumed["status"] == "active"
    assert loaded["meta"]["status"] == "active"
    assert loaded["meta"]["why_now"] == "The backlog slice now exists, so this can become selectable work."
    assert loaded["meta"]["priority_signal"] == "now"
    assert loaded["meta"]["resume_context"] == "Resume once the first-class backlog card exists."
    assert loaded["meta"]["resume_blockers"] == ["Need one operator-visible backlog slice."]
    assert events[-1]["payload"]["action"] == "resume"
    assert events[-1]["payload"]["from_status"] == "parked"
    assert events[-1]["payload"]["status"] == "active"


def test_invalid_transition_and_malformed_meta_fail_closed(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Reject bad lifecycle moves",
        idea_markdown="# Idea Note\n\nkeep honest failure semantics\n",
        uid_override="IDEA-20260404-123000Z-GATE",
        project_id="causyl",
    )

    ledger.apply_lifecycle_action("IDEA-20260404-123000Z-GATE", "discard", note="not worth it")
    with pytest.raises(ValueError, match="invalid idea note transition: discarded -> active"):
        ledger.apply_lifecycle_action("IDEA-20260404-123000Z-GATE", "resume", note="should not work")
    with pytest.raises(ValueError, match="unsupported idea note metadata keys: unknown_flag"):
        ledger.update_meta("IDEA-20260404-123000Z-GATE", {"unknown_flag": True})

    loaded = ledger.read_note("IDEA-20260404-123000Z-GATE")
    assert loaded["meta"]["status"] == "discarded"


def test_park_requires_resume_breadcrumb_and_preserves_state_on_failure(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Parking needs a restart-safe breadcrumb",
        idea_markdown="# Idea Note\n\nDon't lose the trail.\n",
        uid_override="IDEA-20260404-123100Z-PARK",
        project_id="causyl",
        why_now="This needs to survive a pause without chat archaeology.",
    )

    with pytest.raises(ValueError, match="parked idea note requires resume_context or resume_blockers"):
        ledger.apply_lifecycle_action("IDEA-20260404-123100Z-PARK", "park", note="too vague")

    loaded = ledger.read_note("IDEA-20260404-123100Z-PARK")
    assert loaded["meta"]["status"] == "draft"
    assert loaded["meta"]["resume_context"] is None
    assert loaded["meta"]["resume_blockers"] == []


def test_resume_requires_durable_why_now_and_context(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Resume must carry durable context",
        idea_markdown="# Idea Note\n\nResume should fail closed when truth is missing.\n",
        uid_override="IDEA-20260404-123200Z-RESM",
        project_id="causyl",
    )
    ledger.apply_lifecycle_action(
        "IDEA-20260404-123200Z-RESM",
        "park",
        note="paused",
        resume_context="Wait until the first operator-facing queue exists.",
    )

    with pytest.raises(ValueError, match="resuming a parked idea note requires why_now"):
        ledger.apply_lifecycle_action("IDEA-20260404-123200Z-RESM", "resume", note="still missing why_now")

    loaded = ledger.read_note("IDEA-20260404-123200Z-RESM")
    assert loaded["meta"]["status"] == "parked"
    assert loaded["meta"]["why_now"] is None
    assert loaded["meta"]["resume_context"] == "Wait until the first operator-facing queue exists."


def test_promoted_status_requires_target_and_normalizes_provenance(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Promotion target must be explicit",
        idea_markdown="# Idea Note\n\nneeds a real anchor\n",
        uid_override="IDEA-20260404-124000Z-LINK",
        project_id="causyl",
    )

    with pytest.raises(ValueError, match="promotion_target_uid is required"):
        ledger.update_status("IDEA-20260404-124000Z-LINK", "promoted", note="missing target")

    ledger.update_meta("IDEA-20260404-124000Z-LINK", {"promotion_target_uid": "PIS-20260404-193200Z-63AA"})
    promoted = ledger.update_status("IDEA-20260404-124000Z-LINK", "promoted", note="linked promotion")
    loaded = ledger.read_note("IDEA-20260404-124000Z-LINK")

    assert promoted["status"] == "promoted"
    assert loaded["meta"]["promotion_target_uid"] == "PIS-20260404-193200Z-63AA"
    assert loaded["meta"]["related_pis_uids"] == ["PIS-20260404-193200Z-63AA"]


def test_update_meta_rejects_clearing_resume_context_when_parked_and_preserves_state(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Parked breadcrumb must survive metadata edits",
        idea_markdown="# Idea Note\n\nKeep the restart breadcrumb durable.\n",
        uid_override="IDEA-20260404-124100Z-BRDC",
        project_id="causyl",
    )
    ledger.apply_lifecycle_action(
        "IDEA-20260404-124100Z-BRDC",
        "park",
        note="paused",
        resume_context="Resume when the operator queue is visible.",
    )

    with pytest.raises(ValueError, match="parked idea note requires resume_context or resume_blockers"):
        ledger.update_meta("IDEA-20260404-124100Z-BRDC", {"resume_context": None})

    loaded = ledger.read_note("IDEA-20260404-124100Z-BRDC")
    assert loaded["meta"]["status"] == "parked"
    assert loaded["meta"]["resume_context"] == "Resume when the operator queue is visible."
    assert loaded["meta"]["resume_blockers"] == []


def test_update_meta_rejects_clearing_promotion_target_when_promoted_and_preserves_state(tmp_path: Path) -> None:
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    ledger.create_note(
        title="Promoted note must keep target linkage",
        idea_markdown="# Idea Note\n\nDon't lose promotion truth.\n",
        uid_override="IDEA-20260404-124200Z-PRMT",
        project_id="causyl",
    )
    ledger.update_meta("IDEA-20260404-124200Z-PRMT", {"promotion_target_uid": "PIS-20260404-193200Z-63AA"})
    ledger.update_status("IDEA-20260404-124200Z-PRMT", "promoted", note="linked promotion")

    with pytest.raises(ValueError, match="promotion_target_uid is required before marking an idea note promoted"):
        ledger.update_meta("IDEA-20260404-124200Z-PRMT", {"promotion_target_uid": None})

    loaded = ledger.read_note("IDEA-20260404-124200Z-PRMT")
    assert loaded["meta"]["status"] == "promoted"
    assert loaded["meta"]["promotion_target_uid"] == "PIS-20260404-193200Z-63AA"
    assert loaded["meta"]["related_pis_uids"] == ["PIS-20260404-193200Z-63AA"]


def test_create_from_text_helper_builds_note_and_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    result = mod.create_draft_note(
        repo_root=tmp_path,
        raw_text="Maybe let the plane remember favorite experiment views.",
        tags=["ideas", "ui"],
        uid="IDEA-20260404-121000Z-F4ST",
        project_id="causyl",
    )
    ledger = IdeaNoteLedger(repo_root=tmp_path)
    loaded = ledger.read_note("IDEA-20260404-121000Z-F4ST")
    assert result["uid"] == "IDEA-20260404-121000Z-F4ST"
    assert loaded["meta"]["topic_tags"] == ["ideas", "ui"]
    assert "## 4. Raw note" in loaded["idea_markdown"]
    assert "> Maybe let the plane remember favorite experiment views." in loaded["idea_markdown"]


def test_promote_ready_idea_note_creates_linked_intent_sheet(tmp_path: Path) -> None:
    ideas = IdeaNoteLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    ideas.create_note(
        title="Operator-facing intent promotion",
        idea_markdown="# Idea Note\n\nPreserve early work and promote it on purpose.\n",
        uid_override="IDEA-20260404-125000Z-PROM",
        project_id="causyl",
        why_now="Operators need a durable intent anchor once a note survives initial triage.",
        resume_context="Start with one explicit promotion action and preserve backlink provenance.",
        priority_signal="now",
    )

    result = ideas.promote_to_intent_sheet("IDEA-20260404-125000Z-PROM", note="ready_for_intent")
    note = ideas.read_note("IDEA-20260404-125000Z-PROM")
    intent = intents.read_sheet(result["intent_sheet_uid"])
    snapshot = json.loads(
        (intents.get_sheet_dir(result["intent_sheet_uid"]) / "artifacts" / "promotion_record.json").read_text(encoding="utf-8")
    )

    assert result["created_new_intent"] is True
    assert note["meta"]["status"] == "promoted"
    assert note["meta"]["promotion_target_uid"] == result["intent_sheet_uid"]
    assert note["meta"]["related_intent_sheet_uids"] == [result["intent_sheet_uid"]]
    assert intent["meta"]["integration_meta"]["source_idea_note_uid"] == "IDEA-20260404-125000Z-PROM"
    assert intent["meta"]["integration_meta"]["relationship"] == "promoted_from_idea_note"
    assert "Promoted From Idea Note" in intent["intent_markdown"]
    assert snapshot["idea_note_uid"] == "IDEA-20260404-125000Z-PROM"


def test_promote_requires_readiness_fields_and_creates_no_intent(tmp_path: Path) -> None:
    ideas = IdeaNoteLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    ideas.create_note(
        title="Not ready yet",
        idea_markdown="# Idea Note\n\nStill vague.\n",
        uid_override="IDEA-20260404-125100Z-NOPE",
        project_id="causyl",
        why_now="",
        resume_context="",
    )

    with pytest.raises(ValueError, match="missing: why_now, resume_context"):
        ideas.promote_to_intent_sheet("IDEA-20260404-125100Z-NOPE")

    assert intents.list_sheets(limit=10) == []
    assert ideas.read_note("IDEA-20260404-125100Z-NOPE")["meta"]["status"] == "draft"


def test_promote_repairs_partial_state_without_creating_duplicate_intents(tmp_path: Path) -> None:
    ideas = IdeaNoteLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    note_uid = "IDEA-20260404-125200Z-RTRY"
    intent_uid = _derived_intent_uid_for_note(note_uid)
    ideas.create_note(
        title="Repairable promotion path",
        idea_markdown="# Idea Note\n\nRetry must converge.\n",
        uid_override=note_uid,
        project_id="causyl",
        why_now="A promotion retry should reuse the existing target after interruption.",
        resume_context="Retry the same note and repair backlink state instead of minting a second intent.",
    )
    intents.create_sheet(
        title="Repairable promotion path",
        intent_markdown="# Intent Sheet\n\npartial promotion\n",
        uid_override=intent_uid,
        project_id="causyl",
        workstream_type="shared_capability",
        scope_boundary="cross-project",
    )

    repaired = promote_mod.promote_note(repo_root=tmp_path, uid=note_uid, note="retry")
    rows = intents.list_sheets(limit=10)
    note = ideas.read_note(note_uid)

    assert intent_uid == repaired["intent_sheet_uid"]
    assert repaired["created_new_intent"] is False
    assert repaired["repaired_backlink"] is True
    assert len(rows) == 1
    assert note["meta"]["status"] == "promoted"
    assert note["meta"]["promotion_target_uid"] == repaired["intent_sheet_uid"]
