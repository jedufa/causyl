#!/usr/bin/env python3
"""Interactive helper for creating draft PIS items from copied text."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Callable, Iterable, List, Optional

from infra.webui.gpt_client import create_response, extract_output_text
from infra.webui.ledger import ledger_schema
from infra.webui.ledger import ledger_storage
from infra.webui.ledger.ledger_manager import ChangeLedger, new_uid

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MODEL = "gpt-5.4"
PIS_UID_RE = re.compile(r"\bPIS-\d{8}-\d{6}Z-[A-Z0-9]{4}\b")
REQUIRED_PIS_SECTIONS = [
    "## 1. Title",
    "## 2. Goal / user-visible behavior",
    "## 3. Scope",
    "## 4. Source-of-truth decisions",
    "## 5. Dependencies, boundary, and overlap mapping",
    "## 6. Key failure modes and recovery expectations",
    "## 7. Acceptance criteria",
    "## 8. Narrow validation expectations",
]
LIST_FIELDS = [
    "scope_in",
    "scope_out",
    "source_of_truth",
    "dependencies",
    "failure_modes",
    "acceptance_criteria",
    "validation_expectations",
]
MODEL_REQUIRED_FIELDS = [
    "title",
    "goal",
    "scope_in",
    "scope_out",
    "source_of_truth",
    "dependencies",
    "boundary_notes",
    "failure_modes",
    "acceptance_criteria",
    "validation_expectations",
    "why_it_matters",
    "problem_or_opportunity",
    "failure_if_ignored",
    "resume_note",
]


class UserAbort(Exception):
    pass


def _read_input(prompt: str = "") -> str:
    try:
        return input(prompt)
    except EOFError as exc:
        raise UserAbort("input stream closed") from exc
    except KeyboardInterrupt as exc:
        raise UserAbort("canceled by user") from exc


def _read_multiline_until_eof() -> str:
    lines: List[str] = []
    while True:
        raw = sys.stdin.readline()
        if raw == "":
            break
        lines.append(raw.rstrip("\n"))
    return "\n".join(lines).strip()


def _prompt_multiline(label: str) -> str:
    print(f"{label} (finish with EOF; Ctrl-D on Unix/WSL, Ctrl-Z then Enter on Windows)")
    text = _read_multiline_until_eof()
    print("Input captured.")
    return text


def _clean_line(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _nonempty_lines(text: str) -> List[str]:
    return [_clean_line(line) for line in str(text or "").splitlines() if _clean_line(line)]


def _normalize_tags(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        for piece in str(value or "").split(","):
            token = piece.strip().lower()
            if not token or token in out:
                continue
            out.append(token)
    return out


def _normalize_artifact_roots(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    for value in values:
        for piece in str(value or "").split(","):
            token = piece.strip()
            if not token or token in out:
                continue
            out.append(token)
    return out


def _normalize_list_field(value: Any) -> List[str]:
    if isinstance(value, list):
        out = [_clean_line(item) for item in value if _clean_line(item)]
        return out or ["Unknown; requires refinement"]
    text = _clean_line(value)
    return [text or "Unknown; requires refinement"]


def _normalize_text_field(value: Any) -> str:
    text = _clean_line(value)
    return text or "Unknown; requires refinement"


def extract_uid(text: str) -> str:
    match = PIS_UID_RE.search(str(text or ""))
    return str(match.group(0)).strip() if match else ""


def infer_title(text: str) -> str:
    raw = str(text or "")
    if not raw.strip():
        return ""
    parts: List[str] = []
    for line in raw.splitlines():
        current = str(line or "").strip()
        if not current:
            continue
        lower = current.lower()
        if lower.startswith("pis uid:") or lower.startswith("uid:"):
            continue
        if current.startswith("```"):
            continue
        current = re.sub(r"^[#>\-\*\d\.\)\s]+", "", current).strip()
        if not current:
            continue
        parts.append(current)
        if len(parts) >= 6:
            break
    text_line = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if not text_line:
        return ""
    sentence = re.match(r"(.{3,}?[.!?])(?:\s|$)", text_line)
    if sentence:
        text_line = sentence.group(1).strip()
    return text_line[:140].strip()


def infer_why_it_matters(text: str, title: str) -> str:
    candidate = infer_title(text) or str(title or "").strip()
    return candidate or "The idea needs a durable ledger entry so it can be resumed and refined without relying on chat alone."


def infer_problem_or_opportunity(text: str) -> str:
    lines = _nonempty_lines(text)
    if not lines:
        return "Copied source text identified an idea that should be tracked durably in the ledger."
    return " ".join(lines[:3]).strip()[:420].strip()


def default_failure_if_ignored(text: str) -> str:
    if "scheduler" in str(text or "").lower():
        return "The work remains an informal operator memory instead of a durable ledger item, so it is easy to forget, duplicate, or route incorrectly."
    return "The idea remains trapped in transient chat text, making scope loss, overlap, and repeated rediscovery more likely."


def default_resume_note() -> str:
    return "Review overlap with existing ledger items, tighten the scope/non-goals, and turn the draft acceptance criteria into implementation-ready checks before execution."


def _env_csv(name: str) -> List[str]:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return []
    return [piece.strip() for piece in raw.split(",") if piece.strip()]


def resolve_project_scope(
    *,
    project_id: Optional[str] = None,
    workstream_type: Optional[str] = None,
    scope_boundary: Optional[str] = None,
    artifact_roots: Optional[Iterable[str]] = None,
) -> dict[str, Any]:
    chosen_project_id = ledger_schema.normalize_project_id(
        project_id if project_id is not None else os.environ.get("PIS_PROJECT_ID")
    )
    chosen_workstream_type = ledger_schema.normalize_workstream_type(
        workstream_type if workstream_type is not None else os.environ.get("PIS_WORKSTREAM_TYPE")
    )
    chosen_scope_boundary = ledger_schema.normalize_scope_boundary(
        scope_boundary if scope_boundary is not None else os.environ.get("PIS_SCOPE_BOUNDARY")
    )
    chosen_artifact_roots = ledger_schema.normalize_artifact_roots(
        list(artifact_roots) if artifact_roots is not None else _env_csv("PIS_ARTIFACT_ROOTS")
    )
    return {
        "project_id": chosen_project_id,
        "workstream_type": chosen_workstream_type,
        "scope_boundary": chosen_scope_boundary,
        "artifact_roots": chosen_artifact_roots,
    }


def _looks_like_full_pis(text: str) -> bool:
    raw = str(text or "")
    if "# Patch Instruction Sheet (PIS)" in raw:
        return True
    if "**UID:**" in raw and "## 1. Title" in raw:
        return True
    return False


def validate_pis_markdown(text: str, *, expected_uid: str) -> None:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("PIS markdown is empty")
    if f"**UID:** {expected_uid}" not in raw:
        raise ValueError(f"PIS markdown must include '**UID:** {expected_uid}'")
    missing = [section for section in REQUIRED_PIS_SECTIONS if section not in raw]
    if missing:
        raise ValueError("PIS markdown is missing required sections: " + ", ".join(missing))


def _default_structured_fields(raw_text: str, title: str) -> dict[str, Any]:
    problem_or_opportunity = _clean_line(infer_problem_or_opportunity(raw_text))
    failure_if_ignored = _clean_line(default_failure_if_ignored(raw_text))
    resume_note = _clean_line(default_resume_note())
    return {
        "title": title,
        "goal": "Create a durable draft ledger item for this idea so the operator can refine it from a stable UID instead of re-deriving intent from chat.",
        "scope_in": [
            "capture the copied idea as a durable draft with enough structure to resume and refine it honestly",
            f"preserve the current problem/opportunity signal: {problem_or_opportunity}",
            "keep this item explicitly in draft until boundaries, dependencies, and acceptance are tightened",
        ],
        "scope_out": [
            "claiming the item is implementation-ready today",
            "silently choosing sibling ownership or dependency edges without an overlap audit",
            "replacing canonical evidence or later implementation proof with this intake draft",
        ],
        "source_of_truth": [
            "copied operator text is the intake signal for this draft, not the final contract by itself",
            "the ledger item created here becomes the durable planning anchor for follow-up refinement",
            "chat context may explain why the idea appeared, but the ledger item is the authoritative backlog record after creation",
        ],
        "dependencies": [
            "unknown until overlap audit against existing PIS items is completed",
        ],
        "boundary_notes": "this draft owns intake preservation and initial framing only; implementation ownership, decomposition, and validation design must be added explicitly before execution",
        "failure_modes": [
            failure_if_ignored,
            "the copied text is too vague, causing the draft to masquerade as a usable implementation contract",
            "overlap with an existing PIS is missed, producing duplicate or conflicting governance records",
            "after restart, the operator cannot tell what to refine next because the draft lacks a concrete resume note",
        ],
        "acceptance_criteria": [
            "the draft preserves a stable title, why-it-matters statement, current problem/opportunity, likely failure-if-ignored note, and resume note",
            "the document clearly signals draft status and does not pretend unverified dependencies or implementation readiness",
            "another operator can open the UID later and know what text triggered the item plus what refinement step comes next",
        ],
        "validation_expectations": [
            "confirm the created ledger item contains the required draft sections and explicit UID in pis.md",
            "confirm status remains draft and that copied source text is preserved for later refinement",
            "if overlap or dependency truth is still unknown, leave it explicit instead of guessing",
        ],
        "why_it_matters": _clean_line(infer_why_it_matters(raw_text, title)),
        "problem_or_opportunity": problem_or_opportunity,
        "failure_if_ignored": failure_if_ignored,
        "resume_note": resume_note,
    }


def _json_from_response_text(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("empty model response")
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        obj = json.loads(raw)
    except Exception as exc:
        raise ValueError(f"model response was not valid JSON: {exc}") from exc
    if not isinstance(obj, dict):
        raise ValueError("model response must be a JSON object")
    return obj


def normalize_model_fields(obj: dict[str, Any], *, raw_text: str, title: str) -> dict[str, Any]:
    base = _default_structured_fields(raw_text, title)
    for field in MODEL_REQUIRED_FIELDS:
        if field in LIST_FIELDS:
            base[field] = _normalize_list_field(obj.get(field))
        elif field == "title":
            base[field] = _normalize_text_field(obj.get(field) or title)
        else:
            base[field] = _normalize_text_field(obj.get(field))
    return base


def build_api_instructions(uid: str) -> str:
    return (
        "You convert copied operator text into a draft Patch Instruction Sheet field set.\n"
        "Return JSON only.\n"
        f"Do not generate or change the UID; the caller already allocated {uid}.\n"
        "Do not invent certainty. When the source text is missing information, write 'Unknown; requires refinement'.\n"
        "Keep the result draft-grade: structured and useful, but not falsely implementation-ready.\n"
        "Prefer concise, operational wording over narrative."
    )


def build_api_prompt(*, raw_text: str, uid: str, title_hint: str) -> str:
    return (
        "Convert the copied operator text below into JSON fields for a draft PIS.\n\n"
        "Required JSON keys:\n"
        f"{json.dumps(MODEL_REQUIRED_FIELDS)}\n\n"
        "Field expectations:\n"
        "- scope_in, scope_out, source_of_truth, dependencies, failure_modes, acceptance_criteria, validation_expectations: arrays of short strings\n"
        "- title, goal, boundary_notes, why_it_matters, problem_or_opportunity, failure_if_ignored, resume_note: short strings\n"
        "- if dependencies or overlap are unclear, say so explicitly instead of guessing\n"
        f"- the preassigned UID is {uid}; do not include any other UID\n"
        f"- current title hint: {title_hint}\n\n"
        "Copied source text:\n"
        f"{raw_text}"
    )


def draft_fields_via_api(
    *,
    raw_text: str,
    uid: str,
    title_hint: str,
    model: str = DEFAULT_MODEL,
    timeout_sec: float = 120.0,
    response_creator: Optional[Callable[..., dict[str, Any]]] = None,
) -> dict[str, Any]:
    creator = response_creator or create_response
    response = creator(
        model=model,
        input_text=build_api_prompt(raw_text=raw_text, uid=uid, title_hint=title_hint),
        instructions=build_api_instructions(uid),
        reasoning_effort="medium",
        timeout_sec=timeout_sec,
    )
    text = extract_output_text(response)
    parsed = _json_from_response_text(text)
    return normalize_model_fields(parsed, raw_text=raw_text, title=title_hint)


def build_pis_markdown(*, raw_text: str, uid: str, fields: dict[str, Any]) -> str:
    body = str(raw_text or "").strip()
    if _looks_like_full_pis(body):
        validate_pis_markdown(body, expected_uid=uid)
        return body
    quoted_source = "\n".join([f"> {line}" if line else ">" for line in body.splitlines()]) if body else "> (empty)"
    scope_in = "\n".join(f"- {item}" for item in _normalize_list_field(fields.get("scope_in")))
    scope_out = "\n".join(f"- {item}" for item in _normalize_list_field(fields.get("scope_out")))
    source_of_truth = "\n".join(f"- {item}" for item in _normalize_list_field(fields.get("source_of_truth")))
    dependencies = "\n".join(f"- {item}" for item in _normalize_list_field(fields.get("dependencies")))
    failure_modes = "\n".join(f"- {item}" for item in _normalize_list_field(fields.get("failure_modes")))
    acceptance = "\n".join(f"- {item}" for item in _normalize_list_field(fields.get("acceptance_criteria")))
    validation = "\n".join(f"- {item}" for item in _normalize_list_field(fields.get("validation_expectations")))
    return (
        "# Patch Instruction Sheet (PIS)\n\n"
        f"**UID:** {uid}\n\n"
        "## 1. Title\n"
        f"{_normalize_text_field(fields.get('title'))}\n\n"
        "## 2. Goal / user-visible behavior\n"
        f"{_normalize_text_field(fields.get('goal'))}\n\n"
        "## 3. Scope\n"
        "In scope:\n"
        f"{scope_in}\n\n"
        "Explicit non-goals:\n"
        f"{scope_out}\n\n"
        "## 4. Source-of-truth decisions\n"
        f"{source_of_truth}\n\n"
        "## 5. Dependencies, boundary, and overlap mapping\n"
        "Depends on:\n"
        f"{dependencies}\n\n"
        "Boundary / non-overlap:\n"
        f"- {_normalize_text_field(fields.get('boundary_notes'))}\n\n"
        "## 6. Key failure modes and recovery expectations\n"
        f"{failure_modes}\n\n"
        "## 7. Acceptance criteria\n"
        f"{acceptance}\n\n"
        "## 8. Narrow validation expectations\n"
        f"{validation}\n\n"
        "## 9. Intake summary\n"
        f"- Why it matters: {_normalize_text_field(fields.get('why_it_matters'))}\n"
        f"- Current problem or opportunity: {_normalize_text_field(fields.get('problem_or_opportunity'))}\n"
        f"- Likely failure if ignored: {_normalize_text_field(fields.get('failure_if_ignored'))}\n"
        f"- Resume from here: {_normalize_text_field(fields.get('resume_note'))}\n\n"
        "## 10. Copied source text\n"
        f"{quoted_source}\n\n"
        "## 11. Drafting note\n"
        "- Created from copied text via `infra/user/pis-create-from-text`.\n"
        f"- Drafted with model assistance using `{DEFAULT_MODEL}` when available; fallback remains deterministic.\n"
        "- Status intentionally remains `draft` until scope, boundaries, overlap, and acceptance are tightened.\n"
    ).strip() + "\n"


def create_draft_item(
    *,
    repo_root: Path,
    raw_text: str,
    title: Optional[str] = None,
    why_it_matters: Optional[str] = None,
    problem_or_opportunity: Optional[str] = None,
    failure_if_ignored: Optional[str] = None,
    resume_note: Optional[str] = None,
    slug: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    uid: Optional[str] = None,
    project_id: Optional[str] = None,
    workstream_type: Optional[str] = None,
    scope_boundary: Optional[str] = None,
    artifact_roots: Optional[Iterable[str]] = None,
    use_api: bool = True,
    model: str = DEFAULT_MODEL,
    timeout_sec: float = 120.0,
    response_creator: Optional[Callable[..., dict[str, Any]]] = None,
) -> dict:
    source = str(raw_text or "").strip()
    if not source:
        raise ValueError("copied text is empty")
    chosen_uid = str(uid or extract_uid(source) or new_uid()).strip()
    chosen_title = str(title or infer_title(source) or "Draft idea from copied text").strip()
    fields = _default_structured_fields(source, chosen_title)
    if why_it_matters:
        fields["why_it_matters"] = _clean_line(why_it_matters)
    if problem_or_opportunity:
        fields["problem_or_opportunity"] = _clean_line(problem_or_opportunity)
        fields["scope_in"][1] = f"preserve the current problem/opportunity signal: {fields['problem_or_opportunity']}"
    if failure_if_ignored:
        fields["failure_if_ignored"] = _clean_line(failure_if_ignored)
        fields["failure_modes"][0] = fields["failure_if_ignored"]
    if resume_note:
        fields["resume_note"] = _clean_line(resume_note)
    if use_api and not _looks_like_full_pis(source) and (response_creator is not None or os.environ.get("OPENAI_API_KEY")):
        try:
            fields = draft_fields_via_api(
                raw_text=source,
                uid=chosen_uid,
                title_hint=chosen_title,
                model=model,
                timeout_sec=timeout_sec,
                response_creator=response_creator,
            )
        except Exception:
            pass
    ledger = ChangeLedger(repo_root=repo_root)
    project_scope = resolve_project_scope(
        project_id=project_id,
        workstream_type=workstream_type,
        scope_boundary=scope_boundary,
        artifact_roots=artifact_roots,
    )
    pis_markdown = build_pis_markdown(raw_text=source, uid=chosen_uid, fields=fields)
    return ledger.create_item(
        title=_normalize_text_field(fields.get("title")),
        slug=slug,
        tags=_normalize_tags(tags or []),
        pis_markdown=pis_markdown,
        uid_override=chosen_uid,
        project_id=str(project_scope["project_id"]),
        workstream_type=str(project_scope["workstream_type"]),
        scope_boundary=str(project_scope["scope_boundary"]),
        artifact_roots=list(project_scope["artifact_roots"]),
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--title", help="override title")
    parser.add_argument("--why-it-matters", help="override why-it-matters summary")
    parser.add_argument("--problem-or-opportunity", help="override current problem/opportunity summary")
    parser.add_argument("--failure-if-ignored", help="override likely failure if ignored")
    parser.add_argument("--resume-note", help="override resume-from-here note")
    parser.add_argument("--slug", help="override slug")
    parser.add_argument("--tag", action="append", default=[], help="topic tag; may be repeated or comma-separated")
    parser.add_argument("--uid", help="override PIS UID")
    parser.add_argument("--project-id", help=f"ledger project_id (default: {ledger_schema.DEFAULT_PROJECT_ID} or PIS_PROJECT_ID)")
    parser.add_argument(
        "--workstream-type",
        choices=sorted(ledger_schema.ALLOWED_WORKSTREAM_TYPES),
        help=f"ledger workstream_type (default: {ledger_schema.DEFAULT_WORKSTREAM_TYPE} or PIS_WORKSTREAM_TYPE)",
    )
    parser.add_argument(
        "--scope-boundary",
        choices=sorted(ledger_schema.ALLOWED_SCOPE_BOUNDARIES),
        help=f"ledger scope_boundary (default: {ledger_schema.DEFAULT_SCOPE_BOUNDARY} or PIS_SCOPE_BOUNDARY)",
    )
    parser.add_argument(
        "--artifact-root",
        action="append",
        default=[],
        help="artifact root path; may be repeated or comma-separated (default: PIS_ARTIFACT_ROOTS or empty)",
    )
    parser.add_argument("--text-file", help="read copied text from file instead of stdin/prompt")
    parser.add_argument("--stdin", action="store_true", help="read copied text from stdin")
    parser.add_argument("--no-api", action="store_true", help="skip the best-effort model drafting call")
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"model for best-effort drafting (default: {DEFAULT_MODEL})")
    parser.add_argument("--timeout-sec", type=float, default=120.0, help="API timeout for best-effort drafting")
    parser.add_argument("--yes", action="store_true", help="skip confirmation prompt")
    parser.add_argument("--print-only", action="store_true", help="print generated PIS markdown without creating an item")
    return parser


def _load_source_text(args: argparse.Namespace) -> str:
    if args.text_file:
        return Path(args.text_file).read_text(encoding="utf-8").strip()
    if args.stdin or not sys.stdin.isatty():
        return _read_multiline_until_eof()
    return _prompt_multiline("Paste copied text")


def main(argv: Optional[List[str]] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        raw_text = _load_source_text(args)
        if not raw_text.strip():
            raise UserAbort("no copied text provided")
        chosen_uid = str(args.uid or extract_uid(raw_text) or new_uid()).strip()
        chosen_title = str(args.title or infer_title(raw_text) or "Draft idea from copied text").strip()
        tags = _normalize_tags(args.tag or [])
        project_scope = resolve_project_scope(
            project_id=args.project_id,
            workstream_type=args.workstream_type,
            scope_boundary=args.scope_boundary,
            artifact_roots=_normalize_artifact_roots(args.artifact_root or []),
        )
        if sys.stdin.isatty():
            print(f"Prepared draft shell for {chosen_uid}.")
        fields = _default_structured_fields(raw_text, chosen_title)
        if args.why_it_matters:
            fields["why_it_matters"] = _clean_line(args.why_it_matters)
        if args.problem_or_opportunity:
            fields["problem_or_opportunity"] = _clean_line(args.problem_or_opportunity)
            fields["scope_in"][1] = f"preserve the current problem/opportunity signal: {fields['problem_or_opportunity']}"
        if args.failure_if_ignored:
            fields["failure_if_ignored"] = _clean_line(args.failure_if_ignored)
            fields["failure_modes"][0] = fields["failure_if_ignored"]
        if args.resume_note:
            fields["resume_note"] = _clean_line(args.resume_note)

        used_api = False
        api_error = ""
        if not args.no_api and not _looks_like_full_pis(raw_text) and os.environ.get("OPENAI_API_KEY"):
            try:
                if sys.stdin.isatty():
                    print(f"Calling {str(args.model or DEFAULT_MODEL)} for best-effort draft structuring. Please wait...")
                fields = draft_fields_via_api(
                    raw_text=raw_text,
                    uid=chosen_uid,
                    title_hint=chosen_title,
                    model=str(args.model or DEFAULT_MODEL),
                    timeout_sec=float(args.timeout_sec),
                )
                used_api = True
                if sys.stdin.isatty():
                    print("Model draft received. Rendering final PIS locally...")
            except Exception as exc:
                api_error = str(exc)
                if sys.stdin.isatty():
                    print(f"Model draft failed; continuing with deterministic fallback: {api_error}")
        elif sys.stdin.isatty() and not args.no_api:
            print("Skipping model draft and using deterministic local drafting.")

        pis_markdown = build_pis_markdown(raw_text=raw_text, uid=chosen_uid, fields=fields)
        if sys.stdin.isatty():
            print("PIS draft assembled.")
        if args.print_only:
            sys.stdout.write(pis_markdown)
            return 0
        print(f"Title: {_normalize_text_field(fields.get('title'))}")
        print(f"UID:   {chosen_uid}")
        print(f"Model: {str(args.model or DEFAULT_MODEL)}")
        print(f"Draft: {'model-assisted' if used_api else 'deterministic'}")
        if api_error:
            print(f"API:   fallback after error: {api_error}")
        elif not used_api and not args.no_api:
            print("API:   skipped (OPENAI_API_KEY not set or full PIS text was pasted)")
        print(f"Why:   {_normalize_text_field(fields.get('why_it_matters'))}")
        print(f"Resume: {_normalize_text_field(fields.get('resume_note'))}")
        print(f"Project: {project_scope['project_id']}")
        print(f"Workstream: {project_scope['workstream_type']}")
        print(f"Boundary: {project_scope['scope_boundary']}")
        if project_scope["artifact_roots"]:
            print(f"Artifact roots: {', '.join(project_scope['artifact_roots'])}")
        if tags:
            print(f"Tags:  {', '.join(tags)}")
        print()
        print("Preview:")
        print("-" * 60)
        sys.stdout.write(pis_markdown)
        print("-" * 60)
        if not args.yes:
            answer = _read_input("Create draft ledger item? [Y/n]: ").strip().lower()
            if answer not in {"", "y", "yes"}:
                raise UserAbort("canceled by user")
        result = create_draft_item(
            repo_root=REPO_ROOT,
            raw_text=raw_text,
            title=args.title,
            why_it_matters=args.why_it_matters,
            problem_or_opportunity=args.problem_or_opportunity,
            failure_if_ignored=args.failure_if_ignored,
            resume_note=args.resume_note,
            slug=args.slug,
            tags=tags,
            uid=chosen_uid,
            project_id=str(project_scope["project_id"]),
            workstream_type=str(project_scope["workstream_type"]),
            scope_boundary=str(project_scope["scope_boundary"]),
            artifact_roots=list(project_scope["artifact_roots"]),
            use_api=not bool(args.no_api),
            model=str(args.model or DEFAULT_MODEL),
            timeout_sec=float(args.timeout_sec),
        )
        item_dir = ledger_storage.item_dir_for_uid(
            REPO_ROOT / "governance" / "ledger" / "items",
            str(result["uid"]),
        )
        if item_dir is None:
            item_dir = REPO_ROOT / "governance" / "ledger" / "items" / str(result["dir"])
        print(f"Created {result['uid']} at {item_dir}")
        print(
            "Scope:"
            f" project_id={project_scope['project_id']}"
            f" workstream_type={project_scope['workstream_type']}"
            f" scope_boundary={project_scope['scope_boundary']}"
        )
        return 0
    except UserAbort as exc:
        print(f"Canceled: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
