#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.controlplane import sched as v2_sched
from infra.webui import server_routes_and_main as routes
from tools.bin import scheduler_watchdog as watchdog


SCHEMA_VERSION = "race_split_brain_validation_v1"
SCENARIO_IDS = (
    "RACE_POISONED_DEPENDENCY_ADMISSION_V1",
    "SPLIT_BRAIN_SHADOW_EVALUATION_BLOCKS_IMPOSSIBLE_RUNTIME_V1",
    "SPLIT_BRAIN_MULTIPLE_ACTIVE_HEADS_WATCHDOG_V1",
    "RACE_OPERATOR_TRIGGER_REJECTED_UNDER_DEGRADED_OBSERVATION_V1",
    "REPEATED_COLLISION_STORM_FAILS_CLOSED_V1",
    "HEALTHY_SINGLE_READY_NEGATIVE_CONTROL_V1",
)

BUGS_FOUND_AND_FIXED = [
    {
        "issue": "scheduler_v2_admitted_dependency_from_terminal_success_and_active_run_split_brain",
        "fixed_in": "infra/controlplane/scheduled_jobs_parts/part10__detect_pipeline_paused.py",
    },
    {
        "issue": "scheduler_v2_smoothed_multiple_active_children_into_ordinary_dependency_wait",
        "fixed_in": "infra/controlplane/scheduled_jobs_parts/part10__detect_pipeline_paused.py",
    },
    {
        "issue": "watchdog_v2_treated_multiple_active_heads_as_healthy",
        "fixed_in": "tools/bin/scheduler_watchdog.py",
    },
    {
        "issue": "sched_v2_shadow_evaluation_smoothed_impossible_runtime_truth_into_retired_or_active_rows",
        "fixed_in": "infra/controlplane/sched/runtime.py",
    },
]


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: List[Dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def _record_trace(traces: List[Dict[str, Any]], *, scenario_id: str, event: str, details: Dict[str, Any]) -> None:
    traces.append(
        {
            "utc": sched._utc_iso(),
            "scenario_id": scenario_id,
            "event": event,
            "details": details,
        }
    )


@contextlib.contextmanager
def _patched_attr(obj: Any, attr: str, value: Any) -> Iterator[None]:
    original = getattr(obj, attr)
    setattr(obj, attr, value)
    try:
        yield
    finally:
        setattr(obj, attr, original)


class _DummyReq:
    def __init__(self, *, cfg: routes.WebUIConfig, path: str, body: Dict[str, Any] | None = None) -> None:
        payload = json.dumps(body or {}).encode("utf-8")
        self.path = path
        self.headers = {"Content-Length": str(len(payload))}
        self.rfile = io.BytesIO(payload)
        self.wfile = io.BytesIO()
        self.server = SimpleNamespace(state=SimpleNamespace(cfg=cfg))
        self._status: int | None = None

    def send_response(self, status: int) -> None:
        self._status = int(status)

    def send_header(self, _k: str, _v: str) -> None:
        return

    def end_headers(self) -> None:
        return


def _read_payload(req: _DummyReq) -> Dict[str, Any]:
    raw = req.wfile.getvalue().decode("utf-8")
    start = raw.find("{")
    return json.loads(raw[start:] if start >= 0 else "{}")


def _write_workflow_contract(root: Path) -> None:
    payload = json.loads((REPO_ROOT / "governance" / "intent_family_v2_workflow_definition_contract_v1.json").read_text(encoding="utf-8"))
    contract_path = root / "governance" / "intent_family_v2_workflow_definition_contract_v1.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_family(root: Path) -> str:
    parent_uid = "PIS-20260410-030000Z-PA03"
    intent_uid = "IS-20260410-030001Z-IN03"
    impl_uid = "PIS-20260410-030002Z-IM03"
    validation_uid = "PIS-20260410-030003Z-VA03"
    closeout_uid = "PIS-20260410-030004Z-CL03"
    parent_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04" / f"{parent_uid}__parent"
    parent_dir.mkdir(parents=True, exist_ok=True)
    (parent_dir / "pis.md").write_text("# Parent\n", encoding="utf-8")
    (parent_dir / "meta.json").write_text(
        json.dumps(
            {
                "uid": parent_uid,
                "title": "Parent",
                "status": "draft",
                "integration_meta": {
                    "intent_sheet_uid": intent_uid,
                    "child_uids": [impl_uid, validation_uid, closeout_uid],
                    "intent_validation_child_uid": validation_uid,
                    "required_final_child_uid": closeout_uid,
                    "required_final_child_kind": "intent_family_closeout",
                },
            }
        ),
        encoding="utf-8",
    )
    (parent_dir / "events.jsonl").write_text("", encoding="utf-8")
    for uid, slug, title, relationship, rank, deps in [
        (impl_uid, "impl", "Implementation", "execution_child", 1, []),
        (validation_uid, "validation", "Validation", "intent_validation_suite", 2, [impl_uid]),
        (closeout_uid, "closeout", "Closeout", "intent_family_closeout", 3, [impl_uid, validation_uid]),
    ]:
        child_dir = root / "governance/ledger/patch_instruction_sheets/causyl/active/2026/04" / f"{uid}__{slug}"
        child_dir.mkdir(parents=True, exist_ok=True)
        (child_dir / "pis.md").write_text(f"# {title}\n", encoding="utf-8")
        (child_dir / "meta.json").write_text(
            json.dumps({"uid": uid, "title": title, "status": "draft", "integration_meta": {"relationship": relationship}}),
            encoding="utf-8",
        )
        (child_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "type": "decomposed_from_parent",
                    "payload": {
                        "parent_uid": parent_uid,
                        "relationship": relationship,
                        "critical_path_rank": rank,
                        "depends_on": deps,
                    },
                }
            )
            + "\n",
            encoding="utf-8",
        )
    prompt_path = root / "docs/prompts/lab_task_prompts.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("## 2. Implement One Child PIS\n\n```text\nImplement `<CHILD_UID>`.\n```\n", encoding="utf-8")
    return parent_uid


