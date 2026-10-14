#!/usr/bin/env python3
"""Build a categorized inventory of engine references for safe rename planning."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

DEFAULT_OUTPUT = Path("docs/refactor_maps/evo_grid_to_engine_inventory.json")

CATEGORY_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("python_import", re.compile(r"\b(from|import)\s+engine(\.|\b)")),
    ("module_entrypoint", re.compile(r"(?:-m\s+engine\.main|['\"]engine\.main['\"])")),
    ("filesystem_path", re.compile(r"engine/")),
    ("path_probe", re.compile(r"[\"']engine[\"']|Path\(.*engine.*\)|/engine\b")),
]


def _run_rg() -> str:
    cmd = [
        "rg",
        "-n",
        "engine",
        ".",
        "--glob",
        "!.git/**",
        "--glob",
        "!**/__pycache__/**",
    ]
    proc = subprocess.run(cmd, check=False, capture_output=True, text=True)
    if proc.returncode not in (0, 1):
        raise RuntimeError(proc.stderr.strip() or "rg failed")
    return proc.stdout


def _categorize(line: str) -> str:
    for name, pat in CATEGORY_PATTERNS:
        if pat.search(line):
            return name
    return "other"


def build_inventory() -> dict[str, Any]:
    raw = _run_rg()
    hits: list[dict[str, Any]] = []
    per_file_counts: Counter[str] = Counter()
    per_category_counts: Counter[str] = Counter()
    per_file_categories: dict[str, set[str]] = defaultdict(set)

    for row in raw.splitlines():
        parts = row.split(":", 2)
        if len(parts) != 3:
            continue
        file_path, line_no_s, content = parts
        try:
            line_no = int(line_no_s)
        except ValueError:
            continue
        category = _categorize(content)
        hit = {
            "file": file_path,
            "line": line_no,
            "category": category,
            "content": content.strip(),
        }
        hits.append(hit)
        per_file_counts[file_path] += 1
        per_category_counts[category] += 1
        per_file_categories[file_path].add(category)

    top_files = [
        {
            "file": f,
            "count": c,
            "categories": sorted(per_file_categories[f]),
        }
        for f, c in per_file_counts.most_common(50)
    ]

    return {
        "schema_version": "1.0",
        "token": "engine",
        "summary": {
            "files_with_hits": len(per_file_counts),
            "total_hits": len(hits),
            "counts_by_category": dict(per_category_counts),
        },
        "top_files": top_files,
        "hits": hits,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate impact inventory for engine -> engine rename")
    ap.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Output JSON path")
    args = ap.parse_args()

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    data = build_inventory()
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")

    s = data["summary"]
    print(
        "wrote"
        f" {out_path}: files={s['files_with_hits']} hits={s['total_hits']}"
        f" categories={s['counts_by_category']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
