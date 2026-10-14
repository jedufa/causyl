#!/usr/bin/env python3
"""Print persistent Codex slot projection sizes and recent projection metrics."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, Iterable, List

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from infra.bin import persistent_codex_slot as _slot


def _repo_root() -> Path:
    return REPO_ROOT


def _slot_names(root: Path, selected: str) -> Iterable[str]:
    if str(selected or "").strip():
        return [str(selected).strip()]
    return sorted(path.name for path in root.glob("slot_*") if path.is_dir())


def _slot_summary(repo_root: Path, slot_id: str) -> Dict[str, object]:
    state_root = _slot._terminal_slot_state_root(repo_root)
    slot_dir = _slot._slot_dir(state_root, slot_id)
    codex_home = _slot._slot_codex_home(state_root, slot_id)
    inputs = _slot._projection_input_snapshot(codex_home)
    metrics = _slot._load_projection_metrics(repo_root, slot_id)
    slot_bytes = 0
    slot_file_count = 0
    if slot_dir.exists():
        for path in slot_dir.rglob("*"):
            if not path.is_file():
                continue
            slot_file_count += 1
            try:
                slot_bytes += int(path.stat().st_size)
            except Exception:
                continue
    return {
        "slot_id": slot_id,
        "slot_dir": str(slot_dir),
        "slot_bytes": int(slot_bytes),
        "slot_file_count": int(slot_file_count),
        "rollout_file_count": int(inputs.get("rollout_file_count") or 0),
        "rollout_bytes": int(inputs.get("rollout_bytes") or 0),
        "history_bytes": int(inputs.get("history_bytes") or 0),
        "last_projection_duration_s": float(metrics.get("last_projection_duration_s") or 0.0),
        "average_projection_duration_s": float(metrics.get("average_projection_duration_s") or 0.0),
        "last_flush_iteration_s": float(metrics.get("last_flush_iteration_s") or 0.0),
        "last_rollout_file_count": int(metrics.get("last_rollout_file_count") or 0),
        "last_rows_projected": int(metrics.get("last_rows_projected") or 0),
        "last_output_bytes": int(metrics.get("last_output_bytes") or 0),
        "executed_flush_cycles": int(metrics.get("executed_flush_cycles") or 0),
        "skipped_flush_cycles": int(metrics.get("skipped_flush_cycles") or 0),
        "last_inputs_changed": bool(metrics.get("last_inputs_changed") or False),
        "last_projection_utc": str(metrics.get("last_projection_utc") or ""),
        "projection_mode": str(metrics.get("projection_mode") or ""),
        "projection_format": str(metrics.get("projection_format") or ""),
    }


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Print persistent Codex slot projection metrics.")
    parser.add_argument("--slot", default="", help="Optional slot id such as slot_01")
    args = parser.parse_args(argv)
    repo_root = _repo_root()
    state_root = _slot._terminal_slot_state_root(repo_root)
    rows = [_slot_summary(repo_root, slot_id) for slot_id in _slot_names(state_root, str(args.slot or ""))]
    if not rows:
        print("no slot directories found")
        return 0
    for row in rows:
        print(f"{row['slot_id']}")
        print(f"  slot_dir: {row['slot_dir']}")
        print(f"  slot_size_bytes: {row['slot_bytes']}")
        print(f"  slot_file_count: {row['slot_file_count']}")
        print(f"  rollout_file_count: {row['rollout_file_count']}")
        print(f"  rollout_bytes: {row['rollout_bytes']}")
        print(f"  history_bytes: {row['history_bytes']}")
        print(f"  projection_mode: {row['projection_mode']}")
        print(f"  projection_format: {row['projection_format']}")
        print(f"  last_projection_utc: {row['last_projection_utc']}")
        print(f"  last_projection_duration_s: {row['last_projection_duration_s']}")
        print(f"  average_projection_duration_s: {row['average_projection_duration_s']}")
        print(f"  last_flush_iteration_s: {row['last_flush_iteration_s']}")
        print(f"  last_rollout_file_count: {row['last_rollout_file_count']}")
        print(f"  last_rows_projected: {row['last_rows_projected']}")
        print(f"  last_output_bytes: {row['last_output_bytes']}")
        print(f"  last_inputs_changed: {row['last_inputs_changed']}")
        print(f"  executed_flush_cycles: {row['executed_flush_cycles']}")
        print(f"  skipped_flush_cycles: {row['skipped_flush_cycles']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
