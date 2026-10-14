from __future__ import annotations

"""Route coverage for the assistant ingress probe transport contract.

Last modified: 2026-03-31 Europe/Vienna
"""

import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace

from infra.webui import server_routes_and_main as routes


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
        merged_headers = {"Content-Length": str(len(body))}
        for key, value in dict(headers or {}).items():
            merged_headers[str(key)] = str(value)
        self.path = path
        self.headers = merged_headers
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
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


def _clear_probe_env(monkeypatch) -> None:
    for name in (
        "ASSISTANT_PROBE_ENABLED",
        "ASSISTANT_PROBE_AUTH_MODE",
        "ASSISTANT_PROBE_SECRET",
        "ASSISTANT_PROBE_MAX_BODY_BYTES",
        "ASSISTANT_PROBE_MAX_ECHO_BYTES",
        "ASSISTANT_PROBE_MAX_DELAY_MS",
        "ASSISTANT_PROBE_ALLOWED_INGRESS_TAGS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_assistant_probe_ping_route(tmp_path: Path, monkeypatch) -> None:
    _clear_probe_env(monkeypatch)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    req = _DummyReq(cfg=cfg, path="/api/assistant/probe/ping?ingress_tag=direct_https")
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["route"] == "ping"
    assert payload["ingress_tag"] == "direct_https"
    assert payload["classification"] == "success"
    assert payload["host_identity"]["hostname"]

    state_path = tmp_path / payload["audit"]["state_relpath"]
    artifact_path = tmp_path / payload["audit"]["artifact_relpath"]
    assert state_path.exists()
    assert artifact_path.exists()

    audit = json.loads(state_path.read_text(encoding="utf-8"))
    assert audit["route"] == "ping"
    assert audit["classification"] == "success"
    assert audit["ingress_tag"] == "direct_https"


def test_assistant_probe_echo_roundtrip(tmp_path: Path, monkeypatch) -> None:
    _clear_probe_env(monkeypatch)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)
    body = b'{"probe":"echo","ok":true}'
    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/probe/echo?ingress_tag=ngrok",
        body=body,
        headers={"Content-Type": "application/json", "X-Request-Id": "probe-echo-001"},
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["route"] == "echo"
    assert payload["request_id"] == "probe-echo-001"
    assert payload["content_type"] == "application/json"
    assert payload["body_length"] == len(body)
    assert payload["body_preview_text"] == body.decode("utf-8")
    assert payload["body_preview_truncated"] is False
    assert payload["selected_headers"]["content-type"] == "application/json"

    audit = json.loads((tmp_path / payload["audit"]["artifact_relpath"]).read_text(encoding="utf-8"))
    assert audit["request_body_sha256"] == hashlib.sha256(body).hexdigest()
    assert audit["body_length"] == len(body)


def test_assistant_probe_capabilities_route_reports_static_config(tmp_path: Path, monkeypatch) -> None:
    _clear_probe_env(monkeypatch)
    monkeypatch.setenv("ASSISTANT_PROBE_MAX_BODY_BYTES", "12345")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    req = _DummyReq(cfg=cfg, path="/api/assistant/probe/capabilities?ingress_tag=custom_port_test")
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["route"] == "capabilities"
    assert payload["max_body_bytes"] == 12345
    assert "/api/assistant/probe/echo" in {row["path"] for row in payload["routes"]}


def test_assistant_probe_ingress_tag_persisted(tmp_path: Path, monkeypatch) -> None:
    _clear_probe_env(monkeypatch)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    req = _DummyReq(cfg=cfg, path="/api/assistant/probe/ping?ingress_tag=cloudflare_tunnel")
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    audit = json.loads((tmp_path / payload["audit"]["state_relpath"]).read_text(encoding="utf-8"))
    assert audit["ingress_tag"] == "cloudflare_tunnel"


def test_assistant_probe_body_limit_behavior(tmp_path: Path, monkeypatch) -> None:
    _clear_probe_env(monkeypatch)
    monkeypatch.setenv("ASSISTANT_PROBE_MAX_BODY_BYTES", "8")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)
    body = b"0123456789"
    req = _DummyReq(
        cfg=cfg,
        path="/api/assistant/probe/echo?ingress_tag=custom_port_test",
        body=body,
        headers={"Content-Type": "text/plain; charset=utf-8"},
    )

    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 413
    payload = _payload(req)
    assert payload["classification"] == "body_limit_exceeded"
    assert payload["max_body_bytes"] == 8
    audit = json.loads((tmp_path / payload["audit"]["state_relpath"]).read_text(encoding="utf-8"))
    assert audit["classification"] == "body_limit_exceeded"
    assert audit["request_size_bytes"] == len(body)


def test_assistant_probe_timeout_classification(tmp_path: Path, monkeypatch) -> None:
    _clear_probe_env(monkeypatch)
    monkeypatch.setenv("ASSISTANT_PROBE_MAX_DELAY_MS", "100")
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: tmp_path)
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path)

    req = _DummyReq(cfg=cfg, path="/api/assistant/probe/ping?ingress_tag=custom_port_test&delay_ms=75")
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["classification"] == "delayed_response"
    assert payload["delay_applied_ms"] == 75

    audit = json.loads((tmp_path / payload["audit"]["artifact_relpath"]).read_text(encoding="utf-8"))
    assert audit["classification"] == "delayed_response"
    assert audit["delay_applied_ms"] == 75
