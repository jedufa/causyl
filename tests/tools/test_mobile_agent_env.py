from __future__ import annotations

import socket
import subprocess
import textwrap
import threading
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = (REPO_ROOT / "tools" / "bin" / "mobile_agent_env.sh").resolve()
ACTIVATE_SCRIPT_PATH = (REPO_ROOT / "tools" / "bin" / "activate_mobile_agent_shell.sh").resolve()


def _write_stub_adb(tmp_path: Path) -> Path:
    sdk_root = tmp_path / "android-sdk"
    adb_path = sdk_root / "platform-tools" / "adb"
    adb_path.parent.mkdir(parents=True, exist_ok=True)
    adb_path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env bash
            set -euo pipefail
            socket="${ADB_SERVER_SOCKET:-}"
            cmd="${1:-}"
            if [[ "${cmd}" == "start-server" ]]; then
              case "${socket}" in
                tcp:127.0.0.1:${EIEL_STUB_ADB_REJECT_PORT:-65001}) exit 1 ;;
                tcp:127.0.0.1:65001) exit 1 ;;
                tcp:bad.host:5037) exit 1 ;;
                *) exit 0 ;;
              esac
            fi
            if [[ "${cmd}" == "version" ]]; then
              echo "Android Debug Bridge version 1.0.41"
              exit 0
            fi
            exit 0
            """
        ),
        encoding="utf-8",
    )
    adb_path.chmod(0o755)
    return sdk_root


def _write_stub_java_home(tmp_path: Path) -> Path:
    java_home = tmp_path / "jdk"
    (java_home / "bin").mkdir(parents=True, exist_ok=True)
    return java_home


def _run_mobile_env(tmp_path: Path, *, extra_env: dict[str, str]) -> subprocess.CompletedProcess[str]:
    sdk_root = _write_stub_adb(tmp_path)
    java_home = _write_stub_java_home(tmp_path)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
        "JAVA_HOME": str(java_home),
        "JDK_HOME": str(java_home),
        **extra_env,
    }
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    cmd = (
        f"source {SCRIPT_PATH} >/dev/null 2>&1; "
        "printf 'ADB_SERVER_SOCKET=%s\\n' \"${ADB_SERVER_SOCKET:-}\"; "
        "printf 'EIEL_ADB_SERVER_SOCKET_SOURCE=%s\\n' \"${EIEL_ADB_SERVER_SOCKET_SOURCE:-}\"; "
        "printf 'EIEL_ADB_SERVER_SOCKET_STATUS=%s\\n' \"${EIEL_ADB_SERVER_SOCKET_STATUS:-}\""
    )
    return subprocess.run(["bash", "-lc", cmd], cwd=str(REPO_ROOT), env=env, capture_output=True, text=True, check=False)


def test_mobile_agent_env_rejects_invalid_preconfigured_adb_socket(tmp_path: Path) -> None:
    proc = _run_mobile_env(
        tmp_path,
        extra_env={
            "ADB_SERVER_SOCKET": "tcp:bad.host:5037",
            "EIEL_ADB_SERVER_PORT": "65534",
        },
    )
    assert proc.returncode == 0, proc.stderr
    assert "ADB_SERVER_SOCKET=\n" in proc.stdout
    assert "EIEL_ADB_SERVER_SOCKET_SOURCE=invalid_preconfigured\n" in proc.stdout
    assert "EIEL_ADB_SERVER_SOCKET_STATUS=protocol_unhealthy\n" in proc.stdout


def test_mobile_agent_env_does_not_accept_tcp_only_adb_socket(tmp_path: Path) -> None:
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.bind(("127.0.0.1", 0))
    port = str(server.getsockname()[1])
    server.listen(1)
    stop = False

    def _acceptor() -> None:
        nonlocal stop
        while not stop:
            try:
                conn, _ = server.accept()
            except OSError:
                break
            conn.close()

    thread = threading.Thread(target=_acceptor, daemon=True)
    thread.start()
    try:
        proc = _run_mobile_env(
            tmp_path,
            extra_env={
                "EIEL_ADB_SERVER_PORT": port,
                "EIEL_STUB_ADB_REJECT_PORT": port,
            },
        )
    finally:
        stop = True
        server.close()
        thread.join(timeout=1.0)
    assert proc.returncode == 0, proc.stderr
    assert "ADB_SERVER_SOCKET=\n" in proc.stdout
    assert "EIEL_ADB_SERVER_SOCKET_STATUS=not_configured\n" in proc.stdout


def test_mobile_agent_env_preserves_caller_cwd(tmp_path: Path) -> None:
    launch_cwd = tmp_path / "life-load"
    launch_cwd.mkdir()
    sdk_root = _write_stub_adb(tmp_path)
    java_home = _write_stub_java_home(tmp_path)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
        "JAVA_HOME": str(java_home),
        "JDK_HOME": str(java_home),
    }
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", "-lc", f"source {SCRIPT_PATH} >/dev/null 2>&1; pwd"],
        cwd=str(launch_cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == str(launch_cwd)


def test_codex_shell_wrapper_uses_launch_cwd_by_default(tmp_path: Path) -> None:
    launch_cwd = tmp_path / "life-load"
    launch_cwd.mkdir()
    arg_log = tmp_path / "codex_args.txt"
    wrapper = tmp_path / "slot-wrapper"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$@" > {arg_log}
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    sdk_root = _write_stub_adb(tmp_path)
    java_home = _write_stub_java_home(tmp_path)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
        "JAVA_HOME": str(java_home),
        "JDK_HOME": str(java_home),
        "EIEL_CODEX_REAL_BIN": "/bin/true",
        "EIEL_CODEX_SLOT_WRAPPER": str(wrapper),
    }
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", "-lc", f"source {ACTIVATE_SCRIPT_PATH} >/dev/null 2>&1; codex 'hello'"],
        cwd=str(launch_cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    args = arg_log.read_text(encoding="utf-8").splitlines()
    assert "-C" in args
    assert args[args.index("-C") + 1] == str(launch_cwd)


def test_codex_shell_wrapper_respects_explicit_workdir(tmp_path: Path) -> None:
    launch_cwd = tmp_path / "life-load"
    explicit_cwd = tmp_path / "other"
    launch_cwd.mkdir()
    explicit_cwd.mkdir()
    arg_log = tmp_path / "codex_args.txt"
    wrapper = tmp_path / "slot-wrapper"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$@" > {arg_log}
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    sdk_root = _write_stub_adb(tmp_path)
    java_home = _write_stub_java_home(tmp_path)
    env = {
        "HOME": str(tmp_path / "home"),
        "PATH": "/usr/bin:/bin",
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
        "JAVA_HOME": str(java_home),
        "JDK_HOME": str(java_home),
        "EIEL_CODEX_REAL_BIN": "/bin/true",
        "EIEL_CODEX_SLOT_WRAPPER": str(wrapper),
    }
    Path(env["HOME"]).mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", "-lc", f"source {ACTIVATE_SCRIPT_PATH} >/dev/null 2>&1; codex -C {explicit_cwd} 'hello'"],
        cwd=str(launch_cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    args = arg_log.read_text(encoding="utf-8").splitlines()
    c_flags = [index for index, value in enumerate(args) if value == "-C"]
    assert len(c_flags) == 1
    assert args[c_flags[0] + 1] == str(explicit_cwd)


def test_codex_shell_wrapper_auto_trusts_launch_git_root(tmp_path: Path) -> None:
    launch_root = tmp_path / "new-project"
    launch_cwd = launch_root / "nested"
    launch_cwd.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=str(launch_root), check=True)
    arg_log = tmp_path / "codex_args.txt"
    wrapper = tmp_path / "slot-wrapper"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$@" > {arg_log}
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    sdk_root = _write_stub_adb(tmp_path)
    java_home = _write_stub_java_home(tmp_path)
    home = tmp_path / "home"
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
        "JAVA_HOME": str(java_home),
        "JDK_HOME": str(java_home),
        "EIEL_CODEX_REAL_BIN": "/bin/true",
        "EIEL_CODEX_SLOT_WRAPPER": str(wrapper),
    }
    home.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", "-lc", f"source {ACTIVATE_SCRIPT_PATH} >/dev/null 2>&1; codex 'hello'"],
        cwd=str(launch_cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    config_text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert f'[projects."{launch_root}"]' in config_text
    assert 'trust_level = "trusted"' in config_text


def test_codex_shell_wrapper_auto_trust_can_be_disabled(tmp_path: Path) -> None:
    launch_cwd = tmp_path / "new-project"
    launch_cwd.mkdir()
    arg_log = tmp_path / "codex_args.txt"
    wrapper = tmp_path / "slot-wrapper"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$@" > {arg_log}
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    sdk_root = _write_stub_adb(tmp_path)
    java_home = _write_stub_java_home(tmp_path)
    home = tmp_path / "home"
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
        "JAVA_HOME": str(java_home),
        "JDK_HOME": str(java_home),
        "EIEL_CODEX_REAL_BIN": "/bin/true",
        "EIEL_CODEX_SLOT_WRAPPER": str(wrapper),
        "EIEL_CODEX_AUTO_TRUST_PROJECTS": "0",
    }
    home.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", "-lc", f"source {ACTIVATE_SCRIPT_PATH} >/dev/null 2>&1; codex 'hello'"],
        cwd=str(launch_cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    assert not (home / ".codex" / "config.toml").exists()


def test_codex_shell_wrapper_auto_trust_ignores_inherited_slot_home(tmp_path: Path) -> None:
    launch_cwd = tmp_path / "new-project"
    launch_cwd.mkdir()
    arg_log = tmp_path / "codex_args.txt"
    wrapper = tmp_path / "slot-wrapper"
    wrapper.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env bash
            printf '%s\\n' "$@" > {arg_log}
            """
        ),
        encoding="utf-8",
    )
    wrapper.chmod(0o755)
    sdk_root = _write_stub_adb(tmp_path)
    java_home = _write_stub_java_home(tmp_path)
    home = tmp_path / "home"
    slot_home = tmp_path / "slot-codex-home"
    env = {
        "HOME": str(home),
        "PATH": "/usr/bin:/bin",
        "ANDROID_SDK_ROOT": str(sdk_root),
        "ANDROID_HOME": str(sdk_root),
        "JAVA_HOME": str(java_home),
        "JDK_HOME": str(java_home),
        "CODEX_HOME": str(slot_home),
        "EIEL_CODEX_REAL_BIN": "/bin/true",
        "EIEL_CODEX_SLOT_WRAPPER": str(wrapper),
    }
    home.mkdir(parents=True, exist_ok=True)
    slot_home.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        ["bash", "-lc", f"source {ACTIVATE_SCRIPT_PATH} >/dev/null 2>&1; codex 'hello'"],
        cwd=str(launch_cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    config_text = (home / ".codex" / "config.toml").read_text(encoding="utf-8")
    assert f'[projects."{launch_cwd}"]' in config_text
    assert not (slot_home / "config.toml").exists()


def test_bashrc_uses_codex_executable_shim_instead_of_function() -> None:
    bashrc = Path.home() / ".bashrc"
    text = bashrc.read_text(encoding="utf-8")
    activation = 'source "$HOME/causyl/tools/bin/activate_mobile_agent_shell.sh"'
    assert activation not in text
    assert 'EIEL_CODEX_REAL_BIN="${EIEL_CODEX_REAL_BIN:-$HOME/.nvm/versions/node/v24.14.0/bin/codex}"' in text
    assert "codex_fix_shell()" in text
