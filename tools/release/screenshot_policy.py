"""Exact screenshot exceptions; all other binary source inputs remain forbidden."""
import hashlib
import json
import struct
import zlib

SCREENSHOTS = frozenset({
    "docs/screenshots/change-ledger.png",
    "docs/screenshots/agent-surface.png",
    "docs/screenshots/operator-mobile.png",
})
PROVENANCE = "docs/screenshots/CAPTURE_PROVENANCE.json"
MAX_BYTES = 3 * 1024 * 1024


def validate_png(data):
    if len(data) > MAX_BYTES or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("invalid or oversized screenshot PNG")
    pos, chunks, compressed, dimensions = 8, [], bytearray(), None
    while pos < len(data):
        if pos + 12 > len(data):
            raise ValueError("truncated PNG chunk")
        size, kind = struct.unpack(">I4s", data[pos:pos + 8])
        end = pos + size + 12
        if end > len(data) or kind not in {b"IHDR", b"IDAT", b"IEND"}:
            raise ValueError("PNG metadata or unsupported chunk")
        body = data[pos + 8:end - 4]
        if zlib.crc32(kind + body) & 0xffffffff != struct.unpack(">I", data[end - 4:end])[0]:
            raise ValueError("PNG checksum mismatch")
        if kind == b"IHDR":
            if chunks or len(body) != 13:
                raise ValueError("invalid PNG header")
            width, height, bits, color, comp, filtering, interlace = struct.unpack(">IIBBBBB", body)
            if not (320 <= width <= 2000 and 240 <= height <= 2000 and bits == 8
                    and color in {2, 6} and comp == filtering == interlace == 0):
                raise ValueError("unsupported screenshot dimensions or encoding")
            dimensions = (width, height, 3 if color == 2 else 4)
        elif kind == b"IDAT":
            if dimensions is None or b"IEND" in chunks:
                raise ValueError("PNG chunk order")
            compressed.extend(body)
        elif body or end != len(data) or not compressed:
            raise ValueError("invalid PNG end")
        chunks.append(kind)
        pos = end
    if not dimensions or chunks[-1] != b"IEND":
        raise ValueError("incomplete PNG")
    width, height, channels = dimensions
    expected = height * (1 + width * channels)
    decoder = zlib.decompressobj()
    pixels = decoder.decompress(compressed, expected + 1)
    if len(pixels) != expected or not decoder.eof or decoder.unused_data:
        raise ValueError("invalid or oversized PNG pixel data")
    if any(pixels[offset] > 4 for offset in range(0, expected, 1 + width * channels)):
        raise ValueError("invalid PNG row filter")
    return {"width": width, "height": height}


def validate_screenshot_set(root, names):
    admitted = SCREENSHOTS.intersection(names)
    if not admitted:
        return
    if admitted != SCREENSHOTS or PROVENANCE not in names:
        raise ValueError("gallery requires the exact screenshot set and provenance")
    for name in admitted | {PROVENANCE}:
        path = root / name
        if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
            raise ValueError("unsafe screenshot path")
    receipt = json.loads((root / PROVENANCE).read_text())
    if (receipt.get("mode") != "isolated_real_ui_synthetic_responses"
            or receipt.get("network_requests_sent") != 0
            or receipt.get("operator_service_started") is not False
            or receipt.get("provider_or_agent_calls") != 0):
        raise ValueError("invalid screenshot capture boundary")
    rows = receipt.get("screenshots", [])
    if len(rows) != 3 or {row["path"] for row in rows} != SCREENSHOTS:
        raise ValueError("screenshot provenance membership mismatch")
    for row in rows:
        data = (root / row["path"]).read_bytes()
        dimensions = validate_png(data)
        if row.get("sha256") != hashlib.sha256(data).hexdigest() or any(row.get(k) != v for k, v in dimensions.items()):
            raise ValueError("screenshot provenance hash or dimensions mismatch")
    assets = receipt.get("source_assets", [])
    if not assets or receipt.get("capture_script") != "tools/release/capture_ui.cjs":
        raise ValueError("capture input provenance missing")
    for row in assets + [{"path": receipt["capture_script"], "sha256": receipt.get("capture_script_sha256")}]:
        name = row["path"]
        path = root / name
        if (name not in names or path.is_symlink()
                or not path.resolve().is_relative_to(root.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != row.get("sha256")):
            raise ValueError("capture source input changed or missing")
