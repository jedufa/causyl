from __future__ import annotations

import ast
from pathlib import Path
from typing import Iterable, Sequence

from .contracts import CodeMetrics


def _iter_python_files(repo_root: Path, code_paths: Sequence[str]) -> Iterable[Path]:
    seen: set[Path] = set()
    for raw in code_paths:
        candidate = (repo_root / str(raw)).resolve()
        if candidate.is_file() and candidate.suffix == ".py":
            if candidate not in seen:
                seen.add(candidate)
                yield candidate
            continue
        if candidate.is_dir():
            for path in sorted(candidate.rglob("*.py")):
                resolved = path.resolve()
                if resolved not in seen:
                    seen.add(resolved)
                    yield resolved


def measure_code_metrics(repo_root: Path, code_paths: Sequence[str]) -> CodeMetrics:
    file_count = 0
    nonempty_line_count = 0
    branch_count = 0
    function_count = 0
    for path in _iter_python_files(repo_root, code_paths):
        file_count += 1
        raw = path.read_text(encoding="utf-8")
        nonempty_line_count += sum(1 for line in raw.splitlines() if line.strip())
        tree = ast.parse(raw, filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                function_count += 1
            elif isinstance(node, (ast.If, ast.For, ast.AsyncFor, ast.While, ast.Try, ast.IfExp, ast.Match)):
                branch_count += 1
    return CodeMetrics(
        file_count=file_count,
        nonempty_line_count=nonempty_line_count,
        branch_count=branch_count,
        function_count=function_count,
    )
