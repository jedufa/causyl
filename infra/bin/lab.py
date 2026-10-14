from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence

from experiments_shared.action_plane.operator_interface import build_operator_catalog_v1, execute_operator_request_v1, plan_operator_request_v1
from infra.bin.lab_transcript import append_turn, end_session, ensure_session_started, new_session_id
from tools.bin import intent_sheet_next as _intent_sheet_next

_ANSI_RESET = "\033[0m"
_ANSI_BOLD = "\033[1m"
_ANSI_DIM = "\033[2m"
_ANSI_CYAN = "\033[36m"
_ANSI_GREEN = "\033[32m"
_ANSI_YELLOW = "\033[33m"
_ANSI_RED = "\033[31m"
_ANSI_MAGENTA = "\033[35m"
_TURN_DELIMITER = "-" * 48

_ACTION_HELP: Dict[str, Dict[str, Any]] = {
    "repo_archive_snapshot_v1": {
        "summary": "Create a repository archive snapshot.",
        "underlying_commands": [
            "bash infra/bin/archive",
            "bash infra/bin/archive --debug",
            "bash infra/bin/archive --weekly",
        ],
        "help_commands": [],
        "examples": ['lab "archive"', 'lab "archive debug"', 'lab "archive weekly"'],
    },
    "repo_commit_tag_v1": {
        "summary": "Create a governed git commit and tag through the journal-aware path.",
        "underlying_commands": ["bash tools/git/git-commit-tag.sh"],
        "help_commands": [],
        "examples": ['lab "commit"'],
    },
    "repo_show_commit_journal_v1": {
        "summary": "Show pending commit journal entries.",
        "underlying_commands": ["python -m tools.git.support.commit_journal view"],
        "help_commands": ["python -m tools.git.support.commit_journal -h"],
        "examples": ['lab "show commit journal"'],
    },
    "repo_show_recent_commits_v1": {
        "summary": "Show recent git commits.",
        "underlying_commands": ["git log --oneline -n 10"],
        "help_commands": [],
        "examples": ['lab "show recent commits"'],
    },
    "server_status_v1": {
        "summary": "Show current server status through the operator wrapper.",
        "underlying_commands": ["bash infra/user/server-status"],
        "help_commands": [],
        "examples": ['lab "server status"'],
    },
    "server_start_v1": {
        "summary": "Start the server through the operator wrapper.",
        "underlying_commands": ["bash infra/user/server-start"],
        "help_commands": [],
        "examples": ['lab "server start"'],
    },
    "server_stop_v1": {
        "summary": "Stop the server through the operator wrapper.",
        "underlying_commands": ["bash infra/user/server-stop"],
        "help_commands": [],
        "examples": ['lab "server stop"'],
    },
    "server_watch_v1": {
        "summary": "Open the live server/scheduler dashboard.",
        "underlying_commands": ["bash infra/user/server-watch --interval 2 --pipeline-limit 40"],
        "help_commands": [],
        "examples": ['lab "server watch"'],
    },
    "server_watchdog_v1": {
        "summary": "Open the menu-driven server watchdog tool.",
        "underlying_commands": ["bash infra/user/server-watchdog"],
        "help_commands": [],
        "examples": ['lab "server watchdog"'],
    },
    "service_recover_v1": {
        "summary": "Recover or restart the server depending on the request.",
        "underlying_commands": [
            "bash infra/user/server-status",
            "bash infra/user/server-start",
            "bash infra/user/server-restart",
        ],
        "help_commands": ["bash infra/user/server-restart --help"],
        "examples": ['lab "recover service"', 'lab "server restart"'],
    },
    "safe_maintenance_v1": {
        "summary": "Run the scheduler watchdog audit path.",
        "underlying_commands": ["python infra/bin/scheduler_watchdog.py --mode audit"],
        "help_commands": ["python infra/bin/scheduler_watchdog.py --help"],
        "examples": ['lab "run safe maintenance"'],
    },
    "scheduler_inspect_v1": {
        "summary": "Inspect scheduler health through the operator wrapper.",
        "underlying_commands": ["bash infra/user/scheduler-health"],
        "help_commands": ["bash infra/user/scheduler-health --help"],
        "examples": ['lab "inspect scheduler"'],
    },
    "scheduler_recover_v1": {
        "summary": "Run the scheduler recovery flow.",
        "underlying_commands": ["python infra/bin/scheduler_watchdog.py --mode selfheal"],
        "help_commands": ["python infra/bin/scheduler_watchdog.py --help", "bash infra/user/scheduler-recover"],
        "examples": ['lab "recover scheduler"'],
    },
    "scheduler_disable_all_v1": {
        "summary": "Disable all scheduled jobs without killing in-flight work.",
        "underlying_commands": ["bash infra/user/scheduler-disable-all"],
        "help_commands": [],
        "examples": ['lab "scheduler disable all jobs"'],
    },
    "scheduler_enable_all_v1": {
        "summary": "Enable all scheduled jobs.",
        "underlying_commands": ["bash infra/user/scheduler-enable-all"],
        "help_commands": [],
        "examples": ['lab "scheduler enable all jobs"'],
    },
    "scheduler_pause_v1": {
        "summary": "Pause the scheduler pipeline safely.",
        "underlying_commands": ["bash infra/user/scheduler-pause"],
        "help_commands": [],
        "examples": ['lab "scheduler pause"'],
    },
    "scheduler_watch_v1": {
        "summary": "Open the live scheduler dashboard.",
        "underlying_commands": ["bash infra/user/scheduler-watch --interval 2 --pipeline-limit 40"],
        "help_commands": [],
        "examples": ['lab "scheduler watch"'],
    },
    "scheduler_resume_v1": {
        "summary": "Resume the scheduler pipeline safely.",
        "underlying_commands": ["bash infra/user/scheduler-resume"],
        "help_commands": [],
        "examples": ['lab "scheduler resume"'],
    },
    "patch_ingest_repair_v1": {
        "summary": "Apply or repair an incoming patch through the deterministic patch path.",
        "underlying_commands": ["patch_ingest_repair_v1 via the plane"],
        "help_commands": [],
        "examples": ['lab "apply this patch" --patch /path/to/change.patch', 'lab "fix this patch"'],
    },
    "patch_apply_chain_v1": {
        "summary": "Run the bounded patch apply chain with verify/recovery behavior.",
        "underlying_commands": ["patch_apply_chain_v1 via the plane"],
        "help_commands": [],
        "examples": ['lab "repair patch"', 'lab "fix this patch"'],
    },
    "pis_assess_proof_gap_v1": {
        "summary": "Assess PIS proof and closeability evidence.",
        "underlying_commands": ["pis_assess_proof_gap_v1 via the plane"],
        "help_commands": [],
        "examples": ['lab "assess item" --target-pis-uid PIS-...', 'lab "gather proof" --target-pis-uid PIS-...'],
    },
    "build_verify_loop_v1": {
        "summary": "Run bounded build/verify/recovery loops for job-style work.",
        "underlying_commands": ["build_verify_loop_v1 via the plane"],
        "help_commands": [],
        "examples": ['lab "recover job" --job-id JOB-...', 'lab "continue interrupted work" --job-id JOB-...'],
    },
    "job_inspect_bounded_recovery_v1": {
        "summary": "Inspect one job and perform bounded deterministic recovery when supported by the phase-1 job path.",
        "underlying_commands": ["job_inspect_bounded_recovery_v1 via the plane"],
        "help_commands": [],
        "examples": ['lab "inspect job" --job-id JOB-...'],
    },
    "status_reconcile_v1": {
        "summary": "Reconcile status using current evidence and validation context.",
        "underlying_commands": ["status_reconcile_v1 via the plane"],
        "help_commands": [],
        "examples": ['lab "verify outcome" --current-status running --expected-status blocked'],
    },
    "implementation_validate_v1": {
        "summary": "Validate an implementation deterministically when the plane can resolve a concrete target or validation context.",
        "underlying_commands": ["implementation_validate_v1 via the plane"],
        "help_commands": [],
        "examples": [
            'lab "validate the implementation for PIS-20260405-041630Z-B8E2"',
            'lab "is this implementation correct for PIS-20260405-041630Z-B8E2"',
            'lab "validate the implementation" --target-uid PIS-20260405-041630Z-B8E2',
        ],
    },
    "blocked_chain_explain_and_recommend_v1": {
        "summary": "Explain a blocked state and recommend the next safest action.",
        "underlying_commands": ["blocked_chain_explain_and_recommend_v1 via the plane"],
        "help_commands": [],
        "examples": ['lab "explain blocked state" --report-file /path/to/report.json'],
    },
    "implementation_prepare_v1": {
        "summary": "List the next implementation candidates from the ledger.",
        "underlying_commands": ["python -m tools.bin.intent_sheet_next --limit 5"],
        "help_commands": ["python -m tools.bin.intent_sheet_next -h"],
        "examples": [
            'lab "prepare implementation"',
            'lab "what is the next intent sheet to implement?"',
            'lab "what are the next intent sheets to implement?"',
        ],
    },
    "implementation_prepare_pis_v1": {
        "summary": "List the next PIS candidates to implement from the ledger.",
        "underlying_commands": ["python -m tools.bin.pis_next --limit 5"],
        "help_commands": ["python -m tools.bin.pis_next -h"],
        "examples": ['lab "what are the next PISes to implement?"', 'lab "next pises to implement"'],
    },
    "implement_recursive_v1": {
        "summary": "Start the recursive implementation chain for a target UID using the default build-verify-repair contract.",
        "underlying_commands": [
            "python -m tools.bin.implement_recursive --uid <UID>",
            "python -m tools.bin.implement_recursive --uid next_pis",
            "python -m tools.bin.implement_recursive --uid next_intent",
        ],
        "argument_lines": [
            "--plan-only",
            "--decompose-only",
            "--max-children <N>",
            "--parallel safe|off",
            "--proof-profile focused|full",
            "--close auto|manual",
            "--start-now true|false",
        ],
        "help_commands": ["python -m tools.bin.implement_recursive -h"],
        "examples": [
            'lab "implement IS-20260403-212913Z-5817"',
            'lab "implement u4e2"',
            'lab "implement canonical-job-taxonomy"',
            'lab "implement next PIS"',
            'lab "implement next intent"',
            'lab "implement u4e2" --plan-only',
            'lab "implement next PIS" --proof-profile full --close manual',
        ],
    },
    "backlog_inspect_v1": {
        "summary": "Inspect the active backlog.",
        "underlying_commands": ["python -m tools.bin.intent_sheet_next --limit 10"],
        "help_commands": ["python -m tools.bin.intent_sheet_next -h"],
        "examples": ['lab "inspect backlog"', 'lab "prepare implementation"'],
    },
    "repo_import_pis_zip_v1": {
        "summary": "Import a PIS bundle zip.",
        "underlying_commands": ["python -m tools.bin.pis_import_zip --zip-path <path>"],
        "help_commands": ["python -m tools.bin.pis_import_zip -h"],
        "examples": ['lab "integrate zipped PISes"', 'lab "import pis zip" --zip-path /path/to/bundle.zip'],
    },
    "repo_import_intent_sheet_zip_v1": {
        "summary": "Import an intent-sheet bundle zip.",
        "underlying_commands": ["python -m tools.bin.intent_sheet_import_zip --zip-path <path>"],
        "help_commands": ["python -m tools.bin.intent_sheet_import_zip -h"],
        "examples": ['lab "import intent sheet zip"', 'lab "import intent sheet zip" --zip-path /path/to/bundle.zip'],
    },
}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _use_color() -> bool:
    return sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _color(text: str, *codes: str) -> str:
    if not _use_color():
        return text
    prefix = "".join(codes)
    return f"{prefix}{text}{_ANSI_RESET}"


