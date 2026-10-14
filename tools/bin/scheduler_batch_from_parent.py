#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.bin.scheduler_tty_ui import prompt_menu
from infra.controlplane import explicit_pass_chain_compiler
from infra.controlplane import intent_family_v2_compiler
from infra.controlplane import scheduled_jobs as sched
from infra.controlplane.sched import operator_backend as sched_backend
from tools import runtime_policy as _runtime_policy
from infra.webui.ledger import ledger_storage


@dataclass
class ParentChoice:
    uid: str
    title: str
    status: str
    updated_utc: str
    created_utc: str
    child_count: int


def _parse_utc_maybe(raw: str) -> float:
    text = str(raw or "").strip()
    if not text:
        return 0.0
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp()
    except Exception:
        return 0.0


def _parse_start_in(raw: str) -> timedelta:
    text = str(raw or "").strip().lower()
    m = re.fullmatch(r"(\d+)([smhd])", text)
    if not m:
        raise ValueError("start-in must look like 10m, 30s, 2h, or 1d")
    value = int(m.group(1))
    unit = m.group(2)
    if unit == "s":
        return timedelta(seconds=value)
    if unit == "m":
        return timedelta(minutes=value)
    if unit == "h":
        return timedelta(hours=value)
    return timedelta(days=value)


def _stage_runtime(model: str) -> Dict[str, Any]:
    return {
        "agent_tech": str(sched._infer_agent_tech_from_model(model) or "codex"),
        "model": str(model),
        "reasoning_effort": "medium",
        "fallback_on_quota": True,
    }


def _default_builder_model() -> str:
    return _runtime_policy.get_runtime_policy_text(
        "scheduler.child_implementation.default_model",
        "deepseek-reasoner",
    )


def _default_verifier_model() -> str:
    return _runtime_policy.get_runtime_policy_text(
        "scheduler.build_verify.default_model",
        "gpt-5.4",
    )


def _load_recent_parent_choices(limit: int, *, repo_root: Path) -> List[ParentChoice]:
    ledger_root = ledger_storage.ledger_patch_instruction_sheets_root(repo_root)
    if not ledger_root.exists():
        return []
    child_counts: Dict[str, int] = {}
    for item_dir in ledger_storage.list_item_dirs(ledger_root):
        events_path = (item_dir / "events.jsonl").resolve()
        if not events_path.exists():
            continue
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
            parent_uid = str(payload.get("parent_uid") or "").strip()
            if parent_uid:
                child_counts[parent_uid] = int(child_counts.get(parent_uid, 0)) + 1
    out: List[ParentChoice] = []
    for parent_uid, child_count in child_counts.items():
        item_dir = ledger_storage.item_dir_for_uid(ledger_root, parent_uid)
        if item_dir is None:
            continue
        meta_path = (item_dir / "meta.json").resolve()
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
        except Exception:
            meta = {}
        out.append(
            ParentChoice(
                uid=parent_uid,
                title=str(meta.get("title") or parent_uid),
                status=str(meta.get("status") or "unknown"),
                updated_utc=str(meta.get("updated_utc") or ""),
                created_utc=str(meta.get("created_utc") or ""),
                child_count=child_count,
            )
        )
    out.sort(key=lambda row: (_parse_utc_maybe(row.updated_utc), _parse_utc_maybe(row.created_utc), row.uid), reverse=True)
    return out[: max(1, int(limit))]


def _select_parent_uid(parent_uid: str, *, repo_root: Path, limit: int) -> str:
    uid = str(parent_uid or "").strip()
    if uid:
        return uid
    choices = _load_recent_parent_choices(limit, repo_root=repo_root)
    if not choices:
        raise ValueError("no parent PIS items with decomposed children found")
    selected = prompt_menu(
        "Recent parent PIS items",
        choices=choices,
        render=lambda row: (
            f"{row.uid} [{row.status}] {(row.updated_utc or row.created_utc or '-')}"
            f" children={row.child_count} {row.title}"
        ),
        default_index=0,
        fallback_reader=input,
    )
    return selected.uid


