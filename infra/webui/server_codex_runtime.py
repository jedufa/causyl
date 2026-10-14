"""infra.webui.server_codex_runtime

Codex worker runtime scaffolding for the plane.

Iteration-1 intent:
- Define lifecycle/state APIs and persistence contracts.
- Keep this module unwired from HTTP routes.
- Preserve existing /agents behavior until iteration-2 wiring.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import queue
import re
import shlex
import shutil
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

try:
    import fcntl as _fcntl  # POSIX advisory locking for multi-process safety.
except Exception:  # pragma: no cover - non-POSIX fallback
    _fcntl = None  # type: ignore[assignment]


ROLE_SET = {"builder", "assurer", "generalist"}
TASK_ID_RE = re.compile(r"^[a-z0-9]{3}$")
UID_RE = re.compile(r"^PIS-[A-Za-z0-9._:-]+$")

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.Lock] = {}


class CodexRuntimeError(RuntimeError):
    """Raised when codex runtime operations are invalid."""


@dataclass(frozen=True)
class WorkerKey:
    """Stable runtime identity for one role-bound worker."""

    binding_type: str
    binding_id: str
    role: str

    def as_id(self) -> str:
        return f"{_safe_name(self.binding_type)}::{_safe_name(self.binding_id)}::{_safe_name(self.role)}"


@dataclass
class WorkerState:
    """In-memory + persisted worker status snapshot."""

    worker_id: str
    binding_type: str
    binding_id: str
    role: str
    status: str
    created_utc: str
    updated_utc: str
    pid: int = 0
    command: List[str] = field(default_factory=list)
    cwd: str = ""
    last_activity_utc: str = ""
    last_error: str = ""
    bootstrap_hash: str = ""
    mode: str = "codex"

    def to_row(self, *, event: str) -> Dict[str, Any]:
        return {
            "schema_version": "agents_codex_worker_event_v1",
            "event": str(event),
            "utc": _utc_iso(),
            "worker": {
                "worker_id": self.worker_id,
                "binding_type": self.binding_type,
                "binding_id": self.binding_id,
                "role": self.role,
                "status": self.status,
                "created_utc": self.created_utc,
                "updated_utc": self.updated_utc,
                "pid": int(self.pid),
                "command": list(self.command),
                "cwd": self.cwd,
                "last_activity_utc": self.last_activity_utc,
                "last_error": self.last_error,
                "bootstrap_hash": self.bootstrap_hash,
                "mode": self.mode,
            },
        }


@dataclass
class WorkerSession:
    """Live process holder for one codex worker."""

    key: WorkerKey
    state: WorkerState
    proc: Optional[subprocess.Popen[str]] = None
    lock: threading.Lock = field(default_factory=threading.Lock)
    bootstrap_text: str = ""
    stop_requested: threading.Event = field(default_factory=threading.Event)
    stop_reason: str = ""


def _utc_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(s or "").strip()).strip("-") or "x"


def _looks_like_codex_cli(cmd: List[str]) -> bool:
    if not cmd:
        return False
    base = os.path.basename(str(cmd[0] or "")).strip().lower()
    return base == "codex" or base.endswith("/codex")


def _worker_codex_home(repo_root: Path, *, worker_id: str) -> Path:
    safe_worker = _safe_name(worker_id)
    return (Path(repo_root).resolve() / "runtime" / "state" / "codex_oneshot_workers" / safe_worker / "codex_home").resolve()


def _shared_codex_home() -> Path:
    env_home = str(os.environ.get("EIEL_CODEX_SHARED_HOME") or "").strip()
    if env_home:
        return Path(env_home).expanduser().resolve()
    return (Path.home() / ".codex").resolve()


def _ensure_codex_home_seeded(*, target_home: Path, shared_home: Path) -> None:
    target_home.mkdir(parents=True, exist_ok=True)
    if not shared_home.exists():
        return
    shared_files = (
        ".personality_migration",
        "auth.json",
        "config.toml",
        "models_cache.json",
        "version.json",
    )
    shared_dirs = (
        "memories",
        "rules",
        "skills",
    )
    for name in shared_files:
        src = (shared_home / name).resolve()
        dst = (target_home / name).resolve()
        if not src.exists():
            continue
        if dst.is_symlink():
            try:
                if dst.resolve() == src:
                    continue
            except Exception:
                pass
            dst.unlink()
        elif dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            dst.symlink_to(src)
        except Exception:
            if src.is_file():
                shutil.copy2(src, dst)
    for name in shared_dirs:
        src = (shared_home / name).resolve()
        dst = (target_home / name).resolve()
        if not src.exists():
            continue
        if dst.is_symlink():
            try:
                if dst.resolve() == src:
                    continue
            except Exception:
                pass
            dst.unlink()
        elif dst.exists():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        try:
            dst.symlink_to(src, target_is_directory=True)
        except Exception:
            if src.is_dir():
                shutil.copytree(src, dst, dirs_exist_ok=True)


def _codex_subprocess_env(*, repo_root: Path, worker_id: str) -> Dict[str, str]:
    env = dict(os.environ)
    codex_home = _worker_codex_home(repo_root, worker_id=worker_id)
    _ensure_codex_home_seeded(target_home=codex_home, shared_home=_shared_codex_home())
    env["CODEX_HOME"] = str(codex_home)
    env["EIEL_CODEX_ONESHOT_HOME"] = str(codex_home)
    env["EIEL_CODEX_ONESHOT_WORKER_ID"] = str(worker_id or "").strip()
    return env


def _discover_codex_binary() -> str:
    via_path = str(shutil.which("codex") or "").strip()
    if via_path:
        return via_path
    fallback = Path("/home/user/.nvm/versions/node/v24.14.0/bin/codex")
    if fallback.exists() and fallback.is_file():
        return str(fallback)
    return ""


def _build_default_codex_turn_command(*, repo_root: Optional[Path] = None) -> List[str]:
    codex_bin = _discover_codex_binary()
    if not codex_bin:
        return []
    root = Path(repo_root or _repo_root()).resolve()
    unrestricted = str(os.environ.get("EIEL_CODEX_UNRESTRICTED") or "1").strip() == "1"
    if unrestricted:
        return [
            codex_bin,
            "--dangerously-bypass-approvals-and-sandbox",
            "exec",
            "-C",
            str(root),
            "--skip-git-repo-check",
            "-",
        ]
    return [
        codex_bin,
        "-a",
        "never",
        "-s",
        "workspace-write",
        "exec",
        "-C",
        str(root),
        "--skip-git-repo-check",
        "-",
    ]


def _json_stream_cmd(cmd: List[str]) -> List[str]:
    out = list(cmd)
    if not _looks_like_codex_cli(out) or "--json" in out:
        return out
    try:
        exec_idx = out.index("exec")
    except ValueError:
        out.append("--json")
        return out
    out.insert(exec_idx + 1, "--json")
    return out


def _iter_stream_json_events(line: str) -> Iterator[Dict[str, Any]]:
    raw = str(line or "").strip()
    if not raw:
        return
    parts = [part.strip() for part in raw.split(" | ") if part.strip()]
    if len(parts) <= 1:
        try:
            evt = json.loads(raw)
        except Exception:
            return
        if isinstance(evt, dict):
            yield evt
        return
    yielded = False
    for part in parts:
        try:
            evt = json.loads(part)
        except Exception:
            continue
        if isinstance(evt, dict):
            yielded = True
            yield evt
    if yielded:
        return
    try:
        evt = json.loads(raw)
    except Exception:
        return
    if isinstance(evt, dict):
        yield evt


def _parse_stream_event(raw: Dict[str, Any]) -> Optional[Dict[str, str]]:
    if not isinstance(raw, dict):
        return None
    typ = str(raw.get("type") or "").strip()
    payload = raw.get("payload")
    if typ == "event_msg" and isinstance(payload, dict):
        payload_type = str(payload.get("type") or "").strip()
        if payload_type == "agent_message":
            phase = str(payload.get("phase") or "").strip().lower()
            message = str(payload.get("message") or "")
            if not message.strip():
                return None
            if phase == "commentary":
                return {"kind": "commentary", "text": message}
            if phase in {"final", "final_answer"}:
                return {"kind": "final", "text": message}
        if payload_type == "task_complete":
            last = str(payload.get("last_agent_message") or "")
            if last.strip():
                return {"kind": "final", "text": last}
        return None
    if typ == "item.completed":
        item = raw.get("item")
        if isinstance(item, dict) and str(item.get("type") or "").strip() == "agent_message":
            text = str(item.get("text") or "").strip()
            if text:
                return {"kind": "item_message", "text": text}
    if typ in {"thread.started", "turn.started", "item.started", "turn.completed"}:
        return None
    if typ == "error":
        message = str(raw.get("message") or "")
        if message.strip():
            return {"kind": "error", "text": message}
    return None


def _extract_codex_content_from_output(raw_text: str) -> Dict[str, str]:
    final_text = ""
    last_error = ""
    pending_item_message = ""
    parsed_any = False
    raw_tail: deque[str] = deque(maxlen=12)
    for line in str(raw_text or "").splitlines():
        parsed_line = False
        for evt in _iter_stream_json_events(line):
            parsed_line = True
            parsed_any = True
            parsed = _parse_stream_event(evt)
            if parsed is None:
                continue
            kind = str(parsed.get("kind") or "")
            text = str(parsed.get("text") or "")
            if kind == "final":
                final_text = text
                pending_item_message = ""
            elif kind == "item_message":
                pending_item_message = text
            elif kind == "error":
                last_error = text
        if not parsed_line:
            raw = str(line or "").strip()
            if raw:
                raw_tail.append(raw)
    if not final_text and pending_item_message:
        final_text = pending_item_message
    if final_text:
        return {"content": final_text, "error": ""}
    if last_error:
        return {"content": "", "error": last_error}
    if parsed_any:
        fallback = " | ".join(raw_tail).strip()
        return {"content": fallback, "error": fallback}
    plain = str(raw_text or "").strip()
    return {"content": plain, "error": ("" if plain else "codex_turn_failed")}


def _extract_token_usage_from_output(raw_text: str) -> Dict[str, int]:
    text = str(raw_text or "")
    lines = [str(line or "").strip() for line in text.splitlines()]
    for idx, line in enumerate(lines):
        lowered = line.lower()
        if lowered == "tokens used" and idx + 1 < len(lines):
            raw_value = re.sub(r"[^0-9]", "", lines[idx + 1])
            if raw_value:
                total = int(raw_value)
                return {"input_tokens": 0, "output_tokens": 0, "total_tokens": total}
        match = re.search(r"tokens used[:\s]+([0-9][0-9,._ ]*)", line, flags=re.IGNORECASE)
        if match:
            raw_value = re.sub(r"[^0-9]", "", str(match.group(1) or ""))
            if raw_value:
                total = int(raw_value)
                return {"input_tokens": 0, "output_tokens": 0, "total_tokens": total}
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _compose_oneshot_prompt(*, bootstrap_text: str, payload: Dict[str, Any]) -> str:
    """Build the stdin prompt sent to `codex exec -`."""
    bootstrap = str(bootstrap_text or "").strip()
    body = json.dumps(payload, ensure_ascii=False, indent=2)
    parts: List[str] = []
    if bootstrap:
        parts.append("System instructions:\n" + bootstrap)
    parts.append(
        "Turn payload JSON:\n"
        f"{body}\n\n"
        "Respond to the `message` field while following the system instructions above."
    )
    return "\n\n".join(parts).strip() + "\n"


def codex_turn_command_from_env() -> List[str]:
    """Return the configured codex turn command, if any."""
    raw = str(os.environ.get("EIEL_CODEX_TURN_CMD") or "").strip()
    if raw:
        return shlex.split(raw)
    # Self-heal missing env defaults so scheduled/background turns remain operable.
    fallback = _build_default_codex_turn_command(repo_root=_repo_root())
    if fallback:
        os.environ["EIEL_CODEX_TURN_CMD"] = shlex.join(fallback)
    return fallback


def _apply_cwd_to_codex_command(cmd: List[str], *, cwd: Path) -> List[str]:
    """Return a codex exec command whose effective working tree is `cwd`."""
    out = list(cmd or [])
    if not _looks_like_codex_cli(out):
        return out
    normalized: List[str] = []
    skip_next = False
    for part in out:
        if skip_next:
            skip_next = False
            continue
        token = str(part or "")
        if token in {"-C", "--cd", "--cwd"}:
            skip_next = True
            continue
        if token.startswith("--cd=") or token.startswith("--cwd="):
            continue
        normalized.append(token)
    try:
        exec_idx = normalized.index("exec")
    except ValueError:
        return normalized
    normalized[exec_idx + 1:exec_idx + 1] = ["-C", str(Path(cwd).resolve())]
    return normalized


def _apply_model_to_codex_command(cmd: List[str], *, model: str) -> List[str]:
    """Return a codex command with a deterministic `--model` override."""
    out = list(cmd or [])
    model_name = str(model or "").strip()
    if not model_name or not _looks_like_codex_cli(out):
        return out
    normalized: List[str] = []
    skip_next = False
    for part in out:
        if skip_next:
            skip_next = False
            continue
        token = str(part or "")
        if token in {"-m", "--model"}:
            skip_next = True
            continue
        if token.startswith("--model="):
            continue
        normalized.append(token)
    try:
        exec_idx = normalized.index("exec")
    except ValueError:
        normalized.extend(["--model", model_name])
        return normalized
    normalized[exec_idx + 1:exec_idx + 1] = ["--model", model_name]
    return normalized


def _apply_reasoning_effort_to_codex_command(cmd: List[str], *, reasoning_effort: str) -> List[str]:
    """Return a codex command with a deterministic reasoning-effort override."""
    out = list(cmd or [])
    effort = str(reasoning_effort or "").strip().lower()
    if not effort or not _looks_like_codex_cli(out):
        return out
    normalized: List[str] = []
    idx = 0
    while idx < len(out):
        token = str(out[idx] or "")
        if token in {"-c", "--config"} and idx + 1 < len(out):
            cfg = str(out[idx + 1] or "")
            if cfg.startswith("model_reasoning_effort="):
                idx += 2
                continue
            normalized.extend([token, cfg])
            idx += 2
            continue
        if token.startswith("model_reasoning_effort="):
            idx += 1
            continue
        normalized.append(token)
        idx += 1
    try:
        exec_idx = normalized.index("exec")
    except ValueError:
        normalized.extend(["-c", f'model_reasoning_effort="{effort}"'])
        return normalized
    normalized[exec_idx + 1:exec_idx + 1] = ["-c", f'model_reasoning_effort="{effort}"']
    return normalized


def run_codex_oneshot(
    *,
    worker_id: str,
    binding_type: str,
    binding_id: str,
    role: str,
    message: str,
    bootstrap_text: str,
    cwd: Path,
    timeout_s: int,
    command: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Execute one codex turn without requiring a persisted runtime worker."""
    cmd = list(command or codex_turn_command_from_env())
    if not cmd:
        raise CodexRuntimeError("EIEL_CODEX_TURN_CMD is required for codex backend")
    cmd = _apply_cwd_to_codex_command(cmd, cwd=cwd)
    payload = {
        "schema_version": "agents_codex_turn_v1",
        "utc": _utc_iso(),
        "worker_id": str(worker_id or "").strip(),
        "binding_type": str(binding_type or "").strip(),
        "binding_id": str(binding_id or "").strip(),
        "role": str(role or "").strip(),
        "message": str(message or ""),
    }
    data = _compose_oneshot_prompt(bootstrap_text=str(bootstrap_text or ""), payload=payload)
    child_env = _codex_subprocess_env(repo_root=Path(cwd).resolve(), worker_id=worker_id)
    def _run_once(command_to_run: List[str]) -> tuple[str, str, int]:
        proc = subprocess.Popen(
            command_to_run,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(Path(cwd).resolve()),
            env=child_env,
            start_new_session=True,
        )
        try:
            stdout_text, stderr_text = proc.communicate(input=data, timeout=max(1, int(timeout_s)))
            return str(stdout_text or "").strip(), str(stderr_text or "").strip(), int(proc.returncode or 0)
        except KeyboardInterrupt:
            try:
                os.killpg(proc.pid, signal.SIGINT)
            except Exception:
                try:
                    proc.send_signal(signal.SIGINT)
                except Exception:
                    pass
            try:
                proc.wait(timeout=2)
            except Exception:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except Exception:
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                try:
                    proc.wait(timeout=2)
                except Exception:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    try:
                        proc.wait(timeout=2)
                    except Exception:
                        pass
            raise

    out, err, rc = _run_once(cmd)
    comb = (out + "\n" + err).lower()
    needs_tty = ("stdin is not a terminal" in comb) or ("not-tty" in comb)
    if needs_tty and shutil.which("script"):
        out, err, rc = _run_once(["script", "-qefc", shlex.join(cmd), "/dev/null"])
        out = str(out or "").replace("\r", "").strip()
        err = str(err or "").replace("\r", "").strip()
    extracted = _extract_codex_content_from_output(out if out else err)
    content = str(extracted.get("content") or "").strip()
    parsed_error = str(extracted.get("error") or "").strip()
    usage = _extract_token_usage_from_output(out if out else err)
    return {
        "ok": bool(int(rc) == 0 and bool(content)),
        "exit_code": int(rc),
        "content": content,
        "stderr": parsed_error or err,
        "stdout": out,
        "usage": usage,
    }


