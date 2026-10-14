def _normalize_swarm_launch(raw: Any) -> Dict[str, Any]:
    from infra.controlplane import fanout_master_contract as _fanout_master_contract

    obj = raw if isinstance(raw, dict) else {}
    mission_type = str(obj.get("mission_type") or "delivery_patch").strip().lower()
    if mission_type not in SWARM_MISSION_TYPE_ALLOWLIST:
        raise ValueError(f"swarm.mission_type must be one of: {', '.join(sorted(SWARM_MISSION_TYPE_ALLOWLIST))}")
    objective = str(obj.get("objective") or "").strip()
    if not objective:
        raise ValueError("swarm.objective is required")
    scope = str(obj.get("scope") or "").strip()
    expected_outputs = _normalize_string_list(obj.get("expected_outputs"), field_name="swarm.expected_outputs")

    success_raw = obj.get("success_contract") if isinstance(obj.get("success_contract"), dict) else {}
    accepted_statuses = _normalize_string_list(success_raw.get("accepted_statuses"), field_name="swarm.success_contract.accepted_statuses")
    if not accepted_statuses:
        accepted_statuses = ["finished"]
    for status in accepted_statuses:
        if status not in SWARM_SUCCESS_STATUS_SET:
            raise ValueError(
                "swarm.success_contract.accepted_statuses entries must be one of: "
                + ", ".join(sorted(SWARM_SUCCESS_STATUS_SET))
            )
    success_contract = {
        "accepted_statuses": accepted_statuses,
        "require_changed_files": bool(success_raw.get("require_changed_files", True)),
    }

    evidence_raw = obj.get("evidence_requirements") if isinstance(obj.get("evidence_requirements"), dict) else {}
    evidence_requirements = {
        "require_summary_path": bool(evidence_raw.get("require_summary_path", True)),
        "require_worker_artifacts": bool(evidence_raw.get("require_worker_artifacts", False)),
    }

    write_raw = obj.get("write_policy") if isinstance(obj.get("write_policy"), dict) else {}
    write_mode = str(write_raw.get("mode") or "bounded_patch").strip().lower() or "bounded_patch"
    if write_mode not in SWARM_WRITE_POLICY_SET:
        raise ValueError(f"swarm.write_policy.mode must be one of: {', '.join(sorted(SWARM_WRITE_POLICY_SET))}")
    max_changed_files_raw = write_raw.get("max_changed_files")
    try:
        max_changed_files = int(max_changed_files_raw) if max_changed_files_raw not in (None, "") else 25
    except Exception as exc:
        raise ValueError("swarm.write_policy.max_changed_files must be an integer when provided") from exc
    if max_changed_files < 1 or max_changed_files > 500:
        raise ValueError("swarm.write_policy.max_changed_files must be between 1 and 500")
    write_policy = {
        "mode": write_mode,
        "allowed_paths": _normalize_relpath_list(write_raw.get("allowed_paths"), field_name="swarm.write_policy.allowed_paths"),
        "max_changed_files": max_changed_files,
    }

    retry_raw = obj.get("retry_policy") if isinstance(obj.get("retry_policy"), dict) else {}
    retry_policy: Dict[str, int] = {}
    integer_retry_fields = {
        "child_relaunch_limit": (0, 10, 1),
        "master_rerun_limit": (0, 10, 0),
    }
    for key, (minimum, maximum, default) in integer_retry_fields.items():
        value = retry_raw.get(key)
        try:
            ivalue = int(value) if value not in (None, "") else default
        except Exception as exc:
            raise ValueError(f"swarm.retry_policy.{key} must be an integer when provided") from exc
        if ivalue < minimum or ivalue > maximum:
            raise ValueError(f"swarm.retry_policy.{key} must be between {minimum} and {maximum}")
        retry_policy[key] = ivalue

    launch_raw = obj.get("launch_options") if isinstance(obj.get("launch_options"), dict) else {}
    launch_options: Dict[str, Any] = {
        "run_async": bool(launch_raw.get("run_async", False)),
        "started_by_role": str(launch_raw.get("started_by_role") or "generalist").strip().lower() or "generalist",
    }
    _role_target(launch_options["started_by_role"])
    integer_launch_fields = {
        "wall_clock_s": (60, 7200),
        "maker_lease_s": (30, 3600),
        "finisher_lease_s": (30, 3600),
        "max_makers": (1, 12),
        "max_finishers": (1, 8),
        "max_total_tokens": (0, 50_000_000),
    }
    for key, bounds in integer_launch_fields.items():
        value = launch_raw.get(key)
        if value in (None, ""):
            continue
        try:
            ivalue = int(value)
        except Exception as exc:
            raise ValueError(f"swarm.launch_options.{key} must be an integer when provided") from exc
        minimum, maximum = bounds
        if ivalue < minimum or ivalue > maximum:
            raise ValueError(f"swarm.launch_options.{key} must be between {minimum} and {maximum}")
        launch_options[key] = ivalue

    return {
        "mission_type": mission_type,
        "objective": objective,
        "scope": scope,
        "expected_outputs": expected_outputs,
        "success_contract": success_contract,
        "evidence_requirements": evidence_requirements,
        "write_policy": write_policy,
        "retry_policy": retry_policy,
        "launch_options": launch_options,
        "fanout_contract": {
            "uid": _fanout_master_contract.UID,
            "master_owner": _fanout_master_contract.MASTER_OWNER,
            "scheduler_terminal_stage": _fanout_master_contract.SCHEDULER_TERMINAL_STAGE,
        },
    }


def _render_swarm_launch_prompt(sw: Dict[str, Any]) -> str:
    from infra.controlplane import fanout_master_contract as _fanout_master_contract

    success = sw.get("success_contract") if isinstance(sw.get("success_contract"), dict) else {}
    evidence = sw.get("evidence_requirements") if isinstance(sw.get("evidence_requirements"), dict) else {}
    write_policy = sw.get("write_policy") if isinstance(sw.get("write_policy"), dict) else {}
    retry_policy = sw.get("retry_policy") if isinstance(sw.get("retry_policy"), dict) else {}
    launch_options = sw.get("launch_options") if isinstance(sw.get("launch_options"), dict) else {}
    fanout_contract = _fanout_master_contract.fanout_master_contract_v1()
    lines = [
        "[Scheduled Swarm Launch Contract]",
        f"fanout_contract.uid: {fanout_contract.uid}",
        f"fanout_contract.master_owner: {fanout_contract.master_owner}",
        f"fanout_contract.lifecycle_stages: {', '.join(fanout_contract.lifecycle_stages)}",
        f"fanout_contract.durable_truth_fields: {', '.join(fanout_contract.durable_truth_fields)}",
        f"mission_type: {str(sw.get('mission_type') or '')}",
        f"objective: {str(sw.get('objective') or '')}",
        f"scope: {str(sw.get('scope') or '')}",
        f"expected_outputs: {', '.join(list(sw.get('expected_outputs') or [])) or '(unspecified)'}",
        f"accepted_statuses: {', '.join(list(success.get('accepted_statuses') or [])) or 'finished'}",
        f"require_changed_files: {str(bool(success.get('require_changed_files'))).lower()}",
        f"require_summary_path: {str(bool(evidence.get('require_summary_path'))).lower()}",
        f"require_worker_artifacts: {str(bool(evidence.get('require_worker_artifacts'))).lower()}",
        f"write_policy.mode: {str(write_policy.get('mode') or '')}",
        f"write_policy.allowed_paths: {', '.join(list(write_policy.get('allowed_paths') or [])) or '(any repo path)'}",
        f"write_policy.max_changed_files: {str(write_policy.get('max_changed_files') or '')}",
        f"retry_policy.child_relaunch_limit: {str(retry_policy.get('child_relaunch_limit') or 0)}",
        f"retry_policy.master_rerun_limit: {str(retry_policy.get('master_rerun_limit') or 0)}",
        f"launch_options.run_async: {str(bool(launch_options.get('run_async'))).lower()}",
        f"launch_options.started_by_role: {str(launch_options.get('started_by_role') or '')}",
    ]
    for key in ("wall_clock_s", "maker_lease_s", "finisher_lease_s", "max_makers", "max_finishers"):
        if key in launch_options:
            lines.append(f"launch_options.{key}: {str(launch_options.get(key) or '')}")
    if "max_total_tokens" in launch_options:
        lines.append(f"launch_options.max_total_tokens: {str(launch_options.get('max_total_tokens') or '')}")
    return "\n".join(lines).strip()


def _swarm_changed_files_from_state(state: Dict[str, Any]) -> List[str]:
    integration = state.get("integration") if isinstance(state.get("integration"), dict) else {}
    out: List[str] = []
    for item in list(integration.get("changed_files") or []):
        path = str(item or "").strip()
        if path and path not in out:
            out.append(path)
    for worker in list(state.get("workers") or []):
        if not isinstance(worker, dict):
            continue
        for item in list(worker.get("changed_files") or []):
            path = str(item or "").strip()
            if path and path not in out:
                out.append(path)
    return out


def _evaluate_swarm_launch_result(*, state: Dict[str, Any], swarm: Dict[str, Any]) -> Dict[str, Any]:
    from infra.controlplane import fanout_master_integration as _fanout_master_integration

    integrated_result = _fanout_master_integration.integrate_swarm_result(state=state, swarm=swarm)
    evidence_summary = (
        integrated_result.get("evidence_summary")
        if isinstance(integrated_result.get("evidence_summary"), dict)
        else {}
    )
    errors = [str(item).strip() for item in list(integrated_result.get("decision_rationale") or []) if str(item).strip()]

    return {
        "ok": str(integrated_result.get("evaluation_status") or "") == "ok",
        "status": str(integrated_result.get("evaluation_status") or ""),
        "error": "; ".join(errors),
        "changed_files": list(evidence_summary.get("changed_files") or []),
        "summary_path": str(evidence_summary.get("summary_path") or ""),
        "worker_summary_paths": list(evidence_summary.get("worker_summary_paths") or []),
        "worker_statuses": list(evidence_summary.get("worker_statuses") or []),
        "ownership_drift": list(integrated_result.get("ownership_drift") or []),
        "semantically_failed_workers": list(integrated_result.get("semantically_failed_workers") or []),
        "semantically_partial_workers": list(integrated_result.get("semantically_partial_workers") or []),
        "off_target_paths": list(integrated_result.get("off_target_paths") or []),
        "token_usage": dict(evidence_summary.get("token_usage") or {}),
        "integrated_result": integrated_result,
    }


def _slugify_label(text: str, *, limit: int = 32) -> str:
    value = re.sub(r"[^A-Za-z0-9]+", "-", str(text or "").strip().lower()).strip("-")
    while "--" in value:
        value = value.replace("--", "-")
    return value[:limit].strip("-") or "job"


def _job_id_from_ledger_uid(uid: str, title: str) -> str:
    suffix = str(uid or "").rsplit("-", 1)[-1].lower()
    date_part = ""
    m = re.match(r"^PIS-(\d{8})-", str(uid or ""))
    if m:
        date_part = m.group(1)
    pieces = ["pis", suffix]
    if date_part:
        pieces.append(date_part)
    pieces.append(_slugify_label(title))
    raw = "_".join([part for part in pieces if part])[:78].rstrip("._-")
    if not JOB_ID_RE.match(raw):
        raw = f"pis_{suffix}_{date_part or 'job'}"
    return raw


def _continuation_job_id(base_job_id: str, suffix: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "_", str(base_job_id or "").strip()).strip("._-")
    tail = _slugify_label(suffix, limit=18).replace("-", "_")
    raw = f"{base}_{tail}"[:79].rstrip("._-")
    if JOB_ID_RE.match(raw):
        return raw
    fallback = f"job_{tail}"[:79].rstrip("._-")
    return fallback if JOB_ID_RE.match(fallback) else "job_continuation"


def _queue_child_shape_for_mode(child_mode: str) -> str:
    normalized = str(child_mode or "").strip().lower()
    if normalized in {"", "implementation", "single_turn", "single", "generalist"}:
        return "verification_loop_job"
    if normalized in {"build_verify_loop", "build_verify", "builder_verifier_loop", "loop"}:
        return "verification_loop_job"
    if normalized in {"explicit_pass_chain_v1", "explicit_pass_chain", "build_or_improve_chain"}:
        return "verification_loop_job"
    if normalized in {"swarm_launch", "fanout", "fanout_job"}:
        return "fanout_job"
    if normalized in {"campaign", "campaign_job"}:
        return "campaign_job"
    if normalized in {"delegation_tree", "delegation_tree_job", "recursive"}:
        return "delegation_tree_job"
    raise ValueError(
        "queue_child_implementation_jobs.child_mode must be one of: "
        "implementation, single_turn, build_verify_loop, explicit_pass_chain_v1, swarm_launch, campaign_job, delegation_tree_job"
    )


def _assert_legal_queue_child_mode(child_mode: str) -> str:
    child_shape = _queue_child_shape_for_mode(child_mode)
    parent_shape = _job_shape_relationship_contract.parent_shape_for_job_kind("swarm_launch")
    try:
        _job_shape_relationship_contract.assert_legal_parent_child_relationship(parent_shape, child_shape)
    except (KeyError, ValueError) as exc:
        raise ValueError(
            f"queue_child_implementation_jobs.child_mode={str(child_mode or '').strip() or 'implementation'} "
            f"resolves to illegal parent-child job-shape relationship for {parent_shape}: {child_shape}"
        ) from exc
    return child_shape


def _load_prompt_template_from_markdown(*, heading: str, repo_root: Optional[Path] = None) -> str:
    path = (Path(repo_root or _repo_root()).resolve() / LAB_TASK_PROMPT_PATH).resolve()
    text = path.read_text(encoding="utf-8")
    pattern = rf"^##\s+\d+\.\s+{re.escape(heading)}\s*$"
    match = re.search(pattern, text, flags=re.MULTILINE)
    if not match:
        raise ValueError(f"prompt template heading not found: {heading}")
    remainder = text[match.end() :]
    fence_match = re.search(r"```text\n(.*?)\n```", remainder, flags=re.DOTALL)
    if not fence_match:
        raise ValueError(f"prompt template body not found: {heading}")
    return str(fence_match.group(1) or "").strip()


def _implementation_prompt_for_child(*, uid: str, title: str, pis_rel: str, status: str, repo_root: Optional[Path] = None) -> str:
    prompt = _load_prompt_template_from_markdown(heading="Implement One Child PIS", repo_root=repo_root)
    prompt = prompt.replace("<CHILD_UID>", uid).replace("<TITLE>", title)
    return (
        f"{prompt}\n\n"
        "Ledger binding:\n"
        f"- ledger_uid: {uid}\n"
        f"- title: {title}\n"
        f"- pis_path: {pis_rel}\n"
        f"- current_ledger_status: {status}\n"
    )


