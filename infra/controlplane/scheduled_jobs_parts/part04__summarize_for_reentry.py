from infra.controlplane import continuation_packet_bootstrap as _continuation_packet_bootstrap
from infra.controlplane import continuation_context_attachment as _continuation_attachment
from infra.webui import server_lab_assistant_task_register as _lab_task_register


def _summarize_for_reentry(text: str, *, limit: int = 700) -> str:
    flat = " ".join(str(text or "").strip().split())
    if not flat:
        return "(none)"
    if len(flat) <= limit:
        return flat
    return flat[:limit].rstrip() + " ..."


def _continuation_packet_reentry_line(spec: Dict[str, Any], *, repo_root: Optional[Path]) -> str:
    if repo_root is None:
        return ""
    try:
        register_snapshot = _lab_task_register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    except Exception as exc:
        return f"- continuation_packet_bootstrap: blocked; reason=task_register_unavailable:{type(exc).__name__}"
    snapshot = _continuation_packet_bootstrap.packet_snapshot_for_binding(
        spec.get("binding") if isinstance(spec.get("binding"), dict) else {},
        task_register=register_snapshot.get("task_register") if isinstance(register_snapshot, dict) else None,
        repo_root=Path(repo_root).resolve(),
    )
    mode = str(snapshot.get("mode") or "").strip()
    if not mode or mode == "none":
        return ""
    rendered = _continuation_packet_bootstrap.render_packet_line(snapshot)
    detail = rendered[2:] if rendered.startswith("- ") else rendered
    return f"- continuation_packet_bootstrap: {detail}"


def _continuation_inspection_reentry_lines(spec: Dict[str, Any], *, repo_root: Optional[Path]) -> List[str]:
    if repo_root is None:
        return []
    try:
        register_snapshot = _lab_task_register.read_task_register_snapshot(repo_root, initialize_if_missing=False)
    except Exception as exc:
        return [f"- continuation_inspection: blocked; reason=task_register_unavailable:{type(exc).__name__}"]
    projection = _continuation_attachment.inspect_continuation_state_for_binding(
        spec.get("binding") if isinstance(spec.get("binding"), dict) else {},
        task_register=register_snapshot.get("task_register") if isinstance(register_snapshot, dict) else None,
        repo_root=Path(repo_root).resolve(),
    )
    rendered = _continuation_attachment.render_surface_projection_lines(projection)
    return [
        f"- continuation_inspection: {line[2:] if line.startswith('- ') else line}"
        for line in rendered[:2]
    ]


def _scheduled_reentry_section(
    *,
    run_id: str,
    stage: str,
    iteration: int,
    max_iterations: int,
    current_objective: str,
    current_work_item: str,
    latest_builder_summary: str = "",
    latest_verifier_summary: str = "",
    unresolved_issues: str = "",
    reentry_reason: str = "",
    job_shape_summary: Optional[Dict[str, Any]] = None,
    spec: Optional[Dict[str, Any]] = None,
    repo_root: Optional[Path] = None,
) -> List[str]:
    shape_summary = job_shape_summary if isinstance(job_shape_summary, dict) else {}
    lines = [
        "[Scheduled Reentry State]",
        f"- run_id: {str(run_id or '').strip()}",
        f"- stage: {str(stage or '').strip()}",
        f"- iteration: {int(iteration)}/{int(max_iterations)}",
        f"- current_objective: {_summarize_for_reentry(current_objective, limit=500)}",
        f"- current_work_item: {_summarize_for_reentry(current_work_item, limit=300)}",
        f"- latest_builder_output_summary: {_summarize_for_reentry(latest_builder_summary, limit=500)}",
        f"- latest_verifier_findings_summary: {_summarize_for_reentry(latest_verifier_summary, limit=500)}",
        f"- unresolved_issues: {_summarize_for_reentry(unresolved_issues, limit=400)}",
        f"- explicit_reentry_reason: {_summarize_for_reentry(reentry_reason or 'scheduled turn resume with structured state only; do not rely on older session transcript history', limit=300)}",
    ]
    if shape_summary:
        lines.extend(
            [
                f"- job_shape: {str(shape_summary.get('job_shape') or '(missing)')}",
                f"- job_shape_source: {str(shape_summary.get('job_shape_source') or '(missing)')}",
                f"- job_shape_compatibility_mode: {str(bool(shape_summary.get('job_shape_compatibility_mode'))).lower()}",
                f"- job_shape_operator_summary: {_summarize_for_reentry(str(shape_summary.get('operator_summary') or shape_summary.get('error') or '(missing)'), limit=220)}",
                f"- job_shape_progress_semantics: {_summarize_for_reentry(str(shape_summary.get('progress_semantics') or '(missing)'), limit=220)}",
                f"- job_shape_recovery_semantics: {_summarize_for_reentry(str(shape_summary.get('recovery_semantics') or '(missing)'), limit=220)}",
                f"- job_shape_child_launch_semantics: {_summarize_for_reentry(str(shape_summary.get('child_launch_semantics') or '(missing)'), limit=220)}",
            ]
        )
    reentry_packet_line = _continuation_packet_reentry_line(spec or {}, repo_root=repo_root)
    if reentry_packet_line:
        lines.append(reentry_packet_line)
    lines.extend(_continuation_inspection_reentry_lines(spec or {}, repo_root=repo_root))
    return lines


def _build_loop_builder_message(
    spec: Dict[str, Any],
    *,
    run_id: str,
    trigger: str,
    iteration: int,
    max_iterations: int,
    verifier_feedback: str = "",
    pending_actions: Optional[List[str]] = None,
    stop_rule: Optional[List[str]] = None,
    allowed_paths: Optional[List[str]] = None,
    repo_root: Optional[Path] = None,
) -> str:
    normalized_pending_actions = [_normalize_pending_action_text(item) for item in list(pending_actions or []) if _normalize_pending_action_text(item)]
    normalized_stop_rule = [_normalize_pending_action_text(item) for item in list(stop_rule or []) if _normalize_pending_action_text(item)]
    normalized_allowed_paths = [str(path).strip() for path in list(allowed_paths or []) if str(path).strip()]
    repair_turn = bool(normalized_pending_actions)
    job_shape_summary = _job_shape_semantics_projection(
        spec,
        raw_state="",
        active_run=True,
        active_loop_stage="repair" if repair_turn else "builder",
    )
    lines = [
        _scheduled_controller_contract(spec, stage="builder"),
        "",        "[Scheduled Build-Verify Loop]",
        f"job_id: {str(spec.get('job_id') or '').strip()}",
        f"run_id: {str(run_id or '').strip()}",
        f"trigger: {str(trigger or '').strip() or 'scheduler'}",
        f"iteration: {iteration}/{max_iterations}",
        "",
        "Objective:",
        _resolved_prompt_text(spec, repo_root=repo_root),
        "",
    ]
    if repair_turn:
        lines.extend(
            [
                "Bounded repair turn:",
                "- this is the one allowed repair turn after `VERDICT: PASS_PENDING_ACTIONS`",
                "- complete only the listed pending actions; do not reopen broader analysis",
                "- do not introduce unrelated mutations or widen scope to siblings/children unless a pending action explicitly requires it",
                "- if you cannot complete every pending action, say so explicitly and fail honestly",
                "",
                "Pending Actions:",
            ]
            + [f"- {action}" for action in normalized_pending_actions]
        )
        lines.extend(
            [
                "",
                "Stop Rule:",
            ]
            + [f"- {item}" for item in normalized_stop_rule]
        )
        if normalized_allowed_paths:
            lines.extend(["", "Allowed repair scope:"] + [f"- {path}" for path in normalized_allowed_paths])
        lines.extend(
            [
                "",
                "Required response:",
                "- include the heading exactly as `Pending Action Status:`",
                "- under `Pending Action Status:`, report every pending action exactly once as `- APPLIED: <exact action>` or `- NOT APPLIED: <exact action> | reason: <why not>`",
                "- include the heading exactly as `Changed files:` or `Files changed:`",
                "- list every changed file under that heading, or say `Changed files: none` when nothing changed",
                "- emit the heading exactly as `Validation Commands Run:`",
                "- do not rename or substitute any required heading",
                "- under `Validation Commands Run:`, list concrete commands executed and their outcomes in a parseable form; non-code checks are acceptable for governance- or ledger-only work",
                "- call out blockers or assumptions explicitly",
            ]
        )
    else:
        lines.extend(
            [
                "Required response:",
                "- perform the requested build/implementation work",
                "- summarize changes made",
                "- emit the heading exactly as `Validation Commands Run:`",
                "- do not rename or substitute that heading",
                "- include `Validation Commands Run:` even when validation is minimal or a no-op",
                "- under `Validation Commands Run:`, list concrete commands executed and their outcomes in a parseable form; non-code checks are acceptable for governance- or ledger-only work",
                "- call out blockers or assumptions explicitly",
                "- use this exact reply skeleton:",
                "  Changed files:",
                "  - <path> or `none`",
                "  Validation Commands Run:",
                "  - <command>: <result> or `none (reason)`",
                "  Notes:",
                "  - <brief summary or blocker>",
            ]
        )
    if _is_bound_status_reconcile_job(spec, repo_root=repo_root):
        lines.extend(
            [                "- inspect child or dependency state as evidence only; do not mutate child items during parent reconcile",
                "- do not repair stale dependency snapshots in sibling or child `pis.md` files",
                "- do not widen scope to scheduler-test cleanup, validation-suite cleanup, or unrelated dependency-truth repair",
                "- if the bound parent remains draft, record that honestly in the bound parent only",
            ]
        )
    if _is_execution_family_closeout_job(spec, repo_root=repo_root):
        lines.extend(
            [
                "- audit the integrated family, not just the ledger statuses in isolation",
                "- mutate only the declared execution family plus repo files already implicated by successful child receipts for that family",
                "- do not mutate unrelated ledger families, unrelated scheduler cleanup, or broad repo hygiene during family closeout",
                "- if the family is not honestly integrated yet, repair the residual issue inside the family-touched repo scope before attempting closure",
                "- promote statuses only when durable child evidence and the repaired integrated state actually earn `verified`; otherwise fail honestly instead of forcing closure",
                "- required final child evidence is mandatory when the contract declares one",
            ]
        )
    lines.extend(
        [
            "",
            *_scheduled_reentry_section(
                run_id=run_id,
                stage="builder",
                iteration=iteration,
                max_iterations=max_iterations,
                current_objective=_resolved_prompt_text(spec, repo_root=repo_root),
                current_work_item=(
                    "Complete only the bounded pending actions from the verifier and stop after reporting exact action status."
                    if repair_turn
                    else (
                        "Address the latest verifier findings first and then continue the smallest bounded implementation step."
                        if verifier_feedback.strip()
                        else "Execute the requested implementation work and emit concrete validation evidence."
                    )
                ),
                latest_verifier_summary=(
                    " | ".join(normalized_pending_actions)
                    if repair_turn
                    else verifier_feedback
                ),
                unresolved_issues=(
                    " | ".join(normalized_pending_actions)
                    if repair_turn
                    else verifier_feedback
                ),
                reentry_reason=(
                    "bounded repair turn after PASS_PENDING_ACTIONS; no second verifier pass will run"
                    if repair_turn
                    else "new builder iteration in scheduled build/verify loop"
                ),
                job_shape_summary=job_shape_summary,
                spec=spec,
                repo_root=repo_root,
            ),
        ]
    )
    job_shape_context = _job_shape_context_obligation_projection(spec)
    bundle_id = str(job_shape_context.get("required_context_bundle") or spec.get("required_context_bundle") or "").strip()
    if bundle_id:
        lines.extend(["", f"required_context_bundle: {bundle_id}"])
    add_refs = [str(x).strip() for x in list(spec.get("additional_context_refs") or []) if str(x).strip()]
    if add_refs:
        lines.extend(["", "additional_context_refs:"] + [f"- {ref}" for ref in add_refs])
    ban_refs = [str(x).strip() for x in list(spec.get("forbidden_context_refs") or []) if str(x).strip()]
    if ban_refs:
        lines.extend(["", "forbidden_context_refs:"] + [f"- {ref}" for ref in ban_refs])
    refs = _effective_context_refs(spec)
    if refs:
        lines.extend(["", "resolved_context_refs:"] + [f"- {ref}" for ref in refs])
    if job_shape_context:
        lines.extend(
            [
                "",
                f"context_reentry_precedence: {str(job_shape_context.get('reentry_precedence') or '')}",
                f"context_transcript_summary_policy: {str(job_shape_context.get('transcript_summary_policy') or '')}",
            ]
        )
    if verifier_feedback.strip() and not repair_turn:
        lines.extend(["", "Verifier feedback from previous iteration:", verifier_feedback.strip()])
    return "\n".join(lines).strip()

