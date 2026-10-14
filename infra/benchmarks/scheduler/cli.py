from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .adapters import parse_adapter_spec
from .contracts import SCENARIO_FILE, load_scenarios
from .runner import run_scheduler_benchmark


def _split_csv(raw: str) -> tuple[str, ...]:
    return tuple(part.strip() for part in str(raw or "").split(",") if part.strip())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark candidate scheduler behavior against the current scheduler.")
    parser.add_argument("--scenario", default="one_workflow_shadow", help="Scenario key from infra/benchmarks/scheduler/scenarios.json")
    parser.add_argument("--baseline", default="current", help="`current`, `synthetic[:jobs=N][:topology=chain][:case=none]`, or `snapshot:/abs/path.json`")
    parser.add_argument("--candidate", default="", help="`current`, `v2[:parent_uid]`, `synthetic[:jobs=N][:topology=chain][:case=none]`, or `snapshot:/abs/path.json`")
    parser.add_argument("--baseline-label", default="legacy_scheduler")
    parser.add_argument("--candidate-label", default="candidate_scheduler")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--job-prefixes", default="", help="Comma-separated job_id prefixes to include.")
    parser.add_argument("--binding-uids", default="", help="Comma-separated ledger/intention UIDs to include.")
    parser.add_argument("--family-parent-uids", default="", help="Comma-separated family parent PIS UIDs to include.")
    parser.add_argument("--baseline-code-paths", default="infra/controlplane/scheduled_jobs.py,infra/controlplane/scheduled_jobs_parts,infra/controlplane/scheduler_projection_contract.py")
    parser.add_argument("--candidate-code-paths", default="infra/controlplane/sched")
    parser.add_argument("--receipt-limit", type=int, default=3)
    parser.add_argument("--report-dir", default="governance/evidence/benchmarks/scheduler/_results/latest")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-scenarios", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    scenarios = load_scenarios(SCENARIO_FILE)
    if args.list_scenarios:
        payload = [{"key": row.key, "description": row.description} for row in scenarios.values()]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    if not str(args.candidate or "").strip():
        parser.error("the following arguments are required: --candidate")
    scenario = scenarios.get(str(args.scenario or "").strip())
    if scenario is None:
        parser.error(f"unknown scenario `{args.scenario}`")

    repo_root = Path(args.repo_root).resolve()
    shared_job_prefixes = _split_csv(args.job_prefixes)
    shared_binding_uids = _split_csv(args.binding_uids)
    shared_family_uids = _split_csv(args.family_parent_uids)
    baseline_adapter = parse_adapter_spec(
        args.baseline,
        repo_root=repo_root,
        snapshot_label=args.baseline_label,
        scheduler_kind="legacy",
        job_id_prefixes=shared_job_prefixes,
        binding_uids=shared_binding_uids,
        family_parent_uids=shared_family_uids,
        code_paths=_split_csv(args.baseline_code_paths),
        receipt_limit=args.receipt_limit,
    )
    candidate_adapter = parse_adapter_spec(
        args.candidate,
        repo_root=repo_root,
        snapshot_label=args.candidate_label,
        scheduler_kind="candidate",
        job_id_prefixes=shared_job_prefixes,
        binding_uids=shared_binding_uids,
        family_parent_uids=shared_family_uids,
        code_paths=_split_csv(args.candidate_code_paths),
        receipt_limit=args.receipt_limit,
    )
    result = run_scheduler_benchmark(
        baseline_adapter=baseline_adapter,
        candidate_adapter=candidate_adapter,
        scenario=scenario,
        report_dir=Path(args.report_dir),
    )
    if args.json:
        print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    else:
        print(f"status={result.status} scenario={result.scenario_key} baseline={result.baseline_label} candidate={result.candidate_label}")
        for item in result.dimensions:
            print(f"- {item.dimension}: {item.verdict} :: {item.summary}")
    return 0 if result.status == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
