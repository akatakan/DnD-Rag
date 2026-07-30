import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from api.store import GameStore


class CharacterDraftMigrationTest(unittest.TestCase):
    def test_v26_keeps_dm_ready_and_new_player_incomplete(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Creation Gate", "DM", "human")
            joined = store.join_game(created["invite_code"], "Riva")

            with closing(sqlite3.connect(path)) as db:
                db.row_factory = sqlite3.Row
                members = db.execute(
                    """
                    SELECT role, character_ready FROM members
                    WHERE game_id = ? ORDER BY role
                    """,
                    (created["game_id"],),
                ).fetchall()
                draft = db.execute(
                    """
                    SELECT schema_version, status, draft_json
                    FROM character_drafts
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (created["game_id"], joined["character_id"]),
                ).fetchone()

            self.assertEqual(
                {(row["role"], row["character_ready"]) for row in members},
                {("dm", 1), ("player", 0)},
            )
            self.assertEqual(draft["schema_version"], 2)
            self.assertEqual(draft["status"], "active")
            self.assertEqual(
                json.loads(draft["draft_json"])["ability_score_method"],
                "standard_array",
            )

    def test_v26_requires_unpublished_existing_draft_only(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Existing Creation Gate", "DM", "human")
            incomplete = store.join_game(created["invite_code"], "Riva")
            complete = store.join_game(created["invite_code"], "Gareth")
            complete_draft = store.character_draft(
                created["game_id"], complete["character_id"]
            )
            store.mark_character_draft_published(
                created["game_id"],
                complete["character_id"],
                complete_draft["revision"],
            )

            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    "UPDATE members SET character_ready = 1 WHERE role = 'player'"
                )
                db.execute(
                    "DELETE FROM schema_migrations WHERE version = 26"
                )
                db.commit()

            GameStore(path)
            with closing(sqlite3.connect(path)) as db:
                rows = db.execute(
                    """
                    SELECT character_id, character_ready
                    FROM members WHERE game_id = ? AND role = 'player'
                    """,
                    (created["game_id"],),
                ).fetchall()
            readiness = dict(rows)
            self.assertEqual(readiness[incomplete["character_id"]], 0)
            self.assertEqual(readiness[complete["character_id"]], 1)

    def test_v15_revalidation_fails_closed_for_corrupt_draft(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Draft Migration", "DM", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            character = store.game(created["game_id"])["state"]["characters"][
                joined["character_id"]
            ]
            with store.transaction():
                store.create_character_draft(created["game_id"], character)
            with closing(sqlite3.connect(path)) as db:
                db.execute(
                    """
                    UPDATE character_drafts SET draft_json = ?
                    WHERE game_id = ? AND character_id = ?
                    """,
                    ("{corrupt", created["game_id"], joined["character_id"]),
                )
                db.execute("DELETE FROM schema_migrations WHERE version = 15")
                db.commit()

            with self.assertRaisesRegex(RuntimeError, "draft gecersiz"):
                GameStore(path)

            with closing(sqlite3.connect(path)) as db:
                applied = db.execute(
                    "SELECT 1 FROM schema_migrations WHERE version = 15"
                ).fetchone()
                stored = db.execute(
                    """
                    SELECT draft_json FROM character_drafts
                    WHERE game_id = ? AND character_id = ?
                    """,
                    (created["game_id"], joined["character_id"]),
                ).fetchone()[0]
            self.assertIsNone(applied)
            self.assertEqual(stored, "{corrupt")

    def test_v15_owner_trigger_rejects_cross_game_member(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            first = store.create_game("First", "DM One", "human")
            first_player = store.join_game(first["invite_code"], "Riva")
            second = store.create_game("Second", "DM Two", "human")
            second_player = store.join_game(second["invite_code"], "Gareth")
            character = store.game(first["game_id"])["state"]["characters"][
                first_player["character_id"]
            ]
            data = store.character_draft_engine.from_character(character)

            with self.assertRaises(sqlite3.IntegrityError):
                with store.connect() as db:
                    db.execute(
                        """
                        INSERT INTO character_drafts (
                            game_id, character_id, owner_id, schema_version,
                            draft_json, current_step, revision, status,
                            created_at, updated_at, published_at
                        ) VALUES (?, ?, ?, 1, ?, 'basics', 1, 'active',
                                  'now', 'now', NULL)
                        """,
                        (
                            first["game_id"],
                            first_player["character_id"],
                            second_player["member_id"],
                            json.dumps(data),
                        ),
                    )


if __name__ == "__main__":
    unittest.main()
