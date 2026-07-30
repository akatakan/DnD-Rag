import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import ValidationError

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.models import UpdateDicePreferencesRequest
from api.realtime import ConnectionManager
from api.store import GameStore


class DicePreferencesTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.db"
        self.store = GameStore(self.path)
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        created = self.store.create_game("Dice Themes", "DM", "human")
        self.invite_code = created["invite_code"]
        joined = self.store.join_game(created["invite_code"], "Riva")
        self.player_token = joined["token"]
        self.dm = self.store.authenticate(created["token"])
        self.player = self.store.authenticate(joined["token"])

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def test_preferences_default_and_update_are_member_scoped(self):
        self.assertEqual(
            self.store.dice_preferences(self.player)["theme"], "crimson"
        )
        updated = self.store.update_dice_preferences(
            self.player, "arcane", False
        )
        self.assertEqual(updated["theme"], "arcane")
        self.assertFalse(updated["sound_enabled"])
        self.assertEqual(
            self.store.dice_preferences(self.player)["theme"], "arcane"
        )
        self.assertEqual(
            self.store.dice_preferences(self.dm)["theme"], "crimson"
        )

    def test_member_joined_after_migration_gets_lazy_default(self):
        joined = self.store.join_game(self.invite_code, "Gareth")
        auth = self.store.authenticate(joined["token"])
        preferences = self.store.dice_preferences(auth)
        self.assertEqual(
            (preferences["theme"], preferences["sound_enabled"]),
            ("crimson", True),
        )

    def test_database_rejects_cross_game_member_scope(self):
        other = self.store.create_game("Other", "Other DM", "human")
        with self.assertRaises(sqlite3.IntegrityError):
            with self.store.connect() as db:
                db.execute(
                    """
                    INSERT INTO member_dice_preferences (
                        member_id, game_id, theme, sound_enabled, updated_at
                    ) VALUES (?, ?, 'ivory', 1, '2026-01-01T00:00:00Z')
                    ON CONFLICT(member_id) DO UPDATE SET
                        game_id = excluded.game_id
                    """,
                    (self.player.member_id, other["game_id"]),
                )

    def test_request_contract_is_strict(self):
        valid = UpdateDicePreferencesRequest(
            theme="ivory", sound_enabled=False
        )
        self.assertEqual(valid.theme, "ivory")
        with self.assertRaises(ValidationError):
            UpdateDicePreferencesRequest(
                theme="gold", sound_enabled=True
            )
        with self.assertRaises(ValidationError):
            UpdateDicePreferencesRequest(
                theme="crimson", sound_enabled=1
            )
        with self.assertRaises(ValidationError):
            UpdateDicePreferencesRequest(
                theme="crimson",
                sound_enabled=True,
                unexpected="ignored",
            )

    def test_preferences_api_requires_auth_and_validates_payload(self):
        self.assertEqual(
            self.client.get("/api/me/dice-preferences").status_code, 401
        )
        headers = {
            "Authorization": f"Bearer {self.player_token}",
        }
        default = self.client.get(
            "/api/me/dice-preferences", headers=headers
        )
        self.assertEqual(default.status_code, 200, default.text)
        self.assertEqual(default.json()["theme"], "crimson")
        invalid = self.client.patch(
            "/api/me/dice-preferences",
            headers=headers,
            json={"theme": "arcane", "sound_enabled": 1},
        )
        self.assertEqual(invalid.status_code, 422)
        updated = self.client.patch(
            "/api/me/dice-preferences",
            headers=headers,
            json={"theme": "ivory", "sound_enabled": False},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            (updated.json()["theme"], updated.json()["sound_enabled"]),
            ("ivory", False),
        )

    def test_v21_revalidation_rejects_cross_game_corruption(self):
        other = self.store.create_game("Other", "Other DM", "human")
        self.store.dice_preferences(self.player)
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("DROP TRIGGER trg_dice_preferences_member_update")
            db.execute(
                """
                UPDATE member_dice_preferences SET game_id = ?
                WHERE member_id = ?
                """,
                (other["game_id"], self.player.member_id),
            )
            db.execute("DELETE FROM schema_migrations WHERE version = 21")
            db.commit()

        with self.assertRaisesRegex(
            RuntimeError, "dice preference metadata gecersiz"
        ):
            GameStore(self.path)

        with closing(sqlite3.connect(self.path)) as db:
            applied = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 21"
            ).fetchone()
        self.assertIsNone(applied)

    def test_patch_rate_limit_is_member_scoped(self):
        headers = {"Authorization": f"Bearer {self.player_token}"}
        for _ in range(30):
            response = self.client.get(
                "/api/me/dice-preferences", headers=headers
            )
            self.assertEqual(response.status_code, 200)
        responses = [
            self.client.patch(
                "/api/me/dice-preferences",
                headers=headers,
                json={
                    "theme": "crimson",
                    "sound_enabled": bool(index % 2),
                },
            )
            for index in range(31)
        ]
        self.assertTrue(all(response.status_code == 200 for response in responses[:30]))
        self.assertEqual(responses[30].status_code, 429)


if __name__ == "__main__":
    unittest.main()
