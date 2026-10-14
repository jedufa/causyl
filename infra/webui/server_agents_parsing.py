from __future__ import annotations

import json
import re
from typing import Any, Dict

HOST_VERIFICATION_MARKER = "HOST_VERIFICATION_REQUEST_JSON:"
CHAT_HEADER_TITLE_MARKER = "CHAT_HEADER_TITLE:"


def extract_host_verification_request(text: str) -> Dict[str, Any]:
    raw = str(text or "")
    if HOST_VERIFICATION_MARKER not in raw:
        return {}
    payload = raw.split(HOST_VERIFICATION_MARKER, 1)[1].strip()
    if not payload:
        return {}
    try:
        obj = json.loads(payload)
    except Exception:
        return {}
    return obj if isinstance(obj, dict) else {}


def extract_chat_header_title(text: str) -> str:
    for line in reversed(str(text or "").splitlines()):
        if line.startswith(CHAT_HEADER_TITLE_MARKER):
            return normalize_chat_header_title(line.split(":", 1)[1])
    return ""


def strip_chat_header_title_marker(text: str) -> str:
    lines = [line for line in str(text or "").splitlines() if not line.startswith(CHAT_HEADER_TITLE_MARKER)]
    return "\n".join(lines).strip()


def normalize_chat_header_title(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "").strip())
    if not cleaned:
        return ""
    return cleaned[:80].strip()
