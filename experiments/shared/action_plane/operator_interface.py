"""Compact operator-facing interface for the plane.

This module provides a small intent-first request surface over the implemented
action families. It does not replace substrate truth; it classifies, routes,
executes, and renders the existing deterministic actions through one stable
operator result envelope.
"""

from __future__ import annotations

import json
import math
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from infra.bin.lab_transcript import recent_session_context
from infra.webui.server_codex_runtime import run_codex_oneshot
from infra.webui.ledger.ledger_manager import ChangeLedger

from .design_contracts import build_operator_verb_surface_policy_v1, build_request_decomposition_v1
from .phase1_runtime import (
    assess_pis_proof_gap_v1,
    execute_patch_ingest_repair_v1,
    inspect_job_bounded_recovery_v1,
    load_recorded_fallback_events_v1,
    record_fallback_event_v1,
    synthesize_improvement_opportunity_v1,
)
from .phase2_playbooks import (
    execute_build_verify_loop_v1,
    execute_patch_apply_chain_v1,
    execute_status_reconcile_v1,
)
from .phase2_runtime import build_snapshot_drift_action_request
from .phase3_flow import execute_phase3_blocked_chain_explain_and_recommend_v1
from .phase4_flow import build_action_plane_all_phases_report_v1, execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1
from .routing import classify_phase1_action_request, load_action_registry

_DIRECT_ACTIONS = {
    "patch_ingest_repair_v1": execute_patch_ingest_repair_v1,
    "pis_assess_proof_gap_v1": assess_pis_proof_gap_v1,
    "job_inspect_bounded_recovery_v1": inspect_job_bounded_recovery_v1,
    "patch_apply_chain_v1": execute_patch_apply_chain_v1,
    "build_verify_loop_v1": execute_build_verify_loop_v1,
    "status_reconcile_v1": execute_status_reconcile_v1,
    "implementation_validate_v1": None,  # wired below after helper definition
    "blocked_chain_explain_and_recommend_v1": execute_phase3_blocked_chain_explain_and_recommend_v1,
    "repeated_patch_fallback_to_approved_upgrade_v1": execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1,
}

_VERB_ALIASES = {
    "server status": "server_status_v1",
    "inspect server": "server_status_v1",
    "check server": "server_status_v1",
    "server start": "server_start_v1",
    "start server": "server_start_v1",
    "server stop": "server_stop_v1",
    "stop server": "server_stop_v1",
    "server watch": "server_watch_v1",
    "watch server": "server_watch_v1",
    "server watchdog": "server_watchdog_v1",
    "watchdog server": "server_watchdog_v1",
    "integrate zipped pises": "repo_import_pis_zip_v1",
    "integrate zipped pises from downloads directory": "repo_import_pis_zip_v1",
    "import pis zip": "repo_import_pis_zip_v1",
    "import intent sheet zip": "repo_import_intent_sheet_zip_v1",
    "archive": "repo_archive_snapshot_v1",
    "archive debug": "repo_archive_snapshot_v1",
    "archive weekly": "repo_archive_snapshot_v1",
    "commit": "repo_commit_tag_v1",
    "commit changes": "repo_commit_tag_v1",
    "show commit journal": "repo_show_commit_journal_v1",
    "show recent commits": "repo_show_recent_commits_v1",
    "apply patch": "patch_ingest_repair_v1",
    "apply incoming patch": "patch_ingest_repair_v1",
    "assess item": "pis_assess_proof_gap_v1",
    "assess item proof": "pis_assess_proof_gap_v1",
    "inspect job": "job_inspect_bounded_recovery_v1",
    "inspect/recover job": "build_verify_loop_v1",
    "recover job": "build_verify_loop_v1",
    "verify outcome": "status_reconcile_v1",
    "verify implementation": "status_reconcile_v1",
    "validate the implementation": "implementation_validate_v1",
    "validate this implementation": "implementation_validate_v1",
    "review this implementation": "implementation_validate_v1",
    "review the implementation": "implementation_validate_v1",
    "is this implementation correct": "implementation_validate_v1",
    "check the implementation": "implementation_validate_v1",
    "check whether this patch is really implemented": "implementation_validate_v1",
    "is this patch really implemented": "implementation_validate_v1",
    "explain blocked state": "blocked_chain_explain_and_recommend_v1",
    "continue interrupted work": "build_verify_loop_v1",
    "gather proof": "pis_assess_proof_gap_v1",
    "recover service": "service_recover_v1",
    "restart server": "service_recover_v1",
    "server restart": "service_recover_v1",
    "restart runtime": "service_recover_v1",
    "runtime restart": "service_recover_v1",
    "run safe maintenance": "safe_maintenance_v1",
    "prepare implementation": "implementation_prepare_v1",
    "what is the next intent sheet": "implementation_prepare_v1",
    "what are the next intent sheets": "implementation_prepare_v1",
    "next intent sheet": "implementation_prepare_v1",
    "next intent sheets": "implementation_prepare_v1",
    "what is the next intent sheet to implement": "implementation_prepare_v1",
    "what are the next intent sheets to implement": "implementation_prepare_v1",
    "implement the next intent sheet": "implement_recursive_v1",
    "implement next intent sheet": "implement_recursive_v1",
    "implement the next intent sheets": "implementation_prepare_v1",
    "implement next intent sheets": "implementation_prepare_v1",
    "next intent sheets to implement": "implementation_prepare_v1",
    "next intent sheet to implement": "implementation_prepare_v1",
    "what are the next pises to implement": "implementation_prepare_pis_v1",
    "what are the next pis to implement": "implementation_prepare_pis_v1",
    "next pises to implement": "implementation_prepare_pis_v1",
    "next pis to implement": "implementation_prepare_pis_v1",
    "implement": "implement_recursive_v1",
    "inspect scheduler": "scheduler_inspect_v1",
    "recover scheduler": "scheduler_recover_v1",
    "scheduler disable all jobs": "scheduler_disable_all_v1",
    "disable all scheduler jobs": "scheduler_disable_all_v1",
    "scheduler enable all jobs": "scheduler_enable_all_v1",
    "enable all scheduler jobs": "scheduler_enable_all_v1",
    "scheduler watch": "scheduler_watch_v1",
    "watch scheduler": "scheduler_watch_v1",
    "scheduler pause": "scheduler_pause_v1",
    "pause scheduler": "scheduler_pause_v1",
    "scheduler resume": "scheduler_resume_v1",
    "resume scheduler": "scheduler_resume_v1",
    "inspect backlog": "backlog_inspect_v1",
}

_FAMILY_MAP = {
    "server_status_v1": "server_status",
    "server_start_v1": "server_status",
    "server_stop_v1": "server_status",
    "server_watch_v1": "server_status",
    "server_watchdog_v1": "server_status",
    "repo_import_pis_zip_v1": "ledger_import",
    "repo_import_intent_sheet_zip_v1": "ledger_import",
    "repo_archive_snapshot_v1": "archive_snapshot",
    "repo_commit_tag_v1": "repo_commit",
    "repo_show_commit_journal_v1": "repo_commit",
    "repo_show_recent_commits_v1": "repo_commit",
    "patch_ingest_repair_v1": "patch_apply",
    "patch_apply_chain_v1": "patch_apply",
    "pis_assess_proof_gap_v1": "pis_assess_proof_gap",
    "job_inspect_bounded_recovery_v1": "job_inspect_bounded_recovery",
    "build_verify_loop_v1": "build_verify",
    "status_reconcile_v1": "status_reconcile",
    "implementation_validate_v1": "status_reconcile",
    "blocked_chain_explain_and_recommend_v1": "patch_apply",
    "repeated_patch_fallback_to_approved_upgrade_v1": "patch_apply",
    "service_recover_v1": "service_recover",
    "safe_maintenance_v1": "maintenance",
    "implementation_prepare_v1": "implementation_prepare",
    "implementation_prepare_pis_v1": "implementation_prepare",
    "implement_recursive_v1": "implementation_execute",
    "scheduler_inspect_v1": "scheduler_inspect",
    "scheduler_recover_v1": "scheduler_recover",
    "scheduler_disable_all_v1": "scheduler_control",
    "scheduler_enable_all_v1": "scheduler_control",
    "scheduler_watch_v1": "scheduler_control",
    "scheduler_pause_v1": "scheduler_control",
    "scheduler_resume_v1": "scheduler_control",
    "backlog_inspect_v1": "backlog_inspect",
}

_CODEX_BOOTSTRAP_FILES = (
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md",
    "governance/agents/prompts/_all_codex.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_context.md",
    "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
)

_COMM_PARSE_ACTION_PROGRAMS: Dict[str, Dict[str, Any]] = {
    "repo_commit_tag_v1": {
        "kind": "single_script",
        "commands": ["bash tools/git/git-commit-tag.sh"],
        "summary": "Governed commit/tag helper.",
    },
    "repo_archive_snapshot_v1": {
        "kind": "single_script",
        "commands": ["bash infra/bin/archive"],
        "summary": "Repository archive helper.",
    },
    "repo_import_pis_zip_v1": {
        "kind": "single_script",
        "commands": ["python -m tools.bin.pis_import_zip --zip-path <path>"],
        "summary": "PIS zip import helper.",
    },
    "repo_import_intent_sheet_zip_v1": {
        "kind": "single_script",
        "commands": ["python -m tools.bin.intent_sheet_import_zip --zip-path <path>"],
        "summary": "Intent sheet zip import helper.",
    },
    "repo_show_commit_journal_v1": {
        "kind": "single_script",
        "commands": ["python -m tools.git.support.commit_journal view"],
        "summary": "Commit journal viewer.",
    },
    "repo_show_recent_commits_v1": {
        "kind": "single_script",
        "commands": ["git log --oneline -n 10"],
        "summary": "Recent commit viewer.",
    },
    "server_status_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/server-status"],
        "summary": "Server status wrapper.",
    },
    "server_start_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/server-start"],
        "summary": "Server start wrapper.",
    },
    "server_stop_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/server-stop"],
        "summary": "Server stop wrapper.",
    },
    "server_watch_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/server-watch --interval 2 --pipeline-limit 40"],
        "summary": "Live server dashboard.",
    },
    "server_watchdog_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/server-watchdog"],
        "summary": "Server watchdog shell.",
    },
    "service_recover_v1": {
        "kind": "script_sequence",
        "commands": [
            "bash infra/user/server-status",
            "bash infra/user/server-start",
            "bash infra/user/server-restart",
        ],
        "summary": "Service recover/restart sequence chosen from server wrappers.",
    },
    "safe_maintenance_v1": {
        "kind": "single_script",
        "commands": ["python infra/bin/scheduler_watchdog.py --mode audit"],
        "summary": "Scheduler watchdog audit.",
    },
    "scheduler_inspect_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/scheduler-health"],
        "summary": "Scheduler health wrapper.",
    },
    "scheduler_recover_v1": {
        "kind": "single_script",
        "commands": ["python infra/bin/scheduler_watchdog.py --mode selfheal"],
        "summary": "Scheduler recovery helper.",
    },
    "scheduler_disable_all_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/scheduler-disable-all"],
        "summary": "Disable all scheduled jobs.",
    },
    "scheduler_enable_all_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/scheduler-enable-all"],
        "summary": "Enable all scheduled jobs.",
    },
    "scheduler_watch_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/scheduler-watch --interval 2 --pipeline-limit 40"],
        "summary": "Live scheduler dashboard.",
    },
    "scheduler_pause_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/scheduler-pause"],
        "summary": "Pause scheduler safely.",
    },
    "scheduler_resume_v1": {
        "kind": "single_script",
        "commands": ["bash infra/user/scheduler-resume"],
        "summary": "Resume scheduler safely.",
    },
    "patch_ingest_repair_v1": {
        "kind": "deterministic_action",
        "commands": ["execute_patch_ingest_repair_v1(...)"],
        "summary": "Deterministic patch ingest/repair action.",
    },
    "patch_apply_chain_v1": {
        "kind": "playbook_sequence",
        "commands": [
            "execute_patch_apply_chain_v1(...)",
            "execute_phase3_blocked_chain_explain_and_recommend_v1(...)",
        ],
        "summary": "Patch apply playbook with bounded repair/reporting.",
    },
    "pis_assess_proof_gap_v1": {
        "kind": "deterministic_action",
        "commands": ["assess_pis_proof_gap_v1(...)"],
        "summary": "PIS proof-gap assessor.",
    },
    "job_inspect_bounded_recovery_v1": {
        "kind": "deterministic_action",
        "commands": ["inspect_job_bounded_recovery_v1(...)"],
        "summary": "Job inspect/recovery action.",
    },
    "build_verify_loop_v1": {
        "kind": "playbook_sequence",
        "commands": ["execute_build_verify_loop_v1(...)"],
        "summary": "Build/verify bounded loop.",
    },
    "status_reconcile_v1": {
        "kind": "playbook_sequence",
        "commands": ["execute_status_reconcile_v1(...)"],
        "summary": "Status reconcile playbook.",
    },
    "implementation_validate_v1": {
        "kind": "deterministic_action",
        "commands": [
            "resolve validation target from payload or recent lab session context",
            "execute_status_reconcile_v1(...) when target/evidence is sufficient",
        ],
        "summary": "Deterministic implementation-validation intake with bounded target resolution.",
    },
    "blocked_chain_explain_and_recommend_v1": {
        "kind": "deterministic_action",
        "commands": ["execute_phase3_blocked_chain_explain_and_recommend_v1(...)"],
        "summary": "Blocked-state explanation layer.",
    },
    "repeated_patch_fallback_to_approved_upgrade_v1": {
        "kind": "playbook_sequence",
        "commands": [
            "execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1(...)",
            "build_action_plane_all_phases_report_v1(...)",
        ],
        "summary": "Phase-4 absorption economics/proposal flow.",
    },
    "implementation_prepare_v1": {
        "kind": "single_script",
        "commands": ["python -m tools.bin.intent_sheet_next --limit 5"],
        "summary": "Intent-sheet implementation queue.",
    },
    "implementation_prepare_pis_v1": {
        "kind": "single_script",
        "commands": ["python -m tools.bin.pis_next --limit 5"],
        "summary": "PIS implementation queue.",
    },
    "implement_recursive_v1": {
        "kind": "single_script",
        "commands": ["python -m tools.bin.implement_recursive --uid <UID>"],
        "summary": "Recursive implementation driver.",
    },
    "backlog_inspect_v1": {
        "kind": "single_script",
        "commands": ["python -m tools.bin.intent_sheet_next --limit 10"],
        "summary": "Backlog inspection helper.",
    },
}

_OLLAMA_EMBED_BASE_URL = str(os.environ.get("EIEL_OLLAMA_BASE_URL") or "http://127.0.0.1:11434").strip().rstrip("/")
_OLLAMA_EMBED_MODEL = str(os.environ.get("EIEL_OPERATOR_ROUTER_EMBED_MODEL") or "all-minilm:latest").strip() or "all-minilm:latest"
_OLLAMA_PROBE_TTL_SECONDS = 30.0
_OLLAMA_PROBE_TIMEOUT_SECONDS = 0.35
_OLLAMA_EMBED_TIMEOUT_SECONDS = 1.2
_OLLAMA_ROUTE_MIN_SCORE = 0.78
_OLLAMA_ROUTE_MIN_MARGIN = 0.10
_OLLAMA_PROBE_STATE: Dict[str, Any] = {"checked_at": 0.0, "online": False}
_EMBEDDED_ROUTE_PROTOTYPES: Dict[str, List[str]] = {
    "implementation_prepare_v1": [
        "what is the next intent sheet to implement",
        "what is the next intent sheet",
        "next intent sheets to implement",
        "prepare implementation for intent sheets",
    ],
    "implementation_prepare_pis_v1": [
        "what are the next pises to implement",
        "which PIS is next",
        "next pis to implement",
        "prepare implementation for PIS items",
        "show me the next PIS to implement",
    ],
    "scheduler_resume_v1": [
        "resume scheduler",
        "resume the scheduler",
        "scheduler resume",
        "get the scheduler going again",
        "continue the scheduler",
    ],
    "scheduler_pause_v1": [
        "pause scheduler",
        "pause the scheduler",
        "scheduler pause",
        "stop the scheduler for now",
    ],
    "scheduler_watch_v1": [
        "watch scheduler",
        "scheduler watch",
        "show scheduler live watch",
    ],
    "scheduler_recover_v1": [
        "recover scheduler",
        "repair scheduler",
        "heal scheduler",
    ],
    "service_recover_v1": [
        "restart server",
        "restart the server",
        "recover service",
        "server restart",
        "recover the service",
        "bring the server back up",
    ],
    "server_status_v1": [
        "server status",
        "check server",
        "inspect server",
        "is the server up",
    ],
    "backlog_inspect_v1": [
        "inspect backlog",
        "what is next in the backlog",
        "next backlog items",
    ],
    "job_inspect_bounded_recovery_v1": [
        "inspect job",
        "stuck job",
        "failed job",
        "blocked job",
        "look at job",
    ],
    "repo_import_intent_sheet_zip_v1": [
        "import intent sheet zip from downloads",
        "bring in the intent sheet bundle from downloads",
        "import the intent sheet bundle from downloads",
    ],
    "repo_import_pis_zip_v1": [
        "integrate pis from downloads",
        "import the PIS bundle from downloads",
        "bring in the PIS bundle from downloads",
    ],
}


