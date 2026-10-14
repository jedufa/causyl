#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from infra.webui import server_routes_and_main as routes


SCHEMA_VERSION = "unsupervised_autonomy_validation_v1"
SCENARIO_IDS = (
    "STALE_TOGGLE_CONTRADICTS_LIVE_TRUTH_V1",
    "DEGRADED_RUN_NOW_REQUIRES_STALE_ACK_V1",
    "STALE_OVERRIDE_FORBIDDEN_FOR_TRIGGER_V1",
    "EXPLICIT_STALE_OVERRIDE_CANCEL_AUDITED_V1",
    "STALE_OVERRIDE_CANCEL_REQUIRES_EVIDENCE_V1",
    "REPEATED_STALE_TRIGGER_STORM_V1",
    "HIDDEN_ACTIVE_DELETE_BLOCKED_V1",
    "FRESH_MUTATION_NEGATIVE_CONTROL_V1",
)


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


def _scenario_stale_toggle(repo_root: Path, cfg: routes.WebUIConfig, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "STALE_TOGGLE_CONTRADICTS_LIVE_TRUTH_V1"
    sched.save_job(_job_spec("stale_toggle_001", prompt="Reject stale toggle."), repo_root=repo_root)
    req = _DummyReq(
        cfg=cfg,
        path="/api/schedules/job/toggle",
        body={
            "job_id": "stale_toggle_001",
            "enabled": False,
            "observation_evidence": {
                "observation_status": "fresh",
                "view_age_s": 601,
                "scheduler_status": "completed_once",
            },
        },
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    payload = _read_payload(req)
    saved = sched.load_job("stale_toggle_001", repo_root=repo_root)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="toggle_response",
        details={"http_status": req._status, "error": payload.get("error"), "enabled_after": bool(saved.get("enabled"))},
    )
    return {
        "scenario_id": scenario_id,
        "pass": req._status == 409 and str(payload.get("error") or "") == "stale_action_contradicts_live_truth" and bool(saved.get("enabled")),
        "expected_classification": "stale_toggle_rejected",
        "observed_classification": "stale_toggle_rejected"
        if req._status == 409 and str(payload.get("error") or "") == "stale_action_contradicts_live_truth"
        else "stale_toggle_mutated_live_truth",
        "supports_unsupervised_operation": False,
    }


def _scenario_degraded_run_now(repo_root: Path, cfg: routes.WebUIConfig, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "DEGRADED_RUN_NOW_REQUIRES_STALE_ACK_V1"
    sched.save_job(_job_spec("degraded_run_now_001", prompt="Reject degraded run now."), repo_root=repo_root)
    with _patched_observation_payload(
        {
            "jobs": [{"job_id": "degraded_run_now_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable", "snapshot_stale"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        }
    ):
        req = _DummyReq(cfg=cfg, path="/api/schedules/run_now", body={"job_id": "degraded_run_now_001"})
        routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    payload = _read_payload(req)
    state = sched.load_state("degraded_run_now_001", repo_root=repo_root)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="run_now_response",
        details={"http_status": req._status, "error": payload.get("error"), "run_now_requested_utc": state.get("run_now_requested_utc")},
    )
    return {
        "scenario_id": scenario_id,
        "pass": req._status == 409 and str(payload.get("error") or "") == "stale_observation_requires_ack" and not str(state.get("run_now_requested_utc") or "").strip(),
        "expected_classification": "degraded_run_now_rejected",
        "observed_classification": "degraded_run_now_rejected"
        if req._status == 409 and not str(state.get("run_now_requested_utc") or "").strip()
        else "degraded_run_now_mutated_anyway",
        "supports_unsupervised_operation": False,
    }


def _scenario_explicit_stale_override(repo_root: Path, cfg: routes.WebUIConfig, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "EXPLICIT_STALE_OVERRIDE_CANCEL_AUDITED_V1"
    sched.save_job(_job_spec("stale_cancel_001", prompt="Allow explicit stale override."), repo_root=repo_root)
    with _patched_observation_payload(
        {
            "jobs": [{"job_id": "stale_cancel_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        }
    ):
        req = _DummyReq(
            cfg=cfg,
            path="/api/schedules/job/cancel",
            body={
                "job_id": "stale_cancel_001",
                "allow_stale_observation": True,
                "observation_evidence": {"observation_status": "degraded", "view_age_s": 900},
            },
        )
        routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    payload = _read_payload(req)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="cancel_response",
        details={"http_status": req._status, "status": payload.get("status"), "supports_unsupervised_operation": payload.get("mutation_guard", {}).get("supports_unsupervised_operation")},
    )
    return {
        "scenario_id": scenario_id,
        "pass": req._status == 200 and bool(payload.get("mutation_guard", {}).get("allowed")) and not bool(payload.get("mutation_guard", {}).get("supports_unsupervised_operation")),
        "expected_classification": "explicit_stale_override_is_supervised_only",
        "observed_classification": "explicit_stale_override_is_supervised_only"
        if req._status == 200 and not bool(payload.get("mutation_guard", {}).get("supports_unsupervised_operation"))
        else "stale_override_claimed_unsupervised_safe",
        "supports_unsupervised_operation": False,
    }


def _scenario_stale_override_forbidden_trigger(repo_root: Path, cfg: routes.WebUIConfig, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "STALE_OVERRIDE_FORBIDDEN_FOR_TRIGGER_V1"
    sched.save_job(_job_spec("stale_trigger_override_001", prompt="Trigger must not allow stale override."), repo_root=repo_root)
    with _patched_observation_payload(
        {
            "jobs": [{"job_id": "stale_trigger_override_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        }
    ):
        req = _DummyReq(
            cfg=cfg,
            path="/api/schedules/run_now",
            body={
                "job_id": "stale_trigger_override_001",
                "allow_stale_observation": True,
                "observation_evidence": {"observation_status": "degraded", "view_age_s": 900},
            },
        )
        routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    payload = _read_payload(req)
    state = sched.load_state("stale_trigger_override_001", repo_root=repo_root)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="run_now_override_response",
        details={"http_status": req._status, "error": payload.get("error"), "run_now_requested_utc": state.get("run_now_requested_utc")},
    )
    return {
        "scenario_id": scenario_id,
        "pass": req._status == 409 and str(payload.get("error") or "") == "stale_override_forbidden_for_action" and not str(state.get("run_now_requested_utc") or "").strip(),
        "expected_classification": "stale_override_forbidden_for_trigger",
        "observed_classification": "stale_override_forbidden_for_trigger"
        if req._status == 409 and not str(state.get("run_now_requested_utc") or "").strip()
        else "trigger_override_mutated_live_truth",
        "supports_unsupervised_operation": False,
    }


def _scenario_stale_override_requires_evidence(repo_root: Path, cfg: routes.WebUIConfig, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "STALE_OVERRIDE_CANCEL_REQUIRES_EVIDENCE_V1"
    sched.save_job(_job_spec("stale_cancel_missing_evidence_001", prompt="Cancel override requires evidence."), repo_root=repo_root)
    with _patched_observation_payload(
        {
            "jobs": [{"job_id": "stale_cancel_missing_evidence_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        }
    ):
        req = _DummyReq(
            cfg=cfg,
            path="/api/schedules/job/cancel",
            body={"job_id": "stale_cancel_missing_evidence_001", "allow_stale_observation": True},
        )
        routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    payload = _read_payload(req)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="cancel_override_missing_evidence_response",
        details={"http_status": req._status, "error": payload.get("error")},
    )
    return {
        "scenario_id": scenario_id,
        "pass": req._status == 409 and str(payload.get("error") or "") == "stale_override_missing_evidence",
        "expected_classification": "stale_override_requires_evidence",
        "observed_classification": "stale_override_requires_evidence"
        if req._status == 409 and str(payload.get("error") or "") == "stale_override_missing_evidence"
        else "stale_override_missing_evidence_was_allowed",
        "supports_unsupervised_operation": False,
    }


def _scenario_repeated_stale_trigger_storm(repo_root: Path, cfg: routes.WebUIConfig, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "REPEATED_STALE_TRIGGER_STORM_V1"
    sched.save_job(_job_spec("stale_trigger_storm_001", prompt="Repeated stale triggers must not queue work."), repo_root=repo_root)
    errors: List[str] = []
    statuses: List[int] = []
    with _patched_observation_payload(
        {
            "jobs": [{"job_id": "stale_trigger_storm_001", "scheduler_status": "queued"}],
            "observation_integrity": {
                "schema_version": "scheduled_jobs_observation_integrity_v1",
                "status": "degraded",
                "advisory_only": True,
                "reasons": ["jobs_unavailable", "snapshot_stale"],
                "summary": "Jobs view unavailable.",
                "last_error": "synthetic",
            },
        }
    ):
        for _ in range(5):
            req = _DummyReq(cfg=cfg, path="/api/schedules/run_now", body={"job_id": "stale_trigger_storm_001"})
            routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
            payload = _read_payload(req)
            statuses.append(int(req._status or 0))
            errors.append(str(payload.get("error") or ""))
    state = sched.load_state("stale_trigger_storm_001", repo_root=repo_root)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="repeated_stale_trigger_storm",
        details={"statuses": statuses, "errors": errors, "run_now_requested_utc": state.get("run_now_requested_utc")},
    )
    return {
        "scenario_id": scenario_id,
        "pass": statuses == [409, 409, 409, 409, 409] and all(err == "stale_observation_requires_ack" for err in errors) and not str(state.get("run_now_requested_utc") or "").strip(),
        "expected_classification": "repeated_stale_trigger_storm_blocked",
        "observed_classification": "repeated_stale_trigger_storm_blocked"
        if statuses == [409, 409, 409, 409, 409] and not str(state.get("run_now_requested_utc") or "").strip()
        else "repeated_stale_trigger_storm_leaked_mutation",
        "supports_unsupervised_operation": False,
    }


def _scenario_hidden_active_delete(repo_root: Path, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "HIDDEN_ACTIVE_DELETE_BLOCKED_V1"
    sched.save_job(_job_spec("hidden_delete_001", prompt="Delete must fail while hidden live claim exists."), repo_root=repo_root)
    sched._write_state("hidden_delete_001", {"active_run_id": "", "last_status": "running"}, repo_root=repo_root)
    sched._write_json_atomic(
        sched._claim_path("hidden_delete_001", repo_root),
        {
            "schema_version": "scheduled_job_claim_v1",
            "job_id": "hidden_delete_001",
            "run_id": "sj_hidden_delete_001",
            "trigger": "scheduler",
            "claimed_utc": sched._utc_iso(),
            "heartbeat_utc": sched._utc_iso(),
            "pid": 0,
        },
    )
    # pid=0 avoids host-specific liveness assumptions while heartbeat freshness keeps the claim live.
    try:
        sched.delete_job("hidden_delete_001", repo_root=repo_root)
        blocked = False
        message = ""
    except ValueError as exc:
        blocked = True
        message = str(exc)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="delete_attempt",
        details={"blocked": blocked, "message": message},
    )
    return {
        "scenario_id": scenario_id,
        "pass": blocked and "live runtime claim still exists" in message,
        "expected_classification": "hidden_active_delete_blocked",
        "observed_classification": "hidden_active_delete_blocked" if blocked else "hidden_active_delete_allowed",
        "supports_unsupervised_operation": False,
    }


def _scenario_fresh_negative_control(repo_root: Path, cfg: routes.WebUIConfig, traces: List[Dict[str, Any]]) -> Dict[str, Any]:
    scenario_id = "FRESH_MUTATION_NEGATIVE_CONTROL_V1"
    sched.save_job(_job_spec("fresh_toggle_001", prompt="Fresh toggle should remain usable."), repo_root=repo_root)
    req = _DummyReq(
        cfg=cfg,
        path="/api/schedules/job/toggle",
        body={
            "job_id": "fresh_toggle_001",
            "enabled": False,
            "observation_evidence": {"observation_status": "fresh", "view_age_s": 5, "scheduler_status": "queued"},
        },
    )
    routes.WebUIRequestHandler.do_POST(req)  # type: ignore[arg-type]
    payload = _read_payload(req)
    saved = sched.load_job("fresh_toggle_001", repo_root=repo_root)
    _record_trace(
        traces,
        scenario_id=scenario_id,
        event="fresh_toggle_response",
        details={"http_status": req._status, "enabled_after": bool(saved.get("enabled")), "guard_allowed": payload.get("mutation_guard", {}).get("allowed")},
    )
    return {
        "scenario_id": scenario_id,
        "pass": req._status == 200 and not bool(saved.get("enabled")) and bool(payload.get("mutation_guard", {}).get("allowed")),
        "expected_classification": "fresh_mutation_remains_usable",
        "observed_classification": "fresh_mutation_remains_usable"
        if req._status == 200 and not bool(saved.get("enabled"))
        else "fresh_mutation_overblocked",
        "supports_unsupervised_operation": True,
    }


def run_validation(*, out_dir: Path) -> Dict[str, Any]:
    out_dir = Path(out_dir).resolve()
    traces: List[Dict[str, Any]] = []
    td = tempfile.mkdtemp(prefix="unsupervised_autonomy_validation_")
    repo_root = Path(td)
    try:
        with _patched_plane_routes(repo_root) as cfg:
            rows = [
                _scenario_stale_toggle(repo_root, cfg, traces),
                _scenario_degraded_run_now(repo_root, cfg, traces),
                _scenario_stale_override_forbidden_trigger(repo_root, cfg, traces),
                _scenario_explicit_stale_override(repo_root, cfg, traces),
                _scenario_stale_override_requires_evidence(repo_root, cfg, traces),
                _scenario_repeated_stale_trigger_storm(repo_root, cfg, traces),
                _scenario_hidden_active_delete(repo_root, traces),
                _scenario_fresh_negative_control(repo_root, cfg, traces),
            ]
    finally:
        sched.stop_scheduler_service(timeout_s=0.5)
        sched.stop_watchdog_service(timeout_s=0.5)
        try:
            sched.stop_healer_service(timeout_s=0.5)
        except Exception:
            pass
        shutil.rmtree(repo_root, ignore_errors=True)

    matrix = {
        "schema_version": SCHEMA_VERSION,
        "scenario_count": len(rows),
        "pass_count": sum(1 for row in rows if bool(row.get("pass"))),
        "rows": rows,
    }
    negatives = {
        "schema_version": SCHEMA_VERSION,
        "rows": [
            row
            for row in rows
            if row["scenario_id"] in {
                "EXPLICIT_STALE_OVERRIDE_CANCEL_AUDITED_V1",
                "STALE_OVERRIDE_CANCEL_REQUIRES_EVIDENCE_V1",
                "STALE_OVERRIDE_FORBIDDEN_FOR_TRIGGER_V1",
                "FRESH_MUTATION_NEGATIVE_CONTROL_V1",
            }
        ],
        "pass": all(
            bool(row.get("pass"))
            for row in rows
            if row["scenario_id"] in {
                "EXPLICIT_STALE_OVERRIDE_CANCEL_AUDITED_V1",
                "STALE_OVERRIDE_CANCEL_REQUIRES_EVIDENCE_V1",
                "STALE_OVERRIDE_FORBIDDEN_FOR_TRIGGER_V1",
                "FRESH_MUTATION_NEGATIVE_CONTROL_V1",
            }
        ),
    }
    _write_json(out_dir / "unsupervised_autonomy_matrix.json", matrix)
    _write_jsonl(out_dir / "delayed_operator_action_trace.jsonl", traces)
    _write_json(out_dir / "autonomy_negative_controls.json", negatives)
    return {
        "schema_version": SCHEMA_VERSION,
        "scenario_ids": list(SCENARIO_IDS),
        "matrix": matrix,
        "trace_count": len(traces),
        "negative_controls": negatives,
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run delayed-operator and unsupervised autonomy abuse validation.")
    parser.add_argument("--out-dir", required=True, help="Directory for validation artifacts")
    args = parser.parse_args(argv)
    result = run_validation(out_dir=Path(args.out_dir))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
