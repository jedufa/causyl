"""infra.webui.server_agents_swarm

Sibling runtime for lead/maker/finisher delivery swarms.

This intentionally does not reuse the existing builder/verifier workflow state
machine. It stores its own persisted state under governance/agents/runtime/swarm
and uses isolated git worktrees for each worker lane.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.root_tools import load_tool_module as _load_root_tool_module
from infra.webui import server_agents as _agents
from infra.webui.server_codex_runtime import run_codex_oneshot

_runtime_policy = _load_root_tool_module("runtime_policy", "tools/runtime_policy.py")


SCHEMA_STATE = "agents_swarm_state_v1"
SCHEMA_EVENT = "agents_swarm_event_v1"
TASK_ID_RE = re.compile(r"^[a-z0-9]{3}$")
UID_RE = re.compile(r"^PIS-[A-Za-z0-9._:-]+$")

DEFAULT_WALL_CLOCK_S = _runtime_policy.get_runtime_policy_int(
    "agents.swarm.wall_clock_s",
    60 * 60,
    minimum=5 * 60,
    maximum=2 * 60 * 60,
)
DEFAULT_MAKER_LEASE_S = 5 * 60
DEFAULT_FINISHER_LEASE_S = 3 * 60
DEFAULT_MAX_MAKERS = 4
DEFAULT_MAX_FINISHERS = 2
DEFAULT_MAX_TOTAL_TOKENS = 0

MIN_WALL_CLOCK_S = 5 * 60
MAX_WALL_CLOCK_S = 2 * 60 * 60
MIN_MAKER_LEASE_S = 60
MAX_MAKER_LEASE_S = 30 * 60
MIN_FINISHER_LEASE_S = 60
MAX_FINISHER_LEASE_S = 30 * 60
MIN_MAX_MAKERS = 1
MAX_MAX_MAKERS = 6
MIN_MAX_FINISHERS = 1
MAX_MAX_FINISHERS = 4
MIN_MAX_TOTAL_TOKENS = 0
MAX_MAX_TOTAL_TOKENS = 50_000_000

TERMINAL_SWARM_STATUSES = {
    "finished",
    "partial",
    "failed",
    "cancelled",
    "timed_out",
    "interrupted",
}

OVERLAY_DENY_PREFIXES = (
    ".git/",
    ".gradle/",
    "runtime/",
    "governance/agents/runtime/",
    "governance/agents/sessions/",
    "infra/mobile/android/.gradle/",
    "infra/mobile/android/app/build/",
    "infra/mobile/android/build/",
)
OVERLAY_ALLOW_ROOTS = {
    ".tooling",
    "config",
    "docs",
    "governance",
    "infra",
    "mobile",
    "tests",
    "tools",
}
OVERLAY_ALLOW_FILES = {
    "AGENTS.md",
    "README.md",
    "pyproject.toml",
    "pytest.ini",
    "requirements.txt",
}
OVERLAY_ALLOW_EXTENSIONS = {
    ".gradle",
    ".java",
    ".json",
    ".jsonl",
    ".kt",
    ".kts",
    ".md",
    ".properties",
    ".py",
    ".sh",
    ".toml",
    ".txt",
    ".xml",
    ".yaml",
    ".yml",
}

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}
_SWARM_THREADS_GUARD = threading.Lock()
_SWARM_THREADS: Dict[str, threading.Thread] = {}


class AgentSwarmError(RuntimeError):
    """Raised for invalid swarm operations."""


class SwarmRuntimeStop(RuntimeError):
    """Raised when a swarm should stop without being treated as an error."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_root() -> Path:
    p = (_repo_root() / "governance" / "agents" / "runtime" / "swarm").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _worktrees_root() -> Path:
    p = (_repo_root() / "runtime" / "state" / "swarm_worktrees").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _utc_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _parse_utc(value: str) -> Optional[_dt.datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _safe_name(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(value or "").strip()).strip("-") or "x"


def _named_lock(key: str) -> threading.RLock:
    lock_key = str(key or "").strip()
    if not lock_key:
        raise ValueError("lock key is required")
    with _LOCKS_GUARD:
        lk = _LOCKS.get(lock_key)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[lock_key] = lk
        return lk


def _file_lock(path: Path) -> threading.RLock:
    return _named_lock(f"file::{path.resolve()}")


def _swarm_lock(swarm_id: str) -> threading.RLock:
    return _named_lock(f"swarm::{str(swarm_id or '').strip()}")


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, indent=2, ensure_ascii=False) + "\n"
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    with _file_lock(path):
        tmp.write_text(raw, encoding="utf-8")
        os.replace(tmp, path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with _file_lock(path):
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())


def _read_jsonl(path: Path, *, limit: int = 200) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    if len(lines) > int(limit):
        lines = lines[-int(limit):]
    out: List[Dict[str, Any]] = []
    for raw in lines:
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            row = json.loads(s)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _validate_binding(binding: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(binding, dict):
        raise AgentSwarmError("binding must be an object")
    bt = str(binding.get("type") or "").strip()
    bid = str(binding.get("id") or "").strip()
    if bt not in {"task_id", "ledger_uid"}:
        raise AgentSwarmError("binding.type must be task_id|ledger_uid")
    if not bid:
        raise AgentSwarmError("binding.id is required")
    if bt == "task_id" and not TASK_ID_RE.match(bid.lower()):
        raise AgentSwarmError("task_id must match ^[a-z0-9]{3}$")
    if bt == "ledger_uid" and not UID_RE.match(bid):
        raise AgentSwarmError("invalid ledger_uid format")
    return {"type": bt, "id": bid}


def _state_file(swarm_id: str) -> Path:
    return (_runtime_root() / f"swarm__{swarm_id}.json").resolve()


def _events_file(swarm_id: str) -> Path:
    return (_runtime_root() / f"swarm__{swarm_id}.events.jsonl").resolve()


def _artifacts_root(swarm_id: str) -> Path:
    p = (_runtime_root() / f"swarm__{swarm_id}.artifacts").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _workers_root(swarm_id: str) -> Path:
    p = (_artifacts_root(swarm_id) / "workers").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _worker_state_file(swarm_id: str, worker_id: str) -> Path:
    return (_workers_root(swarm_id) / f"{_safe_name(worker_id)}.json").resolve()


def _prompt_path(name: str) -> Path:
    return (_repo_root() / "governance" / "agents" / "prompts" / name).resolve()


def _load_prompt_text(name: str) -> str:
    rendered = _agents.render_prompt_file(_prompt_path(name))
    return str(rendered.get("text") or "").strip()


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")


def _json_fallback(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        return {}
    stripped = raw
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3 and lines[0].startswith("```") and lines[-1].strip() == "```":
            stripped = "\n".join(lines[1:-1]).strip()
    try:
        obj, _ = json.JSONDecoder().raw_decode(stripped)
    except Exception:
        decoder = json.JSONDecoder()
        best_obj: Dict[str, Any] = {}
        best_score = -1
        for match in re.finditer(r"\{", stripped):
            try:
                candidate, _ = decoder.raw_decode(stripped[match.start() :])
            except Exception:
                continue
            if isinstance(candidate, dict):
                score = sum(
                    1
                    for key in ("status", "summary", "changed_files", "local_checks", "known_gaps", "needs_finisher")
                    if key in candidate
                )
                if score > best_score or (score == best_score and len(json.dumps(candidate, ensure_ascii=False)) > len(json.dumps(best_obj, ensure_ascii=False))):
                    best_obj = candidate
                    best_score = score
        if not best_obj:
            return {}
        obj = best_obj
    return obj if isinstance(obj, dict) else {}


def _run_git(args: List[str], *, cwd: Optional[Path] = None, timeout_s: int = 120) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str((cwd or _repo_root()).resolve()),
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_s)),
        check=False,
    )


def _git_output(args: List[str], *, cwd: Optional[Path] = None, timeout_s: int = 120) -> str:
    cp = _run_git(args, cwd=cwd, timeout_s=timeout_s)
    if int(cp.returncode) != 0:
        raise AgentSwarmError(str(cp.stderr or cp.stdout or f"git {' '.join(args)} failed").strip())
    return str(cp.stdout or "")


def _current_git_ref() -> str:
    return _git_output(["rev-parse", "HEAD"], timeout_s=30).strip()


def _git_paths_from_nul(stdout: str) -> List[str]:
    out: List[str] = []
    for raw in str(stdout or "").split("\0"):
        s = str(raw or "").strip()
        if not s:
            continue
        p = Path(s)
        if p.is_absolute() or any(part == ".." for part in p.parts):
            continue
        norm = p.as_posix()
        if norm not in out:
            out.append(norm)
    return out


def _untracked_scan_args() -> List[str]:
    return ["ls-files", "--others", "--exclude-standard", "-z"]


