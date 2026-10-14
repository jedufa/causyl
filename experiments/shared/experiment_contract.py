"""Canonical experiment contract schema, validation, and hash policy."""

from __future__ import annotations

import hashlib
import json
from json import JSONDecodeError
from pathlib import Path
from types import MappingProxyType
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .experiment_artifact_boundary import summarize_artifact_boundary_contract
from .experiment_artifact_boundary import validate_artifact_boundary_contract
from .shared_resource_contract import SharedResourceContractError
from .shared_resource_contract import map_experiment_resource_policy

SCHEMA_VERSION = "experiment_contract_v1"
CONTRACT_FAMILY = "experiment_contract"
SUPPORTED_MAJOR_VERSION = 1
HASH_PREFIX = "sha256:"

TOP_LEVEL_REQUIRED_FIELDS = (
    "schema_version",
    "contract_hash",
    "compatibility",
    "project",
    "launch",
    "configuration",
    "observability",
    "control",
    "analysis",
    "resources",
    "limitations",
    "artifacts",
    "context_policy",
    "validation",
)

TOP_LEVEL_KNOWN_FIELDS = frozenset(TOP_LEVEL_REQUIRED_FIELDS)
ARG_TYPE_SET = frozenset({"path", "string", "int", "bool", "float", "string_list"})
PROGRESS_MODE_SET = frozenset({"wrapper", "file", "none"})
CONTROL_ACTION_TO_FLAG = MappingProxyType(
    {
        "pause": "pause_supported",
        "resume": "resume_supported",
        "graceful_stop": "stop_supported",
        "force_stop": "stop_supported",
        "restart": "restart_supported",
        "checkpoint": "checkpoint_supported",
        "branch_from_checkpoint": "branch_from_checkpoint_supported",
    }
)

ERROR_CODES = MappingProxyType(
    {
        "contract_not_found": "contract_not_found",
        "invalid_contract_json": "invalid_contract_json",
        "missing_field": "missing_field",
        "invalid_type": "invalid_type",
        "unknown_schema_version": "unknown_schema_version",
        "unknown_major_version": "unknown_major_version",
        "invalid_contract_family": "invalid_contract_family",
        "invalid_runner_declaration": "invalid_runner_declaration",
        "invalid_resource_policy": "invalid_resource_policy",
        "invalid_artifact_policy": "invalid_artifact_policy",
        "invalid_context_policy": "invalid_context_policy",
        "invalid_control_policy": "invalid_control_policy",
        "invalid_contract_hash": "invalid_contract_hash",
        "unknown_field": "unknown_field",
        "missing_required_path_declaration": "missing_required_path_declaration",
        "declared_path_not_found": "declared_path_not_found",
        "runner_entrypoint_not_found": "runner_entrypoint_not_found",
        "unsupported_prelaunch_check": "unsupported_prelaunch_check",
    }
)

PRELAUNCH_CHECK_PATH_FIELDS = MappingProxyType(
    {
        "profiles_root_exists": ("configuration", "profiles_root"),
        "registry_root_exists": ("configuration", "registry_root"),
    }
)


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[2]


def experiment_contract_path(project_id: str, *, repo_root: Optional[Path] = None) -> Path:
    project = str(project_id or "").strip().lower()
    if not project:
        raise ValueError("project_id is required")
    root = _repo_root(repo_root)
    return (root / "experiments" / "projects" / project / "_runner" / "experiment_contract.json").resolve()


def _normalize_text(value: Any) -> str:
    return str(value or "").strip()


def _sorted_unique_text_list(value: Any) -> List[str]:
    if not isinstance(value, list):
        return []
    items = {_normalize_text(item) for item in value}
    return sorted(item for item in items if item)


def _normalize_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    text = _normalize_text(value)
    if not text:
        return None
    try:
        return int(text)
    except ValueError:
        return None


