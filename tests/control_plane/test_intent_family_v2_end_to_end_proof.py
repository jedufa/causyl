from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.controlplane import intent_family_v2_compiler as compiler
from infra.controlplane import intent_family_v2_closeout as closeout
from infra.controlplane import intent_family_v2_projection as projection
from infra.controlplane import v2_continuity


def _write_workflow_contract(root: Path, *, extra_fields: dict | None = None) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    if extra_fields:
        payload.update(extra_fields)
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_family(
    root: Path,
    *,
    parent_uid: str = "PIS-20260409-194437Z-310F",
    intent_uid: str = "IS-20260409-190403Z-BFB6",
    cyclic: bool = False,
) -> str:
    parent_dir = (
        root
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "active"
        / "2026"
        / "04"
        / f"{parent_uid}__parent"
    )
    parent_dir.mkdir(parents=True, exist_ok=True)
    impl_uid = "PIS-20260409-195205Z-F3A6"
    validation_uid = "PIS-20260409-195248Z-BCC7"
    closeout_uid = "PIS-20260409-195248Z-4066"
    child_uids = [impl_uid, validation_uid, closeout_uid]
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Intent family v2 parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": child_uids,
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")

    rows = [
        (
            impl_uid,
            "prove-runtime",
            "Prove runtime semantics",
            "execution_child",
            1,
            [closeout_uid] if cyclic else [],
        ),
        (
            validation_uid,
            "intent-validation",
            "Intent validation",
            "intent_validation_suite",
            2,
            [impl_uid],
        ),
        (
            closeout_uid,
            "closeout",
            "Close family",
            "intent_family_closeout",
            3,
            [impl_uid, validation_uid],
        ),
    ]
    for uid, slug, title, relationship, rank, depends_on in rows:
        child_dir = (
            root
            / "governance"
            / "ledger"
            / "patch_instruction_sheets"
            / "causyl"
            / "active"
            / "2026"
            / "04"
            / f"{uid}__{slug}"
        )
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
                        "depends_on": depends_on,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    prompt_path = root / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(
        "## 2. Implement One Child PIS\n\n```text\nImplement ledger item `<CHILD_UID>` in the repo.\n```\n",
        encoding="utf-8",
    )
    return parent_uid


def _compile_family(root: Path, *, cyclic: bool = False) -> dict:
    _write_workflow_contract(root)
    parent_uid = _write_family(root, cyclic=cyclic)
    return compiler.compile_intent_family_v2(
        parent_uid=parent_uid,
        run_at_utc="2026-04-10T10:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=4,
        repo_root=root,
    )


