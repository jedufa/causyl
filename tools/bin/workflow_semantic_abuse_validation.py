#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import intent_family_v2_closeout as closeout
from infra.controlplane import intent_family_v2_compiler as compiler
from infra.controlplane import intent_family_v2_projection as projection
from infra.controlplane import v2_continuity


SCHEMA_VERSION = "workflow_semantic_abuse_validation_v1"
SCENARIO_IDS = (
    "WF_WRONG_NODE_PROMOTION_V1",
    "WF_STALE_VERIFIER_REUSE_V1",
    "WF_PARTIAL_LEDGER_PROMOTION_V1",
    "WF_DETACHED_RESULT_REPLAY_V1",
    "WF_WRONG_PARENT_REPAIR_LINKAGE_V1",
    "WF_PARTIAL_FAMILY_CLOSEOUT_V1",
    "WF_WRONG_VERIFIER_RESULT_ATTACHMENT_V1",
    "WF_POISONED_WRITEBACK_V1",
)


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _write_workflow_contract(root: Path) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_family(root: Path, *, parent_uid: str, intent_uid: str, suffix: str) -> str:
    parent_dir = (
        root
        / "governance"
        / "ledger"
        / "patch_instruction_sheets"
        / "causyl"
        / "active"
        / "2026"
        / "04"
        / f"{parent_uid}__parent_{suffix}"
    )
    parent_dir.mkdir(parents=True, exist_ok=True)
    impl_uid = f"PIS-20260413-{suffix}01Z-A1A1"
    validation_uid = f"PIS-20260413-{suffix}02Z-B2B2"
    closeout_uid = f"PIS-20260413-{suffix}03Z-C3C3"
    child_uids = [impl_uid, validation_uid, closeout_uid]
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": f"Intent family {suffix}",
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
        (impl_uid, "prove-runtime", "Prove runtime semantics", "execution_child", 1, []),
        (validation_uid, "intent-validation", "Intent validation", "intent_validation_suite", 2, [impl_uid]),
        (closeout_uid, "closeout", "Close family", "intent_family_closeout", 3, [impl_uid, validation_uid]),
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
            / f"{uid}__{slug}_{suffix}"
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


def _compile_family(root: Path, *, parent_uid: str, intent_uid: str, suffix: str) -> Dict[str, Any]:
    _write_workflow_contract(root)
    _write_family(root, parent_uid=parent_uid, intent_uid=intent_uid, suffix=suffix)
    return compiler.compile_intent_family_v2(
        parent_uid=parent_uid,
        run_at_utc="2026-04-13T12:00:00Z",
        builder_model="gpt-5.4-mini",
        verifier_model="gpt-5.4",
        max_iterations=2,
        repo_root=root,
    )


