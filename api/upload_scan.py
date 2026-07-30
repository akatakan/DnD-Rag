from __future__ import annotations

import socket
import struct
import math


class UploadScanError(RuntimeError):
    pass


class MalwareDetected(UploadScanError):
    pass


def scan_with_clamav(
    data: bytes,
    *,
    host: str,
    port: int,
    timeout_seconds: float = 5.0,
) -> None:
    """Scan bytes through ClamAV INSTREAM without writing a temporary file."""
    if (
        not isinstance(data, bytes)
        or not host
        or isinstance(port, bool)
        or not 1 <= port <= 65535
        or not math.isfinite(timeout_seconds)
        or not 0.1 <= timeout_seconds <= 60
    ):
        raise UploadScanError("ClamAV adresi gecersiz.")
    try:
        with socket.create_connection(
            (host, port), timeout=timeout_seconds
        ) as connection:
            connection.settimeout(timeout_seconds)
            connection.sendall(b"zINSTREAM\0")
            for offset in range(0, len(data), 64 * 1024):
                chunk = data[offset : offset + 64 * 1024]
                connection.sendall(struct.pack(">I", len(chunk)))
                connection.sendall(chunk)
            connection.sendall(struct.pack(">I", 0))
            response = bytearray()
            while b"\0" not in response:
                chunk = connection.recv(4096 - len(response))
                if not chunk:
                    raise UploadScanError("ClamAV yaniti tamamlanmadi.")
                response.extend(chunk)
                if len(response) >= 4096 and b"\0" not in response:
                    raise UploadScanError("ClamAV yaniti cok buyuk.")
    except (OSError, TimeoutError) as error:
        raise UploadScanError("ClamAV taramasi kullanilamiyor.") from error
    message = bytes(response).split(b"\0", 1)[0].rstrip(
        b"\r\n"
    ).decode("utf-8", errors="replace")
    if message.endswith(" FOUND"):
        raise MalwareDetected("Yukleme malware taramasindan gecemedi.")
    if not message.endswith(" OK"):
        raise UploadScanError("ClamAV belirsiz yanit verdi.")
