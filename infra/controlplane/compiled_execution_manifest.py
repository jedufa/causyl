"""Deterministic compiled execution context manifest resolution."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from infra.controlplane import compiled_execution_ir as _ir
from infra.controlplane import context_bundles as _context_bundles


DEFAULT_MAX_REFS = 24


def _normalize_ref_list(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for raw in values:
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def canonical_manifest_json(manifest: Mapping[str, Any]) -> str:
    return json.dumps(dict(manifest), sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_manifest_json(manifest).encode("utf-8")).hexdigest()


def resolve_context_manifest(
    ir_or_validation: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
    max_refs: int = DEFAULT_MAX_REFS,
    strict_required_refs: bool = False,
) -> Dict[str, Any]:
    validated = (
        dict(ir_or_validation)
        if "normalized_ir" in ir_or_validation
        else _ir.validate_execution_ir(dict(ir_or_validation))
    )
    if not bool(validated.get("ok")):
        return {
            "ok": False,
            "failure_class": "context_manifest_resolution_blocked",
            "errors": [
                {
                    "code": "invalid_ir",
                    "message": "cannot resolve context manifest from invalid execution IR",
                }
            ],
            "warnings": [],
            "manifest": {},
        }
    normalized_ir = dict(validated.get("normalized_ir") or {})
    bundle_id = str(normalized_ir.get("required_context_bundle") or "").strip()
    bundle_refs = list(_context_bundles.bundle_refs(bundle_id)) if bundle_id else []
    additional_refs = _normalize_ref_list(normalized_ir.get("additional_context_refs"))
    forbidden_refs = sorted(set(_normalize_ref_list(normalized_ir.get("forbidden_context_refs"))))
    omitted_refs: List[Dict[str, str]] = []

    ordered_refs: List[str] = []
    for ref in bundle_refs + additional_refs:
        if ref in forbidden_refs:
            omitted_refs.append({"path": ref, "reason": "forbidden"})
            continue
        if ref not in ordered_refs:
            ordered_refs.append(ref)

    if max_refs > 0 and len(ordered_refs) > max_refs:
        dropped = ordered_refs[max_refs:]
        ordered_refs = ordered_refs[:max_refs]
        omitted_refs.extend({"path": ref, "reason": "budget_trim"} for ref in dropped)

    root = Path(repo_root).resolve() if repo_root is not None else None
    files: List[Dict[str, Any]] = []
    missing_required_refs: List[str] = []
    for ref in ordered_refs:
        exists = False
        if root is not None:
            p = (root / ref).resolve()
            try:
                p.relative_to(root)
                exists = p.exists()
            except Exception:
                exists = False
        source = "bundle" if ref in bundle_refs else "additional_ref"
        files.append({"path": ref, "source": source, "exists": exists})
        if strict_required_refs and not exists:
            missing_required_refs.append(ref)

    manifest = {
        "manifest_version": "compiled_context_manifest_v1",
        "ir_fingerprint": str(validated.get("fingerprint") or ""),
        "required_context_bundle": bundle_id,
        "files": files,
        "forbidden_refs": forbidden_refs,
        "omitted_refs": sorted(omitted_refs, key=lambda row: (str(row.get("reason") or ""), str(row.get("path") or ""))),
        "max_refs": int(max_refs),
    }
    manifest["fingerprint"] = manifest_fingerprint(manifest)
    if strict_required_refs and missing_required_refs:
        return {
            "ok": False,
            "failure_class": "required_context_refs_missing",
            "errors": [
                {
                    "code": "missing_required_ref",
                    "message": f"required context refs missing: {', '.join(sorted(missing_required_refs))}",
                }
            ],
            "warnings": [],
            "manifest": manifest,
        }
    return {
        "ok": True,
        "failure_class": "",
        "errors": [],
        "warnings": [],
        "manifest": manifest,
    }
