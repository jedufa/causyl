#!/usr/bin/env python3
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Iterable, List


DEFAULT_TARGETS = (
    "AGENTS.md",
    "README.md",
    "docs/10_operator/**/*.html",
    "governance/agents/README.md",
    "governance/agents/prompts/*.md",
    "infra/webui/*.py",
    "infra/webui/server_routes_and_main_parts/*.py",
    "infra/webui/static/*.html",
    "infra/webui/static/*.js",
    "infra/user/*",
)

EXCLUDED_SUBSTRINGS = (
    "/artifacts/",
    "governance/ledger/patch_instruction_sheets/",
    "runtime/state/",
)

ALLOWED_LINE_MARKERS = (
    "tools/controlplane",
    "control_pane",
    "technical code/module paths",
    "do not introduce new user-facing references",
    "historical text",
    "legacy term",
    "legacy wording",
)

PROHIBITED_PHRASES = (
    "control pane",
    "control plane",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _iter_target_files(repo_root: Path, patterns: Iterable[str]) -> List[Path]:
    out: List[Path] = []
    seen: set[Path] = set()
    for pattern in patterns:
        for path in repo_root.glob(pattern):
            if not path.is_file():
                continue
            if any(part in str(path).replace("\\", "/") for part in EXCLUDED_SUBSTRINGS):
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(resolved)
    return sorted(out)


def _line_allowed(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in ALLOWED_LINE_MARKERS)


def find_violations(repo_root: Path, patterns: Iterable[str]) -> List[dict]:
    violations: List[dict] = []
    for path in _iter_target_files(repo_root, patterns):
        rel = str(path.relative_to(repo_root)).replace("\\", "/")
        for line_no, raw in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
            lowered = raw.lower()
            if _line_allowed(lowered):
                continue
            hits = [phrase for phrase in PROHIBITED_PHRASES if phrase in lowered]
            if not hits:
                continue
            violations.append(
                {
                    "path": rel,
                    "line": line_no,
                    "phrases": hits,
                    "text": raw.strip(),
                }
            )
    return violations


def _parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Enforce operator-facing use of 'the plane' in repo policy/docs text.")
    ap.add_argument("--repo-root", default=str(_repo_root()))
    ap.add_argument("--pattern", action="append", default=[], help="Glob pattern to scan; repeatable")
    return ap


def main(argv: List[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    repo_root = Path(str(args.repo_root)).resolve()
    patterns = list(args.pattern or DEFAULT_TARGETS)
    violations = find_violations(repo_root, patterns)
    if violations:
        for row in violations:
            print(f"{row['path']}:{row['line']}: prohibited terminology {','.join(row['phrases'])}")
            print(f"  {row['text']}")
        return 1
    print("plane terminology OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