def test_intent_family_v2_end_to_end_proof_report_captures_compile_projection_restart_and_shadow_parity(
    tmp_path: Path,
) -> None:
    compiled = _compile_family(tmp_path)
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "interrupted",
        "interrupted": True,
        "rerun_allowed": True,
        "revalidation_required": True,
    }
    decision = v2_continuity.decide_continuity_recovery(continuity_record)
    receipt = v2_continuity.build_continuity_receipt(
        decision,
        runtime_record=continuity_record,
        decision_id="proof.restart.rerun",
        decision_utc="2026-04-09T21:20:00Z",
        evidence_refs=["runtime/state/controlplane/v2_continuity/receipts/family/proof.restart.rerun.json"],
    )
    v2_continuity.write_continuity_receipt(receipt, repo_root=tmp_path)

    runtime_records = [
        {
            "child_uid": "PIS-20260409-195205Z-F3A6",
            "job_id": compiled["job_specs"][0]["job_id"],
            "runtime_state": "running",
            "active": True,
        },
        {
            "child_uid": "PIS-20260409-195248Z-BCC7",
            "job_id": compiled["job_specs"][1]["job_id"],
            "runtime_state": "",
        },
        {
            "child_uid": "PIS-20260409-195248Z-4066",
            "job_id": compiled["job_specs"][2]["job_id"],
            "runtime_state": "",
        },
    ]
    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=runtime_records,
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection=canonical["surfaces"]["watch"],
    )
    report = projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="runtime_restart_shadow_parity",
        proof_case_utc="2026-04-09T21:21:00Z",
        proof_outcome="passed",
        evidence_refs=[
            "governance/intent_family_v2_workflow_definition_contract_v1.json",
            "runtime/state/controlplane/v2_continuity/receipts/family/proof.restart.rerun.json",
        ],
    )
    path = projection.write_intent_family_v2_proof_report(report, repo_root=tmp_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert canonical["family_status"] == "active"
    assert canonical["current_stage"] == "implement"
    assert canonical["continuity"]["continuity_state"] == "rerun_ready"
    assert canonical["continuity"]["decision_source"] == "durable_receipt"
    assert shadow_report["status"] == "match"
    assert loaded["schema_version"] == projection.PROOF_REPORT_SCHEMA_VERSION
    assert loaded["uid"] == projection.PROOF_REPORT_UID
    assert loaded["proof_outcome"] == "passed"
    assert loaded["projection_summary"]["shadow_status"] == "match"
    assert loaded["projection_summary"]["continuity_state"] == "rerun_ready"
    assert loaded["compilation_summary"]["compilation_fingerprint"] == compiled["compilation_fingerprint"]
    assert loaded["continuity_receipt"]["decision_id"] == "proof.restart.rerun"
    assert loaded["report_fingerprint"]


def test_intent_family_v2_end_to_end_proof_report_surfaces_bounded_repair_and_shadow_drift(tmp_path: Path) -> None:
    compiled = _compile_family(tmp_path)
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "failed_verification",
        "repair_needed": True,
        "repair_allowed": True,
        "revalidation_required": True,
    }
    decision = v2_continuity.decide_continuity_recovery(continuity_record)
    receipt = v2_continuity.build_continuity_receipt(
        decision,
        runtime_record=continuity_record,
        decision_id="proof.repair.rerun",
        decision_utc="2026-04-09T21:30:00Z",
    )

    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=[
            {
                "child_uid": "PIS-20260409-195205Z-F3A6",
                "job_id": compiled["job_specs"][0]["job_id"],
                "runtime_state": "failed_verification",
            },
            {
                "child_uid": "PIS-20260409-195248Z-BCC7",
                "job_id": compiled["job_specs"][1]["job_id"],
                "runtime_state": "",
            },
            {
                "child_uid": "PIS-20260409-195248Z-4066",
                "job_id": compiled["job_specs"][2]["job_id"],
                "runtime_state": "",
            },
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection={
            "family_status": "queued",
            "current_stage": "intent_validation",
            "active_job": {"child_uid": "PIS-20260409-195248Z-BCC7"},
            "next_jobs": [{"child_uid": "PIS-20260409-195248Z-4066"}],
            "blockers": [{"reason": "dependency_pending:PIS-20260409-195205Z-F3A6"}],
            "continuity": {"continuity_state": "blocked"},
        },
    )
    report = projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="bounded_repair_shadow_drift",
        proof_case_utc="2026-04-09T21:31:00Z",
        proof_outcome="repair_required",
        remaining_gaps=[
            "shadow_parity_not_earned",
            "bounded_repair_must_rerun_before_promotion",
        ],
    )

    assert canonical["family_status"] == "failed"
    assert canonical["continuity"]["continuity_state"] == "repairing"
    assert canonical["continuity"]["decision_source"] == "durable_receipt"
    assert shadow_report["status"] == "mismatch"
    assert report["proof_outcome"] == "repair_required"
    assert report["projection_summary"]["shadow_status"] == "mismatch"
    assert report["remaining_gaps"] == [
        "shadow_parity_not_earned",
        "bounded_repair_must_rerun_before_promotion",
    ]

    path = projection.write_intent_family_v2_proof_report(report, repo_root=tmp_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded["continuity_receipt"]["recovery_outcome"] == "repair_then_rerun"
    assert loaded["continuity_receipt"]["post_repair_action"] == "focused_revalidation"
    assert loaded["uid"] == projection.PROOF_REPORT_UID
    assert loaded["remaining_gaps"] == [
        "shadow_parity_not_earned",
        "bounded_repair_must_rerun_before_promotion",
    ]


def test_intent_family_v2_end_to_end_proof_report_write_is_idempotent_but_rejects_different_payload(
    tmp_path: Path,
) -> None:
    compiled = _compile_family(tmp_path)
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "interrupted",
        "interrupted": True,
        "rerun_allowed": True,
    }
    receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="proof.idempotent.write",
        decision_utc="2026-04-09T21:35:00Z",
    )
    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=[
            {
                "child_uid": "PIS-20260409-195205Z-F3A6",
                "job_id": compiled["job_specs"][0]["job_id"],
                "runtime_state": "running",
                "active": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-BCC7",
                "job_id": compiled["job_specs"][1]["job_id"],
                "runtime_state": "",
            },
            {
                "child_uid": "PIS-20260409-195248Z-4066",
                "job_id": compiled["job_specs"][2]["job_id"],
                "runtime_state": "",
            },
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection=canonical["surfaces"]["watch"],
    )
    report = projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="durable_write_guard",
        proof_case_utc="2026-04-09T21:36:00Z",
        proof_outcome="passed",
    )

    path = projection.write_intent_family_v2_proof_report(report, repo_root=tmp_path)
    same_path = projection.write_intent_family_v2_proof_report(report, repo_root=tmp_path)

    conflicting = dict(report)
    conflicting["remaining_gaps"] = ["late_shadow_gap"]
    conflicting.pop("report_fingerprint", None)

    assert same_path == path
    with pytest.raises(ValueError, match="refusing to overwrite an existing proof report with different content"):
        projection.write_intent_family_v2_proof_report(conflicting, repo_root=tmp_path)


