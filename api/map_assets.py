import hashlib
import os
from pathlib import Path
import re
import tempfile
import zlib


class MapAssetError(ValueError):
    pass


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
JPEG_SOF_MARKERS = {
    0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
    0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
}
STORAGE_KEY = re.compile(r"^[0-9a-f]{64}\.(png|jpg)$")


def _png_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 33 or not data.startswith(PNG_SIGNATURE):
        return None
    offset = len(PNG_SIGNATURE)
    dimensions: tuple[int, int] | None = None
    saw_image_data = False
    saw_end = False
    chunk_index = 0
    while offset + 12 <= len(data):
        chunk_length = int.from_bytes(data[offset:offset + 4], "big")
        chunk_type = data[offset + 4:offset + 8]
        chunk_end = offset + 12 + chunk_length
        if chunk_end > len(data):
            raise MapAssetError("PNG chunk yapisi gecersiz.")
        chunk_data = data[offset + 8:offset + 8 + chunk_length]
        expected_crc = int.from_bytes(
            data[offset + 8 + chunk_length:chunk_end], "big"
        )
        if zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF != expected_crc:
            raise MapAssetError("PNG CRC dogrulamasi basarisiz.")
        if chunk_index == 0:
            if chunk_type != b"IHDR" or chunk_length != 13:
                raise MapAssetError("PNG IHDR gecersiz.")
            dimensions = (
                int.from_bytes(chunk_data[0:4], "big"),
                int.from_bytes(chunk_data[4:8], "big"),
            )
        elif chunk_type == b"IHDR":
            raise MapAssetError("PNG birden fazla IHDR iceremez.")
        if chunk_type == b"IDAT":
            saw_image_data = True
        if chunk_type == b"IEND":
            if chunk_length != 0 or chunk_end != len(data):
                raise MapAssetError("PNG IEND gecersiz.")
            saw_end = True
            break
        offset = chunk_end
        chunk_index += 1
    if dimensions is None or not saw_image_data or not saw_end:
        raise MapAssetError("PNG dosyasi eksik veya gecersiz.")
    return dimensions


def _jpeg_dimensions(data: bytes) -> tuple[int, int] | None:
    if len(data) < 4 or data[:2] != b"\xff\xd8":
        return None
    if not data.endswith(b"\xff\xd9"):
        raise MapAssetError("JPEG dosyasi tamamlanmamis.")
    offset = 2
    dimensions: tuple[int, int] | None = None
    while offset + 4 <= len(data):
        if data[offset] != 0xFF:
            offset += 1
            continue
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker in {0xD8, 0xD9}:
            continue
        if offset + 2 > len(data):
            break
        segment_length = int.from_bytes(data[offset:offset + 2], "big")
        if segment_length < 2 or offset + segment_length > len(data):
            raise MapAssetError("JPEG segment yapisi gecersiz.")
        if marker == 0xDA:
            if dimensions is None:
                raise MapAssetError("JPEG boyut bilgisi bulunamadi.")
            return dimensions
        if marker in JPEG_SOF_MARKERS:
            if segment_length < 7:
                raise MapAssetError("JPEG SOF gecersiz.")
            height = int.from_bytes(data[offset + 3:offset + 5], "big")
            width = int.from_bytes(data[offset + 5:offset + 7], "big")
            dimensions = (width, height)
        offset += segment_length
    raise MapAssetError("JPEG scan verisi bulunamadi.")


def validate_map_image(
    data: bytes, declared_content_type: str, maximum_bytes: int
) -> dict:
    if not data:
        raise MapAssetError("Harita dosyasi bos olamaz.")
    if len(data) > maximum_bytes:
        raise MapAssetError("Harita dosyasi boyut limitini asti.")
    dimensions = _png_dimensions(data)
    if dimensions is not None:
        content_type, extension = "image/png", "png"
    else:
        dimensions = _jpeg_dimensions(data)
        if dimensions is None:
            raise MapAssetError("Yalnizca PNG veya JPEG harita desteklenir.")
        content_type, extension = "image/jpeg", "jpg"
    normalized_declared = declared_content_type.split(";", 1)[0].strip().lower()
    allowed_declared = (
        {content_type, "image/jpg"} if content_type == "image/jpeg"
        else {content_type}
    )
    if normalized_declared not in allowed_declared:
        raise MapAssetError("Dosya icerigi ile Content-Type uyusmuyor.")
    width, height = dimensions
    if not 64 <= width <= 8192 or not 64 <= height <= 8192:
        raise MapAssetError("Harita boyutu 64..8192 piksel arasinda olmali.")
    if width * height > 64_000_000:
        raise MapAssetError("Harita toplam piksel limitini asti.")
    digest = hashlib.sha256(data).hexdigest()
    return {
        "sha256": digest,
        "content_type": content_type,
        "extension": extension,
        "width": width,
        "height": height,
        "byte_size": len(data),
    }


class LocalMapObjectStore:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put(self, data: bytes, sha256: str, extension: str) -> str:
        storage_key = f"{sha256}.{extension}"
        if not STORAGE_KEY.fullmatch(storage_key):
            raise MapAssetError("Harita storage key gecersiz.")
        target = (self.root / storage_key).resolve()
        if target.parent != self.root:
            raise MapAssetError("Harita storage yolu gecersiz.")
        if target.exists():
            if target.stat().st_size != len(data):
                raise MapAssetError("Content-addressed harita boyutu uyusmuyor.")
            if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
                raise MapAssetError("Content-addressed harita icerigi bozulmus.")
            return storage_key
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                dir=self.root, prefix=".map-", suffix=".tmp", delete=False
            ) as temporary:
                temporary.write(data)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, target)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return storage_key

    def path(self, storage_key: str) -> Path:
        if not STORAGE_KEY.fullmatch(storage_key):
            raise MapAssetError("Harita storage key gecersiz.")
        target = (self.root / storage_key).resolve()
        if target.parent != self.root or not target.is_file():
            raise MapAssetError("Harita dosyasi bulunamadi.")
        return target

    def delete_if_exists(self, storage_key: str) -> bool:
        if not STORAGE_KEY.fullmatch(storage_key):
            raise MapAssetError("Harita storage key gecersiz.")
        target = (self.root / storage_key).resolve()
        if target.parent != self.root:
            raise MapAssetError("Harita storage yolu gecersiz.")
        try:
            target.unlink()
            return True
        except FileNotFoundError:
            return False
