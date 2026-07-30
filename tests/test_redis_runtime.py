import asyncio
import os
import secrets
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from api.shared_runtime import (
    RedisRateLimitBackend,
    RedisRealtimeCoordinator,
)


@unittest.skipUnless(
    os.getenv("TEST_REDIS_URL"),
    "TEST_REDIS_URL gercek Redis entegrasyonu icin gerekli.",
)
class RedisRuntimeIntegrationTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.url = os.environ["TEST_REDIS_URL"]
        self.namespace = f"dnd-test-{secrets.token_hex(8)}"
        self.first = RedisRealtimeCoordinator(
            self.url, "worker-a", namespace=self.namespace
        )
        self.second = RedisRealtimeCoordinator(
            self.url, "worker-b", namespace=self.namespace
        )

    async def asyncTearDown(self):
        keys = list(
            self.first.client.scan_iter(f"{self.namespace}:*")
        )
        if keys:
            self.first.client.delete(*keys)
        self.first.close()
        self.second.close()

    async def test_shared_presence_pubsub_grace_and_rate_limit(self):
        messages = []
        grace = []

        async def no_message(_message):
            return None

        async def receive(message):
            messages.append(message)

        async def no_grace(_game_id, _member_id):
            return True

        async def receive_grace(game_id, member_id):
            grace.append((game_id, member_id))
            return True

        loop = asyncio.get_running_loop()
        self.first.start(loop, no_message, no_grace)
        self.second.start(loop, receive, receive_grace)
        await asyncio.sleep(0.1)

        self.first.join("game", "member", "connection")
        self.assertEqual(
            self.second.online_member_ids("game"), {"member"}
        )
        self.first.heartbeat("game", "member", "connection")
        self.first.client.delete(self.first._presence_key("game"))
        self.first.heartbeat("game", "member", "connection")
        self.assertEqual(
            self.second.online_member_ids("game"), {"member"}
        )
        self.first.publish({"type": "snapshot", "game_id": "game"})
        self.second.schedule_grace(
            "game", "member", time.time() + 0.05
        )
        for _ in range(30):
            if messages and grace:
                break
            await asyncio.sleep(0.05)
        self.assertEqual(messages[0]["type"], "snapshot")
        self.assertEqual(grace, [("game", "member")])

        first_limit = RedisRateLimitBackend(
            self.url, namespace=self.namespace
        )
        second_limit = RedisRateLimitBackend(
            self.url, namespace=self.namespace
        )
        try:
            self.assertIsNone(first_limit.check("shared", 2, 10))
            self.assertIsNone(second_limit.check("shared", 2, 10))
            self.assertGreater(first_limit.check("shared", 2, 10), 0)
            with ThreadPoolExecutor(max_workers=12) as workers:
                results = list(workers.map(
                    lambda index: (
                        first_limit if index % 2 else second_limit
                    ).check("atomic", 5, 10),
                    range(24),
                ))
            self.assertEqual(
                sum(result is None for result in results), 5
            )
            self.assertTrue(all(
                result is None or result > 0 for result in results
            ))
        finally:
            first_limit.close()
            second_limit.close()

        self.first.leave("game", "member", "connection")
        self.assertEqual(self.second.online_member_ids("game"), set())


if __name__ == "__main__":
    unittest.main()