def _swarm_defaults() -> Dict[str, int]:
    return {
        "wall_clock_s": _runtime_policy.get_runtime_policy_int(
            "agents.swarm.wall_clock_s",
            DEFAULT_WALL_CLOCK_S,
            minimum=MIN_WALL_CLOCK_S,
            maximum=MAX_WALL_CLOCK_S,
        ),
        "maker_lease_s": _runtime_policy.get_runtime_policy_int(
            "agents.swarm.maker_lease_s",
            DEFAULT_MAKER_LEASE_S,
            minimum=MIN_MAKER_LEASE_S,
            maximum=MAX_MAKER_LEASE_S,
        ),
        "finisher_lease_s": _runtime_policy.get_runtime_policy_int(
            "agents.swarm.finisher_lease_s",
            DEFAULT_FINISHER_LEASE_S,
            minimum=MIN_FINISHER_LEASE_S,
            maximum=MAX_FINISHER_LEASE_S,
        ),
        "max_makers": _runtime_policy.get_runtime_policy_int(
            "agents.swarm.max_makers",
            DEFAULT_MAX_MAKERS,
            minimum=MIN_MAX_MAKERS,
            maximum=MAX_MAX_MAKERS,
        ),
        "max_finishers": _runtime_policy.get_runtime_policy_int(
            "agents.swarm.max_finishers",
            DEFAULT_MAX_FINISHERS,
            minimum=MIN_MAX_FINISHERS,
            maximum=MAX_MAX_FINISHERS,
        ),
        "max_total_tokens": _runtime_policy.get_runtime_policy_int(
            "agents.swarm.max_total_tokens",
            DEFAULT_MAX_TOTAL_TOKENS,
            minimum=MIN_MAX_TOTAL_TOKENS,
            maximum=MAX_MAX_TOTAL_TOKENS,
        ),
    }


def _is_allowed_overlay_path(rel: str) -> bool:
    rel_path = Path(str(rel or ""))
    if rel_path.is_absolute():
        return False
    norm = rel_path.as_posix().strip()
    if not norm:
        return False
    if any(part == ".." for part in rel_path.parts):
        return False
    if any(part == "__pycache__" for part in rel_path.parts):
        return False
    if norm.endswith((".pyc", ".pyo")):
        return False
    for prefix in OVERLAY_DENY_PREFIXES:
        if norm.startswith(prefix):
            return False
    if norm in OVERLAY_ALLOW_FILES:
        return True
    if rel_path.suffix.lower() not in OVERLAY_ALLOW_EXTENSIONS:
        return False
    if len(rel_path.parts) == 1:
        return True
    root = rel_path.parts[0] if rel_path.parts else ""
    return root in OVERLAY_ALLOW_ROOTS


def _capture_repo_overlay() -> Dict[str, Any]:
    tracked = _run_git(["diff", "--binary", "HEAD"], timeout_s=120)
    if int(tracked.returncode) != 0:
        raise AgentSwarmError(str(tracked.stderr or tracked.stdout or "git diff failed").strip())
    untracked_error = ""
    try:
        untracked = _run_git(_untracked_scan_args(), timeout_s=60)
    except Exception as exc:
        untracked = subprocess.CompletedProcess(["git", *_untracked_scan_args()], 1, stdout="", stderr=str(exc))
        untracked_error = str(exc).strip()
    if int(untracked.returncode) != 0:
        if not untracked_error:
            untracked_error = str(untracked.stderr or untracked.stdout or "git ls-files failed").strip()
        raw_untracked: List[str] = []
    else:
        raw_untracked = _git_paths_from_nul(str(untracked.stdout or ""))
    untracked_files = [path for path in raw_untracked if _is_allowed_overlay_path(path)]
    payload = {
        "tracked_patch": str(tracked.stdout or ""),
        "tracked_dirty": bool(str(tracked.stdout or "").strip()),
        "untracked_files": untracked_files,
        "untracked_count": len(untracked_files),
        "untracked_skipped_count": max(0, len(raw_untracked) - len(untracked_files)),
    }
    if untracked_error:
        payload["untracked_scan_error"] = untracked_error
    return payload


def _apply_overlay_to_worktree(*, worktree: Path, overlay: Dict[str, Any]) -> None:
    tracked_patch = str(overlay.get("tracked_patch") or "")
    if tracked_patch.strip():
        patch_path = (worktree / ".swarm_overlay.patch").resolve()
        patch_path.write_text(tracked_patch, encoding="utf-8")
        cp = _run_git(["apply", "--binary", str(patch_path)], cwd=worktree, timeout_s=120)
        if int(cp.returncode) != 0:
            raise AgentSwarmError(str(cp.stderr or cp.stdout or "git apply failed").strip())
        try:
            patch_path.unlink()
        except Exception:
            pass
    for rel in list(overlay.get("untracked_files") or []):
        rel_path = Path(str(rel))
        src = (_repo_root() / rel_path).resolve()
        dst = (worktree / rel_path).resolve()
        if not src.exists() or src.is_symlink():
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def _create_baseline_commit(worktree: Path, *, message: str) -> None:
    _run_git(["add", "-A"], cwd=worktree, timeout_s=120)
    cp = subprocess.run(
        ["git", "commit", "--allow-empty", "--no-gpg-sign", "-m", message],
        cwd=str(worktree.resolve()),
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "EIEL Swarm",
            "GIT_AUTHOR_EMAIL": "operator@example.invalid",
            "GIT_COMMITTER_NAME": "EIEL Swarm",
            "GIT_COMMITTER_EMAIL": "operator@example.invalid",
        },
        check=False,
    )
    if int(cp.returncode) != 0:
        raise AgentSwarmError(str(cp.stderr or cp.stdout or "git commit failed").strip())


def _provision_worktree(*, swarm_id: str, worker_id: str, base_ref: str, overlay: Dict[str, Any]) -> Path:
    worktree = (_worktrees_root() / _safe_name(swarm_id) / _safe_name(worker_id)).resolve()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    cp = _run_git(["worktree", "add", "--detach", str(worktree), str(base_ref)], timeout_s=180)
    if int(cp.returncode) != 0:
        raise AgentSwarmError(str(cp.stderr or cp.stdout or "git worktree add failed").strip())
    _apply_overlay_to_worktree(worktree=worktree, overlay=overlay)
    _create_baseline_commit(worktree, message=f"EIEL swarm baseline {swarm_id}/{worker_id}")
    return worktree


def _git_tracked_changed_paths(worktree: Path) -> List[str]:
    cp = _run_git(["diff", "--name-only", "HEAD"], cwd=worktree, timeout_s=60)
    if int(cp.returncode) != 0:
        return []
    out: List[str] = []
    for raw in str(cp.stdout or "").splitlines():
        s = str(raw or "").strip()
        if s:
            out.append(s)
    return out


def _git_untracked_paths(worktree: Path) -> List[str]:
    try:
        cp = _run_git(_untracked_scan_args(), cwd=worktree, timeout_s=60)
    except Exception:
        return []
    if int(cp.returncode) != 0:
        return []
    return _git_paths_from_nul(str(cp.stdout or ""))


def _git_changed_paths(worktree: Path) -> List[str]:
    out: List[str] = []
    for rel in _git_tracked_changed_paths(worktree) + _git_untracked_paths(worktree):
        if rel not in out:
            out.append(rel)
    return out


def _git_untracked_patch_text(*, worktree: Path, rel: str) -> str:
    cp = subprocess.run(
        ["git", "diff", "--binary", "--no-index", "--", "/dev/null", str(rel)],
        cwd=str(worktree.resolve()),
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if int(cp.returncode) not in {0, 1}:
        raise AgentSwarmError(str(cp.stderr or cp.stdout or f"git diff --no-index failed for {rel}").strip())
    return str(cp.stdout or "")


def _write_patch_artifact(*, worktree: Path, dst: Path) -> Dict[str, Any]:
    cp = _run_git(["diff", "--binary", "HEAD"], cwd=worktree, timeout_s=120)
    if int(cp.returncode) != 0:
        raise AgentSwarmError(str(cp.stderr or cp.stdout or "git diff failed").strip())
    patch_parts = [str(cp.stdout or "")]
    for rel in _git_untracked_paths(worktree):
        patch_parts.append(_git_untracked_patch_text(worktree=worktree, rel=rel))
    patch_text = "".join(patch_parts)
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(patch_text, encoding="utf-8")
    return {
        "path": str(dst),
        "size_bytes": int(dst.stat().st_size if dst.exists() else 0),
        "has_changes": bool(patch_text.strip()),
    }


def _git_apply_patch(*, worktree: Path, patch_path: Path) -> Dict[str, Any]:
    cp = _run_git(["apply", "--3way", str(patch_path.resolve())], cwd=worktree, timeout_s=120)
    return {
        "ok": bool(int(cp.returncode) == 0),
        "error": str(cp.stderr or cp.stdout or "").strip(),
    }


def _ensure_state(state: Dict[str, Any]) -> Dict[str, Any]:
    state.setdefault("schema_version", SCHEMA_STATE)
    state.setdefault("workers", [])
    state.setdefault("artifacts", {})
    state.setdefault("integration", {})
    state.setdefault("cancel_requested", False)
    state.setdefault("known_gaps", [])
    state.setdefault("status_detail", "")
    state.setdefault("active_progress_note", "")
    state.setdefault("last_meaningful_progress_utc", "")
    state.setdefault("last_meaningful_progress_event", "")
    state.setdefault("active_slice_deadline_utc", "")
    state.setdefault("runner_mode", "thread")
    state.setdefault("runner_pid", 0)
    state.setdefault("token_usage", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0})
    return state


def _empty_token_usage() -> Dict[str, int]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


def _normalize_token_usage(value: Any) -> Dict[str, int]:
    row = value if isinstance(value, dict) else {}
    input_tokens = max(0, int(row.get("input_tokens") or 0))
    output_tokens = max(0, int(row.get("output_tokens") or 0))
    total_tokens = max(0, int(row.get("total_tokens") or (input_tokens + output_tokens) or 0))
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": total_tokens,
    }


def _sum_token_usage(*values: Any) -> Dict[str, int]:
    total = _empty_token_usage()
    for value in values:
        usage = _normalize_token_usage(value)
        total["input_tokens"] += int(usage.get("input_tokens") or 0)
        total["output_tokens"] += int(usage.get("output_tokens") or 0)
        total["total_tokens"] += int(usage.get("total_tokens") or 0)
    return total


