from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Optional, Sequence

try:
    import readline as _readline
except Exception:  # pragma: no cover - platform without readline
    _readline = None

from infra.bin import lab
from infra.bin import lab_e2e
from infra.bin.lab_transcript import append_system_note, append_turn, end_session, ensure_session_started, new_session_id

_DEFAULT_PROMPT_PLAIN = "lab> "
_DEFAULT_PROMPT_COLORED = "\001\033[1;36m\002lab>\001\033[0m\002 "


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Interactive shell for the plane.")
    parser.add_argument("--prompt", default=_DEFAULT_PROMPT_PLAIN, help="Shell prompt text.")
    return parser


def _is_exit_command(line: str) -> bool:
    return str(line or "").strip().lower() in {"exit", "quit"}


def _is_help_command(line: str) -> bool:
    lowered = str(line or "").strip().lower()
    return lowered in {"help", "--help", "-h"} or lowered.startswith("help ")


def _is_reload_command(line: str) -> bool:
    return str(line or "").strip().lower() in {"source", "reload", "reexec"}


def _split_chained_command(line: str) -> list[str]:
    text = str(line or "").strip()
    lowered = text.lower()
    if lowered.startswith("do "):
        text = text[3:].strip()
    parts = [part.strip() for part in lab._split_chained_prompt(text) if part.strip()]
    return parts or ([text] if text else [])


def _is_e2e_command(text: str) -> bool:
    return str(text or "").strip().lower().startswith("e2e ")


def _scheduler_helper_target(parts: Sequence[str]) -> list[str]:
    tokens = [str(part or "").strip() for part in parts if str(part or "").strip()]
    if not tokens or tokens[0].lower() != "scheduler":
        return []
    repo_root = lab._repo_root()
    helper_root = (repo_root / "infra" / "user").resolve()
    if len(tokens) == 1:
        return ["bash", str((helper_root / "scheduler").resolve())]
    helper = tokens[1].lower().replace("_", "-")
    helper_path = (helper_root / f"scheduler-{helper}").resolve()
    if helper_path.exists() and helper_path.is_file():
        return ["bash", str(helper_path), *tokens[2:]]
    return []


def _run_scheduler_helper(text: str) -> tuple[int, str]:
    parts = shlex.split(text)
    cmd = _scheduler_helper_target(parts)
    if not cmd:
        return 2, ""
    proc = subprocess.run(cmd, cwd=str(lab._repo_root()))
    return int(proc.returncode), " ".join(shlex.quote(part) for part in cmd)


def _run_e2e_command(text: str) -> tuple[int, dict, str]:
    parts = shlex.split(text)
    if not parts or parts[0].lower() != "e2e":
        return 2, {}, ""
    args = lab_e2e.build_parser().parse_args(parts[1:])
    result = lab_e2e.run_suite(
        family=str(args.family),
        scenario=str(args.scenario or "").strip(),
        run_all=bool(args.all),
        fast=bool(args.fast),
        full=bool(args.full),
    )
    rendered = json.dumps(result, indent=2, sort_keys=True) if bool(args.json) else lab_e2e.render_suite_text(result)
    print(rendered)
    return (0 if bool(result.get("ok")) else 1), result, rendered


def _history_path(repo_root: Path) -> Path:
    return (Path(repo_root).resolve() / "governance" / "agents" / "runtime" / "labsh_history.txt").resolve()


def _load_history(repo_root: Path) -> None:
    if _readline is None:
        return
    path = _history_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _readline.parse_and_bind("set editing-mode emacs")
        _readline.parse_and_bind("set enable-keypad on")
        _readline.parse_and_bind("set bell-style none")
    except Exception:
        pass
    try:
        if path.exists():
            _readline.read_history_file(str(path))
    except Exception:
        return
    try:
        _readline.set_history_length(1000)
    except Exception:
        pass


