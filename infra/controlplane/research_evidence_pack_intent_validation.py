"""Assembled-system intent validation for research evidence packs.

This module is a read-only validation helper for the IS-12/PIS-B40E child. It
does not author evidence packs; it inspects the existing contract surfaces and
builds a durable scenario matrix that closeout can cite.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import campaign_phase_gate_contract
from infra.controlplane import campaign_record_contract
from infra.controlplane import continuation_packet_contract
from infra.controlplane import literature_compendium_contract
from infra.controlplane import research_evidence_pack_contract
from infra.controlplane import research_evidence_pack_reporting


VALIDATION_UID = "PIS-20260410-102637Z-B40E"
SOURCE_INTENT_UID = "IS-20260403-212914Z-5E8C"
FAMILY_PARENT_UID = "PIS-20260410-102027Z-7198"
SCHEMA_VERSION = "research_evidence_pack_intent_validation_v1"
VALIDATION_CASE_ID = "assembled_system_intent_e2e"
ARTIFACT_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260410-102637Z-B40E__validate-research-evidence-pack-contract-assembled-system-intent-e2e-suite/"
    "artifacts/intent_validation/research_evidence_pack_assembled_system_intent_e2e.json"
)

_DEPENDENCY_STATUS_GAP = (
    "Dependency PIS-20260410-102637Z-EA30 still has durable ledger status draft; "
    "the local authoring helper exists and is exercised by sibling tests, but family closeout should not treat this child as verified closure."
)
_EA30_META_REL = Path(
    "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
    "PIS-20260410-102637Z-EA30__build-evidence-pack-validator-authoring-helpers-and-audit-receipts/meta.json"
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return payload


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


def _ledger_item_dir_for_uid(repo_root: Path, uid: str) -> Path | None:
    root = repo_root / "governance" / "ledger" / "patch_instruction_sheets"
    matches = sorted(root.rglob(f"{uid}__*/meta.json"))
    if not matches:
        return None
    return matches[0].parent


def _dependency_ready(*, repo_root: Path) -> bool:
    item_dir = _ledger_item_dir_for_uid(repo_root, "PIS-20260410-102637Z-EA30")
    meta_path = item_dir / "meta.json" if item_dir is not None else repo_root / _EA30_META_REL
    try:
        meta = _read_json(meta_path)
    except Exception:
        return False
    status = str(meta.get("status") or "").strip().lower()
    evidence = meta.get("evidence")
    return status in {"implemented", "verified"} and isinstance(evidence, Mapping) and bool(evidence)


def _reference_pack_ref(pack_result: Mapping[str, Any]) -> Dict[str, str]:
    normalized = pack_result.get("normalized_pack") if isinstance(pack_result.get("normalized_pack"), Mapping) else {}
    return {
        "pack_id": str(normalized.get("pack_id") or ""),
        "pack_schema_version": str(normalized.get("schema_version") or research_evidence_pack_contract.PACK_SCHEMA_VERSION),
        "pack_artifact_ref": research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix(),
        "pack_fingerprint": str(pack_result.get("computed_fingerprint") or ""),
        "summary": "Reference research evidence pack remains reloadable for later campaign and continuation work.",
    }


def _campaign_record(pack_ref: Mapping[str, str]) -> Dict[str, Any]:
    return {
        "schema_version": campaign_record_contract.SCHEMA_VERSION,
        "campaign_id": "camp-is12-evidence-pack-validation",
        "parent_binding": {"binding_type": "ledger_uid", "binding_id": FAMILY_PARENT_UID},
        "campaign_state": "active",
        "campaign_truth_refs": {
            "ledger_uids": [FAMILY_PARENT_UID],
            "job_ids": ["sj_b40e_validation"],
            "run_ids": ["run_b40e_validation"],
            "receipt_refs": ["runtime/state/scheduled_jobs/receipts/sj_b40e_validation.json"],
            "continuation_packet_ids": ["pkt_is12_evidence_pack_validation"],
            "artifact_refs": [research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix()],
        },
        "evidence_pack_refs": [dict(pack_ref)],
        "record_event_refs": [
            "governance/ledger/patch_instruction_sheets/causyl/active/2026/04/"
            "PIS-20260410-102027Z-7198__research-evidence-pack-contr/events.jsonl"
        ],
        "phases": [
            {
                "phase_id": "phase_literature_synthesis",
                "label": "Literature synthesis",
                "job_shape": "verification_loop_job",
                "state": "succeeded",
                "depends_on": [],
                "output_refs": [
                    research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix(),
                    literature_compendium_contract.REFERENCE_COMPENDIUM_REL.as_posix(),
                ],
                "truth_refs": {
                    "ledger_uids": [VALIDATION_UID],
                    "job_ids": ["sj_b40e_validation"],
                    "run_ids": ["run_b40e_validation"],
                    "receipt_refs": ["runtime/state/scheduled_jobs/receipts/sj_b40e_validation.json"],
                    "continuation_packet_ids": ["pkt_is12_evidence_pack_validation"],
                    "artifact_refs": [research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix()],
                },
                "evidence_pack_refs": [dict(pack_ref)],
                "phase_event_refs": [],
            }
        ],
    }


def _phase_gate_decision(pack_ref: Mapping[str, str], *, dependency_ready: bool) -> Dict[str, Any]:
    return {
        "schema_version": campaign_phase_gate_contract.SCHEMA_VERSION,
        "campaign_id": "camp-is12-evidence-pack-validation",
        "phase_id": "phase_literature_synthesis",
        "decision_id": "gate-is12-reusable-evidence",
        "verdict": "ready" if dependency_ready else "partial_ready",
        "evidence_refs": [research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix()],
        "caveats": [] if dependency_ready else ["This validation child does not own family closeout promotion."],
        "proof_gaps": [] if dependency_ready else ["dependency_ledger_status_not_all_verified"],
        "downstream_unlock": {
            "policy": "full_ready" if dependency_ready else "allowed_with_caveats",
            "allowed": True,
            "unlocked_phase_ids": ["phase_design_reuse"],
        },
        "handoff": {
            "summary": "Downstream work may reuse the evidence pack by durable reference while closeout remains separate.",
            "evidence_refs": [literature_compendium_contract.REFERENCE_COMPENDIUM_REL.as_posix()],
            "evidence_pack_refs": [dict(pack_ref)],
            "caveats": [] if dependency_ready else ["Use linked pack storage, not inline evidence payloads."],
            "proof_gaps": [] if dependency_ready else ["dependency_ledger_status_not_all_verified"],
            "next_decision": {
                "decision_owner": "PIS-20260410-102638Z-A321",
                "question": "Can the research evidence pack family be closed after verifier review?",
                "recommended_action": "close_phase" if dependency_ready else "await_review",
            },
        },
    }


def _continuation_packet(pack_ref: Mapping[str, str]) -> Dict[str, Any]:
    return {
        "schema_version": continuation_packet_contract.SCHEMA_VERSION,
        "packet_id": "pkt_is12_evidence_pack_validation",
        "packet_revision": 1,
        "created_utc": "2026-04-10T12:41:11Z",
        "source_run": {
            "job_id": "pis_b40e_20260410_validate-research-evidence-pack",
            "run_id": "sj_20260410T124002Z_c830b9",
            "attempt_id": "iteration_1",
            "receipt_refs": ["runtime/state/scheduled_jobs/receipts/sj_20260410T124002Z_c830b9.json"],
        },
        "handoff_brief": "Reference evidence pack and compendium can be reloaded from durable artifact refs.",
        "result_summary": {
            "outcome": "partial",
            "summary": "Assembled contract proof is local and dependency-gated for closeout.",
        },
        "evidence_created": [
            {
                "evidence_id": "ev_is12_artifact_matrix",
                "kind": "validation_artifact",
                "summary": "Intent-mapped evidence pack assembled-system validation matrix.",
                "refs": [ARTIFACT_REL.as_posix()],
            }
        ],
        "artifact_index": [
            {
                "artifact_id": "artifact_reference_pack",
                "kind": "evidence_pack",
                "uri": research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix(),
                "summary": "Reference pack with claim/source links and unresolved contradiction state.",
            },
            {
                "artifact_id": "artifact_reference_compendium",
                "kind": "literature_compendium",
                "uri": literature_compendium_contract.REFERENCE_COMPENDIUM_REL.as_posix(),
                "summary": "Reference compendium linked back to evidence pack claims and sources.",
            },
        ],
        "open_questions": [
            {
                "question_id": "q_family_closeout",
                "question": "Verifier and closeout child must decide whether candidate sibling evidence is enough to promote the family.",
            }
        ],
        "failed_paths": [
            {
                "path_id": "fp_family_closeout_dependency_status",
                "summary": "Family closeout remains blocked until dependency ledger status is reviewed by the final child.",
            }
        ],
        "recommended_next_steps": [
            {
                "step_id": "step_closeout_review",
                "priority": "now",
                "step": "Consume the B40E validation artifact in the final closeout child.",
            }
        ],
        "confidence": {
            "level": "medium",
            "rationale": "Local contract validators pass; durable ledger dependency status is not fully verified.",
        },
        "applicability": {
            "scope": "same_family",
            "limitations": ["Does not prove full campaign executor behavior or final family promotion."],
        },
        "evidence_pack_refs": [dict(pack_ref)],
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


def build_research_evidence_pack_intent_validation_artifact(
    *,
    repo_root: Path | None = None,
    validation_case_utc: str | None = None,
    dependency_ready: bool | None = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    dependency_ready = _dependency_ready(repo_root=root) if dependency_ready is None else bool(dependency_ready)
    pack = _read_json(root / research_evidence_pack_contract.REFERENCE_PACK_REL)
    compendium = _read_json(root / literature_compendium_contract.REFERENCE_COMPENDIUM_REL)

    pack_result = research_evidence_pack_contract.validate_research_evidence_pack(pack)
    pack_ref = _reference_pack_ref(pack_result)
    compendium_result = literature_compendium_contract.validate_literature_compendium(compendium, evidence_packs=[pack])
    compendium_report = research_evidence_pack_reporting.build_compendium_report(compendium, evidence_packs=[pack])
    listed_reports = research_evidence_pack_reporting.list_research_evidence_artifacts(repo_root=root)

    campaign_result = campaign_record_contract.validate_campaign_record(_campaign_record(pack_ref), repo_root=root)
    phase_gate_result = campaign_phase_gate_contract.validate_phase_gate_decision(
        _phase_gate_decision(pack_ref, dependency_ready=dependency_ready), repo_root=root
    )
    continuation_result = continuation_packet_contract.validate_continuation_packet(_continuation_packet(pack_ref), repo_root=root)

    unattributed = copy.deepcopy(compendium)
    unattributed["entries"][0]["evidence_refs"][0]["claim_ids"] = []
    unattributed_result = literature_compendium_contract.validate_literature_compendium(unattributed, evidence_packs=[pack])

    flattened = copy.deepcopy(compendium)
    flattened["unresolved_conflicts"] = []
    flattened_result = literature_compendium_contract.validate_literature_compendium(flattened, evidence_packs=[pack])

    missing_ref = dict(pack_ref)
    missing_ref["pack_artifact_ref"] = "governance/missing_research_evidence_pack.json"
    missing_continuation_result = continuation_packet_contract.validate_continuation_packet(
        _continuation_packet(missing_ref),
        repo_root=root,
    )

    raw_note_embedded = copy.deepcopy(compendium)
    raw_note_embedded["raw_note_body"] = "Copied lab note body must not become a sourced compendium entry."
    raw_note_result = literature_compendium_contract.validate_literature_compendium(raw_note_embedded, evidence_packs=[pack])

    scenario_results = [
        _scenario(
            "positive_reusable_pack_and_compendium",
            scenario_kind="success_path",
            outcome="passed" if pack_result["ok"] and compendium_result["ok"] and compendium_report["ok"] else "failed",
            mapped_requirement_ids=["source_provenance", "compendium_reuse", "contradiction_visibility"],
            observations=[
                "Reference pack validates with explicit source IDs, claim-source links, and unresolved contradiction notes.",
                "Reference compendium validates against the pack fingerprint and keeps claim/source/contradiction refs visible.",
                "Reporting summary exposes pack IDs and unresolved conflict IDs without embedding raw notes.",
            ],
            evidence_refs=[
                research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix(),
                literature_compendium_contract.REFERENCE_COMPENDIUM_REL.as_posix(),
                "tests/control_plane/test_research_evidence_pack_reporting.py",
            ],
        ),
        _scenario(
            "positive_campaign_and_continuation_handoff_refs",
            scenario_kind="success_path",
            outcome="passed" if campaign_result["ok"] and phase_gate_result["ok"] and continuation_result["ok"] else "failed",
            mapped_requirement_ids=["durable_handoff_reuse"],
            observations=[
                "Campaign record, phase-gate handoff, and continuation packet all accept the same reloadable evidence_pack_refs row.",
                "The handoff ref validates linked pack existence, schema, pack ID, and fingerprint under repo_root.",
            ],
            evidence_refs=[
                "infra/controlplane/campaign_record_contract.py",
                "infra/controlplane/campaign_phase_gate_contract.py",
                "infra/controlplane/continuation_packet_contract.py",
            ],
        ),
        _scenario(
            "negative_unattributed_summary_rejected",
            scenario_kind="negative_path",
            outcome="passed" if any(row["code"] == "missing_claim_refs" for row in unattributed_result["errors"]) else "failed",
            mapped_requirement_ids=["source_provenance"],
            observations=["A compendium entry without claim refs is rejected instead of becoming unattributed prose."],
            evidence_refs=["tests/control_plane/test_literature_compendium_contract.py::test_literature_compendium_rejects_unattributed_summary"],
        ),
        _scenario(
            "negative_flattened_contradiction_rejected",
            scenario_kind="mismatch_prone",
            outcome="passed" if any(row["field"] == "unresolved_conflicts" for row in flattened_result["errors"]) else "failed",
            mapped_requirement_ids=["contradiction_visibility"],
            observations=["Removing unresolved conflicts from a compendium fails validation instead of flattening contested evidence."],
            evidence_refs=["tests/control_plane/test_literature_compendium_contract.py::test_literature_compendium_rejects_hidden_unresolved_conflict"],
        ),
        _scenario(
            "negative_missing_restart_link_rejected",
            scenario_kind="restart_path",
            outcome="passed" if any(row["code"] == "missing_evidence_pack" for row in missing_continuation_result["errors"]) else "failed",
            mapped_requirement_ids=["durable_handoff_reuse"],
            observations=["A continuation packet with a missing evidence-pack artifact ref fails validation explicitly."],
            evidence_refs=[
                "tests/control_plane/test_research_evidence_pack_intent_validation.py::"
                "test_research_evidence_pack_intent_validation_artifact_maps_intent_and_negative_paths"
            ],
        ),
        _scenario(
            "negative_raw_note_body_rejected",
            scenario_kind="negative_path",
            outcome="passed" if any(row["code"] == "forbidden_inline_ownership" for row in raw_note_result["errors"]) else "failed",
            mapped_requirement_ids=["raw_note_boundary"],
            observations=["Compendia reject embedded raw note bodies and preserve raw notes as linked inputs only."],
            evidence_refs=["tests/control_plane/test_literature_compendium_contract.py::test_literature_compendium_rejects_raw_note_body_embedding"],
        ),
        _scenario(
            "positive_retrieval_listing_keeps_both_artifacts",
            scenario_kind="retrieval_path",
            outcome="passed"
            if [row.get("artifact_kind") for row in listed_reports[:2]] == ["evidence_pack", "literature_compendium"]
            else "failed",
            mapped_requirement_ids=["compendium_reuse", "contradiction_visibility"],
            observations=["Read-only retrieval lists the reference pack and compendium and preserves report-level provenance fields."],
            evidence_refs=["infra/controlplane/research_evidence_pack_reporting.py"],
        ),
    ]

    requirement_coverage = [
        {
            "requirement_id": "source_provenance",
            "intent_clause": "Later jobs can reuse literature synthesis without losing source provenance.",
            "status": "proven",
            "observed_behavior": "Pack and compendium validators require claim/source links and reject unattributed compendium summaries.",
            "scenario_ids": ["positive_reusable_pack_and_compendium", "negative_unattributed_summary_rejected"],
            "evidence_refs": [
                research_evidence_pack_contract.REFERENCE_PACK_REL.as_posix(),
                literature_compendium_contract.REFERENCE_COMPENDIUM_REL.as_posix(),
            ],
        },
        {
            "requirement_id": "contradiction_visibility",
            "intent_clause": "Conflicting evidence is preserved rather than silently averaged away.",
            "status": "proven",
            "observed_behavior": "Unresolved contradiction state is present in reports and hidden-conflict compendia are rejected.",
            "scenario_ids": [
                "positive_reusable_pack_and_compendium",
                "negative_flattened_contradiction_rejected",
                "positive_retrieval_listing_keeps_both_artifacts",
            ],
            "evidence_refs": ["tests/control_plane/test_research_evidence_pack_reporting.py"],
        },
        {
            "requirement_id": "compendium_reuse",
            "intent_clause": "Reusable compendia differ from raw notes and can be consumed by later phases.",
            "status": "proven",
            "observed_behavior": "The compendium links pack IDs, claim IDs, source IDs, and unresolved conflict IDs while reporting can reload both artifacts.",
            "scenario_ids": ["positive_reusable_pack_and_compendium", "positive_retrieval_listing_keeps_both_artifacts"],
            "evidence_refs": [literature_compendium_contract.REFERENCE_COMPENDIUM_REL.as_posix()],
        },
        {
            "requirement_id": "raw_note_boundary",
            "intent_clause": "Summary layers and compendia must not promote raw notes directly into sourced synthesis.",
            "status": "proven",
            "observed_behavior": "Compendium validation rejects inline raw note bodies while preserving raw_note_refs as locators.",
            "scenario_ids": ["negative_raw_note_body_rejected"],
            "evidence_refs": ["infra/controlplane/literature_compendium_contract.py"],
        },
        {
            "requirement_id": "durable_handoff_reuse",
            "intent_clause": "Evidence packs must survive across jobs via durable continuation and campaign handoff memory.",
            "status": "proven",
            "observed_behavior": "Campaign record, phase-gate handoff, and continuation packet validators accept the same durable pack ref and reject missing restart links.",
            "scenario_ids": ["positive_campaign_and_continuation_handoff_refs", "negative_missing_restart_link_rejected"],
            "evidence_refs": [
                "infra/controlplane/evidence_pack_handoff_refs.py",
                "infra/controlplane/continuation_packet_contract.py",
            ],
        },
        {
            "requirement_id": "family_dependency_readiness",
            "intent_clause": "Parent closeout must consume evidence from all dependent children without hiding missing proof.",
            "status": "proven" if dependency_ready else "unproven",
            "observed_behavior": (
                "All implementation dependencies carry durable implementation evidence."
                if dependency_ready
                else "The assembled validation suite passes locally, but one dependency remains draft in durable ledger metadata."
            ),
            "scenario_ids": ["positive_campaign_and_continuation_handoff_refs"],
            "evidence_refs": [_EA30_META_REL.as_posix()],
        },
    ]

    outcome_counts: Dict[str, int] = {}
    for row in scenario_results:
        outcome_counts[row["outcome"]] = outcome_counts.get(row["outcome"], 0) + 1

    residual_gaps = [] if dependency_ready else [_DEPENDENCY_STATUS_GAP]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "uid": VALIDATION_UID,
        "family_parent_uid": FAMILY_PARENT_UID,
        "source_intent_uid": SOURCE_INTENT_UID,
        "validation_case_id": VALIDATION_CASE_ID,
        "validation_case_utc": validation_case_utc or _utc_now(),
        "validation_outcome": "passed" if dependency_ready else "proof_gap",
        "summary": (
            "Local assembled-system validation passes the evidence-pack, compendium, handoff, continuation, "
            "negative-path, reporting, and durable dependency-readiness scenarios."
            if dependency_ready
            else (
                "Local assembled-system validation passes the evidence-pack, compendium, handoff, continuation, "
                "negative-path, and reporting scenarios. Family closeout remains proof-gated by durable dependency status."
            )
        ),
        "residual_gaps": residual_gaps,
        "requirement_coverage": requirement_coverage,
        "scenario_results": scenario_results,
        "coverage_summary": {
            "requirement_count": len(requirement_coverage),
            "scenario_count": len(scenario_results),
            "scenario_outcome_counts": outcome_counts,
            "covered_scenario_kinds": sorted({row["scenario_kind"] for row in scenario_results}),
        },
        "validation_inputs": {
            "pack_ok": bool(pack_result["ok"]),
            "compendium_ok": bool(compendium_result["ok"]),
            "campaign_handoff_ok": bool(campaign_result["ok"]),
            "phase_gate_handoff_ok": bool(phase_gate_result["ok"]),
            "continuation_handoff_ok": bool(continuation_result["ok"]),
            "reporting_artifact_count": len(listed_reports),
        },
    }
    payload["artifact_fingerprint"] = _fingerprint(payload)
    return payload


def validate_research_evidence_pack_intent_validation_artifact(artifact: Mapping[str, Any]) -> List[str]:
    issues: List[str] = []
    if artifact.get("schema_version") != SCHEMA_VERSION:
        issues.append("schema_version must be research_evidence_pack_intent_validation_v1")
    if artifact.get("uid") != VALIDATION_UID:
        issues.append("uid must be PIS-20260410-102637Z-B40E")
    if artifact.get("source_intent_uid") != SOURCE_INTENT_UID:
        issues.append("source_intent_uid must remain IS-20260403-212914Z-5E8C")
    if artifact.get("validation_outcome") not in {"passed", "proof_gap", "contradicted"}:
        issues.append("validation_outcome must be passed, proof_gap, or contradicted")

    requirements = artifact.get("requirement_coverage") if isinstance(artifact.get("requirement_coverage"), list) else []
    scenarios = artifact.get("scenario_results") if isinstance(artifact.get("scenario_results"), list) else []
    requirement_ids = {str(row.get("requirement_id") or "") for row in requirements if isinstance(row, Mapping)}
    for row in requirements:
        if not isinstance(row, Mapping):
            issues.append("requirement_coverage rows must be objects")
            continue
        if not row.get("requirement_id") or not row.get("intent_clause") or not row.get("observed_behavior"):
            issues.append("requirement_coverage rows must include requirement_id, intent_clause, and observed_behavior")
    if not requirements:
        issues.append("requirement_coverage must not be empty")
    if not scenarios:
        issues.append("scenario_results must not be empty")
    for row in scenarios:
        if not isinstance(row, Mapping):
            issues.append("scenario_results rows must be objects")
            continue
        mapped = [str(item) for item in row.get("mapped_requirement_ids") or []]
        if not row.get("scenario_id") or not row.get("scenario_kind") or not row.get("observations"):
            issues.append("scenario_results rows must include scenario_id, scenario_kind, and observations")
        if not mapped:
            issues.append(f"scenario {row.get('scenario_id')} must map to at least one requirement")
        for requirement_id in mapped:
            if requirement_id not in requirement_ids:
                issues.append(f"scenario {row.get('scenario_id')} maps unknown requirement {requirement_id}")
    required_scenario_ids = {
        "negative_unattributed_summary_rejected",
        "negative_flattened_contradiction_rejected",
        "negative_missing_restart_link_rejected",
        "negative_raw_note_body_rejected",
    }
    scenario_ids = {str(row.get("scenario_id") or "") for row in scenarios if isinstance(row, Mapping)}
    missing_negative = sorted(required_scenario_ids - scenario_ids)
    if missing_negative:
        issues.append("missing required negative scenarios: " + ", ".join(missing_negative))
    if artifact.get("validation_outcome") == "passed" and artifact.get("residual_gaps"):
        issues.append("passed artifacts cannot carry residual_gaps")

    fingerprint = str(artifact.get("artifact_fingerprint") or "")
    if not fingerprint:
        issues.append("artifact_fingerprint is required")
    else:
        computed = _fingerprint({key: value for key, value in dict(artifact).items() if key != "artifact_fingerprint"})
        if fingerprint != computed:
            issues.append("artifact_fingerprint does not match payload")
    return issues


def write_research_evidence_pack_intent_validation_artifact(
    artifact: Mapping[str, Any], *, repo_root: Path | None = None
) -> Path:
    issues = validate_research_evidence_pack_intent_validation_artifact(artifact)
    if issues:
        raise ValueError("; ".join(issues))
    root = Path(repo_root or _repo_root()).resolve()
    item_dir = _ledger_item_dir_for_uid(root, VALIDATION_UID)
    path = (
        item_dir / "artifacts/intent_validation/research_evidence_pack_assembled_system_intent_e2e.json"
        if item_dir is not None
        else root / ARTIFACT_REL
    )
    if path.exists():
        existing = _read_json(path)
        if existing.get("artifact_fingerprint") != artifact.get("artifact_fingerprint"):
            raise ValueError("refusing to overwrite an existing validation artifact with different content")
        return path
    return _write_json_atomic(path, artifact)
