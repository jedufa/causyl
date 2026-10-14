"""Emit canonical compiled work-order envelopes for stable orchestration boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Dict, Mapping, Sequence

from infra.controlplane import compiled_work_order_envelope as _envelope


SCHEMA_VERSION = "compiled_work_order_boundary_emission_v1"


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_text_list(value: Any) -> list[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return []
    out: list[str] = []
    for raw in value:
        text = _normalize_text(raw)
        if text and text not in out:
            out.append(text)
    return out


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


def sibling_compiled_artifact_rel(owner_artifact_rel: Path, *, filename: str = "compiled_work_order_envelope.json") -> Path:
    return owner_artifact_rel.parent / filename


def _fingerprint_metadata(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _fallback_payload(
    *,
    boundary_kind: str,
    owner_binding: Mapping[str, Any],
    owner_artifact_rel: str,
    fallback_reason_codes: Sequence[str],
    warnings: Sequence[Mapping[str, Any]] = (),
) -> Dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "boundary_kind": _normalize_text(boundary_kind),
        "status": "fallback_legacy",
        "owner_binding": {
            "binding_type": _normalize_text(owner_binding.get("binding_type")),
            "binding_id": _normalize_text(owner_binding.get("binding_id")),
        },
        "owner_artifact_rel": _normalize_text(owner_artifact_rel),
        "compiled_artifact_rel": "",
        "non_compiled_reason_codes": _normalize_text_list(fallback_reason_codes) or ["unsupported_boundary"],
        "compiled_envelope_fingerprint": "",
        "effective_context_refs": [],
        "warnings": [dict(row) for row in warnings if isinstance(row, Mapping)],
    }
    payload["emission_fingerprint"] = _fingerprint_metadata(payload)
    return payload


def emit_compiled_work_order_boundary(
    *,
    boundary_kind: str,
    work_order_id: str,
    source_binding: Mapping[str, Any],
    handoff_kind: str,
    scope_objective: str,
    in_scope: Sequence[str],
    out_of_scope: Sequence[str],
    required_context_bundle: str,
    additional_context_refs: Sequence[str],
    forbidden_context_refs: Sequence[str],
    expected_outputs: Sequence[Mapping[str, Any]],
    validation_checks: Sequence[str],
    owner_artifact_rel: Path | str,
    prose_brief_present: bool = True,
    execution_family: Mapping[str, Any] | None = None,
    continuation_packet_ref: str = "",
    repo_root: Path | None = None,
    fallback_reason_codes: Sequence[str] | None = None,
) -> Dict[str, Any]:
    owner_artifact_path = Path(_normalize_text(owner_artifact_rel))
    owner_binding = {
        "binding_type": _normalize_text(source_binding.get("binding_type")),
        "binding_id": _normalize_text(source_binding.get("binding_id")),
    }
    if fallback_reason_codes:
        return _fallback_payload(
            boundary_kind=boundary_kind,
            owner_binding=owner_binding,
            owner_artifact_rel=owner_artifact_path.as_posix(),
            fallback_reason_codes=fallback_reason_codes,
        )

    raw_envelope: Dict[str, Any] = {
        "schema_version": _envelope.SCHEMA_VERSION,
        "work_order_id": _normalize_text(work_order_id),
        "source_binding": owner_binding,
        "handoff_kind": _normalize_text(handoff_kind),
        "scope_fence": {
            "objective": _normalize_text(scope_objective),
            "in_scope": _normalize_text_list(in_scope),
            "out_of_scope": _normalize_text_list(out_of_scope),
        },
        "required_context_bundle": _normalize_text(required_context_bundle),
        "additional_context_refs": _normalize_text_list(additional_context_refs),
        "forbidden_context_refs": _normalize_text_list(forbidden_context_refs),
        "expected_outputs": [dict(row) for row in expected_outputs if isinstance(row, Mapping)],
        "validation_expectations": {
            "required_checks": _normalize_text_list(validation_checks),
            "failure_mode": _envelope.FAILURE_MODE_FAIL_CLOSED,
            "prose_coexistence": _envelope.PROSE_COEXISTENCE_FALLBACK_ALLOWED,
        },
    }
    if isinstance(execution_family, Mapping) and execution_family:
        raw_envelope["execution_family"] = {
            _normalize_text(key): _normalize_text(value)
            for key, value in execution_family.items()
            if _normalize_text(key)
        }
    if _normalize_text(continuation_packet_ref):
        raw_envelope["continuation_packet_ref"] = _normalize_text(continuation_packet_ref)

    validated = _envelope.validate_compiled_work_order_envelope(raw_envelope)
    if not validated["ok"]:
        return _fallback_payload(
            boundary_kind=boundary_kind,
            owner_binding=owner_binding,
            owner_artifact_rel=owner_artifact_path.as_posix(),
            fallback_reason_codes=[row.get("code") for row in validated["errors"]],
            warnings=validated["warnings"],
        )
    context = _envelope.resolve_compiled_work_order_context_refs(raw_envelope)
    coexistence = _envelope.resolve_compiled_work_order_coexistence(
        compiled_envelope=raw_envelope,
        prose_brief_present=prose_brief_present,
    )
    if not context["ok"] or not coexistence["ok"]:
        errors = list(context.get("errors") or []) + list(coexistence.get("errors") or [])
        warnings = list(context.get("warnings") or []) + list(coexistence.get("warnings") or [])
        return _fallback_payload(
            boundary_kind=boundary_kind,
            owner_binding=owner_binding,
            owner_artifact_rel=owner_artifact_path.as_posix(),
            fallback_reason_codes=[row.get("code") for row in errors],
            warnings=warnings,
        )

    compiled_artifact_rel = sibling_compiled_artifact_rel(owner_artifact_path)
    status = "compiled_ready"
    if repo_root is not None:
        _write_json_atomic((Path(repo_root).resolve() / compiled_artifact_rel).resolve(), validated["normalized_envelope"])
        status = "compiled_emitted"

    payload = {
        "schema_version": SCHEMA_VERSION,
        "boundary_kind": _normalize_text(boundary_kind),
        "status": status,
        "owner_binding": owner_binding,
        "owner_artifact_rel": owner_artifact_path.as_posix(),
        "compiled_artifact_rel": compiled_artifact_rel.as_posix(),
        "non_compiled_reason_codes": [],
        "compiled_envelope_fingerprint": _normalize_text(validated.get("computed_fingerprint")),
        "effective_context_refs": list(context.get("effective_refs") or []),
        "coexistence": {
            "effective_path": _normalize_text(coexistence.get("effective_path")),
            "precedence": _normalize_text(coexistence.get("precedence")),
        },
        "warnings": [dict(row) for row in list(coexistence.get("warnings") or []) if isinstance(row, Mapping)],
    }
    payload["emission_fingerprint"] = _fingerprint_metadata(payload)
    return payload
