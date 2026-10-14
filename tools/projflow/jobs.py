from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional, Dict, Any

from .models import JobInfo


def jobs_root(repo_root: str) -> Path:
    return Path(repo_root).resolve() / "jobs"


def ensure_job_dirs(repo_root: str, job_id: str) -> Path:
    job_dir = jobs_root(repo_root) / job_id
    (job_dir / "input").mkdir(parents=True, exist_ok=True)
    (job_dir / "runs").mkdir(parents=True, exist_ok=True)
    return job_dir


def list_jobs(repo_root: str) -> List[JobInfo]:
    root = jobs_root(repo_root)
    if not root.exists():
        return []
    items: List[JobInfo] = []
    for child in sorted(root.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        items.append(load_job_info(repo_root, child.name))
    items.sort(key=lambda x: (x.updated_at or "", x.job_id), reverse=True)
    return items


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_job_info(repo_root: str, job_id: str) -> JobInfo:
    job_dir = jobs_root(repo_root) / job_id
    state_path = job_dir / "job_state.json"
    summary_path = job_dir / "pipeline_run_summary.json"
    latest_artifact = None
    if summary_path.exists():
        try:
            summary = load_json(summary_path)
            arts = summary.get("artifacts") or []
            if arts:
                latest_artifact = arts[-1].get("file_path")
        except Exception:
            pass
    if not state_path.exists():
        return JobInfo(job_id=job_id, latest_artifact=latest_artifact)
    try:
        state = load_json(state_path)
        run = state.get("runs", {}).get(state.get("active_head_run_id"), {})
        retry_ctx = run.get("retry_context", {}) or {}
        return JobInfo(
            job_id=job_id,
            status=str(state.get("status") or "INIT"),
            current_stage=state.get("current_stage"),
            active_run_id=state.get("active_head_run_id"),
            latest_artifact=latest_artifact,
            retry_count=int(retry_ctx.get("retry_count_total") or 0),
            updated_at=state.get("updated_at"),
        )
    except Exception:
        return JobInfo(job_id=job_id, latest_artifact=latest_artifact)


def write_raw_request(
    repo_root: str,
    job_id: str,
    *,
    title: str,
    description: str,
    objective: str,
    constraints: List[str],
    acceptance_criteria: List[str],
    allowed_paths: List[str],
    forbidden_paths: List[str],
    raw_spec: str = "",
) -> Path:
    job_dir = ensure_job_dirs(repo_root, job_id)
    path = job_dir / "input" / "00_raw_request.json"
    payload = {
        "title": title,
        "description": description,
        "repo_root": str(Path(repo_root).resolve()),
        "objective": objective,
        "constraints": constraints,
        "acceptance_criteria": acceptance_criteria,
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
        "user_notes": raw_spec or None,
        "metadata": {"input_mode": "structured_spec" if raw_spec else "guided_fields"},
    }
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path