def _aggregate_swarm_token_usage(*, lead_usage: Any = None, workers: Optional[List[Dict[str, Any]]] = None) -> Dict[str, int]:
    parts: List[Any] = []
    if lead_usage is not None:
        parts.append(lead_usage)
    for worker in list(workers or []):
        if isinstance(worker, dict):
            parts.append(worker.get("token_usage"))
            finisher_attempt = worker.get("finisher_attempt")
            if isinstance(finisher_attempt, dict):
                parts.append(finisher_attempt.get("token_usage"))
    return _sum_token_usage(*parts)


def _swarm_tokens_over_cap(*, state: Dict[str, Any]) -> bool:
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    cap = max(0, int(config.get("max_total_tokens") or 0))
    if cap <= 0:
        return False
    usage = _normalize_token_usage(state.get("token_usage"))
    return int(usage.get("total_tokens") or 0) > cap


def _pid_alive(pid: int) -> bool:
    raw_pid = int(pid or 0)
    if raw_pid <= 0:
        return False
    try:
        os.kill(raw_pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _runner_alive(swarm_id: str, state: Dict[str, Any]) -> bool:
    mode = str(state.get("runner_mode") or "thread").strip().lower()
    if mode == "inline":
        return _pid_alive(int(state.get("runner_pid") or 0))
    return _thread_alive(swarm_id)


def _remaining_budget_s(state: Dict[str, Any]) -> Optional[int]:
    deadline = _parse_utc(str(state.get("deadline_utc") or ""))
    if deadline is None:
        return None
    return max(0, int((deadline - _utc_now()).total_seconds()))


def _finalize_swarm_terminal_state(
    swarm_id: str,
    *,
    status: str,
    phase: str,
    reason: str,
    gap: str = "",
    event_name: str,
) -> Dict[str, Any]:
    with _swarm_lock(swarm_id):
        state = _load_state(swarm_id)
        current_status = str(state.get("status") or "").strip().lower()
        if current_status in TERMINAL_SWARM_STATUSES:
            return state
        state["status"] = status
        state["phase"] = phase
        state["finished_utc"] = _utc_iso()
        state["status_detail"] = phase
        state["active_progress_note"] = ""
        state["active_slice_deadline_utc"] = ""
        if gap:
            state.setdefault("known_gaps", [])
            known_gaps = list(state.get("known_gaps") or [])
            if gap not in known_gaps:
                known_gaps.append(gap)
            state["known_gaps"] = known_gaps
        state = _persist_state(state)
    _append_event(swarm_id, event_name, phase=phase, reason=reason)
    return state


def _refresh_swarm_runtime_state(swarm_id: str) -> Dict[str, Any]:
    state = _load_state(swarm_id)
    status = str(state.get("status") or "").strip().lower()
    if status in TERMINAL_SWARM_STATUSES:
        return state
    if bool(state.get("cancel_requested")) and not _thread_alive(swarm_id):
        return _finalize_swarm_terminal_state(
            swarm_id,
            status="cancelled",
            phase="cancelled",
            reason=str(state.get("cancel_reason") or "cancelled"),
            event_name="swarm_cancelled",
        )
    remaining = _remaining_budget_s(state)
    if remaining is not None and remaining <= 0:
        return _finalize_swarm_terminal_state(
            swarm_id,
            status="timed_out",
            phase="timed_out",
            reason="Swarm wall clock expired.",
            gap="Swarm hit its wall-clock deadline before completion.",
            event_name="swarm_timed_out",
        )
    if not _runner_alive(swarm_id, state):
        return _finalize_swarm_terminal_state(
            swarm_id,
            status="interrupted",
            phase="interrupted",
            reason="Swarm runner is no longer attached.",
            gap="Swarm lost its runner before reaching a terminal result.",
            event_name="swarm_interrupted",
        )
    return state


def _enforce_swarm_runtime_state(swarm_id: str, *, phase: str) -> Dict[str, Any]:
    state = _refresh_swarm_runtime_state(swarm_id)
    status = str(state.get("status") or "").strip().lower()
    if status in TERMINAL_SWARM_STATUSES:
        raise SwarmRuntimeStop(f"swarm stopped during {phase}: {status}")
    if bool(state.get("cancel_requested")):
        _finalize_swarm_terminal_state(
            swarm_id,
            status="cancelled",
            phase="cancelled",
            reason=str(state.get("cancel_reason") or "cancelled"),
            event_name="swarm_cancelled",
        )
        raise SwarmRuntimeStop(f"swarm cancelled during {phase}")
    remaining = _remaining_budget_s(state)
    if remaining is not None and remaining <= 0:
        _finalize_swarm_terminal_state(
            swarm_id,
            status="timed_out",
            phase="timed_out",
            reason="Swarm wall clock expired.",
            gap="Swarm hit its wall-clock deadline before completion.",
            event_name="swarm_timed_out",
        )
        raise SwarmRuntimeStop(f"swarm timed out during {phase}")
    return state


def _effective_worker_timeout_s(swarm_id: str, *, requested_s: int, phase: str) -> int:
    state = _enforce_swarm_runtime_state(swarm_id, phase=phase)
    remaining = _remaining_budget_s(state)
    requested = max(1, int(requested_s))
    if remaining is None:
        return requested
    return max(1, min(requested, remaining))


def _effective_slice_timeout_s(*, requested_s: int, state: Dict[str, Any]) -> int:
    requested = max(1, int(requested_s))
    deadline = _parse_utc(str(state.get("active_slice_deadline_utc") or ""))
    if deadline is None:
        return requested
    remaining = max(0, int((deadline - _utc_now()).total_seconds()))
    if remaining <= 0:
        return requested
    return max(1, min(requested, remaining))


def _append_event(swarm_id: str, event: str, **payload: Any) -> None:
    row = {
        "schema_version": SCHEMA_EVENT,
        "swarm_id": swarm_id,
        "utc": _utc_iso(),
        "event": str(event),
        **payload,
    }
    _append_jsonl(_events_file(swarm_id), row)


def _load_state(swarm_id: str) -> Dict[str, Any]:
    return _ensure_state(_read_json(_state_file(swarm_id)))


def _persist_state(state: Dict[str, Any]) -> Dict[str, Any]:
    state = _ensure_state(dict(state))
    state["updated_utc"] = _utc_iso()
    _write_json_atomic(_state_file(str(state.get("swarm_id") or "")), state)
    return state


def _record_swarm_progress(
    swarm_id: str,
    *,
    phase: Optional[str] = None,
    detail: str = "",
    note: str = "",
    deadline_utc: str = "",
    event_name: str = "",
) -> Dict[str, Any]:
    with _swarm_lock(swarm_id):
        state = _load_state(swarm_id)
        if phase is not None:
            state["phase"] = str(phase or state.get("phase") or "").strip()
        if detail:
            state["status_detail"] = str(detail).strip()
        if note:
            state["active_progress_note"] = str(note).strip()
        if deadline_utc is not None:
            state["active_slice_deadline_utc"] = str(deadline_utc or "").strip()
        state["last_meaningful_progress_utc"] = _utc_iso()
        if event_name:
            state["last_meaningful_progress_event"] = str(event_name).strip()
        state = _persist_state(state)
    return state


def _write_worker_state(swarm_id: str, worker: Dict[str, Any]) -> None:
    _write_json_atomic(_worker_state_file(swarm_id, str(worker.get("worker_id") or "")), dict(worker))


def _register_thread(swarm_id: str, thread: Optional[threading.Thread]) -> None:
    with _SWARM_THREADS_GUARD:
        if thread is None:
            _SWARM_THREADS.pop(swarm_id, None)
        else:
            _SWARM_THREADS[swarm_id] = thread


def _thread_alive(swarm_id: str) -> bool:
    with _SWARM_THREADS_GUARD:
        thread = _SWARM_THREADS.get(swarm_id)
    return bool(thread is not None and thread.is_alive())


def _deadline_utc(created_utc: str, wall_clock_s: int) -> str:
    created = _parse_utc(created_utc)
    if created is None:
        created = _utc_now()
    return (created + _dt.timedelta(seconds=int(wall_clock_s))).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _new_swarm_id(binding: Dict[str, str], objective: str) -> str:
    _ = binding, objective
    return f"{_utc_compact().lower()}-{uuid.uuid4().hex[:8]}"


def _lead_fallback_plan(*, objective: str, max_makers: int, maker_lease_s: int) -> Dict[str, Any]:
    return {
        "summary": "Fallback single-slice swarm plan.",
        "good_enough_bar": "Land one coherent implementation slice and report remaining gaps briefly.",
        "cut_rules": ["Prefer core path over polish.", "Avoid waiting on perfect certainty."],
        "maker_slices": [
            {
                "id": "slice-01",
                "title": "Primary delivery slice",
                "objective": str(objective or "").strip(),
                "scope": "Use best judgment inside the repo to ship the main path.",
                "expected_output": "Patch plus short structured summary.",
                "lease_s": int(maker_lease_s),
            }
        ][: max(1, int(max_makers))],
    }


def _extract_shared_markdown_target(objective: str) -> str:
    match = re.search(r"(docs/[A-Za-z0-9_./-]+\.md)", str(objective or ""))
    if not match:
        return ""
    raw = str(match.group(1) or "").strip().strip("/")
    path = Path(raw)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        return ""
    return path.as_posix()


def _looks_like_consolidator_slice(slice_spec: Dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(slice_spec.get("title") or ""),
            str(slice_spec.get("objective") or ""),
            str(slice_spec.get("scope") or ""),
            str(slice_spec.get("expected_output") or ""),
        ]
    ).lower()
    return any(token in text for token in ("consolidat", "final report", "merge", "synthes", "operator-facing report"))


