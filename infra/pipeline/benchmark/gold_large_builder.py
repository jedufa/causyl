from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path
from typing import Dict, List


SELECTED_CASES: List[Dict[str, str]] = [
    {"uid": "PIS-20260310-114704Z-DDD3", "folder": "PIS-20260310-114704Z-DDD3__workflow-resume-after-restart", "theme": "mobile_resume"},
    {"uid": "PIS-20260310-114706Z-2CAC", "folder": "PIS-20260310-114706Z-2CAC__idempotent-orchestration-turn-checkpointing", "theme": "orchestration_checkpointing"},
    {"uid": "PIS-20260310-114708Z-D611", "folder": "PIS-20260310-114708Z-D611__detect-truncated-chat-streams", "theme": "chat_stream_recovery"},
    {"uid": "PIS-20260310-221346Z-1476", "folder": "PIS-20260310-221346Z-1476__explicit-chat-persistence-state-machine-and-recovery-contract", "theme": "chat_persistence_truth"},
    {"uid": "PIS-20260310-222741Z-8D51", "folder": "PIS-20260310-222741Z-8D51__durable-orchestrator-execution-leases-and-restart-recovery", "theme": "execution_leases"},
    {"uid": "PIS-20260312-161622Z-2848", "folder": "PIS-20260312-161622Z-2848__persistent-assistant-identity-and-source-of-truth-contract", "theme": "assistant_truth"},
    {"uid": "PIS-20260312-161622Z-BD05", "folder": "PIS-20260312-161622Z-BD05__durable-task-register-and-open-loop-memory-for-lab-assistant-v1", "theme": "task_register"},
    {"uid": "PIS-20260312-161622Z-DD8C", "folder": "PIS-20260312-161622Z-DD8C__fresh-turn-bootstrap-and-context-reload-over-oneshot-runtime", "theme": "bootstrap_reload"},
    {"uid": "PIS-20260312-215411Z-C735", "folder": "PIS-20260312-215411Z-C735__scheduled-execution-robustness-audit-and-hardening-pass", "theme": "scheduler_recovery"},
    {"uid": "PIS-20260312-224201Z-C8D5", "folder": "PIS-20260312-224201Z-C8D5__long-running-job-lifecycle-heartbeat-timeout-and-reconciliation-contract", "theme": "long_running_jobs"},
    {"uid": "PIS-20260312-230708Z-H3K1", "folder": "PIS-20260312-230708Z-H3K1__task-intake-clarification-and-work-decomposition-for-lab-assistant-v1", "theme": "task_intake"},
    {"uid": "PIS-20260313-030600Z-U1V3", "folder": "PIS-20260313-030600Z-U1V3__workflow-checkpoint-boundaries-residue-and-interrupt-recovery-contract", "theme": "workflow_interrupt_recovery"},
    {"uid": "PIS-20260313-030601Z-V2W4", "folder": "PIS-20260313-030601Z-V2W4__detached-job-heartbeat-cancellation-timeout-and-boot-reconciliation-contract", "theme": "detached_job_recovery"},
    {"uid": "PIS-20260313-033642Z-R8T3", "folder": "PIS-20260313-033642Z-R8T3__durable-turns-chats-and-reattach-for-lab-assistant-v1", "theme": "turn_reattach"},
    {"uid": "PIS-20260313-040414Z-X5V8", "folder": "PIS-20260313-040414Z-X5V8__final-transcript-commit-idempotency-and-turn-to-session-reconciliation-contract", "theme": "transcript_commit"},
    {"uid": "PIS-20260313-072021Z-B707", "folder": "PIS-20260313-072021Z-B707__proof-hardening-and-operations-for-lab-assistant-v1", "theme": "proof_hardening"},
    {"uid": "PIS-20260313-094300Z-M2N4", "folder": "PIS-20260313-094300Z-M2N4__bootstrap-to-task-register-reconstruction-and-degraded-bootstrap-contract", "theme": "bootstrap_task_truth"},
    {"uid": "PIS-20260313-100602Z-N1S3", "folder": "PIS-20260313-100602Z-N1S3__ui-projection-staleness-degraded-and-unreachable-state-contract", "theme": "ui_truth_projection"},
    {"uid": "PIS-20260313-100604Z-Q3U5", "folder": "PIS-20260313-100604Z-Q3U5__replacement-backend-cold-start-reconstruction-and-takeover-compatibility", "theme": "backend_takeover"},
    {"uid": "PIS-20260313-105101Z-406F", "folder": "PIS-20260313-105101Z-406F__overnight-scheduled-chain-to-morning-result-scenario-contract", "theme": "scenario_overnight"},
    {"uid": "PIS-20260313-141709Z-M2C6", "folder": "PIS-20260313-141709Z-M2C6__adaptive-autonomy-and-multi-assistant-coordination-for-lab-assistant-v2-plus", "theme": "multi_assistant_parent"},
    {"uid": "PIS-20260313-143605Z-T8J3", "folder": "PIS-20260313-143605Z-T8J3__supervisory-orchestration-checkpointing-timeout-and-recovery-contract-for-multi-assistant-execution", "theme": "supervisory_orchestration"},
    {"uid": "PIS-20260313-144500Z-X3J7", "folder": "PIS-20260313-144500Z-X3J7__typed-workcell-endpoint-registry-capability-and-preflight-contract", "theme": "workcell_registry"},
    {"uid": "PIS-20260313-150402Z-J5U9", "folder": "PIS-20260313-150402Z-J5U9__cross-project-retrieval-ranking-freshness-and-evidence-first-answer-assembly-con", "theme": "retrieval_ranking"},
    {"uid": "PIS-20260313-150403Z-K6V1", "folder": "PIS-20260313-150403Z-K6V1__contradiction-detection-correction-workflow-and-memory-conflict-resolution-contr", "theme": "memory_conflict_resolution"},
    {"uid": "PIS-20260313-152502Z-S7Q1", "folder": "PIS-20260313-152502Z-S7Q1__long-horizon-execution-loop-milestone-checkpoint-and-replanning-contract", "theme": "long_horizon_replanning"},
    {"uid": "PIS-20260313-154200Z-R2Y7", "folder": "PIS-20260313-154200Z-R2Y7__multi-operator-authority-tenancy-and-high-impact-approval-contract", "theme": "multi_operator_governance"},
    {"uid": "PIS-20260314-041711Z-Q4A6", "folder": "PIS-20260314-041711Z-Q4A6__one-iteration-pis-quality-audit-and-verifier-strictness-recalibration", "theme": "quality_audit"},
    {"uid": "PIS-20260314-200630Z-T9L5", "folder": "PIS-20260314-200630Z-T9L5__deterministic-local-tooling-and-token-efficiency-fabric-for-lab-operations", "theme": "local_tooling"},
    {"uid": "PIS-20260314-224900Z-C0DX", "folder": "PIS-20260314-224900Z-C0DX__jeff-codex-like-interactive-ux-contract", "theme": "interactive_cli"},
]

