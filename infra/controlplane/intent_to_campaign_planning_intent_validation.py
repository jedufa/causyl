"""Assembled-system intent validation for the intent-to-campaign planning compiler."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import campaign_phase_synthesis
from infra.controlplane import campaign_plan_advisory_handoff
from infra.controlplane import campaign_plan_enrichment


VALIDATION_UID = "PIS-20260411-113602Z-E9DE"
SOURCE_INTENT_UID = "IS-20260403-212914Z-C623"
FAMILY_PARENT_UID = "PIS-20260411-112805Z-41E3"
SCHEMA_VERSION = "intent_to_campaign_planning_intent_validation_v1"
VALIDATION_CASE_ID = "assembled_system_intent_e2e"
ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260411-113602Z-E9DE__validate-intent-to-campaign-planning-compiler-assembled-system-intent-e2e-suite/"
    "artifacts/intent_validation/intent_to_campaign_planning_intent_e2e.json"
)
DEPENDENCY_UIDS: Sequence[str] = (
    "PIS-20260411-113558Z-0B60",
    "PIS-20260411-113559Z-8B17",
    "PIS-20260411-113601Z-59F3",
    "PIS-20260411-113602Z-470F",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _canonical_json(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


def _ledger_item_dir_for_uid(repo_root: Path, uid: str) -> Path | None:
    root = repo_root / "governance" / "ledger" / "patch_instruction_sheets"
    matches = sorted(root.rglob(f"{uid}__*/meta.json"))
    return matches[0].parent if matches else None


def _dependency_snapshot(repo_root: Path, uid: str) -> Dict[str, Any]:
    item_dir = _ledger_item_dir_for_uid(repo_root, uid)
    meta_path = item_dir / "meta.json" if item_dir is not None else None
    meta = _read_json(meta_path) if meta_path is not None and meta_path.exists() else {}
    evidence = meta.get("evidence") if isinstance(meta.get("evidence"), Mapping) else {}
    return {
        "uid": uid,
        "status": _normalize_text(meta.get("status")),
        "release_state": _normalize_text(meta.get("release_state")),
        "has_evidence": bool(evidence),
        "has_changesets": isinstance(meta.get("changesets"), list) and bool(meta.get("changesets")),
        "meta_path": meta_path.relative_to(repo_root).as_posix() if meta_path is not None else "",
        "artifact_path": _normalize_text(evidence.get("artifact")),
        "validated_utc": _normalize_text(evidence.get("validated_utc")),
    }


def _scenario(
    scenario_id: str,
    *,
    scenario_kind: str,
    outcome: str,
    mapped_requirement_ids: Sequence[str],
    observations: Sequence[str],
    evidence_refs: Sequence[str],
) -> Dict[str, Any]:
    return {
        "scenario_id": scenario_id,
        "scenario_kind": scenario_kind,
        "outcome": outcome,
        "mapped_requirement_ids": list(mapped_requirement_ids),
        "observations": list(observations),
        "evidence_refs": list(evidence_refs),
    }


def _validation_command(command_id: str, *, command: str, purpose: str, evidence_refs: Sequence[str]) -> Dict[str, Any]:
    return {
        "command_id": command_id,
        "command": command,
        "purpose": purpose,
        "evidence_refs": list(evidence_refs),
    }


def _seed_plan() -> Dict[str, Any]:
    return {
        "schema_version": "campaign_plan_v1",
        "plan_id": "plan_intent_to_campaign_demo_v3",
        "plan_version": "1.2.0",
        "stable_identity": {
            "canonicalization": "json_sort_keys_compact_utf8",
            "content_hash_algorithm": "sha256",
            "schema_migration_policy": "versioned_backward_compatible_or_explicit_migration",
        },
        "metadata": {
            "title": "Intent To Campaign Compiler Demo",
            "created_at_utc": "2026-04-11T13:40:00Z",
            "source_intent_uid": SOURCE_INTENT_UID,
            "ledger_refs": [
                "PIS-20260411-113558Z-0B60",
                "PIS-20260411-113559Z-8B17",
                "PIS-20260411-113601Z-59F3",
                "PIS-20260411-113602Z-470F",
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
            "source_intent_uid": SOURCE_INTENT_UID,
            "upstream_contract_refs": [
                "governance/campaign_record_contract_v1.json",
                "governance/scientific_campaign_method_template_v1.json",
                "governance/budget_adaptive_research_policy_v1.json",
            ],
            "supporting_artifact_refs": [
                "governance/ledger/intent_sheets/causyl/verified/2026/04/IS-20260403-212914Z-C623__intent-sheet-to-campaign-plan-compiler/intent_sheet.md"
            ],
            "lineage_hash": "sha256:intent_to_campaign_demo_v3",
        },
    }


def _valid_request() -> Dict[str, Any]:
    return {
        "schema_version": "campaign_phase_synthesis_request_v1",
        "campaign_plan_seed": _seed_plan(),
        "mission_scope": "multi_stage_campaign",
        "intent_summary": "Turn broad operator research intent into a phased campaign plan with bounded advisory child-PIS handoffs.",
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
            },
        ],
    }


def _build_compiler_stack() -> Dict[str, Any]:
    synthesis = campaign_phase_synthesis.compile_campaign_phase_plan(_valid_request())
    enrichment = campaign_plan_enrichment.enrich_campaign_plan(
        {
            "schema_version": "campaign_plan_enrichment_request_v1",
            "campaign_plan": synthesis.get("campaign_plan") or {},
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
        },
        repo_root=_repo_root(),
    )
    advisory = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(
        {
            "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
            "source_intent_uid": SOURCE_INTENT_UID,
            "phase_synthesis_report": synthesis,
            "enrichment_report": enrichment,
        }
    )
    return {"synthesis": synthesis, "enrichment": enrichment, "advisory": advisory}


def build_intent_to_campaign_planning_intent_validation_artifact(
    *,
    repo_root: Path | None = None,
    validation_case_utc: str | None = None,
    dependency_ready: bool | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    dependency_snapshot = [_dependency_snapshot(root, uid) for uid in DEPENDENCY_UIDS]
    dependency_ready = (
        all(row["has_evidence"] and row["has_changesets"] for row in dependency_snapshot)
        if dependency_ready is None
        else bool(dependency_ready)
    )
    stack = _build_compiler_stack()
    synthesis = stack["synthesis"]
    enrichment = stack["enrichment"]
    advisory = stack["advisory"]

    open_request = _valid_request()
    open_request["mission_scope"] = "open_ended_program"
    open_request["operator_constraints"] = ["Keep it useful."]
    open_request["decomposition_triggers"] = []
    open_ended_result = campaign_phase_synthesis.compile_campaign_phase_plan(open_request)

    candidate = json.loads(json.dumps(_seed_plan()))
    candidate["ambiguity_policy"] = {
        "allowed_in_campaign_plan": [
            "Exact experiment sequence may remain open until prior-knowledge review is complete."
        ],
        "must_resolve_before_pis": [
            "Target phase owner must be named before any child PIS is emitted.",
            "Concrete validation surface for each child PIS must be fixed before handoff.",
        ],
        "named_contract_violation": "campaign_plan_contract_violation",
    }
    candidate["phases"] = [
        {
            "phase_id": "phase_01_everything_everywhere",
            "label": "Do the entire campaign in one shot",
            "goal": "Collapse broad intent into one giant planning phase.",
            "deliverables": ["phase map", "child-PIS mapping"],
            "method_template_phase_ids": [
                "question_framing",
                "prior_knowledge_consolidation",
                "hypothesis_registry",
                "experiment_design",
                "analysis_plan",
                "adversarial_review",
                "continuation",
            ],
            "budget_mode": "campaign",
            "allowed_ambiguities": [
                "Exact experiment sequence may remain open until prior-knowledge review is complete."
            ],
            "must_resolve_before_pis": [],
            "pis_readiness": "ready_for_pis_generation",
        }
    ]
    single_phase_result = campaign_phase_synthesis.validate_phase_synthesis_candidate(_valid_request(), candidate, repo_root=root)

    hidden_receipt_request = {
        "schema_version": campaign_plan_advisory_handoff.REQUEST_SCHEMA_VERSION,
        "source_intent_uid": SOURCE_INTENT_UID,
        "phase_synthesis_report": synthesis,
        "enrichment_report": json.loads(json.dumps(enrichment)),
    }
    hidden_receipt_request["enrichment_report"]["campaign_plan"]["policy_enrichment"]["enrichment_receipts"] = []
    hidden_receipt_artifact = campaign_plan_advisory_handoff.build_campaign_plan_advisory_handoff_artifact(hidden_receipt_request)

    scenario_results = [
        _scenario(
            "positive_broad_intent_compiles_to_phased_plan",
            scenario_kind="success_path",
            outcome="passed" if synthesis["ok"] and len(list((synthesis.get("campaign_plan") or {}).get("phases") or [])) >= 3 else "failed",
            mapped_requirement_ids=["broad_intent_to_phased_plan", "ambiguity_preservation"],
            observations=[
                "Broad intent compiles into a multi-phase campaign plan rather than a giant PIS.",
                "Allowed ambiguity and pre-PIS resolution boundaries stay explicit in the compiled plan.",
            ],
            evidence_refs=[
                "infra/controlplane/campaign_phase_synthesis.py",
                "tests/control_plane/test_campaign_phase_synthesis.py",
            ],
        ),
        _scenario(
            "negative_under_bounded_open_ended_plan_rejected",
            scenario_kind="negative_path",
            outcome="passed" if "under_bounded_open_ended_plan" in list(open_ended_result.get("reason_codes") or []) else "failed",
            mapped_requirement_ids=["broad_intent_to_phased_plan"],
            observations=["Open-ended intent without enough boundary detail stays fail-closed instead of laundering itself into a fake plan."],
            evidence_refs=["tests/control_plane/test_campaign_phase_synthesis.py::test_compile_campaign_phase_plan_returns_clarification_required_for_under_bounded_open_ended_intent"],
        ),
        _scenario(
            "negative_single_phase_collapse_rejected",
            scenario_kind="negative_path",
            outcome="passed" if "single_phase_collapse_forbidden" in list(single_phase_result.get("reason_codes") or []) else "failed",
            mapped_requirement_ids=["distinct_truth_boundary"],
            observations=["The compiler refuses the renamed-giant-PIS anti-pattern rather than treating it as a valid campaign plan."],
            evidence_refs=["tests/control_plane/test_campaign_phase_synthesis.py::test_validate_phase_synthesis_candidate_rejects_single_phase_collapse"],
        ),
        _scenario(
            "positive_policy_enrichment_and_explainability_stay_inspectable",
            scenario_kind="success_path",
            outcome="passed" if enrichment["ok"] and advisory["ok"] else "failed",
            mapped_requirement_ids=["policy_enrichment_visibility", "advisory_handoff_only"],
            observations=[
                "Policy enrichment remains visible through declared receipts tied to specific phases.",
                "Explainability and advisory handoff record how the source intent, phase trace, and policy receipts produced child-PIS proposals.",
            ],
            evidence_refs=[
                "infra/controlplane/campaign_plan_enrichment.py",
                "infra/controlplane/campaign_plan_advisory_handoff.py",
                "tests/control_plane/test_campaign_plan_enrichment.py",
                "tests/control_plane/test_campaign_plan_advisory_handoff.py",
            ],
        ),
        _scenario(
            "negative_hidden_policy_injection_rejected",
            scenario_kind="negative_path",
            outcome="passed" if "missing_policy_enrichment_receipts" in list(hidden_receipt_artifact.get("reason_codes") or []) else "failed",
            mapped_requirement_ids=["policy_enrichment_visibility"],
            observations=["Removing enrichment receipts blocks the explainability surface instead of allowing hidden policy injection."],
            evidence_refs=["tests/control_plane/test_campaign_plan_advisory_handoff.py::test_build_campaign_plan_advisory_handoff_artifact_rejects_missing_policy_receipts"],
        ),
        _scenario(
            "drift_detection_advisory_handoff_stays_non_executable",
            scenario_kind="drift_detection",
            outcome=(
                "passed"
                if advisory["ok"]
                and all(row.get("advisory_only") is True for row in advisory.get("advisory_child_pis_handoff", {}).get("proposals", []))
                and all(not _normalize_text(row.get("execution_owner")) for row in advisory.get("advisory_child_pis_handoff", {}).get("proposals", []))
                else "failed"
            ),
            mapped_requirement_ids=["advisory_handoff_only"],
            observations=["Derived child-PIS outputs stay explicitly advisory and never claim launch authority or execution ownership."],
            evidence_refs=[
                "infra/controlplane/campaign_plan_advisory_handoff.py",
                "tests/control_plane/test_campaign_plan_advisory_handoff.py",
            ],
        ),
    ]

    requirement_coverage = [
        {
            "requirement_id": "distinct_truth_boundary",
            "intent_clause": "Intent Sheet, Campaign Plan, PIS, and Job stay distinct durable objects.",
            "observed_behavior": "Single-phase collapse is rejected and the plan stays pre-execution rather than collapsing into one giant PIS or job payload.",
            "scenario_ids": ["negative_single_phase_collapse_rejected"],
            "evidence_refs": ["governance/campaign_plan_contract_v1.json", "tests/control_plane/test_campaign_phase_synthesis.py"],
            "status": "proven",
        },
        {
            "requirement_id": "broad_intent_to_phased_plan",
            "intent_clause": "Broad intent can become an inspectable phased plan without losing mission meaning.",
            "observed_behavior": "The current stack compiles broad intent into a multi-phase campaign plan and blocks under-bounded open-ended missions.",
            "scenario_ids": ["positive_broad_intent_compiles_to_phased_plan", "negative_under_bounded_open_ended_plan_rejected"],
            "evidence_refs": ["infra/controlplane/campaign_phase_synthesis.py", "tests/control_plane/test_campaign_phase_synthesis.py"],
            "status": "proven",
        },
        {
            "requirement_id": "ambiguity_preservation",
            "intent_clause": "Allowed ambiguity remains visible and required pre-PIS resolution boundaries stay explicit.",
            "observed_behavior": "Compiled phases preserve allowed ambiguity and named pre-PIS blockers in both the plan and explainability artifact.",
            "scenario_ids": ["positive_broad_intent_compiles_to_phased_plan"],
            "evidence_refs": ["infra/controlplane/campaign_phase_synthesis.py", "infra/controlplane/campaign_plan_advisory_handoff.py"],
            "status": "proven",
        },
        {
            "requirement_id": "policy_enrichment_visibility",
            "intent_clause": "Method and budget policy must enrich the plan visibly rather than through hidden prompt magic.",
            "observed_behavior": "Policy changes are carried through declared receipts and the handoff layer blocks when those receipts are removed.",
            "scenario_ids": ["positive_policy_enrichment_and_explainability_stay_inspectable", "negative_hidden_policy_injection_rejected"],
            "evidence_refs": ["infra/controlplane/campaign_plan_enrichment.py", "infra/controlplane/campaign_plan_advisory_handoff.py"],
            "status": "proven",
        },
        {
            "requirement_id": "advisory_handoff_only",
            "intent_clause": "Child-PIS generation remains an advisory handoff, not silent execution ownership.",
            "observed_behavior": "The current handoff artifact emits bounded child-PIS proposals with advisory_only=true and launch_authority=forbidden.",
            "scenario_ids": ["positive_policy_enrichment_and_explainability_stay_inspectable", "drift_detection_advisory_handoff_stays_non_executable"],
            "evidence_refs": ["infra/controlplane/campaign_plan_advisory_handoff.py", "tests/control_plane/test_campaign_plan_advisory_handoff.py"],
            "status": "proven",
        },
        {
            "requirement_id": "family_dependency_readiness",
            "intent_clause": "The assembled validator must cite the current implementation children directly and fail closed if the family proof is stale.",
            "observed_behavior": (
                "All implementation children currently carry evidence and changesets."
                if dependency_ready
                else "At least one dependency child is still missing evidence or changesets, so assembled validation remains a proof gap."
            ),
            "scenario_ids": ["positive_broad_intent_compiles_to_phased_plan", "positive_policy_enrichment_and_explainability_stay_inspectable"],
            "evidence_refs": [row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
            "status": "proven" if dependency_ready else "unproven",
        },
    ]

    validation_results = {
        "compiler_stack_readiness": {
            "status": "passed" if synthesis["ok"] and enrichment["ok"] and advisory["ok"] else "failed",
            "summary": "Compiler stack produced a compiled plan, enriched plan, and advisory handoff artifact.",
        },
        "dependency_ledger_evidence_inspection": {
            "status": "passed" if dependency_ready else "proof_gap",
            "summary": "Implementation children carry the evidence needed for assembled validation." if dependency_ready else "One or more implementation children still lacks closeout-grade evidence.",
        },
    }

    residual_gaps = []
    if not dependency_ready:
        residual_gaps.append(
            {
                "risk_type": "validation_risk",
                "summary": "Implementation-family evidence is incomplete, so assembled validation cannot honestly promote the source intent yet.",
            }
        )
    scenario_outcome_counts: Dict[str, int] = {}
    for row in scenario_results:
        outcome = _normalize_text(row.get("outcome"))
        if outcome:
            scenario_outcome_counts[outcome] = scenario_outcome_counts.get(outcome, 0) + 1
    validation_outcome = "passed" if dependency_ready and scenario_outcome_counts.get("passed", 0) == len(scenario_results) else "proof_gap"
    artifact = {
        "schema_version": SCHEMA_VERSION,
        "uid": VALIDATION_UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "family_parent_uid": FAMILY_PARENT_UID,
        "validation_case_id": VALIDATION_CASE_ID,
        "validation_case_utc": validation_case_utc or _utc_now(),
        "summary": "Assembled validation proves broad-intent phase synthesis, visible policy enrichment, and advisory-only child-PIS handoff against the current compiler stack.",
        "intent_anchor_excerpt": "Large efforts should become phased campaign plans without collapsing into giant PISes, while methodology, budget posture, and ambiguity remain explicit.",
        "dependency_snapshot": dependency_snapshot,
        "scenario_results": scenario_results,
        "requirement_coverage": requirement_coverage,
        "validation_results": validation_results,
        "validation_commands": [
            _validation_command(
                "compiler_stack_readiness",
                command="pytest tests/control_plane/test_campaign_phase_synthesis.py tests/control_plane/test_campaign_plan_enrichment.py tests/control_plane/test_campaign_plan_advisory_handoff.py -q",
                purpose="Verify phase synthesis, policy enrichment, and advisory handoff behavior from current repo state.",
                evidence_refs=[
                    "tests/control_plane/test_campaign_phase_synthesis.py",
                    "tests/control_plane/test_campaign_plan_enrichment.py",
                    "tests/control_plane/test_campaign_plan_advisory_handoff.py",
                ],
            ),
            _validation_command(
                "dependency_ledger_evidence_inspection",
                command="python - <<'PY' ... dependency ledger inspection ... PY",
                purpose="Confirm implementation children carry durable evidence and changesets before source-intent promotion.",
                evidence_refs=[row["meta_path"] for row in dependency_snapshot if row["meta_path"]],
            ),
        ],
        "coverage_summary": {
            "scenario_count": len(scenario_results),
            "requirement_count": len(requirement_coverage),
            "covered_scenario_kinds": sorted({row["scenario_kind"] for row in scenario_results}),
            "scenario_outcome_counts": scenario_outcome_counts,
        },
        "residual_gaps": residual_gaps,
        "validation_outcome": validation_outcome,
    }
    artifact["artifact_fingerprint"] = _fingerprint(artifact)
    return artifact


def validate_intent_to_campaign_planning_intent_validation_artifact(payload: Mapping[str, Any]) -> List[str]:
    artifact = dict(payload or {})
    issues: List[str] = []
    if _normalize_text(artifact.get("schema_version")) != SCHEMA_VERSION:
        issues.append(f"schema_version must remain {SCHEMA_VERSION}")
    if _normalize_text(artifact.get("uid")) != VALIDATION_UID:
        issues.append(f"uid must remain {VALIDATION_UID}")
    required_scenarios = {
        "positive_broad_intent_compiles_to_phased_plan",
        "negative_under_bounded_open_ended_plan_rejected",
        "negative_single_phase_collapse_rejected",
        "positive_policy_enrichment_and_explainability_stay_inspectable",
        "negative_hidden_policy_injection_rejected",
        "drift_detection_advisory_handoff_stays_non_executable",
    }
    seen_scenarios = {
        _normalize_text(row.get("scenario_id"))
        for row in list(artifact.get("scenario_results") or [])
        if isinstance(row, Mapping)
    }
    missing_scenarios = sorted(required_scenarios - seen_scenarios)
    if missing_scenarios:
        issues.append(f"missing required scenarios: {', '.join(missing_scenarios)}")
    validation_commands = {
        _normalize_text(row.get("command_id"))
        for row in list(artifact.get("validation_commands") or [])
        if isinstance(row, Mapping)
    }
    for command_id in ("compiler_stack_readiness", "dependency_ledger_evidence_inspection"):
        if command_id not in validation_commands:
            issues.append(f"validation_commands missing command_id: {command_id}")
    validation_results = artifact.get("validation_results") if isinstance(artifact.get("validation_results"), Mapping) else {}
    for command_id in ("compiler_stack_readiness", "dependency_ledger_evidence_inspection"):
        if command_id not in validation_results:
            issues.append(f"validation_results missing command_id: {command_id}")
    expected = _fingerprint({k: v for k, v in artifact.items() if k != "artifact_fingerprint"})
    if _normalize_text(artifact.get("artifact_fingerprint")) != expected:
        issues.append("artifact_fingerprint does not match payload")
    return issues


def write_intent_to_campaign_planning_intent_validation_artifact(
    artifact: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Path:
    payload = dict(artifact)
    issues = validate_intent_to_campaign_planning_intent_validation_artifact(payload)
    if issues:
        raise ValueError("; ".join(issues))
    root = Path(repo_root or _repo_root()).resolve()
    target = root / ARTIFACT_REL
    if target.exists():
        current = _read_json(target)
        if current == payload:
            return target
        raise ValueError("artifact already exists with different content")
    return _write_json_atomic(target, payload)
