import tempfile
import threading
import unittest
from pathlib import Path

from api.game_engine import CommandError, GameEngine, RevisionConflict
from api.models import CommandRequest
from api.store import GameStore


class RevisionIdempotencyTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.db"
        self.store = GameStore(self.path)
        created = self.store.create_game("Test", "DM", "human")
        joined = self.store.join_game(created["invite_code"], "Riva")
        self.dm_token = created["token"]
        self.dm = self.store.authenticate(created["token"])
        self.character_id = joined["character_id"]
        self.engine = GameEngine(self.store)

    def tearDown(self):
        self.temp.cleanup()

    def command(self, action_id, amount=1, expected_revision=None):
        return CommandRequest(
            type="apply_damage",
            payload={"character_id": self.character_id, "amount": amount},
            client_action_id=action_id,
            expected_revision=expected_revision,
        )

    def test_duplicate_command_replays_original_response_once(self):
        revision = self.store.game(self.dm.game_id)["state_revision"]
        command = self.command("damage-action-001", 2, revision)

        first = self.engine.apply(self.dm, command)
        replay = self.engine.apply(self.dm, command)

        character = self.store.game(self.dm.game_id)["state"]["characters"][
            self.character_id
        ]
        damage_events = [
            event
            for event in self.store.events(self.dm)
            if event["type"] == "character_damaged"
        ]
        self.assertEqual(character["hp"], 8)
        self.assertEqual(len(damage_events), 1)
        self.assertFalse(first["replayed"])
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["event"]["id"], first["event"]["id"])
        self.assertEqual(replay["revision"], first["revision"])

    def test_action_id_cannot_be_reused_with_different_payload(self):
        revision = self.store.game(self.dm.game_id)["state_revision"]
        self.engine.apply(self.dm, self.command("damage-action-002", 1, revision))

        with self.assertRaisesRegex(CommandError, "farklı bir komut"):
            self.engine.apply(
                self.dm,
                self.command("damage-action-002", 3, revision),
            )

    def test_stale_revision_is_rejected_without_side_effects(self):
        revision = self.store.game(self.dm.game_id)["state_revision"]
        self.engine.apply(self.dm, self.command("damage-action-003", 1, revision))

        with self.assertRaises(RevisionConflict) as raised:
            self.engine.apply(
                self.dm,
                self.command("damage-action-004", 4, revision),
            )

        self.assertEqual(raised.exception.actual, revision + 1)
        character = self.store.game(self.dm.game_id)["state"]["characters"][
            self.character_id
        ]
        self.assertEqual(character["hp"], 9)

    def test_concurrent_duplicate_is_executed_once(self):
        revision = self.store.game(self.dm.game_id)["state_revision"]
        command = self.command("damage-action-005", 2, revision)
        barrier = threading.Barrier(2)
        results = []
        result_lock = threading.Lock()

        def apply_duplicate():
            store = GameStore(self.path)
            engine = GameEngine(store)
            authenticated = store.authenticate(self.dm_token)
            barrier.wait()
            result = engine.apply(authenticated, command)
            with result_lock:
                results.append(result)

        workers = [threading.Thread(target=apply_duplicate) for _ in range(2)]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(sorted(result["replayed"] for result in results), [False, True])
        character = self.store.game(self.dm.game_id)["state"]["characters"][
            self.character_id
        ]
        self.assertEqual(character["hp"], 8)


if __name__ == "__main__":
    unittest.main()
