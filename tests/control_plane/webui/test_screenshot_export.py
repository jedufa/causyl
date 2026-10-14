"""The gallery exception must never admit arbitrary images or hidden metadata."""
import struct
import zlib
from pathlib import Path

import pytest

from tools.release.export_source import allowed_path
from tools.release.screenshot_policy import SCREENSHOTS, validate_png, validate_screenshot_set


def chunk(kind, data):
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)


def png(*, metadata=False, width=320):
    header = chunk(b"IHDR", struct.pack(">IIBBBBB", width, 240, 8, 2, 0, 0, 0))
    extra = chunk(b"tEXt", b"Comment\0private fixture") if metadata else b""
    pixels = zlib.compress((b"\0" + bytes(320 * 3)) * 240)
    return b"\x89PNG\r\n\x1a\n" + header + extra + chunk(b"IDAT", pixels) + chunk(b"IEND", b"")


def test_only_exact_screenshot_names_are_admitted():
    assert all(allowed_path(name) for name in SCREENSHOTS)
    assert not allowed_path("docs/screenshots/unreviewed.png")
    assert not allowed_path("docs/screenshots/change-ledger.jpg")
    assert not allowed_path("private/change-ledger.png")
    assert not allowed_path("docs/screenshots/../change-ledger.png")


@pytest.mark.parametrize("data", [b"not an image", png(metadata=True), png(width=50000),
    png()[:-3], png() + b"private trailer", png()[:-15] + bytes(15), bytes(4 * 1024 * 1024)])
def test_metadata_truncation_corruption_and_oversize_are_rejected(data):
    with pytest.raises((ValueError, zlib.error)):
        validate_png(data)


def test_valid_bounded_png_and_complete_real_capture_provenance():
    assert validate_png(png()) == {"width": 320, "height": 240}
    root = Path(__file__).resolve().parents[3]
    validate_screenshot_set(root, set((root / "tools/release/source_allowlist.txt").read_text().splitlines()))


def test_partial_gallery_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="exact screenshot set"):
        validate_screenshot_set(tmp_path, {next(iter(SCREENSHOTS))})
