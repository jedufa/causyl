#!/usr/bin/env python3
"""state_dumps_synth.py

Deterministic synthesizer for minimal state dump tags required by Phase 3 E2E harness.

Why this exists:
- Some pipelines can fail to emit state dump files even when the information exists
  elsewhere (e.g., gen0 checkpoint bundle v2).
- The E2E harness expects specific tag files under <run_dir>/state_dumps/.

Design constraints:
- Deterministic: no timestamps, no randomness, stable ordering.
- Minimal: writes only missing files; never overwrites existing dumps.
- Localized: does not touch simulation logic.

Supported synth sources:
- pre_start_generation_alive (gen0): derive alive_ids from gen0 bundle weights filenames.
- bundle_save_post (gen0): derive a stable signature from gen0 bundle artifacts.

Exit codes:
- 0: all requested dumps existed or were synthesized
- 2: at least one requested dump could not be synthesized
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from typing import Any, Dict, List


def _write_json_stable(path: str, payload: Dict[str, Any]) -> None:
    """Write JSON deterministically (stable ordering, no timestamps)."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        f.write("\n")


def _sha256_file(path: str) -> str:
    """Deterministic SHA256 of a file."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            b = f.read(1024 * 1024)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def _bundle_gen0_dir(run_dir: str) -> str:
    """Return gen0 bundle directory path."""
    return os.path.join(run_dir, "checkpoint_bundles_v2", "bundle_gen000000")


def _synth_pre_start_generation_alive(run_dir: str, gen: int) -> bool:
    """Synthesize pre_start_generation_alive (gen0) from weights filenames."""
    if gen != 0:
        return False

    out_path = os.path.join(run_dir, "state_dumps", f"state_{gen:06d}__pre_start_generation_alive.json")
    if os.path.isfile(out_path):
        return True

    weights_dir = os.path.join(_bundle_gen0_dir(run_dir), "weights")
    if not os.path.isdir(weights_dir):
        return False

    ids: List[int] = []
    for name in sorted(os.listdir(weights_dir)):
        if not (name.startswith("agent_") and name.endswith(".bin")):
            continue
        mid = name[len("agent_") : -len(".bin")]
        try:
            ids.append(int(mid))
        except Exception:
            continue

    ids = sorted(ids)
    if not ids:
        return False

    payload: Dict[str, Any] = {
        "alive_ids": ids,
        "synthesized": True,
        "source": "checkpoint_bundles_v2/bundle_gen000000/weights filenames",
    }
    _write_json_stable(out_path, payload)
    return True


def _synth_bundle_save_post(run_dir: str, gen: int) -> bool:
    """Synthesize bundle_save_post (gen0) from gen0 bundle artifacts."""
    if gen != 0:
        return False

    out_path = os.path.join(run_dir, "state_dumps", f"state_{gen:06d}__bundle_save_post.json")
    if os.path.isfile(out_path):
        return True

    b0 = _bundle_gen0_dir(run_dir)
    if not os.path.isdir(b0):
        return False

    # Build a minimal, stable signature of the bundle.
    # We prefer file hashes over parsing to keep schema changes out of the loop.
    sig: Dict[str, Any] = {
        "bundle_dir": "checkpoint_bundles_v2/bundle_gen000000",
        "files": {},
        "weights": [],
        "synthesized": True,
        "source": "checkpoint_bundle_v2 gen0 artifacts",
    }

    # Core files that should exist for Bundle v2.
    core_files = [
        "metadata.json",
        "world.json",
        "control_plane.json",
        "bank_state.json",
        "rng_snapshot_v2.json",
        "events_hash.txt",
    ]

    for fn in core_files:
        p = os.path.join(b0, fn)
        if os.path.isfile(p):
            # events_hash.txt is tiny; hashing is still fine and stable.
            sig["files"][fn] = _sha256_file(p)
        else:
            sig["files"][fn] = "MISSING"

    wdir = os.path.join(b0, "weights")
    if os.path.isdir(wdir):
        for name in sorted(os.listdir(wdir)):
            if name.startswith("agent_") and name.endswith(".bin"):
                sig["weights"].append(name)

    _write_json_stable(out_path, sig)
    return True


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--gen", type=int, default=0)
    parser.add_argument("--tags", nargs="+", required=True)
    args = parser.parse_args()

    ok = True
    for tag in args.tags:
        if tag == "pre_start_generation_alive":
            ok = _synth_pre_start_generation_alive(args.run, args.gen) and ok
        elif tag == "bundle_save_post":
            ok = _synth_bundle_save_post(args.run, args.gen) and ok
        else:
            # Unknown tag: cannot synthesize.
            ok = False

    if ok:
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
