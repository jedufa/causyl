from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import intent_family_v2_workflow_contract as contract
from infra.controlplane import scheduled_jobs as sched


def test_intent_family_v2_workflow_contract_v1_exposes_expected_shape() -> None:
    payload = contract.intent_family_v2_workflow_contract_v1()

    assert payload.version == "intent_family_v2_workflow_definition_contract_v1"
    assert payload.uid == "PIS-20260409-195200Z-A7V1"
    assert payload.workflow_kind == "intent_family_v2"
    assert payload.canonical_artifact_rel.as_posix() == "governance/intent_family_v2_workflow_definition_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_intent_family_v2_workflow_definition_v1.md"
    assert payload.stage_order == ("intent", "parent", "children", "implement", "intent_validation", "closeout")
    assert payload.required_validation_child_kind == "intent_validation_suite"
    assert payload.required_final_child_kind == "intent_family_closeout"
    assert payload.final_owner_stage == "closeout"
    assert payload.final_owner_node_kind == "intent_family_closeout"


def test_intent_family_v2_workflow_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(stage["stage_id"] for stage in payload["stages"]) == contract.STAGE_ORDER
    assert payload["family_tail_contract"]["required_final_child_kind"] == contract.REQUIRED_FINAL_CHILD_KIND
    assert payload["promotion_law"]["validation_may_promote_parent"] is False
    assert payload["node_kinds"]["intent_family_closeout"]["promotable_targets"] == ["family_parent", "source_intent"]


def test_intent_family_v2_workflow_contract_validation_passes() -> None:
    assert contract.validate_intent_family_v2_workflow_contract_v1() == ()


def test_intent_family_v2_workflow_contract_rejects_unknown_stage() -> None:
    with pytest.raises(KeyError, match="unknown intent_family_v2 stage"):
        contract.canonical_stage_name("verify")


def test_intent_family_v2_workflow_contract_rejects_validation_stage_promotion() -> None:
    with pytest.raises(ValueError, match="only closeout may promote parent or source intent state"):
        contract.validate_promotion("intent_validation", "intent_validation_suite", "family_parent")


def test_intent_family_v2_workflow_contract_allows_closeout_promotion_only_for_closeout_node() -> None:
    contract.validate_promotion("closeout", "intent_family_closeout", "family_parent")
    contract.validate_promotion("closeout", "intent_family_closeout", "source_intent")

    with pytest.raises(ValueError, match="only intent_family_closeout may own closeout promotion"):
        contract.validate_promotion("closeout", "intent_validation_suite", "family_parent")


def test_recursive_family_tail_code_references_shared_workflow_contract() -> None:
    implement_recursive = Path("tools/bin/implement_recursive.py").read_text(encoding="utf-8")
    normalize_tail = Path(
        "infra/controlplane/scheduled_jobs_parts/part02__normalize_scheduler_launch_and_family_queueing.py"
    ).read_text(encoding="utf-8")

    assert "intent_family_v2_workflow_contract" in implement_recursive
    assert "VALIDATION_CHILD_KIND" in implement_recursive
    assert "REQUIRED_FINAL_CHILD_KIND" in implement_recursive
    assert "intent_family_v2_workflow_contract" in normalize_tail


