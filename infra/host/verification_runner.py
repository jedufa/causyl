"""One-shot host verification runner for allowlisted actions."""

from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import shlex
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

from infra.host import verification_actions as _actions
from infra.host import verification_contract as _contract


def _parse_iso_utc(value: str) -> Any | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _contract._dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _status_timing_fields(*, status: str, timeout_s: int, started_utc: str, finished_utc: str, now_utc: str) -> Dict[str, Any]:
    started_dt = _parse_iso_utc(started_utc)
    now_dt = _parse_iso_utc(now_utc)
    finished_dt = _parse_iso_utc(finished_utc)
    deadline_utc = ""
    remaining_s = 0
    if started_dt is not None and int(timeout_s or 0) > 0:
        deadline_dt = started_dt + _contract._dt.timedelta(seconds=int(timeout_s))
        deadline_utc = deadline_dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")
        if finished_dt is not None:
            remaining_s = max(0, int((deadline_dt - finished_dt).total_seconds()))
        elif now_dt is not None and str(status or "").strip().lower() not in {"finished", "failed", "timed_out"}:
            remaining_s = max(0, int((deadline_dt - now_dt).total_seconds()))
    return {
        "deadline_utc": deadline_utc,
        "remaining_s": int(remaining_s),
    }


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_runner_status(
    *,
    job_dir: Path,
    request: Dict[str, Any],
    status: str,
    phase: str,
    message: str = "",
    timeout_s: int = 0,
    stall_timeout_s: int = 0,
    started_utc: str = "",
    finished_utc: str = "",
    last_update_utc: str = "",
    last_output_utc: str = "",
    elapsed_s: int = 0,
    heartbeat_interval_s: int = 0,
    output_seen: bool | None = None,
    subprocess_pid: int = 0,
    progress_note: str = "",
    timeout_source: str = "",
    stall_timeout_source: str = "",
) -> Dict[str, Any]:
    path = (job_dir / "status.json").resolve()
    request_path = (job_dir / "request.json").resolve()
    result_path = (job_dir / "result.json").resolve()
    stdout_path = (job_dir / "stdout.txt").resolve()
    stderr_path = (job_dir / "stderr.txt").resolve()
    previous = _read_json(path)
    now_utc = _contract.utc_now_iso()
    effective_started_utc = str(started_utc or previous.get("started_utc") or "").strip()
    if not effective_started_utc and str(status or "").strip().lower() == "running":
        effective_started_utc = now_utc
    payload = {
        "schema_version": "eiel_host_verification_status_v3",
        "request_id": str(request.get("request_id") or previous.get("request_id") or "").strip(),
        "workflow_id": str(request.get("workflow_id") or previous.get("workflow_id") or "").strip(),
        "action_id": str((((request.get("action") or {}) if isinstance(request.get("action"), dict) else {}).get("action_id") or previous.get("action_id") or "")).strip(),
        "status": str(status or previous.get("status") or "").strip().lower(),
        "phase": str(phase or previous.get("phase") or "").strip(),
        "message": str(message or previous.get("message") or "").strip(),
        "updated_utc": now_utc,
        "last_update_utc": str(last_update_utc or now_utc).strip(),
        "last_heartbeat_utc": now_utc,
        "started_utc": effective_started_utc,
        "finished_utc": str(finished_utc or previous.get("finished_utc") or "").strip(),
        "timeout_s": int(timeout_s or previous.get("timeout_s") or 0),
        "stall_timeout_s": int(stall_timeout_s or previous.get("stall_timeout_s") or 0),
        "heartbeat_interval_s": int(heartbeat_interval_s or previous.get("heartbeat_interval_s") or 0),
        "elapsed_s": int(elapsed_s or previous.get("elapsed_s") or 0),
        "last_output_utc": str(last_output_utc or previous.get("last_output_utc") or "").strip(),
        "output_seen": bool(previous.get("output_seen")) if output_seen is None else bool(output_seen),
        "subprocess_pid": int(subprocess_pid or previous.get("subprocess_pid") or 0),
        "runner_dispatch": str(previous.get("runner_dispatch") or "").strip(),
        "progress_note": str(progress_note or previous.get("progress_note") or "").strip(),
        "timeout_source": str(timeout_source or previous.get("timeout_source") or "").strip(),
        "stall_timeout_source": str(stall_timeout_source or previous.get("stall_timeout_source") or "").strip(),
        "job_dir": str(job_dir),
        "request_path": str(request_path),
        "result_path": str(result_path),
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }
    payload.update(
        _status_timing_fields(
            status=payload["status"],
            timeout_s=int(payload["timeout_s"] or 0),
            started_utc=str(payload["started_utc"] or ""),
            finished_utc=str(payload["finished_utc"] or ""),
            now_utc=now_utc,
        )
    )
    _contract.write_json_atomic(path, payload)
    return payload