def test_intent_family_v2_end_to_end_rejects_malformed_family_graph_before_runtime_proof(tmp_path: Path) -> None:
    _write_workflow_contract(tmp_path)
    parent_uid = _write_family(tmp_path, cyclic=True)

    with pytest.raises(compiler.IntentFamilyV2CompileError) as exc:
        compiler.compile_intent_family_v2(
            parent_uid=parent_uid,
            run_at_utc="2026-04-10T10:00:00Z",
            builder_model="gpt-5.4-mini",
            verifier_model="gpt-5.4",
            max_iterations=4,
            repo_root=tmp_path,
        )

    assert any(row["code"] == "dependency_cycle" for row in exc.value.diagnostics)


def test_intent_family_v2_end_to_end_blocks_illegal_repair_shortcut_receipt(tmp_path: Path) -> None:
    compiled = _compile_family(tmp_path)
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "failed_verification",
        "repair_needed": True,
        "repair_allowed": True,
        "revalidation_required": True,
    }
    decision = v2_continuity.decide_continuity_recovery(continuity_record)
    receipt = v2_continuity.build_continuity_receipt(
        decision,
        runtime_record=continuity_record,
        decision_id="proof.illegal.shortcut",
        decision_utc="2026-04-09T21:40:00Z",
    )
    receipt["post_repair_action"] = "complete"
    receipt["receipt_fingerprint"] = v2_continuity.continuity_receipt_fingerprint(
        {key: value for key, value in receipt.items() if key != "receipt_fingerprint"}
    )

    with pytest.raises(ValueError, match="must require rerun or focused_revalidation"):
        v2_continuity.validate_continuity_receipt(receipt)


