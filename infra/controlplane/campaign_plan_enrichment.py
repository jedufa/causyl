"""Policy enrichment helpers for generated campaign plans."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Tuple

from infra.controlplane import budget_adaptive_research_policy
from infra.controlplane import campaign_plan_contract
from infra.controlplane import scientific_campaign_method_template


REQUEST_SCHEMA_VERSION = "campaign_plan_enrichment_request_v1"
REPORT_SCHEMA_VERSION = "campaign_plan_enrichment_report_v1"
UID = "PIS-20260411-113601Z-59F3"

CONSTRAINT_KINDS: Tuple[str, ...] = campaign_plan_contract.CONSTRAINT_KINDS
STATUS_BLOCKED = "blocked"
STATUS_ENRICHED = "enriched"

_BUDGET_MODE_RANK = {
    mode_id: index for index, mode_id in enumerate(budget_adaptive_research_policy.MODE_IDS)
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


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


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    out: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


def _is_safe_repo_rel(path_text: str) -> bool:
    text = _normalize_text(path_text)
    if not text:
        return False
    path = Path(text)
    return not path.is_absolute() and ".." not in path.parts


def _load_json_ref(path_text: str, *, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / path_text).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _normalize_constraint(raw_constraint: Mapping[str, Any]) -> Dict[str, Any]:
    row = dict(raw_constraint or {})
    return {
        "constraint_id": _normalize_text(row.get("constraint_id")),
        "constraint_kind": _normalize_text(row.get("constraint_kind")).lower(),
        "phase_id": _normalize_text(row.get("phase_id")),
        "method_phase_id": _normalize_text(row.get("method_phase_id")),
        "advisory_only": _normalize_bool(row.get("advisory_only"), default=False),
        "rationale": _normalize_text(row.get("rationale")),
    }


def normalize_campaign_plan_enrichment_request(raw_request: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(raw_request or {})
    return {
        "schema_version": _normalize_text(raw.get("schema_version") or REQUEST_SCHEMA_VERSION),
        "campaign_plan": campaign_plan_contract.normalize_campaign_plan(
            raw.get("campaign_plan") if isinstance(raw.get("campaign_plan"), Mapping) else {}
        ),
        "method_template_ref": _normalize_text(raw.get("method_template_ref")),
        "budget_policy_ref": _normalize_text(raw.get("budget_policy_ref")),
        "selected_budget_mode": _normalize_text(raw.get("selected_budget_mode")).lower(),
        "declared_constraints": [
            _normalize_constraint(row)
            for row in list(raw.get("declared_constraints") or [])
            if isinstance(row, Mapping)
        ],
    }


def _record_error(errors: List[Dict[str, str]], field: str, code: str, message: str) -> None:
    errors.append({"field": field, "code": code, "message": message})


def _receipt(
    *,
    receipt_id: str,
    policy_source_kind: str,
    policy_ref: str,
    target_phase_ids: List[str],
    changed_fields: List[str],
    change_summary: str,
    reason_codes: List[str],
    receipt_state: str,
) -> Dict[str, Any]:
    return {
        "receipt_id": receipt_id,
        "policy_source_kind": policy_source_kind,
        "policy_ref": policy_ref,
        "target_phase_ids": target_phase_ids,
        "changed_fields": changed_fields,
        "change_summary": change_summary,
        "reason_codes": reason_codes,
        "receipt_state": receipt_state,
    }


def enrich_campaign_plan(
    raw_request: Mapping[str, Any],
    *,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    request = normalize_campaign_plan_enrichment_request(raw_request)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    trace: List[str] = []
    root = Path(repo_root) if repo_root is not None else _repo_root()

    if request["schema_version"] != REQUEST_SCHEMA_VERSION:
        _record_error(
            errors,
            "schema_version",
            "invalid_value",
            f"campaign plan enrichment schema_version must remain {REQUEST_SCHEMA_VERSION}",
        )

    plan_validation = campaign_plan_contract.validate_campaign_plan(request["campaign_plan"], repo_root=root)
    if plan_validation["errors"]:
        for row in plan_validation["errors"]:
            errors.append(
                {
                    "field": f"campaign_plan.{row['field']}",
                    "code": row["code"],
                    "message": row["message"],
                }
            )
    else:
        trace.append("validated_campaign_plan_input")

    for field_name in ("method_template_ref", "budget_policy_ref"):
        ref = request[field_name]
        if not ref:
            _record_error(errors, field_name, "missing_field", f"{field_name} is required")
            continue
        if not _is_safe_repo_rel(ref):
            _record_error(errors, field_name, "invalid_ref", f"{field_name} must stay repo-relative and traversal-safe")
            continue
        if not (root / ref).exists():
            _record_error(errors, field_name, "missing_ref", f"referenced enrichment artifact does not exist: {ref}")

    if request["selected_budget_mode"] not in budget_adaptive_research_policy.MODE_IDS:
        _record_error(
            errors,
            "selected_budget_mode",
            "invalid_value",
            "selected_budget_mode must use the canonical budget-adaptive mode vocabulary",
        )

    method_template_payload: Dict[str, Any] = {}
    if not any(row["field"] == "method_template_ref" for row in errors):
        method_template_payload = _load_json_ref(request["method_template_ref"], repo_root=root)
        method_validation = scientific_campaign_method_template.validate_scientific_campaign_method_template(
            method_template_payload
        )
        if method_validation["errors"]:
            _record_error(
                errors,
                "method_template_ref",
                "invalid_method_template",
                "method_template_ref must point at a valid scientific campaign method template artifact",
            )
        else:
            trace.append("validated_method_template_input")

    budget_policy_payload: Dict[str, Any] = {}
    if not any(row["field"] == "budget_policy_ref" for row in errors):
        budget_policy_payload = _load_json_ref(request["budget_policy_ref"], repo_root=root)
        budget_validation = budget_adaptive_research_policy.validate_budget_adaptive_research_policy(
            budget_policy_payload
        )
        if budget_validation["errors"]:
            _record_error(
                errors,
                "budget_policy_ref",
                "invalid_budget_policy",
                "budget_policy_ref must point at a valid budget-adaptive research policy artifact",
            )
        else:
            trace.append("validated_budget_policy_input")

    normalized_plan = copy.deepcopy(plan_validation["normalized_plan"])
    phase_map = {row["phase_id"]: row for row in list(normalized_plan.get("phases") or [])}
    require_pairs: set[tuple[str, str]] = set()
    forbid_pairs: set[tuple[str, str]] = set()
    seen_constraint_ids: set[str] = set()
    for index, row in enumerate(request["declared_constraints"]):
        prefix = f"declared_constraints[{index}]"
        for key in ("constraint_id", "constraint_kind", "phase_id", "method_phase_id", "rationale"):
            if row.get(key) in ("", None):
                _record_error(errors, f"{prefix}.{key}", "missing_field", f"{prefix}.{key} is required")
        if row["constraint_id"] in seen_constraint_ids:
            _record_error(errors, f"{prefix}.constraint_id", "duplicate_constraint_id", "declared constraint ids must be unique")
        seen_constraint_ids.add(row["constraint_id"])
        if row["constraint_kind"] not in CONSTRAINT_KINDS:
            _record_error(errors, f"{prefix}.constraint_kind", "invalid_value", "declared constraint kind is unknown")
        phase = phase_map.get(row["phase_id"])
        if phase is None:
            _record_error(errors, f"{prefix}.phase_id", "invalid_value", "declared constraint must point at an existing phase")
            continue
        if row["method_phase_id"] not in scientific_campaign_method_template.PHASE_IDS:
            _record_error(errors, f"{prefix}.method_phase_id", "invalid_value", "declared constraint must use canonical method-phase ids")
        pair = (row["phase_id"], row["method_phase_id"])
        if row["constraint_kind"] == "require_method_phase":
            require_pairs.add(pair)
            if row["method_phase_id"] not in list(phase.get("method_template_phase_ids") or []):
                _record_error(
                    errors,
                    f"{prefix}.method_phase_id",
                    "missing_required_method_phase",
                    "required method-phase constraint is not represented in the target campaign phase",
                )
        if row["constraint_kind"] == "forbid_method_phase":
            forbid_pairs.add(pair)
            if row["method_phase_id"] in list(phase.get("method_template_phase_ids") or []):
                _record_error(
                    errors,
                    f"{prefix}.method_phase_id",
                    "forbidden_method_phase_present",
                    "forbidden method-phase constraint contradicts the generated campaign phase",
                )

    for phase_id, method_phase_id in sorted(require_pairs & forbid_pairs):
        _record_error(
            errors,
            "declared_constraints",
            "contradictory_constraint",
            f"phase {phase_id} cannot both require and forbid method phase {method_phase_id}",
        )

    selected_budget_mode = request["selected_budget_mode"]
    if selected_budget_mode in _BUDGET_MODE_RANK:
        selected_rank = _BUDGET_MODE_RANK[selected_budget_mode]
        for index, phase in enumerate(list(normalized_plan.get("phases") or [])):
            phase_mode = _normalize_text(phase.get("budget_mode")).lower()
            if phase_mode in _BUDGET_MODE_RANK and _BUDGET_MODE_RANK[phase_mode] > selected_rank:
                _record_error(
                    errors,
                    f"campaign_plan.phases[{index}].budget_mode",
                    "budget_mode_contradicts_phase_plan",
                    "selected budget mode cannot honestly shrink an already broader generated phase plan",
                )

    if errors:
        reason_codes = list(dict.fromkeys(row["code"] for row in errors))
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "uid": UID,
            "ok": False,
            "status": STATUS_BLOCKED,
            "errors": errors,
            "warnings": warnings,
            "reason_codes": reason_codes,
            "campaign_plan": None,
            "enrichment_trace": trace,
        }

    method_phase_map = {
        row["phase_id"]: row for row in list(method_template_payload.get("phases") or []) if isinstance(row, Mapping)
    }
    policy_mode_map = {
        row["mode_id"]: row for row in list(budget_policy_payload.get("modes") or []) if isinstance(row, Mapping)
    }
    receipts: List[Dict[str, Any]] = []

    for phase in list(normalized_plan.get("phases") or []):
        missing_outputs: List[str] = []
        for method_phase_id in list(phase.get("method_template_phase_ids") or []):
            method_phase = method_phase_map.get(method_phase_id) or {}
            for output in list(method_phase.get("required_outputs") or []):
                if output not in list(phase.get("deliverables") or []) and output not in missing_outputs:
                    missing_outputs.append(output)
        if missing_outputs:
            phase["deliverables"] = list(phase.get("deliverables") or []) + missing_outputs
            receipts.append(
                _receipt(
                    receipt_id=f"method_{phase['phase_id']}",
                    policy_source_kind="method_template",
                    policy_ref=request["method_template_ref"],
                    target_phase_ids=[phase["phase_id"]],
                    changed_fields=["deliverables"],
                    change_summary="Added required method-template outputs to the campaign phase deliverables.",
                    reason_codes=["method_required_outputs_bound"],
                    receipt_state="applied",
                )
            )
    trace.append("bound_method_template_outputs")

    final_phase = list(normalized_plan.get("phases") or [])[-1]
    selected_mode = policy_mode_map[selected_budget_mode]
    changed_fields: List[str] = []
    change_notes: List[str] = []
    if _normalize_text(final_phase.get("budget_mode")).lower() != selected_budget_mode:
        final_phase["budget_mode"] = selected_budget_mode
        changed_fields.append("budget_mode")
        change_notes.append("aligned final handoff phase budget mode")
    missing_budget_deliverables = [
        row
        for row in list(selected_mode.get("minimum_deliverables") or [])
        if row not in list(final_phase.get("deliverables") or [])
    ]
    if missing_budget_deliverables:
        final_phase["deliverables"] = list(final_phase.get("deliverables") or []) + missing_budget_deliverables
        changed_fields.append("deliverables")
        change_notes.append("attached minimum deliverable floors for the selected budget mode")
    if changed_fields:
        receipts.append(
            _receipt(
                receipt_id=f"budget_{final_phase['phase_id']}",
                policy_source_kind="budget_policy",
                policy_ref=request["budget_policy_ref"],
                target_phase_ids=[final_phase["phase_id"]],
                changed_fields=changed_fields,
                change_summary="; ".join(change_notes).capitalize() + ".",
                reason_codes=["budget_mode_aligned", "minimum_deliverable_floor_bound"],
                receipt_state="applied",
            )
        )
    trace.append("bound_budget_policy_receipt")

    for row in request["declared_constraints"]:
        receipts.append(
            _receipt(
                receipt_id=f"constraint_{row['constraint_id']}",
                policy_source_kind="declared_constraint",
                policy_ref=request["method_template_ref"],
                target_phase_ids=[row["phase_id"]],
                changed_fields=[],
                change_summary=row["rationale"],
                reason_codes=["constraint_verified"],
                receipt_state="validated_no_change",
            )
        )
    if request["declared_constraints"]:
        trace.append("recorded_declared_constraint_receipts")

    normalized_plan["policy_enrichment"] = {
        "method_template_ref": request["method_template_ref"],
        "budget_policy_ref": request["budget_policy_ref"],
        "selected_budget_mode": selected_budget_mode,
        "declared_constraints": request["declared_constraints"],
        "enrichment_receipts": receipts,
    }

    enriched_validation = campaign_plan_contract.validate_campaign_plan(normalized_plan, repo_root=root)
    if enriched_validation["errors"]:
        return {
            "schema_version": REPORT_SCHEMA_VERSION,
            "uid": UID,
            "ok": False,
            "status": STATUS_BLOCKED,
            "errors": enriched_validation["errors"],
            "warnings": warnings,
            "reason_codes": list(dict.fromkeys(row["code"] for row in enriched_validation["errors"])),
            "campaign_plan": None,
            "enrichment_trace": trace,
        }
    trace.append("validated_enriched_campaign_plan")

    return {
        "schema_version": REPORT_SCHEMA_VERSION,
        "uid": UID,
        "ok": True,
        "status": STATUS_ENRICHED,
        "errors": [],
        "warnings": warnings,
        "reason_codes": [],
        "campaign_plan": enriched_validation["normalized_plan"],
        "enrichment_trace": trace,
    }
