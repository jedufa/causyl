from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from infra.benchmarks.scheduler import load_scenarios, run_scheduler_benchmark
from infra.benchmarks.scheduler.adapters import FilesystemSnapshotAdapter, SyntheticStressSchedulerAdapter, V2IntentFamilySchedulerAdapter


REPO_ROOT = Path(__file__).resolve().parents[2]


def _write_snapshot(
    path: Path,
    *,
    label: str,
    job_status: str = "active",
    projection_reason: str = "active_run_present",
    due_reason: str = "dependency_pis_a7v1",
    receipt_status: str = "ok",
    recovery_reason: str = "durable_restart",
    line_count: int = 100,
) -> Path:
    payload = {
        "schema_version": "scheduler_snapshot_v1",
        "snapshot_label": label,
        "scheduler_kind": label,
        "adapter_kind": "snapshot_file",
        "captured_at_utc": "2026-04-09T20:00:00Z",
        "scenario_key": "one_workflow_shadow",
        "jobs": [
            {
                "job_id": "pis_a7v1_20260409_define-intent-family-v2-workflow",
                "display_title": "A7V1",
                "projected_status": job_status,
                "projection_reason": projection_reason,
                "canonical_state": "running",
                "due_reason": due_reason,
                "depends_on": [],
            }
        ],
        "receipts": [
            {
                "job_id": "pis_a7v1_20260409_define-intent-family-v2-workflow",
                "run_id": "sj_1",
                "status": receipt_status,
                "synthetic_closeout": False,
                "recovery_reason": recovery_reason,
            }
        ],
        "family_records": [
            {
                "parent_uid": "PIS-20260409-194437Z-310F",
                "intent_validation_child_uid": "PIS-20260409-195248Z-BCC7",
                "required_final_child_uid": "PIS-20260409-195248Z-4066",
            }
        ],
        "surface_projections": {
            "watch": [
                {
                    "job_id": "pis_a7v1_20260409_define-intent-family-v2-workflow",
                    "projected_status": job_status,
                    "projection_reason": projection_reason,
                    "canonical_state": "running",
                }
            ]
        },
        "code_metrics": {
            "file_count": 4,
            "nonempty_line_count": line_count,
            "branch_count": 10,
            "function_count": 12
        }
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def test_scheduler_benchmark_passes_for_matching_snapshots(tmp_path: Path) -> None:
    baseline = _write_snapshot(tmp_path / "baseline.json", label="legacy", line_count=100)
    candidate = _write_snapshot(tmp_path / "candidate.json", label="candidate", line_count=75)
    scenario = load_scenarios()["one_workflow_shadow"]

    result = run_scheduler_benchmark(
        baseline_adapter=FilesystemSnapshotAdapter(baseline),
        candidate_adapter=FilesystemSnapshotAdapter(candidate),
        scenario=scenario,
        report_dir=tmp_path / "report",
    )

    assert result.status == "pass"
    assert (tmp_path / "report" / "scheduler_benchmark_report.json").exists()
    assert (tmp_path / "report" / "scheduler_benchmark_report.md").exists()


def test_scheduler_benchmark_fails_when_operator_truth_drifts(tmp_path: Path) -> None:
    baseline = _write_snapshot(tmp_path / "baseline.json", label="legacy")
    candidate = _write_snapshot(tmp_path / "candidate.json", label="candidate", job_status="blocked", projection_reason="dependency_other")
    scenario = load_scenarios()["one_workflow_shadow"]

    result = run_scheduler_benchmark(
        baseline_adapter=FilesystemSnapshotAdapter(baseline),
        candidate_adapter=FilesystemSnapshotAdapter(candidate),
        scenario=scenario,
        report_dir=tmp_path / "report",
    )

    assert result.status == "fail"
    assert any(item.dimension == "operator_truth" and item.verdict == "fail" for item in result.dimensions)


def test_v2_shadow_adapter_projects_active_and_dependency_labels_like_v1() -> None:
    adapter = V2IntentFamilySchedulerAdapter(
        repo_root=REPO_ROOT,
        snapshot_label="scheduler_v2_shadow",
        scheduler_kind="candidate",
        parent_uids=("PIS-TEST",),
    )

    active_row = adapter._job_row(
        job={"job_id": "pis_active", "child_uid": "PIS-ACTIVE", "depends_on_uids": []},
        decision={"decision": "active", "reason": "active_run_present"},
        runtime_record={"runtime_state": "running_iteration", "active_run_id": "sj_active", "last_status": "running"},
        job_id_by_child_uid={},
    )
    blocked_row = adapter._job_row(
        job={
            "job_id": "pis_blocked",
            "child_uid": "PIS-BLOCKED",
            "depends_on_uids": ["PIS-ACTIVE"],
            "depends_on_job_ids": ["pis_active"],
        },
        decision={"decision": "blocked", "reason": "dependency_pending:pis_active"},
        runtime_record={"runtime_state": ""},
        job_id_by_child_uid={"PIS-ACTIVE": "pis_active"},
    )

    assert active_row["projected_status"] == "active"
    assert active_row["projection_reason"] == "active_run_present"
    assert active_row["canonical_state"] == "running_iteration"
    assert active_row["due_reason"] == "active_run"
    assert active_row["active_run_id"] == "sj_active"
    assert blocked_row["depends_on"] == ["pis_active"]
    assert blocked_row["projection_reason"] == "dependency_pending:pis_active"
    assert blocked_row["canonical_state"] == ""


def test_scheduler_benchmark_marks_missing_interfaces_pending(tmp_path: Path) -> None:
    baseline = _write_snapshot(tmp_path / "baseline.json", label="legacy")
    candidate = _write_snapshot(tmp_path / "candidate.json", label="candidate", line_count=75)
    payload = json.loads(candidate.read_text(encoding="utf-8"))
    payload["receipts"] = []
    candidate.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    scenario = load_scenarios()["one_workflow_shadow"]

    result = run_scheduler_benchmark(
        baseline_adapter=FilesystemSnapshotAdapter(baseline),
        candidate_adapter=FilesystemSnapshotAdapter(candidate),
        scenario=scenario,
        report_dir=tmp_path / "report",
    )

    assert result.status == "pending_interface"
    assert any(item.dimension == "terminal_retirement" and item.verdict == "pending_interface" for item in result.dimensions)


def test_scheduler_benchmark_terminal_retirement_uses_latest_receipt_first(tmp_path: Path) -> None:
    baseline = _write_snapshot(tmp_path / "baseline.json", label="legacy", receipt_status="ok")
    candidate = _write_snapshot(tmp_path / "candidate.json", label="candidate", receipt_status="ok", line_count=75)
    for path in (baseline, candidate):
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["receipts"].append(
            {
                "job_id": "pis_a7v1_20260409_define-intent-family-v2-workflow",
                "run_id": "sj_old",
                "status": "failed_verification",
                "synthetic_closeout": False,
                "recovery_reason": "",
            }
        )
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    scenario = load_scenarios()["one_workflow_shadow"]

    result = run_scheduler_benchmark(
        baseline_adapter=FilesystemSnapshotAdapter(baseline),
        candidate_adapter=FilesystemSnapshotAdapter(candidate),
        scenario=scenario,
        report_dir=tmp_path / "report",
    )

    assert result.status == "pass"
    assert any(item.dimension == "terminal_retirement" and item.verdict == "pass" for item in result.dimensions)


def test_scheduler_benchmark_cli_emits_json(tmp_path: Path) -> None:
    baseline = _write_snapshot(tmp_path / "baseline.json", label="legacy")
    candidate = _write_snapshot(tmp_path / "candidate.json", label="candidate", line_count=75)
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "infra.benchmarks.scheduler",
            "--scenario",
            "one_workflow_shadow",
            "--baseline",
            f"snapshot:{baseline}",
            "--candidate",
            f"snapshot:{candidate}",
            "--report-dir",
            str(tmp_path / "report"),
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["status"] == "pass"


def test_scheduler_benchmark_synthetic_production_stress_marks_missing_complexity_pending(tmp_path: Path) -> None:
    scenario = load_scenarios()["production_stress_shadow"]

    result = run_scheduler_benchmark(
        baseline_adapter=SyntheticStressSchedulerAdapter(
            snapshot_label="synthetic_legacy",
            scheduler_kind="legacy",
            job_count=25,
            topology="diamond",
            destructive_case="projection_corruption",
            code_paths=(),
            repo_root=REPO_ROOT,
        ),
        candidate_adapter=SyntheticStressSchedulerAdapter(
            snapshot_label="synthetic_v2_shadow",
            scheduler_kind="candidate",
            job_count=25,
            topology="diamond",
            destructive_case="projection_corruption",
            code_paths=(),
            repo_root=REPO_ROOT,
        ),
        scenario=scenario,
        report_dir=tmp_path / "stress-report",
    )

    assert result.status == "pending_interface"
    assert any(item.dimension == "complexity" and item.verdict == "pending_interface" for item in result.dimensions)


def test_scheduler_benchmark_cli_runs_synthetic_stress_with_complexity(tmp_path: Path) -> None:
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "infra.benchmarks.scheduler",
            "--scenario",
            "production_stress_shadow",
            "--baseline",
            "synthetic:jobs=9:topology=chain:case=kill_active",
            "--candidate",
            "synthetic:jobs=9:topology=chain:case=kill_active",
            "--baseline-code-paths",
            "infra/controlplane/scheduled_jobs.py,infra/controlplane/scheduled_jobs_parts",
            "--candidate-code-paths",
            "infra/controlplane/sched",
            "--report-dir",
            str(tmp_path / "stress-cli-report"),
            "--json",
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "pass"
    assert any(item["dimension"] == "destructive_conditions" and item["verdict"] == "pass" for item in payload["dimensions"])