def _planned_child_specs(
    parent_uid: str,
    *,
    run_at_utc: str,
    builder_model: str,
    verifier_model: str,
    max_iterations: int,
    repo_root: Path,
) -> List[Dict[str, Any]]:
    normalized_family = sched._normalize_intent_family_tail_contract(parent_uid=parent_uid, repo_root=repo_root)
    child_rows = list(normalized_family.get("child_rows") or [])
    if not child_rows:
        child_rows = sched._iter_parent_child_rows(parent_uid=parent_uid, repo_root=repo_root)
    if not child_rows:
        raise ValueError(f"no child PIS items found for parent {parent_uid}")
    parent_dir = sched._resolve_ledger_item_dir_for_uid(parent_uid, repo_root=repo_root)
    parent_meta = sched._read_json((parent_dir / "meta.json").resolve()) if parent_dir is not None else {}
    parent_title = str(parent_meta.get("title") or parent_uid).strip() or parent_uid
    parent_integration = parent_meta.get("integration_meta") if isinstance(parent_meta.get("integration_meta"), dict) else {}
    source_intent_uid = str(parent_integration.get("intent_sheet_uid") or "").strip()
    family_execution_mode = str(parent_integration.get("execution_mode") or "").strip().lower()
    child_relationships = {str(row.get("relationship") or "").strip() for row in child_rows}
    canonical_intent_family = {
        "execution_child",
        "intent_validation_suite",
        "intent_family_closeout",
    }.issubset(child_relationships)
    if source_intent_uid and canonical_intent_family and family_execution_mode in {"explicit_pass_chain_v1", "explicit_pass_chain", "build_or_improve_chain"}:
        compiled = explicit_pass_chain_compiler.compile_explicit_pass_chain(
            parent_uid=parent_uid,
            run_at_utc=run_at_utc,
            builder_model=builder_model,
            verifier_model=verifier_model,
            pass_count=max_iterations,
            repo_root=repo_root,
        )
        return list(compiled.get("job_specs") or [])
    if source_intent_uid and canonical_intent_family:
        compiled = intent_family_v2_compiler.compile_intent_family_v2(
            parent_uid=parent_uid,
            run_at_utc=run_at_utc,
            builder_model=builder_model,
            verifier_model=verifier_model,
            max_iterations=max_iterations,
            repo_root=repo_root,
        )
        return list(compiled.get("job_specs") or [])
    required_final_child_uid = str(parent_integration.get("required_final_child_uid") or "").strip()
    required_final_child_kind = str(parent_integration.get("required_final_child_kind") or "").strip()
    trust_hardening_mode = "broad_current_state_rerun_v1"
    parent_status = str(parent_meta.get("status") or "").strip().lower()
    child_uids = [str(row.get("uid") or "").strip() for row in child_rows if str(row.get("uid") or "").strip()]
    child_statuses = {sched._ledger_status_for_uid(child_uid, repo_root=repo_root) for child_uid in child_uids}
    family_already_verified = parent_status == "verified" and bool(child_uids) and child_statuses == {"verified"}

    planned_job_ids = {
        str(row.get("uid") or ""): sched._job_id_from_ledger_uid(str(row.get("uid") or ""), str(row.get("title") or ""))
        for row in child_rows
    }
    closeout_rows = [
        row for row in child_rows if str(row.get("relationship") or "").strip() == "intent_family_closeout"
    ]
    if closeout_rows:
        canonical_closeout = closeout_rows[-1]
        required_final_child_uid = str(canonical_closeout.get("uid") or "").strip()
        required_final_child_kind = "intent_family_closeout"
    elif required_final_child_uid and not required_final_child_kind:
        for row in child_rows:
            if str(row.get("uid") or "").strip() == required_final_child_uid:
                required_final_child_kind = str(row.get("relationship") or "").strip()
                break
    out: List[Dict[str, Any]] = []
    for row in child_rows:
        uid = str(row.get("uid") or "")
        title = str(row.get("title") or uid)
        pis_rel = str(row.get("pis_rel") or "")
        status = str(row.get("status") or "draft")
        relationship = str(row.get("relationship") or "").strip() or "execution_child"
        critical_path_rank = int(row.get("critical_path_rank") or 0)
        depends_on = [planned_job_ids[dep] for dep in list(row.get("depends_on_uids") or []) if dep in planned_job_ids]
        builder_runtime = _stage_runtime(builder_model)
        verifier_runtime = _stage_runtime(verifier_model)
        builder_runtime["compiled_execution_mode"] = "soft_gate"
        verifier_runtime["compiled_execution_mode"] = "soft_gate"
        spec = {
            "job_id": planned_job_ids[uid],
            "job_kind": "build_verify_loop",
            "enabled": True,
            "binding": {"type": "ledger_uid", "id": uid},
            "depends_on": depends_on,
            "display_title": f"{uid.split('-')[-1].lower()} {title[:80]}",
            "prompt": sched._implementation_prompt_for_child(uid=uid, title=title, pis_rel=pis_rel, status=status, repo_root=repo_root),
            "context_refs": [pis_rel],
            "expected_output": {"kind": "report", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": run_at_utc, "timezone": "UTC"},
            "policy": {
                "max_runtime_seconds": 1800,
                "post_success_commit_tag": True,
            },
            "runtime": dict(builder_runtime),
            "required_context_bundle": "lab_core_v1",
            "loop": {
                "builder_role": "builder",
                "verifier_role": "verifier",
                "max_iterations": int(max_iterations),
                "pass_marker": "VERDICT: PASS",
                "builder_runtime": dict(builder_runtime),
                "verifier_runtime": dict(verifier_runtime),
            },
            "prompt_meta": {
                "generated_from_parent_uid": parent_uid,
                "batch_scheduler": "scheduler_batch_from_parent",
                "execution_family_contract": {
                    "contract_kind": "intent_derived_execution_family_v1" if source_intent_uid else "execution_family_v1",
                    "source_intent_uid": source_intent_uid,
                    "parent_uid": parent_uid,
                    "child_uid": uid,
                    "child_kind": relationship,
                    "critical_path_rank": critical_path_rank,
                    "required_final_child_kind": required_final_child_kind,
                    "required_final_child_uid": required_final_child_uid,
                },
            },
        }
        out.append(spec)
    child_job_ids = [str(spec.get("job_id") or "").strip() for spec in out if str(spec.get("job_id") or "").strip()]
    allowed_ledger_paths = [
        "governance/ledger/patch_instruction_sheets/uid_index.json",
        f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/meta.json",
        f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/events.jsonl",
        f"governance/ledger/patch_instruction_sheets/**/{parent_uid}__*/artifacts/",
        "runtime/state/commit_journal/pending/",
    ]
    for row in child_rows:
        child_uid = str(row.get("uid") or "").strip()
        if not child_uid:
            continue
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
    closeout_builder_runtime = _stage_runtime(builder_model)
    closeout_verifier_runtime = _stage_runtime(verifier_model)
    if not family_already_verified and not required_final_child_uid:
        out.append(
            {
                "job_id": sched._family_closeout_job_id(parent_uid),
                "job_kind": "build_verify_loop",
                "enabled": True,
                "binding": {"type": "ledger_uid", "id": parent_uid},
                "depends_on": list(child_job_ids),
                "display_title": f"{parent_uid.split('-')[-1].lower()} execution family closeout",
                "prompt": sched._execution_family_closeout_prompt(
                    parent_uid=parent_uid,
                    parent_title=parent_title,
                    source_intent_uid=source_intent_uid,
                    required_final_child_uid=required_final_child_uid,
                    child_uids=child_uids,
                    child_job_ids=list(child_job_ids),
                    trust_hardening_mode=trust_hardening_mode,
                ),
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
                "runtime": dict(closeout_verifier_runtime),
                "required_context_bundle": "lab_core_v1",
                "loop": {
                    "builder_role": "builder",
                    "verifier_role": "verifier",
                    "max_iterations": 6,
                    "pass_marker": "VERDICT: PASS",
                    "builder_runtime": dict(closeout_builder_runtime),
                    "verifier_runtime": dict(closeout_verifier_runtime),
                },
                "prompt_meta": {
                    "generated_from_parent_uid": parent_uid,
                    "batch_scheduler": "scheduler_batch_from_parent",
                    "execution_family_closeout": {
                        "contract_kind": "execution_family_closeout_v1",
                        "parent_uid": parent_uid,
                        "source_intent_uid": source_intent_uid,
                        "required_final_child_uid": required_final_child_uid,
                        "child_uids": list(child_uids),
                        "child_job_ids": list(child_job_ids),
                        "integration_audit_mode": "family_receipt_changed_files_v1",
                        "trust_hardening_mode": trust_hardening_mode,
                        "allowed_repo_paths": [],
                        "allowed_ledger_paths": list(allowed_ledger_paths),
                        "closeout_rules": [
                            "required_final_child_must_be_verified",
                            "parent_may_close_only_after_all_children_verified",
                            "intent_may_close_only_after_validating_pis_verified",
                            "family_closeout_must_emit_closure_artifact",
                            "broad_current_state_rerun_required_before_final_closure",
                        ],
                        "status_promotion_policy": "verified_only_after_family_audit_and_required_final_child",
                        "receipt_selection_policy": "latest_successful_child_receipts_only",
                    },
                },
            }
        )
    return out


def _merge_existing(existing: Dict[str, Any], planned: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(existing)
    for key in ("job_kind", "enabled", "binding", "display_title", "prompt", "context_refs", "expected_output", "schedule", "policy", "runtime", "required_context_bundle", "loop"):
        if key in planned:
            out[key] = planned[key]
    if "depends_on" in planned:
        out["depends_on"] = planned["depends_on"]
    elif "depends_on" not in out:
        out["depends_on"] = []
    prompt_meta = dict(existing.get("prompt_meta") or {})
    prompt_meta.update(dict(planned.get("prompt_meta") or {}))
    out["prompt_meta"] = prompt_meta
    return out


def _render_plan(specs: List[Dict[str, Any]]) -> str:
    rows = []
    for spec in specs:
        loop = dict(spec.get("loop") or {})
        rows.append(
            {
                "job_id": str(spec.get("job_id") or ""),
                "binding": str((spec.get("binding") or {}).get("id") or ""),
                "run_at": str((spec.get("schedule") or {}).get("run_at") or ""),
                "depends_on": list(spec.get("depends_on") or []),
                "builder_model": str((loop.get("builder_runtime") or {}).get("model") or ""),
                "verifier_model": str((loop.get("verifier_runtime") or {}).get("model") or ""),
                "max_iterations": int(loop.get("max_iterations") or 0),
            }
        )
    return json.dumps(rows, indent=2, ensure_ascii=False)


def main(argv: List[str] | None = None) -> int:
    try:
        parser = argparse.ArgumentParser(description="Batch create or update child implementation jobs from a parent PIS.")
        parser.add_argument("parent_uid", nargs="?", default="", help="Parent ledger UID, e.g. PIS-20260326-203500Z-R7K2")
        parser.add_argument("--start-in", default="10m", help="Delay before start, e.g. 10m, 30s, 2h, 1d")
        parser.add_argument("--builder-model", default=_default_builder_model(), help="Builder model for every child job")
        parser.add_argument("--verifier-model", default=_default_verifier_model(), help="Verifier model for every child job")
        parser.add_argument("--max-iterations", type=int, default=5, help="Build/verify max iterations for every child job")
        parser.add_argument("--limit", type=int, default=20, help="How many recent parent PIS items to show in interactive selection")
        parser.add_argument("--dry-run", action="store_true", help="Show the planned jobs without saving")
        parser.add_argument("--backend", choices=("v2", "legacy"), default="", help="scheduler backend (default: runtime policy, currently v2)")
        args = parser.parse_args(argv)
        sched_backend.require_legacy_for_mutation("batch create scheduled jobs from parent", backend=args.backend or None)

        parent_uid = _select_parent_uid(str(args.parent_uid or "").strip(), repo_root=REPO_ROOT, limit=max(1, int(args.limit)))
        run_at = (datetime.now(timezone.utc).replace(microsecond=0) + _parse_start_in(args.start_in)).isoformat().replace("+00:00", "Z")
        planned = _planned_child_specs(
            parent_uid,
            run_at_utc=run_at,
            builder_model=args.builder_model,
            verifier_model=args.verifier_model,
            max_iterations=max(1, int(args.max_iterations)),
            repo_root=REPO_ROOT,
        )
        print(_render_plan(planned))
        if args.dry_run:
            return 0

        for spec in planned:
            try:
                existing = sched.load_job(str(spec.get("job_id") or ""), repo_root=REPO_ROOT)
            except FileNotFoundError:
                existing = {}
            saved = sched.save_job(_merge_existing(existing, spec) if existing else spec, repo_root=REPO_ROOT)
            print(f"saved {saved['job_id']}")
        return 0
    except KeyboardInterrupt:
        print("Canceled.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