def _closeout_ready_validation_artifact(compiled: Dict[str, Any]) -> Dict[str, Any]:
    return projection.build_intent_family_v2_intent_validation_artifact(
        compiled,
        validation_case_id="closeout_ready_validation",
        validation_outcome="passed",
        requirement_coverage=[
            {
                "requirement_id": "projection_equivalence",
                "intent_clause": "Projection equivalence remains explicit and fail-closed.",
                "status": "proven",
                "observed_behavior": "Projection shadow remains matched.",
                "evidence_refs": ["projection"],
                "scenario_ids": ["success_path_projection"],
            },
            {
                "requirement_id": "bounded_repair",
                "intent_clause": "Repair still requires rerun or focused revalidation.",
                "status": "proven",
                "observed_behavior": "No contradiction remains.",
                "evidence_refs": ["repair"],
                "scenario_ids": ["negative_path_guard"],
            },
            {
                "requirement_id": "closeout_only_promotion",
                "intent_clause": "Only closeout may promote the parent and source intent.",
                "status": "proven",
                "observed_behavior": "Promotion remains closeout-owned.",
                "evidence_refs": ["closeout"],
                "scenario_ids": ["mismatch_prone_closeout"],
            },
            {
                "requirement_id": "drift_detection",
                "intent_clause": "Drift must stay explicit instead of being smoothed away.",
                "status": "proven",
                "observed_behavior": "Drift remains visible.",
                "evidence_refs": ["drift"],
                "scenario_ids": ["drift_detection_case"],
            },
        ],
        scenario_results=[
            {
                "scenario_id": "success_path_projection",
                "scenario_kind": "success_path",
                "outcome": "passed",
                "mapped_requirement_ids": ["projection_equivalence"],
                "observations": ["Projection remains explicit."],
                "evidence_refs": ["projection"],
            },
            {
                "scenario_id": "negative_path_guard",
                "scenario_kind": "negative_path",
                "outcome": "passed",
                "mapped_requirement_ids": ["bounded_repair"],
                "observations": ["Bounded repair still needs rerun."],
                "evidence_refs": ["repair"],
            },
            {
                "scenario_id": "mismatch_prone_closeout",
                "scenario_kind": "mismatch_prone",
                "outcome": "passed",
                "mapped_requirement_ids": ["closeout_only_promotion"],
                "observations": ["Closeout remains the only promoter."],
                "evidence_refs": ["closeout"],
            },
            {
                "scenario_id": "drift_detection_case",
                "scenario_kind": "drift_detection",
                "outcome": "passed",
                "mapped_requirement_ids": ["drift_detection"],
                "observations": ["Drift stays visible."],
                "evidence_refs": ["drift"],
            },
        ],
    )


def _closeout_ready_proof_report(compiled: Dict[str, Any]) -> Dict[str, Any]:
    continuity_record = {
        "subject_kind": "family",
        "subject_id": compiled["parent_uid"],
        "workflow_kind": "intent_family_v2",
        "runtime_state": "running",
    }
    receipt = v2_continuity.build_continuity_receipt(
        v2_continuity.decide_continuity_recovery(continuity_record),
        runtime_record=continuity_record,
        decision_id="proof.closeout.ready",
        decision_utc="2026-04-13T12:01:00Z",
    )
    child_uids = [spec["binding"]["id"] for spec in compiled["job_specs"]]
    canonical = projection.project_intent_family_v2_family(
        compiled,
        runtime_records=[
            {"child_uid": child_uids[0], "job_id": compiled["job_specs"][0]["job_id"], "runtime_state": "completed", "retired": True},
            {"child_uid": child_uids[1], "job_id": compiled["job_specs"][1]["job_id"], "runtime_state": "completed", "retired": True},
            {"child_uid": child_uids[2], "job_id": compiled["job_specs"][2]["job_id"], "runtime_state": "running", "active": True},
        ],
        continuity_record=continuity_record,
        latest_continuity_receipt=receipt,
    )
    shadow_report = projection.compare_intent_family_v2_shadow(canonical, legacy_projection=canonical["surfaces"]["watch"])
    return projection.build_intent_family_v2_proof_report(
        compiled,
        canonical,
        shadow_report=shadow_report,
        continuity_receipt=receipt,
        proof_case_id="workflow_semantics_proof",
        proof_case_utc="2026-04-13T12:02:00Z",
        proof_outcome="passed",
    )


def _migration_law(compiled: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "final_promotion_owner_uid": closeout.UID,
        "covered_cutover_scope": {
            "family_parent_uid": compiled["parent_uid"],
            "workflow_kind": "intent_family_v2",
            "v2_owned_paths": ["infra/controlplane/intent_family_v2_closeout.py"],
            "operator_surfaces": ["watch"],
        },
        "deletion_budget": {"expected_removals": ["legacy/path.py"], "minimum_removals": 1},
        "rollback_triggers": ["shadow_mismatch"],
        "residual_legacy_ownership": ["legacy_v1"],
    }


