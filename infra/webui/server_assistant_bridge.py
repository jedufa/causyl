"""Assistant access bridge trial surface for authenticated external use.

Last modified: 2026-04-01 Europe/Vienna

This module implements the bounded assistant bridge described by
PIS-20260331-235500Z-T8M3.

The bridge is intentionally narrow:
- authenticated read/search over the repo and active ledger items
- isolated worktree-backed patch trials by default
- explicit, allowlisted validation commands only
- durable audit/receipt storage under assistant_bridge runtime paths
- optional browser-session exchange for the existing mobile plane surface

It is not a scheduler, verifier owner, or canonical writeback surface.
"""

from __future__ import annotations

import base64
import datetime as _dt
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
from urllib.parse import urlencode

from tools import runtime_policy as _runtime_policy
from infra.controlplane import backend_api_boundary as _backend_boundary
from infra.controlplane import pis_improvement_entrypoints as _pis_improvement_entrypoints
from infra.webui.ledger import ledger_storage as _ledger_storage
from infra.webui.server_common import _now_utc_iso, atomic_write_canonical_json

BRIDGE_SCHEMA_VERSION = "assistant_bridge_trial_surface_v1"
BRIDGE_AUTH_STATUS_SCHEMA_VERSION = "assistant_bridge_auth_status_v1"
BRIDGE_CONTROL_PANE_SESSION_SCHEMA_VERSION = "assistant_bridge_control_pane_session_v1"
BRIDGE_BROWSER_RELAY_SCHEMA_VERSION = "assistant_bridge_browser_relay_v1"
BRIDGE_SESSION_SCHEMA_VERSION = "assistant_bridge_session_v1"
BRIDGE_SESSION_MINT_SCHEMA_VERSION = "assistant_bridge_session_mint_v1"
BRIDGE_COMMAND_RECEIPT_SCHEMA_VERSION = "assistant_bridge_command_receipt_v1"
BRIDGE_EVENT_SCHEMA_VERSION = "assistant_bridge_event_v1"
BRIDGE_ARTIFACT_LIST_SCHEMA_VERSION = "assistant_bridge_artifact_list_v1"
BRIDGE_ARTIFACT_READ_SCHEMA_VERSION = "assistant_bridge_artifact_read_v1"
BRIDGE_UID = "PIS-20260331-235500Z-T8M3"
_TOKEN_FILE_SCHEMA_VERSION = "assistant_bridge_tokens_v1"
_TOKEN_STATE_SCHEMA_VERSION = "assistant_bridge_token_state_v1"
_CONTROL_PANE_SESSION_PREFIX = "abrctl"
_BROWSER_RELAY_PREFIX = "abrrel"
_SESSION_MINT_PREFIX = "abrsess"
_AUTHORIZATION_HEADER = "authorization"
_DEFAULT_SEARCH_MAX_RESULTS = 20
_MAX_SEARCH_RESULTS = 100
_MAX_SEARCH_FILE_BYTES = 512 * 1024
_MAX_SEARCH_TOTAL_BYTES = 200 * 1024
_MAX_SEARCH_SNIPPET_CHARS = 240
_ALLOWED_VALIDATION_KINDS = {"py_compile", "pytest", "git_status", "git_diff"}
_ACTIVE_LEDGER_STATUSES = {"draft", "active", "in_progress", "blocked"}
_ALLOWED_CONTROL_PANE_PATHS = {
    "/mobile",
    "/admin/mobile",
    "/mobile/backlog",
    "/admin/mobile/backlog",
    "/mobile/update",
    "/admin/mobile/update",
}
_SKIP_SEARCH_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    "node_modules",
}
_PROTECTED_MUTATION_PREFIXES = (
    "governance/ledger/",
    "governance/agents/runtime/scheduled_jobs/",
    "runtime/state/",
    "runtime/secrets/",
    "runtime/artifacts/assistant_bridge/",
)
_PROTECTED_READ_PREFIXES = (
    "runtime/secrets/",
    "runtime/state/assistant_bridge/browser_relays/",
    "runtime/state/assistant_bridge/control_pane_sessions/",
    "runtime/artifacts/assistant_bridge/",
)
_ROUTE_COMMANDS: Dict[str, Tuple[str, str]] = {
    "/api/assistant/repo/search": ("assistant.repo_search", "read_only"),
    "/api/assistant/ledger/list_active": ("assistant.ledger_list_active", "read_only"),
    "/api/assistant/ledger/read": ("assistant.ledger_read_item", "read_only"),
    "/api/assistant/file/read": ("assistant.file_read", "read_only"),
    "/api/assistant/pis_improvement/trigger": ("assistant.pis_improvement_trigger", "isolated_trial"),
    "/api/assistant/session/open": ("assistant.execution_session_open", "isolated_trial"),
    "/api/assistant/session/apply_diff": ("assistant.execution_session_apply_diff", "isolated_trial"),
    "/api/assistant/session/validate": ("assistant.execution_session_validate", "validation_only"),
    "/api/assistant/session/artifacts/list": ("assistant.execution_session_list_artifacts", "read_only"),
    "/api/assistant/session/artifacts/read": ("assistant.execution_session_read_artifact", "read_only"),
    "/api/assistant/session/close": ("assistant.execution_session_close", "isolated_trial"),
}
_BROWSER_RELAY_OPERATIONS: Dict[str, Tuple[str, str]] = {
    "auth_status": ("auth_status", "read_only"),
    "repo_search": ("assistant.repo_search", "read_only"),
    "ledger_list_active": ("assistant.ledger_list_active", "read_only"),
    "ledger_read": ("assistant.ledger_read_item", "read_only"),
    "file_read": ("assistant.file_read", "read_only"),
    "artifact": ("assistant.browser_relay_artifact", "read_only"),
    "session_open": ("assistant.browser_relay_session_open", "isolated_trial"),
    "session_close": ("assistant.browser_relay_session_close", "isolated_trial"),
    "write_file": ("assistant.browser_relay_write_file", "isolated_trial"),
    "delete_file": ("assistant.browser_relay_delete_file", "isolated_trial"),
    "run_validation": ("assistant.browser_relay_run_validation", "validation_only"),
    "apply_diff": ("assistant.browser_relay_apply_diff", "isolated_trial"),
}
_BROWSER_RELAY_STORED_PAYLOAD_OPERATIONS = {"session_open", "session_close", "write_file", "delete_file", "run_validation", "apply_diff"}
_BROWSER_RELAY_SINGLE_USE_OPERATIONS = set(_BROWSER_RELAY_STORED_PAYLOAD_OPERATIONS)
_READ_ONLY_ROUTE_PATHS = {
    "/api/assistant/repo/search",
    "/api/assistant/ledger/list_active",
    "/api/assistant/ledger/read",
    "/api/assistant/file/read",
}
_READ_ONLY_BROWSER_RELAY_OPERATIONS = {
    "auth_status",
    "repo_search",
    "ledger_list_active",
    "ledger_read",
    "file_read",
}
_LOCK = threading.RLock()


class BridgeRequestError(RuntimeError):
    """Raise one structured fail-closed bridge request error."""

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.code = str(code or "bridge_error")
        self.details = dict(details or {})


@dataclass(frozen=True)
class BridgeConfig:
    enabled: bool
    token_file: str
    read_only_mode: bool
    default_execution_mode: str
    allow_live_mode: bool
    session_ttl_sec: int
    max_sessions: int
    max_diff_bytes: int
    max_write_file_bytes: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    max_artifact_read_bytes: int
    control_pane_session_ttl_sec: int
    browser_relay_ttl_sec: int


@dataclass(frozen=True)
class AuthenticatedToken:
    token_id: str
    issuer: str
    audience: str
    scopes: Tuple[str, ...]
    created_at: str
    expires_at: str
    last_used_at: str
    allowed_repo_roots: Tuple[str, ...]
    allowed_pis_prefixes: Tuple[str, ...]


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _clean_text_list(values: Iterable[Any]) -> List[str]:
    out: List[str] = []
    for raw in list(values or []):
        text = str(raw or "").strip()
        if text and text not in out:
            out.append(text)
    return out


def _bool_text(value: Any, default: bool) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return bool(default)
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _parse_iso_utc(value: Any) -> Optional[_dt.datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return _dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compact_utc() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _json_hash(obj: Dict[str, Any]) -> str:
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _policy_properties() -> Dict[str, str]:
    return _runtime_policy.load_runtime_policy()


def _policy_text(*, keys: Sequence[str], default: str, env_names: Sequence[str] = ()) -> str:
    for env_name in list(env_names or []):
        raw = str(os.environ.get(env_name, "") or "").strip()
        if raw:
            return raw
    props = _policy_properties()
    for key in list(keys or []):
        raw = str(props.get(str(key), "") or "").strip()
        if raw:
            return raw
    return str(default)


def _policy_int(
    *,
    keys: Sequence[str],
    default: int,
    env_names: Sequence[str] = (),
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    raw = _policy_text(keys=keys, default=str(default), env_names=env_names)
    try:
        value = int(raw)
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return int(value)


def _assistant_control_file(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "webui" / "assistant_bridge_control.env").resolve()


def _assistant_bridge_enabled_override(repo_root: Path, default: bool) -> bool:
    path = _assistant_control_file(repo_root)
    if not path.exists() or not path.is_file():
        return bool(default)
    raw = path.read_text(encoding="utf-8", errors="replace")
    for line in raw.splitlines():
        text = str(line or "").strip()
        if not text or text.startswith("#") or "=" not in text:
            continue
        key, value = text.split("=", 1)
        key_text = str(key).strip()
        if key_text.startswith("export "):
            key_text = key_text[len("export ") :].strip()
        if key_text == "EIEL_ASSISTANT_BRIDGE_ENABLED":
            return _bool_text(value, default)
    return bool(default)


def _load_config(repo_root: Path) -> BridgeConfig:
    root = Path(repo_root).resolve()
    enabled = _bool_text(
        _policy_text(
            keys=("assistant.api_enabled", "assistant_api_enabled"),
            default="false",
            env_names=("ASSISTANT_API_ENABLED",),
        ),
        False,
    )
    enabled = _assistant_bridge_enabled_override(root, enabled)
    token_file = _policy_text(
        keys=("assistant.token_file", "assistant_token_file"),
        default="",
        env_names=("ASSISTANT_TOKEN_FILE",),
    )
    read_only_mode = _bool_text(
        _policy_text(
            keys=("assistant.read_only_mode", "assistant_read_only_mode"),
            default="true",
            env_names=("ASSISTANT_READ_ONLY_MODE",),
        ),
        True,
    )
    default_execution_mode = str(
        _policy_text(
            keys=("assistant.default_execution_mode", "assistant_default_execution_mode"),
            default="isolated",
            env_names=("ASSISTANT_DEFAULT_EXECUTION_MODE",),
        )
        or "isolated"
    ).strip().lower()
    if default_execution_mode not in {"isolated", "live"}:
        default_execution_mode = "isolated"
    allow_live_mode = _bool_text(
        _policy_text(
            keys=("assistant.allow_live_mode", "assistant_allow_live_mode"),
            default="false",
            env_names=("ASSISTANT_ALLOW_LIVE_MODE",),
        ),
        False,
    )
    session_ttl_sec = _policy_int(
        keys=("assistant.session_ttl_sec", "assistant_session_ttl_sec"),
        default=3600,
        env_names=("ASSISTANT_SESSION_TTL_SEC",),
        minimum=60,
        maximum=7 * 24 * 3600,
    )
    max_sessions = _policy_int(
        keys=("assistant.max_sessions", "assistant_max_sessions"),
        default=3,
        env_names=("ASSISTANT_MAX_SESSIONS",),
        minimum=1,
        maximum=64,
    )
    max_diff_bytes = _policy_int(
        keys=("assistant.max_diff_bytes", "assistant_max_diff_bytes"),
        default=256 * 1024,
        env_names=("ASSISTANT_MAX_DIFF_BYTES",),
        minimum=1024,
        maximum=10 * 1024 * 1024,
    )
    max_write_file_bytes = _policy_int(
        keys=("assistant.max_write_file_bytes", "assistant_max_write_file_bytes"),
        default=min(max_diff_bytes, 256 * 1024),
        env_names=("ASSISTANT_MAX_WRITE_FILE_BYTES",),
        minimum=1024,
        maximum=10 * 1024 * 1024,
    )
    max_stdout_bytes = _policy_int(
        keys=("assistant.max_stdout_bytes", "assistant_max_stdout_bytes"),
        default=16 * 1024,
        env_names=("ASSISTANT_MAX_STDOUT_BYTES",),
        minimum=1024,
        maximum=5 * 1024 * 1024,
    )
    max_stderr_bytes = _policy_int(
        keys=("assistant.max_stderr_bytes", "assistant_max_stderr_bytes"),
        default=16 * 1024,
        env_names=("ASSISTANT_MAX_STDERR_BYTES",),
        minimum=1024,
        maximum=5 * 1024 * 1024,
    )
    max_artifact_read_bytes = _policy_int(
        keys=("assistant.max_artifact_read_bytes", "assistant_max_artifact_read_bytes"),
        default=64 * 1024,
        env_names=("ASSISTANT_MAX_ARTIFACT_READ_BYTES",),
        minimum=1024,
        maximum=10 * 1024 * 1024,
    )
    control_pane_session_ttl_sec = _policy_int(
        keys=("assistant.control_pane_session_ttl_sec", "assistant_control_pane_session_ttl_sec"),
        default=min(session_ttl_sec, 3600),
        env_names=("ASSISTANT_CONTROL_PANE_SESSION_TTL_SEC",),
        minimum=60,
        maximum=24 * 3600,
    )
    browser_relay_ttl_sec = _policy_int(
        keys=("assistant.browser_relay_ttl_sec", "assistant_browser_relay_ttl_sec"),
        default=min(session_ttl_sec, 300),
        env_names=("ASSISTANT_BROWSER_RELAY_TTL_SEC",),
        minimum=60,
        maximum=900,
    )
    return BridgeConfig(
        enabled=enabled,
        token_file=token_file,
        read_only_mode=read_only_mode,
        default_execution_mode=default_execution_mode,
        allow_live_mode=allow_live_mode,
        session_ttl_sec=session_ttl_sec,
        max_sessions=max_sessions,
        max_diff_bytes=max_diff_bytes,
        max_write_file_bytes=max_write_file_bytes,
        max_stdout_bytes=max_stdout_bytes,
        max_stderr_bytes=max_stderr_bytes,
        max_artifact_read_bytes=max_artifact_read_bytes,
        control_pane_session_ttl_sec=control_pane_session_ttl_sec,
        browser_relay_ttl_sec=browser_relay_ttl_sec,
    )


def _bridge_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "assistant_bridge").resolve()


def _sessions_root(repo_root: Path) -> Path:
    path = (_bridge_root(repo_root) / "sessions").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _worktrees_root(repo_root: Path) -> Path:
    path = (_bridge_root(repo_root) / "worktrees").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _runs_state_root(repo_root: Path) -> Path:
    path = (_bridge_root(repo_root) / "runs").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _audit_root(repo_root: Path) -> Path:
    path = (_bridge_root(repo_root) / "audit").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _artifacts_root(repo_root: Path) -> Path:
    path = (Path(repo_root).resolve() / "runtime" / "artifacts" / "assistant_bridge").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _control_pane_sessions_root(repo_root: Path) -> Path:
    path = (_bridge_root(repo_root) / "control_pane_sessions").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _browser_relay_root(repo_root: Path) -> Path:
    path = (_bridge_root(repo_root) / "browser_relays").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _token_state_path(repo_root: Path) -> Path:
    return (_bridge_root(repo_root) / "token_state.json").resolve()


def _command_receipt_path(repo_root: Path, request_identity_hash: str) -> Path:
    return (_audit_root(repo_root) / "commands" / f"{request_identity_hash}.json").resolve()


def _events_path(repo_root: Path) -> Path:
    return (_audit_root(repo_root) / "command_events.jsonl").resolve()


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = path.parent / f".{path.name}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _read_jsonl(path: Path, *, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    out: List[Dict[str, Any]] = []
    for raw in lines[-int(max(1, limit)) :]:
        try:
            obj = json.loads(raw)
        except Exception:
            continue
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _repo_relpath(repo_root: Path, path: Path) -> str:
    root = Path(repo_root).resolve()
    target = Path(path).resolve()
    try:
        return target.relative_to(root).as_posix()
    except Exception:
        return target.as_posix()


def _append_bridge_event(repo_root: Path, *, event: str, payload: Dict[str, Any]) -> None:
    _append_jsonl(
        _events_path(repo_root),
        {
            "schema_version": BRIDGE_EVENT_SCHEMA_VERSION,
            "uid": BRIDGE_UID,
            "event_id": str(uuid.uuid4()),
            "event": _clean_text(event),
            "utc": _utc_now_iso(),
            "payload": dict(payload or {}),
        },
    )


def _read_token_state(repo_root: Path) -> Dict[str, Any]:
    obj = _read_json(_token_state_path(repo_root))
    if not obj:
        return {
            "schema_version": _TOKEN_STATE_SCHEMA_VERSION,
            "updated_utc": "",
            "last_used_by_token_id": {},
        }
    out = {
        "schema_version": str(obj.get("schema_version") or _TOKEN_STATE_SCHEMA_VERSION),
        "updated_utc": str(obj.get("updated_utc") or ""),
        "last_used_by_token_id": {},
    }
    raw = obj.get("last_used_by_token_id")
    if isinstance(raw, dict):
        out["last_used_by_token_id"] = {str(k): _clean_text(v) for k, v in raw.items() if _clean_text(k)}
    return out


def _touch_token_last_used(repo_root: Path, token_id: str) -> str:
    now_utc = _utc_now_iso()
    path = _token_state_path(repo_root)
    with _LOCK:
        state = _read_token_state(repo_root)
        used = state.get("last_used_by_token_id") if isinstance(state.get("last_used_by_token_id"), dict) else {}
        used[str(token_id)] = now_utc
        state["last_used_by_token_id"] = used
        state["updated_utc"] = now_utc
        _write_json_atomic(path, state)
    return now_utc


def _resolve_token_file(repo_root: Path, raw_path: str) -> Path:
    text = str(raw_path or "").strip()
    if not text:
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "assistant_api_disabled", "assistant bridge token file is not configured")
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        candidate = (Path(repo_root).resolve() / candidate).resolve()
    return candidate


