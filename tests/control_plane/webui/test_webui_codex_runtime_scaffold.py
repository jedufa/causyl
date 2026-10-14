from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from pathlib import Path

from infra.webui.server_codex_runtime import (
    CodexRuntimeManager,
    CodexRuntimeError,
    _apply_model_to_codex_command,
    _apply_reasoning_effort_to_codex_command,
    _extract_codex_content_from_output,
    run_codex_oneshot,
)


def test_codex_runtime_scaffold_start_list_stop(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=["cat"], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}

    started = mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="base+role")
    assert started["ok"] is True
    assert bool(started["started"]) is True
    reused = mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="base+role")
    assert reused["ok"] is True
    assert bool(reused["started"]) is False

    evf = tmp_path / "governance" / "agents" / "runtime" / "workers_events.jsonl"
    raw = evf.read_text(encoding="utf-8")
    assert "bootstrap_pending" in raw
    assert "reused" in raw

    workers = mgr.list_workers()
    assert workers["ok"] is True
    assert len(workers["workers"]) >= 1

    turn = mgr.send_turn(binding=binding, role="builder", message="hello")
    assert turn["ok"] is True
    assert "hello" in str(turn.get("content") or "")
    assert "base+role" in str(turn.get("content") or "")

    stopped = mgr.stop_worker(binding=binding, role="builder", reason="test")
    assert stopped["ok"] is True
    assert bool(stopped["stopped"]) is True


def test_codex_runtime_invalid_binding_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=["cat"], max_workers=3, idle_ttl_s=300)
    bad_binding = {"type": "task_id", "id": "ABCD"}

    try:
        mgr.ensure_worker(binding=bad_binding, role="builder", bootstrap_text="x")
        assert False, "expected CodexRuntimeError"
    except CodexRuntimeError:
        pass


def test_codex_runtime_backend_flag_off_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "llm")
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=["cat"], max_workers=3, idle_ttl_s=300)
    try:
        mgr.ensure_worker(binding={"type": "task_id", "id": "a7k"}, role="builder", bootstrap_text="x")
        assert False, "expected CodexRuntimeError"
    except CodexRuntimeError as e:
        assert "backend disabled" in str(e)


def test_codex_runtime_reap_idle_and_reconcile(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=["cat"], max_workers=3, idle_ttl_s=1)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")
    st = mgr.get_worker(binding=binding, role="builder")
    assert bool(st.get("exists")) is True

    with mgr._guard:  # type: ignore[attr-defined]
        sess = mgr._sessions.get("task_id::a7k::builder")  # type: ignore[attr-defined]
        assert sess is not None
        sess.state.last_activity_utc = "2000-01-01T00:00:00Z"
    rr = mgr.reap_idle()
    assert rr["ok"] is True
    assert int(rr["reaped"]) >= 1

    # Reconcile path should be no-op after stop.
    rec = mgr.reconcile()
    assert rec["ok"] is True


def test_codex_runtime_snapshot_invalid_rows_ignored(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    rt = tmp_path / "governance" / "agents" / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    bad = {
        "schema_version": "agents_codex_workers_state_v1",
        "utc": "2026-03-05T00:00:00Z",
        "workers": [
            {"worker_id": "x", "binding_type": "bad", "binding_id": "x", "role": "builder"},
            {"worker_id": "y", "binding_type": "task_id", "binding_id": "a7k", "role": "builder"},
        ],
    }
    (rt / "workers_state.json").write_text(json.dumps(bad), encoding="utf-8")
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=["cat"], max_workers=3, idle_ttl_s=300)
    rows = mgr.list_workers()["workers"]
    assert len(rows) == 1
    assert str(rows[0]["worker_id"]) == "y"


def test_codex_runtime_interactive_protocol_rejected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setenv("EIEL_CODEX_PROTOCOL", "interactive")
    try:
        CodexRuntimeManager(repo_root=tmp_path, default_cmd=["cat"])
        assert False, "expected CodexRuntimeError"
    except CodexRuntimeError as e:
        assert "unsupported EIEL_CODEX_PROTOCOL" in str(e)


