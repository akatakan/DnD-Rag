import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from api.data_lifecycle import apply_retention, retention_preview
from api.store import GameStore


class DataRetentionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        created = self.store.create_game("Retention", "Morgan", "human")
        self.token = created["token"]
        self.auth = self.store.authenticate(created["token"])
        with self.store.connect() as db:
            db.execute(
                """
                INSERT INTO websocket_tickets (
                    id, member_id, ticket_hash, expires_at, used_at, created_at
                ) VALUES (
                    'expired-ticket', ?, 'expired-ticket-hash',
                    '2020-01-01T00:00:00+00:00', NULL,
                    '2020-01-01T00:00:00+00:00'
                )
                """,
                (self.auth.member_id,),
            )
            db.execute(
                """
                INSERT INTO map_transients (
                    id, game_id, actor_id, kind, payload_json,
                    expires_at, created_at
                ) VALUES (
                    'expired-signal', ?, ?, 'ping', '{"x":1,"y":1}',
                    '2020-01-01T00:00:00+00:00',
                    '2020-01-01T00:00:00+00:00'
                )
                """,
                (self.auth.game_id, self.auth.member_id),
            )
            db.execute(
                """
                INSERT INTO command_receipts (
                    game_id, actor_id, client_action_id, command_type,
                    request_hash, response_json, created_at
                ) VALUES (?, ?, 'old-action', 'roll', 'hash', '{}',
                    '2020-01-01T00:00:00+00:00')
                """,
                (self.auth.game_id, self.auth.member_id),
            )
            db.execute(
                """
                INSERT INTO security_audit_events (
                    game_id, actor_id, action, target_id,
                    metadata_json, created_at
                ) VALUES (?, ?, 'old', NULL, '{}',
                    '2020-01-01T00:00:00+00:00')
                """,
                (self.auth.game_id, self.auth.member_id),
            )

    def tearDown(self):
        self.temp.cleanup()

    def test_preview_is_dry_run_and_apply_requires_confirmation(self):
        current = datetime(2026, 7, 30, tzinfo=UTC)
        preview = retention_preview(self.store, current=current)
        self.assertEqual(preview["websocket_tickets"], 1)
        self.assertEqual(preview["map_transients"], 1)
        self.assertEqual(preview["command_receipts"], 1)
        self.assertEqual(preview["security_audit_events"], 1)
        with self.store.connect() as db:
            self.assertEqual(
                db.execute(
                    "SELECT COUNT(*) FROM map_transients"
                ).fetchone()[0],
                1,
            )
        with self.assertRaises(ValueError):
            apply_retention(
                self.store, confirmation="wrong", current=current
            )

        deleted = apply_retention(
            self.store,
            confirmation="PURGE_EXPIRED_RUNTIME_DATA",
            current=current,
        )
        self.assertEqual(deleted["map_transients"], 1)
        self.assertEqual(deleted["command_receipts"], 1)
        self.assertEqual(deleted["security_audit_events"], 1)
        self.assertIsNotNone(self.store.authenticate(self.token))

    def test_retention_bounds_are_fail_closed(self):
        with self.assertRaises(ValueError):
            retention_preview(self.store, credential_grace_days=0)
        with self.assertRaises(ValueError):
            retention_preview(self.store, audit_retention_days=29)


if __name__ == "__main__":
    unittest.main()
