import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.encounter_engine import EncounterStorageError
from api.game_engine import GameEngine
from api.models import CommandRequest
from api.store import GameStore


class AdvancedEncounterMigrationTest(unittest.TestCase):
    def test_v19_rejects_corrupt_tie_breaker_without_recording_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Tie Migration", "DM", "human")
            state = store.game(created["game_id"])["state"]
            state["combatants"] = [{
                "id": "corrupt-tie-entry",
                "name": "Corrupt",
                "kind": "npc",
                "initiative": 10,
                "tie_breaker": "first",
                "hp": 5,
            }]
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE games SET state_json = ? WHERE id = ?",
                    (json.dumps(state), created["game_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 19")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "tie breaker gecersiz"):
                GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 19"
                ).fetchone()
            self.assertIsNone(applied)

    def test_corrupt_undo_state_fails_closed_and_is_not_consumed(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Undo", "DM", "human")
            auth = store.authenticate(created["token"])
            engine = GameEngine(store)
            engine.apply(
                auth,
                CommandRequest(
                    type="add_combatant",
                    payload={
                        "name": "Goblin", "initiative": 10,
                        "hp": 7, "kind": "monster",
                    },
                    client_action_id="undo-corrupt-add",
                ),
            )
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    """
                    UPDATE encounter_undo_history SET state_json = ?
                    WHERE game_id = ?
                    """,
                    ("{corrupt", created["game_id"]),
                )
                db.commit()

            with self.assertRaises(EncounterStorageError):
                engine.apply(
                    auth,
                    CommandRequest(
                        type="undo_encounter",
                        client_action_id="undo-corrupt-attempt",
                    ),
                )
            self.assertEqual(store.encounter_undo_count(created["game_id"]), 1)

    def test_undo_actor_update_trigger_rejects_cross_game_member(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            second = store.create_game("Second", "DM Two", "human")
            first_auth = store.authenticate(first["token"])
            GameEngine(store).apply(
                first_auth,
                CommandRequest(
                    type="add_combatant",
                    payload={
                        "name": "Goblin",
                        "initiative": 10,
                        "hp": 7,
                        "kind": "monster",
                    },
                    client_action_id="undo-trigger-add",
                ),
            )
            with closing(sqlite3.connect(path)) as db:
                with self.assertRaises(sqlite3.IntegrityError):
                    db.execute(
                        """
                        UPDATE encounter_undo_history SET actor_id = ?
                        WHERE game_id = ?
                        """,
                        (second["member_id"], first["game_id"]),
                    )


if __name__ == "__main__":
    unittest.main()
