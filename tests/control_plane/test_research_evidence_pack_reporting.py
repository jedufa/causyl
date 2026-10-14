from __future__ import annotations

import copy
import json
from pathlib import Path

from infra.controlplane import research_evidence_pack_reporting as reporting


def _reference_pack() -> dict:
    return json.loads(Path("governance/research_evidence_pack_reference_v1.json").read_text(encoding="utf-8"))


def _reference_compendium() -> dict:
    return json.loads(Path("governance/literature_compendium_reference_v1.json").read_text(encoding="utf-8"))


def test_evidence_pack_report_preserves_source_ids_and_contradiction_state() -> None:
    report = reporting.build_evidence_pack_report(_reference_pack())

    assert report["ok"] is True
    assert report["artifact_kind"] == "evidence_pack"
    assert report["pack_id"] == "rep-20260410-reference"
    assert report["source_ids"] == ["src-clean-paper", "src-weak-note", "src-conflict-preprint"]
    assert report["claim_statuses"]["claim-weak"] == "weak_evidence"
    assert report["contradiction_statuses"]["contradiction-alt-prep"] == "unresolved"

    text = reporting.render_operator_report(report)
    assert "pack_id: rep-20260410-reference" in text
    assert "source_ids: src-clean-paper, src-weak-note, src-conflict-preprint" in text
    assert "contradictions: contradiction-alt-prep=unresolved" in text


def test_compendium_report_keeps_pack_claim_source_refs_visible() -> None:
    report = reporting.build_compendium_report(_reference_compendium(), evidence_packs=[_reference_pack()])

    assert report["ok"] is True
    assert report["artifact_kind"] == "literature_compendium"
    assert report["compendium_id"] == "lc-20260410-reference"
    assert report["evidence_pack_ids"] == ["rep-20260410-reference"]
    assert report["entry_refs"]["entry-merged-alt-prep"] == {
        "pack_ids": ["rep-20260410-reference"],
        "claim_ids": ["claim-weak", "claim-conflict"],
        "source_ids": ["src-weak-note", "src-conflict-preprint"],
        "contradiction_ids": ["contradiction-alt-prep"],
    }
    assert report["unresolved_conflict_ids"] == ["conflict-alt-prep-visible"]


def test_validation_failure_report_is_explicit_and_does_not_hide_missing_evidence() -> None:
    raw = _reference_pack()
    raw["claim_source_links"] = []
    before = copy.deepcopy(raw)

    report = reporting.build_evidence_pack_report(raw)

    assert raw == before
    assert report["ok"] is False
    assert report["validation_status"] == "failed"
    assert report["error_count"] > 0
    assert "errors" in report
    assert any(error["code"] == "unsupported_claim" for error in report["errors"])
    assert "claim-clean" in reporting.render_operator_report(report)


def test_evidence_pack_diff_detects_claim_source_link_and_contradiction_changes() -> None:
    before = _reference_pack()
    after = copy.deepcopy(before)
    after["claims"][0]["status"] = "weak_evidence"
    after["claim_source_links"][0]["source_id"] = "src-weak-note"
    after["contradictions"][0]["status"] = "deferred"

    diff = reporting.diff_evidence_packs(before, after)

    assert diff["artifact_kind"] == "evidence_pack_diff"
    assert diff["pack_ids"] == {
        "before": "rep-20260410-reference",
        "after": "rep-20260410-reference",
    }
    assert "claim-clean" in diff["changed_claims"]
    assert "link-clean" in diff["changed_claim_source_links"]
    assert "contradiction-alt-prep" in diff["changed_contradictions"]


def test_compendium_diff_detects_changed_pack_ref_entry_and_conflict_records() -> None:
    before = _reference_compendium()
    after = copy.deepcopy(before)
    after["evidence_pack_refs"][0]["pack_fingerprint"] = "changed"
    after["entries"][0]["evidence_refs"][0]["source_ids"] = ["src-weak-note"]
    after["unresolved_conflicts"][0]["state"] = "deferred"

    diff = reporting.diff_literature_compendia(before, after)

    assert diff["artifact_kind"] == "literature_compendium_diff"
    assert "rep-20260410-reference" in diff["changed_evidence_pack_refs"]
    assert "entry-topic-baseline" in diff["changed_entries"]
    assert "conflict-alt-prep-visible" in diff["changed_unresolved_conflicts"]


def test_list_research_evidence_artifacts_reads_repo_relative_pack_and_compendium(tmp_path: Path) -> None:
    root = tmp_path
    governance = root / "governance"
    governance.mkdir()
    pack_path = governance / "research_evidence_pack_reference_v1.json"
    compendium_path = governance / "literature_compendium_reference_v1.json"
    pack_path.write_text(json.dumps(_reference_pack(), sort_keys=True), encoding="utf-8")
    compendium_path.write_text(json.dumps(_reference_compendium(), sort_keys=True), encoding="utf-8")

    reports = reporting.list_research_evidence_artifacts(repo_root=root, artifact_refs=[pack_path, compendium_path])

    assert [row["artifact_kind"] for row in reports] == ["evidence_pack", "literature_compendium"]
    assert reports[0]["artifact_ref"] == "governance/research_evidence_pack_reference_v1.json"
    assert reports[1]["artifact_ref"] == "governance/literature_compendium_reference_v1.json"


def test_load_compendium_report_fails_visibly_for_missing_pack_artifact(tmp_path: Path) -> None:
    root = tmp_path
    governance = root / "governance"
    governance.mkdir()
    compendium_path = governance / "literature_compendium_reference_v1.json"
    compendium_path.write_text(json.dumps(_reference_compendium(), sort_keys=True), encoding="utf-8")

    report = reporting.load_compendium_report(compendium_path, repo_root=root)

    assert report["ok"] is False
    assert report["validation_status"] == "failed"
    assert any(error["code"] == "unreadable_evidence_pack_ref" for error in report["errors"])
