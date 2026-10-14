"""infra.webui.server_agents_orchestrator

Persisted orchestration runtime for SE->Builder->Verifier workflows.

This module is backend-only and intentionally independent from frontend state.
It stores workflow snapshots + event logs under governance/agents/runtime so
runs can resume after process crashes or restarts.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import inspect
import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from tools import runtime_policy as _runtime_policy
from tools.git.support import commit_journal as _commit_journal
from infra.host import verification_actions as _verification_actions
from infra.host import verification_contract as _verification_contract
from infra.webui import server_agents as _agents
from infra.webui import server_agents_orchestrator_repo_residue as _repo_residue_helpers
from infra.webui.ledger.ledger_manager import ChangeLedger


SCHEMA_STATE = "agents_orchestration_state_v1"
SCHEMA_EVENT = "agents_orchestration_event_v1"
SCHEMA_WORKFLOW_DIGEST = "agents_orchestration_digest_v1"
SCHEMA_REPO_SNAPSHOT = "agents_orchestration_repo_snapshot_v1"
SCHEMA_REPO_RESIDUE_SUMMARY = "agents_orchestration_repo_residue_summary_v1"
TASK_ID_RE = re.compile(r"^[a-z0-9]{3}$")
UID_RE = re.compile(r"^PIS-[A-Za-z0-9._:-]+$")
LEGACY_WORKFLOW_OBJECTIVE_RE = re.compile(
    r"^Implement and verify\s+"
    r"(?P<uid>PIS-[A-Za-z0-9._:-]+)\s+with\s+"
    r"(?P<phase>[^.()]+?)\s*"
    r"\(Builder\s*->\s*Verifier,\s*retry up to\s+\d+\s+iterations\)\.?\s*$",
    re.IGNORECASE,
)
DEFAULT_SUBPHASES = ["PIS"]
DEFAULT_MAX_ITERATIONS = 4
DEFAULT_HEARTBEAT_INTERVAL_S = 60


def _default_max_infra_retries() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.max_infra_retries",
        2,
        minimum=0,
        maximum=100,
    )


def _default_role_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.orchestrator_turn_timeout_s",
        1800,
        env_names=("EIEL_AGENTS_ORCHESTRATOR_TIMEOUT_S",),
        minimum=30,
        maximum=6 * 60 * 60,
    )


def _default_generalist_slice_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.generalist_slice_timeout_s",
        180,
        minimum=30,
        maximum=3600,
    )


def _default_builder_slice_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.builder_slice_timeout_s",
        300,
        minimum=30,
        maximum=3600,
    )


def _default_verifier_slice_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.verifier_slice_timeout_s",
        300,
        minimum=30,
        maximum=3600,
    )


def _default_generalist_progress_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.generalist_progress_timeout_s",
        75,
        minimum=30,
        maximum=1800,
    )


def _default_builder_progress_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.builder_progress_timeout_s",
        120,
        minimum=30,
        maximum=1800,
    )


def _default_verifier_progress_timeout_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.verifier_progress_timeout_s",
        120,
        minimum=30,
        maximum=1800,
    )


def _default_role_output_grace_s() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.role_output_grace_s",
        30,
        minimum=15,
        maximum=300,
    )


def _default_failure_retry_bound() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.failure_retry_bound",
        2,
        minimum=1,
        maximum=5,
    )


def _default_activity_repeat_bound() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.activity_repeat_bound",
        2,
        minimum=1,
        maximum=5,
    )


def _default_stuck_retry_limit() -> int:
    return _runtime_policy.get_runtime_policy_int(
        "agents.workflow.stuck_retry_limit",
        2,
        minimum=1,
        maximum=5,
    )


def _default_workflow_control_mode() -> str:
    return str(
        _runtime_policy.load_runtime_policy().get("agents.workflow.control_mode", "deterministic") or "deterministic"
    ).strip().lower()
MAX_REPO_RESIDUE_FILE_BYTES = 512 * 1024
MAX_MESSAGE_CHARS = 80_000
HOST_VERIFICATION_MARKER = "HOST_VERIFICATION_REQUEST_JSON:"

_LOCKS_GUARD = threading.Lock()
_LOCKS: Dict[str, threading.RLock] = {}


class AgentOrchestrationError(RuntimeError):
    """Raised for invalid orchestration operations."""


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _runtime_root() -> Path:
    p = (_repo_root() / "governance" / "agents" / "runtime" / "orchestration").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _utc_compact() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _parse_iso_utc(value: str) -> _dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return _dt.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None


def _named_lock(key: str) -> threading.RLock:
    lk_key = str(key or "").strip()
    if not lk_key:
        raise ValueError("lock key is required")
    with _LOCKS_GUARD:
        lk = _LOCKS.get(lk_key)
        if lk is None:
            lk = threading.RLock()
            _LOCKS[lk_key] = lk
        return lk


def _file_lock(path: Path) -> threading.RLock:
    key = str(path.resolve())
    return _named_lock(f"file::{key}")


def _workflow_run_lock(workflow_id: str) -> threading.RLock:
    wf = str(workflow_id or "").strip()
    if not wf:
        raise AgentOrchestrationError("workflow_id is required")
    return _named_lock(f"workflow_run::{wf}")


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
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = (path.parent / f".{path.name}.tmp").resolve()
    lk = _file_lock(path)
    with lk:
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            f.write(raw)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)


def _append_jsonl(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    lk = _file_lock(path)
    with lk:
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write(line)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())


def _read_jsonl(path: Path, *, limit: int = 2000) -> List[Dict[str, Any]]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except Exception:
        return []
    lim = max(1, min(20000, int(limit)))
    if len(lines) > lim:
        lines = lines[-lim:]
    out: List[Dict[str, Any]] = []
    for raw in lines:
        s = str(raw or "").strip()
        if not s:
            continue
        try:
            row = json.loads(s)
            if isinstance(row, dict):
                out.append(row)
        except Exception:
            continue
    return out


def _state_file(workflow_id: str) -> Path:
    return (_runtime_root() / f"workflow__{workflow_id}.json").resolve()


def _events_file(workflow_id: str) -> Path:
    return (_runtime_root() / f"workflow__{workflow_id}.events.jsonl").resolve()


def _artifacts_root(workflow_id: str) -> Path:
    """Return the per-workflow artifacts directory."""
    p = (_runtime_root() / f"workflow__{workflow_id}.artifacts").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _workflow_digest_file(workflow_id: str) -> Path:
    """Return the persisted workflow digest artifact path."""
    return (_artifacts_root(workflow_id) / "workflow_digest.json").resolve()


def _repo_residue_root(workflow_id: str) -> Path:
    """Return the per-workflow repo residue artifact directory."""
    p = (_artifacts_root(workflow_id) / "repo_residue").resolve()
    p.mkdir(parents=True, exist_ok=True)
    return p


def _repo_relative_path(path: Path) -> str:
    """Return a repo-relative path string when possible."""
    try:
        rel = path.resolve().relative_to(_repo_root().resolve())
        return rel.as_posix()
    except Exception:
        return str(path)


def _safe_repo_artifact_child(root: Path, relpath: str) -> Path:
    """Resolve a repo-relative artifact child path safely under root."""
    rel = Path(str(relpath or ""))
    if rel.is_absolute() or any(part == ".." for part in rel.parts):
        raise ValueError("unsafe artifact relpath")
    dst = (root / rel).resolve()
    root_resolved = root.resolve()
    if dst != root_resolved and root_resolved not in dst.parents:
        raise ValueError("artifact path escaped root")
    return dst


def _run_git(args: List[str], *, timeout_s: int = 30) -> subprocess.CompletedProcess[str]:
    """Run a git command in the repo root without raising on non-zero exit."""
    cmd = ["git", *args]
    return subprocess.run(
        cmd,
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        timeout=max(1, int(timeout_s)),
        check=False,
    )


def _git_paths_from_nul(stdout: str) -> List[str]:
    """Parse NUL-delimited git path output into normalized repo-relative paths."""
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


def _repo_entry_signature(entry: Dict[str, Any]) -> str:
    """Return a stable comparison signature for a repo snapshot entry."""
    return _repo_residue_helpers.repo_entry_signature(entry)


def _repo_entry_for_path(*, relpath: str, tracked_dirty: bool, untracked: bool) -> Dict[str, Any]:
    """Build one repo snapshot entry for a dirty or untracked path."""
    root = _repo_root().resolve()
    abs_path = (root / relpath).resolve()
    kind = "missing"
    exists = False
    size_bytes = 0
    sha256 = ""
    if abs_path.exists():
        exists = True
        if abs_path.is_symlink():
            kind = "symlink"
        elif abs_path.is_file():
            kind = "file"
            data = abs_path.read_bytes()
            size_bytes = len(data)
            sha256 = hashlib.sha256(data).hexdigest()
        elif abs_path.is_dir():
            kind = "dir"
        else:
            kind = "other"
    return {
        "path": relpath,
        "tracked_dirty": bool(tracked_dirty),
        "untracked": bool(untracked),
        "exists": exists,
        "kind": kind,
        "size_bytes": int(size_bytes),
        "sha256": sha256,
    }


def _collect_repo_dirty_snapshot() -> Dict[str, Any]:
    """Collect a best-effort snapshot of dirty tracked and untracked repo paths."""
    snap: Dict[str, Any] = {
        "schema_version": SCHEMA_REPO_SNAPSHOT,
        "captured_utc": _utc_now_iso(),
        "repo_root": str(_repo_root()),
        "git_available": False,
        "git_error": "",
        "head": "",
        "branch": "",
        "status_short": "",
        "tracked_dirty_paths": [],
        "untracked_paths": [],
        "dirty_path_count": 0,
        "entries": [],
    }
    try:
        head_cp = _run_git(["rev-parse", "--verify", "HEAD"])
        branch_cp = _run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        status_cp = _run_git(["status", "--short", "--branch"])
        tracked_cp = _run_git(["diff", "--name-only", "-z", "HEAD", "--"])
        untracked_cp = _run_git(["ls-files", "--others", "--exclude-standard", "-z"])
    except Exception as exc:
        snap["git_error"] = str(exc)
        return snap
    if head_cp.returncode != 0:
        snap["git_error"] = str(head_cp.stderr or head_cp.stdout or "git rev-parse failed").strip()
        return snap
    snap["git_available"] = True
    snap["head"] = str(head_cp.stdout or "").strip()
    if branch_cp.returncode == 0:
        snap["branch"] = str(branch_cp.stdout or "").strip()
    if status_cp.returncode == 0:
        snap["status_short"] = str(status_cp.stdout or "").strip()
    tracked_dirty_paths = _git_paths_from_nul(tracked_cp.stdout if tracked_cp.returncode == 0 else "")
    untracked_paths = _git_paths_from_nul(untracked_cp.stdout if untracked_cp.returncode == 0 else "")
    entries: List[Dict[str, Any]] = []
    for relpath in tracked_dirty_paths:
        entries.append(_repo_entry_for_path(relpath=relpath, tracked_dirty=True, untracked=False))
    for relpath in untracked_paths:
        if relpath in tracked_dirty_paths:
            continue
        entries.append(_repo_entry_for_path(relpath=relpath, tracked_dirty=False, untracked=True))
    snap["tracked_dirty_paths"] = tracked_dirty_paths
    snap["untracked_paths"] = untracked_paths
    snap["dirty_path_count"] = len(entries)
    snap["entries"] = entries
    return snap


def _persist_repo_snapshot_file_copies(
    *,
    workflow_id: str,
    snapshot: Dict[str, Any],
    artifact_subdir: str,
    include_paths: Optional[set[str]] = None,
) -> Dict[str, Any]:
    """Persist bounded copies of selected dirty files for later inspection/recovery."""
    entries = snapshot.get("entries") if isinstance(snapshot.get("entries"), list) else []
    copies_root = (_repo_residue_root(workflow_id) / artifact_subdir).resolve()
    copies_root.mkdir(parents=True, exist_ok=True)
    copied = 0
    skipped = 0
    for row in entries:
        if not isinstance(row, dict):
            continue
        relpath = str(row.get("path") or "").strip()
        if not relpath:
            continue
        if include_paths is not None and relpath not in include_paths:
            continue
        abs_path = (_repo_root() / relpath).resolve()
        if not abs_path.exists() or not abs_path.is_file() or abs_path.is_symlink():
            row["copy_status"] = "skipped"
            row["copy_reason"] = "not_regular_file"
            skipped += 1
            continue
        size_bytes = int(row.get("size_bytes") or 0)
        if size_bytes > MAX_REPO_RESIDUE_FILE_BYTES:
            row["copy_status"] = "skipped"
            row["copy_reason"] = f"size_exceeds_{MAX_REPO_RESIDUE_FILE_BYTES}"
            skipped += 1
            continue
        try:
            dst = _safe_repo_artifact_child(copies_root, relpath)
            dst.parent.mkdir(parents=True, exist_ok=True)
            data = abs_path.read_bytes()
            with open(dst, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            row["copy_status"] = "saved"
            row["copy_artifact"] = _repo_relative_path(dst)
            copied += 1
        except Exception as exc:
            row["copy_status"] = "error"
            row["copy_reason"] = str(exc)
            skipped += 1
    snapshot["copied_file_count"] = int(copied)
    snapshot["skipped_file_count"] = int(skipped)
    return snapshot


def _repo_snapshot_path(workflow_id: str, name: str) -> Path:
    """Return a repo residue JSON artifact path for a workflow."""
    return (_repo_residue_root(workflow_id) / name).resolve()


def _capture_repo_baseline(state: Dict[str, Any]) -> Dict[str, Any]:
    """Capture the workflow-start repo dirty-state baseline."""
    wf = str(state.get("workflow_id") or "").strip()
    if not wf:
        return state
    info = state.get("repo_residue") if isinstance(state.get("repo_residue"), dict) else {}
    try:
        baseline = _collect_repo_dirty_snapshot()
        baseline = _persist_repo_snapshot_file_copies(
            workflow_id=wf,
            snapshot=baseline,
            artifact_subdir="baseline_files",
        )
        baseline_path = _repo_snapshot_path(wf, "baseline.json")
        _write_json_atomic(baseline_path, baseline)
        info.update(
            {
                "capture_status": "baseline_captured",
                "baseline_artifact": _repo_relative_path(baseline_path),
                "baseline_dirty_path_count": int(baseline.get("dirty_path_count") or 0),
                "capture_error": "",
            }
        )
        state["repo_residue"] = info
        _save_state(state)
        _append_jsonl(
            _events_file(wf),
            _event(
                wf,
                "workflow_repo_residue_baseline_captured",
                {
                    "dirty_path_count": int(baseline.get("dirty_path_count") or 0),
                    "git_available": bool(baseline.get("git_available")),
                },
            ),
        )
    except Exception as exc:
        info.update({"capture_status": "baseline_error", "capture_error": str(exc)})
        state["repo_residue"] = info
        _save_state(state)
    return state


def _tracked_patch_text(paths: List[str]) -> str:
    """Return a tracked-file patch for the provided repo-relative paths."""
    clean = [str(p or "").strip() for p in paths if str(p or "").strip()]
    if not clean:
        return ""
    cp = _run_git(["diff", "--binary", "--no-ext-diff", "HEAD", "--", *clean], timeout_s=60)
    if cp.returncode != 0:
        raise RuntimeError(str(cp.stderr or cp.stdout or "git diff failed").strip())
    return str(cp.stdout or "")


def _capture_nonclean_workflow_repo_residue(state: Dict[str, Any]) -> Dict[str, Any]:
    """Capture best-effort repo residue artifacts when a workflow ends non-cleanly."""
    wf = str(state.get("workflow_id") or "").strip()
    if not wf:
        return state
    info = state.get("repo_residue") if isinstance(state.get("repo_residue"), dict) else {}
    try:
        baseline_path = _repo_snapshot_path(wf, "baseline.json")
        baseline = _read_json(baseline_path)
        current = _collect_repo_dirty_snapshot()
        baseline_entries = baseline.get("entries") if isinstance(baseline.get("entries"), list) else []
        current_entries = current.get("entries") if isinstance(current.get("entries"), list) else []
        current_by_path = {
            str(row.get("path") or ""): row
            for row in current_entries
            if isinstance(row, dict) and str(row.get("path") or "").strip()
        }
        residue_delta = _repo_residue_helpers.summarize_repo_residue_delta(
            baseline_entries=baseline_entries,
            current_entries=current_entries,
        )
        changed_since_start_paths = list(residue_delta.get("changed_since_start_paths") or [])
        clean_at_start_changed_paths = list(residue_delta.get("clean_at_start_changed_paths") or [])
        preexisting_dirty_changed_paths = list(residue_delta.get("preexisting_dirty_changed_paths") or [])
        new_untracked_paths = list(residue_delta.get("new_untracked_paths") or [])
        current = _persist_repo_snapshot_file_copies(
            workflow_id=wf,
            snapshot=current,
            artifact_subdir="current_files",
            include_paths=set(changed_since_start_paths),
        )
        current_path = _repo_snapshot_path(wf, "failure.json")
        _write_json_atomic(current_path, current)
        tracked_patch_paths = [
            relpath
            for relpath in clean_at_start_changed_paths
            if bool((current_by_path.get(relpath) or {}).get("tracked_dirty"))
        ]
        patch_path = (_repo_residue_root(wf) / "workflow_residue.patch").resolve()
        patch_artifact = ""
        patch_text = _tracked_patch_text(tracked_patch_paths)
        if patch_text.strip():
            patch_path.write_text(patch_text, encoding="utf-8")
            patch_artifact = _repo_relative_path(patch_path)
        summary = {
            "schema_version": SCHEMA_REPO_RESIDUE_SUMMARY,
            "captured_utc": _utc_now_iso(),
            "workflow_id": wf,
            "status": str(state.get("status") or ""),
            "baseline_artifact": _repo_relative_path(baseline_path) if baseline_path.exists() else "",
            "current_artifact": _repo_relative_path(current_path),
            "patch_artifact": patch_artifact,
            "baseline_dirty_path_count": int(baseline.get("dirty_path_count") or 0),
            "current_dirty_path_count": int(current.get("dirty_path_count") or 0),
            "changed_since_start_count": len(changed_since_start_paths),
            "changed_since_start_paths": sorted(changed_since_start_paths),
            "clean_at_start_changed_paths": sorted(clean_at_start_changed_paths),
            "preexisting_dirty_changed_paths": sorted(preexisting_dirty_changed_paths),
            "new_untracked_paths": sorted(new_untracked_paths),
        }
        summary_path = _repo_snapshot_path(wf, "summary.json")
        _write_json_atomic(summary_path, summary)
        info.update(
            {
                "capture_status": "captured",
                "current_artifact": _repo_relative_path(current_path),
                "summary_artifact": _repo_relative_path(summary_path),
                "patch_artifact": patch_artifact,
                "changed_since_start_count": len(changed_since_start_paths),
                "capture_error": "",
            }
        )
        state["repo_residue"] = info
        _save_state(state)
        _append_jsonl(
            _events_file(wf),
            _event(
                wf,
                "workflow_repo_residue_captured",
                {
                    "changed_since_start_count": len(changed_since_start_paths),
                    "patch_artifact": patch_artifact,
                },
            ),
        )
    except Exception as exc:
        info.update({"capture_status": "error", "capture_error": str(exc)})
        state["repo_residue"] = info
        _save_state(state)
    return state


def _capture_failed_workflow_repo_residue(state: Dict[str, Any]) -> Dict[str, Any]:
    """Backward-compatible alias for non-clean repo residue capture."""
    return _capture_nonclean_workflow_repo_residue(state)


def _validate_binding(binding: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(binding, dict):
        raise AgentOrchestrationError("binding must be an object")
    bt = str(binding.get("type") or "").strip().lower()
    bid = str(binding.get("id") or "").strip()
    if bt not in ("task_id", "ledger_uid"):
        raise AgentOrchestrationError("binding.type must be task_id|ledger_uid")
    if bt == "task_id" and not TASK_ID_RE.match(bid):
        raise AgentOrchestrationError("task_id must match ^[a-z0-9]{3}$")
    if bt == "ledger_uid" and not UID_RE.match(bid):
        raise AgentOrchestrationError("invalid ledger_uid")
    return {"type": bt, "id": bid}


def _validate_subphases(raw: Any) -> List[str]:
    if not isinstance(raw, list) or not raw:
        return []
    out: List[str] = []
    for x in raw:
        s = str(x or "").strip()
        if not s:
            continue
        if s not in out:
            out.append(s)
    return out


def _subphases_from_options(opts: Dict[str, Any]) -> List[str]:
    explicit = _validate_subphases(opts.get("subphases"))
    if explicit:
        return explicit
    try:
        n = int(opts.get("pis_subphase_count") or 0)
    except Exception:
        n = 0
    if n > 1:
        return [f"P{i}" for i in range(1, min(32, n) + 1)]
    return list(DEFAULT_SUBPHASES)


def _pis_meta_defaults(binding: Dict[str, str]) -> Dict[str, str]:
    if str(binding.get("type") or "") != "ledger_uid":
        return {"phase_label": "", "objective": ""}
    uid = str(binding.get("id") or "")
    try:
        ledger = ChangeLedger(repo_root=_repo_root())
        item = ledger.read_item(uid)
    except Exception:
        return {"phase_label": f"PIS {uid}", "objective": ""}
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    title = str(meta.get("title") or "").strip()
    pis_excerpt = str(meta.get("pis_excerpt") or "").strip()
    return {
        "phase_label": title or f"PIS {uid}",
        "objective": pis_excerpt or title,
    }


def _normalize_workflow_objective(
    *,
    binding: Dict[str, str],
    phase_label: str,
    objective: str,
) -> str:
    obj = str(objective or "").strip()
    if not obj:
        return ""
    if str(binding.get("type") or "") != "ledger_uid":
        return obj
    m = LEGACY_WORKFLOW_OBJECTIVE_RE.match(obj)
    if not m:
        return obj
    uid = str(binding.get("id") or "").strip() or str(m.group("uid") or "").strip()
    phase = str(m.group("phase") or "").strip() or str(phase_label or "").strip()
    if not uid:
        return obj
    if not phase:
        return f"Implement and verify {uid}."
    return f"Implement and verify {uid} with {phase}."


def _workflow_phase_uses_canonical_objective(phase_label: str) -> bool:
    phase = str(phase_label or "").strip()
    if not phase:
        return False
    return re.match(r"^WF-\d+\b", phase, flags=re.IGNORECASE) is not None


def _canonical_workflow_objective_for_binding(binding: Dict[str, str], phase_label: str) -> str:
    if str(binding.get("type") or "") != "ledger_uid":
        return ""
    uid = str(binding.get("id") or "").strip()
    phase = str(phase_label or "").strip()
    if not uid:
        return ""
    if not phase:
        return f"Implement and verify {uid}."
    return f"Implement and verify {uid} with {phase}."


def _normalize_heartbeat_interval_s(raw: Any) -> int:
    try:
        v = int(raw)
    except Exception:
        v = DEFAULT_HEARTBEAT_INTERVAL_S
    return max(1, min(3600, v))


def _normalize_timeout_s(raw: Any) -> int:
    try:
        v = int(raw)
    except Exception:
        v = _default_role_timeout_s()
    return max(30, min(6 * 60 * 60, v))


def _normalize_failure_retry_bound(raw: Any) -> int:
    try:
        v = int(raw)
    except Exception:
        v = _default_failure_retry_bound()
    return max(1, min(5, v))


def _normalize_stuck_retry_limit(raw: Any) -> int:
    try:
        v = int(raw)
    except Exception:
        v = _default_stuck_retry_limit()
    return max(1, min(5, v))


def _normalize_workflow_control_mode(raw: Any) -> str:
    value = str(raw or "").strip().lower()
    if value in {"agentic", "llm", "prompt"}:
        return "agentic"
    return "deterministic"


def current_runtime_defaults() -> Dict[str, Any]:
    """Return the live workflow-control defaults visible to new workflows."""
    return {
        "max_infra_retries": int(_default_max_infra_retries()),
        "turn_timeout_s": int(_default_role_timeout_s()),
        "heartbeat_interval_s": int(DEFAULT_HEARTBEAT_INTERVAL_S),
        "role_slice_timeouts": _default_role_slice_timeouts(),
        "role_progress_timeouts": _default_role_progress_timeouts(),
        "role_output_grace_s": int(_default_role_output_grace_s()),
        "failure_retry_bound": int(_default_failure_retry_bound()),
        "activity_repeat_bound": int(_default_activity_repeat_bound()),
        "stuck_retry_limit": int(_default_stuck_retry_limit()),
        "control_mode": _normalize_workflow_control_mode(_default_workflow_control_mode()),
    }


def _default_role_slice_timeouts() -> Dict[str, int]:
    return {
        "generalist": _default_generalist_slice_timeout_s(),
        "builder": _default_builder_slice_timeout_s(),
        "assurer": _default_verifier_slice_timeout_s(),
    }


def _normalize_role_slice_timeouts(raw: Any) -> Dict[str, int]:
    out = _default_role_slice_timeouts()
    if not isinstance(raw, dict):
        return out
    alias_map = {
        "generalist": "generalist",
        "orchestrator": "generalist",
        "builder": "builder",
        "assurer": "assurer",
        "verifier": "assurer",
    }
    for raw_key, raw_value in raw.items():
        key = alias_map.get(str(raw_key or "").strip().lower())
        if not key:
            continue
        try:
            out[key] = max(30, min(3600, int(raw_value)))
        except Exception:
            continue
    return out


def _role_slice_timeout_s(state: Dict[str, Any], role: str, requested_timeout_s: Any = None) -> int:
    cap = _normalize_timeout_s(requested_timeout_s if requested_timeout_s is not None else state.get("turn_timeout_s"))
    role_timeouts = _normalize_role_slice_timeouts(state.get("role_slice_timeouts"))
    fallback = _default_role_slice_timeouts().get(str(role or "").strip().lower(), _default_builder_slice_timeout_s())
    return max(30, min(cap, int(role_timeouts.get(str(role or "").strip().lower(), fallback))))


def _default_role_progress_timeouts() -> Dict[str, int]:
    return {
        "generalist": _default_generalist_progress_timeout_s(),
        "builder": _default_builder_progress_timeout_s(),
        "assurer": _default_verifier_progress_timeout_s(),
    }


def _normalize_role_progress_timeouts(raw: Any) -> Dict[str, int]:
    out = _default_role_progress_timeouts()
    if not isinstance(raw, dict):
        return out
    alias_map = {
        "generalist": "generalist",
        "orchestrator": "generalist",
        "builder": "builder",
        "assurer": "assurer",
        "verifier": "assurer",
    }
    for raw_key, raw_value in raw.items():
        key = alias_map.get(str(raw_key or "").strip().lower())
        if not key:
            continue
        try:
            out[key] = max(30, min(1800, int(raw_value)))
        except Exception:
            continue
    return out


def _role_progress_timeout_s(state: Dict[str, Any], role: str) -> int:
    role_timeouts = _normalize_role_progress_timeouts(state.get("role_progress_timeouts"))
    fallback = _default_role_progress_timeouts().get(str(role or "").strip().lower(), _default_builder_progress_timeout_s())
    return max(30, min(int(state.get("active_slice_timeout_s") or state.get("turn_timeout_s") or fallback), int(role_timeouts.get(str(role or "").strip().lower(), fallback))))


def _normalize_role_output_grace_s(raw: Any) -> int:
    try:
        value = int(raw)
    except Exception:
        value = _default_role_output_grace_s()
    return max(15, min(300, value))


def _role_waiting_notice_s(progress_timeout_s: int) -> int:
    return max(15, min(60, int(progress_timeout_s) // 3 or 15))


def _deadline_utc(started_utc: str, timeout_s: int) -> str:
    started = _parse_iso_utc(started_utc)
    if started is None:
        return ""
    return (started + _dt.timedelta(seconds=max(1, int(timeout_s)))).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _compact_progress_text(text: str, *, max_chars: int = 220) -> str:
    compact = " ".join(str(text or "").split()).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _normalize_command_text(text: str, *, max_chars: int = 200) -> str:
    compact = " ".join(str(text or "").replace("\n", " ").split()).strip()
    if len(compact) <= max_chars:
        return compact
    return compact[: max_chars - 3].rstrip() + "..."


def _extract_command_fingerprint_text(note: str) -> str:
    text = str(note or "").strip()
    if not text:
        return ""
    for pattern in (
        r"<cmd>(.*?)</cmd>",
        r"`((?:\./gradlew|gradlew|pytest|python(?:3)?\s+-m|python(?:3)?|bash|sh|adb|sdkmanager|emulator|npm|yarn|node|git)\b[^`]*)`",
        r"((?:\./gradlew|gradlew|pytest|python(?:3)?\s+-m|python(?:3)?|bash|sh|adb|sdkmanager|emulator|npm|yarn|node|git)\b[^\n]*)",
    ):
        m = re.search(pattern, text, flags=re.IGNORECASE | re.DOTALL)
        if not m:
            continue
        return _normalize_command_text(m.group(1))
    return ""


def _activity_repeat_bound(state: Dict[str, Any]) -> int:
    try:
        configured = int(state.get("failure_retry_bound") or _default_failure_retry_bound())
    except Exception:
        configured = _default_failure_retry_bound()
    return max(_default_activity_repeat_bound(), min(5, configured + 1))


def _activity_fingerprint(*, role: str, turn: str, category: str, summary: str) -> Dict[str, str]:
    compact = _compact_progress_text(summary, max_chars=240)
    seed = f"{str(role or '').strip().lower()}::{str(turn or '').strip().lower()}::{str(category or '').strip().lower()}::{compact.lower()}"
    return {
        "fingerprint": hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],
        "summary": compact,
        "category": str(category or "").strip().lower(),
    }


def _record_activity_fingerprint(
    *,
    state: Dict[str, Any],
    role: str,
    turn: str,
    category: str,
    summary: str,
) -> Dict[str, Any]:
    row = _activity_fingerprint(role=role, turn=turn, category=category, summary=summary)
    fp = str(row.get("fingerprint") or "").strip()
    records = state.get("role_activity_fingerprints")
    fingerprint_state = dict(records) if isinstance(records, dict) else {}
    previous = fingerprint_state.get(fp) if isinstance(fingerprint_state.get(fp), dict) else {}
    count = int(previous.get("count") or 0) + 1
    entry = {
        "role": role,
        "turn": turn,
        "category": str(row.get("category") or ""),
        "count": count,
        "summary": str(row.get("summary") or ""),
        "updated_utc": _utc_now_iso(),
    }
    fingerprint_state[fp] = entry
    state["role_activity_fingerprints"] = fingerprint_state
    _save_state(state)
    _append_jsonl(
        _events_file(str(state.get("workflow_id") or "")),
        _event(
            str(state.get("workflow_id") or ""),
            "role_activity_fingerprint",
            {
                "role": role,
                "turn": turn,
                "category": str(entry.get("category") or ""),
                "fingerprint": fp,
                "count": count,
                "summary": str(entry.get("summary") or ""),
            },
        ),
    )
    return {
        "fingerprint": fp,
        "count": count,
        "summary": str(entry.get("summary") or ""),
        "category": str(entry.get("category") or ""),
    }


def _stuck_escalation_key(*, role: str, turn: str, fingerprint: str) -> str:
    normalized_fingerprint = str(fingerprint or "").strip().lower() or "unknown"
    return "::".join(
        [
            str(role or "").strip().lower() or "unknown",
            str(turn or "").strip().lower() or "unknown",
            normalized_fingerprint,
        ]
    )


def _record_stuck_escalation(
    *,
    state: Dict[str, Any],
    role: str,
    turn: str,
    fingerprint: str,
    summary: str,
) -> Dict[str, Any]:
    records = state.get("stuck_escalation_counts")
    escalation_state = dict(records) if isinstance(records, dict) else {}
    key = _stuck_escalation_key(role=role, turn=turn, fingerprint=fingerprint)
    previous = escalation_state.get(key) if isinstance(escalation_state.get(key), dict) else {}
    count = int(previous.get("count") or 0) + 1
    row = {
        "role": str(role or "").strip().lower(),
        "turn": str(turn or "").strip().lower(),
        "fingerprint": str(fingerprint or "").strip().lower(),
        "summary": _compact_progress_text(summary, max_chars=240),
        "count": count,
        "updated_utc": _utc_now_iso(),
    }
    escalation_state[key] = row
    state["stuck_escalation_counts"] = escalation_state
    _save_state(state)
    return row


def _codex_worker_pid(*, state: Dict[str, Any], role: str) -> int:
    binding = state.get("binding") if isinstance(state.get("binding"), dict) else {}
    mgr_factory = getattr(_agents, "_codex_manager", None)
    if not callable(mgr_factory):
        return 0
    try:
        mgr = mgr_factory()
        worker = mgr.get_worker(binding=dict(binding), role=role)
    except Exception:
        return 0
    try:
        return max(0, int(worker.get("pid") or 0))
    except Exception:
        return 0


def _sample_process_tree(pid: int) -> List[Dict[str, Any]]:
    if int(pid) <= 0:
        return []
    try:
        cp = subprocess.run(
            ["ps", "-eo", "pid=,ppid=,etimes=,comm=,args="],
            cwd=str(_repo_root()),
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except Exception:
        return []
    if int(cp.returncode or 1) != 0:
        return []
    rows: Dict[int, Dict[str, Any]] = {}
    children: Dict[int, List[int]] = {}
    for raw in str(cp.stdout or "").splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        m = re.match(r"^(\d+)\s+(\d+)\s+(\d+)\s+(\S+)\s+(.*)$", line)
        if not m:
            continue
        row_pid = int(m.group(1))
        row_ppid = int(m.group(2))
        row = {
            "pid": row_pid,
            "ppid": row_ppid,
            "elapsed_s": int(m.group(3)),
            "comm": str(m.group(4) or "").strip(),
            "args": _normalize_command_text(m.group(5), max_chars=240),
        }
        rows[row_pid] = row
        children.setdefault(row_ppid, []).append(row_pid)
    if int(pid) not in rows:
        return []
    seen: set[int] = set()
    stack = [int(pid)]
    ordered: List[Dict[str, Any]] = []
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        row = rows.get(current)
        if row:
            ordered.append(dict(row))
        for child in sorted(children.get(current, []), reverse=True):
            stack.append(int(child))
    ordered.sort(key=lambda row: (int(row.get("elapsed_s") or 0), int(row.get("pid") or 0)), reverse=True)
    return ordered


def _summarize_wait_processes(processes: List[Dict[str, Any]]) -> str:
    interesting: List[str] = []
    for row in processes:
        comm = str(row.get("comm") or "").strip()
        args = str(row.get("args") or "").strip()
        if not args:
            continue
        lower = f"{comm} {args}".lower()
        if any(token in lower for token in ("codex", "script -qefc")):
            continue
        short_args = args
        if comm and short_args.startswith(comm):
            short_args = short_args[len(comm):].strip()
        summary = comm
        if short_args:
            summary += f" {_normalize_command_text(short_args, max_chars=110)}"
        elapsed_s = int(row.get("elapsed_s") or 0)
        if elapsed_s > 0:
            summary += f" ({elapsed_s}s)"
        interesting.append(summary.strip())
        if len(interesting) >= 3:
            break
    if not interesting:
        return ""
    return "Waiting on local commands: " + "; ".join(interesting)


def _describe_role_wait_state(*, state: Dict[str, Any], role: str) -> Dict[str, Any]:
    pid = _codex_worker_pid(state=state, role=role)
    processes = _sample_process_tree(pid)
    summary = _summarize_wait_processes(processes) or f"Waiting for {role} command output."
    fingerprint_source = summary
    if processes:
        fingerprint_source = " | ".join(
            f"{str(row.get('comm') or '').strip()}::{str(row.get('args') or '').strip()}"
            for row in processes[:4]
        )
    row = _activity_fingerprint(role=role, turn=str(state.get("active_turn_name") or state.get("turn") or ""), category="wait_snapshot", summary=fingerprint_source)
    return {
        "pid": int(pid),
        "summary": summary,
        "fingerprint": str(row.get("fingerprint") or ""),
        "fingerprint_summary": str(row.get("summary") or ""),
        "processes": processes[:6],
    }


def _repo_progress_snapshot() -> Dict[str, Any]:
    paths: List[str] = []
    try:
        tracked_cp = _run_git(["diff", "--name-only", "-z", "HEAD", "--"])
        untracked_cp = _run_git(["ls-files", "--others", "--exclude-standard", "-z"])
    except Exception as exc:
        return {
            "marker": "",
            "paths": [],
            "path_count": 0,
            "summary": "",
            "error": str(exc),
        }
    tracked_paths = _git_paths_from_nul(tracked_cp.stdout if tracked_cp.returncode == 0 else "")
    untracked_paths = _git_paths_from_nul(untracked_cp.stdout if untracked_cp.returncode == 0 else "")
    seen: set[str] = set()
    for rel in tracked_paths + untracked_paths:
        normalized = Path(str(rel or "").strip()).as_posix().lstrip("./")
        if not normalized or normalized.startswith("../") or normalized in seen:
            continue
        seen.add(normalized)
        paths.append(normalized)
    paths.sort()
    if not paths:
        return {"marker": "", "paths": [], "path_count": 0, "summary": "", "error": ""}
    marker = hashlib.sha256("\n".join(paths).encode("utf-8")).hexdigest()[:16]
    preview = ", ".join(paths[:3])
    if len(paths) > 3:
        preview += f" (+{len(paths) - 3} more)"
    return {
        "marker": marker,
        "paths": paths,
        "path_count": len(paths),
        "summary": preview,
        "error": "",
    }


def _observe_repo_progress_for_turn(*, state: Dict[str, Any], role: str) -> Dict[str, Any]:
    snapshot = _repo_progress_snapshot()
    current_marker = str(snapshot.get("marker") or "")
    baseline_marker = str(state.get("active_repo_progress_baseline_marker") or "")
    previous_marker = str(state.get("active_repo_progress_marker") or "")
    previously_seen = bool(state.get("active_repo_progress_seen"))
    changed = current_marker != previous_marker
    has_progress = bool(current_marker != baseline_marker)
    ever_progress = bool(previously_seen or has_progress)
    state["active_repo_progress_marker"] = current_marker
    state["active_repo_progress_path_count"] = int(snapshot.get("path_count") or 0)
    state["active_repo_progress_seen"] = ever_progress
    _save_state(state)
    note = ""
    if has_progress and str(snapshot.get("summary") or "").strip():
        note = f"Observed repo changes while {role} was running: {str(snapshot.get('summary') or '').strip()}."
    return {
        "marker": current_marker,
        "changed": changed,
        "has_progress": has_progress,
        "ever_progress": ever_progress,
        "path_count": int(snapshot.get("path_count") or 0),
        "note": note,
        "summary": str(snapshot.get("summary") or "").strip(),
        "error": str(snapshot.get("error") or "").strip(),
    }


def _is_generic_progress_text(text: str) -> bool:
    compact = _compact_progress_text(text).lower()
    if not compact:
        return True
    generic = {
        "working",
        "still working",
        "continuing",
        "continuing to work",
        "thinking",
        "still thinking",
        "processing",
        "making progress",
    }
    return compact in generic


def _state_digest(state: Dict[str, Any]) -> str:
    data = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _workflow_digest_summary_text(state: Dict[str, Any]) -> str:
    latest_host = state.get("latest_host_verification_status") if isinstance(state.get("latest_host_verification_status"), dict) else {}
    intent_report = str(state.get("intent_validation_report") or "").strip()
    reports = state.get("reports") if isinstance(state.get("reports"), list) else []
    candidates = [
        str(state.get("last_error") or "").strip(),
        str(state.get("stuck_reason") or "").strip(),
        str(state.get("active_wait_reason") or "").strip(),
        str(state.get("active_progress_note") or "").strip(),
        str(latest_host.get("progress_note") or latest_host.get("message") or "").strip(),
        str(reports[-1] if reports else "").strip(),
    ]
    if intent_report:
        first_line = next((line.strip() for line in intent_report.splitlines() if line.strip()), "")
        if first_line:
            candidates.append(first_line)
    for candidate in candidates:
        compact = _compact_progress_text(candidate, max_chars=280)
        if compact:
            return compact
    return ""


def _workflow_digest_headline(state: Dict[str, Any]) -> str:
    status = str(state.get("status") or "").strip().lower()
    turn = str(state.get("turn") or "").strip()
    role = str(state.get("active_role") or state.get("stuck_role") or "").strip().lower()
    role_label = _actor_display(role) if role else ""
    status_detail = str(state.get("status_detail") or "").strip().lower()
    if status == "completed":
        verdict = str(state.get("verifier_verdict") or "").strip().upper()
        if verdict and verdict != "UNKNOWN":
            return f"Workflow completed with verifier {verdict}"
        return "Workflow completed"
    if status == "failed":
        if status_detail in {"intent_validation_failed", "evidence_gap", "intent_validation_unknown"}:
            return "Workflow ended without clean proof"
        target = role_label or turn or "workflow"
        return f"Workflow failed in {target}"
    if status == "cancelled":
        return "Workflow cancelled"
    if status == "interrupted":
        return "Workflow interrupted before a safe checkpoint"
    if bool(state.get("stop_after_current_requested")):
        return "Stopping after current workflow step"
    if turn == "host_verification" or "host_verification" in status_detail:
        return "Waiting on host verification"
    if turn == "se_unblock" or state.get("stuck_context"):
        target = role_label or "workflow"
        return f"Recovering stuck {target}"
    if state.get("active_wait_reason"):
        target = role_label or "worker"
        return f"{target} waiting on command output"
    if role_label:
        return f"{role_label} slice active"
    if turn == "intent_validation":
        return "Intent validation running"
    if turn == "se_tasking":
        return "Preparing builder brief"
    if turn == "se_decide":
        return "Applying verifier decision"
    return "Workflow running"


def _workflow_digest_next_action(state: Dict[str, Any]) -> str:
    status = str(state.get("status") or "").strip().lower()
    turn = str(state.get("turn") or "").strip()
    verdict = str(state.get("verifier_verdict") or "").strip().upper()
    if status == "completed":
        return "Review the final intent validation summary."
    if status == "failed":
        if str(state.get("status_detail") or "").strip().lower() in {
            "intent_validation_failed",
            "evidence_gap",
            "intent_validation_unknown",
        }:
            return "Change the proof path or scope before rerunning; this result did not close cleanly."
        return "Inspect the blocker and rerun only after changing the proof path or scope."
    if status == "cancelled":
        return "Restart the workflow only if the objective still matters."
    if status == "interrupted":
        return "Resume through control recovery first; do not replay the interrupted role turn blindly."
    if bool(state.get("stop_after_current_requested")):
        return "Let the active step finish; the workflow will stop at the next checkpoint."
    if turn == "builder_run":
        return "Builder should land one bounded checkpoint or summarize the blocker."
    if turn == "verifier_run":
        return "Verifier should return a verdict or one typed host verification request."
    if turn == "host_verification":
        return "Wait for the typed host proof result; do not retry the same local path."
    if turn == "se_unblock":
        return "Runtime should choose a narrower retry or stop the phase."
    if turn == "intent_validation":
        return "Finish the workflow with a clear evidence-based verdict."
    if verdict == "PASS":
        return "Advance to the next subphase or close with intent validation."
    if verdict == "FAIL":
        return "Fix the concrete defect in the next bounded slice or stop the phase."
    return "Keep the next slice small and evidence-driven."


def _build_workflow_digest(state: Dict[str, Any]) -> Dict[str, Any]:
    wf = str(state.get("workflow_id") or "").strip()
    latest_host_status = state.get("latest_host_verification_status") if isinstance(state.get("latest_host_verification_status"), dict) else {}
    reports = state.get("reports") if isinstance(state.get("reports"), list) else []
    files: List[str] = []
    builder_output = str(state.get("builder_output") or "").strip()
    for line in _section_lines(_extract_markdown_section(builder_output, "Files Changed")):
        for path in _extract_paths_from_line(line):
            if path not in files:
                files.append(path)
    digest = {
        "schema_version": SCHEMA_WORKFLOW_DIGEST,
        "workflow_id": wf,
        "updated_utc": str(state.get("updated_utc") or ""),
        "status": str(state.get("status") or ""),
        "status_detail": str(state.get("status_detail") or ""),
        "turn": str(state.get("turn") or ""),
        "active_role": str(state.get("active_role") or ""),
        "headline": _workflow_digest_headline(state),
        "summary": _workflow_digest_summary_text(state),
        "next_action": _workflow_digest_next_action(state),
        "verifier_verdict": str(state.get("verifier_verdict") or ""),
        "last_meaningful_progress_utc": str(state.get("last_meaningful_progress_utc") or ""),
        "last_meaningful_progress_event": str(state.get("last_meaningful_progress_event") or ""),
        "active_worker_pid": int(state.get("active_worker_pid") or 0),
        "active_wait_reason": str(state.get("active_wait_reason") or ""),
        "active_progress_note": str(state.get("active_progress_note") or ""),
        "files_changed": files[:12],
        "recent_reports": [str(x or "").strip() for x in reports[-4:] if str(x or "").strip()],
        "latest_host_verification": {
            "status": str(latest_host_status.get("status") or ""),
            "phase": str(latest_host_status.get("phase") or ""),
            "progress_note": str(latest_host_status.get("progress_note") or latest_host_status.get("message") or ""),
            "updated_utc": str(latest_host_status.get("updated_utc") or ""),
        },
    }
    residue = state.get("repo_residue") if isinstance(state.get("repo_residue"), dict) else {}
    if residue:
        digest["repo_residue"] = {
            "capture_status": str(residue.get("capture_status") or ""),
            "changed_since_start_count": int(residue.get("changed_since_start_count") or 0),
            "summary_artifact": str(residue.get("summary_artifact") or ""),
        }
    checkpoint = state.get("checkpoint_boundary") if isinstance(state.get("checkpoint_boundary"), dict) else {}
    if checkpoint:
        digest["checkpoint_boundary"] = {
            "boundary_state": str(checkpoint.get("boundary_state") or ""),
            "safe_to_resume": bool(checkpoint.get("safe_to_resume")),
            "resume_policy": str(checkpoint.get("resume_policy") or ""),
        }
    if bool(state.get("stop_after_current_requested")):
        digest["stop_after_current_requested"] = True
    return digest


def _persist_workflow_digest(state: Dict[str, Any]) -> None:
    wf = str(state.get("workflow_id") or "").strip()
    if not wf:
        return
    try:
        _write_json_atomic(_workflow_digest_file(wf), state.get("workflow_digest") if isinstance(state.get("workflow_digest"), dict) else {})
    except Exception:
        return


def _new_workflow_id(binding: Dict[str, str], objective: str) -> str:
    seed = f"{binding['type']}::{binding['id']}::{objective}::{_utc_now_iso()}"
    short = hashlib.sha256(seed.encode("utf-8")).hexdigest()[:8]
    return f"wf-{_utc_compact().lower()}-{short}"


def _event(workflow_id: str, event_type: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "schema_version": SCHEMA_EVENT,
        "utc": _utc_now_iso(),
        "workflow_id": workflow_id,
        "event": str(event_type),
        "payload": dict(payload or {}),
    }


def _checkpoint_boundary_snapshot(state: Dict[str, Any]) -> Dict[str, Any]:
    status = str(state.get("status") or "").strip().lower()
    turn = str(state.get("turn") or "").strip()
    active_turn = str(state.get("active_turn_name") or turn).strip()
    boundary_state = "safe_checkpoint" if status == "idle" else "in_flight_ambiguous"
    safe_to_resume = status == "idle"
    resume_policy = "resume_next_turn" if safe_to_resume else "requires_recovery_review"
    interrupted = state.get("interrupted_context") if isinstance(state.get("interrupted_context"), dict) else {}
    if status == "interrupted":
        boundary_state = "recovery_checkpoint"
        safe_to_resume = False
        resume_policy = "control_recovery_before_role_retry"
    elif status == "cancel_requested":
        boundary_state = "stop_after_current_requested"
        safe_to_resume = False
        resume_policy = "finish_current_step_then_stop"
    elif status in {"completed", "cancelled", "failed"}:
        boundary_state = "terminal"
        safe_to_resume = False
        resume_policy = "terminal"
    return {
        "boundary_state": boundary_state,
        "safe_to_resume": safe_to_resume,
        "resume_policy": resume_policy,
        "next_turn": turn,
        "active_turn": active_turn,
        "active_role": str(state.get("active_role") or state.get("active_turn_role") or "").strip(),
        "last_step_utc": str(state.get("last_step_utc") or "").strip(),
        "active_turn_started_utc": str(state.get("active_turn_started_utc") or "").strip(),
        "stop_after_current_requested": bool(state.get("stop_after_current_requested")),
        "interrupted_turn": str(interrupted.get("interrupted_turn") or ""),
        "interrupted_role": str(interrupted.get("interrupted_role") or ""),
        "interrupted_utc": str(interrupted.get("interrupted_utc") or ""),
        "residue_summary_artifact": str(interrupted.get("residue_summary_artifact") or ""),
    }


def _save_state(state: Dict[str, Any]) -> Dict[str, Any]:
    wf = str(state.get("workflow_id") or "").strip()
    if not wf:
        raise AgentOrchestrationError("state missing workflow_id")
    state["schema_version"] = SCHEMA_STATE
    state["updated_utc"] = _utc_now_iso()
    state["checkpoint_boundary"] = _checkpoint_boundary_snapshot(state)
    state["workflow_digest"] = _build_workflow_digest(state)
    state["state_hash"] = _state_digest({k: v for k, v in state.items() if k != "state_hash"})
    _write_json_atomic(_state_file(wf), state)
    _persist_workflow_digest(state)
    return state


def _load_state(workflow_id: str) -> Dict[str, Any]:
    wf = str(workflow_id or "").strip()
    if not wf:
        raise AgentOrchestrationError("workflow_id is required")
    state = _read_json(_state_file(wf))
    if not state:
        raise FileNotFoundError(f"workflow not found: {wf}")
    if str(state.get("schema_version") or "") != SCHEMA_STATE:
        raise AgentOrchestrationError("unsupported workflow schema")
    return state


def _tail_events(workflow_id: str, *, limit: int = 120) -> List[Dict[str, Any]]:
    return _read_jsonl(_events_file(workflow_id), limit=limit)


def _workflow_is_terminal(status: str) -> bool:
    return str(status or "").strip().lower() in {"completed", "cancelled", "failed"}


def _maybe_reconcile_interrupted_restart(state: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    status = str(state.get("status") or "").strip().lower()
    if status != "running":
        return state, False
    wf = str(state.get("workflow_id") or "").strip()
    interrupted_turn = str(state.get("active_turn_name") or state.get("turn") or "").strip()
    interrupted_role = str(state.get("active_role") or state.get("active_turn_role") or "").strip().lower()
    reason = f"Workflow restart left {interrupted_turn or 'the active step'} without a persisted safe checkpoint."
    fingerprint = _failure_fingerprint(
        role=interrupted_role or "system",
        turn=interrupted_turn or str(state.get("turn") or ""),
        error=reason,
    )
    state = _capture_nonclean_workflow_repo_residue(state)
    residue = state.get("repo_residue") if isinstance(state.get("repo_residue"), dict) else {}
    retry_limit = _normalize_stuck_retry_limit(state.get("stuck_retry_limit"))
    slice_started_utc = str(state.get("active_turn_started_utc") or "")
    slice_timeout_s = int(state.get("active_slice_timeout_s") or 0)
    state["status"] = "interrupted"
    state["status_detail"] = "interrupted_requires_recovery"
    state["last_error"] = reason
    state["turn"] = "se_unblock"
    state["active_role"] = ""
    state["active_wait_reason"] = ""
    state["active_progress_note"] = ""
    state["active_worker_pid"] = 0
    state["active_worker_summary"] = ""
    state["active_wait_repeat_count"] = 0
    state["active_host_verification_progress_fingerprint"] = ""
    state["active_repo_progress_baseline_marker"] = ""
    state["active_repo_progress_marker"] = ""
    state["active_repo_progress_path_count"] = 0
    state["active_repo_progress_seen"] = False
    state["active_slice_timeout_s"] = 0
    state["active_slice_deadline_utc"] = ""
    state["active_turn_name"] = "se_unblock"
    state["interrupted_context"] = {
        "interrupted_utc": _utc_now_iso(),
        "interrupted_status": status,
        "interrupted_turn": interrupted_turn,
        "interrupted_role": interrupted_role,
        "resume_policy": "control_recovery_before_role_retry",
        "residue_summary_artifact": str(residue.get("summary_artifact") or ""),
        "residue_capture_status": str(residue.get("capture_status") or ""),
        "reason": reason,
    }
    state["stuck_role"] = interrupted_role
    state["stuck_reason"] = reason
    state["stuck_context"] = {
        "role": interrupted_role,
        "turn": interrupted_turn,
        "reason": reason,
        "detail": "interrupted_restart",
        "error": reason,
        "response_excerpt": "",
        "fingerprint": str(fingerprint.get("fingerprint") or ""),
        "repeat_count": 1,
        "fingerprint_summary": str(fingerprint.get("summary") or ""),
        "last_progress_note": str(state.get("last_progress_note") or ""),
        "last_meaningful_progress_utc": str(state.get("last_meaningful_progress_utc") or ""),
        "slice_started_utc": slice_started_utc,
        "slice_timeout_s": slice_timeout_s,
        "wait_reason": "",
        "worker_pid": 0,
        "worker_summary": "",
        "wait_repeat_count": 0,
        "workflow_repeat_count": 1,
        "retry_limit": retry_limit,
    }
    state = _save_state(state)
    _append_jsonl(
        _events_file(wf),
        _event(
            wf,
            "workflow_interrupted_reconciled",
            {
                "interrupted_turn": interrupted_turn,
                "interrupted_role": interrupted_role,
                "resume_policy": "control_recovery_before_role_retry",
                "reason": reason,
                "residue_summary_artifact": str(residue.get("summary_artifact") or ""),
            },
        ),
    )
    return state, True


def _resume_noop_result(*, workflow_id: str, state: Dict[str, Any], reason: str) -> Dict[str, Any]:
    status = str(state.get("status") or "").strip().lower()
    return {
        "ok": status != "failed",
        "workflow_id": workflow_id,
        "state": state,
        "steps_run": 0,
        "done": _workflow_is_terminal(status),
        "noop_reason": str(reason or "").strip() or "noop",
    }


def _set_runtime_detail(
    state: Dict[str, Any],
    *,
    detail: str,
    role: str = "",
    turn: str = "",
    started_utc: str = "",
    timeout_s: Optional[int] = None,
    progress_note: Optional[str] = None,
    wait_reason: Optional[str] = None,
    meaningful_event: str = "",
    stuck_reason: Optional[str] = None,
    clear_stuck: bool = False,
) -> Dict[str, Any]:
    state["status_detail"] = str(detail or "").strip()
    if role:
        state["active_role"] = str(role).strip()
    if turn:
        state["active_turn_name"] = str(turn).strip()
    if started_utc:
        state["active_turn_started_utc"] = str(started_utc).strip()
    if timeout_s is not None:
        state["active_slice_timeout_s"] = int(timeout_s)
        state["active_slice_deadline_utc"] = _deadline_utc(str(state.get("active_turn_started_utc") or started_utc or ""), int(timeout_s))
    if progress_note is not None:
        state["active_progress_note"] = _compact_progress_text(progress_note)
    if wait_reason is not None:
        state["active_wait_reason"] = _compact_progress_text(wait_reason)
    if meaningful_event:
        state["last_meaningful_progress_event"] = str(meaningful_event).strip()
        state["last_meaningful_progress_utc"] = _utc_now_iso()
    if clear_stuck:
        state["stuck_reason"] = ""
        state["stuck_role"] = ""
        state["stuck_context"] = {}
    if stuck_reason is not None:
        state["stuck_reason"] = _compact_progress_text(stuck_reason)
        state["stuck_role"] = str(role or state.get("active_role") or "").strip()
    return _save_state(state)


def _clear_runtime_detail(state: Dict[str, Any], *, detail: str = "idle") -> Dict[str, Any]:
    state["status_detail"] = str(detail or "idle").strip()
    state["active_role"] = ""
    state["active_wait_reason"] = ""
    state["active_progress_note"] = ""
    state["active_worker_pid"] = 0
    state["active_worker_summary"] = ""
    state["active_wait_repeat_count"] = 0
    state["active_host_verification_progress_fingerprint"] = ""
    state["active_repo_progress_baseline_marker"] = ""
    state["active_repo_progress_marker"] = ""
    state["active_repo_progress_path_count"] = 0
    state["active_repo_progress_seen"] = False
    state["active_slice_timeout_s"] = 0
    state["active_slice_deadline_utc"] = ""
    state["active_turn_name"] = str(state.get("turn") or "")
    state["active_turn_started_utc"] = ""
    return _save_state(state)


def _record_role_progress(
    *,
    state: Dict[str, Any],
    role: str,
    turn: str,
    note: str,
    event_name: str = "role_progress_note",
    detail: str = "running_active",
) -> None:
    compact = _compact_progress_text(note)
    if not compact or _is_generic_progress_text(compact):
        return
    if compact == str(state.get("active_progress_note") or "").strip():
        return
    _set_runtime_detail(
        state,
        detail=detail,
        role=role,
        turn=turn,
        progress_note=compact,
        wait_reason="",
        meaningful_event=event_name,
    )
    _append_jsonl(
        _events_file(str(state.get("workflow_id") or "")),
        _event(
            str(state.get("workflow_id") or ""),
            event_name,
            {
                "role": role,
                "turn": turn,
                "note": compact,
            },
        ),
    )


def _find_event_utc(events: List[Dict[str, Any]], event_name: str, *, role: str = "", turn: str = "") -> str:
    want_event = str(event_name or "").strip()
    want_role = str(role or "").strip().lower()
    want_turn = str(turn or "").strip().lower()
    for row in events:
        if str(row.get("event") or "").strip() != want_event:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if want_role and str(payload.get("role") or "").strip().lower() != want_role:
            continue
        if want_turn and str(payload.get("turn") or "").strip().lower() != want_turn:
            continue
        return str(row.get("utc") or "").strip()
    return ""


def _latest_event_utc(events: List[Dict[str, Any]], event_name: str, *, role: str = "", turn: str = "") -> str:
    want_event = str(event_name or "").strip()
    want_role = str(role or "").strip().lower()
    want_turn = str(turn or "").strip().lower()
    picked = ""
    for row in events:
        if str(row.get("event") or "").strip() != want_event:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        if want_role and str(payload.get("role") or "").strip().lower() != want_role:
            continue
        if want_turn and str(payload.get("turn") or "").strip().lower() != want_turn:
            continue
        picked = str(row.get("utc") or "").strip()
    return picked


def _actor_display(actor: str) -> str:
    a = str(actor or "").strip().lower()
    if a == "builder":
        return "Builder"
    if a in {"assurer", "verifier"}:
        return "Verifier"
    if a in {"generalist", "orchestrator"}:
        return "Orchestrator"
    if a == "user":
        return "User"
    return "System"


def _push_conversation_item(
    out: List[Dict[str, Any]],
    *,
    utc: str,
    actor: str,
    source: str,
    target: str,
    kind: str,
    text: str,
    title: str = "",
    origin: str = "synthetic",
) -> None:
    body = str(text or "").strip()
    if not body:
        return
    src = str(source or "").strip().lower()
    dst = str(target or "").strip().lower()
    out.append(
        {
            "id": f"conv-{len(out) + 1}",
            "utc": str(utc or "").strip(),
            "actor": str(actor or "").strip().lower() or "system",
            "source": src or "system",
            "target": dst,
            "kind": str(kind or "summary").strip().lower() or "summary",
            "title": str(title or "").strip() or f"{_actor_display(src)} -> {_actor_display(dst or 'system')}",
            "text": body,
            "origin": str(origin or "synthetic").strip().lower() or "synthetic",
        }
    )


def _session_conversation_items(
    binding: Dict[str, Any],
    *,
    since_utc: str = "",
) -> tuple[List[Dict[str, Any]], set[str]]:
    norm_binding = binding if isinstance(binding, dict) else {}
    out: List[Dict[str, Any]] = []
    covered_roles: set[str] = set()
    lower_bound = str(since_utc or "").strip()
    target_by_role = {
        "builder": "generalist",
        "assurer": "generalist",
        "generalist": "user",
    }

    for role in ("generalist", "builder", "assurer"):
        try:
            payload = _agents.read_session(
                role=role,
                binding=norm_binding,
                tail=160,
                inbox_tail=1,
            )
        except Exception:
            continue
        rows = payload.get("session") if isinstance(payload.get("session"), list) else []
        found_meaningful = False
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_utc = str(row.get("utc") or "").strip()
            if lower_bound and (not row_utc or row_utc < lower_bound):
                continue
            sender = str(row.get("sender") or "").strip().lower()
            content = str(row.get("content") or "").strip()
            if sender != "assistant" or not content:
                continue
            found_meaningful = True
            _push_conversation_item(
                out,
                utc=row_utc,
                actor=role,
                source=role,
                target=target_by_role.get(role, "user"),
                kind="message",
                text=content,
                title=f"{_actor_display(role)} discussion",
                origin="session",
            )
        if found_meaningful:
            covered_roles.add(role)

    out.sort(key=lambda row: (str(row.get("utc") or ""), str(row.get("id") or "")))
    return out, covered_roles


def _conversation_items(state: Dict[str, Any], events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    binding = state.get("binding") if isinstance(state.get("binding"), dict) else {}
    binding_type = str(binding.get("type") or "").strip().lower()
    binding_id = str(binding.get("id") or "").strip()
    objective = str(state.get("objective") or "").strip()
    execution_directive = str(state.get("execution_directive") or "").strip()
    max_iterations = int(state.get("max_iterations") or 0)
    se_brief = str(state.get("se_brief") or "").strip()
    builder_output = str(state.get("builder_output") or "").strip()
    verifier_feedback = str(state.get("verifier_feedback") or "").strip()
    intent_validation_report = str(state.get("intent_validation_report") or "").strip()
    reports = state.get("reports") if isinstance(state.get("reports"), list) else []
    workflow_digest = state.get("workflow_digest") if isinstance(state.get("workflow_digest"), dict) else {}

    start_lines: List[str] = []
    if binding_type == "ledger_uid" and binding_id:
        start_lines.append(f"Execute bound PIS `{binding_id}`.")
    elif binding_type and binding_id:
        start_lines.append(f"Start workflow bound to `{binding_type}:{binding_id}`.")
    if max_iterations > 0:
        start_lines.append(f"Workflow config: max_iterations={max_iterations}.")
    if objective:
        start_lines.append(objective)
    if execution_directive:
        start_lines.append(execution_directive)
    _push_conversation_item(
        items,
        utc=_find_event_utc(events, "workflow_started") or str(state.get("created_utc") or ""),
        actor="user",
        source="user",
        target="generalist",
        kind="message",
        title="User -> Orchestrator",
        text="\n\n".join(start_lines),
    )

    _push_conversation_item(
        items,
        utc=(
            _find_event_utc(events, "se_tasking_done")
            or _latest_event_utc(events, "role_turn_finished", role="generalist", turn="se_tasking")
            or str(state.get("last_step_utc") or "")
        ),
        actor="generalist",
        source="generalist",
        target="builder",
        kind="message",
        title="Orchestrator -> Builder",
        text=se_brief,
    )

    if builder_output:
        _push_conversation_item(
            items,
            utc=(
                _find_event_utc(events, "builder_run_done")
                or _latest_event_utc(events, "role_turn_finished", role="builder")
                or str(state.get("updated_utc") or "")
            ),
            actor="builder",
            source="builder",
            target="generalist",
            kind="message",
            title="Builder -> Orchestrator",
            text=builder_output,
        )
    else:
        builder_started = _find_event_utc(events, "role_turn_started", role="builder")
        latest_builder_heartbeat = _latest_event_utc(events, "role_turn_heartbeat", role="builder")
        if builder_started:
            status_bits = ["Builder turn is active."]
            turn = str(state.get("active_turn_name") or state.get("turn") or "").strip()
            if turn:
                status_bits.append(f"turn={turn}")
            status_detail = str(state.get("status_detail") or "").strip()
            if status_detail:
                status_bits.append(f"status={status_detail}")
            progress_note = str(state.get("active_progress_note") or "").strip()
            if progress_note:
                status_bits.append(progress_note)
            if latest_builder_heartbeat:
                status_bits.append(f"latest heartbeat={latest_builder_heartbeat}")
            _push_conversation_item(
                items,
                utc=latest_builder_heartbeat or builder_started,
                actor="builder",
                source="builder",
                target="generalist",
                kind="status",
                title="Builder -> Orchestrator",
                text=" | ".join(status_bits),
            )

    if verifier_feedback:
        _push_conversation_item(
            items,
            utc=(
                _find_event_utc(events, "verifier_run_done")
                or _latest_event_utc(events, "role_turn_finished", role="assurer")
                or str(state.get("updated_utc") or "")
            ),
            actor="assurer",
            source="assurer",
            target="generalist",
            kind="message",
            title="Verifier -> Orchestrator",
            text=verifier_feedback,
        )

    summary_lines: List[str] = []
    if str(workflow_digest.get("headline") or "").strip():
        summary_lines.append(str(workflow_digest.get("headline") or "").strip())
    if str(workflow_digest.get("summary") or "").strip():
        summary_lines.append(str(workflow_digest.get("summary") or "").strip())
    if str(workflow_digest.get("next_action") or "").strip():
        summary_lines.append("Next: " + str(workflow_digest.get("next_action") or "").strip())
    if reports:
        summary_lines.extend(str(x or "").strip() for x in reports if str(x or "").strip())
    verdict = str(state.get("verifier_verdict") or "").strip()
    if verdict and verdict.upper() != "UNKNOWN":
        summary_lines.append(f"Verifier verdict: {verdict}")
    status = str(state.get("status") or "").strip()
    if status:
        summary_lines.append(f"Workflow status: {status}")
    _push_conversation_item(
        items,
        utc=(
            _find_event_utc(events, "se_decision_done")
            or _find_event_utc(events, "workflow_cancelled")
            or _find_event_utc(events, "workflow_failed")
            or str(state.get("updated_utc") or "")
        ),
        actor="generalist",
        source="generalist",
        target="user",
        kind="summary",
        title="Orchestrator -> User",
        text="\n".join(summary_lines),
    )

    _push_conversation_item(
        items,
        utc=_find_event_utc(events, "intent_validation_done") or str(state.get("updated_utc") or ""),
        actor="generalist",
        source="generalist",
        target="user",
        kind="summary",
        title="Orchestrator -> User",
        text=intent_validation_report,
    )

    for row in events:
        event_name = str(row.get("event") or "").strip()
        utc = str(row.get("utc") or "").strip()
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        role = str(payload.get("role") or "").strip().lower()
        turn = str(payload.get("turn") or "").strip()

        if event_name == "role_turn_started" and role:
            _push_conversation_item(
                items,
                utc=utc,
                actor=role,
                source=role,
                target="generalist" if role != "generalist" else "system",
                kind="status",
                title=f"{_actor_display(role)} status",
                text=f"Started `{turn or 'turn'}`.",
            )
            continue

        if event_name == "role_turn_finished" and role:
            ok = payload.get("ok")
            duration_ms = payload.get("duration_ms")
            bits = [f"Finished `{turn or 'turn'}`."]
            if isinstance(ok, bool):
                bits.append("ok=" + ("true" if ok else "false"))
            if isinstance(duration_ms, int):
                bits.append(f"duration_ms={duration_ms}")
            _push_conversation_item(
                items,
                utc=utc,
                actor=role,
                source=role,
                target="generalist" if role != "generalist" else "system",
                kind="status",
                title=f"{_actor_display(role)} status",
                text=" | ".join(bits),
            )
            continue

        if event_name in {"role_progress_note", "role_waiting_host_verification"} and role:
            note = str(payload.get("note") or payload.get("reason") or "").strip()
            if note:
                _push_conversation_item(
                    items,
                    utc=utc,
                    actor=role,
                    source=role,
                    target="generalist" if role != "generalist" else "system",
                    kind="status",
                    title=f"{_actor_display(role)} status",
                    text=note,
                )
            continue

        if event_name in {"role_stuck_detected", "role_escalated"} and role:
            note = str(payload.get("reason") or payload.get("detail") or "").strip()
            if note:
                _push_conversation_item(
                    items,
                    utc=utc,
                    actor=role,
                    source=role,
                    target="generalist",
                    kind="status",
                    title=f"{_actor_display(role)} status",
                    text=note,
                )
            continue

        if event_name == "workflow_cancelled":
            _push_conversation_item(
                items,
                utc=utc,
                actor="system",
                source="system",
                target="user",
                kind="status",
                title="Workflow status",
                text="Workflow cancelled." + (f" reason={payload.get('reason')}" if str(payload.get("reason") or "").strip() else ""),
            )
            continue

        if event_name == "workflow_failed":
            _push_conversation_item(
                items,
                utc=utc,
                actor="system",
                source="system",
                target="user",
                kind="status",
                title="Workflow status",
                text="Workflow failed." + (f" error={payload.get('error')}" if str(payload.get("error") or "").strip() else ""),
            )
            continue

    items.sort(key=lambda row: (str(row.get("utc") or ""), str(row.get("id") or "")))
    return items


def _parse_verdict(text: str) -> str:
    s = str(text or "")
    # Strict contract: only explicit VERDICT lines count.
    m = re.search(r"(?im)^\s*VERDICT\s*:\s*(PASS|FAIL|UNKNOWN)\b", s)
    if m:
        return str(m.group(1)).upper()
    return "UNKNOWN"


def _is_infra_blocked_feedback(text: str) -> bool:
    s = str(text or "").lower()
    pats = [
        "worker limit reached",
        "too many requests",
        "429",
        "rate limit",
        "capacity",
        "service unavailable",
        "stream disconnected before completion",
        "connection error",
    ]
    return any(p in s for p in pats)


def _is_environment_limited_feedback(text: str) -> bool:
    s = str(text or "").lower()
    pats = [
        "environment-limited",
        "under-evidenced",
        "remaining execution gap",
        "remaining proof gap",
        "missing execution evidence",
        "not executed in this environment",
        "skipped in this environment",
        "playwright is not installed",
        "manual checks",
        "host-only tooling",
        "host-side",
        "awaiting external confirmation",
        "tooling limitation",
        "sandbox remains environment-limited",
    ]
    return any(p in s for p in pats)


def _has_concrete_failure_signals(text: str) -> bool:
    s = str(text or "").lower()
    pats = [
        "broken",
        "hang",
        "stuck",
        "assertionerror",
        "uncompletedcoroutineserror",
        "regression",
        "bug",
        "incorrect",
        "disabled in the live code",
        "skipped the canonical snapshot",
        "worker was stuck",
        "never finished",
        "not good enough to trust",
        "not acceptable yet",
    ]
    return any(p in s for p in pats)


def _normalize_verifier_verdict(*, raw_verdict: str, feedback: str, latest_host_result: Dict[str, Any] | None = None) -> str:
    verdict = str(raw_verdict or "UNKNOWN").upper()
    if verdict != "FAIL":
        return verdict if verdict in {"PASS", "FAIL", "UNKNOWN"} else "UNKNOWN"
    feedback_text = str(feedback or "")
    if _has_concrete_failure_signals(feedback_text):
        return "FAIL"
    failure_kind = str(((latest_host_result or {}).get("failure_kind")) or "").strip().lower()
    if _is_environment_limited_feedback(feedback_text) or failure_kind in {
        "environment_missing",
        "environment_blocked",
        "timeout",
        "invalid_request",
    }:
        return "UNKNOWN"
    return "FAIL"


def _extract_host_verification_request(text: str) -> Dict[str, Any]:
    marker = HOST_VERIFICATION_MARKER
    s = str(text or "")
    idx = s.find(marker)
    if idx < 0:
        return {}
    payload = _safe_json_from_text(s[idx + len(marker) :].strip())
    if not payload:
        return {}
    action_id = str(payload.get("action_id") or "").strip()
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    if not action_id:
        return {}
    timeout_s = None
    timeout_raw = payload.get("timeout_s")
    if timeout_raw not in (None, ""):
        try:
            timeout_s = max(1, min(3600, int(timeout_raw)))
        except Exception:
            timeout_s = None
    stall_timeout_s = None
    stall_timeout_raw = payload.get("stall_timeout_s")
    if stall_timeout_raw not in (None, ""):
        try:
            stall_timeout_s = max(1, min(3600, int(stall_timeout_raw)))
            if timeout_s is not None:
                stall_timeout_s = min(int(timeout_s), int(stall_timeout_s))
        except Exception:
            stall_timeout_s = None
    return {
        "action_id": action_id,
        "params": dict(params),
        "reason": str(payload.get("reason") or "").strip(),
        "timeout_s": timeout_s,
        "stall_timeout_s": stall_timeout_s,
    }


def _host_verification_result_summary(result: Dict[str, Any]) -> str:
    if not isinstance(result, dict) or not result:
        return "No host verification evidence is available yet."
    evidence = result.get("evidence") if isinstance(result.get("evidence"), dict) else {}
    summary = str(evidence.get("summary") or "").strip() or "(no summary)"
    artifact_paths = evidence.get("artifact_paths") if isinstance(evidence.get("artifact_paths"), list) else []
    artifact_line = ", ".join([str(x) for x in artifact_paths[:4]]) if artifact_paths else "(none)"
    return "\n".join(
        [
            f"- request_id: {str(result.get('request_id') or '')}",
            f"- action_id: {str(result.get('action_id') or '')}",
            f"- ok: {str(result.get('ok') or False).lower()}",
            f"- failure_kind: {str(result.get('failure_kind') or '') or '(none)'}",
            f"- exit_code: {str(result.get('exit_code') or '')}",
            f"- cwd: {str(result.get('cwd') or '')}",
            f"- argv: {' '.join([str(x) for x in (result.get('argv') or [])])}",
            f"- summary: {summary}",
            f"- stdout_path: {str(evidence.get('stdout_path') or '')}",
            f"- stderr_path: {str(evidence.get('stderr_path') or '')}",
            f"- artifact_paths: {artifact_line}",
        ]
    )


def _build_host_verification_request(state: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    action_id = str(request.get("action_id") or "").strip()
    params = request.get("params") if isinstance(request.get("params"), dict) else {}
    policy: Dict[str, Any] = {
        "allow_network": False,
        "require_clean_exit": True,
    }
    if request.get("timeout_s") not in (None, ""):
        try:
            policy["timeout_s"] = max(1, min(3600, int(request.get("timeout_s"))))
        except Exception:
            pass
    if request.get("stall_timeout_s") not in (None, ""):
        try:
            stall_timeout_s = max(1, min(3600, int(request.get("stall_timeout_s"))))
            if policy.get("timeout_s") is not None:
                stall_timeout_s = min(int(policy.get("timeout_s") or stall_timeout_s), stall_timeout_s)
            policy["stall_timeout_s"] = stall_timeout_s
        except Exception:
            pass
    req = {
        "workflow_id": str(state.get("workflow_id") or "").strip(),
        "turn": f"verifier_iteration_{int(state.get('iteration') or 1)}",
        "requested_by_role": "assurer",
        "binding": dict(state.get("binding") or {}),
        "action": {
            "action_id": action_id,
            "params": dict(params),
        },
        "artifacts": {"capture_stdout": True, "capture_stderr": True},
        "policy": policy,
        "reason": str(request.get("reason") or "").strip(),
        "created_utc": _utc_now_iso(),
    }
    return _verification_contract.normalize_request(req, repo=_repo_root())



def _host_verification_result_matches_request(result: Dict[str, Any], request: Dict[str, Any]) -> bool:
    if not isinstance(result, dict) or not isinstance(request, dict):
        return False
    req_digest = str(request.get("request_digest") or "").strip()
    res_digest = str(result.get("request_digest") or "").strip()
    req_id = str(request.get("request_id") or "").strip()
    res_id = str(result.get("request_id") or "").strip()
    req_workflow = str(request.get("workflow_id") or "").strip()
    res_workflow = str(result.get("workflow_id") or "").strip()
    req_action = str((((request.get("action") or {}) if isinstance(request.get("action"), dict) else {}).get("action_id")) or "").strip()
    res_action = str(result.get("action_id") or "").strip()
    req_binding = request.get("binding") if isinstance(request.get("binding"), dict) else {}
    res_binding = result.get("binding") if isinstance(result.get("binding"), dict) else {}
    if req_workflow and res_workflow and req_workflow != res_workflow:
        return False
    if req_action and res_action and req_action != res_action:
        return False
    if str(req_binding.get("type") or "").strip() and str(res_binding.get("type") or "").strip() and str(req_binding.get("type") or "").strip() != str(res_binding.get("type") or "").strip():
        return False
    if str(req_binding.get("id") or "").strip() and str(res_binding.get("id") or "").strip() and str(req_binding.get("id") or "").strip() != str(res_binding.get("id") or "").strip():
        return False
    if req_digest and res_digest:
        return req_digest == res_digest
    if req_id and res_id:
        return req_id == res_id
    return bool(req_action and req_workflow and req_action == res_action and req_workflow == res_workflow)


def _find_matching_host_verification_result(state: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _verification_contract.normalize_request(request, repo=_repo_root())
    candidates: List[tuple[str, Dict[str, Any]]] = []
    latest = state.get("latest_host_verification_result") if isinstance(state.get("latest_host_verification_result"), dict) else {}
    if latest:
        candidates.append(("workflow_state.latest", latest))
    for row in reversed(list(state.get("host_verification_results") or [])):
        if isinstance(row, dict):
            candidates.append(("workflow_state.history", row))
    job_dir = Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")).resolve()
    durable = _read_json((job_dir / "result.json").resolve()) if str(job_dir) else {}
    if isinstance(durable, dict) and durable:
        candidates.append(("durable_result", durable))
    for source, candidate in candidates:
        if _host_verification_result_matches_request(candidate, normalized):
            reused = dict(candidate)
            reused.setdefault("request_digest", str(normalized.get("request_digest") or ""))
            reused["replayed"] = True
            reused["reentry_source"] = source
            reused["reentry_request_id"] = str(candidate.get("request_id") or "")
            reused["reentry_reused_utc"] = _utc_now_iso()
            return reused
    return {}


def _host_verification_status_path(job_dir: Path) -> Path:
    return (job_dir / "status.json").resolve()


def _host_verification_progress_note(status: Dict[str, Any]) -> str:
    if not isinstance(status, dict):
        return ""
    progress_note = _compact_progress_text(str(status.get("progress_note") or ""))
    if progress_note:
        return progress_note
    message = _compact_progress_text(str(status.get("message") or ""))
    if message:
        return message
    phase = str(status.get("phase") or "").strip().replace("_", " ")
    if phase:
        return f"Host verification {phase}."
    return ""


def _host_verification_wait_reason(status: Dict[str, Any], request: Dict[str, Any]) -> str:
    note = _host_verification_progress_note(status)
    if note:
        return note
    action_id = str((((request.get("action") or {}) if isinstance(request.get("action"), dict) else {}).get("action_id")) or "").strip()
    if action_id:
        return f"Waiting for host verification {action_id}."
    return "Waiting for host verification."


def _sync_host_verification_runtime_progress(
    *,
    state: Dict[str, Any],
    request: Dict[str, Any],
    status: Dict[str, Any],
    force: bool = False,
) -> bool:
    if not isinstance(status, dict) or not status:
        return False
    fingerprint = json.dumps(
        {
            "status": str(status.get("status") or "").strip(),
            "phase": str(status.get("phase") or "").strip(),
            "progress_note": str(status.get("progress_note") or "").strip(),
            "message": str(status.get("message") or "").strip(),
            "remaining_s": int(status.get("remaining_s") or 0),
            "last_output_utc": str(status.get("last_output_utc") or "").strip(),
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    if (not force) and fingerprint == str(state.get("active_host_verification_progress_fingerprint") or ""):
        return False
    note = _host_verification_progress_note(status)
    wait_reason = _host_verification_wait_reason(status, request)
    phase = str(status.get("phase") or "").strip()
    state["active_host_verification_progress_fingerprint"] = fingerprint
    state["active_worker_pid"] = int(status.get("subprocess_pid") or 0)
    state["active_worker_summary"] = note or wait_reason
    state["latest_host_verification_status"] = {
        "request_id": str(status.get("request_id") or ""),
        "status": str(status.get("status") or ""),
        "phase": phase,
        "progress_note": note,
        "message": str(status.get("message") or ""),
        "remaining_s": int(status.get("remaining_s") or 0),
        "timeout_s": int(status.get("timeout_s") or 0),
        "stall_timeout_s": int(status.get("stall_timeout_s") or 0),
        "updated_utc": str(status.get("updated_utc") or ""),
        "last_output_utc": str(status.get("last_output_utc") or ""),
    }
    _set_runtime_detail(
        state,
        detail="running_waiting_host_verification",
        role="assurer",
        turn="host_verification",
        progress_note=note or wait_reason,
        wait_reason=wait_reason,
        meaningful_event="host_verification_progress",
    )
    _append_jsonl(
        _events_file(str(state.get("workflow_id") or "")),
        _event(
            str(state.get("workflow_id") or ""),
            "host_verification_progress",
            {
                "request_id": str(status.get("request_id") or ""),
                "action_id": str(status.get("action_id") or ""),
                "status": str(status.get("status") or ""),
                "phase": phase,
                "progress_note": note,
                "remaining_s": int(status.get("remaining_s") or 0),
                "last_output_utc": str(status.get("last_output_utc") or ""),
                "subprocess_pid": int(status.get("subprocess_pid") or 0),
            },
        ),
    )
    return True


def _call_host_verification_request(*, state: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    """Invoke host verification with backward-compatible test seam semantics.

    Older tests monkeypatch ``_run_host_verification_request`` with a single-
    argument callable that accepts only the normalized request. The production
    helper also needs ``state`` so progress can be mirrored into workflow state.
    This adapter preserves both contracts without forcing the test seam to know
    about runtime progress plumbing.
    """

    runner = _run_host_verification_request
    try:
        params = inspect.signature(runner).parameters
    except (TypeError, ValueError):
        params = {}
    if "state" in params:
        return runner(state=state, request=request)
    return runner(request=request)


def _run_host_verification_request(*, state: Dict[str, Any], request: Dict[str, Any]) -> Dict[str, Any]:
    normalized = _verification_contract.normalize_request(request, repo=_repo_root())
    existing = _find_matching_host_verification_result(state, normalized)
    if existing:
        return existing
    job_dir = Path(str((normalized.get("artifacts") or {}).get("root_dir") or "")).resolve()
    status_path = _host_verification_status_path(job_dir)
    result_box: Dict[str, Any] = {}
    error_box: Dict[str, Any] = {}
    done = threading.Event()

    def _worker() -> None:
        try:
            result_box["result"] = _agents._run_host_verification_request(normalized)  # type: ignore[attr-defined]
        except Exception as exc:
            error_box["error"] = str(exc)
        finally:
            done.set()

    thread = threading.Thread(
        target=_worker,
        name=f"orch-host-verification-{str(normalized.get('request_id') or '')}",
        daemon=True,
    )
    thread.start()
    if status_path.exists():
        _sync_host_verification_runtime_progress(state=state, request=normalized, status=_read_json(status_path), force=True)
    while not done.wait(timeout=5.0):
        _sync_host_verification_runtime_progress(state=state, request=normalized, status=_read_json(status_path))
    thread.join(timeout=1.0)
    if status_path.exists():
        _sync_host_verification_runtime_progress(state=state, request=normalized, status=_read_json(status_path), force=True)
    if "result" in result_box and isinstance(result_box.get("result"), dict):
        return dict(result_box["result"])
    err = str(error_box.get("error") or "host verification runner failed")
    result = _verification_contract.build_result(
        request=normalized,
        ok=False,
        status="failed",
        exit_code=1,
        started_utc=_utc_now_iso(),
        finished_utc=_utc_now_iso(),
        duration_ms=0,
        cwd=str(_repo_root()),
        argv=[],
        evidence={
            "stdout_path": "",
            "stderr_path": "",
            "artifact_paths": [],
            "summary": err[:2000],
        },
        failure_kind="runner_internal_error",
    )
    _verification_contract.write_json_atomic((job_dir / "result.json").resolve(), result)
    return result


def _clip(text: str, *, max_chars: int = MAX_MESSAGE_CHARS) -> str:
    s = str(text or "")
    lim = max(4000, int(max_chars))
    return s[:lim]


def _compose_se_task_prompt(state: Dict[str, Any]) -> str:
    sub = str(state.get("subphases", [""])[int(state.get("subphase_index") or 0)] or "")
    stage = "PIS stage"
    objective = str(state.get("objective") or "")
    context_label = str(state.get("phase_label") or "")
    directive = str(state.get("execution_directive") or "").strip()
    pis_uid = str((state.get("binding") or {}).get("id") or "")
    max_it = int(state.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
    lines = [
        "You are the System Engineering Agent. Produce a compact build brief for the Builder agent.",
        f"PIS UID: {pis_uid}",
        f"Context Label: {context_label}",
        f"{stage}: {sub}",
        f"Max Iterations: {max_it}",
        f"Objective: {objective}",
    ]
    if directive:
        lines.append(f"Execution Directive (apply only if relevant): {directive}")
    lines.extend(
        [
            "",
            "Define what must be true and what must be proved. Do not over-prescribe the Builder's method when a narrower contract is enough.",
            "Prefer a smallest meaningful checkpoint over a giant all-at-once brief.",
            "",
            "Use the configured max_iterations exactly as provided. Do not invent or restate a different retry budget.",
            "",
            "Return sections exactly:",
            "1) Scope",
            "2) Acceptance Criteria",
            "3) Constraints",
            "4) Required Tests",
            "5) Out-of-scope",
        ]
    )
    return "\n".join(lines)


def _compose_builder_prompt(state: Dict[str, Any]) -> str:
    if _is_phase_e_progressive_context(state):
        return _compose_phase_e_master_prompt(state)

    sub = str(state.get("subphases", [""])[int(state.get("subphase_index") or 0)] or "")
    stage = "PIS stage"
    context_label = str(state.get("phase_label") or "")
    itr = int(state.get("iteration") or 1)
    se_brief = str(state.get("se_brief") or "")
    vf = str(state.get("verifier_feedback") or "")
    directive = str(state.get("execution_directive") or "").strip()
    recovery_note = str(state.get("stuck_recovery_note") or "").strip()
    parts = [
        "Implement this PIS scope in the repo and include tests for changed behavior.",
        f"Context Label: {context_label}",
        f"{stage}: {sub}",
        f"Iteration: {itr}",
        "\nSystem Engineering Brief:\n" + se_brief,
    ]
    if directive:
        parts.append("\nExecution Directive (apply only if relevant):\n" + directive)
    if recovery_note:
        parts.append("\nStuck Recovery Guidance:\n" + recovery_note)
    if vf.strip():
        parts.append("\nVerifier Feedback To Address:\n" + vf)
    parts.append(
        "\nExecution rules for this slice:\n"
        "- Use your full engineering judgment; role scope defines responsibility, not a narrow thinking style.\n"
        "- Land the highest-value checkpoint you can prove in this slice.\n"
        "- One focused implementation plus narrow evidence beats a long loop of retries.\n"
        "- If the same validation path stays blocked after a focused retry or the environment is clearly the limiter, stop and report the blocker instead of rehearsing it.\n"
        "- Keep progress notes meaningful: new command, new result, blocker, or strategy change.\n"
    )
    parts.append(
        "\nReturn output with sections:\n"
        "- Change Summary\n"
        "- Files Changed\n"
        "- Validation Commands Run\n"
        "- Residual Risks\n"
    )
    return _clip("\n".join(parts))


def _is_phase_e_progressive_context(state: Dict[str, Any]) -> bool:
    subphases = state.get("subphases") if isinstance(state.get("subphases"), list) else []
    if not subphases:
        return False
    normalized = [str(x or "").strip().upper() for x in subphases]
    if not normalized or any(not s for s in normalized):
        return False
    return all(re.match(r"^E[1-9][0-9]*$", s) is not None for s in normalized)


def _phase_e_stage_requirements(stage: str) -> List[str]:
    s = str(stage or "").strip().upper()
    if s == "E1":
        return [
            "E1 required metrics: Structural Innovation Rate (SIR), Neutrality Profile, Functional Density, Redundancy Index, Perturbation Recovery Taxonomy (`return|shift|collapse`), and Determinism Sentinel.",
            "E1 metric rules: metric-isolated execution only, fixed cadence/sampling schedule, deterministic ordering/aggregation, schema versioning, and RNG usage via RNGManager metric stream with explicit budget.",
            "E1 acceptance gate: `E1` verifier verdict must be `PASS` before any `E2` implementation work is accepted.",
        ]
    if s == "E2":
        return [
            "E2 required failure categories: `drift_collapse`, `maintenance_domination`, `structural_stagnation`, `bloat_ceiling_hit`, `mutation_explosion`, `identity_mimicry`, `rng_desync`, `hidden_scalar_reward_channel`.",
            "E2 taxonomy rules: explicit deterministic triggers, campaign-frozen thresholds/config, distinct queryable annotations, deterministic co-occurrence, and resume-stable category outcomes.",
            "E2 acceptance gate: `E2` verifier verdict must be `PASS` with no regression against accepted `E1` behavior.",
        ]
    return [
        "For this `E*` stage, preserve deterministic execution cadence, metric/taxonomy isolation, schema stability, and strict replay parity expectations.",
    ]


def _compose_phase_e_master_prompt(state: Dict[str, Any]) -> str:
    sub_idx = int(state.get("subphase_index") or 0)
    subphases = state.get("subphases") if isinstance(state.get("subphases"), list) else []
    current = str(subphases[sub_idx] if 0 <= sub_idx < len(subphases) else "").strip() or "E1"
    itr = int(state.get("iteration") or 1)
    phase_label = str(state.get("phase_label") or "").strip()
    objective = str(state.get("objective") or "").strip()
    pis_uid = str((state.get("binding") or {}).get("id") or "").strip()
    se_brief = str(state.get("se_brief") or "").strip()
    directive = str(state.get("execution_directive") or "").strip()
    vf = str(state.get("verifier_feedback") or "").strip()
    ordered = ", ".join([str(x) for x in subphases]) if subphases else current
    stage_requirements = _phase_e_stage_requirements(current)

    parts = [
        "Implement this PIS scope in the repo and include tests for changed behavior.",
        "",
        "Phase E Master Prompt (Progressive Sub-Phases)",
        f"PIS UID: {pis_uid}",
        f"Context Label: {phase_label}",
        f"PIS stage: {current}",
        f"Sub-phase Order (fixed): {ordered}",
        f"Iteration: {itr}",
        f"Objective: {objective}",
        "",
        "Phase-E hard gates",
        "- `E1` must pass before `E2` can advance.",
        "- `E2` must pass with no regression against accepted `E1` behavior.",
        "- If verifier verdict is not `PASS`, do not advance sub-phase.",
        "",
        "Current sub-phase required outcomes",
    ]
    for line in stage_requirements:
        parts.append(f"- {line}")
    parts.extend(
        [
            "",
        "Execution mode",
        "- Execute exactly one sub-phase per run cycle (`E1..En`) with no batching.",
        "- Treat Phase D as complete and immutable; build only on validated D outputs.",
        "- Keep each sub-phase independently reviewable and reversible.",
        "",
        "Entry gate (before coding current sub-phase)",
        "- Confirm sub-phase objective, in-scope files, and deterministic constraints.",
        "- Confirm API/behavior compatibility. If change is required, include explicit compatibility note and migration impact.",
        "- Confirm observability/metadata touchpoints and reuse existing conventions only.",
        "",
        "Mandatory deliverables per sub-phase",
        "- Sub-phase plan with acceptance checks.",
        "- Changed files with concise rationale.",
        "- Identify affected Causyl schema and logging contracts when touched or extended.",
        "- Test evidence: command list, pass/fail, and artifact/log paths.",
        "- Rollback note: exactly how to revert this sub-phase safely.",
        "- Handoff note for next sub-phase (`E{k+1}`) with open risks/dependencies.",
        "",
        "Required tests per sub-phase",
        "- `python -m py_compile <modified files>`",
        "- Targeted unit/integration tests for touched components.",
        "- Schema/log contract checks if logging/metadata changed.",
        "- Scenario tests for single-panel orchestrator happy-path and critical failure handling.",
        "- Sub-phase gate checks proving `E1 -> E2` progression is not bypassed.",
        "",
        "Stop/continue decision gate (must state one)",
        "- `next_subphase`: all acceptance checks pass and residual risks are bounded.",
        "- `extra_iteration`: fixable failures remain; include exact remediation and rerun plan.",
        "- `stop_phase`: blocking failure or scope violation; include failure summary and rollback status.",
        "",
        "Failure handling and retry policy",
        "- For deterministic/test failures: perform one focused fix iteration, rerun targeted tests, then decide.",
        "- For infra/transient failures: retry verifier path up to configured infra retry limit with no scope expansion.",
        "- Never bypass failing gates; no ad-hoc log/metadata formats.",
        "",
        "Final consolidation gate (after last sub-phase only)",
        "- Run `pytest`.",
        "- Run `python -m tools.diagnostics.static_rng_scan --repo_root . --json`.",
        "- Run `tools/diagnostics/verify_p0a_determinism_gate.sh --with-tests`.",
        "- Run `pytest -q tests/evolvability/phase_e` (or phase-e equivalent suite path if relocated).",
        "- Run strict replay e2e with Phase-E enabled and archive comparison artifacts.",
        "- Provide Phase E completion summary mapping each `E*` sub-phase to evidence and artifact paths.",
        "",
        "Constraints",
        "- Do not redo or modify validated Phase D scope.",
        "- Preserve existing APIs/behavior unless explicitly authorized by the active sub-phase with compatibility notes.",
        "- No broad refactors outside Phase E objectives.",
        ]
    )
    if se_brief:
        parts.extend(["", "System Engineering Brief:", se_brief])
    if directive:
        parts.extend(["", "Execution Directive (apply only if relevant):", directive])
    if vf:
        parts.extend(["", "Verifier Feedback To Address:", vf])
    parts.extend(
        [
            "",
            "Return output with sections:",
            "- Change Summary",
            "- Files Changed",
            "- Validation Commands Run",
            "- Residual Risks",
        ]
    )
    return _clip("\n".join(parts))


def _compose_verifier_prompt(state: Dict[str, Any]) -> str:
    itr = int(state.get("iteration") or 1)
    se_brief = str(state.get("se_brief") or "")
    builder = str(state.get("builder_output") or "")
    recovery_note = str(state.get("stuck_recovery_note") or "").strip()
    latest_host = state.get("latest_host_verification_result") if isinstance(state.get("latest_host_verification_result"), dict) else {}
    host_evidence = _host_verification_result_summary(latest_host)
    allowed_actions = ", ".join(_verification_actions.allowed_action_ids())
    parts = [
        "Audit the builder output strictly.",
        f"Iteration: {itr}",
        "",
        "SE Brief:",
        se_brief,
        "",
        "Builder Output:",
        builder,
        "",
    ]
    if recovery_note:
        parts.extend(["Stuck Recovery Guidance:", recovery_note, ""])
    parts.extend(
        [
            "Use the smallest proof step that can honestly change the verdict.",
            "Do not repeat the same blocked or failing proof target just to look busy.",
            "If the remaining proof is host-only or environment-limited, say that plainly and request the typed host action once.",
            "",
            "Host Verification Evidence:",
            host_evidence,
            "",
            "If host-only tooling proof is required and the current evidence is insufficient, append exactly one structured request block at the end.",
            f"Allowed action_id values: {allowed_actions}",
            f"Format:\n{HOST_VERIFICATION_MARKER}",
            '{"action_id":"android_unit_test","params":{"gradle_task":":app:testDebugUnitTest","test_selector":"com.example.Test"},"reason":"short reason"}',
            "",
            "For android_unit_test, include test_selector or test_selectors; bare Gradle-task requests are invalid because unrelated suite failures are not targeted proof.",
            "Do not invent shell commands. Request typed actions only.",
            "If a relevant host verification result is already present above, use it instead of re-requesting the same action.",
            "Return with first line exactly: VERDICT: PASS or VERDICT: FAIL or VERDICT: UNKNOWN",
            "Then provide findings, evidence, impact, and concrete fixes.",
        ]
    )
    return _clip("\n".join(parts))


def _compose_se_unblock_prompt(state: Dict[str, Any]) -> str:
    stuck = state.get("stuck_context") if isinstance(state.get("stuck_context"), dict) else {}
    role = str(stuck.get("role") or state.get("stuck_role") or "").strip() or "builder"
    turn = str(stuck.get("turn") or "").strip() or "builder_run"
    repeat_count = int(stuck.get("repeat_count") or 1)
    workflow_repeat_count = int(stuck.get("workflow_repeat_count") or 1)
    retry_limit = _normalize_stuck_retry_limit(stuck.get("retry_limit") or state.get("stuck_retry_limit"))
    allowed = "retry_builder, retry_verifier, intent_validation, stop_phase"
    return _clip(
        "You are the System Engineering Agent recovering a stuck workflow role.\n"
        f"Role: {role}\n"
        f"Turn: {turn}\n"
        f"Repeat Count: {repeat_count}\n"
        f"Workflow Repeat Count: {workflow_repeat_count}\n"
        f"Retry Limit: {retry_limit}\n"
        f"Reason: {str(stuck.get('reason') or '')}\n"
        f"Fingerprint Summary: {str(stuck.get('fingerprint_summary') or '')}\n"
        f"Last Progress Note: {str(stuck.get('last_progress_note') or '')}\n"
        f"Last Meaningful Progress UTC: {str(stuck.get('last_meaningful_progress_utc') or '')}\n"
        f"Wait Reason: {str(stuck.get('wait_reason') or '')}\n"
        f"Worker PID: {str(stuck.get('worker_pid') or '')}\n"
        f"Worker Summary: {str(stuck.get('worker_summary') or '')}\n"
        f"Wait Repeat Count: {str(stuck.get('wait_repeat_count') or '')}\n"
        f"Error: {str(stuck.get('error') or '')}\n"
        f"Response Excerpt: {str(stuck.get('response_excerpt') or '')}\n\n"
        "Decide the next bounded move. Prefer narrowing scope, switching proof strategy, or stopping over repeating the same swamp.\n"
        "If the same stuck fingerprint is already at the retry limit, do not send it back into the same loop.\n\n"
        "Return JSON only with keys:\n"
        f"decision: one of [{allowed}]\n"
        "reason: short string\n"
        "instruction: short string for the next role slice"
    )


def _compose_se_decision_prompt(state: Dict[str, Any]) -> str:
    verdict = str(state.get("verifier_verdict") or "UNKNOWN")
    vf = str(state.get("verifier_feedback") or "")
    max_it = int(state.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
    itr = int(state.get("iteration") or 1)
    return _clip(
        "You are the System Engineering Agent making a control decision.\n"
        f"Iteration: {itr}/{max_it}\n"
        f"Verifier Verdict: {verdict}\n\n"
        "Verifier feedback:\n"
        f"{vf}\n\n"
        "If the verifier outcome is environment-limited, under-evidenced, or blocked on narrower host proof rather than a concrete product defect, prefer extra_iteration over stop_phase.\n\n"
        "Return JSON only with keys:\n"
        "decision: one of [next_subphase, extra_iteration, stop_phase]\n"
        "reason: short string"
    )


def _compose_intent_prompt(state: Dict[str, Any]) -> str:
    objective = str(state.get("objective") or "")
    reports = state.get("reports") if isinstance(state.get("reports"), list) else []
    report_txt = "\n".join([str(x) for x in reports[-12:]])
    return _clip(
        "You are the Intent Validation Agent.\n"
        f"Objective: {objective}\n\n"
        "Iteration report summary:\n"
        f"{report_txt}\n\n"
        "Return with first line exactly: VERDICT: PASS or VERDICT: FAIL or VERDICT: UNKNOWN\n"
        "Then provide the sections below.\n\n"
        "Return sections exactly:\n"
        "1) Intended Change\n"
        "2) What Was Run\n"
        "3) Pass/Fail Results\n"
        "4) Intent Validation Verdict\n"
        "5) Residual Risks/Gaps\n"
    )


def _deterministic_se_brief(state: Dict[str, Any]) -> str:
    subphases = state.get("subphases") if isinstance(state.get("subphases"), list) else []
    sub_idx = int(state.get("subphase_index") or 0)
    subphase = str(subphases[sub_idx] if 0 <= sub_idx < len(subphases) else "PIS").strip() or "PIS"
    objective = str(state.get("objective") or "").strip()
    directive = str(state.get("execution_directive") or "").strip()
    max_it = int(state.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
    lines = [
        "1) Scope",
        f"- Deliver the smallest meaningful checkpoint for `{subphase}` that advances: {objective or 'the bound workflow objective'}.",
        "",
        "2) Acceptance Criteria",
        "- Land a concrete repo change or an explicit blocker summary grounded in current evidence.",
        "- Run the narrowest validation that fits the changed behavior.",
        "- If remaining proof is host-only or environment-limited, say so plainly and leave verifier one typed host verification request instead of repeating the same local loop.",
        "",
        "3) Constraints",
        f"- Stay within the configured retry budget: max_iterations={max_it}.",
        "- Keep the slice narrow, auditable, and compatible unless the objective requires otherwise.",
        "- Do not broaden scope just because a wide test target is noisy.",
        "",
        "4) Required Tests",
        "- Prefer one targeted command or selector that can actually falsify the claimed change.",
        "- Record the exact commands you ran and any remaining proof gap.",
        "",
        "5) Out-of-scope",
        "- Broad refactors, broad suites, or repeated retries on the same blocked path without new evidence.",
    ]
    if directive:
        lines.extend(["", f"Execution Directive: {directive}"])
    return "\n".join(lines)


def _deterministic_se_decision(state: Dict[str, Any]) -> Dict[str, str]:
    verdict = str(state.get("verifier_verdict") or "UNKNOWN").strip().upper()
    feedback = str(state.get("verifier_feedback") or "").strip()
    itr = int(state.get("iteration") or 1)
    max_it = int(state.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
    infra_retry_count = int(state.get("infra_retry_count") or 0)
    max_infra = int(state.get("max_infra_retries") or _default_max_infra_retries())
    infra_blocked = verdict == "UNKNOWN" and _is_infra_blocked_feedback(feedback)
    env_limited = verdict == "UNKNOWN" and _is_environment_limited_feedback(feedback)

    if verdict == "PASS":
        return {"decision": "next_subphase", "reason": "verifier produced a concrete PASS verdict"}
    if infra_blocked:
        if infra_retry_count < max_infra:
            return {
                "decision": "extra_iteration",
                "reason": "verifier is infra-limited and still has bounded retry budget",
            }
        return {"decision": "stop_phase", "reason": f"infrastructure retry budget exhausted ({max_infra})"}
    if verdict == "FAIL":
        if itr < max_it:
            return {
                "decision": "extra_iteration",
                "reason": "verifier found a concrete defect and another bounded slice remains",
            }
        return {"decision": "stop_phase", "reason": f"iteration budget exhausted after FAIL verdict ({max_it})"}
    if env_limited:
        return {
            "decision": "stop_phase" if itr >= max_it else "extra_iteration",
            "reason": "remaining verifier gap is environment-limited",
        }
    if itr < max_it:
        return {
            "decision": "extra_iteration",
            "reason": "verifier verdict stayed UNKNOWN and one bounded retry remains",
        }
    return {"decision": "stop_phase", "reason": f"verifier verdict stayed UNKNOWN at iteration limit ({max_it})"}


def _deterministic_unblock_decision(state: Dict[str, Any]) -> Dict[str, str]:
    stuck = state.get("stuck_context") if isinstance(state.get("stuck_context"), dict) else {}
    role = str(stuck.get("role") or state.get("stuck_role") or "").strip().lower()
    workflow_repeat_count = int(stuck.get("workflow_repeat_count") or 1)
    retry_limit = _normalize_stuck_retry_limit(stuck.get("retry_limit") or state.get("stuck_retry_limit"))
    detail = str(stuck.get("detail") or "").strip().lower()
    wait_reason = str(stuck.get("wait_reason") or "").strip().lower()
    fingerprint_summary = str(stuck.get("fingerprint_summary") or "").strip()
    host_failure_kind = str(stuck.get("host_failure_kind") or "").strip().lower()

    if workflow_repeat_count >= retry_limit:
        return {
            "decision": "intent_validation" if role in {"builder", "assurer", "verifier"} else "stop_phase",
            "reason": f"same stuck fingerprint hit retry limit ({retry_limit})",
            "instruction": "Stop repeating the same blocked loop and summarize the remaining blocker.",
        }

    if role in {"assurer", "verifier"}:
        if host_failure_kind in {"environment_blocked", "timeout", "stall_timeout"}:
            return {
                "decision": "intent_validation",
                "reason": f"host verification ended with {host_failure_kind or 'a blocking failure'}",
                "instruction": "Use the current proof gap as the terminal blocker instead of reissuing the same host action.",
            }
        return {
            "decision": "retry_verifier",
            "reason": "retry verifier with a narrower proof move instead of the same local loop",
            "instruction": (
                "Do not rerun the same blocked local proof target. Use current evidence, request one typed host verification "
                "action if needed, or return an environment-limited verdict."
            ),
        }

    if role == "builder":
        response_excerpt = str(stuck.get("response_excerpt") or "").strip()
        has_checkpoint_signal = bool(str(state.get("builder_output") or "").strip() or str(state.get("latest_builder_journal_entry") or "").strip())
        if not has_checkpoint_signal and response_excerpt:
            has_checkpoint_signal = bool(
                _extract_markdown_section(response_excerpt, "Change Summary").strip()
                or _extract_markdown_section(response_excerpt, "Files Changed").strip()
            )
        if "gradle" in wait_reason or "pytest" in wait_reason or detail == "blocked_validation_loop":
            if has_checkpoint_signal:
                return {
                    "decision": "retry_verifier",
                    "reason": "builder already has a checkpoint signal and should stop burning slices on local validation",
                    "instruction": (
                        "Do not send builder back into the same local validation loop. Treat the current checkpoint as landed, "
                        "verify it directly, and use one typed host verification request if local proof is still blocked."
                    ),
                }
            return {
                "decision": "retry_builder",
                "reason": "builder got trapped in local validation noise",
                "instruction": (
                    "Stop broad validation loops. Land the smallest code checkpoint you can justify, narrow the proof target, "
                    "or leave the remaining proof gap explicitly for verifier."
                ),
            }
        return {
            "decision": "retry_builder",
            "reason": "builder slice should retry once with a narrower checkpoint",
            "instruction": f"Retry builder with a smaller checkpoint and avoid repeating `{fingerprint_summary or 'the same path'}`.",
        }

    return {
        "decision": "stop_phase",
        "reason": "control role got stuck and no narrower deterministic recovery applies",
        "instruction": "Stop the phase and surface the blocker clearly.",
    }


def _safe_json_from_text(text: str) -> Dict[str, Any]:
    s = str(text or "").strip()
    if not s:
        return {}
    try:
        obj = json.loads(s)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        pass
    m = re.search(r"\{.*\}", s, flags=re.DOTALL)
    if not m:
        return {}
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _role_response_text(resp: Dict[str, Any]) -> str:
    assistant = resp.get("assistant") if isinstance(resp, dict) else {}
    if not isinstance(assistant, dict):
        return ""
    return str(assistant.get("content") or "")


def _heartbeat_pulse(*, workflow_id: str, turn: str, role: str, seq: int, started_utc: str) -> None:
    try:
        st = _load_state(workflow_id)
    except Exception:
        return
    if str(st.get("status") or "").lower() in {"completed", "cancelled", "failed"}:
        return
    hb_utc = _utc_now_iso()
    st["last_heartbeat_utc"] = hb_utc
    st["active_turn_role"] = role
    st["active_turn_name"] = turn
    st["active_turn_started_utc"] = started_utc
    st["active_turn_heartbeat_seq"] = int(seq)
    _save_state(st)
    _append_jsonl(
        _events_file(workflow_id),
        _event(
            workflow_id,
            "role_turn_heartbeat",
            {
                "turn": turn,
                "role": role,
                "seq": int(seq),
                "started_utc": started_utc,
                "heartbeat_utc": hb_utc,
            },
        ),
    )


def _truthy_match(text: str, pattern: str) -> bool:
    try:
        return bool(re.search(pattern, str(text or ""), flags=re.IGNORECASE))
    except Exception:
        return False


def _extract_markdown_section(text: str, heading: str) -> str:
    raw = str(text or "")
    target = str(heading or "").strip().lower()
    if not raw or not target:
        return ""
    pattern = re.compile(
        rf"(?ims)^\s*(?:#+\s*)?(?:\*\*)?{re.escape(heading)}(?:\*\*)?\s*$"
        rf"(?P<body>.*?)(?=^\s*(?:#+\s*)?(?:\*\*)?[A-Za-z][A-Za-z0-9 /_-]*(?:\*\*)?\s*$|\Z)"
    )
    match = pattern.search(raw)
    if match:
        return str(match.group("body") or "").strip()
    # Fallback for inline heading style like "Change Summary:".
    inline = re.compile(
        rf"(?ims)^\s*(?:\*\*)?{re.escape(heading)}(?:\*\*)?\s*:\s*(?P<body>.*?)(?=^\s*(?:\*\*)?[A-Za-z][A-Za-z0-9 /_-]*(?:\*\*)?\s*:|\Z)"
    )
    match = inline.search(raw)
    return str(match.group("body") or "").strip() if match else ""


def _section_lines(section_text: str) -> List[str]:
    out: List[str] = []
    for raw_line in str(section_text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        line = re.sub(r"^\s*[-*+]\s+", "", line)
        line = re.sub(r"^\s*\d+\.\s+", "", line)
        line = line.strip()
        if line:
            out.append(line)
    return out


def _strip_markup(text: str) -> str:
    line = str(text or "").strip()
    if not line:
        return ""
    line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\2", line)
    line = re.sub(r"`([^`]+)`", r"\1", line)
    return " ".join(line.split()).strip()


def _extract_paths_from_line(line: str) -> List[str]:
    raw = _strip_markup(line)
    if not raw:
        return []
    found: List[str] = []
    for match in re.finditer(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", raw):
        candidate = match.group(0).strip()
        if candidate and candidate not in found:
            found.append(candidate)
    return found


def _extract_validation_commands(section_text: str) -> List[str]:
    commands: List[str] = []
    for line in _section_lines(section_text):
        code_blocks = re.findall(r"`([^`]+)`", line)
        if code_blocks:
            for cmd in code_blocks:
                cleaned = " ".join(str(cmd).split()).strip()
                if cleaned and cleaned not in commands:
                    commands.append(cleaned)
            continue
        cleaned = _strip_markup(line)
        if cleaned and cleaned not in commands:
            commands.append(cleaned)
    return commands


def _normalize_failure_signature(text: str, *, max_chars: int = 240) -> str:
    compact = _compact_progress_text(text, max_chars=max_chars).lower()
    compact = re.sub(r"`([^`]+)`", r"\1", compact)
    compact = re.sub(r"\b\d+\b", "#", compact)
    compact = re.sub(r"\s+", " ", compact).strip()
    return compact


def _failure_fingerprint(
    *,
    role: str,
    turn: str,
    text: str = "",
    error: str = "",
    latest_host_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, str]:
    host = latest_host_result if isinstance(latest_host_result, dict) else {}
    if host:
        failure_kind = str(host.get("failure_kind") or "").strip().lower()
        action_id = str(host.get("action_id") or "").strip().lower()
        evidence = host.get("evidence") if isinstance(host.get("evidence"), dict) else {}
        summary = _normalize_failure_signature(str(evidence.get("summary") or ""))
        seed = f"host::{role}::{turn}::{action_id}::{failure_kind}::{summary}"
        summary_text = summary or f"{action_id} {failure_kind}".strip()
    elif error:
        normalized_error = _normalize_failure_signature(error)
        seed = f"error::{role}::{turn}::{normalized_error}"
        summary_text = normalized_error
    else:
        normalized_text = _normalize_failure_signature(text)
        seed = f"text::{role}::{turn}::{normalized_text}"
        summary_text = normalized_text
    return {
        "fingerprint": hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16],
        "summary": summary_text[:240],
    }


def _record_failure_fingerprint(
    *,
    state: Dict[str, Any],
    role: str,
    turn: str,
    text: str = "",
    error: str = "",
    latest_host_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    row = _failure_fingerprint(
        role=role,
        turn=turn,
        text=text,
        error=error,
        latest_host_result=latest_host_result,
    )
    fp = str(row.get("fingerprint") or "").strip()
    records = state.get("role_failure_fingerprints")
    fingerprint_state = dict(records) if isinstance(records, dict) else {}
    previous = fingerprint_state.get(fp) if isinstance(fingerprint_state.get(fp), dict) else {}
    count = int(previous.get("count") or 0) + 1
    entry = {
        "role": role,
        "turn": turn,
        "count": count,
        "summary": str(row.get("summary") or "").strip(),
        "updated_utc": _utc_now_iso(),
    }
    fingerprint_state[fp] = entry
    state["role_failure_fingerprints"] = fingerprint_state
    _save_state(state)
    _append_jsonl(
        _events_file(str(state.get("workflow_id") or "")),
        _event(
            str(state.get("workflow_id") or ""),
            "role_failure_fingerprint",
            {
                "role": role,
                "turn": turn,
                "fingerprint": fp,
                "count": count,
                "summary": str(entry.get("summary") or ""),
            },
        ),
    )
    return {"fingerprint": fp, "count": count, "summary": str(entry.get("summary") or "")}


def _stuck_detail_for_failure(*, error: str = "", text: str = "", latest_host_result: Optional[Dict[str, Any]] = None) -> str:
    host = latest_host_result if isinstance(latest_host_result, dict) else {}
    failure_kind = str(host.get("failure_kind") or "").strip().lower()
    if failure_kind in {"environment_missing", "environment_blocked", "invalid_request"} or _is_environment_limited_feedback(text):
        return "blocked_environment"
    if "timed out" in str(error or "").lower():
        return "blocked_validation_loop"
    return "stuck_escalating"


def _stuck_detail_for_watchdog(*, reason: str = "", response_started: bool = False) -> str:
    normalized = str(reason or "").strip().lower()
    if "response but did not finish cleanly" in normalized or response_started:
        return "stuck_escalating"
    return "blocked_validation_loop"


def _stop_codex_role_worker(*, state: Dict[str, Any], role: str, reason: str) -> Dict[str, Any]:
    binding = state.get("binding") if isinstance(state.get("binding"), dict) else {}
    mgr_factory = getattr(_agents, "_codex_manager", None)
    if not callable(mgr_factory):
        return {"ok": False, "stopped": False, "reason": "codex manager unavailable"}
    try:
        mgr = mgr_factory()
    except Exception as exc:
        return {"ok": False, "stopped": False, "reason": str(exc)}
    try:
        return dict(mgr.stop_worker(binding=dict(binding), role=role, reason=reason) or {})
    except Exception as exc:
        return {"ok": False, "stopped": False, "reason": str(exc)}


def _journal_entry_from_builder_output(*, state: Dict[str, Any], builder_output: str) -> Dict[str, Any]:
    binding = state.get("binding") if isinstance(state.get("binding"), dict) else {}
    subphases = state.get("subphases") if isinstance(state.get("subphases"), list) else []
    sub_idx = int(state.get("subphase_index") or 0)
    subphase = str(subphases[sub_idx] if 0 <= sub_idx < len(subphases) else "PIS").strip() or "PIS"
    iteration = int(state.get("iteration") or 1)
    workflow_id = str(state.get("workflow_id") or "").strip()
    summary_section = _extract_markdown_section(builder_output, "Change Summary")
    files_section = _extract_markdown_section(builder_output, "Files Changed")
    validation_section = _extract_markdown_section(builder_output, "Validation Commands Run")
    risks_section = _extract_markdown_section(builder_output, "Residual Risks")

    summary_bullets = _section_lines(summary_section)
    if not summary_bullets:
        summary_bullets = _section_lines(builder_output)[:6]
    if not summary_bullets:
        summary_bullets = [f"Builder completed {subphase} iteration {iteration}."]

    files: List[str] = []
    for line in _section_lines(files_section):
        for path in _extract_paths_from_line(line):
            if path not in files:
                files.append(path)

    validation = _extract_validation_commands(validation_section)
    notes = _section_lines(risks_section)

    title_basis = files[0] if files else f"{subphase.lower()}-iteration-{iteration}"
    title = f"{subphase} builder update: {title_basis}"
    entry_id = f"orch_{workflow_id}_{subphase.lower()}_iter{iteration}"
    return {
        "entry_id": entry_id,
        "title": title[:120],
        "summary_bullets": summary_bullets,
        "files": files,
        "validation": validation,
        "notes": notes,
        "outcome": "applied",
        "scope_hint": _commit_journal._scope_from_path(files[0]) if files else "workflow/orchestrator",  # type: ignore[attr-defined]
        "author": {
            "role": "builder",
            "source": "codex",
            "binding": {
                "type": str(binding.get("type") or "").strip(),
                "id": str(binding.get("id") or "").strip(),
            },
        },
    }


def _record_builder_commit_journal(*, state: Dict[str, Any], builder_output: str) -> Optional[str]:
    text = str(builder_output or "").strip()
    if not text:
        return None
    entry = _journal_entry_from_builder_output(state=state, builder_output=text)
    try:
        path = _commit_journal.write_entry(entry, repo=_repo_root())
    except Exception as exc:
        wf = str(state.get("workflow_id") or "").strip()
        _append_jsonl(
            _events_file(wf),
            _event(
                wf,
                "builder_run_journal_failed",
                {
                    "iteration": int(state.get("iteration") or 0),
                    "error": str(exc),
                    "entry_id": str(entry.get("entry_id") or ""),
                },
            ),
        )
        return None
    return str(path)


def _extract_builder_evidence(builder_output: str) -> Dict[str, bool]:
    text = str(builder_output or "")
    return {
        "job_spec": _truthy_match(text, r"\bjob_spec(?:\.json)?\b"),
        "manifest": _truthy_match(text, r"\bmanifest(?:\.ya?ml|\.json)?\b"),
        "report": _truthy_match(text, r"\breport(?:\.json)?\b"),
        "wrapper": _truthy_match(text, r"\bwrapper\b|\btools\.run_job\b"),
        "immutable_git_ref": _truthy_match(text, r"\b(immutable\s+git[_\s-]?ref|EXPERIMENT_GIT_REF|git_ref)\b"),
        "seed": _truthy_match(text, r"\bseed\b"),
    }


def _benchmark_intent_validation_mode(state: Dict[str, Any]) -> bool:
    return str(state.get("phase_label") or "").strip().lower() == "benchmark"


def _benchmark_intent_validation_evidence(state: Dict[str, Any]) -> Dict[str, bool]:
    residue = state.get("repo_residue") if isinstance(state.get("repo_residue"), dict) else {}
    builder_output = str(state.get("builder_output") or "")
    files_changed = bool(_extract_markdown_section(builder_output, "Files Changed").strip())
    validation_run = bool(_extract_markdown_section(builder_output, "Validation Commands Run").strip())
    evidence = {
        "repo_change": int(residue.get("changed_since_start_count") or 0) > 0,
    }
    if files_changed or validation_run:
        evidence["builder_files"] = files_changed
        evidence["builder_validation"] = validation_run
    return evidence


def _intent_validation_evidence(state: Dict[str, Any]) -> Dict[str, bool]:
    if _benchmark_intent_validation_mode(state):
        return _benchmark_intent_validation_evidence(state)
    return _extract_builder_evidence(str(state.get("builder_output") or ""))


def _intent_validation_summary(*, state: Dict[str, Any], raw_report: str) -> Dict[str, Any]:
    """Return normalized verdict and evidence status for intent validation."""
    verifier_verdict = str(state.get("verifier_verdict") or "UNKNOWN").upper()
    parsed = _parse_verdict(raw_report)
    final_verdict = parsed if parsed != "UNKNOWN" else verifier_verdict
    evidence = _intent_validation_evidence(state)
    missing = [k for k, v in evidence.items() if not bool(v)]
    benchmark_mode = _benchmark_intent_validation_mode(state)
    if benchmark_mode:
        validated = "YES" if final_verdict == "PASS" else ("NO" if final_verdict == "FAIL" else "UNKNOWN")
    elif final_verdict == "PASS":
        validated = "YES" if not missing else "NO"
    elif final_verdict == "FAIL":
        validated = "NO"
    else:
        validated = "UNKNOWN"
    return {
        "verifier_verdict": verifier_verdict,
        "parsed_verdict": parsed,
        "final_verdict": final_verdict,
        "explicit_intent_verdict": parsed != "UNKNOWN",
        "evidence": evidence,
        "missing_evidence": missing,
        "intent_validated": validated,
    }


def _workflow_completed_cleanly(state: Dict[str, Any]) -> bool:
    """Return True only when intent validation ended in an explicit clean pass."""
    summary = _intent_validation_summary(state=state, raw_report=str(state.get("intent_validation_report_raw") or ""))
    return str(summary.get("intent_validated") or "") == "YES"


def _intent_validation_terminal_state(*, state: Dict[str, Any], raw_report: str) -> Dict[str, str]:
    """Map intent-validation output onto an honest terminal workflow state."""
    summary = _intent_validation_summary(state=state, raw_report=raw_report)
    parsed = str(summary.get("parsed_verdict") or "UNKNOWN").strip().upper()
    final_verdict = str(summary.get("final_verdict") or "UNKNOWN").strip().upper()
    missing = [str(x).strip() for x in (summary.get("missing_evidence") or []) if str(x).strip()]
    if parsed == "FAIL":
        return {
            "status": "failed",
            "status_detail": "intent_validation_failed",
            "reason": "Intent validation agent returned an explicit FAIL verdict.",
        }
    if final_verdict == "PASS":
        return {
            "status": "completed",
            "status_detail": "completed",
            "reason": (
                "Intent validation passed with the required evidence markers."
                if not missing
                else "Intent validation completed with residual evidence gaps surfaced in the report."
            ),
        }
    if final_verdict == "FAIL" and parsed == "UNKNOWN" and missing:
        return {
            "status": "completed",
            "status_detail": "completed",
            "reason": "Verifier risk remains documented, but the intent-validation layer did not return an explicit FAIL verdict.",
        }
    if missing:
        return {
            "status": "failed",
            "status_detail": "evidence_gap",
            "reason": "Intent validation did not establish clean proof and required evidence markers are missing.",
        }
    if final_verdict == "FAIL":
        return {
            "status": "failed",
            "status_detail": "intent_validation_failed",
            "reason": "Intent validation ended in FAIL.",
        }
    return {
        "status": "failed",
        "status_detail": "intent_validation_unknown",
        "reason": "Intent validation ended without a concrete PASS verdict.",
    }


def _format_intent_validation_report(*, state: Dict[str, Any], raw_report: str) -> str:
    phase = str(state.get("phase_label") or "")
    subphases = state.get("subphases") if isinstance(state.get("subphases"), list) else []
    objective = str(state.get("objective") or "")
    reports = state.get("reports") if isinstance(state.get("reports"), list) else []
    summary = _intent_validation_summary(state=state, raw_report=raw_report)
    verifier_verdict = str(summary.get("verifier_verdict") or "UNKNOWN")
    parsed = str(summary.get("parsed_verdict") or "UNKNOWN")
    final_verdict = str(summary.get("final_verdict") or "UNKNOWN")
    evidence = summary.get("evidence") if isinstance(summary.get("evidence"), dict) else {}
    missing = summary.get("missing_evidence") if isinstance(summary.get("missing_evidence"), list) else []
    validated = str(summary.get("intent_validated") or "UNKNOWN")
    benchmark_mode = _benchmark_intent_validation_mode(state)
    risk_line = (
        ("Missing benchmark evidence: " + ", ".join(missing)) if benchmark_mode and missing
        else ("Missing evidence markers: " + ", ".join(missing))
        if missing
        else ("Benchmark repo change was captured." if benchmark_mode else "No explicit residual risk detected in wrapper/manifest/report linkage.")
    )
    run_lines = [f"- {str(x)}" for x in reports[-12:]] or ["- No iteration execution summaries were recorded."]
    evidence_heading = "Benchmark evidence:" if benchmark_mode else "Evidence markers in builder output:"
    evidence_lines = [f"- {k}: {'yes' if v else 'no'}" for k, v in evidence.items()]
    out = [
        "1) Intended Change",
        f"- Objective: {objective}",
        "",
        "2) What Was Run",
        *run_lines,
        f"- {evidence_heading}",
        *evidence_lines,
        "",
        "3) Pass/Fail Results",
        f"- Verifier verdict: {verifier_verdict}",
        f"- Intent-agent parsed verdict: {parsed}",
        f"- Effective final verdict: {final_verdict}",
        "",
        "4) Intent Validation Verdict",
        f"- Intent validated: {validated}",
        "",
        "5) Residual Risks/Gaps",
        f"- {risk_line}",
        "- Raw intent validation response:",
        str(raw_report or "").strip() or "(empty)",
    ]
    return _clip("\n".join(out), max_chars=MAX_MESSAGE_CHARS)


def _escalate_role_stuck(
    *,
    state: Dict[str, Any],
    role: str,
    turn: str,
    reason: str,
    detail: str,
    error: str = "",
    text: str = "",
    latest_host_result: Optional[Dict[str, Any]] = None,
    fingerprint_info: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    wf = str(state.get("workflow_id") or "").strip()
    fingerprint = fingerprint_info if isinstance(fingerprint_info, dict) and fingerprint_info else _record_failure_fingerprint(
        state=state,
        role=role,
        turn=turn,
        text=text,
        error=error,
        latest_host_result=latest_host_result,
    )
    escalation = _record_stuck_escalation(
        state=state,
        role=role,
        turn=turn,
        fingerprint=str(fingerprint.get("fingerprint") or ""),
        summary=str(fingerprint.get("summary") or reason),
    )
    retry_limit = _normalize_stuck_retry_limit(state.get("stuck_retry_limit"))
    context = {
        "role": role,
        "turn": turn,
        "reason": _compact_progress_text(reason),
        "detail": detail,
        "error": _compact_progress_text(error),
        "response_excerpt": _compact_progress_text(text),
        "fingerprint": str(fingerprint.get("fingerprint") or ""),
        "repeat_count": int(fingerprint.get("count") or 1),
        "fingerprint_summary": str(fingerprint.get("summary") or ""),
        "last_progress_note": str(state.get("active_progress_note") or ""),
        "last_meaningful_progress_utc": str(state.get("last_meaningful_progress_utc") or ""),
        "slice_started_utc": str(state.get("active_turn_started_utc") or ""),
        "slice_timeout_s": int(state.get("active_slice_timeout_s") or 0),
        "wait_reason": str(state.get("active_wait_reason") or ""),
        "worker_pid": int(state.get("active_worker_pid") or 0),
        "worker_summary": str(state.get("active_worker_summary") or ""),
        "wait_repeat_count": int(state.get("active_wait_repeat_count") or 0),
        "workflow_repeat_count": int(escalation.get("count") or 1),
        "retry_limit": retry_limit,
    }
    if latest_host_result:
        context["host_request_id"] = str(latest_host_result.get("request_id") or "")
        context["host_action_id"] = str(latest_host_result.get("action_id") or "")
        context["host_failure_kind"] = str(latest_host_result.get("failure_kind") or "")
    state["stuck_context"] = context
    state["turn"] = "se_unblock"
    state["status"] = "idle"
    state["last_step_utc"] = _utc_now_iso()
    _set_runtime_detail(
        state,
        detail=detail,
        role=role,
        turn=turn,
        progress_note=str(context.get("last_progress_note") or "") or reason,
        wait_reason=str(context.get("wait_reason") or ""),
        stuck_reason=reason,
        meaningful_event="role_stuck_detected",
    )
    _append_jsonl(
        _events_file(wf),
        _event(
            wf,
            "role_stuck_detected",
            {
                "role": role,
                "turn": turn,
                "reason": reason,
                "detail": detail,
                "fingerprint": str(fingerprint.get("fingerprint") or ""),
                "repeat_count": int(fingerprint.get("count") or 1),
                "workflow_repeat_count": int(escalation.get("count") or 1),
                "retry_limit": retry_limit,
            },
        ),
    )
    _append_jsonl(
        _events_file(wf),
        _event(
            wf,
            "role_escalated",
            {
                "role": role,
                "turn": turn,
                "next_turn": "se_unblock",
                "reason": reason,
            },
        ),
    )
    return {
        "ok": True,
        "workflow_id": wf,
        "state": state,
        "step": turn,
        "stuck_escalated": True,
    }


def _run_role_turn(
    *,
    state: Dict[str, Any],
    role: str,
    turn: str,
    message: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    wf = str(state.get("workflow_id") or "")
    turn_name = str(turn or "").strip() or "unknown_turn"
    started_utc = _utc_now_iso()
    send_opts = dict(options or {})
    send_opts.setdefault("workflow", True)
    send_opts["prompt_file"] = _agents.canonical_prompt_file(
        send_opts.get("prompt_file"),
        role=role,
        workflow=True,
    )
    slice_timeout_s = _role_slice_timeout_s(state, role, send_opts.get("timeout_s"))
    send_opts["timeout_s"] = slice_timeout_s
    hb_s = _normalize_heartbeat_interval_s(send_opts.get("heartbeat_interval_s") or state.get("heartbeat_interval_s"))
    send_opts["heartbeat_interval_s"] = hb_s
    _set_runtime_detail(
        state,
        detail="running_active",
        role=role,
        turn=turn_name,
        started_utc=started_utc,
        timeout_s=slice_timeout_s,
        progress_note=f"{role} started {turn_name}.",
        wait_reason="",
        meaningful_event="role_turn_started",
        clear_stuck=True,
    )
    repo_progress_baseline = _repo_progress_snapshot()
    state["active_repo_progress_baseline_marker"] = str(repo_progress_baseline.get("marker") or "")
    state["active_repo_progress_marker"] = str(repo_progress_baseline.get("marker") or "")
    state["active_repo_progress_path_count"] = int(repo_progress_baseline.get("path_count") or 0)
    state["active_repo_progress_seen"] = False
    _save_state(state)

    _append_jsonl(
        _events_file(wf),
        _event(
            wf,
            "role_turn_started",
            {
                "turn": turn_name,
                "role": role,
                "timeout_s": int(slice_timeout_s),
                "heartbeat_interval_s": int(hb_s),
                "started_utc": started_utc,
            },
        ),
    )

    stop_evt = threading.Event()

    def _heartbeat_loop() -> None:
        seq = 0
        while not stop_evt.wait(max(1, int(hb_s))):
            seq += 1
            _heartbeat_pulse(
                workflow_id=wf,
                turn=turn_name,
                role=role,
                seq=seq,
                started_utc=started_utc,
            )

    hb_thread = threading.Thread(target=_heartbeat_loop, name=f"orch-hb-{wf}-{turn_name}", daemon=True)
    hb_thread.start()
    t0 = time.time()
    ok = False
    txt = ""
    err = ""
    last_progress_note = ""
    progress_events = 0
    stuck_reason = ""
    stuck_detail = ""
    try:
        if getattr(_agents, "_agents_backend")() == "codex":
            final_evt: Dict[str, Any] = {}
            saw_delta = False
            progress_timeout_s = _role_progress_timeout_s(state, role)
            output_grace_s = min(progress_timeout_s, _normalize_role_output_grace_s(state.get("role_output_grace_s")))
            waiting_notice_s = min(progress_timeout_s, _role_waiting_notice_s(progress_timeout_s))
            wait_observation_interval_s = max(1, min(15, int(waiting_notice_s)))
            stream_q: "queue.Queue[Dict[str, Any]]" = queue.Queue()
            producer_done = threading.Event()
            stop_requested = False
            stop_deadline = 0.0
            last_progress_at = time.monotonic()
            last_event_at = last_progress_at
            last_wait_observation_at = 0.0

            def _stream_role_events() -> None:
                try:
                    for evt in _agents.send_message_stream_events(
                        role=role,
                        binding=dict(state["binding"]),
                        message=message,
                        options=send_opts,
                    ):
                        stream_q.put({"kind": "event", "event": dict(evt)})
                except Exception as exc:
                    stream_q.put({"kind": "stream_error", "error": str(exc)})
                finally:
                    producer_done.set()
                    stream_q.put({"kind": "stream_eof"})

            producer = threading.Thread(
                target=_stream_role_events,
                name=f"orch-role-stream-{wf}-{turn_name}",
                daemon=True,
            )
            producer.start()

            while True:
                try:
                    item = stream_q.get(timeout=0.5)
                except queue.Empty:
                    now_mono = time.monotonic()
                    silence_s = now_mono - last_progress_at
                    idle_event_s = now_mono - last_event_at
                    if (not stop_requested) and idle_event_s >= float(waiting_notice_s) and (
                        last_wait_observation_at <= 0.0 or (now_mono - last_wait_observation_at) >= float(wait_observation_interval_s)
                    ):
                        wait_snapshot = _describe_role_wait_state(state=state, role=role)
                        repo_progress = _observe_repo_progress_for_turn(state=state, role=role)
                        wait_note = str(wait_snapshot.get("summary") or f"Waiting for {role} command output.")
                        wait_fingerprint_summary = str(wait_snapshot.get("fingerprint_summary") or wait_note)
                        if bool(repo_progress.get("ever_progress")):
                            wait_fingerprint_summary = (
                                f"{wait_fingerprint_summary} | repo-progress-seen"
                            )
                        wait_fp = _record_activity_fingerprint(
                            state=state,
                            role=role,
                            turn=turn_name,
                            category="wait_snapshot",
                            summary=wait_fingerprint_summary,
                        )
                        state["active_worker_pid"] = int(wait_snapshot.get("pid") or 0)
                        state["active_worker_summary"] = _compact_progress_text(wait_note, max_chars=240)
                        state["active_wait_repeat_count"] = int(wait_fp.get("count") or 1)
                        if bool(repo_progress.get("changed")) and bool(repo_progress.get("has_progress")):
                            repo_note = str(repo_progress.get("note") or "").strip()
                            if repo_note:
                                last_progress_note = repo_note
                                progress_events += 1
                                last_progress_at = now_mono
                                last_wait_observation_at = 0.0
                                _record_role_progress(
                                    state=state,
                                    role=role,
                                    turn=turn_name,
                                    note=repo_note,
                                    event_name="role_repo_progress",
                                    detail="running_active",
                                )
                        _set_runtime_detail(
                            state,
                            detail="running_waiting_command",
                            role=role,
                            turn=turn_name,
                            progress_note=last_progress_note or wait_note,
                            wait_reason=wait_note,
                            meaningful_event=("role_waiting_command" if int(wait_fp.get("count") or 0) == 1 else ""),
                        )
                        _append_jsonl(
                            _events_file(wf),
                            _event(
                                wf,
                                "role_waiting_command",
                                {
                                    "role": role,
                                    "turn": turn_name,
                                    "reason": wait_note,
                                    "idle_for_s": int(max(0, idle_event_s)),
                                    "repeat_count": int(wait_fp.get("count") or 1),
                                    "worker_pid": int(wait_snapshot.get("pid") or 0),
                                },
                            ),
                        )
                        last_wait_observation_at = now_mono
                        if int(wait_fp.get("count") or 0) == 1:
                            last_progress_at = now_mono
                        elif int(wait_fp.get("count") or 0) >= _activity_repeat_bound(state) and not bool(repo_progress.get("ever_progress")):
                            stuck_reason = f"{role} kept waiting on the same local command/process state {int(wait_fp.get('count') or 0)} times."
                            stuck_detail = "blocked_validation_loop"
                            stop_requested = True
                    if (not stop_requested) and saw_delta and idle_event_s >= float(output_grace_s):
                        stuck_reason = f"{role} emitted a response but did not finish cleanly within {int(output_grace_s)}s."
                        stuck_detail = _stuck_detail_for_watchdog(reason=stuck_reason, response_started=True)
                        stop_requested = True
                    elif (not stop_requested) and silence_s >= float(progress_timeout_s):
                        stuck_reason = f"{role} made no meaningful progress for {int(progress_timeout_s)}s."
                        stuck_detail = _stuck_detail_for_watchdog(reason=stuck_reason, response_started=saw_delta)
                        stop_requested = True
                    if stop_requested:
                        stop_deadline = stop_deadline or (now_mono + 10.0)
                        stop_result = _stop_codex_role_worker(state=state, role=role, reason=stuck_reason)
                        _append_jsonl(
                            _events_file(wf),
                            _event(
                                wf,
                                "role_turn_interrupted",
                                {
                                    "role": role,
                                    "turn": turn_name,
                                    "reason": stuck_reason,
                                    "detail": stuck_detail,
                                    "stop_requested": bool(stop_result.get("stopped")),
                                },
                            ),
                        )
                    if stop_requested and producer_done.is_set():
                        break
                    if stop_requested and stop_deadline and now_mono >= stop_deadline:
                        err = stuck_reason
                        break
                    continue

                kind = str(item.get("kind") or "").strip().lower()
                now_mono = time.monotonic()
                if kind == "event":
                    evt = item.get("event") if isinstance(item.get("event"), dict) else {}
                    evt_type = str(evt.get("type") or "").strip().lower()
                    if evt_type and evt_type != "start":
                        last_event_at = now_mono
                    if evt_type == "commentary":
                        note = _compact_progress_text(str(evt.get("text") or ""))
                        if note:
                            command_note = _extract_command_fingerprint_text(note)
                            if command_note:
                                activity = _record_activity_fingerprint(
                                    state=state,
                                    role=role,
                                    turn=turn_name,
                                    category="command_note",
                                    summary=command_note,
                                )
                                state["active_worker_summary"] = _compact_progress_text(command_note, max_chars=240)
                                state["active_wait_repeat_count"] = int(activity.get("count") or 1)
                                if int(activity.get("count") or 0) == 1:
                                    last_progress_note = f"Running {command_note}."
                                    progress_events += 1
                                    last_progress_at = now_mono
                                    last_wait_observation_at = 0.0
                                    _record_role_progress(
                                        state=state,
                                        role=role,
                                        turn=turn_name,
                                        note=last_progress_note,
                                        event_name="role_command_started",
                                        detail="running_active",
                                    )
                                else:
                                    _set_runtime_detail(
                                        state,
                                        detail="running_waiting_command",
                                        role=role,
                                        turn=turn_name,
                                        progress_note=last_progress_note or f"Running {command_note}.",
                                        wait_reason=f"Still on {command_note}.",
                                    )
                                    _append_jsonl(
                                        _events_file(wf),
                                        _event(
                                            wf,
                                            "role_waiting_command",
                                            {
                                                "role": role,
                                                "turn": turn_name,
                                                "reason": f"Still on {command_note}.",
                                                "repeat_count": int(activity.get("count") or 1),
                                            },
                                        ),
                                    )
                                    if int(activity.get("count") or 0) >= _activity_repeat_bound(state):
                                        stuck_reason = f"{role} repeated the same command/status note {int(activity.get('count') or 0)} times without new evidence."
                                        stuck_detail = "blocked_validation_loop"
                                        stop_requested = True
                            else:
                                if note != str(state.get("active_progress_note") or "").strip():
                                    last_progress_note = note
                                    progress_events += 1
                                    last_progress_at = now_mono
                                    last_wait_observation_at = 0.0
                                    _record_role_progress(
                                        state=state,
                                        role=role,
                                        turn=turn_name,
                                        note=note,
                                        event_name="role_progress_note",
                                        detail="running_active",
                                    )
                    elif evt_type == "delta":
                        last_progress_at = now_mono
                        last_wait_observation_at = 0.0
                        if not saw_delta:
                            saw_delta = True
                            note = f"{role} is drafting the response."
                            last_progress_note = note
                            progress_events += 1
                            _record_role_progress(
                                state=state,
                                role=role,
                                turn=turn_name,
                                note=note,
                                event_name="role_progress_note",
                                detail="running_active",
                            )
                    elif evt_type == "done":
                        final_evt = dict(evt)
                        break
                    continue

                if kind == "stream_error":
                    err = str(item.get("error") or "role stream failed")
                    break
                if kind == "stream_eof" and producer_done.is_set():
                    break
            producer.join(timeout=2.0)
            if not final_evt and not err:
                if stop_requested and stuck_reason:
                    err = stuck_reason
                elif saw_delta:
                    stuck_reason = f"{role} emitted a response but the stream ended without a final completion event."
                    stuck_detail = _stuck_detail_for_watchdog(reason=stuck_reason, response_started=True)
                    err = stuck_reason
            if err and not final_evt and not stop_requested:
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "role_turn_interrupted",
                        {
                            "role": role,
                            "turn": turn_name,
                            "reason": err,
                            "detail": stuck_detail or _stuck_detail_for_watchdog(reason=err, response_started=saw_delta),
                            "stop_requested": False,
                        },
                    ),
                )
            assistant = final_evt.get("assistant") if isinstance(final_evt.get("assistant"), dict) else {}
            ok = bool(final_evt.get("ok"))
            txt = str(assistant.get("content") or final_evt.get("error") or err or "")
            err = str(final_evt.get("error") or err or "")
            if stop_requested and not ok and stuck_reason:
                err = stuck_reason
                if not txt.strip():
                    txt = stuck_reason
        else:
            try:
                resp = _agents.send_message(
                    role=role,
                    binding=dict(state["binding"]),
                    message=message,
                    options=send_opts,
                )
                txt = _role_response_text(resp)
                ok = bool(resp.get("ok"))
                if not txt.strip() and not ok:
                    txt = str((resp.get("assistant") or {}).get("content") or "")
            except Exception as exc:
                ok = False
                err = str(exc)
                txt = err
    finally:
        stop_evt.set()
        hb_thread.join(timeout=2.0)
    dt_ms = int(max(0.0, (time.time() - t0) * 1000.0))
    result_kind = "ok" if ok else ("timed_out" if "timed out" in str(err or txt).lower() else "error")
    _append_jsonl(
        _events_file(wf),
        _event(
            wf,
            "role_turn_finished",
            {
                "turn": turn_name,
                "role": role,
                "ok": ok,
                "duration_ms": int(dt_ms),
                "response_chars": len(str(txt or "")),
                "result": result_kind,
                "progress_events": int(progress_events),
            },
        ),
    )
    if ok:
        _set_runtime_detail(
            state,
            detail="running_active",
            role=role,
            turn=turn_name,
            progress_note=last_progress_note or f"{role} completed {turn_name}.",
            wait_reason="",
            meaningful_event="role_turn_finished",
        )
    return {
        "ok": ok,
        "text": txt,
        "error": err,
        "timed_out": result_kind == "timed_out",
        "duration_ms": int(dt_ms),
        "progress_events": int(progress_events),
        "last_progress_note": last_progress_note,
        "stuck_reason": stuck_reason,
        "stuck_detail": stuck_detail,
    }


def start_workflow(
    *,
    binding: Dict[str, Any],
    phase_label: str,
    objective: str,
    options: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    b = _validate_binding(binding)
    pis_defaults = _pis_meta_defaults(b)
    phase = str(phase_label or "").strip() or str(pis_defaults.get("phase_label") or "").strip() or "PIS Workflow"
    opts = options if isinstance(options, dict) else {}
    subphases = _subphases_from_options(opts)
    try:
        max_iter_raw = int(opts.get("max_iterations")) if opts.get("max_iterations") is not None else DEFAULT_MAX_ITERATIONS
    except Exception:
        max_iter_raw = DEFAULT_MAX_ITERATIONS
    max_iter = max(1, min(8, int(max_iter_raw)))
    obj = _normalize_workflow_objective(
        binding=b,
        phase_label=phase,
        objective=str(objective or "").strip() or str(pis_defaults.get("objective") or "").strip(),
    )
    if str(b.get("type") or "") == "ledger_uid":
        obj = _canonical_workflow_objective_for_binding(b, phase) or obj
    elif _workflow_phase_uses_canonical_objective(phase):
        obj = _canonical_workflow_objective_for_binding(b, phase) or obj
    if not obj:
        raise AgentOrchestrationError("objective is required (or inferable from PIS metadata)")
    try:
        max_infra_retries = int(opts.get("max_infra_retries") or _default_max_infra_retries())
    except Exception:
        max_infra_retries = _default_max_infra_retries()
    max_infra_retries = max(0, min(100, max_infra_retries))
    heartbeat_interval_s = _normalize_heartbeat_interval_s(opts.get("heartbeat_interval_s"))
    turn_timeout_s = _normalize_timeout_s(opts.get("timeout_s"))
    role_slice_timeouts = _normalize_role_slice_timeouts(opts.get("role_slice_timeouts"))
    role_progress_timeouts = _normalize_role_progress_timeouts(opts.get("role_progress_timeouts"))
    role_output_grace_s = _normalize_role_output_grace_s(opts.get("role_output_grace_s"))
    failure_retry_bound = _normalize_failure_retry_bound(opts.get("failure_retry_bound"))
    stuck_retry_limit = _normalize_stuck_retry_limit(opts.get("stuck_retry_limit"))
    control_mode = _normalize_workflow_control_mode(opts.get("control_mode") or _default_workflow_control_mode())

    wf = _new_workflow_id(b, obj)
    now = _utc_now_iso()
    state = {
        "schema_version": SCHEMA_STATE,
        "workflow_id": wf,
        "binding": b,
        "phase_label": phase,
        "objective": obj,
        "status": "idle",
        "created_utc": now,
        "updated_utc": now,
        "subphases": subphases,
        "subphase_index": 0,
        "iteration": 1,
        "max_iterations": max_iter,
        "max_infra_retries": max_infra_retries,
        "infra_retry_count": 0,
        "heartbeat_interval_s": heartbeat_interval_s,
        "turn_timeout_s": turn_timeout_s,
        "role_slice_timeouts": role_slice_timeouts,
        "role_progress_timeouts": role_progress_timeouts,
        "role_output_grace_s": role_output_grace_s,
        "failure_retry_bound": failure_retry_bound,
        "stuck_retry_limit": stuck_retry_limit,
        "control_mode": control_mode,
        "se_tasking_force_role_turn": bool(opts.get("heartbeat_interval_s") is not None or opts.get("timeout_s") is not None),
        "execution_directive": str(opts.get("execution_directive") or "").strip(),
        "turn": "se_tasking",
        "se_brief": "",
        "builder_output": "",
        "verifier_feedback": "",
        "verifier_verdict": "UNKNOWN",
        "status_detail": "idle",
        "active_role": "",
        "active_wait_reason": "",
        "active_progress_note": "",
        "last_meaningful_progress_utc": "",
        "last_meaningful_progress_event": "",
        "active_slice_timeout_s": 0,
        "active_slice_deadline_utc": "",
        "active_worker_pid": 0,
        "active_worker_summary": "",
        "active_wait_repeat_count": 0,
        "active_host_verification_progress_fingerprint": "",
        "stuck_reason": "",
        "stuck_role": "",
        "stuck_context": {},
        "stuck_recovery_note": "",
        "stuck_escalation_counts": {},
        "role_activity_fingerprints": {},
        "role_failure_fingerprints": {},
        "pending_host_verification_request": {},
        "host_verification_requests": [],
        "host_verification_results": [],
        "latest_host_verification_result": {},
        "latest_host_verification_status": {},
        "intent_validation_report": "",
        "reports": [],
        "workflow_digest": {},
        "last_error": "",
        "last_step_utc": "",
        "repo_residue": {
            "capture_status": "pending",
            "baseline_artifact": "",
            "current_artifact": "",
            "summary_artifact": "",
            "patch_artifact": "",
            "baseline_dirty_path_count": 0,
            "changed_since_start_count": 0,
            "capture_error": "",
        },
        "checkpoint_boundary": {},
        "interrupted_context": {},
        "stop_after_current_requested": False,
        "stop_after_current_reason": "",
    }
    _save_state(state)
    _append_jsonl(
        _events_file(wf),
        _event(
            wf,
            "workflow_started",
            {
                "binding": b,
                "phase_label": phase,
                "objective": obj,
                "max_iterations": max_iter,
            },
        ),
    )
    state = _capture_repo_baseline(state)
    binding_result: Dict[str, Any] = {}
    try:
        from infra.webui import server_lab_assistant_delegation_bindings as _bindings

        binding_result = _bindings.bind_surface_launch(
            _repo_root(),
            surface="workflow",
            binding=b,
            summary=f"{phase}: {obj}" if obj else phase,
            requested_action=f"launch workflow {wf}",
            launch_result={
                "workflow_id": wf,
                "status": "running",
                "reference_status": "execution_ref_active",
                "reference_status_reason": "workflow_started",
            },
            input_payload={"phase_label": phase, "objective": obj, "workflow_id": wf},
            writer="workflow.launch.bind",
            launch_cause="operator_action",
        )
    except Exception as exc:
        binding_result = {"ok": False, "error": str(exc)}
    return {"ok": True, "workflow_id": wf, "state": state, "binding_result": binding_result}


def _set_state_error(state: Dict[str, Any], err: Exception) -> Dict[str, Any]:
    state["status"] = "failed"
    state["status_detail"] = "failed"
    state["last_error"] = str(err)
    state["last_step_utc"] = _utc_now_iso()
    state["active_wait_reason"] = ""
    state["active_role"] = ""
    state = _save_state(state)
    return _capture_nonclean_workflow_repo_residue(state)


def step_workflow(*, workflow_id: str) -> Dict[str, Any]:
    wf = str(workflow_id or "").strip()
    if not wf:
        raise AgentOrchestrationError("workflow_id is required")
    run_lock = _workflow_run_lock(wf)
    if not run_lock.acquire(blocking=False):
        state = _load_state(wf)
        return _resume_noop_result(workflow_id=wf, state=state, reason="already_running")
    try:
        state = _load_state(wf)
        state, reconciled = _maybe_reconcile_interrupted_restart(state)
        if reconciled:
            return {
                "ok": False,
                "workflow_id": wf,
                "state": state,
                "done": False,
                "noop_reason": "interrupted_recovery_required",
            }
        wf = str(state.get("workflow_id") or "")
        status = str(state.get("status") or "").lower()
        if status in {"completed", "cancelled"}:
            return {"ok": True, "workflow_id": wf, "state": state, "done": True}
        if status == "interrupted":
            state["status"] = "idle"
            _save_state(state)
        if bool(state.get("stop_after_current_requested")) and str(state.get("status") or "").lower() == "idle":
            stop_reason = str(state.get("stop_after_current_reason") or "")
            state["status"] = "cancelled"
            state["status_detail"] = "stopped_after_current"
            state["last_error"] = stop_reason
            state["stop_after_current_requested"] = False
            state["stop_after_current_reason"] = ""
            state["last_step_utc"] = _utc_now_iso()
            _clear_runtime_detail(state, detail="stopped_after_current")
            _append_jsonl(
                _events_file(wf),
                _event(wf, "workflow_stopped_after_current", {"reason": stop_reason}),
            )
            return {"ok": True, "workflow_id": wf, "state": state, "done": True}

        turn = str(state.get("turn") or "")
        if not turn:
            raise AgentOrchestrationError("workflow state missing turn")

        try:
            state["status"] = "running"
            _save_state(state)

            if turn == "se_tasking":
                control_mode = _normalize_workflow_control_mode(state.get("control_mode") or _default_workflow_control_mode())
                force_role_turn = bool(state.get("se_tasking_force_role_turn"))
                if control_mode == "agentic" or force_role_turn:
                    prompt = _compose_se_task_prompt(state)
                    out = _run_role_turn(state=state, role="generalist", turn=turn, message=prompt)
                    if not bool(out.get("ok")):
                        raise AgentOrchestrationError(str(out.get("error") or out.get("text") or "se_tasking failed"))
                    state["se_brief"] = str(out.get("text") or "")
                    brief_source = "agentic" if control_mode == "agentic" else "deterministic_transport_probe"
                else:
                    state["se_brief"] = _deterministic_se_brief(state)
                    brief_source = "deterministic"
                state["builder_output"] = ""
                state["verifier_feedback"] = ""
                state["verifier_verdict"] = "UNKNOWN"
                state["stuck_recovery_note"] = ""
                state["pending_host_verification_request"] = {}
                state["latest_host_verification_result"] = {}
                state["iteration"] = 1
                state["turn"] = "builder_run"
                state["status"] = "idle"
                state["last_step_utc"] = _utc_now_iso()
                _clear_runtime_detail(state)
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "se_tasking_done",
                        {
                            "subphase": state["subphases"][int(state["subphase_index"])],
                            "brief_source": brief_source,
                        },
                    ),
                )
                return {"ok": True, "workflow_id": wf, "state": state, "step": "se_tasking"}

            if turn == "builder_run":
                prompt = _compose_builder_prompt(state)
                out = _run_role_turn(state=state, role="builder", turn=turn, message=prompt)
                if not bool(out.get("ok")):
                    reason = str(out.get("error") or out.get("text") or "builder role failed").strip() or "builder role failed"
                    if str(out.get("stuck_reason") or "").strip():
                        return _escalate_role_stuck(
                            state=state,
                            role="builder",
                            turn=turn,
                            reason=reason,
                            detail=_stuck_detail_for_failure(error=reason, text=str(out.get("text") or "")),
                            error=str(out.get("error") or ""),
                            text=str(out.get("text") or ""),
                        )
                    raise AgentOrchestrationError(reason)
                builder_text = str(out.get("text") or "")
                state["builder_output"] = builder_text
                state["stuck_recovery_note"] = ""
                journal_path = _record_builder_commit_journal(state=state, builder_output=builder_text)
                if journal_path:
                    state["latest_builder_journal_entry"] = journal_path
                state["pending_host_verification_request"] = {}
                state["latest_host_verification_result"] = {}
                state["turn"] = "verifier_run"
                state["status"] = "idle"
                state["last_step_utc"] = _utc_now_iso()
                _clear_runtime_detail(state)
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "builder_run_done",
                        {
                            "subphase": state["subphases"][int(state["subphase_index"])],
                            "iteration": int(state["iteration"]),
                            "journal_entry_path": journal_path or "",
                        },
                    ),
                )
                return {"ok": True, "workflow_id": wf, "state": state, "step": "builder_run"}

            if turn == "verifier_run":
                prompt = _compose_verifier_prompt(state)
                out = _run_role_turn(state=state, role="assurer", turn=turn, message=prompt)
                verifier_text = str(out.get("text") or "")
                if not bool(out.get("ok")):
                    reason = str(out.get("error") or verifier_text or "verifier role failed").strip() or "verifier role failed"
                    latest_result = state.get("latest_host_verification_result") if isinstance(state.get("latest_host_verification_result"), dict) else {}
                    if _is_infra_blocked_feedback(reason) or _is_environment_limited_feedback(reason) or _is_infra_blocked_feedback(verifier_text) or _is_environment_limited_feedback(verifier_text):
                        state["verifier_feedback"] = verifier_text or reason
                        state["stuck_recovery_note"] = ""
                        state["verifier_verdict"] = "UNKNOWN"
                        state["turn"] = "se_decide"
                        state["status"] = "idle"
                        state["last_step_utc"] = _utc_now_iso()
                        _clear_runtime_detail(state)
                        _append_jsonl(
                            _events_file(wf),
                            _event(
                                wf,
                                "verifier_run_done",
                                {
                                    "subphase": state["subphases"][int(state["subphase_index"])],
                                    "iteration": int(state["iteration"]),
                                    "verdict": "UNKNOWN",
                                    "soft_error": reason,
                                },
                            ),
                        )
                        return {"ok": True, "workflow_id": wf, "state": state, "step": "verifier_run"}
                    if str(out.get("stuck_reason") or "").strip():
                        return _escalate_role_stuck(
                            state=state,
                            role="assurer",
                            turn=turn,
                            reason=reason,
                            detail=_stuck_detail_for_failure(error=reason, text=verifier_text, latest_host_result=latest_result),
                            error=str(out.get("error") or ""),
                            text=verifier_text,
                            latest_host_result=latest_result,
                        )
                    raise AgentOrchestrationError(reason)
                raw_verdict = _parse_verdict(verifier_text)
                host_request = _extract_host_verification_request(verifier_text)
                state["verifier_feedback"] = verifier_text
                state["stuck_recovery_note"] = ""
                latest_result = state.get("latest_host_verification_result") if isinstance(state.get("latest_host_verification_result"), dict) else {}
                verdict = _normalize_verifier_verdict(
                    raw_verdict=raw_verdict,
                    feedback=verifier_text,
                    latest_host_result=latest_result,
                )
                state["verifier_verdict"] = verdict
                if verdict != "UNKNOWN" or not (_is_infra_blocked_feedback(verifier_text) or _is_environment_limited_feedback(verifier_text)):
                    state["infra_retry_count"] = 0
                repeated_request = False
                if host_request:
                    try:
                        pending = _build_host_verification_request(state, host_request)
                    except Exception as exc:
                        state["verifier_feedback"] = f"{verifier_text}\n\nHost verification request rejected by orchestrator: {exc}"
                        pending = {}
                    if pending:
                        reusable = _find_matching_host_verification_result(state, pending)
                        if reusable:
                            pending["replay_existing_result"] = True
                            pending["reentry_source"] = str(reusable.get("reentry_source") or "")
                            pending["reentry_request_id"] = str(reusable.get("reentry_request_id") or reusable.get("request_id") or "")
                        reqs = state.get("host_verification_requests") if isinstance(state.get("host_verification_requests"), list) else []
                        reqs.append(pending)
                        state["host_verification_requests"] = reqs
                        state["pending_host_verification_request"] = pending
                        state["turn"] = "host_verification"
                        state["status"] = "idle"
                        state["last_step_utc"] = _utc_now_iso()
                        _set_runtime_detail(
                            state,
                            detail="running_waiting_host_verification",
                            role="assurer",
                            turn="host_verification",
                            progress_note=f"Waiting for host verification {str((((pending.get('action') or {}) if isinstance(pending.get('action'), dict) else {}).get('action_id')) or '').strip()}.",
                            wait_reason=str(pending.get("reason") or "") or "host verification requested",
                            meaningful_event="role_waiting_host_verification",
                        )
                        _append_jsonl(
                            _events_file(wf),
                            _event(
                                wf,
                                "role_waiting_host_verification",
                                {
                                    "role": "assurer",
                                    "turn": "host_verification",
                                    "request_id": str(pending.get("request_id") or ""),
                                    "action_id": str(((pending.get("action") or {}) if isinstance(pending.get("action"), dict) else {}).get("action_id") or ""),
                                    "reason": str(pending.get("reason") or ""),
                                    "replay_existing_result": bool(pending.get("replay_existing_result")),
                                },
                            ),
                        )
                        _append_jsonl(
                            _events_file(wf),
                            _event(
                                wf,
                                "host_verification_requested",
                                {
                                    "subphase": state["subphases"][int(state["subphase_index"])],
                                    "iteration": int(state["iteration"]),
                                    "request_id": str(pending.get("request_id") or ""),
                                    "action_id": str(((pending.get("action") or {}) if isinstance(pending.get("action"), dict) else {}).get("action_id") or ""),
                                    "replay_existing_result": bool(pending.get("replay_existing_result")),
                                },
                            ),
                        )
                        return {"ok": True, "workflow_id": wf, "state": state, "step": "verifier_run", "host_verification_requested": True}
                if verdict != "PASS":
                    fingerprint = _record_failure_fingerprint(
                        state=state,
                        role="assurer",
                        turn=turn,
                        text=verifier_text,
                        latest_host_result=latest_result,
                    )
                    if int(fingerprint.get("count") or 0) >= int(state.get("failure_retry_bound") or _default_failure_retry_bound()):
                        repeat_reason = f"verifier repeated the same failure signature {int(fingerprint.get('count') or 0)} times"
                        return _escalate_role_stuck(
                            state=state,
                            role="assurer",
                            turn=turn,
                            reason=repeat_reason,
                            detail=_stuck_detail_for_failure(text=verifier_text, latest_host_result=latest_result),
                            text=verifier_text,
                            latest_host_result=latest_result,
                            fingerprint_info=fingerprint,
                        )
                state["turn"] = "se_decide"
                state["status"] = "idle"
                state["last_step_utc"] = _utc_now_iso()
                _clear_runtime_detail(state)
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "verifier_run_done",
                        {
                            "subphase": state["subphases"][int(state["subphase_index"])],
                            "iteration": int(state["iteration"]),
                            "verdict": verdict,
                        },
                    ),
                )
                return {"ok": True, "workflow_id": wf, "state": state, "step": "verifier_run"}

            if turn == "host_verification":
                pending = state.get("pending_host_verification_request") if isinstance(state.get("pending_host_verification_request"), dict) else {}
                if not pending:
                    raise AgentOrchestrationError("host_verification turn missing pending request")
                _set_runtime_detail(
                    state,
                    detail="running_waiting_host_verification",
                    role="assurer",
                    turn=turn,
                    progress_note=f"Running host verification {str((((pending.get('action') or {}) if isinstance(pending.get('action'), dict) else {}).get('action_id')) or '').strip()}.",
                    wait_reason=str(pending.get("reason") or "") or "waiting for host verification",
                    meaningful_event="host_verification_started",
                )
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "host_verification_started",
                        {
                            "request_id": str(pending.get("request_id") or ""),
                            "action_id": str((((pending.get("action") or {}) if isinstance(pending.get("action"), dict) else {}).get("action_id")) or ""),
                            "replay_existing_result": bool(pending.get("replay_existing_result")),
                        },
                    ),
                )
                result = _call_host_verification_request(state=state, request=pending)
                results = state.get("host_verification_results") if isinstance(state.get("host_verification_results"), list) else []
                result["request_digest"] = str(pending.get("request_digest") or "")
                results.append(result)
                state["host_verification_results"] = results
                state["latest_host_verification_result"] = result
                state["pending_host_verification_request"] = {}
                state["turn"] = "verifier_run"
                state["status"] = "idle"
                state["last_step_utc"] = _utc_now_iso()
                _clear_runtime_detail(state)
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "host_verification_finished",
                        {
                            "request_id": str(result.get("request_id") or ""),
                            "action_id": str(result.get("action_id") or ""),
                            "ok": bool(result.get("ok")),
                            "failure_kind": str(result.get("failure_kind") or ""),
                            "replayed": bool(result.get("replayed")),
                            "reentry_source": str(result.get("reentry_source") or ""),
                        },
                    ),
                )
                return {"ok": True, "workflow_id": wf, "state": state, "step": "host_verification"}

            if turn == "se_unblock":
                stuck = state.get("stuck_context") if isinstance(state.get("stuck_context"), dict) else {}
                control_mode = _normalize_workflow_control_mode(state.get("control_mode") or _default_workflow_control_mode())
                if control_mode == "agentic":
                    prompt = _compose_se_unblock_prompt(state)
                    out = _run_role_turn(state=state, role="generalist", turn=turn, message=prompt)
                    if not bool(out.get("ok")):
                        raise AgentOrchestrationError(str(out.get("error") or out.get("text") or "se_unblock failed"))
                    decision_obj = _safe_json_from_text(str(out.get("text") or ""))
                    decision = str(decision_obj.get("decision") or "").strip().lower()
                    reason = str(decision_obj.get("reason") or "").strip()
                    instruction = str(decision_obj.get("instruction") or "").strip()
                    decision_source = "agentic"
                else:
                    decision_obj = _deterministic_unblock_decision(state)
                    decision = str(decision_obj.get("decision") or "").strip().lower()
                    reason = str(decision_obj.get("reason") or "").strip()
                    instruction = str(decision_obj.get("instruction") or "").strip()
                    decision_source = "deterministic"
                stuck_role = str(stuck.get("role") or state.get("stuck_role") or "").strip().lower()
                if decision not in {"retry_builder", "retry_verifier", "intent_validation", "stop_phase"}:
                    decision = "retry_builder" if stuck_role == "builder" else "retry_verifier"
                    reason = reason or "fallback to bounded retry"
                workflow_repeat_count = int(stuck.get("workflow_repeat_count") or 1)
                retry_limit = _normalize_stuck_retry_limit(stuck.get("retry_limit") or state.get("stuck_retry_limit"))
                exhausted_retry_budget = workflow_repeat_count >= retry_limit
                if exhausted_retry_budget and decision in {"retry_builder", "retry_verifier"}:
                    decision = "intent_validation" if stuck_role in {"builder", "assurer", "verifier"} else "stop_phase"
                    reason = reason or f"same stuck fingerprint hit retry limit ({retry_limit})"
                    instruction = instruction or "Stop repeating the same blocked loop and summarize the remaining blocker."
                if exhausted_retry_budget:
                    _append_jsonl(
                        _events_file(wf),
                        _event(
                            wf,
                            "stuck_retry_budget_exhausted",
                            {
                                "role": stuck_role,
                                "repeat_count": workflow_repeat_count,
                                "retry_limit": retry_limit,
                                "fingerprint": str(stuck.get("fingerprint") or ""),
                            },
                        ),
                    )
                state["stuck_recovery_note"] = instruction or reason
                if decision == "retry_builder":
                    state["turn"] = "builder_run"
                elif decision == "retry_verifier":
                    state["turn"] = "verifier_run"
                else:
                    state["turn"] = "intent_validation"
                state["status"] = "idle"
                state["last_step_utc"] = _utc_now_iso()
                _clear_runtime_detail(state)
                state["stuck_context"] = {}
                state["stuck_reason"] = ""
                state["stuck_role"] = ""
                state["interrupted_context"] = {}
                _save_state(state)
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "se_unblock_done",
                        {
                            "decision": decision,
                            "reason": reason,
                            "instruction": instruction,
                            "decision_source": decision_source,
                        },
                    ),
                )
                return {"ok": True, "workflow_id": wf, "state": state, "step": "se_unblock", "decision": decision}

            if turn == "se_decide":
                control_mode = _normalize_workflow_control_mode(state.get("control_mode") or _default_workflow_control_mode())
                if control_mode == "agentic":
                    prompt = _compose_se_decision_prompt(state)
                    out = _run_role_turn(state=state, role="generalist", turn=turn, message=prompt)
                    if not bool(out.get("ok")):
                        raise AgentOrchestrationError(str(out.get("error") or out.get("text") or "se_decide failed"))
                    decision_obj = _safe_json_from_text(str(out.get("text") or ""))
                    decision = str(decision_obj.get("decision") or "").strip().lower()
                    reason = str(decision_obj.get("reason") or "").strip()
                    decision_source = "agentic"
                else:
                    decision_obj = _deterministic_se_decision(state)
                    decision = str(decision_obj.get("decision") or "").strip().lower()
                    reason = str(decision_obj.get("reason") or "").strip()
                    decision_source = "deterministic"
                if decision not in {"next_subphase", "extra_iteration", "stop_phase"}:
                    verdict = str(state.get("verifier_verdict") or "UNKNOWN")
                    if verdict == "PASS":
                        decision = "next_subphase"
                        reason = reason or "fallback by PASS verifier verdict"
                    else:
                        at_max = int(state.get("iteration") or 1) >= int(state.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
                        decision = "stop_phase" if at_max else "extra_iteration"
                        reason = reason or "fallback by FAIL/UNKNOWN verifier verdict"

                if _is_phase_e_progressive_context(state) and decision == "next_subphase":
                    verdict = str(state.get("verifier_verdict") or "UNKNOWN").upper()
                    if verdict != "PASS":
                        at_max = int(state.get("iteration") or 1) >= int(state.get("max_iterations") or DEFAULT_MAX_ITERATIONS)
                        decision = "stop_phase" if at_max else "extra_iteration"
                        gate_reason = "phase_e gate blocks next_subphase without PASS verifier verdict"
                        reason = f"{reason}; {gate_reason}" if reason else gate_reason

                sub = str(state["subphases"][int(state["subphase_index"])])
                summary = (
                    f"{sub}: iteration {int(state.get('iteration') or 1)} verdict="
                    f"{str(state.get('verifier_verdict') or 'UNKNOWN')} decision={decision}"
                )
                reports = state.get("reports") if isinstance(state.get("reports"), list) else []
                reports.append(summary)
                state["reports"] = reports

                infra_blocked = bool(
                    str(state.get("verifier_verdict") or "").upper() == "UNKNOWN"
                    and _is_infra_blocked_feedback(str(state.get("verifier_feedback") or ""))
                )
                if decision == "extra_iteration":
                    if infra_blocked:
                        cur_infra = int(state.get("infra_retry_count") or 0)
                        max_infra = int(state.get("max_infra_retries") or _default_max_infra_retries())
                        if cur_infra < max_infra:
                            state["infra_retry_count"] = cur_infra + 1
                            state["turn"] = "verifier_run"
                        else:
                            state["turn"] = "intent_validation"
                    elif int(state.get("iteration") or 1) < int(state.get("max_iterations") or DEFAULT_MAX_ITERATIONS):
                        state["iteration"] = int(state.get("iteration") or 1) + 1
                        state["turn"] = "builder_run"
                    else:
                        state["turn"] = "intent_validation"
                elif decision == "next_subphase":
                    nxt = int(state.get("subphase_index") or 0) + 1
                    if nxt < len(state["subphases"]):
                        state["subphase_index"] = nxt
                        state["iteration"] = 1
                        state["infra_retry_count"] = 0
                        state["turn"] = "se_tasking"
                    else:
                        state["turn"] = "intent_validation"
                else:
                    state["turn"] = "intent_validation"

                state["status"] = "idle"
                state["last_step_utc"] = _utc_now_iso()
                _clear_runtime_detail(state)
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "se_decision_done",
                        {
                            "subphase": sub,
                            "iteration": int(state.get("iteration") or 1),
                            "decision": decision,
                            "reason": reason,
                            "decision_source": decision_source,
                        },
                    ),
                )
                return {"ok": True, "workflow_id": wf, "state": state, "step": "se_decide", "decision": decision}

            if turn == "intent_validation":
                prompt = _compose_intent_prompt(state)
                out = _run_role_turn(state=state, role="generalist", turn=turn, message=prompt)
                if not bool(out.get("ok")):
                    raise AgentOrchestrationError(str(out.get("error") or out.get("text") or "intent_validation failed"))
                raw_report = str(out.get("text") or "")
                state["intent_validation_report_raw"] = raw_report
                if _benchmark_intent_validation_mode(state):
                    state = _capture_nonclean_workflow_repo_residue(state)
                terminal = _intent_validation_terminal_state(state=state, raw_report=raw_report)
                state["intent_validation_report"] = _format_intent_validation_report(state=state, raw_report=raw_report)
                state["turn"] = "done"
                state["status"] = str(terminal.get("status") or "failed")
                state["status_detail"] = str(terminal.get("status_detail") or "intent_validation_unknown")
                state["last_error"] = "" if state["status"] == "completed" else str(terminal.get("reason") or "")
                state["last_step_utc"] = _utc_now_iso()
                _clear_runtime_detail(state, detail=str(state.get("status_detail") or "completed"))
                _append_jsonl(
                    _events_file(wf),
                    _event(
                        wf,
                        "intent_validation_done",
                        {
                            "status": str(state.get("status") or ""),
                            "status_detail": str(state.get("status_detail") or ""),
                            "verdict": _parse_verdict(str(state.get("intent_validation_report_raw") or "")),
                        },
                    ),
                )
                if (not _workflow_completed_cleanly(state)) and (not _benchmark_intent_validation_mode(state)):
                    state = _capture_nonclean_workflow_repo_residue(state)
                return {
                    "ok": bool(state.get("status") == "completed"),
                    "workflow_id": wf,
                    "state": state,
                    "step": "intent_validation",
                    "done": True,
                }

            if turn == "done":
                state["status"] = "completed"
                _clear_runtime_detail(state, detail="completed")
                return {"ok": True, "workflow_id": wf, "state": state, "done": True}

            raise AgentOrchestrationError(f"unsupported turn: {turn}")
        except Exception as e:
            state = _set_state_error(state, e)
            _append_jsonl(_events_file(wf), _event(wf, "workflow_failed", {"error": str(e), "turn": turn}))
            raise
    finally:
        run_lock.release()


def run_workflow(
    *,
    workflow_id: str,
    max_steps: int = 20,
    heartbeat_interval_s: Optional[int] = None,
    timeout_s: Optional[int] = None,
) -> Dict[str, Any]:
    wf = str(workflow_id or "").strip()
    if not wf:
        raise AgentOrchestrationError("workflow_id is required")
    run_lock = _workflow_run_lock(wf)
    if not run_lock.acquire(blocking=False):
        state = _load_state(wf)
        return _resume_noop_result(workflow_id=wf, state=state, reason="already_running")
    try:
        steps = max(1, min(200, int(max_steps)))
        state = _load_state(wf)
        state, reconciled = _maybe_reconcile_interrupted_restart(state)
        if heartbeat_interval_s is not None:
            state["heartbeat_interval_s"] = _normalize_heartbeat_interval_s(heartbeat_interval_s)
        if timeout_s is not None:
            state["turn_timeout_s"] = _normalize_timeout_s(timeout_s)
        if heartbeat_interval_s is not None or timeout_s is not None:
            _save_state(state)
        if reconciled:
            return {
                "ok": False,
                "workflow_id": wf,
                "state": state,
                "steps_run": 0,
                "done": False,
                "noop_reason": "interrupted_recovery_required",
            }
        st = str(state.get("status") or "").lower()
        if _workflow_is_terminal(st):
            return {
                **_resume_noop_result(workflow_id=wf, state=state, reason="terminal"),
                "error": str(state.get("last_error") or "workflow failed") if st == "failed" else "",
            }

        ran = 0
        last: Dict[str, Any] = {}
        for _ in range(steps):
            state = _load_state(wf)
            state, reconciled = _maybe_reconcile_interrupted_restart(state)
            if reconciled:
                return {
                    "ok": False,
                    "workflow_id": wf,
                    "state": state,
                    "steps_run": ran,
                    "done": False,
                    "noop_reason": "interrupted_recovery_required",
                }
            st = str(state.get("status") or "").lower()
            if _workflow_is_terminal(st):
                return {
                    "ok": st != "failed",
                    "workflow_id": wf,
                    "state": state,
                    "steps_run": ran,
                    "done": True,
                    "error": str(state.get("last_error") or "workflow failed") if st == "failed" else "",
                }
            try:
                last = step_workflow(workflow_id=wf)
                ran += 1
            except Exception:
                state = _load_state(wf)
                return {
                    "ok": False,
                    "workflow_id": wf,
                    "state": state,
                    "steps_run": ran,
                    "done": True,
                    "error": str(state.get("last_error") or "workflow failed"),
                }
            state = last.get("state") if isinstance(last.get("state"), dict) else {}
            st = str(state.get("status") or "").lower()
            if _workflow_is_terminal(st):
                return {
                    "ok": st != "failed",
                    "workflow_id": wf,
                    "state": state,
                    "steps_run": ran,
                    "done": True,
                    "error": str(state.get("last_error") or "") if st == "failed" else "",
                }
        state = _load_state(wf)
        return {
            "ok": True,
            "workflow_id": wf,
            "state": state,
            "steps_run": ran,
            "done": _workflow_is_terminal(str(state.get("status") or "").lower()),
        }
    finally:
        run_lock.release()


def get_status(*, workflow_id: str, events_limit: int = 120) -> Dict[str, Any]:
    state = _load_state(workflow_id)
    wf = str(state.get("workflow_id") or "")
    return {
        "ok": True,
        "workflow_id": wf,
        "state": state,
        "events": _tail_events(wf, limit=max(1, min(2000, int(events_limit)))),
    }


def get_conversation(*, workflow_id: str, events_limit: int = 240) -> Dict[str, Any]:
    state = _load_state(workflow_id)
    wf = str(state.get("workflow_id") or "")
    events = _tail_events(wf, limit=max(1, min(5000, int(events_limit))))
    binding = state.get("binding") if isinstance(state.get("binding"), dict) else {}
    session_items, covered_roles = _session_conversation_items(
        binding,
        since_utc=str(state.get("created_utc") or ""),
    )
    synthetic_items = _conversation_items(state, events)
    if session_items:
        items = []
        for row in synthetic_items:
            actor = str(row.get("actor") or "").strip().lower()
            kind = str(row.get("kind") or "").strip().lower()
            if actor in covered_roles and kind in {"message", "summary"}:
                continue
            items.append(row)
        items.extend(session_items)
        items.sort(key=lambda row: (str(row.get("utc") or ""), str(row.get("id") or "")))
    else:
        items = synthetic_items
    return {
        "ok": True,
        "workflow_id": wf,
        "state": state,
        "items": items,
    }


def cancel_workflow(*, workflow_id: str, reason: str = "") -> Dict[str, Any]:
    state = _load_state(workflow_id)
    wf = str(state.get("workflow_id") or "")
    if str(state.get("status") or "").lower() in {"completed", "cancelled"}:
        return {"ok": True, "workflow_id": wf, "state": state}
    if str(state.get("status") or "").lower() == "running":
        state["stop_after_current_requested"] = True
        state["stop_after_current_reason"] = str(reason or "")
        _save_state(state)
        _append_jsonl(
            _events_file(wf),
            _event(wf, "workflow_stop_after_current_requested", {"reason": str(reason or "")}),
        )
        return {"ok": True, "workflow_id": wf, "state": state}
    state["status"] = "cancelled"
    state["last_error"] = str(reason or "")
    state["stop_after_current_requested"] = False
    state["stop_after_current_reason"] = ""
    _save_state(state)
    _append_jsonl(_events_file(wf), _event(wf, "workflow_cancelled", {"reason": str(reason or "")}))
    return {"ok": True, "workflow_id": wf, "state": state}


def list_workflows(*, binding: Optional[Dict[str, Any]] = None, limit: int = 100) -> Dict[str, Any]:
    root = _runtime_root()
    rows: List[Dict[str, Any]] = []
    b: Optional[Dict[str, str]] = None
    if binding is not None:
        b = _validate_binding(binding)

    files = sorted(root.glob("workflow__*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    lim = max(1, min(2000, int(limit)))
    for p in files:
        st = _read_json(p)
        if not st:
            continue
        if b is not None:
            sb = st.get("binding") if isinstance(st.get("binding"), dict) else {}
            if str(sb.get("type") or "") != b["type"] or str(sb.get("id") or "") != b["id"]:
                continue
        digest = st.get("workflow_digest") if isinstance(st.get("workflow_digest"), dict) else {}
        rows.append(
            {
                "workflow_id": str(st.get("workflow_id") or ""),
                "phase_label": str(st.get("phase_label") or ""),
                "status": str(st.get("status") or ""),
                "updated_utc": str(st.get("updated_utc") or ""),
                "created_utc": str(st.get("created_utc") or ""),
                "binding": st.get("binding") if isinstance(st.get("binding"), dict) else {},
                "turn": str(st.get("turn") or ""),
                "headline": str(digest.get("headline") or ""),
                "summary": str(digest.get("summary") or ""),
                "next_action": str(digest.get("next_action") or ""),
            }
        )
        if len(rows) >= lim:
            break

    return {
        "ok": True,
        "count": len(rows),
        "items": rows,
    }
