from __future__ import annotations

import json
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence

from .complexity import measure_code_metrics
from .contracts import SchedulerSnapshot, ScenarioSpec


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _json_ready_list(rows: Iterable[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
    return tuple(dict(row) for row in rows)


def _find_ledger_dir(repo_root: Path, uid: str, *, is_intent: bool = False) -> Path | None:
    normalized_uid = str(uid or "").strip()
    if not normalized_uid:
        return None
    ledger_root = repo_root / "governance" / "ledger"
    subdir = "intent_sheets" if is_intent else "patch_instruction_sheets"
    uid_index = ledger_root / subdir / "uid_index.json"
    payload = json.loads(uid_index.read_text(encoding="utf-8")) if uid_index.exists() else {}
    rel = str(payload.get(normalized_uid) or "").strip()
    if rel:
        candidate = repo_root / rel
        if (candidate / "meta.json").exists():
            return candidate
    for meta_path in sorted((ledger_root / subdir).glob(f"**/{normalized_uid}__*/meta.json")):
        return meta_path.parent
    return None


def _read_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def _collect_family_records_for_parents(repo_root: Path, family_parent_uids: Sequence[str]) -> tuple[Mapping[str, Any], ...]:
    out: List[Mapping[str, Any]] = []
    for parent_uid in [str(item).strip() for item in family_parent_uids if str(item).strip()]:
        parent_dir = _find_ledger_dir(repo_root, parent_uid)
        if parent_dir is None:
            out.append({"parent_uid": parent_uid, "status": "missing_parent_meta"})
            continue
        meta = _read_json(parent_dir / "meta.json")
        integration = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), Mapping) else {}
        child_uids = [str(item).strip() for item in integration.get("child_uids") or [] if str(item).strip()]
        child_statuses: List[str] = []
        for child_uid in child_uids:
            child_dir = _find_ledger_dir(repo_root, child_uid)
            if child_dir is None:
                child_statuses.append("missing")
                continue
            child_meta = _read_json(child_dir / "meta.json")
            child_statuses.append(str(child_meta.get("status") or "missing"))
        out.append(
            {
                "parent_uid": parent_uid,
                "status": str(meta.get("status") or ""),
                "intent_sheet_uid": str(integration.get("intent_sheet_uid") or ""),
                "child_uids": child_uids,
                "child_statuses": child_statuses,
                "intent_validation_child_uid": str(integration.get("intent_validation_child_uid") or ""),
                "required_final_child_uid": str(integration.get("required_final_child_uid") or ""),
            }
        )
    return tuple(out)


class SchedulerBenchmarkAdapter(ABC):
    adapter_kind = "abstract"

    @abstractmethod
    def capture(self, *, scenario: ScenarioSpec) -> SchedulerSnapshot:
        raise NotImplementedError


class FilesystemSnapshotAdapter(SchedulerBenchmarkAdapter):
    adapter_kind = "snapshot_file"

    def __init__(self, snapshot_path: Path) -> None:
        self.snapshot_path = snapshot_path.resolve()

    def capture(self, *, scenario: ScenarioSpec) -> SchedulerSnapshot:
        payload = json.loads(self.snapshot_path.read_text(encoding="utf-8"))
        code_metrics_payload = payload.get("code_metrics") or None
        from .contracts import CodeMetrics

        return SchedulerSnapshot(
            schema_version=str(payload.get("schema_version") or "scheduler_snapshot_v1"),
            snapshot_label=str(payload.get("snapshot_label") or self.snapshot_path.stem),
            scheduler_kind=str(payload.get("scheduler_kind") or "snapshot"),
            adapter_kind=self.adapter_kind,
            captured_at_utc=str(payload.get("captured_at_utc") or _utc_now_iso()),
            scenario_key=str(payload.get("scenario_key") or scenario.key),
            jobs=tuple(payload.get("jobs") or []),
            receipts=tuple(payload.get("receipts") or []),
            compiled_graphs=tuple(payload.get("compiled_graphs") or []),
            continuity_records=tuple(payload.get("continuity_records") or []),
            closeout_records=tuple(payload.get("closeout_records") or []),
            surface_projections={str(key): tuple(value or []) for key, value in (payload.get("surface_projections") or {}).items()},
            family_records=tuple(payload.get("family_records") or []),
            code_metrics=(
                None
                if not isinstance(code_metrics_payload, Mapping)
                else CodeMetrics(
                    file_count=int(code_metrics_payload.get("file_count") or 0),
                    nonempty_line_count=int(code_metrics_payload.get("nonempty_line_count") or 0),
                    branch_count=int(code_metrics_payload.get("branch_count") or 0),
                    function_count=int(code_metrics_payload.get("function_count") or 0),
                )
            ),
            notes=tuple(str(item) for item in payload.get("notes") or []),
        )