def _normalize_token_row(row: Dict[str, Any], *, repo_root: Path, last_used_state: Dict[str, Any]) -> Dict[str, Any]:
    token_id = _clean_text(row.get("token_id"))
    if not token_id:
        return {}
    scopes = _clean_text_list(list(row.get("scopes") or []))
    allowed_repo_roots = _clean_text_list(list(row.get("allowed_repo_roots") or [])) or ["."]
    allowed_pis_prefixes = _clean_text_list(list(row.get("allowed_pis_prefixes") or []))
    last_used_lookup = last_used_state.get("last_used_by_token_id") if isinstance(last_used_state.get("last_used_by_token_id"), dict) else {}
    return {
        "token_id": token_id,
        "issuer": _clean_text(row.get("issuer")),
        "audience": _clean_text(row.get("audience")),
        "scopes": scopes,
        "created_at": _clean_text(row.get("created_at")),
        "expires_at": _clean_text(row.get("expires_at")),
        "last_used_at": _clean_text(row.get("last_used_at")) or _clean_text(last_used_lookup.get(token_id)),
        "allowed_repo_roots": allowed_repo_roots,
        "allowed_pis_prefixes": allowed_pis_prefixes,
        "revoked": bool(row.get("revoked") is True or _clean_text(row.get("status")).lower() == "revoked"),
        "token": str(row.get("token") or "").strip(),
        "token_sha256": _clean_text(row.get("token_sha256")),
    }


def _load_token_rows(repo_root: Path, config: BridgeConfig) -> List[Dict[str, Any]]:
    token_file = _resolve_token_file(repo_root, config.token_file)
    if not token_file.exists() or not token_file.is_file():
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "assistant_api_disabled", "assistant bridge token file is missing")
    try:
        raw_obj = json.loads(token_file.read_text(encoding="utf-8", errors="replace"))
    except Exception as exc:
        raise BridgeRequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "token_file_invalid", f"assistant token file is malformed: {exc}") from exc
    rows: List[Dict[str, Any]] = []
    if isinstance(raw_obj, dict):
        token_rows = raw_obj.get("tokens")
        if isinstance(token_rows, list):
            rows = [dict(item) for item in token_rows if isinstance(item, dict)]
        elif isinstance(raw_obj.get("items"), list):
            rows = [dict(item) for item in list(raw_obj.get("items") or []) if isinstance(item, dict)]
    elif isinstance(raw_obj, list):
        rows = [dict(item) for item in raw_obj if isinstance(item, dict)]
    last_used_state = _read_token_state(repo_root)
    out: List[Dict[str, Any]] = []
    for row in rows:
        normalized = _normalize_token_row(row, repo_root=repo_root, last_used_state=last_used_state)
        if normalized:
            out.append(normalized)
    return out


def _header_map(headers: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    items: List[Tuple[Any, Any]] = []
    try:
        items = list(headers.items())
    except Exception:
        if isinstance(headers, dict):
            items = list(headers.items())
    for raw_key, raw_value in items:
        key = str(raw_key or "").strip().lower()
        value = str(raw_value or "").strip()
        if key and key not in out:
            out[key] = value
    return out


def _bearer_token_from_request(headers: Mapping[str, str]) -> str:
    header_value = str(headers.get(_AUTHORIZATION_HEADER) or "").strip()
    if not header_value:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "missing_bearer_token", "assistant routes require Authorization: Bearer <token>")
    prefix, _, value = header_value.partition(" ")
    if prefix.lower() != "bearer" or not value.strip():
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "invalid_bearer_token", "assistant routes require a bearer token")
    return value.strip()


def _match_token_row(rows: List[Dict[str, Any]], *, provided_token: str) -> Dict[str, Any]:
    provided_hash = _sha256_text(provided_token)
    for row in list(rows or []):
        if row.get("revoked") is True:
            continue
        stored_sha = _clean_text(row.get("token_sha256"))
        stored_plain = str(row.get("token") or "").strip()
        if stored_sha and hmac.compare_digest(stored_sha, provided_hash):
            return row
        if stored_plain and hmac.compare_digest(stored_plain, provided_token):
            return row
    return {}


def _token_row_by_id(rows: List[Dict[str, Any]], token_id: str) -> Dict[str, Any]:
    wanted = _clean_text(token_id)
    if not wanted:
        return {}
    for row in list(rows or []):
        if _clean_text(row.get("token_id")) == wanted and row.get("revoked") is not True:
            return row
    return {}


def _check_bridge_enabled(config: BridgeConfig) -> None:
    if not bool(config.enabled):
        raise BridgeRequestError(HTTPStatus.NOT_FOUND, "assistant_api_disabled", "assistant bridge endpoints are disabled")


def authenticate_bearer_request(handler: Any, *, repo_root: Path) -> Tuple[BridgeConfig, AuthenticatedToken]:
    root = Path(repo_root).resolve()
    config = _load_config(root)
    _check_bridge_enabled(config)
    headers = _header_map(getattr(handler, "headers", {}))
    token_value = _bearer_token_from_request(headers)
    matched = _match_token_row(_load_token_rows(root, config), provided_token=token_value)
    if not matched:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "auth_denied", "missing or invalid assistant bearer token")
    expires_dt = _parse_iso_utc(matched.get("expires_at"))
    now_dt = _utc_now()
    if expires_dt is not None and expires_dt <= now_dt:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "token_expired", "assistant bearer token is expired")
    last_used_at = _touch_token_last_used(root, str(matched.get("token_id") or ""))
    auth = AuthenticatedToken(
        token_id=str(matched.get("token_id") or ""),
        issuer=str(matched.get("issuer") or ""),
        audience=str(matched.get("audience") or ""),
        scopes=tuple(_clean_text_list(list(matched.get("scopes") or []))),
        created_at=str(matched.get("created_at") or ""),
        expires_at=str(matched.get("expires_at") or ""),
        last_used_at=last_used_at,
        allowed_repo_roots=tuple(_clean_text_list(list(matched.get("allowed_repo_roots") or [])) or ["."]),
        allowed_pis_prefixes=tuple(_clean_text_list(list(matched.get("allowed_pis_prefixes") or []))),
    )
    return config, auth


def _auth_from_token_row(repo_root: Path, row: Dict[str, Any]) -> AuthenticatedToken:
    token_id = _clean_text(row.get("token_id"))
    if not token_id:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "auth_denied", "assistant token row is missing token_id")
    expires_dt = _parse_iso_utc(row.get("expires_at"))
    if expires_dt is not None and expires_dt <= _utc_now():
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "token_expired", "assistant bearer token is expired")
    last_used_at = _touch_token_last_used(repo_root, token_id)
    return AuthenticatedToken(
        token_id=token_id,
        issuer=str(row.get("issuer") or ""),
        audience=str(row.get("audience") or ""),
        scopes=tuple(_clean_text_list(list(row.get("scopes") or []))),
        created_at=str(row.get("created_at") or ""),
        expires_at=str(row.get("expires_at") or ""),
        last_used_at=last_used_at,
        allowed_repo_roots=tuple(_clean_text_list(list(row.get("allowed_repo_roots") or [])) or ["."]),
        allowed_pis_prefixes=tuple(_clean_text_list(list(row.get("allowed_pis_prefixes") or []))),
    )


def _scope_set(auth: AuthenticatedToken) -> set[str]:
    return set(str(item or "").strip() for item in list(auth.scopes or ()) if str(item or "").strip())


def _require_scope(auth: AuthenticatedToken, scope: str) -> None:
    if scope not in _scope_set(auth):
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "forbidden_scope", f"missing required scope: {scope}")


def _require_any_scope(auth: AuthenticatedToken, scopes: Sequence[str]) -> None:
    scope_set = _scope_set(auth)
    wanted = [str(scope or "").strip() for scope in list(scopes or []) if str(scope or "").strip()]
    if not any(scope in scope_set for scope in wanted):
        raise BridgeRequestError(
            HTTPStatus.FORBIDDEN,
            "forbidden_scope",
            f"missing one of required scopes: {', '.join(wanted)}",
        )


def _check_read_only_route(config: BridgeConfig, path: str) -> None:
    if bool(config.read_only_mode) and str(path or "").strip() not in _READ_ONLY_ROUTE_PATHS:
        raise BridgeRequestError(
            HTTPStatus.FORBIDDEN,
            "assistant_read_only_mode",
            f"assistant bridge is running in read-only mode; route is disabled: {path}",
        )


def _check_read_only_browser_operation(config: BridgeConfig, operation: str) -> None:
    op = _normalize_browser_relay_operation(operation)
    if bool(config.read_only_mode) and op not in _READ_ONLY_BROWSER_RELAY_OPERATIONS:
        raise BridgeRequestError(
            HTTPStatus.FORBIDDEN,
            "assistant_read_only_mode",
            f"assistant bridge is running in read-only mode; browser relay operation is disabled: {op}",
        )


def _strip_repo_relative_prefix(text: str) -> str:
    value = str(text or "").replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    return value


def _normalize_repo_relative_path(raw: Any, *, field_name: str = "path") -> str:
    rel = _strip_repo_relative_prefix(str(raw or "").strip())
    if not rel:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, f"missing_{field_name}", f"{field_name} is required")
    path = Path(rel)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, f"invalid_{field_name}", f"{field_name} must be repo-relative")
    return path.as_posix()


def _repo_relpath_allowed_by_token(relpath: str, auth: AuthenticatedToken) -> bool:
    rel = _normalize_repo_relative_path(relpath, field_name="path")
    allowed_roots = _clean_text_list(list(auth.allowed_repo_roots or (".",))) or ["."]
    for raw_root in allowed_roots:
        root = _strip_repo_relative_prefix(str(raw_root or "").strip())
        if not root or root == ".":
            return True
        if rel == root or rel.startswith(root.rstrip("/") + "/"):
            return True
    return False


def _require_allowed_repo_relpath(relpath: str, auth: AuthenticatedToken) -> None:
    rel = _normalize_repo_relative_path(relpath, field_name="path")
    if not _repo_relpath_allowed_by_token(rel, auth):
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "forbidden_repo_root", f"path not allowed by token roots: {rel}")


def _relpath_has_git_boundary(relpath: str) -> bool:
    return ".git" in Path(str(relpath or "").strip()).parts


def _path_matches_prefix(relpath: str, prefix: str) -> bool:
    rel = _strip_repo_relative_prefix(str(relpath or "").strip())
    normalized_prefix = _strip_repo_relative_prefix(str(prefix or "").strip())
    if not normalized_prefix:
        return False
    return rel == normalized_prefix.rstrip("/") or rel.startswith(normalized_prefix.rstrip("/") + "/")


def _is_protected_read_relpath(relpath: str) -> bool:
    rel = _normalize_repo_relative_path(relpath, field_name="path")
    if _relpath_has_git_boundary(rel):
        return True
    return any(_path_matches_prefix(rel, prefix) for prefix in _PROTECTED_READ_PREFIXES)


def _allowed_repo_root_paths(repo_root: Path, auth: AuthenticatedToken) -> List[Path]:
    root = Path(repo_root).resolve()
    allowed: List[Path] = []
    for raw in list(auth.allowed_repo_roots or (".",)):
        txt = str(raw or "").strip() or "."
        candidate = _ledger_storage.safe_resolve_under(root, root / txt)
        if candidate is not None and candidate.exists():
            if candidate not in allowed:
                allowed.append(candidate)
    if not allowed:
        allowed.append(root)
    return allowed


def _requested_search_roots(repo_root: Path, auth: AuthenticatedToken, requested_roots: Sequence[Any]) -> List[Path]:
    root = Path(repo_root).resolve()
    allowed = _allowed_repo_root_paths(root, auth)
    if not requested_roots:
        return allowed
    out: List[Path] = []
    for raw in list(requested_roots or []):
        txt = str(raw or "").strip()
        if not txt:
            continue
        candidate = _ledger_storage.safe_resolve_under(root, root / txt)
        if candidate is None or not candidate.exists():
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_repo_root", f"search root is invalid: {txt}")
        if not any(candidate == allow or str(candidate).startswith(str(allow) + os.sep) for allow in allowed):
            raise BridgeRequestError(HTTPStatus.FORBIDDEN, "forbidden_repo_root", f"search root not allowed by token: {txt}")
        if candidate not in out:
            out.append(candidate)
    return out or allowed


def _is_protected_relpath(relpath: str) -> bool:
    rel = _normalize_repo_relative_path(relpath, field_name="path")
    if _relpath_has_git_boundary(rel):
        return True
    return any(_path_matches_prefix(rel, prefix) for prefix in _PROTECTED_MUTATION_PREFIXES)


def _read_request_body(handler: Any, *, max_bytes: int) -> bytes:
    try:
        length = int(getattr(handler, "headers", {}).get("Content-Length") or 0)
    except Exception:
        length = 0
    if length < 0:
        length = 0
    if length > int(max_bytes):
        raise BridgeRequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "body_too_large", f"request too large ({length} bytes > {int(max_bytes)} bytes)")
    return handler.rfile.read(length) if length > 0 else b""


def _read_request_json(handler: Any, *, max_bytes: int) -> Dict[str, Any]:
    body = _read_request_body(handler, max_bytes=max_bytes)
    if not body:
        return {}
    try:
        obj = json.loads(body.decode("utf-8"))
    except Exception as exc:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_json", f"request body must be valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_json", "request body must be a JSON object")
    return obj


def _allowed_pis(uid: str, auth: AuthenticatedToken) -> bool:
    prefixes = list(auth.allowed_pis_prefixes or ())
    if not prefixes:
        return True
    text = str(uid or "").strip()
    return any(text.startswith(prefix) for prefix in prefixes)


