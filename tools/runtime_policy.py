"""Shared runtime policy loader for non-engine operational defaults."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

import hashlib
import os


_ENV_OVERRIDE_MAP: Dict[str, Tuple[str, ...]] = {
    "scheduler.global_concurrency_cap": ("EIEL_SCHEDULER_GLOBAL_CONCURRENCY_CAP",),
    "scheduler.role_concurrency_caps": ("EIEL_SCHEDULER_ROLE_CONCURRENCY_CAPS",),
    "scheduler.resource_class_caps": ("EIEL_SCHEDULER_RESOURCE_CLASS_CAPS",),
    "mobile.chat_turn_timeout_s": ("EIEL_MOBILE_CHAT_TIMEOUT_S", "EIEL_CHAT_TURN_TIMEOUT_S", "EIEL_CHAT_TIMEOUT_S"),
    "mobile.chat_idle_timeout_s": ("EIEL_CHAT_IDLE_TIMEOUT_S",),
    "mobile.workflow_timeout_s": ("EIEL_MOBILE_WORKFLOW_TIMEOUT_S",),
    "mobile.direct_host_verification_timeout_s": ("EIEL_MOBILE_HOST_VERIFICATION_TIMEOUT_S",),
    "mobile.chat_model": ("EIEL_MOBILE_CHAT_MODEL", "EIEL_MOBILE_MODEL"),
    "mobile.chat_reasoning_effort": ("EIEL_MOBILE_CHAT_REASONING_EFFORT", "EIEL_MOBILE_REASONING_EFFORT"),
    "mobile.chat_fallback_models": ("EIEL_MOBILE_CHAT_FALLBACK_MODELS", "EIEL_MOBILE_FALLBACK_MODELS"),
    "agents.codex_turn_timeout_s": ("EIEL_AGENTS_CODEX_TIMEOUT_S",),
    "agents.codex_max_workers": ("EIEL_CODEX_MAX_WORKERS",),
    "agents.codex_max_workers_per_role": ("EIEL_CODEX_MAX_WORKERS_PER_ROLE",),
    "agents.orchestrator_turn_timeout_s": ("EIEL_AGENTS_ORCHESTRATOR_TIMEOUT_S",),
    "assistant.api_enabled": ("ASSISTANT_API_ENABLED",),
    "assistant.token_file": ("ASSISTANT_TOKEN_FILE",),
    "assistant.read_only_mode": ("ASSISTANT_READ_ONLY_MODE",),
    "assistant.default_execution_mode": ("ASSISTANT_DEFAULT_EXECUTION_MODE",),
    "assistant.allow_live_mode": ("ASSISTANT_ALLOW_LIVE_MODE",),
    "assistant.session_ttl_sec": ("ASSISTANT_SESSION_TTL_SEC",),
    "assistant.max_sessions": ("ASSISTANT_MAX_SESSIONS",),
    "assistant.max_diff_bytes": ("ASSISTANT_MAX_DIFF_BYTES",),
    "assistant.max_write_file_bytes": ("ASSISTANT_MAX_WRITE_FILE_BYTES",),
    "assistant.max_stdout_bytes": ("ASSISTANT_MAX_STDOUT_BYTES",),
    "assistant.max_stderr_bytes": ("ASSISTANT_MAX_STDERR_BYTES",),
    "assistant.max_artifact_read_bytes": ("ASSISTANT_MAX_ARTIFACT_READ_BYTES",),
    "assistant.control_pane_session_ttl_sec": ("ASSISTANT_CONTROL_PANE_SESSION_TTL_SEC",),
    "assistant.browser_relay_ttl_sec": ("ASSISTANT_BROWSER_RELAY_TTL_SEC",),
    "assistant.probe.enabled": ("ASSISTANT_PROBE_ENABLED",),
    "assistant.probe.auth_mode": ("ASSISTANT_PROBE_AUTH_MODE",),
    "assistant.probe.secret": ("ASSISTANT_PROBE_SECRET",),
    "assistant.probe.max_body_bytes": ("ASSISTANT_PROBE_MAX_BODY_BYTES",),
    "assistant.probe.max_echo_bytes": ("ASSISTANT_PROBE_MAX_ECHO_BYTES",),
    "assistant.probe.max_delay_ms": ("ASSISTANT_PROBE_MAX_DELAY_MS",),
    "assistant.probe.allowed_ingress_tags": ("ASSISTANT_PROBE_ALLOWED_INGRESS_TAGS",),
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]

def runtime_policy_path() -> Path:
    return (_repo_root() / "config" / "runtime_policy.properties").resolve()


def _load_properties(path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not path.exists():
        return out
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("!"):
            continue
        key, sep, value = line.partition("=")
        if not sep:
            continue
        name = key.strip()
        if not name:
            continue
        out[name] = value.strip()
    return out


def _policy_file_signature(path: Path) -> Tuple[int, int, str]:
    try:
        stat = path.stat()
    except FileNotFoundError:
        return (-1, -1, "")
    except Exception:
        return (-2, -2, "")
    digest = ""
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except Exception:
        digest = ""
    return (int(stat.st_mtime_ns), int(stat.st_size), digest)


def load_runtime_policy() -> Dict[str, str]:
    return _load_properties(runtime_policy_path())


def clear_runtime_policy_cache() -> None:
    return None


load_runtime_policy.cache_clear = clear_runtime_policy_cache  # type: ignore[attr-defined]


def _classify_runtime_policy_key(key: str) -> str:
    normalized = str(key or "").strip().lower()
    if normalized.startswith("mobile."):
        return "future_turn_only"
    if normalized.startswith("scheduler."):
        return "future_job_only"
    if normalized.startswith("agents.") or normalized.startswith("host_verification."):
        return "future_turn_only"
    if normalized.startswith("assistant."):
        return "restart_required"
    return "unknown"


def runtime_policy_inventory() -> Dict[str, Any]:
    path = runtime_policy_path()
    policy = load_runtime_policy()
    signature = _policy_file_signature(path)
    return {
        "schema_version": "runtime_policy_inventory_v1",
        "path": str(path),
        "exists": bool(path.exists()),
        "key_count": len(policy),
        "keys": sorted(policy.keys()),
        "file_signature": {
            "mtime_ns": int(signature[0]),
            "size_bytes": int(signature[1]),
        },
    }


def runtime_refresh_boundary_inventory() -> Dict[str, Any]:
    policy = load_runtime_policy()
    future_turn_only_keys: List[str] = []
    future_job_only_keys: List[str] = []
    restart_required_keys: List[str] = []
    unknown_keys: List[str] = []
    env_overrides_active: List[Dict[str, str]] = []

    for key in sorted(policy.keys()):
        classification = _classify_runtime_policy_key(key)
        if classification == "future_turn_only":
            future_turn_only_keys.append(key)
        elif classification == "future_job_only":
            future_job_only_keys.append(key)
        elif classification == "restart_required":
            restart_required_keys.append(key)
        else:
            unknown_keys.append(key)
        for env_name in _ENV_OVERRIDE_MAP.get(key, ()):
            raw = str(os.environ.get(env_name, "") or "").strip()
            if raw:
                env_overrides_active.append(
                    {
                        "key": key,
                        "env_name": env_name,
                        "effective_source": "environment",
                    }
                )
                break

    live_refreshable_keys = sorted({*future_turn_only_keys, *future_job_only_keys})
    return {
        "schema_version": "runtime_refresh_boundary_inventory_v1",
        "live_refreshable_keys": live_refreshable_keys,
        "future_turn_only_keys": future_turn_only_keys,
        "future_job_only_keys": future_job_only_keys,
        "restart_required_keys": restart_required_keys,
        "unknown_keys": unknown_keys,
        "env_overrides_active": env_overrides_active,
    }


def get_runtime_policy_int(
    key: str,
    default: int,    *,
    env_names: tuple[str, ...] = (),
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = ""
    for env_name in env_names:
        raw = str(os.environ.get(env_name, "") or "").strip()
        if raw:
            break
    if not raw:
        raw = str(load_runtime_policy().get(key, "") or "").strip()
    try:
        value = int(raw) if raw else int(default)
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return int(value)


def get_runtime_policy_text(
    key: str,
    default: str,
    *,
    env_names: tuple[str, ...] = (),
) -> str:
    raw = ""
    for env_name in env_names:
        raw = str(os.environ.get(env_name, "") or "").strip()
        if raw:
            break
    if not raw:
        raw = str(load_runtime_policy().get(key, "") or "").strip()
    return raw if raw else str(default)


def get_runtime_policy_csv(
    key: str,
    default: str = "",
    *,
    env_names: tuple[str, ...] = (),
) -> List[str]:
    raw = get_runtime_policy_text(key, default, env_names=env_names)
    values: List[str] = []
    for part in str(raw or "").split(","):
        txt = str(part or "").strip()
        if txt and txt not in values:
            values.append(txt)
    return values
