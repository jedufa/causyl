from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import List, Tuple, Optional

_KEY_LABELS = list("abcdefghijklmnopqrstuvwxyz1234567890")


def _read_single_key(prompt: str = "") -> str:
    if prompt:
        print(prompt, end="", flush=True)
    if not sys.stdin.isatty():
        raw = input().strip().lower()
        return raw[:1] if raw else ""
    try:
        import termios, tty  # type: ignore
        fd = sys.stdin.fileno()
        old = termios.tcgetattr(fd)
        try:
            tty.setraw(fd)
            ch = sys.stdin.read(1)
        finally:
            termios.tcsetattr(fd, termios.TCSADRAIN, old)
        print(ch)
        return ch.lower()
    except Exception:
        raw = input().strip().lower()
        return raw[:1] if raw else ""


def ask(prompt: str, default: Optional[str] = None) -> str:
    suffix = f" [{default}]" if default not in (None, "") else ""
    value = input(f"{prompt}{suffix}: ").strip()
    if not value and default is not None:
        return str(default)
    return value


def ask_multiline_list(prompt: str) -> List[str]:
    print(f"{prompt} (enter one per line, blank line to finish)")
    items: List[str] = []
    while True:
        value = input("> ").strip()
        if not value:
            break
        items.append(value)
    return items


def choose(title: str, options: List[Tuple[str, str]]) -> str:
    print(f"\n{title}")
    if len(options) <= len(_KEY_LABELS):
        mapping = {}
        for idx, (value, label) in enumerate(options):
            key = _KEY_LABELS[idx]
            mapping[key] = value
            print(f"[{key}] {label}")
        while True:
            raw = _read_single_key("choose: ").strip().lower()
            if raw in mapping:
                return mapping[raw]
            print("invalid choice. try again.")
    for idx, (_, label) in enumerate(options, start=1):
        print(f"{idx}. {label}")
    while True:
        raw = input("choose: ").strip()
        if raw.isdigit():
            idx = int(raw)
            if 1 <= idx <= len(options):
                return options[idx - 1][0]
        print("invalid choice. try again.")


def confirm(prompt: str, default_yes: bool = True) -> bool:
    default_label = "y" if default_yes else "n"
    while True:
        raw = _read_single_key(f"{prompt} [y/n, default={default_label}]: ").strip().lower()
        if not raw:
            return default_yes
        if raw in {"y", "n"}:
            return raw == "y"
        print("please choose y or n.")


def create_job_flow(default_repo_root: str = "./") -> dict:
    mode = choose("Create job from", [
        ("guided", "Guided fields"),
        ("spec", "Paste full structured spec"),
    ])
    if mode == "spec":
        return parse_structured_spec_flow(default_repo_root)
    title = ask("Title")
    description = ask("Description")
    repo_root = ask("Repo root", default_repo_root)
    objective = ask("Objective")
    constraints = ask_multiline_list("Constraints")
    acceptance_criteria = ask_multiline_list("Acceptance criteria")
    allowed_paths = ask_multiline_list("Allowed paths (optional)")
    forbidden_paths = ask_multiline_list("Forbidden paths (optional)")
    return {
        "title": title,
        "description": description,
        "repo_root": repo_root,
        "objective": objective,
        "constraints": constraints,
        "acceptance_criteria": acceptance_criteria,
        "allowed_paths": allowed_paths,
        "forbidden_paths": forbidden_paths,
    }


def review_decision_flow() -> tuple[str, str, list[str]]:
    decision = choose("Planning review decision", [
        ("APPROVE", "Approve planning result"),
        ("REVISE_AND_RERUN", "Reject / revise and rerun later"),
    ])
    reason = ask("Reason")
    revisions = ask_multiline_list("Requested revisions (optional)")
    return decision, reason, revisions


def _extract_section_block(text: str, section_name: str) -> str:
    pattern = re.compile(rf"^##\s+{re.escape(section_name)}\s*$", re.IGNORECASE | re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    start = match.end()
    rest = text[start:]
    next_sec = re.search(r"^##\s+.+$", rest, re.MULTILINE)
    block = rest[: next_sec.start()] if next_sec else rest
    return block.strip()


def _parse_bullet_lines(block: str) -> List[str]:
    out = []
    for line in block.splitlines():
        stripped = line.strip()
        if stripped.startswith("- "):
            out.append(stripped[2:].strip())
    return out


def parse_structured_spec_flow(default_repo_root: str = "./") -> dict:
    print("Paste full structured spec. Finish with a single line containing END")
    lines: List[str] = []
    while True:
        line = input()
        if line.strip() == "END":
            break
        lines.append(line)
    text = "\n".join(lines).strip()
    title_match = re.search(r"^#\s+(.+)$", text, re.MULTILINE)
    title = title_match.group(1).strip() if title_match else ask("Title")
    purpose_block = _extract_section_block(text, "Purpose")
    scope_block = _extract_section_block(text, "Scope")
    description = title
    objective = purpose_block.splitlines()[0].strip() if purpose_block else title
    constraints = _parse_bullet_lines(scope_block)
    acceptance = []
    test_plan_match = re.search(r"test_plan\s*:\s*(.+)$", text, re.IGNORECASE | re.MULTILINE)
    if test_plan_match:
        acceptance.append("Test plan: " + test_plan_match.group(1).strip())
    repo_root = ask("Repo root", default_repo_root)
    return {
        "title": title,
        "description": description,
        "repo_root": repo_root,
        "objective": objective,
        "constraints": constraints,
        "acceptance_criteria": acceptance,
        "allowed_paths": [],
        "forbidden_paths": [],
        "raw_spec": text,
    }
