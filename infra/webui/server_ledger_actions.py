"""infra.webui.server_ledger_actions

Executable ledger actions (AG01 MVP-1 core).
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from tools.git.support import commit_journal as _commit_journal
from infra.webui.ledger.ledger_manager import ChangeLedger

try:
    import fcntl as _fcntl  # POSIX advisory file locking for multi-process safety.
except Exception:  # pragma: no cover - non-POSIX fallback.
    _fcntl = None  # type: ignore[assignment]


_TASK_ID_RE = re.compile(r"^[a-z0-9]{3}$")
_VAR_RE = re.compile(r"\$\{([A-Z0-9_]+)\}")
_GIT_REF_RE = re.compile(r"^[A-Za-z0-9._/\-]{1,128}$")

_ALLOWED_SCRIPT_ROOTS = (
    "tools/diagnostics",
    "infra/webui/scripts",
)

_WORKFLOW_NEXT: Dict[str, str] = {
    "draft": "ready_for_implement",
    "ready_for_implement": "in_progress",
    "in_progress": "ready_for_assure",
    "ready_for_assure": "verified",
    "verified": "done",
}

_JOB_LOCK = threading.Lock()
_JOB_COUNTER = 0
_RUNNING: Dict[str, Dict[str, Any]] = {}
_FILE_LOCKS_GUARD = threading.Lock()
_FILE_LOCKS: Dict[str, threading.Lock] = {}


class LedgerActionError(RuntimeError):
    """Raised when action spec or execution is invalid."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _safe_under(base: Path, child: Path) -> Optional[Path]:
    try:
        b = base.resolve()
        c = child.resolve()
        c.relative_to(b)
        return c
    except Exception:
        return None


def _next_job_counter() -> int:
    global _JOB_COUNTER
    with _JOB_LOCK:
        _JOB_COUNTER += 1
        return int(_JOB_COUNTER)


def _job_id(uid: str, action_id: str) -> str:
    return f"act_{_utc_compact()}_{_next_job_counter():06d}_{_safe_name(uid)}_{_safe_name(action_id)}"


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "").strip()).strip("-") or "x"


def _file_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _FILE_LOCKS_GUARD:
        lk = _FILE_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _FILE_LOCKS[key] = lk
        return lk


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    lk = _file_lock(path)
    with lk:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            if _fcntl is not None:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
            try:
                f.write(line)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                if _fcntl is not None:
                    _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    out: List[Dict[str, Any]] = []
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            obj = json.loads(s)
            if isinstance(obj, dict):
                out.append(obj)
        except Exception:
            continue
    return out


