"""Assistant ingress probe helpers for external transport verification.

Last modified: 2026-03-31 Europe/Vienna

This surface is intentionally narrow:
- proves ingress reachability and transport integrity only
- never performs bridge execution or repo mutation
- persists durable audit evidence under assistant_bridge roots
"""

from __future__ import annotations

import hashlib
import json
import socket
import time
import uuid
from dataclasses import dataclass
from http import HTTPStatus
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

from tools import runtime_policy as _runtime_policy
from infra.webui import server_lab_assistant_host as _lab_host
from infra.webui.server_common import _now_utc_iso, atomic_write_canonical_json

PROBE_ROUTE_VERSION = "assistant_probe_transport_v1"
PROBE_AUDIT_SCHEMA_VERSION = "assistant_probe_audit_record_v1"
PROBE_CAPABILITIES_SCHEMA_VERSION = "assistant_probe_capabilities_v1"

_DEFAULT_INGRESS_TAGS = [
    "direct_https",
    "cloudflare_tunnel",
    "ngrok",
    "custom_port_test",
    "unspecified",
]
_SAFE_HEADER_NAMES = (
    "content-type",
    "user-agent",
    "x-request-id",
    "traceparent",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-forwarded-port",
    "x-real-ip",
    "cf-connecting-ip",
    "cf-ray",
)
_FORWARDING_HEADER_NAMES = (
    "x-forwarded-for",
    "x-real-ip",
    "cf-connecting-ip",
)
_AUTH_MODES = {"localhost_only", "localhost_or_secret", "secret", "off"}
_SECRET_HEADER = "x-assistant-probe-secret"


@dataclass(frozen=True)
class ProbeConfig:
    enabled: bool
    auth_mode: str
    secret: str
    max_body_bytes: int
    max_echo_bytes: int
    max_delay_ms: int
    allowed_ingress_tags: Tuple[str, ...]


class ProbeRequestError(Exception):
    def __init__(
        self,
        status: int,
        classification: str,
        message: str,
        *,
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.status = int(status)
        self.classification = str(classification or "probe_error")
        self.details = dict(details or {})


@dataclass
class ProbeContext:
    repo_root: Path
    path: str
    method: str
    qs: Mapping[str, List[str]]
    config: ProbeConfig
    started_utc: str
    started_perf: float
    request_id: str
    ingress_tag: str
    remote_addr: str
    selected_headers: Dict[str, str]
    delay_requested_ms: int
    delay_applied_ms: int
    host_identity: Dict[str, Any]
    request_size_bytes: int = 0
    request_body_sha256: str = ""
    request_body_preview_text: str = ""


def _bool_text(raw: str, default: bool) -> bool:
    txt = str(raw or "").strip().lower()
    if not txt:
        return bool(default)
    if txt in {"1", "true", "yes", "on"}:
        return True
    if txt in {"0", "false", "no", "off"}:
        return False
    return bool(default)


def _load_config() -> ProbeConfig:
    enabled = _bool_text(
        _runtime_policy.get_runtime_policy_text(
            "assistant.probe.enabled",
            "true",
            env_names=("ASSISTANT_PROBE_ENABLED",),
        ),
        True,
    )
    auth_mode = str(
        _runtime_policy.get_runtime_policy_text(
            "assistant.probe.auth_mode",
            "localhost_or_secret",
            env_names=("ASSISTANT_PROBE_AUTH_MODE",),
        )
        or "localhost_or_secret"
    ).strip().lower()
    if auth_mode not in _AUTH_MODES:
        auth_mode = "localhost_or_secret"
    secret = str(
        _runtime_policy.get_runtime_policy_text(
            "assistant.probe.secret",
            "",
            env_names=("ASSISTANT_PROBE_SECRET",),
        )
        or ""
    ).strip()
    max_body_bytes = _runtime_policy.get_runtime_policy_int(
        "assistant.probe.max_body_bytes",
        65536,
        env_names=("ASSISTANT_PROBE_MAX_BODY_BYTES",),
        minimum=1,
        maximum=5 * 1024 * 1024,
    )
    max_echo_bytes = _runtime_policy.get_runtime_policy_int(
        "assistant.probe.max_echo_bytes",
        4096,
        env_names=("ASSISTANT_PROBE_MAX_ECHO_BYTES",),
        minimum=256,
        maximum=max_body_bytes,
    )
    max_delay_ms = _runtime_policy.get_runtime_policy_int(
        "assistant.probe.max_delay_ms",
        2500,
        env_names=("ASSISTANT_PROBE_MAX_DELAY_MS",),
        minimum=0,
        maximum=30000,
    )
    allowed_tags = _runtime_policy.get_runtime_policy_csv(
        "assistant.probe.allowed_ingress_tags",
        ",".join(_DEFAULT_INGRESS_TAGS),
        env_names=("ASSISTANT_PROBE_ALLOWED_INGRESS_TAGS",),
    )
    normalized_tags: List[str] = []
    for tag in list(allowed_tags or []):
        txt = str(tag or "").strip().lower()
        if txt and txt not in normalized_tags:
            normalized_tags.append(txt)
    if "unspecified" not in normalized_tags:
        normalized_tags.append("unspecified")
    return ProbeConfig(
        enabled=enabled,
        auth_mode=auth_mode,
        secret=secret,
        max_body_bytes=int(max_body_bytes),
        max_echo_bytes=int(max_echo_bytes),
        max_delay_ms=int(max_delay_ms),
        allowed_ingress_tags=tuple(normalized_tags or list(_DEFAULT_INGRESS_TAGS)),
    )


def _compact_utc_stamp() -> str:
    return time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())


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
        if key and value and key not in out:
            out[key] = value
    return out


