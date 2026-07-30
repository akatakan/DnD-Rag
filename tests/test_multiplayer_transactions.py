import tempfile
import threading
import time
import unittest
from pathlib import Path

from api.game_engine import GameEngine
from api.models import CommandRequest
from api.store import GameStore


class MultiplayerTransactionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.db"
        self.store = GameStore(self.path)
        created = self.store.create_game("Test", "DM", "human")
        joined = self.store.join_game(created["invite_code"], "Riva")
        self.dm = self.store.authenticate(created["token"])
        self.player = self.store.authenticate(joined["token"])
        self.character_id = joined["character_id"]
        self.engine = GameEngine(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, engine, command_type, payload=None):
        return engine.apply(
            self.dm,
            CommandRequest(type=command_type, payload=payload or {}),
        )

    def test_concurrent_connections_do_not_lose_state_updates(self):
        other_store = GameStore(self.path)
        other_engine = GameEngine(other_store)
        started = threading.Event()
        completed = threading.Event()

        def second_writer():
            started.set()
            self.apply(
                other_engine,
                "apply_damage",
                {"character_id": self.character_id, "amount": 1},
            )
            completed.set()

        with self.store.transaction():
            self.apply(
                self.engine,
                "apply_damage",
                {"character_id": self.character_id, "amount": 1},
            )
            worker = threading.Thread(target=second_writer)
            worker.start()
            self.assertTrue(started.wait(1))
            time.sleep(0.05)
            self.assertFalse(completed.is_set())

        worker.join(2)
        self.assertFalse(worker.is_alive())
        character = self.store.game(self.dm.game_id)["state"]["characters"][self.character_id]
        self.assertEqual(character["hp"], 8)

    def test_failed_approval_rolls_back_request_state_and_event(self):
        request = self.engine.apply(
            self.player,
            CommandRequest(type="request_damage", payload={"amount": 4}),
        )["request"]
        original_add_event = self.store.add_event

        def fail_damage_event(game_id, event_type, actor_id, visibility, payload):
            if event_type == "character_damaged":
                raise RuntimeError("simulated event write failure")
            return original_add_event(game_id, event_type, actor_id, visibility, payload)

        self.store.add_event = fail_damage_event
        with self.assertRaises(RuntimeError):
            self.apply(
                self.engine,
                "approve_request",
                {"request_id": request["id"]},
            )

        character = self.store.game(self.dm.game_id)["state"]["characters"][self.character_id]
        self.assertEqual(character["hp"], 10)
        self.assertEqual(
            [item["id"] for item in self.store.pending_requests(self.dm.game_id)],
            [request["id"]],
        )

    def test_snapshot_event_query_is_bounded_to_latest_events(self):
        for index in range(250):
            self.store.add_event(
                self.dm.game_id, "test_event", self.dm.member_id, "party", {"index": index}
            )

        events = self.store.events(self.dm)
        test_events = [event for event in events if event["type"] == "test_event"]
        self.assertEqual(len(test_events), 200)
        self.assertEqual(test_events[0]["payload"]["index"], 50)
        self.assertEqual(test_events[-1]["payload"]["index"], 249)

    def test_event_limit_is_applied_after_visibility_filter(self):
        marker = self.store.add_event(
            self.dm.game_id, "visible_marker", self.dm.member_id, "party", {}
        )
        for index in range(210):
            self.store.add_event(
                self.dm.game_id, "secret_event", self.dm.member_id, "dm_only", {"index": index}
            )

        player_events = self.store.events(self.player)
        self.assertIn(marker["id"], [event["id"] for event in player_events])
        self.assertFalse(any(event["type"] == "secret_event" for event in player_events))

    def test_concurrent_next_session_creation_allows_only_one(self):
        self.store.set_session_status(self.dm.game_id, "live")
        self.store.set_session_status(self.dm.game_id, "completed")
        barrier = threading.Barrier(2)
        outcomes = []
        outcome_lock = threading.Lock()

        def create_next(title):
            other_store = GameStore(self.path)
            barrier.wait()
            try:
                result = other_store.create_session(self.dm.game_id, title)
                outcome = ("created", result["id"])
            except ValueError as error:
                outcome = ("rejected", str(error))
            with outcome_lock:
                outcomes.append(outcome)

        workers = [
            threading.Thread(target=create_next, args=("Second A",)),
            threading.Thread(target=create_next, args=("Second B",)),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(
            sorted(outcome[0] for outcome in outcomes), ["created", "rejected"]
        )
        self.assertEqual(
            len(self.store.sessions(self.dm.game_id)), 2
        )

if __name__ == "__main__":
    unittest.main()
