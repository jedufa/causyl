from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.controlplane import idea_note_pre_intent_intent_validation as validation
from infra.webui import server_routes_and_main as routes
from infra.webui.ledger import idea_note_manager
from infra.webui.ledger.idea_note_manager import IdeaNoteLedger
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str) -> None:
        self.path = path
        self.headers = {}
        self.rfile = io.BytesIO(b"{}")
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _read_payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def test_idea_note_full_lifecycle_e2e(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(idea_note_manager.ledger_storage, "repo_root_from_here", lambda: tmp_path)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_token="tok")
    ideas = IdeaNoteLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)

    created = ideas.create_note(
        title="Idea note full lifecycle",
        idea_markdown="# Idea Note\n\nPromote this only after the plane can show it first.\n",
        uid_override="IDEA-20260501-081500Z-E2E1",
        project_id="causyl",
        why_now="Early work needs a durable pre-intent home before it becomes a formal intent.",
        priority_signal="now",
        resume_context="Show the note in backlog/detail first, then promote it with durable provenance.",
        resume_blockers=["Need one operator-facing backlog surface."],
    )
    uid = str(created["uid"])

    backlog_before = routes._mobile_backlog_item_payload(limit=50, group_mode="status")
    backlog_row = next(item for item in backlog_before["items"] if item["uid"] == uid)
    assert backlog_row["backlog_kind"] == "idea_note"
    assert backlog_row["status"] == "draft"
    assert backlog_row["resume_context"] == "Show the note in backlog/detail first, then promote it with durable provenance."

    detail_req = _DummyReq(cfg=cfg, path=f"/api/idea_notes/{uid}")
    routes.WebUIRequestHandler.do_GET(detail_req)  # type: ignore[arg-type]
    assert detail_req._status == 200
    detail_payload = _read_payload(detail_req)
    assert detail_payload["meta"]["uid"] == uid
    assert detail_payload["meta"]["status"] == "draft"
    assert detail_payload["meta"]["why_now"] == "Early work needs a durable pre-intent home before it becomes a formal intent."

    promoted = ideas.promote_to_intent_sheet(uid, note="scenario_level_e2e")
    note_after = ideas.read_note(uid)
    intent_uid = str(promoted["intent_sheet_uid"])
    intent_after = intents.read_sheet(intent_uid)
    promotion_record = json.loads(
        (intents.get_sheet_dir(intent_uid) / "artifacts" / "promotion_record.json").read_text(encoding="utf-8")
    )
    source_snapshot = json.loads(
        (intents.get_sheet_dir(intent_uid) / "artifacts" / "source_idea_note" / "meta.json").read_text(encoding="utf-8")
    )

    assert promoted["created_new_intent"] is True
    assert note_after["meta"]["status"] == "promoted"
    assert note_after["meta"]["promotion_target_uid"] == intent_uid
    assert note_after["meta"]["related_intent_sheet_uids"] == [intent_uid]
    assert intent_after["meta"]["integration_meta"]["source_idea_note_uid"] == uid
    assert intent_after["meta"]["integration_meta"]["relationship"] == "promoted_from_idea_note"
    assert promotion_record["idea_note_uid"] == uid
    assert promotion_record["intent_sheet_uid"] == intent_uid
    assert source_snapshot["idea_note_uid"] == uid
    assert source_snapshot["promoted_intent_uid"] == intent_uid
    assert source_snapshot["source_meta"]["why_now"] == note_after["meta"]["why_now"]

    detail_after_req = _DummyReq(cfg=cfg, path=f"/api/idea_notes/{uid}")
    routes.WebUIRequestHandler.do_GET(detail_after_req)  # type: ignore[arg-type]
    assert detail_after_req._status == 200
    detail_after_payload = _read_payload(detail_after_req)
    assert detail_after_payload["meta"]["status"] == "promoted"
    assert detail_after_payload["meta"]["promotion_target_uid"] == intent_uid

    backlog_after = routes._mobile_backlog_item_payload(limit=50, group_mode="status")
    assert uid not in {item["uid"] for item in backlog_after["items"]}


