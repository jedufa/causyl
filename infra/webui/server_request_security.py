"""Loopback operator HTTP boundary, shared by every route.

Assistant routes retain their own bearer/operation scope checks. Operator
credentials never authorize an assistant operation. Browser sessions are
process-local, bounded, expiring, and invalidated by operator-token rotation.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from http.cookies import SimpleCookie, CookieError
from urllib.parse import parse_qs, urlsplit

SESSION_TTL = 3600
MAX_SESSIONS = 128
_LOCK = threading.Lock()
_COOKIE = "plane_session"
_CONTROL_COOKIE = "plane_control"
_CONTROL_PAGES = {"/mobile", "/admin/mobile", "/debug/mobile"}
_LOGIN = b'''<!doctype html><html lang="en"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in to the plane</title><main><h1>The plane</h1>
<p>Enter the operator token configured for this local server.</p>
<form method="post" action="/api/auth/login">
<label>Operator token <input type="password" name="token" required
 autocomplete="current-password" maxlength="4096"></label>
<button type="submit">Sign in</button></form></main></html>'''


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _reply(handler, status, payload=None, *, location=None, cookie=None, html=False):
    body = payload if html else json.dumps(payload or {"ok": False}).encode()
    handler.close_connection = True
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8" if html else "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    if location:
        handler.send_header("Location", location)
    if cookie:
        for value in cookie if isinstance(cookie, list) else [cookie]:
            handler.send_header("Set-Cookie", value)
    handler.end_headers()
    handler.wfile.write(body)


def _header_values(handler, name):
    if hasattr(handler.headers, "get_all"):
        return handler.headers.get_all(name, [])
    value = handler.headers.get(name)
    return [] if value is None else [value]


def _transport_allowed(handler):
    # Reject ambiguous credentials/framing and proxy headers. This listener is
    # local HTTP; a reverse proxy must not silently expand its trust boundary.
    for name in ("Host", "Origin", "Authorization", "X-Admin-Token", "Cookie",
                 "Content-Length", "Sec-Fetch-Site"):
        if len(_header_values(handler, name)) > 1:
            return False
    if handler.headers.get("Transfer-Encoding"):
        return False
    if any(handler.headers.get(name) for name in (
            "Forwarded", "X-Forwarded-Host", "X-Forwarded-Proto", "X-Forwarded-For")):
        return False
    host = handler.headers.get("Host", "")
    try:
        authority = urlsplit("http://" + host)
        port = authority.port or 80
        bound_port = handler.server.server_address[1]
    except (ValueError, AttributeError, IndexError):
        return False
    if (authority.hostname not in {"localhost", "127.0.0.1", "::1"}
            or authority.username or authority.password or authority.path
            or authority.query or authority.fragment or port != bound_port):
        return False
    origin = handler.headers.get("Origin")
    if origin is not None and origin != "http://" + host:
        return False
    if handler.headers.get("Sec-Fetch-Site", "") not in {"", "same-origin", "none"}:
        return False
    # No alternate URI forms or encoded routing separators before dispatch.
    path = handler.path
    raw_path = path.split("?", 1)[0]
    if (not path.startswith("/") or path.startswith("//") or "\\" in path
            or "#" in path or any(ord(c) < 32 or ord(c) == 127 for c in path)
            or any(x in raw_path.lower() for x in ("%2f", "%5c", "%00"))):
        return False
    return True


def _cookies(handler):
    jar = SimpleCookie()
    try:
        jar.load(handler.headers.get("Cookie", ""))
    except CookieError:
        return {}
    return {k: v.value for k, v in jar.items()}


def operator_authorized(handler, cfg, validate_control=None):
    required = str(getattr(cfg, "admin_token", "") or "").strip()
    if not getattr(cfg, "admin_enabled", True) or not required:
        return False
    supplied = str(handler.headers.get("X-Admin-Token", "") or "").strip()
    if supplied:
        # An explicit invalid credential never falls through to ambient cookies.
        return hmac.compare_digest(_digest(supplied), _digest(required)) or bool(validate_control and validate_control(supplied))
    cookies = _cookies(handler)
    value = cookies.get(_COOKIE, "")
    if value:
        with _LOCK:
            sessions = getattr(getattr(handler, "server", None), "operator_sessions", {})
            record = sessions.get(_digest(value))
            if record and record[0] > time.monotonic() and hmac.compare_digest(record[1], _digest(required)):
                return True
    control = cookies.get(_CONTROL_COOKIE, "")
    return bool(control and validate_control and validate_control(control))


def _session_cookie(name, value, max_age=SESSION_TTL):
    # HTTP is explicitly restricted to loopback. HttpOnly prevents JS access;
    # SameSite plus an exact Origin check protects against browser CSRF.
    return f"{name}={value}; Path=/; HttpOnly; SameSite=Strict; Max-Age={max_age}"


def guard_request(handler, cfg, method, validate_control):
    """Return True only when routing may continue; otherwise send a response."""
    if not _transport_allowed(handler):
        _reply(handler, 403, {"error": "request origin or authority rejected"})
        return False
    url = urlsplit(handler.path)
    path = url.path
    if method == "GET" and path == "/api/health":
        _reply(handler, 200, {"ok": True, "service": "the plane"})
        return False
    if method == "GET" and path == "/login":
        _reply(handler, 200, _LOGIN, html=True)
        return False
    if method == "POST" and path == "/api/auth/login":
        try:
            size = int(handler.headers.get("Content-Length", "0"))
            if not 0 < size <= 8192 or handler.headers.get("Content-Type", "").split(";", 1)[0] != "application/x-www-form-urlencoded":
                raise ValueError("invalid login body")
            fields = parse_qs(handler.rfile.read(size).decode("utf-8"), max_num_fields=2)
            values = fields.get("token", [])
            if len(values) != 1:
                raise ValueError("invalid token field")
            supplied = values[0]
        except (ValueError, UnicodeError):
            _reply(handler, 400, {"error": "invalid login request"})
            return False
        required = str(getattr(cfg, "admin_token", "") or "").strip()
        if not getattr(cfg, "admin_enabled", True) or not required or not hmac.compare_digest(_digest(supplied), _digest(required)):
            _reply(handler, 401, {"error": "invalid operator token"})
            return False
        session = secrets.token_urlsafe(32)
        with _LOCK:
            now = time.monotonic()
            sessions = {k: v for k, v in getattr(handler.server, "operator_sessions", {}).items()
                        if v[0] > now and hmac.compare_digest(v[1], _digest(required))}
            if len(sessions) >= MAX_SESSIONS:
                del sessions[min(sessions, key=lambda k: sessions[k][0])]
            sessions[_digest(session)] = (now + SESSION_TTL, _digest(required))
            handler.server.operator_sessions = sessions
        _reply(handler, 303, location="/", cookie=_session_cookie(_COOKIE, session))
        return False
    if method == "POST" and path == "/api/auth/logout":
        with _LOCK:
            getattr(handler.server, "operator_sessions", {}).pop(_digest(_cookies(handler).get(_COOKIE, "")), None)
        _reply(handler, 200, {"ok": True}, cookie=[_session_cookie(_COOKIE, "", 0),
                                                   _session_cookie(_CONTROL_COOKIE, "", 0)])
        return False
    # Delegation is narrow: the bridge authenticates and checks each operation.
    # Diagnostic probe routes also require the operator gate.
    if path.startswith("/api/assistant/") and not path.startswith("/api/assistant/probe/"):
        return True
    # Exchange an existing scoped control session URL for an HttpOnly cookie.
    # Its parent-token revocation and expiry are rechecked on every request.
    if method == "GET" and path in _CONTROL_PAGES:
        values = parse_qs(url.query).get("token", [])
        if len(values) == 1 and getattr(cfg, "admin_enabled", True) and getattr(cfg, "admin_token", "") and validate_control(values[0]):
            _reply(handler, 303, location=path, cookie=_session_cookie(_CONTROL_COOKIE, values[0]))
            return False
    if operator_authorized(handler, cfg, validate_control):
        return True
    if method == "GET" and "text/html" in handler.headers.get("Accept", ""):
        _reply(handler, 303, location="/login")
    else:
        _reply(handler, 401, {"error": "operator authentication required"})
    return False
