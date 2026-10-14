def invalidate_task_decomposition(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
    replanning_state: str,
    summary: str,
    trigger: Dict[str, Any],
    next_expected_action: str = "",
    blocked_reason: str = "",
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target_task_id = str(task_id or "").strip()
    normalized_state = str(replanning_state or "").strip()
    if not target_task_id:
        raise TaskRegisterError("task_id is required")
    if normalized_state not in {"needs_replan", "blocked", "degraded"}:
        raise TaskRegisterError(f"invalid replanning_state: {normalized_state or '<missing>'}")
    trigger_row = _normalize_replan_trigger(dict(trigger or {}), default_kind="changed_evidence")
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            task_bucket, existing_task = _find_task_with_bucket(register, target_task_id)
            if existing_task is None:
                raise TaskRegisterError(f"task not found: {target_task_id}")
            existing_contract = dict(existing_task.get("decomposition_contract") or {})
            invalidated = _invalidate_current_revision_history(existing_contract, trigger=trigger_row)
            decomposition_contract = _normalize_task_decomposition_contract(
                {
                    "schema_version": TASK_DECOMPOSITION_CONTRACT_SCHEMA_VERSION,
                    "linked_assumption_ids": list(existing_contract.get("linked_assumption_ids") or []),
                    "linked_acceptance_target_ids": list(existing_contract.get("linked_acceptance_target_ids") or []),
                    "revision_history": invalidated["revision_history"],
                    "current_revision_id": "",
                    "replanning": {
                        "state": normalized_state,
                        "summary": str(summary or "").strip(),
                        "invalidated_revision_id": str(invalidated["invalidated_revision_id"] or ""),
                        "invalidated_plan_version": int(invalidated["invalidated_plan_version"] or 0),
                        "trigger": trigger_row,
                        "updated_utc": _utc_now_iso(),
                    },
                    "updated_utc": _utc_now_iso(),
                }
            )
            existing_task["decomposition_contract"] = decomposition_contract
            if next_expected_action:
                existing_task["next_expected_action"] = str(next_expected_action or "").strip()
            if blocked_reason:
                existing_task["blocked_reason"] = str(blocked_reason or "").strip()
            if normalized_state == "blocked":
                existing_task["status"] = "blocked"
            elif normalized_state == "degraded":
                existing_task["status"] = "degraded"
            normalized_task = _normalize_task(existing_task)
            _replace_task_in_bucket(register, source_bucket=str(task_bucket or ""), task=normalized_task)
            _set_generation(register, writer=writer)
            _append_event(
                repo_root,
                event_type="task_decomposition_invalidated",
                payload={
                    "writer": str(writer or ""),
                    "task_id": target_task_id,
                    "replanning_state": normalized_state,
                    "invalidated_revision_id": str(invalidated["invalidated_revision_id"] or ""),
                    "invalidated_plan_version": int(invalidated["invalidated_plan_version"] or 0),
                    "trigger_kind": str(trigger_row.get("kind") or ""),
                },
            )
            _write_generation_event(repo_root, register=register, writer=writer)
            summary_cache = _write_summary_cache(repo_root, register)
            _append_commit_event(repo_root, register=register, summary=summary_cache, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary_cache)
            return {"ok": True, "changed": True, "task_register": dict(register)}


def publish_task_planning_handoff(
    repo_root: Path,
    *,
    expected_generation: int,
    writer: str,
    task_id: str,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target_task_id = str(task_id or "").strip()
    if not target_task_id:
        raise TaskRegisterError("task_id is required")
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            current_generation = int(register.get("write_generation") or 0)
            if int(expected_generation) != current_generation:
                _reject_stale_write(
                    repo_root,
                    writer=writer,
                    expected_generation=int(expected_generation),
                    current_generation=current_generation,
                )
                raise TaskRegisterError(
                    f"stale write rejected: expected_generation={int(expected_generation)} current_generation={current_generation}"
                )
            _task_bucket, task = _find_task_with_bucket(register, target_task_id)
            if task is None:
                raise TaskRegisterError(f"task not found: {target_task_id}")
            handoff = _build_task_planning_handoff_payload(
                register_generation=current_generation,
                task=task,
            )
            artifact_ref = _write_planning_handoff_artifact(repo_root, task=task, handoff=handoff)
            handoff["artifact_ref"] = artifact_ref
            _append_event(
                repo_root,
                event_type="task_planning_handoff_published",
                payload={
                    "writer": str(writer or ""),
                    "task_id": target_task_id,
                    "ledger_uid": str(task.get("ledger_uid") or "").strip(),
                    "artifact_ref": artifact_ref,
                    "launch_ready": bool(dict(handoff.get("launch_readiness") or {}).get("launch_ready")),
                    "readiness_state": str(dict(handoff.get("launch_readiness") or {}).get("state") or ""),
                    "current_revision_id": str(
                        dict(dict(handoff.get("execution_launch_contract") or {}).get("launch_identity") or {}).get("current_revision_id")
                        or ""
                    ),
                    "plan_version": int(
                        dict(dict(handoff.get("execution_launch_contract") or {}).get("launch_identity") or {}).get("plan_version") or 0
                    ),
                },
            )
            return {"ok": True, "handoff": handoff}


def read_task_planning_handoff(
    repo_root: Path,
    *,
    task_id: str,
    rebuild_if_missing: bool = True,
) -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    target_task_id = str(task_id or "").strip()
    if not target_task_id:
        raise TaskRegisterError("task_id is required")
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            _task_bucket, task = _find_task_with_bucket(register, target_task_id)
            if task is None:
                raise TaskRegisterError(f"task not found: {target_task_id}")
            latest_path = _latest_planning_handoff_artifact_path(repo_root, task=task)
            if latest_path is not None:
                payload = _read_json_strict(latest_path)
                if str(payload.get("schema_version") or "").strip() != TASK_PLANNING_HANDOFF_SCHEMA_VERSION:
                    raise TaskRegisterError("latest planning handoff artifact is incompatible")
                payload["artifact_ref"] = str(latest_path.relative_to(repo_root))
                payload["source_of_truth"] = dict(payload.get("source_of_truth") or {})
                payload["source_of_truth"]["reconstructed_from_canonical_planning_state"] = False
                return {"ok": True, "handoff": payload, "source": "artifact"}
            if not rebuild_if_missing:
                raise TaskRegisterError(f"planning handoff not found for task {target_task_id}")
            handoff = _build_task_planning_handoff_payload(
                register_generation=int(register.get("write_generation") or 0),
                task=task,
            )
            handoff["source_of_truth"] = dict(handoff.get("source_of_truth") or {})
            handoff["source_of_truth"]["reconstructed_from_canonical_planning_state"] = True
            return {"ok": True, "handoff": handoff, "source": "reconstructed"}


def rebuild_summary_cache(repo_root: Path, *, writer: str = "task_register.summary_rebuild") -> Dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    with _LOCK:
        with _task_register_lock(repo_root):
            register = _load_or_init_task_register_locked(repo_root)
            summary = _write_summary_cache(repo_root, register)
            _append_event(
                repo_root,
                event_type="summary_rebuilt",
                payload={"writer": str(writer or ""), "write_generation": int(register.get("write_generation") or 0)},
            )
            _append_commit_event(repo_root, register=register, summary=summary, writer=writer)
            _write_json_atomic(_register_path(repo_root), register)
            _write_json_atomic(_summary_cache_path(repo_root), summary)
            return {"ok": True, "summary_cache": summary, "task_register": dict(register)}
