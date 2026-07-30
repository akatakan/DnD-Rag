import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.models import CommandRequest
from api.realtime import ConnectionManager
from api.store import GameStore


class AdvancedEncounterTest(unittest.TestCase):
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
            json={"name": "Advanced", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.player = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Riva",
            },
        ).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def auth(actor):
        return {"Authorization": f"Bearer {actor['token']}"}

    def command(self, actor, command_type, payload=None, action_id=None):
        return self.client.post(
            "/api/commands",
            headers=self.auth(actor),
            json={
                "type": command_type,
                "payload": payload or {},
                "client_action_id": action_id or f"advanced-{command_type}-001",
            },
        )

    def test_ties_environment_and_current_turn_preservation(self):
        first = self.command(
            self.dm,
            "add_combatant",
            {
                "id": "tie-first",
                "name": "Zulu",
                "initiative": 15,
                "tie_breaker": 5,
                "hp": 10,
                "kind": "npc",
            },
            "advanced-tie-first",
        )
        second = self.command(
            self.dm,
            "add_combatant",
            {
                "id": "tie-second",
                "name": "Alpha",
                "initiative": 15,
                "tie_breaker": 1,
                "hp": 10,
                "kind": "npc",
            },
            "advanced-tie-second",
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.status_code, 200, second.text)
        started = self.command(
            self.dm, "start_encounter", {}, "advanced-tie-start"
        )
        state = started.json()["state"]
        self.assertEqual(
            [item["id"] for item in state["combatants"]],
            ["tie-first", "tie-second"],
        )
        current_id = state["combatants"][state["turn_index"]]["id"]

        lair = self.command(
            self.dm,
            "add_environment_entry",
            {
                "name": "Lair Action",
                "kind": "lair",
                "initiative": 20,
                "tie_breaker": 0,
            },
            "advanced-lair-add",
        )
        self.assertEqual(lair.status_code, 200, lair.text)
        lair_state = lair.json()["state"]
        self.assertEqual(lair_state["combatants"][0]["kind"], "lair")
        self.assertEqual(
            lair_state["combatants"][lair_state["turn_index"]]["id"],
            current_id,
        )
        reordered = self.command(
            self.dm,
            "set_initiative_tiebreaker",
            {"combatant_id": "tie-second", "tie_breaker": 9},
            "advanced-tie-reorder",
        )
        reorder_state = reordered.json()["state"]
        self.assertLess(
            next(i for i, item in enumerate(reorder_state["combatants"]) if item["id"] == "tie-second"),
            next(i for i, item in enumerate(reorder_state["combatants"]) if item["id"] == "tie-first"),
        )
        self.assertEqual(
            reorder_state["combatants"][reorder_state["turn_index"]]["id"],
            current_id,
        )

    def test_character_hp_sync_condition_tick_and_atomic_undo(self):
        character_id = self.player["character_id"]
        added = self.command(
            self.dm,
            "add_combatant",
            {
                "id": character_id,
                "name": "Riva",
                "initiative": 20,
                "hp": 10,
                "kind": "player",
            },
            "advanced-character-add",
        )
        self.assertEqual(added.status_code, 200, added.text)
        self.command(
            self.dm,
            "add_combatant",
            {
                "id": "training-dummy",
                "name": "Dummy",
                "initiative": 1,
                "hp": 20,
                "kind": "npc",
            },
            "advanced-dummy-add",
        )
        self.command(
            self.dm, "start_encounter", {}, "advanced-sync-start"
        )
        damaged = self.command(
            self.dm,
            "adjust_combatant_hp",
            {"combatant_id": character_id, "delta": -3},
            "advanced-sync-damage",
        )
        self.assertEqual(damaged.status_code, 200, damaged.text)
        damaged_state = damaged.json()["state"]
        combatant = next(
            item for item in damaged_state["combatants"]
            if item["id"] == character_id
        )
        self.assertEqual(damaged_state["characters"][character_id]["hp"], 7)
        self.assertEqual(combatant["hp"], 7)
        self.assertTrue(
            self.client.get(
                "/api/snapshot", headers=self.auth(self.dm)
            ).json()["state"]["encounter_undo_available"]
        )

        undone = self.command(
            self.dm, "undo_encounter", {}, "advanced-sync-undo"
        )
        self.assertEqual(undone.status_code, 200, undone.text)
        restored = undone.json()["state"]
        restored_combatant = next(
            item for item in restored["combatants"]
            if item["id"] == character_id
        )
        self.assertEqual(restored["characters"][character_id]["hp"], 10)
        self.assertEqual(restored_combatant["hp"], 10)
        self.assertEqual(
            undone.json()["event"]["payload"]["undone_command"],
            "adjust_combatant_hp",
        )

        conditioned = self.command(
            self.dm,
            "add_condition",
            {
                "character_id": character_id,
                "condition_id": "condition:blinded",
                "duration": {
                    "kind": "rounds",
                    "remaining": 1,
                    "tick": "end_turn",
                },
            },
            "advanced-condition-add",
        )
        self.assertEqual(conditioned.status_code, 200, conditioned.text)
        advanced = self.command(
            self.dm, "next_turn", {}, "advanced-condition-tick"
        )
        self.assertEqual(advanced.status_code, 200, advanced.text)
        self.assertNotIn(
            "condition:blinded",
            advanced.json()["state"]["characters"][character_id]["conditions"],
        )
        self.assertEqual(
            advanced.json()["event"]["payload"]["expired_conditions"],
            ["condition:blinded"],
        )

    def test_failed_undo_event_rolls_back_state_and_history_pop(self):
        self.command(
            self.dm,
            "add_combatant",
            {
                "id": "undo-target",
                "name": "Target",
                "initiative": 10,
                "hp": 8,
                "kind": "npc",
            },
            "advanced-undo-target-add",
        )
        self.command(
            self.dm, "start_encounter", {}, "advanced-undo-start"
        )
        self.command(
            self.dm,
            "adjust_combatant_hp",
            {"combatant_id": "undo-target", "delta": -2},
            "advanced-undo-damage",
        )
        before = self.store.game(self.dm["game_id"])["state"]
        before_count = self.store.encounter_undo_count(self.dm["game_id"])
        original_add_event = self.store.add_event

        def fail_undo_event(game_id, event_type, actor_id, visibility, payload):
            if event_type == "encounter_undone":
                raise RuntimeError("simulated undo event failure")
            return original_add_event(
                game_id, event_type, actor_id, visibility, payload
            )

        self.store.add_event = fail_undo_event
        auth = self.store.authenticate(self.dm["token"])
        with self.assertRaisesRegex(RuntimeError, "simulated"):
            api_app.game_engine.apply(
                auth,
                CommandRequest(
                    type="undo_encounter",
                    client_action_id="advanced-undo-failure",
                ),
            )
        self.assertEqual(
            self.store.game(self.dm["game_id"])["state"], before
        )
        self.assertEqual(
            self.store.encounter_undo_count(self.dm["game_id"]),
            before_count,
        )

    def test_dead_character_cannot_be_healed_and_paused_hp_is_locked(self):
        character_id = self.player["character_id"]
        self.command(
            self.dm,
            "add_combatant",
            {
                "id": character_id,
                "name": "Riva",
                "initiative": 10,
                "hp": 10,
                "kind": "player",
            },
            "advanced-dead-add",
        )
        self.command(
            self.dm, "start_encounter", {}, "advanced-dead-start"
        )
        killed = self.command(
            self.dm,
            "adjust_combatant_hp",
            {"combatant_id": character_id, "delta": -20},
            "advanced-dead-kill",
        )
        self.assertEqual(killed.status_code, 200, killed.text)
        self.assertEqual(
            killed.json()["state"]["characters"][character_id][
                "resource_state"
            ]["death_saves"]["status"],
            "dead",
        )
        heal = self.command(
            self.dm,
            "adjust_combatant_hp",
            {"combatant_id": character_id, "delta": 1},
            "advanced-dead-heal",
        )
        self.assertEqual(heal.status_code, 400, heal.text)
        self.assertEqual(
            self.store.game(self.dm["game_id"])["state"]["characters"][
                character_id
            ]["hp"],
            0,
        )

        self.command(
            self.dm, "pause_encounter", {}, "advanced-dead-pause"
        )
        paused_damage = self.command(
            self.dm,
            "adjust_combatant_hp",
            {"combatant_id": character_id, "delta": -1},
            "advanced-paused-damage",
        )
        self.assertEqual(paused_damage.status_code, 400, paused_damage.text)

    def test_hidden_mutation_events_are_dm_only(self):
        self.command(
            self.dm,
            "add_combatant",
            {
                "id": "hidden-live-target",
                "name": "Hidden Target",
                "initiative": 10,
                "tie_breaker": 0,
                "hp": 8,
                "kind": "monster",
                "hidden": True,
            },
            "advanced-hidden-add",
        )
        self.command(
            self.dm, "start_encounter", {}, "advanced-hidden-start"
        )
        tie = self.command(
            self.dm,
            "set_initiative_tiebreaker",
            {"combatant_id": "hidden-live-target", "tie_breaker": 2},
            "advanced-hidden-tie",
        )
        damage = self.command(
            self.dm,
            "adjust_combatant_hp",
            {"combatant_id": "hidden-live-target", "delta": -1},
            "advanced-hidden-damage",
        )
        self.assertEqual(tie.json()["event"]["visibility"], "dm_only")
        self.assertEqual(damage.json()["event"]["visibility"], "dm_only")
        player_auth = self.store.authenticate(self.player["token"])
        visible_types = {
            event["type"] for event in self.store.events(player_auth)
        }
        self.assertNotIn("initiative_tie_resolved", visible_types)
        self.assertNotIn("combatant_hp_adjusted", visible_types)

    def test_environment_turn_blocks_character_resources(self):
        character_id = self.player["character_id"]
        self.command(
            self.dm,
            "add_combatant",
            {
                "id": character_id,
                "name": "Riva",
                "initiative": 10,
                "hp": 10,
                "kind": "player",
            },
            "advanced-environment-character",
        )
        self.command(
            self.dm, "start_encounter", {}, "advanced-environment-start"
        )
        self.command(
            self.dm,
            "add_environment_entry",
            {
                "name": "Lair Action",
                "kind": "lair",
                "initiative": 20,
                "tie_breaker": 0,
            },
            "advanced-environment-add",
        )
        advanced = self.command(
            self.dm, "next_turn", {}, "advanced-environment-next"
        )
        state = advanced.json()["state"]
        self.assertEqual(
            state["combatants"][state["turn_index"]]["kind"], "lair"
        )
        concentration = self.command(
            self.dm,
            "start_concentration",
            {
                "character_id": character_id,
                "effect_id": "manual:blocked",
                "name": "Blocked Spell",
            },
            "advanced-environment-resource",
        )
        self.assertEqual(concentration.status_code, 400, concentration.text)


if __name__ == "__main__":
    unittest.main()
