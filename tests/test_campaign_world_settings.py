"""DM setting notes and the subclass permission.

Both live in campaign settings, which stay DM-side as a whole. These two are
written for players and are needed while building a character, so they are the
only ones the player snapshot exposes.
"""

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.store import GameStore


class CampaignWorldSettingsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        self.dm = self.client.post(
            "/api/games",
            json={"name": "Ravenloft", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.player = self.client.post(
            "/api/games/join",
            json={"invite_code": self.dm["invite_code"], "player_name": "Riva"},
        ).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def snapshot(self, token):
        return self.client.get("/api/snapshot", headers=self.auth(token)).json()

    def settings(self, version, **overrides):
        body = {
            "expected_version": version,
            "house_rules": [],
            "safety_tools": [],
            "session_zero_agenda": [],
        }
        body.update(overrides)
        return self.client.patch(
            "/api/campaigns/current/settings",
            headers=self.auth(self.dm["token"]),
            json=body,
        )

    def test_a_new_campaign_starts_with_notes_empty_and_subclasses_allowed(self):
        campaign = self.snapshot(self.player["token"])["campaign"]
        self.assertEqual(campaign["world_notes"], "")
        self.assertTrue(campaign["allow_subclasses"])

    def test_the_dm_note_reaches_the_player_building_a_character(self):
        note = "Barovia'da güneş yoktur. Dışarıda geceleri yolculuk etmeyin."
        updated = self.settings(1, world_notes=note)
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertEqual(
            self.snapshot(self.player["token"])["campaign"]["world_notes"], note
        )

    def test_subclass_permission_can_be_withdrawn_and_restored_mid_campaign(self):
        closed = self.settings(1, allow_subclasses=False)
        self.assertEqual(closed.status_code, 200, closed.text)
        self.assertFalse(
            self.snapshot(self.player["token"])["campaign"]["allow_subclasses"]
        )
        version = closed.json()["campaign"]["settings_version"]
        reopened = self.settings(version, allow_subclasses=True)
        self.assertEqual(reopened.status_code, 200, reopened.text)
        self.assertTrue(
            self.snapshot(self.player["token"])["campaign"]["allow_subclasses"]
        )

    def test_the_rest_of_the_settings_stay_out_of_the_player_snapshot(self):
        self.settings(1, world_notes="görünür", session_zero_agenda=["gizli"])
        campaign = self.snapshot(self.player["token"])["campaign"]
        self.assertNotIn("settings", campaign)
        self.assertNotIn("session_zero_agenda", campaign)
        self.assertNotIn("house_rules", campaign)

    def test_a_campaign_saved_before_these_keys_existed_still_reads(self):
        # Nothing rewrites old rows on upgrade, so the read path must default.
        campaign_id = self.store.campaign_for_game(self.dm["game_id"])["id"]
        with self.store.connect() as db:
            db.execute(
                "UPDATE campaigns SET settings_json = ? WHERE id = ?",
                ('{"schema_version": 1, "house_rules": []}', campaign_id),
            )
        campaign = self.snapshot(self.player["token"])["campaign"]
        self.assertEqual(campaign["world_notes"], "")
        self.assertTrue(campaign["allow_subclasses"])

    def test_an_oversized_note_is_refused(self):
        refused = self.settings(1, world_notes="x" * 4001)
        self.assertEqual(refused.status_code, 422, refused.text)


if __name__ == "__main__":
    unittest.main()
