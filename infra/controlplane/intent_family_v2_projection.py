"""Canonical projection, shadow comparison, and canary admission for intent_family_v2."""

from __future__ import annotations

import hashlib
import json
import os
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Mapping, MutableMapping, Sequence

from infra.controlplane import v2_continuity


SCHEMA_VERSION = "intent_family_v2_projection_v1"
SHADOW_SCHEMA_VERSION = "intent_family_v2_shadow_report_v1"
CANARY_RECEIPT_SCHEMA_VERSION = "intent_family_v2_canary_receipt_v1"
PROOF_REPORT_SCHEMA_VERSION = "intent_family_v2_proof_report_v1"
INTENT_VALIDATION_ARTIFACT_SCHEMA_VERSION = "intent_family_v2_intent_validation_artifact_v1"
UID = "PIS-20260409-195204Z-E2Z5"
PROOF_REPORT_UID = "PIS-20260409-195205Z-F3A6"
INTENT_VALIDATION_UID = "PIS-20260409-195248Z-BCC7"
CANARY_RECEIPTS_ROOT_REL = Path("runtime/state/controlplane/intent_family_v2_canary/receipts")
PROOF_REPORTS_ROOT_REL = Path("runtime/state/controlplane/intent_family_v2_proof_reports")
INTENT_VALIDATION_ARTIFACTS_ROOT_REL = Path("runtime/state/controlplane/intent_family_v2_intent_validation_artifacts")

