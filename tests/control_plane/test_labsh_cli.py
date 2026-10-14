from __future__ import annotations

import pytest

from pathlib import Path

from infra.bin import lab, labsh


def test_labsh_runs_requests_until_exit(monkeypatch, capsys) -> None:
    seen = []
    inputs = iter(["apply this patch", "exit"])

    def fake_input(prompt: str) -> str:
        seen.append(("prompt", prompt))
        return next(inputs)

    def fake_lab_main(argv):
        seen.append(("argv", list(argv)))
        print("handled")
        return 0

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(labsh.lab, "main", fake_lab_main)
    rc = labsh.main([])
    assert rc == 0
    assert ("argv", ["apply", "this", "patch"]) in seen
    assert "handled" in capsys.readouterr().out


def test_labsh_uses_same_chain_splitter_as_lab(monkeypatch, capsys) -> None:
    seen = []
    inputs = iter(["restart the webui server, then show me status", "exit"])

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_lab_main(argv):
        seen.append(list(argv))
        return 0

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(labsh.lab, "main", fake_lab_main)
    rc = labsh.main([])
    assert rc == 0
    assert ["restart", "the", "webui", "server"] in seen
    assert ["server", "status"] in seen


def test_labsh_help_stays_in_shell(monkeypatch, capsys) -> None:
    inputs = iter(["help", "quit"])

    def fake_input(prompt: str) -> str:
        return next(inputs)

    called = {"count": 0}

    def fake_lab_main(argv):
        called["count"] += 1
        return 0

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(labsh.lab, "main", fake_lab_main)
    rc = labsh.main([])
    assert rc == 0
    assert called["count"] == 0
    out = capsys.readouterr().out
    assert "Examples:" in out
    assert "Known verbs:" in out
    assert 'Inside labsh, type requests directly without the leading "lab".' in out
    assert 'Type "source" to reload labsh after updates.' in out


def test_labsh_help_archive_prints_topic_help(monkeypatch, capsys) -> None:
    inputs = iter(["help archive", "quit"])

    def fake_input(prompt: str) -> str:
        return next(inputs)

    monkeypatch.setattr("builtins.input", fake_input)
    rc = labsh.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Verb Help: archive" in out
    assert "Action: repo_archive_snapshot_v1" in out


def test_labsh_help_inspect_prints_matching_verbs(monkeypatch, capsys) -> None:
    inputs = iter(["help inspect", "quit"])

    def fake_input(prompt: str) -> str:
        return next(inputs)

    monkeypatch.setattr("builtins.input", fake_input)
    rc = labsh.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert 'Multiple verbs match "inspect":' in out
    assert "  - inspect scheduler" in out


def test_labsh_keeps_running_after_request_failure(monkeypatch, capsys) -> None:
    inputs = iter(["archive weekly", "quit"])
    calls = {"count": 0}

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_lab_main(argv):
        calls["count"] += 1
        if calls["count"] == 1:
            return 1
        return 0

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(labsh.lab, "main", fake_lab_main)
    rc = labsh.main([])
    assert rc == 0
    assert calls["count"] == 1
    assert capsys.readouterr().out == ""


def test_labsh_source_reexecs_shell(monkeypatch, capsys) -> None:
    inputs = iter(["source"])
    captured = {}

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_reload(argv) -> None:
        captured["argv"] = list(argv)
        raise SystemExit(0)

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(labsh, "_reload_shell", fake_reload)
    with pytest.raises(SystemExit) as excinfo:
        labsh.main(["--prompt", "plane> "])
    assert excinfo.value.code == 0
    assert captured["argv"] == ["--prompt", "plane> "]
    out = capsys.readouterr().out
    assert "[the-plane] Reloading labsh..." in out


def test_labsh_ctrl_c_cancels_request_and_keeps_shell(monkeypatch, capsys) -> None:
    inputs = iter(["validate the implementation", "quit"])
    calls = {"count": 0}

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_lab_main(argv):
        calls["count"] += 1
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.setattr(labsh.lab, "main", fake_lab_main)
    rc = labsh.main([])
    assert rc == 0
    assert calls["count"] == 1
    out = capsys.readouterr().out
    assert "[the-plane] Request canceled." in out


def test_labsh_ctrl_c_at_prompt_exits_shell(monkeypatch, capsys) -> None:
    def fake_input(prompt: str) -> str:
        raise KeyboardInterrupt()

    monkeypatch.setattr("builtins.input", fake_input)
    rc = labsh.main([])
    assert rc == 130
    out = capsys.readouterr().out
    assert "[the-plane] Shell canceled." in out


