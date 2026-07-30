import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.store import GameStore


class EncounterMigrationTest(unittest.TestCase):
    def test_v18_revalidation_rolls_back_for_corrupt_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Migration", "DM", "human")
            auth = store.authenticate(created["token"])
            game = store.game(created["game_id"])
            with store.transaction():
                encounter = store.create_encounter_draft(
                    game["campaign_id"], auth.member_id, "Ambush"
                )
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE encounter_drafts SET draft_json = ? WHERE id = ?",
                    ("{corrupt", encounter["id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 18")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "encounter draft gecersiz"):
                GameStore(path)

            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 18"
                ).fetchone()
                stored = db.execute(
                    "SELECT draft_json FROM encounter_drafts WHERE id = ?",
                    (encounter["id"],),
                ).fetchone()[0]
            self.assertIsNone(applied)
            self.assertEqual(stored, "{corrupt")

    def test_creator_campaign_trigger_rejects_cross_game_member(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            second = store.create_game("Second", "DM Two", "human")
            data = store.encounter_engine.create("Ambush")
            with self.assertRaises(sqlite3.IntegrityError):
                store.create_encounter_draft(
                    first["campaign_id"],
                    second["member_id"],
                    data["name"],
                    data=data,
                )

    def test_v18_rejects_cross_campaign_active_draft_reference(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            second = store.create_game("Second", "DM Two", "human")
            second_auth = store.authenticate(second["token"])
            with store.transaction():
                encounter = store.create_encounter_draft(
                    second["campaign_id"], second_auth.member_id, "Foreign"
                )
            first_game = store.game(first["game_id"])
            state = first_game["state"]
            state["active_encounter_id"] = encounter["id"]
            state["active_encounter_revision"] = encounter["revision"]
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), first["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 18")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "active reference gecersiz"):
                GameStore(path)

            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 18"
                ).fetchone()
            self.assertIsNone(applied)


if __name__ == "__main__":
    unittest.main()
