#!/usr/bin/env python3
"""Generate phase name migration JSON from git rename history + plan config."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from datetime import date
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("docs/refactor_maps/phase_name_migration_plan.json")
DEFAULT_OUTPUT = Path("docs/refactor_maps/phase_name_migration_map.json")


def _run_git(cmd: list[str]) -> str:
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"git command failed: {' '.join(cmd)}\n{proc.stderr.strip()}")
    return proc.stdout


def _norm(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", value.lower())


def _status(old_path: str, new_path: str) -> tuple[str, bool, bool]:
    old_exists = Path(old_path).exists()
    new_exists = Path(new_path).exists()
    if new_exists and old_exists:
        return "legacy", old_exists, new_exists
    if new_exists and not old_exists:
        return "active", old_exists, new_exists
    return "planned", old_exists, new_exists


def _parse_rename_lines(raw: str) -> list[tuple[str, str]]:
    out: list[tuple[str, str]] = []
    for line in raw.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        status = parts[0].strip()
        if not status.startswith("R"):
            continue
        old_path, new_path = parts[1].strip(), parts[2].strip()
        if old_path and new_path:
            out.append((old_path, new_path))
    return out


def _is_phase_related(path_a: str, path_b: str, include_prefixes: list[str], phase_tokens: list[str]) -> bool:
    if not (any(path_a.startswith(p) for p in include_prefixes) or any(path_b.startswith(p) for p in include_prefixes)):
        return False
    hay = f"{path_a} {path_b}".lower()
    return any(tok.lower() in hay for tok in phase_tokens)


def _infer_canonical_id(path_a: str, path_b: str, id_rules: list[dict[str, Any]]) -> str:
    hay = f"{path_a} {path_b}".lower()
    for rule in id_rules:
        cid = str(rule.get("canonical_id", "")).strip()
        terms = [str(t).lower() for t in rule.get("match_any", [])]
        if cid and any(term in hay for term in terms):
            return cid
    return "UNMAPPED"


def _collect_git_renames(include_history: bool) -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []

    # staged + unstaged (current work)
    for cmd in (
        ["git", "diff", "--cached", "--name-status", "-M"],
        ["git", "diff", "--name-status", "-M"],
    ):
        pairs.extend(_parse_rename_lines(_run_git(cmd)))

    if include_history:
        # committed history
        pairs.extend(
            _parse_rename_lines(_run_git(["git", "log", "--name-status", "--diff-filter=R", "--pretty=format:"]))
        )

    # de-duplicate while preserving deterministic order
    uniq = sorted(set(pairs), key=lambda x: (x[0], x[1]))
    return uniq


def generate(config_path: Path, output_path: Path, include_history: bool) -> dict[str, Any]:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    aliases = list(cfg.get("aliases", []))
    id_rules = list(cfg.get("id_rules", []))
    planned = list(cfg.get("planned_mappings", []))
    filters = dict(cfg.get("path_filters", {}))
    include_prefixes = list(filters.get("include_prefixes", ["tests/", "docs/"]))
    phase_tokens = list(filters.get("phase_tokens", ["phase"]))

    git_pairs = _collect_git_renames(include_history=include_history)

    mappings: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()

    for old_path, new_path in git_pairs:
        if not _is_phase_related(old_path, new_path, include_prefixes, phase_tokens):
            continue
        key = (old_path, new_path)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        cid = _infer_canonical_id(old_path, new_path, id_rules)
        computed_status, old_exists, new_exists = _status(old_path, new_path)
        mappings.append(
            {
                "canonical_id": cid,
                "status": computed_status,
                "computed_status": computed_status,
                "old_path": old_path,
                "new_path": new_path,
                "old_exists": old_exists,
                "new_exists": new_exists,
                "source": "git_rename",
            }
        )

    for item in planned:
        old_path = str(item.get("old_path", "")).strip()
        new_path = str(item.get("new_path", "")).strip()
        cid = str(item.get("canonical_id", "")).strip() or _infer_canonical_id(old_path, new_path, id_rules)
        if not old_path or not new_path:
            continue
        key = (old_path, new_path)
        if key in seen_keys:
            continue
        seen_keys.add(key)
        computed_status, old_exists, new_exists = _status(old_path, new_path)
        mappings.append(
            {
                "canonical_id": cid,
                "status": computed_status,
                "computed_status": computed_status,
                "old_path": old_path,
                "new_path": new_path,
                "old_exists": old_exists,
                "new_exists": new_exists,
                "source": "plan",
            }
        )

    mappings.sort(
        key=lambda m: (
            {"active": 0, "legacy": 1, "planned": 2}.get(str(m.get("computed_status", "")), 9),
            str(m.get("canonical_id", "")),
            str(m.get("old_path", "")),
            str(m.get("new_path", "")),
        )
    )

    data = {
        "schema_version": "2.0",
        "last_updated": str(date.today()),
        "generated_by": "infra/bin/generate_phase_name_migration_map.py",
        "input_config": str(config_path),
        "include_history": bool(include_history),
        "status_model": {
            "active": "canonical new path exists and old path absent",
            "legacy": "old and new paths both exist",
            "planned": "canonical new path does not exist yet",
        },
        "aliases": aliases,
        "mappings": mappings,
    }

    output_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return data


def main() -> int:
    p = argparse.ArgumentParser(description="Generate phase migration map JSON")
    p.add_argument("--config", default=str(DEFAULT_CONFIG), help="Config JSON path")
    p.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    p.add_argument(
        "--working-tree-only",
        action="store_true",
        help="Use only staged+unstaged git renames (skip committed rename history)",
    )
    args = p.parse_args()

    data = generate(Path(args.config), Path(args.output), include_history=not bool(args.working_tree_only))

    total = len(data.get("mappings", []))
    counts: dict[str, int] = {"active": 0, "legacy": 0, "planned": 0}
    for m in data.get("mappings", []):
        s = str(m.get("computed_status", ""))
        if s in counts:
            counts[s] += 1

    print(
        f"wrote {args.output}: total={total} active={counts['active']} legacy={counts['legacy']} planned={counts['planned']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