SURFACES = ("watch", "cli_summary", "operator_snapshot", "reconciliation_report")
ACTIVE_RUNTIME_STATES = frozenset({"queued", "launching", "running", "running_iteration", "awaiting_verifier"})
SUCCESS_RUNTIME_STATES = frozenset({"succeeded", "completed", "validated", "closed"})
FAILED_RUNTIME_STATES = frozenset({"failed", "failed_verification", "proof_gap", "error", "cancelled"})
BLOCKED_RUNTIME_STATES = frozenset({"blocked", "deferred"})
INTENT_VALIDATION_REQUIREMENT_STATUSES = frozenset({"proven", "indirect", "unproven", "contradicted"})
INTENT_VALIDATION_SCENARIO_OUTCOMES = frozenset({"passed", "gap", "contradicted"})
REQUIRED_INTENT_VALIDATION_SCENARIO_KINDS = frozenset(
    {"success_path", "negative_path", "mismatch_prone", "drift_detection"}
)
COMPARABLE_PROJECTION_PATHS = (
    "family_status",
    "current_stage",
    "active_job.child_uid",
    "continuity.continuity_state",
    "next_jobs",
    "blockers",
)
ALLOWED_MIGRATION_STAGES = frozenset({"shadow_only", "narrow_canary", "replacement_owner"})
FINAL_CLOSEOUT_UID = "PIS-20260409-195248Z-4066"
CANARY_RUNNABLE_CONTINUITY_STATES = frozenset({"", "resume_ready", "rerun_ready"})


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items: List[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in items:
            items.append(text)
    return items


def _safe_name(value: Any) -> str:
    return "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in _normalize_text(value))


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _write_json_atomic(path: Path, obj: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(dict(obj), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _count_by_key(rows: Sequence[Mapping[str, Any]], key: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for row in rows:
        value = _normalize_text(row.get(key))
        if not value:
            continue
        counts[value] = counts.get(value, 0) + 1
    return counts


def _normalize_deletion_budget(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    expected_removals = _normalize_text_list(value.get("expected_removals"))
    minimum_removals_raw = value.get("minimum_removals")
    try:
        minimum_removals = int(minimum_removals_raw)
    except (TypeError, ValueError):
        minimum_removals = 0
    if minimum_removals < 0:
        minimum_removals = 0
    out = {
        "expected_removals": expected_removals,
        "minimum_removals": minimum_removals,
    }
    notes = _normalize_text(value.get("notes"))
    if notes:
        out["notes"] = notes
    return out


def _normalize_covered_cutover_scope(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "family_parent_uid": _normalize_text(value.get("family_parent_uid")),
        "workflow_kind": _normalize_text(value.get("workflow_kind")),
        "v2_owned_paths": _normalize_text_list(value.get("v2_owned_paths")),
        "legacy_runtime_paths": _normalize_text_list(value.get("legacy_runtime_paths")),
        "excluded_workflows": _normalize_text_list(value.get("excluded_workflows")),
        "operator_surfaces": _normalize_text_list(value.get("operator_surfaces")),
    }


def _migration_law_from_admission_request(
    family_projection: Mapping[str, Any],
    *,
    admission_request: Mapping[str, Any],
    legacy_owner: str,
    requested_owner: str,
    requested_stage: str,
) -> Dict[str, Any]:
    family_parent_uid = _normalize_text(family_projection.get("family_parent_uid"))
    workflow_kind = _normalize_text(family_projection.get("workflow_kind"))
    covered_scope = _normalize_covered_cutover_scope(admission_request.get("covered_cutover_scope"))
    if not covered_scope.get("family_parent_uid"):
        covered_scope["family_parent_uid"] = family_parent_uid
    if not covered_scope.get("workflow_kind"):
        covered_scope["workflow_kind"] = workflow_kind
    residual_legacy_ownership = _normalize_text_list(admission_request.get("residual_legacy_ownership"))
    rollback_triggers = _normalize_text_list(admission_request.get("rollback_triggers"))
    deletion_budget = _normalize_deletion_budget(admission_request.get("deletion_budget"))
    unresolved_gaps = _normalize_text_list(admission_request.get("unresolved_gaps"))
    rollback_boundary = {
        "reverts_to_owner": legacy_owner,
        "scope": "family_only",
        "applies_to_future_work_only": True,
    }
    return {
        "uid": "PIS-20260409-195206Z-G4B7",
        "family_parent_uid": family_parent_uid,
        "workflow_kind": workflow_kind,
        "requested_stage": requested_stage,
        "covered_cutover_scope": covered_scope,
        "requested_owner": requested_owner,
        "legacy_owner": legacy_owner,
        "residual_legacy_ownership": residual_legacy_ownership,
        "rollback_triggers": rollback_triggers,
        "deletion_budget": deletion_budget,
        "rollback_boundary": rollback_boundary,
        "retirement_preconditions": [
            "shadow_parity_must_remain_match",
            "intent_validation_must_pass",
            "final_closeout_must_authorize_promotion",
            "deletion_budget_targets_must_be_explicit",
        ],
        "final_promotion_owner_uid": FINAL_CLOSEOUT_UID,
        "unresolved_gaps": unresolved_gaps,
    }


def _migration_scope_is_explicit(migration_law: Mapping[str, Any], *, family_parent_uid: str, workflow_kind: str) -> bool:
    covered_scope = migration_law.get("covered_cutover_scope")
    if not isinstance(covered_scope, Mapping):
        return False
    if _normalize_text(covered_scope.get("family_parent_uid")) != family_parent_uid:
        return False
    if _normalize_text(covered_scope.get("workflow_kind")) != workflow_kind:
        return False
    if not _normalize_text_list(covered_scope.get("v2_owned_paths")):
        return False
    if not _normalize_text_list(covered_scope.get("operator_surfaces")):
        return False
    deletion_budget = migration_law.get("deletion_budget")
    if not isinstance(deletion_budget, Mapping):
        return False
    expected_removals = _normalize_text_list(deletion_budget.get("expected_removals"))
    if not expected_removals:
        return False
    try:
        minimum_removals = int(deletion_budget.get("minimum_removals"))
    except (TypeError, ValueError):
        return False
    if minimum_removals <= 0 or len(expected_removals) < minimum_removals:
        return False
    if not _normalize_text_list(migration_law.get("rollback_triggers")):
        return False
    residual_legacy_ownership = _normalize_text_list(migration_law.get("residual_legacy_ownership"))
    if not residual_legacy_ownership:
        return False
    return True


def _family_graph(compilation: Mapping[str, Any]) -> Dict[str, Any]:
    graph = compilation.get("family_graph") if isinstance(compilation.get("family_graph"), Mapping) else {}
    return dict(graph)


def _child_nodes(compilation: Mapping[str, Any]) -> List[Dict[str, Any]]:
    graph = _family_graph(compilation)
    order = _normalize_text_list(graph.get("recommended_execution_order"))
    nodes = []
    for raw in list(graph.get("nodes") or []):
        if not isinstance(raw, Mapping):
            continue
        node_kind = _normalize_text(raw.get("node_kind"))
        if node_kind in {"execution_child", "intent_validation_suite", "intent_family_closeout"}:
            nodes.append(dict(raw))
    order_index = {uid: idx for idx, uid in enumerate(order)}
    nodes.sort(key=lambda row: (order_index.get(_normalize_text(row.get("node_id")), 9999), _normalize_text(row.get("node_id"))))
    return nodes


def _job_specs_by_uid(compilation: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for spec in list(compilation.get("job_specs") or []):
        if not isinstance(spec, Mapping):
            continue
        binding = spec.get("binding") if isinstance(spec.get("binding"), Mapping) else {}
        uid = _normalize_text(binding.get("id"))
        if uid:
            out[uid] = dict(spec)
    return out


def _runtime_record_index(runtime_records: Sequence[Mapping[str, Any]] | Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    records = runtime_records if isinstance(runtime_records, Sequence) and not isinstance(runtime_records, Mapping) else [runtime_records]
    indexed: Dict[str, Dict[str, Any]] = {}
    for raw in records:
        if not isinstance(raw, Mapping):
            continue
        record = dict(raw)
        aliases = {
            _normalize_text(record.get("binding_uid")),
            _normalize_text(record.get("child_uid")),
            _normalize_text(record.get("job_id")),
            _normalize_text(record.get("subject_id")),
        }
        for alias in aliases:
            if alias:
                indexed[alias] = record
    return indexed


def _completed_child_uids(child_rows: Sequence[Mapping[str, Any]]) -> set[str]:
    return {
        _normalize_text(row.get("child_uid"))
        for row in child_rows
        if _normalize_text(row.get("child_status")) == "completed"
    }


def _surface_payloads(canonical: Mapping[str, Any]) -> Dict[str, Dict[str, Any]]:
    return {
        surface: {
            "family_parent_uid": _normalize_text(canonical.get("family_parent_uid")),
            "family_status": _normalize_text(canonical.get("family_status")),
            "current_stage": _normalize_text(canonical.get("current_stage")),
            "active_job": deepcopy(dict(canonical.get("active_job") or {})),
            "next_jobs": deepcopy(list(canonical.get("next_jobs") or [])),
            "blockers": deepcopy(list(canonical.get("blockers") or [])),
            "continuity": deepcopy(dict(canonical.get("continuity") or {})),
        }
        for surface in SURFACES
    }


def _comparable_projection_rows(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {
        "family_status": _normalize_text(payload.get("family_status")),
        "current_stage": _normalize_text(payload.get("current_stage")),
        "active_job.child_uid": _normalize_text((payload.get("active_job") or {}).get("child_uid")),
        "continuity.continuity_state": _normalize_text((payload.get("continuity") or {}).get("continuity_state")),
        "next_jobs": [_normalize_text(row.get("child_uid")) for row in list(payload.get("next_jobs") or [])],
        "blockers": [_normalize_text(row.get("reason")) for row in list(payload.get("blockers") or [])],
    }


def _append_projection_diffs(
    diffs: List[Dict[str, Any]],
    *,
    canonical_projection: Mapping[str, Any],
    compared_projection: Mapping[str, Any],
    source: str,
) -> None:
    canonical_rows = _comparable_projection_rows(canonical_projection)
    compared_rows = _comparable_projection_rows(compared_projection)
    for path in COMPARABLE_PROJECTION_PATHS:
        canonical_value = canonical_rows[path]
        compared_value = compared_rows[path]
        if canonical_value == compared_value:
            continue
        row = {
            "source": source,
            "path": path,
            "canonical": canonical_value,
            "observed": compared_value,
        }
        if source == "legacy_projection":
            row["legacy"] = compared_value
        diffs.append(row)


def _project_child(
    node: Mapping[str, Any],
    *,
    runtime_record: Mapping[str, Any],
    spec: Mapping[str, Any],
    completed_uids: set[str],
) -> Dict[str, Any]:
    child_uid = _normalize_text(node.get("node_id"))
    depends_on = _normalize_text_list(node.get("depends_on_uids"))
    runtime_state = _normalize_text(runtime_record.get("runtime_state") or runtime_record.get("last_status"))
    operator_block_reason = _normalize_text(runtime_record.get("operator_block_reason"))
    retired = bool(runtime_record.get("retired"))
    active = bool(runtime_record.get("active")) or bool(_normalize_text(runtime_record.get("active_run_id")))

    unmet_dependencies = [uid for uid in depends_on if uid not in completed_uids]
    if operator_block_reason or runtime_state in BLOCKED_RUNTIME_STATES:
        child_status = "blocked"
        reason = operator_block_reason or runtime_state or "blocked"
    elif retired:
        child_status = "completed"
        reason = runtime_state or "completed"
    elif runtime_state in FAILED_RUNTIME_STATES:
        child_status = "failed"
        reason = runtime_state
    elif active or runtime_state in ACTIVE_RUNTIME_STATES:
        child_status = "active"
        reason = runtime_state or "active"
    elif unmet_dependencies:
        child_status = "waiting"
        reason = f"dependency_pending:{unmet_dependencies[0]}"
    else:
        child_status = "ready"
        reason = "ready_from_canonical_runtime"

    return {
        "child_uid": child_uid,
        "job_id": _normalize_text(spec.get("job_id")),
        "title": _normalize_text(node.get("title")) or child_uid,
        "stage": _normalize_text(node.get("stage")),
        "node_kind": _normalize_text(node.get("node_kind")),
        "fanout_role": _normalize_text(node.get("fanout_role")),
        "depends_on_uids": list(depends_on),
        "unmet_dependencies": unmet_dependencies,
        "runtime_state": runtime_state,
        "child_status": child_status,
        "status_reason": reason,
        "retired": retired,
    }


def project_intent_family_v2_family(
    compilation: Mapping[str, Any],
    *,
    runtime_records: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    continuity_record: Mapping[str, Any] | None = None,
    latest_continuity_receipt: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    graph = _family_graph(compilation)
    child_nodes = _child_nodes(compilation)
    specs_by_uid = _job_specs_by_uid(compilation)
    runtime_by_alias = _runtime_record_index(runtime_records)

    provisional_rows: List[Dict[str, Any]] = []
    for node in child_nodes:
        child_uid = _normalize_text(node.get("node_id"))
        spec = specs_by_uid.get(child_uid, {})
        record = runtime_by_alias.get(child_uid) or runtime_by_alias.get(_normalize_text(spec.get("job_id"))) or {}
        provisional_rows.append(_project_child(node, runtime_record=record, spec=spec, completed_uids=set()))

    completed_uids = _completed_child_uids(provisional_rows)
    child_rows = []
    for node in child_nodes:
        child_uid = _normalize_text(node.get("node_id"))
        spec = specs_by_uid.get(child_uid, {})
        record = runtime_by_alias.get(child_uid) or runtime_by_alias.get(_normalize_text(spec.get("job_id"))) or {}
        child_rows.append(_project_child(node, runtime_record=record, spec=spec, completed_uids=completed_uids))

    active_child = next((row for row in child_rows if row["child_status"] == "active"), None)
    blocked_rows = [row for row in child_rows if row["child_status"] == "blocked"]
    failed_rows = [row for row in child_rows if row["child_status"] == "failed"]
    ready_rows = [row for row in child_rows if row["child_status"] == "ready"]
    completed_rows = [row for row in child_rows if row["child_status"] == "completed"]

    if failed_rows:
        family_status = "failed"
        current_stage = failed_rows[0]["stage"]
    elif blocked_rows:
        family_status = "blocked"
        current_stage = blocked_rows[0]["stage"]
    elif active_child is not None:
        family_status = "active"
        current_stage = active_child["stage"]
    elif len(completed_rows) == len(child_rows) and child_rows:
        family_status = "completed"
        current_stage = completed_rows[-1]["stage"]
    else:
        family_status = "queued"
        current_stage = ready_rows[0]["stage"] if ready_rows else (_normalize_text(child_rows[0]["stage"]) if child_rows else "")

    blockers = [
        {
            "child_uid": row["child_uid"],
            "stage": row["stage"],
            "reason": row["status_reason"],
        }
        for row in blocked_rows
    ]
    blockers.extend(
        {
            "child_uid": row["child_uid"],
            "stage": row["stage"],
            "reason": row["status_reason"],
        }
        for row in child_rows
        if row["child_status"] == "waiting"
    )

    continuity_projection = (
        v2_continuity.classify_continuity_from_durable_records(
            continuity_record or {},
            latest_receipt=latest_continuity_receipt,
        )
        if continuity_record
        else {}
    )
    validation_row = next((row for row in child_rows if row["node_kind"] == "intent_validation_suite"), {})
    closeout_row = next((row for row in child_rows if row["node_kind"] == "intent_family_closeout"), {})

    canonical = {
        "schema_version": SCHEMA_VERSION,
        "uid": UID,
        "workflow_kind": _normalize_text(compilation.get("workflow_definition_version")) or "intent_family_v2",
        "family_parent_uid": _normalize_text(compilation.get("parent_uid") or graph.get("parent_uid")),
        "source_intent_uid": _normalize_text(compilation.get("source_intent_uid") or graph.get("source_intent_uid")),
        "family_graph_fingerprint": _normalize_text(graph.get("graph_fingerprint")),
        "family_status": family_status,
        "current_stage": current_stage,
        "active_job": dict(active_child or {}),
        "next_jobs": [dict(row) for row in child_rows if row["child_status"] in {"ready", "waiting"}][:3],
        "blockers": blockers,
        "family_progress": {
            "completed_children": len(completed_rows),
            "total_children": len(child_rows),
            "active_children": sum(1 for row in child_rows if row["child_status"] == "active"),
            "blocked_children": len(blocked_rows),
            "failed_children": len(failed_rows),
        },
        "continuity": continuity_projection,
        "proof_summary": {
            "validation_child_uid": validation_row.get("child_uid", ""),
            "validation_status": validation_row.get("child_status", ""),
            "closeout_child_uid": closeout_row.get("child_uid", ""),
            "closeout_status": closeout_row.get("child_status", ""),
        },
        "children": child_rows,
    }
    canonical["surfaces"] = _surface_payloads(canonical)
    return canonical


def build_intent_family_v2_surface_bundle(
    compilation: Mapping[str, Any],
    *,
    runtime_records: Sequence[Mapping[str, Any]] | Mapping[str, Any],
    continuity_record: Mapping[str, Any] | None = None,
    latest_continuity_receipt: Mapping[str, Any] | None = None,
    legacy_projection: Mapping[str, Any] | None = None,
    compiled_shadow: Mapping[str, Any] | None = None,
    runtime_shadow: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    canonical = project_intent_family_v2_family(
        compilation,
        runtime_records=runtime_records,
        continuity_record=continuity_record,
        latest_continuity_receipt=latest_continuity_receipt,
    )
    surfaces = _surface_payloads(canonical)
    bundle = {
        "canonical_projection": canonical,
        "watch": deepcopy(surfaces["watch"]),
        "cli_summary": deepcopy(surfaces["cli_summary"]),
        "operator_snapshot": deepcopy(surfaces["operator_snapshot"]),
        "reconciliation_report": {
            **deepcopy(surfaces["reconciliation_report"]),
            "projection_source": "canonical_projection",
        },
    }
    if legacy_projection is not None or compiled_shadow is not None or runtime_shadow is not None:
        bundle["reconciliation_report"]["shadow_report"] = compare_intent_family_v2_shadow(
            canonical,
            legacy_projection=legacy_projection or {},
            compiled_shadow=compiled_shadow,
            runtime_shadow=runtime_shadow,
        )
    return bundle


def compare_intent_family_v2_shadow(
    canonical_projection: Mapping[str, Any],
    *,
    legacy_projection: Mapping[str, Any],
    compiled_shadow: Mapping[str, Any] | None = None,
    runtime_shadow: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    canonical_copy = deepcopy(dict(canonical_projection))
    legacy_copy = deepcopy(dict(legacy_projection))
    diffs: List[Dict[str, Any]] = []
    _append_projection_diffs(
        diffs,
        canonical_projection=canonical_copy,
        compared_projection=legacy_copy,
        source="legacy_projection",
    )
    if isinstance(compiled_shadow, Mapping):
        _append_projection_diffs(
            diffs,
            canonical_projection=canonical_copy,
            compared_projection=compiled_shadow,
            source="compiled_shadow",
        )
    if isinstance(runtime_shadow, Mapping):
        _append_projection_diffs(
            diffs,
            canonical_projection=canonical_copy,
            compared_projection=runtime_shadow,
            source="runtime_shadow",
        )

    status = "match" if not diffs else "mismatch"
    return {
        "schema_version": SHADOW_SCHEMA_VERSION,
        "uid": UID,
        "status": status,
        "parity_ok": not diffs,
        "diffs": diffs,
        "isolated_artifacts": {
            "canonical_projection": canonical_copy,
            "legacy_projection": legacy_copy,
            "compiled_shadow": deepcopy(dict(compiled_shadow or {})),
            "runtime_shadow": deepcopy(dict(runtime_shadow or {})),
        },
    }


def evaluate_intent_family_v2_canary_admission(
    family_projection: Mapping[str, Any],
    *,
    shadow_report: Mapping[str, Any],
    admission_request: Mapping[str, Any],
    latest_receipt: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    family_parent_uid = _normalize_text(family_projection.get("family_parent_uid"))
    workflow_kind = _normalize_text(family_projection.get("workflow_kind"))
    requested_scope = _normalize_text_list(admission_request.get("family_parent_uids"))
    request_id = _normalize_text(admission_request.get("request_id"))
    requested_owner = _normalize_text(admission_request.get("requested_owner")) or "intent_family_v2"
    legacy_owner = _normalize_text(admission_request.get("legacy_owner")) or "legacy_v1"
    requested_stage = _normalize_text(admission_request.get("requested_stage")) or "narrow_canary"
    receipt_state = _normalize_text((latest_receipt or {}).get("decision"))
    migration_law = _migration_law_from_admission_request(
        family_projection,
        admission_request=admission_request,
        legacy_owner=legacy_owner,
        requested_owner=requested_owner,
        requested_stage=requested_stage,
    )
    continuity_state = _normalize_text((family_projection.get("continuity") or {}).get("continuity_state"))

    if requested_stage not in ALLOWED_MIGRATION_STAGES:
        requested_stage = "narrow_canary"
        migration_law["requested_stage"] = requested_stage

    if receipt_state == "rolled_back":
        return {
            "family_parent_uid": family_parent_uid,
            "allowed": False,
            "decision": "rolled_back",
            "canonical_owner": legacy_owner,
            "requested_owner": requested_owner,
            "reason": _normalize_text((latest_receipt or {}).get("reason")) or "existing_rollback_receipt",
            "rollback_boundary": {
                "reverts_to_owner": legacy_owner,
                "scope": "family_only",
                "applies_to_future_work_only": True,
            },
            "migration_law": migration_law,
            "retirement_gate": {
                "eligible": False,
                "decision": "rolled_back",
                "reason": "existing_rollback_receipt",
            },
        }

    if not request_id:
        decision = "denied"
        reason = "explicit_request_id_required"
        allowed = False
        canonical_owner = legacy_owner
        admitted_stage = "shadow_only"
    elif requested_scope != [family_parent_uid]:
        decision = "denied"
        reason = "canary_scope_must_match_exactly_one_family"
        allowed = False
        canonical_owner = legacy_owner
        admitted_stage = "shadow_only"
    elif workflow_kind not in {"intent_family_v2", "intent_family_v2_workflow_definition_contract_v1"}:
        decision = "denied"
        reason = "workflow_kind_not_canary_eligible"
        allowed = False
        canonical_owner = legacy_owner
        admitted_stage = "shadow_only"
    elif not _migration_scope_is_explicit(migration_law, family_parent_uid=family_parent_uid, workflow_kind=workflow_kind):
        decision = "denied"
        reason = "covered_cutover_scope_must_be_explicit"
        allowed = False
        canonical_owner = legacy_owner
        admitted_stage = "blocked"
    elif _normalize_text(shadow_report.get("status")) != "match":
        decision = "denied"
        reason = "shadow_parity_not_earned"
        allowed = False
        canonical_owner = legacy_owner
        admitted_stage = "shadow_only"
    elif continuity_state not in CANARY_RUNNABLE_CONTINUITY_STATES:
        decision = "denied"
        reason = f"continuity_{continuity_state or 'unknown'}_blocks_canary"
        allowed = False
        canonical_owner = legacy_owner
        admitted_stage = "blocked"
    elif requested_stage == "replacement_owner":
        decision = "denied"
        reason = "replacement_ownership_requires_final_closeout"
        allowed = False
        canonical_owner = legacy_owner
        admitted_stage = "narrow_canary"
    else:
        decision = "admitted"
        reason = "narrow_shadow_parity_confirmed"
        allowed = True
        canonical_owner = requested_owner
        admitted_stage = "narrow_canary"

    migration_law["active_gate"] = admitted_stage
    migration_law["current_owner"] = canonical_owner
    migration_law["replacement_ownership"] = {
        "allowed": decision == "admitted" and requested_stage == "replacement_owner",
        "reason": (
            "final_closeout_controls_replacement_ownership"
            if requested_stage == "replacement_owner"
            else "narrow_canary_only_until_final_closeout"
        ),
    }
    migration_law["legacy_runtime_retirement"] = {
        "eligible": False,
        "reason": "final_closeout_and_deletion_budget_proof_required",
        "covered_scope_state": "explicit" if reason != "covered_cutover_scope_must_be_explicit" else "ambiguous",
    }

    return {
        "family_parent_uid": family_parent_uid,
        "allowed": allowed,
        "decision": decision,
        "canonical_owner": canonical_owner,
        "requested_owner": requested_owner,
        "legacy_owner": legacy_owner,
        "reason": reason,
        "rollback_boundary": {
            "reverts_to_owner": legacy_owner,
            "scope": "family_only",
            "applies_to_future_work_only": True,
        },
        "migration_law": migration_law,
        "retirement_gate": {
            "eligible": False,
            "decision": admitted_stage,
            "reason": migration_law["legacy_runtime_retirement"]["reason"],
        },
        "isolation": {
            "shadow_mutates_canonical_runtime": False,
            "legacy_truth_preserved_until_admitted": canonical_owner == legacy_owner,
        },
    }


def build_intent_family_v2_canary_receipt(
    admission: Mapping[str, Any],
    *,
    decision_id: str,
    decision_utc: str | None = None,
    evidence_refs: Sequence[str] | None = None,
) -> Dict[str, Any]:
    return {
        "schema_version": CANARY_RECEIPT_SCHEMA_VERSION,
        "uid": UID,
        "family_parent_uid": _normalize_text(admission.get("family_parent_uid")),
        "decision": _normalize_text(admission.get("decision")),
        "allowed": bool(admission.get("allowed")),
        "canonical_owner": _normalize_text(admission.get("canonical_owner")),
        "requested_owner": _normalize_text(admission.get("requested_owner")),
        "legacy_owner": _normalize_text(admission.get("legacy_owner")),
        "reason": _normalize_text(admission.get("reason")),
        "rollback_boundary": deepcopy(dict(admission.get("rollback_boundary") or {})),
        "migration_law": deepcopy(dict(admission.get("migration_law") or {})),
        "retirement_gate": deepcopy(dict(admission.get("retirement_gate") or {})),
        "isolation": deepcopy(dict(admission.get("isolation") or {})),
        "decision_id": _normalize_text(decision_id),
        "decision_utc": _normalize_text(decision_utc) or _utc_now(),
        "evidence_refs": [ref for ref in (_normalize_text(item) for item in list(evidence_refs or ())) if ref],
    }


def build_intent_family_v2_proof_report(
    compilation: Mapping[str, Any],
    canonical_projection: Mapping[str, Any],
    *,
    shadow_report: Mapping[str, Any],
    proof_case_id: str,
    proof_outcome: str,
    proof_case_utc: str | None = None,
    continuity_receipt: Mapping[str, Any] | None = None,
    repaired: bool = False,
    remaining_gaps: Sequence[str] | None = None,
    evidence_refs: Sequence[str] | None = None,
) -> Dict[str, Any]:
    family_parent_uid = _normalize_text(
        canonical_projection.get("family_parent_uid") or compilation.get("parent_uid")
    )
    if not family_parent_uid:
        raise ValueError("family_parent_uid is required for proof reports")
    normalized_case_id = _normalize_text(proof_case_id)
    if not normalized_case_id:
        raise ValueError("proof_case_id is required for proof reports")
    normalized_outcome = _normalize_text(proof_outcome)
    if normalized_outcome not in {"passed", "repair_required", "failed"}:
        raise ValueError("proof_outcome must be one of passed, repair_required, or failed")

    continuity = dict(canonical_projection.get("continuity") or {})
    if continuity_receipt is not None:
        continuity_receipt = dict(continuity_receipt)

    payload = {
        "schema_version": PROOF_REPORT_SCHEMA_VERSION,
        "uid": PROOF_REPORT_UID,
        "family_parent_uid": family_parent_uid,
        "source_intent_uid": _normalize_text(
            canonical_projection.get("source_intent_uid") or compilation.get("source_intent_uid")
        ),
        "proof_case_id": normalized_case_id,
        "proof_case_utc": _normalize_text(proof_case_utc) or _utc_now(),
        "proof_outcome": normalized_outcome,
        "repaired": bool(repaired),
        "remaining_gaps": [
            item for item in (_normalize_text(value) for value in list(remaining_gaps or ())) if item
        ],
        "evidence_refs": [
            item for item in (_normalize_text(value) for value in list(evidence_refs or ())) if item
        ],
        "compilation_summary": {
            "schema_version": _normalize_text(compilation.get("schema_version")),
            "workflow_definition_version": _normalize_text(compilation.get("workflow_definition_version")),
            "compilation_fingerprint": _normalize_text(compilation.get("compilation_fingerprint")),
            "graph_fingerprint": _normalize_text((compilation.get("family_graph") or {}).get("graph_fingerprint")),
        },
        "projection_summary": {
            "family_status": _normalize_text(canonical_projection.get("family_status")),
            "current_stage": _normalize_text(canonical_projection.get("current_stage")),
            "active_child_uid": _normalize_text((canonical_projection.get("active_job") or {}).get("child_uid")),
            "continuity_state": _normalize_text(continuity.get("continuity_state")),
            "continuity_decision_source": _normalize_text(continuity.get("decision_source")),
            "shadow_status": _normalize_text(shadow_report.get("status")),
        },
        "canonical_projection": deepcopy(dict(canonical_projection)),
        "shadow_report": deepcopy(dict(shadow_report)),
        "continuity_receipt": deepcopy(dict(continuity_receipt or {})),
    }
    payload["report_fingerprint"] = _fingerprint(payload)
    return payload


def canary_receipt_path(family_parent_uid: str, *, repo_root: Path | None = None, decision_id: str) -> Path:
    root = Path(repo_root or _repo_root()).resolve()
    safe_family = _safe_name(family_parent_uid)
    safe_decision = _safe_name(decision_id)
    return (root / CANARY_RECEIPTS_ROOT_REL / safe_family / f"{safe_decision}.json").resolve()


def write_intent_family_v2_canary_receipt(receipt: Mapping[str, Any], *, repo_root: Path | None = None) -> Path:
    family_parent_uid = _normalize_text(receipt.get("family_parent_uid"))
    decision_id = _normalize_text(receipt.get("decision_id"))
    if not family_parent_uid or not decision_id:
        raise ValueError("family_parent_uid and decision_id are required for canary receipts")
    path = canary_receipt_path(family_parent_uid, repo_root=repo_root, decision_id=decision_id)
    _write_json_atomic(path, receipt)
    return path


def proof_report_path(family_parent_uid: str, *, repo_root: Path | None = None, proof_case_id: str) -> Path:
    root = Path(repo_root or _repo_root()).resolve()
    return (root / PROOF_REPORTS_ROOT_REL / _safe_name(family_parent_uid) / f"{_safe_name(proof_case_id)}.json").resolve()


def write_intent_family_v2_proof_report(report: Mapping[str, Any], *, repo_root: Path | None = None) -> Path:
    family_parent_uid = _normalize_text(report.get("family_parent_uid"))
    proof_case_id = _normalize_text(report.get("proof_case_id"))
    if not family_parent_uid or not proof_case_id:
        raise ValueError("family_parent_uid and proof_case_id are required for proof reports")
    path = proof_report_path(family_parent_uid, repo_root=repo_root, proof_case_id=proof_case_id)
    normalized = dict(report)
    fingerprint = _normalize_text(normalized.get("report_fingerprint"))
    if not fingerprint:
        normalized["report_fingerprint"] = _fingerprint(normalized)
    elif fingerprint != _fingerprint({key: value for key, value in normalized.items() if key != "report_fingerprint"}):
        raise ValueError("report_fingerprint does not match proof report payload")
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_fingerprint = _normalize_text(existing.get("report_fingerprint"))
        if not existing_fingerprint:
            existing_fingerprint = _fingerprint(existing)
        if existing_fingerprint != normalized["report_fingerprint"]:
            raise ValueError("refusing to overwrite an existing proof report with different content")
        return path
    _write_json_atomic(path, normalized)
    return path


def intent_validation_artifact_path(
    family_parent_uid: str, *, repo_root: Path | None = None, validation_case_id: str
) -> Path:
    root = Path(repo_root or _repo_root()).resolve()
    return (
        root
        / INTENT_VALIDATION_ARTIFACTS_ROOT_REL
        / _safe_name(family_parent_uid)
        / f"{_safe_name(validation_case_id)}.json"
    ).resolve()


def build_intent_family_v2_intent_validation_artifact(
    compilation: Mapping[str, Any],
    *,
    validation_case_id: str,
    validation_outcome: str,
    requirement_coverage: Sequence[Mapping[str, Any]],
    scenario_results: Sequence[Mapping[str, Any]],
    validation_case_utc: str | None = None,
    summary: str | None = None,
    evidence_refs: Sequence[str] | None = None,
) -> Dict[str, Any]:
    family_parent_uid = _normalize_text(compilation.get("parent_uid"))
    source_intent_uid = _normalize_text(compilation.get("source_intent_uid"))
    normalized_case_id = _normalize_text(validation_case_id)
    normalized_outcome = _normalize_text(validation_outcome)
    if not family_parent_uid:
        raise ValueError("family_parent_uid is required for intent validation artifacts")
    if not source_intent_uid:
        raise ValueError("source_intent_uid is required for intent validation artifacts")
    if not normalized_case_id:
        raise ValueError("validation_case_id is required for intent validation artifacts")
    if normalized_outcome not in {"passed", "proof_gap", "contradicted"}:
        raise ValueError("validation_outcome must be one of passed, proof_gap, or contradicted")

    normalized_requirements: List[Dict[str, Any]] = []
    requirement_ids: List[str] = []
    for raw in requirement_coverage:
        requirement_id = _normalize_text(raw.get("requirement_id"))
        if not requirement_id:
            raise ValueError("requirement_coverage rows must include requirement_id")
        if requirement_id in requirement_ids:
            raise ValueError(f"duplicate requirement_id: {requirement_id}")
        status = _normalize_text(raw.get("status"))
        if status not in INTENT_VALIDATION_REQUIREMENT_STATUSES:
            raise ValueError(
                f"requirement {requirement_id} has invalid status {status!r}; "
                f"expected one of {sorted(INTENT_VALIDATION_REQUIREMENT_STATUSES)}"
            )
        intent_clause = _normalize_text(raw.get("intent_clause"))
        if not intent_clause:
            raise ValueError(f"requirement {requirement_id} must include intent_clause")
        notes = _normalize_text(raw.get("notes"))
        row = {
            "requirement_id": requirement_id,
            "intent_clause": intent_clause,
            "status": status,
            "observed_behavior": _normalize_text(raw.get("observed_behavior")),
            "evidence_refs": _normalize_text_list(raw.get("evidence_refs")),
            "scenario_ids": _normalize_text_list(raw.get("scenario_ids")),
        }
        if notes:
            row["notes"] = notes
        normalized_requirements.append(row)
        requirement_ids.append(requirement_id)

    if not normalized_requirements:
        raise ValueError("intent validation artifacts require at least one requirement_coverage row")

    normalized_scenarios: List[Dict[str, Any]] = []
    scenario_ids: List[str] = []
    scenario_kinds_seen: set[str] = set()
    referenced_requirement_ids: set[str] = set()
    for raw in scenario_results:
        scenario_id = _normalize_text(raw.get("scenario_id"))
        if not scenario_id:
            raise ValueError("scenario_results rows must include scenario_id")
        if scenario_id in scenario_ids:
            raise ValueError(f"duplicate scenario_id: {scenario_id}")
        scenario_kind = _normalize_text(raw.get("scenario_kind"))
        if scenario_kind not in REQUIRED_INTENT_VALIDATION_SCENARIO_KINDS:
            raise ValueError(
                f"scenario {scenario_id} has invalid scenario_kind {scenario_kind!r}; "
                f"expected one of {sorted(REQUIRED_INTENT_VALIDATION_SCENARIO_KINDS)}"
            )
        outcome = _normalize_text(raw.get("outcome"))
        if outcome not in INTENT_VALIDATION_SCENARIO_OUTCOMES:
            raise ValueError(
                f"scenario {scenario_id} has invalid outcome {outcome!r}; "
                f"expected one of {sorted(INTENT_VALIDATION_SCENARIO_OUTCOMES)}"
            )
        mapped_requirement_ids = _normalize_text_list(raw.get("mapped_requirement_ids"))
        if not mapped_requirement_ids:
            raise ValueError(f"scenario {scenario_id} must map to at least one requirement_id")
        unknown_requirement_ids = [item for item in mapped_requirement_ids if item not in requirement_ids]
        if unknown_requirement_ids:
            raise ValueError(
                f"scenario {scenario_id} references unknown requirement ids: {', '.join(unknown_requirement_ids)}"
            )
        observations = _normalize_text_list(raw.get("observations"))
        if not observations:
            raise ValueError(f"scenario {scenario_id} must include at least one observation")
        notes = _normalize_text(raw.get("notes"))
        row = {
            "scenario_id": scenario_id,
            "scenario_kind": scenario_kind,
            "outcome": outcome,
            "mapped_requirement_ids": mapped_requirement_ids,
            "observations": observations,
            "evidence_refs": _normalize_text_list(raw.get("evidence_refs")),
        }
        if notes:
            row["notes"] = notes
        normalized_scenarios.append(row)
        scenario_ids.append(scenario_id)
        scenario_kinds_seen.add(scenario_kind)
        referenced_requirement_ids.update(mapped_requirement_ids)

    if not normalized_scenarios:
        raise ValueError("intent validation artifacts require at least one scenario_results row")
    missing_kinds = sorted(REQUIRED_INTENT_VALIDATION_SCENARIO_KINDS - scenario_kinds_seen)
    if missing_kinds:
        raise ValueError(
            "intent validation artifacts must cover success_path, negative_path, mismatch_prone, and drift_detection; "
            f"missing: {', '.join(missing_kinds)}"
        )
    missing_requirement_coverage = [item for item in requirement_ids if item not in referenced_requirement_ids]
    if missing_requirement_coverage:
        raise ValueError(
            "every requirement must map to at least one scenario; missing: "
            + ", ".join(missing_requirement_coverage)
        )

    requirement_status_counts = _count_by_key(normalized_requirements, "status")
    scenario_outcome_counts = _count_by_key(normalized_scenarios, "outcome")
    has_gap = any(
        row["status"] in {"indirect", "unproven"} for row in normalized_requirements
    ) or any(row["outcome"] == "gap" for row in normalized_scenarios)
    has_contradiction = any(
        row["status"] == "contradicted" for row in normalized_requirements
    ) or any(row["outcome"] == "contradicted" for row in normalized_scenarios)

    if normalized_outcome == "passed":
        if has_gap or has_contradiction:
            raise ValueError("passed intent validation artifacts cannot contain gaps or contradictions")
    elif normalized_outcome == "proof_gap":
        if not has_gap or has_contradiction:
            raise ValueError("proof_gap intent validation artifacts must contain a gap and no contradictions")
    elif not has_contradiction:
        raise ValueError("contradicted intent validation artifacts must contain at least one contradiction")

    payload = {
        "schema_version": INTENT_VALIDATION_ARTIFACT_SCHEMA_VERSION,
        "uid": INTENT_VALIDATION_UID,
        "family_parent_uid": family_parent_uid,
        "source_intent_uid": source_intent_uid,
        "validation_case_id": normalized_case_id,
        "validation_case_utc": _normalize_text(validation_case_utc) or _utc_now(),
        "validation_outcome": normalized_outcome,
        "summary": _normalize_text(summary),
        "evidence_refs": [
            item for item in (_normalize_text(value) for value in list(evidence_refs or ())) if item
        ],
        "compilation_summary": {
            "schema_version": _normalize_text(compilation.get("schema_version")),
            "workflow_definition_version": _normalize_text(compilation.get("workflow_definition_version")),
            "compilation_fingerprint": _normalize_text(compilation.get("compilation_fingerprint")),
        },
        "requirement_coverage": normalized_requirements,
        "scenario_results": normalized_scenarios,
        "coverage_summary": {
            "requirement_count": len(normalized_requirements),
            "scenario_count": len(normalized_scenarios),
            "requirement_status_counts": requirement_status_counts,
            "scenario_outcome_counts": scenario_outcome_counts,
            "covered_scenario_kinds": sorted(scenario_kinds_seen),
        },
    }
    payload["artifact_fingerprint"] = _fingerprint(payload)
    return payload


def write_intent_family_v2_intent_validation_artifact(
    artifact: Mapping[str, Any], *, repo_root: Path | None = None
) -> Path:
    family_parent_uid = _normalize_text(artifact.get("family_parent_uid"))
    validation_case_id = _normalize_text(artifact.get("validation_case_id"))
    if not family_parent_uid or not validation_case_id:
        raise ValueError("family_parent_uid and validation_case_id are required for intent validation artifacts")
    path = intent_validation_artifact_path(
        family_parent_uid, repo_root=repo_root, validation_case_id=validation_case_id
    )
    normalized = dict(artifact)
    fingerprint = _normalize_text(normalized.get("artifact_fingerprint"))
    if not fingerprint:
        normalized["artifact_fingerprint"] = _fingerprint(normalized)
    elif fingerprint != _fingerprint({key: value for key, value in normalized.items() if key != "artifact_fingerprint"}):
        raise ValueError("artifact_fingerprint does not match intent validation artifact payload")
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_fingerprint = _normalize_text(existing.get("artifact_fingerprint"))
        if not existing_fingerprint:
            existing_fingerprint = _fingerprint(existing)
        if existing_fingerprint != normalized["artifact_fingerprint"]:
            raise ValueError("refusing to overwrite an existing intent validation artifact with different content")
        return path
    _write_json_atomic(path, normalized)
    return path
