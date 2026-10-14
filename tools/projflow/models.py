from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, List, Dict


VISIBLE_STAGES = [
    ("script_1_job_intake", "Stage 1 - Job Intake"),
    ("script_2_task_classifier", "Stage 2 - Task Classifier"),
    ("script_3_search_plan", "Stage 3 - Search Plan"),
    ("script_4_repo_retriever", "Stage 4 - Repo Retriever"),
    ("script_5_context_ranker", "Stage 5 - Context Ranker"),
    ("script_6_context_bundle", "Stage 6 - Context Bundle"),
    ("script_7_patch_plan", "Stage 7 - Patch Plan"),
    ("script_8_patch_generate", "Stage 8 - Patch Generate"),
    ("script_9_artifact_validate", "Stage 9 - Artifact Validate"),
    ("script_10_patch_apply", "Stage 10 - Patch Apply"),
    ("script_11_verify_static", "Stage 11 - Verify Static"),
    ("script_12_verify_runtime", "Stage 12 - Verify Runtime"),
    ("script_13_contract_verify", "Stage 13 - Contract Verify"),
    ("script_14_repair_loop", "Stage 14 - Repair Loop"),
    ("script_15_finalize_job", "Stage 15 - Finalize Job"),
    ("script_16_job_report", "Stage 16 - Job Report"),
]

ENABLED_STAGE_KEYS = {stage for stage, _ in VISIBLE_STAGES[:7]}

CHECKPOINTS = [
    ("script_4_repo_retriever", "Stage 4 - Retrieval complete"),
    ("script_7_patch_plan", "Stage 7 - Planning complete"),
]

DECISION_CHOICES = [
    ("APPROVE", "Approve planning result"),
    ("REVISE_AND_RERUN", "Reject for revision and rerun later"),
]


@dataclass
class JobInfo:
    job_id: str
    status: str = "INIT"
    current_stage: Optional[str] = None
    active_run_id: Optional[str] = None
    latest_artifact: Optional[str] = None
    retry_count: int = 0
    updated_at: Optional[str] = None


@dataclass
class StageView:
    key: str
    label: str
    visible: bool = True
    runnable: bool = False
    completed: bool = False
    locked_reason: str = ""
    marker: str = "-"


@dataclass
class AppState:
    repo_root: str
    current_job_id: Optional[str] = None
    current_run_id: Optional[str] = None
    recents: Dict[str, object] = field(default_factory=dict)
