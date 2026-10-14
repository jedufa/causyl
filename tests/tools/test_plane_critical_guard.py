from __future__ import annotations

import os

import pytest

from tools.bin import plane_critical_guard as guard


def test_parse_push_specs_ignores_invalid_lines() -> None:
    specs = guard._parse_push_specs(
        "refs/heads/main abc refs/remotes/origin/main def\n"
        "invalid line\n"
        "refs/heads/feat 111 refs/remotes/origin/feat 222\n"
    )
    assert specs == [
        ("refs/heads/main", "abc", "refs/remotes/origin/main", "def"),
        ("refs/heads/feat", "111", "refs/remotes/origin/feat", "222"),
    ]


def test_critical_paths_filters_to_guarded_files() -> None:
    rows = guard._critical_paths(
        [
            "README.md",
            "infra/bin/scheduler_live_watch.py",
            "tools/bin/intent_sheet_next.py",
            "infra/bin/scheduler_live_watch.py",
        ]
    )
    assert rows == [
        "infra/bin/scheduler_live_watch.py",
        "tools/bin/intent_sheet_next.py",
    ]


def test_has_override_accepts_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(guard.OVERRIDE_ENV, "emergency")
    assert guard._has_override([]) is True


def test_has_override_accepts_commit_trailer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(guard.OVERRIDE_ENV, raising=False)
    assert guard._has_override(["subject\n\nALLOW-RECENT-REMOVAL-REUSE: justified"]) is True


def test_run_guard_noops_without_critical_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    called = {"pack": False}

    def fake_main(argv):
        called["pack"] = True
        return 1

    monkeypatch.setattr(guard.pack, "main", fake_main)
    rc = guard._run_guard(touched_paths=["README.md"], override_allowed=False)
    assert rc == 0
    assert called["pack"] is False
