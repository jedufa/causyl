from __future__ import annotations

import json
from pathlib import Path

import pytest

from infra.host import verification_contract as contract
from infra.host import verification_runner as runner


def test_verification_runner_executes_allowlisted_android_request(tmp_path: Path, monkeypatch) -> None:
    android_dir = tmp_path / "infra" / "mobile" / "android" / "app" / "build" / "test-results" / "testDebugUnitTest"
    android_dir.mkdir(parents=True, exist_ok=True)
    artifact = android_dir / "TEST-com.eiel.chat.ChatViewModelWorkflowStateTest.xml"

    def _fake_stream_subprocess(*, argv, cwd, env, timeout_s, stdout_path, stderr_path, stall_timeout_s, status_callback=None):
        assert list(argv) == [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            "--stacktrace",
            ":app:testDebugUnitTest",
            "--tests",
            "com.eiel.chat.ChatViewModelWorkflowStateTest",
        ]
        assert str(cwd) == str((tmp_path / "infra" / "mobile" / "android").resolve())
        assert env is not None
        assert str(env.get("GRADLE_USER_HOME") or "").endswith(".tooling/gradle-home")
        assert int(timeout_s) > 0
        assert int(stall_timeout_s) > 0
        assert callable(status_callback)
        status_callback(
            "running",
            "process emitted output",
            output_seen=True,
            last_output_utc="2026-03-10T00:00:01Z",
            elapsed_s=12,
            heartbeat_interval_s=15,
            subprocess_pid=9876,
        )
        artifact.write_text("<testsuite/>", encoding="utf-8")
        stdout_path.write_text("BUILD SUCCESSFUL\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return ("BUILD SUCCESSFUL\n", "", 0)

    monkeypatch.setattr(runner, "_stream_subprocess", _fake_stream_subprocess)
    monkeypatch.setattr(runner, "_probe_android_gradle_host_capability", lambda: None)
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.ChatViewModelWorkflowStateTest",
            },
        },
        "reason": "targeted Android proof",
    }

    result = runner.run_request(request, repo_root=tmp_path)
    job_dir = Path(str(result["evidence"]["stdout_path"])).resolve().parent

    assert result["ok"] is True
    assert result["action_id"] == "android_unit_test"
    assert artifact.resolve() in [Path(x).resolve() for x in result["evidence"]["artifact_paths"]]
    assert (job_dir / "request.json").exists()
    assert (job_dir / "result.json").exists()
    assert (job_dir / "stdout.txt").read_text(encoding="utf-8") == "BUILD SUCCESSFUL\n"
    parsed = json.loads((job_dir / "result.json").read_text(encoding="utf-8"))
    assert parsed["ok"] is True
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))
    assert status["status"] == "finished"
    assert status["phase"] == "completed"
    assert int(status["timeout_s"]) == 3600
    assert int(status["stall_timeout_s"]) == 900
    assert int(status["heartbeat_interval_s"]) == 15
    assert bool(status["output_seen"]) is True
    assert str(status["last_output_utc"]) == "2026-03-10T00:00:01Z"
    assert str(status["last_heartbeat_utc"]) != ""
    assert str(status["deadline_utc"]) != ""
    assert int(status["remaining_s"]) >= 0
    assert int(status["elapsed_s"]) == 12
    assert int(status["subprocess_pid"]) == 9876
    assert str(status["progress_note"]) == "result.json written"
    assert str(status["timeout_source"]) == "action_default"
    assert str(status["stall_timeout_source"]) == "runner_default"
    assert str(status["job_dir"]) == str(job_dir)
    assert str(status["request_path"]).endswith("/request.json")
    assert str(status["result_path"]).endswith("/result.json")
    assert str(status["stdout_path"]).endswith("/stdout.txt")
    assert str(status["stderr_path"]).endswith("/stderr.txt")


def test_android_unit_test_request_requires_selector(tmp_path: Path) -> None:
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
            },
        },
        "reason": "broad Android proof",
    }

    with pytest.raises(ValueError, match="test_selector or test_selectors"):
        contract.normalize_request(request, repo=tmp_path)