SCORECARD_TEXT = """## Embedded scorecard

Use this scorecard to evaluate the quality of the returned reasoning trace after download.
Do not score style. Score reasoning quality, file-selection quality, and boundary discipline.

| Dimension | Max | What to look for |
|---|---:|---|
| Task understanding | 5 | Correctly frames the contract or feature without drifting into unrelated redesign |
| Domain identification | 5 | Identifies the real owning subsystems and truth boundaries |
| Failure mode coverage | 5 | Captures the important restart, divergence, race, stale, blocked, or safety failures implied by the PIS |
| Invariant quality | 5 | States non-negotiable rules, authority, idempotency, or recovery constraints clearly |
| Retrieval quality | 5 | Candidate files are plausible, focused, and owner-first rather than generic gravity picks |
| Boundary discipline | 5 | Respects explicit non-goals and avoids over-claiming adjacent modules |
| Patch-plan usefulness | 5 | Proposed changes and tests are implementable and narrow |
| Hallucination or drift penalty | -5 to 0 | Penalize invented files, fake certainty, or broad unrelated architecture drift |

Base total: 35
Final score: Base total + penalty
"""

OUTPUT_SCHEMA = """```yaml
case:
  id: ...
  title: ...
  mode: gpt
  evidence_scope: pis_only

task_understanding:
  normalized_goal: ...
  in_scope: [...]
  out_of_scope: [...]

reasoning:
  primary_domains: [...]
  subsystems: [...]
  failure_modes: [...]
  invariants: [...]
  source_of_truth_rules: [...]
  recovery_expectations: [...]
  arbitration_or_concurrency_rules: [...]

retrieval_plan:
  lexical_queries: [...]
  conceptual_queries: [...]
  subsystem_hypotheses: [...]
  negative_targets: [...]
  query_coverage_map:
    "<query>":
      covers: [...]
      subsystem: [...]
      supports_flags: [...]

candidate_files:
  implementation_targets:
    - path: ...
      rationale: ...
      confidence: 0.0
  reference_context:
    - path: ...
      rationale: ...
  forbidden_or_demoted:
    - path_or_pattern: ...
      reason: ...

patch_plan:
  planned_changes: [...]
  tests_to_add_or_update: [...]
  unresolved_questions: [...]

confidence:
  overall: 0.0
  notes: [...]
```"""


def _title_from_pis(text: str) -> str:
    lines = text.splitlines()
    in_title = False
    first_heading = ""
    for raw in lines:
        stripped = raw.strip()
        if not first_heading and stripped.startswith("# "):
            first_heading = stripped[2:].strip()
        if stripped.startswith("## 1. Title"):
            in_title = True
            continue
        if in_title:
            if not stripped:
                continue
            if stripped.startswith("## "):
                break
            return stripped
    return first_heading


