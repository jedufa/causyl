from __future__ import annotations

from pathlib import Path
import threading

from tools.shared import fsio


def test_fsio_round_trips_json_and_jsonl(tmp_path: Path) -> None:
    json_path = tmp_path / "state.json"
    rows_path = tmp_path / "events.jsonl"

    fsio.write_json_atomic(json_path, {"ok": True, "count": 2})
    fsio.append_jsonl(rows_path, {"event": "one"})
    fsio.append_jsonl(rows_path, {"event": "two"})

    assert fsio.read_json_dict(json_path) == {"count": 2, "ok": True}
    assert fsio.read_jsonl(rows_path) == [{"event": "one"}, {"event": "two"}]


def test_fsio_strict_read_rejects_non_object_json(tmp_path: Path) -> None:
    path = tmp_path / "bad.json"
    path.write_text('["not-an-object"]\n', encoding="utf-8")

    try:
        fsio.read_json_dict_strict(path)
    except ValueError as exc:
        assert "expected JSON object" in str(exc)
    else:
        raise AssertionError("expected strict JSON read to fail")


def test_fsio_write_json_atomic_tolerates_concurrent_writers(tmp_path: Path) -> None:
    path = tmp_path / "shared.json"
    errors: list[Exception] = []

    def _writer(idx: int) -> None:
        try:
            for n in range(50):
                fsio.write_json_atomic(path, {"writer": idx, "n": n})
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=_writer, args=(idx,)) for idx in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert errors == []
    payload = fsio.read_json_dict(path)
    assert set(payload.keys()) == {"writer", "n"}