@contextlib.contextmanager
def _patched_plane_routes(repo_root: Path) -> Iterator[routes.WebUIConfig]:
    original_repo = routes._repo_root_from_webui
    original_sched_repo = routes._scheduled_jobs._repo_root
    original_core_repo = sched._repo_root
    original_boundary = routes._process_boundary_mutation
    cfg = routes.WebUIConfig(runs_root=repo_root, static_dir=repo_root)
    try:
        routes._repo_root_from_webui = lambda: repo_root  # type: ignore[assignment]
        routes._scheduled_jobs._repo_root = lambda: repo_root  # type: ignore[assignment]
        sched._repo_root = lambda: repo_root  # type: ignore[assignment]
        routes._process_boundary_mutation = lambda **_kwargs: {"status": "accepted", "command_id": "job.trigger"}  # type: ignore[assignment]
        yield cfg
    finally:
        routes._repo_root_from_webui = original_repo  # type: ignore[assignment]
        routes._scheduled_jobs._repo_root = original_sched_repo  # type: ignore[assignment]
        sched._repo_root = original_core_repo  # type: ignore[assignment]
        routes._process_boundary_mutation = original_boundary  # type: ignore[assignment]


@contextlib.contextmanager
def _patched_observation_payload(payload: Dict[str, Any]) -> Iterator[None]:
    original = routes._schedules_jobs_payload
    try:
        routes._schedules_jobs_payload = lambda repo_root=None, wait_timeout_s=8.0: dict(payload)  # type: ignore[assignment]
        yield
    finally:
        routes._schedules_jobs_payload = original  # type: ignore[assignment]


