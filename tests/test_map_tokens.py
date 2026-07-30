import tempfile
import threading
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.map_assets import LocalMapObjectStore
from api.models import CommandRequest
from api.realtime import ConnectionManager
from api.store import GameStore, MapTokenConflict


def _chunk(kind: bytes, payload: bytes) -> bytes:
    crc = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + crc.to_bytes(4, "big")
    )


def _map_png() -> bytes:
    width, height = 320, 256
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 2, 0, 0, 0))
    )
    pixels = b"".join(
        b"\x00" + b"\x31\x45\x3b" * width for _ in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(pixels))
        + _chunk(b"IEND", b"")
    )


class MapTokenAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = GameStore(root / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.map_object_store = LocalMapObjectStore(root / "maps")
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        self.dm = self.client.post(
            "/api/games",
            json={"name": "Token Game", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.riva = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Riva",
            },
        ).json()
        self.brann = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Brann",
            },
        ).json()
        uploaded = self.client.post(
            "/api/maps/assets",
            headers={
                **self.auth(self.dm["token"]),
                "Content-Type": "image/png",
                "X-Filename": "arena.png",
            },
            content=_map_png(),
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.asset = uploaded.json()
        revision = self.snapshot(self.dm["token"])["revision"]
        revision = self.command(
            self.dm["token"],
            "update_map_scene",
            {
                "scene_revision": 1,
                "asset_id": self.asset["id"],
                "name": "Arena",
                "grid_type": "square",
                "grid_size_px": 64,
                "distance_per_cell": 5,
                "distance_unit": "ft",
                "viewport": {"x": 0, "y": 0, "zoom": 1},
                "published": True,
            },
            revision,
            "token-scene-publish-001",
        ).json()["revision"]
        for player in (self.riva, self.brann):
            response = self.command(
                self.dm["token"],
                "add_combatant",
                {
                    "id": player["character_id"],
                    "name": "Riva" if player is self.riva else "Brann",
                    "initiative": 12,
                    "hp": 10,
                    "kind": "player",
                },
                revision,
            )
            self.assertEqual(response.status_code, 200, response.text)
            revision = response.json()["revision"]
        monster = self.command(
            self.dm["token"],
            "add_combatant",
            {
                "id": "hidden-goblin",
                "name": "Hidden Goblin",
                "initiative": 8,
                "hp": 7,
                "kind": "monster",
                "hidden": True,
            },
            revision,
        )
        self.assertEqual(monster.status_code, 200, monster.text)
        revision = monster.json()["revision"]
        started = self.command(
            self.dm["token"], "start_encounter", {}, revision
        )
        self.assertEqual(started.status_code, 200, started.text)
        revision = started.json()["revision"]
        synced = self.command(
            self.dm["token"],
            "sync_map_tokens",
            {},
            revision,
            "sync-map-tokens-001",
        )
        self.assertEqual(synced.status_code, 200, synced.text)

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def snapshot(self, token: str) -> dict:
        response = self.client.get("/api/snapshot", headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def command(
        self,
        token: str,
        command_type: str,
        payload: dict,
        revision: int | None = None,
        action_id: str | None = None,
    ):
        body = {"type": command_type, "payload": payload}
        if revision is not None:
            body["expected_revision"] = revision
        if action_id is not None:
            body["client_action_id"] = action_id
        return self.client.post(
            "/api/commands", headers=self.auth(token), json=body
        )

    def scene(self, token: str) -> dict:
        response = self.client.get("/api/maps/scene", headers=self.auth(token))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_projection_uses_live_combatants_and_enforces_ownership(self):
        dm_tokens = self.scene(self.dm["token"])["tokens"]
        self.assertEqual(len(dm_tokens), 3)
        hidden = next(
            token for token in dm_tokens
            if token["combatant_id"] == "hidden-goblin"
        )
        self.assertEqual(hidden["hp"], 7)
        self.assertTrue(all(token["can_move"] for token in dm_tokens))

        player_tokens = self.scene(self.riva["token"])["tokens"]
        self.assertEqual(len(player_tokens), 2)
        self.assertNotIn(
            "hidden-goblin",
            {token["combatant_id"] for token in player_tokens},
        )
        own = next(
            token for token in player_tokens
            if token["combatant_id"] == self.riva["character_id"]
        )
        other = next(
            token for token in player_tokens
            if token["combatant_id"] == self.brann["character_id"]
        )
        self.assertTrue(own["can_move"])
        self.assertFalse(other["can_move"])
        self.assertEqual(own["owner_member_id"], self.riva["member_id"])
        self.assertIsNone(other["owner_member_id"])

    def test_owner_move_is_idempotent_and_stale_token_revision_is_409(self):
        own = next(
            token for token in self.scene(self.riva["token"])["tokens"]
            if token["combatant_id"] == self.riva["character_id"]
        )
        revision = self.snapshot(self.riva["token"])["revision"]
        payload = {
            "token_id": own["id"],
            "token_revision": own["revision"],
            "x": 96,
            "y": 96,
        }
        first = self.command(
            self.riva["token"],
            "move_map_token",
            payload,
            revision,
            "player-token-move-001",
        )
        self.assertEqual(first.status_code, 200, first.text)
        replay = self.command(
            self.riva["token"],
            "move_map_token",
            payload,
            revision,
            "player-token-move-001",
        )
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["replayed"])
        moved = next(
            token for token in self.scene(self.riva["token"])["tokens"]
            if token["id"] == own["id"]
        )
        self.assertEqual((moved["x"], moved["y"], moved["revision"]), (96, 96, 2))

        stale = self.command(
            self.riva["token"],
            "move_map_token",
            {**payload, "x": 160},
            None,
            "player-token-stale-002",
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["actual_revision"], 2)

    def test_player_cannot_move_another_token_or_sync(self):
        other = next(
            token for token in self.scene(self.riva["token"])["tokens"]
            if token["combatant_id"] == self.brann["character_id"]
        )
        revision = self.snapshot(self.riva["token"])["revision"]
        blocked = self.command(
            self.riva["token"],
            "move_map_token",
            {
                "token_id": other["id"],
                "token_revision": other["revision"],
                "x": 160,
                "y": 96,
            },
            revision,
        )
        self.assertEqual(blocked.status_code, 400, blocked.text)
        synced = self.command(
            self.riva["token"], "sync_map_tokens", {}, revision
        )
        self.assertEqual(synced.status_code, 400, synced.text)

    def test_concurrent_moves_with_same_token_revision_have_one_winner(self):
        own = next(
            token for token in self.scene(self.riva["token"])["tokens"]
            if token["combatant_id"] == self.riva["character_id"]
        )
        auth = self.store.authenticate(self.riva["token"])
        barrier = threading.Barrier(2)

        def attempt(index: int) -> str:
            barrier.wait()
            try:
                api_app.game_engine.apply(
                    auth,
                    CommandRequest(
                        type="move_map_token",
                        payload={
                            "token_id": own["id"],
                            "token_revision": own["revision"],
                            "x": 96 + index * 64,
                            "y": 96,
                        },
                        client_action_id=f"concurrent-token-move-{index}",
                    ),
                )
                return "moved"
            except MapTokenConflict:
                return "conflict"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, (0, 1)))
        self.assertEqual(sorted(outcomes), ["conflict", "moved"])

    def test_snapshot_does_not_mix_old_game_and_new_token_revisions(self):
        auth = self.store.authenticate(self.riva["token"])
        before_scene = self.scene(self.riva["token"])
        own = next(
            token for token in before_scene["tokens"]
            if token["combatant_id"] == self.riva["character_id"]
        )
        before_revision = self.snapshot(self.riva["token"])["revision"]
        reader_at_scene = threading.Event()
        writer_updated_token = threading.Event()
        release_reader = threading.Event()
        call_lock = threading.Lock()
        calls = 0
        original_map_scene = self.store.map_scene

        def delayed_map_scene(context, game=None):
            nonlocal calls
            with call_lock:
                calls += 1
                call_number = calls
            if call_number == 1:
                reader_at_scene.set()
                self.assertTrue(release_reader.wait(timeout=5))
            else:
                writer_updated_token.set()
            return original_map_scene(context, game)

        self.store.map_scene = delayed_map_scene
        try:
            with ThreadPoolExecutor(max_workers=2) as executor:
                snapshot_future = executor.submit(api_app.snapshot, auth)
                self.assertTrue(reader_at_scene.wait(timeout=5))
                move_future = executor.submit(
                    api_app.game_engine.apply,
                    auth,
                    CommandRequest(
                        type="move_map_token",
                        payload={
                            "token_id": own["id"],
                            "token_revision": own["revision"],
                            "x": 96,
                            "y": 96,
                        },
                        client_action_id="snapshot-token-consistency-001",
                    ),
                )
                self.assertTrue(writer_updated_token.wait(timeout=5))
                self.assertFalse(move_future.done())
                release_reader.set()
                captured = snapshot_future.result(timeout=5)
                move_future.result(timeout=5)
        finally:
            release_reader.set()
            self.store.map_scene = original_map_scene

        captured_token = next(
            token for token in captured["map_scene"]["tokens"]
            if token["id"] == own["id"]
        )
        self.assertEqual(captured["revision"], before_revision)
        self.assertEqual(captured_token["revision"], own["revision"])

    def test_remove_requires_current_token_revision(self):
        token = self.scene(self.dm["token"])["tokens"][0]
        game_revision = self.snapshot(self.dm["token"])["revision"]
        moved = self.command(
            self.dm["token"],
            "move_map_token",
            {
                "token_id": token["id"],
                "token_revision": token["revision"],
                "x": 96,
                "y": 96,
            },
            game_revision,
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        stale_remove = self.command(
            self.dm["token"],
            "remove_map_token",
            {
                "token_id": token["id"],
                "token_revision": token["revision"],
            },
        )
        self.assertEqual(stale_remove.status_code, 409, stale_remove.text)
        current_revision = moved.json()["event"]["payload"]["token_revision"]
        removed = self.command(
            self.dm["token"],
            "remove_map_token",
            {
                "token_id": token["id"],
                "token_revision": current_revision,
            },
            moved.json()["revision"],
        )
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertNotIn(
            token["id"],
            {item["id"] for item in self.scene(self.dm["token"])["tokens"]},
        )

    def test_sync_reconciles_size_and_bounds_after_grid_change(self):
        before = self.scene(self.dm["token"])
        old_tokens = {item["id"]: item for item in before["tokens"]}
        game_revision = self.snapshot(self.dm["token"])["revision"]
        changed = self.command(
            self.dm["token"],
            "update_map_scene",
            {
                "scene_revision": before["revision"],
                "asset_id": before["asset_id"],
                "name": before["name"],
                "grid_type": "square",
                "grid_size_px": 256,
                "distance_per_cell": before["distance_per_cell"],
                "distance_unit": before["distance_unit"],
                "viewport": before["viewport"],
                "published": True,
            },
            game_revision,
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        synced = self.command(
            self.dm["token"],
            "sync_map_tokens",
            {},
            changed.json()["revision"],
        )
        self.assertEqual(synced.status_code, 200, synced.text)
        after = self.scene(self.dm["token"])["tokens"]
        for token in after:
            self.assertEqual(token["size_px"], 160)
            self.assertGreaterEqual(token["x"], 80)
            self.assertLessEqual(token["x"], 240)
            self.assertGreaterEqual(token["y"], 80)
            self.assertLessEqual(token["y"], 176)
            self.assertGreater(token["revision"], old_tokens[token["id"]]["revision"])

    def test_hidden_token_movement_event_is_dm_only(self):
        hidden = next(
            token for token in self.scene(self.dm["token"])["tokens"]
            if token["combatant_id"] == "hidden-goblin"
        )
        revision = self.snapshot(self.dm["token"])["revision"]
        moved = self.command(
            self.dm["token"],
            "move_map_token",
            {
                "token_id": hidden["id"],
                "token_revision": hidden["revision"],
                "x": 224,
                "y": 160,
            },
            revision,
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["event"]["visibility"], "dm_only")
        events = self.client.get(
            "/api/events",
            headers=self.auth(self.riva["token"]),
            params={"after": 0, "limit": 500},
        ).json()["events"]
        self.assertFalse(
            any(
                event["type"] == "map_token_moved"
                and event["payload"].get("combatant_id") == "hidden-goblin"
                for event in events
            )
        )

    def test_unpublish_redacts_all_tokens_and_content(self):
        dm_scene = self.scene(self.dm["token"])
        revision = self.snapshot(self.dm["token"])["revision"]
        unpublished = self.command(
            self.dm["token"],
            "update_map_scene",
            {
                "scene_revision": dm_scene["revision"],
                "asset_id": dm_scene["asset_id"],
                "name": dm_scene["name"],
                "grid_type": dm_scene["grid_type"],
                "grid_size_px": dm_scene["grid_size_px"],
                "distance_per_cell": dm_scene["distance_per_cell"],
                "distance_unit": dm_scene["distance_unit"],
                "viewport": dm_scene["viewport"],
                "published": False,
            },
            revision,
        )
        self.assertEqual(unpublished.status_code, 200, unpublished.text)
        player_scene = self.scene(self.riva["token"])
        self.assertEqual(player_scene["tokens"], [])
        self.assertIsNone(player_scene["asset"])
        self.assertEqual(
            self.client.get(
                self.asset["url"], headers=self.auth(self.riva["token"])
            ).status_code,
            404,
        )
        self.assertNotIn("tokens", unpublished.json()["event"]["payload"])
        visible_token = next(
            item for item in dm_scene["tokens"]
            if item["combatant_id"] == self.riva["character_id"]
        )
        moved = self.command(
            self.dm["token"],
            "move_map_token",
            {
                "token_id": visible_token["id"],
                "token_revision": visible_token["revision"],
                "x": 96,
                "y": 96,
            },
            unpublished.json()["revision"],
        )
        self.assertEqual(moved.status_code, 200, moved.text)
        self.assertEqual(moved.json()["event"]["visibility"], "dm_only")
        player_events = self.client.get(
            "/api/events",
            headers=self.auth(self.riva["token"]),
            params={"after": 0, "limit": 500},
        ).json()["events"]
        self.assertFalse(
            any(
                event["type"] == "map_token_moved"
                and event["payload"].get("token_id") == visible_token["id"]
                for event in player_events
            )
        )


if __name__ == "__main__":
    unittest.main()