def _color_state(value: str) -> str:
    text = str(value or "REVIEW_REQUIRED")
    upper = text.upper()
    if upper == "PASS":
        return _color(text, _ANSI_BOLD, _ANSI_GREEN)
    if upper in {"BLOCKED", "DEGRADED", "REVIEW_REQUIRED"}:
        return _color(text, _ANSI_BOLD, _ANSI_YELLOW if upper == "DEGRADED" else _ANSI_RED)
    if upper == "RUNNING":
        return _color(text, _ANSI_BOLD, _ANSI_CYAN)
    return _color(text, _ANSI_BOLD, _ANSI_MAGENTA)


def _color_confidence(value: str) -> str:
    text = str(value or "Unknown")
    low = text.lower()
    if low == "high":
        return _color(text, _ANSI_GREEN)
    if low == "medium":
        return _color(text, _ANSI_YELLOW)
    if low == "low":
        return _color(text, _ANSI_RED)
    return _color(text, _ANSI_DIM)


def _read_prompt(args_prompt: Sequence[str]) -> str:
    if args_prompt:
        return " ".join(str(x) for x in args_prompt).strip()
    if not sys.stdin.isatty():
        return sys.stdin.read().strip()
    return ""


def _split_chained_prompt(prompt: str) -> List[str]:
    text = str(prompt or "").strip()
    lowered = text.lower()
    if lowered.startswith("do "):
        text = text[3:].strip()
    normalized = re.sub(r"\s+and then\s+", " then ", text, flags=re.IGNORECASE)
    normalized = re.sub(r"\s*,\s*then\s+", " then ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+after that[:,]?\s+", " then ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\s+then do this:\s+", " then ", normalized, flags=re.IGNORECASE)
    normalized = re.sub(r"\.\s*then\s+", " then ", normalized, flags=re.IGNORECASE)
    if " then " in normalized.lower():
        normalized = re.sub(r"\s*,\s*", " then ", normalized)
    parts = [part.strip() for part in re.split(r"\s+then\s+", normalized, flags=re.IGNORECASE) if part.strip()]
    if len(parts) <= 1:
        return [str(prompt or "").strip()] if str(prompt or "").strip() else []
    context_text = normalized.lower()
    contextualized: List[str] = []
    for part in parts:
        part = re.sub(r"^\s*lab>\s*", "", part, flags=re.IGNORECASE).strip()
        part_low = part.lower().strip()
        if part_low in {"show me status", "show status", "status", "check status", "check the status", "check it", "check them"}:
            if "server" in context_text or "webui" in context_text or "runtime" in context_text:
                part = "server status"
            elif "scheduler" in context_text:
                part = "inspect scheduler"
        elif part_low in {"resume it", "resume", "resume them"} and "scheduler" in context_text:
            part = "scheduler resume"
        elif part_low in {"pause it", "pause"} and "scheduler" in context_text:
            part = "scheduler pause"
        elif part_low in {"restart it", "restart"} and ("server" in context_text or "webui" in context_text or "runtime" in context_text):
            part = "server restart"
        contextualized.append(part.strip())
    parts = [part for part in contextualized if part]
    return parts


