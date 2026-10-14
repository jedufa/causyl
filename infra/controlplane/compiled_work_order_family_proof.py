"""Focused family proof helpers for compiled work-order adoption across stable handoffs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Mapping

from infra.controlplane import campaign_phase_synthesis
from infra.controlplane import campaign_plan_advisory_handoff
from infra.controlplane import campaign_plan_enrichment
from infra.controlplane import recursive_dry_run_planner
from infra.controlplane import recursive_planning_nonlive_admission
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched.fanout import materialize_fanout_for_intent
from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger


SCHEMA_VERSION = "compiled_work_order_family_proof_v1"
PROOF_CHILD_UID = "PIS-20260411-144241Z-7992"
PARENT_UID = "PIS-20260411-142246Z-429E"
CHILD_UIDS = (
    "PIS-20260411-144241Z-154B",
    "PIS-20260411-144241Z-2871",
    "PIS-20260411-144241Z-EEAC",
)
ANTI_PATTERN_BANS = (
    "prose_only_handoff_claimed_as_compiled_authoritative",
    "malformed_envelope_treated_as_authoritative",
    "unstable_boundary_forced_onto_compiled_rollout",
    "fallback_path_hidden_by_prompt_prose",
)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _write_workflow_contract(root: Path) -> None:
    payload = json.loads(Path("governance/intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_prompt_template(root: Path) -> None:
    prompt_path = root / "docs" / "prompts" / "lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("## 2. Implement One Child PIS\n\n```text\nImplement `<CHILD_UID>`.\n```\n", encoding="utf-8")


def _consumer_spec(*, compiled_artifact_rel: str = "", status: str = "compiled_emitted", reason_codes: list[str] | None = None) -> Dict[str, Any]:
    prompt_meta = {
        "compiled_work_order": {
            "status": status,
            "compiled_artifact_rel": compiled_artifact_rel,
        }
    }
    if reason_codes:
        prompt_meta["compiled_work_order"]["non_compiled_reason_codes"] = list(reason_codes)
    return {
        "job_id": "compiled_proof_consumer_001",
        "enabled": True,
        "binding": {"type": "ledger_uid", "id": PROOF_CHILD_UID},
        "job_kind": "build_verify_loop",
        "loop": {"max_iterations": 1},
        "prompt": "Use the compiled work order when it is authoritative and fail closed otherwise.",
        "schedule": {"mode": "once", "run_at": "2026-04-11T16:00:00Z", "timezone": "UTC"},
        "expected_output": {"kind": "report", "format": "markdown"},
        "policy": {"repo_change_mode": "strict", "require_commit_journal": True},
        "runtime": {"executor_agent_tech": "codex"},
        "display_title": "Compiled work order family proof consumer",
        "required_context_bundle": "lab_core_v1",
        "context_refs": [],
        "additional_context_refs": [],
        "forbidden_context_refs": [],
        "prompt_meta": prompt_meta,
    }


def _fanout_spec() -> Dict[str, Any]:
    return {
        "source_intent": {"uid": "IS-20260411-160000Z-FN01", "title": "Compiled Fanout Proof"},
        "parent": {"uid": "PIS-20260411-160001Z-PA01", "title": "Compiled Fanout Parent"},
        "parallel_children": [
            {"uid": "PIS-20260411-160002Z-AA01", "title": "Explore fanout branch A"},
            {"uid": "PIS-20260411-160003Z-BB02", "title": "Explore fanout branch B"},
        ],
        "integration_child": {"uid": "PIS-20260411-160004Z-IN03", "title": "Integrate fanout evidence"},
        "validation_child": {"uid": "PIS-20260411-160005Z-VA04", "title": "Validate fanout evidence"},
        "closeout_child": {"uid": "PIS-20260411-160006Z-CL05", "title": "Close fanout proof"},
    }


def _seed_fanout_intent(root: Path) -> None:
    intents = IntentSheetLedger(repo_root=root)
    intents.create_sheet(
        uid_override="IS-20260411-160000Z-FN01",
        title="Compiled Fanout Proof",
        intent_markdown="# Compiled Fanout Proof\n\nUse fanout as implementation methodology.\n",
        project_id="causyl",
    )


def _seed_campaign_plan() -> Dict[str, Any]:
    return {
        "schema_version": "campaign_plan_v1",
        "plan_id": "plan_compiled_campaign_proof_v1",
        "plan_version": "1.3.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": "Compiled Campaign Proof",
            "created_at_utc": "2026-04-11T16:05:00Z",
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "ledger_refs": [
                "PIS-20260411-113558Z-0B60",
                "PIS-20260411-113559Z-8B17",
                "PIS-20260411-113601Z-59F3",
            ],
        },
        "vocabulary_boundary": {
            "intent_sheet": ["operator goal framing", "mission non-goals", "allowed broad ambiguity"],
            "campaign_plan": ["phased plan structure", "review gate placement", "named ambiguity policy"],
            "pis": ["bounded implementation slice", "proof target", "narrow acceptance surface"],
            "job": ["router payload", "runtime receipt", "execution lifecycle"],
        },
        "ambiguity_policy": {
            "allowed_in_campaign_plan": [],
            "must_resolve_before_pis": [],
            "named_contract_violation": "campaign_plan_contract_violation",
        },
        "provenance": {
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "upstream_contract_refs": [
                "governance/campaign_record_contract_v1.json",
                "governance/scientific_campaign_method_template_v1.json",
                "governance/budget_adaptive_research_policy_v1.json",
            ],
            "supporting_artifact_refs": [
                "governance/ledger/intent_sheets/causyl/verified/2026/04/IS-20260403-212914Z-C623__intent-sheet-to-campaign-plan-compiler/intent_sheet.md"
            ],
            "lineage_hash": "sha256:intent_to_campaign_demo_v4",
        },
    }


def _campaign_compiler_stack() -> tuple[Dict[str, Any], Dict[str, Any]]:
    synthesis = campaign_phase_synthesis.compile_campaign_phase_plan(
        {
            "schema_version": "campaign_phase_synthesis_request_v1",
            "campaign_plan_seed": _seed_campaign_plan(),
            "mission_scope": "multi_stage_campaign",
            "intent_summary": "Compile the campaign advisory handoff and prove it survives builder consumption.",
            "operator_constraints": [
                "Keep the planning layer advisory and non-executable.",
                "Expose unresolved ambiguity instead of inventing certainty.",
                "Preserve explicit review gates before downstream PIS generation.",
            ],
            "required_outputs": [
                "phase map",
                "ambiguity register",
                "review-gate note",
                "child-PIS readiness note",
            ],
            "decomposition_triggers": [
                "broad intent spans more than one independent proof boundary",
                "later child slices need different acceptance targets",
            ],
            "review_gate_topics": ["scope approval", "decomposition approval"],
            "requested_budget_mode": "campaign",
            "ambiguities": [
                {
                    "topic": "Exact experiment sequence may remain open until prior-knowledge review is complete.",
                    "resolution_timing": "allowed_in_campaign_plan",
                },
                {
                    "topic": "Target phase owner must be named before any child PIS is emitted.",
                    "resolution_timing": "must_resolve_before_pis",
                },
                {
                    "topic": "Concrete validation surface for each child PIS must be fixed before handoff.",
                    "resolution_timing": "must_resolve_before_pis",
                }
            ],
        }
    )
    enrichment = campaign_plan_enrichment.enrich_campaign_plan(
        {
            "schema_version": "campaign_plan_enrichment_request_v1",
            "campaign_plan": synthesis["campaign_plan"],
            "method_template_ref": "governance/scientific_campaign_method_template_reference_v1.json",
            "budget_policy_ref": "governance/budget_adaptive_research_policy_v1.json",
            "selected_budget_mode": "campaign",
            "declared_constraints": [
                {
                    "constraint_id": "keep_continuation_visible",
                    "constraint_kind": "require_method_phase",
                    "phase_id": "phase_03_prepare_child_pis_handoff",
                    "method_phase_id": "continuation",
                    "advisory_only": False,
                    "rationale": "Continuation posture must remain visible before child-PIS handoff.",
                }
            ],
        }
    )
    return synthesis, enrichment


def _budget_balance(*, job_uid: str, remaining_minor_units: int) -> dict:
    allocated_minor_units = remaining_minor_units + 100
    return {
        "job_uid": job_uid,
        "unit": "usd_cents",
        "allocated_minor_units": allocated_minor_units,
        "consumed_minor_units": 100,
        "remaining_minor_units": remaining_minor_units,
    }


def _truth_refs(label: str) -> dict:
    return {
        "ledger_uids": [f"PIS-{label}"],
        "campaign_ids": [f"camp_{label}"],
        "job_ids": [f"job_{label}"],
        "run_ids": [],
        "receipt_refs": [f"runtime/state/scheduled_jobs/receipts/{label}.json"],
        "continuation_packet_ids": [f"pkt_{label}"],
        "artifact_refs": [f"docs/contracts/{label}.md"],
    }


def _recursive_bad_request() -> Dict[str, Any]:
    return {
        "schema_version": "recursive_dry_run_plan_request_v1",
        "plan_id": "plan_recursive_unstable_001",
        "root_plan_id": "plan_recursive_unstable_001",
        "parent_plan_id": "",
        "parent_job_id": "job_recursive_unstable_001",
        "planning_mode": "dry_run_only",
        "root_binding": {"binding_type": "ledger_uid", "binding_id": "PIS-20260409-153903Z-7C65"},
        "plan_truth_refs": _truth_refs("recursive_unstable_root"),
        "plan_event_refs": ["governance/ledger/patch_instruction_sheets/causyl/active/2026/04/PIS-20260409-154916Z-0853/events.jsonl"],
        "root_node": {
            "node_id": "node_recursive_root",
            "job_shape": "campaign_job",
            "truth_refs": _truth_refs("recursive_unstable_root"),
            "node_event_refs": ["events.jsonl#node_recursive_root"],
            "dependency_node_ids": [],
            "budget_balance": _budget_balance(job_uid="job_recursive_unstable_001", remaining_minor_units=1000),
            "campaign_phase_category": "planning",
            "descendants": [
                {
                    "node_id": "node_recursive_child",
                    "job_shape": "verification_loop_job",
                    "truth_refs": _truth_refs("recursive_unstable_child"),
                    "node_event_refs": ["events.jsonl#node_recursive_child"],
                    "dependency_node_ids": ["node_missing"],
                    "requested_budget_minor_units": 250,
                    "parent_reserved_budget_minor_units": 100,
                    "minimum_parent_remaining_minor_units": 100,
                    "descendants": [],
                }
            ],
        },
    }


def _recursive_entrypoint() -> Dict[str, Any]:
    return {
        "entrypoint_kind": "campaign_phase",
        "parent_job_shape": "campaign_job",
        "parent_job_id": "job_recursive_unstable_001",
        "binding_type": "ledger_uid",
        "binding_id": "PIS-20260409-153903Z-7C65",
        "campaign_id": "camp_recursive_unstable_root",
        "phase_id": "phase_recursive_plan",
        "campaign_phase_category": "planning",
        "campaign_truth_refs": ["campaigns/camp_recursive_unstable_root.json"],
    }


def _case_summary(
    *,
    case_id: str,
    producer: Mapping[str, Any],
    consumer_ir: Mapping[str, Any],
) -> Dict[str, Any]:
    normalized_ir = consumer_ir.get("normalized_ir") if isinstance(consumer_ir.get("normalized_ir"), Mapping) else {}
    metadata = normalized_ir.get("metadata") if isinstance(normalized_ir.get("metadata"), Mapping) else {}
    validation_plan = list(normalized_ir.get("validation_plan") or [])
    return {
        "case_id": case_id,
        "producer_status": _normalize_text(producer.get("status")),
        "consumer_mode": _normalize_text(metadata.get("compiled_work_order_mode")),
        "consumer_status": _normalize_text(metadata.get("compiled_work_order_status")),
        "consumer_fallback_reason": _normalize_text(metadata.get("compiled_work_order_fallback_reason")),
        "consumer_scope_objective": _normalize_text(metadata.get("compiled_work_order_scope_objective")),
        "consumer_validation_checks": json.loads(str(metadata.get("compiled_work_order_validation_checks") or "[]")),
        "validation_plan": validation_plan,
    }


def collect_compiled_work_order_family_proof(*, repo_root: Path) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    _write_workflow_contract(repo_root)
    _write_prompt_template(repo_root)
    _seed_fanout_intent(repo_root)

    fanout = materialize_fanout_for_intent(
        "IS-20260411-160000Z-FN01",
        _fanout_spec(),
        repo_root=repo_root,
    )
    fanout_compiled_path = str((repo_root / fanout["compiled_work_order"]["compiled_artifact_rel"]).resolve())
    fanout_ir = sched.compiled_execution_ir_from_job_spec(_consumer_spec(compiled_artifact_rel=fanout_compiled_path))

    synthesis, enrichment = _campaign_compiler_stack()
    campaign_artifact = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(
        {
            "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
            "source_intent_uid": "IS-20260403-212914Z-C623",
            "phase_synthesis_report": synthesis,
            "enrichment_report": enrichment,
        }
    )
    campaign_artifact_path = campaign_plan_advisory_handoff.write_campaign_plan_advisory_handoff_artifact(
        campaign_artifact,
        repo_root=repo_root,
    )
    campaign_payload = json.loads(campaign_artifact_path.read_text(encoding="utf-8"))
    campaign_compiled_path = str((repo_root / campaign_payload["compiled_work_order"]["compiled_artifact_rel"]).resolve())
    campaign_ir = sched.compiled_execution_ir_from_job_spec(_consumer_spec(compiled_artifact_rel=campaign_compiled_path))

    malformed_envelope_path = repo_root / "runtime" / "state" / "controlplane" / "compiled_work_order_family_proof" / "malformed_envelope.json"
    malformed_envelope_path.parent.mkdir(parents=True, exist_ok=True)
    malformed_envelope_path.write_text('{"schema_version":"compiled_work_order_envelope_v1"}\n', encoding="utf-8")
    malformed_ir = sched.compiled_execution_ir_from_job_spec(
        _consumer_spec(compiled_artifact_rel=str(malformed_envelope_path.resolve()))
    )

    recursive_plan = recursive_dry_run_planner.build_recursive_dry_run_plan(_recursive_bad_request())
    unstable_boundary = recursive_planning_nonlive_admission.materialize_recursive_planning_nonlive_admission(
        entrypoint=_recursive_entrypoint(),
        plan_result=recursive_plan,
        request_live_launch=True,
    )
    unstable_ir = sched.compiled_execution_ir_from_job_spec(
        _consumer_spec(
            status=str(((unstable_boundary.get("compiled_work_order") or {}).get("status")) or "fallback_legacy"),
            reason_codes=list(((unstable_boundary.get("compiled_work_order") or {}).get("non_compiled_reason_codes")) or []),
        )
    )

    cases = [
        _case_summary(
            case_id="fanout_supported_authoritative_handoff",
            producer=fanout["compiled_work_order"],
            consumer_ir=fanout_ir,
        ),
        _case_summary(
            case_id="campaign_supported_authoritative_handoff",
            producer=campaign_payload["compiled_work_order"],
            consumer_ir=campaign_ir,
        ),
        _case_summary(
            case_id="malformed_envelope_fails_closed_to_fallback",
            producer={
                "status": "compiled_invalid_artifact",
            },
            consumer_ir=malformed_ir,
        ),
        _case_summary(
            case_id="unstable_recursive_boundary_stays_explicit_fallback",
            producer=unstable_boundary["compiled_work_order"],
            consumer_ir=unstable_ir,
        ),
    ]

    all_cases_passed = (
        cases[0]["producer_status"] == "compiled_emitted"
        and cases[0]["consumer_mode"] == "authoritative"
        and cases[0]["consumer_scope_objective"] == "Compile the stable scheduler-v2 fanout child-brief handoff for orchestrator-owned child PIS emission."
        and "fanout_authoring_compile" in cases[0]["consumer_validation_checks"]
        and cases[1]["producer_status"] == "compiled_emitted"
        and cases[1]["consumer_mode"] == "authoritative"
        and cases[1]["consumer_scope_objective"] == "Compile the stable campaign phase brief handoff for advisory child-PIS preparation without granting execution authority."
        and "campaign_advisory_handoff" in cases[1]["consumer_validation_checks"]
        and cases[2]["consumer_mode"] == "compatibility_fallback"
        and cases[2]["consumer_fallback_reason"] == "compiled_work_order_validation_failed"
        and cases[3]["producer_status"] == "fallback_legacy"
        and cases[3]["consumer_mode"] == "compatibility_fallback"
        and cases[3]["consumer_fallback_reason"] == "handoff_marked_non_compiled"
    )

    return {
        "schema_version": SCHEMA_VERSION,
        "proof_child_uid": PROOF_CHILD_UID,
        "parent_uid": PARENT_UID,
        "child_uids": list(CHILD_UIDS),
        "case_count": len(cases),
        "all_cases_passed": all_cases_passed,
        "specialized_verifier_review_required": True,
        "anti_pattern_bans": list(ANTI_PATTERN_BANS),
        "cases": cases,
    }
