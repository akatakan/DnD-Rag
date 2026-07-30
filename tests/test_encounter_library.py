import tempfile
import threading
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.models import CommandRequest
from api.realtime import ConnectionManager
from api.store import GameStore


class EncounterLibraryTest(unittest.TestCase):
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
            json={"name": "Encounter Test", "dm_name": "DM", "dm_mode": "human"},
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
                "client_action_id": action_id,
            },
        )

    def create_and_populate(self):
        created = self.command(
            self.dm,
            "create_encounter_draft",
            {"name": "Moon Ambush", "description": "A bridge ambush."},
            "encounter-create-001",
        )
        self.assertEqual(created.status_code, 200, created.text)
        encounter_id = created.json()["event"]["payload"]["encounter_id"]
        manual = {
            "id": "manual-goblin-001",
            "source": {"type": "manual", "id": None},
            "name": "Goblin Scout",
            "kind": "monster",
            "initiative": 14,
            "hp": 7,
            "max_hp": 7,
            "armor_class": 15,
            "hidden": False,
        }
        character = {
            "id": "character-riva-slot",
            "source": {
                "type": "character",
                "id": self.player["character_id"],
            },
            "name": "Riva placeholder",
            "kind": "player",
            "initiative": 0,
            "hp": 1,
            "max_hp": 1,
            "armor_class": 10,
            "hidden": False,
        }
        updated = self.command(
            self.dm,
            "update_encounter_draft",
            {
                "encounter_id": encounter_id,
                "draft_revision": 1,
                "patch": {"combatants": [manual, character]},
            },
            "encounter-update-001",
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        return encounter_id

    def test_builder_duplicate_start_pause_resume_and_redaction(self):
        encounter_id = self.create_and_populate()
        library = self.client.get(
            "/api/encounters", headers=self.auth(self.dm)
        )
        self.assertEqual(library.status_code, 200, library.text)
        draft = library.json()["encounters"][0]
        self.assertEqual(draft["revision"], 2)
        self.assertEqual(len(draft["data"]["combatants"]), 2)

        duplicate = self.command(
            self.dm,
            "duplicate_encounter_draft",
            {"encounter_id": encounter_id},
            "encounter-duplicate-001",
        )
        self.assertEqual(duplicate.status_code, 200, duplicate.text)
        self.assertTrue(
            duplicate.json()["event"]["payload"]["name"].endswith("(Copy)")
        )

        started = self.command(
            self.dm,
            "start_saved_encounter",
            {"encounter_id": encounter_id, "draft_revision": 2},
            "encounter-start-001",
        )
        self.assertEqual(started.status_code, 200, started.text)
        state = started.json()["state"]
        self.assertEqual(state["active_encounter_id"], encounter_id)
        self.assertEqual(state["active_encounter_revision"], 2)
        self.assertEqual(state["encounter_status"], "active")
        self.assertEqual(state["combatants"][0]["name"], "Goblin Scout")
        self.assertEqual(
            {item["id"] for item in state["combatants"]},
            {"manual-goblin-001", self.player["character_id"]},
        )

        player_state = self.client.get(
            "/api/snapshot", headers=self.auth(self.player)
        ).json()["state"]
        monster = next(
            item for item in player_state["combatants"]
            if item["kind"] == "monster"
        )
        self.assertIsNone(player_state["active_encounter_id"])
        self.assertIsNone(player_state["active_encounter_revision"])
        self.assertNotIn("hp", monster)
        self.assertNotIn("max_hp", monster)

        state_before_pause = started.json()["state"]
        paused = self.command(
            self.dm, "pause_encounter", {}, "encounter-pause-001"
        )
        paused_state = paused.json()["state"]
        self.assertEqual(paused_state["encounter_status"], "paused")
        for key in ("round", "turn_index", "turn_serial", "turn_actions"):
            self.assertEqual(paused_state[key], state_before_pause[key])
        blocked_turn = self.command(
            self.dm, "next_turn", {}, "encounter-paused-turn-001"
        )
        self.assertEqual(blocked_turn.status_code, 400)
        blocked_rest = self.command(
            self.player,
            "short_rest",
            {"hit_dice": 0},
            "encounter-paused-rest-001",
        )
        self.assertEqual(blocked_rest.status_code, 400)
        resumed = self.command(
            self.dm, "resume_encounter", {}, "encounter-resume-001"
        )
        resumed_state = resumed.json()["state"]
        self.assertEqual(resumed_state["encounter_status"], "active")
        for key in ("round", "turn_index", "turn_serial", "turn_actions"):
            self.assertEqual(resumed_state[key], paused_state[key])

    def test_hidden_current_turn_is_not_mapped_to_a_visible_combatant(self):
        created = self.command(
            self.dm,
            "create_encounter_draft",
            {"name": "Hidden Ambush"},
            "encounter-hidden-create-001",
        )
        encounter_id = created.json()["event"]["payload"]["encounter_id"]
        combatants = [
            {
                "id": "hidden-stalker-001",
                "source": {"type": "manual", "id": None},
                "name": "Hidden Stalker",
                "kind": "monster",
                "initiative": 99,
                "hp": 10,
                "max_hp": 10,
                "armor_class": 12,
                "hidden": True,
            },
            {
                "id": "visible-guard-001",
                "source": {"type": "manual", "id": None},
                "name": "Visible Guard",
                "kind": "npc",
                "initiative": 10,
                "hp": 10,
                "max_hp": 10,
                "armor_class": 12,
                "hidden": False,
            },
        ]
        updated = self.command(
            self.dm,
            "update_encounter_draft",
            {
                "encounter_id": encounter_id,
                "draft_revision": 1,
                "patch": {"combatants": combatants},
            },
            "encounter-hidden-update-001",
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        started = self.command(
            self.dm,
            "start_saved_encounter",
            {"encounter_id": encounter_id, "draft_revision": 2},
            "encounter-hidden-start-001",
        )
        self.assertEqual(started.status_code, 200, started.text)

        hidden_turn = self.client.get(
            "/api/snapshot", headers=self.auth(self.player)
        ).json()["state"]
        self.assertEqual(hidden_turn["turn_index"], -1)
        self.assertEqual(
            [item["id"] for item in hidden_turn["combatants"]],
            ["visible-guard-001"],
        )

        advanced = self.command(
            self.dm, "next_turn", {}, "encounter-hidden-next-001"
        )
        self.assertEqual(advanced.status_code, 200, advanced.text)
        visible_turn = self.client.get(
            "/api/snapshot", headers=self.auth(self.player)
        ).json()["state"]
        self.assertEqual(visible_turn["turn_index"], 0)
        self.assertEqual(
            visible_turn["combatants"][visible_turn["turn_index"]]["id"],
            advanced.json()["state"]["combatants"][
                advanced.json()["state"]["turn_index"]
            ]["id"],
        )

    def test_permissions_stale_revision_and_cross_campaign_id(self):
        encounter_id = self.create_and_populate()
        player_list = self.client.get(
            "/api/encounters", headers=self.auth(self.player)
        )
        self.assertEqual(player_list.status_code, 403)
        player_create = self.command(
            self.player,
            "create_encounter_draft",
            {"name": "Unauthorized"},
            "encounter-player-create-001",
        )
        self.assertEqual(player_create.status_code, 400)
        stale = self.command(
            self.dm,
            "update_encounter_draft",
            {
                "encounter_id": encounter_id,
                "draft_revision": 1,
                "patch": {"name": "Stale"},
            },
            "encounter-stale-update-001",
        )
        self.assertEqual(stale.status_code, 409, stale.text)

        other = self.client.post(
            "/api/games",
            json={"name": "Other", "dm_name": "Other DM", "dm_mode": "human"},
        ).json()
        foreign = self.command(
            other,
            "duplicate_encounter_draft",
            {"encounter_id": encounter_id},
            "encounter-cross-game-001",
        )
        self.assertEqual(foreign.status_code, 400)

    def test_concurrent_draft_updates_have_exactly_one_winner(self):
        encounter_id = self.create_and_populate()
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def update(name: str, action_id: str):
            store = GameStore(self.path)
            engine = GameEngine(store)
            auth = store.authenticate(self.dm["token"])
            barrier.wait()
            try:
                engine.apply(
                    auth,
                    CommandRequest(
                        type="update_encounter_draft",
                        payload={
                            "encounter_id": encounter_id,
                            "draft_revision": 2,
                            "patch": {"name": name},
                        },
                        client_action_id=action_id,
                    ),
                )
                outcome = "updated"
            except ValueError:
                outcome = "conflict"
            with lock:
                outcomes.append(outcome)

        workers = [
            threading.Thread(
                target=update,
                args=("Ambush A", "encounter-concurrent-update-a"),
            ),
            threading.Thread(
                target=update,
                args=("Ambush B", "encounter-concurrent-update-b"),
            ),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(sorted(outcomes), ["conflict", "updated"])
        drafts = self.store.encounter_drafts(self.dm["campaign_id"])
        self.assertEqual(drafts[0]["revision"], 3)

    def test_concurrent_start_has_exactly_one_winner(self):
        encounter_id = self.create_and_populate()
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        lock = threading.Lock()

        def start(action_id: str):
            store = GameStore(self.path)
            engine = GameEngine(store)
            auth = store.authenticate(self.dm["token"])
            barrier.wait()
            try:
                engine.apply(
                    auth,
                    CommandRequest(
                        type="start_saved_encounter",
                        payload={
                            "encounter_id": encounter_id,
                            "draft_revision": 2,
                        },
                        client_action_id=action_id,
                    ),
                )
                outcome = "started"
            except ValueError:
                outcome = "rejected"
            with lock:
                outcomes.append(outcome)

        workers = [
            threading.Thread(
                target=start, args=("encounter-concurrent-start-a",)
            ),
            threading.Thread(
                target=start, args=("encounter-concurrent-start-b",)
            ),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(5)
        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(sorted(outcomes), ["rejected", "started"])
        game = self.store.game(self.dm["game_id"])
        self.assertEqual(game["state"]["active_encounter_id"], encounter_id)
        self.assertEqual(game["state"]["encounter_status"], "active")


if __name__ == "__main__":
    unittest.main()
