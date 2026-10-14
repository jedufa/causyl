from __future__ import annotations

import ast
import dataclasses
import re
import shlex
from typing import List, Optional

from .core import PipelineError


@dataclasses.dataclass(frozen=True)
class ParsedDiffFile:
    old_path: Optional[str]
    new_path: Optional[str]
    has_hunk: bool

    @property
    def is_new_file(self) -> bool:
        return self.old_path == "/dev/null" and bool(self.new_path and self.new_path != "/dev/null")

    @property
    def is_deleted_file(self) -> bool:
        return self.new_path == "/dev/null" and bool(self.old_path and self.old_path != "/dev/null")

    @property
    def target_path(self) -> str:
        if self.new_path and self.new_path != "/dev/null":
            return str(self.new_path)
        if self.old_path and self.old_path != "/dev/null":
            return str(self.old_path)
        raise PipelineError(
            "malformed_diff",
            "Diff section does not declare a target path",
            details={"old_path": self.old_path, "new_path": self.new_path},
            exit_code=11,
        )


@dataclasses.dataclass
class _MutableDiffFile:
    old_path: Optional[str] = None
    new_path: Optional[str] = None
    has_hunk: bool = False

    def freeze(self) -> ParsedDiffFile:
        return ParsedDiffFile(old_path=self.old_path, new_path=self.new_path, has_hunk=self.has_hunk)


SUPPORTED_EMPTY_LINES = {
    "new file mode",
    "deleted file mode",
    "index ",
    "old mode ",
    "new mode ",
    "similarity index ",
    "rename from ",
    "rename to ",
    "copy from ",
    "copy to ",
}

