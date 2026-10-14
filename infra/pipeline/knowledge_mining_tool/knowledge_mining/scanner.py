from __future__ import annotations

import ast
import fnmatch
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .models import Evidence, ScanResult
from .utils import (
    classify_path_signal,
    detect_bucket_hits,
    is_high_signal_path_class,
    is_low_signal_path,
    is_noise_import_root,
    is_noise_term,
    iter_identifier_forms,
    iter_term_forms,
    relative_posix,
    repo_slug_from_path,
    should_skip_directory,
)

try:
    import pathspec  # type: ignore
except Exception:
    pathspec = None

DEFAULT_EXCLUDED_DIRS = {
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox", ".nox", ".idea", ".vscode", "node_modules", "venv", ".venv", "build", "dist",
}

@dataclass
class _FallbackGitIgnoreRule:
    pattern: str
    negate: bool
    directory_only: bool
    anchored: bool
    basename_only: bool


class _GitIgnoreMatcher:
    def __init__(self, repo_root: Path, logger: logging.Logger, gitignore_path: Path | None = None) -> None:
        self.repo_root = repo_root
        self.logger = logger
        self.gitignore_path = gitignore_path or (repo_root / ".gitignore")
        self._spec = None
        self._rules: list[_FallbackGitIgnoreRule] = []
        self.loaded = False
        if not self.gitignore_path.exists():
            return
        lines = self.gitignore_path.read_text(encoding="utf-8", errors="ignore").splitlines()
        self.loaded = True
        if pathspec is not None:
            self._spec = pathspec.PathSpec.from_lines("gitwildmatch", lines)
            return
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            negate = line.startswith("!")
            if negate:
                line = line[1:].strip()
                if not line:
                    continue
            directory_only = line.endswith("/")
            if directory_only:
                line = line[:-1]
            anchored = line.startswith("/")
            if anchored:
                line = line[1:]
            pattern = line.replace("\\", "/")
            if not pattern:
                continue
            basename_only = "/" not in pattern
            self._rules.append(_FallbackGitIgnoreRule(pattern=pattern, negate=negate, directory_only=directory_only, anchored=anchored, basename_only=basename_only))

    def match_file(self, relpath: str, is_dir: bool = False) -> bool:
        relpath = relpath.strip("/")
        if not relpath:
            return False
        if self._spec is not None:
            return bool(self._spec.match_file(relpath))
        matched = False
        path_parts = relpath.split("/")
        for rule in self._rules:
            if rule.directory_only and not is_dir:
                if not any(fnmatch.fnmatch(part, rule.pattern) for part in path_parts[:-1]):
                    continue
            if self._rule_matches(rule, relpath, path_parts, is_dir=is_dir):
                matched = not rule.negate
        return matched

    def _rule_matches(self, rule: _FallbackGitIgnoreRule, relpath: str, path_parts: list[str], *, is_dir: bool) -> bool:
        pattern = rule.pattern
        if rule.basename_only:
            return any(fnmatch.fnmatch(part, pattern) for part in path_parts)
        candidates = [relpath]
        if not rule.anchored:
            candidates.extend("/".join(path_parts[index:]) for index in range(1, len(path_parts)))
        for candidate in candidates:
            if fnmatch.fnmatch(candidate, pattern):
                return True
            if is_dir and fnmatch.fnmatch(f"{candidate}/", f"{pattern}/"):
                return True
            if not rule.anchored and fnmatch.fnmatch(candidate, f"*/{pattern}"):
                return True
        return False


