from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Any


def recents_path(repo_root: str) -> Path:
    return Path(repo_root).resolve() / ".projflow_recents.json"


def load_recents(repo_root: str) -> Dict[str, Any]:
    path = recents_path(repo_root)
    if not path.exists():
        return {"recent_jobs": [], "recent_repo_roots": [str(Path(repo_root).resolve())], "last_job_id": None}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("recent_jobs", [])
            data.setdefault("recent_repo_roots", [str(Path(repo_root).resolve())])
            data.setdefault("last_job_id", None)
            return data
    except Exception:
        pass
    return {"recent_jobs": [], "recent_repo_roots": [str(Path(repo_root).resolve())], "last_job_id": None}


def save_recents(repo_root: str, recents: Dict[str, Any]) -> None:
    path = recents_path(repo_root)
    path.write_text(json.dumps(recents, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def remember_job(repo_root: str, job_id: str) -> Dict[str, Any]:
    recents = load_recents(repo_root)
    jobs = [j for j in recents.get("recent_jobs", []) if j != job_id]
    jobs.insert(0, job_id)
    recents["recent_jobs"] = jobs[:10]
    recents["last_job_id"] = job_id
    save_recents(repo_root, recents)
    return recents
