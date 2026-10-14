from __future__ import annotations

import json
from pathlib import Path

from infra.controlplane import intent_family_v2_projection as projection
from infra.controlplane import v2_continuity


def _compiled_family() -> dict:
    return {
        "schema_version": "intent_family_v2_compilation_v1",
        "workflow_definition_version": "intent_family_v2",
        "parent_uid": "PIS-20260409-194437Z-310F",
        "source_intent_uid": "IS-20260409-190403Z-BFB6",
        "family_graph": {
            "parent_uid": "PIS-20260409-194437Z-310F",
            "source_intent_uid": "IS-20260409-190403Z-BFB6",
            "graph_fingerprint": "graph-demo-001",
            "recommended_execution_order": [
                "PIS-20260409-195204Z-A111",
                "PIS-20260409-195204Z-B222",
                "PIS-20260409-195204Z-C333",
            ],
            "nodes": [
                {"node_id": "IS-20260409-190403Z-BFB6", "node_kind": "source_intent", "stage": "intent", "title": "intent"},
                {"node_id": "PIS-20260409-194437Z-310F", "node_kind": "family_parent", "stage": "parent", "title": "parent"},
                {
                    "node_id": "PIS-20260409-195204Z-A111",
                    "node_kind": "execution_child",
                    "stage": "implement",
                    "title": "Implement core runtime",
                    "depends_on_uids": [],
                },
                {
                    "node_id": "PIS-20260409-195204Z-B222",
                    "node_kind": "intent_validation_suite",
                    "stage": "intent_validation",
                    "title": "Validate runtime",
                    "depends_on_uids": ["PIS-20260409-195204Z-A111"],
                },
                {
                    "node_id": "PIS-20260409-195204Z-C333",
                    "node_kind": "intent_family_closeout",
                    "stage": "closeout",
                    "title": "Close family",
                    "depends_on_uids": ["PIS-20260409-195204Z-A111", "PIS-20260409-195204Z-B222"],
                },
            ],
        },
        "job_specs": [
            {"job_id": "job_impl", "binding": {"id": "PIS-20260409-195204Z-A111"}},
            {"job_id": "job_validate", "binding": {"id": "PIS-20260409-195204Z-B222"}},
            {"job_id": "job_close", "binding": {"id": "PIS-20260409-195204Z-C333"}},
        ],
    }


def _admission_request(*, requested_stage: str = "narrow_canary", overrides: dict | None = None) -> dict:
    payload = {
        "request_id": "canary.request.default",
        "requested_owner": "intent_family_v2",
        "legacy_owner": "legacy_v1",
        "requested_stage": requested_stage,
        "family_parent_uids": ["PIS-20260409-194437Z-310F"],
        "covered_cutover_scope": {
            "family_parent_uid": "PIS-20260409-194437Z-310F",
            "workflow_kind": "intent_family_v2",
            "v2_owned_paths": [
                "scheduler.intent_family_v2.runtime_projection",
                "scheduler.intent_family_v2.operator_snapshot",
            ],
            "legacy_runtime_paths": ["legacy_v1.scheduler_loop"],
            "operator_surfaces": ["watch", "cli_summary", "operator_snapshot"],
            "excluded_workflows": ["unrelated_scheduler_workflows"],
        },
        "residual_legacy_ownership": ["legacy_v1.scheduler_loop"],
        "rollback_triggers": [
            "shadow_mismatch_detected",
            "operator_requested_rollback",
            "recovery_gap_detected",
        ],
        "deletion_budget": {
            "expected_removals": [
                "legacy_scheduler_projection_branch",
                "legacy_scheduler_family_gate",
            ],
            "minimum_removals": 2,
        },
    }
    if overrides:
        payload.update(overrides)
    return payload


