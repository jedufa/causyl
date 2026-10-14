"""Fresh-turn bootstrap and bounded context reload for Lab Assistant v1 (DD8C).

This module owns only per-turn read-side context assembly over the existing
ADD9/2848/BD05 durable substrates. It does not create new durable truth.
"""

from __future__ import annotations

import datetime as _dt
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from infra.controlplane import continuation_packet_bootstrap as _continuation_packet_bootstrap
from infra.controlplane import continuation_context_attachment as _continuation_attachment
from infra.controlplane import plane_mission_contract as _plane_mission_contract
from infra.webui import server_lab_assistant_delegation_policy as _lab_delegate_policy
from infra.webui import server_lab_assistant_host as _lab_host
from infra.webui import server_lab_assistant_correlation as _lab_correlation
from infra.webui import server_lab_assistant_recovery as _lab_recovery
from infra.webui import server_lab_assistant_state as _lab_state
from infra.webui import server_lab_assistant_task_register as _lab_task_register


BOOTSTRAP_SCHEMA_VERSION = "lab_assistant_fresh_turn_bootstrap_v1"
_DEFAULT_MAX_CHARS = 4000
_MIN_MAX_CHARS = 1200
_MAX_MAX_CHARS = 24000
_DEFAULT_RECENT_CHAT_ROWS = 8
_MAX_RECENT_CHAT_ROWS = 20


