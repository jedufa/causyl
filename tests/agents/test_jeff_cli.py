from __future__ import annotations

import io
import json
from pathlib import Path

from agents.jeff import cli, session_store


class _FakeBackend:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def propose(self, *, task: str, inspection: dict, session_summary: str):  # noqa: ARG002
        class _Proposal:
            summary = self.payload.get("summary", "")
            operations = self.payload.get("operations", [])
            checks = self.payload.get("checks", [])
            claimed_changed_files = self.payload.get("claimed_changed_files", [])

        return _Proposal()


def test_jeff_run_applies_change_and_records_session(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "hello.py"
    target.write_text("print('old')\n", encoding="utf-8")
    monkeypatch.setattr(
        "agents.jeff.backend.create_backend",
        lambda provider, model: _FakeBackend({"summary": "update hello", "operations": [{"path": "hello.py", "content": "print('new')\n"}], "checks": []}),
    )
    sessions_root = tmp_path / "agents" / "jeff" / "sessions"
    monkeypatch.setattr(session_store, "SESSIONS_ROOT", sessions_root)
    out = io.StringIO()
    rc = cli.main(["run", "--repo", str(repo), "--task", "update hello", "--approval", "auto"], stdin=io.StringIO(""), stdout=out)
    assert rc == 0
    assert target.read_text(encoding="utf-8") == "print('new')\n"
    sessions = [p for p in sessions_root.iterdir() if p.is_dir()]
    assert len(sessions) == 1
    session_dir = sessions[0]
    session = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    assert session["turns"][-1]["applied"] is True
    assert (session_dir / "transcript.jsonl").exists()
    assert (session_dir / "checkpoints").is_dir()
    assert (session_dir / "checkpoints" / "undone").is_dir()
    assert (session_dir / "artifacts").is_dir()


def test_jeff_undo_restores_last_checkpoint(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "hello.py"
    target.write_text("print('old')\n", encoding="utf-8")
    monkeypatch.setattr(
        "agents.jeff.backend.create_backend",
        lambda provider, model: _FakeBackend({"summary": "update hello", "operations": [{"path": "hello.py", "content": "print('new')\n"}], "checks": []}),
    )
    sessions_root = tmp_path / "agents" / "jeff" / "sessions"
    monkeypatch.setattr(session_store, "SESSIONS_ROOT", sessions_root)
    out = io.StringIO()
    cli.main(["run", "--repo", str(repo), "--task", "update hello", "--approval", "auto"], stdin=io.StringIO(""), stdout=out)
    session_dir = next(p for p in sessions_root.iterdir() if p.is_dir())
    session = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    rc = cli.main(["undo", "--session", session["session_id"]], stdout=io.StringIO())
    assert rc == 0
    assert target.read_text(encoding="utf-8") == "print('old')\n"
    assert list((session_dir / "checkpoints" / "undone").glob("*.json"))


def test_jeff_resume_appends_turn_to_existing_session(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    target = repo / "hello.py"
    target.write_text("print('old')\n", encoding="utf-8")
    payloads = iter(
        [
            {"summary": "first", "operations": [{"path": "hello.py", "content": "print('one')\n"}], "checks": []},
            {"summary": "second", "operations": [{"path": "hello.py", "content": "print('two')\n"}], "checks": []},
        ]
    )
    monkeypatch.setattr("agents.jeff.backend.create_backend", lambda provider, model: _FakeBackend(next(payloads)))
    sessions_root = tmp_path / "agents" / "jeff" / "sessions"
    monkeypatch.setattr(session_store, "SESSIONS_ROOT", sessions_root)
    cli.main(["run", "--repo", str(repo), "--task", "first", "--approval", "auto"], stdin=io.StringIO(""), stdout=io.StringIO())
    session_dir = next(p for p in sessions_root.iterdir() if p.is_dir())
    session = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    rc = cli.main(["resume", "--session", session["session_id"], "--task", "second", "--approval", "auto"], stdin=io.StringIO(""), stdout=io.StringIO())
    assert rc == 0
    updated = json.loads((session_dir / "state.json").read_text(encoding="utf-8"))
    assert len(updated["turns"]) == 2
    assert target.read_text(encoding="utf-8") == "print('two')\n"


def test_jeff_verification_fails_when_claimed_changed_file_missing(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text("print('old')\n", encoding="utf-8")
    monkeypatch.setattr(
        "agents.jeff.backend.create_backend",
        lambda provider, model: _FakeBackend(
            {
                "summary": "bad claim",
                "operations": [{"path": "hello.py", "content": "print('new')\n"}],
                "checks": [],
                "claimed_changed_files": ["hello.py", "missing.py"],
            }
        ),
    )
    sessions_root = tmp_path / "agents" / "jeff" / "sessions"
    monkeypatch.setattr(session_store, "SESSIONS_ROOT", sessions_root)
    rc = cli.main(["run", "--repo", str(repo), "--task", "bad claim", "--approval", "auto"], stdin=io.StringIO(""), stdout=io.StringIO())
    assert rc == 1
    session = json.loads((next(p for p in sessions_root.iterdir() if p.is_dir()) / "state.json").read_text(encoding="utf-8"))
    assert session["turns"][-1]["verification"]["reason"] == "claimed_changed_files_missing"


def test_jeff_diff_shows_last_turn_patch_preview(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "hello.py").write_text("print('old')\n", encoding="utf-8")
    monkeypatch.setattr(
        "agents.jeff.backend.create_backend",
        lambda provider, model: _FakeBackend({"summary": "update hello", "operations": [{"path": "hello.py", "content": "print('new')\n"}], "checks": []}),
    )
    sessions_root = tmp_path / "agents" / "jeff" / "sessions"
    monkeypatch.setattr(session_store, "SESSIONS_ROOT", sessions_root)
    cli.main(["run", "--repo", str(repo), "--task", "update hello", "--approval", "auto"], stdin=io.StringIO(""), stdout=io.StringIO())
    session = json.loads((next(p for p in sessions_root.iterdir() if p.is_dir()) / "state.json").read_text(encoding="utf-8"))
    out = io.StringIO()
    rc = cli.main(["diff", "--session", session["session_id"]], stdout=out)
    assert rc == 0
    text = out.getvalue()
    assert "session_id=" in text
    assert "diff_preview:" in text
    assert "a/hello.py" in text


def test_jeff_chat_defaults_repo_to_causyl(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_run_task(**kwargs):
        captured.update(kwargs)
        session = dict(kwargs["session"])
        session["turns"] = [{"turn_id": "turn_0001", "status": "ok"}]
        return {
            "session": session,
            "proposal": {"summary": "ok"},
            "verification": {"ok": True, "reason": "proposal_only"},
            "diff_preview": "",
        }

    monkeypatch.setattr("agents.jeff.backend.create_backend", lambda provider, model: _FakeBackend({"summary": "noop", "operations": [{"path": "x.txt", "content": "x"}], "checks": []}))
    monkeypatch.setattr("agents.jeff.engine.run_task", _fake_run_task)
    out = io.StringIO()
    rc = cli.main(["chat", "--task", "status", "--approval", "never"], stdin=io.StringIO(""), stdout=out)
    assert rc == 0
    assert captured["repo_path"] == "/home/user/causyl"


def test_tools_bin_jeff_preserves_pipeline_prompt_mode(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, object] = {}

    def _fake_execve(path: str, argv: list[str], env: dict[str, str]) -> None:
        captured["path"] = path
        captured["argv"] = list(argv)
        captured["env"] = dict(env)
        raise SystemExit(0)

    monkeypatch.setattr("os.execve", _fake_execve)
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["jeff", "--repo-root", str(tmp_path), "show", "compact", "progress"])
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    try:
        runpy.run_path(str((Path(__file__).resolve().parents[2] / "tools" / "bin" / "jeff")), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
    assert "--deterministic-only" in list(captured["argv"])


def test_tools_bin_jeff_without_args_defaults_to_chat(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def _fake_main(argv=None):
        captured["argv"] = list(argv or [])
        return 0

    monkeypatch.setattr("agents.jeff.cli.main", _fake_main)
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["jeff"])
    fake_stdin = io.StringIO("")
    monkeypatch.setattr(fake_stdin, "isatty", lambda: True)
    monkeypatch.setattr(sys, "stdin", fake_stdin)

    try:
        runpy.run_path(str((Path(__file__).resolve().parents[2] / "tools" / "bin" / "jeff")), run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
    assert captured["argv"] == ["chat"]
