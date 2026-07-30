from __future__ import annotations

import asyncio
import hashlib
import json
import re
import secrets
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any


MessageCallback = Callable[[dict[str, Any]], Awaitable[None]]
GraceCallback = Callable[[str, str], Awaitable[bool]]
_NAMESPACE_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _validate_namespace(namespace: str) -> str:
    if not _NAMESPACE_PATTERN.fullmatch(namespace):
        raise ValueError(
            "Redis namespace harf/rakamla baslayan 1-64 "
            "harf, rakam, nokta, tire veya alt cizgi olmali."
        )
    return namespace


class RedisRateLimitBackend:
    """Atomic, Redis TIME based sliding-window rate limits."""

    _SCRIPT = """
local current = redis.call('TIME')
local now_ms = (current[1] * 1000) + math.floor(current[2] / 1000)
local cutoff = now_ms - tonumber(ARGV[1])
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', cutoff)
local count = redis.call('ZCARD', KEYS[1])
if count >= tonumber(ARGV[2]) then
  local oldest = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
  return math.max(1, tonumber(oldest[2]) + tonumber(ARGV[1]) - now_ms)
end
redis.call('ZADD', KEYS[1], now_ms, ARGV[3])
redis.call('PEXPIRE', KEYS[1], ARGV[1])
return 0
"""

    def __init__(self, redis_url: str, namespace: str = "dnd-table"):
        self.namespace = _validate_namespace(namespace)
        try:
            import redis
        except ImportError as error:  # pragma: no cover - deployment guard
            raise RuntimeError(
                "REDIS_URL icin 'redis' paketi kurulmus olmali."
            ) from error
        self.client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            self.client.ping()
        except redis.RedisError as error:
            raise RuntimeError("Redis rate-limit servisine baglanilamadi.") from error
        self._script = self.client.register_script(self._SCRIPT)

    def check(self, key: str, limit: int, window_seconds: float) -> float | None:
        if not key or len(key) > 256 or limit < 1 or window_seconds <= 0:
            raise ValueError("Rate-limit parametreleri gecersiz.")
        window_ms = max(1, int(window_seconds * 1000))
        key_digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        redis_key = f"{self.namespace}:rate:{key_digest}"
        member = f"{time.time_ns()}:{secrets.token_hex(8)}"
        retry_ms = int(
            self._script(
                keys=[redis_key],
                args=[window_ms, limit, member],
            )
        )
        return retry_ms / 1000 if retry_ms else None

    def close(self) -> None:
        self.client.close()