def _slug(folder_name: str) -> str:
    parts = folder_name.split("__", 1)
    return parts[1] if len(parts) == 2 else folder_name


def _prompt_text(uid: str, title: str, pis_text: str, folder_name: str) -> str:
    return f"""# GPT gold-large prompt for {uid}

You are being used as a benchmark reasoning engine, not as a prose assistant.

Your job is to analyze one Patch Instruction Sheet and produce a structured reasoning trace that can be compared against a deterministic pipeline.

You must behave like a careful systems architect.
Do not try to impress.
Do not write an essay.
Do not invent files or subsystems you cannot justify.

## Benchmark case

- UID: `{uid}`
- Title: `{title}`
- Folder: `{folder_name}`

## Raw PIS

```md
{pis_text.rstrip()}
```

## Your task

Produce a structured reasoning trace in valid YAML using the schema below.

### Rules

1. Stay within the PIS boundary.
2. Do not redesign the whole system.
3. Do not invent exact files unless you have clear evidence from the PIS.
4. If file-level certainty is low, provide likely implementation surfaces and confidence values.
5. Separate implementation targets, reference context, and forbidden or demoted targets.
6. Be explicit about source-of-truth rules, recovery rules, and concurrency or arbitration rules when relevant.
7. Keep it compact and concrete.
8. Prefer owner-first file selection and adjacent tests over generic hub files.
9. Mention uncertainty honestly instead of filling gaps with architectural fantasies.

## Required output schema

{OUTPUT_SCHEMA}

{SCORECARD_TEXT}

## Downloadable output requirement

Your final answer must be delivered as a downloadable file attachment named:

`{uid}__reference_trace.yaml`

The attached file must contain only valid YAML matching the schema above.
Do not put the YAML inline in the visible chat body.

If attachment creation is available, provide the YAML as a downloadable file and keep any visible chat text minimal.
If attachment creation is not available, return only this exact text in the visible chat:

`ATTACHMENT_CREATION_FAILED`
"""


def build_gold_large(repo_root: Path) -> Dict[str, str]:
    items_root = repo_root / "governance" / "ledger" / "items"
    gold_root = repo_root / "benchmarks" / "pipeline" / "benchmark-gpt" / "gold_large" / "gold_items_large"
    prompt_root = repo_root / "benchmarks" / "pipeline" / "benchmark-gpt" / "gold_large" / "gpt_prompts"
    manifest_json = repo_root / "benchmarks" / "pipeline" / "benchmark-gpt" / "gold_large" / "selection_manifest.json"
    manifest_md = repo_root / "benchmarks" / "pipeline" / "benchmark-gpt" / "gold_large" / "selection_manifest.md"

    gold_root.mkdir(parents=True, exist_ok=True)
    prompt_root.mkdir(parents=True, exist_ok=True)

    manifest_rows = []
    for case in SELECTED_CASES:
        src = items_root / case["folder"]
        dst = gold_root / case["folder"]
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(src, dst)

        pis_path = src / "pis.md"
        pis_text = pis_path.read_text(encoding="utf-8", errors="replace")
        title = _title_from_pis(pis_text) or case["uid"]
        prompt_name = f"{case['uid']}__{_slug(case['folder'])}.md"
        prompt_path = prompt_root / prompt_name
        prompt_path.write_text(_prompt_text(case["uid"], title, pis_text, case["folder"]), encoding="utf-8")

        manifest_rows.append(
            {
                "uid": case["uid"],
                "folder": case["folder"],
                "theme": case["theme"],
                "title": title,
                "copied_to": str(dst.relative_to(repo_root)),
                "prompt_path": str(prompt_path.relative_to(repo_root)),
            }
        )

    manifest_json.write_text(json.dumps({"selected_cases": manifest_rows}, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    md_lines = [
        "# Gold Large Selection Manifest",
        "",
        f"- Cases: `{len(manifest_rows)}`",
        "",
        "| UID | Theme | Title | Prompt |",
        "|---|---|---|---|",
    ]
    for row in manifest_rows:
        md_lines.append(
            f"| {row['uid']} | {row['theme']} | {row['title']} | `{row['prompt_path']}` |"
        )
    manifest_md.write_text("\n".join(md_lines).rstrip() + "\n", encoding="utf-8")
    return {
        "gold_root": str(gold_root),
        "prompt_root": str(prompt_root),
        "manifest_json": str(manifest_json),
        "manifest_md": str(manifest_md),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Copy selected large gold PIS folders and generate GPT prompts.")
    parser.add_argument("--repo-root", default=".")
    args = parser.parse_args()
    result = build_gold_large(Path(args.repo_root).resolve())
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
