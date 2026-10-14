"""Canonical scientific campaign method template v1 helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


SCHEMA_VERSION = "scientific_campaign_method_template_v1"
CONTRACT_SCHEMA_VERSION = "scientific_campaign_method_template_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/scientific_campaign_method_template_v1.json")
REFERENCE_TEMPLATE_REL = Path("governance/scientific_campaign_method_template_reference_v1.json")
PILOT_FIXTURES_REL = Path("governance/scientific_campaign_method_template_pilot_fixtures_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_scientific_campaign_method_template_v1.md")
UID = "PIS-20260410-193415Z-2A95"

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "template_id",
    "template_version",
    "stable_identity",
    "metadata",
    "ownership_boundary",
    "phases",
)
STABLE_IDENTITY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "canonicalization",
    "content_hash_algorithm",
    "schema_migration_policy",
)
METADATA_REQUIRED_FIELDS: Tuple[str, ...] = (
    "title",
    "created_at_utc",
    "source_intent_uid",
    "ledger_refs",
)
OWNERSHIP_BOUNDARY_REQUIRED_FIELDS: Tuple[str, ...] = (
    "belongs_in_method_template",
    "outside_method_template",
)
PHASE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "phase_id",
    "phase_order",
    "label",
    "purpose",
    "required_inputs",
    "required_outputs",
    "completion_signals",
    "input_linkages",
    "output_linkages",
)
PHASE_EXTENSION_REQUIRED_FIELDS: Dict[str, Tuple[str, ...]] = {
    "hypothesis_registry": ("hypotheses",),
    "adversarial_review": ("review",),
    "outcome_documentation": ("outcome_record",),
    "continuation": ("continuation_notes",),
}
HYPOTHESIS_REQUIRED_FIELDS: Tuple[str, ...] = (
    "hypothesis_id",
    "statement",
    "claim_strength_tier",
    "falsifiability_notes",
    "support_basis",
    "uncertainty_notes",
    "unresolved_questions",
)
ADVERSARIAL_REVIEW_REQUIRED_FIELDS: Tuple[str, ...] = (
    "prompt",
    "challenge_questions",
    "review_verdict",
    "residual_risk_notes",
)
OUTCOME_RECORD_REQUIRED_FIELDS: Tuple[str, ...] = (
    "result_class",
    "summary",
    "uncertainty_notes",
    "unresolved_questions",
    "failure_notes",
)
CONTINUATION_NOTES_REQUIRED_FIELDS: Tuple[str, ...] = (
    "next_decision",
    "handoff_summary",
    "stop_conditions",
)
PHASE_LINKAGE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "link_kind",
    "contract_ref",
    "link_mode",
    "purpose",
)
PHASE_IDS: Tuple[str, ...] = (
    "question_framing",
    "prior_knowledge_consolidation",
    "hypothesis_registry",
    "experiment_design",
    "analysis_plan",
    "adversarial_review",
    "outcome_documentation",
    "continuation",
)
FORBIDDEN_TOP_LEVEL_FIELDS: Tuple[str, ...] = (
    "campaign_execution_record",
    "campaign_record_body",
    "evidence_pack_payload",
    "continuation_packet_payload",
    "budget_adaptive_rigor_policy",
    "prompt_prose_template",
    "template_summary_only",
)
FORBIDDEN_PHASE_FIELDS: Tuple[str, ...] = (
    "evidence_pack_body",
    "continuation_packet_body",
    "execution_state",
    "budget_policy_override",
    "prompt_text",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "mandatory_phase_ids_must_be_present_exactly_once",
    "phase_vocabulary_must_use_canonical_ids",
    "phase_rows_require_structured_inputs_outputs_and_completion_signals",
    "phase_handoff_links_must_use_reference_only_neighbor_links",
    "each_phase_must_name_campaign_phase_record_authority",
    "prior_knowledge_consolidation_requires_evidence_pack_linkage",
    "continuation_phase_requires_continuation_packet_linkage",
    "hypothesis_registry_requires_structured_claim_strength_and_uncertainty_records",
    "adversarial_review_requires_structured_prompt_and_residual_risk_notes",
    "negative_results_require_failure_notes_and_follow_up_visibility",
    "prose_only_templates_rejected",
    "adjacent_execution_evidence_continuation_and_budget_payloads_forbidden_inline",
)
CLAIM_STRENGTH_TIERS: Tuple[str, ...] = (
    "exploratory_hunch",
    "working_claim",
    "strong_claim",
)
REVIEW_VERDICTS: Tuple[str, ...] = (
    "pass_with_residual_risk",
    "revise_before_execution",
    "blocked_pending_missing_challenge",
)
RESULT_CLASSES: Tuple[str, ...] = (
    "supporting_signal",
    "negative_result",
    "inconclusive",
)
LINK_KINDS: Tuple[str, ...] = (
    "campaign_phase_record",
    "evidence_pack_ref",
    "continuation_packet",
)
LINK_MODE_REFERENCE_ONLY = "reference_only"
LINK_KIND_TO_CONTRACT_REF = {
    "campaign_phase_record": "docs/contracts/contract_campaign_record_schema_v1.md",
    "evidence_pack_ref": "docs/contracts/contract_research_evidence_pack_v1.md",
    "continuation_packet": "docs/contracts/continuation_packet_v1.md",
}
ADVISORY_METHOD_POSTURE_CODES: Tuple[str, ...] = (
    "missing_prior_knowledge_evidence_chain",
    "missing_hypothesis_uncertainty_notes",
    "missing_adversarial_review_challenge_questions",
    "missing_outcome_uncertainty_notes",
    "missing_continuation_stop_conditions",
)
PILOT_FIXTURE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "fixture_id",
    "campaign_label",
    "campaign_kind",
    "focus_summary",
    "method_template",
)


@dataclass(frozen=True)
class ScientificCampaignMethodTemplateContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    reference_template_rel: Path
    pilot_fixtures_rel: Path
    canonical_doc_rel: Path
    top_level_required_fields: Tuple[str, ...]
    phase_ids: Tuple[str, ...]
    phase_required_fields: Tuple[str, ...]
    phase_extension_required_fields: Dict[str, Tuple[str, ...]]
    fail_closed_rules: Tuple[str, ...]
    advisory_method_posture_codes: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


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


def _normalize_positive_int(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 0
    return parsed if parsed > 0 else 0


def _append_missing_errors(errors: List[Dict[str, str]], prefix: str, row: Mapping[str, Any], fields: Sequence[str]) -> None:
    for field in fields:
        value = row.get(field)
        if value in ("", None, [], {}):
            errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})


def _normalize_hypothesis(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "hypothesis_id": _normalize_text(row.get("hypothesis_id")),
        "statement": _normalize_text(row.get("statement")),
        "claim_strength_tier": _normalize_text(row.get("claim_strength_tier")),
        "falsifiability_notes": _normalize_text(row.get("falsifiability_notes")),
        "support_basis": _normalize_text_list(row.get("support_basis")),
        "uncertainty_notes": _normalize_text(row.get("uncertainty_notes")),
        "unresolved_questions": _normalize_text_list(row.get("unresolved_questions")),
    }


def _normalize_adversarial_review(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "prompt": _normalize_text(row.get("prompt")),
        "challenge_questions": _normalize_text_list(row.get("challenge_questions")),
        "review_verdict": _normalize_text(row.get("review_verdict")),
        "residual_risk_notes": _normalize_text(row.get("residual_risk_notes")),
    }


def _normalize_outcome_record(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "result_class": _normalize_text(row.get("result_class")),
        "summary": _normalize_text(row.get("summary")),
        "uncertainty_notes": _normalize_text(row.get("uncertainty_notes")),
        "unresolved_questions": _normalize_text_list(row.get("unresolved_questions")),
        "failure_notes": _normalize_text_list(row.get("failure_notes")),
    }


def _normalize_continuation_notes(row: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "next_decision": _normalize_text(row.get("next_decision")),
        "handoff_summary": _normalize_text(row.get("handoff_summary")),
        "stop_conditions": _normalize_text_list(row.get("stop_conditions")),
    }


def _normalize_phase_linkage(row: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "link_kind": _normalize_text(row.get("link_kind")),
        "contract_ref": _normalize_text(row.get("contract_ref")),
        "link_mode": _normalize_text(row.get("link_mode")),
        "purpose": _normalize_text(row.get("purpose")),
    }


def _normalize_phase_linkages(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, str]] = []
    for raw in value:
        if isinstance(raw, Mapping):
            out.append(_normalize_phase_linkage(raw))
    out.sort(key=lambda row: (row["link_kind"], row["contract_ref"], row["purpose"]))
    return out


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def scientific_campaign_method_template_contract_v1() -> ScientificCampaignMethodTemplateContractV1:
    return ScientificCampaignMethodTemplateContractV1(
        version=CONTRACT_SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        reference_template_rel=REFERENCE_TEMPLATE_REL,
        pilot_fixtures_rel=PILOT_FIXTURES_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        phase_ids=PHASE_IDS,
        phase_required_fields=PHASE_REQUIRED_FIELDS,
        phase_extension_required_fields=PHASE_EXTENSION_REQUIRED_FIELDS,
        fail_closed_rules=FAIL_CLOSED_RULES,
        advisory_method_posture_codes=ADVISORY_METHOD_POSTURE_CODES,
    )


def load_scientific_campaign_method_template_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_reference_scientific_campaign_method_template(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / REFERENCE_TEMPLATE_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_scientific_campaign_method_template_pilot_fixtures(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / PILOT_FIXTURES_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_scientific_campaign_method_template(raw_template: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_template or {})
    stable_identity = raw.get("stable_identity") if isinstance(raw.get("stable_identity"), Mapping) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    ownership_boundary = raw.get("ownership_boundary") if isinstance(raw.get("ownership_boundary"), Mapping) else {}
    phases_raw = raw.get("phases") if isinstance(raw.get("phases"), list) else []
    phases: List[Dict[str, Any]] = []
    for phase in phases_raw:
        if not isinstance(phase, Mapping):
            continue
        normalized_phase = {
            "phase_id": _normalize_text(phase.get("phase_id")),
            "phase_order": _normalize_positive_int(phase.get("phase_order")),
            "label": _normalize_text(phase.get("label")),
            "purpose": _normalize_text(phase.get("purpose")),
            "required_inputs": _normalize_text_list(phase.get("required_inputs")),
            "required_outputs": _normalize_text_list(phase.get("required_outputs")),
            "completion_signals": _normalize_text_list(phase.get("completion_signals")),
            "input_linkages": _normalize_phase_linkages(phase.get("input_linkages")),
            "output_linkages": _normalize_phase_linkages(phase.get("output_linkages")),
        }
        phase_id = normalized_phase["phase_id"]
        if phase_id == "hypothesis_registry":
            hypotheses_raw = phase.get("hypotheses") if isinstance(phase.get("hypotheses"), list) else []
            normalized_phase["hypotheses"] = [_normalize_hypothesis(row) for row in hypotheses_raw if isinstance(row, Mapping)]
        if phase_id == "adversarial_review":
            review_raw = phase.get("review") if isinstance(phase.get("review"), Mapping) else {}
            normalized_phase["review"] = _normalize_adversarial_review(review_raw)
        if phase_id == "outcome_documentation":
            outcome_raw = phase.get("outcome_record") if isinstance(phase.get("outcome_record"), Mapping) else {}
            normalized_phase["outcome_record"] = _normalize_outcome_record(outcome_raw)
        if phase_id == "continuation":
            continuation_raw = phase.get("continuation_notes") if isinstance(phase.get("continuation_notes"), Mapping) else {}
            normalized_phase["continuation_notes"] = _normalize_continuation_notes(continuation_raw)
        phases.append(normalized_phase)
    phases.sort(key=lambda row: (row["phase_order"], row["phase_id"]))
    return {
        "schema_version": _normalize_text(raw.get("schema_version") or SCHEMA_VERSION),
        "template_id": _normalize_text(raw.get("template_id")),
        "template_version": _normalize_text(raw.get("template_version")),
        "stable_identity": {
            "canonicalization": _normalize_text(stable_identity.get("canonicalization")),
            "content_hash_algorithm": _normalize_text(stable_identity.get("content_hash_algorithm")),
            "schema_migration_policy": _normalize_text(stable_identity.get("schema_migration_policy")),
        },
        "metadata": {
            "title": _normalize_text(metadata.get("title")),
            "created_at_utc": _normalize_text(metadata.get("created_at_utc")),
            "source_intent_uid": _normalize_text(metadata.get("source_intent_uid")),
            "ledger_refs": _normalize_text_list(metadata.get("ledger_refs")),
        },
        "ownership_boundary": {
            "belongs_in_method_template": _normalize_text_list(ownership_boundary.get("belongs_in_method_template")),
            "outside_method_template": _normalize_text_list(ownership_boundary.get("outside_method_template")),
        },
        "phases": phases,
    }


def validate_scientific_campaign_method_template(raw_template: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_template or {})
    normalized = normalize_scientific_campaign_method_template(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", [], {}) or value is None:
            errors.append({"field": field, "code": "missing_field", "message": f"method template requires {field}"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": f"schema_version must be {SCHEMA_VERSION}"})

    _append_missing_errors(errors, "stable_identity", normalized["stable_identity"], STABLE_IDENTITY_REQUIRED_FIELDS)
    _append_missing_errors(errors, "metadata", normalized["metadata"], METADATA_REQUIRED_FIELDS)
    _append_missing_errors(errors, "ownership_boundary", normalized["ownership_boundary"], OWNERSHIP_BOUNDARY_REQUIRED_FIELDS)

    for forbidden in FORBIDDEN_TOP_LEVEL_FIELDS:
        if forbidden in raw and raw.get(forbidden) not in (None, "", [], {}, ()):
            errors.append(
                {
                    "field": forbidden,
                    "code": "forbidden_prose_only_template" if "prompt" in forbidden or "summary" in forbidden else "forbidden_inline_ownership",
                    "message": "method templates stay structured and link adjacent systems rather than embedding or replacing them",
                }
            )

    phase_ids_seen: List[str] = []
    phase_orders_seen: List[int] = []
    for index, phase in enumerate(normalized["phases"]):
        prefix = f"phases[{index}]"
        _append_missing_errors(errors, prefix, phase, PHASE_REQUIRED_FIELDS)

        phase_id = phase["phase_id"]
        if phase_id in phase_ids_seen:
            errors.append({"field": f"{prefix}.phase_id", "code": "duplicate_phase_id", "message": "phase_id values must be unique"})
        else:
            phase_ids_seen.append(phase_id)

        phase_order = phase["phase_order"]
        if phase_order in phase_orders_seen and phase_order != 0:
            errors.append({"field": f"{prefix}.phase_order", "code": "duplicate_phase_order", "message": "phase_order values must be unique"})
        else:
            phase_orders_seen.append(phase_order)

        if phase_id and phase_id not in PHASE_IDS:
            errors.append({"field": f"{prefix}.phase_id", "code": "unknown_phase_id", "message": "phase vocabulary drifted from the canonical method phase ids"})

        for field in ("required_inputs", "required_outputs", "completion_signals"):
            if not isinstance(phase.get(field), list) or not phase.get(field):
                errors.append(
                    {
                        "field": f"{prefix}.{field}",
                        "code": "missing_field",
                        "message": f"{prefix}.{field} requires at least one structured entry",
                    }
                )

        for field in ("input_linkages", "output_linkages"):
            linkages = phase.get(field) if isinstance(phase.get(field), list) else []
            if not linkages:
                errors.append(
                    {
                        "field": f"{prefix}.{field}",
                        "code": "missing_field",
                        "message": f"{prefix}.{field} must describe adjacent durable references instead of leaving the handoff implicit",
                    }
                )
                continue

            for linkage_index, linkage in enumerate(linkages):
                linkage_prefix = f"{prefix}.{field}[{linkage_index}]"
                _append_missing_errors(errors, linkage_prefix, linkage, PHASE_LINKAGE_REQUIRED_FIELDS)
                link_kind = linkage.get("link_kind")
                if link_kind and link_kind not in LINK_KINDS:
                    errors.append(
                        {
                            "field": f"{linkage_prefix}.link_kind",
                            "code": "invalid_value",
                            "message": "link_kind must use the canonical campaign_phase_record, evidence_pack_ref, or continuation_packet vocabulary",
                        }
                    )
                link_mode = linkage.get("link_mode")
                if link_mode and link_mode != LINK_MODE_REFERENCE_ONLY:
                    errors.append(
                        {
                            "field": f"{linkage_prefix}.link_mode",
                            "code": "invalid_link_mode",
                            "message": "adjacent-system handoffs must stay reference_only instead of smuggling inline payloads",
                        }
                    )
                expected_contract_ref = LINK_KIND_TO_CONTRACT_REF.get(str(link_kind))
                if expected_contract_ref and linkage.get("contract_ref") != expected_contract_ref:
                    errors.append(
                        {
                            "field": f"{linkage_prefix}.contract_ref",
                            "code": "invalid_contract_ref",
                            "message": f"{link_kind} links must point to {expected_contract_ref}",
                        }
                    )

        for field in PHASE_EXTENSION_REQUIRED_FIELDS.get(phase_id, ()):
            value = phase.get(field)
            if value in ("", None, [], {}):
                errors.append(
                    {
                        "field": f"{prefix}.{field}",
                        "code": "missing_field",
                        "message": f"{prefix}.{field} is required for the {phase_id} phase",
                    }
                )

        if phase_id == "hypothesis_registry":
            hypotheses = phase.get("hypotheses") if isinstance(phase.get("hypotheses"), list) else []
            if not hypotheses:
                errors.append(
                    {
                        "field": f"{prefix}.hypotheses",
                        "code": "missing_field",
                        "message": "hypothesis registry must contain at least one structured hypothesis record",
                    }
                )
            for hypothesis_index, hypothesis in enumerate(hypotheses):
                hypothesis_prefix = f"{prefix}.hypotheses[{hypothesis_index}]"
                _append_missing_errors(errors, hypothesis_prefix, hypothesis, HYPOTHESIS_REQUIRED_FIELDS)
                claim_strength_tier = hypothesis.get("claim_strength_tier")
                if claim_strength_tier and claim_strength_tier not in CLAIM_STRENGTH_TIERS:
                    errors.append(
                        {
                            "field": f"{hypothesis_prefix}.claim_strength_tier",
                            "code": "invalid_value",
                            "message": "claim_strength_tier must use the canonical exploratory_hunch, working_claim, or strong_claim vocabulary",
                        }
                    )
                support_basis = hypothesis.get("support_basis") if isinstance(hypothesis.get("support_basis"), list) else []
                if claim_strength_tier == "strong_claim" and not support_basis:
                    errors.append(
                        {
                            "field": f"{hypothesis_prefix}.support_basis",
                            "code": "unsupported_strong_claim",
                            "message": "strong_claim entries require explicit support_basis notes instead of confidence by vibes",
                        }
                    )

        if phase_id == "adversarial_review":
            review = phase.get("review") if isinstance(phase.get("review"), Mapping) else {}
            _append_missing_errors(errors, f"{prefix}.review", review, ADVERSARIAL_REVIEW_REQUIRED_FIELDS)
            verdict = review.get("review_verdict")
            if verdict and verdict not in REVIEW_VERDICTS:
                errors.append(
                    {
                        "field": f"{prefix}.review.review_verdict",
                        "code": "invalid_value",
                        "message": "review_verdict must use the canonical adversarial review verdict vocabulary",
                    }
                )
            if not isinstance(review.get("challenge_questions"), list) or not review.get("challenge_questions"):
                errors.append(
                    {
                        "field": f"{prefix}.review.challenge_questions",
                        "code": "missing_field",
                        "message": "adversarial review requires at least one challenge question",
                    }
                )

        if phase_id == "outcome_documentation":
            outcome_record = phase.get("outcome_record") if isinstance(phase.get("outcome_record"), Mapping) else {}
            _append_missing_errors(errors, f"{prefix}.outcome_record", outcome_record, OUTCOME_RECORD_REQUIRED_FIELDS)
            result_class = outcome_record.get("result_class")
            if result_class and result_class not in RESULT_CLASSES:
                errors.append(
                    {
                        "field": f"{prefix}.outcome_record.result_class",
                        "code": "invalid_value",
                        "message": "result_class must use the canonical supporting_signal, negative_result, or inconclusive vocabulary",
                    }
                )
            failure_notes = outcome_record.get("failure_notes") if isinstance(outcome_record.get("failure_notes"), list) else []
            if result_class == "negative_result" and not failure_notes:
                errors.append(
                    {
                        "field": f"{prefix}.outcome_record.failure_notes",
                        "code": "missing_negative_result_failure_notes",
                        "message": "negative_result outcomes must preserve explicit failure notes",
                    }
                )

        if phase_id == "continuation":
            continuation_notes = phase.get("continuation_notes") if isinstance(phase.get("continuation_notes"), Mapping) else {}
            _append_missing_errors(errors, f"{prefix}.continuation_notes", continuation_notes, CONTINUATION_NOTES_REQUIRED_FIELDS)
            if not isinstance(continuation_notes.get("stop_conditions"), list) or not continuation_notes.get("stop_conditions"):
                errors.append(
                    {
                        "field": f"{prefix}.continuation_notes.stop_conditions",
                        "code": "missing_field",
                        "message": "continuation notes require at least one stop condition or next-check trigger",
                    }
                )

        input_linkages = phase.get("input_linkages") if isinstance(phase.get("input_linkages"), list) else []
        output_linkages = phase.get("output_linkages") if isinstance(phase.get("output_linkages"), list) else []
        all_link_kinds = {str(row.get("link_kind") or "") for row in [*input_linkages, *output_linkages]}
        output_link_kinds = {str(row.get("link_kind") or "") for row in output_linkages}

        if "campaign_phase_record" not in all_link_kinds:
            errors.append(
                {
                    "field": f"{prefix}.input_linkages",
                    "code": "missing_campaign_phase_link",
                    "message": "each method phase must name the canonical campaign phase record interface instead of inventing private lifecycle truth",
                }
            )
        if phase_id == "prior_knowledge_consolidation" and "evidence_pack_ref" not in all_link_kinds:
            errors.append(
                {
                    "field": f"{prefix}.input_linkages",
                    "code": "missing_evidence_pack_link",
                    "message": "prior_knowledge_consolidation must link a canonical evidence pack before design can promote folklore into plans",
                }
            )
        if phase_id == "outcome_documentation" and "campaign_phase_record" not in output_link_kinds:
            errors.append(
                {
                    "field": f"{prefix}.output_linkages",
                    "code": "missing_campaign_phase_output_link",
                    "message": "outcome_documentation outputs must link back to the canonical campaign phase record for lifecycle truth",
                }
            )
        if phase_id == "continuation" and "continuation_packet" not in output_link_kinds:
            errors.append(
                {
                    "field": f"{prefix}.output_linkages",
                    "code": "missing_continuation_packet_link",
                    "message": "continuation outputs must link a canonical continuation packet instead of ending in vibes",
                }
            )

        raw_phase = raw.get("phases", [])[index] if isinstance(raw.get("phases"), list) and index < len(raw.get("phases", [])) else {}
        if isinstance(raw_phase, Mapping):
            for forbidden in FORBIDDEN_PHASE_FIELDS:
                if forbidden in raw_phase and raw_phase.get(forbidden) not in (None, "", [], {}, ()):
                    errors.append(
                        {
                            "field": f"{prefix}.{forbidden}",
                            "code": "forbidden_inline_ownership",
                            "message": "phase rows may declare obligations and links but do not inline execution, evidence, continuation, or budget payloads",
                        }
                    )

    missing_phase_ids = [phase_id for phase_id in PHASE_IDS if phase_id not in phase_ids_seen]
    for phase_id in missing_phase_ids:
        errors.append(
            {
                "field": f"phases:{phase_id}",
                "code": "missing_mandatory_phase",
                "message": f"canonical method template must include the {phase_id} phase",
            }
        )

    if normalized["phases"] and tuple(phase["phase_order"] for phase in normalized["phases"]) != tuple(range(1, len(normalized["phases"]) + 1)):
        warnings.append(
            {
                "field": "phases.phase_order",
                "code": "nonsequential_phase_order",
                "message": "phase_order is easier to diff when it remains sequential",
            }
        )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_template": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def assess_scientific_campaign_method_advisory_admission(raw_template: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = normalize_scientific_campaign_method_template(raw_template)
    warnings: List[Dict[str, str]] = []
    phases_by_id = {
        str(row.get("phase_id") or ""): row
        for row in normalized.get("phases", [])
        if isinstance(row, Mapping) and str(row.get("phase_id") or "")
    }

    prior_knowledge = phases_by_id.get("prior_knowledge_consolidation", {})
    prior_link_kinds = {
        str(link.get("link_kind") or "")
        for link in [
            *(prior_knowledge.get("input_linkages") if isinstance(prior_knowledge.get("input_linkages"), list) else []),
            *(prior_knowledge.get("output_linkages") if isinstance(prior_knowledge.get("output_linkages"), list) else []),
        ]
        if isinstance(link, Mapping)
    }
    if "evidence_pack_ref" not in prior_link_kinds:
        warnings.append(
            {
                "field": "phases.prior_knowledge_consolidation",
                "code": "missing_prior_knowledge_evidence_chain",
                "message": "Advisory admission expects prior knowledge consolidation to keep an evidence-pack chain before design work scales up.",
            }
        )

    hypothesis_registry = phases_by_id.get("hypothesis_registry", {})
    hypotheses = hypothesis_registry.get("hypotheses") if isinstance(hypothesis_registry.get("hypotheses"), list) else []
    if not hypotheses or any(not str(row.get("uncertainty_notes") or "").strip() for row in hypotheses if isinstance(row, Mapping)):
        warnings.append(
            {
                "field": "phases.hypothesis_registry.hypotheses",
                "code": "missing_hypothesis_uncertainty_notes",
                "message": "Advisory admission expects every hypothesis to record uncertainty notes before claims are treated as method-ready.",
            }
        )

    adversarial_review = phases_by_id.get("adversarial_review", {})
    review = adversarial_review.get("review") if isinstance(adversarial_review.get("review"), Mapping) else {}
    challenge_questions = review.get("challenge_questions") if isinstance(review.get("challenge_questions"), list) else []
    if not challenge_questions:
        warnings.append(
            {
                "field": "phases.adversarial_review.review.challenge_questions",
                "code": "missing_adversarial_review_challenge_questions",
                "message": "Advisory admission expects explicit challenge questions so review posture is durable instead of implied.",
            }
        )

    outcome = phases_by_id.get("outcome_documentation", {})
    outcome_record = outcome.get("outcome_record") if isinstance(outcome.get("outcome_record"), Mapping) else {}
    if not str(outcome_record.get("uncertainty_notes") or "").strip():
        warnings.append(
            {
                "field": "phases.outcome_documentation.outcome_record.uncertainty_notes",
                "code": "missing_outcome_uncertainty_notes",
                "message": "Advisory admission expects outcome uncertainty notes so follow-on work can separate evidence from interpretation.",
            }
        )

    continuation = phases_by_id.get("continuation", {})
    continuation_notes = continuation.get("continuation_notes") if isinstance(continuation.get("continuation_notes"), Mapping) else {}
    stop_conditions = continuation_notes.get("stop_conditions") if isinstance(continuation_notes.get("stop_conditions"), list) else []
    if not stop_conditions:
        warnings.append(
            {
                "field": "phases.continuation.continuation_notes.stop_conditions",
                "code": "missing_continuation_stop_conditions",
                "message": "Advisory admission expects continuation stop conditions so successor work can tell progress from drift.",
            }
        )

    return {
        "admitted": True,
        "warnings": warnings,
        "warning_count": len(warnings),
        "normalized_template": normalized,
    }


def validate_scientific_campaign_method_template_pilot_fixtures(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_scientific_campaign_method_template_pilot_fixtures(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != "scientific_campaign_method_template_pilot_fixtures_v1":
        issues.append("pilot fixtures schema_version must remain scientific_campaign_method_template_pilot_fixtures_v1")
    fixture_rows = payload.get("fixtures")
    if not isinstance(fixture_rows, list) or not fixture_rows:
        issues.append("pilot fixtures must contain at least one fixture row")
        return tuple(issues)

    for index, row in enumerate(fixture_rows):
        if not isinstance(row, Mapping):
            issues.append(f"fixtures[{index}] must be an object")
            continue
        for field in PILOT_FIXTURE_REQUIRED_FIELDS:
            if row.get(field) in ("", None, [], {}):
                issues.append(f"fixtures[{index}].{field} is required")
        template = row.get("method_template")
        if not isinstance(template, Mapping):
            continue
        validation = validate_scientific_campaign_method_template(template)
        if not validation["ok"]:
            issues.append(f"fixtures[{index}].method_template must satisfy the hard method-template contract")
        advisory = assess_scientific_campaign_method_advisory_admission(template)
        if advisory["warning_count"] != 0:
            issues.append(f"fixtures[{index}].method_template should model a method-complete pilot without advisory gaps")

    return tuple(issues)


def validate_scientific_campaign_method_template_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_scientific_campaign_method_template_contract(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != CONTRACT_SCHEMA_VERSION:
        issues.append("schema_version must remain scientific_campaign_method_template_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260410-193415Z-2A95")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the scientific campaign method template contract doc")
    if str(payload.get("reference_template_rel") or "") != REFERENCE_TEMPLATE_REL.as_posix():
        issues.append("reference_template_rel must point at the canonical reference template")
    if str(payload.get("pilot_fixtures_rel") or "") != PILOT_FIXTURES_REL.as_posix():
        issues.append("pilot_fixtures_rel must point at the scientific campaign method pilot fixtures artifact")
    if tuple(payload.get("top_level_required_fields") or ()) != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("top_level_required_fields must list the canonical method template fields in order")
    if tuple(payload.get("phase_ids") or ()) != PHASE_IDS:
        issues.append("phase_ids must match the canonical method phase vocabulary")
    if tuple(payload.get("phase_required_fields") or ()) != PHASE_REQUIRED_FIELDS:
        issues.append("phase_required_fields must match the runtime helper contract")
    payload_phase_extension_fields = {str(key): tuple(value or ()) for key, value in dict(payload.get("phase_extension_required_fields") or {}).items()}
    if payload_phase_extension_fields != PHASE_EXTENSION_REQUIRED_FIELDS:
        issues.append("phase_extension_required_fields must match the runtime helper contract")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must match the runtime helper contract")
    if tuple(payload.get("advisory_method_posture_codes") or ()) != ADVISORY_METHOD_POSTURE_CODES:
        issues.append("advisory_method_posture_codes must match the runtime helper contract")

    reference = load_reference_scientific_campaign_method_template(repo_root=repo_root)
    reference_result = validate_scientific_campaign_method_template(reference)
    if not reference_result["ok"]:
        issues.append("reference scientific campaign method template must validate under the runtime helper contract")
    pilot_fixture_issues = validate_scientific_campaign_method_template_pilot_fixtures(repo_root=repo_root)
    issues.extend(pilot_fixture_issues)

    return tuple(issues)
