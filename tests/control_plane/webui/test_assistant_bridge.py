from __future__ import annotations

"""Route coverage for the assistant access bridge trial surface.

Last modified: 2026-04-01 Europe/Vienna
"""

import hashlib
import io
import json
import subprocess
import base64
from pathlib import Path
from types import SimpleNamespace

from infra.controlplane import pis_improvement_entrypoints
from infra.webui import server_http_core as http_core
from infra.webui import server_routes_and_main as routes
from infra.webui.ledger import ledger_storage


class _DummyReq:
    def __init__(
        self,
        *,
        cfg: routes.WebUIConfig,
        path: str,
        body: bytes = b"",
        headers: dict[str, str] | None = None,
        client_addr: str = "127.0.0.1",
    ) -> None:
        merged_headers = {"Content-Length": str(len(body)), "Host": "127.0.0.1:8787"}
        for key, value in dict(headers or {}).items():
            merged_headers[str(key)] = str(value)
        self.path = path
        self.headers = merged_headers
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg), server_address=("127.0.0.1", 8787))
        self.client_address = (client_addr, 8787)
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def _json_body(obj: dict) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def _clear_assistant_env(monkeypatch) -> None:
    for name in (
        "ASSISTANT_API_ENABLED",
        "ASSISTANT_TOKEN_FILE",
        "ASSISTANT_READ_ONLY_MODE",
        "ASSISTANT_DEFAULT_EXECUTION_MODE",
        "ASSISTANT_ALLOW_LIVE_MODE",
        "ASSISTANT_SESSION_TTL_SEC",
        "ASSISTANT_MAX_SESSIONS",
        "ASSISTANT_MAX_DIFF_BYTES",
        "ASSISTANT_MAX_WRITE_FILE_BYTES",
        "ASSISTANT_MAX_STDOUT_BYTES",
        "ASSISTANT_MAX_STDERR_BYTES",
        "ASSISTANT_MAX_ARTIFACT_READ_BYTES",
        "ASSISTANT_CONTROL_PANE_SESSION_TTL_SEC",
        "ASSISTANT_BROWSER_RELAY_TTL_SEC",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ASSISTANT_READ_ONLY_MODE", "false")


def _patch_repo_root(monkeypatch, repo_root: Path) -> None:
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: repo_root)
    monkeypatch.setattr(http_core, "_repo_root_from_webui", lambda: repo_root)
    monkeypatch.setattr(http_core, "_admin_repo_root", lambda: repo_root)


def _write_token_file(
    repo_root: Path,
    *,
    token: str,
    scopes: list[str],
    token_id: str = "assistant-demo",
    allowed_repo_roots: list[str] | None = None,
    allowed_pis_prefixes: list[str] | None = None,
) -> Path:
    path = (repo_root / "runtime" / "secrets" / "assistant_bridge_tokens.json").resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "assistant_bridge_tokens_v1",
        "tokens": [
            {
                "token_id": token_id,
                "token": token,
                "issuer": "pytest",
                "audience": "lab",
                "scopes": list(scopes),
                "created_at": "2026-03-31T23:59:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "allowed_repo_roots": list(allowed_repo_roots or ["."]),
                "allowed_pis_prefixes": list(allowed_pis_prefixes or []),
            }
        ],
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _assistant_headers(token: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    merged = {"Authorization": f"Bearer {token}"}
    for key, value in dict(extra or {}).items():
        merged[str(key)] = str(value)
    return merged


def _init_git_repo(repo_root: Path) -> str:
    repo_root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.email", "pytest@example.com"], cwd=repo_root, check=True)
    subprocess.run(["git", "config", "user.name", "Pytest"], cwd=repo_root, check=True)
    pkg_dir = repo_root / "pkg"
    pkg_dir.mkdir(parents=True, exist_ok=True)
    (pkg_dir / "demo.py").write_text("VALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "pkg/demo.py"], cwd=repo_root, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "initial demo"], cwd=repo_root, check=True)
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo_root, text=True).strip()


def _seed_active_ledger_item(repo_root: Path, *, uid: str, slug: str, title: str, include_event: bool = False) -> Path:
    active_root = repo_root / "governance" / "ledger" / "patch_instruction_sheets"
    item_dir = ledger_storage.ensure_item_dir(
        active_root,
        uid=uid,
        slug=slug,
        status="active",
        created_utc="2026-03-31T23:59:00Z",
    )
    ledger_storage.atomic_write_json(
        item_dir / "meta.json",
        {
            "uid": uid,
            "slug": slug,
            "title": title,
            "status": "active",
            "release_state": "draft",
            "created_utc": "2026-03-31T23:59:00Z",
            "updated_utc": "2026-03-31T23:59:30Z",
            "tags": ["assistant", "bridge"],
            "topic_tags": ["control-plane"],
        },
    )
    ledger_storage.atomic_write_text(item_dir / "pis.md", f"# {uid}\n\n{title}\n")
    if include_event:
        (item_dir / "events.jsonl").write_text(
            json.dumps({"type": "created", "utc": "2026-03-31T23:59:00Z"}) + "\n",
            encoding="utf-8",
        )
    return item_dir


def _bridge_cfg(repo_root: Path, *, admin_token: str = "") -> routes.WebUIConfig:
    return routes.WebUIConfig(runs_root=repo_root, static_dir=repo_root, admin_token=admin_token)


def _mint_browser_relay(cfg: routes.WebUIConfig, token: str, *, operation: str, params: dict | None = None) -> dict:
    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/auth/browser_relay_url",
        body=_json_body({"operation": operation, "params": dict(params or {})}),
        headers=_assistant_headers(token, {"Host": "127.0.0.1:8787"}),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    assert req._status == 200, _payload(req)
    return _payload(req)


def _execute_browser_relay(cfg: routes.WebUIConfig, relay_url: str) -> _DummyReq:
    relay_path = str(relay_url).replace("http://127.0.0.1:8787", "", 1)
    req = _DummyReq(cfg=cfg, path=relay_path)
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]
    return req