HUNK_HEADER_RE = re.compile(r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? \+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@")


def normalize_diff_text(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        raise PipelineError("empty_diff", "Patch artifact content must not be empty", exit_code=11)
    if "\x00" in raw:
        raise PipelineError("unsafe_diff", "Patch artifact contains NUL bytes", exit_code=11)
    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
    if normalized.startswith("\ufeff"):
        normalized = normalized.lstrip("\ufeff")
    if not normalized.endswith("\n"):
        normalized += "\n"
    return normalized


def _decode_quoted_path(token: str) -> str:
    text = str(token or "").strip()
    if text.startswith('"') and text.endswith('"') and len(text) >= 2:
        try:
            value = ast.literal_eval(text)
        except Exception:
            value = text[1:-1]
        return str(value)
    return text


def _strip_header_timestamp(token: str) -> str:
    text = str(token or "").strip()
    if "\t" in text:
        text = text.split("\t", 1)[0].strip()
    return text


def _normalize_patch_path_token(token: str) -> Optional[str]:
    text = _strip_header_timestamp(_decode_quoted_path(token))
    if not text:
        return None
    if text in {"/dev/null", "a/dev/null", "b/dev/null"}:
        return "/dev/null"
    if text.startswith("a/"):
        return text[2:]
    if text.startswith("b/"):
        return text[2:]
    return text


def _start_new_section(sections: List[ParsedDiffFile], current: Optional[_MutableDiffFile]) -> _MutableDiffFile:
    if current is not None:
        sections.append(current.freeze())
    return _MutableDiffFile()


def parse_unified_diff(text: str) -> List[ParsedDiffFile]:
    normalized = normalize_diff_text(text)
    if "GIT binary patch" in normalized or "\nBinary files " in normalized:
        raise PipelineError("unsupported_diff", "Binary patches are not supported in execution MVP", exit_code=11)

    lines = normalized.splitlines()
    sections: List[ParsedDiffFile] = []
    current: Optional[_MutableDiffFile] = None
    saw_hunk_anywhere = False

    for line in lines:
        if line.startswith("diff --git "):
            remainder = line[len("diff --git ") :].strip()
            try:
                parts = shlex.split(remainder)
            except Exception as exc:
                raise PipelineError(
                    "malformed_diff",
                    "Unable to parse diff --git header",
                    details={"line": line},
                    exit_code=11,
                ) from exc
            if len(parts) < 2:
                raise PipelineError(
                    "malformed_diff",
                    "diff --git header must contain old and new path",
                    details={"line": line},
                    exit_code=11,
                )
            current = _start_new_section(sections, current)
            current.old_path = _normalize_patch_path_token(parts[0])
            current.new_path = _normalize_patch_path_token(parts[1])
            continue

        if line.startswith("--- "):
            if current is None:
                current = _MutableDiffFile()
            elif current.has_hunk:
                current = _start_new_section(sections, current)
            current.old_path = _normalize_patch_path_token(line[4:])
            continue

        if line.startswith("+++ "):
            if current is None:
                current = _MutableDiffFile()
            current.new_path = _normalize_patch_path_token(line[4:])
            continue

        if line.startswith("@@"):
            if current is None:
                raise PipelineError(
                    "malformed_diff",
                    "Diff hunk encountered before file header",
                    details={"line": line},
                    exit_code=11,
                )
            current.has_hunk = True
            saw_hunk_anywhere = True
            continue

        if line.startswith("@@@ "):
            raise PipelineError("unsupported_diff", "Combined merge diffs are not supported", details={"line": line}, exit_code=11)

    if current is not None:
        sections.append(current.freeze())

    if not sections:
        raise PipelineError("malformed_diff", "Patch artifact is not a recognizable unified diff", exit_code=11)
    if not saw_hunk_anywhere:
        raise PipelineError("malformed_diff", "Unified diff must contain at least one hunk", exit_code=11)

    validated_sections: List[ParsedDiffFile] = []
    for section in sections:
        if not section.old_path and not section.new_path:
            raise PipelineError("malformed_diff", "Diff section is missing both old and new paths", exit_code=11)
        if not section.has_hunk:
            raise PipelineError(
                "malformed_diff",
                "Each diff section must contain at least one hunk",
                details={"old_path": section.old_path, "new_path": section.new_path},
                exit_code=11,
            )
        validated_sections.append(section)
    return validated_sections


def extract_target_files(text: str) -> List[str]:
    out: List[str] = []
    seen = set()
    for section in parse_unified_diff(text):
        target = section.target_path
        if target not in seen:
            seen.add(target)
            out.append(target)
    if not out:
        raise PipelineError("malformed_diff", "Unified diff does not reference any target files", exit_code=11)
    return out


def count_changed_lines(text: str) -> dict:
    normalized = normalize_diff_text(text)
    insertions = 0
    deletions = 0
    in_hunk = False
    for line in normalized.splitlines():
        if line.startswith("diff --git "):
            in_hunk = False
            continue
        if line.startswith("@@ "):
            in_hunk = True
            continue
        if not in_hunk:
            continue
        if line.startswith("+") and not line.startswith("+++"):
            insertions += 1
        elif line.startswith("-") and not line.startswith("---"):
            deletions += 1
    return {"insertions": insertions, "deletions": deletions}


def repair_unified_diff_hunk_headers(text: str) -> str:
    normalized = normalize_diff_text(text)
    lines = normalized.splitlines()
    out: List[str] = []
    idx = 0
    changed = False
    while idx < len(lines):
        line = lines[idx]
        if not line.startswith("@@ "):
            out.append(line)
            idx += 1
            continue
        match = HUNK_HEADER_RE.match(line)
        if not match:
            out.append(line)
            idx += 1
            continue
        hunk_lines: List[str] = []
        idx += 1
        while idx < len(lines):
            current = lines[idx]
            if current.startswith(("diff --git ", "--- ", "+++ ", "@@ ")):
                break
            hunk_lines.append(current)
            idx += 1
        old_count = 0
        new_count = 0
        for hunk_line in hunk_lines:
            if hunk_line.startswith("\\"):
                continue
            if hunk_line.startswith((" ", "-")):
                old_count += 1
            if hunk_line.startswith((" ", "+")):
                new_count += 1
        old_start = int(match.group("old_start"))
        new_start = int(match.group("new_start"))
        repaired = f"@@ -{old_start},{old_count} +{new_start},{new_count} @@"
        if repaired != line:
            changed = True
        out.append(repaired)
        out.extend(hunk_lines)
    rebuilt = "\n".join(out)
    if rebuilt and not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return rebuilt if changed else normalized


def repair_unified_diff_bare_hunks(text: str) -> str:
    normalized = normalize_diff_text(text)
    lines = normalized.splitlines()
    out: List[str] = []
    idx = 0
    changed = False
    current_old_start = 1
    current_new_start = 1
    while idx < len(lines):
        line = lines[idx]
        if line.startswith(("diff --git ", "--- ", "+++ ")):
            out.append(line)
            idx += 1
            continue
        if not line.startswith("@@"):
            out.append(line)
            idx += 1
            continue
        if HUNK_HEADER_RE.match(line):
            match = HUNK_HEADER_RE.match(line)
            if match is not None:
                current_old_start = int(match.group("old_start"))
                current_new_start = int(match.group("new_start"))
            out.append(line)
            idx += 1
            continue
        hunk_lines: List[str] = []
        idx += 1
        while idx < len(lines):
            current = lines[idx]
            if current.startswith(("diff --git ", "--- ", "+++ ", "@@ ")):
                break
            if current.startswith("@@") and not HUNK_HEADER_RE.match(current):
                break
            hunk_lines.append(current)
            idx += 1
        old_count = 0
        new_count = 0
        for hunk_line in hunk_lines:
            if hunk_line.startswith("\\"):
                continue
            if hunk_line.startswith((" ", "-")):
                old_count += 1
            if hunk_line.startswith((" ", "+")):
                new_count += 1
        repaired = f"@@ -{current_old_start},{old_count} +{current_new_start},{new_count} @@"
        out.append(repaired)
        out.extend(hunk_lines)
        changed = True
        current_old_start += max(old_count, 1)
        current_new_start += max(new_count, 1)
    rebuilt = "\n".join(out)
    if rebuilt and not rebuilt.endswith("\n"):
        rebuilt += "\n"
    return rebuilt if changed else normalized