def test_idea_note_promotion_requires_valid_idea_note_id(tmp_path: Path) -> None:
    ideas = IdeaNoteLedger(repo_root=tmp_path)
    intents = IntentSheetLedger(repo_root=tmp_path)
    ideas.create_note(
        title="Neighbor note stays untouched",
        idea_markdown="# Idea Note\n\nThis is here to catch accidental mutation.\n",
        uid_override="IDEA-20260501-081600Z-CTRL",
        project_id="causyl",
        why_now="Negative controls are useful when a test is supposed to fail closed.",
        resume_context="Keep this note stable while a bad promotion request fails elsewhere.",
    )

    with pytest.raises(FileNotFoundError):
        ideas.promote_to_intent_sheet("IDEA-20260501-081601Z-MISS", note="should_fail_closed")

    assert intents.list_sheets(limit=10) == []
    assert ideas.read_note("IDEA-20260501-081600Z-CTRL")["meta"]["status"] == "draft"


def test_idea_note_pre_intent_validation_artifact_keeps_dependency_gap_visible() -> None:
    artifact = validation.build_idea_note_pre_intent_intent_validation_artifact(
        validation_case_utc="2026-05-01T08:30:00Z",
        dependency_ready=False,
    )

    assert validation.validate_idea_note_pre_intent_intent_validation_artifact(artifact) == []
    assert artifact["uid"] == validation.VALIDATION_UID
    assert artifact["validation_outcome"] == "proof_gap"
    assert artifact["validation_results"]["dependency_ledger_evidence_inspection"]["status"] == "proof_gap"
    assert any(
        row["requirement_id"] == "family_dependency_readiness" and row["status"] == "unproven"
        for row in artifact["requirement_coverage"]
    )
    assert [row["command"] for row in artifact["validation_commands"]] == [
        f"pytest -q {validation.E2E_TEST_SELECTOR}",
        f"pytest -q {validation.NEGATIVE_TEST_SELECTOR}",
    ]
    assert any(row["scenario_id"] == "positive_idea_note_full_lifecycle_e2e" for row in artifact["scenario_results"])
    assert any(row["scenario_id"] == "negative_invalid_idea_note_id_promotion_fails_closed" for row in artifact["scenario_results"])


def test_idea_note_pre_intent_validation_artifact_can_represent_ready_family() -> None:
    artifact = validation.build_idea_note_pre_intent_intent_validation_artifact(
        validation_case_utc="2026-05-01T08:30:00Z",
        dependency_ready=True,
    )

    assert validation.validate_idea_note_pre_intent_intent_validation_artifact(artifact) == []
    assert artifact["validation_outcome"] == "passed"
    assert artifact["validation_results"]["dependency_ledger_evidence_inspection"]["status"] == "passed"
    assert any(
        row["requirement_id"] == "family_dependency_readiness" and row["status"] == "proven"
        for row in artifact["requirement_coverage"]
    )
    assert artifact["residual_gaps"] == []
    assert {row["outcome"] for row in artifact["scenario_results"]} == {"passed"}


def test_idea_note_pre_intent_validation_rejects_missing_required_scenario() -> None:
    artifact = validation.build_idea_note_pre_intent_intent_validation_artifact(
        validation_case_utc="2026-05-01T08:30:00Z",
        dependency_ready=False,
    )
    artifact["scenario_results"] = [
        row for row in artifact["scenario_results"] if row["scenario_id"] != "negative_invalid_idea_note_id_promotion_fails_closed"
    ]
    artifact["artifact_fingerprint"] = "stale"

    issues = validation.validate_idea_note_pre_intent_intent_validation_artifact(artifact)

    assert any("missing required scenarios" in issue for issue in issues)
    assert any("artifact_fingerprint does not match payload" in issue for issue in issues)


def test_write_idea_note_pre_intent_validation_artifact_is_idempotent(tmp_path) -> None:
    artifact = validation.build_idea_note_pre_intent_intent_validation_artifact(
        validation_case_utc="2026-05-01T08:30:00Z",
        dependency_ready=False,
    )

    path = validation.write_idea_note_pre_intent_intent_validation_artifact(artifact, repo_root=tmp_path)
    second = validation.write_idea_note_pre_intent_intent_validation_artifact(artifact, repo_root=tmp_path)

    assert second == path
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["artifact_fingerprint"] == artifact["artifact_fingerprint"]

    changed = dict(artifact)
    changed["non_claims"] = ["changed"]
    changed["artifact_fingerprint"] = "changed"
    with pytest.raises(ValueError, match="artifact"):
        validation.write_idea_note_pre_intent_intent_validation_artifact(changed, repo_root=tmp_path)
