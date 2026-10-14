from __future__ import annotations

import json
import types
from pathlib import Path

import pytest

from experiments_shared import manifest_runner as shared_manifest_runner
from experiments_shared import run_job as shared_run_job
from experiments_shared.experiment_records import (
    RunRecordError,
    build_initial_run_record,
    collect_checkpoint_truth,
    job_spec_hash,
    read_run_record,
    record_heartbeat,
    record_intervention,
    record_terminal,
    reconcile_run_record,
    run_record_path,
    summarize_run_record,
    write_run_record,
)
from experiments_shared.job_manifests.wrapper_exec import _find_wrapper_dir_for_job
from infra.controlplane import job_budget_cost_ledger_store



def test_run_record_state_transition_law_and_hash(tmp_path: Path) -> None:
    run_dir = tmp_path / "wrapper_run"
    run_dir.mkdir(parents=True)

    initial = build_initial_run_record(
        run_id="wrapper_run",
        job_id="job-001",
        job_spec_hash="sha256:jobspec",
        project_id="demo",
        created_at_utc="2026-04-05T04:16:30Z",
        launch={
            "entry_module": "experiments.projects.demo._runner.run_registry_case",
            "seed": 7,
        },
    )
    written = write_run_record(run_dir, initial)

    assert written["lifecycle"]["state"] == "launching"
    assert written["record_hash"].startswith("sha256:")

    running = record_heartbeat(run_dir, heartbeat_at_utc="2026-04-05T04:16:31Z", note="wrapper_started")
    assert running["lifecycle"]["state"] == "running"
    assert running["lifecycle"]["heartbeat_count"] == 1

    terminal = record_terminal(
        run_dir,
        status="complete",
        exit_code=0,
        ended_at_utc="2026-04-05T04:16:32Z",
        transition_reason="wrapper_terminal_update",
    )
    assert terminal["lifecycle"]["state"] == "succeeded"
    assert terminal["lifecycle"]["terminal"] is True

    illegal = dict(terminal)
    illegal["lifecycle"] = dict(terminal["lifecycle"])
    illegal["lifecycle"]["state"] = "running"
    illegal["lifecycle"]["terminal"] = False
    illegal["lifecycle"]["ended_at_utc"] = ""

    with pytest.raises(RunRecordError):
        write_run_record(run_dir, illegal, previous=terminal)



