"""Canonical budget-adaptive research policy v1 helpers."""

from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import evidence_pack_handoff_refs
from infra.controlplane import scientific_campaign_method_template


SCHEMA_VERSION = "budget_adaptive_research_policy_v1"
CONTRACT_SCHEMA_VERSION = "budget_adaptive_research_policy_contract_v1"
REFERENCE_FIXTURES_SCHEMA_VERSION = "budget_adaptive_research_policy_reference_examples_v1"
CONTRACT_ARTIFACT_REL = Path("governance/budget_adaptive_research_policy_v1.json")
REFERENCE_FIXTURES_REL = Path("governance/budget_adaptive_research_policy_reference_examples_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_budget_adaptive_research_policy_v1.md")
UID = "PIS-20260411-081500Z-A4D1"

TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "policy_id",
    "policy_version",
    "stable_identity",
    "metadata",
    "ownership_boundary",
    "canonical_mode_order",
    "modes",
    "scaling_law",
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
    "belongs_in_policy",
    "outside_policy",
)
MODE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "mode_id",
    "label",
    "summary",
    "minimum_deliverables",
    "literature_breadth",
    "depth_expectation",
    "hypothesis_count_floor",
    "hypothesis_count_ceiling",
    "hypothesis_posture",
    "verification_strictness_floor",
)
SCALING_LAW_REQUIRED_FIELDS: Tuple[str, ...] = (
    "fixed_method_template_phase_ids",
    "breadth_progression",
    "depth_progression",
    "hypothesis_progression",
    "verification_progression",
    "invariants",
)
FIXTURE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "fixture_id",
    "selection_posture",
    "selected_mode",
    "request_summary",
    "proposed_deliverables",
    "literature_breadth",
    "hypothesis_count",
    "verification_strictness",
    "expected_validation_outcome",
)
MODE_IDS: Tuple[str, ...] = ("micro", "standard", "overnight", "campaign", "expedition")
BREADTH_LEVELS: Tuple[str, ...] = ("targeted", "scoped", "broad", "portfolio", "program_wide")
DEPTH_LEVELS: Tuple[str, ...] = ("single_pass", "two_pass", "multi_pass", "campaign_stage", "program_scale")
VERIFICATION_LEVELS: Tuple[str, ...] = (
    "sanity_check",
    "cross_source_check",
    "targeted_adversarial_check",
    "adversarial_gate",
    "multi_stage_adversarial",
)
FIXTURE_SELECTION_POSTURES: Tuple[str, ...] = ("compliant", "under_scoped", "over_scoped")
EXPECTED_FIXTURE_OUTCOMES: Tuple[str, ...] = ("accepted", "rejected")
FORBIDDEN_TOP_LEVEL_FIELDS: Tuple[str, ...] = (
    "runtime_budget_ledger",
    "campaign_plan_compiler",
    "scheduler_gate",
    "operator_ui_override",
)
FAIL_CLOSED_RULES: Tuple[str, ...] = (
    "all_first_class_modes_must_be_defined_exactly_once",
    "deliverable_floors_must_be_present_for_every_mode",
    "mode_scaling_law_must_not_change_method_template_phase_ids",
    "fixture_examples_must_distinguish_under_scoped_and_over_scoped_allocations",
    "budget_ledgers_compilers_scheduler_gates_and_ui_overrides_forbidden_inline",
)

PLANNING_CONTINUATION_DECISIONS: Tuple[str, ...] = ("stop", "continue")
PLANNING_CONVERGENCE_POSTURES: Tuple[str, ...] = (
    "converge_immediately",
    "bounded_convergence",
    "broaden_with_pruning",
    "evidence_gated_broadening",
    "portfolio_breadth",
)
MODE_WAVE_CAPS: Mapping[str, int] = {
    "micro": 1,
    "standard": 2,
    "overnight": 3,
    "campaign": 4,
    "expedition": 5,
}
MODE_CONVERGENCE_POSTURES: Mapping[str, str] = {
    "micro": "converge_immediately",
    "standard": "bounded_convergence",
    "overnight": "broaden_with_pruning",
    "campaign": "evidence_gated_broadening",
    "expedition": "portfolio_breadth",
}
STOP_EVIDENCE_REQUIRED_MODES = frozenset({"campaign", "expedition"})
POLICY_REASON_CODES: Tuple[str, ...] = (
    "unknown_budget_adaptive_mode",
    "invalid_requested_builder_waves",
    "invalid_candidate_continuation_decision",
    "over_wave_cap",
    "missing_continuation_rationale",
    "proof_blind_continuation",
    "premature_convergence",
    "invalid_evidence_pack_ref",
)

