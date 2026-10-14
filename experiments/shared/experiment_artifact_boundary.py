"""Helpers for experiment artifact-boundary declarations.

This module keeps the repo-vs-external-artifact split explicit and reusable
across contract validation, summary projection, and runner env resolution.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

ARTIFACT_BOUNDARY_REVERSE_POINTER_SURFACES = (
    "run_record.json",
    "artifact_manifest.json",
)

_ENV_OR_PATH_RE = re.compile(r"^\s*(?P<env>[A-Z][A-Z0-9_]*)\s+or\s+(?P<fallback>.+?)\s*$")


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[2]


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _normalize_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    return None


def _resolve_declared_path(path_text: str, *, repo_root: Path) -> Path:
    candidate = Path(path_text).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / candidate).resolve()


def _path_display(path: Path, *, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def parse_external_data_root_declaration(declaration: Any) -> Dict[str, Any]:
    """Parse a declared external-data-root value.

    Supported forms:
    - ``~/causyl_experiment_data/demo``
    - ``../demo-data``
    - ``EXPERIMENT_OUTPUTS_DIR or ~/causyl_experiment_data/demo``
    """

    text = _normalize_text(declaration)
    if not text:
        return {
            "ok": False,
            "declaration": "",
            "kind": "",
            "env_var": "",
            "fallback": "",
            "error": "artifacts.external_data_root is required",
        }
    match = _ENV_OR_PATH_RE.match(text)
    if match is not None:
        fallback = _normalize_text(match.group("fallback"))
        if not fallback:
            return {
                "ok": False,
                "declaration": text,
                "kind": "",
                "env_var": _normalize_text(match.group("env")),
                "fallback": "",
                "error": "artifacts.external_data_root fallback path is required when using ENV_VAR or <path>",
            }
        return {
            "ok": True,
            "declaration": text,
            "kind": "env_or_path",
            "env_var": _normalize_text(match.group("env")),
            "fallback": fallback,
            "error": "",
        }
    return {
        "ok": True,
        "declaration": text,
        "kind": "path",
        "env_var": "",
        "fallback": text,
        "error": "",
    }


def summarize_artifact_boundary_contract(
    artifacts: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Build a deterministic artifact-boundary summary projection."""

    root = _repo_root(repo_root)
    repo_tracked_outputs = sorted(
        {
            _normalize_text(item)
            for item in list((artifacts or {}).get("repo_tracked_outputs") or [])
            if _normalize_text(item)
        }
    )
    parsed = parse_external_data_root_declaration((artifacts or {}).get("external_data_root"))
    resolved_fallback = ""
    default_runs_root = ""
    if bool(parsed.get("ok")):
        fallback_path = _resolve_declared_path(str(parsed.get("fallback") or ""), repo_root=root)
        resolved_fallback = _path_display(fallback_path, repo_root=root)
        default_runs_root = str((fallback_path / "runs").resolve())
    return {
        "declared": bool(_normalize_text((artifacts or {}).get("external_data_root"))),
        "repo_tracked_outputs": repo_tracked_outputs,
        "artifact_manifest_required": bool((artifacts or {}).get("artifact_manifest_required")),
        "external_data_root": {
            "declaration": _normalize_text((artifacts or {}).get("external_data_root")),
            "resolution_kind": str(parsed.get("kind") or ""),
            "env_var": str(parsed.get("env_var") or ""),
            "fallback_path": str(parsed.get("fallback") or ""),
            "resolved_fallback_path": resolved_fallback,
            "default_runs_root": default_runs_root,
        },
        "reverse_pointer_surfaces": list(ARTIFACT_BOUNDARY_REVERSE_POINTER_SURFACES),
    }


def validate_artifact_boundary_contract(
    artifacts: Mapping[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    """Validate artifact-boundary declarations in an experiment contract."""

    root = _repo_root(repo_root)
    obj = dict(artifacts or {})
    errors = []
    warnings = []

    repo_tracked_outputs = [
        _normalize_text(item)
        for item in list(obj.get("repo_tracked_outputs") or [])
        if _normalize_text(item)
    ]
    if not repo_tracked_outputs:
        errors.append(
            {
                "field": "artifacts.repo_tracked_outputs",
                "message": "artifacts.repo_tracked_outputs must list at least one small tracked output or proof surface",
            }
        )

    manifest_required = _normalize_bool(obj.get("artifact_manifest_required"))
    if manifest_required is None:
        errors.append(
            {
                "field": "artifacts.artifact_manifest_required",
                "message": "artifacts.artifact_manifest_required must be a boolean",
            }
        )

    parsed = parse_external_data_root_declaration(obj.get("external_data_root"))
    if not bool(parsed.get("ok")):
        errors.append(
            {
                "field": "artifacts.external_data_root",
                "message": str(parsed.get("error") or "artifacts.external_data_root is invalid"),
            }
        )
    else:
        fallback_path = _resolve_declared_path(str(parsed.get("fallback") or ""), repo_root=root)
        if _is_within(root, fallback_path):
            errors.append(
                {
                    "field": "artifacts.external_data_root",
                    "message": "artifacts.external_data_root fallback must resolve outside the tracked repo root",
                }
            )

    return {
        "ok": not errors,
        "errors": errors,
        "warnings": warnings,
        "summary": summarize_artifact_boundary_contract(obj, repo_root=root),
    }


def resolve_external_data_root(
    declaration: Any,
    *,
    repo_root: Optional[Path] = None,
    env: Optional[Mapping[str, str]] = None,
) -> Dict[str, Any]:
    """Resolve one external-data-root declaration to a concrete path."""

    root = _repo_root(repo_root)
    parsed = parse_external_data_root_declaration(declaration)
    if not bool(parsed.get("ok")):
        raise ValueError(str(parsed.get("error") or "artifacts.external_data_root is invalid"))

    env_map = dict(os.environ)
    if env is not None:
        env_map.update({str(key): str(value) for key, value in dict(env).items()})

    resolved_source = "path_declaration"
    env_var = str(parsed.get("env_var") or "")
    env_value = _normalize_text(env_map.get(env_var)) if env_var else ""
    if env_var and env_value:
        resolved_path = _resolve_declared_path(env_value, repo_root=root)
        resolved_source = f"env:{env_var}"
    else:
        resolved_path = _resolve_declared_path(str(parsed.get("fallback") or ""), repo_root=root)
        if str(parsed.get("kind") or "") == "env_or_path":
            resolved_source = "fallback_path"

    if _is_within(root, resolved_path):
        raise ValueError("artifacts.external_data_root must resolve outside the tracked repo root")

    return {
        "declaration": str(parsed.get("declaration") or ""),
        "kind": str(parsed.get("kind") or ""),
        "env_var": env_var,
        "fallback_path": str(parsed.get("fallback") or ""),
        "path": resolved_path,
        "resolved_path": str(resolved_path),
        "resolved_source": resolved_source,
        "default_runs_root": (resolved_path / "runs").resolve(),
    }
