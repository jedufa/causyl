"""Benchmark harness for comparing workflow and swarm executions.

This module runs both contenders against the same immutable git ref in
disposable worktrees, captures runtime artifacts, executes shared post-run
checks, and writes a durable comparison report under runtime/state.
"""

from __future__ import annotations

import argparse
import contextlib
import datetime as _dt
import hashlib
import json
import os
import shlex
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from tools import runtime_policy as _runtime_policy
from infra.webui import server_agents as _agents
from infra.webui import server_agents_orchestrator as _orchestrator
from infra.webui import server_agents_swarm as _swarm
from infra.webui import server_codex_runtime as _codex_runtime


SCHEMA_SPEC = "agent_benchmark_spec_v1"
SCHEMA_SUITE = "agent_benchmark_suite_v1"
SCHEMA_RESULT = "agent_benchmark_result_v1"
SCHEMA_SUITE_RESULT = "agent_benchmark_suite_result_v1"
SCHEMA_JOB = "agent_benchmark_job_v1"
TASK_ID_PREFIX = "task_id::"
LEDGER_UID_PREFIX = "ledger_uid::"
DEFAULT_WORKFLOW_PHASE_LABEL = "Benchmark"
DEFAULT_WORKFLOW_MAX_STEPS = 200
_JOB_THREADS_GUARD = threading.Lock()
_JOB_THREADS: Dict[str, threading.Thread] = {}
_TERMINAL_JOB_STATUSES = {"completed", "completed_with_failures", "failed"}
_BENCHMARK_SANDBOX_RESET_PATHS = (
    "governance/agents/runtime",
    "governance/agents/sessions",
    "runtime/state",
)
_BENCHMARK_EXPORT_DENY_PREFIXES = (
    ".git/",
    ".gradle/",
    "governance/agents/runtime/",
    "governance/agents/sessions/",
    "runtime/state/",
    "infra/mobile/android/.gradle/",
    "infra/mobile/android/app/build/",
    "infra/mobile/android/build/",
)
_BENCHMARK_OVERLAY_DENY_PREFIXES = _BENCHMARK_EXPORT_DENY_PREFIXES
_BENCHMARK_OVERLAY_DENY_EXACT = (
    "commit journal error: no pending commit journal entries and no relevant changed files to reconstruct.message.txt",
    "commit journal error: no pending commit journal entries.message.txt",
)
_TERMINAL_RUNTIME_STATUSES = {"completed", "completed_with_failures", "cancelled", "failed", "interrupted"}
_RUNTIME_SUCCESS_STATUSES = {"completed", "completed_with_failures", "finished", "succeeded", "success"}


class BenchmarkError(RuntimeError):
    """Raised for invalid benchmark configuration or execution."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_root() -> Path:
    root = (_repo_root() / "runtime" / "state" / "agent_benchmarks").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _runs_root() -> Path:
    root = (_runtime_root() / "runs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _jobs_root() -> Path:
    root = (_runtime_root() / "jobs").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _worktrees_root() -> Path:
    root = (_runtime_root() / "worktrees").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _cleanup_root() -> Path:
    root = (_runtime_root() / "cleanup").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _utc_now() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _utc_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return _utc_now().strftime("%Y%m%dT%H%M%SZ")


def _parse_iso_utc(value: str) -> Optional[_dt.datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    _atomic_write_text(path, payload)


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_text(path, str(text or ""))


def _atomic_write_text(path: Path, text: str) -> None:
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=str(target.parent), delete=False) as handle:
        handle.write(text)
        tmp_name = handle.name
    os.replace(tmp_name, target)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    with open(path, "a", encoding="utf-8", newline="\n") as handle:
        handle.write(line)
        handle.write("\n")


def _read_jsonl(path: Path, *, limit: int = 100) -> List[Dict[str, Any]]:
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
        text = str(raw or "").strip()
        if not text:
            continue
        try:
            row = json.loads(text)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _safe_name(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or "").strip())
    cleaned = cleaned.strip("-")
    return cleaned or "x"


def _digest_text(text: str) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()[:8]


def _run_git(args: List[str], *, cwd: Optional[Path] = None, timeout_s: int = 180) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str((cwd or _repo_root()).resolve()),
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_s)),
        check=False,
    )


def _benchmark_worktree_create_timeout_s() -> int:
    """Return the timeout budget for benchmark sandbox worktree creation."""
    return _runtime_policy.get_runtime_policy_int(
        "agents.benchmark.worktree_create_timeout_s",
        90,
        minimum=15,
        maximum=600,
    )


def _resolve_git_ref(git_ref: str) -> str:
    raw = str(git_ref or "").strip() or "HEAD"
    cp = _run_git(["rev-parse", "--verify", raw])
    if cp.returncode != 0:
        raise BenchmarkError(str(cp.stderr or cp.stdout or f"git rev-parse failed for {raw}").strip())
    return str(cp.stdout or "").strip()


def _configure_local_codex_env_defaults(*, repo_root: Path, force_turn_cmd: bool = False) -> None:
    """Mirror the server's Codex fallback env so benchmark children can launch turns."""
    if "PYTEST_CURRENT_TEST" in os.environ:
        return
    codex_bin = shutil.which("codex")
    if not codex_bin:
        fallback = Path("/home/user/.nvm/versions/node/v24.14.0/bin/codex")
        if fallback.exists() and fallback.is_file():
            codex_bin = str(fallback)
    if not codex_bin:
        return
    if not str(os.environ.get("EIEL_AGENTS_BACKEND") or "").strip():
        os.environ["EIEL_AGENTS_BACKEND"] = "codex"
    if not str(os.environ.get("EIEL_CODEX_PROTOCOL") or "").strip():
        os.environ["EIEL_CODEX_PROTOCOL"] = "oneshot"
    turn_cmd = str(os.environ.get("EIEL_CODEX_TURN_CMD") or "").strip()
    if force_turn_cmd or not turn_cmd:
        quoted_repo_root = shlex.quote(str(repo_root))
        if str(os.environ.get("EIEL_CODEX_UNRESTRICTED") or "1").strip() == "1":
            os.environ["EIEL_CODEX_TURN_CMD"] = (
                f"{codex_bin} --dangerously-bypass-approvals-and-sandbox exec -C {quoted_repo_root} --skip-git-repo-check -"
            )
        else:
            os.environ["EIEL_CODEX_TURN_CMD"] = (
                f"{codex_bin} -a never -s workspace-write exec -C {quoted_repo_root} --skip-git-repo-check -"
            )


def _new_run_id(spec: Dict[str, Any], git_sha: str) -> str:
    label = str(spec.get("label") or spec.get("objective") or "benchmark").strip()
    return f"bench_{_utc_compact().lower()}_{_digest_text(label + git_sha)}"


def _new_job_id(label: str) -> str:
    return f"benchjob_{_utc_compact().lower()}_{_digest_text(label)}"


def _binding_key(binding: Dict[str, str]) -> str:
    btype = str(binding.get("type") or "").strip()
    bid = str(binding.get("id") or "").strip()
    if btype == "task_id":
        return f"{TASK_ID_PREFIX}{bid}"
    return f"{LEDGER_UID_PREFIX}{bid}"