def _build_loop_verifier_message(
    spec: Dict[str, Any],
    *,
    run_id: str,
    trigger: str,
    iteration: int,
    max_iterations: int,
    builder_output: str,
    pass_marker: str,
    repo_root: Optional[Path] = None,
) -> str:
    # Keep verifier payloads bounded so CLI argument length limits are not exceeded.
    loop = _normalize_loop(spec.get("loop"))
    loop_policy = str(loop.get("loop_policy") or DEFAULT_BUILD_VERIFY_LOOP_POLICY)
    loop_policy_label = _loop_policy_operator_label(loop_policy)
    max_builder_chars = 12000
    builder_text = str(builder_output or "").strip()
    if len(builder_text) > max_builder_chars:
        omitted = len(builder_text) - max_builder_chars
        builder_text = (
            builder_text[:max_builder_chars]
            + f"\n\n[truncated {omitted} chars from builder output for verifier prompt size safety]"
        )
    lines = [
        _scheduled_controller_contract(spec, stage="verifier"),
        "",
        "[Scheduled Build-Verify Loop]",
        f"job_id: {str(spec.get('job_id') or '').strip()}",
        f"run_id: {str(run_id or '').strip()}",
        f"trigger: {str(trigger or '').strip() or 'scheduler'}",
        f"iteration: {iteration}/{max_iterations}",
        f"loop policy: {loop_policy_label}",
        "",
        "Review task:",
        _resolved_prompt_text(spec, repo_root=repo_root),
        "",
        "Verifier instructions:",
        f"- start the first line with exactly `{pass_marker}` if the task passes",
        "- otherwise start the first line with exactly `VERDICT: FAIL` unless this loop policy explicitly allows another verdict",
        "- do not put headings, summaries, or structural-validity lines before the verdict line",
        "- use a fail-closed review stance: do not pass on vague confidence",
        "- PASS only when all are true:",
        "  1) scope and boundary rules in the job prompt are met",        "  2) required output sections are present in builder output",
        "  3) validation evidence is concrete (commands + outcomes), not implied",
        "  4) no obvious high-impact gap remains that is cheap to close now",
        "- do not emit `HOST_VERIFICATION_REQUEST_JSON` in this scheduled verifier path",
        "- do not request unsupported ledger-only host actions such as `ledger_query`; inspect repo and ledger files directly instead",
        "- if local evidence is insufficient, return `VERDICT: FAIL` with the smallest concrete next builder feedback instead of requesting host verification",
        "- if an obvious missing test/guard/check is identified, return `VERDICT: FAIL` with exact next patch feedback",
        "- give concise evidence-backed findings with file references when possible",
        "- if it fails, give the smallest concrete next feedback for the builder",
        "- use this exact opening template:",
        f"  {pass_marker} or VERDICT: FAIL or VERDICT: UNKNOWN or VERDICT: PASS_PENDING_ACTIONS",
        "  Findings:",
        "  - <brief evidence-backed finding or `none`>",
        "  Boundary Review:",
        "  - scope_alignment: pass|fail|unknown - <brief reason>",
        "  - contract_alignment: pass|fail|unknown - <brief reason>",
        "  Proof Review:",
        "  - evidence_status: sufficient|insufficient|missing - <brief reason>",
        "  - pass_fail_closed_check: satisfied|unsatisfied - <brief reason>",
        "  Validation Review:",
        "  - commands_reviewed: <brief command summary or `none`>",
        "  - outcome_support: sufficient|insufficient|not_run - <brief reason>",
        "- if you include confidence or convergence hints, put them in a separate `Confidence:` block after Validation Review",
        "- confidence language is advisory only and never substitutes for proof or validation review",
    ]
    job_shape_summary = _job_shape_semantics_projection(
        spec,
        raw_state="",
        active_run=True,
        active_loop_stage="verifier",
    )
    if loop_policy == DEFAULT_BUILD_VERIFY_LOOP_POLICY:
        lines.extend(
            [
                f"- this job uses the default `{BUILD_VERIFY_REPAIR_OPERATOR_LABEL}` loop, also called `{ONE_POINT_FIVE_ITERATIONS_OPERATOR_LABEL}`",
                f"- allowed verdicts in this loop policy: `{pass_marker}`, `VERDICT: FAIL`, `VERDICT: UNKNOWN`, `VERDICT: PASS_PENDING_ACTIONS`",
                "- ordinary builder shortcomings are not terminal in this mode; use a bounded repair path instead of immediate failure",
                "- on the first verifier pass, if one bounded repair turn can finish the work honestly, you MUST use `VERDICT: PASS_PENDING_ACTIONS`",
                "- on the first verifier pass, plain `VERDICT: FAIL` is reserved for terminal blockers only",
                "- if you emit first-pass `VERDICT: FAIL`, you MUST also include an exact `Terminal Blockers:` block with explicit blocker bullets",
                "- use `VERDICT: PASS_PENDING_ACTIONS` only when one bounded repair turn can finish the work without needing a second verifier pass",
                "- if you emit `VERDICT: PASS_PENDING_ACTIONS`, you MUST also include these exact companion blocks:",
                "  Pending Actions:",
                "  - <explicit finite action>",
                "  Stop Rule:",
                "  - one builder repair turn only",
                "  - no scope widening",
                "  - if builder cannot complete all actions, fail honestly",
                "  Terminal Blockers:",
                "  - <explicit terminal blocker when repair is not allowed>",
                "- pending actions must be explicit, finite, narrow, checkable, and in-scope",
                "- reject vague action lists such as `improve reasoning`, `tighten evidence`, `clean up issues`, or `review dependencies more carefully`",
                "- do not use `VERDICT: PASS_PENDING_ACTIONS` when multiple repair rounds or a final verifier confirmation are still required",
            ]
        )
    lines.extend(_high_impact_stabilization_verifier_contract(spec, repo_root=repo_root))
    if _supports_confidence_early_stop(spec, repo_root=repo_root):
        lines.extend(
            [                "- end the response with an exact `Loop Control:` block in this shape:",                "  Loop Control:",
                "  - confidence_percent: <integer 0-100>",
                "  - confidence_delta_this_iteration: <integer>",
                "  - expected_next_iteration_gain: <integer>",
                "  - recommended_action: continue|stop|escalate",
                "  - good_enough_for: draft_only|operator_review|not_promotable",
                "- do not rename `Loop Control:` or any field names inside it",
                "- every field above is required on every verifier reply for this job class; do not leave blanks",
                "- if the same blocker is repeating and you expect no meaningful new signal next iteration, still emit concrete numeric values and set `recommended_action: stop`",
                "- these confidence fields do not change the verdict and must never substitute for proof",
            ]
        )
    lines.extend(
        [
            "",
            *_scheduled_reentry_section(
                run_id=run_id,
                stage="verifier",
                iteration=iteration,
                max_iterations=max_iterations,
                current_objective=_resolved_prompt_text(spec, repo_root=repo_root),
                current_work_item="Review the latest builder output against the scheduled job objective and reply with a strict verdict.",
                latest_builder_summary=builder_text,
                reentry_reason="new verifier iteration in scheduled build/verify loop",
                job_shape_summary=job_shape_summary,
                spec=spec,
                repo_root=repo_root,
            ),
            "",
            "Latest builder output to review:",
            builder_text,
        ]
    )
    bundle_id = str(spec.get("required_context_bundle") or "").strip()
    if bundle_id:
        lines.extend(["", f"required_context_bundle: {bundle_id}"])
    add_refs = [str(x).strip() for x in list(spec.get("additional_context_refs") or []) if str(x).strip()]
    if add_refs:
        lines.extend(["", "additional_context_refs:"] + [f"- {ref}" for ref in add_refs])
    ban_refs = [str(x).strip() for x in list(spec.get("forbidden_context_refs") or []) if str(x).strip()]
    if ban_refs:
        lines.extend(["", "forbidden_context_refs:"] + [f"- {ref}" for ref in ban_refs])
    refs = _effective_context_refs(spec)
    if refs:
        lines.extend(["", "resolved_context_refs:"] + [f"- {ref}" for ref in refs])
    return "\n".join(lines).strip()


