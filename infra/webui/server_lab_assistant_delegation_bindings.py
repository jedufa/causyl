"""DF0C delegation bindings, evidence capture, and next-decision synthesis."""

from __future__ import annotations

import datetime as _dt
import json
import os
from pathlib import Path
import time as _time
from typing import Any, Dict, List, Optional, Tuple

from infra.host import verification_contract as _verification_contract
from infra.webui import server_lab_assistant_correlation as _correlation
from infra.webui import server_lab_assistant_mailbox as _mailbox
from infra.webui import server_lab_assistant_delegation_policy as _policy
from infra.webui import server_lab_assistant_execution_reconciliation as _execution_reconciliation
from infra.webui import server_lab_assistant_execution_truth_ownership as _truth_ownership
from infra.webui import server_lab_assistant_job_lifecycle as _job_lifecycle
from infra.webui import server_lab_assistant_recovery as _recovery
from infra.webui import server_lab_assistant_task_register as _register


SNAPSHOT_SCHEMA_VERSION = "lab_assistant_delegation_snapshot_v1"
SNAPSHOT_INDEX_SCHEMA_VERSION = "lab_assistant_delegation_reverse_index_v1"
SYNTHESIS_TERMINAL_SUCCESS = {"completed", "passed", "ok", "success", "conditional_pass_repair_applied"}
SYNTHESIS_TERMINAL_FAILURE = {"failed", "error", "cancelled", "timed_out", "timeout"}
RUNNING_STATES = {"running", "queued", "starting", "idle", "open"}


class DelegationBindingsError(RuntimeError):
    """Raised when DF0C cannot truthfully bind or synthesize delegated work."""


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _snapshot_root(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "runtime" / "state" / "lab_assistant" / "delegation").resolve()


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _snapshot_path(repo_root: Path) -> Path:
    return (_snapshot_root(repo_root) / "delegation_snapshot.json").resolve()


def _reverse_index_path(repo_root: Path) -> Path:
    return (_snapshot_root(repo_root) / "delegation_reverse_index.json").resolve()