def _command_receipt(repo_root: Path, request_identity_hash: str) -> Dict[str, Any]:
    return _read_json(_command_receipt_path(repo_root, request_identity_hash))


def _write_command_receipt(repo_root: Path, receipt: Dict[str, Any]) -> None:
    request_identity_hash = _clean_text(receipt.get("request_identity_hash"))
    if not request_identity_hash:
        return
    _write_json_atomic(_command_receipt_path(repo_root, request_identity_hash), receipt)


def _store_command_started(
    repo_root: Path,
    *,
    boundary_result: Dict[str, Any],
    auth: AuthenticatedToken,
    route_path: str,
    intent_classification: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    request_identity_hash = _clean_text(boundary_result.get("request_identity_hash"))
    now_utc = _utc_now_iso()
    receipt = {
        "schema_version": BRIDGE_COMMAND_RECEIPT_SCHEMA_VERSION,
        "uid": BRIDGE_UID,
        "request_identity_hash": request_identity_hash,
        "request_id": _clean_text(boundary_result.get("request_id")),
        "command_id": _clean_text(boundary_result.get("command_id")),
        "token_id": auth.token_id,
        "route": _clean_text(route_path),
        "intent_classification": _clean_text(intent_classification),
        "status": "started",
        "started_utc": now_utc,
        "updated_utc": now_utc,
        "payload_sha256": _json_hash(payload),
        "payload_keys": sorted(str(key) for key in payload.keys()),
        "artifact_refs": [],
        "session_id": _clean_text(payload.get("session_id")),
        "boundary_result": dict(boundary_result),
    }
    _write_command_receipt(repo_root, receipt)
    _append_bridge_event(
        repo_root,
        event="command_started",
        payload={
            "request_identity_hash": request_identity_hash,
            "request_id": receipt["request_id"],
            "command_id": receipt["command_id"],
            "token_id": auth.token_id,
            "route": route_path,
        },
    )
    return receipt


def _store_command_finished(
    repo_root: Path,
    *,
    receipt: Dict[str, Any],
    status: str,
    http_status: int,
    response_payload: Dict[str, Any],
    artifact_refs: Sequence[str] = (),
    session_id: str = "",
) -> None:
    out = dict(receipt or {})
    out["status"] = _clean_text(status) or "finished"
    out["updated_utc"] = _utc_now_iso()
    out["http_status"] = int(http_status)
    out["response_payload"] = dict(response_payload or {})
    out["artifact_refs"] = _clean_text_list(list(artifact_refs or []))
    if session_id:
        out["session_id"] = _clean_text(session_id)
    _write_command_receipt(repo_root, out)
    _append_bridge_event(
        repo_root,
        event="command_finished",
        payload={
            "request_identity_hash": _clean_text(out.get("request_identity_hash")),
            "request_id": _clean_text(out.get("request_id")),
            "command_id": _clean_text(out.get("command_id")),
            "status": _clean_text(status),
            "http_status": int(http_status),
            "session_id": _clean_text(out.get("session_id")),
        },
    )


def _route_base_payload(*, route: str, auth: Optional[AuthenticatedToken] = None) -> Dict[str, Any]:
    payload = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "uid": BRIDGE_UID,
        "utc": _utc_now_iso(),
        "route": _clean_text(route),
    }
    if auth is not None:
        payload["token_id"] = auth.token_id
    return payload


def _boundary_error_payload(route: str, boundary_result: Dict[str, Any], *, auth: Optional[AuthenticatedToken] = None) -> Dict[str, Any]:
    payload = _route_base_payload(route=route, auth=auth)
    payload.update({
        "ok": False,
        "boundary_result": dict(boundary_result),
    })
    return payload


def _resume_duplicate_result(repo_root: Path, boundary_result: Dict[str, Any], *, route: str, auth: AuthenticatedToken) -> Tuple[int, Dict[str, Any]]:
    request_identity_hash = _clean_text(boundary_result.get("request_identity_hash"))
    receipt = _command_receipt(repo_root, request_identity_hash)
    if not receipt:
        payload = _route_base_payload(route=route, auth=auth)
        payload.update(
            {
                "ok": False,
                "status": "duplicate_receipt_missing",
                "boundary_result": dict(boundary_result),
                "summary": "duplicate request was suppressed, but no durable command receipt is available yet",
            }
        )
        return HTTPStatus.ACCEPTED, payload
    terminal = str(receipt.get("status") or "").strip().lower()
    if terminal in {"succeeded", "failed"}:
        stored_payload = receipt.get("response_payload") if isinstance(receipt.get("response_payload"), dict) else {}
        payload = dict(stored_payload)
        payload["duplicate_suppressed"] = True
        payload["boundary_result"] = dict(boundary_result)
        payload["replayed_from_receipt"] = True
        return int(receipt.get("http_status") or HTTPStatus.OK), payload
    payload = _route_base_payload(route=route, auth=auth)
    payload.update(
        {
            "ok": False,
            "status": terminal or "started",
            "boundary_result": dict(boundary_result),
            "summary": "duplicate request is already recorded and still in progress or not terminal",
            "receipt": {
                "request_identity_hash": request_identity_hash,
                "request_id": _clean_text(receipt.get("request_id")),
                "command_id": _clean_text(receipt.get("command_id")),
                "status": terminal or "started",
                "updated_utc": _clean_text(receipt.get("updated_utc")),
            },
        }
    )
    return HTTPStatus.ACCEPTED, payload


def _git_env() -> Dict[str, str]:
    env = os.environ.copy()
    env.setdefault("GIT_TERMINAL_PROMPT", "0")
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


def _run_subprocess(argv: Sequence[str], *, cwd: Path, timeout_s: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(argv),
        cwd=str(Path(cwd).resolve()),
        capture_output=True,
        text=True,
        timeout=int(timeout_s),
        check=False,
        env=_git_env(),
    )


def _run_git(repo_root: Path, argv: Sequence[str], *, cwd: Optional[Path] = None, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    workdir = Path(cwd or repo_root).resolve()
    return _run_subprocess(["git", *list(argv)], cwd=workdir, timeout_s=timeout_s)


def _resolve_commit(repo_root: Path, raw_ref: str) -> str:
    ref = str(raw_ref or "").strip()
    if not ref:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_base_commit", "base_commit_sha is required")
    cp = _run_git(repo_root, ["rev-parse", "--verify", f"{ref}^{{commit}}"], cwd=repo_root, timeout_s=30)
    if int(cp.returncode) != 0:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_base_commit", f"base commit does not resolve: {ref}")
    return str(cp.stdout or "").strip()


def _repo_has_unresolved_merges(repo_root: Path) -> bool:
    cp = _run_git(repo_root, ["diff", "--name-only", "--diff-filter=U"], cwd=repo_root, timeout_s=30)
    if int(cp.returncode) != 0:
        return False
    return bool(str(cp.stdout or "").strip())


def _git_head_sha(repo_root: Path, *, cwd: Optional[Path] = None) -> str:
    cp = _run_git(repo_root, ["rev-parse", "HEAD"], cwd=cwd or repo_root, timeout_s=30)
    return str(cp.stdout or "").strip() if int(cp.returncode) == 0 else ""


def _git_status_summary(repo_root: Path, *, cwd: Optional[Path] = None) -> Dict[str, Any]:
    cp = _run_git(repo_root, ["status", "--porcelain"], cwd=cwd or repo_root, timeout_s=30)
    rows = [str(line or "") for line in str(cp.stdout or "").splitlines() if str(line or "").strip()]
    return {
        "clean": not bool(rows),
        "entries": rows[:100],
        "truncated": len(rows) > 100,
    }


def _session_path(repo_root: Path, session_id: str) -> Path:
    return (_sessions_root(repo_root) / f"{_clean_text(session_id)}.json").resolve()


def _load_session(repo_root: Path, session_id: str) -> Dict[str, Any]:
    session = _read_json(_session_path(repo_root, session_id))
    if not session:
        raise BridgeRequestError(HTTPStatus.NOT_FOUND, "session_not_found", f"session not found: {session_id}")
    return session


def _save_session(repo_root: Path, session: Dict[str, Any]) -> Dict[str, Any]:
    path = _session_path(repo_root, _clean_text(session.get("session_id")))
    atomic_write_canonical_json(path, session)
    return session


def _session_expired(session: Dict[str, Any]) -> bool:
    expires_dt = _parse_iso_utc(session.get("expires_at"))
    return expires_dt is not None and expires_dt <= _utc_now()


def _expire_session(repo_root: Path, session: Dict[str, Any]) -> Dict[str, Any]:
    if _session_expired(session) and str(session.get("status") or "").strip().lower() == "open":
        session = dict(session)
        session["status"] = "expired"
        session["closed_reason"] = "expired"
        session["last_activity_at"] = _utc_now_iso()
        _save_session(repo_root, session)
    return session


def _active_open_sessions(repo_root: Path) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for path in sorted(_sessions_root(repo_root).glob("*.json")):
        row = _read_json(path)
        if not row:
            continue
        row = _expire_session(repo_root, row)
        if str(row.get("status") or "").strip().lower() == "open":
            out.append(row)
    return out


def _ensure_session_access(session: Dict[str, Any], auth: AuthenticatedToken, *, allow_expired_read: bool = False) -> Dict[str, Any]:
    token_id = _clean_text(session.get("token_id"))
    if token_id != auth.token_id:
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "forbidden_session", "session is not owned by this assistant token")
    if _session_expired(session):
        session = dict(session)
        session["status"] = "expired"
        session["closed_reason"] = str(session.get("closed_reason") or "expired") or "expired"
        if not allow_expired_read:
            raise BridgeRequestError(HTTPStatus.CONFLICT, "session_expired", "session is expired")
    return session


def _session_work_root(repo_root: Path, session: Dict[str, Any]) -> Path:
    mode = _clean_text(session.get("execution_mode")).lower()
    if mode == "live":
        return Path(repo_root).resolve()
    worktree_path = _clean_text(session.get("worktree_path"))
    if not worktree_path:
        raise BridgeRequestError(HTTPStatus.CONFLICT, "session_missing_worktree", "isolated session has no worktree path")
    return Path(worktree_path).resolve()


def _require_live_repo_write_scope(session: Dict[str, Any], auth: AuthenticatedToken) -> None:
    if _clean_text(session.get("execution_mode")).lower() == "live":
        _require_scope(auth, "repo.write.live")


def _list_session_run_ids(session: Dict[str, Any]) -> List[str]:
    return _clean_text_list(list(session.get("run_ids") or []))


def _append_session_run(repo_root: Path, session: Dict[str, Any], *, run_id: str, artifact_refs: Sequence[str]) -> Dict[str, Any]:
    out = dict(session)
    run_ids = _list_session_run_ids(out)
    if run_id not in run_ids:
        run_ids.append(run_id)
    out["run_ids"] = run_ids
    refs = _clean_text_list(list(out.get("artifact_refs") or []))
    for ref in list(artifact_refs or []):
        txt = str(ref or "").strip()
        if txt and txt not in refs:
            refs.append(txt)
    out["artifact_refs"] = refs
    out["last_activity_at"] = _utc_now_iso()
    _save_session(repo_root, out)
    return out


def _issue_session_mint_token(repo_root: Path, session: Dict[str, Any], auth: AuthenticatedToken, *, ttl_sec: int) -> Tuple[Dict[str, Any], str]:
    session_id = _clean_text(session.get("session_id"))
    if not session_id:
        raise BridgeRequestError(HTTPStatus.CONFLICT, "missing_session_id", "session_id is required")
    secret = secrets.token_urlsafe(24)
    raw_token = f"{_SESSION_MINT_PREFIX}_{session_id}.{secret}"
    expires_at = (_utc_now() + _dt.timedelta(seconds=int(ttl_sec))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    out = dict(session)
    out["session_mint_token_sha256"] = _sha256_text(raw_token)
    out["session_mint_expires_at"] = expires_at
    out["session_mint_issued_at"] = _utc_now_iso()
    _save_session(repo_root, out)
    _append_bridge_event(
        repo_root,
        event="session_mint_issued",
        payload={
            "session_id": session_id,
            "token_id": auth.token_id,
            "expires_at": expires_at,
        },
    )
    return out, raw_token


def _session_mint_payload(raw_token: str, session: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": BRIDGE_SESSION_MINT_SCHEMA_VERSION,
        "ok": True,
        "session_id": _clean_text(session.get("session_id")),
        "access_token": raw_token,
        "expires_at": _clean_text(session.get("session_mint_expires_at")),
        "mint_path": "/api/assistant/browser_relay/session_execute",
        "mint_query_contract": {
            "required": ["access_token", "operation"],
            "optional": ["payload_b64"],
            "notes": "payload_b64 is URL-safe base64 encoded UTF-8 JSON payload for the requested operation; session_id is injected automatically from the session mint token.",
        },
        "allowed_operations": ["write_file", "delete_file", "run_validation", "apply_diff", "artifact", "session_close"],
        "url_template": f"/api/assistant/browser_relay/session_execute?access_token={raw_token}&operation=<operation>&payload_b64=<urlsafe-base64-json>",
    }


def _decode_session_mint_payload(raw: str) -> Dict[str, Any]:
    text = str(raw or "").strip()
    if not text:
        return {}
    padded = text + ("=" * ((4 - (len(text) % 4)) % 4))
    try:
        decoded = base64.urlsafe_b64decode(padded.encode("utf-8"))
    except Exception as exc:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_payload_b64", "payload_b64 is not valid URL-safe base64") from exc
    try:
        obj = json.loads(decoded.decode("utf-8"))
    except Exception as exc:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_payload_json", "payload_b64 must decode to a JSON object") from exc
    if not isinstance(obj, dict):
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_payload_json", "payload_b64 must decode to a JSON object")
    return {str(k): v for k, v in obj.items()}


def _validate_session_mint_access_token(repo_root: Path, config: BridgeConfig, token_value: str) -> Tuple[Dict[str, Any], AuthenticatedToken]:
    raw = str(token_value or "").strip()
    if not raw.startswith(f"{_SESSION_MINT_PREFIX}_") or "." not in raw:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "invalid_session_mint_token", "missing or invalid session mint token")
    prefix, _, _secret = raw.partition(".")
    session_id = prefix[len(f"{_SESSION_MINT_PREFIX}_") :].strip()
    session = _load_session(repo_root, session_id)
    token_row = _token_row_by_id(_load_token_rows(repo_root, config), _clean_text(session.get("token_id")))
    if not token_row:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "session_mint_token_revoked", "source assistant token is no longer active")
    auth = _auth_from_token_row(repo_root, token_row)
    session = _ensure_session_access(session, auth, allow_expired_read=False)
    if str(session.get("status") or "").strip().lower() != "open":
        raise BridgeRequestError(HTTPStatus.CONFLICT, "session_not_open", "session is not open")
    expires_dt = _parse_iso_utc(session.get("session_mint_expires_at"))
    if expires_dt is not None and expires_dt <= _utc_now():
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "session_mint_expired", "session mint token is expired")
    expected_sha = _clean_text(session.get("session_mint_token_sha256"))
    if not expected_sha or not hmac.compare_digest(expected_sha, _sha256_text(raw)):
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "invalid_session_mint_token", "session mint token is invalid")
    return session, auth


def _session_target_path(repo_root: Path, session: Dict[str, Any], auth: AuthenticatedToken, raw_path: Any) -> Tuple[Path, str]:
    work_root = _session_work_root(repo_root, session)
    rel = _normalize_repo_relative_path(raw_path, field_name="path")
    _require_allowed_repo_relpath(rel, auth)
    if _is_protected_relpath(rel):
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "protected_path", f"path is protected: {rel}")
    target = _ledger_storage.safe_resolve_under(work_root, work_root / rel)
    if target is None:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_path", f"path escapes the session work root: {rel}")
    return target, rel


