#!/usr/bin/env python3
"""
tools.verify.verify_determinism_fingerprint

Phase-3 determinism fingerprint verifier with resume-window normalization.

Why this exists
---------------
In resume workflows (e.g. resume from bundle_gen000001), the resumed run's
CSV artifacts (metrics.csv, behaviors.csv) naturally begin at generation=1,
while a fresh baseline run often includes generation=0 as a prefix row.
If the fingerprint hashes entire files byte-for-byte, this "prefix-only"
difference causes a false FAIL even when the overlapping window is identical.

This verifier:
- Compares determinism_fingerprint.json between two run directories.
- If a mismatch is detected and a resume generation > 0 is inferred,
  it materializes normalized comparison CSVs containing only rows with
  generation >= resume_gen for BOTH runs, rewrites BOTH fingerprints to
  point to the normalized CSVs, and re-compares.

This keeps the *semantics* ("same from resume boundary onward") strict,
while preventing "extra gen0 row" from poisoning equivalence.

Contract notes
--------------
- No RNG calls.
- No timestamps or absolute paths are added to fingerprints.
- Changes are local to run directories produced by the harness.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import sys
import zipfile
import glob
from typing import Dict, Any, Optional, Tuple


# ------------------------------
# Small utilities
# ------------------------------

def _find_console_log(run_dir: str) -> str | None:
    """Best-effort: locate the single console_*.log file within a run directory."""
    try:
        for name in os.listdir(run_dir):
            if name.startswith("console_") and name.endswith(".log"):
                return os.path.join(run_dir, name)
    except FileNotFoundError:
        return None
    return None


def _iter_birth_internal_json(log_text: str, gen: int) -> list[dict]:
    """Extract BIRTH_INTERNAL_TRACE JSON objects for a given generation."""
    out: list[dict] = []
    for line in log_text.splitlines():
        if "[BIRTH_INTERNAL_TRACE]" not in line:
            continue
        m = re.search(r"\{.*\}", line)
        if not m:
            continue
        try:
            obj = json.loads(m.group(0))
        except Exception:
            continue
        if obj.get("gen") == gen:
            out.append(obj)
    return out


def _report_first_birth_divergence(run_a_dir: str, run_b_dir: str, resume_gen: int) -> str | None:
    """Return a short diagnostic string pointing to the first birth-trace divergence (if any)."""
    log_a_path = _find_console_log(run_a_dir)
    log_b_path = _find_console_log(run_b_dir)
    if not log_a_path or not log_b_path:
        return None

    try:
        log_a = open(log_a_path, "r", encoding="utf-8", errors="replace").read()
        log_b = open(log_b_path, "r", encoding="utf-8", errors="replace").read()
    except Exception:
        return None

    a_items = _iter_birth_internal_json(log_a, resume_gen)
    b_items = _iter_birth_internal_json(log_b, resume_gen)
    if not a_items or not b_items:
        return None

    # Focus on the most load-bearing early marker: mate adjacency list at tick 0.
    a_focus = [x for x in a_items if x.get("stage") == "mate_adj_pre" and x.get("tick") == 0]
    b_focus = [x for x in b_items if x.get("stage") == "mate_adj_pre" and x.get("tick") == 0]
    if not a_focus or not b_focus:
        return None

    for ia, (xa, xb) in enumerate(zip(a_focus, b_focus)):
        if xa.get("adj") != xb.get("adj") or xa.get("parent") != xb.get("parent"):
            return (
                "birth_trace_divergence: "
                f"gen={resume_gen} tick=0 stage=mate_adj_pre "
                f"at_index={ia} parentA={xa.get('parent')} parentB={xb.get('parent')} "
                f"adjA={xa.get('adj')} adjB={xb.get('adj')}"
            )

    # If the focused subset matches, attempt a broader first-mismatch scan.
    for i, (xa, xb) in enumerate(zip(a_items, b_items)):
        if xa != xb:
            return f"birth_trace_divergence: gen={resume_gen} first_json_mismatch_index={i}"

    return None

def _sha256_file(path: str) -> str:
    """Compute sha256 of a file in a streaming manner."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: str) -> Dict[str, Any]:
    """Load JSON from disk."""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path: str, obj: Dict[str, Any]) -> None:
    """Write JSON to disk with stable formatting."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _infer_resume_gen_from_path(s: str) -> Optional[int]:
    """
    Infer resume generation from a string that may contain 'bundle_gen000001'.
    Returns None if not found.
    """
    m = re.search(r"bundle_gen(\d{6})", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _infer_resume_gen(run_dir: str) -> Optional[int]:
    """
    Infer resume generation for a run directory.

    Sources (first hit wins):
    - run_config.csv: row 'resume_from', value contains bundle_genXXXXXX
    - manifest.json: cli_args.resume_from contains bundle_genXXXXXX

    Returns None if not inferable or if resume_from is empty.
    """
    run_cfg = os.path.join(run_dir, "run_config.csv")
    if os.path.isfile(run_cfg):
        try:
            with open(run_cfg, "r", encoding="utf-8") as f:
                # It's a 2-column CSV in many harnesses: key,value
                reader = csv.reader(f)
                for row in reader:
                    if not row:
                        continue
                    if row[0].strip() == "resume_from" and len(row) >= 2:
                        v = row[1].strip()
                        if v:
                            g = _infer_resume_gen_from_path(v)
                            if g is not None:
                                return g
        except Exception:
            pass

    manifest = os.path.join(run_dir, "manifest.json")
    if os.path.isfile(manifest):
        try:
            mj = _load_json(manifest)
            cli_args = mj.get("cli_args") or {}
            resume_from = cli_args.get("resume_from") or ""
            if isinstance(resume_from, str) and resume_from:
                g = _infer_resume_gen_from_path(resume_from)
                if g is not None:
                    return g
        except Exception:
            pass

    return None


def _normalize_csv_by_generation(src_csv: str, dst_csv: str, resume_gen: int) -> None:
    """
    Create a normalized CSV that includes:
    - The header row (always)
    - All data rows where int(row['generation']) >= resume_gen

    If the file has no 'generation' column, this is a no-op copy.
    """
    # Fail fast on missing input
    if not os.path.isfile(src_csv):
        raise FileNotFoundError(src_csv)

    with open(src_csv, "r", encoding="utf-8", newline="") as fin:
        # Sniff dialect conservatively; fallback to excel
        sample = fin.read(4096)
        fin.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample)
        except Exception:
            dialect = csv.excel

        reader = csv.reader(fin, dialect)
        try:
            header = next(reader)
        except StopIteration:
            # empty file: create empty output
            with open(dst_csv, "w", encoding="utf-8", newline="") as fout:
                pass
            return

        # Find generation column index (case sensitive to keep simple)
        gen_idx = None
        for i, col in enumerate(header):
            if col == "generation":
                gen_idx = i
                break

        with open(dst_csv, "w", encoding="utf-8", newline="") as fout:
            writer = csv.writer(fout, dialect)
            writer.writerow(header)

            if gen_idx is None:
                # No generation column: copy everything
                for row in reader:
                    writer.writerow(row)
                return

            for row in reader:
                if not row:
                    continue
                if gen_idx >= len(row):
                    continue
                try:
                    g = int(row[gen_idx])
                except Exception:
                    continue
                if g >= resume_gen:
                    writer.writerow(row)


def _rewrite_fingerprint_to_cmp_csv(run_dir: str, fp: Dict[str, Any], resume_gen: int) -> Tuple[Dict[str, Any], bool]:
    """
    For a run_dir fingerprint, materialize normalized CSVs and repoint artifacts:
      metrics.csv   -> metrics__cmp.csv
      behaviors.csv -> behaviors__cmp.csv

    Returns (updated_fp, changed_flag).
    """
    changed = False
    artifacts = fp.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        return fp, False

    # Only act if these artifacts exist
    for name, src in (("metrics.csv", "metrics.csv"), ("behaviors.csv", "behaviors.csv")):
        if name not in artifacts:
            continue
        src_path = os.path.join(run_dir, src)
        dst_name = name.replace(".csv", "__cmp.csv")
        dst_path = os.path.join(run_dir, dst_name)

        _normalize_csv_by_generation(src_path, dst_path, resume_gen)
        dst_sha = _sha256_file(dst_path)

        # Update artifact record
        rec = dict(artifacts[name])
        rec["path"] = dst_name
        rec["sha256"] = dst_sha
        artifacts[name] = rec
        changed = True

    if changed:
        fp["artifacts"] = artifacts
    return fp, changed


def _fingerprints_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    """Strict equality on the JSON objects (after stable formatting)."""
    return a == b




def _embed_e2e_logs_into_archive(archive_path: str) -> None:
    """
    Ensure _e2e_*.log files are embedded inside the given runs archive zip.

    This is a harness convenience: some runners emit logs next to the archive,
    but we require the archive to be self-contained for forensic upload.
    - No RNG calls.
    - No timestamps.
    - Embeds logs at the ZIP root as their basename (e.g. _e2e_fresh.log).
    """
    if not archive_path:
        return
    if not os.path.isfile(archive_path):
        return

    arc_dir = os.path.dirname(os.path.abspath(archive_path))
    candidates: list[str] = []
    for d in (arc_dir, os.getcwd()):
        try:
            candidates.extend(glob.glob(os.path.join(d, "_e2e_*.log")))
        except Exception:
            continue
    # De-dup deterministically
    candidates = sorted(set(os.path.abspath(p) for p in candidates))

    if not candidates:
        return

    try:
        with zipfile.ZipFile(archive_path, mode="a", compression=zipfile.ZIP_DEFLATED) as z:
            present = set(z.namelist())
            for p in candidates:
                name = os.path.basename(p)
                if name in present:
                    continue
                # Store at root of archive
                z.write(p, arcname=name)
    except Exception:
        # Never fail the determinism verdict due to log embedding issues
        return

# ------------------------------
# CLI
# ------------------------------

def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify determinism fingerprint equivalence between two run dirs.")
    p.add_argument("run_a", help="Run directory A (fresh)")
    p.add_argument("run_b", help="Run directory B (resume)")
    p.add_argument("--runs-archive", default="", help="Optional path to the final runs zip; if set, embed _e2e_*.log into it.")
    p.add_argument("--rewrite-on-normalize", action="store_true", default=True,
                   help="Rewrite determinism_fingerprint.json in-place after normalization (default: true).")
    p.add_argument("--no-rewrite-on-normalize", dest="rewrite_on_normalize", action="store_false",
                   help="Do not rewrite determinism_fingerprint.json; compare normalized in-memory only.")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    """
    Entry point.

    Exit codes:
    0 -> PASS
    2 -> FAIL (mismatch)
    3 -> ERROR (IO/format)
    """
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    run_a = os.path.abspath(args.run_a)
    run_b = os.path.abspath(args.run_b)

    fp_a_path = os.path.join(run_a, "determinism_fingerprint.json")
    fp_b_path = os.path.join(run_b, "determinism_fingerprint.json")

    try:
        fp_a = _load_json(fp_a_path)
        fp_b = _load_json(fp_b_path)
    except Exception as e:
        print(f"[FPRINT] ERROR: failed to load fingerprints: {e}", file=sys.stderr)
        return 3

    if _fingerprints_equal(fp_a, fp_b):
        print("[FPRINT] PASS: fingerprints identical")
        return 0

    # Infer resume generation from either run (prefer one that actually resumed)
    resume_gen = _infer_resume_gen(run_b)
    if resume_gen is None:
        resume_gen = _infer_resume_gen(run_a)

    if resume_gen is None or resume_gen <= 0:
        print("[FPRINT] FAIL: fingerprints differ (no resume_gen inference possible)")
        return 2

    # Normalize both and compare again
    try:
        fp_a2, ch_a = _rewrite_fingerprint_to_cmp_csv(run_a, fp_a, resume_gen)
        fp_b2, ch_b = _rewrite_fingerprint_to_cmp_csv(run_b, fp_b, resume_gen)
    except Exception as e:
        print(f"[FPRINT] ERROR: normalization failed: {e}", file=sys.stderr)
        return 3

    if args.rewrite_on_normalize and (ch_a or ch_b):
        try:
            _dump_json(fp_a_path, fp_a2)
            _dump_json(fp_b_path, fp_b2)
        except Exception as e:
            print(f"[FPRINT] ERROR: failed to rewrite fingerprints: {e}", file=sys.stderr)
            return 3

    if _fingerprints_equal(fp_a2, fp_b2):
        print(f"[FPRINT] PASS: fingerprints equivalent after normalization (generation >= {resume_gen})")
        return 0

    print(f"[FPRINT] FAIL: fingerprints still differ after normalization (generation >= {resume_gen})")
    diag = _report_first_birth_divergence(run_a, run_b, resume_gen)
    if diag:
        print(f"[FPRINT] DIAG: {diag}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
