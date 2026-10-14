"""infra.webui.gpt_client

Minimal OpenAI client for the WebUI admin workflows.

Design goals
------------
- Stdlib only (no extra dependencies).
- Small surface: upload a file, call Responses API, extract output text.
- Fail loudly with actionable error strings for UI to display.

This module intentionally avoids being "smart". The WebUI drives policy
decisions (model choice, prompts, storage), this module just does HTTP.

References (official docs)
--------------------------
- Files API: POST /v1/files (multipart form) purpose="user_data".
- Responses API: POST /v1/responses with input_file parts referencing file_id.
"""

from __future__ import annotations

import json
import mimetypes
import os
import time
import uuid
import urllib.request
from dataclasses import dataclass
from typing import Any, Dict, Iterator, Optional, Tuple


DEFAULT_BASE_URL = "https://api.openai.com/v1"


class OpenAIHttpError(RuntimeError):
    """Raised when OpenAI returns a non-2xx response."""


@dataclass(frozen=True)
class UploadResult:
    """Result for a successful file upload."""

    file_id: str
    filename: str
    bytes: int
    purpose: str


def _read_key(api_key: Optional[str]) -> str:
    """Read API key from parameter or environment."""
    k = (api_key or os.environ.get("OPENAI_API_KEY") or "").strip()
    if not k:
        raise ValueError("OPENAI_API_KEY not set")
    return k


def _http_json(
    *,
    url: str,
    method: str,
    headers: Dict[str, str],
    body: Optional[bytes],
    timeout_sec: float,
) -> Dict[str, Any]:
    """Perform an HTTP request and parse JSON response."""
    req = urllib.request.Request(url=url, data=body, method=method)
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = resp.read()
            try:
                return json.loads(data.decode("utf-8"))
            except Exception as e:
                raise OpenAIHttpError(f"non-JSON response from OpenAI: {e}")
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        raw = b""
        try:
            raw = e.read()  # type: ignore[assignment]
        except Exception:
            raw = b""
        msg = raw.decode("utf-8", errors="replace") if raw else str(e)
        raise OpenAIHttpError(f"OpenAI HTTP {e.code}: {msg}")
    except Exception as e:
        raise OpenAIHttpError(str(e))


def upload_file(
    file_path: str,
    *,
    api_key: Optional[str] = None,
    purpose: str = "user_data",
    base_url: str = DEFAULT_BASE_URL,
    timeout_sec: float = 320.0,
) -> UploadResult:
    """Upload a file via the OpenAI Files API.

    Args:
        file_path: Path to file on disk.
        api_key: Optional OpenAI API key; defaults to OPENAI_API_KEY env.
        purpose: Files API "purpose".
        base_url: API base URL.
        timeout_sec: HTTP timeout.

    Returns:
        UploadResult containing file_id.
    """
    key = _read_key(api_key)
    p = os.path.abspath(file_path)
    if not os.path.exists(p):
        raise FileNotFoundError(p)

    filename = os.path.basename(p)
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    boundary = f"----openai-webui-{uuid.uuid4().hex}"

    def _part(name: str, value: str) -> bytes:
        return (
            f"--{boundary}\r\n"
            f"Content-Disposition: form-data; name=\"{name}\"\r\n\r\n"
            f"{value}\r\n"
        ).encode("utf-8")

    with open(p, "rb") as f:
        blob = f.read()

    file_hdr = (
        f"--{boundary}\r\n"
        f"Content-Disposition: form-data; name=\"file\"; filename=\"{filename}\"\r\n"
        f"Content-Type: {ctype}\r\n\r\n"
    ).encode("utf-8")
    tail = f"\r\n--{boundary}--\r\n".encode("utf-8")

    body = b"".join([
        _part("purpose", purpose),
        file_hdr,
        blob,
        tail,
    ])

    res = _http_json(
        url=f"{base_url}/files",
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        },
        body=body,
        timeout_sec=timeout_sec,
    )

    fid = str(res.get("id") or "").strip()
    if not fid:
        raise OpenAIHttpError(f"upload succeeded but file id missing: {res}")
    return UploadResult(file_id=fid, filename=str(res.get("filename") or filename), bytes=int(res.get("bytes") or len(blob)), purpose=str(res.get("purpose") or purpose))