def _runtime_root(repo_root: Path) -> Path:
    p = (repo_root / "governance" / "agents" / "runtime").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _events_file(repo_root: Path) -> Path:
    return (_runtime_root(repo_root) / "workers_events.jsonl").resolve()


def _state_file(repo_root: Path) -> Path:
    return (_runtime_root(repo_root) / "workers_state.json").resolve()


def _file_lock(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        lk = _LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _LOCKS[key] = lk
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


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    tmp = (path.parent / f".{path.name}.tmp.{os.getpid()}.{threading.get_ident()}").resolve()
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        if _fcntl is not None:
            _fcntl.flock(f.fileno(), _fcntl.LOCK_EX)
        try:
            f.write(raw)
            f.flush()
            os.fsync(f.fileno())
        finally:
            if _fcntl is not None:
                _fcntl.flock(f.fileno(), _fcntl.LOCK_UN)
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _feature_enabled() -> bool:
    v = str(os.environ.get("EIEL_AGENTS_BACKEND", "llm") or "llm").strip().lower()
    return v == "codex"


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def _iso_to_epoch(iso_utc: str) -> float:
    s = str(iso_utc or "").strip()
    if not s:
        return 0.0
    try:
        return _dt.datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _validate_key(binding: Dict[str, Any], role: str) -> WorkerKey:
    bt = str((binding or {}).get("type") or "").strip()
    bid = str((binding or {}).get("id") or "").strip()
    rr = str(role or "").strip().lower()
    if rr not in ROLE_SET:
        raise CodexRuntimeError("invalid role")
    if bt not in ("task_id", "ledger_uid"):
        raise CodexRuntimeError("binding.type must be task_id|ledger_uid")
    if bt == "task_id" and not TASK_ID_RE.match(bid.lower()):
        raise CodexRuntimeError("task_id must match ^[a-z0-9]{3}$")
    if bt == "ledger_uid" and not UID_RE.match(bid):
        raise CodexRuntimeError("invalid ledger_uid format")
    return WorkerKey(binding_type=bt, binding_id=bid, role=rr)


def _is_pid_alive(pid: int) -> bool:
    try:
        if int(pid) <= 0:
            return False
        os.kill(int(pid), 0)
        return True
    except Exception:
        return False


class CodexRuntimeManager:
    """Lifecycle manager for role-specific codex workers (scaffold only)."""

    def __init__(
        self,
        *,
        repo_root: Optional[Path] = None,
        max_workers: int = 100,
        max_workers_per_role: int = 25,
        idle_ttl_s: int = 20 * 60,
        default_cmd: Optional[List[str]] = None,
    ) -> None:
        self.repo_root = (repo_root or _repo_root()).resolve()
        self.max_workers = max(1, int(max_workers))
        self.max_workers_per_role = max(1, int(max_workers_per_role))
        self.idle_ttl_s = max(60, int(idle_ttl_s))
        self.protocol = str(os.environ.get("EIEL_CODEX_PROTOCOL", "oneshot") or "oneshot").strip().lower()
        if self.protocol != "oneshot":
            raise CodexRuntimeError("unsupported EIEL_CODEX_PROTOCOL; only 'oneshot' is supported")
        self.default_cmd = list(default_cmd or self._cmd_from_env())
        if not self.default_cmd:
            raise CodexRuntimeError("EIEL_CODEX_TURN_CMD is required for codex backend")
        self._guard = threading.Lock()
        self._sessions: Dict[str, WorkerSession] = {}
        self._load_state_snapshot()

    def _cmd_from_env(self) -> List[str]:
        raw = str(os.environ.get("EIEL_CODEX_TURN_CMD") or "").strip()
        return shlex.split(raw) if raw else []

    def _persist(self) -> None:
        rows = []
        for sid, sess in self._sessions.items():
            st = sess.state
            rows.append(
                {
                    "worker_id": sid,
                    "binding_type": st.binding_type,
                    "binding_id": st.binding_id,
                    "role": st.role,
                    "status": st.status,
                    "created_utc": st.created_utc,
                    "updated_utc": st.updated_utc,
                    "pid": int(st.pid),
                    "command": list(st.command),
                    "cwd": st.cwd,
                    "last_activity_utc": st.last_activity_utc,
                    "last_error": st.last_error,
                    "bootstrap_hash": st.bootstrap_hash,
                    "bootstrap_text": str(sess.bootstrap_text or ""),
                    "mode": st.mode,
                }
            )
        _write_json(
            _state_file(self.repo_root),
            {"schema_version": "agents_codex_workers_state_v1", "utc": _utc_iso(), "workers": rows},
        )

    def _load_state_snapshot(self) -> None:
        snap = _read_json(_state_file(self.repo_root))
        rows = snap.get("workers")
        if not isinstance(rows, list):
            return
        now = time.time()
        with self._guard:
            for r in rows:
                if not isinstance(r, dict):
                    continue
                wid = str(r.get("worker_id") or "").strip()
                if not wid:
                    continue
                try:
                    _validate_key(
                        {"type": str(r.get("binding_type") or ""), "id": str(r.get("binding_id") or "")},
                        str(r.get("role") or ""),
                    )
                except Exception:
                    continue
                status = str(r.get("status") or "").strip().lower()
                age_s = now - _iso_to_epoch(str(r.get("updated_utc") or ""))
                if status in {"stale", "stopped", "exited"} and age_s > (14 * 24 * 3600):
                    continue
                st = WorkerState(
                    worker_id=wid,
                    binding_type=str(r.get("binding_type") or ""),
                    binding_id=str(r.get("binding_id") or ""),
                    role=str(r.get("role") or ""),
                    status="stale",
                    created_utc=str(r.get("created_utc") or _utc_iso()),
                    updated_utc=_utc_iso(),
                    pid=0,
                    command=([str(x) for x in r.get("command", []) if str(x or "").strip()] if isinstance(r.get("command"), list) else []),
                    cwd=str(r.get("cwd") or str(self.repo_root)),
                    last_activity_utc=str(r.get("last_activity_utc") or ""),
                    last_error="requires restart",
                    bootstrap_hash=str(r.get("bootstrap_hash") or ""),
                    mode="codex",
                )
                key = WorkerKey(binding_type=st.binding_type, binding_id=st.binding_id, role=st.role)
                self._sessions[wid] = WorkerSession(
                    key=key,
                    state=st,
                    proc=None,
                    bootstrap_text=str(r.get("bootstrap_text") or ""),
                )
        self._persist()

    def _counts_toward_capacity(self, sess: WorkerSession) -> bool:
        status = str(sess.state.status or "").strip().lower()
        return status not in {"stale", "stopped", "exited", "error"}

    def _active_worker_count_locked(self) -> int:
        return sum(1 for sess in self._sessions.values() if self._counts_toward_capacity(sess))

    def _active_worker_count_for_role_locked(self, role: str) -> int:
        role_norm = str(role or "").strip().lower()
        return sum(
            1
            for sess in self._sessions.values()
            if self._counts_toward_capacity(sess) and str(sess.state.role or "").strip().lower() == role_norm
        )

    def _require_enabled(self) -> None:
        if not _feature_enabled():
            raise CodexRuntimeError("codex backend disabled (set EIEL_AGENTS_BACKEND=codex)")

    def reconfigure(
        self,
        *,
        max_workers: Optional[int] = None,
        max_workers_per_role: Optional[int] = None,
        default_cmd: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Update live runtime limits/command without recreating the manager."""
        changed = False
        with self._guard:
            if max_workers is not None:
                value = max(1, int(max_workers))
                if value != self.max_workers:
                    self.max_workers = value
                    changed = True
            if max_workers_per_role is not None:
                value = max(1, int(max_workers_per_role))
                if value != self.max_workers_per_role:
                    self.max_workers_per_role = value
                    changed = True
            if default_cmd is not None:
                cmd = [str(part) for part in list(default_cmd) if str(part or "").strip()]
                if cmd and cmd != self.default_cmd:
                    self.default_cmd = cmd
                    changed = True
        return {
            "ok": True,
            "changed": changed,
            "max_workers": int(self.max_workers),
            "max_workers_per_role": int(self.max_workers_per_role),
            "default_cmd": list(self.default_cmd),
        }

    def _record_event(self, state: WorkerState, *, event: str) -> None:
        _append_jsonl(_events_file(self.repo_root), state.to_row(event=event))

    def list_workers(self) -> Dict[str, Any]:
        with self._guard:
            rows = []
            for sess in self._sessions.values():
                st = sess.state
                rows.append(
                    {
                        "worker_id": st.worker_id,
                        "binding_type": st.binding_type,
                        "binding_id": st.binding_id,
                        "role": st.role,
                        "status": st.status,
                        "pid": int(st.pid),
                        "updated_utc": st.updated_utc,
                        "last_activity_utc": st.last_activity_utc,
                        "last_error": st.last_error,
                    }
                )
        return {
            "ok": True,
            "backend": "codex" if _feature_enabled() else "llm",
            "workers": rows,
        }

    def get_worker(self, *, binding: Dict[str, Any], role: str) -> Dict[str, Any]:
        key = _validate_key(binding, role)
        wid = key.as_id()
        with self._guard:
            sess = self._sessions.get(wid)
            if not sess:
                return {"ok": True, "exists": False, "worker_id": wid}
            st = sess.state
            return {
                "ok": True,
                "exists": True,
                "worker_id": wid,
                "status": st.status,
                "pid": int(st.pid),
                "last_error": st.last_error,
                "updated_utc": st.updated_utc,
            }

    def ensure_worker(
        self,
        *,
        binding: Dict[str, Any],
        role: str,
        bootstrap_text: str,
        command: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        self._require_enabled()
        key = _validate_key(binding, role)
        wid = key.as_id()
        now = _utc_iso()
        cmd = list(command or self.default_cmd)
        if not cmd:
            raise CodexRuntimeError("empty codex command")
        with self._guard:
            existing = self._sessions.get(wid)
            if existing:
                if self.protocol == "oneshot":
                    desired_cmd = list(cmd)
                    old_cmd = list(existing.state.command or [])
                    desired_bootstrap_text = str(bootstrap_text or "")
                    desired_bootstrap_hash = _sha256_text(bootstrap_text)
                    existing.stop_requested.clear()
                    existing.stop_reason = ""
                    if desired_cmd and old_cmd != desired_cmd:
                        existing.state.command = desired_cmd
                        self._record_event(existing.state, event="reconfigured")
                    if desired_bootstrap_text and not str(existing.bootstrap_text or "").strip():
                        existing.bootstrap_text = desired_bootstrap_text
                    if desired_bootstrap_hash != str(existing.state.bootstrap_hash or ""):
                        existing.bootstrap_text = desired_bootstrap_text
                        existing.state.bootstrap_hash = desired_bootstrap_hash
                        self._record_event(existing.state, event="bootstrap_updated")
                    existing.state.updated_utc = now
                    existing.state.last_activity_utc = now
                    if existing.state.status in {"stale", "stopped", "exited", "error"}:
                        existing.state.status = "ready"
                        existing.state.last_error = ""
                    self._record_event(existing.state, event="reused")
                    self._persist()
                    return {"ok": True, "started": False, "worker_id": wid, "status": existing.state.status}
                if existing.proc is not None and existing.proc.poll() is None:
                    existing.state.updated_utc = now
                    existing.state.last_activity_utc = now
                    self._persist()
                    return {"ok": True, "started": False, "worker_id": wid, "status": existing.state.status}

            active_total = self._active_worker_count_locked()
            active_for_role = self._active_worker_count_for_role_locked(role)
            if active_total >= self.max_workers and wid not in self._sessions:
                raise CodexRuntimeError("worker limit reached")
            if active_for_role >= self.max_workers_per_role and wid not in self._sessions:
                raise CodexRuntimeError("worker limit reached")

            proc: Optional[subprocess.Popen[str]] = None
            status = "ready"
            pid = 0
            # Iteration-2 default: oneshot mode keeps worker state persistent while
            # executing codex turn command per message. Interactive mode remains opt-in.
            if self.protocol == "interactive":
                proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.repo_root),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    start_new_session=True,
                )
                status = "running"
                pid = int(proc.pid or 0)
            st = WorkerState(
                worker_id=wid,
                binding_type=key.binding_type,
                binding_id=key.binding_id,
                role=key.role,
                status=status,
                created_utc=now,
                updated_utc=now,
                pid=pid,
                command=cmd,
                cwd=str(self.repo_root),
                last_activity_utc=now,
                bootstrap_hash=_sha256_text(bootstrap_text),
                mode="codex",
            )
            sess = WorkerSession(key=key, state=st, proc=proc, bootstrap_text=str(bootstrap_text or ""))
            self._sessions[wid] = sess
            self._record_event(st, event="started")
            self._record_event(st, event="bootstrap_pending")
            if self.protocol == "oneshot":
                self._record_event(st, event="ready")
            self._persist()
        return {"ok": True, "started": True, "worker_id": wid, "pid": int(pid), "protocol": self.protocol}

    def _run_oneshot_turn(
        self,
        *,
        sess: WorkerSession,
        message: str,
        timeout_s: int,
        model: str = "",
        reasoning_effort: str = "",
    ) -> Dict[str, Any]:
        payload = {
            "schema_version": "agents_codex_turn_v1",
            "utc": _utc_iso(),
            "worker_id": sess.state.worker_id,
            "binding_type": sess.state.binding_type,
            "binding_id": sess.state.binding_id,
            "role": sess.state.role,
            "message": str(message or ""),
        }
        data = _compose_oneshot_prompt(bootstrap_text=sess.bootstrap_text, payload=payload)
        cmd = list(sess.state.command or self.default_cmd)
        if not cmd:
            raise CodexRuntimeError("empty codex turn command")
        cmd = _apply_model_to_codex_command(cmd, model=model)
        cmd = _apply_reasoning_effort_to_codex_command(cmd, reasoning_effort=reasoning_effort)
        cmd = _json_stream_cmd(cmd)
        cp = subprocess.run(
            cmd,
            input=data,
            capture_output=True,
            text=True,
            cwd=str(self.repo_root),
            env=_codex_subprocess_env(repo_root=self.repo_root, worker_id=sess.state.worker_id),
            timeout=max(1, int(timeout_s)),
            check=False,
        )
        out = str(cp.stdout or "").strip()
        err = str(cp.stderr or "").strip()
        rc = int(cp.returncode)

        comb = (out + "\n" + err).lower()
        needs_tty = ("stdin is not a terminal" in comb) or ("not-tty" in comb)
        if needs_tty and shutil.which("script"):
            cp2 = subprocess.run(
                ["script", "-qefc", shlex.join(cmd), "/dev/null"],
                input=data,
                capture_output=True,
                text=True,
                cwd=str(self.repo_root),
                env=_codex_subprocess_env(repo_root=self.repo_root, worker_id=sess.state.worker_id),
                timeout=max(1, int(timeout_s)),
                check=False,
            )
            out = str(cp2.stdout or "").replace("\r", "").strip()
            err = str(cp2.stderr or "").replace("\r", "").strip()
            rc = int(cp2.returncode)

        extracted = _extract_codex_content_from_output(out if out else err)
        content = str(extracted.get("content") or "").strip()
        parsed_error = str(extracted.get("error") or "").strip()
        return {
            "ok": bool(int(rc) == 0 and bool(content)),
            "exit_code": int(rc),
            "content": content,
            "stderr": parsed_error or err,
            "stdout": out,
        }

    def _run_oneshot_turn_stream(
        self,
        *,
        sess: WorkerSession,
        message: str,
        timeout_s: int,
        model: str = "",
        reasoning_effort: str = "",
    ) -> Iterator[Dict[str, Any]]:
        payload = {
            "schema_version": "agents_codex_turn_v1",
            "utc": _utc_iso(),
            "worker_id": sess.state.worker_id,
            "binding_type": sess.state.binding_type,
            "binding_id": sess.state.binding_id,
            "role": sess.state.role,
            "message": str(message or ""),
        }
        data = _compose_oneshot_prompt(bootstrap_text=sess.bootstrap_text, payload=payload)
        base_cmd = list(sess.state.command or self.default_cmd)
        if not base_cmd:
            raise CodexRuntimeError("empty codex turn command")
        base_cmd = _apply_model_to_codex_command(base_cmd, model=model)
        base_cmd = _apply_reasoning_effort_to_codex_command(base_cmd, reasoning_effort=reasoning_effort)
        cmd = _json_stream_cmd(base_cmd)
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            cwd=str(self.repo_root),
            env=_codex_subprocess_env(repo_root=self.repo_root, worker_id=sess.state.worker_id),
            bufsize=1,
            start_new_session=True,
        )
        with self._guard:
            current = self._sessions.get(sess.state.worker_id)
            if current is sess:
                sess.proc = proc
                sess.state.pid = int(proc.pid or 0)
                sess.state.status = "running"
                sess.state.updated_utc = _utc_iso()
                sess.state.last_activity_utc = sess.state.updated_utc
                self._persist()
        assert proc.stdin is not None
        assert proc.stdout is not None
        proc.stdin.write(data)
        proc.stdin.close()

        deadline = time.monotonic() + max(1, int(timeout_s))
        final_text = ""
        last_error = ""
        pending_modern_agent_message = ""
        raw_tail: "deque[str]" = deque(maxlen=12)
        q: "queue.Queue[Optional[str]]" = queue.Queue()

        def _reader() -> None:
            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    q.put(line)
            finally:
                q.put(None)

        t = threading.Thread(target=_reader, name="codex_runtime_stream_reader", daemon=True)
        t.start()
        try:
            while True:
                if sess.stop_requested.is_set():
                    stop_reason = str(sess.stop_reason or "codex turn stopped").strip() or "codex turn stopped"
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    yield {
                        "type": "done",
                        "ok": False,
                        "error": stop_reason,
                        "worker_id": sess.state.worker_id,
                        "exit_code": 143,
                    }
                    return
                if time.monotonic() > deadline:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    yield {
                        "type": "done",
                        "ok": False,
                        "error": f"codex turn timed out after {int(timeout_s)}s",
                        "worker_id": sess.state.worker_id,
                        "exit_code": 124,
                    }
                    return
                try:
                    line = q.get(timeout=0.2)
                except queue.Empty:
                    line = ""
                if line is None:
                    break
                if not line:
                    if proc.poll() is not None:
                        break
                    continue
                parsed_any = False
                for evt in _iter_stream_json_events(line):
                    evt_type = str(evt.get("type") or "").strip()
                    if evt_type == "item.completed":
                        item = evt.get("item")
                        if isinstance(item, dict) and str(item.get("type") or "").strip() == "agent_message":
                            text = str(item.get("text") or "").strip()
                            if text:
                                if pending_modern_agent_message:
                                    yield {
                                        "type": "commentary",
                                        "text": pending_modern_agent_message,
                                        "worker_id": sess.state.worker_id,
                                    }
                                pending_modern_agent_message = text
                                parsed_any = True
                                continue
                    if evt_type == "turn.completed":
                        if pending_modern_agent_message:
                            text = pending_modern_agent_message
                            pending_modern_agent_message = ""
                            if text != final_text or not text.strip():
                                delta = text
                                if final_text and text.startswith(final_text):
                                    delta = text[len(final_text):]
                                final_text = text
                                yield {"type": "delta", "delta": delta, "content": text, "worker_id": sess.state.worker_id}
                            parsed_any = True
                            continue
                    parsed = _parse_stream_event(evt)
                    if parsed is None:
                        continue
                    parsed_any = True
                    kind = str(parsed.get("kind") or "")
                    text = str(parsed.get("text") or "")
                    if kind == "commentary":
                        yield {"type": "commentary", "text": text, "worker_id": sess.state.worker_id}
                    elif kind == "final":
                        if text == final_text and text.strip():
                            continue
                        delta = text
                        if final_text and text.startswith(final_text):
                            delta = text[len(final_text):]
                        final_text = text
                        yield {"type": "delta", "delta": delta, "content": text, "worker_id": sess.state.worker_id}
                    elif kind == "error":
                        last_error = text
                if parsed_any:
                    continue
                raw = str(line or "").strip()
                if raw:
                    raw_tail.append(raw)

            rc = int(proc.wait(timeout=1))
            if pending_modern_agent_message and not final_text.strip():
                final_text = pending_modern_agent_message
            ok = bool(final_text.strip())
            if (not ok) and (not last_error.strip()) and raw_tail:
                last_error = " | ".join(raw_tail)
            yield {
                "type": "done",
                "ok": ok,
                "content": final_text,
                "error": ("" if ok else (last_error or "codex_turn_failed")),
                "worker_id": sess.state.worker_id,
                "exit_code": rc,
            }
        finally:
            with self._guard:
                current = self._sessions.get(sess.state.worker_id)
                if current is sess and sess.proc is proc:
                    sess.proc = None
                    sess.state.pid = 0
                    sess.state.updated_utc = _utc_iso()
                    sess.state.last_activity_utc = sess.state.updated_utc
                    if sess.state.status == "running":
                        sess.state.status = "ready"
                    self._persist()
                sess.stop_requested.clear()
                sess.stop_reason = ""

    def send_turn(
        self,
        *,
        binding: Dict[str, Any],
        role: str,
        message: str,
        timeout_s: int = 180,
        model: str = "",
        reasoning_effort: str = "",
    ) -> Dict[str, Any]:
        """Iteration-1 scaffold: shape contract for iteration-2 route wiring."""
        self._require_enabled()
        key = _validate_key(binding, role)
        wid = key.as_id()
        msg = str(message or "").strip()
        if not msg:
            raise CodexRuntimeError("message is required")
        with self._guard:
            sess = self._sessions.get(wid)
            if not sess:
                raise CodexRuntimeError("worker not started")
            st = sess.state
            st.updated_utc = _utc_iso()
            st.last_activity_utc = st.updated_utc
        if self.protocol == "oneshot":
            with sess.lock:
                try:
                    tr = self._run_oneshot_turn(
                        sess=sess,
                        message=msg,
                        timeout_s=timeout_s,
                        model=model,
                        reasoning_effort=reasoning_effort,
                    )
                except Exception as e:
                    tr = {
                        "ok": False,
                        "exit_code": 1,
                        "content": "",
                        "stderr": str(e),
                        "stdout": "",
                        "error": "codex_turn_exec_error",
                    }
            with self._guard:
                st = self._sessions.get(wid).state if self._sessions.get(wid) else None
                if st is not None:
                    st.updated_utc = _utc_iso()
                    st.last_activity_utc = st.updated_utc
                    st.status = "ready" if bool(tr.get("ok")) else "error"
                    st.last_error = "" if bool(tr.get("ok")) else str(tr.get("stderr") or tr.get("content") or "codex_turn_failed")
                    self._persist()
                    self._record_event(st, event=("turn_ok" if bool(tr.get("ok")) else "turn_error"))
            return {
                "ok": bool(tr.get("ok")),
                "worker_id": wid,
                "status": ("ok" if bool(tr.get("ok")) else "error"),
                "content": str(tr.get("content") or ""),
                "error": ("" if bool(tr.get("ok")) else str(tr.get("stderr") or tr.get("error") or "codex_turn_failed")),
                "exit_code": int(tr.get("exit_code") or 1),
                "timeout_s": int(timeout_s),
                "model": str(model or ""),
                "reasoning_effort": str(reasoning_effort or ""),
                "protocol": self.protocol,
            }

        # Interactive transport is intentionally not wired yet.
        with self._guard:
            self._persist()
        return {
            "ok": False,
            "worker_id": wid,
            "status": "unwired",
            "error": "codex_turn_transport_unwired",
            "timeout_s": int(timeout_s),
            "protocol": self.protocol,
        }

    def stream_turn(
        self,
        *,
        binding: Dict[str, Any],
        role: str,
        message: str,
        timeout_s: int = 180,
        model: str = "",
        reasoning_effort: str = "",
    ) -> Iterator[Dict[str, Any]]:
        """Stream wrapper over turn execution (oneshot protocol)."""
        self._require_enabled()
        key = _validate_key(binding, role)
        wid = key.as_id()
        yield {"type": "start", "worker_id": wid}
        with self._guard:
            sess = self._sessions.get(wid)
            if not sess:
                raise CodexRuntimeError("worker not started")
            sess.state.updated_utc = _utc_iso()
            sess.state.last_activity_utc = sess.state.updated_utc
        with sess.lock:
            try:
                final_evt: Dict[str, Any] = {}
                for evt in self._run_oneshot_turn_stream(
                    sess=sess,
                    message=message,
                    timeout_s=timeout_s,
                    model=model,
                    reasoning_effort=reasoning_effort,
                ):
                    if str(evt.get("type") or "") == "done":
                        final_evt = evt
                    yield evt
            finally:
                pass
        with self._guard:
            st = self._sessions.get(wid).state if self._sessions.get(wid) else None
            if st is not None:
                st.updated_utc = _utc_iso()
                st.last_activity_utc = st.updated_utc
                ok = bool(final_evt.get("ok"))
                if st.status != "stopped":
                    st.status = "ready" if ok else "error"
                st.last_error = "" if ok else str(final_evt.get("error") or st.last_error or "codex_turn_failed")
                st.pid = 0
                self._persist()
                self._record_event(st, event=("turn_ok" if ok else "turn_error"))

    def stop_worker(self, *, binding: Dict[str, Any], role: str, reason: str = "stop") -> Dict[str, Any]:
        key = _validate_key(binding, role)
        wid = key.as_id()
        proc: Optional[subprocess.Popen[str]] = None
        with self._guard:
            sess = self._sessions.get(wid)
            if not sess:
                return {"ok": True, "stopped": False, "worker_id": wid, "reason": "not_found"}
            sess.stop_reason = str(reason or "stop")
            sess.stop_requested.set()
            proc = sess.proc
        if proc is not None and proc.poll() is None:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except Exception:
                try:
                    proc.terminate()
                except Exception:
                    pass
            time.sleep(0.2)
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
        with self._guard:
            sess = self._sessions.get(wid)
            if not sess:
                return {"ok": True, "stopped": False, "worker_id": wid, "reason": "not_found"}
            sess.state.status = "stopped"
            sess.state.updated_utc = _utc_iso()
            sess.state.last_error = str(reason or "stop")
            sess.state.pid = 0
            sess.proc = None
            self._record_event(sess.state, event="stopped")
            self._persist()
        return {"ok": True, "stopped": True, "worker_id": wid}

    def stop_all(self, *, reason: str = "shutdown") -> Dict[str, Any]:
        with self._guard:
            keys = list(self._sessions.keys())
        stopped = 0
        for wid in keys:
            with self._guard:
                sess = self._sessions.get(wid)
                if not sess:
                    continue
                binding = {"type": sess.key.binding_type, "id": sess.key.binding_id}
                role = sess.key.role
            rr = self.stop_worker(binding=binding, role=role, reason=reason)
            if bool(rr.get("stopped")):
                stopped += 1
        return {"ok": True, "stopped": stopped, "total": len(keys)}

    def reap_idle(self) -> Dict[str, Any]:
        now = time.time()
        ids: List[str] = []
        with self._guard:
            for wid, sess in self._sessions.items():
                ts = str(sess.state.last_activity_utc or sess.state.updated_utc or sess.state.created_utc)
                try:
                    age = now - _dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                except Exception:
                    age = 0.0
                if age > float(self.idle_ttl_s):
                    ids.append(wid)
        reaped = 0
        for wid in ids:
            with self._guard:
                sess = self._sessions.get(wid)
                if not sess:
                    continue
                binding = {"type": sess.key.binding_type, "id": sess.key.binding_id}
                role = sess.key.role
            rr = self.stop_worker(binding=binding, role=role, reason="idle_ttl")
            if bool(rr.get("stopped")):
                reaped += 1
        return {"ok": True, "reaped": reaped, "considered": len(ids)}

    def reconcile(self) -> Dict[str, Any]:
        """Best-effort process reconciliation for persisted sessions."""
        updated = 0
        with self._guard:
            for sess in self._sessions.values():
                if sess.proc is not None:
                    if sess.proc.poll() is None:
                        continue
                    sess.state.status = "exited"
                    sess.state.pid = 0
                    sess.state.updated_utc = _utc_iso()
                    updated += 1
                elif sess.state.pid and not _is_pid_alive(sess.state.pid):
                    sess.state.status = "stale"
                    sess.state.pid = 0
                    sess.state.updated_utc = _utc_iso()
                    updated += 1
        if updated:
            self._persist()
        return {"ok": True, "updated": updated}