def _feature_enabled() -> bool:
    v = str(os.environ.get("EIEL_WEBUI_LEDGER_ACTIONS", "1") or "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _actions_for_uid(ledger: ChangeLedger, uid: str) -> Tuple[Path, Dict[str, Any], List[Dict[str, Any]]]:
    item_dir = ledger.get_item_dir(uid)
    meta = ledger.read_item(uid).get("meta")
    if not isinstance(meta, dict):
        raise LedgerActionError("meta missing")
    actions = meta.get("actions")
    if actions is None:
        return item_dir, meta, []
    if not isinstance(actions, list):
        raise LedgerActionError("meta.actions must be a list")
    out: List[Dict[str, Any]] = []
    for a in actions:
        if isinstance(a, dict):
            out.append(a)
    return item_dir, meta, out


def _substitute_vars(text: str, values: Dict[str, str]) -> str:
    s = str(text or "")
    bad = [m.group(1) for m in _VAR_RE.finditer(s) if m.group(1) not in values]
    if bad:
        raise LedgerActionError(f"unknown substitution variables: {sorted(set(bad))}")
    def _rep(m: re.Match[str]) -> str:
        return str(values.get(m.group(1), ""))
    return _VAR_RE.sub(_rep, s)


def _validate_git_ref(v: str) -> str:
    s = str(v or "").strip()
    if not s:
        return s
    if not _GIT_REF_RE.match(s):
        raise LedgerActionError("invalid GIT_REF")
    if ".." in s or s.startswith("/"):
        raise LedgerActionError("invalid GIT_REF")
    return s


def _validate_task_id(v: str) -> str:
    s = str(v or "").strip().lower()
    if not s:
        return s
    if not _TASK_ID_RE.match(s):
        raise LedgerActionError("invalid TASK_ID")
    return s


def _resolve_tokens(*, ledger: ChangeLedger, uid: str, task_id: str, git_ref: str, runs_root: str) -> Dict[str, str]:
    repo_root = str(ledger.repo_root.resolve())
    ledger.get_item_dir(uid)

    task_ok = _validate_task_id(task_id)
    git_ok = _validate_git_ref(git_ref)

    rr = str(runs_root or "").strip()
    if rr:
        runs_p = Path(rr).expanduser().resolve()
    else:
        runs_p = (ledger.repo_root / "runtime" / "live").resolve()
    if _safe_under(ledger.repo_root, runs_p) is None:
        raise LedgerActionError("RUNS_ROOT must resolve under repo root")

    return {
        "REPO_ROOT": repo_root,
        "LEDGER_UID": str(uid),
        "TASK_ID": task_ok,
        "GIT_REF": git_ok,
        "RUNS_ROOT": str(runs_p),
    }


def _resolve_script(repo_root: Path, script_text: str, tokens: Dict[str, str]) -> Path:
    script_sub = _substitute_vars(str(script_text or "").strip(), tokens)
    if not script_sub:
        raise LedgerActionError("action.script is required")
    script_p = (repo_root / script_sub).resolve()
    if _safe_under(repo_root, script_p) is None:
        raise LedgerActionError("script path escapes repo root")
    if not script_p.exists() or not script_p.is_file():
        raise LedgerActionError(f"script not found: {script_sub}")
    # allowlist roots
    ok = False
    for root_rel in _ALLOWED_SCRIPT_ROOTS:
        ar = (repo_root / root_rel).resolve()
        if _safe_under(ar, script_p) is not None:
            ok = True
            break
    if not ok:
        raise LedgerActionError("script path is outside allowlisted roots")
    if script_p.name in ("bash", "sh"):
        raise LedgerActionError("direct shell executables are not allowed")
    return script_p


def _action_spec(actions: List[Dict[str, Any]], action_id: str) -> Dict[str, Any]:
    aid = str(action_id or "").strip()
    if not aid:
        raise LedgerActionError("action_id is required")
    for a in actions:
        if str(a.get("id") or "").strip() == aid:
            return a
    raise LedgerActionError(f"action not found: {aid}")


def _build_cmd(*, repo_root: Path, spec: Dict[str, Any], tokens: Dict[str, str]) -> Tuple[List[str], Path, Dict[str, str], int]:
    script = _resolve_script(repo_root, str(spec.get("script") or ""), tokens)
    args = spec.get("args")
    arg_list: List[str] = []
    if args is not None:
        if not isinstance(args, list):
            raise LedgerActionError("action.args must be a list")
        for x in args:
            arg_list.append(_substitute_vars(str(x or ""), tokens))

    # No shell mode; execute script directly or via python.
    cmd: List[str]
    if script.suffix.lower() == ".py":
        cmd = [sys.executable, str(script)] + arg_list
    else:
        cmd = [str(script)] + arg_list

    # Forbid injecting shell wrappers in args.
    bad_patterns = {"-c", "--command"}
    if any(a in bad_patterns for a in cmd):
        raise LedgerActionError("shell command wrappers are not allowed")

    cwd_raw = str(spec.get("cwd") or "${REPO_ROOT}")
    cwd_sub = _substitute_vars(cwd_raw, tokens)
    cwd = Path(cwd_sub).expanduser().resolve()
    if _safe_under(repo_root, cwd) is None:
        raise LedgerActionError("cwd escapes repo root")
    cwd.mkdir(parents=True, exist_ok=True)

    env_over = spec.get("env")
    env = dict(os.environ)
    if env_over is not None:
        if not isinstance(env_over, dict):
            raise LedgerActionError("action.env must be an object")
        for k, v in env_over.items():
            kk = str(k or "").strip()
            if not kk:
                continue
            env[kk] = _substitute_vars(str(v or ""), tokens)

    timeout_s = int(spec.get("timeout_s") or 1800)
    timeout_s = max(1, min(timeout_s, 24 * 3600))
    return cmd, cwd, env, timeout_s


def _actions_dir(item_dir: Path) -> Path:
    p = (item_dir / "artifacts" / "actions").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _jobs_file(item_dir: Path) -> Path:
    return (_actions_dir(item_dir) / "jobs.jsonl").resolve()


def _artifact_paths(item_dir: Path, action_id: str, job_id: str) -> Tuple[Path, Path]:
    ts = _utc_compact()
    aid = _safe_name(action_id)
    jid = _safe_name(job_id)
    base = f"{ts}__{aid}__{jid}"
    ad = _actions_dir(item_dir)
    return (ad / f"{base}.log").resolve(), (ad / f"{base}.result.json").resolve()


def _write_result(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = (path.parent / f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}").resolve()
    raw = json.dumps(data, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _is_pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False


def _tail_log(path: Path, *, lines: int = 120) -> List[str]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    want = max(1, min(2000, int(lines)))
    chunk_size = 8192
    max_scan_bytes = 8 * 1024 * 1024
    scanned = 0
    buf = bytearray()
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            pos = f.tell()
            while pos > 0 and scanned < max_scan_bytes and buf.count(b"\n") <= want:
                read_n = min(chunk_size, pos, max_scan_bytes - scanned)
                pos -= read_n
                f.seek(pos)
                chunk = f.read(read_n)
                if not chunk:
                    break
                buf[:0] = chunk
                scanned += len(chunk)
    except Exception:
        return []
    raw = bytes(buf).decode("utf-8", errors="replace").splitlines()
    if len(raw) > want:
        raw = raw[-want:]
    return raw


def _record_job(item_dir: Path, row: Dict[str, Any]) -> None:
    _append_jsonl(_jobs_file(item_dir), row)


def _start_runner(
    *,
    ledger: ChangeLedger,
    uid: str,
    item_dir: Path,
    action_id: str,
    label: str,
    cmd: List[str],
    cwd: Path,
    env: Dict[str, str],
    timeout_s: int,
    job_id: str,
    log_path: Path,
    result_path: Path,
) -> Dict[str, Any]:
    jid = str(job_id or "").strip()
    if not jid:
        raise LedgerActionError("job_id is required")
    start_ts = time.time()
    start_iso = _utc_iso()

    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        start_new_session=True,
    )

    state = {
        "job_id": jid,
        "uid": uid,
        "action_id": action_id,
        "label": label,
        "status": "running",
        "pid": int(proc.pid or 0),
        "start_utc": start_iso,
        "cmd_resolved": cmd,
        "cwd": str(cwd),
        "log_path": str(log_path),
        "result_path": str(result_path),
        "timeout_s": int(timeout_s),
        "cancel_requested": False,
        "force_cancel_requested": False,
    }

    _record_job(item_dir, {"schema_version": "ledger_action_job_v1", **state, "event": "started", "utc": _utc_iso()})

    with _JOB_LOCK:
        _RUNNING[jid] = {"proc": proc, "state": state, "item_dir": item_dir, "ledger": ledger}

    def _runner() -> None:
        cancel_mode = ""
        try:
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a", encoding="utf-8", newline="\n") as lf:
                while True:
                    if proc.stdout is not None:
                        line = proc.stdout.readline()
                    else:
                        line = ""
                    if line:
                        lf.write(line)
                        lf.flush()
                    rc = proc.poll()
                    if rc is not None and not line:
                        break
                    # timeout handling
                    now = time.time()
                    if (now - start_ts) > float(timeout_s):
                        cancel_mode = "timeout"
                        try:
                            proc.terminate()
                        except Exception:
                            pass
                        time.sleep(1.0)
                        if proc.poll() is None:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        break

                    with _JOB_LOCK:
                        rr = _RUNNING.get(jid, {})
                        st = rr.get("state", {})
                        if bool(st.get("force_cancel_requested")):
                            cancel_mode = "killed"
                            try:
                                proc.kill()
                            except Exception:
                                pass
                            break
                        if bool(st.get("cancel_requested")):
                            cancel_mode = "canceled"
                            try:
                                proc.terminate()
                            except Exception:
                                pass

            rc = proc.wait(timeout=10)
        except Exception as e:
            rc = -1
            cancel_mode = cancel_mode or f"runner_error:{e}"

        end_iso = _utc_iso()
        end_ts = time.time()
        duration = max(0.0, end_ts - start_ts)

        result_obj = {
            "schema_version": "ledger_action_result_v1",
            "uid": uid,
            "action_id": action_id,
            "job_id": jid,
            "start_utc": start_iso,
            "end_utc": end_iso,
            "duration_s": duration,
            "cmd_resolved": cmd,
            "cwd": str(cwd),
            "exit_code": int(rc),
            "status": ("ok" if int(rc) == 0 and not cancel_mode else "failed"),
            "cancel_mode": cancel_mode,
            "pid": int(proc.pid or 0),
            "host": socket.gethostname(),
            "log_path": str(log_path),
            "errors": "",
        }
        _write_result(result_path, result_obj)

        result_sha = _sha256_file(result_path) if result_path.exists() else ""
        log_sha = _sha256_file(log_path) if log_path.exists() else ""

        end_row = {
            "schema_version": "ledger_action_job_v1",
            "job_id": jid,
            "uid": uid,
            "action_id": action_id,
            "event": "finished",
            "utc": end_iso,
            "status": result_obj["status"],
            "exit_code": int(rc),
            "cancel_mode": cancel_mode,
            "log_path": str(log_path),
            "result_path": str(result_path),
            "duration_s": duration,
        }
        _record_job(item_dir, end_row)

        try:
            rel_log = log_path.relative_to(item_dir).as_posix()
            rel_result = result_path.relative_to(item_dir).as_posix()
        except Exception:
            rel_log = str(log_path)
            rel_result = str(result_path)
        ledger.append_event(
            uid,
            event_type="ledger_action_run",
            payload={
                "uid": uid,
                "action_id": action_id,
                "job_id": jid,
                "status": result_obj["status"],
                "exit_code": int(rc),
                "duration_s": duration,
                "cmd_resolved": cmd,
                "start_utc": start_iso,
                "end_utc": end_iso,
                "pid": int(proc.pid or 0),
                "host": socket.gethostname(),
                "artifacts": {
                    "log_path": rel_log,
                    "result_path": rel_result,
                    "log_sha256": log_sha,
                    "result_sha256": result_sha,
                },
            },
        )

        with _JOB_LOCK:
            _RUNNING.pop(jid, None)

    th = threading.Thread(target=_runner, daemon=True, name=f"ledger-action-{jid}")
    th.start()
    return {
        "ok": True,
        "job_id": jid,
        "uid": uid,
        "action_id": action_id,
        "status": "running",
        "pid": int(proc.pid or 0),
        "log_path": str(log_path),
        "result_path": str(result_path),
        "start_utc": start_iso,
        "cmd_resolved": cmd,
        "cwd": str(cwd),
        "timeout_s": int(timeout_s),
    }


def run_action(
    *,
    uid: str,
    action_id: str,
    task_id: str = "",
    git_ref: str = "",
    runs_root: str = "",
) -> Dict[str, Any]:
    if not _feature_enabled():
        raise LedgerActionError("ledger actions are disabled by feature flag")
    ledger = ChangeLedger(repo_root=_repo_root())
    item_dir, _meta, actions = _actions_for_uid(ledger, uid)
    spec = _action_spec(actions, action_id)
    tokens = _resolve_tokens(ledger=ledger, uid=uid, task_id=task_id, git_ref=git_ref, runs_root=runs_root)
    cmd, cwd, env, timeout_s = _build_cmd(repo_root=ledger.repo_root, spec=spec, tokens=tokens)
    jid = _job_id(uid, action_id)
    log_path, result_path = _artifact_paths(item_dir, action_id, jid)
    return _start_runner(
        ledger=ledger,
        uid=uid,
        item_dir=item_dir,
        action_id=action_id,
        label=str(spec.get("label") or action_id),
        cmd=cmd,
        cwd=cwd,
        env=env,
        timeout_s=timeout_s,
        job_id=jid,
        log_path=log_path,
        result_path=result_path,
    )


def _reconcile_uid(uid: str) -> None:
    ledger = ChangeLedger(repo_root=_repo_root())
    item_dir = ledger.get_item_dir(uid)
    rows = _read_jsonl(_jobs_file(item_dir))
    latest: Dict[str, Dict[str, Any]] = {}
    for r in rows:
        jid = str(r.get("job_id") or "").strip()
        if not jid:
            continue
        latest[jid] = r
    for jid, row in latest.items():
        event = str(row.get("event") or "").strip().lower()
        status = str(row.get("status") or "").strip().lower()
        if event == "started":
            needs_reconcile = True
        elif event == "reconciled" and status == "running_unknown":
            needs_reconcile = True
        else:
            needs_reconcile = False
        if not needs_reconcile:
            continue
        if jid in _RUNNING:
            continue
        pid = int(row.get("pid") or 0)
        reconciled_status = "running_unknown" if _is_pid_alive(pid) else "stale"
        if event == "reconciled" and status == reconciled_status:
            continue
        _record_job(
            item_dir,
            {
                "schema_version": "ledger_action_job_v1",
                "job_id": jid,
                "uid": uid,
                "action_id": str(row.get("action_id") or ""),
                "event": "reconciled",
                "utc": _utc_iso(),
                "status": reconciled_status,
                "pid": pid,
            },
        )


def action_status(*, uid: str, job_id: str = "", limit: int = 80) -> Dict[str, Any]:
    ledger = ChangeLedger(repo_root=_repo_root())
    _reconcile_uid(uid)
    item_dir = ledger.get_item_dir(uid)
    rows = _read_jsonl(_jobs_file(item_dir))
    lim = max(1, min(1000, int(limit)))
    if len(rows) > lim:
        rows = rows[-lim:]
    with _JOB_LOCK:
        running = {
            jid: {
                "job_id": jid,
                "status": str(obj.get("state", {}).get("status") or "running"),
                "pid": int(obj.get("state", {}).get("pid") or 0),
                "action_id": str(obj.get("state", {}).get("action_id") or ""),
                "start_utc": str(obj.get("state", {}).get("start_utc") or ""),
                "log_tail": _tail_log(Path(str(obj.get("state", {}).get("log_path") or ""))),
            }
            for jid, obj in _RUNNING.items()
            if str(obj.get("state", {}).get("uid") or "") == str(uid)
        }

    if job_id:
        for r in reversed(rows):
            if str(r.get("job_id") or "") == str(job_id):
                return {"ok": True, "uid": uid, "job": r, "running": running.get(job_id)}
        return {"ok": True, "uid": uid, "job": None, "running": running.get(job_id)}

    return {"ok": True, "uid": uid, "jobs": rows, "running": list(running.values())}


def action_log(*, uid: str, job_id: str, lines: int = 200) -> Dict[str, Any]:
    """Return tail lines for a specific action job log."""
    ledger = ChangeLedger(repo_root=_repo_root())
    item_dir = ledger.get_item_dir(uid)
    _reconcile_uid(uid)
    rows = _read_jsonl(_jobs_file(item_dir))
    target: Optional[Dict[str, Any]] = None
    jid = str(job_id or "").strip()
    if not jid:
        raise LedgerActionError("job_id is required")
    for r in reversed(rows):
        if str(r.get("job_id") or "") == jid:
            target = r
            break
    with _JOB_LOCK:
        run = _RUNNING.get(jid)
    path_s = ""
    if run and isinstance(run, dict):
        st = run.get("state") if isinstance(run.get("state"), dict) else {}
        path_s = str(st.get("log_path") or "")
    if (not path_s) and isinstance(target, dict):
        path_s = str(target.get("log_path") or "")
    if not path_s:
        return {"ok": True, "uid": uid, "job_id": jid, "log_path": "", "lines": []}
    p = Path(path_s).resolve()
    if _safe_under(item_dir, p) is None:
        raise LedgerActionError("log path escapes ledger item")
    tail = _tail_log(p, lines=max(1, min(2000, int(lines))))
    try:
        rel = p.relative_to(item_dir).as_posix()
    except Exception:
        rel = str(p)
    return {"ok": True, "uid": uid, "job_id": jid, "log_path": rel, "lines": tail}


def _cancel_job(*, uid: str, job_id: str, force: bool) -> Dict[str, Any]:
    jid = str(job_id or "").strip()
    if not jid:
        raise LedgerActionError("job_id is required")
    with _JOB_LOCK:
        rr = _RUNNING.get(jid)
        if not rr:
            return {"ok": True, "uid": uid, "job_id": jid, "status": "not_running"}
        st = rr.get("state", {})
        if str(st.get("uid") or "") != str(uid):
            raise LedgerActionError("job does not belong to uid")
        st["cancel_requested"] = True
        if force:
            st["force_cancel_requested"] = True
        rr["state"] = st
        _RUNNING[jid] = rr
        proc = rr.get("proc")
    try:
        if proc is not None:
            if force:
                proc.kill()
            else:
                proc.terminate()
    except Exception:
        pass
    return {"ok": True, "uid": uid, "job_id": jid, "status": ("force_cancel_requested" if force else "cancel_requested")}


def cancel_action(*, uid: str, job_id: str) -> Dict[str, Any]:
    return _cancel_job(uid=uid, job_id=job_id, force=False)


def force_cancel_action(*, uid: str, job_id: str) -> Dict[str, Any]:
    return _cancel_job(uid=uid, job_id=job_id, force=True)


def _latest_action_success(item_dir: Path, action_id: str) -> bool:
    ad = _actions_dir(item_dir)
    pat = f"*__{_safe_name(action_id)}__*.result.json"
    files = sorted(ad.glob(pat))
    if not files:
        return False
    p = files[-1]
    try:
        obj = json.loads(p.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return False
    try:
        exit_code = int(obj.get("exit_code", 1))
    except Exception:
        exit_code = 1
    return exit_code == 0


def _evidence_missing(item_dir: Path, required: Any) -> List[str]:
    if required is None:
        return []
    if not isinstance(required, list):
        raise LedgerActionError("required_evidence must be a list")
    missing: List[str] = []
    for ent in required:
        if isinstance(ent, str):
            s = ent.strip()
            if not s:
                continue
            if s.startswith("action_success:"):
                aid = s.split(":", 1)[1].strip()
                if not _latest_action_success(item_dir, aid):
                    missing.append(s)
            else:
                p = (item_dir / s).resolve()
                if _safe_under(item_dir, p) is None or not p.exists():
                    missing.append(s)
            continue
        if isinstance(ent, dict):
            kind = str(ent.get("kind") or "").strip()
            if kind == "action_success":
                aid = str(ent.get("action_id") or "").strip()
                if not aid or not _latest_action_success(item_dir, aid):
                    missing.append(f"action_success:{aid or '?'}")
                continue
            if kind == "artifact_exists":
                rel = str(ent.get("path") or "").strip()
                p = (item_dir / rel).resolve()
                if not rel or _safe_under(item_dir, p) is None or not p.exists():
                    missing.append(f"artifact_exists:{rel or '?'}")
                continue
            missing.append(f"unknown_kind:{kind or '?'}")
            continue
        missing.append("invalid_required_evidence_entry")
    return missing


def mark_verified(*, uid: str) -> Dict[str, Any]:
    ledger = ChangeLedger(repo_root=_repo_root())
    item_dir, meta, _actions = _actions_for_uid(ledger, uid)
    missing = _evidence_missing(item_dir, meta.get("required_evidence"))
    if missing:
        return {
            "ok": False,
            "uid": uid,
            "blocked": True,
            "reason": "required_evidence_missing",
            "missing": missing,
        }
    try:
        journal = _commit_journal.scan_pending_entries(repo=_repo_root())
        audit = _commit_journal.audit_worktree_coverage(repo=_repo_root())
    except Exception as exc:
        return {
            "ok": False,
            "uid": uid,
            "blocked": True,
            "reason": "commit_journal_scan_error",
            "error": str(exc),
        }
    invalid_count = int(journal.get("invalid_count") or 0)
    pending_count = int(journal.get("pending_count") or 0)
    changed_paths = list(audit.get("changed_paths") or [])
    uncovered = list(audit.get("uncovered_paths") or [])
    if invalid_count > 0:
        return {
            "ok": False,
            "uid": uid,
            "blocked": True,
            "reason": "commit_journal_invalid",
            "invalid_count": invalid_count,
        }
    if changed_paths and pending_count <= 0:
        return {
            "ok": False,
            "uid": uid,
            "blocked": True,
            "reason": "commit_journal_missing",
            "changed_paths": changed_paths[:20],
        }
    if uncovered:
        return {
            "ok": False,
            "uid": uid,
            "blocked": True,
            "reason": "commit_journal_uncovered_paths",
            "uncovered_paths": uncovered[:20],
        }
    out = ledger.update_status(uid, "verified", note="verified_by_required_evidence")
    ledger.append_event(
        uid,
        event_type="ledger_status_changed",
        payload={
            "uid": uid,
            "status": "verified",
            "reason": "required_evidence_satisfied",
            "utc": _utc_iso(),
        },
    )
    return {"ok": True, "uid": uid, "status": "verified", "result": out}


def workflow_next_status(current_status: str) -> str:
    """Return next canonical workflow status or empty string when terminal/unknown."""
    st = str(current_status or "").strip().lower()
    return str(_WORKFLOW_NEXT.get(st) or "")


def workflow_status(*, uid: str) -> Dict[str, Any]:
    """Return workflow state snapshot for a ledger item."""
    ledger = ChangeLedger(repo_root=_repo_root())
    item = ledger.read_item(uid)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    cur = str(meta.get("status") or "").strip().lower()
    nxt = workflow_next_status(cur)
    return {
        "ok": True,
        "uid": uid,
        "current_status": cur,
        "next_status": nxt or None,
        "terminal": (nxt == ""),
    }


def advance_status(*, uid: str, note: str = "", requested_next: str = "") -> Dict[str, Any]:
    """Advance workflow status one step (or to requested_next if provided)."""
    ledger = ChangeLedger(repo_root=_repo_root())
    item = ledger.read_item(uid)
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    current = str(meta.get("status") or "").strip().lower()

    expected_next = workflow_next_status(current)
    target = str(requested_next or "").strip().lower()
    if not expected_next:
        return {
            "ok": False,
            "uid": uid,
            "blocked": True,
            "reason": "no_workflow_next_status",
            "current_status": current,
        }
    if target and target != expected_next:
        return {
            "ok": False,
            "uid": uid,
            "blocked": True,
            "reason": "invalid_workflow_transition",
            "current_status": current,
            "expected_next_status": expected_next,
            "requested_next_status": target,
        }
    if not target:
        target = expected_next

    if target == "verified":
        mv = mark_verified(uid=uid)
        if not bool(mv.get("ok")):
            return {
                "ok": False,
                "uid": uid,
                "blocked": True,
                "reason": str(mv.get("reason") or "required_evidence_missing"),
                "missing": mv.get("missing") if isinstance(mv.get("missing"), list) else [],
                "current_status": current,
                "next_status": target,
            }
        return {
            "ok": True,
            "uid": uid,
            "current_status": current,
            "next_status": target,
            "status": "verified",
            "result": mv.get("result"),
        }

    out = ledger.update_status(uid, target, note=(str(note or "").strip() or f"workflow_advance:{current}->{target}"))
    ledger.append_event(
        uid,
        event_type="ledger_status_changed",
        payload={
            "uid": uid,
            "from_status": current,
            "status": target,
            "reason": "workflow_advance",
            "note": str(note or ""),
            "utc": _utc_iso(),
        },
    )
    return {
        "ok": True,
        "uid": uid,
        "current_status": current,
        "next_status": target,
        "status": str(out.get("status") or target),
        "result": out,
    }
