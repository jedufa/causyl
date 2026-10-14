from __future__ import annotations

from infra.controlplane import fanout_child_contract as contract


def _base_work_order() -> dict:
    return {
        "schema_version": "fanout_child_contract_v1",
        "work_order_id": "wo_demo_001",
        "parent_uid": "PIS-20260409-081310Z-E085",
        "child_uid": "PIS-20260409-082101Z-B2G2",
        "child_role": "builder",
        "scope_fence": {
            "objective": "Define the child contract only.",
            "in_scope": ["infra/controlplane/fanout_child_contract.py", "tests/control_plane/test_fanout_child_contract.py"],
            "out_of_scope": ["infra/controlplane/scheduled_jobs.py"],
        },
        "ownership_fence": {
            "owned_paths": ["infra/controlplane", "tests/control_plane/test_fanout_child_contract.py"],
            "forbidden_parent_claims": [
                "integrator_owner",
                "integration_disposition",
                "final_verdict",
                "operator_summary_state",
                "child_roster",
                "fanout_stage",
            ],
        },
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs": ["governance/fanout_master_contract_v1.json"],
        "forbidden_context_refs": [],
        "expected_evidence": [
            {
                "evidence_id": "ev_contract_tests",
                "kind": "unit_test",
                "summary": "Targeted contract tests for normalization and fail-closed rejection.",
                "required": True,
            }
        ],
        "next_decision": {
            "decision_owner": "fanout_master",
            "question": "Is the child packet clean enough for parent integration?",
            "recommended_action": "",
        },
    }


def _base_packet() -> dict:
    work_order = _base_work_order()
    return {
        "schema_version": "fanout_child_contract_v1",
        "packet_id": "pkt_demo_001",
        "work_order_id": work_order["work_order_id"],
        "parent_uid": work_order["parent_uid"],
        "child_uid": work_order["child_uid"],
        "status": "partial",
        "summary": "Defined the contract and left launch-time integration to the parent-owned fanout flow.",
        "scope_fence": dict(work_order["scope_fence"]),
        "ownership_fence": dict(work_order["ownership_fence"]),
        "evidence_summary": [
            {
                "evidence_id": "ev_contract_tests",
                "status": "provided",
                "summary": "Focused tests cover success and fail-closed rejection cases.",
                "refs": ["tests/control_plane/test_fanout_child_contract.py"],
            }
        ],
        "changed_files": ["infra/controlplane/fanout_child_contract.py"],
        "caveats": ["No parent integration or launch wiring is claimed in this child slice."],
        "unresolved_questions": ["Should the parent persist packets inline or by reference only?"],
        "next_decision": {
            "decision_owner": "fanout_master",
            "question": "Is the child packet clean enough for parent integration?",
            "recommended_action": "collect",
        },
    }


def test_fanout_child_contract_v1_exposes_expected_metadata() -> None:
    payload = contract.fanout_child_contract_v1()

    assert payload.version == "fanout_child_contract_v1"
    assert payload.uid == "PIS-20260409-082101Z-B2G2"
    assert payload.canonical_artifact_rel.as_posix() == "governance/fanout_child_contract_v1.json"
    assert payload.canonical_doc_rel.as_posix() == "docs/contracts/contract_fanout_child_execution_v1.md"
    assert payload.required_context_bundle_rule == "bundle_plus_delta_minus_forbidden"
    assert payload.forbidden_parent_claims == (
        "integrator_owner",
        "integration_disposition",
        "final_verdict",
        "operator_summary_state",
        "child_roster",
        "fanout_stage",
    )


def test_validate_child_work_order_and_context_resolution_succeed() -> None:
    result = contract.validate_child_work_order(_base_work_order())
    resolved = contract.resolve_child_context_refs(_base_work_order())

    assert result["ok"] is True
    assert result["computed_fingerprint"] != ""
    assert resolved["ok"] is True
    assert resolved["effective_refs"][:3] == [
        "governance/agents/prompts/_all_codex.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
    ]
    assert resolved["effective_refs"][-1] == "governance/fanout_master_contract_v1.json"


def test_validate_child_work_order_rejects_forbidden_bundle_ref_and_missing_claim_fence() -> None:
    raw = _base_work_order()
    raw["forbidden_context_refs"] = ["governance/agents/prompts/_all_codex.md"]
    raw["ownership_fence"]["forbidden_parent_claims"] = ["final_verdict"]

    result = contract.validate_child_work_order(raw)

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "forbidden_bundle_ref" in codes
    assert "invalid_value" in codes


def test_validate_child_return_packet_succeeds_against_bound_work_order() -> None:
    result = contract.validate_child_return_packet(_base_packet(), work_order=_base_work_order())

    assert result["ok"] is True
    assert result["computed_fingerprint"] != ""
    assert result["normalized_work_order"]["work_order_id"] == "wo_demo_001"


def test_validate_child_return_packet_rejects_missing_required_evidence_on_success() -> None:
    raw = _base_packet()
    raw["status"] = "succeeded"
    raw["evidence_summary"][0]["status"] = "missing"

    result = contract.validate_child_return_packet(raw, work_order=_base_work_order())

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "contradictory_status" in codes


def test_validate_child_return_packet_rejects_unbound_or_mismatched_work_order() -> None:
    raw = _base_packet()
    raw["status"] = "succeeded"

    unbound = contract.validate_child_return_packet(raw)

    assert unbound["ok"] is False
    assert unbound["failure_class"] == "child_return_packet_validation_blocked"
    assert any(row["field"] == "work_order" for row in unbound["errors"])

    mismatched_work_order = _base_work_order()
    mismatched_work_order["expected_evidence"] = [
        {
            "evidence_id": "ev_other_required",
            "kind": "unit_test",
            "summary": "Different required evidence from the bound work order.",
            "required": True,
        }
    ]

    mismatched = contract.validate_child_return_packet(raw, work_order=mismatched_work_order)

    codes = {row["code"] for row in mismatched["errors"]}
    assert mismatched["ok"] is False
    assert "missing_evidence_summary" in codes
    assert "contradictory_status" in codes


def test_validate_child_return_packet_rejects_ownership_drift_and_parent_claims() -> None:
    raw = _base_packet()
    raw["changed_files"] = ["infra/mobile/android/app/src/main/java/com/eiel/chat/ui/AppShell.kt"]
    raw["final_verdict"] = "PASS"

    result = contract.validate_child_return_packet(raw, work_order=_base_work_order())

    codes = {row["code"] for row in result["errors"]}
    assert result["ok"] is False
    assert "ownership_violation" in codes
    assert "forbidden_parent_claim" in codes


def test_validate_fanout_child_contract_v1_returns_no_issues() -> None:
    assert contract.validate_fanout_child_contract_v1() == ()