def _query_first(qs: Mapping[str, List[str]], key: str) -> str:
    try:
        values = qs.get(key) or []
    except Exception:
        values = []
    if not values:
        return ""
    return str(values[0] or "").strip()


def _request_id(qs: Mapping[str, List[str]], headers: Dict[str, str]) -> str:
    for key in ("x-request-id",):
        value = str(headers.get(key) or "").strip()
        if value:
            return value[:128]
    for key in ("request_id", "requestId"):
        value = _query_first(qs, key)
        if value:
            return value[:128]
    return f"assistant_probe_{uuid.uuid4().hex[:12]}"


def _remote_addr(handler: Any) -> str:
    try:
        client_address = getattr(handler, "client_address", None)
        if isinstance(client_address, tuple) and client_address:
            return str(client_address[0] or "").strip()
    except Exception:
        return ""
    return ""


def _is_loopback(addr: str) -> bool:
    txt = str(addr or "").strip().lower()
    return txt in {"127.0.0.1", "::1", "localhost"} or txt.startswith("::ffff:127.")


def _selected_headers(headers: Dict[str, str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for name in _SAFE_HEADER_NAMES:
        value = str(headers.get(name) or "").strip()
        if value:
            out[name] = value
    return out


def _parse_ingress_tag(qs: Mapping[str, List[str]], headers: Dict[str, str], config: ProbeConfig) -> str:
    raw = _query_first(qs, "ingress_tag") or str(headers.get("x-assistant-ingress-tag") or "")
    tag = str(raw or "").strip().lower() or "unspecified"
    if tag not in set(config.allowed_ingress_tags):
        raise ProbeRequestError(
            HTTPStatus.BAD_REQUEST,
            "invalid_ingress_tag",
            f"unsupported ingress_tag: {tag}",
            details={"allowed_ingress_tags": list(config.allowed_ingress_tags)},
        )
    return tag


def _parse_delay_ms(qs: Mapping[str, List[str]], headers: Dict[str, str], config: ProbeConfig) -> Tuple[int, int]:
    raw = _query_first(qs, "delay_ms") or str(headers.get("x-assistant-probe-delay-ms") or "")
    if not raw:
        return 0, 0
    try:
        requested = int(raw)
    except Exception:
        raise ProbeRequestError(HTTPStatus.BAD_REQUEST, "invalid_delay_ms", "delay_ms must be an integer")
    if requested < 0:
        raise ProbeRequestError(HTTPStatus.BAD_REQUEST, "invalid_delay_ms", "delay_ms must be >= 0")
    applied = min(int(config.max_delay_ms), requested)
    return requested, applied


def _host_identity(repo_root: Path) -> Dict[str, Any]:
    health = _lab_host.host_health_snapshot(repo_root, initialize_if_missing=False)
    return {
        "hostname": socket.gethostname().strip() or "unknown",
        "install_id": str(health.get("install_id") or ""),
        "last_boot_id": str(health.get("last_boot_id") or ""),
        "last_boot_utc": str(health.get("last_boot_utc") or ""),
    }


def _build_context(handler: Any, *, repo_root: Path, path: str, method: str, qs: Mapping[str, List[str]]) -> ProbeContext:
    config = _load_config()
    headers = _header_map(getattr(handler, "headers", {}))
    ingress_tag = _parse_ingress_tag(qs, headers, config)
    delay_requested_ms, delay_applied_ms = _parse_delay_ms(qs, headers, config)
    return ProbeContext(
        repo_root=Path(repo_root).resolve(),
        path=str(path or ""),
        method=str(method or "GET").upper(),
        qs=qs,
        config=config,
        started_utc=_now_utc_iso(),
        started_perf=time.perf_counter(),
        request_id=_request_id(qs, headers),
        ingress_tag=ingress_tag,
        remote_addr=_remote_addr(handler),
        selected_headers=_selected_headers(headers),
        delay_requested_ms=int(delay_requested_ms),
        delay_applied_ms=int(delay_applied_ms),
        host_identity=_host_identity(Path(repo_root).resolve()),
    )


def _auth_secret_from_request(qs: Mapping[str, List[str]], headers: Dict[str, str]) -> str:
    value = str(headers.get(_SECRET_HEADER) or "").strip()
    if value:
        return value
    return _query_first(qs, "probe_secret")


def _enforce_access(handler: Any, ctx: ProbeContext) -> None:
    if not ctx.config.enabled:
        raise ProbeRequestError(HTTPStatus.NOT_FOUND, "probe_disabled", "assistant probe endpoints are disabled")
    headers = _header_map(getattr(handler, "headers", {}))
    forwarded_present = any(str(headers.get(name) or "").strip() for name in _FORWARDING_HEADER_NAMES)
    local = _is_loopback(ctx.remote_addr)
    mode = ctx.config.auth_mode
    if mode == "off":
        return
    if mode == "localhost_only":
        if local and not forwarded_present:
            return
        raise ProbeRequestError(
            HTTPStatus.FORBIDDEN,
            "auth_denied",
            "assistant probe is limited to direct localhost requests",
        )
    if mode == "localhost_or_secret" and local and not forwarded_present:
        return
    provided = _auth_secret_from_request(ctx.qs, headers)
    if ctx.config.secret and provided == ctx.config.secret:
        return
    if mode == "localhost_or_secret" and not ctx.config.secret:
        raise ProbeRequestError(
            HTTPStatus.UNAUTHORIZED,
            "probe_secret_required",
            "assistant probe requires ASSISTANT_PROBE_SECRET for non-local ingress",
        )
    raise ProbeRequestError(HTTPStatus.UNAUTHORIZED, "auth_denied", "missing or invalid assistant probe secret")


def _request_content_length(handler: Any) -> int:
    try:
        return max(0, int(getattr(handler, "headers", {}).get("Content-Length") or 0))
    except Exception:
        return 0


def _read_echo_body(handler: Any, ctx: ProbeContext) -> bytes:
    announced = _request_content_length(handler)
    ctx.request_size_bytes = int(announced)
    if announced > int(ctx.config.max_body_bytes):
        raise ProbeRequestError(
            HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
            "body_limit_exceeded",
            f"request too large ({announced} bytes > {ctx.config.max_body_bytes} bytes)",
            details={
                "max_body_bytes": int(ctx.config.max_body_bytes),
                "request_size_bytes": int(announced),
            },
        )
    if announced <= 0:
        return b""
    body = handler.rfile.read(announced)
    ctx.request_size_bytes = len(body)
    ctx.request_body_sha256 = hashlib.sha256(body).hexdigest()
    preview = body[: int(ctx.config.max_echo_bytes)]
    ctx.request_body_preview_text = preview.decode("utf-8", errors="replace")
    return body


def _audit_roots(repo_root: Path) -> Tuple[Path, Path]:
    state_root = (repo_root / "runtime" / "state" / "assistant_bridge" / "audit" / "probes").resolve()
    artifact_root = (repo_root / "runtime" / "artifacts" / "assistant_bridge" / "probes").resolve()
    return state_root, artifact_root


def _json_size(obj: Dict[str, Any]) -> int:
    return len(json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))


def _response_payload_size(payload: Dict[str, Any]) -> int:
    return _json_size(payload)


def _build_audit_record(
    ctx: ProbeContext,
    *,
    route_name: str,
    status_code: int,
    classification: str,
    ok: bool,
    response_payload: Dict[str, Any],
    content_type: str = "",
    body_length: int = 0,
) -> Dict[str, Any]:
    finished_utc = _now_utc_iso()
    duration_ms = max(0, int(round((time.perf_counter() - float(ctx.started_perf)) * 1000.0)))
    return {
        "schema_version": PROBE_AUDIT_SCHEMA_VERSION,
        "event_id": f"probe_{_compact_utc_stamp()}_{uuid.uuid4().hex[:8]}",
        "route_version": PROBE_ROUTE_VERSION,
        "route": str(route_name or ""),
        "path": str(ctx.path or ""),
        "method": str(ctx.method or "GET"),
        "request_id": str(ctx.request_id or ""),
        "ingress_tag": str(ctx.ingress_tag or "unspecified"),
        "status_code": int(status_code),
        "ok": bool(ok),
        "classification": str(classification or "success"),
        "started_utc": str(ctx.started_utc or ""),
        "finished_utc": finished_utc,
        "duration_ms": duration_ms,
        "delay_requested_ms": int(ctx.delay_requested_ms or 0),
        "delay_applied_ms": int(ctx.delay_applied_ms or 0),
        "remote_addr": str(ctx.remote_addr or ""),
        "selected_headers": dict(ctx.selected_headers or {}),
        "request_size_bytes": int(ctx.request_size_bytes or 0),
        "request_body_sha256": str(ctx.request_body_sha256 or ""),
        "request_body_preview_text": str(ctx.request_body_preview_text or ""),
        "response_size_bytes": _response_payload_size(response_payload),
        "content_type": str(content_type or ""),
        "body_length": int(body_length or 0),
        "host_identity": dict(ctx.host_identity or {}),
    }


def _persist_audit(repo_root: Path, record: Dict[str, Any]) -> Dict[str, str]:
    state_root, artifact_root = _audit_roots(repo_root)
    event_id = str(record.get("event_id") or f"probe_{uuid.uuid4().hex[:8]}")
    filename = f"{event_id}.json"
    state_path = (state_root / filename).resolve()
    artifact_path = (artifact_root / filename).resolve()
    atomic_write_canonical_json(state_path, record)
    atomic_write_canonical_json(artifact_path, record)
    root = Path(repo_root).resolve()
    return {
        "event_id": event_id,
        "state_relpath": state_path.relative_to(root).as_posix(),
        "artifact_relpath": artifact_path.relative_to(root).as_posix(),
    }


def _with_audit(
    ctx: ProbeContext,
    *,
    route_name: str,
    status_code: int,
    payload: Dict[str, Any],
    classification: str,
    ok: bool,
    content_type: str = "",
    body_length: int = 0,
) -> Tuple[int, Dict[str, Any]]:
    record = _build_audit_record(
        ctx,
        route_name=route_name,
        status_code=status_code,
        classification=classification,
        ok=ok,
        response_payload=payload,
        content_type=content_type,
        body_length=body_length,
    )
    audit_refs: Dict[str, str] = {}
    try:
        audit_refs = _persist_audit(ctx.repo_root, record)
    except Exception as exc:
        audit_refs = {"event_id": str(record.get("event_id") or ""), "error": str(exc)}
    payload = dict(payload)
    payload["audit"] = dict(audit_refs)
    payload["classification"] = str(classification or "success")
    return int(status_code), payload


def _apply_delay(ctx: ProbeContext) -> None:
    if int(ctx.delay_applied_ms or 0) > 0:
        time.sleep(float(ctx.delay_applied_ms) / 1000.0)


def _ping_payload(ctx: ProbeContext) -> Dict[str, Any]:
    return {
        "schema_version": PROBE_ROUTE_VERSION,
        "route": "ping",
        "route_version": PROBE_ROUTE_VERSION,
        "ok": True,
        "request_id": str(ctx.request_id or ""),
        "utc": _now_utc_iso(),
        "ingress_tag": str(ctx.ingress_tag or "unspecified"),
        "delay_requested_ms": int(ctx.delay_requested_ms or 0),
        "delay_applied_ms": int(ctx.delay_applied_ms or 0),
        "selected_headers": dict(ctx.selected_headers or {}),
        "remote_addr": str(ctx.remote_addr or ""),
        "host_identity": dict(ctx.host_identity or {}),
    }


def _echo_payload(ctx: ProbeContext, body: bytes, content_type: str) -> Dict[str, Any]:
    preview = body[: int(ctx.config.max_echo_bytes)]
    preview_text = preview.decode("utf-8", errors="replace")
    return {
        "schema_version": PROBE_ROUTE_VERSION,
        "route": "echo",
        "route_version": PROBE_ROUTE_VERSION,
        "ok": True,
        "request_id": str(ctx.request_id or ""),
        "utc": _now_utc_iso(),
        "ingress_tag": str(ctx.ingress_tag or "unspecified"),
        "delay_requested_ms": int(ctx.delay_requested_ms or 0),
        "delay_applied_ms": int(ctx.delay_applied_ms or 0),
        "content_type": str(content_type or "application/octet-stream"),
        "body_length": len(body),
        "body_sha256": hashlib.sha256(body).hexdigest(),
        "body_preview_text": preview_text,
        "body_preview_truncated": len(body) > len(preview),
        "preview_bytes": len(preview),
        "selected_headers": dict(ctx.selected_headers or {}),
        "remote_addr": str(ctx.remote_addr or ""),
        "host_identity": dict(ctx.host_identity or {}),
    }


def _capabilities_payload(ctx: ProbeContext) -> Dict[str, Any]:
    return {
        "schema_version": PROBE_CAPABILITIES_SCHEMA_VERSION,
        "route": "capabilities",
        "route_version": PROBE_ROUTE_VERSION,
        "ok": True,
        "request_id": str(ctx.request_id or ""),
        "utc": _now_utc_iso(),
        "ingress_tag": str(ctx.ingress_tag or "unspecified"),
        "enabled": bool(ctx.config.enabled),
        "auth_mode": str(ctx.config.auth_mode or ""),
        "secret_configured": bool(ctx.config.secret),
        "max_body_bytes": int(ctx.config.max_body_bytes),
        "max_echo_bytes": int(ctx.config.max_echo_bytes),
        "max_delay_ms": int(ctx.config.max_delay_ms),
        "allowed_ingress_tags": list(ctx.config.allowed_ingress_tags),
        "routes": [
            {
                "path": "/api/assistant/probe/ping",
                "method": "GET",
                "supports_delay_ms": True,
            },
            {
                "path": "/api/assistant/probe/echo",
                "method": "POST",
                "supports_delay_ms": True,
                "supports_body_limit_smoke": True,
            },
            {
                "path": "/api/assistant/probe/capabilities",
                "method": "GET",
                "supports_delay_ms": False,
            },
        ],
    }


def _error_payload(ctx: ProbeContext, route_name: str, exc: ProbeRequestError) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema_version": PROBE_ROUTE_VERSION,
        "route": str(route_name or ""),
        "route_version": PROBE_ROUTE_VERSION,
        "ok": False,
        "request_id": str(ctx.request_id or ""),
        "utc": _now_utc_iso(),
        "ingress_tag": str(ctx.ingress_tag or "unspecified"),
        "error": str(exc),
        "delay_requested_ms": int(ctx.delay_requested_ms or 0),
        "delay_applied_ms": 0,
    }
    payload.update(dict(exc.details or {}))
    return payload