def test_project_intent_family_v2_family_reuses_one_canonical_projection_for_all_surfaces() -> None:
    continuity_record = {
        "subject_kind": "family",
        "subject_id": "PIS-20260409-194437Z-310F",
        "workflow_kind": "intent_family_v2",
        "runtime_state": "running",
        "interrupted": True,
        "rerun_allowed": True,
    }
    latest_receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="family.demo.rerun",
        decision_utc="2026-04-09T21:00:00Z",
    )
    canonical = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "running", "active": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": ""},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": ""},
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=latest_receipt,
    )

    assert canonical["family_status"] == "active"
    assert canonical["current_stage"] == "implement"
    assert canonical["active_job"]["child_uid"] == "PIS-20260409-195204Z-A111"
    assert [row["child_uid"] for row in canonical["next_jobs"]] == [
        "PIS-20260409-195204Z-B222",
        "PIS-20260409-195204Z-C333",
    ]
    assert canonical["continuity"]["continuity_state"] == "rerun_ready"
    assert canonical["continuity"]["decision_source"] == "durable_receipt"
    assert {surface["current_stage"] for surface in canonical["surfaces"].values()} == {"implement"}
    assert {surface["active_job"]["child_uid"] for surface in canonical["surfaces"].values()} == {
        "PIS-20260409-195204Z-A111"
    }


def test_build_intent_family_v2_surface_bundle_routes_watch_cli_snapshot_and_reconciliation_through_canonical_projection() -> None:
    runtime_records = [
        {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "succeeded", "retired": True},
        {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": "running", "active": True},
        {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": ""},
    ]
    bundle = projection.build_intent_family_v2_surface_bundle(
        _compiled_family(),
        runtime_records=runtime_records,
        legacy_projection={
            "family_status": "queued",
            "current_stage": "closeout",
            "active_job": {"child_uid": "PIS-20260409-195204Z-C333"},
            "next_jobs": [],
            "blockers": [],
            "continuity": {},
        },
    )

    canonical = bundle["canonical_projection"]
    assert canonical["family_status"] == "active"
    assert canonical["current_stage"] == "intent_validation"
    assert canonical["active_job"]["child_uid"] == "PIS-20260409-195204Z-B222"
    assert [row["child_uid"] for row in canonical["next_jobs"]] == ["PIS-20260409-195204Z-C333"]

    for surface_name in ("watch", "cli_summary", "operator_snapshot", "reconciliation_report"):
        surface = bundle[surface_name]
        assert surface["family_status"] == canonical["family_status"]
        assert surface["current_stage"] == canonical["current_stage"]
        assert surface["active_job"]["child_uid"] == canonical["active_job"]["child_uid"]
        assert [row["child_uid"] for row in surface["next_jobs"]] == [
            row["child_uid"] for row in canonical["next_jobs"]
        ]

    assert bundle["reconciliation_report"]["projection_source"] == "canonical_projection"
    assert bundle["reconciliation_report"]["shadow_report"]["status"] == "mismatch"


def test_compare_intent_family_v2_shadow_reports_diffs_without_mutating_canonical_projection() -> None:
    canonical = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "running", "active": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": ""},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": ""},
        ],
    )
    original = json.loads(json.dumps(canonical))

    legacy_projection = {
        "family_status": "blocked",
        "current_stage": "intent_validation",
        "active_job": {"child_uid": "PIS-20260409-195204Z-B222"},
        "next_jobs": [{"child_uid": "PIS-20260409-195204Z-C333"}],
        "blockers": [{"reason": "dependency_pending:PIS-20260409-195204Z-A111"}],
        "continuity": {"continuity_state": "blocked"},
    }
    report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection=legacy_projection,
        compiled_shadow={
            "family_status": "queued",
            "current_stage": "implement",
            "active_job": {"child_uid": "PIS-20260409-195204Z-A111"},
            "next_jobs": [{"child_uid": "PIS-20260409-195204Z-C333"}],
            "blockers": [],
            "continuity": {"continuity_state": "rerun_ready"},
        },
        runtime_shadow={
            "family_status": "active",
            "current_stage": "intent_validation",
            "active_job": {"child_uid": "PIS-20260409-195204Z-B222"},
            "next_jobs": [{"child_uid": "PIS-20260409-195204Z-C333"}],
            "blockers": [{"reason": "runtime_conflict_detected"}],
            "continuity": {"continuity_state": "blocked"},
            "legacy_owner": "v1",
        },
    )

    assert report["status"] == "mismatch"
    assert any(row["source"] == "legacy_projection" and row["path"] == "family_status" for row in report["diffs"])
    assert any(row["source"] == "compiled_shadow" and row["path"] == "family_status" for row in report["diffs"])
    assert any(row["source"] == "runtime_shadow" and row["path"] == "active_job.child_uid" for row in report["diffs"])
    assert any(row["source"] == "runtime_shadow" and row["path"] == "blockers" for row in report["diffs"])
    assert report["isolated_artifacts"]["runtime_shadow"]["legacy_owner"] == "v1"
    assert canonical == original