def _scenario_poisoned_dependency_admission(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "RACE_POISONED_DEPENDENCY_ADMISSION_V1"
    from types import SimpleNamespace
    from tools import runtime_policy as runtime_policy
    from infra.controlplane.sched import runtime as sched_v2_runtime

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        plan = SimpleNamespace(
            parent_uid="PIS-TEST-PARENT",
            jobs=[
                SimpleNamespace(job_id="job_a", child_uid="PIS-A", depends_on_uids=()),
                SimpleNamespace(job_id="job_b", child_uid="PIS-B", depends_on_uids=("PIS-A",)),
            ],
        )
        state_by_job = {
            "job_a": {"last_status": "ok", "active_run_id": "sj_poisoned_a"},
            "job_b": {"last_status": "", "active_run_id": ""},
        }
        submissions: list[str] = []

        class _Future:
            def done(self) -> bool:
                return False

        class _Pool:
            def submit(self, *args, **kwargs):
                submissions.append(str(args[1]))
                return _Future()

        with _patched_attr(runtime_policy, "get_runtime_policy_text", lambda key, default="", env_names=(): "PIS-TEST-PARENT" if key == "scheduler.v2.parent_uid" else default):
            with _patched_attr(sched_v2_runtime, "compile_scheduler_plan", lambda **kwargs: plan):
                with _patched_attr(sched, "load_state", lambda job_id, repo_root=None: dict(state_by_job[job_id])):
                    with _patched_attr(
                        sched,
                        "load_job",
                        lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True, "role": "builder", "policy": {"max_runtime_seconds": 600}},
                    ):
                        with _patched_attr(sched, "_get_dispatch_pool", lambda max_workers=1: _Pool()):
                            with _patched_attr(sched, "_dispatch_future_active", lambda job_id: False):
                                with _patched_attr(sched, "_get_active_job_count", lambda: 0):
                                    with sched._DISPATCH_GUARD:
                                        sched._DISPATCH_ACTIVE_JOBS.clear()
                                        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
                                        sched._DISPATCH_ACTIVE_JOB_META.clear()
                                    result = sched._scheduler_v2_tick(repo_root=root, now=sched._parse_utc("2026-03-08T04:00:00Z"))
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="scheduler_tick",
            details={"submitted": result["submitted"], "skipped": result["skipped"], "truth_ambiguous": result.get("truth_ambiguous") or []},
        )
        passed = (
            result["submitted_count"] == 0
            and submissions == []
            and {"job_id": "job_a", "reason": "truth_ambiguous:terminal_success_and_active_run"} in result["skipped"]
            and {"job_id": "job_b", "reason": "dependency_truth_ambiguous:PIS-A"} in result["skipped"]
        )
        snapshots[scenario_id] = {"result": result}
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "category": "race",
            "expected_classification": "poisoned_dependency_state_blocks_admission",
            "observed_classification": "poisoned_dependency_state_blocks_admission" if passed else "poisoned_dependency_state_admitted",
            "disqualifying_incident": "dependent_submitted_from_terminal_success_and_active_run_split_brain",
        }


def _scenario_multiple_active_heads_watchdog(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "SPLIT_BRAIN_MULTIPLE_ACTIVE_HEADS_WATCHDOG_V1"
    bundle = {
        "schema_version": "sched_v2_surface_bundle_v1",
        "status": "ready",
        "projection_equivalent": True,
        "parent_uid": "PIS-V2-PARENT",
        "ownership": {"mode": "v2_primary", "canonical_owner": "sched_v2"},
        "surfaces": {
            "watch": [
                {"job_id": "pis_active_1", "child_uid": "PIS-ACTIVE-1", "decision": "active", "reason": "active_run_present", "unmet_dependencies": []},
                {"job_id": "pis_active_2", "child_uid": "PIS-ACTIVE-2", "decision": "active", "reason": "active_run_present", "unmet_dependencies": []},
                {"job_id": "pis_blocked_tail", "child_uid": "PIS-BLOCKED", "decision": "blocked", "reason": "dependency_pending:PIS-ACTIVE-2", "unmet_dependencies": ["pis_active_2"]},
            ],
            "api": [],
            "web": [],
            "mobile": [],
        },
    }
    called: List[str] = []
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        with _patched_attr(watchdog.sched_v2_surfaces, "build_internal_surface_bundle", lambda **kwargs: bundle):
            with _patched_attr(sched, "request_run_now", lambda job_id, *, repo_root=None, request_source="": called.append(str(job_id)) or {"ok": True}):
                result = watchdog.run_watchdog(mode="selfheal", repo_root=root, scheduler_mode="v2", v2_parent_uid="PIS-V2-PARENT")
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="watchdog_run",
            details={"findings": result["findings"], "actions": result["actions"], "health": result["health"]},
        )
        passed = (
            not called
            and any(f["kind"] == "sched_v2_multiple_active_heads" for f in result["findings"])
            and result["health"]["reason"] == "sched_v2_multiple_active_heads"
            and result["truth_reconciliation"]["overall_state"] == "truth_ambiguous"
        )
        snapshots[scenario_id] = {"result": result}
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "category": "split_brain",
            "expected_classification": "multiple_active_heads_stay_ambiguous",
            "observed_classification": "multiple_active_heads_stay_ambiguous" if passed else "multiple_active_heads_smoothed_healthy",
            "disqualifying_incident": "watchdog_treated_multiple_active_heads_as_healthy",
        }


