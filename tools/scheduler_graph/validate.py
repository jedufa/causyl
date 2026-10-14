from __future__ import annotations

import argparse
import json
from collections import defaultdict, deque
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from infra.controlplane import scheduled_jobs as sched


REPORT_SCHEMA_VERSION = "scheduler_graph_v1"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _utc_iso() -> str:
    return sched._utc_iso()


def _write_json(path: Path, obj: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _graph_cache_dir(repo_root: Path) -> Path:
    path = (repo_root / "runtime" / "state" / "scheduler_graph").resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def _load_spec(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    try:
        return sched.load_job(job_id, repo_root=repo_root)
    except Exception:
        return {}


def _load_state(job_id: str, *, repo_root: Path) -> Dict[str, Any]:
    try:
        return sched._load_json(sched._state_path(job_id, repo_root=repo_root))
    except Exception:
        return {}


def _normalize_job_ids(job_ids: Optional[Iterable[str]], *, repo_root: Path) -> List[str]:
    if job_ids:
        out: List[str] = []
        for item in job_ids:
            txt = str(item or "").strip()
            if txt and txt not in out:
                out.append(txt)
        return out
    rows = sched.list_jobs(repo_root=repo_root, now_utc=_utc_iso())
    return [str(row.get("job_id") or "") for row in rows if str(row.get("job_id") or "").strip()]


def _topological_order(nodes: List[str], deps: Dict[str, List[str]]) -> Tuple[List[str], List[str]]:
    indegree: Dict[str, int] = {node: 0 for node in nodes}
    dependents: Dict[str, List[str]] = defaultdict(list)
    for node in nodes:
        for dep in deps.get(node, []):
            if dep not in indegree:
                continue
            indegree[node] += 1
            dependents[dep].append(node)
    queue = deque(sorted([node for node, degree in indegree.items() if degree == 0]))
    out: List[str] = []
    while queue:
        node = queue.popleft()
        out.append(node)
        for child in sorted(dependents.get(node, [])):
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    cycles = sorted([node for node, degree in indegree.items() if degree > 0])
    return out, cycles


def build_scheduler_graph_report(*, repo_root: Optional[Path] = None, job_ids: Optional[Iterable[str]] = None, write_cache: bool = True) -> Dict[str, Any]:
    root = Path(repo_root or _repo_root()).resolve()
    selected_job_ids = _normalize_job_ids(job_ids, repo_root=root)
    nodes: List[Dict[str, Any]] = []
    deps: Dict[str, List[str]] = {}
    missing_dependencies: List[Dict[str, str]] = []
    blocking_reasons: List[Dict[str, str]] = []
    for job_id in selected_job_ids:
        spec = _load_spec(job_id, repo_root=root)
        if not spec:
            continue
        state = _load_state(job_id, repo_root=root)
        depends_on = [str(x or "").strip() for x in list(spec.get("depends_on") or []) if str(x or "").strip()]
        deps[job_id] = depends_on
        nodes.append(
            {
                "job_id": job_id,
                "enabled": bool(spec.get("enabled", True)),
                "display_title": str(spec.get("display_title") or job_id),
                "last_status": str(state.get("last_status") or ""),
                "active_run_id": str(state.get("active_run_id") or ""),
                "depends_on": depends_on,
            }
        )
    node_ids = [str(row["job_id"]) for row in nodes]
    node_set = set(node_ids)
    for node in nodes:
        for dep in list(node.get("depends_on") or []):
            if dep not in node_set:
                missing_dependencies.append({"job_id": str(node["job_id"]), "missing_dep": dep})
                blocking_reasons.append({"job_id": str(node["job_id"]), "reason": f"missing_dependency:{dep}"})
    order, cycles = _topological_order(node_ids, deps)
    for node in nodes:
        for dep in list(node.get("depends_on") or []):
            dep_spec = _load_spec(dep, repo_root=root)
            dep_state = _load_state(dep, repo_root=root)
            if dep_spec and not bool(dep_spec.get("enabled", True)):
                blocking_reasons.append({"job_id": str(node["job_id"]), "reason": f"dependency_disabled:{dep}"})
            if str(dep_state.get("last_status") or "").lower() in {"blocked", "failed_verification", "proof_gap", "error"}:
                blocking_reasons.append({"job_id": str(node["job_id"]), "reason": f"dependency_not_ok:{dep}"})
    for cycle_job in cycles:
        blocking_reasons.append({"job_id": cycle_job, "reason": "cycle_detected"})
    report = {
        "schema_version": REPORT_SCHEMA_VERSION,
        "generated_utc": _utc_iso(),
        "job_count": len(nodes),
        "validated_graph": {
            "nodes": nodes,
            "edges": [{"from": dep, "to": node} for node, node_deps in deps.items() for dep in node_deps],
        },
        "priority_order": order,
        "blocking_reasons": blocking_reasons,
        "missing_dependencies": missing_dependencies,
        "retry_plan": {
            "blocked_job_ids": sorted({str(row["job_id"]) for row in blocking_reasons}),
            "cycle_job_ids": cycles,
        },
    }
    if write_cache:
        _write_json((_graph_cache_dir(root) / "latest_report.json").resolve(), report)
    return report


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate scheduler job dependency graph.")
    parser.add_argument("--job-id", action="append", dest="job_ids", default=[], help="Limit validation to one or more job ids.")
    parser.add_argument("--no-cache", action="store_true", help="Do not write runtime/state/scheduler_graph/latest_report.json")
    args = parser.parse_args(argv)
    report = build_scheduler_graph_report(job_ids=list(args.job_ids or []), write_cache=not bool(args.no_cache))
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
