from __future__ import annotations

import importlib.util
import json
import os
import threading
import time
from pathlib import Path

import pytest


def _load_module():
    root = Path(__file__).resolve().parents[2]
    path = root / "tools" / "bin" / "persistent_codex_slot.py"
    spec = importlib.util.spec_from_file_location("persistent_codex_slot", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


def test_project_slot_rows_keeps_only_user_and_assistant_messages(tmp_path: Path) -> None:
    mod = _load_module()
    codex_home = tmp_path / "codex_home"
    _write_jsonl(
        codex_home / "history.jsonl",
        [
            {"session_id": "sess-a", "ts": 1773180001, "text": "hello from user"},
            {"session_id": "sess-a", "ts": 1773180005, "text": "second turn"},
        ],
    )
    _write_jsonl(
        codex_home / "sessions" / "2026" / "03" / "10" / "rollout-a.jsonl",
        [
            {
                "type": "session_meta",
                "timestamp": "2026-03-10T22:00:00.000Z",
                "payload": {"id": "sess-a", "timestamp": "2026-03-10T22:00:00.000Z", "cwd": "/repo"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T22:00:02.000Z",
                "payload": {"type": "message", "role": "developer", "content": [{"type": "input_text", "text": "ignore me"}]},
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T22:00:03.000Z",
                "payload": {"type": "reasoning", "encrypted_content": "secret"},
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T22:00:04.000Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "commentary",
                    "content": [{"type": "output_text", "text": "working"}],
                },
            },
            {
                "type": "response_item",
                "timestamp": "2026-03-10T22:00:06.000Z",
                "payload": {
                    "type": "message",
                    "role": "assistant",
                    "phase": "final",
                    "content": [{"type": "output_text", "text": "done"}],
                },
            },
        ],
    )

    rows = mod._project_slot_rows("slot_01", codex_home)

    assert [row["kind"] for row in rows] == [
        "session_start",
        "message",
        "message",
        "message",
        "message",
        "session_end",
    ]
    assert [row["sender"] for row in rows if row["kind"] == "message"] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
    assert all("ignore me" not in json.dumps(row) for row in rows)
    assert all("secret" not in json.dumps(row) for row in rows)

    markdown = mod._render_terminal_bubble_markdown("slot_01", rows)
    assert "hello from user" in markdown
    assert "working" in markdown
    assert "ignore me" not in markdown
    assert "secret" not in markdown


def test_allocate_slot_reuses_stale_registry_entry(tmp_path: Path, monkeypatch) -> None:
    mod = _load_module()
    state_root = tmp_path / "state"
    stale_registry = {
        "version": 1,
        "max_slots": 2,
        "slots": {
            "slot_01": {"active": True, "pid": 999999, "launch_id": "old"},
            "slot_02": {"active": True, "pid": 123456, "launch_id": "busy"},
        },
    }
    mod._write_json_atomic(state_root / "registry.json", stale_registry)
    monkeypatch.setattr(mod, "_pid_is_alive", lambda pid: int(pid) == 123456)

    slot_id, lease = mod._allocate_slot(state_root, max_slots=2)

    assert slot_id == "slot_01"
    assert lease["active"] is True
    assert lease["pid"] > 0


def test_parse_args_preserves_codex_flags() -> None:
    mod = _load_module()

    parsed = mod._parse_args(["-C", "/repo", "-c", "shell_environment_policy.inherit=all", "resume"])

    assert parsed.slot_count == mod.DEFAULT_SLOT_COUNT
    assert parsed.codex_args == ["-C", "/repo", "-c", "shell_environment_policy.inherit=all", "resume"]


def test_real_codex_bin_requires_explicit_env(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    monkeypatch.delenv("EIEL_CODEX_REAL_BIN", raising=False)

    with pytest.raises(RuntimeError, match="EIEL_CODEX_REAL_BIN is required"):
        mod._real_codex_bin()


def test_real_codex_bin_rejects_wrapper_recursion(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    monkeypatch.setenv("EIEL_CODEX_REAL_BIN", str(Path(mod.__file__).resolve()))

    with pytest.raises(RuntimeError, match="launcher itself"):
        mod._real_codex_bin()


def test_projection_flush_loop_writes_live_projection(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _load_module()
    calls: list[tuple[str, str]] = []

    monkeypatch.setenv("EIEL_TERMINAL_SLOT_PROJECTION_FLUSH_S", "0.25")
    monkeypatch.setattr(
        mod,
        "_write_slot_projection",
        lambda repo_root, slot_id, slot_home: calls.append((str(slot_id), str(slot_home))),
    )

    stop_event = threading.Event()
    thread = threading.Thread(
        target=mod._projection_flush_loop,
        kwargs={
            "repo_root": tmp_path,
            "slot_id": "slot_01",
            "slot_home": tmp_path / "codex_home",
            "stop_event": stop_event,
        },
        daemon=True,
    )
    thread.start()
    time.sleep(0.35)
    stop_event.set()
    thread.join(timeout=2.0)

    assert calls
