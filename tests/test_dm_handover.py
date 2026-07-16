import asyncio
import tempfile
import unittest
from pathlib import Path

from api.game_engine import CommandError, GameEngine
from api.models import CommandRequest
from api.realtime import ConnectionManager
from api.store import GameStore


class FakeWebSocket:
    def __init__(self):
        self.messages = []

    async def accept(self):
        return None

    async def send_json(self, message):
        self.messages.append(message)


class DMHandoverTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        self.engine = GameEngine(self.store)
        self.dm_result = self.store.create_game("Test", "Morgan", "human")
        self.player_result = self.store.join_game(self.dm_result["invite_code"], "Riva")
        self.second_result = self.store.join_game(self.dm_result["invite_code"], "Gareth")
        self.dm = self.store.authenticate(self.dm_result["token"])

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, auth, command_type, payload=None):
        return self.engine.apply(auth, CommandRequest(type=command_type, payload=payload or {}))

    def test_co_dm_must_accept_handover_before_controlling_game(self):
        self.apply(self.dm, "assign_co_dm", {"member_id": self.player_result["member_id"]})
        co_dm = self.store.authenticate(self.player_result["token"])
        self.assertEqual(co_dm.role, "co_dm")
        with self.assertRaises(CommandError):
            self.apply(co_dm, "update_scene", {"title": "Too early"})

        self.store.set_handover(self.dm.game_id, {
            "status": "offered", "offline_dm_id": self.dm.member_id,
            "candidate_id": co_dm.member_id,
        })
        self.apply(co_dm, "accept_dm_handover")
        self.apply(co_dm, "update_scene", {"title": "Co-DM scene"})
        self.assertEqual(self.store.game(self.dm.game_id)["state"]["scene"]["title"], "Co-DM scene")
        with self.assertRaises(CommandError):
            self.apply(self.dm, "assign_co_dm", {"member_id": self.second_result["member_id"]})

        self.apply(self.dm, "reclaim_dm_control")
        self.assertEqual(self.store.game(self.dm.game_id)["active_dm_id"], self.dm.member_id)
        with self.assertRaises(CommandError):
            self.apply(co_dm, "update_scene", {"title": "No longer active"})

    def test_player_majority_can_enable_ai_dm(self):
        game_id = self.dm.game_id
        voters = [self.player_result["member_id"], self.second_result["member_id"]]
        self.store.set_handover(game_id, {
            "status": "vote_ai", "offline_dm_id": self.dm.member_id,
            "eligible_voters": voters, "votes": [], "required": 2,
        })
        first = self.store.authenticate(self.player_result["token"])
        second = self.store.authenticate(self.second_result["token"])
        self.apply(first, "vote_ai_takeover")
        self.assertEqual(self.store.game(game_id)["dm_mode"], "human")
        self.apply(second, "vote_ai_takeover")
        game = self.store.game(game_id)
        self.assertEqual(game["dm_mode"], "ai")
        self.assertEqual(game["handover"], {})

    def test_reconnect_within_grace_cancels_handover(self):
        async def scenario():
            manager = ConnectionManager(self.store, grace_seconds=0.03)
            expired = []

            async def callback(game_id, member_id):
                expired.append((game_id, member_id))

            manager.on_grace_expired = callback
            first_socket = FakeWebSocket()
            await manager.connect(first_socket, self.dm)
            manager.disconnect(first_socket, self.dm)
            self.assertEqual(self.store.game(self.dm.game_id)["handover"]["status"], "grace")
            await asyncio.sleep(0.01)
            await manager.connect(FakeWebSocket(), self.dm)
            await asyncio.sleep(0.04)
            self.assertEqual(expired, [])
            self.assertEqual(self.store.game(self.dm.game_id)["handover"], {})

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()


