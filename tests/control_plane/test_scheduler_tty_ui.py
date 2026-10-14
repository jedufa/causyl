from __future__ import annotations

from tools.bin import scheduler_tty_ui as ui


def test_menu_tokens_starts_with_numbers_then_letters() -> None:
    assert ui.menu_tokens(3) == ["1", "2", "3"]
    assert ui.menu_tokens(12)[9:] == ["a", "b", "c"]


def test_prompt_menu_uses_default_on_empty_fallback(monkeypatch) -> None:
    monkeypatch.setattr(ui, "tty_single_key_supported", lambda: False)
    result = ui.prompt_menu(
        "Mode",
        choices=["implementation", "decompose_parent"],
        default_index=1,
        fallback_reader=lambda _prompt: "",
    )
    assert result == "decompose_parent"


def test_prompt_menu_accepts_numeric_fallback(monkeypatch) -> None:
    monkeypatch.setattr(ui, "tty_single_key_supported", lambda: False)
    result = ui.prompt_menu(
        "Mode",
        choices=["implementation", "decompose_parent"],
        default_index=0,
        fallback_reader=lambda _prompt: "2",
    )
    assert result == "decompose_parent"


def test_prompt_yes_no_uses_default_on_empty_fallback(monkeypatch) -> None:
    monkeypatch.setattr(ui, "tty_single_key_supported", lambda: False)
    assert ui.prompt_yes_no("Apply", default=True, fallback_reader=lambda _prompt: "") is True
    assert ui.prompt_yes_no("Apply", default=False, fallback_reader=lambda _prompt: "") is False


def test_prompt_yes_no_accepts_single_letter_fallback(monkeypatch) -> None:
    monkeypatch.setattr(ui, "tty_single_key_supported", lambda: False)
    assert ui.prompt_yes_no("Apply", default=False, fallback_reader=lambda _prompt: "y") is True
    assert ui.prompt_yes_no("Apply", default=True, fallback_reader=lambda _prompt: "n") is False


def test_prompt_menu_large_list_falls_back_to_numbered_line_input(monkeypatch) -> None:
    monkeypatch.setattr(ui, "tty_single_key_supported", lambda: True)
    choices = [f"item-{idx}" for idx in range(1, 51)]
    result = ui.prompt_menu(
        "Large menu",
        choices=choices,
        default_index=0,
        fallback_reader=lambda _prompt: "12",
    )
    assert result == "item-12"
