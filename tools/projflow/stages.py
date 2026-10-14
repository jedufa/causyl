from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from .models import VISIBLE_STAGES, ENABLED_STAGE_KEYS, StageView


def load_state(repo_root: str, job_id: str) -> dict:
    path = Path(repo_root).resolve() / "jobs" / job_id / "job_state.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def next_stage_key(state: dict) -> str | None:
    seq = list(state.get("stage_sequence") or [])
    active_run = state.get("runs", {}).get(state.get("active_head_run_id"), {})
    completed = set(active_run.get("completed_stages") or [])
    for key in seq:
        if key not in completed:
            return key
    return None


def stage_views(repo_root: str, job_id: str | None) -> List[StageView]:
    state = load_state(repo_root, job_id) if job_id else {}
    active_run = state.get("runs", {}).get(state.get("active_head_run_id"), {})
    completed = set(active_run.get("completed_stages") or [])
    nxt = next_stage_key(state)
    current_status = str(state.get("status") or "INIT")
    views: List[StageView] = []
    for key, label in VISIBLE_STAGES:
        completed_flag = key in completed
        runnable = False
        locked_reason = ""
        marker = "-"
        if completed_flag:
            marker = "✓"
        if key not in ENABLED_STAGE_KEYS:
            locked_reason = "Not enabled in this front-end version yet"
        elif not job_id:
            if key == "script_1_job_intake":
                runnable = True
                marker = ">"
            else:
                locked_reason = "Create or open a job first"
        else:
            if completed_flag:
                marker = "✓"
            elif current_status == "WAITING_REVIEW" and key != "script_7_patch_plan":
                locked_reason = "Planning review decision required first"
            elif nxt == key:
                runnable = True
                marker = ">"
            else:
                locked_reason = "Previous stage incomplete"
        views.append(StageView(key=key, label=label, runnable=runnable, completed=completed_flag, locked_reason=locked_reason, marker=marker))
    return views


def checkpoint_options() -> List[Tuple[str, str]]:
    from .models import CHECKPOINTS
    return CHECKPOINTS[:]