def test_verification_runner_executes_allowlisted_android_request_with_multiple_selectors(
    tmp_path: Path, monkeypatch
) -> None:
    android_dir = tmp_path / "infra" / "mobile" / "android" / "app" / "build" / "test-results" / "testDebugUnitTest"
    android_dir.mkdir(parents=True, exist_ok=True)
    artifact = android_dir / "TEST-com.eiel.chat.data.HttpChatApiTest.xml"

    def _fake_stream_subprocess(*, argv, cwd, env, timeout_s, stdout_path, stderr_path, stall_timeout_s, status_callback=None):
        assert list(argv) == [
            "./gradlew",
            "--no-daemon",
            "--console=plain",
            "--stacktrace",
            ":app:testDebugUnitTest",
            "--tests",
            "com.eiel.chat.ui.MarkdownParserTest",
            "--tests",
            "com.eiel.chat.data.HttpChatApiTest",
            "--tests",
            "com.eiel.chat.ChatViewModelWorkflowStateTest",
        ]
        assert str(cwd) == str((tmp_path / "infra" / "mobile" / "android").resolve())
        assert env is not None
        assert env["ANDROID_USER_HOME"] == str((tmp_path / ".tooling" / "android-user-home").resolve())
        assert env["ANDROID_AVD_HOME"] == str((tmp_path / ".tooling" / "android-user-home" / "avd").resolve())
        assert env["ANDROID_EMULATOR_HOME"] == str((tmp_path / ".tooling" / "android-user-home").resolve())
        assert "ANDROID_PREFS_ROOT" not in env
        assert int(timeout_s) > 0
        assert int(stall_timeout_s) > 0
        artifact.write_text("<testsuite/>", encoding="utf-8")
        stdout_path.write_text("BUILD SUCCESSFUL\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return ("BUILD SUCCESSFUL\n", "", 0)

    monkeypatch.setattr(runner, "_stream_subprocess", _fake_stream_subprocess)
    monkeypatch.setattr(runner, "_probe_android_gradle_host_capability", lambda: None)
    monkeypatch.setattr(
        runner.os,
        "environ",
        {
            "PATH": "/usr/bin",
            "ANDROID_USER_HOME": str((tmp_path / ".tooling" / "android-user-home").resolve()),
            "ANDROID_PREFS_ROOT": str((tmp_path / ".tooling" / "android-user-home").resolve()),
        },
    )
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selectors": [
                    "com.eiel.chat.ui.MarkdownParserTest",
                    "com.eiel.chat.data.HttpChatApiTest",
                    "com.eiel.chat.ChatViewModelWorkflowStateTest",
                ],
            },
        },
        "reason": "targeted Android proof",
    }

    result = runner.run_request(request, repo_root=tmp_path)

    assert result["ok"] is True
    assert artifact.resolve() in [Path(x).resolve() for x in result["evidence"]["artifact_paths"]]


def test_verification_runner_loads_repo_android_env_script(tmp_path: Path, monkeypatch) -> None:
    tools_mobile = tmp_path / "tools" / "mobile"
    tools_mobile.mkdir(parents=True, exist_ok=True)
    script_path = tools_mobile / "android_env.sh"
    script_path.write_text(
        "\n".join(
            [
                "#!/usr/bin/env bash",
                'export JAVA_HOME="/tmp/jdk-17"',
                'export ANDROID_SDK_ROOT="/tmp/android-sdk"',
                'export PATH="${JAVA_HOME}/bin:${PATH}"',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        runner.os,
        "environ",
        {
            "PATH": "/usr/bin",
            "ANDROID_PREFS_ROOT": "/tmp/android-user-home",
        },
    )

    env = runner._build_subprocess_env(repo=tmp_path, action_id="android_unit_test")

    assert env["JAVA_HOME"] == "/tmp/jdk-17"
    assert env["JDK_HOME"] == "/tmp/jdk-17"
    assert env["ANDROID_SDK_ROOT"] == "/tmp/android-sdk"
    assert env["PATH"].startswith("/tmp/jdk-17/bin:")
    assert env["GRADLE_USER_HOME"] == str((tmp_path / ".tooling" / "gradle-home").resolve())
    assert env["ANDROID_USER_HOME"] == str((tmp_path / ".tooling" / "android-user-home").resolve())
    assert env["ANDROID_AVD_HOME"] == str((tmp_path / ".tooling" / "android-user-home" / "avd").resolve())
    assert env["ANDROID_EMULATOR_HOME"] == str((tmp_path / ".tooling" / "android-user-home").resolve())
    assert "ANDROID_PREFS_ROOT" not in env
    assert Path(env["GRADLE_USER_HOME"]).is_dir()
    assert Path(env["ANDROID_USER_HOME"]).is_dir()
    assert Path(env["ANDROID_AVD_HOME"]).is_dir()


def test_verification_runner_returns_structured_invalid_request_result(tmp_path: Path) -> None:
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "unknown_action",
            "params": {},
        },
        "reason": "should be rejected",
    }

    result = runner.run_request(request, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_kind"] == "invalid_request"
    assert result["exit_code"] == 2
    assert "unknown verification action" in str(result["evidence"]["summary"])


def test_verification_runner_uses_action_default_timeout_when_request_omits_timeout(tmp_path: Path) -> None:
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.ChatViewModelWorkflowStateTest",
            },
        },
        "reason": "targeted Android proof",
    }

    normalized = runner._contract.normalize_request(request, repo=tmp_path)

    assert int(normalized["policy"]["timeout_s"]) == 3600