def test_collect_checkpoint_truth_and_reconcile_uncertain_interrupted(tmp_path: Path) -> None:
    run_dir = tmp_path / "branch_child"
    bundle_dir = run_dir / "checkpoint_bundles_v2" / "bundle_gen000010"
    bundle_dir.mkdir(parents=True)

    (run_dir / "run_record.json").write_text("{\n", encoding="utf-8")
    (run_dir / "run_record.partial.json").write_text(
        json.dumps(
            {
                "schema_version": "experiment_run_record_partial_write_v1",
                "target_file": "run_record.json",
                "run_id": "branch_child",
                "started_at_utc": "2026-04-05T04:16:30Z",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "job_id": "job-branch-001",
                "job_spec_hash": "sha256:branchspec",
                "status": "started",
                "start_time_utc": "2026-04-05T04:16:30Z",
                "end_time_utc": "",
                "exit_code": 0,
                "failure_code": "",
                "failure_detail": "",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "branch_meta.json").write_text(
        json.dumps(
            {
                "schema_version": "branch_meta_v1",
                "branch_id": "child-branch",
                "parent_branch_id": "parent-branch",
                "root_branch_id": "root-branch",
                "parent_checkpoint_id": "ckpt-10",
                "root_checkpoint_id": "ckpt-root",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "resume_from.json").write_text(
        json.dumps(
            {
                "schema_version": "branch_checkpoint_pointer_v1",
                "checkpoint_dir": "checkpoint_bundles_v2/bundle_gen000010",
                "checkpoint_generation": 10,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    (bundle_dir / "metadata.json").write_text(
        json.dumps(
            {
                "schema_version": "checkpoint_bundle_v2",
                "checkpoint_id": "ckpt-10",
                "generation_index": 10,
                "branch_id": "parent-branch",
                "campaign_id": "camp-001",
                "code_commit_hash": "abc123",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    checkpoint = collect_checkpoint_truth(run_dir)
    assert checkpoint["latest_checkpoint_id"] == "ckpt-10"
    assert checkpoint["latest_checkpoint_generation"] == 10
    assert checkpoint["resume_status"] == "branched_resume"
    assert checkpoint["parent_branch_id"] == "parent-branch"

    reconciled = reconcile_run_record(
        run_dir,
        observed_at_utc="2026-04-05T05:00:00Z",
        stale_heartbeat_after_sec=10,
        record_repair_event=True,
    )

    assert reconciled["lifecycle"]["state"] == "uncertain_interrupted"
    assert reconciled["lifecycle"]["confidence"] == "uncertain"
    assert reconciled["checkpoint"]["latest_checkpoint_id"] == "ckpt-10"
    assert reconciled["checkpoint"]["resume_status"] == "branched_resume"
    assert reconciled["reconciliation"]["status"] == "uncertain"
    assert "partial_write_marker_present" in reconciled["reconciliation"]["warnings"]
    assert reconciled["interventions"]["repair_count"] == 1
    assert (run_dir / "run_record.interventions.jsonl").exists()
    rows = [json.loads(line) for line in (run_dir / "run_record.interventions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    assert rows[-1]["action"] == "reconcile_run_truth"
    assert rows[-1]["repair"] is True



def test_record_intervention_updates_summary_surface(tmp_path: Path) -> None:
    run_dir = tmp_path / "managed_run"
    run_dir.mkdir(parents=True)
    write_run_record(
        run_dir,
        build_initial_run_record(
            run_id="managed_run",
            job_id="job-ops-001",
            job_spec_hash="sha256:ops",
            created_at_utc="2026-04-05T04:16:30Z",
        ),
    )

    event = record_intervention(
        run_dir,
        action="graceful_stop_requested",
        actor="operator",
        reason="operator asked wrapper to stop",
        details={"source": "test"},
        repair=False,
        recorded_at_utc="2026-04-05T04:16:40Z",
    )

    record = read_run_record(run_dir)
    summary = summarize_run_record(record)

    assert event["event_type"] == "operator_action"
    assert record["interventions"]["count"] == 1
    assert record["interventions"]["repair_count"] == 0
    assert record["interventions"]["last_action"] == "graceful_stop_requested"
    assert record["interventions"]["last_actor"] == "operator"
    assert summary["intervention_count"] == 1
    assert summary["repair_count"] == 0



def test_run_job_writes_canonical_run_record_and_summary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = tmp_path
    wrapper_root = repo_root / "wrapper_jobs"
    wrapper_root.mkdir(parents=True)

    module_name = "demo_wrapper_success"
    (repo_root / f"{module_name}.py").write_text(
        """
import json
import os
import time
from pathlib import Path


def main() -> int:
    payload = Path(os.environ[\"EXPERIMENT_RUNS_DIR\"])
    payload.mkdir(parents=True, exist_ok=True)
    (payload / \"report.json\").write_text(json.dumps({\"ok\": True}, sort_keys=True) + \"\\n\", encoding=\"utf-8\")
    time.sleep(0.12)
    return 0


if __name__ == \"__main__\":
    raise SystemExit(main())
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(repo_root))
    monkeypatch.setattr(shared_run_job, "_repo_root_from_this_file", lambda: str(repo_root))
    monkeypatch.setattr(shared_run_job, "RUN_RECORD_HEARTBEAT_INTERVAL_SEC", 0.05)
    monkeypatch.setattr(shared_run_job, "is_immutable_git_ref", lambda git_ref, repo_root: (True, ""))
    monkeypatch.setattr(
        shared_run_job,
        "resolve_git",
        lambda git_ref, repo_root: types.SimpleNamespace(
            resolved_commit="b" * 40,
            describe="baseline-458",
            dirty=False,
        ),
    )

    job_spec = {
        "schema_version": "v1",
        "job_id": "wrapper_job_success_001",
        "git_ref": "a" * 40,
        "entrypoint": {"kind": "python_module", "module": module_name},
        "cli_args": [],
        "seed": 11,
        "created_at_utc": "2026-04-05T04:16:30Z",
        "canonicalization": {
            "json": "sort_keys=True,separators=(',',':'),ensure_ascii=False",
            "hash": "sha256(utf8(canonical_json(job_spec)))",
        },
        "job_spec_hash": "",
    }
    job_spec["job_spec_hash"] = job_spec_hash(job_spec)
    spec_path = repo_root / "job_spec.json"
    spec_path.write_text(json.dumps(job_spec, sort_keys=True) + "\n", encoding="utf-8")

    rc = shared_run_job.run_one_job([
        "--job_spec",
        str(spec_path),
        "--wrapper_root",
        str(wrapper_root),
    ])

    assert rc == 0
    wrappers = [path for path in wrapper_root.iterdir() if path.is_dir()]
    assert len(wrappers) == 1
    wrapper_dir = wrappers[0]

    record = read_run_record(wrapper_dir)
    summary = summarize_run_record(wrapper_dir)
    manifest = json.loads((wrapper_dir / "artifact_manifest.json").read_text(encoding="utf-8"))

    assert record["job_id"] == "wrapper_job_success_001"
    assert record["job_spec_hash"] == job_spec["job_spec_hash"]
    assert record["launch"]["entry_module"] == module_name
    assert record["launch"]["resolved_commit"] == "b" * 40
    assert record["lifecycle"]["state"] == "succeeded"
    assert record["lifecycle"]["confidence"] == "durable"
    assert record["lifecycle"]["heartbeat_count"] >= 1
    assert summary["state"] == "succeeded"
    assert summary["reconciliation_status"] == "clean"
    assert any(row["path"] == "run_record.json" for row in manifest["files"])
    assert (wrapper_dir / "payload" / "report.json").exists()
    cost_events = job_budget_cost_ledger_store.read_cost_event_records("wrapper_job_success_001", repo_root=tmp_path)
    assert len(cost_events) == 3
    assert [row["cost_event_uid"] for row in cost_events] == [
        f"{wrapper_dir.name}:launch",
        f"{wrapper_dir.name}:running_started",
        f"{wrapper_dir.name}:terminal:succeeded",
    ]
    assert [row["cost_reason_code"] for row in cost_events] == ["reservation", "usage", "usage"]
    assert all(row["job_uid"] == "wrapper_job_success_001" for row in cost_events)
    assert all(row["unit"] == "lifecycle_events" for row in cost_events)
    assert all(row["direction"] == "debit" for row in cost_events)
    assert all(row["amount_minor_units"] == 1 for row in cost_events)
    assert all(str(row["recorded_at_utc"]).endswith("Z") for row in cost_events)


def test_lifecycle_cost_checkpoint_posting_is_idempotent_for_retry_replay(tmp_path: Path) -> None:
    run_record = {
        "run_id": "wj_20260405_retry_001",
        "job_id": "job-retry-001",
        "updated_at_utc": "2026-04-05T04:16:31Z",
        "lifecycle": {"last_transition_utc": "2026-04-05T04:16:31Z"},
    }

    first = shared_run_job._post_job_lifecycle_cost_checkpoint(
        repo_root=str(tmp_path),
        run_record=run_record,
        checkpoint_id="running_started",
        cost_reason_code="usage",
    )
    second = shared_run_job._post_job_lifecycle_cost_checkpoint(
        repo_root=str(tmp_path),
        run_record=run_record,
        checkpoint_id="running_started",
        cost_reason_code="usage",
    )

    assert first["deduped"] is False
    assert second["deduped"] is True
    assert job_budget_cost_ledger_store.read_cost_event_records("job-retry-001", repo_root=tmp_path) == [
        {
            "schema_version": "job_budget_cost_ledger_record_v1",
            "cost_event_uid": "wj_20260405_retry_001:running_started",
            "job_uid": "job-retry-001",
            "recorded_at_utc": "2026-04-05T04:16:31Z",
            "amount_minor_units": 1,
            "unit": "lifecycle_events",
            "direction": "debit",
            "cost_reason_code": "usage",
        }
    ]



def test_run_job_fails_closed_when_canonical_run_record_launch_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    repo_root = tmp_path
    wrapper_root = repo_root / "wrapper_jobs"
    wrapper_root.mkdir(parents=True)

    module_name = "demo_wrapper_launch_failure"
    (repo_root / f"{module_name}.py").write_text(
        """
def main() -> int:
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
""".strip()
        + "\n",
        encoding="utf-8",
    )

    monkeypatch.syspath_prepend(str(repo_root))
    monkeypatch.setattr(shared_run_job, "_repo_root_from_this_file", lambda: str(repo_root))
    monkeypatch.setattr(shared_run_job, "is_immutable_git_ref", lambda git_ref, repo_root: (True, ""))
    monkeypatch.setattr(
        shared_run_job,
        "resolve_git",
        lambda git_ref, repo_root: types.SimpleNamespace(
            resolved_commit="c" * 40,
            describe="baseline-458",
            dirty=False,
        ),
    )

    def _raise_record_launch(*args, **kwargs):
        raise RuntimeError("disk full")

    monkeypatch.setattr(shared_run_job, "record_launch", _raise_record_launch)

    job_spec = {
        "schema_version": "v1",
        "job_id": "wrapper_job_launch_fail_001",
        "git_ref": "a" * 40,
        "entrypoint": {"kind": "python_module", "module": module_name},
        "cli_args": [],
        "seed": 5,
        "created_at_utc": "2026-04-05T04:16:30Z",
        "canonicalization": {
            "json": "sort_keys=True,separators=(',',':'),ensure_ascii=False",
            "hash": "sha256(utf8(canonical_json(job_spec)))",
        },
        "job_spec_hash": "",
    }
    job_spec["job_spec_hash"] = job_spec_hash(job_spec)
    spec_path = repo_root / "job_spec.json"
    spec_path.write_text(json.dumps(job_spec, sort_keys=True) + "\n", encoding="utf-8")

    rc = shared_run_job.run_one_job([
        "--job_spec",
        str(spec_path),
        "--wrapper_root",
        str(wrapper_root),
    ])

    assert rc == 1
    wrappers = [path for path in wrapper_root.iterdir() if path.is_dir()]
    assert len(wrappers) == 1
    wrapper_dir = wrappers[0]
    run_meta = json.loads((wrapper_dir / "run_meta.json").read_text(encoding="utf-8"))
    assert run_meta["status"] == "failed"
    assert run_meta["failure_code"] == "RUN_EVIDENCE_WRITE_FAIL"
    assert "invalid_or_missing_run_record" in run_meta["failure_detail"]
    repaired_record = read_run_record(wrapper_dir)
    repaired_summary = summarize_run_record(repaired_record)
    assert repaired_summary["state"] == "failed"
    assert repaired_summary["failure_code"] == "RUN_EVIDENCE_WRITE_FAIL"


def test_manifest_runner_prefers_run_record_over_legacy_run_meta(tmp_path: Path) -> None:
    wrapper_root = tmp_path / "wrappers"
    wrapper_root.mkdir(parents=True)
    wrapper_dir = wrapper_root / "wj_20260405_041630_deadbeef_abc1234567_bbbbbbb"
    wrapper_dir.mkdir(parents=True)

    write_run_record(
        wrapper_dir,
        build_initial_run_record(
            run_id=wrapper_dir.name,
            job_id="job-prefers-run-record",
            job_spec_hash="sha256:jobhash",
            created_at_utc="2026-04-05T04:16:30Z",
        ),
    )
    record_terminal(
        wrapper_dir,
        status="failed",
        exit_code=7,
        failure_code="RUN_ASSERTION_FAILURE",
        failure_detail="sim failed deterministically",
        ended_at_utc="2026-04-05T04:17:00Z",
    )
    (wrapper_dir / "run_meta.json").write_text(
        json.dumps(
            {
                "schema_version": "v1",
                "job_id": "job-prefers-run-record",
                "job_spec_hash": "sha256:jobhash",
                "status": "complete",
                "exit_code": 0,
                "failure_code": "",
                "failure_detail": "",
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    resolved = shared_manifest_runner._job_result_status_from_run_record(
        wrapper_dir=str(wrapper_dir),
        wrapper_root=str(wrapper_root),
        rc=0,
    )

    assert resolved["run_record_rel"].endswith("run_record.json")
    assert resolved["status"] == "failed"
    assert resolved["exit_code"] == 7
    assert resolved["failure_code"] == "RUN_ASSERTION_FAILURE"


def test_find_wrapper_dir_for_job_falls_back_to_run_record(tmp_path: Path) -> None:
    wrapper_root = tmp_path / "wrappers"
    run_dir = wrapper_root / "wj_20260405_041630_deadbeef_abc1234567_bbbbbbb"
    run_dir.mkdir(parents=True)

    record = build_initial_run_record(
        run_id=run_dir.name,
        job_id="job-fallback-001",
        job_spec_hash="sha256:abcdef1234567890",
        created_at_utc="2026-04-05T04:16:30Z",
    )
    write_run_record(run_dir, record)

    found = _find_wrapper_dir_for_job(str(wrapper_root), "job-fallback-001", "sha256:abcdef1234567890"[:12])
    assert found == str(run_dir)