def _normalize_maker_slices_for_output_paths(*, objective: str, maker_slices: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    shared_target = _extract_shared_markdown_target(objective)
    rows = [dict(item) for item in list(maker_slices or []) if isinstance(item, dict)]
    if not shared_target or len(rows) <= 1:
        return rows
    target_path = Path(shared_target)
    parent = target_path.parent.as_posix()
    stem = target_path.stem
    suffix = target_path.suffix or ".md"
    consolidator_idx = next((idx for idx, row in enumerate(rows) if _looks_like_consolidator_slice(row)), len(rows) - 1)
    out: List[Dict[str, Any]] = []
    for idx, row in enumerate(rows):
        worker_id = _safe_name(str(row.get("id") or f"slice-{idx + 1:02d}"))
        normalized = dict(row)
        if idx == consolidator_idx:
            normalized["artifact_path"] = shared_target
            normalized["shared_output_owner"] = True
        else:
            normalized["artifact_path"] = f"{parent}/{stem}__{worker_id}{suffix}" if parent else f"{stem}__{worker_id}{suffix}"
            normalized["shared_output_owner"] = False
            expected = str(normalized.get("expected_output") or "").strip()
            if expected:
                normalized["expected_output"] = f"{expected} Write your slice findings to {normalized['artifact_path']}."
            else:
                normalized["expected_output"] = f"Write your slice findings to {normalized['artifact_path']}."
        out.append(normalized)
    return out


def _compose_lead_message(*, objective: str, binding: Dict[str, str], state: Dict[str, Any]) -> str:
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    defaults = _swarm_defaults()
    maker_lease_s = int(config.get("maker_lease_s") or defaults["maker_lease_s"])
    return (
        "You are planning a 60-minute delivery swarm.\n"
        "Output one JSON object only. No prose, no markdown fences.\n\n"
        "Required keys:\n"
        "- summary: string\n"
        "- good_enough_bar: string\n"
        "- cut_rules: array of short strings\n"
        "- maker_slices: array of 1-4 objects with keys:\n"
        "  id, title, objective, scope, expected_output, lease_s\n\n"
        "Rules:\n"
        "- Keep slices independent where possible.\n"
        "- Respect the hard wall clock.\n"
        f"- Use maker_lease_s={maker_lease_s} by default unless a slice clearly needs less time.\n"
        "- Prefer a smaller number of high-leverage slices over busywork.\n\n"
        f"Binding: {binding['type']}::{binding['id']}\n"
        f"Objective:\n{objective.strip()}\n\n"
        f"Budget: wall_clock_s={int(config.get('wall_clock_s') or defaults['wall_clock_s'])} "
        f"max_makers={int(config.get('max_makers') or defaults['max_makers'])} "
        f"max_finishers={int(config.get('max_finishers') or defaults['max_finishers'])} "
        f"max_total_tokens={int(config.get('max_total_tokens') or defaults['max_total_tokens'])}\n"
    )


def _compose_maker_message(*, objective: str, slice_spec: Dict[str, Any], state: Dict[str, Any]) -> str:
    artifact_path = str(slice_spec.get("artifact_path") or "").strip()
    shared_owner = bool(slice_spec.get("shared_output_owner"))
    artifact_instructions = (
        (
            f"Durable output path: {artifact_path}\n"
            "You are the only slice allowed to write the shared final report path.\n\n"
        )
        if artifact_path and shared_owner
        else (
            f"Durable output path: {artifact_path}\n"
            "Do not write the shared final report path directly. Land your slice output only at the assigned durable output path.\n\n"
        )
        if artifact_path
        else ""
    )
    return (
        "Implement your assigned swarm slice in this worktree.\n"
        "Do the work. Edit files directly. Run cheap local checks if they help.\n"
        "Do not create git commits. Do not wait for outside confirmation.\n"
        "If unsure, take the safest reversible path and keep moving.\n\n"
        f"{artifact_instructions}"
        "Return one JSON object only. No prose, no markdown fences.\n"
        "Required keys:\n"
        "- status: delivered|delivered_with_known_gaps|blocked_need_master_decision|split_needed|failed_with_evidence\n"
        "- summary: string\n"
        "- changed_files: array of repo-relative paths\n"
        "- local_checks: array of objects with keys command,result,notes\n"
        "- known_gaps: array of short strings\n"
        "- needs_finisher: boolean\n\n"
        f"Overall objective:\n{objective.strip()}\n\n"
        f"Assigned slice:\n{json.dumps(slice_spec, ensure_ascii=False, indent=2)}\n\n"
        f"Deadline UTC: {state.get('deadline_utc')}\n"
    )


def _compact_worker_text(text: str, *, limit: int = 280) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _looks_like_codex_transcript_wrapper(raw):
        for line in reversed(raw.splitlines()):
            txt = str(line or "").strip()
            if txt.startswith("ERROR:"):
                return txt[:limit]
        return "Worker runtime returned a transcript wrapper instead of a clean structured completion."
    return raw[:limit]


def _sanitize_maker_summary_for_finisher(maker_summary: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(maker_summary or {})
    return {
        "worker_id": str(row.get("worker_id") or "").strip(),
        "kind": str(row.get("kind") or "").strip(),
        "slice_id": str(row.get("slice_id") or "").strip(),
        "title": str(row.get("title") or "").strip(),
        "status": str(row.get("status") or "").strip(),
        "summary": _compact_worker_text(str(row.get("summary") or ""), limit=400),
        "known_gaps": [_compact_worker_text(str(item or "")) for item in list(row.get("known_gaps") or [])[:5] if _compact_worker_text(str(item or ""))],
        "changed_files": [str(item or "").strip() for item in list(row.get("changed_files") or [])[:20] if str(item or "").strip()],
        "local_checks": list(row.get("local_checks") or [])[:5],
        "patch_path": str(row.get("patch_path") or "").strip(),
        "summary_path": str(row.get("summary_path") or "").strip(),
        "error": _compact_worker_text(str(row.get("error") or ""), limit=400),
        "finished_utc": str(row.get("finished_utc") or "").strip(),
        "patch_has_changes": bool(row.get("patch_has_changes")),
        "patch_size_bytes": int(row.get("patch_size_bytes") or 0),
        "needs_finisher": bool(row.get("needs_finisher")),
    }


def _compose_finisher_message(*, objective: str, slice_spec: Dict[str, Any], maker_summary: Dict[str, Any], state: Dict[str, Any]) -> str:
    safe_summary = _sanitize_maker_summary_for_finisher(maker_summary)
    return (
        "You are the finisher for an existing swarm slice.\n"
        "Work in this same worktree and fix the slice on the spot.\n"
        "Prefer repairing rough edges, failed checks, or integration risk over expanding scope.\n"
        "Do not create git commits.\n\n"
        "Return one JSON object only. No prose, no markdown fences.\n"
        "Required keys:\n"
        "- status: delivered|delivered_with_known_gaps|failed_with_evidence\n"
        "- summary: string\n"
        "- changed_files: array of repo-relative paths\n"
        "- local_checks: array of objects with keys command,result,notes\n"
        "- known_gaps: array of short strings\n\n"
        f"Overall objective:\n{objective.strip()}\n\n"
        f"Slice:\n{json.dumps(slice_spec, ensure_ascii=False, indent=2)}\n\n"
        f"Maker summary:\n{json.dumps(safe_summary, ensure_ascii=False, indent=2)}\n\n"
        f"Deadline UTC: {state.get('deadline_utc')}\n"
    )


def _worker_record(
    *,
    worker_id: str,
    kind: str,
    slice_spec: Dict[str, Any],
    worktree: Path,
    lease_s: int,
) -> Dict[str, Any]:
    now = _utc_iso()
    return {
        "worker_id": worker_id,
        "kind": kind,
        "slice_id": str(slice_spec.get("id") or worker_id),
        "title": str(slice_spec.get("title") or worker_id),
        "status": "running",
        "lease_s": int(lease_s),
        "worktree_path": str(worktree),
        "started_utc": now,
        "updated_utc": now,
        "summary": "",
        "known_gaps": [],
        "changed_files": [],
        "local_checks": [],
        "patch_path": "",
        "raw_output_path": "",
        "summary_path": "",
        "error": "",
    }


def _run_worker_turn(
    *,
    swarm_id: str,
    objective: str,
    kind: str,
    slice_spec: Dict[str, Any],
    prompt_name: str,
    worktree: Path,
    lease_s: int,
    artifacts_dir: Path,
    state: Dict[str, Any],
    maker_summary: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    worker_id = str(slice_spec.get("id") or f"{kind}-{_safe_name(slice_spec.get('title') or 'slice')}").strip()
    record = _worker_record(worker_id=worker_id, kind=kind, slice_spec=slice_spec, worktree=worktree, lease_s=lease_s)
    if kind == "maker":
        message = _compose_maker_message(objective=objective, slice_spec=slice_spec, state=state)
    else:
        message = _compose_finisher_message(
            objective=objective,
            slice_spec=slice_spec,
            maker_summary=dict(maker_summary or {}),
            state=state,
        )
    raw_path = (artifacts_dir / f"{_safe_name(worker_id)}__{kind}.raw.txt").resolve()
    summary_path = (artifacts_dir / f"{_safe_name(worker_id)}__{kind}.summary.json").resolve()
    patch_path = (artifacts_dir / f"{_safe_name(worker_id)}__{kind}.patch").resolve()
    try:
        # The caller already enforces the live swarm runtime state and hands us
        # the current worker lease. Bound only by the current slice deadline if
        # one is actively set, so stale persisted swarm deadlines do not bypass
        # worker-result integrity checks.
        effective_timeout_s = _effective_slice_timeout_s(
            requested_s=lease_s,
            state=state,
        )
        record["lease_s"] = int(effective_timeout_s)
        turn = run_codex_oneshot(
            worker_id=f"swarm::{swarm_id}::{worker_id}::{kind}",
            binding_type="swarm",
            binding_id=swarm_id,
            role=f"swarm_{kind}",
            message=message,
            bootstrap_text=_load_prompt_text(prompt_name),
            cwd=worktree,
            timeout_s=effective_timeout_s,
        )
    except Exception as exc:
        turn = {
            "ok": False,
            "exit_code": 1,
            "content": "",
            "stderr": str(exc),
            "stdout": "",
        }
    raw_text = str(turn.get("content") or turn.get("stderr") or "")
    _write_text(raw_path, raw_text)
    summary = _json_fallback(raw_text)
    token_usage = _normalize_token_usage(turn.get("usage"))
    changed_files = _git_changed_paths(worktree)
    if not summary:
        summary = {
            "status": ("failed_with_evidence" if not bool(turn.get("ok")) else "delivered_with_known_gaps"),
            "summary": (raw_text.strip() or "Worker did not return structured JSON."),
            "changed_files": changed_files,
            "local_checks": [],
            "known_gaps": ([] if bool(turn.get("ok")) else [str(turn.get("stderr") or "worker turn failed").strip()]),
            "needs_finisher": bool(turn.get("ok")),
        }
    reported_changed_files = [str(path or "").strip() for path in list(summary.get("changed_files") or []) if str(path or "").strip()]
    summary["changed_files"] = list(changed_files or reported_changed_files)
    summary["local_checks"] = list(summary.get("local_checks") or [])
    summary["known_gaps"] = list(summary.get("known_gaps") or [])
    summary["status"] = _normalize_worker_status(summary.get("status"), kind=kind, known_gaps=summary["known_gaps"])
    patch_info = _write_patch_artifact(worktree=worktree, dst=patch_path)
    has_material_changes = bool(patch_info.get("has_changes")) or bool(changed_files)
    if not bool(turn.get("ok")):
        summary["status"] = (
            "delivered_with_known_gaps"
            if _summary_claims_delivery(summary) and has_material_changes and _looks_like_codex_transcript_wrapper(raw_text)
            else "failed_with_evidence"
        )
        summary["known_gaps"] = _merge_known_gaps(
            list(summary.get("known_gaps") or []),
            [_compact_worker_text(str(turn.get("stderr") or raw_text or "worker turn failed").strip(), limit=400)],
        )
        if not str(summary.get("summary") or "").strip():
            summary["summary"] = "Worker runtime failed before clean delivery."
    if reported_changed_files and not changed_files:
        summary["known_gaps"] = _merge_known_gaps(
            list(summary.get("known_gaps") or []),
            ["Worker reported file changes but the worktree diff was empty."],
        )
    if not bool(patch_info.get("has_changes")) and not changed_files and _worker_claims_any_delivery(summary):
        summary["status"] = "failed_with_evidence"
        summary["known_gaps"] = _merge_known_gaps(
            list(summary.get("known_gaps") or []),
            ["Worker returned a delivered status but produced no code changes."],
        )
    _write_json_atomic(summary_path, dict(summary))
    record.update(
        {
            "status": str(summary.get("status") or "delivered_with_known_gaps"),
            "updated_utc": _utc_iso(),
            "finished_utc": _utc_iso(),
            "summary": str(summary.get("summary") or "").strip(),
            "changed_files": list(summary.get("changed_files") or []),
            "local_checks": list(summary.get("local_checks") or []),
            "known_gaps": list(summary.get("known_gaps") or []),
            "raw_output_path": str(raw_path),
            "summary_path": str(summary_path),
            "patch_path": str(patch_path),
            "patch_has_changes": bool(patch_info.get("has_changes")),
            "patch_size_bytes": int(patch_info.get("size_bytes") or 0),
            "needs_finisher": bool(summary.get("needs_finisher")),
            "error": ("" if bool(turn.get("ok")) else str(turn.get("stderr") or raw_text or "worker turn failed")),
            "token_usage": token_usage,
        }
    )
    _write_worker_state(swarm_id, record)
    return record


def _select_finisher_targets(workers: List[Dict[str, Any]], *, max_finishers: int) -> List[Dict[str, Any]]:
    targets: List[Dict[str, Any]] = []
    for worker in workers:
        status = str(worker.get("status") or "").strip().lower()
        needs_finisher = bool(worker.get("needs_finisher"))  # legacy/future-safe
        if status in {"delivered_with_known_gaps", "blocked_need_master_decision", "split_needed", "failed_with_evidence"}:
            targets.append(worker)
            continue
        if list(worker.get("known_gaps") or []):
            targets.append(worker)
            continue
        if needs_finisher:
            targets.append(worker)
    return targets[: max(0, int(max_finishers))]


def _merge_known_gaps(*values: List[str]) -> List[str]:
    merged: List[str] = []
    for group in values:
        for raw in list(group or []):
            gap = str(raw or "").strip()
            if gap and gap not in merged:
                merged.append(gap)
    return merged


_MAKER_ALLOWED_STATUSES = {
    "delivered",
    "delivered_with_known_gaps",
    "blocked_need_master_decision",
    "split_needed",
    "failed_with_evidence",
}
_FINISHER_ALLOWED_STATUSES = {
    "delivered",
    "delivered_with_known_gaps",
    "failed_with_evidence",
}
_WORKER_DELIVERY_STATUSES = {"delivered", "delivered_with_known_gaps"}


def _normalize_worker_status(raw_status: Any, *, kind: str, known_gaps: List[str]) -> str:
    status = str(raw_status or "").strip().lower()
    allowed = _FINISHER_ALLOWED_STATUSES if str(kind or "").strip().lower() == "finisher" else _MAKER_ALLOWED_STATUSES
    if status in allowed:
        return status
    if status:
        known_gaps.append(f"Worker returned unsupported status '{status}'; treating it as failed_with_evidence.")
    else:
        known_gaps.append("Worker omitted status; treating it as failed_with_evidence.")
    return "failed_with_evidence"


def _worker_claims_clean_delivery(worker: Dict[str, Any]) -> bool:
    return str(worker.get("status") or "").strip().lower() == "delivered"


def _worker_claims_any_delivery(worker: Dict[str, Any]) -> bool:
    return str(worker.get("status") or "").strip().lower() in _WORKER_DELIVERY_STATUSES


def _worker_sort_key(worker: Dict[str, Any]) -> tuple[int, str, str]:
    plan_index = worker.get("plan_index")
    try:
        idx = int(plan_index)
    except Exception:
        idx = 1_000_000
    return (
        idx,
        str(worker.get("slice_id") or worker.get("worker_id") or ""),
        str(worker.get("worker_id") or ""),
    )


def _summary_claims_delivery(summary: Dict[str, Any]) -> bool:
    return _worker_claims_any_delivery(summary)


def _looks_like_codex_transcript_wrapper(text: str) -> bool:
    raw = str(text or "")
    return "OpenAI Codex v" in raw and "\ncodex\n" in raw


def _resolve_finisher_result(*, maker: Dict[str, Any], finisher: Dict[str, Any]) -> Dict[str, Any]:
    finisher_attempt = {
        "status": str(finisher.get("status") or "").strip(),
        "summary": str(finisher.get("summary") or "").strip(),
        "error": str(finisher.get("error") or "").strip(),
        "known_gaps": list(finisher.get("known_gaps") or []),
        "local_checks": list(finisher.get("local_checks") or []),
        "token_usage": _normalize_token_usage(finisher.get("token_usage")),
        "patch_path": str(finisher.get("patch_path") or "").strip(),
        "patch_has_changes": bool(finisher.get("patch_has_changes")),
        "patch_size_bytes": int(finisher.get("patch_size_bytes") or 0),
        "raw_output_path": str(finisher.get("raw_output_path") or "").strip(),
        "summary_path": str(finisher.get("summary_path") or "").strip(),
        "finished_utc": str(finisher.get("finished_utc") or "").strip(),
    }
    merged_gaps = _merge_known_gaps(
        list(maker.get("known_gaps") or []),
        list(finisher.get("known_gaps") or []),
    )
    if bool(finisher.get("patch_has_changes")) and _summary_claims_delivery(finisher):
        adopted = dict(finisher)
        adopted["kind"] = "finisher"
        adopted["slice_id"] = str(maker.get("slice_id") or adopted.get("slice_id") or "")
        adopted["title"] = str(maker.get("title") or adopted.get("title") or "")
        adopted["known_gaps"] = merged_gaps
        adopted["maker_patch_path"] = str(maker.get("patch_path") or "").strip()
        adopted["maker_summary_path"] = str(maker.get("summary_path") or "").strip()
        adopted["maker_raw_output_path"] = str(maker.get("raw_output_path") or "").strip()
        adopted["maker_status"] = str(maker.get("status") or "").strip()
        adopted["finisher_attempt"] = finisher_attempt
        return adopted

    preserved = dict(maker)
    preserved["known_gaps"] = merged_gaps
    if bool(finisher.get("patch_has_changes")) and not _summary_claims_delivery(finisher):
        preserved["known_gaps"] = _merge_known_gaps(
            preserved["known_gaps"],
            ["Finisher produced a patch without delivered status; integration kept the maker patch."],
        )
    preserved["finisher_attempt"] = finisher_attempt
    return preserved


def _run_integration(
    *,
    swarm_id: str,
    base_ref: str,
    overlay: Dict[str, Any],
    workers: List[Dict[str, Any]],
    artifacts_root: Path,
) -> Dict[str, Any]:
    _enforce_swarm_runtime_state(swarm_id, phase="integration")
    integration_dir = _provision_worktree(
        swarm_id=swarm_id,
        worker_id="integration",
        base_ref=base_ref,
        overlay=overlay,
    )
    applied: List[Dict[str, Any]] = []
    failed: List[Dict[str, Any]] = []
    for worker in sorted(workers, key=_worker_sort_key):
        _enforce_swarm_runtime_state(swarm_id, phase="integration")
        patch_path = Path(str(worker.get("patch_path") or "")).resolve()
        if not patch_path.exists() or patch_path.stat().st_size <= 0:
            continue
        result = _git_apply_patch(worktree=integration_dir, patch_path=patch_path)
        row = {
            "worker_id": str(worker.get("worker_id") or ""),
            "patch_path": str(patch_path),
            "ok": bool(result.get("ok")),
            "error": str(result.get("error") or ""),
        }
        if bool(result.get("ok")):
            applied.append(row)
        else:
            failed.append(row)
    combined_patch_path = (artifacts_root / "integration.patch").resolve()
    combined_patch = _write_patch_artifact(worktree=integration_dir, dst=combined_patch_path)
    return {
        "worktree_path": str(integration_dir),
        "applied": applied,
        "failed": failed,
        "combined_patch_path": str(combined_patch_path),
        "combined_patch_has_changes": bool(combined_patch.get("has_changes")),
        "changed_files": _git_changed_paths(integration_dir),
    }


def _cleanup_worktree(path: Path) -> Dict[str, Any]:
    target = path.resolve()
    if not target.exists():
        return {"path": str(target), "ok": True, "removed": False, "method": "missing", "error": ""}
    cp = _run_git(["worktree", "remove", "--force", str(target)], timeout_s=180)
    if int(cp.returncode) == 0:
        return {"path": str(target), "ok": True, "removed": True, "method": "git_worktree_remove", "error": ""}
    try:
        shutil.rmtree(target)
    except Exception as exc:
        return {
            "path": str(target),
            "ok": False,
            "removed": False,
            "method": "failed",
            "error": str(exc),
        }
    return {"path": str(target), "ok": True, "removed": True, "method": "shutil_rmtree", "error": ""}


def _cleanup_swarm_worktrees(swarm_id: str) -> Dict[str, Any]:
    state = _load_state(swarm_id)
    candidates: List[Path] = []
    for worker in list(state.get("workers") or []):
        raw = str(worker.get("worktree_path") or "").strip()
        if raw:
            candidates.append(Path(raw))
    integration = state.get("integration") if isinstance(state.get("integration"), dict) else {}
    raw_integration = str(integration.get("worktree_path") or "").strip()
    if raw_integration:
        candidates.append(Path(raw_integration))
    rows: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate.resolve())
        if key in seen:
            continue
        seen.add(key)
        rows.append(_cleanup_worktree(candidate))
    _run_git(["worktree", "prune"], timeout_s=120)
    summary = {
        "attempted": len(rows),
        "removed": len([row for row in rows if bool(row.get("removed"))]),
        "failed": [row for row in rows if not bool(row.get("ok"))],
        "rows": rows,
        "cleaned_utc": _utc_iso(),
    }
    with _swarm_lock(swarm_id):
        state = _load_state(swarm_id)
        state.setdefault("artifacts", {})
        state["artifacts"]["worktree_cleanup"] = summary
        _persist_state(state)
    return summary


def _write_summary_markdown(*, state: Dict[str, Any], path: Path) -> None:
    token_usage = _normalize_token_usage(state.get("token_usage"))
    lines = [
        f"# Swarm {state.get('swarm_id')}",
        "",
        f"- status: {state.get('status')}",
        f"- phase: {state.get('phase')}",
        f"- objective: {state.get('objective')}",
        f"- base_ref: {state.get('base_ref')}",
        f"- token_usage.total_tokens: {int(token_usage.get('total_tokens') or 0)}",
        "",
        "## Workers",
    ]
    for worker in list(state.get("workers") or []):
        worker_tokens = _normalize_token_usage((worker if isinstance(worker, dict) else {}).get("token_usage"))
        lines.extend(
            [
                f"- {worker.get('worker_id')} ({worker.get('kind')}): {worker.get('status')}",
                f"  - changed_files: {', '.join(list(worker.get('changed_files') or [])) or '(none)'}",
                f"  - token_usage.total_tokens: {int(worker_tokens.get('total_tokens') or 0)}",
                f"  - summary: {str(worker.get('summary') or '').strip()}",
            ]
        )
        finisher_attempt = worker.get("finisher_attempt") if isinstance(worker.get("finisher_attempt"), dict) else {}
        if finisher_attempt:
            lines.extend(
                [
                    f"  - finisher_attempt_status: {str(finisher_attempt.get('status') or '').strip() or '(missing)'}",
                    f"  - finisher_attempt_error: {str(finisher_attempt.get('error') or '').strip() or '(none)'}",
                    f"  - finisher_patch_adopted: {'yes' if str(worker.get('kind') or '') == 'finisher' else 'no'}",
                ]
            )
    integration = state.get("integration") if isinstance(state.get("integration"), dict) else {}
    if integration:
        lines.extend(
            [
                "",
                "## Integration",
                f"- worktree: {integration.get('worktree_path')}",
                f"- applied patches: {len(list(integration.get('applied') or []))}",
                f"- failed patches: {len(list(integration.get('failed') or []))}",
            ]
        )
    if list(state.get("known_gaps") or []):
        lines.extend(["", "## Known Gaps"])
        for gap in list(state.get("known_gaps") or []):
            lines.append(f"- {gap}")
    _write_text(path, "\n".join(lines).rstrip() + "\n")


def _execute_maker_slices(
    *,
    swarm_id: str,
    maker_slices: List[Dict[str, Any]],
    maker_job: Any,
) -> List[Dict[str, Any]]:
    workers: List[Dict[str, Any]] = []
    maker_stop: Optional[SwarmRuntimeStop] = None

    def _record_result(result: Dict[str, Any], *, plan_index: int) -> None:
        result["plan_index"] = int(plan_index)
        workers.append(result)
        _append_event(
            swarm_id,
            "maker_completed",
            worker_id=str(result.get("worker_id") or ""),
            status=str(result.get("status") or ""),
        )
        _record_swarm_progress(
            swarm_id,
            phase="makers",
            detail="running_makers",
            note=f"Maker {str(result.get('worker_id') or '')} finished with {str(result.get('status') or '')}.",
            deadline_utc="",
            event_name="maker_completed",
        )

    try:
        with ThreadPoolExecutor(max_workers=max(1, len(maker_slices))) as pool:
            future_map = {
                pool.submit(maker_job, slice_spec, idx): {**dict(slice_spec), "_plan_index": idx}
                for idx, slice_spec in enumerate(maker_slices)
            }
            for future in as_completed(future_map):
                try:
                    result = future.result()
                except SwarmRuntimeStop as exc:
                    maker_stop = exc
                    continue
                _record_result(result, plan_index=int(future_map[future].get("_plan_index") or 0))
    except RuntimeError as exc:
        if "interpreter shutdown" not in str(exc).lower():
            raise
        _append_event(
            swarm_id,
            "maker_parallelism_degraded",
            reason="interpreter_shutdown",
            note="Falling back to sequential maker execution because thread-pool scheduling is unavailable.",
        )
        _record_swarm_progress(
            swarm_id,
            phase="makers",
            detail="running_makers",
            note="Parallel maker scheduling was unavailable; continuing sequentially.",
            deadline_utc="",
            event_name="maker_parallelism_degraded",
        )
        for idx, slice_spec in enumerate(maker_slices):
            try:
                result = maker_job(slice_spec, idx)
            except SwarmRuntimeStop as stop_exc:
                maker_stop = stop_exc
                continue
            _record_result(result, plan_index=idx)

    if maker_stop is not None:
        raise maker_stop
    return sorted(workers, key=_worker_sort_key)


def _execute_swarm(swarm_id: str) -> None:
    with _swarm_lock(swarm_id):
        state = _load_state(swarm_id)
        objective = str(state.get("objective") or "").strip()
        if not objective:
            raise AgentSwarmError("objective is required")
        state["status"] = "running"
        state["phase"] = "planning"
        state["started_utc"] = _utc_iso()
        state = _persist_state(state)
        _append_event(swarm_id, "swarm_running", phase="planning")
        _record_swarm_progress(
            swarm_id,
            phase="planning",
            detail="running_planning",
            note="Lead is planning maker slices.",
            event_name="swarm_running",
        )

    state = _enforce_swarm_runtime_state(swarm_id, phase="planning")
    artifacts_root = _artifacts_root(swarm_id)
    config = state.get("config") if isinstance(state.get("config"), dict) else {}
    overlay_timeout_s = min(
        _effective_worker_timeout_s(
            swarm_id,
            requested_s=180,
            phase="planning:overlay",
        ),
        180,
    )
    _record_swarm_progress(
        swarm_id,
        phase="planning",
        detail="running_planning",
        note="Capturing repo overlay for worker worktrees.",
        deadline_utc=_deadline_utc(_utc_iso(), overlay_timeout_s),
        event_name="overlay_capture_started",
    )
    base_ref = _current_git_ref()
    overlay = _capture_repo_overlay()
    with _swarm_lock(swarm_id):
        state = _load_state(swarm_id)
        state["base_ref"] = base_ref
        state["overlay"] = {
            "tracked_dirty": bool(overlay.get("tracked_dirty")),
            "untracked_count": int(overlay.get("untracked_count") or 0),
            "untracked_skipped_count": int(overlay.get("untracked_skipped_count") or 0),
        }
        if str(overlay.get("untracked_scan_error") or "").strip():
            state["overlay"]["untracked_scan_error"] = str(overlay.get("untracked_scan_error") or "").strip()
        state = _persist_state(state)

    lead_plan_path = (artifacts_root / "lead_plan.json").resolve()
    lead_raw_path = (artifacts_root / "lead_plan.raw.txt").resolve()
    lead_timeout_s = min(
        _effective_worker_timeout_s(
            swarm_id,
            requested_s=int(config.get("maker_lease_s") or DEFAULT_MAKER_LEASE_S),
            phase="planning:lead",
        ),
        10 * 60,
    )
    _record_swarm_progress(
        swarm_id,
        phase="planning",
        detail="running_planning",
        note="Lead planning is running.",
        deadline_utc=_deadline_utc(_utc_iso(), lead_timeout_s),
        event_name="lead_planning_started",
    )
    lead_turn = run_codex_oneshot(
        worker_id=f"swarm::{swarm_id}::lead",
        binding_type="swarm",
        binding_id=swarm_id,
        role="swarm_lead",
        message=_compose_lead_message(objective=objective, binding=dict(state.get("binding") or {}), state=state),
        bootstrap_text=_load_prompt_text("swarm_lead.md"),
        cwd=_repo_root(),
        timeout_s=lead_timeout_s,
    )
    lead_usage = _normalize_token_usage(lead_turn.get("usage"))
    lead_raw = str(lead_turn.get("content") or lead_turn.get("stderr") or "")
    _write_text(lead_raw_path, lead_raw)
    plan = _json_fallback(lead_raw)
    if not isinstance(plan.get("maker_slices"), list) or not list(plan.get("maker_slices") or []):
        plan = _lead_fallback_plan(
            objective=objective,
            max_makers=int(config.get("max_makers") or DEFAULT_MAX_MAKERS),
            maker_lease_s=int(config.get("maker_lease_s") or DEFAULT_MAKER_LEASE_S),
        )
    _write_json_atomic(lead_plan_path, dict(plan))
    _append_event(swarm_id, "lead_plan_ready", maker_count=len(list(plan.get("maker_slices") or [])))
    _record_swarm_progress(
        swarm_id,
        phase="makers",
        detail="running_makers",
        note=f"Lead planned {len(list(plan.get('maker_slices') or []))} maker slices.",
        deadline_utc="",
        event_name="lead_plan_ready",
    )

    with _swarm_lock(swarm_id):
        state = _enforce_swarm_runtime_state(swarm_id, phase="makers")
        state["phase"] = "makers"
        state["artifacts"]["lead_plan_path"] = str(lead_plan_path)
        state["artifacts"]["lead_plan_raw_path"] = str(lead_raw_path)
        state["artifacts"]["lead_token_usage"] = dict(lead_usage)
        state["plan_summary"] = str(plan.get("summary") or "")
        state["good_enough_bar"] = str(plan.get("good_enough_bar") or "")
        state["cut_rules"] = list(plan.get("cut_rules") or [])
        state["token_usage"] = _aggregate_swarm_token_usage(lead_usage=lead_usage, workers=[])
        state = _persist_state(state)

    maker_slices = [s for s in list(plan.get("maker_slices") or []) if isinstance(s, dict)]
    maker_slices = _normalize_maker_slices_for_output_paths(objective=objective, maker_slices=maker_slices)
    maker_slices = maker_slices[: max(1, int(config.get("max_makers") or DEFAULT_MAX_MAKERS))]
    maker_artifacts_dir = (artifacts_root / "maker_outputs").resolve()
    maker_artifacts_dir.mkdir(parents=True, exist_ok=True)
    def _maker_job(slice_spec: Dict[str, Any], idx: int) -> Dict[str, Any]:
        _enforce_swarm_runtime_state(swarm_id, phase="makers")
        worker_id = str(slice_spec.get("id") or f"slice-{idx + 1:02d}")
        requested_lease_s = int(slice_spec.get("lease_s") or config.get("maker_lease_s") or DEFAULT_MAKER_LEASE_S)
        effective_lease_s = _effective_worker_timeout_s(
            swarm_id,
            requested_s=requested_lease_s,
            phase=f"makers:{worker_id}:lease",
        )
        _record_swarm_progress(
            swarm_id,
            phase="makers",
            detail="running_makers",
            note=f"Maker {worker_id} is running.",
            deadline_utc=_deadline_utc(_utc_iso(), effective_lease_s),
            event_name="maker_started",
        )
        worktree = _provision_worktree(
            swarm_id=swarm_id,
            worker_id=f"maker-{worker_id}",
            base_ref=base_ref,
            overlay=overlay,
        )
        return _run_worker_turn(
            swarm_id=swarm_id,
            objective=objective,
            kind="maker",
            slice_spec=slice_spec,
            prompt_name="swarm_maker.md",
            worktree=worktree,
            lease_s=effective_lease_s,
            artifacts_dir=maker_artifacts_dir,
            state=state,
        )
    workers = _execute_maker_slices(
        swarm_id=swarm_id,
        maker_slices=maker_slices,
        maker_job=_maker_job,
    )

    with _swarm_lock(swarm_id):
        state = _enforce_swarm_runtime_state(swarm_id, phase="finishers")
        state["workers"] = list(workers)
        state["token_usage"] = _aggregate_swarm_token_usage(
            lead_usage=((state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}) or {}).get("lead_token_usage"),
            workers=workers,
        )
        if _swarm_tokens_over_cap(state=state):
            cap = int(((state.get("config") if isinstance(state.get("config"), dict) else {}) or {}).get("max_total_tokens") or 0)
            total = int((state.get("token_usage") if isinstance(state.get("token_usage"), dict) else {}).get("total_tokens") or 0)
            state = _persist_state(state)
            raise AgentSwarmError(f"swarm used {total} total tokens which exceeds max_total_tokens={cap}")
        state["phase"] = "finishers"
        state = _persist_state(state)

    finisher_targets = _select_finisher_targets(workers, max_finishers=int(config.get("max_finishers") or DEFAULT_MAX_FINISHERS))
    if finisher_targets:
        finisher_artifacts_dir = (artifacts_root / "finisher_outputs").resolve()
        finisher_artifacts_dir.mkdir(parents=True, exist_ok=True)
        finished_workers: List[Dict[str, Any]] = []
        for worker in workers:
            _enforce_swarm_runtime_state(swarm_id, phase="finishers")
            target = next((t for t in finisher_targets if str(t.get("worker_id") or "") == str(worker.get("worker_id") or "")), None)
            if target is None:
                finished_workers.append(worker)
                continue
            finisher_lease_s = _effective_worker_timeout_s(
                swarm_id,
                requested_s=int(config.get("finisher_lease_s") or DEFAULT_FINISHER_LEASE_S),
                phase=f"finishers:{str(worker.get('worker_id') or '')}:lease",
            )
            _record_swarm_progress(
                swarm_id,
                phase="finishers",
                detail="running_finishers",
                note=f"Finisher is tightening {str(worker.get('worker_id') or '')}.",
                deadline_utc=_deadline_utc(_utc_iso(), finisher_lease_s),
                event_name="finisher_started",
            )
            finished = _run_worker_turn(
                swarm_id=swarm_id,
                objective=objective,
                kind="finisher",
                slice_spec={
                    "id": str(worker.get("worker_id") or ""),
                    "title": str(worker.get("title") or ""),
                    "objective": str(worker.get("summary") or ""),
                    "scope": "Repair the slice in place.",
                    "expected_output": "Improved patch plus short structured summary.",
                },
                prompt_name="swarm_finisher.md",
                worktree=Path(str(worker.get("worktree_path") or "")).resolve(),
                lease_s=finisher_lease_s,
                artifacts_dir=finisher_artifacts_dir,
                state=state,
                maker_summary=worker,
            )
            finished = _resolve_finisher_result(maker=worker, finisher=finished)
            finished["slice_id"] = str(worker.get("slice_id") or finished.get("slice_id") or "")
            finished["title"] = str(worker.get("title") or finished.get("title") or "")
            finished_workers.append(finished)
            _append_event(
                swarm_id,
                "finisher_completed",
                worker_id=str(worker.get("worker_id") or ""),
                status=str((finished.get("finisher_attempt") or {}).get("status") or finished.get("status") or ""),
            )
            _record_swarm_progress(
                swarm_id,
                phase="finishers",
                detail="running_finishers",
                note=f"Finisher finished {str(worker.get('worker_id') or '')} with {str((finished.get('finisher_attempt') or {}).get('status') or finished.get('status') or '')}.",
                deadline_utc="",
                event_name="finisher_completed",
            )
        workers = sorted(finished_workers, key=_worker_sort_key)

    with _swarm_lock(swarm_id):
        state = _enforce_swarm_runtime_state(swarm_id, phase="integration")
        state["workers"] = list(workers)
        state["token_usage"] = _aggregate_swarm_token_usage(
            lead_usage=((state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}) or {}).get("lead_token_usage"),
            workers=workers,
        )
        if _swarm_tokens_over_cap(state=state):
            cap = int(((state.get("config") if isinstance(state.get("config"), dict) else {}) or {}).get("max_total_tokens") or 0)
            total = int((state.get("token_usage") if isinstance(state.get("token_usage"), dict) else {}).get("total_tokens") or 0)
            state = _persist_state(state)
            raise AgentSwarmError(f"swarm used {total} total tokens which exceeds max_total_tokens={cap}")
        state["phase"] = "integration"
        state = _persist_state(state)

    remaining_budget_s = _remaining_budget_s(state)
    _record_swarm_progress(
        swarm_id,
        phase="integration",
        detail="running_integration",
        note="Lead is integrating worker patches.",
        deadline_utc=(
            _deadline_utc(_utc_iso(), max(1, int(remaining_budget_s)))
            if remaining_budget_s is not None and remaining_budget_s > 0
            else ""
        ),
        event_name="integration_started",
    )
    integration = _run_integration(
        swarm_id=swarm_id,
        base_ref=base_ref,
        overlay=overlay,
        workers=workers,
        artifacts_root=artifacts_root,
    )
    summary_path = (artifacts_root / "summary.md").resolve()

    with _swarm_lock(swarm_id):
        state = _load_state(swarm_id)
        state["integration"] = integration
        state["workers"] = list(sorted(workers, key=_worker_sort_key))
        state["artifacts"]["summary_path"] = str(summary_path)
        state["token_usage"] = _aggregate_swarm_token_usage(
            lead_usage=((state.get("artifacts") if isinstance(state.get("artifacts"), dict) else {}) or {}).get("lead_token_usage"),
            workers=list(state.get("workers") or []),
        )
        state["known_gaps"] = []
        for worker in list(state.get("workers") or []):
            for gap in list(worker.get("known_gaps") or []):
                if gap not in state["known_gaps"]:
                    state["known_gaps"].append(gap)
        if list(integration.get("failed") or []):
            state["known_gaps"].append("Some worker patches did not apply cleanly in the integration worktree.")
        integrated_changes = bool(
            integration.get("combined_patch_has_changes")
            or list(integration.get("changed_files") or [])
        )
        if not integrated_changes:
            state["known_gaps"].append("Integration produced no code changes from worker output.")
        worker_rows = list(state.get("workers") or [])
        worker_statuses = [str(w.get("status") or "").strip().lower() for w in worker_rows]
        exact_delivered = [w for w in worker_rows if _worker_claims_clean_delivery(w)]
        any_delivery = [w for w in worker_rows if _worker_claims_any_delivery(w)]
        semantic_failures = [status for status in worker_statuses if status in {"failed_with_evidence", "blocked_need_master_decision", "split_needed"}]
        semantic_partials = [
            w for w in worker_rows
            if str(w.get("status") or "").strip().lower() == "delivered_with_known_gaps" or list(w.get("known_gaps") or [])
        ]
        if not integrated_changes or not any_delivery:
            state["status"] = "failed"
        elif list(integration.get("failed") or []) or semantic_failures or semantic_partials:
            state["status"] = "partial"
        elif exact_delivered and len(exact_delivered) == len(worker_rows):
            state["status"] = "finished"
        else:
            state["status"] = "partial"
        state["phase"] = "completed"
        state["finished_utc"] = _utc_iso()
        state["status_detail"] = "completed"
        state["active_progress_note"] = ""
        state["active_slice_deadline_utc"] = ""
        state = _persist_state(state)
        _write_summary_markdown(state=state, path=summary_path)
        _append_event(swarm_id, "swarm_completed", status=str(state.get("status") or ""))


