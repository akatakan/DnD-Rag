import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.store import GameStore


class CharacterMigrationTest(unittest.TestCase):
    def test_v8_legacy_character_is_backfilled_without_changing_hp_or_ac(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Legacy Character", "Morgan", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            game = store.game(created["game_id"])
            state = game["state"]
            state["characters"][joined["character_id"]] = {
                "id": joined["character_id"],
                "owner_id": joined["member_id"],
                "name": "Riva",
                "class_name": "Wizard of the Old Tower",
                "level": 3,
                "ac": 17,
                "max_hp": 23,
                "hp": 20,
                "temp_hp": 4,
                "conditions": ["Blinded"],
                "inventory": ["Shield"],
            }
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ?, state_revision = 8 WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version >= 9")
                db.execute("DROP TABLE character_schema_history")
                db.execute("DROP TABLE character_resource_history")
                db.commit()

            migrated_store = GameStore(path)
            migrated_game = migrated_store.game(created["game_id"])
            character = migrated_game["state"]["characters"][joined["character_id"]]

            self.assertEqual(character["schema_version"], 2)
            self.assertIsNone(character["class_id"])
            self.assertEqual(
                character["legacy_class_name"], "Wizard of the Old Tower"
            )
            self.assertEqual(character["class_name"], "Wizard of the Old Tower")
            self.assertEqual(character["derived"]["armor_class"], 17)
            self.assertEqual(character["derived"]["max_hp"], 23)
            self.assertEqual(character["hp"], 20)
            self.assertEqual(character["temp_hp"], 4)
            self.assertEqual(character["conditions"], ["Blinded"])
            self.assertEqual(character["inventory"], ["Shield"])
            self.assertEqual(character["resource_state"]["schema_version"], 2)
            self.assertEqual(
                character["effects"]["conditions"][0]["name"], "Blinded"
            )
            self.assertEqual(migrated_game["state_revision"], 12)
            inventory_entries = list(
                character["inventory_state"]["entries"].values()
            )
            self.assertEqual(len(inventory_entries), 1)
            self.assertEqual(inventory_entries[0]["catalog_id"], "item:shield")
            self.assertEqual(inventory_entries[0]["unit_weight_lb"], 6)
            with closing(sqlite3.connect(path)) as db:
                history = db.execute(
                    """
                    SELECT from_version, to_version
                    FROM character_schema_history
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (created["game_id"], joined["character_id"]),
                ).fetchone()
            self.assertEqual(history, (1, 2))
            with closing(sqlite3.connect(path)) as db:
                resource_history = db.execute(
                    """
                    SELECT from_version, to_version
                    FROM character_resource_history
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (created["game_id"], joined["character_id"]),
                ).fetchone()
            self.assertEqual(resource_history, (0, 2))
            with closing(sqlite3.connect(path)) as db:
                inventory_history = db.execute(
                    """
                    SELECT from_version, to_version
                    FROM character_inventory_history
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (created["game_id"], joined["character_id"]),
                ).fetchone()
            self.assertEqual(inventory_history, (0, 1))

            revision = migrated_game["state_revision"]
            reopened = GameStore(path)
            self.assertEqual(
                reopened.game(created["game_id"])["state_revision"], revision
            )
            with closing(sqlite3.connect(path)) as db:
                history_count = db.execute(
                    "SELECT COUNT(*) FROM character_schema_history"
                ).fetchone()[0]
            self.assertEqual(history_count, 1)

    def test_future_resource_state_fails_closed_without_recording_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Future", "Morgan", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            game = store.game(created["game_id"])
            state = game["state"]
            state["characters"][joined["character_id"]]["resource_state"][
                "schema_version"
            ] = 3
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version >= 10")
                db.commit()
            with self.assertRaisesRegex(RuntimeError, "daha yeni"):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                version = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 10"
                ).fetchone()
                stored = json.loads(
                    db.execute(
                        "SELECT state_json FROM games WHERE id = ?",
                        (created["game_id"],),
                    ).fetchone()[0]
                )
            self.assertIsNone(version)
            self.assertEqual(
                stored["characters"][joined["character_id"]]["resource_state"][
                    "schema_version"
                ],
                3,
            )

    def test_malformed_current_resource_state_rolls_back_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Malformed", "Morgan", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            game = store.game(created["game_id"])
            state = game["state"]
            del state["characters"][joined["character_id"]]["effects"]
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version >= 10")
                db.commit()
            with self.assertRaisesRegex(RuntimeError, "gecersiz"):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                version = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 10"
                ).fetchone()
            self.assertIsNone(version)

    def test_v11_upgrades_development_v1_death_save_turn_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("V1 Resource", "Morgan", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            state = store.game(created["game_id"])["state"]
            state["encounter_status"] = "active"
            del state["turn_serial"]
            resource = state["characters"][joined["character_id"]]["resource_state"]
            resource["schema_version"] = 1
            saves = resource["death_saves"]
            del saves["last_rolled_turn"]
            saves["last_rolled_round"] = 3
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 11")
                db.commit()

            upgraded = GameStore(path).game(created["game_id"])["state"]
            character = upgraded["characters"][joined["character_id"]]
            self.assertEqual(upgraded["turn_serial"], 1)
            self.assertEqual(character["resource_state"]["schema_version"], 2)
            self.assertEqual(
                character["resource_state"]["death_saves"]["last_rolled_turn"], 1
            )
            with closing(sqlite3.connect(path)) as db:
                history = db.execute(
                    """
                    SELECT from_version, to_version
                    FROM character_resource_history
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (created["game_id"], joined["character_id"]),
                ).fetchone()
            self.assertEqual(history, (1, 2))


if __name__ == "__main__":
    unittest.main()
