#!/usr/bin/env python3
"""Deterministic verifier for full Phase E (E1 + E2)."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

GREEN = "\033[32m"
RED = "\033[31m"
RESET = "\033[0m"


@dataclass
class StageResult:
    stage: str
    command: str
    log_path: str
    rc: int
    elapsed_seconds: float

    @property
    def status(self) -> str:
        return "PASS" if int(self.rc) == 0 else "FAIL"


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_capture(*, cmd: str, log_path: Path, env: Optional[Dict[str, str]] = None) -> StageResult:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    t0 = datetime.now(timezone.utc).timestamp()
    with log_path.open("w", encoding="utf-8") as f:
        proc = subprocess.run(
            cmd,
            shell=True,
            stdout=f,
            stderr=subprocess.STDOUT,
            env=env,
            check=False,
            text=True,
            executable="/bin/bash",
        )
    t1 = datetime.now(timezone.utc).timestamp()
    return StageResult(
        stage=log_path.stem,
        command=cmd,
        log_path=str(log_path),
        rc=int(proc.returncode),
        elapsed_seconds=float(max(0.0, t1 - t0)),
    )


def _git_head() -> str:
    proc = subprocess.run(
        "git rev-parse HEAD",
        shell=True,
        text=True,
        capture_output=True,
        check=False,
        executable="/bin/bash",
    )
    if proc.returncode != 0:
        return "unknown"
    return proc.stdout.strip() or "unknown"


def _parse_run_paths_from_harness_log(log_path: Path) -> tuple[str, str]:
    text = log_path.read_text(encoding="utf-8", errors="replace")
    run_a: Optional[str] = None
    run_b: Optional[str] = None
    for ln in text.splitlines():
        if ln.startswith("[INFO] Run A: "):
            run_a = ln.split(": ", 1)[1].strip()
        if ln.startswith("[INFO] Run B("):
            run_b = ln.split(": ", 1)[1].strip()
    if not run_a or not run_b:
        raise RuntimeError("Could not parse Run A / Run B from replay harness log.")
    return run_a, run_b


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    out: List[Dict[str, Any]] = []
    for ln in path.read_text(encoding="utf-8", errors="replace").splitlines():
        s = ln.strip()
        if not s:
            continue
        out.append(json.loads(s))
    return out


def _post_resume_rows(path: Path, resume_gen: int) -> List[Dict[str, Any]]:
    return [r for r in _read_jsonl(path) if int(r.get("generation", -1)) > int(resume_gen)]


def _parity_check(*, run_a: Path, run_b: Path, resume_gen: int) -> Dict[str, Any]:
    # E1
    e1_latest_equal = _read_json(run_a / "phase_e_e1_latest.json") == _read_json(run_b / "phase_e_e1_latest.json")
    e1_metrics_equal = _post_resume_rows(run_a / "metrics" / "phase_e_e1_metrics.jsonl", resume_gen) == _post_resume_rows(
        run_b / "metrics" / "phase_e_e1_metrics.jsonl", resume_gen
    )
    e1_sentinel_equal = _post_resume_rows(run_a / "metrics" / "phase_e_e1_sentinel.jsonl", resume_gen) == _post_resume_rows(
        run_b / "metrics" / "phase_e_e1_sentinel.jsonl", resume_gen
    )

    # E2
    e2_latest_equal = _read_json(run_a / "phase_e_e2_latest.json") == _read_json(run_b / "phase_e_e2_latest.json")
    e2_taxonomy_equal = _post_resume_rows(run_a / "metrics" / "phase_e_e2_taxonomy.jsonl", resume_gen) == _post_resume_rows(
        run_b / "metrics" / "phase_e_e2_taxonomy.jsonl", resume_gen
    )
    e2_annotations_equal = _post_resume_rows(
        run_a / "metrics" / "phase_e_e2_annotations.jsonl", resume_gen
    ) == _post_resume_rows(run_b / "metrics" / "phase_e_e2_annotations.jsonl", resume_gen)

    ms = _read_json(run_a / "checkpoint_bundles_v2" / f"bundle_gen{int(resume_gen):06d}" / "metrics_state.json")
    has_e1_state = isinstance(ms, dict) and isinstance(ms.get("phase_e_e1_state"), dict)
    has_e2_state = isinstance(ms, dict) and isinstance(ms.get("phase_e_e2_state"), dict)

    return {
        "schema_version": "phase_e_replay_parity_v1",
        "resume_gen": int(resume_gen),
        "run_a": str(run_a),
        "run_b": str(run_b),
        "e1_latest_equal": bool(e1_latest_equal),
        "e1_post_resume_metrics_equal": bool(e1_metrics_equal),
        "e1_post_resume_sentinel_equal": bool(e1_sentinel_equal),
        "e2_latest_equal": bool(e2_latest_equal),
        "e2_post_resume_taxonomy_equal": bool(e2_taxonomy_equal),
        "e2_post_resume_annotations_equal": bool(e2_annotations_equal),
        "bundle_has_phase_e_e1_state": bool(has_e1_state),
        "bundle_has_phase_e_e2_state": bool(has_e2_state),
    }


def _harness_cmd(*, resume_gen: int) -> str:
    return (
        "PYTHONHASHSEED=0 EIEL_FORENSIC_V4=1 EIEL_POLICY_EXEC_PROBE=1 "
        "EIEL_DETERMINISM_STRICT=1 EIEL_STRICT_DETERMINISM=1 DEBUG_DETERMINISM=1 "
        "CLEAN_RUNS=1 ZIP_OUT_DIR=runtime/adhoc "
        "./tests/harnesses/phase3_resume/checkpoint_resume_determinism_e2e.sh "
        "--membership-trace --membership-keep 64 --membership-harness "
        f"--resume-gen {int(resume_gen)} --resume-gens {int(resume_gen)} -- "
        "--knob sim_cfg.steps_per_generation=8 "
        "--knob sim_cfg.phase_e_e1_enable=1 "
        "--knob sim_cfg.phase_e_e1_cadence_gen=1 "
        "--knob sim_cfg.phase_e_e1_knockout_every_gen=1 "
        "--knob sim_cfg.phase_e_e2_enable=1 "
        "--knob sim_cfg.phase_e_e2_cadence_gen=1"
    )


def _report_text(
    *,
    commit: str,
    artifacts_root: Path,
    stage_results: List[StageResult],
    parity: Dict[str, Any],
    verdict: str,
) -> str:
    command_lines: List[str] = []
    for idx, sr in enumerate(stage_results, start=1):
        command_lines.append(
            f"- {idx}. `{sr.command}`\n  - {sr.status}: `{sr.log_path}`"
        )
    parity_items = [
        ("E1 latest parity", parity.get("e1_latest_equal")),
        ("E1 post-resume metrics parity", parity.get("e1_post_resume_metrics_equal")),
        ("E1 post-resume sentinel parity", parity.get("e1_post_resume_sentinel_equal")),
        ("E2 latest parity", parity.get("e2_latest_equal")),
        ("E2 post-resume taxonomy parity", parity.get("e2_post_resume_taxonomy_equal")),
        ("E2 post-resume annotations parity", parity.get("e2_post_resume_annotations_equal")),
        ("Checkpoint has phase_e_e1_state", parity.get("bundle_has_phase_e_e1_state")),
        ("Checkpoint has phase_e_e2_state", parity.get("bundle_has_phase_e_e2_state")),
    ]
    parity_lines = [f"- {k}: {'PASS' if bool(v) else 'FAIL'}" for k, v in parity_items]

    return (
        "# EIEL Evolvability Phase E Verification Report\n\n"
        "1) Baseline + commit hash\n"
        "- Baseline reference: `baseline-256`\n"
        f"- Commit hash: `{commit}`\n"
        f"- Artifacts root: `{artifacts_root}`\n\n"
        "2) Files inspected\n"
        "- `engine/simulation/metrics_pkg/phase_e_metrics_engine.py`\n"
        "- `engine/simulation/runner_pkg/runner_loop.py`\n"
        "- `engine/simulation/runner_pkg/runner_config.py`\n"
        "- `engine/simulation/checkpoint_bundle_v2.py`\n"
        "- `governance/rng_budget_contract.json`\n"
        "- `governance/rng_budget_contract.lock.json`\n"
        "- `tests/evolvability/phase_e/test_e1_metrics_engine.py`\n"
        "- `tests/evolvability/phase_e/test_e2_failure_taxonomy.py`\n\n"
        "3) Determinism + replay parity matrix\n"
        + "\n".join(parity_lines)
        + "\n\n4) Command matrix (PASS/FAIL + artifact path)\n"
        + "\n".join(command_lines)
        + "\n\n5) Final verdict\n"
        f"- `{verdict}`\n"
    )


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Verify full Phase E (E1+E2 determinism and replay parity).")
    ap.add_argument("--resume-gen", type=int, default=10, help="Checkpoint generation for replay parity (default: 10).")
    ap.add_argument(
        "--artifacts-dir",
        default=f"/tmp/phase_e_verification_{_utc_stamp()}",
        help="Directory for command logs/artifacts (default: /tmp/phase_e_verification_<utc>).",
    )
    ap.add_argument(
        "--report-path",
        default="docs/reports/EIEL_Evolvability_PhaseE_Verification_Report.md",
        help="Output markdown report path.",
    )
    ap.add_argument(
        "--summary-path",
        default="runtime/logs/validation/phase_e/phase_e_verification_summary.json",
        help="Output summary JSON path.",
    )
    ap.add_argument("--skip-full-pytest", action="store_true", help="Skip final `pytest -q` stage.")
    return ap


def main() -> int:
    args = _parser().parse_args()

    artifacts_dir = Path(args.artifacts_dir).resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    commit = _git_head()

    commands: List[tuple[str, str]] = [
        (
            "cmd01_py_compile",
            "python -m py_compile "
            "engine/simulation/metrics_pkg/phase_e_metrics_engine.py "
            "engine/simulation/runner_pkg/runner_loop.py "
            "engine/simulation/runner_pkg/runner_config.py "
            "engine/simulation/checkpoint_bundle_v2.py "
            "tests/evolvability/phase_e/test_e1_metrics_engine.py "
            "tests/evolvability/phase_e/test_e2_failure_taxonomy.py",
        ),
        ("cmd02_static_rng_scan", "python -m tools.diagnostics.static_rng_scan --repo_root . --json"),
        ("cmd03_verify_p0a", "tools/diagnostics/verify_p0a_determinism_gate.sh --with-tests"),
        ("cmd04_pytest_e1_file", "pytest -q tests/evolvability/phase_e/test_e1_metrics_engine.py"),
        ("cmd05_pytest_e2_file", "pytest -q tests/evolvability/phase_e/test_e2_failure_taxonomy.py"),
        ("cmd06_pytest_phase_e", "pytest -q tests/evolvability/phase_e"),
        (
            "cmd07_pytest_phasee_plus_regressions",
            "pytest -q tests/evolvability/phase_e "
            "tests/evolvability/phase_d/test_d1_developmental_program.py "
            "tests/evolvability/phase_c/test_phase_c_integration_validation.py "
            "tests/evolvability/phase_b/test_b1_modular_genome_canonicalization.py "
            "tests/integration/metrics/test_epi_engine.py",
        ),
    ]
    if not bool(args.skip_full_pytest):
        commands.append(("cmd08_pytest_full", "pytest -q"))
    commands.append(("cmd09_replay_harness", _harness_cmd(resume_gen=int(args.resume_gen))))

    stage_results: List[StageResult] = []
    for stage_name, cmd in commands:
        log_path = artifacts_dir / f"{stage_name}.log"
        print(f"[verify_phase_e] running {stage_name} ...", flush=True)
        sr = _run_capture(cmd=cmd, log_path=log_path)
        stage_results.append(sr)
        if sr.rc == 0:
            print(f"[verify_phase_e] {stage_name}: {GREEN}PASS{RESET} (log: {log_path})", flush=True)
        else:
            print(f"[verify_phase_e] {stage_name}: {RED}FAIL{RESET} (log: {log_path})", file=sys.stderr, flush=True)

    parity: Dict[str, Any] = {
        "schema_version": "phase_e_replay_parity_v1",
        "error": "parity_not_run",
    }
    replay_stage = next((s for s in stage_results if s.stage == "cmd09_replay_harness"), None)
    if replay_stage is not None and replay_stage.rc == 0:
        run_a_s, run_b_s = _parse_run_paths_from_harness_log(Path(replay_stage.log_path))
        parity = _parity_check(run_a=Path(run_a_s), run_b=Path(run_b_s), resume_gen=int(args.resume_gen))
        parity_path = artifacts_dir / "cmd10_phase_e_replay_parity.json"
        parity_path.write_text(json.dumps(parity, sort_keys=True, indent=2) + "\n", encoding="utf-8")
        print(f"[verify_phase_e] cmd10_phase_e_replay_parity: {GREEN}PASS{RESET} (log: {parity_path})", flush=True)
    else:
        print(
            "[verify_phase_e] cmd10_phase_e_replay_parity: "
            f"{RED}FAIL{RESET} (replay harness missing or failed)",
            file=sys.stderr,
            flush=True,
        )

    command_pass = all(int(s.rc) == 0 for s in stage_results) and len(stage_results) == len(commands)
    parity_pass = all(
        bool(parity.get(k, False))
        for k in (
            "e1_latest_equal",
            "e1_post_resume_metrics_equal",
            "e1_post_resume_sentinel_equal",
            "e2_latest_equal",
            "e2_post_resume_taxonomy_equal",
            "e2_post_resume_annotations_equal",
            "bundle_has_phase_e_e1_state",
            "bundle_has_phase_e_e2_state",
        )
    )
    verdict = "VALIDATED" if (command_pass and parity_pass) else "NOT VALIDATED"

    summary: Dict[str, Any] = {
        "schema_version": "phase_e_verification_summary_v1",
        "utc": _utc_now(),
        "commit": commit,
        "baseline_reference": "baseline-256",
        "artifacts_root": str(artifacts_dir),
        "resume_gen": int(args.resume_gen),
        "command_matrix": [
            {
                "stage": s.stage,
                "status": s.status,
                "rc": int(s.rc),
                "log_path": s.log_path,
                "command": s.command,
                "elapsed_seconds": s.elapsed_seconds,
            }
            for s in stage_results
        ],
        "parity": parity,
        "command_pass": bool(command_pass),
        "parity_pass": bool(parity_pass),
        "final_verdict": verdict,
    }

    summary_path = Path(args.summary_path)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(json.dumps(summary, sort_keys=True, indent=2) + "\n", encoding="utf-8")

    report_path = Path(args.report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        _report_text(
            commit=commit,
            artifacts_root=artifacts_dir,
            stage_results=stage_results,
            parity=parity,
            verdict=verdict,
        ),
        encoding="utf-8",
    )

    print(json.dumps({"ok": verdict == "VALIDATED", "verdict": verdict, "summary_path": str(summary_path)}, sort_keys=True))
    return 0 if verdict == "VALIDATED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
