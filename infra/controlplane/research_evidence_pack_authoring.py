"""Authoring and audit helpers for research evidence pack files."""

from __future__ import annotations

import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Sequence

from infra.controlplane import research_evidence_pack_contract as contract


RECEIPT_SCHEMA_VERSION = "research_evidence_pack_audit_receipt_v1"
AUTHORING_HELPER_UID = "PIS-20260410-102637Z-EA30"

APPENDABLE_SECTIONS = frozenset(
    {
        "sources",
        "source_quality_notes",
        "claims",
        "claim_source_links",
        "contradictions",
        "unresolved_conflict_notes",
        "summary_layers",
    }
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return (json.dumps(dict(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with tmp.open("wb") as handle:
        handle.write(_json_bytes(payload))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"{path} must contain a JSON object evidence pack")
    return payload


def _relative_path(path: Path, *, repo_root: Path | None = None) -> str:
    normalized = Path(path)
    if repo_root is not None:
        try:
            return normalized.resolve().relative_to(Path(repo_root).resolve()).as_posix()
        except ValueError:
            pass
    return normalized.as_posix()


def _row_id(row: Mapping[str, Any], id_field: str) -> str:
    return str(row.get(id_field) or "").strip()


def _ids(rows: Iterable[Mapping[str, Any]], id_field: str) -> set[str]:
    return {value for value in (_row_id(row, id_field) for row in rows) if value}


def _ensure_no_duplicate_append_ids(
    base: Mapping[str, Any],
    additions: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[Dict[str, str]]:
    id_fields = {
        "sources": "source_id",
        "claims": "claim_id",
        "claim_source_links": "link_id",
        "contradictions": "contradiction_id",
        "unresolved_conflict_notes": "note_id",
        "summary_layers": "layer_id",
    }
    errors: list[Dict[str, str]] = []
    for section, id_field in id_fields.items():
        existing = _ids((row for row in base.get(section, []) if isinstance(row, Mapping)), id_field)
        incoming = []
        for row in additions.get(section, ()):
            if not isinstance(row, Mapping):
                continue
            value = _row_id(row, id_field)
            if value:
                incoming.append(value)
        for value in sorted(set(incoming) & existing):
            errors.append(
                {
                    "field": section,
                    "code": "append_would_overwrite_identity",
                    "message": f"{section} already contains {id_field}={value}",
                }
            )
        for value in sorted({item for item in incoming if incoming.count(item) > 1}):
            errors.append(
                {
                    "field": section,
                    "code": "duplicate_append_identity",
                    "message": f"append payload repeats {id_field}={value}",
                }
            )
    return errors


def _failure_result(errors: Sequence[Mapping[str, str]], *, pack: Mapping[str, Any] | None = None) -> Dict[str, Any]:
    normalized = contract.normalize_research_evidence_pack(pack or {})
    return {
        "ok": False,
        "errors": [dict(error) for error in errors],
        "warnings": [],
        "normalized_pack": normalized,
        "computed_fingerprint": "",
    }


def compact_result_for_output(result: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = result.get("normalized_pack") if isinstance(result.get("normalized_pack"), Mapping) else {}
    output = {
        "ok": bool(result.get("ok")),
        "pack_id": str(normalized.get("pack_id") or ""),
        "pack_schema_version": str(normalized.get("schema_version") or ""),
        "computed_fingerprint": str(result.get("computed_fingerprint") or ""),
        "error_count": len(result.get("errors") or []),
        "warning_count": len(result.get("warnings") or []),
        "errors": list(result.get("errors") or [])[:20],
        "warnings": list(result.get("warnings") or [])[:20],
    }
    if isinstance(result.get("receipt"), Mapping):
        receipt = result["receipt"]
        output["receipt"] = {
            "schema_version": receipt.get("schema_version"),
            "action": receipt.get("action"),
            "result": receipt.get("result"),
            "pack_fingerprint": receipt.get("pack_fingerprint"),
        }
    return output


def build_audit_receipt(
    *,
    pack_path: Path,
    action: str,
    validation_result: Mapping[str, Any],
    repo_root: Path | None = None,
    created_at_utc: str | None = None,
) -> Dict[str, Any]:
    normalized = validation_result.get("normalized_pack") if isinstance(validation_result.get("normalized_pack"), Mapping) else {}
    return {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "authoring_helper_uid": AUTHORING_HELPER_UID,
        "created_at_utc": created_at_utc or _utc_now(),
        "action": str(action or "").strip() or "validate",
        "result": "ok" if validation_result.get("ok") else "failed",
        "pack_path": _relative_path(Path(pack_path), repo_root=repo_root),
        "pack_id": str(normalized.get("pack_id") or ""),
        "pack_schema_version": str(normalized.get("schema_version") or ""),
        "pack_fingerprint": str(validation_result.get("computed_fingerprint") or ""),
        "error_count": len(validation_result.get("errors") or []),
        "warning_count": len(validation_result.get("warnings") or []),
        "errors": list(validation_result.get("errors") or [])[:20],
        "warnings": list(validation_result.get("warnings") or [])[:20],
    }


def write_audit_receipt(
    *,
    receipt_path: Path,
    pack_path: Path,
    action: str,
    validation_result: Mapping[str, Any],
    repo_root: Path | None = None,
    created_at_utc: str | None = None,
) -> Dict[str, Any]:
    receipt = build_audit_receipt(
        pack_path=pack_path,
        action=action,
        validation_result=validation_result,
        repo_root=repo_root,
        created_at_utc=created_at_utc,
    )
    _write_json_atomic(Path(receipt_path), receipt)
    return receipt


def validate_evidence_pack_file(
    pack_path: Path,
    *,
    receipt_path: Path | None = None,
    repo_root: Path | None = None,
    action: str = "validate",
) -> Dict[str, Any]:
    pack = _read_json(Path(pack_path))
    result = contract.validate_research_evidence_pack(pack)
    if receipt_path is not None and result["ok"]:
        result = dict(result)
        result["receipt"] = write_audit_receipt(
            receipt_path=receipt_path,
            pack_path=pack_path,
            action=action,
            validation_result=result,
            repo_root=repo_root,
        )
    return result


def create_evidence_pack_file(
    pack_path: Path,
    pack: Mapping[str, Any],
    *,
    receipt_path: Path | None = None,
    repo_root: Path | None = None,
    overwrite: bool = False,
) -> Dict[str, Any]:
    pack_path = Path(pack_path)
    if pack_path.exists() and not overwrite:
        return _failure_result(
            [
                {
                    "field": "pack_path",
                    "code": "already_exists",
                    "message": "refusing to overwrite an existing evidence pack without overwrite=True",
                }
            ],
            pack=pack,
        )

    candidate = copy.deepcopy(dict(pack))
    result = contract.validate_research_evidence_pack(candidate)
    if not result["ok"]:
        return result

    _write_json_atomic(pack_path, result["normalized_pack"])
    result = dict(result)
    if receipt_path is not None:
        result["receipt"] = write_audit_receipt(
            receipt_path=receipt_path,
            pack_path=pack_path,
            action="create",
            validation_result=result,
            repo_root=repo_root,
        )
    return result


def append_evidence_pack_records(
    pack_path: Path,
    *,
    receipt_path: Path | None = None,
    repo_root: Path | None = None,
    **sections: Sequence[Mapping[str, Any]],
) -> Dict[str, Any]:
    unknown_sections = sorted(set(sections) - APPENDABLE_SECTIONS)
    if unknown_sections:
        return _failure_result(
            [
                {
                    "field": section,
                    "code": "unsupported_append_section",
                    "message": "section is not appendable in research_evidence_pack_v1",
                }
                for section in unknown_sections
            ]
        )

    base = _read_json(Path(pack_path))
    additions: Dict[str, Sequence[Mapping[str, Any]]] = {}
    for section, rows in sections.items():
        if rows is None:
            continue
        if not isinstance(rows, Sequence) or isinstance(rows, (str, bytes, bytearray)):
            return _failure_result(
                [
                    {
                        "field": section,
                        "code": "invalid_append_payload",
                        "message": "append payload section must be a list of JSON objects",
                    }
                ],
                pack=base,
            )
        additions[section] = rows

    duplicate_errors = _ensure_no_duplicate_append_ids(base, additions)
    if duplicate_errors:
        return _failure_result(duplicate_errors, pack=base)

    candidate = copy.deepcopy(base)
    for section, rows in additions.items():
        candidate.setdefault(section, [])
        if not isinstance(candidate[section], list):
            return _failure_result(
                [
                    {
                        "field": section,
                        "code": "invalid_pack_section",
                        "message": "existing pack section must be a list before append",
                    }
                ],
                pack=base,
            )
        for row in rows:
            if not isinstance(row, Mapping):
                return _failure_result(
                    [
                        {
                            "field": section,
                            "code": "invalid_append_payload",
                            "message": "append payload rows must be JSON objects",
                        }
                    ],
                    pack=base,
                )
            candidate[section].append(copy.deepcopy(dict(row)))

    result = contract.validate_research_evidence_pack(candidate)
    if not result["ok"]:
        return result

    _write_json_atomic(Path(pack_path), result["normalized_pack"])
    result = dict(result)
    if receipt_path is not None:
        result["receipt"] = write_audit_receipt(
            receipt_path=receipt_path,
            pack_path=pack_path,
            action="append",
            validation_result=result,
            repo_root=repo_root,
        )
    return result


def append_source(
    pack_path: Path,
    *,
    source: Mapping[str, Any],
    source_quality_note: Mapping[str, Any],
    receipt_path: Path | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    return append_evidence_pack_records(
        pack_path,
        sources=[source],
        source_quality_notes=[source_quality_note],
        receipt_path=receipt_path,
        repo_root=repo_root,
    )


def append_claim(
    pack_path: Path,
    *,
    claim: Mapping[str, Any],
    claim_source_link: Mapping[str, Any],
    receipt_path: Path | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    return append_evidence_pack_records(
        pack_path,
        claims=[claim],
        claim_source_links=[claim_source_link],
        receipt_path=receipt_path,
        repo_root=repo_root,
    )


def append_contradiction_note(
    pack_path: Path,
    *,
    contradiction: Mapping[str, Any],
    unresolved_conflict_note: Mapping[str, Any],
    receipt_path: Path | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    return append_evidence_pack_records(
        pack_path,
        contradictions=[contradiction],
        unresolved_conflict_notes=[unresolved_conflict_note],
        receipt_path=receipt_path,
        repo_root=repo_root,
    )


def append_summary_layer(
    pack_path: Path,
    *,
    summary_layer: Mapping[str, Any],
    receipt_path: Path | None = None,
    repo_root: Path | None = None,
) -> Dict[str, Any]:
    return append_evidence_pack_records(
        pack_path,
        summary_layers=[summary_layer],
        receipt_path=receipt_path,
        repo_root=repo_root,
    )