class CurrentPlaneSchedulerAdapter(SchedulerBenchmarkAdapter):
    adapter_kind = "current_plane"

    def __init__(
        self,
        *,
        repo_root: Path,
        snapshot_label: str,
        scheduler_kind: str,
        job_id_prefixes: Sequence[str] = (),
        binding_uids: Sequence[str] = (),
        family_parent_uids: Sequence[str] = (),
        code_paths: Sequence[str] = (),
        receipt_limit: int = 3,
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.snapshot_label = snapshot_label
        self.scheduler_kind = scheduler_kind
        self.job_id_prefixes = tuple(str(item).strip() for item in job_id_prefixes if str(item).strip())
        self.binding_uids = tuple(str(item).strip() for item in binding_uids if str(item).strip())
        self.family_parent_uids = tuple(str(item).strip() for item in family_parent_uids if str(item).strip())
        self.code_paths = tuple(str(item).strip() for item in code_paths if str(item).strip())
        self.receipt_limit = max(0, int(receipt_limit))

    def _binding_value(self, row: Mapping[str, Any]) -> str:
        binding = row.get("binding") if isinstance(row.get("binding"), Mapping) else {}
        return str(binding.get("ledger_uid") or binding.get("uid") or "").strip()

    def _row_selected(self, row: Mapping[str, Any]) -> bool:
        job_id = str(row.get("job_id") or "").strip()
        if self.job_id_prefixes and not any(job_id.startswith(prefix) for prefix in self.job_id_prefixes):
            return False
        binding_value = self._binding_value(row)
        if self.binding_uids and binding_value not in self.binding_uids:
            return False
        return True

    def _normalize_row(self, row: Mapping[str, Any]) -> Dict[str, Any]:
        scheduler_projection = row.get("scheduler_projection_v1") if isinstance(row.get("scheduler_projection_v1"), Mapping) else {}
        lifecycle = row.get("lifecycle_projection") if isinstance(row.get("lifecycle_projection"), Mapping) else {}
        state = row.get("state") if isinstance(row.get("state"), Mapping) else {}
        due = row.get("due") if isinstance(row.get("due"), Mapping) else {}
        return {
            "job_id": str(row.get("job_id") or ""),
            "display_title": str(row.get("display_title") or ""),
            "job_kind": str(row.get("job_kind") or ""),
            "binding_uid": self._binding_value(row),
            "depends_on": list(row.get("depends_on") or []),
            "projected_status": str(scheduler_projection.get("projected_status") or ""),
            "projection_reason": str(scheduler_projection.get("projection_reason") or ""),
            "canonical_state": str(scheduler_projection.get("canonical_state") or lifecycle.get("canonical_state") or ""),
            "due_reason": str(due.get("reason") or ""),
            "due_eligible": bool(due.get("eligible")),
            "active_run_id": str(state.get("active_run_id") or ""),
            "last_status": str(state.get("last_status") or ""),
            "active_loop_stage": str(state.get("active_loop_stage") or ""),
            "last_recovered_reason": str(state.get("last_recovered_reason") or ""),
            "last_nonlaunch_reason": str(state.get("last_nonlaunch_reason") or ""),
            "last_receipt_path": str(state.get("last_receipt_path") or ""),
            "job_shape": str(row.get("job_shape") or ""),
            "job_shape_summary": dict(row.get("job_shape_summary") or {}),
        }

    def _collect_receipts(self, rows: Sequence[Mapping[str, Any]]) -> tuple[Mapping[str, Any], ...]:
        from infra.controlplane import scheduled_jobs as sched

        out: List[Mapping[str, Any]] = []
        for row in rows:
            job_id = str(row.get("job_id") or "").strip()
            if not job_id:
                continue
            for receipt in sched.list_receipts(job_id, repo_root=self.repo_root, limit=self.receipt_limit):
                loop = receipt.get("loop") if isinstance(receipt.get("loop"), Mapping) else {}
                recovery = receipt.get("recovery") if isinstance(receipt.get("recovery"), Mapping) else {}
                out.append(
                    {
                        "job_id": job_id,
                        "run_id": str(receipt.get("run_id") or ""),
                        "status": str(receipt.get("status") or ""),
                        "started_utc": str(receipt.get("started_utc") or ""),
                        "finished_utc": str(receipt.get("finished_utc") or ""),
                        "terminal_status": str(receipt.get("terminal_status") or ""),
                        "completed_iterations": int(loop.get("completed_iterations") or 0),
                        "max_iterations": int(loop.get("max_iterations") or 0),
                        "recovery_reason": str(recovery.get("recovery_reason") or ""),
                        "synthetic_closeout": bool(recovery.get("synthetic_closeout")),
                    }
                )
        return tuple(out)

    def _collect_family_records(self) -> tuple[Mapping[str, Any], ...]:
        return _collect_family_records_for_parents(self.repo_root, self.family_parent_uids)

    def capture(self, *, scenario: ScenarioSpec) -> SchedulerSnapshot:
        from infra.controlplane import scheduled_jobs as sched

        rows = [row for row in sched.list_jobs(repo_root=self.repo_root) if self._row_selected(row)]
        jobs = tuple(self._normalize_row(row) for row in rows)
        projected_rows = tuple(
            {
                "job_id": str(item.get("job_id") or ""),
                "projected_status": str(item.get("projected_status") or ""),
                "projection_reason": str(item.get("projection_reason") or ""),
                "canonical_state": str(item.get("canonical_state") or ""),
            }
            for item in jobs
        )
        notes = []
        if projected_rows:
            notes.append("surface_projections synthesized from shared scheduler projection contract")
        return SchedulerSnapshot(
            schema_version="scheduler_snapshot_v1",
            snapshot_label=self.snapshot_label,
            scheduler_kind=self.scheduler_kind,
            adapter_kind=self.adapter_kind,
            captured_at_utc=_utc_now_iso(),
            scenario_key=scenario.key,
            jobs=jobs,
            receipts=self._collect_receipts(rows),
            compiled_graphs=(),
            continuity_records=(),
            closeout_records=(),
            surface_projections={
                "api_list": projected_rows,
                "watch": projected_rows,
                "api_detail": projected_rows,
                "next_job_summary": projected_rows,
            },
            family_records=self._collect_family_records(),
            code_metrics=(measure_code_metrics(self.repo_root, self.code_paths) if self.code_paths else None),
            notes=tuple(notes),
        )


class V2IntentFamilySchedulerAdapter(SchedulerBenchmarkAdapter):
    adapter_kind = "v2_intent_family_shadow"

    def __init__(
        self,
        *,
        repo_root: Path,
        snapshot_label: str,
        scheduler_kind: str,
        parent_uids: Sequence[str],
        code_paths: Sequence[str] = (),
        run_at_utc: str = "2026-04-10T00:00:00Z",
    ) -> None:
        self.repo_root = repo_root.resolve()
        self.snapshot_label = snapshot_label
        self.scheduler_kind = scheduler_kind
        self.parent_uids = tuple(str(item).strip() for item in parent_uids if str(item).strip())
        self.code_paths = tuple(str(item).strip() for item in code_paths if str(item).strip())
        self.run_at_utc = str(run_at_utc or "2026-04-10T00:00:00Z").strip()

    def _job_row(
        self,
        *,
        job: Mapping[str, Any],
        decision: Mapping[str, Any],
        runtime_record: Mapping[str, Any],
        job_id_by_child_uid: Mapping[str, str],
    ) -> Dict[str, Any]:
        decision_value = str(decision.get("decision") or "").strip()
        runtime_state = str(runtime_record.get("runtime_state") or "").strip()
        if decision_value == "retired":
            projected_status = "completed_once"
            projection_reason = "disabled"
            canonical_state = "succeeded"
            due_reason = "disabled"
            due_eligible = False
        elif decision_value == "active":
            projected_status = "active"
            projection_reason = str(decision.get("reason") or "active_run_present")
            canonical_state = runtime_state or "running"
            due_reason = "active_run"
            due_eligible = False
        elif decision_value == "blocked":
            projected_status = "blocked"
            projection_reason = str(decision.get("reason") or "blocked")
            canonical_state = runtime_state if projection_reason.startswith("dependency_pending:") else (runtime_state or "blocked")
            due_reason = projection_reason
            due_eligible = False
        else:
            projected_status = "ready"
            projection_reason = str(decision.get("reason") or "dependencies_satisfied")
            canonical_state = runtime_state
            due_reason = "ready"
            due_eligible = True
        depends_on_job_ids = [str(item).strip() for item in job.get("depends_on_job_ids") or [] if str(item).strip()]
        depends_on = depends_on_job_ids or [
            job_id_by_child_uid[uid]
            for uid in [str(item).strip() for item in job.get("depends_on_uids") or [] if str(item).strip()]
            if uid in job_id_by_child_uid
        ]
        return {
            "job_id": str(job.get("job_id") or ""),
            "display_title": str(job.get("title") or ""),
            "job_kind": "build_verify_loop",
            "binding_uid": str(job.get("child_uid") or ""),
            "depends_on": depends_on,
            "projected_status": projected_status,
            "projection_reason": projection_reason,
            "canonical_state": canonical_state,
            "due_reason": due_reason,
            "due_eligible": due_eligible,
            "active_run_id": str(runtime_record.get("active_run_id") or ""),
            "last_status": str(runtime_record.get("last_status") or ""),
            "active_loop_stage": "",
            "last_recovered_reason": "",
            "last_nonlaunch_reason": "",
            "last_receipt_path": str(runtime_record.get("last_receipt_path") or ""),
            "job_shape": "verification_loop_job",
            "job_shape_summary": {},
        }

    def _receipt_row(self, runtime_record: Mapping[str, Any]) -> Mapping[str, Any] | None:
        last_status = str(runtime_record.get("last_status") or "").strip()
        job_id = str(runtime_record.get("job_id") or "").strip()
        if not last_status or not job_id:
            return None
        return {
            "job_id": job_id,
            "run_id": "",
            "status": last_status,
            "started_utc": "",
            "finished_utc": "",
            "terminal_status": "",
            "completed_iterations": 0,
            "max_iterations": 0,
            "recovery_reason": "",
            "synthetic_closeout": False,
        }

    def capture(self, *, scenario: ScenarioSpec) -> SchedulerSnapshot:
        from infra.controlplane import sched as v2_sched

        jobs: List[Mapping[str, Any]] = []
        receipts: List[Mapping[str, Any]] = []
        compiled_graphs: List[Mapping[str, Any]] = []
        continuity_records: List[Mapping[str, Any]] = []
        family_parent_uids = list(self.parent_uids)
        for parent_uid in self.parent_uids:
            payload = v2_sched.run_shadow_tick(parent_uid=parent_uid, run_at_utc=self.run_at_utc, repo_root=self.repo_root)
            plan = payload.get("plan") if isinstance(payload.get("plan"), Mapping) else {}
            evaluation = payload.get("evaluation") if isinstance(payload.get("evaluation"), Mapping) else {}
            plan_jobs = [row for row in list(plan.get("jobs") or []) if isinstance(row, Mapping)]
            decisions = [row for row in list(evaluation.get("admitted") or []) if isinstance(row, Mapping)]
            runtime_records = [row for row in list(payload.get("runtime_records") or []) if isinstance(row, Mapping)]
            decision_by_uid = {str(row.get("child_uid") or ""): row for row in decisions}
            runtime_by_uid = {str(row.get("child_uid") or ""): row for row in runtime_records}
            job_id_by_child_uid = {str(row.get("child_uid") or ""): str(row.get("job_id") or "") for row in plan_jobs}
            for job in plan_jobs:
                child_uid = str(job.get("child_uid") or "")
                jobs.append(
                    self._job_row(
                        job=job,
                        decision=decision_by_uid.get(child_uid, {}),
                        runtime_record=runtime_by_uid.get(child_uid, {}),
                        job_id_by_child_uid=job_id_by_child_uid,
                    )
                )
            for runtime_record in runtime_records:
                receipt = self._receipt_row(runtime_record)
                if receipt is not None:
                    receipts.append(receipt)
            raw_compilation = plan.get("raw_compilation") if isinstance(plan.get("raw_compilation"), Mapping) else {}
            family_graph = raw_compilation.get("family_graph") if isinstance(raw_compilation.get("family_graph"), Mapping) else {}
            if family_graph:
                compiled_graphs.append(dict(family_graph))
            continuity = evaluation.get("continuity") if isinstance(evaluation.get("continuity"), Mapping) else {}
            if continuity:
                continuity_records.append({"parent_uid": parent_uid, **dict(continuity)})

        projected_rows = tuple(
            {
                "job_id": str(item.get("job_id") or ""),
                "projected_status": str(item.get("projected_status") or ""),
                "projection_reason": str(item.get("projection_reason") or ""),
                "canonical_state": str(item.get("canonical_state") or ""),
            }
            for item in jobs
        )
        return SchedulerSnapshot(
            schema_version="scheduler_snapshot_v1",
            snapshot_label=self.snapshot_label,
            scheduler_kind=self.scheduler_kind,
            adapter_kind=self.adapter_kind,
            captured_at_utc=_utc_now_iso(),
            scenario_key=scenario.key,
            jobs=tuple(jobs),
            receipts=tuple(receipts),
            compiled_graphs=tuple(compiled_graphs),
            continuity_records=tuple(continuity_records),
            closeout_records=(),
            surface_projections={
                "v2_shadow": projected_rows,
            },
            family_records=_collect_family_records_for_parents(self.repo_root, family_parent_uids),
            code_metrics=(measure_code_metrics(self.repo_root, self.code_paths) if self.code_paths else None),
            notes=(
                "captured directly from infra.controlplane.sched shadow runtime",
                "candidate snapshot is non-mutating and does not claim canonical scheduler ownership",
            ),
        )


class SyntheticStressSchedulerAdapter(SchedulerBenchmarkAdapter):
    adapter_kind = "synthetic_stress"

    def __init__(
        self,
        *,
        snapshot_label: str,
        scheduler_kind: str,
        job_count: int,
        topology: str = "chain",
        destructive_case: str = "none",
        owner_mode: str = "v2_shadow",
        code_paths: Sequence[str] = (),
        repo_root: Path | None = None,
    ) -> None:
        self.snapshot_label = str(snapshot_label or "synthetic_scheduler")
        self.scheduler_kind = str(scheduler_kind or "synthetic")
        self.job_count = max(1, int(job_count))
        self.topology = str(topology or "chain").strip().lower()
        self.destructive_case = str(destructive_case or "none").strip().lower()
        self.owner_mode = str(owner_mode or "v2_shadow").strip().lower()
        self.code_paths = tuple(str(item).strip() for item in code_paths if str(item).strip())
        self.repo_root = Path(repo_root or ".").resolve()

    def _depends_on(self, index: int) -> list[str]:
        if index == 0:
            return []
        if self.topology == "wide":
            return ["synth_0000"] if index > 0 else []
        if self.topology == "diamond":
            if index in {1, 2}:
                return ["synth_0000"]
            if index >= 3:
                return ["synth_0001", "synth_0002"]
        return [f"synth_{index - 1:04d}"]

    def _job_state(self, index: int) -> tuple[str, str, str, bool]:
        if self.destructive_case == "dependency_contradiction" and index == min(2, self.job_count - 1):
            return "blocked", "dependency_pending:synth_0001", "blocked", False
        if self.destructive_case == "kill_active" and index == 0:
            return "active", "active_run_present", "running", False
        if self.destructive_case == "kill_before_closeout" and index == self.job_count - 1:
            return "blocked", "closeout_waiting_for_restart_recovery", "blocked", False
        if self.destructive_case == "stale_claim" and index == 0:
            return "blocked", "stale_claim_blocked_until_policy_decision", "blocked", False
        if index < max(0, self.job_count - 1):
            return "completed_once", "disabled", "succeeded", False
        return "ready", "dependencies_satisfied", "", True

    def _continuity_record(self) -> Mapping[str, Any]:
        recovery_by_case = {
            "none": "none",
            "kill_active": "resume",
            "kill_before_closeout": "rerun",
            "stale_claim": "block",
            "projection_corruption": "block",
            "dependency_contradiction": "block",
        }
        return {
            "destructive_case": self.destructive_case,
            "continuity_state": "stress_case",
            "recovery_outcome": recovery_by_case.get(self.destructive_case, "block"),
            "invented_work": False,
            "canonical_truth_wins": True,
            "repair_receipts_required": self.destructive_case != "none",
            "resume_rerun_block_fail_only": True,
        }

    def capture(self, *, scenario: ScenarioSpec) -> SchedulerSnapshot:
        jobs: List[Mapping[str, Any]] = []
        receipts: List[Mapping[str, Any]] = []
        projections: List[Mapping[str, Any]] = []
        for index in range(self.job_count):
            job_id = f"synth_{index:04d}"
            projected_status, projection_reason, canonical_state, due_eligible = self._job_state(index)
            row = {
                "job_id": job_id,
                "display_title": f"Synthetic stress job {index:04d}",
                "job_kind": "build_verify_loop",
                "binding_uid": f"PIS-SYNTH-{index:04d}",
                "depends_on": self._depends_on(index),
                "projected_status": projected_status,
                "projection_reason": projection_reason,
                "canonical_state": canonical_state,
                "due_reason": projection_reason,
                "due_eligible": due_eligible,
                "active_run_id": f"stress_active_{index:04d}" if projected_status == "active" else "",
                "last_status": "ok" if canonical_state == "succeeded" else ("running" if projected_status == "active" else ""),
                "active_loop_stage": "builder" if projected_status == "active" else "",
                "last_recovered_reason": "",
                "last_nonlaunch_reason": projection_reason if projected_status == "blocked" else "",
                "last_receipt_path": "",
                "job_shape": "verification_loop_job",
                "job_shape_summary": {},
            }
            jobs.append(row)
            projections.append(
                {
                    "job_id": job_id,
                    "projected_status": projected_status,
                    "projection_reason": projection_reason,
                    "canonical_state": canonical_state,
                }
            )
            if canonical_state == "succeeded":
                receipts.append(
                    {
                        "job_id": job_id,
                        "run_id": f"stress_{index:04d}",
                        "status": "ok",
                        "terminal_status": "ok",
                        "recovery_reason": "",
                        "synthetic_closeout": False,
                    }
                )
        if self.destructive_case != "none":
            receipts.append(
                {
                    "job_id": "synth_recovery_probe",
                    "run_id": f"stress_{self.destructive_case}",
                    "status": "blocked" if self.destructive_case in {"stale_claim", "projection_corruption", "dependency_contradiction"} else "ok",
                    "terminal_status": "",
                    "recovery_reason": self._continuity_record()["recovery_outcome"],
                    "synthetic_closeout": False,
                }
            )
        surface_projections = {
            "watch": tuple(projections),
            "api": tuple(projections),
            "web": tuple(projections),
            "mobile": tuple(projections),
        }
        return SchedulerSnapshot(
            schema_version="scheduler_snapshot_v1",
            snapshot_label=self.snapshot_label,
            scheduler_kind=self.scheduler_kind,
            adapter_kind=self.adapter_kind,
            captured_at_utc=_utc_now_iso(),
            scenario_key=scenario.key,
            jobs=tuple(jobs),
            receipts=tuple(receipts),
            compiled_graphs=(
                {
                    "workflow_kind": "intent_family_v2",
                    "topology": self.topology,
                    "job_count": self.job_count,
                    "single_compiled_graph": True,
                },
            ),
            continuity_records=(self._continuity_record(),),
            closeout_records=(
                {
                    "single_owner": "intent_family_closeout",
                    "promotion_path": "closeout_only",
                    "stored_derived_metadata_canonical": False,
                },
            ),
            surface_projections=surface_projections,
            family_records=(
                {
                    "parent_uid": "PIS-SYNTH-PARENT",
                    "intent_validation_child_uid": "PIS-SYNTH-VALIDATION",
                    "required_final_child_uid": "PIS-SYNTH-CLOSEOUT",
                },
            ),
            code_metrics=(measure_code_metrics(self.repo_root, self.code_paths) if self.code_paths else None),
            notes=(
                f"ownership_mode={self.owner_mode}",
                "canonical_write_allowed=false" if self.owner_mode in {"legacy", "v2_shadow", "v2_canary"} else "canonical_write_allowed=true",
                "public_port_policy=no_new_public_port",
                "api_surface=existing_plane_schedule_api",
                "watch_surface=shared_projection_bundle",
            ),
        )


def parse_adapter_spec(
    raw: str,
    *,
    repo_root: Path,
    snapshot_label: str,
    scheduler_kind: str,
    job_id_prefixes: Sequence[str] = (),
    binding_uids: Sequence[str] = (),
    family_parent_uids: Sequence[str] = (),
    code_paths: Sequence[str] = (),
    receipt_limit: int = 3,
) -> SchedulerBenchmarkAdapter:
    value = str(raw or "").strip()
    if value == "current":
        return CurrentPlaneSchedulerAdapter(
            repo_root=repo_root,
            snapshot_label=snapshot_label,
            scheduler_kind=scheduler_kind,
            job_id_prefixes=job_id_prefixes,
            binding_uids=binding_uids,
            family_parent_uids=family_parent_uids,
            code_paths=code_paths,
            receipt_limit=receipt_limit,
        )
    if value == "v2" or value.startswith("v2:"):
        parent_uids = family_parent_uids
        if value.startswith("v2:"):
            parent_uids = tuple(part.strip() for part in value.split(":", 1)[1].split(",") if part.strip())
        return V2IntentFamilySchedulerAdapter(
            repo_root=repo_root,
            snapshot_label=snapshot_label,
            scheduler_kind=scheduler_kind,
            parent_uids=parent_uids,
            code_paths=code_paths,
        )
    if value.startswith("snapshot:"):
        return FilesystemSnapshotAdapter(Path(value.split(":", 1)[1]))
    if value.startswith("synthetic"):
        parts = value.split(":")
        options: Dict[str, str] = {}
        for part in parts[1:]:
            if "=" in part:
                key, option_value = part.split("=", 1)
                options[key.strip()] = option_value.strip()
        return SyntheticStressSchedulerAdapter(
            repo_root=repo_root,
            snapshot_label=snapshot_label,
            scheduler_kind=scheduler_kind,
            job_count=int(options.get("jobs") or options.get("job_count") or 25),
            topology=options.get("topology", "chain"),
            destructive_case=options.get("case", options.get("destructive", "none")),
            owner_mode=options.get("owner", "v2_shadow"),
            code_paths=code_paths,
        )
    raise ValueError("adapter spec must be `current`, `v2[:parent_uid,...]`, `synthetic[:jobs=N][:topology=chain][:case=none]`, or `snapshot:/abs/path/to/file.json`")