def test_codex_runtime_requires_turn_cmd_if_not_injected(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setenv("EIEL_CODEX_PROTOCOL", "oneshot")
    monkeypatch.delenv("EIEL_CODEX_TURN_CMD", raising=False)
    try:
        CodexRuntimeManager(repo_root=tmp_path)
        assert False, "expected CodexRuntimeError"
    except CodexRuntimeError as e:
        assert "EIEL_CODEX_TURN_CMD is required" in str(e)


def test_codex_runtime_oneshot_tty_required_command_supported(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "tty_echo.py"
    script.write_text(
        "import sys\n"
        "if not sys.stdin.isatty():\n"
        "    print('not-tty')\n"
        "    raise SystemExit(2)\n"
        "data = sys.stdin.read()\n"
        "print('TTY_OK')\n"
        "print(data)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")
    res = mgr.send_turn(binding=binding, role="builder", message="hello-tty")
    txt = str(res.get("content") or "")
    if bool(res.get("ok")):
        assert "TTY_OK" in txt
        assert "hello-tty" in txt
    else:
        # Some sandboxed environments cannot allocate ptys; accept explicit
        # terminal-allocation failure as evidence fallback was attempted.
        assert "pseudo-terminal" in txt.lower() or "permission denied" in txt.lower()


def test_run_codex_oneshot_retargets_embedded_codex_cwd_to_worker_worktree(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "codex exec -C /wrong/root --skip-git-repo-check -")

    calls: list[dict] = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            calls.append({"cmd": list(cmd), "cwd": str(kwargs.get("cwd") or ""), "env": dict(kwargs.get("env") or {})})
            self.pid = 12345
            self.returncode = 0

        def communicate(self, input=None, timeout=None):
            return ('{"type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}', "")

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            return None

        def terminate(self):
            return None

        def kill(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    worker_cwd = tmp_path / "worker"
    worker_cwd.mkdir(parents=True, exist_ok=True)

    result = run_codex_oneshot(
        worker_id="swarm::x::slice_1::maker",
        binding_type="swarm",
        binding_id="x",
        role="swarm_maker",
        message="hello",
        bootstrap_text="bootstrap",
        cwd=worker_cwd,
        timeout_s=30,
    )

    assert result["ok"] is True
    assert len(calls) == 1
    assert calls[0]["cwd"] == str(worker_cwd.resolve())
    assert calls[0]["cmd"] == [
        "codex",
        "exec",
        "-C",
        str(worker_cwd.resolve()),
        "--skip-git-repo-check",
        "-",
    ]


def test_run_codex_oneshot_uses_isolated_codex_home_per_worker(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "codex exec --skip-git-repo-check -")
    shared_home = tmp_path / "shared_codex_home"
    shared_home.mkdir(parents=True, exist_ok=True)
    (shared_home / "auth.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("EIEL_CODEX_SHARED_HOME", str(shared_home))

    calls: list[dict] = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            calls.append({"cmd": list(cmd), "cwd": str(kwargs.get("cwd") or ""), "env": dict(kwargs.get("env") or {})})
            self.pid = 12346
            self.returncode = 0

        def communicate(self, input=None, timeout=None):
            return ('{"type":"event_msg","payload":{"type":"agent_message","phase":"final_answer","message":"done"}}', "")

        def wait(self, timeout=None):
            return 0

        def send_signal(self, sig):
            return None

        def terminate(self):
            return None

        def kill(self):
            return None

    monkeypatch.setattr(subprocess, "Popen", _FakePopen)
    worker_cwd = tmp_path / "worker"
    worker_cwd.mkdir(parents=True, exist_ok=True)

    result = run_codex_oneshot(
        worker_id="scheduled_job::pis_e76a::builder",
        binding_type="ledger_uid",
        binding_id="PIS-20260502-141930Z-E76A",
        role="builder",
        message="hello",
        bootstrap_text="bootstrap",
        cwd=worker_cwd,
        timeout_s=30,
    )

    assert result["ok"] is True
    assert len(calls) == 1
    env = calls[0]["env"]
    assert env["CODEX_HOME"].endswith("runtime/state/codex_oneshot_workers/scheduled_job-pis_e76a-builder/codex_home")
    assert env["EIEL_CODEX_ONESHOT_WORKER_ID"] == "scheduled_job::pis_e76a::builder"
    assert Path(env["CODEX_HOME"]).exists()
    assert (Path(env["CODEX_HOME"]) / "auth.json").exists()


def test_apply_model_to_codex_command_replaces_existing_flag_and_scopes_to_exec() -> None:
    cmd = ["codex", "exec", "-C", "/repo", "-m", "old-model", "--skip-git-repo-check", "-"]
    got = _apply_model_to_codex_command(cmd, model="gpt-5.3-codex")
    assert got == [
        "codex",
        "exec",
        "--model",
        "gpt-5.3-codex",
        "-C",
        "/repo",
        "--skip-git-repo-check",
        "-",
    ]


def test_apply_reasoning_effort_to_codex_command_replaces_existing_flag_and_scopes_to_exec() -> None:
    cmd = [
        "codex",
        "exec",
        "-C",
        "/repo",
        "-c",
        'model_reasoning_effort="low"',
        "--skip-git-repo-check",
        "-",
    ]
    got = _apply_reasoning_effort_to_codex_command(cmd, reasoning_effort="medium")
    assert got == [
        "codex",
        "exec",
        "-c",
        'model_reasoning_effort="medium"',
        "-C",
        "/repo",
        "--skip-git-repo-check",
        "-",
    ]


def test_codex_runtime_snapshot_restores_command_and_reconfigures_on_env_change(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setenv("EIEL_CODEX_PROTOCOL", "oneshot")
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "cat")
    rt = tmp_path / "governance" / "agents" / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "agents_codex_workers_state_v1",
        "utc": "2026-03-05T00:00:00Z",
        "workers": [
            {
                "worker_id": "task_id::a7k::builder",
                "binding_type": "task_id",
                "binding_id": "a7k",
                "role": "builder",
                "status": "ready",
                "created_utc": "2026-03-05T00:00:00Z",
                "updated_utc": "2026-03-05T00:00:00Z",
                "pid": 0,
                "command": ["cat"],
                "cwd": str(tmp_path),
                "last_activity_utc": "2026-03-05T00:00:00Z",
                "last_error": "",
                "bootstrap_hash": "x",
                "mode": "codex",
            }
        ],
    }
    (rt / "workers_state.json").write_text(json.dumps(state), encoding="utf-8")
    mgr = CodexRuntimeManager(repo_root=tmp_path, max_workers=3, idle_ttl_s=300)
    got = mgr.get_worker(binding={"type": "task_id", "id": "a7k"}, role="builder")
    assert got["exists"] is True
    # Change env turn command and ensure worker picks it up via reconfigure path.
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "cat -n")
    mgr2 = CodexRuntimeManager(repo_root=tmp_path, max_workers=3, idle_ttl_s=300)
    rr = mgr2.ensure_worker(binding={"type": "task_id", "id": "a7k"}, role="builder", bootstrap_text="x")
    assert rr["ok"] is True
    raw = (rt / "workers_events.jsonl").read_text(encoding="utf-8")
    assert "reconfigured" in raw


def test_codex_runtime_reuse_updates_bootstrap_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setenv("EIEL_CODEX_PROTOCOL", "oneshot")
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "cat")
    mgr = CodexRuntimeManager(repo_root=tmp_path, max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}

    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="first bootstrap")
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="second bootstrap")
    turn = mgr.send_turn(binding=binding, role="builder", message="hello")

    txt = str(turn.get("content") or "")
    assert "second bootstrap" in txt
    assert "first bootstrap" not in txt


