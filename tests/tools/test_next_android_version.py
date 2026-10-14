from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "tools" / "mobile" / "next_android_version.py"
    spec = importlib.util.spec_from_file_location("next_android_version", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_allocate_version_is_monotonic(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module()
    repo_root = tmp_path / "repo"
    repo_root.mkdir(parents=True)
    times = iter([1700000000, 1699999990])
    monkeypatch.setattr(mod.time, "time", lambda: next(times))

    first = mod.allocate_version(repo_root=repo_root, base_version="0.1.0", channel="debug")
    second = mod.allocate_version(repo_root=repo_root, base_version="0.1.0", channel="debug")

    assert first["version_code"] == 1700000000
    assert second["version_code"] == 1700000001
    assert second["version_name"] == "0.1.0-debug+1700000001"


def test_release_version_name_uses_release_format() -> None:
    mod = _load_module()

    assert mod._version_name(base_version="0.1.0", channel="release", version_code=1700000002) == "0.1.0+1700000002"
