import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.store import GameStore


class MultiplayerAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        self.client = TestClient(api_app.app)
        created = self.client.post(
            "/api/games",
            json={"name": "Test Game", "dm_name": "DM", "dm_mode": "assisted"},
        ).json()
        joined = self.client.post(
            "/api/games/join",
            json={"invite_code": created["invite_code"], "player_name": "Riva"},
        ).json()
        self.dm = created
        self.player = joined

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def command(self, token, command_type, payload=None):
        return self.client.post(
            "/api/commands",
            headers=self.auth(token),
            json={"type": command_type, "payload": payload or {}},
        )

    def test_player_damage_request_requires_dm_approval(self):
        before = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        character_id = self.player["character_id"]
        self.command(self.player["token"], "request_damage", {"amount": 4})
        unchanged = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        self.assertEqual(unchanged["state"]["characters"][character_id]["hp"], before["state"]["characters"][character_id]["hp"])

        dm_snapshot = self.client.get("/api/snapshot", headers=self.auth(self.dm["token"])).json()
        request_id = dm_snapshot["pending_requests"][0]["id"]
        response = self.command(self.dm["token"], "approve_request", {"request_id": request_id})
        self.assertEqual(response.status_code, 200)
        after = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        self.assertEqual(after["state"]["characters"][character_id]["hp"], 6)

    def test_player_cannot_use_dm_command(self):
        response = self.command(self.player["token"], "next_turn")
        self.assertEqual(response.status_code, 400)

    def test_player_snapshot_redacts_hidden_monsters_and_hp(self):
        self.command(self.dm["token"], "add_combatant", {"name": "Goblin", "initiative": 12, "hp": 7, "kind": "monster"})
        self.command(self.dm["token"], "add_combatant", {"name": "Hidden Imp", "initiative": 14, "hp": 10, "kind": "monster", "hidden": True})
        player_snapshot = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        self.assertEqual([item["name"] for item in player_snapshot["state"]["combatants"]], ["Goblin"])
        self.assertNotIn("hp", player_snapshot["state"]["combatants"][0])
        dm_snapshot = self.client.get("/api/snapshot", headers=self.auth(self.dm["token"])).json()
        self.assertEqual(len(dm_snapshot["state"]["combatants"]), 2)
        self.assertEqual(dm_snapshot["state"]["combatants"][0]["hp"], 7)

    def test_private_roll_is_not_visible_to_other_player(self):
        second = self.client.post(
            "/api/games/join",
            json={"invite_code": self.dm["invite_code"], "player_name": "Gareth"},
        ).json()
        self.command(self.player["token"], "roll", {"expression": "1d20", "visibility": "dm_only"})
        first_events = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()["events"]
        second_events = self.client.get("/api/snapshot", headers=self.auth(second["token"])).json()["events"]
        dm_events = self.client.get("/api/snapshot", headers=self.auth(self.dm["token"])).json()["events"]
        self.assertTrue(any(event["type"] == "dice_rolled" for event in first_events))
        self.assertFalse(any(event["type"] == "dice_rolled" for event in second_events))
        self.assertTrue(any(event["type"] == "dice_rolled" for event in dm_events))

    def test_websocket_receives_role_specific_snapshot(self):
        with self.client.websocket_connect(
            f"/ws/games/{self.player['game_id']}?token={self.player['token']}"
        ) as websocket:
            message = websocket.receive_json()
            self.assertEqual(message["kind"], "snapshot")
            self.assertEqual(message["snapshot"]["me"]["role"], "player")


if __name__ == "__main__":
    unittest.main()