def _iter_parent_child_rows(*, parent_uid: str, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    root = Path(repo_root or _repo_root()).resolve()
    parent_dir = _resolve_ledger_item_dir_for_uid(parent_uid, repo_root=root)
    parent_meta = _read_json((parent_dir / "meta.json").resolve()) if parent_dir is not None else {}
    integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    canonical_child_uids = [str(item or "").strip() for item in list(integration.get("child_uids") or []) if str(item or "").strip()]
    canonical_uid_set = set(canonical_child_uids)
    rows: List[Dict[str, Any]] = []
    ledger_root = _ledger_storage.ledger_patch_instruction_sheets_root(root)
    item_dirs = _ledger_storage.list_item_dirs(ledger_root)
    for item_dir in item_dirs:
        meta_path = (item_dir / "meta.json").resolve()
        pis_path = (item_dir / "pis.md").resolve()
        events_path = (item_dir / "events.jsonl").resolve()
        if not meta_path.exists() or not pis_path.exists() or not events_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
        child_uid = str(meta.get("uid") or item_dir.name.split("__", 1)[0]).strip()
        title = str(meta.get("title") or child_uid).strip() or child_uid
        status = str(meta.get("status") or "draft").strip() or "draft"
        relationship = ""
        critical_path_rank = 0
        fanout_role = ""
        depends_on: List[str] = []
        matched = False
        for raw in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                event = json.loads(raw)
            except Exception:
                continue
            if str(event.get("type") or "") != "decomposed_from_parent":
                continue
            payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
            if str(payload.get("parent_uid") or "").strip() != parent_uid:
                continue
            relationship = str(payload.get("relationship") or "").strip()
            try:
                critical_path_rank = int(payload.get("critical_path_rank") or 0)
            except Exception:
                critical_path_rank = 0
            fanout_role = str(payload.get("fanout_role") or "").strip()
            depends_on = [str(x).strip() for x in list(payload.get("depends_on") or []) if str(x).strip()]
            matched = True
            break
        if not matched:
            continue
        if canonical_uid_set and child_uid not in canonical_uid_set:
            continue
        if not depends_on:
            depends_on = _pis_depends_on_uids_from_markdown(pis_path)
        rows.append(
            {
                "uid": child_uid,
                "title": title,
                "status": status,
                "relationship": relationship,
                "critical_path_rank": critical_path_rank,
                "depends_on_uids": depends_on,
                "fanout_role": fanout_role,
                "pis_rel": str(pis_path.relative_to(root)).replace("\\", "/"),
            }
        )
    if rows:
        rows_by_uid = {str(row.get("uid") or "").strip(): row for row in rows if str(row.get("uid") or "").strip()}
        if canonical_child_uids:
            ordered = [rows_by_uid[child_uid] for child_uid in canonical_child_uids if child_uid in rows_by_uid]
            if ordered:
                return ordered
        rows.sort(key=lambda row: (int(row.get("critical_path_rank") or 0), str(row.get("uid") or "")))
        return rows

    fallback_child_uids = list(canonical_child_uids)
    if not fallback_child_uids:
        return rows

    for index, child_uid in enumerate(fallback_child_uids, start=1):
        item_dir = _resolve_ledger_item_dir_for_uid(child_uid, repo_root=root)
        if item_dir is None:
            continue
        meta_path = (item_dir / "meta.json").resolve()
        pis_path = (item_dir / "pis.md").resolve()
        if not meta_path.exists() or not pis_path.exists():
            continue
        meta = _read_json(meta_path)
        title = str(meta.get("title") or child_uid).strip() or child_uid
        status = str(meta.get("status") or "draft").strip() or "draft"
        child_integration = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
        relationship = str(child_integration.get("relationship") or "").strip()
        fanout_role = str(child_integration.get("fanout_role") or "").strip()
        critical_path_rank = int(child_integration.get("sequence_index") or index)
        depends_on = _pis_depends_on_uids_from_markdown(pis_path)
        rows.append(
            {
                "uid": child_uid,
                "title": title,
                "status": status,
                "relationship": relationship,
                "critical_path_rank": critical_path_rank,
                "depends_on_uids": depends_on,
                "fanout_role": fanout_role,
                "pis_rel": str(pis_path.relative_to(root)).replace("\\", "/"),
            }
        )
    rows.sort(key=lambda row: (int(row.get("critical_path_rank") or 0), str(row.get("uid") or "")))
    return rows


def _pis_depends_on_uids_from_markdown(pis_path: Path) -> List[str]:
    try:
        text = pis_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return []
    marker = "Depends on:"
    if marker not in text:
        return []
    after = text.split(marker, 1)[1]
    lines: List[str] = []
    for raw in after.splitlines():
        if raw.startswith("Boundary /") or raw.startswith("## "):
            break
        lines.append(raw)
    out: List[str] = []
    for raw in lines:
        line = raw.strip()
        if not line.startswith("-"):
            continue
        for match in re.findall(r"PIS-[A-Z0-9-]+", line):
            uid = str(match).strip()
            if uid and uid not in out:
                out.append(uid)
    return out


def _resolve_ledger_item_dir_for_uid(uid: str, *, repo_root: Optional[Path] = None) -> Optional[Path]:
    normalized = str(uid or "").strip()
    if not normalized:
        return None
    ledger_root = _ledger_storage.ledger_patch_instruction_sheets_root(Path(repo_root or _repo_root()).resolve())
    return _ledger_storage.item_dir_for_uid(ledger_root, normalized)


def _ledger_status_for_uid(uid: str, *, repo_root: Optional[Path] = None) -> str:
    item_dir = _resolve_ledger_item_dir_for_uid(uid, repo_root=repo_root)
    if item_dir is None:
        return ""
    meta = _read_json((item_dir / "meta.json").resolve()) or {}
    return str(meta.get("status") or "").strip().lower()


def _normalize_intent_family_tail_contract(
    parent_uid: str,
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    from infra.controlplane import intent_family_v2_workflow_contract as _intent_family_v2_workflow_contract

    root = Path(repo_root or _repo_root()).resolve()
    parent_dir = _resolve_ledger_item_dir_for_uid(parent_uid, repo_root=root)
    if parent_dir is None:
        return {"updated": False, "reason": "missing_parent", "child_rows": []}
    parent_meta = _read_json((parent_dir / "meta.json").resolve()) or {}
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    source_intent_uid = str(parent_integration.get("intent_sheet_uid") or "").strip()
    if not source_intent_uid:
        return {"updated": False, "reason": "not_intent_derived", "child_rows": _iter_parent_child_rows(parent_uid=parent_uid, repo_root=root)}
    child_rows = _iter_parent_child_rows(parent_uid=parent_uid, repo_root=root)
    if len(child_rows) < 2:
        return {"updated": False, "reason": "insufficient_children", "child_rows": child_rows}

    def _write_parent_family_projection(projected_rows: List[Dict[str, Any]], *, changed_reason: str) -> bool:
        normalized_child_uids = [str(row.get("uid") or "").strip() for row in projected_rows if str(row.get("uid") or "").strip()]
        if not normalized_child_uids:
            return False
        implementation_rows = [
            row for row in projected_rows
            if str(row.get("relationship") or "").strip() == "execution_child"
        ]
        validation_row = next(
            (row for row in projected_rows if str(row.get("relationship") or "").strip() == _intent_family_v2_workflow_contract.VALIDATION_CHILD_KIND),
            {},
        )
        closeout_row = next(
            (row for row in projected_rows if str(row.get("relationship") or "").strip() == _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND),
            {},
        )
        updated_integration = dict(parent_integration)
        updated_integration["child_uids"] = list(normalized_child_uids)
        updated_integration["recommended_execution_order"] = list(normalized_child_uids)
        updated_integration["first_child_uid"] = normalized_child_uids[0]
        updated_integration["recommended_first_child_uid"] = (
            str(implementation_rows[0].get("uid") or normalized_child_uids[0]).strip() if implementation_rows else normalized_child_uids[0]
        )
        updated_integration["implementation_child_uids"] = [
            str(row.get("uid") or "").strip() for row in implementation_rows if str(row.get("uid") or "").strip()
        ]
        updated_integration["intent_validation_child_uid"] = str(validation_row.get("uid") or "").strip()
        updated_integration["required_final_child_uid"] = str(closeout_row.get("uid") or "").strip()
        updated_integration["required_final_child_kind"] = (
            _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND
            if str(closeout_row.get("uid") or "").strip()
            else str(updated_integration.get("required_final_child_kind") or "").strip()
        )
        changed = (
            updated_integration != parent_integration
            or list(parent_meta.get("child_pis_uids") or []) != normalized_child_uids
        )
        if not changed:
            return False
        parent_meta["integration_meta"] = updated_integration
        parent_meta["child_pis_uids"] = list(normalized_child_uids)
        parent_meta["updated_utc"] = _utc_iso()
        (parent_dir / "meta.json").write_text(
            json.dumps(parent_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return True

    def _rewrite_child_relationships(updates: Dict[str, str], *, reason: str) -> Dict[str, Any]:
        changed = False
        for child_uid, relationship in updates.items():
            child_dir = _resolve_ledger_item_dir_for_uid(child_uid, repo_root=root)
            if child_dir is None:
                continue
            meta_path = (child_dir / "meta.json").resolve()
            child_meta = _read_json(meta_path) or {}
            child_integration = (
                child_meta.get("integration_meta") if isinstance(child_meta.get("integration_meta"), dict) else {}
            )
            if str(child_integration.get("relationship") or "").strip() != relationship:
                child_integration = dict(child_integration)
                child_integration["relationship"] = relationship
                child_meta["integration_meta"] = child_integration
                child_meta["updated_utc"] = _utc_iso()
                meta_path.write_text(
                    json.dumps(child_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                changed = True
            events_path = (child_dir / "events.jsonl").resolve()
            if events_path.exists():
                rewritten: List[str] = []
                event_changed = False
                for raw in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
                    line = raw.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        rewritten.append(raw)
                        continue
                    if str(event.get("type") or "") == "decomposed_from_parent":
                        payload = event.get("payload") if isinstance(event.get("payload"), dict) else {}
                        if (
                            str(payload.get("parent_uid") or "").strip() == parent_uid
                            and str(payload.get("relationship") or "").strip() != relationship
                        ):
                            payload = dict(payload)
                            payload["relationship"] = relationship
                            event["payload"] = payload
                            event_changed = True
                    rewritten.append(json.dumps(event, ensure_ascii=False, sort_keys=True))
                if event_changed:
                    events_path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")
                    changed = True
        return {
            "updated": changed,
            "reason": reason if changed else "already_normalized",
            "child_rows": _iter_parent_child_rows(parent_uid=parent_uid, repo_root=root),
        }

    child_relationships = {str(row.get("relationship") or "").strip() for row in child_rows}
    has_canonical_tail = (
        _intent_family_v2_workflow_contract.VALIDATION_CHILD_KIND in child_relationships
        and _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND in child_relationships
    )
    if has_canonical_tail:
        legacy_uids = {
            str(row.get("uid") or "").strip(): "execution_child"
            for row in child_rows
            if str(row.get("relationship") or "").strip() in {"proof_child", "closeout_child"}
            and str(row.get("uid") or "").strip()
        }
        if legacy_uids:
            return _rewrite_child_relationships(legacy_uids, reason="normalized_legacy_intermediate_child_kinds")
        projection_changed = _write_parent_family_projection(child_rows, changed_reason="already_normalized")
        return {
            "updated": projection_changed,
            "reason": "canonical_projection_refreshed" if projection_changed else "already_normalized",
            "child_rows": _iter_parent_child_rows(parent_uid=parent_uid, repo_root=root),
        }

    validation_uid = str(parent_integration.get("intent_validation_child_uid") or "").strip()
    closeout_uid = str(parent_integration.get("required_final_child_uid") or "").strip()
    if not validation_uid or not closeout_uid:
        return {"updated": False, "reason": "missing_explicit_tail_relationship_metadata", "child_rows": child_rows}
    rows_by_uid = {str(row.get("uid") or "").strip(): row for row in child_rows if str(row.get("uid") or "").strip()}
    validation_row = rows_by_uid.get(validation_uid)
    closeout_row = rows_by_uid.get(closeout_uid)
    if validation_row is None or closeout_row is None:
        return {"updated": False, "reason": "missing_explicit_tail_rows", "child_rows": child_rows}
    if validation_uid == closeout_uid:
        return {"updated": False, "reason": "invalid_explicit_tail_relationship_metadata", "child_rows": child_rows}

    updates = {
        validation_uid: _intent_family_v2_workflow_contract.VALIDATION_CHILD_KIND,
        closeout_uid: _intent_family_v2_workflow_contract.REQUIRED_FINAL_CHILD_KIND,
    }
    rewrite_result = _rewrite_child_relationships(updates, reason="normalized_old_tail_shape")

    projection_changed = _write_parent_family_projection(child_rows, changed_reason="normalized_old_tail_shape")
    return {
        "updated": bool(rewrite_result.get("updated")) or projection_changed,
        "reason": "normalized_old_tail_shape",
        "child_rows": _iter_parent_child_rows(parent_uid=parent_uid, repo_root=root),
    }


def _family_closeout_job_id(parent_uid: str) -> str:
    suffix = str(parent_uid or "").rsplit("-", 1)[-1].lower()
    m = __import__("re").match(r"^PIS-(\d{8})-", str(parent_uid or ""))
    date_part = m.group(1) if m else "job"
    return f"close_{suffix}_{date_part}_execution_family"


def _execution_family_closeout_prompt(
    *,
    parent_uid: str,
    parent_title: str,
    source_intent_uid: str,
    required_final_child_uid: str,
    child_uids: List[str],
    child_job_ids: List[str],
    trust_hardening_mode: str = "",
) -> str:
    normalized_trust_hardening_mode = str(trust_hardening_mode or "").strip()
    lines = [
        f"Audit, integrate, and close the execution family rooted at `{parent_uid}` only when the durable child-job evidence honestly supports closure.",
        "",
        "Instructions:",
        "1. Read the bound parent PIS and inspect the whole declared child family, not just one child at a time.",
        "2. Review the latest successful scheduled-job receipts/reports for the listed child jobs, especially their durable changed-file evidence and validation commands.",
        "3. Audit whether the family is actually integrated end to end; if residual integration gaps remain within the family-touched repo paths, fix them before closeout.",
        "4. Re-run validation from current repo state before closure, not just receipt archaeology.",
        "5. Promote each listed child PIS to `verified` only if the child itself earned that status from durable evidence after any needed family integration repair.",
        "6. Promote the final closeout child, the bound parent PIS, and the source intent sheet as part of this closeout job when the whole child family is complete and the required final child passed.",
        "7. If a source intent sheet is listed, promote it to `verified` only after its validating PIS requirements are satisfied; do not leave intent promotion as a later human/manual follow-up when it is earned.",
        "8. Write durable closure evidence/artifacts/events for the family items you update.",
        "9. Include a parseable `Validation Commands Run:` section covering the actual proof used for closeout.",
        "",
        "You may mutate only the declared family ledger records plus repo files already implicated by the successful child receipts for this family.",
        "Do not widen scope to unrelated ledger families, unrelated scheduler cleanup, or broad repo hygiene.",
        "Judge the declared family only and fail honestly if any required closeout condition is not actually earned.",
        "",
        "Execution family contract:",
        f"- parent_uid: {parent_uid}",
        f"- parent_title: {parent_title}",
        f"- source_intent_uid: {source_intent_uid or '(none)'}",
        f"- required_final_child_uid: {required_final_child_uid or '(none)'}",
        f"- child_uids: {', '.join(child_uids) if child_uids else '(none)'}",
        f"- child_job_ids: {', '.join(child_job_ids) if child_job_ids else '(none)'}",
    ]
    if normalized_trust_hardening_mode == "broad_current_state_rerun_v1":
        lines[5:5] = [
            "4a. In trust-hardening mode, rerun a broader current-state validation slice that exercises the family from the repaired repo state, not only the final child’s historical selectors.",
            "4b. Compare that broader rerun against the receipt-derived family proof and record any delta in findings, confidence, or closure status.",
        ]
        lines.append(f"- trust_hardening_mode: {normalized_trust_hardening_mode}")
    return "\n".join(lines).strip()


def _compiled_ir_execution_family_closeout_repo_paths_from_receipts(
    child_job_ids: List[str],
    *,
    repo_root: Optional[Path] = None,
) -> List[str]:
    out: List[str] = []
    for job_id in [str(item).strip() for item in list(child_job_ids or []) if str(item).strip()]:
        try:
            state = load_state(job_id, repo_root=repo_root)
        except Exception:
            state = {}
        if not _is_success_status(state.get("last_status")):
            continue
        receipt_path = Path(str(state.get("last_receipt_path") or "").strip())
        if not receipt_path.exists():
            continue
        receipt = _read_json(receipt_path)
        repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
        for raw in list(repo_residue.get("declared_changed_files") or []):
            path = str(raw or "").strip().replace("\\", "/").lstrip("./")
            if path and path not in out:
                out.append(path)
    return out


def _queue_execution_family_closeout_job_after_pass(
    spec: Dict[str, Any],
    *,
    parent_uid: str,
    child_rows: List[Dict[str, Any]],
    child_job_ids: List[str],
    run_at_utc: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    def _retire_existing_closeout(*, reason: str) -> Dict[str, Any]:
        if not closeout_path.exists():
            return {"retired": False, "reason": "job_missing"}
        existing = _read_json(closeout_path)
        state_path = _state_path(closeout_job_id, repo_root=repo_root)
        state = _read_json(state_path)
        if str(state.get("active_run_id") or "").strip():
            return {"retired": False, "reason": "active_run_present"}
        prompt_meta = existing.get("prompt_meta") if isinstance(existing.get("prompt_meta"), dict) else {}
        retired_meta = prompt_meta.get("retired_closeout") if isinstance(prompt_meta.get("retired_closeout"), dict) else {}
        prompt_meta["retired_closeout"] = {
            "reason": str(reason or "").strip() or "retired",
            "retired_utc": _utc_iso(),
        }
        existing["prompt_meta"] = prompt_meta
        existing["enabled"] = False
        save_job(existing, repo_root=repo_root)
        if state:
            state["run_now_requested_utc"] = ""
            state["run_now_request_run_id"] = ""
            state["run_now_request_source"] = ""
            state["last_nonlaunch_reason"] = f"retired_closeout:{str(reason or '').strip() or 'retired'}"
            state["updated_utc"] = _utc_iso()
            state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {
            "retired": True,
            "job_id": closeout_job_id,
            "reason": str(reason or "").strip() or "retired",
            "previous_reason": str(retired_meta.get("reason") or "").strip(),
        }

    parent_dir = _resolve_ledger_item_dir_for_uid(parent_uid, repo_root=repo_root)
    parent_meta = _read_json((parent_dir / "meta.json").resolve()) if parent_dir is not None else {}
    parent_title = str(parent_meta.get("title") or parent_uid).strip() or parent_uid
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    source_intent_uid = str(parent_integration.get("intent_sheet_uid") or "").strip()
    required_final_child_uid = str(parent_integration.get("required_final_child_uid") or "").strip()
    trust_hardening_mode = "broad_current_state_rerun_v1"
    closeout_job_id = _family_closeout_job_id(parent_uid)
    closeout_path = jobs_dir(repo_root) / f"{_safe_name(closeout_job_id)}.json"
    child_uids = [str(row.get("uid") or "").strip() for row in child_rows if str(row.get("uid") or "").strip()]
    if not child_uids or not list(child_job_ids or []):
        return {
            "created": False,
            "job_id": closeout_job_id,
            "reason": "missing_child_dependencies",
            "child_uids": list(child_uids),
            "child_job_ids": [str(x).strip() for x in list(child_job_ids or []) if str(x).strip()],
            "retired_existing": _retire_existing_closeout(reason="missing_child_dependencies"),
        }
    parent_status = str(parent_meta.get("status") or "").strip().lower()
    child_statuses = {_ledger_status_for_uid(child_uid, repo_root=repo_root) for child_uid in child_uids}
    if parent_status == "verified" and child_uids and child_statuses == {"verified"}:
        return {
            "created": False,
            "job_id": closeout_job_id,
            "reason": "family_already_verified",
            "retired_existing": _retire_existing_closeout(reason="family_already_verified"),
        }
    if required_final_child_uid:
        return {
            "created": False,
            "job_id": closeout_job_id,
            "reason": "final_child_owns_family_closeout",
            "required_final_child_uid": required_final_child_uid,
            "retired_existing": _retire_existing_closeout(reason="final_child_owns_family_closeout"),
        }
    existing_closeout = _read_json(closeout_path) if closeout_path.exists() else {}
    existing_meta = existing_closeout.get("prompt_meta") if isinstance(existing_closeout.get("prompt_meta"), dict) else {}
    existing_contract = (
        existing_meta.get("execution_family_closeout")
        if isinstance(existing_meta.get("execution_family_closeout"), dict)
        else {}
    )
    existing_child_uids = [str(x).strip() for x in list(existing_contract.get("child_uids") or []) if str(x).strip()]
    existing_child_job_ids = [str(x).strip() for x in list(existing_contract.get("child_job_ids") or []) if str(x).strip()]
    normalized_child_job_ids = [str(x).strip() for x in list(child_job_ids or []) if str(x).strip()]
    if existing_closeout and existing_child_uids == child_uids and existing_child_job_ids == normalized_child_job_ids:
        return {"created": False, "job_id": closeout_job_id, "reason": "job_exists"}
    allowed_repo_paths = _compiled_ir_execution_family_closeout_repo_paths_from_receipts(child_job_ids, repo_root=repo_root)
    allowed_ledger_paths: List[str] = [
        "governance/ledger/patch_instruction_sheets/uid_index.json",
        f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/meta.json",
        f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/events.jsonl",
        f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/artifacts/",
        "runtime/state/commit_journal/pending/",
    ]
    for child_uid in child_uids:
        allowed_ledger_paths.extend(
            [
                f"governance/ledger/patch_instruction_sheets/**/{child_uid}__*/meta.json",
                f"governance/ledger/patch_instruction_sheets/**/{child_uid}__*/events.jsonl",
                f"governance/ledger/patch_instruction_sheets/**/{child_uid}__*/artifacts/",
            ]
        )
    if source_intent_uid:
        allowed_ledger_paths.extend(
            [
                f"governance/ledger/intent_sheets/**/{source_intent_uid}__*/meta.json",
                f"governance/ledger/intent_sheets/**/{source_intent_uid}__*/events.jsonl",
                f"governance/ledger/intent_sheets/**/{source_intent_uid}__*/artifacts/",
            ]
        )

    builder_model = _runtime_policy.get_runtime_policy_text(
        "scheduler.child_implementation.default_model",
        _scheduler_default_model(job_kind=""),
    )
    verifier_model = _runtime_policy.get_runtime_policy_text(
        "scheduler.build_verify.default_model",
        _scheduler_default_model(job_kind="build_verify_loop"),
    )
    builder_runtime = {
        "agent_tech": str(_infer_agent_tech_from_model(builder_model) or "codex"),
        "model": builder_model,
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
    }
    verifier_runtime = {
        "agent_tech": str(_infer_agent_tech_from_model(verifier_model) or "codex"),
        "model": verifier_model,
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
    }
    builder_fallbacks = _runtime_policy_models("scheduler.child_implementation.default_fallback_models", "")
    verifier_fallbacks = _runtime_policy_models(
        "scheduler.build_verify.default_fallback_models",
        ",".join(_scheduler_default_fallback_models(job_kind="build_verify_loop")),
    )
    if builder_fallbacks:
        builder_runtime["fallback_models"] = list(builder_fallbacks)
    if verifier_fallbacks:
        verifier_runtime["fallback_models"] = list(verifier_fallbacks)

    prompt = _execution_family_closeout_prompt(
        parent_uid=parent_uid,
        parent_title=parent_title,
        source_intent_uid=source_intent_uid,
        required_final_child_uid=required_final_child_uid,
        child_uids=child_uids,
        child_job_ids=child_job_ids,
        trust_hardening_mode=trust_hardening_mode,
    )
    closeout_spec = {
        "job_id": closeout_job_id,
        "job_kind": "build_verify_loop",
        "enabled": True,
        "binding": {"type": "ledger_uid", "id": parent_uid},
        "depends_on": list(child_job_ids),
        "display_title": f"{parent_uid.split('-')[-1].lower()} execution family closeout",
        "prompt": prompt,
        "context_refs": [],
        "expected_output": {"kind": "report", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": run_at_utc, "timezone": "UTC"},
        "policy": {
            "max_runtime_seconds": 1800,
            "repo_change_mode": "strict",
            "require_clean_worktree": False,
            "require_commit_journal": True,
            "require_validation_evidence": True,
            "post_success_commit_tag": True,
            "agent_turn_retries": 0,
            "job_transient_retries": 0,
        },
        "runtime": _normalize_runtime(dict(verifier_runtime), job_kind="build_verify_loop"),
        "required_context_bundle": "lab_core_v1",
        "loop": {
            "builder_role": "builder",
            "verifier_role": "verifier",
            "max_iterations": 1,
            "pass_marker": "VERDICT: PASS",
            "builder_runtime": _normalize_runtime(dict(builder_runtime), job_kind="build_verify_loop"),
            "verifier_runtime": _normalize_runtime(dict(verifier_runtime), job_kind="build_verify_loop"),
        },
        "prompt_meta": {
            "generated_from_parent_decomposition_job": str(spec.get("job_id") or ""),
            "generated_from_parent_uid": parent_uid,
            "execution_family_closeout": {
                "contract_kind": "execution_family_closeout_v1",
                "parent_uid": parent_uid,
                "source_intent_uid": source_intent_uid,
                "required_final_child_uid": required_final_child_uid,
                "child_uids": list(child_uids),
                "child_job_ids": list(child_job_ids),
                "integration_audit_mode": "family_receipt_changed_files_v1",
                "trust_hardening_mode": trust_hardening_mode,
                "allowed_repo_paths": list(allowed_repo_paths),
                "allowed_ledger_paths": list(allowed_ledger_paths),
                "closeout_rules": [
                    "required_final_child_must_be_verified",
                    "parent_may_close_only_after_all_children_verified",
                    "intent_may_close_only_after_validating_pis_verified",
                    "family_closeout_must_emit_closure_artifact",
                    "broad_current_state_rerun_required_before_final_closure",
                    "machine_closeout_must_promote_final_child_parent_and_source_intent_when_earned",
                ],
                "status_promotion_policy": "verified_only_after_family_audit_and_required_final_child",
                "receipt_selection_policy": "latest_successful_child_receipts_only",
            },
        },
    }
    saved = save_job(closeout_spec, repo_root=repo_root)
    return {
        "created": True,
        "job_id": str(saved.get("job_id") or closeout_job_id),
        "depends_on": list(child_job_ids),
        "regenerated": bool(existing_closeout),
    }


def _queue_child_implementation_jobs_after_pass(
    spec: Dict[str, Any],
    *,
    finished_utc: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    def _child_spec_for_row(
        row: Dict[str, Any],
        *,
        child_uid: str,
        job_id: str,
        depends_on_jobs: List[str],
    ) -> Dict[str, Any]:
        child_spec = {
            "job_id": job_id,
            "job_kind": "single_turn",
            "enabled": True,
            "role": "generalist",
            "binding": {"type": "ledger_uid", "id": child_uid},
            "depends_on": depends_on_jobs,
            "display_title": f"{child_uid.split('-')[-1].lower()} {str(row.get('title') or '')[:80]}",
            "prompt": _implementation_prompt_for_child(
                uid=child_uid,
                title=str(row.get("title") or child_uid),
                pis_rel=str(row.get("pis_rel") or ""),
                status=str(row.get("status") or "draft"),
                repo_root=repo_root,
            ),
            "context_refs": [str(row.get("pis_rel") or "")],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": run_at_utc, "timezone": "UTC"},
            "policy": {"max_runtime_seconds": 1800},
            "runtime": dict(child_runtime),
            "required_context_bundle": "lab_core_v1",
            "prompt_meta": {
                "generated_from_parent_decomposition_job": str(spec.get("job_id") or ""),
                "generated_from_parent_uid": parent_uid,
                "generated_child_mode": "build_verify_loop" if child_build_verify else "implementation",
                "execution_family_contract": {
                    "contract_kind": "intent_derived_execution_family_v1" if source_intent_uid else "execution_family_v1",
                    "source_intent_uid": source_intent_uid,
                    "parent_uid": parent_uid,
                    "child_uid": child_uid,
                    "child_kind": str(row.get("relationship") or "").strip() or "execution_child",
                    "critical_path_rank": int(row.get("critical_path_rank") or 0),
                    "required_final_child_kind": required_final_child_kind,
                    "required_final_child_uid": required_final_child_uid,
                },
            },
        }
        if child_build_verify:
            child_spec["job_kind"] = "build_verify_loop"
            child_spec.pop("role", None)
            child_spec["policy"] = {
                "max_runtime_seconds": 1800,
                "post_success_commit_tag": child_post_success_commit_tag,
            }
            child_verifier_runtime["compiled_execution_mode"] = "soft_gate"
            child_runtime["compiled_execution_mode"] = "soft_gate"
            child_spec["runtime"] = _normalize_runtime(dict(child_verifier_runtime), job_kind="build_verify_loop")
            child_spec["loop"] = {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": child_loop_max_iterations,
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": _normalize_runtime(dict(child_runtime), job_kind="build_verify_loop"),
                "verifier_runtime": _normalize_runtime(dict(child_verifier_runtime), job_kind="build_verify_loop"),
            }
        return child_spec

    def _save_or_skip_child_spec(
        row: Dict[str, Any],
        *,
        child_uid: str,
        child_spec: Dict[str, Any],
        depends_on_jobs: List[str],
    ) -> str:
        job_id = str(child_spec.get("job_id") or "")
        job_path = jobs_dir(repo_root) / f"{_safe_name(job_id)}.json"
        if job_path.exists():
            existing = _read_json(job_path)
            existing_depends = [str(item).strip() for item in list(existing.get("depends_on") or []) if str(item).strip()]
            existing_binding = str((existing.get("binding") or {}).get("id") or "").strip()
            existing_prompt_meta = existing.get("prompt_meta") if isinstance(existing.get("prompt_meta"), dict) else {}
            existing_execution_family_contract = (
                existing_prompt_meta.get("execution_family_contract")
                if isinstance(existing_prompt_meta.get("execution_family_contract"), dict)
                else {}
            )
            planned_execution_family_contract = (
                child_spec.get("prompt_meta", {}).get("execution_family_contract")
                if isinstance(child_spec.get("prompt_meta"), dict)
                else {}
            )
            if (
                existing_depends == depends_on_jobs
                and existing_binding == child_uid
                and existing_execution_family_contract == planned_execution_family_contract
            ):
                skipped.append({"uid": child_uid, "job_id": job_id, "reason": "job_exists"})
                return job_id
        saved = save_job(child_spec, repo_root=repo_root)
        saved_job_id = str(saved.get("job_id") or job_id)
        reason = "job_updated" if job_path.exists() else "job_created"
        created.append({"uid": child_uid, "job_id": saved_job_id, "depends_on": depends_on_jobs, "run_at": run_at_utc, "reason": reason})
        return saved_job_id

    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    queue_cfg = prompt_meta.get("queue_child_implementation_jobs") if isinstance(prompt_meta.get("queue_child_implementation_jobs"), dict) else {}
    if not bool(queue_cfg.get("enabled")):
        return {"enabled": False, "created_count": 0, "skipped_count": 0, "children": []}
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    if str(binding.get("type") or "") != "ledger_uid":
        return {"enabled": True, "created_count": 0, "skipped_count": 0, "children": [], "error": "binding_not_ledger_uid"}
    parent_uid = str(binding.get("id") or "").strip()
    if not parent_uid:
        return {"enabled": True, "created_count": 0, "skipped_count": 0, "children": [], "error": "missing_parent_uid"}
    delay_minutes = max(0, int(queue_cfg.get("run_after_minutes") or 5))
    run_at_utc = _utc_iso((_parse_utc(finished_utc).astimezone(_dt.timezone.utc) + _dt.timedelta(minutes=delay_minutes)).replace(microsecond=0))
    normalized_family = _normalize_intent_family_tail_contract(parent_uid=parent_uid, repo_root=repo_root)
    child_rows = list(normalized_family.get("child_rows") or [])
    if not child_rows:
        child_rows = _iter_parent_child_rows(parent_uid=parent_uid, repo_root=repo_root)
    child_relationships = {str(row.get("relationship") or "").strip() for row in child_rows}
    existing_jobs = list_jobs(repo_root=repo_root)
    binding_to_job: Dict[str, str] = {}
    for row in existing_jobs:
        row_binding = row.get("binding") if isinstance(row.get("binding"), dict) else {}
        if str(row_binding.get("type") or "") == "ledger_uid":
            binding_to_job[str(row_binding.get("id") or "").strip()] = str(row.get("job_id") or "")
    planned_job_ids = {str(row.get("uid") or ""): _job_id_from_ledger_uid(str(row.get("uid") or ""), str(row.get("title") or "")) for row in child_rows}
    created: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []
    child_default_model = _runtime_policy.get_runtime_policy_text(
        "scheduler.child_implementation.default_model",
        _scheduler_default_model(job_kind=""),
    )
    child_fallback_models = _runtime_policy_models("scheduler.child_implementation.default_fallback_models", "")
    child_runtime = {
        "agent_tech": str(_infer_agent_tech_from_model(child_default_model) or "codex"),
        "model": child_default_model,
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
    }
    if child_fallback_models:
        child_runtime["fallback_models"] = list(child_fallback_models)
    child_mode = str(queue_cfg.get("child_mode") or "implementation").strip().lower()
    _assert_legal_queue_child_mode(child_mode)
    child_build_verify = child_mode in {"build_verify_loop", "build_verify", "builder_verifier_loop", "loop"}
    child_explicit_pass_chain = child_mode in {"explicit_pass_chain_v1", "explicit_pass_chain", "build_or_improve_chain"}
    child_verifier_model = _runtime_policy.get_runtime_policy_text(
        "scheduler.build_verify.default_model",
        _scheduler_default_model(job_kind="build_verify_loop"),
    )
    child_verifier_fallback_models = _runtime_policy_models(
        "scheduler.build_verify.default_fallback_models",
        ",".join(_scheduler_default_fallback_models(job_kind="build_verify_loop")),
    )
    child_verifier_runtime = {
        "agent_tech": str(_infer_agent_tech_from_model(child_verifier_model) or "codex"),
        "model": child_verifier_model,
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
    }
    if child_verifier_fallback_models:
        child_verifier_runtime["fallback_models"] = list(child_verifier_fallback_models)
    child_loop_max_iterations = max(1, min(10, int(queue_cfg.get("max_iterations") or 5)))
    child_post_success_commit_tag = bool(queue_cfg.get("post_success_commit_tag", True))
    parent_dir = _resolve_ledger_item_dir_for_uid(parent_uid, repo_root=repo_root)
    parent_meta = _read_json((parent_dir / "meta.json").resolve()) if parent_dir is not None else {}
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    decomposition_readiness = (
        parent_integration.get("decomposition_readiness")
        if isinstance(parent_integration.get("decomposition_readiness"), dict)
        else {}
    )
    readiness_state = str(decomposition_readiness.get("state") or "").strip().lower()
    source_intent_uid = str(parent_integration.get("intent_sheet_uid") or "").strip()
    canonical_intent_family = bool(source_intent_uid) and {
        "execution_child",
        "intent_validation_suite",
        "intent_family_closeout",
    }.issubset(child_relationships)
    required_final_child_uid = str(parent_integration.get("required_final_child_uid") or "").strip()
    required_final_child_kind = str(parent_integration.get("required_final_child_kind") or "").strip()
    closeout_rows = [
        row for row in child_rows if str(row.get("relationship") or "").strip() == "intent_family_closeout"
    ]
    if closeout_rows:
        canonical_closeout = closeout_rows[-1]
        canonical_uid = str(canonical_closeout.get("uid") or "").strip()
        canonical_kind = str(canonical_closeout.get("relationship") or "").strip()
        if canonical_uid and (
            required_final_child_uid != canonical_uid or required_final_child_kind != canonical_kind
        ):
            required_final_child_uid = canonical_uid
            required_final_child_kind = canonical_kind
            parent_integration = dict(parent_integration)
            parent_integration["required_final_child_uid"] = required_final_child_uid
            parent_integration["required_final_child_kind"] = required_final_child_kind
            parent_meta = dict(parent_meta)
            parent_meta["integration_meta"] = parent_integration
            parent_meta["updated_utc"] = _utc_iso()
            (parent_dir / "meta.json").write_text(
                json.dumps(parent_meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    elif required_final_child_uid and not required_final_child_kind:
        for row in child_rows:
            if str(row.get("uid") or "").strip() == required_final_child_uid:
                required_final_child_kind = str(row.get("relationship") or "").strip()
                break
    non_executable_relationships = {
        "clarification_child",
        "discovery_child",
        "requirements_clarification",
        "clarification_only",
        "non_executable_support",
    }
    executable_child_rows = [
        row for row in child_rows if str(row.get("relationship") or "").strip().lower() not in non_executable_relationships
    ]
    clarification_child_rows = [
        row for row in child_rows if str(row.get("relationship") or "").strip().lower() in non_executable_relationships
    ]
    if readiness_state in {"clarification_needed", "not_ready", "blocked"}:
        for row in clarification_child_rows:
            child_uid = str(row.get("uid") or "")
            job_id = planned_job_ids.get(child_uid) or _job_id_from_ledger_uid(child_uid, str(row.get("title") or child_uid))
            depends_on_jobs: List[str] = []
            child_spec = _child_spec_for_row(
                row,
                child_uid=child_uid,
                job_id=job_id,
                depends_on_jobs=depends_on_jobs,
            )
            binding_to_job[child_uid] = _save_or_skip_child_spec(
                row,
                child_uid=child_uid,
                child_spec=child_spec,
                depends_on_jobs=depends_on_jobs,
            )
        followup_job: Dict[str, Any] = {}
        clarification_job_ids = [
            str(binding_to_job.get(str(row.get("uid") or "")) or "").strip()
            for row in clarification_child_rows
            if str(binding_to_job.get(str(row.get("uid") or "")) or "").strip()
        ]
        if clarification_job_ids:
            followup_spec = dict(spec)
            followup_prompt_meta = dict(followup_spec.get("prompt_meta") or {})
            followup_continuation = dict(followup_prompt_meta.get("autonomous_continuation") or {})
            followup_continuation.update(
                {
                    "kind": "clarification_followup_parent_decomposition",
                    "parent_uid": parent_uid,
                    "generated_from_parent_job": str(spec.get("job_id") or ""),
                }
            )
            followup_prompt_meta["autonomous_continuation"] = followup_continuation
            followup_spec["prompt_meta"] = followup_prompt_meta
            followup_spec["job_id"] = _continuation_job_id(str(spec.get("job_id") or _job_id_from_ledger_uid(parent_uid, str(parent_meta.get("title") or parent_uid))), "clarify_followup")
            followup_spec["depends_on"] = list(clarification_job_ids)
            followup_spec["schedule"] = {"mode": "once", "run_at": run_at_utc, "timezone": "UTC"}
            followup_path = jobs_dir(repo_root) / f"{_safe_name(str(followup_spec.get('job_id') or ''))}.json"
            if followup_path.exists():
                existing_followup = _read_json(followup_path)
                if (
                    [str(item).strip() for item in list(existing_followup.get("depends_on") or []) if str(item).strip()]
                    == list(clarification_job_ids)
                ):
                    followup_job = {
                        "job_id": str(existing_followup.get("job_id") or followup_spec.get("job_id") or ""),
                        "created": False,
                        "reason": "job_exists",
                    }
                else:
                    saved_followup = save_job(followup_spec, repo_root=repo_root)
                    followup_job = {
                        "job_id": str(saved_followup.get("job_id") or followup_spec.get("job_id") or ""),
                        "created": True,
                        "reason": "job_updated",
                    }
            else:
                saved_followup = save_job(followup_spec, repo_root=repo_root)
                followup_job = {
                    "job_id": str(saved_followup.get("job_id") or followup_spec.get("job_id") or ""),
                    "created": True,
                    "reason": "job_created",
                }
        next_ready_job_id = clarification_job_ids[0] if clarification_job_ids else ""
        return {
            "enabled": True,
            "created_count": len(created),
            "skipped_count": len(skipped),
            "run_at_utc": run_at_utc,
            "children": created,
            "skipped": skipped,
            "queue_blocked_reason": readiness_state,
            "readiness_summary": str(decomposition_readiness.get("summary") or "").strip(),
            "missing_details": [
                str(x).strip() for x in list(decomposition_readiness.get("missing_details") or []) if str(x).strip()
            ],
            "clarification_continuation": {
                "enabled": bool(clarification_job_ids),
                "clarification_job_ids": clarification_job_ids,
                "parent_followup_job": followup_job,
            },
            "next_ready_job_id": next_ready_job_id,
        }
    compilation_meta: Dict[str, Any] = {}
    if canonical_intent_family and child_explicit_pass_chain:
        from infra.controlplane import explicit_pass_chain_compiler as _explicit_pass_chain_compiler

        compiled = _explicit_pass_chain_compiler.compile_explicit_pass_chain(
            parent_uid=parent_uid,
            run_at_utc=run_at_utc,
            builder_model=child_default_model,
            verifier_model=child_verifier_model,
            pass_count=child_loop_max_iterations,
            repo_root=Path(repo_root or _repo_root()).resolve(),
        )
        family_graph = compiled.get("family_graph") if isinstance(compiled.get("family_graph"), dict) else {}
        compilation_meta = {
            "schema_version": str(compiled.get("schema_version") or ""),
            "workflow_definition_version": str(compiled.get("workflow_definition_version") or ""),
            "workflow_kind": str(compiled.get("workflow_kind") or ""),
            "execution_mode": str(compiled.get("execution_mode") or ""),
            "compilation_fingerprint": str(compiled.get("compilation_fingerprint") or ""),
            "family_graph_fingerprint": str(family_graph.get("graph_fingerprint") or ""),
            "recommended_execution_order": list(family_graph.get("recommended_execution_order") or []),
        }
        created_job_ids: set[str] = set()
        for child_spec in list(compiled.get("job_specs") or []):
            if not isinstance(child_spec, dict):
                continue
            bound_uid = str(((child_spec.get("binding") or {}).get("id")) or "").strip()
            if not bound_uid:
                continue
            prompt_meta = dict(child_spec.get("prompt_meta") or {}) if isinstance(child_spec.get("prompt_meta"), dict) else {}
            prompt_meta["generated_from_parent_decomposition_job"] = str(spec.get("job_id") or "")
            prompt_meta["generated_from_parent_uid"] = parent_uid
            prompt_meta["generated_child_mode"] = "explicit_pass_chain_v1"
            prompt_meta["execution_family_compilation"] = dict(compilation_meta)
            child_spec["prompt_meta"] = prompt_meta
            saved = save_job(child_spec, repo_root=repo_root)
            saved_job_id = str(saved.get("job_id") or child_spec.get("job_id") or "").strip()
            if saved_job_id:
                created_job_ids.add(saved_job_id)
            explicit_meta = (
                prompt_meta.get("explicit_pass_chain")
                if isinstance(prompt_meta.get("explicit_pass_chain"), dict)
                else {}
            )
            projection_uid = str(explicit_meta.get("projection_for_child_uid") or "").strip()
            if projection_uid and saved_job_id:
                binding_to_job[projection_uid] = saved_job_id
            created.append(
                {
                    "uid": bound_uid,
                    "job_id": saved_job_id,
                    "depends_on": [str(item).strip() for item in list(child_spec.get("depends_on") or []) if str(item).strip()],
                    "run_at": run_at_utc,
                    "reason": "job_created",
                }
            )
        created[:] = [row for row in created if str(row.get("job_id") or "").strip() in created_job_ids]
    elif canonical_intent_family:
        from infra.controlplane import intent_family_v2_compiler as _intent_family_v2_compiler

        compiled = _intent_family_v2_compiler.compile_intent_family_v2(
            parent_uid=parent_uid,
            run_at_utc=run_at_utc,
            builder_model=child_default_model,
            verifier_model=child_verifier_model,
            max_iterations=child_loop_max_iterations,
            repo_root=Path(repo_root or _repo_root()).resolve(),
        )
        family_graph = compiled.get("family_graph") if isinstance(compiled.get("family_graph"), dict) else {}
        compilation_meta = {
            "schema_version": str(compiled.get("schema_version") or ""),
            "workflow_definition_version": str(compiled.get("workflow_definition_version") or ""),
            "compilation_fingerprint": str(compiled.get("compilation_fingerprint") or ""),
            "family_graph_fingerprint": str(family_graph.get("graph_fingerprint") or ""),
            "recommended_execution_order": list(family_graph.get("recommended_execution_order") or []),
        }
        compiled_specs_by_uid = {
            str((row.get("binding") or {}).get("id") or "").strip(): dict(row)
            for row in list(compiled.get("job_specs") or [])
            if str((row.get("binding") or {}).get("id") or "").strip()
        }
        for row in child_rows:
            child_uid = str(row.get("uid") or "")
            job_id = planned_job_ids[child_uid]
            if row not in executable_child_rows:
                skipped.append({"uid": child_uid, "job_id": job_id, "reason": "non_executable_relationship"})
                binding_to_job[child_uid] = job_id
                continue
            child_spec = dict(compiled_specs_by_uid.get(child_uid) or {})
            if not child_spec:
                skipped.append({"uid": child_uid, "job_id": job_id, "reason": "compiled_spec_missing"})
                continue
            prompt_meta = (
                dict(child_spec.get("prompt_meta") or {})
                if isinstance(child_spec.get("prompt_meta"), dict)
                else {}
            )
            prompt_meta["generated_from_parent_decomposition_job"] = str(spec.get("job_id") or "")
            prompt_meta["generated_from_parent_uid"] = parent_uid
            prompt_meta["generated_child_mode"] = "compiled_intent_family_v2"
            prompt_meta["execution_family_compilation"] = dict(compilation_meta)
            child_spec["prompt_meta"] = prompt_meta
            depends_on_jobs = [str(item).strip() for item in list(child_spec.get("depends_on") or []) if str(item).strip()]
            job_path = jobs_dir(repo_root) / f"{_safe_name(str(child_spec.get('job_id') or job_id))}.json"
            if job_path.exists():
                existing = _read_json(job_path)
                existing_depends = [str(item).strip() for item in list(existing.get("depends_on") or []) if str(item).strip()]
                existing_binding = str((existing.get("binding") or {}).get("id") or "").strip()
                existing_prompt_meta = existing.get("prompt_meta") if isinstance(existing.get("prompt_meta"), dict) else {}
                existing_execution_family_contract = (
                    existing_prompt_meta.get("execution_family_contract")
                    if isinstance(existing_prompt_meta.get("execution_family_contract"), dict)
                    else {}
                )
                existing_execution_family_compilation = (
                    existing_prompt_meta.get("execution_family_compilation")
                    if isinstance(existing_prompt_meta.get("execution_family_compilation"), dict)
                    else {}
                )
                planned_execution_family_contract = (
                    child_spec.get("prompt_meta", {}).get("execution_family_contract")
                    if isinstance(child_spec.get("prompt_meta"), dict)
                    else {}
                )
                if (
                    existing_depends == depends_on_jobs
                    and existing_binding == child_uid
                    and existing_execution_family_contract == planned_execution_family_contract
                    and existing_execution_family_compilation == compilation_meta
                ):
                    skipped.append({"uid": child_uid, "job_id": str(child_spec.get("job_id") or job_id), "reason": "job_exists"})
                    binding_to_job[child_uid] = str(child_spec.get("job_id") or job_id)
                    continue
            saved = save_job(child_spec, repo_root=repo_root)
            binding_to_job[child_uid] = str(saved.get("job_id") or job_id)
            reason = "job_updated" if job_path.exists() else "job_created"
            created.append({"uid": child_uid, "job_id": str(saved.get("job_id") or job_id), "depends_on": depends_on_jobs, "run_at": run_at_utc, "reason": reason})
    else:
        for row in child_rows:
            child_uid = str(row.get("uid") or "")
            job_id = planned_job_ids[child_uid]
            if row not in executable_child_rows:
                skipped.append({"uid": child_uid, "job_id": job_id, "reason": "non_executable_relationship"})
                binding_to_job[child_uid] = job_id
                continue
            depends_on_jobs: List[str] = []
            for dep_uid in list(row.get("depends_on_uids") or []):
                dep_job_id = binding_to_job.get(str(dep_uid)) or planned_job_ids.get(str(dep_uid), "")
                if dep_job_id and dep_job_id not in depends_on_jobs:
                    depends_on_jobs.append(dep_job_id)
            child_spec = _child_spec_for_row(
                row,
                child_uid=child_uid,
                job_id=job_id,
                depends_on_jobs=depends_on_jobs,
            )
            binding_to_job[child_uid] = _save_or_skip_child_spec(
                row,
                child_uid=child_uid,
                child_spec=child_spec,
                depends_on_jobs=depends_on_jobs,
            )
    family_closeout_job = _queue_execution_family_closeout_job_after_pass(
        spec,
        parent_uid=parent_uid,
        child_rows=executable_child_rows,
        child_job_ids=[str(binding_to_job.get(str(row.get("uid") or "")) or planned_job_ids.get(str(row.get("uid") or ""), "")) for row in executable_child_rows if str(binding_to_job.get(str(row.get("uid") or "")) or planned_job_ids.get(str(row.get("uid") or ""), "")).strip()],
        run_at_utc=run_at_utc,
        repo_root=repo_root,
    )
    next_ready_job_id = ""
    for row in executable_child_rows:
        child_uid = str(row.get("uid") or "").strip()
        candidate_job_id = str(binding_to_job.get(child_uid) or planned_job_ids.get(child_uid, "")).strip()
        if not candidate_job_id:
            continue
        dep_uids = [str(item or "").strip() for item in list(row.get("depends_on_uids") or []) if str(item or "").strip()]
        dep_job_ids = [str(binding_to_job.get(dep_uid) or planned_job_ids.get(dep_uid, "")).strip() for dep_uid in dep_uids]
        dep_job_ids = [job_id for job_id in dep_job_ids if job_id]
        if any(str(load_state(dep_job_id, repo_root=repo_root).get("last_status") or "").strip().lower() not in SUCCESS_TERMINAL_STATUSES for dep_job_id in dep_job_ids):
            continue
        next_ready_job_id = candidate_job_id
        break
    return {
        "enabled": True,
        "created_count": len(created),
        "skipped_count": len(skipped),
        "run_at_utc": run_at_utc,
        "children": created,
        "skipped": skipped,
        "family_closeout_job": family_closeout_job,
        "execution_family_compilation": dict(compilation_meta),
        "next_ready_job_id": next_ready_job_id,
    }


def _looks_like_pis_creation_job(spec: Dict[str, Any]) -> bool:
    text_parts: List[str] = [
        str(spec.get("job_id") or ""),
        str(spec.get("title") or ""),
        str(spec.get("description") or ""),
        str(spec.get("task_id") or ""),
    ]
    prompt = spec.get("prompt")
    if isinstance(prompt, dict):
        text_parts.extend(
            [
                str(prompt.get("message") or ""),
                str(prompt.get("builder_message") or ""),
                str(prompt.get("verifier_message") or ""),
                str(prompt.get("builder_prompt") or ""),
                str(prompt.get("verifier_prompt") or ""),
            ]
        )
    blob = " ".join([x for x in text_parts if x]).lower()
    if "pis" not in blob:
        return False
    return any(
        k in blob
        for k in [
            "create child",
            "create children",
            "children pis",
            "parent pis",
            "pis creation",
            "mint child",
            "decomposition",
        ]
    )


def _apply_pis_creation_runtime_overrides(spec: Dict[str, Any], runtime: Dict[str, Any]) -> Dict[str, Any]:
    if not _looks_like_pis_creation_job(spec):
        return runtime
    kind = _normalize_job_kind(spec.get("job_kind"))
    out = dict(runtime or {})
    if kind == "build_verify_loop":
        return out

    default_model = _runtime_policy.get_runtime_policy_text(
        "scheduler.decompose.runtime_model",
        _scheduler_default_model(job_kind=kind),
    )
    default_fallbacks = _runtime_policy_models(
        "scheduler.decompose.runtime_fallback_models",
        ",".join(_scheduler_default_fallback_models(job_kind=kind)),
    )
    if not str(out.get("model") or "").strip():
        out["model"] = default_model
    if not str(out.get("agent_tech") or "").strip():
        inferred = _infer_agent_tech_from_model(str(out.get("model") or default_model))
        if inferred:
            out["agent_tech"] = inferred
    if not list(out.get("fallback_models") or []) and default_fallbacks:
        out["fallback_models"] = list(default_fallbacks)
    if "fallback_on_quota" not in out:
        out["fallback_on_quota"] = True
    return _normalize_runtime(out, job_kind=kind)


def _role_target(raw: Any) -> Dict[str, str]:
    role = str(raw or "").strip().lower()
    target = ROLE_TARGETS.get(role)
    if target is None:
        raise ValueError("role must be builder, assurer, verifier, generalist, or orchestrator")
    return {
        "role": role,
        "runtime_role": str(target.get("runtime_role") or role),
        "prompt_file": str(target.get("prompt_file") or ""),
    }


def _normalize_loop(raw: Any) -> Dict[str, Any]:
    obj = raw if isinstance(raw, dict) else {}
    raw_loop_policy = str(obj.get("loop_policy") or "").strip().lower()
    loop_policy = _normalize_loop_policy(obj.get("loop_policy"))
    builder_only = bool(obj.get("builder_only"))
    builder_role = str(obj.get("builder_role") or "builder").strip().lower() or "builder"
    verifier_role = "" if builder_only else (str(obj.get("verifier_role") or "verifier").strip().lower() or "verifier")
    _role_target(builder_role)
    if verifier_role:
        _role_target(verifier_role)
    default_max_iterations = 1
    try:
        max_iterations = int(obj.get("max_iterations") or default_max_iterations)
    except Exception:
        max_iterations = default_max_iterations
    pass_marker = str(obj.get("pass_marker") or "VERDICT: PASS").strip() or "VERDICT: PASS"
    builder_runtime_raw = obj.get("builder_runtime")
    verifier_runtime_raw = obj.get("verifier_runtime")
    if builder_runtime_raw is not None and not isinstance(builder_runtime_raw, dict):
        raise ValueError("loop.builder_runtime must be an object when provided")
    if verifier_runtime_raw is not None and not isinstance(verifier_runtime_raw, dict):
        raise ValueError("loop.verifier_runtime must be an object when provided")
    builder_runtime = _normalize_runtime(builder_runtime_raw, job_kind="build_verify_loop") if isinstance(builder_runtime_raw, dict) and builder_runtime_raw else {}
    verifier_runtime = _normalize_runtime(verifier_runtime_raw, job_kind="build_verify_loop") if isinstance(verifier_runtime_raw, dict) and verifier_runtime_raw else {}
    return {
        "loop_policy": loop_policy,
        "builder_role": builder_role,
        "verifier_role": verifier_role,
        "builder_only": builder_only,
        "max_iterations": max(1, min(10, max_iterations)),        "pass_marker": pass_marker,
        "builder_runtime": builder_runtime,
        "verifier_runtime": verifier_runtime,
    }


def _merge_runtime_overrides(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base or {})
    ov = override if isinstance(override, dict) else {}
    text_keys = ("executor_agent_tech", "pipeline_executor_mode", "agent_tech", "advisory_agent_tech", "fallback_agent_tech", "model", "advisory_model", "fallback_model", "reasoning_effort", "execution_mode", "compiled_execution_mode")
    for key in text_keys:
        value = str(ov.get(key) or "").strip()
        if value:            out[key] = value
    for key in ("fallback_agent_techs", "fallback_models", "tech_model_sequence"):
        value = ov.get(key)
        # Keep base fallbacks unless override provides a non-empty list.
        if isinstance(value, list) and value:
            out[key] = value
    if "fallback_on_quota" in ov:
        out["fallback_on_quota"] = bool(ov.get("fallback_on_quota"))
    return out


def _apply_runtime_send_options(options: Dict[str, Any], runtime: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(options or {})
    ov = dict(runtime or {})
    model = str(ov.get("model") or "").strip()
    reasoning_effort = str(ov.get("reasoning_effort") or "").strip()
    if model:
        out["model"] = model
    if reasoning_effort:
        out["reasoning_effort"] = reasoning_effort
    # Scheduled jobs own retry/fallback sequencing in _agent_send_with_retry_and_fallback.
    # Do not pass provider-level fallback models through again, or backends may
    # incorrectly retry cross-provider models inside a fixed provider adapter.
    out.pop("fallback_model", None)
    out.pop("fallback_models", None)
    out["fallback_on_quota"] = bool(ov.get("fallback_on_quota", True))
    return out


def _dedupe_strs(items: List[str]) -> List[str]:
    out: List[str] = []
    for item in items:
        txt = str(item or "").strip()
        if txt and txt not in out:
            out.append(txt)
    return out


def _codex_controller_runtime(runtime: Dict[str, Any]) -> Dict[str, Any]:
    requested = dict(runtime or {})
    primary = str(requested.get("model") or "").strip()
    fallback_models = list(requested.get("fallback_models") or [])
    sequence = list(requested.get("tech_model_sequence") or [])
    codex_models: List[str] = []
    for model_name in [primary, *fallback_models]:
        inferred = _infer_agent_tech_from_model(model_name)
        if not model_name:
            continue
        if inferred in {"", "openai", "codex"}:
            codex_models.append(str(model_name or "").strip())
    for row in sequence:
        if not isinstance(row, dict):
            continue
        if str(row.get("agent_tech") or "").strip().lower() in {"codex", "openai"}:
            codex_models.append(str(row.get("model") or "").strip())
    codex_models = _dedupe_strs(codex_models)
    controller_default_model = _runtime_policy.get_runtime_policy_text("scheduler.controller.default_model", "gpt-5.4")
    controller_default_fallbacks = _runtime_policy_models("scheduler.controller.default_fallback_models", "")
    controller_model = codex_models[0] if codex_models else controller_default_model
    if codex_models:
        controller_fallbacks = _dedupe_strs(codex_models[1:] + controller_default_fallbacks)
    else:
        controller_fallbacks = _dedupe_strs(controller_default_fallbacks)
    out = {
        "executor_agent_tech": "codex",
        "agent_tech": "codex",
        "model": controller_model,
        "fallback_models": controller_fallbacks,
        "fallback_on_quota": bool(requested.get("fallback_on_quota", True)),
        "reasoning_effort": str(requested.get("reasoning_effort") or "").strip().lower(),
        "fallback_agent_techs": [],
        "tech_model_sequence": [{"agent_tech": "codex", "model": controller_model}] + [
            {"agent_tech": "codex", "model": model_name} for model_name in controller_fallbacks
        ],
    }
    normalized = _normalize_runtime(out, job_kind="single_turn")
    normalized["executor_agent_tech"] = "codex"
    normalized["agent_tech"] = "codex"
    normalized["advisory_agent_tech"] = ""
    normalized["advisory_model"] = ""
    normalized["fallback_agent_tech"] = ""
    normalized["fallback_agent_techs"] = []
    normalized["execution_mode"] = "controller_only"
    normalized["tech_model_sequence"] = [{"agent_tech": "codex", "model": controller_model}] + [
        {"agent_tech": "codex", "model": model_name} for model_name in controller_fallbacks
    ]
    return normalized


def _scheduled_execution_contract(runtime: Dict[str, Any], *, job_kind: str = "single_turn") -> Dict[str, Any]:
    requested = dict(runtime or {})
    requested_tech = str(requested.get("advisory_agent_tech") or requested.get("agent_tech") or "").strip().lower()
    executor_tech = str(requested.get("executor_agent_tech") or "").strip().lower() or "codex"
    pipeline_executor_mode = str(requested.get("pipeline_executor_mode") or "").strip().lower()
    execution_mode = str(requested.get("execution_mode") or "").strip().lower()
    advisory_runtime: Dict[str, Any] = {}
    controller_runtime: Dict[str, Any]
    selected_execution_mode = "controller_only"
    execution_owner = executor_tech or "codex"
    proof_owner = "scheduler"
    retry_owner = "scheduler"
    force_codex_controller = (
        executor_tech == "codex"
        and not str(requested.get("advisory_agent_tech") or "").strip()
        and str(requested.get("execution_mode") or "").strip().lower() in {"", "controller_only"}
        and requested_tech in {"", "openai"}
    )
    if executor_tech == "pipeline":
        selected_execution_mode = "controller_only"
        execution_owner = "pipeline"
        if not pipeline_executor_mode:
            pipeline_executor_mode = PIPELINE_STAGE8_BACKEND_MODE
        controller_runtime = {
            "executor_agent_tech": "pipeline",
            "agent_tech": "pipeline",
            "model": "pipeline",
            "reasoning_effort": str(requested.get("reasoning_effort") or "").strip().lower(),
            "pipeline_executor_mode": pipeline_executor_mode,
        }
        if pipeline_executor_mode == PIPELINE_FULL_AUTO_MODE:
            proof_owner = "pipeline"
            retry_owner = "pipeline"
    elif requested_tech and requested_tech != "codex" and not force_codex_controller:
        selected_execution_mode = execution_mode or "advisory_then_controller"
        advisory_runtime = dict(requested)
        advisory_runtime["fallback_agent_techs"] = [
            str(x or "").strip().lower()
            for x in list(advisory_runtime.get("fallback_agent_techs") or [])
            if str(x or "").strip().lower() and str(x or "").strip().lower() != "codex"
        ]
        if str(advisory_runtime.get("fallback_agent_tech") or "").strip().lower() == "codex":
            advisory_runtime["fallback_agent_tech"] = ""
        advisory_runtime["fallback_models"] = [
            str(x or "").strip()
            for x in list(advisory_runtime.get("fallback_models") or [])
            if str(x or "").strip() and _infer_agent_tech_from_model(str(x or "").strip()) != "codex"
        ]
        advisory_runtime["tech_model_sequence"] = [
            {"agent_tech": str(row.get("agent_tech") or "").strip().lower(), "model": str(row.get("model") or "").strip()}
            for row in list(advisory_runtime.get("tech_model_sequence") or [])
            if isinstance(row, dict) and str(row.get("agent_tech") or "").strip().lower() not in {"", "codex"}
        ]
        controller_runtime = _codex_controller_runtime(requested)
        if selected_execution_mode == "controller_only":
            advisory_runtime = {}
        elif selected_execution_mode == "advisory_only":
            controller_runtime = {}
            execution_owner = str(advisory_runtime.get("agent_tech") or executor_tech or "codex")
    else:
        selected_execution_mode = execution_mode or "controller_only"
        if executor_tech == "codex":
            controller_runtime = _codex_controller_runtime(requested)
            controller_runtime["execution_mode"] = selected_execution_mode
        else:
            controller_runtime = dict(requested)
    return {
        "execution_mode": selected_execution_mode,
        "compiled_execution_mode": _compiled_execution_mode_for_job({"job_kind": job_kind}, requested),
        "controller_runtime": controller_runtime,
        "advisory_runtime": advisory_runtime,
        "requested_backend_runtime": dict(requested),
        "write_capable": True,
        "execution_owner": execution_owner,
        "proof_owner": proof_owner,
        "retry_owner": retry_owner,
        "pipeline_executor_mode": pipeline_executor_mode,
    }

def _append_advisory_backend_context(
    message: str,
    *,
    advisory_runtime: Dict[str, Any],
    advisory_result: Dict[str, Any],
    advisory_send: Dict[str, Any],
) -> str:
    assistant = advisory_result.get("assistant") if isinstance(advisory_result.get("assistant"), dict) else {}
    if not bool(advisory_result.get("ok")):
        return str(message or "")
    status = str(assistant.get("status") or "")
    content = str(assistant.get("content") or "")
    if not content.strip():
        return str(message or "")
    selected_tech = str(advisory_send.get("selected_agent_tech") or advisory_runtime.get("agent_tech") or "")
    selected_model = str(advisory_send.get("selected_model") or advisory_runtime.get("model") or "")
    advisory_payload = _normalized_advisory_backend_payload(content)
    advisory_block = "\n".join(
        [
            "",
            "[Advisory Backend Result JSON]",
            json.dumps(
                {
                    "backend_agent_tech": selected_tech,
                    "backend_model": selected_model,
                    "backend_status": status,
                    "response_format": str(advisory_payload.get("response_format") or ""),
                    "payload": advisory_payload.get("payload") or {},
                },
                ensure_ascii=False,
                indent=2,
            ),
            "[End Advisory Backend Result JSON]",
        ]
    ).strip()
    return f"{str(message or '').rstrip()}\n\n{advisory_block}\n"


def _scheduled_executor_mode(spec: Dict[str, Any]) -> str:
    return "pis_creation" if _looks_like_pis_creation_job(spec) else "repo_change"


def _scheduled_controller_contract(spec: Dict[str, Any], *, stage: str) -> str:
    mode = _scheduled_executor_mode(spec)
    if mode == "pis_creation":
        body = [
            "You are the Codex executor of record for a scheduled PIS/work-order job.",
            "You are responsible for producing the final durable output.",
            "If advisory backend evidence is present, treat it as advisory only.",
            "Do not delegate authority, tool use, or final judgment to the advisory backend.",
            "Produce the concrete PIS/work-order artifact requested by the task, not just commentary.",
        ]
    elif stage == "verifier":
        body = [
            "You are the Codex verifier/controller for a scheduled write-capable job.",
            "You must issue the final verification judgment yourself.",
            "If advisory backend evidence is present, use it as input only.",
            "Return a fail-closed verdict that matches the required verifier contract exactly.",
        ]
    else:
        body = [
            "You are the Codex executor of record for a scheduled write-capable job.",
            "You must perform the final codebase/worktree mutation logic yourself.",
            "If advisory backend evidence is present, use it as advisory input only.",
            "Do not stop at planning or summarizing; produce the concrete implementation output the job asks for.",
        ]
    return "\n".join(["[Codex Scheduled Controller Contract]"] + [f"- {line}" for line in body]).strip()


def _scheduled_advisory_schema(spec: Dict[str, Any], *, stage: str) -> Dict[str, Any]:
    mode = _scheduled_executor_mode(spec)
    if mode == "pis_creation":
        return {
            "mode": "pis_creation",
            "stage": stage,
            "required_keys": ["task_understanding", "proposed_artifacts", "acceptance_checks", "risks"],
            "notes": "Return compact structured help for Codex to create the PIS/work order.",
        }
    if stage == "verifier":
        return {
            "mode": "repo_change",
            "stage": "verifier",
            "required_keys": ["verdict", "findings", "missing_validation", "smallest_next_step"],
            "notes": "verdict must be one of pass, fail, uncertain.",
        }
    return {
        "mode": "repo_change",
        "stage": stage,
        "required_keys": ["task_understanding", "proposed_edits", "validation_suggestions", "blocking_issues"],
        "notes": "Return concrete implementation guidance, not roleplay or narrative.",
    }


def _build_advisory_backend_message(
    spec: Dict[str, Any],
    *,
    stage: str,
    base_message: str,
) -> str:
    schema = _scheduled_advisory_schema(spec, stage=stage)
    lines = [
        "[Scheduled Advisory Backend Request]",
        "You are an advisory backend called by a Codex executor.",
        "You have no tool, repo, write, scheduling, or authority ownership.",
        "Respond with JSON only. No markdown fences. No prose before or after the JSON.",
        "Your response must be valid JSON and include every required key exactly once.",
        f"required_schema: {json.dumps(schema, ensure_ascii=False)}",
        "",
        "Task context for your advisory response:",
        str(base_message or "").strip(),
    ]
    return "\n".join(lines).strip()


def _normalized_advisory_backend_payload(content: str) -> Dict[str, Any]:
    raw = str(content or "").strip()
    if not raw:
        return {"response_format": "empty", "payload": {"raw_text": "", "parse_error": "empty_response"}}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {"response_format": "json", "payload": parsed}
        return {"response_format": "json_non_object", "payload": {"raw_json": parsed, "parse_error": "top_level_not_object"}}
    except Exception as exc:
        return {"response_format": "non_json_wrapped", "payload": {"raw_text": raw, "parse_error": str(exc)}}


def _apply_project_runner_contract_policy(spec: Dict[str, Any]) -> Dict[str, Any]:
    project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
    scheduler_policy = project_run.get("scheduler_policy") if isinstance(project_run.get("scheduler_policy"), dict) else {}
    if not scheduler_policy:
        return spec

    required_bundle = str(scheduler_policy.get("required_context_bundle") or "").strip()
    if not required_bundle:
        raise ValueError("experiment contract context_policy.required_context_bundle is required for scheduled execution")
    current_bundle = str(spec.get("required_context_bundle") or "").strip()
    if current_bundle and current_bundle != required_bundle:
        raise ValueError(
            "scheduled required_context_bundle conflicts with experiment contract "
            f"({current_bundle} != {required_bundle})"
        )
    spec["required_context_bundle"] = _normalize_context_bundle(required_bundle)

    additional_allowed = bool(scheduler_policy.get("additional_context_refs_allowed"))
    explicit_extra_refs = list(spec.get("context_refs") or []) + list(spec.get("additional_context_refs") or [])
    if explicit_extra_refs and not additional_allowed:
        raise ValueError(
            "experiment contract forbids additional context refs for scheduled execution: "
            + ", ".join(explicit_extra_refs)
        )
    forbidden_requested = [
        ref
        for ref in explicit_extra_refs
        if ref in {str(item or "").strip() for item in list(scheduler_policy.get("forbidden_context_refs") or []) if str(item or "").strip()}
    ]
    if forbidden_requested:
        raise ValueError(
            "experiment contract forbids requested context refs for scheduled execution: "
            + ", ".join(forbidden_requested)
        )

    forbidden = []
    for item in list(spec.get("forbidden_context_refs") or []) + list(scheduler_policy.get("forbidden_context_refs") or []):
        text = str(item or "").strip()
        if text and text not in forbidden:
            forbidden.append(text)
    spec["forbidden_context_refs"] = forbidden
    return spec


def validate_job_spec(
    raw: Dict[str, Any],
    *,
    now_utc: Optional[str] = None,
    repo_root: Optional[Path] = None,
    allow_legacy_job_shape_compatibility: bool = False,
) -> Dict[str, Any]:
    job_id = str(raw.get("job_id") or "").strip()
    if not JOB_ID_RE.match(job_id):
        raise ValueError("job_id must match ^[A-Za-z0-9][A-Za-z0-9._-]{2,79}$")
    job_kind = _normalize_job_kind(raw.get("job_kind"))
    role_meta = _role_target(raw.get("role")) if job_kind == "single_turn" else None
    prompt = str(raw.get("prompt") or "").strip()
    prompt_ref = str(raw.get("prompt_ref") or "").strip()
    if not prompt and not prompt_ref and job_kind not in {"swarm_launch", "project_runner"}:
        raise ValueError("prompt is required")
    created_utc = str(raw.get("created_utc") or now_utc or _utc_iso()).strip() or _utc_iso()
    _parse_utc(created_utc)
    spec = {
        "schema_version": SCHEMA_VERSION,
        "job_id": job_id,
        "enabled": bool(raw.get("enabled", True)),
        "created_utc": created_utc,
        "updated_utc": str(now_utc or _utc_iso()),
        "binding": _normalize_binding(raw.get("binding") or {}),
        "depends_on": _normalize_depends_on(raw.get("depends_on"), job_id=job_id),
        "context_refs": _normalize_context_refs(raw.get("context_refs")),
        "required_context_bundle": _normalize_context_bundle(raw.get("required_context_bundle")),
        "additional_context_refs": _normalize_context_refs(raw.get("additional_context_refs")),
        "forbidden_context_refs": _normalize_context_refs(raw.get("forbidden_context_refs")),
        "expected_output": _normalize_expected_output(raw.get("expected_output")),
        "schedule": _normalize_schedule(raw.get("schedule") or {}),
        "policy": _normalize_policy(raw.get("policy"), job_kind=job_kind),
        "runtime": _normalize_runtime(raw.get("runtime"), job_kind=job_kind),
        "job_kind": job_kind,
        "display_title": _normalize_display_title(str(raw.get("display_title") or "")),
        "prompt_ref": prompt_ref,
        "prompt_sha256": str(raw.get("prompt_sha256") or "").strip(),
        "prompt_preview": str(raw.get("prompt_preview") or _normalize_prompt_preview(prompt)).strip(),
    }
    raw_job_shape = str(raw.get("job_shape") or "").strip()
    raw_job_shape_source = str(raw.get("job_shape_source") or "").strip()
    raw_job_shape_compatibility_mode = bool(raw.get("job_shape_compatibility_mode"))
    raw_job_shape_migration_status = str(raw.get("job_shape_migration_status") or "").strip()
    raw_job_shape_migration_reason = str(raw.get("job_shape_migration_reason") or "").strip()
    _validate_job_shape_write_contract(
        raw_job_shape=raw_job_shape,
        raw_job_shape_source=raw_job_shape_source,
        raw_job_shape_compatibility_mode=raw_job_shape_compatibility_mode,
        raw_job_shape_migration_status=raw_job_shape_migration_status,
        raw_job_shape_migration_reason=raw_job_shape_migration_reason,
        allow_legacy_job_shape_compatibility=allow_legacy_job_shape_compatibility,
    )
    if raw_job_shape or raw_job_shape_source or raw_job_shape_compatibility_mode:
        spec.update(
            _resolve_job_shape_fields(
                {
                    "job_kind": job_kind,
                    "job_shape": raw_job_shape,
                    "job_shape_source": raw_job_shape_source,
                    "job_shape_compatibility_mode": raw_job_shape_compatibility_mode,
                    "job_shape_migration_status": raw_job_shape_migration_status,
                    "job_shape_migration_reason": raw_job_shape_migration_reason,
                }
            )
        )
    else:
        spec.update(
            {
                "job_shape": _canonical_job_shape_for_job_kind(job_kind),
                "job_shape_source": "explicit",
                "job_shape_compatibility_mode": False,
                "job_shape_migration_status": "explicit",
                "job_shape_migration_reason": "",
            }
        )
    if raw_job_shape_migration_status and not (raw_job_shape or raw_job_shape_source or raw_job_shape_compatibility_mode):
        spec.update(
            _resolve_job_shape_fields(
                {
                    "job_kind": job_kind,
                    "job_shape": str(spec.get("job_shape") or ""),
                    "job_shape_source": str(spec.get("job_shape_source") or ""),
                    "job_shape_compatibility_mode": bool(spec.get("job_shape_compatibility_mode")),
                    "job_shape_migration_status": raw_job_shape_migration_status,
                    "job_shape_migration_reason": raw_job_shape_migration_reason,
                }
            )
        )
    if isinstance(raw.get("prompt_meta"), dict) and raw.get("prompt_meta"):
        spec["prompt_meta"] = dict(raw.get("prompt_meta") or {})
    if prompt:
        spec["prompt"] = prompt
    if job_kind == "single_turn":
        spec["role"] = role_meta["role"]
    elif job_kind == "build_verify_loop":
        spec["loop"] = _normalize_loop(raw.get("loop"))
    elif job_kind == "project_runner":
        from experiments_shared import project_runner_contract as _project_runner_contract

        spec["project_run"] = _project_runner_contract.normalize_project_run(
            raw.get("project_run"),
            repo_root=Path(repo_root or _repo_root()).resolve(),
        )
        spec = _apply_project_runner_contract_policy(spec)
    else:
        spec["swarm"] = _normalize_swarm_launch(raw.get("swarm"))
    _validate_job_shape_context_spec(spec)
    return spec


def _validate_job_shape_write_contract(
    *,
    raw_job_shape: str,
    raw_job_shape_source: str,
    raw_job_shape_compatibility_mode: bool,
    raw_job_shape_migration_status: str,
    raw_job_shape_migration_reason: str,
    allow_legacy_job_shape_compatibility: bool,
) -> None:
    if raw_job_shape_source not in JOB_SHAPE_SOURCE_SET:
        raise ValueError(
            "job_shape_source must be one of: " + ", ".join(sorted(source for source in JOB_SHAPE_SOURCE_SET if source))
        )
    compatibility_requested = (
        raw_job_shape_compatibility_mode
        or raw_job_shape_source == "compatibility_inferred_from_job_kind"
    )
    backfill_requested = raw_job_shape_source == "backfilled_from_job_kind_contract"

    if raw_job_shape_migration_status and raw_job_shape_migration_status not in JOB_SHAPE_MIGRATION_STATUS_SET:
        raise ValueError(
            "job_shape_migration_status must be one of "
            + ", ".join(sorted(JOB_SHAPE_MIGRATION_STATUS_SET))
        )
    if raw_job_shape_migration_reason and not raw_job_shape_migration_status:
        raise ValueError("job_shape_migration_reason requires an explicit job_shape_migration_status")

    if compatibility_requested and not allow_legacy_job_shape_compatibility:
        raise ValueError(
            "job_shape compatibility mode is reserved for legacy records during explicit migration"
        )
    if backfill_requested and not allow_legacy_job_shape_compatibility:
        raise ValueError(
            "job_shape backfill source is reserved for the legacy backfill path"
        )
    if raw_job_shape_migration_status and raw_job_shape_migration_status != "explicit" and not allow_legacy_job_shape_compatibility:
        raise ValueError(
            "job_shape migration status is reserved for legacy records during explicit migration"
        )

    if compatibility_requested:
        if raw_job_shape_source != "compatibility_inferred_from_job_kind" or not raw_job_shape_compatibility_mode:
            raise ValueError(
                "job_shape compatibility records must persist both compatibility source and compatibility_mode=true"
            )
        if raw_job_shape_migration_status not in {"legacy_migration_required", "legacy_pending_backfill", "legacy_incompatible"}:
            raise ValueError(
                "job_shape compatibility records must use migration status legacy_migration_required, "
                "legacy_pending_backfill, or legacy_incompatible"
            )
        if raw_job_shape_migration_status in {"legacy_migration_required", "legacy_incompatible"} and not raw_job_shape_migration_reason:
            raise ValueError(
                "job_shape migration status "
                + raw_job_shape_migration_status
                + " requires a non-empty job_shape_migration_reason"
            )
        return

    if backfill_requested:
        if raw_job_shape_compatibility_mode:
            raise ValueError("job_shape backfilled records may not persist compatibility_mode=true")
        if raw_job_shape_migration_status != "legacy_backfilled":
            raise ValueError("job_shape backfilled records must use migration status legacy_backfilled")
        if raw_job_shape_migration_reason:
            raise ValueError("job_shape backfilled records may not persist a migration reason")
        return

    if raw_job_shape_migration_status == "legacy_backfilled":
        raise ValueError(
            "job_shape migration status legacy_backfilled requires source backfilled_from_job_kind_contract"
        )
    if raw_job_shape_migration_status in {"legacy_migration_required", "legacy_pending_backfill", "legacy_incompatible"}:
        raise ValueError(
            "job_shape migration status "
            + raw_job_shape_migration_status
            + " requires compatibility_inferred_from_job_kind with compatibility_mode=true"
        )
    if raw_job_shape_migration_reason:
        raise ValueError("explicit job_shape records may not persist a migration reason")


def _validate_job_shape_context_spec(spec: Dict[str, Any]) -> None:
    migration_status = str(spec.get("job_shape_migration_status") or "").strip()
    if migration_status == "legacy_incompatible":
        reason = str(spec.get("job_shape_migration_reason") or "").strip() or "legacy record is incompatible with the canonical job-shape contract"
        raise ValueError(reason)
    if migration_status == "legacy_migration_required":
        reason = str(spec.get("job_shape_migration_reason") or "").strip() or "legacy record requires explicit job-shape backfill before launch"
        raise ValueError(reason)
    obligations = _resolved_job_shape_context_obligations(spec)
    required_bundle = str(obligations.get("required_context_bundle") or "").strip()
    current_bundle = str(spec.get("required_context_bundle") or "").strip()
    shape_name = str(obligations.get("job_shape") or spec.get("job_shape") or "").strip()
    if current_bundle != required_bundle:
        raise ValueError(
            f"job_shape {shape_name} requires required_context_bundle={required_bundle}"
        )

    forbidden_refs = {
        str(item or "").strip()
        for item in list(spec.get("forbidden_context_refs") or [])
        if str(item or "").strip()
    }
    bundle_refs = set(_context_bundles.bundle_refs(required_bundle))
    blocked_bundle_refs = sorted(ref for ref in forbidden_refs if ref in bundle_refs)
    if blocked_bundle_refs:
        raise ValueError(
            f"job_shape {shape_name} forbidden_context_refs may not remove required bundle refs: "
            + ", ".join(blocked_bundle_refs)
        )


def _compiled_work_order_handoff_from_job_spec(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    handoff = (
        prompt_meta.get("compiled_work_order")
        if isinstance(prompt_meta.get("compiled_work_order"), dict)
        else (spec.get("compiled_work_order") if isinstance(spec.get("compiled_work_order"), dict) else {})
    )
    artifact_rel = str(handoff.get("compiled_artifact_rel") or "").strip()
    handoff_status = str(handoff.get("status") or "").strip()
    fallback_codes = [str(item).strip() for item in list(handoff.get("non_compiled_reason_codes") or []) if str(item).strip()]
    if fallback_codes or handoff_status == "fallback_legacy":
        return {
            "mode": "compatibility_fallback",
            "status": handoff_status or "fallback_legacy",
            "fallback_reason": "handoff_marked_non_compiled",
            "reason_codes": fallback_codes,
            "artifact_rel": artifact_rel,
        }

    inline_envelope = handoff.get("compiled_envelope") if isinstance(handoff.get("compiled_envelope"), dict) else {}
    raw_envelope = dict(inline_envelope)
    if not raw_envelope and artifact_rel:
        artifact_path = (root / artifact_rel).resolve()
        if not artifact_path.exists():
            return {
                "mode": "compatibility_fallback",
                "status": handoff_status or "compiled_missing_artifact",
                "fallback_reason": "compiled_artifact_missing",
                "reason_codes": ["compiled_artifact_missing"],
                "artifact_rel": artifact_rel,
            }
        loaded = _read_json(artifact_path)
        if not isinstance(loaded, dict) or not loaded:
            return {
                "mode": "compatibility_fallback",
                "status": handoff_status or "compiled_invalid_artifact",
                "fallback_reason": "compiled_artifact_unreadable",
                "reason_codes": ["compiled_artifact_unreadable"],
                "artifact_rel": artifact_rel,
            }
        raw_envelope = dict(loaded)
    if not raw_envelope:
        return {
            "mode": "compatibility_fallback",
            "status": handoff_status or "compiled_absent",
            "fallback_reason": "compiled_envelope_absent",
            "reason_codes": [],
            "artifact_rel": artifact_rel,
        }

    from infra.controlplane import compiled_work_order_envelope as _compiled_work_order_envelope

    validated = _compiled_work_order_envelope.validate_compiled_work_order_envelope(raw_envelope)
    if not validated.get("ok"):
        return {
            "mode": "compatibility_fallback",
            "status": handoff_status or "compiled_validation_failed",
            "fallback_reason": str(validated.get("failure_class") or "compiled_work_order_validation_failed"),
            "reason_codes": [str((row or {}).get("code") or "").strip() for row in list(validated.get("errors") or []) if str((row or {}).get("code") or "").strip()],
            "artifact_rel": artifact_rel,
            "warnings": list(validated.get("warnings") or []),
        }
    context = _compiled_work_order_envelope.resolve_compiled_work_order_context_refs(validated.get("normalized_envelope") or {})
    coexistence = _compiled_work_order_envelope.resolve_compiled_work_order_coexistence(
        compiled_envelope=validated.get("normalized_envelope") or {},
        prose_brief_present=bool(str(spec.get("prompt") or "").strip() or str(spec.get("prompt_preview") or "").strip()),
    )
    if not bool(context.get("ok")) or not bool(coexistence.get("ok")):
        errors = list(context.get("errors") or []) + list(coexistence.get("errors") or [])
        return {
            "mode": "compatibility_fallback",
            "status": handoff_status or "compiled_context_blocked",
            "fallback_reason": "compiled_contract_blocked",
            "reason_codes": [str((row or {}).get("code") or "").strip() for row in errors if str((row or {}).get("code") or "").strip()],
            "artifact_rel": artifact_rel,
            "warnings": list(context.get("warnings") or []) + list(coexistence.get("warnings") or []),
        }
    normalized = dict(validated.get("normalized_envelope") or {})
    return {
        "mode": "authoritative",
        "status": "compiled_authoritative",
        "artifact_rel": artifact_rel,
        "normalized_envelope": normalized,
        "effective_context_refs": list(context.get("effective_refs") or []),
        "omitted_context_refs": list(context.get("omitted_refs") or []),
        "effective_path": str(coexistence.get("effective_path") or ""),
        "precedence": str(coexistence.get("precedence") or ""),
        "warnings": list(context.get("warnings") or []) + list(coexistence.get("warnings") or []),
    }


def _spec_with_compiled_work_order_contract(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    hydrated = dict(spec)
    handoff = _compiled_work_order_handoff_from_job_spec(hydrated, repo_root=repo_root)
    if str(handoff.get("mode") or "") != "authoritative":
        return hydrated
    envelope = handoff.get("normalized_envelope") if isinstance(handoff.get("normalized_envelope"), dict) else {}
    if not envelope:
        return hydrated
    forbidden_refs = {str(ref).strip() for ref in list(envelope.get("forbidden_context_refs") or []) if str(ref).strip()}
    hydrated["required_context_bundle"] = str(envelope.get("required_context_bundle") or hydrated.get("required_context_bundle") or "").strip()
    merged_refs: List[str] = []
    for ref in list(hydrated.get("context_refs") or []):
        normalized = str(ref or "").strip()
        if normalized and normalized not in forbidden_refs and normalized not in merged_refs:
            merged_refs.append(normalized)
    for ref in list(envelope.get("additional_context_refs") or []):
        normalized = str(ref or "").strip()
        if normalized and normalized not in forbidden_refs and normalized not in merged_refs:
            merged_refs.append(normalized)
    for ref in list(hydrated.get("additional_context_refs") or []):
        normalized = str(ref or "").strip()
        if normalized and normalized not in forbidden_refs and normalized not in merged_refs:
            merged_refs.append(normalized)
    hydrated["additional_context_refs"] = merged_refs
    hydrated["forbidden_context_refs"] = sorted(forbidden_refs)
    return hydrated


def compiled_execution_ir_from_job_spec(spec: Dict[str, Any]) -> Dict[str, Any]:
    compiled_handoff = _compiled_work_order_handoff_from_job_spec(spec)
    compiled_envelope = compiled_handoff.get("normalized_envelope") if isinstance(compiled_handoff.get("normalized_envelope"), dict) else {}
    policy = spec.get("policy") if isinstance(spec.get("policy"), dict) else {}
    runtime = spec.get("runtime") if isinstance(spec.get("runtime"), dict) else {}
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    expected_output = spec.get("expected_output") if isinstance(spec.get("expected_output"), dict) else {}
    job_shape_fields = _resolve_job_shape_fields(spec)
    job_kind = str(spec.get("job_kind") or "").strip().lower()
    role = str(spec.get("role") or "").strip().lower()
    project_run = spec.get("project_run") if isinstance(spec.get("project_run"), dict) else {}
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    execution_family_contract = (
        prompt_meta.get("execution_family_contract")
        if isinstance(prompt_meta.get("execution_family_contract"), dict)
        else {}
    )
    execution_family_closeout = (
        prompt_meta.get("execution_family_closeout")
        if isinstance(prompt_meta.get("execution_family_closeout"), dict)
        else {}
    )
    repo_change_mode = str(policy.get("repo_change_mode") or "").strip().lower()
    swarm_write_policy = ((spec.get("swarm") if isinstance(spec.get("swarm"), dict) else {}) or {}).get("write_policy")
    swarm_write_mode = str((swarm_write_policy if isinstance(swarm_write_policy, dict) else {}).get("mode") or "").strip().lower()
    write_capable = job_kind == "build_verify_loop" or repo_change_mode == "strict" or (job_kind == "swarm_launch" and swarm_write_mode == "bounded_patch")
    if job_kind == "build_verify_loop":
        mode = "build"
    elif job_kind == "swarm_launch":
        mode = "orchestrate"
    elif role == "verifier":
        mode = "verify"
    else:
        mode = "plan"
    claim_scope = "bounded_repo_change" if write_capable else "analysis_only"
    validation_plan: List[str] = []
    if write_capable:
        validation_plan.append("validation_evidence_required")
        if bool(policy.get("require_commit_journal")):
            validation_plan.append("commit_journal_required")
    child_kind = str(execution_family_contract.get("child_kind") or "").strip().lower()
    if child_kind == "intent_validation_suite":
        for marker in (
            "intent_e2e_validation_required",
            "intent_acceptance_summary_required",
            "negative_path_check_required",
        ):
            if marker not in validation_plan:
                validation_plan.append(marker)
    if child_kind == "intent_family_closeout":
        for marker in (
            "execution_family_audit_required",
            "execution_family_integration_repair_allowed",
            "execution_family_closeout_required",
            "execution_family_trust_hardening_required",
        ):
            if marker not in validation_plan:
                validation_plan.append(marker)
    closeout_kind = str(execution_family_closeout.get("contract_kind") or "").strip().lower()
    if closeout_kind:
        for marker in (
            "execution_family_audit_required",
            "execution_family_integration_repair_allowed",
            "execution_family_closeout_required",
        ):
            if marker not in validation_plan:
                validation_plan.append(marker)
        if str(execution_family_closeout.get("trust_hardening_mode") or "").strip():
            validation_plan.append("execution_family_trust_hardening_required")
    allowed_files = ["<scheduler_managed_scope>"] if write_capable else []
    execution_family_ir: Dict[str, Any] = {}
    if execution_family_contract:
        execution_family_ir = {
            "contract_kind": str(execution_family_contract.get("contract_kind") or "").strip(),
            "phase": "child_execution",
            "source_intent_uid": str(execution_family_contract.get("source_intent_uid") or "").strip(),
            "parent_uid": str(execution_family_contract.get("parent_uid") or "").strip(),
            "child_uid": str(execution_family_contract.get("child_uid") or "").strip(),
            "child_kind": str(execution_family_contract.get("child_kind") or "").strip(),
            "required_final_child_kind": str(execution_family_contract.get("required_final_child_kind") or "").strip(),
            "required_final_child_uid": str(execution_family_contract.get("required_final_child_uid") or "").strip(),
        }
    elif execution_family_closeout:
        allowed_repo_paths = [str(item).strip() for item in list(execution_family_closeout.get("allowed_repo_paths") or []) if str(item).strip()]
        if not allowed_repo_paths:
            allowed_repo_paths = _compiled_ir_execution_family_closeout_repo_paths_from_receipts(
                [str(item).strip() for item in list(execution_family_closeout.get("child_job_ids") or []) if str(item).strip()],
                repo_root=None,
            )
        allowed_ledger_paths = [str(item).strip() for item in list(execution_family_closeout.get("allowed_ledger_paths") or []) if str(item).strip()]
        closeout_rules = [str(item).strip() for item in list(execution_family_closeout.get("closeout_rules") or []) if str(item).strip()]
        execution_family_ir = {
            "contract_kind": str(execution_family_closeout.get("contract_kind") or "").strip(),
            "phase": "family_closeout",
            "source_intent_uid": str(execution_family_closeout.get("source_intent_uid") or "").strip(),
            "parent_uid": str(execution_family_closeout.get("parent_uid") or "").strip(),
            "required_final_child_uid": str(execution_family_closeout.get("required_final_child_uid") or "").strip(),
            "child_uids": [str(item).strip() for item in list(execution_family_closeout.get("child_uids") or []) if str(item).strip()],
            "child_job_ids": [str(item).strip() for item in list(execution_family_closeout.get("child_job_ids") or []) if str(item).strip()],
            "integration_audit_mode": str(execution_family_closeout.get("integration_audit_mode") or "").strip(),
            "trust_hardening_mode": str(execution_family_closeout.get("trust_hardening_mode") or "").strip(),
            "status_promotion_policy": str(execution_family_closeout.get("status_promotion_policy") or "").strip(),
            "receipt_selection_policy": str(execution_family_closeout.get("receipt_selection_policy") or "").strip(),
            "allowed_repo_paths": allowed_repo_paths,
            "allowed_ledger_paths": allowed_ledger_paths,
            "closeout_rules": closeout_rules,
        }
        allowed_files = sorted(list(dict.fromkeys(list(allowed_repo_paths) + list(allowed_ledger_paths))))
    goal = str(spec.get("prompt_preview") or spec.get("display_title") or spec.get("job_id") or "").strip()
    if not goal:
        goal = str((spec.get("prompt") or "")).strip()[:240]
    if not goal and job_kind == "project_runner":
        goal = (
            f"Run project adapter {str(project_run.get('project_id') or '').strip()}/"
            f"{str(project_run.get('runner_id') or '').strip()}"
        ).strip()
    if str(compiled_handoff.get("mode") or "") == "authoritative":
        goal = str((((compiled_envelope.get("scope_fence") if isinstance(compiled_envelope.get("scope_fence"), dict) else {}) or {}).get("objective")) or goal).strip() or goal
        for marker in [str(item).strip() for item in list(((compiled_envelope.get("validation_expectations") if isinstance(compiled_envelope.get("validation_expectations"), dict) else {}) or {}).get("required_checks") or []) if str(item).strip()]:
            if marker not in validation_plan:
                validation_plan.append(marker)
    compiled_forbidden_refs = {
        str(ref).strip()
        for ref in list(compiled_envelope.get("forbidden_context_refs") or [])
        if str(ref).strip()
    } if str(compiled_handoff.get("mode") or "") == "authoritative" else set()
    merged_additional_context_refs: List[str] = []
    for ref in list(spec.get("context_refs") or []):
        normalized = str(ref or "").strip()
        if normalized and normalized not in compiled_forbidden_refs and normalized not in merged_additional_context_refs:
            merged_additional_context_refs.append(normalized)
    if str(compiled_handoff.get("mode") or "") == "authoritative":
        for ref in list(compiled_envelope.get("additional_context_refs") or []):
            normalized = str(ref or "").strip()
            if normalized and normalized not in compiled_forbidden_refs and normalized not in merged_additional_context_refs:
                merged_additional_context_refs.append(normalized)
    for ref in list(spec.get("additional_context_refs") or []):
        normalized = str(ref or "").strip()
        if normalized and normalized not in compiled_forbidden_refs and normalized not in merged_additional_context_refs:
            merged_additional_context_refs.append(normalized)
    forbidden_context_refs = (
        [str(ref).strip() for ref in list(compiled_envelope.get("forbidden_context_refs") or []) if str(ref).strip()]
        if str(compiled_handoff.get("mode") or "") == "authoritative"
        else list(spec.get("forbidden_context_refs") or [])
    )
    from infra.controlplane import builder_turn_contract as _builder_turn_contract
    from infra.controlplane import verifier_review_contract as _verifier_review_contract

    builder_contract = _builder_turn_contract.builder_turn_contract_v1()
    verifier_contract = _verifier_review_contract.verifier_review_contract_v1()
    raw_ir = {
        "ir_version": _compiled_execution_ir.IR_VERSION,
        "task_id": str(spec.get("job_id") or "").strip(),
        "title": str(spec.get("display_title") or spec.get("job_id") or "").strip(),
        "goal": goal,
        "constraints": [
            f"binding:{str(binding.get('type') or '').strip()}:{str(binding.get('id') or '').strip()}",
            f"executor_agent_tech:{str(runtime.get('executor_agent_tech') or '').strip()}",
            f"compiled_work_order_mode:{str(compiled_handoff.get('mode') or 'compatibility_fallback')}",
        ],
        "required_context_bundle": (
            str(compiled_envelope.get("required_context_bundle") or "").strip()
            if str(compiled_handoff.get("mode") or "") == "authoritative"
            else str(spec.get("required_context_bundle") or "").strip()
        ),
        "additional_context_refs": merged_additional_context_refs,
        "forbidden_context_refs": forbidden_context_refs,
        "required_output_kind": str(expected_output.get("kind") or "").strip(),
        "allowed_files": allowed_files,
        "validation_plan": validation_plan,
        "claim_scope": claim_scope,
        "mode": mode,
        "execution_family": execution_family_ir,
        "metadata": {
            "job_kind": job_kind,
            "job_shape": str(job_shape_fields.get("job_shape") or ""),
            "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
            "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
            "role": role,
            "binding_type": str(binding.get("type") or "").strip(),
            "repo_change_mode": repo_change_mode or "none",
            "executor_agent_tech": str(runtime.get("executor_agent_tech") or "").strip(),
            "compiled_execution_mode": str(runtime.get("compiled_execution_mode") or "").strip().lower(),
            "project_id": str(project_run.get("project_id") or "").strip(),
            "runner_id": str(project_run.get("runner_id") or "").strip(),
            "execution_family_contract_kind": str(execution_family_contract.get("contract_kind") or "").strip(),
            "execution_family_parent_uid": str(execution_family_contract.get("parent_uid") or "").strip(),
            "execution_family_child_uid": str(execution_family_contract.get("child_uid") or "").strip(),
            "execution_family_child_kind": str(execution_family_contract.get("child_kind") or "").strip(),
            "execution_family_source_intent_uid": str(execution_family_contract.get("source_intent_uid") or "").strip(),
            "execution_family_required_final_child_kind": str(execution_family_contract.get("required_final_child_kind") or "").strip(),
            "execution_family_required_final_child_uid": str(execution_family_contract.get("required_final_child_uid") or "").strip(),
            "execution_family_closeout_contract_kind": str(execution_family_closeout.get("contract_kind") or "").strip(),
            "execution_family_closeout_parent_uid": str(execution_family_closeout.get("parent_uid") or "").strip(),
            "execution_family_closeout_source_intent_uid": str(execution_family_closeout.get("source_intent_uid") or "").strip(),
            "execution_family_closeout_required_final_child_uid": str(execution_family_closeout.get("required_final_child_uid") or "").strip(),
            "execution_family_closeout_integration_audit_mode": str(execution_family_closeout.get("integration_audit_mode") or "").strip(),
            "compiled_work_order_mode": str(compiled_handoff.get("mode") or "compatibility_fallback"),
            "compiled_work_order_status": str(compiled_handoff.get("status") or ""),
            "compiled_work_order_artifact_rel": str(compiled_handoff.get("artifact_rel") or ""),
            "compiled_work_order_fallback_reason": str(compiled_handoff.get("fallback_reason") or ""),
            "compiled_work_order_reason_codes": json.dumps(list(compiled_handoff.get("reason_codes") or []), sort_keys=True, ensure_ascii=False),
            "compiled_work_order_handoff_kind": str(compiled_envelope.get("handoff_kind") or ""),
            "compiled_work_order_precedence": str(compiled_handoff.get("precedence") or ""),
            "compiled_work_order_effective_path": str(compiled_handoff.get("effective_path") or ""),
            "compiled_work_order_scope_objective": str((((compiled_envelope.get("scope_fence") if isinstance(compiled_envelope.get("scope_fence"), dict) else {}) or {}).get("objective")) or ""),
            "compiled_work_order_scope_in_scope": json.dumps(list((((compiled_envelope.get("scope_fence") if isinstance(compiled_envelope.get("scope_fence"), dict) else {}) or {}).get("in_scope") or [])), sort_keys=True, ensure_ascii=False),
            "compiled_work_order_scope_out_of_scope": json.dumps(list((((compiled_envelope.get("scope_fence") if isinstance(compiled_envelope.get("scope_fence"), dict) else {}) or {}).get("out_of_scope") or [])), sort_keys=True, ensure_ascii=False),
            "compiled_work_order_required_context_bundle": str(compiled_envelope.get("required_context_bundle") or ""),
            "compiled_work_order_validation_checks": json.dumps(list((((compiled_envelope.get("validation_expectations") if isinstance(compiled_envelope.get("validation_expectations"), dict) else {}) or {}).get("required_checks") or [])), sort_keys=True, ensure_ascii=False),
            "compiled_work_order_expected_outputs": json.dumps(list(compiled_envelope.get("expected_outputs") or []), sort_keys=True, ensure_ascii=False),
            "builder_contract_version": builder_contract.version,
            "builder_result_packet_version": _builder_turn_contract.RESULT_PACKET_VERSION,
            "builder_contract_doc_rel": builder_contract.canonical_doc_rel.as_posix(),
            "builder_contract_revision": _builder_turn_contract.builder_turn_contract_revision(),
            "verifier_contract_doc_rel": verifier_contract.canonical_doc_rel.as_posix(),
            "verifier_contract_revision": _verifier_review_contract.verifier_review_contract_revision(),
            "verifier_contract_version": verifier_contract.version if str(runtime.get("compiled_execution_mode") or "").strip().lower() in {"soft_gate", "hard_gate"} else "",
            "verifier_result_packet_version": _verifier_review_contract.RESULT_PACKET_VERSION,
        },
    }
    return _compiled_execution_ir.validate_execution_ir(raw_ir)


def _spec_with_generated_attachment_ref(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    attachment_ref = _continuation_context_attachment.context_attachment_ref_for_job_spec(
        spec,
        repo_root=Path(repo_root or _repo_root()).resolve(),
    )
    if not attachment_ref:
        return dict(spec)
    hydrated = dict(spec)
    additional_context_refs = list(hydrated.get("additional_context_refs") or [])
    if attachment_ref not in additional_context_refs:
        hydrated["additional_context_refs"] = additional_context_refs + [attachment_ref]
    return hydrated


def compiled_context_manifest_from_job_spec(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
    max_refs: int = _compiled_execution_manifest.DEFAULT_MAX_REFS,
) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    hydrated_spec = _spec_with_generated_attachment_ref(_spec_with_compiled_work_order_contract(spec, repo_root=root), repo_root=root)
    ir_result = compiled_execution_ir_from_job_spec(hydrated_spec)
    runtime = _normalize_runtime(spec.get("runtime"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    strict_required_refs = _compiled_execution_mode_for_job(spec, runtime) == _compiled_execution_contract.ROLLOUT_HARD_GATE
    result = _compiled_execution_manifest.resolve_context_manifest(
        ir_result,
        repo_root=root,
        max_refs=max_refs,
        strict_required_refs=strict_required_refs,
    )
    if bool(result.get("ok")):
        manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else {}
        bootstrap = _continuation_context_attachment.continuation_bootstrap_projection_for_job_spec(
            hydrated_spec,
            repo_root=root,
        )
        if str(bootstrap.get("status") or "").strip():
            manifest["continuation_bootstrap"] = bootstrap
    return result


def compiled_prompt_artifact_from_job_spec(
    spec: Dict[str, Any],
    *,
    role: str,
    repo_root: Optional[Path] = None,
    max_refs: int = _compiled_execution_manifest.DEFAULT_MAX_REFS,
) -> Dict[str, Any]:
    hydrated_spec = _spec_with_generated_attachment_ref(spec, repo_root=repo_root)
    ir_result = compiled_execution_ir_from_job_spec(hydrated_spec)
    if not bool(ir_result.get("ok")):
        return {
            "ok": False,
            "failure_class": "compiled_prompt_blocked_by_invalid_ir",
            "errors": list(ir_result.get("errors") or []),
        }
    manifest_result = compiled_context_manifest_from_job_spec(hydrated_spec, repo_root=repo_root, max_refs=max_refs)
    if not bool(manifest_result.get("ok")):
        return {
            "ok": False,
            "failure_class": str(manifest_result.get("failure_class") or "compiled_prompt_blocked_by_manifest"),
            "errors": list(manifest_result.get("errors") or []),
        }
    artifact = _compiled_prompt_renderer.render_compiled_prompt(
        role=role,
        normalized_ir=ir_result["normalized_ir"],
        manifest=manifest_result["manifest"],
    )
    return {
        "ok": True,
        "ir": ir_result,
        "manifest": manifest_result,
        "artifact": artifact,
    }


def _compiled_execution_mode_for_job(spec: Dict[str, Any], runtime: Dict[str, Any]) -> str:
    requested = str((runtime or {}).get("compiled_execution_mode") or "").strip().lower()
    job_kind = _normalize_job_kind(spec.get("job_kind"))
    task_class = f"scheduled_{job_kind}"
    if requested == _compiled_execution_contract.ROLLOUT_HARD_GATE:
        return _compiled_execution_contract.ROLLOUT_HARD_GATE
    if requested:
        return requested
    return _compiled_execution_contract.compiled_execution_rollout_mode_for_task_class(task_class)
