"""Deterministic context compression and prompt supply engine.

Last modified: 2026-03-30 23:57 Europe/Zurich
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any, Mapping, Sequence

from tools.deterministic_tooling.common import (
    build_envelope,
    canonical_source,
    ensure_runtime_dir,
    estimate_tokens,
    file_excerpt,
    find_item_dir_for_uid,
    git_head,
    hash_object,
    iter_existing_paths,
    load_cache,
    normalized_list,
    repo_rel,
    store_cache,
    write_output,
)

SCHEMA_VERSION = "context_bundle_v1"
TOOL_NAME = "context_compression_supply_engine"
CACHE_TTL_S = 3600.0
DEFAULT_BUDGET_TOKENS = 12000
MAX_ITEMS = 12


def _cache_key(*, repo_root: Path, target_pis_uid: str, target_files: Sequence[str], must_include: Sequence[str], budget_tokens: int) -> str:
    return hash_object(
        {
            "git_head": git_head(repo_root),
            "target_pis_uid": str(target_pis_uid or "").strip(),
            "target_files": list(target_files or []),
            "must_include": list(must_include or []),
            "budget_tokens": int(budget_tokens),
        }
    ).split(":", 1)[1]


def _candidate_paths(repo_root: Path, *, target_pis_uid: str, target_files: Sequence[str], must_include: Sequence[str]) -> list[Path]:
    raw_paths: list[str | Path] = list(target_files or []) + list(must_include or [])
    if target_pis_uid:
        item_dir = find_item_dir_for_uid(repo_root, target_pis_uid)
        if item_dir is not None:
            raw_paths.extend(
                [
                    item_dir / "pis.md",
                    item_dir / "meta.json",
                ]
            )
    return iter_existing_paths(repo_root, raw_paths)


def build_context_bundle(
    *,
    repo_root: Path,
    target_pis_uid: str = "",
    target_files: Sequence[str] = (),
    budget_tokens: int = DEFAULT_BUDGET_TOKENS,
    must_include: Sequence[str] = (),
    previous_context_key: str = "",
) -> dict[str, Any]:
    repo_root = Path(repo_root).resolve()
    budget_tokens = max(32, int(budget_tokens or DEFAULT_BUDGET_TOKENS))
    key = _cache_key(
        repo_root=repo_root,
        target_pis_uid=target_pis_uid,
        target_files=target_files,
        must_include=must_include,
        budget_tokens=budget_tokens,
    )
    cache_dir = ensure_runtime_dir(repo_root, "context_cache")
    cache_path = cache_dir / f"{key}.json"
    cached = load_cache(cache_path, ttl_s=CACHE_TTL_S)
    if cached is not None:
        return cached

    warnings: list[dict[str, Any]] = []
    paths = _candidate_paths(repo_root, target_pis_uid=target_pis_uid, target_files=target_files, must_include=must_include)
    if not paths:
        warnings.append({"code": "no_context_candidates", "severity": "warning", "message": "No candidate context files were found."})
    items: list[dict[str, Any]] = []
    used_tokens = 0
    dropped: list[dict[str, Any]] = []
    provenance: list[dict[str, Any]] = []
    for path in paths:
        if len(items) >= MAX_ITEMS:
            dropped.append({"path": repo_rel(repo_root, path), "reason": "max_items_exceeded"})
            continue
        excerpt = file_excerpt(path)
        content = str(excerpt.get("content") or "")
        token_count = estimate_tokens(content)
        remaining = max(0, budget_tokens - used_tokens)
        if token_count > remaining and remaining > 0:
            content = content[: max(16, remaining * 4)]
            while estimate_tokens(content) > remaining and len(content) > 16:
                content = content[: max(16, len(content) - max(1, len(content) // 8))]
            excerpt["content"] = content
            excerpt["content_hash"] = hash_object({"path": str(path), "content": content})
            token_count = estimate_tokens(content)
        if token_count > remaining:
            dropped.append({"path": repo_rel(repo_root, path), "reason": "budget_exceeded"})
            continue
        used_tokens += token_count
        span = (int(excerpt.get("start_line") or 0), int(excerpt.get("end_line") or 0))
        items.append(
            {
                "path": repo_rel(repo_root, path),
                "span": [span[0], span[1]],
                "excerpt": content,
                "token_count": token_count,
                "content_hash": str(excerpt.get("content_hash") or ""),
            }
        )
        provenance.append(canonical_source(repo_root, path, line_span=span))

    outputs = {
        "context_key": key,
        "context_bundle": items,
        "budget_report": {
            "used_tokens": used_tokens,
            "remaining_tokens": max(0, budget_tokens - used_tokens),
            "budget_tokens": budget_tokens,
            "truncation_reason": dropped[0]["reason"] if dropped else "",
            "dropped_items": dropped,
        },
        "missing_refs": [row["path"] for row in dropped if row["reason"] == "budget_exceeded"],
        "previous_context_key": str(previous_context_key or "").strip(),
    }
    envelope = build_envelope(
        schema_version=SCHEMA_VERSION,
        tool_name=TOOL_NAME,
        inputs={
            "repo_root": str(repo_root),
            "target_pis_uid": str(target_pis_uid or "").strip(),
            "target_files": normalized_list(str(path) for path in target_files),
            "must_include": normalized_list(str(path) for path in must_include),
            "budget_tokens": budget_tokens,
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
    parser = argparse.ArgumentParser(description="Build a deterministic context bundle")
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--target-pis", default="")
    parser.add_argument("--files", nargs="*", default=[])
    parser.add_argument("--must-include", nargs="*", default=[])
    parser.add_argument("--budget", type=int, default=DEFAULT_BUDGET_TOKENS)
    parser.add_argument("--output")
    args = parser.parse_args(argv)

    payload = build_context_bundle(
        repo_root=Path(args.repo_root),
        target_pis_uid=args.target_pis,
        target_files=args.files,
        must_include=args.must_include,
        budget_tokens=int(args.budget),
    )
    write_output(Path(args.output).resolve() if args.output else None, payload)
    print(payload["output_hash"])
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
