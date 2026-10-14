#!/usr/bin/env python3
"""Generate the Q4A6 one-iteration strict re-verification campaign.

This script is deterministic over current scheduler state/receipts and writes:
- per-target strict re-verification scheduled jobs (single_turn/verifier)
- one rollup classification job
- one final confidence report job
- docs/reports/one_iteration_reverify_plan_2026-03-14.md
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.controlplane import scheduled_jobs as sched
from tools import runtime_policy as _runtime_policy
from infra.webui.ledger import ledger_storage

BASE = REPO_ROOT / "governance" / "agents" / "runtime" / "scheduled_jobs"
STATE_DIR = BASE / "state"
RECEIPTS_DIR = BASE / "receipts"
INBOX_DIR = BASE / "inbox"
LEDGER_ITEMS_DIR = REPO_ROOT / "governance" / "ledger" / "items"
REPORT_PATH = REPO_ROOT / "docs" / "reports" / "one_iteration_reverify_plan_2026-03-14.md"

BINDING_UID = "PIS-20260314-041711Z-Q4A6"
SOURCE_JOB_ID = "overnight_20260314_q4a6_checker_generate_reverify_queue"
RUN_AT_UTC = "2026-03-14T06:00:00Z"



def _campaign_runtime_profile() -> Dict[str, Any]:
    primary = _runtime_policy.get_runtime_policy_text("scheduler.build_verify.default_model", "gpt-5.4")
    fallbacks = _runtime_policy.get_runtime_policy_csv(
        "scheduler.build_verify.default_fallback_models",
        "deepseek-reasoner",
    )
    return {
        "model": primary,
        "fallback_models": list(fallbacks),
        "fallback_on_quota": True,
        "reasoning_effort": "medium",
    }


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _norm_job_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")


def _iterations_from_receipt(receipt: Dict[str, Any]) -> int:
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    try:
        completed = int(loop.get("completed_iterations") or 0)
    except Exception:
        completed = 0
    if completed > 0:
        return completed
    it = loop.get("iterations") if isinstance(loop.get("iterations"), list) else []
    return len(it)


def _truncate(text: str, *, limit: int = 1600) -> str:
    t = str(text or "").strip()
    if len(t) <= limit:
        return t
    return t[: max(0, limit - 13)].rstrip() + "\n...[truncated]"


def _extract_first_iteration_commentary(receipt: Dict[str, Any]) -> Dict[str, str]:
    loop = receipt.get("loop") if isinstance(receipt.get("loop"), dict) else {}
    rows = loop.get("iterations") if isinstance(loop.get("iterations"), list) else []
    if not rows:
        return {"builder_commentary": "", "verifier_commentary": ""}
    first = rows[0] if isinstance(rows[0], dict) else {}
    builder = first.get("builder") if isinstance(first.get("builder"), dict) else {}
    verifier = first.get("verifier") if isinstance(first.get("verifier"), dict) else {}
    return {
        "builder_commentary": _truncate(str(builder.get("content") or ""), limit=1800),
        "verifier_commentary": _truncate(str(verifier.get("content") or ""), limit=900),
    }


def _find_pis_path(uid: str) -> Path | None:
    item_dir = ledger_storage.item_dir_for_uid(LEDGER_ITEMS_DIR, uid)
    if item_dir is None:
        return None
    return item_dir / "pis.md"


def _extract_pis_title(pis_path: Path) -> str:
    lines = pis_path.read_text(encoding="utf-8", errors="replace").splitlines()
    in_title = False
    for line in lines:
        if line.strip().startswith("## 1. Title"):
            in_title = True
            continue
        if in_title:
            s = line.strip()
            if not s:
                continue
            if s.startswith("## "):
                break
            return s
    return ""


def _collect_targets() -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    targets: List[Dict[str, Any]] = []
    skipped: List[Dict[str, Any]] = []

    for st_path in sorted(STATE_DIR.glob("*.json"), key=lambda p: p.name):
        state = _read_json(st_path)
        job_id = str(state.get("job_id") or st_path.stem).strip()
        if not job_id:
            continue
        if str(state.get("last_status") or "") != "ok":
            continue

        run_id = str(state.get("last_run_id") or "").strip()
        if not run_id:
            skipped.append({"job_id": job_id, "reason": "missing_last_run_id"})
            continue

        receipt_path = RECEIPTS_DIR / job_id / f"{run_id}.json"
        if not receipt_path.exists():
            skipped.append({"job_id": job_id, "reason": "missing_receipt"})
            continue
        receipt = _read_json(receipt_path)

        if str(receipt.get("job_kind") or "") != "build_verify_loop":
            continue
        if str(receipt.get("status") or "") != "ok":
            continue

        iterations = _iterations_from_receipt(receipt)
        if iterations != 1:
            continue

        spec_path = INBOX_DIR / f"{job_id}.json"
        if not spec_path.exists():
            skipped.append({"job_id": job_id, "reason": "missing_job_spec"})
            continue
        spec = _read_json(spec_path)

        binding = spec.get("binding") if isinstance(spec.get("binding"), dict) else {}
        btype = str(binding.get("type") or "")
        bid = str(binding.get("id") or "")
        if btype != "ledger_uid" or not bid.startswith("PIS-"):
            skipped.append({"job_id": job_id, "reason": "non_ledger_pis_binding"})
            continue

        pis_path = _find_pis_path(bid)
        if pis_path is None:
            skipped.append({"job_id": job_id, "reason": f"missing_pis:{bid}"})
            continue

        title = _extract_pis_title(pis_path)
        commentary = _extract_first_iteration_commentary(receipt)
        targets.append(
            {
                "job_id": job_id,
                "run_id": run_id,
                "iterations": iterations,
                "pis_uid": bid,
                "pis_title": title,
                "pis_path": str(pis_path.relative_to(REPO_ROOT)),
                "receipt_path": str(receipt_path.relative_to(REPO_ROOT)),
                "spec_path": str(spec_path.relative_to(REPO_ROOT)),
                "builder_commentary": commentary["builder_commentary"],
                "verifier_commentary": commentary["verifier_commentary"],
            }
        )

    targets.sort(key=lambda r: (str(r["pis_uid"]), str(r["job_id"])))
    return targets, skipped


def _strict_prompt(row: Dict[str, Any], all_targets: List[Dict[str, Any]]) -> str:
    uid = str(row["pis_uid"])
    siblings = [t["pis_uid"] for t in all_targets if t["pis_uid"] != uid][:8]
    sibling_text = ", ".join(siblings) if siblings else "none_detected"

    builder_commentary = str(row.get("builder_commentary") or "").strip()
    verifier_commentary = str(row.get("verifier_commentary") or "").strip()

    return (
        f"Strict second-pass verification for one-iteration accepted job `{row['job_id']}`.\n\n"
        "Objective:\n"
        "Perform a verification-only re-audit. Do not rewrite implementation unless explicit rebuild-required evidence is found.\n\n"
        f"Target PIS: {uid} ({row['pis_title']})\n"
        f"Bound source job: {row['job_id']}\n"
        f"Primary artifacts:\n"
        f"- {row['pis_path']}\n"
        f"- {row['spec_path']}\n"
        f"- {row['receipt_path']}\n\n"
        "Mandatory strictness rules (fail-closed):\n"
        "1) Findings must include concrete file paths and 1-based line references when claiming defects or omissions.\n"
        "2) Explicit boundary review versus sibling PISes is required; inspect at least 2 sibling PISes when available.\n"
        f"   Candidate sibling UIDs: {sibling_text}\n"
        "3) Proof review must list actual validation commands used in source implementation and judge sufficiency.\n"
        "4) Reject PASS when optional-but-cheap high-impact tests were skipped without strong reason.\n"
        "5) Verdict contract: output exactly one of `VERDICT: KEEP_AS_IS`, `VERDICT: PATCH_ONLY`, `VERDICT: REBUILD_REQUIRED`.\n"
        "6) If proof is insufficient, classify as PATCH_ONLY or REBUILD_REQUIRED; do not default to KEEP_AS_IS.\n\n"
        "Implementer-derived context (mandatory to challenge, not to trust blindly):\n"
        "- Review the original builder and verifier commentary below.\n"
        "- Cross-check claimed validations against actual code/tests.\n"
        "- Treat unproven builder claims as unproven, not accepted.\n\n"
        f"Original builder commentary excerpt:\n{builder_commentary or '[missing builder commentary in receipt]'}\n\n"
        f"Original verifier commentary excerpt:\n{verifier_commentary or '[missing verifier commentary in receipt]'}\n\n"
        "Required output sections:\n"
        "- Boundary Review\n"
        "- Findings (with file:line evidence)\n"
        "- Validation Proof Review\n"
        "- Verdict and Rationale\n"
        "- Follow-up (if patch/rebuild required)\n"
    )


def _build_reverify_job(row: Dict[str, Any], idx: int, all_targets: List[Dict[str, Any]]) -> Dict[str, Any]:
    stem = hashlib.sha256(str(row["job_id"]).encode("utf-8")).hexdigest()[:8]
    job_id = f"overnight_20260314_q4a6_rv_{idx:02d}_{stem}"
    prompt = _strict_prompt(row, all_targets)
    return {
        "schema_version": "scheduled_job_v1",
        "job_id": _norm_job_id(job_id),
        "job_kind": "single_turn",
        "role": "verifier",
        "enabled": False,
        "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "binding": {"type": "ledger_uid", "id": BINDING_UID},
        "depends_on": [SOURCE_JOB_ID],
        "prompt": prompt,
        "context_refs": [
            "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md",
            "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
            "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_taxonomy.md",
            "governance/agents/prompts/_all_codex.md",
            "docs/reports/scheduled_jobs_status_2026-03-14.md",
            str(row["pis_path"]),
            str(row["spec_path"]),
            str(row["receipt_path"]),
        ],
        "expected_output": {"kind": "strict_reverify_report", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": RUN_AT_UTC, "timezone": "UTC"},
        "policy": {
            "allow_overlap": False,
            "max_runtime_seconds": 2400,
            "post_success_commit_tag": False,
            "post_success_commit_tag_retries": 0,
            "post_success_commit_tag_backoff_seconds": 60,
            "agent_turn_retries": 0,
            "agent_turn_retry_backoff_seconds": 5,
            "repo_change_mode": "none",
            "require_validation_evidence": False,
            "require_clean_worktree": False,
            "require_commit_journal": False,
        },
        "runtime": {
            "model": "gpt-5.4",
            "fallback_model": "",
            "fallback_models": [
                "gpt-5.4-mini",
                "gpt-5.2-codex",
                "gpt-5.2",
                "gpt-5.1-codex-max",                "gpt-5.1-codex-mini",
            ],
            "fallback_on_quota": True,
            "reasoning_effort": "medium",
        },
    }


def _rollup_prompt(targets: List[Dict[str, Any]], reverify_ids: List[str], final_job_id: str) -> str:
    lines = [
        "Aggregate strict re-verification outputs for Q4A6 one-iteration accepted jobs.",
        "",
        "Required work:",
        "1) Inspect each re-verification receipt/report listed below.",
        "2) Produce a table grouped into: keep-as-is / patch-only / rebuild-required.",
        "3) For patch-only and rebuild-required, include concrete reason and expected follow-up artifact owner.",
        "4) Create a machine-readable summary at governance/agents/runtime/scheduled_jobs/rerun_campaigns/one_iteration_reverify_rollup_2026-03-14.json.",
        "5) Fail closed: if any dependency report is missing or inconclusive, mark that target as rebuild-required unless disproven.",
        "",
        "Do not implement code changes in this job.",
        "",
        "Reverify job IDs:",
    ]
    lines.extend(f"- {jid}" for jid in reverify_ids)
    lines.extend(
        [
            "",
            "Output sections:",
            "- Rollup Summary",
            "- Classification Table",
            "- Dependency/Proof Gaps",
            "- Next Queue Recommendations",
            f"- Final confidence handoff target: {final_job_id}",
            "",
            "Verdict contract:",
            "- VERDICT: PASS only when all targets are classified with explicit evidence.",
            "- Otherwise VERDICT: FAIL.",
        ]
    )
    return "\n".join(lines)


def _final_prompt(rollup_job_id: str) -> str:
    return (
        "Final confidence report for Q4A6 strict re-verification campaign.\n\n"
        "This job runs after rollup and must summarize campaign-level confidence.\n"
        "If rollup recommends patch/rebuild items that do not yet have completed outputs, report status as `deferred` and fail closed.\n\n"
        f"Primary upstream dependency: {rollup_job_id}\n\n"
        "Required output:\n"
        "- overall confidence tier\n"
        "- keep-as-is / patch-only / rebuild-required counts\n"
        "- unresolved proof gaps\n"
        "- explicit statement of whether confidence applies only to completed items\n\n"
        "Verdict contract:\n"
        "- VERDICT: PASS only if no unresolved high-impact proof gaps remain.\n"
        "- VERDICT: FAIL otherwise.\n"
    )


def _render_report(
    targets: List[Dict[str, Any]],
    skipped: List[Dict[str, Any]],
    reverify_specs: List[Dict[str, Any]],
    rollup_spec: Dict[str, Any],
    final_spec: Dict[str, Any],
) -> str:
    utc_now = dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    total_one_iter = len(targets) + len([s for s in skipped if str(s.get("reason", "")).startswith("non_ledger_pis_binding")])

    lines: List[str] = []
    lines.append("# One-Iteration Strict Re-Verification Plan (2026-03-14)")
    lines.append("")
    lines.append(f"Generated at `{utc_now}` for ledger item `{BINDING_UID}`.")
    lines.append("")
    lines.append("## Boundary Summary")
    lines.append("")
    lines.append("- In scope: build deterministic inventory of one-iteration accepted build/verify jobs, create strict verifier-only second-pass jobs, and queue rollup/final report jobs with dependency-driven sequencing.")
    lines.append("- Out of scope: scheduler core changes, automatic broad rebuild campaign execution, or rewriting prior implementations in this queue-generation turn.")
    lines.append("")
    lines.append("## Inventory Summary")
    lines.append("")
    lines.append(f"- One-iteration accepted build/verify-loop jobs detected: `{total_one_iter}`")
    lines.append(f"- Targeted (ledger-bound PIS jobs): `{len(targets)}`")
    lines.append(f"- Skipped (non-ledger or unresolved artifacts): `{len(skipped)}`")
    lines.append("")
    lines.append("### Target Table")
    lines.append("")
    lines.append("| # | Source Job | PIS UID | PIS Title | Reverify Job ID |")
    lines.append("|---:|---|---|---|---|")
    for idx, (row, spec) in enumerate(zip(targets, reverify_specs), start=1):
        title = str(row["pis_title"]).replace("|", "/")
        lines.append(f"| {idx} | `{row['job_id']}` | `{row['pis_uid']}` | {title} | `{spec['job_id']}` |")
    lines.append("")

    if skipped:
        lines.append("### Skipped Rows")
        lines.append("")
        lines.append("| Job ID | Reason |")
        lines.append("|---|---|")
        for row in sorted(skipped, key=lambda r: (str(r.get("reason")), str(r.get("job_id")))):
            lines.append(f"| `{row.get('job_id','')}` | `{row.get('reason','')}` |")
        lines.append("")

    lines.append("## Queue Creation Summary")
    lines.append("")
    lines.append(f"- Created strict verifier re-verification jobs: `{len(reverify_specs)}`")
    lines.append(f"- Created rollup job: `{rollup_spec['job_id']}`")
    lines.append(f"- Created final confidence job: `{final_spec['job_id']}`")
    lines.append("")
    lines.append("### Dependency Graph")
    lines.append("")
    lines.append(f"- `{SOURCE_JOB_ID}` -> all `overnight_20260314_q4a6_rv_*` jobs")
    lines.append(f"- all `overnight_20260314_q4a6_rv_*` jobs -> `{rollup_spec['job_id']}`")
    lines.append(f"- `{rollup_spec['job_id']}` -> `{final_spec['job_id']}`")
    lines.append("")
    lines.append("### Created Scheduled Job IDs")
    lines.append("")
    for spec in reverify_specs:
        lines.append(f"- `{spec['job_id']}`")
    lines.append(f"- `{rollup_spec['job_id']}`")
    lines.append(f"- `{final_spec['job_id']}`")
    lines.append("")

    lines.append("## Prompt Quality Summary")
    lines.append("")
    lines.append("All generated strict verifier prompts enforce:")
    lines.append("- concrete file/line findings")
    lines.append("- explicit sibling-boundary review")
    lines.append("- proof/validation command sufficiency review")
    lines.append("- fail-closed verdict contract")
    lines.append("- rejection of PASS when cheap high-impact tests are missing")
    lines.append("")
    lines.append("### Strict Verifier Prompt Examples")
    lines.append("")
    for spec in reverify_specs[:3]:
        prompt_preview = "\n".join(str(spec["prompt"]).splitlines()[:18])
        lines.append(f"#### `{spec['job_id']}`")
        lines.append("")
        lines.append("```text")
        lines.append(prompt_preview)
        lines.append("```")
        lines.append("")

    return "\n".join(lines) + "\n"


def main() -> int:
    targets, skipped = _collect_targets()
    if not targets:
        raise SystemExit("No one-iteration ledger-bound targets found; aborting without writing empty campaign.")

    reverify_specs: List[Dict[str, Any]] = []
    for idx, row in enumerate(targets, start=1):
        spec = _build_reverify_job(row, idx, targets)
        canonical = sched.save_job(spec, repo_root=REPO_ROOT)
        reverify_specs.append(canonical)

    rollup_job_id = "overnight_20260314_q4a6_reverify_rollup"
    final_job_id = "overnight_20260314_q4a6_reverify_confidence_report"

    rollup_spec = {
        "schema_version": "scheduled_job_v1",
        "job_id": rollup_job_id,
        "job_kind": "single_turn",
        "role": "verifier",
        "enabled": False,
        "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "binding": {"type": "ledger_uid", "id": BINDING_UID},
        "depends_on": [s["job_id"] for s in reverify_specs],
        "prompt": _rollup_prompt(targets, [s["job_id"] for s in reverify_specs], final_job_id),
        "context_refs": [
            "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md",
            "docs/reports/one_iteration_reverify_plan_2026-03-14.md",
            "governance/agents/runtime/scheduled_jobs/receipts",
            "governance/agents/runtime/scheduled_jobs/reports",
            "governance/agents/runtime/scheduled_jobs/inbox",
            "governance/agents/runtime/scheduled_jobs/state",
        ],
        "expected_output": {"kind": "strict_reverify_rollup", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": RUN_AT_UTC, "timezone": "UTC"},
        "policy": {
            "allow_overlap": False,
            "max_runtime_seconds": 3600,
            "post_success_commit_tag": False,
            "post_success_commit_tag_retries": 0,
            "post_success_commit_tag_backoff_seconds": 60,
            "agent_turn_retries": 0,
            "agent_turn_retry_backoff_seconds": 5,
            "repo_change_mode": "none",
            "require_validation_evidence": False,
            "require_clean_worktree": False,
            "require_commit_journal": False,
        },
        "runtime": _campaign_runtime_profile(),
    }
    rollup_spec = sched.save_job(rollup_spec, repo_root=REPO_ROOT)

    final_spec = {
        "schema_version": "scheduled_job_v1",
        "job_id": final_job_id,
        "job_kind": "single_turn",
        "role": "verifier",
        "enabled": False,
        "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "binding": {"type": "ledger_uid", "id": BINDING_UID},
        "depends_on": [rollup_job_id],
        "prompt": _final_prompt(rollup_job_id),
        "context_refs": [
            "docs/reports/one_iteration_reverify_plan_2026-03-14.md",
            "governance/agents/runtime/scheduled_jobs/reports",
            "governance/agents/runtime/scheduled_jobs/receipts",
            "governance/agents/runtime/scheduled_jobs/state",
            "governance/agents/runtime/scheduled_jobs/inbox",
        ],
        "expected_output": {"kind": "strict_reverify_final_confidence", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": RUN_AT_UTC, "timezone": "UTC"},
        "policy": {
            "allow_overlap": False,
            "max_runtime_seconds": 1800,
            "post_success_commit_tag": False,
            "post_success_commit_tag_retries": 0,
            "post_success_commit_tag_backoff_seconds": 60,
            "agent_turn_retries": 0,
            "agent_turn_retry_backoff_seconds": 5,
            "repo_change_mode": "none",
            "require_validation_evidence": False,
            "require_clean_worktree": False,
            "require_commit_journal": False,
        },
        "runtime": _campaign_runtime_profile(),
    }
    final_spec = sched.save_job(final_spec, repo_root=REPO_ROOT)

    report = _render_report(targets, skipped, reverify_specs, rollup_spec, final_spec)
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")

    out = {
        "targets": targets,
        "skipped": skipped,
        "created_reverify_jobs": [s["job_id"] for s in reverify_specs],
        "created_rollup_job": rollup_spec["job_id"],
        "created_final_job": final_spec["job_id"],
        "report_path": str(REPORT_PATH.relative_to(REPO_ROOT)),
    }
    print(json.dumps(out, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
