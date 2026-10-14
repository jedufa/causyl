from __future__ import annotations

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
    ) -> None:
        merged_headers = {"Content-Length": str(len(body))}
        for key, value in dict(headers or {}).items():
            merged_headers[str(key)] = str(value)
        self.path = path
        self.headers = merged_headers
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self.client_address = ("127.0.0.1", 8787)
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _payload(req: _DummyReq) -> dict:
    return json.loads(req.wfile.getvalue().decode("utf-8"))


def test_patch_apply_upload_honors_permit_deletions_query_flag(tmp_path: Path, monkeypatch) -> None:
    cfg = routes.WebUIConfig(runs_root=tmp_path, static_dir=tmp_path, admin_token="secret")
    monkeypatch.setattr(routes, "_admin_repo_root", lambda: Path(tmp_path))

    captured: dict[str, object] = {}

    def _fake_run(cmd, cwd=None, capture_output=None, text=None):
        captured["cmd"] = list(cmd)
        captured["cwd"] = cwd
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(routes.subprocess, "run", _fake_run)

    req = _DummyReq(
        cfg=cfg,
        path="/api/admin/patch/apply?token=secret&permit_deletions=1",
        body=b"dummy patch",
        headers={"Content-Type": "application/octet-stream", "X-Filename": "smoke.diff"},
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]

    assert req._status == 200
    payload = _payload(req)
    assert payload["ok"] is True
    assert payload["permit_deletions"] is True
    assert "--permit-deletions" in list(captured["cmd"])
