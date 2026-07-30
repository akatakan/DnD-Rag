import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.store import GameStore


class InventoryMigrationTest(unittest.TestCase):
    def prepare(self, path: Path, name: str):
        store = GameStore(path)
        created = store.create_game(name, "Morgan", "human")
        joined = store.join_game(created["invite_code"], "Riva")
        return store, created, joined

    def test_future_inventory_state_fails_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store, created, joined = self.prepare(path, "Future Inventory")
            state = store.game(created["game_id"])["state"]
            state["characters"][joined["character_id"]]["inventory_state"][
                "schema_version"
            ] = 2
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 13")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "daha yeni"):
                GameStore(path)

            with closing(sqlite3.connect(path)) as db:
                version = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 13"
                ).fetchone()
                stored = json.loads(
                    db.execute(
                        "SELECT state_json FROM games WHERE id = ?",
                        (created["game_id"],),
                    ).fetchone()[0]
                )
            self.assertIsNone(version)
            self.assertEqual(
                stored["characters"][joined["character_id"]]["inventory_state"][
                    "schema_version"
                ],
                2,
            )

    def test_malformed_current_inventory_state_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store, created, joined = self.prepare(path, "Malformed Inventory")
            state = store.game(created["game_id"])["state"]
            state["characters"][joined["character_id"]]["inventory_state"][
                "currency"
            ]["gp"] = -1
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 13")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "gecersiz"):
                GameStore(path)

            with closing(sqlite3.connect(path)) as db:
                version = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 13"
                ).fetchone()
            self.assertIsNone(version)

    def test_non_object_inventory_state_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store, created, joined = self.prepare(path, "Invalid Shape")
            state = store.game(created["game_id"])["state"]
            state["characters"][joined["character_id"]]["inventory_state"] = []
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 13")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "state gecersiz"):
                GameStore(path)

            with closing(sqlite3.connect(path)) as db:
                version = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 13"
                ).fetchone()
            self.assertIsNone(version)


if __name__ == "__main__":
    unittest.main()
