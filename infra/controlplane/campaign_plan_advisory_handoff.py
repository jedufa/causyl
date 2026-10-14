"""Explainability and advisory handoff helpers for campaign-plan compilation."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import campaign_plan_contract
from infra.controlplane import compiled_work_order_boundary_emission as compiled_boundary


REQUEST_SCHEMA_VERSION = "campaign_plan_advisory_handoff_request_v1"
ARTIFACT_SCHEMA_VERSION = "campaign_plan_advisory_handoff_artifact_v1"
UID = "PIS-20260411-113602Z-470F"
DEFAULT_ARTIFACT_DIR = Path("runtime/state/controlplane/campaign_plan_advisory_handoff")


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


def _safe_name(value: Any) -> str:
    text = _normalize_text(value)
    safe = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in text).strip("-")
    return safe or "x"


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(dict(payload), indent=2, sort_keys=True, ensure_ascii=False))
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
    return path


def advisory_handoff_artifact_rel(*, source_intent_uid: str, plan_id: str) -> Path:
    return DEFAULT_ARTIFACT_DIR / f"{_safe_name(source_intent_uid)}__{_safe_name(plan_id)}.json"


def _compiled_work_order_payload(
    *,
    request: Mapping[str, Any],
    artifact: Mapping[str, Any],
    repo_root: Path | None = None,
    artifact_rel: Path | None = None,
) -> Dict[str, Any]:
    source_intent_uid = _normalize_text(request.get("source_intent_uid"))
    plan_id = _normalize_text(artifact.get("plan_id"))
    owner_artifact_rel = artifact_rel or advisory_handoff_artifact_rel(source_intent_uid=source_intent_uid, plan_id=plan_id)
    phase_ids = [
        _normalize_text(row.get("phase_id"))
        for row in list(artifact.get("phase_derivations") or [])
        if isinstance(row, Mapping) and _normalize_text(row.get("phase_id"))
    ]
    if not bool(artifact.get("ok")):
        return compiled_boundary.emit_compiled_work_order_boundary(
            boundary_kind="campaign_phase_brief",
            work_order_id=f"{source_intent_uid}__{plan_id}__campaign_phase_brief",
            source_binding={"binding_type": "intent_sheet_uid", "binding_id": source_intent_uid},
            handoff_kind="campaign_phase_brief",
            scope_objective="Compile the stable campaign advisory handoff only when the phase brief is ready and policy receipts are present.",
            in_scope=["phase_derivations", "advisory_child_pis_handoff"],
            out_of_scope=["downstream child execution", "launch authority"],
            required_context_bundle="lab_core_v1",
            additional_context_refs=[],
            forbidden_context_refs=[],
            expected_outputs=[],
            validation_checks=[],
            owner_artifact_rel=owner_artifact_rel,
            fallback_reason_codes=list(artifact.get("reason_codes") or ["campaign_handoff_not_ready"]),
        )
    return compiled_boundary.emit_compiled_work_order_boundary(
        boundary_kind="campaign_phase_brief",
        work_order_id=f"{source_intent_uid}__{plan_id}__campaign_phase_brief",
        source_binding={"binding_type": "intent_sheet_uid", "binding_id": source_intent_uid},
        handoff_kind="campaign_phase_brief",
        scope_objective="Compile the stable campaign phase brief handoff for advisory child-PIS preparation without granting execution authority.",
        in_scope=[
            "phase_derivations",
            "advisory_child_pis_handoff",
            *phase_ids,
        ],
        out_of_scope=[
            "live child launch",
            "builder/verifier compiled-envelope enforcement",
        ],
        required_context_bundle="lab_core_v1",
        additional_context_refs=[
            "governance/scientific_campaign_method_template_v1.json",
            "governance/campaign_plan_contract_v1.json",
        ],
        forbidden_context_refs=[],
        expected_outputs=[
            {
                "output_id": "campaign_phase_brief",
                "kind": "artifact",
                "summary": "Durable advisory campaign phase brief for child-PIS preparation.",
                "required": True,
            }
        ],
        validation_checks=["campaign_phase_synthesis", "campaign_plan_enrichment", "campaign_advisory_handoff"],
        owner_artifact_rel=owner_artifact_rel,
        prose_brief_present=True,
        execution_family={
            "contract_kind": "campaign_phase_advisory",
            "boundary_owner": "orchestrator",
            "launch_authority": "forbidden",
        },
        repo_root=repo_root,
    )


def _phase_receipt_index(receipts: Sequence[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    out: Dict[str, List[Dict[str, Any]]] = {}
    for raw in receipts:
        if not isinstance(raw, Mapping):
            continue
        receipt = dict(raw)
        for phase_id in _normalize_text_list(receipt.get("target_phase_ids")):
            out.setdefault(phase_id, []).append(receipt)
    return out


def _proposal_for_phase(
    phase: Mapping[str, Any],
    *,
    plan: Mapping[str, Any],
    receipts: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    phase_id = _normalize_text(phase.get("phase_id"))
    deliverables = _normalize_text_list(phase.get("deliverables"))
    must_resolve = _normalize_text_list(phase.get("must_resolve_before_pis"))
    proof_focus = deliverables[0] if deliverables else "bounded implementation proof"
    return {
        "proposal_id": f"{phase_id}__advisory_child_pis",
        "source_phase_id": phase_id,
        "proposed_title": f"{_normalize_text(phase.get('label'))} Child PIS Proposal",
        "advisory_only": True,
        "execution_owner": "",
        "launch_authority": "forbidden",
        "bounded_scope": _normalize_text(phase.get("goal")),
        "proof_focus": proof_focus,
        "required_resolution_topics": must_resolve,
        "policy_receipt_ids": [
            _normalize_text(row.get("receipt_id"))
            for row in receipts
            if _normalize_text(row.get("receipt_id"))
        ],
        "lineage_refs": _normalize_text_list(plan.get("provenance", {}).get("supporting_artifact_refs")),
    }


def build_campaign_plan_advisory_handoff_artifact(raw_request: Mapping[str, Any]) -> Dict[str, Any]:
    request = dict(raw_request or {})
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    if _normalize_text(request.get("schema_version") or REQUEST_SCHEMA_VERSION) != REQUEST_SCHEMA_VERSION:
        errors.append(
            {
                "field": "schema_version",
                "code": "invalid_request_schema",
                "message": f"schema_version must remain {REQUEST_SCHEMA_VERSION}",
            }
        )

    source_intent_uid = _normalize_text(request.get("source_intent_uid"))
    phase_synthesis_report = request.get("phase_synthesis_report") if isinstance(request.get("phase_synthesis_report"), Mapping) else {}
    enrichment_report = request.get("enrichment_report") if isinstance(request.get("enrichment_report"), Mapping) else {}
    synthesis_request = phase_synthesis_report.get("normalized_request") if isinstance(phase_synthesis_report.get("normalized_request"), Mapping) else {}
    phase_generation_trace = _normalize_text_list(phase_synthesis_report.get("phase_generation_trace"))
    plan = enrichment_report.get("campaign_plan") if isinstance(enrichment_report.get("campaign_plan"), Mapping) else {}
    plan_validation = campaign_plan_contract.validate_campaign_plan(plan)
    normalized_plan = plan_validation.get("normalized_plan") if isinstance(plan_validation.get("normalized_plan"), Mapping) else {}
    policy_enrichment = normalized_plan.get("policy_enrichment") if isinstance(normalized_plan.get("policy_enrichment"), Mapping) else {}
    receipts = [
        dict(row)
        for row in list(policy_enrichment.get("enrichment_receipts") or [])
        if isinstance(row, Mapping)
    ]
    receipt_index = _phase_receipt_index(receipts)

    if not source_intent_uid:
        errors.append(
            {
                "field": "source_intent_uid",
                "code": "missing_field",
                "message": "source_intent_uid is required",
            }
        )
    if not phase_synthesis_report or not bool(phase_synthesis_report.get("ok")):
        errors.append(
            {
                "field": "phase_synthesis_report",
                "code": "phase_synthesis_not_ready",
                "message": "phase_synthesis_report must be a successful compilation result",
            }
        )
    if not phase_generation_trace:
        errors.append(
            {
                "field": "phase_synthesis_report.phase_generation_trace",
                "code": "missing_phase_generation_trace",
                "message": "phase_generation_trace is required for explainability",
            }
        )
    if not enrichment_report or not bool(enrichment_report.get("ok")):
        errors.append(
            {
                "field": "enrichment_report",
                "code": "plan_enrichment_not_ready",
                "message": "enrichment_report must be a successful enrichment result",
            }
        )
    if plan_validation.get("errors"):
        errors.extend(
            {
                "field": f"campaign_plan.{row['field']}",
                "code": row["code"],
                "message": row["message"],
            }
            for row in plan_validation["errors"]
        )
    if not receipts:
        errors.append(
            {
                "field": "campaign_plan.policy_enrichment.enrichment_receipts",
                "code": "missing_policy_enrichment_receipts",
                "message": "enrichment receipts are required so policy changes stay inspectable",
            }
        )
    metadata = normalized_plan.get("metadata") if isinstance(normalized_plan.get("metadata"), Mapping) else {}
    provenance = normalized_plan.get("provenance") if isinstance(normalized_plan.get("provenance"), Mapping) else {}
    for field, value in {
        "campaign_plan.metadata.source_intent_uid": _normalize_text(metadata.get("source_intent_uid")),
        "campaign_plan.provenance.source_intent_uid": _normalize_text(provenance.get("source_intent_uid")),
    }.items():
        if source_intent_uid and value != source_intent_uid:
            errors.append(
                {
                    "field": field,
                    "code": "source_intent_uid_mismatch",
                    "message": "campaign plan explainability must remain anchored to one source intent",
                }
            )

    proposals: List[Dict[str, Any]] = []
    phase_derivations: List[Dict[str, Any]] = []
    for raw_phase in list(normalized_plan.get("phases") or []):
        if not isinstance(raw_phase, Mapping):
            continue
        phase = dict(raw_phase)
        phase_id = _normalize_text(phase.get("phase_id"))
        phase_receipts = receipt_index.get(phase_id, [])
        if not phase_receipts:
            warnings.append(
                {
                    "field": f"phases.{phase_id}",
                    "code": "phase_without_policy_receipt",
                    "message": f"phase {phase_id} has no direct policy receipt coverage",
                }
            )
        phase_derivations.append(
            {
                "phase_id": phase_id,
                "label": _normalize_text(phase.get("label")),
                "goal": _normalize_text(phase.get("goal")),
                "deliverables": _normalize_text_list(phase.get("deliverables")),
                "method_template_phase_ids": _normalize_text_list(phase.get("method_template_phase_ids")),
                "budget_mode": _normalize_text(phase.get("budget_mode")),
                "allowed_ambiguities": _normalize_text_list(phase.get("allowed_ambiguities")),
                "must_resolve_before_pis": _normalize_text_list(phase.get("must_resolve_before_pis")),
                "pis_readiness": _normalize_text(phase.get("pis_readiness")),
                "source_intent_links": {
                    "intent_summary": _normalize_text(synthesis_request.get("intent_summary")),
                    "required_outputs": _normalize_text_list(synthesis_request.get("required_outputs")),
                    "review_gate_topics": _normalize_text_list(synthesis_request.get("review_gate_topics")),
                },
                "policy_receipt_ids": [
                    _normalize_text(row.get("receipt_id"))
                    for row in phase_receipts
                    if _normalize_text(row.get("receipt_id"))
                ],
            }
        )
        if _normalize_text(phase.get("pis_readiness")) == "ready_for_pis_generation":
            proposals.append(_proposal_for_phase(phase, plan=normalized_plan, receipts=phase_receipts))

    if not proposals:
        errors.append(
            {
                "field": "campaign_plan.phases",
                "code": "missing_advisory_handoff_phase",
                "message": "at least one phase must be ready_for_pis_generation before advisory handoff is honest",
            }
        )

    reason_codes = list(dict.fromkeys(row["code"] for row in errors))
    artifact = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "uid": UID,
        "source_intent_uid": source_intent_uid,
        "plan_id": _normalize_text(normalized_plan.get("plan_id")),
        "plan_fingerprint": _normalize_text(plan_validation.get("computed_fingerprint")),
        "status": "advisory_ready" if not errors else "blocked",
        "ok": not errors,
        "reason_codes": reason_codes,
        "errors": errors,
        "warnings": warnings,
        "phase_generation_trace": phase_generation_trace,
        "explainability_summary": {
            "intent_summary": _normalize_text(synthesis_request.get("intent_summary")),
            "selected_budget_mode": _normalize_text(policy_enrichment.get("selected_budget_mode")),
            "declared_constraint_ids": [
                _normalize_text(row.get("constraint_id"))
                for row in list(policy_enrichment.get("declared_constraints") or [])
                if isinstance(row, Mapping) and _normalize_text(row.get("constraint_id"))
            ],
            "policy_receipt_ids": [
                _normalize_text(row.get("receipt_id"))
                for row in receipts
                if _normalize_text(row.get("receipt_id"))
            ],
        },
        "phase_derivations": phase_derivations,
        "advisory_child_pis_handoff": {
            "status": "advisory_only" if proposals else "blocked",
            "proposal_count": len(proposals),
            "proposals": proposals,
            "blockers": _normalize_text_list(normalized_plan.get("ambiguity_policy", {}).get("must_resolve_before_pis")),
        },
    }
    artifact["compiled_work_order"] = _compiled_work_order_payload(request=request, artifact=artifact)
    artifact["artifact_fingerprint"] = _fingerprint(artifact)
    return artifact


def validate_campaign_plan_advisory_handoff_artifact(payload: Mapping[str, Any]) -> List[str]:
    artifact = dict(payload or {})
    issues: List[str] = []
    if _normalize_text(artifact.get("schema_version")) != ARTIFACT_SCHEMA_VERSION:
        issues.append(f"schema_version must remain {ARTIFACT_SCHEMA_VERSION}")
    for field in ("uid", "source_intent_uid", "plan_id", "plan_fingerprint", "artifact_fingerprint"):
        if not _normalize_text(artifact.get(field)):
            issues.append(f"{field} is required")
    if _normalize_text(artifact.get("uid")) != UID:
        issues.append(f"uid must remain {UID}")
    if not _normalize_text_list(artifact.get("phase_generation_trace")):
        issues.append("phase_generation_trace is required")
    phase_derivations = artifact.get("phase_derivations")
    if not isinstance(phase_derivations, list) or not phase_derivations:
        issues.append("phase_derivations must contain at least one phase row")
    handoff = artifact.get("advisory_child_pis_handoff")
    if not isinstance(handoff, Mapping):
        issues.append("advisory_child_pis_handoff is required")
    else:
        proposals = handoff.get("proposals")
        if not isinstance(proposals, list) or not proposals:
            issues.append("advisory_child_pis_handoff.proposals must contain at least one advisory proposal")
        else:
            for idx, raw in enumerate(proposals):
                if not isinstance(raw, Mapping):
                    issues.append(f"proposal {idx} must be an object")
                    continue
                if raw.get("advisory_only") is not True:
                    issues.append(f"proposal {idx} must remain advisory_only")
                if _normalize_text(raw.get("launch_authority")) != "forbidden":
                    issues.append(f"proposal {idx} must keep launch_authority=forbidden")
                if _normalize_text(raw.get("execution_owner")):
                    issues.append(f"proposal {idx} must not claim execution_owner")
    expected = _fingerprint({k: v for k, v in artifact.items() if k != "artifact_fingerprint"})
    if _normalize_text(artifact.get("artifact_fingerprint")) != expected:
        issues.append("artifact_fingerprint does not match payload")
    return issues


def write_campaign_plan_advisory_handoff_artifact(
    artifact: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    artifact_rel: Path | None = None,
) -> Path:
    root = Path(repo_root or ".").resolve()
    payload = dict(artifact or {})
    owner_artifact_rel = artifact_rel or advisory_handoff_artifact_rel(
        source_intent_uid=_normalize_text(payload.get("source_intent_uid")),
        plan_id=_normalize_text(payload.get("plan_id")),
    )
    payload["compiled_work_order"] = _compiled_work_order_payload(
        request={"source_intent_uid": _normalize_text(payload.get("source_intent_uid"))},
        artifact=payload,
        repo_root=root,
        artifact_rel=owner_artifact_rel,
    )
    payload["artifact_fingerprint"] = _fingerprint({k: v for k, v in payload.items() if k != "artifact_fingerprint"})
    return _write_json_atomic((root / owner_artifact_rel).resolve(), payload)
