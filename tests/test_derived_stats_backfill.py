"""Migration 032: refreshing derived blocks that were frozen before a field existed.

A character's derived block is persisted inside games.state_json and is only
regenerated when the character mutates. Adding a derived value therefore does
nothing for characters that were already published: Passive Investigation and
Passive Insight shipped that way and rendered blank on every existing sheet.
The migration exists to close that gap, so what it must prove is that a stale
block is rebuilt and an untouched one is left alone.
"""

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.migrations import MIGRATIONS, apply_migrations
from api.realtime import ConnectionManager
from api.store import GameStore

# Same rules-valid build the enforcement suite uses as its control: Fighter 1,
# Human, Acolyte. Perception and Insight are proficient, Investigation is not.
VALID_SKILLS = ["arcana", "athletics", "insight", "perception", "religion"]
STALE_KEYS = ("passive_perception", "passive_investigation", "passive_insight")


def migration_version(name: str) -> int:
    return next(number for number, label, _ in MIGRATIONS if label == name)


class DerivedStatsBackfillTest(unittest.TestCase):
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
        self.game = self.client.post(
            "/api/games",
            json={"name": "Backfill", "dm_name": "DM", "dm_mode": "human"},
        ).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def publish(self):
        joined = self.client.post(
            "/api/games/join",
            json={"invite_code": self.game["invite_code"], "player_name": "Riva"},
        ).json()
        headers = {"Authorization": f"Bearer {joined['token']}"}
        route = f"/api/characters/{joined['character_id']}/draft"
        current = self.client.get(route, headers=headers).json()
        current = self.client.patch(
            route,
            headers=headers,
            json={
                "expected_revision": current["revision"],
                "patch": {"skill_proficiencies": VALID_SKILLS},
            },
        ).json()
        while current["current_step"] != "review":
            current = self.client.post(
                route + "/navigate",
                headers=headers,
                json={"expected_revision": current["revision"], "direction": "next"},
            ).json()
        published = self.client.post(
            "/api/commands",
            headers=headers,
            json={
                "type": "publish_character_draft",
                "payload": {
                    "character_id": joined["character_id"],
                    "draft_revision": current["revision"],
                },
                "client_action_id": "publish-backfill",
            },
        )
        self.assertEqual(published.status_code, 200, published.text)
        return joined["character_id"]

    def state(self, db):
        row = db.execute(
            "SELECT id, state_json FROM games WHERE id = ?", (self.game["game_id"],)
        ).fetchone()
        return json.loads(row["state_json"])

    def rewind_and_reapply(self, db):
        db.execute(
            "DELETE FROM schema_migrations WHERE version >= ?",
            (migration_version("refresh_derived_stats"),),
        )
        db.commit()
        apply_migrations(db)
        db.commit()

    def test_a_block_frozen_before_the_fields_existed_is_rebuilt(self):
        character_id = self.publish()
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            # Rewind the stored block to what a pre-032 row actually looks like.
            state = self.state(db)
            derived = state["characters"][character_id]["derived"]
            for key in STALE_KEYS:
                derived.pop(key, None)
            db.execute(
                "UPDATE games SET state_json = ? WHERE id = ?",
                (json.dumps(state, ensure_ascii=False), self.game["game_id"]),
            )
            db.commit()

            self.rewind_and_reapply(db)

            refreshed = self.state(db)["characters"][character_id]["derived"]
            # Wis 10 and Int 10 with a +2 proficiency bonus: proficient in
            # Perception and Insight, not in Investigation.
            self.assertEqual(refreshed["passive_perception"], 12)
            self.assertEqual(refreshed["passive_insight"], 12)
            self.assertEqual(refreshed["passive_investigation"], 10)
        finally:
            db.close()

    def test_an_already_current_block_is_left_untouched(self):
        character_id = self.publish()
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            before = self.state(db)["characters"][character_id]
            self.rewind_and_reapply(db)
            self.assertEqual(self.state(db)["characters"][character_id], before)
        finally:
            db.close()

    def test_a_game_the_engine_cannot_read_does_not_block_startup(self):
        # Startup applies migrations, so a single unreadable row must not be
        # able to take the whole deployment down with it.
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        try:
            db.execute(
                "UPDATE games SET state_json = ? WHERE id = ?",
                ('{"characters": {"x": {"broken": true}}}', self.game["game_id"]),
            )
            db.commit()
            self.rewind_and_reapply(db)
            self.assertEqual(
                self.state(db)["characters"]["x"], {"broken": True}
            )
        finally:
            db.close()


if __name__ == "__main__":
    unittest.main()