def handle_get_request(handler: Any, *, repo_root: Path, path: str, qs: Mapping[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    route_name = "ping" if path.endswith("/ping") else "capabilities"
    ctx = _build_context(handler, repo_root=repo_root, path=path, method="GET", qs=qs)
    try:
        _enforce_access(handler, ctx)
        if route_name == "ping":
            _apply_delay(ctx)
            payload = _ping_payload(ctx)
            classification = "delayed_response" if int(ctx.delay_applied_ms or 0) > 0 else "success"
            return _with_audit(ctx, route_name=route_name, status_code=HTTPStatus.OK, payload=payload, classification=classification, ok=True)
        payload = _capabilities_payload(ctx)
        return _with_audit(ctx, route_name=route_name, status_code=HTTPStatus.OK, payload=payload, classification="success", ok=True)
    except ProbeRequestError as exc:
        payload = _error_payload(ctx, route_name, exc)
        return _with_audit(ctx, route_name=route_name, status_code=exc.status, payload=payload, classification=exc.classification, ok=False)
    except Exception as exc:  # pragma: no cover - defensive fail-closed guard
        err = ProbeRequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))
        payload = _error_payload(ctx, route_name, err)
        return _with_audit(ctx, route_name=route_name, status_code=err.status, payload=payload, classification=err.classification, ok=False)


