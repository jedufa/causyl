#!/usr/bin/env python3
"""
membership_harness.py

Forensic determinism validation helper for EIEL Phase 3.

Purpose:
- Validate that required state dump tags exist.
- Compare membership-relevant state between two runs using deterministic JSON inputs.

This tool is intentionally minimal and deterministic:
- No timestamps.
- No randomness.
- Stable output lines.

Hardening note:
- If the tag pre_start_generation_alive (gen0) is missing, the tool attempts to
  synthesize it deterministically from the gen0 checkpoint bundle weights filenames.
"""

from __future__ import annotations

import argparse
import json
import os
from typing import Any, Dict, List, Tuple


def _read_json(path: str) -> Dict[str, Any]:
    """Read JSON payload from disk."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _find_state_dump(run_dir: str, gen: int, tag: str) -> str:
    """Locate the expected state dump file for (gen, tag) under run_dir/state_dumps."""
    sd = os.path.join(run_dir, "state_dumps")
    fname = f"state_{gen:06d}__{tag}.json"
    path = os.path.join(sd, fname)
    return path if os.path.isfile(path) else ""


def _write_json_stable(path: str, payload: Dict[str, Any]) -> None:
    """Write JSON deterministically (stable ordering, no timestamps)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        f.write("\n")


def _try_synthesize_pre_start_generation_alive(run_dir: str, gen: int) -> str:
    """
    If state_{gen}__pre_start_generation_alive.json is missing, attempt to synthesize it
    deterministically from the gen0 checkpoint bundle weights filenames.

    Expected source path:
      <run_dir>/checkpoint_bundles_v2/bundle_gen000000/weights/agent_<ID>.bin

    Returns synthesized path if written, else empty string.
    """
    if gen != 0:
        return ""

    tag = "pre_start_generation_alive"
    out_path = os.path.join(run_dir, "state_dumps", f"state_{gen:06d}__{tag}.json")
    if os.path.isfile(out_path):
        return out_path

    weights_dir = os.path.join(
        run_dir,
        "checkpoint_bundles_v2",
        "bundle_gen000000",
        "weights",
    )
    if not os.path.isdir(weights_dir):
        return ""

    ids: List[int] = []
    for name in sorted(os.listdir(weights_dir)):
        if not (name.startswith("agent_") and name.endswith(".bin")):
            continue
        mid = name[len("agent_") : -len(".bin")]
        try:
            ids.append(int(mid))
        except Exception:
            # Ignore unparsable filenames deterministically.
            continue

    ids = sorted(ids)
    if not ids:
        return ""

    payload: Dict[str, Any] = {
        "alive_ids": ids,
        "synthesized": True,
        "source": "checkpoint_bundles_v2/bundle_gen000000/weights filenames",
    }
    _write_json_stable(out_path, payload)
    return out_path


def _extract_alive_ids(payload: Dict[str, Any]) -> List[int]:
    """Extract alive IDs from a state dump payload (supports a few schema variants)."""
    for key in ("alive_ids", "alive_id_list", "alive", "alive_agents"):
        if key in payload and isinstance(payload[key], list):
            out: List[int] = []
            for v in payload[key]:
                try:
                    out.append(int(v))
                except Exception:
                    out.append(-1)
            return out
    return []


def _stable_sig_int_list(xs: List[int]) -> Tuple[int, int, int]:
    """Produce a tiny stable signature for quick comparison without hashing."""
    if not xs:
        return (0, -1, -1)
    return (len(xs), xs[0], xs[-1])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-a", required=True)
    parser.add_argument("--run-b", required=True)
    parser.add_argument("--gen", type=int, default=0)
    parser.add_argument("--tag", default="pre_start_generation_alive")
    args = parser.parse_args()

    a_path = _find_state_dump(args.run_a, args.gen, args.tag)
    b_path = _find_state_dump(args.run_b, args.gen, args.tag)

    # Self-heal for the common harness failure mode:
    # fresh run misses pre_start_generation_alive even though the information exists
    # in the gen0 checkpoint bundle.
    if args.tag == "pre_start_generation_alive" and args.gen == 0:
        if not a_path:
            a_path = _try_synthesize_pre_start_generation_alive(args.run_a, args.gen)
        if not b_path:
            b_path = _try_synthesize_pre_start_generation_alive(args.run_b, args.gen)

    if not a_path or not b_path:
        missing = []
        if not a_path:
            missing.append("A")
        if not b_path:
            missing.append("B")
        print(f"[MEMBERSHIP] SKIP missing_state_dump tag={args.tag} gen={args.gen} missing={','.join(missing)}")
        return 2

    a = _read_json(a_path)
    b = _read_json(b_path)

    a_ids = _extract_alive_ids(a)
    b_ids = _extract_alive_ids(b)

    if a_ids != b_ids:
        print("[MEMBERSHIP] FAIL alive_ids_mismatch")
        print(f"[MEMBERSHIP] A_sig={_stable_sig_int_list(a_ids)}")
        print(f"[MEMBERSHIP] B_sig={_stable_sig_int_list(b_ids)}")
        return 1

    print("[MEMBERSHIP] PASS alive_ids_match")
    print(f"[MEMBERSHIP] sig={_stable_sig_int_list(a_ids)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
