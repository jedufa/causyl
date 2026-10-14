from __future__ import annotations

from pathlib import Path
import re


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


def test_main_shell_does_not_eager_load_hidden_embedded_causyl_panes() -> None:
    html = (_repo_root() / "infra" / "webui" / "static" / "index.html").read_text(encoding="utf-8")

    for frame_id, src in {
        "agentsEmbedFrame": "/agents?embedded=1",
        "mobileEmbedFrame": "/mobile?embedded=1&pane=admin",
        "schedulesEmbedFrame": "/schedules?embedded=1",
        "mobileUpdateEmbedFrame": "/mobile/update?embedded=1&channel=debug",
    }.items():
        start = html.index(f'id="{frame_id}"')
        chunk = html[start : html.index("</iframe>", start)]
        assert f'data-src="{src}"' in chunk
        assert re.search(r'(?<!data-)src="' + re.escape(src) + r'"', chunk) is None


def test_embedded_panes_load_only_when_their_view_is_rendered() -> None:
    js = (_repo_root() / "infra" / "webui" / "static" / "app_core_part6.js").read_text(encoding="utf-8")

    assert "function loadEmbeddedFrame" in js
    assert 'frame.getAttribute("data-src")' in js
    assert 'loadEmbeddedFrame("#agentsEmbedFrame", "/agents?embedded=1")' in js
    assert 'loadEmbeddedFrame("#mobileEmbedFrame", "/mobile?embedded=1&pane=admin")' in js
    assert 'loadEmbeddedFrame("#schedulesEmbedFrame", "/schedules?embedded=1")' in js
