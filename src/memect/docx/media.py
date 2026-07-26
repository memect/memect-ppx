"""Image loading and dimension inference."""

from __future__ import annotations

from pathlib import Path
from struct import unpack

from .errors import UnsupportedImageError
from .model import Picture
from .units import Length, ensure_length, inch, px

_CONTENT_TYPES = {
    "gif": "image/gif",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
}


def load_picture(
    source: str | Path | bytes,
    *,
    image_id: int,
    width: Length | int | float | None = None,
    height: Length | int | float | None = None,
    alt_text: str = "",
) -> Picture:
    if isinstance(source, bytes):
        data = source
        source_name = "image"
        suffix = ""
    else:
        path = Path(source)
        data = path.read_bytes()
        source_name = path.stem or f"image{image_id}"
        suffix = path.suffix.lower().lstrip(".")

    ext, content_type, pixel_size = _identify_image(data, suffix=suffix)
    width_emu, height_emu = _resolve_size(width, height, pixel_size)
    base_name = _safe_name(source_name) or "image"
    return Picture(
        name=f"{base_name}{image_id}.{ext}",
        content_type=content_type,
        data=data,
        width_emu=width_emu,
        height_emu=height_emu,
        alt_text=alt_text,
        image_id=image_id,
    )


def _identify_image(data: bytes, *, suffix: str = "") -> tuple[str, str, tuple[int, int] | None]:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        if len(data) < 24:
            raise UnsupportedImageError("Invalid PNG image data")
        width, height = unpack(">II", data[16:24])
        return "png", "image/png", (width, height)

    if data.startswith(b"\xff\xd8"):
        size = _jpeg_size(data)
        return "jpg", "image/jpeg", size

    if data[:6] in (b"GIF87a", b"GIF89a"):
        if len(data) < 10:
            raise UnsupportedImageError("Invalid GIF image data")
        width, height = unpack("<HH", data[6:10])
        return "gif", "image/gif", (width, height)

    normalized = "jpg" if suffix == "jpeg" else suffix
    if normalized in _CONTENT_TYPES:
        return normalized, _CONTENT_TYPES[normalized], None

    raise UnsupportedImageError("Only PNG, JPEG, and GIF images are supported")


def _jpeg_size(data: bytes) -> tuple[int, int] | None:
    pos = 2
    sof_markers = {
        0xC0,
        0xC1,
        0xC2,
        0xC3,
        0xC5,
        0xC6,
        0xC7,
        0xC9,
        0xCA,
        0xCB,
        0xCD,
        0xCE,
        0xCF,
    }
    while pos < len(data):
        while pos < len(data) and data[pos] == 0xFF:
            pos += 1
        if pos >= len(data):
            break
        marker = data[pos]
        pos += 1
        if marker in (0xD8, 0xD9):
            continue
        if pos + 2 > len(data):
            break
        segment_len = int.from_bytes(data[pos : pos + 2], "big")
        if segment_len < 2 or pos + segment_len > len(data):
            break
        if marker in sof_markers and segment_len >= 7:
            height = int.from_bytes(data[pos + 3 : pos + 5], "big")
            width = int.from_bytes(data[pos + 5 : pos + 7], "big")
            return width, height
        pos += segment_len
    return None


def _resolve_size(
    width: Length | int | float | None,
    height: Length | int | float | None,
    pixel_size: tuple[int, int] | None,
) -> tuple[int, int]:
    width_len = ensure_length(width, default_unit="in")
    height_len = ensure_length(height, default_unit="in")

    if width_len is None and height_len is None:
        if pixel_size is None:
            width_len = inch(4)
            height_len = inch(3)
        else:
            width_px, height_px = pixel_size
            width_len = px(width_px)
            height_len = px(height_px)
    elif width_len is None:
        assert height_len is not None
        if pixel_size is None:
            width_len = height_len
        else:
            width_px, height_px = pixel_size
            width_len = Length(height_len.inches() * width_px / height_px, "in")
    elif height_len is None:
        if pixel_size is None:
            height_len = width_len
        else:
            width_px, height_px = pixel_size
            height_len = Length(width_len.inches() * height_px / width_px, "in")

    return width_len.emu(), height_len.emu()


def _safe_name(value: str) -> str:
    chars = []
    for char in value:
        if char.isascii() and (char.isalnum() or char in ("-", "_")):
            chars.append(char)
    return "".join(chars)
