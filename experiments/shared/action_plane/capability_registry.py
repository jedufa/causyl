"""Tool capability registry helpers for the phase-1 action plane."""

from __future__ import annotations

import json

from .contract_source import load_contract
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


TOOL_CAPABILITY_REGISTRY_PATH = Path("governance/contracts/tool_capability_registry_v1.json")
REGISTRY_SCHEMA_VERSION = "tool_capability_registry_v1"


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _load_json(path: Path) -> Dict[str, Any]:
    return load_contract(path)


def load_tool_capability_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    path = (root / TOOL_CAPABILITY_REGISTRY_PATH).resolve()
    data = _load_json(path)
    if str(data.get("schema_version") or "").strip() != REGISTRY_SCHEMA_VERSION:
        raise ValueError(f"unsupported tool capability registry schema: {path}")
    return data


def _candidate_exec_paths(entry: Dict[str, Any], *, repo_root: Path) -> List[Path]:
    command = [str(part or "").strip() for part in list(entry.get("command") or []) if str(part or "").strip()]
    candidates: List[Path] = []
    for token in command:
        if token.startswith("-") or token in {"python", "python3"}:
            continue
        candidate = Path(token).expanduser()
        if candidate.is_absolute():
            candidates.append(candidate.resolve())
        else:
            candidates.append((repo_root / candidate).resolve())
        if candidate.suffix:
            break
    if not candidates:
        exec_path = str(entry.get("executable_path") or "").strip()
        if exec_path:
            path = Path(exec_path).expanduser()
            candidates.append(path.resolve() if path.is_absolute() else (repo_root / path).resolve())
    return candidates


def _has_runnable_backing(entry: Dict[str, Any], *, repo_root: Path) -> bool:
    candidates = _candidate_exec_paths(entry, repo_root=repo_root)
    for path in candidates:
        if path.exists() and path.is_file():
            return True
    return False


def derive_tool_capability_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry = load_tool_capability_registry(repo_root=repo_root)
    root = _repo_root(repo_root)
    derived_entries: List[Dict[str, Any]] = []
    for entry in list(registry.get("entries") or []):
        row = dict(entry)
        row["has_runnable_backing"] = _has_runnable_backing(row, repo_root=root)
        row["derivation_status"] = "validated_executable" if row["has_runnable_backing"] else "descriptive_only"
        derived_entries.append(row)
    registry["entries"] = derived_entries
    return registry


def validate_tool_capability_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry = derive_tool_capability_registry(repo_root=repo_root)
    root = _repo_root(repo_root)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []
    ids: set[str] = set()
    for entry in list(registry.get("entries") or []):
        tool_id = str(entry.get("tool_id") or "").strip()
        if not tool_id:
            errors.append({"code": "missing_tool_id", "message": "registry entry missing tool_id"})
            continue
        if tool_id in ids:
            errors.append({"code": "duplicate_tool_id", "message": f"duplicate tool_id: {tool_id}"})
        ids.add(tool_id)
        status = str(entry.get("status") or "").strip()
        if status == "active" and not entry.get("has_runnable_backing"):
            errors.append(
                {
                    "code": "active_entry_not_executable",
                    "message": f"active tool_id lacks runnable backing: {tool_id}",
                }
            )
        if status == "planned" and entry.get("has_runnable_backing"):
            warnings.append(
                {
                    "code": "planned_entry_already_backed",
                    "message": f"planned tool_id already has runnable backing: {tool_id}",
                }
            )
        if status == "active":
            evidence_checks = [str(item or "").strip() for item in list(entry.get("evidence_checks") or []) if str(item or "").strip()]
            if "executable_backing" not in evidence_checks:
                errors.append(
                    {
                        "code": "missing_executable_evidence_check",
                        "message": f"active tool_id missing executable_backing evidence check: {tool_id}",
                    }
                )
        executable_path = str(entry.get("executable_path") or "").strip()
        if executable_path:
            resolved = (root / executable_path).resolve() if not Path(executable_path).is_absolute() else Path(executable_path).resolve()
            if status == "active" and not resolved.exists():
                errors.append(
                    {
                        "code": "declared_executable_path_missing",
                        "message": f"declared executable path missing for {tool_id}: {executable_path}",
                    }
                )
    return {"ok": not errors, "errors": errors, "warnings": warnings, "registry": registry}