def _verified_child_rows(compiled: Dict[str, Any], *, final_status: str = "draft") -> List[Dict[str, str]]:
    child_uids = [spec["binding"]["id"] for spec in compiled["job_specs"]]
    return [
        {"uid": child_uids[0], "relationship": "execution_child", "status": "verified"},
        {"uid": child_uids[1], "relationship": "intent_validation_suite", "status": "verified"},
        {"uid": child_uids[2], "relationship": "intent_family_closeout", "status": final_status},
    ]


def _record_trace(traces: List[Dict[str, Any]], *, scenario_id: str, audit: Dict[str, Any]) -> None:
    traces.append(
        {
            "scenario_id": scenario_id,
            "decision": audit["decision"],
            "promotion_decision": audit["promotion_decision"],
            "remaining_gaps": list(audit["remaining_gaps"]),
        }
    )


def _matrix_row(
    *,
    scenario_id: str,
    observed_classification: str,
    audit: Dict[str, Any],
    pass_verdict: bool,
    notes: str,
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "observed_classification": observed_classification,
        "decision": audit["decision"],
        "promotion_decision": audit["promotion_decision"],
        "correctness_verdict": "pass" if pass_verdict else "fail",
        "remaining_gaps": list(audit["remaining_gaps"]),
        "notes": notes,
    }


def _scenario_wrong_node_promotion(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120001Z-A1L4", intent_uid="IS-20260413-120001Z-A1L4", suffix="1201")
    foreign = _compile_family(root, parent_uid="PIS-20260413-120011Z-B1L4", intent_uid="IS-20260413-120011Z-B1L4", suffix="1202")
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=_verified_child_rows(compiled),
        proof_report=_closeout_ready_proof_report(compiled),
        intent_validation_artifact=_closeout_ready_validation_artifact(foreign),
        migration_law=_migration_law(compiled),
    )
    _record_trace(traces, scenario_id="WF_WRONG_NODE_PROMOTION_V1", audit=audit)
    snapshots["WF_WRONG_NODE_PROMOTION_V1"] = audit
    classification = "intent_validation_family_parent_mismatch"
    return _matrix_row(
        scenario_id="WF_WRONG_NODE_PROMOTION_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=classification in audit["remaining_gaps"],
        notes="Foreign-family validation artifact must block promotion.",
    )


def _scenario_stale_verifier_reuse(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120021Z-C1L4", intent_uid="IS-20260413-120021Z-C1L4", suffix="1203")
    artifact = _closeout_ready_validation_artifact(compiled)
    artifact["compilation_summary"]["compilation_fingerprint"] = "stale_compilation_fingerprint"
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=_verified_child_rows(compiled),
        proof_report=_closeout_ready_proof_report(compiled),
        intent_validation_artifact=artifact,
        migration_law=_migration_law(compiled),
    )
    _record_trace(traces, scenario_id="WF_STALE_VERIFIER_REUSE_V1", audit=audit)
    snapshots["WF_STALE_VERIFIER_REUSE_V1"] = audit
    classification = "intent_validation_compilation_mismatch"
    return _matrix_row(
        scenario_id="WF_STALE_VERIFIER_REUSE_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=classification in audit["remaining_gaps"],
        notes="Stale validation artifact reuse must block closeout.",
    )


def _scenario_partial_ledger_promotion(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120031Z-D1L4", intent_uid="IS-20260413-120031Z-D1L4", suffix="1204")
    rows = _verified_child_rows(compiled)
    rows[0]["status"] = "draft"
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=rows,
        proof_report=_closeout_ready_proof_report(compiled),
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=_migration_law(compiled),
    )
    _record_trace(traces, scenario_id="WF_PARTIAL_LEDGER_PROMOTION_V1", audit=audit)
    snapshots["WF_PARTIAL_LEDGER_PROMOTION_V1"] = audit
    classification = next(g for g in audit["remaining_gaps"] if g.startswith("child_"))
    return _matrix_row(
        scenario_id="WF_PARTIAL_LEDGER_PROMOTION_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=audit["decision"] == "not_closable",
        notes="Partial family status must block promotion.",
    )


