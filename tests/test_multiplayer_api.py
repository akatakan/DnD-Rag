import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.realtime import ConnectionManager
from api.store import GameStore


class MultiplayerAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = GameStore(Path(self.temp.name) / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        created = self.client.post(
            "/api/games",
            json={"name": "Test Game", "dm_name": "DM", "dm_mode": "assisted"},
        ).json()
        joined = self.client.post(
            "/api/games/join",
            json={"invite_code": created["invite_code"], "player_name": "Riva"},
        ).json()
        self.dm = created
        self.player = joined

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def auth(token):
        return {"Authorization": f"Bearer {token}"}

    def command(
        self,
        token,
        command_type,
        payload=None,
        client_action_id=None,
        expected_revision=None,
    ):
        body = {"type": command_type, "payload": payload or {}}
        if client_action_id is not None:
            body["client_action_id"] = client_action_id
        if expected_revision is not None:
            body["expected_revision"] = expected_revision
        return self.client.post(
            "/api/commands",
            headers=self.auth(token),
            json=body,
        )

    def navigate_draft_to_review(self, route, token, draft):
        current = draft
        while current["current_step"] != "review":
            response = self.client.post(
                route + "/navigate",
                headers=self.auth(token),
                json={
                    "expected_revision": current["revision"],
                    "direction": "next",
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            current = response.json()
        return current

    def test_player_damage_request_requires_dm_approval(self):
        before = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        character_id = self.player["character_id"]
        self.command(self.player["token"], "request_damage", {"amount": 4})
        unchanged = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        self.assertEqual(unchanged["state"]["characters"][character_id]["hp"], before["state"]["characters"][character_id]["hp"])

        dm_snapshot = self.client.get("/api/snapshot", headers=self.auth(self.dm["token"])).json()
        request_id = dm_snapshot["pending_requests"][0]["id"]
        response = self.command(self.dm["token"], "approve_request", {"request_id": request_id})
        self.assertEqual(response.status_code, 200)
        after = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        self.assertEqual(after["state"]["characters"][character_id]["hp"], 6)

    def test_player_cannot_use_dm_command(self):
        response = self.command(self.player["token"], "next_turn")
        self.assertEqual(response.status_code, 400)

    def test_character_draft_autosave_conflict_navigation_and_publish(self):
        character_id = self.player["character_id"]
        route = f"/api/characters/{character_id}/draft"
        created = self.client.post(
            route, headers=self.auth(self.player["token"])
        )
        self.assertEqual(created.status_code, 200, created.text)
        draft = created.json()
        saved = self.client.patch(
            route,
            headers=self.auth(self.player["token"]),
            json={
                "expected_revision": draft["revision"],
                "patch": {
                    "name": "Tess",
                    "ability_scores": {
                        "strength": 16,
                        "dexterity": 12,
                        "constitution": 14,
                        "intelligence": 10,
                        "wisdom": 10,
                        "charisma": 8,
                    },
                },
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        stale = self.client.patch(
            route,
            headers=self.auth(self.player["token"]),
            json={
                "expected_revision": draft["revision"],
                "patch": {"name": "Stale"},
            },
        )
        self.assertEqual(stale.status_code, 409)
        navigated = self.client.post(
            route + "/navigate",
            headers=self.auth(self.player["token"]),
            json={
                "expected_revision": saved.json()["revision"],
                "direction": "next",
            },
        )
        self.assertEqual(navigated.status_code, 200, navigated.text)
        self.assertEqual(navigated.json()["current_step"], "abilities")
        review = self.navigate_draft_to_review(
            route, self.player["token"], navigated.json()
        )
        before = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        published = self.command(
            self.player["token"],
            "publish_character_draft",
            {"draft_revision": review["revision"]},
            "publish-draft-action-001",
            before["revision"],
        )
        self.assertEqual(published.status_code, 200, published.text)
        own = published.json()["own_character"]
        self.assertEqual(own["name"], "Tess")
        self.assertEqual(own["inputs"]["ability_scores"]["strength"], 16)
        replay = self.command(
            self.player["token"],
            "publish_character_draft",
            {"draft_revision": review["revision"]},
            "publish-draft-action-001",
            before["revision"],
        )
        self.assertEqual(replay.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["event"]["id"], published.json()["event"]["id"]
        )
        final_draft = self.client.get(
            route, headers=self.auth(self.player["token"])
        ).json()
        self.assertEqual(final_draft["status"], "published")
        rejected_reopen = self.client.patch(
            route,
            headers=self.auth(self.player["token"]),
            json={
                "expected_revision": final_draft["revision"],
                "patch": {"name": "Reopened"},
            },
        )
        self.assertEqual(rejected_reopen.status_code, 409)

    def test_character_draft_is_private_and_failed_publish_is_atomic(self):
        second = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Other",
            },
        ).json()
        character_id = self.player["character_id"]
        route = f"/api/characters/{character_id}/draft"
        draft = self.client.post(
            route, headers=self.auth(self.player["token"])
        ).json()
        denied = self.client.get(route, headers=self.auth(second["token"]))
        self.assertEqual(denied.status_code, 403)
        broken = self.client.patch(
            route,
            headers=self.auth(self.player["token"]),
            json={
                "expected_revision": draft["revision"],
                "patch": {"name": "   "},
            },
        ).json()
        before = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        failed = self.command(
            self.player["token"],
            "publish_character_draft",
            {"draft_revision": broken["revision"]},
            "publish-draft-invalid-001",
            before["revision"],
        )
        self.assertEqual(failed.status_code, 400)
        after = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        self.assertEqual(after["revision"], before["revision"])
        self.assertEqual(after["own_character"]["name"], before["own_character"]["name"])
        persisted = self.client.get(
            route, headers=self.auth(self.player["token"])
        ).json()
        self.assertEqual(persisted["revision"], broken["revision"])
        self.assertEqual(persisted["status"], "active")

    def test_draft_publish_requires_review_and_is_blocked_during_encounter(self):
        character_id = self.player["character_id"]
        route = f"/api/characters/{character_id}/draft"
        draft = self.client.post(
            route, headers=self.auth(self.player["token"])
        ).json()
        bypassed = self.command(
            self.player["token"],
            "publish_character_draft",
            {"draft_revision": draft["revision"]},
        )
        self.assertEqual(bypassed.status_code, 400)

        review = self.navigate_draft_to_review(
            route, self.player["token"], draft
        )
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": character_id,
                "name": "Riva",
                "initiative": 10,
                "kind": "player",
            },
        )
        self.command(self.dm["token"], "start_encounter")
        blocked = self.command(
            self.player["token"],
            "publish_character_draft",
            {"draft_revision": review["revision"]},
        )
        persisted = self.client.get(
            route, headers=self.auth(self.player["token"])
        ).json()

        self.assertEqual(blocked.status_code, 400)
        self.assertEqual(persisted["status"], "active")
        self.assertEqual(persisted["revision"], review["revision"])

    def test_corrupt_persisted_draft_maps_to_service_unavailable(self):
        character_id = self.player["character_id"]
        route = f"/api/characters/{character_id}/draft"
        self.client.post(route, headers=self.auth(self.player["token"]))
        with self.store.connect() as db:
            db.execute(
                """
                UPDATE character_drafts SET draft_json = ?
                WHERE game_id = ? AND character_id = ?
                """,
                ("{corrupt", self.player["game_id"], character_id),
            )

        response = self.client.get(
            route, headers=self.auth(self.player["token"])
        )
        self.assertEqual(response.status_code, 503)

    def test_configured_spell_cast_is_typed_consumes_slot_and_heals(self):
        character_id = self.player["character_id"]
        configured = self.command(
            self.dm["token"],
            "configure_character_actions",
            {
                "character_id": character_id,
                "ability": "wisdom",
                "known_spell_ids": ["spell:cure-wounds"],
                "prepared_spell_ids": ["spell:cure-wounds"],
                "slots": {"1": 1},
                "attacks": [],
            },
        )
        self.assertEqual(configured.status_code, 200)
        damaged = self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": character_id, "amount": 5},
        )
        self.assertEqual(damaged.status_code, 200)
        cast = self.command(
            self.player["token"],
            "cast_spell",
            {
                "spell_id": "spell:cure-wounds",
                "slot_level": 1,
                "target_character_id": character_id,
            },
        )
        self.assertEqual(cast.status_code, 200, cast.text)
        payload = cast.json()["event"]["payload"]
        self.assertEqual(payload["intent"]["kind"], "spell")
        self.assertEqual(payload["intent"]["source_id"], "spell:cure-wounds")
        own = cast.json()["state"]["characters"][character_id]
        self.assertEqual(
            own["action_state"]["spellcasting"]["slots"]["1"]["remaining"], 0
        )
        self.assertGreater(own["hp"], 5)

    def test_failed_self_target_spell_rolls_back_slot_consumption(self):
        character_id = self.player["character_id"]
        configured = self.command(
            self.dm["token"],
            "configure_character_actions",
            {
                "character_id": character_id,
                "ability": "wisdom",
                "known_spell_ids": ["spell:cure-wounds"],
                "prepared_spell_ids": ["spell:cure-wounds"],
                "slots": {"1": 1},
                "attacks": [],
            },
        )
        killed = self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": character_id, "amount": 20},
        )
        failed = self.command(
            self.player["token"],
            "cast_spell",
            {
                "spell_id": "spell:cure-wounds",
                "slot_level": 1,
                "target_character_id": character_id,
            },
        )
        current = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()["state"]["characters"][character_id]

        self.assertEqual(configured.status_code, 200)
        self.assertEqual(
            killed.json()["event"]["payload"]["death_status"], "dead"
        )
        self.assertEqual(failed.status_code, 400)
        self.assertEqual(
            current["action_state"]["spellcasting"]["slots"]["1"]["remaining"],
            1,
        )

    def test_player_cannot_inject_attack_definition_and_action_is_idempotent(self):
        character_id = self.player["character_id"]
        configured = self.command(
            self.dm["token"],
            "configure_character_actions",
            {
                "character_id": character_id,
                "ability": None,
                "known_spell_ids": [],
                "prepared_spell_ids": [],
                "slots": {},
                "attacks": [{
                    "id": "training-blade",
                    "name": "Training Blade",
                    "ability": "strength",
                    "proficient": True,
                    "damage_dice": "1d4",
                    "damage_type": "slashing",
                }],
            },
        )
        self.assertEqual(configured.status_code, 200)
        injected = self.command(
            self.player["token"],
            "use_attack",
            {
                "attack_id": "training-blade",
                "target_character_id": character_id,
                "damage_dice": "100d1000",
            },
        )
        self.assertEqual(injected.status_code, 422)
        action_id = "typed-attack-idempotency-001"
        first = self.command(
            self.player["token"],
            "use_attack",
            {
                "attack_id": "training-blade",
                "target_character_id": character_id,
            },
            action_id,
        )
        replay = self.command(
            self.player["token"],
            "use_attack",
            {
                "attack_id": "training-blade",
                "target_character_id": character_id,
            },
            action_id,
        )
        self.assertEqual(first.status_code, 200, first.text)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            first.json()["event"]["payload"]["intent"]["intent_id"],
            replay.json()["event"]["payload"]["intent"]["intent_id"],
        )

    def test_player_cannot_attack_other_character_outside_encounter(self):
        second = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Gareth",
            },
        ).json()
        configured = self.command(
            self.dm["token"],
            "configure_character_actions",
            {
                "character_id": self.player["character_id"],
                "ability": None,
                "known_spell_ids": [],
                "prepared_spell_ids": [],
                "slots": {},
                "attacks": [{
                    "id": "training-blade",
                    "name": "Training Blade",
                    "ability": "strength",
                    "proficient": True,
                    "damage_dice": "1d4",
                    "damage_type": "slashing",
                }],
            },
        )
        before = self.store.game(self.player["game_id"])["state"]["characters"][
            second["character_id"]
        ]["hp"]
        attacked = self.command(
            self.player["token"],
            "use_attack",
            {
                "attack_id": "training-blade",
                "target_character_id": second["character_id"],
            },
        )
        after = self.store.game(self.player["game_id"])["state"]["characters"][
            second["character_id"]
        ]["hp"]

        self.assertEqual(configured.status_code, 200)
        self.assertEqual(attacked.status_code, 400)
        self.assertEqual(after, before)

    def test_attack_consumes_only_one_action_per_active_turn(self):
        character_id = self.player["character_id"]
        self.command(
            self.dm["token"],
            "configure_character_actions",
            {
                "character_id": character_id,
                "ability": None,
                "known_spell_ids": [],
                "prepared_spell_ids": [],
                "slots": {},
                "attacks": [{
                    "id": "training-blade",
                    "name": "Training Blade",
                    "ability": "strength",
                    "proficient": True,
                    "damage_dice": "1d4",
                    "damage_type": "slashing",
                }],
            },
        )
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": character_id,
                "name": "Riva",
                "initiative": 10,
                "kind": "player",
            },
        )
        self.command(self.dm["token"], "start_encounter")
        first = self.command(
            self.player["token"],
            "use_attack",
            {
                "attack_id": "training-blade",
                "target_character_id": character_id,
            },
        )
        duplicate = self.command(
            self.player["token"],
            "use_attack",
            {
                "attack_id": "training-blade",
                "target_character_id": character_id,
            },
        )

        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            first.json()["state"]["turn_actions"][character_id]["action"],
            "attack:training-blade",
        )

    def test_command_revision_conflict_returns_409_and_duplicate_replays(self):
        before = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        payload = {
            "character_id": self.player["character_id"],
            "amount": 1,
        }
        first = self.command(
            self.dm["token"],
            "apply_damage",
            payload,
            "api-damage-action-001",
            before["revision"],
        )
        replay = self.command(
            self.dm["token"],
            "apply_damage",
            payload,
            "api-damage-action-001",
            before["revision"],
        )
        stale = self.command(
            self.dm["token"],
            "apply_damage",
            payload,
            "api-damage-action-002",
            before["revision"],
        )

        self.assertEqual(first.status_code, 200)
        self.assertFalse(first.json()["replayed"])
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["event"]["id"], first.json()["event"]["id"]
        )
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(
            stale.json()["detail"]["actual_revision"],
            before["revision"] + 1,
        )

    def test_event_cursor_skips_invisible_events_and_finds_future_public_event(self):
        initial = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        secret = self.store.add_event(
            self.dm["game_id"], "secret", self.dm["member_id"], "dm_only", {}
        )
        hidden_page = self.client.get(
            f"/api/events?after={initial['event_cursor']}&limit=10",
            headers=self.auth(self.player["token"]),
        ).json()
        self.assertEqual(hidden_page["events"], [])
        self.assertEqual(hidden_page["next_cursor"], secret["id"])

        public = self.store.add_event(
            self.dm["game_id"], "public_marker", self.dm["member_id"], "party", {}
        )
        visible_page = self.client.get(
            f"/api/events?after={hidden_page['next_cursor']}&limit=10",
            headers=self.auth(self.player["token"]),
        ).json()
        self.assertEqual(
            [event["id"] for event in visible_page["events"]], [public["id"]]
        )
        self.assertEqual(visible_page["next_cursor"], public["id"])

    def test_event_cursor_paginates_and_websocket_catches_up(self):
        initial = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        markers = [
            self.store.add_event(
                self.dm["game_id"],
                "cursor_marker",
                self.dm["member_id"],
                "party",
                {"index": index},
            )
            for index in range(3)
        ]
        first = self.client.get(
            f"/api/events?after={initial['event_cursor']}&limit=2",
            headers=self.auth(self.player["token"]),
        ).json()
        second = self.client.get(
            f"/api/events?after={first['next_cursor']}&limit=2",
            headers=self.auth(self.player["token"]),
        ).json()

        self.assertEqual(
            [event["id"] for event in first["events"]],
            [markers[0]["id"], markers[1]["id"]],
        )
        self.assertTrue(first["has_more"])
        self.assertEqual(
            [event["id"] for event in second["events"]], [markers[2]["id"]]
        )
        self.assertFalse(second["has_more"])

        with self.client.websocket_connect(
            f"/ws/games/{self.player['game_id']}?token={self.player['token']}"
            f"&after={initial['event_cursor']}"
        ) as websocket:
            catch_up = websocket.receive_json()
            self.assertEqual(catch_up["kind"], "catch_up")
            self.assertEqual(
                [event["id"] for event in catch_up["events"]],
                [marker["id"] for marker in markers],
            )

    def test_create_join_and_snapshot_include_campaign_session_context(self):
        self.assertEqual(self.dm["campaign_id"], self.dm["game_id"])
        self.assertEqual(self.player["campaign_id"], self.dm["campaign_id"])
        self.assertEqual(self.player["session_id"], self.dm["session_id"])

        player_snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        self.assertEqual(
            player_snapshot["campaign"]["id"], self.dm["campaign_id"]
        )
        self.assertEqual(
            player_snapshot["session"]["id"], self.dm["session_id"]
        )
        self.assertEqual(player_snapshot["campaign"]["status"], "active")
        self.assertEqual(player_snapshot["session"]["status"], "preparing")
        self.assertNotIn("settings", player_snapshot["campaign"])
        self.assertNotIn("summary", player_snapshot["session"])
        self.assertNotIn("auth_token_id", player_snapshot["me"])
        self.assertNotIn("auth_expires_at", player_snapshot["me"])

    def test_snapshot_never_returns_plain_invite_and_owner_can_rotate_it(self):
        dm_snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        player_snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        self.assertIsNone(dm_snapshot["game"]["invite_code"])
        self.assertIsNotNone(dm_snapshot["game"]["invite"])
        self.assertIsNone(player_snapshot["game"]["invite_code"])
        self.assertIsNone(player_snapshot["game"]["invite"])

        denied = self.client.post(
            "/api/invites/rotate",
            headers=self.auth(self.player["token"]),
            json={"max_uses": 1},
        )
        rotated = self.client.post(
            "/api/invites/rotate",
            headers=self.auth(self.dm["token"]),
            json={"max_uses": 1},
        )
        self.assertEqual(denied.status_code, 403)
        self.assertEqual(rotated.status_code, 200)
        joined = self.client.post(
            "/api/games/join",
            json={
                "invite_code": rotated.json()["invite_code"],
                "player_name": "One Use",
            },
        )
        exhausted = self.client.post(
            "/api/games/join",
            json={
                "invite_code": rotated.json()["invite_code"],
                "player_name": "Too Late",
            },
        )
        self.assertEqual(joined.status_code, 200)
        self.assertEqual(exhausted.status_code, 404)

    def test_token_rotation_logout_and_security_audit_endpoints(self):
        rotated = self.client.post(
            "/api/auth/rotate", headers=self.auth(self.player["token"])
        )
        self.assertEqual(rotated.status_code, 200)
        new_token = rotated.json()["token"]
        self.assertEqual(
            self.client.get(
                "/api/snapshot", headers=self.auth(self.player["token"])
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.get(
                "/api/security/audit", headers=self.auth(new_token)
            ).status_code,
            403,
        )
        audit = self.client.get(
            "/api/security/audit", headers=self.auth(self.dm["token"])
        )
        self.assertEqual(audit.status_code, 200)
        self.assertIn(
            "token_rotated",
            [event["action"] for event in audit.json()["events"]],
        )
        logout = self.client.post(
            "/api/auth/logout", headers=self.auth(new_token)
        )
        self.assertTrue(logout.json()["revoked"])
        self.assertEqual(
            self.client.get(
                "/api/snapshot", headers=self.auth(new_token)
            ).status_code,
            401,
        )

    def test_websocket_ticket_authenticates_without_bearer_in_url(self):
        ticket = self.client.post(
            "/api/ws-ticket", headers=self.auth(self.player["token"])
        ).json()["ticket"]
        with self.client.websocket_connect(
            f"/ws/games/{self.player['game_id']}?ticket={ticket}"
        ) as websocket:
            self.assertEqual(websocket.receive_json()["kind"], "catch_up")
            self.assertEqual(websocket.receive_json()["kind"], "snapshot")

    def test_only_active_dm_can_manage_session_lifecycle(self):
        denied = self.client.post(
            "/api/sessions/status",
            headers=self.auth(self.player["token"]),
            json={"status": "live", "expected_revision": 0},
        )
        self.assertEqual(denied.status_code, 400)

        before = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        live = self.client.post(
            "/api/sessions/status",
            headers=self.auth(self.dm["token"]),
            json={
                "status": "live",
                "expected_revision": before["revision"],
            },
        )
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json()["status"], "live")
        stale = self.client.post(
            "/api/sessions/status",
            headers=self.auth(self.dm["token"]),
            json={
                "status": "paused",
                "expected_revision": before["revision"],
            },
        )
        self.assertEqual(stale.status_code, 409)
        duplicate = self.client.post(
            "/api/sessions/status",
            headers=self.auth(self.dm["token"]),
            json={
                "status": "live",
                "expected_revision": live.json()["revision"],
            },
        )
        self.assertEqual(duplicate.status_code, 400)
        completed = self.client.post(
            "/api/sessions/status",
            headers=self.auth(self.dm["token"]),
            json={
                "status": "completed",
                "expected_revision": live.json()["revision"],
            },
        )
        self.assertEqual(completed.status_code, 200)
        created = self.client.post(
            "/api/sessions",
            headers=self.auth(self.dm["token"]),
            json={"title": "The Sunless Keep"},
        )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["number"], 2)

    def test_player_snapshot_redacts_hidden_monsters_and_hp(self):
        self.command(self.dm["token"], "add_combatant", {"name": "Goblin", "initiative": 12, "hp": 7, "kind": "monster"})
        self.command(self.dm["token"], "add_combatant", {"name": "Hidden Imp", "initiative": 14, "hp": 10, "kind": "monster", "hidden": True})
        player_snapshot = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()
        self.assertEqual([item["name"] for item in player_snapshot["state"]["combatants"]], ["Goblin"])
        self.assertNotIn("hp", player_snapshot["state"]["combatants"][0])
        dm_snapshot = self.client.get("/api/snapshot", headers=self.auth(self.dm["token"])).json()
        self.assertEqual(len(dm_snapshot["state"]["combatants"]), 2)
        self.assertEqual(dm_snapshot["state"]["combatants"][0]["hp"], 7)

    def test_player_command_response_uses_same_role_redaction_as_snapshot(self):
        second = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Gareth",
            },
        ).json()
        self.command(
            second["token"],
            "add_inventory_item",
            {"name": "Private Journal"},
        )
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": "hidden-imp",
                "name": "Hidden Imp",
                "initiative": 20,
                "hp": 10,
                "kind": "monster",
                "hidden": True,
            },
        )
        response = self.command(self.player["token"], "roll").json()
        own = response["state"]["characters"][self.player["character_id"]]
        other = response["state"]["characters"][second["character_id"]]
        self.assertIn("inventory_state", own)
        self.assertNotIn("inventory_state", other)
        self.assertNotIn(
            "Hidden Imp",
            [item["name"] for item in response["state"]["combatants"]],
        )

    def test_redacted_idempotent_replay_keeps_original_state_version(self):
        revision = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()["revision"]
        first = self.command(
            self.player["token"],
            "add_inventory_item",
            {"name": "Replay Probe"},
            client_action_id="inventory-replay-001",
            expected_revision=revision,
        )
        self.command(
            self.player["token"],
            "adjust_currency",
            {"denomination": "gp", "delta": 50},
        )
        replay = self.command(
            self.player["token"],
            "add_inventory_item",
            {"name": "Replay Probe"},
            client_action_id="inventory-replay-001",
            expected_revision=revision,
        )
        character_id = self.player["character_id"]
        self.assertEqual(first.status_code, 200)
        self.assertTrue(replay.json()["replayed"])
        self.assertEqual(
            replay.json()["state"]["characters"][character_id][
                "inventory_state"
            ]["currency"]["gp"],
            0,
        )
        current = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        self.assertEqual(
            current["state"]["characters"][character_id]["inventory_state"][
                "currency"
            ]["gp"],
            50,
        )

    def test_private_roll_is_not_visible_to_other_player(self):
        second = self.client.post(
            "/api/games/join",
            json={"invite_code": self.dm["invite_code"], "player_name": "Gareth"},
        ).json()
        self.command(self.player["token"], "roll", {"expression": "1d20", "visibility": "dm_only"})
        first_events = self.client.get("/api/snapshot", headers=self.auth(self.player["token"])).json()["events"]
        second_events = self.client.get("/api/snapshot", headers=self.auth(second["token"])).json()["events"]
        dm_events = self.client.get("/api/snapshot", headers=self.auth(self.dm["token"])).json()["events"]
        self.assertTrue(any(event["type"] == "dice_rolled" for event in first_events))
        self.assertFalse(any(event["type"] == "dice_rolled" for event in second_events))
        self.assertTrue(any(event["type"] == "dice_rolled" for event in dm_events))

    def test_websocket_receives_role_specific_snapshot(self):
        with self.client.websocket_connect(
            f"/ws/games/{self.player['game_id']}?token={self.player['token']}"
        ) as websocket:
            catch_up = websocket.receive_json()
            message = websocket.receive_json()
            self.assertEqual(catch_up["kind"], "catch_up")
            self.assertGreaterEqual(catch_up["next_cursor"], 1)
            self.assertEqual(message["kind"], "snapshot")
            self.assertEqual(message["snapshot"]["me"]["role"], "player")

    def test_invalid_roll_visibility_is_rejected(self):
        response = self.command(
            self.player["token"], "roll", {"expression": "1d20", "visibility": "player:someone-else"}
        )
        self.assertEqual(response.status_code, 422)

    def test_authenticated_rules_catalog_is_versioned_filterable_and_sourced(self):
        unauthorized = self.client.get("/api/rulesets")
        self.assertEqual(unauthorized.status_code, 401)

        versions = self.client.get(
            "/api/rulesets", headers=self.auth(self.player["token"])
        )
        self.assertEqual(versions.status_code, 200)
        summary = versions.json()["rulesets"][0]
        self.assertEqual(summary["id"], "srd-5.2.1")
        self.assertEqual(summary["status"], "foundation")
        self.assertEqual(summary["license"]["id"], "CC-BY-4.0")

        filtered = self.client.get(
            "/api/rulesets/srd-5.2.1/entries?type=condition&q=blind",
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(filtered.status_code, 200)
        self.assertEqual(filtered.json()["entries"][0]["id"], "condition:blinded")
        self.assertTrue(filtered.json()["entries"][0]["provenance"]["page_labels"])

        detail = self.client.get(
            "/api/rulesets/srd-5.2.1/entries/feature:second-wind",
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["entry"]["type"], "feature")

    def test_rules_catalog_rejects_unknown_type_and_version(self):
        invalid_type = self.client.get(
            "/api/rulesets/srd-5.2.1/entries?type=monster",
            headers=self.auth(self.player["token"]),
        )
        missing_version = self.client.get(
            "/api/rulesets/srd-9/entries",
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(invalid_type.status_code, 400)
        self.assertEqual(missing_version.status_code, 404)
        oversized_query = self.client.get(
            f"/api/rulesets/srd-5.2.1/entries?q={'x' * 201}",
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(oversized_query.status_code, 422)

    def test_character_update_recalculates_authoritative_derived_stats(self):
        updated = self.command(
            self.dm["token"],
            "update_character",
            {
                "character_id": self.player["character_id"],
                "level": 5,
                "inputs": {
                    "ability_scores": {
                        "strength": 16,
                        "dexterity": 14,
                        "constitution": 14,
                    },
                    "skill_proficiencies": ["athletics", "perception"],
                    "skill_expertise": ["athletics"],
                },
            },
        )
        self.assertEqual(updated.status_code, 200)
        character = updated.json()["state"]["characters"][self.player["character_id"]]
        self.assertEqual(character["derived"]["proficiency_bonus"], 3)
        self.assertEqual(character["derived"]["saving_throws"]["strength"], 6)
        self.assertEqual(character["derived"]["skills"]["athletics"], 9)
        self.assertEqual(character["derived"]["initiative"], 2)
        self.assertEqual(character["derived"]["max_hp"], 44)
        self.assertEqual(character["max_hp"], character["derived"]["max_hp"])
        self.assertNotIn("armor_class", updated.json()["event"]["payload"])
        self.assertNotIn("max_hp", updated.json()["event"]["payload"])
        observer = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Observer",
            },
        ).json()
        observer_events = self.client.get(
            "/api/events?after=0&limit=100",
            headers=self.auth(observer["token"]),
        ).json()["events"]
        character_event = next(
            event
            for event in observer_events
            if event["type"] == "character_updated"
        )
        self.assertNotIn("armor_class", character_event["payload"])
        self.assertNotIn("max_hp", character_event["payload"])

    def test_player_cannot_change_level_or_class_and_level_cannot_decrease(self):
        player_level = self.command(
            self.player["token"], "update_character", {"level": 5}
        )
        player_class = self.command(
            self.player["token"], "update_character", {"class_id": None}
        )
        leveled = self.command(
            self.dm["token"],
            "update_character",
            {"character_id": self.player["character_id"], "level": 10},
        )
        lowered = self.command(
            self.dm["token"],
            "update_character",
            {"character_id": self.player["character_id"], "level": 1},
        )
        changed_class = self.command(
            self.dm["token"],
            "update_character",
            {"character_id": self.player["character_id"], "class_id": None},
        )
        self.assertEqual(player_level.status_code, 400)
        self.assertEqual(player_class.status_code, 400)
        self.assertEqual(leveled.status_code, 200)
        self.assertEqual(lowered.status_code, 400)
        self.assertEqual(changed_class.status_code, 400)
        character = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()["state"]["characters"][self.player["character_id"]]
        self.assertEqual(character["level"], 10)
        self.assertEqual(
            character["resource_state"]["class_resources"]["second-wind"][
                "maximum"
            ],
            4,
        )

    def test_character_derived_fields_cannot_be_written_by_client(self):
        derived = self.command(
            self.player["token"],
            "update_character",
            {"derived": {"armor_class": 99}},
        )
        legacy_projection = self.command(
            self.player["token"],
            "update_character",
            {"ac": 99, "max_hp": 999},
        )
        invalid_expertise = self.command(
            self.player["token"],
            "update_character",
            {"inputs": {"skill_expertise": ["arcana"]}},
        )
        protected_results = [
            self.command(
                self.player["token"],
                "update_character",
                {"inputs": {field: value}},
            )
            for field, value in (
                ("armor_class", {"base": 30}),
                ("hit_points", {"level_one_base": 500}),
                ("speed", {"base": 500}),
            )
        ]
        self.assertEqual(derived.status_code, 422)
        self.assertEqual(legacy_projection.status_code, 422)
        self.assertEqual(invalid_expertise.status_code, 400)
        self.assertTrue(all(response.status_code == 400 for response in protected_results))

    def test_request_body_limit_rejects_declared_and_streamed_oversize_bodies(self):
        original = api_app.MAX_REQUEST_BODY_BYTES
        api_app.MAX_REQUEST_BODY_BYTES = 128
        try:
            declared = self.client.post(
                "/api/commands",
                content=b"x" * 129,
                headers={
                    **self.auth(self.player["token"]),
                    "Content-Type": "application/json",
                },
            )
            streamed = self.client.post(
                "/api/commands",
                content=iter([b"x" * 1024]),
                headers={
                    **self.auth(self.player["token"]),
                    "Content-Type": "application/json",
                },
            )
        finally:
            api_app.MAX_REQUEST_BODY_BYTES = original
        self.assertEqual(declared.status_code, 413)
        self.assertEqual(streamed.status_code, 413)

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=6))
    def test_class_resource_short_rest_and_hit_die_are_authoritative(self, _roll):
        first = self.command(
            self.player["token"],
            "use_second_wind",
        )
        second = self.command(
            self.player["token"],
            "use_second_wind",
        )
        exhausted = self.command(
            self.player["token"],
            "use_second_wind",
        )
        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(exhausted.status_code, 400)

        self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 8},
        )
        rested = self.command(
            self.player["token"], "short_rest", {"hit_dice": 1}
        )
        character = rested.json()["state"]["characters"][self.player["character_id"]]
        self.assertEqual(rested.status_code, 200)
        self.assertEqual(character["hp"], 8)
        self.assertEqual(character["resource_state"]["hit_dice"]["remaining"], 0)
        self.assertEqual(
            character["resource_state"]["class_resources"]["second-wind"]["remaining"],
            1,
        )

    def test_zero_hp_damage_and_healing_update_death_save_state(self):
        dropped = self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 10},
        )
        character = dropped.json()["state"]["characters"][self.player["character_id"]]
        self.assertEqual(character["resource_state"]["death_saves"]["status"], "active")

        damaged = self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 1, "critical": True},
        )
        character = damaged.json()["state"]["characters"][self.player["character_id"]]
        self.assertEqual(character["resource_state"]["death_saves"]["failures"], 2)

        healed = self.command(
            self.dm["token"],
            "apply_heal",
            {"character_id": self.player["character_id"], "amount": 1},
        )
        character = healed.json()["state"]["characters"][self.player["character_id"]]
        self.assertEqual(character["resource_state"]["death_saves"]["status"], "none")
        self.assertEqual(character["resource_state"]["death_saves"]["failures"], 0)

    def test_timed_condition_expires_at_character_end_turn(self):
        added = self.command(
            self.dm["token"],
            "add_condition",
            {
                "character_id": self.player["character_id"],
                "condition_id": "condition:blinded",
                "duration": {"kind": "rounds", "remaining": 1, "tick": "end_turn"},
            },
        )
        self.assertEqual(added.status_code, 200)
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": self.player["character_id"],
                "name": "Riva",
                "initiative": 10,
                "kind": "player",
            },
        )
        self.command(self.dm["token"], "start_encounter")
        advanced = self.command(self.dm["token"], "next_turn")
        character = advanced.json()["state"]["characters"][self.player["character_id"]]
        self.assertEqual(character["conditions"], [])
        self.assertEqual(
            advanced.json()["event"]["payload"]["expired_conditions"],
            ["condition:blinded"],
        )

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=12))
    def test_death_save_is_limited_to_active_turn_once_per_turn(self, _roll):
        self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 10},
        )
        before_encounter = self.command(self.player["token"], "death_save")
        self.assertEqual(before_encounter.status_code, 400)
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": self.player["character_id"],
                "name": "Riva",
                "initiative": 10,
                "kind": "player",
            },
        )
        self.command(self.dm["token"], "start_encounter")
        first = self.command(self.player["token"], "death_save")
        duplicate = self.command(self.player["token"], "death_save")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(
            first.json()["state"]["characters"][self.player["character_id"]][
                "resource_state"
            ]["death_saves"]["successes"],
            1,
        )
        self.assertEqual(duplicate.status_code, 400)
        self.command(self.dm["token"], "complete_encounter")
        self.command(self.dm["token"], "start_encounter")
        next_encounter_turn = self.command(self.player["token"], "death_save")
        self.assertEqual(next_encounter_turn.status_code, 200)

    def test_rest_is_rejected_during_active_encounter(self):
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": self.player["character_id"],
                "name": "Riva",
                "initiative": 10,
                "kind": "player",
            },
        )
        self.command(self.dm["token"], "start_encounter")
        short_rest = self.command(
            self.player["token"], "short_rest", {"hit_dice": 0}
        )
        long_rest = self.command(self.player["token"], "long_rest")
        self.assertEqual(short_rest.status_code, 400)
        self.assertEqual(long_rest.status_code, 400)

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=6))
    def test_second_wind_requires_own_turn_and_one_bonus_action(self, _roll):
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": "monster-1",
                "name": "Goblin",
                "initiative": 20,
                "kind": "monster",
            },
        )
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": self.player["character_id"],
                "name": "Riva",
                "initiative": 10,
                "kind": "player",
            },
        )
        self.command(self.dm["token"], "start_encounter")
        off_turn = self.command(self.player["token"], "use_second_wind")
        self.command(self.dm["token"], "next_turn")
        first = self.command(self.player["token"], "use_second_wind")
        duplicate = self.command(self.player["token"], "use_second_wind")
        self.assertEqual(off_turn.status_code, 400)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(duplicate.status_code, 400)
        self.assertEqual(
            first.json()["state"]["turn_actions"][self.player["character_id"]][
                "bonus_action"
            ],
            "feature:second-wind",
        )

    def test_dead_character_rejects_normal_healing(self):
        self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 10},
        )
        self.command(
            self.dm["token"],
            "apply_damage",
            {
                "character_id": self.player["character_id"],
                "amount": 1,
                "critical": True,
            },
        )
        killed = self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 1},
        )
        self.assertEqual(
            killed.json()["state"]["characters"][self.player["character_id"]][
                "resource_state"
            ]["death_saves"]["status"],
            "dead",
        )
        healed = self.command(
            self.dm["token"],
            "apply_heal",
            {"character_id": self.player["character_id"], "amount": 10},
        )
        self.assertEqual(healed.status_code, 400)

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=1))
    def test_damage_breaks_failed_concentration(self, _roll):
        started = self.command(
            self.player["token"],
            "start_concentration",
            {"effect_id": "effect:test", "name": "Test Concentration"},
        )
        self.assertEqual(started.status_code, 200)
        damaged = self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 2},
        )
        character = damaged.json()["state"]["characters"][self.player["character_id"]]
        check = damaged.json()["event"]["payload"]["concentration_check"]
        self.assertIsNone(character["effects"]["concentration"])
        self.assertEqual(check["dc"], 10)
        self.assertFalse(check["maintained"])

    @patch("api.resource_engine.roll", return_value=SimpleNamespace(total=1))
    def test_temp_hp_absorption_still_triggers_concentration_check(self, _roll):
        state = self.store.game(self.player["game_id"])["state"]
        character = state["characters"][self.player["character_id"]]
        character["temp_hp"] = 5
        self.store.save_state(self.player["game_id"], state)

        started = self.command(
            self.player["token"],
            "start_concentration",
            {"effect_id": "effect:test", "name": "Test Concentration"},
        )
        self.assertEqual(started.status_code, 200)
        damaged = self.command(
            self.dm["token"],
            "apply_damage",
            {"character_id": self.player["character_id"], "amount": 2},
        )

        self.assertEqual(damaged.status_code, 200)
        character = damaged.json()["state"]["characters"][
            self.player["character_id"]
        ]
        check = damaged.json()["event"]["payload"]["concentration_check"]
        self.assertEqual(character["hp"], character["max_hp"])
        self.assertEqual(character["temp_hp"], 3)
        self.assertEqual(check["dc"], 10)
        self.assertFalse(check["maintained"])

    def test_command_rate_limit_returns_retry_after(self):
        original = api_app.rate_limiter

        class DenyLimiter:
            @staticmethod
            def check(key, limit):
                return 12.5

        api_app.rate_limiter = DenyLimiter()
        try:
            response = self.command(self.player["token"], "roll")
        finally:
            api_app.rate_limiter = original
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.headers["Retry-After"], "13")

    def test_identity_inventory_equip_updates_ac_and_enforces_slot(self):
        first = self.command(
            self.player["token"],
            "add_inventory_item",
            {"catalog_id": "item:shield"},
        )
        first_id = first.json()["event"]["payload"]["item_id"]
        equipped = self.command(
            self.player["token"], "equip_item", {"item_id": first_id}
        )
        character = equipped.json()["state"]["characters"][
            self.player["character_id"]
        ]
        self.assertEqual(character["ac"], 12)
        self.assertTrue(
            character["inventory_state"]["entries"][first_id]["equipped"]
        )

        second = self.command(
            self.player["token"],
            "add_inventory_item",
            {"catalog_id": "item:shield"},
        )
        second_id = second.json()["event"]["payload"]["item_id"]
        slot_conflict = self.command(
            self.player["token"], "equip_item", {"item_id": second_id}
        )
        remove_equipped = self.command(
            self.player["token"],
            "remove_inventory_item",
            {"item_id": first_id},
        )
        self.assertEqual(slot_conflict.status_code, 400)
        self.assertEqual(remove_equipped.status_code, 400)
        self.assertEqual(
            self.command(
                self.player["token"], "unequip_item", {"item_id": first_id}
            ).status_code,
            200,
        )
        self.assertEqual(
            self.command(
                self.player["token"],
                "remove_inventory_item",
                {"item_id": first_id},
            ).status_code,
            200,
        )

    def test_currency_weight_and_encumbrance_policy_are_authoritative(self):
        adjusted = self.command(
            self.player["token"],
            "adjust_currency",
            {"denomination": "gp", "delta": 50},
        )
        character = adjusted.json()["state"]["characters"][
            self.player["character_id"]
        ]
        self.assertEqual(
            character["inventory_state"]["derived"]["coin_weight_lb"], 1
        )
        player_policy = self.command(
            self.player["token"],
            "set_encumbrance_policy",
            {"policy": "ignore"},
        )
        dm_policy = self.command(
            self.dm["token"],
            "set_encumbrance_policy",
            {
                "character_id": self.player["character_id"],
                "policy": "ignore",
            },
        )
        self.assertEqual(player_policy.status_code, 400)
        self.assertEqual(dm_policy.status_code, 200)

    def test_inventory_events_are_private_to_character_owner_and_dms(self):
        observer = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Observer",
            },
        ).json()
        cursor = self.client.get(
            "/api/snapshot", headers=self.auth(observer["token"])
        ).json()["event_cursor"]

        adjusted = self.command(
            self.player["token"],
            "adjust_currency",
            {"denomination": "gp", "delta": 50},
        )
        event_id = adjusted.json()["event"]["id"]
        observer_events = self.client.get(
            f"/api/events?after={cursor}&limit=100",
            headers=self.auth(observer["token"]),
        ).json()["events"]
        dm_events = self.client.get(
            f"/api/events?after={cursor}&limit=100",
            headers=self.auth(self.dm["token"]),
        ).json()["events"]

        self.assertNotIn(event_id, [event["id"] for event in observer_events])
        self.assertIn(event_id, [event["id"] for event in dm_events])
        self.assertEqual(
            adjusted.json()["event"]["visibility"],
            f"player:{self.player['member_id']}",
        )

    def test_attunement_is_rest_gated_and_custom_rules_require_dm(self):
        player_magic = self.command(
            self.player["token"],
            "add_inventory_item",
            {
                "name": "Forbidden Charm",
                "requires_attunement": True,
            },
        )
        self.assertEqual(player_magic.status_code, 400)
        added = self.command(
            self.dm["token"],
            "add_inventory_item",
            {
                "character_id": self.player["character_id"],
                "name": "Moon Charm",
                "requires_attunement": True,
                "equipment_slot": "neck",
            },
        )
        item_id = added.json()["event"]["payload"]["item_id"]
        attuned = self.command(
            self.player["token"], "attune_item", {"item_id": item_id}
        )
        self.assertEqual(attuned.status_code, 200)
        self.assertTrue(
            attuned.json()["state"]["characters"][self.player["character_id"]][
                "inventory_state"
            ]["entries"][item_id]["attuned"]
        )
        self.assertIn("rest", attuned.json()["event"]["payload"])

    def test_combat_equipment_requires_own_turn_and_one_action(self):
        added = self.command(
            self.player["token"],
            "add_inventory_item",
            {"catalog_id": "item:shield"},
        )
        item_id = added.json()["event"]["payload"]["item_id"]
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": "monster-1",
                "name": "Goblin",
                "initiative": 20,
                "kind": "monster",
            },
        )
        self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": self.player["character_id"],
                "name": "Riva",
                "initiative": 10,
                "kind": "player",
            },
        )
        self.command(self.dm["token"], "start_encounter")
        off_turn = self.command(
            self.player["token"], "equip_item", {"item_id": item_id}
        )
        self.command(self.dm["token"], "next_turn")
        equipped = self.command(
            self.player["token"], "equip_item", {"item_id": item_id}
        )
        duplicate_action = self.command(
            self.player["token"], "unequip_item", {"item_id": item_id}
        )
        self.assertEqual(off_turn.status_code, 400)
        self.assertEqual(equipped.status_code, 200)
        self.assertEqual(duplicate_action.status_code, 400)
        self.assertEqual(
            equipped.json()["state"]["turn_actions"][
                self.player["character_id"]
            ]["action"],
            "inventory:equip_item",
        )

    def test_public_mode_rejects_untrusted_browser_origin(self):
        original_mode = api_app.PUBLIC_MODE
        original_origins = api_app.web_origins
        api_app.PUBLIC_MODE = True
        api_app.web_origins = ["https://table.example"]
        try:
            rejected = self.client.get(
                "/api/health", headers={"Origin": "https://evil.example"}
            )
            allowed = self.client.get(
                "/api/health", headers={"Origin": "https://table.example"}
            )
            non_browser = self.client.get("/api/health")
        finally:
            api_app.PUBLIC_MODE = original_mode
            api_app.web_origins = original_origins
        self.assertEqual(rejected.status_code, 426)
        self.assertEqual(allowed.status_code, 426)
        self.assertEqual(non_browser.status_code, 426)
        api_app.PUBLIC_MODE = True
        api_app.web_origins = ["https://table.example"]
        try:
            with TestClient(
                api_app.app, base_url="https://api.example"
            ) as https_client:
                secure_rejected = https_client.get(
                    "/api/health",
                    headers={"Origin": "https://evil.example"},
                )
                secure_allowed = https_client.get(
                    "/api/health",
                    headers={"Origin": "https://table.example"},
                )
        finally:
            api_app.PUBLIC_MODE = original_mode
            api_app.web_origins = original_origins
        self.assertEqual(secure_rejected.status_code, 403)
        self.assertEqual(secure_allowed.status_code, 200)
        self.assertIn(
            "strict-transport-security", secure_allowed.headers
        )


if __name__ == "__main__":
    unittest.main()
