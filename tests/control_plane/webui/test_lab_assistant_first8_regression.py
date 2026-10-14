from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from infra.webui import server_lab_assistant_bootstrap as bootstrap
from infra.webui import server_lab_assistant_delegation_bindings as bindings
from infra.webui import server_lab_assistant_delegation_policy as policy
from infra.webui import server_lab_assistant_host as host
from infra.webui import server_lab_assistant_operator_snapshot as operator_snapshot
from infra.webui import server_lab_assistant_recovery as recovery
from infra.webui import server_lab_assistant_state as state
from infra.webui import server_lab_assistant_task_register as register


pytestmark = [pytest.mark.regression]


def _init_repo(root: Path) -> None:
    (root / "governance" / "ledger").mkdir(parents=True, exist_ok=True)
    (root / "governance" / "agents" / "runtime" / "orchestration").mkdir(parents=True, exist_ok=True)


def _write_json(path: Path, obj: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def _seed_task(root: Path, *, task_id: str, title: str, ledger_uid: str) -> dict:
    current = register.load_or_init_task_register(root)
    return register.upsert_task(
        root,
        expected_generation=int(current["write_generation"]),
        writer="first8.regression.seed",
        task={
            "task_id": task_id,
            "kind": "software",
            "title": title,
            "status": "active",
            "owner": "assistant",
            "source": "operator",
            "ledger_uid": ledger_uid,
            "next_expected_action": "preserve durable truth across restart",
        },
    )["task_register"]


def _workflow_request(*, task_id: str, ledger_uid: str, request_id: str, summary: str) -> dict:
    classification = policy.classify_answer_vs_delegate(
        {
            "summary": summary,
            "preferred_surface": "workflow",
            "estimated_duration_s": 120,
            "requires_new_evidence": True,
            "requires_external_action": True,
            "proof_heavy": True,
            "multi_step": True,
            "outlives_turn": True,
        }
    )
    request = policy.build_typed_delegation_request(
        classification=classification,
        surface="workflow",
        owner_ref={"task_id": task_id, "ledger_uid": ledger_uid},
        summary=summary,
        requested_action="launch bounded workflow",
        expected_evidence=[{"kind": "workflow_digest", "description": "durable workflow digest", "required": True}],
        next_decision={"kind": "review_evidence", "summary": "Review workflow result", "owner": "assistant"},
        input_payload={"scope": "first8_regression"},
    )
    request["request_id"] = request_id
    return request


def _restore_export(*, source: Path, target: Path, export_receipt: dict) -> None:
    manifest_path = Path(export_receipt["manifest_path"])
    payload_root = manifest_path.parent / "payload"
    shutil.copytree(payload_root, target)
    _write_json(
        target / "runtime" / "state" / "lab_assistant" / "host" / "restore_receipt.json",
        {
            "schema_version": "lab_assistant_host_restore_receipt_v1",
            "restored_utc": "2026-03-12T22:11:49Z",
            "source_manifest_path": str(manifest_path),
            "source_export_id": export_receipt["export_id"],
            "source_manifest_sha256": json.loads(manifest_path.read_text(encoding="utf-8"))["manifest_sha256"],
            "partial_restore": False,
            "missing_files": [],
            "errors": [],
            "restored_file_count": export_receipt["file_count"],
            "note": "first8 regression restore",
        },
    )
    _init_repo(target)


def test_first8_regression_restored_host_keeps_identity_bootstraps_and_reports_truthfully(tmp_path: Path) -> None:
    source = tmp_path / "source"
    target = tmp_path / "target"
    _init_repo(source)
    host.bootstrap_host_baseline(source)
    source_state = state.load_or_init_assistant_state(source)
    seeded = _seed_task(
        source,
        task_id="task_first8_restore",
        title="Restore first-8 durable contract",
        ledger_uid="PIS-20260312-220056Z-5D08",
    )
    request = _workflow_request(
        task_id="task_first8_restore",
        ledger_uid="PIS-20260312-220056Z-5D08",
        request_id="delegate_first8_restore",
        summary="Carry durable workflow truth across restore",
    )
    bound = bindings.bind_delegation_request(
        source,
        expected_generation=int(seeded["write_generation"]),
        writer="first8.regression.bind",
        request=request,
        launch_result={"workflow_id": "wf_first8_restore"},
    )
    ref = bound["delegation_ref"]
    _write_json(Path(ref["status_refs"][0]), {"workflow_id": "wf_first8_restore", "status": "completed"})
    _write_json(Path(ref["evidence_candidates"][0]), {"summary": "durable proof"})

    precedence = state.resolve_source_precedence(
        topic="assistant_identity",
        runtime_value=source_state["assistant_id"],
        chat_value="assistant_from_chat_should_lose",
    )
    pre_restore_bootstrap = bootstrap.build_fresh_turn_bootstrap(
        source,
        recent_chat_rows=[{"role": "user", "content": "chat transcript says a different assistant exists"}],
    )
    assert precedence["winner_source"] == "runtime_state"
    assert precedence["value"] == source_state["assistant_id"]
    assert precedence["conflict"] is True
    assert pre_restore_bootstrap["status"] == "ready"
    assert "supplemental only" in pre_restore_bootstrap["text"]

    export_receipt = host.export_host_baseline(source, note="first8 regression export")
    _restore_export(source=source, target=target, export_receipt=export_receipt)
    host.bootstrap_host_baseline(target)

    recovery_snapshot = recovery.reconcile_startup(target)
    target_state = state.read_assistant_state_snapshot(target, initialize_if_missing=False)["assistant_state"]
    target_register = register.read_task_register_snapshot(target, initialize_if_missing=False)["task_register"]
    target_bootstrap = bootstrap.build_fresh_turn_bootstrap(
        target,
        recent_chat_rows=[{"role": "assistant", "content": "chat still is not canonical truth"}],
        max_chars=12000,
    )
    rendered_bootstrap = bootstrap.render_fresh_turn_bootstrap_text(target, recent_chat_rows=[], max_chars=12000)
    target_snapshot = operator_snapshot.build_operator_snapshot(target)

    assert recovery_snapshot["status"] == "ready"
    assert recovery_snapshot["counts"]["completed"] == 1
    assert recovery_snapshot["host_health"]["mode"] == "restored_host"
    assert target_state["assistant_id"] == source_state["assistant_id"]
    assert target_register["assistant_id_ref"] == source_state["assistant_id"]
    assert target_register["completed_tasks"][0]["task_id"] == "task_first8_restore"
    assert target_register["completed_tasks"][0]["status"] == "completed"
    assert target_bootstrap["status"] == "ready"
    assert target_bootstrap["assistant_state"]["assistant_id"] == source_state["assistant_id"]
    assert "task_first8_restore" in target_bootstrap["text"]
    assert "Answer-vs-delegate policy:" in rendered_bootstrap
    assert target_snapshot["operator_state"] == "idle"
    assert target_snapshot["host_health"]["mode"] == "restored_host"
    assert len(target_snapshot["completed_tasks"]) == 1
    assert target_snapshot["completed_tasks"][0]["task_id"] == "task_first8_restore"
    assert target_snapshot["completed_tasks"][0]["evidence_backed_completion"] is True
    assert target_snapshot["delegated_work"][0]["status"] == "completed"


def test_first8_regression_duplicate_replay_and_restart_without_native_state_stays_truthful(tmp_path: Path) -> None:
    _init_repo(tmp_path)
    host.bootstrap_host_baseline(tmp_path)
    state.load_or_init_assistant_state(tmp_path)
    seeded = _seed_task(
        tmp_path,
        task_id="task_first8_restart",
        title="Truthful restart without native workflow state",
        ledger_uid="PIS-20260312-220056Z-5D08",
    )
    request = _workflow_request(
        task_id="task_first8_restart",
        ledger_uid="PIS-20260312-220056Z-5D08",
        request_id="delegate_first8_restart",
        summary="Track restart truth without duplicate workflow launch",
    )
    first = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(seeded["write_generation"]),
        writer="first8.regression.first_bind",
        request=request,
        launch_result={"workflow_id": "wf_first8_restart"},
    )
    second = bindings.bind_delegation_request(
        tmp_path,
        expected_generation=int(first["task_register"]["write_generation"]),
        writer="first8.regression.duplicate_bind",
        request=request,
        launch_result={"workflow_id": "wf_should_not_launch"},
    )

    host.bootstrap_host_baseline(tmp_path)
    recovery_snapshot = recovery.reconcile_startup(tmp_path)
    boot_packet = bootstrap.build_fresh_turn_bootstrap(tmp_path, recent_chat_rows=[], max_chars=12000)
    snapshot = operator_snapshot.build_operator_snapshot(tmp_path)
    task_register = register.read_task_register_snapshot(tmp_path, initialize_if_missing=False)["task_register"]
    journal_lines = (
        tmp_path / "runtime" / "state" / "lab_assistant" / "recovery" / "idempotency_journal.jsonl"
    ).read_text(encoding="utf-8").splitlines()

    assert second["duplicate_suppressed"] is True
    assert second["delegation_ref"]["subsystem_id"] == "wf_first8_restart"
    assert len(task_register["residue_tasks"][0]["delegation_refs"]) == 1
    assert len(journal_lines) == 2
    assert "duplicate_reattached" in journal_lines[1]
    assert recovery_snapshot["status"] == "interrupted_recovering"
    assert recovery_snapshot["continuity_proven"] is False
    assert "Workflow wf_first8_restart is missing its native state record." in recovery_snapshot["summary"]
    assert task_register["residue_tasks"][0]["status"] == "interrupted_recovering"
    assert boot_packet["status"] == "degraded"
    assert "state=interrupted_recovering" in boot_packet["text"]
    assert "reload_status: degraded" in boot_packet["text"]
    assert snapshot["operator_state"] == "interrupted_recovering"
    assert snapshot["active_tasks"][0]["status"] == "interrupted"
    assert snapshot["delegated_work"][0]["status"] == "interrupted"
