"""A player character joins initiative as itself, not as a typed copy.

Both doors into the initiative order used to get this wrong in different
ways: the DM console sent a flat initiative of 10 from the client, and a
saved encounter draft copied the bare Dexterity modifier. Either way a
player's roll never happened, while NPCs got a real 1d20 plus their
modifier. Both doors are covered here.

Initiative is a roll, and rolls belong to the engine. What the client sends
now is identity; everything else comes from the sheet.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.store import GameStore

VALID_SKILLS = ["arcana", "athletics", "insight", "perception", "religion"]


class CharacterInInitiativeTest(unittest.TestCase):
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
            json={"name": "Initiative", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.character_id = self.publish()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def publish(self):
        joined = self.client.post(
            "/api/games/join",
            json={"invite_code": self.dm["invite_code"], "player_name": "Riva"},
        ).json()
        self.player = joined
        headers = self.auth(joined["token"])
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
                "client_action_id": "publish-initiative-case",
            },
        )
        self.assertEqual(published.status_code, 200, published.text)
        return joined["character_id"]

    def snapshot(self, token=None):
        return self.client.get(
            "/api/snapshot", headers=self.auth(token or self.dm["token"])
        ).json()

    def command(self, kind, payload, token=None):
        return self.client.post(
            "/api/commands",
            headers=self.auth(token or self.dm["token"]),
            json={
                "type": kind,
                "payload": payload,
                "client_action_id": uuid4().hex,
            },
        )

    def add_character(self, **extra):
        return self.command(
            "add_combatant", {"character_id": self.character_id, **extra}
        )

    def combatant(self):
        return self.snapshot()["state"]["combatants"][0]

    def test_the_character_joins_under_its_own_identity(self):
        added = self.add_character()
        self.assertEqual(added.status_code, 200, added.text)
        combatant = self.combatant()
        self.assertEqual(combatant["id"], self.character_id)
        self.assertEqual(combatant["name"], "Riva")
        self.assertEqual(combatant["kind"], "player")
        self.assertEqual(
            combatant["source"], {"type": "character", "id": self.character_id}
        )

    def test_initiative_is_rolled_from_the_characters_own_modifier(self):
        # Sampling real rolls proves almost nothing here: with Dex +2 a dropped
        # modifier still lands inside a plausible range most of the time. So
        # pin the dice down and read the expression the engine asked for.
        modifier = self.snapshot()["state"]["characters"][self.character_id][
            "derived"
        ]["initiative"]
        self.assertEqual(modifier, 2, "fixture drifted; Riva should be Dex +2")

        # 57 is unreachable by any real 1d20+2, so a patch that failed to take
        # cannot be mistaken for a pass.
        with patch(
            "api.encounter_engine.roll",
            return_value=SimpleNamespace(total=57),
        ) as rolled:
            self.assertEqual(self.add_character().status_code, 200)

        rolled.assert_called_once_with("1d20+2")
        self.assertEqual(self.combatant()["initiative"], 57)

    def test_the_caller_may_not_rename_a_character_while_adding_it(self):
        refused = self.add_character(name="Baska Ad")
        self.assertEqual(refused.status_code, 422, refused.text)
        self.assertIn("ad kaydin kendisinden gelir", refused.text)

    def test_a_combatant_cannot_be_both_npc_and_character(self):
        refused = self.command(
            "add_combatant", {"character_id": self.character_id, "npc_id": "x"}
        )
        self.assertEqual(refused.status_code, 422, refused.text)

    def test_the_dm_may_still_override_initiative(self):
        # Surprise rounds and house rules need a way in; the roll is the
        # default, not a cage. 47 is outside the 3..22 a real 1d20+2 can
        # produce, so an ignored override cannot pass by luck.
        self.assertEqual(self.add_character(initiative=47).status_code, 200)
        self.assertEqual(self.combatant()["initiative"], 47)

    def test_an_explicit_initiative_does_not_roll_a_die_at_all(self):
        # `payload.get("initiative", rolled_initiative(character))` evaluated
        # the default eagerly, so an override still rolled and threw the result
        # away. The number came out right either way, which is why this needs
        # its own test: a discarded roll is still a roll, and it moves whatever
        # RNG the engine is asked to share or seed.
        with (
            patch("api.game_engine.rolled_initiative") as rolled_initiative,
            patch("api.encounter_engine.roll") as die,
        ):
            self.assertEqual(
                self.add_character(initiative=47).status_code, 200
            )

        rolled_initiative.assert_not_called()
        die.assert_not_called()
        self.assertEqual(self.combatant()["initiative"], 47)

    def test_a_saved_draft_rolls_the_characters_initiative_too(self):
        # The draft stores a placeholder slot; the real values -- initiative
        # included -- are filled in at start. Before the fix this path copied
        # the bare modifier, so every player character entered a saved
        # encounter at Dexterity and lost the roll the DM console gave them.
        created = self.command(
            "create_encounter_draft", {"name": "Kopru Pususu"}
        )
        self.assertEqual(created.status_code, 200, created.text)
        encounter_id = created.json()["event"]["payload"]["encounter_id"]
        updated = self.command(
            "update_encounter_draft",
            {
                "encounter_id": encounter_id,
                "draft_revision": 1,
                "patch": {
                    "combatants": [{
                        "id": "character-riva-slot",
                        "source": {
                            "type": "character",
                            "id": self.character_id,
                        },
                        "name": "Riva yer tutucu",
                        "kind": "player",
                        "initiative": 0,
                        "hp": 1,
                        "max_hp": 1,
                        "armor_class": 10,
                        "hidden": False,
                    }],
                },
            },
        )
        self.assertEqual(updated.status_code, 200, updated.text)

        with patch(
            "api.encounter_engine.roll",
            return_value=SimpleNamespace(total=63),
        ) as rolled:
            started = self.command(
                "start_saved_encounter",
                {"encounter_id": encounter_id, "draft_revision": 2},
            )

        self.assertEqual(started.status_code, 200, started.text)
        rolled.assert_called_once_with("1d20+2")
        combatant = started.json()["state"]["combatants"][0]
        self.assertEqual(combatant["id"], self.character_id)
        self.assertEqual(combatant["initiative"], 63)


if __name__ == "__main__":
    unittest.main()
