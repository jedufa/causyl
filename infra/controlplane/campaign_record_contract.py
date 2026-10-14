"""Canonical first-release campaign record and phase-graph contract helpers."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence, Tuple

from infra.controlplane import evidence_pack_handoff_refs, job_shape_contract


SCHEMA_VERSION = "campaign_record_contract_v1"
CONTRACT_ARTIFACT_REL = Path("governance/campaign_record_contract_v1.json")
CONTRACT_DOC_REL = Path("docs/contracts/contract_campaign_record_schema_v1.md")
UID = "PIS-20260409-121600Z-J8M2"

CAMPAIGN_LIFECYCLE_STATES: Tuple[str, ...] = (
    "draft",
    "active",
    "blocked",
    "review",
    "completed",
    "failed",
    "cancelled",
)
PHASE_LIFECYCLE_STATES: Tuple[str, ...] = (
    "declared",
    "running",
    "blocked",
    "succeeded",
    "failed",
    "cancelled",
)
TERMINAL_PHASE_STATES: Tuple[str, ...] = ("succeeded", "failed", "cancelled")
TERMINAL_CAMPAIGN_STATES: Tuple[str, ...] = ("completed", "failed", "cancelled")
CAMPAIGN_REQUIRED_FIELDS: Tuple[str, ...] = (
    "schema_version",
    "campaign_id",
    "parent_binding",
    "campaign_state",
    "campaign_truth_refs",
    "record_event_refs",
    "phases",
)
PHASE_REQUIRED_FIELDS: Tuple[str, ...] = (
    "phase_id",
    "label",
    "job_shape",
    "state",
    "depends_on",
    "output_refs",
    "truth_refs",
    "phase_event_refs",
)
TRUTH_REF_FIELDS: Tuple[str, ...] = (
    "ledger_uids",
    "job_ids",
    "run_ids",
    "receipt_refs",
    "continuation_packet_ids",
    "artifact_refs",
)
FORBIDDEN_INLINE_TRUTH_FIELDS: Tuple[str, ...] = (
    "task_register_snapshot",
    "task_records",
    "job_receipts",
    "receipt_payloads",
    "transcript_summary",
    "transcript_summaries",
)
FORBIDDEN_PHASE_INLINE_FIELDS: Tuple[str, ...] = (
    "task_snapshot",
    "task_record",
    "receipt_payload",
    "receipt_body",
    "transcript_summary",
)
APPEND_ONLY_FIELDS: Tuple[str, ...] = ("record_event_refs", "phase_event_refs")
ALLOW_EMPTY_PHASE_LIST_FIELDS = frozenset({"depends_on", "output_refs", "phase_event_refs"})


@dataclass(frozen=True)
class CampaignRecordContractV1:
    version: str
    uid: str
    canonical_artifact_rel: Path
    canonical_doc_rel: Path
    campaign_lifecycle_states: Tuple[str, ...]
    phase_lifecycle_states: Tuple[str, ...]
    terminal_phase_states: Tuple[str, ...]
    append_only_fields: Tuple[str, ...]


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


def _normalize_truth_refs(value: Any) -> Dict[str, List[str]]:
    row = value if isinstance(value, Mapping) else {}
    return {field: _normalize_text_list(row.get(field)) for field in TRUTH_REF_FIELDS}


def _normalize_parent_binding(value: Any) -> Dict[str, str]:
    row = value if isinstance(value, Mapping) else {}
    return {
        "binding_type": _normalize_text(row.get("binding_type")).lower(),
        "binding_id": _normalize_text(row.get("binding_id")),
    }


def phase_terminal_class(state: str) -> str:
    normalized = _normalize_text(state).lower()
    if normalized == "succeeded":
        return "success_terminal"
    if normalized == "failed":
        return "failure_terminal"
    if normalized == "cancelled":
        return "cancelled_terminal"
    return "non_terminal"


def campaign_terminal_class(state: str) -> str:
    normalized = _normalize_text(state).lower()
    if normalized == "completed":
        return "success_terminal"
    if normalized == "failed":
        return "failure_terminal"
    if normalized == "cancelled":
        return "cancelled_terminal"
    return "non_terminal"


def normalize_phase_node(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    job_shape = _normalize_text(obj.get("job_shape"))
    return {
        "phase_id": _normalize_text(obj.get("phase_id")),
        "label": _normalize_text(obj.get("label")),
        "job_shape": job_shape,
        "state": _normalize_text(obj.get("state")).lower(),
        "depends_on": _normalize_text_list(obj.get("depends_on")),
        "output_refs": _normalize_text_list(obj.get("output_refs")),
        "truth_refs": _normalize_truth_refs(obj.get("truth_refs")),
        "evidence_pack_refs": evidence_pack_handoff_refs.normalize_evidence_pack_refs(obj.get("evidence_pack_refs")),
        "phase_event_refs": _normalize_text_list(obj.get("phase_event_refs")),
        "terminal_class": phase_terminal_class(_normalize_text(obj.get("state")).lower()),
    }


def normalize_campaign_record(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    phases = [normalize_phase_node(row) for row in list(obj.get("phases") or []) if isinstance(row, Mapping)]
    phases.sort(key=lambda row: (row["phase_id"], row["label"]))
    return {
        "schema_version": _normalize_text(obj.get("schema_version") or SCHEMA_VERSION),
        "campaign_id": _normalize_text(obj.get("campaign_id")),
        "parent_binding": _normalize_parent_binding(obj.get("parent_binding")),
        "campaign_state": _normalize_text(obj.get("campaign_state")).lower(),
        "campaign_truth_refs": _normalize_truth_refs(obj.get("campaign_truth_refs")),
        "evidence_pack_refs": evidence_pack_handoff_refs.normalize_evidence_pack_refs(obj.get("evidence_pack_refs")),
        "record_event_refs": _normalize_text_list(obj.get("record_event_refs")),
        "phases": phases,
        "campaign_terminal_class": campaign_terminal_class(_normalize_text(obj.get("campaign_state")).lower()),
    }


def _canonical_json(value: Mapping[str, Any]) -> str:
    return json.dumps(dict(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _fingerprint(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _has_nonempty_truth_ref(value: Mapping[str, Sequence[str]]) -> bool:
    return any(bool(list(value.get(field) or ())) for field in TRUTH_REF_FIELDS)


def _cycle_detect(phases: Sequence[Mapping[str, Any]]) -> bool:
    phase_map = {str(row.get("phase_id") or ""): list(row.get("depends_on") or []) for row in phases}
    visiting: set[str] = set()
    visited: set[str] = set()

    def _walk(node: str) -> bool:
        if node in visited:
            return False
        if node in visiting:
            return True
        visiting.add(node)
        for dep in phase_map.get(node, []):
            if dep in phase_map and _walk(dep):
                return True
        visiting.remove(node)
        visited.add(node)
        return False

    return any(_walk(node) for node in phase_map)


def campaign_record_contract_v1() -> CampaignRecordContractV1:
    return CampaignRecordContractV1(
        version=SCHEMA_VERSION,
        uid=UID,
        canonical_artifact_rel=CONTRACT_ARTIFACT_REL,
        canonical_doc_rel=CONTRACT_DOC_REL,
        campaign_lifecycle_states=CAMPAIGN_LIFECYCLE_STATES,
        phase_lifecycle_states=PHASE_LIFECYCLE_STATES,
        terminal_phase_states=TERMINAL_PHASE_STATES,
        append_only_fields=APPEND_ONLY_FIELDS,
    )


def load_campaign_record_contract(*, repo_root: Path | None = None) -> Dict[str, Any]:
    root = Path(repo_root) if repo_root is not None else _repo_root()
    with (root / CONTRACT_ARTIFACT_REL).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def validate_campaign_record(raw_record: Mapping[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    raw = dict(raw_record or {})
    normalized = normalize_campaign_record(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    for field in CAMPAIGN_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", [], {}) or value is None:
            errors.append({"field": field, "code": "missing_field", "message": f"campaign record requires {field}"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append({"field": "schema_version", "code": "invalid_value", "message": "campaign record schema_version drifted"})
    if normalized["campaign_state"] not in CAMPAIGN_LIFECYCLE_STATES:
        errors.append({"field": "campaign_state", "code": "invalid_value", "message": "illegal campaign lifecycle state"})

    binding = normalized["parent_binding"]
    if binding["binding_type"] != "ledger_uid":
        errors.append(
            {
                "field": "parent_binding.binding_type",
                "code": "invalid_value",
                "message": "campaign parent binding must point at canonical ledger truth via ledger_uid",
            }
        )
    if not binding["binding_id"]:
        errors.append(
            {"field": "parent_binding.binding_id", "code": "missing_field", "message": "campaign parent binding requires a UID"}
        )

    if not _has_nonempty_truth_ref(normalized["campaign_truth_refs"]):
        errors.append(
            {
                "field": "campaign_truth_refs",
                "code": "invalid_value",
                "message": "campaign record must link back to canonical lower-layer truth refs",
            }
        )

    for field in FORBIDDEN_INLINE_TRUTH_FIELDS:
        if field in raw and raw.get(field) not in (None, "", [], {}, ()):
            errors.append(
                {
                    "field": field,
                    "code": "forbidden_inline_truth",
                    "message": "campaign record must not inline task or receipt truth",
                }
            )

    if not normalized["record_event_refs"]:
        errors.append(
            {
                "field": "record_event_refs",
                "code": "invalid_value",
                "message": "campaign record requires at least one append-only event ref",
            }
        )

    errors.extend(
        evidence_pack_handoff_refs.validate_evidence_pack_refs(
            normalized["evidence_pack_refs"],
            repo_root=repo_root,
            field_prefix="evidence_pack_refs",
        )
    )

    phase_ids: List[str] = []
    for index, raw_phase in enumerate(list(raw.get("phases") or [])):
        if not isinstance(raw_phase, Mapping):
            errors.append({"field": f"phases[{index}]", "code": "invalid_type", "message": "phase entry must be an object"})
            continue
        phase = normalize_phase_node(raw_phase)
        for field in PHASE_REQUIRED_FIELDS:
            value = phase.get(field)
            if value in ("", {}) or value is None or (value == [] and field not in ALLOW_EMPTY_PHASE_LIST_FIELDS):
                errors.append(
                    {"field": f"phases[{index}].{field}", "code": "missing_field", "message": f"phase requires {field}"}
                )
        if phase["state"] not in PHASE_LIFECYCLE_STATES:
            errors.append(
                {"field": f"phases[{index}].state", "code": "invalid_value", "message": "illegal phase lifecycle state"}
            )
        try:
            job_shape_contract.canonical_job_shape_name(phase["job_shape"])
        except KeyError:
            errors.append(
                {"field": f"phases[{index}].job_shape", "code": "invalid_value", "message": "phase job_shape must be canonical"}
            )
        if phase["phase_id"] in phase_ids:
            errors.append(
                {"field": f"phases[{index}].phase_id", "code": "duplicate_phase_id", "message": "phase_id must be unique"}
            )
        if phase["phase_id"]:
            phase_ids.append(phase["phase_id"])
        for dep in phase["depends_on"]:
            if dep == phase["phase_id"]:
                errors.append(
                    {"field": f"phases[{index}].depends_on", "code": "self_edge", "message": "phase may not depend on itself"}
                )
        for forbidden in FORBIDDEN_PHASE_INLINE_FIELDS:
            if forbidden in raw_phase and raw_phase.get(forbidden) not in (None, "", [], {}, ()):
                errors.append(
                    {
                        "field": f"phases[{index}].{forbidden}",
                        "code": "forbidden_inline_truth",
                        "message": "phase record must carry refs, not embedded task or receipt truth",
                    }
                )
        errors.extend(
            evidence_pack_handoff_refs.validate_evidence_pack_refs(
                phase["evidence_pack_refs"],
                repo_root=repo_root,
                field_prefix=f"phases[{index}].evidence_pack_refs",
            )
        )
        for key in raw_phase.keys():
            if key not in PHASE_REQUIRED_FIELDS and key not in {"terminal_class", "evidence_pack_refs"}:
                warnings.append(
                    {
                        "field": f"phases[{index}].{key}",
                        "code": "unknown_field",
                        "message": "phase field is outside the v1 contract and ignored for canonicalization",
                    }
                )

    normalized_phase_map = {phase["phase_id"]: phase for phase in normalized["phases"] if phase["phase_id"]}
    for phase in normalized["phases"]:
        for dep in phase["depends_on"]:
            if dep not in normalized_phase_map:
                errors.append(
                    {
                        "field": f"phase:{phase['phase_id']}.depends_on",
                        "code": "undeclared_edge_target",
                        "message": f"dependency target {dep} is not declared in the phase graph",
                    }
                )
    if normalized["phases"] and _cycle_detect(normalized["phases"]):
        errors.append({"field": "phases", "code": "cyclic_graph", "message": "phase graph must remain acyclic in v1"})

    for key in raw.keys():
        if key not in CAMPAIGN_REQUIRED_FIELDS and key not in {"campaign_terminal_class", "evidence_pack_refs"}:
            warnings.append(
                {
                    "field": key,
                    "code": "unknown_field",
                    "message": "campaign field is outside the v1 contract and ignored for canonicalization",
                }
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "normalized_record": normalized,
        "computed_fingerprint": _fingerprint(normalized),
    }


def validate_campaign_record_contract_v1(*, repo_root: Path | None = None) -> Tuple[str, ...]:
    payload = load_campaign_record_contract(repo_root=repo_root)
    issues = []

    if str(payload.get("schema_version") or "") != SCHEMA_VERSION:
        issues.append("schema_version must remain campaign_record_contract_v1")
    if str(payload.get("uid") or "") != UID:
        issues.append("uid must remain bound to PIS-20260409-121600Z-J8M2")
    if str(payload.get("canonical_doc_rel") or "") != CONTRACT_DOC_REL.as_posix():
        issues.append("canonical_doc_rel must point at the campaign record contract doc")
    if tuple(payload.get("campaign_lifecycle_states") or ()) != CAMPAIGN_LIFECYCLE_STATES:
        issues.append("campaign_lifecycle_states must match the runtime helper contract")
    if tuple(payload.get("phase_lifecycle_states") or ()) != PHASE_LIFECYCLE_STATES:
        issues.append("phase_lifecycle_states must match the runtime helper contract")
    if tuple(payload.get("terminal_phase_states") or ()) != TERMINAL_PHASE_STATES:
        issues.append("terminal_phase_states must match the runtime helper contract")
    if tuple(payload.get("campaign_required_fields") or ()) != CAMPAIGN_REQUIRED_FIELDS:
        issues.append("campaign_required_fields must list the canonical campaign fields in order")
    if tuple(payload.get("phase_required_fields") or ()) != PHASE_REQUIRED_FIELDS:
        issues.append("phase_required_fields must list the canonical phase fields in order")
    if tuple(payload.get("truth_ref_fields") or ()) != TRUTH_REF_FIELDS:
        issues.append("truth_ref_fields must list the canonical truth ref fields in order")

    forbidden_top = payload.get("forbidden_inline_truth_fields")
    if not isinstance(forbidden_top, dict) or tuple(forbidden_top.keys()) != FORBIDDEN_INLINE_TRUTH_FIELDS:
        issues.append("forbidden_inline_truth_fields must list the canonical forbidden top-level truth fields in order")
    forbidden_phase = payload.get("forbidden_phase_inline_fields")
    if not isinstance(forbidden_phase, dict) or tuple(forbidden_phase.keys()) != FORBIDDEN_PHASE_INLINE_FIELDS:
        issues.append("forbidden_phase_inline_fields must list the canonical forbidden phase truth fields in order")

    append_only = tuple(payload.get("append_only_fields") or ())
    if append_only != APPEND_ONLY_FIELDS:
        issues.append("append_only_fields must remain aligned with the append-only contract fields")

    return tuple(issues)
