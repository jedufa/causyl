from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Optional

from .models import CHECKPOINTS

BIN_RUN = "infra/bin/pipeline_run.py"
BIN_REVIEW = "infra/bin/pipeline_review.py"
BIN_DECIDE = "infra/bin/pipeline_decide.py"


def _run(cmd: list[str], repo_root: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        cmd,
        cwd=str(Path(repo_root).resolve()),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def run_stage_or_checkpoint(repo_root: str, job_id: str, *, stage_key: Optional[str] = None, checkpoint_key: Optional[str] = None, resume: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, BIN_RUN, "--repo-root", repo_root, "--job-id", job_id]
    if resume:
        cmd.append("--resume")
    if stage_key:
        cmd.extend(["--start-stage", stage_key, "--stop-after-stage", stage_key])
    elif checkpoint_key:
        cmd.append("--resume")
        cmd.extend(["--stop-after-stage", checkpoint_key])
    return _run(cmd, repo_root)


def render_review(repo_root: str, job_id: str) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, BIN_REVIEW, "--repo-root", repo_root, "--job-id", job_id]
    return _run(cmd, repo_root)


def persist_decision(repo_root: str, job_id: str, *, decision: str, reason: str, revisions: list[str]) -> subprocess.CompletedProcess[str]:
    cmd = [sys.executable, BIN_DECIDE, "--repo-root", repo_root, "--job-id", job_id, "--decision", decision, "--reason", reason]
    for revision in revisions:
        if revision.strip():
            cmd.extend(["--revision", revision.strip()])
    return _run(cmd, repo_root)