def _is_help_prompt(prompt: str) -> bool:
    lowered = str(prompt or "").strip().lower()
    return lowered in {"help", "--help", "-h"}


def _help_topic(prompt: str) -> str:
    text = str(prompt or "").strip()
    low = text.lower()
    if low.startswith("help "):
        return text[5:].strip()
    return ""


def _strip_force_codex(prompt: str) -> tuple[str, bool]:
    text = str(prompt or "").strip()
    if not text:
        return "", False
    patterns = [
        r"^\s*force codex[:\s,-]*",
        r"\s+force codex\s*$",
        r"\s*\(force codex\)\s*",
    ]
    updated = text
    forced = False
    for pattern in patterns:
        next_text, count = re.subn(pattern, " ", updated, flags=re.IGNORECASE)
        if count:
            updated = next_text
            forced = True
    updated = re.sub(r"\s+", " ", updated).strip()
    return updated or text, forced


def _print_help(parser: argparse.ArgumentParser) -> None:
    parser.print_help(sys.stdout)
    print("")
    print("Examples:")
    print('  lab "fix this patch"')
    print('  lab "commit"')
    print('  lab "archive debug"')
    print('  lab "do commit then archive debug then archive weekly"')
    print('  lab --plan "verify outcome" --current-status running --expected-status blocked')
    catalog = build_operator_catalog_v1(repo_root=_repo_root(), contract_repo_root=_repo_root())
    verbs = sorted(
        {
            str(row.get("operator_verb") or "").strip()
            for row in list(catalog.get("supported_surface_verbs") or [])
            if str(row.get("operator_verb") or "").strip()
        }
    )
    if verbs:
        print("")
        print("Known verbs:")
        for verb in verbs:
            print(f"  - {verb}")
        print("")
    print("Built-ins and modifiers:")
    print("  - help")
    print("  - help <topic>")
    print("  - do <request> then <request> [then ...]")
    print('  - force codex <request>')
    print("")
    print('Verb help: `lab "help archive"` or inside labsh: `help inspect`')


def _catalog_aliases_by_action(catalog: Mapping[str, Any]) -> Dict[str, List[str]]:
    aliases: Dict[str, List[str]] = {}
    for row in list(catalog.get("supported_surface_verbs") or []):
        action_id = str(row.get("action_id") or "").strip()
        verb = str(row.get("operator_verb") or "").strip()
        if not action_id or not verb:
            continue
        aliases.setdefault(action_id, []).append(verb)
    for action_id in list(aliases):
        aliases[action_id] = sorted(set(aliases[action_id]))
    return aliases


def _resolve_help_topic(topic: str, catalog: Mapping[str, Any]) -> tuple[list[str], list[str]]:
    cleaned = str(topic or "").strip().lower()
    if not cleaned:
        return [], []
    aliases_by_action = _catalog_aliases_by_action(catalog)
    exact_actions = sorted(
        {
            action_id
            for action_id, aliases in aliases_by_action.items()
            if cleaned == action_id.lower() or cleaned in {alias.lower() for alias in aliases}
        }
    )
    if exact_actions:
        matched_verbs = sorted({verb for action_id in exact_actions for verb in aliases_by_action.get(action_id, [])})
        return exact_actions, matched_verbs
    partial_actions = sorted(
        {
            action_id
            for action_id, aliases in aliases_by_action.items()
            if any(cleaned in alias.lower() for alias in aliases) or cleaned in action_id.lower()
        }
    )
    matched_verbs = sorted({verb for action_id in partial_actions for verb in aliases_by_action.get(action_id, [])})
    return partial_actions, matched_verbs


def _print_help_topic(topic: str) -> int:
    catalog = build_operator_catalog_v1(repo_root=_repo_root(), contract_repo_root=_repo_root())
    action_ids, matched_verbs = _resolve_help_topic(topic, catalog)
    if not action_ids:
        print(f'No known verb matched "{topic}".')
        print('Use `lab help` to list the current surface.')
        return 1
    if len(action_ids) > 1:
        print(f'Multiple verbs match "{topic}":')
        for verb in matched_verbs:
            print(f"  - {verb}")
        print("")
        print('Be more specific, for example: `help inspect scheduler` or `help inspect job`.')
        return 0
    action_id = action_ids[0]
    info = dict(_ACTION_HELP.get(action_id) or {})
    aliases = _catalog_aliases_by_action(catalog).get(action_id, [])
    print(f"Verb Help: {topic}")
    print(f"Action: {action_id}")
    if info.get("summary"):
        print(f"Summary: {info['summary']}")
    if aliases:
        print("Known forms:")
        for verb in aliases:
            print(f"  - {verb}")
    commands = list(info.get("underlying_commands") or [])
    if commands:
        print("Underlying commands:")
        for command in commands:
            print(f"  - {command}")
    help_commands = list(info.get("help_commands") or [])
    argument_lines = list(info.get("argument_lines") or [])
    if argument_lines:
        print("Supported arguments:")
        for line in argument_lines:
            print(f"  - {line}")
    if help_commands:
        print("Argument help:")
        for command in help_commands:
            print(f"  - {command}")
    examples = list(info.get("examples") or [])
    if examples:
        print("Examples:")
        for example in examples:
            print(f"  - {example}")
    return 0


