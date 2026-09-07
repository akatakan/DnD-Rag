"""DM-authored NPCs and how they reach an encounter.

NPCs are written by the DM, not imported from a ruleset, so nothing here is
bound to catalog schema or source provenance. The point of storing them is
that a stat block typed once cannot drift from the one the table agreed on,
so adding an NPC to an encounter must come from the record, not the caller.
"""

import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.store import GameStore

GOBLIN = {
    "name": "Goblin Nişancı",
    "kind": "monster",
    "armor_class": 15,
    "max_hp": 7,
    "initiative_modifier": 2,
    "speed": 30,
    "notes": "Kısa yay taşır.",
}


class CampaignNpcTest(unittest.TestCase):
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
            json={"name": "NPC", "dm_name": "DM", "dm_mode": "human"},
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

    def create(self, token=None, **overrides):
        return self.client.post(
            "/api/npcs",
            headers=self.auth(token or self.dm["token"]),
            json={**GOBLIN, **overrides},
        )

    def command(self, token, kind, payload):
        return self.client.post(
            "/api/commands",
            headers=self.auth(token),
            json={"type": kind, "payload": payload, "client_action_id": uuid4().hex},
        )

    def test_the_dm_creates_lists_edits_and_deletes_an_npc(self):
        created = self.create()
        self.assertEqual(created.status_code, 201, created.text)
        npc = created.json()
        self.assertEqual(npc["armor_class"], 15)

        listed = self.client.get("/api/npcs", headers=self.auth(self.dm["token"]))
        self.assertEqual([item["id"] for item in listed.json()["npcs"]], [npc["id"]])

        edited = self.client.patch(
            f"/api/npcs/{npc['id']}",
            headers=self.auth(self.dm["token"]),
            json={**GOBLIN, "max_hp": 12},
        )
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual(edited.json()["max_hp"], 12)

        removed = self.client.delete(
            f"/api/npcs/{npc['id']}", headers=self.auth(self.dm["token"])
        )
        self.assertEqual(removed.status_code, 204, removed.text)
        self.assertEqual(
            self.client.get("/api/npcs", headers=self.auth(self.dm["token"])).json()["npcs"],
            [],
        )

    def test_a_player_can_neither_read_nor_write_the_library(self):
        listed = self.client.get("/api/npcs", headers=self.auth(self.player["token"]))
        self.assertEqual(listed.status_code, 403, listed.text)
        created = self.create(token=self.player["token"])
        self.assertEqual(created.status_code, 403, created.text)

    def test_two_npcs_cannot_share_a_name(self):
        self.assertEqual(self.create().status_code, 201)
        clash = self.create()
        self.assertEqual(clash.status_code, 409, clash.text)
        # A different name is fine, and renaming back onto a taken one is not.
        other = self.create(name="Goblin Şef")
        self.assertEqual(other.status_code, 201, other.text)
        renamed = self.client.patch(
            f"/api/npcs/{other.json()['id']}",
            headers=self.auth(self.dm["token"]),
            json={**GOBLIN, "name": "Goblin Nişancı"},
        )
        self.assertEqual(renamed.status_code, 409, renamed.text)

    def test_an_npc_joins_an_encounter_with_its_own_stats(self):
        npc = self.create().json()
        added = self.command(
            self.dm["token"], "add_combatant", {"npc_id": npc["id"]}
        )
        self.assertEqual(added.status_code, 200, added.text)
        snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        combatant = snapshot["state"]["combatants"][0]
        self.assertEqual(combatant["name"], "Goblin Nişancı")
        self.assertEqual(combatant["kind"], "monster")
        self.assertEqual(combatant["max_hp"], 7)
        self.assertEqual(combatant["source"], {"type": "npc", "id": npc["id"]})
        # Initiative is rolled from the stored modifier: 1d20+2.
        self.assertTrue(3 <= combatant["initiative"] <= 22, combatant["initiative"])

    def test_the_caller_may_not_rename_an_npc_while_adding_it(self):
        npc = self.create().json()
        refused = self.command(
            self.dm["token"],
            "add_combatant",
            {"npc_id": npc["id"], "name": "Baska Ad"},
        )
        # Payload shape is rejected by the request model, so this is a 422.
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("ad NPC kaydindan gelir", refused.text)

    def test_an_unknown_npc_is_not_found(self):
        missing = self.command(
            self.dm["token"], "add_combatant", {"npc_id": "does-not-exist"}
        )
        self.assertIn(missing.status_code, (400, 404), missing.text)

    def test_a_manual_combatant_still_works(self):
        added = self.command(
            self.dm["token"],
            "add_combatant",
            {"name": "Elle eklenen", "hp": 5, "initiative": 3},
        )
        self.assertEqual(added.status_code, 200, added.text)
        snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        self.assertEqual(
            snapshot["state"]["combatants"][0]["source"],
            {"type": "manual", "id": None},
        )


if __name__ == "__main__":
    unittest.main()
