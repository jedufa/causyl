from __future__ import annotations

import json

import pytest

from tools.bin import plane_critical_regression_pack as pack


def test_plane_critical_regression_pack_lists_option_groups(capsys) -> None:
    rc = pack.main(["--group", "all", "--list", "--json"])
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["groups"] == ["option_a", "option_b"]
    assert "option_a" in payload["commands"]
    assert "option_b" in payload["commands"]


def test_plane_critical_regression_pack_recent_removal_check_ignores_noncritical_paths() -> None:
    payload = pack._recent_removals_report(touched_paths=["README.md"], since="1 day ago", max_commits=3)
    assert payload["critical_paths"] == []
    assert payload["findings"] == []


def test_plane_critical_regression_pack_returns_success_when_group_commands_pass(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        pack,
        "_run_group",
        lambda name: [{"group": name, "command": ["pytest"], "returncode": 0, "stdout": "", "stderr": ""}],
    )
    rc = pack.main(["--group", "option_a", "--json"])
    assert rc == 0


@pytest.mark.parametrize("first_code,expected", [(0, ["option_a", "option_b"]), (1, ["option_a"])])
def test_all_groups_run_after_success_and_stop_after_failure(monkeypatch, capsys, first_code, expected):
    calls = []
    def run(name):
        calls.append(name)
        return [{"group": name, "returncode": first_code if name == "option_a" else 0}]
    monkeypatch.setattr(pack, "_run_group", run)
    assert pack.main(["--group", "all", "--json"]) == first_code
    assert calls == expected
    assert [r["group"] for r in json.loads(capsys.readouterr().out)["results"]] == expected
