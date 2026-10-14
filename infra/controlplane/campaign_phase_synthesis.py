"""Deterministic intent-to-campaign phase synthesis helpers."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import budget_adaptive_research_policy
from infra.controlplane import campaign_plan_contract


REQUEST_SCHEMA_VERSION = "campaign_phase_synthesis_request_v1"
REPORT_SCHEMA_VERSION = "campaign_phase_synthesis_report_v1"
UID = "PIS-20260411-113559Z-8B17"

MISSION_SCOPE_VOCAB: Tuple[str, ...] = (
    "single_slice",
    "multi_stage_campaign",
    "open_ended_program",
)
RESOLUTION_TIMING_VOCAB: Tuple[str, ...] = (
    "allowed_in_campaign_plan",
    "must_resolve_before_pis",
)
STATUS_VOCAB: Tuple[str, ...] = (
    "compiled",
    "clarification_required",
    "blocked",
)
SEED_REQUIRED_FIELDS: Tuple[str, ...] = tuple(
    field for field in campaign_plan_contract.TOP_LEVEL_REQUIRED_FIELDS if field != "phases"
)
CLARIFICATION_REASON_CODES = frozenset(
    {
        "under_bounded_open_ended_plan",
        "missing_pre_pis_resolution_boundary",
        "missing_allowed_plan_ambiguity",
    }
)


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


def _normalize_seed(raw_seed: Any) -> Dict[str, Any]:
    seed = raw_seed if isinstance(raw_seed, Mapping) else {}
    normalized = campaign_plan_contract.normalize_campaign_plan(seed)
    return {field: normalized.get(field) for field in SEED_REQUIRED_FIELDS}


def _normalize_ambiguity(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "topic": _normalize_text(row.get("topic")),
        "resolution_timing": _normalize_text(row.get("resolution_timing")).lower(),
    }


def normalize_phase_synthesis_request(raw_request: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_request or {})
    ambiguities = [
        _normalize_ambiguity(row)
        for row in list(raw.get("ambiguities") or [])
        if isinstance(row, Mapping)
    ]
    return {
        "schema_version": _normalize_text(raw.get("schema_version") or REQUEST_SCHEMA_VERSION),
        "campaign_plan_seed": _normalize_seed(raw.get("campaign_plan_seed")),
        "mission_scope": _normalize_text(raw.get("mission_scope")).lower(),
        "intent_summary": _normalize_text(raw.get("intent_summary")),
        "operator_constraints": _normalize_text_list(raw.get("operator_constraints")),
        "required_outputs": _normalize_text_list(raw.get("required_outputs")),
        "decomposition_triggers": _normalize_text_list(raw.get("decomposition_triggers")),
        "review_gate_topics": _normalize_text_list(raw.get("review_gate_topics")),
        "requested_budget_mode": _normalize_text(raw.get("requested_budget_mode")).lower(),
        "ambiguities": [
            row
            for row in ambiguities
            if row["topic"] and row["resolution_timing"] in RESOLUTION_TIMING_VOCAB
        ],
    }


def _merge_unique_text(*values: Sequence[str]) -> List[str]:
    out: List[str] = []
    for rows in values:
        for raw in rows:
            text = _normalize_text(raw)
            if text and text not in out:
                out.append(text)
    return out


def _seed_missing_fields(seed: Mapping[str, Any]) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    for field in SEED_REQUIRED_FIELDS:
        value = seed.get(field)
        if value in ("", None, [], {}):
            errors.append(
                {
                    "field": f"campaign_plan_seed.{field}",
                    "code": "missing_field",
                    "message": f"campaign phase synthesis requires campaign_plan_seed.{field}",
                }
            )
    return errors


def _request_ambiguities_by_timing(request: Mapping[str, Any], timing: str) -> List[str]:
    return [
        row["topic"]
        for row in list(request.get("ambiguities") or [])
        if row.get("resolution_timing") == timing
    ]


def _budget_mode_for_request(request: Mapping[str, Any]) -> str:
    requested = _normalize_text(request.get("requested_budget_mode")).lower()
    if requested in budget_adaptive_research_policy.MODE_IDS:
        return requested
    mission_scope = _normalize_text(request.get("mission_scope")).lower()
    if mission_scope == "open_ended_program":
        return "campaign"
    if mission_scope == "single_slice":
        return "standard"
    return "campaign"


def _needs_multi_phase(request: Mapping[str, Any]) -> bool:
    if _normalize_text(request.get("mission_scope")).lower() in {"multi_stage_campaign", "open_ended_program"}:
        return True
    if len(list(request.get("decomposition_triggers") or [])) >= 2:
        return True
    return len(list(request.get("required_outputs") or [])) >= 4


def _build_phase(
    *,
    phase_id: str,
    label: str,
    goal: str,
    deliverables: Sequence[str],
    method_template_phase_ids: Sequence[str],
    budget_mode: str,
    allowed_ambiguities: Sequence[str],
    must_resolve_before_pis: Sequence[str],
    pis_readiness: str,
) -> Dict[str, Any]:
    return {
        "phase_id": phase_id,
        "label": label,
        "goal": goal,
        "deliverables": list(deliverables),
        "method_template_phase_ids": list(method_template_phase_ids),
        "budget_mode": budget_mode,
        "allowed_ambiguities": list(allowed_ambiguities),
        "must_resolve_before_pis": list(must_resolve_before_pis),
        "pis_readiness": pis_readiness,
    }


def _build_candidate_plan(request: Mapping[str, Any]) -> Dict[str, Any]:
    seed = dict(request["campaign_plan_seed"])
    allowed = _request_ambiguities_by_timing(request, "allowed_in_campaign_plan")
    must_resolve = _request_ambiguities_by_timing(request, "must_resolve_before_pis")
    budget_mode = _budget_mode_for_request(request)
    intent_summary = _normalize_text(request.get("intent_summary")) or "Compile broad intent into a phased campaign plan."

    ambiguity_policy = dict(seed.get("ambiguity_policy") or {})
    ambiguity_policy["allowed_in_campaign_plan"] = _merge_unique_text(
        ambiguity_policy.get("allowed_in_campaign_plan") or [],
        allowed,
    )
    ambiguity_policy["must_resolve_before_pis"] = _merge_unique_text(
        ambiguity_policy.get("must_resolve_before_pis") or [],
        must_resolve,
    )
    seed["ambiguity_policy"] = ambiguity_policy

    phase_1 = _build_phase(
        phase_id="phase_01_bound_scope_and_review",
        label="Bound scope and ambiguity review",
        goal=f"Define the first bounded campaign shape for: {intent_summary}",
        deliverables=[
            "campaign scope review gate",
            "constraint register",
            "ambiguity register",
        ],
        method_template_phase_ids=(
            "question_framing",
            "prior_knowledge_consolidation",
        ),
        budget_mode=budget_mode,
        allowed_ambiguities=allowed[:2],
        must_resolve_before_pis=[],
        pis_readiness="advisory_only",
    )
    phase_2 = _build_phase(
        phase_id="phase_02_design_and_decomposition_gate",
        label="Design decomposition review gate",
        goal="Convert the bounded mission into staged work without collapsing into one giant PIS.",
        deliverables=[
            "phase map",
            "decomposition trigger review gate",
            "policy-linked planning note",
        ],
        method_template_phase_ids=(
            "hypothesis_registry",
            "experiment_design",
            "analysis_plan",
        ),
        budget_mode=budget_mode,
        allowed_ambiguities=allowed[2:] if len(allowed) > 2 else allowed,
        must_resolve_before_pis=must_resolve,
        pis_readiness="advisory_only",
    )
    phase_3 = _build_phase(
        phase_id="phase_03_prepare_child_pis_handoff",
        label="Prepare bounded child PIS handoff",
        goal="Produce a PIS-ready handoff only after the review gates and resolution boundary are explicit.",
        deliverables=[
            "candidate child-PIS set",
            "phase-to-PIS mapping",
            "handoff readiness note",
        ],
        method_template_phase_ids=(
            "adversarial_review",
            "continuation",
        ),
        budget_mode="standard",
        allowed_ambiguities=[],
        must_resolve_before_pis=[],
        pis_readiness="ready_for_pis_generation",
    )

    seed["phases"] = [phase_1, phase_2, phase_3]
    return seed


def _phase_surface_contains(plan: Mapping[str, Any], topic: str, *, field: str) -> bool:
    normalized_topic = _normalize_text(topic)
    if not normalized_topic:
        return False
    if normalized_topic in list(plan.get("ambiguity_policy", {}).get(field) or []):
        return True
    for phase in list(plan.get("phases") or []):
        if normalized_topic in list(phase.get(field) or []):
            return True
    return False


def validate_phase_synthesis_candidate(
    raw_request: Mapping[str, Any],
    candidate_plan: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    request = normalize_phase_synthesis_request(raw_request)
    contract_result = campaign_plan_contract.validate_campaign_plan(candidate_plan, repo_root=repo_root)
    errors = list(contract_result["errors"])
    warnings = list(contract_result["warnings"])
    normalized_plan = contract_result["normalized_plan"]

    if _needs_multi_phase(request) and len(list(normalized_plan.get("phases") or [])) < 2:
        errors.append(
            {
                "field": "phases",
                "code": "single_phase_collapse_forbidden",
                "message": "broad intent that needs decomposition may not collapse into one giant campaign phase",
            }
        )

    if request["mission_scope"] == "open_ended_program" and (
        len(request["operator_constraints"]) < 2 or not request["decomposition_triggers"]
    ):
        errors.append(
            {
                "field": "operator_constraints",
                "code": "under_bounded_open_ended_plan",
                "message": "open-ended program intent must name at least two constraints and one decomposition trigger",
            }
        )

    if not any(
        "review" in _normalize_text(phase.get("label")).lower()
        or any("review gate" in _normalize_text(item).lower() for item in list(phase.get("deliverables") or []))
        for phase in list(normalized_plan.get("phases") or [])
    ):
        errors.append(
            {
                "field": "phases",
                "code": "missing_review_gate",
                "message": "phase synthesis must surface at least one explicit review gate",
            }
        )

    for topic in _request_ambiguities_by_timing(request, "allowed_in_campaign_plan"):
        if not _phase_surface_contains(normalized_plan, topic, field="allowed_ambiguities") and not _phase_surface_contains(
            normalized_plan,
            topic,
            field="allowed_in_campaign_plan",
        ):
            errors.append(
                {
                    "field": "ambiguity_policy.allowed_in_campaign_plan",
                    "code": "ambiguity_erased_too_early",
                    "message": "allowed ambiguity from the intent must remain visible in the campaign plan",
                }
            )

    for topic in _request_ambiguities_by_timing(request, "must_resolve_before_pis"):
        if not _phase_surface_contains(normalized_plan, topic, field="must_resolve_before_pis"):
            errors.append(
                {
                    "field": "ambiguity_policy.must_resolve_before_pis",
                    "code": "missing_pre_pis_resolution_boundary",
                    "message": "pre-PIS resolution requirements must remain explicit in the campaign plan",
                }
            )

    final_phase = list(normalized_plan.get("phases") or [])[-1] if normalized_plan.get("phases") else {}
    if _normalize_text(final_phase.get("pis_readiness")) != "ready_for_pis_generation":
        errors.append(
            {
                "field": "phases[-1].pis_readiness",
                "code": "missing_pis_ready_terminal_phase",
                "message": "phase synthesis must terminate in a PIS-ready handoff phase",
            }
        )

    reason_codes = tuple(dict.fromkeys(row["code"] for row in errors))
    status = "compiled" if not errors else "clarification_required" if set(reason_codes).issubset(CLARIFICATION_REASON_CODES) else "blocked"
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "uid": UID,
        "ok": not errors,
        "status": status,
        "errors": errors,
        "warnings": warnings,
        "reason_codes": list(reason_codes),
        "normalized_request": request,
        "normalized_plan": normalized_plan,
        "computed_fingerprint": contract_result["computed_fingerprint"],
    }


def compile_campaign_phase_plan(
    raw_request: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    request = normalize_phase_synthesis_request(raw_request)
    errors = _seed_missing_fields(request["campaign_plan_seed"])
    phase_generation_trace: List[str] = []

    if request["schema_version"] != REQUEST_SCHEMA_VERSION:
        errors.append(
            {
                "field": "schema_version",
                "code": "invalid_value",
                "message": f"phase synthesis request schema_version must remain {REQUEST_SCHEMA_VERSION}",
            }
        )
    if request["mission_scope"] not in MISSION_SCOPE_VOCAB:
        errors.append(
            {
                "field": "mission_scope",
                "code": "invalid_value",
                "message": "phase synthesis requires a closed mission_scope vocabulary",
            }
        )
    if not request["intent_summary"]:
        errors.append(
            {
                "field": "intent_summary",
                "code": "missing_field",
                "message": "phase synthesis requires an intent_summary",
            }
        )

    allowed_topics = _request_ambiguities_by_timing(request, "allowed_in_campaign_plan")
    must_resolve_topics = _request_ambiguities_by_timing(request, "must_resolve_before_pis")
    if not allowed_topics:
        errors.append(
            {
                "field": "ambiguities",
                "code": "missing_allowed_plan_ambiguity",
                "message": "phase synthesis requires at least one explicitly allowed ambiguity to preserve plan-level flexibility",
            }
        )
    if not must_resolve_topics:
        errors.append(
            {
                "field": "ambiguities",
                "code": "missing_pre_pis_resolution_boundary",
                "message": "phase synthesis requires at least one named pre-PIS resolution requirement",
            }
        )

    if request["mission_scope"] == "open_ended_program" and (
        len(request["operator_constraints"]) < 2 or not request["decomposition_triggers"]
    ):
        errors.append(
            {
                "field": "operator_constraints",
                "code": "under_bounded_open_ended_plan",
                "message": "open-ended program intent needs at least two constraints and one decomposition trigger before a phase graph is honest",
            }
        )

    if errors:
        reason_codes = list(dict.fromkeys(row["code"] for row in errors))
        status = "clarification_required" if set(reason_codes).issubset(CLARIFICATION_REASON_CODES) else "blocked"
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "uid": UID,
            "ok": False,
            "status": status,
            "errors": errors,
            "warnings": [],
            "reason_codes": reason_codes,
            "normalized_request": request,
            "campaign_plan": None,
            "computed_fingerprint": "",
            "phase_generation_trace": phase_generation_trace,
        }

    phase_generation_trace.append("validated_request_and_campaign_plan_seed")
    if _needs_multi_phase(request):
        phase_generation_trace.append("selected_multi_phase_path")
    else:
        phase_generation_trace.append("selected_minimum_two_phase_path")
    phase_generation_trace.append("attached_explicit_review_gate_and_ambiguity_policy")

    candidate_plan = _build_candidate_plan(request)
    validation = validate_phase_synthesis_candidate(request, candidate_plan, repo_root=repo_root)
    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "uid": UID,
        "ok": validation["ok"],
        "status": validation["status"],
        "errors": validation["errors"],
        "warnings": validation["warnings"],
        "reason_codes": validation["reason_codes"],
        "normalized_request": validation["normalized_request"],
        "campaign_plan": validation["normalized_plan"] if validation["ok"] else None,
        "computed_fingerprint": validation["computed_fingerprint"] if validation["ok"] else "",
        "phase_generation_trace": phase_generation_trace,
    }
