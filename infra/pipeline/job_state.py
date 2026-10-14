from __future__ import annotations

import contextlib
import os
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from .core import PipelineError, load_json, save_json_atomic, utc_now_iso

JOB_ALLOWED_STATUSES = {
    "INIT",
    "RUNNING",
    "WAITING_REVIEW",
    "WAITING_RETRY_DECISION",
    "RETRYING",
    "FINALIZED",
    "ABORTED",
}
RUN_ALLOWED_STATUSES = JOB_ALLOWED_STATUSES
BRANCH_ALLOWED_STATUSES = {"RUNNING", "WAITING_REVIEW", "WAITING_RETRY_DECISION", "FINALIZED", "ABORTED", "SUPERSEDED"}
BRANCH_PURPOSES = {"primary", "repair", "regenerate", "manual_replay", "exploratory", "local_fix", "plan_revision", "context_expansion", "task_reinterpretation"}
REVIEW_STATUSES = {"PENDING", "REVIEW_RENDERED", "APPROVED", "REJECTED", "REVISION_REQUESTED"}


class JobStateStore:
    def __init__(self, job_dir: str | os.PathLike[str], *, manifest_name: str = "job_state.json") -> None:
        self.job_dir = Path(job_dir).resolve()
        self.job_dir.mkdir(parents=True, exist_ok=True)
        self.state_path = self.job_dir / manifest_name
        self.lock_path = self.job_dir / f"{manifest_name}.lock"

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        fd: Optional[int] = None
        try:
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode("utf-8"))
            os.close(fd)
            fd = None
            yield
        except FileExistsError as exc:
            raise PipelineError(
                "job_lock_exists",
                f"Job is already being processed: {self.lock_path}",
                details={"lock_path": str(self.lock_path)},
                exit_code=6,
            ) from exc
        finally:
            if fd is not None:
                os.close(fd)
            with contextlib.suppress(FileNotFoundError):
                os.unlink(self.lock_path)

    def _validate_state(self, state: Dict[str, Any]) -> None:
        if str(state.get("status") or "") not in JOB_ALLOWED_STATUSES:
            raise PipelineError("job_state_invalid", "Invalid job status", details={"status": state.get("status")}, exit_code=3)
        stage_sequence = state.get("stage_sequence")
        if not isinstance(stage_sequence, list) or not stage_sequence or not all(str(x).strip() for x in stage_sequence):
            raise PipelineError("job_state_invalid", "stage_sequence must be a non-empty list", exit_code=3)
        branches = state.get("branches")
        runs = state.get("runs")
        if not isinstance(branches, dict) or not isinstance(runs, dict) or not branches or not runs:
            raise PipelineError("job_state_invalid", "branches and runs must be non-empty mappings", exit_code=3)
        active_branch_id = str(state.get("active_branch_id") or "")
        active_run_id = str(state.get("active_head_run_id") or "")
        if active_branch_id not in branches or active_run_id not in runs:
            raise PipelineError(
                "job_state_invalid",
                "active branch/run missing",
                details={"active_branch_id": active_branch_id, "active_run_id": active_run_id},
                exit_code=3,
            )

        for branch_id, branch in branches.items():
            if str(branch.get("status") or "") not in BRANCH_ALLOWED_STATUSES:
                raise PipelineError("job_state_invalid", "Invalid branch status", details={"branch_id": branch_id}, exit_code=3)
            purpose = str(branch.get("branch_purpose") or "")
            if purpose not in BRANCH_PURPOSES:
                raise PipelineError("job_state_invalid", "Invalid branch purpose", details={"branch_id": branch_id, "branch_purpose": purpose}, exit_code=3)
            if str(branch.get("head_run_id") or "") not in runs:
                raise PipelineError("job_state_invalid", "Branch head run missing", details={"branch_id": branch_id}, exit_code=3)
            run_ids = list(branch.get("run_ids") or [])
            if not run_ids:
                raise PipelineError("job_state_invalid", "branch.run_ids must not be empty", details={"branch_id": branch_id}, exit_code=3)
            if str(branch.get("head_run_id") or "") not in run_ids:
                raise PipelineError("job_state_invalid", "branch head must be included in run_ids", details={"branch_id": branch_id}, exit_code=3)
            for run_id in run_ids:
                if str(run_id) not in runs:
                    raise PipelineError("job_state_invalid", "branch references unknown run", details={"branch_id": branch_id, "run_id": run_id}, exit_code=3)

        for run_id, run in runs.items():
            if str(run.get("status") or "") not in RUN_ALLOWED_STATUSES:
                raise PipelineError("job_state_invalid", "Invalid run status", details={"run_id": run_id}, exit_code=3)
            branch_id = str(run.get("branch_id") or "")
            if branch_id not in branches:
                raise PipelineError("job_state_invalid", "Run branch missing", details={"run_id": run_id}, exit_code=3)
            if str(run_id) not in [str(x) for x in branches[branch_id].get("run_ids", [])]:
                raise PipelineError("job_state_invalid", "Run missing from owning branch run_ids", details={"run_id": run_id, "branch_id": branch_id}, exit_code=3)
            completed = list(run.get("completed_stages") or [])
            expected_order = [stage for stage in stage_sequence if stage in completed]
            if completed != expected_order:
                raise PipelineError("job_state_invalid", "completed_stages must preserve stage order", details={"run_id": run_id, "completed_stages": completed}, exit_code=3)
            attempt_index = run.get("attempt_index")
            if not isinstance(attempt_index, int) or attempt_index < 0:
                raise PipelineError("job_state_invalid", "attempt_index must be a non-negative integer", details={"run_id": run_id}, exit_code=3)
            artifact_ids = run.get("artifact_ids") or {}
            execution_log_ids = run.get("execution_log_ids") or {}
            if not isinstance(artifact_ids, dict) or not isinstance(execution_log_ids, dict):
                raise PipelineError("job_state_invalid", "artifact_ids and execution_log_ids must be mappings", details={"run_id": run_id}, exit_code=3)
            for stage_key in completed:
                if stage_key not in artifact_ids or stage_key not in execution_log_ids:
                    raise PipelineError("job_state_invalid", "completed stage missing artifact linkage", details={"run_id": run_id, "stage": stage_key}, exit_code=3)
            failure = run.get("failure_classification") or {}
            if not isinstance(failure, dict):
                raise PipelineError("job_state_invalid", "failure_classification must be a mapping", details={"run_id": run_id}, exit_code=3)
            competing = failure.get("competing_hypotheses") or []
            if not isinstance(competing, list):
                raise PipelineError("job_state_invalid", "competing_hypotheses must be a list", details={"run_id": run_id}, exit_code=3)
            confidence = failure.get("confidence", 0.0)
            if not isinstance(confidence, (int, float)):
                raise PipelineError("job_state_invalid", "failure confidence must be numeric", details={"run_id": run_id}, exit_code=3)
            retry_context = run.get("retry_context") or {}
            if not isinstance(retry_context, dict):
                raise PipelineError("job_state_invalid", "retry_context must be a mapping", details={"run_id": run_id}, exit_code=3)
            for key in ["retry_count_total", "retry_count_in_branch"]:
                if not isinstance(retry_context.get(key, 0), int) or int(retry_context.get(key, 0)) < 0:
                    raise PipelineError("job_state_invalid", f"{key} must be a non-negative integer", details={"run_id": run_id}, exit_code=3)
            review_state = run.get("review") or {}
            if not isinstance(review_state, dict):
                raise PipelineError("job_state_invalid", "review state must be a mapping", details={"run_id": run_id}, exit_code=3)
            review_status = str(review_state.get("status") or "PENDING")
            if review_status not in REVIEW_STATUSES:
                raise PipelineError("job_state_invalid", "invalid review status", details={"run_id": run_id, "status": review_status}, exit_code=3)


    def _normalize_legacy_state(self, state: Dict[str, Any]) -> Dict[str, Any]:
        out = dict(state)
        stage_sequence = [str(x) for x in list(out.get("stage_sequence") or []) if str(x).strip()]
        out["stage_sequence"] = stage_sequence
        branches = dict(out.get("branches") or {})
        runs = dict(out.get("runs") or {})
        for branch_id, branch in list(branches.items()):
            item = dict(branch or {})
            item.setdefault("branch_purpose", "primary")
            item.setdefault("created_from_run_id", None)
            item.setdefault("run_ids", [])
            if not item["run_ids"] and item.get("head_run_id"):
                item["run_ids"] = [str(item["head_run_id"])]
            item.setdefault("lineage_notes", [])
            branches[branch_id] = item
        for run_id, run in list(runs.items()):
            item = dict(run or {})
            item.setdefault("attempt_index", 0)
            item.setdefault("reentry_stage", None)
            item.setdefault("execution_log_ids", {})
            item.setdefault("failure_classification", {"primary_hypothesis": None, "competing_hypotheses": [], "confidence": 0.0, "rationale_trace": []})
            item.setdefault("retry_context", {"retry_count_total": 0, "retry_count_in_branch": 0, "retry_mode": None, "retry_reason": None, "recommended_branch_action": None})
            item.setdefault("lineage_notes", [])
            review = dict(item.get("review") or {})
            review.setdefault("required", "script_7_patch_plan" in stage_sequence)
            review.setdefault("status", "PENDING")
            review.setdefault("planning_review_artifact_id", None)
            review.setdefault("decision_artifact_id", None)
            review.setdefault("decision", None)
            review.setdefault("decision_reason", None)
            review.setdefault("requested_revisions", [])
            if review.get("decision") == "APPROVE":
                review["status"] = "APPROVED"
            elif review.get("decision") == "REJECT":
                review["status"] = "REJECTED"
            elif review.get("decision") == "REVISE_AND_RERUN":
                review["status"] = "REVISION_REQUESTED"
            elif review.get("planning_review_artifact_id"):
                review["status"] = "REVIEW_RENDERED"
            item["review"] = review
            runs[run_id] = item
            branch_id = str(item.get("branch_id") or "")
            if branch_id in branches:
                run_ids = [str(x) for x in branches[branch_id].get("run_ids", [])]
                if str(run_id) not in run_ids:
                    run_ids.append(str(run_id))
                branches[branch_id]["run_ids"] = run_ids
                branches[branch_id].setdefault("created_from_run_id", item.get("parent_run_id"))
        out["branches"] = branches
        out["runs"] = runs
        return out

    def load(self) -> Dict[str, Any]:
        if not self.state_path.exists():
            raise PipelineError(
                "job_state_missing",
                f"Job state file not found: {self.state_path}",
                details={"path": str(self.state_path)},
                exit_code=3,
            )
        state = self._normalize_legacy_state(load_json(self.state_path))
        self._validate_state(state)
        return state

    def save(self, state: Dict[str, Any]) -> None:
        out = dict(state)
        out["updated_at"] = utc_now_iso()
        self._validate_state(out)
        save_json_atomic(self.state_path, out)

    def _new_branch_record(
        self,
        *,
        branch_id: str,
        run_id: str,
        now: str,
        parent_branch_id: Optional[str],
        parent_run_id: Optional[str],
        branch_purpose: str,
    ) -> Dict[str, Any]:
        return {
            "branch_id": str(branch_id),
            "parent_branch_id": parent_branch_id,
            "branch_purpose": str(branch_purpose),
            "status": "RUNNING",
            "created_from_run_id": parent_run_id,
            "head_run_id": str(run_id),
            "run_ids": [str(run_id)],
            "created_at": now,
            "updated_at": now,
            "lineage_notes": [],
        }

    def _new_run_record(
        self,
        *,
        run_id: str,
        branch_id: str,
        now: str,
        parent_run_id: Optional[str],
        attempt_index: int,
        reentry_stage: Optional[str],
        retry_count_total: int,
        retry_count_in_branch: int,
        retry_mode: Optional[str],
        retry_reason: Optional[str],
        recommended_branch_action: Optional[str],
    ) -> Dict[str, Any]:
        return {
            "run_id": str(run_id),
            "branch_id": str(branch_id),
            "parent_run_id": parent_run_id,
            "attempt_index": int(attempt_index),
            "reentry_stage": reentry_stage,
            "status": "INIT",
            "current_stage": None,
            "completed_stages": [],
            "failed_stage": None,
            "failure_classification": {
                "primary_hypothesis": None,
                "competing_hypotheses": [],
                "confidence": 0.0,
                "rationale_trace": [],
            },
            "retry_context": {
                "retry_count_total": int(retry_count_total),
                "retry_count_in_branch": int(retry_count_in_branch),
                "retry_mode": retry_mode,
                "retry_reason": retry_reason,
                "recommended_branch_action": recommended_branch_action,
            },
            "artifact_ids": {},
            "execution_log_ids": {},
            "review": {
                "required": True,
                "status": "PENDING",
                "planning_review_artifact_id": None,
                "decision_artifact_id": None,
                "decision": None,
                "decision_reason": None,
                "requested_revisions": [],
            },
            "lineage_notes": [],
            "created_at": now,
            "updated_at": now,
        }

    def _next_attempt_index(self, state: Dict[str, Any]) -> int:
        return max([int(dict(run).get("attempt_index", 0)) for run in dict(state.get("runs") or {}).values()] or [0]) + 1

    def initialize_job(
        self,
        *,
        job_id: str,
        branch_id: str,
        run_id: str,
        schema_version: str,
        contract_version: str,
        stage_sequence: List[str],
        parent_run_id: Optional[str] = None,
        parent_branch_id: Optional[str] = None,
        branch_purpose: str = "primary",
    ) -> Dict[str, Any]:
        now = utc_now_iso()
        state = {
            "job_id": str(job_id),
            "active_branch_id": str(branch_id),
            "active_head_run_id": str(run_id),
            "status": "INIT",
            "current_stage": None,
            "schema_version": str(schema_version),
            "contract_version": str(contract_version),
            "stage_sequence": [str(x) for x in stage_sequence],
            "created_at": now,
            "updated_at": now,
            "branches": {
                str(branch_id): self._new_branch_record(
                    branch_id=str(branch_id),
                    run_id=str(run_id),
                    now=now,
                    parent_branch_id=parent_branch_id,
                    parent_run_id=parent_run_id,
                    branch_purpose=branch_purpose,
                )
            },
            "runs": {
                str(run_id): self._new_run_record(
                    run_id=str(run_id),
                    branch_id=str(branch_id),
                    now=now,
                    parent_run_id=parent_run_id,
                    attempt_index=0,
                    reentry_stage=None,
                    retry_count_total=0,
                    retry_count_in_branch=0,
                    retry_mode=None,
                    retry_reason=None,
                    recommended_branch_action=None,
                )
            },
        }
        state["runs"][str(run_id)]["review"]["required"] = "script_7_patch_plan" in stage_sequence
        self.save(state)
        return state

    def get_active_run(self) -> Dict[str, Any]:
        state = self.load()
        return dict(state["runs"][state["active_head_run_id"]])

    def set_run_status(self, run_id: str, status: str, *, current_stage: Optional[str] = None, failed_stage: Optional[str] = None) -> Dict[str, Any]:
        state = self.load()
        if status not in RUN_ALLOWED_STATUSES:
            raise PipelineError("job_state_invalid", "Invalid run status", details={"status": status}, exit_code=3)
        run = dict(state["runs"][run_id])
        branch = dict(state["branches"][run["branch_id"]])
        now = utc_now_iso()
        run["status"] = status
        if current_stage is not None:
            run["current_stage"] = current_stage
            state["current_stage"] = current_stage
        if failed_stage is not None:
            run["failed_stage"] = failed_stage
        run["updated_at"] = now
        state["runs"][run_id] = run
        branch["updated_at"] = now
        branch["head_run_id"] = str(run_id)
        if status in {"ABORTED", "FINALIZED", "WAITING_REVIEW", "WAITING_RETRY_DECISION"}:
            branch["status"] = status
            state["status"] = status
        elif status == "RETRYING":
            branch["status"] = "RUNNING"
            state["status"] = "RETRYING"
        else:
            branch["status"] = "RUNNING"
            state["status"] = "RUNNING"
        state["branches"][run["branch_id"]] = branch
        state["active_head_run_id"] = str(run_id)
        state["active_branch_id"] = str(run["branch_id"])
        self.save(state)
        return state

    def mark_stage_complete(self, *, run_id: str, stage_key: str, artifact_id: str, execution_log_id: str) -> Dict[str, Any]:
        state = self.load()
        run = dict(state["runs"][run_id])
        completed = list(run.get("completed_stages") or [])
        if stage_key not in completed:
            completed.append(stage_key)
        run["completed_stages"] = completed
        artifact_ids = dict(run.get("artifact_ids") or {})
        artifact_ids[stage_key] = str(artifact_id)
        run["artifact_ids"] = artifact_ids
        execution_log_ids = dict(run.get("execution_log_ids") or {})
        execution_log_ids[stage_key] = str(execution_log_id)
        run["execution_log_ids"] = execution_log_ids
        run["current_stage"] = str(stage_key)
        run["status"] = "RUNNING"
        run["updated_at"] = utc_now_iso()
        state["runs"][run_id] = run
        state["active_head_run_id"] = str(run_id)
        state["active_branch_id"] = str(run["branch_id"])
        state["current_stage"] = str(stage_key)
        state["status"] = "RUNNING"
        branch = dict(state["branches"][run["branch_id"]])
        branch["head_run_id"] = str(run_id)
        if str(run_id) not in [str(x) for x in branch.get("run_ids", [])]:
            branch.setdefault("run_ids", []).append(str(run_id))
        branch["status"] = "RUNNING"
        branch["updated_at"] = utc_now_iso()
        state["branches"][run["branch_id"]] = branch
        self.save(state)
        return state

    def record_failure_classification(
        self,
        *,
        run_id: str,
        primary_hypothesis: Optional[str],
        competing_hypotheses: Optional[List[str]] = None,
        confidence: float = 0.0,
        rationale_trace: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        state = self.load()
        run = dict(state["runs"][run_id])
        run["failure_classification"] = {
            "primary_hypothesis": primary_hypothesis,
            "competing_hypotheses": [str(x) for x in (competing_hypotheses or []) if str(x).strip()],
            "confidence": float(confidence),
            "rationale_trace": [str(x) for x in (rationale_trace or []) if str(x).strip()],
        }
        run["updated_at"] = utc_now_iso()
        state["runs"][run_id] = run
        self.save(state)
        return state

    def fork_branch(
        self,
        *,
        parent_run_id: str,
        new_branch_id: str,
        new_run_id: str,
        branch_purpose: str,
        reentry_stage: Optional[str],
        retry_mode: Optional[str],
        retry_reason: Optional[str],
        recommended_branch_action: str = "fork_new_branch",
    ) -> Dict[str, Any]:
        state = self.load()
        if parent_run_id not in state["runs"]:
            raise PipelineError("job_state_invalid", "parent_run_id not found", details={"parent_run_id": parent_run_id}, exit_code=3)
        if branch_purpose not in BRANCH_PURPOSES:
            raise PipelineError("job_state_invalid", "invalid branch purpose", details={"branch_purpose": branch_purpose}, exit_code=3)
        parent_run = dict(state["runs"][parent_run_id])
        parent_branch_id = str(parent_run["branch_id"])
        now = utc_now_iso()
        branch = self._new_branch_record(
            branch_id=new_branch_id,
            run_id=new_run_id,
            now=now,
            parent_branch_id=parent_branch_id,
            parent_run_id=parent_run_id,
            branch_purpose=branch_purpose,
        )
        run = self._new_run_record(
            run_id=new_run_id,
            branch_id=new_branch_id,
            now=now,
            parent_run_id=parent_run_id,
            attempt_index=self._next_attempt_index(state),
            reentry_stage=reentry_stage,
            retry_count_total=int(parent_run.get("retry_context", {}).get("retry_count_total", 0)) + 1,
            retry_count_in_branch=0,
            retry_mode=retry_mode,
            retry_reason=retry_reason,
            recommended_branch_action=recommended_branch_action,
        )
        run["review"]["required"] = state["runs"][parent_run_id]["review"].get("required", True)
        state["branches"][new_branch_id] = branch
        state["runs"][new_run_id] = run
        state["active_branch_id"] = new_branch_id
        state["active_head_run_id"] = new_run_id
        state["status"] = "RETRYING"
        state["current_stage"] = reentry_stage
        self.save(state)
        return state

    def create_child_run(
        self,
        *,
        branch_id: str,
        run_id: str,
        parent_run_id: str,
        reentry_stage: Optional[str] = None,
        retry_mode: Optional[str] = None,
        retry_reason: Optional[str] = None,
        recommended_branch_action: str = "same_branch_reentry",
    ) -> Dict[str, Any]:
        state = self.load()
        if parent_run_id not in state["runs"]:
            raise PipelineError("job_state_invalid", "parent_run_id not found", details={"parent_run_id": parent_run_id}, exit_code=3)
        if branch_id not in state["branches"]:
            raise PipelineError("job_state_invalid", "branch_id not found", details={"branch_id": branch_id}, exit_code=3)
        parent_run = dict(state["runs"][parent_run_id])
        if str(parent_run.get("branch_id")) != str(branch_id):
            raise PipelineError("job_state_invalid", "child run branch mismatch", details={"parent_run_id": parent_run_id, "branch_id": branch_id}, exit_code=3)
        now = utc_now_iso()
        run = self._new_run_record(
            run_id=run_id,
            branch_id=branch_id,
            now=now,
            parent_run_id=parent_run_id,
            attempt_index=self._next_attempt_index(state),
            reentry_stage=reentry_stage,
            retry_count_total=int(parent_run.get("retry_context", {}).get("retry_count_total", 0)) + 1,
            retry_count_in_branch=int(parent_run.get("retry_context", {}).get("retry_count_in_branch", 0)) + 1,
            retry_mode=retry_mode,
            retry_reason=retry_reason,
            recommended_branch_action=recommended_branch_action,
        )
        run["review"]["required"] = parent_run.get("review", {}).get("required", True)
        state["runs"][run_id] = run
        branch = dict(state["branches"][branch_id])
        run_ids = [str(x) for x in branch.get("run_ids", [])]
        if str(run_id) not in run_ids:
            run_ids.append(str(run_id))
        branch["run_ids"] = run_ids
        branch["head_run_id"] = str(run_id)
        branch["status"] = "RUNNING"
        branch["updated_at"] = now
        state["branches"][branch_id] = branch
        state["active_head_run_id"] = str(run_id)
        state["active_branch_id"] = str(branch_id)
        state["status"] = "RETRYING"
        state["current_stage"] = reentry_stage
        self.save(state)
        return state

    def next_stage(self) -> Optional[str]:
        state = self.load()
        run = state["runs"][state["active_head_run_id"]]
        completed = list(run.get("completed_stages") or [])
        reentry_stage = str(run.get("reentry_stage") or "").strip()
        started = not bool(reentry_stage)
        for stage_key in state["stage_sequence"]:
            if not started:
                if stage_key != reentry_stage:
                    continue
                started = True
            if stage_key not in completed:
                return str(stage_key)
        return None

    def record_planning_review(self, *, run_id: str, artifact_id: str) -> Dict[str, Any]:
        state = self.load()
        run = dict(state["runs"][run_id])
        review = dict(run.get("review") or {})
        review["planning_review_artifact_id"] = str(artifact_id)
        review["status"] = "REVIEW_RENDERED"
        run["review"] = review
        run["updated_at"] = utc_now_iso()
        state["runs"][run_id] = run
        self.save(state)
        return state

    def record_operator_decision(
        self,
        *,
        run_id: str,
        artifact_id: str,
        decision: str,
        decision_reason: str,
        requested_revisions: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        state = self.load()
        run = dict(state["runs"][run_id])
        review = dict(run.get("review") or {})
        review["decision_artifact_id"] = str(artifact_id)
        review["decision"] = str(decision)
        review["decision_reason"] = str(decision_reason)
        review["requested_revisions"] = [str(x) for x in (requested_revisions or []) if str(x).strip()]
        if str(decision) == "APPROVE":
            review["status"] = "APPROVED"
        elif str(decision) == "REJECT":
            review["status"] = "REJECTED"
        else:
            review["status"] = "REVISION_REQUESTED"
        run["review"] = review
        run["updated_at"] = utc_now_iso()
        state["runs"][run_id] = run
        self.save(state)
        return state

    def validate_registry_alignment(self, registry_by_artifact_id: Dict[str, Dict[str, Any]]) -> None:
        state = self.load()
        for run_id, run in state["runs"].items():
            for stage_key in run.get("completed_stages") or []:
                artifact_id = str(run.get("artifact_ids", {}).get(stage_key) or "")
                log_id = str(run.get("execution_log_ids", {}).get(stage_key) or "")
                if artifact_id not in registry_by_artifact_id:
                    raise PipelineError("state_registry_drift", "Completed stage artifact missing from registry", details={"run_id": run_id, "stage": stage_key, "artifact_id": artifact_id}, exit_code=3)
                if log_id not in registry_by_artifact_id:
                    raise PipelineError("state_registry_drift", "Execution log artifact missing from registry", details={"run_id": run_id, "stage": stage_key, "artifact_id": log_id}, exit_code=3)
            review = dict(run.get("review") or {})
            for field in ["planning_review_artifact_id", "decision_artifact_id"]:
                artifact_id = str(review.get(field) or "").strip()
                if artifact_id and artifact_id not in registry_by_artifact_id:
                    raise PipelineError("state_registry_drift", "Review artifact missing from registry", details={"run_id": run_id, "field": field, "artifact_id": artifact_id}, exit_code=3)
