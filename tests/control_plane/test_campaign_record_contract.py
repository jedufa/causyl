from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import campaign_record_contract as contract


def _base_record() -> dict:
    return {
        "schema_version": "campaign_record_contract_v1",
        "campaign_id": "camp-20260409-identity-demo",
        "parent_binding": {
            "binding_type": "ledger_uid",
            "binding_id": "PIS-20260409-121217Z-2A6A",
        },
        "campaign_state": "active",
        "campaign_truth_refs": {
            "ledger_uids": ["PIS-20260409-121217Z-2A6A"],
            "job_ids": ["job-campaign-001"],
            "run_ids": [],
            "receipt_refs": ["runtime/state/scheduled_jobs/receipts/job-campaign-001.json"],
            "continuation_packet_ids": [],
            "artifact_refs": [],
        },
        "record_event_refs": [
            "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-121217Z-2A6A__campaign-execution-contract/events.jsonl"
        ],
        "phases": [
            {
                "phase_id": "phase_define_contract",
                "label": "Define contract",
                "job_shape": "verification_loop_job",
                "state": "succeeded",
                "depends_on": [],
                "output_refs": ["governance/campaign_record_contract_v1.json"],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-121600Z-J8M2"],
                    "job_ids": ["sj_20260409T123231Z_f93d7d"],
                    "run_ids": [],
                    "receipt_refs": ["runtime/state/scheduled_jobs/receipts/sj_20260409T123231Z_f93d7d.json"],
                    "continuation_packet_ids": [],
                    "artifact_refs": ["docs/contracts/contract_campaign_record_schema_v1.md"],
                },
                "phase_event_refs": [
                    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-121600Z-J8M2__define-campaign-identity-phase-graph-and-durable-record-contract/events.jsonl"
                ],
            },
            {
                "phase_id": "phase_launch_bindings",
                "label": "Bind launch",
                "job_shape": "fanout_job",
                "state": "declared",
                "depends_on": ["phase_define_contract"],
                "output_refs": [],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-121602Z-L1P4"],
                    "job_ids": [],
                    "run_ids": [],
                    "receipt_refs": [],
                    "continuation_packet_ids": [],
                    "artifact_refs": [],
                },
                "phase_event_refs": [],
            },
        ],
    }


def _reference_pack_ref() -> dict:
    return {
        "pack_id": "rep-20260410-reference",
        "pack_schema_version": "research_evidence_pack_v1",
        "pack_artifact_ref": "governance/research_evidence_pack_reference_v1.json",
        "pack_fingerprint": "242bae0de4113a23933a78399d79e879c7d90bf7bd1d06df40840db494fbea87",
        "summary": "Reference evidence pack is available for successor campaign work.",
    }


def test_campaign_record_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.campaign_record_contract_v1()

    assert payload.version == "campaign_record_contract_v1"
    assert payload.uid == "PIS-20260409-121600Z-J8M2"
    assert payload.canonical_artifact_rel.as_posix() == "governance/campaign_record_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_campaign_record_schema_v1.md"
    assert payload.phase_lifecycle_states == ("declared", "running", "blocked", "succeeded", "failed", "cancelled")
    assert payload.append_only_fields == ("record_event_refs", "phase_event_refs")


def test_campaign_record_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/campaign_record_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["campaign_lifecycle_states"]) == contract.CAMPAIGN_LIFECYCLE_STATES
    assert tuple(payload["phase_required_fields"]) == contract.PHASE_REQUIRED_FIELDS
    assert tuple(payload["truth_ref_fields"]) == contract.TRUTH_REF_FIELDS


def test_validate_campaign_record_contract_v1_returns_no_issues() -> None:
    assert contract.validate_campaign_record_contract_v1() == ()


def test_validate_campaign_record_accepts_explicit_phase_graph_and_truth_refs() -> None:
    result = contract.validate_campaign_record(_base_record())

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["normalized_record"]["phases"][0]["terminal_class"] == "success_terminal"
    assert result["normalized_record"]["campaign_terminal_class"] == "non_terminal"
    assert result["computed_fingerprint"] != ""


def test_validate_campaign_record_persists_reloadable_evidence_pack_refs() -> None:
    raw = _base_record()
    raw["evidence_pack_refs"] = [_reference_pack_ref()]
    raw["phases"][0]["evidence_pack_refs"] = [_reference_pack_ref()]

    result = contract.validate_campaign_record(raw, repo_root=Path("."))

    assert result["ok"] is True
    assert result["errors"] == []
    assert result["normalized_record"]["evidence_pack_refs"][0]["pack_id"] == "rep-20260410-reference"
    assert (
        result["normalized_record"]["phases"][0]["evidence_pack_refs"][0]["pack_artifact_ref"]
        == "governance/research_evidence_pack_reference_v1.json"
    )


def test_validate_campaign_record_rejects_missing_evidence_pack_ref() -> None:
    raw = _base_record()
    pack_ref = _reference_pack_ref()
    pack_ref["pack_artifact_ref"] = "governance/missing_evidence_pack.json"
    raw["phases"][0]["evidence_pack_refs"] = [pack_ref]

    result = contract.validate_campaign_record(raw, repo_root=Path("."))

    assert result["ok"] is False
    assert any(row["code"] == "missing_evidence_pack" for row in result["errors"])


def test_validate_campaign_record_rejects_unsafe_evidence_pack_ref_path() -> None:
    raw = _base_record()
    pack_ref = _reference_pack_ref()
    pack_ref["pack_artifact_ref"] = "/tmp/not-a-repo-pack.json"
    raw["evidence_pack_refs"] = [pack_ref]

    result = contract.validate_campaign_record(raw, repo_root=Path("."))

    assert result["ok"] is False
    assert any(row["code"] == "invalid_evidence_pack_ref" for row in result["errors"])


def test_validate_campaign_record_rejects_duplicate_phase_ids_and_unknown_edge_targets() -> None:
    raw = _base_record()
    raw["phases"][1]["phase_id"] = "phase_define_contract"
    raw["phases"][1]["depends_on"] = ["missing_phase"]

    result = contract.validate_campaign_record(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "duplicate_phase_id" in codes
    assert "undeclared_edge_target" in codes


def test_validate_campaign_record_rejects_cyclic_phase_graph_and_illegal_state() -> None:
    raw = _base_record()
    raw["phases"][0]["depends_on"] = ["phase_launch_bindings"]
    raw["phases"][1]["state"] = "ready"

    result = contract.validate_campaign_record(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "cyclic_graph" in codes
    assert "invalid_value" in codes


def test_validate_campaign_record_rejects_inline_truth_theft_and_nonledger_parent_binding() -> None:
    raw = _base_record()
    raw["parent_binding"]["binding_type"] = "task_id"
    raw["task_register_snapshot"] = {"task_id": "shadow-copy"}
    raw["phases"][0]["receipt_payload"] = {"status": "ok"}

    result = contract.validate_campaign_record(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "forbidden_inline_truth" in codes
    assert "invalid_value" in codes