def _loop_passed(content: str, *, pass_marker: str) -> bool:
    marker_token = _extract_verdict_token(pass_marker)
    content_token = _extract_verdict_token(content)
    if marker_token:
        return bool(content_token) and content_token == marker_token
    marker_line = str(pass_marker or "").strip()
    if not marker_line:
        return False
    for raw in str(content or "").splitlines():
        line = str(raw or "").strip()
        if line:
            return line == marker_line
    return False


def _supports_confidence_early_stop(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> bool:
    if _is_bound_status_reconcile_job(spec, repo_root=repo_root):
        return True
    if _is_execution_family_closeout_job(spec, repo_root=repo_root):
        return True
    prompt_text = " ".join(str(_resolved_prompt_text(spec, repo_root=repo_root) or "").lower().split())
    return any(token in prompt_text for token in ("audit", "governance check", "governance review"))


def _bound_ledger_item_title(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> str:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    if str(binding.get("type") or "").strip() != "ledger_uid":
        return ""
    uid = str(binding.get("id") or "").strip()
    if not uid:
        return ""
    item_dir = _resolve_ledger_item_dir_for_uid(uid, repo_root=repo_root)
    if item_dir is None:
        return ""
    pis_path = item_dir / "pis.md"
    if pis_path.exists():
        lines = pis_path.read_text(encoding="utf-8").splitlines()
        for idx, raw in enumerate(lines):
            if str(raw or "").strip() == "## 1. Title":
                for candidate in lines[idx + 1 :]:
                    title = " ".join(str(candidate or "").split()).strip()
                    if title:
                        return title
                break
    slug = str(item_dir.name or "")
    if "__" in slug:
        slug = slug.split("__", 1)[1]
    return slug.replace("-", " ").strip()


def _is_high_impact_stabilization_job(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> bool:
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    if str(binding.get("type") or "").strip() != "ledger_uid":
        return False
    uid = str(binding.get("id") or "").strip()
    if not uid:
        return False
    item_dir = _resolve_ledger_item_dir_for_uid(uid, repo_root=repo_root)
    if item_dir is not None:
        root = Path(repo_root or _repo_root()).resolve()
        try:
            rel = item_dir.relative_to(root).as_posix()
        except Exception:
            rel = str(item_dir).replace("\\", "/")
        if (
            rel.startswith("governance/ledger/patch_instruction_sheets/active/2026/04/")
            or "/active/2026/04/" in rel
        ):
            return True
    prompt_text = " ".join(str(_resolved_prompt_text(spec, repo_root=repo_root) or "").lower().split())
    return any(token in prompt_text for token in ("stabilization", "stabilize", "integrity", "hardening", "corruption", "executor-of-record"))


def _high_impact_stabilization_verifier_contract(spec: Dict[str, Any], *, repo_root: Optional[Path] = None) -> List[str]:
    if not _is_high_impact_stabilization_job(spec, repo_root=repo_root):
        return []
    binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
    uid = str(binding.get("id") or "").strip()
    title = _bound_ledger_item_title(spec, repo_root=repo_root)
    title_text = f" (`{title}`)" if title else ""
    return [
        f"- this is a high-impact stabilization review for bound ledger item `{uid}`{title_text}; generic PASS language is invalid",
        "- include these exact review blocks in the response:",
        "  Findings by Severity:",
        "  - <severity critical|high|medium|low> | <file:line or artifact path> | <finding or `none`>",
        "  Boundary Review:",
        "  - in_scope_only: yes|no",
        "  - sibling_overlap_found: yes|no",
        "  - boundary_notes: <short text>",
        "  Proof Review:",
        "  - validation_evidence_sufficient: yes|no",
        "  - remaining_proof_gaps: <explicit text or `none`>",
        "  Validation Run Review:",
        "  - commands_checked: <explicit command list or `none`>",
        "  - results_checked: <pass/fail summary>",
        "  One-Iteration Assessment:",
        "  - disposition: keep_as_is|patch_only|rebuild_required",
        "- if any exact block above is missing, fail closed with `VERDICT: FAIL`",
        "- one-iteration PASS is only acceptable when `disposition: keep_as_is` and the proof review says remaining_proof_gaps: none",
    ]


def _extract_loop_control_raw_value(text: str, field_name: str) -> Optional[str]:
    pattern = rf"(?im)^\s*(?:-\s*)?{re.escape(field_name)}\s*:\s*(.*?)\s*$"
    match = re.search(pattern, str(text or ""))
    if not match:
        return None
    return str(match.group(1) or "").strip()


def _extract_int_field(text: str, field_name: str) -> Tuple[Optional[int], bool, bool]:
    raw = _extract_loop_control_raw_value(text, field_name)
    if raw is None:
        return None, False, False
    if raw == "":
        return None, True, False
    if re.fullmatch(r"-?\d+", raw):
        try:
            return int(raw), True, False
        except Exception:
            return None, True, True
    return None, True, True


def _extract_enum_field(text: str, field_name: str, allowed: Set[str]) -> Tuple[str, bool, bool]:
    raw = _extract_loop_control_raw_value(text, field_name)
    if raw is None:
        return "", False, False
    if raw == "":
        return "", True, False
    value = raw.lower()
    return (value, True, False) if value in allowed else ("", True, True)


def _verifier_loop_control_metadata(content: str) -> Dict[str, Any]:
    text = str(content or "")
    confidence_percent, confidence_present, confidence_invalid = _extract_int_field(text, "confidence_percent")
    confidence_delta, delta_present, delta_invalid = _extract_int_field(text, "confidence_delta_this_iteration")
    expected_gain, gain_present, gain_invalid = _extract_int_field(text, "expected_next_iteration_gain")
    recommended_action, action_present, action_invalid = _extract_enum_field(text, "recommended_action", {"continue", "stop", "escalate"})
    good_enough_for, good_enough_present, good_enough_invalid = _extract_enum_field(text, "good_enough_for", {"draft_only", "operator_review", "not_promotable"})
    loop_control_header_present = bool(re.search(r"(?im)^\s*loop control\s*:\s*$", text))
    detected_fields = {
        "confidence_percent": confidence_present,
        "confidence_delta_this_iteration": delta_present,
        "expected_next_iteration_gain": gain_present,
        "recommended_action": action_present,
        "good_enough_for": good_enough_present,
    }
    any_field_present = any(detected_fields.values())
    parse_error = any((confidence_invalid, delta_invalid, gain_invalid, action_invalid, good_enough_invalid))
    lines: List[str] = []
    for raw in text.splitlines():
        line = str(raw or "").strip()
        if not line:
            continue
        lowered = line.lower()
        if lowered == "loop control:":
            continue
        if lowered == "- loop control:":
            continue
        if lowered.startswith("confidence_percent:"):
            continue
        if lowered.startswith("- confidence_percent:"):
            continue
        if lowered.startswith("confidence_delta_this_iteration:"):
            continue
        if lowered.startswith("- confidence_delta_this_iteration:"):
            continue
        if lowered.startswith("expected_next_iteration_gain:"):
            continue
        if lowered.startswith("- expected_next_iteration_gain:"):
            continue
        if lowered.startswith("recommended_action:"):
            continue
        if lowered.startswith("- recommended_action:"):
            continue
        if lowered.startswith("good_enough_for:"):
            continue
        if lowered.startswith("- good_enough_for:"):
            continue
        lines.append(line)
    blocker_signature = _sha256_text("\n".join(lines).strip()) if lines else ""
    return {
        "confidence_percent": confidence_percent,
        "confidence_delta_this_iteration": confidence_delta,
        "expected_next_iteration_gain": expected_gain,
        "recommended_action": recommended_action,
        "good_enough_for": good_enough_for,
        "loop_control_header_present": loop_control_header_present,
        "loop_control_missing": not any_field_present,
        "loop_control_parse_error": parse_error,
        "loop_control_detected_fields": detected_fields,
        "blocker_signature": blocker_signature,
    }


def _loop_control_contract_violation(loop_control: Dict[str, Any]) -> bool:
    if not isinstance(loop_control, dict):
        return True
    if bool(loop_control.get("loop_control_missing")) or bool(loop_control.get("loop_control_parse_error")):
        return True
    if loop_control.get("confidence_percent") is None:
        return True
    if loop_control.get("confidence_delta_this_iteration") is None:
        return True
    if loop_control.get("expected_next_iteration_gain") is None:
        return True
    if str(loop_control.get("recommended_action") or "") not in {"continue", "stop", "escalate"}:
        return True
    return False


def _should_inject_loop_control_stop_fallback(
    spec: Dict[str, Any],
    iterations: List[Dict[str, Any]],
    *,
    repo_root: Optional[Path] = None,
    min_repeat_count: int = 2,
) -> bool:
    if not _supports_confidence_early_stop(spec, repo_root=repo_root):
        return False
    if len(iterations) < min_repeat_count:
        return False
    recent = iterations[-min_repeat_count:]
    blocker_signatures: List[str] = []
    evidence_signatures: List[str] = []
    for row in recent:
        verifier = row.get("verifier") if isinstance(row.get("verifier"), dict) else {}
        content = str(verifier.get("content") or "").strip()
        if not content.startswith("VERDICT: FAIL"):
            return False
        meta = verifier.get("meta") if isinstance(verifier.get("meta"), dict) else {}
        loop_control = meta.get("loop_control") if isinstance(meta.get("loop_control"), dict) else {}
        blocker_signatures.append(str(loop_control.get("blocker_signature") or ""))
        evidence_signatures.append(_iteration_evidence_signature(row, repo_root=repo_root))
    if not blocker_signatures or not blocker_signatures[-1]:
        return False
    if len(set(blocker_signatures)) != 1:
        return False
    if len(set(evidence_signatures)) != 1:
        return False
    return True


def _enforce_verifier_loop_control(
    spec: Dict[str, Any],
    iterations: List[Dict[str, Any]],
    *,
    repo_root: Optional[Path] = None,
) -> None:
    if not _supports_confidence_early_stop(spec, repo_root=repo_root):
        return
    if not iterations:
        return
    current = iterations[-1]
    verifier = current.get("verifier") if isinstance(current.get("verifier"), dict) else {}
    content = str(verifier.get("content") or "").strip()
    if not content.startswith("VERDICT: FAIL"):
        return
    meta = verifier.get("meta") if isinstance(verifier.get("meta"), dict) else {}
    loop_control = dict(meta.get("loop_control") or {})
    if not _loop_control_contract_violation(loop_control):
        loop_control["loop_control_contract_violation"] = False
        loop_control.setdefault("loop_control_fallback_source", "")
        meta["loop_control"] = loop_control
        verifier["meta"] = meta
        return

    loop_control["loop_control_contract_violation"] = True
    if _should_inject_loop_control_stop_fallback(spec, iterations, repo_root=repo_root):
        loop_control["confidence_percent"] = 90
        loop_control["confidence_delta_this_iteration"] = 0
        loop_control["expected_next_iteration_gain"] = 0
        loop_control["recommended_action"] = "stop"
        loop_control["good_enough_for"] = str(loop_control.get("good_enough_for") or "draft_only")
        loop_control["loop_control_fallback_source"] = "repeated_same_blocker_stop"
    else:
        loop_control["confidence_percent"] = int(loop_control.get("confidence_percent") if loop_control.get("confidence_percent") is not None else 50)
        loop_control["confidence_delta_this_iteration"] = int(loop_control.get("confidence_delta_this_iteration") if loop_control.get("confidence_delta_this_iteration") is not None else 0)
        loop_control["expected_next_iteration_gain"] = int(loop_control.get("expected_next_iteration_gain") if loop_control.get("expected_next_iteration_gain") is not None else 1)
        action = str(loop_control.get("recommended_action") or "").strip().lower()
        loop_control["recommended_action"] = action if action in {"continue", "stop", "escalate"} else "continue"
        loop_control["good_enough_for"] = str(loop_control.get("good_enough_for") or "")
        loop_control["loop_control_fallback_source"] = "contract_default_continue"
    loop_control["loop_control_missing"] = False
    loop_control["loop_control_parse_error"] = False
    meta["loop_control"] = loop_control
    verifier["meta"] = meta


def _iteration_evidence_signature(iteration_row: Dict[str, Any], *, repo_root: Optional[Path] = None) -> str:
    builder = iteration_row.get("builder") if isinstance(iteration_row.get("builder"), dict) else {}
    builder_content = str(builder.get("content") or "")
    parts = sorted(set(_extract_validation_commands(builder_content)) | set(_extract_changed_files(builder_content, repo_root=repo_root)))
    return _sha256_text("\n".join(parts)) if parts else ""


def _should_economic_stop(
    spec: Dict[str, Any],
    iterations: List[Dict[str, Any]],
    *,
    repo_root: Optional[Path] = None,
    min_repeat_count: int = 2,
    min_expected_gain: int = 2,
) -> Dict[str, Any]:
    if not _supports_confidence_early_stop(spec, repo_root=repo_root):
        return {"stop": False, "status": "", "reason": ""}
    if len(iterations) < min_repeat_count:
        return {"stop": False, "status": "", "reason": ""}
    recent = iterations[-min_repeat_count:]
    verifier_rows: List[Dict[str, Any]] = []
    evidence_signatures: List[str] = []
    for row in recent:
        verifier = row.get("verifier") if isinstance(row.get("verifier"), dict) else {}
        content = str(verifier.get("content") or "").strip()
        if not content.startswith("VERDICT: FAIL"):
            return {"stop": False, "status": "", "reason": ""}
        meta = verifier.get("meta") if isinstance(verifier.get("meta"), dict) else {}
        loop_control = meta.get("loop_control") if isinstance(meta.get("loop_control"), dict) else {}
        if loop_control.get("expected_next_iteration_gain") is None:
            return {"stop": False, "status": "", "reason": ""}
        verifier_rows.append(loop_control)
        evidence_signatures.append(_iteration_evidence_signature(row, repo_root=repo_root))
    blocker_signatures = [str(row.get("blocker_signature") or "") for row in verifier_rows]
    if not blocker_signatures or len(set(blocker_signatures)) != 1 or not blocker_signatures[0]:
        return {"stop": False, "status": "", "reason": ""}
    if len(set(evidence_signatures)) != 1:
        return {"stop": False, "status": "", "reason": ""}
    gains = [int(row.get("expected_next_iteration_gain")) for row in verifier_rows if row.get("expected_next_iteration_gain") is not None]
    if len(gains) != min_repeat_count or any(gain >= min_expected_gain for gain in gains):
        return {"stop": False, "status": "", "reason": ""}
    deltas = [row.get("confidence_delta_this_iteration") for row in verifier_rows]
    if any(delta is None for delta in deltas):
        return {"stop": False, "status": "", "reason": ""}
    if any(int(delta) >= min_expected_gain for delta in deltas):
        return {"stop": False, "status": "", "reason": ""}
    recommended_actions = {str(row.get("recommended_action") or "") for row in verifier_rows}
    good_enough_values = {str(row.get("good_enough_for") or "") for row in verifier_rows}
    if "stop" in recommended_actions and good_enough_values & {"draft_only", "operator_review"}:
        return {"stop": True, "status": "good_enough_keep_current_status", "reason": "repeated_low_gain_fail_keep_current_status"}
    if "stop" in recommended_actions or "escalate" in recommended_actions:
        return {"stop": True, "status": "economic_stop_no_new_signal", "reason": "repeated_low_gain_fail_no_new_signal"}
    return {"stop": False, "status": "", "reason": ""}


def _job_report_markdown(*, spec: Dict[str, Any], receipt: Dict[str, Any]) -> str:
    job_kind = _normalize_job_kind(spec.get("job_kind"))
    job_shape_summary = _job_shape_semantics_projection(
        receipt if str(receipt.get("job_shape") or "").strip() else spec,
        raw_state=str(receipt.get("status") or ""),
        active_run=False,
        active_loop_stage="",
    )
    job_shape_fields = {
        "job_shape": str(job_shape_summary.get("job_shape") or ""),
        "job_shape_source": str(job_shape_summary.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_summary.get("job_shape_compatibility_mode")),
    }
    role_meta = _role_target(spec.get("role") or "generalist") if job_kind == "single_turn" else None
    runtime = receipt.get("runtime") if isinstance(receipt.get("runtime"), dict) else dict(spec.get("runtime") or {})
    prompt_text = str((receipt.get("resolved_prompt") or {}).get("text") or _resolved_prompt_text(spec)).strip()
    lines = [
        f"# Scheduled Job Report: {str(spec.get('job_id') or '')}",
        "",
        f"- status: {str(receipt.get('status') or '')}",
        f"- run_id: {str(receipt.get('run_id') or '')}",
        f"- trigger: {str(receipt.get('trigger') or '')}",
        f"- job_kind: {job_kind}",
        f"- job_shape: {str(job_shape_fields.get('job_shape') or '')}",
        f"- job_shape_source: {str(job_shape_fields.get('job_shape_source') or '')}",
        f"- job_shape_compatibility_mode: {str(bool(job_shape_fields.get('job_shape_compatibility_mode'))).lower()}",
        f"- job_shape_summary: {str(job_shape_summary.get('operator_summary') or job_shape_summary.get('error') or '')}",
        f"- role: {'' if role_meta is None else role_meta['role']}",
        f"- runtime_role: {'' if role_meta is None else role_meta['runtime_role']}",
        f"- binding: {str((spec.get('binding') or {}).get('type') or '')}:{str((spec.get('binding') or {}).get('id') or '')}",
        f"- started_utc: {str(receipt.get('started_utc') or '')}",
        f"- finished_utc: {str(receipt.get('finished_utc') or '')}",
        f"- model: {str(runtime.get('model') or '')}",
        f"- reasoning_effort: {str(runtime.get('reasoning_effort') or '')}",
        "",
        "## Prompt",
        "",
        prompt_text,
    ]
    resolved_prompt = receipt.get("resolved_prompt") if isinstance(receipt.get("resolved_prompt"), dict) else {}
    if resolved_prompt:
        lines.extend(
            [
                "",
                "## Prompt Provenance",
                "",
                f"- prompt_ref: {str(resolved_prompt.get('prompt_ref') or '')}",
                f"- prompt_sha256: {str(resolved_prompt.get('prompt_sha256') or '')}",
            ]
        )
    job_shape_context = _job_shape_context_obligation_projection(spec)
    bundle_id = str(job_shape_context.get("required_context_bundle") or spec.get("required_context_bundle") or "").strip()
    add_refs = [str(x).strip() for x in list(spec.get("additional_context_refs") or []) if str(x).strip()]
    ban_refs = [str(x).strip() for x in list(spec.get("forbidden_context_refs") or []) if str(x).strip()]
    refs = _effective_context_refs(spec)
    if bundle_id or add_refs or ban_refs or refs or job_shape_context:
        lines.extend(["", "## Context"])
        if bundle_id:
            lines.append(f"- required_context_bundle: {bundle_id}")
        if add_refs:
            lines.extend(["- additional_context_refs:"] + [f"  - {ref}" for ref in add_refs])
        if ban_refs:
            lines.extend(["- forbidden_context_refs:"] + [f"  - {ref}" for ref in ban_refs])
        if refs:
            lines.extend(["- resolved_context_refs:"] + [f"  - {ref}" for ref in refs])
        if job_shape_context:
            lines.append(f"- context_reentry_precedence: {str(job_shape_context.get('reentry_precedence') or '')}")
            lines.append(f"- context_transcript_summary_policy: {str(job_shape_context.get('transcript_summary_policy') or '')}")
    lines.extend(
        [
            "",
            "## Job Shape Semantics",
            "",
            f"- recognized: {str(bool(job_shape_summary.get('recognized'))).lower()}",
            f"- progress_semantics: {str(job_shape_summary.get('progress_semantics') or '')}",
            f"- recovery_semantics: {str(job_shape_summary.get('recovery_semantics') or '')}",
            f"- child_launch_semantics: {str(job_shape_summary.get('child_launch_semantics') or '')}",
        ]
    )
    job_retry = receipt.get("job_retry") if isinstance(receipt.get("job_retry"), dict) else {}
    if job_retry:
        lines.extend(
            [
                "",
                "## Whole-Job Retry",
                "",
                f"- attempt_count: {str(job_retry.get('attempt_count') or 1)}",
                f"- retries_used: {str(job_retry.get('retries_used') or 0)}",
                f"- succeeded_after_retry: {str(bool(job_retry.get('succeeded_after_retry'))).lower()}",
                f"- final_failure_class: {str(job_retry.get('final_failure_class') or '')}",
                f"- final_failure_reason: {str(job_retry.get('final_failure_reason') or '')}",
            ]
        )
        history = list(job_retry.get("history") or [])
        if history:
            lines.extend(["", "### Retry History", ""])
            for row in history:
                if not isinstance(row, dict):
                    continue
                lines.append(
                    f"- attempt={str(row.get('attempt') or '')} status={str(row.get('status') or '')} "
                    f"retryable={str(bool(row.get('retryable'))).lower()} "
                    f"failure_class={str(row.get('failure_class') or '')} "
                    f"reason={str(row.get('classification_reason') or '')} "
                    f"error={str(row.get('error') or '')}"
                )
    model_telemetry = receipt.get("model_telemetry") if isinstance(receipt.get("model_telemetry"), dict) else {}
    runtime_backend = receipt.get("runtime_backend") if isinstance(receipt.get("runtime_backend"), dict) else {}
    compiled_execution = receipt.get("compiled_execution") if isinstance(receipt.get("compiled_execution"), dict) else {}
    if model_telemetry:
        lines.extend(
            [
                "",
                "## Model Telemetry",
                "",
                f"- executor_agent_tech: {str(model_telemetry.get('executor_agent_tech') or '')}",
                f"- advisory_agent_tech: {str(model_telemetry.get('advisory_agent_tech') or '')}",
                f"- advisory_model: {str(model_telemetry.get('advisory_model') or '')}",
                f"- requested_agent_tech: {str(model_telemetry.get('requested_agent_tech') or '')}",
                f"- requested_model: {str(model_telemetry.get('requested_model') or '')}",
                f"- reasoning_effort: {str(model_telemetry.get('reasoning_effort') or '')}",
            ]
        )
        stage_rows = list(model_telemetry.get("stages") or [])
        if stage_rows:
            lines.extend(["", "### Stage Attempts", ""])
            for row in stage_rows:
                if not isinstance(row, dict):
                    continue
                stage_name = str(row.get("stage") or "")
                iter_txt = str(row.get("iteration") or "")
                prefix = f"{stage_name}" + (f" (iteration {iter_txt})" if iter_txt and iter_txt != "0" else "")
                lines.append(
                    f"- {prefix}: selected_agent_tech={str(row.get('selected_agent_tech') or '')} "
                    f"selected_model={str(row.get('selected_model') or '')} "
                    f"advisory_agent_tech={str(row.get('advisory_agent_tech') or '')} "
                    f"advisory_model={str(row.get('advisory_model') or '')} "
                    f"requested_model_meta={str(row.get('requested_model_meta') or '')} "
                    f"backend={str(row.get('backend') or '')} provider_model={str(row.get('provider_model') or '')}"
                )
    if compiled_execution:
        lines.extend(
            [
                "",
                "## Compiled Execution",
                "",
                f"- enabled: {str(bool(compiled_execution.get('enabled'))).lower()}",
                f"- rollout_mode: {str(compiled_execution.get('rollout_mode') or '')}",
                f"- execution_mode: {str(compiled_execution.get('execution_mode') or '')}",
            ]
        )
        persistence = compiled_execution.get("artifact_persistence") if isinstance(compiled_execution.get("artifact_persistence"), dict) else {}
        if persistence:
            lines.append(f"- artifact_dir: {str(persistence.get('artifact_dir') or '')}")
            paths = persistence.get("paths") if isinstance(persistence.get("paths"), dict) else {}
            for key, value in paths.items():
                lines.append(f"- {key}: {value}")
    if runtime_backend:
        codex_cli = runtime_backend.get("codex_cli_version") if isinstance(runtime_backend.get("codex_cli_version"), dict) else {}
        claude_cli = runtime_backend.get("claude_cli_version") if isinstance(runtime_backend.get("claude_cli_version"), dict) else {}
        gemini_cli = runtime_backend.get("gemini_cli_version") if isinstance(runtime_backend.get("gemini_cli_version"), dict) else {}
        ollama_cli = runtime_backend.get("ollama_cli_version") if isinstance(runtime_backend.get("ollama_cli_version"), dict) else {}
        lines.extend(
            [
                "",
                "## Runtime Backend",
                "",
                f"- backend: {str(runtime_backend.get('backend') or '')}",
                f"- codex_binary: {str(runtime_backend.get('codex_binary') or '')}",
                f"- codex_cli_version: {str(codex_cli.get('version') or '')}",
                f"- codex_cli_probe_ok: {str(bool(codex_cli.get('ok'))).lower()}",
                f"- claude_binary: {str(runtime_backend.get('claude_binary') or '')}",
                f"- claude_cli_version: {str(claude_cli.get('version') or '')}",
                f"- claude_cli_probe_ok: {str(bool(claude_cli.get('ok'))).lower()}",
                f"- gemini_binary: {str(runtime_backend.get('gemini_binary') or '')}",
                f"- gemini_cli_version: {str(gemini_cli.get('version') or '')}",
                f"- gemini_cli_probe_ok: {str(bool(gemini_cli.get('ok'))).lower()}",
                f"- ollama_binary: {str(runtime_backend.get('ollama_binary') or '')}",
                f"- ollama_cli_version: {str(ollama_cli.get('version') or '')}",
                f"- ollama_cli_probe_ok: {str(bool(ollama_cli.get('ok'))).lower()}",
                f"- xai_transport_binary: {str(runtime_backend.get('xai_binary') or '')}",
                f"- xai_api_key_present: {str(bool(runtime_backend.get('xai_api_key_present'))).lower()}",
            ]
        )
    executor_of_record = receipt.get("executor_of_record") if isinstance(receipt.get("executor_of_record"), dict) else {}
    if executor_of_record:
        lines.extend(
            [
                "",
                "## Executor Of Record",
                "",
                f"- write_capable: {str(bool(executor_of_record.get('write_capable'))).lower()}",
                f"- required_executor: {str(executor_of_record.get('required_executor') or '')}",
                f"- actual_executor: {str(executor_of_record.get('actual_executor') or '')}",
                f"- execution_mode: {str(executor_of_record.get('execution_mode') or '')}",
                f"- pipeline_executor_mode: {str(executor_of_record.get('pipeline_executor_mode') or '')}",
                f"- contract_ok: {str(bool(executor_of_record.get('contract_ok'))).lower()}",
                f"- violation_reason: {str(executor_of_record.get('violation_reason') or '')}",
            ]
        )
    if job_kind == "single_turn":
        lines.extend(["", "## Assistant Output", "", str((receipt.get("assistant") or {}).get("content") or "")])
    elif job_kind == "swarm_launch":
        swarm_contract = receipt.get("swarm") if isinstance(receipt.get("swarm"), dict) else dict(spec.get("swarm") or {})
        swarm_state = swarm_contract.get("state") if isinstance(swarm_contract.get("state"), dict) else {}
        swarm_eval = swarm_contract.get("evaluation") if isinstance(swarm_contract.get("evaluation"), dict) else {}
        fanout_projection = swarm_contract.get("fanout_projection") if isinstance(swarm_contract.get("fanout_projection"), dict) else {}
        integrated_result = (
            swarm_contract.get("integrated_result")
            if isinstance(swarm_contract.get("integrated_result"), dict)
            else {}
        )
        lines.extend(
            [
                "",
                "## Swarm Launch",
                "",
                f"- swarm_id: {str(swarm_contract.get('swarm_id') or '')}",
                f"- mission_type: {str(swarm_contract.get('mission_type') or '')}",
                f"- objective: {str(swarm_contract.get('objective') or '')}",
                f"- scope: {str(swarm_contract.get('scope') or '')}",
                f"- final_swarm_status: {str(swarm_contract.get('final_status') or swarm_state.get('status') or '')}",
                f"- final_swarm_phase: {str(swarm_contract.get('final_phase') or swarm_state.get('phase') or '')}",
                f"- fanout_stage: {str(fanout_projection.get('fanout_stage') or '')}",
                f"- fanout_master_owner: {str(fanout_projection.get('master_owner') or '')}",
                f"- integrated_classification: {str(integrated_result.get('classification') or '')}",
                f"- integration_disposition: {str(fanout_projection.get('integration_disposition') or '')}",
                f"- final_verdict: {str(fanout_projection.get('final_verdict') or '')}",
                f"- evidence_status: {str(swarm_eval.get('status') or '')}",
                f"- changed_files: {', '.join(list(swarm_eval.get('changed_files') or [])) or '(none)'}",
                f"- summary_path: {str(swarm_eval.get('summary_path') or '')}",
            ]
        )
        child_roster = list(fanout_projection.get("child_roster") or [])
        if child_roster:
            lines.append(f"- child_roster: {', '.join(child_roster)}")
        restart_reconciliation = (
            fanout_projection.get("restart_reconciliation")
            if isinstance(fanout_projection.get("restart_reconciliation"), dict)
            else {}
        )
        retry_boundaries = (
            fanout_projection.get("retry_boundaries")
            if isinstance(fanout_projection.get("retry_boundaries"), dict)
            else {}
        )
        if restart_reconciliation:
            lines.extend(
                [
                    f"- restart_reconciliation_status: {str(restart_reconciliation.get('status') or '')}",
                    f"- restart_reconciliation_summary: {str(restart_reconciliation.get('summary') or '')}",
                ]
            )
        child_relaunch = retry_boundaries.get("child_relaunch") if isinstance(retry_boundaries.get("child_relaunch"), dict) else {}
        master_rerun = retry_boundaries.get("master_rerun") if isinstance(retry_boundaries.get("master_rerun"), dict) else {}
        if child_relaunch:
            lines.append(f"- child_relaunch_state: {str(child_relaunch.get('state') or '')}")
        if master_rerun:
            lines.append(f"- master_rerun_state: {str(master_rerun.get('state') or '')}")
        rerun_child_ids = list(integrated_result.get("rerun_child_ids") or [])
        if rerun_child_ids:
            lines.append(f"- rerun_child_ids: {', '.join(rerun_child_ids)}")
        conflicts = list(integrated_result.get("conflicts") or [])
        if conflicts:
            lines.extend(["", "### Integration Conflicts", ""])
            for row in conflicts:
                if not isinstance(row, dict):
                    continue
                lines.append(f"- {str(row.get('summary') or '')}")
        worker_summary_paths = list(swarm_eval.get("worker_summary_paths") or [])
        if worker_summary_paths:
            lines.extend(["", "### Worker Summary Paths", ""])
            for path in worker_summary_paths:
                lines.append(f"- {str(path)}")
        workers = list(swarm_state.get("workers") or [])
        if workers:
            lines.extend(["", "### Worker Results", ""])
            for worker in workers:
                if not isinstance(worker, dict):
                    continue
                lines.append(
                    f"- {str(worker.get('worker_id') or '')} ({str(worker.get('kind') or '')}): "
                    f"{str(worker.get('status') or '')}"
                )
    elif job_kind == "project_runner":
        project_run = receipt.get("project_run") if isinstance(receipt.get("project_run"), dict) else dict(spec.get("project_run") or {})
        scheduler_policy = receipt.get("scheduler_policy") if isinstance(receipt.get("scheduler_policy"), dict) else {}
        contract_projection = receipt.get("contract_projection") if isinstance(receipt.get("contract_projection"), dict) else {}
        lines.extend(
            [
                "",
                "## Project Runner",
                "",
                f"- project_id: {str(project_run.get('project_id') or '')}",
                f"- runner_id: {str(project_run.get('runner_id') or '')}",
                f"- args: {json.dumps(project_run.get('args') or {}, sort_keys=True)}",
                f"- command: {str(receipt.get('command_display') or '')}",
                f"- cwd: {str(receipt.get('cwd') or '')}",
                f"- entrypoint: {str(receipt.get('entrypoint') or '')}",
                f"- manifest_path: {str(receipt.get('manifest_path') or '')}",
                f"- exit_code: {str(receipt.get('exit_code') or '')}",
                f"- runner_log_path: {str(receipt.get('runner_log_path') or '')}",
            ]
        )
        if contract_projection:
            status_surfaces = contract_projection.get("status_surfaces") if isinstance(contract_projection.get("status_surfaces"), dict) else {}
            control_declarations = contract_projection.get("control_declarations") if isinstance(contract_projection.get("control_declarations"), dict) else {}
            control_policy_summary = (
                contract_projection.get("control_action_policy_summary")
                if isinstance(contract_projection.get("control_action_policy_summary"), dict)
                else {}
            )
            analysis_entrypoints = contract_projection.get("analysis_entrypoints") if isinstance(contract_projection.get("analysis_entrypoints"), dict) else {}
            limitations = [str(row).strip() for row in list(contract_projection.get("limitations") or []) if str(row).strip()]
            lines.extend(
                [
                    "",
                    "### Declared Status Surfaces",
                    "",
                    f"- progress_mode: {str(status_surfaces.get('progress_mode') or 'none')}",
                    f"- progress_fields: {', '.join(list(status_surfaces.get('progress_fields') or [])) or '(none declared)'}",
                    f"- status_files: {', '.join(list(status_surfaces.get('status_files') or [])) or '(none declared)'}",
                    f"- health_signals: {', '.join(list(status_surfaces.get('health_signals') or [])) or '(none declared)'}",
                    "",
                    "### Declared Controls",
                    "",
                    f"- supported_actions: {', '.join(list(control_declarations.get('supported_actions') or [])) or '(none declared)'}",
                    f"- unsupported_actions: {', '.join(list(control_declarations.get('unsupported_actions') or [])) or '(none declared)'}",
                    "",
                    "### Analysis Entry Points",
                    "",
                    f"- recipes: {', '.join(list(analysis_entrypoints.get('recipes') or [])) or '(none declared)'}",
                    f"- default_recipe: {str(analysis_entrypoints.get('default_recipe') or '(none declared)')}",
                    f"- primary_artifacts: {', '.join(list(analysis_entrypoints.get('primary_artifacts') or [])) or '(none declared)'}",
                    "",
                    "### Declared Limitations",
                    "",
                    f"- limitations: {' | '.join(limitations) if limitations else '(none declared)'}",
                ]
            )
            if control_policy_summary:
                lines.extend(
                    [
                        "",
                        "### Effective Control Policy",
                        "",
                        f"- headline: {str(control_policy_summary.get('headline') or '(none)')}",
                        f"- available_actions: {', '.join(list(control_policy_summary.get('available_actions') or [])) or '(none)'}",
                        f"- blocked_actions: {', '.join(list(control_policy_summary.get('blocked_actions') or [])) or '(none)'}",
                        f"- unsupported_actions: {', '.join(list(control_policy_summary.get('unsupported_actions') or [])) or '(none)'}",
                        f"- staged_actions: {', '.join(list(control_policy_summary.get('staged_actions') or [])) or '(none)'}",
                    ]
                )
        if scheduler_policy:
            lines.extend(
                [
                    "",
                    "### Contract Policy",
                    "",
                    f"- resource_class: {str(scheduler_policy.get('resource_class') or '')}",
                    f"- project_semaphore: {str(scheduler_policy.get('project_semaphore') or '')}",
                    f"- max_parallel_runs: {str(scheduler_policy.get('max_parallel_runs') or '')}",
                    f"- required_context_bundle: {str(receipt.get('required_context_bundle') or '')}",
                    f"- additional_context_refs_allowed: {str(bool(scheduler_policy.get('additional_context_refs_allowed'))).lower()}",
                    f"- forbidden_context_refs: {', '.join(list(receipt.get('forbidden_context_refs') or [])) or '(none)'}",
                ]
            )
        stdout_tail = str(receipt.get("stdout_tail") or "").strip()
        if stdout_tail:
            lines.extend(["", "### Output Tail", "", stdout_tail])
    else:
        pipeline_execution = receipt.get("pipeline_execution") if isinstance(receipt.get("pipeline_execution"), dict) else {}
        if str(pipeline_execution.get("mode") or "") == PIPELINE_FULL_AUTO_MODE:
            summary = pipeline_execution.get("summary") if isinstance(pipeline_execution.get("summary"), dict) else {}
            finalization = summary.get("finalization_state") if isinstance(summary.get("finalization_state"), dict) else {}
            repair = summary.get("repair_decision_summary") if isinstance(summary.get("repair_decision_summary"), dict) else {}
            lines.extend(
                [
                    "",
                    "## Pipeline Full-Auto Execution",
                    "",
                    f"- pipeline_job_id: {str(pipeline_execution.get('job_id') or '')}",
                    f"- pipeline_run_id: {str(pipeline_execution.get('pipeline_run_id') or summary.get('run_id') or '')}",
                    f"- pipeline_best_run_id: {str(pipeline_execution.get('pipeline_best_run_id') or ((summary.get('controller') if isinstance(summary.get('controller'), dict) else {}) or {}).get('best_run_id') or '')}",
                    f"- selected_model: {str(pipeline_execution.get('selected_model') or '')}",
                    f"- stage8_model: {str(pipeline_execution.get('stage8_model') or '')}",
                    f"- stage8_agent_tech: {str(pipeline_execution.get('stage8_agent_tech') or '')}",
                    f"- final_status: {str(finalization.get('final_status') or '')}",
                    f"- retry_authorization_state: {str(repair.get('retry_authorization_state') or '')}",
                    f"- blocking_reason_codes: {', '.join(list(repair.get('blocking_reason_codes') or [])) or 'none'}",
                ]
            )
            summary_paths = pipeline_execution.get("summary_paths") if isinstance(pipeline_execution.get("summary_paths"), dict) else {}
            if summary_paths:
                lines.extend(
                    [
                        "",
                        "### Pipeline Summary Paths",
                        "",
                        f"- json: {str(summary_paths.get('json_path') or '')}",
                        f"- markdown: {str(summary_paths.get('markdown_path') or '')}",
                    ]
                )
            repo_promotion = pipeline_execution.get("repo_promotion") if isinstance(pipeline_execution.get("repo_promotion"), dict) else {}
            if repo_promotion:
                lines.extend(
                    [
                        "",
                        "### Repo Promotion",
                        "",
                        f"- ok: {str(bool(repo_promotion.get('ok'))).lower()}",
                        f"- reason: {str(repo_promotion.get('reason') or '')}",
                        f"- error: {str(repo_promotion.get('error') or '')}",
                    ]
                )
        else:
            loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
            lines.extend(
                [
                    "",
                    "## Loop Summary",
                    "",
                    f"- loop_policy: {str(loop.get('loop_policy') or '')}",
                    f"- builder_role: {str(loop.get('builder_role') or '')}",
                    f"- verifier_role: {str(loop.get('verifier_role') or '')}",
                    f"- max_iterations: {str(loop.get('max_iterations') or '')}",
                    f"- completed_iterations: {str(loop.get('completed_iterations') or '')}",
                    f"- pass_marker: {str(loop.get('pass_marker') or '')}",
                    "",
                    "## Iterations",
                    "",
                ]
            )
            for row in list(loop.get("iterations") or []):
                lines.extend(
                    [
                        f"### Iteration {int(row.get('iteration') or 0)}",
                        "",
                        "#### Builder",
                        "",
                        str((row.get("builder") or {}).get("content") or ""),
                        "",
                        "#### Verifier",
                        "",
                        str((row.get("verifier") or {}).get("content") or ""),
                        "",
                    ]
                )
    err = str(receipt.get("error") or "").strip()
    if err:
        lines.extend(["", "## Error", "", err])
    execution_proof = receipt.get("execution_proof") if isinstance(receipt.get("execution_proof"), dict) else {}
    if execution_proof:
        lines.extend(
            [
                "",
                "## Execution Proof",
                "",
                f"- required_validation_evidence: {str(bool(execution_proof.get('required_validation_evidence'))).lower()}",
                f"- validation_command_count: {str(execution_proof.get('validation_command_count') or 0)}",
                f"- proof_gap: {str(bool(execution_proof.get('proof_gap'))).lower()}",
                f"- proof_gap_reason: {str(execution_proof.get('proof_gap_reason') or '')}",
            ]
        )
        validation_commands = list(execution_proof.get("validation_commands") or [])
        if validation_commands:
            lines.extend(["", "### Validation Commands", ""])
            for cmd in validation_commands:
                lines.append(f"- {str(cmd)}")
    validation_claim_hygiene = receipt.get("validation_claim_hygiene") if isinstance(receipt.get("validation_claim_hygiene"), dict) else {}
    if validation_claim_hygiene:
        lines.extend(
            [
                "",
                "## Validation Claim Hygiene",
                "",
                f"- claim_scope: {str(validation_claim_hygiene.get('claim_scope') or '')}",
                f"- evidence_scope: {str(validation_claim_hygiene.get('evidence_scope') or '')}",
                f"- manual_only: {str(bool(validation_claim_hygiene.get('manual_only'))).lower()}",
                f"- broad_success_claim_allowed: {str(bool(validation_claim_hygiene.get('broad_success_claim_allowed'))).lower()}",
                f"- summary: {str(validation_claim_hygiene.get('summary') or '')}",
            ]
        )
        remaining_gaps = list(validation_claim_hygiene.get("remaining_proof_gaps") or [])
        if remaining_gaps:
            lines.extend(["", "### Remaining Proof Gaps", ""])
            for gap in remaining_gaps:
                lines.append(f"- {str(gap)}")
    repo_change_evidence = receipt.get("repo_change_evidence") if isinstance(receipt.get("repo_change_evidence"), dict) else {}
    if repo_change_evidence:
        lines.extend(            [
                "",
                "## Repo Change Evidence",
                "",
                f"- git_dirty: {str(bool(repo_change_evidence.get('git_dirty'))).lower()}",
                f"- commit_journal_pending_count: {str(repo_change_evidence.get('commit_journal_pending_count') or 0)}",
                f"- commit_journal_invalid_count: {str(repo_change_evidence.get('commit_journal_invalid_count') or 0)}",
                f"- commit_journal_audit_ok: {str(bool(repo_change_evidence.get('commit_journal_audit_ok'))).lower()}",
            ]
        )
        dirty_preview = str(repo_change_evidence.get("dirty_preview") or "").strip()
        if dirty_preview:
            lines.extend(["", "### Dirty Worktree Preview", "", dirty_preview])
        uncovered = list(repo_change_evidence.get("commit_journal_uncovered_paths") or [])
        if uncovered:
            lines.extend(["", "### Commit Journal Uncovered Paths", ""])
            for path in uncovered:
                lines.append(f"- {str(path)}")
    lifecycle = receipt.get("lifecycle") if isinstance(receipt.get("lifecycle"), dict) else {}
    if lifecycle:
        cancel = lifecycle.get("cancel") if isinstance(lifecycle.get("cancel"), dict) else {}
        timeout = lifecycle.get("timeout") if isinstance(lifecycle.get("timeout"), dict) else {}
        heartbeat = lifecycle.get("heartbeat") if isinstance(lifecycle.get("heartbeat"), dict) else {}
        lines.extend(
            [
                "",
                "## Lifecycle",
                "",
                f"- status: {str(lifecycle.get('status') or '')}",
                f"- reconciled_utc: {str(lifecycle.get('reconciled_utc') or '')}",
                f"- cancel_requested: {str(bool(cancel.get('requested'))).lower()}",
                f"- cancel_requested_utc: {str(cancel.get('requested_utc') or '')}",
                f"- cancel_result: {str(cancel.get('result') or '')}",
                f"- timeout_expired: {str(bool(timeout.get('expired'))).lower()}",
                f"- timeout_failure_kind: {str(timeout.get('failure_kind') or '')}",
                f"- heartbeat_claim_present: {str(bool(heartbeat.get('claim_present'))).lower()}",
                f"- heartbeat_claim_reason: {str(heartbeat.get('claim_reason') or '')}",
                f"- heartbeat_age_s: {str(heartbeat.get('age_s') or '')}",
            ]
        )
    reconciliation = receipt.get("reconciliation") if isinstance(receipt.get("reconciliation"), dict) else {}
    if reconciliation:
        active_claim = reconciliation.get("active_claim") if isinstance(reconciliation.get("active_claim"), dict) else {}
        lines.extend(
            [
                "",
                "## Scheduler Reconciliation",
                "",
                f"- status_projection: {str(reconciliation.get('status_projection') or '')}",
                f"- due_eligible: {str(bool(reconciliation.get('due_eligible'))).lower()}",
                f"- due_reason: {str(reconciliation.get('due_reason') or '')}",
                f"- operator_request: {str(reconciliation.get('operator_request') or '')}",
                f"- queue_backpressure_reason: {str(reconciliation.get('queue_backpressure_reason') or '')}",
                f"- blocked: {str(bool(reconciliation.get('blocked'))).lower()}",
                f"- blocking_reason: {str(reconciliation.get('blocking_reason') or '')}",
                f"- active_claim_present: {str(bool(active_claim.get('present'))).lower()}",
                f"- active_claim_reason: {str(active_claim.get('reason') or '')}",
            ]
        )
    repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
    if repo_residue:
        lines.extend(
            [
                "",
                "## Repo Residue",
                "",
                f"- source: {str(repo_residue.get('source') or '')}",
                f"- git_available: {str(bool(repo_residue.get('git_available'))).lower()}",
                f"- baseline_dirty_path_count: {str(repo_residue.get('baseline_dirty_path_count') or 0)}",
                f"- current_dirty_path_count: {str(repo_residue.get('current_dirty_path_count') or 0)}",
                f"- introduced_dirty_path_count: {len(list(repo_residue.get('introduced_dirty_paths') or []))}",
                f"- undeclared_dirty_path_count: {len(list(repo_residue.get('dirty_paths_not_declared') or []))}",
                f"- declared_changed_file_count: {len(list(repo_residue.get('declared_changed_files') or []))}",
                f"- validation_command_count: {len(list(repo_residue.get('validation_commands') or []))}",
                f"- artifact_ref_count: {len(list(repo_residue.get('artifact_refs') or []))}",
            ]
        )
        introduced = list(repo_residue.get("introduced_dirty_paths") or [])
        if introduced:
            lines.extend(["", "### Introduced Dirty Paths", ""])
            for item in introduced:
                lines.append(f"- {str(item)}")
        undeclared = list(repo_residue.get("dirty_paths_not_declared") or [])
        if undeclared:
            lines.extend(["", "### Dirty Paths Not Declared", ""])
            for item in undeclared:
                lines.append(f"- {str(item)}")
        artifact_refs = list(repo_residue.get("artifact_refs") or [])
        if artifact_refs:
            lines.extend(["", "### Artifact Refs", ""])
            for item in artifact_refs:
                lines.append(f"- {str(item)}")

    forensic_debug_kit = receipt.get("forensic_debug_kit") if isinstance(receipt.get("forensic_debug_kit"), dict) else {}
    if forensic_debug_kit:
        lines.extend(
            [
                "",
                "## Forensic Debug Kit",
                "",
                f"- classification: {str(forensic_debug_kit.get('classification') or '')}",
                f"- stage: {str(forensic_debug_kit.get('stage') or '')}",
                f"- status: {str(forensic_debug_kit.get('status') or '')}",
                f"- reason_code: {str(forensic_debug_kit.get('reason_code') or '')}",
                f"- last_error: {str(forensic_debug_kit.get('last_error') or '')}",
                f"- proof_gap: {str(bool(forensic_debug_kit.get('proof_gap'))).lower()}",
                f"- proof_gap_reason: {str(forensic_debug_kit.get('proof_gap_reason') or '')}",
                f"- next_decision_hint: {str(forensic_debug_kit.get('next_decision_hint') or '')}",
                f"- receipt_path: {str(forensic_debug_kit.get('receipt_path') or '')}",
                f"- report_path: {str(forensic_debug_kit.get('report_path') or '')}",
            ]
        )
        evidence_refs = list(forensic_debug_kit.get("evidence_refs") or [])
        if evidence_refs:
            lines.extend(["", "### Debug Evidence Refs", ""])
            for item in evidence_refs:
                lines.append(f"- {str(item)}")
    ownership = receipt.get("ownership") if isinstance(receipt.get("ownership"), dict) else {}
    if ownership:
        lines.extend(
            [
                "",
                "## Ownership",
                "",
                f"- scheduler_scope: {str(ownership.get('scheduler_scope') or '')}",
                f"- runtime_scope: {str(ownership.get('runtime_scope') or '')}",
                f"- handoff_state: {str(ownership.get('handoff_state') or '')}",
            ]
        )
    git_hook = receipt.get("git_commit_tag") if isinstance(receipt.get("git_commit_tag"), dict) else {}
    if git_hook:
        lines.extend(
            [
                "",
                "## Git Commit Tag",
                "",
                f"- ok: {str(git_hook.get('ok') or False).lower()}",
                f"- command: {str(git_hook.get('command') or '')}",
                f"- exit_code: {str(git_hook.get('exit_code') or '')}",
            ]
        )
        attempts = list(git_hook.get("attempts") or [])
        if attempts:
            lines.append(f"- attempts: {len(attempts)}")
        stdout = str(git_hook.get("stdout") or "").strip()
        stderr = str(git_hook.get("stderr") or "").strip()
        if stdout:
            lines.extend(["", "### stdout", "", stdout])
        if stderr:
            lines.extend(["", "### stderr", "", stderr])
    return "\n".join(lines).strip() + "\n"


def _decision_report_markdown(*, spec: Dict[str, Any], receipt: Dict[str, Any]) -> str:
    reason = str(receipt.get("reason") or "")
    due = receipt.get("due") if isinstance(receipt.get("due"), dict) else {}
    job_shape_summary = _job_shape_semantics_projection(
        receipt if str(receipt.get("job_shape") or "").strip() else spec,
        raw_state=str(receipt.get("status") or ""),
        active_run=False,
        active_loop_stage="",
    )
    job_shape_fields = {
        "job_shape": str(job_shape_summary.get("job_shape") or ""),
        "job_shape_source": str(job_shape_summary.get("job_shape_source") or ""),
        "job_shape_compatibility_mode": bool(job_shape_summary.get("job_shape_compatibility_mode")),
    }
    lines = [
        f"# Scheduled Job Decision: {str(spec.get('job_id') or '')}",
        "",
        f"- status: {str(receipt.get('status') or '')}",
        f"- run_id: {str(receipt.get('run_id') or '')}",
        f"- trigger: {str(receipt.get('trigger') or '')}",
        f"- job_shape: {str(job_shape_fields.get('job_shape') or '')}",
        f"- job_shape_source: {str(job_shape_fields.get('job_shape_source') or '')}",
        f"- job_shape_compatibility_mode: {str(bool(job_shape_fields.get('job_shape_compatibility_mode'))).lower()}",
        f"- job_shape_summary: {str(job_shape_summary.get('operator_summary') or job_shape_summary.get('error') or '')}",
        f"- reason: {reason}",
        f"- due_reason: {str(due.get('reason') or '')}",
        f"- due_utc: {str(due.get('due_utc') or '')}",
        f"- slot_key: {str(due.get('slot_key') or '')}",
        "",
        "## Ownership",
        "",
        "- scheduler_scope: launch intent + gating + launch/non-launch evidence",
        "- runtime_scope: no runtime ownership because launch did not hand off",
        "- handoff_state: not_launched",
        "",
        "## Prompt",
        "",
        str(spec.get("prompt") or ""),
    ]
    guard = receipt.get("execution_guard") if isinstance(receipt.get("execution_guard"), dict) else {}
    if guard:
        lines.extend(
            [
                "",
                "## Execution Guard",
                "",
                f"- mode: {str(guard.get('mode') or '')}",
                f"- blocked: {str(bool(guard.get('blocked'))).lower()}",
                f"- reason: {str(guard.get('reason') or '')}",
                f"- dirty_worktree: {str(bool(guard.get('dirty_worktree'))).lower()}",
                f"- commit_journal_pending_count: {str(guard.get('commit_journal_pending_count') or 0)}",
                f"- commit_journal_invalid_count: {str(guard.get('commit_journal_invalid_count') or 0)}",
            ]
        )
    lifecycle = receipt.get("lifecycle") if isinstance(receipt.get("lifecycle"), dict) else {}
    if lifecycle:
        cancel = lifecycle.get("cancel") if isinstance(lifecycle.get("cancel"), dict) else {}
        lines.extend(
            [
                "",
                "## Lifecycle",
                "",
                f"- status: {str(lifecycle.get('status') or '')}",
                f"- cancel_requested: {str(bool(cancel.get('requested'))).lower()}",
                f"- cancel_result: {str(cancel.get('result') or '')}",
            ]
        )
    reconciliation = receipt.get("reconciliation") if isinstance(receipt.get("reconciliation"), dict) else {}
    if reconciliation:
        lines.extend(
            [
                "",
                "## Scheduler Reconciliation",
                "",
                f"- status_projection: {str(reconciliation.get('status_projection') or '')}",
                f"- due_eligible: {str(bool(reconciliation.get('due_eligible'))).lower()}",
                f"- due_reason: {str(reconciliation.get('due_reason') or '')}",
                f"- operator_request: {str(reconciliation.get('operator_request') or '')}",
                f"- blocked: {str(bool(reconciliation.get('blocked'))).lower()}",
                f"- blocking_reason: {str(reconciliation.get('blocking_reason') or '')}",
            ]
        )
    repo_residue = receipt.get("repo_residue") if isinstance(receipt.get("repo_residue"), dict) else {}
    if repo_residue:
        lines.extend(
            [
                "",
                "## Repo Residue",
                "",
                f"- source: {str(repo_residue.get('source') or '')}",
                f"- current_dirty_path_count: {str(repo_residue.get('current_dirty_path_count') or 0)}",
                f"- undeclared_dirty_path_count: {len(list(repo_residue.get('dirty_paths_not_declared') or []))}",
            ]
        )

    forensic_debug_kit = receipt.get("forensic_debug_kit") if isinstance(receipt.get("forensic_debug_kit"), dict) else {}
    if forensic_debug_kit:
        lines.extend(
            [
                "",
                "## Forensic Debug Kit",
                "",
                f"- classification: {str(forensic_debug_kit.get('classification') or '')}",
                f"- stage: {str(forensic_debug_kit.get('stage') or '')}",
                f"- status: {str(forensic_debug_kit.get('status') or '')}",
                f"- reason_code: {str(forensic_debug_kit.get('reason_code') or '')}",
                f"- next_decision_hint: {str(forensic_debug_kit.get('next_decision_hint') or '')}",
                f"- receipt_path: {str(forensic_debug_kit.get('receipt_path') or '')}",
                f"- report_path: {str(forensic_debug_kit.get('report_path') or '')}",
            ]
        )
    return "\n".join(lines).strip() + "\n"


def _git_status_porcelain(*, repo_root: Optional[Path] = None) -> str:
    cp = subprocess.run(
        ["git", "status", "--short", "--untracked-files=all"],
        cwd=str(Path(repo_root or _repo_root()).resolve()),
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if cp.returncode != 0:
        return f"[git_status_error] {str(cp.stderr or cp.stdout or 'git status failed').strip()}"
    return str(cp.stdout or "").strip()


def _is_repo_change_strict(policy: Dict[str, Any]) -> bool:
    return str(policy.get("repo_change_mode") or "none").strip().lower() == "strict"
