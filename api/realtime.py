import asyncio
import secrets
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import WebSocket

from api.models import AuthContext
from api.shared_runtime import RedisRealtimeCoordinator
from api.store import GameStore

GraceCallback = Callable[[str, str], Awaitable[None]]


class ConnectionManager:
    def __init__(
        self,
        store: GameStore,
        grace_seconds: float = 60,
        coordinator: RedisRealtimeCoordinator | None = None,
    ):
        self.store = store
        self.grace_seconds = grace_seconds
        self.coordinator = coordinator
        self.connections: dict[str, list[tuple[WebSocket, AuthContext]]] = defaultdict(list)
        self.connection_ids: dict[int, str] = {}
        self.grace_tasks: dict[str, asyncio.Task] = {}
        self.on_grace_expired: GraceCallback | None = None
        self.snapshot_factory: Callable[[AuthContext], dict] | None = None
        self._shared_started = False
        self._shared_start_lock = asyncio.Lock()
        self._presence_locks: dict[str, asyncio.Lock] = defaultdict(
            asyncio.Lock
        )

    async def start_shared(self) -> None:
        if self.coordinator is None or self._shared_started:
            return
        async with self._shared_start_lock:
            if self._shared_started:
                return
            await asyncio.to_thread(
                self.coordinator.start,
                asyncio.get_running_loop(),
                self._receive_shared,
                self._shared_grace_expired,
            )
            for game_id, member_id, deadline in await asyncio.to_thread(
                self.store.recoverable_grace_handovers
            ):
                await asyncio.to_thread(
                    self.coordinator.schedule_grace,
                    game_id,
                    member_id,
                    deadline,
                )
            self._shared_started = True

    def online_member_ids(self, game_id: str) -> set[str]:
        if self.coordinator is not None:
            return self.coordinator.online_member_ids(game_id)
        return {auth.member_id for _, auth in self.connections[game_id]}

    async def online_member_ids_async(self, game_id: str) -> set[str]:
        if self.coordinator is not None:
            return await asyncio.to_thread(
                self.coordinator.online_member_ids, game_id
            )
        return self.online_member_ids(game_id)

    def is_online(self, game_id: str, member_id: str) -> bool:
        return member_id in self.online_member_ids(game_id)

    async def connect(self, websocket: WebSocket, auth: AuthContext) -> None:
        await websocket.accept()
        self.connections[auth.game_id].append((websocket, auth))
        connection_id = secrets.token_urlsafe(18)
        self.connection_ids[id(websocket)] = connection_id
        if self.coordinator is not None:
            try:
                await self.start_shared()
                async with self._presence_locks[auth.game_id]:
                    await asyncio.to_thread(
                        self.coordinator.join,
                        auth.game_id,
                        auth.member_id,
                        connection_id,
                    )
                    await self._handle_active_dm_reconnect(auth)
            except Exception:
                self.connections[auth.game_id] = [
                    item
                    for item in self.connections[auth.game_id]
                    if item[0] is not websocket
                ]
                self.connection_ids.pop(id(websocket), None)
                try:
                    await websocket.close(code=1013)
                except Exception:
                    pass
                raise
        else:
            await self._handle_active_dm_reconnect(auth)

    async def _handle_active_dm_reconnect(
        self, auth: AuthContext
    ) -> None:
        game = self.store.game(auth.game_id)
        if auth.member_id != game["active_dm_id"]:
            return
        if self.coordinator is not None:
            await asyncio.to_thread(
                self.coordinator.cancel_grace,
                auth.game_id,
                auth.member_id,
            )
        task = self.grace_tasks.pop(auth.game_id, None)
        if task:
            task.cancel()
        event = None
        with self.store.transaction():
            game = self.store.game(auth.game_id)
            if auth.member_id != game["active_dm_id"]:
                return
            handover = game.get("handover") or {}
            if (
                handover.get("offline_dm_id") == auth.member_id
                and handover.get("status")
                in {"grace", "offered", "vote_ai", "assisted"}
            ):
                self.store.cancel_handover(auth.game_id)
                event = self.store.add_event(auth.game_id, "dm_reconnected", auth.member_id, "party", {})
        if event is not None:
            await self.broadcast_event(event)

    def disconnect(
        self,
        websocket: WebSocket,
        auth: AuthContext,
        trigger_grace: bool = True,
    ) -> None:
        self.connections[auth.game_id] = [
            item for item in self.connections[auth.game_id] if item[0] is not websocket
        ]
        connection_id = self.connection_ids.pop(id(websocket), None)
        if self.coordinator is not None and connection_id is not None:
            self.coordinator.leave(
                auth.game_id, auth.member_id, connection_id
            )
        if not trigger_grace:
            return
        game = self.store.game(auth.game_id)
        if (
            auth.member_id != game["active_dm_id"]
            or self.is_online(auth.game_id, auth.member_id)
        ):
            return
        existing = self.grace_tasks.pop(auth.game_id, None)
        if existing:
            existing.cancel()
        deadline = datetime.now(UTC) + timedelta(seconds=self.grace_seconds)
        self.store.set_handover(auth.game_id, {
            "status": "grace", "offline_dm_id": auth.member_id,
            "deadline": deadline.isoformat(),
        })
        event = self.store.add_event(
            auth.game_id, "dm_connection_lost", auth.member_id, "party",
            {"grace_seconds": self.grace_seconds, "deadline": deadline.isoformat()},
        )
        asyncio.create_task(self.broadcast_event(event))
        if self.coordinator is not None:
            self.coordinator.schedule_grace(
                auth.game_id, auth.member_id, deadline.timestamp()
            )
            return
        self.grace_tasks[auth.game_id] = asyncio.create_task(
            self._grace_wait(auth.game_id, auth.member_id)
        )

    async def disconnect_async(
        self,
        websocket: WebSocket,
        auth: AuthContext,
        trigger_grace: bool = True,
    ) -> None:
        if self.coordinator is None:
            self.disconnect(websocket, auth, trigger_grace)
            return
        async with self._presence_locks[auth.game_id]:
            self.connections[auth.game_id] = [
                item
                for item in self.connections[auth.game_id]
                if item[0] is not websocket
            ]
            connection_id = self.connection_ids.pop(
                id(websocket), None
            )
            if connection_id is not None:
                await asyncio.to_thread(
                    self.coordinator.leave,
                    auth.game_id,
                    auth.member_id,
                    connection_id,
                )
            if not trigger_grace:
                return
            game = self.store.game(auth.game_id)
            if (
                auth.member_id != game["active_dm_id"]
                or auth.member_id
                in await self.online_member_ids_async(auth.game_id)
            ):
                return
            handover = game.get("handover") or {}
            if (
                handover.get("status") == "grace"
                and handover.get("offline_dm_id") == auth.member_id
            ):
                return
            deadline = datetime.now(UTC) + timedelta(
                seconds=self.grace_seconds
            )
            self.store.set_handover(auth.game_id, {
                "status": "grace",
                "offline_dm_id": auth.member_id,
                "deadline": deadline.isoformat(),
            })
            event = self.store.add_event(
                auth.game_id,
                "dm_connection_lost",
                auth.member_id,
                "party",
                {
                    "grace_seconds": self.grace_seconds,
                    "deadline": deadline.isoformat(),
                },
            )
            asyncio.create_task(self.broadcast_event(event))
            await asyncio.to_thread(
                self.coordinator.schedule_grace,
                auth.game_id,
                auth.member_id,
                deadline.timestamp(),
            )

    async def disconnect_member(
        self,
        game_id: str,
        member_id: str,
        code: int = 4401,
        trigger_grace: bool = True,
        publish: bool = True,
    ) -> None:
        targets = [
            (websocket, auth)
            for websocket, auth in list(self.connections[game_id])
            if auth.member_id == member_id
        ]
        for websocket, auth in targets:
            try:
                await websocket.close(code=code)
            except Exception:
                pass
            finally:
                await self.disconnect_async(
                    websocket, auth, trigger_grace=trigger_grace
                )
        if publish and self.coordinator is not None:
            await asyncio.to_thread(
                self.coordinator.publish,
                {
                    "type": "disconnect",
                    "game_id": game_id,
                    "member_id": member_id,
                    "code": code,
                    "trigger_grace": trigger_grace,
                },
            )

    async def heartbeat(self, websocket: WebSocket, auth: AuthContext) -> None:
        if self.coordinator is None:
            return
        async with self._presence_locks[auth.game_id]:
            connection_id = self.connection_ids.get(id(websocket))
            if connection_id is not None:
                # Heartbeat is an upsert so a live socket restores presence
                # after Redis eviction/failover. The per-game lock and
                # connection-id recheck prevent a delayed beat from
                # resurrecting a socket after disconnect.
                await asyncio.to_thread(
                    self.coordinator.heartbeat,
                    auth.game_id,
                    auth.member_id,
                    connection_id,
                )

    async def _grace_wait(self, game_id: str, member_id: str) -> None:
        current_task = asyncio.current_task()
        try:
            await asyncio.sleep(self.grace_seconds)
            game = self.store.game(game_id)
            if (
                not self.is_online(game_id, member_id)
                and game["active_dm_id"] == member_id
                and self.on_grace_expired
            ):
                await self.on_grace_expired(game_id, member_id)
        except asyncio.CancelledError:
            pass
        finally:
            # A rapid disconnect/reconnect/disconnect can install a replacement
            # timer before the cancelled task reaches finally. The old task must
            # not remove that newer timer.
            if self.grace_tasks.get(game_id) is current_task:
                self.grace_tasks.pop(game_id, None)

    async def broadcast_event(self, event: dict) -> None:
        await self._broadcast_event_local(event)
        if self.coordinator is not None:
            await asyncio.to_thread(
                self.coordinator.publish,
                {"type": "event", "event": event},
            )

    async def _broadcast_event_local(self, event: dict) -> None:
        dead = []
        for websocket, auth in list(self.connections[event["game_id"]]):
            fresh_auth = self.store.refresh_auth_context(auth)
            if fresh_auth is None:
                dead.append((websocket, auth))
                continue
            if not self.store.can_view(event["visibility"], fresh_auth):
                continue
            try:
                await websocket.send_json({"kind": "event", "event": event})
            except Exception:
                dead.append((websocket, auth))
        for websocket, auth in dead:
            try:
                await websocket.close(code=4401)
            except Exception:
                pass
            await self.disconnect_async(websocket, auth)

    async def broadcast_snapshot(self, game_id: str, snapshot_factory) -> None:
        self.snapshot_factory = snapshot_factory
        await self._broadcast_snapshot_local(game_id, snapshot_factory)
        if self.coordinator is not None:
            await asyncio.to_thread(
                self.coordinator.publish,
                {"type": "snapshot", "game_id": game_id},
            )

    async def _broadcast_snapshot_local(
        self, game_id: str, snapshot_factory
    ) -> None:
        dead = []
        for websocket, auth in list(self.connections[game_id]):
            try:
                fresh_auth = self.store.refresh_auth_context(auth)
                if fresh_auth is None:
                    dead.append((websocket, auth))
                    continue
                projected = await asyncio.to_thread(
                    snapshot_factory, fresh_auth
                )
                await websocket.send_json({
                    "kind": "snapshot",
                    "snapshot": projected,
                })
            except Exception:
                dead.append((websocket, auth))
        for websocket, auth in dead:
            try:
                await websocket.close(code=4401)
            except Exception:
                pass
            await self.disconnect_async(websocket, auth)

    async def _receive_shared(self, message: dict[str, Any]) -> None:
        message_type = message.get("type")
        if message_type == "resync" and self.snapshot_factory is not None:
            for game_id, entries in list(self.connections.items()):
                if entries:
                    await self._broadcast_snapshot_local(
                        game_id, self.snapshot_factory
                    )
        elif message_type == "event" and isinstance(message.get("event"), dict):
            event = message["event"]
            if (
                not isinstance(event.get("game_id"), str)
                or not isinstance(event.get("visibility"), str)
            ):
                return
            await self._broadcast_event_local(event)
        elif (
            message_type == "snapshot"
            and isinstance(message.get("game_id"), str)
            and 1 <= len(message["game_id"]) <= 128
            and self.snapshot_factory is not None
        ):
            await self._broadcast_snapshot_local(
                message["game_id"], self.snapshot_factory
            )
        elif (
            message_type == "disconnect"
            and isinstance(message.get("game_id"), str)
            and isinstance(message.get("member_id"), str)
        ):
            code = message.get("code", 4401)
            trigger_grace = message.get("trigger_grace", True)
            if (
                not isinstance(code, int)
                or isinstance(code, bool)
                or not 1000 <= code <= 4999
                or not isinstance(trigger_grace, bool)
                or not 1 <= len(message["game_id"]) <= 128
                or not 1 <= len(message["member_id"]) <= 128
            ):
                return
            await self.disconnect_member(
                message["game_id"],
                message["member_id"],
                code=code,
                trigger_grace=trigger_grace,
                publish=False,
            )

    async def _shared_grace_expired(
        self, game_id: str, member_id: str
    ) -> bool:
        game = self.store.game(game_id)
        handover = game.get("handover") or {}
        if (
            game["active_dm_id"] != member_id
            or handover.get("status") != "grace"
            or handover.get("offline_dm_id") != member_id
        ):
            return True
        if member_id in await self.online_member_ids_async(game_id):
            return False
        if self.on_grace_expired:
            await self.on_grace_expired(game_id, member_id)
            return True
        return False

    def close(self) -> None:
        for task in list(self.grace_tasks.values()):
            task.cancel()
        self.grace_tasks.clear()
        if self.coordinator is not None:
            for game_id, entries in list(self.connections.items()):
                for websocket, auth in entries:
                    connection_id = self.connection_ids.pop(
                        id(websocket), None
                    )
                    if connection_id is not None:
                        try:
                            self.coordinator.leave(
                                game_id, auth.member_id, connection_id
                            )
                        except Exception:
                            pass
            self.coordinator.close()
        self.connections.clear()
        self.connection_ids.clear()
        self._shared_started = False

    async def close_async(self) -> None:
        tasks = list(self.grace_tasks.values())
        for task in tasks:
            task.cancel()
        self.grace_tasks.clear()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self.coordinator is not None:
            for game_id, entries in list(self.connections.items()):
                for websocket, auth in entries:
                    connection_id = self.connection_ids.pop(
                        id(websocket), None
                    )
                    if connection_id is not None:
                        try:
                            await asyncio.to_thread(
                                self.coordinator.leave,
                                game_id,
                                auth.member_id,
                                connection_id,
                            )
                        except Exception:
                            pass
            await asyncio.to_thread(self.coordinator.close)
        self.connections.clear()
        self.connection_ids.clear()
        self._shared_started = False
