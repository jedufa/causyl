def _execution_family_closeout_contract(spec: Dict[str, Any]) -> Dict[str, Any]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    contract = (
        prompt_meta.get("execution_family_closeout")
        if isinstance(prompt_meta.get("execution_family_closeout"), dict)
        else {}
    )
    return dict(contract)


def _execution_family_closeout_item_dir(
    uid: str,
    *,
    is_intent: bool = False,
    repo_root: Optional[Path] = None,
) -> Optional[Path]:
    normalized = str(uid or "").strip()
    if not normalized:
        return None
    if not is_intent:
        return _resolve_ledger_item_dir_for_uid(normalized, repo_root=repo_root)
    root = Path(repo_root or _repo_root()).resolve()
    matches = sorted((root / "governance" / "ledger" / "intent_sheets").glob(f"**/{normalized}__*/"))
    return matches[0] if matches else None


def _execution_family_closeout_events(item_dir: Optional[Path]) -> List[Dict[str, Any]]:
    if item_dir is None:
        return []
    events_path = (item_dir / "events.jsonl").resolve()
    if not events_path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for raw in events_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        raw = str(raw or "").strip()
        if not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _execution_family_closeout_latest_event(events: List[Dict[str, Any]], event_type: str) -> Dict[str, Any]:
    for event in reversed(events):
        if str(event.get("type") or "").strip() == event_type:
            return dict(event)
    return {}