def _read_file_preview(path: Path, *, max_bytes: int) -> Dict[str, Any]:
    target = Path(path).resolve()
    if target.is_symlink() or not target.exists() or not target.is_file():
        raise BridgeRequestError(HTTPStatus.NOT_FOUND, "file_not_found", f"file not found: {target.name}")
    size_bytes = int(target.stat().st_size)
    read_limit = max(1, int(max_bytes))
    if size_bytes <= read_limit:
        data = target.read_bytes()
        truncated = False
    else:
        with open(target, "rb") as handle:
            data = handle.read(read_limit)
        truncated = True
    try:
        return {
            "encoding": "utf-8",
            "content_text": data.decode("utf-8"),
            "content_base64": "",
            "truncated": truncated,
            "size_bytes": size_bytes,
            "read_limit_bytes": read_limit,
        }
    except Exception:
        return {
            "encoding": "base64",
            "content_text": "",
            "content_base64": base64.b64encode(data).decode("ascii"),
            "truncated": truncated,
            "size_bytes": size_bytes,
            "read_limit_bytes": read_limit,
        }


def _run_state_dir(repo_root: Path, run_id: str) -> Path:
    path = (_runs_state_root(repo_root) / _clean_text(run_id)).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_artifact_dir(repo_root: Path, run_id: str) -> Path:
    path = (_artifacts_root(repo_root) / _clean_text(run_id)).resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_hidden_path(path: Path) -> bool:
    return any(part.startswith(".") for part in path.parts if part not in {".", ".."})


def _search_file_matches(path: Path, *, query: str, case_sensitive: bool) -> List[Dict[str, Any]]:
    try:
        if path.is_symlink() or not path.is_file():
            return []
        if int(path.stat().st_size) > _MAX_SEARCH_FILE_BYTES:
            return []
        sample = path.read_bytes()[:4096]
        if b"\x00" in sample:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []
    needle = query if case_sensitive else query.lower()
    out: List[Dict[str, Any]] = []
    for idx, raw_line in enumerate(text.splitlines(), start=1):
        hay = raw_line if case_sensitive else raw_line.lower()
        if needle in hay:
            snippet = raw_line.strip()
            if len(snippet) > _MAX_SEARCH_SNIPPET_CHARS:
                snippet = snippet[: _MAX_SEARCH_SNIPPET_CHARS - 1].rstrip() + "…"
            out.append({"line_start": idx, "line_end": idx, "preview": snippet})
    return out


def _repo_search(repo_root: Path, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "repo.read")
    query = str(payload.get("query") or "").strip()
    if not query:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_query", "query is required")
    case_sensitive = bool(payload.get("case_sensitive") is True)
    max_results = int(payload.get("max_results") or _DEFAULT_SEARCH_MAX_RESULTS)
    max_results = max(1, min(_MAX_SEARCH_RESULTS, max_results))
    requested_roots = _requested_search_roots(repo_root, auth, list(payload.get("roots") or []))
    matches: List[Dict[str, Any]] = []
    bytes_returned = 0
    truncated = False
    searched_roots = [_repo_relpath(repo_root, root) for root in requested_roots]
    for base in requested_roots:
        for cur_root, dirs, files in os.walk(base):
            current = Path(cur_root)
            dirs[:] = [
                name
                for name in dirs
                if name not in _SKIP_SEARCH_DIRS
                and not name.startswith(".")
                and _ledger_storage.safe_resolve_under(base, current / name) is not None
                and not _is_hidden_path((current / name).relative_to(base))
            ]
            if _repo_relpath(repo_root, current).startswith("runtime/state/assistant_bridge"):
                dirs[:] = []
                continue
            for name in files:
                candidate = current / name
                rel_from_base = candidate.relative_to(base)
                if name.startswith(".") or _is_hidden_path(rel_from_base):
                    continue
                file_matches = _search_file_matches(candidate, query=query, case_sensitive=case_sensitive)
                if not file_matches:
                    continue
                repo_rel = _repo_relpath(repo_root, candidate)
                for row in file_matches:
                    result = {
                        "path": repo_rel,
                        **row,
                    }
                    result_bytes = len(json.dumps(result, ensure_ascii=False))
                    if len(matches) >= max_results or bytes_returned + result_bytes > _MAX_SEARCH_TOTAL_BYTES:
                        truncated = True
                        break
                    matches.append(result)
                    bytes_returned += result_bytes
                if truncated:
                    break
            if truncated:
                break
        if truncated:
            break
    return {
        "ok": True,
        "query": query,
        "case_sensitive": case_sensitive,
        "roots": searched_roots,
        "max_results": max_results,
        "result_count": len(matches),
        "truncated": truncated,
        "results": matches,
        "binary_files_skipped": True,
        "hidden_paths_excluded": True,
    }


def _active_items_root(repo_root: Path) -> Path:
    return _ledger_storage.ledger_patch_instruction_sheets_root(Path(repo_root).resolve())


def _ledger_list_active(repo_root: Path, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "ledger.read")
    limit = int(payload.get("limit") or 50)
    limit = max(1, min(500, limit))
    uid_prefix = str(payload.get("uid_prefix") or "").strip()
    title_contains = str(payload.get("title_contains") or "").strip().lower()
    items: List[Dict[str, Any]] = []
    for item_dir in _ledger_storage.list_item_dirs(_active_items_root(repo_root)):
        meta = _ledger_storage.read_json(item_dir / "meta.json") or {}
        uid = str(meta.get("uid") or "").strip()
        if not uid:
            continue
        status = str(meta.get("status") or "").strip().lower()
        if status and status not in _ACTIVE_LEDGER_STATUSES:
            continue
        if uid_prefix and not uid.startswith(uid_prefix):
            continue
        if not _allowed_pis(uid, auth):
            continue
        title = str(meta.get("title") or uid).strip()
        if title_contains and title_contains not in title.lower():
            continue
        items.append(
            {
                "uid": uid,
                "slug": str(meta.get("slug") or "").strip(),
                "title": title,
                "status": str(meta.get("status") or "").strip(),
                "release_state": str(meta.get("release_state") or "").strip(),
                "created_utc": str(meta.get("created_utc") or "").strip(),
                "updated_utc": str(meta.get("updated_utc") or "").strip(),
                "path": _repo_relpath(repo_root, item_dir),
                "tags": _clean_text_list(list(meta.get("tags") or [])),
                "topic_tags": _clean_text_list(list(meta.get("topic_tags") or [])),
            }
        )
    items.sort(key=lambda row: (str(row.get("updated_utc") or ""), str(row.get("uid") or "")), reverse=True)
    truncated = len(items) > limit
    return {
        "ok": True,
        "limit": limit,
        "count": min(limit, len(items)),
        "truncated": truncated,
        "items": items[:limit],
    }


def _ledger_read_item(repo_root: Path, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "ledger.read")
    uid = str(payload.get("uid") or "").strip()
    if not uid:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_uid", "uid is required")
    if not _allowed_pis(uid, auth):
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "forbidden_pis_uid", f"token cannot read PIS UID: {uid}")
    item_dir = _ledger_storage.item_dir_for_uid(_active_items_root(repo_root), uid)
    if item_dir is None:
        raise BridgeRequestError(HTTPStatus.NOT_FOUND, "ledger_item_not_found", f"active ledger item not found: {uid}")
    meta = _ledger_storage.read_json(item_dir / "meta.json") or {}
    pis_text = (item_dir / "pis.md").read_text(encoding="utf-8", errors="replace") if (item_dir / "pis.md").exists() else ""
    include_events = bool(payload.get("include_events") is True)
    events_limit = int(payload.get("events_limit") or 50)
    events_limit = max(1, min(500, events_limit))
    events_rows: List[Dict[str, Any]] = []
    if include_events:
        events_rows = _read_jsonl(item_dir / "events.jsonl", limit=events_limit)
    return {
        "ok": True,
        "uid": uid,
        "path": _repo_relpath(repo_root, item_dir),
        "meta": meta,
        "pis_markdown": pis_text,
        "events": events_rows,
        "events_truncated": include_events and ((item_dir / "events.jsonl").exists() and len(events_rows) == events_limit),
    }


def _file_read(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    preview = _read_bound_path(repo_root, config, auth, payload)
    return {
        "ok": True,
        "path": str(preview.get("path") or ""),
        "source": str(preview.get("source") or ""),
        "size_bytes": int(preview.get("size_bytes") or 0),
        "encoding": str(preview.get("encoding") or ""),
        "truncated": bool(preview.get("truncated") is True),
        "content_text": str(preview.get("content_text") or ""),
        "content_base64": str(preview.get("content_base64") or ""),
        "read_limit_bytes": int(preview.get("read_limit_bytes") or 0),
    }


def _open_isolated_worktree(repo_root: Path, *, session_id: str, base_commit_sha: str) -> Path:
    worktree = (_worktrees_root(repo_root) / session_id).resolve()
    if worktree.exists():
        shutil.rmtree(worktree)
    cp = _run_git(repo_root, ["worktree", "add", "--detach", str(worktree), str(base_commit_sha)], cwd=repo_root, timeout_s=180)
    if int(cp.returncode) != 0:
        raise BridgeRequestError(
            HTTPStatus.BAD_REQUEST,
            "worktree_open_failed",
            str(cp.stderr or cp.stdout or "git worktree add failed").strip(),
        )
    return worktree


def _cleanup_worktree(repo_root: Path, path: Path) -> Dict[str, Any]:
    target = Path(path).resolve()
    if not target.exists():
        return {"path": str(target), "ok": True, "removed": False, "method": "missing", "error": ""}
    cp = _run_git(repo_root, ["worktree", "remove", "--force", str(target)], cwd=repo_root, timeout_s=180)
    if int(cp.returncode) == 0:
        return {"path": str(target), "ok": True, "removed": True, "method": "git_worktree_remove", "error": ""}
    try:
        shutil.rmtree(target)
    except Exception as exc:
        return {"path": str(target), "ok": False, "removed": False, "method": "failed", "error": str(exc)}
    return {"path": str(target), "ok": True, "removed": True, "method": "shutil_rmtree", "error": ""}


def _open_execution_session(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, payload: Dict[str, Any], *, route_path: str) -> Dict[str, Any]:
    _require_scope(auth, "session.open")
    if _repo_has_unresolved_merges(repo_root):
        raise BridgeRequestError(HTTPStatus.CONFLICT, "unresolved_merges", "session open blocked because the repo has unresolved merges")
    if len(_active_open_sessions(repo_root)) >= int(config.max_sessions):
        raise BridgeRequestError(HTTPStatus.CONFLICT, "session_limit_reached", "assistant bridge session limit reached")
    base_commit_sha = _resolve_commit(repo_root, str(payload.get("base_commit_sha") or ""))
    execution_mode = str(payload.get("execution_mode") or config.default_execution_mode or "isolated").strip().lower()
    if execution_mode not in {"isolated", "live"}:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_execution_mode", "execution_mode must be isolated or live")
    if execution_mode == "live":
        if not bool(config.allow_live_mode):
            raise BridgeRequestError(HTTPStatus.FORBIDDEN, "live_mode_disabled", "live mode is disabled by config")
        _require_scope(auth, "repo.write.live")
        if not _git_status_summary(repo_root, cwd=repo_root).get("clean"):
            raise BridgeRequestError(HTTPStatus.CONFLICT, "live_repo_not_clean", "live mode requires a clean repo working tree")
    session_id = f"assistant_session_{_compact_utc()}_{uuid.uuid4().hex[:8]}"
    created_at = _utc_now_iso()
    expires_at = (_utc_now() + _dt.timedelta(seconds=int(config.session_ttl_sec))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    worktree_path = ""
    live_reason = ""
    if execution_mode == "isolated":
        worktree_path = str(_open_isolated_worktree(repo_root, session_id=session_id, base_commit_sha=base_commit_sha))
    else:
        live_reason = str(payload.get("live_mode_reason") or payload.get("reason") or "").strip()
        if not live_reason:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_live_mode_reason", "live mode requires live_mode_reason")
    session = {
        "schema_version": BRIDGE_SESSION_SCHEMA_VERSION,
        "uid": BRIDGE_UID,
        "session_id": session_id,
        "execution_mode": execution_mode,
        "repo_root": str(Path(repo_root).resolve()),
        "base_commit_sha": base_commit_sha,
        "worktree_path": worktree_path,
        "created_at": created_at,
        "last_activity_at": created_at,
        "expires_at": expires_at,
        "created_from_route": _clean_text(route_path),
        "intent_classification": "isolated_trial",
        "token_id": auth.token_id,
        "bridge_schema_version": BRIDGE_SCHEMA_VERSION,
        "status": "open",
        "closed_reason": "",
        "run_ids": [],
        "artifact_refs": [],
        "allowed_repo_roots": list(auth.allowed_repo_roots or (".",)),
        "live_mode_reason": live_reason,
    }
    _save_session(repo_root, session)
    _append_bridge_event(
        repo_root,
        event="session_opened",
        payload={
            "session_id": session_id,
            "execution_mode": execution_mode,
            "token_id": auth.token_id,
            "base_commit_sha": base_commit_sha,
            "worktree_path": worktree_path,
        },
    )
    session, session_mint_token = _issue_session_mint_token(
        repo_root,
        session,
        auth,
        ttl_sec=min(int(config.browser_relay_ttl_sec), int(config.session_ttl_sec)),
    )
    return {
        "ok": True,
        "session": session,
        "session_mint": _session_mint_payload(session_mint_token, session),
    }


_PATCH_DIFF_RE = re.compile(r"^diff --git a/(?P<a>.+?) b/(?P<b>.+)$")
_PATCH_FORBIDDEN_RE = re.compile(r"^(?:rename from|rename to|copy from|copy to)\b", re.IGNORECASE)


def _parse_patch_changed_files(diff_text: str) -> List[str]:
    if not str(diff_text or "").strip():
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "empty_diff", "diff_text is required")
    out: List[str] = []
    for raw_line in str(diff_text or "").splitlines():
        line = str(raw_line or "")
        if _PATCH_FORBIDDEN_RE.match(line.strip()):
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "unsupported_patch_feature", "rename/copy patch headers are not supported in v1")
        match = _PATCH_DIFF_RE.match(line)
        if not match:
            continue
        a_path = match.group("a").strip()
        b_path = match.group("b").strip()
        chosen = b_path if b_path != "/dev/null" else a_path
        rel = chosen.strip().lstrip("./")
        if rel and rel not in out:
            out.append(rel)
    if not out:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "diff_missing_paths", "diff does not declare changed files")
    return out


def _patch_deletes_files(diff_text: str) -> bool:
    for raw_line in str(diff_text or "").splitlines():
        line = str(raw_line or "").strip().lower()
        if line.startswith("deleted file mode") or line == "+++ /dev/null":
            return True
    return False


def _validate_patch_targets(changed_files: Sequence[str], *, expected_changed_files: Sequence[Any], auth: AuthenticatedToken) -> List[str]:
    allowlist = _clean_text_list(list(expected_changed_files or []))
    if not allowlist:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_expected_changed_files", "expected_changed_files allowlist is required")
    rejected: List[str] = []
    for rel in list(changed_files or []):
        norm = _normalize_repo_relative_path(rel, field_name="path")
        if _is_protected_relpath(norm) or norm not in allowlist or not _repo_relpath_allowed_by_token(norm, auth):
            rejected.append(norm)
    if rejected:
        raise BridgeRequestError(
            HTTPStatus.BAD_REQUEST,
            "unexpected_changed_files",
            "patch touches unexpected or protected files",
            details={"rejected_files": rejected, "allowlist": allowlist},
        )
    return allowlist