def _run_command_capture(*, command: Sequence[str], cwd: Path, stream_output: bool) -> subprocess.CompletedProcess[str]:
    if not stream_output:
        return subprocess.run(
            list(command),
            cwd=str(cwd),
            capture_output=True,
            text=True,
            check=False,
        )

    proc = subprocess.Popen(
        list(command),
        cwd=str(cwd),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    stdout_chunks: List[str] = []
    stderr_chunks: List[str] = []

    def _pump(src, dst, bucket: List[str]) -> None:
        try:
            for chunk in iter(src.readline, ""):
                bucket.append(chunk)
                dst.write(chunk)
                dst.flush()
        finally:
            src.close()

    stdout_thread = threading.Thread(target=_pump, args=(proc.stdout, sys.stdout, stdout_chunks))
    stderr_thread = threading.Thread(target=_pump, args=(proc.stderr, sys.stderr, stderr_chunks))
    stdout_thread.start()
    stderr_thread.start()
    returncode = proc.wait()
    stdout_thread.join()
    stderr_thread.join()
    return subprocess.CompletedProcess(
        args=list(command),
        returncode=returncode,
        stdout="".join(stdout_chunks),
        stderr="".join(stderr_chunks),
    )


def _command_result(
    *,
    cp: subprocess.CompletedProcess[str],
    command: Sequence[str],
    reason_ok: str,
    reason_error: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    ok = cp.returncode == 0
    reason = reason_ok if ok else str(cp.stderr or cp.stdout or reason_error).strip()
    result = {
        "ok": ok,
        "command": list(command),
        "stdout": str(cp.stdout or ""),
        "stderr": str(cp.stderr or ""),
        "exit_code": int(cp.returncode),
        "outcome": {
            "outcome": "success_deterministic" if ok else "blocked_requires_review",
            "verifier_verdict": "PASS" if ok else "",
            "proof_gaps": [] if ok else [reason],
            "evidence_artifacts": [],
            "validation_commands_executed": [" ".join(command)],
        },
        "terminal_reason": reason,
        "pre_escalation_checklist": {
            "deterministic_path_attempted": True,
            "recovery_attempted": False,
            "evidence_gathered": True,
        },
    }
    for key, value in dict(extra or {}).items():
        result[key] = value
    return result


def _run_shell_action(
    *,
    command: Sequence[str],
    repo_root: Path,
    stream_output: bool,
    reason_ok: str,
    reason_error: str,
    extra: Optional[Mapping[str, Any]] = None,
) -> Dict[str, Any]:
    cp = _run_command_capture(command=command, cwd=repo_root, stream_output=stream_output)
    return _command_result(cp=cp, command=command, reason_ok=reason_ok, reason_error=reason_error, extra=extra)


def _build_direct_operator_surface(action_id: str, result: Mapping[str, Any]) -> Dict[str, Any]:
    if action_id == "implementation_validate_v1":
        ok = bool(result.get("ok"))
        target_uid = str(result.get("resolved_target_uid") or "").strip()
        target_source = str(result.get("resolved_target_source") or "").strip()
        report = dict(result.get("report") or {})
        terminal_reason = str(result.get("terminal_reason") or report.get("terminal_reason") or "").strip()
        report_outcome = str(report.get("final_chain_outcome") or report.get("status") or "").strip()
        if not ok:
            reason = (
                "Validation needs an explicit target UID or recent session target."
                if terminal_reason == "missing_target_uid_for_implementation_validation"
                else (
                    "Implementation validation found unresolved proof gaps."
                    if report_outcome == "completed_with_proof_gap"
                    else (terminal_reason or "Implementation validation did not reach a passing result.")
                )
            )
            unresolved_items = ["missing_target_uid"] if terminal_reason == "missing_target_uid_for_implementation_validation" else []
            if report_outcome == "completed_with_proof_gap" and "missing_validation_commands" not in unresolved_items:
                unresolved_items.append("missing_validation_commands")
            required_next_steps = (
                ["Provide --target-uid or mention the target UID in the request."]
                if unresolved_items
                else []
            )
            if report_outcome == "completed_with_proof_gap":
                required_next_steps.append("Provide explicit validation commands or stronger proof inputs.")
            return {
                "executive_surface": {
                    "state": "REVIEW_REQUIRED",
                    "primary_next_action": {"label": "Provide validation target" if "missing_target_uid" in unresolved_items else "Provide stronger proof inputs"},
                    "confidence": "Low",
                    "short_reason": reason,
                },
                "status_class": "blocked",
                "status_summary": reason,
                "primary_explanation": {
                    "primary_reason": reason,
                    "proof_state": "uncertain",
                    "evidence_references": [target_uid] if target_uid else [],
                },
                "proof_uncertainty": {
                    "proof_gap_present": bool(unresolved_items),
                    "unresolved_items": unresolved_items,
                    "stale_evidence_present": False,
                    "contradiction_present": False,
                    "confidence_bucket": "low",
                    "required_next_proof_steps": required_next_steps,
                },
                "recommended_actions": [
                    {
                        "label": "Provide validation target",
                        "action_id": "implementation_validate_v1",
                        "reason": "The plane needs an explicit implementation target before it can validate truthfully.",
                    }
                ],
                "manual_review_required": True,
                "codex_transparency": {
                    "codex_used": False,
                    "usage_reason": "",
                    "recurrence_class": "",
                    "economic_recommendation_class": "",
                },
                "artifact_references": [target_uid] if target_uid else [],
                "section_order": [
                    "status_summary",
                    "primary_explanation",
                    "proof_uncertainty",
                    "recommended_actions",
                    "codex_transparency",
                    "artifact_references",
                ],
            }
        source_note = " using recent session context" if target_source == "recent_lab_session_context" else ""
        reason = f"Implementation validation completed for {target_uid or 'the target'}{source_note}."
        return {
            "executive_surface": {
                "state": "PASS",
                "primary_next_action": {"label": "Review validation outcome"},
                "confidence": "High",
                "short_reason": reason,
            },
            "status_class": "pass",
            "status_summary": reason,
            "primary_explanation": {
                "primary_reason": target_source or "explicit",
                "proof_state": "validated",
                "evidence_references": [target_uid] if target_uid else [],
            },
            "proof_uncertainty": {
                "proof_gap_present": False,
                "unresolved_items": [],
                "stale_evidence_present": False,
                "contradiction_present": False,
                "confidence_bucket": "high",
                "required_next_proof_steps": [],
            },
            "recommended_actions": [],
            "manual_review_required": False,
            "codex_transparency": {
                "codex_used": False,
                "usage_reason": "",
                "recurrence_class": "",
                "economic_recommendation_class": "",
            },
            "artifact_references": [target_uid] if target_uid else [],
            "section_order": [
                "status_summary",
                "primary_explanation",
                "proof_uncertainty",
                "recommended_actions",
                "codex_transparency",
                "artifact_references",
            ],
        }
    ok = bool(result.get("ok"))
    state = "PASS" if ok else "REVIEW_REQUIRED"
    confidence = "High" if ok else "Low"
    service_reason = "Service restart completed." if bool(result.get("restart_requested")) else "Service recovery check completed."
    summary_map = {
        "repo_commit_tag_v1": (
            "Commit completed and tagged through the governed git path." if ok else "Commit failed in the governed git path.",
            "Review commit result",
        ),
        "repo_archive_snapshot_v1": ("Archive command completed.", "Review archive result"),
        "server_status_v1": ("Server status retrieved.", "Review server status"),
        "server_start_v1": ("Server start completed.", "Review server status"),
        "server_stop_v1": ("Server stop completed.", "Review server status"),
        "server_watch_v1": ("Server watch started.", "Review live server dashboard"),
        "server_watchdog_v1": ("Server watchdog opened.", "Review watchdog state"),
        "repo_import_pis_zip_v1": ("PIS bundle import completed.", "Review imported PIS items"),
        "repo_import_intent_sheet_zip_v1": ("Intent Sheet bundle import completed.", "Review imported intent sheets"),
        "repo_show_commit_journal_v1": ("Pending commit journal entries listed below.", "Review pending journal entries"),
        "repo_show_recent_commits_v1": ("Recent commits listed below.", "Review recent commits"),
        "service_recover_v1": (service_reason, "Review service status"),
        "safe_maintenance_v1": ("Safe maintenance audit completed.", "Review maintenance findings"),
        "implementation_prepare_v1": ("Next implementation candidates are listed below.", "Review listed intent sheets"),
        "implementation_prepare_pis_v1": ("Next implementation candidates are listed below.", "Review listed PIS items"),
        "implement_recursive_v1": ("Implementation chain was started with the default build-verify-repair contract.", "Review implementation chain"),
        "implementation_validate_v1": ("Implementation validation completed.", "Review validation outcome"),
        "scheduler_inspect_v1": ("Scheduler health check completed.", "Review scheduler health"),
        "scheduler_recover_v1": ("Scheduler recovery flow completed.", "Review scheduler recovery result"),
        "scheduler_disable_all_v1": ("All scheduler jobs disabled.", "Review scheduler state"),
        "scheduler_enable_all_v1": ("All scheduler jobs enabled.", "Review scheduler state"),
        "scheduler_watch_v1": ("Scheduler watch started.", "Review live scheduler dashboard"),
        "scheduler_pause_v1": ("Scheduler pipeline paused.", "Review scheduler state"),
        "scheduler_resume_v1": ("Scheduler pipeline resumed.", "Review scheduler state"),
        "backlog_inspect_v1": ("Active backlog items are listed below.", "Review active backlog items"),
    }
    success_reason, next_label = summary_map.get(action_id, ("Command completed.", "Review result"))
    short_reason = success_reason if ok else str(result.get("terminal_reason") or "Command failed").strip()
    return {
        "executive_surface": {
            "state": state,
            "primary_next_action": {"label": next_label if ok else "Manual review required"},
            "confidence": confidence,
            "short_reason": short_reason,
        },
        "status_class": "pass" if ok else "blocked",
        "status_summary": short_reason,
        "primary_explanation": {
            "primary_reason": str(result.get("terminal_reason") or "").strip(),
            "proof_state": "validated" if ok else "uncertain",
            "evidence_references": [],
        },
        "proof_uncertainty": {
            "proof_gap_present": not ok,
            "unresolved_items": [] if ok else [str(result.get("terminal_reason") or "command_failed").strip()],
            "stale_evidence_present": False,
            "contradiction_present": False,
            "confidence_bucket": "high" if ok else "low",
            "required_next_proof_steps": [] if ok else ["manual_review"],
        },
        "recommended_actions": [] if ok else [{"label": "Manual review required", "reason": str(result.get("terminal_reason") or "command_failed").strip()}],
        "manual_review_required": not ok,
        "codex_transparency": {
            "codex_used": False,
            "usage_reason": "",
            "family": "",
            "recurrence_class": "",
            "economic_recommendation_class": "",
        },
        "artifact_references": [],
    }


def _ledger_active_item_count(root: Path) -> int:
    base = root / "governance" / "ledger" / "patch_instruction_sheets" / "causyl" / "active"
    if not base.exists():
        return 0
    return sum(1 for path in base.rglob("meta.json") if path.is_file())


def _load_codex_bootstrap_text(repo_root: Path) -> str:
    parts: List[str] = []
    for relative in _CODEX_BOOTSTRAP_FILES:
        path = (repo_root / relative).resolve()
        if not path.exists():
            continue
        parts.append(f"[{relative}]\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts).strip()


def _recent_lab_session_context(repo_root: Path) -> str:
    session_id = str(os.environ.get("EIEL_LAB_SESSION_ID") or os.environ.get("EIEL_LABSH_SESSION_ID") or "").strip()
    if not session_id:
        return ""
    try:
        return recent_session_context(repo_root, session_id, limit_messages=6, max_chars=2400)
    except Exception:
        return ""


def _infer_recent_target_uid_from_lab_context(repo_root: Path) -> str:
    text = _recent_lab_session_context(repo_root)
    if not text:
        return ""
    import re

    matches = re.findall(r"\b((?:IS|PIS)-\d{8}-\d{6}Z-[A-Z0-9]{4})\b", text, flags=re.IGNORECASE)
    if matches:
        return str(matches[-1]).upper()
    return ""


def _infer_recent_target_uid_from_active_ledger_sessions(repo_root: Path) -> str:
    base = (repo_root / "governance" / "ledger" / "patch_instruction_sheets").resolve()
    if not base.exists():
        return ""
    import re

    candidates: List[tuple[float, str, str]] = []
    for path in base.rglob("session__*.jsonl"):
        try:
            if "active" not in path.parts:
                continue
            match = re.search(r"\b(PIS-\d{8}-\d{6}Z-[A-Z0-9]{4})__", path.as_posix(), flags=re.IGNORECASE)
            if not match:
                continue
            stat = path.stat()
            candidates.append((float(stat.st_mtime), path.as_posix(), str(match.group(1) or "").upper()))
        except Exception:
            continue
    if not candidates:
        return ""
    candidates.sort()
    return candidates[-1][2]


def _extract_validation_commands_from_builder_report(content: str) -> List[str]:
    lines = str(content or "").splitlines()
    commands: List[str] = []
    in_section = False
    for raw_line in lines:
        line = raw_line.rstrip()
        stripped = line.strip()
        if not in_section:
            if stripped.lower() == "validation commands run:":
                in_section = True
            continue
        if not stripped:
            if commands:
                break
            continue
        if not stripped.startswith("- "):
            if commands:
                break
            continue
        command = stripped[2:].strip()
        if "=>" in command:
            command = command.split("=>", 1)[0].strip()
        if command.startswith("`") and command.endswith("`") and len(command) >= 2:
            command = command[1:-1].strip()
        if command:
            commands.append(command)
    deduped: List[str] = []
    for command in commands:
        if command not in deduped:
            deduped.append(command)
    return deduped


def _infer_validation_commands_from_target_builder_session(repo_root: Path, target_uid: str) -> List[str]:
    uid = str(target_uid or "").strip().upper()
    if not uid:
        return []
    base = (repo_root / "governance" / "ledger" / "patch_instruction_sheets").resolve()
    if not base.exists():
        return []
    candidates: List[tuple[float, List[str]]] = []
    for path in base.rglob("session__builder.jsonl"):
        try:
            if uid not in path.as_posix().upper():
                continue
            stat = path.stat()
            lines = path.read_text(encoding="utf-8").splitlines()
        except Exception:
            continue
        latest_commands: List[str] = []
        for line in lines:
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if str(payload.get("sender") or "").strip() != "assistant":
                continue
            commands = _extract_validation_commands_from_builder_report(str(payload.get("content") or ""))
            if commands:
                latest_commands = commands
        if latest_commands:
            candidates.append((float(stat.st_mtime), latest_commands))
    if not candidates:
        return []
    candidates.sort()
    return candidates[-1][1]


def _unsupported_request_fallback_event(
    *,
    payload: Mapping[str, Any],
    selection: Mapping[str, Any],
    codex_ok: bool,
) -> Dict[str, Any]:
    authority_scope = str(payload.get("authority_scope") or "").strip()
    request_text = _normalize_request_text(payload)
    failure_class = str(selection.get("unsupported_reason") or "unsupported_request").strip() or "unsupported_request"
    commparse_related = failure_class in {
        "force_codex_override",
        "unsupported_request",
        "no_supported_phase1_rule_match",
        "semantic_implementation_review_requires_codex",
        "multi_step_campaign_planning_requires_codex",
    } or "commparse" in failure_class
    return {
        "action_family": "operator_surface_routing",
        "intent": request_text,
        "failure_class": failure_class,
        "deterministic_steps_attempted": ["request_decomposition_v1", "commparse_v1", "operator_router_v1"],
        "fallback_reason": "unsupported_request_surface_gap",
        "commparse_related_gap": commparse_related,
        "selection_origin": str(selection.get("selection_origin") or "").strip(),
        "pre_escalation_checklist": {
            "deterministic_path_attempted": True,
            "recovery_attempted": False,
            "evidence_gathered": True,
        },
        "post_fallback_outcome": "success_after_codex_fallback" if codex_ok else "failed_unknown_class",
        "solution_path_signature": f"operator_surface::{request_text.lower().strip()}",
        "bounded_authority_safe": authority_scope not in {"administrative", "high_impact"},
        "validator_path_clear": True,
        "expected_future_codex_cost": 3.0,
        "operator_friction_cost": 2.0,
        "estimated_build_cost": 2.0,
        "estimated_maintenance_cost": 0.5,
    }


def _build_unsupported_codex_message(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> str:
    request_text = _normalize_request_text(payload)
    authority_scope = str(payload.get("authority_scope") or "").strip() or "read_only"
    patch_path = str(payload.get("patch_path") or "").strip()
    zip_path = str(payload.get("zip_path") or "").strip()
    lines = [
        "You are the bounded fallback executor for the plane.",
        "The operator asked for a request the deterministic front door does not yet support.",
        "Handle the request directly using the existing repo substrate, then add the narrowest deterministic support to the plane so the same request family routes in the future.",
        "Do not invent a parallel interface.",
        "Prefer existing scripts, CLIs, schedulers, ledger tools, and validation paths.",
        "If the repo does not already have a suitable deterministic entrypoint, it is acceptable to build one new narrow script or wrapper to support this request family in the future.",
        "When you add deterministic support, also update commparse so the request family is parsed correctly in the future.",
        "That can include the narrowest needed changes to verb aliases, heuristic routing, command-parse program mapping, and focused tests.",
        "Keep changes narrow and auditable.",
        "",
        f"Operator request: {request_text}",
        f"Authority scope: {authority_scope}",
    ]
    if patch_path:
        lines.append(f"Patch path: {patch_path}")
    if zip_path:
        lines.append(f"Zip path: {zip_path}")
    lines.extend(
        [
            "",
            "Required output:",
            "- perform the request if safe and supported by the repo substrate",
            "- if code changes are needed, implement them directly",
            "- validate with the narrowest honest checks",
            "- summarize what was changed, what was run, and any remaining boundary",
        ]
    )
    recent_context = _recent_lab_session_context(_repo_root(repo_root))
    if recent_context:
        lines.extend(
            [
                "",
                "Recent operator conversation context (secondary to runtime truth):",
                recent_context,
            ]
        )
    return "\n".join(lines).strip()


def _build_known_failure_codex_message(
    payload: Mapping[str, Any],
    *,
    action_id: str,
    failure_reason: str,
    repo_root: Optional[Path] = None,
) -> str:
    request_text = _normalize_request_text(payload)
    authority_scope = str(payload.get("authority_scope") or "").strip() or "read_only"
    lines = [
        "You are the bounded fallback executor for the plane.",
        "A deterministic action was selected and then failed during execution.",
        "Handle the original operator request directly using the existing repo substrate, staying within the same boundary.",
        "If you can also add the narrowest deterministic improvement so this failure class routes or executes cleanly next time, do that.",
        "Do not invent a parallel interface.",
        "If the repo does not already have a suitable deterministic entrypoint, it is acceptable to build one new narrow script or wrapper to support this request family in the future.",
        "If the failure came from bad routing or weak parsing, update commparse as part of the fix so the request family is selected correctly next time.",
        "That can include the narrowest needed changes to verb aliases, heuristic routing, command-parse program mapping, and focused tests.",
        "",
        f"Operator request: {request_text}",
        f"Selected action: {action_id}",
        f"Failure reason: {failure_reason}",
        f"Authority scope: {authority_scope}",
        "",
        "Required output:",
        "- perform the request if safe and supported by the repo substrate",
        "- if code changes are needed, implement them directly",
        "- validate with the narrowest honest checks",
        "- summarize what was changed, what was run, and any remaining boundary",
    ]
    recent_context = _recent_lab_session_context(_repo_root(repo_root))
    if recent_context:
        lines.extend(
            [
                "",
                "Recent operator conversation context (secondary to runtime truth):",
                recent_context,
            ]
        )
    return "\n".join(lines).strip()


def _announce_codex_fallback(payload: Mapping[str, Any], *, action_id: str, trigger_reason: str) -> None:
    if not bool(payload.get("stream_subprocess_output")):
        return
    request_text = str(_normalize_request_text(payload) or "").strip()
    line = f"[the-plane] Launching Codex fallback for {action_id}: {trigger_reason}"
    if request_text:
        line += f" | request={request_text}"
    if sys.stdout.isatty() and os.environ.get("NO_COLOR") is None:
        line = f"\033[1;35m{line}\033[0m"
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _execute_unsupported_request_via_codex(
    payload: Mapping[str, Any],
    *,
    selection: Mapping[str, Any],
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    message = _build_unsupported_codex_message(payload, repo_root=root)
    bootstrap_text = _load_codex_bootstrap_text(root)
    _announce_codex_fallback(
        payload,
        action_id=str(selection.get("action_id") or "unsupported_request").strip() or "unsupported_request",
        trigger_reason=str(selection.get("unsupported_reason") or "unsupported_request_surface_gap").strip(),
    )
    try:
        turn = run_codex_oneshot(
            worker_id=f"plane_unsupported::{str(payload.get('task_id') or 'adhoc').strip() or 'adhoc'}",
            binding_type="operator_request",
            binding_id=str(_normalize_request_text(payload) or "unsupported_request").strip(),
            role="builder",
            message=message,
            bootstrap_text=bootstrap_text,
            cwd=root,
            timeout_s=900,
        )
    except Exception as exc:
        turn = {
            "ok": False,
            "exit_code": 1,
            "content": "",
            "stderr": str(exc).strip() or "codex_fallback_failed",
            "stdout": "",
            "usage": {},
        }
    codex_ok = bool(turn.get("ok"))
    summary = str(turn.get("content") or turn.get("stderr") or "").strip()
    fallback_event = _unsupported_request_fallback_event(payload=payload, selection=selection, codex_ok=codex_ok)
    learning = _learning_from_fallback_events({"fallback_events": [fallback_event]}, repo_root=root)
    state = "PASS" if codex_ok else "REVIEW_REQUIRED"
    next_label = "Request handled via Codex fallback" if codex_ok else "Manual review required"
    return {
        "schema_version": "plane_operator_result_v1",
        "surface_name": "the plane",
        "request_text": str(_normalize_request_text(payload) or "").strip(),
        "mode": "execute",
        "classification": {
            "schema_version": "plane_request_classification_v1",
            "surface_name": "the plane",
            "request_text": str(_normalize_request_text(payload) or "").strip(),
            "decomposition": build_request_decomposition_v1(
                {
                    "request_text": _normalize_request_text(payload),
                    "artifact_presence_flags": _artifact_presence_flags(payload),
                    "target_identifiers": _target_identifiers(payload),
                    "authority_class": str(payload.get("authority_class") or payload.get("authority_scope") or "").strip(),
                    "operator_verb": str(payload.get("operator_verb") or "").strip(),
                },
                repo_root=root,
            ),
            "selection": dict(selection),
        },
        "selection": dict(selection),
        "supported": codex_ok,
        "execution": {
            "ok": codex_ok,
            "stdout": str(turn.get("content") or ""),
            "stderr": str(turn.get("stderr") or ""),
            "exit_code": int(turn.get("exit_code") or 1),
            "usage": dict(turn.get("usage") or {}),
        },
        "operator_surface": {
            "executive_surface": {
                "state": state,
                "primary_next_action": {"label": next_label},
                "confidence": "Medium" if codex_ok else "Low",
                "short_reason": summary or "Codex fallback executed for unsupported request.",
            },
            "status_class": "pass" if codex_ok else "blocked",
            "status_summary": summary or "Codex fallback executed for unsupported request.",
            "primary_explanation": {
                "primary_reason": "unsupported_request_surface_gap",
                "proof_state": "fallback_used" if codex_ok else "uncertain",
                "evidence_references": [],
            },
            "proof_uncertainty": {
                "proof_gap_present": not codex_ok,
                "unresolved_items": [] if codex_ok else ["codex_fallback_failed"],
                "stale_evidence_present": False,
                "contradiction_present": False,
                "confidence_bucket": "medium" if codex_ok else "low",
                "required_next_proof_steps": [] if codex_ok else ["manual_review"],
            },
            "recommended_actions": [] if codex_ok else [{"label": "Manual review required", "reason": "Codex fallback did not complete successfully."}],
            "manual_review_required": not codex_ok,
            "codex_transparency": {
                "codex_used": True,
                "usage_reason": "unsupported_request_surface_gap",
                "family": "operator_surface_routing",
                "recurrence_class": "first_observation",
                "economic_recommendation_class": str(dict(learning.get("phase4_absorption") or {}).get("economic_decision_output", {}).get("decision_class") or "keep_codex_fallback").strip(),
                "eligible": True,
                "failure_class": str(selection.get("unsupported_reason") or "unsupported_request").strip(),
            },
            "artifact_references": [],
        },
        "summary": summary or "Codex fallback executed for unsupported request.",
        "terminal_class": "success_after_codex_fallback" if codex_ok else "failed_unknown_class",
        "qualifiers": ["improvement_task_created"] if dict(learning.get("improvement_opportunity") or {}).get("decision") == "promote_opportunity" else [],
        "codex_fallback_request": {
            "eligible": True,
            "action_family": "operator_surface_routing",
            "intent": _normalize_request_text(payload),
            "failure_class": str(selection.get("unsupported_reason") or "unsupported_request").strip(),
            "trigger_reason": "unsupported_request_surface_gap",
        },
        "learning_artifacts": learning,
        "catalog": build_operator_catalog_v1(repo_root=root, contract_repo_root=root),
    }


def _execute_known_failure_via_codex(
    payload: Mapping[str, Any],
    *,
    selection: Mapping[str, Any],
    action_id: str,
    failure_reason: str,
    repo_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    bootstrap_text = _load_codex_bootstrap_text(root)
    _announce_codex_fallback(payload, action_id=action_id, trigger_reason=str(failure_reason or "deterministic_execution_failed").strip())
    try:
        turn = run_codex_oneshot(
            worker_id=f"plane_failure::{str(payload.get('task_id') or action_id).strip() or action_id}",
            binding_type="operator_request",
            binding_id=str(_normalize_request_text(payload) or action_id).strip(),
            role="builder",
            message=_build_known_failure_codex_message(payload, action_id=action_id, failure_reason=failure_reason, repo_root=root),
            bootstrap_text=bootstrap_text,
            cwd=root,
            timeout_s=900,
        )
    except Exception as exc:
        turn = {
            "ok": False,
            "exit_code": 1,
            "content": "",
            "stderr": str(exc).strip() or "codex_fallback_failed",
            "stdout": "",
            "usage": {},
        }
    codex_ok = bool(turn.get("ok"))
    fallback_event = {
        "action_family": str(_FAMILY_MAP.get(action_id) or "operator_execution").strip(),
        "intent": _normalize_request_text(payload),
        "failure_class": str(failure_reason or "deterministic_execution_failed").strip(),
        "deterministic_steps_attempted": [action_id],
        "fallback_reason": "deterministic_execution_failed",
        "commparse_related_gap": bool(str(selection.get("selection_origin") or "").strip().startswith("commparse")) and "route" in str(failure_reason or "").lower(),
        "selection_origin": str(selection.get("selection_origin") or "").strip(),
        "pre_escalation_checklist": {
            "deterministic_path_attempted": True,
            "recovery_attempted": False,
            "evidence_gathered": True,
        },
        "post_fallback_outcome": "success_after_codex_fallback" if codex_ok else "failed_unknown_class",
        "solution_path_signature": f"{action_id}::{str(failure_reason or 'failed').strip().lower()}",
        "bounded_authority_safe": str(payload.get("authority_scope") or "").strip() not in {"administrative", "high_impact"},
        "validator_path_clear": True,
        "expected_future_codex_cost": 3.0,
        "operator_friction_cost": 2.0,
        "estimated_build_cost": 1.5,
        "estimated_maintenance_cost": 0.5,
    }
    learning = _learning_from_fallback_events({"fallback_events": [fallback_event]}, repo_root=root)
    summary = str(turn.get("content") or turn.get("stderr") or "").strip()
    return {
        "schema_version": "plane_operator_result_v1",
        "surface_name": "the plane",
        "request_text": str(_normalize_request_text(payload) or "").strip(),
        "mode": "execute",
        "classification": classify_operator_request_v1(payload, repo_root=root, contract_repo_root=root),
        "selection": dict(selection),
        "supported": codex_ok,
        "execution": {
            "ok": codex_ok,
            "stdout": str(turn.get("content") or ""),
            "stderr": str(turn.get("stderr") or ""),
            "exit_code": int(turn.get("exit_code") or 1),
            "usage": dict(turn.get("usage") or {}),
        },
        "operator_surface": {
            "executive_surface": {
                "state": "PASS" if codex_ok else "REVIEW_REQUIRED",
                "primary_next_action": {"label": "Request handled via Codex fallback" if codex_ok else "Manual review required"},
                "confidence": "Medium" if codex_ok else "Low",
                "short_reason": summary or failure_reason,
            },
            "status_class": "pass" if codex_ok else "blocked",
            "status_summary": summary or failure_reason,
            "primary_explanation": {
                "primary_reason": str(failure_reason or "deterministic_execution_failed").strip(),
                "proof_state": "fallback_used" if codex_ok else "uncertain",
                "evidence_references": [],
            },
            "proof_uncertainty": {
                "proof_gap_present": not codex_ok,
                "unresolved_items": [] if codex_ok else ["codex_fallback_failed"],
                "stale_evidence_present": False,
                "contradiction_present": False,
                "confidence_bucket": "medium" if codex_ok else "low",
                "required_next_proof_steps": [] if codex_ok else ["manual_review"],
            },
            "recommended_actions": [] if codex_ok else [{"label": "Manual review required", "reason": "Codex fallback did not complete successfully."}],
            "manual_review_required": not codex_ok,
            "codex_transparency": {
                "codex_used": True,
                "usage_reason": "deterministic_execution_failed",
                "family": str(_FAMILY_MAP.get(action_id) or "").strip(),
                "recurrence_class": "first_observation",
                "economic_recommendation_class": str(dict(learning.get("phase4_absorption") or {}).get("economic_decision_output", {}).get("decision_class") or "keep_codex_fallback").strip(),
                "eligible": True,
                "failure_class": str(failure_reason or "deterministic_execution_failed").strip(),
            },
            "artifact_references": [],
        },
        "summary": summary or failure_reason,
        "terminal_class": "success_after_codex_fallback" if codex_ok else "failed_unknown_class",
        "qualifiers": ["improvement_task_created"] if dict(learning.get("improvement_opportunity") or {}).get("decision") == "promote_opportunity" else [],
        "codex_fallback_request": {
            "eligible": True,
            "action_id": action_id,
            "action_family": str(_FAMILY_MAP.get(action_id) or "").strip(),
            "intent": _normalize_request_text(payload),
            "failure_class": str(failure_reason or "deterministic_execution_failed").strip(),
            "trigger_reason": "deterministic_execution_failed",
        },
        "learning_artifacts": learning,
        "catalog": build_operator_catalog_v1(repo_root=root, contract_repo_root=root),
    }


def _execute_repo_archive_snapshot_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    request_text = _normalize_request_text(request).lower().strip()
    command = ["bash", "infra/bin/archive"]
    archive_mode = "share"
    if "weekly" in request_text:
        command.append("--weekly")
        archive_mode = "weekly"
    elif "debug" in request_text:
        command.append("--debug")
        archive_mode = "debug"
    cp = _run_command_capture(
        command=command,
        cwd=root,
        stream_output=bool(request.get("stream_subprocess_output")),
    )
    ok = cp.returncode == 0
    reason = "archive_snapshot_completed" if ok else str(cp.stderr or cp.stdout or "archive failed").strip()
    validation_command = " ".join(command)
    return {
        "ok": ok,
        "command": command,
        "stdout": str(cp.stdout or ""),
        "stderr": str(cp.stderr or ""),
        "exit_code": int(cp.returncode),
        "archive_mode": archive_mode,
        "outcome": {
            "outcome": "success_deterministic" if ok else "blocked_requires_review",
            "verifier_verdict": "PASS" if ok else "",
            "proof_gaps": [] if ok else [reason],
            "evidence_artifacts": [],
            "validation_commands_executed": [validation_command],
        },
        "terminal_reason": reason,
        "pre_escalation_checklist": {
            "deterministic_path_attempted": True,
            "recovery_attempted": False,
            "evidence_gathered": True,
        },
    }


def _execute_repo_import_pis_zip_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    zip_path = str(request.get("zip_path") or "").strip()
    if not zip_path:
        raise ValueError("repo_import_pis_zip_v1 requires zip_path")
    command = ["python", "-m", "tools.bin.pis_import_zip", zip_path]
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    cp = _run_command_capture(
        command=command,
        cwd=root,
        stream_output=bool(request.get("stream_subprocess_output")),
    )
    ok = cp.returncode == 0
    reason = "pis_zip_import_completed" if ok else str(cp.stderr or cp.stdout or "pis zip import failed").strip()
    return {
        "ok": ok,
        "command": command,
        "stdout": str(cp.stdout or ""),
        "stderr": str(cp.stderr or ""),
        "exit_code": int(cp.returncode),
        "zip_path": zip_path,
        "outcome": {
            "outcome": "success_deterministic" if ok else "blocked_requires_review",
            "verifier_verdict": "PASS" if ok else "",
            "proof_gaps": [] if ok else [reason],
            "evidence_artifacts": [],
            "validation_commands_executed": [" ".join(command)],
        },
        "terminal_reason": reason,
        "pre_escalation_checklist": {
            "deterministic_path_attempted": True,
            "recovery_attempted": False,
            "evidence_gathered": True,
        },
    }


def _execute_repo_commit_tag_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    request_text = _normalize_request_text(request)
    subject_override = ""
    lowered = request_text.lower().strip()
    if lowered.startswith("commit "):
        subject_override = request_text[7:].strip()
    elif lowered in {"commit", "lab commit"}:
        subject_override = ""
    command = ["bash", "tools/git/git-commit-tag.sh"]
    if subject_override:
        command.append(subject_override)
    cp = _run_command_capture(
        command=command,
        cwd=root,
        stream_output=bool(request.get("stream_subprocess_output")),
    )
    ok = cp.returncode == 0
    lines = [line.strip() for line in str(cp.stdout or "").splitlines() if str(line).strip()]
    baseline = ""
    for line in lines:
        if "using baseline-" in line:
            baseline = line.split("using ", 1)[-1].strip()
            break
        if "committed and tagged baseline-" in line:
            baseline = line.split("committed and tagged ", 1)[-1].strip()
            break
    terminal = "success_deterministic" if ok else "blocked_requires_review"
    reason = "journal_commit_and_tag_completed" if ok else str(cp.stderr or cp.stdout or "git commit/tag failed").strip()
    return {
        "ok": ok,
        "command": command,
        "stdout": str(cp.stdout or ""),
        "stderr": str(cp.stderr or ""),
        "exit_code": int(cp.returncode),
        "commit_baseline_id": baseline,
        "outcome": {
            "outcome": terminal,
            "verifier_verdict": "PASS" if ok else "",
            "proof_gaps": [] if ok else [reason],
            "evidence_artifacts": [],
            "validation_commands_executed": ["bash tools/git/git-commit-tag.sh"],
        },
        "terminal_reason": reason,
        "pre_escalation_checklist": {
            "deterministic_path_attempted": True,
            "recovery_attempted": False,
            "evidence_gathered": True,
        },
    }


def _execute_repo_show_commit_journal_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["python", "-m", "tools.git.support.commit_journal", "view"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="commit_journal_rendered",
        reason_error="commit journal view failed",
    )


def _execute_repo_show_recent_commits_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    count = str(request.get("recent_commit_limit") or "10").strip()
    command = ["git", "log", "--oneline", "-n", count]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="recent_commits_rendered",
        reason_error="git log failed",
        extra={"recent_commit_limit": int(count)},
    )


def _execute_repo_import_intent_sheet_zip_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    zip_path = str(request.get("zip_path") or "").strip()
    if not zip_path:
        raise ValueError("repo_import_intent_sheet_zip_v1 requires zip_path")
    command = ["python", "-m", "tools.bin.intent_sheet_import_zip", zip_path]
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="intent_sheet_zip_import_completed",
        reason_error="intent sheet zip import failed",
        extra={"zip_path": zip_path},
    )


def _execute_service_recover_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    stream_output = bool(request.get("stream_subprocess_output"))
    request_text = _normalize_request_text(request).lower().strip()
    explicit_restart = any(term in request_text for term in ("restart server", "server restart", "restart runtime", "runtime restart"))
    if explicit_restart:
        restart_command = ["bash", "infra/user/server-restart"]
        restart_cp = _run_command_capture(command=restart_command, cwd=root, stream_output=stream_output)
        restart_stdout = str(restart_cp.stdout or "")
        restart_stderr = str(restart_cp.stderr or "")
        if restart_cp.returncode != 0 and "readiness timeout after" in f"{restart_stdout}\n{restart_stderr}":
            status_command = ["bash", "infra/user/server-status"]
            status_cp = _run_command_capture(command=status_command, cwd=root, stream_output=stream_output)
            status_stdout = str(status_cp.stdout or "")
            process_alive = "[server_status] starting pid=" in status_stdout or "[server_status] ready pid=" in status_stdout
            if process_alive:
                result = _command_result(
                    cp=restart_cp,
                    command=restart_command,
                    reason_ok="service_restart_completed",
                    reason_error="service restart readiness still pending after timeout",
                    extra={
                        "restart_requested": True,
                        "recovery_attempted": True,
                        "fallback_allowed": False,
                        "status_probe_stdout": status_stdout,
                        "status_probe_stderr": str(status_cp.stderr or ""),
                        "terminal_reason": "service restart completed but readiness is still pending after timeout",
                        "ok": False,
                    },
                )
                result["outcome"]["outcome"] = "recovery_pending_validation"
                result["outcome"]["proof_gaps"] = ["service restart completed but readiness is still pending after timeout"]
                result["outcome"]["validation_commands_executed"] = [" ".join(restart_command), " ".join(status_command)]
                result["pre_escalation_checklist"]["recovery_attempted"] = True
                return result
        result = _command_result(
            cp=restart_cp,
            command=restart_command,
            reason_ok="service_restart_completed",
            reason_error="service restart failed",
            extra={"restart_requested": True, "recovery_attempted": True},
        )
        result["pre_escalation_checklist"]["recovery_attempted"] = True
        result["outcome"]["validation_commands_executed"] = [" ".join(restart_command)]
        return result
    status_command = ["bash", "infra/user/server-status"]
    status_cp = _run_command_capture(command=status_command, cwd=root, stream_output=stream_output)
    status_stdout = str(status_cp.stdout or "")
    if status_cp.returncode == 0 and "[server_status] ready" in status_stdout:
        return _command_result(
            cp=status_cp,
            command=status_command,
            reason_ok="service_already_ready",
            reason_error="server status failed",
            extra={"recovery_attempted": False, "restart_requested": False},
        )
    start_command = ["bash", "infra/user/server-start"]
    start_cp = _run_command_capture(command=start_command, cwd=root, stream_output=stream_output)
    result = _command_result(
        cp=start_cp,
        command=start_command,
        reason_ok="service_recovery_completed",
        reason_error="service recovery failed",
        extra={
            "status_probe_stdout": status_stdout,
            "status_probe_stderr": str(status_cp.stderr or ""),
            "recovery_attempted": True,
            "restart_requested": False,
        },
    )
    result["pre_escalation_checklist"]["recovery_attempted"] = True
    result["outcome"]["validation_commands_executed"] = [" ".join(status_command), " ".join(start_command)]
    return result


def _execute_server_status_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/server-status"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="server_status_completed",
        reason_error="server status failed",
    )


def _execute_server_start_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/server-start"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="server_start_completed",
        reason_error="server start failed",
    )


def _execute_server_stop_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/server-stop"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="server_stop_completed",
        reason_error="server stop failed",
    )


def _execute_server_watch_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/server-watch", "--interval", "2", "--pipeline-limit", "40"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="server_watch_started",
        reason_error="server watch failed",
    )


