"""infra.webui.server_agents

Agent Control Plane MVP-0 backend helpers.

This module provides filesystem-backed session/inbox/handoff handling for
Builder/Assurer/Generalist agent panels.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

from infra.root_tools import load_tool_module as _load_root_tool_module
from infra.host import verification_actions as _verification_actions
from infra.host import verification_contract as _verification_contract
_runtime_policy = _load_root_tool_module("runtime_policy", "tools/runtime_policy.py")
_fsio = _load_root_tool_module("shared_fsio", "tools/shared/fsio.py")
from infra.webui import server_lab_assistant_bootstrap as _lab_bootstrap
from infra.webui import server_agents_parsing as _agents_parsing
from infra.webui import server_context_rich_reply as _context_rich_reply
from infra.webui import server_codex_programmatic_surface as _codex_surface
from infra.webui import server_lab_assistant_intake as _lab_intake
from infra.webui import server_lab_assistant_turns as _lab_turns
from infra.webui import server_lab_assistant_task_register as _lab_task_register
from infra.webui.gpt_client import create_response, extract_output_text, stream_response_events
from infra.webui.ledger.ledger_manager import ChangeLedger
from infra.webui.server_codex_runtime import CodexRuntimeError, CodexRuntimeManager, codex_turn_command_from_env


ROLE_SET = {"builder", "assurer", "generalist"}
HOST_VERIFICATION_MARKER = "HOST_VERIFICATION_REQUEST_JSON:"
CHAT_HEADER_TITLE_MARKER = "CHAT_HEADER_TITLE:"
TASK_ID_RE = re.compile(r"^[a-z0-9]{3}$")
HANDOFF_READY_VALUES = {"READY", "BLOCKED"}
REPO_CONTEXT_MAX_FILES = 4
REPO_CONTEXT_MAX_CHARS = 12000
CODEX_TIMEOUT_DEFAULT_S = 7000
# Gemini prompt transport now uses stdin, so argv-size limits are no longer a
# runtime concern for normal scheduler turns.
CLI_ARG_PROMPT_MAX_BYTES = 180000
ACTIVE_ROLE_PROMPT_FILES = {
    "builder": "role__builder_mobile.md",
    "assurer": "role__verifier_mobile.md",
    "generalist": "role__generalist_mobile.md",
}
WORKFLOW_ROLE_PROMPT_FILES = {
    "builder": "role__builder_mobile.md",
    "assurer": "role__verifier_mobile.md",
    "generalist": "role__generalist_mobile.md",
}
PROMPT_FILE_REDIRECTS = {
    "role__builder.md": "role__builder_mobile.md",
    "role__assurer.md": "role__verifier_mobile.md",
    "role__generalist.md": "role__generalist_mobile.md",
}
_PROMPT_INCLUDE_RE = re.compile(r"^\s*\{\{include:([A-Za-z0-9._/\-]+)\}\}\s*$")
MOBILE_BUBBLE_FILE_MAP = {
    ("task_id", "mpl", "generalist"): "plane.md",
    ("task_id", "mbl", "generalist"): "generalist_a.md",
    ("task_id", "mb2", "generalist"): "generalist_a2.md",
    ("task_id", "mb3", "generalist"): "generalist_a3.md",
    ("task_id", "mor", "generalist"): "orchestrator.md",
    ("task_id", "mbu", "builder"): "builder.md",
    ("task_id", "mvr", "assurer"): "verifier.md",
}
_MOBILE_TASK_BINDING_IDS = {str(binding_id).strip().lower() for binding_type, binding_id, _role in MOBILE_BUBBLE_FILE_MAP if binding_type == "task_id"}


_CODEX_MANAGER_GUARD = threading.Lock()
_CODEX_MANAGER: Optional[CodexRuntimeManager] = None
_HOST_VERIFICATION_DISPATCH_GUARD = threading.Lock()
_HOST_VERIFICATION_DISPATCH_MODE = "inline"
_HOST_VERIFICATION_WORK_QUEUE: Optional["queue.Queue[tuple[Optional[Dict[str, Any]], Optional[queue.Queue[Dict[str, Any]]]]]"] = None
_HOST_VERIFICATION_WORKER: Optional[threading.Thread] = None
_HOST_VERIFICATION_WORKER_STOP: Optional[threading.Event] = None
_HOST_VERIFICATION_REPLY_GUARD = threading.Lock()
_HOST_VERIFICATION_PENDING_REPLY_QUEUES: Dict[str, "queue.Queue[Dict[str, Any]]"] = {}
_HOST_VERIFICATION_TERMINAL_STATES = {"finished", "failed", "timed_out"}
_HOST_VERIFICATION_IDLE_TRACE_INTERVAL_S = 10.0
_HOST_VERIFICATION_LAST_IDLE_TRACE_AT = 0.0
_HOST_VERIFICATION_DEFAULT_TIMEOUT_S = 3600


class PromptConfigError(RuntimeError):
    """Raised when prompt files are missing or invalid."""


def _combined_prompt_text(*, instructions: str, model_input: str) -> str:
    return (
        "System instructions:\n"
        f"{str(instructions or '').strip()}\n\n"
        "User task:\n"
        f"{str(model_input or '').strip()}\n"
    ).strip()


def _combined_prompt_bytes(*, instructions: str, model_input: str) -> int:
    return len(_combined_prompt_text(instructions=instructions, model_input=model_input).encode("utf-8"))


def _looks_like_quota_or_model_unavailable(text: str) -> bool:
    t = str(text or "").strip().lower()
    if not t:
        return False
    needles = [
        "insufficient_quota",
        "quota",
        "usage limit",
        "usage_limit",
        "credit",
        "billing",
        "rate limit",
        "429",
        "model_not_found",
        "model not found",
        "unavailable model",
        "unsupported model",
    ]
    return any(n in t for n in needles)


def _model_chain_from_options(options: Optional[Dict[str, Any]], *, default_model: str) -> List[str]:
    obj = options if isinstance(options, dict) else {}
    primary_model = str(obj.get("model") or default_model or "").strip()
    fallback_model = str(obj.get("fallback_model") or "").strip()
    raw_fallback_models = obj.get("fallback_models")
    models: List[str] = []
    if primary_model:
        models.append(primary_model)
    if isinstance(raw_fallback_models, list):
        for item in raw_fallback_models:
            txt = str(item or "").strip()
            if txt and txt not in models:
                models.append(txt)
    elif isinstance(raw_fallback_models, str):
        for part in str(raw_fallback_models).split(","):
            txt = str(part or "").strip()
            if txt and txt not in models:
                models.append(txt)
    if fallback_model and fallback_model not in models:
        models.append(fallback_model)
    if not models:
        models = [str(default_model or "").strip()]
    return models


def _capture_operator_intake(*, role: str, binding: Dict[str, Any], message: str, writer_prefix: str) -> Dict[str, Any]:
    reg = _lab_task_register.load_or_init_task_register(_repo_root())
    expected_generation = int(reg.get("write_generation") or 0)
    normalized_binding = _normalize_binding(binding)
    ledger_uid = str(
        normalized_binding.get("ledger_uid")
        or (normalized_binding.get("id") if normalized_binding.get("type") == "ledger_uid" else "")
    )
    task_id = str(
        normalized_binding.get("task_id")
        or (normalized_binding.get("id") if normalized_binding.get("type") == "task_id" else "")
    )
    return _lab_intake.persist_structured_request_intake(
        _repo_root(),
        expected_generation=expected_generation,
        writer=f"{writer_prefix}.{_lab_intake._clean_text(role)}",
        raw_request_text=message,
        task_id=task_id,
        ledger_uid=ledger_uid,
    )


def _capture_operator_intake_safe(*, role: str, binding: Dict[str, Any], message: str, writer_prefix: str) -> Dict[str, Any]:
    try:
        return _capture_operator_intake(
            role=role,
            binding=binding,
            message=message,
            writer_prefix=writer_prefix,
        )
    except Exception as exc:
        return {
            "ok": False,
            "persisted": False,
            "reason": "intake_capture_failed",
            "error": str(exc),
            "intake": {"raw_request_text": _lab_intake._clean_text(message)},
            "task_register": _lab_task_register.load_or_init_task_register(_repo_root()),
        }


def _attach_operator_intake(*, payload: Dict[str, Any], operator_intake: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(payload)
    out["operator_intake"] = dict(operator_intake or {})
    out["plane_routed"] = _operator_intake_plane_routed(operator_intake)
    if bool(operator_intake.get("ok")) and operator_intake.get("task_id"):
        out["task_id"] = str(operator_intake.get("task_id"))
    return out


def _operator_intake_plane_routed(operator_intake: Optional[Dict[str, Any]]) -> bool:
    intake = operator_intake if isinstance(operator_intake, dict) else {}
    if not bool(intake.get("ok")):
        return False
    source = str(intake.get("source") or "").strip().lower()
    intake_path = str(intake.get("intake_path") or "").strip().lower()
    return source == "operator_intake" and intake_path == "canonical"


def _plane_routed_meta(operator_intake: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    if not _operator_intake_plane_routed(operator_intake):
        return {}
    return {
        "plane_routed": True,
        "plane_route": "canonical_operator_intake",
        "plane_route_label": "Plane",
    }


def _repo_relpath(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(_repo_root()))
    except Exception:
        return str(path)


def _codex_surface_task_id(*, binding: Dict[str, Any], operator_intake: Dict[str, Any]) -> str:
    task_id = str(operator_intake.get("task_id") or "").strip()
    if task_id:
        return task_id
    hinted_task_id = str(binding.get("task_id") or "").strip()
    if hinted_task_id:
        return hinted_task_id
    if str(binding.get("type") or "").strip() == "task_id":
        bound_task_id = str(binding.get("id") or "").strip()
        if not bound_task_id:
            return ""
        snapshot = _lab_task_register.read_task_register_snapshot(_repo_root(), initialize_if_missing=True)
        active_tasks = list((snapshot.get("task_register") or {}).get("active_tasks") or [])
        existing = next(
            (row for row in active_tasks if str(row.get("task_id") or "").strip() == bound_task_id),
            None,
        )
        if existing:
            return bound_task_id
    return ""


def _codex_surface_binding_ref(*, binding: Dict[str, Any], role: str) -> str:
    return (
        f"{str(binding.get('type') or '').strip()}::"
        f"{str(binding.get('id') or '').strip()}::"
        f"{str(role or '').strip()}"
    )


def _launch_codex_surface_run(
    *,
    binding: Dict[str, Any],
    role: str,
    operator_intake: Dict[str, Any],
    turn_id: str,
    worker_id: str,
) -> Dict[str, Any]:
    task_id = _codex_surface_task_id(binding=binding, operator_intake=operator_intake)
    if not task_id:
        return {"ok": False, "launched": False, "task_id": "", "run_id": str(turn_id or "").strip()}
    snapshot = _lab_task_register.read_task_register_snapshot(_repo_root(), initialize_if_missing=True)
    _codex_surface.launch_codex_run(
        _repo_root(),
        expected_generation=int((snapshot.get("task_register") or {}).get("write_generation") or 0),
        writer="agents.send_message.codex.surface",
        task_id=task_id,
        run_id=str(turn_id or "").strip(),
        execution_ref=f"governance/agents/runtime/workers_state.json#{str(worker_id or '').strip()}",
        binding_ref=_codex_surface_binding_ref(binding=binding, role=role),
        job_uri="governance/agents/runtime/workers_state.json",
        trace_id=str(turn_id or "").strip(),
        reference_status="execution_ref_active",
        reference_status_reason="codex_send_message_launch",
    )
    return {"ok": True, "launched": True, "task_id": task_id, "run_id": str(turn_id or "").strip()}


def _write_codex_surface_checkpoint(
    *,
    task_id: str,
    run_id: str,
    ok: bool,
    terminal_state: str,
    summary_text: str,
    worker_id: str,
    role: str,
    binding: Dict[str, Any],
    session_file: Path,
) -> Dict[str, Any]:
    if not str(task_id or "").strip() or not str(run_id or "").strip():
        return {"ok": False, "written": False}
    snapshot = _lab_task_register.read_task_register_snapshot(_repo_root(), initialize_if_missing=True)
    summary = str(summary_text or "").strip()
    if len(summary) > 240:
        summary = summary[:237].rstrip() + "..."
    checkpoint_status = "recorded" if ok else "failed"
    result = _codex_surface.write_codex_run_evidence(
        _repo_root(),
        expected_generation=int((snapshot.get("task_register") or {}).get("write_generation") or 0),
        writer="agents.send_message.codex.surface",
        task_id=str(task_id or "").strip(),
        run_id=str(run_id or "").strip(),
        summary=summary or terminal_state or checkpoint_status,
        evidence_refs=[_repo_relpath(session_file)],
        payload={
            "terminal_state": str(terminal_state or "").strip(),
            "worker_id": str(worker_id or "").strip(),
            "role": str(role or "").strip(),
            "binding": {
                "type": str(binding.get("type") or "").strip(),
                "id": str(binding.get("id") or "").strip(),
            },
            "session_file": _repo_relpath(session_file),
        },
        checkpoint_status=checkpoint_status,
    )
    return {"ok": True, "written": True, "payload_ref": str(result.get("payload_ref") or "")}


def _agents_backend() -> str:
    return str(os.environ.get("EIEL_AGENTS_BACKEND", "llm") or "llm").strip().lower()


def _default_agent_tech() -> str:
    return "codex" if _agents_backend() == "codex" else "openai"


@contextmanager
def _forced_agents_backend(backend: str) -> Iterator[None]:
    requested = str(backend or "").strip().lower()
    if not requested:
        yield
        return
    had_prev = "EIEL_AGENTS_BACKEND" in os.environ
    prev = str(os.environ.get("EIEL_AGENTS_BACKEND") or "")
    os.environ["EIEL_AGENTS_BACKEND"] = requested
    try:
        yield
    finally:
        if had_prev:
            os.environ["EIEL_AGENTS_BACKEND"] = prev
        else:
            os.environ.pop("EIEL_AGENTS_BACKEND", None)


def _default_openai_chat_model() -> str:
    return _runtime_policy.get_runtime_policy_text(
        "mobile.chat_model",
        "gpt-5.4",
        env_names=("EIEL_MOBILE_CHAT_MODEL", "EIEL_MOBILE_MODEL"),
    )


def _infer_agent_tech_from_model(model: str) -> str:
    name = str(model or "").strip().lower()
    if not name:
        return ""
    if "codex" in name:
        return "codex"
    if name.startswith(("gpt-", "o1", "o3", "o4")):
        return "openai"
    if name.startswith("claude"):
        return "claude"
    if name.startswith("gemini"):
        return "gemini"
    if name.startswith("grok"):
        return "xai"
    if name.startswith("deepseek"):
        return "deepseek"
    if name.startswith(("qwen", "llama", "mistral", "phi", "gemma")):
        return "ollama"
    return ""


def _resolved_agent_tech_for_model(requested_agent_tech: str, model: str) -> str:
    requested = str(requested_agent_tech or "").strip().lower()
    inferred = _infer_agent_tech_from_model(model)
    if not inferred:
        return requested
    if not requested:
        return inferred
    if requested == inferred:
        return requested
    if requested in {"openai", "codex"} and inferred in {"openai", "codex"}:
        return requested
    return inferred


def _resolve_agent_tech(options: Optional[Dict[str, Any]] = None) -> str:
    requested = str((options or {}).get("agent_tech") or "").strip().lower()
    model = str((options or {}).get("model") or "").strip()
    if requested in {"codex", "openai", "claude", "gemini", "xai", "ollama", "deepseek"}:
        return requested
    inferred = _resolved_agent_tech_for_model("", model)
    if inferred:
        return inferred
    return _default_agent_tech()


def _claude_binary() -> str:
    return str(shutil.which("claude") or "").strip()


def _claude_cli_version_probe() -> Dict[str, Any]:
    binary = _claude_binary()
    if not binary:
        return {"ok": False, "binary": "", "version": "", "error": "missing_binary"}
    try:
        cp = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        version_txt = str((cp.stdout or cp.stderr or "")).strip().splitlines()
        version_line = version_txt[0].strip() if version_txt else ""
        return {
            "ok": bool(cp.returncode == 0 and version_line),
            "binary": binary,
            "version": version_line,
            "exit_code": int(cp.returncode),
            "error": "" if cp.returncode == 0 else (version_line or "version_probe_failed"),
        }
    except Exception as e:
        return {"ok": False, "binary": binary, "version": "", "error": str(e)}


def _gemini_binary() -> str:
    return str(shutil.which("gemini") or "").strip()


def _gemini_cli_version_probe() -> Dict[str, Any]:
    binary = _gemini_binary()
    if not binary:
        return {"ok": False, "binary": "", "version": "", "error": "missing_binary"}
    try:
        cp = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        version_txt = str((cp.stdout or cp.stderr or "")).strip().splitlines()
        version_line = version_txt[0].strip() if version_txt else ""
        return {
            "ok": bool(cp.returncode == 0 and version_line),
            "binary": binary,
            "version": version_line,
            "exit_code": int(cp.returncode),
            "error": "" if cp.returncode == 0 else (version_line or "version_probe_failed"),
        }
    except Exception as e:
        return {"ok": False, "binary": binary, "version": "", "error": str(e)}


def _run_claude_oneshot(
    *,
    model_input: str,
    instructions: str,
    model: str,
    reasoning_effort: str,
    timeout_s: int,
) -> Dict[str, Any]:
    binary = _claude_binary()
    if not binary:
        raise RuntimeError("claude CLI not found on PATH")
    prompt_text = _combined_prompt_text(instructions=instructions, model_input=model_input)
    prompt_bytes = len(prompt_text.encode("utf-8"))
    cmd: List[str] = [binary, "-p", "--permission-mode", "bypassPermissions"]
    if str(model or "").strip():
        cmd.extend(["--model", str(model).strip()])
    effort = str(reasoning_effort or "").strip().lower()
    if effort in {"low", "medium", "high"}:
        cmd.extend(["--effort", effort])
    cp = subprocess.run(
        cmd,
        input=prompt_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=max(30, int(timeout_s or CODEX_TIMEOUT_DEFAULT_S)),
    )
    out = str(cp.stdout or "").strip()
    err = str(cp.stderr or "").strip()
    ok = bool(cp.returncode == 0 and out)
    return {
        "ok": ok,
        "content": out if out else (err if err else ""),
        "error": "" if ok else (err or out or f"claude_exit_{int(cp.returncode)}"),
        "exit_code": int(cp.returncode),
        "selected_model": str(model or "").strip(),
        "binary": binary,
        "request_payload_bytes": int(prompt_bytes),
        "transport": "stdin",
    }


def _run_gemini_oneshot(
    *,
    model_input: str,
    instructions: str,
    model: str,
    timeout_s: int,
) -> Dict[str, Any]:
    binary = _gemini_binary()
    prompt_text = _combined_prompt_text(instructions=instructions, model_input=model_input)
    prompt_bytes = len(prompt_text.encode("utf-8"))
    if not binary:
        raise RuntimeError("gemini CLI not found on PATH")
    # Use stdin transport for Gemini to avoid argv-size limits with large prompts.
    cmd: List[str] = [binary, "--model", str(model or "").strip() or "gemini-2.5-pro"]
    cp = subprocess.run(
        cmd,
        input=prompt_text,
        capture_output=True,
        text=True,
        check=False,
        timeout=max(30, int(timeout_s or CODEX_TIMEOUT_DEFAULT_S)),
    )
    out = str(cp.stdout or "").strip()
    err = str(cp.stderr or "").strip()
    ok = bool(cp.returncode == 0 and out)
    return {
        "ok": ok,
        "content": out if out else (err if err else ""),
        "error": "" if ok else (err or out or f"gemini_exit_{int(cp.returncode)}"),
        "exit_code": int(cp.returncode),
        "selected_model": str(model or "").strip(),
        "binary": binary,
        "request_payload_bytes": int(prompt_bytes),
        "transport": "stdin",
    }


def _xai_runtime_probe() -> Dict[str, Any]:
    curl_bin = str(shutil.which("curl") or "").strip()
    key_present = bool(str(os.environ.get("XAI_API_KEY") or "").strip())
    return {
        "ok": bool(curl_bin and key_present),
        "curl_binary": curl_bin,
        "api_key_present": key_present,
        "error": "" if (curl_bin and key_present) else ("missing_api_key" if curl_bin else "missing_curl"),
    }


def _deepseek_runtime_probe() -> Dict[str, Any]:
    curl_bin = str(shutil.which("curl") or "").strip()
    key_present = bool(str(os.environ.get("DEEPSEEK_API_KEY") or "").strip())
    return {
        "ok": bool(curl_bin and key_present),
        "curl_binary": curl_bin,
        "api_key_present": key_present,
        "base_url": str(os.environ.get("EIEL_DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/"),
        "error": "" if (curl_bin and key_present) else ("missing_api_key" if curl_bin else "missing_curl"),
    }


def _run_xai_oneshot(
    *,
    model_input: str,
    instructions: str,
    model: str,
    timeout_s: int,
) -> Dict[str, Any]:
    curl_bin = str(shutil.which("curl") or "").strip()
    if not curl_bin:
        raise RuntimeError("curl not found on PATH for xai backend")
    api_key = str(os.environ.get("XAI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("XAI_API_KEY is required for xai backend")
    payload = {
        "messages": [
            {"role": "system", "content": str(instructions or "").strip()},
            {"role": "user", "content": str(model_input or "").strip()},
        ],
        "model": str(model or "").strip() or "grok-code-fast-1",
        "stream": False,
        "temperature": 0,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_bytes = len(payload_json.encode("utf-8"))
    payload_file = None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as pf:
        pf.write(payload_json)
        pf.flush()
        payload_file = pf.name
    cmd: List[str] = [
        curl_bin,
        "-sS",
        "https://api.x.ai/v1/chat/completions",
        "-H",
        "Content-Type: application/json",
        "-H",
        f"Authorization: Bearer {api_key}",
        "--data-binary",
        f"@{payload_file}",
    ]
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(30, int(timeout_s or CODEX_TIMEOUT_DEFAULT_S)),
        )
    finally:
        try:
            if payload_file:
                Path(payload_file).unlink(missing_ok=True)
        except Exception:
            pass
    raw_out = str(cp.stdout or "").strip()
    raw_err = str(cp.stderr or "").strip()
    body: Dict[str, Any] = {}
    try:
        body = json.loads(raw_out) if raw_out else {}
    except Exception:
        body = {}
    if cp.returncode != 0:
        return {
            "ok": False,
            "content": raw_err or raw_out or "",
            "error": raw_err or raw_out or f"xai_exit_{int(cp.returncode)}",
            "exit_code": int(cp.returncode),
            "selected_model": str(payload.get("model") or ""),
            "binary": curl_bin,
            "request_payload_bytes": int(payload_bytes),
            "transport": "temp_file_http_body",
        }
    message = ""
    if isinstance(body, dict):
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
            if isinstance(msg, dict):
                message = str(msg.get("content") or "").strip()
    if message:
        return {
            "ok": True,
            "content": message,
            "error": "",
            "exit_code": int(cp.returncode),
            "selected_model": str(body.get("model") or payload.get("model") or ""),
            "binary": curl_bin,
            "request_payload_bytes": int(payload_bytes),
            "transport": "temp_file_http_body",
        }
    err_code = str(body.get("code") or "").strip() if isinstance(body, dict) else ""
    err_txt = str(body.get("error") or "").strip() if isinstance(body, dict) else ""
    merged = (f"{err_code}: {err_txt}".strip(": ").strip()) or raw_out or raw_err or "xai_empty_response"
    return {
        "ok": False,
        "content": merged,
        "error": merged,
        "exit_code": int(cp.returncode),
        "selected_model": str(payload.get("model") or ""),
        "binary": curl_bin,
        "request_payload_bytes": int(payload_bytes),
        "transport": "temp_file_http_body",
    }


def _run_deepseek_oneshot(
    *,
    model_input: str,
    instructions: str,
    model: str,
    timeout_s: int,
) -> Dict[str, Any]:
    curl_bin = str(shutil.which("curl") or "").strip()
    if not curl_bin:
        raise RuntimeError("curl not found on PATH for deepseek backend")
    api_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("DEEPSEEK_API_KEY is required for deepseek backend")
    base_url = str(os.environ.get("EIEL_DEEPSEEK_BASE_URL") or "https://api.deepseek.com").strip().rstrip("/")
    payload = {
        "messages": [
            {"role": "system", "content": str(instructions or "").strip()},
            {"role": "user", "content": str(model_input or "").strip()},
        ],
        "model": str(model or "").strip() or "deepseek-chat",
        "stream": False,
    }
    payload_json = json.dumps(payload, ensure_ascii=False)
    payload_bytes = len(payload_json.encode("utf-8"))
    # Advisory backend calls should fail closed on network stalls instead of
    # inheriting a long scheduled-turn timeout and freezing the workflow.
    request_timeout_s = min(max(30, int(timeout_s or CODEX_TIMEOUT_DEFAULT_S)), 90)
    payload_file = None
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as pf:
        pf.write(payload_json)
        pf.flush()
        payload_file = pf.name
    cmd: List[str] = [
        curl_bin,
        "-sS",
        "--connect-timeout",
        "15",
        "--max-time",
        str(request_timeout_s),
        f"{base_url}/chat/completions",
        "-H",
        "Content-Type: application/json",
        "-H",
        f"Authorization: Bearer {api_key}",
        "--data-binary",
        f"@{payload_file}",
    ]
    try:
        cp = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=request_timeout_s + 5,
        )
    finally:
        try:
            if payload_file:
                Path(payload_file).unlink(missing_ok=True)
        except Exception:
            pass
    raw_out = str(cp.stdout or "").strip()
    raw_err = str(cp.stderr or "").strip()
    body: Dict[str, Any] = {}
    try:
        body = json.loads(raw_out) if raw_out else {}
    except Exception:
        body = {}
    if cp.returncode != 0:
        return {
            "ok": False,
            "content": raw_err or raw_out or "",
            "error": raw_err or raw_out or f"deepseek_exit_{int(cp.returncode)}",
            "exit_code": int(cp.returncode),
            "selected_model": str(payload.get("model") or ""),
            "binary": curl_bin,
            "base_url": base_url,
            "request_payload_bytes": int(payload_bytes),
            "transport": "temp_file_http_body",
        }
    message = ""
    if isinstance(body, dict):
        choices = body.get("choices")
        if isinstance(choices, list) and choices:
            msg = choices[0].get("message") if isinstance(choices[0], dict) else {}
            if isinstance(msg, dict):
                message = str(msg.get("content") or "").strip()
    if message:
        return {
            "ok": True,
            "content": message,
            "error": "",
            "exit_code": int(cp.returncode),
            "selected_model": str(body.get("model") or payload.get("model") or ""),
            "binary": curl_bin,
            "base_url": base_url,
            "request_payload_bytes": int(payload_bytes),
            "transport": "temp_file_http_body",
        }
    err_block = body.get("error") if isinstance(body, dict) else {}
    err_code = str(err_block.get("code") or "").strip() if isinstance(err_block, dict) else ""
    err_txt = str(err_block.get("message") or err_block or "").strip() if err_block else ""
    merged = (f"{err_code}: {err_txt}".strip(": ").strip()) or raw_out or raw_err or "deepseek_empty_response"
    return {
        "ok": False,
        "content": merged,
        "error": merged,
        "exit_code": int(cp.returncode),
        "selected_model": str(payload.get("model") or ""),
        "binary": curl_bin,
        "base_url": base_url,
        "request_payload_bytes": int(payload_bytes),
        "transport": "temp_file_http_body",
    }


def _ollama_binary() -> str:
    return str(shutil.which("ollama") or "").strip()


def _ollama_runtime_probe() -> Dict[str, Any]:
    binary = _ollama_binary()
    if not binary:
        return {"ok": False, "binary": "", "version": "", "error": "missing_binary"}
    try:
        cp = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        version_txt = str((cp.stdout or cp.stderr or "")).strip().splitlines()
        version_line = version_txt[0].strip() if version_txt else ""
        return {
            "ok": bool(cp.returncode == 0 and version_line),
            "binary": binary,
            "version": version_line,
            "exit_code": int(cp.returncode),
            "error": "" if cp.returncode == 0 else (version_line or "version_probe_failed"),
        }
    except Exception as e:
        return {"ok": False, "binary": binary, "version": "", "error": str(e)}
