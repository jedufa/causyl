"""Real loopback requests through the production handler, with disposable state."""
from dataclasses import replace
import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

from infra.webui import server_routes_and_main as routes
from infra.webui import server_request_security as security

TOKEN = "fictional-operator-token-for-tests"


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setattr(routes, "_web_surface_enabled", lambda: True)
    monkeypatch.setattr(routes, "_admin_repo_root", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, admin_token=TOKEN)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), routes.WebUIRequestHandler)
    httpd.state = routes._ServerState(cfg)
    run = tmp_path / "example"
    run.mkdir()
    httpd.state.set_run_index_item("example", run, run)
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
    thread.start()
    yield httpd, run
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=3)
    assert not thread.is_alive()


def request(server, method, path, body=None, headers=None):
    httpd, _ = server
    conn = http.client.HTTPConnection(*httpd.server_address, timeout=3)
    conn.request(method, path, body, headers or {})
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return result


@pytest.mark.parametrize("method,path", [
    ("GET", "/api/ledger/items"), ("GET", "/api/schedules/health"),
    ("GET", "/api/admin/fs/list"), ("GET", "/docs/"),
    ("GET", "/static/app.js"), ("GET", "/api/run/example/notes"),
    ("POST", "/api/run/example/notes"), ("POST", "/api/ledger/item/create"),
    ("POST", "/api/action/execute"), ("POST", "/api/admin/cmd/start"),
    ("POST", "/api/assistant/probe/echo"), ("POST", "/unknown-future-route"),
])
def test_every_operator_route_is_closed_before_dispatch(server, method, path):
    status, _, _ = request(server, method, path, b'{}')
    assert status == 401
    assert list(server[1].iterdir()) == []


@pytest.mark.parametrize("headers", [
    {"Origin": "https://attacker.example.invalid"}, {"Origin": "null"},
    {"Sec-Fetch-Site": "cross-site"}, {"Sec-Fetch-Site": "same-site"},
    {"Host": "attacker.example.invalid"}, {"Host": "127.0.0.1:1"},
    {"X-Forwarded-Host": "localhost"}, {"X-Forwarded-Proto": "https"},
])
def test_cross_origin_and_rebinding_cannot_mutate_even_with_token(server, headers):
    status, _, _ = request(server, "POST", "/api/run/example/notes", b'{"text":"bad"}',
                           {"X-Admin-Token": TOKEN, **headers})
    assert status == 403
    assert list(server[1].iterdir()) == []


def test_valid_header_changes_real_notes_and_protects_reads(server):
    headers = {"X-Admin-Token": TOKEN}
    status, _, _ = request(server, "POST", "/api/run/example/notes", b'{"text":"proof"}', headers)
    assert status == 200
    assert (server[1] / "ui/notes.md").read_text().strip() == "proof"
    assert request(server, "GET", "/api/run/example/notes")[0] == 401
    status, _, body = request(server, "GET", "/api/run/example/notes", headers=headers)
    assert status == 200 and json.loads(body)["text"].strip() == "proof"