def _apply_diff(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "diff.apply")
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
    session = _ensure_session_access(_load_session(repo_root, session_id), auth)
    _require_live_repo_write_scope(session, auth)
    if str(session.get("status") or "") != "open":
        raise BridgeRequestError(HTTPStatus.CONFLICT, "session_not_open", "session is not open")
    work_root = _session_work_root(repo_root, session)
    if not work_root.exists():
        raise BridgeRequestError(HTTPStatus.CONFLICT, "work_root_missing", "session work root is missing")
    base_commit_sha = _resolve_commit(repo_root, str(payload.get("base_commit_sha") or ""))
    if base_commit_sha != str(session.get("base_commit_sha") or ""):
        raise BridgeRequestError(HTTPStatus.CONFLICT, "base_commit_mismatch", "base_commit_sha does not match the session base commit")
    diff_text = str(payload.get("diff_text") or "")
    diff_bytes = diff_text.encode("utf-8")
    if len(diff_bytes) > int(config.max_diff_bytes):
        raise BridgeRequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "diff_too_large", "diff exceeds assistant bridge size limit")
    diff_hash = str(payload.get("diff_hash") or "").strip().lower()
    calculated_hash = _sha256_bytes(diff_bytes)
    if not diff_hash or diff_hash != calculated_hash:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "diff_hash_mismatch", "diff_hash does not match diff_text")
    if b"\x00" in diff_bytes or "GIT binary patch" in diff_text or "Binary files " in diff_text:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "binary_patch_rejected", "binary patches are not supported in v1")
    changed_files = _parse_patch_changed_files(diff_text)
    if _patch_deletes_files(diff_text) and payload.get("allow_file_deletions") is not True:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "patch_file_deletion_rejected", "file deletion patches require allow_file_deletions=true")
    allowlist = _validate_patch_targets(changed_files, expected_changed_files=list(payload.get("expected_changed_files") or []), auth=auth)
    run_id = f"assistant_apply_{_compact_utc()}_{uuid.uuid4().hex[:8]}"
    run_state_dir = _run_state_dir(repo_root, run_id)
    run_artifact_dir = _run_artifact_dir(repo_root, run_id)
    patch_state_path = (run_state_dir / "apply.patch").resolve()
    patch_artifact_path = (run_artifact_dir / "apply.patch").resolve()
    patch_state_path.write_text(diff_text, encoding="utf-8")
    patch_artifact_path.write_text(diff_text, encoding="utf-8")
    pre_apply_head_sha = _git_head_sha(repo_root, cwd=work_root)
    pre_apply_status = _git_status_summary(repo_root, cwd=work_root)
    check_cp = _run_git(repo_root, ["apply", "--check", "--whitespace=nowarn", str(patch_state_path)], cwd=work_root, timeout_s=120)
    if int(check_cp.returncode) != 0:
        raise BridgeRequestError(
            HTTPStatus.BAD_REQUEST,
            "git_apply_check_failed",
            str(check_cp.stderr or check_cp.stdout or "git apply --check failed").strip(),
            details={"rejected_files": changed_files},
        )
    apply_cp = _run_git(repo_root, ["apply", "--whitespace=nowarn", str(patch_state_path)], cwd=work_root, timeout_s=120)
    if int(apply_cp.returncode) != 0:
        raise BridgeRequestError(
            HTTPStatus.BAD_REQUEST,
            "git_apply_failed",
            str(apply_cp.stderr or apply_cp.stdout or "git apply failed").strip(),
            details={"rejected_files": changed_files},
        )
    post_apply_head_sha = _git_head_sha(repo_root, cwd=work_root)
    post_apply_status = _git_status_summary(repo_root, cwd=work_root)
    result = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": session_id,
        "execution_mode": str(session.get("execution_mode") or ""),
        "base_commit_sha": base_commit_sha,
        "diff_hash": calculated_hash,
        "payload_hash": _json_hash(payload),
        "changed_files": changed_files,
        "expected_changed_files": allowlist,
        "pre_apply_head_sha": pre_apply_head_sha,
        "post_apply_head_sha": post_apply_head_sha,
        "pre_apply_status": pre_apply_status,
        "post_apply_status": post_apply_status,
        "rejected_files": [],
        "artifact_refs": [
            _repo_relpath(repo_root, patch_state_path),
            _repo_relpath(repo_root, patch_artifact_path),
        ],
    }
    atomic_write_canonical_json((run_state_dir / "apply_result.json").resolve(), result)
    atomic_write_canonical_json((run_artifact_dir / "apply_result.json").resolve(), result)
    session = _append_session_run(repo_root, session, run_id=run_id, artifact_refs=result["artifact_refs"] + [
        _repo_relpath(repo_root, (run_state_dir / "apply_result.json").resolve()),
        _repo_relpath(repo_root, (run_artifact_dir / "apply_result.json").resolve()),
    ])
    return {
        "ok": True,
        "session_id": session_id,
        "run_id": run_id,
        "execution_mode": str(session.get("execution_mode") or ""),
        "changed_files": changed_files,
        "artifact_refs": list(session.get("artifact_refs") or []),
        "apply_result": result,
    }


def _write_file(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "repo.write")
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
    session = _ensure_session_access(_load_session(repo_root, session_id), auth)
    _require_live_repo_write_scope(session, auth)
    if str(session.get("status") or "") != "open":
        raise BridgeRequestError(HTTPStatus.CONFLICT, "session_not_open", "session is not open")
    target, rel = _session_target_path(repo_root, session, auth, payload.get("path"))
    content = payload.get("content")
    if not isinstance(content, str):
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_content", "content must be a text string")
    if "\x00" in content:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "binary_content_rejected", "text-only writes are supported in v1")
    mode = str(payload.get("mode") or "overwrite").strip().lower()
    if mode not in {"create", "overwrite", "append"}:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_write_mode", "mode must be create, overwrite, or append")
    if target.exists() and target.is_symlink():
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_target", f"write target must not be a symlink: {rel}")
    if target.exists() and not target.is_file():
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_target", f"write target must be a file: {rel}")
    data = content.encode("utf-8")
    if len(data) > int(config.max_write_file_bytes):
        raise BridgeRequestError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "write_file_too_large",
            f"write content exceeds size limit ({len(data)} bytes > {int(config.max_write_file_bytes)} bytes)",
        )
    existed_before = target.exists()
    existing_bytes = b""
    if mode == "append" and existed_before:
        existing_bytes = target.read_bytes()
    if mode == "create" and existed_before:
        raise BridgeRequestError(HTTPStatus.CONFLICT, "file_already_exists", f"file already exists: {rel}")
    final_bytes = existing_bytes + data if mode == "append" else data
    if len(final_bytes) > int(config.max_write_file_bytes):
        raise BridgeRequestError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "write_file_too_large",
            f"resulting file exceeds size limit ({len(final_bytes)} bytes > {int(config.max_write_file_bytes)} bytes)",
        )
    target.parent.mkdir(parents=True, exist_ok=True)
    if mode == "append":
        with open(target, "ab") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    else:
        tmp = target.parent / f".{target.name}.assistant_bridge.tmp"
        with open(tmp, "wb") as handle:
            handle.write(final_bytes)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
    run_id = f"assistant_write_{_compact_utc()}_{uuid.uuid4().hex[:8]}"
    run_state_dir = _run_state_dir(repo_root, run_id)
    run_artifact_dir = _run_artifact_dir(repo_root, run_id)
    modified_utc = _utc_now_iso()
    result = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": session_id,
        "execution_mode": str(session.get("execution_mode") or ""),
        "path": rel,
        "mode": mode,
        "bytes_written": len(data),
        "size_bytes": len(final_bytes),
        "sha256": _sha256_bytes(final_bytes),
        "modified_utc": modified_utc,
        "created": not existed_before,
        "existed_before": existed_before,
        "artifact_refs": [],
    }
    atomic_write_canonical_json((run_state_dir / "write_result.json").resolve(), result)
    atomic_write_canonical_json((run_artifact_dir / "write_result.json").resolve(), result)
    artifact_refs = [
        _repo_relpath(repo_root, (run_state_dir / "write_result.json").resolve()),
        _repo_relpath(repo_root, (run_artifact_dir / "write_result.json").resolve()),
    ]
    result["artifact_refs"] = artifact_refs
    atomic_write_canonical_json((run_state_dir / "write_result.json").resolve(), result)
    atomic_write_canonical_json((run_artifact_dir / "write_result.json").resolve(), result)
    session = _append_session_run(repo_root, session, run_id=run_id, artifact_refs=artifact_refs)
    return {
        "ok": True,
        "session_id": session_id,
        "run_id": run_id,
        "path": rel,
        "bytes_written": len(data),
        "artifact_refs": list(session.get("artifact_refs") or []),
        "write_result": result,
    }


def _delete_file(repo_root: Path, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "repo.write")
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
    session = _ensure_session_access(_load_session(repo_root, session_id), auth)
    _require_live_repo_write_scope(session, auth)
    if str(session.get("status") or "") != "open":
        raise BridgeRequestError(HTTPStatus.CONFLICT, "session_not_open", "session is not open")
    target, rel = _session_target_path(repo_root, session, auth, payload.get("path"))
    if target.exists() and target.is_symlink():
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_target", f"delete target must not be a symlink: {rel}")
    if target.exists() and target.is_dir():
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "delete_directory_forbidden", f"delete target must be a file: {rel}")
    existed = target.exists()
    if existed:
        target.unlink()
    run_id = f"assistant_delete_{_compact_utc()}_{uuid.uuid4().hex[:8]}"
    run_state_dir = _run_state_dir(repo_root, run_id)
    run_artifact_dir = _run_artifact_dir(repo_root, run_id)
    deleted_utc = _utc_now_iso()
    result = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": session_id,
        "execution_mode": str(session.get("execution_mode") or ""),
        "path": rel,
        "deleted": existed,
        "existed": existed,
        "deleted_utc": deleted_utc,
        "artifact_refs": [],
    }
    atomic_write_canonical_json((run_state_dir / "delete_result.json").resolve(), result)
    atomic_write_canonical_json((run_artifact_dir / "delete_result.json").resolve(), result)
    artifact_refs = [
        _repo_relpath(repo_root, (run_state_dir / "delete_result.json").resolve()),
        _repo_relpath(repo_root, (run_artifact_dir / "delete_result.json").resolve()),
    ]
    result["artifact_refs"] = artifact_refs
    atomic_write_canonical_json((run_state_dir / "delete_result.json").resolve(), result)
    atomic_write_canonical_json((run_artifact_dir / "delete_result.json").resolve(), result)
    session = _append_session_run(repo_root, session, run_id=run_id, artifact_refs=artifact_refs)
    return {
        "ok": True,
        "session_id": session_id,
        "run_id": run_id,
        "path": rel,
        "deleted": existed,
        "artifact_refs": list(session.get("artifact_refs") or []),
        "delete_result": result,
    }


def _validated_relative_paths(repo_root: Path, work_root: Path, paths: Sequence[Any]) -> List[str]:
    _ = repo_root
    base = Path(work_root).resolve()
    out: List[str] = []
    for raw in list(paths or []):
        rel = str(raw or "").strip().lstrip("./")
        if not rel or rel.startswith("/") or ".." in Path(rel).parts:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_validation_path", f"invalid validation path: {raw}")
        target = _ledger_storage.safe_resolve_under(base, base / rel)
        if target is None or not target.exists() or not target.is_file():
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_validation_path", f"validation path does not exist: {rel}")
        work_rel = _repo_relpath(base, target)
        if work_rel.startswith("runtime/state/assistant_bridge/") or work_rel.startswith("runtime/artifacts/assistant_bridge/"):
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_validation_path", f"bridge runtime files are not valid targets: {rel}")
        if _is_protected_relpath(work_rel):
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_validation_path", f"protected validation target is not allowed: {rel}")
        out.append(work_rel)
    return out


def _safe_git_argument_paths(paths: Sequence[Any]) -> List[str]:
    out: List[str] = []
    for raw in list(paths or []):
        rel = _normalize_repo_relative_path(raw, field_name="path")
        if _is_protected_relpath(rel):
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_validation_path", f"protected validation target is not allowed: {rel}")
        out.append(rel)
    return out


def _scrubbed_env() -> Dict[str, str]:
    allowed = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "LANG": os.environ.get("LANG", "C.UTF-8"),
        "LC_ALL": os.environ.get("LC_ALL", "C.UTF-8"),
        "PYTHONUNBUFFERED": "1",
        "PYTHONDONTWRITEBYTECODE": "1",
        "GIT_TERMINAL_PROMPT": "0",
    }
    return {key: str(value) for key, value in allowed.items() if str(value) != ""}


def _preview_text(text: str, *, limit: int) -> Tuple[str, bool]:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= int(limit):
        return text, False
    preview_bytes = encoded[: int(limit)]
    preview = preview_bytes.decode("utf-8", errors="replace")
    return preview, True


def _validate_session(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "validation.run")
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
    session = _ensure_session_access(_load_session(repo_root, session_id), auth)
    if str(session.get("status") or "") != "open":
        raise BridgeRequestError(HTTPStatus.CONFLICT, "session_not_open", "session is not open")
    work_root = _session_work_root(repo_root, session)
    validation = payload.get("validation") if isinstance(payload.get("validation"), dict) else {}
    kind = str(validation.get("kind") or "").strip()
    if kind not in _ALLOWED_VALIDATION_KINDS:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "validation_not_allowlisted", f"validation kind is not allowlisted: {kind}")
    timeout_s = int(validation.get("timeout_sec") or validation.get("timeout_s") or 600)
    timeout_s = max(1, min(timeout_s, 3600))
    argv: List[str] = []
    if kind == "py_compile":
        rel_paths = _validated_relative_paths(repo_root, work_root, list(validation.get("paths") or []))
        if not rel_paths:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_validation_paths", "py_compile validation requires paths")
        argv = [sys.executable, "-m", "py_compile", *rel_paths]
    elif kind == "pytest":
        selector = str(validation.get("selector") or "").strip()
        if selector:
            if selector.startswith("-") or "\n" in selector or selector.startswith("/") or ".." in selector:
                raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_pytest_selector", "pytest selector must be one safe repo-relative selector")
            selectors = [selector]
        else:
            selectors = _validated_relative_paths(repo_root, work_root, list(validation.get("paths") or []))
        if not selectors:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_validation_paths", "pytest validation requires selector or paths")
        argv = [sys.executable, "-m", "pytest", "-q", *selectors]
    elif kind == "git_status":
        rel_paths = _safe_git_argument_paths(list(validation.get("paths") or []))
        argv = ["git", "status", "--porcelain", "--untracked-files=normal"]
        if rel_paths:
            argv.extend(["--", *rel_paths])
    elif kind == "git_diff":
        rel_paths = _safe_git_argument_paths(list(validation.get("paths") or []))
        argv = ["git", "diff", "--no-ext-diff", "--"]
        if rel_paths:
            argv.extend(rel_paths)
    run_id = f"assistant_validate_{_compact_utc()}_{uuid.uuid4().hex[:8]}"
    run_state_dir = _run_state_dir(repo_root, run_id)
    run_artifact_dir = _run_artifact_dir(repo_root, run_id)
    stdout_path = (run_artifact_dir / "stdout.txt").resolve()
    stderr_path = (run_artifact_dir / "stderr.txt").resolve()
    started_utc = _utc_now_iso()
    started_at = time.monotonic()
    proc = subprocess.run(
        argv,
        cwd=str(work_root),
        capture_output=True,
        text=True,
        timeout=timeout_s,
        check=False,
        env=_scrubbed_env(),
    )
    duration_ms = max(0, int(round((time.monotonic() - started_at) * 1000.0)))
    stdout_text = str(proc.stdout or "")
    stderr_text = str(proc.stderr or "")
    stdout_path.write_text(stdout_text, encoding="utf-8")
    stderr_path.write_text(stderr_text, encoding="utf-8")
    stdout_preview, stdout_truncated = _preview_text(stdout_text, limit=int(config.max_stdout_bytes))
    stderr_preview, stderr_truncated = _preview_text(stderr_text, limit=int(config.max_stderr_bytes))
    result = {
        "schema_version": BRIDGE_SCHEMA_VERSION,
        "run_id": run_id,
        "session_id": session_id,
        "kind": kind,
        "command": argv,
        "started_utc": started_utc,
        "finished_utc": _utc_now_iso(),
        "duration_ms": duration_ms,
        "exit_code": int(proc.returncode),
        "stdout_preview": stdout_preview,
        "stderr_preview": stderr_preview,
        "stdout_truncated": stdout_truncated,
        "stderr_truncated": stderr_truncated,
        "network_policy": "no_bridge_grant",
        "artifact_refs": [
            _repo_relpath(repo_root, stdout_path),
            _repo_relpath(repo_root, stderr_path),
        ],
    }
    atomic_write_canonical_json((run_state_dir / "validation_result.json").resolve(), result)
    atomic_write_canonical_json((run_artifact_dir / "validation_result.json").resolve(), result)
    artifact_refs = list(result["artifact_refs"]) + [
        _repo_relpath(repo_root, (run_state_dir / "validation_result.json").resolve()),
        _repo_relpath(repo_root, (run_artifact_dir / "validation_result.json").resolve()),
    ]
    session = _append_session_run(repo_root, session, run_id=run_id, artifact_refs=artifact_refs)
    return {
        "ok": int(proc.returncode) == 0,
        "session_id": session_id,
        "run_id": run_id,
        "validation_result": result,
        "artifact_refs": list(session.get("artifact_refs") or []),
    }


