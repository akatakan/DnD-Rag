import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.map_assets import LocalMapObjectStore
from api.realtime import ConnectionManager
from api.store import GameStore


class DataLifecycleAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = GameStore(root / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.connections.snapshot_factory = api_app.snapshot
        api_app.map_object_store = LocalMapObjectStore(root / "maps")
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        self.dm = self.client.post(
            "/api/games",
            json={
                "name": "Exportable",
                "dm_name": "Morgan",
                "dm_mode": "human",
            },
        ).json()
        self.player = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Riva",
            },
        ).json()

    def tearDown(self):
        self.temp.cleanup()

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def test_owner_export_is_scoped_and_contains_no_credentials(self):
        denied = self.client.get(
            "/api/campaign/export",
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(denied.status_code, 403, denied.text)

        response = self.client.get(
            "/api/campaign/export",
            headers=self.auth(self.dm["token"]),
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        exported = response.json()
        self.assertEqual(exported["format"], "tetsu-campaign-export")
        self.assertEqual(exported["campaign_id"], self.dm["game_id"])
        self.assertIn("games", exported["tables"])
        self.assertIn("members", exported["tables"])
        serialized = response.text.lower()
        for forbidden in (
            "token_hash",
            "ticket_hash",
            "code_hash",
            "invite_code",
            "storage_key",
            "pepper_fingerprint",
        ):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn(self.dm["token"], response.text)
        self.assertNotIn(self.player["token"], response.text)
        self.assertNotIn("security_audit_events", exported["tables"])
        self.assertNotIn("command_receipts", exported["tables"])

    def test_delete_requires_owner_and_exact_confirmation(self):
        confirmation = f"{self.dm['game_id']}:Exportable"
        denied = self.client.request(
            "DELETE",
            "/api/campaign",
            headers=self.auth(self.player["token"]),
            json={"confirmation": confirmation},
        )
        self.assertEqual(denied.status_code, 403, denied.text)
        wrong = self.client.request(
            "DELETE",
            "/api/campaign",
            headers=self.auth(self.dm["token"]),
            json={"confirmation": "wrong"},
        )
        self.assertEqual(wrong.status_code, 400, wrong.text)

        deleted = self.client.request(
            "DELETE",
            "/api/campaign",
            headers=self.auth(self.dm["token"]),
            json={"confirmation": confirmation},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(deleted.json()["deleted"])
        self.assertIsNone(self.store.authenticate(self.dm["token"]))
        self.assertIsNone(self.store.authenticate(self.player["token"]))
        with self.store.connect() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM games").fetchone()[0], 0
            )
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM campaigns").fetchone()[0], 0
            )

    def test_unicode_confirmation_and_live_socket_delete(self):
        campaign_id = self.dm["game_id"]
        with self.store.connect() as db:
            db.execute(
                "UPDATE campaigns SET name = ? WHERE id = ?",
                ("Ejderha Çığı", campaign_id),
            )

        class Socket:
            closed = False

            async def close(inner_self, code):
                inner_self.closed = code == 4404

        socket = Socket()
        auth = self.store.authenticate(self.dm["token"])
        self.assertIsNotNone(auth)
        api_app.connections.connections[campaign_id].append((socket, auth))
        deleted = self.client.request(
            "DELETE",
            "/api/campaign",
            headers=self.auth(self.dm["token"]),
            json={"confirmation": f"{campaign_id}:Ejderha Çığı"},
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        self.assertTrue(socket.closed)


if __name__ == "__main__":
    unittest.main()