def _find_patch_candidates(cwd: Path) -> List[Path]:
    candidates: List[Path] = []
    for pattern in ("*.patch", "*.diff"):
        for path in sorted(cwd.glob(pattern)):
            if path.is_file():
                candidates.append(path.resolve())
    # stable de-dup preserving order
    seen = set()
    unique: List[Path] = []
    for path in candidates:
        text = str(path)
        if text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return unique


def _default_patch_search_roots(cwd: Path) -> List[Path]:
    roots: List[Path] = [cwd]
    downloads = Path("/mnt/c/home/user/Downloads")
    if downloads != cwd:
        roots.append(downloads)
    return roots


def _default_pis_zip_search_roots(cwd: Path) -> List[Path]:
    downloads = Path("/mnt/c/home/user/Downloads")
    roots: List[Path] = [downloads]
    if cwd != downloads:
        roots.append(cwd)
    return roots


def _find_patch_candidates_in_roots(roots: Sequence[Path]) -> List[Path]:
    candidates: List[Path] = []
    for root in roots:
        candidates.extend(_find_patch_candidates(root))
    seen = set()
    unique: List[Path] = []
    for path in candidates:
        text = str(path)
        if text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return unique


def _find_zip_candidates(cwd: Path) -> List[Path]:
    candidates: List[Path] = []
    for pattern in ("*.zip", "*.ZIP"):
        for path in sorted(cwd.glob(pattern)):
            if path.is_file():
                candidates.append(path.resolve())
    seen = set()
    unique: List[Path] = []
    for path in candidates:
        text = str(path)
        if text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return unique


def _find_pis_zip_candidates_in_roots(prompt: str, roots: Sequence[Path]) -> List[Path]:
    lowered = str(prompt or "").lower()
    candidates: List[Path] = []
    for root in roots:
        for path in _find_zip_candidates(root):
            name = path.name.lower()
            if "pis" in lowered:
                if "pis" in name:
                    candidates.append(path)
            else:
                candidates.append(path)
    seen = set()
    unique: List[Path] = []
    for path in candidates:
        text = str(path)
        if text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return unique


def _find_intent_sheet_zip_candidates_in_roots(prompt: str, roots: Sequence[Path]) -> List[Path]:
    lowered = str(prompt or "").lower()
    candidates: List[Path] = []
    for root in roots:
        for path in _find_zip_candidates(root):
            name = path.name.lower()
            if "intent" in lowered or "sheet" in lowered:
                if "intent" in name or "sheet" in name:
                    candidates.append(path)
            else:
                candidates.append(path)
    seen = set()
    unique: List[Path] = []
    for path in candidates:
        text = str(path)
        if text in seen:
            continue
        seen.add(text)
        unique.append(path)
    return unique


def _prompt_requires_patch_artifact(prompt: str) -> bool:
    lowered = str(prompt or "").lower()
    if not any(term in lowered for term in ("patch", "diff")):
        return False
    return any(
        term in lowered
        for term in (
            "apply this patch",
            "apply patch",
            "apply incoming patch",
            "fix this patch",
            "repair this patch",
            "repair patch",
        )
    )


def _prompt_requires_zip_artifact(prompt: str) -> bool:
    lowered = str(prompt or "").lower()
    if "zip" not in lowered and "downloads" not in lowered and "download" not in lowered:
        return False
    return any(term in lowered for term in ("import", "integrate"))


def _maybe_autodetect_patch(prompt: str, explicit_patch: str, cwd: Path) -> str:
    if str(explicit_patch or "").strip():
        return str(Path(explicit_patch).expanduser().resolve())
    lowered = str(prompt or "").lower()
    if "patch" not in lowered and "diff" not in lowered:
        return ""
    candidates = _find_patch_candidates_in_roots(_default_patch_search_roots(cwd))
    if len(candidates) == 1:
        return str(candidates[0])
    return ""


def _patch_detection_error(prompt: str, explicit_patch: str, cwd: Path) -> str:
    if str(explicit_patch or "").strip():
        return ""
    if not _prompt_requires_patch_artifact(prompt):
        return ""
    candidates = _find_patch_candidates_in_roots(_default_patch_search_roots(cwd))
    if not candidates:
        return "No patch file was provided and no single .patch/.diff file was found in the current directory or /mnt/c/home/user/Downloads. Use --patch <path>."
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates[:5])
        suffix = "" if len(candidates) <= 5 else ", ..."
        return f"Multiple patch files were found across the current directory and /mnt/c/home/user/Downloads ({names}{suffix}). Use --patch <path>."
    return ""


def _maybe_autodetect_zip(prompt: str, explicit_zip: str, cwd: Path) -> str:
    if str(explicit_zip or "").strip():
        return str(Path(explicit_zip).expanduser().resolve())
    lowered = str(prompt or "").lower()
    if not any(term in lowered for term in ("zip", "downloads", "download", "import", "integrate")):
        return ""
    roots = _default_pis_zip_search_roots(cwd)
    if "intent" in lowered or "sheet" in lowered:
        candidates = _find_intent_sheet_zip_candidates_in_roots(prompt, roots)
    elif "pis" in lowered:
        candidates = _find_pis_zip_candidates_in_roots(prompt, roots)
    else:
        return ""
    if len(candidates) == 1:
        return str(candidates[0])
    return ""


