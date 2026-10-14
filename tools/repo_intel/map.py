"""Deterministic repo intelligence and impact map builder.

Last modified: 2026-03-30 23:57 Europe/Zurich
"""

from __future__ import annotations

import argparse
import fnmatch
from pathlib import Path
from typing import Any, Sequence

from tools.deterministic_tooling.common import (
    build_envelope,
    canonical_source,
    ensure_runtime_dir,
    git_head,
    hash_object,
    iter_existing_paths,
    load_cache,
    normalized_list,
    read_text,
    repo_rel,
    store_cache,
    write_output,
)

SCHEMA_VERSION = "deterministic_tooling_envelope_v1"
TOOL_NAME = "repo_intel_and_impact_map"
CACHE_TTL_S = 1800.0
_CRITICAL_PREFIXES = (
    "infra/webui/",
    "tools/git/",
    "infra/controlplane/",
    "config/runtime_policy.properties",
    "governance/ledger/",
)


def _cache_key(*, repo_root: Path, target_files: Sequence[str], policy_path: str) -> str:
    return hash_object({"git_head": git_head(repo_root), "target_files": list(target_files or []), "policy_path": policy_path}).split(":", 1)[1]


def _tests_for_file(repo_root: Path, rel_path: str) -> list[str]:
    tests_root = repo_root / "tests"
    if not tests_root.exists():
        return []
    rel = str(rel_path or "")
    stem = Path(rel).stem.lower()
    parent = Path(rel).parent.name.lower()
    matches: list[str] = []
    for test_path in sorted(tests_root.rglob("test_*.py")):
        candidate = repo_rel(repo_root, test_path)
        name = test_path.name.lower()
        text = ""
        if stem and stem in name:
            matches.append(candidate)
            continue
        if parent and parent in candidate.lower():
            matches.append(candidate)
            continue
        if stem:
            try:
                text = read_text(test_path)
            except Exception:
                text = ""
            if stem and stem in text.lower():
                matches.append(candidate)
    return normalized_list(matches)


def _owner_refs(repo_root: Path, rel_path: str, policy_path: str) -> list[str]:
    owners: list[str] = []
    if policy_path:
        owners.append(policy_path)
    path_text = str(rel_path)
    ledger_root = repo_root / "governance" / "ledger" / "items"
    if ledger_root.exists():
        for pis_path in sorted(ledger_root.rglob("pis.md")):
            try:
                text = read_text(pis_path)
            except Exception:
                continue
            if path_text in text:
                owners.append(repo_rel(repo_root, pis_path))
                if len(owners) >= 4:
                    break
    top_level = path_text.split("/", 1)[0]
    if top_level:
        owners.append(f"owner:{top_level}")
    return normalized_list(owners)


def _risk_score(rel_path: str) -> int:
    for prefix in _CRITICAL_PREFIXES:
        if rel_path.startswith(prefix):
            return 3
    if rel_path.startswith("tools/") or rel_path.startswith("config/"):
        return 2
    return 1


def build_impact_map(*, repo_root: Path, target_files: Sequence[str], policy_path: str = "", blocking_tags: Sequence[str] = ()) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    key = _cache_key(repo_root=repo_root, target_files=target_files, policy_path=policy_path)
    cache_dir = ensure_runtime_dir(repo_root, "repo_intel_cache")
    cache_path = cache_dir / f"{key}.json"
    cached = load_cache(cache_path, ttl_s=CACHE_TTL_S)
    if cached is not None:
        return cached

    warnings: list[dict[str, Any]] = []
    module_rows: list[dict[str, Any]] = []
    test_refs: list[str] = []
    provenance: list[dict[str, Any]] = []
    for path in iter_existing_paths(repo_root, target_files):
        rel = repo_rel(repo_root, path)
        owners = _owner_refs(repo_root, rel, policy_path)
        tests = _tests_for_file(repo_root, rel)
        for test_path in tests:
            if test_path not in test_refs:
                test_refs.append(test_path)
        score = _risk_score(rel)
        row = {
            "path": rel,
            "module": rel.replace("/", ".").rsplit(".", 1)[0],
            "owners": owners,
            "tests": tests,
            "risk_score": score,
            "critical": score >= 3,
        }
        module_rows.append(row)
        provenance.append(canonical_source(repo_root, path))
    module_rows.sort(key=lambda row: (-int(row["risk_score"]), ",".join(row["owners"]), str(row["path"])))
    if not module_rows:
        warnings.append({"code": "no_target_files", "severity": "warning", "message": "No target files were found for impact mapping."})

    outputs = {
        "module_graph": module_rows,
        "impact_risk_rank": [{"path": row["path"], "risk_score": row["risk_score"], "owners": row["owners"]} for row in module_rows],
        "test_matrix": [{"path": path, "reason": "name_or_content_match"} for path in sorted(test_refs)],
        "missing_ref_warnings": [row["path"] for row in module_rows if not row["owners"]],
    }
    envelope = build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={
            "repo_root": str(repo_root),
            "target_files": normalized_list(str(item) for item in target_files),
            "policy_path": str(policy_path or "").strip(),
            "blocking_tags": normalized_list(str(item) for item in blocking_tags),
            "git_head": git_head(repo_root),
        },
        outputs=outputs,
        warnings=warnings,
        provenance=provenance,
        artifacts=[{"path": repo_rel(repo_root, cache_path), "kind": "cache_entry"}],
    )
    store_cache(cache_path, envelope)
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic repo impact map")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--target-files", nargs="*", default=[])
    parser.add_argument("--policy", default="")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    payload = build_impact_map(repo_root=Path(args.repo_root), target_files=args.target_files, policy_path=args.policy)
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