def _write_json_atomic(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    tmp = path.parent / f".{path.name}.tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(raw)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    if not path.exists() or not path.is_file() or path.is_symlink():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _normalize_path(repo_root: Path, raw: Any) -> str:
    text = _clean_text(raw)
    if not text:
        return ""
    path = Path(text)
    if not path.is_absolute():
        path = (Path(repo_root).resolve() / path).resolve()
    else:
        path = path.resolve()
    repo = Path(repo_root).resolve()
    if path != repo and repo not in path.parents:
        raise DelegationBindingsError(f"path escaped repo root: {text}")
    return str(path)


def _normalize_path_list(repo_root: Path, values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    out: List[str] = []
    for raw in values:
        path = _normalize_path(repo_root, raw)
        if path and path not in out:
            out.append(path)
    return out


def _task_for_owner(register: Dict[str, Any], owner_ref: Dict[str, Any]) -> Dict[str, Any]:
    task_id = _clean_text(owner_ref.get("task_id"))
    ledger_uid = _clean_text(owner_ref.get("ledger_uid"))
    open_loop_id = _clean_text(owner_ref.get("open_loop_id"))
    tasks = [
        dict(task)
        for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
        for task in list(register.get(bucket_name) or [])
        if isinstance(task, dict)
    ]
    if task_id:
        for task in tasks:
            if _clean_text(task.get("task_id")) == task_id:
                return dict(task)
    if ledger_uid:
        matches = [dict(task) for task in tasks if _clean_text(task.get("ledger_uid")) == ledger_uid]
        if len(matches) == 1:
            return matches[0]
    if open_loop_id:
        loops = list(register.get("open_loops") or [])
        for loop in loops:
            if _clean_text(loop.get("loop_id")) == open_loop_id:
                linked_task_id = _clean_text(loop.get("linked_task_id"))
                if linked_task_id:
                    for task in tasks:
                        if _clean_text(task.get("task_id")) == linked_task_id:
                            return dict(task)
    raise DelegationBindingsError("owner_ref did not resolve to a canonical task-register task")


def _workflow_paths(repo_root: Path, workflow_id: str) -> Tuple[str, str, str]:
    root = (Path(repo_root).resolve() / "governance" / "agents" / "runtime" / "orchestration").resolve()
    state_path = (root / f"workflow__{workflow_id}.json").resolve()
    events_path = (root / f"workflow__{workflow_id}.events.jsonl").resolve()
    artifacts_root = (root / f"workflow__{workflow_id}.artifacts").resolve()
    return str(state_path), str(events_path), str(artifacts_root)


def _canonical_execution_surface(surface: str) -> str:
    return _register._normalize_execution_surface(surface)


def _canonical_execution_reference_id(binding_ref: Dict[str, Any]) -> Tuple[str, str]:
    execution_ref = str((binding_ref or {}).get("subsystem_id") or "").strip()
    job_uri = ""
    status_refs = list((binding_ref or {}).get("status_refs") or [])
    if status_refs:
        job_uri = str(status_refs[0])
    return execution_ref, job_uri


def _build_binding_ref(repo_root: Path, request: Dict[str, Any], launch_result: Dict[str, Any]) -> Dict[str, Any]:
    surface = _clean_text(request.get("surface")).lower()
    request_id = _clean_text(request.get("request_id"))
    launch = dict(launch_result or {})
    status_refs: List[str] = []
    artifact_refs: List[str] = []
    evidence_candidates: List[str] = []
    subsystem_id = ""
    subsystem = surface
    native_status_hint = _clean_text(launch.get("status") or launch.get("state"))

    if surface == "workflow":
        subsystem_id = _clean_text(launch.get("workflow_id"))
        if not subsystem_id:
            raise DelegationBindingsError("workflow binding requires workflow_id")
        state_path, events_path, artifacts_root = _workflow_paths(repo_root, subsystem_id)
        status_refs = [state_path, events_path]
        artifact_refs = [artifacts_root]
        evidence_candidates = [str(Path(artifacts_root) / "workflow_digest.json")]
    elif surface == "verifier":
        normalized = _verification_contract.normalize_request(
            {
                "request_id": _clean_text(launch.get("request_id") or request_id),
                "workflow_id": _clean_text(launch.get("workflow_id") or "assistant"),
                "binding": request.get("owner_ref") or {},
                "action": {
                    "action_id": _clean_text(launch.get("action_id") or ((launch.get("action") or {}) if isinstance(launch.get("action"), dict) else {}).get("action_id")),
                    "params": dict(launch.get("params") or ((launch.get("action") or {}) if isinstance(launch.get("action"), dict) else {}).get("params") or {}),
                },
                "artifacts": {
                    "root_dir": _clean_text(((launch.get("artifacts") or {}) if isinstance(launch.get("artifacts"), dict) else {}).get("root_dir")),
                },
                "policy": {"timeout_s": int(launch.get("timeout_s") or 900)},
                "reason": _clean_text(launch.get("reason") or request.get("requested_action")),
            },
            repo=Path(repo_root).resolve(),
        )
        subsystem_id = _clean_text(normalized.get("request_id"))
        root_dir = _clean_text(((normalized.get("artifacts") or {}) if isinstance(normalized.get("artifacts"), dict) else {}).get("root_dir"))
        status_refs = [str(Path(root_dir) / "status.json")]
        artifact_refs = [root_dir]
        evidence_candidates = [str(Path(root_dir) / "result.json")]
    elif surface == "scheduler":
        from infra.controlplane import scheduled_jobs as _scheduled_jobs

        subsystem_id = _clean_text(launch.get("job_id"))
        if not subsystem_id:
            raise DelegationBindingsError("scheduler binding requires job_id")
        state_path = str(_scheduled_jobs.state_dir(Path(repo_root).resolve()) / f"{subsystem_id}.json")
        status_refs = [state_path]
        if _clean_text(launch.get("run_id")):
            receipt_path = str(_scheduled_jobs.receipts_dir(Path(repo_root).resolve()) / subsystem_id / f"{_clean_text(launch.get('run_id'))}.json")
            evidence_candidates = [receipt_path]
            artifact_refs = [str(_scheduled_jobs.reports_dir(Path(repo_root).resolve()) / subsystem_id / f"{_clean_text(launch.get('run_id'))}.md")]
        else:
            artifact_refs = [str(_scheduled_jobs.receipts_dir(Path(repo_root).resolve()) / subsystem_id)]
    elif surface == "execution_queue":
        subsystem_id = _clean_text(launch.get("run_id"))
        if not subsystem_id:
            raise DelegationBindingsError("execution_queue binding requires run_id")
        queue_root = (Path(repo_root).resolve() / "governance" / "agents" / "runtime" / "execution_queue").resolve()
        status_refs = [str(queue_root / "state.json"), str(queue_root / "events.jsonl")]
        artifact_refs = [str(queue_root / "artifacts")]
    elif surface == "mailbox":
        subsystem_id = _clean_text(launch.get("request_id") or request_id)
        if not subsystem_id:
            raise DelegationBindingsError("mailbox binding requires request_id")
        status_refs = [
            str(_mailbox.request_path(Path(repo_root).resolve(), subsystem_id)),
            str(_mailbox.snapshot_path(Path(repo_root).resolve())),
        ]
        artifact_refs = [str(_mailbox.mailbox_root(Path(repo_root).resolve()))]
    elif surface == "ledger":
        subsystem_id = _clean_text(launch.get("uid") or request.get("owner_ref", {}).get("ledger_uid"))
        if not subsystem_id:
            raise DelegationBindingsError("ledger binding requires uid")
    else:
        subsystem_id = _clean_text(launch.get("ref_id") or launch.get("operation_id") or request_id)
        status_refs = _normalize_path_list(repo_root, launch.get("status_refs"))
        artifact_refs = _normalize_path_list(repo_root, launch.get("artifact_refs"))
        evidence_candidates = _normalize_path_list(repo_root, launch.get("evidence_candidates"))

    extra_status_refs = _normalize_path_list(repo_root, launch.get("status_refs"))
    extra_artifact_refs = _normalize_path_list(repo_root, launch.get("artifact_refs"))
    extra_evidence = _normalize_path_list(repo_root, launch.get("evidence_candidates"))
    input_payload = request.get("input_payload") if isinstance(request.get("input_payload"), dict) else {}
    routing_decision = input_payload.get("routing_decision") if isinstance(input_payload.get("routing_decision"), dict) else {}
    for row in extra_status_refs:
        if row not in status_refs:
            status_refs.append(row)
    for row in extra_artifact_refs:
        if row not in artifact_refs:
            artifact_refs.append(row)
    for row in extra_evidence:
        if row not in evidence_candidates:
            evidence_candidates.append(row)

    now = _utc_now_iso()
    return {
        "schema_version": _register.DELEGATION_REF_SCHEMA_VERSION,
        "delegation_request_id": request_id,
        "surface": surface,
        "subsystem": subsystem,
        "subsystem_id": subsystem_id,
        "summary": _clean_text(request.get("summary")),
        "requested_action": _clean_text(request.get("requested_action")),
        "bound_utc": _clean_text(launch.get("bound_utc") or now),
        "updated_utc": _clean_text(launch.get("updated_utc") or now),
        "correlation_id": _clean_text(request.get("correlation_id")),
        "launch_turn_id": _clean_text(request.get("launch_turn_id")),
        "detached_job_id": _clean_text(launch.get("detached_job_id") or request.get("detached_job_id")),
        "result_correlation_basis": _clean_text(request.get("result_correlation_basis")),
        "launch_cause": _clean_text(request.get("launch_cause")),
        "parent_request_id": _clean_text(request.get("parent_request_id")),
        "prior_result_id": _clean_text(request.get("prior_result_id")),
        "reason_codes": list(request.get("reason_codes") or []),
        "mission_fit": dict(request.get("mission_fit") or {}),
        "expected_evidence": list(request.get("expected_evidence") or []),
        "evidence_candidates": evidence_candidates,
        "status_refs": status_refs,
        "artifact_refs": artifact_refs,
        "native_status_hint": native_status_hint,
        "owner_ref": dict(request.get("owner_ref") or {}),
        "next_decision": dict(request.get("next_decision") or {}),
        "routing_decision": dict(routing_decision),
    }


def bind_delegation_request(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    request: Dict[str, Any],
    launch_result: Dict[str, Any],
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    normalized_request = _policy.validate_typed_delegation_request(request)
    if _clean_text(normalized_request.get("surface")).lower() == "mailbox":
        mailbox_enqueued = _mailbox.enqueue_request(
            repo_root,
            request=normalized_request,
            writer=writer,
            max_delivery_attempts=int((launch_result or {}).get("max_delivery_attempts") or 3),
        )
        if bool(mailbox_enqueued.get("duplicate_suppressed")):
            existing = dict(mailbox_enqueued.get("request_record") or {})
            binding_ref = _recovery.attach_idempotency_keys(
                _build_binding_ref(
                    repo_root,
                    normalized_request,
                    {
                        "request_id": _clean_text(existing.get("request_id")),
                        "status_refs": [str(_mailbox.request_path(repo_root, _clean_text(existing.get("request_id"))))],
                        "artifact_refs": [str(_mailbox.mailbox_root(repo_root))],
                        "native_status": _clean_text(existing.get("state")),
                    },
                ),
                request=normalized_request,
            )
            _recovery.record_delegation_idempotency(
                repo_root,
                request=normalized_request,
                status="duplicate_reattached",
                writer=writer,
                delegation_ref=binding_ref,
                matched_keys=list(mailbox_enqueued.get("matched_keys") or []),
            )
            return {
                "ok": True,
                "duplicate_suppressed": True,
                "delegation_ref": binding_ref,
                "task_register": _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)["task_register"],
                "delegation_snapshot": rebuild_delegation_snapshot(repo_root, writer=writer),
            }
        launch_result = {
            **dict(launch_result or {}),
            "request_id": _clean_text((mailbox_enqueued.get("request_record") or {}).get("request_id")),
            "native_status": _clean_text((mailbox_enqueued.get("request_record") or {}).get("state")),
            "status_refs": [str(_mailbox.request_path(repo_root, _clean_text(normalized_request.get("request_id"))))],
            "artifact_refs": [str(_mailbox.mailbox_root(repo_root))],
        }
    duplicate = _recovery.find_duplicate_delegation(
        repo_root,
        request=normalized_request,
        ignore_request_id=_clean_text(normalized_request.get("request_id")),
    )
    if bool(duplicate.get("duplicate")):
        existing_ref = dict(duplicate.get("delegation_ref") or {})
        _recovery.record_delegation_idempotency(
            repo_root,
            request=normalized_request,
            status="duplicate_reattached",
            writer=writer,
            delegation_ref=existing_ref,
            matched_keys=list(duplicate.get("matched_keys") or []),
        )
        return {
            "ok": True,
            "duplicate_suppressed": True,
            "delegation_ref": existing_ref,
            "task_register": _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)["task_register"],
            "delegation_snapshot": rebuild_delegation_snapshot(repo_root, writer=writer),
        }
    snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=True)
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    task = _task_for_owner(register, normalized_request.get("owner_ref") or {})
    if int(register.get("write_generation") or 0) != int(expected_generation):
        raise DelegationBindingsError(
            f"stale write rejected before binding: expected_generation={int(expected_generation)} "
            f"current_generation={int(register.get('write_generation') or 0)}"
        )

    binding_ref = _recovery.attach_idempotency_keys(
        _build_binding_ref(repo_root, normalized_request, launch_result),
        request=normalized_request,
    )
    refs = [
        ref
        for ref in list(task.get("delegation_refs") or [])
        if _clean_text(ref.get("delegation_request_id")) != binding_ref["delegation_request_id"]
    ]
    refs.append(binding_ref)
    task["delegation_refs"] = refs
    task["updated_utc"] = _utc_now_iso()
    updated = _register.upsert_task(
        repo_root,
        expected_generation=int(expected_generation),
        writer=writer,
        task=task,
    )
    register_after = updated["task_register"]
    after_generation = int(register_after.get("write_generation") or 0)
    execution_ref_record = _register.record_execution_ref(
        repo_root,
        expected_generation=after_generation,
        writer=writer,
        execution_ref={
            "kind": binding_ref["subsystem"],
            "ref_id": binding_ref["subsystem_id"],
            "label": binding_ref["summary"] or binding_ref["requested_action"],
            "status": binding_ref["native_status_hint"] or "bound",
            "task_id": _clean_text(task.get("task_id")),
            "ledger_uid": _clean_text(task.get("ledger_uid")),
        },
    )
    execution_id = _clean_text(binding_ref.get("subsystem_id"))
    execution_ref, job_uri = _canonical_execution_reference_id(binding_ref)
    reference_status = _clean_text((launch_result or {}).get("reference_status"))
    reference_status_reason = _clean_text((launch_result or {}).get("reference_status_reason"))
    allow_conflict_overwrite = bool((launch_result or {}).get("allow_conflict_overwrite"))
    next_generation = int((execution_ref_record.get("task_register") or {}).get("write_generation") or after_generation)
    checkpoint = _checkpoint_for_binding_ref(
        binding_ref,
        writer=writer,
        reference_status=reference_status,
        reference_status_reason=reference_status_reason,
    )
    _register.record_execution_surface_ownership(
        repo_root,
        expected_generation=next_generation,
        writer=writer,
        task_id=_clean_text(task.get("task_id")),
        selected_surface=_canonical_execution_surface(_clean_text(binding_ref.get("surface"))),
        execution_id=execution_id,
        execution_ref=execution_ref,
        binding_ref=_clean_text(binding_ref.get("delegation_request_id")),
        job_uri=job_uri,
        trace_id=_clean_text(binding_ref.get("correlation_id")),
        checkpoint=checkpoint,
        reference_status=reference_status,
        reference_status_reason=reference_status_reason,
        allow_conflict_overwrite=allow_conflict_overwrite,
    )
    _correlation.record_delegation_binding(repo_root, delegation_ref=binding_ref, writer=f"{writer}.correlation")
    delegation_snapshot = rebuild_delegation_snapshot(repo_root, writer=writer)
    _recovery.record_delegation_idempotency(
        repo_root,
        request=normalized_request,
        status="bound",
        writer=writer,
        delegation_ref=binding_ref,
    )
    return {
        "ok": True,
        "delegation_ref": binding_ref,
        "task_register": _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)["task_register"],
        "delegation_snapshot": delegation_snapshot,
    }


