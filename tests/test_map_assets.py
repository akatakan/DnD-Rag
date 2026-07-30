import hashlib
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
from api.map_assets import LocalMapObjectStore, MapAssetError, validate_map_image
from api.realtime import ConnectionManager
from api.store import GameStore


def png_chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + checksum.to_bytes(4, "big")
    )


def valid_png(width: int = 64, height: int = 64) -> bytes:
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 2, 0, 0, 0))
    )
    pixels = b"".join(b"\x00" + b"\x33\x55\x77" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + png_chunk(b"IHDR", ihdr)
        + png_chunk(b"IDAT", zlib.compress(pixels))
        + png_chunk(b"IEND", b"")
    )


class MapImageValidationTest(unittest.TestCase):
    def test_png_is_structurally_validated_and_hashed(self):
        data = valid_png(96, 80)
        metadata = validate_map_image(data, "image/png", 1024 * 1024)
        self.assertEqual((metadata["width"], metadata["height"]), (96, 80))
        self.assertEqual(metadata["sha256"], hashlib.sha256(data).hexdigest())

        corrupt = bytearray(data)
        corrupt[29] ^= 1
        with self.assertRaisesRegex(MapAssetError, "CRC"):
            validate_map_image(bytes(corrupt), "image/png", 1024 * 1024)
        with self.assertRaises(MapAssetError):
            validate_map_image(data[:-12], "image/png", 1024 * 1024)
        with self.assertRaisesRegex(MapAssetError, "Content-Type"):
            validate_map_image(data, "image/jpeg", 1024 * 1024)

    def test_limits_are_enforced_before_storage(self):
        data = valid_png()
        with self.assertRaisesRegex(MapAssetError, "boyut limitini"):
            validate_map_image(data, "image/png", len(data) - 1)
        with self.assertRaisesRegex(MapAssetError, "64..8192"):
            validate_map_image(valid_png(63, 64), "image/png", 1024 * 1024)
        fake_jpeg = (
            b"\xff\xd8\xff\xc0\x00\x0b\x08\x00\x40\x00\x40\x03\x01\x11"
            b"\xff\xd9"
        )
        with self.assertRaisesRegex(MapAssetError, "scan"):
            validate_map_image(fake_jpeg, "image/jpeg", 1024 * 1024)

    def test_content_addressed_store_detects_existing_corruption(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = LocalMapObjectStore(root)
            data = valid_png()
            digest = hashlib.sha256(data).hexdigest()
            key = store.put(data, digest, "png")
            self.assertEqual(store.path(key).read_bytes(), data)
            store.path(key).write_bytes(b"x" * len(data))
            with self.assertRaisesRegex(MapAssetError, "bozulmus"):
                store.put(data, digest, "png")
            with self.assertRaises(MapAssetError):
                store.path("../map.png")


class MapAssetAPITest(unittest.TestCase):
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
        self.game = self.client.post(
            "/api/games",
            json={"name": "Map Game", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.player = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.game["invite_code"],
                "player_name": "Riva",
            },
        ).json()
        self.image = valid_png()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    @staticmethod
    def auth(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def upload(self, token: str | None = None):
        headers = {
            **self.auth(token or self.game["token"]),
            "Content-Type": "image/png",
            "X-Filename": "dungeon.png",
        }
        return self.client.post(
            "/api/maps/assets", headers=headers, content=self.image
        )

    def scene_payload(self, asset_id: str, revision: int = 1) -> dict:
        return {
            "scene_revision": revision,
            "asset_id": asset_id,
            "name": "Crypt",
            "grid_type": "square",
            "grid_size_px": 64,
            "distance_per_cell": 5,
            "distance_unit": "ft",
            "viewport": {"x": 12, "y": -8, "zoom": 1.25},
            "published": True,
        }

    def command(
        self, payload: dict, action_id: str, expected_revision: int
    ):
        return self.client.post(
            "/api/commands",
            headers=self.auth(self.game["token"]),
            json={
                "type": "update_map_scene",
                "payload": payload,
                "client_action_id": action_id,
                "expected_revision": expected_revision,
            },
        )

    def test_upload_requires_active_dm_and_rejects_invalid_body(self):
        self.assertEqual(self.upload(self.player["token"]).status_code, 403)
        invalid = self.client.post(
            "/api/maps/assets",
            headers={
                **self.auth(self.game["token"]),
                "Content-Type": "image/png",
                "X-Filename": "broken.png",
            },
            content=b"\x89PNG\r\n\x1a\n",
        )
        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(
            self.client.get(
                "/api/maps/assets", headers=self.auth(self.player["token"])
            ).status_code,
            403,
        )
        preflight = self.client.options(
            "/api/maps/assets",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers":
                    "authorization,content-type,x-filename",
            },
        )
        self.assertEqual(preflight.status_code, 200, preflight.text)
        self.assertIn(
            "x-filename",
            preflight.headers["access-control-allow-headers"].lower(),
        )

    def test_map_body_limit_is_enforced_before_parser(self):
        previous = api_app.MAX_MAP_UPLOAD_BYTES
        api_app.MAX_MAP_UPLOAD_BYTES = len(self.image) - 1
        try:
            response = self.upload()
        finally:
            api_app.MAX_MAP_UPLOAD_BYTES = previous
        self.assertEqual(response.status_code, 413, response.text)

    def test_unpublished_asset_is_redacted_then_visible_after_publish(self):
        uploaded = self.upload()
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        asset = uploaded.json()
        player_headers = self.auth(self.player["token"])
        self.assertEqual(
            self.client.get(asset["url"], headers=player_headers).status_code,
            404,
        )
        hidden = self.client.get("/api/maps/scene", headers=player_headers).json()
        self.assertIsNone(hidden["asset"])
        self.assertFalse(hidden["published"])
        self.assertEqual(hidden["name"], "Battle Map")
        self.assertEqual(hidden["grid_type"], "none")

        snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.game["token"])
        ).json()
        saved = self.command(
            self.scene_payload(asset["id"]),
            "publish-map-scene-001",
            snapshot["revision"],
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        visible = self.client.get("/api/maps/scene", headers=player_headers).json()
        self.assertEqual(visible["asset"]["id"], asset["id"])
        content = self.client.get(asset["url"], headers=player_headers)
        self.assertEqual(content.status_code, 200, content.text)
        self.assertEqual(content.content, self.image)
        self.assertEqual(content.headers["x-content-type-options"], "nosniff")

    def test_scene_revision_and_command_idempotency_prevent_lost_updates(self):
        asset = self.upload().json()
        snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.game["token"])
        ).json()
        payload = self.scene_payload(asset["id"])
        first = self.command(payload, "map-idempotency-action-001", snapshot["revision"])
        self.assertEqual(first.status_code, 200, first.text)
        replay = self.command(payload, "map-idempotency-action-001", snapshot["revision"])
        self.assertEqual(replay.status_code, 200, replay.text)
        self.assertTrue(replay.json()["replayed"])
        current = self.client.get(
            "/api/maps/scene", headers=self.auth(self.game["token"])
        ).json()
        self.assertEqual(current["revision"], 2)

        stale = self.command(
            {**payload, "name": "Stale"},
            "map-stale-action-002",
            first.json()["revision"],
        )
        self.assertEqual(stale.status_code, 409)
        self.assertIn("revision", stale.json()["detail"]["message"].lower())
        self.assertEqual(stale.json()["detail"]["expected_revision"], 1)
        self.assertEqual(stale.json()["detail"]["actual_revision"], 2)

    def test_asset_is_campaign_scoped(self):
        asset = self.upload().json()
        other = self.client.post(
            "/api/games",
            json={"name": "Other", "dm_name": "Other DM", "dm_mode": "human"},
        ).json()
        self.assertEqual(
            self.client.get(
                asset["url"], headers=self.auth(other["token"])
            ).status_code,
            404,
        )

    def test_campaign_quota_is_serialized_across_connections(self):
        auth = self.store.authenticate(self.game["token"])

        def metadata(index: int) -> tuple[str, dict]:
            digest = f"{index:064x}"
            return f"{digest}.png", {
                "sha256": digest,
                "extension": "png",
                "content_type": "image/png",
                "byte_size": 10 * 1024 * 1024,
                "width": 4096,
                "height": 4096,
            }

        for index in range(1, 10):
            storage_key, item = metadata(index)
            with self.store.transaction():
                self.store.create_map_asset(
                    auth, f"seed-{index}.png", storage_key, item
                )

        barrier = threading.Barrier(2)

        def attempt(index: int) -> bool:
            barrier.wait()
            storage_key, item = metadata(index)
            try:
                with self.store.transaction():
                    self.store.create_map_asset(
                        auth, f"race-{index}.png", storage_key, item
                    )
                return True
            except ValueError:
                return False

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(attempt, (10, 11)))
        self.assertEqual(sorted(outcomes), [False, True])
        with self.store.connect() as db:
            total = db.execute(
                "SELECT SUM(byte_size) FROM map_assets"
            ).fetchone()[0]
        self.assertEqual(total, 100 * 1024 * 1024)

    def test_quota_rejection_does_not_leave_orphan_object(self):
        auth = self.store.authenticate(self.game["token"])
        for index in range(10):
            digest = f"{index + 1:064x}"
            metadata = {
                "sha256": digest,
                "extension": "png",
                "content_type": "image/png",
                "byte_size": 10 * 1024 * 1024,
                "width": 4096,
                "height": 4096,
            }
            with self.store.transaction():
                self.store.create_map_asset(
                    auth, f"seed-{index}.png", f"{digest}.png", metadata
                )

        response = self.upload()
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(list(api_app.map_object_store.root.glob("*")), [])


if __name__ == "__main__":
    unittest.main()
