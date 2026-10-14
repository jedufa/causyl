from __future__ import annotations

from pathlib import Path

from infra.webui import server_lab_assistant_execution_reconciliation as recon


def test_reconciliation_preserves_conflicting_substrate_truth_as_ambiguous(tmp_path: Path) -> None:
    report = recon.build_task_reconciliation_report(
        tmp_path,
        task_id="task_y5z7",
        ledger_uid="PIS-20260313-030604Z-Y5Z7",
        task_status="active",
        refs=[
            {"surface": "workflow", "subsystem_id": "wf_1", "delegation_request_id": "req_wf", "status_refs": ["runtime/wf_1.json"]},
            {"surface": "scheduler", "subsystem_id": "job_1", "delegation_request_id": "req_sched", "status_refs": ["runtime/job_1.json"]},
        ],
        syntheses=[
            {"request_id": "req_wf", "status": "completed", "native_status": "completed", "evidence_status": "present", "evidence_refs": ["runtime/wf_1.json"]},
            {"request_id": "req_sched", "status": "failed", "native_status": "error", "evidence_status": "present", "evidence_refs": ["runtime/job_1.json"]},
        ],
        writer="y5z7.test",
    )
    projection = report["projection"]
    assert projection["status"] == "ambiguous_artifact"
    assert projection["ambiguous"] is True
    assert projection["conflict_count"] >= 1
    assert any(row["kind"] == "conflicting_terminal_truth" for row in report["conflicts"])


def test_reconciliation_record_writes_durable_report_receipt_and_latest_pointer(tmp_path: Path) -> None:
    report = recon.record_task_reconciliation_report(
        tmp_path,
        task_id="task_y5z7_receipt",
        ledger_uid="PIS-20260313-030604Z-Y5Z7",
        task_status="active",
        refs=[
            {
                "surface": "workflow",
                "subsystem_id": "wf_receipt",
                "delegation_request_id": "req_receipt",
                "status_refs": ["governance/agents/runtime/orchestration/workflow__wf_receipt.json"],
                "artifact_refs": ["governance/agents/runtime/orchestration/workflow__wf_receipt.artifacts/repo_residue/summary.json"],
            },
            {
                "surface": "scheduler",
                "subsystem_id": "sj_receipt",
                "delegation_request_id": "req_sched_receipt",
                "status_refs": ["governance/agents/runtime/scheduled_jobs/state/sj_receipt.json"],
                "artifact_refs": ["governance/agents/runtime/scheduled_jobs/receipts/sj_receipt/sj_1.json"],
            },
        ],
        syntheses=[
            {
                "request_id": "req_receipt",
                "status": "completed",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["governance/agents/runtime/orchestration/workflow__wf_receipt.artifacts/workflow_digest.json"],
            },
            {
                "request_id": "req_sched_receipt",
                "status": "completed",
                "native_status": "ok",
                "evidence_status": "present",
                "evidence_refs": ["governance/agents/runtime/scheduled_jobs/receipts/sj_receipt/sj_1.json"],
            },
        ],
        writer="y5z7.test",
    )
    paths = report["report_paths"]
    report_path = Path(paths["report_path"])
    receipt_path = Path(paths["receipt_path"])
    latest_path = Path(paths["latest_task_report_path"])
    assert report_path.exists()
    assert receipt_path.exists()
    assert latest_path.exists()
    assert report["projection"]["status"] == "completed"
    assert report["projection"]["residue_pointer_count"] >= 1
    assert report["durable_refs"]["receipt_refs"]


def test_reconciliation_blocks_clean_success_when_durable_pointers_are_missing(tmp_path: Path) -> None:
    report = recon.build_task_reconciliation_report(
        tmp_path,
        task_id="task_y5z7_missing",
        ledger_uid="PIS-20260313-030604Z-Y5Z7",
        task_status="active",
        refs=[{"surface": "workflow", "subsystem_id": "wf_missing", "delegation_request_id": "req_missing"}],
        syntheses=[{"request_id": "req_missing", "status": "completed", "native_status": "completed", "evidence_status": "present", "evidence_refs": []}],
        writer="y5z7.test",
    )
    assert report["projection"]["status"] == "ambiguous_artifact"
    assert "missing_durable_pointer" in report["projection"]["reason_codes"]

