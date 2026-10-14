"""Typed request/result helpers for host verification jobs."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Dict

from infra.host import verification_actions as _verification_actions


SCHEMA_REQUEST = "eiel_verification_request_v1"
SCHEMA_RESULT = "eiel_verification_result_v1"
FAILURE_KINDS = {
    "test_failed",
    "environment_missing",
    "environment_blocked",
    "invalid_request",
    "timeout",
    "runner_internal_error",
}
_ACTION_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")


def utc_now_iso() -> str:
    """Return second-resolution UTC timestamp."""
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def utc_compact() -> str:
    """Return compact UTC timestamp suitable for ids."""
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def repo_root() -> Path:
    """Resolve repository root from this module location."""
    return Path(__file__).resolve().parents[2]


def verification_jobs_root(*, repo: Path | None = None) -> Path:
    """Return durable verification-jobs root."""
    base = Path(repo or repo_root()).resolve()
    root = (base / "runtime" / "state" / "webui" / "verification_jobs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_name(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text or "").strip()).strip("-") or "x"


def request_digest(*, action_id: str, params: Dict[str, Any]) -> str:
    """Return stable digest for request identity."""
    payload = {"action_id": str(action_id or "").strip(), "params": dict(params or {})}
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def request_id(*, workflow_id: str, action_id: str) -> str:
    """Build a readable verification request id."""
    return f"vrf_{utc_compact().lower()}_{_safe_name(workflow_id)}_{_safe_name(action_id)}"


def ensure_job_dir(request_id_value: str, *, repo: Path | None = None) -> Path:
    """Create the job directory for a request id."""
    job_dir = (verification_jobs_root(repo=repo) / _safe_name(request_id_value)).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def normalize_request(obj: Dict[str, Any], *, repo: Path | None = None) -> Dict[str, Any]:
    """Validate and normalize a verification request payload."""
    if not isinstance(obj, dict):
        raise ValueError("request must be an object")
    action = obj.get("action") if isinstance(obj.get("action"), dict) else {}
    action_id = str(action.get("action_id") or "").strip()
    if not _ACTION_ID_RE.match(action_id):
        raise ValueError("invalid action_id")
    params = action.get("params") if isinstance(action.get("params"), dict) else {}
    request_id_value = str(obj.get("request_id") or "").strip() or request_id(
        workflow_id=str(obj.get("workflow_id") or "wf"),
        action_id=action_id,
    )
    job_dir = ensure_job_dir(request_id_value, repo=repo)
    binding = obj.get("binding") if isinstance(obj.get("binding"), dict) else {}
    policy = obj.get("policy") if isinstance(obj.get("policy"), dict) else {}
    artifacts = obj.get("artifacts") if isinstance(obj.get("artifacts"), dict) else {}
    timeout_raw = policy.get("timeout_s")
    if timeout_raw in (None, ""):
        try:
            timeout_s = int(_verification_actions.get_action_spec(action_id).timeout_s_default)
        except Exception:
            timeout_s = 900
    else:
        timeout_s = int(timeout_raw)
    timeout_s = max(1, min(3600, int(timeout_s)))
    stall_timeout_raw = policy.get("stall_timeout_s")
    stall_timeout_s = None
    if stall_timeout_raw not in (None, ""):
        stall_timeout_s = max(1, min(3600, int(stall_timeout_raw)))
        stall_timeout_s = min(timeout_s, int(stall_timeout_s))
    # Validate action parameters up front so malformed requests fail as
    # invalid_request during normalization instead of much later inside the
    # subprocess runner.
    _verification_actions.get_action_spec(action_id).build_argv(dict(params))
    return {
        "schema_version": SCHEMA_REQUEST,
        "request_id": request_id_value,
        "workflow_id": str(obj.get("workflow_id") or "").strip(),
        "turn": str(obj.get("turn") or "").strip(),
        "requested_by_role": str(obj.get("requested_by_role") or "").strip() or "assurer",
        "binding": {"type": str(binding.get("type") or "").strip(), "id": str(binding.get("id") or "").strip()},
        "action": {"action_id": action_id, "params": dict(params)},
        "artifacts": {
            "root_dir": str(job_dir),
            "capture_stdout": bool(artifacts.get("capture_stdout", True)),
            "capture_stderr": bool(artifacts.get("capture_stderr", True)),
            "artifact_globs": [str(x) for x in (artifacts.get("artifact_globs") or []) if str(x or "").strip()],
        },
        "policy": {
            "timeout_s": int(timeout_s),
            "stall_timeout_s": int(stall_timeout_s) if stall_timeout_s is not None else None,
            "allow_network": bool(policy.get("allow_network", False)),
            "require_clean_exit": bool(policy.get("require_clean_exit", True)),
        },
        "reason": str(obj.get("reason") or "").strip(),
        "request_digest": request_digest(action_id=action_id, params=dict(params)),
        "created_utc": str(obj.get("created_utc") or "").strip() or utc_now_iso(),
    }


def build_result(
    *,
    request: Dict[str, Any],
    ok: bool,
    status: str,
    exit_code: int,
    started_utc: str,
    finished_utc: str,
    duration_ms: int,
    cwd: str,
    argv: list[str],
    evidence: Dict[str, Any],
    failure_kind: str,
    runner_version: str = "v1",
) -> Dict[str, Any]:
    """Build a normalized verification result payload."""
    fk = str(failure_kind or "").strip() or ("test_failed" if not ok else "")
    if fk and fk not in FAILURE_KINDS:
        raise ValueError(f"invalid failure_kind: {fk}")
    artifacts = request.get("artifacts") if isinstance(request.get("artifacts"), dict) else {}
    binding = request.get("binding") if isinstance(request.get("binding"), dict) else {}
    artifact_lineage = {
        "schema_version": "eiel_verification_result_artifact_lineage_v1",
        "job_dir": str(artifacts.get("root_dir") or "").strip(),
        "request_path": str((Path(str(artifacts.get("root_dir") or "")) / "request.json").resolve()) if str(artifacts.get("root_dir") or "").strip() else "",
        "status_path": str((Path(str(artifacts.get("root_dir") or "")) / "status.json").resolve()) if str(artifacts.get("root_dir") or "").strip() else "",
        "result_path": str((Path(str(artifacts.get("root_dir") or "")) / "result.json").resolve()) if str(artifacts.get("root_dir") or "").strip() else "",
        "stdout_path": str((evidence or {}).get("stdout_path") or "").strip(),
        "stderr_path": str((evidence or {}).get("stderr_path") or "").strip(),
        "artifact_paths": [str(x) for x in list((evidence or {}).get("artifact_paths") or []) if str(x or "").strip()],
        "request_digest": str(request.get("request_digest") or "").strip(),
    }
    return {
        "schema_version": SCHEMA_RESULT,
        "request_id": str(request.get("request_id") or "").strip(),
        "request_digest": str(request.get("request_digest") or "").strip(),
        "workflow_id": str(request.get("workflow_id") or "").strip(),
        "binding": {"type": str(binding.get("type") or "").strip(), "id": str(binding.get("id") or "").strip()},
        "ok": bool(ok),
        "status": str(status or "").strip() or ("passed" if ok else "failed"),
        "action_id": str(((request.get("action") or {}) if isinstance(request.get("action"), dict) else {}).get("action_id") or "").strip(),
        "started_utc": str(started_utc or "").strip(),
        "finished_utc": str(finished_utc or "").strip(),
        "duration_ms": int(max(0, int(duration_ms))),
        "exit_code": int(exit_code),
        "cwd": str(cwd or "").strip(),
        "argv": [str(x) for x in argv],
        "evidence": dict(evidence or {}),
        "artifact_lineage": artifact_lineage,
        "failure_kind": fk,
        "runner": {"host": "local", "runner_version": str(runner_version or "v1")},
    }


def write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    """Write JSON atomically with fsync."""
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)
