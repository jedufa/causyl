from __future__ import annotations

import argparse
import json
from pathlib import Path

from .cutover import evaluate_cutover_gates
from .ownership import load_ownership
from .runtime import run_shadow_tick
from .surfaces import build_internal_surface_bundle


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the v2 scheduler shadow runtime for one intent-family parent.")
    parser.add_argument("--parent-uid", default="")
    parser.add_argument("--run-at-utc", default="2026-04-10T00:00:00Z")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--mode", default="", help="legacy, shadow/v2_shadow, canary/v2_canary, primary/v2_primary, or empty for runtime policy")
    parser.add_argument("--surface-bundle", action="store_true", help="Emit the shared internal surface bundle for API/watch/web/mobile consumers.")
    parser.add_argument("--cutover-gates", action="store_true", help="Evaluate v2 primary cutover gates without switching ownership.")
    parser.add_argument("--benchmark-report", default="", help="Benchmark report path for --cutover-gates.")
    parser.add_argument("--allow-primary", action="store_true", help="Allow --cutover-gates to pass the explicit primary-ownership gate.")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    repo_root = Path(args.repo_root).resolve()
    if args.surface_bundle:
        payload = build_internal_surface_bundle(
            parent_uid=args.parent_uid,
            run_at_utc=args.run_at_utc,
            repo_root=repo_root,
            mode=args.mode or None,
        )
        print(json.dumps(payload, indent=2, sort_keys=True) if args.json else f"surface_bundle status={payload['status']} projection_equivalent={payload['projection_equivalent']}")
        return 0 if payload["status"] != "blocked" else 2

    if args.cutover_gates:
        payload = evaluate_cutover_gates(
            parent_uid=args.parent_uid,
            repo_root=repo_root,
            benchmark_report_path=Path(args.benchmark_report).resolve() if args.benchmark_report else None,
            target_mode=args.mode or None,
            allow_primary=bool(args.allow_primary),
        )
        if args.json:
            print(json.dumps(payload, indent=2, sort_keys=True))
        else:
            print(f"cutover_gates ready={payload['ready']} target_mode={payload['target_mode']} next_action={payload['next_action']}")
            for row in payload["gates"]:
                print(f"- {row['name']}: {'pass' if row['ok'] else 'block'} :: {row['detail']}")
        return 0 if payload["ready"] else 2

    if not str(args.parent_uid or "").strip():
        parser.error("--parent-uid is required unless --surface-bundle or --cutover-gates is used")
    ownership = load_ownership(mode=args.mode or None)
    payload = run_shadow_tick(
        parent_uid=args.parent_uid,
        run_at_utc=args.run_at_utc,
        repo_root=repo_root,
        mode=ownership.mode,
    )
    if args.json:
        print(json.dumps(payload, indent=2, sort_keys=True))
    else:
        evaluation = payload["evaluation"]
        print(
            f"v2 scheduler {ownership.mode} parent={evaluation['parent_uid']} "
            f"status={evaluation['family_status']} stage={evaluation['current_stage']} "
            f"canonical_owner={ownership.canonical_owner}"
        )
        for row in evaluation["admitted"]:
            print(f"- {row['child_uid']} {row['decision']} {row['reason']} gate={row['gate']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
