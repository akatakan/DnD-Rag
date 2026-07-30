import struct
import zlib
from pathlib import Path
import os
import tempfile
import threading
from contextlib import contextmanager

from PIL import Image, ImageDraw


_RENDER_LOCKS_GUARD = threading.Lock()
_RENDER_LOCKS: dict[str, tuple[threading.Lock, int]] = {}


@contextmanager
def _target_render_lock(target: Path):
    key = str(target.resolve())
    with _RENDER_LOCKS_GUARD:
        lock, references = _RENDER_LOCKS.get(
            key, (threading.Lock(), 0)
        )
        _RENDER_LOCKS[key] = (lock, references + 1)
    lock.acquire()
    try:
        yield
    finally:
        lock.release()
        with _RENDER_LOCKS_GUARD:
            current_lock, references = _RENDER_LOCKS[key]
            if references == 1:
                del _RENDER_LOCKS[key]
            else:
                _RENDER_LOCKS[key] = (current_lock, references - 1)


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return (
        struct.pack(">I", len(payload))
        + kind
        + payload
        + struct.pack(">I", checksum)
    )


def render_fog_mask(
    columns: int,
    rows: int,
    revealed_cells: set[tuple[int, int]],
) -> bytes:
    if not 1 <= columns <= 8192 or not 1 <= rows <= 8192:
        raise ValueError("Fog mask boyutu gecersiz.")
    scanlines = bytearray()
    for y in range(rows):
        scanlines.append(0)
        for x in range(columns):
            scanlines.extend((0, 0 if (x, y) in revealed_cells else 255))
    header = struct.pack(">IIBBBBB", columns, rows, 8, 4, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(bytes(scanlines), level=6))
        + _chunk(b"IEND", b"")
    )


def render_fogged_map(
    source: Path,
    target: Path,
    grid_size: int,
    revealed_cells: set[tuple[int, int]],
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    # A single process may receive many requests for the same new revision.
    # Serialize each target's recheck/render/replace sequence without making
    # unrelated games wait for one another.
    with _target_render_lock(target):
        if target.is_file():
            return
        temporary_path: Path | None = None
        try:
            with Image.open(source) as opened:
                opened.load()
                image = opened.convert("RGB")
            try:
                with Image.new("1", image.size, 0) as mask:
                    draw = ImageDraw.Draw(mask)
                    for cell_x, cell_y in revealed_cells:
                        left, top = cell_x * grid_size, cell_y * grid_size
                        if left >= image.width or top >= image.height:
                            continue
                        draw.rectangle(
                            (
                                left,
                                top,
                                min(image.width, left + grid_size) - 1,
                                min(image.height, top + grid_size) - 1,
                            ),
                            fill=1,
                        )
                    with Image.new("RGB", image.size) as darkness:
                        fogged = Image.composite(image, darkness, mask)
            finally:
                image.close()
            try:
                with tempfile.NamedTemporaryFile(
                    dir=target.parent,
                    prefix=".fog-",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_path = Path(temporary.name)
                fogged.save(temporary_path, format="PNG", optimize=True)
            finally:
                fogged.close()
            # Windows rejects fsync on a read-only descriptor.
            with temporary_path.open("rb+") as saved:
                saved.flush()
                os.fsync(saved.fileno())
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