def _execute_server_watchdog_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/server-watchdog"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="server_watchdog_started",
        reason_error="server watchdog failed",
    )


def _execute_implementation_validate_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    resolved_target = str(request.get("target_uid") or request.get("target_pis_uid") or "").strip()
    target_source = "explicit" if resolved_target else ""
    if not resolved_target:
        resolved_target = _infer_recent_target_uid_from_lab_context(root)
        if resolved_target:
            target_source = "recent_lab_session_context"
    if not resolved_target:
        resolved_target = _infer_recent_target_uid_from_active_ledger_sessions(root)
        if resolved_target:
            target_source = "active_ledger_session_artifact"
    if not resolved_target:
        return {
            "ok": False,
            "resolved_target_uid": "",
            "resolved_target_source": "",
            "terminal_reason": "missing_target_uid_for_implementation_validation",
            "pre_escalation_checklist": {
                "deterministic_path_attempted": True,
                "recovery_attempted": False,
                "evidence_gathered": True,
            },
            "outcome": {
                "outcome": "blocked_requires_review",
                "verifier_verdict": "",
                "proof_gaps": ["missing_target_uid"],
                "evidence_artifacts": [],
                "validation_commands_executed": list(request.get("validation_commands") or []),
            },
        }
    reconcile_request = dict(request)
    reconcile_request.setdefault("target_uid", resolved_target)
    if not list(reconcile_request.get("validation_commands") or []):
        inferred_commands = _infer_validation_commands_from_target_builder_session(root, resolved_target)
        if inferred_commands:
            reconcile_request["validation_commands"] = inferred_commands
            reconcile_request["validation_commands_executed"] = inferred_commands
    result = _execute_action("status_reconcile_v1", reconcile_request, repo_root=root)
    result = dict(result)
    result["resolved_target_uid"] = resolved_target
    result["resolved_target_source"] = target_source
    return result