_BREADTH_RANK = {value: index for index, value in enumerate(BREADTH_LEVELS)}
_VERIFICATION_RANK = {value: index for index, value in enumerate(VERIFICATION_LEVELS)}


@dataclass(frozen=True)
class BudgetAdaptiveResearchPolicyContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    reference_fixtures_rel: Path
    canonical_doc_rel: Path
    top_level_required_fields: Tuple[str, ...]
    mode_ids: Tuple[str, ...]
    fail_closed_rules: Tuple[str, ...]


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
    return parsed if parsed >= 0 else 0


def _normalize_bool(value: Any, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = _normalize_text(value).lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return default


def _append_missing_errors(errors: List[Dict[str, str]], prefix: str, row: Mapping[str, Any], fields: Sequence[str]) -> None:
    for field in fields:
        value = row.get(field)
        if value in ("", None, [], {}):
            errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def budget_adaptive_research_policy_contract_v1() -> BudgetAdaptiveResearchPolicyContractV1:
    return BudgetAdaptiveResearchPolicyContractV1(
        version=CONTRACT_SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        reference_fixtures_rel=REFERENCE_FIXTURES_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
        mode_ids=MODE_IDS,
        fail_closed_rules=FAIL_CLOSED_RULES,
    )


def load_budget_adaptive_research_policy_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    path = root / CONTRACT_ARTIFACT_REL
    if not path.exists() and repo_root is not None:
        path = _repo_root() / CONTRACT_ARTIFACT_REL
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_budget_adaptive_research_policy_reference_examples(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / REFERENCE_FIXTURES_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def normalize_budget_adaptive_research_policy(raw_policy: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_policy or {})
    stable_identity = raw.get("stable_identity") if isinstance(raw.get("stable_identity"), Mapping) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), Mapping) else {}
    ownership_boundary = raw.get("ownership_boundary") if isinstance(raw.get("ownership_boundary"), Mapping) else {}
    scaling_law = raw.get("scaling_law") if isinstance(raw.get("scaling_law"), Mapping) else {}

    modes: List[Dict[str, Any]] = []
    for row in raw.get("modes") if isinstance(raw.get("modes"), list) else []:
        if not isinstance(row, Mapping):
            continue
        modes.append(
            {
                "mode_id": _normalize_text(row.get("mode_id")),
                "label": _normalize_text(row.get("label")),
                "summary": _normalize_text(row.get("summary")),
                "minimum_deliverables": _normalize_text_list(row.get("minimum_deliverables")),
                "literature_breadth": _normalize_text(row.get("literature_breadth")),
                "depth_expectation": _normalize_text(row.get("depth_expectation")),
                "hypothesis_count_floor": _normalize_positive_int(row.get("hypothesis_count_floor")),
                "hypothesis_count_ceiling": _normalize_positive_int(row.get("hypothesis_count_ceiling")),
                "hypothesis_posture": _normalize_text(row.get("hypothesis_posture")),
                "verification_strictness_floor": _normalize_text(row.get("verification_strictness_floor")),
            }
        )
    modes.sort(key=lambda row: (MODE_IDS.index(row["mode_id"]) if row["mode_id"] in MODE_IDS else len(MODE_IDS), row["mode_id"]))

    return {
        "schema_version": _normalize_text(raw.get("schema_version") or SCHEMA_VERSION),
        "policy_id": _normalize_text(raw.get("policy_id")),
        "policy_version": _normalize_text(raw.get("policy_version")),
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
            "belongs_in_policy": _normalize_text_list(ownership_boundary.get("belongs_in_policy")),
            "outside_policy": _normalize_text_list(ownership_boundary.get("outside_policy")),
        },
        "canonical_mode_order": _normalize_text_list(raw.get("canonical_mode_order")),
        "modes": modes,
        "scaling_law": {
            "fixed_method_template_phase_ids": _normalize_text_list(scaling_law.get("fixed_method_template_phase_ids")),
            "breadth_progression": {str(key): _normalize_text(value) for key, value in dict(scaling_law.get("breadth_progression") or {}).items()},
            "depth_progression": {str(key): _normalize_text(value) for key, value in dict(scaling_law.get("depth_progression") or {}).items()},
            "hypothesis_progression": {str(key): _normalize_text(value) for key, value in dict(scaling_law.get("hypothesis_progression") or {}).items()},
            "verification_progression": {str(key): _normalize_text(value) for key, value in dict(scaling_law.get("verification_progression") or {}).items()},
            "invariants": _normalize_text_list(scaling_law.get("invariants")),
        },
    }