def _normalize_number(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = _normalize_text(value)
    if not text:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def _normalize_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    return None


def _normalize_json_like(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key in sorted(value.keys(), key=lambda raw: _normalize_text(raw)):
            norm_key = _normalize_text(key)
            if not norm_key:
                continue
            out[norm_key] = _normalize_json_like(value[key])
        return out
    if isinstance(value, list):
        return [_normalize_json_like(item) for item in value]
    if isinstance(value, str):
        return value.strip()
    return value


def _normalize_unknown_fields(obj: Mapping[str, Any], known_fields: frozenset[str]) -> Dict[str, Any]:
    extra: Dict[str, Any] = {}
    for key in sorted(obj.keys(), key=lambda raw: _normalize_text(raw)):
        norm_key = _normalize_text(key)
        if not norm_key or norm_key in known_fields:
            continue
        extra[norm_key] = _normalize_json_like(obj[key])
    return extra


def _get_nested_text(obj: Mapping[str, Any], path: Sequence[str]) -> str:
    current: Any = obj
    for key in path:
        if not isinstance(current, Mapping):
            return ""
        current = current.get(key)
    return _normalize_text(current)


def _resolve_contract_declared_path(path_text: str, *, repo_root: Path) -> Path:
    candidate = Path(path_text).expanduser()
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / candidate).resolve()


def _path_display(path: Path, *, repo_root: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        return str(path.resolve())


def _runner_module_candidates(command: Sequence[str], *, repo_root: Path) -> List[Path]:
    argv = [str(part or "").strip() for part in command if str(part or "").strip()]
    for idx, token in enumerate(argv):
        if token == "-m" and idx + 1 < len(argv):
            module_name = argv[idx + 1].strip()
            if not module_name:
                break
            module_rel = Path(*module_name.split("."))
            return [
                (repo_root / module_rel).with_suffix(".py"),
                repo_root / module_rel / "__main__.py",
            ]
    return []


def _normalize_entrypoints(value: Any) -> Dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, Any] = {}
    for runner_id in sorted(value.keys(), key=lambda raw: _normalize_text(raw)):
        runner_key = _normalize_text(runner_id)
        runner = value[runner_id]
        if not runner_key or not isinstance(runner, dict):
            continue
        command: List[str] = []
        for part in list(runner.get("command") or []):
            text = _normalize_text(part)
            if text:
                command.append(text)
        env_obj = runner.get("env") if isinstance(runner.get("env"), dict) else {}
        env = {str(k).strip(): str(v).strip() for k, v in sorted(env_obj.items(), key=lambda item: _normalize_text(item[0])) if str(k).strip()}
        normalized_runner: Dict[str, Any] = {
            "command": command,
            "working_dir": _normalize_text(runner.get("working_dir")),
            "env": env,
        }
        normalized_runner.update(_normalize_unknown_fields(runner, frozenset({"command", "working_dir", "env"})))
        out[runner_key] = normalized_runner
    return out


def _normalize_arg_types(value: Any) -> Dict[str, str]:
    if not isinstance(value, dict):
        return {}
    out: Dict[str, str] = {}
    for arg_name in sorted(value.keys(), key=lambda raw: _normalize_text(raw)):
        key = _normalize_text(arg_name)
        arg_type = _normalize_text(value[arg_name]).lower()
        if key:
            out[key] = arg_type
    return out


def _normalize_status_files(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    rows: List[Dict[str, Any]] = []
    for raw in value:
        if not isinstance(raw, dict):
            continue
        row = {
            "path": _normalize_text(raw.get("path")),
            "type": _normalize_text(raw.get("type")),
            "priority": _normalize_int(raw.get("priority")),
        }
        row.update(_normalize_unknown_fields(raw, frozenset({"path", "type", "priority"})))
        rows.append(row)
    rows.sort(key=lambda row: (999999 if row.get("priority") is None else int(row["priority"]), str(row.get("path") or ""), str(row.get("type") or "")))
    return rows


def normalize_experiment_contract(raw: Mapping[str, Any]) -> Dict[str, Any]:
    obj = dict(raw or {})
    compatibility_raw = obj.get("compatibility") if isinstance(obj.get("compatibility"), dict) else {}
    project_raw = obj.get("project") if isinstance(obj.get("project"), dict) else {}
    launch_raw = obj.get("launch") if isinstance(obj.get("launch"), dict) else {}
    args_raw = launch_raw.get("args") if isinstance(launch_raw.get("args"), dict) else {}
    observability_raw = obj.get("observability") if isinstance(obj.get("observability"), dict) else {}
    progress_raw = observability_raw.get("progress") if isinstance(observability_raw.get("progress"), dict) else {}
    control_raw = obj.get("control") if isinstance(obj.get("control"), dict) else {}
    resources_raw = obj.get("resources") if isinstance(obj.get("resources"), dict) else {}
    estimated_footprint_raw = (
        resources_raw.get("estimated_footprint") if isinstance(resources_raw.get("estimated_footprint"), dict) else {}
    )
    context_raw = obj.get("context_policy") if isinstance(obj.get("context_policy"), dict) else {}
    validation_raw = obj.get("validation") if isinstance(obj.get("validation"), dict) else {}

    normalized: Dict[str, Any] = {
        "schema_version": _normalize_text(obj.get("schema_version") or SCHEMA_VERSION),
        "contract_hash": _normalize_text(obj.get("contract_hash")),
        "compatibility": {
            "contract_family": _normalize_text(compatibility_raw.get("contract_family") or CONTRACT_FAMILY),
            "major": _normalize_int(compatibility_raw.get("major")),
            "minor": _normalize_int(compatibility_raw.get("minor")),
        },
        "project": {
            "project_id": _normalize_text(project_raw.get("project_id")).lower(),
            "display_name": _normalize_text(project_raw.get("display_name")),
            "owner": _normalize_text(project_raw.get("owner")),
            "tags": _sorted_unique_text_list(project_raw.get("tags")),
        },
        "launch": {
            "default_runner_id": _normalize_text(launch_raw.get("default_runner_id")),
            "supported_runners": _sorted_unique_text_list(launch_raw.get("supported_runners")),
            "entrypoints": _normalize_entrypoints(launch_raw.get("entrypoints")),
            "args": {
                "required": _sorted_unique_text_list(args_raw.get("required")),
                "optional": _sorted_unique_text_list(args_raw.get("optional")),
                "types": _normalize_arg_types(args_raw.get("types")),
            },
        },
        "configuration": _normalize_json_like(obj.get("configuration") if isinstance(obj.get("configuration"), dict) else {}),
        "observability": {
            "status_files": _normalize_status_files(observability_raw.get("status_files")),
            "progress": {
                "mode": _normalize_text(progress_raw.get("mode")).lower(),
                "fields": _sorted_unique_text_list(progress_raw.get("fields")),
            },
            "health_signals": _sorted_unique_text_list(observability_raw.get("health_signals")),
        },
        "control": {
            "pause_supported": bool(control_raw.get("pause_supported")) if isinstance(control_raw.get("pause_supported"), bool) else False,
            "resume_supported": bool(control_raw.get("resume_supported")) if isinstance(control_raw.get("resume_supported"), bool) else False,
            "stop_supported": bool(control_raw.get("stop_supported")) if isinstance(control_raw.get("stop_supported"), bool) else False,
            "restart_supported": bool(control_raw.get("restart_supported")) if isinstance(control_raw.get("restart_supported"), bool) else False,
            "checkpoint_supported": bool(control_raw.get("checkpoint_supported")) if isinstance(control_raw.get("checkpoint_supported"), bool) else False,
            "branch_from_checkpoint_supported": bool(control_raw.get("branch_from_checkpoint_supported"))
            if isinstance(control_raw.get("branch_from_checkpoint_supported"), bool)
            else False,
            "intervention_actions": _sorted_unique_text_list(control_raw.get("intervention_actions")),
            "unsupported_actions": _sorted_unique_text_list(control_raw.get("unsupported_actions")),
        },
        "analysis": _normalize_json_like(obj.get("analysis") if isinstance(obj.get("analysis"), dict) else {}),
        "resources": {
            "resource_class": _normalize_text(resources_raw.get("resource_class")),
            "project_semaphore": _normalize_text(resources_raw.get("project_semaphore")),
            "max_parallel_runs": _normalize_int(resources_raw.get("max_parallel_runs")),
            "estimated_footprint": {
                "gpu_count": _normalize_number(estimated_footprint_raw.get("gpu_count")),
                "cpu_cores": _normalize_number(estimated_footprint_raw.get("cpu_cores")),
                "ram_gb": _normalize_number(estimated_footprint_raw.get("ram_gb")),
            },
        },
        "limitations": _sorted_unique_text_list(obj.get("limitations")),
        "artifacts": _normalize_json_like(obj.get("artifacts") if isinstance(obj.get("artifacts"), dict) else {}),
        "context_policy": {
            "required_context_bundle": _normalize_text(context_raw.get("required_context_bundle")),
            "additional_context_refs_allowed": bool(context_raw.get("additional_context_refs_allowed"))
            if isinstance(context_raw.get("additional_context_refs_allowed"), bool)
            else False,
            "forbidden_context_refs": _sorted_unique_text_list(context_raw.get("forbidden_context_refs")),
        },
        "validation": {
            "schema_validation_required": bool(validation_raw.get("schema_validation_required"))
            if isinstance(validation_raw.get("schema_validation_required"), bool)
            else False,
            "hash_validation_required": bool(validation_raw.get("hash_validation_required"))
            if isinstance(validation_raw.get("hash_validation_required"), bool)
            else False,
            "prelaunch_checks": _sorted_unique_text_list(validation_raw.get("prelaunch_checks")),
        },
    }
    for section_name, raw_section, known_fields in (
        ("compatibility", compatibility_raw, frozenset({"contract_family", "major", "minor"})),
        ("project", project_raw, frozenset({"project_id", "display_name", "owner", "tags"})),
        ("launch", launch_raw, frozenset({"default_runner_id", "supported_runners", "entrypoints", "args"})),
        ("observability", observability_raw, frozenset({"status_files", "progress", "health_signals"})),
        (
            "control",
            control_raw,
            frozenset(
                {
                    "pause_supported",
                    "resume_supported",
                    "stop_supported",
                    "restart_supported",
                    "checkpoint_supported",
                    "branch_from_checkpoint_supported",
                    "intervention_actions",
                    "unsupported_actions",
                }
            ),
        ),
        ("resources", resources_raw, frozenset({"resource_class", "project_semaphore", "max_parallel_runs", "estimated_footprint"})),
        (
            "context_policy",
            context_raw,
            frozenset({"required_context_bundle", "additional_context_refs_allowed", "forbidden_context_refs"}),
        ),
        ("validation", validation_raw, frozenset({"schema_validation_required", "hash_validation_required", "prelaunch_checks"})),
    ):
        normalized[section_name].update(_normalize_unknown_fields(raw_section, known_fields))
    normalized["launch"]["args"].update(_normalize_unknown_fields(args_raw, frozenset({"required", "optional", "types"})))
    normalized["observability"]["progress"].update(_normalize_unknown_fields(progress_raw, frozenset({"mode", "fields"})))
    normalized["resources"]["estimated_footprint"].update(
        _normalize_unknown_fields(estimated_footprint_raw, frozenset({"gpu_count", "cpu_cores", "ram_gb"}))
    )
    normalized["configuration"] = dict(normalized["configuration"] or {})
    normalized["configuration"].update(
        _normalize_unknown_fields(
            obj.get("configuration") if isinstance(obj.get("configuration"), dict) else {},
            frozenset({"profiles_root", "registry_root", "override_modes", "default_profile"}),
        )
    )
    if "override_modes" in normalized["configuration"]:
        normalized["configuration"]["override_modes"] = _sorted_unique_text_list(normalized["configuration"].get("override_modes"))
    normalized["analysis"] = dict(normalized["analysis"] or {})
    normalized["analysis"].update(
        _normalize_unknown_fields(
            obj.get("analysis") if isinstance(obj.get("analysis"), dict) else {},
            frozenset({"primary_artifacts", "recipes", "default_recipe"}),
        )
    )
    if "primary_artifacts" in normalized["analysis"]:
        normalized["analysis"]["primary_artifacts"] = _sorted_unique_text_list(normalized["analysis"].get("primary_artifacts"))
    if "recipes" in normalized["analysis"]:
        normalized["analysis"]["recipes"] = _sorted_unique_text_list(normalized["analysis"].get("recipes"))
    normalized["artifacts"] = dict(normalized["artifacts"] or {})
    normalized["artifacts"].update(
        _normalize_unknown_fields(
            obj.get("artifacts") if isinstance(obj.get("artifacts"), dict) else {},
            frozenset({"repo_tracked_outputs", "external_data_root", "artifact_manifest_required"}),
        )
    )
    if "repo_tracked_outputs" in normalized["artifacts"]:
        normalized["artifacts"]["repo_tracked_outputs"] = _sorted_unique_text_list(normalized["artifacts"].get("repo_tracked_outputs"))
    normalized.update(_normalize_unknown_fields(obj, TOP_LEVEL_KNOWN_FIELDS))
    return normalized


def canonicalize_experiment_contract(raw: Mapping[str, Any]) -> Dict[str, Any]:
    """Backward-compatible alias for normalize_experiment_contract."""
    return normalize_experiment_contract(raw)


def canonical_experiment_contract_json(normalized_contract: Mapping[str, Any], *, include_contract_hash: bool = True) -> str:
    obj = dict(normalized_contract)
    if not include_contract_hash:
        obj.pop("contract_hash", None)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def compute_experiment_contract_hash(raw_contract: Mapping[str, Any]) -> str:
    normalized = normalize_experiment_contract(raw_contract)
    payload = canonical_experiment_contract_json(normalized, include_contract_hash=False).encode("utf-8")
    return HASH_PREFIX + hashlib.sha256(payload).hexdigest()


def validate_experiment_contract(raw_contract: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    raw = dict(raw_contract or {})
    normalized = normalize_experiment_contract(raw)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    unknown_top_level = sorted(set(raw.keys()) - TOP_LEVEL_KNOWN_FIELDS)
    for key in unknown_top_level:
        warnings.append(
            {
                "code": ERROR_CODES["unknown_field"],
                "field": str(key),
                "message": f"unknown top-level field ignored for compatibility: {key}",
            }
        )

    for field in TOP_LEVEL_REQUIRED_FIELDS:
        value = normalized.get(field)
        if value in ("", None) or (isinstance(value, dict) and not value) or (isinstance(value, list) and field != "limitations" and not value):
            errors.append({"code": ERROR_CODES["missing_field"], "field": field, "message": f"{field} is required"})

    if normalized["schema_version"] != SCHEMA_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["unknown_schema_version"],
                "field": "schema_version",
                "message": f"schema_version must be {SCHEMA_VERSION}",
            }
        )

    compatibility = normalized["compatibility"]
    if compatibility.get("contract_family") != CONTRACT_FAMILY:
        errors.append(
            {
                "code": ERROR_CODES["invalid_contract_family"],
                "field": "compatibility.contract_family",
                "message": f"compatibility.contract_family must be {CONTRACT_FAMILY}",
            }
        )
    if compatibility.get("major") != SUPPORTED_MAJOR_VERSION:
        errors.append(
            {
                "code": ERROR_CODES["unknown_major_version"],
                "field": "compatibility.major",
                "message": f"compatibility.major must be {SUPPORTED_MAJOR_VERSION}",
            }
        )
    if compatibility.get("minor") is None or int(compatibility["minor"]) < 0:
        errors.append(
            {
                "code": ERROR_CODES["invalid_type"],
                "field": "compatibility.minor",
                "message": "compatibility.minor must be a non-negative integer",
            }
        )

    project = normalized["project"]
    if not project.get("project_id"):
        errors.append({"code": ERROR_CODES["missing_field"], "field": "project.project_id", "message": "project.project_id is required"})
    if not project.get("display_name"):
        errors.append(
            {
                "code": ERROR_CODES["missing_field"],
                "field": "project.display_name",
                "message": "project.display_name is required",
            }
        )

    launch = normalized["launch"]
    supported_runners = list(launch.get("supported_runners") or [])
    entrypoints = dict(launch.get("entrypoints") or {})
    default_runner = str(launch.get("default_runner_id") or "")
    if not supported_runners:
        errors.append(
            {
                "code": ERROR_CODES["invalid_runner_declaration"],
                "field": "launch.supported_runners",
                "message": "launch.supported_runners must list at least one runner",
            }
        )
    if default_runner not in supported_runners:
        errors.append(
            {
                "code": ERROR_CODES["invalid_runner_declaration"],
                "field": "launch.default_runner_id",
                "message": "launch.default_runner_id must exist in launch.supported_runners",
            }
        )
    for runner_id in supported_runners:
        row = entrypoints.get(runner_id)
        if not isinstance(row, dict):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_runner_declaration"],
                    "field": f"launch.entrypoints.{runner_id}",
                    "message": f"launch.entrypoints must define {runner_id}",
                }
            )
            continue
        command = row.get("command")
        if not isinstance(command, list) or not command or any(not _normalize_text(part) for part in command):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_runner_declaration"],
                    "field": f"launch.entrypoints.{runner_id}.command",
                    "message": "runner command must be a non-empty argv list",
                }
            )
            continue
        if not _normalize_text(row.get("working_dir")):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_runner_declaration"],
                    "field": f"launch.entrypoints.{runner_id}.working_dir",
                    "message": "runner working_dir is required",
                }
            )
        runner_required_raw = row.get("required_args")
        runner_optional_raw = row.get("optional_args")
        if runner_required_raw not in (None, "") and not isinstance(runner_required_raw, list):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_runner_declaration"],
                    "field": f"launch.entrypoints.{runner_id}.required_args",
                    "message": "runner required_args must be a list",
                }
            )
        if runner_optional_raw not in (None, "") and not isinstance(runner_optional_raw, list):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_runner_declaration"],
                    "field": f"launch.entrypoints.{runner_id}.optional_args",
                    "message": "runner optional_args must be a list",
                }
            )
    args_contract = launch.get("args") if isinstance(launch.get("args"), dict) else {}
    declared_args = set(args_contract.get("required") or []) | set(args_contract.get("optional") or [])
    arg_types = args_contract.get("types") if isinstance(args_contract.get("types"), dict) else {}
    overlap = sorted(set(args_contract.get("required") or []) & set(args_contract.get("optional") or []))
    if overlap:
        errors.append(
            {
                "code": ERROR_CODES["invalid_runner_declaration"],
                "field": "launch.args",
                "message": f"launch args cannot be both required and optional: {', '.join(overlap)}",
            }
        )
    missing_types = sorted(arg for arg in declared_args if arg not in arg_types)
    if missing_types:
        errors.append(
            {
                "code": ERROR_CODES["invalid_runner_declaration"],
                "field": "launch.args.types",
                "message": f"launch.args.types missing entries for: {', '.join(missing_types)}",
            }
        )
    invalid_types = sorted(arg for arg, arg_type in arg_types.items() if arg_type not in ARG_TYPE_SET)
    if invalid_types:
        errors.append(
            {
                "code": ERROR_CODES["invalid_runner_declaration"],
                "field": "launch.args.types",
                "message": f"launch.args.types must use supported types only: {', '.join(invalid_types)}",
            }
        )
    for runner_id in supported_runners:
        row = entrypoints.get(runner_id)
        if not isinstance(row, dict):
            continue
        runner_required = _sorted_unique_text_list(row.get("required_args")) if "required_args" in row else list(args_contract.get("required") or [])
        runner_optional = _sorted_unique_text_list(row.get("optional_args")) if "optional_args" in row else list(args_contract.get("optional") or [])
        runner_overlap = sorted(set(runner_required) & set(runner_optional))
        if runner_overlap:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_runner_declaration"],
                    "field": f"launch.entrypoints.{runner_id}",
                    "message": f"runner args cannot be both required and optional: {', '.join(runner_overlap)}",
                }
            )
        runner_missing_types = sorted(arg for arg in set(runner_required) | set(runner_optional) if arg not in arg_types)
        if runner_missing_types:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_runner_declaration"],
                    "field": f"launch.entrypoints.{runner_id}",
                    "message": f"runner args must be declared in launch.args.types: {', '.join(runner_missing_types)}",
                }
            )

    observability = normalized["observability"]
    progress = observability.get("progress") if isinstance(observability.get("progress"), dict) else {}
    if progress.get("mode") not in PROGRESS_MODE_SET:
        errors.append(
            {
                "code": ERROR_CODES["invalid_type"],
                "field": "observability.progress.mode",
                "message": "observability.progress.mode must be one of wrapper, file, none",
            }
        )
    seen_priorities: set[int] = set()
    for idx, row in enumerate(list(observability.get("status_files") or [])):
        priority = row.get("priority")
        if not isinstance(priority, int) or priority <= 0:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_type"],
                    "field": f"observability.status_files[{idx}].priority",
                    "message": "status file priority must be a positive integer",
                }
            )
        elif priority in seen_priorities:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_type"],
                    "field": "observability.status_files",
                    "message": "status file priorities must be unique",
                }
            )
        else:
            seen_priorities.add(priority)
        if not _normalize_text(row.get("path")) or not _normalize_text(row.get("type")):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_type"],
                    "field": f"observability.status_files[{idx}]",
                    "message": "status files require path and type",
                }
            )

    control = normalized["control"]
    bool_fields = (
        "pause_supported",
        "resume_supported",
        "stop_supported",
        "restart_supported",
        "checkpoint_supported",
        "branch_from_checkpoint_supported",
    )
    for field in bool_fields:
        if _normalize_bool(control.get(field)) is None:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_control_policy"],
                    "field": f"control.{field}",
                    "message": f"control.{field} must be a boolean",
                }
            )
    overlap_actions = sorted(set(control.get("intervention_actions") or []) & set(control.get("unsupported_actions") or []))
    if overlap_actions:
        errors.append(
            {
                "code": ERROR_CODES["invalid_control_policy"],
                "field": "control",
                "message": f"control actions cannot be both supported and unsupported: {', '.join(overlap_actions)}",
            }
        )
    for action in list(control.get("intervention_actions") or []):
        flag = CONTROL_ACTION_TO_FLAG.get(action)
        if not flag:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_control_policy"],
                    "field": "control.intervention_actions",
                    "message": f"unsupported intervention action declared: {action}",
                }
            )
            continue
        if not bool(control.get(flag)):
            errors.append(
                {
                    "code": ERROR_CODES["invalid_control_policy"],
                    "field": f"control.{flag}",
                    "message": f"{action} requires {flag}=true",
                }
            )

    analysis = normalized["analysis"]
    recipes = list(analysis.get("recipes") or [])
    default_recipe = _normalize_text(analysis.get("default_recipe"))
    if default_recipe and default_recipe not in recipes:
        errors.append(
            {
                "code": ERROR_CODES["invalid_type"],
                "field": "analysis.default_recipe",
                "message": "analysis.default_recipe must exist in analysis.recipes",
            }
        )

    artifact_boundary = validate_artifact_boundary_contract(
        normalized.get("artifacts") if isinstance(normalized.get("artifacts"), dict) else {},
        repo_root=_repo_root(repo_root),
    )
    for row in list(artifact_boundary.get("errors") or []):
        errors.append(
            {
                "code": ERROR_CODES["invalid_artifact_policy"],
                "field": str(row.get("field") or "artifacts"),
                "message": str(row.get("message") or "invalid artifact policy"),
            }
        )
    for row in list(artifact_boundary.get("warnings") or []):
        warnings.append(
            {
                "code": ERROR_CODES["invalid_artifact_policy"],
                "field": str(row.get("field") or "artifacts"),
                "message": str(row.get("message") or "artifact policy warning"),
            }
        )

    resources = normalized["resources"]
    max_parallel_runs = resources.get("max_parallel_runs")
    if not _normalize_text(resources.get("resource_class")):
        errors.append(
            {
                "code": ERROR_CODES["invalid_resource_policy"],
                "field": "resources.resource_class",
                "message": "resources.resource_class is required",
            }
        )
    if not isinstance(max_parallel_runs, int) or max_parallel_runs <= 0:
        errors.append(
            {
                "code": ERROR_CODES["invalid_resource_policy"],
                "field": "resources.max_parallel_runs",
                "message": "resources.max_parallel_runs must be a positive integer",
            }
        )
    try:
        canonical_resource_policy = map_experiment_resource_policy(resources)
    except SharedResourceContractError as exc:
        errors.append(
            {
                "code": ERROR_CODES["invalid_resource_policy"],
                "field": "resources",
                "message": str(exc),
            }
        )
        canonical_resource_policy = None
    estimated_footprint = resources.get("estimated_footprint") if isinstance(resources.get("estimated_footprint"), dict) else {}
    for key in ("gpu_count", "cpu_cores", "ram_gb"):
        value = estimated_footprint.get(key)
        if value is None or value < 0:
            errors.append(
                {
                    "code": ERROR_CODES["invalid_resource_policy"],
                    "field": f"resources.estimated_footprint.{key}",
                    "message": f"resources.estimated_footprint.{key} must be a non-negative number",
                }
            )

    context_policy = normalized["context_policy"]
    if not _normalize_text(context_policy.get("required_context_bundle")):
        errors.append(
            {
                "code": ERROR_CODES["invalid_context_policy"],
                "field": "context_policy.required_context_bundle",
                "message": "context_policy.required_context_bundle is required",
            }
        )
    if _normalize_bool(context_policy.get("additional_context_refs_allowed")) is None:
        errors.append(
            {
                "code": ERROR_CODES["invalid_context_policy"],
                "field": "context_policy.additional_context_refs_allowed",
                "message": "context_policy.additional_context_refs_allowed must be a boolean",
            }
        )

    validation = normalized["validation"]
    if _normalize_bool(validation.get("schema_validation_required")) is None:
        errors.append(
            {
                "code": ERROR_CODES["invalid_type"],
                "field": "validation.schema_validation_required",
                "message": "validation.schema_validation_required must be a boolean",
            }
        )
    if _normalize_bool(validation.get("hash_validation_required")) is None:
        errors.append(
            {
                "code": ERROR_CODES["invalid_type"],
                "field": "validation.hash_validation_required",
                "message": "validation.hash_validation_required must be a boolean",
            }
        )

    computed_hash = compute_experiment_contract_hash(normalized)
    stored_hash = _normalize_text(normalized.get("contract_hash"))
    if not stored_hash.startswith(HASH_PREFIX):
        errors.append(
            {
                "code": ERROR_CODES["invalid_contract_hash"],
                "field": "contract_hash",
                "message": "contract_hash must use sha256:<hex> format",
            }
        )
    elif stored_hash != computed_hash:
        errors.append(
            {
                "code": ERROR_CODES["invalid_contract_hash"],
                "field": "contract_hash",
                "message": "contract_hash does not match canonical contract payload",
            }
        )

    ok = not errors
    return {
        "ok": ok,
        "failure_class": "" if ok else "experiment_contract_validation_failed",
        "errors": errors,
        "warnings": warnings,
        "normalized_contract": normalized,
        "canonical_json": canonical_experiment_contract_json(normalized),
        "computed_contract_hash": computed_hash,
        "canonical_resource_policy": canonical_resource_policy,
    }


