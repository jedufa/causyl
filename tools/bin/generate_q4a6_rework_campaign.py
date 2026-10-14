#!/usr/bin/env python3
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
from tools.bin import generate_q4a6_reverify_campaign as base

REPORT_PATH = REPO_ROOT / "docs" / "reports" / "one_iteration_rework_campaign_plan_2026-03-14.md"
RUN_AT_UTC = "2026-03-14T06:15:00Z"



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
BINDING_UID = "PIS-20260314-041711Z-Q4A6"


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def _safe_title(s: str) -> str:
    t = str(s or "").strip()
    return t.replace("|", "/")


def _norm_job_id(raw: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", raw).strip("-")


def _extract_parent_uid(pis_path_rel: str) -> str:
    p = (REPO_ROOT / str(pis_path_rel)).resolve()
    ev = p.parent / "events.jsonl"
    if not ev.exists():
        return ""
    for line in ev.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if str(row.get("type") or "") != "decomposed_from_parent":
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else {}
        uid = str(payload.get("parent_uid") or "").strip()
        if uid:
            return uid
    return ""


def _find_pis_path(uid: str) -> str:
    p = base._find_pis_path(uid)  # type: ignore[attr-defined]
    if not p:
        return ""
    return str(p.relative_to(REPO_ROOT))


def _is_doc_generation_job(row: Dict[str, Any]) -> bool:
    jid = str(row.get("job_id") or "").lower()
    if "children_" in jid or "_children_" in jid or "parent_" in jid:
        return True
    spec_path = REPO_ROOT / str(row.get("spec_path") or "")
    spec = _read_json(spec_path) if spec_path.exists() else {}
    prompt = str(spec.get("prompt") or "").lower()
    needles = [
        "create child pis",
        "create children pis",
        "child pises",
        "children pises",
        "queue the child",
        "schedule each created child",
        "governance task",
    ]
    return any(n in prompt for n in needles)


def _rework_prompt(
    *,
    row: Dict[str, Any],
    parent_uid: str,
    parent_path: str,
    layer: str,
) -> str:
    additions_path = f"docs/reports/q4a6/verifier_additions/{row['job_id']}.md"
    parent_line = f"{parent_uid} ({parent_path})" if parent_uid and parent_path else "[parent unknown]"
    return (
        f"Rework pass for previously one-iteration-accepted job `{row['job_id']}`.\n\n"
        "Important: this work is partially done already. Start by auditing existing implementation/artifacts, then patch only what is needed.\n\n"
        f"Layer: {layer}\n"
        f"Target child PIS: {row['pis_uid']} ({row['pis_title']})\n"
        f"Target parent PIS: {parent_line}\n"
        f"Source receipt: {row['receipt_path']}\n\n"
        "Required strategy:\n"
        "1) Audit existing implementation/docs/tests first.\n"
        "2) Patch only proven gaps; do not rewrite blindly.\n"
        "3) Produce verifier guidance from implementer perspective.\n\n"
        "Mandatory output sections:\n"
        "- Boundary Summary\n"
        "- Findings From Existing Work\n"
        "- Patch Summary\n"
        "- Verifier Prompt Additions\n"
        "- Validation Commands Run\n"
        "- Residual Risks / Proof Gaps\n"
        "- Commit Journal\n\n"
        "Verifier Prompt Additions requirements:\n"
        "- include concrete checks specific to this target and your changes\n"
        "- include boundary checks against sibling/parent contracts\n"
        "- include must-run validation selectors/commands\n"
        f"- write the final additions to `{additions_path}`\n\n"
        "Three-layer verification contract that must be satisfied:\n"
        "1) child PIS contract\n"
        "2) builder-specific verifier additions\n"
        "3) parent PIS constraints and boundaries\n"
    )


def _build_rework_job(
    *,
    row: Dict[str, Any],
    idx: int,
    layer: str,
    depends_on: List[str],
) -> Dict[str, Any]:
    stem = hashlib.sha256(str(row["job_id"]).encode("utf-8")).hexdigest()[:8]
    prefix = "q4a6a" if layer == "phase_a_pis_docs" else "q4a6b"
    job_id = _norm_job_id(f"overnight_20260314_{prefix}_rw_{idx:02d}_{stem}")
    parent_uid = _extract_parent_uid(str(row["pis_path"]))
    parent_path = _find_pis_path(parent_uid) if parent_uid else ""
    prompt = _rework_prompt(row=row, parent_uid=parent_uid, parent_path=parent_path, layer=layer)

    context_refs = [
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_agent_bootstrap.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_assistant_path.md",
        "docs/90_archive/markdown_source/persistent_files_chatgpt/lab_scheduler_verification_directives.md",
        "governance/agents/prompts/_all_codex.md",
        str(row["pis_path"]),
        str(row["spec_path"]),
        str(row["receipt_path"]),
    ]
    if parent_path:
        context_refs.append(parent_path)

    return {
        "schema_version": "scheduled_job_v1",
        "job_id": job_id,
        "job_kind": "build_verify_loop",
        "enabled": False,
        "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "binding": {"type": "ledger_uid", "id": BINDING_UID},
        "depends_on": list(depends_on),
        "prompt": prompt,
        "context_refs": context_refs,
        "expected_output": {"kind": "rework_and_strict_verify", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": RUN_AT_UTC, "timezone": "UTC"},
        "policy": {
            "allow_overlap": False,
            "max_runtime_seconds": 5400,
            "post_success_commit_tag": True,
            "post_success_commit_tag_retries": 2,
            "post_success_commit_tag_backoff_seconds": 60,
            "agent_turn_retries": 1,
            "agent_turn_retry_backoff_seconds": 15,
            "repo_change_mode": "strict",
            "require_validation_evidence": True,
            "require_clean_worktree": False,
            "require_commit_journal": True,
        },
        "loop": {"builder_role": "builder", "verifier_role": "verifier", "max_iterations": 4, "pass_marker": "VERDICT: PASS"},
        "runtime": _campaign_runtime_profile(),
    }


def _cleanup_old_q4a6_jobs() -> List[str]:
    changed: List[str] = []
    for spec_path in sorted(sched.jobs_dir(REPO_ROOT).glob("*.json")):
        spec = sched._read_json(spec_path)
        if not spec:
            continue
        jid = str(spec.get("job_id") or spec_path.stem)
        if not (jid.startswith("overnight_20260314_q4a6_rv_") or jid in {
            "overnight_20260314_q4a6_checker_generate_reverify_queue",
            "overnight_20260314_q4a6_reverify_rollup",
            "overnight_20260314_q4a6_reverify_confidence_report",
        }):
            continue
        if bool(spec.get("enabled")):
            sched.set_job_enabled(jid, False, repo_root=REPO_ROOT)
        changed.append(jid)
    return sorted(changed)


def main() -> int:
    targets, skipped = base._collect_targets()  # type: ignore[attr-defined]
    if not targets:
        raise SystemExit("No one-iteration targets found.")

    old_disabled = _cleanup_old_q4a6_jobs()

    docs_targets = [t for t in targets if _is_doc_generation_job(t)]
    impl_targets = [t for t in targets if t not in docs_targets]

    docs_targets = sorted(docs_targets, key=lambda r: str(r["job_id"]))
    impl_targets = sorted(impl_targets, key=lambda r: str(r["job_id"]))

    created_a: List[str] = []
    created_b: List[str] = []

    prev_dep: List[str] = []
    for i, row in enumerate(docs_targets, start=1):
        spec = _build_rework_job(row=row, idx=i, layer="phase_a_pis_docs", depends_on=prev_dep)
        out = sched.save_job(spec, repo_root=REPO_ROOT)
        jid = str(out.get("job_id") or spec["job_id"])
        created_a.append(jid)
        prev_dep = [jid]

    phase_a_rollup = "overnight_20260314_q4a6a_rollup"
    rollup_a_spec = {
        "schema_version": "scheduled_job_v1",
        "job_id": phase_a_rollup,
        "job_kind": "single_turn",
        "role": "verifier",
        "enabled": False,
        "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "binding": {"type": "ledger_uid", "id": BINDING_UID},
        "depends_on": prev_dep,
        "prompt": "Roll up phase A (child-PIS governance rework) outcomes and list approved verifier-prompt additions paths.",
        "context_refs": ["docs/reports/one_iteration_rework_campaign_plan_2026-03-14.md", "docs/reports/q4a6/verifier_additions"],
        "expected_output": {"kind": "q4a6_phase_a_rollup", "format": "markdown"},
        "schedule": {"mode": "once", "run_at": RUN_AT_UTC, "timezone": "UTC"},
            "policy": {"allow_overlap": False, "max_runtime_seconds": 1800, "repo_change_mode": "none"},
    }
    sched.save_job(rollup_a_spec, repo_root=REPO_ROOT)

    prev_dep = [phase_a_rollup]
    for i, row in enumerate(impl_targets, start=1):
        spec = _build_rework_job(row=row, idx=i, layer="phase_b_implementation", depends_on=prev_dep)
        out = sched.save_job(spec, repo_root=REPO_ROOT)
        jid = str(out.get("job_id") or spec["job_id"])
        created_b.append(jid)
        prev_dep = [jid]

    phase_b_rollup = "overnight_20260314_q4a6b_rollup"
    final_job = "overnight_20260314_q4a6c_final_confidence"

    sched.save_job(
        {
            "schema_version": "scheduled_job_v1",
            "job_id": phase_b_rollup,
            "job_kind": "single_turn",
            "role": "verifier",
            "enabled": False,
            "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "binding": {"type": "ledger_uid", "id": BINDING_UID},
            "depends_on": prev_dep,
            "prompt": "Roll up phase B (implementation rework) outcomes with keep/patch/rebuild classification and unresolved gaps.",
            "context_refs": ["docs/reports/one_iteration_rework_campaign_plan_2026-03-14.md", "docs/reports/q4a6/verifier_additions"],
            "expected_output": {"kind": "q4a6_phase_b_rollup", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": RUN_AT_UTC, "timezone": "UTC"},
            "policy": {"allow_overlap": False, "max_runtime_seconds": 2400, "repo_change_mode": "none"},
        },
        repo_root=REPO_ROOT,
    )

    sched.save_job(
        {
            "schema_version": "scheduled_job_v1",
            "job_id": final_job,
            "job_kind": "single_turn",
            "role": "verifier",
            "enabled": False,
            "created_utc": dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "binding": {"type": "ledger_uid", "id": BINDING_UID},
            "depends_on": [phase_b_rollup],
            "prompt": "Produce final confidence summary for Q4A6 rework campaign, with explicit note on deferred risks.",
            "context_refs": ["docs/reports/one_iteration_rework_campaign_plan_2026-03-14.md"],
            "expected_output": {"kind": "q4a6_final_confidence", "format": "markdown"},
            "schedule": {"mode": "once", "run_at": RUN_AT_UTC, "timezone": "UTC"},
            "policy": {"allow_overlap": False, "max_runtime_seconds": 1200, "repo_change_mode": "none"},
        },
        repo_root=REPO_ROOT,
    )

    lines: List[str] = []
    lines.append("# One-Iteration Rework Campaign Plan (2026-03-14)")
    lines.append("")
    lines.append(f"- target binding: `{BINDING_UID}`")
    lines.append(f"- old q4a6 queue marked superseded (disabled): `{len(old_disabled)}` jobs")
    lines.append(f"- phase A (child-PIS docs/governance) jobs: `{len(created_a)}`")
    lines.append(f"- phase B (implementation rework) jobs: `{len(created_b)}`")
    lines.append("")
    lines.append("## Cleanup")
    lines.append("")
    for jid in old_disabled:
        lines.append(f"- `{jid}`")
    lines.append("")
    lines.append("## Phase A Queue (Child-PIS Rework)")
    lines.append("")
    for jid in created_a:
        lines.append(f"- `{jid}`")
    lines.append(f"- `{phase_a_rollup}`")
    lines.append("")
    lines.append("## Phase B Queue (Implementation Rework)")
    lines.append("")
    for jid in created_b:
        lines.append(f"- `{jid}`")
    lines.append(f"- `{phase_b_rollup}`")
    lines.append(f"- `{final_job}`")
    lines.append("")
    lines.append("## Notes")
    lines.append("")
    lines.append("- Every rework job is a normal build/verify loop with max 4 iterations.")
    lines.append("- Builder is explicitly required to write/update verifier additions at `docs/reports/q4a6/verifier_additions/<source_job_id>.md`.")
    lines.append("- Verification contract is three-layer: child PIS, builder-specific additions, parent PIS.")
    lines.append("- Jobs are created disabled for controlled launch.")
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(
        json.dumps(
            {
                "old_queue_disabled_count": len(old_disabled),
                "phase_a_jobs": created_a,
                "phase_b_jobs": created_b,
                "phase_a_rollup": phase_a_rollup,
                "phase_b_rollup": phase_b_rollup,
                "final_job": final_job,
                "report_path": str(REPORT_PATH.relative_to(REPO_ROOT)),
                "skipped_count": len(skipped),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
