#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


REPO_ROOT = _repo_root()
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from experiments_shared import (  # noqa: E402
    build_operator_catalog_v1,
    execute_operator_request_v1,
    plan_operator_request_v1,
    render_operator_result_text,
)


def _load_json_file(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _maybe_load_text(path: str) -> str:
    if not str(path or "").strip():
        return ""
    return Path(path).read_text(encoding="utf-8")


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


def _add_shared_payload_flags(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--repo-root", default="", help="Optional repo root override.")
    parser.add_argument("--payload-file", default="", help="Optional JSON payload file merged first.")
    parser.add_argument("--json", action="store_true", help="Print structured JSON instead of human-readable output.")
    parser.add_argument("--patch-path", default="", help="Path to a patch artifact.")
    parser.add_argument("--patch-text-file", default="", help="Path to a patch/diff text file.")
    parser.add_argument("--expected-target-file", action="append", default=[], help="Repeatable expected target file.")
    parser.add_argument("--target-pis-uid", default="", help="Target PIS UID for proof assessment.")
    parser.add_argument("--target-uid", default="", help="Generic target uid for reconcile-style actions.")
    parser.add_argument("--pis-markdown-file", default="", help="Path to markdown used for proof assessment.")
    parser.add_argument("--job-id", default="", help="Target job id for job inspection.")
    parser.add_argument("--job-spec-file", default="", help="Optional job spec JSON file.")
    parser.add_argument("--job-state-file", default="", help="Optional job state JSON file.")
    parser.add_argument("--receipts-file", default="", help="Optional receipts JSON file.")
    parser.add_argument("--watchdog-file", default="", help="Optional watchdog summary JSON file.")
    parser.add_argument("--snapshot-file", default="", help="Optional snapshot JSON file.")
    parser.add_argument("--verifier-file", default="", help="Optional verifier result JSON file.")
    parser.add_argument("--report-file", default="", help="Optional report/composed action report JSON file.")
    parser.add_argument("--fallback-events-file", default="", help="Optional fallback events JSON file.")
    parser.add_argument("--improvement-opportunities-file", default="", help="Optional improvement opportunities JSON file.")
    parser.add_argument("--iterations-file", default="", help="Optional iterations JSON file for build/verify loop.")
    parser.add_argument("--validation-command", action="append", default=[], help="Repeatable validation command.")
    parser.add_argument("--evidence-artifact", action="append", default=[], help="Repeatable evidence artifact reference.")
    parser.add_argument("--artifact-reference", action="append", default=[], help="Repeatable operator artifact reference.")
    parser.add_argument("--verifier-verdict", default="", help="Explicit verifier verdict, e.g. PASS.")
    parser.add_argument("--current-status", default="", help="Current observed status for reconcile/explanation flows.")
    parser.add_argument("--expected-status", default="", help="Expected status for reconcile flows.")
    parser.add_argument("--authority-scope", default="", help="Authority scope hint.")
    parser.add_argument("--allow-recovery", action="store_true", help="Allow bounded deterministic recovery when supported.")
    parser.add_argument("--allow-fallback", action="store_true", help="Allow fallback only when bounded policy conditions are satisfied.")
    parser.add_argument("--bounded-ambiguity-reason", default="", help="Bounded ambiguity reason for proof-assess fallback.")
    parser.add_argument("--disagreement-note", default="", help="Receipt/watchdog disagreement note.")
    parser.add_argument("--escalation-reason", default="", help="Escalation reason record.")
    parser.add_argument("--plan-only", action="store_true", help="For implementation actions, resolve and plan without saving or starting jobs.")
    parser.add_argument("--decompose-only", action="store_true", help="For implementation actions, stop after parent decomposition setup.")
    parser.add_argument("--max-children", default="", help="For implementation actions, requested child cap during decomposition.")
    parser.add_argument("--parallel", default="", choices=("safe", "off"), help="For implementation actions, child parallelism policy.")
    parser.add_argument("--proof-profile", default="", choices=("focused", "full"), help="For implementation actions, proof profile.")
    parser.add_argument("--close", dest="close_mode", default="", choices=("auto", "manual"), help="For implementation actions, closure mode.")
    parser.add_argument("--start-now", default="", choices=("true", "false"), help="For implementation actions, whether to start jobs immediately.")


def _build_payload(args: argparse.Namespace, *, request_text: str = "", action_id: str = "", operator_verb: str = "") -> Dict[str, Any]:
    payload: Dict[str, Any] = {}
    if str(args.payload_file or "").strip():
        loaded = _load_json_file(str(args.payload_file))
        if not isinstance(loaded, Mapping):
            raise SystemExit("payload file must contain a JSON object")
        payload.update(dict(loaded))

    if request_text:
        payload["request_text"] = request_text
    if action_id:
        payload["action_id"] = action_id
    if operator_verb:
        payload["operator_verb"] = operator_verb

    simple_fields = {
        "patch_path": args.patch_path,
        "target_pis_uid": args.target_pis_uid,
        "target_uid": args.target_uid,
        "job_id": args.job_id,
        "verifier_verdict": args.verifier_verdict,
        "current_status": args.current_status,
        "expected_status": args.expected_status,
        "authority_scope": args.authority_scope,
        "bounded_ambiguity_reason": args.bounded_ambiguity_reason,
        "disagreement_note": args.disagreement_note,
        "escalation_reason": args.escalation_reason,
    }
    for key, value in simple_fields.items():
        if str(value or "").strip():
            payload[key] = str(value).strip()
    if "target_uid" not in payload:
        inferred_uid = _infer_target_uid_from_prompt(request_text)
        if inferred_uid:
            payload["target_uid"] = inferred_uid

    text_file_fields = {
        "patch_text": args.patch_text_file,
        "pis_markdown": args.pis_markdown_file,
    }
    for key, path in text_file_fields.items():
        text = _maybe_load_text(str(path or ""))
        if text:
            payload[key] = text

    json_file_fields = {
        "job_spec": args.job_spec_file,
        "job_state": args.job_state_file,
        "receipts": args.receipts_file,
        "watchdog_summary": args.watchdog_file,
        "snapshot": args.snapshot_file,
        "verifier_result": args.verifier_file,
        "report": args.report_file,
        "fallback_events": args.fallback_events_file,
        "improvement_opportunities": args.improvement_opportunities_file,
        "iterations": args.iterations_file,
    }
    for key, path in json_file_fields.items():
        if str(path or "").strip():
            payload[key] = _load_json_file(str(path))

    list_fields = {
        "expected_target_files": args.expected_target_file,
        "validation_commands_executed": args.validation_command,
        "validation_commands": args.validation_command,
        "evidence_artifacts": args.evidence_artifact,
        "artifact_references": args.artifact_reference,
    }
    for key, values in list_fields.items():
        cleaned = [str(item).strip() for item in list(values or []) if str(item).strip()]
        if cleaned:
            payload[key] = cleaned

    if bool(args.allow_recovery):
        payload["allow_recovery"] = True
    if bool(args.allow_fallback):
        payload["allow_fallback"] = True
    if bool(args.plan_only):
        payload["plan_only"] = True
    if bool(args.decompose_only):
        payload["decompose_only"] = True
    if str(args.max_children or "").strip():
        payload["max_children"] = int(args.max_children)
    if str(args.parallel or "").strip():
        payload["parallel"] = str(args.parallel).strip()
    if str(args.proof_profile or "").strip():
        payload["proof_profile"] = str(args.proof_profile).strip()
    if str(args.close_mode or "").strip():
        payload["close_mode"] = str(args.close_mode).strip()
    if str(args.start_now or "").strip():
        payload["start_now"] = str(args.start_now).strip().lower() != "false"
    return payload


def _print_result(result: Mapping[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(result, indent=2, sort_keys=True))
        return
    print(render_operator_result_text(result))


def _cmd_verbs(args: argparse.Namespace) -> int:
    repo_root = Path(str(args.repo_root or "").strip()).resolve() if str(args.repo_root or "").strip() else REPO_ROOT
    catalog = build_operator_catalog_v1(repo_root=repo_root)
    if args.json:
        print(json.dumps(catalog, indent=2, sort_keys=True))
        return 0
    print(f"Surface: {catalog.get('surface_name')}")
    print("Implemented verbs:")
    for row in list(catalog.get("implemented_verbs") or []):
        print(f"- {row.get('operator_verb')} ({row.get('action_id')})")
    supported = list(catalog.get("supported_surface_verbs") or [])
    if supported:
        print("Supported routed verbs:")
        for row in supported:
            print(f"- {row.get('operator_verb')} ({row.get('action_id')})")
    planned = list(catalog.get("planned_verbs") or [])
    if planned:
        print("Planned verbs:")
        for row in planned:
            print(f"- {row.get('operator_verb')}")
    return 0


def _cmd_request(args: argparse.Namespace) -> int:
    repo_root = Path(str(args.repo_root or "").strip()).resolve() if str(args.repo_root or "").strip() else REPO_ROOT
    request_text = str(args.request_text or "").strip()
    payload = _build_payload(args, request_text=request_text)
    result = execute_operator_request_v1(payload, repo_root=repo_root) if bool(args.execute) else plan_operator_request_v1(payload, repo_root=repo_root)
    _print_result(result, as_json=bool(args.json))
    return 0 if bool(result.get("supported")) else 1


def _cmd_run(args: argparse.Namespace) -> int:
    repo_root = Path(str(args.repo_root or "").strip()).resolve() if str(args.repo_root or "").strip() else REPO_ROOT
    action_id = str(args.action_id or "").strip()
    operator_verb = str(args.verb or "").strip()
    if not action_id and not operator_verb:
        raise SystemExit("run requires --action-id or --verb")
    payload = _build_payload(args, action_id=action_id, operator_verb=operator_verb)
    result = execute_operator_request_v1(payload, repo_root=repo_root)
    _print_result(result, as_json=bool(args.json))
    return 0 if bool(result.get("supported")) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Compact operator-facing CLI for the plane.")
    subparsers = parser.add_subparsers(dest="cmd", required=True)

    verbs = subparsers.add_parser("verbs", help="List implemented and planned operator verbs.")
    verbs.add_argument("--repo-root", default="", help="Optional repo root override.")
    verbs.add_argument("--json", action="store_true", help="Print structured JSON.")
    verbs.set_defaults(func=_cmd_verbs)

    request = subparsers.add_parser("request", help="Classify one operator request and optionally execute it.")
    request.add_argument("request_text", help="Natural-language or compact-verb request text.")
    request.add_argument("--execute", action="store_true", help="Execute the selected action instead of only planning it.")
    _add_shared_payload_flags(request)
    request.set_defaults(func=_cmd_request)

    run = subparsers.add_parser("run", help="Execute one implemented plane action directly.")
    run.add_argument("--action-id", default="", help="Explicit implemented action id to run.")
    run.add_argument("--verb", default="", help="Explicit implemented operator verb to run.")
    _add_shared_payload_flags(run)
    run.set_defaults(func=_cmd_run)
    return parser


def main(argv: Optional[List[str]] = None) -> int:
    try:
        args = build_parser().parse_args(argv)
        return int(args.func(args))
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