def summarize_experiment_contract(
    normalized_contract: Mapping[str, Any],
    *,
    contract_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    project = normalized_contract.get("project") if isinstance(normalized_contract.get("project"), dict) else {}
    launch = normalized_contract.get("launch") if isinstance(normalized_contract.get("launch"), dict) else {}
    resources = normalized_contract.get("resources") if isinstance(normalized_contract.get("resources"), dict) else {}
    context_policy = (
        normalized_contract.get("context_policy") if isinstance(normalized_contract.get("context_policy"), dict) else {}
    )
    observability = (
        normalized_contract.get("observability") if isinstance(normalized_contract.get("observability"), dict) else {}
    )
    progress = observability.get("progress") if isinstance(observability.get("progress"), dict) else {}
    control = normalized_contract.get("control") if isinstance(normalized_contract.get("control"), dict) else {}
    analysis = normalized_contract.get("analysis") if isinstance(normalized_contract.get("analysis"), dict) else {}
    configuration = (
        normalized_contract.get("configuration") if isinstance(normalized_contract.get("configuration"), dict) else {}
    )
    limitations = [_normalize_text(row) for row in list(normalized_contract.get("limitations") or []) if _normalize_text(row)]
    status_files = [
        str(row.get("path") or "").strip()
        for row in list(observability.get("status_files") or [])
        if str(row.get("path") or "").strip()
    ]
    progress_mode = _normalize_text(progress.get("mode"))
    progress_fields = [_normalize_text(row) for row in list(progress.get("fields") or []) if _normalize_text(row)]
    health_signals = [_normalize_text(row) for row in list(observability.get("health_signals") or []) if _normalize_text(row)]
    supported_actions = [_normalize_text(row) for row in list(control.get("intervention_actions") or []) if _normalize_text(row)]
    unsupported_actions = [_normalize_text(row) for row in list(control.get("unsupported_actions") or []) if _normalize_text(row)]
    analysis_recipes = [_normalize_text(row) for row in list(analysis.get("recipes") or []) if _normalize_text(row)]
    primary_artifacts = [_normalize_text(row) for row in list(analysis.get("primary_artifacts") or []) if _normalize_text(row)]
    summary: Dict[str, Any] = {
        "project_id": _normalize_text(project.get("project_id")),
        "display_name": _normalize_text(project.get("display_name")),
        "contract_hash": _normalize_text(normalized_contract.get("contract_hash")),
        "default_runner_id": _normalize_text(launch.get("default_runner_id")),
        "supported_runners": list(launch.get("supported_runners") or []),
        "required_context_bundle": _normalize_text(context_policy.get("required_context_bundle")),
        "resource_class": _normalize_text(resources.get("resource_class")),
        "max_parallel_runs": resources.get("max_parallel_runs"),
        "progress_mode": progress_mode,
        "intervention_actions": supported_actions,
        "prelaunch_checks": list(
            ((normalized_contract.get("validation") if isinstance(normalized_contract.get("validation"), dict) else {}).get("prelaunch_checks") or [])
        ),
        "status_surfaces": {
            "declared": bool(status_files or health_signals or (progress_mode and progress_mode != "none") or progress_fields),
            "status_files": status_files,
            "progress_mode": progress_mode or "none",
            "progress_fields": progress_fields,
            "health_signals": health_signals,
        },
        "control_declarations": {
            "supported_actions": supported_actions,
            "unsupported_actions": unsupported_actions,
            "pause_supported": bool(control.get("pause_supported")),
            "resume_supported": bool(control.get("resume_supported")),
            "stop_supported": bool(control.get("stop_supported")),
            "restart_supported": bool(control.get("restart_supported")),
            "checkpoint_supported": bool(control.get("checkpoint_supported")),
            "branch_from_checkpoint_supported": bool(control.get("branch_from_checkpoint_supported")),
        },
        "analysis_entrypoints": {
            "declared": bool(analysis_recipes or primary_artifacts or _normalize_text(analysis.get("default_recipe"))),
            "recipes": analysis_recipes,
            "default_recipe": _normalize_text(analysis.get("default_recipe")),
            "primary_artifacts": primary_artifacts,
        },
        "limitations": limitations,
        "shared_resource_contract": map_experiment_resource_policy(resources),
    }
    if contract_path is not None:
        summary["contract_path"] = _path_display(contract_path, repo_root=root)

    declared_paths: Dict[str, str] = {}
    for key in ("profiles_root", "registry_root"):
        value = _normalize_text(configuration.get(key))
        if value:
            declared_paths[key] = value
    if declared_paths:
        summary["declared_paths"] = declared_paths

    if status_files:
        summary["status_files"] = status_files

    artifacts = normalized_contract.get("artifacts") if isinstance(normalized_contract.get("artifacts"), dict) else {}
    artifact_boundary = summarize_artifact_boundary_contract(artifacts, repo_root=root)
    if artifact_boundary.get("declared"):
        summary["artifact_boundary"] = artifact_boundary

    owner = _normalize_text(project.get("owner"))
    if owner:
        summary["owner"] = owner
    tags = list(project.get("tags") or [])
    if tags:
        summary["tags"] = tags
    return summary


def prelaunch_check_experiment_contract(
    normalized_contract: Mapping[str, Any],
    *,
    contract_path: Optional[Path] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    validation = validate_experiment_contract(normalized_contract, repo_root=root)
    if not validation["ok"]:
        errors.extend(dict(row) for row in validation["errors"])

    if contract_path is not None and (not contract_path.exists() or not contract_path.is_file()):
        errors.append(
            {
                "code": ERROR_CODES["contract_not_found"],
                "field": "contract_path",
                "message": f"experiment contract file not found: {contract_path}",
            }
        )

    normalized = validation["normalized_contract"]
    prelaunch_checks = list(
        ((normalized.get("validation") if isinstance(normalized.get("validation"), dict) else {}).get("prelaunch_checks") or [])
    )
    launch = normalized.get("launch") if isinstance(normalized.get("launch"), dict) else {}
    entrypoints = launch.get("entrypoints") if isinstance(launch.get("entrypoints"), dict) else {}
    default_runner_id = _normalize_text(launch.get("default_runner_id"))

    for check_name in prelaunch_checks:
        if check_name in PRELAUNCH_CHECK_PATH_FIELDS:
            path_parts = PRELAUNCH_CHECK_PATH_FIELDS[check_name]
            field_name = ".".join(path_parts)
            declared = _get_nested_text(normalized, path_parts)
            if not declared:
                errors.append(
                    {
                        "code": ERROR_CODES["missing_required_path_declaration"],
                        "field": field_name,
                        "message": f"{field_name} must be declared for prelaunch check {check_name}",
                    }
                )
                continue
            resolved = _resolve_contract_declared_path(declared, repo_root=root)
            if not resolved.exists():
                errors.append(
                    {
                        "code": ERROR_CODES["declared_path_not_found"],
                        "field": field_name,
                        "message": f"{field_name} does not exist: {_path_display(resolved, repo_root=root)}",
                    }
                )
            continue

        if check_name == "runner_exists":
            runner = entrypoints.get(default_runner_id) if default_runner_id else None
            if not isinstance(runner, dict):
                errors.append(
                    {
                        "code": ERROR_CODES["invalid_runner_declaration"],
                        "field": "launch.default_runner_id",
                        "message": "launch.default_runner_id must resolve to a declared entrypoint for prelaunch",
                    }
                )
                continue
            working_dir = _normalize_text(runner.get("working_dir"))
            if not working_dir:
                errors.append(
                    {
                        "code": ERROR_CODES["missing_required_path_declaration"],
                        "field": f"launch.entrypoints.{default_runner_id}.working_dir",
                        "message": f"launch.entrypoints.{default_runner_id}.working_dir is required for runner_exists",
                    }
                )
            else:
                runner_dir = _resolve_contract_declared_path(working_dir, repo_root=root)
                if not runner_dir.exists() or not runner_dir.is_dir():
                    errors.append(
                        {
                            "code": ERROR_CODES["declared_path_not_found"],
                            "field": f"launch.entrypoints.{default_runner_id}.working_dir",
                            "message": f"runner working_dir does not exist: {_path_display(runner_dir, repo_root=root)}",
                        }
                    )
            command = runner.get("command") if isinstance(runner.get("command"), list) else []
            module_candidates = _runner_module_candidates(command, repo_root=root)
            if module_candidates and not any(candidate.exists() and candidate.is_file() for candidate in module_candidates):
                display = ", ".join(_path_display(candidate, repo_root=root) for candidate in module_candidates)
                errors.append(
                    {
                        "code": ERROR_CODES["runner_entrypoint_not_found"],
                        "field": f"launch.entrypoints.{default_runner_id}.command",
                        "message": f"runner module target does not exist: {display}",
                    }
                )
            continue

        warnings.append(
            {
                "code": ERROR_CODES["unsupported_prelaunch_check"],
                "field": "validation.prelaunch_checks",
                "message": f"unsupported prelaunch check declared but ignored for compatibility: {check_name}",
            }
        )

    ok = not errors
    return {
        "ok": ok,
        "failure_class": "" if ok else "experiment_contract_prelaunch_failed",
        "errors": errors,
        "warnings": warnings,
    }


def read_experiment_contract(project_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    project_key = str(project_id or "").strip().lower()
    path = experiment_contract_path(project_key, repo_root=repo_root)
    base_result: Dict[str, Any] = {
        "ok": False,
        "failure_class": "",
        "project_id": project_key,
        "contract_path": str(path),
        "contract": None,
        "summary": {},
        "prelaunch": {"ok": False, "failure_class": "", "errors": [], "warnings": []},
        "errors": [],
        "warnings": [],
    }
    if not path.exists() or not path.is_file():
        base_result["failure_class"] = "experiment_contract_read_failed"
        base_result["errors"] = [
            {
                "code": ERROR_CODES["contract_not_found"],
                "field": "contract_path",
                "message": f"experiment contract not found for {project_key}: {path}",
            }
        ]
        return base_result

    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except JSONDecodeError as exc:
        base_result["failure_class"] = "experiment_contract_read_failed"
        base_result["errors"] = [
            {
                "code": ERROR_CODES["invalid_contract_json"],
                "field": "contract_path",
                "message": f"experiment contract is not valid JSON: {exc.msg}",
            }
        ]
        return base_result

    validation = validate_experiment_contract(raw, repo_root=repo_root)
    if not validation["ok"]:
        base_result["failure_class"] = validation["failure_class"]
        base_result["errors"] = [dict(row) for row in validation["errors"]]
        base_result["warnings"] = [dict(row) for row in validation["warnings"]]
        return base_result

    normalized = dict(validation["normalized_contract"])
    if normalized.get("project", {}).get("project_id") != project_key:
        base_result["failure_class"] = "experiment_contract_read_failed"
        base_result["errors"] = [
            {
                "code": ERROR_CODES["invalid_type"],
                "field": "project.project_id",
                "message": f"experiment contract project_id mismatch for {project_key}",
            }
        ]
        return base_result

    prelaunch = prelaunch_check_experiment_contract(normalized, contract_path=path, repo_root=repo_root)
    summary = summarize_experiment_contract(normalized, contract_path=path, repo_root=repo_root)
    base_result.update(
        {
            "ok": bool(prelaunch["ok"]),
            "failure_class": prelaunch["failure_class"],
            "contract": normalized,
            "summary": summary,
            "prelaunch": prelaunch,
            "warnings": [dict(row) for row in validation["warnings"]] + [dict(row) for row in prelaunch["warnings"]],
            "errors": [dict(row) for row in prelaunch["errors"]],
        }
    )
    return base_result


def load_experiment_contract(project_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    result = read_experiment_contract(project_id, repo_root=repo_root)
    if result["contract"] is None:
        reasons = "; ".join(row["message"] for row in result["errors"])
        if any(str(row.get("code") or "") == ERROR_CODES["contract_not_found"] for row in result["errors"]):
            raise FileNotFoundError(reasons)
        raise ValueError(f"invalid experiment contract for {project_id}: {reasons}")
    if not result.get("ok"):
        reasons = "; ".join(str(row.get("message") or "") for row in list(result.get("errors") or []))
        raise ValueError(f"experiment contract prelaunch failed for {project_id}: {reasons}")
    return dict(result["contract"])
