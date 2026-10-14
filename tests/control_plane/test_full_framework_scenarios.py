from __future__ import annotations

import pytest

from infra.controlplane import full_framework_scenarios as scenarios


pytestmark = [pytest.mark.regression, pytest.mark.integration]


def test_full_framework_scenarios_snapshot_exposes_canonical_sequence() -> None:
    payload = scenarios.full_framework_scenarios_snapshot()

    assert payload["schema_version"] == "lab_assistant_full_framework_scenarios_v1"
    assert payload["uid"] == "PIS-20260312-235056Z-V4N8"
    assert payload["scenario_uids"] == [
        "PIS-20260313-105059Z-2B7D",
        "PIS-20260313-105100Z-9434",
        "PIS-20260313-105101Z-406F",
        "PIS-20260313-105102Z-04C8",
        "PIS-20260313-105104Z-1544",
    ]
    assert payload["scenarios"][0]["sequence"] == 1
    assert payload["scenarios"][-1]["sequence"] == 5


def test_full_framework_scenarios_validation_requires_start_success_and_coverage() -> None:
    result = scenarios.validate_full_framework_scenarios()
    assert result["status"] == "pass"
    assert result["errors"] == []

import json
from pathlib import Path

from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_task_register as task_register


def _init_recovery_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "assistant_state").mkdir(parents=True, exist_ok=True)
    (root / "runtime" / "state" / "lab_assistant" / "task_register").mkdir(parents=True, exist_ok=True)


def _seed_task_register(root: Path, *, write_generation: int, task_ids: list[str]) -> None:
    host.bootstrap_host_baseline(root)
    register = dict(task_register.load_or_init_task_register(root))
    register["write_generation"] = write_generation
    register["active_tasks"] = [
        {
            "task_id": task_id,
            "kind": "software",
            "title": f"Task {task_id}",
            "status": "queued",
            "owner": "assistant",
            "source": "operator",
            "next_expected_action": "execute",
            "updated_utc": host._utc_now_iso(),
        }
        for task_id in task_ids
    ]
    task_register._set_generation(register, writer="test.full_framework.seed")
    task_register._write_json_atomic(task_register._register_path(root), register)
    task_register._write_summary_cache(root, register)


def test_stabilization_regression_wave_snapshot_exposes_canonical_seams() -> None:
    payload = scenarios.stabilization_regression_wave_snapshot()

    assert payload["schema_version"] == "lab_assistant_stabilization_regression_wave_v1"
    assert payload["uid"] == "PIS-20260402-062000Z-L2A8"
    assert [row["seam_id"] for row in payload["seams"]] == [
        "intake_to_snapshot",
        "workflow_recovery",
        "scheduled_forensics",
        "operator_snapshot_language",
        "host_restore_recovery",
    ]
    assert payload["pytest_args"]


def test_stabilization_regression_wave_validation_requires_selectors() -> None:
    result = scenarios.validate_stabilization_regression_wave()
    assert result["status"] == "pass"
    assert result["errors"] == []


def test_stabilization_regression_wave_includes_host_restore_recovery_seam() -> None:
    payload = scenarios.stabilization_regression_wave_snapshot()
    seam = next(row for row in payload["seams"] if row["seam_id"] == "host_restore_recovery")

    assert seam["selectors"]
    assert all("restore" in selector or "recovery" in selector for selector in seam["selectors"])


def test_host_recovery_drill_round_trip_preserves_durable_truth(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    _init_recovery_repo(source_root)
    (source_root / "runtime" / "state" / "lab_assistant" / "assistant_state" / "identity.json").write_text(
        json.dumps({"assistant_id": "lab-assistant-v1"}),
        encoding="utf-8",
    )
    _seed_task_register(source_root, write_generation=5, task_ids=["task-a"])
    host.bootstrap_host_baseline(source_root)
    host.mark_clean_shutdown(source_root, reason="scenario drill")

    report = host.run_backup_restore_host_replacement_drill(source_root, note="scenario restore")

    assert report["status"] == "pass"
    assert report["mismatches"] == []
    assert report["target_boot"]["mode"] == "restored_host"


def test_release_channel_integrity_snapshot_exposes_required_receipt_fields() -> None:
    payload = scenarios.release_channel_integrity_snapshot()

    assert payload["schema_version"] == "lab_assistant_release_channel_integrity_v1"
    assert payload["uid"] == "PIS-20260402-063100Z-X4M1"
    assert [row["channel"] for row in payload["channels"]] == ["debug", "release"]
    assert all("validation_scope" in row["required_receipt_fields"] for row in payload["channels"])


def test_release_channel_integrity_validation_requires_lineage_and_scope() -> None:
    result = scenarios.validate_release_channel_integrity()

    assert result["status"] == "pass"
    assert result["errors"] == []