def _list_run_artifacts(repo_root: Path, run_id: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for base in (_run_state_dir(repo_root, run_id), _run_artifact_dir(repo_root, run_id)):
        for path in sorted(base.rglob("*")):
            if path.is_symlink() or not path.is_file():
                continue
            out.append(
                {
                    "path": _repo_relpath(repo_root, path),
                    "size_bytes": int(path.stat().st_size),
                    "source": "state" if str(base).startswith(str(_runs_state_root(repo_root))) else "artifact",
                }
            )
    return out


def _list_artifacts(repo_root: Path, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "artifacts.read")
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
    session = _ensure_session_access(_load_session(repo_root, session_id), auth, allow_expired_read=True)
    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        if run_id not in _list_session_run_ids(session):
            raise BridgeRequestError(HTTPStatus.FORBIDDEN, "forbidden_run", "run is not owned by this session")
        artifacts = _list_run_artifacts(repo_root, run_id)
        return {
            "ok": True,
            "session_id": session_id,
            "run_id": run_id,
            "artifacts": artifacts,
        }
    artifact_refs = _clean_text_list(list(session.get("artifact_refs") or []))
    artifacts: List[Dict[str, Any]] = []
    for ref in artifact_refs:
        target = _ledger_storage.safe_resolve_under(Path(repo_root).resolve(), Path(repo_root).resolve() / ref)
        if target is None or not target.exists() or not target.is_file():
            continue
        artifacts.append({"path": ref, "size_bytes": int(target.stat().st_size)})
    return {
        "ok": True,
        "session_id": session_id,
        "run_id": "",
        "artifacts": artifacts,
    }


def _read_artifact(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "artifacts.read")
    session_id = str(payload.get("session_id") or "").strip()
    artifact_ref = str(payload.get("artifact_ref") or "").strip().lstrip("./")
    if not session_id:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
    if not artifact_ref:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_artifact_ref", "artifact_ref is required")
    session = _ensure_session_access(_load_session(repo_root, session_id), auth, allow_expired_read=True)
    allowed_refs = set(_clean_text_list(list(session.get("artifact_refs") or [])))
    for run_id in _list_session_run_ids(session):
        for row in _list_run_artifacts(repo_root, run_id):
            allowed_refs.add(str(row.get("path") or ""))
    if artifact_ref not in allowed_refs:
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "forbidden_artifact", "artifact is not owned by this session")
    target = _ledger_storage.safe_resolve_under(Path(repo_root).resolve(), Path(repo_root).resolve() / artifact_ref)
    if target is None or not target.exists() or not target.is_file():
        raise BridgeRequestError(HTTPStatus.NOT_FOUND, "artifact_not_found", f"artifact not found: {artifact_ref}")
    preview = _read_file_preview(target, max_bytes=int(config.max_artifact_read_bytes))
    return {
        "ok": True,
        "session_id": session_id,
        "artifact_ref": artifact_ref,
        **preview,
    }


def _read_bound_path(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    rel = _normalize_repo_relative_path(payload.get("path"), field_name="path")
    session_id = str(payload.get("session_id") or "").strip()
    if session_id:
        session = _ensure_session_access(_load_session(repo_root, session_id), auth, allow_expired_read=True)
        allowed_refs = set(_clean_text_list(list(session.get("artifact_refs") or [])))
        for run_id in _list_session_run_ids(session):
            for row in _list_run_artifacts(repo_root, run_id):
                allowed_refs.add(str(row.get("path") or ""))
        if rel in allowed_refs:
            _require_scope(auth, "artifacts.read")
            return _read_artifact(repo_root, config, auth, {"session_id": session_id, "artifact_ref": rel})
        _require_scope(auth, "repo.read")
        if _is_protected_read_relpath(rel):
            raise BridgeRequestError(HTTPStatus.FORBIDDEN, "protected_path", f"path is protected: {rel}")
        _require_allowed_repo_relpath(rel, auth)
        work_root = _session_work_root(repo_root, session)
        target = _ledger_storage.safe_resolve_under(work_root, work_root / rel)
        if target is None or not target.exists() or not target.is_file():
            raise BridgeRequestError(HTTPStatus.NOT_FOUND, "file_not_found", f"file not found: {rel}")
        preview = _read_file_preview(target, max_bytes=int(config.max_artifact_read_bytes))
        return {
            "ok": True,
            "session_id": session_id,
            "path": rel,
            "source": "session_file",
            **preview,
        }
    _require_scope(auth, "repo.read")
    if _is_protected_read_relpath(rel):
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "protected_path", f"path is protected: {rel}")
    _require_allowed_repo_relpath(rel, auth)
    target = _ledger_storage.safe_resolve_under(Path(repo_root).resolve(), Path(repo_root).resolve() / rel)
    if target is None or not target.exists() or not target.is_file():
        raise BridgeRequestError(HTTPStatus.NOT_FOUND, "file_not_found", f"file not found: {rel}")
    preview = _read_file_preview(target, max_bytes=int(config.max_artifact_read_bytes))
    return {
        "ok": True,
        "session_id": "",
        "path": rel,
        "source": "repo_file",
        **preview,
    }


def _close_session(repo_root: Path, auth: AuthenticatedToken, payload: Dict[str, Any]) -> Dict[str, Any]:
    _require_scope(auth, "session.open")
    session_id = str(payload.get("session_id") or "").strip()
    if not session_id:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
    session = _ensure_session_access(_load_session(repo_root, session_id), auth, allow_expired_read=True)
    cleanup = {"ok": True, "removed": False, "method": "n/a", "path": "", "error": ""}
    if str(session.get("execution_mode") or "").strip().lower() == "isolated":
        raw_worktree = str(session.get("worktree_path") or "").strip()
        if raw_worktree:
            cleanup = _cleanup_worktree(repo_root, Path(raw_worktree))
    out = dict(session)
    out["status"] = "closed"
    out["closed_reason"] = str(payload.get("closed_reason") or "client_request").strip() or "client_request"
    out["last_activity_at"] = _utc_now_iso()
    _save_session(repo_root, out)
    _append_bridge_event(
        repo_root,
        event="session_closed",
        payload={
            "session_id": session_id,
            "token_id": auth.token_id,
            "cleanup": cleanup,
            "closed_reason": out["closed_reason"],
        },
    )
    return {
        "ok": True,
        "session": out,
        "cleanup": cleanup,
    }


def _execute_bridge_command(repo_root: Path, config: BridgeConfig, auth: AuthenticatedToken, command_id: str, payload: Dict[str, Any], *, route_path: str) -> Dict[str, Any]:
    root = Path(repo_root).resolve()
    if command_id == "assistant.repo_search":
        return _repo_search(root, auth, payload)
    if command_id == "assistant.ledger_list_active":
        return _ledger_list_active(root, auth, payload)
    if command_id == "assistant.ledger_read_item":
        return _ledger_read_item(root, auth, payload)
    if command_id == "assistant.file_read":
        return _file_read(root, config, auth, payload)
    if command_id == "assistant.pis_improvement_trigger":
        _require_scope(auth, "ledger.read")
        _require_scope(auth, "session.open")
        request = payload.get("request")
        if not isinstance(request, Mapping):
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_request", "request object is required")
        try:
            return _pis_improvement_entrypoints.run_request(dict(request), repo_root=root)
        except ValueError as exc:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_pis_improvement_request", str(exc)) from exc
    if command_id == "assistant.execution_session_open":
        return _open_execution_session(root, config, auth, payload, route_path=route_path)
    if command_id == "assistant.execution_session_apply_diff":
        return _apply_diff(root, config, auth, payload)
    if command_id == "assistant.execution_session_validate":
        return _validate_session(root, config, auth, payload)
    if command_id == "assistant.execution_session_list_artifacts":
        return _list_artifacts(root, auth, payload)
    if command_id == "assistant.execution_session_read_artifact":
        return _read_artifact(root, config, auth, payload)
    if command_id == "assistant.execution_session_close":
        return _close_session(root, auth, payload)
    raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "unsupported_command", f"unsupported bridge command: {command_id}")


def _host_header_info(handler: Any) -> Tuple[str, str]:
    headers = _header_map(getattr(handler, "headers", {}))
    host = str(headers.get("x-forwarded-host") or headers.get("host") or "").strip()
    proto = str(headers.get("x-forwarded-proto") or "").strip().lower()
    if proto not in {"http", "https"}:
        proto = "https" if headers.get("cf-visitor") else "http"
    return host, proto


def _control_pane_session_path(repo_root: Path, session_id: str) -> Path:
    return (_control_pane_sessions_root(repo_root) / f"{_clean_text(session_id)}.json").resolve()


def _browser_relay_path(repo_root: Path, relay_id: str) -> Path:
    return (_browser_relay_root(repo_root) / f"{_clean_text(relay_id)}.json").resolve()


def mint_control_pane_session(handler: Any, *, repo_root: Path, auth: AuthenticatedToken, ttl_sec: int, page_path: str) -> Dict[str, Any]:
    _require_scope(auth, "control_pane.access")
    path = str(page_path or "/admin/mobile").strip() or "/admin/mobile"
    if path not in _ALLOWED_CONTROL_PANE_PATHS:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_control_pane_path", f"mobile plane path is not allowlisted: {path}")
    session_id = f"controlpane_{_compact_utc()}_{uuid.uuid4().hex[:8]}"
    secret = secrets.token_urlsafe(24)
    raw_token = f"{_CONTROL_PANE_SESSION_PREFIX}_{session_id}.{secret}"
    issued_at = _utc_now_iso()
    expires_at = (_utc_now() + _dt.timedelta(seconds=int(ttl_sec))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    record = {
        "schema_version": BRIDGE_CONTROL_PANE_SESSION_SCHEMA_VERSION,
        "uid": BRIDGE_UID,
        "session_id": session_id,
        "token_id": auth.token_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "control_pane_path": path,
        "session_token_sha256": _sha256_text(raw_token),
        "revoked": False,
    }
    atomic_write_canonical_json(_control_pane_session_path(repo_root, session_id), record)
    host, proto = _host_header_info(handler)
    query = urlencode({"token": raw_token})
    control_pane_url = f"{path}?{query}"
    if host:
        control_pane_url = f"{proto}://{host}{control_pane_url}"
    _append_bridge_event(
        repo_root,
        event="control_pane_session_issued",
        payload={
            "session_id": session_id,
            "token_id": auth.token_id,
            "expires_at": expires_at,
            "path": path,
        },
    )
    return {
        "ok": True,
        "session_id": session_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "access_token": raw_token,
        "control_pane_url": control_pane_url,
        "admin_header_name": "X-Admin-Token",
        "control_pane_path": path,
    }


def validate_control_pane_access_token(repo_root: Path, token_value: str) -> Dict[str, Any]:
    raw = str(token_value or "").strip()
    if not raw.startswith(f"{_CONTROL_PANE_SESSION_PREFIX}_") or "." not in raw:
        return {}
    prefix, _, _secret = raw.partition(".")
    session_id = prefix[len(f"{_CONTROL_PANE_SESSION_PREFIX}_") :].strip()
    if not session_id:
        return {}
    record = _read_json(_control_pane_session_path(repo_root, session_id))
    if not record or bool(record.get("revoked") is True):
        return {}
    try:
        config = _load_config(repo_root)
        token_row = _token_row_by_id(_load_token_rows(repo_root, config), _clean_text(record.get("token_id")))
    except Exception:
        return {}
    if not token_row:
        return {}
    expires_dt = _parse_iso_utc(record.get("expires_at"))
    if expires_dt is not None and expires_dt <= _utc_now():
        return {}
    expected_sha = _clean_text(record.get("session_token_sha256"))
    if not expected_sha or not hmac.compare_digest(expected_sha, _sha256_text(raw)):
        return {}
    return record


def _normalize_browser_relay_operation(raw: Any) -> str:
    op = _clean_text(raw).lower()
    if op not in _BROWSER_RELAY_OPERATIONS:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_browser_relay_operation", f"unsupported browser relay operation: {raw}")
    return op


def _bool_from_query(values: Sequence[str]) -> bool:
    raw = str(list(values or [""])[0] or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _int_from_query(values: Sequence[str], *, default: int) -> int:
    raw = str(list(values or [""])[0] or "").strip()
    if not raw:
        return int(default)
    return int(raw)


def _params_for_browser_relay_operation(operation: str, data: Mapping[str, Any]) -> Dict[str, Any]:
    op = _normalize_browser_relay_operation(operation)
    if op == "auth_status":
        return {}
    if op == "repo_search":
        payload: Dict[str, Any] = {"query": str(data.get("query") or "").strip()}
        if not payload["query"]:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_query", "query is required")
        roots = _clean_text_list(list(data.get("roots") or []))
        if roots:
            payload["roots"] = roots
        if bool(data.get("case_sensitive") is True):
            payload["case_sensitive"] = True
        if data.get("max_results") not in {None, ""}:
            payload["max_results"] = int(data.get("max_results"))
        return payload
    if op == "file_read":
        return {"path": _normalize_repo_relative_path(data.get("path"), field_name="path")}
    if op == "artifact":
        payload = {"path": _normalize_repo_relative_path(data.get("path"), field_name="path")}
        session_id = _clean_text(data.get("session_id"))
        if session_id:
            payload["session_id"] = session_id
        return payload
    if op == "ledger_list_active":
        payload: Dict[str, Any] = {}
        if data.get("limit") not in {None, ""}:
            payload["limit"] = int(data.get("limit"))
        if _clean_text(data.get("uid_prefix")):
            payload["uid_prefix"] = _clean_text(data.get("uid_prefix"))
        if _clean_text(data.get("title_contains")):
            payload["title_contains"] = _clean_text(data.get("title_contains"))
        return payload
    if op == "session_open":
        payload = {
            "base_commit_sha": _clean_text(data.get("base_commit_sha")),
            "execution_mode": _clean_text(data.get("execution_mode")).lower() or "isolated",
        }
        if not payload["base_commit_sha"]:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_base_commit_sha", "base_commit_sha is required")
        if payload["execution_mode"] not in {"isolated", "live"}:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_execution_mode", "execution_mode must be isolated or live")
        if payload["execution_mode"] == "live":
            live_reason = _clean_text(data.get("live_mode_reason") or data.get("reason"))
            if not live_reason:
                raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_live_mode_reason", "live mode requires live_mode_reason")
            payload["live_mode_reason"] = live_reason
        return payload
    if op == "session_close":
        payload = {
            "session_id": _clean_text(data.get("session_id")),
            "closed_reason": _clean_text(data.get("closed_reason")) or "client_request",
        }
        if not payload["session_id"]:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
        return payload
    if op == "write_file":
        content = data.get("content")
        if not isinstance(content, str):
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_content", "content must be a text string")
        payload = {
            "session_id": _clean_text(data.get("session_id")),
            "path": _normalize_repo_relative_path(data.get("path"), field_name="path"),
            "content": content,
            "mode": str(data.get("mode") or "overwrite").strip().lower() or "overwrite",
        }
        if not payload["session_id"]:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
        if payload["mode"] not in {"create", "overwrite", "append"}:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_write_mode", "mode must be create, overwrite, or append")
        return payload
    if op == "delete_file":
        payload = {
            "session_id": _clean_text(data.get("session_id")),
            "path": _normalize_repo_relative_path(data.get("path"), field_name="path"),
        }
        if not payload["session_id"]:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
        return payload
    if op == "run_validation":
        session_id = _clean_text(data.get("session_id"))
        if not session_id:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
        validation = dict(data.get("validation") or {}) if isinstance(data.get("validation"), dict) else {}
        kind = _clean_text(validation.get("kind"))
        if kind not in _ALLOWED_VALIDATION_KINDS:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "validation_not_allowlisted", f"validation kind is not allowlisted: {kind}")
        validation_payload: Dict[str, Any] = {"kind": kind}
        if validation.get("timeout_sec") not in {None, ""}:
            validation_payload["timeout_sec"] = int(validation.get("timeout_sec"))
        elif validation.get("timeout_s") not in {None, ""}:
            validation_payload["timeout_sec"] = int(validation.get("timeout_s"))
        selector = str(validation.get("selector") or "").strip()
        if selector:
            if selector.startswith("-") or "\n" in selector or selector.startswith("/") or ".." in selector:
                raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_pytest_selector", "pytest selector must be one safe repo-relative selector")
            validation_payload["selector"] = selector
        paths = [_normalize_repo_relative_path(item, field_name="path") for item in list(validation.get("paths") or [])]
        if paths:
            validation_payload["paths"] = paths
        return {"session_id": session_id, "validation": validation_payload}
    if op == "apply_diff":
        session_id = _clean_text(data.get("session_id"))
        if not session_id:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_session_id", "session_id is required")
        diff_text = str(data.get("diff") if data.get("diff") is not None else data.get("diff_text") or "")
        if not diff_text.strip():
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "empty_diff", "diff is required")
        diff_hash = _sha256_text(diff_text)
        provided_hash = _clean_text(data.get("diff_hash")).lower()
        if provided_hash and provided_hash != diff_hash:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "diff_hash_mismatch", "diff_hash does not match diff")
        expected_changed_files = [_normalize_repo_relative_path(item, field_name="expected_changed_files") for item in list(data.get("expected_changed_files") or [])]
        if not expected_changed_files:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_expected_changed_files", "expected_changed_files allowlist is required")
        payload = {
            "session_id": session_id,
            "base_commit_sha": _clean_text(data.get("base_commit_sha")),
            "diff_text": diff_text,
            "diff_hash": diff_hash,
            "expected_changed_files": expected_changed_files,
        }
        if not payload["base_commit_sha"]:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_base_commit_sha", "base_commit_sha is required")
        if data.get("allow_file_deletions") is True:
            payload["allow_file_deletions"] = True
        return payload
    payload = {"uid": _clean_text(data.get("uid"))}
    if not payload["uid"]:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_uid", "uid is required")
    if bool(data.get("include_events") is True):
        payload["include_events"] = True
    if data.get("events_limit") not in {None, ""}:
        payload["events_limit"] = int(data.get("events_limit"))
    return payload