def _scenario_shadow_evaluation_blocks_impossible_runtime(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "SPLIT_BRAIN_SHADOW_EVALUATION_BLOCKS_IMPOSSIBLE_RUNTIME_V1"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        _write_workflow_contract(root)
        parent_uid = _write_family(root)
        plan = v2_sched.compile_scheduler_plan(parent_uid=parent_uid, run_at_utc="2026-04-10T00:00:00Z", repo_root=root)
        evaluation = v2_sched.evaluate_scheduler_plan(
            plan,
            runtime_records=[
                {
                    "child_uid": plan.jobs[0].child_uid,
                    "job_id": plan.jobs[0].job_id,
                    "runtime_state": "succeeded",
                    "active": True,
                    "active_run_id": "sj_poisoned",
                    "retired": False,
                },
                {
                    "child_uid": plan.jobs[1].child_uid,
                    "job_id": plan.jobs[1].job_id,
                    "runtime_state": "",
                    "active": False,
                    "active_run_id": "",
                    "retired": False,
                },
                {
                    "child_uid": plan.jobs[2].child_uid,
                    "job_id": plan.jobs[2].job_id,
                    "runtime_state": "",
                    "active": False,
                    "active_run_id": "",
                    "retired": False,
                },
            ],
        )
        rows = {row.child_uid: row for row in evaluation.admitted}
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="shadow_evaluation",
            details={
                "decisions": {uid: {"decision": row.decision, "reason": row.reason} for uid, row in rows.items()},
            },
        )
        passed = (
            rows[plan.jobs[0].child_uid].decision == "blocked"
            and rows[plan.jobs[0].child_uid].reason == "truth_ambiguous:terminal_success_and_active_run"
            and rows[plan.jobs[1].child_uid].decision == "blocked"
            and rows[plan.jobs[1].child_uid].reason == f"dependency_truth_ambiguous:{plan.jobs[0].child_uid}"
            and rows[plan.jobs[2].child_uid].decision == "blocked"
            and rows[plan.jobs[2].child_uid].reason == f"dependency_truth_ambiguous:{plan.jobs[0].child_uid}"
        )
        snapshots[scenario_id] = {
            "decisions": {uid: {"decision": row.decision, "reason": row.reason} for uid, row in rows.items()}
        }
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "category": "split_brain",
            "expected_classification": "shadow_evaluation_blocks_impossible_runtime_truth",
            "observed_classification": "shadow_evaluation_blocks_impossible_runtime_truth" if passed else "shadow_evaluation_smoothed_impossible_runtime_truth",
            "disqualifying_incident": "shadow_evaluation_translated_impossible_runtime_truth_into_plausible_surface_rows",
        }


def _job_spec(job_id: str, *, prompt: str) -> Dict[str, Any]:
    return {
        "job_id": job_id,
        "enabled": True,
        "role": "generalist",
        "binding": {"type": "task_id", "id": job_id},
        "prompt": prompt,
        "schedule": {"mode": "once", "run_at": "2026-04-13T11:30:00Z", "timezone": "UTC"},
        "policy": {"max_runtime_seconds": 900},
    }