def handle_post_request(handler: Any, *, repo_root: Path, path: str, qs: Mapping[str, List[str]]) -> Tuple[int, Dict[str, Any]]:
    route_name = "echo"
    ctx = _build_context(handler, repo_root=repo_root, path=path, method="POST", qs=qs)
    try:
        _enforce_access(handler, ctx)
        body = _read_echo_body(handler, ctx)
        _apply_delay(ctx)
        headers = _header_map(getattr(handler, "headers", {}))
        content_type = str(headers.get("content-type") or "application/octet-stream")
        payload = _echo_payload(ctx, body, content_type)
        classification = "delayed_response" if int(ctx.delay_applied_ms or 0) > 0 else "success"
        return _with_audit(
            ctx,
            route_name=route_name,
            status_code=HTTPStatus.OK,
            payload=payload,
            classification=classification,
            ok=True,
            content_type=content_type,
            body_length=len(body),
        )
    except ProbeRequestError as exc:
        payload = _error_payload(ctx, route_name, exc)
        return _with_audit(ctx, route_name=route_name, status_code=exc.status, payload=payload, classification=exc.classification, ok=False)
    except Exception as exc:  # pragma: no cover - defensive fail-closed guard
        err = ProbeRequestError(HTTPStatus.INTERNAL_SERVER_ERROR, "internal_error", str(exc))
        payload = _error_payload(ctx, route_name, err)
        return _with_audit(ctx, route_name=route_name, status_code=err.status, payload=payload, classification=err.classification, ok=False)


__all__ = [
    "PROBE_ROUTE_VERSION",
    "PROBE_AUDIT_SCHEMA_VERSION",
    "PROBE_CAPABILITIES_SCHEMA_VERSION",
    "handle_get_request",
    "handle_post_request",
]