def test_intent_family_v2_intent_validation_artifact_maps_scenarios_back_to_intent_requirements(
    tmp_path: Path,
) -> None:
    compiled = _compile_family(tmp_path)
    artifact = projection.build_intent_family_v2_intent_validation_artifact(
        compiled,
        validation_case_id="assembled_system_intent_e2e",
        validation_case_utc="2026-04-09T21:45:00Z",
        validation_outcome="proof_gap",
        summary=(
            "Representative success, fail-closed, mismatch-prone, and drift-detection scenarios ran; "
            "closeout-only promotion remains unproven in this child."
        ),
        evidence_refs=[
            "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_captures_compile_projection_restart_and_shadow_parity",
            "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_rejects_malformed_family_graph_before_runtime_proof",
            "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_blocks_illegal_repair_shortcut_receipt",
            "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_surfaces_bounded_repair_and_shadow_drift",
        ],
        requirement_coverage=[
            {
                "requirement_id": "workflow_shape",
                "intent_clause": "The target first-class workflow is intent -> parent -> children -> implement -> intent validation -> closeout.",
                "status": "proven",
                "observed_behavior": "Canonical graph and dependency ordering are compiled for the covered family.",
                "scenario_ids": ["success_compile_projection", "negative_cycle_rejected"],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_compiler.py::test_compile_intent_family_v2_emits_canonical_graph_and_job_specs",
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_rejects_malformed_family_graph_before_runtime_proof",
                ],
            },
            {
                "requirement_id": "restart_honesty",
                "intent_clause": "On restart, contradiction, or partial residue, the runtime should recover from durable checkpoint when possible, otherwise rerun or block explicitly.",
                "status": "proven",
                "observed_behavior": "Interrupted runtime projects rerun_ready from a durable continuity receipt instead of pretending execution continued.",
                "scenario_ids": ["success_compile_projection"],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_captures_compile_projection_restart_and_shadow_parity",
                ],
            },
            {
                "requirement_id": "bounded_repair",
                "intent_clause": "Repair must emit evidence and be followed by rerun or focused revalidation; it cannot directly promote closure.",
                "status": "proven",
                "observed_behavior": "Repair shortcut receipts that skip rerun or focused revalidation are rejected fail-closed.",
                "scenario_ids": ["mismatch_prone_repair_guard", "shadow_drift_detected"],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_blocks_illegal_repair_shortcut_receipt",
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_surfaces_bounded_repair_and_shadow_drift",
                ],
            },
            {
                "requirement_id": "projection_equivalence",
                "intent_clause": "All operator surfaces must use the same projection function and equivalence tests across surfaces are mandatory before cutover.",
                "status": "proven",
                "observed_behavior": "Matching watch projection stays in parity and drift is surfaced explicitly as mismatch instead of being silently absorbed.",
                "scenario_ids": ["success_compile_projection", "shadow_drift_detected"],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_captures_compile_projection_restart_and_shadow_parity",
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_surfaces_bounded_repair_and_shadow_drift",
                ],
            },
            {
                "requirement_id": "closeout_only_promotion",
                "intent_clause": "The closeout stage is the only stage allowed to promote the family parent and the source intent.",
                "status": "unproven",
                "observed_behavior": "This child validates intent-mapped runtime behavior but does not yet prove final promotion ownership.",
                "scenario_ids": ["success_compile_projection"],
                "notes": "Promotion ownership belongs to the closeout child and remains outside this checkpoint.",
            },
        ],
        scenario_results=[
            {
                "scenario_id": "success_compile_projection",
                "scenario_kind": "success_path",
                "outcome": "passed",
                "mapped_requirement_ids": ["workflow_shape", "restart_honesty", "projection_equivalence", "closeout_only_promotion"],
                "observations": [
                    "Compiler emits the canonical family graph for the covered workflow.",
                    "Interrupted family projects rerun_ready from durable continuity evidence.",
                    "Shadow parity stays match when legacy projection equals canonical watch output.",
                ],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_captures_compile_projection_restart_and_shadow_parity",
                ],
            },
            {
                "scenario_id": "negative_cycle_rejected",
                "scenario_kind": "negative_path",
                "outcome": "passed",
                "mapped_requirement_ids": ["workflow_shape"],
                "observations": [
                    "Malformed dependency cycles are rejected before runtime proof.",
                    "The covered workflow fails closed instead of accepting an impossible family graph.",
                ],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_rejects_malformed_family_graph_before_runtime_proof",
                ],
            },
            {
                "scenario_id": "mismatch_prone_repair_guard",
                "scenario_kind": "mismatch_prone",
                "outcome": "passed",
                "mapped_requirement_ids": ["bounded_repair"],
                "observations": [
                    "A repair receipt that tries to mark work complete after failure is rejected.",
                    "Revalidation remains mandatory after bounded repair.",
                ],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_blocks_illegal_repair_shortcut_receipt",
                ],
            },
            {
                "scenario_id": "shadow_drift_detected",
                "scenario_kind": "drift_detection",
                "outcome": "passed",
                "mapped_requirement_ids": ["bounded_repair", "projection_equivalence"],
                "observations": [
                    "Bounded repair produces a repairing continuity state rather than silent closure.",
                    "Shadow mismatch is surfaced explicitly when legacy projection drifts from canonical truth.",
                ],
                "evidence_refs": [
                    "tests/control_plane/test_intent_family_v2_end_to_end_proof.py::test_intent_family_v2_end_to_end_proof_report_surfaces_bounded_repair_and_shadow_drift",
                ],
            },
        ],
    )

    path = projection.write_intent_family_v2_intent_validation_artifact(artifact, repo_root=tmp_path)
    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert loaded["schema_version"] == projection.INTENT_VALIDATION_ARTIFACT_SCHEMA_VERSION
    assert loaded["uid"] == projection.INTENT_VALIDATION_UID
    assert loaded["validation_outcome"] == "proof_gap"
    assert loaded["coverage_summary"]["requirement_status_counts"]["proven"] == 4
    assert loaded["coverage_summary"]["requirement_status_counts"]["unproven"] == 1
    assert set(loaded["coverage_summary"]["covered_scenario_kinds"]) == {
        "drift_detection",
        "mismatch_prone",
        "negative_path",
        "success_path",
    }
    assert loaded["artifact_fingerprint"]


