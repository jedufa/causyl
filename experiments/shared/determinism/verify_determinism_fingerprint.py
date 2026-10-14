"""Determinism fingerprint verification shared across experiment projects."""

from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import re
import sys
import zipfile
from typing import Any, Dict, Optional, Tuple


def _find_console_log(run_dir: str) -> str | None:
    try:
        for name in os.listdir(run_dir):
            if name.startswith("console_") and name.endswith(".log"):
                return os.path.join(run_dir, name)
    except FileNotFoundError:
        return None
    return None


def _iter_birth_internal_json(log_text: str, gen: int) -> list[dict]:
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
    for i, (xa, xb) in enumerate(zip(a_items, b_items)):
        if xa != xb:
            return f"birth_trace_divergence: gen={resume_gen} first_json_mismatch_index={i}"
    return None


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _load_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _dump_json(path: str, obj: Dict[str, Any]) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def _infer_resume_gen_from_path(s: str) -> Optional[int]:
    m = re.search(r"bundle_gen(\d{6})", s)
    if not m:
        return None
    try:
        return int(m.group(1))
    except ValueError:
        return None


def _infer_resume_gen(run_dir: str) -> Optional[int]:
    run_cfg = os.path.join(run_dir, "run_config.csv")
    if os.path.isfile(run_cfg):
        try:
            with open(run_cfg, "r", encoding="utf-8") as f:
                reader = csv.reader(f)
                for row in reader:
                    if row and row[0].strip() == "resume_from" and len(row) >= 2:
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
    if not os.path.isfile(src_csv):
        raise FileNotFoundError(src_csv)
    with open(src_csv, "r", encoding="utf-8", newline="") as fin:
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
            with open(dst_csv, "w", encoding="utf-8", newline="") as fout:
                pass
            return
        gen_idx = next((i for i, col in enumerate(header) if col == "generation"), None)
        with open(dst_csv, "w", encoding="utf-8", newline="") as fout:
            writer = csv.writer(fout, dialect)
            writer.writerow(header)
            if gen_idx is None:
                for row in reader:
                    writer.writerow(row)
                return
            for row in reader:
                if not row or gen_idx >= len(row):
                    continue
                try:
                    g = int(row[gen_idx])
                except Exception:
                    continue
                if g >= resume_gen:
                    writer.writerow(row)


def _rewrite_fingerprint_to_cmp_csv(run_dir: str, fp: Dict[str, Any], resume_gen: int) -> Tuple[Dict[str, Any], bool]:
    changed = False
    artifacts = fp.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        return fp, False
    for name, src in (("metrics.csv", "metrics.csv"), ("behaviors.csv", "behaviors.csv")):
        if name not in artifacts:
            continue
        src_path = os.path.join(run_dir, src)
        dst_name = name.replace(".csv", "__cmp.csv")
        dst_path = os.path.join(run_dir, dst_name)
        _normalize_csv_by_generation(src_path, dst_path, resume_gen)
        rec = dict(artifacts[name])
        rec["path"] = dst_name
        rec["sha256"] = _sha256_file(dst_path)
        artifacts[name] = rec
        changed = True
    if changed:
        fp["artifacts"] = artifacts
    return fp, changed


def _fingerprints_equal(a: Dict[str, Any], b: Dict[str, Any]) -> bool:
    return a == b


def _embed_e2e_logs_into_archive(archive_path: str) -> None:
    if not archive_path or not os.path.isfile(archive_path):
        return
    arc_dir = os.path.dirname(os.path.abspath(archive_path))
    candidates: list[str] = []
    for d in (arc_dir, os.getcwd()):
        try:
            candidates.extend(glob.glob(os.path.join(d, "_e2e_*.log")))
        except Exception:
            continue
    candidates = sorted(set(os.path.abspath(p) for p in candidates))
    if not candidates:
        return
    try:
        with zipfile.ZipFile(archive_path, mode="a", compression=zipfile.ZIP_DEFLATED) as z:
            present = set(z.namelist())
            for p in candidates:
                name = os.path.basename(p)
                if name not in present:
                    z.write(p, arcname=name)
    except Exception:
        return


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify determinism fingerprint equivalence between two run dirs.")
    p.add_argument("run_a")
    p.add_argument("run_b")
    p.add_argument("--runs-archive", default="")
    p.add_argument("--rewrite-on-normalize", action="store_true", default=True)
    p.add_argument("--no-rewrite-on-normalize", dest="rewrite_on_normalize", action="store_false")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
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
    resume_gen = _infer_resume_gen(run_b)
    if resume_gen is None:
        resume_gen = _infer_resume_gen(run_a)
    if resume_gen is None or resume_gen <= 0:
        print("[FPRINT] FAIL: fingerprints differ (no resume_gen inference possible)")
        return 2
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
        _embed_e2e_logs_into_archive(str(args.runs_archive or ""))
        return 0
    print(f"[FPRINT] FAIL: fingerprints still differ after normalization (generation >= {resume_gen})")
    diag = _report_first_birth_divergence(run_a, run_b, resume_gen)
    if diag:
        print(f"[FPRINT] DIAG: {diag}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