def _payload_b64(obj: dict) -> str:
    raw = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")


def _open_session(cfg: routes.WebUIConfig, token: str, *, base_commit_sha: str) -> dict:
    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/open",
        body=_json_body({"idempotency_key": f"open-{base_commit_sha[:8]}", "base_commit_sha": base_commit_sha}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    assert req._status == 200, _payload(req)
    return _payload(req)


def test_assistant_auth_status_requires_valid_bearer_and_reports_scopes(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    token = "assistant-status-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["repo.read", "ledger.read", "control_pane.access"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    req = _DummyReq(cfg=cfg, path="/api/assistant/auth/status")
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]
    assert req._status == 401
    assert _payload(req)["error_code"] == "missing_bearer_token"

    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/auth/status",
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["assistant_api_enabled"] is True
    assert payload["read_only_mode"] is False
    assert payload["token"]["token_id"] == "assistant-demo"
    assert payload["token"]["issuer"] == "pytest"
    assert set(payload["token"]["scopes"]) == {"control_pane.access", "ledger.read", "repo.read"}
    assert "/admin/mobile" in payload["control_pane_paths"]


def test_read_only_mode_allows_file_read_and_denies_session_open(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    monkeypatch.setenv("ASSISTANT_READ_ONLY_MODE", "true")
    head_sha = _init_git_repo(tmp_path)
    token = "assistant-read-only-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["repo.read", "ledger.read", "session.open"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    read_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/file/read",
        body=_json_body({"idempotency_key": "file-read", "path": "pkg/demo.py"}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(read_req)  # type: ignore[arg-type]
    assert read_req._status == 200, _payload(read_req)
    read_payload = _payload(read_req)
    assert read_payload["ok"] is True
    assert read_payload["path"] == "pkg/demo.py"
    assert "VALUE = 1" in read_payload["content_text"]

    open_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/open",
        body=_json_body({"idempotency_key": "session-open", "base_commit_sha": head_sha}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(open_req)  # type: ignore[arg-type]
    assert open_req._status == 403, _payload(open_req)
    open_payload = _payload(open_req)
    assert open_payload["error_code"] == "assistant_read_only_mode"


def test_browser_relay_file_read_returns_repo_content(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    _init_git_repo(tmp_path)
    token = "assistant-file-read-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["repo.read"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    relay_payload = _mint_browser_relay(cfg, token, operation="file_read", params={"path": "pkg/demo.py"})
    relay_req = _execute_browser_relay(cfg, relay_payload["relay_url"])
    assert relay_req._status == 200, _payload(relay_req)
    payload = _payload(relay_req)
    assert payload["browser_relay"]["operation"] == "file_read"
    assert payload["path"] == "pkg/demo.py"
    assert "VALUE = 1" in payload["content_text"]


def test_control_pane_session_exchange_returns_browser_url_and_admin_guard_accepts_token(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    token = "assistant-control-pane-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["control_pane.access"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path, admin_token="real-admin-token")

    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/auth/control_pane_session",
        body=_json_body({"path": "/admin/mobile"}),
        headers=_assistant_headers(token, {"Host": "127.0.0.1:8787"}),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["control_pane_path"] == "/admin/mobile"
    assert payload["control_pane_url"].startswith("http://127.0.0.1:8787/admin/mobile?token=abrctl_")
    session_token = payload["access_token"]

    admin_req = _DummyReq(cfg=cfg, path="/api/admin/config", headers={"X-Admin-Token": session_token})
    assert http_core._admin_guard(admin_req, cfg, {"token": [session_token]}) is True

    denied_req = _DummyReq(cfg=cfg, path="/api/admin/config?token=bad-token")
    assert http_core._admin_guard(denied_req, cfg, {"token": ["bad-token"]}) is False
    assert denied_req._status == 401
    assert _payload(denied_req)["error"] == "missing/invalid admin token"


def test_control_pane_session_is_revoked_when_parent_assistant_token_disappears(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    token = "assistant-control-pane-revocation"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["control_pane.access"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path, admin_token="real-admin-token")

    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/auth/control_pane_session",
        body=_json_body({"path": "/admin/mobile"}),
        headers=_assistant_headers(token, {"Host": "127.0.0.1:8787"}),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    assert req._status == 200
    session_token = _payload(req)["access_token"]

    token_file.write_text(json.dumps({"schema_version": "assistant_bridge_tokens_v1", "tokens": []}, ensure_ascii=False, indent=2), encoding="utf-8")

    denied_req = _DummyReq(cfg=cfg, path=f"/api/admin/config?token={session_token}")
    assert http_core._admin_guard(denied_req, cfg, {"token": [session_token]}) is False
    assert denied_req._status == 401
    assert _payload(denied_req)["error"] == "missing/invalid admin token"


def test_browser_relay_auth_status_works_without_bearer_header(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    token = "assistant-browser-relay-auth-status"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["repo.read", "ledger.read", "control_pane.access"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    mint_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/auth/browser_relay_url",
        body=_json_body({"operation": "auth_status"}),
        headers=_assistant_headers(token, {"Host": "127.0.0.1:8787"}),
    )
    routes.WebUIRequestHandler.do_POST(mint_req)  # type: ignore[arg-type]

    assert mint_req._status == 200
    mint_payload = _payload(mint_req)
    relay_url = mint_payload["relay_url"]
    relay_path = relay_url.replace("http://127.0.0.1:8787", "", 1)

    relay_req = _DummyReq(cfg=cfg, path=relay_path)
    routes.WebUIRequestHandler.do_GET(relay_req)  # type: ignore[arg-type]

    assert relay_req._status == 200
    relay_payload = _payload(relay_req)
    assert relay_payload["assistant_api_enabled"] is True
    assert relay_payload["token"]["token_id"] == "assistant-demo"
    assert relay_payload["browser_relay"]["operation"] == "auth_status"


def test_browser_relay_repo_search_rejects_tampered_query_params(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    _init_git_repo(tmp_path)
    token = "assistant-browser-relay-search"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["repo.read"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    mint_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/auth/browser_relay_url",
        body=_json_body({"operation": "repo_search", "params": {"query": "VALUE = 1", "roots": ["pkg"]}}),
        headers=_assistant_headers(token, {"Host": "127.0.0.1:8787"}),
    )
    routes.WebUIRequestHandler.do_POST(mint_req)  # type: ignore[arg-type]

    assert mint_req._status == 200
    mint_payload = _payload(mint_req)
    relay_path = mint_payload["relay_url"].replace("http://127.0.0.1:8787", "", 1)

    relay_req = _DummyReq(cfg=cfg, path=relay_path)
    routes.WebUIRequestHandler.do_GET(relay_req)  # type: ignore[arg-type]

    assert relay_req._status == 200
    relay_payload = _payload(relay_req)
    assert relay_payload["result_count"] == 1
    assert relay_payload["results"][0]["path"] == "pkg/demo.py"
    assert relay_payload["browser_relay"]["operation"] == "repo_search"

    tampered_req = _DummyReq(
        cfg=cfg,
        path=relay_path.replace("query=VALUE+%3D+1", "query=missing-query"),
    )
    routes.WebUIRequestHandler.do_GET(tampered_req)  # type: ignore[arg-type]

    assert tampered_req._status == 403
    assert _payload(tampered_req)["error_code"] == "browser_relay_params_mismatch"


def test_pis_improvement_trigger_route_returns_shared_envelope(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    token = "assistant-pis-improvement-trigger"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["ledger.read", "session.open"],
    )
    _seed_active_ledger_item(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
    )
    item_dir = _seed_active_ledger_item(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
    )
    ledger_storage.atomic_write_json(
        item_dir / "meta.json",
        {
            "uid": "PIS-20260412-062101Z-B2D4",
            "slug": "candidate",
            "title": "Structured critique contract",
            "status": "verified",
            "release_state": "draft",
            "created_utc": "2026-04-12T06:21:01Z",
            "updated_utc": "2026-04-12T06:21:01Z",
            "tags": ["assistant", "bridge"],
            "topic_tags": ["control-plane"],
        },
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/pis_improvement/trigger",
        body=_json_body(
            {
                "idempotency_key": "pis-improvement-1",
                "request": {
                    "schema_version": "pis_improvement_entrypoint_request_v1",
                    "action": "launch",
                    "request_id": "pis_request_001",
                    "source_pis_uid": "PIS-20260412-060451Z-EB85",
                },
            }
        ),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200, _payload(req)
    payload = _payload(req)
    assert payload["ok"] is True
    assert payload["action"] == "launch"
    assert payload["source_pis"]["uid"] == "PIS-20260412-060451Z-EB85"
    assert payload["selected_candidate_uids"] == ["PIS-20260412-062101Z-B2D4"]
    assert payload["artifacts"]["session_manifest"].startswith("runtime/live/pis_improvement/sessions/")


def test_pis_improvement_trigger_route_matches_shared_handler(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    token = "assistant-pis-improvement-trigger-match"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["ledger.read", "session.open"],
    )
    _seed_active_ledger_item(
        tmp_path,
        uid="PIS-20260412-060451Z-EB85",
        slug="parent",
        title="PIS Improvement Automation",
    )
    item_dir = _seed_active_ledger_item(
        tmp_path,
        uid="PIS-20260412-062101Z-B2D4",
        slug="candidate",
        title="Structured critique contract",
    )
    ledger_storage.atomic_write_json(
        item_dir / "meta.json",
        {
            "uid": "PIS-20260412-062101Z-B2D4",
            "slug": "candidate",
            "title": "Structured critique contract",
            "status": "verified",
            "release_state": "draft",
            "created_utc": "2026-04-12T06:21:01Z",
            "updated_utc": "2026-04-12T06:21:01Z",
            "tags": ["assistant", "bridge"],
            "topic_tags": ["control-plane"],
        },
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)
    request_payload = {
        "schema_version": "pis_improvement_entrypoint_request_v1",
        "action": "launch",
        "request_id": "pis_request_002",
        "source_pis_uid": "PIS-20260412-060451Z-EB85",
    }

    expected = pis_improvement_entrypoints.run_request(
        dict(request_payload),
        repo_root=tmp_path,
    )
    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/pis_improvement/trigger",
        body=_json_body(
            {
                "idempotency_key": "pis-improvement-2",
                "request": request_payload,
            }
        ),
        headers=_assistant_headers(token),
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200, _payload(req)
    payload = _payload(req)
    assert payload["boundary_result"]["command_id"] == "assistant.pis_improvement_trigger"
    for key, value in expected.items():
        assert payload[key] == value


def test_repo_search_route_returns_match_and_replays_duplicate_receipt(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    _init_git_repo(tmp_path)
    token = "assistant-search-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["repo.read"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)
    body = {"request_id": "search-req-1", "idempotency_key": "search-idem-1", "query": "VALUE = 1"}

    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/repo/search",
        body=_json_body(body),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["ok"] is True
    assert payload["result_count"] == 1
    assert payload["results"][0]["path"] == "pkg/demo.py"
    assert payload["results"][0]["preview"] == "VALUE = 1"
    assert payload["boundary_result"]["request_id"] == "search-req-1"

    receipts = list((tmp_path / "runtime" / "state" / "assistant_bridge" / "audit" / "commands").glob("*.json"))
    assert len(receipts) == 1
    receipt = json.loads(receipts[0].read_text(encoding="utf-8"))
    assert receipt["command_id"] == "assistant.repo_search"
    assert receipt["status"] == "succeeded"

    replay = _DummyReq(
        cfg=cfg,
        path="/api/assistant/repo/search",
        body=_json_body(body),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(replay)  # type: ignore[arg-type]

    assert replay._status == 200
    replay_payload = _payload(replay)
    assert replay_payload["duplicate_suppressed"] is True
    assert replay_payload["replayed_from_receipt"] is True
    assert replay_payload["results"][0]["path"] == "pkg/demo.py"


def test_ledger_list_and_read_routes_respect_allowed_pis_prefixes(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    allowed_uid = "PIS-20260331-235500Z-T8M3"
    blocked_uid = "PIS-20260331-235900Z-A1Q4"
    _seed_active_ledger_item(tmp_path, uid=allowed_uid, slug="assistant-access-bridge-trial-surface", title="Allowed bridge item", include_event=True)
    _seed_active_ledger_item(tmp_path, uid=blocked_uid, slug="assistant-ingress-probe-transport-contract", title="Blocked probe item")
    token = "assistant-ledger-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["ledger.read"],
        allowed_pis_prefixes=[allowed_uid],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    list_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/ledger/list_active",
        body=_json_body({"idempotency_key": "ledger-list-1"}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(list_req)  # type: ignore[arg-type]

    assert list_req._status == 200
    list_payload = _payload(list_req)
    assert [row["uid"] for row in list_payload["items"]] == [allowed_uid]

    read_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/ledger/read",
        body=_json_body({"idempotency_key": "ledger-read-1", "uid": allowed_uid, "include_events": True}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(read_req)  # type: ignore[arg-type]

    assert read_req._status == 200
    read_payload = _payload(read_req)
    assert read_payload["uid"] == allowed_uid
    assert read_payload["meta"]["title"] == "Allowed bridge item"
    assert read_payload["events"][0]["type"] == "created"

    blocked_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/ledger/read",
        body=_json_body({"idempotency_key": "ledger-read-2", "uid": blocked_uid}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(blocked_req)  # type: ignore[arg-type]

    assert blocked_req._status == 403
    blocked_payload = _payload(blocked_req)
    assert blocked_payload["error_code"] == "forbidden_pis_uid"


def test_session_lifecycle_open_apply_validate_artifacts_and_close(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    base_commit = _init_git_repo(tmp_path)
    token = "assistant-session-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["session.open", "diff.apply", "validation.run", "artifacts.read"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    open_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/open",
        body=_json_body({"idempotency_key": "session-open-1", "base_commit_sha": base_commit}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(open_req)  # type: ignore[arg-type]

    assert open_req._status == 200
    open_payload = _payload(open_req)
    session = open_payload["session"]
    session_id = session["session_id"]
    worktree_path = Path(session["worktree_path"])
    assert session["execution_mode"] == "isolated"
    assert worktree_path.exists()

    diff_text = "".join(
        [
            "diff --git a/pkg/demo.py b/pkg/demo.py\n",
            "--- a/pkg/demo.py\n",
            "+++ b/pkg/demo.py\n",
            "@@ -1 +1 @@\n",
            "-VALUE = 1\n",
            "+VALUE = 2\n",
        ]
    )
    apply_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/apply_diff",
        body=_json_body(
            {
                "idempotency_key": "session-apply-1",
                "session_id": session_id,
                "base_commit_sha": base_commit,
                "diff_text": diff_text,
                "diff_hash": hashlib.sha256(diff_text.encode("utf-8")).hexdigest(),
                "expected_changed_files": ["pkg/demo.py"],
            }
        ),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(apply_req)  # type: ignore[arg-type]

    assert apply_req._status == 200
    apply_payload = _payload(apply_req)
    assert apply_payload["ok"] is True
    assert apply_payload["changed_files"] == ["pkg/demo.py"]
    apply_run_id = apply_payload["run_id"]
    assert (worktree_path / "pkg" / "demo.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    validate_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/validate",
        body=_json_body(
            {
                "idempotency_key": "session-validate-1",
                "session_id": session_id,
                "validation": {"kind": "py_compile", "paths": ["pkg/demo.py"]},
            }
        ),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(validate_req)  # type: ignore[arg-type]

    assert validate_req._status == 200
    validate_payload = _payload(validate_req)
    assert validate_payload["ok"] is True
    validate_run_id = validate_payload["run_id"]
    assert validate_payload["validation_result"]["exit_code"] == 0

    list_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/artifacts/list",
        body=_json_body({"idempotency_key": "session-artifacts-list-1", "session_id": session_id, "run_id": validate_run_id}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(list_req)  # type: ignore[arg-type]

    assert list_req._status == 200
    list_payload = _payload(list_req)
    artifact_paths = {row["path"] for row in list_payload["artifacts"]}
    assert any(path.endswith("validation_result.json") for path in artifact_paths)
    assert any(path.endswith("stdout.txt") for path in artifact_paths)

    artifact_ref = next(path for path in artifact_paths if path.endswith("validation_result.json"))
    read_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/artifacts/read",
        body=_json_body({"idempotency_key": "session-artifacts-read-1", "session_id": session_id, "artifact_ref": artifact_ref}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(read_req)  # type: ignore[arg-type]

    assert read_req._status == 200
    read_payload = _payload(read_req)
    assert read_payload["encoding"] == "utf-8"
    assert '"exit_code":0' in read_payload["content_text"]

    close_req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/session/close",
        body=_json_body({"idempotency_key": "session-close-1", "session_id": session_id, "closed_reason": "pytest_done"}),
        headers=_assistant_headers(token),
    )
    routes.WebUIRequestHandler.do_POST(close_req)  # type: ignore[arg-type]

    assert close_req._status == 200
    close_payload = _payload(close_req)
    assert close_payload["session"]["status"] == "closed"
    assert close_payload["cleanup"]["ok"] is True
    assert close_payload["cleanup"]["removed"] is True
    assert not worktree_path.exists()
    assert apply_run_id in close_payload["session"]["run_ids"]
    assert validate_run_id in close_payload["session"]["run_ids"]


def test_browser_relay_write_read_delete_are_single_use_and_session_scoped(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    base_commit = _init_git_repo(tmp_path)
    token = "assistant-browser-relay-write-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["session.open", "repo.write", "repo.read", "artifacts.read"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    session_payload = _open_session(cfg, token, base_commit_sha=base_commit)
    session_id = session_payload["session"]["session_id"]
    worktree_path = Path(session_payload["session"]["worktree_path"])

    write_mint = _mint_browser_relay(
        cfg,
        token,
        operation="write_file",
        params={
            "session_id": session_id,
            "path": "pkg/generated_remote.py",
            "content": "VALUE = 7\n",
            "mode": "create",
        },
    )
    assert write_mint["relay_mode"] == "stored_payload"
    assert write_mint["params"]["content_bytes"] == len("VALUE = 7\n".encode("utf-8"))

    write_req = _execute_browser_relay(cfg, write_mint["relay_url"])
    assert write_req._status == 200
    write_payload = _payload(write_req)
    assert write_payload["ok"] is True
    assert write_payload["write_result"]["path"] == "pkg/generated_remote.py"
    assert write_payload["write_result"]["sha256"] == hashlib.sha256(b"VALUE = 7\n").hexdigest()
    assert (worktree_path / "pkg" / "generated_remote.py").read_text(encoding="utf-8") == "VALUE = 7\n"

    write_replay_req = _execute_browser_relay(cfg, write_mint["relay_url"])
    assert write_replay_req._status == 200
    write_replay_payload = _payload(write_replay_req)
    assert write_replay_payload["duplicate_suppressed"] is True
    assert write_replay_payload["replayed_from_browser_relay"] is True
    assert write_replay_payload["run_id"] == write_payload["run_id"]
    assert write_replay_payload["browser_relay"]["replayed"] is True

    read_mint = _mint_browser_relay(
        cfg,
        token,
        operation="artifact",
        params={"session_id": session_id, "path": "pkg/generated_remote.py"},
    )
    read_req = _execute_browser_relay(cfg, read_mint["relay_url"])
    assert read_req._status == 200
    read_payload = _payload(read_req)
    assert read_payload["source"] == "session_file"
    assert read_payload["path"] == "pkg/generated_remote.py"
    assert read_payload["encoding"] == "utf-8"
    assert read_payload["content_text"] == "VALUE = 7\n"

    delete_mint = _mint_browser_relay(
        cfg,
        token,
        operation="delete_file",
        params={"session_id": session_id, "path": "pkg/generated_remote.py"},
    )
    delete_req = _execute_browser_relay(cfg, delete_mint["relay_url"])
    assert delete_req._status == 200
    delete_payload = _payload(delete_req)
    assert delete_payload["ok"] is True
    assert delete_payload["deleted"] is True
    assert not (worktree_path / "pkg" / "generated_remote.py").exists()

    delete_replay_req = _execute_browser_relay(cfg, delete_mint["relay_url"])
    assert delete_replay_req._status == 200
    delete_replay_payload = _payload(delete_replay_req)
    assert delete_replay_payload["duplicate_suppressed"] is True
    assert delete_replay_payload["run_id"] == delete_payload["run_id"]


def test_browser_relay_session_open_and_close_work_without_bearer_header(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    base_commit = _init_git_repo(tmp_path)
    token = "assistant-browser-relay-session-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["session.open"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    open_mint = _mint_browser_relay(
        cfg,
        token,
        operation="session_open",
        params={"base_commit_sha": base_commit, "execution_mode": "isolated"},
    )
    assert open_mint["relay_mode"] == "stored_payload"
    open_req = _execute_browser_relay(cfg, open_mint["relay_url"])
    assert open_req._status == 200
    open_payload = _payload(open_req)
    session = open_payload["session"]
    session_id = session["session_id"]
    worktree_path = Path(session["worktree_path"])
    assert session["execution_mode"] == "isolated"
    assert worktree_path.exists()

    close_mint = _mint_browser_relay(
        cfg,
        token,
        operation="session_close",
        params={"session_id": session_id, "closed_reason": "browser_relay_done"},
    )
    assert close_mint["relay_mode"] == "stored_payload"
    close_req = _execute_browser_relay(cfg, close_mint["relay_url"])
    assert close_req._status == 200
    close_payload = _payload(close_req)
    assert close_payload["session"]["status"] == "closed"
    assert close_payload["cleanup"]["ok"] is True
    assert close_payload["cleanup"]["removed"] is True
    assert not worktree_path.exists()

    close_replay_req = _execute_browser_relay(cfg, close_mint["relay_url"])
    assert close_replay_req._status == 200
    close_replay_payload = _payload(close_replay_req)
    assert close_replay_payload["duplicate_suppressed"] is True
    assert close_replay_payload["replayed_from_browser_relay"] is True


def test_session_execute_can_run_followup_operations_without_bearer_header(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    base_commit = _init_git_repo(tmp_path)
    token = "assistant-session-mint-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["session.open", "repo.write", "artifacts.read"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    open_mint = _mint_browser_relay(
        cfg,
        token,
        operation="session_open",
        params={"base_commit_sha": base_commit, "execution_mode": "isolated"},
    )
    open_req = _execute_browser_relay(cfg, open_mint["relay_url"])
    assert open_req._status == 200
    open_payload = _payload(open_req)
    session_id = open_payload["session"]["session_id"]
    worktree_path = Path(open_payload["session"]["worktree_path"])
    session_mint_token = open_payload["session_mint"]["access_token"]

    write_req = _DummyReq(
        cfg=cfg,
        path=(
                "/api/assistant/browser_relay/session_execute"
                f"?access_token={session_mint_token}"
                "&operation=write_file"
                "&payload_b64=" + _payload_b64({'path': 'pkg/from_mint.py', 'content': 'VALUE = 9\n', 'mode': 'create'})
            ),
        )
    routes.WebUIRequestHandler.do_GET(write_req)  # type: ignore[arg-type]
    assert write_req._status == 200
    write_payload = _payload(write_req)
    assert write_payload["write_result"]["path"] == "pkg/from_mint.py"
    assert (worktree_path / "pkg" / "from_mint.py").read_text(encoding="utf-8") == "VALUE = 9\n"

    close_req = _DummyReq(
        cfg=cfg,
        path=(
            "/api/assistant/browser_relay/session_execute"
            f"?access_token={session_mint_token}"
            "&operation=session_close"
        ),
    )
    routes.WebUIRequestHandler.do_GET(close_req)  # type: ignore[arg-type]
    assert close_req._status == 200
    close_payload = _payload(close_req)
    assert close_payload["session"]["status"] == "closed"
    assert close_payload["cleanup"]["removed"] is True
    assert not worktree_path.exists()


def test_browser_relay_end_to_end_patch_validate_artifact_and_invalid_diff(tmp_path: Path, monkeypatch) -> None:
    _clear_assistant_env(monkeypatch)
    base_commit = _init_git_repo(tmp_path)
    token = "assistant-browser-relay-e2e-token"
    token_file = _write_token_file(
        tmp_path,
        token=token,
        scopes=["session.open", "repo.write", "repo.read", "validation.run", "diff.apply", "artifacts.read"],
    )
    monkeypatch.setenv("ASSISTANT_API_ENABLED", "true")
    monkeypatch.setenv("ASSISTANT_TOKEN_FILE", str(token_file))
    _patch_repo_root(monkeypatch, tmp_path)
    cfg = _bridge_cfg(tmp_path)

    session_payload = _open_session(cfg, token, base_commit_sha=base_commit)
    session_id = session_payload["session"]["session_id"]
    worktree_path = Path(session_payload["session"]["worktree_path"])

    test_file = "".join(
        [
            "from pathlib import Path\n\n",
            "def test_demo_value() -> None:\n",
            "    namespace = {}\n",
            "    exec(Path('pkg/demo.py').read_text(encoding='utf-8'), namespace)\n",
            "    assert namespace['VALUE'] == 2\n",
        ]
    )
    write_test_mint = _mint_browser_relay(
        cfg,
        token,
        operation="write_file",
        params={
            "session_id": session_id,
            "path": "tests/test_demo_value.py",
            "content": test_file,
            "mode": "create",
        },
    )
    write_test_req = _execute_browser_relay(cfg, write_test_mint["relay_url"])
    assert write_test_req._status == 200
    assert (worktree_path / "tests" / "test_demo_value.py").exists()

    py_compile_mint = _mint_browser_relay(
        cfg,
        token,
        operation="run_validation",
        params={
            "session_id": session_id,
            "validation": {"kind": "py_compile", "paths": ["tests/test_demo_value.py", "pkg/demo.py"]},
        },
    )
    py_compile_req = _execute_browser_relay(cfg, py_compile_mint["relay_url"])
    assert py_compile_req._status == 200
    py_compile_payload = _payload(py_compile_req)
    assert py_compile_payload["ok"] is True
    assert py_compile_payload["validation_result"]["exit_code"] == 0

    diff_text = "".join(
        [
            "diff --git a/pkg/demo.py b/pkg/demo.py\n",
            "--- a/pkg/demo.py\n",
            "+++ b/pkg/demo.py\n",
            "@@ -1 +1 @@\n",
            "-VALUE = 1\n",
            "+VALUE = 2\n",
        ]
    )
    apply_mint = _mint_browser_relay(
        cfg,
        token,
        operation="apply_diff",
        params={
            "session_id": session_id,
            "base_commit_sha": base_commit,
            "diff": diff_text,
            "expected_changed_files": ["pkg/demo.py"],
        },
    )
    assert apply_mint["params"]["diff_sha256"] == hashlib.sha256(diff_text.encode("utf-8")).hexdigest()
    apply_req = _execute_browser_relay(cfg, apply_mint["relay_url"])
    assert apply_req._status == 200
    apply_payload = _payload(apply_req)
    assert apply_payload["changed_files"] == ["pkg/demo.py"]
    assert (worktree_path / "pkg" / "demo.py").read_text(encoding="utf-8") == "VALUE = 2\n"

    git_diff_mint = _mint_browser_relay(
        cfg,
        token,
        operation="run_validation",
        params={
            "session_id": session_id,
            "validation": {"kind": "git_diff", "paths": ["pkg/demo.py"]},
        },
    )
    git_diff_req = _execute_browser_relay(cfg, git_diff_mint["relay_url"])
    assert git_diff_req._status == 200
    git_diff_payload = _payload(git_diff_req)
    assert git_diff_payload["validation_result"]["exit_code"] == 0
    assert "+VALUE = 2" in git_diff_payload["validation_result"]["stdout_preview"]

    pytest_mint = _mint_browser_relay(
        cfg,
        token,
        operation="run_validation",
        params={
            "session_id": session_id,
            "validation": {"kind": "pytest", "paths": ["tests/test_demo_value.py"]},
        },
    )
    pytest_req = _execute_browser_relay(cfg, pytest_mint["relay_url"])
    assert pytest_req._status == 200
    pytest_payload = _payload(pytest_req)
    assert pytest_payload["ok"] is True
    assert pytest_payload["validation_result"]["exit_code"] == 0

    validation_artifact_ref = next(
        path
        for path in pytest_payload["artifact_refs"]
        if path.endswith(f"{pytest_payload['run_id']}/validation_result.json")
    )
    artifact_mint = _mint_browser_relay(
        cfg,
        token,
        operation="artifact",
        params={"session_id": session_id, "path": validation_artifact_ref},
    )
    artifact_req = _execute_browser_relay(cfg, artifact_mint["relay_url"])
    assert artifact_req._status == 200
    artifact_payload = _payload(artifact_req)
    assert artifact_payload["artifact_ref"] == validation_artifact_ref
    assert artifact_payload["encoding"] == "utf-8"
    assert '"exit_code": 0' not in artifact_payload["content_text"]
    assert '"exit_code":0' in artifact_payload["content_text"]

    read_demo_mint = _mint_browser_relay(
        cfg,
        token,
        operation="artifact",
        params={"session_id": session_id, "path": "pkg/demo.py"},
    )
    read_demo_req = _execute_browser_relay(cfg, read_demo_mint["relay_url"])
    assert read_demo_req._status == 200
    read_demo_payload = _payload(read_demo_req)
    assert read_demo_payload["source"] == "session_file"
    assert read_demo_payload["content_text"] == "VALUE = 2\n"

    invalid_diff_mint = _mint_browser_relay(
        cfg,
        token,
        operation="apply_diff",
        params={
            "session_id": session_id,
            "base_commit_sha": base_commit,
            "diff": "not a diff\n",
            "expected_changed_files": ["pkg/demo.py"],
        },
    )
    invalid_diff_req = _execute_browser_relay(cfg, invalid_diff_mint["relay_url"])
    assert invalid_diff_req._status == 400
    invalid_diff_payload = _payload(invalid_diff_req)
    assert invalid_diff_payload["ok"] is False
    assert invalid_diff_payload["error_code"] == "diff_missing_paths"
    assert invalid_diff_payload["browser_relay"]["single_use"] is True

    invalid_diff_replay_req = _execute_browser_relay(cfg, invalid_diff_mint["relay_url"])
    assert invalid_diff_replay_req._status == 400
    invalid_diff_replay_payload = _payload(invalid_diff_replay_req)
    assert invalid_diff_replay_payload["duplicate_suppressed"] is True
    assert invalid_diff_replay_payload["replayed_from_browser_relay"] is True