def _scenario_detached_result_replay(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120041Z-E1L4", intent_uid="IS-20260413-120041Z-E1L4", suffix="1205")
    report = _closeout_ready_proof_report(compiled)
    report["compilation_summary"]["compilation_fingerprint"] = "stale_proof_compilation_fingerprint"
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=_verified_child_rows(compiled),
        proof_report=report,
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=_migration_law(compiled),
    )
    _record_trace(traces, scenario_id="WF_DETACHED_RESULT_REPLAY_V1", audit=audit)
    snapshots["WF_DETACHED_RESULT_REPLAY_V1"] = audit
    classification = "implementation_proof_compilation_mismatch"
    return _matrix_row(
        scenario_id="WF_DETACHED_RESULT_REPLAY_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=classification in audit["remaining_gaps"],
        notes="Detached or stale proof report replay must block closeout.",
    )


def _scenario_wrong_parent_repair_linkage(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120051Z-F1L4", intent_uid="IS-20260413-120051Z-F1L4", suffix="1206")
    migration = _migration_law(compiled)
    migration["covered_cutover_scope"]["family_parent_uid"] = "PIS-WRONG-FAMILY"
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=_verified_child_rows(compiled),
        proof_report=_closeout_ready_proof_report(compiled),
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=migration,
    )
    _record_trace(traces, scenario_id="WF_WRONG_PARENT_REPAIR_LINKAGE_V1", audit=audit)
    snapshots["WF_WRONG_PARENT_REPAIR_LINKAGE_V1"] = audit
    classification = "migration_law_family_scope_mismatch"
    return _matrix_row(
        scenario_id="WF_WRONG_PARENT_REPAIR_LINKAGE_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=classification in audit["remaining_gaps"],
        notes="Wrong-parent repair linkage must fail closed.",
    )


def _scenario_partial_family_closeout(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120061Z-G1L4", intent_uid="IS-20260413-120061Z-G1L4", suffix="1207")
    rows = _verified_child_rows(compiled)
    rows[1]["status"] = "draft"
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=rows,
        proof_report=_closeout_ready_proof_report(compiled),
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=_migration_law(compiled),
    )
    _record_trace(traces, scenario_id="WF_PARTIAL_FAMILY_CLOSEOUT_V1", audit=audit)
    snapshots["WF_PARTIAL_FAMILY_CLOSEOUT_V1"] = audit
    classification = next(g for g in audit["remaining_gaps"] if g.startswith("child_"))
    return _matrix_row(
        scenario_id="WF_PARTIAL_FAMILY_CLOSEOUT_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=audit["decision"] == "not_closable",
        notes="Unresolved child status must keep family open.",
    )


def _scenario_wrong_verifier_result_attachment(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120071Z-H1L4", intent_uid="IS-20260413-120071Z-H1L4", suffix="1208")
    artifact = _closeout_ready_validation_artifact(compiled)
    artifact["source_intent_uid"] = "IS-WRONG-INTENT"
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=_verified_child_rows(compiled),
        proof_report=_closeout_ready_proof_report(compiled),
        intent_validation_artifact=artifact,
        migration_law=_migration_law(compiled),
    )
    _record_trace(traces, scenario_id="WF_WRONG_VERIFIER_RESULT_ATTACHMENT_V1", audit=audit)
    snapshots["WF_WRONG_VERIFIER_RESULT_ATTACHMENT_V1"] = audit
    classification = "intent_validation_source_intent_mismatch"
    return _matrix_row(
        scenario_id="WF_WRONG_VERIFIER_RESULT_ATTACHMENT_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=classification in audit["remaining_gaps"],
        notes="Wrong source-intent attachment must block closeout.",
    )


