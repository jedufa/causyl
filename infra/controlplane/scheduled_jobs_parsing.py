from __future__ import annotations

import re
from typing import List


def extract_markdown_section(markdown: str, heading: str) -> str:
    lines = str(markdown or "").splitlines()
    target = str(heading or "").strip().lower()
    capture: List[str] = []
    active = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            current = stripped.lstrip("#").strip().lower()
            if active and current != target:
                break
            active = current == target
            continue
        if active:
            capture.append(line)
    return "\n".join(capture).strip()


def normalize_section_heading_label(line: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(line or "").strip().lower()).strip()


def has_named_heading(text: str, *headings: str) -> bool:
    wanted = {normalize_section_heading_label(h) for h in headings if str(h or "").strip()}
    if not wanted:
        return False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not (
            stripped.startswith("#")
            or (stripped.startswith("**") and stripped.endswith("**") and len(stripped) > 4)
            or stripped.endswith(":")
        ):
            continue
        current = normalize_section_heading_label(stripped.lstrip("#"))
        if current in wanted:
            return True
    return False


def extract_named_bullet_block(text: str, heading: str) -> List[str]:
    lines = str(text or "").splitlines()
    target = normalize_section_heading_label(heading)
    items: List[str] = []
    active = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#") or (stripped.startswith("**") and stripped.endswith("**")) or stripped.endswith(":"):
            current = normalize_section_heading_label(stripped.lstrip("#"))
            if active and current != target:
                break
            active = current == target
            continue
        if active and stripped.startswith(("-", "*")):
            items.append(stripped[1:].strip())
    return items