def test_intent_family_v2_intent_validation_artifact_fails_closed_without_full_scenario_coverage(
    tmp_path: Path,
) -> None:
    compiled = _compile_family(tmp_path)

    with pytest.raises(ValueError, match="must cover success_path, negative_path, mismatch_prone, and drift_detection"):
        projection.build_intent_family_v2_intent_validation_artifact(
            compiled,
            validation_case_id="missing_scenarios",
            validation_outcome="passed",
            requirement_coverage=[
                {
                    "requirement_id": "workflow_shape",
                    "intent_clause": "The target first-class workflow is intent -> parent -> children -> implement -> intent validation -> closeout.",
                    "status": "proven",
                    "scenario_ids": ["only_success"],
                }
            ],
            scenario_results=[
                {
                    "scenario_id": "only_success",
                    "scenario_kind": "success_path",
                    "outcome": "passed",
                    "mapped_requirement_ids": ["workflow_shape"],
                    "observations": ["One happy-path scenario exists."],
                }
            ],
        )


def _closeout_ready_validation_artifact(compiled: dict) -> dict:
    return projection.build_intent_family_v2_intent_validation_artifact(
        compiled,
        validation_case_id="assembled_system_intent_e2e",
        validation_case_utc="2026-04-09T22:07:00Z",
        validation_outcome="proof_gap",
        summary="Only the closeout-owned promotion step remains.",
        requirement_coverage=[
            {
                "requirement_id": "workflow_shape",
                "intent_clause": "Workflow shape is explicit.",
                "status": "proven",
                "observed_behavior": "Compiler emits the canonical family graph.",
                "scenario_ids": ["success_compile_projection"],
                "evidence_refs": ["shape"],
            },
            {
                "requirement_id": "restart_honesty",
                "intent_clause": "Recovery stays explicit.",
                "status": "proven",
                "observed_behavior": "Continuity receipt keeps rerun semantics explicit.",
                "scenario_ids": ["success_compile_projection"],
                "evidence_refs": ["continuity"],
            },
            {
                "requirement_id": "bounded_repair",
                "intent_clause": "Repair requires rerun.",
                "status": "proven",
                "observed_behavior": "Illegal repair shortcut is rejected.",
                "scenario_ids": ["mismatch_prone_repair_guard"],
                "evidence_refs": ["repair"],
            },
            {
                "requirement_id": "projection_equivalence",
                "intent_clause": "Shadow drift must surface explicitly.",
                "status": "proven",
                "observed_behavior": "Shadow parity matches canonical watch output.",
                "scenario_ids": ["success_compile_projection"],
                "evidence_refs": ["projection"],
            },
            {
                "requirement_id": "closeout_only_promotion",
                "intent_clause": "Closeout owns promotion.",
                "status": "unproven",
                "observed_behavior": "Validation intentionally defers final promotion ownership to closeout.",
                "scenario_ids": ["success_compile_projection"],
                "evidence_refs": [],
            },
        ],
        scenario_results=[
            {
                "scenario_id": "success_compile_projection",
                "scenario_kind": "success_path",
                "outcome": "passed",
                "mapped_requirement_ids": [
                    "workflow_shape",
                    "restart_honesty",
                    "projection_equivalence",
                    "closeout_only_promotion",
                ],
                "observations": ["The remaining promotion step belongs to closeout."],
                "evidence_refs": ["shape"],
            },
            {
                "scenario_id": "negative_cycle_rejected",
                "scenario_kind": "negative_path",
                "outcome": "passed",
                "mapped_requirement_ids": ["workflow_shape"],
                "observations": ["Malformed family graphs fail closed."],
                "evidence_refs": ["shape"],
            },
            {
                "scenario_id": "mismatch_prone_repair_guard",
                "scenario_kind": "mismatch_prone",
                "outcome": "passed",
                "mapped_requirement_ids": ["bounded_repair"],
                "observations": ["Repair still requires rerun or focused revalidation."],
                "evidence_refs": ["repair"],
            },
            {
                "scenario_id": "shadow_drift_detected",
                "scenario_kind": "drift_detection",
                "outcome": "passed",
                "mapped_requirement_ids": ["projection_equivalence"],
                "observations": ["Projection drift stays explicit."],
                "evidence_refs": ["projection"],
            },
        ],
    )


