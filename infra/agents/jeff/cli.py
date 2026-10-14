from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Sequence

from agents.jeff import backend as jeff_backend
from agents.jeff import engine, session_store

DEFAULT_REPO = "/home/user/causyl"


def _approval_callback_factory(*, stdin: Any, stdout: Any):
    def _callback(state: dict) -> bool:
        diff_preview = str(state.get("diff_preview") or "").strip()
        if diff_preview:
            stdout.write(diff_preview + "\n")
        stdout.write("Apply proposed changes? [y/N]: ")
        stdout.flush()
        answer = stdin.readline().strip().lower()
        return answer in {"y", "yes"}

    return _callback


def _print_result(result: dict, *, stdout: Any) -> None:
    proposal = dict(result.get("proposal") or {})
    verification = dict(result.get("verification") or {})
    stdout.write(f"session_id={result['session']['session_id']}\n")
    stdout.write(f"proposal_summary={proposal.get('summary','')}\n")
    stdout.write(f"verification={json.dumps(verification, ensure_ascii=False)}\n")
    if str(result.get("diff_preview") or "").strip():
        stdout.write("diff_preview:\n")
        stdout.write(str(result.get("diff_preview") or "") + "\n")


def _load_or_create_session(*, repo: str, session_id: str) -> dict:
    if session_id:
        return session_store.load_session(session_id)
    session = session_store.new_session(repo_path=str(Path(repo).resolve()))
    return session_store.save_session(session)


def _diff(args: argparse.Namespace, *, stdout: Any) -> int:
    session = session_store.load_session(args.session)
    turns = list(session.get("turns") or [])
    if not turns:
        stdout.write("no recorded turns for session\n")
        return 1
    last = turns[-1]
    preview = str(last.get("diff_preview") or "").strip()
    if not preview:
        stdout.write("no diff evidence recorded for last turn\n")
        return 1
    stdout.write(f"session_id={session['session_id']}\n")
    stdout.write(f"turn_id={last.get('turn_id','')}\n")
    stdout.write("diff_preview:\n")
    stdout.write(preview + "\n")
    return 0


def _run_once(args: argparse.Namespace, *, stdout: Any, stdin: Any) -> int:
    session = _load_or_create_session(repo=args.repo, session_id=str(getattr(args, "session", "") or ""))
    backend = jeff_backend.create_backend(provider=args.provider, model=args.model)
    result = engine.run_task(
        session=session,
        repo_path=args.repo,
        task=args.task,
        approval_policy=args.approval,
        backend=backend,
        approval_callback=_approval_callback_factory(stdin=stdin, stdout=stdout),
    )
    _print_result(result, stdout=stdout)
    return 0 if bool((result.get("verification") or {}).get("ok")) else 1


def _chat(args: argparse.Namespace, *, stdin: Any, stdout: Any) -> int:
    session = _load_or_create_session(repo=args.repo, session_id=str(getattr(args, "session", "") or ""))
    backend = jeff_backend.create_backend(provider=args.provider, model=args.model)
    if str(args.task or "").strip():
        result = engine.run_task(
            session=session,
            repo_path=args.repo,
            task=args.task,
            approval_policy=args.approval,
            backend=backend,
            approval_callback=_approval_callback_factory(stdin=stdin, stdout=stdout),
        )
        _print_result(result, stdout=stdout)
        return 0 if bool((result.get("verification") or {}).get("ok")) else 1
    stdout.write(f"jeff session {session['session_id']} for {args.repo}\n")
    while True:
        stdout.write("jeff> ")
        stdout.flush()
        raw = stdin.readline()
        if raw == "":
            return 0
        task = raw.strip()
        if not task:
            continue
        if task in {"/quit", "/exit"}:
            return 0
        if task == "/undo":
            current = session_store.load_session(session["session_id"])
            undo = engine.undo_last_checkpoint(current)
            session_store.save_session(current)
            stdout.write(json.dumps(undo, ensure_ascii=False) + "\n")
            continue
        result = engine.run_task(
            session=session_store.load_session(session["session_id"]),
            repo_path=args.repo,
            task=task,
            approval_policy=args.approval,
            backend=backend,
            approval_callback=_approval_callback_factory(stdin=stdin, stdout=stdout),
        )
        session = result["session"]
        _print_result(result, stdout=stdout)


def _resume(args: argparse.Namespace, *, stdout: Any, stdin: Any) -> int:
    session = session_store.load_session(args.session)
    repo = str(session.get("repo_path") or "")
    if not str(args.task or "").strip():
        stdout.write(json.dumps(session, indent=2, ensure_ascii=False) + "\n")
        return 0
    backend = jeff_backend.create_backend(provider=args.provider, model=args.model)
    result = engine.run_task(
        session=session,
        repo_path=repo,
        task=args.task,
        approval_policy=args.approval,
        backend=backend,
        approval_callback=_approval_callback_factory(stdin=stdin, stdout=stdout),
    )
    _print_result(result, stdout=stdout)
    return 0 if bool((result.get("verification") or {}).get("ok")) else 1


def _undo(args: argparse.Namespace, *, stdout: Any) -> int:
    session = session_store.load_session(args.session)
    result = engine.undo_last_checkpoint(session)
    session_store.save_session(session)
    stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    return 0 if bool(result.get("ok")) else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="jeff")
    sub = parser.add_subparsers(dest="command", required=True)

    def _common(p: argparse.ArgumentParser, *, need_repo: bool = True) -> None:
        if need_repo:
            p.add_argument("--repo", default=DEFAULT_REPO)
        p.add_argument("--approval", default="ask", choices=["ask", "auto", "never"])
        p.add_argument("--provider", default="ollama")
        p.add_argument("--model", default="qwen2.5-coder:14b")

    chat = sub.add_parser("chat")
    _common(chat)
    chat.add_argument("--task", default="")
    chat.add_argument("--session", default="")

    run = sub.add_parser("run")
    _common(run)
    run.add_argument("--task", required=True)
    run.add_argument("--session", default="")

    resume = sub.add_parser("resume")
    _common(resume, need_repo=False)
    resume.add_argument("--session", required=True)
    resume.add_argument("--task", default="")

    undo = sub.add_parser("undo")
    undo.add_argument("--session", required=True)

    diff = sub.add_parser("diff")
    diff.add_argument("--session", required=True)
    return parser


def main(argv: Sequence[str] | None = None, *, stdin: Any = None, stdout: Any = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv if argv is not None else sys.argv[1:]))
    stdin = sys.stdin if stdin is None else stdin
    stdout = sys.stdout if stdout is None else stdout
    if args.command == "chat":
        return _chat(args, stdin=stdin, stdout=stdout)
    if args.command == "run":
        return _run_once(args, stdout=stdout, stdin=stdin)
    if args.command == "resume":
        return _resume(args, stdout=stdout, stdin=stdin)
    if args.command == "undo":
        return _undo(args, stdout=stdout)
    if args.command == "diff":
        return _diff(args, stdout=stdout)
    raise SystemExit(2)