def _validate_binding(raw: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(raw, dict):
        raise BenchmarkError("binding must be an object")
    btype = str(raw.get("type") or "").strip()
    bid = str(raw.get("id") or "").strip()
    if btype not in {"task_id", "ledger_uid"}:
        raise BenchmarkError("binding.type must be task_id|ledger_uid")
    if not bid:
        raise BenchmarkError("binding.id is required")
    return {"type": btype, "id": bid}


def _validate_command_list(raw: Any) -> List[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise BenchmarkError("check_commands must be a list of shell command strings")
    commands: List[str] = []
    for item in raw:
        cmd = str(item or "").strip()
        if cmd:
            commands.append(cmd)
    return commands


def _validate_spec(raw: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise BenchmarkError("benchmark spec must be a JSON object")
    binding = _validate_binding(raw.get("binding") if isinstance(raw.get("binding"), dict) else {})
    objective = str(raw.get("objective") or "").strip()
    if not objective:
        raise BenchmarkError("objective is required")
    workflow_raw = raw.get("workflow") if isinstance(raw.get("workflow"), dict) else {}
    swarm_raw = raw.get("swarm") if isinstance(raw.get("swarm"), dict) else {}
    workflow_enabled = bool(workflow_raw.get("enabled", True))
    swarm_enabled = bool(swarm_raw.get("enabled", True))
    if not workflow_enabled and not swarm_enabled:
        raise BenchmarkError("at least one contender must be enabled")
    workflow = {
        "enabled": workflow_enabled,
        "phase_label": str(workflow_raw.get("phase_label") or "Benchmark").strip() or "Benchmark",
        "max_steps": max(1, min(int(workflow_raw.get("max_steps") or 200), 1000)),
        "options": dict(workflow_raw.get("options") or {}),
    }
    swarm = {
        "enabled": swarm_enabled,
        "options": dict(swarm_raw.get("options") or {}),
    }
    return {
        "schema_version": str(raw.get("schema_version") or SCHEMA_SPEC),
        "label": str(raw.get("label") or objective).strip() or objective,
        "git_ref": str(raw.get("git_ref") or "HEAD").strip() or "HEAD",
        "binding": binding,
        "objective": objective,
        "check_commands": _validate_command_list(raw.get("check_commands")),
        "cleanup_worktrees": bool(raw.get("cleanup_worktrees", True)),
        "workflow": workflow,
        "swarm": swarm,
    }


def create_benchmark_spec(
    *,
    objective: str,
    binding: Dict[str, Any],
    label: Optional[str] = None,
    git_ref: str = "HEAD",
    check_commands: Optional[List[str]] = None,
    workflow_enabled: bool = True,
    swarm_enabled: bool = True,
    workflow_options: Optional[Dict[str, Any]] = None,
    swarm_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Build a validated benchmark spec with sane defaults."""
    raw: Dict[str, Any] = {
        "schema_version": SCHEMA_SPEC,
        "label": str(label or objective).strip() or str(objective or "").strip(),
        "git_ref": str(git_ref or "HEAD").strip() or "HEAD",
        "binding": dict(binding or {}),
        "objective": str(objective or "").strip(),
        "check_commands": list(check_commands or []),
        "workflow": {
            "enabled": bool(workflow_enabled),
            "phase_label": DEFAULT_WORKFLOW_PHASE_LABEL,
            "max_steps": DEFAULT_WORKFLOW_MAX_STEPS,
            "options": {},
        },
        "swarm": {
            "enabled": bool(swarm_enabled),
            "options": {},
        },
        # Benchmarks are playground runs. Worktrees are always disposable.
        "cleanup_worktrees": True,
    }
    if isinstance(workflow_options, dict) and workflow_options:
        workflow = raw["workflow"]
        workflow["phase_label"] = str(workflow_options.get("phase_label") or workflow["phase_label"]).strip() or workflow["phase_label"]
        workflow["max_steps"] = workflow_options.get("max_steps", workflow["max_steps"])
        workflow["options"] = dict(workflow_options.get("options") or {})
    if isinstance(swarm_options, dict) and swarm_options:
        raw["swarm"]["options"] = dict(swarm_options)
    return _validate_spec(raw)


def load_benchmark_spec(path: Path) -> Dict[str, Any]:
    """Load and validate a benchmark spec from disk."""
    raw = _read_json(path)
    if not raw:
        raise BenchmarkError(f"benchmark spec not found or invalid JSON: {path}")
    return _validate_spec(raw)


def _validate_suite(raw: Dict[str, Any], *, suite_path: Path) -> Dict[str, Any]:
    if not isinstance(raw, dict):
        raise BenchmarkError("benchmark suite must be a JSON object")
    raw_paths = raw.get("spec_paths")
    if not isinstance(raw_paths, list) or not raw_paths:
        raise BenchmarkError("benchmark suite spec_paths must be a non-empty list")
    resolved_paths: List[str] = []
    labels: List[str] = []
    for item in raw_paths:
        raw_path = str(item or "").strip()
        if not raw_path:
            raise BenchmarkError("benchmark suite spec_paths entries must be non-empty strings")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            repo_relative = (_repo_root() / candidate).resolve()
            suite_relative = (suite_path.parent / candidate).resolve()
            candidate = repo_relative if repo_relative.exists() else suite_relative
        spec = load_benchmark_spec(candidate)
        resolved_paths.append(str(candidate))
        labels.append(str(spec.get("label") or "").strip())
    if len(set(labels)) != len(labels):
        raise BenchmarkError("benchmark suite spec labels must be unique")
    return {
        "schema_version": str(raw.get("schema_version") or SCHEMA_SUITE),
        "label": str(raw.get("label") or suite_path.stem).strip() or suite_path.stem,
        "description": str(raw.get("description") or "").strip(),
        "spec_paths": resolved_paths,
    }


def load_benchmark_suite(path: Path) -> Dict[str, Any]:
    """Load and validate a benchmark suite manifest from disk."""
    raw = _read_json(path)
    if not raw:
        raise BenchmarkError(f"benchmark suite not found or invalid JSON: {path}")
    return _validate_suite(raw, suite_path=path.resolve())


def _create_worktree(*, git_sha: str, run_id: str, mode: str) -> Path:
    leaf = _safe_name(f"{run_id}__{mode}")
    worktree = (_worktrees_root() / leaf).resolve()
    archive_path = (worktree.parent / f".{leaf}.tar").resolve()
    worktree.parent.mkdir(parents=True, exist_ok=True)
    timeout_s = _benchmark_worktree_create_timeout_s()
    archive_pathspecs = ["--", "."]
    for prefix in _BENCHMARK_OVERLAY_DENY_PREFIXES:
        normalized = str(prefix or "").strip().rstrip("/")
        if not normalized:
            continue
        archive_pathspecs.append(f":(exclude){normalized}")
        archive_pathspecs.append(f":(exclude){normalized}/**")
    for exact in _BENCHMARK_OVERLAY_DENY_EXACT:
        normalized = str(exact or "").strip()
        if normalized:
            archive_pathspecs.append(f":(exclude){normalized}")
    try:
        cp = _run_git(
            ["archive", "--format=tar", f"--output={archive_path}", git_sha, *archive_pathspecs],
            timeout_s=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        shutil.rmtree(worktree, ignore_errors=True)
        archive_path.unlink(missing_ok=True)
        raise BenchmarkError(
            f"git archive timed out for {mode} after {int(timeout_s)}s"
        ) from exc
    if cp.returncode != 0:
        shutil.rmtree(worktree, ignore_errors=True)
        archive_path.unlink(missing_ok=True)
        raise BenchmarkError(str(cp.stderr or cp.stdout or f"git archive failed for {mode}").strip())
    worktree.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(archive_path, "r") as handle:
            handle.extractall(worktree)
    except Exception as exc:
        shutil.rmtree(worktree, ignore_errors=True)
        raise BenchmarkError(f"failed to extract benchmark sandbox for {mode}: {_error_text(exc)}") from exc
    finally:
        archive_path.unlink(missing_ok=True)
    init_cp = _run_git(["init", "-q"], cwd=worktree, timeout_s=120)
    if init_cp.returncode != 0:
        shutil.rmtree(worktree, ignore_errors=True)
        raise BenchmarkError(str(init_cp.stderr or init_cp.stdout or f"git init failed for {mode}").strip())
    return worktree


def _capture_repo_overlay() -> Dict[str, Any]:
    pathspecs = ["--"]
    for prefix in _BENCHMARK_OVERLAY_DENY_PREFIXES:
        normalized = str(prefix or "").strip().rstrip("/")
        if not normalized:
            continue
        pathspecs.append(f":(exclude){normalized}")
        pathspecs.append(f":(exclude){normalized}/**")
    for exact in _BENCHMARK_OVERLAY_DENY_EXACT:
        normalized = str(exact or "").strip()
        if normalized:
            pathspecs.append(f":(exclude){normalized}")
    tracked_cp = _run_git(["diff", "--binary", "HEAD", *pathspecs], timeout_s=120)
    if tracked_cp.returncode != 0:
        raise BenchmarkError(str(tracked_cp.stderr or tracked_cp.stdout or "git diff failed").strip())
    untracked_cp = _run_git(["ls-files", "--others", "--exclude-standard"], timeout_s=120)
    if untracked_cp.returncode != 0:
        raise BenchmarkError(str(untracked_cp.stderr or untracked_cp.stdout or "git ls-files failed").strip())
    all_untracked = _relative_repo_paths_from_output(str(untracked_cp.stdout or ""))
    untracked_files = [rel for rel in all_untracked if _should_export_benchmark_path(rel)]
    untracked_skipped_count = max(0, len(all_untracked) - len(untracked_files))
    return {
        "tracked_patch": str(tracked_cp.stdout or ""),
        "tracked_dirty": bool(str(tracked_cp.stdout or "").strip()),
        "untracked_files": untracked_files,
        "untracked_count": len(untracked_files),
        "untracked_skipped_count": int(untracked_skipped_count),
        "note": "Benchmark overlay applies tracked repo diff and relevant untracked repo files, excluding runtime/generated residue.",
    }


def _apply_overlay_to_worktree(*, worktree: Path, overlay: Dict[str, Any]) -> None:
    tracked_patch = str(overlay.get("tracked_patch") or "")
    if tracked_patch.strip():
        patch_path = (worktree / ".benchmark_overlay.patch").resolve()
        _write_text(patch_path, tracked_patch)
        cp = _run_git(["apply", "--binary", "--whitespace=nowarn", str(patch_path)], cwd=worktree, timeout_s=120)
        try:
            patch_path.unlink(missing_ok=True)
        except Exception:
            pass
        if cp.returncode != 0:
            raise BenchmarkError(str(cp.stderr or cp.stdout or "git apply failed").strip())
    for rel in list(overlay.get("untracked_files") or []):
        src = (_repo_root() / str(rel)).resolve()
        dst = (worktree / str(rel)).resolve()
        if not src.exists() or src.is_symlink():
            continue
        _copy_path(src, dst)


def _create_baseline_commit(*, worktree: Path, message: str) -> None:
    add_cp = _run_git(["add", "-A"], cwd=worktree, timeout_s=120)
    if add_cp.returncode != 0:
        raise BenchmarkError(str(add_cp.stderr or add_cp.stdout or "git add failed").strip())
    cp = subprocess.run(
        ["git", "commit", "--allow-empty", "--no-gpg-sign", "-m", message],
        cwd=str(worktree.resolve()),
        capture_output=True,
        text=True,
        timeout=120,
        env={
            **os.environ,
            "GIT_AUTHOR_NAME": "EIEL Benchmark",
            "GIT_AUTHOR_EMAIL": "operator@example.invalid",
            "GIT_COMMITTER_NAME": "EIEL Benchmark",
            "GIT_COMMITTER_EMAIL": "operator@example.invalid",
        },
        check=False,
    )
    if cp.returncode != 0:
        raise BenchmarkError(str(cp.stderr or cp.stdout or "git commit failed").strip())


def _remove_path_any(path: Path) -> bool:
    target = Path(path).resolve()
    if not target.exists() and not target.is_symlink():
        return False
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target, ignore_errors=False)
    else:
        target.unlink(missing_ok=True)
    return True


def _sanitize_benchmark_worktree(worktree: Path) -> Dict[str, Any]:
    """Remove repo-resident runtime residue so contenders start from a clean sandbox."""
    root = Path(worktree).resolve()
    removed: List[str] = []
    errors: List[str] = []
    for rel in _BENCHMARK_SANDBOX_RESET_PATHS:
        target = (root / rel).resolve()
        try:
            if _remove_path_any(target):
                removed.append(rel)
        except Exception as exc:
            errors.append(f"{rel}: {_error_text(exc)}")
    return {"removed": removed, "errors": errors}


def _remove_worktree(path: Path) -> Dict[str, Any]:
    target = Path(path).resolve()
    immediate = _try_remove_worktree_sync(target, timeout_s=60)
    if immediate is not None:
        return immediate
    return _schedule_worktree_cleanup(target)


def _cleanup_slug(path: Path) -> str:
    target = Path(path).resolve()
    return f"{_safe_name(target.name)}_{_digest_text(str(target))}"


def _cleanup_paths(path: Path) -> Dict[str, Path]:
    root = (_cleanup_root() / _cleanup_slug(path)).resolve()
    return {
        "root": root,
        "request": (root / "request.json").resolve(),
        "response": (root / "response.json").resolve(),
        "stdout": (root / "stdout.txt").resolve(),
        "stderr": (root / "stderr.txt").resolve(),
    }


def _cleanup_worktree_now(path: Path) -> Dict[str, Any]:
    target = Path(path).resolve()
    removed_by = "missing"
    errors: List[str] = []
    if target.exists():
        try:
            shutil.rmtree(target)
            removed_by = "filesystem"
        except Exception as exc:
            removed_by = "filesystem"
            errors.append(f"filesystem remove failed: {_error_text(exc)}")
    return {
        "ok": not target.exists(),
        "path": str(target),
        "error": "; ".join(part for part in errors if str(part or "").strip()).strip(),
        "removed_by": removed_by,
        "deferred": False,
    }


def _benchmark_run_status(run_id: str) -> str:
    result_path = (_runs_root() / str(run_id or "").strip() / "result.json").resolve()
    result = _read_json(result_path)
    return str(result.get("status") or "").strip().lower()


def _prune_stale_worktrees(*, min_age_s: int = 300) -> Dict[str, Any]:
    now = time.time()
    removed: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    for candidate in sorted(_worktrees_root().glob("*")):
        if not candidate.is_dir():
            continue
        run_id = candidate.name.split("__", 1)[0].strip()
        status = _benchmark_run_status(run_id)
        age_s = max(0.0, now - float(candidate.stat().st_mtime))
        if status == "running":
            skipped.append({"path": str(candidate), "run_id": run_id, "reason": "running", "age_s": round(age_s, 3)})
            continue
        if age_s < float(max(0, int(min_age_s))):
            skipped.append({"path": str(candidate), "run_id": run_id, "reason": "fresh", "age_s": round(age_s, 3)})
            continue
        cleanup = _cleanup_worktree_now(candidate)
        cleanup["run_id"] = run_id
        cleanup["age_s"] = round(age_s, 3)
        removed.append(cleanup)
    return {"removed": removed, "skipped": skipped}


def _cleanup_worktree_child_main(argv: Optional[List[str]] = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        raise SystemExit("usage: <request_json> <response_json>")
    request_path = Path(str(args[0])).resolve()
    response_path = Path(str(args[1])).resolve()
    request = _read_json(request_path)
    worktree = Path(str(request.get("worktree") or "")).resolve()
    try:
        payload = _cleanup_worktree_now(worktree)
    except BaseException as exc:
        payload = {
            "ok": False,
            "path": str(worktree),
            "error": _error_text(exc),
            "removed_by": "child_failed",
            "deferred": False,
        }
    _write_json(response_path, payload)
    return 0 if bool(payload.get("ok")) else 1


def _start_cleanup_worktree_process(path: Path) -> Dict[str, Any]:
    target = Path(path).resolve()
    files = _cleanup_paths(target)
    _write_json(files["request"], {"worktree": str(target)})
    env = dict(os.environ)
    repo_root = _repo_root()
    existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = str(repo_root) if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    stdout_handle = open(files["stdout"], "w", encoding="utf-8", newline="")
    stderr_handle = open(files["stderr"], "w", encoding="utf-8", newline="")
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "from tools.benchmarks.agent_benchmark import _cleanup_worktree_child_main as _m; raise SystemExit(_m())",
                str(files["request"]),
                str(files["response"]),
            ],
            cwd=str(repo_root),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    return {
        "process": proc,
        "stdout_handle": stdout_handle,
        "stderr_handle": stderr_handle,
        "response_path": files["response"],
        "stdout_path": files["stdout"],
        "stderr_path": files["stderr"],
        "request_path": files["request"],
    }


def _try_remove_worktree_sync(path: Path, *, timeout_s: int) -> Optional[Dict[str, Any]]:
    child = _start_cleanup_worktree_process(path)
    proc = child["process"]
    stdout_handle = child["stdout_handle"]
    stderr_handle = child["stderr_handle"]
    response_path = Path(str(child["response_path"])).resolve()
    stdout_path = Path(str(child["stdout_path"])).resolve()
    stderr_path = Path(str(child["stderr_path"])).resolve()
    try:
        try:
            proc.wait(timeout=max(1, int(timeout_s)))
        except subprocess.TimeoutExpired:
            try:
                os.killpg(int(proc.pid), signal.SIGKILL)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
            return None
        payload = _read_json(response_path)
        if payload:
            return payload
        stderr_tail = _read_last_line(stderr_path)
        error = "cleanup child exited without a result"
        if stderr_tail:
            error = f"{error}; {stderr_tail}"
        return {
            "ok": False,
            "path": str(Path(path).resolve()),
            "error": error,
            "removed_by": "child_missing_result",
            "deferred": False,
        }
    finally:
        stdout_handle.close()
        stderr_handle.close()


def _schedule_worktree_cleanup(path: Path) -> Dict[str, Any]:
    child = _start_cleanup_worktree_process(path)
    proc = child["process"]
    child["stdout_handle"].close()
    child["stderr_handle"].close()
    return {
        "ok": False,
        "path": str(Path(path).resolve()),
        "error": "cleanup deferred after synchronous cleanup timed out",
        "removed_by": "deferred_child",
        "deferred": True,
        "runner_pid": int(proc.pid),
        "request_path": str(child["request_path"]),
        "response_path": str(child["response_path"]),
        "stdout_path": str(child["stdout_path"]),
        "stderr_path": str(child["stderr_path"]),
    }


@contextlib.contextmanager
def _repo_root_override(repo_root: Path) -> Iterator[None]:
    """Temporarily redirect repo-root based modules into a playground worktree."""
    target = Path(repo_root).resolve()
    patches = [
        (_runtime_policy, "_repo_root"),
        (_agents, "_repo_root"),
        (_orchestrator, "_repo_root"),
        (_swarm, "_repo_root"),
        (_codex_runtime, "_repo_root"),
    ]
    saved: List[tuple[object, str, Any]] = []
    saved_manager = getattr(_agents, "_CODEX_MANAGER", None)
    try:
        for module, attr in patches:
            saved.append((module, attr, getattr(module, attr)))
            setattr(module, attr, lambda _target=target: _target)
        _agents._CODEX_MANAGER = None
        getattr(_runtime_policy.load_runtime_policy, "cache_clear", lambda: None)()
        yield
    finally:
        _agents._CODEX_MANAGER = None
        for module, attr, value in reversed(saved):
            setattr(module, attr, value)
        _agents._CODEX_MANAGER = saved_manager
        getattr(_runtime_policy.load_runtime_policy, "cache_clear", lambda: None)()


def _copy_path(src: Path, dst: Path) -> str:
    source = Path(src).resolve()
    if not source.exists():
        return ""
    if source.is_dir():
        shutil.copytree(source, dst, dirs_exist_ok=True)
    else:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, dst)
    return str(dst)


def _relative_repo_paths_from_output(text: str) -> List[str]:
    """Normalize newline-delimited git path output into safe repo-relative paths."""
    out: List[str] = []
    seen: set[str] = set()
    for raw in str(text or "").splitlines():
        rel = str(raw or "").strip()
        if not rel:
            continue
        candidate = Path(rel)
        if candidate.is_absolute():
            continue
        normalized = candidate.as_posix().lstrip("./")
        if not normalized or normalized.startswith("../"):
            continue
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out


def _should_export_benchmark_path(path: str) -> bool:
    rel = Path(str(path or "").strip()).as_posix().lstrip("./")
    if not rel:
        return False
    for prefix in _BENCHMARK_EXPORT_DENY_PREFIXES:
        raw = str(prefix or "").strip()
        if not raw:
            continue
        normalized = raw.rstrip("/")
        if rel == normalized or rel.startswith(f"{normalized}/"):
            return False
    return True


def _render_export_status_text(*, tracked_paths: List[str], deleted_paths: List[str], untracked_paths: List[str]) -> str:
    lines: List[str] = []
    for rel in tracked_paths:
        lines.append(f"M {rel}")
    for rel in deleted_paths:
        lines.append(f"D {rel}")
    for rel in untracked_paths:
        lines.append(f"?? {rel}")
    return ("\n".join(lines).rstrip() + "\n") if lines else ""


def _empty_exported_changes_payload(*, contender_dir: Path) -> Dict[str, Any]:
    """Create durable placeholder export artifacts before inspecting the worktree."""
    export_dir = (contender_dir / "exported_changes").resolve()
    snapshot_root = (export_dir / "files").resolve()
    patch_path = (export_dir / "final_patch.diff").resolve()
    status_path = (export_dir / "git_status.txt").resolve()
    manifest_path = (export_dir / "manifest.json").resolve()
    export_dir.mkdir(parents=True, exist_ok=True)
    snapshot_root.mkdir(parents=True, exist_ok=True)
    _write_text(patch_path, "")
    _write_text(status_path, "")
    manifest = {
        "tracked_changed": [],
        "untracked": [],
        "deleted": [],
        "copied_file_count": 0,
        "copied_files": [],
        "patch_path": str(patch_path),
        "status_path": str(status_path),
        "snapshot_root": str(snapshot_root),
        "errors": [],
    }
    _write_json(manifest_path, manifest)
    return {
        "patch_path": str(patch_path),
        "status_path": str(status_path),
        "manifest_path": str(manifest_path),
        "snapshot_root": str(snapshot_root),
        "copied_file_count": 0,
        "copied_files": [],
        "deleted_files": [],
        "tracked_changed": [],
        "untracked_files": [],
        "errors": [],
    }


def _capture_worktree_exports_safe(*, worktree: Path, contender_dir: Path) -> Dict[str, Any]:
    """Guarantee export placeholder artifacts exist even if capture later fails."""
    payload = _empty_exported_changes_payload(contender_dir=contender_dir)
    try:
        return _capture_worktree_exports(worktree=worktree, contender_dir=contender_dir)
    except BaseException as exc:
        error = f"failed to export contender changes: {_error_text(exc)}"
        manifest_path = Path(str(payload["manifest_path"])).resolve()
        manifest = _read_json(manifest_path)
        errors = list(manifest.get("errors") if isinstance(manifest.get("errors"), list) else [])
        errors.append(error)
        manifest["errors"] = errors
        _write_json(manifest_path, manifest)
        payload["errors"] = errors
        return payload


def _synthesize_swarm_exported_changes_from_runtime(*, payload: Dict[str, Any], contender_dir: Path) -> Dict[str, Any]:
    row = dict(payload if isinstance(payload, dict) else {})
    if str(row.get("mode") or "").strip().lower() != "swarm":
        return row
    exported = row.get("exported_changes") if isinstance(row.get("exported_changes"), dict) else {}
    if bool(_delivery_summary(exported).get("delivered")):
        return row
    copied_runtime = row.get("copied_runtime") if isinstance(row.get("copied_runtime"), dict) else {}
    state_path = Path(str(copied_runtime.get("state_path") or "")).resolve()
    artifacts_root = Path(str(copied_runtime.get("artifacts_root") or "")).resolve()
    state = _read_json(state_path)
    integration = state.get("integration") if isinstance(state.get("integration"), dict) else {}
    changed_files = [str(path or "").strip() for path in list(integration.get("changed_files") or []) if str(path or "").strip()]
    patch_src = (artifacts_root / "integration.patch").resolve()
    if not changed_files and not patch_src.exists():
        return row
    export = dict(exported or _empty_exported_changes_payload(contender_dir=contender_dir))
    patch_path = Path(str(export.get("patch_path") or "")).resolve()
    status_path = Path(str(export.get("status_path") or "")).resolve()
    manifest_path = Path(str(export.get("manifest_path") or "")).resolve()
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict):
        manifest = {}
    if patch_src.exists():
        patch_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(patch_src, patch_path)
    _write_text(status_path, _render_export_status_text(tracked_paths=changed_files, deleted_paths=[], untracked_paths=[]))
    errors = list(manifest.get("errors") if isinstance(manifest.get("errors"), list) else [])
    manifest.update(
        {
            "tracked_changed": list(changed_files),
            "untracked": [],
            "deleted": [],
            "copied_file_count": int(manifest.get("copied_file_count") or 0),
            "copied_files": list(manifest.get("copied_files") if isinstance(manifest.get("copied_files"), list) else []),
            "patch_path": str(patch_path),
            "status_path": str(status_path),
            "snapshot_root": str(export.get("snapshot_root") or ""),
            "errors": errors,
        }
    )
    _write_json(manifest_path, manifest)
    export["patch_path"] = str(patch_path)
    export["status_path"] = str(status_path)
    export["manifest_path"] = str(manifest_path)
    export["tracked_changed"] = list(changed_files)
    export["untracked_files"] = []
    export["deleted_files"] = []
    export["copied_file_count"] = int(manifest.get("copied_file_count") or 0)
    export["copied_files"] = list(manifest.get("copied_files") or [])
    export["errors"] = list(errors)
    row["exported_changes"] = export
    return row


def _capture_worktree_exports(*, worktree: Path, contender_dir: Path) -> Dict[str, Any]:
    """Export final contender changes before tearing down the disposable worktree."""
    payload = _empty_exported_changes_payload(contender_dir=contender_dir)
    export_dir = (contender_dir / "exported_changes").resolve()
    snapshot_root = Path(str(payload["snapshot_root"])).resolve()
    patch_path = Path(str(payload["patch_path"])).resolve()
    status_path = Path(str(payload["status_path"])).resolve()
    manifest_path = Path(str(payload["manifest_path"])).resolve()
    errors: List[str] = []

    def _capture_git(args: List[str], *, label: str) -> subprocess.CompletedProcess[str]:
        try:
            cp = _run_git(args, cwd=worktree, timeout_s=120)
        except BaseException as exc:
            errors.append(f"{label}: {_error_text(exc)}")
            return subprocess.CompletedProcess(["git", *args], 1, stdout="", stderr=_error_text(exc))
        if cp.returncode != 0:
            message = str(cp.stderr or cp.stdout or f"git {' '.join(args)} failed").strip()
            if message:
                errors.append(f"{label}: {message}")
        return cp

    tracked_cp = _capture_git(["diff", "--name-only", "HEAD", "--diff-filter=ACMRTUXB", "--"], label="tracked diff")
    deleted_cp = _capture_git(["diff", "--name-only", "HEAD", "--diff-filter=D", "--"], label="deleted diff")
    untracked_cp = _capture_git(["ls-files", "--others", "--exclude-standard"], label="untracked listing")

    tracked_paths = [rel for rel in _relative_repo_paths_from_output(str(tracked_cp.stdout or "")) if _should_export_benchmark_path(rel)]
    deleted_paths = [rel for rel in _relative_repo_paths_from_output(str(deleted_cp.stdout or "")) if _should_export_benchmark_path(rel)]
    untracked_paths = [rel for rel in _relative_repo_paths_from_output(str(untracked_cp.stdout or "")) if _should_export_benchmark_path(rel)]

    _write_text(status_path, _render_export_status_text(tracked_paths=tracked_paths, deleted_paths=deleted_paths, untracked_paths=untracked_paths))

    patch_paths = list(dict.fromkeys(tracked_paths + deleted_paths))
    if patch_paths:
        patch_cp = _capture_git(["diff", "--binary", "HEAD", "--", *patch_paths], label="final patch")
        patch_text = str(patch_cp.stdout or "")
    else:
        patch_cp = subprocess.CompletedProcess(["git", "diff"], 0, stdout="", stderr="")
        patch_text = ""
    _write_text(patch_path, patch_text)

    copied_paths: List[str] = []
    for rel in tracked_paths + untracked_paths:
        src = (worktree / rel).resolve()
        if not src.exists() and not src.is_symlink():
            continue
        try:
            copied = _copy_path(src, snapshot_root / rel)
        except BaseException as exc:
            errors.append(f"snapshot copy failed for {rel}: {_error_text(exc)}")
            continue
        if copied:
            copied_paths.append(rel)

    manifest = {
        "tracked_changed": tracked_paths,
        "untracked": untracked_paths,
        "deleted": deleted_paths,
        "copied_file_count": len(copied_paths),
        "copied_files": copied_paths,
        "patch_path": str(patch_path),
        "status_path": str(status_path),
        "snapshot_root": str(snapshot_root),
        "errors": list(errors),
    }
    _write_json(manifest_path, manifest)
    return {
        "patch_path": str(patch_path),
        "status_path": manifest["status_path"],
        "manifest_path": str(manifest_path),
        "snapshot_root": str(snapshot_root),
        "copied_file_count": int(manifest["copied_file_count"]),
        "copied_files": list(copied_paths),
        "deleted_files": list(deleted_paths),
        "tracked_changed": list(tracked_paths),
        "untracked_files": list(untracked_paths),
        "errors": list(manifest["errors"]),
    }


def _run_shell_command(*, command: str, cwd: Path, output_dir: Path, index: int) -> Dict[str, Any]:
    started = time.monotonic()
    cp = subprocess.run(
        ["bash", "-lc", command],
        cwd=str(cwd.resolve()),
        capture_output=True,
        text=True,
        timeout=max(1, int(_runtime_policy.get_runtime_policy_int("agents.swarm.wall_clock_s", 3600, minimum=60, maximum=7200))),
        check=False,
    )
    elapsed_s = round(time.monotonic() - started, 3)
    stdout_path = (output_dir / f"check_{index:02d}.stdout.txt").resolve()
    stderr_path = (output_dir / f"check_{index:02d}.stderr.txt").resolve()
    _write_text(stdout_path, str(cp.stdout or ""))
    _write_text(stderr_path, str(cp.stderr or ""))
    return {
        "index": int(index),
        "command": command,
        "ok": bool(cp.returncode == 0),
        "exit_code": int(cp.returncode),
        "elapsed_s": elapsed_s,
        "stdout_path": str(stdout_path),
        "stderr_path": str(stderr_path),
    }


def _run_check_commands(*, commands: List[str], cwd: Path, output_dir: Path) -> Dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = [_run_shell_command(command=cmd, cwd=cwd, output_dir=output_dir, index=i + 1) for i, cmd in enumerate(commands)]
    passed = sum(1 for row in rows if bool(row.get("ok")))
    failed = sum(1 for row in rows if not bool(row.get("ok")))
    return {"rows": rows, "summary": {"total": len(rows), "passed": passed, "failed": failed}}


def _failed_checks(*, message: str) -> Dict[str, Any]:
    return {
        "rows": [],
        "summary": {"total": 0, "passed": 0, "failed": 0},
        "skipped": True,
        "error": str(message or "").strip(),
    }


def _workflow_runtime_paths(repo_root: Path, workflow_id: str) -> Dict[str, str]:
    with _repo_root_override(repo_root):
        return {
            "state_path": str(_orchestrator._state_file(workflow_id)),
            "events_path": str(_orchestrator._events_file(workflow_id)),
            "artifacts_root": str(_orchestrator._artifacts_root(workflow_id)),
        }


def _swarm_runtime_paths(repo_root: Path, swarm_id: str) -> Dict[str, str]:
    with _repo_root_override(repo_root):
        return {
            "state_path": str(_swarm._state_file(swarm_id)),
            "events_path": str(_swarm._events_file(swarm_id)),
            "artifacts_root": str(_swarm._artifacts_root(swarm_id)),
        }


def _execute_workflow(*, repo_root: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    with _repo_root_override(repo_root):
        started = time.monotonic()
        started_payload = _orchestrator.start_workflow(
            binding=spec["binding"],
            phase_label=str(spec["workflow"]["phase_label"]),
            objective=str(spec["objective"]),
            options=dict(spec["workflow"]["options"]),
        )
        workflow_id = str(started_payload.get("workflow_id") or "")
        result = _orchestrator.run_workflow(workflow_id=workflow_id, max_steps=int(spec["workflow"]["max_steps"]))
        elapsed_s = round(time.monotonic() - started, 3)
        status = _orchestrator.get_status(workflow_id=workflow_id, events_limit=2000)
    return {
        "mode": "workflow",
        "runtime_id": workflow_id,
        "ok": bool(result.get("ok")),
        "elapsed_s": elapsed_s,
        "result": result,
        "state": status.get("state") if isinstance(status.get("state"), dict) else {},
        "events": status.get("events") if isinstance(status.get("events"), list) else [],
        "runtime_paths": _workflow_runtime_paths(repo_root, workflow_id),
    }


def _execute_swarm(*, repo_root: Path, spec: Dict[str, Any]) -> Dict[str, Any]:
    with _repo_root_override(repo_root):
        started = time.monotonic()
        result = _swarm.start_swarm(
            binding=spec["binding"],
            objective=str(spec["objective"]),
            options=dict(spec["swarm"]["options"]),
            run_async=False,
        )
        elapsed_s = round(time.monotonic() - started, 3)
        swarm_id = str(result.get("swarm_id") or "")
        status = _swarm.get_status(swarm_id=swarm_id, events_limit=2000)
    return {
        "mode": "swarm",
        "runtime_id": swarm_id,
        "ok": bool(result.get("ok")),
        "elapsed_s": elapsed_s,
        "result": result,
        "state": status.get("state") if isinstance(status.get("state"), dict) else {},
        "events": status.get("events") if isinstance(status.get("events"), list) else [],
        "runtime_paths": _swarm_runtime_paths(repo_root, swarm_id),
    }


def _copy_runtime_artifacts(*, contender_dir: Path, runtime_paths: Dict[str, str]) -> Dict[str, str]:
    copied: Dict[str, str] = {}
    state_raw = str(runtime_paths.get("state_path") or "").strip()
    events_raw = str(runtime_paths.get("events_path") or "").strip()
    artifacts_raw = str(runtime_paths.get("artifacts_root") or "").strip()
    copied["state_path"] = _copy_path(Path(state_raw), contender_dir / "runtime_state.json") if state_raw else ""
    copied["events_path"] = _copy_path(Path(events_raw), contender_dir / "runtime_events.jsonl") if events_raw else ""
    copied["artifacts_root"] = _copy_path(Path(artifacts_raw), contender_dir / "artifacts") if artifacts_raw else ""
    return copied


def _parse_event_utc(row: Dict[str, Any]) -> Optional[_dt.datetime]:
    raw = str(row.get("utc") or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _time_to_first_progress_s(events: List[Dict[str, Any]], *, started_utc: str) -> Optional[float]:
    if not events:
        return None
    try:
        start = _dt.datetime.fromisoformat(str(started_utc).replace("Z", "+00:00"))
    except Exception:
        return None
    for row in events:
        event_type = str(row.get("event") or "").strip().lower()
        if event_type.endswith("started") or event_type.endswith("running") or event_type.endswith("done"):
            event_dt = _parse_event_utc(row)
            if event_dt is not None:
                return round((event_dt - start).total_seconds(), 3)
    return None


def _workflow_metrics(contender: Dict[str, Any], checks: Dict[str, Any]) -> Dict[str, Any]:
    state = contender.get("state") if isinstance(contender.get("state"), dict) else {}
    events = contender.get("events") if isinstance(contender.get("events"), list) else []
    host_results = state.get("host_verification_results") if isinstance(state.get("host_verification_results"), list) else []
    reports = state.get("reports") if isinstance(state.get("reports"), list) else []
    return {
        "status": str(state.get("status") or ""),
        "turn": str(state.get("turn") or ""),
        "elapsed_s": float(contender.get("elapsed_s") or 0.0),
        "event_count": len(events),
        "time_to_first_progress_s": _time_to_first_progress_s(events, started_utc=str(state.get("created_utc") or "")),
        "last_error": str(state.get("last_error") or ""),
        "verifier_verdict": str(state.get("verifier_verdict") or ""),
        "reports_count": len(reports),
        "host_verification_count": len(host_results),
        "checks_passed": int((checks.get("summary") or {}).get("passed") or 0),
        "checks_failed": int((checks.get("summary") or {}).get("failed") or 0),
    }


def _swarm_metrics(contender: Dict[str, Any], checks: Dict[str, Any]) -> Dict[str, Any]:
    state = contender.get("state") if isinstance(contender.get("state"), dict) else {}
    events = contender.get("events") if isinstance(contender.get("events"), list) else []
    workers = state.get("workers") if isinstance(state.get("workers"), list) else []
    integration = state.get("integration") if isinstance(state.get("integration"), dict) else {}
    delivered = sum(1 for row in workers if str((row or {}).get("status") or "").startswith("delivered"))
    return {
        "status": str(state.get("status") or ""),
        "phase": str(state.get("phase") or ""),
        "elapsed_s": float(contender.get("elapsed_s") or 0.0),
        "event_count": len(events),
        "time_to_first_progress_s": _time_to_first_progress_s(events, started_utc=str(state.get("created_utc") or "")),
        "known_gaps_count": len(state.get("known_gaps") if isinstance(state.get("known_gaps"), list) else []),
        "worker_count": len(workers),
        "delivered_workers": delivered,
        "integration_failed_count": len(integration.get("failed") if isinstance(integration.get("failed"), list) else []),
        "checks_passed": int((checks.get("summary") or {}).get("passed") or 0),
        "checks_failed": int((checks.get("summary") or {}).get("failed") or 0),
    }


def _failed_contender_payload(*, mode: str, error: str, runtime_id: str = "", checks: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    check_payload = checks if isinstance(checks, dict) else _failed_checks(message="contender did not complete")
    return {
        "mode": mode,
        "ok": False,
        "runtime_id": str(runtime_id or ""),
        "error": str(error or "").strip() or f"{mode} benchmark failed",
        "metrics": {
            "status": "benchmark_failed",
            "elapsed_s": 0.0,
            "event_count": 0,
            "time_to_first_progress_s": None,
            "checks_passed": int((check_payload.get("summary") or {}).get("passed") or 0),
            "checks_failed": int((check_payload.get("summary") or {}).get("failed") or 0),
        },
        "checks": check_payload,
        "copied_runtime": {"state_path": "", "events_path": "", "artifacts_root": ""},
    }


def _path_file_size(path_text: str) -> int:
    """Return the file size for a persisted path, or zero when unavailable."""
    path_raw = str(path_text or "").strip()
    if not path_raw:
        return 0
    try:
        return int(Path(path_raw).stat().st_size)
    except Exception:
        return 0


def _delivery_summary(exported_changes: Dict[str, Any]) -> Dict[str, Any]:
    """Summarize whether a contender actually exported reusable code changes."""
    row = exported_changes if isinstance(exported_changes, dict) else {}
    tracked_changed = row.get("tracked_changed") if isinstance(row.get("tracked_changed"), list) else []
    untracked_files = row.get("untracked_files") if isinstance(row.get("untracked_files"), list) else []
    deleted_files = row.get("deleted_files") if isinstance(row.get("deleted_files"), list) else []
    copied_files = row.get("copied_files") if isinstance(row.get("copied_files"), list) else []
    patch_bytes = _path_file_size(str(row.get("patch_path") or ""))
    changed_path_count = len(tracked_changed) + len(untracked_files) + len(deleted_files)
    copied_file_count = max(int(row.get("copied_file_count") or 0), len(copied_files))
    delivered = bool(patch_bytes > 0 or changed_path_count > 0 or copied_file_count > 0)
    return {
        "delivered": delivered,
        "patch_bytes": int(patch_bytes),
        "patch_nonempty": bool(patch_bytes > 0),
        "changed_path_count": int(changed_path_count),
        "copied_file_count": int(copied_file_count),
    }


def _runtime_outcome_summary(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Derive contender runtime truth from persisted runtime state when available."""
    row = dict(payload if isinstance(payload, dict) else {})
    state: Dict[str, Any] = {}
    state_source = ""
    if isinstance(row.get("state"), dict):
        state = dict(row.get("state") or {})
        state_source = "payload.state"
    if not state:
        copied_runtime = row.get("copied_runtime") if isinstance(row.get("copied_runtime"), dict) else {}
        state_path = Path(str(copied_runtime.get("state_path") or "")).resolve()
        read_state = _read_json(state_path)
        if read_state:
            state = read_state
            state_source = "copied_runtime.state_path"
    status = str(state.get("status") or "").strip().lower()
    if not status:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        status = str(metrics.get("status") or "").strip().lower()
    launch_ok = bool(row.get("ok"))
    if status in _TERMINAL_RUNTIME_STATUSES:
        runtime_ok = bool(status in _RUNTIME_SUCCESS_STATUSES)
        source = f"persisted_terminal:{state_source or 'metrics.status'}"
    elif status:
        runtime_ok = launch_ok
        source = "launch_ack_with_nonterminal_status"
    else:
        runtime_ok = launch_ok
        source = "launch_ack_only"
    return {
        "runtime_ok": bool(runtime_ok),
        "runtime_status": status,
        "source": source,
    }


def _comparison_for_contender(row: Dict[str, Any]) -> Dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    delivery = row.get("delivery") if isinstance(row.get("delivery"), dict) else {}
    runtime_ok = bool(row.get("runtime_ok", row.get("ok")))
    delivered = bool(delivery.get("delivered", metrics.get("delivered_code")))
    checks_failed = int(metrics.get("checks_failed") or 0)
    elapsed_s = float(metrics.get("elapsed_s") or 0.0)
    runtime_points = 2 if runtime_ok else 0
    delivery_points = 1 if delivered else 0
    score = runtime_points + delivery_points - checks_failed
    return {
        "score": int(score),
        "runtime_points": int(runtime_points),
        "delivery_points": int(delivery_points),
        "checks_penalty": int(checks_failed),
        "runtime_ok": runtime_ok,
        "delivered_code": delivered,
        "checks_failed": checks_failed,
        "elapsed_s": elapsed_s,
    }


def _comparison_summary(contenders: Dict[str, Any]) -> Dict[str, Any]:
    rows: Dict[str, Dict[str, Any]] = {}
    for mode, raw in contenders.items():
        row = raw if isinstance(raw, dict) else {}
        if not row:
            continue
        rows[str(mode)] = _comparison_for_contender(row)
    ranked_modes = sorted(
        rows.keys(),
        key=lambda mode: (
            -int(rows[mode]["score"]),
            -int(bool(rows[mode]["runtime_ok"])),
            -int(bool(rows[mode]["delivered_code"])),
            int(rows[mode]["checks_failed"]),
            float(rows[mode]["elapsed_s"]),
            str(mode),
        ),
    )
    winner = ""
    tie = False
    if ranked_modes:
        winner = ranked_modes[0]
        if len(ranked_modes) > 1:
            top = rows[ranked_modes[0]]
            second = rows[ranked_modes[1]]
            tie = (
                int(top["score"]) == int(second["score"])
                and bool(top["runtime_ok"]) == bool(second["runtime_ok"])
                and bool(top["delivered_code"]) == bool(second["delivered_code"])
                and int(top["checks_failed"]) == int(second["checks_failed"])
            )
            if tie:
                winner = ""
    return {
        "winner": winner,
        "tie": tie,
        "ranked_modes": ranked_modes,
        "contenders": rows,
        "scoring": {
            "formula": "score = runtime_points(2 if runtime_ok else 0) + delivery_points(1 if delivered_code else 0) - checks_failed"
        },
    }


def _annotate_contender_outcome(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Attach delivery-aware benchmark fields to a persisted contender payload."""
    row = dict(payload if isinstance(payload, dict) else {})
    metrics = dict(row.get("metrics") if isinstance(row.get("metrics"), dict) else {})
    runtime = _runtime_outcome_summary(row)
    runtime_ok = bool(runtime.get("runtime_ok"))
    delivery = _delivery_summary(row.get("exported_changes") if isinstance(row.get("exported_changes"), dict) else {})
    metrics["delivery_changed_paths"] = int(delivery["changed_path_count"])
    metrics["delivery_copied_files"] = int(delivery["copied_file_count"])
    metrics["delivery_patch_bytes"] = int(delivery["patch_bytes"])
    metrics["delivered_code"] = bool(delivery["delivered"])
    metrics["runtime_status"] = str(runtime.get("runtime_status") or "")
    metrics["runtime_outcome_source"] = str(runtime.get("source") or "")
    row["metrics"] = metrics
    row["runtime_ok"] = runtime_ok
    row["delivery"] = delivery
    row["ok"] = runtime_ok
    return row


def _contender_placeholder(*, mode: str, error: str = "") -> Dict[str, Any]:
    """Return an immediately persisted placeholder so contender dirs are never empty."""
    payload = _failed_contender_payload(mode=mode, error=error or f"{mode} benchmark pending")
    payload["metrics"]["status"] = "pending"
    return payload


def _error_text(exc: BaseException) -> str:
    text = str(exc or "").strip()
    return text or exc.__class__.__name__


def _job_file(job_id: str) -> Path:
    return (_jobs_root() / f"benchmark__{_safe_name(job_id)}.json").resolve()


def _job_events_file(job_id: str) -> Path:
    return (_jobs_root() / f"benchmark__{_safe_name(job_id)}.events.jsonl").resolve()


def _job_request_file(job_id: str) -> Path:
    return (_jobs_root() / f"benchmark__{_safe_name(job_id)}.request.json").resolve()


def _job_response_file(job_id: str) -> Path:
    return (_jobs_root() / f"benchmark__{_safe_name(job_id)}.response.json").resolve()


def _job_probe_file(job_id: str) -> Path:
    return (_jobs_root() / f"benchmark__{_safe_name(job_id)}.probe.json").resolve()


def _job_stdout_file(job_id: str) -> Path:
    return (_jobs_root() / f"benchmark__{_safe_name(job_id)}.stdout.txt").resolve()


def _job_stderr_file(job_id: str) -> Path:
    return (_jobs_root() / f"benchmark__{_safe_name(job_id)}.stderr.txt").resolve()


def _recover_missing_job_state(job_id: str) -> Dict[str, Any]:
    request = _read_json(_job_request_file(job_id))
    response = _read_json(_job_response_file(job_id))
    probe = _read_json(_job_probe_file(job_id))
    spec = request.get("spec") if isinstance(request.get("spec"), dict) else {}
    binding = dict(spec.get("binding") or {})
    state: Dict[str, Any] = {
        "schema_version": SCHEMA_JOB,
        "benchmark_id": job_id,
        "status": ("running" if request else "failed"),
        "created_utc": _utc_iso(),
        "label": str(spec.get("label") or spec.get("objective") or job_id).strip() or job_id,
        "objective": str(spec.get("objective") or "").strip(),
        "git_ref": str(spec.get("git_ref") or "HEAD").strip() or "HEAD",
        "binding": binding,
        "binding_key": (_binding_key(binding) if binding else ""),
        "result_path": "",
        "summary_path": "",
        "run_id": "",
        "thread_alive": False,
        "runner_pid": 0,
        "runner_alive": False,
        "runner_timeout_s": _benchmark_job_timeout_s(),
        "runner_request_path": str(_job_request_file(job_id)),
        "runner_response_path": str(_job_response_file(job_id)),
        "runner_probe_path": str(_job_probe_file(job_id)),
        "runner_stdout_path": str(_job_stdout_file(job_id)),
        "runner_stderr_path": str(_job_stderr_file(job_id)),
        "events_path": str(_job_events_file(job_id)),
        "job_path": str(_job_file(job_id)),
    }
    state["run_id"] = str(probe.get("run_id") or state.get("run_id") or "").strip()
    state["result_path"] = str(probe.get("result_path") or state.get("result_path") or "").strip()
    state["summary_path"] = str(probe.get("summary_path") or state.get("summary_path") or "").strip()
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    response_error = str(response.get("error") or "").strip()
    if result:
        state = _apply_benchmark_result_to_job_state(state, result)
        state["thread_alive"] = False
        state["runner_alive"] = False
    elif response_error:
        state["status"] = "failed"
        state["finished_utc"] = _utc_iso()
        state["error"] = response_error
    _write_job_state(job_id, state)
    _append_job_event(
        job_id,
        "benchmark_state_recovered",
        recovered_from="missing_job_file",
        status=str(state.get("status") or ""),
    )
    return state


def _write_job_state(job_id: str, state: Dict[str, Any]) -> None:
    _write_json(_job_file(job_id), state)


def _append_job_event(job_id: str, event: str, **fields: Any) -> None:
    _append_jsonl(
        _job_events_file(job_id),
        {
            "schema_version": SCHEMA_JOB,
            "utc": _utc_iso(),
            "event": str(event or "").strip(),
            **dict(fields or {}),
        },
    )


def _thread_alive(job_id: str) -> bool:
    with _JOB_THREADS_GUARD:
        thread = _JOB_THREADS.get(str(job_id or "").strip())
        return bool(thread is not None and thread.is_alive())


def _runner_pid(state: Dict[str, Any]) -> int:
    try:
        return max(0, int(state.get("runner_pid") or 0))
    except Exception:
        return 0


def _pid_alive(pid: int) -> bool:
    if int(pid or 0) <= 0:
        return False
    try:
        os.kill(int(pid), 0)
    except OSError:
        return False
    return True


def _read_last_line(path: Path) -> str:
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return ""
    for raw in reversed(lines):
        text = str(raw or "").strip()
        if text:
            return text
    return ""


def _benchmark_job_timeout_s() -> int:
    """Return the hard timeout for one full background benchmark run."""
    fallback = _benchmark_contender_timeout_s() * 2 + 300
    return _runtime_policy.get_runtime_policy_int(
        "agents.benchmark.job_timeout_s",
        fallback,
        minimum=600,
        maximum=28800,
    )


def _apply_benchmark_result_to_job_state(state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(state)
    updated["status"] = str(result.get("status") or "completed")
    updated["finished_utc"] = _utc_iso()
    updated["run_id"] = str(result.get("run_id") or "")
    updated["result_path"] = str(result.get("result_path") or "")
    updated["summary_path"] = str(result.get("summary_path") or "")
    updated["result_excerpt"] = _result_excerpt(result)
    updated["report_text"] = summarize_benchmark_result(result)
    updated.pop("error", None)
    return updated


def _write_job_probe(job_id: str, result: Dict[str, Any]) -> None:
    payload = {
        "schema_version": SCHEMA_JOB,
        "benchmark_id": str(job_id or "").strip(),
        "run_id": str(result.get("run_id") or "").strip(),
        "status": str(result.get("status") or "").strip(),
        "result_path": str(result.get("result_path") or "").strip(),
        "summary_path": str(result.get("summary_path") or "").strip(),
        "updated_utc": _utc_iso(),
    }
    _write_json(_job_probe_file(job_id), payload)


def _result_path_for_run_id(run_id: str) -> Path:
    return (_runs_root() / str(run_id or "").strip() / "result.json").resolve()


def _summary_path_for_run_id(run_id: str) -> Path:
    return (_runs_root() / str(run_id or "").strip() / "summary.md").resolve()


def _contender_progress_excerpt(result: Dict[str, Any]) -> Dict[str, Any]:
    contenders = result.get("contenders") if isinstance(result.get("contenders"), dict) else {}
    excerpt: Dict[str, Any] = {}
    for mode in ("workflow", "swarm"):
        row = contenders.get(mode) if isinstance(contenders.get(mode), dict) else {}
        if not row:
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        runner = row.get("runner") if isinstance(row.get("runner"), dict) else {}
        runtime_progress = runner.get("runtime_progress") if isinstance(runner.get("runtime_progress"), dict) else {}
        excerpt[mode] = {
            "ok": bool(row.get("ok")),
            "status": str(metrics.get("status") or metrics.get("phase") or metrics.get("turn") or "").strip(),
            "error": str(row.get("error") or "").strip(),
            "runtime_id": str(row.get("runtime_id") or runtime_progress.get("runtime_id") or "").strip(),
            "phase": str(runtime_progress.get("phase") or "").strip(),
            "updated_utc": str(runtime_progress.get("updated_utc") or "").strip(),
        }
    return excerpt


def _live_contender_progress_from_run_id(run_id: str) -> Dict[str, Any]:
    resolved_run_id = str(run_id or "").strip()
    if not resolved_run_id:
        return {}
    run_dir = (_runs_root() / resolved_run_id).resolve()
    excerpt: Dict[str, Any] = {}
    for mode in ("workflow", "swarm"):
        row = _read_json(run_dir / mode / "result.json")
        if not row:
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        runner = row.get("runner") if isinstance(row.get("runner"), dict) else {}
        runtime_progress = runner.get("runtime_progress") if isinstance(runner.get("runtime_progress"), dict) else {}
        excerpt[mode] = {
            "ok": bool(row.get("ok")),
            "status": str(metrics.get("status") or metrics.get("phase") or metrics.get("turn") or "").strip(),
            "error": str(row.get("error") or "").strip(),
            "runtime_id": str(row.get("runtime_id") or runtime_progress.get("runtime_id") or "").strip(),
            "phase": str(metrics.get("phase") or runtime_progress.get("phase") or "").strip(),
            "updated_utc": str(runtime_progress.get("updated_utc") or "").strip(),
        }
    return excerpt


def _apply_benchmark_progress_to_job_state(state: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    updated = dict(state)
    updated["run_id"] = str(result.get("run_id") or updated.get("run_id") or "").strip()
    updated["result_path"] = str(result.get("result_path") or updated.get("result_path") or "").strip()
    updated["summary_path"] = str(result.get("summary_path") or updated.get("summary_path") or "").strip()
    updated["result_excerpt"] = _result_excerpt(result)
    live_excerpt = _live_contender_progress_from_run_id(updated["run_id"])
    updated["progress_excerpt"] = live_excerpt or _contender_progress_excerpt(result)
    updated["run_updated_utc"] = str(result.get("updated_utc") or result.get("created_utc") or "").strip()
    return updated


def _contender_is_incomplete(row: Dict[str, Any]) -> bool:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    status = str(metrics.get("status") or metrics.get("phase") or metrics.get("turn") or "").strip().lower()
    return status in {"", "pending", "running"}


def _complete_partial_benchmark_result(result: Dict[str, Any], *, runner_error: str) -> Dict[str, Any]:
    repaired = dict(result)
    contenders = repaired.get("contenders") if isinstance(repaired.get("contenders"), dict) else {}
    updated_contenders: Dict[str, Any] = {}
    for mode, raw_row in contenders.items():
        row = dict(raw_row) if isinstance(raw_row, dict) else {}
        if not row:
            continue
        if _contender_is_incomplete(row):
            row["ok"] = False
            row["error"] = str(row.get("error") or runner_error).strip() or runner_error
            metrics = dict(row.get("metrics") if isinstance(row.get("metrics"), dict) else {})
            metrics["status"] = "benchmark_failed"
            row["metrics"] = metrics
        updated_contenders[str(mode)] = row
    repaired["contenders"] = updated_contenders
    repaired["finished_utc"] = str(repaired.get("finished_utc") or _utc_iso())
    if runner_error:
        repaired["recovery_error"] = str(runner_error).strip()
    if not updated_contenders:
        repaired["status"] = "failed"
    else:
        all_ok = all(bool((row or {}).get("ok")) for row in updated_contenders.values())
        repaired["status"] = "completed" if all_ok else "completed_with_failures"
    return repaired


def _persist_benchmark_result_artifacts(
    *,
    run_dir: Path,
    result: Dict[str, Any],
    probe_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    persisted = dict(result)
    result_path = (run_dir / "result.json").resolve()
    summary_md_path = (run_dir / "summary.md").resolve()
    summary_txt_path = (run_dir / "summary.txt").resolve()

    persisted["updated_utc"] = _utc_iso()
    persisted["result_path"] = str(result_path)
    persisted["summary_path"] = str(persisted.get("summary_path") or summary_md_path)
    persisted["report_text"] = summarize_benchmark_result(persisted)
    _write_json(result_path, persisted)
    if probe_job_id:
        _write_job_probe(probe_job_id, persisted)

    try:
        _write_summary_markdown(path=summary_md_path, result=persisted)
    except BaseException as exc:
        persisted["summary_markdown_path"] = str(summary_md_path)
        persisted["summary_write_error"] = _error_text(exc)
        persisted["summary_path"] = str(summary_txt_path)
        persisted["report_text"] = summarize_benchmark_result(persisted)
        _write_text(summary_txt_path, persisted["report_text"].rstrip() + "\n")

    _write_json(result_path, persisted)
    if probe_job_id:
        _write_job_probe(probe_job_id, persisted)
    return persisted


def _recover_job_result(job_id: str, state: Dict[str, Any], *, runner_error: str) -> Dict[str, Any]:
    current = dict(state)
    probe = _read_json(_job_probe_file(job_id))
    run_id = str(current.get("run_id") or probe.get("run_id") or "").strip()
    result_path_raw = str(current.get("result_path") or probe.get("result_path") or "").strip()
    result_path = Path(result_path_raw).resolve() if result_path_raw else _result_path_for_run_id(run_id)
    run_result = _read_json(result_path)
    if not run_result:
        return {}
    current = _apply_benchmark_progress_to_job_state(current, run_result)
    run_dir = result_path.parent
    terminal = str(run_result.get("status") or "").strip() in _TERMINAL_JOB_STATUSES
    summary_raw = str(run_result.get("summary_path") or "").strip()
    summary_path = Path(summary_raw) if summary_raw else None
    missing_summary = summary_path is None or not summary_path.exists()
    if not terminal and runner_error:
        run_result = _complete_partial_benchmark_result(run_result, runner_error=runner_error)
    if terminal or runner_error or missing_summary:
        run_result = _persist_benchmark_result_artifacts(run_dir=run_dir, result=run_result, probe_job_id=job_id)
    return run_result


def _finalize_job_failed(job_id: str, state: Dict[str, Any], *, error: str, event_name: str) -> Dict[str, Any]:
    updated = dict(state)
    updated["status"] = "failed"
    updated["finished_utc"] = _utc_iso()
    updated["error"] = str(error or "benchmark failed").strip() or "benchmark failed"
    updated["thread_alive"] = False
    updated["runner_alive"] = False
    _write_job_state(job_id, updated)
    _append_job_event(job_id, event_name, error=updated["error"])
    return updated


def _finalize_job_from_result(job_id: str, state: Dict[str, Any], result: Dict[str, Any], *, event_name: str) -> Dict[str, Any]:
    updated = _apply_benchmark_result_to_job_state(state, result)
    updated["thread_alive"] = False
    updated["runner_alive"] = False
    _write_job_state(job_id, updated)
    _write_job_probe(job_id, result)
    _append_job_event(
        job_id,
        event_name,
        run_id=updated["run_id"],
        status=updated["status"],
        result_path=updated["result_path"],
    )
    return updated


def _reconcile_job_state(job_id: str, state: Dict[str, Any]) -> Dict[str, Any]:
    current = dict(state)
    if str(current.get("status") or "").strip() in _TERMINAL_JOB_STATUSES:
        return current

    run_result = _recover_job_result(job_id, current, runner_error="")
    if run_result:
        current = _apply_benchmark_progress_to_job_state(current, run_result)
        if str(run_result.get("status") or "").strip() in _TERMINAL_JOB_STATUSES:
            return _finalize_job_from_result(job_id, current, run_result, event_name="benchmark_finished_recovered")

    response = _read_json(_job_response_file(job_id))
    result = response.get("result") if isinstance(response.get("result"), dict) else {}
    if result:
        return _finalize_job_from_result(job_id, current, result, event_name="benchmark_finished_recovered")

    response_error = str(response.get("error") or "").strip()
    if response_error:
        return _finalize_job_failed(job_id, current, error=response_error, event_name="benchmark_failed_recovered")

    runner_pid = _runner_pid(current)
    runner_alive = _pid_alive(runner_pid)
    thread_alive = _thread_alive(job_id)
    current["runner_alive"] = runner_alive
    current["thread_alive"] = thread_alive

    if runner_alive:
        _write_job_state(job_id, current)
        return current

    if thread_alive:
        return current

    stderr_tail = _read_last_line(_job_stderr_file(job_id))
    exit_code = current.get("runner_exit_code")
    pieces = ["benchmark runner stopped before writing a final result"]
    if exit_code not in (None, ""):
        pieces.append(f"exit_code={exit_code}")
    if stderr_tail:
        pieces.append(stderr_tail)
    runner_error = "; ".join(pieces)
    run_result = _recover_job_result(job_id, current, runner_error=runner_error)
    if run_result:
        current = _apply_benchmark_progress_to_job_state(current, run_result)
        return _finalize_job_from_result(job_id, current, run_result, event_name="benchmark_finished_recovered")
    return _finalize_job_failed(
        job_id,
        current,
        error=runner_error,
        event_name="benchmark_failed_recovered",
    )


def get_benchmark_status(*, benchmark_id: str) -> Dict[str, Any]:
    """Return persisted status for a background benchmark job."""
    job_id = str(benchmark_id or "").strip()
    if not job_id:
        raise BenchmarkError("benchmark_id is required")
    state = _read_json(_job_file(job_id))
    if not state:
        state = _recover_missing_job_state(job_id)
        if not state:
            raise FileNotFoundError(job_id)
    state["benchmark_id"] = job_id
    state = _reconcile_job_state(job_id, state)
    if str(state.get("status") or "").strip() in _TERMINAL_JOB_STATUSES:
        state["thread_alive"] = False
        state["runner_alive"] = False
    else:
        state["thread_alive"] = _thread_alive(job_id)
        state["runner_alive"] = _pid_alive(_runner_pid(state))
    state["events_path"] = str(_job_events_file(job_id))
    if "events" not in state:
        state["events"] = _read_jsonl(_job_events_file(job_id), limit=80)
    return state


def _result_excerpt(result: Dict[str, Any]) -> Dict[str, Any]:
    contenders = result.get("contenders") if isinstance(result.get("contenders"), dict) else {}
    excerpt: Dict[str, Any] = {}
    for mode in ("workflow", "swarm"):
        row = contenders.get(mode) if isinstance(contenders.get(mode), dict) else {}
        if not row:
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        excerpt[mode] = {
            "ok": bool(row.get("ok")),
            "runtime_ok": bool(row.get("runtime_ok", row.get("ok"))),
            "runtime_id": str(row.get("runtime_id") or ""),
            "status": str(metrics.get("status") or metrics.get("phase") or metrics.get("turn") or ""),
            "elapsed_s": float(metrics.get("elapsed_s") or 0.0),
            "checks_failed": int(metrics.get("checks_failed") or 0),
            "delivered_code": bool(metrics.get("delivered_code")),
            "error": str(row.get("error") or ""),
        }
    return excerpt


def summarize_benchmark_result(result: Dict[str, Any]) -> str:
    """Render a short human-readable comparison summary."""
    lines = [
        f"Benchmark {str(result.get('run_id') or '').strip()} finished: {str(result.get('status') or '').strip() or 'completed'}.",
    ]
    contenders = result.get("contenders") if isinstance(result.get("contenders"), dict) else {}
    ok_count = 0
    failed_count = 0
    for mode in ("workflow", "swarm"):
        row = contenders.get(mode) if isinstance(contenders.get(mode), dict) else {}
        if not row:
            continue
        if bool(row.get("ok")):
            ok_count += 1
        else:
            failed_count += 1
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        status = str(metrics.get("status") or metrics.get("phase") or metrics.get("turn") or "unknown").strip()
        elapsed = round(float(metrics.get("elapsed_s") or 0.0), 3)
        failed = int(metrics.get("checks_failed") or 0)
        delivered_code = bool(metrics.get("delivered_code"))
        score_row = _comparison_for_contender(row)
        runtime_ok = bool(row.get("runtime_ok", row.get("ok")))
        detail = f"{mode}: {status}, {elapsed}s"
        if delivered_code:
            detail += ", delivered_code=yes"
        detail += f", score={int(score_row['score'])}"
        if runtime_ok != bool(row.get("ok")):
            detail += f", runtime_ok={str(runtime_ok).lower()}"
        if failed:
            detail += f", checks failed={failed}"
        error = str(row.get("error") or "").strip()
        if error:
            detail += f", error={error}"
        lines.append(detail)
    summary_path = str(result.get("summary_path") or "").strip()
    if summary_path:
        lines.append(f"Report: {summary_path}")
    comparison = result.get("comparison") if isinstance(result.get("comparison"), dict) else {}
    winner = str(comparison.get("winner") or "").strip()
    if winner:
        lines.append(f"Winner: {winner}")
    elif bool(comparison.get("tie")):
        lines.append("Winner: tie")
    lines.append(f"Totals: ok={ok_count} failed={failed_count}")
    return "\n".join(lines)


def _write_summary_markdown(*, path: Path, result: Dict[str, Any]) -> None:
    contenders = result.get("contenders") if isinstance(result.get("contenders"), dict) else {}
    lines = [
        f"# {str(result.get('label') or 'Agent benchmark')}",
        "",
        f"- git sha: {result.get('git_sha')}",
        f"- git ref: {result.get('git_ref')}",
        f"- binding: {result.get('binding_key')}",
        "",
    ]
    for mode in ("workflow", "swarm"):
        row = contenders.get(mode) if isinstance(contenders.get(mode), dict) else {}
        if not row:
            continue
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        lines.extend(
            [
                f"## {mode}",
                "",
                f"- ok: {row.get('ok')}",
                f"- runtime_ok: {row.get('runtime_ok', row.get('ok'))}",
                f"- runtime id: {row.get('runtime_id')}",
                f"- status: {metrics.get('status') or metrics.get('phase') or metrics.get('turn') or ''}",
                f"- elapsed_s: {metrics.get('elapsed_s')}",
                f"- event_count: {metrics.get('event_count')}",
                f"- delivered_code: {metrics.get('delivered_code')}",
                f"- delivery_changed_paths: {metrics.get('delivery_changed_paths')}",
                f"- delivery_patch_bytes: {metrics.get('delivery_patch_bytes')}",
                f"- checks_passed: {int(metrics.get('checks_passed') or 0)}",
                f"- checks_failed: {metrics.get('checks_failed')}",
                f"- error: {row.get('error') or ''}",
                f"- final_patch: {((row.get('exported_changes') or {}).get('patch_path') if isinstance(row.get('exported_changes'), dict) else '') or ''}",
                f"- modified_files_snapshot: {((row.get('exported_changes') or {}).get('snapshot_root') if isinstance(row.get('exported_changes'), dict) else '') or ''}",
                "",
            ]
        )
    comparison = result.get("comparison") if isinstance(result.get("comparison"), dict) else {}
    winner = str(comparison.get("winner") or "").strip()
    lines.extend(
        [
            "## comparison",
            "",
            f"- winner: {winner or ('tie' if bool(comparison.get('tie')) else '')}",
            f"- tie: {bool(comparison.get('tie'))}",
            f"- scoring_formula: {((comparison.get('scoring') or {}).get('formula') if isinstance(comparison.get('scoring'), dict) else '') or ''}",
            "",
        ]
    )
    _write_text(path, "\n".join(lines).rstrip() + "\n")


def _enabled_contender_modes(spec: Dict[str, Any]) -> List[str]:
    """Return the benchmark contender modes enabled by the spec."""
    enabled: List[str] = []
    for mode in ("workflow", "swarm"):
        row = spec.get(mode) if isinstance(spec.get(mode), dict) else {}
        if bool(row.get("enabled")):
            enabled.append(mode)
    return enabled


def _run_benchmark_contender_pipeline(
    *,
    mode: str,
    git_sha: str,
    run_id: str,
    run_dir: Path,
    spec: Dict[str, Any],
    overlay: Dict[str, Any],
) -> Dict[str, Any]:
    """Run one contender end to end inside its disposable worktree."""
    contender_dir = (run_dir / mode).resolve()
    contender_dir.mkdir(parents=True, exist_ok=True)
    contender_payload: Dict[str, Any] = _contender_placeholder(mode=mode, error=f"{mode} benchmark started")
    _write_json(contender_dir / "result.json", contender_payload)

    cleanup_summary: Dict[str, Any] = {}
    worktree: Optional[Path] = None
    try:
        worktree = _create_worktree(git_sha=git_sha, run_id=run_id, mode=mode)
        cleanup_summary["worktree"] = str(worktree)
        cleanup_summary["sandbox_reset"] = _sanitize_benchmark_worktree(worktree)
        _apply_overlay_to_worktree(worktree=worktree, overlay=overlay)
        _create_baseline_commit(worktree=worktree, message=f"EIEL benchmark baseline {run_id}/{mode}")
        contender_payload = _contender_placeholder(mode=mode, error=f"{mode} benchmark running")
        contender_payload["metrics"]["status"] = "running"
        _write_json(contender_dir / "result.json", contender_payload)
        contender_payload = _run_contender_guarded(mode=mode, worktree=worktree, contender_dir=contender_dir, spec=spec)
    except BaseException as exc:
        contender_payload = _failed_contender_payload(mode=mode, error=_error_text(exc))
    finally:
        if worktree is not None:
            contender_payload["exported_changes"] = _capture_worktree_exports_safe(
                worktree=worktree,
                contender_dir=contender_dir,
            )
            contender_payload = _synthesize_swarm_exported_changes_from_runtime(
                payload=contender_payload,
                contender_dir=contender_dir,
            )
            contender_payload = _annotate_contender_outcome(contender_payload)
            try:
                cleanup_summary["cleanup"] = _remove_worktree(worktree)
            except BaseException as exc:
                cleanup_summary["cleanup"] = {
                    "ok": False,
                    "path": str(worktree),
                    "removed_by": "failed",
                    "error": _error_text(exc),
                }
        else:
            cleanup_summary["cleanup"] = {"ok": True, "path": "", "removed_by": "not-created", "error": ""}

    contender_payload = _annotate_contender_outcome(contender_payload)
    _write_json(contender_dir / "result.json", contender_payload)
    return {"mode": mode, "payload": contender_payload, "cleanup": cleanup_summary}


def _run_contender(
    *,
    mode: str,
    worktree: Path,
    contender_dir: Path,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    contender: Optional[Dict[str, Any]] = None
    checks: Dict[str, Any] = _failed_checks(message="contender execution did not finish")
    try:
        if mode == "workflow":
            contender = _execute_workflow(repo_root=worktree, spec=spec)
        else:
            contender = _execute_swarm(repo_root=worktree, spec=spec)
    except BaseException as exc:
        try:
            checks = _run_check_commands(
                commands=list(spec.get("check_commands") or []),
                cwd=worktree,
                output_dir=contender_dir / "checks",
            )
        except BaseException as check_exc:
            checks = _failed_checks(message=f"check commands failed after contender error: {_error_text(check_exc)}")
        return _failed_contender_payload(mode=mode, error=_error_text(exc), checks=checks)

    try:
        checks = _run_check_commands(
            commands=list(spec.get("check_commands") or []),
            cwd=worktree,
            output_dir=contender_dir / "checks",
        )
    except BaseException as exc:
        checks = _failed_checks(message=f"check commands failed: {_error_text(exc)}")

    try:
        copied = _copy_runtime_artifacts(contender_dir=contender_dir, runtime_paths=dict(contender.get("runtime_paths") or {}))
    except BaseException as exc:
        copied = {"state_path": "", "events_path": "", "artifacts_root": ""}
        return _failed_contender_payload(
            mode=mode,
            runtime_id=str(contender.get("runtime_id") or ""),
            error=f"failed to copy runtime artifacts: {_error_text(exc)}",
            checks=checks,
        ) | {"copied_runtime": copied}

    metrics = _workflow_metrics(contender, checks) if mode == "workflow" else _swarm_metrics(contender, checks)
    checks_ok = int((checks.get("summary") or {}).get("failed") or 0) == 0
    payload = {
        "mode": mode,
        "ok": bool(contender.get("ok")) and checks_ok,
        "runtime_id": str(contender.get("runtime_id") or ""),
        "metrics": metrics,
        "checks": checks,
        "copied_runtime": copied,
    }
    if not bool(contender.get("ok")):
        payload["error"] = str(
            (contender.get("result") or {}).get("error") or metrics.get("last_error") or f"{mode} contender returned not ok"
        ).strip()
    elif not checks_ok:
        failed = int((checks.get("summary") or {}).get("failed") or 0)
        payload["error"] = f"{mode} benchmark check commands failed ({failed})"
    return payload


def _benchmark_contender_timeout_s() -> int:
    """Return the hard timeout for one benchmark contender child process."""
    fallback = _runtime_policy.get_runtime_policy_int(
        "agents.swarm.wall_clock_s",
        3600,
        minimum=60,
        maximum=7200,
    ) + 300
    return _runtime_policy.get_runtime_policy_int(
        "agents.benchmark.contender_timeout_s",
        fallback,
        minimum=300,
        maximum=14400,
    )


def _benchmark_contender_stall_timeout_s() -> int:
    """Return the D-state stall budget for one contender child process."""
    return _runtime_policy.get_runtime_policy_int(
        "agents.benchmark.contender_stall_timeout_s",
        60,
        minimum=10,
        maximum=1800,
    )


def _benchmark_terminal_runtime_grace_s() -> int:
    """Return the grace window after an inner runtime is already terminal."""
    return _runtime_policy.get_runtime_policy_int(
        "agents.benchmark.terminal_runtime_grace_s",
        30,
        minimum=1,
        maximum=180,
    )


def _benchmark_planning_progress_timeout_s() -> int:
    """Return the longer no-progress budget for quiet planning phases."""
    return _runtime_policy.get_runtime_policy_int(
        "agents.benchmark.planning_progress_timeout_s",
        180,
        minimum=30,
        maximum=1800,
    )


def _benchmark_runtime_deadline_grace_s() -> int:
    """Return the grace window to honor an inner runtime slice deadline."""
    return _runtime_policy.get_runtime_policy_int(
        "agents.benchmark.runtime_deadline_grace_s",
        10,
        minimum=0,
        maximum=120,
    )


def _runtime_progress_timeout_s(
    *,
    timeout_s: int,
    stall_timeout_s: int,
    snapshot: Dict[str, Any],
) -> int:
    """Compute a fair no-progress timeout from the latest runtime snapshot."""
    limit = max(1, int(stall_timeout_s))
    if not isinstance(snapshot, dict) or not snapshot:
        return min(max(1, int(timeout_s)), limit)

    phase = str(snapshot.get("phase") or "").strip().lower()
    status_detail = str(snapshot.get("status_detail") or "").strip().lower()
    if phase.startswith("planning") or status_detail.startswith("planning"):
        limit = max(limit, int(_benchmark_planning_progress_timeout_s()))

    deadline = _parse_iso_utc(str(snapshot.get("active_deadline_utc") or ""))
    last_progress = _parse_iso_utc(str(snapshot.get("last_progress_utc") or ""))
    if deadline is not None:
        if last_progress is not None:
            allowed = int((deadline - last_progress).total_seconds()) + int(_benchmark_runtime_deadline_grace_s())
            if allowed > 0:
                limit = max(limit, allowed)
        remaining = int((deadline - _utc_now()).total_seconds())
        if remaining > 0:
            limit = max(limit, remaining + int(_benchmark_runtime_deadline_grace_s()))

    return min(max(1, int(timeout_s)), limit)


def _pid_process_state(pid: int) -> str:
    raw_pid = int(pid or 0)
    if raw_pid <= 0:
        return ""
    stat_path = Path("/proc") / str(raw_pid) / "stat"
    try:
        raw = stat_path.read_text(encoding="utf-8", errors="replace").strip()
    except Exception:
        return ""
    if not raw:
        return ""
    parts = raw.split()
    return str(parts[2]).strip() if len(parts) >= 3 else ""


def _runtime_progress_snapshot(*, mode: str, worktree: Path) -> Dict[str, Any]:
    runtime_dir = (
        worktree / "governance" / "agents" / "runtime" / ("orchestration" if mode == "workflow" else "swarm")
    ).resolve()
    if not runtime_dir.exists() or not runtime_dir.is_dir():
        return {}
    pattern = "workflow__*.json" if mode == "workflow" else "swarm__*.json"
    latest_path: Optional[Path] = None
    latest_mtime = -1.0
    for path in runtime_dir.glob(pattern):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            mtime = float(path.stat().st_mtime)
        except Exception:
            continue
        if latest_path is None or mtime > latest_mtime:
            latest_path = path
            latest_mtime = mtime
    if latest_path is None:
        return {}
    state = _read_json(latest_path)
    if not state:
        return {}
    runtime_id = str(state.get("workflow_id") or state.get("swarm_id") or latest_path.stem).strip()
    status = str(state.get("status") or "").strip()
    status_detail = str(state.get("status_detail") or "").strip()
    phase = str(state.get("phase") or state.get("turn") or "").strip()
    updated_utc = str(state.get("updated_utc") or "").strip()
    last_progress_utc = str(
        state.get("last_meaningful_progress_utc")
        or state.get("updated_utc")
        or state.get("created_utc")
        or ""
    ).strip()
    active_deadline_utc = str(state.get("active_slice_deadline_utc") or state.get("deadline_utc") or "").strip()
    marker = "||".join(
        (
            runtime_id,
            status,
            status_detail,
            phase,
            str(state.get("active_progress_note") or "").strip(),
            last_progress_utc,
            updated_utc,
        )
    )
    return {
        "mode": mode,
        "path": str(latest_path),
        "runtime_id": runtime_id,
        "status": status,
        "status_detail": status_detail,
        "phase": phase,
        "updated_utc": updated_utc,
        "last_progress_utc": last_progress_utc,
        "active_deadline_utc": active_deadline_utc,
        "marker": marker,
    }


def _run_contender_child_main(argv: Optional[List[str]] = None) -> int:
    """Execute one contender in a standalone child process and persist its payload."""
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        raise SystemExit("usage: <request_json> <response_json>")
    request_path = Path(str(args[0])).resolve()
    response_path = Path(str(args[1])).resolve()
    request = _read_json(request_path)
    mode = str(request.get("mode") or "").strip()
    contender_dir = Path(str(request.get("contender_dir") or "")).resolve()
    worktree = Path(str(request.get("worktree") or "")).resolve()
    spec = request.get("spec") if isinstance(request.get("spec"), dict) else {}
    _configure_local_codex_env_defaults(repo_root=worktree, force_turn_cmd=True)
    try:
        payload = _run_contender(
            mode=mode,
            worktree=worktree,
            contender_dir=contender_dir,
            spec=spec,
        )
        _write_json(response_path, payload)
        return 0
    except BaseException as exc:
        payload = _failed_contender_payload(mode=mode or "unknown", error=_error_text(exc))
        try:
            _write_json(response_path, payload)
        except Exception:
            pass
        return 1


def _run_contender_guarded(
    *,
    mode: str,
    worktree: Path,
    contender_dir: Path,
    spec: Dict[str, Any],
) -> Dict[str, Any]:
    """Run one contender in a killable child process so hangs finalize cleanly."""
    timeout_s = _benchmark_contender_timeout_s()
    stall_timeout_s = min(timeout_s, _benchmark_contender_stall_timeout_s())
    terminal_runtime_grace_s = min(timeout_s, _benchmark_terminal_runtime_grace_s())
    request_path = (contender_dir / "runner_request.json").resolve()
    response_path = (contender_dir / "runner_response.json").resolve()
    stdout_path = (contender_dir / "runner.stdout.txt").resolve()
    stderr_path = (contender_dir / "runner.stderr.txt").resolve()
    _write_json(
        request_path,
        {
            "mode": mode,
            "worktree": str(Path(worktree).resolve()),
            "contender_dir": str(Path(contender_dir).resolve()),
            "spec": spec,
        },
    )
    _write_json(
        contender_dir / "result.json",
        _contender_placeholder(mode=mode, error=f"{mode} benchmark running"),
    )
    env = dict(os.environ)
    repo_root = _repo_root()
    existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = str(repo_root) if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    cmd = [
        sys.executable,
        "-c",
        "from tools.benchmarks.agent_benchmark import _run_contender_child_main as _m; raise SystemExit(_m())",
        str(request_path),
        str(response_path),
    ]
    stdout_handle = open(stdout_path, "w", encoding="utf-8", newline="")
    stderr_handle = open(stderr_path, "w", encoding="utf-8", newline="")
    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(repo_root),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise
    try:
        deadline = time.monotonic() + max(1, int(timeout_s))
        d_state_since = 0.0
        progress_started = time.monotonic()
        last_progress_at = progress_started
        latest_snapshot: Dict[str, Any] = {}
        last_marker = ""
        last_placeholder_write_at = 0.0
        terminal_snapshot_since = 0.0

        def _write_placeholder(status: str, *, error: str = "") -> None:
            payload = _contender_placeholder(mode=mode, error=error or f"{mode} benchmark {status}")
            payload["metrics"]["status"] = status
            if latest_snapshot:
                payload["runtime_id"] = str(latest_snapshot.get("runtime_id") or "").strip()
                phase = str(latest_snapshot.get("phase") or "").strip()
                if phase:
                    payload["metrics"]["phase"] = phase
            payload["runner"] = {
                "timed_out": False,
                "timeout_s": int(timeout_s),
                "stall_timeout_s": int(stall_timeout_s),
                "exit_code": proc.poll(),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "pid": int(proc.pid),
                "runtime_progress": dict(latest_snapshot),
            }
            _write_json(contender_dir / "result.json", payload)

        while True:
            payload = _read_json(response_path)
            if payload:
                payload["runner"] = {
                    "timed_out": False,
                    "timeout_s": int(timeout_s),
                    "stall_timeout_s": int(stall_timeout_s),
                    "exit_code": proc.poll(),
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "pid": int(proc.pid),
                }
                return payload
            rc = proc.poll()
            if rc is not None:
                break
            now = time.monotonic()
            if (now - last_placeholder_write_at) >= 1.0:
                _write_placeholder("running")
                last_placeholder_write_at = now
            if now >= deadline:
                try:
                    os.killpg(int(proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                payload = _failed_contender_payload(mode=mode, error=f"{mode} contender timed out after {int(timeout_s)}s")
                payload["runner"] = {
                    "timed_out": True,
                    "timeout_s": int(timeout_s),
                    "stall_timeout_s": int(stall_timeout_s),
                    "exit_code": None,
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "pid": int(proc.pid),
                    "runtime_progress": latest_snapshot,
                }
                _write_json(contender_dir / "result.json", payload)
                return payload
            process_state = _pid_process_state(int(proc.pid))
            if process_state == "D":
                if d_state_since <= 0.0:
                    d_state_since = now
                elif (now - d_state_since) >= float(stall_timeout_s):
                    try:
                        os.killpg(int(proc.pid), signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    payload = _failed_contender_payload(
                        mode=mode,
                        error=f"{mode} contender stalled in D state for {int(stall_timeout_s)}s",
                    )
                    payload["runner"] = {
                        "timed_out": False,
                        "stalled": True,
                        "process_state": "D",
                        "timeout_s": int(timeout_s),
                        "stall_timeout_s": int(stall_timeout_s),
                        "exit_code": None,
                        "stdout_path": str(stdout_path),
                        "stderr_path": str(stderr_path),
                        "pid": int(proc.pid),
                        "runtime_progress": latest_snapshot,
                    }
                    _write_json(contender_dir / "result.json", payload)
                    return payload
            else:
                d_state_since = 0.0
            snapshot = _runtime_progress_snapshot(mode=mode, worktree=worktree)
            if snapshot:
                latest_snapshot = snapshot
                marker = str(snapshot.get("marker") or "")
                if marker and marker != last_marker:
                    last_marker = marker
                    last_progress_at = now
                    terminal_snapshot_since = 0.0
                deadline_utc = _parse_iso_utc(str(snapshot.get("active_deadline_utc") or ""))
                if deadline_utc is not None and _utc_now() > deadline_utc:
                    try:
                        os.killpg(int(proc.pid), signal.SIGKILL)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    error = (
                        f"{mode} contender exceeded runtime slice deadline"
                        f" ({str(snapshot.get('runtime_id') or '').strip() or 'unknown runtime'}"
                    )
                    phase = str(snapshot.get("phase") or "").strip()
                    if phase:
                        error += f", phase={phase}"
                    error += ")"
                    payload = _failed_contender_payload(mode=mode, error=error)
                    payload["runner"] = {
                        "timed_out": False,
                        "stalled": True,
                        "timeout_s": int(timeout_s),
                        "stall_timeout_s": int(stall_timeout_s),
                        "exit_code": None,
                        "stdout_path": str(stdout_path),
                        "stderr_path": str(stderr_path),
                        "pid": int(proc.pid),
                        "runtime_progress": latest_snapshot,
                    }
                    _write_json(contender_dir / "result.json", payload)
                    return payload
                runtime_status = str(snapshot.get("status") or "").strip().lower()
                if runtime_status in _TERMINAL_RUNTIME_STATUSES:
                    if terminal_snapshot_since <= 0.0:
                        terminal_snapshot_since = now
                    elif (now - terminal_snapshot_since) >= float(terminal_runtime_grace_s):
                        try:
                            os.killpg(int(proc.pid), signal.SIGKILL)
                        except Exception:
                            try:
                                proc.kill()
                            except Exception:
                                pass
                        runtime_id = str(snapshot.get("runtime_id") or "").strip()
                        phase = str(snapshot.get("phase") or "").strip()
                        error = f"{mode} runtime reached terminal status {runtime_status}"
                        details: List[str] = []
                        if runtime_id:
                            details.append(runtime_id)
                        if phase:
                            details.append(f"phase={phase}")
                        if details:
                            error += f" ({', '.join(details)})"
                        error += " before contender subprocess returned"
                        payload = _failed_contender_payload(mode=mode, runtime_id=runtime_id, error=error)
                        payload["metrics"]["status"] = runtime_status
                        if phase:
                            payload["metrics"]["phase"] = phase
                        payload["runner"] = {
                            "timed_out": False,
                            "stalled": True,
                            "terminal_runtime": True,
                            "timeout_s": int(timeout_s),
                            "stall_timeout_s": int(stall_timeout_s),
                            "terminal_runtime_grace_s": int(terminal_runtime_grace_s),
                            "exit_code": None,
                            "stdout_path": str(stdout_path),
                            "stderr_path": str(stderr_path),
                            "pid": int(proc.pid),
                            "runtime_progress": latest_snapshot,
                        }
                        _write_json(contender_dir / "result.json", payload)
                        return payload
                else:
                    terminal_snapshot_since = 0.0
            progress_timeout_s = _runtime_progress_timeout_s(
                timeout_s=timeout_s,
                stall_timeout_s=stall_timeout_s,
                snapshot=latest_snapshot,
            )
            if not snapshot and (now - progress_started) >= float(stall_timeout_s):
                try:
                    os.killpg(int(proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                payload = _failed_contender_payload(
                    mode=mode,
                    error=f"{mode} contender did not publish runtime progress within {int(stall_timeout_s)}s",
                )
                payload["runner"] = {
                    "timed_out": False,
                    "stalled": True,
                    "timeout_s": int(timeout_s),
                    "stall_timeout_s": int(stall_timeout_s),
                    "exit_code": None,
                    "stdout_path": str(stdout_path),
                    "stderr_path": str(stderr_path),
                    "pid": int(proc.pid),
                    "runtime_progress": latest_snapshot,
                }
                _write_json(contender_dir / "result.json", payload)
                return payload
            if snapshot and (now - last_progress_at) >= float(progress_timeout_s):
                try:
                    os.killpg(int(proc.pid), signal.SIGKILL)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                runtime_id = str(latest_snapshot.get("runtime_id") or "").strip()
                phase = str(latest_snapshot.get("phase") or "").strip()
                error = f"{mode} contender made no runtime progress for {int(progress_timeout_s)}s"
                if runtime_id or phase:
                    details: List[str] = []
                    if runtime_id:
                        details.append(runtime_id)
                    if phase:
                        details.append(f"phase={phase}")
                    error += f" ({', '.join(details)})"
                payload = _failed_contender_payload(mode=mode, error=error)
                payload["runner"] = {
                        "timed_out": False,
                        "stalled": True,
                        "timeout_s": int(timeout_s),
                        "stall_timeout_s": int(stall_timeout_s),
                        "progress_timeout_s": int(progress_timeout_s),
                        "exit_code": None,
                        "stdout_path": str(stdout_path),
                        "stderr_path": str(stderr_path),
                        "pid": int(proc.pid),
                        "runtime_progress": latest_snapshot,
                }
                _write_json(contender_dir / "result.json", payload)
                return payload
            time.sleep(0.2)

        payload = _read_json(response_path)
        if payload:
            payload["runner"] = {
                "timed_out": False,
                "timeout_s": int(timeout_s),
                "stall_timeout_s": int(stall_timeout_s),
                "exit_code": int(proc.returncode),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "pid": int(proc.pid),
                "runtime_progress": latest_snapshot,
            }
            _write_json(contender_dir / "result.json", payload)
            return payload
        error_parts = [f"{mode} contender subprocess exited before writing a result"]
        if proc.returncode not in (None, 0):
            error_parts.append(f"exit_code={int(proc.returncode)}")
        stderr_tail = _read_last_line(stderr_path)
        if stderr_tail:
            error_parts.append(stderr_tail)
        payload = _failed_contender_payload(mode=mode, error="; ".join(error_parts))
        payload["runner"] = {
            "timed_out": False,
            "timeout_s": int(timeout_s),
            "stall_timeout_s": int(stall_timeout_s),
            "exit_code": int(proc.returncode) if proc.returncode is not None else None,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "pid": int(proc.pid),
            "runtime_progress": latest_snapshot,
        }
        _write_json(contender_dir / "result.json", payload)
        return payload
    finally:
        stdout_handle.close()
        stderr_handle.close()


def run_benchmark_from_spec(
    *,
    spec: Dict[str, Any],
    spec_path: Optional[Path] = None,
    run_id: Optional[str] = None,
    probe_job_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Run the benchmark described by the validated spec."""
    _configure_local_codex_env_defaults(repo_root=_repo_root())
    _prune_stale_worktrees()
    git_sha = _resolve_git_ref(str(spec.get("git_ref") or "HEAD"))
    resolved_run_id = str(run_id or "").strip() or _new_run_id(spec, git_sha)
    run_dir = (_runs_root() / resolved_run_id).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_copy_path = (run_dir / "spec.json").resolve()
    _write_json(spec_copy_path, spec)
    if spec_path is not None:
        _write_text(run_dir / "spec_source.txt", str(Path(spec_path).resolve()))

    result: Dict[str, Any] = {
        "schema_version": SCHEMA_RESULT,
        "run_id": resolved_run_id,
        "label": str(spec.get("label") or ""),
        "git_ref": str(spec.get("git_ref") or "HEAD"),
        "git_sha": git_sha,
        "created_utc": _utc_iso(),
        "binding": dict(spec.get("binding") or {}),
        "binding_key": _binding_key(dict(spec.get("binding") or {})),
        "objective": str(spec.get("objective") or ""),
        "spec_path": str(spec_copy_path),
        "contenders": {},
        "status": "running",
    }
    result = _persist_benchmark_result_artifacts(run_dir=run_dir, result=result, probe_job_id=probe_job_id)

    cleanup_summary: Dict[str, Any] = {}
    try:
        overlay = _capture_repo_overlay()
        result["repo_overlay"] = {
            "tracked_dirty": bool(overlay.get("tracked_dirty")),
            "untracked_count": int(overlay.get("untracked_count") or 0),
            "untracked_skipped_count": int(overlay.get("untracked_skipped_count") or 0),
        }
        result = _persist_benchmark_result_artifacts(run_dir=run_dir, result=result, probe_job_id=probe_job_id)
        enabled_modes = _enabled_contender_modes(spec)
        thread_rows: Dict[str, Dict[str, Any]] = {}
        thread_errors: Dict[str, str] = {}
        thread_guard = threading.Lock()
        worker_threads: List[threading.Thread] = []

        def _runner(mode: str) -> None:
            try:
                pipeline = _run_benchmark_contender_pipeline(
                    mode=mode,
                    git_sha=git_sha,
                    run_id=resolved_run_id,
                    run_dir=run_dir,
                    spec=spec,
                    overlay=overlay,
                )
            except BaseException as exc:
                pipeline = {
                    "mode": mode,
                    "payload": _failed_contender_payload(mode=mode, error=_error_text(exc)),
                    "cleanup": {"cleanup": {"ok": False, "path": "", "removed_by": "failed", "error": _error_text(exc)}},
                }
            with thread_guard:
                thread_rows[mode] = pipeline
                cleanup_summary[mode] = dict(pipeline.get("cleanup") if isinstance(pipeline.get("cleanup"), dict) else {})
                error_text = str((pipeline.get("payload") or {}).get("error") or "").strip()
                if error_text:
                    thread_errors[mode] = error_text

        for mode in enabled_modes:
            contender_dir = (run_dir / mode).resolve()
            contender_dir.mkdir(parents=True, exist_ok=True)
            contender_payload: Dict[str, Any] = _contender_placeholder(mode=mode, error=f"{mode} benchmark started")
            _write_json(contender_dir / "result.json", contender_payload)
            result["contenders"][mode] = contender_payload
            cleanup_summary[mode] = {}
            worker = threading.Thread(target=_runner, args=(mode,), name=f"benchmark-{resolved_run_id}-{mode}", daemon=True)
            worker_threads.append(worker)
            worker.start()

        result = _persist_benchmark_result_artifacts(run_dir=run_dir, result=result, probe_job_id=probe_job_id)

        while True:
            any_alive = False
            contender_rows = result.get("contenders") if isinstance(result.get("contenders"), dict) else {}
            for worker in worker_threads:
                worker.join(timeout=0.05)
                if worker.is_alive():
                    any_alive = True
            for mode in enabled_modes:
                current = _read_json(run_dir / mode / "result.json")
                if current:
                    contender_rows[mode] = current
            result["contenders"] = contender_rows
            result = _persist_benchmark_result_artifacts(run_dir=run_dir, result=result, probe_job_id=probe_job_id)
            if not any_alive:
                break
            time.sleep(0.1)

        for mode in enabled_modes:
            pipeline = thread_rows.get(mode) if isinstance(thread_rows.get(mode), dict) else {}
            payload = pipeline.get("payload") if isinstance(pipeline.get("payload"), dict) else {}
            if payload:
                result["contenders"][mode] = payload
                _write_json(run_dir / mode / "result.json", payload)
        contender_rows = result.get("contenders") if isinstance(result.get("contenders"), dict) else {}
        result["comparison"] = _comparison_summary(contender_rows)
        all_ok = bool(contender_rows) and all(bool((row or {}).get("ok")) for row in contender_rows.values())
        result["status"] = "completed" if all_ok else "completed_with_failures"
        if thread_errors:
            result["contender_errors"] = dict(sorted(thread_errors.items()))
    except BaseException as exc:
        result["status"] = "failed"
        result["fatal_error"] = _error_text(exc)
    finally:
        result["finished_utc"] = _utc_iso()
        result["cleanup"] = cleanup_summary
        result = _persist_benchmark_result_artifacts(run_dir=run_dir, result=result, probe_job_id=probe_job_id)
    return result


def run_benchmark(*, spec_path: Path) -> Dict[str, Any]:
    """Load a benchmark spec from disk and execute it."""
    spec = load_benchmark_spec(spec_path)
    return run_benchmark_from_spec(spec=spec, spec_path=spec_path)


def run_benchmark_suite(*, suite_path: Path) -> Dict[str, Any]:
    """Load a benchmark suite manifest from disk and execute each spec sequentially."""
    suite = load_benchmark_suite(suite_path)
    runs: List[Dict[str, Any]] = []
    ok_count = 0
    failed_count = 0
    for raw_spec_path in suite["spec_paths"]:
        payload = run_benchmark(spec_path=Path(raw_spec_path))
        runs.append(
            {
                "spec_path": str(raw_spec_path),
                "run_id": str(payload.get("run_id") or ""),
                "status": str(payload.get("status") or ""),
                "summary_path": str(payload.get("summary_path") or ""),
                "result_path": str(payload.get("result_path") or ""),
            }
        )
        contenders = payload.get("contenders") if isinstance(payload.get("contenders"), dict) else {}
        all_ok = bool(contenders) and all(bool((row or {}).get("ok")) for row in contenders.values())
        if all_ok:
            ok_count += 1
        else:
            failed_count += 1
    return {
        "schema_version": SCHEMA_SUITE_RESULT,
        "suite_label": suite["label"],
        "suite_path": str(suite_path.resolve()),
        "status": "completed" if failed_count == 0 else "completed_with_failures",
        "spec_count": len(runs),
        "ok_count": ok_count,
        "failed_count": failed_count,
        "runs": runs,
    }


def run_simple_benchmark(
    *,
    objective: str,
    binding: Dict[str, Any],
    label: Optional[str] = None,
    git_ref: str = "HEAD",
    check_commands: Optional[List[str]] = None,
    workflow_enabled: bool = True,
    swarm_enabled: bool = True,
    workflow_options: Optional[Dict[str, Any]] = None,
    swarm_options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Run a benchmark without requiring a hand-authored spec file."""
    spec = create_benchmark_spec(
        objective=objective,
        binding=binding,
        label=label,
        git_ref=git_ref,
        check_commands=check_commands,
        workflow_enabled=workflow_enabled,
        swarm_enabled=swarm_enabled,
        workflow_options=workflow_options,
        swarm_options=swarm_options,
    )
    return run_benchmark_from_spec(spec=spec)


def _run_benchmark_child_main(argv: Optional[List[str]] = None) -> int:
    """Execute a full benchmark in a standalone child process and persist its result."""
    args = list(argv if argv is not None else sys.argv[1:])
    if len(args) != 2:
        raise SystemExit("usage: <request_json> <response_json>")
    request_path = Path(str(args[0])).resolve()
    response_path = Path(str(args[1])).resolve()
    request = _read_json(request_path)
    spec = request.get("spec") if isinstance(request.get("spec"), dict) else {}
    run_id = str(request.get("run_id") or "").strip() or None
    probe_job_id = str(request.get("probe_job_id") or "").strip() or None
    try:
        result = run_benchmark_from_spec(spec=spec, run_id=run_id, probe_job_id=probe_job_id)
        _write_json(response_path, {"ok": True, "result": result})
        return 0
    except BaseException as exc:
        payload = {"ok": False, "error": _error_text(exc)}
        try:
            _write_json(response_path, payload)
        except Exception:
            pass
        return 1


def start_background_benchmark(
    *,
    objective: str,
    binding: Dict[str, Any],
    label: Optional[str] = None,
    git_ref: str = "HEAD",
    check_commands: Optional[List[str]] = None,
    workflow_enabled: bool = True,
    swarm_enabled: bool = True,
    workflow_options: Optional[Dict[str, Any]] = None,
    swarm_options: Optional[Dict[str, Any]] = None,
    on_complete: Optional[Callable[[Dict[str, Any]], None]] = None,
) -> Dict[str, Any]:
    """Start a background benchmark job and persist status under runtime/state."""
    spec = create_benchmark_spec(
        objective=objective,
        binding=binding,
        label=label,
        git_ref=git_ref,
        check_commands=check_commands,
        workflow_enabled=workflow_enabled,
        swarm_enabled=swarm_enabled,
        workflow_options=workflow_options,
        swarm_options=swarm_options,
    )
    git_sha = _resolve_git_ref(str(spec.get("git_ref") or "HEAD"))
    run_id = _new_run_id(spec, git_sha)
    label_text = str(spec.get("label") or spec.get("objective") or "benchmark").strip()
    job_id = _new_job_id(label_text)
    state: Dict[str, Any] = {
        "schema_version": SCHEMA_JOB,
        "benchmark_id": job_id,
        "status": "queued",
        "created_utc": _utc_iso(),
        "label": label_text,
        "objective": str(spec.get("objective") or ""),
        "git_ref": str(spec.get("git_ref") or "HEAD"),
        "binding": dict(spec.get("binding") or {}),
        "binding_key": _binding_key(dict(spec.get("binding") or {})),
        "result_path": str(_result_path_for_run_id(run_id)),
        "summary_path": str(_summary_path_for_run_id(run_id)),
        "run_id": run_id,
        "thread_alive": True,
        "runner_pid": 0,
        "runner_alive": False,
        "runner_timeout_s": _benchmark_job_timeout_s(),
        "runner_request_path": str(_job_request_file(job_id)),
        "runner_response_path": str(_job_response_file(job_id)),
        "runner_stdout_path": str(_job_stdout_file(job_id)),
        "runner_stderr_path": str(_job_stderr_file(job_id)),
        "events_path": str(_job_events_file(job_id)),
        "job_path": str(_job_file(job_id)),
    }
    _write_job_state(job_id, state)
    _append_job_event(job_id, "benchmark_queued", objective=state["objective"], binding=state["binding"])

    _write_json(_job_request_file(job_id), {"spec": spec, "run_id": run_id, "probe_job_id": job_id})
    for cleanup_path in (_job_response_file(job_id), _job_stdout_file(job_id), _job_stderr_file(job_id)):
        try:
            cleanup_path.unlink()
        except FileNotFoundError:
            pass

    repo_root = _repo_root()
    env = dict(os.environ)
    existing_pythonpath = str(env.get("PYTHONPATH") or "").strip()
    env["PYTHONPATH"] = str(repo_root) if not existing_pythonpath else f"{repo_root}{os.pathsep}{existing_pythonpath}"
    child_cmd = [
        sys.executable,
        "-c",
        "from tools.benchmarks.agent_benchmark import _run_benchmark_child_main as _m; raise SystemExit(_m())",
        str(_job_request_file(job_id)),
        str(_job_response_file(job_id)),
    ]
    stdout_handle = open(_job_stdout_file(job_id), "w", encoding="utf-8", newline="")
    stderr_handle = open(_job_stderr_file(job_id), "w", encoding="utf-8", newline="")
    try:
        proc = subprocess.Popen(
            child_cmd,
            cwd=str(repo_root),
            env=env,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
    except Exception:
        stdout_handle.close()
        stderr_handle.close()
        raise

    def _runner() -> None:
        current = dict(state)
        current["status"] = "running"
        current["started_utc"] = _utc_iso()
        current["runner_pid"] = int(proc.pid)
        current["runner_alive"] = True
        _write_job_state(job_id, current)
        _append_job_event(job_id, "benchmark_started", git_ref=current["git_ref"], runner_pid=int(proc.pid))
        callback_payload: Dict[str, Any]
        try:
            timeout_s = int(current.get("runner_timeout_s") or _benchmark_job_timeout_s())
            try:
                exit_code = int(proc.wait(timeout=max(1, timeout_s)))
                current["runner_exit_code"] = exit_code
                current["runner_alive"] = False
                _write_job_state(job_id, current)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = int(proc.wait(timeout=30))
                current["runner_exit_code"] = exit_code
                current["runner_alive"] = False
                current = _finalize_job_failed(
                    job_id,
                    current,
                    error=f"benchmark runner timed out after {timeout_s}s",
                    event_name="benchmark_failed",
                )
            else:
                current = _read_json(_job_file(job_id)) or current
                if str(current.get("status") or "").strip() in _TERMINAL_JOB_STATUSES:
                    pass
                else:
                    response = _read_json(_job_response_file(job_id))
                    result = response.get("result") if isinstance(response.get("result"), dict) else {}
                    if result:
                        current = _finalize_job_from_result(job_id, current, result, event_name="benchmark_finished")
                    else:
                        response_error = str(response.get("error") or "").strip()
                        stderr_tail = _read_last_line(_job_stderr_file(job_id))
                        error = response_error or f"benchmark runner exited before writing a result; exit_code={exit_code}"
                        if stderr_tail and stderr_tail not in error:
                            error = f"{error}; {stderr_tail}"
                        current = _finalize_job_failed(job_id, current, error=error, event_name="benchmark_failed")
        except BaseException as exc:
            current["runner_alive"] = False
            current = _finalize_job_failed(job_id, current, error=_error_text(exc), event_name="benchmark_failed")
        finally:
            stdout_handle.close()
            stderr_handle.close()
            current["thread_alive"] = False
            _write_job_state(job_id, current)
            with _JOB_THREADS_GUARD:
                _JOB_THREADS.pop(job_id, None)
            callback_payload = get_benchmark_status(benchmark_id=job_id)
        if callable(on_complete):
            try:
                on_complete(callback_payload)
            except Exception:
                pass

    thread = threading.Thread(target=_runner, name=f"benchmark-{job_id}", daemon=True)
    with _JOB_THREADS_GUARD:
        _JOB_THREADS[job_id] = thread
    thread.start()
    return get_benchmark_status(benchmark_id=job_id)


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Run workflow vs swarm playground benchmark")
    ap.add_argument("--spec", help="Path to agent benchmark spec JSON")
    ap.add_argument("--suite", help="Path to agent benchmark suite JSON")
    ap.add_argument("--objective", help="Benchmark objective for the default runner path")
    ap.add_argument("--binding-type", choices=["task_id", "ledger_uid"], help="Binding type for the default runner path")
    ap.add_argument("--binding-id", help="Binding id for the default runner path")
    ap.add_argument("--label", help="Optional benchmark label")
    ap.add_argument("--git-ref", default="HEAD", help="Git ref to benchmark from")
    ap.add_argument("--check-command", action="append", default=[], help="Optional shared post-run check command")
    return ap


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.spec and args.suite:
        raise SystemExit("use either --spec or --suite, not both")
    if args.spec:
        payload = run_benchmark(spec_path=Path(str(args.spec)).resolve())
    elif args.suite:
        payload = run_benchmark_suite(suite_path=Path(str(args.suite)).resolve())
    else:
        if not args.objective or not args.binding_type or not args.binding_id:
            raise SystemExit("--spec, --suite, or (--objective, --binding-type, --binding-id) is required")
        payload = run_simple_benchmark(
            objective=str(args.objective),
            binding={"type": str(args.binding_type), "id": str(args.binding_id)},
            label=str(args.label or "").strip() or None,
            git_ref=str(args.git_ref or "HEAD"),
            check_commands=list(args.check_command or []),
        )
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    if str(payload.get("schema_version") or "") == SCHEMA_SUITE_RESULT:
        return 0 if int(payload.get("failed_count") or 0) == 0 else 1
    contenders = payload.get("contenders") if isinstance(payload.get("contenders"), dict) else {}
    workflow_ok = bool((contenders.get("workflow") or {}).get("ok", True))
    swarm_ok = bool((contenders.get("swarm") or {}).get("ok", True))
    return 0 if workflow_ok and swarm_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
