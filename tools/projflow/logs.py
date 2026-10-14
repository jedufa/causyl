from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from .artifacts import active_run_id, job_dir


def log_files(repo_root: str, job_id: str) -> List[Path]:
    run_id = active_run_id(repo_root, job_id)
    if not run_id:
        return []
    logs_dir = job_dir(repo_root, job_id) / "runs" / run_id / "logs"
    if not logs_dir.exists():
        return []
    return sorted([p for p in logs_dir.iterdir() if p.is_file()])


def latest_log(repo_root: str, job_id: str) -> Optional[Path]:
    files = log_files(repo_root, job_id)
    return files[-1] if files else None


def tail_log(path: Path, lines: int = 40) -> str:
    if not path or not path.exists():
        return "No log available."
    txt = path.read_text(encoding="utf-8").splitlines()
    return "\n".join(txt[-lines:])
