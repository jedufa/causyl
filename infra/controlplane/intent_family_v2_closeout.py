"""Fail-closed closeout audit for intent_family_v2 families."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Sequence


SCHEMA_VERSION = "intent_family_v2_closeout_audit_v1"
UID = "PIS-20260409-195248Z-4066"
ALLOWED_VALIDATION_GAP_IDS = frozenset({"closeout_only_promotion"})


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


def _normalize_deletion_budget(value: Any) -> Dict[str, Any]:
    if not isinstance(value, Mapping):
        return {}
    return {
        "expected_removals": _normalize_text_list(value.get("expected_removals")),
        "minimum_removals": value.get("minimum_removals"),
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _requirement_statuses(validation_artifact: Mapping[str, Any]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for raw in list(validation_artifact.get("requirement_coverage") or []):
        if not isinstance(raw, Mapping):
            continue
        requirement_id = _normalize_text(raw.get("requirement_id"))
        if requirement_id:
            out[requirement_id] = _normalize_text(raw.get("status"))
    return out


def _validation_gaps(
    validation_artifact: Mapping[str, Any] | None,
    *,
    family_parent_uid: str,
    source_intent_uid: str,
    expected_compilation_fingerprint: str,
) -> List[str]:
    if not isinstance(validation_artifact, Mapping) or not validation_artifact:
        return ["intent_validation_artifact_missing"]
    outcome = _normalize_text(validation_artifact.get("validation_outcome"))
    gaps: List[str] = []
    if _normalize_text(validation_artifact.get("family_parent_uid")) != family_parent_uid:
        gaps.append("intent_validation_family_parent_mismatch")
    if _normalize_text(validation_artifact.get("source_intent_uid")) != source_intent_uid:
        gaps.append("intent_validation_source_intent_mismatch")
    artifact_fingerprint = _normalize_text(
        ((validation_artifact.get("compilation_summary") if isinstance(validation_artifact.get("compilation_summary"), Mapping) else {}) or {}).get("compilation_fingerprint")
    )
    if expected_compilation_fingerprint and artifact_fingerprint != expected_compilation_fingerprint:
        gaps.append("intent_validation_compilation_mismatch")
    if outcome == "contradicted":
        gaps.append("intent_validation_contradicted")
        return gaps

    statuses = _requirement_statuses(validation_artifact)
    if not statuses:
        gaps.append("intent_validation_requirements_missing")
        return gaps

    for requirement_id, status in statuses.items():
        if status in {"proven", "indirect"}:
            continue
        if requirement_id in ALLOWED_VALIDATION_GAP_IDS and status == "unproven" and outcome == "proof_gap":
            continue
        gaps.append(f"intent_validation_{requirement_id}_{status or 'missing'}")
    return gaps


def _proof_gaps(
    proof_report: Mapping[str, Any] | None,
    *,
    family_parent_uid: str,
    source_intent_uid: str,
    expected_compilation_fingerprint: str,
) -> List[str]:
    if not isinstance(proof_report, Mapping) or not proof_report:
        return ["implementation_proof_report_missing"]
    gaps: List[str] = []
    if _normalize_text(proof_report.get("family_parent_uid")) != family_parent_uid:
        gaps.append("implementation_proof_family_parent_mismatch")
    if _normalize_text(proof_report.get("source_intent_uid")) != source_intent_uid:
        gaps.append("implementation_proof_source_intent_mismatch")
    proof_fingerprint = _normalize_text(
        ((proof_report.get("compilation_summary") if isinstance(proof_report.get("compilation_summary"), Mapping) else {}) or {}).get("compilation_fingerprint")
    )
    if expected_compilation_fingerprint and proof_fingerprint != expected_compilation_fingerprint:
        gaps.append("implementation_proof_compilation_mismatch")
    if _normalize_text(proof_report.get("proof_outcome")) != "passed":
        gaps.append(f"implementation_proof_{_normalize_text(proof_report.get('proof_outcome')) or 'invalid'}")
        return gaps
    projection_summary = proof_report.get("projection_summary")
    if isinstance(projection_summary, Mapping):
        if _normalize_text(projection_summary.get("shadow_status")) != "match":
            gaps.append("implementation_proof_shadow_mismatch")
    return gaps


def _migration_scope_is_explicit(
    migration_law: Mapping[str, Any], *, family_parent_uid: str, accepted_workflow_kinds: Sequence[str]
) -> bool:
    covered_scope = migration_law.get("covered_cutover_scope")
    if not isinstance(covered_scope, Mapping):
        return False
    if _normalize_text(covered_scope.get("family_parent_uid")) != family_parent_uid:
        return False
    if _normalize_text(covered_scope.get("workflow_kind")) not in set(accepted_workflow_kinds):
        return False
    if not _normalize_text_list(covered_scope.get("v2_owned_paths")):
        return False
    if not _normalize_text_list(covered_scope.get("operator_surfaces")):
        return False
    deletion_budget = _normalize_deletion_budget(migration_law.get("deletion_budget"))
    expected_removals = deletion_budget.get("expected_removals") or []
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
    if not _normalize_text_list(migration_law.get("residual_legacy_ownership")):
        return False
    return True


def _migration_gaps(
    migration_law: Mapping[str, Any] | None,
    *,
    required_owner_uid: str,
    family_parent_uid: str,
    accepted_workflow_kinds: Sequence[str],
) -> List[str]:
    if not isinstance(migration_law, Mapping) or not migration_law:
        return ["migration_law_missing"]
    gaps: List[str] = []
    if _normalize_text(migration_law.get("final_promotion_owner_uid")) != required_owner_uid:
        gaps.append("migration_law_final_owner_mismatch")
    if not _migration_scope_is_explicit(
        migration_law, family_parent_uid=family_parent_uid, accepted_workflow_kinds=accepted_workflow_kinds
    ):
        covered_scope = migration_law.get("covered_cutover_scope")
        if not isinstance(covered_scope, Mapping):
            gaps.append("migration_law_scope_missing")
        else:
            if _normalize_text(covered_scope.get("family_parent_uid")) != family_parent_uid:
                gaps.append("migration_law_family_scope_mismatch")
            if _normalize_text(covered_scope.get("workflow_kind")) not in set(accepted_workflow_kinds):
                gaps.append("migration_law_workflow_kind_mismatch")
            if not _normalize_text_list(covered_scope.get("v2_owned_paths")):
                gaps.append("migration_law_v2_owned_paths_missing")
            if not _normalize_text_list(covered_scope.get("operator_surfaces")):
                gaps.append("migration_law_operator_surfaces_missing")
        deletion_budget = _normalize_deletion_budget(migration_law.get("deletion_budget"))
        expected_removals = deletion_budget.get("expected_removals") or []
        try:
            minimum_removals = int(deletion_budget.get("minimum_removals"))
        except (TypeError, ValueError):
            minimum_removals = 0
        if not expected_removals or minimum_removals <= 0 or len(expected_removals) < minimum_removals:
            gaps.append("migration_law_deletion_budget_invalid")
        if not _normalize_text_list(migration_law.get("rollback_triggers")):
            gaps.append("migration_law_rollback_triggers_missing")
        if not _normalize_text_list(migration_law.get("residual_legacy_ownership")):
            gaps.append("migration_law_residual_legacy_ownership_missing")
    if _normalize_text_list(migration_law.get("unresolved_gaps")):
        gaps.append("migration_law_unresolved_gaps_present")
    return gaps


def _child_rows(child_status_rows: Sequence[Mapping[str, Any]], *, required_final_child_uid: str) -> tuple[list[dict[str, str]], list[str]]:
    normalized_rows: List[Dict[str, str]] = []
    blocking: List[str] = []
    for raw in child_status_rows:
        if not isinstance(raw, Mapping):
            continue
        uid = _normalize_text(raw.get("uid") or raw.get("child_uid"))
        if not uid:
            continue
        status = _normalize_text(raw.get("status") or raw.get("child_status"))
        title = _normalize_text(raw.get("title"))
        relationship = _normalize_text(raw.get("relationship") or raw.get("node_kind"))
        normalized_rows.append(
            {
                "uid": uid,
                "title": title,
                "relationship": relationship,
                "status": status,
            }
        )
        if uid == required_final_child_uid:
            continue
        if status != "verified":
            blocking.append(f"child_{uid}_status_{status or 'missing'}")
    normalized_rows.sort(key=lambda row: row["uid"])
    return normalized_rows, blocking


def build_intent_family_v2_closeout_audit(
    compilation: Mapping[str, Any],
    *,
    child_status_rows: Sequence[Mapping[str, Any]],
    proof_report: Mapping[str, Any] | None = None,
    intent_validation_artifact: Mapping[str, Any] | None = None,
    migration_law: Mapping[str, Any] | None = None,
    repaired_findings: Sequence[str] | None = None,
    rerun_commands: Sequence[str] | None = None,
    rerun_utc: str | None = None,
    evidence_refs: Sequence[str] | None = None,
) -> Dict[str, Any]:
    family_graph = compilation.get("family_graph") if isinstance(compilation.get("family_graph"), Mapping) else {}
    family_parent_uid = _normalize_text(compilation.get("parent_uid") or family_graph.get("parent_uid"))
    source_intent_uid = _normalize_text(compilation.get("source_intent_uid") or family_graph.get("source_intent_uid"))
    accepted_workflow_kinds = tuple(
        item
        for item in (
            _normalize_text(family_graph.get("workflow_kind")),
            _normalize_text(compilation.get("workflow_kind")),
            _normalize_text(compilation.get("workflow_definition_version")),
            "intent_family_v2",
        )
        if item
    )
    required_final_child_uid = _normalize_text(
        family_graph.get("required_final_child_uid") or compilation.get("required_final_child_uid")
    )
    if not family_parent_uid:
        raise ValueError("family_parent_uid is required")
    if not source_intent_uid:
        raise ValueError("source_intent_uid is required")
    if not required_final_child_uid:
        raise ValueError("required_final_child_uid is required")

    normalized_children, child_gaps = _child_rows(
        child_status_rows, required_final_child_uid=required_final_child_uid
    )
    expected_compilation_fingerprint = _normalize_text(compilation.get("compilation_fingerprint"))
    validation_gaps = _validation_gaps(
        intent_validation_artifact,
        family_parent_uid=family_parent_uid,
        source_intent_uid=source_intent_uid,
        expected_compilation_fingerprint=expected_compilation_fingerprint,
    )
    proof_gaps = _proof_gaps(
        proof_report,
        family_parent_uid=family_parent_uid,
        source_intent_uid=source_intent_uid,
        expected_compilation_fingerprint=expected_compilation_fingerprint,
    )
    migration_gaps = _migration_gaps(
        migration_law,
        required_owner_uid=UID,
        family_parent_uid=family_parent_uid,
        accepted_workflow_kinds=accepted_workflow_kinds,
    )
    repaired = _normalize_text_list(list(repaired_findings or ()))
    reruns = _normalize_text_list(list(rerun_commands or ()))
    refs = _normalize_text_list(list(evidence_refs or ()))

    remaining_gaps = child_gaps + proof_gaps + validation_gaps + migration_gaps
    decision = "verified" if not remaining_gaps else "not_closable"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "uid": UID,
        "family_parent_uid": family_parent_uid,
        "source_intent_uid": source_intent_uid,
        "required_final_child_uid": required_final_child_uid,
        "decision": decision,
        "status_recommendation": "verified" if decision == "verified" else "draft",
        "promotion_decision": (
            "promote_parent_and_source_intent" if decision == "verified" else "leave_family_open"
        ),
        "repaired_findings": repaired,
        "rerun_utc": _normalize_text(rerun_utc) or _utc_now(),
        "rerun_commands": reruns,
        "evidence_refs": refs,
        "child_status_rows": normalized_children,
        "remaining_gaps": remaining_gaps,
        "proof_summary": {
            "proof_outcome": _normalize_text((proof_report or {}).get("proof_outcome")),
            "validation_outcome": _normalize_text((intent_validation_artifact or {}).get("validation_outcome")),
            "migration_owner_uid": _normalize_text((migration_law or {}).get("final_promotion_owner_uid")),
        },
        "summary": (
            "Closeout may promote the parent and source intent."
            if decision == "verified"
            else "Closeout must leave the family open until the remaining gaps are resolved."
        ),
    }
    payload["audit_fingerprint"] = _fingerprint(payload)
    return payload


def render_intent_family_v2_closeout_audit_markdown(audit: Mapping[str, Any]) -> str:
    lines = [
        "# Intent Family V2 Closeout Audit",
        "",
        f"- Closeout UID: `{_normalize_text(audit.get('uid'))}`",
        f"- Parent UID: `{_normalize_text(audit.get('family_parent_uid'))}`",
        f"- Required final child UID: `{_normalize_text(audit.get('required_final_child_uid'))}`",
        f"- Source intent UID: `{_normalize_text(audit.get('source_intent_uid'))}`",
        f"- Audit UTC: `{_normalize_text(audit.get('rerun_utc'))}`",
        f"- Decision: `{_normalize_text(audit.get('decision'))}`",
        "",
        "## Summary",
        "",
        _normalize_text(audit.get("summary")),
        "",
        "## Bounded repair follow-through",
        "",
    ]
    repaired = _normalize_text_list(audit.get("repaired_findings"))
    if repaired:
        lines.extend(f"- {item}" for item in repaired)
    else:
        lines.append("- No bounded repair was required inside closeout scope.")
    lines.extend(["", "## Child status snapshot", ""])
    for row in list(audit.get("child_status_rows") or []):
        if not isinstance(row, Mapping):
            continue
        lines.append(
            f"- `{_normalize_text(row.get('uid'))}` `{_normalize_text(row.get('relationship')) or 'child'}`: "
            f"`{_normalize_text(row.get('status')) or 'missing'}`"
        )
    lines.extend(["", "## Validation rerun", ""])
    rerun_commands = _normalize_text_list(audit.get("rerun_commands"))
    if rerun_commands:
        lines.extend(f"- `{command}`" for command in rerun_commands)
    else:
        lines.append("- No rerun commands recorded.")
    lines.extend(["", "## Remaining gaps", ""])
    remaining_gaps = _normalize_text_list(audit.get("remaining_gaps"))
    if remaining_gaps:
        lines.extend(f"- `{gap}`" for gap in remaining_gaps)
    else:
        lines.append("- None.")
    lines.extend(["", "## Evidence refs", ""])
    evidence_refs = _normalize_text_list(audit.get("evidence_refs"))
    if evidence_refs:
        lines.extend(f"- `{ref}`" for ref in evidence_refs)
    else:
        lines.append("- None.")
    lines.extend(
        [
            "",
            "## Closeout decision",
            "",
            (
                "Promote the parent family and source intent."
                if _normalize_text(audit.get("decision")) == "verified"
                else "Do not promote the parent family or source intent from this closeout attempt."
            ),
        ]
    )
    return "\n".join(lines) + "\n"
