#!/usr/bin/env python3
from __future__ import annotations

import os
import string
import sys
from typing import Callable, Iterable, List, Sequence, TypeVar

try:
    import termios
    import tty
except Exception:  # pragma: no cover
    termios = None  # type: ignore[assignment]
    tty = None  # type: ignore[assignment]


T = TypeVar("T")


class TtyPromptAbort(Exception):
    pass


def tty_single_key_supported() -> bool:
    if str(os.environ.get("SCHEDULER_SINGLE_KEY_MENUS") or "").strip().lower() not in {"1", "true", "yes", "on"}:
        return False
    if termios is None or tty is None:
        return False
    stream = sys.stdin
    if not bool(getattr(stream, "isatty", lambda: False)()):
        return False
    return callable(getattr(stream, "fileno", None))


def menu_tokens(count: int) -> List[str]:
    alphabet = list(string.digits[1:] + string.ascii_lowercase)
    if count < 1:
        return []
    if count > len(alphabet):
        raise TtyPromptAbort(f"menu too large for single-key selection: {count}")
    return alphabet[:count]


def _fallback_reader_or_input(fallback_reader: Callable[[str], str] | None) -> Callable[[str], str]:
    return fallback_reader or input


def read_menu_key(
    *,
    fallback_reader: Callable[[str], str] | None = None,
    fallback_prompt: str = "",
) -> str:
    if not tty_single_key_supported():
        if fallback_reader is None:
            raise TtyPromptAbort("no fallback reader available")
        return str(fallback_reader(fallback_prompt) or "").strip().lower()
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        chars = sys.stdin.read(1)
    except KeyboardInterrupt as exc:
        raise TtyPromptAbort("canceled by user") from exc
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)
    if chars in {"\x03", "\x1b"}:
        raise TtyPromptAbort("canceled by user")
    if chars in {"\r", "\n"}:
        return ""
    if chars:
        print(chars)
    return chars.strip().lower()


def prompt_menu(
    label: str,
    *,
    choices: Sequence[T],
    render: Callable[[T], str] | None = None,
    default_index: int = 0,
    fallback_reader: Callable[[str], str] | None = None,
) -> T:
    items = list(choices)
    if not items:
        raise TtyPromptAbort("menu choices are empty")
    if default_index < 0 or default_index >= len(items):
        default_index = 0
    render_item = render or (lambda item: str(item))
    if len(items) > len(string.digits[1:] + string.ascii_lowercase):
        reader = _fallback_reader_or_input(fallback_reader)
        print(label)
        for idx, item in enumerate(items, start=1):
            marker = " (default)" if idx - 1 == default_index else ""
            print(f"  {idx}. {render_item(item)}{marker}")
        while True:
            raw = str(reader(f"Selection [{default_index + 1}]: ") or "").strip()
            if not raw:
                return items[default_index]
            if raw.isdigit():
                idx = int(raw) - 1
                if 0 <= idx < len(items):
                    return items[idx]
            print(f"Choose a number between 1 and {len(items)}.", file=sys.stderr)
    tokens = menu_tokens(len(items))
    default_token = tokens[default_index]
    print(label)
    for token, item in zip(tokens, items):
        marker = " (default)" if token == default_token else ""
        print(f"  {token}. {render_item(item)}{marker}")
    if tty_single_key_supported():
        print(f"Select one key [{default_token}]: ", end="", flush=True)
    while True:
        raw = read_menu_key(fallback_reader=fallback_reader, fallback_prompt="")
        token = raw or default_token
        for item_token, item in zip(tokens, items):
            if token == item_token:
                return item
        print(f"Choose one of: {', '.join(tokens)}.", file=sys.stderr)
        if tty_single_key_supported():
            print(f"Select one key [{default_token}]: ", end="", flush=True)


def prompt_yes_no(
    label: str,
    *,
    default: bool,
    fallback_reader: Callable[[str], str] | None = None,
) -> bool:
    yes_token = "y"
    no_token = "n"
    default_token = yes_token if default else no_token
    if tty_single_key_supported():
        suffix = "[Y/n]" if default else "[y/N]"
        print(f"{label} {suffix}: ", end="", flush=True)
        while True:
            raw = read_menu_key(fallback_reader=fallback_reader, fallback_prompt="")
            token = (raw or default_token).lower()
            if token == yes_token:
                return True
            if token == no_token:
                return False
            print(f"Choose one of: {yes_token}, {no_token}.", file=sys.stderr)
            print(f"{label} {suffix}: ", end="", flush=True)
    if fallback_reader is None:
        raise TtyPromptAbort("no fallback reader available")
    suffix = "Y/n" if default else "y/N"
    while True:
        raw = str(fallback_reader(f"{label} [{suffix}]: ") or "").strip().lower()
        if not raw:
            return default
        if raw in {"y", "yes"}:
            return True
        if raw in {"n", "no"}:
            return False
        print("Please answer yes or no.", file=sys.stderr)
