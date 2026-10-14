"""Canonical mission contract loader and projections for the plane."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple


SCHEMA_VERSION = "plane_mission_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/plane_mission_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_plane_mission_contract_v1.md")
UID = "PIS-20260501-114501Z-A1F3"
TOP_LEVEL_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "uid",
    "mission_id",
    "mission_version",
    "product_promise",
    "operator_success_model",
    "activity_vs_mission_failure_warning",
    "anti_activity_warning",
    "claim_ceiling_summary",
    "overreach_guardrails",
    "refresh_scope_summary",
    "refresh_scope_reload_limits",
    "aligned_expansion_boundaries",
    "explicit_non_goals",
    "explanatory_refs",
)


class PlaneMissionContractError(RuntimeError):
    """Raised when the canonical plane mission contract cannot be loaded."""


@dataclass(frozen=True)
class PlaneMissionContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    top_level_required_fields: Tuple[str, ...]


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _fingerprint(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def plane_mission_contract_v1() -> PlaneMissionContractV1:
    return PlaneMissionContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        top_level_required_fields=TOP_LEVEL_REQUIRED_FIELDS,
    )


def load_plane_mission_contract_artifact(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise PlaneMissionContractError("canonical mission contract must be a JSON object")
    return payload


def validate_plane_mission_contract(raw: Mapping[str, Any]) -> Dict[str, Any]:
    errors: List[Dict[str, str]] = []
    normalized = {
        "schema_version": _normalize_text(raw.get("schema_version")),
        "uid": _normalize_text(raw.get("uid")),
        "contract_family": _normalize_text(raw.get("contract_family")),
        "canonical_doc_rel": _normalize_text(raw.get("canonical_doc_rel")),
        "mission_id": _normalize_text(raw.get("mission_id")),
        "mission_version": _normalize_text(raw.get("mission_version")),
        "product_promise": _normalize_text(raw.get("product_promise")),
        "operator_success_model": _normalize_text_list(raw.get("operator_success_model")),
        "activity_vs_mission_failure_warning": _normalize_text(raw.get("activity_vs_mission_failure_warning")),
        "anti_activity_warning": _normalize_text(raw.get("anti_activity_warning")),
        "claim_ceiling_summary": _normalize_text(raw.get("claim_ceiling_summary")),
        "overreach_guardrails": _normalize_text_list(raw.get("overreach_guardrails")),
        "refresh_scope_summary": _normalize_text(raw.get("refresh_scope_summary")),
        "refresh_scope_reload_limits": _normalize_text_list(raw.get("refresh_scope_reload_limits")),
        "aligned_expansion_boundaries": _normalize_text_list(raw.get("aligned_expansion_boundaries")),
        "explicit_non_goals": _normalize_text_list(raw.get("explicit_non_goals")),
        "explanatory_refs": _normalize_text_list(raw.get("explanatory_refs")),
    }

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", None, [], {}):
            errors.append({"field": field, "code": "missing_field", "message": f"{field} is required"})

    if normalized["schema_version"] and normalized["schema_version"] != SCHEMA_VERSION:
        errors.append(
            {
                "field": "schema_version",
                "code": "invalid_schema_version",
                "message": f"schema_version must remain {SCHEMA_VERSION}",
            }
        )
    if normalized["uid"] and normalized["uid"] != UID:
        errors.append({"field": "uid", "code": "invalid_uid", "message": f"uid must remain {UID}"})
    if normalized["canonical_doc_rel"] and normalized["canonical_doc_rel"] != CONTRACT_DOC_REL.as_posix():
        errors.append(
            {
                "field": "canonical_doc_rel",
                "code": "invalid_canonical_doc_rel",
                "message": f"canonical_doc_rel must remain {CONTRACT_DOC_REL.as_posix()}",
            }
        )

    boundary_keys = {text.casefold(): text for text in normalized["aligned_expansion_boundaries"]}
    for text in normalized["explicit_non_goals"]:
        match = boundary_keys.get(text.casefold())
        if match is not None:
            errors.append(
                {
                    "field": "explicit_non_goals",
                    "code": "contradictory_boundary_statement",
                    "message": f"non-goal duplicates aligned expansion boundary: {match}",
                }
            )

    if normalized["product_promise"] and normalized["product_promise"] == normalized["anti_activity_warning"]:
        errors.append(
            {
                "field": "anti_activity_warning",
                "code": "contradictory_warning",
                "message": "anti_activity_warning must not collapse into product_promise",
            }
        )

    contract = dict(normalized)
    contract["source_fingerprint"] = _fingerprint(contract)
    return {
        "ok": not errors,
        "errors": errors,
        "normalized_contract": contract,
        "computed_fingerprint": contract["source_fingerprint"],
    }


def project_runtime_mission_context(contract: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_plane_mission_contract(contract).get("normalized_contract") or {}
    return {
        "mission_id": _normalize_text(normalized.get("mission_id")),
        "mission_version": _normalize_text(normalized.get("mission_version")),
        "product_promise": _normalize_text(normalized.get("product_promise")),
        "operator_success_model": list(normalized.get("operator_success_model") or []),
        "activity_vs_mission_failure_warning": _normalize_text(normalized.get("activity_vs_mission_failure_warning")),
        "anti_activity_warning": _normalize_text(normalized.get("anti_activity_warning")),
        "claim_ceiling_summary": _normalize_text(normalized.get("claim_ceiling_summary")),
        "overreach_guardrails": list(normalized.get("overreach_guardrails") or []),
        "refresh_scope_summary": _normalize_text(normalized.get("refresh_scope_summary")),
        "refresh_scope_reload_limits": list(normalized.get("refresh_scope_reload_limits") or []),
        "aligned_expansion_boundaries": list(normalized.get("aligned_expansion_boundaries") or []),
        "explicit_non_goals": list(normalized.get("explicit_non_goals") or []),
        "explanatory_refs": list(normalized.get("explanatory_refs") or []),
        "source_fingerprint": _normalize_text(normalized.get("source_fingerprint")),
    }


def project_operator_mission_summary(contract: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = validate_plane_mission_contract(contract).get("normalized_contract") or {}
    return {
        "headline": _normalize_text(normalized.get("product_promise")),
        "operator_success_model": list(normalized.get("operator_success_model") or []),
        "activity_vs_mission_failure_warning": _normalize_text(normalized.get("activity_vs_mission_failure_warning")),
        "anti_activity_warning": _normalize_text(normalized.get("anti_activity_warning")),
        "claim_ceiling_summary": _normalize_text(normalized.get("claim_ceiling_summary")),
        "refresh_scope_summary": _normalize_text(normalized.get("refresh_scope_summary")),
        "aligned_expansion_boundaries": list(normalized.get("aligned_expansion_boundaries") or []),
        "explicit_non_goals": list(normalized.get("explicit_non_goals") or []),
        "source_fingerprint": _normalize_text(normalized.get("source_fingerprint")),
    }


def read_plane_mission_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    path = (root / CONTRACT_ARTIFACT_REL).resolve()
    try:
        payload = load_plane_mission_contract_artifact(repo_root=root)
    except FileNotFoundError:
        return {
            "ok": False,
            "failure_class": "mission_contract_missing",
            "summary": "Canonical plane mission contract is missing.",
            "path": str(path),
            "errors": [{"field": CONTRACT_ARTIFACT_REL.as_posix(), "code": "missing_file", "message": "canonical mission contract file is missing"}],
        }
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "failure_class": "mission_contract_malformed",
            "summary": "Canonical plane mission contract is malformed JSON.",
            "path": str(path),
            "errors": [{"field": CONTRACT_ARTIFACT_REL.as_posix(), "code": "malformed_json", "message": str(exc)}],
        }
    except PlaneMissionContractError as exc:
        return {
            "ok": False,
            "failure_class": "mission_contract_malformed",
            "summary": str(exc),
            "path": str(path),
            "errors": [{"field": CONTRACT_ARTIFACT_REL.as_posix(), "code": "malformed_payload", "message": str(exc)}],
        }

    validation = validate_plane_mission_contract(payload)
    if not validation["ok"]:
        return {
            "ok": False,
            "failure_class": "mission_contract_invalid",
            "summary": "Canonical plane mission contract failed validation.",
            "path": str(path),
            "errors": list(validation["errors"]),
            "contract": dict(validation["normalized_contract"]),
        }

    contract = dict(validation["normalized_contract"])
    return {
        "ok": True,
        "path": str(path),
        "contract": contract,
        "runtime_projection": project_runtime_mission_context(contract),
        "operator_projection": project_operator_mission_summary(contract),
        "computed_fingerprint": _normalize_text(validation["computed_fingerprint"]),
    }


def load_plane_mission_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    result = read_plane_mission_contract(repo_root=repo_root)
    if not result.get("ok"):
        raise PlaneMissionContractError(str(result.get("summary") or "plane mission contract load failed"))
    return dict(result.get("contract") or {})


def validate_plane_mission_contract_artifact(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_plane_mission_contract_artifact(repo_root=repo_root)
    result = validate_plane_mission_contract(payload)
    return tuple(f"{row['field']}:{row['code']}" for row in result["errors"])