def login(server):
    host, port = server[0].server_address
    status, headers, _ = request(server, "POST", "/api/auth/login", urlencode({"token": TOKEN}),
        {"Content-Type": "application/x-www-form-urlencoded", "Origin": f"http://{host}:{port}"})
    assert status == 303 and headers["Location"] == "/"
    cookie = headers["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    assert TOKEN not in cookie
    return cookie.split(";", 1)[0]


def test_browser_login_cookie_logout_rotation_and_expiry(server, monkeypatch):
    cookie = login(server)
    headers = {"Cookie": cookie}
    assert request(server, "POST", "/api/run/example/notes", b'{"text":"browser"}', headers)[0] == 200
    assert request(server, "GET", "/api/run/example/notes", headers={**headers, "X-Admin-Token": "wrong"})[0] == 401
    assert request(server, "POST", "/api/auth/logout", headers=headers)[0] == 200
    assert request(server, "GET", "/api/run/example/notes", headers=headers)[0] == 401
    cookie = login(server)
    server[0].state.cfg = replace(server[0].state.cfg, admin_token="rotated-fictional-token")
    assert request(server, "GET", "/api/run/example/notes", headers={"Cookie": cookie})[0] == 401
    server[0].state.cfg = replace(server[0].state.cfg, admin_token=TOKEN)
    cookie = login(server)
    now = security.time.monotonic()
    monkeypatch.setattr(security.time, "monotonic", lambda: now + security.SESSION_TTL + 1)
    assert request(server, "GET", "/api/run/example/notes", headers={"Cookie": cookie})[0] == 401


def test_query_admin_tokens_rejected_and_never_logged(server, capsys):
    assert request(server, "GET", "/api/run/example/notes?token=" + TOKEN)[0] == 401
    assert TOKEN not in capsys.readouterr().err


def test_scoped_browser_exchange_rechecks_revocation(server, monkeypatch):
    active = [True]
    token = "fictional-scoped-session"
    monkeypatch.setattr(routes._assistant_bridge, "validate_control_pane_access_token",
                        lambda root, value: active[0] and value == token)
    status, headers, _ = request(server, "GET", "/admin/mobile?token=" + token)
    assert status == 303 and headers["Location"] == "/admin/mobile"
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    assert request(server, "GET", "/api/run/example/notes", headers={"Cookie": cookie})[0] == 200
    active[0] = False
    assert request(server, "GET", "/api/run/example/notes", headers={"Cookie": cookie})[0] == 401


def test_health_is_minimal_and_html_navigation_reaches_login(server):
    status, headers, body = request(server, "GET", "/api/health")
    assert status == 200 and json.loads(body) == {"ok": True, "service": "the plane"}
    assert headers["Referrer-Policy"] == "no-referrer"
    assert headers["X-Frame-Options"] == "SAMEORIGIN"
    assert request(server, "GET", "/", headers={"Accept": "text/html"})[1]["Location"] == "/login"
    assert b'type="password"' in request(server, "GET", "/login")[2]


@pytest.mark.parametrize("path", ["//attacker.invalid/api/run/example/notes", "http://attacker.invalid/api/run/example/notes", "/api%2frun/example/notes", "/api\\run/example/notes"])
def test_ambiguous_paths_are_rejected(server, path):
    # BaseHTTPRequestHandler normalizes initial // to /; either denial is safe.
    assert request(server, "POST", path, b'{}')[0] in {401, 403}
    assert list(server[1].iterdir()) == []


def test_duplicate_authority_and_credentials_are_rejected(server):
    conn = http.client.HTTPConnection(*server[0].server_address, timeout=3)
    conn.putrequest("POST", "/api/run/example/notes")
    conn.putheader("Host", "attacker.invalid")
    conn.putheader("X-Admin-Token", TOKEN)
    conn.putheader("Content-Length", "2")
    conn.endheaders(b'{}')
    response = conn.getresponse()
    assert response.status == 403
    response.read()
    conn.close()
    assert list(server[1].iterdir()) == []


def test_operator_token_does_not_authorize_assistant_operations(server):
    assert request(server, "POST", "/api/assistant/session/open", b'{}', {"X-Admin-Token": TOKEN})[0] in {401, 403}


def test_empty_or_disabled_operator_credentials_fail_closed(server):
    server[0].state.cfg = replace(server[0].state.cfg, admin_token="")
    assert request(server, "POST", "/api/run/example/notes", b'{}')[0] == 401
    server[0].state.cfg = replace(server[0].state.cfg, admin_token=TOKEN)
    server[0].state.cfg = replace(server[0].state.cfg, admin_enabled=False)
    assert request(server, "POST", "/api/run/example/notes", b'{}', {"X-Admin-Token": TOKEN})[0] == 401
    assert list(server[1].iterdir()) == []