def test_codex_runtime_restored_worker_recovers_bootstrap_text_on_reuse(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setenv("EIEL_CODEX_PROTOCOL", "oneshot")
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "cat")
    bootstrap_text = "markdown bootstrap"
    bootstrap_hash = hashlib.sha256(bootstrap_text.encode("utf-8")).hexdigest()
    rt = tmp_path / "governance" / "agents" / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "agents_codex_workers_state_v1",
        "utc": "2026-03-05T00:00:00Z",
        "workers": [
            {
                "worker_id": "task_id::a7k::builder",
                "binding_type": "task_id",
                "binding_id": "a7k",
                "role": "builder",
                "status": "ready",
                "created_utc": "2026-03-05T00:00:00Z",
                "updated_utc": "2026-03-05T00:00:00Z",
                "pid": 0,
                "command": ["cat"],
                "cwd": str(tmp_path),
                "last_activity_utc": "2026-03-05T00:00:00Z",
                "last_error": "",
                "bootstrap_hash": bootstrap_hash,
                "mode": "codex",
            }
        ],
    }
    (rt / "workers_state.json").write_text(json.dumps(state), encoding="utf-8")

    mgr = CodexRuntimeManager(repo_root=tmp_path, max_workers=3, idle_ttl_s=300)
    mgr.ensure_worker(binding={"type": "task_id", "id": "a7k"}, role="builder", bootstrap_text=bootstrap_text)
    turn = mgr.send_turn(binding={"type": "task_id", "id": "a7k"}, role="builder", message="hello")

    txt = str(turn.get("content") or "")
    assert "markdown bootstrap" in txt
    assert "hello" in txt


