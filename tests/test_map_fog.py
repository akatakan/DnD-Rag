import struct
import tempfile
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from io import BytesIO
from pathlib import Path

from fastapi.testclient import TestClient
from PIL import Image

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.map_assets import LocalMapObjectStore
from api.map_fog import render_fog_mask, render_fogged_map
from api.realtime import ConnectionManager
from api.store import GameStore


def _chunk(kind: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(kind + payload) & 0xFFFFFFFF
    return (
        len(payload).to_bytes(4, "big")
        + kind
        + payload
        + checksum.to_bytes(4, "big")
    )


def _map_png() -> bytes:
    width, height = 320, 256
    ihdr = (
        width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + bytes((8, 2, 0, 0, 0))
    )
    pixels = b"".join(
        b"\x00" + b"\x22\x35\x2d" * width for _ in range(height)
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", ihdr)
        + _chunk(b"IDAT", zlib.compress(pixels))
        + _chunk(b"IEND", b"")
    )


class FogMaskTest(unittest.TestCase):
    def test_mask_is_bounded_png_with_only_revealed_alpha(self):
        data = render_fog_mask(5, 4, {(1, 2)})
        self.assertTrue(data.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(struct.unpack(">II", data[16:24]), (5, 4))
        offset = 8
        compressed = bytearray()
        while offset < len(data):
            length = int.from_bytes(data[offset:offset + 4], "big")
            kind = data[offset + 4:offset + 8]
            payload = data[offset + 8:offset + 8 + length]
            if kind == b"IDAT":
                compressed.extend(payload)
            offset += 12 + length
        raw = zlib.decompress(bytes(compressed))
        row_size = 1 + 5 * 2
        self.assertEqual(raw[2 * row_size + 1 + 1 * 2 + 1], 0)
        self.assertEqual(raw[2], 255)
        with self.assertRaises(ValueError):
            render_fog_mask(0, 4, set())

    def test_concurrent_projection_of_same_revision_is_atomic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.png"
            target = root / "cache" / "projected.png"
            source.write_bytes(_map_png())
            with ThreadPoolExecutor(max_workers=8) as workers:
                list(
                    workers.map(
                        lambda _: render_fogged_map(
                            source, target, 64, {(1, 1)}
                        ),
                        range(16),
                    )
                )
            with Image.open(target) as image:
                self.assertEqual(image.size, (320, 256))
                self.assertEqual(image.getpixel((80, 80)), (34, 53, 45))
                self.assertEqual(image.getpixel((10, 10)), (0, 0, 0))
            self.assertEqual(
                list(target.parent.glob(".fog-*.tmp")),
                [],
            )


class MapFogAPITest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = GameStore(root / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.map_object_store = LocalMapObjectStore(root / "maps")
        api_app.map_fog_cache_root = root / "fog-cache"
        api_app.map_fog_cache_root.mkdir()
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        self.dm = self.client.post(
            "/api/games",
            json={"name": "Fog Game", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.player = self.client.post(
            "/api/games/join",
            json={
                "invite_code": self.dm["invite_code"],
                "player_name": "Riva",
            },
        ).json()
        uploaded = self.client.post(
            "/api/maps/assets",
            headers={
                **self.auth(self.dm["token"]),
                "Content-Type": "image/png",
                "X-Filename": "fog-map.png",
            },
            content=_map_png(),
        )
        self.assertEqual(uploaded.status_code, 201, uploaded.text)
        self.asset = uploaded.json()
        snapshot = self.snapshot(self.dm["token"])
        published = self.command(
            self.dm["token"],
            "update_map_scene",
            {
                "scene_revision": 1,
                "asset_id": self.asset["id"],
                "name": "Fog Map",
                "grid_type": "square",
                "grid_size_px": 64,
                "distance_per_cell": 5,
                "distance_unit": "ft",
                "viewport": {"x": 0, "y": 0, "zoom": 1},
                "published": True,
            },
            snapshot["revision"],
        )
        self.assertEqual(published.status_code, 200, published.text)

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
        revision: int | None,
        action_id: str | None = None,
    ):
        body = {"type": command_type, "payload": payload}
        if revision is not None:
            body["expected_revision"] = revision
        if action_id:
            body["client_action_id"] = action_id
        return self.client.post(
            "/api/commands", headers=self.auth(token), json=body
        )

    def test_fog_cells_are_dm_only_and_player_gets_raster_mask(self):
        dm_snapshot = self.snapshot(self.dm["token"])
        fog = dm_snapshot["map_scene"]["fog"]
        self.assertFalse(fog["enabled"])
        enabled = self.command(
            self.dm["token"],
            "set_map_fog",
            {"fog_revision": fog["revision"], "enabled": True},
            dm_snapshot["revision"],
        )
        self.assertEqual(enabled.status_code, 200, enabled.text)
        painted = self.command(
            self.dm["token"],
            "paint_map_fog",
            {
                "fog_revision": fog["revision"] + 1,
                "mode": "reveal",
                "cells": [[1, 2], [2, 2]],
            },
            enabled.json()["revision"],
            "paint-fog-cells-001",
        )
        self.assertEqual(painted.status_code, 200, painted.text)
        self.assertNotIn("cells", painted.json()["event"]["payload"])
        self.assertEqual(painted.json()["event"]["visibility"], "dm_only")

        dm_scene = self.snapshot(self.dm["token"])["map_scene"]
        self.assertEqual(dm_scene["fog"]["revealed_cells"], [[1, 2], [2, 2]])
        player_scene = self.snapshot(self.player["token"])["map_scene"]
        self.assertIsNone(player_scene["fog"]["revealed_cells"])
        self.assertIn("/api/maps/fog-mask", player_scene["fog"]["mask_url"])
        mask = self.client.get(
            player_scene["fog"]["mask_url"],
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(mask.status_code, 200, mask.text)
        self.assertEqual(mask.headers["content-type"], "image/png")
        self.assertEqual(struct.unpack(">II", mask.content[16:24]), (5, 4))
        protected_map = self.client.get(
            player_scene["asset"]["url"],
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(protected_map.status_code, 200, protected_map.text)
        self.assertEqual(protected_map.headers["content-type"], "image/png")
        self.assertNotEqual(protected_map.content, _map_png())
        with Image.open(BytesIO(protected_map.content)) as projected:
            self.assertEqual(projected.getpixel((96, 160)), (34, 53, 45))
            self.assertEqual(projected.getpixel((10, 10)), (0, 0, 0))
        raw_url_attempt = self.client.get(
            self.asset["url"],
            headers=self.auth(self.player["token"]),
        )
        self.assertEqual(raw_url_attempt.status_code, 200)
        self.assertEqual(raw_url_attempt.content, protected_map.content)

    def test_cache_is_game_scoped_for_same_asset_and_fog_revision(self):
        first_snapshot = self.snapshot(self.dm["token"])
        enabled = self.command(
            self.dm["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": True},
            first_snapshot["revision"],
        )
        first_painted = self.command(
            self.dm["token"],
            "paint_map_fog",
            {
                "fog_revision": 2,
                "mode": "reveal",
                "cells": [[0, 0]],
            },
            enabled.json()["revision"],
        )
        self.assertEqual(first_painted.status_code, 200, first_painted.text)

        second_dm = self.client.post(
            "/api/games",
            json={"name": "Other Fog", "dm_name": "Other DM", "dm_mode": "human"},
        ).json()
        second_player = self.client.post(
            "/api/games/join",
            json={
                "invite_code": second_dm["invite_code"],
                "player_name": "Brann",
            },
        ).json()
        second_upload = self.client.post(
            "/api/maps/assets",
            headers={
                **self.auth(second_dm["token"]),
                "Content-Type": "image/png",
                "X-Filename": "same-map.png",
            },
            content=_map_png(),
        )
        self.assertEqual(second_upload.status_code, 201, second_upload.text)
        second_asset = second_upload.json()
        self.assertEqual(second_asset["id"] != self.asset["id"], True)
        second_snapshot = self.snapshot(second_dm["token"])
        second_published = self.command(
            second_dm["token"],
            "update_map_scene",
            {
                "scene_revision": 1,
                "asset_id": second_asset["id"],
                "name": "Other Fog Map",
                "grid_type": "square",
                "grid_size_px": 64,
                "distance_per_cell": 5,
                "distance_unit": "ft",
                "viewport": {"x": 0, "y": 0, "zoom": 1},
                "published": True,
            },
            second_snapshot["revision"],
        )
        second_enabled = self.command(
            second_dm["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": True},
            second_published.json()["revision"],
        )
        second_painted = self.command(
            second_dm["token"],
            "paint_map_fog",
            {
                "fog_revision": 2,
                "mode": "reveal",
                "cells": [[1, 1]],
            },
            second_enabled.json()["revision"],
        )
        self.assertEqual(second_painted.status_code, 200, second_painted.text)

        first_scene = self.snapshot(self.player["token"])["map_scene"]
        second_scene = self.snapshot(second_player["token"])["map_scene"]
        first_map = self.client.get(
            first_scene["asset"]["url"],
            headers=self.auth(self.player["token"]),
        )
        second_map = self.client.get(
            second_scene["asset"]["url"],
            headers=self.auth(second_player["token"]),
        )
        self.assertEqual(first_map.status_code, 200)
        self.assertEqual(second_map.status_code, 200)
        self.assertNotEqual(first_map.content, second_map.content)
        self.assertEqual(
            len(list(api_app.map_fog_cache_root.glob("*.png"))),
            2,
        )

    def test_scene_grid_revision_invalidates_fog_projection_url_and_cache(self):
        snapshot = self.snapshot(self.dm["token"])
        enabled = self.command(
            self.dm["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": True},
            snapshot["revision"],
        )
        painted = self.command(
            self.dm["token"],
            "paint_map_fog",
            {
                "fog_revision": 2,
                "mode": "reveal",
                "cells": [[1, 1]],
            },
            enabled.json()["revision"],
        )
        before_scene = self.snapshot(self.player["token"])["map_scene"]
        before_map = self.client.get(
            before_scene["asset"]["url"],
            headers=self.auth(self.player["token"]),
        )
        dm_scene = self.snapshot(self.dm["token"])["map_scene"]
        changed = self.command(
            self.dm["token"],
            "update_map_scene",
            {
                "scene_revision": dm_scene["revision"],
                "asset_id": dm_scene["asset_id"],
                "name": dm_scene["name"],
                "grid_type": dm_scene["grid_type"],
                "grid_size_px": 32,
                "distance_per_cell": dm_scene["distance_per_cell"],
                "distance_unit": dm_scene["distance_unit"],
                "viewport": dm_scene["viewport"],
                "published": True,
            },
            painted.json()["revision"],
        )
        self.assertEqual(changed.status_code, 200, changed.text)
        after_scene = self.snapshot(self.player["token"])["map_scene"]
        self.assertNotEqual(
            before_scene["asset"]["url"], after_scene["asset"]["url"]
        )
        self.assertNotEqual(
            before_scene["fog"]["mask_url"], after_scene["fog"]["mask_url"]
        )
        after_map = self.client.get(
            after_scene["asset"]["url"],
            headers=self.auth(self.player["token"]),
        )
        self.assertNotEqual(before_map.content, after_map.content)

    def test_player_cannot_change_fog_and_stale_revision_is_409(self):
        snapshot = self.snapshot(self.player["token"])
        blocked = self.command(
            self.player["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": True},
            snapshot["revision"],
        )
        self.assertEqual(blocked.status_code, 400, blocked.text)
        dm_snapshot = self.snapshot(self.dm["token"])
        first = self.command(
            self.dm["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": True},
            dm_snapshot["revision"],
        )
        self.assertEqual(first.status_code, 200, first.text)
        stale = self.command(
            self.dm["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": False},
            None,
        )
        self.assertEqual(stale.status_code, 409, stale.text)
        self.assertEqual(stale.json()["detail"]["actual_revision"], 2)

    def test_unpublished_scene_denies_player_mask(self):
        dm_snapshot = self.snapshot(self.dm["token"])
        enabled = self.command(
            self.dm["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": True},
            dm_snapshot["revision"],
        )
        scene = self.snapshot(self.dm["token"])["map_scene"]
        unpublished = self.command(
            self.dm["token"],
            "update_map_scene",
            {
                "scene_revision": scene["revision"],
                "asset_id": scene["asset_id"],
                "name": scene["name"],
                "grid_type": scene["grid_type"],
                "grid_size_px": scene["grid_size_px"],
                "distance_per_cell": scene["distance_per_cell"],
                "distance_unit": scene["distance_unit"],
                "viewport": scene["viewport"],
                "published": False,
            },
            enabled.json()["revision"],
        )
        self.assertEqual(unpublished.status_code, 200, unpublished.text)
        player_scene = self.snapshot(self.player["token"])["map_scene"]
        self.assertFalse(player_scene["fog"]["enabled"])
        self.assertIsNone(player_scene["fog"]["mask_url"])
        self.assertEqual(
            self.client.get(
                "/api/maps/fog-mask",
                headers=self.auth(self.player["token"]),
            ).status_code,
            404,
        )

    def test_ping_and_draw_are_temporary_and_event_payload_has_no_geometry(self):
        player_snapshot = self.snapshot(self.player["token"])
        ping = self.command(
            self.player["token"],
            "map_ping",
            {"x": 96, "y": 96},
            player_snapshot["revision"],
            "map-ping-player-001",
        )
        self.assertEqual(ping.status_code, 200, ping.text)
        self.assertNotIn("x", ping.json()["event"]["payload"])
        signals = self.snapshot(self.dm["token"])["map_scene"]["signals"]
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["payload"], {"x": 96, "y": 96})

        blocked_draw = self.command(
            self.player["token"],
            "map_draw",
            {"points": [[10, 10], [20, 20]]},
            ping.json()["revision"],
        )
        self.assertEqual(blocked_draw.status_code, 400, blocked_draw.text)
        dm_snapshot = self.snapshot(self.dm["token"])
        drawn = self.command(
            self.dm["token"],
            "map_draw",
            {"points": [[20, 20], [40, 40], [60, 30]]},
            dm_snapshot["revision"],
            "map-draw-dm-001",
        )
        self.assertEqual(drawn.status_code, 200, drawn.text)
        self.assertNotIn("points", drawn.json()["event"]["payload"])
        self.assertEqual(
            len(self.snapshot(self.player["token"])["map_scene"]["signals"]),
            2,
        )

        with self.store.connect() as db:
            db.execute(
                """
                UPDATE map_transients
                SET expires_at = '2000-01-01T00:00:00+00:00'
                WHERE game_id = ?
                """,
                (self.dm["game_id"],),
            )
        self.assertEqual(
            self.snapshot(self.player["token"])["map_scene"]["signals"], []
        )

    def test_fog_filters_hidden_transient_geometry_from_player_projection(self):
        snapshot = self.snapshot(self.dm["token"])
        enabled = self.command(
            self.dm["token"],
            "set_map_fog",
            {"fog_revision": 1, "enabled": True},
            snapshot["revision"],
        )
        painted = self.command(
            self.dm["token"],
            "paint_map_fog",
            {
                "fog_revision": 2,
                "mode": "reveal",
                "cells": [[1, 1]],
            },
            enabled.json()["revision"],
        )
        hidden_draw = self.command(
            self.dm["token"],
            "map_draw",
            {"points": [[10, 10], [20, 20]]},
            painted.json()["revision"],
        )
        self.assertEqual(hidden_draw.status_code, 200, hidden_draw.text)
        self.assertEqual(hidden_draw.json()["event"]["visibility"], "dm_only")
        shown_draw = self.command(
            self.dm["token"],
            "map_draw",
            {"points": [[80, 80], [100, 100]]},
            hidden_draw.json()["revision"],
        )
        self.assertEqual(shown_draw.status_code, 200, shown_draw.text)
        player_signals = self.snapshot(self.player["token"])["map_scene"]["signals"]
        self.assertEqual(len(player_signals), 1)
        self.assertEqual(
            player_signals[0]["payload"]["points"],
            [[80, 80], [100, 100]],
        )
        dm_signals = self.snapshot(self.dm["token"])["map_scene"]["signals"]
        self.assertEqual(len(dm_signals), 2)

    def test_persisted_transient_payload_is_revalidated_fail_closed(self):
        snapshot = self.snapshot(self.dm["token"])
        drawn = self.command(
            self.dm["token"],
            "map_draw",
            {"points": [[80, 80], [100, 100]]},
            snapshot["revision"],
        )
        self.assertEqual(drawn.status_code, 200, drawn.text)
        with self.store.connect() as db:
            db.execute(
                """
                UPDATE map_transients
                SET payload_json =
                    '{"points":[[80,80],[100,100]],"secret":"leak"}'
                WHERE game_id = ?
                """,
                (self.dm["game_id"],),
            )
        auth = self.store.authenticate(self.dm["token"])
        self.assertIsNotNone(auth)
        with self.assertRaisesRegex(RuntimeError, "payload gecersiz"):
            self.store.map_scene(auth)


if __name__ == "__main__":
    unittest.main()
