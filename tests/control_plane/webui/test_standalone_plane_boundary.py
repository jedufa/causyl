"""Guard the separation between the plane and optional experiment engines."""
from __future__ import annotations

import ast
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

from infra.webui import server_routes_and_main as routes

ROOT = Path(__file__).resolve().parents[3]


def _engine_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        names = ([node.module or ""] if isinstance(node, ast.ImportFrom) else
                 [alias.name for alias in node.names] if isinstance(node, ast.Import) else [])
        found.extend(name for name in names if name.split('.')[0] in {'emergence', 'simulation'}
                     or name.startswith('experiments.projects.emergence'))
    return found


def test_import_guard_detects_forbidden_imports() -> None:
    assert _engine_imports('from emergence.obs_logging import log_event')
    assert _engine_imports('import experiments.projects.emergence')
    assert not _engine_imports('from experiments.shared import runner_utils')


def test_plane_source_has_no_engine_imports() -> None:
    violations = []
    for base in ('infra', 'experiments/shared'):
        for path in (ROOT / base).rglob('*.py'):
            if not path.is_symlink():
                violations.extend((str(path.relative_to(ROOT)), name)
                                  for name in _engine_imports(path.read_text()))
    assert violations == []
    assert not (ROOT / 'experiments/projects/emergence').exists()


@pytest.mark.parametrize('method', ['GET', 'POST'])
@pytest.mark.parametrize('path', ['/api/emergence/pressure-search', '/api/builder/launch', '/api/chronicle/library'])
def test_retired_routes_return_not_found(tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
                                        method: str, path: str) -> None:
    monkeypatch.setattr(routes, '_web_surface_enabled', lambda: True)
    req = object.__new__(routes.WebUIRequestHandler)
    req.path = path
    req.headers = {'Content-Length': '2', 'Host': '127.0.0.1:8787', 'X-Admin-Token': 'test-token'}
    req.rfile = io.BytesIO(b'{}')
    req.wfile = io.BytesIO()
    req.server = SimpleNamespace(state=SimpleNamespace(cfg=routes.WebUIConfig(runs_root=tmp_path, admin_token="test-token")), server_address=("127.0.0.1", 8787))
    statuses = []
    req.send_response = lambda status: statuses.append(int(status))
    req.send_header = lambda *args: None
    req.end_headers = lambda: None
    getattr(req, 'do_' + method)()
    assert statuses == [404]
    assert list(tmp_path.iterdir()) == []


def test_engine_environment_cannot_select_plane_run_root(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('EIEL_RUNS_ROOT', raising=False)
    monkeypatch.setenv('EMERGENCE_RUNS_DIR', '/must/not/be/read')
    monkeypatch.setenv('EMERGENCE_OUTPUTS_DIR', '/must/not/be/read')
    assert routes.parse_args([]).runs_root == 'runtime/adhoc'
