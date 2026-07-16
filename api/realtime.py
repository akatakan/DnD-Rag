import asyncio
from collections import defaultdict
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from fastapi import WebSocket

from api.models import AuthContext
from api.store import GameStore

GraceCallback = Callable[[str, str], Awaitable[None]]


class ConnectionManager:
    def __init__(self, store: GameStore, grace_seconds: float = 60):
        self.store = store
        self.grace_seconds = grace_seconds
        self.connections: dict[str, list[tuple[WebSocket, AuthContext]]] = defaultdict(list)
        self.grace_tasks: dict[str, asyncio.Task] = {}
        self.on_grace_expired: GraceCallback | None = None

    def online_member_ids(self, game_id: str) -> set[str]:
        return {auth.member_id for _, auth in self.connections[game_id]}

    def is_online(self, game_id: str, member_id: str) -> bool:
        return member_id in self.online_member_ids(game_id)

    async def connect(self, websocket: WebSocket, auth: AuthContext) -> None:
        await websocket.accept()
        self.connections[auth.game_id].append((websocket, auth))
        game = self.store.game(auth.game_id)
        if auth.member_id == game["active_dm_id"]:
            task = self.grace_tasks.pop(auth.game_id, None)
            if task:
                task.cancel()
            if game["handover"].get("status") == "grace":
                self.store.cancel_handover(auth.game_id)
                event = self.store.add_event(auth.game_id, "dm_reconnected", auth.member_id, "party", {})
                await self.broadcast_event(event)

    def disconnect(self, websocket: WebSocket, auth: AuthContext) -> None:
        self.connections[auth.game_id] = [
            item for item in self.connections[auth.game_id] if item[0] is not websocket
        ]
        game = self.store.game(auth.game_id)
        if auth.member_id != game["active_dm_id"] or self.is_online(auth.game_id, auth.member_id):
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
        self.grace_tasks[auth.game_id] = asyncio.create_task(
            self._grace_wait(auth.game_id, auth.member_id)
        )

    async def _grace_wait(self, game_id: str, member_id: str) -> None:
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
            self.grace_tasks.pop(game_id, None)

    async def broadcast_event(self, event: dict) -> None:
        dead = []
        for websocket, auth in list(self.connections[event["game_id"]]):
            if not self.store.can_view(event["visibility"], auth):
                continue
            try:
                await websocket.send_json({"kind": "event", "event": event})
            except Exception:
                dead.append((websocket, auth))
        for websocket, auth in dead:
            self.disconnect(websocket, auth)

    async def broadcast_snapshot(self, game_id: str, snapshot_factory) -> None:
        dead = []
        for websocket, auth in list(self.connections[game_id]):
            try:
                member = self.store.member(game_id, auth.member_id)
                game = self.store.game(game_id)
                fresh_auth = AuthContext(
                    game_id=game_id, member_id=member["id"], role=member["role"],
                    character_id=member["character_id"],
                    is_owner=game["owner_id"] == member["id"],
                )
                await websocket.send_json({"kind": "snapshot", "snapshot": snapshot_factory(fresh_auth)})
            except Exception:
                dead.append((websocket, auth))
        for websocket, auth in dead:
            self.disconnect(websocket, auth)
