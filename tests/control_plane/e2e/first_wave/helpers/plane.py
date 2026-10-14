from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_routes_and_main as routes


class DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: bytes = b"") -> None:
        self.path = path
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def configure_plane_routes(*, repo_root: Path, monkeypatch) -> routes.WebUIConfig:
    monkeypatch.setattr(sched, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(routes._scheduled_jobs, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(routes, "_repo_root_from_webui", lambda: repo_root)
    monkeypatch.setattr(
        routes._scheduled_jobs,
        "scheduler_service_status",
        lambda: {
            "running": True,
            "interval_s": 30,
            "last_tick_utc": "2026-04-11T00:00:00Z",
            "last_error": "",
        },
    )
    return routes.WebUIConfig(runs_root=repo_root, static_dir=repo_root)


def install_success_runtime(*, monkeypatch) -> None:
    monkeypatch.setattr(
        sched._agents,
        "send_message",
        lambda **kwargs: {"ok": True, "assistant": {"content": "scheduled ok", "status": "ok", "meta": {}}},
    )


def json_body(req: DummyReq) -> dict[str, Any]:
    raw = req.wfile.getvalue().decode("utf-8")
    start = raw.find("{")
    assert start >= 0
    return json.loads(raw[start:])


def post_json(cfg: routes.WebUIConfig, path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    req = DummyReq(cfg=cfg, path=path, body=json.dumps(payload).encode("utf-8"))
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    assert req._status is not None
    return req._status, json_body(req)


def get_json(cfg: routes.WebUIConfig, path: str) -> tuple[int, dict[str, Any]]:
    req = DummyReq(cfg=cfg, path=path)
    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]
    assert req._status is not None
    return req._status, json_body(req)
