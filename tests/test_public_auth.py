import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from api.realtime import ConnectionManager
from api.security import LOCAL_AUTH_PEPPER, validate_public_security
from api.store import GameStore


class PublicAuthStoreTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.db"
        self.store = GameStore(
            self.path,
            auth_pepper="test-pepper-with-at-least-thirty-two-characters",
        )
        self.created = self.store.create_game("Test", "DM", "human")
        self.auth = self.store.authenticate(self.created["token"])

    def tearDown(self):
        self.temp.cleanup()

    def rows(self, query, parameters=()):
        db = sqlite3.connect(self.path)
        try:
            return db.execute(query, parameters).fetchall()
        finally:
            db.close()

    def test_plaintext_credentials_are_not_persisted(self):
        persisted = [
            value
            for row in self.rows(
                """SELECT token FROM members
                UNION ALL SELECT invite_code FROM games
                UNION ALL SELECT token_hash FROM auth_tokens
                UNION ALL SELECT code_hash FROM game_invites"""
            )
            for value in row
        ]
        self.assertNotIn(self.created["token"], persisted)
        self.assertNotIn(self.created["invite_code"], persisted)
        self.assertTrue(
            all(
                self.created["token"] not in value
                and self.created["invite_code"] not in value
                for value in persisted
            )
        )

    def test_token_rotation_and_logout_revoke_previous_secrets(self):
        rotated = self.store.rotate_token(self.created["token"])
        self.assertIsNone(self.store.authenticate(self.created["token"]))
        self.assertIsNotNone(self.store.authenticate(rotated["token"]))

        self.assertTrue(self.store.revoke_token(rotated["token"]))
        self.assertIsNone(self.store.authenticate(rotated["token"]))
        self.assertFalse(self.store.revoke_token(rotated["token"]))
        actions = [
            event["action"]
            for event in self.store.security_audit(self.created["game_id"])
        ]
        self.assertIn("token_rotated", actions)
        self.assertIn("token_revoked", actions)

    def test_expired_token_is_rejected(self):
        expired_store = GameStore(
            Path(self.temp.name) / "expired.db",
            auth_pepper="test-pepper-with-at-least-thirty-two-characters",
            token_ttl_hours=-1,
        )
        created = expired_store.create_game("Expired", "DM", "human")
        self.assertIsNone(expired_store.authenticate(created["token"]))

    def test_invite_rotation_revokes_old_code_and_enforces_use_limit(self):
        rotated = self.store.rotate_invite(
            self.created["game_id"], self.created["member_id"], max_uses=1
        )
        with self.assertRaises(KeyError):
            self.store.join_game(self.created["invite_code"], "Old Invite")

        self.store.join_game(rotated["invite_code"], "First Player")
        with self.assertRaises(KeyError):
            self.store.join_game(rotated["invite_code"], "Second Player")
        self.assertIsNone(self.store.active_invite(self.created["game_id"]))

    def test_concurrent_single_use_invite_allows_only_one_join(self):
        rotated = self.store.rotate_invite(
            self.created["game_id"], self.created["member_id"], max_uses=1
        )
        barrier = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def join(name):
            store = GameStore(
                self.path,
                auth_pepper="test-pepper-with-at-least-thirty-two-characters",
            )
            barrier.wait()
            try:
                store.join_game(rotated["invite_code"], name)
                outcome = "joined"
            except KeyError:
                outcome = "rejected"
            with outcome_lock:
                outcomes.append(outcome)

        workers = [
            threading.Thread(target=join, args=("A",)),
            threading.Thread(target=join, args=("B",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(sorted(outcomes), ["joined", "rejected"])

    def test_websocket_ticket_is_single_use_and_bound_to_active_token(self):
        first = self.store.create_websocket_ticket(
            self.auth, self.created["token"]
        )
        self.assertIsNotNone(
            self.store.consume_websocket_ticket(
                first["ticket"], self.created["game_id"]
            )
        )
        self.assertIsNone(
            self.store.consume_websocket_ticket(
                first["ticket"], self.created["game_id"]
            )
        )

        second = self.store.create_websocket_ticket(
            self.auth, self.created["token"]
        )
        self.store.revoke_token(self.created["token"])
        self.assertIsNone(
            self.store.consume_websocket_ticket(
                second["ticket"], self.created["game_id"]
            )
        )

    def test_pepper_change_fails_fast_instead_of_silent_lockout(self):
        with self.assertRaisesRegex(RuntimeError, "AUTH_PEPPER"):
            GameStore(
                self.path,
                auth_pepper=(
                    "a-different-pepper-with-at-least-thirty-two-characters"
                ),
            )

    def test_v6_upgrade_requires_explicit_existing_pepper_binding(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("DROP TABLE auth_configuration")
            db.execute("DELETE FROM schema_migrations WHERE version = 7")
            db.commit()
        finally:
            db.close()

        with self.assertRaisesRegex(RuntimeError, "BIND_EXISTING"):
            GameStore(
                self.path,
                auth_pepper=(
                    "a-different-pepper-with-at-least-thirty-two-characters"
                ),
            )

        rebound = GameStore(
            self.path,
            auth_pepper="test-pepper-with-at-least-thirty-two-characters",
            allow_existing_pepper_bind=True,
        )
        self.assertIsNotNone(rebound.authenticate(self.created["token"]))
        with self.assertRaisesRegex(RuntimeError, "AUTH_PEPPER"):
            GameStore(
                self.path,
                auth_pepper=(
                    "a-different-pepper-with-at-least-thirty-two-characters"
                ),
            )


class PublicSecurityConfigTest(unittest.TestCase):
    def test_public_mode_requires_private_pepper_and_https_origins(self):
        with self.assertRaisesRegex(RuntimeError, "AUTH_PEPPER"):
            validate_public_security(
                True, LOCAL_AUTH_PEPPER, ["https://table.example"]
            )
        with self.assertRaisesRegex(RuntimeError, "HTTPS"):
            validate_public_security(
                True,
                "private-pepper-with-at-least-thirty-two-characters",
                ["http://table.example"],
            )
        validate_public_security(
            True,
            "private-pepper-with-at-least-thirty-two-characters",
            ["https://table.example"],
        )

    def test_local_mode_keeps_zero_config_defaults(self):
        validate_public_security(
            False, LOCAL_AUTH_PEPPER, ["http://localhost:5173"]
        )


class FakeWebSocket:
    def __init__(self):
        self.messages = []
        self.closed_with = None

    async def send_json(self, message):
        self.messages.append(message)

    async def close(self, code):
        self.closed_with = code


class PublicAuthRealtimeTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(
            Path(self.temp.name) / "game.db",
            auth_pepper="test-pepper-with-at-least-thirty-two-characters",
        )
        self.created = self.store.create_game("Test", "DM", "human")
        self.player = self.store.join_game(
            self.created["invite_code"], "Riva"
        )
        self.manager = ConnectionManager(self.store)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_revoked_connection_cannot_receive_future_events(self):
        auth = self.store.authenticate(self.player["token"])
        socket = FakeWebSocket()
        self.manager.connections[auth.game_id].append((socket, auth))
        self.store.revoke_token(self.player["token"])
        event = self.store.add_event(
            auth.game_id, "private", auth.member_id,
            f"player:{auth.member_id}", {},
        )

        await self.manager.broadcast_event(event)

        self.assertEqual(socket.messages, [])
        self.assertEqual(socket.closed_with, 4401)
        self.assertFalse(
            self.manager.is_online(auth.game_id, auth.member_id)
        )

    async def test_rotation_disconnect_can_suppress_false_dm_grace(self):
        auth = self.store.authenticate(self.created["token"])
        socket = FakeWebSocket()
        self.manager.connections[auth.game_id].append((socket, auth))

        await self.manager.disconnect_member(
            auth.game_id, auth.member_id, trigger_grace=False
        )

        self.assertEqual(socket.closed_with, 4401)
        self.assertEqual(self.store.game(auth.game_id)["handover"], {})
        self.assertNotIn(auth.game_id, self.manager.grace_tasks)


if __name__ == "__main__":
    unittest.main()
