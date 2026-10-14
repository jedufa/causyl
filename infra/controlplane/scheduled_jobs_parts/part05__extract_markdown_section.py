def _extract_markdown_section(markdown: str, heading: str) -> str:
    return _scheduled_jobs_parsing.extract_markdown_section(markdown, heading)


def _extract_validation_commands(builder_content: str) -> List[str]:
    text = str(builder_content or "")
    if not text.splitlines():
        return []

    # Normalize common builder heading drift to the canonical proof section
    # before extraction. Proof remains fail-closed if no concrete commands
    # can be parsed from the normalized section.
    heading_aliases = (
        ("Local verification:", "Validation Commands Run:"),
        ("Local Verification:", "Validation Commands Run:"),
    )
    if "Validation Commands Run:" not in text:
        for source, target in heading_aliases:
            if source in text:
                text = text.replace(source, target, 1)
                break

    lines = text.splitlines()
    accepted_headings = {
        "validation commands run",
        "validation run",
        "validation",
    }

    def _normalize_label(line: str) -> str:
        s = str(line or "").strip()
        if s.startswith("## "):
            s = s[3:].strip()
        elif s.startswith("### "):
            s = s[4:].strip()
        if s.startswith("**") and s.endswith("**") and len(s) > 4:
            s = s[2:-2].strip()
        s = s.rstrip(":").strip().lower()
        return s

    start = -1
    for idx, raw in enumerate(lines):
        if _normalize_label(raw) in accepted_headings:
            start = idx + 1
            break
    if start < 0:
        for heading in ("Validation Commands Run", "Validation run", "Validation"):
            section = _extract_markdown_section(text, heading)
            if section.strip():
                lines = section.splitlines()
                start = 0
                break
    if start < 0:
        return []

    commands: List[str] = []
    for raw in lines[start:]:
        line = str(raw or "").strip()
        if not line:
            continue
        normalized = _normalize_label(line)
        if normalized and normalized not in accepted_headings:
            looks_like_heading = (
                line.startswith("## ")
                or line.startswith("### ")
                or (line.startswith("**") and line.endswith("**"))
            )
            if (
                commands
                and (
                    looks_like_heading
                    or (
                        not line.startswith("-")
                        and not line.startswith("*")
                        and not line.startswith("`")
                    )
                )
            ):
                break
        if line.startswith("- "):
            line = line[2:].strip()
        elif line.startswith("* "):
            line = line[2:].strip()
        if not line:
            continue
        lower = line.lower()
        if lower in {"syntax checks:", "targeted tests:", "targeted tests (ran individually for clarity here):"}:
            continue
        if line not in commands:
            commands.append(line)
    return commands


def _extract_changed_files(builder_content: str, *, repo_root: Optional[Path] = None) -> List[str]:
    text = str(builder_content or "")
    if not text.strip():
        return []
    lines = text.splitlines()
    root = Path(repo_root or _repo_root()).resolve()

    def _normalize_rel(raw: str) -> str:
        value = str(raw or "").strip().strip("`").strip()
        if not value:
            return ""
        file_tag = re.fullmatch(r"<file>([^<]+)</file>", value, flags=re.IGNORECASE)
        if file_tag:
            value = str(file_tag.group(1) or "").strip()
        link_match = re.fullmatch(r"\[[^\]]+\]\(([^)]+)\)", value)
        if link_match:
            value = str(link_match.group(1) or "").strip()
        candidate = Path(value)
        if candidate.is_absolute():
            try:
                return candidate.resolve().relative_to(root).as_posix()
            except Exception:
                return ""
        return value.replace("\\", "/").lstrip("./")

    def _extract_paths_from_line(line: str) -> List[str]:
        out: List[str] = []
        for match in re.findall(r"\[[^\]]+\]\(([^)]+)\)", line):
            rel = _normalize_rel(match)
            if rel:
                out.append(rel)
        for match in re.findall(r"<file>([^<]+)</file>", line, flags=re.IGNORECASE):
            rel = _normalize_rel(match)
            if rel:
                out.append(rel)
        stripped = str(line or "").strip()
        if stripped.startswith("- "):
            payload = stripped[2:].strip()
            normalized_payload = payload.strip("`").strip().lower()
            if normalized_payload in {"none", "(none)", "n/a", "no changes"}:
                return []
            rel = _normalize_rel(payload)
            if rel and rel not in out:
                out.append(rel)
        return out

    changed: List[str] = []
    accepted_headings = {"changed files", "files changed"}
    stop_headings = {
        "validation commands run",
        "validation run",
        "validation",
        "notes",
        "verdict",
    }

    def _normalize_heading(line: str) -> str:
        s = str(line or "").strip()
        if s.startswith("## "):
            s = s[3:].strip()
        elif s.startswith("### "):
            s = s[4:].strip()
        if s.startswith("**") and s.endswith("**") and len(s) > 4:
            s = s[2:-2].strip()
        return s.rstrip(":").strip().lower()

    for line in lines:
        raw = str(line or "").strip()
        normalized = _normalize_heading(raw)
        if normalized in accepted_headings and ":" in raw:
            tail = raw.split(":", 1)[1].strip()
            for rel in _extract_paths_from_line(tail) or [_normalize_rel(tail)]:
                if rel and rel not in changed:
                    changed.append(rel)

    start = -1
    for idx, raw in enumerate(lines):
        if _normalize_heading(raw) in accepted_headings:
            start = idx + 1
            break
    if start < 0:
        for heading in ("Changed files", "Files Changed"):
            section = _extract_markdown_section(text, heading)
            if section.strip():
                lines = section.splitlines()
                start = 0
                break
    if start < 0:
        return changed

    for raw in lines[start:]:
        line = str(raw or "").strip()
        if not line:
            continue
        normalized = _normalize_heading(line)
        if normalized in stop_headings:
            break
        if normalized and normalized not in accepted_headings:
            looks_like_heading = (
                line.startswith("## ")
                or line.startswith("### ")
                or (line.startswith("**") and line.endswith("**"))
            )
            if looks_like_heading or not line.startswith("-"):
                break
        for rel in _extract_paths_from_line(line):
            if rel and rel not in changed:
                changed.append(rel)
    return changed


def _normalize_section_heading_label(line: str) -> str:
    return _scheduled_jobs_parsing.normalize_section_heading_label(line)


def _has_named_heading(text: str, *headings: str) -> bool:
    return _scheduled_jobs_parsing.has_named_heading(text, *headings)


def _extract_named_bullet_block(text: str, heading: str) -> List[str]:
    return _scheduled_jobs_parsing.extract_named_bullet_block(text, heading)


def _extract_verdict_token(content: str) -> str:
    match = re.search(r"(?im)^\s*VERDICT:\s*([A-Z_]+)\b", str(content or ""))
    if not match:
        return ""
    return str(match.group(1) or "").strip().upper()


