"""Deterministic verification and proof bundler.

Last modified: 2026-03-30 23:57 Europe/Zurich
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.deterministic_tooling.common import build_envelope, ensure_runtime_dir, git_head, hash_object, load_cache, normalized_list, read_json, repo_rel, store_cache, write_output

SCHEMA_VERSION = "proof_bundle_v1"
TOOL_NAME = "verification_and_proof_bundler"
CACHE_TTL_S = 900.0


def _selector_rank(selector: str) -> tuple[int, str]:
    text = str(selector or "")
    severity = 0 if text.startswith("tests/") else 1
    return (severity, text)


def _collect_selectors(selectors: Sequence[str], impact_map: Mapping[str, Any] | None) -> list[str]:
    out = normalized_list(selectors)
    if out:
        return sorted(out, key=_selector_rank)
    if not isinstance(impact_map, Mapping):
        return []
    tests = impact_map.get("outputs", {}).get("test_matrix") if isinstance(impact_map.get("outputs"), Mapping) else impact_map.get("test_matrix")
    if not isinstance(tests, list):
        return []
    for row in tests:
        if isinstance(row, Mapping):
            path = str(row.get("path") or "").strip()
            if path:
                out.append(path)
    return sorted(normalized_list(out), key=_selector_rank)


def build_verification_bundle(
    *,
    repo_root: Path,
    target_pis_uid: str,
    selectors: Sequence[str] = (),
    changed_files: Sequence[str] = (),
    impact_map: Mapping[str, Any] | None = None,
    previous_evidence: Sequence[str] = (),
    require_evidence: bool = False,
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    cache_dir = ensure_runtime_dir(repo_root, "verification_bundle")
    key = hash_object({
        "git_head": git_head(repo_root),
        "target_pis_uid": target_pis_uid,
        "selectors": list(selectors or []),
        "changed_files": list(changed_files or []),
        "previous_evidence": list(previous_evidence or []),
        "require_evidence": bool(require_evidence),
    }).split(":", 1)[1]
    cache_path = cache_dir / f"{key}.json"
    cached = load_cache(cache_path, ttl_s=CACHE_TTL_S)
    if cached is not None:
        return cached

    warnings: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    selector_list = _collect_selectors(selectors, impact_map)
    if not selector_list:
        errors.append({"code": "missing_selectors", "severity": "error", "message": "No verification selectors were available."})
    evidence_paths = normalized_list(previous_evidence)
    missing_bucket: list[str] = []
    for selector in selector_list:
        if not evidence_paths:
            missing_bucket.append(selector)
    if require_evidence and missing_bucket:
        errors.append({"code": "missing_evidence", "severity": "error", "message": "Required evidence was not provided for all selectors."})
    test_plan = [
        {
            "selector": selector,
            "command": f"pytest {selector}",
            "severity": "primary" if selector.startswith("tests/") else "secondary",
        }
        for selector in selector_list
    ]
    proof_score = "blocked" if errors else ("missing_evidence" if missing_bucket else "ready")
    outputs = {
        "test_plan": test_plan,
        "evidence_manifest": {
            "target_pis_uid": str(target_pis_uid or "").strip(),
            "changed_files": normalized_list(changed_files),
            "previous_evidence": evidence_paths,
            "missing_bucket": missing_bucket,
        },
        "proof_scorecard": {
            "proof_score": proof_score,
            "selector_count": len(selector_list),
            "missing_evidence_count": len(missing_bucket),
        },
        "missing_bucket": missing_bucket,
    }
    envelope = build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={
            "repo_root": str(repo_root),
            "target_pis_uid": str(target_pis_uid or "").strip(),
            "selectors": selector_list,
            "changed_files": normalized_list(changed_files),
            "require_evidence": bool(require_evidence),
        },
        outputs=outputs,
        warnings=warnings,
        errors=errors,
        provenance=[{"ref_type": "impact_map", "ref": "inline", "content_hash": hash_object(impact_map or {})}],
        artifacts=[{"path": repo_rel(repo_root, cache_path), "kind": "cache_entry"}],
    )
    store_cache(cache_path, envelope)
    return envelope


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a deterministic verification bundle")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--pis", required=True)
    parser.add_argument("--selector", action="append", default=[])
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--impact-map")
    parser.add_argument("--previous-evidence", action="append", default=[])
    parser.add_argument("--require-evidence", action="store_true")
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    impact_map = read_json(Path(args.impact_map)) if args.impact_map else None
    payload = build_verification_bundle(
        repo_root=Path(args.repo_root),
        target_pis_uid=args.pis,
        selectors=args.selector,
        changed_files=args.changed_file,
        impact_map=impact_map,
        previous_evidence=args.previous_evidence,
        require_evidence=bool(args.require_evidence),
    )
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
