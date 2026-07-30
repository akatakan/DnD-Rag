import tempfile
import unittest
from pathlib import Path

from api.store import GameStore


class CampaignSessionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        self.created = self.store.create_game("Shattered Crown", "Morgan", "human")

    def tearDown(self):
        self.temp.cleanup()

    def test_new_game_creates_campaign_preparing_session_and_membership(self):
        campaign = self.store.campaign_for_game(self.created["game_id"])
        session = self.store.active_session(self.created["game_id"])
        memberships = self.store.campaign_members(campaign["id"])

        self.assertEqual(self.created["campaign_id"], campaign["id"])
        self.assertEqual(self.created["session_id"], session["id"])
        self.assertEqual(campaign["status"], "active")
        self.assertEqual(campaign["ruleset_version"], "srd-5.2.1")
        self.assertEqual(session["number"], 1)
        self.assertEqual(session["status"], "preparing")
        self.assertIsNone(session["started_at"])
        self.assertEqual(
            [membership["member_id"] for membership in memberships],
            [self.created["member_id"]],
        )

    def test_join_adds_campaign_membership_and_returns_domain_ids(self):
        joined = self.store.join_game(self.created["invite_code"], "Riva")
        memberships = self.store.campaign_members(self.created["campaign_id"])

        self.assertEqual(joined["campaign_id"], self.created["campaign_id"])
        self.assertEqual(joined["session_id"], self.created["session_id"])
        self.assertEqual(
            {membership["member_id"] for membership in memberships},
            {self.created["member_id"], joined["member_id"]},
        )

    def test_session_lifecycle_and_next_session(self):
        live = self.store.set_session_status(self.created["game_id"], "live")
        self.assertIsNotNone(live["started_at"])

        paused = self.store.set_session_status(self.created["game_id"], "paused")
        self.assertEqual(paused["status"], "paused")
        resumed = self.store.set_session_status(self.created["game_id"], "live")
        self.assertEqual(resumed["started_at"], live["started_at"])

        completed = self.store.set_session_status(
            self.created["game_id"], "completed"
        )
        self.assertIsNotNone(completed["ended_at"])
        next_session = self.store.create_session(
            self.created["game_id"], "The Sunless Keep"
        )

        self.assertEqual(next_session["number"], 2)
        self.assertEqual(next_session["title"], "The Sunless Keep")
        self.assertEqual(next_session["status"], "preparing")
        self.assertEqual(
            len(self.store.sessions(self.created["campaign_id"])), 2
        )

    def test_invalid_lifecycle_transitions_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "tamamlanmalıdır"):
            self.store.create_session(self.created["game_id"])
        with self.assertRaisesRegex(ValueError, "desteklenmiyor"):
            self.store.set_session_status(self.created["game_id"], "completed")
        with self.assertRaisesRegex(ValueError, "Geçersiz"):
            self.store.set_session_status(self.created["game_id"], "unknown")


if __name__ == "__main__":
    unittest.main()