def test_intent_family_v2_closeout_audit_blocks_promotion_when_family_truth_is_partial(tmp_path: Path) -> None:
    compiled = _compile_family(tmp_path)
    artifact = _closeout_ready_validation_artifact(compiled)

    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=[
            {"uid": "PIS-20260409-195205Z-F3A6", "relationship": "execution_child", "status": "draft"},
            {"uid": "PIS-20260409-195248Z-BCC7", "relationship": "intent_validation_suite", "status": "draft"},
            {"uid": "PIS-20260409-195248Z-4066", "relationship": "intent_family_closeout", "status": "draft"},
        ],
        intent_validation_artifact=artifact,
        repaired_findings=["fixed_external_dependency_lookup_for_non_sibling_foundations"],
        rerun_commands=["python -m pytest tests/control_plane/test_intent_family_v2_compiler.py -q"],
        evidence_refs=[
            "tests/control_plane/test_intent_family_v2_compiler.py::test_compile_intent_family_v2_allows_verified_external_foundation_dependencies"
        ],
    )

    assert audit["decision"] == "not_closable"
    assert audit["promotion_decision"] == "leave_family_open"
    assert "child_PIS-20260409-195205Z-F3A6_status_draft" in audit["remaining_gaps"]
    assert "implementation_proof_report_missing" in audit["remaining_gaps"]
    assert "migration_law_missing" in audit["remaining_gaps"]
    assert "intent_validation_closeout_only_promotion_unproven" not in audit["remaining_gaps"]

    rendered = closeout.render_intent_family_v2_closeout_audit_markdown(audit)
    assert "Do not promote the parent family or source intent from this closeout attempt." in rendered
    assert "fixed_external_dependency_lookup_for_non_sibling_foundations" in rendered


def test_intent_family_v2_closeout_audit_promotes_only_after_full_family_evidence(tmp_path: Path) -> None:
    compiled = _compile_family(tmp_path)
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "running",
    }
    receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="proof.closeout.pass",
        decision_utc="2026-04-09T22:05:00Z",
    )
    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=[
            {
                "child_uid": "PIS-20260409-195205Z-F3A6",
                "job_id": compiled["job_specs"][0]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-BCC7",
                "job_id": compiled["job_specs"][1]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-4066",
                "job_id": compiled["job_specs"][2]["job_id"],
                "runtime_state": "running",
                "active": True,
            },
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection=canonical["surfaces"]["watch"],
    )
    proof_report = projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="closeout_parental_proof",
        proof_case_utc="2026-04-09T22:06:00Z",
        proof_outcome="passed",
    )
    admission = projection.evaluate_intent_family_v2_canary_admission(
        canonical,
        shadow_report=shadow_report,
        admission_request={
            "request_id": "closeout-ready",
            "family_parent_uids": [compiled["parent_uid"]],
            "requested_owner": "intent_family_v2",
            "legacy_owner": "legacy_v1",
            "requested_stage": "narrow_canary",
            "covered_cutover_scope": {
                "family_parent_uid": compiled["parent_uid"],
                "workflow_kind": canonical["workflow_kind"],
                "v2_owned_paths": ["infra/controlplane/intent_family_v2_compiler.py"],
                "operator_surfaces": ["watch"],
            },
            "deletion_budget": {"expected_removals": ["legacy/path.py"], "minimum_removals": 1},
            "rollback_triggers": ["shadow_mismatch"],
            "residual_legacy_ownership": ["legacy_v1"],
        },
    )

    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=[
            {"uid": "PIS-20260409-195205Z-F3A6", "relationship": "execution_child", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-BCC7", "relationship": "intent_validation_suite", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-4066", "relationship": "intent_family_closeout", "status": "draft"},
        ],
        proof_report=proof_report,
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=admission["migration_law"],
        rerun_commands=["pytest -q tests/control_plane/test_intent_family_v2_end_to_end_proof.py -k closeout"],
    )

    assert audit["decision"] == "verified"
    assert audit["status_recommendation"] == "verified"
    assert audit["promotion_decision"] == "promote_parent_and_source_intent"
    assert audit["remaining_gaps"] == []


