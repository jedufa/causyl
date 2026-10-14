from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_restart_continuity_validation as continuity


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_scheduler_restart_continuity_validation_writes_required_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"

    result = continuity.run_validation(out_dir=out_dir)

    required = [
        out_dir / "restart_scenario_matrix.json",
        out_dir / "recovery_latency_summary.json",
        out_dir / "restart_negative_controls.json",
        out_dir / "restart_truth_trace.jsonl",
        out_dir / "restart_truth_snapshots.json",
    ]
    assert all(path.exists() for path in required)

    matrix = _load_json(out_dir / "restart_scenario_matrix.json")
    latency = _load_json(out_dir / "recovery_latency_summary.json")
    negative = _load_json(out_dir / "restart_negative_controls.json")
    traces = [json.loads(line) for line in (out_dir / "restart_truth_trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]

    assert [row["scenario_id"] for row in matrix["scenarios"]] == list(continuity.SCENARIO_IDS)
    assert latency["pass"] is True
    assert latency["duplicate_execution_count"] == 0
    assert latency["silent_abandonment_count"] == 0
    assert latency["hidden_active_incidents"] == 0
    assert negative["pass"] is True
    assert result["trace_count"] == len(traces)


def test_scheduler_restart_continuity_validation_preserves_operator_mutation_and_clears_stale_lease(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"
    continuity.run_validation(out_dir=out_dir)

    traces = [json.loads(line) for line in (out_dir / "restart_truth_trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    stale_lease = next(row for row in traces if row["scenario_id"] == "RESTART_ACTIVE_WITH_LEASE_V1")
    operator_mutation = next(row for row in traces if row["scenario_id"] == "RESTART_DURING_OPERATOR_MUTATION_V1")

    assert stale_lease["details"]["last_status"] == "blocked"
    assert stale_lease["details"]["last_nonlaunch_reason"] == "startup_audit_stale_claim_cleared"
    assert operator_mutation["details"]["run_now_requested_utc"]


def test_scheduler_restart_continuity_validation_exercises_real_watchdog_service_path(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    original = continuity.sched.start_watchdog_service

    def _wrapped(*, interval_s: int = 900, repo_root=None):  # type: ignore[no-untyped-def]
        calls.append(str(Path(repo_root or ".").resolve()))
        return original(interval_s=interval_s, repo_root=repo_root)

    monkeypatch.setattr(continuity.sched, "start_watchdog_service", _wrapped)

    out_dir = tmp_path / "artifacts" / "validation"
    continuity.run_validation(out_dir=out_dir)

    assert calls