def _owner_ref_from_binding(binding: Dict[str, Any]) -> Dict[str, str]:
    if not isinstance(binding, dict):
        raise DelegationBindingsError("binding must be an object to derive owner_ref")
    if any(binding.get(key) for key in ("task_id", "ledger_uid", "open_loop_id")):
        return {
            "task_id": _clean_text(binding.get("task_id")),
            "ledger_uid": _clean_text(binding.get("ledger_uid")),
            "open_loop_id": _clean_text(binding.get("open_loop_id")),
        }
    binding_type = _clean_text(binding.get("type")).lower()
    binding_id = _clean_text(binding.get("id"))
    if binding_type == "task_id":
        if not binding_id:
            raise DelegationBindingsError("binding.id is required for task_id owner_ref")
        return {"task_id": binding_id}
    if binding_type == "ledger_uid":
        if not binding_id:
            raise DelegationBindingsError("binding.id is required for ledger_uid owner_ref")
        return {"ledger_uid": binding_id}
    if binding_type == "open_loop_id":
        if not binding_id:
            raise DelegationBindingsError("binding.id is required for open_loop_id owner_ref")
        return {"open_loop_id": binding_id}
    raise DelegationBindingsError("binding must include task_id, ledger_uid, or open_loop_id")


def _default_expected_evidence(surface: str) -> List[Dict[str, Any]]:
    label = _clean_text(surface) or "execution_surface"
    return [
        {
            "kind": "execution_record",
            "description": f"{label} execution record",
            "required": True,
        }
    ]


def _default_next_decision(surface: str) -> Dict[str, str]:
    label = _clean_text(surface) or "execution surface"
    return {
        "kind": "assistant_continue",
        "summary": f"Wait for {label} to publish durable evidence.",
        "owner": "assistant",
    }


def _checkpoint_status_for_reference(reference_status: str) -> str:
    normalized = _clean_text(reference_status).lower()
    if normalized == "execution_ref_conflict":
        return "ambiguous"
    if normalized == "execution_ref_stale":
        return "stale"
    return "recorded"


def _checkpoint_for_binding_ref(
    binding_ref: Dict[str, Any],
    *,
    writer: str,
    reference_status: str,
    reference_status_reason: str = "",
) -> Optional[Dict[str, Any]]:
    if not isinstance(binding_ref, dict):
        return None
    surface = _clean_text(binding_ref.get("surface"))
    subsystem_id = _clean_text(binding_ref.get("subsystem_id"))
    detached_job_id = _clean_text(binding_ref.get("detached_job_id"))
    request_id = _clean_text(binding_ref.get("delegation_request_id"))
    checkpoint_id = ""
    if detached_job_id:
        checkpoint_id = f"job:{detached_job_id}"
    elif subsystem_id:
        checkpoint_id = f"{surface}:{subsystem_id}" if surface else subsystem_id
    elif request_id:
        checkpoint_id = f"{surface}:{request_id}" if surface else request_id
    evidence_refs = _truth_ownership.filter_durable_evidence_refs(
        list(binding_ref.get("status_refs") or [])
        + list(binding_ref.get("artifact_refs") or [])
        + list(binding_ref.get("evidence_candidates") or [])
    )
    if not checkpoint_id and not evidence_refs:
        return None
    return {
        "schema_version": _register.TASK_RUN_CHECKPOINT_SCHEMA_VERSION,
        "checkpoint_id": checkpoint_id or "binding_ref",
        "checkpoint_status": _checkpoint_status_for_reference(reference_status),
        "checkpoint_utc": _utc_now_iso(),
        "checkpoint_note": _clean_text(reference_status_reason) or "execution_surface_bound",
        "checkpoint_source": _clean_text(writer),
        "checkpoint_revision": 0,
        "progress_percent": None,
        "progress_summary": _clean_text(binding_ref.get("summary")),
        "evidence_refs": evidence_refs,
    }


def _launch_request_id(surface: str, launch_result: Dict[str, Any]) -> str:
    launch = dict(launch_result or {})
    for key in ("request_id", "workflow_id", "run_id", "job_id", "ref_id", "operation_id"):
        value = _clean_text(launch.get(key))
        if value:
            return value
    return _clean_text(surface)