def test_normalize_intent_family_tail_contract_fails_closed_without_explicit_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    impl_uid = "PIS-20260326-210001Z-A1B2"
    proof_uid = "PIS-20260326-210002Z-C3D4"
    closeout_uid = "PIS-20260326-210003Z-E5F6"

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": "IS-20260326-000001Z-TEST",
                    "proof_child_uid": proof_uid,
                    "required_final_child_uid": proof_uid,
                    "child_uids": [impl_uid, proof_uid, closeout_uid],
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, slug, title, rank, deps in [
        (impl_uid, "child-a", "Child A", 1, []),
        (proof_uid, "proof", "Prove Parent End-to-End", 2, [impl_uid]),
        (closeout_uid, "closeout", "Close Parent Intent Boundary and Family Acceptance", 3, [impl_uid, proof_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "draft",
                    "integration_meta": {"relationship": "execution_child"},
                }
            ),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": "execution_child",
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    result = sched._normalize_intent_family_tail_contract(parent_uid, repo_root=tmp_path)

    assert result["updated"] is False
    assert result["reason"] == "missing_explicit_tail_relationship_metadata"
    parent_meta = json.loads((parent_dir / "meta.json").read_text(encoding="utf-8"))
    assert "intent_validation_child_uid" not in parent_meta["integration_meta"]
    assert parent_meta["integration_meta"]["required_final_child_uid"] == proof_uid
    proof_meta = json.loads(
        (
            tmp_path
            / "governance"
            / "ledger"
            / "patch_instruction_sheets"
            / "causyl"
            / "active"
            / "2026"
            / "03"
            / f"{proof_uid}__proof"
            / "meta.json"
        ).read_text(encoding="utf-8")
    )
    closeout_meta = json.loads(
        (
            tmp_path
            / "governance"
            / "ledger"
            / "patch_instruction_sheets"
            / "causyl"
            / "active"
            / "2026"
            / "03"
            / f"{closeout_uid}__closeout"
            / "meta.json"
        ).read_text(encoding="utf-8")
    )
    assert proof_meta["integration_meta"]["relationship"] == "execution_child"
    assert closeout_meta["integration_meta"]["relationship"] == "execution_child"


def test_validate_intent_family_v2_workflow_contract_rejects_stage_order_drift(tmp_path: Path) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    payload["stages"][4]["stage_id"] = "closeout"
    payload["stages"][5]["stage_id"] = "intent_validation"

    contract_path = tmp_path / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    issues = contract.validate_intent_family_v2_workflow_contract_v1(repo_root=tmp_path)

    assert "stages must list the canonical stage order exactly" in issues


def test_normalize_intent_family_tail_contract_rewrites_legacy_intermediate_child_kinds_when_canonical_tail_exists(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(sched, "_repo_root", lambda: tmp_path)
    parent_uid = "PIS-20260326-203500Z-R7K2"
    impl_uid = "PIS-20260326-210001Z-A1B2"
    proof_uid = "PIS-20260326-210002Z-C3D4"
    closeout_impl_uid = "PIS-20260326-210003Z-E5F6"
    validation_uid = "PIS-20260326-210004Z-G7H8"
    family_closeout_uid = "PIS-20260326-210005Z-J9K0"

    parent_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": "IS-20260326-000001Z-TEST",
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": family_closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                    "child_uids": [impl_uid, proof_uid, closeout_impl_uid, validation_uid, family_closeout_uid],
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    for uid, slug, title, relationship, rank, deps in [
        (impl_uid, "child-a", "Child A", "execution_child", 1, []),
        (proof_uid, "proof", "Prove Parent End-to-End", "proof_child", 2, [impl_uid]),
        (closeout_impl_uid, "closeout", "Close Parent Intent Boundary and Family Acceptance", "closeout_child", 3, [proof_uid]),
        (validation_uid, "intent-validation", "Validate Parent End-to-End", "intent_validation_suite", 4, [closeout_impl_uid]),
        (family_closeout_uid, "family-closeout", "Family Closeout", "intent_family_closeout", 5, [validation_uid]),
    ]:
        child_dir = tmp_path / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active" / "2026" / "03" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps(
                {
                    "uid": uid,
                    "title": title,
                    "status": "draft",
                    "integration_meta": {"relationship": relationship},
                }
            ),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": relationship,
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )

    result = sched._normalize_intent_family_tail_contract(parent_uid, repo_root=tmp_path)

    assert result["updated"] is True
    assert result["reason"] == "normalized_legacy_intermediate_child_kinds"
    rows = {str(row["uid"]): str(row["relationship"]) for row in result["child_rows"]}
    assert rows[proof_uid] == "execution_child"
    assert rows[closeout_impl_uid] == "execution_child"
    assert rows[validation_uid] == "intent_validation_suite"
    assert rows[family_closeout_uid] == "intent_family_closeout"