def test_codex_runtime_stale_workers_do_not_consume_capacity(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    monkeypatch.setenv("EIEL_CODEX_PROTOCOL", "oneshot")
    monkeypatch.setenv("EIEL_CODEX_TURN_CMD", "cat")
    rt = tmp_path / "governance" / "agents" / "runtime"
    rt.mkdir(parents=True, exist_ok=True)
    state = {
        "schema_version": "agents_codex_workers_state_v1",
        "utc": "2026-03-05T00:00:00Z",
        "workers": [
            {
                "worker_id": "task_id::a7k::builder",
                "binding_type": "task_id",
                "binding_id": "a7k",
                "role": "builder",
                "status": "ready",
                "created_utc": "2026-03-05T00:00:00Z",
                "updated_utc": "2026-03-05T00:00:00Z",
                "pid": 0,
                "command": ["cat"],
                "cwd": str(tmp_path),
                "last_activity_utc": "2026-03-05T00:00:00Z",
                "last_error": "",
                "bootstrap_hash": "x",
                "mode": "codex",
            }
        ],
    }
    (rt / "workers_state.json").write_text(json.dumps(state), encoding="utf-8")

    mgr = CodexRuntimeManager(repo_root=tmp_path, max_workers=1, max_workers_per_role=1, idle_ttl_s=300)
    second = mgr.ensure_worker(binding={"type": "task_id", "id": "b7k"}, role="generalist", bootstrap_text="y")
    assert second["ok"] is True


def test_codex_runtime_role_capacity_is_enforced_per_role(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=["cat"], max_workers=4, max_workers_per_role=2, idle_ttl_s=300)

    mgr.ensure_worker(binding={"type": "task_id", "id": "a7k"}, role="builder", bootstrap_text="x")
    mgr.ensure_worker(binding={"type": "task_id", "id": "b7k"}, role="builder", bootstrap_text="x")
    mgr.ensure_worker(binding={"type": "task_id", "id": "c7k"}, role="generalist", bootstrap_text="x")

    try:
        mgr.ensure_worker(binding={"type": "task_id", "id": "d7k"}, role="builder", bootstrap_text="x")
        assert False, "expected CodexRuntimeError"
    except CodexRuntimeError as e:
        assert "worker limit reached" in str(e)

    extra = mgr.ensure_worker(binding={"type": "task_id", "id": "e7k"}, role="generalist", bootstrap_text="x")
    assert extra["ok"] is True


def test_codex_runtime_stream_turn_emits_commentary_before_final(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "emit_stream.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"commentary\",\"message\":\"Tracing it now.\"}}), flush=True)\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"final_answer\",\"message\":\"Final answer\"}}), flush=True)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")

    events = list(mgr.stream_turn(binding=binding, role="builder", message="hello"))

    assert events[0]["type"] == "start"
    assert any(evt["type"] == "commentary" and evt["text"] == "Tracing it now." for evt in events)
    assert any(evt["type"] == "delta" and evt["delta"] == "Final answer" for evt in events)
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True


def test_codex_runtime_stream_turn_deduplicates_repeated_final_payloads(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "emit_stream_dupe.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"commentary\",\"message\":\"Still working.\"}}), flush=True)\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"final_answer\",\"message\":\"Final answer\"}}), flush=True)\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"task_complete\",\"last_agent_message\":\"Final answer\"}}), flush=True)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")

    events = list(mgr.stream_turn(binding=binding, role="builder", message="hello"))

    deltas = [evt["delta"] for evt in events if evt.get("type") == "delta"]
    assert deltas == ["Final answer"]
    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "Final answer"


def test_codex_runtime_stream_turn_emits_incremental_delta_for_growing_final_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "emit_stream_growth.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"commentary\",\"message\":\"Still working.\"}}), flush=True)\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"final_answer\",\"message\":\"Final\"}}), flush=True)\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"final_answer\",\"message\":\"Final answer\"}}), flush=True)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")

    events = list(mgr.stream_turn(binding=binding, role="builder", message="hello"))

    deltas = [evt["delta"] for evt in events if evt.get("type") == "delta"]
    snapshots = [evt["content"] for evt in events if evt.get("type") == "delta"]
    assert deltas == ["Final", " answer"]
    assert snapshots == ["Final", "Final answer"]
    assert events[-1]["type"] == "done"
    assert events[-1]["content"] == "Final answer"


