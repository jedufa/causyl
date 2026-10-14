"""Scaffold-owned discovery registry for experiment listing and resolution.

This registry is intentionally narrow. It makes scaffolded experiments visible to
operator and listing flows without replacing the canonical experiment contract as
the source of semantic truth.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from infra.root_tools import load_tool_module as _load_root_tool_module

fsio = _load_root_tool_module("shared_fsio", "tools/shared/fsio.py")


REGISTRY_SCHEMA_VERSION = "scaffold_experiment_registry_v1"
REGISTRY_RELATIVE_PATH = "experiments/registry/scaffolded_experiments.json"
REGISTRY_ARTIFACT_BOUNDARY_SCHEMA_VERSION = "scaffold_experiment_registry_artifact_boundary_v1"
REGISTRY_COMPATIBILITY_SCHEMA_VERSION = "scaffold_experiment_registry_compatibility_v1"


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[2]


def experiment_registry_path(*, repo_root: Optional[Path] = None) -> Path:
    return (_repo_root(repo_root) / REGISTRY_RELATIVE_PATH).resolve()


def _json_dump(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, indent=2, sort_keys=True) + "\n"


def _default_registry() -> Dict[str, Any]:
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "records": [],
    }


def _read_registry(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    path = experiment_registry_path(repo_root=repo_root)
    if not path.exists():
        return _default_registry()
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("schema_version") or "").strip() != REGISTRY_SCHEMA_VERSION:
        raise ValueError("experiment discovery registry has unsupported schema_version")
    rows = data.get("records")
    if not isinstance(rows, list):
        raise ValueError("experiment discovery registry is missing records")
    return {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "records": [row for row in rows if isinstance(row, dict)],
    }


def build_registered_experiment_record(
    *,
    project_id: str,
    display_name: str,
    project_root: str,
    contract_path: str,
    scaffold_manifest_path: str,
    scaffold_version: str,
    tags: Sequence[str] = (),
    family_tags: Sequence[str] = (),
    required_context_bundle: str = "",
    resource_class: str = "",
    analysis_recipe: str = "",
    external_data_root: str = "",
    layout_contract_path: str = "",
    run_lifecycle_contract_path: str = "",
) -> Dict[str, Any]:
    project_root_text = str(project_root).strip()
    analysis_recipe_text = str(analysis_recipe).strip()
    external_data_root_text = str(external_data_root).strip()
    contract_path_text = str(contract_path).strip()
    scaffold_manifest_path_text = str(scaffold_manifest_path).strip()
    record = {
        "project_id": str(project_id).strip(),
        "slug": str(project_id).strip(),
        "display_name": str(display_name).strip(),
        "project_root": project_root_text,
        "contract_path": contract_path_text,
        "scaffold_manifest_path": scaffold_manifest_path_text,
        "scaffold_version": str(scaffold_version).strip(),
        "discovery_status": "registered",
        "tags": sorted({str(tag or "").strip().lower() for tag in list(tags or []) if str(tag or "").strip()}),
        "family_tags": sorted({str(tag or "").strip().lower() for tag in list(family_tags or []) if str(tag or "").strip()}),
        "required_context_bundle": str(required_context_bundle).strip(),
        "resource_class": str(resource_class).strip(),
        "analysis_recipe": analysis_recipe_text,
        "external_data_root": external_data_root_text,
        "artifact_boundary": {
            "schema_version": REGISTRY_ARTIFACT_BOUNDARY_SCHEMA_VERSION,
            "policy_path": f"{project_root_text}/_artifacts/data_root_policy.json",
            "external_data_root": external_data_root_text,
            "repo_tracked_placeholders": [
                f"{project_root_text}/_artifacts/README.md",
                f"{project_root_text}/_artifacts/data_root_policy.json",
                f"{project_root_text}/analysis/{analysis_recipe_text}.md",
            ],
            "reserved_runtime_roots": [
                f"{project_root_text}/_artifacts/proofs",
                f"{project_root_text}/_artifacts/reports",
            ],
        },
        "compatibility_metadata": {
            "schema_version": REGISTRY_COMPATIBILITY_SCHEMA_VERSION,
            "scaffold_version": str(scaffold_version).strip(),
            "discovery_status": "registered",
            "upgrade_readiness": "unknown",
            "contract_anchors": {
                "canonical_contract_path": contract_path_text,
                "scaffold_manifest_path": scaffold_manifest_path_text,
                "layout_contract_path": str(layout_contract_path).strip(),
                "run_lifecycle_contract_path": str(run_lifecycle_contract_path).strip(),
            },
        },
    }
    return record


def plan_experiment_registration(record: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    registry = _read_registry(repo_root=repo_root)
    rows = [dict(item) for item in list(registry.get("records") or []) if isinstance(item, dict)]
    project_id = str(record.get("project_id") or "").strip()
    contract_path = str(record.get("contract_path") or "").strip()

    same_id = next((row for row in rows if str(row.get("project_id") or "").strip() == project_id), None)
    same_contract = next((row for row in rows if str(row.get("contract_path") or "").strip() == contract_path), None)

    collisions: List[Dict[str, str]] = []
    if same_id is not None and str(same_id.get("contract_path") or "").strip() != contract_path:
        collisions.append(
            {
                "type": "duplicate_project_id_conflict",
                "path": REGISTRY_RELATIVE_PATH,
                "message": f"project_id {project_id} is already registered with a different contract path",
            }
        )
    if same_contract is not None and str(same_contract.get("project_id") or "").strip() != project_id:
        collisions.append(
            {
                "type": "duplicate_contract_path_conflict",
                "path": REGISTRY_RELATIVE_PATH,
                "message": f"contract path {contract_path} is already claimed by another project id",
            }
        )
    if collisions:
        return {
            "ok": False,
            "registry_path": REGISTRY_RELATIVE_PATH,
            "record": dict(record),
            "collisions": collisions,
        }

    action = "create"
    updated_rows: List[Dict[str, Any]] = []
    if same_id is not None:
        if json.dumps(same_id, sort_keys=True) == json.dumps(dict(record), sort_keys=True):
            action = "noop"
            updated_rows = rows
        else:
            action = "update"
            for row in rows:
                if str(row.get("project_id") or "").strip() == project_id:
                    updated_rows.append(dict(record))
                else:
                    updated_rows.append(row)
    else:
        updated_rows = rows + [dict(record)]
    updated_rows = sorted(updated_rows, key=lambda row: str(row.get("project_id") or ""))
    payload = {
        "schema_version": REGISTRY_SCHEMA_VERSION,
        "records": updated_rows,
    }
    return {
        "ok": True,
        "registry_path": REGISTRY_RELATIVE_PATH,
        "record": dict(record),
        "action": action,
        "payload": payload,
        "collisions": [],
    }


def list_registered_experiments(*, repo_root: Optional[Path] = None, verify_paths: bool = True) -> List[Dict[str, Any]]:
    root = _repo_root(repo_root)
    registry = _read_registry(repo_root=root)
    rows: List[Dict[str, Any]] = []
    for record in list(registry.get("records") or []):
        row = dict(record)
        if verify_paths:
            contract_exists = (root / str(row.get("contract_path") or "")).resolve().exists()
            manifest_exists = (root / str(row.get("scaffold_manifest_path") or "")).resolve().exists()
            artifact_boundary = row.get("artifact_boundary") if isinstance(row.get("artifact_boundary"), dict) else {}
            policy_path = str(artifact_boundary.get("policy_path") or "").strip()
            artifact_boundary_policy_exists = True
            if policy_path:
                artifact_boundary_policy_exists = (root / policy_path).resolve().exists()
            row["contract_exists"] = bool(contract_exists)
            row["scaffold_manifest_exists"] = bool(manifest_exists)
            row["artifact_boundary_policy_exists"] = bool(artifact_boundary_policy_exists)
            row["discovery_health"] = (
                "ok" if contract_exists and manifest_exists and artifact_boundary_policy_exists else "missing_paths"
            )
        rows.append(row)
    return sorted(rows, key=lambda row: str(row.get("project_id") or ""))


def resolve_registered_experiment(
    project_id: str,
    *,
    repo_root: Optional[Path] = None,
    verify_paths: bool = True,
) -> Dict[str, Any]:
    project = str(project_id or "").strip().lower()
    if not project:
        return {"ok": False, "error": "project_id is required", "code": "project_id_required"}
    rows = list_registered_experiments(repo_root=repo_root, verify_paths=verify_paths)
    record = next((row for row in rows if str(row.get("project_id") or "") == project), None)
    if record is None:
        return {
            "ok": False,
            "error": f"registered experiment not found: {project}",
            "code": "registered_experiment_not_found",
            "project_id": project,
        }
    return {
        "ok": True,
        "project_id": project,
        "registry_path": REGISTRY_RELATIVE_PATH,
        "record": record,
    }


def write_experiment_registry(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Path:
    path = experiment_registry_path(repo_root=repo_root)
    fsio.write_json_atomic(path, dict(payload))
    return path