def test_evaluate_intent_family_v2_canary_admission_allows_only_exact_family_scope_after_shadow_match() -> None:
    family_projection = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "succeeded", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": "validated", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": "", "retired": False},
        ],
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        family_projection,
        legacy_projection={
            "family_status": "queued",
            "current_stage": "closeout",
            "active_job": {},
            "next_jobs": [{"child_uid": "PIS-20260409-195204Z-C333"}],
            "blockers": [],
            "continuity": {},
        },
    )

    allowed = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(overrides={"request_id": "canary.request.001"}),
    )
    denied = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(
            overrides={
                "request_id": "canary.request.002",
                "family_parent_uids": ["PIS-20260409-194437Z-310F", "PIS-20260409-999999Z-WIDE"],
            }
        ),
    )

    assert allowed["allowed"] is True
    assert allowed["decision"] == "admitted"
    assert allowed["canonical_owner"] == "intent_family_v2"
    assert allowed["migration_law"]["active_gate"] == "narrow_canary"
    assert allowed["migration_law"]["replacement_ownership"]["allowed"] is False
    assert allowed["migration_law"]["legacy_runtime_retirement"]["eligible"] is False
    assert denied["allowed"] is False
    assert denied["reason"] == "canary_scope_must_match_exactly_one_family"


def test_evaluate_intent_family_v2_canary_admission_blocks_when_cutover_scope_is_ambiguous() -> None:
    family_projection = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "succeeded", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": "validated", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": "", "retired": False},
        ],
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        family_projection,
        legacy_projection={
            "family_status": "queued",
            "current_stage": "closeout",
            "active_job": {},
            "next_jobs": [{"child_uid": "PIS-20260409-195204Z-C333"}],
            "blockers": [],
            "continuity": {},
        },
    )

    denied = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(
            overrides={
                "covered_cutover_scope": {
                    "family_parent_uid": "PIS-20260409-194437Z-310F",
                    "workflow_kind": "intent_family_v2",
                    "legacy_runtime_paths": ["legacy_v1.scheduler_loop"],
                    "operator_surfaces": ["watch"],
                },
                "deletion_budget": {"expected_removals": ["legacy_scheduler_projection_branch"], "minimum_removals": 2},
                "residual_legacy_ownership": [],
            }
        ),
    )

    assert denied["allowed"] is False
    assert denied["reason"] == "covered_cutover_scope_must_be_explicit"
    assert denied["migration_law"]["active_gate"] == "blocked"
    assert denied["migration_law"]["legacy_runtime_retirement"]["covered_scope_state"] == "ambiguous"


def test_evaluate_intent_family_v2_canary_admission_denies_replacement_ownership_until_final_closeout() -> None:
    family_projection = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "succeeded", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": "validated", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": "", "retired": False},
        ],
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        family_projection,
        legacy_projection={
            "family_status": "queued",
            "current_stage": "closeout",
            "active_job": {},
            "next_jobs": [{"child_uid": "PIS-20260409-195204Z-C333"}],
            "blockers": [],
            "continuity": {},
        },
    )

    denied = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(
            requested_stage="replacement_owner",
            overrides={"request_id": "canary.request.replacement.001"},
        ),
    )

    assert denied["allowed"] is False
    assert denied["reason"] == "replacement_ownership_requires_final_closeout"
    assert denied["migration_law"]["active_gate"] == "narrow_canary"
    assert denied["migration_law"]["replacement_ownership"]["allowed"] is False


