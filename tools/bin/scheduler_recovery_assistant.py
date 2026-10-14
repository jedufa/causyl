#!/usr/bin/env python3
"""Interactive recovery assistant for scheduled jobs.

This wraps scheduler_pipeline_healer with an operator-first flow:
1) Diagnose current scheduler state (dry-run)
2) Propose the safest repair strategy
3) Ask for explicit confirmation
4) Apply repairs
5) Optionally relaunch recently failed jobs
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]


def _ensure_repo_in_path() -> None:
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))


_ensure_repo_in_path()

from tools.bin.scheduler_tty_ui import prompt_menu, prompt_yes_no
from infra.controlplane.sched import operator_backend as sched_backend


def _utc_now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _parse_utc(value: str) -> dt.datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return dt.datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(dt.timezone.utc)
    except Exception:
        return None


def _yes_no(prompt: str, *, default: bool = False) -> bool:
    try:
        return prompt_yes_no(prompt, default=default, fallback_reader=input)
    except EOFError:
        return default


def _choose(prompt: str, options: Sequence[str], *, default: str) -> str:
    try:
        return prompt_menu(
            prompt,
            choices=list(options),
            default_index=list(options).index(default) if default in options else 0,
            fallback_reader=input,
        )
    except EOFError:
        return default


def _recommend_flags(findings: List[Dict[str, Any]]) -> Dict[str, bool]:
    allow_enable_heads = any(str(f.get("finding_type") or "") == "disabled_chain_head_with_blocked_dependents" for f in findings)
    allow_runtime_repair = any(str(f.get("repair_class") or "") == "runtime_route_repair" for f in findings)
    # Kicking repaired heads is generally safe and useful after explicit confirmation.
    allow_kick = bool(findings)
    return {
        "allow_enable_heads": allow_enable_heads,
        "allow_runtime_repair": allow_runtime_repair,
        "allow_kick": allow_kick,
    }


def _is_recurring_job(job_id: str) -> bool:
    return str(job_id or "").strip().startswith("recurring_")


def _load_rows_by_job(*, repo_root: Path) -> Dict[str, Dict[str, Any]]:
    from infra.controlplane import scheduled_jobs as sched

    rows = sched.list_jobs(repo_root=repo_root)
    return {str(row.get("job_id") or ""): row for row in rows if str(row.get("job_id") or "").strip()}


def _runtime_preview_text(runtime: Dict[str, Any]) -> str:
    tech = str(runtime.get("agent_tech") or "").strip() or "?"
    model = str(runtime.get("model") or "").strip() or "?"
    fallbacks = list(runtime.get("fallback_models") or [])
    fb_preview = ", ".join(str(x) for x in fallbacks[:2]) if fallbacks else "-"
    return f"{tech}/{model} | fallback_models={fb_preview}"


def _summarize_planned_action(
    finding: Dict[str, Any],
    *,
    row: Dict[str, Any] | None,
    runtime_target: Dict[str, Any],
) -> str:
    finding_type = str(finding.get("finding_type") or "")
    job_id = str(finding.get("job_id") or "")
    if row is None:
        return f"- {job_id} | unable to load current job state"
    state = row.get("state") if isinstance(row.get("state"), dict) else {}
    due = row.get("due") if isinstance(row.get("due"), dict) else {}
    if finding_type == "once_slot_consumed_on_failure":
        return (
            f"- {job_id} | clear state.last_slot_key={state.get('last_slot_key') or '-'} "
            f"| last_status={state.get('last_status') or '-'} | due={due.get('reason') or '-'}"
        )
    if finding_type == "stranded_active_run":
        return (
            f"- {job_id} | clear active_run_id={state.get('active_run_id') or '-'} "
            f"| claim_reason={state.get('active_claim_reason') or '-'} "
            f"| last_status={state.get('last_status') or '-'}"
        )
    if finding_type == "disabled_chain_head_with_blocked_dependents":
        return (
            f"- {job_id} | enabled: {str(bool(row.get('enabled'))).lower()} -> true "
            f"| blocked_dependents={len(list((finding.get('evidence') or {}).get('blocked_dependents') or []))}"
        )
    if finding_type == "repeated_runtime_signature_failure":
        current_runtime = row.get("runtime") if isinstance(row.get("runtime"), dict) else {}
        changed = current_runtime != runtime_target
        return (
            f"- {job_id}\n"
            f"    change:  {'yes' if changed else 'no (already on healer profile)'}\n"
            f"    before: {_runtime_preview_text(current_runtime)}\n"
            f"    after:  {_runtime_preview_text(runtime_target)}"
        )
    if finding_type == "dependency_chain_deadlock":
        evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
        blocked = list(evidence.get("blocked_dependents") or [])
        preview = ", ".join(str(x) for x in blocked[:3]) if blocked else "-"
        suffix = " ..." if len(blocked) > 3 else ""
        return (
            f"- {job_id} | root_cause={evidence.get('root_cause') or '-'} "
            f"| blocked_dependents={preview}{suffix}"
        )
    return f"- {job_id} | {finding_type}"


def _print_safety_contract(findings: List[Dict[str, Any]]) -> None:
    recurring = sorted({str(f.get("job_id") or "") for f in findings if _is_recurring_job(str(f.get("job_id") or ""))})
    print("")
    print("Safety contract")
    print("- mutates only: enabled, state.last_slot_key, runtime.*")
    print("- never resets history, receipts, or checkpoints")
    print("- never kills active runs")
    print("- kick only runs a normal scheduler execute_job() on a due eligible repaired head")
    if recurring:
        print(f"- recurring jobs in scope: {', '.join(recurring[:4])}{' ...' if len(recurring) > 4 else ''}")
        print("  review runtime-route repairs for recurring jobs carefully before applying")


def _print_planned_actions(findings: List[Dict[str, Any]], *, repo_root: Path) -> None:
    from tools.bin import scheduler_pipeline_healer as healer

    rows_by_job = _load_rows_by_job(repo_root=repo_root)
    runtime_target = healer._runtime_healer_profile()
    print("")
    print("Planned mutations")
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        key = (str(finding.get("job_id") or ""), str(finding.get("finding_type") or ""))
        if key in seen:
            continue
        seen.add(key)
        row = rows_by_job.get(key[0])
        print(_summarize_planned_action(finding, row=row, runtime_target=runtime_target))


def _print_diagnosis(report: Dict[str, Any]) -> None:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    heartbeat = report.get("heartbeat") if isinstance(report.get("heartbeat"), dict) else {}
    findings = report.get("findings") if isinstance(report.get("findings"), list) else []
    grouped = _group_findings(findings)
    print("")
    print("Recovery diagnosis")
    print(f"- findings: {int(summary.get('finding_count') or len(findings))}")
    print(f"- deadlocks: {int(summary.get('deadlock_count') or 0)}")
    print(f"- active jobs: {int(heartbeat.get('active_job_count') or 0)}")
    print(f"- due eligible jobs: {int(heartbeat.get('due_eligible_count') or 0)}")
    print(f"- oldest blocked head: {str(heartbeat.get('oldest_blocked_chain_head') or '')}")
    if not findings:
        print("- no findings")
        return

    print("")
    print("Findings by class")
    for finding_type, rows in grouped:
        print(f"- {finding_type}: {len(rows)}")

    deadlocks = [row for row in findings if str(row.get("finding_type") or "") == "dependency_chain_deadlock"]
    if deadlocks:
        print("")
        print("Deadlocked chain heads")
        for row in deadlocks[:8]:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            blocked = list(evidence.get("blocked_dependents") or [])
            print(
                f"- {row.get('job_id')} | root={evidence.get('root_cause') or '?'} | "
                f"blocked_dependents={len(blocked)} | repair={row.get('repair_class')}"
            )
            if blocked:
                preview = ", ".join(str(x) for x in blocked[:3])
                suffix = " ..." if len(blocked) > 3 else ""
                print(f"    dependents: {preview}{suffix}")

    runtime_loops = [row for row in findings if str(row.get("finding_type") or "") == "repeated_runtime_signature_failure"]
    if runtime_loops:
        print("")
        print("Runtime failure loops")
        for row in runtime_loops[:8]:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            sig = str(evidence.get("error_signature_hash") or "")[:10]
            err = _compact_text(str(evidence.get("latest_error") or ""), limit=120)
            print(
                f"- {row.get('job_id')} | repeats={int(evidence.get('repeat_count') or 0)} | "
                f"sig={sig} | repair={row.get('repair_class')}"
            )
            if err:
                print(f"    error: {err}")

    stranded = [row for row in findings if str(row.get("finding_type") or "") == "stranded_active_run"]
    if stranded:
        print("")
        print("Stranded active runs")
        for row in stranded[:8]:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            print(
                f"- {row.get('job_id')} | active_run_id={evidence.get('active_run_id') or '?'} "
                f"| claim_reason={evidence.get('active_claim_reason') or '?'} | repair={row.get('repair_class')}"
            )

    other_rows = [
        row for row in findings
        if str(row.get("finding_type") or "") not in {"dependency_chain_deadlock", "repeated_runtime_signature_failure", "stranded_active_run"}
    ]
    if other_rows:
        print("")
        print("Other actionable findings")
        for row in other_rows[:8]:
            evidence = row.get("evidence") if isinstance(row.get("evidence"), dict) else {}
            print(
                f"- {row.get('finding_type')} | job={row.get('job_id')} | "
                f"last_status={evidence.get('last_status') or '?'} | repair={row.get('repair_class')}"
            )
            print(f"    summary: {row.get('summary')}")


def _group_findings(findings: List[Dict[str, Any]]) -> List[Tuple[str, List[Dict[str, Any]]]]:
    buckets: Dict[str, List[Dict[str, Any]]] = {}
    for row in findings:
        key = str(row.get("finding_type") or "unknown")
        buckets.setdefault(key, []).append(row)
    return sorted(buckets.items(), key=lambda item: (-len(item[1]), item[0]))


def _compact_text(raw: str, *, limit: int) -> str:
    text = " ".join(str(raw or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _print_recent_failed_candidates(rows: List[Dict[str, Any]]) -> None:
    if not rows:
        print("- none")
        return
    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx}. {row['job_id']} | status={row['last_status']} | "
            f"finished={row['last_finished_utc'] or '?'} | due_reason={row['due_reason'] or '?'}"
        )


def _select_relaunch_jobs(rows: List[Dict[str, Any]]) -> List[str]:
    if not rows:
        return []
    choice = _choose("Relaunch which jobs?", ["none", "all", "pick"], default="none")
    if choice == "none":
        return []
    if choice == "all":
        return [str(row["job_id"]) for row in rows]
    selected = prompt_menu(
        "Select failed job to relaunch",
        choices=rows,
        render=lambda row: f"{row['job_id']} | status={row['last_status']} | finished={row['last_finished_utc'] or '?'}",
        default_index=0,
        fallback_reader=input,
    )
    return [str(selected["job_id"])]


def _print_recommended_plan(findings: List[Dict[str, Any]], flags: Dict[str, bool]) -> None:
    enable_count = sum(1 for row in findings if str(row.get("finding_type") or "") == "disabled_chain_head_with_blocked_dependents")
    runtime_count = sum(
        1
        for row in findings
        if str(row.get("finding_type") or "") == "repeated_runtime_signature_failure"
        and bool((row.get("assistant_meta") or {}).get("runtime_change_needed", True))
    )
    deadlock_count = sum(1 for row in findings if str(row.get("finding_type") or "") == "dependency_chain_deadlock")
    print("")
    print("Recommended repair plan")
    print("- apply: true")
    print(f"- re-enable blocked parent jobs when safe: {str(flags['allow_enable_heads']).lower()}  # affected_heads={enable_count}")
    print(f"- change the runtime/model for jobs stuck in the same failure loop: {str(flags['allow_runtime_repair']).lower()}  # affected_jobs={runtime_count}")
    print(f"- immediately start one repaired job if it is ready to run now: {str(flags['allow_kick']).lower()}  # deadlocked_heads={deadlock_count}")


def _confirm_flags(flags: Dict[str, bool]) -> Dict[str, bool]:
    decided = dict(flags)
    if flags["allow_enable_heads"]:
        decided["allow_enable_heads"] = _yes_no(
            "Re-enable blocked parent jobs when that safely unblocks their dependent jobs?",
            default=True,
        )
    if flags["allow_runtime_repair"]:
        decided["allow_runtime_repair"] = _yes_no(
            "Change the runtime/model for jobs that are stuck repeating the same failure?",
            default=True,
        )
    if flags["allow_kick"]:
        decided["allow_kick"] = _yes_no(
            "After repair, immediately start one repaired job that is already due to run?",
            default=True,
        )
    return decided


def _filter_recent_failed_for_safe_relaunch(rows: List[Dict[str, Any]]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    safe: List[Dict[str, Any]] = []
    deferred: List[Dict[str, Any]] = []
    for row in rows:
        if _is_recurring_job(str(row.get("job_id") or "")):
            deferred.append(row)
        else:
            safe.append(row)
    return safe, deferred


def _annotate_findings_for_assistant(findings: List[Dict[str, Any]], *, repo_root: Path) -> List[Dict[str, Any]]:
    from tools.bin import scheduler_pipeline_healer as healer

    rows_by_job = _load_rows_by_job(repo_root=repo_root)
    runtime_target = healer._runtime_healer_profile()
    out: List[Dict[str, Any]] = []
    for finding in findings:
        row = dict(finding)
        meta = dict(row.get("assistant_meta") or {})
        if str(row.get("finding_type") or "") == "repeated_runtime_signature_failure":
            current_row = rows_by_job.get(str(row.get("job_id") or ""))
            current_runtime = (current_row or {}).get("runtime") if isinstance((current_row or {}).get("runtime"), dict) else {}
            meta["runtime_change_needed"] = bool(current_runtime != runtime_target)
        row["assistant_meta"] = meta
        out.append(row)
    return out


def _recent_failed_jobs(*, repo_root: Path, hours: int, limit: int) -> List[Dict[str, Any]]:
    from infra.controlplane import scheduled_jobs as sched

    cutoff = _utc_now() - dt.timedelta(hours=max(1, int(hours)))
    rows = sched.list_jobs(repo_root=repo_root)
    out: List[Dict[str, Any]] = []
    for row in rows:
        job_id = str(row.get("job_id") or "").strip()
        if not job_id:
            continue
        if not bool(row.get("enabled")):
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        last_status = str(state.get("last_status") or "").strip().lower()
        if last_status not in {"error", "failed_verification", "interrupted"}:
            continue
        if str(state.get("active_run_id") or "").strip():
            continue
        finished = _parse_utc(str(state.get("last_finished_utc") or state.get("updated_utc") or ""))
        if not finished or finished < cutoff:
            continue
        out.append(
            {
                "job_id": job_id,
                "last_status": last_status,
                "last_run_id": str(state.get("last_run_id") or ""),
                "last_finished_utc": str(state.get("last_finished_utc") or ""),
                "due_reason": str((row.get("due") or {}).get("reason") or ""),
            }
        )
    out.sort(key=lambda r: str(r.get("last_finished_utc") or ""), reverse=True)
    return out[: max(1, int(limit))]


def _launch_jobs_force(job_ids: List[str], *, repo_root: Path, verbose: bool = True) -> List[Dict[str, Any]]:
    from infra.controlplane import scheduled_jobs as sched

    results: List[Dict[str, Any]] = []
    total = len(job_ids)
    for idx, job_id in enumerate(job_ids, start=1):
        if verbose:
            print(f"[relaunch] {idx}/{total} starting {job_id}", flush=True)
        payload = sched.execute_job(job_id, repo_root=repo_root, trigger="recovery_assistant", force=True)
        row = (
            {
                "job_id": job_id,
                "ok": bool(payload.get("ok")),
                "skipped": bool(payload.get("skipped")),
                "reason": str(payload.get("reason") or ""),
                "run_id": str(payload.get("run_id") or ""),
                "receipt_path": str(payload.get("receipt_path") or ""),
            }
        )
        results.append(row)
        if verbose:
            print(
                f"[relaunch] {idx}/{total} done {job_id} | ok={str(row['ok']).lower()} "
                f"| skipped={str(row['skipped']).lower()} | reason={row['reason'] or '-'} "
                f"| run_id={row['run_id'] or '-'}",
                flush=True,
            )
    return results


def main(argv: List[str] | None = None) -> int:
    try:
        _ensure_repo_in_path()
        from tools.bin import scheduler_pipeline_healer as healer

        parser = argparse.ArgumentParser(
            description="Interactive scheduler recovery assistant (diagnose -> confirm -> repair -> optional relaunch)."
        )
        parser.add_argument("--repo-root", default=str(REPO_ROOT))
        parser.add_argument("--repeat-failure-threshold", type=int, default=1)
        parser.add_argument("--idle-minutes", type=int, default=60)
        parser.add_argument("--max-repairs-per-job-per-hour", type=int, default=2)
        parser.add_argument("--max-repairs-global-per-day", type=int, default=10)
        parser.add_argument("--recent-failed-hours", type=int, default=8)
        parser.add_argument("--recent-failed-limit", type=int, default=15)
        parser.add_argument("--yes", action="store_true", help="Apply recommended repairs without prompt.")
        parser.add_argument(
            "--relaunch-recent-failed",
            action="store_true",
            help="After repair, relaunch recent failed jobs without prompt.",
        )
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        if sched_backend.selected_backend(args.backend or None) == "v2":
            print(
                json.dumps(
                    {
                        "ok": True,
                        "scheduler_backend": "v2",
                        "skipped": True,
                        "reason": "v2 recovery uses the watchdog decision surface; legacy recovery assistant is disabled unless --backend legacy is explicit",
                    },
                    indent=2,
                    ensure_ascii=False,
                )
            )
            return 0
        repo_root = Path(args.repo_root).resolve()
        preview = healer.run_healer(
            repo_root=repo_root,
            apply=False,
            repeat_failure_threshold=max(1, int(args.repeat_failure_threshold)),
            idle_minutes=max(1, int(args.idle_minutes)),
            allow_enable_heads=False,
            allow_runtime_repair=False,
            allow_kick=False,
            max_repairs_per_job=max(1, int(args.max_repairs_per_job_per_hour)),
            max_repairs_global=max(1, int(args.max_repairs_global_per_day)),
        )
        findings = preview.get("findings") if isinstance(preview.get("findings"), list) else []
        findings = _annotate_findings_for_assistant(findings, repo_root=repo_root)
        preview = dict(preview)
        preview["findings"] = findings
        _print_diagnosis(preview)
        if not findings:
            print("")
            print("No recovery action needed.")
            return 0

        flags = _recommend_flags(findings)
        _print_safety_contract(findings)
        _print_planned_actions(findings, repo_root=repo_root)
        _print_recommended_plan(findings, flags)

        chosen_flags = dict(flags) if args.yes else _confirm_flags(flags)
        apply_repairs = bool(args.yes) or _yes_no("Apply the selected repairs now?", default=False)
        if not apply_repairs:
            print("Recovery aborted by operator.")
            return 1

        applied = healer.run_healer(
            repo_root=repo_root,
            apply=True,
            repeat_failure_threshold=max(1, int(args.repeat_failure_threshold)),
            idle_minutes=max(1, int(args.idle_minutes)),
            allow_enable_heads=bool(chosen_flags["allow_enable_heads"]),
            allow_runtime_repair=bool(chosen_flags["allow_runtime_repair"]),
            allow_kick=bool(chosen_flags["allow_kick"]),
            max_repairs_per_job=max(1, int(args.max_repairs_per_job_per_hour)),
            max_repairs_global=max(1, int(args.max_repairs_global_per_day)),
        )
        print("")
        print("Repairs applied")
        print(json.dumps(applied.get("summary") or {}, indent=2, ensure_ascii=False))
        print(f"- receipt: {applied.get('receipt_path')}")
        print(f"- report: {applied.get('report_path')}")

        recent = _recent_failed_jobs(
            repo_root=repo_root,
            hours=max(1, int(args.recent_failed_hours)),
            limit=max(1, int(args.recent_failed_limit)),
        )
        safe_recent, deferred_recent = _filter_recent_failed_for_safe_relaunch(recent)
        if not recent:
            print("")
            print("No recent failed jobs eligible for forced relaunch.")
            return 0

        print("")
        print("Recent failed jobs eligible for relaunch")
        _print_recent_failed_candidates(safe_recent)
        if deferred_recent:
            print("")
            print("Relaunch deferred by safety policy")
            for row in deferred_recent:
                print(f"- {row['job_id']} | recurring job; review manually before relaunch")

        selected_job_ids = [row["job_id"] for row in safe_recent] if args.relaunch_recent_failed else _select_relaunch_jobs(safe_recent)
        if not selected_job_ids:
            print("Relaunch skipped by operator.")
            return 0

        launch_results = _launch_jobs_force(selected_job_ids, repo_root=repo_root)
        print("")
        print("Relaunch results")
        for row in launch_results:
            print(
                f"- {row['job_id']} | ok={str(row['ok']).lower()} | skipped={str(row['skipped']).lower()} "
                f"| reason={row['reason']} | run_id={row['run_id']}"
            )
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
