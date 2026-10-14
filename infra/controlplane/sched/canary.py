from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from infra.benchmarks.scheduler.adapters import CurrentPlaneSchedulerAdapter, V2IntentFamilySchedulerAdapter
from infra.benchmarks.scheduler.contracts import load_scenarios
from infra.benchmarks.scheduler.runner import run_scheduler_benchmark
from infra.webui.ledger import ledger_storage

from . import runtime


TARGET_SCHEMA_VERSION = "real_pis_scheduler_v2_shadow_canary_target_v1"
RECEIPT_SCHEMA_VERSION = "real_pis_scheduler_v2_shadow_canary_receipt_v1"
DEFAULT_TARGET_REL = Path("infra/controlplane/sched/real_pis_shadow_canary_target.json")
DEFAULT_ARTIFACT_ROOT_REL = Path("governance/evidence/benchmarks/scheduler/real_pis_shadow_canary")
APPROVED_ARTIFACT_ROOTS = (Path("governance/evidence/benchmarks/scheduler"), Path("infra/controlplane/sched"))
EXPECTED_CANONICAL_EXECUTOR = "scheduler_v1"
EXPECTED_SHADOW_EXECUTOR = "infra.controlplane.sched.run_shadow_tick"
EXPECTED_SELECTOR_CHILD_UID = "PIS-20260410-041856Z-A480"


class CanaryBlocked(RuntimeError):
    """Raised when the canary cannot run without leaving the approved shadow boundary."""


