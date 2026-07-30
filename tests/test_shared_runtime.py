import asyncio
import tempfile
import threading
import unittest
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

import api.app as api_app
from api.rate_limit import RateLimiter
from api.realtime import ConnectionManager
from api.shared_runtime import RedisRealtimeCoordinator
from api.store import GameStore


class FakeWebSocket:
    def __init__(self):
        self.messages = []
        self.closed_with = None

    async def accept(self):
        return None

    async def send_json(self, message):
        self.messages.append(message)

    async def close(self, code=1000):
        self.closed_with = code


class SharedHub:
    def __init__(self):
        self.presence = defaultdict(set)
        self.coordinators = []
        self.grace = {}

    def coordinator(self, instance_id):
        coordinator = FakeCoordinator(self, instance_id)
        self.coordinators.append(coordinator)
        return coordinator


class FakeCoordinator:
    def __init__(self, hub, instance_id):
        self.hub = hub
        self.instance_id = instance_id
        self.message_callback = None
        self.grace_callback = None
        self.loop = None
        self.start_count = 0
        self.heartbeat_started = threading.Event()
        self.heartbeat_release = threading.Event()
        self.block_heartbeat = False

    def start(self, loop, message_callback, grace_callback):
        self.start_count += 1
        self.loop = loop
        self.message_callback = message_callback
        self.grace_callback = grace_callback

    def join(self, game_id, member_id, connection_id):
        self.hub.presence[game_id].add(
            (member_id, self.instance_id, connection_id)
        )

    def heartbeat(self, game_id, member_id, connection_id):
        self.heartbeat_started.set()
        if self.block_heartbeat:
            self.heartbeat_release.wait(timeout=2)
        self.hub.presence[game_id].add(
            (member_id, self.instance_id, connection_id)
        )

    def leave(self, game_id, member_id, connection_id):
        self.hub.presence[game_id].discard(
            (member_id, self.instance_id, connection_id)
        )

    def online_member_ids(self, game_id):
        return {
            member_id
            for member_id, _, _ in self.hub.presence[game_id]
        }

    def publish(self, message):
        for coordinator in self.hub.coordinators:
            if (
                coordinator is not self
                and coordinator.message_callback is not None
            ):
                coordinator.loop.call_soon_threadsafe(
                    coordinator.loop.create_task,
                    coordinator.message_callback({
                        **message,
                        "origin": self.instance_id,
                    }),
                )

    def schedule_grace(self, game_id, member_id, deadline_epoch):
        self.hub.grace[(game_id, member_id)] = deadline_epoch

    def cancel_grace(self, game_id, member_id):
        self.hub.grace.pop((game_id, member_id), None)

    def close(self):
        return None


class SharedRuntimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        self.created = self.store.create_game(
            "Shared", "Morgan", "human"
        )
        self.player = self.store.join_game(
            self.created["invite_code"], "Riva"
        )
        self.dm_auth = self.store.authenticate(self.created["token"])
        self.player_auth = self.store.authenticate(self.player["token"])
        self.hub = SharedHub()
        self.first = ConnectionManager(
            self.store, coordinator=self.hub.coordinator("worker-a")
        )
        self.second = ConnectionManager(
            self.store, coordinator=self.hub.coordinator("worker-b")
        )

    async def asyncTearDown(self):
        self.first.close()
        self.second.close()
        self.temp.cleanup()

    async def test_presence_is_shared_and_last_disconnect_starts_grace(self):
        first_socket = FakeWebSocket()
        second_socket = FakeWebSocket()
        await self.first.connect(first_socket, self.dm_auth)
        await self.second.connect(second_socket, self.dm_auth)

        await self.first.disconnect_async(first_socket, self.dm_auth)
        self.assertEqual(
            self.store.game(self.dm_auth.game_id)["handover"], {}
        )
        await self.second.disconnect_async(second_socket, self.dm_auth)
        handover = self.store.game(self.dm_auth.game_id)["handover"]
        self.assertEqual(handover["status"], "grace")
        self.assertIn(
            (self.dm_auth.game_id, self.dm_auth.member_id),
            self.hub.grace,
        )

    async def test_event_snapshot_and_disconnect_cross_workers(self):
        first_socket = FakeWebSocket()
        second_socket = FakeWebSocket()
        await self.first.connect(first_socket, self.player_auth)
        await self.second.connect(second_socket, self.player_auth)
        self.second.snapshot_factory = lambda auth: {
            "game_id": auth.game_id,
            "worker": "b",
        }
        event = self.store.add_event(
            self.player_auth.game_id,
            "shared_event",
            self.dm_auth.member_id,
            "party",
            {},
        )

        await self.first.broadcast_event(event)
        await self.first.broadcast_snapshot(
            self.player_auth.game_id,
            lambda auth: {"game_id": auth.game_id, "worker": "a"},
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertTrue(any(
            item.get("kind") == "event"
            for item in second_socket.messages
        ))
        self.assertTrue(any(
            item.get("kind") == "snapshot"
            and item["snapshot"]["worker"] == "b"
            for item in second_socket.messages
        ))

        await self.first.disconnect_member(
            self.player_auth.game_id,
            self.player_auth.member_id,
            trigger_grace=False,
        )
        await asyncio.sleep(0)
        self.assertEqual(second_socket.closed_with, 4401)

    async def test_pubsub_reconnect_resyncs_authoritative_snapshots(self):
        socket = FakeWebSocket()
        await self.second.connect(socket, self.player_auth)
        self.second.snapshot_factory = lambda auth: {
            "game_id": auth.game_id,
            "resynced": True,
        }
        socket.messages.clear()
        await self.second._receive_shared({"type": "resync"})
        self.assertEqual(socket.messages, [{
            "kind": "snapshot",
            "snapshot": {
                "game_id": self.player_auth.game_id,
                "resynced": True,
            },
        }])

    def test_rate_limiter_delegates_to_shared_backend(self):
        class Backend:
            def __init__(self):
                self.calls = []

            def check(self, key, limit, window_seconds):
                self.calls.append((key, limit, window_seconds))
                return 2.5

        backend = Backend()
        limiter = RateLimiter(backend)
        self.assertEqual(limiter.check("command:member", 10, 30), 2.5)
        self.assertEqual(
            backend.calls, [("command:member", 10, 30)]
        )

    async def test_shared_rate_limit_does_not_block_event_loop(self):
        class BlockingBackend:
            def __init__(self):
                self.started = threading.Event()
                self.release = threading.Event()

            def check(self, key, limit, window_seconds):
                self.started.set()
                self.release.wait(timeout=2)
                return None

            def close(self):
                return None

        backend = BlockingBackend()
        limiter = RateLimiter(backend)
        checked = asyncio.create_task(
            limiter.check_async("command:member", 10, 30)
        )
        self.assertTrue(
            await asyncio.to_thread(backend.started.wait, 1)
        )
        # If check_async called Redis directly this timeout could not run
        # until the blocking backend returned.
        await asyncio.wait_for(asyncio.sleep(0), timeout=0.1)
        backend.release.set()
        self.assertIsNone(await checked)

    async def test_runtime_starts_without_waiting_for_first_socket(self):
        coordinator = self.first.coordinator
        await self.first.start_shared()
        await self.first.start_shared()
        self.assertEqual(coordinator.start_count, 1)

    async def test_startup_recovers_persisted_grace_without_socket(self):
        deadline = datetime.now(UTC) + timedelta(seconds=60)
        self.store.set_handover(self.dm_auth.game_id, {
            "status": "grace",
            "offline_dm_id": self.dm_auth.member_id,
            "deadline": deadline.isoformat(),
        })
        await self.first.start_shared()
        restored = self.hub.grace[
            (self.dm_auth.game_id, self.dm_auth.member_id)
        ]
        self.assertAlmostEqual(
            restored, deadline.timestamp(), delta=0.1
        )

    async def test_heartbeat_restores_evicted_presence_without_ghosting_disconnect(self):
        socket = FakeWebSocket()
        await self.first.connect(socket, self.player_auth)
        self.hub.presence[self.player_auth.game_id].clear()
        await self.first.heartbeat(socket, self.player_auth)
        self.assertIn(
            self.player_auth.member_id,
            self.first.online_member_ids(self.player_auth.game_id),
        )

        coordinator = self.first.coordinator
        coordinator.block_heartbeat = True
        coordinator.heartbeat_started.clear()
        heartbeat = asyncio.create_task(
            self.first.heartbeat(socket, self.player_auth)
        )
        await asyncio.to_thread(
            coordinator.heartbeat_started.wait, 1
        )
        disconnected = asyncio.create_task(
            self.first.disconnect_async(
                socket, self.player_auth, trigger_grace=False
            )
        )
        await asyncio.sleep(0)
        coordinator.heartbeat_release.set()
        await asyncio.gather(heartbeat, disconnected)
        self.assertNotIn(
            self.player_auth.member_id,
            self.first.online_member_ids(self.player_auth.game_id),
        )

    async def test_simultaneous_last_disconnect_creates_one_grace_event(self):
        first_socket = FakeWebSocket()
        second_socket = FakeWebSocket()
        await self.first.connect(first_socket, self.dm_auth)
        await self.first.connect(second_socket, self.dm_auth)
        await asyncio.gather(
            self.first.disconnect_async(first_socket, self.dm_auth),
            self.first.disconnect_async(second_socket, self.dm_auth),
        )
        events = self.store.events(self.dm_auth)
        self.assertEqual(
            sum(
                event["type"] == "dm_connection_lost"
                for event in events
            ),
            1,
        )

    async def test_graceful_shutdown_releases_presence_and_connections(self):
        socket = FakeWebSocket()
        await self.first.connect(socket, self.player_auth)
        await self.first.close_async()
        self.assertEqual(
            self.hub.presence[self.player_auth.game_id],
            set(),
        )
        self.assertEqual(dict(self.first.connections), {})
        self.assertEqual(self.first.connection_ids, {})

    def test_redis_namespace_rejects_key_separator_injection_before_connect(self):
        with self.assertRaises(ValueError):
            RedisRealtimeCoordinator(
                "redis://unreachable.invalid",
                "worker",
                namespace="tenant:escape",
            )

    async def test_app_lifespan_starts_and_closes_replaced_runtime(self):
        class Runtime:
            def __init__(self):
                self.started = False
                self.closed = False

            async def start_shared(self):
                self.started = True

            async def close_async(self):
                self.closed = True

        class Limiter:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        original_connections = api_app.connections
        original_limiter = api_app.rate_limiter
        runtime = Runtime()
        limiter = Limiter()
        api_app.connections = runtime
        api_app.rate_limiter = limiter
        try:
            async with api_app.app_lifespan(api_app.app):
                self.assertTrue(runtime.started)
                self.assertFalse(runtime.closed)
            self.assertTrue(runtime.closed)
            self.assertTrue(limiter.closed)
        finally:
            api_app.connections = original_connections
            api_app.rate_limiter = original_limiter


if __name__ == "__main__":
    unittest.main()
