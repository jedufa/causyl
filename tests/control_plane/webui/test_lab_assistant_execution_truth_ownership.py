from __future__ import annotations

from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_execution_reconciliation as recon
from infra.webui import server_lab_assistant_execution_truth_ownership as ownership
from infra.webui import server_lab_assistant_job_lifecycle as jobs
from infra.webui import server_lab_assistant_writeback as writeback


def test_ownership_snapshot_declares_canonical_domains() -> None:
    payload = ownership.ownership_snapshot()
    by_domain = {row["domain_id"]: row for row in payload["domains"]}

    assert payload["uid"] == "PIS-20260313-100601Z-M9R2"
    assert by_domain[ownership.DOMAIN_SCHEDULER_TRUTH]["canonical_owner"] == ownership.OWNER_EXECUTION_BACKEND
    assert by_domain[ownership.DOMAIN_RUNTIME_TRUTH]["canonical_owner"] == ownership.OWNER_EXECUTION_BACKEND
    assert by_domain[ownership.DOMAIN_TASK_WRITEBACK]["canonical_owner"] == ownership.OWNER_BD05_TASK_REGISTER
    assert by_domain[ownership.DOMAIN_OPERATOR_PROJECTION]["canonical_owner"] == ownership.OWNER_WEB_CONSOLE_PROJECTION


def test_reconciliation_treats_process_local_refs_as_non_durable() -> None:
    report = recon.build_task_reconciliation_report(
        Path("."),
        task_id="task_m9r2_refs",
        ledger_uid="PIS-20260313-100601Z-M9R2",
        task_status="active",
        refs=[{"surface": "workflow", "subsystem_id": "wf", "delegation_request_id": "req"}],
        syntheses=[
            {
                "request_id": "req",
                "status": "completed",
                "native_status": "completed",
                "evidence_status": "present",
                "evidence_refs": ["memory://worker/completed", "socket://runtime/wf", "runtime/evidence/wf.json"],
            }
        ],
        writer="y5z7.test",
    )
    assert report["projection"]["status"] == "completed"
    assert report["durable_refs"]["all_refs"] == ["runtime/evidence/wf.json"]


def test_reconciliation_write_rejects_projection_writer(tmp_path: Path) -> None:
    with pytest.raises(ownership.ExecutionTruthOwnershipError):
        recon.record_task_reconciliation_report(
            tmp_path,
            task_id="task_m9r2_recon_guard",
            ledger_uid="PIS-20260313-100601Z-M9R2",
            task_status="active",
            refs=[],
            syntheses=[],
            writer="web_console.snapshot",
        )


def test_writeback_rejects_projection_writer_even_before_task_lookup(tmp_path: Path) -> None:
    with pytest.raises(ownership.ExecutionTruthOwnershipError):
        writeback.reconcile_task(
            tmp_path,
            task_id="task_m9r2_writeback_guard",
            writer="web_console.writeback",
        )


def test_detached_job_runtime_truth_rejects_projection_writer(tmp_path: Path) -> None:
    with pytest.raises(ownership.ExecutionTruthOwnershipError):
        jobs.reconcile_jobs(
            tmp_path,
            writer="webui.reconcile",
            mode="steady_state",
            now_utc="2026-03-13T00:00:00Z",
        )


def test_task_run_intervention_rejects_projection_writer(tmp_path: Path) -> None:
    from infra.webui import server_lab_assistant_task_register as register

    (tmp_path / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    current = register.load_or_init_task_register(tmp_path)
    seeded = register.upsert_task(
        tmp_path,
        expected_generation=int(current["write_generation"]),
        writer="execution.guard",
        task={
            "task_id": "task_intervention_guard",
            "kind": "software",
            "title": "Guard intervention writer",
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "test guard",
        },
    )["task_register"]
    with_run = register.record_execution_surface_ownership(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="execution.guard",
        task_id="task_intervention_guard",
        selected_surface="pipeline",
        execution_id="run_intervention_guard_001",
        execution_ref="subsystem_pipeline:run_intervention_guard_001",
        binding_ref="binding_intervention_guard_001",
        job_uri=str(tmp_path / "runtime" / "state" / "pipeline" / "state.json"),
        trace_id="trace_intervention_guard_001",
    )["task_register"]

    with pytest.raises(ownership.ExecutionTruthOwnershipError):
        register.record_task_run_intervention(
            tmp_path,
            expected_generation=int(with_run["write_generation"]),
            writer="web_console.snapshot",
            task_id="task_intervention_guard",
            run_id="run_intervention_guard_001",
            action="pause",
            reason="projection must not mutate canonical intervention truth",
        )