def _params_from_browser_relay_query(operation: str, qs: Mapping[str, List[str]]) -> Dict[str, Any]:
    op = _normalize_browser_relay_operation(operation)
    if op == "auth_status":
        return {}
    if op in _BROWSER_RELAY_STORED_PAYLOAD_OPERATIONS:
        extra_keys = sorted(key for key in qs.keys() if key != "access_token")
        if extra_keys:
            raise BridgeRequestError(
                HTTPStatus.BAD_REQUEST,
                "unexpected_browser_relay_query",
                "stored-payload relay URLs do not accept mutable query parameters",
                details={"unexpected_keys": extra_keys},
            )
        return {}
    if op == "repo_search":
        payload: Dict[str, Any] = {"query": _clean_text(list(qs.get("query") or [""])[0])}
        if not payload["query"]:
            raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_query", "query is required")
        roots = _clean_text_list(list(qs.get("root") or []))
        if roots:
            payload["roots"] = roots
        if _bool_from_query(qs.get("case_sensitive") or []):
            payload["case_sensitive"] = True
        if list(qs.get("max_results") or []):
            payload["max_results"] = _int_from_query(qs.get("max_results") or [], default=_DEFAULT_SEARCH_MAX_RESULTS)
        return payload
    if op == "file_read":
        return {"path": _normalize_repo_relative_path(list(qs.get("path") or [""])[0], field_name="path")}
    if op == "ledger_list_active":
        payload: Dict[str, Any] = {}
        if list(qs.get("limit") or []):
            payload["limit"] = _int_from_query(qs.get("limit") or [], default=50)
        uid_prefix = _clean_text(list(qs.get("uid_prefix") or [""])[0])
        if uid_prefix:
            payload["uid_prefix"] = uid_prefix
        title_contains = _clean_text(list(qs.get("title_contains") or [""])[0])
        if title_contains:
            payload["title_contains"] = title_contains
        return payload
    if op == "artifact":
        payload = {"path": _normalize_repo_relative_path(list(qs.get("path") or [""])[0], field_name="path")}
        session_id = _clean_text(list(qs.get("session_id") or [""])[0])
        if session_id:
            payload["session_id"] = session_id
        return payload
    payload = {"uid": _clean_text(list(qs.get("uid") or [""])[0])}
    if not payload["uid"]:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "missing_uid", "uid is required")
    if _bool_from_query(qs.get("include_events") or []):
        payload["include_events"] = True
    if list(qs.get("events_limit") or []):
        payload["events_limit"] = _int_from_query(qs.get("events_limit") or [], default=50)
    return payload


def _browser_relay_query_pairs(operation: str, payload: Dict[str, Any]) -> List[Tuple[str, str]]:
    op = _normalize_browser_relay_operation(operation)
    params = dict(payload or {})
    if op == "auth_status":
        return []
    if op in _BROWSER_RELAY_STORED_PAYLOAD_OPERATIONS:
        return []
    if op == "repo_search":
        out = [("query", str(params.get("query") or ""))]
        for root in list(params.get("roots") or []):
            out.append(("root", str(root)))
        if params.get("case_sensitive") is True:
            out.append(("case_sensitive", "1"))
        if params.get("max_results") not in {None, ""}:
            out.append(("max_results", str(params.get("max_results"))))
        return out
    if op == "file_read":
        return [("path", _clean_text(params.get("path")))]
    if op == "ledger_list_active":
        out: List[Tuple[str, str]] = []
        if params.get("limit") not in {None, ""}:
            out.append(("limit", str(params.get("limit"))))
        if _clean_text(params.get("uid_prefix")):
            out.append(("uid_prefix", _clean_text(params.get("uid_prefix"))))
        if _clean_text(params.get("title_contains")):
            out.append(("title_contains", _clean_text(params.get("title_contains"))))
        return out
    if op == "artifact":
        out = [("path", _clean_text(params.get("path")))]
        if _clean_text(params.get("session_id")):
            out.append(("session_id", _clean_text(params.get("session_id"))))
        return out
    out = [("uid", _clean_text(params.get("uid")))]
    if params.get("include_events") is True:
        out.append(("include_events", "1"))
    if params.get("events_limit") not in {None, ""}:
        out.append(("events_limit", str(params.get("events_limit"))))
    return out


def _browser_relay_response_params(operation: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    op = _normalize_browser_relay_operation(operation)
    params = dict(payload or {})
    if op == "write_file":
        content = str(params.get("content") or "")
        return {
            "session_id": _clean_text(params.get("session_id")),
            "path": _clean_text(params.get("path")),
            "mode": _clean_text(params.get("mode")) or "overwrite",
            "content_sha256": _sha256_text(content),
            "content_bytes": len(content.encode("utf-8")),
        }
    if op == "file_read":
        return {"path": _clean_text(params.get("path"))}
    if op == "session_open":
        summary = {
            "base_commit_sha": _clean_text(params.get("base_commit_sha")),
            "execution_mode": _clean_text(params.get("execution_mode")) or "isolated",
        }
        if _clean_text(params.get("live_mode_reason")):
            summary["live_mode_reason"] = _clean_text(params.get("live_mode_reason"))
        return summary
    if op == "session_close":
        return {
            "session_id": _clean_text(params.get("session_id")),
            "closed_reason": _clean_text(params.get("closed_reason")) or "client_request",
        }
    if op == "apply_diff":
        diff_text = str(params.get("diff_text") or "")
        return {
            "session_id": _clean_text(params.get("session_id")),
            "base_commit_sha": _clean_text(params.get("base_commit_sha")),
            "diff_sha256": _clean_text(params.get("diff_hash")) or _sha256_text(diff_text),
            "diff_bytes": len(diff_text.encode("utf-8")),
            "expected_changed_files": _clean_text_list(list(params.get("expected_changed_files") or [])),
            "allow_file_deletions": bool(params.get("allow_file_deletions") is True),
        }
    if op == "run_validation":
        validation = params.get("validation") if isinstance(params.get("validation"), dict) else {}
        summary = {"kind": _clean_text(validation.get("kind"))}
        if _clean_text_list(list(validation.get("paths") or [])):
            summary["paths"] = _clean_text_list(list(validation.get("paths") or []))
        if _clean_text(validation.get("selector")):
            summary["selector"] = _clean_text(validation.get("selector"))
        if validation.get("timeout_sec") not in {None, ""}:
            summary["timeout_sec"] = int(validation.get("timeout_sec"))
        return {"session_id": _clean_text(params.get("session_id")), "validation": summary}
    if op == "delete_file":
        return {"session_id": _clean_text(params.get("session_id")), "path": _clean_text(params.get("path"))}
    return dict(params or {})


def mint_browser_relay_url(handler: Any, *, repo_root: Path, auth: AuthenticatedToken, ttl_sec: int, operation: str, params: Dict[str, Any]) -> Dict[str, Any]:
    op = _normalize_browser_relay_operation(operation)
    if op == "repo_search":
        _require_scope(auth, "repo.read")
    elif op == "file_read":
        _require_scope(auth, "repo.read")
    elif op in {"ledger_list_active", "ledger_read"}:
        _require_scope(auth, "ledger.read")
    elif op == "artifact":
        _require_any_scope(auth, ("repo.read", "artifacts.read"))
    elif op == "session_open":
        _require_scope(auth, "session.open")
    elif op == "session_close":
        _require_scope(auth, "session.open")
        _ensure_session_access(_load_session(repo_root, _clean_text(params.get("session_id"))), auth, allow_expired_read=True)
    elif op in {"write_file", "delete_file"}:
        _require_scope(auth, "repo.write")
        _ensure_session_access(_load_session(repo_root, _clean_text(params.get("session_id"))), auth)
    elif op == "run_validation":
        _require_scope(auth, "validation.run")
        _ensure_session_access(_load_session(repo_root, _clean_text(params.get("session_id"))), auth)
    elif op == "apply_diff":
        _require_scope(auth, "diff.apply")
        _ensure_session_access(_load_session(repo_root, _clean_text(params.get("session_id"))), auth)
    relay_id = f"browserrelay_{_compact_utc()}_{uuid.uuid4().hex[:8]}"
    secret = secrets.token_urlsafe(24)
    raw_token = f"{_BROWSER_RELAY_PREFIX}_{relay_id}.{secret}"
    issued_at = _utc_now_iso()
    expires_at = (_utc_now() + _dt.timedelta(seconds=int(ttl_sec))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    relay_mode = "stored_payload" if op in _BROWSER_RELAY_STORED_PAYLOAD_OPERATIONS else "query"
    record = {
        "schema_version": BRIDGE_BROWSER_RELAY_SCHEMA_VERSION,
        "uid": BRIDGE_UID,
        "relay_id": relay_id,
        "operation": op,
        "token_id": auth.token_id,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "relay_token_sha256": _sha256_text(raw_token),
        "relay_mode": relay_mode,
        "single_use": op in _BROWSER_RELAY_SINGLE_USE_OPERATIONS,
        "stored_payload": dict(params or {}) if relay_mode == "stored_payload" else {},
        "params_summary": _browser_relay_response_params(op, params),
        "params_hash": _json_hash(dict(params or {})),
        "execution_status": "issued",
        "execution_http_status": 0,
        "executed_at": "",
        "response_payload": {},
        "revoked": False,
    }
    atomic_write_canonical_json(_browser_relay_path(repo_root, relay_id), record)
    relay_path = f"/api/assistant/browser_relay/{op}"
    query_pairs = [("access_token", raw_token), *_browser_relay_query_pairs(op, params)]
    relay_url = f"{relay_path}?{urlencode(query_pairs, doseq=True)}"
    host, proto = _host_header_info(handler)
    if host:
        relay_url = f"{proto}://{host}{relay_url}"
    _append_bridge_event(
        repo_root,
        event="browser_relay_issued",
        payload={
            "relay_id": relay_id,
            "operation": op,
            "token_id": auth.token_id,
            "expires_at": expires_at,
            "params_hash": record["params_hash"],
            "relay_mode": relay_mode,
        },
    )
    return {
        "ok": True,
        "relay_id": relay_id,
        "operation": op,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "relay_url": relay_url,
        "access_token": raw_token,
        "params": dict(record["params_summary"]),
        "params_hash": record["params_hash"],
        "relay_mode": relay_mode,
    }


def _validate_browser_relay_access_token(repo_root: Path, config: BridgeConfig, *, operation: str, token_value: str, params: Dict[str, Any]) -> Tuple[str, AuthenticatedToken, Dict[str, Any]]:
    raw = str(token_value or "").strip()
    if not raw.startswith(f"{_BROWSER_RELAY_PREFIX}_") or "." not in raw:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "invalid_browser_relay_token", "missing or invalid browser relay token")
    prefix, _, _secret = raw.partition(".")
    relay_id = prefix[len(f"{_BROWSER_RELAY_PREFIX}_") :].strip()
    record = _read_json(_browser_relay_path(repo_root, relay_id))
    if not record or bool(record.get("revoked") is True):
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "invalid_browser_relay_token", "browser relay token is not active")
    if _clean_text(record.get("operation")) != _normalize_browser_relay_operation(operation):
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "browser_relay_operation_mismatch", "browser relay token does not match operation")
    expires_dt = _parse_iso_utc(record.get("expires_at"))
    if expires_dt is not None and expires_dt <= _utc_now():
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "browser_relay_expired", "browser relay token is expired")
    expected_sha = _clean_text(record.get("relay_token_sha256"))
    if not expected_sha or not hmac.compare_digest(expected_sha, _sha256_text(raw)):
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "invalid_browser_relay_token", "browser relay token is invalid")
    relay_mode = _clean_text(record.get("relay_mode")) or "query"
    expected_params = dict(record.get("stored_payload") or {}) if relay_mode == "stored_payload" else dict(params or {})
    if _clean_text(record.get("params_hash")) != _json_hash(expected_params):
        raise BridgeRequestError(HTTPStatus.FORBIDDEN, "browser_relay_params_mismatch", "browser relay URL parameters do not match the issued relay")
    token_row = _token_row_by_id(_load_token_rows(repo_root, config), _clean_text(record.get("token_id")))
    if not token_row:
        raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "browser_relay_token_revoked", "source assistant token is no longer active")
    return relay_id, _auth_from_token_row(repo_root, token_row), record


