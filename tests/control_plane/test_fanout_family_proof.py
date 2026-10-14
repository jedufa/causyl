from __future__ import annotations

from pathlib import Path

from infra.controlplane import fanout_child_contract as child_contract
from infra.controlplane import fanout_master_contract as master_contract
from infra.controlplane import fanout_master_integration as integration


def _bound_proof_artifact() -> Path:
    base = Path("governance/ledger/patch_instruction_sheets/causyl")
    for ledger_status in ("active", "verified"):
        candidate = (
            base
            / ledger_status
            / "2026"
            / "04"
            / "PIS-20260409-082104Z-E5K5__prove-fanout-job-contract-end-to-end"
            / "artifacts"
            / "verification"
            / "fanout_job_family_proof.md"
        )
        if candidate.exists():
            return candidate
    return (
        base
        / "verified"
        / "2026"
        / "04"
        / "PIS-20260409-082104Z-E5K5__prove-fanout-job-contract-end-to-end"
        / "artifacts"
        / "verification"
        / "fanout_job_family_proof.md"
    )


def _work_order() -> dict:
    return {
        "schema_version": "fanout_child_contract_v1",
        "work_order_id": "wo_family_proof_001",
        "parent_uid": "PIS-20260409-081310Z-E085",
        "child_uid": "PIS-20260409-082101Z-B2G2",
        "child_role": "builder",
        "scope_fence": {
            "objective": "Bounded fanout child packet for end-to-end family proof.",
            "in_scope": [
                "infra/controlplane/fanout_child_contract.py",
                "infra/controlplane/fanout_master_integration.py",
                "tests/control_plane/test_fanout_family_proof.py",
            ],
            "out_of_scope": ["infra/controlplane/scheduled_jobs.py"],
        },
        "ownership_fence": {
            "owned_paths": ["infra/controlplane", "tests/control_plane/test_fanout_family_proof.py"],
            "forbidden_parent_claims": list(master_contract.FORBIDDEN_CHILD_CLAIMS),
        },
        "required_context_bundle": "lab_core_v1",
        "additional_context_refs": ["governance/fanout_master_contract_v1.json"],
        "forbidden_context_refs": [],
        "expected_evidence": [
            {
                "evidence_id": "ev_family_proof",
                "kind": "unit_test",
                "summary": "Focused family proof regression.",
                "required": True,
            }
        ],
        "next_decision": {
            "decision_owner": "fanout_master",
            "question": "Can the master integrate this child packet deterministically?",
            "recommended_action": "collect",
        },
    }


def _return_packet(*, packet_id: str, status: str, summary: str, changed_files: list[str], known_gap: str | None = None) -> dict:
    packet = {
        "schema_version": "fanout_child_contract_v1",
        "packet_id": packet_id,
        "work_order_id": "wo_family_proof_001",
        "parent_uid": "PIS-20260409-081310Z-E085",
        "child_uid": "PIS-20260409-082101Z-B2G2",
        "status": status,
        "summary": summary,
        "scope_fence": {
            "objective": "Bounded fanout child packet for end-to-end family proof.",
            "in_scope": [
                "infra/controlplane/fanout_child_contract.py",
                "infra/controlplane/fanout_master_integration.py",
                "tests/control_plane/test_fanout_family_proof.py",
            ],
            "out_of_scope": ["infra/controlplane/scheduled_jobs.py"],
        },
        "ownership_fence": {
            "owned_paths": ["infra/controlplane", "tests/control_plane/test_fanout_family_proof.py"],
            "forbidden_parent_claims": list(master_contract.FORBIDDEN_CHILD_CLAIMS),
        },
        "evidence_summary": [
            {
                "evidence_id": "ev_family_proof",
                "status": "provided",
                "summary": "Focused family proof regression.",
                "refs": ["tests/control_plane/test_fanout_family_proof.py"],
            }
        ],
        "changed_files": changed_files,
        "caveats": [] if known_gap is None else [known_gap],
        "unresolved_questions": [],
        "next_decision": {
            "decision_owner": "fanout_master",
            "question": "Can the master integrate this child packet deterministically?",
            "recommended_action": "collect",
        },
    }
    if status == "partial" and known_gap is not None:
        packet["unresolved_questions"] = [known_gap]
    return packet


