import asyncio
import threading
import time
from collections import defaultdict, deque
from typing import Protocol


class RateLimitBackend(Protocol):
    def check(
        self, key: str, limit: int, window_seconds: float
    ) -> float | None: ...

    def close(self) -> None: ...


class RateLimiter:
    """Small process-local sliding-window limiter for the local/LAN server."""

    def __init__(self, backend: RateLimitBackend | None = None):
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._checks = 0
        self._backend = backend

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> float | None:
        if self._backend is not None:
            return self._backend.check(key, limit, window_seconds)
        current = time.monotonic()
        cutoff = current - window_seconds
        with self._lock:
            self._checks += 1
            if self._checks % 256 == 0:
                stale = [
                    bucket
                    for bucket, timestamps in self._hits.items()
                    if not timestamps or timestamps[-1] <= cutoff
                ]
                for bucket in stale:
                    del self._hits[bucket]
            hits = self._hits[key]
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if len(hits) >= limit:
                return max(0.001, hits[0] + window_seconds - current)
            hits.append(current)
            return None

    async def check_async(
        self,
        key: str,
        limit: int,
        window_seconds: float = 60.0,
    ) -> float | None:
        if self._backend is not None:
            return await asyncio.to_thread(
                self._backend.check, key, limit, window_seconds
            )
        return self.check(key, limit, window_seconds)

    def clear(self) -> None:
        if self._backend is not None:
            return
        with self._lock:
            self._hits.clear()
            self._checks = 0

    def close(self) -> None:
        if self._backend is not None:
            self._backend.close()