def test_intent_family_v2_closeout_audit_blocks_mismatched_migration_scope(tmp_path: Path) -> None:
    compiled = _compile_family(tmp_path)
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "running",
    }
    receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="proof.closeout.scope-mismatch",
        decision_utc="2026-04-09T22:07:00Z",
    )
    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=[
            {
                "child_uid": "PIS-20260409-195205Z-F3A6",
                "job_id": compiled["job_specs"][0]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-BCC7",
                "job_id": compiled["job_specs"][1]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-4066",
                "job_id": compiled["job_specs"][2]["job_id"],
                "runtime_state": "running",
                "active": True,
            },
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection=canonical["surfaces"]["watch"],
    )
    proof_report = projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="closeout_scope_mismatch",
        proof_case_utc="2026-04-09T22:08:00Z",
        proof_outcome="passed",
    )
    admission = projection.evaluate_intent_family_v2_canary_admission(
        canonical,
        shadow_report=shadow_report,
        admission_request={
            "request_id": "closeout-scope-mismatch",
            "family_parent_uids": [compiled["parent_uid"]],
            "requested_owner": "intent_family_v2",
            "legacy_owner": "legacy_v1",
            "requested_stage": "narrow_canary",
            "covered_cutover_scope": {
                "family_parent_uid": "PIS-OTHER-FAMILY",
                "workflow_kind": "some_other_workflow",
                "v2_owned_paths": ["infra/controlplane/intent_family_v2_compiler.py"],
                "operator_surfaces": ["watch"],
            },
            "deletion_budget": {"expected_removals": ["legacy/path.py"], "minimum_removals": 1},
            "rollback_triggers": ["shadow_mismatch"],
            "residual_legacy_ownership": ["legacy_v1"],
        },
    )

    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=[
            {"uid": "PIS-20260409-195205Z-F3A6", "relationship": "execution_child", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-BCC7", "relationship": "intent_validation_suite", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-4066", "relationship": "intent_family_closeout", "status": "draft"},
        ],
        proof_report=proof_report,
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=admission["migration_law"],
        rerun_commands=["pytest -q tests/control_plane/test_intent_family_v2_end_to_end_proof.py -k closeout"],
    )

    assert audit["decision"] == "not_closable"
    assert audit["promotion_decision"] == "leave_family_open"
    assert "migration_law_family_scope_mismatch" in audit["remaining_gaps"]
    assert "migration_law_workflow_kind_mismatch" in audit["remaining_gaps"]


