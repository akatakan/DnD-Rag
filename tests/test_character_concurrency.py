import tempfile
import threading
import unittest
from pathlib import Path

from api.game_engine import CommandError, GameEngine, RevisionConflict
from api.models import CommandRequest
from api.store import GameStore


class CharacterConcurrencyTest(unittest.TestCase):
    def test_concurrent_character_updates_with_same_revision_do_not_merge_silently(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Concurrency", "Morgan", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            revision = store.game(created["game_id"])["state_revision"]
            barrier = threading.Barrier(2)
            outcomes = []
            lock = threading.Lock()

            def update(ability):
                worker_store = GameStore(path)
                worker_engine = GameEngine(worker_store)
                auth = worker_store.authenticate(joined["token"])
                command = CommandRequest(
                    type="update_character",
                    payload={"inputs": {"ability_scores": {ability: 16}}},
                    expected_revision=revision,
                    client_action_id=f"character-{ability}-update",
                )
                barrier.wait()
                try:
                    worker_engine.apply(auth, command)
                    outcome = ("updated", ability)
                except RevisionConflict:
                    outcome = ("conflict", ability)
                with lock:
                    outcomes.append(outcome)

            workers = [
                threading.Thread(target=update, args=("strength",)),
                threading.Thread(target=update, args=("dexterity",)),
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)

            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(
                sorted(outcome[0] for outcome in outcomes),
                ["conflict", "updated"],
            )
            character = store.game(created["game_id"])["state"]["characters"][
                joined["character_id"]
            ]
            scores = character["inputs"]["ability_scores"]
            self.assertEqual(
                sorted([scores["strength"], scores["dexterity"]]), [10, 16]
            )

    def test_concurrent_second_wind_commands_share_one_bonus_action(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Action Ledger", "Morgan", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            dm_auth = store.authenticate(created["token"])
            engine = GameEngine(store)
            engine.apply(
                dm_auth,
                CommandRequest(
                    type="add_combatant",
                    payload={
                        "id": joined["character_id"],
                        "name": "Riva",
                        "initiative": 10,
                        "kind": "player",
                    },
                ),
            )
            engine.apply(dm_auth, CommandRequest(type="start_encounter"))
            barrier = threading.Barrier(2)
            outcomes = []
            lock = threading.Lock()

            def use_second_wind(action_id):
                worker_store = GameStore(path)
                worker = GameEngine(worker_store)
                auth = worker_store.authenticate(joined["token"])
                barrier.wait()
                try:
                    worker.apply(
                        auth,
                        CommandRequest(
                            type="use_second_wind",
                            client_action_id=action_id,
                        ),
                    )
                    outcome = "used"
                except CommandError:
                    outcome = "rejected"
                with lock:
                    outcomes.append(outcome)

            workers = [
                threading.Thread(
                    target=use_second_wind, args=(f"second-wind-{index}",)
                )
                for index in range(2)
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)

            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(sorted(outcomes), ["rejected", "used"])
            state = store.game(created["game_id"])["state"]
            character = state["characters"][joined["character_id"]]
            self.assertEqual(
                character["resource_state"]["class_resources"]["second-wind"][
                    "remaining"
                ],
                1,
            )
            self.assertEqual(
                state["turn_actions"][joined["character_id"]]["bonus_action"],
                "feature:second-wind",
            )

    def test_concurrent_equips_share_one_action_and_one_slot(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "game.db"
            store = GameStore(path)
            created = store.create_game("Equipment Ledger", "Morgan", "human")
            joined = store.join_game(created["invite_code"], "Riva")
            dm_auth = store.authenticate(created["token"])
            player_auth = store.authenticate(joined["token"])
            engine = GameEngine(store)
            item_ids = [
                engine.apply(
                    player_auth,
                    CommandRequest(
                        type="add_inventory_item",
                        payload={"catalog_id": "item:shield"},
                    ),
                )["event"]["payload"]["item_id"]
                for _ in range(2)
            ]
            engine.apply(
                dm_auth,
                CommandRequest(
                    type="add_combatant",
                    payload={
                        "id": joined["character_id"],
                        "name": "Riva",
                        "initiative": 10,
                        "kind": "player",
                    },
                ),
            )
            engine.apply(dm_auth, CommandRequest(type="start_encounter"))
            barrier = threading.Barrier(2)
            outcomes = []
            lock = threading.Lock()

            def equip(item_id):
                worker_store = GameStore(path)
                worker = GameEngine(worker_store)
                auth = worker_store.authenticate(joined["token"])
                barrier.wait()
                try:
                    worker.apply(
                        auth,
                        CommandRequest(
                            type="equip_item",
                            payload={"item_id": item_id},
                            client_action_id=f"equip-{item_id}",
                        ),
                    )
                    outcome = "equipped"
                except CommandError:
                    outcome = "rejected"
                with lock:
                    outcomes.append(outcome)

            workers = [
                threading.Thread(target=equip, args=(item_id,))
                for item_id in item_ids
            ]
            for worker in workers:
                worker.start()
            for worker in workers:
                worker.join(5)

            self.assertFalse(any(worker.is_alive() for worker in workers))
            self.assertEqual(sorted(outcomes), ["equipped", "rejected"])
            state = store.game(created["game_id"])["state"]
            entries = state["characters"][joined["character_id"]][
                "inventory_state"
            ]["entries"]
            self.assertEqual(
                sum(1 for entry in entries.values() if entry["equipped"]), 1
            )
            self.assertEqual(
                state["turn_actions"][joined["character_id"]]["action"],
                "inventory:equip_item",
            )


if __name__ == "__main__":
    unittest.main()