def test_codex_runtime_stream_turn_reports_raw_output_when_no_structured_final_exists(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "emit_stream_raw_error.py"
    script.write_text(
        "import sys\n"
        "sys.stdin.read()\n"
        "print('rollout recorder shutdown failed', flush=True)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")

    events = list(mgr.stream_turn(binding=binding, role="builder", message="hello"))

    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is False
    assert "rollout recorder shutdown failed" in str(events[-1]["error"])


def test_codex_runtime_stream_turn_accepts_pipe_separated_json_events(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "emit_stream_pipe_events.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "events = [\n"
        "  {\"type\":\"thread.started\",\"thread_id\":\"abc\"},\n"
        "  {\"type\":\"turn.started\"},\n"
        "  {\"type\":\"item.completed\",\"item\":{\"id\":\"item_0\",\"type\":\"agent_message\",\"text\":\"Final answer from item\"}},\n"
        "  {\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1}},\n"
        "]\n"
        "print(' | '.join(json.dumps(evt) for evt in events), flush=True)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")

    events = list(mgr.stream_turn(binding=binding, role="builder", message="hello"))

    deltas = [evt["delta"] for evt in events if evt.get("type") == "delta"]
    assert deltas == ["Final answer from item"]
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True
    assert events[-1]["content"] == "Final answer from item"


def test_codex_runtime_stream_turn_treats_intermediate_modern_agent_messages_as_commentary(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "emit_stream_modern_commentary_then_final.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "sys.stdin.read()\n"
        "events = [\n"
        "  {\"type\":\"thread.started\",\"thread_id\":\"abc\"},\n"
        "  {\"type\":\"turn.started\"},\n"
        "  {\"type\":\"item.completed\",\"item\":{\"id\":\"item_0\",\"type\":\"agent_message\",\"text\":\"Checking the workspace.\"}},\n"
        "  {\"type\":\"item.completed\",\"item\":{\"id\":\"item_1\",\"type\":\"agent_message\",\"text\":\"Final answer from item\"}},\n"
        "  {\"type\":\"turn.completed\",\"usage\":{\"input_tokens\":1,\"output_tokens\":1}},\n"
        "]\n"
        "print(' | '.join(json.dumps(evt) for evt in events), flush=True)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")

    events = list(mgr.stream_turn(binding=binding, role="builder", message="hello"))

    commentary = [evt["text"] for evt in events if evt.get("type") == "commentary"]
    deltas = [evt["delta"] for evt in events if evt.get("type") == "delta"]
    assert commentary == ["Checking the workspace."]
    assert deltas == ["Final answer from item"]
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is True
    assert events[-1]["content"] == "Final answer from item"


def test_extract_codex_content_from_output_prefers_structured_final_message() -> None:
    raw = "\n".join(
        [
            json.dumps({"type": "thread.started", "thread_id": "abc"}),
            json.dumps({"type": "item.completed", "item": {"id": "item_0", "type": "agent_message", "text": "Checking the workspace."}}),
            json.dumps({"type": "event_msg", "payload": {"type": "agent_message", "phase": "final_answer", "message": "Final answer"}}),
            json.dumps({"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 1}}),
        ]
    )
    got = _extract_codex_content_from_output(raw)
    assert got["content"] == "Final answer"
    assert got["error"] == ""


def test_codex_runtime_stop_worker_interrupts_stream_turn(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("EIEL_AGENTS_BACKEND", "codex")
    script = tmp_path / "emit_and_sleep.py"
    script.write_text(
        "import json\n"
        "import sys\n"
        "import time\n"
        "sys.stdin.read()\n"
        "print(json.dumps({\"type\":\"event_msg\",\"payload\":{\"type\":\"agent_message\",\"phase\":\"commentary\",\"message\":\"Still working.\"}}), flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    mgr = CodexRuntimeManager(repo_root=tmp_path, default_cmd=[sys.executable, str(script)], max_workers=3, idle_ttl_s=300)
    binding = {"type": "task_id", "id": "a7k"}
    mgr.ensure_worker(binding=binding, role="builder", bootstrap_text="x")

    events: list[dict] = []

    def _run_stream() -> None:
        events.extend(list(mgr.stream_turn(binding=binding, role="builder", message="hello", timeout_s=60)))

    thread = threading.Thread(target=_run_stream, daemon=True)
    thread.start()

    for _ in range(50):
        worker = mgr.get_worker(binding=binding, role="builder")
        if int(worker.get("pid") or 0) > 0:
            break
        thread.join(timeout=0.05)

    stopped = mgr.stop_worker(binding=binding, role="builder", reason="test stop")
    thread.join(timeout=5.0)

    assert stopped["ok"] is True
    assert stopped["stopped"] is True
    assert thread.is_alive() is False
    assert events[0]["type"] == "start"
    assert events[-1]["type"] == "done"
    assert events[-1]["ok"] is False
    assert str(events[-1]["error"] or "") in {"test stop", "codex_turn_failed"}