def _classify_failure(*, exit_code: int, stdout_text: str, stderr_text: str) -> str:
    combined = f"{stdout_text}\n{stderr_text}".lower()
    if (
        "operation not permitted" in combined
        or "permission denied" in combined
        or "socket creation failed" in combined
        or "could not determine a usable wildcard ip" in combined
    ):
        return "environment_blocked"
    if "java_home" in combined or "sdk location not found" in combined or "command not found" in combined:
        return "environment_missing"
    if exit_code == 124:
        return "timeout"
    return "test_failed"


def _collect_artifacts(*, cwd: Path, globs: List[str]) -> List[str]:
    out: List[str] = []
    for pattern in globs:
        for raw in glob.glob(str((cwd / pattern).resolve()), recursive=True):
            p = Path(raw).resolve()
            if p.exists():
                out.append(str(p))
    return sorted(set(out))


def _append_line(path: Path, text: str) -> None:
    with open(path, "a", encoding="utf-8", newline="") as handle:
        handle.write(text)
        handle.flush()


def _default_stall_timeout_s(timeout_s: int) -> int:
    """Return a bounded no-output timeout for one verification job."""
    timeout_value = max(1, int(timeout_s or 0))
    # Long Android/Gradle runs can go quiet for several minutes while still
    # making progress, so keep the stall timeout independent from the wall
    # timeout and much less aggressive for long-running host jobs.
    return max(300, min(900, timeout_value // 4))


def _resolve_stall_timeout_s(*, timeout_s: int, requested_stall_timeout_s: Any = None) -> int:
    """Resolve one bounded no-output timeout, honoring explicit request override."""
    if requested_stall_timeout_s in (None, ""):
        return _default_stall_timeout_s(timeout_s)
    try:
        value = int(requested_stall_timeout_s)
    except Exception:
        return _default_stall_timeout_s(timeout_s)
    value = max(1, min(3600, value))
    return min(max(1, int(timeout_s or 1)), value)


def _stream_subprocess(
    *,
    argv: List[str],
    cwd: Path,
    env: Dict[str, str],
    timeout_s: int,
    stdout_path: Path,
    stderr_path: Path,
    stall_timeout_s: int,
    status_callback: Any | None = None,
) -> tuple[str, str, int]:
    """Run one subprocess while streaming output into durable job files."""
    started_marker = (
        f"[runner] started_utc={_contract.utc_now_iso()} cwd={cwd} "
        f"timeout_s={timeout_s} stall_timeout_s={stall_timeout_s}\n"
        f"[runner] argv={' '.join(shlex.quote(str(x)) for x in argv)}\n"
    )
    stdout_path.write_text(started_marker, encoding="utf-8")
    stderr_path.write_text("", encoding="utf-8")

    proc = subprocess.Popen(
        argv,
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
        env=env,
        start_new_session=True,
    )
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []
    started_at = time.monotonic()
    last_output_at = time.monotonic()
    last_heartbeat_at = time.monotonic()
    output_lock = threading.Lock()
    first_output_seen = False
    last_output_utc = ""
    heartbeat_interval_s = 15

    def _reader(stream: Any, sink_path: Path, chunks: List[str]) -> None:
        nonlocal last_output_at, first_output_seen, last_output_utc
        try:
            while True:
                line = stream.readline()
                if line == "":
                    break
                chunks.append(line)
                _append_line(sink_path, line)
                with output_lock:
                    last_output_at = time.monotonic()
                    last_output_utc = _contract.utc_now_iso()
                if (not first_output_seen) and callable(status_callback):
                    first_output_seen = True
                    try:
                        status_callback(
                            "streaming_output",
                            "process emitted output",
                            output_seen=True,
                            last_output_utc=last_output_utc,
                            elapsed_s=int(max(0.0, time.monotonic() - started_at)),
                            heartbeat_interval_s=heartbeat_interval_s,
                            subprocess_pid=int(proc.pid or 0),
                            progress_note="child process emitted output",
                        )
                    except Exception:
                        pass
        finally:
            try:
                stream.close()
            except Exception:
                pass

    stdout_thread = threading.Thread(
        target=_reader,
        args=(proc.stdout, stdout_path, stdout_chunks),
        name="verification-runner-stdout",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_reader,
        args=(proc.stderr, stderr_path, stderr_chunks),
        name="verification-runner-stderr",
        daemon=True,
    )
    stdout_thread.start()
    stderr_thread.start()
    if callable(status_callback):
        try:
            status_callback(
                "waiting_for_output",
                "subprocess started",
                output_seen=False,
                last_output_utc="",
                elapsed_s=0,
                heartbeat_interval_s=heartbeat_interval_s,
                subprocess_pid=int(proc.pid or 0),
                progress_note="child process started; waiting for first output",
            )
        except Exception:
            pass

    exit_code = 1
    try:
        while True:
            maybe_exit = proc.poll()
            if maybe_exit is not None:
                exit_code = int(maybe_exit)
                break
            now = time.monotonic()
            if (now - last_output_at) > float(stall_timeout_s):
                if callable(status_callback):
                    try:
                        status_callback(
                            "stall_timeout",
                            f"no child output for {stall_timeout_s}s; killing subprocess",
                            output_seen=first_output_seen,
                            last_output_utc=last_output_utc,
                            elapsed_s=int(max(0.0, now - started_at)),
                            heartbeat_interval_s=heartbeat_interval_s,
                            subprocess_pid=int(proc.pid or 0),
                            progress_note="runner killed stalled subprocess after no output window elapsed",
                        )
                    except Exception:
                        pass
                _terminate_process_tree(proc)
                _append_line(
                    stderr_path,
                    f"[runner] no child output for {stall_timeout_s}s; killed stalled subprocess\n",
                )
                exit_code = 124
                break
            if (now - started_at) > float(timeout_s):
                if callable(status_callback):
                    try:
                        status_callback(
                            "wall_timeout",
                            f"timed out after {timeout_s}s; killing subprocess",
                            output_seen=first_output_seen,
                            last_output_utc=last_output_utc,
                            elapsed_s=int(max(0.0, now - started_at)),
                            heartbeat_interval_s=heartbeat_interval_s,
                            subprocess_pid=int(proc.pid or 0),
                            progress_note="runner killed subprocess after wall timeout elapsed",
                        )
                    except Exception:
                        pass
                _terminate_process_tree(proc)
                _append_line(stderr_path, f"[runner] timed out after {timeout_s}s; killed subprocess\n")
                exit_code = 124
                break
            if callable(status_callback) and (now - last_heartbeat_at) >= float(heartbeat_interval_s):
                last_heartbeat_at = now
                try:
                    status_callback(
                        "waiting_for_output" if not first_output_seen else "streaming_output",
                        "subprocess still running without output yet" if not first_output_seen else "subprocess still running",
                        output_seen=first_output_seen,
                        last_output_utc=last_output_utc,
                        elapsed_s=int(max(0.0, now - started_at)),
                        heartbeat_interval_s=heartbeat_interval_s,
                        subprocess_pid=int(proc.pid or 0),
                        progress_note=(
                            "subprocess still running without output yet"
                            if not first_output_seen
                            else "subprocess still running and heartbeat refreshed after prior output"
                        ),
                    )
                except Exception:
                    pass
            time.sleep(0.25)
    finally:
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)

    stdout_text = stdout_path.read_text(encoding="utf-8")
    stderr_text = stderr_path.read_text(encoding="utf-8")
    return stdout_text, stderr_text, exit_code


def _terminate_process_tree(proc: subprocess.Popen[str]) -> None:
    """Terminate one subprocess and its descendants started in a new session."""
    try:
        pgid = os.getpgid(proc.pid)
    except Exception:
        pgid = 0
    if pgid > 0:
        try:
            os.killpg(pgid, signal.SIGKILL)
            return
        except Exception:
            pass
    try:
        proc.kill()
    except Exception:
        pass


def _load_env_from_script(script_path: Path, *, base_env: Dict[str, str]) -> Dict[str, str]:
    """Return environment after sourcing one shell script."""
    quoted = shlex.quote(str(script_path))
    cp = subprocess.run(
        ["bash", "-lc", f"source {quoted} >/dev/null 2>&1 && env -0"],
        capture_output=True,
        text=False,
        timeout=30,
        check=False,
        env=dict(base_env),
    )
    if int(cp.returncode) != 0:
        raise RuntimeError(f"failed to source env script: {script_path}")
    raw = bytes(cp.stdout or b"")
    out: Dict[str, str] = {}
    for chunk in raw.split(b"\0"):
        if not chunk or b"=" not in chunk:
            continue
        key_raw, value_raw = chunk.split(b"=", 1)
        key = key_raw.decode("utf-8", errors="replace")
        value = value_raw.decode("utf-8", errors="replace")
        if key:
            out[key] = value
    return out


def _build_subprocess_env(*, repo: Path, action_id: str) -> Dict[str, str]:
    """Build subprocess environment for one verification action."""
    env = dict(os.environ)
    if action_id == "android_unit_test":
        android_env_path = (repo / "tools" / "mobile" / "android_env.sh").resolve()
        if android_env_path.exists() and android_env_path.is_file():
            env.update(_load_env_from_script(android_env_path, base_env=env))
        gradle_user_home = (repo / ".tooling" / "gradle-home").resolve()
        android_user_home = (repo / ".tooling" / "android-user-home").resolve()
        android_avd_home = (android_user_home / "avd").resolve()
        env["GRADLE_USER_HOME"] = str(gradle_user_home)
        env["ANDROID_USER_HOME"] = str(android_user_home)
        env["ANDROID_AVD_HOME"] = str(android_avd_home)
        env["ANDROID_EMULATOR_HOME"] = str(android_user_home)
        env.pop("ANDROID_PREFS_ROOT", None)
        if str(env.get("JAVA_HOME") or "").strip() and not str(env.get("JDK_HOME") or "").strip():
            env["JDK_HOME"] = str(env["JAVA_HOME"])
        gradle_user_home.mkdir(parents=True, exist_ok=True)
        android_user_home.mkdir(parents=True, exist_ok=True)
        android_avd_home.mkdir(parents=True, exist_ok=True)
    return env


def _probe_android_gradle_host_capability() -> str | None:
    """Return a short error if the host blocks socket-based Gradle startup."""
    sock = None
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 0))
        return None
    except OSError as exc:
        return (
            "host blocks local server sockets required by Gradle daemon startup "
            f"({exc.__class__.__name__}: {exc})"
        )
    finally:
        if sock is not None:
            try:
                sock.close()
            except OSError:
                pass