def _utc_now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _bounded_int(raw: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(minimum, min(maximum, value))


def _int_or_default(raw: Any, default: int) -> int:
    try:
        return int(raw)
    except Exception:
        return int(default)


def fresh_turn_context_max_chars() -> int:
    return _bounded_int(
        os.environ.get("EIEL_LAB_ASSISTANT_BOOTSTRAP_MAX_CHARS", _DEFAULT_MAX_CHARS),
        default=_DEFAULT_MAX_CHARS,
        minimum=_MIN_MAX_CHARS,
        maximum=_MAX_MAX_CHARS,
    )


def fresh_turn_recent_chat_rows() -> int:
    return _bounded_int(
        os.environ.get("EIEL_LAB_ASSISTANT_BOOTSTRAP_CHAT_ROWS", _DEFAULT_RECENT_CHAT_ROWS),
        default=_DEFAULT_RECENT_CHAT_ROWS,
        minimum=0,
        maximum=_MAX_RECENT_CHAT_ROWS,
    )


def _clean_text(value: Any, *, limit: int = 180) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    if limit <= 3:
        return text[:limit]
    return text[: limit - 3].rstrip() + "..."


def _trimmed_recent_chat_rows(rows: Optional[List[Dict[str, Any]]], *, max_rows: int) -> List[Dict[str, str]]:
    if not rows or max_rows <= 0:
        return []
    normalized: List[Dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        role = str(row.get("role") or "").strip().lower()
        if role not in {"user", "assistant"}:
            continue
        content = _clean_text(row.get("content") or "", limit=220)
        if not content:
            continue
        normalized.append({"role": role, "content": content})
    if len(normalized) <= max_rows:
        return normalized
    return normalized[-max_rows:]


def _task_line(task: Dict[str, Any]) -> str:
    intake = dict(task.get("intake") or {})
    scope_contract = dict(task.get("scope_contract") or {})
    acceptance_contract = dict(task.get("acceptance_contract") or {})
    readiness = _clean_text(intake.get("readiness") or "", limit=40)
    missing = list(intake.get("missing_information") or [])
    intake_bits = []
    if readiness:
        intake_bits.append(f"intake={readiness}")
    if missing:
        intake_bits.append(f"missing={len(missing)}")
    assumptions = list(scope_contract.get("assumptions") or [])
    provisional = [
        row for row in assumptions if str(row.get("approval_state") or "").strip() in {"provisional", "needs_operator_confirmation"}
    ]
    current_scope = dict(scope_contract.get("current_scope") or {})
    if assumptions:
        intake_bits.append(f"assumptions={len(assumptions)}")
    if provisional:
        intake_bits.append(f"provisional={len(provisional)}")
    if str(current_scope.get("summary") or "").strip():
        intake_bits.append(f"scope={_clean_text(current_scope.get('summary') or '', limit=48)}")
    completion_state = dict(acceptance_contract.get("completion_state") or {})
    proof_targets = list(acceptance_contract.get("proof_targets") or [])
    review_gates = list(acceptance_contract.get("review_gates") or [])
    if acceptance_contract:
        done_state = "planned"
        if bool(completion_state.get("operator_approved")):
            done_state = "operator_approved"
        elif bool(completion_state.get("evidence_complete")):
            done_state = "evidence_complete"
        elif bool(completion_state.get("implementation_complete")):
            done_state = "implementation_complete"
        intake_bits.append(f"done={done_state}")
    if proof_targets:
        intake_bits.append(f"proof={len(proof_targets)}")
    pending_review = [row for row in review_gates if str(row.get("status") or "").strip() not in {"approved", "waived"}]
    if pending_review:
        intake_bits.append(f"review_pending={len(pending_review)}")
    decomposition_contract = dict(task.get("decomposition_contract") or {})
    plan_summary = dict(decomposition_contract.get("plan_summary") or {})
    replanning = dict(decomposition_contract.get("replanning") or {})
    if decomposition_contract:
        replan_state = _clean_text(replanning.get("state") or "settled", limit=24)
        if replan_state != "settled":
            intake_bits.append(f"replan={replan_state}")
        else:
            intake_bits.append(f"plan={_clean_text(decomposition_contract.get('mode') or '', limit=24)}")
    if int(plan_summary.get("milestone_count") or 0) > 0:
        intake_bits.append(f"milestones={int(plan_summary.get('milestone_count') or 0)}")
    if int(plan_summary.get("checkpoint_count") or 0) > 0:
        intake_bits.append(f"checkpoints={int(plan_summary.get('checkpoint_count') or 0)}")
    if int(plan_summary.get("dependency_edge_count") or 0) > 0:
        intake_bits.append(f"edges={int(plan_summary.get('dependency_edge_count') or 0)}")
    if int(plan_summary.get("fanout_ready_count") or 0) > 0:
        intake_bits.append(f"fanout_ready={int(plan_summary.get('fanout_ready_count') or 0)}")
    if int(plan_summary.get("forced_serial_count") or 0) > 0:
        intake_bits.append(f"forced_serial={int(plan_summary.get('forced_serial_count') or 0)}")
    if str(plan_summary.get("direct_summary") or "").strip():
        intake_bits.append(f"direct={_clean_text(plan_summary.get('direct_summary') or '', limit=40)}")
    if str(replanning.get("summary") or "").strip() and _clean_text(replanning.get("state") or "settled", limit=24) != "settled":
        intake_bits.append(f"replan_summary={_clean_text(replanning.get('summary') or '', limit=48)}")
    intake_suffix = "; " + ", ".join(intake_bits) if intake_bits else ""
    return (
        f"- [{_clean_text(task.get('kind') or 'task', limit=24)}] "
        f"{_clean_text(task.get('title') or task.get('task_id') or '', limit=120)} "
        f"(status={_clean_text(task.get('status') or '', limit=24)}; "
        f"next={_clean_text(task.get('next_expected_action') or '', limit=80)}{intake_suffix})"
    )


def _open_loop_line(loop: Dict[str, Any]) -> str:
    linked = _clean_text(loop.get("linked_task_id") or loop.get("linked_ledger_uid") or "", limit=36)
    suffix = f"; linked={linked}" if linked else ""
    return (
        f"- [{_clean_text(loop.get('kind') or 'loop', limit=32)}] "
        f"{_clean_text(loop.get('note') or loop.get('loop_id') or '', limit=140)}"
        f" (status={_clean_text(loop.get('status') or '', limit=24)}{suffix})"
    )


def _constraint_line(constraint: Dict[str, Any]) -> str:
    scope = _clean_text(constraint.get("scope") or "global", limit=20)
    return f"- {scope}:{_clean_text(constraint.get('key') or '', limit=36)}={_clean_text(constraint.get('value') or '', limit=120)}"


def _execution_ref_line(ref: Dict[str, Any]) -> str:
    label = _clean_text(ref.get("label") or ref.get("ref_id") or "", limit=90)
    task_id = _clean_text(ref.get("task_id") or ref.get("ledger_uid") or "", limit=36)
    task_suffix = f"; task={task_id}" if task_id else ""
    return (
        f"- [{_clean_text(ref.get('kind') or 'ref', limit=24)}] {label} "
        f"(status={_clean_text(ref.get('status') or '', limit=24)}{task_suffix})"
    )


def _recovery_note_line(snapshot: Dict[str, Any]) -> str:
    return (
        f"- status={_clean_text(snapshot.get('status') or 'unknown', limit=32)}; "
        f"summary={_clean_text(snapshot.get('summary') or 'No durable recovery note.', limit=180)}"
    )


def _chat_line(row: Dict[str, str]) -> str:
    speaker = "User" if str(row.get("role") or "").strip().lower() == "user" else "Assistant"
    return f"- {speaker}: {_clean_text(row.get('content') or '', limit=180)}"


def _mission_contract_lines(snapshot: Dict[str, Any]) -> List[str]:
    if not bool(snapshot.get("ok")):
        return [
            f"- status=unavailable; failure_class={_clean_text(snapshot.get('failure_class') or 'unknown', limit=40)}",
            f"- summary={_clean_text(snapshot.get('summary') or 'Canonical plane mission contract could not be loaded.', limit=180)}",
        ]
    runtime_projection = dict(snapshot.get("runtime_projection") or {})
    return [
        (
            f"- mission_id={_clean_text(runtime_projection.get('mission_id'), limit=48)}; "
            f"version={_clean_text(runtime_projection.get('mission_version'), limit=24)}; "
            f"fingerprint={_clean_text(runtime_projection.get('source_fingerprint'), limit=18)}"
        ),
        f"- promise={_clean_text(runtime_projection.get('product_promise'), limit=180)}",
        f"- anti_activity_warning={_clean_text(runtime_projection.get('anti_activity_warning'), limit=180)}",
        f"- claim_ceiling={_clean_text(runtime_projection.get('claim_ceiling_summary'), limit=180)}",
        f"- refresh_scope={_clean_text(runtime_projection.get('refresh_scope_summary'), limit=180)}",
        (
            f"- guardrails={len(runtime_projection.get('overreach_guardrails') or [])}; "
            f"reload_limits={len(runtime_projection.get('refresh_scope_reload_limits') or [])}; "
            f"aligned_boundaries={len(runtime_projection.get('aligned_expansion_boundaries') or [])}; "
            f"non_goals={len(runtime_projection.get('explicit_non_goals') or [])}"
        ),
    ]


def _fit_lines(
    *,
    output_lines: List[str],
    section_name: str,
    heading: str,
    item_lines: List[str],
    budget_chars: int,
    omitted_counts: Dict[str, int],
) -> None:
    if not item_lines:
        return
    prospective = output_lines + [heading]
    if len("\n".join(prospective)) > budget_chars:
        omitted_counts[section_name] = omitted_counts.get(section_name, 0) + len(item_lines)
        return
    output_lines.append(heading)
    included = 0
    for line in item_lines:
        prospective = output_lines + [line]
        if len("\n".join(prospective)) > budget_chars:
            break
        output_lines.append(line)
        included += 1
    omitted = len(item_lines) - included
    if omitted > 0:
        omitted_counts[section_name] = omitted_counts.get(section_name, 0) + omitted


def _core_lines(
    *,
    host_health: Dict[str, Any],
    assistant_state: Optional[Dict[str, Any]],
    task_register: Optional[Dict[str, Any]],
    summary_cache: Optional[Dict[str, Any]],
    correlation_snapshot: Optional[Dict[str, Any]],
    degraded_reasons: List[str],
) -> List[str]:
    lines = [
        "Lab Assistant fresh-turn bootstrap",
        "- durable_truth_order: runtime_state/task_register first; recent_chat supplemental only",
        (
            f"- host: status={_clean_text(host_health.get('status') or 'unknown', limit=24)}; "
            f"mode={_clean_text(host_health.get('mode') or 'unknown', limit=36)}"
        ),
    ]
    if assistant_state is None:
        lines.append("- assistant_state: missing")
    else:
        lines.append(
            "- assistant: "
            f"{_clean_text(assistant_state.get('assistant_label') or _lab_state.default_assistant_label(), limit=48)} "
            f"({_clean_text(assistant_state.get('assistant_id') or '', limit=48)}); "
            f"state={_clean_text(assistant_state.get('current_state') or '', limit=24)}; "
            f"reason={_clean_text(assistant_state.get('state_reason') or '', limit=96)}"
        )
    if task_register is None:
        lines.append("- task_register: missing")
    else:
        lines.append(
            "- register: "
            f"{_clean_text(task_register.get('register_id') or '', limit=48)}; "
            f"generation={int(task_register.get('write_generation') or 0)}; "
            f"active_tasks={len(task_register.get('active_tasks') or [])}; "
            f"open_loops={len(task_register.get('open_loops') or [])}; "
            f"constraints={len(task_register.get('constraints') or [])}; "
            f"recent_refs={len(task_register.get('recent_execution_refs') or [])}"
        )
    if summary_cache and isinstance(summary_cache.get("lines"), list):
        lines.append(
            f"- derived_summary_lines={len(summary_cache.get('lines') or [])}; "
            f"summary_generation={_int_or_default(summary_cache.get('register_generation'), -1)}"
        )
    if correlation_snapshot is None:
        lines.append("- correlation_delivery: unavailable")
    else:
        delivery_counts = dict(correlation_snapshot.get("delivery_counts") or {})
        lines.append(
            "- correlation_delivery: "
            f"pending={int(delivery_counts.get('pending_reattach', 0))}; "
            f"delivered={int(delivery_counts.get('delivered', 0))}; "
            f"ambiguous={int(delivery_counts.get('ambiguous', 0))}; "
            f"unresolved={int(delivery_counts.get('unresolved', 0))}"
        )
    if degraded_reasons:
        lines.append("- reload_status: degraded")
        lines.append("- degraded_reasons: " + ", ".join(sorted(set(_clean_text(item, limit=80) for item in degraded_reasons))))
    else:
        lines.append("- reload_status: ready")
    return lines


def build_fresh_turn_bootstrap(
    repo_root: Path,
    *,
    recent_chat_rows: Optional[List[Dict[str, Any]]] = None,
    max_chars: Optional[int] = None,
) -> Dict[str, Any]:
    """Assemble a deterministic per-turn bootstrap packet from durable state."""
    root = Path(repo_root).resolve()
    budget_chars = fresh_turn_context_max_chars() if max_chars is None else _bounded_int(
        max_chars,
        default=fresh_turn_context_max_chars(),
        minimum=_MIN_MAX_CHARS,
        maximum=_MAX_MAX_CHARS,
    )
    degraded_reasons: List[str] = []

    try:
        correlation_snapshot = _lab_correlation.read_snapshot(root, initialize_if_missing=False)
    except Exception as exc:
        correlation_snapshot = {"error": str(exc)}
        degraded_reasons.append(f"correlation_snapshot_unavailable:{type(exc).__name__}")

    try:
        host_health = _lab_host.host_health_snapshot(root, initialize_if_missing=False)
    except Exception as exc:
        host_health = {
            "status": "blocked",
            "mode": "bootstrap_read_failed",
            "summary": str(exc),
        }
        degraded_reasons.append(f"host_health_unavailable:{type(exc).__name__}")

    try:
        # Fresh-turn bootstrap must be resilient across cold restarts.
        assistant_snapshot = _lab_state.read_assistant_state_snapshot(root, initialize_if_missing=False)
        assistant_state = assistant_snapshot.get("assistant_state")
    except Exception as exc:
        assistant_snapshot = {"error": str(exc)}
        assistant_state = None
        degraded_reasons.append(f"assistant_state_unavailable:{type(exc).__name__}")

    try:
        # Only repair BD05 state when assistant identity already exists; bootstrap must
        # not manufacture assistant runtime state as a side effect of a read.
        register_snapshot = _lab_task_register.read_task_register_snapshot(
            root,
            initialize_if_missing=assistant_state is not None,
        )
        task_register = register_snapshot.get("task_register")
        summary_cache = register_snapshot.get("summary_cache")
    except Exception as exc:
        register_snapshot = {"error": str(exc)}
        task_register = None
        summary_cache = None
        degraded_reasons.append(f"task_register_unavailable:{type(exc).__name__}")
    recovery_snapshot = _lab_recovery.read_recovery_snapshot(root)
    mission_contract = _plane_mission_contract.read_plane_mission_contract(repo_root=root)

    host_status = str(host_health.get("status") or "").strip().lower()
    if host_status in {"blocked", "degraded"}:
        degraded_reasons.append(f"host_status:{host_status}")
    if assistant_state is None:
        degraded_reasons.append("assistant_state_missing")
    if task_register is None:
        degraded_reasons.append("task_register_missing")
    if assistant_state is not None and task_register is not None:
        if str(task_register.get("assistant_id_ref") or "") != str(assistant_state.get("assistant_id") or ""):
            degraded_reasons.append("assistant_id_mismatch")
        if bool(task_register.get("summary_stale")):
            degraded_reasons.append("summary_cache_stale")
    assistant_runtime_state = str((assistant_state or {}).get("current_state") or "").strip().lower()
    if assistant_runtime_state in {"blocked", "interrupted_recovering", "failed"}:
        degraded_reasons.append(f"assistant_runtime_state:{assistant_runtime_state}")
    if isinstance(correlation_snapshot, dict):
        delivery_counts = dict(correlation_snapshot.get("delivery_counts") or {})
        if int(delivery_counts.get("ambiguous", 0)) > 0 or int(delivery_counts.get("unresolved", 0)) > 0:
            degraded_reasons.append("detached_result_delivery_ambiguity_detected")
    continuation_snapshot = _continuation_packet_bootstrap.register_packet_snapshot(task_register, repo_root=root)
    if int(continuation_snapshot.get("blocked_count") or 0) > 0:
        degraded_reasons.append("continuation_packet_bootstrap_blocked")

    trimmed_chat = _trimmed_recent_chat_rows(recent_chat_rows, max_rows=fresh_turn_recent_chat_rows())
    omitted_counts: Dict[str, int] = {}
    if recent_chat_rows:
        omitted_counts["recent_chat_rows"] = max(0, len([r for r in recent_chat_rows if isinstance(r, dict)]) - len(trimmed_chat))

    core = _core_lines(
        host_health=host_health,
        assistant_state=assistant_state if isinstance(assistant_state, dict) else None,
        task_register=task_register if isinstance(task_register, dict) else None,
        summary_cache=summary_cache if isinstance(summary_cache, dict) else None,
        correlation_snapshot=correlation_snapshot if isinstance(correlation_snapshot, dict) else None,
        degraded_reasons=degraded_reasons,
    )
    output_lines = list(core)

    register_obj = task_register if isinstance(task_register, dict) else {}
    _fit_lines(
        output_lines=output_lines,
        section_name="recovery_notes",
        heading="Recovery notes:",
        item_lines=[_recovery_note_line(recovery_snapshot)],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="mission_contract",
        heading="Mission contract:",
        item_lines=_mission_contract_lines(mission_contract),
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="active_tasks",
        heading="Active tasks restored:",
        item_lines=[_task_line(task) for task in list(register_obj.get("active_tasks") or [])],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="residue_tasks",
        heading="Residue tasks restored:",
        item_lines=[_task_line(task) for task in list(register_obj.get("residue_tasks") or [])],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="completed_tasks",
        heading="Completed tasks restored:",
        item_lines=[_task_line(task) for task in list(register_obj.get("completed_tasks") or [])],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="open_loops",
        heading="Open loops restored:",
        item_lines=[_open_loop_line(loop) for loop in list(register_obj.get("open_loops") or [])],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="constraints",
        heading="Constraints restored:",
        item_lines=[_constraint_line(item) for item in list(register_obj.get("constraints") or [])],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="recent_execution_refs",
        heading="Recent delegated/work refs restored:",
        item_lines=[_execution_ref_line(item) for item in list(register_obj.get("recent_execution_refs") or [])],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="continuation_packets",
        heading="Continuation packets restored:",
        item_lines=[
            _continuation_packet_bootstrap.render_packet_line(row)
            for row in list(continuation_snapshot.get("rows") or [])
        ],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    continuation_projection_lines: List[str] = []
    for row in list(continuation_snapshot.get("rows") or []):
        task_id = _clean_text(row.get("task_id"))
        ledger_uid = _clean_text(row.get("ledger_uid"))
        binding_ref = {"type": "task_id", "id": task_id} if task_id else {"type": "ledger_uid", "id": ledger_uid}
        if not _clean_text(binding_ref.get("id")):
            continue
        continuation_projection_lines.extend(
            _continuation_attachment.render_surface_projection_lines(
                _continuation_attachment.inspect_continuation_state_for_binding(
                    binding_ref,
                    task_register=task_register,
                    repo_root=root,
                )
            )
        )
    _fit_lines(
        output_lines=output_lines,
        section_name="continuation_inspection",
        heading="Continuation inspection restored:",
        item_lines=continuation_projection_lines,
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )
    _fit_lines(
        output_lines=output_lines,
        section_name="recent_chat_rows",
        heading="Supplemental recent chat:",
        item_lines=[_chat_line(row) for row in trimmed_chat],
        budget_chars=budget_chars,
        omitted_counts=omitted_counts,
    )

    trim_bits = [
        f"{name}={count}"
        for name, count in (
            ("active_tasks", omitted_counts.get("active_tasks", 0)),
            ("residue_tasks", omitted_counts.get("residue_tasks", 0)),
            ("completed_tasks", omitted_counts.get("completed_tasks", 0)),
            ("open_loops", omitted_counts.get("open_loops", 0)),
            ("constraints", omitted_counts.get("constraints", 0)),
            ("recent_execution_refs", omitted_counts.get("recent_execution_refs", 0)),
            ("recent_chat_rows", omitted_counts.get("recent_chat_rows", 0)),
        )
        if int(count) > 0
    ]
    if trim_bits:
        trim_line = "- trimmed_for_budget: " + ", ".join(trim_bits)
        if len("\n".join(output_lines + [trim_line])) <= budget_chars:
            output_lines.append(trim_line)

    text = "\n".join(output_lines)
    if len(text) > budget_chars:
        text = text[:budget_chars].rstrip()
        if len(text) > 3:
            text = text[:-3].rstrip() + "..."

    return {
        "schema_version": BOOTSTRAP_SCHEMA_VERSION,
        "utc": _utc_now_iso(),
        "status": "degraded" if degraded_reasons else "ready",
        "degraded": bool(degraded_reasons),
        "degraded_reasons": sorted(set(degraded_reasons)),
        "host_health": host_health,
        "assistant_state": assistant_state if isinstance(assistant_state, dict) else None,
        "task_register": task_register if isinstance(task_register, dict) else None,
        "summary_cache": summary_cache if isinstance(summary_cache, dict) else None,
        "correlation_snapshot": correlation_snapshot if isinstance(correlation_snapshot, dict) else None,
        "recovery_snapshot": recovery_snapshot,
        "assistant_snapshot_error": str(assistant_snapshot.get("error") or ""),
        "task_register_snapshot_error": str(register_snapshot.get("error") or ""),
        "recent_chat_rows": trimmed_chat,
        "budget_chars": int(budget_chars),
        "used_chars": len(text),
        "mission_contract": mission_contract,
        "omitted_counts": {
            "active_tasks": int(omitted_counts.get("active_tasks", 0)),
            "open_loops": int(omitted_counts.get("open_loops", 0)),
            "constraints": int(omitted_counts.get("constraints", 0)),
            "recent_execution_refs": int(omitted_counts.get("recent_execution_refs", 0)),
            "continuation_packets": int(omitted_counts.get("continuation_packets", 0)),
            "recovery_notes": int(omitted_counts.get("recovery_notes", 0)),
            "mission_contract": int(omitted_counts.get("mission_contract", 0)),
            "recent_chat_rows": int(omitted_counts.get("recent_chat_rows", 0)),
        },
        "continuation_packet_snapshot": continuation_snapshot,
        "text": text,
    }


def render_fresh_turn_bootstrap_text(
    repo_root: Path,
    *,
    recent_chat_rows: Optional[List[Dict[str, Any]]] = None,
    max_chars: Optional[int] = None,
) -> str:
    packet = build_fresh_turn_bootstrap(repo_root, recent_chat_rows=recent_chat_rows, max_chars=max_chars)
    return (
        "Fresh-turn durable bootstrap:\n"
        f"{packet['text']}\n\n"
        "Bootstrap rules:\n"
        "- Use durable assistant state and task register as primary truth.\n"
        "- Treat recent chat as supplemental only.\n"
        "- If the bootstrap says state is degraded or missing, say that plainly instead of inventing continuity.\n\n"
        f"{_lab_delegate_policy.render_answer_delegate_policy_text(repo_root=repo_root)}"
    )