def _save_history(repo_root: Path) -> None:
    if _readline is None:
        return
    path = _history_path(repo_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _readline.write_history_file(str(path))
    except Exception:
        return


def _remember_history_line(text: str) -> None:
    if _readline is None:
        return
    line = str(text or "").strip()
    if not line:
        return
    try:
        length = _readline.get_current_history_length()
        if length > 0:
            previous = _readline.get_history_item(length)
            if str(previous or "").strip() == line:
                return
        _readline.add_history(line)
    except Exception:
        return


def _reload_shell(argv: Sequence[str]) -> None:
    os.execv(sys.executable, [sys.executable, "-m", "infra.bin.labsh", *list(argv)])


def main(argv: Optional[Sequence[str]] = None) -> int:
    shell_argv = list(argv) if argv is not None else []
    args = build_parser().parse_args(shell_argv)
    prompt = str(args.prompt or _DEFAULT_PROMPT_PLAIN)
    if prompt == _DEFAULT_PROMPT_PLAIN and sys.stdin.isatty() and sys.stdout.isatty():
        prompt = _DEFAULT_PROMPT_COLORED
    repo_root = lab._repo_root()
    session_id = str(os.environ.get("EIEL_LAB_SESSION_ID") or os.environ.get("EIEL_LABSH_SESSION_ID") or "").strip() or new_session_id()
    os.environ["EIEL_LAB_SESSION_ID"] = session_id
    os.environ["EIEL_LABSH_SESSION_ID"] = session_id
    os.environ["EIEL_LAB_SESSION_MODE"] = "interactive"
    ensure_session_started(repo_root, session_id, mode="interactive", cwd=os.getcwd())
    _load_history(repo_root)
    while True:
        try:
            line = input(prompt)
        except EOFError:
            _save_history(repo_root)
            end_session(repo_root, session_id, reason="eof")
            sys.stdout.write("\n")
            return 0
        except KeyboardInterrupt:
            _save_history(repo_root)
            end_session(repo_root, session_id, reason="shell_canceled")
            sys.stdout.write("\n[the-plane] Shell canceled.\n")
            sys.stdout.flush()
            return 130
        text = str(line or "").strip()
        if not text:
            continue
        _remember_history_line(text)
        if _is_exit_command(text):
            _save_history(repo_root)
            end_session(repo_root, session_id, reason="operator_exit")
            return 0
        if _is_help_command(text):
            topic = lab._help_topic(text)
            if topic:
                lab._print_help_topic(topic)
            else:
                lab._print_help(lab.build_parser())
                print("")
                print('Inside labsh, type requests directly without the leading "lab".')
                print('Built-in E2E command: e2e fcee [--fast|--full|--all|--scenario NAME]')
                print('Type "source" to reload labsh after updates.')
                print('Use "exit" or "quit" to leave.')
            append_system_note(repo_root, session_id, note='Displayed labsh help.', phase="commentary")
            continue
        if _is_reload_command(text):
            print("[the-plane] Reloading labsh...")
            sys.stdout.flush()
            append_system_note(repo_root, session_id, note="Reloading labsh.", phase="commentary")
            _save_history(repo_root)
            _reload_shell(shell_argv)
        commands = _split_chained_command(text)
        for index, command_text in enumerate(commands, start=1):
            if len(commands) > 1:
                print(f"[{index}/{len(commands)}] {command_text}")
            try:
                if _is_e2e_command(command_text):
                    rc, e2e_result, rendered = _run_e2e_command(command_text)
                    append_turn(
                        repo_root,
                        session_id,
                        request_text=command_text,
                        payload={"request_text": command_text, "command_family": "e2e"},
                        result={
                            "supported": bool(e2e_result),
                            "selection": {"action_id": f"e2e::{str(e2e_result.get('family') or '').strip()}"},
                            "summary": str(e2e_result.get("family") or "").strip(),
                            "terminal_class": "success_deterministic" if bool(e2e_result.get("ok")) else "failed_with_known_gap",
                            "operator_surface": {
                                "codex_transparency": {"codex_used": False},
                            },
                        },
                        rendered_text=rendered,
                    )
                elif _scheduler_helper_target(shlex.split(command_text)):
                    rc, rendered = _run_scheduler_helper(command_text)
                    append_turn(
                        repo_root,
                        session_id,
                        request_text=command_text,
                        payload={"request_text": command_text, "command_family": "scheduler_helper"},
                        result={
                            "supported": True,
                            "selection": {"action_id": "scheduler_helper"},
                            "summary": rendered,
                            "terminal_class": "success_deterministic" if rc == 0 else "failed_with_known_gap",
                            "operator_surface": {
                                "codex_transparency": {"codex_used": False},
                            },
                        },
                        rendered_text=rendered,
                    )
                else:
                    rc = lab.main(shlex.split(command_text))
            except KeyboardInterrupt:
                print("[the-plane] Request canceled.")
                append_system_note(repo_root, session_id, note="Request canceled.", phase="commentary")
                _save_history(repo_root)
                break
            except ValueError as exc:
                print(f"Parse error: {exc}", file=sys.stderr)
                rc = 2
            if rc not in {0, None}:
                # Keep the shell alive after request-level failures.
                _save_history(repo_root)
                break


if __name__ == "__main__":
    raise SystemExit(main())