def validate_budget_adaptive_research_policy(raw_policy: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_policy or {})
    normalized = normalize_budget_adaptive_research_policy(raw)
    errors: List[Dict[str, str]] = []

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", [], {}) or value is None:
            errors.append({"field": field, "code": "missing_field", "message": f"policy requires {field}"})

    if normalized["schema_version"] not in (SCHEMA_VERSION, CONTRACT_SCHEMA_VERSION):
        errors.append(
            {
                "field": "schema_version",
                "code": "invalid_value",
                "message": f"schema_version must be {SCHEMA_VERSION} or {CONTRACT_SCHEMA_VERSION}",
            }
        )

    _append_missing_errors(errors, "stable_identity", normalized["stable_identity"], STABLE_IDENTITY_REQUIRED_FIELDS)
    _append_missing_errors(errors, "metadata", normalized["metadata"], METADATA_REQUIRED_FIELDS)
    _append_missing_errors(errors, "ownership_boundary", normalized["ownership_boundary"], OWNERSHIP_BOUNDARY_REQUIRED_FIELDS)
    _append_missing_errors(errors, "scaling_law", normalized["scaling_law"], SCALING_LAW_REQUIRED_FIELDS)

    for forbidden in FORBIDDEN_TOP_LEVEL_FIELDS:
        if raw.get(forbidden) not in (None, "", [], {}, ()):
            errors.append(
                {
                    "field": forbidden,
                    "code": "forbidden_inline_ownership",
                    "message": "budget-adaptive policy defines contract law, not runtime ledger, compiler, scheduler, or UI ownership",
                }
            )

    if tuple(normalized["canonical_mode_order"]) != MODE_IDS:
        errors.append(
            {
                "field": "canonical_mode_order",
                "code": "invalid_mode_order",
                "message": "canonical_mode_order must list micro, standard, overnight, campaign, expedition in order",
            }
        )

    mode_ids_seen: List[str] = []
    scaling_law = normalized["scaling_law"]
    for index, row in enumerate(normalized["modes"]):
        prefix = f"modes[{index}]"
        _append_missing_errors(errors, prefix, row, MODE_REQUIRED_FIELDS)
        mode_id = row["mode_id"]
        if mode_id in mode_ids_seen:
            errors.append({"field": f"{prefix}.mode_id", "code": "duplicate_mode_id", "message": "mode_id values must be unique"})
        else:
            mode_ids_seen.append(mode_id)
        if mode_id and mode_id not in MODE_IDS:
            errors.append({"field": f"{prefix}.mode_id", "code": "unknown_mode_id", "message": "mode_id must use the canonical research mode vocabulary"})
        if not row["minimum_deliverables"]:
            errors.append(
                {
                    "field": f"{prefix}.minimum_deliverables",
                    "code": "missing_deliverable_floor",
                    "message": "every research mode must declare at least one minimum deliverable",
                }
            )
        if row["literature_breadth"] and row["literature_breadth"] not in BREADTH_LEVELS:
            errors.append({"field": f"{prefix}.literature_breadth", "code": "invalid_value", "message": "literature_breadth must use the canonical progression vocabulary"})
        if row["depth_expectation"] and row["depth_expectation"] not in DEPTH_LEVELS:
            errors.append({"field": f"{prefix}.depth_expectation", "code": "invalid_value", "message": "depth_expectation must use the canonical progression vocabulary"})
        if row["verification_strictness_floor"] and row["verification_strictness_floor"] not in VERIFICATION_LEVELS:
            errors.append(
                {
                    "field": f"{prefix}.verification_strictness_floor",
                    "code": "invalid_value",
                    "message": "verification_strictness_floor must use the canonical progression vocabulary",
                }
            )
        if row["hypothesis_count_floor"] == 0:
            errors.append({"field": f"{prefix}.hypothesis_count_floor", "code": "invalid_value", "message": "hypothesis_count_floor must be positive"})
        if row["hypothesis_count_ceiling"] == 0:
            errors.append({"field": f"{prefix}.hypothesis_count_ceiling", "code": "invalid_value", "message": "hypothesis_count_ceiling must be positive"})
        if row["hypothesis_count_floor"] > row["hypothesis_count_ceiling"]:
            errors.append(
                {
                    "field": f"{prefix}.hypothesis_count_ceiling",
                    "code": "invalid_range",
                    "message": "hypothesis_count_ceiling must be greater than or equal to hypothesis_count_floor",
                }
            )
        if mode_id in MODE_IDS:
            if scaling_law["breadth_progression"].get(mode_id) != row["literature_breadth"]:
                errors.append({"field": f"{prefix}.literature_breadth", "code": "scaling_law_mismatch", "message": "mode breadth must match scaling_law.breadth_progression"})
            if scaling_law["depth_progression"].get(mode_id) != row["depth_expectation"]:
                errors.append({"field": f"{prefix}.depth_expectation", "code": "scaling_law_mismatch", "message": "mode depth must match scaling_law.depth_progression"})
            if scaling_law["hypothesis_progression"].get(mode_id) != row["hypothesis_posture"]:
                errors.append({"field": f"{prefix}.hypothesis_posture", "code": "scaling_law_mismatch", "message": "mode hypothesis posture must match scaling_law.hypothesis_progression"})
            if scaling_law["verification_progression"].get(mode_id) != row["verification_strictness_floor"]:
                errors.append(
                    {
                        "field": f"{prefix}.verification_strictness_floor",
                        "code": "scaling_law_mismatch",
                        "message": "mode verification floor must match scaling_law.verification_progression",
                    }
                )

    for mode_id in MODE_IDS:
        if mode_id not in mode_ids_seen:
            errors.append({"field": f"modes:{mode_id}", "code": "missing_mode", "message": f"missing canonical research mode {mode_id}"})

    expected_phase_ids = tuple(scientific_campaign_method_template.PHASE_IDS)
    if tuple(scaling_law["fixed_method_template_phase_ids"]) != expected_phase_ids:
        errors.append(
            {
                "field": "scaling_law.fixed_method_template_phase_ids",
                "code": "method_template_phase_drift",
                "message": "budget adaptation must preserve the canonical scientific campaign method-template phase nouns",
            }
        )

    return {
        "ok": not errors,
        "errors": errors,
        "normalized_policy": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def normalize_reference_fixture(raw_fixture: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(raw_fixture or {})
    return {
        "fixture_id": _normalize_text(row.get("fixture_id")),
        "selection_posture": _normalize_text(row.get("selection_posture")),
        "selected_mode": _normalize_text(row.get("selected_mode")),
        "request_summary": _normalize_text(row.get("request_summary")),
        "proposed_deliverables": _normalize_text_list(row.get("proposed_deliverables")),
        "literature_breadth": _normalize_text(row.get("literature_breadth")),
        "hypothesis_count": _normalize_positive_int(row.get("hypothesis_count")),
        "verification_strictness": _normalize_text(row.get("verification_strictness")),
        "expected_validation_outcome": _normalize_text(row.get("expected_validation_outcome")),
    }


def validate_reference_fixture(raw_fixture: Mapping[str, Any], *, policy: Mapping[str, Any]) -> Dict[str, Any]:
    fixture = normalize_reference_fixture(raw_fixture)
    normalized_policy = normalize_budget_adaptive_research_policy(policy)
    modes = {row["mode_id"]: row for row in normalized_policy["modes"]}
    errors: List[Dict[str, str]] = []

    _append_missing_errors(errors, "fixture", fixture, FIXTURE_REQUIRED_FIELDS)

    if fixture["selection_posture"] and fixture["selection_posture"] not in FIXTURE_SELECTION_POSTURES:
        errors.append({"field": "fixture.selection_posture", "code": "invalid_value", "message": "selection_posture must be compliant, under_scoped, or over_scoped"})
    if fixture["expected_validation_outcome"] and fixture["expected_validation_outcome"] not in EXPECTED_FIXTURE_OUTCOMES:
        errors.append({"field": "fixture.expected_validation_outcome", "code": "invalid_value", "message": "expected_validation_outcome must be accepted or rejected"})

    mode = modes.get(fixture["selected_mode"])
    if mode is None:
        errors.append({"field": "fixture.selected_mode", "code": "unknown_mode_id", "message": "selected_mode must name a canonical research mode"})
    else:
        missing_deliverables = [row for row in mode["minimum_deliverables"] if row not in fixture["proposed_deliverables"]]
        if missing_deliverables:
            errors.append(
                {
                    "field": "fixture.proposed_deliverables",
                    "code": "missing_deliverable_floor",
                    "message": f"fixture is missing mode deliverable floors: {', '.join(missing_deliverables)}",
                }
            )

        if fixture["literature_breadth"] not in _BREADTH_RANK:
            errors.append({"field": "fixture.literature_breadth", "code": "invalid_value", "message": "fixture literature_breadth must use the canonical progression vocabulary"})
        else:
            expected_rank = _BREADTH_RANK[mode["literature_breadth"]]
            actual_rank = _BREADTH_RANK[fixture["literature_breadth"]]
            if actual_rank < expected_rank:
                errors.append({"field": "fixture.literature_breadth", "code": "under_scoped_literature_breadth", "message": "fixture breadth is thinner than the selected mode floor"})
            if actual_rank > expected_rank:
                errors.append({"field": "fixture.literature_breadth", "code": "over_scoped_literature_breadth", "message": "fixture breadth exceeds the selected mode boundary"})

        if fixture["hypothesis_count"] < mode["hypothesis_count_floor"]:
            errors.append({"field": "fixture.hypothesis_count", "code": "under_scoped_hypothesis_count", "message": "fixture hypothesis count is below the selected mode floor"})
        if fixture["hypothesis_count"] > mode["hypothesis_count_ceiling"]:
            errors.append({"field": "fixture.hypothesis_count", "code": "over_scoped_hypothesis_count", "message": "fixture hypothesis count exceeds the selected mode boundary"})

        if fixture["verification_strictness"] not in _VERIFICATION_RANK:
            errors.append({"field": "fixture.verification_strictness", "code": "invalid_value", "message": "fixture verification_strictness must use the canonical progression vocabulary"})
        else:
            expected_rank = _VERIFICATION_RANK[mode["verification_strictness_floor"]]
            actual_rank = _VERIFICATION_RANK[fixture["verification_strictness"]]
            if actual_rank < expected_rank:
                errors.append({"field": "fixture.verification_strictness", "code": "under_scoped_verification_strictness", "message": "fixture verification is below the selected mode floor"})
            if actual_rank > expected_rank and fixture["selected_mode"] != "expedition":
                errors.append({"field": "fixture.verification_strictness", "code": "over_scoped_verification_strictness", "message": "fixture verification exceeds the selected mode boundary"})

    actual_outcome = "accepted" if not errors else "rejected"
    if fixture["expected_validation_outcome"] and fixture["expected_validation_outcome"] != actual_outcome:
        errors.append(
            {
                "field": "fixture.expected_validation_outcome",
                "code": "expected_outcome_mismatch",
                "message": f"fixture expected {fixture['expected_validation_outcome']} but validated as {actual_outcome}",
            }
        )

    return {"ok": not errors, "errors": errors, "normalized_fixture": fixture}


def validate_budget_adaptive_research_policy_reference_examples(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    policy = load_budget_adaptive_research_policy_contract(repo_root=repo_root)
    payload = load_budget_adaptive_research_policy_reference_examples(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != REFERENCE_FIXTURES_SCHEMA_VERSION:
        issues.append("schema_version must remain budget_adaptive_research_policy_reference_examples_v1")
    if str(payload.get("policy_uid") or "") != UID:
        issues.append("policy_uid must remain bound to PIS-20260411-081500Z-A4D1")
    fixtures = payload.get("fixtures")
    if not isinstance(fixtures, list) or not fixtures:
        issues.append("fixtures must contain at least one reference example")
        return tuple(issues)

    for index, row in enumerate(fixtures):
        if not isinstance(row, Mapping):
            issues.append(f"fixtures[{index}] must be an object")
            continue
        result = validate_reference_fixture(row, policy=policy)
        tolerated_codes = {
            "missing_deliverable_floor",
            "under_scoped_literature_breadth",
            "over_scoped_literature_breadth",
            "under_scoped_hypothesis_count",
            "over_scoped_hypothesis_count",
            "under_scoped_verification_strictness",
            "over_scoped_verification_strictness",
        }
        for entry in result["errors"]:
            if entry["code"] not in tolerated_codes:
                issues.append(f"fixtures[{index}]: {entry['code']}")

    return tuple(issues)


def validate_budget_adaptive_research_policy_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_budget_adaptive_research_policy_contract(repo_root=repo_root)
    issues: List[str] = []

    if str(payload.get("schema_version") or "") != CONTRACT_SCHEMA_VERSION:
        issues.append("schema_version must remain budget_adaptive_research_policy_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260411-081500Z-A4D1")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the budget-adaptive research policy contract doc")
    if str(payload.get("reference_fixtures_rel") or "") != REFERENCE_FIXTURES_REL.as_posix():
        issues.append("reference_fixtures_rel must point at the budget-adaptive research policy reference examples")
    if tuple(payload.get("top_level_required_fields") or ()) != TOP_LEVEL_REQUIRED_FIELDS:
        issues.append("top_level_required_fields must list the canonical policy fields in order")
    if tuple(payload.get("canonical_mode_order") or ()) != MODE_IDS:
        issues.append("canonical_mode_order must list the canonical research modes in order")
    if tuple(payload.get("fail_closed_rules") or ()) != FAIL_CLOSED_RULES:
        issues.append("fail_closed_rules must match the runtime helper contract")

    validation = validate_budget_adaptive_research_policy(payload)
    if not validation["ok"]:
        issues.append("reference budget-adaptive research policy artifact must validate under the runtime helper contract")
    issues.extend(validate_budget_adaptive_research_policy_reference_examples(repo_root=repo_root))
    return tuple(issues)


def validate_policy_fixture_without_mutation(raw_fixture: Mapping[str, Any], *, policy: Mapping[str, Any]) -> Dict[str, Any]:
    fixture_copy = copy.deepcopy(raw_fixture)
    result = validate_reference_fixture(fixture_copy, policy=policy)
    return result


def normalize_budget_adaptive_planning_guidance(raw_guidance: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(raw_guidance or {})
    return {
        "selected_mode": _normalize_text(row.get("selected_mode")),
        "requested_builder_waves": _normalize_positive_int(row.get("requested_builder_waves")),
        "candidate_continuation_decision": _normalize_text(row.get("candidate_continuation_decision")).lower(),
        "continuation_rationale": _normalize_text(row.get("continuation_rationale")),
        "continuation_packet_id": _normalize_text(row.get("continuation_packet_id")),
        "evidence_pack_refs": evidence_pack_handoff_refs.normalize_evidence_pack_refs(row.get("evidence_pack_refs")),
        "advisory_only": _normalize_bool(row.get("advisory_only"), default=True),
    }


def normalize_budget_adaptive_planning_receipt(raw_receipt: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(raw_receipt or {})
    return {
        "policy_uid": _normalize_text(row.get("policy_uid")),
        "policy_version": _normalize_text(row.get("policy_version")),
        "selected_mode": _normalize_text(row.get("selected_mode")),
        "convergence_posture": _normalize_text(row.get("convergence_posture")),
        "wave_cap": _normalize_positive_int(row.get("wave_cap")),
        "requested_builder_waves": _normalize_positive_int(row.get("requested_builder_waves")),
        "candidate_continuation_decision": _normalize_text(row.get("candidate_continuation_decision")).lower(),
        "effective_continuation_decision": _normalize_text(row.get("effective_continuation_decision")).lower(),
        "continuation_rationale": _normalize_text(row.get("continuation_rationale")),
        "policy_state": _normalize_text(row.get("policy_state")).lower(),
        "advisory_only": _normalize_bool(row.get("advisory_only"), default=True),
        "reason_codes": [code for code in _normalize_text_list(row.get("reason_codes")) if code in POLICY_REASON_CODES],
        "has_durable_proof": _normalize_bool(row.get("has_durable_proof"), default=False),
        "continuation_packet_id": _normalize_text(row.get("continuation_packet_id")),
        "evidence_pack_refs": evidence_pack_handoff_refs.normalize_evidence_pack_refs(row.get("evidence_pack_refs")),
        "minimum_deliverables": _normalize_text_list(row.get("minimum_deliverables")),
    }


def evaluate_budget_adaptive_planning_guidance(
    raw_guidance: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    policy: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized = normalize_budget_adaptive_planning_guidance(raw_guidance)
    if not any(
        (
            normalized["selected_mode"],
            normalized["requested_builder_waves"],
            normalized["candidate_continuation_decision"],
            normalized["continuation_rationale"],
            normalized["continuation_packet_id"],
            normalized["evidence_pack_refs"],
        )
    ):
        return {
            "ok": True,
            "errors": [],
            "warnings": [],
            "normalized_guidance": normalized,
            "receipt": {
                "policy_uid": UID,
                "policy_version": load_budget_adaptive_research_policy_contract(repo_root=repo_root)["policy_version"],
                "selected_mode": "",
                "convergence_posture": "",
                "wave_cap": 0,
                "requested_builder_waves": 0,
                "candidate_continuation_decision": "",
                "effective_continuation_decision": "",
                "continuation_rationale": "",
                "policy_state": "not_configured",
                "advisory_only": normalized["advisory_only"],
                "reason_codes": [],
                "has_durable_proof": False,
                "continuation_packet_id": "",
                "evidence_pack_refs": [],
                "minimum_deliverables": [],
            },
        }
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    selected_mode = normalized["selected_mode"]
    candidate_decision = normalized["candidate_continuation_decision"]
    requested_builder_waves = normalized["requested_builder_waves"]
    continuation_packet_id = normalized["continuation_packet_id"]
    continuation_rationale = normalized["continuation_rationale"]
    evidence_pack_refs = normalized["evidence_pack_refs"]
    has_durable_proof = bool(evidence_pack_refs or continuation_packet_id)
    normalized_policy = normalize_budget_adaptive_research_policy(policy or load_budget_adaptive_research_policy_contract(repo_root=repo_root))

    if selected_mode not in MODE_IDS:
        errors.append(
            {
                "field": "selected_mode",
                "code": "unknown_budget_adaptive_mode",
                "message": "selected_mode must name one of the canonical research modes",
            }
        )
    if requested_builder_waves <= 0:
        errors.append(
            {
                "field": "requested_builder_waves",
                "code": "invalid_requested_builder_waves",
                "message": "requested_builder_waves must be a positive integer",
            }
        )
    if candidate_decision not in PLANNING_CONTINUATION_DECISIONS:
        errors.append(
            {
                "field": "candidate_continuation_decision",
                "code": "invalid_candidate_continuation_decision",
                "message": "candidate_continuation_decision must be stop or continue",
            }
        )

    for row in evidence_pack_handoff_refs.validate_evidence_pack_refs(
        evidence_pack_refs,
        repo_root=repo_root,
        field_prefix="budget_adaptive_guidance.evidence_pack_refs",
    ):
        code = row["code"]
        errors.append(
            {
                "field": row["field"],
                "code": "invalid_evidence_pack_ref",
                "message": row["message"],
            }
        )
        if code == "fingerprint_mismatch":
            warnings.append(
                {
                    "field": row["field"],
                    "code": "invalid_evidence_pack_ref",
                    "message": row["message"],
                }
            )

    wave_cap = MODE_WAVE_CAPS.get(selected_mode, 0)
    convergence_posture = MODE_CONVERGENCE_POSTURES.get(selected_mode, "")
    reason_codes: List[str] = []
    effective_decision = candidate_decision

    def _record(field: str, code: str, message: str) -> None:
        errors.append({"field": field, "code": code, "message": message})
        if code not in reason_codes:
            reason_codes.append(code)

    if wave_cap and requested_builder_waves > wave_cap:
        effective_decision = "stop"
        _record(
            "requested_builder_waves",
            "over_wave_cap",
            f"mode {selected_mode} allows at most {wave_cap} builder waves",
        )

    if candidate_decision == "continue" and not continuation_rationale:
        effective_decision = "stop"
        _record(
            "continuation_rationale",
            "missing_continuation_rationale",
            "continue recommendations require an explicit continuation rationale",
        )

    if candidate_decision == "continue" and not has_durable_proof:
        effective_decision = "stop"
        _record(
            "candidate_continuation_decision",
            "proof_blind_continuation",
            "continue recommendations require a linked evidence pack ref or continuation packet id",
        )

    if candidate_decision == "stop" and selected_mode in STOP_EVIDENCE_REQUIRED_MODES and not has_durable_proof:
        effective_decision = "continue"
        _record(
            "candidate_continuation_decision",
            "premature_convergence",
            f"mode {selected_mode} requires durable evidence or continuation linkage before recommending stop",
        )

    policy_state = "accepted"
    if errors:
        policy_state = "downgraded" if normalized["advisory_only"] else "rejected"

    minimum_deliverables = []
    if selected_mode in MODE_IDS:
        mode_map = {row["mode_id"]: row for row in normalized_policy["modes"]}
        minimum_deliverables = list((mode_map.get(selected_mode) or {}).get("minimum_deliverables") or [])

    receipt = {
        "policy_uid": UID,
        "policy_version": normalized_policy["policy_version"],
        "selected_mode": selected_mode,
        "convergence_posture": convergence_posture,
        "wave_cap": wave_cap,
        "requested_builder_waves": requested_builder_waves,
        "candidate_continuation_decision": candidate_decision,
        "effective_continuation_decision": effective_decision,
        "continuation_rationale": continuation_rationale,
        "policy_state": policy_state,
        "advisory_only": normalized["advisory_only"],
        "reason_codes": reason_codes,
        "has_durable_proof": has_durable_proof,
        "continuation_packet_id": continuation_packet_id,
        "evidence_pack_refs": evidence_pack_refs,
        "minimum_deliverables": minimum_deliverables,
    }

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_guidance": normalized,
        "receipt": receipt,
    }


def validate_budget_adaptive_planning_receipt(
    raw_receipt: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
    field_prefix: str = "budget_adaptive_receipt",
) -> List[Dict[str, str]]:
    normalized = normalize_budget_adaptive_planning_receipt(raw_receipt)
    guidance = {
        "selected_mode": normalized["selected_mode"],
        "requested_builder_waves": normalized["requested_builder_waves"],
        "candidate_continuation_decision": normalized["candidate_continuation_decision"],
        "continuation_rationale": normalized["continuation_rationale"],
        "continuation_packet_id": normalized["continuation_packet_id"],
        "evidence_pack_refs": normalized["evidence_pack_refs"],
        "advisory_only": normalized["advisory_only"],
    }
    evaluated = evaluate_budget_adaptive_planning_guidance(guidance, repo_root=repo_root)
    expected = normalize_budget_adaptive_planning_receipt(evaluated["receipt"])
    errors: List[Dict[str, str]] = []

    if normalized["policy_uid"] and normalized["policy_uid"] != UID:
        errors.append(
            {
                "field": f"{field_prefix}.policy_uid",
                "code": "invalid_value",
                "message": f"{field_prefix}.policy_uid must remain bound to {UID}",
            }
        )
    if normalized["policy_version"] and normalized["policy_version"] != expected["policy_version"]:
        errors.append(
            {
                "field": f"{field_prefix}.policy_version",
                "code": "invalid_value",
                "message": f"{field_prefix}.policy_version must match the canonical policy version",
            }
        )

    comparable_fields = (
        "selected_mode",
        "convergence_posture",
        "wave_cap",
        "requested_builder_waves",
        "candidate_continuation_decision",
        "effective_continuation_decision",
        "continuation_rationale",
        "policy_state",
        "advisory_only",
        "reason_codes",
        "has_durable_proof",
        "continuation_packet_id",
        "evidence_pack_refs",
        "minimum_deliverables",
    )
    for field in comparable_fields:
        if normalized[field] != expected[field]:
            errors.append(
                {
                    "field": f"{field_prefix}.{field}",
                    "code": "invalid_value",
                    "message": f"{field_prefix}.{field} must stay consistent with the evaluated budget-adaptive planning policy output",
                }
            )
    return errors
