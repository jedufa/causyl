"""Reference-only evidence-pack handoff helpers.

These helpers validate durable references to research evidence packs without
duplicating the evidence-pack schema. Callers may validate only reference shape,
or also require the linked pack file to exist under a supplied repo root.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from infra.controlplane import research_evidence_pack_authoring as authoring
from infra.controlplane import research_evidence_pack_contract as contract


REF_REQUIRED_FIELDS = ("pack_id", "pack_artifact_ref", "pack_fingerprint", "summary")
REF_OPTIONAL_FIELDS = ("pack_schema_version",)


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _resolve_repo_path(ref: str, *, repo_root: Path | None) -> Path:
    path = Path(ref)
    if repo_root is None:
        return path
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("evidence pack artifact refs must be repo-relative without parent traversal")
    root = Path(repo_root).resolve()
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ValueError("evidence pack artifact ref escapes repo root") from exc
    return resolved


def normalize_evidence_pack_refs(raw_refs: Any) -> List[Dict[str, str]]:
    rows = raw_refs if isinstance(raw_refs, list) else []
    out: List[Dict[str, str]] = []
    for raw in rows:
        row = raw if isinstance(raw, Mapping) else {}
        ref = {
            "pack_id": _normalize_text(row.get("pack_id")),
            "pack_schema_version": _normalize_text(row.get("pack_schema_version") or contract.PACK_SCHEMA_VERSION),
            "pack_artifact_ref": _normalize_text(row.get("pack_artifact_ref")),
            "pack_fingerprint": _normalize_text(row.get("pack_fingerprint")),
            "summary": _normalize_text(row.get("summary")),
        }
        if any(ref.values()):
            out.append(ref)
    out.sort(key=lambda row: (row["pack_id"], row["pack_artifact_ref"], row["pack_fingerprint"]))
    return out


def validate_evidence_pack_refs(
    refs: Sequence[Mapping[str, Any]],
    *,
    repo_root: Path | None = None,
    field_prefix: str = "evidence_pack_refs",
) -> List[Dict[str, str]]:
    errors: List[Dict[str, str]] = []
    seen_pack_ids: set[str] = set()

    for index, raw_ref in enumerate(refs):
        ref = dict(raw_ref)
        prefix = f"{field_prefix}[{index}]"
        for field in REF_REQUIRED_FIELDS:
            if not _normalize_text(ref.get(field)):
                errors.append({"field": f"{prefix}.{field}", "code": "missing_field", "message": f"{prefix}.{field} is required"})

        pack_id = _normalize_text(ref.get("pack_id"))
        if pack_id:
            if pack_id in seen_pack_ids:
                errors.append({"field": f"{prefix}.pack_id", "code": "duplicate_id", "message": "pack_id must be unique in handoff refs"})
            seen_pack_ids.add(pack_id)

        if _normalize_text(ref.get("pack_schema_version")) != contract.PACK_SCHEMA_VERSION:
            errors.append(
                {
                    "field": f"{prefix}.pack_schema_version",
                    "code": "invalid_value",
                    "message": f"pack_schema_version must be {contract.PACK_SCHEMA_VERSION}",
                }
            )

        artifact_ref = _normalize_text(ref.get("pack_artifact_ref"))
        if repo_root is None or not artifact_ref:
            continue

        try:
            path = _resolve_repo_path(artifact_ref, repo_root=repo_root)
        except ValueError as exc:
            errors.append(
                {
                    "field": f"{prefix}.pack_artifact_ref",
                    "code": "invalid_evidence_pack_ref",
                    "message": str(exc),
                }
            )
            continue
        if not path.exists():
            errors.append(
                {
                    "field": f"{prefix}.pack_artifact_ref",
                    "code": "missing_evidence_pack",
                    "message": "referenced evidence pack artifact does not exist",
                }
            )
            continue

        try:
            result = authoring.validate_evidence_pack_file(path, repo_root=repo_root)
        except (OSError, ValueError) as exc:
            errors.append(
                {
                    "field": f"{prefix}.pack_artifact_ref",
                    "code": "invalid_evidence_pack",
                    "message": f"referenced evidence pack could not be loaded: {exc}",
                }
            )
            continue

        normalized_pack = result.get("normalized_pack") if isinstance(result.get("normalized_pack"), Mapping) else {}
        if not result.get("ok"):
            errors.append(
                {
                    "field": f"{prefix}.pack_artifact_ref",
                    "code": "invalid_evidence_pack",
                    "message": "referenced evidence pack failed validation",
                }
            )
        if pack_id and _normalize_text(normalized_pack.get("pack_id")) != pack_id:
            errors.append({"field": f"{prefix}.pack_id", "code": "pack_id_mismatch", "message": "pack_id must match the referenced pack"})
        fingerprint = _normalize_text(ref.get("pack_fingerprint"))
        if fingerprint and _normalize_text(result.get("computed_fingerprint")) != fingerprint:
            errors.append(
                {
                    "field": f"{prefix}.pack_fingerprint",
                    "code": "fingerprint_mismatch",
                    "message": "pack_fingerprint must match the referenced evidence pack",
                }
            )

    return errors