def _run_swarm_thread(swarm_id: str) -> None:
    try:
        _execute_swarm(swarm_id)
    except SwarmRuntimeStop:
        return
    except Exception as exc:
        with _swarm_lock(swarm_id):
            state = _load_state(swarm_id)
            state["status"] = "failed"
            state["phase"] = "failed"
            state["finished_utc"] = _utc_iso()
            state.setdefault("known_gaps", [])
            state["known_gaps"] = list(state.get("known_gaps") or []) + [str(exc)]
            state["last_error"] = str(exc)
            _persist_state(state)
        _append_event(swarm_id, "swarm_failed", error=str(exc), traceback=traceback.format_exc())
    finally:
        _register_thread(swarm_id, None)
        _cleanup_swarm_worktrees(swarm_id)


def start_swarm(
    *,
    binding: Dict[str, Any],
    objective: str,
    options: Optional[Dict[str, Any]] = None,
    run_async: bool = True,
) -> Dict[str, Any]:
    opts = dict(options or {})
    b = _validate_binding(binding)
    obj = str(objective or "").strip()
    if not obj:
        raise AgentSwarmError("objective is required")
    defaults = _swarm_defaults()
    wall_clock_s = int(opts.get("wall_clock_s") or defaults["wall_clock_s"])
    maker_lease_s = int(opts.get("maker_lease_s") or defaults["maker_lease_s"])
    finisher_lease_s = int(opts.get("finisher_lease_s") or defaults["finisher_lease_s"])
    max_makers = int(opts.get("max_makers") or defaults["max_makers"])
    max_finishers = int(opts.get("max_finishers") or defaults["max_finishers"])
    max_total_tokens = int(opts.get("max_total_tokens") or defaults["max_total_tokens"])
    started_by_role = str(opts.get("started_by_role") or "generalist").strip().lower() or "generalist"
    swarm_id = _new_swarm_id(b, obj)
    created_utc = _utc_iso()
    state = _ensure_state(
        {
            "swarm_id": swarm_id,
            "binding": b,
            "objective": obj,
            "status": "queued",
            "phase": "queued",
            "created_utc": created_utc,
            "updated_utc": created_utc,
            "deadline_utc": _deadline_utc(created_utc, wall_clock_s),
            "started_by_role": started_by_role,
            "config": {
                "wall_clock_s": int(wall_clock_s),
                "maker_lease_s": int(maker_lease_s),
                "finisher_lease_s": int(finisher_lease_s),
                "max_makers": int(max_makers),
                "max_finishers": int(max_finishers),
                "max_total_tokens": int(max_total_tokens),
            },
            "runner_mode": ("thread" if run_async else "inline"),
            "runner_pid": int(os.getpid()),
        }
    )
    with _swarm_lock(swarm_id):
        _persist_state(state)
        _append_event(swarm_id, "swarm_started", binding=b, started_by_role=started_by_role)
    if run_async:
        thread = threading.Thread(target=_run_swarm_thread, args=(swarm_id,), daemon=True, name=f"agent-swarm-{swarm_id}")
        thread.start()
        _register_thread(swarm_id, thread)
    else:
        _run_swarm_thread(swarm_id)
    return {"ok": True, "swarm_id": swarm_id, "state": _load_state(swarm_id)}


