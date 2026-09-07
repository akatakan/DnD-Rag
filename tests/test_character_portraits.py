"""Character portraits: upload, authorisation and isolation from the map library.

Portraits deliberately live beside the rules aggregate rather than inside it,
so none of this may touch the character schema. They do share the campaign
image store, which is where the quota, validation and de-duplication come from,
so the map library must keep listing maps only.
"""

import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from fastapi.testclient import TestClient

import api.app as api_app
from api.ai_dm import AIDMOrchestrator
from api.game_engine import GameEngine
from api.map_assets import LocalMapObjectStore
from api.realtime import ConnectionManager
from api.store import GameStore


def png_bytes(width: int = 128, height: int = 128, tint: int = 0) -> bytes:
    """A real PNG, since the validator checks CRCs and pixels, not extensions."""
    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    raw = b"".join(
        b"\x00" + bytes([(x + tint) % 256, (y + tint) % 256, 128] * 1)
        * 1
        + bytes([(x + tint) % 256, (y + tint) % 256, 128]) * (width - 1)
        for y in range(height)
        for x in [0]
    )
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


class CharacterPortraitTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = GameStore(root / "game.db")
        api_app.store = self.store
        api_app.game_engine = GameEngine(self.store)
        api_app.ai_dm = AIDMOrchestrator(self.store)
        api_app.connections = ConnectionManager(self.store)
        api_app.map_object_store = LocalMapObjectStore(root / "objects")
        api_app.rate_limiter.clear()
        self.client = TestClient(api_app.app)
        created = self.client.post(
            "/api/games",
            json={"name": "Portraits", "dm_name": "DM", "dm_mode": "human"},
        ).json()
        self.dm = created
        self.player = self.client.post(
            "/api/games/join",
            json={"invite_code": created["invite_code"], "player_name": "Riva"},
        ).json()
        self.other = self.client.post(
            "/api/games/join",
            json={"invite_code": created["invite_code"], "player_name": "Kel"},
        ).json()

    def tearDown(self):
        self.client.close()
        self.temp.cleanup()

    def auth(self, token):
        return {"Authorization": f"Bearer {token}"}

    def upload(self, token, character_id, data=None, content_type="image/png"):
        return self.client.post(
            f"/api/characters/{character_id}/portrait",
            headers={
                **self.auth(token),
                "Content-Type": content_type,
                "X-Filename": "riva.png",
            },
            content=data if data is not None else png_bytes(),
        )

    def test_owner_uploads_and_everyone_at_the_table_can_see_it(self):
        character_id = self.player["character_id"]
        created = self.upload(self.player["token"], character_id)
        self.assertEqual(created.status_code, 201, created.text)
        self.assertEqual(created.json()["character_id"], character_id)

        content = self.client.get(
            f"/api/characters/{character_id}/portrait",
            headers=self.auth(self.other["token"]),
        )
        self.assertEqual(content.status_code, 200, content.text)
        self.assertEqual(content.headers["content-type"], "image/png")
        self.assertEqual(content.headers["x-content-type-options"], "nosniff")

        snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.dm["token"])
        ).json()
        self.assertIn(character_id, snapshot["portraits"])

    def test_another_player_cannot_replace_someone_elses_portrait(self):
        refused = self.upload(self.other["token"], self.player["character_id"])
        self.assertEqual(refused.status_code, 403, refused.text)

    def test_the_dm_may_replace_a_player_portrait(self):
        allowed = self.upload(self.dm["token"], self.player["character_id"])
        self.assertEqual(allowed.status_code, 201, allowed.text)

    def test_an_unknown_character_is_not_found(self):
        missing = self.upload(self.player["token"], "character-does-not-exist")
        self.assertEqual(missing.status_code, 404, missing.text)

    def test_a_non_image_payload_is_refused(self):
        refused = self.upload(
            self.player["token"],
            self.player["character_id"],
            data=b"this is not a png",
        )
        self.assertEqual(refused.status_code, 400, refused.text)

    def test_replacing_a_portrait_keeps_one_row_per_character(self):
        character_id = self.player["character_id"]
        self.assertEqual(self.upload(self.player["token"], character_id).status_code, 201)
        second = self.upload(
            self.player["token"], character_id, data=png_bytes(tint=40)
        )
        self.assertEqual(second.status_code, 201, second.text)
        snapshot = self.client.get(
            "/api/snapshot", headers=self.auth(self.player["token"])
        ).json()
        self.assertEqual(len(snapshot["portraits"]), 1)
        self.assertEqual(
            snapshot["portraits"][character_id]["asset_id"],
            second.json()["asset_id"],
        )

    def test_portraits_do_not_appear_in_the_map_library(self):
        self.upload(self.player["token"], self.player["character_id"])
        listed = self.client.get(
            "/api/maps/assets", headers=self.auth(self.dm["token"])
        )
        self.assertEqual(listed.status_code, 200, listed.text)
        self.assertEqual(listed.json()["assets"], [])

    def test_a_character_without_a_portrait_returns_not_found(self):
        missing = self.client.get(
            f"/api/characters/{self.other['character_id']}/portrait",
            headers=self.auth(self.other["token"]),
        )
        self.assertEqual(missing.status_code, 404, missing.text)


if __name__ == "__main__":
    unittest.main()