def test_evaluate_intent_family_v2_canary_admission_denies_when_continuity_blocked() -> None:
    family_projection = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "succeeded", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": "validated", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": "", "retired": False},
        ],
        continuity_record={
            "subject_kind": "family",
            "subject_id": "PIS-20260409-194437Z-310F",
            "workflow_kind": "intent_family_v2",
            "runtime_state": "running",
            "operator_block_reason": "dependency_wait",
        },
    )
    shadow_report = {"status": "match"}

    denied = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(overrides={"request_id": "canary.request.continuity.blocked"}),
    )

    assert family_projection["continuity"]["continuity_state"] == "blocked"
    assert denied["allowed"] is False
    assert denied["reason"] == "continuity_blocked_blocks_canary"
    assert denied["migration_law"]["active_gate"] == "blocked"


def test_evaluate_intent_family_v2_canary_admission_denies_when_continuity_repairing() -> None:
    continuity_record = {
        "subject_kind": "family",
        "subject_id": "PIS-20260409-194437Z-310F",
        "workflow_kind": "intent_family_v2",
        "runtime_state": "failed",
        "repair_needed": True,
        "repair_allowed": True,
        "revalidation_required": True,
    }
    latest_receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="family.demo.repairing",
        decision_utc="2026-04-09T21:05:00Z",
    )
    family_projection = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "succeeded", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": "validated", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": "", "retired": False},
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=latest_receipt,
    )
    shadow_report = {"status": "match"}

    denied = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(overrides={"request_id": "canary.request.continuity.repairing"}),
    )

    assert family_projection["continuity"]["continuity_state"] == "repairing"
    assert denied["allowed"] is False
    assert denied["reason"] == "continuity_repairing_blocks_canary"
    assert denied["migration_law"]["active_gate"] == "blocked"


def test_evaluate_intent_family_v2_canary_admission_respects_existing_rollback_receipt(tmp_path: Path) -> None:
    family_projection = projection.project_intent_family_v2_family(
        _compiled_family(),
        runtime_records=[
            {"child_uid": "PIS-20260409-195204Z-A111", "job_id": "job_impl", "runtime_state": "succeeded", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-B222", "job_id": "job_validate", "runtime_state": "validated", "retired": True},
            {"child_uid": "PIS-20260409-195204Z-C333", "job_id": "job_close", "runtime_state": "closed", "retired": True},
        ],
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        family_projection,
        legacy_projection={
            "family_status": "completed",
            "current_stage": "closeout",
            "active_job": {},
            "next_jobs": [],
            "blockers": [],
            "continuity": {},
        },
    )
    admitted = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(overrides={"request_id": "canary.request.003"}),
    )
    receipt = projection.build_intent_family_v2_canary_receipt(
        {
            **admitted,
            "decision": "rolled_back",
            "reason": "operator_requested_rollback",
        },
        decision_id="canary.rollback.001",
        decision_utc="2026-04-09T21:10:00Z",
        evidence_refs=["runtime/state/demo_shadow_report.json"],
    )
    path = projection.write_intent_family_v2_canary_receipt(receipt, repo_root=tmp_path)

    loaded = json.loads(path.read_text(encoding="utf-8"))
    rolled_back = projection.evaluate_intent_family_v2_canary_admission(
        family_projection,
        shadow_report=shadow_report,
        admission_request=_admission_request(overrides={"request_id": "canary.request.004"}),
        latest_receipt=loaded,
    )

    assert loaded["schema_version"] == "intent_family_v2_canary_receipt_v1"
    assert loaded["migration_law"]["uid"] == "PIS-20260409-195206Z-G4B7"
    assert loaded["migration_law"]["covered_cutover_scope"]["v2_owned_paths"] == [
        "scheduler.intent_family_v2.runtime_projection",
        "scheduler.intent_family_v2.operator_snapshot",
    ]
    assert loaded["retirement_gate"]["eligible"] is False
    assert rolled_back["decision"] == "rolled_back"
    assert rolled_back["canonical_owner"] == "legacy_v1"
    assert rolled_back["rollback_boundary"]["scope"] == "family_only"
