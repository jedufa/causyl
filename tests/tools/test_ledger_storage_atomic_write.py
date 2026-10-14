from __future__ import annotations

import os
import threading
from pathlib import Path

from infra.webui.ledger import ledger_storage


def test_atomic_write_text_uses_per_writer_temp_paths(tmp_path: Path, monkeypatch) -> None:
    target = tmp_path / "state.json"
    original_replace = os.replace
    barrier = threading.Barrier(2)
    replace_sources: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def delayed_replace(src: str | os.PathLike[str], dst: str | os.PathLike[str]) -> None:
        with lock:
            replace_sources.append(os.fspath(src))
        barrier.wait(timeout=5)
        original_replace(src, dst)

    monkeypatch.setattr(ledger_storage.os, "replace", delayed_replace)

    def write(value: str) -> None:
        try:
            ledger_storage.atomic_write_text(target, value)
        except BaseException as exc:  # pragma: no cover - re-raised below for clearer thread failure
            errors.append(exc)

    threads = [threading.Thread(target=write, args=(value,)) for value in ("one", "two")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert len(replace_sources) == 2
    assert replace_sources[0] != replace_sources[1]
    assert target.read_text(encoding="utf-8") in {"one\n", "two\n"}