def test_labsh_writes_interactive_session_projection(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    inputs = iter(["server status", "quit"])

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_execute(payload, *, repo_root=None, contract_repo_root=None):
        return {
            "supported": True,
            "summary": "handled",
            "selection": {"action_id": "server_status_v1"},
            "operator_surface": {
                "executive_surface": {
                    "state": "PASS",
                    "primary_next_action": {"label": "Review server status"},
                    "confidence": "High",
                    "short_reason": "Server status retrieved.",
                }
            },
        }

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(lab, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(lab, "execute_operator_request_v1", fake_execute)
    rc = labsh.main([])
    assert rc == 0
    session_root = repo_root / "governance" / "agents" / "sessions" / "lab_front_door"
    session_files = list(session_root.glob("*/session__lab.jsonl"))
    assert len(session_files) == 1
    text = session_files[0].read_text(encoding="utf-8")
    assert '"mode": "interactive"' in text
    assert "server status" in text
    bubble_root = repo_root / "governance" / "agents" / "lab_bubbles"
    bubble_files = list(bubble_root.glob("*.md"))
    assert len(bubble_files) == 1
    bubble_text = bubble_files[0].read_text(encoding="utf-8")
    assert "# Lab / " in bubble_text
    assert "server status" in bubble_text
    assert "STATE: PASS" in bubble_text


def test_labsh_runs_e2e_builtin(monkeypatch, tmp_path: Path, capsys) -> None:
    inputs = iter(["e2e fcee --all", "quit"])
    called = {}
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_run_suite(*, family: str, scenario: str = "", run_all: bool = False, fast: bool = False, full: bool = False, root=None):
        called["family"] = family
        called["scenario"] = scenario
        called["run_all"] = run_all
        return {"family": family, "ok": True, "passed": 2, "total": 2, "scenarios": []}

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(lab, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(labsh.lab_e2e, "run_suite", fake_run_suite)
    monkeypatch.setattr(labsh.lab_e2e, "render_suite_text", lambda result: "FCEE E2E STATUS\nOVERALL: TRUSTABLE (2/2 scenarios)")
    rc = labsh.main([])
    assert rc == 0
    assert called["family"] == "fcee"
    assert called["run_all"] is True
    out = capsys.readouterr().out
    assert "FCEE E2E STATUS" in out


def test_labsh_can_chain_e2e_then_lab(monkeypatch, tmp_path: Path, capsys) -> None:
    inputs = iter(["do e2e fcee then server status", "quit"])
    seen = []
    repo_root = tmp_path / "repo"
    repo_root.mkdir()

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_run_suite(*, family: str, scenario: str = "", run_all: bool = False, fast: bool = False, full: bool = False, root=None):
        seen.append(("e2e", family))
        return {"family": family, "ok": True, "passed": 1, "total": 1, "scenarios": []}

    def fake_lab_main(argv):
        seen.append(("lab", list(argv)))
        print("server handled")
        return 0

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(lab, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(labsh.lab_e2e, "run_suite", fake_run_suite)
    monkeypatch.setattr(labsh.lab_e2e, "render_suite_text", lambda result: "FCEE E2E STATUS\nOVERALL: TRUSTABLE (1/1 scenarios)")
    monkeypatch.setattr(labsh.lab, "main", fake_lab_main)
    rc = labsh.main([])
    assert rc == 0
    assert ("e2e", "fcee") in seen
    assert ("lab", ["server", "status"]) in seen
    out = capsys.readouterr().out
    assert "[1/2] e2e fcee" in out
    assert "[2/2] server status" in out


def test_labsh_persists_history_file(monkeypatch, tmp_path: Path, capsys) -> None:
    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    inputs = iter(["server status", "quit"])

    class FakeReadline:
        def __init__(self) -> None:
            self.items: list[str] = []
            self.loaded_path = ""
            self.saved_path = ""
            self.max_length = 0

        def read_history_file(self, path: str) -> None:
            self.loaded_path = path

        def write_history_file(self, path: str) -> None:
            self.saved_path = path
            Path(path).write_text("\n".join(self.items) + ("\n" if self.items else ""), encoding="utf-8")

        def set_history_length(self, length: int) -> None:
            self.max_length = length

        def get_current_history_length(self) -> int:
            return len(self.items)

        def get_history_item(self, index: int):
            if 1 <= index <= len(self.items):
                return self.items[index - 1]
            return None

        def add_history(self, line: str) -> None:
            self.items.append(line)

    fake_readline = FakeReadline()

    def fake_input(prompt: str) -> str:
        return next(inputs)

    def fake_lab_main(argv):
        print("handled")
        return 0

    monkeypatch.setattr("builtins.input", fake_input)
    monkeypatch.chdir(repo_root)
    monkeypatch.setattr(lab, "_repo_root", lambda: repo_root)
    monkeypatch.setattr(labsh, "_readline", fake_readline)
    monkeypatch.setattr(labsh.lab, "main", fake_lab_main)
    rc = labsh.main([])
    assert rc == 0
    history_path = repo_root / "governance" / "agents" / "runtime" / "labsh_history.txt"
    assert fake_readline.saved_path == str(history_path)
    assert history_path.read_text(encoding="utf-8").splitlines() == ["server status", "quit"]


def test_labsh_history_dedupes_adjacent_duplicates(monkeypatch) -> None:
    class FakeReadline:
        def __init__(self) -> None:
            self.items: list[str] = []

        def get_current_history_length(self) -> int:
            return len(self.items)

        def get_history_item(self, index: int):
            if 1 <= index <= len(self.items):
                return self.items[index - 1]
            return None

        def add_history(self, line: str) -> None:
            self.items.append(line)

    fake_readline = FakeReadline()
    monkeypatch.setattr(labsh, "_readline", fake_readline)
    labsh._remember_history_line("server status")
    labsh._remember_history_line("server status")
    labsh._remember_history_line("archive weekly")
    assert fake_readline.items == ["server status", "archive weekly"]
