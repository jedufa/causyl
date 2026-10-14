"""tools.scripts.gen_repo_context

Generate a single, high-signal markdown file (REPO_CONTEXT.md) that explains:
  - what each top-level folder is for
  - what the primary execution entrypoints are
  - where the important "contracts" live (schemas, reports)
  - where outputs and evidence are written

Scalable design (important):
  - The file has a MANUAL "Mission Brief" section at the top.
  - Everything below the AUTO-GENERATED marker is regenerated each time.
  - When generating, we preserve the manual section from the repo-root REPO_CONTEXT.md
    (if present), and refresh the rest.

Intended use:
  - Run automatically during repo snapshot/archiving
  - Bundle the generated REPO_CONTEXT.md inside the snapshot ZIP root

CLI:
  python -m tools.scripts.gen_repo_context --out /tmp/REPO_CONTEXT.md

Notes:
  - Deliberately lightweight: no external deps.
  - Uses simple heuristics + docstring sniffing (no heavy AST indexing).
"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# -----------------------------------------------------------------------------
# Markers that separate manual and generated sections
# -----------------------------------------------------------------------------
AUTO_MARKER_BEGIN = (
    "============================================================\n"
    "AUTO-GENERATED MAP BELOW (DO NOT HAND-EDIT)\n"
    "The generator is allowed to replace everything below this line.\n"
    "============================================================\n"
)

AUTO_MARKER_LINE = "AUTO-GENERATED MAP BELOW (DO NOT HAND-EDIT)"


# -----------------------------------------------------------------------------
# Mapping table: keep this human-curated. It is fast, stable, and explains intent.
# -----------------------------------------------------------------------------
TOP_LEVEL_PURPOSE: Dict[str, str] = {
    "engine": "Simulation engine: environment + agents + evolution loop (core runtime).",
    "scripts": "Runnable Python modules (experiments/runs/*) + small runner utilities.",
    "experiments": "Registry + harness: declarative experiment YAML + matrix execution.",
    "tools": "Orchestration tooling: Phase 1 wrapper, Phase 2 manifest runner, aggregation.",
    "docs/specs/schemas": "Machine-checkable contracts: job spec / manifest / reports / artifact manifests.",
    "docs": "Project documentation (user manual + API docs build config).",
}


def _read_first_line_of_docstring(py_path: Path) -> str:
    """Return the first non-empty line of a module docstring, if present.

    Args:
        py_path: Path to a Python source file.

    Returns:
        A short summary line, or "" if none found.
    """
    try:
        text = py_path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""

    stripped = text.lstrip()
    if not (stripped.startswith('"""') or stripped.startswith("'''")):
        return ""

    quote = '"""' if stripped.startswith('"""') else "'''"
    end = stripped.find(quote, len(quote))
    if end == -1:
        return ""

    body = stripped[len(quote) : end]
    for line in body.splitlines():
        s = line.strip()
        if s:
            return s
    return ""


def _rel(p: Path, root: Path) -> str:
    """Render a relative path using forward slashes."""
    return p.resolve().relative_to(root.resolve()).as_posix()


def _git_try(root: Path, args: List[str]) -> str:
    """Best-effort git command from repo root. Returns 'n/a' on failure."""
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=str(root),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
        return out or "n/a"
    except Exception:
        return "n/a"


def _pick_key_files(root: Path) -> List[Tuple[str, str]]:
    """Pick a short list of high-value files to surface in the context doc."""
    candidates: List[Tuple[str, str]] = []

    def add(path: str, why: str) -> None:
        p = root / path
        if p.exists():
            candidates.append((path, why))

    add("JOB_SPEC_v1.md", "Phase 0/1 contract: what a Job is and what must be produced.")
    add("noxfile.py", "Automation entrypoints (quick/nightly suites).")
    add("pyproject.toml", "Packaging metadata / tooling config.")
    add("requirements-dev.txt", "Dev dependencies.")
    add("engine/main.py", "Engine entrypoint (simulation run, canonical).")
    add("engine/main.py", "Engine entrypoint (legacy compatibility).")
    add("experiments/USAGE.md", "How to use registry + harness.")
    add("experiments/harness/run_experiments.py", "Harness runner: expands registry and executes runs.")
    add("tools/run_job.py", "Phase 1 wrapper: atomic job execution with forensic artifacts.")
    add("tools/run_manifest.py", "Phase 2 runner: execute a manifest of jobs with concurrency.")

    return candidates


