from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace

from infra.webui import server_routes_and_main as routes


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str) -> None:
        self.path = path
        self.headers = {}
        self.rfile = io.BytesIO(b"{}")
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _read_payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def test_full_framework_scenario_route_returns_catalog(tmp_path: Path) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path)
    req = _DummyReq(cfg=cfg, path="/api/admin/lab_assistant/full_framework_scenarios")

    routes.WebUIRequestHandler.do_GET(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _read_payload(req)
    assert payload["schema_version"] == "lab_assistant_full_framework_scenarios_v1"
    assert payload["uid"] == "PIS-20260312-235056Z-V4N8"
    assert payload["scenario_uids"][0] == "PIS-20260313-105059Z-2B7D"
