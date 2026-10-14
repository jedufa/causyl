from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import recursive_plan_record_contract as contract


def _base_record() -> dict:
    return {
        "schema_version": "recursive_plan_record_contract_v1",
        "plan_id": "plan_root_demo_001",
        "root_plan_id": "plan_root_demo_001",
        "root_node_id": "node_root",
        "root_binding": {
            "binding_type": "ledger_uid",
            "binding_id": "PIS-20260409-153903Z-7C65",
        },
        "parent_plan_id": "",
        "parent_job_id": "sj_recursive_seed_001",
        "plan_status": "validated",
        "plan_truth_refs": {
            "ledger_uids": ["PIS-20260409-153903Z-7C65"],
            "campaign_ids": ["camp_recursive_demo_001"],
            "job_ids": ["sj_recursive_seed_001"],
            "run_ids": [],
            "receipt_refs": ["runtime/state/scheduled_jobs/receipts/sj_recursive_seed_001.json"],
            "continuation_packet_ids": ["pkt_recursive_seed_001"],
            "artifact_refs": ["governance/recursive_plan_record_contract_v1.json"],
        },
        "plan_event_refs": [
            "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-154915Z-E6CF__define-recursive-lineage-ancestry-path-and-plan-record-contract/events.jsonl"
        ],
        "budget_adaptive_receipt": {
            "policy_uid": "PIS-20260411-081500Z-A4D1",
            "policy_version": "1.0.0",
            "selected_mode": "standard",
            "convergence_posture": "bounded_convergence",
            "wave_cap": 2,
            "requested_builder_waves": 2,
            "candidate_continuation_decision": "continue",
            "effective_continuation_decision": "continue",
            "continuation_rationale": "One more bounded pass is still inside the mode envelope.",
            "policy_state": "accepted",
            "advisory_only": False,
            "reason_codes": [],
            "has_durable_proof": True,
            "continuation_packet_id": "pkt_recursive_seed_001",
            "evidence_pack_refs": [],
            "minimum_deliverables": [
                "structured synthesis",
                "prioritized hypothesis list",
                "verification notes",
                "explicit next-step recommendation"
            ],
        },
        "nodes": [
            {
                "node_id": "node_root",
                "job_shape": "verification_loop_job",
                "node_status": "validated",
                "declared_depth": 0,
                "ancestry_path": [],
                "parent_node_id": "",
                "child_node_ids": ["node_child"],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-153903Z-7C65"],
                    "campaign_ids": ["camp_recursive_demo_001"],
                    "job_ids": ["sj_recursive_seed_001"],
                    "run_ids": [],
                    "receipt_refs": ["runtime/state/scheduled_jobs/receipts/sj_recursive_seed_001.json"],
                    "continuation_packet_ids": [],
                    "artifact_refs": ["docs/contracts/contract_recursive_plan_record_v1.md"],
                },
                "node_event_refs": [
                    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-154915Z-E6CF__define-recursive-lineage-ancestry-path-and-plan-record-contract/events.jsonl#node_root"
                ],
            },
            {
                "node_id": "node_child",
                "job_shape": "verification_loop_job",
                "node_status": "planned",
                "declared_depth": 1,
                "ancestry_path": ["node_root"],
                "parent_node_id": "node_root",
                "child_node_ids": [],
                "truth_refs": {
                    "ledger_uids": ["PIS-20260409-154915Z-E6CF"],
                    "campaign_ids": ["camp_recursive_demo_001"],
                    "job_ids": ["sj_recursive_seed_001"],
                    "run_ids": [],
                    "receipt_refs": [],
                    "continuation_packet_ids": ["pkt_recursive_seed_001"],
                    "artifact_refs": ["docs/contracts/contract_recursive_plan_record_v1.md"],
                },
                "node_event_refs": [
                    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-154915Z-E6CF__define-recursive-lineage-ancestry-path-and-plan-record-contract/events.jsonl#node_child"
                ],
            },
        ],
        "dependency_edges": [
            {
                "edge_id": "edge_child_depends_on_root",
                "from_node_id": "node_child",
                "to_node_id": "node_root",
                "edge_kind": "depends_on",
            }
        ],
    }


def test_recursive_plan_record_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.recursive_plan_record_contract_v1()

    assert payload.version == "recursive_plan_record_contract_v1"
    assert payload.uid == "PIS-20260409-154915Z-E6CF"
    assert payload.canonical_artifact_rel.as_posix() == "governance/recursive_plan_record_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_recursive_plan_record_v1.md"
    assert payload.plan_status_vocab == contract.PLAN_STATUS_VOCAB
    assert payload.append_only_fields == ("plan_event_refs", "node_event_refs")


def test_recursive_plan_record_contract_artifact_matches_runtime_exports() -> None:
    payload = json.loads(Path("governance/recursive_plan_record_contract_v1.json").read_text(encoding="utf-8"))

    assert payload["canonical_doc_rel"] == contract.CONTRACT_DOC_REL.as_posix()
    assert tuple(payload["plan_required_fields"]) == contract.PLAN_REQUIRED_FIELDS
    assert tuple(payload["node_required_fields"]) == contract.NODE_REQUIRED_FIELDS
    assert tuple(payload["edge_required_fields"]) == contract.EDGE_REQUIRED_FIELDS
    assert tuple(payload["truth_ref_fields"]) == contract.TRUTH_REF_FIELDS


def test_validate_recursive_plan_record_contract_v1_returns_no_issues() -> None:
    assert contract.validate_recursive_plan_record_contract_v1() == ()


def test_validate_recursive_plan_record_accepts_explicit_lineage_and_dependency_edges() -> None:
    result = contract.validate_recursive_plan_record(_base_record())
    node_map = {row["node_id"]: row for row in result["normalized_record"]["nodes"]}

    assert result["ok"] is True
    assert result["errors"] == []
    assert node_map["node_child"]["ancestry_path"] == ["node_root"]
    assert result["normalized_record"]["budget_adaptive_receipt"]["effective_continuation_decision"] == "continue"
    assert result["computed_fingerprint"] != ""


def test_validate_recursive_plan_record_rejects_malformed_ancestry_duplicates_and_missing_edge_targets() -> None:
    raw = _base_record()
    raw["nodes"][1]["node_id"] = "node_root"
    raw["nodes"][1]["declared_depth"] = 2
    raw["nodes"][1]["ancestry_path"] = ["node_missing"]
    raw["dependency_edges"][0]["to_node_id"] = "node_unknown"

    result = contract.validate_recursive_plan_record(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "duplicate_node_id" in codes
    assert "malformed_ancestry_path" in codes
    assert "undeclared_edge_target" in codes


def test_validate_recursive_plan_record_rejects_parent_child_mismatch_and_inline_truth_theft() -> None:
    raw = _base_record()
    raw["task_register_snapshot"] = {"task_id": "shadow_copy"}
    raw["nodes"][0]["child_node_ids"] = []
    raw["nodes"][1]["receipt_payload"] = {"status": "pretend"}

    result = contract.validate_recursive_plan_record(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "forbidden_inline_truth" in codes
    assert "parent_child_mismatch" in codes


def test_validate_recursive_plan_record_rejects_budget_adaptive_receipt_drift() -> None:
    raw = _base_record()
    raw["budget_adaptive_receipt"]["wave_cap"] = 5
    raw["budget_adaptive_receipt"]["effective_continuation_decision"] = "stop"

    result = contract.validate_recursive_plan_record(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "invalid_value" in codes
    assert any(
        row["field"] == "budget_adaptive_receipt.wave_cap"
        for row in result["errors"]
    )