def _execute_browser_relay_operation(
    repo_root: Path,
    config: BridgeConfig,
    auth: AuthenticatedToken,
    *,
    relay_id: str,
    record: Dict[str, Any],
) -> Tuple[int, Dict[str, Any]]:
    relay_path = _browser_relay_path(repo_root, relay_id)
    op = _normalize_browser_relay_operation(record.get("operation"))
    with _LOCK:
        current = _read_json(relay_path)
        if not current:
            raise BridgeRequestError(HTTPStatus.UNAUTHORIZED, "invalid_browser_relay_token", "browser relay token is not active")
        if bool(current.get("single_use") is True) and _clean_text(current.get("executed_at")):
            cached = dict(current.get("response_payload") or {})
            if not cached:
                raise BridgeRequestError(HTTPStatus.CONFLICT, "browser_relay_already_consumed", "browser relay was already consumed")
            replayed = dict(cached)
            replayed["duplicate_suppressed"] = True
            replayed["replayed_from_browser_relay"] = True
            _append_bridge_event(
                repo_root,
                event="browser_relay_replayed",
                payload={
                    "relay_id": relay_id,
                    "operation": op,
                    "token_id": auth.token_id,
                    "params_hash": _clean_text(current.get("params_hash")),
                    "http_status": int(current.get("execution_http_status") or HTTPStatus.OK),
                    "result": _clean_text(current.get("execution_status")) or "cached",
                },
            )
            return int(current.get("execution_http_status") or HTTPStatus.OK), replayed
        payload = dict(current.get("stored_payload") or {})
        try:
            if op == "session_open":
                response = _open_execution_session(repo_root, config, auth, payload, route_path="/api/assistant/browser_relay/session_open")
            elif op == "session_close":
                response = _close_session(repo_root, auth, payload)
            elif op == "write_file":
                response = _write_file(repo_root, config, auth, payload)
            elif op == "delete_file":
                response = _delete_file(repo_root, auth, payload)
            elif op == "run_validation":
                response = _validate_session(repo_root, config, auth, payload)
            elif op == "apply_diff":
                response = _apply_diff(repo_root, config, auth, payload)
            else:
                raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_browser_relay_operation", f"unsupported stored browser relay operation: {op}")
            http_status = int(HTTPStatus.OK)
            execution_status = "succeeded"
        except BridgeRequestError as exc:
            response = {
                "ok": False,
                "error_code": exc.code,
                "error": str(exc),
                **dict(exc.details or {}),
            }
            http_status = int(exc.status)
            execution_status = "failed"
        except Exception as exc:  # pragma: no cover - fail-closed guard
            response = {
                "ok": False,
                "error_code": "internal_error",
                "error": str(exc),
            }
            http_status = int(HTTPStatus.INTERNAL_SERVER_ERROR)
            execution_status = "failed"
        current["executed_at"] = _utc_now_iso()
        current["execution_http_status"] = http_status
        current["execution_status"] = execution_status
        current["response_payload"] = dict(response or {})
        atomic_write_canonical_json(relay_path, current)
    _append_bridge_event(
        repo_root,
        event="browser_relay_executed",
        payload={
            "relay_id": relay_id,
            "operation": op,
            "token_id": auth.token_id,
            "params_hash": _clean_text(current.get("params_hash")),
            "http_status": http_status,
            "result": execution_status,
        },
    )
    return http_status, dict(response or {})


def _browser_relay_payload(route: str, operation: str, relay_id: str, auth: AuthenticatedToken, response: Dict[str, Any]) -> Dict[str, Any]:
    payload = _route_base_payload(route=route, auth=auth)
    payload.update(response)
    payload["browser_relay"] = {
        "ok": True,
        "operation": operation,
        "relay_id": relay_id,
        "single_use": operation in _BROWSER_RELAY_SINGLE_USE_OPERATIONS,
        "replayed": bool(response.get("replayed_from_browser_relay") is True),
    }
    return payload


def _session_execute(handler: Any, *, repo_root: Path, config: BridgeConfig, qs: Mapping[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    if bool(config.read_only_mode):
        raise BridgeRequestError(
            HTTPStatus.FORBIDDEN,
            "assistant_read_only_mode",
            "assistant bridge is running in read-only mode; session execution is disabled",
        )
    session, auth = _validate_session_mint_access_token(
        repo_root,
        config,
        _clean_text(list(qs.get("access_token") or [""])[0]),
    )
    operation = _clean_text(list(qs.get("operation") or [""])[0]).lower()
    if operation not in {"write_file", "delete_file", "run_validation", "apply_diff", "artifact", "session_close"}:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_session_mint_operation", f"unsupported session mint operation: {operation}")
    payload = _decode_session_mint_payload(_clean_text(list(qs.get("payload_b64") or [""])[0]))
    payload["session_id"] = _clean_text(session.get("session_id"))
    if operation == "artifact":
        response = _read_bound_path(repo_root, config, auth, payload)
        response["session_id"] = _clean_text(session.get("session_id"))
        response["session_mint"] = {"ok": True, "operation": operation, "session_id": _clean_text(session.get("session_id"))}
        return HTTPStatus.OK, response
    params = _params_for_browser_relay_operation(operation, payload)
    if operation == "write_file":
        response = _write_file(repo_root, config, auth, params)
    elif operation == "delete_file":
        response = _delete_file(repo_root, auth, params)
    elif operation == "run_validation":
        response = _validate_session(repo_root, config, auth, params)
    elif operation == "apply_diff":
        response = _apply_diff(repo_root, config, auth, params)
    elif operation == "session_close":
        response = _close_session(repo_root, auth, params)
    else:
        raise BridgeRequestError(HTTPStatus.BAD_REQUEST, "invalid_session_mint_operation", f"unsupported session mint operation: {operation}")
    response["session_id"] = _clean_text(session.get("session_id"))
    response["session_mint"] = {"ok": True, "operation": operation, "session_id": _clean_text(session.get("session_id"))}
    return HTTPStatus.OK, response


def _auth_status_payload(config: BridgeConfig, auth: AuthenticatedToken) -> Dict[str, Any]:
    return {
        "schema_version": BRIDGE_AUTH_STATUS_SCHEMA_VERSION,
        "uid": BRIDGE_UID,
        "utc": _utc_now_iso(),
        "ok": True,
        "assistant_api_enabled": bool(config.enabled),
        "read_only_mode": bool(config.read_only_mode),
        "token": {
            "token_id": auth.token_id,
            "issuer": auth.issuer,
            "audience": auth.audience,
            "scopes": list(auth.scopes),
            "created_at": auth.created_at,
            "expires_at": auth.expires_at,
            "last_used_at": auth.last_used_at,
            "allowed_repo_roots": list(auth.allowed_repo_roots),
            "allowed_pis_prefixes": list(auth.allowed_pis_prefixes),
        },
        "allowed_read_routes": sorted(_READ_ONLY_ROUTE_PATHS),
        "control_pane_paths": sorted(_ALLOWED_CONTROL_PANE_PATHS),
    }


def _route_command(handler: Any, *, repo_root: Path, path: str, qs: Mapping[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    _ = qs
    root = Path(repo_root).resolve()
    body = _read_request_json(handler, max_bytes=2 * 1024 * 1024)
    config, auth = authenticate_bearer_request(handler, repo_root=root)
    _check_read_only_route(config, path)
    command_id, intent_classification = _ROUTE_COMMANDS[path]
    payload = {str(k): v for k, v in dict(body or {}).items() if str(k) not in {"request_id", "idempotency_key"}}
    request_id = str(body.get("request_id") or f"assistant_bridge_{uuid.uuid4().hex[:12]}").strip()
    idempotency_key = str(body.get("idempotency_key") or "").strip()
    boundary_result = _backend_boundary.process_command_request(
        {
            "request_id": request_id,
            "command_id": command_id,
            "idempotency_key": idempotency_key,
            "intent_classification": intent_classification,
            "token_scopes": list(auth.scopes),
            "payload": payload,
        },
        repo_root=root,
        writer=f"assistant_bridge.route.{command_id}",
    )
    boundary_status = str(boundary_result.get("status") or "").strip().lower()
    if boundary_status == _backend_boundary.COMMAND_STATUS_DUPLICATE_SUPPRESSED:
        return _resume_duplicate_result(root, boundary_result, route=path, auth=auth)
    if boundary_status != _backend_boundary.COMMAND_STATUS_ACCEPTED:
        return _backend_boundary.http_status_for_result(boundary_result), _boundary_error_payload(path, boundary_result, auth=auth)
    receipt = _store_command_started(root, boundary_result=boundary_result, auth=auth, route_path=path, intent_classification=intent_classification, payload=payload)
    try:
        response = _execute_bridge_command(root, config, auth, command_id, payload, route_path=path)
        payload_out = _route_base_payload(route=path, auth=auth)
        payload_out.update(response)
        payload_out["boundary_result"] = dict(boundary_result)
        artifact_refs = _clean_text_list(list(payload_out.get("artifact_refs") or []))
        session_id = _clean_text(payload_out.get("session_id")) or _clean_text((payload_out.get("session") or {}).get("session_id"))
        _store_command_finished(root, receipt=receipt, status="succeeded", http_status=HTTPStatus.OK, response_payload=payload_out, artifact_refs=artifact_refs, session_id=session_id)
        return HTTPStatus.OK, payload_out
    except BridgeRequestError as exc:
        error_payload = _route_base_payload(route=path, auth=auth)
        error_payload.update({
            "ok": False,
            "error_code": exc.code,
            "error": str(exc),
            "boundary_result": dict(boundary_result),
            **dict(exc.details or {}),
        })
        session_id = _clean_text(payload.get("session_id"))
        _store_command_finished(root, receipt=receipt, status="failed", http_status=exc.status, response_payload=error_payload, artifact_refs=[], session_id=session_id)
        return exc.status, error_payload
    except Exception as exc:  # pragma: no cover - fail-closed guard
        error_payload = _route_base_payload(route=path, auth=auth)
        error_payload.update({
            "ok": False,
            "error_code": "internal_error",
            "error": str(exc),
            "boundary_result": dict(boundary_result),
        })
        session_id = _clean_text(payload.get("session_id"))
        _store_command_finished(root, receipt=receipt, status="failed", http_status=HTTPStatus.INTERNAL_SERVER_ERROR, response_payload=error_payload, artifact_refs=[], session_id=session_id)
        return HTTPStatus.INTERNAL_SERVER_ERROR, error_payload


def handle_get_request(handler: Any, *, repo_root: Path, path: str, qs: Mapping[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    root = Path(repo_root).resolve()
    try:
        if path == "/api/assistant/auth/status":
            config, auth = authenticate_bearer_request(handler, repo_root=root)
            return HTTPStatus.OK, _auth_status_payload(config, auth)
        if path == "/api/assistant/browser_relay/session_execute":
            config = _load_config(root)
            _check_bridge_enabled(config)
            return _session_execute(handler, repo_root=root, config=config, qs=qs)
        relay_prefix = "/api/assistant/browser_relay/"
        if path.startswith(relay_prefix):
            config = _load_config(root)
            _check_bridge_enabled(config)
            operation = path[len(relay_prefix) :].strip().strip("/")
            _check_read_only_browser_operation(config, operation)
            params = _params_from_browser_relay_query(operation, qs)
            relay_token = _clean_text(list(qs.get("access_token") or [""])[0])
            relay_id, auth, relay_record = _validate_browser_relay_access_token(root, config, operation=operation, token_value=relay_token, params=params)
            op = _normalize_browser_relay_operation(operation)
            route = f"{relay_prefix}{op}"
            try:
                if op == "auth_status":
                    response_payload = _auth_status_payload(config, auth)
                    status_code = int(HTTPStatus.OK)
                elif op == "repo_search":
                    response_payload = _repo_search(root, auth, params)
                    status_code = int(HTTPStatus.OK)
                elif op == "ledger_list_active":
                    response_payload = _ledger_list_active(root, auth, params)
                    status_code = int(HTTPStatus.OK)
                elif op == "ledger_read":
                    response_payload = _ledger_read_item(root, auth, params)
                    status_code = int(HTTPStatus.OK)
                elif op == "file_read":
                    response_payload = _file_read(root, config, auth, params)
                    status_code = int(HTTPStatus.OK)
                elif op == "artifact":
                    response_payload = _read_bound_path(root, config, auth, params)
                    status_code = int(HTTPStatus.OK)
                else:
                    status_code, response_payload = _execute_browser_relay_operation(
                        root,
                        config,
                        auth,
                        relay_id=relay_id,
                        record=relay_record,
                    )
                return status_code, _browser_relay_payload(route, op, relay_id, auth, response_payload)
            except BridgeRequestError as exc:
                return exc.status, _browser_relay_payload(
                    route,
                    op,
                    relay_id,
                    auth,
                    {
                        "ok": False,
                        "error_code": exc.code,
                        "error": str(exc),
                        **dict(exc.details or {}),
                    },
                )
        return HTTPStatus.NOT_FOUND, {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "utc": _utc_now_iso(),
            "ok": False,
            "error_code": "route_not_found",
            "error": f"unknown assistant GET route: {path}",
        }
    except BridgeRequestError as exc:
        return exc.status, {
            "schema_version": BRIDGE_AUTH_STATUS_SCHEMA_VERSION,
            "utc": _utc_now_iso(),
            "ok": False,
            "error_code": exc.code,
            "error": str(exc),
            **dict(exc.details or {}),
        }
    except Exception as exc:  # pragma: no cover
        return HTTPStatus.INTERNAL_SERVER_ERROR, {
            "schema_version": BRIDGE_AUTH_STATUS_SCHEMA_VERSION,
            "utc": _utc_now_iso(),
            "ok": False,
            "error_code": "internal_error",
            "error": str(exc),
        }


def handle_post_request(handler: Any, *, repo_root: Path, path: str, qs: Mapping[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    _ = qs
    root = Path(repo_root).resolve()
    try:
        if path == "/api/assistant/auth/control_pane_session":
            body = _read_request_json(handler, max_bytes=64 * 1024)
            config, auth = authenticate_bearer_request(handler, repo_root=root)
            if bool(config.read_only_mode):
                raise BridgeRequestError(
                    HTTPStatus.FORBIDDEN,
                    "assistant_read_only_mode",
                    "assistant bridge is running in read-only mode; browser admin exchange is disabled",
                )
            ttl_sec = int(body.get("ttl_sec") or config.control_pane_session_ttl_sec)
            ttl_sec = max(60, min(ttl_sec, int(config.control_pane_session_ttl_sec)))
            page_path = str(body.get("path") or "/admin/mobile").strip() or "/admin/mobile"
            payload = _route_base_payload(route=path, auth=auth)
            payload.update(mint_control_pane_session(handler, repo_root=root, auth=auth, ttl_sec=ttl_sec, page_path=page_path))
            return HTTPStatus.OK, payload
        if path == "/api/assistant/auth/browser_relay_url":
            body = _read_request_json(handler, max_bytes=2 * 1024 * 1024)
            config, auth = authenticate_bearer_request(handler, repo_root=root)
            operation = _normalize_browser_relay_operation(body.get("operation"))
            _check_read_only_browser_operation(config, operation)
            ttl_sec = int(body.get("ttl_sec") or config.browser_relay_ttl_sec)
            ttl_sec = max(60, min(ttl_sec, int(config.browser_relay_ttl_sec)))
            params = _params_for_browser_relay_operation(operation, dict(body.get("params") or {}))
            payload = _route_base_payload(route=path, auth=auth)
            payload.update(mint_browser_relay_url(handler, repo_root=root, auth=auth, ttl_sec=ttl_sec, operation=operation, params=params))
            return HTTPStatus.OK, payload
        if path in _ROUTE_COMMANDS:
            return _route_command(handler, repo_root=root, path=path, qs=qs)
        return HTTPStatus.NOT_FOUND, {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "utc": _utc_now_iso(),
            "ok": False,
            "error_code": "route_not_found",
            "error": f"unknown assistant POST route: {path}",
        }
    except BridgeRequestError as exc:
        return exc.status, {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "utc": _utc_now_iso(),
            "ok": False,
            "route": path,
            "error_code": exc.code,
            "error": str(exc),
            **dict(exc.details or {}),
        }
    except subprocess.TimeoutExpired as exc:
        return HTTPStatus.GATEWAY_TIMEOUT, {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "utc": _utc_now_iso(),
            "ok": False,
            "route": path,
            "error_code": "subprocess_timeout",
            "error": str(exc),
        }
    except Exception as exc:  # pragma: no cover
        return HTTPStatus.INTERNAL_SERVER_ERROR, {
            "schema_version": BRIDGE_SCHEMA_VERSION,
            "utc": _utc_now_iso(),
            "ok": False,
            "route": path,
            "error_code": "internal_error",
            "error": str(exc),
        }


__all__ = [
    "BRIDGE_SCHEMA_VERSION",
    "BridgeRequestError",
    "AuthenticatedToken",
    "BridgeConfig",
    "authenticate_bearer_request",
    "handle_get_request",
    "handle_post_request",
    "mint_browser_relay_url",
    "mint_control_pane_session",
    "validate_control_pane_access_token",
]