def _execution_family_closeout_evidence_path_values(evidence: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    for raw in evidence.values():
        if not isinstance(raw, str):
            continue
        normalized = raw.strip()
        if not normalized or "/" not in normalized:
            continue
        out.append(normalized)
    return out


def _execution_family_closeout_events_chain_error(item_dir: Optional[Path]) -> str:
    if item_dir is None:
        return ""
    events_path = (item_dir / "events.jsonl").resolve()
    if not events_path.exists():
        return ""
    prev_hash = ""
    for index, raw in enumerate(events_path.read_text(encoding="utf-8", errors="ignore").splitlines(), start=1):
        normalized = str(raw or "").strip()
        if not normalized:
            continue
        try:
            payload = json.loads(normalized)
        except Exception:
            return f"line_{index}_invalid_json"
        if not isinstance(payload, dict):
            return f"line_{index}_invalid_payload"
        if str(payload.get("schema_version") or "").strip() != "ledger_event_v1":
            continue
        row_prev_hash = str(payload.get("prev_hash") or "")
        if row_prev_hash != prev_hash:
            return f"line_{index}_prev_hash_mismatch"
        row_hash = str(payload.get("hash") or "")
        row_without_hash = dict(payload)
        row_without_hash.pop("hash", None)
        expected_hash = hashlib.sha256(
            row_prev_hash.encode("utf-8")
            + json.dumps(
                row_without_hash,
                sort_keys=True,
                separators=(",", ":"),
                ensure_ascii=False,
            ).encode("utf-8")
        ).hexdigest()
        if row_hash != expected_hash:
            return f"line_{index}_hash_mismatch"
        prev_hash = row_hash
    return ""


def _execution_family_closeout_relocated_evidence_path(
    path_value: str,
    *,
    repo_root: Optional[Path] = None,
) -> str:
    normalized = str(path_value or "").strip().replace("\\", "/").lstrip("./")
    if not normalized:
        return ""
    if "/governance/ledger/" in normalized:
        normalized = "governance/ledger/" + normalized.split("/governance/ledger/", 1)[-1]
    if "governance/ledger/" not in normalized:
        normalized = normalized if normalized.startswith("governance/ledger/") else normalized
    if "governance/ledger/" not in normalized:
        return ""
    uid_match = re.search(r"((?:PIS|IS)-\d{8}(?:-\d{6}Z)?-[A-Z0-9]{4,})__", normalized)
    if uid_match is None:
        return ""
    uid = str(uid_match.group(1) or "").strip()
    if not uid:
        return ""
    slug_marker = f"{uid}__"
    if slug_marker not in normalized:
        return ""
    suffix = normalized.split(slug_marker, 1)[-1]
    if "/" not in suffix:
        return ""
    relative_suffix = suffix.split("/", 1)[-1].strip()
    if not relative_suffix:
        return ""
    root = Path(repo_root or _repo_root()).resolve()
    if uid.startswith("IS-"):
        search_root = root / "governance" / "ledger" / "intent_sheets"
    else:
        search_root = _ledger_storage.ledger_patch_instruction_sheets_root(root)
    if not search_root.exists():
        return ""
    matches = sorted(search_root.glob(f"**/{uid}__*"))
    for target_dir in matches:
        if not target_dir.is_dir():
            continue
        candidate = (target_dir / relative_suffix).resolve()
        if not candidate.exists():
            continue
        try:
            return str(candidate.relative_to(root)).replace("\\", "/")
        except ValueError:
            return str(candidate).replace("\\", "/")
    return ""


def _execution_family_closeout_evidence_path_exists(path_value: str, *, item_dir: Path, repo_root: Optional[Path]) -> bool:
    normalized = str(path_value or "").strip()
    if not normalized:
        return False
    item_candidate = (item_dir / normalized).resolve()
    if item_candidate.exists():
        return True
    root = Path(repo_root or _repo_root()).resolve()
    repo_candidate = (root / normalized).resolve()
    return repo_candidate.exists()


def _execution_family_child_handoff_policy(
    *,
    child_uid: str,
    parent_uid: str = "",
    required_final_child_uid: str = "",
    intent_validation_child_uid: str = "",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    normalized_child_uid = str(child_uid or "").strip()
    normalized_parent_uid = str(parent_uid or "").strip()
    normalized_final_uid = str(required_final_child_uid or "").strip()
    normalized_validation_uid = str(intent_validation_child_uid or "").strip()
    if normalized_parent_uid and (not normalized_final_uid or not normalized_validation_uid):
        parent_dir = _execution_family_closeout_item_dir(normalized_parent_uid, repo_root=repo_root)
        if parent_dir is not None:
            parent_meta = _read_json((parent_dir / "meta.json").resolve())
            integration = (
                parent_meta.get("integration_meta")
                if isinstance(parent_meta.get("integration_meta"), dict)
                else {}
            )
            if not normalized_final_uid:
                normalized_final_uid = str(integration.get("required_final_child_uid") or "").strip()
            if not normalized_validation_uid:
                normalized_validation_uid = str(integration.get("intent_validation_child_uid") or "").strip()
    relationship = "implementation_child"
    if normalized_child_uid and normalized_child_uid == normalized_final_uid:
        relationship = "intent_family_closeout"
    elif normalized_child_uid and normalized_child_uid == normalized_validation_uid:
        relationship = "intent_validation_child"
    requires_changesets = relationship == "implementation_child"
    return {
        "relationship": relationship,
        "requires_changesets": requires_changesets,
        "required_final_child_uid": normalized_final_uid,
        "intent_validation_child_uid": normalized_validation_uid,
    }


def _bound_status_reconcile_projection(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    ledger_uid = str(prompt_meta.get("ledger_truth_reconcile_for_uid") or "").strip()
    applies = bool(ledger_uid)
    if not applies:
        return {"applies": False, "ledger_uid": "", "status": "", "meta_path": ""}
    item_dir = _resolve_ledger_item_dir_for_uid(ledger_uid, repo_root=repo_root)
    meta_path = (item_dir / "meta.json").resolve() if item_dir is not None else None
    meta = _read_json(meta_path) if meta_path is not None and meta_path.exists() else {}
    return {
        "applies": True,
        "ledger_uid": ledger_uid,
        "status": str(meta.get("status") or "").strip().lower(),
        "meta_path": str(meta_path) if meta_path is not None else "",
    }


def _bound_ledger_item_projection(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    ledger_uid = str(binding.get("id") or "").strip() if str(binding.get("type") or "").strip() == "ledger_uid" else ""
    if not ledger_uid:
        return {"applies": False, "ledger_uid": "", "status": "", "meta_path": ""}
    item_dir = _resolve_ledger_item_dir_for_uid(ledger_uid, repo_root=repo_root)
    meta_path = (item_dir / "meta.json").resolve() if item_dir is not None else None
    meta = _read_json(meta_path) if meta_path is not None and meta_path.exists() else {}
    return {
        "applies": True,
        "ledger_uid": ledger_uid,
        "status": str(meta.get("status") or "").strip().lower(),
        "meta_path": str(meta_path) if meta_path is not None else "",
    }


def _changesets_from_receipt_path(receipt_path: Path, *, repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    if not receipt_path.exists():
        return []
    receipt = _read_json(receipt_path)
    declared_changed_files = _collect_receipt_declared_changed_files(receipt, repo_root=repo_root)
    if not declared_changed_files:
        repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
        for relpath in list(repo_residue.get("declared_changed_files") or []):
            normalized = _normalize_repo_like_path(str(relpath or ""), repo_root=repo_root)
            if normalized and normalized not in declared_changed_files:
                declared_changed_files.append(normalized)
    if not declared_changed_files:
        return []
    run_id = str(receipt.get("run_id") or "").strip()
    summary = "Synthesized from successful scheduled receipt changed-files evidence"
    if run_id:
        summary += f" ({run_id})"
    return [
        {
            "kind": "implementation",
            "summary": summary,
            "files": list(declared_changed_files),
            "source": "scheduled_receipt_declared_changed_files_v1",
        }
    ]


def _backfill_family_child_changesets_from_success_receipt(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    ledger_uid = str(binding.get("id") or "").strip() if str(binding.get("type") or "").strip() == "ledger_uid" else ""
    parent_uid = str(prompt_meta.get("generated_from_parent_uid") or "").strip()
    if not ledger_uid or not parent_uid:
        return {"ok": False, "applies": False, "reason": "not_generated_family_child"}
    policy = _execution_family_child_handoff_policy(child_uid=ledger_uid, parent_uid=parent_uid, repo_root=repo_root)
    if not bool(policy.get("requires_changesets")):
        return {
            "ok": False,
            "applies": True,
            "reason": "changesets_not_required",
            "relationship": str(policy.get("relationship") or ""),
        }
    item_dir = _resolve_ledger_item_dir_for_uid(ledger_uid, repo_root=repo_root)
    if item_dir is None:
        return {"ok": False, "applies": True, "reason": "ledger_item_missing", "ledger_uid": ledger_uid}
    meta = _read_json((item_dir / "meta.json").resolve())
    status = str(meta.get("status") or "").strip().lower()
    evidence = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
    changesets = meta.get("changesets") if isinstance(meta.get("changesets"), list) else []
    if status != "verified":
        return {"ok": False, "applies": True, "reason": f"ledger_status_{status or 'missing'}", "ledger_uid": ledger_uid}
    if not evidence:
        return {"ok": False, "applies": True, "reason": "ledger_evidence_missing", "ledger_uid": ledger_uid}
    if changesets:
        return {"ok": True, "applies": True, "reason": "changesets_already_present", "ledger_uid": ledger_uid}
    state = load_state(str(spec.get("job_id") or "").strip(), repo_root=repo_root)
    if not _is_success_status(state.get("last_status")):
        return {"ok": False, "applies": True, "reason": "job_not_success", "ledger_uid": ledger_uid}
    receipt_path = Path(str(state.get("last_receipt_path") or "").strip())
    synthesized = _changesets_from_receipt_path(receipt_path, repo_root=repo_root)
    if not synthesized:
        return {
            "ok": False,
            "applies": True,
            "reason": "receipt_changed_files_missing",
            "ledger_uid": ledger_uid,
            "receipt_path": str(receipt_path),
        }
    from infra.webui.ledger.ledger_manager import ChangeLedger

    ledger = ChangeLedger(repo_root=Path(repo_root or _repo_root()).resolve())
    ledger.update_meta(
        ledger_uid,
        {
            "changesets": synthesized,
        },
    )
    return {
        "ok": True,
        "applies": True,
        "reason": "changesets_backfilled_from_receipt",
        "ledger_uid": ledger_uid,
        "relationship": str(policy.get("relationship") or ""),
        "receipt_path": str(receipt_path),
        "changesets": synthesized,
    }


def _execution_family_closeout_truth_check(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    contract = _execution_family_closeout_contract(spec)
    if not contract:
        return {"ok": True, "errors": [], "details": {}}

    errors: List[str] = []
    details: Dict[str, Any] = {
        "parent_uid": str(contract.get("parent_uid") or "").strip(),
        "source_intent_uid": str(contract.get("source_intent_uid") or "").strip(),
        "required_final_child_uid": str(contract.get("required_final_child_uid") or "").strip(),
        "intent_validation_child_uid": "",
        "child_uids": [str(item).strip() for item in list(contract.get("child_uids") or []) if str(item).strip()],
        "child_job_ids": [str(item).strip() for item in list(contract.get("child_job_ids") or []) if str(item).strip()],
        "child_job_statuses": {},
        "child_statuses": {},
    }

    for job_id in details["child_job_ids"]:
        state = load_state(job_id, repo_root=repo_root)
        last_status = str(state.get("last_status") or "").strip()
        details["child_job_statuses"][job_id] = last_status
        if not _is_success_status(last_status):
            errors.append(f"child_job_not_success:{job_id}:{last_status or 'missing'}")
            continue
        receipt_path = Path(str(state.get("last_receipt_path") or "").strip())
        if not receipt_path.exists():
            errors.append(f"child_job_missing_receipt:{job_id}")

    required_final_child_uid = str(details["required_final_child_uid"] or "").strip()

    for child_uid in details["child_uids"]:
        item_dir = _execution_family_closeout_item_dir(child_uid, repo_root=repo_root)
        if item_dir is None:
            errors.append(f"child_meta_missing:{child_uid}")
            continue
        meta_path = (item_dir / "meta.json").resolve()
        if not meta_path.exists():
            errors.append(f"child_meta_missing:{child_uid}")
            continue
        meta = _read_json(meta_path)
        status = str(meta.get("status") or "").strip()
        details["child_statuses"][child_uid] = status
        event_chain_error = _execution_family_closeout_events_chain_error(item_dir)
        if event_chain_error:
            errors.append(f"child_event_chain_invalid:{child_uid}:{event_chain_error}")
        if status != "verified":
            errors.append(f"child_not_verified:{child_uid}:{status or 'missing'}")
        evidence = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
        changesets = meta.get("changesets") if isinstance(meta.get("changesets"), list) else []
        handoff_policy = _execution_family_child_handoff_policy(
            child_uid=child_uid,
            parent_uid=str(details.get("parent_uid") or ""),
            required_final_child_uid=required_final_child_uid,
            intent_validation_child_uid=str(details.get("intent_validation_child_uid") or ""),
            repo_root=repo_root,
        )
        details.setdefault("child_evidence_ready", {})[child_uid] = bool(evidence)
        details.setdefault("child_changesets_ready", {})[child_uid] = bool(changesets)
        details.setdefault("child_handoff_relationships", {})[child_uid] = str(handoff_policy.get("relationship") or "")
        if child_uid != required_final_child_uid:
            if not evidence:
                errors.append(f"child_missing_evidence:{child_uid}")
            if bool(handoff_policy.get("requires_changesets")) and not changesets:
                errors.append(f"child_missing_changesets:{child_uid}")

    if required_final_child_uid:
        if required_final_child_uid not in details["child_uids"]:
            errors.append(f"required_final_child_not_declared:{required_final_child_uid}")
        final_dir = _execution_family_closeout_item_dir(required_final_child_uid, repo_root=repo_root)
        if final_dir is None:
            errors.append(f"required_final_child_missing:{required_final_child_uid}")
        else:
            meta = _read_json((final_dir / "meta.json").resolve())
            final_status = str(meta.get("status") or "").strip()
            details["required_final_child_status"] = final_status
            if final_status != "verified":
                errors.append(f"required_final_child_not_verified:{required_final_child_uid}:{final_status or 'missing'}")
            required_evidence = [str(item).strip() for item in list(meta.get("required_evidence") or []) if str(item).strip()]
            evidence_map = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
            for rel_path in required_evidence:
                if not (final_dir / rel_path).resolve().exists():
                    errors.append(f"required_final_child_missing_evidence:{required_final_child_uid}:{rel_path}")
            for normalized in _execution_family_closeout_evidence_path_values(evidence_map):
                if not _execution_family_closeout_evidence_path_exists(normalized, item_dir=final_dir, repo_root=repo_root):
                    relocated = _execution_family_closeout_relocated_evidence_path(normalized, repo_root=repo_root)
                    if relocated:
                        errors.append(
                            "required_final_child_stale_evidence_ref:"
                            f"{required_final_child_uid}:{normalized}->{relocated}"
                        )
                        continue
                    errors.append(f"required_final_child_missing_evidence:{required_final_child_uid}:{normalized}")
            events = _execution_family_closeout_events(final_dir)
            event_chain_error = _execution_family_closeout_events_chain_error(final_dir)
            if event_chain_error:
                errors.append(f"required_final_child_event_chain_invalid:{required_final_child_uid}:{event_chain_error}")
            relevant_types = {
                "meta_updated",
                "status_reconcile_reviewed",
                "verification_checked",
                "status_changed",
                "ledger_status_changed",
            }
            relevant_utcs = [str(event.get("utc") or "").strip() for event in events if str(event.get("type") or "").strip() in relevant_types]
            if relevant_utcs and relevant_utcs != sorted(relevant_utcs):
                errors.append(f"required_final_child_event_chronology_invalid:{required_final_child_uid}")
            latest_status_event = _execution_family_closeout_latest_event(events, "status_changed")
            latest_ledger_status_event = _execution_family_closeout_latest_event(events, "ledger_status_changed")
            latest_verification_event = _execution_family_closeout_latest_event(events, "verification_checked")
            if latest_status_event:
                latest_status = str(((latest_status_event.get("payload") if isinstance(latest_status_event.get("payload"), dict) else {}).get("status")) or "").strip()
                if latest_status != "verified":
                    errors.append(f"required_final_child_status_event_not_verified:{required_final_child_uid}:{latest_status or 'missing'}")
            if latest_ledger_status_event:
                latest_ledger_status = str(((latest_ledger_status_event.get("payload") if isinstance(latest_ledger_status_event.get("payload"), dict) else {}).get("status")) or "").strip()
                if latest_ledger_status != "verified":
                    errors.append(f"required_final_child_ledger_status_event_not_verified:{required_final_child_uid}:{latest_ledger_status or 'missing'}")
            if latest_verification_event:
                latest_verification_result = str(((latest_verification_event.get("payload") if isinstance(latest_verification_event.get("payload"), dict) else {}).get("result")) or "").strip()
                if latest_verification_result != "pass":
                    errors.append(f"required_final_child_verification_event_not_pass:{required_final_child_uid}:{latest_verification_result or 'missing'}")
            updated_utc = str(meta.get("updated_utc") or "").strip()
            if updated_utc and relevant_utcs and updated_utc != relevant_utcs[-1]:
                errors.append(f"required_final_child_updated_utc_mismatch:{required_final_child_uid}")

    parent_uid = str(details["parent_uid"] or "").strip()
    if parent_uid:
        parent_dir = _execution_family_closeout_item_dir(parent_uid, repo_root=repo_root)
        if parent_dir is None:
            errors.append(f"parent_meta_missing:{parent_uid}")
        else:
            parent_meta = _read_json((parent_dir / "meta.json").resolve())
            parent_status = str(parent_meta.get("status") or "").strip()
            parent_integration = (
                parent_meta.get("integration_meta")
                if isinstance(parent_meta.get("integration_meta"), dict)
                else {}
            )
            details["parent_status"] = parent_status
            details["intent_validation_child_uid"] = str(parent_integration.get("intent_validation_child_uid") or "").strip()
            parent_event_chain_error = _execution_family_closeout_events_chain_error(parent_dir)
            if parent_event_chain_error:
                errors.append(f"parent_event_chain_invalid:{parent_uid}:{parent_event_chain_error}")
            if parent_status != "verified":
                errors.append(f"parent_not_verified:{parent_uid}:{parent_status or 'missing'}")

    source_intent_uid = str(details["source_intent_uid"] or "").strip()
    if source_intent_uid:
        intent_dir = _execution_family_closeout_item_dir(source_intent_uid, is_intent=True, repo_root=repo_root)
        if intent_dir is None:
            errors.append(f"source_intent_missing:{source_intent_uid}")
        else:
            intent_meta = _read_json((intent_dir / "meta.json").resolve())
            intent_status = str(intent_meta.get("status") or "").strip()
            details["source_intent_status"] = intent_status
            if intent_status != "verified":
                errors.append(f"source_intent_not_verified:{source_intent_uid}:{intent_status or 'missing'}")
            validating_target_uid = str(details.get("intent_validation_child_uid") or "").strip() or required_final_child_uid
            if validating_target_uid:
                validating = [str(item).strip() for item in list(intent_meta.get("validating_pis_uids") or []) if str(item).strip()]
                details["source_intent_validating_pis_uids"] = list(validating)
                if validating_target_uid not in validating:
                    errors.append(f"source_intent_missing_validating_pis:{source_intent_uid}:{validating_target_uid}")

    return {"ok": not errors, "errors": errors, "details": details}


def _ledger_bound_family_truth_projection(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    ledger_uid = str(binding.get("id") or "").strip() if str(binding.get("type") or "").strip() == "ledger_uid" else ""
    generated_from_parent_uid = str(prompt_meta.get("generated_from_parent_uid") or "").strip()
    generated_by_reconcile = bool(prompt_meta.get("generated_by_closeout_truth_reconcile"))
    applies = bool(ledger_uid) and bool(generated_from_parent_uid or generated_by_reconcile)
    if not applies:
        return {
            "applies": False,
            "ledger_uid": ledger_uid,
            "generated_from_parent_uid": generated_from_parent_uid,
            "generated_by_reconcile": generated_by_reconcile,
            "ready": True,
            "status": "",
            "has_evidence": False,
            "has_changesets": False,
            "meta_path": "",
            "missing_reasons": [],
        }
    handoff_policy = _execution_family_child_handoff_policy(
        child_uid=ledger_uid,
        parent_uid=generated_from_parent_uid,
        repo_root=repo_root,
    )
    backfill_result = _backfill_family_child_changesets_from_success_receipt(spec, repo_root=repo_root)
    item_dir = _resolve_ledger_item_dir_for_uid(ledger_uid, repo_root=repo_root)
    meta_path = (item_dir / "meta.json").resolve() if item_dir is not None else None
    meta = _read_json(meta_path) if meta_path is not None and meta_path.exists() else {}
    evidence = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
    changesets = meta.get("changesets") if isinstance(meta.get("changesets"), list) else []
    status = str(meta.get("status") or "").strip().lower()
    has_evidence = bool(evidence)
    has_changesets = bool(changesets)
    missing_reasons: List[str] = []
    if status != "verified":
        missing_reasons.append(f"ledger_status_{status or 'missing'}")
    if not has_evidence:
        missing_reasons.append("ledger_evidence_missing")
    if bool(handoff_policy.get("requires_changesets")) and not has_changesets:
        missing_reasons.append("ledger_changesets_missing")
    return {
        "applies": True,
        "ledger_uid": ledger_uid,
        "generated_from_parent_uid": generated_from_parent_uid,
        "generated_by_reconcile": generated_by_reconcile,
        "ready": not missing_reasons,
        "status": status,
        "has_evidence": has_evidence,
        "has_changesets": has_changesets,
        "handoff_relationship": str(handoff_policy.get("relationship") or ""),
        "requires_changesets": bool(handoff_policy.get("requires_changesets")),
        "changeset_backfill_result": dict(backfill_result),
        "meta_path": str(meta_path) if meta_path is not None else "",
        "missing_reasons": missing_reasons,
    }


def _execution_family_closeout_allowed_promotion_error(error: str, *, details: Dict[str, Any]) -> bool:
    text = str(error or "").strip()
    if not text:
        return False
    required_final_child_uid = str(details.get("required_final_child_uid") or "").strip()
    parent_uid = str(details.get("parent_uid") or "").strip()
    source_intent_uid = str(details.get("source_intent_uid") or "").strip()
    intent_validation_child_uid = str(details.get("intent_validation_child_uid") or "").strip()
    allowed_prefixes = []
    if required_final_child_uid:
        allowed_prefixes.extend(
            [
                f"child_not_verified:{required_final_child_uid}:",
                f"required_final_child_not_verified:{required_final_child_uid}:",
            ]
        )
    if parent_uid:
        allowed_prefixes.append(f"parent_not_verified:{parent_uid}:")
    if source_intent_uid:
        allowed_prefixes.append(f"source_intent_not_verified:{source_intent_uid}:")
        if intent_validation_child_uid:
            allowed_prefixes.append(
                f"source_intent_missing_validating_pis:{source_intent_uid}:{intent_validation_child_uid}"
            )
    return any(text.startswith(prefix) for prefix in allowed_prefixes)


def _execution_family_closeout_ready_for_machine_promotion(closeout_truth: Dict[str, Any]) -> bool:
    if bool(closeout_truth.get("ok")):
        return True
    details = closeout_truth.get("details") if isinstance(closeout_truth.get("details"), dict) else {}
    errors = [str(item).strip() for item in list(closeout_truth.get("errors") or []) if str(item).strip()]
    if not errors:
        return False
    return all(_execution_family_closeout_allowed_promotion_error(error, details=details) for error in errors)


def _execution_family_closeout_promote_verified_family(
    spec: Dict[str, Any],
    *,
    closeout_truth: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    contract = _execution_family_closeout_contract(spec)
    if not contract:
        return {"ok": True, "promoted": [], "reason": "not_execution_family_closeout"}
    if not _execution_family_closeout_ready_for_machine_promotion(closeout_truth):
        return {
            "ok": False,
            "promoted": [],
            "reason": "blocking_closeout_truth_errors",
            "errors": list(closeout_truth.get("errors") or []),
        }

    from infra.webui.ledger.intent_sheet_manager import IntentSheetLedger
    from infra.webui.ledger.ledger_manager import ChangeLedger

    root = Path(repo_root or _repo_root()).resolve()
    details = closeout_truth.get("details") if isinstance(closeout_truth.get("details"), dict) else {}
    required_final_child_uid = str(details.get("required_final_child_uid") or contract.get("required_final_child_uid") or "").strip()
    parent_uid = str(details.get("parent_uid") or contract.get("parent_uid") or "").strip()
    source_intent_uid = str(details.get("source_intent_uid") or contract.get("source_intent_uid") or "").strip()
    intent_validation_child_uid = str(
        details.get("intent_validation_child_uid") or contract.get("intent_validation_child_uid") or ""
    ).strip()
    promoted: List[str] = []
    ledger = ChangeLedger(repo_root=root)
    note = f"execution_family_closeout:{str(spec.get('job_id') or '').strip()}"

    def _promote_pis(uid: str, label: str) -> None:
        if not uid:
            return
        item = ledger.read_item(uid)
        status = str(((item.get("meta") if isinstance(item.get("meta"), dict) else {}) or {}).get("status") or "").strip().lower()
        if status == "verified":
            return
        ledger.update_status(uid, "verified", note=note)
        promoted.append(f"{label}:{uid}")

    _promote_pis(required_final_child_uid, "final_child")
    _promote_pis(parent_uid, "parent")

    if source_intent_uid:
        intent_ledger = IntentSheetLedger(repo_root=root)
        intent = intent_ledger.read_sheet(source_intent_uid)
        intent_meta = intent.get("meta") if isinstance(intent.get("meta"), dict) else {}
        patch: Dict[str, Any] = {}
        validating = [str(item).strip() for item in list(intent_meta.get("validating_pis_uids") or []) if str(item).strip()]
        if intent_validation_child_uid and intent_validation_child_uid not in validating:
            patch["validating_pis_uids"] = validating + [intent_validation_child_uid]
        derived = [str(item).strip() for item in list(intent_meta.get("derived_pis_uids") or []) if str(item).strip()]
        if parent_uid and parent_uid not in derived:
            patch["derived_pis_uids"] = derived + [parent_uid]
        if patch:
            intent_ledger.update_meta(source_intent_uid, patch)
            promoted.append(f"source_intent_meta:{source_intent_uid}")
        intent_status = str(intent_meta.get("status") or "").strip().lower()
        if intent_status != "verified":
            intent_ledger.update_status(source_intent_uid, "verified", note=note)
            promoted.append(f"source_intent:{source_intent_uid}")

    if parent_uid and required_final_child_uid:
        parent_events = _execution_family_closeout_events(_execution_family_closeout_item_dir(parent_uid, repo_root=root))
        latest_reconcile = _execution_family_closeout_latest_event(parent_events, "family_parent_reconciled")
        latest_payload = latest_reconcile.get("payload") if isinstance(latest_reconcile.get("payload"), dict) else {}
        latest_child_uid = str(latest_payload.get("closeout_child_uid") or "").strip()
        latest_status = str(latest_payload.get("status") or "").strip()
        if latest_child_uid != required_final_child_uid or latest_status != "verified":
            payload = {"closeout_child_uid": required_final_child_uid, "status": "verified"}
            if source_intent_uid:
                payload["source_intent_uid"] = source_intent_uid
            if intent_validation_child_uid:
                payload["validating_pis_uid"] = intent_validation_child_uid
            ledger.append_event(parent_uid, event_type="family_parent_reconciled", payload=payload)
            promoted.append(f"parent_reconciled:{parent_uid}")

    post_check = _execution_family_closeout_truth_check(spec, repo_root=root)
    return {
        "ok": bool(post_check.get("ok")),
        "promoted": promoted,
        "reason": "promoted_and_verified" if bool(post_check.get("ok")) else "post_promotion_truth_failed",
        "post_check": post_check,
    }


def _execution_family_closeout_allowed_integrity_defect_error(error: str, *, details: Dict[str, Any]) -> bool:
    text = str(error or "").strip()
    if not text:
        return False
    parent_uid = str(details.get("parent_uid") or "").strip()
    source_intent_uid = str(details.get("source_intent_uid") or "").strip()
    intent_validation_child_uid = str(details.get("intent_validation_child_uid") or "").strip()
    if parent_uid and text.startswith(f"parent_event_chain_invalid:{parent_uid}:"):
        return True
    if (
        source_intent_uid
        and intent_validation_child_uid
        and text == f"source_intent_missing_validating_pis:{source_intent_uid}:{intent_validation_child_uid}"
    ):
        return True
    return _execution_family_closeout_allowed_promotion_error(text, details=details)


def _execution_family_closeout_formal_integrity_defect(closeout_truth: Dict[str, Any]) -> bool:
    if bool(closeout_truth.get("ok")):
        return False
    details = closeout_truth.get("details") if isinstance(closeout_truth.get("details"), dict) else {}
    errors = [str(item).strip() for item in list(closeout_truth.get("errors") or []) if str(item).strip()]
    if not errors:
        return False
    parent_uid = str(details.get("parent_uid") or "").strip()
    if not parent_uid:
        return False
    if not any(error.startswith(f"parent_event_chain_invalid:{parent_uid}:") for error in errors):
        return False
    return all(_execution_family_closeout_allowed_integrity_defect_error(error, details=details) for error in errors)


def _record_execution_family_closeout_integrity_defect(
    spec: Dict[str, Any],
    *,
    closeout_truth: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    contract = _execution_family_closeout_contract(spec)
    details = closeout_truth.get("details") if isinstance(closeout_truth.get("details"), dict) else {}
    root = Path(repo_root or _repo_root()).resolve()
    parent_uid = str(details.get("parent_uid") or contract.get("parent_uid") or "").strip()
    required_final_child_uid = str(
        details.get("required_final_child_uid") or contract.get("required_final_child_uid") or ""
    ).strip()
    source_intent_uid = str(details.get("source_intent_uid") or contract.get("source_intent_uid") or "").strip()
    errors = [str(item).strip() for item in list(closeout_truth.get("errors") or []) if str(item).strip()]
    summary = (
        "Execution family completed, but closeout integrity remains defective because the parent event hash chain is broken."
    )
    outcome_payload = {
        "status": EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS,
        "closeout_decision": "executed_with_integrity_defect",
        "status_recommendation": EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS,
        "residual_gap": summary,
        "closeout_truth_errors": list(errors),
        "closeout_truth_job_id": str(spec.get("job_id") or "").strip(),
    }
    updated_meta_paths: List[str] = []

    def _update_item_meta(item_dir: Optional[Path], *, is_parent: bool = False) -> None:
        if item_dir is None:
            return
        meta_path = (item_dir / "meta.json").resolve()
        if not meta_path.exists():
            return
        meta = _read_json(meta_path)
        evidence = meta.get("evidence") if isinstance(meta.get("evidence"), dict) else {}
        merged_evidence = dict(evidence)
        merged_evidence.update(outcome_payload)
        meta["evidence"] = merged_evidence
        if is_parent:
            integration = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), dict) else {}
            merged_integration = dict(integration)
            merged_integration["family_execution_status"] = EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS
            merged_integration["family_execution_residual_gap"] = summary
            meta["integration_meta"] = merged_integration
        _write_json_atomic(meta_path, meta)
        updated_meta_paths.append(str(meta_path.relative_to(root)).replace("\\", "/"))

    _update_item_meta(_execution_family_closeout_item_dir(parent_uid, repo_root=root), is_parent=True)
    _update_item_meta(_execution_family_closeout_item_dir(required_final_child_uid, repo_root=root))
    _update_item_meta(_execution_family_closeout_item_dir(source_intent_uid, is_intent=True, repo_root=root))
    return {
        "applied": bool(updated_meta_paths),
        "status": EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS,
        "residual_gap": summary,
        "errors": list(errors),
        "updated_meta_paths": updated_meta_paths,
    }


def _repairable_builder_contract_reason(reason: str) -> bool:
    return str(reason or "").strip() in {
        "missing_changed_files_section",
        "missing_validation_commands_section",
        "missing_required_sections",
        "missing_required_diff_block",
    }


def _repairable_verifier_contract_reason(reason: str) -> bool:
    return str(reason or "").strip() in {
        "missing_verdict_header",
        "verdict_not_on_first_line",
        "missing_findings_section",
        "missing_boundary_review_section",
        "missing_proof_review_section",
        "missing_validation_review_section",
        "missing_boundary_review_fields",
        "missing_proof_review_fields",
        "missing_validation_review_fields",
        "pass_without_explicit_proof_review",
        "pass_without_explicit_validation_review",
    }


def _repairable_pending_actions_contract(contract: Dict[str, Any]) -> bool:
    errors = [str(item).strip() for item in list(contract.get("errors") or []) if str(item).strip()]
    if not errors:
        return False
    allowed_exact = {
        "missing_stop_rule_block",
        "missing_stop_rule_one_repair_turn",
        "missing_stop_rule_no_scope_widening",
        "missing_stop_rule_fail_honestly",
    }
    for error in errors:
        if error not in allowed_exact:
            return False
    return True


def _status_reconcile_job_id_from_uid(uid: str) -> str:
    suffix = str(uid or "").rsplit("-", 1)[-1].lower()
    m = re.match(r"^PIS-(\d{8})-", str(uid or ""))
    date_part = m.group(1) if m else "job"
    return f"verify_{suffix}_{date_part}_status_reconcile"


def _status_reconcile_prompt_for_uid(*, uid: str, title: str, pis_rel: str, source_job_id: str) -> str:
    return (
        f"Verify whether ledger item `{uid}` has actually earned a stronger ledger status based on repo evidence, tests, and prior implementation receipts.\n\n"
        "Instructions:\n"
        "1. Read the bound PIS and inspect only the files needed to judge the implementation honestly.\n"
        "2. Review the latest successful scheduled-job receipt/report for this bound item when useful.\n"
        "3. If the implementation is fully supported, update ledger truth to `verified` and append durable events/evidence.\n"
        "4. If confidence is only partial, keep or set the honest non-verified status and state the exact remaining gap.\n"
        "5. Run the narrowest real validation commands that support the verdict.\n"
        "6. Include a parseable `Validation Commands Run` section in the builder output.\n\n"
        "Do not claim verification from scheduler success alone; judge the bound item itself.\n\n"
        "Ledger binding:\n"
        f"- ledger_uid: {uid}\n"
        f"- title: {title}\n"
        f"- pis_path: {pis_rel}\n"
        f"- source_job_id: {source_job_id}\n"
    )


def _queue_status_reconcile_job_for_uid(
    uid: str,
    *,
    source_job_id: str,
    generated_from_parent_uid: str = "",
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    item_dir = _resolve_ledger_item_dir_for_uid(uid, repo_root=repo_root)
    if item_dir is None:
        return {"ok": False, "reason": "ledger_item_not_found", "ledger_uid": uid}
    meta = _read_json((item_dir / "meta.json").resolve())
    status = str(meta.get("status") or "").strip().lower()
    if status == "verified":
        return {"ok": True, "reason": "ledger_item_already_verified", "reconcile_job_id": _status_reconcile_job_id_from_uid(uid)}
    title = str(meta.get("title") or uid).strip() or uid
    root = Path(repo_root or _repo_root()).resolve()
    pis_rel = str((item_dir / "pis.md").resolve().relative_to(root)).replace("\\", "/")
    reconcile_job_id = _status_reconcile_job_id_from_uid(uid)
    try:
        existing = load_job(reconcile_job_id, repo_root=repo_root)
    except FileNotFoundError:
        existing = {}
    if existing:
        return {"ok": True, "reason": "reconcile_job_exists", "reconcile_job_id": reconcile_job_id}
    now_utc = _utc_iso()
    primary = {
        "agent_tech": "openai",
        "model": "gpt-5.4",
        "fallback_models": [],
        "fallback_on_quota": True,
        "reasoning_effort": "medium",
    }
    verifier = {
        "agent_tech": "openai",
        "model": "gpt-5.4",
        "fallback_models": [],
        "fallback_on_quota": True,
        "reasoning_effort": "medium",
    }
    reconcile_spec = {
        "job_id": reconcile_job_id,
        "job_kind": "build_verify_loop",
        "enabled": True,
        "binding": {"type": "ledger_uid", "id": uid},
        "display_title": f"Verify {uid.split('-')[-1].lower()} ledger status reconciliation",
        "prompt": _status_reconcile_prompt_for_uid(uid=uid, title=title, pis_rel=pis_rel, source_job_id=source_job_id),
        "context_refs": [pis_rel],
        "expected_output": {"kind": "report", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": now_utc, "timezone": "UTC"},
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
        "runtime": dict(primary),
        "required_context_bundle": "lab_core_v1",
        "loop": {
            "builder_role": "builder",
            "verifier_role": "verifier",
            "max_iterations": 4,
            "pass_marker": "VERDICT: PASS",
            "builder_runtime": dict(primary),
            "verifier_runtime": dict(verifier),
        },
        "prompt_meta": {
            "generated_by_closeout_truth_reconcile": True,
            "ledger_truth_reconcile_for_job_id": source_job_id,
            "ledger_truth_reconcile_for_uid": uid,
            **({"generated_from_parent_uid": str(generated_from_parent_uid).strip()} if str(generated_from_parent_uid).strip() else {}),
        },
    }
    saved = save_job(reconcile_spec, repo_root=repo_root)
    return {"ok": True, "reason": "reconcile_job_created", "reconcile_job_id": str(saved.get("job_id") or reconcile_job_id)}


def _queue_execution_family_closeout_reconcile_jobs(
    spec: Dict[str, Any],
    *,
    closeout_truth: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    details = closeout_truth.get("details") if isinstance(closeout_truth.get("details"), dict) else {}
    child_uids = [str(item).strip() for item in list(details.get("child_uids") or []) if str(item).strip()]
    child_job_ids = [str(item).strip() for item in list(details.get("child_job_ids") or []) if str(item).strip()]
    child_statuses = details.get("child_statuses") if isinstance(details.get("child_statuses"), dict) else {}
    child_job_statuses = details.get("child_job_statuses") if isinstance(details.get("child_job_statuses"), dict) else {}
    source_job_id = str(spec.get("job_id") or "").strip()
    queued: List[str] = []
    for index, child_uid in enumerate(child_uids):
        child_status = str(child_statuses.get(child_uid) or "").strip().lower()
        child_job_id = child_job_ids[index] if index < len(child_job_ids) else ""
        child_job_status = str(child_job_statuses.get(child_job_id) or "").strip().lower()
        if child_status == "verified" or not _is_success_status(child_job_status):
            continue
        result = _queue_status_reconcile_job_for_uid(child_uid, source_job_id=source_job_id, repo_root=repo_root)
        if bool(result.get("ok")):
            reconcile_job_id = str(result.get("reconcile_job_id") or "").strip()
            if reconcile_job_id and reconcile_job_id not in queued:
                queued.append(reconcile_job_id)
    return {"ok": bool(queued), "queued_job_ids": queued}


def _execute_build_verify_loop_job(
    spec: Dict[str, Any],
    *,
    binding_override: Optional[Dict[str, Any]] = None,
    run_id: str,
    run_attempt: int = 1,
    trigger: str,
    max_runtime_seconds: int,
    repo_root: Optional[Path] = None,
) -> Tuple[bool, Dict[str, Any], str]:
    loop = _normalize_loop(spec.get("loop"))
    loop_policy = str(loop.get("loop_policy") or DEFAULT_BUILD_VERIFY_LOOP_POLICY)
    builder_only = bool(loop.get("builder_only"))
    runtime = _apply_pis_creation_runtime_overrides(
        spec,
        _normalize_runtime(spec.get("runtime"), job_kind=_normalize_job_kind(spec.get("job_kind"))),    )
    continuation_bootstrap = (
        dict(spec.get("_continuation_bootstrap") or {})
        if isinstance(spec.get("_continuation_bootstrap"), dict)
        else _continuation_context_attachment.continuation_bootstrap_projection_for_job_spec(spec, repo_root=repo_root)
    )
    policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    delivery_policy = str(policy.get("delivery_policy") or STRICT_DELIVERY_POLICY).strip().lower() or STRICT_DELIVERY_POLICY
    delivery_guaranteed = delivery_policy == DELIVERY_GUARANTEED_POLICY
    builder_meta = _role_target(loop.get("builder_role"))
    verifier_meta = _role_target(loop.get("verifier_role")) if str(loop.get("verifier_role") or "").strip() else {}
    builder_runtime_override = loop.get("builder_runtime") if isinstance(loop.get("builder_runtime"), dict) else {}
    verifier_runtime_override = loop.get("verifier_runtime") if isinstance(loop.get("verifier_runtime"), dict) else {}
    builder_runtime = _merge_runtime_overrides(runtime, builder_runtime_override)
    verifier_runtime = _merge_runtime_overrides(runtime, verifier_runtime_override)

    def _needs_codex_controller(runtime_obj: Dict[str, Any]) -> bool:
        return (
            str(runtime_obj.get("executor_agent_tech") or "").strip().lower() == "codex"
            and str(runtime_obj.get("advisory_agent_tech") or "").strip() == ""
            and str(runtime_obj.get("agent_tech") or "").strip().lower() in {"", "openai"}
            and str(runtime_obj.get("execution_mode") or "").strip().lower() in {"", "controller_only"}
        )

    if _needs_codex_controller(builder_runtime):
        builder_runtime = _codex_controller_runtime(builder_runtime)
    if verifier_meta and _needs_codex_controller(verifier_runtime):
        verifier_runtime = _codex_controller_runtime(verifier_runtime)
    runtime_binding = dict(spec.get("binding") or {})
    if isinstance(binding_override, dict):
        if str(binding_override.get("task_id") or "").strip():
            runtime_binding["task_id"] = str(binding_override.get("task_id") or "").strip()
        if str(binding_override.get("ledger_uid") or "").strip():
            runtime_binding["ledger_uid"] = str(binding_override.get("ledger_uid") or "").strip()
        if str(binding_override.get("open_loop_id") or "").strip():
            runtime_binding["open_loop_id"] = str(binding_override.get("open_loop_id") or "").strip()
    max_iterations = int(loop.get("max_iterations") or 3)
    pass_marker = str(loop.get("pass_marker") or "VERDICT: PASS")
    prompt_text = _resolved_prompt_text(spec, repo_root=repo_root)
    prompt_sha = _sha256_text(prompt_text)
    compiled_shadow = _compiled_execution_shadow_bundle_for_job(
        spec,
        roles=["builder", "verifier", "repair"],
        runtime=runtime,
        repo_root=repo_root,
    )
    iterations: List[Dict[str, Any]] = []
    last_error = ""
    ok = False
    final_status = "failed_verification"
    pending_repair_contract: Optional[Dict[str, Any]] = None
    execution_family_closeout_contract = _execution_family_closeout_contract(spec)
    execution_family_already_satisfied = False
    bound_ledger_item = _bound_ledger_item_projection(spec, repo_root=repo_root)
    bound_ledger_item_already_satisfied = False
    bound_status_reconcile = _bound_status_reconcile_projection(spec, repo_root=repo_root)
    bound_status_reconcile_already_satisfied = False
    closeout_integrity_defect_outcome: Dict[str, Any] = {}
    if execution_family_closeout_contract:
        closeout_truth = _execution_family_closeout_truth_check(spec, repo_root=repo_root)
        if bool(closeout_truth.get("ok")):
            execution_family_already_satisfied = True
            ok = True
            final_status = "ok"
            retired_spec = dict(spec)
            retired_spec["enabled"] = False
            retired_prompt_meta = retired_spec.get("prompt_meta") if isinstance(retired_spec.get("prompt_meta"), dict) else {}
            retired_prompt_meta["retired_closeout"] = {
                "reason": "family_already_verified_at_execution",
                "retired_utc": _utc_iso(),
            }
            retired_spec["prompt_meta"] = retired_prompt_meta
            save_job(retired_spec, repo_root=repo_root)
    if bound_ledger_item.get("applies") and str(bound_ledger_item.get("status") or "") == "verified":
        bound_ledger_item_already_satisfied = True
        ok = True
        final_status = "ok"
        retired_spec = dict(spec)
        retired_spec["enabled"] = False
        retired_prompt_meta = retired_spec.get("prompt_meta") if isinstance(retired_spec.get("prompt_meta"), dict) else {}
        retired_prompt_meta["retired_bound_ledger_item"] = {
            "reason": "bound_item_already_verified_at_execution",
            "retired_utc": _utc_iso(),
            "ledger_uid": str(bound_ledger_item.get("ledger_uid") or ""),
        }
        retired_spec["prompt_meta"] = retired_prompt_meta
        save_job(retired_spec, repo_root=repo_root)
    if bound_status_reconcile.get("applies") and str(bound_status_reconcile.get("status") or "") == "verified":
        bound_status_reconcile_already_satisfied = True
        ok = True
        final_status = "ok"
        retired_spec = dict(spec)
        retired_spec["enabled"] = False
        retired_prompt_meta = retired_spec.get("prompt_meta") if isinstance(retired_spec.get("prompt_meta"), dict) else {}
        retired_prompt_meta["retired_reconcile"] = {
            "reason": "bound_item_already_verified_at_execution",
            "retired_utc": _utc_iso(),
            "ledger_uid": str(bound_status_reconcile.get("ledger_uid") or ""),
        }
        retired_spec["prompt_meta"] = retired_prompt_meta
        save_job(retired_spec, repo_root=repo_root)

    for iteration in range(1, max_iterations + 1):
        if execution_family_already_satisfied or bound_ledger_item_already_satisfied or bound_status_reconcile_already_satisfied:
            break
        previous_feedback = ""
        if iterations:
            previous_feedback = str((iterations[-1].get("verifier") or {}).get("content") or "")
        _write_active_loop_state(
            str(spec.get("job_id") or ""),
            run_id=run_id,
            iteration=iteration,
            stage="builder",
            completed_iterations=len(iterations),
            max_iterations=max_iterations,
            repo_root=repo_root,
        )
        builder_message = _build_loop_builder_message(
            spec,
            run_id=run_id,
            trigger=trigger,
            iteration=iteration,
            max_iterations=max_iterations,
            verifier_feedback=previous_feedback,
            repo_root=repo_root,
        )
        builder_compiled_dispatch = _compiled_prompt_dispatch_for_role(
            compiled_execution=compiled_shadow,
            role="builder",
        )
        if not bool(builder_compiled_dispatch.get("ok", True)):
            last_error = str(builder_compiled_dispatch.get("failure_class") or "compiled_prompt_unavailable")
            builder_result = _compiled_dispatch_blocked_result(role=builder_meta["runtime_role"], dispatch=builder_compiled_dispatch)
            builder_send_meta = {"selected_model": "", "attempts": []}
            builder_advisory_result = {"ok": False, "assistant": {"content": "", "status": "skipped", "meta": {}}}
            builder_advisory_send_meta = {"selected_model": "", "attempts": []}
        else:
            try:
                builder_options: Dict[str, Any] = {
                    "timeout_s": max_runtime_seconds,
                    "resend_key": _scheduled_resend_key(
                        job_id=str(spec.get("job_id") or ""),
                        run_id=run_id,
                        stage="builder",
                        iteration=iteration,
                        run_attempt=run_attempt,
                    ),
                    "_scheduled_stage": "builder",
                    "_scheduled_context_mode": "run_scoped_no_history",
                    **({"prompt_file": builder_meta["prompt_file"]} if builder_meta["prompt_file"] else {}),
                }
                builder_options = _apply_runtime_send_options(builder_options, builder_runtime)
                builder_result, builder_send_meta, builder_advisory_result, builder_advisory_send_meta = _execute_scheduled_controlled_turn(
                    spec=spec,
                    role=builder_meta["runtime_role"],
                    binding=runtime_binding,
                    message=builder_message,
                    compiled_prompt_text=str(builder_compiled_dispatch.get("prompt_text") or ""),
                    compiled_dispatch_mode=str(builder_compiled_dispatch.get("dispatch_mode") or "legacy_message"),
                    options=builder_options,
                    retries=int(policy.get("agent_turn_retries") or 0),
                    backoff_seconds=int(policy.get("agent_turn_retry_backoff_seconds") or 5),
                    runtime=builder_runtime,
                )
            except Exception as e:
                last_error = str(e)
                builder_result = {"ok": False, "assistant": {"content": f"[scheduler_error] {last_error}", "status": "error", "meta": {"error": last_error}}}
                builder_send_meta = {"selected_model": "", "attempts": []}
                builder_advisory_result = {"ok": False, "assistant": {"content": "", "status": "error", "meta": {"error": last_error}}}
                builder_advisory_send_meta = {"selected_model": "", "attempts": []}
        builder_assistant = builder_result.get("assistant") if isinstance(builder_result.get("assistant"), dict) else {}
        builder_payload = _stage_payload(
            assistant=builder_assistant,
            runtime_send=dict(builder_send_meta or {}),
            advisory_result=builder_advisory_result,
            advisory_send=dict(builder_advisory_send_meta or {}),
        )
        builder_payload["meta"]["compiled_dispatch"] = dict(builder_compiled_dispatch)
        builder_payload["meta"]["compiled_output_classification"] = _compiled_output_classifier.classify_builder_output(
            str(builder_assistant.get("content") or ""),
            normalized_ir=((compiled_shadow.get("ir") if isinstance(compiled_shadow.get("ir"), dict) else {}).get("normalized_ir") or {}),
        )
        if not bool(builder_result.get("ok")):
            final_status = "error"
            last_error = str(last_error or _error_text_from_send_result(builder_result))
            _append_iteration(
                iterations=iterations,
                iteration=iteration,
                builder_message=builder_message,
                builder_payload=builder_payload,
                verifier_message="",
                verifier_payload={"content": "", "status": "skipped", "meta": {}},
            )
            break
        if not bool(builder_payload["meta"]["compiled_output_classification"].get("ok")):
            last_error = str(builder_payload["meta"]["compiled_output_classification"].get("reason") or "invalid_builder_output")
            repeated_compiled_failure = _compiled_failure_policy.repeated_structural_failure_disposition(
                iterations + [{"builder": builder_payload, "verifier": {}}]
            )
            summary = "Builder output failed structural classification."
            if last_error == "missing_changed_files_section":
                summary = "Builder output is missing the required `Changed files` section. Re-emit the same work with an explicit changed-files block."
            elif last_error == "missing_validation_commands_section":
                summary = "Builder output is missing the required `Validation Commands Run` section. Re-emit the same work with explicit validation evidence."
            elif last_error == "missing_required_sections":
                summary = "Builder output is missing one or more explicitly required builder sections. Re-emit the same work with the required canonical headings."
            elif last_error == "missing_required_diff_block":
                summary = "Builder output is missing the required diff block. Re-emit the same work with the required diff block."
            verifier_payload = {
                "content": f"VERDICT: FAIL\n{summary}",
                "status": "classification_rejected_continuity_retry"
                if _repairable_builder_contract_reason(last_error)
                else "classification_rejected",
                "meta": {
                    "error": last_error,
                    "compiled_output_classification": dict(builder_payload["meta"]["compiled_output_classification"]),
                    "continuity_retry": _repairable_builder_contract_reason(last_error),
                },
            }
            if bool(repeated_compiled_failure.get("stop")):
                final_status = "failed_verification"
                last_error = str(repeated_compiled_failure.get("reason") or "repeated_builder_structural_failure")
                verifier_payload["content"] = "VERDICT: FAIL\nRepeated builder structural failure with no new signal."
                verifier_payload["meta"]["compiled_retry_disposition"] = dict(repeated_compiled_failure)
            _append_iteration(
                iterations=iterations,
                iteration=iteration,
                builder_message=builder_message,
                builder_payload=builder_payload,
                verifier_message="",
                verifier_payload=verifier_payload,
            )
            if final_status == "failed_verification":
                break
            continue
        repeated_compiled_failure = _compiled_failure_policy.repeated_structural_failure_disposition(
            iterations + [{"builder": builder_payload, "verifier": {}}]
        )
        if bool(repeated_compiled_failure.get("stop")):
            final_status = "failed_verification"
            last_error = str(repeated_compiled_failure.get("reason") or "repeated_builder_structural_failure")
            _append_iteration(
                iterations=iterations,
                iteration=iteration,
                builder_message=builder_message,
                builder_payload=builder_payload,
                verifier_message="",
                verifier_payload={
                    "content": "VERDICT: FAIL\nRepeated builder structural failure with no new signal.",
                    "status": "classification_rejected",
                    "meta": {
                        "error": last_error,
                        "compiled_retry_disposition": dict(repeated_compiled_failure),
                    },
                },
            )
            break
        scope_check = _status_reconcile_scope_violation(
            spec,
            builder_content=str(builder_assistant.get("content") or ""),
            repo_root=repo_root,
        )
        if not bool(scope_check.get("violated")):
            scope_check = _execution_family_closeout_scope_violation(
                spec,
                builder_content=str(builder_assistant.get("content") or ""),
                repo_root=repo_root,
            )
        if bool(scope_check.get("violated")):
            off_target_paths = [str(path).strip() for path in list(scope_check.get("off_target_paths") or []) if str(path).strip()]
            allowed_paths = [str(path).strip() for path in list(scope_check.get("allowed_paths") or []) if str(path).strip()]
            last_error = "off_target_scope"
            scope_kind = str(scope_check.get("scope_kind") or "").strip()
            summary_line = "Off-target scope. Update only the bound parent reconcile evidence; do not mutate child or sibling items."
            if scope_kind == "execution_family_closeout":
                summary_line = "Off-target scope. Update only the declared execution family records and family-derived repo paths from successful child receipts; do not mutate unrelated ledger or scheduler files."
            scope_feedback_lines = [
                "VERDICT: FAIL",
                summary_line,
            ]
            if off_target_paths:
                scope_feedback_lines.append(f"Off-target paths: {', '.join(off_target_paths)}")
            if allowed_paths:
                scope_feedback_lines.append(f"Allowed paths: {', '.join(allowed_paths)}")
            scope_feedback = "\n".join(scope_feedback_lines)
            _append_iteration(
                iterations=iterations,
                iteration=iteration,
                builder_message=builder_message,
                builder_payload=builder_payload,
                verifier_message="",
                verifier_payload={
                    "content": scope_feedback,
                    "status": "scope_rejected",
                    "meta": {
                        "error": "off_target_scope",
                        "off_target_paths": off_target_paths,
                        "allowed_paths": allowed_paths,
                    },
                },
            )
            continue
        if builder_only:
            _append_iteration(
                iterations=iterations,
                iteration=iteration,
                builder_message=builder_message,
                builder_payload=builder_payload,
                verifier_message="",
                verifier_payload={
                    "content": "[builder_only] verifier stage skipped by loop.builder_only",
                    "status": "skipped_builder_only",
                    "meta": {"builder_only": True},
                },
            )
            ok = True
            final_status = "delivered_builder_only" if delivery_guaranteed else "ok"
            break
        verifier_message = _build_loop_verifier_message(
            spec,
            run_id=run_id,
            trigger=trigger,
            iteration=iteration,
            max_iterations=max_iterations,
            builder_output=str(builder_assistant.get("content") or ""),
            pass_marker=pass_marker,
            repo_root=repo_root,
        )
        verifier_compiled_dispatch = _compiled_prompt_dispatch_for_role(
            compiled_execution=compiled_shadow,
            role="verifier",
        )
        _write_active_loop_state(
            str(spec.get("job_id") or ""),
            run_id=run_id,
            iteration=iteration,
            stage="verifier",
            completed_iterations=max(0, iteration - 1),
            max_iterations=max_iterations,
            repo_root=repo_root,
        )
        if not bool(verifier_compiled_dispatch.get("ok", True)):
            last_error = str(verifier_compiled_dispatch.get("failure_class") or "compiled_prompt_unavailable")
            verifier_result = _compiled_dispatch_blocked_result(role=verifier_meta["runtime_role"], dispatch=verifier_compiled_dispatch)
            verifier_send_meta = {"selected_model": "", "attempts": []}
            verifier_advisory_result = {"ok": False, "assistant": {"content": "", "status": "skipped", "meta": {}}}
            verifier_advisory_send_meta = {"selected_model": "", "attempts": []}
        else:
            try:
                verifier_options: Dict[str, Any] = {
                    "timeout_s": max_runtime_seconds,
                    "resend_key": _scheduled_resend_key(
                        job_id=str(spec.get("job_id") or ""),
                        run_id=run_id,
                        stage="verifier",
                        iteration=iteration,
                        run_attempt=run_attempt,
                    ),
                    "_scheduled_stage": "verifier",
                    "_scheduled_context_mode": "run_scoped_no_history",
                    **({"prompt_file": verifier_meta["prompt_file"]} if verifier_meta["prompt_file"] else {}),
                }
                verifier_options = _apply_runtime_send_options(verifier_options, verifier_runtime)
                verifier_result, verifier_send_meta, verifier_advisory_result, verifier_advisory_send_meta = _execute_scheduled_controlled_turn(
                    spec=spec,
                    role=verifier_meta["runtime_role"],
                    binding=runtime_binding,
                    message=verifier_message,
                    compiled_prompt_text=str(verifier_compiled_dispatch.get("prompt_text") or ""),
                    compiled_dispatch_mode=str(verifier_compiled_dispatch.get("dispatch_mode") or "legacy_message"),
                    options=verifier_options,
                    retries=int(policy.get("agent_turn_retries") or 0),
                    backoff_seconds=int(policy.get("agent_turn_retry_backoff_seconds") or 5),
                    runtime=verifier_runtime,
                )
            except Exception as e:
                last_error = str(e)
                verifier_result = {
                    "ok": False,
                    "assistant": {"content": f"[scheduler_error] {last_error}", "status": "error", "meta": {"error": last_error}},
                }
                verifier_send_meta = {"selected_model": "", "attempts": []}
                verifier_advisory_result = {"ok": False, "assistant": {"content": "", "status": "error", "meta": {"error": last_error}}}
                verifier_advisory_send_meta = {"selected_model": "", "attempts": []}
        verifier_assistant = verifier_result.get("assistant") if isinstance(verifier_result.get("assistant"), dict) else {}
        verifier_payload = _stage_payload(
            assistant=verifier_assistant,
            runtime_send=dict(verifier_send_meta or {}),
            advisory_result=verifier_advisory_result,
            advisory_send=dict(verifier_advisory_send_meta or {}),
        )
        verifier_payload["meta"]["compiled_dispatch"] = dict(verifier_compiled_dispatch)
        verifier_payload["meta"]["compiled_output_classification"] = _compiled_output_classifier.classify_verifier_output(
            str(verifier_assistant.get("content") or ""),
            normalized_ir=((compiled_shadow.get("ir") if isinstance(compiled_shadow.get("ir"), dict) else {}).get("normalized_ir") or {}),
        )
        verifier_payload["meta"]["loop_control"] = _verifier_loop_control_metadata(str(verifier_assistant.get("content") or ""))
        verdict = _extract_verdict_token(str(verifier_assistant.get("content") or ""))
        if verdict == PASS_PENDING_ACTIONS_VERDICT:
            verifier_payload["meta"]["pending_actions_contract"] = _parse_pass_pending_actions_contract(
                str(verifier_assistant.get("content") or ""),
                builder_content=str(builder_assistant.get("content") or ""),
                repo_root=repo_root,
            )
        _append_iteration(
            iterations=iterations,
            iteration=iteration,
            builder_message=builder_message,
            builder_payload=builder_payload,
            verifier_message=verifier_message,
            verifier_payload=verifier_payload,
        )
        if not bool(verifier_payload["meta"]["compiled_output_classification"].get("ok")):
            last_error = str(verifier_payload["meta"]["compiled_output_classification"].get("reason") or "invalid_verifier_output")
            if _repairable_verifier_contract_reason(last_error):
                verifier_payload["status"] = "classification_rejected_continuity_retry"
                verifier_payload["content"] = (
                    "VERDICT: FAIL\n"
                    "Verifier output violated the canonical verdict format. "
                    "Re-emit the verdict with `VERDICT:` on the first line and keep the underlying judgment unchanged."
                )
                verifier_payload["meta"]["continuity_retry"] = True
                iterations[-1]["verifier"] = verifier_payload
                continue
            final_status = "failed_verification"
            break
        _enforce_verifier_loop_control(spec, iterations, repo_root=repo_root)
        if not bool(verifier_result.get("ok")):
            final_status = "error"
            last_error = str(last_error or _error_text_from_send_result(verifier_result))
            break
        if _loop_passed(str(verifier_assistant.get("content") or ""), pass_marker=pass_marker):
            closeout_truth = _execution_family_closeout_truth_check(spec, repo_root=repo_root)
            closeout_promotion = _execution_family_closeout_promote_verified_family(
                spec,
                closeout_truth=closeout_truth,
                repo_root=repo_root,
            )
            if bool(closeout_promotion.get("ok")) and isinstance(closeout_promotion.get("post_check"), dict):
                closeout_truth = dict(closeout_promotion.get("post_check") or {})
            if not bool(closeout_truth.get("ok")):
                if delivery_guaranteed and _execution_family_closeout_formal_integrity_defect(closeout_truth):
                    closeout_integrity_defect_outcome = _record_execution_family_closeout_integrity_defect(
                        spec,
                        closeout_truth=closeout_truth,
                        repo_root=repo_root,
                    )
                    ok = True
                    final_status = EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS
                    iterations[-1]["verifier"]["status"] = "closeout_integrity_defect"
                    iterations[-1]["verifier"]["meta"]["execution_family_closeout_truth"] = dict(closeout_truth)
                    iterations[-1]["verifier"]["meta"]["execution_family_closeout_promotion"] = dict(closeout_promotion)
                    iterations[-1]["verifier"]["meta"]["execution_family_closeout_integrity_defect"] = dict(
                        closeout_integrity_defect_outcome
                    )
                    existing_content = str(iterations[-1]["verifier"].get("content") or "").rstrip()
                    machine_lines = [
                        "Machine Closeout Truth Check: EXECUTED_WITH_INTEGRITY_DEFECT",
                        *[f"- {item}" for item in list(closeout_truth.get("errors") or [])],
                    ]
                    iterations[-1]["verifier"]["content"] = (
                        existing_content + "\n\n" + "\n".join(machine_lines)
                        if existing_content
                        else "\n".join(machine_lines)
                    )
                    break
                reconcile_jobs = _queue_execution_family_closeout_reconcile_jobs(
                    spec,
                    closeout_truth=closeout_truth,
                    repo_root=repo_root,
                )
                if list(reconcile_jobs.get("queued_job_ids") or []):
                    final_status = "proof_gap"
                    last_error = "execution_family_closeout_reconcile_queued: " + ", ".join(
                        [str(item) for item in list(reconcile_jobs.get("queued_job_ids") or [])]
                    )
                    iterations[-1]["verifier"]["status"] = "reconcile_queued"
                    iterations[-1]["verifier"]["meta"]["execution_family_closeout_truth"] = dict(closeout_truth)
                    iterations[-1]["verifier"]["meta"]["execution_family_closeout_promotion"] = dict(closeout_promotion)
                    iterations[-1]["verifier"]["meta"]["queued_reconcile_job_ids"] = list(reconcile_jobs.get("queued_job_ids") or [])
                    existing_content = str(iterations[-1]["verifier"].get("content") or "").rstrip()
                    machine_lines = [
                        "Machine Closeout Truth Check: RECONCILE_QUEUED",
                        *[f"- {item}" for item in list(closeout_truth.get("errors") or [])],
                        *[f"- queued_reconcile_job:{item}" for item in list(reconcile_jobs.get("queued_job_ids") or [])],
                    ]
                    iterations[-1]["verifier"]["content"] = (
                        existing_content + "\n\n" + "\n".join(machine_lines)
                        if existing_content
                        else "\n".join(machine_lines)
                    )
                    break
                last_error = "execution_family_closeout_truth_failed: " + "; ".join(
                    [str(item) for item in list(closeout_truth.get("errors") or [])]
                )
                iterations[-1]["verifier"]["status"] = "truth_rejected"
                iterations[-1]["verifier"]["meta"]["execution_family_closeout_truth"] = dict(closeout_truth)
                iterations[-1]["verifier"]["meta"]["execution_family_closeout_promotion"] = dict(closeout_promotion)
                existing_content = str(iterations[-1]["verifier"].get("content") or "").rstrip()
                machine_lines = [
                    "Machine Closeout Truth Check: FAIL",
                    *[f"- {item}" for item in list(closeout_truth.get("errors") or [])],
                ]
                iterations[-1]["verifier"]["content"] = (
                    existing_content + "\n\n" + "\n".join(machine_lines)
                    if existing_content
                    else "\n".join(machine_lines)
                )
                if iteration >= max_iterations:
                    final_status = "failed_verification"
                    break
                continue
            iterations[-1]["verifier"]["meta"]["execution_family_closeout_promotion"] = dict(closeout_promotion)
            ok = True
            final_status = "ok"
            break
        if loop_policy == DEFAULT_BUILD_VERIFY_LOOP_POLICY and iteration == 1 and verdict == "FAIL":
            repair_contract = _parse_bounded_repair_contract(
                str(verifier_assistant.get("content") or ""),
                builder_content=str(builder_assistant.get("content") or ""),
                repo_root=repo_root,
            )
            verifier_payload["meta"]["bounded_repair_contract"] = dict(repair_contract)
            if bool(repair_contract.get("ok")):
                verifier_payload["meta"]["conditional_fail_promoted_to_pending_actions"] = True
                pending_repair_contract = {
                    "source_iteration": iteration,
                    "pending_actions": list(repair_contract.get("pending_actions") or []),
                    "stop_rule": list(repair_contract.get("stop_rule") or []),
                    "allowed_paths": list(repair_contract.get("allowed_paths") or []),
                    "verifier_content": str(verifier_assistant.get("content") or ""),
                }
                break
            terminal_contract = _parse_terminal_fail_contract(str(verifier_assistant.get("content") or ""))
            verifier_payload["meta"]["terminal_fail_contract"] = dict(terminal_contract)
            if not bool(terminal_contract.get("ok")):
                final_status = "failed_verification"
                last_error = "invalid_conditional_fail_contract: " + "; ".join([str(item) for item in list(terminal_contract.get("errors") or [])])
                break
            final_status = "failed_verification"
            last_error = str(last_error or f"verifier did not emit `{pass_marker}` within {max_iterations} iteration(s)")
            break
        if verdict == PASS_PENDING_ACTIONS_VERDICT:
            if loop_policy != DEFAULT_BUILD_VERIFY_LOOP_POLICY:
                final_status = "failed_verification"
                last_error = f"unsupported verifier verdict for loop_policy={loop_policy}: {PASS_PENDING_ACTIONS_VERDICT}"
                break
            contract = verifier_payload["meta"].get("pending_actions_contract") if isinstance(verifier_payload["meta"].get("pending_actions_contract"), dict) else {}
            if not bool(contract.get("ok")):
                if _repairable_pending_actions_contract(contract):
                    verifier_payload["status"] = "pending_actions_contract_repaired"
                    verifier_payload["meta"]["continuity_retry"] = True
                    iterations[-1]["verifier"] = verifier_payload
                    pending_repair_contract = {
                        "source_iteration": iteration,
                        "pending_actions": list(contract.get("pending_actions") or []),
                        "stop_rule": [
                            "one builder repair turn only",
                            "no scope widening",
                            "if builder cannot complete all actions, fail honestly",
                        ],
                        "allowed_paths": list(contract.get("allowed_paths") or []),
                        "verifier_content": str(verifier_assistant.get("content") or ""),
                    }
                    break
                final_status = "failed_verification"
                last_error = "invalid PASS_PENDING_ACTIONS contract: " + "; ".join([str(item) for item in list(contract.get("errors") or [])])
                break
            pending_repair_contract = {
                "source_iteration": iteration,
                "pending_actions": list(contract.get("pending_actions") or []),
                "stop_rule": list(contract.get("stop_rule") or []),
                "allowed_paths": list(contract.get("allowed_paths") or []),
                "verifier_content": str(verifier_assistant.get("content") or ""),
            }
            break
        stop_decision = _should_economic_stop(spec, iterations, repo_root=repo_root)
        if bool(stop_decision.get("stop")):
            final_status = str(stop_decision.get("status") or "economic_stop_no_new_signal")
            last_error = str(stop_decision.get("reason") or "economic early stop: repeated low-gain verifier fail with no new signal")
            break

    if pending_repair_contract is not None and not ok:
        repair_iteration = max(len(iterations) + 1, int(pending_repair_contract.get("source_iteration") or 0) + 1)
        repair_max_iterations = max(max_iterations, repair_iteration)
        pending_actions = [str(item).strip() for item in list(pending_repair_contract.get("pending_actions") or []) if str(item).strip()]
        stop_rule = [str(item).strip() for item in list(pending_repair_contract.get("stop_rule") or []) if str(item).strip()]
        allowed_paths = [str(item).strip() for item in list(pending_repair_contract.get("allowed_paths") or []) if str(item).strip()]
        waiver_note = (
            "[no second verifier pass executed; bounded repair stop after "
            f"{PASS_PENDING_ACTIONS_VERDICT} from iteration {int(pending_repair_contract.get('source_iteration') or 0)}]"
        )
        _write_active_loop_state(
            str(spec.get("job_id") or ""),
            run_id=run_id,
            iteration=repair_iteration,
            stage="builder",
            completed_iterations=len(iterations),
            max_iterations=repair_max_iterations,
            repo_root=repo_root,
        )
        repair_message = _build_loop_builder_message(
            spec,
            run_id=run_id,
            trigger=trigger,
            iteration=repair_iteration,
            max_iterations=repair_max_iterations,
            pending_actions=pending_actions,
            stop_rule=stop_rule,
            allowed_paths=allowed_paths,
            repo_root=repo_root,
        )
        repair_compiled_dispatch = _compiled_prompt_dispatch_for_role(
            compiled_execution=compiled_shadow,
            role="repair",
        )
        try:
            repair_options: Dict[str, Any] = {
                "timeout_s": max_runtime_seconds,
                "resend_key": _scheduled_resend_key(
                    job_id=str(spec.get("job_id") or ""),
                    run_id=run_id,
                    stage="builder",
                    iteration=repair_iteration,
                    run_attempt=run_attempt,
                ),
                "_scheduled_stage": "builder",
                "_scheduled_context_mode": "run_scoped_no_history",
                **({"prompt_file": builder_meta["prompt_file"]} if builder_meta["prompt_file"] else {}),
            }
            repair_options = _apply_runtime_send_options(repair_options, builder_runtime)
            repair_result, repair_send_meta, repair_advisory_result, repair_advisory_send_meta = _execute_scheduled_controlled_turn(
                spec=spec,
                role=builder_meta["runtime_role"],
                binding=dict(spec.get("binding") or {}),
                message=repair_message,
                compiled_prompt_text=str(repair_compiled_dispatch.get("prompt_text") or ""),
                compiled_dispatch_mode=str(repair_compiled_dispatch.get("dispatch_mode") or "legacy_message"),
                options=repair_options,
                retries=int(policy.get("agent_turn_retries") or 0),
                backoff_seconds=int(policy.get("agent_turn_retry_backoff_seconds") or 5),
                runtime=builder_runtime,
            )
        except Exception as e:
            last_error = str(e)
            repair_result = {"ok": False, "assistant": {"content": f"[scheduler_error] {last_error}", "status": "error", "meta": {"error": last_error}}}
            repair_send_meta = {"selected_model": "", "attempts": []}
            repair_advisory_result = {"ok": False, "assistant": {"content": "", "status": "error", "meta": {"error": last_error}}}
            repair_advisory_send_meta = {"selected_model": "", "attempts": []}
        repair_assistant = repair_result.get("assistant") if isinstance(repair_result.get("assistant"), dict) else {}
        repair_payload = _stage_payload(
            assistant=repair_assistant,
            runtime_send=dict(repair_send_meta or {}),
            advisory_result=repair_advisory_result,
            advisory_send=dict(repair_advisory_send_meta or {}),
        )
        repair_payload["meta"]["compiled_dispatch"] = dict(repair_compiled_dispatch)
        waived_verifier_payload: Dict[str, Any] = {
            "content": waiver_note,
            "status": "waived_after_pending_actions",
            "meta": {
                "waived": True,
                "source_iteration": int(pending_repair_contract.get("source_iteration") or 0),
                "pending_actions": pending_actions,
                "stop_rule": stop_rule,
                "allowed_paths": allowed_paths,
                "pending_actions_contract": {
                    "ok": True,
                    "verdict": PASS_PENDING_ACTIONS_VERDICT,
                    "pending_actions": pending_actions,
                    "stop_rule": stop_rule,
                    "allowed_paths": allowed_paths,
                    "errors": [],
                },
            },
        }
        if not bool(repair_result.get("ok")):
            final_status = "error"
            last_error = str(last_error or _error_text_from_send_result(repair_result))
            _append_iteration(
                iterations=iterations,
                iteration=repair_iteration,
                builder_message=repair_message,
                builder_payload=repair_payload,
                verifier_message="",
                verifier_payload=waived_verifier_payload,
            )
        else:
            repair_scope_check = _status_reconcile_scope_violation(
                spec,
                builder_content=str(repair_assistant.get("content") or ""),
                repo_root=repo_root,
            )
            if not bool(repair_scope_check.get("violated")):
                repair_scope_check = _execution_family_closeout_scope_violation(
                    spec,
                    builder_content=str(repair_assistant.get("content") or ""),
                    repo_root=repo_root,
                )
            repair_validation = _validate_bounded_repair_builder_output(
                str(repair_assistant.get("content") or ""),
                pending_actions=pending_actions,
                allowed_paths=allowed_paths,
                repo_root=repo_root,
            )
            waived_verifier_payload["meta"]["repair_validation"] = repair_validation
            if bool(repair_scope_check.get("violated")):
                off_target_paths = [str(path).strip() for path in list(repair_scope_check.get("off_target_paths") or []) if str(path).strip()]
                allowed_scope_paths = [str(path).strip() for path in list(repair_scope_check.get("allowed_paths") or []) if str(path).strip()]
                final_status = "failed_verification"
                last_error = "off_target_scope"
                waived_verifier_payload["status"] = "scope_rejected"
                waived_verifier_payload["meta"]["error"] = "off_target_scope"
                waived_verifier_payload["meta"]["off_target_paths"] = off_target_paths
                waived_verifier_payload["meta"]["allowed_paths"] = allowed_scope_paths
            elif not bool(repair_validation.get("ok")):
                final_status = "failed_verification"
                last_error = "bounded repair failed: " + "; ".join([str(item) for item in list(repair_validation.get("errors") or [])])
                waived_verifier_payload["status"] = "repair_rejected"
                waived_verifier_payload["meta"]["error"] = "bounded_repair_failed"
                waived_verifier_payload["meta"]["repair_errors"] = list(repair_validation.get("errors") or [])
            else:
                closeout_truth = _execution_family_closeout_truth_check(spec, repo_root=repo_root)
                closeout_promotion = _execution_family_closeout_promote_verified_family(
                    spec,
                    closeout_truth=closeout_truth,
                    repo_root=repo_root,
                )
                if bool(closeout_promotion.get("ok")) and isinstance(closeout_promotion.get("post_check"), dict):
                    closeout_truth = dict(closeout_promotion.get("post_check") or {})
                if not bool(closeout_truth.get("ok")):
                    if delivery_guaranteed and _execution_family_closeout_formal_integrity_defect(closeout_truth):
                        ok = True
                        final_status = EXECUTED_WITH_CLOSEOUT_INTEGRITY_DEFECT_STATUS
                        closeout_integrity_defect_outcome = _record_execution_family_closeout_integrity_defect(
                            spec,
                            closeout_truth=closeout_truth,
                            repo_root=repo_root,
                        )
                        waived_verifier_payload["status"] = "closeout_integrity_defect"
                        waived_verifier_payload["meta"]["execution_family_closeout_truth"] = dict(closeout_truth)
                        waived_verifier_payload["meta"]["execution_family_closeout_promotion"] = dict(closeout_promotion)
                        waived_verifier_payload["meta"]["execution_family_closeout_integrity_defect"] = dict(
                            closeout_integrity_defect_outcome
                        )
                        existing_content = str(waived_verifier_payload.get("content") or "").rstrip()
                        machine_lines = [
                            "Machine Closeout Truth Check: EXECUTED_WITH_INTEGRITY_DEFECT",
                            *[f"- {item}" for item in list(closeout_truth.get("errors") or [])],
                        ]
                        waived_verifier_payload["content"] = (
                            existing_content + "\n\n" + "\n".join(machine_lines)
                            if existing_content
                            else "\n".join(machine_lines)
                        )
                    else:
                        final_status = "failed_verification"
                        last_error = "execution_family_closeout_truth_failed: " + "; ".join(
                            [str(item) for item in list(closeout_truth.get("errors") or [])]
                        )
                        waived_verifier_payload["status"] = "truth_rejected"
                        waived_verifier_payload["meta"]["execution_family_closeout_truth"] = dict(closeout_truth)
                        waived_verifier_payload["meta"]["execution_family_closeout_promotion"] = dict(closeout_promotion)
                        existing_content = str(waived_verifier_payload.get("content") or "").rstrip()
                        machine_lines = [
                            "Machine Closeout Truth Check: FAIL",
                            *[f"- {item}" for item in list(closeout_truth.get("errors") or [])],
                        ]
                        waived_verifier_payload["content"] = (
                            existing_content + "\n\n" + "\n".join(machine_lines)
                            if existing_content
                            else "\n".join(machine_lines)
                        )
                else:
                    ok = True
                    final_status = CONDITIONAL_PASS_TERMINAL_STATUS
                    waived_verifier_payload["meta"]["repair_applied_without_second_verifier_pass"] = True
                    waived_verifier_payload["meta"]["execution_family_closeout_promotion"] = dict(closeout_promotion)
            _append_iteration(
                iterations=iterations,
                iteration=repair_iteration,
                builder_message=repair_message,
                builder_payload=repair_payload,
                verifier_message="",
                verifier_payload=waived_verifier_payload,
            )

    job_shape_fields = _resolve_job_shape_fields(spec)
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,        "job_kind": "build_verify_loop",
        "job_shape": str(job_shape_fields.get("job_shape") or ""),
        "job_shape_source": str(job_shape_fields.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_fields.get("job_shape_compatibility_mode")),
        "job_id": str(spec.get("job_id") or ""),
        "run_id": run_id,
        "trigger": trigger,
        "status": final_status,
        "role": "",
        "runtime_role": "",
        "binding": dict(spec.get("binding") or {}),
        "schedule": dict(spec.get("schedule") or {}),
        "expected_output": dict(spec.get("expected_output") or {}),
        "context_refs": list(spec.get("context_refs") or []),
        "required_context_bundle": str(spec.get("required_context_bundle") or ""),
        "additional_context_refs": list(spec.get("additional_context_refs") or []),
        "forbidden_context_refs": list(spec.get("forbidden_context_refs") or []),
        "effective_context_refs": _effective_context_refs(spec),
        "job_shape_context": _job_shape_context_obligation_projection(spec),
        "runtime": runtime,
        "compiled_execution": compiled_shadow,
        "execution_contract": _scheduled_execution_contract(runtime, job_kind="build_verify_loop"),
        "continuation_bootstrap": continuation_bootstrap,
        "resolved_prompt": {
            "prompt_ref": str(spec.get("prompt_ref") or ""),
            "prompt_sha256": prompt_sha,
            "text": prompt_text,
        },
        "loop": {
            "loop_policy": loop_policy,
            "builder_role": builder_meta["role"],
            "builder_runtime_role": builder_meta["runtime_role"],
            "builder_runtime": builder_runtime,
            "verifier_role": str(verifier_meta.get("role") or ""),
            "verifier_runtime_role": str(verifier_meta.get("runtime_role") or ""),
            "verifier_runtime": verifier_runtime,
            "max_iterations": max_iterations,
            "completed_iterations": len(iterations),
            "pass_marker": pass_marker,
            "builder_only": builder_only,
            "delivery_policy": delivery_policy,
            "conditional_repair_applied": final_status == CONDITIONAL_PASS_TERMINAL_STATUS,
            "final_verifier_confirmation": final_status == "ok",
            "iterations": iterations,
        },
    }
    if closeout_integrity_defect_outcome:
        receipt["execution_family_closeout_integrity_defect"] = dict(closeout_integrity_defect_outcome)
    if execution_family_already_satisfied:
        receipt["execution_family_closeout_short_circuit"] = {
            "applied": True,
            "reason": "family_already_verified_at_execution",
            "retired_job": True,
        }
    if bound_ledger_item_already_satisfied:
        receipt["bound_ledger_item_short_circuit"] = {
            "applied": True,
            "reason": "bound_item_already_verified_at_execution",
            "retired_job": True,
            "ledger_uid": str(bound_ledger_item.get("ledger_uid") or ""),
        }
    if bound_status_reconcile_already_satisfied:
        receipt["bound_status_reconcile_short_circuit"] = {
            "applied": True,
            "reason": "bound_item_already_verified_at_execution",
            "retired_job": True,
            "ledger_uid": str(bound_status_reconcile.get("ledger_uid") or ""),
        }
    compiled_retry = _compiled_failure_policy.classify_compiled_retry_disposition(receipt=receipt)
    if any(str(compiled_retry.get(key) or "") for key in ("class", "reason", "source")):
        receipt["compiled_retry_disposition"] = compiled_retry
    if pending_repair_contract is not None:
        receipt["loop"]["conditional_repair_source_iteration"] = int(pending_repair_contract.get("source_iteration") or 0)
    validation_commands: List[str] = []
    for row in iterations:
        validation_commands.extend(_extract_validation_commands(str((row.get("builder") or {}).get("content") or "")))
    dedup_validation: List[str] = []
    for cmd in validation_commands:
        if cmd not in dedup_validation:
            dedup_validation.append(cmd)
    policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    repo_change_strict = _is_repo_change_strict(policy)
    if repo_change_strict:
        proof = {
            "required_validation_evidence": bool(policy.get("require_validation_evidence")),
            "validation_command_count": len(dedup_validation),
            "validation_commands": dedup_validation,
            "proof_gap": False,
            "proof_gap_reason": "",
        }
        if execution_family_already_satisfied:
            proof["required_validation_evidence"] = False
            proof["proof_gap"] = False
            proof["proof_gap_reason"] = "family_already_verified_at_execution"
        elif bound_ledger_item_already_satisfied:
            proof["required_validation_evidence"] = False
            proof["proof_gap"] = False
            proof["proof_gap_reason"] = "bound_item_already_verified_at_execution"
        elif bound_status_reconcile_already_satisfied:
            proof["required_validation_evidence"] = False
            proof["proof_gap"] = False
            proof["proof_gap_reason"] = "bound_item_already_verified_at_execution"
        elif (
            bool(policy.get("require_validation_evidence"))
            and not dedup_validation
            and str(final_status or "").strip().lower() not in {"error", "interrupted", "cancelled"}
        ):
            ok = False
            final_status = "proof_gap"
            proof["proof_gap"] = True
            proof["proof_gap_reason"] = "required_validation_commands_missing"
            last_error = "required validation evidence missing: builder output did not include a `Validation Commands Run` section"
        receipt["execution_proof"] = proof
        receipt["repo_change_evidence"] = _build_repo_change_evidence(repo_root=repo_root)
    if delivery_guaranteed and not ok:
        delivered_status = ""
        if final_status == "proof_gap":
            delivered_status = "delivered_unverified"
        elif final_status in {
            "failed_verification",
            "good_enough_keep_current_status",
            "economic_stop_no_new_signal",
            "needs_review",
            "deferred",
            "blocked",
        }:
            delivered_status = "delivered_with_findings"
        if delivered_status:
            ok = True
            receipt["delivery_outcome"] = {
                "policy": delivery_policy,
                "delivered": True,
                "source_terminal_status": final_status,
                "delivery_status": delivered_status,
                "builder_only": builder_only,
                "verification_advisory_only": True,
                "findings_present": final_status != "proof_gap",
                "proof_gap_present": final_status == "proof_gap",
            }
            final_status = delivered_status
            receipt["status"] = delivered_status
    if not ok:
        receipt["status"] = final_status
        receipt["error"] = str(last_error or f"verifier did not emit `{pass_marker}` within {max_iterations} iteration(s)")
    elif str(receipt.get("status") or "").strip() != final_status:
        receipt["status"] = final_status
        receipt.pop("error", None)
    return ok, receipt, str(receipt.get("error") or "")


def _collect_failed_runtime_attempts(receipt: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    if str(receipt.get("job_kind") or "") == "single_turn":
        runtime_send = receipt.get("runtime_send") if isinstance(receipt.get("runtime_send"), dict) else {}
        for row in list(runtime_send.get("attempts") or []):
            if isinstance(row, dict) and not bool(row.get("ok")):
                out.append(row)
        return out
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    for step in list(loop.get("iterations") or []):
        if not isinstance(step, dict):
            continue
        for stage_name in ("builder", "verifier"):
            stage = step.get(stage_name) if isinstance(step.get(stage_name), dict) else {}
            runtime_send = stage.get("runtime_send") if isinstance(stage.get("runtime_send"), dict) else {}
            for row in list(runtime_send.get("attempts") or []):
                if isinstance(row, dict) and not bool(row.get("ok")):
                    out.append(row)
    return out


def _classify_whole_job_failure_for_retry(spec: Dict[str, Any], receipt: Dict[str, Any]) -> Dict[str, Any]:
    status = str(receipt.get("status") or "").strip().lower()
    pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
    compiled_retry = receipt.get("compiled_retry_disposition") if isinstance(receipt.get("compiled_retry_disposition"), dict) else {}
    if status in {"failed_verification", "proof_gap", "blocked", "needs_review", "deferred", "interrupted", "good_enough_keep_current_status", "economic_stop_no_new_signal"}:
        return {"retryable": False, "class": status, "reason": f"non_retryable_status:{status}"}
    if str(compiled_retry.get("class") or "").strip():
        return {
            "retryable": bool(compiled_retry.get("retryable")),
            "class": str(compiled_retry.get("class") or ""),
            "reason": str(compiled_retry.get("reason") or "compiled_retry_disposition"),
        }
    if str(pipeline_execution.get("mode") or "") == PIPELINE_FULL_AUTO_MODE and bool(pipeline_execution.get("summary")):
        return {"retryable": False, "class": status or "pipeline_full_auto_terminal", "reason": "pipeline_full_auto_is_retry_owner"}
    if status == "error" and str(receipt.get("error") or "").lower().startswith("post-success git commit/tag failed"):
        return {"retryable": False, "class": "post_success_commit_tag_failed", "reason": "commit_tag_has_dedicated_retry_policy"}
    if _normalize_job_kind(spec.get("job_kind")) == "build_verify_loop":
        loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
        for row in list(loop.get("iterations") or []):
            if not isinstance(row, dict):
                continue
            verifier = row.get("verifier") if isinstance(row.get("verifier"), dict) else {}
            content = str(verifier.get("content") or "").strip().upper()
            if content.startswith("VERDICT: FAIL"):
                return {"retryable": False, "class": "semantic_verifier_fail", "reason": "verifier_explicit_fail"}
    failed_attempts = _collect_failed_runtime_attempts(receipt)
    if failed_attempts:
        if all(bool(row.get("transient_like")) for row in failed_attempts):
            return {"retryable": True, "class": "transient_runtime_error", "reason": "all_failed_runtime_attempts_marked_transient"}
        return {"retryable": False, "class": "non_transient_runtime_error", "reason": "runtime_error_not_marked_transient"}
    err = str(receipt.get("error") or "")
    if status == "error" and _looks_like_transient_runtime_error(err):
        return {"retryable": True, "class": "transient_runtime_error", "reason": "error_text_marked_transient"}
    if status == "error":
        return {"retryable": False, "class": "error", "reason": "error_not_classified_transient"}
    return {"retryable": False, "class": status or "unknown", "reason": "status_not_retryable"}
