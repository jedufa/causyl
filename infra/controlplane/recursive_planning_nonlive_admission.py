"""Bind validated recursive dry-run outputs to scheduler-facing non-live admission truth."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping

from infra.controlplane import compiled_work_order_boundary_emission as compiled_boundary
from infra.controlplane import recursive_dry_run_planner
from infra.controlplane import recursive_planning_receipts
from infra.controlplane import recursive_spawn_policy_contract


SCHEMA_VERSION = "recursive_planning_nonlive_admission_v1"
UID = "PIS-20260409-154916Z-2B1D"
ENTRYPOINT_KINDS = ("campaign_phase", "parent_job")
DEFAULT_NONLAUNCH_REASON = "recursive_planning_dry_run_only"


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


def _normalize_entrypoint(value: Mapping[str, Any] | None) -> Dict[str, Any]:
    row = dict(value or {})
    return {
        "entrypoint_kind": _normalize_text(row.get("entrypoint_kind")).lower(),
        "parent_job_shape": _normalize_text(row.get("parent_job_shape")),
        "parent_job_id": _normalize_text(row.get("parent_job_id")),
        "binding_type": _normalize_text(row.get("binding_type")).lower(),
        "binding_id": _normalize_text(row.get("binding_id")),
        "campaign_id": _normalize_text(row.get("campaign_id")),
        "phase_id": _normalize_text(row.get("phase_id")),
        "campaign_phase_category": _normalize_text(row.get("campaign_phase_category")).lower(),
        "campaign_truth_refs": _normalize_text_list(row.get("campaign_truth_refs")),
    }


def _append_error(errors: List[Dict[str, str]], *, field: str, code: str, message: str) -> None:
    errors.append({"field": field, "code": code, "message": message})


def _dedupe_codes(errors: List[Dict[str, str]]) -> List[str]:
    out: List[str] = []
    for row in errors:
        code = _normalize_text(row.get("code"))
        if code and code not in out:
            out.append(code)
    return out


def _root_truth_campaign_ids(plan_result: Mapping[str, Any]) -> List[str]:
    plan_record = plan_result.get("plan_record") if isinstance(plan_result.get("plan_record"), Mapping) else {}
    plan_truth_refs = plan_record.get("plan_truth_refs") if isinstance(plan_record.get("plan_truth_refs"), Mapping) else {}
    campaign_ids = _normalize_text_list(plan_truth_refs.get("campaign_ids"))
    if campaign_ids:
        return campaign_ids
    normalized_request = (
        plan_result.get("normalized_request") if isinstance(plan_result.get("normalized_request"), Mapping) else {}
    )
    root_node = normalized_request.get("root_node") if isinstance(normalized_request.get("root_node"), Mapping) else {}
    root_truth_refs = root_node.get("truth_refs") if isinstance(root_node.get("truth_refs"), Mapping) else {}
    return _normalize_text_list(root_truth_refs.get("campaign_ids"))


def materialize_recursive_planning_nonlive_admission(
    *,
    entrypoint: Mapping[str, Any],
    plan_result: Mapping[str, Any],
    request_live_launch: bool = False,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    normalized_entrypoint = _normalize_entrypoint(entrypoint)
    errors: List[Dict[str, str]] = []

    plan_record = plan_result.get("plan_record") if isinstance(plan_result.get("plan_record"), Mapping) else {}
    validation_report = (
        plan_result.get("validation_report") if isinstance(plan_result.get("validation_report"), Mapping) else {}
    )
    normalized_request = (
        plan_result.get("normalized_request") if isinstance(plan_result.get("normalized_request"), Mapping) else {}
    )
    graph_summary = validation_report.get("graph_summary") if isinstance(validation_report.get("graph_summary"), Mapping) else {}
    root_binding = plan_record.get("root_binding") if isinstance(plan_record.get("root_binding"), Mapping) else {}
    root_campaign_ids = _root_truth_campaign_ids(plan_result)

    kind = normalized_entrypoint["entrypoint_kind"]
    parent_job_shape = normalized_entrypoint["parent_job_shape"]
    if kind not in ENTRYPOINT_KINDS:
        _append_error(
            errors,
            field="entrypoint_kind",
            code="entrypoint_kind_invalid",
            message="recursive planning admission requires a campaign_phase or parent_job entrypoint",
        )
    if parent_job_shape not in recursive_spawn_policy_contract.PLANNING_PARENT_SHAPES:
        _append_error(
            errors,
            field="parent_job_shape",
            code="parent_shape_not_planning_eligible",
            message="entrypoint parent_job_shape is not allowed to request recursive dry-run planning",
        )
    if not normalized_entrypoint["parent_job_id"]:
        _append_error(
            errors,
            field="parent_job_id",
            code="missing_field",
            message="entrypoint parent_job_id is required for scheduler-facing recursive planning admission",
        )
    if (
        _normalize_text(plan_record.get("parent_job_id"))
        and normalized_entrypoint["parent_job_id"]
        and normalized_entrypoint["parent_job_id"] != _normalize_text(plan_record.get("parent_job_id"))
    ):
        _append_error(
            errors,
            field="parent_job_id",
            code="parent_job_mismatch",
            message="entrypoint parent_job_id must match the validated recursive plan parent_job_id",
        )
    if kind == "campaign_phase":
        if parent_job_shape != "campaign_job":
            _append_error(
                errors,
                field="parent_job_shape",
                code="campaign_entrypoint_requires_campaign_job",
                message="campaign-phase recursive planning entrypoints must originate from campaign_job parents",
            )
        if not normalized_entrypoint["campaign_id"]:
            _append_error(errors, field="campaign_id", code="missing_field", message="campaign_phase entrypoints require campaign_id")
        if not normalized_entrypoint["phase_id"]:
            _append_error(errors, field="phase_id", code="missing_field", message="campaign_phase entrypoints require phase_id")
        if (
            normalized_entrypoint["campaign_phase_category"]
            not in recursive_spawn_policy_contract.ALLOWED_CAMPAIGN_PHASE_CATEGORIES
        ):
            _append_error(
                errors,
                field="campaign_phase_category",
                code="campaign_phase_not_planning_eligible",
                message="campaign_phase entrypoints must stay within planning, repair, or review categories",
            )
    if request_live_launch:
        _append_error(
            errors,
            field="request_live_launch",
            code="live_recursive_launch_forbidden",
            message="validated recursive dry-run outputs may not request live descendant launches in v1",
        )

    if _normalize_text(validation_report.get("schema_version")) != recursive_dry_run_planner.REPORT_SCHEMA_VERSION:
        _append_error(
            errors,
            field="validation_report.schema_version",
            code="invalid_report_schema",
            message="recursive planning admission requires a canonical recursive dry-run validation report",
        )
    if not bool(plan_result.get("ok")):
        for code in _normalize_text_list(validation_report.get("non_launch_reason_codes")) or ["plan_report_not_validated"]:
            _append_error(
                errors,
                field="plan_result",
                code=code,
                message="recursive planning admission requires a validated dry-run plan result",
            )
    if _normalize_text(validation_report.get("report_status")).lower() != "validated":
        _append_error(
            errors,
            field="validation_report.report_status",
            code="plan_report_not_validated",
            message="validation_report.report_status must remain validated before scheduler admission projection",
        )
    if _normalize_text(plan_record.get("plan_status")).lower() != "validated":
        _append_error(
            errors,
            field="plan_record.plan_status",
            code="plan_record_not_validated",
            message="plan_record.plan_status must remain validated before scheduler admission projection",
        )
    if bool(validation_report.get("live_queue_admission_allowed")):
        _append_error(
            errors,
            field="validation_report.live_queue_admission_allowed",
            code="live_queue_admission_forbidden",
            message="recursive dry-run outputs may not mark live_queue_admission_allowed true in v1",
        )

    if normalized_entrypoint["binding_type"] and normalized_entrypoint["binding_type"] != _normalize_text(root_binding.get("binding_type")).lower():
        _append_error(
            errors,
            field="binding_type",
            code="binding_mismatch",
            message="entrypoint binding_type must match the validated recursive plan root_binding.binding_type",
        )
    if normalized_entrypoint["binding_id"] and normalized_entrypoint["binding_id"] != _normalize_text(root_binding.get("binding_id")):
        _append_error(
            errors,
            field="binding_id",
            code="binding_mismatch",
            message="entrypoint binding_id must match the validated recursive plan root_binding.binding_id",
        )
    if root_campaign_ids and normalized_entrypoint["campaign_id"] and normalized_entrypoint["campaign_id"] not in root_campaign_ids:
        _append_error(
            errors,
            field="campaign_id",
            code="campaign_context_mismatch",
            message="campaign_id must be preserved from the validated plan truth refs when projecting scheduler admission",
        )

    campaign_id = normalized_entrypoint["campaign_id"] or (root_campaign_ids[0] if root_campaign_ids else "")
    campaign_truth_refs = list(root_campaign_ids)
    for ref in normalized_entrypoint["campaign_truth_refs"]:
        if ref not in campaign_truth_refs:
            campaign_truth_refs.append(ref)

    non_launch_reason_codes = _dedupe_codes(errors)
    if not non_launch_reason_codes:
        non_launch_reason_codes = [DEFAULT_NONLAUNCH_REASON]

    admission = {
        "schema_version": SCHEMA_VERSION,
        "uid": UID,
        "entrypoint": dict(normalized_entrypoint),
        "campaign_context": {
            "campaign_id": campaign_id,
            "phase_id": normalized_entrypoint["phase_id"],
            "campaign_phase_category": normalized_entrypoint["campaign_phase_category"],
            "campaign_truth_refs": campaign_truth_refs,
            "preserved_from": "campaign_input_and_validated_plan_truth",
        },
        "scheduler_admission": {
            "launch_allowed": False,
            "nonlaunch_status": "blocked",
            "nonlaunch_reason": DEFAULT_NONLAUNCH_REASON if not errors else non_launch_reason_codes[0],
            "decision_family": "explicit_non_launch",
        },
        "queue_law": {
            "validated_plan_required": True,
            "entrypoint_allowed": not bool(errors),
            "live_queue_admission_allowed": False,
            "admission_gate_reason": _normalize_text(validation_report.get("admission_gate_reason"))
            or "validated recursive dry-run outputs remain non-live in v1",
            "planning_mode": _normalize_text(normalized_request.get("planning_mode")).lower(),
        },
        "plan_projection": {
            "plan_id": _normalize_text(plan_record.get("plan_id")),
            "root_plan_id": _normalize_text(plan_record.get("root_plan_id")),
            "parent_plan_id": _normalize_text(plan_record.get("parent_plan_id")),
            "parent_job_id": _normalize_text(plan_record.get("parent_job_id")),
            "root_node_id": _normalize_text(plan_record.get("root_node_id")),
            "request_fingerprint": _normalize_text(validation_report.get("request_fingerprint")),
            "plan_fingerprint": _normalize_text(validation_report.get("plan_fingerprint")),
            "graph_summary": dict(graph_summary),
            "truth_refs": dict(plan_record.get("plan_truth_refs") or {}),
            "event_refs": list(plan_record.get("plan_event_refs") or []),
        },
        "non_launch_reason_codes": non_launch_reason_codes,
    }
    compiled_owner_rel = (
        Path("runtime/state/controlplane/recursive_planning/non_live_admission")
        / (_normalize_text(admission["plan_projection"]["plan_id"]) or "unknown_plan")
        / "non_live_admission.json"
    )
    if repo_root is not None and admission["plan_projection"]["plan_id"]:
        compiled_owner_rel = (
            recursive_planning_receipts._plan_receipts_dir(  # noqa: SLF001 - shared internal receipt root keeps linkage stable.
                admission["plan_projection"]["plan_id"],
                repo_root=Path(repo_root).resolve(),
            ).relative_to(Path(repo_root).resolve())
            / "non_live_admission.json"
        )
    admission["compiled_work_order"] = compiled_boundary.emit_compiled_work_order_boundary(
        boundary_kind="recursive_planning_handoff",
        work_order_id=f"{_normalize_text(admission['plan_projection']['plan_id'])}__recursive_planning_handoff",
        source_binding={
            "binding_type": _normalize_text(root_binding.get("binding_type")) or normalized_entrypoint["binding_type"],
            "binding_id": _normalize_text(root_binding.get("binding_id")) or normalized_entrypoint["binding_id"],
        },
        handoff_kind="recursive_planning_handoff",
        scope_objective="Compile the recursive dry-run planning handoff only for validated non-live admission receipts.",
        in_scope=[
            "campaign_context",
            "scheduler_admission",
            "plan_projection",
        ],
        out_of_scope=[
            "live descendant launch",
            "non-validated recursive plans",
        ],
        required_context_bundle="lab_core_v1",
        additional_context_refs=list(plan_record.get("plan_event_refs") or []),
        forbidden_context_refs=[],
        expected_outputs=[
            {
                "output_id": "recursive_non_live_admission",
                "kind": "receipt",
                "summary": "Durable non-live recursive planning admission receipt.",
                "required": True,
            }
        ],
        validation_checks=["recursive_dry_run_validation", "non_live_admission_projection"],
        owner_artifact_rel=compiled_owner_rel,
        prose_brief_present=True,
        execution_family={
            "contract_kind": "recursive_planning_nonlive_admission",
            "boundary_owner": "orchestrator",
            "launch_allowed": "false",
        },
        repo_root=Path(repo_root).resolve() if repo_root is not None and not errors else None,
        fallback_reason_codes=non_launch_reason_codes if errors else None,
        continuation_packet_ref="",
    )
    result = {
        "ok": not errors,
        "errors": errors,
        "normalized_entrypoint": normalized_entrypoint,
        "admission": admission,
        "compiled_work_order": admission["compiled_work_order"],
    }
    if repo_root is not None and admission["plan_projection"]["plan_id"]:
        persisted = recursive_planning_receipts.record_non_live_admission_receipt(
            admission,
            plan_result=plan_result,
            repo_root=Path(repo_root).resolve(),
            writer="recursive_planning_nonlive_admission.materialize_recursive_planning_nonlive_admission",
        )
        result["durable_receipt"] = persisted["receipt"]
        result["reconciled_projection"] = persisted["projection"]
    return result