class RedisRealtimeCoordinator:
    """Shared presence, pub/sub and recoverable grace scheduling for workers."""

    def __init__(
        self,
        redis_url: str,
        instance_id: str,
        *,
        namespace: str = "dnd-table",
        presence_ttl_seconds: int = 90,
    ):
        self.namespace = _validate_namespace(namespace)
        if (
            not isinstance(instance_id, str)
            or not 1 <= len(instance_id) <= 160
        ):
            raise ValueError("Redis instance_id 1-160 karakter olmali.")
        try:
            import redis
        except ImportError as error:  # pragma: no cover - deployment guard
            raise RuntimeError(
                "REDIS_URL icin 'redis' paketi kurulmus olmali."
            ) from error
        self._redis_module = redis
        self.client = redis.Redis.from_url(
            redis_url,
            decode_responses=True,
            socket_connect_timeout=2,
            socket_timeout=2,
        )
        try:
            self.client.ping()
        except redis.RedisError as error:
            raise RuntimeError("Redis realtime servisine baglanilamadi.") from error
        self.instance_id = instance_id
        self.presence_ttl_seconds = max(45, presence_ttl_seconds)
        self.channel = f"{namespace}:realtime"
        self.grace_key = f"{namespace}:grace"
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._subscribed = threading.Event()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._message_callback: MessageCallback | None = None
        self._grace_callback: GraceCallback | None = None
        self._join_script = self.client.register_script(self._JOIN_SCRIPT)
        self._heartbeat_script = self.client.register_script(
            self._HEARTBEAT_SCRIPT
        )
        self._online_script = self.client.register_script(self._ONLINE_SCRIPT)
        self._schedule_script = self.client.register_script(
            self._SCHEDULE_SCRIPT
        )
        self._due_script = self.client.register_script(self._DUE_SCRIPT)
        self._reschedule_script = self.client.register_script(
            self._RESCHEDULE_SCRIPT
        )

    def _presence_key(self, game_id: str) -> str:
        return f"{self.namespace}:presence:{game_id}"

    @staticmethod
    def _presence_member(member_id: str, instance_id: str, connection_id: str) -> str:
        return json.dumps(
            [member_id, instance_id, connection_id],
            ensure_ascii=True,
            separators=(",", ":"),
        )

    def join(
        self, game_id: str, member_id: str, connection_id: str
    ) -> None:
        key = self._presence_key(game_id)
        self._join_script(
            keys=[key],
            args=[
                self._presence_member(
                    member_id, self.instance_id, connection_id
                ),
                self.presence_ttl_seconds,
            ],
        )

    def heartbeat(
        self, game_id: str, member_id: str, connection_id: str
    ) -> None:
        entry = self._presence_member(
            member_id, self.instance_id, connection_id
        )
        self._heartbeat_script(
            keys=[self._presence_key(game_id)],
            args=[entry, self.presence_ttl_seconds],
        )

    def leave(
        self, game_id: str, member_id: str, connection_id: str
    ) -> None:
        self.client.zrem(
            self._presence_key(game_id),
            self._presence_member(
                member_id, self.instance_id, connection_id
            ),
        )

    def online_member_ids(self, game_id: str) -> set[str]:
        key = self._presence_key(game_id)
        entries = self._online_script(keys=[key], args=[])
        members: set[str] = set()
        for entry in entries:
            try:
                decoded = json.loads(entry)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                isinstance(decoded, list)
                and len(decoded) == 3
                and all(isinstance(value, str) for value in decoded)
                and 1 <= len(decoded[0]) <= 128
            ):
                members.add(decoded[0])
        return members

    def publish(self, message: dict[str, Any]) -> None:
        envelope = {**message, "origin": self.instance_id}
        self.client.publish(
            self.channel,
            json.dumps(envelope, ensure_ascii=True, separators=(",", ":")),
        )

    def schedule_grace(
        self, game_id: str, member_id: str, deadline_epoch: float
    ) -> None:
        value = json.dumps(
            [game_id, member_id], ensure_ascii=True, separators=(",", ":")
        )
        delay = max(0.0, deadline_epoch - time.time())
        self._schedule_script(
            keys=[self.grace_key], args=[value, delay]
        )

    def cancel_grace(self, game_id: str, member_id: str) -> None:
        value = json.dumps(
            [game_id, member_id], ensure_ascii=True, separators=(",", ":")
        )
        self.client.zrem(self.grace_key, value)

    def start(
        self,
        loop: asyncio.AbstractEventLoop,
        message_callback: MessageCallback,
        grace_callback: GraceCallback,
    ) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._loop = loop
        self._message_callback = message_callback
        self._grace_callback = grace_callback
        self._stop.clear()
        self._ready.clear()
        self._subscribed.clear()
        self._thread = threading.Thread(
            target=self._run,
            name=f"redis-realtime-{self.instance_id[:8]}",
            daemon=True,
        )
        self._thread.start()
        if not self._ready.wait(timeout=2):
            self._stop.set()
            self._thread.join(timeout=2)
            self._thread = None
            raise RuntimeError("Redis pub/sub subscriber baslatilamadi.")

    def _submit(self, coroutine: Awaitable[None]) -> None:
        if self._loop is None or self._loop.is_closed():
            if hasattr(coroutine, "close"):
                coroutine.close()  # type: ignore[attr-defined]
            return
        future = asyncio.run_coroutine_threadsafe(coroutine, self._loop)
        # Retrieve exceptions so failed callbacks do not become silent,
        # unobserved concurrent-future warnings.
        future.add_done_callback(
            lambda completed: completed.exception()
            if not completed.cancelled()
            else None
        )

    def _run(self) -> None:
        subscribed_once = False
        while not self._stop.is_set():
            pubsub = self.client.pubsub(ignore_subscribe_messages=False)
            try:
                pubsub.subscribe(self.channel)
                while not self._stop.is_set():
                    confirmation = pubsub.get_message(timeout=0.25)
                    if (
                        confirmation
                        and confirmation.get("type") == "subscribe"
                        and confirmation.get("channel") == self.channel
                    ):
                        self._subscribed.set()
                        self._ready.set()
                        if (
                            subscribed_once
                            and self._message_callback is not None
                        ):
                            self._submit(self._message_callback({
                                "type": "resync",
                                "origin": self.instance_id,
                            }))
                        subscribed_once = True
                        break
                while not self._stop.is_set():
                    message = pubsub.get_message(timeout=0.25)
                    if message and message.get("type") == "message":
                        try:
                            payload = json.loads(message["data"])
                        except (TypeError, json.JSONDecodeError):
                            payload = None
                        if (
                            isinstance(payload, dict)
                            and payload.get("origin") != self.instance_id
                            and self._message_callback is not None
                        ):
                            self._submit(self._message_callback(payload))
                    self._claim_due_grace()
            except self._redis_module.RedisError:
                # Redis is mandatory when configured. Keep retrying so a brief
                # failover does not permanently disable this worker's bus.
                self._stop.wait(0.5)
            finally:
                self._subscribed.clear()
                try:
                    pubsub.close()
                except self._redis_module.RedisError:
                    pass

    def _claim_due_grace(self) -> None:
        due = self._due_script(keys=[self.grace_key], args=[20])
        for value in due:
            lock_key = (
                f"{self.namespace}:grace-lock:"
                f"{hashlib.sha256(value.encode('utf-8')).hexdigest()}"
            )
            lock_token = secrets.token_hex(16)
            if not self.client.set(
                lock_key, lock_token, nx=True, px=60_000
            ):
                continue
            try:
                decoded = json.loads(value)
            except (TypeError, ValueError, json.JSONDecodeError):
                self.client.zrem(self.grace_key, value)
                self._release_lock(lock_key, lock_token)
                continue
            if (
                isinstance(decoded, list)
                and len(decoded) == 2
                and all(
                    isinstance(item, str) and 1 <= len(item) <= 128
                    for item in decoded
                )
                and self._grace_callback is not None
            ):
                game_id, member_id = decoded
                self._submit(
                    self._complete_grace(
                        game_id, member_id, value, lock_key, lock_token
                    )
                )
            else:
                self.client.zrem(self.grace_key, value)
                self._release_lock(lock_key, lock_token)

    def _release_lock(self, lock_key: str, lock_token: str) -> None:
        self.client.eval(
            """
            if redis.call('GET', KEYS[1]) == ARGV[1] then
              return redis.call('DEL', KEYS[1])
            end
            return 0
            """,
            1,
            lock_key,
            lock_token,
        )

    async def _complete_grace(
        self,
        game_id: str,
        member_id: str,
        value: str,
        lock_key: str,
        lock_token: str,
    ) -> None:
        try:
            completed = (
                await self._grace_callback(game_id, member_id)
                if self._grace_callback is not None
                else False
            )
            if completed:
                await asyncio.to_thread(
                    self.client.zrem, self.grace_key, value
                )
            else:
                # A crashed worker's presence lease can outlive the grace
                # deadline. Keep the durable deadline until that lease expires.
                await asyncio.to_thread(
                    self._reschedule_script,
                    keys=[self.grace_key],
                    args=[value, 1],
                )
        except Exception:
            # Preserve durable work but avoid a hot retry loop when the
            # callback's database or broadcast dependency is temporarily down.
            await asyncio.to_thread(
                self._reschedule_script,
                keys=[self.grace_key],
                args=[value, 1],
            )
            raise
        finally:
            await asyncio.to_thread(
                self._release_lock, lock_key, lock_token
            )

    def close(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread and thread.is_alive():
            thread.join(timeout=2)
        self._thread = None
        self._subscribed.clear()
        try:
            self.client.close()
        except self._redis_module.RedisError:
            pass

    def is_healthy(self) -> bool:
        thread = self._thread
        if (
            thread is None
            or not thread.is_alive()
            or not self._subscribed.is_set()
        ):
            return False
        try:
            return bool(self.client.ping())
        except self._redis_module.RedisError:
            return False
    _JOIN_SCRIPT = """
local current = redis.call('TIME')
local expiry = tonumber(current[1]) + (tonumber(current[2]) / 1000000)
  + tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], expiry, ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
return expiry
"""
    _HEARTBEAT_SCRIPT = """
local current = redis.call('TIME')
local expiry = tonumber(current[1]) + (tonumber(current[2]) / 1000000)
  + tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], expiry, ARGV[1])
redis.call('EXPIRE', KEYS[1], tonumber(ARGV[2]) * 2)
return 1
"""
    _ONLINE_SCRIPT = """
local current = redis.call('TIME')
local now = tonumber(current[1]) + (tonumber(current[2]) / 1000000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
return redis.call('ZRANGE', KEYS[1], 0, -1)
"""
    _SCHEDULE_SCRIPT = """
local current = redis.call('TIME')
local deadline = tonumber(current[1]) + (tonumber(current[2]) / 1000000)
  + tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], deadline, ARGV[1])
return deadline
"""
    _DUE_SCRIPT = """
local current = redis.call('TIME')
local now = tonumber(current[1]) + (tonumber(current[2]) / 1000000)
return redis.call('ZRANGEBYSCORE', KEYS[1], '-inf', now, 'LIMIT', 0, ARGV[1])
"""
    _RESCHEDULE_SCRIPT = """
local current = redis.call('TIME')
local deadline = tonumber(current[1]) + (tonumber(current[2]) / 1000000)
  + tonumber(ARGV[2])
redis.call('ZADD', KEYS[1], deadline, ARGV[1])
return deadline
"""