def _execute_safe_maintenance_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["python", "infra/bin/scheduler_watchdog.py", "--mode", "audit"]
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="safe_maintenance_audit_completed",
        reason_error="safe maintenance audit failed",
    )


def _execute_scheduler_inspect_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/scheduler-health"]
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="scheduler_health_check_completed",
        reason_error="scheduler health check failed",
    )


def _execute_scheduler_recover_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["python", "infra/bin/scheduler_watchdog.py", "--mode", "selfheal"]
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    result = _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="scheduler_recovery_completed",
        reason_error="scheduler recovery failed",
    )
    result["pre_escalation_checklist"]["recovery_attempted"] = True
    return result


def _execute_scheduler_disable_all_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/scheduler-disable-all"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="scheduler_disable_all_completed",
        reason_error="scheduler disable all failed",
    )


def _execute_scheduler_enable_all_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/scheduler-enable-all"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="scheduler_enable_all_completed",
        reason_error="scheduler enable all failed",
    )


def _execute_scheduler_watch_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/scheduler-watch", "--interval", "2", "--pipeline-limit", "40"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="scheduler_watch_started",
        reason_error="scheduler watch failed",
    )


def _execute_scheduler_pause_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/scheduler-pause"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="scheduler_pause_completed",
        reason_error="scheduler pause failed",
    )


def _execute_scheduler_resume_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["bash", "infra/user/scheduler-resume"]
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="scheduler_resume_completed",
        reason_error="scheduler resume failed",
    )


def _execute_implementation_prepare_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    limit = _requested_parallel_safe_limit(request, default=5 if not _wants_parallel_safe_only(request) else 2)
    command = ["python", "-m", "tools.bin.intent_sheet_next", "--limit", str(limit)]
    if _wants_pairwise_parallel_safe_set(request):
        command.append("--set-safe-only")
    if _wants_parallel_safe_only(request):
        command.append("--safe-only")
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    cp = _run_command_capture(command=command, cwd=root, stream_output=bool(request.get("stream_subprocess_output")))
    active_items = _ledger_active_item_count(root)
    return _command_result(
        cp=cp,
        command=command,
        reason_ok="implementation_preparation_summary_completed",
        reason_error="implementation preparation summary failed",
        extra={
            "active_patch_instruction_count": active_items,
            "requested_limit": int(limit),
            "safe_only": bool(_wants_parallel_safe_only(request)),
            "set_safe_only": bool(_wants_pairwise_parallel_safe_set(request)),
        },
    )


def _execute_implementation_prepare_pis_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["python", "-m", "tools.bin.pis_next", "--limit", "5"]
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    cp = _run_command_capture(command=command, cwd=root, stream_output=bool(request.get("stream_subprocess_output")))
    active_items = _ledger_active_item_count(root)
    return _command_result(
        cp=cp,
        command=command,
        reason_ok="implementation_preparation_summary_completed",
        reason_error="implementation preparation summary failed",
        extra={"active_patch_instruction_count": active_items},
    )


def _execute_implement_recursive_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    uid = str(request.get("target_uid") or request.get("target_pis_uid") or "").strip().upper()
    if not uid:
        raise ValueError("implement_recursive_v1 requires target_uid")
    command = ["python", "-m", "tools.bin.implement_recursive", "--uid", uid]
    if bool(request.get("plan_only")):
        command.append("--plan-only")
    if bool(request.get("decompose_only")):
        command.append("--decompose-only")
    if str(request.get("max_children") or "").strip():
        command.extend(["--max-children", str(request.get("max_children"))])
    if str(request.get("parallel") or "").strip():
        command.extend(["--parallel", str(request.get("parallel"))])
    if str(request.get("proof_profile") or "").strip():
        command.extend(["--proof-profile", str(request.get("proof_profile"))])
    if str(request.get("close_mode") or "").strip():
        command.extend(["--close", str(request.get("close_mode"))])
    if "start_now" in request:
        command.extend(["--start-now", "true" if bool(request.get("start_now")) else "false"])
    return _run_shell_action(
        command=command,
        repo_root=root,
        stream_output=bool(request.get("stream_subprocess_output")),
        reason_ok="implement_recursive_started",
        reason_error="recursive implementation dispatch failed",
    )