class RepoScanner:
    def __init__(
        self,
        repo_root: Path,
        *,
        logger: logging.Logger,
        include_globs: list[str] | None = None,
        exclude_globs: list[str] | None = None,
        limit_files: int | None = None,
        max_py_parse_bytes: int = 2_000_000,
        progress_every: int = 500,
        max_evidence_paths: int = 8,
        respect_gitignore: bool = True,
        gitignore_path: Path | None = None,
    ) -> None:
        self.repo_root = repo_root
        self.logger = logger
        self.include_globs = include_globs or []
        self.exclude_globs = exclude_globs or []
        self.limit_files = limit_files
        self.max_py_parse_bytes = max_py_parse_bytes
        self.progress_every = progress_every
        self.max_evidence_paths = max_evidence_paths
        self.respect_gitignore = respect_gitignore
        self.gitignore_matcher = _GitIgnoreMatcher(repo_root, logger, gitignore_path=gitignore_path) if respect_gitignore else None
        self.gitignore_skipped = 0
        self.default_excluded_skipped = 0
        self.low_signal_seen = 0

    def scan(self) -> ScanResult:
        repo_root = self.repo_root.resolve()
        result = ScanResult(repo_root=repo_root, repo_slug=repo_slug_from_path(repo_root))
        file_paths = self._collect_files()
        result.total_files = len(file_paths)
        self.logger.info("Indexed %s files under %s", len(file_paths), repo_root)
        if self.respect_gitignore and self.gitignore_matcher and self.gitignore_matcher.loaded:
            self.logger.info("Skipped %s paths due to .gitignore", self.gitignore_skipped)
        if self.default_excluded_skipped:
            self.logger.info("Skipped %s paths due to default excludes", self.default_excluded_skipped)
        if not file_paths:
            return result

        for index, path in enumerate(file_paths, start=1):
            relpath = relative_posix(path, repo_root)
            path_class = classify_path_signal(relpath)
            self._record_file_metadata(result, path, relpath)
            if path_class == "low_signal":
                self.low_signal_seen += 1
            else:
                self._record_path_terms(result, path, relpath, path_class=path_class)
                self._record_bucket_hits(result, path, relpath, path_class=path_class)
                if path.suffix.lower() == ".py":
                    result.python_files += 1
                    try:
                        self._parse_python_file(result, path, relpath, path_class=path_class)
                    except Exception as exc:
                        result.parse_errors += 1
                        self.logger.debug("Failed to parse %s: %s", relpath, exc)
            if self.progress_every and index % self.progress_every == 0:
                self.logger.info("Scanned %s / %s files...", index, len(file_paths))
        if self.low_signal_seen:
            self.logger.info("De-prioritized %s low-signal files from term mining", self.low_signal_seen)
        return result

    def _collect_files(self) -> list[Path]:
        file_paths: list[Path] = []
        for current_root, dirs, files in self._walk(self.repo_root):
            current_root_path = Path(current_root)
            for filename in files:
                path = current_root_path / filename
                if not path.is_file():
                    continue
                relpath = relative_posix(path, self.repo_root)
                if self._should_skip_path(relpath, is_dir=False):
                    continue
                if self.include_globs and not any(fnmatch.fnmatch(relpath, pattern) for pattern in self.include_globs):
                    continue
                if self.exclude_globs and any(fnmatch.fnmatch(relpath, pattern) for pattern in self.exclude_globs):
                    continue
                file_paths.append(path)
                if self.limit_files and len(file_paths) >= self.limit_files:
                    return file_paths
        return file_paths

    def _walk(self, root: Path) -> Iterable[tuple[str, list[str], list[str]]]:
        for current_root, dirs, files in os.walk(root):
            current_root_path = Path(current_root)
            filtered_dirs: list[str] = []
            for dirname in dirs:
                if should_skip_directory(dirname) or dirname in DEFAULT_EXCLUDED_DIRS:
                    self.default_excluded_skipped += 1
                    continue
                dir_path = current_root_path / dirname
                relpath = relative_posix(dir_path, self.repo_root)
                if self._should_skip_path(relpath, is_dir=True):
                    continue
                filtered_dirs.append(dirname)
            dirs[:] = filtered_dirs
            yield current_root, dirs, files

    def _should_skip_path(self, relpath: str, *, is_dir: bool) -> bool:
        parts = Path(relpath).parts
        if any(part in DEFAULT_EXCLUDED_DIRS for part in parts):
            self.default_excluded_skipped += 1
            return True
        if self.respect_gitignore and self.gitignore_matcher and self.gitignore_matcher.match_file(relpath, is_dir=is_dir):
            self.gitignore_skipped += 1
            return True
        return False

    def _record_file_metadata(self, result: ScanResult, path: Path, relpath: str) -> None:
        extension = path.suffix.lower() or "<no_ext>"
        result.extensions[extension] += 1
        parts = Path(relpath).parts
        if parts:
            result.top_level_dirs[parts[0]] += 1

    def _record_path_terms(self, result: ScanResult, path: Path, relpath: str, *, path_class: str) -> None:
        parts = Path(relpath).parts
        for index, part in enumerate(parts):
            stem = Path(part).stem if index == len(parts) - 1 else part
            for form in iter_term_forms(stem):
                if is_noise_term(form):
                    continue
                self._add_evidence(result.term_evidence, form, relpath, source="path", path_class=path_class)
                if index < len(parts) - 1:
                    self._add_evidence(result.directory_token_evidence, form, relpath, source="directory", path_class=path_class)

    def _record_bucket_hits(self, result: ScanResult, path: Path, relpath: str, *, path_class: str) -> None:
        hits = detect_bucket_hits(Path(relpath).parts[:-1], path.name)
        for bucket in hits:
            self._add_evidence(result.bucket_evidence, bucket, relpath, source="bucket", path_class=path_class)

    def _parse_python_file(self, result: ScanResult, path: Path, relpath: str, *, path_class: str) -> None:
        try:
            if path.stat().st_size > self.max_py_parse_bytes:
                self.logger.debug("Skipping AST parse for large file %s", relpath)
                return
        except OSError:
            return
        text = path.read_text(encoding="utf-8", errors="ignore")
        module = ast.parse(text, filename=relpath)
        for node in ast.walk(module):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self._record_identifier(result, alias.name, relpath, source="import", path_class=path_class)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    self._record_identifier(result, node.module, relpath, source="import_from", path_class=path_class)
                for alias in node.names:
                    self._record_identifier(result, alias.name, relpath, source="import_name", path_class=path_class)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._record_identifier(result, node.name, relpath, source="function", path_class=path_class)
            elif isinstance(node, ast.ClassDef):
                self._record_identifier(result, node.name, relpath, source="class", path_class=path_class)
                for base in node.bases:
                    if isinstance(base, ast.Name):
                        self._record_identifier(result, base.id, relpath, source="base_class", path_class=path_class)
                    elif isinstance(base, ast.Attribute):
                        self._record_identifier(result, base.attr, relpath, source="base_class", path_class=path_class)

    def _record_identifier(self, result: ScanResult, identifier: str, relpath: str, *, source: str, path_class: str) -> None:
        root_token = identifier.split('.', 1)[0]
        if source in {"import", "import_from", "import_name"} and is_noise_import_root(root_token):
            return
        for form in iter_identifier_forms(identifier):
            if is_noise_term(form):
                continue
            self._add_evidence(result.term_evidence, form, relpath, source=source, path_class=path_class)

    def _add_evidence(self, store: dict[str, Evidence], term: str, relpath: str, *, source: str, path_class: str) -> None:
        evidence = store.setdefault(term, Evidence())
        evidence.add(relpath, source=source, path_class=path_class)