def get_status(*, swarm_id: str, events_limit: int = 120) -> Dict[str, Any]:
    sid = str(swarm_id or "").strip()
    if not sid:
        raise AgentSwarmError("swarm_id is required")
    if not _state_file(sid).exists():
        raise FileNotFoundError("swarm not found")
    state = _refresh_swarm_runtime_state(sid)
    thread_alive = _thread_alive(sid)
    runner_alive = _runner_alive(sid, state)
    return {
        "ok": True,
        "swarm_id": sid,
        "state": {
            **state,
            "runner_state": ("attached" if runner_alive else "detached"),
        },
        "events": _read_jsonl(_events_file(sid), limit=max(1, int(events_limit))),
        "thread_alive": thread_alive,
    }


def list_swarms(*, binding: Optional[Dict[str, Any]] = None, limit: int = 100) -> Dict[str, Any]:
    filt: Optional[Dict[str, str]] = None
    if binding is not None:
        filt = _validate_binding(binding)
    rows: List[Dict[str, Any]] = []
    for path in sorted(_runtime_root().glob("swarm__*.json")):
        state = _ensure_state(_read_json(path))
        sid = str(state.get("swarm_id") or "").strip()
        if not sid:
            continue
        state = _refresh_swarm_runtime_state(sid)
        thread_alive = _thread_alive(sid)
        if filt is not None:
            state_binding = state.get("binding") if isinstance(state.get("binding"), dict) else {}
            if str(state_binding.get("type") or "") != filt["type"] or str(state_binding.get("id") or "") != filt["id"]:
                continue
        rows.append(
            {
                "swarm_id": sid,
                "binding": dict(state.get("binding") or {}),
                "objective": str(state.get("objective") or ""),
                "status": str(state.get("status") or ""),
                "phase": str(state.get("phase") or ""),
                "updated_utc": str(state.get("updated_utc") or ""),
                "deadline_utc": str(state.get("deadline_utc") or ""),
                "thread_alive": thread_alive,
                "runner_state": ("attached" if thread_alive else "detached"),
            }
        )
    rows.sort(key=lambda row: str(row.get("updated_utc") or ""), reverse=True)
    return {"ok": True, "items": rows[: max(1, int(limit))]}


def cancel_swarm(*, swarm_id: str, reason: str = "") -> Dict[str, Any]:
    sid = str(swarm_id or "").strip()
    if not sid:
        raise AgentSwarmError("swarm_id is required")
    with _swarm_lock(sid):
        state = _load_state(sid)
        if not state:
            raise FileNotFoundError("swarm not found")
        state["cancel_requested"] = True
        state["cancel_reason"] = str(reason or "").strip() or "cancelled"
        if str(state.get("status") or "").strip().lower() == "queued":
            state["status"] = "cancelled"
            state["phase"] = "cancelled"
            state["finished_utc"] = _utc_iso()
        _persist_state(state)
    _append_event(sid, "swarm_cancel_requested", reason=str(reason or "").strip() or "cancelled")
    return {"ok": True, "swarm_id": sid, "state": _load_state(sid)}