def _worker_from_packet(packet: dict, *, worker_id: str, worker_status: str, summary_path: str) -> dict:
    evidence_refs = list(packet["evidence_summary"][0]["refs"])
    worker = {
        "worker_id": worker_id,
        "status": worker_status,
        "changed_files": list(packet["changed_files"]),
        "summary_path": summary_path,
        "local_checks": evidence_refs,
    }
    if packet["status"] == "partial":
        worker["known_gaps"] = list(packet["caveats"] or packet["unresolved_questions"])
    return worker


def _swarm() -> dict:
    return {
        "success_contract": {
            "accepted_statuses": ["finished"],
            "require_changed_files": True,
        },
        "evidence_requirements": {
            "require_summary_path": True,
            "require_worker_artifacts": False,
        },
        "write_policy": {
            "allowed_paths": ["infra/controlplane"],
            "max_changed_files": 5,
        },
        "retry_policy": {
            "child_relaunch_limit": 1,
            "master_rerun_limit": 0,
        },
        "launch_options": {},
    }


def test_fanout_family_proof_happy_path_is_deterministic_from_child_validation_to_terminal_projection() -> None:
    work_order = _work_order()
    packet = _return_packet(
        packet_id="pkt_family_happy_001",
        status="succeeded",
        summary="Delivered one bounded child result for deterministic master integration.",
        changed_files=["infra/controlplane/fanout_master_integration.py"],
    )

    validated = child_contract.validate_child_return_packet(packet, work_order=work_order)
    assert validated["ok"] is True

    integrated = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/fanout_master_integration.py"]},
            "artifacts": {"summary_path": "/tmp/fanout_happy.md"},
            "workers": [
                _worker_from_packet(
                    packet,
                    worker_id="builder_child_001",
                    worker_status="delivered",
                    summary_path="/tmp/child_happy.md",
                )
            ],
        },
        swarm=_swarm(),
    )
    projected = master_contract.project_fanout_state(
        {
            "status": "finished",
            "phase": "completed",
            "workers": [
                _worker_from_packet(
                    packet,
                    worker_id="builder_child_001",
                    worker_status="delivered",
                    summary_path="/tmp/child_happy.md",
                )
            ],
        },
        as_terminal_report=True,
        integrated_result=integrated,
    )

    assert integrated["classification"] == "complete_success"
    assert integrated["integration_disposition"] == "integrated_clean"
    assert integrated["final_verdict"] == "success_deterministic"
    assert integrated["keep_child_ids"] == ["builder_child_001"]
    assert projected["master_owner"] == "fanout_master_release_one"
    assert projected["fanout_stage"] == "terminal_reporting"
    assert projected["integration_disposition"] == "integrated_clean"
    assert projected["final_verdict"] == "success_deterministic"
    assert projected["child_roster"] == ["builder_child_001"]


