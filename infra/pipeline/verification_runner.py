from __future__ import annotations

import re
import shutil
import os
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from .command_policy import CommandSpec, canonical_command_string, executable_exists
from .core import PipelineError, run_subprocess, save_text_atomic
from .workspace import WorkspaceInfo, create_isolated_workspace


def prepare_verification_workspace(
    *,
    repo_root: str,
    workspace_root: str,
    source_workspace_path: str,
    changed_files: Sequence[str],
    job_id: str,
    run_id: str,
    stage_key: str,
) -> WorkspaceInfo:
    workspace = create_isolated_workspace(
        repo_root=repo_root,
        workspace_root=workspace_root,
        job_id=job_id,
        run_id=run_id,
        stage_key=stage_key,
    )
    source_root = Path(source_workspace_path).resolve()
    target_root = Path(workspace.workspace_path).resolve()
    for rel_path in list(changed_files or []):
        normalized = str(rel_path or "").strip().replace("\\", "/").strip("/")
        if not normalized:
            continue
        src = source_root / normalized
        dst = target_root / normalized
        if src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if src.is_dir():
                if dst.exists() and dst.is_file():
                    dst.unlink()
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                shutil.copy2(src, dst)
        elif dst.exists():
            if dst.is_dir():
                shutil.rmtree(dst)
            else:
                dst.unlink()
    return workspace


def _render_check_log(*, spec: CommandSpec, result: Mapping[str, Any]) -> str:
    stdout = str(result.get("stdout") or "")
    stderr = str(result.get("stderr") or "")
    return "\n".join(
        [
            f"name: {spec.name}",
            f"command: {canonical_command_string(spec.command)}",
            f"cwd: {result.get('cwd')}",
            f"returncode: {result.get('returncode')}",
            f"timed_out: {result.get('timed_out')}",
            f"duration_ms: {result.get('duration_ms')}",
            "",
            "[stdout]",
            stdout,
            "",
            "[stderr]",
            stderr,
            "",
        ]
    )


def _verification_env(*, workspace_path: str) -> Dict[str, str]:
    env: Dict[str, str] = {
        "PATH": str(os.environ.get("PATH") or ""),
        "PYTHONIOENCODING": "utf-8",
        "LANG": str(os.environ.get("LANG") or "C.UTF-8"),
        "LC_ALL": str(os.environ.get("LC_ALL") or os.environ.get("LANG") or "C.UTF-8"),
    }
    home_value = str(os.environ.get("HOME") or "").strip()
    if home_value:
        env["HOME"] = home_value
    for key in ["TMPDIR", "TEMP", "TMP", "SYSTEMROOT"]:
        value = str(os.environ.get(key) or "").strip()
        if value:
            env[key] = value
    python_path_parts = [str(workspace_path)]
    inherited_pythonpath = str(os.environ.get("PYTHONPATH") or "").strip()
    if inherited_pythonpath:
        python_path_parts.append(inherited_pythonpath)
    env["PYTHONPATH"] = os.pathsep.join(python_path_parts)
    return env


def execute_verification_check(
    *,
    spec: CommandSpec,
    workspace_path: str,
    log_path: str,
) -> Dict[str, Any]:
    executable = str(spec.command[0] or "")
    if not executable_exists(executable):
        message = f"required executable not found: {Path(executable).name or executable}"
        save_text_atomic(log_path, message + "\n")
        return {
            "name": spec.name,
            "command": list(spec.command),
            "exit_code": None,
            "duration_ms": 0,
            "status": "failed" if spec.required else "skipped",
            "log_path": str(Path(log_path).resolve()),
            "failure_reason": "missing_executable",
        }
    try:
        result = run_subprocess(
            list(spec.command),
            cwd=workspace_path,
            env=_verification_env(workspace_path=workspace_path),
            timeout_seconds=int(spec.timeout_seconds),
        )
        save_text_atomic(log_path, _render_check_log(spec=spec, result=result.to_dict()))
        return {
            "name": spec.name,
            "command": list(spec.command),
            "exit_code": int(result.returncode),
            "duration_ms": int(result.duration_ms),
            "status": "passed" if int(result.returncode) == 0 else "failed",
            "log_path": str(Path(log_path).resolve()),
            "failure_reason": None if int(result.returncode) == 0 else "nonzero_exit",
        }
    except PipelineError as exc:
        details = dict(exc.details or {})
        result_payload = details if isinstance(details.get("args"), list) else {
            "cwd": workspace_path,
            "returncode": exc.exit_code,
            "stdout": str(details.get("stdout") or ""),
            "stderr": str(details.get("stderr") or exc.message),
            "timed_out": exc.code == "timeout",
            "duration_ms": int(details.get("duration_ms") or 0),
        }
        save_text_atomic(log_path, _render_check_log(spec=spec, result=result_payload))
        return {
            "name": spec.name,
            "command": list(spec.command),
            "exit_code": int(exc.exit_code) if exc.exit_code else None,
            "duration_ms": int(result_payload.get("duration_ms") or 0),
            "status": "failed" if spec.required else "skipped",
            "log_path": str(Path(log_path).resolve()),
            "failure_reason": exc.code,
        }


def summarize_check_statuses(checks: Sequence[Mapping[str, Any]]) -> Dict[str, int]:
    passed = failed = skipped = 0
    for check in list(checks or []):
        status = str(check.get("status") or "").strip().lower()
        if status == "passed":
            passed += 1
        elif status == "failed":
            failed += 1
        else:
            skipped += 1
    return {"passed": passed, "failed": failed, "skipped": skipped}


PYTEST_FAILURE_RE = re.compile(r"(?m)^(?:FAILED|ERROR)\s+([^\s]+::[^\s]+)")


def extract_failing_test_identifiers(log_text: str) -> List[str]:
    seen = set()
    out: List[str] = []
    for match in PYTEST_FAILURE_RE.finditer(str(log_text or "")):
        test_id = str(match.group(1) or "").strip()
        if not test_id or test_id in seen:
            continue
        out.append(test_id)
        seen.add(test_id)
    return out