def _zip_detection_error(prompt: str, explicit_zip: str, cwd: Path) -> str:
    if str(explicit_zip or "").strip():
        return ""
    lowered = str(prompt or "").lower()
    if not _prompt_requires_zip_artifact(prompt):
        return ""
    if "intent" in lowered or "sheet" in lowered:
        noun = "Intent Sheet"
        candidates = _find_intent_sheet_zip_candidates_in_roots(prompt, _default_pis_zip_search_roots(cwd))
    elif "pis" in lowered:
        noun = "PIS"
        candidates = _find_pis_zip_candidates_in_roots(prompt, _default_pis_zip_search_roots(cwd))
    else:
        return ""
    if not candidates:
        return f"No {noun} zip was provided and no single matching .zip file was found in the current directory or /mnt/c/home/user/Downloads. Use --zip-path <path>."
    if len(candidates) > 1:
        names = ", ".join(path.name for path in candidates[:5])
        suffix = "" if len(candidates) <= 5 else ", ..."
        return f"Multiple {noun} zip files were found across the current directory and /mnt/c/home/user/Downloads ({names}{suffix}). Use --zip-path <path>."
    return ""


def _infer_authority_scope(prompt: str, payload: Mapping[str, Any]) -> str:
    lowered = str(prompt or "").lower()
    if str(payload.get("authority_scope") or "").strip():
        return str(payload["authority_scope"]).strip()
    if str(payload.get("patch_path") or "").strip():
        return "repo_mutation"
    if str(payload.get("zip_path") or "").strip():
        return "repo_mutation"
    if any(term in lowered for term in ("recover scheduler",)):
        return "scheduler_mutation"
    if any(term in lowered for term in ("recover service", "refresh runtime", "restart runtime", "restart server")):
        return "runtime_mutation"
    if any(
        key in payload
        for key in (
            "report",
            "snapshot",
            "receipts",
            "watchdog_summary",
            "iterations",
            "fallback_events",
            "current_status",
            "expected_status",
        )
    ):
        return "read_only"
    return "read_only"


