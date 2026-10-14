from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .command_policy import CommandSpec, allowed_executable_basenames, build_command_spec, validate_static_only_command
from .core import PipelineError


PYTHON_SUFFIXES = {".py", ".pyi"}
DEFAULT_BLOCKED_APPLY_STATUSES = ["conflict", "rejected", "skipped"]


@dataclasses.dataclass(frozen=True)
class VerificationProfile:
    required: bool
    checks: List[CommandSpec]
    profile_source: str
    blocked_apply_statuses: List[str]
    require_static_pass: bool = True
    missing_profile_reason_code: Optional[str] = None


def _mapping(value: Any) -> Dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _list_of_mappings(value: Any) -> List[Dict[str, Any]]:
    return [dict(item) for item in list(value or []) if isinstance(item, Mapping)]


def _strings(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    seen = set()
    for value in list(values or []):
        txt = str(value or "").strip()
        if not txt or txt in seen:
            continue
        out.append(txt)
        seen.add(txt)
    return out


def _timeout(config: Mapping[str, Any], *, stage_timeout_key: str) -> int:
    timeouts = _mapping(config.get("timeouts"))
    stage_timeout = int(timeouts.get(stage_timeout_key) or 0)
    if stage_timeout > 0:
        return stage_timeout
    default_timeout = int(timeouts.get("subprocess_default_seconds") or 0)
    if default_timeout <= 0:
        return 300
    return default_timeout


def _stage_cfg(config: Mapping[str, Any], stage_key: str) -> Dict[str, Any]:
    stages = _mapping(config.get("stages"))
    return _mapping(stages.get(stage_key))


def _python_paths(paths: Sequence[str]) -> List[str]:
    return [path for path in _strings(paths) if Path(path).suffix.lower() in PYTHON_SUFFIXES]


def _expand_check_entry(
    *,
    entry: Mapping[str, Any],
    config: Mapping[str, Any],
    default_timeout: int,
    default_required: bool,
    source: str,
    allowed_executables: Sequence[str],
    apply_changed_files: Sequence[str],
    allowed_edit_files: Sequence[str],
    stage_kind: str,
) -> Optional[CommandSpec]:
    check_type = str(entry.get("type") or "command").strip().lower()
    name = str(entry.get("name") or check_type or "verification_check")
    required = bool(entry.get("required", default_required))
    timeout_seconds = int(entry.get("timeout_seconds") or default_timeout)
    if check_type in {"python_compile_scope_files", "python_compile_changed_files"}:
        candidates = _python_paths(apply_changed_files) or _python_paths(allowed_edit_files)
        if not candidates:
            return None
        python_path = str(_mapping(config.get("tools")).get("python_path") or "python3")
        return build_command_spec(
            name=name,
            command=[python_path, "-m", "py_compile", *candidates],
            timeout_seconds=timeout_seconds,
            required=required,
            source=source,
            allowed_executables=allowed_executables,
        )
    if check_type == "command":
        spec = build_command_spec(
            name=name,
            command=entry.get("command"),
            timeout_seconds=timeout_seconds,
            required=required,
            source=source,
            allowed_executables=allowed_executables,
        )
        if stage_kind == "static":
            validate_static_only_command(spec.command)
        return spec
    raise PipelineError(
        "unsupported_verification_check_type",
        "verification profile contains an unsupported check type",
        details={"type": check_type, "name": name},
        exit_code=3,
    )


def build_stage11_contract_section(*, config: Mapping[str, Any], allowed_edit_files: Sequence[str]) -> Dict[str, Any]:
    stage_cfg = _stage_cfg(config, "script_11_verify_static")
    checks: List[Dict[str, Any]] = []
    if bool(stage_cfg.get("enable_default_python_compile", True)) and _python_paths(allowed_edit_files):
        checks.append(
            {
                "name": "python_compile_scope_files",
                "type": "python_compile_scope_files",
                "required": True,
                "timeout_seconds": _timeout(config, stage_timeout_key="static_verification_seconds"),
            }
        )
    checks.extend(_list_of_mappings(stage_cfg.get("checks")))
    required = bool(stage_cfg.get("required", False) or checks)
    return {
        "required": required,
        "blocked_apply_statuses": list(DEFAULT_BLOCKED_APPLY_STATUSES),
        "checks": checks,
    }


def build_stage12_contract_section(*, config: Mapping[str, Any], required_tests: Sequence[str]) -> Dict[str, Any]:
    stage_cfg = _stage_cfg(config, "script_12_verify_runtime")
    checks: List[Dict[str, Any]] = []
    checks.extend(_list_of_mappings(stage_cfg.get("checks")))
    timeout_seconds = _timeout(config, stage_timeout_key="runtime_verification_seconds")
    for idx, command in enumerate(_strings(required_tests), start=1):
        checks.append(
            {
                "name": f"required_test_{idx}",
                "type": "command",
                "command": command,
                "required": True,
                "timeout_seconds": timeout_seconds,
            }
        )
    required = bool(stage_cfg.get("required", False) or checks)
    return {
        "required": required,
        "require_static_pass": bool(stage_cfg.get("require_static_pass", True)),
        "blocked_apply_statuses": list(DEFAULT_BLOCKED_APPLY_STATUSES),
        "checks": checks,
    }


def build_stage13_contract_section(
    *,
    static_contract: Mapping[str, Any],
    runtime_contract: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "require_static_success": bool(static_contract.get("required", False)),
        "require_runtime_success": bool(runtime_contract.get("required", False)),
        "fail_closed_on_missing_evidence": True,
        "enforce_required_files_touched": True,
        "enforce_scope": True,
    }


def build_static_profile(
    *,
    config: Mapping[str, Any],
    execution_contract: Mapping[str, Any],
    apply_result: Mapping[str, Any],
) -> VerificationProfile:
    stage_cfg = _mapping(config.get("_stage"))
    contract = _mapping(execution_contract.get("stage11_static_verification"))
    apply_changed_files = _strings(apply_result.get("changed_files") or [])
    allowed_edit_files = _strings(execution_contract.get("allowed_edit_files") or [])
    source = "execution_contract" if contract else ("stage_config" if stage_cfg else "fallback")
    timeout_seconds = int(contract.get("timeout_seconds") or stage_cfg.get("timeout_seconds") or _timeout(config, stage_timeout_key="static_verification_seconds"))
    blocked_apply_statuses = _strings(contract.get("blocked_apply_statuses") or stage_cfg.get("blocked_apply_statuses") or DEFAULT_BLOCKED_APPLY_STATUSES)
    entries = _list_of_mappings(contract.get("checks"))
    if not entries:
        entries = _list_of_mappings(stage_cfg.get("checks"))
    required: Optional[bool] = contract.get("required") if "required" in contract else None
    if required is None and "required" in stage_cfg:
        required = bool(stage_cfg.get("required"))
    if not entries and bool(stage_cfg.get("enable_default_python_compile", True)) and (_python_paths(apply_changed_files) or _python_paths(allowed_edit_files)):
        entries = [{
            "name": "python_compile_scope_files",
            "type": "python_compile_scope_files",
            "required": True,
            "timeout_seconds": timeout_seconds,
        }]
        if source == "fallback":
            source = "built_in_python_compile"
    if required is None:
        required = bool(entries)
    allowlist = sorted(allowed_executable_basenames(config, extra=stage_cfg.get("allow_executables") if isinstance(stage_cfg.get("allow_executables"), list) else None))
    checks: List[CommandSpec] = []
    for entry in entries:
        spec = _expand_check_entry(
            entry=entry,
            config=config,
            default_timeout=timeout_seconds,
            default_required=bool(required),
            source=source,
            allowed_executables=allowlist,
            apply_changed_files=apply_changed_files,
            allowed_edit_files=allowed_edit_files,
            stage_kind="static",
        )
        if spec is not None:
            checks.append(spec)
    missing_reason = None
    if bool(required) and not checks:
        missing_reason = "missing_static_profile"
    return VerificationProfile(
        required=bool(required),
        checks=checks,
        profile_source=source,
        blocked_apply_statuses=blocked_apply_statuses or list(DEFAULT_BLOCKED_APPLY_STATUSES),
        require_static_pass=True,
        missing_profile_reason_code=missing_reason,
    )


def build_runtime_profile(
    *,
    config: Mapping[str, Any],
    execution_contract: Mapping[str, Any],
    apply_result: Mapping[str, Any],
) -> VerificationProfile:
    stage_cfg = _mapping(config.get("_stage"))
    contract = _mapping(execution_contract.get("stage12_runtime_verification"))
    apply_changed_files = _strings(apply_result.get("changed_files") or [])
    allowed_edit_files = _strings(execution_contract.get("allowed_edit_files") or [])
    source = "execution_contract" if contract else ("stage_config" if stage_cfg else "fallback")
    timeout_seconds = int(contract.get("timeout_seconds") or stage_cfg.get("timeout_seconds") or _timeout(config, stage_timeout_key="runtime_verification_seconds"))
    blocked_apply_statuses = _strings(contract.get("blocked_apply_statuses") or stage_cfg.get("blocked_apply_statuses") or DEFAULT_BLOCKED_APPLY_STATUSES)
    entries = _list_of_mappings(contract.get("checks"))
    if not entries:
        entries = _list_of_mappings(stage_cfg.get("checks"))
    required_tests = _strings(_mapping(execution_contract.get("constraints")).get("required_tests") or [])
    if not contract and not entries and required_tests:
        entries = [
            {
                "name": f"required_test_{idx}",
                "type": "command",
                "command": command,
                "required": True,
                "timeout_seconds": timeout_seconds,
            }
            for idx, command in enumerate(required_tests, start=1)
        ]
        if source == "fallback":
            source = "required_tests_fallback"
    required: Optional[bool] = contract.get("required") if "required" in contract else None
    if required is None and "required" in stage_cfg:
        required = bool(stage_cfg.get("required"))
    if required is None:
        required = bool(entries or required_tests)
    require_static_pass = bool(contract.get("require_static_pass", stage_cfg.get("require_static_pass", True)))
    allowlist = sorted(allowed_executable_basenames(config, extra=stage_cfg.get("allow_executables") if isinstance(stage_cfg.get("allow_executables"), list) else None))
    checks: List[CommandSpec] = []
    for entry in entries:
        spec = _expand_check_entry(
            entry=entry,
            config=config,
            default_timeout=timeout_seconds,
            default_required=bool(required),
            source=source,
            allowed_executables=allowlist,
            apply_changed_files=apply_changed_files,
            allowed_edit_files=allowed_edit_files,
            stage_kind="runtime",
        )
        if spec is not None:
            checks.append(spec)
    missing_reason = None
    if bool(required) and not checks:
        missing_reason = "missing_runtime_profile"
    return VerificationProfile(
        required=bool(required),
        checks=checks,
        profile_source=source,
        blocked_apply_statuses=blocked_apply_statuses or list(DEFAULT_BLOCKED_APPLY_STATUSES),
        require_static_pass=require_static_pass,
        missing_profile_reason_code=missing_reason,
    )


def static_verification_required(execution_contract: Mapping[str, Any]) -> bool:
    section = _mapping(execution_contract.get("stage11_static_verification"))
    if "required" in section:
        return bool(section.get("required"))
    return bool(section.get("checks"))


def runtime_verification_required(execution_contract: Mapping[str, Any]) -> bool:
    section = _mapping(execution_contract.get("stage12_runtime_verification"))
    if "required" in section:
        return bool(section.get("required"))
    if section.get("checks"):
        return True
    return bool(_mapping(execution_contract.get("constraints")).get("required_tests"))


def apply_status_blocks_verification(*, apply_status: str, profile: VerificationProfile) -> bool:
    status = str(apply_status or "").strip().lower()
    blocked = {value.strip().lower() for value in list(profile.blocked_apply_statuses or []) if str(value or "").strip()}
    return status != "applied_cleanly" or status in blocked
