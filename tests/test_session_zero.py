import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.store import GameStore


class SessionZeroTest(unittest.TestCase):
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
        self.dm = self.client.post(
            "/api/games",
            json={"name": "Session Zero", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.player = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Riva",
            },
        ).json()
        self.other = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Other",
            },
        ).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def test_settings_readiness_schedule_and_privacy(self):
        dm_snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        settings = self.client.patch(
            "/api/campaigns/current/settings",
            headers=self.auth(self.dm["token"]),
            json={
                "expected_version": dm_snapshot["campaign"]["settings_version"],
                "house_rules": [{
                    "id": "rule-flanking",
                    "title": "Flanking",
                    "description": "Advantage yok.",
                    "enabled": True,
                }],
                "safety_tools": ["x_card", "open_door"],
                "session_zero_agenda": ["Ton ve tema", "Karakter bağları"],
            },
        )
        self.assertEqual(settings.status_code, 200, settings.text)
        player_lobby = self.client.get(
            "/api/campaigns/current/lobby",
            headers=self.auth(self.player["token"]),
        ).json()
        own = next(
            member for member in player_lobby["members"]
            if member["member_id"] == self.player["member_id"]
        )
        updated = self.client.patch(
            "/api/campaigns/current/session-zero",
            headers=self.auth(self.player["token"]),
            json={
                "expected_version": own["readiness_version"],
                "readiness_status": "ready",
                "consent_status": "accepted",
                "lines": ["Body horror"],
                "veils": ["Romance"],
                "notes": "Private note",
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        stale = self.client.patch(
            "/api/campaigns/current/session-zero",
            headers=self.auth(self.player["token"]),
            json={
                "expected_version": own["readiness_version"],
                "readiness_status": "not_ready",
                "consent_status": "accepted",
            },
        )
        self.assertEqual(stale.status_code, 409)
        other_lobby = self.client.get(
            "/api/campaigns/current/lobby",
            headers=self.auth(self.other["token"]),
        ).json()
        player_as_seen_by_other = next(
            member for member in other_lobby["members"]
            if member["member_id"] == self.player["member_id"]
        )
        self.assertNotIn("safety_preferences", player_as_seen_by_other)
        dm_lobby = self.client.get(
            "/api/campaigns/current/lobby",
            headers=self.auth(self.dm["token"]),
        ).json()
        player_as_seen_by_dm = next(
            member for member in dm_lobby["members"]
            if member["member_id"] == self.player["member_id"]
        )
        self.assertEqual(
            player_as_seen_by_dm["safety_preferences"]["notes"], "Private note"
        )
        events = self.client.get(
            "/api/events?after=0&limit=100",
            headers=self.auth(self.other["token"]),
        ).json()["events"]
        event = next(
            item for item in events
            if item["type"] == "session_zero_member_updated"
        )
        self.assertNotIn("lines", event["payload"])
        self.assertNotIn("notes", event["payload"])

        fresh = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        scheduled = self.client.patch(
            "/api/sessions/schedule",
            headers=self.auth(self.dm["token"]),
            json={
                "expected_revision": fresh["revision"],
                "scheduled_at": "2026-08-01T20:00:00+03:00",
            },
        )
        self.assertEqual(scheduled.status_code, 200, scheduled.text)
        self.assertIsNotNone(scheduled.json()["session"]["scheduled_at"])
        forbidden = self.client.patch(
            "/api/campaigns/current/settings",
            headers=self.auth(self.player["token"]),
            json={
                "expected_version": settings.json()["campaign"]["settings_version"],
                "house_rules": [],
                "safety_tools": [],
                "session_zero_agenda": [],
            },
        )
        self.assertEqual(forbidden.status_code, 400)

    def test_ready_requires_consent(self):
        response = self.client.patch(
            "/api/campaigns/current/session-zero",
            headers=self.auth(self.player["token"]),
            json={
                "expected_version": 1,
                "readiness_status": "ready",
                "consent_status": "pending",
            },
        )
        self.assertEqual(response.status_code, 422)
        with self.assertRaisesRegex(ValueError, "Session Zero onayi"):
            self.store.update_session_zero_member(
                self.player["game_id"],
                self.player["member_id"],
                1,
                "ready",
                "pending",
                {},
            )

    def test_duplicate_settings_and_naive_schedule_are_rejected(self):
        duplicate = self.client.patch(
            "/api/campaigns/current/settings",
            headers=self.auth(self.dm["token"]),
            json={
                "expected_version": 1,
                "house_rules": [
                    {
                        "id": "same",
                        "title": "One",
                        "description": "",
                        "enabled": True,
                    },
                    {
                        "id": "same",
                        "title": "Two",
                        "description": "",
                        "enabled": True,
                    },
                ],
                "safety_tools": ["x_card", "x_card"],
                "session_zero_agenda": [],
            },
        )
        self.assertEqual(duplicate.status_code, 422)
        snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        naive = self.client.patch(
            "/api/sessions/schedule",
            headers=self.auth(self.dm["token"]),
            json={
                "expected_revision": snapshot["revision"],
                "scheduled_at": "2026-08-01T20:00:00",
            },
        )
        self.assertEqual(naive.status_code, 422)

    def test_v16_corrupt_settings_rolls_back(self):
        self.client.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE campaigns SET settings_json = ?",
                (json.dumps(["not-object"]),),
            )
            db.execute("DELETE FROM schema_migrations WHERE version = 16")
            db.commit()
        with self.assertRaises(RuntimeError):
            GameStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            applied = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 16"
            ).fetchone()
        self.assertIsNone(applied)


if __name__ == "__main__":
    unittest.main()
