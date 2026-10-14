from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from .core import PipelineError


def _normalize_ref(key: str, value: Any) -> str:
    ref = str(value or "").strip()
    if not ref:
        raise PipelineError(
            "schema_validation_failed",
            f"artifact lineage entry {key} must be a non-empty string",
            details={"artifact_key": key},
            exit_code=3,
        )
    ref_path = Path(ref)
    if ref_path.is_absolute() or ".." in ref_path.parts:
        raise PipelineError(
            "invalid_artifact_lineage_path",
            "artifact lineage refs must be run-directory-relative paths",
            details={"artifact_key": key, "artifact_ref": ref},
            exit_code=3,
        )
    return ref


def normalize_artifact_lineage(value: Any) -> Dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PipelineError(
            "schema_validation_failed",
            "artifact_lineage must be a mapping when present",
            details={"field": "artifact_lineage"},
            exit_code=3,
        )
    normalized: Dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = str(raw_key or "").strip()
        if not key:
            raise PipelineError(
                "schema_validation_failed",
                "artifact_lineage keys must be non-empty strings",
                details={"field": "artifact_lineage"},
                exit_code=3,
            )
        normalized[key] = _normalize_ref(key, raw_value)
    return normalized


def build_artifact_lineage(*sources: Optional[Mapping[str, Any]], additions: Optional[Mapping[str, Any]] = None) -> Dict[str, str]:
    lineage: Dict[str, str] = {}
    for source in sources:
        if source is None:
            continue
        lineage.update(normalize_artifact_lineage(source))
    if additions:
        for raw_key, raw_value in additions.items():
            key = str(raw_key or "").strip()
            if not key:
                raise PipelineError(
                    "schema_validation_failed",
                    "artifact lineage additions must have non-empty keys",
                    details={"field": "artifact_lineage"},
                    exit_code=3,
                )
            lineage[key] = _normalize_ref(key, raw_value)
    return lineage


def resolve_lineage_artifact(
    *,
    run_dir: str | Path,
    lineage: Optional[Mapping[str, Any]],
    key: str,
    fallback_filename: str,
) -> Path:
    run_dir_path = Path(run_dir).resolve()
    normalized = normalize_artifact_lineage(lineage)
    ref = normalized.get(key) or _normalize_ref(key, fallback_filename)
    target = (run_dir_path / ref).resolve()
    try:
        target.relative_to(run_dir_path)
    except ValueError as exc:
        raise PipelineError(
            "invalid_artifact_lineage_path",
            "artifact lineage ref resolved outside the current run directory",
            details={"artifact_key": key, "artifact_ref": ref, "resolved_path": str(target)},
            exit_code=3,
        ) from exc
    if not target.exists():
        raise PipelineError(
            "missing_lineage_artifact",
            "required lineage artifact is missing from the current run directory",
            details={"artifact_key": key, "artifact_ref": ref, "resolved_path": str(target)},
            exit_code=3,
        )
    return target