def _scan_tree_stats(root: Path, limit_each: int = 10) -> Dict[str, Dict[str, object]]:
    """Collect small stats for top-level directories."""
    stats: Dict[str, Dict[str, object]] = {}

    for name in sorted(TOP_LEVEL_PURPOSE.keys()):
        d = root / name
        if not d.exists() or not d.is_dir():
            continue

        py_files = sorted(d.rglob("*.py"))
        md_files = sorted(d.rglob("*.md"))
        yaml_files = sorted(list(d.rglob("*.yml")) + list(d.rglob("*.yaml")))

        py_list: List[str] = []
        for p in py_files[:limit_each]:
            summary = _read_first_line_of_docstring(p)
            rel = _rel(p, root)
            if summary:
                py_list.append(f"- `{rel}`: {summary}")
            else:
                py_list.append(f"- `{rel}`")

        stats[name] = {
            "py_count": len(py_files),
            "md_count": len(md_files),
            "yaml_count": len(yaml_files),
            "py_list": py_list,
        }

    return stats


def _default_manual_brief(root: Path) -> str:
    """Return a default manual mission brief block with placeholders."""
    describe = _git_try(root, ["describe", "--tags", "--always", "--dirty"])
    head = _git_try(root, ["rev-parse", "--short=12", "HEAD"])
    return "\n".join(
        [
            "# Causyl Repo Context",
            "",
            "This file exists so a reviewer can orient in minutes, not hours.",
            "",
            "============================================================",
            "MISSION BRIEF (EDIT THIS SECTION MANUALLY)",
            "============================================================",
            "",
            "Snapshot identity",
            f"- Git describe: {describe}",
            f"- Commit (short): {head}",
            "- Baseline tag: baseline-___",
            "- Dirty state: clean / dirty",
            "- Date: YYYY-MM-DD",
            "",
            "Authoritative references",
            "- Plan: Implementation_Plan_v2.docx",
            "- Hard constraints: TEST DELIVERY CONTRACT.txt",
            "",
            "Current focus (what we are trying to prove right now)",
            "- Active phase: _______________________________",
            "- Primary question: ___________________________",
            "- Success signal: _____________________________",
            "- Failure signal: _____________________________",
            "",
            "Non-negotiables (things you must not change casually)",
            "- Selection snapshot timing",
            "- Reproduction ordering",
            "- Fitness evaluation phase boundaries",
            "- Registry naming conventions",
            "- Evidence mapping contracts (job_spec/manifest/report)",
            "",
            "Known baseline behavior (what evolution is currently “doing wrong”)",
            "- Early-life reproduction dominates selection",
            "- Late-life collapse tolerated",
            "- Metrics without structural consequence are inert",
            "",
            "Run expectations (rough)",
            "- Typical runtime: ~__ s/gen @ pop __, steps __",
            "- Typical outputs: runs/experiments + runtime/live/wrapper_jobs",
            "",
            AUTO_MARKER_BEGIN.rstrip("\n"),
            "",
        ]
    )


def _extract_manual_prefix(existing_text: str) -> Optional[str]:
    """Extract everything up to and including the auto marker block."""
    idx = existing_text.find(AUTO_MARKER_LINE)
    if idx == -1:
        return None

    lines = existing_text.splitlines(True)

    marker_i = None
    for i, ln in enumerate(lines):
        if AUTO_MARKER_LINE in ln:
            marker_i = i
            break
    if marker_i is None:
        return None

    # Include through the next closing ===== line after the marker (best effort)
    end_i = marker_i
    for j in range(marker_i, min(marker_i + 12, len(lines))):
        if lines[j].strip().startswith("=") and j > marker_i:
            end_i = j
            break

    prefix = "".join(lines[: end_i + 1]).rstrip() + "\n\n"
    return prefix