def _execute_backlog_inspect_v1(request: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    root = _repo_root(repo_root)
    command = ["python", "-m", "tools.bin.intent_sheet_next", "--limit", "10"]
    if not bool(request.get("stream_subprocess_output")):
        command.append("--json")
    cp = _run_command_capture(command=command, cwd=root, stream_output=bool(request.get("stream_subprocess_output")))
    active_items = _ledger_active_item_count(root)
    return _command_result(
        cp=cp,
        command=command,
        reason_ok="backlog_inspection_completed",
        reason_error="backlog inspection failed",
        extra={"active_patch_instruction_count": active_items},
    )


_DIRECT_ACTIONS["repo_commit_tag_v1"] = _execute_repo_commit_tag_v1
_DIRECT_ACTIONS["repo_show_commit_journal_v1"] = _execute_repo_show_commit_journal_v1
_DIRECT_ACTIONS["repo_show_recent_commits_v1"] = _execute_repo_show_recent_commits_v1
_DIRECT_ACTIONS["repo_import_pis_zip_v1"] = _execute_repo_import_pis_zip_v1
_DIRECT_ACTIONS["repo_import_intent_sheet_zip_v1"] = _execute_repo_import_intent_sheet_zip_v1
_DIRECT_ACTIONS["repo_archive_snapshot_v1"] = _execute_repo_archive_snapshot_v1
_DIRECT_ACTIONS["server_status_v1"] = _execute_server_status_v1
_DIRECT_ACTIONS["server_start_v1"] = _execute_server_start_v1
_DIRECT_ACTIONS["server_stop_v1"] = _execute_server_stop_v1
_DIRECT_ACTIONS["server_watch_v1"] = _execute_server_watch_v1
_DIRECT_ACTIONS["server_watchdog_v1"] = _execute_server_watchdog_v1
_DIRECT_ACTIONS["service_recover_v1"] = _execute_service_recover_v1
_DIRECT_ACTIONS["safe_maintenance_v1"] = _execute_safe_maintenance_v1
_DIRECT_ACTIONS["implementation_prepare_v1"] = _execute_implementation_prepare_v1
_DIRECT_ACTIONS["implementation_prepare_pis_v1"] = _execute_implementation_prepare_pis_v1
_DIRECT_ACTIONS["implement_recursive_v1"] = _execute_implement_recursive_v1
_DIRECT_ACTIONS["implementation_validate_v1"] = _execute_implementation_validate_v1
_DIRECT_ACTIONS["scheduler_inspect_v1"] = _execute_scheduler_inspect_v1
_DIRECT_ACTIONS["scheduler_recover_v1"] = _execute_scheduler_recover_v1
_DIRECT_ACTIONS["scheduler_disable_all_v1"] = _execute_scheduler_disable_all_v1
_DIRECT_ACTIONS["scheduler_enable_all_v1"] = _execute_scheduler_enable_all_v1
_DIRECT_ACTIONS["scheduler_watch_v1"] = _execute_scheduler_watch_v1
_DIRECT_ACTIONS["scheduler_pause_v1"] = _execute_scheduler_pause_v1
_DIRECT_ACTIONS["scheduler_resume_v1"] = _execute_scheduler_resume_v1
_DIRECT_ACTIONS["backlog_inspect_v1"] = _execute_backlog_inspect_v1


def _repo_root(repo_root: Optional[Path] = None) -> Path:
    if repo_root is not None:
        return Path(repo_root).resolve()
    return Path(__file__).resolve().parents[3]


def _contract_root(contract_repo_root: Optional[Path], repo_root: Optional[Path]) -> Path:
    if contract_repo_root is not None:
        return Path(contract_repo_root).resolve()
    return _repo_root(repo_root)


def _as_list(values: Sequence[str] | None) -> List[str]:
    out: List[str] = []
    for value in list(values or []):
        text = str(value or "").strip()
        if text:
            out.append(text)
    return out


def _infer_target_uid_from_request_text(request_text: str) -> str:
    text = str(request_text or "").strip()
    if not text:
        return ""
    match = re.search(r"\b((?:IS|PIS)-\d{8}-\d{6}Z-[A-Z0-9]{4})\b", text, flags=re.IGNORECASE)
    if match:
        return str(match.group(1) or "").upper()
    next_match = re.match(
        r"^\s*implement\s+(?:the\s+)?next\s+(pis|intent|intent sheet)\s*(?:please)?\s*[?.!]*\s*$",
        text,
        flags=re.IGNORECASE,
    )
    if not next_match:
        return ""
    kind = str(next_match.group(1) or "").strip().lower()
    if kind == "pis":
        return "next_pis"
    return "next_intent"


def _resolve_uid4_target_from_request_text(request_text: str, *, repo_root: Optional[Path] = None) -> str:
    text = str(request_text or "").strip()
    if not text:
        return ""
    uid4_match = re.search(r"\(([A-Za-z0-9]{4})\s+in\s+progress\)", text, flags=re.IGNORECASE)
    if not uid4_match:
        uid4_match = re.search(r"\b([A-Za-z0-9]{4})\b(?=\s+in\s+progress\b)", text, flags=re.IGNORECASE)
    if not uid4_match:
        return ""
    uid4 = str(uid4_match.group(1) or "").strip().upper()
    if not uid4:
        return ""
    try:
        rows = ChangeLedger(repo_root=_repo_root(repo_root)).list_items(limit=2000)
    except Exception:
        return ""
    pis_matches = [
        str(row.get("uid") or "").strip().upper()
        for row in rows
        if str(row.get("uid") or "").strip().upper().startswith("PIS-")
        and str(row.get("uid") or "").strip().upper().endswith(f"-{uid4}")
    ]
    pis_matches = sorted(set(match for match in pis_matches if match))
    if len(pis_matches) == 1:
        return pis_matches[0]
    is_matches = [
        str(row.get("uid") or "").strip().upper()
        for row in rows
        if str(row.get("uid") or "").strip().upper().startswith("IS-")
        and str(row.get("uid") or "").strip().upper().endswith(f"-{uid4}")
    ]
    is_matches = sorted(set(match for match in is_matches if match))
    if len(is_matches) == 1:
        return is_matches[0]
    return ""


def _resolved_target_uid(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> str:
    explicit = str(payload.get("target_uid") or payload.get("target_pis_uid") or "").strip()
    if explicit:
        return explicit.upper()
    request_text = _normalize_request_text(payload)
    inferred = _infer_target_uid_from_request_text(request_text).upper()
    if inferred:
        return inferred
    return _resolve_uid4_target_from_request_text(request_text, repo_root=repo_root).upper()


def _requested_parallel_safe_limit(payload: Mapping[str, Any], *, default: int = 2) -> int:
    for key in ("limit", "requested_limit", "safe_parallel_limit"):
        raw = str(payload.get(key) or "").strip()
        if raw.isdigit():
            return max(1, min(int(raw), 10))
    text = _normalize_request_text(payload).lower()
    match = re.search(r"\b(\d+)\b", text)
    if match:
        return max(1, min(int(match.group(1)), 10))
    word_map = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5}
    for word, value in word_map.items():
        if re.search(rf"\b{word}\b", text):
            return value
    return default


def _wants_parallel_safe_only(payload: Mapping[str, Any]) -> bool:
    if "safe_only" in payload:
        return bool(payload.get("safe_only"))
    text = _normalize_request_text(payload).lower()
    if "parallel" not in text:
        return False
    return "safe" in text or "safely" in text


def _wants_pairwise_parallel_safe_set(payload: Mapping[str, Any]) -> bool:
    if "set_safe_only" in payload:
        return bool(payload.get("set_safe_only"))
    text = _normalize_request_text(payload).lower()
    if "parallel" not in text:
        return False
    return (
        "themselves" in text
        or "among themselves" in text
        or "mutually safe" in text
        or "safe as a set" in text
    )


def _implemented_action_rows(*, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    rows: Dict[str, Dict[str, Any]] = {}
    for row in list(load_action_registry(repo_root=_contract_root(contract_repo_root, repo_root)).get("actions") or []):
        action_id = str(row.get("action_id") or "").strip()
        if action_id:
            rows[action_id] = dict(row)
    return rows


def build_operator_catalog_v1(*, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    action_rows = _implemented_action_rows(repo_root=repo_root, contract_repo_root=contract_repo_root)
    policy = build_operator_verb_surface_policy_v1({}, repo_root=_contract_root(contract_repo_root, repo_root))
    implemented_verbs: List[Dict[str, Any]] = []
    for action_id, row in action_rows.items():
        implemented_verbs.append(
            {
                "action_id": action_id,
                "operator_verb": str(row.get("operator_verb") or row.get("title") or action_id).strip(),
                "title": str(row.get("title") or action_id).strip(),
                "status": "implemented",
            }
        )
    supported_surface_verbs: List[Dict[str, Any]] = []
    for operator_verb, action_id in sorted(_VERB_ALIASES.items()):
        if action_id not in _DIRECT_ACTIONS:
            continue
        supported_surface_verbs.append(
            {
                "operator_verb": operator_verb,
                "action_id": action_id,
                "status": "implemented",
            }
        )
    implemented_names = {row["operator_verb"] for row in implemented_verbs} | {row["operator_verb"] for row in supported_surface_verbs}
    planned_verbs = [
        {"operator_verb": verb, "status": "planned"}
        for verb in list(policy.get("public_verbs") or [])
        if str(verb or "").strip() and str(verb or "").strip() not in implemented_names
    ]
    direct_actions = [
        {"action_id": action_id, "status": "implemented"}
        for action_id in sorted(_DIRECT_ACTIONS)
        if action_id not in action_rows
    ]
    return {
        "schema_version": "plane_operator_catalog_v1",
        "surface_name": str(policy.get("default_surface_name") or "the plane"),
        "implemented_verbs": implemented_verbs,
        "supported_surface_verbs": supported_surface_verbs,
        "planned_verbs": planned_verbs,
        "direct_actions": direct_actions,
    }


def _artifact_presence_flags(payload: Mapping[str, Any]) -> Dict[str, bool]:
    return {
        "patch_diff": bool(str(payload.get("patch_text") or "").strip() or str(payload.get("patch_path") or "").strip()),
        "pis_evidence": bool(str(payload.get("target_pis_uid") or "").strip() or str(payload.get("pis_markdown") or "").strip()),
        "job_spec": bool(str(payload.get("job_id") or "").strip() or bool(payload.get("job_spec"))),
        "job_receipts": bool(payload.get("receipts")),
    }


def _target_identifiers(payload: Mapping[str, Any]) -> Dict[str, str]:
    return {
        "target_pis_uid": str(payload.get("target_pis_uid") or "").strip(),
        "job_id": str(payload.get("job_id") or "").strip(),
    }


def _normalize_request_text(payload: Mapping[str, Any]) -> str:
    request_text = str(payload.get("request_text") or "").strip()
    if request_text:
        return request_text
    operator_verb = str(payload.get("operator_verb") or "").strip()
    if operator_verb:
        return operator_verb
    action_id = str(payload.get("action_id") or "").strip()
    return action_id


def _contains_any(text: str, terms: Sequence[str]) -> bool:
    lowered = str(text or "").lower()
    return any(str(term or "").lower() in lowered for term in terms if str(term or "").strip())


def _looks_like_chained_request(text: str) -> bool:
    lowered = str(text or "").lower()
    return " then " in lowered or " and then " in lowered


def _ollama_embed_is_online() -> bool:
    now = time.time()
    checked_at = float(_OLLAMA_PROBE_STATE.get("checked_at") or 0.0)
    if now - checked_at < _OLLAMA_PROBE_TTL_SECONDS:
        return bool(_OLLAMA_PROBE_STATE.get("online"))
    online = False
    tags_url = f"{_OLLAMA_EMBED_BASE_URL}/api/tags"
    try:
        with urllib.request.urlopen(tags_url, timeout=_OLLAMA_PROBE_TIMEOUT_SECONDS) as resp:
            online = bool(resp.read(64))
    except Exception:
        online = False
    _OLLAMA_PROBE_STATE["checked_at"] = now
    _OLLAMA_PROBE_STATE["online"] = online
    return online


def _ollama_embed_texts(texts: Sequence[str]) -> List[List[float]]:
    req = urllib.request.Request(
        f"{_OLLAMA_EMBED_BASE_URL}/api/embed",
        data=json.dumps({"model": _OLLAMA_EMBED_MODEL, "input": list(texts)}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=_OLLAMA_EMBED_TIMEOUT_SECONDS) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    vectors = payload.get("embeddings") if isinstance(payload.get("embeddings"), list) else payload.get("embedding")
    if not isinstance(vectors, list):
        raise ValueError("ollama embed returned no vectors")
    if vectors and not isinstance(vectors[0], list):
        vectors = [vectors]
    out: List[List[float]] = []
    for vec in list(vectors or []):
        row = [float(v) for v in list(vec or [])]
        norm = math.sqrt(sum(v * v for v in row)) or 1.0
        out.append([v / norm for v in row])
    if len(out) != len(texts):
        raise ValueError("ollama embed vector count mismatch")
    return out


def _cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    num = sum(float(x) * float(y) for x, y in zip(a, b))
    da = math.sqrt(sum(float(x) * float(x) for x in a))
    db = math.sqrt(sum(float(y) * float(y) for y in b))
    return num / (da * db) if da and db else 0.0


def _embedding_semantic_selection(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    request_text = str(_normalize_request_text(payload) or "").strip()
    if not request_text or not _ollama_embed_is_online():
        return {}
    all_texts: List[str] = []
    labels: List[str] = []
    for action_id, samples in _EMBEDDED_ROUTE_PROTOTYPES.items():
        for sample in samples:
            all_texts.append(sample)
            labels.append(action_id)
    all_texts.append(request_text)
    try:
        vectors = _ollama_embed_texts(all_texts)
    except Exception:
        _OLLAMA_PROBE_STATE["checked_at"] = time.time()
        _OLLAMA_PROBE_STATE["online"] = False
        return {}
    query_vec = vectors[-1]
    best_by_action: Dict[str, float] = {}
    for action_id, vec in zip(labels, vectors[:-1]):
        score = _cosine_similarity(query_vec, vec)
        if score > float(best_by_action.get(action_id, -1.0)):
            best_by_action[action_id] = score
    ranked = sorted(((score, action_id) for action_id, score in best_by_action.items()), reverse=True)
    if not ranked:
        return {}
    top_score, top_action = ranked[0]
    second_score = ranked[1][0] if len(ranked) > 1 else 0.0
    if top_score < _OLLAMA_ROUTE_MIN_SCORE or (top_score - second_score) < _OLLAMA_ROUTE_MIN_MARGIN:
        return {}
    row = _implemented_action_rows(repo_root=repo_root, contract_repo_root=contract_repo_root).get(top_action, {})
    return {
        "action_id": top_action,
        "operator_verb": str(row.get("operator_verb") or row.get("title") or top_action).strip(),
        "confidence_bucket": "medium",
        "matched_rule_id": "embedding_semantic_match",
        "fallback_eligibility_class": str(row.get("fallback_policy") or "").strip(),
        "required_evidence_gatherers": [],
        "supported": True,
        "semantic_score": round(float(top_score), 6),
        "semantic_margin": round(float(top_score - second_score), 6),
        "semantic_model": _OLLAMA_EMBED_MODEL,
    }


def _semantic_selection(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    request_text = str(_normalize_request_text(payload) or "").strip().lower()
    if not request_text:
        return {}

    cleaned = " ".join(request_text.replace("?", " ").replace("!", " ").replace(",", " ").split())
    tokens = set(cleaned.split())
    resolved_target_uid = _resolved_target_uid(payload)

    def _has_phrase(*phrases: str) -> bool:
        return any(phrase in cleaned for phrase in phrases if str(phrase or "").strip())

    entity = ""
    if _has_phrase("intent sheet", "intent sheets"):
        entity = "intent_sheet"
    elif _has_phrase("pis", "pises"):
        entity = "pis"
    elif "scheduler" in tokens:
        entity = "scheduler"
    elif "server" in tokens or "service" in tokens or "runtime" in tokens or "webui" in tokens:
        entity = "server"
    elif "backlog" in tokens:
        entity = "backlog"
    elif "job" in tokens or "jobs" in tokens:
        entity = "job"

    modifier = ""
    if "next" in tokens:
        modifier = "next"
    elif "latest" in tokens or "recent" in tokens or "newest" in tokens:
        modifier = "latest"
    elif "blocked" in tokens or "stuck" in tokens:
        modifier = "blocked"
    elif "failed" in tokens or "failure" in tokens:
        modifier = "failed"

    verb = ""
    query_tokens = {"what", "which", "show", "list", "inspect", "check", "review", "look"}
    if tokens & query_tokens:
        verb = "query"
    if tokens & {"import", "integrate"} or {"bring", "in"} <= tokens:
        verb = "import"
    if "watch" in tokens:
        verb = "watch"
    if "pause" in tokens:
        verb = "pause"
    if "stop" in tokens and entity == "scheduler":
        verb = "pause"
    if "resume" in tokens:
        verb = "resume"
    if tokens & {"recover", "repair", "heal"} or ("restart" in tokens and entity in {"server", "scheduler"}):
        verb = "recover"
    if entity == "scheduler" and {"going", "again"} <= tokens:
        verb = "resume"
    if entity == "scheduler" and "continue" in tokens:
        verb = "resume"
    if entity == "server" and {"bring", "back", "up"} <= tokens:
        verb = "recover"
    if entity == "server" and "up" in tokens and not verb:
        verb = "query"
    if tokens & {"implement", "build"} and not verb:
        verb = "implement"

    action_id = ""
    rule_id = ""
    confidence = "medium"
    if verb == "import" and _has_phrase("intent sheet zip", "intent sheet bundle", "intent sheet from downloads"):
        action_id = "repo_import_intent_sheet_zip_v1"
        rule_id = "semantic_import_intent_sheet_bundle"
    elif verb == "import" and _has_phrase("pis bundle", "pis zip", "pises from downloads", "pis from downloads"):
        action_id = "repo_import_pis_zip_v1"
        rule_id = "semantic_import_pis_bundle"
    elif entity == "intent_sheet" and modifier == "next" and verb == "implement":
        action_id = "implement_recursive_v1"
        rule_id = "semantic_implement_next_intent_sheet"
    elif entity == "intent_sheet" and modifier == "next":
        action_id = "implementation_prepare_v1"
        rule_id = "semantic_next_intent_sheet"
    elif entity == "pis" and modifier == "next" and verb == "implement":
        action_id = "implement_recursive_v1"
        rule_id = "semantic_implement_next_pis"
    elif entity == "pis" and modifier == "next":
        action_id = "implementation_prepare_pis_v1"
        rule_id = "semantic_next_pis"
    elif entity == "scheduler" and verb == "recover":
        action_id = "scheduler_recover_v1"
        rule_id = "semantic_scheduler_recover"
    elif entity == "scheduler" and verb == "watch":
        action_id = "scheduler_watch_v1"
        rule_id = "semantic_scheduler_watch"
    elif entity == "scheduler" and verb == "pause":
        action_id = "scheduler_pause_v1"
        rule_id = "semantic_scheduler_pause"
    elif entity == "scheduler" and verb == "resume":
        action_id = "scheduler_resume_v1"
        rule_id = "semantic_scheduler_resume"
    elif entity == "server" and verb == "recover":
        action_id = "service_recover_v1"
        rule_id = "semantic_server_recover"
    elif entity == "server" and verb == "watch":
        action_id = "server_watch_v1"
        rule_id = "semantic_server_watch"
    elif entity == "server" and verb == "query":
        action_id = "server_status_v1"
        rule_id = "semantic_server_status"
    elif entity == "backlog" and (verb == "query" or modifier == "next"):
        action_id = "backlog_inspect_v1"
        rule_id = "semantic_backlog_inspect"
    elif entity == "job" and modifier in {"blocked", "failed"}:
        action_id = "job_inspect_bounded_recovery_v1"
        rule_id = "semantic_job_inspect"
    elif entity == "job" and verb == "query":
        action_id = "job_inspect_bounded_recovery_v1"
        rule_id = "semantic_job_inspect"

    if not action_id:
        return {}
    row = _implemented_action_rows(repo_root=repo_root, contract_repo_root=contract_repo_root).get(action_id, {})
    return {
        "action_id": action_id,
        "operator_verb": str(row.get("operator_verb") or row.get("title") or action_id).strip(),
        "confidence_bucket": confidence,
        "matched_rule_id": rule_id,
        "fallback_eligibility_class": str(row.get("fallback_policy") or "").strip(),
        "required_evidence_gatherers": [],
        "supported": True,
        "resolved_target_uid": resolved_target_uid,
    }


def _phase1_backstop_selection(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    request_text = _normalize_request_text(payload)
    routed = classify_phase1_action_request(
        {
            "request_text": request_text,
            "artifact_presence_flags": _artifact_presence_flags(payload),
            "target_identifiers": _target_identifiers(payload),
        },
        repo_root=_contract_root(contract_repo_root, repo_root),
    )
    selection = {
        "action_id": str(routed.get("action_id") or "").strip(),
        "operator_verb": "",
        "confidence_bucket": str(routed.get("confidence_bucket") or "none").strip(),
        "matched_rule_id": str(routed.get("matched_rule_id") or "").strip(),
        "fallback_eligibility_class": str(routed.get("fallback_eligibility_class") or "none").strip(),
        "required_evidence_gatherers": list(routed.get("required_evidence_gatherers") or []),
        "supported": bool(str(routed.get("action_id") or "").strip()),
        "unsupported_reason": str(routed.get("unsupported_reason") or "").strip(),
        "selection_origin": "phase1_backstop",
    }
    if selection["action_id"]:
        row = _implemented_action_rows(repo_root=repo_root, contract_repo_root=contract_repo_root).get(selection["action_id"], {})
        selection["operator_verb"] = str(row.get("operator_verb") or row.get("title") or selection["action_id"]).strip()
    return selection


def _parser_candidate_selections(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    parser_steps = (
        ("commparse_direct", _direct_selection),
        ("semantic_embedding", _embedding_semantic_selection),
        ("semantic_router", _semantic_selection),
        ("commparse_heuristic", _heuristic_selection),
        ("verb_alias", _verb_alias_selection),
        ("phase1_backstop", _phase1_backstop_selection),
    )
    for parser_id, parser_fn in parser_steps:
        selection = parser_fn(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
        if not selection:
            continue
        candidate = dict(selection)
        candidate["selection_origin"] = str(candidate.get("selection_origin") or parser_id).strip()
        candidate["parser_id"] = parser_id
        candidates.append(candidate)
    return candidates


def _parser_disagreement_summary(candidates: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    action_ids: List[str] = []
    by_parser: List[str] = []
    for row in candidates:
        action_id = str(row.get("action_id") or "").strip()
        parser_id = str(row.get("parser_id") or row.get("selection_origin") or "").strip()
        if action_id:
            action_ids.append(action_id)
            by_parser.append(f"{parser_id}={action_id}")
    distinct_action_ids = sorted(set(action_ids))
    disagreement_present = len(distinct_action_ids) > 1
    return {
        "candidate_action_ids": distinct_action_ids,
        "disagreement_present": disagreement_present,
        "disagreement_summary": ", ".join(by_parser) if disagreement_present else "",
    }


def _parser_priority_weight(parser_id: str) -> float:
    return {
        "commparse_direct": 1.0,
        "verb_alias": 0.95,
        "semantic_embedding": 0.95,
        "semantic_router": 0.85,
        "commparse_heuristic": 0.7,
        "phase1_backstop": 0.5,
    }.get(str(parser_id or "").strip(), 0.4)


def _confidence_bucket_weight(confidence_bucket: str) -> float:
    return {
        "explicit": 1.0,
        "high": 0.9,
        "medium": 0.7,
        "low": 0.45,
        "none": 0.2,
    }.get(str(confidence_bucket or "").strip().lower(), 0.4)


def _contextual_action_bonus(payload: Mapping[str, Any], action_id: str) -> float:
    request_text = _normalize_request_text(payload).strip().lower()
    resolved_target_uid = _resolved_target_uid(payload)
    if request_text.startswith("implement") and resolved_target_uid in {"NEXT_PIS", "NEXT_INTENT"} and action_id == "implement_recursive_v1":
        return 0.25
    if (
        request_text.startswith("implement")
        and resolved_target_uid.startswith(("IS-", "PIS-"))
        and action_id == "implement_recursive_v1"
    ):
        return 0.5
    return 0.0


def _choose_primary_parser_candidate(payload: Mapping[str, Any], candidates: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    if not candidates:
        return {}
    request_text = _normalize_request_text(payload).strip().lower()
    resolved_target_uid = _resolved_target_uid(payload)
    if request_text.startswith("implement") and resolved_target_uid.startswith(("IS-", "PIS-")):
        for parser_id in ("commparse_direct", "verb_alias", "commparse_heuristic", "semantic_router", "semantic_embedding", "phase1_backstop"):
            for row in candidates:
                if str(row.get("action_id") or "").strip() != "implement_recursive_v1":
                    continue
                if str(row.get("parser_id") or row.get("selection_origin") or "").strip() != parser_id:
                    continue
                return dict(row)
    for parser_id in ("commparse_direct", "verb_alias"):
        for row in candidates:
            if str(row.get("parser_id") or row.get("selection_origin") or "").strip() == parser_id:
                return dict(row)
    unsupported_priority_reasons = {"force_codex_override", "multi_step_campaign_planning_requires_codex"}
    unsupported_candidates = []
    for row in candidates:
        if bool(row.get("supported")):
            continue
        unsupported_reason = str(row.get("unsupported_reason") or "").strip()
        if unsupported_reason not in unsupported_priority_reasons:
            continue
        parser_id = str(row.get("parser_id") or row.get("selection_origin") or "").strip()
        unsupported_candidates.append(
            (
                -_parser_priority_weight(parser_id) * _confidence_bucket_weight(str(row.get("confidence_bucket") or "none")),
                dict(row),
            )
        )
    if unsupported_candidates:
        unsupported_candidates.sort(key=lambda item: item[0])
        return dict(unsupported_candidates[0][1])
    scored: Dict[str, Dict[str, Any]] = {}
    for index, row in enumerate(candidates):
        action_id = str(row.get("action_id") or "").strip()
        if not action_id:
            continue
        parser_id = str(row.get("parser_id") or row.get("selection_origin") or "").strip()
        score = (
            _parser_priority_weight(parser_id) * _confidence_bucket_weight(str(row.get("confidence_bucket") or "none"))
            + _contextual_action_bonus(payload, action_id)
        )
        bucket = scored.setdefault(
            action_id,
            {
                "total_score": 0.0,
                "best_score": -1.0,
                "first_index": index,
                "winner": dict(row),
            },
        )
        bucket["total_score"] = float(bucket.get("total_score") or 0.0) + score
        if score > float(bucket.get("best_score") or -1.0):
            bucket["best_score"] = score
            bucket["winner"] = dict(row)
            bucket["first_index"] = min(int(bucket.get("first_index") or index), index)
    if not scored:
        return dict(candidates[0])
    ranked = sorted(
        scored.items(),
        key=lambda item: (
            -float(item[1].get("total_score") or 0.0),
            -float(item[1].get("best_score") or 0.0),
            int(item[1].get("first_index") or 0),
        ),
    )
    return dict(ranked[0][1].get("winner") or candidates[0])


def _command_parse_agrees_with_final_selection(command_parse: Mapping[str, Any], final_action_id: str) -> bool:
    final = str(final_action_id or "").strip()
    selected = str(command_parse.get("selected_action_id") or "").strip()
    return bool(selected and selected == final)


def _commparse_primary_selection(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    candidates = _parser_candidate_selections(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
    if candidates:
        return _choose_primary_parser_candidate(payload, candidates)
    return {}


def _render_commparse_commands(payload: Mapping[str, Any], action_id: str, commands: Sequence[str]) -> List[str]:
    rendered = [str(x).strip() for x in list(commands or []) if str(x).strip()]
    if action_id == "implementation_prepare_v1":
        limit = _requested_parallel_safe_limit(payload, default=5 if not _wants_parallel_safe_only(payload) else 2)
        command = f"python -m tools.bin.intent_sheet_next --limit {int(limit)}"
        if _wants_pairwise_parallel_safe_set(payload):
            command += " --set-safe-only"
        if _wants_parallel_safe_only(payload):
            command += " --safe-only"
        return [command]
    if action_id == "backlog_inspect_v1":
        limit = _requested_parallel_safe_limit(payload, default=10)
        return [f"python -m tools.bin.intent_sheet_next --limit {int(limit)}"]
    if action_id == "implement_recursive_v1":
        target_uid = _resolved_target_uid(payload)
        if target_uid:
            return [f"python -m tools.bin.implement_recursive --uid {target_uid}"]
    return rendered


def build_command_parse_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    request_text = _normalize_request_text(payload)
    candidates = _parser_candidate_selections(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
    selection = _choose_primary_parser_candidate(payload, candidates)
    action_id = str(selection.get("action_id") or "").strip()
    program = dict(_COMM_PARSE_ACTION_PROGRAMS.get(action_id) or {})
    commands = _render_commparse_commands(payload, action_id, list(program.get("commands") or []))
    disagreement = _parser_disagreement_summary(candidates)
    if _looks_like_chained_request(request_text) and commands:
        shape = "sequence_request"
        summary = str(program.get("summary") or f"Would call {action_id}.").strip()
        summary = f"Looks like a chained request; first deterministic step would be {action_id}. {summary}"
    else:
        shape = str(program.get("kind") or ("unsupported" if not action_id else "deterministic_action")).strip()
        summary = str(program.get("summary") or ("No deterministic command/program candidate yet." if not action_id else f"Would call {action_id}.")).strip()
    if bool(disagreement.get("disagreement_present")) and action_id:
        summary = f"Parser disagreement detected; primary candidate is {action_id}. {summary}"
    return {
        "schema_version": "plane_command_parse_v1",
        "surface_name": "the plane",
        "request_text": request_text,
        "selected_action_id": action_id,
        "selected_operator_verb": str(selection.get("operator_verb") or "").strip(),
        "confidence_bucket": str(selection.get("confidence_bucket") or "none").strip(),
        "matched_rule_id": str(selection.get("matched_rule_id") or "").strip(),
        "selection_origin": str(selection.get("selection_origin") or "").strip(),
        "supported": bool(action_id),
        "execution_shape": shape,
        "would_call_commands": commands,
        "summary": summary,
        "parser_candidates": [
            {
                "parser_id": str(row.get("parser_id") or row.get("selection_origin") or "").strip(),
                "selected_action_id": str(row.get("action_id") or "").strip(),
                "matched_rule_id": str(row.get("matched_rule_id") or "").strip(),
                "confidence_bucket": str(row.get("confidence_bucket") or "none").strip(),
                "supported": bool(row.get("supported")),
            }
            for row in candidates
        ],
        "candidate_action_ids": list(disagreement.get("candidate_action_ids") or []),
        "disagreement_present": bool(disagreement.get("disagreement_present")),
        "disagreement_summary": str(disagreement.get("disagreement_summary") or "").strip(),
    }


def _heuristic_selection(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    request_text = _normalize_request_text(payload)
    low = request_text.lower()
    resolved_target_uid = _resolved_target_uid(payload)
    if bool(payload.get("force_codex")):
        return {
            "action_id": "",
            "operator_verb": "forced codex fallback",
            "confidence_bucket": "explicit",
            "matched_rule_id": "heuristic_force_codex_override",
            "fallback_eligibility_class": "allowed",
            "required_evidence_gatherers": [],
            "supported": False,
            "unsupported_reason": "force_codex_override",
        }
    patch_present = bool(str(payload.get("patch_text") or "").strip() or str(payload.get("patch_path") or "").strip())
    report_present = bool(payload.get("report") or payload.get("snapshot") or payload.get("receipts"))
    iterations_present = bool(payload.get("iterations"))
    fallback_events_present = bool(payload.get("fallback_events"))
    current_status_present = bool(str(payload.get("current_status") or "").strip())
    expected_status_present = bool(str(payload.get("expected_status") or "").strip())

    def _selection(action_id: str, rule_id: str, confidence: str = "high") -> Dict[str, Any]:
        row = _implemented_action_rows(repo_root=repo_root, contract_repo_root=contract_repo_root).get(action_id, {})
        return {
            "action_id": action_id,
            "operator_verb": str(row.get("operator_verb") or row.get("title") or action_id).strip(),
            "confidence_bucket": confidence,
            "matched_rule_id": rule_id,
            "fallback_eligibility_class": str(row.get("fallback_policy") or "").strip(),
            "required_evidence_gatherers": [],
            "supported": True,
        }

    if fallback_events_present and _contains_any(
        low,
        [
            "upgrade",
            "absorb",
            "absorption",
            "recurring fallback",
            "recurring",
            "automate",
            "worth it",
            "worth automating",
            "codex drain",
            "dependency on codex",
            "independence roadmap",
            "rejected for absorption",
        ],
    ):
        return _selection("repeated_patch_fallback_to_approved_upgrade_v1", "heuristic_phase4_absorption")
    if _contains_any(low, ["apply this patch", "fix this patch", "repair this patch", "repair patch", "patch failed", "patch failure", "what should i do with this patch"]):
        return _selection("patch_ingest_repair_v1", "heuristic_phase1_patch_prompt_family", "medium")
    if _contains_any(
        low,
        [
            "why is this blocked",
            "why is the system degraded",
            "why did this patch fail",
            "explain blocked",
            "blocked state",
            "degraded",
            "what should i do",
            "what should i do now",
            "what happened",
            "next safest action",
            "next step",
            "show uncertainty",
            "reveal proof gaps",
            "show proof gaps",
            "explain the current state",
            "explain this blocked state",
        ],
    ):
        return _selection("blocked_chain_explain_and_recommend_v1", "heuristic_phase3_operator", "medium" if report_present else "low")
    if _contains_any(
        low,
        [
            "validate the implementation",
            "validate this implementation",
            "review this implementation",
            "review the implementation",
            "is this implementation correct",
            "check the implementation",
            "check whether this patch is really implemented",
            "is this patch really implemented",
        ],
    ):
        concrete_validation_context = any(
            (
                str(payload.get("target_uid") or "").strip(),
                str(payload.get("target_pis_uid") or "").strip(),
                str(payload.get("current_status") or "").strip(),
                str(payload.get("expected_status") or "").strip(),
                str(payload.get("verifier_verdict") or "").strip(),
                list(payload.get("validation_commands") or []),
                list(payload.get("validation_commands_executed") or []),
                payload.get("report"),
                payload.get("snapshot"),
                payload.get("receipts"),
            )
        )
        if concrete_validation_context:
            return _selection("implementation_validate_v1", "heuristic_implementation_validate_deterministic_slice", "medium")
        return _selection("implementation_validate_v1", "heuristic_implementation_validate_intake", "low")
    if _contains_any(
        low,
        [
            "breaks down",
            "break down",
            "parent pis",
            "children pises",
            "children pis",
            "schedules their implementation",
            "builder-verifier loop",
            "build, verify, build",
        ],
    ):
        return {
            "action_id": "",
            "operator_verb": "implementation campaign",
            "confidence_bucket": "high",
            "matched_rule_id": "heuristic_codex_first_campaign_planning",
            "fallback_eligibility_class": "allowed",
            "required_evidence_gatherers": [],
            "supported": False,
            "unsupported_reason": "multi_step_campaign_planning_requires_codex",
        }
    if current_status_present or expected_status_present or _contains_any(low, ["verify outcome", "verify implementation", "reconcile", "compare outcomes", "status drift"]):
        return _selection("status_reconcile_v1", "heuristic_phase2_status_reconcile")
    if iterations_present or _contains_any(
        low,
        [
            "continue interrupted",
            "recover job",
            "inspect/recover job",
            "re-run work",
            "bounded recovery",
            "queue verifier",
            "verify build",
            "run build and verify",
            "verify this task",
            "build verification",
            "build and verify",
            "try again and stop if it is pointless",
            "try again",
        ],
    ):
        return _selection("build_verify_loop_v1", "heuristic_phase2_build_verify", "medium")
    if _contains_any(
        low,
        [
            "import intent sheet zip",
            "intent sheet zip",
            "integrate intent sheet",
            "import intent sheet",
            "intent sheet from downloads",
        ],
    ):
        return _selection("repo_import_intent_sheet_zip_v1", "heuristic_repo_import_intent_sheet_zip", "medium")
    if _contains_any(
        low,
        [
            "integrate pis from downloads",
            "integrate pis",
            "import pis from downloads",
            "import pises from downloads",
            "integrate zipped pises",
            "integrate zipped pis",
            "import pis zip",
            "pis zip",
            "zipped pises",
            "zipped pis",
        ],
    ):
        return _selection("repo_import_pis_zip_v1", "heuristic_repo_import_pis_zip", "medium")
    if _contains_any(low, ["show commit journal", "commit journal", "journal status", "show journal"]):
        return _selection("repo_show_commit_journal_v1", "heuristic_repo_show_commit_journal", "medium")
    if _contains_any(low, ["show recent commits", "recent commits", "last commits", "git log"]):
        return _selection("repo_show_recent_commits_v1", "heuristic_repo_show_recent_commits", "medium")
    if _contains_any(
        low,
        [
            "archive",
            "snapshot",
            "weekly archive",
            "debug archive",
        ],
    ):
        return _selection("repo_archive_snapshot_v1", "heuristic_repo_archive", "medium")
    if _contains_any(
        low,
        [
            "commit",
            "commit changes",
            "save changes",
            "create commit",
            "tag this baseline",
        ],
    ):
        return _selection("repo_commit_tag_v1", "heuristic_repo_commit", "medium")
    if _contains_any(low, ["server status", "inspect server", "check server", "server health", "server ready"]):
        return _selection("server_status_v1", "heuristic_server_status", "medium")
    if _contains_any(low, ["server watchdog", "watchdog server"]):
        return _selection("server_watchdog_v1", "heuristic_server_watchdog", "medium")
    if _contains_any(low, ["server watch", "watch server"]):
        return _selection("server_watch_v1", "heuristic_server_watch", "medium")
    if _contains_any(low, ["recover service", "restart server", "server restart", "restart runtime", "runtime restart", "refresh runtime", "restart the webui server", "restart webui server", "webui restart"]):
        return _selection("service_recover_v1", "heuristic_service_recover", "medium")
    if _contains_any(low, ["server start", "start server"]):
        return _selection("server_start_v1", "heuristic_server_start", "medium")
    if _contains_any(low, ["server stop", "stop server"]):
        return _selection("server_stop_v1", "heuristic_server_stop", "medium")
    if _contains_any(low, ["run safe maintenance", "safe maintenance", "maintenance audit"]):
        return _selection("safe_maintenance_v1", "heuristic_safe_maintenance", "medium")
    if _contains_any(
        low,
        [
            "implement next pis",
            "implement the next pis",
            "implement next intent",
            "implement next intent sheet",
            "implement the next intent sheet",
            "implement the next intent sheet please",
            "implement next intent sheet please",
        ],
    ) and resolved_target_uid in {"NEXT_PIS", "NEXT_INTENT"}:
        return _selection("implement_recursive_v1", "heuristic_recursive_implement_next_selector", "medium")
    if _contains_any(
        low,
        [
            "implement ",
            "implement\t",
            "implement\n",
            "implement recursively",
            "implement recursively.",
        ],
    ) and resolved_target_uid:
        return _selection("implement_recursive_v1", "heuristic_recursive_implement", "medium")
    if _contains_any(
        low,
        [
            "safe to implement in parallel",
            "safe to start in parallel",
            "safe to run in parallel",
            "safe in parallel",
            "safe between themselves to implement in parallel",
            "safe between themselves in parallel",
            "safe among themselves to implement in parallel",
            "mutually safe to implement in parallel",
            "safe as a set to implement in parallel",
            "implement in parallel",
            "what is the next intent sheet",
            "what are the next intent sheets",
            "next intent sheet",
            "next intent sheets",
            "what is the next intent sheet to implement",
            "what are the next intent sheets to implement",
            "implement the next intent sheet",
            "implement next intent sheet",
            "implement the next intent sheets",
            "implement next intent sheets",
            "next intent sheets to implement",
            "next intent sheet to implement",
        ],
    ):
        return _selection("implementation_prepare_v1", "heuristic_implementation_prepare_intent_explicit", "medium")
    if _contains_any(
        low,
        [
            "what are the next pises to implement",
            "what are the next pis to implement",
            "next pises to implement",
            "next pis to implement",
        ],
    ):
        return _selection("implementation_prepare_pis_v1", "heuristic_implementation_prepare_pis", "medium")
    if _contains_any(
        low,
        [
            "prepare implementation",
            "implementation campaign",
            "prepare implementation campaign",
            "what should i implement next",
            "what are the next pises i should implement",
            "next pises i should implement",
            "next pis i should implement",
            "next items i should implement",
            "what should i build next",
        ],
    ):
        return _selection("implementation_prepare_v1", "heuristic_implementation_prepare", "medium")
    if _contains_any(low, ["inspect scheduler", "scheduler status", "scheduler health", "check scheduler"]):
        return _selection("scheduler_inspect_v1", "heuristic_scheduler_inspect", "medium")
    if _contains_any(low, ["recover scheduler", "heal scheduler", "repair scheduler"]):
        return _selection("scheduler_recover_v1", "heuristic_scheduler_recover", "medium")
    if _contains_any(low, ["scheduler disable all jobs", "disable all scheduler jobs"]) or (
        "disable all jobs" in low and "scheduler" in low
    ):
        return _selection("scheduler_disable_all_v1", "heuristic_scheduler_disable_all", "medium")
    if _contains_any(low, ["scheduler enable all jobs", "enable all scheduler jobs"]) or (
        "enable all jobs" in low and "scheduler" in low
    ):
        return _selection("scheduler_enable_all_v1", "heuristic_scheduler_enable_all", "medium")
    if _contains_any(low, ["scheduler watch", "watch scheduler"]):
        return _selection("scheduler_watch_v1", "heuristic_scheduler_watch", "medium")
    if _contains_any(low, ["scheduler pause", "pause scheduler", "pause the scheduler", "pause scheduler job", "pause the scheduler job"]):
        return _selection("scheduler_pause_v1", "heuristic_scheduler_pause", "medium")
    if _contains_any(low, ["scheduler resume", "resume scheduler", "resume the scheduler", "resume scheduler job", "resume the scheduler job"]):
        return _selection("scheduler_resume_v1", "heuristic_scheduler_resume", "medium")
    if _contains_any(low, ["inspect backlog", "show backlog", "review backlog", "what is next in the backlog", "next backlog items"]):
        return _selection("backlog_inspect_v1", "heuristic_backlog_inspect", "medium")
    if (patch_present or _contains_any(low, ["patch", "diff", "reconcile patch", "validate patch"])) and _contains_any(low, ["patch", "diff", "reconcile patch", "validate patch"]):
        has_chain_evidence = any(
            (
                str(payload.get("target_pis_uid") or "").strip(),
                str(payload.get("pis_markdown") or "").strip(),
            )
        )
        if has_chain_evidence:
            return _selection("patch_apply_chain_v1", "heuristic_phase2_patch_chain")
        return _selection("patch_ingest_repair_v1", "heuristic_phase1_patch_ingest", "medium")
    if _contains_any(low, ["gather proof", "assess item", "assess closeability", "proof gap", "closeable"]) or (
        "pis" in low and bool(str(payload.get("target_pis_uid") or payload.get("target_uid") or "").strip())
    ):
        return _selection("pis_assess_proof_gap_v1", "heuristic_phase1_pis_assess", "medium")
    if _contains_any(low, ["inspect job", "job status", "stuck job"]):
        return _selection("job_inspect_bounded_recovery_v1", "heuristic_phase1_job_inspect", "medium")
    return {}


def _direct_selection(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    action_rows = _implemented_action_rows(repo_root=repo_root, contract_repo_root=contract_repo_root)
    action_id = str(payload.get("action_id") or "").strip()
    if action_id:
        if action_id in _DIRECT_ACTIONS:
            row = action_rows.get(action_id, {})
            return {
                "action_id": action_id,
                "operator_verb": str(row.get("operator_verb") or row.get("title") or action_id).strip(),
                "confidence_bucket": "explicit",
                "matched_rule_id": "explicit_action_id",
                "fallback_eligibility_class": str(row.get("fallback_policy") or "").strip(),
                "required_evidence_gatherers": [],
                "supported": True,
            }
        return {
            "action_id": action_id,
            "operator_verb": "",
            "confidence_bucket": "none",
            "matched_rule_id": "",
            "fallback_eligibility_class": "none",
            "required_evidence_gatherers": [],
            "supported": False,
            "unsupported_reason": "unknown_action_id",
        }
    return {}


def _verb_alias_selection(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    action_rows = _implemented_action_rows(repo_root=repo_root, contract_repo_root=contract_repo_root)
    operator_verb = str(payload.get("operator_verb") or "").strip().lower()
    alias_action_id = _VERB_ALIASES.get(operator_verb)
    if alias_action_id:
        row = action_rows.get(alias_action_id, {})
        return {
            "action_id": alias_action_id,
            "operator_verb": str(row.get("operator_verb") or row.get("title") or alias_action_id).strip(),
            "confidence_bucket": "explicit",
            "matched_rule_id": "explicit_operator_verb",
            "fallback_eligibility_class": str(row.get("fallback_policy") or "").strip(),
            "required_evidence_gatherers": [],
            "supported": True,
        }
    return {}


def classify_operator_request_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    request_text = _normalize_request_text(payload)
    decomposition = build_request_decomposition_v1(
        {
            "request_text": request_text,
            "artifact_presence_flags": _artifact_presence_flags(payload),
            "target_identifiers": _target_identifiers(payload),
            "authority_class": str(payload.get("authority_class") or payload.get("authority_scope") or "").strip(),
            "operator_verb": str(payload.get("operator_verb") or "").strip(),
        },
        repo_root=_contract_root(contract_repo_root, repo_root),
    )
    selection = _commparse_primary_selection(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
    return {
        "schema_version": "plane_request_classification_v1",
        "surface_name": "the plane",
        "request_text": request_text,
        "decomposition": decomposition,
        "selection": selection,
    }


def _phase3_payload_from_result(action_id: str, result: Mapping[str, Any]) -> Dict[str, Any]:
    outcome = dict(result.get("outcome") or {})
    terminal = str(outcome.get("outcome") or "").strip()
    proof_gaps = list(outcome.get("proof_gaps") or result.get("proof_gaps") or [])
    verifier_verdict = str(outcome.get("verifier_verdict") or "").strip()
    artifact_refs = list(outcome.get("evidence_artifacts") or [])
    reason = str(result.get("failure_reason_code") or result.get("proof_class") or result.get("state_class") or terminal or "result_recorded").strip()
    report_like = dict(result.get("report") or {})
    if not report_like:
        iteration_reports = list(result.get("iteration_reports") or [])
        if iteration_reports:
            report_like = dict(iteration_reports[-1] or {})
    if report_like:
        terminal = terminal or str(report_like.get("final_chain_outcome") or report_like.get("status") or "").strip()
        verifier_verdict = verifier_verdict or str(report_like.get("final_verifier_verdict") or "").strip()
        reason = str(report_like.get("terminal_reason") or reason or "result_recorded").strip()
        if not proof_gaps:
            for record in list(report_like.get("step_records") or []):
                if isinstance(record, Mapping):
                    for gap in list(record.get("proof_gaps") or []):
                        text = str(gap or "").strip()
                        if text:
                            proof_gaps.append(text)
        if not artifact_refs:
            artifact_refs = list(report_like.get("evidence_artifacts") or [])
    if terminal.startswith("success_"):
        status = "pass"
    elif terminal == "completed_with_pass":
        status = "pass"
    elif terminal == "completed_with_proof_gap":
        status = "proof_gap"
    elif terminal == "proof_gap_present":
        status = "proof_gap"
    elif terminal == "failed_with_known_gap":
        status = "failed"
    elif terminal == "partial_success":
        status = "running"
    else:
        status = "blocked"
    report = {
        "action_family": _FAMILY_MAP.get(action_id, ""),
        "status": status,
        "final_chain_outcome": status,
        "final_verifier_verdict": verifier_verdict,
        "terminal_reason": reason,
        "step_records": [{"proof_gaps": proof_gaps}] if proof_gaps else [],
    }
    return {
        "report": report,
        "artifact_references": artifact_refs,
        "fallback_events": list(result.get("fallback_events") or []),
        "improvement_opportunities": list(result.get("improvement_opportunities") or []),
    }


def _execute_action(action_id: str, payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    handler = _DIRECT_ACTIONS[action_id]
    request = dict(payload)
    if action_id == "patch_apply_chain_v1":
        request.setdefault("task_id", "plane_patch_apply_chain")
        request.setdefault("action_family", "patch_apply")
        request.setdefault("execution_mode", "deterministic_then_codex")
        request.setdefault("context_bundle_id", "lab_delivery_safety_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "repo_mutation")
        request.setdefault("target_pis_uid", str(request.get("target_pis_uid") or request.get("target_uid") or "").strip())
    elif action_id == "build_verify_loop_v1":
        request.setdefault("task_id", "plane_build_verify_loop")
        request.setdefault("action_family", "build_verify")
        request.setdefault("execution_mode", "deterministic_then_codex")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "status_reconcile_v1":
        if not any(key in request for key in ("task_id", "action_family", "execution_mode", "context_bundle_id", "requested_by", "source_event", "authority_scope")):
            base = build_snapshot_drift_action_request(
                {
                    "drift_id": str(request.get("task_id") or "plane_status_reconcile"),
                    "target_uid": str(request.get("target_uid") or request.get("target_pis_uid") or "UNKNOWN"),
                },
                repo_root=repo_root,
            )
            base.update(request)
            request = base
        request.setdefault("task_id", "plane_status_reconcile")
        request.setdefault("action_family", "status_reconcile")
        request.setdefault("execution_mode", "deterministic_then_codex")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "repo_commit_tag_v1":
        request.setdefault("task_id", "plane_repo_commit")
        request.setdefault("action_family", "repo_commit")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_delivery_safety_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "repo_mutation")
    elif action_id == "repo_archive_snapshot_v1":
        request.setdefault("task_id", "plane_repo_archive")
        request.setdefault("action_family", "archive_snapshot")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_delivery_safety_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "repo_import_pis_zip_v1":
        request.setdefault("task_id", "plane_pis_zip_import")
        request.setdefault("action_family", "ledger_import")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_delivery_safety_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "repo_mutation")
    elif action_id == "repo_import_intent_sheet_zip_v1":
        request.setdefault("task_id", "plane_intent_sheet_zip_import")
        request.setdefault("action_family", "ledger_import")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_delivery_safety_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "repo_mutation")
    elif action_id == "repo_show_commit_journal_v1":
        request.setdefault("task_id", "plane_show_commit_journal")
        request.setdefault("action_family", "repo_commit")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "repo_show_recent_commits_v1":
        request.setdefault("task_id", "plane_show_recent_commits")
        request.setdefault("action_family", "repo_commit")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "server_status_v1":
        request.setdefault("task_id", "plane_server_status")
        request.setdefault("action_family", "server_status")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "service_recover_v1":
        request.setdefault("task_id", "plane_service_recover")
        request.setdefault("action_family", "service_recover")
        request.setdefault("execution_mode", "deterministic_recovery")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "runtime_mutation")
    elif action_id in {"server_start_v1", "server_stop_v1"}:
        request.setdefault("task_id", f"plane_{action_id.removesuffix('_v1')}")
        request.setdefault("action_family", "server_status")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "runtime_mutation")
    elif action_id in {"server_watch_v1", "server_watchdog_v1"}:
        request.setdefault("task_id", f"plane_{action_id.removesuffix('_v1')}")
        request.setdefault("action_family", "server_status")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "safe_maintenance_v1":
        request.setdefault("task_id", "plane_safe_maintenance")
        request.setdefault("action_family", "maintenance")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "implementation_prepare_v1":
        request.setdefault("task_id", "plane_implementation_prepare")
        request.setdefault("action_family", "implementation_prepare")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "implementation_prepare_pis_v1":
        request.setdefault("task_id", "plane_implementation_prepare_pis")
        request.setdefault("action_family", "implementation_prepare")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "implement_recursive_v1":
        request.setdefault("task_id", "plane_implement_recursive")
        request.setdefault("action_family", "implementation_execute")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "repo_mutation")
    elif action_id == "implementation_validate_v1":
        request.setdefault("task_id", "plane_implementation_validate")
        request.setdefault("action_family", "status_reconcile")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "scheduler_inspect_v1":
        request.setdefault("task_id", "plane_scheduler_inspect")
        request.setdefault("action_family", "scheduler_inspect")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    elif action_id == "scheduler_recover_v1":
        request.setdefault("task_id", "plane_scheduler_recover")
        request.setdefault("action_family", "scheduler_recover")
        request.setdefault("execution_mode", "deterministic_recovery")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "scheduler_mutation")
    elif action_id in {"scheduler_disable_all_v1", "scheduler_enable_all_v1", "scheduler_watch_v1", "scheduler_pause_v1", "scheduler_resume_v1"}:
        request.setdefault("task_id", f"plane_{action_id.removesuffix('_v1')}")
        request.setdefault("action_family", "scheduler_control")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "scheduler_mutation")
    elif action_id == "backlog_inspect_v1":
        request.setdefault("task_id", "plane_backlog_inspect")
        request.setdefault("action_family", "backlog_inspect")
        request.setdefault("execution_mode", "deterministic_only")
        request.setdefault("context_bundle_id", "lab_core_v1")
        request.setdefault("requested_by", "operator")
        request.setdefault("source_event", "cli")
        request.setdefault("authority_scope", "read_only")
    return dict(handler(request, repo_root=repo_root))


def _terminal_class_for_result(action_id: str, result: Mapping[str, Any]) -> str:
    if action_id in {"blocked_chain_explain_and_recommend_v1", "repeated_patch_fallback_to_approved_upgrade_v1"}:
        return "success_deterministic" if bool(result.get("ok")) else "failed_unknown_class"
    outcome = dict(result.get("outcome") or {})
    text = str(outcome.get("outcome") or "").strip()
    if text:
        return text
    report = dict(result.get("report") or {})
    if report:
        report_outcome = str(report.get("final_chain_outcome") or report.get("status") or "").strip()
        if report_outcome == "completed_with_pass":
            return "success_deterministic"
        if report_outcome == "completed_with_proof_gap":
            return "proof_gap_present"
        if report_outcome in {"blocked", "escalated_and_blocked"}:
            return "blocked_requires_review"
        if report_outcome == "failed":
            return "failed_with_known_gap"
    iteration_reports = list(result.get("iteration_reports") or [])
    if iteration_reports:
        last_report = dict(iteration_reports[-1] or {})
        report_outcome = str(last_report.get("final_chain_outcome") or last_report.get("status") or "").strip()
        if report_outcome == "completed_with_pass":
            return "success_deterministic"
        if report_outcome == "completed_with_proof_gap":
            return "proof_gap_present"
        if report_outcome in {"blocked", "escalated_and_blocked"}:
            return "blocked_requires_review"
        if report_outcome == "failed":
            return "failed_with_known_gap"
    if bool(result.get("ok")):
        return "success_deterministic"
    return "failed_unknown_class"


def _qualifiers_for_result(result: Mapping[str, Any], operator_surface: Mapping[str, Any]) -> List[str]:
    qualifiers: List[str] = []
    uncertainty = dict(operator_surface.get("proof_uncertainty") or {})
    if bool(uncertainty.get("proof_gap_present")):
        qualifiers.append("proof_gap_present")
    if bool(uncertainty.get("stale_evidence_present")):
        qualifiers.append("stale_evidence_present")
    if bool(uncertainty.get("contradiction_present")):
        qualifiers.append("contradiction_present")
    if bool(operator_surface.get("manual_review_required")):
        qualifiers.append("manual_review_required")
    return qualifiers


def _is_blocking_terminal(terminal_class: str) -> bool:
    return terminal_class in {"blocked_requires_review", "failed_with_known_gap", "failed_unknown_class", "proof_gap_present"}


def _failure_class_from_result(result: Mapping[str, Any], terminal_class: str) -> str:
    outcome = dict(result.get("outcome") or {})
    proof_gaps = list(outcome.get("proof_gaps") or result.get("proof_gaps") or [])
    report = dict(result.get("report") or {})
    iteration_reports = list(result.get("iteration_reports") or [])
    last_report = dict(iteration_reports[-1] or {}) if iteration_reports else {}
    return str(
        result.get("failure_reason_code")
        or result.get("terminal_reason")
        or report.get("terminal_reason")
        or last_report.get("terminal_reason")
        or (proof_gaps[0] if proof_gaps else terminal_class)
        or "unknown_failure"
    ).strip()


def _build_codex_fallback_request(
    *,
    action_id: str,
    payload: Mapping[str, Any],
    result: Mapping[str, Any],
    selection: Mapping[str, Any],
    terminal_class: str,
) -> Dict[str, Any]:
    if not bool(payload.get("allow_fallback")) or not _is_blocking_terminal(terminal_class):
        return {}
    evidence_artifacts = list(dict(result.get("outcome") or {}).get("evidence_artifacts") or result.get("artifact_references") or payload.get("artifact_references") or [])
    validation_commands = list(dict(result.get("outcome") or {}).get("validation_commands_executed") or payload.get("validation_commands") or payload.get("validation_commands_executed") or [])
    checklist = dict(result.get("pre_escalation_checklist") or {})
    fallback_request = {
        "schema_version": "plane_codex_fallback_request_v1",
        "eligible": bool(result.get("fallback_allowed")) or bool(payload.get("allow_fallback")),
        "action_id": action_id,
        "action_family": _FAMILY_MAP.get(action_id, ""),
        "intent": str(_normalize_request_text(payload) or "").strip(),
        "failure_class": _failure_class_from_result(result, terminal_class),
        "fallback_policy": str(selection.get("fallback_eligibility_class") or "").strip(),
        "scope_objective": f"Resolve {action_id} failure within the existing request boundary.",
        "evidence_bundle": {
            "evidence_artifacts": evidence_artifacts,
            "validation_commands": validation_commands,
            "raw_result_present": True,
        },
        "mutation_boundary": {
            "authority_scope": str(payload.get("authority_scope") or "").strip(),
            "expected_target_files": list(payload.get("expected_target_files") or []),
            "target_uid": str(payload.get("target_uid") or payload.get("target_pis_uid") or "").strip(),
            "job_id": str(payload.get("job_id") or "").strip(),
        },
        "validator_expectations": {
            "verifier_verdict": str(payload.get("verifier_verdict") or "").strip(),
            "required_validation_commands": validation_commands,
            "required_terminal_class": terminal_class,
        },
        "pre_escalation_checklist": checklist,
        "trigger_reason": "deterministic_path_blocked_or_insufficient",
    }
    return fallback_request


def _learning_from_fallback_events(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None) -> Dict[str, Any]:
    events = [dict(row) for row in list(payload.get("fallback_events") or []) if isinstance(row, Mapping)]
    if not events:
        return {}
    recorded_events: List[Dict[str, Any]] = []
    for row in events:
        recorded_events.append(record_fallback_event_v1(row, repo_root=repo_root))
    aggregated_events: List[Dict[str, Any]] = list(recorded_events)
    anchor = recorded_events[-1]
    historical = load_recorded_fallback_events_v1(
        repo_root=repo_root,
        action_family=str(anchor.get("action_family") or "").strip(),
        failure_class=str(anchor.get("failure_class") or "").strip(),
        limit=12,
    )
    seen_ids = {
        str(row.get("fallback_event_id") or row.get("event_id") or row.get("id") or "").strip()
        for row in recorded_events
    }
    for row in historical:
        row_id = str(row.get("fallback_event_id") or row.get("event_id") or row.get("id") or "").strip()
        if row_id and row_id in seen_ids:
            continue
        aggregated_events.append(dict(row))
    opportunity = {}
    if len(aggregated_events) >= 2:
        opportunity = synthesize_improvement_opportunity_v1(aggregated_events, repo_root=repo_root)
    phase4 = execute_phase4_repeated_patch_fallback_to_upgrade_proposal_v1({"fallback_events": aggregated_events}, repo_root=repo_root)
    commparse_signals = []
    for row in aggregated_events:
        if not bool(row.get("commparse_related_gap")):
            continue
        failure_class = str(row.get("failure_class") or "unsupported_request").strip()
        signal = {
            "target": "commparse",
            "failure_class": failure_class,
            "selection_origin": str(row.get("selection_origin") or "").strip(),
            "suggested_updates": [
                "heuristic routing",
                "verb aliases",
                "command-parse program mapping",
                "focused parser regression tests",
            ],
        }
        if failure_class == "force_codex_override":
            signal["priority"] = "high"
            signal["suggested_updates"].insert(0, "override-specific parser rule review")
        elif "campaign" in failure_class or "semantic" in failure_class:
            signal["priority"] = "medium"
            signal["suggested_updates"].insert(0, "codex-first semantic family contract")
        else:
            signal["priority"] = "medium"
        commparse_signals.append(signal)
    return {
        "fallback_events": recorded_events,
        "aggregated_fallback_events": aggregated_events,
        "improvement_opportunity": opportunity,
        "phase4_absorption": phase4,
        "commparse_improvement_signals": commparse_signals,
    }


def execute_operator_request_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    classification = classify_operator_request_v1(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
    command_parse = build_command_parse_v1(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
    selection = dict(classification.get("selection") or {})
    action_id = str(selection.get("action_id") or "").strip()
    catalog = build_operator_catalog_v1(repo_root=repo_root, contract_repo_root=contract_repo_root)
    if not action_id or action_id not in _DIRECT_ACTIONS:
        if bool(payload.get("allow_fallback")):
            return _execute_unsupported_request_via_codex(payload, selection=selection, repo_root=repo_root)
        return {
            "schema_version": "plane_operator_result_v1",
            "surface_name": "the plane",
            "request_text": str(classification.get("request_text") or "").strip(),
            "mode": "execute",
            "classification": classification,
            "command_parse": command_parse,
            "selection": selection,
            "supported": False,
            "summary": "Request is not yet supported by the plane's implemented action surface.",
            "operator_surface": {
                "status_class": "unknown",
                "status_summary": "The plane could not route this request to an implemented action.",
                "primary_explanation": {
                    "primary_reason": str(selection.get("unsupported_reason") or "unsupported_request").strip(),
                    "proof_state": "uncertain",
                    "evidence_references": [],
                },
                "proof_uncertainty": {
                    "proof_gap_present": False,
                    "unresolved_items": [],
                    "stale_evidence_present": False,
                    "contradiction_present": False,
                    "confidence_bucket": "low",
                    "required_next_proof_steps": [],
                },
                "recommended_actions": [],
                "manual_review_required": True,
                "codex_transparency": {
                    "codex_used": False,
                    "usage_reason": "",
                    "recurrence_class": "",
                    "economic_recommendation_class": "",
                },
                "artifact_references": [],
                "section_order": [
                    "status_summary",
                    "primary_explanation",
                    "proof_uncertainty",
                    "recommended_actions",
                    "codex_transparency",
                    "artifact_references",
                ],
            },
            "catalog": catalog,
        }

    try:
        raw_result = _execute_action(action_id, payload, repo_root=repo_root)
    except Exception as exc:
        if bool(payload.get("allow_fallback")):
            return _execute_known_failure_via_codex(
                payload,
                selection=selection,
                action_id=action_id,
                failure_reason=str(exc).strip() or "deterministic_execution_failed",
                repo_root=repo_root,
            )
        raise
    if action_id in {
        "repo_commit_tag_v1",
        "repo_archive_snapshot_v1",
        "server_status_v1",
        "server_start_v1",
        "server_stop_v1",
        "server_watch_v1",
        "server_watchdog_v1",
        "repo_import_pis_zip_v1",
        "repo_import_intent_sheet_zip_v1",
        "repo_show_commit_journal_v1",
        "repo_show_recent_commits_v1",
        "service_recover_v1",
        "safe_maintenance_v1",
        "implementation_prepare_v1",
        "implementation_prepare_pis_v1",
        "implement_recursive_v1",
        "implementation_validate_v1",
        "scheduler_inspect_v1",
        "scheduler_recover_v1",
        "scheduler_disable_all_v1",
        "scheduler_enable_all_v1",
        "scheduler_watch_v1",
        "scheduler_pause_v1",
        "scheduler_resume_v1",
        "backlog_inspect_v1",
    }:
        operator_surface = _build_direct_operator_surface(action_id, raw_result)
    elif action_id == "blocked_chain_explain_and_recommend_v1":
        operator_surface = dict(raw_result.get("operator_surface") or {})
    else:
        phase3_result = execute_phase3_blocked_chain_explain_and_recommend_v1(
            _phase3_payload_from_result(action_id, raw_result),
            repo_root=repo_root,
        )
        operator_surface = dict(phase3_result.get("operator_surface") or {})
    terminal_class = _terminal_class_for_result(action_id, raw_result)
    qualifiers = _qualifiers_for_result(raw_result, operator_surface)
    summary = str(operator_surface.get("status_summary") or "").strip() or "Execution completed."
    fallback_request = _build_codex_fallback_request(
        action_id=action_id,
        payload=payload,
        result=raw_result,
        selection=selection,
        terminal_class=terminal_class,
    )
    suppress_codex_fallback = action_id == "implementation_validate_v1" and not bool(payload.get("force_codex"))
    if bool(fallback_request.get("eligible")) and not suppress_codex_fallback:
        return _execute_known_failure_via_codex(
            payload,
            selection=selection,
            action_id=action_id,
            failure_reason=_failure_class_from_result(raw_result, terminal_class),
            repo_root=repo_root,
        )
    learning = _learning_from_fallback_events(payload, repo_root=repo_root)
    all_phases_report = {}
    if str(_FAMILY_MAP.get(action_id) or "").strip() == "patch_apply" and list(payload.get("fallback_events") or []):
        all_phases_report = build_action_plane_all_phases_report_v1(
            {
                "fallback_events": list(payload.get("fallback_events") or []),
                "report": _phase3_payload_from_result(action_id, raw_result).get("report"),
            },
            repo_root=repo_root,
        )
    return {
        "schema_version": "plane_operator_result_v1",
        "surface_name": "the plane",
        "request_text": str(classification.get("request_text") or "").strip(),
        "mode": "execute",
        "classification": classification,
        "command_parse": {
            **command_parse,
            "agreement_with_final_selection": _command_parse_agrees_with_final_selection(command_parse, action_id),
            "final_action_id": action_id,
        },
        "selection": selection,
        "supported": True,
        "execution": raw_result,
        "operator_surface": operator_surface,
        "summary": summary,
        "terminal_class": terminal_class,
        "qualifiers": qualifiers,
        "codex_fallback_request": fallback_request,
        "learning_artifacts": learning,
        "all_phases_report": all_phases_report,
        "catalog": catalog,
    }


def plan_operator_request_v1(payload: Mapping[str, Any], *, repo_root: Optional[Path] = None, contract_repo_root: Optional[Path] = None) -> Dict[str, Any]:
    classification = classify_operator_request_v1(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
    command_parse = build_command_parse_v1(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
    selection = dict(classification.get("selection") or {})
    supported = bool(selection.get("supported")) and str(selection.get("action_id") or "").strip() in _DIRECT_ACTIONS
    summary = (
        f"The plane would route this request to {selection.get('action_id')}."
        if supported
        else "The plane does not have an implemented action for this request yet."
    )
    operator_surface = {
        "executive_surface": {
            "state": "RUNNING" if supported else "REVIEW_REQUIRED",
            "primary_next_action": {
                "label": str(selection.get("action_id") or "Manual review required").strip(),
            },
            "confidence": "High" if supported else "Unknown",
            "short_reason": summary,
        }
    }
    return {
        "schema_version": "plane_operator_result_v1",
        "surface_name": "the plane",
        "request_text": str(classification.get("request_text") or "").strip(),
        "mode": "plan",
        "classification": classification,
        "command_parse": {
            **command_parse,
            "agreement_with_final_selection": _command_parse_agrees_with_final_selection(
                command_parse,
                str(selection.get("action_id") or "").strip(),
            ),
            "final_action_id": str(selection.get("action_id") or "").strip(),
        },
        "selection": selection,
        "supported": supported,
        "summary": summary,
        "operator_surface": operator_surface,
        "catalog": build_operator_catalog_v1(repo_root=repo_root, contract_repo_root=contract_repo_root),
    }


def render_operator_result_text(result: Mapping[str, Any]) -> str:
    lines: List[str] = []
    lines.append(f"Surface: {result.get('surface_name') or 'the plane'}")
    request_text = str(result.get("request_text") or "").strip()
    if request_text:
        lines.append(f"Request: {request_text}")
    selection = dict(result.get("selection") or {})
    action_id = str(selection.get("action_id") or "").strip()
    if action_id:
        lines.append(f"Selected action: {action_id}")
    lines.append(f"Supported: {'yes' if bool(result.get('supported')) else 'no'}")
    mode = str(result.get("mode") or "").strip()
    if mode:
        lines.append(f"Mode: {mode}")
    terminal = str(result.get("terminal_class") or "").strip()
    if terminal:
        lines.append(f"Terminal class: {terminal}")
    summary = str(result.get("summary") or "").strip()
    if summary:
        lines.append(f"Summary: {summary}")
    operator_surface = dict(result.get("operator_surface") or {})
    if operator_surface:
        executive = dict(operator_surface.get("executive_surface") or {})
        explanation = dict(operator_surface.get("explanation") or operator_surface.get("primary_explanation") or {})
        uncertainty = dict(operator_surface.get("uncertainty") or operator_surface.get("proof_uncertainty") or {})
        if executive:
            lines.append(f"STATE: {executive.get('state') or 'REVIEW_REQUIRED'}")
            primary_next = dict(executive.get("primary_next_action") or {})
            if primary_next.get("label"):
                lines.append(f"NEXT ACTION: {primary_next.get('label')}")
            lines.append(f"CONFIDENCE: {executive.get('confidence') or 'Unknown'}")
            if executive.get("short_reason"):
                lines.append(f"SHORT REASON: {executive.get('short_reason')}")
        else:
            lines.append(f"Status: {operator_surface.get('status_class') or 'unknown'}")
        proof_state = str(explanation.get("proof_state") or operator_surface.get("primary_explanation", {}).get("proof_state") or "").strip()
        if proof_state:
            lines.append(f"Proof state: {proof_state}")
        unresolved = _as_list(uncertainty.get("unresolved_items"))
        if unresolved:
            lines.append("Unresolved items: " + ", ".join(unresolved))
        recommendation_block = dict(operator_surface.get("recommendations") or {})
        primary = dict(recommendation_block.get("primary") or {})
        alternatives = list(recommendation_block.get("alternatives") or [])
        if primary:
            lines.append(f"Primary recommendation: {primary.get('label') or primary.get('action_id')}")
            if primary.get("reason"):
                lines.append(f"Recommendation reason: {primary.get('reason')}")
        if alternatives:
            lines.append("Alternatives:")
            for row in alternatives:
                lines.append(f"- {row.get('label') or row.get('action_id')}: {row.get('reason')}")
    fallback = dict(result.get("codex_fallback_request") or {})
    if fallback:
        lines.append(f"Codex fallback eligible: {'yes' if bool(fallback.get('eligible')) else 'no'}")
        lines.append(f"Fallback failure class: {fallback.get('failure_class') or 'unknown'}")
    learning = dict(result.get("learning_artifacts") or {})
    if learning.get("improvement_opportunity"):
        lines.append(f"Improvement opportunity: {dict(learning['improvement_opportunity']).get('decision')}")
    return "\n".join(lines)
