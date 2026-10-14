from __future__ import annotations

import difflib
import subprocess
from pathlib import Path
from typing import Any, Callable, Dict, List, TypedDict

try:
    from langgraph.graph import END, StateGraph
except ModuleNotFoundError:
    END = None
    StateGraph = None

from agents.jeff import session_store


class JeffState(TypedDict, total=False):
    session: Dict[str, Any]
    repo_path: str
    task: str
    approval_policy: str
    backend: Any
    approval_callback: Any
    inspection: Dict[str, Any]
    proposal: Dict[str, Any]
    diff_preview: str
    approval_granted: bool
    applied: bool
    applied_checkpoint_id: str
    checks_result: List[Dict[str, Any]]
    verification: Dict[str, Any]
    error: str


def _session_summary(session: Dict[str, Any]) -> str:
    turns = list(session.get("turns") or [])
    if not turns:
        return "no prior turns"
    recent = turns[-3:]
    return " | ".join(f"{row.get('turn_id')}:{row.get('status')}" for row in recent)


def _record_transcript_row(session: Dict[str, Any], row: Dict[str, Any]) -> None:
    sid = str(session.get("session_id") or "").strip()
    if sid:
        session_store.append_transcript_row(sid, row)


def _repo_snapshot(repo_root: Path, *, limit: int = 12) -> Dict[str, Any]:
    files = [p for p in repo_root.rglob("*") if p.is_file() and ".git" not in p.parts]
    files = sorted(files)[:limit]
    preview: List[Dict[str, str]] = []
    for path in files:
        try:
            content = path.read_text(encoding="utf-8")
        except Exception:
            continue
        preview.append(
            {
                "path": str(path.relative_to(repo_root)).replace("\\", "/"),
                "preview": content[:400],
            }
        )
    return {"file_count": len(files), "files": preview}


