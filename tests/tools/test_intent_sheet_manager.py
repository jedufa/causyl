from __future__ import annotations

from pathlib import Path

from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
from infra.webui.ledger.ledger_manager import ChangeLedger
from tools.bin import intent_sheet_create_from_text as mod


def test_create_intent_sheet_writes_real_ledger_entry(tmp_path: Path) -> None:
    root = tmp_path
    ledger = IntentSheetLedger(repo_root=root)

    created = ledger.create_sheet(
        title="Mobile operator truth model",
        intent_markdown="# Intent Sheet\n\noperator truth for the plane\n",
        uid_override="IS-20260403-120000Z-T3ST",
        project_id="causyl",
        workstream_type="platform",
        scope_boundary="platform-local",
        derived_pis_uids=["PIS-20260403-120100Z-ABCD"],
        decomposition_class="workflow",
        decomposition_target_range={"min": 2, "max": 5},
        decomposition_rationale="Medium workflow with persistence and operator read surface.",
        mandatory_tail={"proof": True, "closure": True},
    )

    loaded = ledger.read_sheet("IS-20260403-120000Z-T3ST")
    assert created["uid"] == "IS-20260403-120000Z-T3ST"
    assert loaded["meta"]["status"] == "draft"
    assert loaded["meta"]["project_id"] == "causyl"
    assert loaded["meta"]["derived_pis_uids"] == ["PIS-20260403-120100Z-ABCD"]
    assert loaded["meta"]["decomposition_class"] == "workflow"
    assert loaded["meta"]["decomposition_target_range"] == {"min": 2, "max": 5}
    assert loaded["meta"]["decomposition_rationale"] == "Medium workflow with persistence and operator read surface."
    assert loaded["meta"]["mandatory_tail"] == {"proof": True, "closure": True}
    assert "operator truth for the plane" in loaded["intent_markdown"]


def test_intent_sheet_status_move_uses_project_sharded_bucket(tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    items = ChangeLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Emergence proof anchor",
        intent_markdown="# Intent Sheet\n\nanchor\n",
        uid_override="IS-20260403-120500Z-M0VE",
        project_id="emergence",
        workstream_type="project",
        scope_boundary="project-local",
        validating_pis_uids=["PIS-20260403-120600Z-VAL1"],
    )
    items.create_item(title="Validation suite", slug="validation-suite", uid_override="PIS-20260403-120600Z-VAL1")
    items.update_status("PIS-20260403-120600Z-VAL1", "verified", note="suite passed")
    ledger.update_status("IS-20260403-120500Z-M0VE", "verified", note="accepted")
    d = ledger.get_sheet_dir("IS-20260403-120500Z-M0VE")
    rel = str(d.relative_to(tmp_path / "governance" / "ledger" / "intent_sheets")).replace("\\", "/")
    assert rel.startswith("emergence/verified/2026/04/")


def test_intent_sheet_update_meta_persists_evidence(tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Evidence closeout",
        intent_markdown="# Intent Sheet\n\nclose with evidence\n",
        uid_override="IS-20260403-121100Z-EVID",
        project_id="causyl",
    )

    ledger.update_meta(
        "IS-20260403-121100Z-EVID",
        {"evidence": {"kind": "intent_sheet_verified_by_family_closeout", "validating_pis_uid": "PIS-20260403-121101Z-VAL1"}},
    )

    loaded = ledger.read_sheet("IS-20260403-121100Z-EVID")
    assert loaded["meta"]["evidence"] == {
        "kind": "intent_sheet_verified_by_family_closeout",
        "validating_pis_uid": "PIS-20260403-121101Z-VAL1",
    }


def test_intent_sheet_verified_requires_validating_pis(tmp_path: Path) -> None:
    ledger = IntentSheetLedger(repo_root=tmp_path)
    ledger.create_sheet(
        title="Needs validation",
        intent_markdown="# Intent Sheet\n\nmust not close early\n",
        uid_override="IS-20260403-121500Z-GATE",
        project_id="causyl",
    )
    try:
        ledger.update_status("IS-20260403-121500Z-GATE", "verified", note="should fail")
        assert False, "expected verified gate failure"
    except ValueError as exc:
        assert "validating PIS" in str(exc)


def test_create_from_text_helper_builds_sheet_and_markdown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(mod, "REPO_ROOT", tmp_path)
    result = mod.create_draft_sheet(
        repo_root=tmp_path,
        raw_text="Need an intent sheet that can derive many PISes.",
        tags=["planning", "governance"],
        uid="IS-20260403-121000Z-F4ST",
        project_id="causyl",
    )
    ledger = IntentSheetLedger(repo_root=tmp_path)
    loaded = ledger.read_sheet("IS-20260403-121000Z-F4ST")
    assert result["uid"] == "IS-20260403-121000Z-F4ST"
    assert loaded["meta"]["topic_tags"] == ["planning", "governance"]
    assert "## 4. Expected derived work" in loaded["intent_markdown"]
    assert "> Need an intent sheet that can derive many PISes." in loaded["intent_markdown"]