def test_intent_family_v2_closeout_audit_blocks_foreign_validation_artifact_reuse(tmp_path: Path) -> None:
    compiled = _compile_family(tmp_path)
    foreign_root = tmp_path / "foreign"
    _write_workflow_contract(foreign_root)
    foreign_parent_uid = _write_family(
        foreign_root,
        parent_uid="PIS-20260409-204437Z-410F",
        intent_uid="IS-20260409-200403Z-CFB7",
    )
    foreign = compiler.compile_intent_family_v2(
        parent_uid=foreign_parent_uid,
        run_at_utc="2026-04-10T10:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=4,
        repo_root=foreign_root,
    )
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "running",
    }
    receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="proof.closeout.foreign-validation",
        decision_utc="2026-04-09T22:09:00Z",
    )
    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=[
            {
                "child_uid": "PIS-20260409-195205Z-F3A6",
                "job_id": compiled["job_specs"][0]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-BCC7",
                "job_id": compiled["job_specs"][1]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-4066",
                "job_id": compiled["job_specs"][2]["job_id"],
                "runtime_state": "running",
                "active": True,
            },
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection=canonical["surfaces"]["watch"],
    )
    proof_report = projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="closeout_foreign_validation_proof",
        proof_case_utc="2026-04-09T22:10:00Z",
        proof_outcome="passed",
    )
    admission = projection.evaluate_intent_family_v2_canary_admission(
        canonical,
        shadow_report=shadow_report,
        admission_request={
            "request_id": "closeout-foreign-validation",
            "family_parent_uids": [compiled["parent_uid"]],
            "requested_owner": "intent_family_v2",
            "legacy_owner": "legacy_v1",
            "requested_stage": "narrow_canary",
            "covered_cutover_scope": {
                "family_parent_uid": compiled["parent_uid"],
                "workflow_kind": canonical["workflow_kind"],
                "v2_owned_paths": ["infra/controlplane/intent_family_v2_compiler.py"],
                "operator_surfaces": ["watch"],
            },
            "deletion_budget": {"expected_removals": ["legacy/path.py"], "minimum_removals": 1},
            "rollback_triggers": ["shadow_mismatch"],
            "residual_legacy_ownership": ["legacy_v1"],
        },
    )

    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=[
            {"uid": "PIS-20260409-195205Z-F3A6", "relationship": "execution_child", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-BCC7", "relationship": "intent_validation_suite", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-4066", "relationship": "intent_family_closeout", "status": "draft"},
        ],
        proof_report=proof_report,
        intent_validation_artifact=_closeout_ready_validation_artifact(foreign),
        migration_law=admission["migration_law"],
    )

    assert audit["decision"] == "not_closable"
    assert audit["promotion_decision"] == "leave_family_open"
    assert "intent_validation_family_parent_mismatch" in audit["remaining_gaps"]
    assert "intent_validation_source_intent_mismatch" in audit["remaining_gaps"]
    assert "intent_validation_compilation_mismatch" in audit["remaining_gaps"]


def test_intent_family_v2_closeout_audit_blocks_stale_proof_replay(tmp_path: Path) -> None:
    compiled = _compile_family(tmp_path)
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "running",
    }
    receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="proof.closeout.stale-proof",
        decision_utc="2026-04-09T22:11:00Z",
    )
    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=[
            {
                "child_uid": "PIS-20260409-195205Z-F3A6",
                "job_id": compiled["job_specs"][0]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-BCC7",
                "job_id": compiled["job_specs"][1]["job_id"],
                "runtime_state": "completed",
                "retired": True,
            },
            {
                "child_uid": "PIS-20260409-195248Z-4066",
                "job_id": compiled["job_specs"][2]["job_id"],
                "runtime_state": "running",
                "active": True,
            },
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(
        canonical,
        legacy_projection=canonical["surfaces"]["watch"],
    )
    proof_report = projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="closeout_stale_proof_replay",
        proof_case_utc="2026-04-09T22:12:00Z",
        proof_outcome="passed",
    )
    proof_report["compilation_summary"]["compilation_fingerprint"] = "stale_compilation_fingerprint"
    admission = projection.evaluate_intent_family_v2_canary_admission(
        canonical,
        shadow_report=shadow_report,
        admission_request={
            "request_id": "closeout-stale-proof",
            "family_parent_uids": [compiled["parent_uid"]],
            "requested_owner": "intent_family_v2",
            "legacy_owner": "legacy_v1",
            "requested_stage": "narrow_canary",
            "covered_cutover_scope": {
                "family_parent_uid": compiled["parent_uid"],
                "workflow_kind": canonical["workflow_kind"],
                "v2_owned_paths": ["infra/controlplane/intent_family_v2_compiler.py"],
                "operator_surfaces": ["watch"],
            },
            "deletion_budget": {"expected_removals": ["legacy/path.py"], "minimum_removals": 1},
            "rollback_triggers": ["shadow_mismatch"],
            "residual_legacy_ownership": ["legacy_v1"],
        },
    )

    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=[
            {"uid": "PIS-20260409-195205Z-F3A6", "relationship": "execution_child", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-BCC7", "relationship": "intent_validation_suite", "status": "verified"},
            {"uid": "PIS-20260409-195248Z-4066", "relationship": "intent_family_closeout", "status": "draft"},
        ],
        proof_report=proof_report,
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=admission["migration_law"],
    )

    assert audit["decision"] == "not_closable"
    assert audit["promotion_decision"] == "leave_family_open"
    assert "implementation_proof_compilation_mismatch" in audit["remaining_gaps"]