def run_request(request: Dict[str, Any], *, repo_root: Path | None = None) -> Dict[str, Any]:
    """Execute one normalized verification request."""
    repo = Path(repo_root or _contract.repo_root()).resolve()
    try:
        normalized = _contract.normalize_request(request, repo=repo)
        job_dir = Path(normalized["artifacts"]["root_dir"]).resolve()
    except Exception as exc:
        raw_obj = request if isinstance(request, dict) else {}
        raw_action = raw_obj.get("action") if isinstance(raw_obj.get("action"), dict) else {}
        action_id = str(raw_action.get("action_id") or "invalid_request").strip() or "invalid_request"
        workflow_id = str(raw_obj.get("workflow_id") or "wf").strip() or "wf"
        request_id_value = str(raw_obj.get("request_id") or "").strip() or _contract.request_id(
            workflow_id=workflow_id,
            action_id=action_id,
        )
        job_dir = _contract.ensure_job_dir(request_id_value, repo=repo)
        request_path = (job_dir / "request.json").resolve()
        stdout_path = (job_dir / "stdout.txt").resolve()
        stderr_path = (job_dir / "stderr.txt").resolve()
        result_path = (job_dir / "result.json").resolve()
        request_stub = {
            "schema_version": _contract.SCHEMA_REQUEST,
            "request_id": request_id_value,
            "workflow_id": workflow_id,
            "turn": str(raw_obj.get("turn") or "").strip(),
            "requested_by_role": str(raw_obj.get("requested_by_role") or "assurer").strip() or "assurer",
            "binding": raw_obj.get("binding") if isinstance(raw_obj.get("binding"), dict) else {"type": "", "id": ""},
            "action": {"action_id": action_id, "params": dict(raw_action.get("params") or {}) if isinstance(raw_action.get("params"), dict) else {}},
            "artifacts": {
                "root_dir": str(job_dir),
                "capture_stdout": True,
                "capture_stderr": True,
                "artifact_globs": [],
            },
            "policy": {
                "timeout_s": 0,
                "stall_timeout_s": None,
                "allow_network": False,
                "require_clean_exit": True,
            },
            "reason": str(raw_obj.get("reason") or "").strip(),
            "request_digest": "",
            "created_utc": _contract.utc_now_iso(),
        }
        _contract.write_json_atomic(request_path, request_stub)
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(str(exc), encoding="utf-8")
        result = _contract.build_result(
            request=request_stub,
            ok=False,
            status="failed",
            exit_code=2,
            started_utc=_contract.utc_now_iso(),
            finished_utc=_contract.utc_now_iso(),
            duration_ms=0,
            cwd="",
            argv=[],
            evidence={
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "artifact_paths": [],
                "summary": str(exc)[:2000],
            },
            failure_kind="invalid_request",
        )
        _contract.write_json_atomic(result_path, result)
        return result
    job_dir.mkdir(parents=True, exist_ok=True)
    request_path = (job_dir / "request.json").resolve()
    stdout_path = (job_dir / "stdout.txt").resolve()
    stderr_path = (job_dir / "stderr.txt").resolve()
    result_path = (job_dir / "result.json").resolve()
    _contract.write_json_atomic(request_path, normalized)

    action = normalized["action"]
    try:
        spec = _actions.get_action_spec(str(action["action_id"]))
        argv = spec.build_argv(dict(action.get("params") or {}))
    except Exception as exc:
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text(str(exc), encoding="utf-8")
        result = _contract.build_result(
            request=normalized,
            ok=False,
            status="failed",
            exit_code=2,
            started_utc=_contract.utc_now_iso(),
            finished_utc=_contract.utc_now_iso(),
            duration_ms=0,
            cwd="",
            argv=[],
            evidence={
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "artifact_paths": [],
                "summary": str(exc)[:2000],
            },
            failure_kind="invalid_request",
        )
        _contract.write_json_atomic(result_path, result)
        return result

    cwd = spec.cwd(repo)
    timeout_s = int((normalized.get("policy") or {}).get("timeout_s") or spec.timeout_s_default)
    stall_timeout_s = _resolve_stall_timeout_s(
        timeout_s=timeout_s,
        requested_stall_timeout_s=((normalized.get("policy") or {}) if isinstance(normalized.get("policy"), dict) else {}).get("stall_timeout_s"),
    )
    heartbeat_interval_s = 15
    raw_policy = request.get("policy") if isinstance(request.get("policy"), dict) else {}
    timeout_source = "request_policy" if raw_policy.get("timeout_s") not in (None, "") else "action_default"
    stall_timeout_source = "request_policy" if raw_policy.get("stall_timeout_s") not in (None, "") else "runner_default"
    env = _build_subprocess_env(repo=repo, action_id=str(action["action_id"]))
    host_block_reason = ""
    if str(action["action_id"]) == "android_unit_test":
        host_block_reason = str(_probe_android_gradle_host_capability() or "")
    started_utc = _contract.utc_now_iso()
    _write_runner_status(
        job_dir=job_dir,
        request=normalized,
        status="running",
        phase="launching",
        message="runner accepted request and is preparing host verification",
        timeout_s=timeout_s,
        stall_timeout_s=stall_timeout_s,
        heartbeat_interval_s=heartbeat_interval_s,
        started_utc=started_utc,
        progress_note=(
            "request accepted; validating environment and preparing command "
            f"(wall_timeout={timeout_s}s, no_output_timeout={stall_timeout_s}s)"
        ),
        timeout_source=timeout_source,
        stall_timeout_source=stall_timeout_source,
    )
    t0 = time.time()
    stdout_text = ""
    stderr_text = ""
    exit_code = 1
    failure_kind = "runner_internal_error"
    ok = False
    if host_block_reason:
        _write_runner_status(
            job_dir=job_dir,
            request=normalized,
            status="failed",
            phase="blocked",
            message=host_block_reason,
            timeout_s=timeout_s,
            stall_timeout_s=stall_timeout_s,
            started_utc=started_utc,
            finished_utc=_contract.utc_now_iso(),
            progress_note="environment preflight blocked execution before subprocess launch",
            timeout_source=timeout_source,
            stall_timeout_source=stall_timeout_source,
        )
        stderr_text = host_block_reason
        exit_code = 126
        failure_kind = "environment_blocked"
    else:
        try:
            stdout_text, stderr_text, exit_code = _stream_subprocess(
                argv=argv,
                cwd=cwd,
                env=env,
                timeout_s=timeout_s,
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                stall_timeout_s=stall_timeout_s,
                status_callback=lambda phase, message, **meta: _write_runner_status(
                    job_dir=job_dir,
                    request=normalized,
                    status="running",
                    phase=str(phase or "running"),
                    message=str(message or ""),
                    timeout_s=timeout_s,
                    stall_timeout_s=stall_timeout_s,
                    heartbeat_interval_s=int(meta.get("heartbeat_interval_s") or heartbeat_interval_s),
                    started_utc=started_utc,
                    last_output_utc=str(meta.get("last_output_utc") or ""),
                    elapsed_s=int(meta.get("elapsed_s") or 0),
                    output_seen=meta.get("output_seen"),
                    subprocess_pid=int(meta.get("subprocess_pid") or 0),
                    progress_note=str(meta.get("progress_note") or ""),
                    timeout_source=timeout_source,
                    stall_timeout_source=stall_timeout_source,
                ),
            )
            ok = exit_code == 0
            failure_kind = "" if ok else _classify_failure(exit_code=exit_code, stdout_text=stdout_text, stderr_text=stderr_text)
        except FileNotFoundError as exc:
            _write_runner_status(
                job_dir=job_dir,
                request=normalized,
                status="failed",
                phase="environment_missing",
                message=str(exc),
                timeout_s=timeout_s,
                stall_timeout_s=stall_timeout_s,
                heartbeat_interval_s=heartbeat_interval_s,
                started_utc=started_utc,
                finished_utc=_contract.utc_now_iso(),
                progress_note="runner could not launch command because executable was missing",
                timeout_source=timeout_source,
                stall_timeout_source=stall_timeout_source,
            )
            stderr_text = str(exc)
            exit_code = 127
            failure_kind = "environment_missing"
        except PermissionError as exc:
            _write_runner_status(
                job_dir=job_dir,
                request=normalized,
                status="failed",
                phase="environment_blocked",
                message=str(exc),
                timeout_s=timeout_s,
                stall_timeout_s=stall_timeout_s,
                heartbeat_interval_s=heartbeat_interval_s,
                started_utc=started_utc,
                finished_utc=_contract.utc_now_iso(),
                progress_note="runner could not launch command because execution was blocked",
                timeout_source=timeout_source,
                stall_timeout_source=stall_timeout_source,
            )
            stderr_text = str(exc)
            exit_code = 126
            failure_kind = "environment_blocked"
        except Exception as exc:
            _write_runner_status(
                job_dir=job_dir,
                request=normalized,
                status="failed",
                phase="runner_internal_error",
                message=str(exc),
                timeout_s=timeout_s,
                stall_timeout_s=stall_timeout_s,
                heartbeat_interval_s=heartbeat_interval_s,
                started_utc=started_utc,
                finished_utc=_contract.utc_now_iso(),
                progress_note="runner hit an internal error before collecting artifacts",
                timeout_source=timeout_source,
                stall_timeout_source=stall_timeout_source,
            )
            stderr_text = str(exc)
            exit_code = 1
            failure_kind = "runner_internal_error"

    if not stdout_path.exists():
        stdout_path.write_text(stdout_text, encoding="utf-8")
    if not stderr_path.exists():
        stderr_path.write_text(stderr_text, encoding="utf-8")
    finished_utc = _contract.utc_now_iso()
    _write_runner_status(
        job_dir=job_dir,
        request=normalized,
        status="running" if ok else ("timed_out" if failure_kind == "timeout" else "failed"),
        phase="collecting_artifacts",
        message="runner finished process execution and is collecting artifacts",
        timeout_s=timeout_s,
        stall_timeout_s=stall_timeout_s,
        heartbeat_interval_s=heartbeat_interval_s,
        started_utc=started_utc,
        finished_utc=finished_utc if ok else "",
        progress_note="process finished; collecting artifacts and building structured result",
        timeout_source=timeout_source,
        stall_timeout_source=stall_timeout_source,
    )
    artifact_paths = _collect_artifacts(cwd=cwd, globs=list(spec.artifact_globs) + list(normalized["artifacts"].get("artifact_globs") or []))
    summary = "verification passed" if ok else (stderr_text.strip() or stdout_text.strip() or failure_kind)
    result = _contract.build_result(
        request=normalized,
        ok=ok,
        status="passed" if ok else "failed",
        exit_code=exit_code,
        started_utc=started_utc,
        finished_utc=finished_utc,
        duration_ms=int(max(0.0, (time.time() - t0) * 1000.0)),
        cwd=str(cwd),
        argv=[str(x) for x in argv],
        evidence={
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "artifact_paths": artifact_paths,
            "summary": summary[:2000],
        },
        failure_kind=failure_kind,
    )
    _write_runner_status(
        job_dir=job_dir,
        request=normalized,
        status="finished" if ok else ("timed_out" if failure_kind == "timeout" else "failed"),
        phase="completed",
        message=str(summary[:2000]),
        timeout_s=timeout_s,
        stall_timeout_s=stall_timeout_s,
        heartbeat_interval_s=heartbeat_interval_s,
        started_utc=started_utc,
        finished_utc=finished_utc,
        progress_note="result.json written",
        timeout_source=timeout_source,
        stall_timeout_source=stall_timeout_source,
    )
    _contract.write_json_atomic(result_path, result)
    return result


def main(argv: List[str] | None = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Run one allowlisted host verification request.")
    parser.add_argument("--request", required=True, help="Path to request.json")
    args = parser.parse_args(argv)
    req_path = Path(args.request).resolve()
    request_obj = json.loads(req_path.read_text(encoding="utf-8"))
    result = run_request(request_obj)
    sys.stdout.write(json.dumps(result, sort_keys=True, separators=(",", ":"), ensure_ascii=False))
    sys.stdout.write("\n")
    return 0 if bool(result.get("ok")) else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
