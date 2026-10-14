from __future__ import annotations

from pathlib import Path
from typing import Any

from infra.webui import server_routes_and_main as routes


def test_admin_git_status_skips_porcelain_when_index_lock_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo_root = tmp_path
    (repo_root / ".git").mkdir()
    (repo_root / ".git" / "index.lock").write_text("", encoding="utf-8")
    seen: list[list[str]] = []

    class _Proc:
        def __init__(self, stdout: str = "", returncode: int = 0) -> None:
            self.stdout = stdout
            self.stderr = ""
            self.returncode = returncode

    def _fake_run(cmd: list[str], **kwargs: Any) -> _Proc:
        seen.append(list(cmd))
        if cmd[1:] == ["branch", "--show-current"]:
            return _Proc("main\n")
        if cmd[1:] == ["rev-parse", "--short", "HEAD"]:
            return _Proc("abc123\n")
        if cmd[1:] == ["describe", "--tags", "--always", "--dirty"]:
            return _Proc("baseline-324-1-gabc123\n")
        if cmd[1:] == ["tag", "--list", "baseline-*"]:
            return _Proc("baseline-324\n")
        raise AssertionError(f"unexpected git command: {cmd!r}")

    monkeypatch.setattr(routes.subprocess, "run", _fake_run)
    monkeypatch.setattr(routes, "_now_utc_iso", lambda: "2026-03-11T19:55:00Z")

    payload = routes._admin_git_status_payload(repo_root=repo_root, env={})

    assert payload["repo_busy"] is True
    assert payload["branch"] == "main"
    assert payload["head"] == "abc123"
    assert payload["baseline_tag_suggested"] == "baseline-325"
    assert payload["status_porcelain"] == ""
    assert "index lock present" in payload["stderr"]
    assert "commit journal audit skipped" in payload["commit_journal"]["error"]
    assert ["git", "status", "--porcelain=v1"] not in seen