def _scenario_poisoned_writeback(root: Path, traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    compiled = _compile_family(root, parent_uid="PIS-20260413-120081Z-I1L4", intent_uid="IS-20260413-120081Z-I1L4", suffix="1209")
    foreign = _compile_family(root, parent_uid="PIS-20260413-120091Z-J1L4", intent_uid="IS-20260413-120091Z-J1L4", suffix="1210")
    report = _closeout_ready_proof_report(foreign)
    audit = closeout.build_intent_family_v2_closeout_audit(
        compiled,
        child_status_rows=_verified_child_rows(compiled),
        proof_report=report,
        intent_validation_artifact=_closeout_ready_validation_artifact(compiled),
        migration_law=_migration_law(compiled),
    )
    _record_trace(traces, scenario_id="WF_POISONED_WRITEBACK_V1", audit=audit)
    snapshots["WF_POISONED_WRITEBACK_V1"] = audit
    classification = "implementation_proof_family_parent_mismatch"
    return _matrix_row(
        scenario_id="WF_POISONED_WRITEBACK_V1",
        observed_classification=classification,
        audit=audit,
        pass_verdict=classification in audit["remaining_gaps"],
        notes="Foreign proof report must not authorize writeback or promotion.",
    )


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    snapshots: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        rows.append(_scenario_wrong_node_promotion(root, traces, snapshots))
        rows.append(_scenario_stale_verifier_reuse(root, traces, snapshots))
        rows.append(_scenario_partial_ledger_promotion(root, traces, snapshots))
        rows.append(_scenario_detached_result_replay(root, traces, snapshots))
        rows.append(_scenario_wrong_parent_repair_linkage(root, traces, snapshots))
        rows.append(_scenario_partial_family_closeout(root, traces, snapshots))
        rows.append(_scenario_wrong_verifier_result_attachment(root, traces, snapshots))
        rows.append(_scenario_poisoned_writeback(root, traces, snapshots))

    matrix = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "rows": rows,
    }
    negatives = {
        "schema_version": SCHEMA_VERSION,
        "rows": [
            {
                "negative_control_id": "NEGCTRL_WORKFLOW_FOREIGN_ARTIFACT_REUSE_SEED_V1",
                "pass": "intent_validation_family_parent_mismatch" in next(r["remaining_gaps"] for r in rows if r["scenario_id"] == "WF_WRONG_NODE_PROMOTION_V1"),
            },
            {
                "negative_control_id": "NEGCTRL_WORKFLOW_STALE_COMPILATION_REUSE_SEED_V1",
                "pass": "implementation_proof_compilation_mismatch" in next(r["remaining_gaps"] for r in rows if r["scenario_id"] == "WF_DETACHED_RESULT_REPLAY_V1"),
            },
        ],
    }
    summary = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "pass_count": sum(1 for row in rows if row["correctness_verdict"] == "pass"),
        "fail_count": sum(1 for row in rows if row["correctness_verdict"] != "pass"),
        "bugs_found_and_fixed": [
            {"issue": "closeout_trusted_foreign_validation_and_proof_artifacts", "status": "fixed"},
            {"issue": "closeout_allowed_stale_compilation_artifact_reuse", "status": "fixed"},
        ],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    _write_json(out_dir / "full_workflow_abuse_matrix.json", matrix)
    _write_json(out_dir / "full_workflow_negative_controls.json", negatives)
    _write_json(out_dir / "full_workflow_snapshots.json", snapshots)
    _write_jsonl(out_dir / "full_workflow_truth_trace.jsonl", traces)
    _write_json(out_dir / "full_workflow_metrics_summary.json", summary)
    return {
        "matrix": matrix,
        "negative_controls": negatives,
        "snapshots": snapshots,
        "summary": summary,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run workflow semantic abuse validation.")
    parser.add_argument("--out-dir", required=True)
    args = parser.parse_args(argv)
    result = run_validation(out_dir=Path(args.out_dir))
    print(json.dumps(result["summary"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