def _normalize_pending_action_text(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _pending_action_is_explicit(action: str) -> bool:
    normalized = _normalize_pending_action_text(action)
    lowered = normalized.lower()
    if len(normalized) < 12 or len(normalized.split()) < 3:
        return False
    if lowered in _PENDING_ACTION_VAGUE_PHRASES:
        return False
    if any(phrase in lowered for phrase in _PENDING_ACTION_VAGUE_PHRASES):
        return False
    return True


def _normalize_repo_like_path(raw: str, *, repo_root: Optional[Path] = None) -> str:
    value = str(raw or "").strip().strip("`'\"").strip()
    if not value or value.startswith("http://") or value.startswith("https://"):
        return ""
    candidate = Path(value)
    root = Path(repo_root or _repo_root()).resolve()
    if candidate.is_absolute():
        try:
            return candidate.resolve().relative_to(root).as_posix()
        except Exception:
            return ""
    normalized = value.replace("\\", "/").lstrip("./")
    if not normalized or normalized.startswith("../"):
        return ""
    has_suffix = bool(re.search(r"\.[A-Za-z0-9]{1,12}$", normalized.rstrip("/")))
    if "/" not in normalized and not has_suffix:
        return ""
    resolved = (root / normalized.rstrip("/")).resolve()
    try:
        resolved.relative_to(root)
    except Exception:
        return ""
    # Reject prose like `models/tests` unless it names a real repo path or a concrete file-like target.
    if "/" in normalized and not has_suffix and not resolved.exists():
        return ""
    return normalized


def _extract_repo_like_paths(text: str, *, repo_root: Optional[Path] = None) -> List[str]:
    out: List[str] = []
    raw_text = str(text or "")
    candidates: List[str] = []
    for match in re.findall(r"`([^`\n]+)`", raw_text):
        candidates.append(str(match or ""))
    for match in re.findall(r"(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+/?", raw_text):
        candidates.append(str(match or ""))
    for candidate in candidates:
        rel = _normalize_repo_like_path(candidate, repo_root=repo_root)
        if rel and rel not in out:
            out.append(rel)
    return out


def _path_is_allowed(candidate: str, allowed_paths: List[str]) -> bool:
    normalized_candidate = str(candidate or "").strip().replace("\\", "/").lstrip("./")
    for raw in allowed_paths:
        allow = str(raw or "").strip().replace("\\", "/").lstrip("./")
        if not allow:
            continue
        if allow.endswith("/"):
            if normalized_candidate.startswith(allow):
                return True
            continue
        if normalized_candidate == allow:
            return True
    return False


def _parse_pass_pending_actions_contract(
    verifier_content: str,
    *,
    builder_content: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    contract = _parse_bounded_repair_contract(
        verifier_content,
        builder_content=builder_content,
        repo_root=repo_root,
    )
    errors: List[str] = list(contract.get("errors") or [])
    if str(contract.get("verdict") or "") != PASS_PENDING_ACTIONS_VERDICT:
        errors.append("verdict_not_pass_pending_actions")
    contract["ok"] = not errors
    contract["errors"] = errors
    return contract


def _parse_bounded_repair_contract(
    verifier_content: str,
    *,
    builder_content: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    verdict = _extract_verdict_token(verifier_content)
    pending_actions_raw = _extract_named_bullet_block(verifier_content, "Pending Actions")
    stop_rule = _extract_named_bullet_block(verifier_content, "Stop Rule")
    pending_actions: List[str] = []
    errors: List[str] = []
    for raw in pending_actions_raw:
        action = _normalize_pending_action_text(raw)
        if not action:
            continue
        if action not in pending_actions:
            pending_actions.append(action)
    if not pending_actions:
        errors.append("missing_pending_actions_block")
    if len(pending_actions) > 8:
        errors.append("pending_actions_unbounded")
    for action in pending_actions:
        if not _pending_action_is_explicit(action):
            errors.append(f"vague_pending_action:{action}")
    stop_rule_text = "\n".join(stop_rule).lower()
    if not stop_rule:
        errors.append("missing_stop_rule_block")
    if "one builder repair turn only" not in stop_rule_text:
        errors.append("missing_stop_rule_one_repair_turn")
    if "no scope widening" not in stop_rule_text:
        errors.append("missing_stop_rule_no_scope_widening")
    if "if builder cannot complete all actions" not in stop_rule_text or "fail honestly" not in stop_rule_text:
        errors.append("missing_stop_rule_fail_honestly")

    base_paths = _extract_changed_files(builder_content, repo_root=repo_root)
    action_paths = _extract_repo_like_paths("\n".join(pending_actions), repo_root=repo_root)
    if base_paths:
        widened_action_paths = [path for path in action_paths if not _path_is_allowed(path, base_paths)]
        if widened_action_paths:
            errors.append(f"pending_actions_scope_widening:{', '.join(widened_action_paths)}")
    allowed_paths: List[str] = []
    for path in list(base_paths) + list(action_paths):
        if path and path not in allowed_paths:
            allowed_paths.append(path)
    return {
        "ok": not errors,
        "verdict": verdict,
        "pending_actions": pending_actions,
        "stop_rule": stop_rule,
        "allowed_paths": allowed_paths,
        "errors": errors,
    }


def _parse_terminal_fail_contract(verifier_content: str) -> Dict[str, Any]:
    verdict = _extract_verdict_token(verifier_content)
    blockers_raw = _extract_named_bullet_block(verifier_content, "Terminal Blockers")
    blockers: List[str] = []
    errors: List[str] = []
    for raw in blockers_raw:
        action = _normalize_pending_action_text(raw)
        if not action:
            continue
        if action not in blockers:
            blockers.append(action)
    if verdict != "FAIL":
        errors.append("verdict_not_fail")
    if not blockers:
        errors.append("missing_terminal_blockers_block")
    if len(blockers) > 8:
        errors.append("terminal_blockers_unbounded")
    for blocker in blockers:
        if not _pending_action_is_explicit(blocker):
            errors.append(f"vague_terminal_blocker:{blocker}")
    return {
        "ok": not errors,
        "verdict": verdict,
        "terminal_blockers": blockers,
        "errors": errors,
    }


def _extract_pending_action_status_rows(builder_content: str) -> Dict[str, Any]:
    rows_raw = _extract_named_bullet_block(builder_content, "Pending Action Status")
    rows: List[Dict[str, str]] = []
    errors: List[str] = []
    for raw in rows_raw:
        match = re.match(r"(?is)^(APPLIED|NOT[ _-]?APPLIED)\s*:\s*(.*?)(?:\s*\|\s*reason\s*:\s*(.*))?$", str(raw or "").strip())
        if not match:
            errors.append(f"invalid_pending_action_status_row:{str(raw or '').strip()}")
            continue
        state = str(match.group(1) or "").strip().upper().replace("_", " ").replace("-", " ")
        state = "NOT APPLIED" if state.startswith("NOT") else "APPLIED"
        action = _normalize_pending_action_text(str(match.group(2) or ""))
        reason = _normalize_pending_action_text(str(match.group(3) or ""))
        rows.append({"state": state, "action": action, "reason": reason})
    return {"rows": rows, "errors": errors, "present": _has_named_heading(builder_content, "Pending Action Status")}


def _validate_bounded_repair_builder_output(
    builder_content: str,
    *,
    pending_actions: List[str],
    allowed_paths: List[str],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    errors: List[str] = []
    status_parse = _extract_pending_action_status_rows(builder_content)
    action_rows = list(status_parse.get("rows") or [])
    errors.extend(list(status_parse.get("errors") or []))
    if not bool(status_parse.get("present")):
        errors.append("missing_pending_action_status_section")

    action_by_name: Dict[str, Dict[str, str]] = {}
    for row in action_rows:
        name = _normalize_pending_action_text(row.get("action") or "")
        if not name:
            errors.append("blank_pending_action_status")
            continue
        if name in action_by_name:
            errors.append(f"duplicate_pending_action_status:{name}")
            continue
        action_by_name[name] = row

    expected_actions = [_normalize_pending_action_text(action) for action in pending_actions]
    for action in expected_actions:
        row = action_by_name.get(action)
        if row is None:
            errors.append(f"missing_pending_action_status:{action}")
            continue
        if str(row.get("state") or "") != "APPLIED":
            reason = str(row.get("reason") or "").strip()
            errors.append(f"pending_action_not_applied:{action}{' | ' + reason if reason else ''}")
    for action in action_by_name:
        if action not in expected_actions:
            errors.append(f"unexpected_pending_action_status:{action}")

    changed_files_present = _has_named_heading(builder_content, "Changed files", "Files changed")
    if not changed_files_present:
        errors.append("missing_changed_files_section")
    changed_files = _extract_changed_files(builder_content, repo_root=repo_root)
    off_target_paths: List[str] = []
    if changed_files and not allowed_paths:
        errors.append("missing_allowed_repair_scope")
    if allowed_paths:
        off_target_paths = [path for path in changed_files if not _path_is_allowed(path, allowed_paths)]
        if off_target_paths:
            errors.append(f"repair_scope_widening:{', '.join(off_target_paths)}")

    validation_section_present = _has_named_heading(builder_content, "Validation Commands Run", "Validation run", "Validation")
    if not validation_section_present:
        errors.append("missing_validation_commands_section")
    validation_commands = _extract_validation_commands(builder_content)
    return {
        "ok": not errors,
        "errors": errors,
        "changed_files": changed_files,
        "off_target_paths": off_target_paths,
        "validation_commands": validation_commands,
        "pending_action_rows": action_rows,
    }


def _is_bound_status_reconcile_job(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> bool:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    if str(binding.get("type") or "").strip() != "ledger_uid":
        return False
    prompt_text = _resolved_prompt_text(spec, repo_root=repo_root)
    normalized = " ".join(str(prompt_text or "").lower().split())
    return (
        "verify whether ledger item" in normalized
        and "stronger ledger status" in normalized
        and "judge the bound item itself" in normalized
    )


def _status_reconcile_allowed_mutation_paths(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> List[str]:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    uid = str(binding.get("id") or "").strip()
    item_dir = _resolve_ledger_item_dir_for_uid(uid, repo_root=repo_root)
    if item_dir is None:
        root = Path(repo_root or _repo_root()).resolve()
        legacy_matches = sorted((root / "governance" / "ledger" / "items").glob(f"{uid}__*"))
        if not legacy_matches:
            return []
        item_dir = legacy_matches[0]
    root = Path(repo_root or _repo_root()).resolve()
    try:
        item_rel = item_dir.relative_to(root).as_posix()
    except Exception:
        return []
    return [
        f"{item_rel}/meta.json",
        f"{item_rel}/events.jsonl",
        f"{item_rel}/artifacts/",
    ]


def _status_reconcile_scope_violation(
    spec: Dict[str, Any],
    *,
    builder_content: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    if not _is_bound_status_reconcile_job(spec, repo_root=repo_root):
        return {"violated": False, "off_target_paths": [], "allowed_paths": [], "scope_kind": ""}
    changed = _extract_changed_files(builder_content, repo_root=repo_root)
    if not changed:
        return {
            "violated": False,
            "off_target_paths": [],
            "allowed_paths": _status_reconcile_allowed_mutation_paths(spec, repo_root=repo_root),
            "scope_kind": "status_reconcile",
        }
    allowed = _status_reconcile_allowed_mutation_paths(spec, repo_root=repo_root)
    def _allowed(path: str) -> bool:
        candidate = str(path or "").strip().replace("\\", "/").lstrip("./")
        for allow in allowed:
            normalized = str(allow or "").strip().replace("\\", "/").lstrip("./")
            if not normalized:
                continue
            if normalized.endswith("/"):
                if candidate.startswith(normalized):
                    return True
            elif candidate == normalized:
                return True
        return False
    off_target = [path for path in changed if not _allowed(path)]
    return {
        "violated": bool(off_target),
        "off_target_paths": off_target,
        "allowed_paths": allowed,
        "scope_kind": "status_reconcile",
    }


def _is_execution_family_closeout_job(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> bool:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    contract = prompt_meta.get("execution_family_closeout") if isinstance(prompt_meta.get("execution_family_closeout"), dict) else {}
    if str(contract.get("parent_uid") or "").strip():
        return True
    prompt_text = _resolved_prompt_text(spec, repo_root=repo_root)
    normalized = " ".join(str(prompt_text or "").lower().split())
    return "close the execution family rooted at" in normalized and "required_final_child_uid" in normalized


def _execution_family_closeout_allowed_mutation_paths(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> List[str]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    contract = prompt_meta.get("execution_family_closeout") if isinstance(prompt_meta.get("execution_family_closeout"), dict) else {}
    parent_uid = str(contract.get("parent_uid") or "").strip()
    child_uids = [str(item).strip() for item in list(contract.get("child_uids") or []) if str(item).strip()]
    source_intent_uid = str(contract.get("source_intent_uid") or "").strip()
    labels: List[str] = []
    if parent_uid:
        labels.append(f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/meta.json")
        labels.append(f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/events.jsonl")
        labels.append(f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/artifacts/")
    for uid in child_uids:
        labels.append(f"governance/ledger/patch_instruction_sheets/**/{uid}__*/meta.json")
        labels.append(f"governance/ledger/patch_instruction_sheets/**/{uid}__*/events.jsonl")
        labels.append(f"governance/ledger/patch_instruction_sheets/**/{uid}__*/artifacts/")
    if source_intent_uid:
        labels.append(f"governance/ledger/intent_sheets/**/{source_intent_uid}__*/meta.json")
        labels.append(f"governance/ledger/intent_sheets/**/{source_intent_uid}__*/events.jsonl")
        labels.append(f"governance/ledger/intent_sheets/**/{source_intent_uid}__*/artifacts/")
    labels.append("governance/ledger/patch_instruction_sheets/uid_index.json")
    labels.append("runtime/state/commit_journal/pending/")
    labels.extend(_execution_family_closeout_repo_paths_from_receipts(spec, repo_root=repo_root))
    return labels


def _execution_family_closeout_repo_paths_from_receipts(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> List[str]:
    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    contract = prompt_meta.get("execution_family_closeout") if isinstance(prompt_meta.get("execution_family_closeout"), dict) else {}
    child_job_ids = [str(item).strip() for item in list(contract.get("child_job_ids") or []) if str(item).strip()]
    out: List[str] = []
    for job_id in child_job_ids:
        state = load_state(job_id, repo_root=repo_root)
        if not _is_success_status(state.get("last_status")):
            continue
        receipt_path = Path(str(state.get("last_receipt_path") or "").strip())
        if not receipt_path.exists():
            continue
        receipt = _read_json(receipt_path)
        repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
        declared = list(repo_residue.get("declared_changed_files") or [])
        for raw in declared:
            path = str(raw or "").strip().replace("\\", "/").lstrip("./")
            if path and path not in out:
                out.append(path)
    return out


def _execution_family_closeout_scope_violation(
    spec: Dict[str, Any],
    *,
    builder_content: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    if not _is_execution_family_closeout_job(spec, repo_root=repo_root):
        return {"violated": False, "off_target_paths": [], "allowed_paths": [], "scope_kind": ""}
    changed = _extract_changed_files(builder_content, repo_root=repo_root)
    allowed = _execution_family_closeout_allowed_mutation_paths(spec, repo_root=repo_root)
    if not changed:
        return {"violated": False, "off_target_paths": [], "allowed_paths": allowed, "scope_kind": "execution_family_closeout"}

    prompt_meta = spec.get("prompt_meta") if isinstance(spec.get("prompt_meta"), dict) else {}
    contract = prompt_meta.get("execution_family_closeout") if isinstance(prompt_meta.get("execution_family_closeout"), dict) else {}
    parent_uid = str(contract.get("parent_uid") or "").strip()
    child_uids = [str(item).strip() for item in list(contract.get("child_uids") or []) if str(item).strip()]
    source_intent_uid = str(contract.get("source_intent_uid") or "").strip()

    def _matches_family_item(path: str, *, uid: str, root_prefix: str) -> bool:
        candidate = str(path or "").strip().replace("\\", "/").lstrip("./")
        marker = f"/{uid}__"
        if not candidate.startswith(root_prefix):
            return False
        needle_haystack = f"/{candidate}"
        if marker not in needle_haystack:
            return False
        tail = needle_haystack.split(marker, 1)[1].lstrip("/")
        if "/" not in tail:
            return False
        tail = tail.split("/", 1)[1]
        return tail == "meta.json" or tail == "events.jsonl" or tail.startswith("artifacts/")

    def _allowed(path: str) -> bool:
        candidate = str(path or "").strip().replace("\\", "/").lstrip("./")
        if candidate == "governance/ledger/patch_instruction_sheets/uid_index.json":
            return True
        if parent_uid and _matches_family_item(candidate, uid=parent_uid, root_prefix="governance/ledger/patch_instruction_sheets/"):
            return True
        for uid in child_uids:
            if _matches_family_item(candidate, uid=uid, root_prefix="governance/ledger/patch_instruction_sheets/"):
                return True
        if source_intent_uid and _matches_family_item(candidate, uid=source_intent_uid, root_prefix="governance/ledger/intent_sheets/"):
            return True
        if candidate in _execution_family_closeout_repo_paths_from_receipts(spec, repo_root=repo_root):
            return True
        return False

    off_target = [path for path in changed if not _allowed(path)]
    return {
        "violated": bool(off_target),
        "off_target_paths": off_target,
        "allowed_paths": allowed,
        "scope_kind": "execution_family_closeout",
    }


def _repo_guard_preflight(
    *,
    spec: Dict[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    job_kind = _normalize_job_kind(spec.get("job_kind"))
    policy = _normalize_policy(spec.get("policy"), job_kind=job_kind)
    guard = {
        "mode": str(policy.get("repo_change_mode") or "none"),
        "blocked": False,
        "reason": "",
        "dirty_worktree": False,
        "dirty_preview": "",
        "commit_journal_pending_count": 0,
        "commit_journal_invalid_count": 0,
    }
    if not _is_repo_change_strict(policy):
        return guard
    if bool(policy.get("require_clean_worktree")):
        dirty = _git_status_porcelain(repo_root=repo_root)
        if dirty:
            guard["dirty_preview"] = "\n".join(str(dirty).splitlines()[:8])
            if str(dirty).startswith("[git_status_error]"):
                guard["blocked"] = True
                guard["reason"] = "repo_guard_git_status_error"
                return guard
            guard["dirty_worktree"] = True
            guard["blocked"] = True
            guard["reason"] = "repo_guard_dirty_worktree"
            return guard
    if bool(policy.get("require_commit_journal")):
        try:
            journal = _commit_journal.scan_pending_entries(repo=Path(repo_root or _repo_root()).resolve())
        except Exception as exc:
            guard["blocked"] = True
            guard["reason"] = "repo_guard_commit_journal_scan_error"
            guard["error"] = str(exc)
            return guard
        guard["commit_journal_pending_count"] = int(journal.get("pending_count") or 0)
        guard["commit_journal_invalid_count"] = int(journal.get("invalid_count") or 0)
    return guard


def _build_repo_change_evidence(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    evidence: Dict[str, Any] = {
        "git_dirty": False,
        "dirty_preview": "",
        "commit_journal_pending_count": 0,
        "commit_journal_invalid_count": 0,
        "commit_journal_audit_ok": False,
        "commit_journal_uncovered_paths": [],
    }
    dirty = _git_status_porcelain(repo_root=repo_root)
    if dirty and not str(dirty).startswith("[git_status_error]"):
        evidence["git_dirty"] = True
        evidence["dirty_preview"] = "\n".join(str(dirty).splitlines()[:12])
    elif str(dirty).startswith("[git_status_error]"):
        evidence["git_status_error"] = str(dirty)
    try:
        journal = _commit_journal.scan_pending_entries(repo=Path(repo_root or _repo_root()).resolve())
        evidence["commit_journal_pending_count"] = int(journal.get("pending_count") or 0)
        evidence["commit_journal_invalid_count"] = int(journal.get("invalid_count") or 0)
    except Exception as exc:
        evidence["commit_journal_scan_error"] = str(exc)
        return evidence
    try:
        audit = _commit_journal.audit_worktree_coverage(repo=Path(repo_root or _repo_root()).resolve())
        evidence["commit_journal_audit_ok"] = bool(audit.get("ok"))
        evidence["commit_journal_uncovered_paths"] = list(audit.get("uncovered_paths") or [])[:12]
    except Exception as exc:
        evidence["commit_journal_audit_error"] = str(exc)
    return evidence



def _parse_git_status_entries(*, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    dirty = _git_status_porcelain(repo_root=root)
    snapshot: Dict[str, Any] = {
        "git_available": True,
        "git_status_error": "",
        "dirty_preview": "",
        "entries": [],
        "dirty_path_count": 0,
        "tracked_dirty_paths": [],
        "untracked_paths": [],
        "dirty_paths": [],
    }
    if not dirty:
        return snapshot
    if str(dirty).startswith("[git_status_error]"):
        snapshot["git_available"] = False
        snapshot["git_status_error"] = str(dirty)
        return snapshot

    def _digest_path(relpath: str) -> Dict[str, Any]:
        candidate = (root / relpath).resolve()
        row: Dict[str, Any] = {"exists": candidate.exists(), "is_dir": False, "sha256": "", "size_bytes": 0}
        try:
            candidate.relative_to(root)
        except Exception:
            return row
        if not candidate.exists():
            return row
        if candidate.is_dir():
            row["is_dir"] = True
            return row
        try:
            payload = candidate.read_bytes()
        except Exception:
            return row
        row["sha256"] = hashlib.sha256(payload).hexdigest()
        row["size_bytes"] = len(payload)
        return row

    entries = []
    tracked_dirty_paths = []
    untracked_paths = []
    for raw in str(dirty).splitlines():
        line = str(raw or "").rstrip()
        if len(line) < 4:
            continue
        status_code = line[:2]
        relpath = line[3:].strip()
        if " -> " in relpath:
            relpath = relpath.split(" -> ", 1)[1].strip()
        relpath = relpath.replace("\\", "/").lstrip("./")
        if not relpath:
            continue
        untracked = status_code == "??"
        tracked_dirty = not untracked
        digest = _digest_path(relpath)
        row = {
            "path": relpath,
            "status": status_code,
            "tracked_dirty": bool(tracked_dirty),
            "untracked": bool(untracked),
            **digest,
        }
        entries.append(row)
        if tracked_dirty and relpath not in tracked_dirty_paths:
            tracked_dirty_paths.append(relpath)
        if untracked and relpath not in untracked_paths:
            untracked_paths.append(relpath)
    snapshot["entries"] = entries
    snapshot["dirty_path_count"] = len(entries)
    snapshot["tracked_dirty_paths"] = tracked_dirty_paths
    snapshot["untracked_paths"] = untracked_paths
    snapshot["dirty_paths"] = tracked_dirty_paths + [path for path in untracked_paths if path not in tracked_dirty_paths]
    snapshot["dirty_preview"] = "\n".join(str(dirty).splitlines()[:12])
    return snapshot


def _repo_snapshot_entry_signature(row: Dict[str, Any]) -> str:
    entry = row if isinstance(row, dict) else {}
    return "|".join(
        [
            str(entry.get("status") or ""),
            str(bool(entry.get("tracked_dirty"))).lower(),
            str(bool(entry.get("untracked"))).lower(),
            str(bool(entry.get("exists"))).lower(),
            str(bool(entry.get("is_dir"))).lower(),
            str(entry.get("size_bytes") or 0),
            str(entry.get("sha256") or ""),
        ]
    )


def _dedup_strs(values: List[str]) -> List[str]:
    out: List[str] = []
    for raw in list(values or []):
        item = str(raw or "").strip()
        if item and item not in out:
            out.append(item)
    return out


def _collect_receipt_declared_changed_files(receipt: Dict[str, Any], *, repo_root: Optional[Path] = None) -> List[str]:
    out: List[str] = []
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    iterations = list(loop.get("iterations") or [])
    for row in iterations:
        if not isinstance(row, dict):
            continue
        builder = row.get("builder") if isinstance(row.get("builder"), dict) else {}
        for relpath in _extract_changed_files(str(builder.get("content") or ""), repo_root=repo_root):
            if relpath not in out:
                out.append(relpath)
    assistant = receipt.get("assistant") if isinstance(receipt.get("assistant"), dict) else {}
    for relpath in _extract_changed_files(str(assistant.get("content") or ""), repo_root=repo_root):
        if relpath not in out:
            out.append(relpath)
    pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
    summary = pipeline_execution.get("summary") if isinstance(pipeline_execution.get("summary"), dict) else {}
    artifacts = summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    validated_path_txt = str(artifacts.get("validated_artifact_path") or "").strip()
    if validated_path_txt:
        payload = _read_json(Path(validated_path_txt))
        validated = dict(dict(payload.get("result") or {}).get("validated_artifact") or {})
        for relpath in list(validated.get("target_files") or []):
            rel = _normalize_repo_like_path(str(relpath or ""), repo_root=repo_root)
            if rel and rel not in out:
                out.append(rel)
    return out


def _collect_receipt_validation_commands(receipt: Dict[str, Any]) -> List[str]:
    execution_proof = receipt.get("execution_proof") if isinstance(receipt.get("execution_proof"), dict) else {}
    commands = [str(cmd) for cmd in list(execution_proof.get("validation_commands") or []) if str(cmd or "").strip()]
    if commands:
        return _dedup_strs(commands)
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    collected: List[str] = []
    for row in list(loop.get("iterations") or []):
        if not isinstance(row, dict):
            continue
        builder = row.get("builder") if isinstance(row.get("builder"), dict) else {}
        collected.extend(_extract_validation_commands(str(builder.get("content") or "")))
    assistant = receipt.get("assistant") if isinstance(receipt.get("assistant"), dict) else {}
    collected.extend(_extract_validation_commands(str(assistant.get("content") or "")))
    return _dedup_strs(collected)


def _is_broad_validation_command(command: str) -> bool:
    normalized = " ".join(str(command or "").strip().lower().split())
    if not normalized:
        return False
    if normalized in {"pytest", "pytest -q", "python -m pytest", "python -m pytest -q", "nox", "tox"}:
        return True
    if normalized.startswith("pytest ") or normalized.startswith("python -m pytest "):
        targeted_markers = (" tests/", "::", " -k ", " -m ", ".py", " --maxfail=", " --lf", " --ff")
        return not any(marker in normalized for marker in targeted_markers)
    return False


def _validation_claim_hygiene(*, spec: Dict[str, Any], receipt: Dict[str, Any]) -> Dict[str, Any]:
    commands = _collect_receipt_validation_commands(receipt)
    execution_proof = receipt.get("execution_proof") if isinstance(receipt.get("execution_proof"), dict) else {}
    explicit_proof_gap_reason = str(execution_proof.get("proof_gap_reason") or "").strip()
    broad_commands = [cmd for cmd in commands if _is_broad_validation_command(cmd)]
    if not commands:
        evidence_scope = "no_local_validation_commands"
        claim_scope = "manual_or_missing_validation"
    elif broad_commands and len(broad_commands) == len(commands):
        evidence_scope = "broad_suite_commands"
        claim_scope = "broad_validation_scope"
    elif broad_commands:
        evidence_scope = "mixed_broad_and_targeted_commands"
        claim_scope = "bounded_to_explicit_validation_commands"
    else:
        evidence_scope = "targeted_commands_only"
        claim_scope = "bounded_to_explicit_validation_commands"

    remaining_proof_gaps: List[str] = []
    if explicit_proof_gap_reason:
        remaining_proof_gaps.append(explicit_proof_gap_reason)
    if not commands:
        remaining_proof_gaps.append("no_local_validation_commands_captured")
    elif evidence_scope != "broad_suite_commands":
        remaining_proof_gaps.append("broader_subsystem_or_end_to_end_behavior_not_proven")

    broad_success_claim_allowed = bool(commands) and evidence_scope == "broad_suite_commands" and not explicit_proof_gap_reason
    if broad_success_claim_allowed:
        summary = "Broader completion language is allowed only to the extent named by the explicit broad validation commands in evidence."
    elif not commands:
        summary = "No local validation commands were captured. Any success claim must stay manual, bounded, and provisional."
    else:
        summary = "Only the exact validation commands listed are in evidence; broader subsystem success remains unproven."

    return {
        "schema_version": "scheduled_job_validation_claim_hygiene_v1",
        "validation_command_count": len(commands),
        "validation_commands": commands,
        "evidence_scope": evidence_scope,
        "claim_scope": claim_scope,
        "manual_only": not bool(commands),
        "broad_success_claim_allowed": bool(broad_success_claim_allowed),
        "remaining_proof_gaps": _dedup_strs(remaining_proof_gaps),
        "summary": summary,
    }


def _scheduled_executor_of_record_audit(*, spec: Dict[str, Any], receipt: Dict[str, Any]) -> Dict[str, Any]:
    execution_contract = receipt.get("execution_contract") if isinstance(receipt.get("execution_contract"), dict) else {}
    controller_runtime = execution_contract.get("controller_runtime") if isinstance(execution_contract.get("controller_runtime"), dict) else {}
    runtime = receipt.get("runtime") if isinstance(receipt.get("runtime"), dict) else dict(spec.get("runtime") or {})
    model_telemetry = receipt.get("model_telemetry") if isinstance(receipt.get("model_telemetry"), dict) else {}
    compiled_execution = receipt.get("compiled_execution") if isinstance(receipt.get("compiled_execution"), dict) else {}
    policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    swarm = spec.get("swarm") if isinstance(spec.get("swarm"), dict) else {}
    swarm_write = swarm.get("write_policy") if isinstance(swarm.get("write_policy"), dict) else {}

    write_capable = bool(
        str(compiled_execution.get("claim_scope") or "") == "bounded_repo_change"
        or _normalize_job_kind(spec.get("job_kind")) == "build_verify_loop"
        or _is_repo_change_strict(policy)
        or (
            _normalize_job_kind(spec.get("job_kind")) == "swarm_launch"
            and str(swarm_write.get("mode") or "").strip().lower() == "bounded_patch"
        )
    )
    required_executor = "codex" if write_capable else str(execution_contract.get("execution_owner") or "").strip().lower()
    actual_executor = str(
        execution_contract.get("execution_owner")
        or model_telemetry.get("executor_agent_tech")
        or controller_runtime.get("agent_tech")
        or runtime.get("executor_agent_tech")
        or runtime.get("agent_tech")
        or ""
    ).strip().lower()
    execution_mode = str(execution_contract.get("execution_mode") or runtime.get("execution_mode") or "").strip().lower()
    pipeline_executor_mode = str(execution_contract.get("pipeline_executor_mode") or runtime.get("pipeline_executor_mode") or "").strip().lower()
    contract_ok = True
    violation_reason = ""
    if write_capable and actual_executor != "codex":
        contract_ok = False
        violation_reason = f"write_capable_job_executed_by_non_codex:{actual_executor or 'unknown'}"
    elif write_capable and execution_mode == "advisory_only":
        contract_ok = False
        violation_reason = "write_capable_job_cannot_be_advisory_only"

    return {
        "schema_version": "scheduled_job_executor_of_record_audit_v1",
        "write_capable": bool(write_capable),
        "required_executor": required_executor,
        "actual_executor": actual_executor,
        "execution_mode": execution_mode,
        "pipeline_executor_mode": pipeline_executor_mode,
        "contract_ok": bool(contract_ok),
        "violation_reason": violation_reason,
        "compatibility_note": "historical_receipts_may_lack_executor_of_record_fields; current receipts emit this audit block explicitly",
    }


def _collect_receipt_artifact_refs(receipt: Dict[str, Any]) -> List[str]:
    out: List[str] = []
    def _walk(obj: Any) -> None:
        if isinstance(obj, dict):
            for key, value in obj.items():
                key_txt = str(key or "").lower()
                if isinstance(value, str):
                    rel = value.replace("\\", "/").strip()
                    if rel and ("path" in key_txt or "artifact" in key_txt or rel.endswith((".json", ".md", ".patch", ".txt", ".zip"))):
                        if rel not in out:
                            out.append(rel)
                else:
                    _walk(value)
        elif isinstance(obj, list):
            for row in obj:
                _walk(row)

    _walk(receipt.get("execution_proof") if isinstance(receipt.get("execution_proof"), dict) else {})
    _walk(receipt.get("compiled_execution") if isinstance(receipt.get("compiled_execution"), dict) else {})
    _walk(receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {})
    return out[:64]



def _forensic_debug_stage(receipt: Dict[str, Any]) -> str:
    pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
    if str(pipeline_execution.get("mode") or "").strip():
        return f"pipeline:{str(pipeline_execution.get('mode') or '').strip()}"
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    if list(loop.get("iterations") or []):
        return "build_verify_loop"
    if isinstance(receipt.get("assistant"), dict):
        return "single_turn"
    ownership = receipt.get("ownership") if isinstance(receipt.get("ownership"), dict) else {}
    if str(ownership.get("handoff_state") or "").strip() == "not_launched":
        return "nonlaunch"
    return "terminal"


def _forensic_debug_classification(receipt: Dict[str, Any]) -> str:
    status = str(receipt.get("status") or "").strip().lower()
    ownership = receipt.get("ownership") if isinstance(receipt.get("ownership"), dict) else {}
    if str(ownership.get("handoff_state") or "").strip() == "not_launched":
        return "nonlaunch"
    if status in SUCCESS_TERMINAL_STATUSES:
        return "success"
    return "failure"


def _forensic_debug_next_hint(receipt: Dict[str, Any]) -> str:
    lifecycle = receipt.get("lifecycle") if isinstance(receipt.get("lifecycle"), dict) else {}
    cancel = lifecycle.get("cancel") if isinstance(lifecycle.get("cancel"), dict) else {}
    timeout = lifecycle.get("timeout") if isinstance(lifecycle.get("timeout"), dict) else {}
    execution_proof = receipt.get("execution_proof") if isinstance(receipt.get("execution_proof"), dict) else {}
    repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
    reason_code = str(receipt.get("reason_code") or "").strip()
    status = str(receipt.get("status") or "").strip().lower()

    if bool(cancel.get("requested")):
        return "Decide whether to relaunch or preserve the cancelled state before resuming work."
    if bool(timeout.get("expired")):
        return "Inspect the timeout boundary and decide whether the runtime budget or workload shape needs adjustment."
    if bool(execution_proof.get("proof_gap")):
        return "Inspect missing validation evidence and narrow any completion claim before retrying."
    if len(list(repo_residue.get("dirty_paths_not_declared") or [])):
        return "Inspect undeclared dirty paths before any retry so residue does not get misreported as clean."
    if reason_code:
        return f"Review reason_code `{reason_code}` first; it is the narrowest durable clue for the terminal outcome."
    if status in {"failed", "failed_verification", "error", "timed_out", "cancelled", "blocked"}:
        return "Inspect the receipt, report, and referenced artifacts before deciding whether a bounded retry is justified."
    return "No immediate debug action is required from the current durable receipt."


def _build_forensic_debug_kit(
    *,
    spec: Dict[str, Any],
    receipt: Dict[str, Any],
    receipt_path: str,
    report_path: str,
) -> Dict[str, Any]:
    execution_proof = receipt.get("execution_proof") if isinstance(receipt.get("execution_proof"), dict) else {}
    repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
    binding = receipt.get("binding") if isinstance(receipt.get("binding"), dict) else dict(spec.get("binding") or {})
    artifact_refs = _collect_receipt_artifact_refs(receipt)
    return {
        "schema_version": "scheduled_job_forensic_debug_kit_v1",
        "required_for_terminal_debug": True,
        "classification": _forensic_debug_classification(receipt),
        "job_id": str(receipt.get("job_id") or spec.get("job_id") or "").strip(),
        "run_id": str(receipt.get("run_id") or "").strip(),
        "job_kind": _normalize_job_kind(receipt.get("job_kind") or spec.get("job_kind")),
        "binding": {
            "type": str(binding.get("type") or "").strip(),
            "id": str(binding.get("id") or "").strip(),
        },
        "stage": _forensic_debug_stage(receipt),
        "status": str(receipt.get("status") or "").strip(),
        "reason_code": str(receipt.get("reason_code") or "").strip(),
        "pipeline_summary_status": str(receipt.get("pipeline_summary_status") or "").strip(),
        "pipeline_final_status": str(receipt.get("pipeline_final_status") or "").strip(),
        "last_error": str(receipt.get("error") or "").strip(),
        "proof_gap": bool(execution_proof.get("proof_gap")),
        "proof_gap_reason": str(execution_proof.get("proof_gap_reason") or "").strip(),
        "validation_commands": _collect_receipt_validation_commands(receipt),
        "evidence_refs": artifact_refs,
        "repo_residue_summary": {
            "introduced_dirty_path_count": len(list(repo_residue.get("introduced_dirty_paths") or [])),
            "undeclared_dirty_path_count": len(list(repo_residue.get("dirty_paths_not_declared") or [])),
            "artifact_ref_count": len(list(repo_residue.get("artifact_refs") or [])),
        },
        "next_decision_hint": _forensic_debug_next_hint(receipt),
        "receipt_path": str(receipt_path or "").strip(),
        "report_path": str(report_path or "").strip(),
        "redaction_guidance": "Use infra/bin/safe_exec for diagnostics that may print secrets.",
    }


def _build_repo_residue_evidence(
    *,
    baseline: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
    declared_changed_files: Optional[List[str]] = None,
    validation_commands: Optional[List[str]] = None,
    artifact_refs: Optional[List[str]] = None,
    source: str = "runtime",
) -> Dict[str, Any]:
    baseline_snapshot = dict(baseline or _parse_git_status_entries(repo_root=repo_root))
    current_snapshot = _parse_git_status_entries(repo_root=repo_root)
    baseline_entries = {
        str(row.get("path") or ""): row
        for row in list(baseline_snapshot.get("entries") or [])
        if isinstance(row, dict) and str(row.get("path") or "").strip()
    }
    current_entries = {
        str(row.get("path") or ""): row
        for row in list(current_snapshot.get("entries") or [])
        if isinstance(row, dict) and str(row.get("path") or "").strip()
    }
    introduced_dirty_paths: List[str] = []
    introduced_tracked_dirty_paths: List[str] = []
    introduced_untracked_paths: List[str] = []
    touched_preexisting_dirty_paths: List[str] = []
    for relpath, current_row in current_entries.items():
        base_row = baseline_entries.get(relpath)
        if base_row is None:
            introduced_dirty_paths.append(relpath)
            if bool(current_row.get("tracked_dirty")):
                introduced_tracked_dirty_paths.append(relpath)
            if bool(current_row.get("untracked")):
                introduced_untracked_paths.append(relpath)
            continue
        if _repo_snapshot_entry_signature(base_row) != _repo_snapshot_entry_signature(current_row):
            touched_preexisting_dirty_paths.append(relpath)
    declared = _dedup_strs(list(declared_changed_files or []))
    current_dirty_paths = _dedup_strs(list(current_snapshot.get("dirty_paths") or []))
    undeclared = [path for path in current_dirty_paths if path not in declared]
    return {
        "schema_version": "scheduled_job_repo_residue_v1",
        "captured_utc": _utc_iso(),
        "source": str(source or "runtime"),
        "git_available": bool(current_snapshot.get("git_available", True) and baseline_snapshot.get("git_available", True)),
        "git_status_error": str(current_snapshot.get("git_status_error") or baseline_snapshot.get("git_status_error") or ""),
        "baseline_dirty_path_count": int(baseline_snapshot.get("dirty_path_count") or 0),
        "current_dirty_path_count": int(current_snapshot.get("dirty_path_count") or 0),
        "baseline_dirty_paths": _dedup_strs(list(baseline_snapshot.get("dirty_paths") or [])),
        "current_dirty_paths": current_dirty_paths,
        "introduced_dirty_paths": sorted(introduced_dirty_paths),
        "introduced_tracked_dirty_paths": sorted(introduced_tracked_dirty_paths),
        "introduced_untracked_paths": sorted(introduced_untracked_paths),
        "touched_preexisting_dirty_paths": sorted(touched_preexisting_dirty_paths),
        "declared_changed_files": declared,
        "dirty_paths_not_declared": sorted(undeclared),
        "validation_commands": _dedup_strs(list(validation_commands or [])),
        "artifact_refs": _dedup_strs(list(artifact_refs or []))[:64],
        "repo_change_evidence": _build_repo_change_evidence(repo_root=repo_root),
    }


def _pending_operator_request(state: Dict[str, Any]) -> str:
    st = state if isinstance(state, dict) else {}
    if str(st.get("cancel_requested_utc") or "").strip():
        return "cancel_requested"
    if str(st.get("postpone_requested_utc") or "").strip():
        return "postpone_requested"
    if str(st.get("run_now_requested_utc") or "").strip():
        return "run_now_requested"
    return ""


def _build_scheduler_reconciliation(
    spec: Dict[str, Any],
    *,
    state: Dict[str, Any],
    due: Dict[str, Any],
    now: Optional[_dt.datetime] = None,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    st = dict(state or {})
    current = now or _utc_now()
    active_run_id = str(st.get("active_run_id") or "").strip()
    active_claim = {"present": False, "stale": False, "reason": "missing", "run_id": "", "heartbeat_utc": "", "age_s": None}
    if active_run_id:
        policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
        active_claim = _active_claim_health(
            str(spec.get("job_id") or ""),
            repo_root=repo_root,
            max_runtime_seconds=int(policy.get("max_runtime_seconds") or 1800),
            expected_run_id=active_run_id,
            now=current,
        )
    operator_request = _pending_operator_request(st)
    queue_backpressure_reason = _scheduler_backpressure_reason(spec, trigger="scheduler", force=False)
    due_reason = str((due or {}).get("reason") or "")
    blocking_reason = ""
    if queue_backpressure_reason:
        blocking_reason = queue_backpressure_reason
    elif active_run_id and bool(active_claim.get("present")) and not bool(active_claim.get("stale")):
        blocking_reason = "active_run"
    elif due_reason and not bool((due or {}).get("eligible")):
        blocking_reason = due_reason
    return {
        "schema_version": "scheduled_job_reconciliation_v1",
        "evaluated_utc": _utc_iso(current),
        "due_eligible": bool((due or {}).get("eligible")),
        "due_reason": due_reason,
        "slot_key": str((due or {}).get("slot_key") or ""),
        "due_utc": str((due or {}).get("due_utc") or ""),
        "active_run_id": active_run_id,
        "active_claim": {
            "present": bool(active_claim.get("present")),
            "stale": bool(active_claim.get("stale")),
            "reason": str(active_claim.get("reason") or ""),
            "run_id": str(active_claim.get("run_id") or ""),
            "heartbeat_utc": str(active_claim.get("heartbeat_utc") or ""),
            "age_s": active_claim.get("age_s"),
        },
        "operator_request": operator_request,
        "queue_backpressure_reason": queue_backpressure_reason,
        "blocked": bool(blocking_reason),
        "blocking_reason": blocking_reason,
        "status_projection": str(st.get("last_status") or ("running" if active_run_id else "idle")),
    }


def _queue_backpressure_reason() -> str:
    if _execution_queue is None:
        return ""
    try:
        payload = _execution_queue.status()
    except Exception:
        return "execution_queue_status_unavailable"
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    queue_status = str(state.get("status") or "").strip().lower()
    if queue_status == "paused":
        return "execution_queue_paused"
    if bool(state.get("stop_requested")):
        return "execution_queue_stop_requested"
    return ""


def _scheduler_backpressure_reason(spec: Dict[str, Any], *, trigger: str, force: bool) -> str:
    if force or trigger != "scheduler":
        return ""
    if _normalize_job_kind(spec.get("job_kind")) != "build_verify_loop":
        return ""
    return _queue_backpressure_reason()


def _evaluate_job_budget_admission(
    spec: Dict[str, Any],
    *,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    job_uid = str(spec.get("job_id") or "").strip()
    policy = _normalize_policy(spec.get("policy"), job_kind=_normalize_job_kind(spec.get("job_kind")))
    if not bool(policy.get("require_budget_admission")):
        return {
            "enforced": False,
            "allowed": True,
            "job_uid": job_uid,
            "reason": "budget_admission_not_required",
        }
    if not job_uid:
        return {
            "enforced": True,
            "allowed": False,
            "job_uid": job_uid,
            "reason": "job_budget_job_uid_missing",
            "message": "job budget admission requires a non-empty job_id",
        }

    try:
        recovery = _job_budget_cost_ledger_store.ensure_recovery_outcome_recorded(
            job_uid,
            recorded_at_utc=_utc_iso(),
            repo_root=repo_root,
        )
        if recovery.get("present"):
            policy_status = str(recovery.get("status") or "")
            if policy_status in {"blocked", "review_required"}:
                return {
                    "enforced": True,
                    "allowed": False,
                    "job_uid": job_uid,
                    "reason": f"job_budget_recovery_{policy_status}",
                    "message": str(recovery.get("message") or "persisted job budget recovery state blocks launch"),
                    "snapshot": dict(recovery.get("snapshot") or {}),
                    "recovery_outcome": recovery,
                }

        allocations = _job_budget_cost_ledger_store.read_allocation_records(job_uid, repo_root=repo_root)
        cost_events = _job_budget_cost_ledger_store.read_cost_event_records(job_uid, repo_root=repo_root)
        if not allocations and not cost_events:
            return {
                "enforced": True,
                "allowed": False,
                "job_uid": job_uid,
                "reason": "job_budget_ledger_missing",
                "message": "required durable job budget ledger is missing",
                "snapshot": {
                    "job_uid": job_uid,
                    "unit": "",
                    "allocated_minor_units": 0,
                    "cost_debit_minor_units": 0,
                    "cost_credit_minor_units": 0,
                    "remaining_minor_units": 0,
                    "allocation_count": 0,
                    "cost_event_count": 0,
                    "last_durable_event": None,
                },
            }
        snapshot = _job_budget_cost_ledger_store.replay_job_budget_state(job_uid, repo_root=repo_root)
        remaining_minor_units = int(snapshot.get("remaining_minor_units") or 0)
        snapshot = {
            **dict(snapshot),
            "allocation_count": len(allocations),
            "cost_event_count": len(cost_events),
            "last_durable_event": _job_budget_cost_ledger_store.last_durable_event(job_uid, repo_root=repo_root),
        }
        if remaining_minor_units <= 0:
            return {
                "enforced": True,
                "allowed": False,
                "job_uid": job_uid,
                "reason": "job_budget_exhausted",
                "message": (
                    "remaining budget is exhausted: "
                    f"{remaining_minor_units} {str(snapshot.get('unit') or '').strip() or 'minor_units'}"
                ),
                "snapshot": snapshot,
                "recovery_outcome": recovery,
            }
        result = {
            "enforced": True,
            "allowed": True,
            "job_uid": job_uid,
            "reason": "",
            "message": "",
            "snapshot": snapshot,
        }
        if recovery.get("warning"):
            result["reason"] = "job_budget_recovery_warning"
            result["message"] = str(recovery.get("message") or "persisted job budget recovery state warns future runs")
        if recovery.get("present"):
            result["recovery_outcome"] = recovery
        return result
    except Exception as exc:
        return {
            "enforced": True,
            "allowed": False,
            "job_uid": job_uid,
            "reason": "job_budget_ledger_unreadable",
            "message": f"required durable job budget ledger is unreadable: {exc}",
            "error": str(exc),
        }


def _surface_for_trigger(trigger: str) -> str:
    text = str(trigger or "").strip().lower()
    if text in {"watchdog"}:
        return "watchdog"
    if text in {"pipeline_healer", "healer"}:
        return "healer"
    return "scheduler"


def _bind_scheduler_launch(
    *,
    spec: Dict[str, Any],
    binding_override: Optional[Dict[str, Any]] = None,
    run_id: str,
    trigger: str,
    status: str,
    reason: str,
    event: str,
    receipt_path: str,
    report_path: str,
    repo_root: Optional[Path],
    reference_status: str,
    reference_status_reason: str,
    writer: str,
    launch_cause: str,
    surface: str = "",
) -> Dict[str, Any]:
    binding = dict(binding_override or (spec.get("binding") if isinstance(spec.get("binding"), dict) else {}) or {})
    binding_type = str(binding.get("type") or "").strip().lower()
    if not binding or binding_type == "none":
        return {"ok": True, "skipped": True, "reason": "no_binding_required"}
    try:
        from infra.webui import server_lab_assistant_delegation_bindings as _bindings
    except Exception as exc:
        return {"ok": False, "error": str(exc), "reason": "binding_import_failed"}
    surface_label = str(surface or _surface_for_trigger(trigger)).strip().lower() or "scheduler"
    job_id = str(spec.get("job_id") or "")
    summary = job_display_title(spec) or job_id
    requested_action = f"run scheduled job {job_id}".strip()
    launch_result = {
        "job_id": job_id,
        "run_id": run_id,
        "status": status,
        "reference_status": reference_status,
        "reference_status_reason": reference_status_reason,
    }
    input_payload = {
        "job_id": job_id,
        "run_id": run_id,
        "trigger": str(trigger or ""),
        "status": str(status or ""),
        "reason": str(reason or ""),
        "event": str(event or ""),
        "receipt_path": str(receipt_path or ""),
        "report_path": str(report_path or ""),
    }
    try:
        return _bindings.bind_surface_launch(
            repo_root or _repo_root(),
            surface=surface_label,
            binding=binding,
            summary=summary,
            requested_action=requested_action,
            launch_result=launch_result,
            input_payload=input_payload,
            writer=writer,
            launch_cause=launch_cause,
        )
    except Exception as exc:
        return {
            "ok": False,
            "reason": "binding_launch_failed",
            "error": str(exc),
            "surface": surface_label,
        }


def _seed_single_turn_owner_binding(
    spec: Dict[str, Any],
    *,
    run_id: str,
    trigger: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    def _scheduler_seed_task_id_for_ledger_uid(ledger_uid: str) -> str:
        text = str(ledger_uid or "").strip().upper()
        if not text:
            return ""
        parts = [part for part in text.split("-") if part]
        tail = parts[-1].lower() if parts else "task"
        stamp = parts[-2].lower() if len(parts) >= 2 else "bound"
        return f"task_{tail}_{stamp}"

    def _select_existing_scheduler_owner_task(matches: List[Dict[str, Any]]) -> str:
        rows = [dict(row) for row in list(matches or []) if isinstance(row, dict)]
        if not rows:
            return ""
        non_intake = [row for row in rows if str(row.get("source") or "").strip().lower() != "operator_intake"]
        if len(non_intake) == 1:
            return str(non_intake[0].get("task_id") or "").strip()
        scheduler_rows = [row for row in rows if str(row.get("source") or "").strip().lower() == "scheduler"]
        if len(scheduler_rows) == 1:
            return str(scheduler_rows[0].get("task_id") or "").strip()
        return ""

    binding = dict(spec.get("binding") if isinstance(spec.get("binding"), dict) else {})
    binding_type = str(binding.get("type") or "").strip().lower()
    if binding_type not in {"ledger_uid", "task_id"}:
        return binding
    try:
        from infra.webui import server_lab_assistant_host as _host
        from infra.webui import server_lab_assistant_task_register as _register
    except Exception:
        return binding

    root = Path(repo_root or _repo_root()).resolve()
    try:
        _host.bootstrap_host_baseline(root)
    except Exception:
        return binding

    if binding_type == "ledger_uid":
        ledger_uid = str(binding.get("id") or "").strip()
        if not ledger_uid:
            return binding
        try:
            snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=True)
            current = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
            active_tasks = list(current.get("active_tasks") or []) if isinstance(current, dict) else []
            matches = [
                dict(row)
                for row in active_tasks
                if isinstance(row, dict) and str(row.get("ledger_uid") or "").strip() == ledger_uid
            ]
            task_id = _select_existing_scheduler_owner_task(matches)
            if not task_id and len(matches) == 1:
                task_id = str(matches[0].get("task_id") or "").strip()
            if not task_id:
                task_id = _scheduler_seed_task_id_for_ledger_uid(ledger_uid)
            if not task_id:
                return binding
            _register.upsert_task(
                root,
                expected_generation=int(current.get("write_generation") or 0),
                writer="scheduler.launch.owner_seed",
                task={
                    "task_id": task_id,
                    "kind": "software",
                    "title": job_display_title(spec) or task_id,
                    "status": "active",
                    "owner": "scheduler",
                    "source": "scheduler",
                    "ledger_uid": ledger_uid,
                    "next_expected_action": "run scheduled job",
                },
            )
        except Exception:
            return binding
        if task_id:
            return {"task_id": task_id, "ledger_uid": ledger_uid}
        return binding

    task_id = str(binding.get("id") or "").strip()
    if not task_id:
        return binding
    try:
        snapshot = _register.read_task_register_snapshot(root, initialize_if_missing=True)
        current = snapshot.get("task_register") if isinstance(snapshot.get("task_register"), dict) else {}
        active_tasks = list(current.get("active_tasks") or []) if isinstance(current, dict) else []
        if any(str(row.get("task_id") or "").strip() == task_id for row in active_tasks if isinstance(row, dict)):
            return binding
        _register.upsert_task(
            root,
            expected_generation=int(current.get("write_generation") or 0),
            writer="scheduler.launch.task_seed",
            task={
                "task_id": task_id,
                "kind": "software",
                "title": job_display_title(spec) or task_id,
                "status": "active",
                "owner": "assistant",
                "source": "scheduler",
                "ledger_uid": str(binding.get("ledger_uid") or "").strip(),
                "next_expected_action": "run scheduled job",
            },
        )
    except Exception:
        return binding
    if task_id:
        seeded_binding = {"task_id": task_id}
        if str(binding.get("ledger_uid") or "").strip():
            seeded_binding["ledger_uid"] = str(binding.get("ledger_uid") or "").strip()
        return seeded_binding
    return binding
