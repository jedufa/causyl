#!/usr/bin/env python3
"""Resolve legacy phase labels to canonical IDs and migration paths."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

DEFAULT_MAP_PATH = Path("docs/refactor_maps/phase_name_migration_map.json")


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _load_map(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _match_ids(query: str, aliases: list[dict[str, Any]]) -> list[str]:
    nq = _norm(query)
    out: list[str] = []
    for item in aliases:
        cid = str(item.get("canonical_id", "")).strip()
        terms = [str(t) for t in item.get("legacy_terms", [])]
        if _norm(cid) == nq:
            out.append(cid)
            continue
        for term in terms:
            nt = _norm(term)
            if nt and (nq == nt or nq in nt or nt in nq):
                out.append(cid)
                break
    return sorted(set(out))


def _status_rank(status: str) -> int:
    return {"active": 0, "legacy": 1, "planned": 2}.get(status, 9)


def main() -> int:
    p = argparse.ArgumentParser(description="Resolve legacy phase names to canonical IDs and paths")
    p.add_argument("query", help="Legacy phase label, canonical ID, or path fragment (e.g. phase5c, p1a2)")
    p.add_argument("--map", dest="map_path", default=str(DEFAULT_MAP_PATH), help="Path to phase map JSON")
    p.add_argument("--json", action="store_true", help="Emit JSON")
    p.add_argument(
        "--status",
        choices=["active", "legacy", "planned", "all"],
        default="all",
        help="Filter mappings by status",
    )
    args = p.parse_args()

    data = _load_map(Path(args.map_path))
    aliases = list(data.get("aliases", []))
    mappings = list(data.get("mappings", []))

    matched_ids = _match_ids(args.query, aliases)
    nq = _norm(args.query)

    filtered: list[dict[str, Any]] = []
    restrict_to_ids = bool(matched_ids)
    for m in mappings:
        status = str(m.get("computed_status") or m.get("status", "")).strip()
        if args.status != "all" and status != args.status:
            continue
        old_path = str(m.get("old_path", ""))
        new_path = str(m.get("new_path", ""))
        cid = str(m.get("canonical_id", ""))
        path_hit = nq in _norm(old_path) or nq in _norm(new_path)
        id_hit = cid in matched_ids
        if (restrict_to_ids and id_hit) or ((not restrict_to_ids) and (path_hit or id_hit)):
            filtered.append(m)

    filtered.sort(
        key=lambda m: (
            _status_rank(str(m.get("computed_status") or m.get("status", ""))),
            str(m.get("canonical_id", "")),
            str(m.get("new_path", "")),
        )
    )

    alias_rows = [a for a in aliases if str(a.get("canonical_id", "")) in matched_ids]

    result = {
        "query": args.query,
        "matched_canonical_ids": matched_ids,
        "aliases": alias_rows,
        "mappings": filtered,
    }

    if args.json:
        print(json.dumps(result, indent=2))
        return 0

    if not matched_ids and not filtered:
        print(f"No mapping found for query: {args.query}")
        return 1

    if matched_ids:
        print("Canonical IDs:")
        for row in alias_rows:
            print(f"- {row['canonical_id']}: {row['canonical_topic']}")

    if filtered:
        print("Mappings:")
        for m in filtered:
            status = m.get("computed_status") or m.get("status")
            print(f"- [{status}] {m['old_path']} -> {m['new_path']} ({m['canonical_id']})")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