def _repo_root(repo_root: Path | None = None) -> Path:
    return Path(repo_root or runtime._repo_root()).resolve()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(dict(payload), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip("-") or "unknown"


def _sha256_json(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _approved_artifact_roots_from_target(target: Mapping[str, Any]) -> tuple[Path, ...]:
    roots = []
    for item in list(target.get("approved_artifact_roots") or []):
        normalized = str(item or "").strip()
        if normalized:
            roots.append(Path(normalized))
    if not roots:
        raise CanaryBlocked("durable canary target must declare approved_artifact_roots")
    unsupported = [root for root in roots if root not in APPROVED_ARTIFACT_ROOTS]
    if unsupported:
        joined = ", ".join(str(root) for root in unsupported)
        raise CanaryBlocked(f"durable canary target declared unsupported approved_artifact_roots: {joined}")
    return tuple(roots)


def _assert_approved_artifact_path(path: Path, *, repo_root: Path, approved_roots: Sequence[Path]) -> None:
    resolved = path.resolve()
    approved = [(repo_root / rel).resolve() for rel in approved_roots]
    if not any(_is_under(resolved, root) for root in approved):
        raise CanaryBlocked(f"artifact path is outside approved roots: {path}")


def load_canary_target(*, repo_root: Path | None = None, target_path: Path | None = None) -> dict[str, Any]:
    root = _repo_root(repo_root)
    path = (target_path if target_path is not None else root / DEFAULT_TARGET_REL).resolve()
    if not path.exists():
        raise CanaryBlocked(f"durable canary target is missing: {path}")
    target = _read_json(path)
    if str(target.get("schema_version") or "") != TARGET_SCHEMA_VERSION:
        raise CanaryBlocked("durable canary target has an unsupported schema_version")
    if str(target.get("target_kind") or "") != "intent_family_parent_pis":
        raise CanaryBlocked("real-PIS canary target must be an intent_family_parent_pis")
    if not str(target.get("target_uid") or "").strip():
        raise CanaryBlocked("real-PIS canary target_uid is required")
    if str(target.get("canonical_executor") or "").strip() != EXPECTED_CANONICAL_EXECUTOR:
        raise CanaryBlocked("real-PIS canary target must keep scheduler_v1 as canonical_executor")
    if str(target.get("shadow_executor") or "").strip() != EXPECTED_SHADOW_EXECUTOR:
        raise CanaryBlocked("real-PIS canary target must use infra.controlplane.sched.run_shadow_tick as shadow_executor")
    if str(target.get("selected_by_child_uid") or "").strip() != EXPECTED_SELECTOR_CHILD_UID:
        raise CanaryBlocked(f"real-PIS canary target must record {EXPECTED_SELECTOR_CHILD_UID} as selected_by_child_uid")
    _approved_artifact_roots_from_target(target)
    if not str(target.get("rationale") or "").strip():
        raise CanaryBlocked("real-PIS canary selection rationale is required")
    return target


def _ledger_dir_for_uid(repo_root: Path, uid: str) -> Path | None:
    normalized = str(uid or "").strip()
    if not normalized:
        return None
    root = repo_root / "governance/ledger/patch_instruction_sheets"
    resolved = ledger_storage.item_dir_for_uid(root, normalized)
    if resolved is not None:
        return resolved
    for meta_path in sorted(root.glob(f"**/{normalized}__*/meta.json")):
        return meta_path.parent
    return None


def _hash_file(path: Path) -> Mapping[str, Any]:
    if not path.exists():
        return {"exists": False, "sha256": ""}
    return {"exists": True, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def _canonical_truth_fingerprints(
    *,
    repo_root: Path,
    target_uid: str,
    child_uids: Sequence[str],
    job_ids: Sequence[str],
) -> dict[str, Mapping[str, Any]]:
    paths: list[Path] = []
    for uid in (target_uid, *child_uids):
        ledger_dir = _ledger_dir_for_uid(repo_root, uid)
        if ledger_dir is None:
            continue
        paths.extend([ledger_dir / "meta.json", ledger_dir / "events.jsonl", ledger_dir / "pis.md"])
    state_root = repo_root / "governance/agents/runtime/scheduled_jobs/state"
    if job_ids:
        paths.extend(state_root / f"{job_id}.json" for job_id in job_ids if str(job_id or "").strip())
    elif state_root.exists():
        paths.extend(state_root.glob("*.json"))
    out: dict[str, Mapping[str, Any]] = {}
    for path in sorted({item.resolve() for item in paths}):
        rel = str(path.relative_to(repo_root)) if _is_under(path, repo_root) else str(path)
        out[rel] = _hash_file(path)
    return out


def _job_ids_from_shadow(payload: Mapping[str, Any]) -> tuple[str, ...]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), Mapping) else {}
    jobs = [row for row in list(plan.get("jobs") or []) if isinstance(row, Mapping)]
    return tuple(str(row.get("job_id") or "").strip() for row in jobs if str(row.get("job_id") or "").strip())


def _child_uids_from_shadow(payload: Mapping[str, Any]) -> tuple[str, ...]:
    plan = payload.get("plan") if isinstance(payload.get("plan"), Mapping) else {}
    jobs = [row for row in list(plan.get("jobs") or []) if isinstance(row, Mapping)]
    return tuple(str(row.get("child_uid") or "").strip() for row in jobs if str(row.get("child_uid") or "").strip())


def _child_uids_from_parent_meta(*, repo_root: Path, parent_uid: str) -> tuple[str, ...]:
    parent_dir = _ledger_dir_for_uid(repo_root, parent_uid)
    if parent_dir is None:
        return ()
    meta = _read_json(parent_dir / "meta.json")
    integration = meta.get("integration_meta") if isinstance(meta.get("integration_meta"), Mapping) else {}
    return tuple(str(item).strip() for item in list(integration.get("child_uids") or []) if str(item).strip())


def run_real_pis_shadow_canary(
    *,
    run_at_utc: str,
    repo_root: Path | None = None,
    target_path: Path | None = None,
    artifact_root: Path | None = None,
) -> dict[str, Any]:
    root = _repo_root(repo_root)
    target = load_canary_target(repo_root=root, target_path=target_path)
    parent_uid = str(target["target_uid"]).strip()
    approved_roots = _approved_artifact_roots_from_target(target)

    target_child_uids = _child_uids_from_parent_meta(repo_root=root, parent_uid=parent_uid)
    before = _canonical_truth_fingerprints(repo_root=root, target_uid=parent_uid, child_uids=target_child_uids, job_ids=())
    shadow_payload = runtime.run_shadow_tick(parent_uid=parent_uid, run_at_utc=run_at_utc, repo_root=root)
    child_uids = _child_uids_from_shadow(shadow_payload)
    job_ids = _job_ids_from_shadow(shadow_payload)

    digest = _sha256_json({"target": target, "run_at_utc": run_at_utc, "shadow": shadow_payload})[:16]
    root_rel = artifact_root if artifact_root is not None else root / DEFAULT_ARTIFACT_ROOT_REL
    artifact_dir = (root_rel / _safe_segment(parent_uid) / f"{_safe_segment(run_at_utc)}__{digest}").resolve()
    _assert_approved_artifact_path(artifact_dir, repo_root=root, approved_roots=approved_roots)
    benchmark_dir = artifact_dir / "benchmark"

    benchmark_status = "not_run"
    benchmark_error = ""
    benchmark_result: Mapping[str, Any] | None = None
    try:
        scenario = load_scenarios()["one_workflow_shadow"]
        benchmark = run_scheduler_benchmark(
            baseline_adapter=CurrentPlaneSchedulerAdapter(
                repo_root=root,
                snapshot_label="scheduler_v1_canonical",
                scheduler_kind="legacy",
                job_id_prefixes=job_ids,
                family_parent_uids=(parent_uid,),
                code_paths=(
                    "infra/controlplane/scheduled_jobs.py",
                    "infra/controlplane/scheduled_jobs_parts",
                    "infra/controlplane/scheduler_projection_contract.py",
                ),
            ),
            candidate_adapter=V2IntentFamilySchedulerAdapter(
                repo_root=root,
                snapshot_label="scheduler_v2_shadow",
                scheduler_kind="candidate",
                parent_uids=(parent_uid,),
                code_paths=("infra/controlplane/sched",),
                run_at_utc=run_at_utc,
            ),
            scenario=scenario,
            report_dir=benchmark_dir,
        )
        benchmark_status = benchmark.status
        benchmark_result = benchmark.to_dict()
    except Exception as exc:  # pragma: no cover - exercised via status fields in integration paths.
        benchmark_status = "blocked"
        benchmark_error = f"{type(exc).__name__}: {exc}"

    after = _canonical_truth_fingerprints(
        repo_root=root,
        target_uid=parent_uid,
        child_uids=child_uids or target_child_uids,
        job_ids=(),
    )
    canonical_truth_mutated = before != after
    evaluation = shadow_payload.get("evaluation") if isinstance(shadow_payload.get("evaluation"), Mapping) else {}
    shadow_mutates = bool(evaluation.get("mutates_canonical_state"))
    status = "pass" if benchmark_status == "pass" and not canonical_truth_mutated and not shadow_mutates else "blocked"
    blockers: list[str] = []
    if benchmark_status != "pass":
        blockers.append(f"benchmark_status:{benchmark_status}")
    if benchmark_error:
        blockers.append(benchmark_error)
    if canonical_truth_mutated:
        blockers.append("canonical_truth_fingerprint_changed")
    if shadow_mutates:
        blockers.append("shadow_runtime_reported_canonical_mutation")

    shadow_path = artifact_dir / "shadow_tick.json"
    fingerprints_path = artifact_dir / "canonical_truth_fingerprints.json"
    receipt_path = artifact_dir / "canary_receipt.json"
    _write_json_atomic(shadow_path, shadow_payload)
    _write_json_atomic(fingerprints_path, {"before": before, "after": after, "changed": canonical_truth_mutated})
    receipt = {
        "schema_version": RECEIPT_SCHEMA_VERSION,
        "status": status,
        "blockers": blockers,
        "target": target,
        "run_at_utc": run_at_utc,
        "artifact_dir": str(artifact_dir.relative_to(root)),
        "artifacts": {
            "shadow_tick": str(shadow_path.relative_to(root)),
            "canonical_truth_fingerprints": str(fingerprints_path.relative_to(root)),
            "canary_receipt": str(receipt_path.relative_to(root)),
            "benchmark_report_json": str((benchmark_dir / "scheduler_benchmark_report.json").relative_to(root)),
            "benchmark_report_md": str((benchmark_dir / "scheduler_benchmark_report.md").relative_to(root)),
        },
        "benchmark_status": benchmark_status,
        "benchmark_error": benchmark_error,
        "benchmark_result": benchmark_result,
        "shadow_mode": "shadow",
        "v1_scheduler_remains_canonical": True,
        "v2_shadow_mutates_canonical_state": shadow_mutates,
        "canonical_truth_mutated": canonical_truth_mutated,
    }
    _write_json_atomic(receipt_path, receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the repeatable real-PIS scheduler v2 shadow canary.")
    parser.add_argument("--run-at-utc", default="2026-04-10T04:29:00Z")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--target-path", default="")
    parser.add_argument("--artifact-root", default="")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    receipt = run_real_pis_shadow_canary(
        run_at_utc=args.run_at_utc,
        repo_root=Path(args.repo_root).resolve(),
        target_path=Path(args.target_path).resolve() if args.target_path else None,
        artifact_root=Path(args.artifact_root).resolve() if args.artifact_root else None,
    )
    if args.json:
        print(json.dumps(receipt, indent=2, sort_keys=True))
    else:
        print(f"real-PIS scheduler v2 shadow canary status={receipt['status']} benchmark={receipt['benchmark_status']}")
        print(f"artifact_dir={receipt['artifact_dir']}")
        for blocker in receipt["blockers"]:
            print(f"- {blocker}")
    return 0 if receipt["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