def _record_failed_binding(
    repo_root: Path,
    *,
    owner_ref: Dict[str, Any],
    surface: str,
    request_id: str,
    correlation_id: str,
    launch_result: Dict[str, Any],
    writer: str,
    reason: str,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    try:
        snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
        register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
        task = _task_for_owner(register, owner_ref)
        expected_generation = int(register.get("write_generation") or 0)
        execution_id = _clean_text(
            (launch_result or {}).get("run_id")
            or (launch_result or {}).get("job_id")
            or (launch_result or {}).get("request_id")
            or request_id
        )
        execution_ref = execution_id or _clean_text(request_id or surface)
        binding_ref = _clean_text(request_id or execution_ref)
        job_uri = ""
        status_refs = list((launch_result or {}).get("status_refs") or [])
        if status_refs:
            try:
                job_uri = _normalize_path(repo_root, status_refs[0])
            except Exception:
                job_uri = _clean_text(status_refs[0])
        trace_id = _clean_text(correlation_id or (launch_result or {}).get("correlation_id"))
        record = _register.record_execution_surface_ownership(
            repo_root,
            expected_generation=expected_generation,
            writer=writer,
            task_id=_clean_text(task.get("task_id")),
            selected_surface=_canonical_execution_surface(surface),
            execution_id=_clean_text(execution_id),
            execution_ref=_clean_text(execution_ref),
            binding_ref=_clean_text(binding_ref),
            job_uri=_clean_text(job_uri),
            trace_id=trace_id,
            reference_status="execution_ref_conflict",
            reference_status_reason=_clean_text(reason) or "binding_failed",
            allow_conflict_overwrite=False,
        )
        return {"ok": True, "task_id": _clean_text(task.get("task_id")), "task_register": dict(record.get("task_register") or {})}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def bind_surface_launch(
    repo_root: Path,
    *,
    surface: str,
    binding: Dict[str, Any],
    summary: str,
    requested_action: str,
    launch_result: Optional[Dict[str, Any]] = None,
    input_payload: Optional[Dict[str, Any]] = None,
    expected_evidence: Optional[List[Dict[str, Any]]] = None,
    next_decision: Optional[Dict[str, Any]] = None,
    request_id: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    correlation_id: Optional[str] = None,
    launch_turn_id: Optional[str] = None,
    launch_cause: str = "assistant_turn",
    writer: str = "delegation_bindings.surface_launch",
    max_retries: int = 6,
    stale_retry_backoff_s: float = 0.05,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    try:
        owner_ref = _owner_ref_from_binding(binding)
    except Exception as exc:
        return {"ok": False, "error": str(exc), "surface": _clean_text(surface), "reason": "invalid_binding"}
    launch = dict(launch_result or {})
    chosen_request_id = _clean_text(request_id) or _launch_request_id(surface, launch)
    if not chosen_request_id:
        chosen_request_id = None
    reason_codes = ["execution_surface_launch"]
    surface_code = _clean_text(surface)
    if surface_code:
        reason_codes.append(f"surface_{surface_code}")
    try:
        request = _policy.build_typed_delegation_request(
            classification={
                "classification": "delegate",
                "reason_codes": reason_codes,
                "mission_fit": {
                    "status": "mission_aligned",
                    "summary": "Execution-surface launch follows the laboratory mission boundary.",
                    "source": "bind_surface_launch_default",
                    "requires_note": False,
                },
            },
            surface=_clean_text(surface),
            owner_ref=owner_ref,
            summary=_clean_text(summary),
            requested_action=_clean_text(requested_action),
            expected_evidence=expected_evidence if expected_evidence is not None else _default_expected_evidence(surface),
            next_decision=next_decision if next_decision is not None else _default_next_decision(surface),
            input_payload=dict(input_payload or {}),
            request_id=chosen_request_id,
            idempotency_key=_clean_text(idempotency_key or chosen_request_id),
            correlation_id=_clean_text(correlation_id or launch.get("correlation_id")),
            launch_turn_id=_clean_text(launch_turn_id),
            detached_job_id=_clean_text(launch.get("detached_job_id")),
            launch_cause=_clean_text(launch_cause) or "assistant_turn",
        )
    except Exception as exc:
        failure = {
            "ok": False,
            "error": str(exc),
            "surface": _clean_text(surface),
            "reason": "request_build_failed",
        }
        failure["truth_record"] = _record_failed_binding(
            repo_root,
            owner_ref=owner_ref,
            surface=_clean_text(surface),
            request_id=_clean_text(chosen_request_id),
            correlation_id=_clean_text(correlation_id or launch.get("correlation_id")),
            launch_result=launch,
            writer=f"{writer}.failed_binding",
            reason="request_build_failed",
        )
        return failure
    attempts: List[Dict[str, Any]] = []
    max_attempts = max(1, int(max_retries) + 1)
    provisional_ref = _recovery.attach_idempotency_keys(
        _build_binding_ref(repo_root, request, launch),
        request=request,
    )
    _recovery.record_delegation_idempotency(
        repo_root,
        request=request,
        status="launch_observed",
        writer=f"{writer}.launch_observed",
        delegation_ref=provisional_ref,
    )
    for attempt in range(max_attempts):
        snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=True)
        register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
        expected_generation = int(register.get("write_generation") or 0)
        try:
            result = bind_delegation_request(
                repo_root,
                expected_generation=expected_generation,
                writer=writer,
                request=request,
                launch_result=launch,
            )
            return {
                "ok": True,
                "surface": _clean_text(surface),
                "request_id": _clean_text(request.get("request_id")),
                "duplicate_suppressed": bool(result.get("duplicate_suppressed")),
                "delegation_ref": dict(result.get("delegation_ref") or {}),
                "attempts": attempts,
            }
        except Exception as exc:
            err_text = str(exc)
            attempts.append({"attempt": attempt + 1, "error": err_text})
            if "stale write rejected" in err_text.lower() and attempt < max_attempts - 1:
                if float(stale_retry_backoff_s) > 0:
                    _time.sleep(float(stale_retry_backoff_s))
                continue
            return {
                "ok": False,
                "surface": _clean_text(surface),
                "request_id": _clean_text(request.get("request_id")),
                "error": err_text,
                "attempts": attempts,
                "truth_record": _record_failed_binding(
                    repo_root,
                    owner_ref=owner_ref,
                    surface=_clean_text(surface),
                    request_id=_clean_text(request.get("request_id")),
                    correlation_id=_clean_text(correlation_id or launch.get("correlation_id")),
                    launch_result=launch,
                    writer=f"{writer}.failed_binding",
                    reason=err_text or "binding_failed",
                ),
            }
    return {
        "ok": False,
        "surface": _clean_text(surface),
        "request_id": _clean_text(request.get("request_id")),
        "error": "binding_failed_after_retries",
        "attempts": attempts,
        "truth_record": _record_failed_binding(
            repo_root,
            owner_ref=owner_ref,
            surface=_clean_text(surface),
            request_id=_clean_text(request.get("request_id")),
            correlation_id=_clean_text(correlation_id or launch.get("correlation_id")),
            launch_result=launch,
            writer=f"{writer}.failed_binding",
            reason="binding_failed_after_retries",
        ),
    }


def _existing_candidate_paths(ref: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for raw in list(ref.get("evidence_candidates") or []):
        path = Path(str(raw))
        if path.exists() and path.is_file():
            resolved = str(path.resolve())
            if resolved not in out:
                out.append(resolved)
    return out


def _native_state_from_json_paths(paths: List[str]) -> Dict[str, Any]:
    for raw in paths:
        obj = _read_json(Path(raw))
        if obj:
            return obj
    return {}


def _running_decision(ref: Dict[str, Any]) -> Dict[str, str]:
    return {
        "kind": "assistant_continue",
        "summary": f"Wait for {ref.get('surface')} {ref.get('subsystem_id')} to produce durable evidence.",
        "owner": "assistant",
    }


def _evidence_gap_decision(ref: Dict[str, Any]) -> Dict[str, str]:
    return {
        "kind": "assistant_continue",
        "summary": f"Collect durable evidence for {ref.get('surface')} {ref.get('subsystem_id')} before claiming completion.",
        "owner": "assistant",
    }


def _failure_decision(ref: Dict[str, Any], native_status: str) -> Dict[str, str]:
    return {
        "kind": "assistant_continue",
        "summary": f"Inspect {ref.get('surface')} {ref.get('subsystem_id')} after native status {native_status or 'unknown'} and decide whether to relaunch or escalate.",
        "owner": "assistant",
    }


def _synthesize_workflow(ref: Dict[str, Any]) -> Dict[str, Any]:
    state = _read_json(Path(str((ref.get("status_refs") or [""])[0])))
    status = _clean_text(state.get("status")).lower()
    if not state:
        return {
            "status": "interrupted",
            "native_status": "missing",
            "evidence_status": "missing_native_record",
            "evidence_refs": [],
            "next_decision": _failure_decision(ref, "missing"),
            "summary": f"Workflow {ref.get('subsystem_id')} is missing its native state record.",
        }
    evidence_refs = _existing_candidate_paths(ref)
    if status == "interrupted":
        interrupted = state.get("interrupted_context") if isinstance(state.get("interrupted_context"), dict) else {}
        residue_ref = _clean_text(interrupted.get("residue_summary_artifact"))
        if residue_ref:
            residue_abs = str((Path(_repo_root()).resolve() / residue_ref).resolve())
            if residue_abs not in evidence_refs:
                evidence_refs.append(residue_abs)
        return {
            "status": "interrupted",
            "native_status": status,
            "evidence_status": "ambiguous",
            "evidence_refs": evidence_refs or [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
            "next_decision": _failure_decision(ref, status),
            "summary": _clean_text(interrupted.get("reason")) or f"Workflow {ref.get('subsystem_id')} was interrupted before a safe checkpoint.",
        }
    if status in SYNTHESIS_TERMINAL_SUCCESS:
        if len(evidence_refs) == 1:
            return {
                "status": "completed",
                "native_status": status,
                "evidence_status": "present",
                "evidence_refs": evidence_refs,
                "next_decision": dict(ref.get("next_decision") or {}),
                "summary": f"Workflow {ref.get('subsystem_id')} completed with durable evidence.",
            }
        if len(evidence_refs) > 1:
            return {
                "status": "ambiguous_artifact",
                "native_status": status,
                "evidence_status": "ambiguous",
                "evidence_refs": evidence_refs,
                "next_decision": _evidence_gap_decision(ref),
                "summary": f"Workflow {ref.get('subsystem_id')} completed but left multiple competing evidence artifacts.",
            }
        return {
            "status": "evidence_missing",
            "native_status": status,
            "evidence_status": "missing",
            "evidence_refs": [],
            "next_decision": _evidence_gap_decision(ref),
            "summary": f"Workflow {ref.get('subsystem_id')} completed without durable evidence artifacts.",
        }
    if status in SYNTHESIS_TERMINAL_FAILURE:
        return {
            "status": "failed" if status != "cancelled" else "interrupted",
            "native_status": status,
            "evidence_status": "present",
            "evidence_refs": [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
            "next_decision": _failure_decision(ref, status),
            "summary": f"Workflow {ref.get('subsystem_id')} ended with native status {status}.",
        }
    return {
        "status": "waiting_on_job",
        "native_status": status or "running",
        "evidence_status": "pending",
        "evidence_refs": [],
        "next_decision": _running_decision(ref),
        "summary": f"Workflow {ref.get('subsystem_id')} is still in progress.",
    }


def _synthesize_verifier(ref: Dict[str, Any]) -> Dict[str, Any]:
    status = _native_state_from_json_paths(list(ref.get("status_refs") or []))
    result_path = Path(str((ref.get("evidence_candidates") or [""])[0]))
    result = _read_json(result_path)
    if result:
        ok = bool(result.get("ok"))
        native_status = _clean_text(result.get("status") or ("passed" if ok else "failed")).lower()
        return {
            "status": "completed" if ok else "failed",
            "native_status": native_status,
            "evidence_status": "present",
            "evidence_refs": [str(result_path.resolve())],
            "next_decision": dict(ref.get("next_decision") or {}) if ok else _failure_decision(ref, native_status),
            "summary": f"Verifier {ref.get('subsystem_id')} returned {native_status}.",
        }
    native_status = _clean_text(status.get("status")).lower()
    if native_status in SYNTHESIS_TERMINAL_FAILURE:
        return {
            "status": "failed",
            "native_status": native_status,
            "evidence_status": "missing_result",
            "evidence_refs": [],
            "next_decision": _failure_decision(ref, native_status),
            "summary": f"Verifier {ref.get('subsystem_id')} ended with {native_status} but no result.json.",
        }
    return {
        "status": "waiting_on_verification",
        "native_status": native_status or "running",
        "evidence_status": "pending",
        "evidence_refs": [],
        "next_decision": _running_decision(ref),
        "summary": f"Verifier {ref.get('subsystem_id')} is still running or queued.",
    }


def _synthesize_scheduler(ref: Dict[str, Any]) -> Dict[str, Any]:
    state = _native_state_from_json_paths(list(ref.get("status_refs") or []))
    if not state:
        return {
            "status": "interrupted",
            "native_status": "missing",
            "evidence_status": "missing_native_record",
            "evidence_refs": [],
            "next_decision": _failure_decision(ref, "missing"),
            "summary": f"Scheduled job {ref.get('subsystem_id')} is missing its native state record.",
        }
    native_status = _clean_text(state.get("last_status")).lower()
    receipt_path = ""
    evidence_candidates = list(ref.get("evidence_candidates") or [])
    if evidence_candidates:
        receipt_path = str(Path(str(evidence_candidates[0])).resolve())
    elif _clean_text(state.get("last_receipt_path")):
        receipt_path = str(Path(str(state.get("last_receipt_path"))).resolve())
    receipt = _read_json(Path(receipt_path)) if receipt_path else {}
    receipt_status = _clean_text(receipt.get("status")).lower() if receipt else ""
    terminal_failure_statuses = SYNTHESIS_TERMINAL_FAILURE | {"proof_gap", "semantic_failure"}
    effective_terminal_status = native_status or receipt_status
    if native_status in SYNTHESIS_TERMINAL_SUCCESS:
        if receipt:
            return {
                "status": "completed",
                "native_status": native_status,
                "evidence_status": "present",
                "evidence_refs": [receipt_path],
                "next_decision": dict(ref.get("next_decision") or {}),
                "summary": f"Scheduled job {ref.get('subsystem_id')} completed with a durable receipt.",
            }
        return {
            "status": "evidence_missing",
            "native_status": native_status,
            "evidence_status": "missing",
            "evidence_refs": [],
            "next_decision": _evidence_gap_decision(ref),
            "summary": f"Scheduled job {ref.get('subsystem_id')} reports success but no receipt was found.",
        }
    if effective_terminal_status in terminal_failure_statuses:
        return {
            "status": "failed",
            "native_status": effective_terminal_status,
            "evidence_status": "present" if receipt else "missing",
            "evidence_refs": [receipt_path] if receipt else [],
            "next_decision": _failure_decision(ref, effective_terminal_status),
            "summary": f"Scheduled job {ref.get('subsystem_id')} ended with {effective_terminal_status}.",
        }
    if _clean_text(state.get("active_run_id")):
        return {
            "status": "waiting_on_job",
            "native_status": native_status or "running",
            "evidence_status": "pending",
            "evidence_refs": [],
            "next_decision": _running_decision(ref),
            "summary": f"Scheduled job {ref.get('subsystem_id')} is still running.",
        }
    return {
        "status": "waiting_on_job",
        "native_status": native_status or "queued",
        "evidence_status": "pending",
        "evidence_refs": [],
        "next_decision": _running_decision(ref),
        "summary": f"Scheduled job {ref.get('subsystem_id')} has not produced terminal evidence yet.",
    }


def _synthesize_execution_queue(ref: Dict[str, Any]) -> Dict[str, Any]:
    state = _native_state_from_json_paths(list(ref.get("status_refs") or []))
    queue_state = state.get("state") if isinstance(state.get("state"), dict) else state
    if not queue_state:
        return {
            "status": "interrupted",
            "native_status": "missing",
            "evidence_status": "missing_native_record",
            "evidence_refs": [],
            "next_decision": _failure_decision(ref, "missing"),
            "summary": f"Execution queue state for {ref.get('subsystem_id')} is missing.",
        }
    run_id = _clean_text(queue_state.get("run_id"))
    native_status = _clean_text(queue_state.get("status")).lower()
    if run_id and run_id == _clean_text(ref.get("subsystem_id")) and bool(queue_state.get("active")):
        return {
            "status": "waiting_on_job",
            "native_status": native_status or "running",
            "evidence_status": "pending",
            "evidence_refs": [],
            "next_decision": _running_decision(ref),
            "summary": f"Execution queue run {ref.get('subsystem_id')} is active.",
        }
    for row in list(queue_state.get("recent_results") or []):
        if _clean_text(row.get("run_id")) == _clean_text(ref.get("subsystem_id")):
            result_status = _clean_text(row.get("status")).lower()
            return {
                "status": "completed" if result_status in {"completed", "committed", "ok"} else "failed",
                "native_status": result_status,
                "evidence_status": "present",
                "evidence_refs": [],
                "next_decision": dict(ref.get("next_decision") or {}) if result_status in {"completed", "committed", "ok"} else _failure_decision(ref, result_status),
                "summary": f"Execution queue run {ref.get('subsystem_id')} recorded {result_status}.",
            }
    return {
        "status": "interrupted",
        "native_status": native_status or "unknown",
        "evidence_status": "missing_result",
        "evidence_refs": [],
        "next_decision": _failure_decision(ref, native_status or "unknown"),
        "summary": f"Execution queue run {ref.get('subsystem_id')} no longer appears in the native queue state.",
    }


def _synthesize_mailbox(repo_root: Path, ref: Dict[str, Any]) -> Dict[str, Any]:
    record = _read_json(Path(str((ref.get("status_refs") or [""])[0])))
    if not record:
        return {
            "status": "interrupted",
            "native_status": "missing",
            "evidence_status": "missing_native_record",
            "evidence_refs": [],
            "next_decision": _failure_decision(ref, "missing"),
            "summary": f"Mailbox request {ref.get('subsystem_id')} is missing its durable record.",
        }
    state = _clean_text(record.get("state")).lower()
    handoff = dict(record.get("handoff") or {})
    if state in {"queued", "replayed"}:
        return {
            "status": "accepted",
            "native_status": state,
            "evidence_status": "pending",
            "evidence_refs": [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
            "next_decision": _running_decision(ref),
            "summary": f"Mailbox request {ref.get('subsystem_id')} is durably queued for claim.",
        }
    if state == "claimed":
        return {
            "status": "running",
            "native_status": state,
            "evidence_status": "pending",
            "evidence_refs": [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
            "next_decision": _running_decision(ref),
            "summary": f"Mailbox request {ref.get('subsystem_id')} is currently claimed but not yet handed off to a job.",
        }
    if state == "lease_expired":
        return {
            "status": "interrupted",
            "native_status": state,
            "evidence_status": "ambiguous",
            "evidence_refs": [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
            "next_decision": _failure_decision(ref, state),
            "summary": f"Mailbox request {ref.get('subsystem_id')} lost its claim lease before durable job acceptance.",
        }
    if state == "dead_lettered":
        return {
            "status": "failed",
            "native_status": state,
            "evidence_status": "present",
            "evidence_refs": [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
            "next_decision": _failure_decision(ref, state),
            "summary": f"Mailbox request {ref.get('subsystem_id')} is dead-lettered and needs operator or assistant review.",
        }
    if state == "handed_off":
        detached_job_id = _clean_text(handoff.get("detached_job_id"))
        if detached_job_id:
            lifecycle_path = _job_lifecycle.job_path(Path(repo_root).resolve(), detached_job_id)
            lifecycle = _read_json(lifecycle_path)
            if lifecycle:
                lifecycle_state = _clean_text(lifecycle.get("state")).lower()
                evidence_refs = [str(lifecycle_path.resolve())]
                latest = dict(lifecycle.get("latest_result") or {})
                late_result_pending = bool(lifecycle.get("late_result_pending_reconciliation"))
                late_result_basis = _clean_text(lifecycle.get("late_result_basis_state"))
                if lifecycle_state == "queued":
                    return {
                        "status": "waiting_on_job",
                        "native_status": lifecycle_state,
                        "evidence_status": "present",
                        "evidence_refs": evidence_refs,
                        "next_decision": _running_decision(ref),
                        "summary": f"Detached job {detached_job_id} was accepted and is waiting to start.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
                if lifecycle_state == "running":
                    return {
                        "status": "running",
                        "native_status": lifecycle_state,
                        "evidence_status": "pending",
                        "evidence_refs": evidence_refs,
                        "next_decision": _running_decision(ref),
                        "summary": f"Detached job {detached_job_id} is actively running.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
                if lifecycle_state == "heartbeat_stale":
                    return {
                        "status": "interrupted",
                        "native_status": lifecycle_state,
                        "evidence_status": "ambiguous",
                        "evidence_refs": evidence_refs,
                        "next_decision": _failure_decision(ref, lifecycle_state),
                        "summary": f"Detached job {detached_job_id} stopped heartbeating and may still be running elsewhere.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
                if lifecycle_state == "timed_out":
                    return {
                        "status": "timed_out",
                        "native_status": lifecycle_state,
                        "evidence_status": "ambiguous",
                        "evidence_refs": evidence_refs,
                        "next_decision": _failure_decision(ref, lifecycle_state),
                        "summary": f"Detached job {detached_job_id} exceeded its timeout and may still report late results.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
                if lifecycle_state == "cancelled":
                    return {
                        "status": "interrupted",
                        "native_status": lifecycle_state,
                        "evidence_status": "present",
                        "evidence_refs": evidence_refs,
                        "next_decision": _failure_decision(ref, lifecycle_state),
                        "summary": f"Detached job {detached_job_id} was cancelled.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
                if lifecycle_state == "interrupted":
                    return {
                        "status": "interrupted",
                        "native_status": lifecycle_state,
                        "evidence_status": "ambiguous",
                        "evidence_refs": evidence_refs,
                        "next_decision": _failure_decision(ref, lifecycle_state),
                        "summary": f"Detached job {detached_job_id} was interrupted during restart reconciliation.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
                if lifecycle_state == "ambiguous":
                    return {
                        "status": "ambiguous_artifact",
                        "native_status": lifecycle_state,
                        "evidence_status": "ambiguous",
                        "evidence_refs": evidence_refs,
                        "next_decision": _failure_decision(ref, lifecycle_state),
                        "summary": _clean_text(latest.get("summary")) or f"Detached job {detached_job_id} reached a conflicting terminal outcome.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
                if lifecycle_state in {"completed", "failed"}:
                    if late_result_pending:
                        return {
                            "status": "ambiguous_artifact",
                            "native_status": lifecycle_state,
                            "evidence_status": "ambiguous",
                            "evidence_refs": evidence_refs + [row for row in list(latest.get("evidence_refs") or []) if row],
                            "next_decision": _failure_decision(ref, "late_result_pending_reconciliation"),
                            "summary": (
                                f"Detached job {detached_job_id} reported {lifecycle_state} after {late_result_basis or 'a prior runtime fault'} "
                                "and still needs explicit reconciliation."
                            ),
                            "detached_job_id": detached_job_id,
                            "result_correlation_basis": "detached_job_id",
                        }
                    correlation_id = _clean_text(ref.get("correlation_id"))
                    correlation_record = (
                        _read_json(_correlation.record_path(Path(repo_root).resolve(), correlation_id))
                        if correlation_id
                        else {}
                    )
                    correlation_status = _clean_text(correlation_record.get("latest_delivery_status")).lower()
                    correlation_latest_result_id = _clean_text(correlation_record.get("latest_result_id"))
                    lifecycle_latest_result_id = _clean_text(latest.get("result_id"))
                    correlation_path = (
                        str(_correlation.record_path(Path(repo_root).resolve(), correlation_id).resolve()) if correlation_id else ""
                    )
                    delivery_refs = evidence_refs + [row for row in list(latest.get("evidence_refs") or []) if row]
                    if correlation_path:
                        delivery_refs.append(correlation_path)
                    if correlation_status == "pending_reattach" and lifecycle_latest_result_id:
                        delivery_refs_with_status = list(delivery_refs)
                        return {
                            "status": lifecycle_state,
                            "native_status": lifecycle_state,
                            "evidence_status": "ambiguous",
                            "evidence_refs": delivery_refs_with_status,
                            "next_decision": {
                                "kind": "review_evidence",
                                "summary": f"Review detached result delivery for {detached_job_id} before canonical writeback.",
                                "owner": "assistant",
                            },
                            "summary": (
                                f"Detached job {detached_job_id} reported {lifecycle_state}, but canonical delivery "
                                "is still pending reattach."
                            ),
                            "detached_job_id": detached_job_id,
                            "result_correlation_basis": "detached_job_id",
                        }
                    if (
                        correlation_status != "delivered"
                        or not lifecycle_latest_result_id
                        or correlation_latest_result_id != lifecycle_latest_result_id
                    ):
                        pending_reason = correlation_status or "missing_delivery_record"
                        if correlation_latest_result_id and lifecycle_latest_result_id and correlation_latest_result_id != lifecycle_latest_result_id:
                            pending_reason = "delivery_truth_mismatch"
                        return {
                            "status": "ambiguous_artifact",
                            "native_status": lifecycle_state,
                            "evidence_status": "ambiguous",
                            "evidence_refs": delivery_refs,
                            "next_decision": _failure_decision(ref, pending_reason),
                            "summary": (
                                f"Detached job {detached_job_id} reported {lifecycle_state}, but canonical delivery "
                                "into correlation truth is not yet proven."
                            ),
                            "detached_job_id": detached_job_id,
                            "result_correlation_basis": "detached_job_id",
                        }
                    return {
                        "status": lifecycle_state,
                        "native_status": lifecycle_state,
                        "evidence_status": "present",
                        "evidence_refs": delivery_refs,
                        "next_decision": dict(ref.get("next_decision") or _running_decision(ref)) if lifecycle_state == "completed" else _failure_decision(ref, lifecycle_state),
                        "summary": _clean_text(latest.get("summary")) or f"Detached job {detached_job_id} {lifecycle_state}.",
                        "detached_job_id": detached_job_id,
                        "result_correlation_basis": "detached_job_id",
                    }
        detached_job_id = _clean_text(handoff.get("detached_job_id"))
        missing_reason = "missing_detached_job_lifecycle" if detached_job_id else "missing_detached_job_handoff_target"
        return {
            "status": "interrupted",
            "native_status": state,
            "evidence_status": "ambiguous",
            "evidence_refs": [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
            "next_decision": _failure_decision(ref, missing_reason),
            "summary": (
                f"Mailbox request {ref.get('subsystem_id')} recorded a handoff to "
                f"{handoff.get('job_surface') or 'job'} {detached_job_id or '<missing>'}, "
                "but the detached-job lifecycle record is not durably present."
            ),
            "detached_job_id": detached_job_id,
            "result_correlation_basis": "detached_job_id" if detached_job_id else "request_id",
        }
    return {
        "status": "superseded",
        "native_status": state or "superseded",
        "evidence_status": "present",
        "evidence_refs": [str(Path(str((ref.get("status_refs") or [""])[0])).resolve())],
        "next_decision": _failure_decision(ref, state or "superseded"),
        "summary": f"Mailbox request {ref.get('subsystem_id')} is no longer claimable.",
    }


def synthesize_delegation_ref(repo_root: Path, ref: Dict[str, Any]) -> Dict[str, Any]:
    _ = Path(repo_root).resolve()
    surface = _clean_text(ref.get("surface")).lower()
    if surface == "workflow":
        synthesis = _synthesize_workflow(ref)
    elif surface == "verifier":
        synthesis = _synthesize_verifier(ref)
    elif surface == "scheduler":
        synthesis = _synthesize_scheduler(ref)
    elif surface == "execution_queue":
        synthesis = _synthesize_execution_queue(ref)
    elif surface == "mailbox":
        synthesis = _synthesize_mailbox(Path(repo_root).resolve(), ref)
    else:
        evidence_refs = _existing_candidate_paths(ref)
        if evidence_refs:
            synthesis = {
                "status": "completed",
                "native_status": _clean_text(ref.get("native_status_hint") or "completed").lower(),
                "evidence_status": "present",
                "evidence_refs": evidence_refs,
                "next_decision": dict(ref.get("next_decision") or {}),
                "summary": f"{surface} {ref.get('subsystem_id')} has durable evidence.",
            }
        else:
            synthesis = {
                "status": "waiting_on_job",
                "native_status": _clean_text(ref.get("native_status_hint") or "waiting").lower(),
                "evidence_status": "pending",
                "evidence_refs": [],
                "next_decision": _running_decision(ref),
                "summary": f"{surface} {ref.get('subsystem_id')} is waiting on durable evidence.",
            }
    request_envelope = {
        "schema_version": _policy.DELEGATION_REQUEST_SCHEMA_VERSION,
        "request_id": _clean_text(ref.get("delegation_request_id")),
        "surface": surface,
        "classification": "delegate",
        "reason_codes": list(ref.get("reason_codes") or ["bound"]),
        "owner_ref": dict(ref.get("owner_ref") or {}),
        "summary": _clean_text(ref.get("summary")),
        "requested_action": _clean_text(ref.get("requested_action") or ref.get("summary")),
        "input_payload": {},
        "mission_fit": dict(ref.get("mission_fit") or {}),
        "expected_evidence": list(ref.get("expected_evidence") or [{"kind": "status_artifact", "description": "delegated evidence", "required": True}]),
        "next_decision": dict(ref.get("next_decision") or _running_decision(ref)),
        "status_on_reject": "blocked",
        "created_utc": _clean_text(ref.get("bound_utc") or _utc_now_iso()),
        "correlation_id": _clean_text(ref.get("correlation_id")),
        "launch_turn_id": _clean_text(ref.get("launch_turn_id")),
        "detached_job_id": _clean_text(ref.get("detached_job_id")),
        "result_correlation_basis": _clean_text(ref.get("result_correlation_basis") or "request_id"),
        "idempotency_key": _clean_text(((ref.get("idempotency_keys") or [""])[0]) or ref.get("delegation_request_id")),
        "launch_cause": _clean_text(ref.get("launch_cause") or "delegated_execution"),
        "parent_request_id": _clean_text(ref.get("parent_request_id")),
        "prior_result_id": _clean_text(ref.get("prior_result_id")),
        "parent_ledger_uid": _clean_text(((ref.get("owner_ref") or {}) if isinstance(ref.get("owner_ref"), dict) else {}).get("ledger_uid")),
        "parent_task_id": _clean_text(((ref.get("owner_ref") or {}) if isinstance(ref.get("owner_ref"), dict) else {}).get("task_id")),
        "parent_open_loop_id": _clean_text(((ref.get("owner_ref") or {}) if isinstance(ref.get("owner_ref"), dict) else {}).get("open_loop_id")),
    }
    detached_job_id = _clean_text(synthesis.get("detached_job_id"))
    if detached_job_id:
        request_envelope["detached_job_id"] = detached_job_id
        request_envelope["result_correlation_basis"] = _clean_text(
            synthesis.get("result_correlation_basis") or "detached_job_id"
        )
    result = _policy.build_typed_delegation_result(
        request=request_envelope,
        status=_clean_text(synthesis.get("status")),
        summary=_clean_text(synthesis.get("summary")),
        native_status=_clean_text(synthesis.get("native_status")),
        evidence_status=_clean_text(synthesis.get("evidence_status")),
        evidence_refs=list(synthesis.get("evidence_refs") or []),
        artifact_refs=list(ref.get("artifact_refs") or []),
        next_decision=dict(synthesis.get("next_decision") or _running_decision(ref)),
        updated_utc=_utc_now_iso(),
        worker_ref=f"{surface}:{_clean_text(ref.get('subsystem_id'))}",
        producer="df0c.synthesize_delegation_ref",
        prior_result_id=_clean_text(ref.get("prior_result_id")),
    )
    return {
        **result,
        "delegation_request_id": result["request_id"],
        "subsystem_id": _clean_text(ref.get("subsystem_id")),
    }


def synthesize_task_status(
    repo_root: Path,
    *,
    task_id: str,
    task_row: Optional[Dict[str, Any]] = None,
    persist_reconciliation: bool = True,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target = dict(task_row or {})
    if target and _clean_text(target.get("task_id")) != _clean_text(task_id):
        target = {}
    if not target:
        register = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False).get("task_register") or {}
        tasks = [
            dict(task)
            for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
            for task in list(register.get(bucket_name) or [])
            if isinstance(task, dict)
        ]
        target = next((dict(task) for task in tasks if _clean_text(task.get("task_id")) == _clean_text(task_id)), None)
    if target is None:
        raise DelegationBindingsError(f"task not found: {_clean_text(task_id)}")
    refs = list(target.get("delegation_refs") or [])
    syntheses = [synthesize_delegation_ref(repo_root, ref) for ref in refs]
    latest = syntheses[-1] if syntheses else {
        "status": _clean_text(target.get("status") or "active"),
        "summary": "Task has no delegation bindings.",
        "next_decision": {"kind": "assistant_continue", "summary": _clean_text(target.get("next_expected_action")), "owner": "assistant"},
        "evidence_status": "none",
        "evidence_refs": [],
    }
    reconciliation_report: Dict[str, Any] = {}
    reconciliation_error = ""
    try:
        if persist_reconciliation:
            reconciliation_report = _execution_reconciliation.record_task_reconciliation_report(
                repo_root,
                task_id=_clean_text(target.get("task_id")),
                ledger_uid=_clean_text(target.get("ledger_uid")),
                task_status=_clean_text(target.get("status")),
                refs=refs,
                syntheses=syntheses,
                writer="y5z7.synthesize_task_status",
            )
        else:
            reconciliation_report = _execution_reconciliation.build_task_reconciliation_report(
                repo_root,
                task_id=_clean_text(target.get("task_id")),
                ledger_uid=_clean_text(target.get("ledger_uid")),
                task_status=_clean_text(target.get("status")),
                refs=refs,
                syntheses=syntheses,
                writer="y5z7.synthesize_task_status.read_only",
            )
    except Exception as exc:
        reconciliation_error = str(exc)
    projection = dict(reconciliation_report.get("projection") or {})
    projected_status = _clean_text(projection.get("status")).lower()
    if projected_status and projected_status != _clean_text(latest.get("status")).lower():
        latest = dict(latest)
        latest["status"] = projected_status
        latest["summary"] = _clean_text(projection.get("summary")) or _clean_text(latest.get("summary"))
        if projected_status in {"ambiguous_artifact", "interrupted_recovering"}:
            latest["evidence_status"] = "ambiguous"
            latest["next_decision"] = {
                "kind": "assistant_continue",
                "summary": "Reconcile conflicting substrate truth before claiming completion.",
                "owner": "assistant",
            }
        elif projected_status == "failed":
            latest["next_decision"] = {
                "kind": "assistant_continue",
                "summary": "Inspect conflicting runtime records and decide relaunch or escalation.",
                "owner": "assistant",
            }
    durable_refs = list(((reconciliation_report.get("durable_refs") or {}) if isinstance(reconciliation_report.get("durable_refs"), dict) else {}).get("all_refs") or [])
    if durable_refs:
        latest = dict(latest)
        existing_refs = [str(item) for item in list(latest.get("evidence_refs") or []) if str(item).strip()]
        latest["evidence_refs"] = list(dict.fromkeys(existing_refs + durable_refs))
    if reconciliation_report:
        latest = dict(latest)
        latest["execution_reconciliation"] = {
            "reconciliation_id": _clean_text(reconciliation_report.get("reconciliation_id")),
            "projected_status": _clean_text(projection.get("status")),
            "reason_codes": list(projection.get("reason_codes") or []),
            "conflict_count": int(projection.get("conflict_count") or 0),
            "report_path": _clean_text(((reconciliation_report.get("report_paths") or {}) if isinstance(reconciliation_report.get("report_paths"), dict) else {}).get("report_path")),
            "receipt_path": _clean_text(((reconciliation_report.get("report_paths") or {}) if isinstance(reconciliation_report.get("report_paths"), dict) else {}).get("receipt_path")),
        }
    return {
        "schema_version": "lab_assistant_task_delegation_status_v1",
        "task_id": _clean_text(target.get("task_id")),
        "ledger_uid": _clean_text(target.get("ledger_uid")),
        "task_title": _clean_text(target.get("title")),
        "task_status": _clean_text(target.get("status")),
        "delegated": bool(refs),
        "delegation_count": len(refs),
        "delegation_refs": refs,
        "delegation_summaries": syntheses,
        "latest": latest,
        "reconciliation": {
            "reconciliation_id": _clean_text(reconciliation_report.get("reconciliation_id")),
            "projection": projection,
            "report_paths": dict(reconciliation_report.get("report_paths") or {}),
            "error": _clean_text(reconciliation_error),
        },
    }


def rebuild_delegation_snapshot(repo_root: Path, *, writer: str = "delegation_bindings.snapshot") -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    snapshot = _register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    register = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
    tasks = [
        dict(task)
        for bucket_name in ("active_tasks", "completed_tasks", "residue_tasks")
        for task in list(register.get(bucket_name) or [])
        if isinstance(task, dict)
    ]
    task_rows = [
        synthesize_task_status(
            repo_root,
            task_id=_clean_text(task.get("task_id")),
            task_row=dict(task),
            persist_reconciliation=False,
        )
        for task in tasks
        if _clean_text(task.get("task_id"))
    ]
    reverse_index: Dict[str, Dict[str, str]] = {}
    for task_row in task_rows:
        for ref in list(task_row.get("delegation_refs") or []):
            key = f"{_clean_text(ref.get('subsystem'))}:{_clean_text(ref.get('subsystem_id'))}"
            if key:
                reverse_index[key] = {
                    "task_id": _clean_text(task_row.get("task_id")),
                    "ledger_uid": _clean_text(task_row.get("ledger_uid")),
                    "delegation_request_id": _clean_text(ref.get("delegation_request_id")),
                }
    payload = {
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "derived_only": True,
        "writer": str(writer or ""),
        "generated_utc": _utc_now_iso(),
        "register_generation": int(register.get("write_generation") or 0),
        "tasks": task_rows,
    }
    index_payload = {
        "schema_version": SNAPSHOT_INDEX_SCHEMA_VERSION,
        "derived_only": True,
        "generated_utc": payload["generated_utc"],
        "register_generation": payload["register_generation"],
        "bindings": reverse_index,
    }
    _write_json_atomic(_snapshot_path(repo_root), payload)
    _write_json_atomic(_reverse_index_path(repo_root), index_payload)
    return {
        "snapshot": payload,
        "reverse_index": index_payload,
    }
