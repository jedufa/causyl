from __future__ import annotations

import io
from pathlib import Path

from infra.webui import server_http_core as mod


class _DummyHandler:
    def __init__(self) -> None:
        self.status = 0
        self.headers: list[tuple[str, str]] = []
        self.wfile = io.BytesIO()

    def send_response(self, status: int) -> None:
        self.status = int(status)

    def send_header(self, key: str, value: str) -> None:
        self.headers.append((str(key), str(value)))

    def end_headers(self) -> None:
        return None


def test_project_docs_auto_discovery_and_index(tmp_path: Path) -> None:
    site_root = tmp_path / "experiments" / "projects" / "demo" / "site"
    site_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<h1>Demo Docs</h1>", encoding="utf-8")

    rows = mod._discover_project_docs_sites(tmp_path)
    assert rows == [
        {
            "project_id": "demo",
            "project_root": str((tmp_path / "experiments" / "projects" / "demo").resolve()),
            "docs_root": str(site_root.resolve()),
            "docs_kind": "site",
            "docs_url": "/projects/demo/docs/",
        }
    ]
    html = mod._project_docs_index_html(tmp_path)
    assert "/projects/demo/docs/" in html
    assert "demo" in html


def test_serve_project_docs_file_serves_project_site_content(tmp_path: Path) -> None:
    site_root = tmp_path / "experiments" / "projects" / "demo" / "site"
    asset_root = site_root / "assets"
    asset_root.mkdir(parents=True, exist_ok=True)
    (site_root / "index.html").write_text("<h1>Demo Docs</h1>", encoding="utf-8")
    (asset_root / "app.js").write_text("console.log('demo');", encoding="utf-8")

    index_handler = _DummyHandler()
    assert mod._serve_project_docs_file(index_handler, tmp_path, "/projects/demo/docs/") is True
    assert index_handler.status == 200
    assert b"Demo Docs" in index_handler.wfile.getvalue()

    asset_handler = _DummyHandler()
    assert mod._serve_project_docs_file(asset_handler, tmp_path, "/projects/demo/docs/assets/app.js") is True
    assert asset_handler.status == 200
    assert b"console.log('demo');" in asset_handler.wfile.getvalue()