def _scenario_operator_trigger_under_degraded_observation(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "RACE_OPERATOR_TRIGGER_REJECTED_UNDER_DEGRADED_OBSERVATION_V1"
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        sched.save_job(_job_spec("collision_trigger_001", prompt="Reject stale trigger under split brain."), repo_root=root)
        with _patched_plane_routes(root) as cfg:
            with _patched_observation_payload(
                {
                    "jobs": [{"job_id": "collision_trigger_001", "scheduler_status": "active"}],
                    "observation_integrity": {
                        "schema_version": "scheduled_jobs_observation_integrity_v1",
                        "status": "degraded",
                        "advisory_only": True,
                        "reasons": ["jobs_unavailable", "snapshot_stale", "split_brain_detected"],
                        "summary": "Jobs view is stale while runtime truth is ambiguous.",
                        "last_error": "synthetic_split_brain",
                    },
                }
            ):
                req = _DummyReq(
                    cfg=cfg,
                    path="/api/schedules/run_now",
                    body={
                        "job_id": "collision_trigger_001",
                        "allow_stale_observation": True,
                        "observation_evidence": {"observation_status": "degraded", "view_age_s": 901, "surface": "schedules"},
                    },
                )
                routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
        payload = _read_payload(req)
        state = sched.load_state("collision_trigger_001", repo_root=root)
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="operator_run_now",
            details={"http_status": req._status, "payload": payload, "run_now_requested_utc": state.get("run_now_requested_utc")},
        )
        passed = (
            req._status == 409
            and str(payload.get("error") or "") == "stale_override_forbidden_for_action"
            and not str(state.get("run_now_requested_utc") or "").strip()
        )
        snapshots[scenario_id] = {"payload": payload}
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "category": "race",
            "expected_classification": "stale_operator_trigger_blocked_under_collision",
            "observed_classification": "stale_operator_trigger_blocked_under_collision" if passed else "stale_operator_trigger_mutated_anyway",
            "disqualifying_incident": "operator_trigger_override_accepted_under_degraded_split_brain_observation",
        }


def _scenario_repeated_collision_storm(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "REPEATED_COLLISION_STORM_FAILS_CLOSED_V1"
    rows: List[Dict[str, Any]] = []
    for idx in range(5):
        rows.append(_scenario_poisoned_dependency_admission(traces, snapshots))
        rows.append(_scenario_multiple_active_heads_watchdog(traces, snapshots))
    pass_count = sum(1 for row in rows if bool(row.get("pass")))
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="storm_summary",
        details={"iterations": 5, "scenario_runs": len(rows), "pass_count": pass_count},
    )
    passed = pass_count == len(rows)
    snapshots[scenario_id] = {"iterations": 5, "rows": rows}
    return {
        "scenario_id": scenario_id,
        "pass": passed,
        "category": "race",
        "expected_classification": "collision_storm_remains_fail_closed",
        "observed_classification": "collision_storm_remains_fail_closed" if passed else "collision_storm_leaked_admission_or_recovery",
        "disqualifying_incident": "repeated_collision_pressure_eventually_admitted_or_smoothed_poisoned_truth",
        "iterations": 5,
    }


