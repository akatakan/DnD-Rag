import sqlite3
import tempfile
import unittest
from pathlib import Path

from api.migrations import LATEST_SCHEMA_VERSION
from api.store import GameStore


class CampaignVaultTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "game.db"
        self.store = GameStore(
            self.path,
            auth_pepper="campaign-vault-test-pepper-at-least-32-chars",
        )
        self.created = self.store.create_game(
            "Northwatch", "Morgan", "human"
        )
        self.auth = self.store.authenticate(self.created["token"])
        self.secret = "a" * 64

    def tearDown(self):
        self.temp.cleanup()

    def test_dm_can_list_and_resume_after_original_token_is_revoked(self):
        attached = self.store.attach_campaign_vault(self.auth, self.secret)
        self.assertTrue(attached["attached"])
        campaigns = self.store.campaign_vault_campaigns(self.secret)
        self.assertEqual(len(campaigns), 1)
        self.assertEqual(campaigns[0]["name"], "Northwatch")
        self.assertEqual(campaigns[0]["game_id"], self.created["game_id"])
        self.assertNotIn(self.secret, self.path.read_bytes().decode(
            "utf-8", errors="ignore"
        ))

        self.assertTrue(self.store.revoke_token(self.created["token"]))
        self.assertIsNone(self.store.authenticate(self.created["token"]))
        resumed = self.store.resume_campaign_vault(
            self.secret, self.created["game_id"]
        )
        resumed_auth = self.store.authenticate(resumed["token"])
        self.assertIsNotNone(resumed_auth)
        self.assertEqual(resumed_auth.game_id, self.created["game_id"])
        self.assertEqual(resumed_auth.member_id, self.created["member_id"])

    def test_one_device_can_hold_multiple_campaigns_and_detach_one(self):
        self.store.attach_campaign_vault(self.auth, self.secret)
        second = self.store.create_game("Sunless Citadel", "Morgan", "human")
        second_auth = self.store.authenticate(second["token"])
        self.store.attach_campaign_vault(second_auth, self.secret)

        self.assertEqual(
            {
                campaign["game_id"]
                for campaign in self.store.campaign_vault_campaigns(
                    self.secret
                )
            },
            {self.created["game_id"], second["game_id"]},
        )
        self.assertTrue(
            self.store.detach_campaign_vault(
                self.secret, self.created["game_id"]
            )
        )
        self.assertEqual(
            [
                campaign["game_id"]
                for campaign in self.store.campaign_vault_campaigns(
                    self.secret
                )
            ],
            [second["game_id"]],
        )

    def test_player_and_invalid_secrets_are_rejected(self):
        player = self.store.join_game(
            self.created["invite_code"], "Player"
        )
        player_auth = self.store.authenticate(player["token"])
        with self.assertRaises(PermissionError):
            self.store.attach_campaign_vault(player_auth, self.secret)
        with self.assertRaises(ValueError):
            self.store.campaign_vault_campaigns("too-short")

    def test_previous_schema_is_upgraded_without_losing_campaign(self):
        db = sqlite3.connect(self.path)
        try:
            db.execute("DROP TABLE campaign_device_memberships")
            db.execute("DROP TABLE campaign_device_vaults")
            db.execute(
                "DELETE FROM schema_migrations WHERE version = ?",
                (LATEST_SCHEMA_VERSION,),
            )
            db.commit()
        finally:
            db.close()
        upgraded = GameStore(
            self.path,
            auth_pepper="campaign-vault-test-pepper-at-least-32-chars",
        )
        self.assertEqual(
            upgraded.game(self.created["game_id"])["name"],
            "Northwatch",
        )
        upgraded.attach_campaign_vault(
            upgraded.authenticate(self.created["token"]), self.secret
        )
        self.assertEqual(
            len(upgraded.campaign_vault_campaigns(self.secret)), 1
        )


if __name__ == "__main__":
    unittest.main()
