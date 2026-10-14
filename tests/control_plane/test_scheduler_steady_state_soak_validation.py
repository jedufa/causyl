from __future__ import annotations

import json
from pathlib import Path

from tools.bin import scheduler_steady_state_soak_validation as soak


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_scheduler_steady_state_soak_validation_writes_required_artifacts(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"

    result = soak.run_validation(out_dir=out_dir)

    manifest_path = out_dir / "soak_scenario_manifest.json"
    summary_path = out_dir / "soak_metrics_summary.json"
    trace_path = out_dir / "soak_truth_trace.jsonl"

    assert manifest_path.exists()
    assert summary_path.exists()
    assert trace_path.exists()
    assert Path(result["trace_path"]) == trace_path.resolve()

    manifest = _load_json(manifest_path)
    summary = _load_json(summary_path)
    trace_rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]

    assert manifest["pis_uid"] == "PIS-20260413-061522Z-2F87"
    assert [row["scenario_id"] for row in manifest["scenarios"]] == list(soak.SCENARIO_IDS)
    assert manifest["negative_control_policy"]["observed"] == ["negative_control"]

    observed = summary["observed_metrics"]
    assert summary["pass"] is True
    assert observed["scenario_count"] == 3
    assert observed["transition_count"] >= 12
    assert observed["max_queue_depth"] >= 4
    assert observed["duplicate_launch_count"] == 0
    assert observed["hidden_active_incidents"] == 0
    assert observed["truth_divergence_incidents"] == 0
    assert observed["failed_negative_control_count"] == 0

    negative_control = next(row for row in trace_rows if row["event"] == "negative_control_detected")
    assert negative_control["details"]["detected"] is True
    assert negative_control["details"]["injected_expected_active_count"] != negative_control["details"]["observed_active_count"]


def test_scheduler_steady_state_soak_validation_covers_operator_mutation_and_dependency_resolved_launches(tmp_path: Path) -> None:
    out_dir = tmp_path / "artifacts" / "validation"
    soak.run_validation(out_dir=out_dir)

    trace_rows = [json.loads(line) for line in (out_dir / "soak_truth_trace.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    mutation_actions = [row["details"]["action"] for row in trace_rows if row["event"] == "queue_mutation"]
    launch_reasons = [
        item["launch_reason"]
        for row in trace_rows
        if row["event"] == "tick"
        for item in row.get("tick_result", {}).get("submitted", [])
    ]

    assert "add" in mutation_actions
    assert "edit" in mutation_actions
    assert "disable_delete" in mutation_actions
    assert "dependency_resolved" in launch_reasons


def test_scheduler_steady_state_soak_snapshot_uses_plane_jobs_payload_for_operator_view(tmp_path: Path, monkeypatch) -> None:
    calls: list[Path] = []
    original = soak._build_schedules_jobs_payload_inprocess

    def _wrapped(*, repo_root: Path | None = None):  # type: ignore[no-untyped-def]
        calls.append(Path(repo_root or ".").resolve())
        return original(repo_root=repo_root)

    monkeypatch.setattr(soak, "_build_schedules_jobs_payload_inprocess", _wrapped)

    out_dir = tmp_path / "artifacts" / "validation"
    soak.run_validation(out_dir=out_dir)

    assert calls