def test_verification_runner_classifies_gradle_socket_failure_as_environment_blocked() -> None:
    failure_kind = runner._classify_failure(
        exit_code=1,
        stdout_text="",
        stderr_text="Could not determine a usable wildcard IP for this machine. Socket creation failed.",
    )

    assert failure_kind == "environment_blocked"


def test_verification_runner_fails_fast_when_host_blocks_android_gradle_socket(tmp_path: Path, monkeypatch) -> None:
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.ChatViewModelWorkflowStateTest",
            },
        },
        "reason": "targeted Android proof",
    }

    monkeypatch.setattr(
        runner,
        "_probe_android_gradle_host_capability",
        lambda: "host blocks local server sockets required by Gradle daemon startup",
    )

    def _unexpected_stream_subprocess(*args, **kwargs):
        raise AssertionError("_stream_subprocess should not execute when host preflight already failed")

    monkeypatch.setattr(runner, "_stream_subprocess", _unexpected_stream_subprocess)

    result = runner.run_request(request, repo_root=tmp_path)

    assert result["ok"] is False
    assert result["failure_kind"] == "environment_blocked"
    assert result["exit_code"] == 126
    assert "host blocks local server sockets" in str(result["evidence"]["summary"])


def test_stream_subprocess_writes_start_marker_and_times_out_without_child_output(
    tmp_path: Path, monkeypatch
) -> None:
    stdout_path = tmp_path / "stdout.txt"
    stderr_path = tmp_path / "stderr.txt"
    moments = iter([0.0, 0.0, 31.0, 31.0, 31.0])

    class _FakeStream:
        def readline(self) -> str:
            return ""

        def close(self) -> None:
            return None

    class _FakeProc:
        def __init__(self) -> None:
            self.stdout = _FakeStream()
            self.stderr = _FakeStream()
            self.returncode = None
            self.killed = False
            self.pid = 4242

        def poll(self):
            return self.returncode

        def kill(self) -> None:
            self.killed = True
            self.returncode = -9

        def wait(self, timeout=None) -> int:
            return int(self.returncode or -9)

    fake_proc = _FakeProc()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: fake_proc)
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(moments))
    monkeypatch.setattr(runner.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(runner.os, "getpgid", lambda _pid: 4242)
    seen = {"killpg": None}

    def _fake_killpg(pgid: int, sig: int) -> None:
        seen["killpg"] = (pgid, sig)
        fake_proc.killed = True
        fake_proc.returncode = -9

    monkeypatch.setattr(runner.os, "killpg", _fake_killpg)

    seen_status: list[tuple[str, str, dict]] = []

    def _status_callback(phase: str, message: str, **meta) -> None:
        seen_status.append((phase, message, dict(meta)))

    stdout_text, stderr_text, exit_code = runner._stream_subprocess(
        argv=["./gradlew", ":app:testDebugUnitTest"],
        cwd=tmp_path,
        env={"PATH": "/usr/bin"},
        timeout_s=90,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        stall_timeout_s=30,
        status_callback=_status_callback,
    )

    assert exit_code == 124
    assert fake_proc.killed is True
    assert seen["killpg"] == (4242, runner.signal.SIGKILL)
    assert "[runner] started_utc=" in stdout_text
    assert "no child output for 30s" in stderr_text
    assert seen_status[0][0] == "waiting_for_output"
    assert seen_status[0][1] == "subprocess started"
    assert int(seen_status[0][2]["subprocess_pid"]) == 4242
    assert seen_status[-1][0] == "stall_timeout"
    assert "no child output for 30s" in seen_status[-1][1]


def test_default_stall_timeout_scales_but_stays_bounded() -> None:
    assert runner._default_stall_timeout_s(60) == 300
    assert runner._default_stall_timeout_s(1800) == 450
    assert runner._default_stall_timeout_s(3600) == 900


def test_verification_runner_status_marks_explicit_timeout_override(tmp_path: Path, monkeypatch) -> None:
    def _fake_stream_subprocess(*, argv, cwd, env, timeout_s, stdout_path, stderr_path, stall_timeout_s, status_callback=None):
        if callable(status_callback):
            status_callback(
                "streaming_output",
                "process emitted output",
                output_seen=True,
                last_output_utc="2026-03-10T00:00:05Z",
                elapsed_s=5,
                heartbeat_interval_s=15,
                subprocess_pid=1234,
                progress_note="child process emitted output",
            )
        stdout_path.write_text("BUILD SUCCESSFUL\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return ("BUILD SUCCESSFUL\n", "", 0)

    monkeypatch.setattr(runner, "_stream_subprocess", _fake_stream_subprocess)
    monkeypatch.setattr(runner, "_probe_android_gradle_host_capability", lambda: None)
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.HostVerificationSentinelTest",
            },
        },
        "policy": {"timeout_s": 1800},
        "reason": "targeted Android proof with explicit timeout",
    }

    result = runner.run_request(request, repo_root=tmp_path)
    job_dir = Path(str(result["evidence"]["stdout_path"])).resolve().parent
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))

    assert int(status["timeout_s"]) == 1800
    assert int(status["stall_timeout_s"]) == 450
    assert str(status["timeout_source"]) == "request_policy"
    assert str(status["stall_timeout_source"]) == "runner_default"


