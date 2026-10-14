"""Shared manifest-driven contract for project runner jobs.

This module lets the scheduler launch project-local experiment adapters through
one stable interface instead of hardcoding per-project commands.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from experiments_shared.experiment_contract import experiment_contract_path
from experiments_shared.experiment_contract import read_experiment_contract
from experiments_shared.experiment_control_policy import resolve_experiment_control_policy
from experiments_shared.runner_utils import normalize_project_runs_root

ARG_TYPE_SET = {"str", "string", "path", "int", "float", "bool", "string_list"}
MANIFEST_SCHEMA_VERSION = "project_runner_manifest_v1"
TRUE_TOKENS = {"1", "true", "yes", "on"}
FALSE_TOKENS = {"0", "false", "no", "off", ""}


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[2]


def _project_manifest_path(project_id: str, *, repo_root: Optional[Path] = None) -> Path:
    root = _repo_root(repo_root)
    project = str(project_id or "").strip().lower()
    if not project:
        raise ValueError("project_id is required")
    return (root / "experiments" / "projects" / project / "_runner" / "project_runner_manifest.json").resolve()


def load_project_runner_manifest(project_id: str, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    path = _project_manifest_path(project_id, repo_root=repo_root)
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"project runner manifest not found for {project_id}: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if str(data.get("schema_version") or "").strip() != MANIFEST_SCHEMA_VERSION:
        raise ValueError(f"unsupported project runner manifest schema for {project_id}")
    if str(data.get("project_id") or "").strip().lower() != str(project_id or "").strip().lower():
        raise ValueError(f"project runner manifest project_id mismatch for {project_id}")
    runners = data.get("runners")
    if not isinstance(runners, dict) or not runners:
        raise ValueError(f"project runner manifest has no runners for {project_id}")
    return data


def _manifest_compatibility_required_message(project_id: str) -> str:
    return (
        f"experiment contract required for {project_id}; "
        "set project_run.allow_manifest_compatibility=true to use the legacy manifest compatibility path"
    )


def list_project_runners(
    project_id: str,
    *,
    repo_root: Optional[Path] = None,
    allow_manifest_compatibility: bool = False,
) -> List[str]:
    root = _repo_root(repo_root)
    contract_path = experiment_contract_path(project_id, repo_root=root)
    if contract_path.exists() and contract_path.is_file():
        contract_result = read_experiment_contract(project_id, repo_root=root)
        if not contract_result.get("contract"):
            messages = "; ".join(str(row.get("message") or "") for row in list(contract_result.get("errors") or []))
            raise ValueError(f"invalid experiment contract for {project_id}: {messages}")
        if not contract_result.get("ok"):
            messages = "; ".join(str(row.get("message") or "") for row in list(contract_result.get("errors") or []))
            raise ValueError(f"experiment contract prelaunch failed for {project_id}: {messages}")
        launch = contract_result["contract"].get("launch") if isinstance(contract_result["contract"], dict) else {}
        return sorted(str(key).strip() for key in (launch.get("supported_runners") or []) if str(key).strip())
    if not allow_manifest_compatibility:
        raise ValueError(_manifest_compatibility_required_message(project_id))
    manifest = load_project_runner_manifest(project_id, repo_root=root)
    return sorted(str(key).strip() for key in manifest.get("runners", {}).keys() if str(key).strip())


def _split_contract_command(command: List[str]) -> tuple[List[str], str]:
    argv = [str(part or "").strip() for part in list(command or []) if str(part or "").strip()]
    if not argv:
        return [], ""
    for idx, token in enumerate(argv[:-1]):
        if token == "-m":
            return argv[: idx + 1], argv[idx + 1]
    return argv[:-1], argv[-1]


def _contract_module_candidates(command: List[str], *, repo_root: Path) -> List[Path]:
    argv = [str(part or "").strip() for part in list(command or []) if str(part or "").strip()]
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


def _normalize_contract_args_contract(launch_args: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    required = {
        str(name).strip()
        for name in list(launch_args.get("required") or [])
        if str(name).strip()
    }
    optional = {
        str(name).strip()
        for name in list(launch_args.get("optional") or [])
        if str(name).strip()
    }
    types = launch_args.get("types") if isinstance(launch_args.get("types"), dict) else {}
    out: Dict[str, Dict[str, Any]] = {}
    for arg_name in sorted(required | optional):
        arg_type = str(types.get(arg_name) or "string").strip().lower() or "string"
        out[arg_name] = {"type": arg_type, "required": arg_name in required}
    return out


def _normalize_contract_runner_args_contract(launch_args: Dict[str, Any], runner: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    required_source = runner.get("required_args") if "required_args" in runner else launch_args.get("required")
    optional_source = runner.get("optional_args") if "optional_args" in runner else launch_args.get("optional")
    arg_types = launch_args.get("types") if isinstance(launch_args.get("types"), dict) else {}
    return _normalize_contract_args_contract(
        {
            "required": required_source,
            "optional": optional_source,
            "types": arg_types,
        }
    )


def _contract_runner_path_candidates(command: List[str], *, repo_root: Path) -> List[Path]:
    candidates = _contract_module_candidates(command, repo_root=repo_root)
    if candidates:
        return candidates
    for token in list(command or []):
        text = str(token or "").strip()
        if not text or text.startswith("-"):
            continue
        if text in {"python", "python3", sys.executable}:
            continue
        candidate = Path(text).expanduser()
        if candidate.suffix == ".py":
            if candidate.is_absolute():
                return [candidate.resolve()]
            return [(repo_root / candidate).resolve()]
    return []


def _normalize_contract_command_for_execution(command: List[str], *, repo_root: Path) -> List[str]:
    argv = [str(part or "").strip() for part in list(command or []) if str(part or "").strip()]
    if not argv:
        return []
    if _contract_module_candidates(argv, repo_root=repo_root):
        return argv
    out = list(argv)
    for idx, token in enumerate(out):
        candidate = Path(token).expanduser()
        if token.startswith("-"):
            continue
        if candidate.suffix != ".py":
            continue
        if candidate.is_absolute():
            out[idx] = str(candidate.resolve())
        else:
            out[idx] = str((repo_root / candidate).resolve())
        break
    return out


def _scheduler_policy_from_contract(contract: Dict[str, Any]) -> Dict[str, Any]:
    resources = contract.get("resources") if isinstance(contract.get("resources"), dict) else {}
    context_policy = contract.get("context_policy") if isinstance(contract.get("context_policy"), dict) else {}
    return {
        "resource_class": str(resources.get("resource_class") or "").strip(),
        "project_semaphore": str(resources.get("project_semaphore") or "").strip(),
        "max_parallel_runs": int(resources.get("max_parallel_runs") or 0),
        "required_context_bundle": str(context_policy.get("required_context_bundle") or "").strip(),
        "additional_context_refs_allowed": bool(context_policy.get("additional_context_refs_allowed")),
        "forbidden_context_refs": [
            str(item or "").strip()
            for item in list(context_policy.get("forbidden_context_refs") or [])
            if str(item or "").strip()
        ],
    }


def _resolve_project_runner_from_contract(project_id: str, runner_id: str, *, repo_root: Path) -> Dict[str, Any]:
    contract_result = read_experiment_contract(project_id, repo_root=repo_root)
    contract = contract_result.get("contract") if isinstance(contract_result.get("contract"), dict) else None
    if contract is None:
        messages = "; ".join(str(row.get("message") or "") for row in list(contract_result.get("errors") or []))
        if not messages:
            messages = f"experiment contract not available for {project_id}"
        raise ValueError(f"invalid experiment contract for {project_id}: {messages}")
    if not contract_result.get("ok"):
        messages = "; ".join(str(row.get("message") or "") for row in list(contract_result.get("errors") or []))
        raise ValueError(f"experiment contract prelaunch failed for {project_id}: {messages}")
    control_policy_result = resolve_experiment_control_policy(
        {"project_run": {"project_id": project_id}, "runner_id": runner_id},
        repo_root=repo_root,
    )
    if not control_policy_result.get("ok"):
        messages = "; ".join(str(row.get("message") or "") for row in list(control_policy_result.get("errors") or []))
        raise ValueError(f"invalid experiment control policy for {project_id}: {messages}")

    launch = contract.get("launch") if isinstance(contract.get("launch"), dict) else {}
    entrypoints = launch.get("entrypoints") if isinstance(launch.get("entrypoints"), dict) else {}
    runner_key = str(runner_id or "").strip()
    runner = entrypoints.get(runner_key)
    if not isinstance(runner, dict):
        raise ValueError(f"unknown runner_id for {project_id}: {runner_id}")

    base_command = [str(part or "").strip() for part in list(runner.get("command") or []) if str(part or "").strip()]
    if not base_command:
        raise ValueError(f"runner {runner_id} for {project_id} is missing command")
    working_dir = str(runner.get("working_dir") or "").strip()
    if not working_dir:
        raise ValueError(f"runner {runner_id} for {project_id} is missing working_dir")
    working_dir_path = (repo_root / working_dir).resolve() if not Path(working_dir).is_absolute() else Path(working_dir).resolve()
    if not working_dir_path.exists() or not working_dir_path.is_dir():
        raise FileNotFoundError(f"project runner cwd not found: {working_dir}")

    candidates = _contract_runner_path_candidates(base_command, repo_root=repo_root)
    if candidates and not any(path.exists() and path.is_file() for path in candidates):
        display = ", ".join(str(path) for path in candidates)
        raise FileNotFoundError(f"runner entrypoint not found: {display}")
    base_command = _normalize_contract_command_for_execution(base_command, repo_root=repo_root)

    launch_args = launch.get("args") if isinstance(launch.get("args"), dict) else {}
    command_prefix, entrypoint = _split_contract_command(base_command)
    return {
        "project_id": str(project_id).strip().lower(),
        "runner_id": runner_key,
        "entrypoint": entrypoint,
        "entrypoint_path": "",
        "project_root": "",
        "cwd": working_dir,
        "description": "",
        "args": _normalize_contract_runner_args_contract(launch_args, runner),
        "command_prefix": command_prefix,
        "base_command": base_command,
        "manifest_path": "",
        "launch_definition_path": str(experiment_contract_path(project_id, repo_root=repo_root)),
        "launch_resolution_source": "experiment_contract",
        "contract_hash": str(contract.get("contract_hash") or "").strip(),
        "contract_projection": dict(control_policy_result.get("contract_projection") or {}),
        "scheduler_policy": _scheduler_policy_from_contract(contract),
        "configuration_roots": {
            "profiles_root": str(((contract.get("configuration") if isinstance(contract.get("configuration"), dict) else {}).get("profiles_root") or "")).strip(),
            "registry_root": str(((contract.get("configuration") if isinstance(contract.get("configuration"), dict) else {}).get("registry_root") or "")).strip(),
        },
    }


def resolve_project_runner(
    project_id: str,
    runner_id: str,
    *,
    repo_root: Optional[Path] = None,
    allow_manifest_compatibility: bool = False,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    contract_path = experiment_contract_path(project_id, repo_root=root)
    if contract_path.exists() and contract_path.is_file():
        return _resolve_project_runner_from_contract(project_id, runner_id, repo_root=root)
    if not allow_manifest_compatibility:
        raise ValueError(_manifest_compatibility_required_message(project_id))

    manifest = load_project_runner_manifest(project_id, repo_root=repo_root)
    runner_key = str(runner_id or "").strip()
    runner = manifest.get("runners", {}).get(runner_key)
    if not isinstance(runner, dict):
        raise ValueError(f"unknown runner_id for {project_id}: {runner_id}")
    entrypoint = str(runner.get("entrypoint") or "").strip()
    if not entrypoint:
        raise ValueError(f"runner {runner_id} for {project_id} is missing entrypoint")
    root = _repo_root(repo_root)
    entrypoint_path = (root / entrypoint).resolve()
    if not entrypoint_path.exists() or not entrypoint_path.is_file():
        raise FileNotFoundError(f"runner entrypoint not found: {entrypoint}")
    args = runner.get("args")
    if not isinstance(args, dict):
        raise ValueError(f"runner {runner_id} for {project_id} has invalid args contract")
    command_prefix_raw = runner.get("command_prefix")
    if command_prefix_raw in (None, ""):
        command_prefix_raw = manifest.get("command_prefix")
    command_prefix: List[str] = []
    if command_prefix_raw not in (None, ""):
        if not isinstance(command_prefix_raw, list):
            raise ValueError(f"runner {runner_id} for {project_id} has invalid command_prefix")
        for item in command_prefix_raw:
            text = str(item or "").strip()
            if text:
                command_prefix.append(text)
    return {
        "project_id": str(project_id).strip().lower(),
        "runner_id": runner_key,
        "entrypoint": entrypoint,
        "entrypoint_path": entrypoint_path,
        "project_root": str(manifest.get("project_root") or "").strip(),
        "cwd": str(runner.get("cwd") or manifest.get("project_root") or "").strip(),
        "description": str(runner.get("description") or "").strip(),
        "args": args,
        "command_prefix": command_prefix,
        "base_command": [],
        "manifest_path": str(_project_manifest_path(project_id, repo_root=repo_root)),
        "launch_definition_path": str(_project_manifest_path(project_id, repo_root=repo_root)),
        "launch_resolution_source": "manifest_compatibility",
        "contract_hash": "",
        "contract_projection": {},
        "scheduler_policy": {},
        "configuration_roots": {},
    }


def _normalize_string_list(raw: Any, *, field_name: str) -> List[str]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise ValueError(f"{field_name} must be a list")
    out: List[str] = []
    for item in raw:
        text = str(item or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _normalize_bool(raw: Any, *, field_name: str) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, int) and raw in (0, 1):
        return bool(raw)
    text = str(raw or "").strip().lower()
    if text in TRUE_TOKENS:
        return True
    if text in FALSE_TOKENS:
        return False
    raise ValueError(f"{field_name} must be a boolean")


def normalize_project_run(raw: Any, *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    project_id = str(obj.get("project_id") or "").strip().lower()
    runner_id = str(obj.get("runner_id") or "").strip()
    allow_manifest_compatibility = _normalize_bool(
        obj.get("allow_manifest_compatibility", False),
        field_name="project_run.allow_manifest_compatibility",
    )
    if not project_id:
        raise ValueError("project_run.project_id is required")
    if not runner_id:
        raise ValueError("project_run.runner_id is required")
    resolved = resolve_project_runner(
        project_id,
        runner_id,
        repo_root=repo_root,
        allow_manifest_compatibility=allow_manifest_compatibility,
    )
    root = _repo_root(repo_root)
    project_root_rel = str(resolved.get("project_root") or "").strip()
    project_root = (root / project_root_rel).resolve() if project_root_rel else root
    raw_args = obj.get("args")
    if raw_args is None:
        raw_args = obj.get("arguments")
    if raw_args in (None, ""):
        raw_args = {}
    if not isinstance(raw_args, dict):
        raise ValueError("project_run.args must be an object")
    allowed_args = resolved.get("args") if isinstance(resolved.get("args"), dict) else {}
    normalized_args: Dict[str, Any] = {}
    for arg_name, arg_spec in allowed_args.items():
        if not isinstance(arg_spec, dict):
            continue
        arg_type = str(arg_spec.get("type") or "string").strip().lower() or "string"
        if arg_type not in ARG_TYPE_SET:
            raise ValueError(f"runner arg type must be one of: {', '.join(sorted(ARG_TYPE_SET))}")
        present = arg_name in raw_args
        if not present and "default" in arg_spec:
            value = arg_spec.get("default")
        else:
            value = raw_args.get(arg_name)
        if arg_type == "bool":
            if present or "default" in arg_spec:
                normalized_args[arg_name] = _normalize_bool(value, field_name=f"project_run.args.{arg_name}")
        elif arg_type == "string_list":
            items = _normalize_string_list(value, field_name=f"project_run.args.{arg_name}")
            if items or present or "default" in arg_spec:
                normalized_args[arg_name] = items
        elif arg_type == "int":
            if present or "default" in arg_spec:
                if isinstance(value, bool):
                    raise ValueError(f"project_run.args.{arg_name} must be an integer")
                try:
                    normalized_args[arg_name] = int(str(value).strip())
                except ValueError as exc:
                    raise ValueError(f"project_run.args.{arg_name} must be an integer") from exc
        elif arg_type == "float":
            if present or "default" in arg_spec:
                if isinstance(value, bool):
                    raise ValueError(f"project_run.args.{arg_name} must be a float")
                try:
                    normalized_args[arg_name] = float(str(value).strip())
                except ValueError as exc:
                    raise ValueError(f"project_run.args.{arg_name} must be a float") from exc
        else:
            text = str(value or "").strip()
            if text:
                if arg_name == "runs_root":
                    text = str(
                        normalize_project_runs_root(
                            repo_root=root,
                            project_root=project_root,
                            project_id=project_id,
                            raw=text,
                        )
                    )
                normalized_args[arg_name] = text
        if bool(arg_spec.get("required")) and arg_name not in normalized_args:
            raise ValueError(f"project_run.args.{arg_name} is required")
    unknown = sorted(str(key).strip() for key in raw_args.keys() if str(key).strip() and key not in allowed_args)
    if unknown:
        raise ValueError(f"unsupported project_run args for {project_id}/{runner_id}: {', '.join(unknown)}")
    return {
        "project_id": project_id,
        "runner_id": runner_id,
        "args": normalized_args,
        "allow_manifest_compatibility": allow_manifest_compatibility,
        "launch_resolution_source": str(resolved.get("launch_resolution_source") or ""),
        "contract_hash": str(resolved.get("contract_hash") or ""),
        "contract_projection": dict(resolved.get("contract_projection") or {}),
        "scheduler_policy": dict(resolved.get("scheduler_policy") or {}),
    }


def build_project_run_command(project_run: Dict[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    normalized = normalize_project_run(project_run, repo_root=repo_root)
    resolved = resolve_project_runner(
        str(normalized.get("project_id") or ""),
        str(normalized.get("runner_id") or ""),
        repo_root=repo_root,
        allow_manifest_compatibility=bool(normalized.get("allow_manifest_compatibility")),
    )
    root = _repo_root(repo_root)
    cwd_rel = str(resolved.get("cwd") or "").strip()
    cwd = (root / cwd_rel).resolve() if cwd_rel else root
    if not cwd.exists() or not cwd.is_dir():
        raise FileNotFoundError(f"project runner cwd not found: {cwd_rel or cwd}")
    args_contract = resolved.get("args") if isinstance(resolved.get("args"), dict) else {}
    command_prefix = [str(item).strip() for item in list(resolved.get("command_prefix") or []) if str(item).strip()]
    base_command = [str(item).strip() for item in list(resolved.get("base_command") or []) if str(item).strip()]
    if base_command:
        cli: List[str] = list(base_command)
    else:
        cli = list(command_prefix) if command_prefix else [sys.executable]
        cli.append(str(resolved["entrypoint_path"]))
    normalized_args = normalized.get("args") if isinstance(normalized.get("args"), dict) else {}
    for arg_name, arg_spec in args_contract.items():
        if not isinstance(arg_spec, dict):
            continue
        arg_type = str(arg_spec.get("type") or "string").strip().lower() or "string"
        if arg_name not in normalized_args:
            continue
        flag = f"--{arg_name}"
        value = normalized_args.get(arg_name)
        if arg_type == "bool":
            if bool(value):
                cli.append(flag)
        elif arg_type == "string_list":
            for item in list(value or []):
                text = str(item or "").strip()
                if text:
                    cli.extend([flag, text])
        else:
            text = str(value or "").strip()
            if text:
                cli.extend([flag, text])
    return {
        "project_run": normalized,
        "command": cli,
        "cwd": str(cwd),
        "entrypoint": str(resolved.get("entrypoint") or ""),
        "command_prefix": command_prefix,
        "manifest_path": str(resolved.get("manifest_path") or ""),
        "launch_definition_path": str(resolved.get("launch_definition_path") or ""),
        "launch_resolution_source": str(resolved.get("launch_resolution_source") or ""),
        "contract_hash": str(resolved.get("contract_hash") or ""),
        "contract_projection": dict(resolved.get("contract_projection") or {}),
        "configuration_roots": dict(resolved.get("configuration_roots") or {}),
        "description": str(resolved.get("description") or ""),
    }