def _normalize_operations(raw: List[Dict[str, Any]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for row in list(raw or []):
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "").strip()
        if not path:
            continue
        out.append({"path": path, "content": str(row.get("content") or "")})
    return out


def _diff_preview(repo_root: Path, operations: List[Dict[str, str]]) -> str:
    chunks: List[str] = []
    for row in operations:
        rel = str(row.get("path") or "")
        target = (repo_root / rel).resolve()
        before = ""
        if target.exists():
            before = target.read_text(encoding="utf-8")
        after = str(row.get("content") or "")
        diff = difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
        chunks.extend(list(diff))
    return "\n".join(chunks).strip()


def _stage_log(state: JeffState, stage: str, message: str, *, alias: str = "", metrics: Dict[str, Any] | None = None, decision: Dict[str, Any] | None = None) -> None:
    session = dict(state.get("session") or {})
    rows = list(session.get("stage_logs") or [])
    row = {
        "utc": session_store.utc_iso(),
        "stage": stage,
        "alias": alias or stage,
        "message": message,
        "metrics": dict(metrics or {}),
        "decision": dict(decision or {}),
    }
    rows.append(row)
    session["stage_logs"] = rows
    state["session"] = session
    _record_transcript_row(
        session,
        {
            "type": "stage",
            "utc": row["utc"],
            "stage": row["stage"],
            "alias": row["alias"],
            "message": row["message"],
            "metrics": row["metrics"],
            "decision": row["decision"],
        },
    )


def _plan(state: JeffState) -> JeffState:
    _stage_log(state, "plan", f"task={state.get('task')}", alias="plan")
    return state


def _inspect(state: JeffState) -> JeffState:
    repo_root = Path(str(state.get("repo_path") or "")).resolve()
    state["inspection"] = _repo_snapshot(repo_root)
    _stage_log(
        state,
        "inspect",
        f"files={state['inspection'].get('file_count')}",
        alias="scan",
        metrics={"file_count": int(state["inspection"].get("file_count") or 0)},
    )
    return state


def _propose(state: JeffState) -> JeffState:
    backend = state.get("backend")
    proposal = backend.propose(
        task=str(state.get("task") or ""),
        inspection=dict(state.get("inspection") or {}),
        session_summary=_session_summary(dict(state.get("session") or {})),
    )
    state["proposal"] = {
        "summary": proposal.summary,
        "operations": _normalize_operations(proposal.operations),
        "checks": list(proposal.checks),
        "claimed_changed_files": list(proposal.claimed_changed_files),
    }
    _stage_log(
        state,
        "propose",
        proposal.summary or "proposal_ready",
        alias="diff",
        metrics={"operations": len(state["proposal"]["operations"])},
    )
    return state


def _verify_proposal(state: JeffState) -> JeffState:
    repo_root = Path(str(state.get("repo_path") or "")).resolve()
    proposal = dict(state.get("proposal") or {})
    operations = _normalize_operations(list(proposal.get("operations") or []))
    if not operations:
        state["verification"] = {"ok": False, "reason": "no_operations"}
        state["error"] = "no operations proposed"
        _stage_log(state, "verify", "proposal_rejected:no_operations", alias="diff", decision={"ok": False, "reason": "no_operations"})
        return state
    claimed = [str(item or "").strip() for item in list(proposal.get("claimed_changed_files") or []) if str(item or "").strip()]
    missing = [rel for rel in claimed if rel and not (repo_root / rel).exists() and rel not in {str(op.get("path") or "") for op in operations}]
    if missing:
        state["verification"] = {"ok": False, "reason": "claimed_changed_files_missing", "missing": missing}
        state["error"] = "claimed changed files missing"
        _stage_log(
            state,
            "verify",
            "proposal_rejected:claimed_changed_files_missing",
            alias="diff",
            decision={"ok": False, "reason": "claimed_changed_files_missing", "missing": missing},
        )
        return state
    state["diff_preview"] = _diff_preview(repo_root, operations)
    state["verification"] = {"ok": True, "reason": "proposal_valid"}
    _stage_log(state, "verify", "proposal_valid", alias="diff", decision={"ok": True, "reason": "proposal_valid"})
    return state


def _approval(state: JeffState) -> JeffState:
    policy = str(state.get("approval_policy") or "ask").strip().lower()
    granted = False
    if policy == "auto":
        granted = True
    elif policy == "never":
        granted = False
    else:
        callback = state.get("approval_callback")
        granted = bool(callback(state))
    state["approval_granted"] = granted
    _stage_log(
        state,
        "approval",
        "approved" if granted else "not_approved",
        alias="ask",
        decision={"approval_policy": policy, "approved": granted},
    )
    return state


def _write_checkpoint(repo_root: Path, session: Dict[str, Any], operations: List[Dict[str, str]]) -> str:
    checkpoint_id = f"cp_{len(list(session.get('checkpoints') or [])) + 1:04d}"
    files: List[Dict[str, Any]] = []
    for row in operations:
        rel = str(row.get("path") or "")
        target = (repo_root / rel).resolve()
        existed = target.exists()
        before = target.read_text(encoding="utf-8") if existed else ""
        files.append({"path": rel, "existed": existed, "before": before})
    checkpoint = {"checkpoint_id": checkpoint_id, "utc": session_store.utc_iso(), "files": files}
    checkpoints = list(session.get("checkpoints") or [])
    checkpoints.append(checkpoint)
    session["checkpoints"] = checkpoints
    session["last_checkpoint_id"] = checkpoint_id
    session_store.save_checkpoint(str(session.get("session_id") or ""), checkpoint)
    return checkpoint_id


def _apply(state: JeffState) -> JeffState:
    if not bool(state.get("approval_granted")):
        _stage_log(state, "apply", "skipped", alias="apply", decision={"applied": False, "reason": "approval_denied"})
        return state
    repo_root = Path(str(state.get("repo_path") or "")).resolve()
    session = dict(state.get("session") or {})
    operations = _normalize_operations(list((state.get("proposal") or {}).get("operations") or []))
    checkpoint_id = _write_checkpoint(repo_root, session, operations)
    for row in operations:
        target = (repo_root / str(row.get("path") or "")).resolve()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(str(row.get("content") or ""), encoding="utf-8")
    state["session"] = session
    state["applied_checkpoint_id"] = checkpoint_id
    state["applied"] = True
    _stage_log(state, "apply", checkpoint_id, alias="apply", decision={"applied": True, "checkpoint_id": checkpoint_id})
    return state


def _run_checks(state: JeffState) -> JeffState:
    results: List[Dict[str, Any]] = []
    if not bool(state.get("applied")):
        state["checks_result"] = results
        _stage_log(state, "checks", "skipped", alias="run", metrics={"check_count": 0})
        return state
    repo_root = Path(str(state.get("repo_path") or "")).resolve()
    for command in list((state.get("proposal") or {}).get("checks") or []):
        proc = subprocess.run(command, cwd=repo_root, shell=True, text=True, capture_output=True)
        results.append(
            {
                "command": command,
                "returncode": int(proc.returncode),
                "stdout": proc.stdout[-2000:],
                "stderr": proc.stderr[-2000:],
            }
        )
    state["checks_result"] = results
    _stage_log(state, "checks", f"count={len(results)}", alias="run", metrics={"check_count": len(results)})
    return state


def _finalize(state: JeffState) -> JeffState:
    repo_root = Path(str(state.get("repo_path") or "")).resolve()
    proposal = dict(state.get("proposal") or {})
    claimed = [str(item or "").strip() for item in list(proposal.get("claimed_changed_files") or []) if str(item or "").strip()]
    missing = []
    for rel in claimed:
        if not (repo_root / rel).exists():
            missing.append(rel)
    verification = dict(state.get("verification") or {})
    if missing:
        verification = {"ok": False, "reason": "claimed_changed_files_missing", "missing": missing}
    elif verification.get("ok") and state.get("applied"):
        verification = {"ok": True, "reason": "applied_and_verified"}
    elif verification.get("ok"):
        verification = {"ok": True, "reason": "proposal_only"}
    state["verification"] = verification
    session = dict(state.get("session") or {})
    turns = list(session.get("turns") or [])
    turn_id = f"turn_{len(turns) + 1:04d}"
    turns.append(
        {
            "turn_id": turn_id,
            "utc": session_store.utc_iso(),
            "task": str(state.get("task") or ""),
            "status": "ok" if verification.get("ok") else "failed",
            "proposal_summary": str(proposal.get("summary") or ""),
            "claimed_changed_files": claimed,
            "approval_granted": bool(state.get("approval_granted")),
            "applied": bool(state.get("applied")),
            "checkpoint_id": str(state.get("applied_checkpoint_id") or ""),
            "verification": verification,
            "checks_result": list(state.get("checks_result") or []),
            "diff_preview": str(state.get("diff_preview") or ""),
        }
    )
    session["turns"] = turns
    state["session"] = session
    _stage_log(
        state,
        "finalize",
        str(verification.get("reason") or ""),
        alias="done",
        decision={"ok": bool(verification.get("ok")), "reason": str(verification.get("reason") or "")},
    )
    return state


def build_graph() -> Any:
    if StateGraph is None:
        class _SequentialGraph:
            def invoke(self, state: JeffState) -> JeffState:
                for stage in (_plan, _inspect, _propose, _verify_proposal, _approval, _apply, _run_checks, _finalize):
                    state = stage(state)
                return state

        return _SequentialGraph()

    graph = StateGraph(JeffState)
    graph.add_node("plan", _plan)
    graph.add_node("inspect", _inspect)
    graph.add_node("propose", _propose)
    graph.add_node("verify", _verify_proposal)
    graph.add_node("approval", _approval)
    graph.add_node("apply", _apply)
    graph.add_node("checks", _run_checks)
    graph.add_node("finalize", _finalize)
    graph.set_entry_point("plan")
    graph.add_edge("plan", "inspect")
    graph.add_edge("inspect", "propose")
    graph.add_edge("propose", "verify")
    graph.add_edge("verify", "approval")
    graph.add_edge("approval", "apply")
    graph.add_edge("apply", "checks")
    graph.add_edge("checks", "finalize")
    graph.add_edge("finalize", END)
    return graph.compile()


def run_task(
    *,
    session: Dict[str, Any],
    repo_path: str,
    task: str,
    approval_policy: str,
    backend: Any,
    approval_callback: Callable[[JeffState], bool] | None = None,
) -> Dict[str, Any]:
    graph = build_graph()
    state: JeffState = {
        "session": dict(session),
        "repo_path": str(Path(repo_path).resolve()),
        "task": str(task or "").strip(),
        "approval_policy": str(approval_policy or "ask").strip().lower(),
        "backend": backend,
        "approval_callback": approval_callback,
        "approval_granted": False,
        "applied": False,
        "checks_result": [],
    }
    result = graph.invoke(state)
    saved = session_store.save_session(dict(result.get("session") or {}))
    result["session"] = saved
    return result


def undo_last_checkpoint(session: Dict[str, Any]) -> Dict[str, Any]:
    repo_root = Path(str(session.get("repo_path") or "")).resolve()
    checkpoints = list(session.get("checkpoints") or [])
    if not checkpoints:
        return {"ok": False, "reason": "no_checkpoint"}
    checkpoint = checkpoints.pop()
    sid = str(session.get("session_id") or "")
    for row in list(checkpoint.get("files") or []):
        rel = str(row.get("path") or "")
        target = (repo_root / rel).resolve()
        if bool(row.get("existed")):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(row.get("before") or ""), encoding="utf-8")
        elif target.exists():
            target.unlink()
    try:
        archived = session_store.archive_checkpoint(sid, str(checkpoint.get("checkpoint_id") or ""))
    except Exception as exc:
        return {"ok": False, "reason": "undo_archive_failed", "error": str(exc)}
    session["checkpoints"] = checkpoints
    session["last_checkpoint_id"] = str(checkpoints[-1].get("checkpoint_id") or "") if checkpoints else ""
    stage_logs = list(session.get("stage_logs") or [])
    row = {"utc": session_store.utc_iso(), "stage": "undo", "alias": "undo", "message": str(checkpoint.get("checkpoint_id") or ""), "metrics": {}, "decision": {"ok": True}}
    stage_logs.append(row)
    session["stage_logs"] = stage_logs
    _record_transcript_row(session, {"type": "stage", **row})
    return {"ok": True, "checkpoint_id": checkpoint.get("checkpoint_id") or "", "archived_to": str(archived)}
