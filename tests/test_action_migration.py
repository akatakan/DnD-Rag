import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.store import GameStore


class ActionMigrationTest(unittest.TestCase):
    def test_v14_backfills_and_audits_action_state(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Actions", "DM", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            with closing(sqlite3.connect(path)) as db:
                state = json.loads(
                    db.execute(
                        "SELECT state_json FROM games WHERE id = ?",
                        (created["game_id"],),
                    ).fetchone()[0]
                )
                state["characters"][joined["character_id"]].pop("action_state")
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 14")
                db.execute("DROP TABLE character_action_history")
                db.commit()
            migrated = GameStore(path).game(created["game_id"])
            character = migrated["state"]["characters"][joined["character_id"]]
            self.assertEqual(character["action_state"]["schema_version"], 1)
            with closing(sqlite3.connect(path)) as db:
                history = db.execute(
                    """
                    SELECT from_version, to_version
                    FROM character_action_history
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (created["game_id"], joined["character_id"]),
                ).fetchone()
            self.assertEqual(history, (0, 1))

    def test_v14_fails_closed_for_future_or_non_object_state(self):
        for invalid in ({"schema_version": 99}, "corrupt"):
            with self.subTest(invalid=invalid), tempfile.TemporaryDirectory() as temp:
                path = Path(temp) / "game.db"
                store = GameStore(path)
                created = store.create_game("Actions", "DM", "human")
                joined = store.join_game(created["invite_code"], "Riva")
                with closing(sqlite3.connect(path)) as db:
                    state = json.loads(
                        db.execute(
                            "SELECT state_json FROM games WHERE id = ?",
                            (created["game_id"],),
                        ).fetchone()[0]
                    )
                    state["characters"][joined["character_id"]]["action_state"] = invalid
                    db.execute(
                        "UPDATE games SET state_json = ? WHERE id = ?",
                        (json.dumps(state), created["game_id"]),
                    )
                    db.execute("DELETE FROM schema_migrations WHERE version = 14")
                    db.commit()
                with self.assertRaises(RuntimeError):
                    GameStore(path)
                with closing(sqlite3.connect(path)) as db:
                    applied = db.execute(
                        "SELECT 1 FROM schema_migrations WHERE version = 14"
                    ).fetchone()
                self.assertIsNone(applied)

    def test_v14_rolls_back_current_state_with_unknown_fields(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Actions", "DM", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            with closing(sqlite3.connect(path)) as db:
                state = json.loads(
                    db.execute(
                        "SELECT state_json FROM games WHERE id = ?",
                        (created["game_id"],),
                    ).fetchone()[0]
                )
                state["characters"][joined["character_id"]]["action_state"][
                    "injected"
                ] = True
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 14")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "action state gecersiz"):
                GameStore(path)

            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 14"
                ).fetchone()
                stored = json.loads(
                    db.execute(
                        "SELECT state_json FROM games WHERE id = ?",
                        (created["game_id"],),
                    ).fetchone()[0]
                )
            self.assertIsNone(applied)
            self.assertTrue(
                stored["characters"][joined["character_id"]]["action_state"][
                    "injected"
                ]
            )


if __name__ == "__main__":
    unittest.main()