def _scenario_healthy_negative_control(traces: List[Dict[str, Any]], snapshots: Dict[str, Any]) -> Dict[str, Any]:
    scenario_id = "HEALTHY_SINGLE_READY_NEGATIVE_CONTROL_V1"
    from types import SimpleNamespace
    from tools import runtime_policy as runtime_policy
    from infra.controlplane.sched import runtime as sched_v2_runtime

    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        plan = SimpleNamespace(
            parent_uid="PIS-TEST-PARENT",
            jobs=[
                SimpleNamespace(job_id="job_a", child_uid="PIS-A", depends_on_uids=()),
                SimpleNamespace(job_id="job_b", child_uid="PIS-B", depends_on_uids=("PIS-A",)),
            ],
        )
        state_by_job = {
            "job_a": {"last_status": "ok", "active_run_id": ""},
            "job_b": {"last_status": "", "active_run_id": ""},
        }
        submissions: list[str] = []

        class _Future:
            def done(self) -> bool:
                return False

        class _Pool:
            def submit(self, *args, **kwargs):
                submissions.append(str(args[1]))
                return _Future()

        with _patched_attr(runtime_policy, "get_runtime_policy_text", lambda key, default="", env_names=(): "PIS-TEST-PARENT" if key == "scheduler.v2.parent_uid" else default):
            with _patched_attr(sched_v2_runtime, "compile_scheduler_plan", lambda **kwargs: plan):
                with _patched_attr(sched, "load_state", lambda job_id, repo_root=None: dict(state_by_job[job_id])):
                    with _patched_attr(
                        sched,
                        "load_job",
                        lambda job_id, repo_root=None: {"job_id": job_id, "enabled": True, "role": "builder", "policy": {"max_runtime_seconds": 600}},
                    ):
                        with _patched_attr(sched, "_get_dispatch_pool", lambda max_workers=1: _Pool()):
                            with _patched_attr(sched, "_dispatch_future_active", lambda job_id: False):
                                with _patched_attr(sched, "_get_active_job_count", lambda: 0):
                                    with sched._DISPATCH_GUARD:
                                        sched._DISPATCH_ACTIVE_JOBS.clear()
                                        sched._DISPATCH_ACTIVE_JOB_ROLES.clear()
                                        sched._DISPATCH_ACTIVE_JOB_META.clear()
                                    result = sched._scheduler_v2_tick(repo_root=root, now=sched._parse_utc("2026-03-08T04:00:00Z"))
        _record_trace(
            traces,
            scenario_id=scenario_id,
            event="healthy_tick",
            details={"submitted": result["submitted"], "skipped": result["skipped"]},
        )
        passed = (
            any(
                row.get("job_id") == "job_b" and row.get("child_uid") == "PIS-B"
                for row in list(result.get("submitted") or [])
                if isinstance(row, dict)
            )
            and submissions == ["job_b"]
        )
        snapshots[scenario_id] = {"result": result}
        return {
            "scenario_id": scenario_id,
            "pass": passed,
            "category": "negative_control",
            "expected_classification": "healthy_dependency_handoff_still_progresses",
            "observed_classification": "healthy_dependency_handoff_still_progresses" if passed else "healthy_dependency_handoff_overblocked",
            "disqualifying_incident": "hardening_caused_over_conservative_paralysis",
        }


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    traces: List[Dict[str, Any]] = []
    snapshots: Dict[str, Any] = {}
    rows = [
        _scenario_poisoned_dependency_admission(traces, snapshots),
        _scenario_shadow_evaluation_blocks_impossible_runtime(traces, snapshots),
        _scenario_multiple_active_heads_watchdog(traces, snapshots),
        _scenario_operator_trigger_under_degraded_observation(traces, snapshots),
        _scenario_repeated_collision_storm(traces, snapshots),
        _scenario_healthy_negative_control(traces, snapshots),
    ]
    race_rows = [row for row in rows if str(row.get("category") or "") in {"race", "negative_control"}]
    split_brain_rows = [row for row in rows if str(row.get("category") or "") == "split_brain"]
    negative_rows = [row for row in rows if str(row.get("category") or "") == "negative_control"]
    metrics = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "pass_count": sum(1 for row in rows if bool(row.get("pass"))),
        "negative_controls_passed": sum(1 for row in negative_rows if bool(row.get("pass"))),
        "repeated_collision_iterations": 5,
        "bugs_found_and_fixed": BUGS_FOUND_AND_FIXED,
    }
    race_matrix = {
        "schema_version": SCHEMA_VERSION,
        "rows": race_rows,
        "scenario_count": len(race_rows),
        "disqualifying_incidents": [row["disqualifying_incident"] for row in race_rows],
    }
    split_brain_matrix = {
        "schema_version": SCHEMA_VERSION,
        "rows": split_brain_rows,
        "scenario_count": len(split_brain_rows),
        "disqualifying_incidents": [row["disqualifying_incident"] for row in split_brain_rows],
    }
    _write_json(out_dir / "race_matrix.json", race_matrix)
    _write_json(out_dir / "split_brain_matrix.json", split_brain_matrix)
    _write_json(out_dir / "race_split_brain_metrics_summary.json", metrics)
    _write_json(out_dir / "race_split_brain_truth_snapshots.json", {"schema_version": SCHEMA_VERSION, "snapshots": snapshots})
    _write_jsonl(out_dir / "race_truth_trace.jsonl", traces)
    return {
        "race_matrix": race_matrix,
        "split_brain_matrix": split_brain_matrix,
        "metrics": metrics,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", type=Path, required=True)
    args = ap.parse_args()
    result = run_validation(out_dir=args.out_dir.resolve())
    print(json.dumps(result["metrics"], indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