def test_fanout_family_proof_negative_paths_keep_malformed_partial_and_conflict_cases_explicit() -> None:
    work_order = _work_order()
    malformed_packet = _return_packet(
        packet_id="pkt_family_bad_001",
        status="succeeded",
        summary="Looks superficially valid but is intentionally unbound.",
        changed_files=["infra/controlplane/fanout_child_contract.py"],
    )
    malformed = child_contract.validate_child_return_packet(malformed_packet)
    assert malformed["ok"] is False
    assert malformed["failure_class"] == "child_return_packet_validation_blocked"
    assert any(row["field"] == "work_order" for row in malformed["errors"])

    keep_packet = _return_packet(
        packet_id="pkt_family_keep_001",
        status="succeeded",
        summary="Delivered clean child output.",
        changed_files=["infra/controlplane/fanout_master_integration.py"],
    )
    partial_packet = _return_packet(
        packet_id="pkt_family_partial_001",
        status="partial",
        summary="Delivered useful child output with a declared proof gap.",
        changed_files=["infra/controlplane/fanout_master_contract.py"],
        known_gap="verifier_followup_pending",
    )
    assert child_contract.validate_child_return_packet(keep_packet, work_order=work_order)["ok"] is True
    assert child_contract.validate_child_return_packet(partial_packet, work_order=work_order)["ok"] is True

    partial = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {
                "changed_files": [
                    "infra/controlplane/fanout_master_integration.py",
                    "infra/controlplane/fanout_master_contract.py",
                ]
            },
            "artifacts": {"summary_path": "/tmp/fanout_partial.md"},
            "workers": [
                _worker_from_packet(
                    keep_packet,
                    worker_id="builder_child_keep",
                    worker_status="delivered",
                    summary_path="/tmp/child_keep.md",
                ),
                _worker_from_packet(
                    partial_packet,
                    worker_id="builder_child_partial",
                    worker_status="delivered_with_known_gaps",
                    summary_path="/tmp/child_partial.md",
                ),
            ],
        },
        swarm=_swarm(),
    )

    assert partial["classification"] == "partial_success"
    assert partial["keep_child_ids"] == ["builder_child_keep", "builder_child_partial"]
    assert partial["partial_child_ids"] == ["builder_child_partial"]
    assert partial["restart_reconciliation"]["status"] == "partial_collection_reconciled"
    assert partial["restart_reconciliation"]["completed_child_ids"] == [
        "builder_child_keep",
        "builder_child_partial",
    ]
    assert partial["retry_boundaries"]["child_relaunch"]["state"] == "not_needed"

    conflicting = integration.integrate_swarm_result(
        state={
            "status": "finished",
            "phase": "completed",
            "integration": {"changed_files": ["infra/controlplane/fanout_master_integration.py"]},
            "artifacts": {"summary_path": "/tmp/fanout_conflict.md"},
            "workers": [
                _worker_from_packet(
                    keep_packet,
                    worker_id="builder_child_dup",
                    worker_status="delivered",
                    summary_path="/tmp/child_dup_1.md",
                ),
                _worker_from_packet(
                    keep_packet,
                    worker_id="builder_child_dup",
                    worker_status="delivered",
                    summary_path="/tmp/child_dup_2.md",
                ),
            ],
        },
        swarm=_swarm(),
    )

    assert conflicting["classification"] == "conflicting_outputs"
    assert conflicting["final_verdict"] == "blocked_requires_review"
    assert conflicting["restart_reconciliation"]["status"] == "blocked_on_conflicting_receipts"
    assert conflicting["restart_reconciliation"]["duplicate_child_ids"] == ["builder_child_dup"]
    assert conflicting["retry_boundaries"]["child_relaunch"]["state"] == "blocked_by_conflict"


def test_bound_fanout_family_proof_artifact_lists_happy_path_negative_paths_and_antipattern_boundary() -> None:
    artifact = _bound_proof_artifact()
    assert artifact.exists()
    body = artifact.read_text(encoding="utf-8")

    assert "PIS-20260409-082104Z-E5K5" in body
    assert "PIS-20260409-082100Z-A1F1" in body
    assert "PIS-20260409-082101Z-B2G2" in body
    assert "PIS-20260409-082102Z-C3H3" in body
    assert "PIS-20260409-082103Z-D4J4" in body
    assert "happy_path_single_child_delivered" in body
    assert "malformed_unbound_child_packet" in body
    assert "partial_success_with_declared_gap" in body
    assert "duplicate_receipt_conflict_restart_block" in body
    assert "peer-coordinated swarm behavior" in body
    assert "fanout_master_release_one" in body
