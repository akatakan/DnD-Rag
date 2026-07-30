import sqlite3
import tempfile
import threading
import unittest
from contextlib import closing
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.models import CommandRequest
from api.realtime import ConnectionManager
from api.store import GameStore


class SessionWorkspaceTest(unittest.TestCase):
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
            json={"name": "Session", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.player = self.client.post(
            "/api/games/join",
            json={"invite_code": self.dm["invite_code"], "player_name": "Riva"},
        ).json()
        self.other = self.client.post(
            "/api/games/join",
            json={"invite_code": self.dm["invite_code"], "player_name": "Other"},
        ).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def command(self, actor, command_type, payload, action_id):
        return self.client.post(
            "/api/commands",
            headers=self.auth(actor["token"]),
            json={
                "type": command_type,
                "payload": payload,
                "client_action_id": action_id,
            },
        )

    def test_notes_loot_quests_and_summary_are_role_aware_and_idempotent(self):
        private_note = self.command(
            self.player,
            "add_session_note",
            {"content": "Secret plan", "visibility": "private"},
            "session-private-note-001",
        )
        self.assertEqual(private_note.status_code, 200, private_note.text)
        party_note = self.command(
            self.player,
            "add_session_note",
            {"content": "We enter the keep", "visibility": "party"},
            "session-party-note-001",
        )
        replay = self.command(
            self.player,
            "add_session_note",
            {"content": "We enter the keep", "visibility": "party"},
            "session-party-note-001",
        )
        self.assertTrue(replay.json()["replayed"])
        loot = self.command(
            self.dm,
            "add_session_loot",
            {"name": "Moon Key", "quantity": 1},
            "session-loot-add-001",
        ).json()["event"]["payload"]
        claimed = self.command(
            self.player,
            "claim_session_loot",
            {"loot_id": loot["id"]},
            "session-loot-claim-001",
        )
        self.assertEqual(claimed.status_code, 200, claimed.text)
        duplicate_claim = self.command(
            self.other,
            "claim_session_loot",
            {"loot_id": loot["id"]},
            "session-loot-claim-002",
        )
        self.assertEqual(duplicate_claim.status_code, 400)
        quest = self.command(
            self.dm,
            "add_session_quest",
            {"title": "Open the Moon Door", "description": "Find the sigil."},
            "session-quest-add-001",
        ).json()["event"]["payload"]
        completed = self.command(
            self.dm,
            "set_session_quest_status",
            {"quest_id": quest["id"], "status": "completed"},
            "session-quest-complete-001",
        )
        self.assertEqual(completed.status_code, 200, completed.text)
        draft_summary = self.command(
            self.dm,
            "update_session_summary",
            {
                "title": "The Moon Door",
                "highlights": ["Found the key"],
                "next_steps": ["Enter the vault"],
                "published": False,
            },
            "session-summary-draft-001",
        )
        self.assertEqual(draft_summary.status_code, 200)

        player_workspace = self.client.get(
            "/api/sessions/current/workspace",
            headers=self.auth(self.player["token"]),
        ).json()
        other_workspace = self.client.get(
            "/api/sessions/current/workspace",
            headers=self.auth(self.other["token"]),
        ).json()
        dm_workspace = self.client.get(
            "/api/sessions/current/workspace",
            headers=self.auth(self.dm["token"]),
        ).json()
        self.assertEqual(
            {note["content"] for note in player_workspace["notes"]},
            {"Secret plan", "We enter the keep"},
        )
        self.assertEqual(
            {note["content"] for note in other_workspace["notes"]},
            {"We enter the keep"},
        )
        self.assertIn("Secret plan", {
            note["content"] for note in dm_workspace["notes"]
        })
        self.assertIsNone(player_workspace["summary"])
        player_events = self.client.get(
            "/api/events?after=0&limit=100",
            headers=self.auth(self.player["token"]),
        ).json()["events"]
        self.assertFalse(any(
            event["type"] == "session_summary_updated"
            and event["payload"].get("published") is False
            for event in player_events
        ))
        self.assertEqual(
            player_workspace["loot"][0]["claimant_id"],
            self.player["member_id"],
        )
        self.assertEqual(player_workspace["quests"][0]["status"], "completed")

        published = self.command(
            self.dm,
            "update_session_summary",
            {
                "title": "The Moon Door",
                "highlights": ["Found the key"],
                "next_steps": ["Enter the vault"],
                "published": True,
            },
            "session-summary-publish-001",
        )
        self.assertEqual(published.status_code, 200)
        visible = self.client.get(
            "/api/sessions/current/workspace",
            headers=self.auth(self.player["token"]),
        ).json()["summary"]
        self.assertTrue(visible["published"])

    def test_completed_session_rejects_new_artifacts_but_allows_summary(self):
        before = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        started = self.client.post(
            "/api/sessions/status",
            headers=self.auth(self.dm["token"]),
            json={
                "status": "live",
                "expected_revision": before["revision"],
            },
        )
        self.assertEqual(started.status_code, 200)
        ended = self.client.post(
            "/api/sessions/status",
            headers=self.auth(self.dm["token"]),
            json={
                "status": "completed",
                "expected_revision": started.json()["revision"],
            },
        )
        self.assertEqual(ended.status_code, 200)
        rejected = self.command(
            self.player,
            "add_session_note",
            {"content": "Late edit", "visibility": "party"},
            "session-late-note-001",
        )
        self.assertEqual(rejected.status_code, 400)
        summary = self.command(
            self.dm,
            "update_session_summary",
            {
                "title": "Final",
                "highlights": [],
                "next_steps": [],
                "published": True,
            },
            "session-final-summary-001",
        )
        self.assertEqual(summary.status_code, 200, summary.text)

    def test_cross_game_loot_and_quest_ids_are_rejected(self):
        other_game = self.client.post(
            "/api/games",
            json={"name": "Other Game", "dm_name": "Other DM", "dm_mode": "human"},
        ).json()
        foreign_loot = self.command(
            other_game,
            "add_session_loot",
            {"name": "Foreign Crown", "quantity": 1},
            "session-foreign-loot-add-001",
        ).json()["event"]["payload"]["id"]
        foreign_quest = self.command(
            other_game,
            "add_session_quest",
            {"title": "Foreign Quest", "description": ""},
            "session-foreign-quest-add-001",
        ).json()["event"]["payload"]["id"]
        claim = self.command(
            self.player,
            "claim_session_loot",
            {"loot_id": foreign_loot},
            "session-foreign-loot-claim-001",
        )
        update = self.command(
            self.dm,
            "set_session_quest_status",
            {"quest_id": foreign_quest, "status": "completed"},
            "session-foreign-quest-update-001",
        )
        self.assertEqual(claim.status_code, 400)
        self.assertEqual(update.status_code, 400)

    def test_concurrent_loot_claim_has_exactly_one_winner(self):
        loot_id = self.command(
            self.dm,
            "add_session_loot",
            {"name": "Single Crown", "quantity": 1},
            "session-race-loot-add-001",
        ).json()["event"]["payload"]["id"]
        barrier = threading.Barrier(2)
        outcomes: list[str] = []
        outcome_lock = threading.Lock()

        def claim(actor, action_id):
            store = GameStore(self.path)
            engine = GameEngine(store)
            auth = store.authenticate(actor["token"])
            barrier.wait()
            try:
                engine.apply(
                    auth,
                    CommandRequest(
                        type="claim_session_loot",
                        payload={"loot_id": loot_id},
                        client_action_id=action_id,
                    ),
                )
                outcome = "claimed"
            except ValueError:
                outcome = "rejected"
            with outcome_lock:
                outcomes.append(outcome)

        workers = [
            threading.Thread(
                target=claim,
                args=(self.player, "session-race-claim-player"),
            ),
            threading.Thread(
                target=claim,
                args=(self.other, "session-race-claim-other"),
            ),
        ]
        for worker in workers:
            worker.start()
        for worker in workers:
            worker.join(3)

        self.assertFalse(any(worker.is_alive() for worker in workers))
        self.assertEqual(sorted(outcomes), ["claimed", "rejected"])
        workspace = self.store.session_workspace(
            self.store.authenticate(self.dm["token"])
        )
        self.assertEqual(workspace["loot"][0]["status"], "claimed")

    def test_v17_corrupt_summary_rolls_back_and_workspace_fails_closed(self):
        self.client.close()
        with closing(sqlite3.connect(self.path)) as db:
            db.execute(
                "UPDATE sessions SET summary_json = ?",
                ('{"published": false, "highlights": "leak"}',),
            )
            db.execute("DELETE FROM schema_migrations WHERE version = 17")
            db.commit()
        with self.assertRaises(RuntimeError):
            GameStore(self.path)
        with closing(sqlite3.connect(self.path)) as db:
            applied = db.execute(
                "SELECT 1 FROM schema_migrations WHERE version = 17"
            ).fetchone()
        self.assertIsNone(applied)


if __name__ == "__main__":
    unittest.main()