def _load_manual_prefix(root: Path) -> str:
    """Load manual prefix from repo-root REPO_CONTEXT.md, or return default."""
    existing_path = (root / "docs" / "repo" / "REPO_CONTEXT.md")
    if existing_path.exists():
        try:
            text = existing_path.read_text(encoding="utf-8", errors="replace")
            prefix = _extract_manual_prefix(text)
            if prefix:
                return prefix
        except Exception:
            pass
    return _default_manual_brief(root)


def render_auto_section(root: Path) -> str:
    """Render the auto-generated repository map markdown."""
    key_files = _pick_key_files(root)
    stats = _scan_tree_stats(root)

    lines: List[str] = []

    lines.append("## Primary entrypoints (the stuff you actually run)")
    lines.append("")
    lines.append("- Engine (direct): `python -m engine.main ...`")
    lines.append("- Run module: `python -m experiments.runs.<name> ...`")
    lines.append("- Harness (registry): `python -m experiments.harness.run_experiments --registry ...`")
    lines.append("- Phase 1 wrapper (one job): `python -m tools.run_job --module <module> -- ...`")
    lines.append("- Phase 2 manifest runner: `python -m tools.run_manifest --manifest <file.yaml>`")
    lines.append("")

    lines.append("## Top-level map")
    lines.append("")
    for name in sorted(TOP_LEVEL_PURPOSE.keys()):
        p = root / name
        if p.exists() and p.is_dir():
            lines.append(f"- `{name}/`: {TOP_LEVEL_PURPOSE[name]}")
    lines.append("")

    lines.append("## Key files to read first")
    lines.append("")
    for path, why in key_files:
        lines.append(f"- `{path}`: {why}")
    lines.append("")

    lines.append("## Folder stats (small, but useful)")
    lines.append("")
    for name in sorted(stats.keys()):
        s = stats[name]
        lines.append(f"### `{name}/`")
        lines.append(f"- Python files: {s['py_count']}")
        lines.append(f"- Markdown files: {s['md_count']}")
        lines.append(f"- YAML files: {s['yaml_count']}")
        if s["py_list"]:
            lines.append("- Sample Python files:")
            lines.extend(s["py_list"])  # type: ignore[arg-type]
        lines.append("")

    lines.append("## Evidence / outputs (where results land)")
    lines.append("")
    lines.append("Common patterns in this repo:")
    lines.append("- `runs/` and `runs/experiments/...`: simulation outputs (metrics.csv, reports, etc.)")
    lines.append("- `runtime/live/wrapper_jobs/...`: Phase-1 wrapper forensic folders (payload + logs + meta)")
    lines.append("- `experiments/registry/v1/*.yaml`: registry entries naming tests")
    lines.append("")

    lines.append("## If you are reviewing a snapshot ZIP")
    lines.append("")
    lines.append("Start here:")
    lines.append("1) `SNAPSHOT_MANIFEST.txt` (git identity + file_count + dirty status)")
    lines.append("2) This file (`REPO_CONTEXT.md`) (structure + entrypoints)")
    lines.append("3) `JOB_SPEC_v1.md` and `tools/run_job.py` (execution contract)")
    lines.append("")

    return "\n".join(lines)


def render_repo_context(root: Path) -> str:
    """Render full repository context markdown (manual prefix + auto section)."""
    prefix = _load_manual_prefix(root)
    auto = render_auto_section(root)
    return prefix + auto


def main(argv: Optional[List[str]] = None) -> int:
    """CLI entrypoint."""
    parser = argparse.ArgumentParser(description="Generate REPO_CONTEXT.md")
    parser.add_argument("--root", default=".", help="Repo root (default: .)")
    parser.add_argument("--out", default="docs/repo/REPO_CONTEXT.md", help="Output markdown path.")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    out_path = Path(args.out).resolve()

    content = render_repo_context(root)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")

    print(f"Wrote: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