def _load_json_file(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _infer_target_uid_from_prompt(prompt: str) -> str:
    match = re.search(r"\b((?:IS|PIS)-\d{8}-\d{6}Z-[A-Z0-9]{4})\b", str(prompt or ""), flags=re.IGNORECASE)
    if match:
        return str(match.group(1) or "").upper()
    next_safe_match = re.match(
        r"^\s*implement\s+(?:the\s+)?next\s+safe\s+intent(?:\s+sheet)?\s*(?:please)?\s*[?.!]*\s*$",
        str(prompt or ""),
        flags=re.IGNORECASE,
    )
    if next_safe_match:
        return "next_safe_intent"
    next_match = re.match(
        r"^\s*implement\s+(?:the\s+)?next\s+(pis|intent|intent sheet)\s*(?:please)?\s*[?.!]*\s*$",
        str(prompt or ""),
        flags=re.IGNORECASE,
    )
    if next_match:
        kind = str(next_match.group(1) or "").strip().lower()
        if kind == "pis":
            return "next_pis"
        return "next_intent"
    implement_match = re.match(r"^\s*implement\s+([A-Za-z0-9][A-Za-z0-9._:-]*)\s*$", str(prompt or ""), flags=re.IGNORECASE)
    if not implement_match:
        return ""
    return str(implement_match.group(1) or "").strip()


def _build_payload(args: argparse.Namespace, prompt: str, cwd: Path) -> Dict[str, Any]:
    request_text, force_codex = _strip_force_codex(prompt)
    payload: Dict[str, Any] = {"request_text": request_text}
    if args.task_id:
        payload["task_id"] = str(args.task_id).strip()
    patch_path = _maybe_autodetect_patch(prompt, args.patch, cwd)
    if patch_path:
        payload["patch_path"] = patch_path
    zip_path = _maybe_autodetect_zip(prompt, args.zip_path, cwd)
    if zip_path:
        payload["zip_path"] = zip_path
    if args.target_pis_uid:
        payload["target_pis_uid"] = str(args.target_pis_uid).strip()
    if args.job_id:
        payload["job_id"] = str(args.job_id).strip()
    if args.target_uid:
        payload["target_uid"] = str(args.target_uid).strip()
    elif _infer_target_uid_from_prompt(prompt):
        payload["target_uid"] = _infer_target_uid_from_prompt(prompt)
    if args.report_file:
        payload["report"] = _load_json_file(args.report_file)
    if args.snapshot_file:
        payload["snapshot"] = _load_json_file(args.snapshot_file)
    if args.receipts_file:
        payload["receipts"] = _load_json_file(args.receipts_file)
    if args.watchdog_file:
        payload["watchdog_summary"] = _load_json_file(args.watchdog_file)
    if args.iterations_file:
        payload["iterations"] = _load_json_file(args.iterations_file)
    if args.fallback_events_file:
        payload["fallback_events"] = _load_json_file(args.fallback_events_file)
    if args.current_status:
        payload["current_status"] = str(args.current_status).strip()
    if args.expected_status:
        payload["expected_status"] = str(args.expected_status).strip()
    if args.verifier_verdict:
        payload["verifier_verdict"] = str(args.verifier_verdict).strip()
    if args.allow_fallback:
        payload["allow_fallback"] = True
    if args.allow_recovery:
        payload["allow_recovery"] = True
    if force_codex:
        payload["force_codex"] = True
        payload["allow_fallback"] = True
    if bool(getattr(args, "plan_only", False)):
        payload["plan_only"] = True
    if bool(getattr(args, "decompose_only", False)):
        payload["decompose_only"] = True
    if str(getattr(args, "max_children", "") or "").strip():
        payload["max_children"] = int(args.max_children)
    if str(getattr(args, "parallel", "") or "").strip():
        payload["parallel"] = str(args.parallel).strip()
    if str(getattr(args, "proof_profile", "") or "").strip():
        payload["proof_profile"] = str(args.proof_profile).strip()
    if str(getattr(args, "close_mode", "") or "").strip():
        payload["close_mode"] = str(args.close_mode).strip()
    if str(getattr(args, "start_now", "") or "").strip():
        payload["start_now"] = str(args.start_now).strip().lower() != "false"
    if args.validation_command:
        cleaned = [str(x).strip() for x in list(args.validation_command) if str(x).strip()]
        if cleaned:
            payload["validation_commands"] = cleaned
            payload["validation_commands_executed"] = cleaned
    authority_scope = _infer_authority_scope(prompt, payload)
    payload["authority_scope"] = authority_scope
    payload["authority_class"] = authority_scope
    return payload


def _read_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError:
        return ""


def _is_intent_implementation_request(prompt: str, payload: Mapping[str, Any]) -> bool:
    target_uid = str(payload.get("target_uid") or "").strip().upper()
    if target_uid in {"NEXT_INTENT", "NEXT_SAFE_INTENT"}:
        return True
    if target_uid.startswith("IS-"):
        return True
    return bool(
        re.match(
            r"^\s*implement\s+",
            str(prompt or ""),
            flags=re.IGNORECASE,
        )
    )


def _intent_safety_row(target_uid: str) -> Dict[str, Any]:
    rows = _intent_sheet_next.next_sheets(repo_root=Path(__file__).resolve().parents[2], limit=5000)
    target = str(target_uid or "").strip().upper()
    if target == "NEXT_INTENT":
        return dict(rows[0]) if rows else {}
    if target == "NEXT_SAFE_INTENT":
        for row in rows:
            safety = row.get("parallel_safety") if isinstance(row.get("parallel_safety"), dict) else {}
            if bool(safety.get("safe")) and str(safety.get("reason") or "").strip() != "resume_existing_family":
                return dict(row)
        return {}
    for row in rows:
        if str(row.get("uid") or "").strip().upper() == target:
            return dict(row)
    return {}


def _implementation_launch_confirmation(
    *,
    prompt: str,
    payload: Mapping[str, Any],
    plan_mode: bool,
) -> bool:
    if plan_mode or not _is_intent_implementation_request(prompt, payload):
        return True
    row = _intent_safety_row(str(payload.get("target_uid") or ""))
    safety = row.get("parallel_safety") if isinstance(row.get("parallel_safety"), dict) else {}
    title = str(row.get("title") or payload.get("target_uid") or "").strip()
    uid = str(row.get("uid") or payload.get("target_uid") or "").strip().upper()
    why = _intent_sheet_next._human_safety_reason(safety) if isinstance(safety, dict) else "unknown"
    if bool(safety.get("safe")):
        print(f"Safe to execute this job, executing now: {uid} | {title}")
        return True
    print(f"Not safe to execute the job now because of {why}.")
    answer = _read_input("Please confirm you want to proceed anyway: [Y/n] ").strip().lower()
    return answer in {"", "y", "yes"}


def _builtin_result(
    *,
    prompt: str,
    mode: str,
    kind: str,
    summary: str,
    topic: str = "",
    next_label: str = "",
    state: str = "PASS",
    confidence: str = "High",
) -> Dict[str, Any]:
    action_id = f"front_door_{kind}_v1"
    resolved_next_label = next_label.strip() or ("Show help output" if kind.startswith("help") else "Front-door action completed")
    return {
        "schema_version": "plane_operator_result_v1",
        "surface_name": "the plane",
        "request_text": prompt,
        "mode": mode,
        "classification": {
            "schema_version": "plane_request_classification_v1",
            "surface_name": "the plane",
            "request_text": prompt,
            "selection": {
                "action_id": action_id,
                "operator_verb": topic or kind,
                "confidence_bucket": "explicit",
                "matched_rule_id": f"front_door_{kind}",
                "fallback_eligibility_class": "none",
                "required_evidence_gatherers": [],
                "supported": True,
            },
        },
        "command_parse": {
            "schema_version": "plane_command_parse_v1",
            "surface_name": "the plane",
            "request_text": prompt,
            "selected_action_id": action_id,
            "selected_operator_verb": topic or kind,
            "confidence_bucket": "explicit",
            "matched_rule_id": f"front_door_{kind}",
            "supported": True,
            "execution_shape": "front_door_builtin",
            "would_call_commands": [],
            "summary": summary,
            "agreement_with_final_selection": True,
            "final_action_id": action_id,
        },
        "selection": {
            "action_id": action_id,
            "operator_verb": topic or kind,
            "confidence_bucket": "explicit",
            "matched_rule_id": f"front_door_{kind}",
            "fallback_eligibility_class": "none",
            "required_evidence_gatherers": [],
            "supported": True,
        },
        "supported": True,
        "summary": summary,
        "operator_surface": {
            "executive_surface": {
                "state": state,
                "primary_next_action": {"label": resolved_next_label},
                "confidence": confidence,
                "short_reason": summary,
            }
        },
    }


def execute_front_door_prompt(
    args: argparse.Namespace,
    *,
    prompt: str,
    cwd: Path,
    repo_root: Path,
    contract_repo_root: Path,
) -> tuple[list[Dict[str, Any]], int]:
    if _is_help_prompt(prompt):
        return ([_builtin_result(prompt=prompt, mode="front_door", kind="help", summary="Front-door help output.")], 0)
    topic = _help_topic(prompt)
    if topic:
        return ([_builtin_result(prompt=prompt, mode="front_door", kind="help_topic", summary=f'Front-door help topic "{topic}".', topic=topic)], 0)
    prompt_steps = _split_chained_prompt(prompt)
    if not prompt_steps:
        raise ValueError("no_prompt_steps")
    results: List[Dict[str, Any]] = []
    exit_code = 0
    total_steps = len(prompt_steps)
    for index, step_prompt in enumerate(prompt_steps, start=1):
        if not args.plan:
            patch_error = _patch_detection_error(step_prompt, args.patch, cwd)
            if patch_error:
                raise ValueError(patch_error)
            zip_error = _zip_detection_error(step_prompt, args.zip_path, cwd)
            if zip_error:
                raise ValueError(zip_error)
        payload = _build_payload(args, step_prompt, cwd)
        payload["sequence_index"] = index
        payload["sequence_total"] = total_steps
        payload["sequence_full_prompt"] = prompt
        if not args.plan and not args.no_fallback:
            payload["allow_fallback"] = True
        elif args.no_fallback:
            payload["allow_fallback"] = False
        if not args.plan and not args.json:
            payload["stream_subprocess_output"] = True
        if not args.plan and not _implementation_launch_confirmation(prompt=step_prompt, payload=payload, plan_mode=bool(args.plan)):
            result = _builtin_result(
                prompt=step_prompt,
                mode="execute",
                kind="implementation_launch_canceled",
                summary="Implementation launch canceled by operator.",
                next_label="No action taken",
            )
            result["front_door_payload"] = dict(payload)
            result["chain_index"] = index
            result["chain_total"] = total_steps
            results.append(result)
            break
        try:
            result = (
                plan_operator_request_v1(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
                if args.plan
                else execute_operator_request_v1(payload, repo_root=repo_root, contract_repo_root=contract_repo_root)
            )
        except Exception as exc:
            result = {
                "schema_version": "plane_operator_result_v1",
                "surface_name": "the plane",
                "request_text": step_prompt,
                "mode": "plan" if args.plan else "execute",
                "supported": False,
                "summary": str(exc).strip() or "request failed",
                "operator_surface": {
                    "executive_surface": {
                        "state": "REVIEW_REQUIRED",
                        "primary_next_action": {"label": "Manual review required"},
                        "confidence": "Low",
                        "short_reason": str(exc).strip() or "request failed",
                    }
                },
            }
        result = dict(result)
        result["front_door_payload"] = dict(payload)
        result["chain_index"] = index
        result["chain_total"] = total_steps
        results.append(result)
        if not bool(result.get("supported", True)):
            exit_code = 1
            break
    return results, exit_code


def _render_human(result: Mapping[str, Any], *, verbose: bool = False) -> str:
    operator_surface = dict(result.get("operator_surface") or {})
    command_parse = dict(result.get("command_parse") or {})
    executive = dict(operator_surface.get("executive_surface") or {})
    explanation = dict(operator_surface.get("explanation") or operator_surface.get("primary_explanation") or {})
    uncertainty = dict(operator_surface.get("uncertainty") or operator_surface.get("proof_uncertainty") or {})
    recommendations = dict(operator_surface.get("recommendations") or {})
    primary = dict(recommendations.get("primary") or {})
    codex = dict(operator_surface.get("codex_transparency") or result.get("codex_fallback_request") or {})
    execution = dict(result.get("execution") or {})

    lines: List[str] = []
    if command_parse:
        parsed_action = str(command_parse.get("selected_action_id") or "no deterministic candidate").strip()
        lines.append(f"{_color('COMMPARSE:', _ANSI_BOLD, _ANSI_MAGENTA)} {_color(parsed_action, _ANSI_BOLD)}")
        parse_shape = str(command_parse.get("execution_shape") or "").strip()
        parse_summary = str(command_parse.get("summary") or "").strip()
        if parse_shape:
            lines.append(f"{_color('PARSE SHAPE:', _ANSI_BOLD, _ANSI_MAGENTA)} {parse_shape}")
        if parse_summary:
            lines.append(f"{_color('PARSE REASON:', _ANSI_BOLD, _ANSI_MAGENTA)} {parse_summary}")
        commands = [str(x).strip() for x in list(command_parse.get("would_call_commands") or []) if str(x).strip()]
        if commands:
            label = "PARSE COMMAND" if len(commands) == 1 else "PARSE COMMANDS"
            lines.append(f"{_color(label + ':', _ANSI_BOLD, _ANSI_MAGENTA)}")
            for command in commands:
                lines.append(f"  {command}")
        agreement = command_parse.get("agreement_with_final_selection")
        if agreement is not None:
            lines.append(f"{_color('PARSE AGREEMENT:', _ANSI_BOLD, _ANSI_MAGENTA)} {'yes' if bool(agreement) else 'no'}")
        disagreement_summary = str(command_parse.get("disagreement_summary") or "").strip()
        if disagreement_summary:
            lines.append(f"{_color('PARSE DISAGREEMENT:', _ANSI_BOLD, _ANSI_MAGENTA)} {disagreement_summary}")
        lines.append("")
    state = str(executive.get("state") or operator_surface.get("status_class") or "REVIEW_REQUIRED")
    lines.append(f"{_color('STATE:', _ANSI_BOLD, _ANSI_CYAN)} {_color_state(state)}")
    next_label = str((executive.get("primary_next_action") or {}).get("label") or primary.get("label") or primary.get("action_id") or "Manual review required")
    lines.append(f"{_color('NEXT ACTION:', _ANSI_BOLD, _ANSI_CYAN)} {_color(next_label, _ANSI_BOLD)}")
    confidence = str(executive.get("confidence") or uncertainty.get("confidence_bucket") or "Unknown")
    lines.append(f"{_color('CONFIDENCE:', _ANSI_BOLD, _ANSI_CYAN)} {_color_confidence(confidence)}")
    short_reason = str(executive.get("short_reason") or explanation.get("summary") or result.get("summary") or "").strip()
    if short_reason:
        lines.append(f"{_color('REASON:', _ANSI_BOLD, _ANSI_CYAN)} {short_reason}")
    codex_used = bool(codex.get("codex_used"))
    codex_result = str(execution.get("stdout") or "").strip()
    if codex_used and codex_result:
        lines.append("")
        lines.append(_color("CODEX RESULT:", _ANSI_BOLD, _ANSI_MAGENTA))
        lines.append(_color(codex_result, _ANSI_MAGENTA))
    if not verbose:
        lines.append("")
        lines.append(_color(_TURN_DELIMITER, _ANSI_DIM))
        return "\n".join(lines)

    why: List[str] = []
    if explanation.get("summary"):
        why.append(str(explanation.get("summary")))
    primary_reason = str(explanation.get("primary_reason") or "").strip()
    if primary_reason and primary_reason not in why:
        why.append(primary_reason)
    rec_reason = str(primary.get("reason") or "").strip()
    if rec_reason and rec_reason not in why:
        why.append(rec_reason)
    if why:
        lines.append("")
        lines.append(_color("WHY:", _ANSI_BOLD, _ANSI_CYAN))
        for item in why:
            lines.append(f"- {item}")

    unresolved = [str(x).strip() for x in list(uncertainty.get("unresolved_items") or []) if str(x).strip()]
    if uncertainty.get("proof_gap_present") or uncertainty.get("stale_evidence_present") or uncertainty.get("contradiction_present") or unresolved:
        lines.append("")
        lines.append(_color("UNCERTAINTY:", _ANSI_BOLD, _ANSI_CYAN))
        if uncertainty.get("proof_gap_present"):
            lines.append("- proof gap present")
        if uncertainty.get("stale_evidence_present"):
            lines.append("- stale evidence present")
        if uncertainty.get("contradiction_present"):
            lines.append("- contradiction present")
        for item in unresolved:
            lines.append(f"- {item}")

    codex_used = codex.get("codex_used")
    eligible = codex.get("eligible")
    if codex_used or eligible:
        lines.append("")
        lines.append(_color("CODEX:", _ANSI_BOLD, _ANSI_MAGENTA))
        if codex_used is not None:
            lines.append(f"- used: {'yes' if bool(codex_used) else 'no'}")
        if eligible is not None:
            lines.append(f"- fallback eligible: {'yes' if bool(eligible) else 'no'}")
        reason = str(codex.get("reason") or codex.get("usage_reason") or codex.get("failure_class") or "").strip()
        if reason:
            lines.append(f"- reason: {reason}")
        rec_class = str(codex.get("recurrence_class") or "").strip()
        if rec_class:
            lines.append(f"- recurrence: {rec_class}")
    return "\n".join(lines)


def _forward_execution_streams(result: Mapping[str, Any]) -> None:
    execution = dict(result.get("execution") or {})
    stdout_text = str(execution.get("stdout") or result.get("stdout") or "")
    stderr_text = str(execution.get("stderr") or result.get("stderr") or "")
    if stdout_text.strip():
        if not stdout_text.endswith("\n"):
            stdout_text += "\n"
        sys.stdout.write(stdout_text)
    if stderr_text.strip():
        if not stderr_text.endswith("\n"):
            stderr_text += "\n"
        sys.stderr.write(stderr_text)


def _print_chain_header(index: int, total: int, prompt: str) -> None:
    print(f"[{index}/{total}] {prompt}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Human-facing front door for the plane.")
    parser.add_argument("prompt", nargs="*", help="Natural-language request, e.g. lab \"fix this patch\".")
    parser.add_argument("--plan", action="store_true", help="Only plan; do not execute.")
    parser.add_argument("--json", action="store_true", help="Print full JSON result.")
    parser.add_argument("--verbose", action="store_true", help="Show Why / Uncertainty / Codex blocks.")
    parser.add_argument("--repo-root", default="", help="Optional repo root override.")
    parser.add_argument("--patch", default="", help="Explicit patch file path.")
    parser.add_argument("--zip-path", default="", help="Explicit zip file path.")
    parser.add_argument("--report-file", default="", help="Optional JSON report file.")
    parser.add_argument("--snapshot-file", default="", help="Optional snapshot JSON file.")
    parser.add_argument("--receipts-file", default="", help="Optional receipts JSON file.")
    parser.add_argument("--watchdog-file", default="", help="Optional watchdog JSON file.")
    parser.add_argument("--iterations-file", default="", help="Optional iterations JSON file.")
    parser.add_argument("--fallback-events-file", default="", help="Optional fallback events JSON file.")
    parser.add_argument("--target-pis-uid", default="", help="Optional PIS uid.")
    parser.add_argument("--target-uid", default="", help="Optional target uid.")
    parser.add_argument("--job-id", default="", help="Optional job id.")
    parser.add_argument("--task-id", default="", help="Optional task id.")
    parser.add_argument("--current-status", default="", help="Optional current status.")
    parser.add_argument("--expected-status", default="", help="Optional expected status.")
    parser.add_argument("--verifier-verdict", default="", help="Optional explicit verifier verdict.")
    parser.add_argument("--validation-command", action="append", default=[], help="Repeatable validation command.")
    parser.add_argument("--allow-fallback", action="store_true", help="Allow fallback when bounded policy permits it.")
    parser.add_argument("--no-fallback", action="store_true", help="Disable Codex fallback for this request.")
    parser.add_argument("--allow-recovery", action="store_true", help="Allow bounded deterministic recovery when supported.")
    parser.add_argument("--plan-only", action="store_true", help="For implementation actions, resolve and plan without saving or starting jobs.")
    parser.add_argument("--decompose-only", action="store_true", help="For implementation actions, stop after parent decomposition setup.")
    parser.add_argument("--max-children", default="", help="For implementation actions, requested child cap during decomposition.")
    parser.add_argument("--parallel", default="", choices=("safe", "off"), help="For implementation actions, child parallelism policy.")
    parser.add_argument("--proof-profile", default="", choices=("focused", "full"), help="For implementation actions, proof profile.")
    parser.add_argument("--close", dest="close_mode", default="", choices=("auto", "manual"), help="For implementation actions, closure mode.")
    parser.add_argument("--start-now", default="", choices=("true", "false"), help="For implementation actions, whether to start jobs immediately.")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    prompt = _read_prompt(args.prompt)
    if not prompt:
        parser.print_help(sys.stderr)
        return 2
    if _is_help_prompt(prompt):
        _print_help(parser)
        return 0
    topic = _help_topic(prompt)
    if topic:
        return _print_help_topic(topic)
    cwd = Path.cwd()
    contract_repo_root = _repo_root()
    repo_root = Path(args.repo_root).resolve() if str(args.repo_root or "").strip() else contract_repo_root
    session_id = str(os.environ.get("EIEL_LAB_SESSION_ID") or "").strip() or new_session_id()
    session_mode = str(os.environ.get("EIEL_LAB_SESSION_MODE") or "").strip() or "oneshot"
    session_owner = not bool(str(os.environ.get("EIEL_LAB_SESSION_ID") or "").strip())
    ensure_session_started(repo_root, session_id, mode=session_mode, cwd=cwd)
    try:
        results, exit_code = execute_front_door_prompt(
            args,
            prompt=prompt,
            cwd=cwd,
            repo_root=repo_root,
            contract_repo_root=contract_repo_root,
        )
    except KeyboardInterrupt:
        if session_owner:
            end_session(repo_root, session_id, reason="canceled")
        print("Canceled.", file=sys.stderr)
        return 130
    except ValueError as exc:
        print(str(exc).strip(), file=sys.stderr)
        return 2
    total_steps = len(results)
    for result in results:
        rendered_text = json.dumps(result, indent=2, sort_keys=True) if args.json else _render_human(result, verbose=bool(args.verbose))
        append_turn(
            repo_root,
            session_id,
            request_text=str(result.get("request_text") or prompt).strip(),
            payload=dict(result.get("front_door_payload") or {}),
            result=result,
            rendered_text=rendered_text,
        )
    if args.json:
        if len(results) == 1:
            print(json.dumps(results[0], indent=2, sort_keys=True))
        else:
            print(
                json.dumps(
                    {
                        "schema_version": "plane_operator_chain_result_v1",
                        "surface_name": "the plane",
                        "mode": "plan" if args.plan else "execute",
                        "steps": results,
                    },
                    indent=2,
                    sort_keys=True,
                )
            )
    else:
        for idx, result in enumerate(results, start=1):
            if len(results) > 1:
                if idx > 1:
                    print("")
                _print_chain_header(idx, len(results), str(result.get("request_text") or result["front_door_payload"].get("request_text") or ""))
            print(_render_human(result, verbose=bool(args.verbose)))
            if not args.plan and not bool(result["front_door_payload"].get("stream_subprocess_output")):
                _forward_execution_streams(result)
    if session_owner:
        end_session(repo_root, session_id, reason="completed" if exit_code == 0 else "blocked")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