def create_response(
    *,
    model: str,
    input_text: str,
    file_id: Optional[str] = None,
    tools: Optional[Any] = None,
    instructions: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout_sec: float = 320.0,
) -> Dict[str, Any]:
    """Call the OpenAI Responses API.

    Args:
        model: Model name.
        input_text: Text prompt.
        file_id: Optional file_id to attach as input_file.

    Returns:
        Parsed JSON response.
    """
    key = _read_key(api_key)
    content = []
    if file_id:
        content.append({"type": "input_file", "file_id": str(file_id)})
    content.append({"type": "input_text", "text": str(input_text)})

    payload: Dict[str, Any] = {
        "model": model,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }

    # Optional instructions (system-style guidance supported by Responses API).
    if isinstance(instructions, str) and instructions.strip():
        payload["instructions"] = instructions.strip()

    if isinstance(reasoning_effort, str) and reasoning_effort.strip():
        payload["reasoning"] = {"effort": reasoning_effort.strip().lower()}

    # Optional tools. Used in particular for code_interpreter container files.
    # NOTE: When attaching a .zip (repo snapshots), OpenAI rejects it as an
    #       "input_file" for context stuffing, but it *can* be provided to the
    #       code interpreter container. Callers should set tools accordingly and
    #       avoid passing file_id.
    if tools is not None:
        payload["tools"] = tools

    body = json.dumps(payload).encode("utf-8")
    return _http_json(
        url=f"{base_url}/responses",
        method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        },
        body=body,
        timeout_sec=timeout_sec,
    )


def stream_response_events(
    *,
    model: str,
    input_text: str,
    file_id: Optional[str] = None,
    tools: Optional[Any] = None,
    instructions: Optional[str] = None,
    reasoning_effort: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: str = DEFAULT_BASE_URL,
    timeout_sec: float = 320.0,) -> Iterator[Dict[str, Any]]:
    """Yield Responses API stream events.

    This uses SSE (`stream=true`) and yields parsed JSON objects from each
    `data:` frame.
    """
    key = _read_key(api_key)
    content = []
    if file_id:
        content.append({"type": "input_file", "file_id": str(file_id)})
    content.append({"type": "input_text", "text": str(input_text)})

    payload: Dict[str, Any] = {
        "model": model,
        "stream": True,
        "input": [
            {
                "role": "user",
                "content": content,
            }
        ],
    }
    if isinstance(instructions, str) and instructions.strip():
        payload["instructions"] = instructions.strip()
    if isinstance(reasoning_effort, str) and reasoning_effort.strip():
        payload["reasoning"] = {"effort": reasoning_effort.strip().lower()}
    if tools is not None:
        payload["tools"] = tools
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url=f"{base_url}/responses", data=body, method="POST")
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Content-Type", "application/json")
    req.add_header("Accept", "text/event-stream")

    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            for raw in resp:
                if not raw:
                    continue
                line = raw.decode("utf-8", errors="replace").strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data = line[len("data:"):].strip()
                if not data or data == "[DONE]":
                    continue
                try:
                    obj = json.loads(data)
                except Exception:
                    continue
                if isinstance(obj, dict):
                    yield obj
    except urllib.error.HTTPError as e:  # type: ignore[attr-defined]
        raw = b""
        try:
            raw = e.read()  # type: ignore[assignment]
        except Exception:
            raw = b""
        msg = raw.decode("utf-8", errors="replace") if raw else str(e)
        raise OpenAIHttpError(f"OpenAI HTTP {e.code}: {msg}")
    except Exception as e:
        raise OpenAIHttpError(str(e))


def extract_output_text(resp: Dict[str, Any]) -> str:
    """Best-effort extraction of the assistant's text from a Responses API payload."""
    # Newer responses often include output_text directly.
    try:
        ot = resp.get("output_text")
        if isinstance(ot, str) and ot.strip():
            return ot
    except Exception:
        pass

    # Fallback: walk output[].content[].text
    parts = []
    out = resp.get("output")
    if isinstance(out, list):
        for item in out:
            if not isinstance(item, dict):
                continue
            content = item.get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                # Common shapes: {type:"output_text", text:"..."} or {type:"text", text:"..."}
                txt = c.get("text")
                if isinstance(txt, str) and txt:
                    parts.append(txt)
    return "\n".join(parts).strip()