def test_verification_runner_status_marks_explicit_stall_timeout_override(tmp_path: Path, monkeypatch) -> None:
    def _fake_stream_subprocess(*, argv, cwd, env, timeout_s, stdout_path, stderr_path, stall_timeout_s, status_callback=None):
        assert int(timeout_s) == 3600
        assert int(stall_timeout_s) == 1200
        if callable(status_callback):
            status_callback(
                "streaming_output",
                "process emitted output",
                output_seen=True,
                last_output_utc="2026-03-10T00:00:05Z",
                elapsed_s=5,
                heartbeat_interval_s=15,
                subprocess_pid=1234,
                progress_note="child process emitted output",
            )
        stdout_path.write_text("BUILD SUCCESSFUL\n", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
        return ("BUILD SUCCESSFUL\n", "", 0)

    monkeypatch.setattr(runner, "_stream_subprocess", _fake_stream_subprocess)
    monkeypatch.setattr(runner, "_probe_android_gradle_host_capability", lambda: None)
    request = {
        "workflow_id": "wf-test",
        "turn": "verifier_iteration_1",
        "requested_by_role": "assurer",
        "binding": {"type": "task_id", "id": "a7k"},
        "action": {
            "action_id": "android_unit_test",
            "params": {
                "gradle_task": ":app:testDebugUnitTest",
                "test_selector": "com.eiel.chat.HostVerificationSentinelTest",
            },
        },
        "policy": {"timeout_s": 3600, "stall_timeout_s": 1200},
        "reason": "targeted Android proof with explicit no-output timeout",
    }

    result = runner.run_request(request, repo_root=tmp_path)
    job_dir = Path(str(result["evidence"]["stdout_path"])).resolve().parent
    status = json.loads((job_dir / "status.json").read_text(encoding="utf-8"))

    assert int(status["timeout_s"]) == 3600
    assert int(status["stall_timeout_s"]) == 1200
    assert str(status["timeout_source"]) == "request_policy"
    assert str(status["stall_timeout_source"]) == "request_policy"
