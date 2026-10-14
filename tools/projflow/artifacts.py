from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


def job_dir(repo_root: str, job_id: str) -> Path:
    return Path(repo_root).resolve() / "jobs" / job_id


def active_run_id(repo_root: str, job_id: str) -> Optional[str]:
    state_path = job_dir(repo_root, job_id) / "job_state.json"
    if not state_path.exists():
        return None
    data = json.loads(state_path.read_text(encoding="utf-8"))
    return data.get("active_head_run_id")


def latest_artifact_path(repo_root: str, job_id: str) -> Optional[Path]:
    summary_path = job_dir(repo_root, job_id) / "pipeline_run_summary.json"
    if not summary_path.exists():
        return None
    data = json.loads(summary_path.read_text(encoding="utf-8"))
    arts = data.get("artifacts") or []
    if not arts:
        return None
    fp = arts[-1].get("file_path")
    return job_dir(repo_root, job_id) / fp if fp and not Path(fp).is_absolute() else Path(fp) if fp else None


def artifact_history(repo_root: str, job_id: str) -> List[Path]:
    run_id = active_run_id(repo_root, job_id)
    if not run_id:
        return []
    run_dir = job_dir(repo_root, job_id) / "runs" / run_id
    files = [p for p in run_dir.iterdir() if p.is_file() and p.suffix in {".json", ".md"}]
    return sorted(files)


def summarize_artifact(path: Path) -> str:
    if not path.exists():
        return f"Artifact missing: {path}"
    if path.suffix == ".md":
        txt = path.read_text(encoding="utf-8").splitlines()
        preview = "\n".join(txt[:20])
        return preview or "(empty markdown artifact)"
    data = json.loads(path.read_text(encoding="utf-8"))
    result = data.get("result", data)
    stage = data.get("stage", "unknown")
    lines = [f"Stage: {stage}"]
    if "patch_plan" in result:
        pp = result["patch_plan"]
        lines.append(f"Files proposed: {len(pp.get('modify_files') or [])}")
        lines.append(f"Risks: {len(pp.get('risks') or [])}")
        lines.append(f"Required tests: {len(pp.get('required_tests') or [])}")
        if pp.get("confidence") is not None:
            lines.append(f"Confidence: {pp.get('confidence')}")
    elif "retrieval" in result:
        r = result["retrieval"]
        files = r.get("file_candidates") or []
        lines.append(f"File candidates: {len(files)}")
        if files:
            top = files[:3]
            lines.append("Top hits: " + ", ".join(item.get("path", "?") for item in top))
    elif "ranked_context" in result:
        rc = result["ranked_context"]
        selected = rc.get("selected_files") or []
        lines.append(f"Selected files: {len(selected)}")
    elif "context_bundle" in result:
        cb = result["context_bundle"]
        stats = cb.get("bundle_stats") or {}
        lines.append(f"Files in bundle: {stats.get('total_files', 0)}")
        lines.append(f"Snippets: {stats.get('total_snippets', 0)}")
        lines.append(f"Chars: {stats.get('total_chars', 0)}")
    elif "task_profile" in result:
        tp = result["task_profile"]
        lines.append(f"Task type: {tp.get('primary_task_type')}")
        lines.append(f"Risk: {tp.get('risk_level')}")
        lines.append(f"Scope: {tp.get('expected_edit_scope')}")
    elif "search_plan" in result:
        sp = result["search_plan"]
        lines.append(f"Queries: {len(sp.get('queries') or [])}")
        lines.append(f"Strategies: {len(sp.get('strategies') or [])}")
    else:
        keys = list(result.keys())[:6] if isinstance(result, dict) else []
        lines.append("Top-level result keys: " + ", ".join(keys))
    return "\n".join(lines)


def load_text_preview(path: Path, max_lines: int = 40) -> str:
    if not path.exists():
        return f"Missing: {path}"
    txt = path.read_text(encoding="utf-8").splitlines()
    preview = txt[:max_lines]
    if len(txt) > max_lines:
        preview.append(f"... ({len(txt) - max_lines} more lines)")
    return "\n".join(preview)
