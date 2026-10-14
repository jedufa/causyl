#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import subprocess
import sys
from pathlib import Path
from typing import List, Optional


def default_script_path() -> Path:
    return Path(__file__).with_name("pis-dataset-extractor.py")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Batch wrapper for pis-dataset-extractor.py")
    parser.add_argument("--items-root", default="governance/ledger/patch_instruction_sheets", help="Ledger items root.")
    parser.add_argument("--out-dir", default="infra/pipeline/knowledge_drafts/pis_datasets", help="Batch output directory.")
    parser.add_argument("--script", default="", help="Single extractor path. Defaults to sibling script.")
    parser.add_argument("--limit", type=int, default=0, help="Optional max number of items. 0 means all items.")
    parser.add_argument("--match", default="", help="Optional substring filter on item names.")
    parser.add_argument("--summary-csv", default="", help="Optional CSV summary output path.")
    parser.add_argument("--fail-fast", action="store_true", help="Stop on first failed extraction.")
    args = parser.parse_args(argv)

    items_root = Path(args.items_root)
    out_dir = Path(args.out_dir)
    script = Path(args.script) if args.script else default_script_path()
    summary_csv = Path(args.summary_csv) if args.summary_csv else out_dir / "batch_summary.csv"

    if not items_root.exists():
        print(f"ERROR: items root not found: {items_root}", file=sys.stderr)
        return 2
    if not script.exists():
        print(f"ERROR: single extractor not found: {script}", file=sys.stderr)
        return 2
    out_dir.mkdir(parents=True, exist_ok=True)

    item_dirs = sorted(p for p in items_root.iterdir() if p.is_dir() and (p / "pis.md").exists())
    if args.match:
        item_dirs = [p for p in item_dirs if args.match.lower() in p.name.lower()]
    if args.limit > 0:
        item_dirs = item_dirs[:args.limit]

    rows = []
    failures = 0
    for item in item_dirs:
        cmd = [sys.executable, str(script), "--pis", str(item), "--out-dir", str(out_dir)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        stdout_lines = [line.strip() for line in proc.stdout.splitlines() if line.strip()]
        rows.append({
            "item_dir": item.name,
            "status": "ok" if proc.returncode == 0 else "failed",
            "dataset_yaml": stdout_lines[0] if len(stdout_lines) >= 1 else "",
            "dataset_md": stdout_lines[1] if len(stdout_lines) >= 2 else "",
            "stderr": proc.stderr.strip(),
        })
        if proc.returncode != 0:
            failures += 1
            if args.fail_fast:
                break

    with summary_csv.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["item_dir", "status", "dataset_yaml", "dataset_md", "stderr"])
        writer.writeheader()
        writer.writerows(rows)

    print(str(summary_csv))
    print(f"processed={len(rows)} failures={failures} limit={args.limit or 'all'}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
